"""P3:逐字执行文档里的一条命令并真实落盘;P2/P4 的辅助(写 doc_meta、打判定)。

**纪律(本 skill 的灵魂)**:execute 模式只跑**传进来的原样命令**,在文档指明的 cwd 下用
`bash -lc`(登录 shell——机器 profile 若已 source CANN 即生效,这是「机器现成 CANN」的给定状态),
**绝不注入文档以外的 env / flag / source / 清缓存**,失败也不重试。它只忠实执行 + 记录,
判定(OK/FAIL/...)交给 agent 看真实输出后用 --judge 写回。

三种模式:
  执行:  <python> <skill>/scripts/run_step.py --repo R --idx N --cwd DIR --cmd '逐字命令' [--doc-quote '...'] [--expected '...'] [--timeout 1800]
  判定:  <python> <skill>/scripts/run_step.py --repo R --idx N --judge --verdict OK|FAIL|DOC_AMBIGUOUS|DOC_MISSING [--defect '...'] [--fix '...']
  记元:  <python> <skill>/scripts/run_step.py --repo R --meta --doc <文档相对路径> [--prereq '前提声明' ...]

**判上一步 + 跑下一步合成一次调用**(省掉逐步的两次往返):执行模式可带
`--prev-verdict`(默认判 idx-1,可用 `--prev-idx` 指定),先落上一步判定再执行本步。
内置「卡住即停」闸门:上一步判成 FAIL/DOC_AMBIGUOUS/DOC_MISSING 且本步不是它自己时,
**拒绝执行本步**并提示收尾——比人工两次调用更难绕过纪律。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _state  # noqa: E402

_TAIL_LINES = 60  # steps.json 里只存输出尾部摘录;完整日志在 steps/<idx>.log


def _excerpt(text: str, n: int = _TAIL_LINES) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:]) if len(lines) > n else text


def execute(repo: str, idx: int, cwd: str, cmd: str, doc_quote: str = "",
            expected: str = "", timeout: int = 1800, slug: str = "") -> dict:
    log = _state.logs_dir(repo) / f"{idx:02d}.{_state.slug(slug or cmd)}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    run_cwd = Path(cwd).expanduser()
    start = time.time()
    timed_out = False
    if not run_cwd.is_dir():
        # cwd 不存在也如实记录(可能是文档没把 cwd 说清/前一步没产出该目录)
        stdout, stderr, code = "", f"[run_step] cwd 不存在: {run_cwd}", 127
    else:
        try:
            # 关键:bash -lc 跑「原样命令」,不前置 source、不加任何 env/flag
            res = subprocess.run(["bash", "-lc", cmd], cwd=str(run_cwd),
                                 capture_output=True, text=True, errors="replace",
                                 timeout=timeout)
            stdout, stderr, code = res.stdout, res.stderr, res.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = (e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")) \
                + f"\n[run_step] 超时 {timeout}s 被终止"
            code = 124
        except OSError as e:
            stdout, stderr, code = "", f"[run_step] 启动失败: {e}", 126
    dur = round(time.time() - start, 1)
    log.write_text(
        f"$ cd {run_cwd}\n$ {cmd}\n[timeout={timeout}s exit={code} timed_out={timed_out} dur={dur}s]\n"
        f"\n--- STDOUT ---\n{stdout}\n--- STDERR ---\n{stderr}\n", encoding="utf-8")
    record = {
        "idx": idx, "doc_quote": doc_quote, "command": cmd, "cwd": str(run_cwd),
        "expected": expected, "exit_code": code, "duration_s": dur, "timed_out": timed_out,
        "stdout_excerpt": _excerpt(stdout), "stderr_excerpt": _excerpt(stderr),
        "log_path": str(log), "verdict": "UNJUDGED",
    }
    _state.upsert_step(repo, record)
    return record


def judge_prev(repo: str, prev_idx: int, cur_idx: int, verdict: str,
               defect: str | None, fix: str | None,
               injected_fix: str | None) -> tuple[bool, bool, str]:
    """执行前先给上一步落判定。返回 (是否成功, 是否放行本步, 提示文本)。

    放行规则(卡住即停):上一步是卡点(FAIL/DOC_AMBIGUOUS/DOC_MISSING)时不放行——
    除非本次要跑的就是那一步本身(探索趟注入修复后重跑同一 idx,属正当续跑)。
    """
    ok = _state.set_verdict(repo, prev_idx, verdict, defect, fix, injected_fix)
    if not ok:
        return False, False, f"[ERROR] --prev-verdict 指向的 idx={prev_idx} 不存在(先执行再判定)"
    note = f"[judge #{prev_idx}] → {verdict}" + (f"  (注入修复: {injected_fix})" if injected_fix else "")
    if verdict in _state.BLOCKER_VERDICTS and prev_idx != cur_idx:
        return True, False, (
            f"{note}\n"
            f"[STOP] 第 {prev_idx} 步判为 {verdict}(卡点),本次**不执行**第 {cur_idx} 步。\n"
            f"       忠实趟:到此为止,直接出报告(render_report --kind faithful)。\n"
            f"       探索趟:注入修订建议后重跑第 {prev_idx} 步(--idx {prev_idx}),不要跳过它。")
    return True, True, note


def main() -> int:
    ap = argparse.ArgumentParser(description="cann-doc-quickstart-check 忠实执行器 / 判定器 / 元信息")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--idx", type=int)
    # 模式开关
    ap.add_argument("--judge", action="store_true", help="判定模式:给某步打 verdict")
    ap.add_argument("--meta", action="store_true", help="记元模式:写 doc_meta.json")
    # 执行模式参数
    ap.add_argument("--cwd", help="文档指明的工作目录(绝对路径)")
    ap.add_argument("--cmd", help="逐字命令(原样执行,勿改)")
    ap.add_argument("--doc-quote", default="", help="文档原文")
    ap.add_argument("--expected", default="", help="文档声称的期望")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--slug", default="")
    # 判定模式参数
    ap.add_argument("--verdict", choices=sorted(_state.VERDICTS))
    ap.add_argument("--defect", default=None, help="文档缺陷描述")
    ap.add_argument("--fix", default=None, help="修订建议(文档应补/改成什么)")
    ap.add_argument("--injected-fix", default=None,
                    help="探索趟专用:为绕过本步文档缺陷,实际注入的修复(只能是本 skill 提出的修订建议,如 source/soc/proxy)")
    # 记元模式参数
    ap.add_argument("--doc", help="选定文档(相对仓根)")
    ap.add_argument("--prereq", action="append", default=[], help="文档声明的前提(可多次)")
    # 执行模式可选:同一次调用里先给「上一步」落判定,省一次往返
    ap.add_argument("--prev-verdict", choices=sorted(_state.VERDICTS - {"UNJUDGED"}),
                    help="执行本步前,先给上一步打判定(省一次调用)")
    ap.add_argument("--prev-idx", type=int, default=None,
                    help="--prev-verdict 作用的步号,默认 idx-1")
    ap.add_argument("--prev-defect", default=None, help="上一步的缺陷描述")
    ap.add_argument("--prev-fix", default=None, help="上一步的修订建议")
    ap.add_argument("--prev-injected-fix", default=None,
                    help="探索趟专用:上一步实际注入了什么修复")
    args = ap.parse_args()

    if args.meta:
        if not args.doc:
            ap.error("--meta 需 --doc <文档路径>")
        _state.save_meta(args.repo, {"doc": args.doc, "declared_prerequisites": args.prereq})
        print(f"[meta] {args.repo}: doc={args.doc} prereqs={len(args.prereq)}")
        return 0

    if args.judge:
        if args.idx is None or not args.verdict:
            ap.error("--judge 需 --idx 与 --verdict")
        ok = _state.set_verdict(args.repo, args.idx, args.verdict, args.defect, args.fix,
                                args.injected_fix)
        if not ok:
            print(f"[ERROR] 找不到 idx={args.idx} 的步骤(先执行再判定)", file=sys.stderr)
            return 1
        print(f"[judge] {args.repo} #{args.idx} → {args.verdict}"
              + (f"  (注入修复: {args.injected_fix})" if args.injected_fix else ""))
        return 0

    # 执行模式
    if args.idx is None or args.cwd is None or args.cmd is None:
        ap.error("执行模式需 --idx --cwd --cmd")

    if args.prev_verdict:
        prev_idx = args.prev_idx if args.prev_idx is not None else args.idx - 1
        ok, proceed, note = judge_prev(args.repo, prev_idx, args.idx, args.prev_verdict,
                                       args.prev_defect, args.prev_fix, args.prev_injected_fix)
        if not ok:
            print(note, file=sys.stderr)
            return 1          # 判定没落上就执行下一步 = 台账缺一条,必须硬失败
        print(note)
        if not proceed:
            return 0          # 卡住即停:判定已落盘,本步不执行

    rec = execute(args.repo, args.idx, args.cwd, args.cmd, args.doc_quote,
                  args.expected, args.timeout, args.slug)
    blk = "  ← blocker(若判 FAIL/DOC_*,卡住即停)" if rec["exit_code"] != 0 else ""
    print(f"[run #{rec['idx']}] exit={rec['exit_code']} dur={rec['duration_s']}s "
          f"log={rec['log_path']}{blk}")
    print("--- 输出尾部(供 agent 对照文档期望后 --judge)---")
    print(rec["stdout_excerpt"][-1200:] or "(stdout 空)")
    if rec["stderr_excerpt"].strip():
        print("--- stderr 尾部 ---")
        print(rec["stderr_excerpt"][-800:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
