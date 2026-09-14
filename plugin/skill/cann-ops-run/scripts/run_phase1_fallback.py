"""Phase 1 单算子兜底跑测：用于合并 build 连坐失败的仓。

策略：对指定仓的 BUILD_FAIL 算子，串行调用 phase_examples.process_op
（独立 build/install/run），仓间 ProcessPool 并发（默认 3 worker）。

为什么不复用 run_phase1_batched：合并 build 一坏全坏，无法区分真假；
单算子串行才能识别每个算子的真实状态。

CLI：
  python3 run_phase1_fallback.py --repo-mapping repo1=path1,repo2=path2,...
                                 [--statuses BUILD_FAIL,INSTALL_FAIL]
                                 [--max-workers 3]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from state import load as load_state  # noqa: E402
from utils import parse_repo_mapping, persist_quickstart_record  # noqa: E402
from quickstart_probe import discover_command_templates  # noqa: E402

# 汇总报告写到 CWD/cann-ops-report/（逐仓产物在 <repo>/test/）
OUTPUTS_DIR = Path.cwd() / "cann-ops-report"

DEFAULT_STATUSES = {"BUILD_FAIL", "INSTALL_FAIL"}

# SOC 由 main() 从 --soc 参数填入，跨进程通过 fork 继承
SOC: str = ""

# 用户已确认「某仓解析不出命令模板时改用默认模板」；由 main() 从 --force-default-template 填入
FORCE_DEFAULT_TEMPLATE: bool = False


# REPO_PATHS 在 main() 由 CLI 参数填入；worker 通过 initializer 显式接收（spawn 安全）
REPO_PATHS: dict[str, str] = {}


def _init_worker(soc: str, repo_paths: dict[str, str], force_default_template: bool) -> None:
    """在每个 worker 进程里恢复全局；fork 平台冗余、spawn 平台必需。"""
    global SOC, REPO_PATHS, FORCE_DEFAULT_TEMPLATE
    SOC = soc
    REPO_PATHS = repo_paths
    FORCE_DEFAULT_TEMPLATE = force_default_template


def pick_ops(repo: str, statuses: set[str]) -> list[str]:
    data = load_state()
    ops = data["repos"].get(repo, {}).get("ops", {})
    return [
        op for op, st in ops.items()
        if st.get("phase1", {}).get("status") in statuses
    ]


def run_repo_fallback(repo: str, ops: list[str]) -> dict:
    """对一个仓的 ops 列表串行单算子跑测。子进程内 import phase_examples 直跑。"""
    from phase_examples import process_op

    repo_path_str = REPO_PATHS.get(repo)
    if not repo_path_str:
        print(f"[{repo}] SKIP: 调用方未提供该仓路径（--repo-mapping）", flush=True)
        return {"repo": repo, "total_s": 0, "counts": {"SKIPPED": len(ops)}, "per_op": []}
    repo_path = Path(repo_path_str)

    templates = discover_command_templates(repo_path)
    missing = [k for k in ("build", "run") if templates[k] is None]
    if missing and not FORCE_DEFAULT_TEMPLATE:
        print(f"[{repo}] ✗ 未能从 {templates['source']} 解析出 {'/'.join(missing)} 命令模板，"
              f"跳过该仓。确认要用默认模板就带 --force-default-template 重跑，"
              f"或者先检查/补齐该仓的 QUICKSTART.md。", flush=True)
        return {"repo": repo, "total_s": 0, "counts": {}, "per_op": [],
                "status": "TEMPLATE_UNRESOLVED", "missing": missing, "source": templates["source"]}
    persist_quickstart_record(repo, templates)
    from phase_examples import _DEFAULT_BUILD_TEMPLATE, _DEFAULT_RUN_TEMPLATE
    build_template = templates["build"] or _DEFAULT_BUILD_TEMPLATE
    run_template = templates["run"] or _DEFAULT_RUN_TEMPLATE

    t0 = time.time()
    counts: dict[str, int] = {}
    per_op = []
    print(f"[{repo}] start fallback for {len(ops)} ops", flush=True)
    for i, op in enumerate(ops, 1):
        op_t0 = time.time()
        status = process_op(
            repo, repo_path, op, SOC,
            build_timeout=900, install_timeout=300, test_timeout=600,
            build_template=build_template, run_template=run_template,
        )
        dt = time.time() - op_t0
        counts[status] = counts.get(status, 0) + 1
        per_op.append({"op": op, "status": status, "duration_s": round(dt, 1)})
        symbol = "✓" if status == "PASS" else "✗"
        print(f"[{repo}] [{i}/{len(ops)}] {symbol} {op}: {status} ({dt:.0f}s)", flush=True)

    total = time.time() - t0
    print(f"[{repo}] done {counts.get('PASS',0)}/{len(ops)} PASS in {total:.0f}s", flush=True)
    return {"repo": repo, "total_s": total, "counts": counts, "per_op": per_op}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-mapping", required=True,
                    help="仓名到本地路径的映射，CSV 格式：repo1=path1,repo2=path2,...")
    ap.add_argument("--soc", required=True,
                    help="目标 SOC 名称（如 ascend910b / ascend950 等），由 skill 询问用户得到")
    ap.add_argument("--statuses", default=",".join(sorted(DEFAULT_STATUSES)),
                    help="re-run ops in these phase1 statuses")
    ap.add_argument("--max-workers", type=int, default=3)
    ap.add_argument("--force-default-template", action="store_true",
                    help="某仓 QUICKSTART.md 解析不出命令模板时，用户已确认改用默认模板")
    args = ap.parse_args()

    global SOC, FORCE_DEFAULT_TEMPLATE
    SOC = args.soc
    FORCE_DEFAULT_TEMPLATE = args.force_default_template

    REPO_PATHS.update(parse_repo_mapping(args.repo_mapping))
    repos = list(REPO_PATHS.keys())
    if not repos:
        ap.error("--repo-mapping 至少要包含一项")
    statuses = {s.strip() for s in args.statuses.split(",") if s.strip()}

    plan = {repo: pick_ops(repo, statuses) for repo in repos}
    total_ops = sum(len(v) for v in plan.values())
    print(f"=== fallback plan: {total_ops} ops across {len(repos)} repos ===")
    for repo, ops in plan.items():
        print(f"  [{repo}] {len(ops)} ops")
    if total_ops == 0:
        print("nothing to do.")
        return 0

    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=args.max_workers,
                             initializer=_init_worker,
                             initargs=(SOC, dict(REPO_PATHS), FORCE_DEFAULT_TEMPLATE)) as ex:
        fut2repo = {
            ex.submit(run_repo_fallback, repo, ops): repo
            for repo, ops in plan.items() if ops
        }
        for fut in as_completed(fut2repo):
            results.append(fut.result())

    total = time.time() - t0
    print("\n📊 单算子兜底跑测最终报告")
    overall: dict[str, int] = {}
    unresolved = [r for r in results if r.get("status") == "TEMPLATE_UNRESOLVED"]
    for r in sorted(results, key=lambda x: x["repo"]):
        if r.get("status") == "TEMPLATE_UNRESOLVED":
            print(f"  [{r['repo']}] ⛔ 未跑：命令模板缺 {'/'.join(r['missing'])}（{r['source']}）")
            continue
        c = r["counts"]
        passed = c.get("PASS", 0)
        n = sum(c.values())
        print(f"  [{r['repo']}] {passed}/{n} PASS, {r['total_s']:.0f}s, {c}")
        for k, v in c.items():
            overall[k] = overall.get(k, 0) + v
    print(f"\nTOTAL: {overall.get('PASS',0)}/{sum(overall.values())} PASS")
    print(f"状态分布: {overall}")
    print(f"总耗时: {total/60:.1f} 分钟")

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUTS_DIR / "phase1_fallback_report.json"
    report_path.write_text(json.dumps({
        "results": results, "overall": overall, "total_s": total,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📄 详细报告: {report_path}")

    # 兜底跑测改写了 per-repo run_state，刷新跨仓 SUMMARY.md（否则它停在 batched 那轮）
    from state import write_summary_md
    summary = write_summary_md(phase="phase1", soc=args.soc)
    print(f"📄 摘要(给人看): {summary}")

    if unresolved:
        print(f"\n⛔ {len(unresolved)} 个仓未跑：命令模板解析不出来，等用户确认要不要用默认模板。"
              f"确认后带 --force-default-template 重跑这些仓。")
        return 4  # 优先于 0：这是需要用户明确决策的问题

    return 0


if __name__ == "__main__":
    sys.exit(main())
