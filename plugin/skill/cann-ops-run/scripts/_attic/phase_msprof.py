"""Phase 4：msprof 性能采集（默认关，用户主动要求才启用）。

来源：<repo>/docs/QUICKSTART.md §三-2 性能采集
  msprof --application="./test_aclnn_<op>"

前置：Phase 1 必须 PASS（有可执行文件 build/test_aclnn_<op>）

判定：
  - exit_code == 0 → PASS（msprof 一般不输出标准 success pattern）
  - 跑完后产物在 <repo>/build/PROF_*/ 目录下，结果摘要由 msprof 自动 export

CLI（skill 内部）：
  python3 phase_msprof.py --repo <name> --repo-path <path> \
      --inputs <json> [--op <op>] [--timeout 600]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from state import init_repo, get_op, update_op  # noqa: E402
from utils import ensure_log_path, run_cmd  # noqa: E402


def find_test_executable(repo_path: Path, op: str) -> Path | None:
    """run_example 跑过后，可执行文件在 <repo>/build/test_aclnn_<op>。"""
    build_dir = repo_path / "build"
    if not build_dir.is_dir():
        return None
    candidates = list(build_dir.rglob(f"test_aclnn_{op}"))
    candidates += list(build_dir.rglob(f"test_aclnn_{op}_*"))
    candidates = [c for c in candidates if c.is_file() and os.access(str(c), os.X_OK)]
    return candidates[0] if candidates else None


def process_op(repo: str, repo_path: Path, op: str, timeout: int) -> str:
    # Phase 1 必须 PASS
    op_state = get_op(repo, op)
    if op_state.get("phase1", {}).get("status") != "PASS":
        log = ensure_log_path(repo, op, "phase4.precheck")
        log.write_text(
            f"[SKIP] phase1 status is {op_state.get('phase1', {}).get('status')}\n",
            encoding="utf-8",
        )
        update_op(repo, op, "phase4", "SKIPPED_NO_ARTIFACT", 0.0, str(log),
                  extra={"reason": "phase1 not PASS"})
        return "SKIP"

    exe = find_test_executable(repo_path, op)
    if exe is None:
        log = ensure_log_path(repo, op, "phase4.precheck")
        log.write_text(f"[SKIP] test executable not found under {repo_path}/build/\n",
                       encoding="utf-8")
        update_op(repo, op, "phase4", "SKIPPED_NO_ARTIFACT", 0.0, str(log),
                  extra={"reason": "executable not found"})
        return "SKIP"

    log = ensure_log_path(repo, op, "phase4.msprof")
    # 在可执行文件所在目录跑（QUICKSTART 推荐）
    cmd = f'msprof --application="./{exe.name}"'
    res = run_cmd(cmd, exe.parent, timeout=timeout, log_path=log)

    if res.timed_out:
        update_op(repo, op, "phase4", "TIMEOUT", res.duration_s, str(log))
        return "TIMEOUT"
    if res.exit_code != 0:
        update_op(repo, op, "phase4", "RUN_EXIT_FAIL", res.duration_s, str(log),
                  extra={"exit_code": res.exit_code})
        return "RUN_EXIT_FAIL"

    # msprof 成功后产物目录在 <executable_parent>/PROF_*/
    prof_dirs = sorted(exe.parent.glob("PROF_*"), reverse=True)
    prof_dir = str(prof_dirs[0]) if prof_dirs else None

    update_op(repo, op, "phase4", "PASS", res.duration_s, str(log),
              extra={"prof_dir": prof_dir})
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
    ap.add_argument("--timeout", type=int, default=600)
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
