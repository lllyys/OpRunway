"""Phase 3：pytest 精度对照。

来源：<op>/tests/pytest/README.md（如 ops-transformer/attention/flash_attention_score）
  - 命令：pytest -s
  - 框架：CPU 实现 (cpu_impl.py) vs NPU 实现 (npu_impl.py) 精度对比
  - 前置：torch_npu 已安装，CANN 环境变量已 source

判定：
  - exit_code == 0 且 stdout 含 "passed" → PASS
  - exit_code != 0 → FAIL（pytest 用 1=测试失败，2=异常等）

CLI（skill 内部）：
  python3 phase_pytest.py --repo <name> --repo-path <path> \
      --inputs <json> [--op <op>] [--timeout 3600]
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


def has_pytest(op_dir: Path) -> bool:
    p = op_dir / "tests" / "pytest"
    if not p.is_dir():
        return False
    # 至少要有一个 test_*.py 才算可跑
    return any(p.glob("test_*.py"))


def process_op(repo: str, repo_path: Path, op: str, timeout: int) -> str:
    op_dir = find_op_dir(repo_path, op)
    if op_dir is None or not has_pytest(op_dir):
        log = ensure_log_path(repo, op, "phase3.precheck")
        log.write_text(
            f"[SKIP] op_dir={op_dir} pytest_present="
            f"{has_pytest(op_dir) if op_dir else False}\n",
            encoding="utf-8",
        )
        update_op(repo, op, "phase3", "SKIPPED_NO_ARTIFACT", 0.0, str(log),
                  extra={"reason": "no tests/pytest/test_*.py"})
        return "SKIP"

    pytest_dir = op_dir / "tests" / "pytest"
    log = ensure_log_path(repo, op, "phase3.pytest")
    cmd = "pytest -s"
    res = run_cmd(cmd, pytest_dir, timeout=timeout, log_path=log)

    if res.timed_out:
        update_op(repo, op, "phase3", "TIMEOUT", res.duration_s, str(log))
        return "TIMEOUT"

    verdict, reason = res.classify()

    if verdict == "FAIL":
        status = "RUN_EXIT_FAIL" if res.exit_code != 0 else "RUN_PATTERN_FAIL"
        update_op(repo, op, "phase3", status, res.duration_s, str(log),
                  extra={"exit_code": res.exit_code, "verdict_reason": reason})
        return status

    if verdict == "UNCERTAIN":
        update_op(repo, op, "phase3", "UNCERTAIN", res.duration_s, str(log),
                  extra={"exit_code": res.exit_code, "verdict_reason": reason,
                         "note": "exit==0 but no strong pass/fail signal — agent review needed"})
        return "UNCERTAIN"

    update_op(repo, op, "phase3", "PASS", res.duration_s, str(log))
    return "PASS"


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from utils import resolve_ops, OpsResolutionError  # noqa: E402

    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--op", default=None)
    ap.add_argument("--ops", default="",
                    help="目标算子 CSV，例如 op1,op2,op3。若不传则按 --ops-file → cann-950-feature-scan 产物的优先级回退")
    ap.add_argument("--ops-file", default="",
                    help="目标算子文件（.json 含 unique_targets / 顶层 list / 一行一算子的纯文本）")
    ap.add_argument("--timeout", type=int, default=3600)
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
        status = process_op(args.repo, repo_path, op, args.timeout)
        print(f"[{args.repo}] {op}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
