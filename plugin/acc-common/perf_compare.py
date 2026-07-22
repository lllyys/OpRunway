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

⚠ **mock 基线 = 非验收证据**（C5）：`mock_baseline` 造的是「NPU mock us × 1.08」这种编出来的数，
拿它比出来的「达标」不构成任何性能结论。故凡消费 `baseline.mock=True` 的报告，一律打
`evidence_grade="development"` + `acceptance_note="NON-ACCEPTANCE (mock evidence)…"`
（字段名与措辞照 `catlass_adapter.run_catlass_mock` 的既有口径，不另发明），让「假基线的达标」
在产物里一眼可辨、不可能被当成真达标。真基线（`_real_baseline.json`）/ 外部 GPU 标杆**一字不受影响**。
"""
import argparse, json, math, re, sys

_US_RE = re.compile(r"<\s*(\d+(?:\.\d+)?)\s*us")
_GAP_RE = re.compile(r"差\s*(\d+(?:\.\d+)?)\s*us")
_VALID_SCOPES = {"kernel_only", "device_e2e_no_h2d_d2h", "host_e2e_with_h2d_d2h"}
# 缺/废基线的挂起态描述（pc-4/gb-9：区分「缺标杆」「口径不可比」「标杆被判废」）。
_BLOCKED_NOTE = {
    "blocked_wait_gpu_benchmark": "缺外部 GPU 标杆 → 正规挂起（blocked_wait_gpu_benchmark，非 fail）",
    "blocked_incomparable_timing_scope":
        "GPU 标杆内部计时口径不一致（混合 scope）→ 不可比挂起（blocked_incomparable_timing_scope）",
    "blocked_gpu_baseline_invalid":
        "GPU 标杆有硬错被判废 → 阻断（blocked_gpu_baseline_invalid，非「缺标杆」）",
}
# —— 非验收证据的统一口径（C5）：字段名与措辞**照 catlass_adapter.run_catlass_mock 已有的那份**，别另发明。
_DEV_GRADE = "development"
_NON_ACCEPTANCE_NOTE = ("NON-ACCEPTANCE (mock evidence)：性能基线是 mock 编的假数（NPU mock us × 常数），"
                        "本报告只证管路接通，**不构成性能验收结论**")
_MOCK_BASELINE_NOTE = "⚠ 使用 mock 基线（本地演示逻辑、非真实基线，不可当真通过验收）"


def _mark_non_acceptance(report, baseline):
    """消费 mock 基线的报告 → 打 NON-ACCEPTANCE 戳（幂等）。真基线 / 外部 GPU 标杆一律不动。

    判据只有一条：`baseline.mock is truthy`——该标只由 `mock_baseline()` 自己写，真机 `_real_baseline.json`
    与 `gpu_baseline.parse_gpu_baseline` 都不写，故真机通路的报告**一个字节都不变**（fail-closed 方向正确：
    漏标只会发生在「有人手搓一份不带 mock 标的假基线」，那属证据伪造、不是本函数的防线）。
    """
    if not (isinstance(baseline, dict) and baseline.get("mock")):
        return report
    report["evidence_grade"] = _DEV_GRADE
    report["acceptance_note"] = _NON_ACCEPTANCE_NOTE
    notes = report.setdefault("notes", [])
    if _MOCK_BASELINE_NOTE not in notes:
        notes.append(_MOCK_BASELINE_NOTE)
    summary = report.get("summary")
    if isinstance(summary, dict):
        summary["baseline_mock"] = True      # 供门/报告醒目「不可当真通过」（与主流程口径一致、幂等）
    return report


def _finite_pos(x):
    """有限正数（拒 bool/None/NaN/inf/≤0）——数值合法性校验（codex M4）。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def _resolve_target_ratio(perf_spec):
    """pc-3：target_ratio 严格化，返回 (tgt|None, err|None)。
    缺失且声明了基线 → invalid（拒静默套 0.95 放行）；缺失且未声明基线 → 用默认 0.95（无性能验收目标）；
    存在但非有限正数（0/负/bool/NaN/inf/字符串）→ invalid。"""
    if not isinstance(perf_spec, dict) or "target_ratio" not in perf_spec:
        if isinstance(perf_spec, dict) and perf_spec.get("baseline"):
            return None, "spec 声明了性能基线却缺 target_ratio（拒静默套 0.95）→ invalid_config"
        return 0.95, None
    tgt = perf_spec["target_ratio"]
    if not _finite_pos(tgt):
        return None, f"target_ratio={tgt!r} 非法（须有限正数，拒 0/负/bool/NaN/inf/字符串）→ invalid_config"
    return float(tgt), None


def _invalid(op, notes):
    """pc-7：坏输入 → 结构化 invalid report（参照 validator.py，绝不下标崩溃）。"""
    return {"op": op if isinstance(op, str) and op else "?", "baseline_source": None,
            "target_ratio": None, "per_case": [], "notes": list(notes),
            "summary": {"perf_cases": 0, "达标": 0, "blocked": 0, "status": "invalid"}}


def _precheck(spec, caseset, evidence, baseline):
    """pc-7：入口轻量 schema 校验——容器坏 → 结构化 invalid（不崩）；条目级坏在循环里降级 blocked。"""
    op = spec.get("op") if isinstance(spec, dict) else None
    if not isinstance(op, str) or not op:
        return _invalid("?", ["spec 缺/坏 op（须非空字符串）"])
    if not isinstance(caseset, dict) or not isinstance(caseset.get("cases"), list):
        return _invalid(op, ["caseset 缺/坏 cases（须 list）"])
    if not isinstance(evidence, dict) or not isinstance(evidence.get("evidence"), list):
        return _invalid(op, ["evidence 缺/坏 evidence（须 list）"])
    if baseline is not None and (not isinstance(baseline, dict)
                                 or not isinstance(baseline.get("per_case"), list)):
        return _invalid(op, ["baseline 缺/坏 per_case（须 list）"])
    return None


def mock_baseline(spec, evidence, factor=1.08, slow_cases=None):
    """v0 占位：TBE 基线 us = NPU mock us × factor（>1 → 基线更慢）。
    slow_cases 内 cid → base=round(npu*0.8,3)、env 标 (inj-slow)：造「NPU 略慢于 TBE 但小差」以本地
    触发小 shape 例外通道。仅对已测到 us 的用例造基线；us=None（如 new_example 未接 msprof）跳过。
    ⚠ mock 注入仅供本地演示逻辑，产物明标 (inj-slow)，禁作真实人工 CP 依据（codex M11）。

    C5：返回的基线自身即带 `evidence_grade=development` + `acceptance_note=NON-ACCEPTANCE (mock evidence)`
    ——落盘成 baseline.json 后**一眼可辨是假基线**；`mock: True` 则驱动 `_mark_non_acceptance` 把同一枚戳
    传导到 perf_report。本函数保留（测试与本地演示要用），但它产的东西**永远不是验收证据**。"""
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
    # pc-1：mock 基线明标 mock=True，供 perf_compare 打「不可当真通过」，防纯 NPU 数据造出「通过」。
    return {"source": spec.get("perf", {}).get("baseline"), "scope": "kernel_only",
            "per_case": per, "mock": True,
            "evidence_grade": _DEV_GRADE, "acceptance_note": _NON_ACCEPTANCE_NOTE}


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


# §perf 同输入 + trivial-met（用户 2026-07-15 + 评审 #2）：perf 测/比**全部相同输入**，但退化/微小 case
# （numel < 阈值）kernel 时间纯启动开销、perf 无意义 → 标 trivial-met（达标、不失败、不 blocked、不需基线），
# perf 达标实际由代表性大 shape 主导。阈值**固定 4096**——与门 `_GATE_TRIVIAL_MAX_NUMEL` 同口径、**不 spec 覆盖**
# （codex 门 #3：可覆盖会致 compare↔gate 阈值不一致 + 字符串值 TypeError 崩；固定值杜绝两患）。
_DEFAULT_TRIVIAL_NUMEL = 4096


def _broadcast_shape(shapes):
    """numpy 广播规则的纯 py 实现：右对齐，每维 1 可广播到 N、冲突→None；维须**非 bool 非负 int**否则 None。"""
    out_rev, maxlen = [], max((len(s) for s in shapes), default=0)
    for i in range(maxlen):
        dim = 1
        for s in shapes:
            if i >= len(s):
                continue
            d = s[len(s) - 1 - i]
            if not isinstance(d, int) or isinstance(d, bool) or d < 0:
                return None
            if d == 1:
                continue
            if dim == 1:
                dim = d
            elif dim != d:
                return None
        out_rev.append(dim)
    return list(reversed(out_rev))


def _case_numel(case):
    """case 输出元素数=**全部输入 broadcast 后**的输出 numel（codex #1：只取 inputs[0] 会让广播用例
    (1,)+(8192,) 被误判 numel=1 蒙混 trivial）；坏/不可广播 → None（不当退化、走正常判定，fail-closed）。"""
    if not isinstance(case, dict):
        return None
    inp = case.get("inputs") or []
    shapes = []
    for it in inp:
        if not isinstance(it, dict) or not isinstance(it.get("shape"), list):
            return None
        shapes.append(it["shape"])
    if not shapes:
        return None
    out = _broadcast_shape(shapes)
    if out is None:
        return None
    n = 1
    for d in out:
        n *= d
    return n


def perf_compare(spec, caseset, evidence, baseline, expect_source=None, baseline_blocked_status=None):
    # pc-7：入口轻量 schema 校验——坏输入收敛为结构化 invalid，绝不下标崩溃。
    # C5：**每一条 return 都过 `_mark_non_acceptance`**——mock 基线的报告无论走哪个出口（invalid / no_perf_cases /
    #     invalid_config / 正常判定）都得带 NON-ACCEPTANCE 戳，漏一个出口就等于留一条「假基线报告看起来像真的」的缝。
    bad = _precheck(spec, caseset, evidence, baseline)
    if bad is not None:
        return _mark_non_acceptance(bad, baseline)
    op = spec["op"]
    cases = caseset["cases"]
    perf_ids = sorted({c["id"] for c in cases
                       if isinstance(c, dict) and c.get("id") and "性能" in (c.get("dims") or [])})
    perf_spec = spec.get("perf") or {}
    case_by_id = {c["id"]: c for c in cases if isinstance(c, dict) and c.get("id")}
    exc, exc_note = _parse_small_shape_exception(spec)
    trivial_numel = _DEFAULT_TRIVIAL_NUMEL   # 固定，与门同口径（不 spec 覆盖，codex #3）
    # pc-3：target_ratio 严格化（非法/声明基线却缺 → invalid_config；绝不静默套 0.95 放行）。
    tgt, tgt_err = _resolve_target_ratio(perf_spec)

    # 缺/废基线（T8/gb-9）：期待外部 GPU 标杆但没给（或标杆被判废）→ 正规挂起；
    # 不静默 mock、不判 fail、baseline=None 不崩；据 baseline_blocked_status 落**正确**挂起码
    # （别把「有硬错的 baseline=None」等同「缺标杆」——gb-9）。
    if baseline is None:
        src = expect_source or perf_spec.get("baseline")
        status = baseline_blocked_status or "blocked_wait_gpu_benchmark"
        top_note = _BLOCKED_NOTE.get(status, f"缺/废基线 → 挂起（{status}）")
        if not perf_ids:
            return _no_perf_cases(spec, src, tgt, perf_spec, [top_note])
        ev = {e.get("case_id"): e for e in evidence["evidence"] if isinstance(e, dict) and e.get("case_id")}
        rows = []
        for cid in perf_ids:
            perf = (ev.get(cid) or {}).get("perf")
            rows.append({"case_id": cid,
                         "npu_us": (perf.get("us") if isinstance(perf, dict) else None),
                         "npu_scope": (perf.get("scope") if isinstance(perf, dict) else None),
                         "达标": False, "blocked": False, "note": top_note})
        notes = [top_note]
        if exc_note:
            notes.append(exc_note)
        return {"op": op, "baseline_source": src, "target_ratio": tgt,
                "per_case": rows, "notes": notes,
                "summary": {"perf_cases": len(rows), "达标": 0, "blocked": 0, "status": status}}

    src = baseline.get("source")
    if not perf_ids:
        return _mark_non_acceptance(
            _no_perf_cases(spec, src, tgt, perf_spec, [exc_note] if exc_note else None), baseline)

    # pc-3：有性能用例要判、但 target_ratio 非法/缺 → invalid_config；不进 ratio、绝不全达标。
    if tgt is None:
        rows = [{"case_id": cid, "达标": False, "blocked": True, "note": tgt_err} for cid in perf_ids]
        notes = [tgt_err] + ([exc_note] if exc_note else [])
        return _mark_non_acceptance(
            {"op": op, "baseline_source": src, "target_ratio": None,
             "per_case": rows, "notes": notes,
             "summary": {"perf_cases": len(rows), "达标": 0, "blocked": len(rows),
                         "status": "invalid_config"}}, baseline)

    ev_list, bl_list = evidence["evidence"], baseline["per_case"]
    notes = []
    if baseline.get("mock"):  # pc-1：mock 基线明标——防「纯 NPU 数据造出通过报告」被当真验收
        notes.append(_MOCK_BASELINE_NOTE)
    if exc_note:
        notes.append(exc_note)
    dup = False
    ev_ids = [e.get("case_id") for e in ev_list if isinstance(e, dict)]
    bl_ids = [b.get("case_id") for b in bl_list if isinstance(b, dict)]
    if len(set(ev_ids)) != len(ev_ids):
        notes.append("evidence 有重复 case_id")
        dup = True
    if len(set(bl_ids)) != len(bl_ids):
        notes.append("baseline 有重复 case_id")
        dup = True
    ev = {e.get("case_id"): e for e in ev_list if isinstance(e, dict) and e.get("case_id")}
    bl = {b.get("case_id"): b for b in bl_list if isinstance(b, dict) and b.get("case_id")}
    bscope = baseline.get("scope")

    rows = []
    scope_mismatch = 0
    risk_flags = set()
    for cid in perf_ids:
        # §trivial-met：退化/微小 case（numel<阈值）无意义 → 达标、不需基线/scope/us（评审 #2、用户 Q4）。
        # 放循环首：连缺基线（如 GPU 标杆只给大 shape）也不 blocked，perf 达标由大 shape 主导。
        cnumel = _case_numel(case_by_id.get(cid))
        if isinstance(cnumel, int) and 0 < cnumel < trivial_numel:
            rows.append({"case_id": cid, "达标": True, "trivial": True, "numel": cnumel,
                         "note": f"trivial-met（numel={cnumel}<{trivial_numel}，退化 case perf 无意义免测）"})
            continue
        e, b = ev.get(cid), bl.get(cid)
        if not e or not b:
            miss = ("evidence " if not e else "") + ("baseline" if not b else "")
            rows.append({"case_id": cid, "达标": False, "blocked": True, "note": f"缺 {miss.strip()}"})
            continue
        eperf = e.get("perf") if isinstance(e, dict) else None
        escope = eperf.get("scope") if isinstance(eperf, dict) else None
        # pc-4：任一侧 scope 缺失/None/非合法枚举 或 双边不一致 → 不可比（强制 scope 非空，None!=None 不再放行）。
        if escope not in _VALID_SCOPES or bscope not in _VALID_SCOPES or escope != bscope:
            scope_mismatch += 1
            rows.append({"case_id": cid, "达标": False, "blocked": True,
                         "note": f"BLOCKED_INCOMPARABLE_SCOPE npu={escope!r} vs baseline={bscope!r}"})
            continue
        npu = eperf.get("us") if isinstance(eperf, dict) else None
        base = b.get("us")
        if not _finite_pos(npu) or not _finite_pos(base):  # 0/负/NaN/inf/None → blocked（不进例外/不算 ratio）
            rows.append({"case_id": cid, "达标": False, "blocked": True,
                         "note": f"非法计时数值 npu_us={npu} baseline_us={base}（须有限正数）"})
            continue
        raw = base / npu           # pc-2：先算原始比再比阈——round 只用于展示，不得把 <tgt 的比值救活成达标
        met = raw >= tgt
        ratio = round(raw, 3)
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

    report = {"op": op, "baseline_source": src, "target_ratio": tgt,
              "per_case": rows, "notes": notes,
              "summary": {"perf_cases": len(rows), "达标": passed, "blocked": blocked,
                          "status": status}}
    if risk_flags:
        report["summary"]["risk"] = sorted(risk_flags)
    if exc_rows:  # 唯一事实源：仅在有例外行时产 simulation
        report["simulation"] = _build_simulation(exc, exc_rows, case_by_id, op)
    # pc-1 + C5：summary.baseline_mock 标 + 报告级 NON-ACCEPTANCE 戳，统一由 _mark_non_acceptance 落。
    return _mark_non_acceptance(report, baseline)


def main(argv):
    """CLI：缺基线默认**挂起**（不静默造 mock、不产假通过）；mock 仅在显式 --mock 下启用。
    C5：`--mock` 产的报告带 `evidence_grade=development` + `acceptance_note=NON-ACCEPTANCE (mock evidence)`。"""
    ap = argparse.ArgumentParser(description="Task3 perf_compare")
    ap.add_argument("spec")
    ap.add_argument("caseset")
    ap.add_argument("evidence")
    ap.add_argument("baseline", nargs="?", default=None, help="基线 JSON；缺省且无 --mock → 挂起")
    ap.add_argument("--mock", action="store_true",
                    help="显式启用 mock 基线（本地演示，产物标『不可当真通过』；不加则缺基线即挂起）")
    ap.add_argument("--out", default="perf_report.json")
    a = ap.parse_args(argv)
    spec = json.load(open(a.spec, encoding="utf-8"))
    caseset = json.load(open(a.caseset, encoding="utf-8"))
    evidence = json.load(open(a.evidence, encoding="utf-8"))
    if a.baseline:
        baseline = json.load(open(a.baseline, encoding="utf-8"))
    elif a.mock:
        baseline = mock_baseline(spec, evidence)
        print("[perf_compare] ⚠ 使用 mock 基线（--mock）——本地演示，产物不可当真通过验收")
    else:  # pc-1：默认不再静默 mock，缺基线 → None → 挂起（非 status=ok）
        baseline = None
        print("[perf_compare] ⚠ 未提供基线且未加 --mock → 挂起（不静默造 mock、不产假通过）")
    report = perf_compare(spec, caseset, evidence, baseline)
    json.dump(report, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[perf_compare] {report['summary']} -> {a.out}")
    if report.get("acceptance_note"):   # C5：假基线的「达标」绝不能读起来像真达标
        print(f"[perf_compare] ⚠ {report['acceptance_note']}")


if __name__ == "__main__":
    main(sys.argv[1:])
