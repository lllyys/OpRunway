#!/usr/bin/env python3
"""等一件真机上的事做完：产物落盘、进程退出、或两者都要。

**跑测侧唯一的等待方式。** 不要用 `sleep N`、`for i in $(seq ...)` 这类循环等——
两种错法都实测过，代价都很大：

- 一轮一轮短查询（每 3~5 秒一次 `pgrep`）：一次真机验收里查了 91 次，
  每次都要模型完整生成一轮，烧的是上下文预算，跑到一半就触发压缩。
- 前台 `sleep 290`：占死用户终端，而且醒来时事情可能早就完了。实测一次验收
  里等待循环占了 Bash 总时长的 87.5%，3 小时。

这个脚本单次调用内部阻塞，带超时上限，退出时把等到的状态和进度一次打印清楚。

**产物与进程两个条件都要给。** 只给 `--file` 时，跑测进程崩掉后产物永远不出现，
本脚本会一路等满 `--timeout` 才返回——一轮实测在第 3 秒就崩的 `NameError`，
只等产物的话要到上限才发现。两个都给时，「进程没了而产物不在」当场判成崩了，
退 3 并让调用方去看日志，不再空等。只给 `--file` 时脚本会警告。

**`--timeout` 是心跳间隔，不是「这轮最多跑多久」。** 内部轮询是 5s 起步、每轮
乘 1.5、封顶 60s，与 `--timeout` 无关；而调起本脚本的工具**不向用户流式输出**，
所以中途打的进度行要等本脚本退出才到得了终端。于是从用户那边看，`--timeout`
就是「多久能看到一次消息」。默认 120s，**不要为了少调几次设成 3600**——那等于
一小时不吭声，与卡死无法区分。

一轮真的要跑一小时也不必加大 `--timeout`：退 1 时原样再调一次就是继续等，
脚本会在退出信息里说清楚该不该再调。

用法：

    # 等一轮跑测（最常用）：产物与进程两个条件都给
    python3 wait_for.py --file stage/accuracy.json \\
        --pattern "run_atk.py --mode accuracy" \\
        --progress-from evidence/accuracy.log

    # 只等进程退出（那一步不落固定产物时）
    python3 wait_for.py --pattern "build_install.py" \\
        --progress-from evidence/build.log

退出码：

    0  等到了
    1  这一段没等到。**不等于失败**，多数时候就是还在跑——按脚本打的「下一步」办
    2  参数不对：一个等待条件都没给
    3  崩了：进程已退出而产物没落盘。**再等没有意义**，去看日志尾部
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# 轮询间隔从这里起步，每轮乘 1.5 直到上限。**开头密、后面疏**：刚起跑时几秒内
# 就崩的情况要早点发现，跑了十分钟还在跑的就不必每 5 秒看一次。
INTERVAL_START = 5.0
INTERVAL_MAX = 60.0
INTERVAL_GROWTH = 1.5

# 一段等待的上限，也就是用户两次看到消息之间的最长间隔。见模块开头。
#
# 定 120 而不是更小：每回吐一次就是模型完整生成一轮，约 200~300 token，
# 一条链路两小时的等待按 120s 折合约 60 次。更小的间隔省下的是等待时间
# （47 分钟的轮次上不到 1%），花掉的是上下文预算——本脚本存在的理由正是
# 上一版每 3~5 秒查一次把预算烧到触发压缩。
HEARTBEAT_SECONDS = 120

# 抓进度的默认正则。**跑测脚本按这个形状打进度行**（`run_atk.py` 隔离复验的
# `已完成 N/M`），两边改一处就对不上，`tests/test_layout_split.py` 钉住这条耦合。
PROGRESS_RE_DEFAULT = r"(\d+)/(\d+)"

# 每轮只读日志尾部这么多字节。跑测日志是会长到几 MB 的（实测一轮 1000 条的
# accuracy.log 5.6 MB），每轮全文读一遍再全文正则，与要拿的「最后一处进度」
# 不成比例。取尾部即可，进度行按定义在末尾。
PROGRESS_TAIL_BYTES = 64 * 1024


def _own_chain():
    """本进程及其**全部祖先**的 pid。

    `--pattern` 的值就在本进程的 argv 里，也在拉起本进程的每一层 shell 的
    命令行里（`bash -c "... --pattern 'run_atk.py --mode X' ..."`、ssh 包装那层
    同样如此）。`pgrep -f` 匹配整条命令行，于是这些祖先全部命中——**只排掉
    自己的 pid 不够**，判据会恒真：进程早已退出却一直报「还在跑」，崩溃判不出来，
    每次都空等满一整段。真机实测过，ssh 那层 shell 就是这么把判据顶住的。

    排的是祖先不是后代：`probe_env.own_pids` 排后代，那条管的是「别把自己拉起的
    atk 算成别人占卡」，方向相反，不能拿来当这里的判据。
    """
    try:
        out = subprocess.run(["ps", "-eo", "pid,ppid"], capture_output=True,
                             text=True, timeout=20, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return {os.getpid()}
    parent = {}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            parent[int(parts[0])] = int(parts[1])
    chain, pid = set(), os.getpid()
    while pid and pid not in chain:
        chain.add(pid)
        pid = parent.get(pid, 0)
    return chain


def _pattern_running(pattern):
    """进程还在不在。用 pgrep -f，模式里加方括号没有意义（pgrep 不经过 shell）。"""
    try:
        done = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    except OSError:
        return False
    own = _own_chain()
    pids = [p for p in done.stdout.split() if p.isdigit() and int(p) not in own]
    return bool(pids)


def _progress(path, regex):
    """从日志尾部抓最后一处进度。抓不到返回空串，不报错——日志还没长出来是常态。

    只读末尾 `PROGRESS_TAIL_BYTES`：日志会长到几 MB，而要拿的只是最后一处进度。
    从中间截断可能切坏多字节字符，所以 `errors="replace"` 兜住——被切坏的那一行
    进度抓不到，下一轮就补上了。
    """
    if not path or not regex:
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - PROGRESS_TAIL_BYTES))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    hits = re.findall(regex, text)
    if not hits:
        return ""
    last = hits[-1]
    return "/".join(last) if isinstance(last, tuple) else str(last)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", action="append", default=[],
                        help="等这个文件出现。可给多次，全部出现才算完")
    parser.add_argument("--pattern", help="等匹配这个 pgrep -f 模式的进程全部退出")
    parser.add_argument("--pid", type=int, help="等这个 pid 退出")
    parser.add_argument("--timeout", type=int, default=HEARTBEAT_SECONDS,
                        help=f"这一段等多久，秒。默认 {HEARTBEAT_SECONDS}。"
                             "**这是心跳间隔不是任务上限**，没等到就原样再调一次")
    parser.add_argument("--progress-from", help="从这个日志里抓进度，只用于打印")
    parser.add_argument("--progress-re", default=PROGRESS_RE_DEFAULT,
                        help=f"抓进度的正则，默认 {PROGRESS_RE_DEFAULT}")
    args = parser.parse_args()

    if not (args.file or args.pattern or args.pid):
        print("--file、--pattern、--pid 至少给一个，否则没有等待条件。",
              file=sys.stderr)
        return 2

    def _proc_gone():
        """跑测进程还在不在。没给进程条件时返回 None = 判不了。"""
        if args.pattern and _pattern_running(args.pattern):
            return False
        if args.pid:
            try:
                os.kill(args.pid, 0)
                return False
            except (OSError, ProcessLookupError):
                return True
        return True if args.pattern else None

    def _files_missing():
        return [f for f in args.file if not Path(f).exists()]

    def done():
        if _files_missing():
            return False
        return _proc_gone() is not False

    what = "、".join(filter(None, [
        f"{len(args.file)} 个产物落盘" if args.file else "",
        f"进程 {args.pattern} 退出" if args.pattern else "",
        f"pid {args.pid} 退出" if args.pid else "",
    ]))
    print(f"等待      {what}；这一段上限 {args.timeout}s", flush=True)
    if args.file and not (args.pattern or args.pid):
        print("提醒      只等产物，没等进程。跑测进程崩掉时产物永远不出现，"
              "本次要等满 "
              f"{args.timeout}s 才返回。补 --pattern 或 --pid，崩没崩当场就知道。",
              flush=True)

    started = time.time()
    interval = INTERVAL_START
    last_seen = ""
    # 本段第一次抓到的进度。用来在超时那一刻回答「这一段里到底动没动」——
    # 只报最后一个读数的话，调用方分不清「还在跑」和「卡在这个数上」。
    first_seen = None
    while True:
        if done():
            waited = time.time() - started
            seen = _progress(args.progress_from, args.progress_re)
            print(f"等到了    用了 {waited:.0f}s"
                  + (f"，最后进度 {seen}" if seen else ""), flush=True)
            for path in args.file:
                print(f"          {path} 已落盘", flush=True)
            return 0
        # **进程没了而产物不在 = 崩了。** 两个条件是与的关系，不特判的话这里会
        # 一路等满这一段才返回——实测过一次第 3 秒就崩的 `NameError`，等产物
        # 等到了上限。特判之后崩没崩在一个轮询间隔内就知道。
        missing = _files_missing()
        if missing and _proc_gone() is True:
            waited = time.time() - started
            seen = _progress(args.progress_from, args.progress_re)
            print(f"\n崩了      进程已退出，但产物没落盘（等了 {waited:.0f}s）",
                  file=sys.stderr)
            for path in missing:
                print(f"          缺 {path}", file=sys.stderr)
            if seen:
                print(f"进度      停在 {seen}", file=sys.stderr)
            print("下一步    **不要再等**，去看日志尾部。再调一次本脚本没有意义："
                  "进程已经不在了。", file=sys.stderr)
            return 3

        waited = time.time() - started
        if waited >= args.timeout:
            seen = _progress(args.progress_from, args.progress_re)
            print(f"\n没等到    这一段等了 {waited:.0f}s：{what}", file=sys.stderr)
            if not seen:
                print(f"进度      抓不到（没给 --progress-from，或日志里还没长出"
                      f"匹配 {args.progress_re} 的行）。在跑还是卡住，这里判不了。",
                      file=sys.stderr)
                print("下一步    原样再调一次。连着两段都抓不到，再去看日志尾部。",
                      file=sys.stderr)
            elif first_seen is not None and seen != first_seen:
                print(f"进度      {seen}（这一段从 {first_seen} 长到 {seen}，还在动）",
                      file=sys.stderr)
                print("下一步    原样再调一次继续等。**没等到不等于失败**，"
                      "这一段的上限只是心跳间隔。", file=sys.stderr)
            else:
                print(f"进度      {seen}，这一段 {waited:.0f}s 没动过", file=sys.stderr)
                print(f"下一步    看日志尾部：{args.progress_from or '本轮日志'}。"
                      "跑测脚本那侧有停滞检测会自己杀，这里只报现象、不下结论。",
                      file=sys.stderr)
            return 1
        seen = _progress(args.progress_from, args.progress_re)
        if seen and first_seen is None:
            first_seen = seen
        if seen and seen != last_seen:
            print(f"          {waited:.0f}s  进度 {seen}", flush=True)
            last_seen = seen
        time.sleep(min(interval, args.timeout - waited))
        interval = min(interval * INTERVAL_GROWTH, INTERVAL_MAX)


if __name__ == "__main__":
    sys.exit(main())
