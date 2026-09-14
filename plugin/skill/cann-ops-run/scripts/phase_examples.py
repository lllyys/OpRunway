"""Phase 1：examples 端到端跑测。

每个算子的生命周期（默认模板；实际命令优先从目标仓自己的 docs/QUICKSTART.md 解析，
解析不到才 fallback 到下面这套默认值，见 quickstart_probe.discover_command_templates）：
  step 1: build       bash build.sh --pkg --soc=<soc> --ops=<op> -j16
  step 2: install     ./build_out/cann-ops-<repo>-*linux*.run --quiet
  step 3: run         bash build.sh --run_example <op> eager cust --vendor_name=<vendor>
  step 4: judge       exit_code==0 AND stdout matches success_pattern

CLI（skill 内部使用，用户不直接调用）：
  python3 phase_examples.py --repo <name> --repo-path <path> \
      --inputs <json> --soc <soc_version> [--op <op>] [--build-timeout 600] \
      [--test-timeout 600]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 允许直接 python3 phase_examples.py（不用 -m runner.phase_examples）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from state import init_repo, update_op  # noqa: E402
from utils import (  # noqa: E402
    append_ld_library_path, classify_run_status, ensure_log_path, find_run_pkg,
    persist_quickstart_record, run_cmd, vendor_name_for,
)
from quickstart_probe import discover_command_templates  # noqa: E402

_DEFAULT_BUILD_TEMPLATE = "bash build.sh --pkg --soc={soc} --ops={op} -j{jobs}"
_DEFAULT_RUN_TEMPLATE = "bash build.sh --run_example {op} eager cust --vendor_name=custom"

# build 并行度 -j：单算子路径用全部可用核（无仓间争用，吃满即可）
try:
    _JOBS = len(os.sched_getaffinity(0))
except AttributeError:
    _JOBS = os.cpu_count() or 16


def has_examples(op_dir: Path) -> bool:
    """判断算子目录下是否存在可跑的 example（test_*.cpp）。"""
    ex = op_dir / "examples"
    if not ex.is_dir():
        return False
    return any(ex.rglob("test_*.cpp"))


def find_op_dir(repo_path: Path, op: str) -> Path | None:
    """在仓内定位算子目录（带 op_kernel/op_host 的才算真目录，排除 3rd 和 fast_kernel_launch_example）。"""
    for p in repo_path.rglob(op):
        if not (p.is_dir() and p.name == op):
            continue
        sp = str(p)
        if "/3rd/" in sp or "/fast_kernel_launch_example/" in sp:
            continue
        if (p / "op_kernel").exists() or (p / "op_host").exists():
            return p
    return None


def build_op(repo: str, repo_path: Path, op: str, soc: str, timeout: int,
             build_template: str = _DEFAULT_BUILD_TEMPLATE) -> bool:
    log = ensure_log_path(repo, op, "phase1.build")
    cmd = build_template.format(soc=soc, op=op, jobs=_JOBS)
    res = run_cmd(cmd, repo_path, timeout=timeout, log_path=log)
    if res.exit_code != 0:
        update_op(repo, op, "phase1", "BUILD_FAIL", res.duration_s, str(log),
                  extra={"step": "build"})
        return False
    return True


def install_pkg(repo: str, repo_path: Path, op: str, install_timeout: int) -> bool:
    pkg = find_run_pkg(repo_path)
    log = ensure_log_path(repo, op, "phase1.install")
    if pkg is None:
        log.write_text("[ERROR] build_out/cann-ops-*linux*.run not found\n", encoding="utf-8")
        update_op(repo, op, "phase1", "INSTALL_FAIL", 0.0, str(log),
                  extra={"step": "install", "reason": "run_pkg_not_found"})
        return False
    cmd = f"{pkg} --quiet"
    res = run_cmd(cmd, repo_path, timeout=install_timeout, log_path=log)
    if res.exit_code != 0:
        # 部分版本没有 --quiet，回退尝试无参
        cmd = str(pkg)
        res = run_cmd(cmd, repo_path, timeout=install_timeout, log_path=log)
        if res.exit_code != 0:
            update_op(repo, op, "phase1", "INSTALL_FAIL", res.duration_s, str(log),
                      extra={"step": "install"})
            return False
    return True


def run_example(repo: str, repo_path: Path, op: str, timeout: int,
                run_template: str = _DEFAULT_RUN_TEMPLATE) -> tuple[bool, str]:
    """跑 run_example，返回 (passed, status)。"""
    log = ensure_log_path(repo, op, "phase1.run")
    cmd = run_template.format(op=op)

    env = os.environ.copy()
    ascend_home = env.get("ASCEND_HOME_PATH", "")
    if ascend_home:
        env = append_ld_library_path(env, repo, ascend_home)

    res = run_cmd(cmd, repo_path, timeout=timeout, log_path=log, env=env)

    # 单点判定（含 TIMEOUT + T2 空退归 SKIPPED_NO_RUN_ARTIFACT），与 batched runner 共用
    status, reason = classify_run_status(res.stdout, res.stderr, res.exit_code,
                                         res.duration_s, res.timed_out)
    extra = {"step": "run_example", "exit_code": res.exit_code, "verdict_reason": reason}
    if status == "UNCERTAIN":
        extra["note"] = "exit==0 but no strong pass/fail signal — agent review needed"
    update_op(repo, op, "phase1", status, res.duration_s, str(log), extra=extra)
    return status == "PASS", status


def process_op(repo: str, repo_path: Path, op: str, soc: str,
               build_timeout: int, install_timeout: int, test_timeout: int,
               build_template: str = _DEFAULT_BUILD_TEMPLATE,
               run_template: str = _DEFAULT_RUN_TEMPLATE) -> str:
    op_dir = find_op_dir(repo_path, op)
    if op_dir is None or not has_examples(op_dir):
        log = ensure_log_path(repo, op, "phase1.precheck")
        log.write_text(
            f"[SKIP] op_dir={op_dir} examples_present="
            f"{has_examples(op_dir) if op_dir else False}\n",
            encoding="utf-8",
        )
        update_op(repo, op, "phase1", "SKIPPED_NO_ARTIFACT", 0.0, str(log),
                  extra={"reason": "no examples/test_*.cpp"})
        return "SKIP"

    if not build_op(repo, repo_path, op, soc, build_timeout, build_template):
        return "BUILD_FAIL"
    if not install_pkg(repo, repo_path, op, install_timeout):
        return "INSTALL_FAIL"

    passed, status = run_example(repo, repo_path, op, test_timeout, run_template)
    return status


def main() -> int:
    from utils import resolve_ops, OpsResolutionError  # noqa: E402

    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--soc", required=True,
                    help="目标 SOC 名称（如 ascend910b / ascend950 等），由 skill 询问用户得到")
    ap.add_argument("--op", default=None,
                    help="只跑单个算子；若不传则跑下面 ops 来源解析出的全部算子")
    ap.add_argument("--ops", default="",
                    help="目标算子 CSV，例如 op1,op2,op3。若不传则按 --ops-file → cann-950-feature-scan 产物的优先级回退")
    ap.add_argument("--ops-file", default="",
                    help="目标算子文件（.json 含 unique_targets / 顶层 list / 一行一算子的纯文本）")
    ap.add_argument("--build-timeout", type=int, default=900)
    ap.add_argument("--install-timeout", type=int, default=300)
    ap.add_argument("--test-timeout", type=int, default=600)
    ap.add_argument("--force-default-template", action="store_true",
                    help="该仓 QUICKSTART.md 解析不出命令模板时，用户已确认改用默认模板；"
                         "不传此参数时解析失败会直接报错退出（退出码 4），等 skill 问过用户再决定")
    args = ap.parse_args()

    repo_path = Path(args.repo_path)
    if not repo_path.is_dir():
        print(f"[ERROR] repo path not found: {repo_path}", file=sys.stderr)
        return 1

    try:
        ops = resolve_ops(args.repo, cli_ops=args.ops or None,
                          cli_ops_file=args.ops_file or None)
    except OpsResolutionError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    init_repo(args.repo, ops)

    templates = discover_command_templates(repo_path)
    missing = [k for k in ("build", "run") if templates[k] is None]
    if missing and not args.force_default_template:
        print(f"[ERROR] {args.repo}: 未能从 {templates['source']} 解析出 {'/'.join(missing)} "
              f"命令模板。请确认是否改用默认模板后带 --force-default-template 重跑，"
              f"或者先检查/补齐该仓的 QUICKSTART.md。", file=sys.stderr)
        return 4
    persist_quickstart_record(args.repo, templates)
    build_template = templates["build"] or _DEFAULT_BUILD_TEMPLATE
    run_template = templates["run"] or _DEFAULT_RUN_TEMPLATE

    target_ops = [args.op] if args.op else ops
    for op in target_ops:
        if op not in ops:
            print(f"[WARN] op '{op}' not in target ops list, skipping", file=sys.stderr)
            continue
        status = process_op(args.repo, repo_path, op, args.soc,
                           args.build_timeout, args.install_timeout, args.test_timeout,
                           build_template, run_template)
        print(f"[{args.repo}] {op}: {status}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
