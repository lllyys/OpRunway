"""Task 2 · validator — spec + caseset + evidence.json -> verdict.json（确定性裁决）。

ADR 0007：裁决只从这里出。职责：
1) 契约校验——evidence 必须与 caseset **一一对应**（无缺、无多、无重复），否则整体 fail（防空 evidence 假通过）。
2) 口径以 **spec 为权威**——verify_mode、precision.threshold 三处（spec/caseset.expected/evidence）必须一致，否则 fail（防 adapter 放宽阈值假通过）。
3) 按 case dims 只裁相关维度；性能维度交 Task 3 perf_compare（此处 na）。
UNCERTAIN 不阻塞出产物、但 overall 记 needs_review、不可直接 PASS。
"""
import json, sys


def _spec_threshold(spec):
    return spec["precision"].get("threshold", 0)


def _judge_precision(verify_mode, prec, thr):
    value, metric = prec["value"], prec.get("metric")
    if verify_mode == "exact":
        if metric != "exact_mismatch":
            return "fail", f"metric={metric} 与 verify_mode=exact 不符"
        return ("pass" if value <= thr else "fail"), f"exact mismatch={value} ≤ {thr}"
    if verify_mode == "behavioral":
        return "na", "行为型：无数值 golden，精度维度 na"
    if verify_mode != "numerical":  # 未知 verify_mode 不静默当 numerical → 显式 fail
        return "fail", f"未知 verify_mode={verify_mode}（仅 exact/numerical/behavioral）"
    if metric != "max_rel_err":
        return "fail", f"metric={metric} 与 verify_mode=numerical 不符"
    if not thr:
        return "uncertain", f"max_rel_err={value}，spec 未给数值阈值"
    return ("pass" if value < thr else "fail"), f"max_rel_err={value} vs thr={thr}"


def validate(spec, caseset, evidence):
    vm = spec["verify_mode"]
    thr = _spec_threshold(spec)
    case_ids = [c["id"] for c in caseset["cases"]]
    ev_list = evidence["evidence"]
    ev_ids = [e["case_id"] for e in ev_list]

    # 1) 契约校验：caseset ↔ evidence 一一对应 + verify_mode 合法
    problems = []
    if vm not in ("exact", "numerical", "behavioral"):
        problems.append(f"spec.verify_mode={vm!r} 非法（仅 exact/numerical/behavioral）")
    if len(case_ids) != len(set(case_ids)):
        problems.append("caseset 有重复 case_id")
    if len(ev_ids) != len(set(ev_ids)):
        problems.append("evidence 有重复 case_id")
    missing = set(case_ids) - set(ev_ids)
    extra = set(ev_ids) - set(case_ids)
    if missing:
        problems.append(f"evidence 缺 case: {sorted(missing)}")
    if extra:
        problems.append(f"evidence 有多余 case: {sorted(extra)}")

    ev_by_id = {e["case_id"]: e for e in ev_list}
    per = []
    for c in caseset["cases"]:
        cid, dims, exp = c["id"], c.get("dims", []), c.get("expected", {})
        row = {"case_id": cid, "功能": "na", "精度": "na", "性能": "na",
               "判据": "", "evidence_ref": cid}
        e = ev_by_id.get(cid)
        if e is None:
            row.update(功能="fail", 判据="evidence 缺此 case")
            per.append(row); continue
        # 2) 口径一致性（spec 权威）
        if exp.get("verify_mode") != vm:
            row.update(功能="fail", 判据=f"case.verify_mode={exp.get('verify_mode')} ≠ spec {vm}")
            per.append(row); continue
        if exp.get("threshold") != thr or e["precision"].get("threshold") != thr:
            row.update(功能="fail",
                       判据=f"threshold 不一致 spec={thr}/case={exp.get('threshold')}/ev={e['precision'].get('threshold')}")
            per.append(row); continue
        # 3) 按 dims 裁维度
        why = []
        if "功能" in dims:
            row["功能"] = "pass" if e.get("status") == "ok" else "fail"
        if "精度" in dims:
            row["精度"], w = _judge_precision(vm, e["precision"], thr); why.append(w)
        row["判据"] = "；".join(why) if why else f"dims={dims}（性能交 perf_compare）"
        per.append(row)

    fails = [p for p in per if p["功能"] == "fail" or p["精度"] == "fail"]
    uncertain = [p["case_id"] for p in per if p["精度"] == "uncertain"]
    if problems or fails:
        overall = "fail"
    elif uncertain:
        overall = "needs_review"
    else:
        overall = "pass"
    return {"op": spec["op"], "verify_mode": vm, "contract_problems": problems,
            "per_case": per,
            "overall": {"verdict": overall, "uncertain": uncertain,
                        "counts": {"total": len(per), "fail": len(fails),
                                   "uncertain": len(uncertain),
                                   "contract_problems": len(problems)}}}


def main(argv):
    spec = json.load(open(argv[0], encoding="utf-8"))
    caseset = json.load(open(argv[1], encoding="utf-8"))
    evidence = json.load(open(argv[2], encoding="utf-8"))
    verdict = validate(spec, caseset, evidence)
    json.dump(verdict, open(argv[3], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    o = verdict["overall"]
    print(f"[validator] overall={o['verdict']} {o['counts']} -> {argv[3]}")


if __name__ == "__main__":
    main(sys.argv[1:])
