"""Task 3 · perf_compare — evidence + baseline.json -> perf_report.json。

对比双方 scope 必须一致（默认 kernel-only，ADR 0006·proposed·未 settle）；不一致 →
blocked_incomparable_timing_scope、不出结论。ratio = baseline_us / npu_us（>1 表示 NPU 更快）；
达标 = ratio ≥ spec.perf.target_ratio。缺证据/基线 → blocked；无性能用例 → 显式 no_perf_cases。

小 shape 例外（T6，任务书条款）：`小shape` tag 的性能用例，若 max(NPU,基线) < when_us_below 且
  |NPU-基线| ≤ abs_gap_us_within，达标**保持 False** + 打 `exception` 标 + 记 exception_detail；
  status=exception → 编排层映射 PASSED_WITH_RISK（挂人核仿真图，绝不偷偷置 True）。

GPU 标杆 consumer（T8）：`expect_source ∈ {gpu, gpu_external}` 且缺基线 → blocked_wait_gpu_benchmark
  （正规挂起、非 fail、baseline=None 不崩）；消费的基线带 policy_risk 且达标 → summary.risk。

`report['simulation']` **只此处生成**（唯一事实源）；perf_sim_plot 只渲染、不二次推断。
v0 提供 mock_baseline；真机/外部给基线时替换。
"""
import json, math, re, sys

_US_RE = re.compile(r"<\s*(\d+(?:\.\d+)?)\s*us")
_GAP_RE = re.compile(r"差\s*(\d+(?:\.\d+)?)\s*us")


def _finite_pos(x):
    """有限正数（拒 bool/None/NaN/inf/≤0）——数值合法性校验（codex M4）。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def mock_baseline(spec, evidence, factor=1.08, slow_cases=None):
    """v0 占位：TBE 基线 us = NPU mock us × factor（>1 → 基线更慢）。
    slow_cases 内 cid → base=round(npu*0.8,3)、env 标 (inj-slow)：造「NPU 略慢于 TBE 但小差」以本地
    触发小 shape 例外通道。仅对已测到 us 的用例造基线；us=None（如 new_example 未接 msprof）跳过。
    ⚠ mock 注入仅供本地演示逻辑，产物明标 (inj-slow)，禁作真实人工 CP 依据（codex M11）。"""
    slow = set(slow_cases or [])
    per = []
    for e in evidence["evidence"]:
        us = e["perf"].get("us")
        if us is None:
            continue
        if e["case_id"] in slow:
            per.append({"case_id": e["case_id"], "us": round(us * 0.8, 3), "env": "mock-TBE(inj-slow)"})
        else:
            per.append({"case_id": e["case_id"], "us": round(us * factor, 3), "env": "mock-TBE"})
    return {"source": spec.get("perf", {}).get("baseline"), "scope": "kernel_only", "per_case": per}


def _parse_small_shape_exception(spec):
    """spec.perf.small_shape_exception → (dict|None, note|None)，绝不硬编码 10/3。
    dict → 取 when_us_below/abs_gap_us_within（须有限正数）+ requires?；
    str(legacy) → 正则抓 `<Nus` 阈 与 `差Nus` 容差，两者都成才构造；
    缺失/解析不出/非法 → (None, note)（例外禁用）。"""
    sse = (spec.get("perf") or {}).get("small_shape_exception")
    if sse is None:
        return None, None
    if isinstance(sse, dict):
        wb, ag = sse.get("when_us_below"), sse.get("abs_gap_us_within")
        if _finite_pos(wb) and _finite_pos(ag):
            return {"when_us_below": float(wb), "abs_gap_us_within": float(ag),
                    "requires": sse.get("requires")}, None
        return None, "small_shape_exception 对象缺/非法 when_us_below/abs_gap_us_within（例外禁用）"
    if isinstance(sse, str):
        mw, mg = _US_RE.search(sse), _GAP_RE.search(sse)
        if mw and mg:
            return {"when_us_below": float(mw.group(1)), "abs_gap_us_within": float(mg.group(1)),
                    "requires": "simulation_plot+analysis"}, None
        return None, f"small_shape_exception 字符串未解析出阈值/容差（例外禁用）: {sse!r}"
    return None, "small_shape_exception 类型非法（须 object 或 string；例外禁用）"


def _numel(case):
    if not isinstance(case, dict):
        return None
    ins = case.get("inputs")
    if not isinstance(ins, list) or not ins or not isinstance(ins[0], dict):
        return None
    shp = ins[0].get("shape")
    if not isinstance(shp, list):
        return None
    n = 1
    for dcol in shp:
        if not isinstance(dcol, int) or isinstance(dcol, bool):
            return None
        n *= dcol
    return n


def _lin_slope(xs, ys):
    """最小二乘斜率（需 ≥2 个不同 x）；否则 None。标注为『模型/推断』用（非实测）。"""
    n = len(xs)
    if n < 2 or len(set(xs)) < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return None
    return round(sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom, 6)


def _build_simulation(exc, exc_rows, case_by_id, op):
    """唯一事实源：据例外行组装 report['simulation']（perf_sim_plot 只消费此块，不二次推断）。"""
    points = []
    for r in exc_rows:
        cid = r["case_id"]
        d = r["exception_detail"]
        points.append({"case_id": cid, "numel": _numel(case_by_id.get(cid)),
                       "npu_us": d["npu_us"], "baseline_us": d["baseline_us"],
                       "gap": d["gap"], "within": d["within"], "conclusion": d["conclusion"]})
    sim = {"op": op, "when_us_below": exc["when_us_below"],
           "abs_gap_us_within": exc["abs_gap_us_within"], "points": points,
           "overall": f"{len(points)} 个小shape性能用例落在 <{exc['when_us_below']}us 且 |NPU-基线|"
                      f"≤{exc['abs_gap_us_within']}us 容差内 → 判与内置基线一致/更优（达标记 False，挂人工 CP）"}
    fit_pts = [p for p in points if isinstance(p.get("numel"), int)]
    if len(fit_pts) >= 2 and len({p["numel"] for p in fit_pts}) >= 2:
        xs = [p["numel"] for p in fit_pts]
        sim["fit"] = {"npu_us_per_numel": _lin_slope(xs, [p["npu_us"] for p in fit_pts]),
                      "baseline_us_per_numel": _lin_slope(xs, [p["baseline_us"] for p in fit_pts]),
                      "note": "线性拟合斜率（模型/推断，非实测）"}
    return sim


def _no_perf_cases(spec, src, tgt, perf_spec, extra_notes=None):
    note = "caseset 无性能用例"
    if perf_spec.get("baseline"):  # spec 声明了性能基线却无用例 → 疑 gen_cases 用例缺陷
        note += "；但 spec 声明了性能基线（疑用例缺陷，非「无需性能验收」）"
    notes = [note] + list(extra_notes or [])
    return {"op": spec["op"], "baseline_source": src, "target_ratio": tgt,
            "per_case": [], "notes": notes,
            "summary": {"perf_cases": 0, "达标": 0, "blocked": 0, "status": "no_perf_cases"}}


def perf_compare(spec, caseset, evidence, baseline, expect_source=None):
    perf_ids = sorted({c["id"] for c in caseset["cases"] if "性能" in c.get("dims", [])})
    perf_spec = spec.get("perf", {})
    tgt = perf_spec.get("target_ratio", 0.95)
    case_by_id = {c["id"]: c for c in caseset["cases"] if isinstance(c, dict) and c.get("id")}
    exc, exc_note = _parse_small_shape_exception(spec)

    # 缺基线（T8）：期待外部 GPU 标杆但没给 → 正规挂起，不静默 mock、不判 fail、baseline=None 不崩。
    if baseline is None:
        src = expect_source or perf_spec.get("baseline")
        if not perf_ids:
            return _no_perf_cases(spec, src, tgt, perf_spec, ["缺外部 GPU 标杆但无性能用例"])
        ev = {e["case_id"]: e for e in evidence["evidence"]}
        rows = []
        for cid in perf_ids:
            e = ev.get(cid)
            rows.append({"case_id": cid,
                         "npu_us": (e["perf"].get("us") if e else None),
                         "npu_scope": (e["perf"].get("scope") if e else None),
                         "达标": False, "blocked": False, "note": "await GPU baseline"})
        notes = ["缺外部 GPU 标杆 → 正规挂起（blocked_wait_gpu_benchmark，非 fail）"]
        if exc_note:
            notes.append(exc_note)
        return {"op": spec["op"], "baseline_source": src, "target_ratio": tgt,
                "per_case": rows, "notes": notes,
                "summary": {"perf_cases": len(rows), "达标": 0, "blocked": 0,
                            "status": "blocked_wait_gpu_benchmark"}}

    src = baseline.get("source")
    if not perf_ids:
        return _no_perf_cases(spec, src, tgt, perf_spec, [exc_note] if exc_note else None)

    ev_list, bl_list = evidence["evidence"], baseline["per_case"]
    notes = []
    if exc_note:
        notes.append(exc_note)
    dup = False
    if len({e["case_id"] for e in ev_list}) != len(ev_list):
        notes.append("evidence 有重复 case_id")
        dup = True
    if len({b["case_id"] for b in bl_list}) != len(bl_list):
        notes.append("baseline 有重复 case_id")
        dup = True
    ev = {e["case_id"]: e for e in ev_list}
    bl = {b["case_id"]: b for b in bl_list}
    bscope = baseline.get("scope")

    rows = []
    scope_mismatch = 0
    risk_flags = set()
    for cid in perf_ids:
        e, b = ev.get(cid), bl.get(cid)
        if not e or not b:
            miss = ("evidence " if not e else "") + ("baseline" if not b else "")
            rows.append({"case_id": cid, "达标": False, "blocked": True, "note": f"缺 {miss.strip()}"})
            continue
        if e["perf"]["scope"] != bscope:
            scope_mismatch += 1
            rows.append({"case_id": cid, "达标": False, "blocked": True,
                         "note": f"BLOCKED_INCOMPARABLE_SCOPE {e['perf']['scope']} vs {bscope}"})
            continue
        npu, base = e["perf"].get("us"), b.get("us")
        if not _finite_pos(npu) or not _finite_pos(base):  # 0/负/NaN/inf/None → blocked（不进例外/不算 ratio）
            rows.append({"case_id": cid, "达标": False, "blocked": True,
                         "note": f"非法计时数值 npu_us={npu} baseline_us={base}（须有限正数）"})
            continue
        ratio = round(base / npu, 3)
        met = ratio >= tgt
        row = {"case_id": cid, "scope": bscope, "npu_us": npu,
               "baseline": {"source": src, "us": base}, "ratio": ratio, "达标": met}
        if met and b.get("policy_risk"):  # T8 M6：消费 sub-policy 基线且达标 → 记风险（不允许干净 PASS）
            row["policy_risk"] = b["policy_risk"]
            risk_flags.add("sub_policy_timing")
        if not met and exc is not None:  # 小 shape 例外：仅对未达标行判资格
            tags = (case_by_id.get(cid) or {}).get("tags") or []
            gap = round(abs(npu - base), 6)
            if ("小shape" in tags and max(npu, base) < exc["when_us_below"]
                    and gap <= exc["abs_gap_us_within"]):
                row["exception"] = "small_shape"
                row["exception_detail"] = {
                    "npu_us": npu, "baseline_us": base, "gap": gap,
                    "within": exc["abs_gap_us_within"], "when_us_below": exc["when_us_below"],
                    "conclusion": f"小shape场景 max(NPU,基线)={max(npu, base)}us <{exc['when_us_below']}us、"
                                  f"差 {gap}us ≤ 容差 {exc['abs_gap_us_within']}us → 与内置基线一致/更优"
                                  f"（达标记 False，挂人核）"}
        rows.append(row)

    passed = sum(1 for r in rows if r.get("达标"))
    blocked = sum(1 for r in rows if r.get("blocked"))
    exc_rows = [r for r in rows if r.get("exception")]
    genuine_fail = sum(1 for r in rows
                       if not r.get("达标") and not r.get("blocked") and not r.get("exception"))
    # status 优先级：incomparable > blocked(其它) > fail(genuine) > exception > ok
    if scope_mismatch:
        status = "blocked_incomparable_timing_scope"
    elif blocked or dup:
        status = "blocked"
    elif genuine_fail:
        status = "fail"
    elif exc_rows:
        status = "exception"
    else:
        status = "ok"

    report = {"op": spec["op"], "baseline_source": src, "target_ratio": tgt,
              "per_case": rows, "notes": notes,
              "summary": {"perf_cases": len(rows), "达标": passed, "blocked": blocked,
                          "status": status}}
    if risk_flags:
        report["summary"]["risk"] = sorted(risk_flags)
    if exc_rows:  # 唯一事实源：仅在有例外行时产 simulation
        report["simulation"] = _build_simulation(exc, exc_rows, case_by_id, spec["op"])
    return report


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
