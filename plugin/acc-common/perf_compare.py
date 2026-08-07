"""Task 3 · perf_compare — evidence + baseline.json -> perf_report.json。

对比双方 scope 必须一致（默认 kernel-only，ADR 0006·proposed·未 settle）；不一致 →
blocked_incomparable_timing_scope、不出结论。ratio = baseline_us / npu_us（>1 表示 NPU 更快）；
达标 = ratio ≥ spec.perf.target_ratio。缺证据/基线 → blocked；无性能用例 → 显式 no_perf_cases。

小 shape 例外（T6，任务书条款）：`小shape` tag 的性能用例，若 max(NPU,基线) < when_us_below 且
  |NPU-基线| ≤ abs_gap_us_within，达标**保持 False** + 打 `exception` 标 + 记 exception_detail；
  status=exception → 编排层映射 PASSED_WITH_RISK（挂人核仿真图，绝不偷偷置 True）。

`perf.mode=measure_only`（AGENTS.md §5.10，2026-08-03）：**只测不比**——逐 case 转录 NPU msprof
  kernel-only 实测耗时 + 只读分档汇总，`summary.status="measured"`，**不产 ratio、不产
  cases_above_threshold、不产任何达标结论**。缺一条实测即 `blocked`（"不做对比" ≠ "不做测量"）。

⚠ **「没有性能目标」不再有隐式档**（2026-08-06）：有性能用例要判、spec 却给不出 `target_ratio` 时
  一律 `invalid_config`，**不再兜底套 0.95**（那是工具替任务书造要求，5.8 的反例）。任务书真没有
  性能要求就走上面那条 `measure_only` —— 它要 cite/quote/快照指纹的授权锚，是**显式**的宽档；
  「spec 里什么都不写」拿不到同样的待遇。详见 `_resolve_target_ratio` / `_NO_TARGET_ERR`。

GPU 标杆 consumer（T8）：`expect_source ∈ {gpu, gpu_external}` 且缺基线 → blocked_wait_gpu_benchmark
  （正规挂起、非 fail、baseline=None 不崩）；消费的基线带 policy_risk 且达标 → summary.risk。

`report['simulation']` **只此处生成**（唯一事实源）；perf_sim_plot 只渲染、不二次推断。
v0 提供 mock_baseline；真机/外部给基线时替换。

⚠ **mock 基线 = 非验收证据**（C5）：`mock_baseline` 造的是「NPU mock us × 1.08」这种编出来的数，
拿它比出来的「达标」不构成任何性能结论。故凡消费 `baseline.mock=True` 的报告，一律打
`evidence_grade="development"` + `acceptance_note="NON-ACCEPTANCE (mock evidence)…"`
（字段名与措辞照 `catlass_adapter.run_catlass_mock` 的既有口径，不另发明），让「假基线的达标」
在产物里一眼可辨、不可能被当成真达标。真基线（`_real_baseline.json`）/ 外部 GPU 标杆**一字不受影响**。

报告三件套（M3，2026-07-25 加）：正常判定出口额外产 **只读聚合**——报告级 `by_dtype` /
`overall_speedup` / `custom_only_by_dtype`，summary 级 `cases_above_threshold` / `cases_scored`。
对标 cannbot `skills/operator-evaluation/scripts/performance.py`（逐块行号见 `_report_aggregate`）。
**它们一个字节都不参与裁决**：`达标` / `blocked` / `status` / simulation 与本块无关，删掉这些字段
报告的结论一模一样。加它们只为让人读报告时有 cannbot 同款的 dtype 汇总口径。
"""
import argparse, json, math, os, re, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perf_mode  # noqa: E402

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


#: 缺 `target_ratio` 时的统一拒绝语。**两条出路都逐字给出**，免得读到这条错的人以为
#: 「没有性能要求」这件事本身不再被支持——它仍然支持，只是必须**显式声明**（§5.10），
#: 不能靠「spec 里什么都不写」让工具替任务书猜一个阈值。
_NO_TARGET_ERR = (
    "spec 未给出可判的性能验收目标（无 target_ratio）→ invalid_config。\n"
    "  ⚠ **这里刻意不再兜底套 0.95**：那等于替任务书凭空造一条它没写过的验收要求"
    "（aclnnRoll 试跑实测：47 条性能用例被这个凭空目标判出 19 条 fail）。\n"
    "  两条合法出路，二选一显式写进 spec：\n"
    "    · 任务书确有比值要求 → 补 perf.target_ratio（配 perf.baseline）；\n"
    "    · 任务书没有性能要求 / 只要求与 GPU 比 / 本轮改动属新增 dtype·扩 shape·新算子 →\n"
    "      按 AGENTS.md §5.10 写 perf.mode='measure_only' + perf.measure_only_authorization"
    "（ground + cite + quote + taskdoc_snapshot_sha256），走「只测不比」。")


def _resolve_target_ratio(perf_spec):
    """pc-3：target_ratio 严格化，返回 (tgt|None, err|None)。

    | perf 块 | 出口 | 为什么 |
    |---|---|---|
    | 有合法 `target_ratio` | `(float, None)` | 正常判达标 |
    | 缺 `target_ratio`（含 perf 块整块缺席 / `null` / `{}`） | `(None, err)` | **不兜底**：见 `_NO_TARGET_ERR` |
    | `target_ratio` 非有限正数 | `(None, err)` | 0/负/bool/NaN/inf/字符串一律 invalid_config |

    ⚠ 「perf 块整块缺席」此前返回 `(0.95, None)`——**那是本仓 5.8「不捏造」的反例写进了代码**：
    工具替任务书造了一条 `ratio ≥ 0.95` 的验收要求，报告里 `target_ratio: 0.95` 看上去还像是
    任务书写的。改成 fail-closed 后，「任务书没有性能要求」必须走 §5.10 的 `measure_only`
    **显式声明 + 授权锚**（cite/quote/快照指纹），而不是靠「spec 里什么都不写」拿到同样的宽档。

    ⚠ 本函数只在**有性能用例要判**时才决定成败：调用方的 `if not perf_ids: return _no_perf_cases(...)`
    排在 `tgt_err` 检查之前，所以「没有性能用例的 spec 不写 perf 块」照旧走 `no_perf_cases`
    →「PASS(无性能要求)」，一个字节没变。收紧的只有「有性能用例、却连目标都没有」这一格。
    """
    if not isinstance(perf_spec, dict) or "target_ratio" not in perf_spec:
        if isinstance(perf_spec, dict) and perf_spec.get("baseline"):
            return None, "spec 声明了性能基线却缺 target_ratio（拒静默套 0.95）→ invalid_config"
        return None, _NO_TARGET_ERR
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


# ————————————————— M3 · cannbot 报告三件套（**纯只读聚合，不参与任何裁决**）—————————————————
# provenance：对标 `repos/cannbot-ops-input/skills/operator-evaluation/scripts/performance.py`
#   · by_dtype              ← performance.py:34-59  `summarize_latency`（每 dtype 一行，两侧各取 median）
#   · overall_speedup       ← performance.py:98-112 `build_performance_report`（Σ(base·count)/Σ(npu·count)）
#   · cases_above_threshold ← performance.py:80-95  `count_speedup_above`（**严格 `ratio > threshold`**）
#   · custom_only_by_dtype  ← performance.py:62-77  `summarize_custom_only_latency`（无基线只报绝对时延）
#
# ⚠⚠ 两把尺子故意并存（蓝本 `dev-doc/oprunway-cannbot-alignment-plan.md` L1 的裁决，别「顺手统一」）：
#   - **硬门**（`perf_compare` 里的 `met = raw >= tgt`）用 `>=`，**一个字不动**——它是我们的验收判据，
#     4 个 pin 算子的真机验收结论就挂在它上面；`>=` 对「恰好压线」更友好，是有意选的。
#   - **本块的 `cases_above_threshold`** 用 cannbot 的**严格 `>`**——它是**展示口径**，为的是我们报告里
#     那个数与 cannbot 报告里同名的数可以直接对照。
#   于是「ratio 恰等于 target_ratio」的用例会出现 **达标=True 但不计入 cases_above_threshold** 的现象：
#   这不是 bug，是两把尺子的定义差。测试 `PerfReportAggregateTest.test_ratio_equal_threshold_*` 钉死它。
#
# ⚠ 另一条纪律（承 pc-2 的血教训）：严格 `>` 比的是**重算的原始比** `base/npu`，
#   **绝不用 `row["ratio"]`**——那是 `round(raw, 3)` 的展示值，拿它比阈值会重演 pc-2 那种
#   「round 把数救活/误杀」的老 bug（raw=0.9504 → round 0.95 → 严格 `>` 反而漏计）。


def _case_dtype(case):
    """case 的 dtype 分组键 = `inputs[0].dtype`——沿用本仓既有口径（`gen_cases.py` 产 `dtype_tested`
    用的就是 `c["inputs"][0]["dtype"]`），op-中立、**无算子名分支**（律令#0）。
    取不到 → `"unknown"`：照 cannbot performance.py:42 的 `row.get("dtype", "unknown")` 兜底——
    宁可归到 unknown 桶，也不能把这一行**从聚合里悄悄丢掉**（丢行 = 报告里的 count 对不上）。

    ⚠ 与 `validator` 的精度 `by_dtype`（L3）**故意用不同的键**，别当成不一致去「统一」：
      · 精度侧按**输出 dtype**（`derive_output_dtype`）——因为容差是按输出 dtype 定的，
        IsClose 这种 float→bool 算子按输入归桶会回显一份根本没用上的 float 容差；
      · 性能侧（这里）按**输入 dtype**——性能看的是喂进去的负载，且 cannbot performance.py
        本身就是按 `case["dtype"]`（输入）归桶的。两边各自都对。"""
    if not isinstance(case, dict):
        return "unknown"
    ins = case.get("inputs")
    if not isinstance(ins, list) or not ins or not isinstance(ins[0], dict):
        return "unknown"
    dt = ins[0].get("dtype")
    return dt if isinstance(dt, str) and dt else "unknown"


def _median(values):
    """与 cannbot `median_us`（performance.py:17-21）同函数同语义：`statistics.median`，
    **偶数个样本取中间两数的平均**（不是取下中位）。
    空集这里返回 None、cannbot 那边抛 ValueError——有意偏离：本块只是只读报告字段，
    绝不允许「某个桶恰好空了」把整份 perf_report 炸掉。调用点已保证不传空集，这是第二道保险。"""
    vals = [float(v) for v in values]
    return float(statistics.median(vals)) if vals else None


def _report_aggregate(rows, case_by_id, ev, bl, tgt):
    """据**已判完**的 per_case rows 组装报告三件套。纯读：不看也不改任何 `达标`/`blocked`/`status`。

    进聚合的只有「真量到且可比」的行 —— 即带 `ratio` 的行（两侧 us 都是有限正数、scope 已对齐）。
    缺证据缺基线 / scope 不可比的行**一律不进**：它们压根没有可比测量，
    硬塞进 median 就是编数字（承 CLAUDE.md「不凭空捏造」）。故 `cases_scored ≤ perf_cases`，
    二者不等是正常的——cannbot 同理（`count_speedup_above` 对 `custom_us<=0` 的行既不计分子也不计分母）。

    `custom_only_by_dtype`：**只有 npu 侧量到、根本没有基线条目**的行，只报绝对时延、
    **不硬算 speedup**（cannbot 在 CPU-only 基线时就是这么干的）。判据是「`bl` 里没有这个 case_id」；
    「有基线但 scope 不可比 / 基线数值非法」**不算**无基线，不进这个桶（否则 `no_npu_baseline` 这个
    标签就是撒谎）。

    返回 dict；`custom_only_by_dtype` **没有内容时不返回该键**（省得每份报告都挂一个空列表）。
    """
    by, custom_only = {}, {}
    above = scored = 0
    for r in rows:
        cid = r.get("case_id")
        dt = _case_dtype(case_by_id.get(cid))
        if "ratio" in r:
            npu, base = r.get("npu_us"), (r.get("baseline") or {}).get("us")
            if not _finite_pos(npu) or not _finite_pos(base):
                continue                    # 理论到不了（有 ratio 必两侧合法）；留作 fail-closed 兜底
            raw = base / npu                # ⚠ 重算原始比，绝不用 round 过的 r["ratio"]（见上文纪律）
            g = by.setdefault(dt, {"npu": [], "baseline": []})
            g["npu"].append(float(npu))
            g["baseline"].append(float(base))
            scored += 1
            if raw > tgt:                   # 严格 >：cannbot count_speedup_above 口径（与硬门 >= 并存）
                above += 1
            continue
        if not r.get("blocked") or cid in bl:
            continue                        # 有基线（只是不可比）→ 不属「无基线」桶
        perf = (ev.get(cid) or {}).get("perf")
        us = perf.get("us") if isinstance(perf, dict) else None
        if _finite_pos(us):                 # 只在 npu 侧真量到有效数时才报绝对时延
            custom_only.setdefault(dt, []).append(float(us))

    by_dtype = []
    for dt in sorted(by):                   # 按 dtype 名排序：与 cannbot 一致，且产物稳定可 diff
        npu_med, base_med = _median(by[dt]["npu"]), _median(by[dt]["baseline"])
        by_dtype.append({"dtype": dt, "count": len(by[dt]["npu"]),
                         # 字段名用本仓既有词表：cannbot 的 custom_us ↔ 我们的 npu_us（同物异名）。
                         # cannbot 的 baseline_device 审计字段我们不带：那是它「同一份报告里可能混
                         # npu/cpu 基线」才需要的，我们的基线来源单一、已在报告级 baseline_source。
                         "npu_us": npu_med, "baseline_us": base_med,
                         # 不 round：本块是聚合口径的忠实移植，round 是 per-case `ratio` 的展示约定，
                         # 别把它套过来（套了就看不出两处数的真实差异）。
                         "speedup": (base_med / npu_med) if _finite_pos(npu_med) else None})
    npu_total = sum(row["npu_us"] * row["count"] for row in by_dtype)
    base_total = sum(row["baseline_us"] * row["count"] for row in by_dtype)
    # 分母为 0（一行可比测量都没有）→ None，**绝不编造**一个 speedup（cannbot `speedup()` 同款守卫）。
    out = {"by_dtype": by_dtype, "overall_speedup": (base_total / npu_total) if npu_total > 0 else None,
           "cases_above_threshold": above, "cases_scored": scored}
    if custom_only:
        out["custom_only_by_dtype"] = [
            {"dtype": dt, "count": len(vals), "npu_us": _median(vals),
             # 照 cannbot 的 `comparison: no_npu_baseline` 标签：这一行**不是** speedup，别当加速比读。
             "comparison": "no_npu_baseline"} for dt, vals in sorted(custom_only.items())]
    return out


def _shape_class_aggregate(rows, caseset):
    """按 caseset 的大小 shape 元数据做只读汇总；不参与任何裁决。

    新契约声明 ``perf_case_policy`` 后，每条性能行都必须有合法分类，且两桶计数要与生成期账本一致；
    缺失时产 ``problems``，绝不静默少算。旧 caseset 未声明 policy 且没有分类元数据时返回 ``None``，
    保持历史兼容。
    """
    case_by_id = {c.get("id"): c for c in (caseset.get("cases") or [])
                  if isinstance(c, dict) and isinstance(c.get("id"), str)}
    declared = isinstance(caseset.get("perf_case_policy"), dict)
    has_meta = any(isinstance((case_by_id.get(r.get("case_id")) or {}).get(
        "perf_shape_classification"), dict) for r in rows if isinstance(r, dict))
    if not declared:
        # legacy caseset 没有完整策略账本时不生成大小 shape 报告；即使零星 case 被手工塞了分类，
        # 也不能据部分元数据产一个看似完整的视图。
        return None
    buckets = {}
    problems = []
    for row in rows:
        cid = row.get("case_id")
        meta = (case_by_id.get(cid) or {}).get("perf_shape_classification")
        if not isinstance(meta, dict) or meta.get("class") not in ("small", "large"):
            problems.append(f"{cid}: 缺/坏 perf_shape_classification")
            continue
        cls = meta["class"]
        item = buckets.setdefault(
            cls, {"rows": 0, "scored": 0, "met": 0, "blocked": 0,
                  "npu_all": [], "baseline_all": [], "paired_npu": [], "paired_baseline": []})
        item["rows"] += 1
        item["met"] += int(bool(row.get("达标")))
        item["blocked"] += int(bool(row.get("blocked")))
        if _finite_pos(row.get("npu_us")):
            item["npu_all"].append(float(row["npu_us"]))
        base = (row.get("baseline") or {}).get("us")
        if _finite_pos(base):
            item["baseline_all"].append(float(base))
        if "ratio" in row and _finite_pos(row.get("npu_us")):
            if _finite_pos(base):
                item["scored"] += 1
                item["paired_npu"].append(float(row["npu_us"]))
                item["paired_baseline"].append(float(base))
    expected = ((caseset.get("perf_case_policy") or {}).get("counts") or {}) if declared else {}
    out, all_npu, all_base, all_paired_npu, all_paired_base = [], [], [], [], []
    for cls in ("small", "large"):
        if cls not in buckets:
            item = {"rows": 0, "scored": 0, "met": 0, "blocked": 0,
                    "npu_all": [], "baseline_all": [], "paired_npu": [], "paired_baseline": []}
        else:
            item = buckets[cls]
        if declared and expected.get(cls) != item["rows"]:
            problems.append(
                f"{cls}: 生成期账本={expected.get(cls)!r}，报告行={item['rows']}，大小 shape 计数不一致")
        npu_med, base_med = _median(item["npu_all"]), _median(item["baseline_all"])
        pair_npu_med, pair_base_med = _median(item["paired_npu"]), _median(item["paired_baseline"])
        all_npu.extend(item["npu_all"])
        all_base.extend(item["baseline_all"])
        all_paired_npu.extend(item["paired_npu"])
        all_paired_base.extend(item["paired_baseline"])
        out.append({"class": cls, "cases": item["rows"], "planned_cases": item["rows"],
                    "cases_scored": item["scored"],
                    "达标": item["met"], "blocked": item["blocked"],
                    "npu_us": npu_med, "baseline_us": base_med,
                    "speedup": (pair_base_med / pair_npu_med)
                    if _finite_pos(pair_npu_med) else None})
    npu_med, base_med = _median(all_npu), _median(all_base)
    pair_npu_med, pair_base_med = _median(all_paired_npu), _median(all_paired_base)
    overall = {"class": "overall", "cases": sum(x["cases"] for x in out),
               "planned_cases": sum(x["planned_cases"] for x in out),
               "cases_scored": sum(x["cases_scored"] for x in out),
               "达标": sum(x["达标"] for x in out), "blocked": sum(x["blocked"] for x in out),
               "npu_us": npu_med, "baseline_us": base_med,
               "speedup": (pair_base_med / pair_npu_med) if _finite_pos(pair_npu_med) else None}
    return {"by_shape_class": out, "overall": overall,
            "complete": not problems, "problems": problems}


def _attach_shape_report(report, caseset):
    """给已成型报告附加大小 shape 只读视图；异常只记 notes，不改 summary/status。"""
    try:
        agg = _shape_class_aggregate(report.get("per_case") or [], caseset)
    except Exception as exc:
        report.setdefault("notes", []).append(
            f"大小 shape 报告生成失败已跳过，裁决不受影响：{exc!r}")
        return report
    if agg is None:
        return report
    report["by_shape_class"] = agg["by_shape_class"]
    report["shape_overall"] = agg["overall"]
    report["shape_report_complete"] = agg["complete"]
    if agg["problems"]:
        report["shape_report_problems"] = agg["problems"]
        report.setdefault("notes", []).append("大小 shape 报告契约不完整：" + "；".join(agg["problems"]))
    return report


def attach_skipped_shape_plan(report, caseset):
    """精度门跳过 Task3 时保留生成期性能计划；实际采集数仍严格为零。

    复用同一分类聚合器核对 case 元数据与 ``perf_case_policy.counts``，但不把计划行塞进
    ``per_case``，避免下游误认成已采集性能证据。
    """
    planned_rows = [
        {"case_id": c.get("id")}
        for c in (caseset.get("cases") or [])
        if isinstance(c, dict) and "性能" in (c.get("dims") or [])
    ]
    try:
        agg = _shape_class_aggregate(planned_rows, caseset)
    except Exception as exc:
        report.setdefault("notes", []).append(
            f"性能计划大小 shape 报告生成失败已跳过，裁决不受影响：{exc!r}")
        return report
    if agg is None:
        report.setdefault("summary", {})["planned_cases"] = len(planned_rows)
        return report

    by_shape = []
    for item in agg["by_shape_class"]:
        planned = item["planned_cases"]
        by_shape.append({
            **item,
            "cases": 0,
            "planned_cases": planned,
            "cases_scored": 0,
            "达标": 0,
            "blocked": 0,
            "npu_us": None,
            "baseline_us": None,
            "speedup": None,
        })
    report["by_shape_class"] = by_shape
    report["shape_overall"] = {
        **agg["overall"],
        "cases": 0,
        "planned_cases": sum(x["planned_cases"] for x in by_shape),
        "cases_scored": 0,
        "达标": 0,
        "blocked": 0,
        "npu_us": None,
        "baseline_us": None,
        "speedup": None,
    }
    report["shape_report_complete"] = agg["complete"]
    report.setdefault("summary", {})["planned_cases"] = report["shape_overall"]["planned_cases"]
    report["summary"]["cases_scored"] = 0
    if agg["problems"]:
        report["shape_report_problems"] = agg["problems"]
        report.setdefault("notes", []).append(
            "性能计划大小 shape 报告契约不完整：" + "；".join(agg["problems"]))
    return report


def _attach_non_passing_cases(report, caseset, evidence, baseline):
    """把所有未通过性能 case 逐条挂到最终报告，且不改变既有裁决。

    ``per_case`` 是确定性裁决明细，但早期仅含 case_id/数值/简短 note；baseline 采集失败的
    原始行为保存在 ``baseline.excluded``，dtype、shape 与大小分类则在 caseset。这里把三处
    已有事实做只读联结，生成面向人读报告的 ``non_passing_cases``：

    * ratio 未达标、blocked、exception、等待外部 baseline 等所有非 PASS 行都必须出现；
    * 每行带 dtype、输入 shape、small/large、双边行为/计时和原因；
    * 不猜归因，不把 baseline limitation 写成 DUT defect；
    * 本块不参与 ``达标`` / ``blocked`` / ``status`` 计算，生成失败也不能改写裁决。
    """
    rows = report.get("per_case")
    if not isinstance(rows, list):
        return report
    case_by_id = {
        c.get("id"): c for c in ((caseset or {}).get("cases") or [])
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    }
    ev_by_id = {
        e.get("case_id"): e for e in ((evidence or {}).get("evidence") or [])
        if isinstance(e, dict) and isinstance(e.get("case_id"), str)
    }
    baseline_doc = baseline if isinstance(baseline, dict) else {}
    baseline_by_id = {
        b.get("case_id"): b for b in (baseline_doc.get("per_case") or [])
        if isinstance(b, dict) and isinstance(b.get("case_id"), str)
    }
    excluded_by_id = {
        b.get("case_id"): b for b in (baseline_doc.get("excluded") or [])
        if isinstance(b, dict) and isinstance(b.get("case_id"), str)
    }
    report_status = str((report.get("summary") or {}).get("status") or "")
    failures = []
    for row in rows:
        if not isinstance(row, dict) or row.get("达标") is True:
            continue
        cid = row.get("case_id")
        case = case_by_id.get(cid) or {}
        inputs = case.get("inputs") if isinstance(case.get("inputs"), list) else []
        shapes = [
            {"name": inp.get("name"), "shape": inp.get("shape")}
            for inp in inputs if isinstance(inp, dict)
        ]
        shape_meta = case.get("perf_shape_classification")
        shape_meta = shape_meta if isinstance(shape_meta, dict) else {}
        perf = (ev_by_id.get(cid) or {}).get("perf")
        perf = perf if isinstance(perf, dict) else {}
        excluded = excluded_by_id.get(cid) or {}
        baseline_row = baseline_by_id.get(cid) or {}

        if row.get("blocked") or report_status.startswith("blocked"):
            outcome = "blocked"
        elif row.get("exception"):
            outcome = "exception"
        else:
            outcome = "failed"
        reason = row.get("note")
        if not reason and outcome == "failed":
            reason = (
                f"ratio={row.get('ratio')!r} < target_ratio={report.get('target_ratio')!r}"
            )
        item = {
            "case_id": cid,
            "outcome": outcome,
            "reason": reason or "未达性能验收标准",
            "dtype": _case_dtype(case),
            "inputs": shapes,
            "shape_class": shape_meta.get("class"),
            "input_bytes": shape_meta.get("input_bytes"),
            "ratio": row.get("ratio"),
            "target_ratio": report.get("target_ratio"),
            "custom": {
                "behavior": perf.get("behavior"),
                "us": row.get("npu_us", perf.get("us")),
                "scope": row.get("npu_scope", perf.get("scope")),
                "note": perf.get("note"),
            },
            "baseline": {
                "behavior": excluded.get("behavior"),
                "us": (row.get("baseline") or {}).get("us", baseline_row.get("us")),
                "scope": baseline_doc.get("scope"),
                "reason": excluded.get("reason"),
            },
        }
        failures.append(item)
    report["non_passing_cases"] = failures
    summary = report.get("summary")
    if isinstance(summary, dict):
        summary["non_passing"] = len(failures)
        summary["failed"] = sum(1 for item in failures if item["outcome"] == "failed")
        summary["exceptions"] = sum(1 for item in failures if item["outcome"] == "exception")
    return report


# ————————————————— measure_only · 只测不比（AGENTS.md §5.10）—————————————————
# ⚠ 本块**不算 ratio、不比阈值、不出达标**。它唯一的产出是「每条性能 case 的 NPU kernel-only
#   实测耗时」+ 只读分档汇总。缺一条实测即 `blocked`——`measure_only` 是「不做对比」，
#   **不是**「不做测量」；让零 msprof 数据也能过门就等于把它做成了 fail-open 开关。
#
# 字段命名刻意**避开** ratio 通路的词表（`by_dtype` / `by_shape_class` / `达标` / `speedup`）：
#   下游任何按老键名读报告的地方在 measure_only 报告上会**读不到东西**（→ 显式缺失），
#   而不是读到一个 0 或 None 被误解成「测了、只是没达标」。


def _measured_by_dtype(rows, case_by_id):
    """按输入 dtype 汇总实测耗时中位数（只读展示，零裁决影响）。dtype 口径同 `_case_dtype`。"""
    buckets = {}
    for r in rows:
        if r.get("blocked") or not _finite_pos(r.get("npu_us")):
            continue
        buckets.setdefault(_case_dtype(case_by_id.get(r.get("case_id"))), []).append(
            float(r["npu_us"]))
    return [{"dtype": dt, "count": len(vals), "npu_us": _median(vals),
             # 照 cannbot `no_npu_baseline` 的标法：这一行是**绝对时延**，不是加速比。
             "comparison": "no_baseline_measured_only"}
            for dt, vals in sorted(buckets.items())]


def _measured_shape_aggregate(rows, caseset):
    """measure_only 的大小 shape 只读汇总；口径与生成期账本交叉对齐，不参与裁决。

    与 `_shape_class_aggregate` 的区别：**只出实测字段**（cases / measured / npu_us），
    绝不出 `达标` / `baseline_us` / `speedup`——没有对照物时那三个字段无从谈起，
    填 0/None 会被读成「比过了只是没达标」。
    """
    case_by_id = {c.get("id"): c for c in (caseset.get("cases") or [])
                  if isinstance(c, dict) and isinstance(c.get("id"), str)}
    policy = caseset.get("perf_case_policy")
    if not isinstance(policy, dict):
        return None                                   # legacy caseset 无账本 → 不产半份视图
    buckets, problems = {}, []
    for row in rows:
        cid = row.get("case_id")
        meta = (case_by_id.get(cid) or {}).get("perf_shape_classification")
        if not isinstance(meta, dict) or meta.get("class") not in ("small", "large"):
            problems.append(f"{cid}: 缺/坏 perf_shape_classification")
            continue
        item = buckets.setdefault(meta["class"], {"cases": 0, "measured": 0, "us": []})
        item["cases"] += 1
        if not row.get("blocked") and _finite_pos(row.get("npu_us")):
            item["measured"] += 1
            item["us"].append(float(row["npu_us"]))
    expected = (policy.get("counts") or {}) if isinstance(policy.get("counts"), dict) else {}
    out, all_us = [], []
    for cls in ("small", "large"):
        item = buckets.get(cls) or {"cases": 0, "measured": 0, "us": []}
        if expected.get(cls) != item["cases"]:
            problems.append(
                f"{cls}: 生成期账本={expected.get(cls)!r}，报告行={item['cases']}，大小 shape 计数不一致")
        all_us.extend(item["us"])
        out.append({"class": cls, "cases": item["cases"], "measured": item["measured"],
                    "npu_us": _median(item["us"])})
    overall = {"class": "overall", "cases": sum(x["cases"] for x in out),
               "measured": sum(x["measured"] for x in out), "npu_us": _median(all_us)}
    return {"by_shape_class": out, "overall": overall,
            "complete": not problems, "problems": problems}


def _measure_only_compare(spec, caseset, evidence, op, perf_ids, case_by_id):
    """`perf.mode=measure_only` 的唯一出口：逐 case 转录 NPU 实测，**不做任何对比**。"""
    perf_spec = spec.get("perf") or {}
    notes = [perf_mode.MEASURE_ONLY_NOTE]
    if not perf_ids:
        report = _no_perf_cases(spec, None, None, perf_spec, notes)
        report["perf_mode"] = perf_mode.MODE_MEASURE_ONLY
        # `_no_perf_cases` 是与 ratio 通路共用的出口，它落的 `达标: 0` 在本口径下**没有含义**：
        # 没有对照物就没有「达标」这件事，留一个 `0` 会被读成「一条都没达标」——那正是本档刻意
        # 不出该键的理由（见上面主出口的注释）。本仓契约也明令禁止：
        # `validate_acceptance_state._gate_measure_only_report` 对 measure_only 报告校
        # 「summary 不得含 达标 / cases_above_threshold / cases_scored」，
        # 留着这个 0 等于自己产出一份过不了自己那道门的产物。
        # ⚠ **一道门都没放松**：`status` 仍是 `no_perf_cases`，gate_task3 见它照旧记 error → BLOCKED；
        #   run_workflow 的 `perf_measured_only` 要求 `status == measured`，此出口拿不到，
        #   overall 仍落 `BLOCKED(measure_only 性能实测未完成)`。这里改的只是**词表**，不是判定。
        report["summary"].pop("达标", None)
        # 补本口径的计数词（0 条性能 case → 0 条实测）。缺这一项时门会另记一条
        # 「summary.measured=None 非整数计数」的**噪声** error，把真正的原因（无性能用例）淹掉。
        report["summary"]["measured"] = 0
        return report
    ev_list = evidence["evidence"]
    ev_ids = [e.get("case_id") for e in ev_list if isinstance(e, dict)]
    dup = len(set(ev_ids)) != len(ev_ids)
    if dup:
        notes.append("evidence 有重复 case_id")
    ev = {e.get("case_id"): e for e in ev_list if isinstance(e, dict) and e.get("case_id")}

    rows, measured, blocked = [], 0, 0
    for cid in perf_ids:
        eperf = (ev.get(cid) or {}).get("perf")
        us = eperf.get("us") if isinstance(eperf, dict) else None
        scope = eperf.get("scope") if isinstance(eperf, dict) else None
        row = {"case_id": cid, "npu_us": us, "scope": scope}
        # ★ 红线：没有真实实测（us 非有限正数 / scope 缺失或非法）→ blocked。
        #   status 因此落 `blocked`，验收门 gate_task3 见 blocked 即 FAILED。
        if _finite_pos(us) and scope in _VALID_SCOPES:
            row["blocked"] = False
            measured += 1
        else:
            row["blocked"] = True
            row["note"] = (f"measure_only 缺真实 NPU 实测：npu_us={us!r} scope={scope!r}"
                           "（须有限正数 + 合法计时口径）——不得以「未做对比」为由免测")
            blocked += 1
        rows.append(row)

    status = "blocked" if (blocked or dup) else perf_mode.STATUS_MEASURED
    report = {"op": op, "perf_mode": perf_mode.MODE_MEASURE_ONLY,
              "baseline_source": None, "target_ratio": None,
              "per_case": rows, "notes": notes,
              # 刻意**不出** `达标` / `cases_above_threshold` / `cases_scored`：
              # 没有对照物就没有「达标」这件事，出一个 0 等于给出一个未做的裁决。
              "summary": {"perf_cases": len(rows), "measured": measured,
                          "blocked": blocked, "status": status}}
    report["measured_by_dtype"] = _measured_by_dtype(rows, case_by_id)
    try:
        agg = _measured_shape_aggregate(rows, caseset)
    except Exception as exc:   # 只读报表塌了也绝不拖垮裁决（同 ratio 通路的纪律）
        agg = None
        notes.append(f"measure_only 大小 shape 汇总生成失败已跳过，实测数据不受影响：{exc!r}")
    if agg is not None:
        report["measured_by_shape_class"] = agg["by_shape_class"]
        report["measured_shape_overall"] = agg["overall"]
        report["measured_shape_complete"] = agg["complete"]
        if agg["problems"]:
            report["measured_shape_problems"] = agg["problems"]
            notes.append("measure_only 大小 shape 汇总契约不完整：" + "；".join(agg["problems"]))
    return report


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
    # AGENTS.md §5.10 · 只测不比：**在取基线之前**分流。放在这里而不是更靠后，是因为
    # `baseline is None` 在下面的 ratio 通路里意味着「缺/废标杆 → 挂起」，而 measure_only
    # 的 baseline 本来就该是 None——不分流会被误判成 blocked_wait_gpu_benchmark。
    try:
        mode = perf_mode.resolve_spec_mode(spec)
    except ValueError as ex:
        return _mark_non_acceptance(_invalid(op, [f"perf.mode 配置非法：{ex}"]), baseline)
    if perf_mode.is_measure_only(mode):
        if baseline is not None:
            # 走到这里说明编排层给了一份 measure_only 不该有的基线 → 宁可停下也不「顺手忽略」。
            return _mark_non_acceptance(
                _invalid(op, ["perf.mode='measure_only' 却收到了性能基线——"
                              "只测不比的口径下不得消费任何对照物，fail-closed"]), baseline)
        return _measure_only_compare(spec, caseset, evidence, op, perf_ids, case_by_id)
    exc, exc_note = _parse_small_shape_exception(spec)
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
        report = _attach_shape_report(
            {"op": op, "baseline_source": src, "target_ratio": tgt,
             "per_case": rows, "notes": notes,
             "summary": {"perf_cases": len(rows), "达标": 0, "blocked": 0, "status": status}},
            caseset)
        return _attach_non_passing_cases(report, caseset, evidence, baseline)

    src = baseline.get("source")
    if not perf_ids:
        return _mark_non_acceptance(
            _no_perf_cases(spec, src, tgt, perf_spec, [exc_note] if exc_note else None), baseline)

    # pc-3：有性能用例要判、但 target_ratio 非法/缺 → invalid_config；不进 ratio、绝不全达标。
    if tgt is None:
        rows = [{"case_id": cid, "达标": False, "blocked": True, "note": tgt_err} for cid in perf_ids]
        notes = [tgt_err] + ([exc_note] if exc_note else [])
        report = _attach_shape_report(
            {"op": op, "baseline_source": src, "target_ratio": None,
             "per_case": rows, "notes": notes,
             "summary": {"perf_cases": len(rows), "达标": 0, "blocked": len(rows),
                         "status": "invalid_config"}},
            caseset)
        return _mark_non_acceptance(
            _attach_non_passing_cases(report, caseset, evidence, baseline), baseline)

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
        e, b = ev.get(cid), bl.get(cid)
        if not e or not b:
            miss = ("evidence " if not e else "") + ("baseline" if not b else "")
            eperf = e.get("perf") if isinstance(e, dict) else None
            row = {"case_id": cid, "达标": False, "blocked": True,
                   "note": f"缺 {miss.strip()}"}
            if isinstance(eperf, dict):
                row["npu_us"] = eperf.get("us")
                row["npu_scope"] = eperf.get("scope")
            rows.append(row)
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
    _attach_shape_report(report, caseset)
    # M3：cannbot 报告三件套——**只读展示、零裁决影响**（上面 passed/blocked/status 已经算完了，
    # 这里只是把同一批 rows 再汇总一遍给人看）。dtype 聚合只挂在正常判定出口；
    # shape 报告在已有 case 行的早退路径也保留，用于如实展示 NPU 已采集值、baseline 缺失与 blocked，
    # 挂一堆空聚合只会让「没数据」看起来像「数据是 0」。故下游读这些键必须当**可选**。
    # by_dtype/overall_speedup/custom_only_by_dtype 放报告级（= cannbot report 同层），
    # 两个计数放 summary（蓝本 M3 指定，且 CLI 会打印 summary，两个 int 不会把那行撑爆）。
    # ⚠ 消费 mock 基线时这几个数同样是编的——同一份 report 上已有 NON-ACCEPTANCE 戳（C5），
    #   别把 overall_speedup 单独摘出去引用，摘出去就把戳丢了。
    try:
        agg = _report_aggregate(rows, case_by_id, ev, bl, tgt)
    except Exception as exc:   # 只读报表塌了也绝不拖垮裁决（与 validator 的 L3 精度聚合同一条纪律）。
        # 整块**不出**，而不是出一堆 0——0 会被读成「量到了，只是都为 0」，那是编数字。
        agg = None
        notes.append(f"报告聚合块（by_dtype/overall_speedup/cases_*）生成失败已跳过，"
                     f"裁决不受影响（status/达标/blocked 均在此之前算完）：{exc!r}")
    if agg is not None:
        report["by_dtype"] = agg["by_dtype"]
        report["overall_speedup"] = agg["overall_speedup"]
        if "custom_only_by_dtype" in agg:
            report["custom_only_by_dtype"] = agg["custom_only_by_dtype"]
        report["summary"]["cases_above_threshold"] = agg["cases_above_threshold"]
        report["summary"]["cases_scored"] = agg["cases_scored"]
    if risk_flags:
        report["summary"]["risk"] = sorted(risk_flags)
    if exc_rows:  # 唯一事实源：仅在有例外行时产 simulation
        report["simulation"] = _build_simulation(exc, exc_rows, case_by_id, op)
    _attach_non_passing_cases(report, caseset, evidence, baseline)
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
    elif perf_mode.resolve_spec_mode(spec) == perf_mode.MODE_MEASURE_ONLY:
        baseline = None                  # §5.10 只测不比：本来就不该有基线，不打「挂起」误导语
        print(f"[perf_compare] {perf_mode.MEASURE_ONLY_NOTE}")
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
