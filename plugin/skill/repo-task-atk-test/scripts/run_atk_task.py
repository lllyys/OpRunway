"""带挂死检测地执行任意 ATK 命令。

ATK 有一类失败不会让进程退出：任务链没建起来，或 celery 在任务体之外崩掉，
结果永远不会发布，主进程就一直等，进度条停在 `0/N` 直到超时。

这类失败在日志里**秒级就有定论**，只是没人盯着看。人只能看到「跑得很久」，
于是按「算子慢」去排查，方向全错。本轮两次踩中，都是靠人工怀疑耗时才发现。

本脚本把「盯日志」这件事自动化，对精度、性能和其他任何 ATK 任务一视同仁：

- 命中终局特征时立刻停止并给出诊断，不等超时。
- 没有已知特征但进度长时间不前进时，按疑似挂死停止。
- 正常跑完时原样返回 ATK 的退出码。

用法：
    run_atk_task.py -o <日志路径> [--stall-timeout 秒] -- <完整 atk 命令>

退出码：ATK 自己的退出码；被判定挂死时为 2；监督器自身出错为 3。
"""

import argparse
import os
import re
import signal
import subprocess
import sys
import time

# celery 在任务体之外抛异常时，该任务的结果永远不会发布，
# 依赖它的 chain / chord 回调不会触发，主进程无限等待。
# 这是「进程还在但永远不会结束」的确定信号。
FATAL_OUTSIDE_BODY = re.compile(r"Exception raised outside body")

PROGRESS = re.compile(r"(\d+)\s*/\s*(\d+)\s*\[")
CREATE_START = re.compile(r"create_tasks start.*all_cases_num:\s*(\d+)")
CREATE_FAIL = re.compile(r"create_tasks fail")
FINISHED = re.compile(r"Total Task:\s*\d+,\s*success\s*\d+,\s*failed\s*\d+")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip(text):
    return ANSI.sub("", text).replace("\r", "\n")


def human(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{seconds:02d}s"


_last_report = [0.0]


def report_progress(current, started, every):
    """把已经解析出来的 done/total 打给用户看。

    这个数字本来只喂给挂死判定，一行都不外露——于是一轮上百条用例的跑测在
    终端里几十分钟毫无输出，人只能猜是不是卡了，实测两次被手动中断。
    打印按 `every` 秒节流，避免刷屏盖掉真正的诊断信息。
    """
    if not current:
        return
    now = time.time()
    done, total = int(current[0]), int(current[1])
    if now - _last_report[0] < every and done != total:
        return
    _last_report[0] = now
    elapsed = now - started
    eta = f"，预计剩余 {human(elapsed / done * (total - done))}" if done else ""
    print(f"  进度 {done}/{total}，已用 {human(elapsed)}{eta}", flush=True)


def diagnose(text, total_cases, create_fail_count):
    """只在能确定「永远不会结束」时返回诊断，否则返回 None。"""
    if FATAL_OUTSIDE_BODY.search(text):
        return ("celery 在任务体之外抛了异常，该任务的结果永远不会发布，"
                "依赖它的任务链回调不会触发。\n"
                "  这不是算子慢，是任务链已经断了，继续等只会等到超时。")
    if total_cases and create_fail_count >= total_cases:
        return (f"{create_fail_count}/{total_cases} 条用例建任务失败，没有任何任务会执行。\n"
                "  常见原因是节点配置不满足后端要求，日志里搜 create_tasks fail 看具体 error。")
    return None


def main():
    parser = argparse.ArgumentParser(
        description="带挂死检测地执行 ATK 命令",
        usage="run_atk_task.py -o <日志> [--stall-timeout 秒] -- <atk 命令>")
    parser.add_argument("-o", "--log", required=True, help="ATK 输出落盘路径")
    parser.add_argument("--stall-timeout", type=int, default=900,
                        help="进度不前进多少秒判为疑似挂死，默认 900")
    parser.add_argument("--poll", type=int, default=5, help="轮询间隔秒")
    parser.add_argument("--progress-every", type=int, default=30,
                        help="至少隔多少秒打印一次进度，默认 30")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="-- 之后是完整的 atk 命令")
    args = parser.parse_args()

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        print("没有给出要执行的 atk 命令。用 -- 分隔，例如：\n"
              "  run_atk_task.py -o run.log -- atk node -b cpu task -c cases.json -tk accuracy",
              file=sys.stderr)
        return 3

    print("执行：" + " ".join(command))
    print(f"日志：{args.log}（进度停滞 {args.stall_timeout}s 判为疑似挂死）")

    with open(args.log, "wb") as sink:
        process = subprocess.Popen(command, stdout=sink, stderr=subprocess.STDOUT,
                                   start_new_session=True)

        offset = 0
        seen = ""
        total_cases = 0
        create_fail = 0
        last_progress = None
        started = last_advance = time.time()
        verdict = None

        while True:
            code = process.poll()
            with open(args.log, "rb") as reader:
                reader.seek(offset)
                chunk = reader.read()
                offset = reader.tell()
            if chunk:
                seen += strip(chunk.decode("utf-8", "replace"))

            if code is not None:
                break

            match = CREATE_START.search(seen)
            if match:
                total_cases = int(match.group(1))
            create_fail = len(CREATE_FAIL.findall(seen))

            if not FINISHED.search(seen):
                verdict = diagnose(seen, total_cases, create_fail)
                if verdict:
                    break

                marks = PROGRESS.findall(seen)
                current = marks[-1] if marks else None
                if current != last_progress:
                    last_progress = current
                    last_advance = time.time()
                    report_progress(current, started, args.progress_every)
                elif time.time() - last_advance > args.stall_timeout:
                    done, total = current if current else ("?", "?")
                    verdict = (f"进度停在 {done}/{total} 超过 {args.stall_timeout}s 没有前进，"
                               "且日志里没有新的用例事件。\n"
                               "  按疑似挂死处理。若该算子确实存在超长用例，"
                               "调大 --stall-timeout 后重跑。")
                    break

            time.sleep(args.poll)

        if verdict:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except OSError:
                pass
            process.wait(timeout=30)
            print(f"\n✗ 判定挂死，已停止。\n  {verdict}", file=sys.stderr)
            tail = [line for line in seen.splitlines()
                    if line.strip() and "ATK任务进度" not in line][-8:]
            print("  最后若干条非进度日志：", file=sys.stderr)
            for line in tail:
                print(f"    {line[:160]}", file=sys.stderr)
            print(f"  完整日志：{args.log}", file=sys.stderr)
            return 2

    # 收尾时补打最后一条进度：轮询到进程退出就 break 了，
    # 收官的 N/N 落在最后一次轮询之后，不补就永远看不到「跑完了多少」。
    marks = PROGRESS.findall(seen)
    if marks:
        report_progress(marks[-1], started, 0)
    print(f"ATK 正常结束，退出码 {code}，用时 {human(time.time() - started)}")
    return code


if __name__ == "__main__":
    sys.exit(main())
