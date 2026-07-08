"""OpRunway 顶层编排（Layer 2 薄壳的本地驱动版）——串 Task 1→2→3。

Task 1 gen_cases → Task 2 repo_adapter + validator → Task 3 perf_compare。
stage 间只经 JSON/数据文件交接。CC/Codex/Antigravity 的薄壳只需换调用方式，核心不动。

用法：python run_workflow.py <spec.json> [--mode mock] [--out <dir>] [--defect id1,id2]
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_cases, repo_adapter, validator, perf_compare  # noqa: E402
import validate_acceptance_state as gate  # noqa: E402


def run(spec_path, mode="mock", out_dir="reports/_run", defect=None):
    if mode not in repo_adapter.MODES:  # 先校验，避免 Task1 已跑再 KeyError、留半产物
        raise SystemExit(f"unknown mode {mode!r}, supported={list(repo_adapter.MODES)}")
    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, "work")
    spec = json.load(open(spec_path, encoding="utf-8"))

    def _dump(obj, name):
        p = os.path.join(out_dir, name)
        json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return p

    print(f"=== OpRunway workflow · {spec['op']} · mode={mode} ===")
    for stale in ("_real_baseline.json", "perf_result.txt"):  # 清上轮残留，防 stale 真基线被复用
        sp = os.path.join(work, stale)
        if os.path.exists(sp):
            os.remove(sp)
    # Task 1
    caseset = gen_cases.gen_cases(spec, work)
    _dump(caseset, "caseset.json")
    print(f"[Task1 gen_cases] {len(caseset['cases'])} 用例")
    # Task 2
    evidence = repo_adapter.MODES[mode](caseset, work, defect_cases=defect)
    _dump(evidence, "evidence.json")
    verdict = validator.validate(spec, caseset, evidence)
    _dump(verdict, "verdict.json")
    o = verdict["overall"]
    print(f"[Task2 run+validate] 裁决={o['verdict']} {o['counts']}")
    # Task 3（new_example 会写真基线 _real_baseline.json；否则 mock）
    real_bl = os.path.join(work, "_real_baseline.json")
    if os.path.exists(real_bl):
        baseline = json.load(open(real_bl, encoding="utf-8"))
    else:
        baseline = perf_compare.mock_baseline(spec, evidence)
    _dump(baseline, "baseline.json")
    report = perf_compare.perf_compare(spec, caseset, evidence, baseline)
    _dump(report, "perf_report.json")
    print(f"[Task3 perf_compare] {report['summary']} (基线={report['baseline_source']})")

    ps = report["summary"]
    # 验收门（硬 blocker）：三级机器门读**落盘产物**独立复核（防跑子集/放宽阈值/混 e2e）。
    # 无性能要求的算子不跑 task3 门（免因缺性能用例误挡）。
    gate_stages = ["task1", "task2"]
    if ps.get("perf_cases", 0) > 0 or spec.get("perf", {}).get("baseline"):
        gate_stages.append("task3")
    gate_errs = {}
    for st in gate_stages:
        es = []
        gate._GATES[st](out_dir, es)
        if es:
            gate_errs[st] = es
    gate_passed = not gate_errs
    print(f"[验收门] {'/'.join(gate_stages)} → STATUS: {'PASSED' if gate_passed else 'FAILED'}"
          + ("" if gate_passed else f" · {gate_errs}"))

    # 总体口径：精度 + 性能 + 验收门都要过（门 FAILED 一票否决，不出 pass）
    perf_pass = (ps.get("status") == "ok" and ps.get("blocked", 0) == 0
                 and ps.get("perf_cases", 0) == ps.get("达标", 0))
    prec = verdict["overall"]["verdict"]
    if not gate_passed:
        overall = "BLOCKED(验收门未过)"
    elif prec == "pass" and perf_pass:
        overall = "PASS"
    elif prec == "fail":
        overall = "FAIL(精度)"
    elif not perf_pass:
        if ps.get("perf_cases"):
            overall = f"性能未达成({ps.get('status')})"
        elif spec.get("perf", {}).get("baseline"):
            overall = "BLOCKED(spec 声明性能目标但无性能用例)"
        else:
            overall = "PASS(无性能要求)"
    else:
        overall = "NEEDS_REVIEW"
    print(f"[总体] 精度={prec} · 性能达标 {ps.get('达标')}/{ps.get('perf_cases')}({ps.get('status')}) · 门={'PASSED' if gate_passed else 'FAILED'} → {overall}")

    # 门控后的**验收裁决**（区别于 raw verdict.json=validator 精度判定）：上游产物即下游输入
    _dump({"op": spec["op"], "overall": overall,
           "gate": {"passed": gate_passed, "errors": gate_errs},
           "precision_verdict": prec, "perf_status": ps.get("status")}, "acceptance.json")
    print(f"--- 产物在 {out_dir}/ ---")
    return {"verdict": verdict, "perf_report": report,
            "gate": {"passed": gate_passed, "errors": gate_errs}, "overall": overall}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--mode", default="mock", choices=list(repo_adapter.MODES))
    ap.add_argument("--out", default="reports/_run")
    ap.add_argument("--defect", default=None)
    a = ap.parse_args()
    result = run(a.spec, a.mode, a.out, a.defect.split(",") if a.defect else None)
    # CLI 退出码：门未过 / 精度fail / 性能未达 / blocked / needs_review → 非零，CI/上层可当硬失败
    sys.exit(0 if result["overall"].startswith("PASS") else 1)


if __name__ == "__main__":
    main()
