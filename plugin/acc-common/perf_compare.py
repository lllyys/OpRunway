"""Task 3 · perf_compare — evidence + baseline.json -> perf_report.json。

对比双方 scope 必须一致（默认 kernel-only，ADR 0006·proposed·未 settle）；不一致 →
blocked_incomparable_timing_scope、不出结论。ratio = baseline_us / npu_us（>1 表示 NPU 更快）；
达标 = ratio ≥ spec.perf.target_ratio。缺证据/基线 → blocked；无性能用例 → 显式 no_perf_cases。

**无验收目标**（任务书写「性能要求：无」→ spec 整个省略 `perf` 块）→ `collected_no_target`：
计时照常采集、`per_case` 的 `us`/`ratio` 照常记，但**一条都不判达标**——`达标` 与
`cases_above_threshold` 一律 `None`（**不是 0**：0 会被读成「一条都没达标」）。
⚠ 它**既不是 pass 也不是 fail**，是「采了数但没有验收目标可判」；编排层不得把它计入性能 fail，
也不得据它落干净 PASS。此前这里静默套 0.95 当阈值——那是**凭空造出一条任务书没写的验收要求**，
本轮改掉的正是它。

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

报告三件套（M3，2026-07-25 加）：正常判定出口额外产 **只读聚合**——报告级 `by_dtype` /
`overall_speedup` / `custom_only_by_dtype`，summary 级 `cases_above_threshold` / `cases_scored`。
对标 cannbot `skills/operator-evaluation/scripts/performance.py`（逐块行号见 `_report_aggregate`）。
**它们一个字节都不参与裁决**：`达标` / `blocked` / `status` / simulation 与本块无关，删掉这些字段
报告的结论一模一样。加它们只为让人读报告时有 cannbot 同款的 dtype 汇总口径。
"""
import argparse, json, math, re, statistics, sys

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
# —— 无验收目标（spec 未声明 perf 目标）的统一措辞：既不是通过也不是失败，只是「没目标可判」。
_NO_TARGET_STATUS = "collected_no_target"
_NO_TARGET_NOTE = (
    "spec 未声明性能验收目标（无 perf.target_ratio 且未声明 perf.baseline，对应任务书「性能要求：无」）"
    f"→ {_NO_TARGET_STATUS}：只采集不判达标。`达标` 与 `cases_above_threshold` 记 None（**不是 0**）。"
    "⚠ **性能未验证**——无验收目标即无从验证：这既不是通过也不是失败。本报告里任何「达标」计数"
    "（含 by_shape_class 的行级 True 计数）在此状态下都没有判定含义，"
    "不得读作「一条都没达标」，也不得读作「性能已验证通过」")
_NO_TARGET_UNVERIFIED_NOTE = (
    "⚠ 且本轮一条可比测量都没采到（cases_scored=0）：既没有验收目标，也没有性能数据——"
    "报告里的性能部分不构成任何结论")


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
    """pc-3：target_ratio 严格化。返回 `(tgt, err)`，三种出口**必须靠 err 区分，别只看 tgt is None**：

    | 出口 | 返回 | 含义 |
    |---|---|---|
    | 有目标 | `(float, None)` | 正常判达标 |
    | 配置非法 | `(None, err)` | invalid_config：perf 块存在但没给出可判的目标（详见下表） |
    | **无验收目标** | `(None, None)` | 任务书「性能要求：无」→ spec **整块省略** perf → 采集但不判达标 |

    ⚠ 「无验收目标」此前返回 `(0.95, None)`——那等于**替任务书凭空造一条 ratio≥0.95 的要求**，
    aclnnRoll 试跑就因此把 47 条性能用例判出 19 条 fail。改成 `(None, None)` 后，
    调用方须以 `tgt is None and err is None` 判「无目标」，以 `err is not None` 判 invalid_config。

    ⚠ **「无目标」的入口收得很窄，只认「perf 整块不存在」**（调用方的 `spec.get("perf") or {}`
    把「键缺席 / `null` / `{}`」都归一成空 dict，这三种等价）。只要 perf 块里**写了任何东西**却仍
    给不出可判目标，一律 invalid_config——因为那更像 spec 被写坏/抽取残缺，而不是任务书真没要求：

    | perf 块 | 出口 | 为什么 |
    |---|---|---|
    | 缺席 / `null` / `{}` | 无验收目标 | 任务书「性能要求：无」的如实表达 |
    | `{"baseline": "tbe"}` | invalid_config | 声明了基线却缺阈（拒静默套 0.95） |
    | `{"baseline": ""}`、`{"warmup": 5}`、`{"small_shape_exception": …}` | invalid_config | 写了性能配置却没有目标：残缺 spec，不是「没有要求」 |
    | `"perf": "性能要求：无"`（非对象） | invalid_config | 坏 spec 必须报出来，不能被读成「合法地没有要求」 |

    这道收窄是 fail-closed 方向：**误判成 invalid_config 只会多报一次错，误判成「无目标」会把
    一条真实的性能要求整条吞掉。**"""
    if not isinstance(perf_spec, dict):
        return None, (f"spec.perf 非对象（{type(perf_spec).__name__}）；性能要求须为 object 或整体省略，"
                      "写成字符串/数字不得被读成「无验收目标」→ invalid_config")
    if "target_ratio" not in perf_spec:
        if perf_spec.get("baseline"):
            return None, "spec 声明了性能基线却缺 target_ratio（拒静默套 0.95）→ invalid_config"
        if perf_spec:
            return None, (f"spec.perf 写了 {sorted(perf_spec)} 却既无 target_ratio 也无有效 baseline"
                          "（残缺 spec，非「无性能要求」——后者须整块省略 perf）→ invalid_config")
        return None, None       # perf 整块缺席/空 → 无验收目标：合法状态，不是错误
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
    # 「无验收目标」是靠 `spec.perf` **缺席或空对象**判定的，所以坏掉的 perf 块必须在这里就拦下：
    # 否则 `"perf": "性能要求：无"` 这种写法会一路走到 `(spec.get("perf") or {}).get(...)` 抛
    # AttributeError（旧行为），或者更糟——被误读成「合法地没有要求」。fail-closed。
    perf_spec = spec.get("perf")
    if perf_spec is not None and not isinstance(perf_spec, dict):
        return _invalid(op, [f"spec.perf 非对象（{type(perf_spec).__name__}）；性能要求须为 object "
                             "或整体省略，写成字符串/数字不得被读成「无验收目标」"])
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
# ⚠⚠ 两把尺子故意并存（蓝本 `doc/oprunway-cannbot-alignment-plan.md` L1 的裁决，别「顺手统一」）：
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

    `tgt is None`（无验收目标）时 `cases_above_threshold` 返回 **None 而不是 0**：没有阈值就没有
    「超过阈值的条数」这回事，落 0 会被读成「一条都没超」。`cases_scored` 不受影响——它数的是
    实际采到的可比测量条数，与有没有目标无关。
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
            # 严格 >：cannbot count_speedup_above 口径（与硬门 >= 并存）。tgt is None（无验收目标）→
            # 压根没有阈值可比，不计数（下面整块出 None，不出 0）。
            if tgt is not None and raw > tgt:
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
           "cases_above_threshold": (above if tgt is not None else None), "cases_scored": scored}
    if custom_only:
        out["custom_only_by_dtype"] = [
            {"dtype": dt, "count": len(vals), "npu_us": _median(vals),
             # 照 cannbot 的 `comparison: no_npu_baseline` 标签：这一行**不是** speedup，别当加速比读。
             "comparison": "no_npu_baseline"} for dt, vals in sorted(custom_only.items())]
    return out


def _shape_class_aggregate(rows, caseset, no_target=False):
    """按 caseset 的大小 shape 元数据做只读汇总；不参与任何裁决。

    新契约声明 ``perf_case_policy`` 后，每条性能行都必须有合法分类，且两桶计数要与生成期账本一致；
    缺失时产 ``problems``，绝不静默少算。旧 caseset 未声明 policy 且没有分类元数据时返回 ``None``，
    保持历史兼容。

    ``no_target=True``（无验收目标）时每桶与 overall 的 ``达标`` 一律写 ``None``——与 summary 同口径。
    否则顶层写 ``达标: null``、这张表却写 ``达标: 0``，Markdown 渲染出来就是「达标 0」，
    读者只会得出「一条都没达标」这个恰好相反的结论。``cases_scored`` / ``blocked`` 不受影响：
    它们数的是实采与挂起，与有没有目标无关。
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
        item["met"] += int(row.get("达标") is True)   # 严格 is True：None（无目标）不得被算作 0 以外的东西
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
                    "达标": (None if no_target else item["met"]), "blocked": item["blocked"],
                    "npu_us": npu_med, "baseline_us": base_med,
                    "speedup": (pair_base_med / pair_npu_med)
                    if _finite_pos(pair_npu_med) else None})
    npu_med, base_med = _median(all_npu), _median(all_base)
    pair_npu_med, pair_base_med = _median(all_paired_npu), _median(all_paired_base)
    overall = {"class": "overall", "cases": sum(x["cases"] for x in out),
               "planned_cases": sum(x["planned_cases"] for x in out),
               "cases_scored": sum(x["cases_scored"] for x in out),
               "达标": (None if no_target else sum(x["达标"] for x in out)),
               "blocked": sum(x["blocked"] for x in out),
               "npu_us": npu_med, "baseline_us": base_med,
               "speedup": (pair_base_med / pair_npu_med) if _finite_pos(pair_npu_med) else None}
    return {"by_shape_class": out, "overall": overall,
            "complete": not problems, "problems": problems}


def _attach_shape_report(report, caseset, no_target=False):
    """给已成型报告附加大小 shape 只读视图；异常只记 notes，不改 summary/status。"""
    try:
        agg = _shape_class_aggregate(report.get("per_case") or [], caseset, no_target=no_target)
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

    ⚠ ``达标 is None``（无验收目标，见 ``_NO_TARGET_NOTE``）的行**不是**未通过：没有目标就既没达标
    也没未达标。这类行只有在自己 blocked / 命中 exception 时才进本表；否则跳过。把它们当 failed
    列进来，就等于把「任务书没提要求」重新写成「一条都没达到要求」——本轮要修的正是这个。
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
        if (row.get("达标") is None and not row.get("blocked")
                and not row.get("exception")):
            continue                    # 无验收目标 → 该行既非达标也非未达标，不入未通过表
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
    # pc-3：target_ratio 严格化（非法/声明基线却缺 → invalid_config；绝不静默套 0.95 放行）。
    # 三态：有目标 (float, None) / invalid_config (None, err) / **无验收目标** (None, None)。
    tgt, tgt_err = _resolve_target_ratio(perf_spec)
    no_target = tgt is None and tgt_err is None

    # 缺/废基线（T8/gb-9）：期待外部 GPU 标杆但没给（或标杆被判废）→ 正规挂起；
    # 不静默 mock、不判 fail、baseline=None 不崩；据 baseline_blocked_status 落**正确**挂起码
    # （别把「有硬错的 baseline=None」等同「缺标杆」——gb-9）。
    if baseline is None:
        src = expect_source or perf_spec.get("baseline")
        status = baseline_blocked_status or "blocked_wait_gpu_benchmark"
        top_note = _BLOCKED_NOTE.get(status, f"缺/废基线 → 挂起（{status}）")
        if not perf_ids:
            return _no_perf_cases(spec, src, tgt, perf_spec,
                                  [top_note] + ([_NO_TARGET_NOTE] if no_target else []))
        ev = {e.get("case_id"): e for e in evidence["evidence"] if isinstance(e, dict) and e.get("case_id")}
        rows = []
        for cid in perf_ids:
            perf = (ev.get(cid) or {}).get("perf")
            rows.append({"case_id": cid,
                         "npu_us": (perf.get("us") if isinstance(perf, dict) else None),
                         "npu_scope": (perf.get("scope") if isinstance(perf, dict) else None),
                         # 无验收目标时这里同样记 None：缺基线 + 本来也没目标，写 False/0 就是
                         # 又一次把「没有要求」渲染成「零条达标」。
                         "达标": (None if no_target else False),
                         "blocked": False, "note": top_note})
        notes = [top_note]
        if no_target:   # 挂起态仍如实说明「本来就没有验收目标」，免得被读成「性能没达标才挂起」
            notes.append(_NO_TARGET_NOTE)
        if exc_note:
            notes.append(exc_note)
        report = _attach_shape_report(
            {"op": op, "baseline_source": src, "target_ratio": tgt,
             "per_case": rows, "notes": notes,
             "summary": {"perf_cases": len(rows), "达标": (None if no_target else 0),
                         "blocked": 0, "status": status}},
            caseset, no_target=no_target)
        # ⚠ 这条提前出口**照旧不挂任何聚合键**（`cases_above_threshold` / `cases_scored` / `by_dtype`）：
        #   它按定义一条可比测量都没有，挂上去会让「没数据」看起来像「数据是 0」。
        #   `test_early_exits_carry_no_aggregate_fields` 钉死这条，别顺手补一个 None 进来。
        return _attach_non_passing_cases(report, caseset, evidence, baseline)

    src = baseline.get("source")
    if not perf_ids:
        extra = ([_NO_TARGET_NOTE] if no_target else []) + ([exc_note] if exc_note else [])
        return _mark_non_acceptance(
            _no_perf_cases(spec, src, tgt, perf_spec, extra), baseline)

    # pc-3：有性能用例要判、但 target_ratio 非法/声明基线却缺 → invalid_config；不进 ratio、绝不全达标。
    # ⚠ 判据是 `tgt_err is not None`，**不是** `tgt is None`——后者现在还包含合法的「无验收目标」，
    #   用它会把「任务书没提性能要求」误判成「spec 配置坏了」。
    if tgt_err is not None:
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
    if no_target:   # 无验收目标：数照采、判定不做——措辞必须让人一眼看出「不是通过、也不是失败」
        notes.append(_NO_TARGET_NOTE)
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
        # 无验收目标 → `达标` 记 None（**不是 False**）：没有目标就没有「未达标」这个结论。
        # ratio 仍照记——它是实测导出的展示值，与有没有验收目标无关。
        met = None if no_target else (raw >= tgt)
        ratio = round(raw, 3)
        row = {"case_id": cid, "scope": bscope, "npu_us": npu,
               "baseline": {"source": src, "us": base}, "ratio": ratio, "达标": met}
        # T8 M6：消费 sub-policy 基线且达标 → 记风险（不允许干净 PASS）。
        # 无目标时也把 policy_risk 如实挂在行上（基线口径有风险是事实），但**不进 risk_flags**——
        # risk_flags 限定「达标了但基线口径可疑」，这里压根没有达标这个结论。
        if b.get("policy_risk") and (met is True or no_target):
            row["policy_risk"] = b["policy_risk"]
            if met is True:
                risk_flags.add("sub_policy_timing")
        # 小 shape 例外：仅对**判定为未达标**的行判资格（`met is None` 的无目标态不进——没有未达标可豁免）。
        # ⚠ 诚实记账：收窄「无目标」入口后这条 `is False` 在**当前契约下测不出差异**——带
        #   `small_shape_exception` 的 perf 块已被判 invalid_config，故 `no_target` 恒有 `exc is None`，
        #   `is False` 与 `not met` 结果相同（mutation 验证里 M8 是唯一没被逮住的一条，原因就是这个）。
        #   仍写 `is False` 是**防将来**：哪天有人放宽了入口，`not None` 会让无目标行凭空长出 exception。
        if met is False and exc is not None:
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

    # 无验收目标 → `达标` 计数记 None（**不是 0**）：0 会被读成「一条都没达标」。
    passed = None if no_target else sum(1 for r in rows if r.get("达标"))
    blocked = sum(1 for r in rows if r.get("blocked"))
    exc_rows = [r for r in rows if r.get("exception")]
    # `达标 is False` 而非 `not 达标`：无目标态的 None 不得被算成「真失败」。
    genuine_fail = sum(1 for r in rows
                       if r.get("达标") is False and not r.get("blocked") and not r.get("exception"))
    # status 优先级：incomparable > blocked(其它) > collected_no_target > fail(genuine) > exception > ok
    # 证据完整性问题（scope 不可比 / 缺证据缺基线）仍排在无目标之前：任务书不要求性能，
    # 不代表「采到的数据缺一半」这件事可以不报。
    if scope_mismatch:
        status = "blocked_incomparable_timing_scope"
    elif blocked or dup:
        status = "blocked"
    elif no_target:
        # 采了数但没有验收目标可判。**既不是 ok 也不是 fail**——放在 fail/exception/ok 之前，
        # 免得哪天有人改了上面的计数逻辑就悄悄落回 `ok`（那等于把「没要求」读成「通过了」）。
        status = _NO_TARGET_STATUS
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
    _attach_shape_report(report, caseset, no_target=no_target)
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
        # 「无目标」绝不等于「自动通过」：一条可比测量都没采到时（全行 blocked，status 落 blocked），
        # 额外点明「连数据都没有」，免得只读 status 的人以为「没要求所以一切正常」。
        if no_target and not agg["cases_scored"]:
            notes.append(_NO_TARGET_UNVERIFIED_NOTE)
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
