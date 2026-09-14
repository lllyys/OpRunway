"""Phase 2：opkernel UT。

来源：
  bash build.sh --help 中的：--opkernel_test  build and run opkernel unit tests
  scripts/ci/check_kernel_ut.sh：判断 <op>/tests/ut/op_kernel/ 是否存在

CLI（skill 内部）：
  python3 phase_kernel_ut.py --repo <name> --repo-path <path> \
      --inputs <json> --soc <soc> [--op <op>] [--timeout 1800]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from state import init_repo, update_op  # noqa: E402
from utils import ensure_log_path, run_cmd  # noqa: E402
from phase_examples import find_op_dir  # noqa: E402


def has_kernel_ut(op_dir: Path) -> bool:
    return (op_dir / "tests" / "ut" / "op_kernel").is_dir()


def process_op(repo: str, repo_path: Path, op: str, soc: str, timeout: int) -> str:
    op_dir = find_op_dir(repo_path, op)
    if op_dir is None or not has_kernel_ut(op_dir):
        log = ensure_log_path(repo, op, "phase2.precheck")
        log.write_text(
            f"[SKIP] op_dir={op_dir} kernel_ut_present="
            f"{has_kernel_ut(op_dir) if op_dir else False}\n",
            encoding="utf-8",
        )
        update_op(repo, op, "phase2", "SKIPPED_NO_ARTIFACT", 0.0, str(log),
                  extra={"reason": "no tests/ut/op_kernel/"})
        return "SKIP"

    log = ensure_log_path(repo, op, "phase2.kernel_ut")
    cmd = f"bash build.sh --opkernel_test --ops={op} --soc={soc}"
    res = run_cmd(cmd, repo_path, timeout=timeout, log_path=log)

    if res.timed_out:
        update_op(repo, op, "phase2", "TIMEOUT", res.duration_s, str(log))
        return "TIMEOUT"
    if res.exit_code != 0:
        update_op(repo, op, "phase2", "RUN_EXIT_FAIL", res.duration_s, str(log),
                  extra={"exit_code": res.exit_code})
        return "RUN_EXIT_FAIL"

    update_op(repo, op, "phase2", "PASS", res.duration_s, str(log))
    return "PASS"


def main() -> int:
    from utils import resolve_ops, OpsResolutionError  # noqa: E402

    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--soc", required=True,
                    help="目标 SOC 名称（如 ascend910b / ascend950 等），由 skill 询问用户得到")
    ap.add_argument("--op", default=None)
    ap.add_argument("--ops", default="",
                    help="目标算子 CSV，例如 op1,op2,op3。若不传则按 --ops-file → cann-950-feature-scan 产物的优先级回退")
    ap.add_argument("--ops-file", default="",
                    help="目标算子文件（.json 含 unique_targets / 顶层 list / 一行一算子的纯文本）")
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    repo_path = Path(args.repo_path)

    try:
        ops = resolve_ops(args.repo, cli_ops=args.ops or None,
                          cli_ops_file=args.ops_file or None)
    except OpsResolutionError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    init_repo(args.repo, ops)

    target_ops = [args.op] if args.op else ops
    for op in target_ops:
        if op not in ops:
            print(f"[WARN] op '{op}' not in target ops list, skipping", file=sys.stderr)
            continue
        status = process_op(args.repo, repo_path, op, args.soc, args.timeout)
        print(f"[{args.repo}] {op}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
