"""OpRunway 顶层编排（Layer 2 薄壳的本地驱动版）——串 Task 1→2→3。

Task 1 gen_cases → Task 2 repo_adapter + validator → Task 3 perf_compare。
stage 间只经 JSON/数据文件交接。CC/Codex/Antigravity 的薄壳只需换调用方式，核心不动。

用法：python run_workflow.py <spec.json> [--mode mock] [--out <dir>] [--defect id1,id2]
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_cases, repo_adapter, validator, perf_compare  # noqa: E402
import validate_acceptance_state as gate  # noqa: E402

# T6/T8：人读 overall → 机读 canonical 状态（task3 状态机词汇）。
_STATE_MAP = {
    "PASS": "PASSED", "PASS(无性能要求)": "PASSED",
    "FAIL(精度)": "FAILED_PRECISION", "NEEDS_REVIEW": "NEEDS_REVIEW",
    "PASSED_WITH_RISK": "PASSED_WITH_RISK",
    "BLOCKED_WAIT_GPU_BENCHMARK": "BLOCKED_WAIT_GPU_BENCHMARK",
    "BLOCKED_INCOMPARABLE_TIMING_SCOPE": "BLOCKED_INCOMPARABLE_TIMING_SCOPE",
}

def _canonical_state(overall, ps):
    """人读 overall → 机读 canonical 状态（T6/T8）。门因不可比/挂起而 FAILED 时据 perf status 细化，
    避免笼统 BLOCKED(验收门未过) 掩盖 canonical 出口。"""
    if overall in _STATE_MAP:
        return _STATE_MAP[overall]
    st = ps.get("status")
    if st == "blocked_incomparable_timing_scope":
        return "BLOCKED_INCOMPARABLE_TIMING_SCOPE"
    if st == "blocked_wait_gpu_benchmark":
        return "BLOCKED_WAIT_GPU_BENCHMARK"
    if isinstance(overall, str) and overall.startswith("性能未达成"):
        return "FAILED_PERFORMANCE"
    if isinstance(overall, str) and overall.startswith("BLOCKED"):
        return "BLOCKED_EVIDENCE_INCOMPLETE"
    return "NEEDS_REVIEW"


def _exit_code(overall):
    """退出码枚举（T5；修 startswith('PASS') 潜伏 bug——PASSED_WITH_RISK 曾被误判为 0 干净退出）：
      0 = 干净 PASS / PASS(无性能要求)；
      2 = PASSED_WITH_RISK（requires_human_cp、CI 挂起转人工、非自动合并/非自动失败）；
      1 = 其余（FAIL 精度 / 性能未达 / BLOCKED_* / NEEDS_REVIEW）。"""
    if overall in ("PASS", "PASS(无性能要求)"):
        return 0
    if overall == "PASSED_WITH_RISK":
        return 2
    return 1


def run(spec_path, mode="mock", out_dir="reports/_run", defect=None, perf_slow=None, gpu_baseline=None):
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
    import glob  # T6：清上轮小shape仿真图，防 stale SVG 让「有图」门误过（codex H7）
    for old in glob.glob(os.path.join(out_dir, "perf_sim_*.svg")):
        os.remove(old)
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
    # Task 3（new_example 会写真基线 _real_baseline.json；否则 mock；T8：--gpu-baseline / spec gpu_external）
    real_bl = os.path.join(work, "_real_baseline.json")
    expect_gpu = (gpu_baseline is not None
                  or spec.get("perf", {}).get("baseline") in ("gpu", "gpu_external"))
    expect_source = "gpu_external" if expect_gpu else None
    gpu_prov = None
    if gpu_baseline is not None:  # T8：解析外部 GPU 标杆(consumer 侧)；hard error→baseline None→挂起(非 PASS)
        import gpu_baseline as gpubl
        baseline, parse_report = gpubl.parse_gpu_baseline(gpu_baseline, caseset)
        _dump(parse_report, "gpu_baseline_parse_report.json")
        gpu_prov = {"source": expect_source, "path": gpu_baseline,
                    "contract_version": parse_report.get("contract_version"),
                    "parse_report": "gpu_baseline_parse_report.json",
                    "hard_errors": parse_report.get("hard_errors", 0)}
    elif os.path.exists(real_bl):
        baseline = json.load(open(real_bl, encoding="utf-8"))
    elif expect_gpu:  # 期待 GPU 标杆但没给 → 正规挂起（perf_compare 产 blocked_wait_gpu_benchmark）
        baseline = None
    else:
        baseline = perf_compare.mock_baseline(spec, evidence, slow_cases=perf_slow)
    if baseline is not None:
        _dump(baseline, "baseline.json")
    report = perf_compare.perf_compare(spec, caseset, evidence, baseline, expect_source=expect_source)
    if report["summary"].get("status") == "exception":  # T6：例外态渲染仿真图，门循环前落盘+记 sha
        import perf_sim_plot
        svg_name = f"perf_sim_{spec['op'].lower()}.svg"
        svg_path = os.path.join(out_dir, svg_name)
        perf_sim_plot.render_svg(report["simulation"], svg_path)
        report["simulation_plot"] = {"file": svg_name, "sha256": perf_sim_plot.sha256_of(svg_path)}
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

    # 总体口径：精度(放行看 acceptance) + 性能 + 验收门都要过（门 FAILED 一票否决，不出 pass）。
    # 精度 verdict ∈ {pass, fail, needs_review, passed_with_risk}；放行只看 acceptance（ADR 0005）。
    perf_pass = (ps.get("status") == "ok" and ps.get("blocked", 0) == 0
                 and ps.get("perf_cases", 0) == ps.get("达标", 0))
    ov = verdict["overall"]
    prec = ov["verdict"]
    requires_human_cp = False       # T6：PASSED_WITH_RISK 走人工 CP（挂起转人工，非自动合并/失败）
    if not gate_passed:
        overall = "BLOCKED(验收门未过)"
    elif prec == "fail":
        overall = "FAIL(精度)"
    elif prec == "needs_review":
        overall = "NEEDS_REVIEW"
    elif not perf_pass:                                  # 精度 pass/passed_with_risk，但性能有问题
        st = ps.get("status")
        if st == "exception":                            # T6 小shape例外：门已过(有图+交叉一致)→放行需人核
            overall, requires_human_cp = "PASSED_WITH_RISK", True
        elif st == "blocked_wait_gpu_benchmark":         # T8 缺外部 GPU 标杆：正规挂起、非 fail
            overall = "BLOCKED_WAIT_GPU_BENCHMARK"
        elif st == "blocked_incomparable_timing_scope":  # T8 双边口径不可比（通常门已先判 FAILED）
            overall = "BLOCKED_INCOMPARABLE_TIMING_SCOPE"
        elif ps.get("perf_cases"):
            overall = f"性能未达成({st})"
        elif spec.get("perf", {}).get("baseline"):
            overall = "BLOCKED(spec 声明性能目标但无性能用例)"
        elif prec == "passed_with_risk":            # 无性能要求 + 精度带风险 → 仍走人工 CP
            overall, requires_human_cp = "PASSED_WITH_RISK", True
        else:
            overall = "PASS(无性能要求)"
    elif prec == "passed_with_risk":                     # 精度带风险(任务书宽于平台底线)、性能达标 → 人工 CP
        overall, requires_human_cp = "PASSED_WITH_RISK", True
    else:                                                # prec == pass 且性能达标
        overall = "PASS"
    state = _canonical_state(overall, ps)   # T6/T8：机读 canonical 状态（人读串仍 overall）
    exit_code = _exit_code(overall)         # T5：退出码枚举 0 干净 / 2 PASSED_WITH_RISK / 1 其余
    print(f"[总体] 精度={prec} · 风险 {ov['counts'].get('risk', 0)} · 性能达标 {ps.get('达标')}/{ps.get('perf_cases')}"
          f"({ps.get('status')}) · 门={'PASSED' if gate_passed else 'FAILED'} → {overall}"
          + (" · requires_human_cp（挂起转人工）" if requires_human_cp else ""))

    # 门控后的**验收裁决**（区别于 raw verdict.json=validator 精度判定）：上游产物即下游输入。
    # T5 三层 pass 明细 + risk 说明；T6/T8 机读 state + 挂起证据(human_cp) + GPU 标杆 provenance。
    human_cp = None
    if requires_human_cp:  # T6：机器只产证据挂 pending，真正人工 CP 留会话 agent 形态（codex H3/D4）
        ev_files = ([f"perf_sim_{spec['op'].lower()}.svg", "perf_report.json#simulation"]
                    if ps.get("status") == "exception" else [])
        human_cp = {"status": "pending", "evidence": ev_files,
                    "note": "机器产证据挂 pending；真正人工 CP 由会话 agent(可 AskUserQuestion)补"}
    acc = {"op": spec["op"], "overall": overall, "state": state, "exit_code": exit_code,
           "requires_human_cp": requires_human_cp, "repo_mode": mode,
           "gate": {"passed": gate_passed, "errors": gate_errs},
           "precision_verdict": prec, "perf_status": ps.get("status"),
           "three_layer": {"catlass_compare_na": verdict.get("catlass_compare_na", []),
                           "risk_cases": ov.get("risk", []),
                           "uncertain_cases": ov.get("uncertain", []),
                           "note": "放行只看 acceptance_precision_pass；risk=acceptance 过但 standard 不过 → 人工 CP"}}
    if human_cp is not None:
        acc["human_cp"] = human_cp
    if gpu_prov is not None:
        acc["gpu_baseline"] = gpu_prov
    _dump(acc, "acceptance.json")
    print(f"--- 产物在 {out_dir}/ ---")
    return {"verdict": verdict, "perf_report": report,
            "gate": {"passed": gate_passed, "errors": gate_errs}, "overall": overall,
            "state": state, "exit_code": exit_code, "requires_human_cp": requires_human_cp}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--mode", default="mock", choices=list(repo_adapter.MODES))
    ap.add_argument("--out", default="reports/_run")
    ap.add_argument("--defect", default=None)
    ap.add_argument("--perf-slow", default=None,
                    help="逗号分隔 cid：mock 下把这些用例基线造成略慢(本地演示小shape例外)")
    ap.add_argument("--gpu-baseline", default=None, help="外部 GPU 标杆 JSON（Task3 consumer 侧对比）")
    a = ap.parse_args()
    result = run(a.spec, a.mode, a.out, a.defect.split(",") if a.defect else None,
                 perf_slow=a.perf_slow.split(",") if a.perf_slow else None,
                 gpu_baseline=a.gpu_baseline)
    # CLI 退出码：0 干净 PASS / 2 PASSED_WITH_RISK(挂起转人工) / 1 其余（门未过/精度fail/性能未达/BLOCKED/needs_review）
    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
