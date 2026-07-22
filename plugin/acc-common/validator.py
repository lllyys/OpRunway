"""Task 2 · validator — spec + caseset + evidence.json -> verdict.json（确定性裁决）。

ADR 0007：裁决只从这里出。ADR 0005：精度三层口径、放行只看 acceptance。职责：
0) **算子身份 + IO schema 锚定**（effective-standard-security finding #5）——`spec.op == caseset.op ==
   evidence.op`；每个 case 的 inputs(name/dtype)/attrs 须符合 spec IO 矩阵。防「另一算子/另一 dtype 的
   真通过 caseset+evidence 冒充」。
1) 契约校验——evidence 与 caseset **一一对应**（无缺/多/重复），否则整体 fail（防空 evidence 假通过）。
2) **核心原则**：凡决定「怎么判」的东西一律**从 spec 派生**；caseset/evidence 的声明只作「待与 spec 派生值
   核对的断言」，**绝不作派生输入**。故：
   · 比对 dtype `cdtype` **据 spec IO 矩阵派生**（`precision_policy.derive_output_dtype`），**不取** caseset 自
     声明的 `expected.compare_dtype`/`tolerance_policy_id` 后缀；随后强制 `expected.compare_dtype == 派生值`
     （不符 → contract fail）。整型→EXACT、选 AOT 哪一 dtype 行，全基于**真实输出 dtype**（finding #1/#2）。
   · 由 `spec_standard` + 派生 cdtype 复算 canonical policy，要求 spec-canonical / caseset.expected /
     evidence.precision **三处结构化 policy 全等**（standard + tolerance_policy_id + 结构化 policy + threshold
     digest 向后兼容）。仅比 caseset↔evidence 不够——两侧**同步放宽**即可绕过，故锚回 spec。
   · acceptance 层同样**据 spec 复算 canonical**（`precision_policy.resolve_acceptance`）：spec 声明 acceptance
     → 三处一致 + acceptance_metrics 必填；**spec 未声明 → caseset+evidence 一律不得私带 acceptance**
     （finding #3：防 T5「值被同步放宽」原洞在 acceptance 层换入口重演）。
3) 三层 pass 同出（canonical 字段名）：`catlass_compare_pass` / `standard_profile_pass` /
   `acceptance_precision_pass`；**放行只看 acceptance**；acceptance 过 & standard(平台底线) 不过
   → 该 case `risk=true`、overall=`passed_with_risk`（人工 CP）。ecosystem_mere_mare 单标杆不过
   → `uncertain`（NOT_SETTLED，不自动 fail）。**standard 或 acceptance 任一 uncertain → 至少 needs_review**
   （finding #9，不被 acceptance pass 吞掉）。
4) 按 case dims 只裁相关维度；性能维交 Task 3 perf_compare（此处 na）。
5) **输出形状对账**（C1 下游，用户 2026-07-22）：caseset 若声明显式输出形状（gen_cases 据 per-op
   `golden.py::out_shape(in_shapes, attrs)` 派生），则 evidence 侧一切「输出形状/规模」证据必须与它相符——
   不符即 fail 并报清形状差异，**绝不静默 reshape、绝不广播凑合**（那等于把形状 bug 藏起来）。
   caseset 未声明 → elementwise 缺省语义（输出同输入形状），行为与本条落地前**完全一致**。
6) **C4 · dtype 冲突以任务书为准**（用户 2026-07-22）：任务书要求、算子 `op_def` 不声明支持的 dtype 差额，
   由 spec 的 `task_pr_gaps` 以结构化条目 `kind=dtype_unsupported_by_op_def` 挂账，裁决落 `passed_with_gaps`。
   **反后门**：gap 须有据可查（`task_doc_ref` + `op_def_ref` + `op_def_dtypes`）、自洽、且**不得覆盖
   「算子实现了但跑挂了」**（该 dtype 若有真实用例在跑 → 拒绝挂账）。任一条不满足 → contract fail（不是忽略）。
overall 优先级：`contract/fail > needs_review(uncertain) > passed_with_risk > passed_with_gaps > pass`。
（注：`blocked` 由**门/编排层**裁定，validator **不产出** blocked——finding #11。）

judge_* 入口做 metric **schema 校验**（计数=非负整数、numel=正整数、MERE/MARE=有限非负浮点）：
非法/缺失/坏类型一律收敛到 fail（不进正常 pass、不抛异常崩溃，finding #8）。
顶层坏 JSON（cases/evidence 非列表、case 缺 id 等）→ 收敛 contract_problems + overall=fail，不下标崩溃（finding #10）。

**纯 stdlib**（judge 只做纯算术；误差分布复算在采集层 repo_adapter，本文件不 import numpy）。
"""
import json, math, operator as _operator, sys
import precision_policy


# ------------------------------------------------------ metric schema 校验 ---
def _is_nonneg_int(x):
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def _is_pos_int(x):
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def _is_finite_nonneg_num(x):
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x) and x >= 0)


# ------------------------------------------------------------ 纯算术 judge ---
def judge_ascendoptest(policy, metrics):
    """AscendOpTest 默认：坏点占比门——`bad_count <= numel * error_rate` 才过。
    schema：bad_count 非负整数、numel 正整数、error_rate 有限非负；任一非法 → fail（finding #8）。"""
    if not isinstance(metrics, dict) or "bad_count" not in metrics or "numel" not in metrics:
        got = sorted(metrics) if isinstance(metrics, dict) else type(metrics).__name__
        return "fail", f"metrics 缺 bad_count/numel（{got}）"
    bad, n = metrics["bad_count"], metrics["numel"]
    if not _is_nonneg_int(bad):
        return "fail", f"bad_count 非法（须非负整数）: {bad!r}"
    if not _is_pos_int(n):
        return "fail", f"numel 非法（须正整数，防空输出假通过）: {n!r}"
    err = policy.get("error_rate")
    if not _is_finite_nonneg_num(err):
        return "fail", f"policy.error_rate 非法（须有限非负）: {err!r}"
    return ("pass" if bad <= n * err else "fail"), f"bad_count={bad} vs numel*error_rate={n * err}"


def judge_mere_mare(policy, metrics):
    """生态 MERE/MARE（proposed/NOT_SETTLED）：`MERE<Th 且 MARE<max_ratio*Th` 才过；
    不过 → **uncertain**（单标杆失败非终判，ATK 双标杆本轮 out-of-scope）。
    schema：mere/mare 有限非负、threshold 有限正、max_ratio 有限非负；非法 → fail（finding #8）。"""
    if not isinstance(metrics, dict) or "mere" not in metrics or "mare" not in metrics:
        got = sorted(metrics) if isinstance(metrics, dict) else type(metrics).__name__
        return "fail", f"metrics 缺 mere/mare（{got}）"
    mere, mare = metrics["mere"], metrics["mare"]
    if not _is_finite_nonneg_num(mere) or not _is_finite_nonneg_num(mare):
        return "fail", f"MERE/MARE 非法（须有限非负）: mere={mere!r} mare={mare!r}"
    th, ratio = policy.get("threshold"), policy.get("max_ratio")
    if not (_is_finite_nonneg_num(th) and th > 0) or not _is_finite_nonneg_num(ratio):
        return "fail", f"policy.threshold/max_ratio 非法: th={th!r} ratio={ratio!r}"
    ok = (mere < th) and (mare < ratio * th)
    why = f"MERE={mere}<{th} 且 MARE={mare}<{ratio * th}（NOT_SETTLED）"
    return ("pass" if ok else "uncertain"), why


def judge_exact(policy, metrics):
    """exact：`exact_mismatch <= max_mismatch(0)` 才过。
    schema：exact_mismatch 非负整数、numel 正整数、max_mismatch 非负整数；非法 → fail（finding #8）。"""
    if not isinstance(metrics, dict) or "exact_mismatch" not in metrics:
        got = sorted(metrics) if isinstance(metrics, dict) else type(metrics).__name__
        return "fail", f"metrics 缺 exact_mismatch（{got}）"
    mism = metrics["exact_mismatch"]
    if not _is_nonneg_int(mism):
        return "fail", f"exact_mismatch 非法（须非负整数）: {mism!r}"
    n = metrics.get("numel")
    if not _is_pos_int(n):
        return "fail", f"numel 非法（须正整数，防空输出假通过）: {n!r}"
    mm = policy.get("max_mismatch", 0)
    if not _is_nonneg_int(mm):
        return "fail", f"policy.max_mismatch 非法（须非负整数）: {mm!r}"
    return ("pass" if mism <= mm else "fail"), f"exact_mismatch={mism}"


_JUDGES = {precision_policy.ASCENDOPTEST_DEFAULT: judge_ascendoptest,
           precision_policy.ECOSYSTEM_MERE_MARE: judge_mere_mare,
           precision_policy.EXACT: judge_exact}


def _judge_by_policy(policy, metrics):
    kind = policy.get("kind") if isinstance(policy, dict) else None
    if kind == precision_policy.BEHAVIORAL:
        return "na", "行为型：无数值 golden，精度维度 na"
    j = _JUDGES.get(kind)
    if j is None:
        return "fail", f"未知/缺失 policy.kind={kind!r}"
    return j(policy, metrics)


# ------------------------------------------------ 三处一致（spec 权威 canonical）---
def _case_input_dtypes(case):
    """取该 case 的 [(input name, dtype), ...]（仅作断言，交 derive_output_dtype 据 spec 校验+派生）。"""
    ins = case.get("inputs")
    if not isinstance(ins, list):
        raise ValueError("case 缺 inputs 列表（无法据 spec 派生输出 dtype）")
    out = []
    for inp in ins:
        if not isinstance(inp, dict) or not inp.get("name") or not inp.get("dtype"):
            raise ValueError(f"case input 项缺 name/dtype：{inp!r}")
        out.append((inp["name"], inp["dtype"]))
    return out


def _canonical(spec_standard, cdtype):
    """按 spec 权威 standard + **spec 派生 cdtype** 复算 canonical (policy, tpid)；不可复算 → 抛 ValueError。"""
    tpid = precision_policy.tolerance_policy_id(spec_standard, cdtype)
    pol = precision_policy.threshold_for(spec_standard, cdtype)
    return pol, tpid


def _precision_contract(eff_standard, cdtype, exp, ev_prec, canon_acc):
    """口径三处一致（**spec 派生 canonical**）——防 caseset+evidence 同步放宽。返回 (ok, why)。

    以 **eff_standard**（据 spec + spec 派生 cdtype/compare 复算）+ **spec 派生 cdtype**（非 caseset 自声明）
    复算 canonical policy，要求 canonical / caseset / evidence 三处 standard + tolerance_policy_id + 结构化 policy
    + threshold digest **全等**。acceptance 据 spec 复算的 `canon_acc`（None=spec 未声明）另校验（finding #3）。
    """
    if not isinstance(ev_prec, dict):
        return False, "evidence 缺 precision（非对象）"
    if not isinstance(exp.get("policy"), dict):
        return False, "caseset.expected 缺结构化 policy"
    if not isinstance(ev_prec.get("policy"), dict):
        return False, "evidence.precision 缺结构化 policy"
    try:
        canon_pol, canon_tpid = _canonical(eff_standard, cdtype)
    except (ValueError, KeyError) as ex:
        return False, f"无法据 spec 复算 canonical（standard={eff_standard} dtype={cdtype}）：{ex}"
    canon_digest = precision_policy.threshold_digest(canon_pol)
    for side, obj in (("caseset", exp), ("evidence", ev_prec)):
        if obj.get("standard") != eff_standard:
            return False, f"{side}.standard={obj.get('standard')} ≠ 有效标准 {eff_standard}"
        if obj.get("tolerance_policy_id") != canon_tpid:
            return False, (f"{side}.tolerance_policy_id={obj.get('tolerance_policy_id')} "
                           f"≠ spec-canonical {canon_tpid}")
        if obj.get("policy") != canon_pol:
            return False, f"{side}.policy 与 spec-canonical 不一致（放宽/漏字段/多字段）"
        if obj.get("threshold") != canon_digest:
            return False, (f"{side}.threshold(digest)={obj.get('threshold')} "
                           f"≠ spec-canonical {canon_digest}")
    # acceptance 层：据 **spec** 复算 canonical（finding #3），非仅比 caseset↔evidence。
    if canon_acc is not None:                                 # spec 声明了 acceptance → 三处全等
        canon_acc_pol, canon_acc_tpid = canon_acc
        for side, obj in (("caseset", exp), ("evidence", ev_prec)):
            if obj.get("acceptance_policy") != canon_acc_pol:
                return False, f"{side}.acceptance_policy 与 spec-canonical 不一致（放宽/漏字段/多字段）"
            if obj.get("acceptance_tolerance_policy_id") != canon_acc_tpid:
                return False, f"{side}.acceptance_tolerance_policy_id 与 spec-canonical 不一致"
        if not isinstance(ev_prec.get("acceptance_metrics"), dict):
            return False, "spec 声明 acceptance 但 evidence 缺 acceptance_metrics（必填）"
    else:                                                    # spec 未声明 → 两侧一律不得私带（防 T5 洞重演）
        for side, obj in (("caseset", exp), ("evidence", ev_prec)):
            for k in ("acceptance_policy", "acceptance_tolerance_policy_id"):
                if obj.get(k) is not None:
                    return False, f"spec 未声明 acceptance，但 {side} 私带 {k}（额外口径，拒绝）"
        if ev_prec.get("acceptance_metrics") is not None:
            return False, "spec 未声明 acceptance，但 evidence 私带 acceptance_metrics（拒绝）"
    return True, ""


# ---- dims 受控词表（finding #4）：只认 功能/精度/性能；空/未知/数值 case 缺精度 → contract fail ----
_DIM_VOCAB = frozenset({"功能", "精度", "性能"})


def _dims_contract(dims, vm, allow_na=False):
    """校验 case.dims（finding #4）。返回 err 字符串或 None（合法）。

    · 非列表/空 → 非法（防 dims=[] 抹掉裁决维度让 na-only 假通过）；
    · 含受控词表外 token → 非法（防伪造维度）；
    · verify_mode ∈ {exact, numerical} 且**非纯性能 case**（dims != {性能}）→ 必须含「精度」（数值 case 不可漏裁精度）。
    · `allow_na=True`（§1.4 空 Tensor 功能用例，numel=0 无精度可判）→ 豁免「必含精度」；仍校非空+词表。
      调用方须先确认确为真空 Tensor（防伪造 na 跳精度）。
    """
    if not isinstance(dims, list) or not dims:
        return f"dims 非列表或空（{dims!r}）——数值/功能维度被抹，拒绝（防 na-only 假通过）"
    unknown = set(dims) - _DIM_VOCAB
    if unknown:
        return f"dims 含受控词表外 token {sorted(unknown)}（仅 {sorted(_DIM_VOCAB)}）"
    if allow_na:
        return None
    if vm in ("exact", "numerical") and set(dims) != {"性能"} and "精度" not in dims:
        return f"verify_mode={vm} 的数值 case 必须含「精度」维（dims={dims}，纯性能 case 例外）"
    return None


# ---- 严格真空判定（codex #4，与门口径一致）：拒 shape:[false]/[0.0] 被 `0 in shape` 蒙混 ----
def _strict_empty_shape(shape):
    if not isinstance(shape, list) or not shape:
        return False
    for d in shape:
        if not isinstance(d, int) or isinstance(d, bool) or d < 0:
            return False
    return 0 in shape                    # 全为非负 int 时，0 in 仅匹配整数 0


def _case_strict_empty(c):
    return isinstance(c, dict) and any(
        isinstance(it, dict) and _strict_empty_shape(it.get("shape"))
        for it in (c.get("inputs") or []))


# ============================================ 输出形状对账（C1 下游，2026-07-22）============
# caseset 侧「期望输出形状」的候选键：gen_cases 据 per-op `golden.py::out_shape(in_shapes, attrs)` 写入。
# 主名 `out_shape`（= C1 里那个函数名）；另收两个同义别名，防上游改名后本门静默失效（宽松探测、严格对账）。
_EXP_SHAPE_KEYS = ("out_shape", "output_shape", "expected_out_shape")
# evidence 侧「实际输出形状」的候选键（precision 层与 precision.provenance 层都收）。
# ⚠ 现阶段采集层（repo_adapter/catlass_adapter）**尚未**产这些字段——故本对账当下主要经 numel 生效；
#   采集层补上形状后，逐维对账自动生效（本函数不需再改）。此边界写在这里，别把它说成「已逐维验形」。
_EV_SHAPE_KEYS = ("out_shape", "output_shape", "actual_out_shape", "npu_out_shape")


def _norm_shape(v):
    """规范化形状 → 非负 int 列表；非法 → None（`[]` = 0 维标量，合法）。

    维度接受任何实现 `__index__` 的整数——**validator 是 stdlib-only、不 import numpy**，若按
    `isinstance(d, int)` 判会把 `np.int64` 维当坏形状硬拒（in-memory 调用路径下 caseset 可能带 numpy 标量）。
    仍严格拒 bool / 浮点 / 字符串 / 负数。"""
    if not isinstance(v, (list, tuple)):
        return None
    out = []
    for d in v:
        if isinstance(d, (bool, float)):
            return None
        try:
            i = _operator.index(d)
        except TypeError:
            return None
        if i < 0:
            return None
        out.append(i)
    return out


def _is_shape(v):
    return _norm_shape(v) is not None


def _numel_of(shape):
    n = 1
    for d in shape:
        n *= d
    return n


def _shape_decl(obj, keys, where):
    """从 obj 的候选键里取**唯一**形状声明。返回 (shape|None, err|None)。

    多个候选键并存且值不同 → err（**不静默挑一个**）；值不是合法形状 → err（不猜、不容忍坏形状）。"""
    if not isinstance(obj, dict):
        return None, None
    found = {k: obj[k] for k in keys if obj.get(k) is not None}
    if not found:
        return None, None
    norm = {}
    for k, v in found.items():
        s = _norm_shape(v)
        if s is None:
            return None, f"{where}.{k}={v!r} 非法（须非负 int 形状列表）"
        norm[k] = s
    vals = list(norm.values())
    if any(v != vals[0] for v in vals[1:]):
        return None, f"{where} 多处形状声明互相矛盾 {norm}（不静默择一）"
    return vals[0], None


def _evidence_shape(ev_prec):
    """evidence 自报的**实际输出形状**：precision 层 + precision.provenance 层一并探测，两层不一致 → err。"""
    top, err = _shape_decl(ev_prec, _EV_SHAPE_KEYS, "evidence.precision")
    if err:
        return None, err
    prov = ev_prec.get("provenance") if isinstance(ev_prec, dict) else None
    sub, err = _shape_decl(prov, _EV_SHAPE_KEYS, "evidence.precision.provenance")
    if err:
        return None, err
    if top is not None and sub is not None and top != sub:
        return None, f"evidence 两层输出形状声明不一致 precision={top} / provenance={sub}"
    return (top if top is not None else sub), None


def _broadcast_shape(shapes):
    """numpy 广播规则的纯 py 实现（缺省 elementwise 语义用）：右对齐、每维 1 可广播到 N、冲突 → None。"""
    if not shapes:
        return None
    out_rev, maxlen = [], max(len(s) for s in shapes)
    for i in range(maxlen):
        dim = 1
        for s in shapes:
            if i >= len(s):
                continue
            d = s[len(s) - 1 - i]
            if d == 1:
                continue
            if dim == 1:
                dim = d
            elif dim != d:
                return None
        out_rev.append(dim)
    return list(reversed(out_rev))


def _out_shape_contract(c, exp, ev_prec):
    """输出形状对账（C1 下游）。返回 err 字符串或 None（合法）。

    · caseset `expected.out_shape` 声明了 → 它就是**期望输出形状**（由 per-op `golden.py::out_shape` 派生）；
    · caseset 未声明、但 evidence 自报了实际输出形状 → 用**缺省 elementwise 语义**（输入广播）兜底核对，
      不让一个无人对账的自报形状溜过去（推不出缺省期望 → fail-closed 拒，不放行）；
    · 两侧都没声明 → None（行为与本条落地前完全一致，现有 4 份样例零变更）。

    对账两层：① evidence 自报实际形状须与期望**逐维相等**；② evidence 的 `provenance.numel` /
    `metrics.numel`（现成的规模证据）须 == 期望形状元素数。任一不符 → 返回 err（调用方判 fail）。
    **不 reshape、不广播凑合**——形状不符就是失败，不是「对齐一下再比」。"""
    # 宽松探测两处落点：`case.expected.out_shape`（主）与 `case.out_shape`（备）——W1 的字段落点未最终对齐，
    # 两处都收；两处都写且不一致 → 拒（不静默择一）。
    exp_shape, err = _shape_decl(exp, _EXP_SHAPE_KEYS, "caseset.expected")
    if err:
        return f"：{err}"
    case_shape, err = _shape_decl(c, _EXP_SHAPE_KEYS, "caseset.case")
    if err:
        return f"：{err}"
    if exp_shape is not None and case_shape is not None and exp_shape != case_shape:
        return (f"：caseset 两处期望输出形状不一致 expected={exp_shape} / case={case_shape}（不静默择一）")
    if exp_shape is None:
        exp_shape = case_shape
    act_shape, err = _evidence_shape(ev_prec)
    if err:
        return f"：{err}"
    if exp_shape is None:
        if act_shape is None:
            return None                      # 缺省 elementwise 语义，无显式期望可对账
        ins = c.get("inputs") if isinstance(c, dict) else None
        shapes = [_norm_shape(it["shape"]) for it in (ins or [])
                  if isinstance(it, dict) and _is_shape(it.get("shape"))]
        if not ins or len(shapes) != len(ins):
            return (f"：evidence 自报输出形状 {act_shape}，但 caseset 未声明期望且输入形状不可用"
                    "（无从对账，拒——不接受无人对账的自报形状）")
        exp_shape = _broadcast_shape(shapes)
        if exp_shape is None:
            return (f"：evidence 自报输出形状 {act_shape}，但 caseset 未声明期望、输入形状 {shapes} 无法广播"
                    "出缺省期望（无从对账，拒）")
    if act_shape is not None and act_shape != exp_shape:
        return (f"：实际输出形状 {act_shape} ≠ 期望 {exp_shape}"
                "（形状不符即失败——不静默 reshape、不广播凑合）")
    want = _numel_of(exp_shape)
    for label, holder in (("provenance.numel", ev_prec.get("provenance") if isinstance(ev_prec, dict) else None),
                          ("metrics.numel", ev_prec.get("metrics") if isinstance(ev_prec, dict) else None)):
        if not isinstance(holder, dict) or holder.get("numel") is None:
            continue
        n = holder["numel"]
        if not (isinstance(n, int) and not isinstance(n, bool)):
            return f"：evidence {label}={n!r} 非整数（输出规模无从对账）"
        if n != want:
            return (f"：evidence {label}={n} ≠ 期望输出形状 {exp_shape} 的元素数 {want}"
                    "（输出规模与期望形状不符——形状 bug 不许被静默吞掉）")
    return None


# ==================================== C4 · dtype 冲突（任务书 vs op_def），2026-07-22 ========
# 用户拍板：**任务书为准**——任务书声明的 dtype 全集是需求，算子 `op_def` 支持不了的差额入 `task_pr_gaps`、
# 裁决落 `passed_with_gaps`。「没实现」是**发现**、不是借口（承 canon `task-spec-authoritative-over-pr`）。
DTYPE_GAP_KIND = "dtype_unsupported_by_op_def"


def _structured_dtype_gaps(container):
    """从 `task_pr_gaps` 里挑出结构化的 dtype 冲突条目（历史的自由文本条目原样忽略、不报错）。"""
    raw = container.get("task_pr_gaps") if isinstance(container, dict) else None
    if not isinstance(raw, list):
        return []
    return [g for g in raw if isinstance(g, dict) and g.get("kind") == DTYPE_GAP_KIND]


def _check_dtype_gap(g, i, required, actual_dtypes):
    """单条 dtype 冲突 gap 的「有据可查」硬校；返回 problem 列表（空 = 合法）。

    ⚠ 这条**绝不能**变成「宣称有 gap 就免检」的后门，故四道硬校缺一即拒（拒 = 记 contract problem →
    overall=fail，**不是**静默忽略该 gap）：
      ① **有据**——`task_doc_ref`（任务书原文定位）+ `op_def_ref`（op_def 出处）+ `op_def_dtypes`
         （op_def 实际声明的支持集）三者必填且类型正确；没有出处的 gap 一律不认。
      ② **自洽**——声称「op_def 不支持」的 dtype 不得同时出现在自报的 `op_def_dtypes` 里。
      ③ **不得覆盖真失败**——该 dtype 若**有真实用例在跑**（实测集含之），说明它被实现且被测了，属
         「算子实现了但跑挂了」，必须走精度/功能裁决；用「op_def 不支持」罩住 = 拿没实现当借口 → 拒。
         **这条就是「没实现」与「实现了但跑挂了」的判别式**：前者压根造不出用例，后者一定有用例+证据。
      ④ **在需求内**——spec 声明了 `dtype_required` 全集时，gap 的 dtype 须确在任务书要求内
         （给任务书没要求的 dtype 挂账 = 无据）。
    """
    tag = f"task_pr_gaps[{i}]({DTYPE_GAP_KIND})"
    dts = g.get("dtypes")
    if not (isinstance(dts, list) and dts and all(isinstance(x, str) and x for x in dts)):
        return [f"{tag}: dtypes 须为非空 dtype 字符串列表（{dts!r}）"]
    probs = []
    for k in ("task_doc_ref", "op_def_ref"):
        v = g.get(k)
        if not (isinstance(v, str) and v.strip()):
            probs.append(f"{tag}: 缺 {k}（gap 须有据可查：指向任务书原文 / op_def 出处，"
                         "否则就成了『宣称有 gap 就免检』）")
    od = g.get("op_def_dtypes")
    if not (isinstance(od, list) and all(isinstance(x, str) and x for x in od)):
        probs.append(f"{tag}: op_def_dtypes 须为 dtype 字符串列表（op_def 实际声明的支持集，供交叉核验）")
    else:
        contra = sorted(set(dts) & set(od))
        if contra:
            probs.append(f"{tag}: {contra} 既称 op_def 不支持、又列在自报 op_def_dtypes 里（自相矛盾·伪造 gap）")
    ran = sorted(set(dts) & set(actual_dtypes))
    if ran:
        probs.append(f"{tag}: {ran} 有真实用例在跑——属「算子实现了但跑挂了」，须走精度/功能裁决，"
                     "不得用「op_def 不支持」的 gap 罩住")
    if required is not None:
        outside = sorted(set(dts) - set(required))
        if outside:
            probs.append(f"{tag}: {outside} 不在任务书 dtype_required {sorted(required)} 内"
                         "（为任务书没要求的 dtype 挂账·gap 无据）")
    return probs


def _dtype_gaps(spec, caseset, cases):
    """据 **spec**（权威）取 dtype 冲突 gap，并要求 caseset 透传**逐条一致**；逐条硬校。
    返回 (合法 gap 列表, contract problem 列表)。

    权威在 spec 而非 caseset——否则「往 caseset 里私塞一条 gap」就能自助免检（同 finding #1/#2 的纪律：
    caseset 的声明只作待核对的断言，绝不作派生输入）。"""
    spec_gaps = _structured_dtype_gaps(spec)
    cs_gaps = _structured_dtype_gaps(caseset)
    if cs_gaps != spec_gaps:
        return [], [f"caseset 的 {DTYPE_GAP_KIND} 条目与 spec 不一致"
                    f"（spec {len(spec_gaps)} 条 / caseset {len(cs_gaps)} 条，权威在 spec；"
                    "拒绝 caseset 私改/私塞 gap 自助免检）"]
    if not spec_gaps:
        return [], []
    req = spec.get("dtype_required")
    required = req if isinstance(req, list) and all(isinstance(x, str) for x in req) else None
    actual = set()
    for c in cases:
        for it in ((c.get("inputs") or []) if isinstance(c, dict) else []):
            if isinstance(it, dict) and isinstance(it.get("dtype"), str) and it["dtype"]:
                actual.add(it["dtype"])
    ok, probs = [], []
    for i, g in enumerate(spec_gaps):
        errs = _check_dtype_gap(g, i, required, actual)
        if errs:
            probs.extend(errs)
        else:
            ok.append(g)
    return ok, probs


# ------------------------------------------------------- 空 per_case 的骨架 ---
def _empty_row(cid):
    return {"case_id": cid, "功能": "na", "精度": "na", "性能": "na",
            "catlass_compare_pass": "na", "standard_profile_pass": "na",
            "acceptance_precision_pass": "na", "risk": False,
            "判据": "", "evidence_ref": cid}


def _verdict(op, vm, spec_standard, problems, per, gaps=None):
    fails = [p for p in per if p["功能"] == "fail" or p["精度"] == "fail"]
    # finding #9：standard 或 acceptance 任一 uncertain 都要计入 needs_review（不被 acceptance pass 吞）。
    unc_ids, seen = [], set()
    for p in per:
        if (p["精度"] == "uncertain" or p["standard_profile_pass"] == "uncertain"
                or p["acceptance_precision_pass"] == "uncertain"):
            if p["case_id"] not in seen:
                seen.add(p["case_id"]); unc_ids.append(p["case_id"])
    # finding #4：区分 na（未裁）与 pass（裁过且过）——**应裁精度却停 na** 的数值 case 不得贡献 overall=pass；
    # 计入 needs_review（不静默放过）。`_prec_expected` 由主循环按 dims 标注，随后从行内剥除（不进产物）。
    # （已 fail 的 case 不重复计入——它已在 fails/overall=fail 里。）
    for p in per:
        if (p.pop("_prec_expected", False) and p["精度"] == "na" and p["功能"] != "fail"
                and p["case_id"] not in seen):
            seen.add(p["case_id"]); unc_ids.append(p["case_id"])
    risks = [p["case_id"] for p in per if p.get("risk")]
    catlass_na = [p["case_id"] for p in per if p["catlass_compare_pass"] == "na"]
    gaps = list(gaps or [])
    if problems or fails:
        overall = "fail"
    elif unc_ids:
        overall = "needs_review"
    elif risks:
        overall = "passed_with_risk"
    elif gaps:
        # C4：全过、但任务书要求的 dtype 有一部分 op_def 压根不支持 → 不是干净 pass，是「带发现的通过」。
        # 排在 passed_with_risk 之后：risk 要人工 CP（更强），gap 只需如实上报，不吞掉更严的终态。
        overall = "passed_with_gaps"
    else:
        overall = "pass"
    return {"op": op, "verify_mode": vm, "standard": spec_standard,
            "contract_problems": problems, "per_case": per,
            "catlass_compare_na": catlass_na,
            "overall": {"verdict": overall, "uncertain": unc_ids, "risk": risks,
                        # gap 原样带出处一起进产物——「有据可查」要能被下游报告/人工复核直接读到。
                        "gaps": gaps,
                        "requires_human_cp": overall == "passed_with_risk",
                        "counts": {"total": len(per), "fail": len(fails),
                                   "uncertain": len(unc_ids), "risk": len(risks),
                                   "gaps": len(gaps),
                                   "contract_problems": len(problems)}}}


# --------------------------------------------------------------------- 裁决 ---
def validate(spec, caseset, evidence):
    # finding #10：顶层最小 schema 校验——坏 JSON/类型错收敛 contract_problems + overall=fail，绝不下标崩溃。
    problems = []
    if not isinstance(spec, dict):
        return _verdict("?", None, None, ["spec 非对象（无法裁决）"], [])
    vm = spec.get("verify_mode")
    op = spec.get("op", "?")
    # finding #5：算子身份三处锚定——spec.op == caseset.op == evidence.op（防另一算子的真通过产物冒充）。
    caseset_op = caseset.get("op") if isinstance(caseset, dict) else None
    evidence_op = evidence.get("op") if isinstance(evidence, dict) else None
    if caseset_op != op:
        problems.append(f"caseset.op={caseset_op!r} ≠ spec.op={op!r}（算子身份不符，防冒充）")
    if evidence_op != op:
        problems.append(f"evidence.op={evidence_op!r} ≠ spec.op={op!r}（算子身份不符，防冒充）")
    cases = caseset.get("cases") if isinstance(caseset, dict) else None
    ev_list = evidence.get("evidence") if isinstance(evidence, dict) else None
    if not isinstance(cases, list) or not cases:
        problems.append("caseset.cases 缺失/非列表/空（无用例可裁）")
    if not isinstance(ev_list, list):
        problems.append("evidence.evidence 缺失或非列表")
    if not isinstance(cases, list) or not cases or not isinstance(ev_list, list):
        return _verdict(op, vm, None, problems, [])  # 结构性致命 → 直接出 fail verdict（不崩）

    try:
        spec_standard = precision_policy.select_standard(spec)
    except ValueError as ex:
        spec_standard = None
        problems.append(f"spec 精度标准无法解析：{ex}")

    if vm not in ("exact", "numerical", "behavioral"):
        problems.append(f"spec.verify_mode={vm!r} 非法（仅 exact/numerical/behavioral）")

    # C4：dtype 冲突 gap（任务书要求 − op_def 支持）——据 **spec** 取、caseset 须透传一致、逐条硬校出处。
    # 校不过 → 进 contract problems（overall=fail），**不是**忽略该 gap：伪造的 gap 必须被拒得响亮。
    gaps, gap_problems = _dtype_gaps(spec, caseset, cases)
    problems.extend(gap_problems)

    # finding #5：spec io=='attr' 名集——case.attrs 的 key 须 ⊆ 此集（防伪造 attr 冒充覆盖）。
    spec_params = spec.get("params") if isinstance(spec.get("params"), list) else []
    attr_names = {p["name"] for p in spec_params
                  if isinstance(p, dict) and p.get("io") == "attr" and p.get("name")}

    # case_ids / ev_ids：逐项抗坏（缺 id / 非对象 → contract 问题，不崩）
    case_ids = []
    for i, c in enumerate(cases):
        if not isinstance(c, dict) or not c.get("id"):
            problems.append(f"caseset.cases[{i}] 非对象或缺 id")
            continue
        case_ids.append(c["id"])
    ev_by_id, ev_ids = {}, []
    for i, e in enumerate(ev_list):
        if not isinstance(e, dict) or not e.get("case_id"):
            problems.append(f"evidence[{i}] 非对象或缺 case_id")
            continue
        ev_ids.append(e["case_id"])
        ev_by_id[e["case_id"]] = e

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

    per = []
    for c in cases:
        if not isinstance(c, dict) or not c.get("id"):
            continue                                 # 已在上文记 contract 问题
        cid = c["id"]
        dims = c.get("dims") or []
        exp = c.get("expected") if isinstance(c.get("expected"), dict) else {}
        row = _empty_row(cid)
        e = ev_by_id.get(cid)
        if e is None:
            row.update(功能="fail", 判据="evidence 缺此 case")
            per.append(row); continue
        ev_prec = e.get("precision") or {}
        # §1.4 空 Tensor 功能用例（Layer A：expected.compare=na、dims=["功能"]）→ 判 na、不判精度。
        # ⚠ 防伪造：compare=na 仅对**真空 Tensor**（某输入 shape 含 0）合法；否则=正常 case 冒充 na
        #   跳精度 → fail（不信自报，与 gate-must-check-effective-object 同纪律）。
        if exp.get("compare") == "na":
            if not _case_strict_empty(c):    # codex #4：严格真空（拒 shape:[false]/[0.0] 伪造）
                row.update(功能="fail", 判据="expected.compare=na 但非严格真空 Tensor（伪造 na 跳精度，拒绝）")
                per.append(row); continue
            dim_err = _dims_contract(dims, vm, allow_na=True)
            if dim_err:
                row.update(功能="fail", 判据=f"dims 契约{dim_err}")
                per.append(row); continue
            row["功能"] = "pass" if e.get("status") in ("ok", "skipped_empty") else "fail"
            row["精度"] = "na"; row["性能"] = "na"
            row["判据"] = "空Tensor 功能用例（numel=0，精度 na）"
            per.append(row); continue
        # finding #4：dims 受控词表——空/未知/数值 case 缺「精度」→ contract fail（防 na-only 假通过）。
        dim_err = _dims_contract(dims, vm)
        if dim_err:
            row.update(功能="fail", 判据=f"dims 契约{dim_err}")
            per.append(row); continue
        # finding #5：case.attrs key 须 ⊆ spec attr 名集（防伪造 attr 冒充覆盖）。
        bad_attrs = set(c.get("attrs") or {}) - attr_names
        if bad_attrs:
            row.update(功能="fail", 判据=f"case.attrs 含 spec 未声明 attr {sorted(bad_attrs)}（IO schema 不符）")
            per.append(row); continue
        # C1 下游 · 输出形状对账：形状不符 → fail-closed，报清「实际 vs 期望」。放在精度口径校验**之前**——
        # 形状都对不上时，任何误差数字都无意义，没必要也不应该继续按阈值判。
        shape_err = _out_shape_contract(c, exp, ev_prec)
        if shape_err:
            row.update(功能="fail", 判据=f"输出形状对账{shape_err}")
            per.append(row); continue
        # 是否**应裁精度**（数值/exact 的非纯性能 case）——供 _verdict 区分 na 与 pass（finding #4）。
        row["_prec_expected"] = (vm in ("exact", "numerical") and "精度" in dims)
        # 2) 口径一致性（spec 权威）：verify_mode + 标准 + policy 三处一致
        if exp.get("verify_mode") != vm:
            row.update(功能="fail", 判据=f"case.verify_mode={exp.get('verify_mode')} ≠ spec {vm}")
            per.append(row); continue
        if spec_standard is None:
            row.update(功能="fail", 判据="spec 精度标准不可解析，无法据 spec 校验口径")
            per.append(row); continue
        # 核心原则（finding #1/#2/#5）：cdtype **据 spec IO 矩阵派生**（校验 case.inputs name/dtype ∈ spec 允许集），
        # **不取** caseset 自声明的 compare_dtype/tpid 后缀；随后强制 expected.compare_dtype == 派生值。
        try:
            cdtype = precision_policy.derive_output_dtype(spec, _case_input_dtypes(c))
        except (ValueError, KeyError) as ex:
            row.update(功能="fail", 判据=f"IO schema/派生输出 dtype 失败：{ex}")
            per.append(row); continue
        if exp.get("compare_dtype") != cdtype:
            row.update(功能="fail",
                       判据=f"expected.compare_dtype={exp.get('compare_dtype')!r} ≠ spec 派生输出 dtype {cdtype!r}"
                            "（比对 dtype 谎报——据 spec 派生值核对不符，拒绝）")
            per.append(row); continue
        # T7：per-case **有效标准**（int→EXACT；bf16 靠 compare=exact_equal 收紧；余=spec 标准）。据**派生 cdtype**
        # + compare 复算，故 caseset 谎报别的 dtype/口径也过不了（int→EXACT 基于真实输出 dtype，防换入口绕过）。
        eff_std = precision_policy.effective_standard(spec_standard, cdtype, exp.get("compare"))
        if exp.get("standard") != eff_std:
            row.update(功能="fail",
                       判据=f"standard 与有效标准不一致 eff={eff_std}/case={exp.get('standard')}"
                            f"（spec={spec_standard} dtype={cdtype} compare={exp.get('compare')}）")
            per.append(row); continue
        # acceptance canonical **据 spec 复算**（None=spec 未声明 → caseset+evidence 不得私带，finding #3）。
        try:
            canon_acc = precision_policy.resolve_acceptance(spec, eff_std, cdtype)
        except (ValueError, KeyError) as ex:
            row.update(功能="fail", 判据=f"无法据 spec 复算 canonical acceptance：{ex}")
            per.append(row); continue
        ok, why = _precision_contract(eff_std, cdtype, exp, ev_prec, canon_acc)
        if not ok:
            row.update(功能="fail", 判据=f"精度口径{why}")
            per.append(row); continue
        # 3) 按 dims 裁维度
        whys = []
        if "功能" in dims:
            row["功能"] = "pass" if e.get("status") == "ok" else "fail"
        if "精度" in dims:
            policy = exp["policy"]
            # ⚠ 边界（effective-standard-security-7）：validator **本身**仍全信 evidence.precision.metrics 的数值
            #   （本文件只据 spec 复算「用哪套阈值/口径」判，防口径放宽，不证明 metrics 来自真实产物）。
            #   metrics↔产物的绑定已由**门 gate_task2（A 方案）**补上：门按 provenance 读磁盘 golden/out、校 sha256、
            #   依 caseset policy 重算 metrics 并逐字段比对，故「伪造 bad_count=0 而产物不动」会被门判 FAILED。
            #   **但仍有未防的一层**（诚实）：A 只绑定「metrics↔这两文件」，**不绑定**「文件↔一次真 NPU 跑测」——
            #   同控产物+evidence 者把 out 写成 golden 副本即得「真的」bad_count=0（未测 NPU）。产物↔真机来源须
            #   OPRUNWAY_DONE 哨兵 / raw log hash / msprof 输出绑定（本轮不做）；别声称已彻底防伪造。
            metrics = ev_prec.get("metrics")
            if not isinstance(metrics, dict):
                row.update(精度="fail", 判据="evidence 缺 precision.metrics（误差分布未复算）")
                per.append(row); continue
            std_state, std_why = _judge_by_policy(policy, metrics)
            acc_policy = exp.get("acceptance_policy")
            if acc_policy:
                acc_metrics = ev_prec.get("acceptance_metrics", metrics)
                acc_state, acc_why = _judge_by_policy(acc_policy, acc_metrics)
            else:                                   # 无 acceptance_policy → 继承 standard
                acc_state, acc_why = std_state, std_why
            row["catlass_compare_pass"] = "na"      # mock/new_example：仓内无 catlass smoke
            row["standard_profile_pass"] = std_state
            row["acceptance_precision_pass"] = acc_state
            row["精度"] = acc_state                 # 放行只看 acceptance
            row["risk"] = (acc_state == "pass" and std_state == "fail")
            whys.append(f"acceptance:{acc_why}" + (f" | standard:{std_why}" if acc_policy else ""))
            if row["risk"]:
                whys.append("⚠risk：acceptance 过但平台底线(standard) 不过 → 需人工 CP")
        row["判据"] = "；".join(whys) if whys else f"dims={dims}（性能交 perf_compare）"
        per.append(row)

    return _verdict(op, vm, spec_standard, problems, per, gaps)


def main(argv):
    out_path = argv[3]
    try:
        spec = json.load(open(argv[0], encoding="utf-8"))
        caseset = json.load(open(argv[1], encoding="utf-8"))
        evidence = json.load(open(argv[2], encoding="utf-8"))
        verdict = validate(spec, caseset, evidence)
    except Exception as ex:                          # finding #10：任何异常也要出 verdict.json（overall=fail）
        verdict = _verdict("?", None, None, [f"validator 载入/裁决异常：{type(ex).__name__}: {ex}"], [])
    json.dump(verdict, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    o = verdict["overall"]
    print(f"[validator] overall={o['verdict']} {o['counts']} -> {out_path}")
    return 0 if o["verdict"] != "fail" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
