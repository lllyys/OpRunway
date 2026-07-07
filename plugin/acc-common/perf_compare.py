"""Task 3 · perf_compare — evidence + baseline.json -> perf_report.json。

对比双方 scope 必须一致（默认 kernel-only，ADR 0006）；不一致 → blocked、不出结论。
ratio = baseline_us / npu_us（>1 表示 NPU 更快）；达标 = ratio ≥ spec.perf.target_ratio。
缺证据/基线 → blocked（不静默跳过）；无性能用例 → 显式 no_perf_cases（区别于用例缺陷）。
v0 提供 mock_baseline；真机/外部给基线时替换。
"""
import json, sys


def mock_baseline(spec, evidence, factor=1.08):
    """v0 占位：TBE 基线 us = NPU mock us × factor（>1 → 基线更慢）。
    仅对已测到 us 的用例造基线；us=None（如 new_example 未接 msprof）跳过 → perf_compare 该用例 blocked。"""
    per = [{"case_id": e["case_id"], "us": round(e["perf"]["us"] * factor, 3), "env": "mock-TBE"}
           for e in evidence["evidence"] if e["perf"].get("us") is not None]
    return {"source": spec.get("perf", {}).get("baseline"), "scope": "kernel_only", "per_case": per}


def perf_compare(spec, caseset, evidence, baseline):
    perf_ids = sorted({c["id"] for c in caseset["cases"] if "性能" in c.get("dims", [])})
    perf_spec = spec.get("perf", {})
    tgt = perf_spec.get("target_ratio", 0.95)
    src = baseline.get("source")
    if not perf_ids:
        note = "caseset 无性能用例"
        if perf_spec.get("baseline"):  # spec 声明了性能基线却无用例 → 疑 gen_cases 用例缺陷
            note += "；但 spec 声明了性能基线（疑用例缺陷，非「无需性能验收」）"
        return {"op": spec["op"], "baseline_source": src, "target_ratio": tgt,
                "per_case": [], "notes": [note],
                "summary": {"perf_cases": 0, "达标": 0, "blocked": 0, "status": "no_perf_cases"}}

    ev_list, bl_list = evidence["evidence"], baseline["per_case"]
    notes = []
    if len({e["case_id"] for e in ev_list}) != len(ev_list):
        notes.append("evidence 有重复 case_id")
    if len({b["case_id"] for b in bl_list}) != len(bl_list):
        notes.append("baseline 有重复 case_id")
    ev = {e["case_id"]: e for e in ev_list}
    bl = {b["case_id"]: b for b in bl_list}
    bscope = baseline.get("scope")

    rows = []
    for cid in perf_ids:
        e, b = ev.get(cid), bl.get(cid)
        if not e or not b:
            miss = ("evidence " if not e else "") + ("baseline" if not b else "")
            rows.append({"case_id": cid, "达标": False, "blocked": True, "note": f"缺 {miss.strip()}"})
            continue
        if e["perf"]["scope"] != bscope:
            rows.append({"case_id": cid, "达标": False, "blocked": True,
                         "note": f"BLOCKED_INCOMPARABLE_SCOPE {e['perf']['scope']} vs {bscope}"})
            continue
        npu, base = e["perf"]["us"], b["us"]
        if not npu:
            rows.append({"case_id": cid, "达标": False, "blocked": True, "note": "npu_us=0"})
            continue
        ratio = round(base / npu, 3)
        rows.append({"case_id": cid, "scope": bscope, "npu_us": npu,
                     "baseline": {"source": src, "us": base},
                     "ratio": ratio, "达标": ratio >= tgt})
    passed = sum(1 for r in rows if r.get("达标"))
    blocked = sum(1 for r in rows if r.get("blocked"))
    if blocked or notes:            # 重复 evidence/baseline(notes) 或缺证据/scope 不符 → blocked
        status = "blocked"
    elif passed < len(rows):        # 有性能用例未达标 → fail（不可当 ok）
        status = "fail"
    else:
        status = "ok"
    return {"op": spec["op"], "baseline_source": src, "target_ratio": tgt,
            "per_case": rows, "notes": notes,
            "summary": {"perf_cases": len(rows), "达标": passed, "blocked": blocked,
                        "status": status}}


def main(argv):
    spec = json.load(open(argv[0], encoding="utf-8"))
    caseset = json.load(open(argv[1], encoding="utf-8"))
    evidence = json.load(open(argv[2], encoding="utf-8"))
    baseline = (json.load(open(argv[3], encoding="utf-8")) if len(argv) > 3 and argv[3]
                else mock_baseline(spec, evidence))
    report = perf_compare(spec, caseset, evidence, baseline)
    out = argv[4] if len(argv) > 4 else "perf_report.json"
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[perf_compare] {report['summary']} -> {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
