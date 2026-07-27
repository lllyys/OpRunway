#!/usr/bin/env python3
"""从已落盘且已过三级门的验收证据生成干净 PASS acceptance.json。

本入口只处理最窄的 clean-pass 情形；风险、挂起、无性能任务或任何不完整状态均 fail-closed，
不得借此绕过 run_workflow 的完整状态机。
"""

import argparse
import json
import os
import tempfile

import run_workflow
import validate_acceptance_state as gate


class FinalizeError(RuntimeError):
    pass


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except Exception as ex:
        raise FinalizeError(f"缺/坏 JSON {path}: {type(ex).__name__}: {ex}") from ex
    if not isinstance(value, dict):
        raise FinalizeError(f"{path} 顶层须为对象")
    return value


def build_clean_acceptance(spec, evidence, verdict, perf_report, gate_errors):
    if gate_errors:
        raise FinalizeError(f"验收门未过：{gate_errors}")
    if evidence.get("evidence_grade") != "acceptance_candidate":
        raise FinalizeError("evidence_grade 不是 acceptance_candidate")
    if evidence.get("runner_source") != "user":
        raise FinalizeError("runner_source 不是 user")
    runner_form = spec.get("runner_form") or "cpp"
    if evidence.get("runner_form") != runner_form:
        raise FinalizeError(
            f"evidence.runner_form={evidence.get('runner_form')!r} 与 spec={runner_form!r} 不一致")

    overall = verdict.get("overall")
    if not isinstance(overall, dict) or overall.get("verdict") != "pass":
        raise FinalizeError("精度 verdict 不是干净 pass")
    counts = overall.get("counts")
    if not isinstance(counts, dict) or any(counts.get(k, 0) != 0 for k in (
            "fail", "uncertain", "risk", "gaps", "golden_blocked", "contract_problems")):
        raise FinalizeError(f"精度 counts 不是干净零风险状态：{counts!r}")

    summary = perf_report.get("summary")
    if not isinstance(summary, dict):
        raise FinalizeError("perf_report 缺 summary")
    perf_cases = summary.get("perf_cases")
    if (summary.get("status") != "ok" or summary.get("blocked") != 0
            or not isinstance(perf_cases, int) or isinstance(perf_cases, bool) or perf_cases <= 0
            or summary.get("达标") != perf_cases
            or summary.get("cases_scored") != perf_cases
            or summary.get("non_passing") != 0):
        raise FinalizeError(f"性能不是全部可比且全部达标：{summary!r}")

    clean = "PASS"
    state = run_workflow._canonical_state(clean, summary)
    exit_code = run_workflow._exit_code(clean)
    if state != "PASSED" or exit_code != 0:
        raise FinalizeError(f"状态映射异常：state={state!r}, exit_code={exit_code!r}")
    return {
        "op": spec.get("op"),
        "overall": clean,
        "state": state,
        "exit_code": exit_code,
        "requires_human_cp": False,
        "repo_mode": evidence.get("repo_mode"),
        "gate": {"passed": True, "errors": {}},
        "precision_verdict": "pass",
        "perf_status": "ok",
        "three_layer": {
            "catlass_compare_na": verdict.get("catlass_compare_na", []),
            "risk_cases": overall.get("risk", []),
            "uncertain_cases": overall.get("uncertain", []),
            "note": "放行只看 acceptance_precision_pass；risk=acceptance 过但 standard 不过 → 人工 CP",
        },
    }


def finalize_directory(out_dir):
    out_dir = os.path.realpath(out_dir)
    caseset = _load(os.path.join(out_dir, "caseset.json"))
    op = caseset.get("op")
    if not isinstance(op, str) or not op or "/" in op or "\\" in op:
        raise FinalizeError(f"caseset.op 非法：{op!r}")
    spec = _load(os.path.join(out_dir, "ops", op, f"{op}.spec.json"))
    evidence = _load(os.path.join(out_dir, "evidence.json"))
    verdict = _load(os.path.join(out_dir, "verdict.json"))
    perf_report = _load(os.path.join(out_dir, "perf_report.json"))

    gate_errors = {}
    for stage in ("task1", "task2", "task3"):
        errors = []
        gate._GATES[stage](out_dir, errors)
        if errors:
            gate_errors[stage] = errors
    acceptance = build_clean_acceptance(
        spec, evidence, verdict, perf_report, gate_errors)

    fd, tmp = tempfile.mkstemp(prefix=".acceptance.", suffix=".json", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(acceptance, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(out_dir, "acceptance.json"))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return acceptance


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="仅对已落盘、三级门全过的 clean-pass 验收证据生成 acceptance.json")
    parser.add_argument("--dir", required=True, help="run_workflow 产物目录")
    args = parser.parse_args(argv)
    try:
        acceptance = finalize_directory(args.dir)
    except FinalizeError as ex:
        print(f"[finalize_clean_acceptance] REFUSED: {ex}")
        return 1
    print(json.dumps(acceptance, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
