"""机器可校验验收门（P0）——三级门，只认结构化机读证据，不认 md/LOG 文字。

把「验证-才-信」从纪律变成**代码硬门**：由 run_workflow / op-acceptance 在出裁决前强制跑，
任一 stage `FAILED` → 不推进、不生成裁决。核心防「跑子集报 100%」：evidence/perf 必须覆盖
caseset 全部用例、id 一一对应、每 (dtype,shape) 计数不缺。

**门是完整性门**：只保证证据可信+完整（不重判精度/性能 pass-fail，那是 validator/perf_compare 的活）。
**抗坏输入**：坏/缺字段的产物 → 累计成 error、判 FAILED，绝不崩溃、绝不静默放过。

A 方案（gate_task2 · evidence↔产物 provenance 绑定）：除「阈值/口径三处一致」外，再按 evidence 的 provenance
读磁盘产物（golden/out .npy）、先校 sha256、再依 caseset policy **重算** metrics 并与 evidence 自报值逐字段比对，
堵「伪造 bad_count=0 直接 pass」的自报数字洞。这仍属**证据可信**（验证「数字是否真从产物算出」），不重判 verdict。
⚠ 已知边界（诚实）：A 只证「metrics 由产物算出」，**不证**「产物来自真 NPU 跑测」——产物↔真机绑定须
OPRUNWAY_DONE 哨兵 / raw log hash / msprof 输出绑定（本轮不做）；别把本门说成「已防伪造」。

用法: python3 validate_acceptance_state.py --stage task1|task2|task3 --dir <reports 产物目录>
只读、零硬编码。打印累积 error（非 fail-fast）+ 末行 `STATUS: PASSED|FAILED`，exit 0/1。
（task1/task3 为 stdlib；**task2 的 A 方案重算按需惰性 import numpy + precision_policy**——numpy 缺失即 FAILED、
不静默 skip。validator.py 仍 stdlib-only、不受本门引入 numpy 影响。）
"""
import argparse, hashlib, json, math, os, statistics, sys
from collections import Counter

# T6/T8 扩枚举：exception=小shape例外(合法放行需交叉校验)；
# blocked_wait_gpu_benchmark=缺外部 GPU 标杆正规挂起；blocked_incomparable_timing_scope=双边口径不可比。
_PERF_STATUS = {"ok", "no_perf_cases", "blocked", "fail",
                "exception", "blocked_wait_gpu_benchmark", "blocked_incomparable_timing_scope",
                # High#2（2026-07-24）：验收通路缺**真实**基线（采集端未接通）→ 正规挂起。
                # 它取代的是原来那条「静默 mock 兜底」的路——mock 基线在验收通路上等于冒充达标。
                "blocked_wait_real_baseline"}
# gt3-1：blocked 行仅在这几种「合法挂起/不可采集」态下才允许免 scope 证据校验；
# status ∈ {ok, fail, exception} 下出现 blocked 行 = 口径矛盾（零证据放行洞），记 error。
_BLOCKED_OK_STATUS = {"blocked", "blocked_incomparable_timing_scope", "blocked_wait_gpu_benchmark",
                      "blocked_wait_real_baseline"}
# gt3-2 的「挂起态」族：缺**基线**（NPU 侧已测）→ 门仍强制 NPU 侧证据完整（npu_us + kernel_only）。
# 采集端整条没接通时 npu_us 为 None → 门 FAILED（fail-closed，正确方向：不给「等基线」当免检牌）。
_PERF_WAIT_STATUS = {"blocked_wait_gpu_benchmark", "blocked_wait_real_baseline"}
# validator overall.verdict 合法枚举（C4 2026-07-22 加 passed_with_gaps：任务书要求的 dtype 有一部分
# 算子 op_def 压根不支持 → 带发现的通过；差额挂 task_pr_gaps，见 _check_unsupported_gap 的反后门硬校）。
# 2026-07-23：passed_with_gaps 再添一类撑法——op_def 声明了、但目标硬件那支实现没有
# （`dtype_unsupported_on_target_hw`，见 _check_target_hw_gap）；两类均为被测物侧发现、同进 unsupported 桶。
_VERDICT_ENUM = {
    "pass", "fail", "needs_review", "passed_with_risk", "passed_with_gaps",
    "blocked_golden_unauthorized",
}
# C4 结构化 gap 类型：任务书要求、算子 op_def 不声明支持的 dtype 差额（与既有 dtype_deferred 语义不同——
# deferred = 我们这条 pipeline 暂未测；unsupported = PR/算子根本没实现，是对被测方的**发现**）。
_DTYPE_GAP_KIND = "dtype_unsupported_by_op_def"
# 第三类结构化 gap（2026-07-23，im2col 的 bool 撞出）：任务书要 dtype X、算子 op_def **声明支持** X，
# 但**目标硬件那一支的 aclnn 实现**（如 im2col A2/A3 非 regbase 分支的 DTYPE_SUPPORT_LIST）**不含** X。
# 仍是**被测物侧的验收发现**（≠ dtype_deferred 的「我们这条 pipeline 测不了」）→ 与 C4 同桶、裁决落
# passed_with_gaps；反后门硬校方向与 C4 相反（op_def **须含**、目标硬件实现 **须不含**），见 _check_target_hw_gap。
_TARGET_HW_GAP_KIND = "dtype_unsupported_on_target_hw"
# 被测物侧「发现类」gap——都喂 passed_with_gaps、都进 unsupported 桶（覆盖门认作已挂账、passed_with_gaps
# 交叉核验认作有据）。`dtype_deferred` 是「**我们的**能力缺口」，语义不同、不在此集。
_FINDING_GAP_KINDS = {_DTYPE_GAP_KIND, _TARGET_HW_GAP_KIND}


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _load(d, name):
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return "__BAD__"


def _case_key(c, errs=None):
    """用例的 (dtype, shape) 键——覆盖计数用；抗坏字段。
    shape 必须是 list/tuple——shape:null 时 `list(None)` 会 TypeError 崩（finding #15），
    此处显式校验：非法记 error 并退回占位 '?'。"""
    ins = c.get("inputs") if isinstance(c, dict) else None
    if not isinstance(ins, list) or not ins or not isinstance(ins[0], dict):
        return "?"
    shape = ins[0].get("shape", [])
    if not isinstance(shape, (list, tuple)):
        if errs is not None:
            errs.append(f"{c.get('id', '?')}: inputs[0].shape 非 list/tuple（{shape!r}）")
        return "?"
    return f"{ins[0].get('dtype', '?')}{list(shape)}"


def _ids_from_evidence(ev_list, errs):
    ids = []
    for i, e in enumerate(ev_list or []):
        if not isinstance(e, dict) or not e.get("case_id"):
            errs.append(f"evidence[{i}] 缺 case_id（证据不完整）")
            continue
        ids.append(e["case_id"])
    if len(ids) != len(set(ids)):
        errs.append(f"evidence 有重复 case_id: {[k for k, v in Counter(ids).items() if v > 1]}")
    return ids


def _actual_dtypes(cs, errs):
    """从**真实 cases** 归并实测 dtype 集（gate-must-check-the-effective-object·不信自报汇总）。
    抗坏输入：非字符串 dtype（坏 JSON 里 dtype 为 dict/list）→ 记 error 不崩（否则 set.add/sorted 抛
    TypeError、落不成 BLOCKED）。`errs=None` 时只归并、不记 error（供只读探测复用）。"""
    cases = cs.get("cases") if isinstance(cs, dict) and isinstance(cs.get("cases"), list) else []
    actual = set()
    for c in cases:
        ins = c.get("inputs") if isinstance(c, dict) else None
        if isinstance(ins, list) and ins and isinstance(ins[0], dict):
            dt = ins[0].get("dtype")
            if isinstance(dt, str) and dt:
                actual.add(dt)
            elif dt is not None and errs is not None:
                errs.append(f"case {c.get('id', '?')}: inputs[0].dtype 非字符串（{type(dt).__name__}·证据不可信）")
    return actual


def _check_unsupported_gap(g, i, required, actual, errs):
    """C4 单条 `dtype_unsupported_by_op_def` gap 的「有据可查」硬校；合法 → 返回其 dtypes，否则 []（并记 error）。

    ⚠ **这条绝不能变成「宣称有 gap 就免检」的后门**，故四道硬校缺一即拒（拒 = 该 gap 不计入已挂账集，
    对应 dtype 仍按「静默收窄」判 → BLOCKED；同时把理由写清）：
      ① **有据**——`task_doc_ref`（任务书原文定位）+ `op_def_ref`（op_def 出处）+ `op_def_dtypes`
         （op_def 实际声明的支持集）必填且类型正确。
      ② **自洽**——声称不支持的 dtype 不得同时列在自报的 `op_def_dtypes` 里。
      ③ **不得覆盖真失败**——该 dtype 若**有真实用例在跑**（实测集含之），说明它被实现且被测了，
         属「算子实现了但跑挂了」，必须走精度/功能裁决。**这就是「没实现」与「跑挂了」的判别式**：
         前者压根造不出用例，后者一定有用例+evidence。
      ④ **在需求内**——`dtype_required` 是 list 时，gap 的 dtype 须确在任务书要求内。
    """
    tag = f"task_pr_gaps[{i}]({_DTYPE_GAP_KIND})"
    dts = g.get("dtypes")
    if not (isinstance(dts, list) and dts and all(isinstance(x, str) and x for x in dts)):
        errs.append(f"{tag}: dtypes 须为非空 dtype 字符串列表（{dts!r}）")
        return []
    bad = False
    for k in ("task_doc_ref", "op_def_ref"):
        v = g.get(k)
        if not (isinstance(v, str) and v.strip()):
            errs.append(f"{tag}: 缺 {k}（gap 须有据可查：指向任务书原文 / op_def 出处，"
                        "否则就成了『宣称有 gap 就免检』）")
            bad = True
    od = g.get("op_def_dtypes")
    if not (isinstance(od, list) and all(isinstance(x, str) and x for x in od)):
        errs.append(f"{tag}: op_def_dtypes 须为 dtype 字符串列表（op_def 实际声明的支持集，供交叉核验）")
        bad = True
    else:
        contra = sorted(set(dts) & set(od))
        if contra:
            errs.append(f"{tag}: {contra} 既称 op_def 不支持、又列在自报 op_def_dtypes 里（自相矛盾·伪造 gap）")
            bad = True
    ran = sorted(set(dts) & set(actual))
    if ran:
        errs.append(f"{tag}: {ran} 有真实用例在跑——属「算子实现了但跑挂了」，须走精度/功能裁决，"
                    "不得用「op_def 不支持」的 gap 罩住")
        bad = True
    if required is not None:
        outside = sorted(set(dts) - set(required))
        if outside:
            errs.append(f"{tag}: {outside} 不在任务书 dtype_required {sorted(required)} 内"
                        "（为任务书没要求的 dtype 挂账·gap 无据）")
            bad = True
    return [] if bad else dts


def _check_target_hw_gap(g, i, required, actual, errs):
    """第三类 `dtype_unsupported_on_target_hw` gap 的「有据可查」硬校；合法 → 返回其 dtypes，否则 []（并记 error）。

    语义：任务书要 dtype X；算子 op_def **声明支持** X；但**目标硬件那一支的 aclnn 实现**（如 im2col 的
    A2/A3 非 regbase 分支 `DTYPE_SUPPORT_LIST`）**不含** X → **被测物侧**的验收发现（≠ `dtype_deferred`
    的「我们这条 pipeline 测不了」）。故与 C4 同桶（unsupported）、裁决落 passed_with_gaps。

    ⚠ 与 C4 同为反后门硬校、**方向相反**：C4 证「op_def 压根没声明」（op_def_dtypes 不得含）；本 kind 证
    「op_def 声明了、但目标硬件那支没实现」（op_def_dtypes **须含**、impl_dtypes **须不含**）。五道硬校
    缺一即拒（拒 = 该 gap 不计入已挂账集 → 对应 dtype 仍按「静默收窄」判 → BLOCKED；同时把理由写清）：
      ① **有据**——`dtypes`（非空 dtype 串表）+ `task_doc_ref`（任务书原文）+ `op_def_ref`（op_def 出处）
         + `impl_ref`（目标硬件实现出处，如 aclnn_xxx.cpp:行）+ `target_hw`（哪支硬件）必填且类型正确。
      ② **op_def 确实声明**——`op_def_dtypes`（op_def 实际声明集）须**含**全部 gap dtype；若不含
         说明 op_def 其实没声明该 dtype → 该走 C4（`dtype_unsupported_by_op_def`），本 kind 拒。
      ③ **目标硬件那支确实没实现**——`impl_dtypes`（目标硬件实现实际支持集）须**不含**任一 gap dtype；
         若含说明目标硬件其实实现了 → 不是「没实现」的发现，本 kind 拒。
      ④ **不得覆盖真失败**——gap dtype 若**有真实用例在跑**（实测集含之），属「实现了但跑挂了」，
         必须走精度/功能裁决；这就是「没实现」与「跑挂了」的判别式（后者一定有用例 + evidence）。
      ⑤ **在需求内**——`dtype_required` 是 list 时，gap 的 dtype 须确在任务书要求内。
    """
    tag = f"task_pr_gaps[{i}]({_TARGET_HW_GAP_KIND})"
    dts = g.get("dtypes")
    if not (isinstance(dts, list) and dts and all(isinstance(x, str) and x for x in dts)):
        errs.append(f"{tag}: dtypes 须为非空 dtype 字符串列表（{dts!r}）")
        return []
    bad = False
    for k in ("task_doc_ref", "op_def_ref", "impl_ref", "target_hw"):
        v = g.get(k)
        if not (isinstance(v, str) and v.strip()):
            errs.append(f"{tag}: 缺 {k}（gap 须有据可查：任务书原文 / op_def 出处 / "
                        "目标硬件实现出处 / 哪支硬件，否则就成了『宣称有 gap 就免检』）")
            bad = True
    # ② op_def **须含**全部 gap dtype（与 C4「不得含」相反）——证「op_def 确实声明了」。
    od = g.get("op_def_dtypes")
    if not (isinstance(od, list) and all(isinstance(x, str) and x for x in od)):
        errs.append(f"{tag}: op_def_dtypes 须为 dtype 字符串列表（op_def 实际声明的支持集，供交叉核验）")
        bad = True
    else:
        undeclared = sorted(set(dts) - set(od))
        if undeclared:
            errs.append(f"{tag}: {undeclared} 不在自报 op_def_dtypes {sorted(set(od))} 里"
                        f"（op_def 其实没声明该 dtype → 属 {_DTYPE_GAP_KIND}(C4)，非本 kind）")
            bad = True
    # ③ impl_dtypes **须不含**任一 gap dtype——证「目标硬件那支确实没实现」。
    im = g.get("impl_dtypes")
    if not (isinstance(im, list) and all(isinstance(x, str) and x for x in im)):
        errs.append(f"{tag}: impl_dtypes 须为 dtype 字符串列表（目标硬件实现实际支持集，供交叉核验）")
        bad = True
    else:
        implemented = sorted(set(dts) & set(im))
        if implemented:
            errs.append(f"{tag}: {implemented} 既称目标硬件实现不支持、又列在自报 impl_dtypes 里"
                        "（自相矛盾·伪造 gap）")
            bad = True
    # ④ 不得罩住「实现了但跑挂了」。
    ran = sorted(set(dts) & set(actual))
    if ran:
        errs.append(f"{tag}: {ran} 有真实用例在跑——属「实现了但跑挂了」，须走精度/功能裁决，"
                    "不得用「目标硬件不支持」的 gap 罩住")
        bad = True
    # ⑤ 在需求内。
    if required is not None:
        outside = sorted(set(dts) - set(required))
        if outside:
            errs.append(f"{tag}: {outside} 不在任务书 dtype_required {sorted(required)} 内"
                        "（为任务书没要求的 dtype 挂账·gap 无据）")
            bad = True
    return [] if bad else dts


def _collect_dtype_gaps(cs, actual, required, errs):
    """归并 `task_pr_gaps` 里各类「已挂账」dtype，返回 (deferred 集, unsupported 集)。

    · `dtype_deferred`——我们这条 pipeline 暂未测（既有语义/字段要求**原样不动**）；
    · `dtype_unsupported_by_op_def`（C4）——任务书要求但算子 op_def 根本不声明支持，逐条硬校（见上）；
    · `dtype_unsupported_on_target_hw`——op_def 声明了、但目标硬件那支 aclnn 实现没有，逐条硬校（见上）。
    后两类同属**被测物侧发现类**（`_FINDING_GAP_KINDS`）→ 并进 **unsupported 桶**（覆盖门认作已挂账、
    passed_with_gaps 交叉核验认作有据）。
    ⚠ 硬校**无条件行使**：不因 `dtype_required` 缺失而跳过——否则删掉 dtype_required 即可连带绕过 gap 校验
      （同 codex#2 对 dtype_tested 的教训）。"""
    gaps = cs.get("task_pr_gaps") if isinstance(cs, dict) and isinstance(cs.get("task_pr_gaps"), list) else []
    deferred, unsupported = set(), set()
    for i, g in enumerate(gaps):
        if not isinstance(g, dict):
            continue                                  # 历史自由文本条目：原样忽略、不报错
        kind = g.get("kind")
        if kind == "dtype_deferred":
            dts = g.get("dtypes")
            if isinstance(dts, list):
                deferred.update(x for x in dts if isinstance(x, str))
        elif kind == _DTYPE_GAP_KIND:
            unsupported.update(_check_unsupported_gap(g, i, required, actual, errs))
        elif kind == _TARGET_HW_GAP_KIND:
            unsupported.update(_check_target_hw_gap(g, i, required, actual, errs))
    return deferred, unsupported


def _gate_dtype_coverage(cs, errs):
    """Q7 dtype 覆盖门（gate-must-check-the-effective-object）：任务书要求的 dtype 全集 `dtype_required`
    若未被实测集 `dtype_tested` 覆盖、且 `task_pr_gaps` 无对应挂账记录 → **静默收窄=证据不完整**
    → error（走 BLOCKED）。挂账有三类：`dtype_deferred`（我们暂未测）、C4 的
    `dtype_unsupported_by_op_def`（算子 op_def 根本不声明支持）、`dtype_unsupported_on_target_hw`
    （op_def 声明了、目标硬件那支实现没有）——后两类均落 passed_with_gaps。防误伤/防阻塞：
      · `dtype_required` **未声明**（legacy 未迁）→ 不 BLOCK，仅提示「覆盖门未行使」（避免一刀切炸掉现有 spec）。
      · `dtype_required` == `"needs_user"`（全集未知·信息库未接通）→ 不 BLOCK，提示「不谎报覆盖」。
    读的是 caseset 顶层的 dtype_required/dtype_tested/task_pr_gaps（gen_cases 从 spec 透传/派生）。"""
    actual = _actual_dtypes(cs, errs)
    # 自报 dtype_tested 若声明 → **恒**与真实用例 dtype 集对账（不因 dtype_required 缺失而跳过——否则删 required 即同时绕过对账）。
    tested = cs.get("dtype_tested")
    if tested is not None:
        if not isinstance(tested, list) or not all(isinstance(x, str) for x in tested):
            errs.append("dtype_tested 须为 dtype 字符串列表（证据不可信）")
        elif set(tested) != actual:
            errs.append(f"dtype_tested 自报 {sorted(set(tested))} 与真实用例 dtype 集 {sorted(actual)} 不符"
                        "（自报覆盖与实际生成漂移/伪造·证据不可信）")
    req = cs.get("dtype_required")
    required = req if isinstance(req, list) and all(isinstance(x, str) for x in req) else None
    # gap 归并+硬校**先于**下面所有 early return——不因 dtype_required 未声明/needs_user/类型非法而跳过，
    # 否则「删掉 dtype_required」即可连带绕过 C4 的伪造 gap 校验（同 codex#2 对 dtype_tested 的教训）。
    deferred, unsupported = _collect_dtype_gaps(cs, actual, required, errs)
    # 覆盖门：仅 dtype_required 声明为 list 时行使；未声明(legacy)/needs_user(全集未知) → 不 BLOCK（migration 宽容·见 doc TODO）。
    if req in (None, [], ""):
        print("  dtype_required 未声明 → dtype 覆盖门未行使（不阻塞·避免误伤 legacy spec）")
        return
    if req == "needs_user":
        print("  dtype_required=needs_user（全集未知·信息库/用户未接通）→ 覆盖门未行使、不谎报覆盖")
        return
    if required is None:
        errs.append("dtype_required 类型非法（须 list of dtype 字符串 或 \"needs_user\"）")
        return
    accounted = deferred | unsupported
    uncovered = [dt for dt in req if dt not in actual and dt not in accounted]
    if uncovered:
        errs.append(
            f"dtype 覆盖不足：任务书要求 {req}、实测(真实用例) {sorted(actual)}、"
            f"缺 {uncovered} 且 task_pr_gaps 无 dtype_deferred / "
            f"{' / '.join(sorted(_FINDING_GAP_KINDS))} 记录"
            "（静默收窄 dtype 覆盖·证据不完整）")
    else:
        print(f"  dtype 覆盖 OK：要求={req} 实测(真实用例)={sorted(actual)} "
              f"已 deferred={sorted(deferred)} op_def 不支持={sorted(unsupported)}")


def _check_oracle_source(cid, exp, prec, errs, pp):
    """Q9 oracle_source 门校（gate-must-check-the-effective-object · Gate-checks-evidence-integrity-not-verdict）：
    evidence.precision.oracle_source 必须 (a) ∈ 六枚举 `precision_policy.ORACLE_SOURCES`，且 (b) ==
    `oracle_source_from_golden(caseset.expected.golden_source)`。防伪造 evidence 直接篡改 oracle_source 蒙混。
    fail-closed：caseset 缺 golden_source / 映射失败 / oracle_source 缺失/非法/不符 → 累计 error（证据不可信）。"""
    gs = exp.get("golden_source") if isinstance(exp, dict) else None
    if not gs:
        errs.append(f"{cid}: caseset expected 缺 golden_source"
                    "（无法核 evidence oracle_source 是否属实·防篡改门失效）")
        return
    try:
        expect = pp.oracle_source_from_golden(gs)
    except Exception as ex:
        errs.append(f"{cid}: caseset golden_source={gs!r} 无法映射 oracle_source（{type(ex).__name__}: {ex}）")
        return
    claimed = prec.get("oracle_source")
    if claimed is None:
        errs.append(f"{cid}: evidence 缺 precision.oracle_source（证据不完整·不可信）")
        return
    if claimed not in pp.ORACLE_SOURCES:
        errs.append(f"{cid}: evidence oracle_source={claimed!r} 非法（须属 {list(pp.ORACLE_SOURCES)}）")
        return
    if claimed != expect:
        errs.append(f"{cid}: evidence oracle_source={claimed!r} ≠ 据 caseset golden_source 映射的 {expect!r}"
                    "（伪造/篡改 oracle_source·证据不可信）")


# ══════════════ 多输出契约（`expected.outputs[]`）的门支持 —— op-中立、据字段触发 ══════════════
# 触发**只据结构字段**：caseset `expected` 里出现 `outputs` 键 → 走多输出分支；否则**原封不动**走 legacy
# 单输出通路（向后兼容硬约束：现有 4 个单输出算子 isclose/sign/equal/neg 的判定链零变更）。
# 与 validator `_judge_multi` 同纪律：逐输出按 **role** 对齐、role 须唯一且非空（否则无从按角色匹配 canonical
# 判据）；缺输出 / 多输出 / 结构不合法 一律 fail-closed 记 error（走 BLOCKED），绝不静默兜底。
_MO_KEY = "outputs"
# 多输出 case 的 `expected` **不得**同时带 legacy 单输出口径字段——两套结构并存 = 门/validator 各读一套，
# 是典型的「看起来对」。fail-closed 拒。
_MO_FORBIDDEN_LEGACY_KEYS = ("golden_path", "policy", "threshold", "standard",
                             "tolerance_policy_id", "compare", "acceptance_policy")
# 逐输出口径三处一致所校的字段（与 legacy 的四字段同口径；threshold 在 torch_allclose/index 口径下是
# `(rtol, atol)` 二元组 → JSON 落地为 list，故类型放行 list，**不是**只认标量）。
_MO_FIELD_TYPES = {"standard": (str,), "tolerance_policy_id": (str, type(None)),
                   "policy": (dict,), "threshold": (int, float, list)}


def _is_multi_output(exp):
    """caseset case 的 expected 是否走多输出契约（据字段，绝非按算子名）。"""
    return isinstance(exp, dict) and _MO_KEY in exp


def _mo_shape_ok(v):
    """输出形状：list 且每维非 bool 非负 int（0-d 归约输出 → 空 list，合法）。"""
    return isinstance(v, list) and all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in v)


def _mo_numel(shape):
    n = 1
    for x in shape:
        n *= x
    return n


def _mo_caseset_outputs(cid, exp, errs, where="caseset"):
    """取 caseset `expected.outputs` 并做**结构合法性**校验；不合法 → 记 error 返回 None（fail-closed）。"""
    for k in _MO_FORBIDDEN_LEGACY_KEYS:
        if k in exp:
            errs.append(f"{cid}: {where} expected 同时带多输出 outputs[] 与 legacy 单输出字段 {k!r}"
                        "（两套精度结构并存·读哪套都可能是错的，拒）")
    outs = exp.get(_MO_KEY)
    if not isinstance(outs, list) or not outs:
        errs.append(f"{cid}: {where} expected.outputs 非列表或为空（多输出契约无输出可校·证据不完整）")
        return None
    roles, ok = [], True
    for k, o in enumerate(outs):
        if not isinstance(o, dict):
            errs.append(f"{cid}: 输出#{k} 非对象")
            ok = False
            continue
        role = o.get("role")
        if not isinstance(role, str) or not role:
            errs.append(f"{cid}: 输出#{k} 缺 role（多输出按 role 对齐 canonical 判据，须非空字符串）")
            ok = False
        else:
            roles.append(role)
        if not o.get("golden_path"):
            errs.append(f"{cid}: 输出#{k}({role}) 无 golden_path")
            ok = False
        if not _mo_shape_ok(o.get("out_shape")):
            errs.append(f"{cid}: 输出#{k}({role}) 的 out_shape 非法（须非负整数 list）：{o.get('out_shape')!r}")
            ok = False
        for key, types in _MO_FIELD_TYPES.items():
            if key not in o:
                errs.append(f"{cid}: 输出#{k}({role}) 缺 expected.{key}（无法做三处一致、防放宽门失效）")
                ok = False
            elif not isinstance(o[key], types) or isinstance(o[key], bool):
                errs.append(f"{cid}: 输出#{k}({role}) 的 expected.{key} 类型错"
                            f"（{type(o[key]).__name__}，须 {tuple(t.__name__ for t in types)}）")
                ok = False
        if role == "index":
            if not isinstance(o.get("index_of"), str) or not o["index_of"]:
                errs.append(f"{cid}: 输出#{k} role=index 但 index_of 缺失/非字符串"
                            "（index 判据须指明所引 value 输出）")
                ok = False
    dup = [r for r, n in Counter(roles).items() if n > 1]
    if dup:
        errs.append(f"{cid}: 输出 role 重复 {dup}（多输出按 role 对齐 canonical，须唯一）")
        ok = False
    if any(r == "index" for r in roles) and not any(r != "index" for r in roles):
        errs.append(f"{cid}: 只有 index 输出、无被引的 value 输出（index 判据无所依·结构不合法）")
        ok = False
    return outs if ok else None


def _gate_task2_outputs(cid, exp, prec, errs):
    """多输出契约的**逐输出**口径三处一致门（防放宽/防缺输出/防换序）——legacy 单输出不走此路。

    caseset `expected.outputs[k]` ↔ evidence `precision.outputs[k]` 按**位置**配对、再校 role 相等
    （位置=spec out-param 顺序，与 validator `_judge_multi` 同口径），逐输出比 standard /
    tolerance_policy_id / policy / threshold 全等。任一侧缺字段即 error（不做「双非 None 才比」的宽容——
    那会放过缺字段假通过，见 legacy finding #12 的同一教训）。"""
    outs = _mo_caseset_outputs(cid, exp, errs)          # caseset 侧结构合法性（不合法 → None）
    ev_outs = prec.get(_MO_KEY)
    if not isinstance(ev_outs, list) or not ev_outs:
        errs.append(f"{cid}: evidence precision.outputs 缺失/非列表/为空（多输出证据不完整）")
        return
    if outs is None:
        return                                          # caseset 侧已不合法，逐输出比对无意义（已记 error）
    if len(ev_outs) != len(outs):
        errs.append(f"{cid}: evidence precision.outputs 长度 {len(ev_outs)} ≠ caseset {len(outs)}"
                    "（缺输出/多输出·⚠跑子集到输出粒度）")
        return
    for k, (exp_o, ev_o) in enumerate(zip(outs, ev_outs)):
        if not isinstance(ev_o, dict):
            errs.append(f"{cid}: evidence 输出#{k} 非对象")
            continue
        role = exp_o.get("role")
        if ev_o.get("role") != role:
            errs.append(f"{cid}: 输出#{k} role 不一致 caseset={role!r}/evidence={ev_o.get('role')!r}"
                        "（输出换序/张冠李戴·证据不可信）")
            continue
        if ev_o.get("golden_path") != exp_o.get("golden_path"):
            errs.append(f"{cid}: 输出#{k}({role}) evidence golden_path={ev_o.get('golden_path')!r} "
                        f"≠ caseset {exp_o.get('golden_path')!r}（真值来源被换·证据不可信）")
        for key in _MO_FIELD_TYPES:
            if key not in ev_o:
                errs.append(f"{cid}: 输出#{k}({role}) evidence 缺 {key}（无法做三处一致、防放宽门失效）")
                continue
            if ev_o[key] != exp_o.get(key):
                errs.append(f"{cid}: 输出#{k}({role}) evidence {key}={ev_o[key]!r} "
                            f"≠ caseset {exp_o.get(key)!r}（防放宽假通过）")
        if not isinstance(ev_o.get("metrics"), dict):
            errs.append(f"{cid}: 输出#{k}({role}) evidence 缺 metrics（误差分布未复算·证据不完整）")


_STORAGE_ITEMSIZE = {
    "bool": 1, "int8": 1, "uint8": 1,
    "float16": 2, "bfloat16": 2, "int16": 2, "uint16": 2,
    "float32": 4, "int32": 4, "uint32": 4,
    "float64": 8, "int64": 8, "uint64": 8, "complex64": 8,
    "complex128": 16,
}
_PERF_SHAPE_PROFILES = {
    "Atlas A3": {"metric": "sum_input_bytes", "small_max_bytes": 256 * 1024},
}


def _gate_perf_case_policy(cs, cases, errs):
    """复核性能 case⊆精度 case、输入物理字节分类与选择账本；仅查契约，不判性能。"""
    policy = cs.get("perf_case_policy")
    if policy is None:
        return                                      # legacy caseset 保持兼容
    if not isinstance(policy, dict):
        errs.append("caseset.perf_case_policy 非对象")
        return
    rule = policy.get("shape_classification")
    limit = rule.get("small_max_bytes") if isinstance(rule, dict) else None
    hardware = rule.get("hardware") if isinstance(rule, dict) else None
    profile = _PERF_SHAPE_PROFILES.get(hardware)
    if (policy.get("case_source") != "precision_cases"
            or not isinstance(rule, dict) or rule.get("metric") != "sum_input_bytes"
            or not _is_int(limit) or limit < 1
            or profile is None
            or limit != profile["small_max_bytes"]
            or rule.get("metric") != profile["metric"]
            or rule.get("boundary") != "small_if_input_bytes_lte_limit"):
        errs.append("perf_case_policy 来源/分类规则非法")
        return
    selection_rule = policy.get("case_selection")
    new_selection_contract = isinstance(selection_rule, dict)
    min_total_elements = (
        selection_rule.get("min_total_input_elements") if new_selection_contract else 1)
    if (not _is_int(min_total_elements) or min_total_elements < 1):
        errs.append("perf_case_policy.case_selection.min_total_input_elements 非法")
        return
    include_precision_tags = (
        selection_rule.get("include_precision_tags", []) if new_selection_contract else [])
    if (not isinstance(include_precision_tags, list)
            or any(not isinstance(tag, str) or not tag
                   for tag in include_precision_tags)
            or len(set(include_precision_tags)) != len(include_precision_tags)):
        errs.append("perf_case_policy.case_selection.include_precision_tags 非法")
        return
    actual_counts = {"small": 0, "large": 0}
    selected_ids, excluded_ids, excluded_degenerate_ids, by_dtype = [], [], [], {}
    for c in cases:
        if not isinstance(c, dict):
            continue
        dims, cid = c.get("dims") or [], c.get("id")
        if "精度" in dims and "性能" not in dims and isinstance(cid, str):
            excluded_ids.append(cid)
            exclusion = c.get("perf_selection_exclusion")
            if (set(c.get("tags") or []).intersection(include_precision_tags)
                    and not isinstance(exclusion, dict)):
                errs.append(f"{cid}: 命中 include_precision_tags 却未进入性能维")
            if isinstance(exclusion, dict):
                inputs = c.get("inputs") if isinstance(c.get("inputs"), list) else []
                total_elements = 0
                valid_elements = bool(inputs)
                for inp in inputs:
                    shape = inp.get("shape") if isinstance(inp, dict) else None
                    if (not isinstance(shape, list)
                            or any(not _is_int(x) or x < 0 for x in shape)):
                        valid_elements = False
                        break
                    numel = 1
                    for x in shape:
                        numel *= x
                    total_elements += numel
                reason = exclusion.get("reason")
                if reason == "degenerate_total_input_elements_below_minimum":
                    if (not new_selection_contract
                            or not valid_elements
                            or total_elements >= min_total_elements
                            or exclusion.get("total_input_elements") != total_elements
                            or exclusion.get("min_total_input_elements") != min_total_elements):
                        errs.append(
                            f"{cid}: perf_selection_exclusion 与退化输入选择规则不一致")
                    else:
                        excluded_degenerate_ids.append(cid)
                elif reason == "balanced_max_cases_limit":
                    max_cases = (
                        selection_rule.get("max_cases")
                        if new_selection_contract else None)
                    if (not valid_elements
                            or total_elements < min_total_elements
                            or not _is_int(max_cases) or max_cases < 1
                            or exclusion.get("max_cases") != max_cases
                            or exclusion.get("balance_axes")
                            != ["dtype", "shape_class"]):
                        errs.append(
                            f"{cid}: perf_selection_exclusion 与 max_cases 平衡选择规则不一致")
                else:
                    errs.append(
                        f"{cid}: perf_selection_exclusion.reason={reason!r} 非受控词")
        if "性能" not in dims:
            continue
        if "精度" not in dims:
            errs.append(f"{cid}: 性能 case 不是精度 case")
        total, dtypes = 0, set()
        inputs = c.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            errs.append(f"{cid}: 性能 case 无 inputs，无法复核物理字节")
            continue
        valid = True
        for inp in inputs:
            shape = inp.get("shape") if isinstance(inp, dict) else None
            dtype = inp.get("dtype") if isinstance(inp, dict) else None
            storage = inp.get("storage_dtype") if isinstance(inp, dict) else None
            storage = storage or ("uint16" if dtype == "bfloat16" else dtype)
            if (not isinstance(shape, list)
                    or any(not _is_int(x) or x < 0 for x in shape)
                    or storage not in _STORAGE_ITEMSIZE):
                errs.append(f"{cid}: input shape/storage dtype 非法，无法复核大小 shape")
                valid = False
                break
            numel = 1
            for x in shape:
                numel *= x
            total += numel * _STORAGE_ITEMSIZE[storage]
            if isinstance(dtype, str):
                dtypes.add(dtype)
        if not valid:
            continue
        expected_cls = "small" if total <= limit else "large"
        meta = c.get("perf_shape_classification")
        if (not isinstance(meta, dict) or meta.get("class") != expected_cls
                or meta.get("input_bytes") != total or meta.get("small_max_bytes") != limit
                or meta.get("metric") != "sum_input_bytes"
                or meta.get("hardware") != hardware
                or meta.get("boundary") != "small_if_input_bytes_lte_limit"):
            errs.append(f"{cid}: perf_shape_classification 与输入物理字节 {total} 不一致")
            continue
        actual_counts[expected_cls] += 1
        selected_ids.append(cid)
        key = "+".join(sorted(dtypes)) if dtypes else "unknown"
        by_dtype[key] = by_dtype.get(key, 0) + 1
    if policy.get("counts") != actual_counts:
        errs.append(f"perf_case_policy.counts={policy.get('counts')!r} 与实际 {actual_counts!r} 不一致")
    sel = policy.get("selection")
    expected_selection = {
        "identity_rule": "selected case_id is reused from the same precision caseset",
        "selected_case_ids": selected_ids,
        "excluded_precision_case_ids": excluded_ids,
        "selected_by_dtype": dict(sorted(by_dtype.items())),
        "selected_total": len(selected_ids),
        "precision_total": sum(1 for c in cases
                               if isinstance(c, dict) and "精度" in (c.get("dims") or [])),
    }
    if new_selection_contract or (
            isinstance(sel, dict) and "excluded_degenerate_case_ids" in sel):
        expected_selection["excluded_degenerate_case_ids"] = excluded_degenerate_ids
    if sel != expected_selection:
        errs.append("perf_case_policy.selection 与 caseset 实际性能/精度 case 身份不一致")


def gate_task1(d, errs):
    """用例集自洽 + （有 evidence 时）id 一一对应，专防跑子集。"""
    cs = _load(d, "caseset.json")
    if not isinstance(cs, dict):
        errs.append("缺/坏 caseset.json（Task1 未产用例）")
        return
    cases = cs.get("cases")
    if not isinstance(cases, list) or not cases:
        errs.append("caseset 无用例或 cases 非列表")
        return
    ids = []
    for i, c in enumerate(cases):
        if not isinstance(c, dict):
            errs.append(f"case[{i}] 非对象")
            continue
        cid = c.get("id")
        if not cid:
            errs.append(f"case[{i}] 缺 id")
            continue
        ids.append(cid)
        if not c.get("inputs"):
            errs.append(f"{cid}: 无 inputs")
        exp = c.get("expected") if isinstance(c.get("expected"), dict) else {}
        if _is_multi_output(exp):
            # 多输出契约：口径/golden 逐输出落在 `expected.outputs[]`，顶层无 golden_path/threshold/policy。
            # 逐输出校完整性（结构不合法即 fail-closed），legacy 单输出分支**一行不走**（向后兼容硬约束）。
            _mo_caseset_outputs(cid, exp, errs)
            if not c.get("dims"):
                errs.append(f"{cid}: 无 dims（功能/精度/性能维度）")
            continue
        if not exp.get("golden_path"):
            errs.append(f"{cid}: 无 golden_path")
        # §1.4 空 Tensor 功能用例（compare=na，numel=0）：无精度口径可判 → 豁免阈值/标准/policy 完整性
        #  （validator 判 na）；防伪造：na 仅对真空 Tensor（某 input shape 含 0）合法，否则记 error。
        if exp.get("compare") == "na":
            if not _case_strict_empty(c):    # codex #4：严格真空（拒 shape:[false]/[0.0] 伪造）
                errs.append(f"{cid}: expected.compare=na 但非严格真空 Tensor（伪造 na 跳精度门，拒绝）")
        else:
            if exp.get("threshold") is None:
                errs.append(f"{cid}: 缺 expected.threshold")
            # T5 结构化口径必填（缺 → 无法做三处一致的防放宽门）
            if not exp.get("standard"):
                errs.append(f"{cid}: 缺 expected.standard（精度标准未声明）")
            if not exp.get("tolerance_policy_id"):
                errs.append(f"{cid}: 缺 expected.tolerance_policy_id")
            if not isinstance(exp.get("policy"), dict):
                errs.append(f"{cid}: 缺结构化 expected.policy")
        if not c.get("dims"):
            errs.append(f"{cid}: 无 dims（功能/精度/性能维度）")
    dup = [k for k, v in Counter(ids).items() if v > 1]
    if dup:
        errs.append(f"caseset 有重复 case_id: {dup}")
    cov = Counter(_case_key(c, errs) for c in cases if isinstance(c, dict))
    print(f"  用例数={len(cases)} | (dtype,shape) 覆盖={dict(cov)}")
    _gate_dtype_coverage(cs, errs)   # Q7：任务书 dtype 全集 vs 实测覆盖（未声明→不阻塞）
    _gate_perf_case_policy(cs, cases, errs)
    ev = _load(d, "evidence.json")  # 有 evidence（已跑）→ id 必须一一对应、不许子集
    if isinstance(ev, dict):
        eids = _ids_from_evidence(ev.get("evidence"), errs)
        miss, extra = set(ids) - set(eids), set(eids) - set(ids)
        if miss:
            errs.append(f"⚠跑子集：evidence 缺 {sorted(miss)}（caseset 有、实跑无）")
        if extra:
            errs.append(f"evidence 多出 {sorted(extra)}（caseset 无）")
    elif ev == "__BAD__":
        errs.append("evidence.json 解析失败（坏 JSON）")


def _canonical_sha(value):
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode()
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(raw).hexdigest()


def _gate_cpp_extension_receipt(d, caseset, envelope, ev_list, errs):
    """cpp_extension 的独立 build/load/ELF receipt 完整性门。

    adapter 已做首轮验证；本门从落盘工件重新算摘要，并要求每条 evidence 绑定同一 receipt。
    只核证据来源，不重判数值结果。
    """
    if envelope.get("runner_form") != "cpp_extension":
        return
    receipt = envelope.get("cpp_extension_receipt")
    if not isinstance(receipt, dict):
        errs.append("cpp_extension evidence 缺 cpp_extension_receipt")
        return
    if (receipt.get("schema") != "oprunway.cpp_extension_receipt"
            or receipt.get("schema_version") != 1
            or receipt.get("status") != "VERIFIED"):
        errs.append("cpp_extension receipt schema/status 非 VERIFIED v1")
        return
    manifest_path = _pinned_product(d, "cpp_extension/extension_manifest.json")
    plan_path = _pinned_product(d, "cpp_extension_invocation_plan.json")
    snapshot_path = _pinned_product(d, "cpp_extension_caseset.json")
    for label, path in (
            ("manifest", manifest_path), ("invocation plan", plan_path),
            ("caseset snapshot", snapshot_path)):
        if path is None:
            errs.append(f"cpp_extension {label} 缺失/逃逸/非普通文件")
    if any(path is None for path in (manifest_path, plan_path, snapshot_path)):
        return
    try:
        manifest = _load_json_file(manifest_path)
        plan = _load_json_file(plan_path)
        snapshot = _load_json_file(snapshot_path)
    except (OSError, ValueError, TypeError) as ex:
        errs.append(f"cpp_extension 绑定工件坏 JSON: {type(ex).__name__}: {ex}")
        return
    bindings = receipt.get("bindings")
    if not isinstance(bindings, dict):
        errs.append("cpp_extension receipt.bindings 缺失")
        return
    expected = {
        "caseset_sha256": _canonical_sha(caseset),
        "manifest_sha256": _canonical_sha(manifest),
        "invocation_plan_sha256": _canonical_sha(plan),
        "spec_sha256": manifest.get("spec_sha256"),
    }
    if _canonical_sha(snapshot) != expected["caseset_sha256"]:
        errs.append("cpp_extension caseset snapshot 与正式 caseset 漂移")
    for key, value in expected.items():
        if not isinstance(value, str) or len(value) != 64:
            errs.append(f"cpp_extension 无法派生 {key}")
        elif bindings.get(key) != value:
            errs.append(
                f"cpp_extension receipt.bindings.{key} 与落盘工件摘要不符")
    artifact = receipt.get("artifact")
    artifact_path = (_pinned_product(d, artifact.get("path"))
                     if isinstance(artifact, dict) else None)
    if artifact_path is None:
        errs.append("cpp_extension ELF 缺失/逃逸/非普通文件")
    elif artifact.get("sha256") != _sha256(artifact_path):
        errs.append("cpp_extension ELF sha256 与 receipt 不符")
    load = receipt.get("load")
    wanted = {row.get("entrypoint") for row in (manifest.get("variants") or [])
              if isinstance(row, dict)}
    if (not isinstance(load, dict) or load.get("success") is not True
            or load.get("loader") != "torch.ops.load_library"
            or load.get("namespace") != manifest.get("namespace")
            or not isinstance(load.get("schemas"), dict)
            or set(load["schemas"]) != wanted):
        errs.append("cpp_extension load namespace/schema/loader receipt 与 manifest 不一致")
    runtime = receipt.get("runtime")
    if not isinstance(runtime, dict) or any(
            not runtime.get(k) for k in
            ("torch_version", "torch_npu_version", "cann_version", "soc")):
        errs.append("cpp_extension runtime provenance 不完整")
    vendor = receipt.get("vendor")
    vendor_sha = vendor.get("library_sha256") if isinstance(vendor, dict) else None
    symbols_owned = vendor.get("symbols_owned") if isinstance(vendor, dict) else None
    if (not isinstance(vendor_sha, str) or len(vendor_sha) != 64
            or any(ch not in "0123456789abcdef" for ch in vendor_sha)
            or not isinstance(symbols_owned, list)
            or not symbols_owned
            or any(not isinstance(symbol, str) or not symbol
                   for symbol in symbols_owned)):
        errs.append("cpp_extension vendor library/symbol ownership provenance 不完整")
    build_receipt = vendor.get("build_receipt") if isinstance(vendor, dict) else None
    source = build_receipt.get("source") if isinstance(build_receipt, dict) else None
    build = build_receipt.get("build") if isinstance(build_receipt, dict) else None
    artifact_rec = (build_receipt.get("artifact")
                    if isinstance(build_receipt, dict) else None)
    head = source.get("pr_head_sha") if isinstance(source, dict) else None
    build_digest = _canonical_sha(build_receipt)
    if (not isinstance(build_receipt, dict)
            or build_receipt.get("schema") != "oprunway.vendor_build_receipt"
            or build_receipt.get("schema_version") != 1
            or build_receipt.get("status") != "VERIFIED"
            or not isinstance(head, str) or len(head) != 40
            or any(ch not in "0123456789abcdefABCDEF" for ch in head)
            or not isinstance(source.get("repo"), str) or not source["repo"].strip()
            or not isinstance(build, dict)
            or not isinstance(build.get("argv"), list) or not build["argv"]
            or any(not isinstance(x, str) or not x for x in build["argv"])
            or not isinstance(build.get("cwd"), str) or not build["cwd"]
            or build.get("returncode") != 0
            or not isinstance(artifact_rec, dict)
            or artifact_rec.get("library_path") != vendor.get("library_path")
            or artifact_rec.get("library_sha256") != vendor_sha
            or vendor.get("build_receipt_sha256") != build_digest):
        errs.append(
            "cpp_extension vendor build receipt 未完整绑定 PR head→构建→安装 ELF")
    receipt_sha = _canonical_sha(receipt)
    for row in ev_list:
        if isinstance(row, dict) and row.get("cpp_extension_receipt_sha256") != receipt_sha:
            errs.append(
                f"{row.get('case_id')}: cpp_extension receipt digest 缺失或漂移")


def _load_json_file(path):
    with open(path, encoding="utf-8") as src:
        return json.load(
            src,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"非法 JSON 常量 {token}")))


def gate_task2(d, errs):
    """精度证据**完整性**门：全覆盖(防子集) + precision 必填 + 阈值三处一致(防放宽) + oracle_source 门校 + 无契约问题。
    注：精度 pass/fail 本身由 validator 判、**此门不重判**——合法的精度 fail 不该被门当 BLOCKED。
    Q9 oracle_source 门校（gate-must-check-the-effective-object）：evidence.precision.oracle_source 须 ∈ 六枚举
    且 == oracle_source_from_golden(caseset.expected.golden_source)——防手搓/伪造 evidence 直接写任意 oracle_source 蒙混。"""
    cs, ev, vd = _load(d, "caseset.json"), _load(d, "evidence.json"), _load(d, "verdict.json")
    if not (isinstance(cs, dict) and isinstance(ev, dict) and isinstance(vd, dict)):
        errs.append("缺/坏 caseset/evidence/verdict.json（Task2 未跑全）")
        return
    # finding #13：cases/evidence 非列表或空 → 直接 FAILED（不静默兜成空列表放过）。
    cases = cs.get("cases")
    if not isinstance(cases, list) or not cases:
        errs.append("caseset.cases 缺失/非列表/空（Task2 无用例可核）")
        return
    ev_list = ev.get("evidence")
    if not isinstance(ev_list, list) or not ev_list:
        errs.append("evidence.evidence 缺失/非列表/空（Task2 无证据可核）")
        return
    _gate_cpp_extension_receipt(d, cs, ev, ev_list, errs)
    # ID 用 Counter 校验（重复不被 set 折叠）。
    cid_list = [c["id"] for c in cases if isinstance(c, dict) and c.get("id")]
    cid_dups = [k for k, v in Counter(cid_list).items() if v > 1]
    if cid_dups:
        errs.append(f"caseset 有重复 case_id: {cid_dups}")
    cids = set(cid_list)
    eids = set(_ids_from_evidence(ev_list, errs))  # 内部已用 Counter 报 evidence 重复
    if cids != eids:
        errs.append(f"⚠跑子集：caseset id != evidence id（缺 {sorted(cids - eids)} 多 {sorted(eids - cids)}）")
    # verdict 自身完整性 + 契约问题（finding #14：verdict 枚举 + counts 必填整数）
    ov = vd.get("overall")
    if not isinstance(ov, dict) or "verdict" not in ov:
        errs.append("verdict.overall.verdict 缺失（validator 产物不完整）")
        ov = {}
    elif ov.get("verdict") not in _VERDICT_ENUM:
        errs.append(f"verdict.overall.verdict={ov.get('verdict')!r} 非法（须属 {sorted(_VERDICT_ENUM)}）")
    # 交叉核验（**双向**）：裁决 verdict 与「caseset 里结构合法的被测物侧 dtype gap」必须自洽。两类 finding
    # gap（op_def 侧 C4 / target_hw 侧）任一有据即认——合法性用与 task1 **同一套**硬校、同进 unsupported 桶。
    # 无条件先算一次 _valid_finding（结构合法的 finding gap dtype 集），两个方向共用。
    probe = []
    _req = cs.get("dtype_required")
    _, _valid_finding = _collect_dtype_gaps(
        cs, _actual_dtypes(cs, None),
        _req if isinstance(_req, list) and all(isinstance(x, str) for x in _req) else None, probe)
    _verdict = ov.get("verdict")
    # 方向①：裁决自称 passed_with_gaps → caseset 必须真有结构合法的 finding gap 撑着
    #        （防手改 verdict.json 写个 passed_with_gaps 冒充「有 gap 所以放过」）。
    if _verdict == "passed_with_gaps" and not _valid_finding:
        errs.append("verdict=passed_with_gaps 但 caseset 无结构合法的 "
                    f"{' / '.join(sorted(_FINDING_GAP_KINDS))} 记录"
                    f"（裁决自称有 gap 却无据·拒）：{probe}")
    # 方向②（防 fail-open 断链·2026-07-23 红队）：caseset 有结构合法的 finding gap（覆盖门据此认账放行），
    #        validator 裁决却是**最低档的干净 pass**——说明这条被测物侧发现没被反映进 verdict（典型：
    #        `dtype_unsupported_on_target_hw` 这一 kind validator 侧尚未识别，被当自由文本吞掉 → 干净 pass）。
    #        任它过 = 「算子未实现任务书要求的 dtype」被机读成干净通过、CI 可自动合并（fail-open）。
    #        fail-closed 判 FAILED（BLOCKED）：有已挂账 finding gap 时，合法 verdict 至少是 passed_with_gaps
    #        （或更严的 fail / needs_review / passed_with_risk），唯独最低档 'pass' 与「有已挂账 gap」矛盾。
    if _verdict == "pass" and _valid_finding:
        errs.append(f"caseset 有结构合法的被测物侧 dtype gap {sorted(_valid_finding)}"
                    "（覆盖门据此认账放行），validator 裁决却是干净 pass——该 gap 未反映进 verdict"
                    "（verdict 侧未接线/被抹）→「算子未实现任务书要求的 dtype」不得机读成干净通过·"
                    "fail-closed 判 FAILED")
    counts = ov.get("counts") if isinstance(ov.get("counts"), dict) else None
    if counts is None:
        errs.append("verdict.overall.counts 缺失")
    else:
        for k in ("contract_problems", "fail", "uncertain"):
            if not _is_int(counts.get(k)):
                errs.append(f"verdict.overall.counts.{k} 缺失或非整数: {counts.get(k)!r}")
        if _is_int(counts.get("contract_problems")) and counts["contract_problems"]:
            errs.append(f"契约问题 {counts['contract_problems']} 条（validator 标 evidence↔caseset 契约破损）")
    _gate_accuracy_report(vd, cases, ev_list, errs)
    # precision 必填 + **口径三处一致（policy 化）**——防 adapter 偷偷放宽阈值/漏采精度假通过。
    # T5：由「标量 threshold 相等」升级为「tolerance_policy_id + 结构化 policy 一致」（保留 threshold digest）。
    exp_by_id = {c["id"]: (c.get("expected") or {})
                 for c in cases if isinstance(c, dict) and c.get("id")}
    # 多输出 index 输出的 gather 重算要拿到**整条 case**（输入路径 + attr 值 → 归约轴），故另备一份 case 索引。
    case_by_id = {c["id"]: c for c in cases if isinstance(c, dict) and c.get("id")}
    # §1.4 空 Tensor 功能用例（compare=na，numel=0）：无精度 metrics/阈值 → 豁免精度证据完整性（validator 判 na）。
    #  codex #4：Task2 **独立**复核真空（不依赖 Task1）——compare=na 且**真严格真空**才入豁免集；伪造 na（非真空）
    #  不豁免 → 下方精度证据完整性照校、因缺字段被门 FAILED。
    na_ids = {c["id"] for c in cases if isinstance(c, dict) and c.get("id")
              and isinstance(c.get("expected"), dict) and c["expected"].get("compare") == "na"
              and _case_strict_empty(c)}
    # Q9 oracle_source 门校用 precision_policy（纯 stdlib：ORACLE_SOURCES + oracle_source_from_golden，不拉 numpy）。
    # import 失败（几乎不会）→ 记 error、oracle 校跳过（但门 FAILED），不静默放过。
    try:
        import precision_policy as _pp
    except Exception as ex:
        _pp = None
        errs.append(f"precision_policy 不可用（{type(ex).__name__}: {ex}）——oracle_source 门校无法进行，判 FAILED")
    for e in ev_list:
        if not isinstance(e, dict) or not e.get("case_id"):
            continue
        cid = e["case_id"]
        if cid in na_ids:
            continue                                  # 空 Tensor 功能用例：无精度证据可校（validator→na）
        prec = e.get("precision")
        if not isinstance(prec, dict):
            errs.append(f"{cid}: evidence 缺 precision（证据不完整、不可信）")
            continue
        exp0 = exp_by_id.get(cid)
        if _is_multi_output(exp0):
            # 多输出契约：口径三处一致**逐输出**做（缺输出/换序/放宽任一 → error）；随后仍走同一套
            # Q9 oracle_source 门校（多输出的 oracle_source 仍在 precision 顶层）。legacy 分支零变更。
            _gate_task2_outputs(cid, exp0, prec, errs)
            if _pp is not None:
                _check_oracle_source(cid, exp0, prec, errs, _pp)
            continue
        if prec.get("threshold") is None:
            errs.append(f"{cid}: evidence 缺 precision.threshold（证据不完整、不可信）")
        if not prec.get("tolerance_policy_id"):
            errs.append(f"{cid}: evidence 缺 precision.tolerance_policy_id（口径不可追溯）")
        if not isinstance(prec.get("policy"), dict):
            errs.append(f"{cid}: evidence 缺结构化 precision.policy")
        exp = exp_by_id.get(cid)
        if exp is None:
            continue  # caseset 无此 case（多余）已在上文报
        # finding #12：三处一致改为「任一侧缺字段即 error」（旧「双非 None 才比」会放过缺字段假通过）。
        # caseset expected 侧四字段必填且类型正确 + 与 evidence 全等（防放宽）。
        _types = {"threshold": (int, float), "tolerance_policy_id": str, "standard": str, "policy": dict}
        for key in ("threshold", "tolerance_policy_id", "standard", "policy"):
            ce, ee = exp.get(key), prec.get(key)
            if ce is None:
                errs.append(f"{cid}: caseset expected 缺 {key}（无法做三处一致、防放宽门失效）")
                continue
            if not isinstance(ce, _types[key]) or isinstance(ce, bool):
                errs.append(f"{cid}: caseset expected.{key} 类型错（{type(ce).__name__}，须 {_types[key]}）")
                continue
            if ee is None:
                errs.append(f"{cid}: evidence precision 缺 {key}（无法做三处一致、防放宽门失效）")
                continue
            if ce != ee:
                errs.append(f"{cid}: evidence {key}={ee} ≠ caseset {ce}（防放宽假通过）")
        # Q9 oracle_source 门校（gate-must-check-the-effective-object）：evidence.precision.oracle_source 须
        #   ∈ 六枚举 且 == oracle_source_from_golden(caseset.expected.golden_source)。防伪造 evidence 篡改 oracle_source。
        if _pp is not None:
            _check_oracle_source(cid, exp, prec, errs, _pp)
    # === A 方案：evidence.precision.metrics ↔ 磁盘产物 provenance 绑定（重算比对）===
    # 上文只校「阈值/口径三处一致」（防放宽），却全信 evidence 自报的 metrics **数值**；此段按 provenance 读产物、
    # 先校 sha、再依 caseset policy 重算 metrics 并逐字段比对，堵「伪造 bad_count=0 直接 pass」的自报数字洞。
    _gate_precision_provenance(d, [e for e in ev_list if isinstance(e, dict) and e.get("case_id") not in na_ids],
                               exp_by_id, errs, case_by_id)  # 空 Tensor 功能用例无产物 provenance，过滤（validator→na）
    print(f"  精度裁决={ov.get('verdict')}(validator 判) | 证据覆盖={'一致' if cids == eids else '不一致'}")


def _gate_accuracy_report(vd, cases, ev_list, errs):
    """从 verdict.per_case + caseset + evidence 独立重建精度桶，再核固定报告视图。

    这里不重判数值阈值，只绑定 validator 已给出的逐 case 结论与执行状态；避免同时篡改
    ``accuracy_summary`` 旧五桶和 ``report`` 后仍可通过。
    """
    acc = vd.get("accuracy_summary")
    if not isinstance(acc, dict):
        errs.append("verdict 缺 accuracy_summary（精度按 dtype 报告不完整）")
        return
    report = acc.get("report")
    if not isinstance(report, dict) or not isinstance(report.get("overall"), dict):
        errs.append("accuracy_summary 缺 report.overall（总数/通过/失败/待复核）")
        return

    case_by_id = {c.get("id"): c for c in cases
                  if isinstance(c, dict) and isinstance(c.get("id"), str)}
    ev_by_id = {e.get("case_id"): e for e in ev_list
                if isinstance(e, dict) and isinstance(e.get("case_id"), str)}
    verdict_rows = vd.get("per_case")
    if not isinstance(verdict_rows, list):
        errs.append("verdict.per_case 缺失/非列表，无法独立重建精度汇总")
        return

    def metrics_present(precision):
        if not isinstance(precision, dict):
            return False
        outputs = precision.get("outputs")
        if isinstance(outputs, list):
            return bool(outputs) and all(
                isinstance(o, dict) and isinstance(o.get("metrics"), dict) for o in outputs)
        return isinstance(precision.get("metrics"), dict)

    def input_dtype_signature(case):
        return tuple(sorted(
            i.get("dtype") for i in (case.get("inputs") or [])
            if isinstance(i, dict) and isinstance(i.get("dtype"), str)))

    def explicit_compare_dtype(case):
        exp = case.get("expected") if isinstance(case, dict) else None
        if not isinstance(exp, dict):
            return None
        outputs = exp.get("outputs")
        if isinstance(outputs, list):
            dtypes = sorted({o.get("compare_dtype") for o in outputs
                             if isinstance(o, dict) and o.get("role") != "index"
                             and isinstance(o.get("compare_dtype"), str)})
            return "+".join(dtypes) if dtypes else None
        return exp.get("compare_dtype") if isinstance(exp.get("compare_dtype"), str) else None

    # 空 Tensor 的 expected.compare_dtype 为 null；validator 对普通 dtype 仍可由同接口的
    # 非空见证确定输出 dtype。这里从同一 caseset 的显式 compare_dtype 建确定性映射，不凭算子名猜。
    dtype_hints = {}
    for hint_case in cases:
        if not isinstance(hint_case, dict):
            continue
        hint = explicit_compare_dtype(hint_case)
        if hint is not None:
            dtype_hints.setdefault(input_dtype_signature(hint_case), set()).add(hint)

    def dtype_of(case):
        exp = case.get("expected") if isinstance(case, dict) else None
        if not isinstance(exp, dict):
            return "unknown"
        explicit = explicit_compare_dtype(case)
        if explicit is not None:
            return explicit
        input_dtypes = input_dtype_signature(case)
        # AscendOpTest 的 BF16 空 Tensor 无数值比较产物，validator 按 unknown 挂账。
        if "bfloat16" in input_dtypes:
            return "unknown"
        hints = dtype_hints.get(input_dtypes, set())
        if len(hints) == 1:
            return next(iter(hints))
        # legacy 单输出 caseset 可能尚未落 compare_dtype；仅在所有输入 dtype 唯一时作确定性回退。
        unique_inputs = sorted(set(input_dtypes))
        return unique_inputs[0] if len(unique_inputs) == 1 else "unknown"

    buckets = {}
    all_counts = {k: 0 for k in ("passed", "failed", "errored", "uncertain", "na")}
    for row in verdict_rows:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            errs.append("verdict.per_case 含缺/坏 case_id，无法独立重建精度汇总")
            continue
        cid = row["case_id"]
        case = case_by_id.get(cid)
        ev = ev_by_id.get(cid)
        if case is None:
            errs.append(f"{cid}: verdict.per_case 在 caseset 中无对应 case")
            continue
        state = row.get("精度")
        if row.get("功能") != "fail" and state == "pass":
            bucket = "passed"
        elif row.get("功能") != "fail" and state == "uncertain":
            bucket = "uncertain"
        else:
            exp = case.get("expected") if isinstance(case.get("expected"), dict) else {}
            precision_expected = (
                exp.get("verify_mode") in ("exact", "numerical")
                and "精度" in (case.get("dims") or []))
            if row.get("功能") != "fail" and state == "na" and not precision_expected:
                bucket = "na"
            else:
                executed = (isinstance(ev, dict) and ev.get("status") == "ok"
                            and metrics_present(ev.get("precision")))
                bucket = "failed" if executed else "errored"
        dtype = dtype_of(case)
        b = buckets.setdefault(dtype, {k: 0 for k in all_counts})
        b[bucket] += 1
        all_counts[bucket] += 1

    derived_total = sum(all_counts.values())
    if acc.get("total") != derived_total:
        errs.append(f"accuracy_summary.total={acc.get('total')!r} 与 verdict.per_case 数 {derived_total} 不一致")
    if acc.get("executed") != all_counts["passed"] + all_counts["failed"]:
        errs.append("accuracy_summary.executed 与逐 case 执行桶不一致")
    for key, value in all_counts.items():
        if acc.get(key) != value:
            errs.append(f"accuracy_summary.{key}={acc.get(key)!r} 与逐 case 派生 {value} 不一致")

    def report_view(src):
        return {"total": src["passed"] + src["failed"] + src["errored"] + src["uncertain"],
                "passed": src["passed"], "failed": src["failed"] + src["errored"],
                "needs_review": src["uncertain"], "na": src["na"]}

    exp_overall = report_view(all_counts)
    if report["overall"] != exp_overall:
        errs.append(f"accuracy_summary.report.overall={report['overall']!r} "
                    f"与逐 case 独立派生 {exp_overall!r} 不一致")
    legacy_rows = acc.get("by_dtype")
    report_rows = report.get("by_dtype")
    if not isinstance(legacy_rows, list) or not isinstance(report_rows, list):
        errs.append("accuracy_summary by_dtype/report.by_dtype 缺失或非列表")
        return
    legacy_by_dtype = {r.get("dtype"): r for r in legacy_rows
                       if isinstance(r, dict) and isinstance(r.get("dtype"), str)}
    exp_rows = []
    for dtype, counts in sorted(buckets.items()):
        legacy = legacy_by_dtype.get(dtype)
        if legacy is None:
            errs.append(f"accuracy_summary.by_dtype 缺 {dtype} 行")
        else:
            if legacy.get("count") != sum(counts.values()):
                errs.append(f"accuracy_summary.by_dtype[{dtype}].count 与逐 case 派生不一致")
            for key, value in counts.items():
                if legacy.get(key) != value:
                    errs.append(f"accuracy_summary.by_dtype[{dtype}].{key} "
                                f"与逐 case 派生 {value} 不一致")
        exp_rows.append({"dtype": dtype, **report_view(counts)})
    extra = sorted(set(legacy_by_dtype) - set(buckets))
    if extra:
        errs.append(f"accuracy_summary.by_dtype 多出无逐 case 支撑的 dtype 行 {extra}")
    if report_rows != exp_rows:
        errs.append("accuracy_summary.report.by_dtype 与逐 case 独立派生不一致")


def _perf_finite_pos(x):
    """有限正数（拒 bool/None/NaN/inf/≤0）——挂起态 NPU 侧 us 完整性用。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def _strict_empty_shape(shape):
    """严格真空判定（codex #4）：shape 须非空 list、每维**非 bool 非负 int**、且至少一维严格==整数 0。
    防伪造 shape:[false]/[0.0] 被 `0 in shape` 当作空 Tensor 蒙混（False==0、0.0==0）。"""
    if not isinstance(shape, list) or not shape:
        return False
    for d in shape:
        if not isinstance(d, int) or isinstance(d, bool) or d < 0:
            return False
    return 0 in shape                    # 此时全为非负 int，0 in 仅匹配整数 0


def _case_strict_empty(case):
    """case 是否**真空 Tensor**：某输入 shape 严格真空（codex #4，三处门/validator 共用口径）。"""
    return isinstance(case, dict) and any(
        isinstance(it, dict) and _strict_empty_shape(it.get("shape"))
        for it in (case.get("inputs") or []))


def _pinned_file(d, rel):
    """把 rel 钉死在 d 内的普通文件（codex M2）；绝对路径/`..` 逃逸/symlink/非文件 → None。"""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        return None
    joined = os.path.join(d, rel)
    if os.path.islink(joined):
        return None
    base = os.path.realpath(d)
    target = os.path.realpath(joined)
    try:
        if os.path.commonpath([base, target]) != base:
            return None
    except ValueError:
        return None
    return target if os.path.isfile(target) else None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ========================= A 方案：evidence.metrics ↔ 磁盘产物 provenance 绑定 =========================
def _pinned_product(d, rel):
    """把 per-case 产物（golden/out .npy）钉在 `<d>/work` 下解析——run_workflow 固定用 `<out_dir>/work` 承载
    repo_adapter 的 work_dir 产物，而门 `--dir=<out_dir>`，故产物在门视角下位于 `work/` 子目录。
    绝对路径 / `rel` 含 `..` 组件 / 逃出 `<d>/work` / symlink / 非普通文件 → None。

    pv-1 修正：**根落在 `realpath(<d>/work)`**（旧实现误用 `realpath(d)`——比 docstring 宽：`rel='../evil.npy'`
    realpath 到 `<d>/evil.npy`，`commonpath([<d>,<d>/evil.npy])==<d>` 会通过 → 可读 work/ 之外、`<d>` 之内的文件）。
    并**显式拒 `rel` 含 `..` 组件**（产物路径形如 `<cid>/out.npy`，`..` 无合法用途；不依赖 realpath 事后兜）。"""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        return None
    if ".." in rel.replace("\\", "/").split("/"):   # pv-1：显式拒 `..` 组件（含 "../x"、"a/../b"、".."）
        return None
    base = os.path.realpath(os.path.join(d, "work"))   # pv-1：根落在 <d>/work（非 <d>）——与 joined 同根
    joined = os.path.join(d, "work", rel)
    if os.path.islink(joined):
        return None
    target = os.path.realpath(joined)
    try:
        if os.path.commonpath([base, target]) != base:
            return None
    except ValueError:
        return None
    return target if os.path.isfile(target) else None


def _metrics_match(recalc, claimed, cid, errs, tag="metrics"):
    """逐字段比对：**重算出的每个 metric 都须在 evidence 自报值里 present 且相符**——计数类(int)精确相等、
    浮点带合理容差（同函数同字节重算本应逐位相等，容差只兜 JSON 往返末位）。evidence 多余键忽略。"""
    if not isinstance(claimed, dict):
        errs.append(f"{cid}: evidence 缺 precision.{tag}（无法与产物重算比对）")
        return
    for k, rv in recalc.items():
        cv = claimed.get(k)
        if isinstance(rv, bool):                       # 防御：目前无 bool metric
            if cv is not rv:
                errs.append(f"{cid}: 重算 {tag}.{k}={rv} ≠ evidence {cv!r}")
        elif isinstance(rv, int):                      # 计数类：精确相等（拒 bool 冒充 int）
            if not (isinstance(cv, int) and not isinstance(cv, bool) and cv == rv):
                errs.append(f"{cid}: 重算 {tag}.{k}={rv}（计数须精确）≠ evidence {cv!r}"
                            "（自报数字与产物重算不符·疑伪造）")
        else:                                          # 浮点：合理容差
            if not (isinstance(cv, (int, float)) and not isinstance(cv, bool)
                    and math.isclose(float(cv), float(rv), rel_tol=1e-9, abs_tol=1e-12)):
                errs.append(f"{cid}: 重算 {tag}.{k}={rv} ≉ evidence {cv!r}（浮点超容差·疑伪造）")


def _load_verified(np, path, want_sha, cid, kind, errs):
    """pv-3：**一次性读入 bytes** → `hashlib.sha256(bytes)` 校 provenance → 从内存 `io.BytesIO` 交
    `np.load(allow_pickle=False)`——消灭「`_sha256(path)` 读一次、`np.load(path)` 再 open 一次」的 TOCTOU
    （两次 open 之间产物可被换：sha 属坏文件、load 读好文件）。sha 不符/加载失败 → 记 error 返回 None
    （调用方据 None 提前返回，判 FAILED）。措辞保留「sha256/篡改」以维持既有断言。"""
    import io
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as ex:
        errs.append(f"{cid}: {kind} 产物读取失败（{type(ex).__name__}: {ex}）")
        return None
    if hashlib.sha256(data).hexdigest() != want_sha:
        errs.append(f"{cid}: {kind} 产物 sha256 与 provenance 不符（产物被替换/篡改）")
        return None
    try:
        return np.load(io.BytesIO(data), allow_pickle=False)   # allow_pickle=False：防恶意 .npy 反序列化
    except Exception as ex:
        errs.append(f"{cid}: {kind} 产物 np.load 失败（{type(ex).__name__}: {ex}）")
        return None


def _recompute_case(np, precision_policy, d, cid, exp, prec, errs):
    """单 case 的 evidence↔产物绑定：读 provenance 指的产物 → 先校 sha256 → np.load → 依 caseset policy 重算
    metrics → 与 evidence 自报 metrics 逐字段比对。任一环不符/缺失 → FAILED（mock 也不放宽）。"""
    prov = prec.get("provenance")
    if not isinstance(prov, dict):
        errs.append(f"{cid}: evidence.precision 缺 provenance（A 方案产物绑定缺失·metrics 真伪不可校验）")
        return
    miss = [k for k in ("golden_sha256", "out_sha256", "numel") if prov.get(k) is None]
    if miss:
        errs.append(f"{cid}: provenance 缺字段 {miss}")
        return
    gt = _pinned_product(d, prec.get("golden_path"))
    ot = _pinned_product(d, prec.get("out_path"))
    if gt is None:
        errs.append(f"{cid}: golden 产物缺失/路径逃逸/非普通文件（{prec.get('golden_path')!r}）")
    if ot is None:
        errs.append(f"{cid}: out 产物缺失/路径逃逸/非普通文件（{prec.get('out_path')!r}）")
    if gt is None or ot is None:
        return
    # 先校 sha256——产物字节被替换/篡改而 provenance 未同改 → 不符 → FAILED（堵「改 out.npy 字节」洞）。
    # pv-3：读 bytes 与 sha/load 共用同一份内存（_load_verified），杜绝二次 open 的 TOCTOU。
    golden = _load_verified(np, gt, prov["golden_sha256"], cid, "golden", errs)
    # out 产物有两种落盘形态，**按字节本身判**（`.npy` 有 magic），不按扩展名、更不按算子/通路身份：
    #   · `.npy`（new_example 等 runner 直接 np.save）；
    #   · raw 扁平 `.bin`（aclnn_py driver 的 dump）——legacy 单输出通路以前只会 np.load，
    #     于是 aclnn_py + 单输出的组合在这道门上恒 FAILED（Cannot load file containing pickled data）。
    # raw 形态的 dtype/shape **只从 caseset.expected 取**（spec 派生的 canonical 判据），
    # 不取 evidence 自报——判据不得随被校验方的自报值漂移。
    out = _load_verified_out(
        np, ot, prov["out_sha256"], cid, "out", errs,
        dtype_name=exp.get("compare_dtype"), shape=exp.get("out_shape"))
    if golden is None or out is None:
        return
    if not _is_int(prov["numel"]) or int(golden.size) != prov["numel"]:
        errs.append(f"{cid}: golden.numel={int(golden.size)} ≠ provenance.numel={prov['numel']!r}")
    policy = exp.get("policy")
    if not isinstance(policy, dict):
        errs.append(f"{cid}: caseset.expected.policy 非 dict（无法据 caseset 口径重算 metrics）")
        return
    # 依 caseset 的 standard/compare_dtype 重算（policy 已在上文三处一致门校过 == evidence policy）。
    # ⚠ 用与 repo_adapter **同一份** precision_policy.compute_metrics——目的是绑定 evidence↔产物，**不是**
    #   交叉验证 metric 实现（若换一份实现比对，就变成验证算法而非「数字是否真从产物算出」了）。
    try:
        recalc = precision_policy.compute_metrics(out, golden, policy)
    except Exception as ex:
        errs.append(f"{cid}: 依 caseset policy 重算 metrics 失败（{type(ex).__name__}: {ex}）——不静默放行")
        return
    _metrics_match(recalc, prec.get("metrics"), cid, errs, tag="metrics")
    acc_pol = exp.get("acceptance_policy")   # spec 声明 acceptance 时一并绑定（本 scope 一般不触发）
    if isinstance(acc_pol, dict):
        try:
            racc = precision_policy.compute_metrics(out, golden, acc_pol)
        except Exception as ex:
            errs.append(f"{cid}: 依 caseset acceptance_policy 重算失败（{type(ex).__name__}: {ex}）")
            return
        _metrics_match(racc, prec.get("acceptance_metrics"), cid, errs, tag="acceptance_metrics")


#: `.npy` 的固定魔数（numpy format spec）。用它判落盘形态——比扩展名可靠，也不需要任何
#: 「这条通路会产哪种文件」的先验知识。
_NPY_MAGIC = b"\x93NUMPY"


def _load_verified_out(np, path, want_sha, cid, kind, errs, dtype_name=None, shape=None):
    """legacy 单输出 out 产物的形态自适应读回：`.npy` 走 np.load，raw 扁平字节走 frombuffer。

    两条分支的 sha256 绑定与 TOCTOU 纪律完全相同（都是「一次性读入 bytes → 校 sha → 从同一份内存解释」）。
    raw 分支的 dtype/shape 由调用方从 **caseset.expected**（canonical 判据）传入，并要求
    `nbytes` 与 `numel(shape) * itemsize` 精确相等——多一字节少一字节都拒。
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as ex:
        errs.append(f"{cid}: {kind} 产物读取失败（{type(ex).__name__}: {ex}）")
        return None
    if hashlib.sha256(data).hexdigest() != want_sha:
        errs.append(f"{cid}: {kind} 产物 sha256 与 provenance 不符（产物被替换/篡改）")
        return None
    if data[:len(_NPY_MAGIC)] == _NPY_MAGIC:
        import io
        try:
            return np.load(io.BytesIO(data), allow_pickle=False)
        except Exception as ex:
            errs.append(f"{cid}: {kind} 产物 np.load 失败（{type(ex).__name__}: {ex}）")
            return None
    if not isinstance(dtype_name, str) or not dtype_name:
        errs.append(f"{cid}: {kind} 产物是 raw 字节，但 caseset.expected 缺 compare_dtype"
                    "（无法按 canonical 口径读回·证据不完整）")
        return None
    if not _mo_shape_ok(shape):
        errs.append(f"{cid}: {kind} 产物是 raw 字节，但 caseset.expected.out_shape 非法：{shape!r}")
        return None
    try:
        dt = np.dtype(dtype_name)
    except Exception as ex:
        errs.append(f"{cid}: {kind} 产物 compare_dtype={dtype_name!r} 非法（{type(ex).__name__}: {ex}）")
        return None
    want_bytes = _mo_numel(shape) * dt.itemsize
    if len(data) != want_bytes:
        errs.append(f"{cid}: {kind} 产物字节数 {len(data)} ≠ caseset 口径应有的 {want_bytes}"
                    f"（dtype={dtype_name} shape={shape}·磁盘字节与 canonical 判据不符）")
        return None
    try:
        return np.frombuffer(data, dtype=dt).reshape(shape)
    except Exception as ex:
        errs.append(f"{cid}: {kind} 产物按 canonical dtype/shape 解释失败（{type(ex).__name__}: {ex}）")
        return None


def _load_verified_bin(np, path, want_sha, dtype_name, shape, cid, kind, errs):
    """多输出通路的 out 产物是 **raw `.bin`**（driver 扁平 dump），不是 `.npy` → 不能走 `np.load`。

    与 `_load_verified` 同纪律：**一次性读入 bytes** → sha256 校 provenance → 从**同一份内存** `frombuffer`
    按「evidence 自报的 dtype/shape」解释（消灭二次 open 的 TOCTOU）。防「dtype/shape 随便报、字节随便解释」：
    要求 `nbytes == numel(shape) * itemsize` **精确相等**（多一字节少一字节都拒），不合法即 error 返回 None。"""
    if not isinstance(dtype_name, str) or not dtype_name:
        errs.append(f"{cid}: {kind} 产物缺 out_dtype（无法按真实字节口径读回·证据不完整）")
        return None
    if not _mo_shape_ok(shape):
        errs.append(f"{cid}: {kind} 产物 out_shape 非法（须非负整数 list）：{shape!r}")
        return None
    try:
        dt = np.dtype(dtype_name)
    except Exception as ex:
        errs.append(f"{cid}: {kind} 产物 out_dtype={dtype_name!r} 非法（{type(ex).__name__}: {ex}）")
        return None
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as ex:
        errs.append(f"{cid}: {kind} 产物读取失败（{type(ex).__name__}: {ex}）")
        return None
    if hashlib.sha256(data).hexdigest() != want_sha:
        errs.append(f"{cid}: {kind} 产物 sha256 与 provenance 不符（产物被替换/篡改）")
        return None
    want_bytes = _mo_numel(shape) * dt.itemsize
    if len(data) != want_bytes:
        errs.append(f"{cid}: {kind} 产物字节数 {len(data)} ≠ dtype/shape 应有的 {want_bytes}"
                    f"（dtype={dtype_name} shape={shape}·自报口径与磁盘字节不符）")
        return None
    try:
        return np.frombuffer(data, dtype=dt).reshape(shape)
    except Exception as ex:
        errs.append(f"{cid}: {kind} 产物按 dtype/shape 解释失败（{type(ex).__name__}: {ex}）")
        return None


def _mo_gather_ctx(d, case, policy, out_shape, cid, errs):
    """index 输出重算所需的 gather 上下文（source/dim/keepdim）——**与采集层同一份实现**。

    复用 `repo_adapter._index_gather_ctx`（据 policy.gather_from 定位输入 + 据 attr 值和形状唯一解出归约轴，
    全程 op-中立、绝不读 attr 名/算子名）。**刻意共用同一实现**：本门的目的是绑定「evidence 的数字是否真从
    产物算出」，不是交叉验证 gather 算法——换一份实现比对会变成验证算法本身（同 compute_metrics 的既有纪律）。
    import 失败 / 归约轴歧义 → 记 error 返回 None（fail-closed，绝不猜轴：猜错轴会静默算出「看起来对」的数）。"""
    try:
        import repo_adapter
    except Exception as ex:
        errs.append(f"{cid}: repo_adapter 不可用（{type(ex).__name__}: {ex}）——index 输出 gather 重算无法进行，判 FAILED")
        return None
    try:
        return repo_adapter._index_gather_ctx(case, os.path.join(d, "work"), policy, list(out_shape))
    except Exception as ex:
        errs.append(f"{cid}: index 输出 gather 上下文重建失败（{type(ex).__name__}: {ex}）——不静默放行")
        return None


def _recompute_case_multi(np, precision_policy, d, cid, case, exp, prec, errs):
    """多输出契约的 evidence↔产物绑定（**逐输出**）：golden `.npy` + out `.bin` 各自校 sha → 依 caseset
    该输出的 policy 重算 metrics → 与 evidence 自报值逐字段比对。任一环不符 → FAILED。

    · golden/out 路径均**相对 `<d>/work`** 解析（与 legacy 同一根，`_pinned_product` 钉死、拒逃逸）。
      out 是 raw `.bin`，按 evidence 自报的 `out_dtype`/`out_shape` 读回，并要求字节数精确吻合。
    · 两侧一律 reshape 到 **caseset 声明**的 `out_shape`（authoritative；0-d 归约输出的 `(1,)` vs `()` 差异
      在此归一，与采集层同口径），numel 不等即 fail-closed。
    · index 输出走 `index_value_consistency`：重建 gather 上下文（gather 源 + 归约轴）后再重算。"""
    outs = exp.get(_MO_KEY)
    ev_outs = prec.get(_MO_KEY)
    if not isinstance(outs, list) or not isinstance(ev_outs, list) or len(outs) != len(ev_outs) or not outs:
        return                                          # 结构问题已由 _gate_task2_outputs 记 error
    for k, (exp_o, ev_o) in enumerate(zip(outs, ev_outs)):
        if not isinstance(exp_o, dict) or not isinstance(ev_o, dict):
            continue                                    # 已记 error
        role = exp_o.get("role")
        tag = f"{cid}#{k}({role})"
        prov = ev_o.get("provenance")
        if not isinstance(prov, dict):
            errs.append(f"{tag}: evidence 输出缺 provenance（产物绑定缺失·metrics 真伪不可校验）")
            continue
        miss = [x for x in ("golden_sha256", "out_sha256", "numel") if prov.get(x) is None]
        if miss:
            errs.append(f"{tag}: provenance 缺字段 {miss}")
            continue
        gt = _pinned_product(d, ev_o.get("golden_path"))
        ot = _pinned_product(d, ev_o.get("out_path"))
        if gt is None:
            errs.append(f"{tag}: golden 产物缺失/路径逃逸/非普通文件（{ev_o.get('golden_path')!r}）")
        if ot is None:
            errs.append(f"{tag}: out 产物缺失/路径逃逸/非普通文件（{ev_o.get('out_path')!r}）")
        if gt is None or ot is None:
            continue
        golden = _load_verified(np, gt, prov["golden_sha256"], tag, "golden", errs)
        out = _load_verified_bin(np, ot, prov["out_sha256"], ev_o.get("out_dtype"),
                                 ev_o.get("out_shape"), tag, "out", errs)
        if golden is None or out is None:
            continue
        if not _is_int(prov["numel"]) or int(golden.size) != prov["numel"]:
            errs.append(f"{tag}: golden.numel={int(golden.size)} ≠ provenance.numel={prov['numel']!r}")
        if str(out.dtype) != str(golden.dtype):
            errs.append(f"{tag}: out 产物 dtype={out.dtype} ≠ golden dtype={golden.dtype}"
                        "（两侧口径不同·无法按同一口径重算·fail-closed）")
            continue
        shape = exp_o.get("out_shape")                  # caseset 声明形状为权威（与采集层同口径）
        if not _mo_shape_ok(shape):
            errs.append(f"{tag}: caseset 输出 out_shape 非法（{shape!r}）——无法归一形状重算")
            continue
        want = _mo_numel(shape)
        if int(golden.size) != want or int(out.size) != want:
            errs.append(f"{tag}: golden/out 元素数（{int(golden.size)}/{int(out.size)}）"
                        f"≠ caseset out_shape {shape} 应有的 {want}（形状契约不符）")
            continue
        golden, out = golden.reshape(shape), out.reshape(shape)
        policy = exp_o.get("policy")
        if not isinstance(policy, dict):
            errs.append(f"{tag}: caseset 输出 policy 非 dict（无法据 caseset 口径重算 metrics）")
            continue
        kwargs = {}
        if policy.get("kind") == "index_value_consistency":
            gctx = _mo_gather_ctx(d, case, policy, shape, tag, errs)
            if gctx is None:
                continue
            kwargs["gather_ctx"] = gctx
        try:
            recalc = precision_policy.compute_metrics(out, golden, policy, **kwargs)
        except Exception as ex:
            errs.append(f"{tag}: 依 caseset policy 重算 metrics 失败（{type(ex).__name__}: {ex}）——不静默放行")
            continue
        _metrics_match(recalc, ev_o.get("metrics"), tag, errs, tag="metrics")


def _gate_precision_provenance(d, ev_list, exp_by_id, errs, case_by_id=None):
    """A 方案总入口：证明 evidence.precision.metrics **确实从磁盘产物算出**（属**证据可信**，不重判 verdict——
    canon 定「门只管证据可信完整、pass/fail 归 validator」，重算校验的是「evidence 声称的数字是否真从产物算出」，
    仍属证据可信，pass/fail 由 validator 依阈值裁）。

    硬纪律：numpy 缺失 / 产物缺失 / sha 不符 / 重算不符 一律 FAILED（mock 也不放宽），**绝不静默 skip**——否则
    等于留「删掉 numpy 即绕过」的后门。
    ⚠ 已知边界（诚实、勿写成「已防伪造」）：A 只证「metrics 由 golden/out 这两文件算出」，**不证**「这两文件来自
       一次真 NPU 跑测」。同时控制产物+evidence 的攻击者把 out.npy 写成 golden.npy 的副本 → bad_count=0 是「真的」，
       只是它没测 NPU。产物↔真机来源的绑定须 OPRUNWAY_DONE 哨兵 / raw log hash / msprof 输出绑定（本轮不做）。"""
    try:
        import numpy as np
        import precision_policy
    except Exception as ex:   # pv-5：不止 ImportError——破损/伪 numpy 抛 RuntimeError 等非 ImportError 亦须判
        # FAILED（旧洞：`import precision_policy` 在 try 外 + 只兜 ImportError → 非 ImportError 穿透
        # gate_task2→main 无 try → 门 traceback 崩溃，违反模块「抗坏输入…绝不崩溃」契约）。
        errs.append(f"numpy/precision_policy 不可用（{type(ex).__name__}: {ex}）——A 方案产物重算无法进行，"
                    "判 FAILED（绝不静默 skip，否则「删掉/弄坏 numpy 即绕过」；亦不 traceback 崩溃）")
        return
    for e in ev_list:
        if not isinstance(e, dict) or not isinstance(e.get("case_id"), str) or not e["case_id"]:
            continue                                   # 缺/坏 case_id 已在上文报
        cid = e["case_id"]
        exp = exp_by_id.get(cid)
        prec = e.get("precision")
        if exp is None or not isinstance(prec, dict):
            continue                                   # 多余 case / 缺 precision 已在上文报
        try:
            if _is_multi_output(exp):                  # 多输出契约：逐输出绑定（legacy 单输出走原路径、零变更）
                case = (case_by_id or {}).get(cid)
                if not isinstance(case, dict):
                    errs.append(f"{cid}: caseset 无该 case（多输出重算需整条 case 的输入/attr）")
                    continue
                _recompute_case_multi(np, precision_policy, d, cid, case, exp, prec, errs)
            else:
                _recompute_case(np, precision_policy, d, cid, exp, prec, errs)
        except Exception as ex:                        # 抗坏输入：任何意外 → FAILED、绝不崩溃/静默放过
            errs.append(f"{cid}: 产物重算校验异常（{type(ex).__name__}: {ex}）——判 FAILED，不崩溃")


def _gate_small_shape_exception(pr, d, errs):
    """小shape例外门（T6 H6/H7/M2）：simulation 完整 + 例外行↔simulation 集合/数值交叉一致
    + 落盘 SVG(路径钉死在 d 内、sha256 重算相符)。任一不满足 → 不可静默放行 → FAILED。"""
    tag = "小shape例外缺/对不上仿真图或分析·不可静默放行"
    sim = pr.get("simulation")
    if (not isinstance(sim, dict) or "when_us_below" not in sim or "abs_gap_us_within" not in sim
            or not isinstance(sim.get("points"), list) or not sim["points"]):
        errs.append(f"{tag}：simulation 缺失/不完整")
        return
    per = pr.get("per_case") if isinstance(pr.get("per_case"), list) else []
    exc_rows = {r["case_id"]: r for r in per
                if isinstance(r, dict) and isinstance(r.get("case_id"), str) and r["case_id"]
                and r.get("exception")}
    sim_pts = {p["case_id"]: p for p in sim["points"]
               if isinstance(p, dict) and isinstance(p.get("case_id"), str) and p["case_id"]}
    if set(exc_rows) != set(sim_pts):
        errs.append(f"{tag}：例外行 {sorted(exc_rows)} ≠ simulation 点 {sorted(sim_pts)}")
        return
    for cid, p in sim_pts.items():
        det = exc_rows[cid].get("exception_detail") or {}
        for k in ("npu_us", "baseline_us", "gap", "within"):
            if p.get(k) != det.get(k):
                errs.append(f"{tag}：{cid} simulation.{k}={p.get(k)} ≠ exception_detail.{k}={det.get(k)}")
    plot = pr.get("simulation_plot")
    if not isinstance(plot, dict) or not plot.get("file") or not plot.get("sha256"):
        errs.append(f"{tag}：缺 simulation_plot(file/sha256)")
        return
    fname = plot["file"]
    # gt3-7 第一道守卫：basename 必须 .svg——挡把 file 指向 caseset.json 等非图产物（旧洞：任意文件皆过）。
    if not (isinstance(fname, str) and os.path.basename(fname).lower().endswith(".svg")):
        errs.append(f"{tag}：simulation_plot.file 非 .svg（{fname!r}·防指向任意产物文件）")
        return
    target = _pinned_file(d, fname)
    if target is None:
        errs.append(f"{tag}：simulation_plot 路径逃逸/非普通文件 {fname!r}")
        return
    on_disk = _sha256(target)
    if on_disk != plot["sha256"]:
        errs.append(f"{tag}：simulation_plot sha256 不符（stale/被替换）")
        return
    # gt3-7 核心（重算比对）：用 simulation 数据在门内**确定性重算** SVG，要求落盘图字节 == 重算字节。
    # render_svg 纯 stdlib、确定性（无时间戳/随机/字典序依赖，float 用 .2f）→ 图真正锚定 simulation：
    # 指向任意文件/伪造 SVG（哪怕 sha 与该文件自洽）都无法与「本 simulation 派生的字节」对齐。
    # 只 import 调用 perf_sim_plot、绝不改它（并行任务文件）；渲染失败(坏数据/意外非确定)不静默放行。
    try:
        import tempfile
        import perf_sim_plot
        with tempfile.TemporaryDirectory() as _tmp:
            _rec = os.path.join(_tmp, "recomputed.svg")
            perf_sim_plot.render_svg(sim, _rec)
            expect_sha = _sha256(_rec)
    except Exception as ex:
        errs.append(f"{tag}：simulation_plot 重算失败（{type(ex).__name__}: {ex}）——无法锚定 simulation")
        return
    if on_disk != expect_sha:
        errs.append(f"{tag}：simulation_plot 与 simulation 数据不符"
                    "——落盘图非由本 simulation 渲染（伪造/换图/stale·图未真正锚定数据）")


def _perf_ids_from_caseset(cs, errs):
    """caseset 里 dims 含「性能」的 case IDs（含重复原样返回，供 Counter 全量比对查重）；抗坏字段。"""
    cases = cs.get("cases") if isinstance(cs, dict) else None
    if not isinstance(cases, list) or not cases:
        errs.append("缺/坏 caseset.json 或无用例（gate_task3 无法按 case 对齐性能证据、防跑子集）")
        return None
    ids = []
    for i, c in enumerate(cases):
        if not isinstance(c, dict):
            continue
        dims = c.get("dims")
        if isinstance(dims, list) and "性能" in dims:
            cid = c.get("id")
            if not cid:
                errs.append(f"caseset 性能 case[{i}] 缺 id")
                continue
            ids.append(cid)
    dup = [k for k, v in Counter(ids).items() if v > 1]
    if dup:
        errs.append(f"caseset 性能用例有重复 case_id: {dup}")
    return ids


def _perf_evidence_ids(ev_list):
    """带**真实 perf 载荷**（perf.us 有限正 + perf.scope 存在）的 evidence case_id 集（gt3-3）。
    只核「case_id 存在」会放过空壳 `{"case_id":"p0"}`——性能证据真实性须落到 perf 载荷本身。
    数据模型已支持（真实 evidence 项带 perf={scope,us}），故采「有载荷才计入」的更实口径。"""
    ids = set()
    for e in ev_list or []:
        if not isinstance(e, dict) or not isinstance(e.get("case_id"), str) or not e["case_id"]:
            continue  # 缺/坏 case_id 已由 _ids_from_evidence 报，此处只挑有真实 perf 载荷者
        perf = e.get("perf")
        if isinstance(perf, dict) and _perf_finite_pos(perf.get("us")) and perf.get("scope"):
            ids.add(e["case_id"])
    return ids


def _gate_perf_measurement_binding(cs, ev, pr, d, per, errs):
    """新 shape 报告契约下，把报告逐 case 数值锚回 evidence/baseline 独立产物。"""
    if not isinstance(cs, dict) or not isinstance(cs.get("perf_case_policy"), dict):
        return
    ev_by_id = {e.get("case_id"): e for e in ev.get("evidence") or []
                if isinstance(e, dict) and isinstance(e.get("case_id"), str)}
    for row in per:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            continue
        cid = row["case_id"]
        eperf = (ev_by_id.get(cid) or {}).get("perf")
        if not isinstance(eperf, dict):
            continue
        npu = row.get("npu_us")
        evidence_us = eperf.get("us")
        # 双侧都缺值由「真实性/完整性」门统一报；这里仅查一侧有值或双侧有值却不一致，
        # 避免 blocked 工件再附带误导性的 `None ≠ None`。
        if _perf_finite_pos(npu) or _perf_finite_pos(evidence_us):
            if not (_perf_finite_pos(npu) and _perf_finite_pos(evidence_us)
                    and math.isclose(float(npu), float(evidence_us),
                                     rel_tol=1e-12, abs_tol=1e-12)):
                errs.append(
                    f"{cid}: perf_report.npu_us={npu!r} ≠ evidence.perf.us={evidence_us!r}")
        row_scope = row.get("scope") if "scope" in row else row.get("npu_scope")
        evidence_scope = eperf.get("scope")
        if (row_scope is not None or evidence_scope is not None) and row_scope != evidence_scope:
            errs.append(f"{cid}: perf_report NPU scope={row_scope!r} "
                        f"≠ evidence.perf.scope={evidence_scope!r}")
    baseline = _load(d, "baseline.json")
    if not isinstance(baseline, dict):
        return
    base_by_id = {b.get("case_id"): b for b in baseline.get("per_case") or []
                  if isinstance(b, dict) and isinstance(b.get("case_id"), str)}
    for row in per:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            continue
        cid = row["case_id"]
        expected_base = base_by_id.get(cid)
        claimed = row.get("baseline")
        if expected_base is None:
            if isinstance(claimed, dict) and claimed.get("us") is not None:
                errs.append(f"{cid}: baseline.json 无此 case，perf_report 却声明 baseline.us")
            continue
        if not isinstance(claimed, dict):
            errs.append(f"{cid}: baseline.json 有此 case，perf_report 缺 baseline")
            continue
        claimed_us, expected_us = claimed.get("us"), expected_base.get("us")
        if _perf_finite_pos(claimed_us) or _perf_finite_pos(expected_us):
            if not (_perf_finite_pos(claimed_us) and _perf_finite_pos(expected_us)
                    and math.isclose(float(claimed_us), float(expected_us),
                                     rel_tol=1e-12, abs_tol=1e-12)):
                errs.append(f"{cid}: perf_report baseline.us={claimed_us!r} "
                            f"≠ baseline.json={expected_us!r}")
        if claimed.get("source") != baseline.get("source"):
            errs.append(f"{cid}: perf_report baseline.source={claimed.get('source')!r} "
                        f"≠ baseline.json.source={baseline.get('source')!r}")


def _gate_perf_case_alignment(pr, d, per, s, has_summary, st, errs):
    """per_case 与 caseset/evidence **按 case 对齐**（补 T5 门延后 finding）——防「跑性能子集 + 伪造
    summary=ok」蒙混：① caseset(dims 含「性能」)↔perf per_case 用 Counter 全量比对（拒缺/多/重复）；
    ② 性能 case 必须真有 evidence（拒伪造 per_case 未实跑）；③ summary 的 perf_cases/达标/blocked
    计数与 per_case 行级实际一致（拒伪造 summary）。此门只查完整性/一致性，不重判达标。"""
    # gt3-6②：case_id 为非空 list/dict 时 Counter(per_ids) 会崩 unhashable → 只收字符串 id
    # （非法 case_id 的 error 已在 gate_task3 行循环记，此处过滤免崩）。
    per_ids = [r.get("case_id") for r in per
               if isinstance(r, dict) and isinstance(r.get("case_id"), str) and r.get("case_id")]
    per_dups = [k for k, v in Counter(per_ids).items() if v > 1]
    if per_dups:
        errs.append(f"perf per_case 有重复 case_id: {per_dups}")
    cs = _load(d, "caseset.json")
    if cs == "__BAD__":
        errs.append("caseset.json 解析失败（无法做性能 per_case 对齐、防跑子集）")
    else:
        perf_ids = _perf_ids_from_caseset(cs, errs)  # cs=None 时内部记 error 并返回 None
        if perf_ids is not None:
            # gt3-4 交叉：status=ok 但 caseset 无任何「性能」dim 用例 → 口径矛盾（应为 no_perf_cases）。
            if st == "ok" and not perf_ids:
                errs.append("status=ok 但 caseset 无「性能」dim 用例（0 性能用例应为 no_perf_cases·口径矛盾）")
            want, got = Counter(perf_ids), Counter(per_ids)
            miss = sorted((want - got).elements())
            extra = sorted((got - want).elements())
            if miss:
                errs.append(f"⚠跑性能子集：perf per_case 缺 {miss}（caseset 性能用例有、perf 无）")
            if extra:
                errs.append(f"perf per_case 多出 {extra}（caseset 无对应性能用例）")
            ev = _load(d, "evidence.json")
            if isinstance(ev, dict):
                _ids_from_evidence(ev.get("evidence"), errs)  # 报 evidence 缺 case_id/重复（副作用）
                perf_eids = _perf_evidence_ids(ev.get("evidence"))  # gt3-3：须带真实 perf 载荷
                ev_miss = sorted(cid for cid in set(perf_ids) if cid not in perf_eids)
                if ev_miss:
                    errs.append(f"⚠性能证据缺失/空壳：evidence 无真实 perf 载荷 {ev_miss}"
                                "（性能用例未实跑/伪造 per_case/空壳证据）")
                _gate_perf_measurement_binding(cs, ev, pr, d, per, errs)
            elif ev == "__BAD__":
                errs.append("evidence.json 解析失败（无法核性能证据真实性）")
            elif ev is None:
                errs.append("缺 evidence.json（无法核性能证据真实性、防伪造 per_case）")
    # summary 计数须与 per_case 行级一致（防伪造 summary 蒙混）——summary 缺失已在上文报，跳过免噪。
    # gt3-8：summary 三计数用 _is_int（拒 bool，True==1 曾被当合法计数）；行级 达标 强制 bool
    # （达标="yes" 曾按 truthy 计入），非 bool 记 error 再按严格 is True 计数。
    if has_summary:
        n_meet = 0
        n_blocked = 0
        for r in per:
            if not isinstance(r, dict):
                continue
            da = r.get("达标")
            if da is not None and not isinstance(da, bool):
                errs.append(f"{r.get('case_id', '?')}: 达标 非 bool（{da!r}）——伪计数")
            if da is True:
                n_meet += 1
            if r.get("blocked") is True:
                n_blocked += 1
        for key, actual in (("perf_cases", len(per)), ("达标", n_meet), ("blocked", n_blocked)):
            claimed = s.get(key)
            if not _is_int(claimed):
                errs.append(f"summary.{key}={claimed!r} 非整数计数（拒 bool/非法类型）")
            elif claimed != actual:
                errs.append(f"summary.{key}={claimed!r} 与 per_case 行级实际 {actual} 不一致（伪造/漏计）")


def _gate_shape_report(pr, d, per, errs):
    """核大小 shape 报告覆盖与计数；只验派生完整性，不重判性能阈值。"""
    cs = _load(d, "caseset.json")
    if not isinstance(cs, dict) or not isinstance(cs.get("perf_case_policy"), dict):
        return                                      # legacy caseset 没声明新契约，保持兼容
    policy = cs["perf_case_policy"]
    by_id = {c.get("id"): c for c in (cs.get("cases") or [])
             if isinstance(c, dict) and isinstance(c.get("id"), str)}
    expected_counts = (policy.get("counts") or {}) if isinstance(policy.get("counts"), dict) else {}
    actual = {k: {"planned_cases": 0, "cases_scored": 0, "达标": 0, "blocked": 0,
                  "npu_all": [], "baseline_all": [], "paired_npu": [], "paired_baseline": []}
              for k in ("small", "large")}
    for r in per:
        if not isinstance(r, dict) or not isinstance(r.get("case_id"), str):
            continue
        meta = (by_id.get(r["case_id"]) or {}).get("perf_shape_classification")
        cls = meta.get("class") if isinstance(meta, dict) else None
        if cls not in actual:
            errs.append(f"{r['case_id']}: 声明了 perf_case_policy 但缺/坏大小 shape 分类")
            continue
        a = actual[cls]
        a["planned_cases"] += 1
        npu = r.get("npu_us")
        base = (r.get("baseline") or {}).get("us")
        if _perf_finite_pos(npu):
            a["npu_all"].append(float(npu))
        if _perf_finite_pos(base):
            a["baseline_all"].append(float(base))
        scored = ("ratio" in r and _perf_finite_pos(npu) and _perf_finite_pos(base))
        a["cases_scored"] += int(scored)
        if scored:
            a["paired_npu"].append(float(npu))
            a["paired_baseline"].append(float(base))
        a["达标"] += int(r.get("达标") is True)
        a["blocked"] += int(r.get("blocked") is True)
    for cls in ("small", "large"):
        if expected_counts.get(cls) != actual[cls]["planned_cases"]:
            errs.append(f"{cls}: perf_case_policy.counts={expected_counts.get(cls)!r} "
                        f"与性能行实际 {actual[cls]['planned_cases']} 不一致")
    if pr.get("shape_report_complete") is not True:
        errs.append(f"大小 shape 报告不完整：{pr.get('shape_report_problems')}")
    rows = pr.get("by_shape_class")
    if not isinstance(rows, list):
        errs.append("perf_report 缺 by_shape_class")
        return
    got = {r.get("class"): r for r in rows if isinstance(r, dict)}
    count_keys = ("planned_cases", "cases_scored", "达标", "blocked")

    def med(vals):
        return float(statistics.median(vals)) if vals else None

    def same_number(got_value, expected_value):
        if expected_value is None:
            return got_value is None
        return (isinstance(got_value, (int, float)) and not isinstance(got_value, bool)
                and math.isfinite(got_value)
                and math.isclose(float(got_value), float(expected_value), rel_tol=1e-12, abs_tol=1e-12))

    for cls in ("small", "large"):
        row = got.get(cls)
        if not isinstance(row, dict):
            errs.append(f"by_shape_class 缺 {cls} 行")
            continue
        for key in count_keys:
            value = actual[cls][key]
            if row.get(key) != value:
                errs.append(f"by_shape_class[{cls}].{key}={row.get(key)!r} 与行级实际 {value} 不一致")
        if row.get("cases") != actual[cls]["planned_cases"]:
            errs.append(f"by_shape_class[{cls}].cases 与 planned_cases 不一致")
        exp_npu, exp_base = med(actual[cls]["npu_all"]), med(actual[cls]["baseline_all"])
        pair_npu, pair_base = med(actual[cls]["paired_npu"]), med(actual[cls]["paired_baseline"])
        exp_speedup = (pair_base / pair_npu) if pair_npu is not None else None
        for key, value in (("npu_us", exp_npu), ("baseline_us", exp_base), ("speedup", exp_speedup)):
            if not same_number(row.get(key), value):
                errs.append(f"by_shape_class[{cls}].{key}={row.get(key)!r} 与行级派生 {value!r} 不一致")
    overall = pr.get("shape_overall")
    if not isinstance(overall, dict):
        errs.append("perf_report 缺 shape_overall")
        return
    sums = {k: sum(actual[cls][k] for cls in ("small", "large")) for k in count_keys}
    for key, value in sums.items():
        if overall.get(key) != value:
            errs.append(f"shape_overall.{key}={overall.get(key)!r} 与大小桶合计 {value} 不一致")
    if overall.get("cases") != sums["planned_cases"]:
        errs.append("shape_overall.cases 与 planned_cases 不一致")
    all_npu = actual["small"]["npu_all"] + actual["large"]["npu_all"]
    all_base = actual["small"]["baseline_all"] + actual["large"]["baseline_all"]
    pair_npu = actual["small"]["paired_npu"] + actual["large"]["paired_npu"]
    pair_base = actual["small"]["paired_baseline"] + actual["large"]["paired_baseline"]
    pn, pb = med(pair_npu), med(pair_base)
    exp_numbers = {"npu_us": med(all_npu), "baseline_us": med(all_base),
                   "speedup": (pb / pn) if pn is not None else None}
    for key, value in exp_numbers.items():
        if not same_number(overall.get(key), value):
            errs.append(f"shape_overall.{key}={overall.get(key)!r} 与行级派生 {value!r} 不一致")


def _gate_non_passing_report(pr, d, per, s, errs):
    """核最终报告没有遗漏性能未通过 case；只验派生完整性，不重判 ratio。"""
    expected_rows = [
        row for row in per
        if isinstance(row, dict) and row.get("达标") is not True
        and isinstance(row.get("case_id"), str)
    ]
    details = pr.get("non_passing_cases")
    if not expected_rows:
        if details is not None and details != []:
            errs.append("perf_report.non_passing_cases 在全部达标时须为空数组")
        return
    if not isinstance(details, list):
        errs.append("perf_report 缺 non_passing_cases（性能失败/挂起用例未逐条记录）")
        return
    detail_ids = [item.get("case_id") for item in details if isinstance(item, dict)]
    expected_ids = [row["case_id"] for row in expected_rows]
    if len(details) != len(detail_ids) or len(set(detail_ids)) != len(detail_ids):
        errs.append("perf_report.non_passing_cases 含非对象、坏 case_id 或重复 case_id")
    if Counter(detail_ids) != Counter(expected_ids):
        errs.append(
            f"perf_report.non_passing_cases 与未通过 per_case 不一致："
            f"报告={sorted(x for x in detail_ids if isinstance(x, str))}，"
            f"应为={sorted(expected_ids)}"
        )
    cs = _load(d, "caseset.json") or {}
    case_by_id = {
        c.get("id"): c for c in (cs.get("cases") or [])
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    }
    row_by_id = {row["case_id"]: row for row in expected_rows}
    status = str((s or {}).get("status") or "")
    failed = exceptions = 0
    for item in details:
        if not isinstance(item, dict) or not isinstance(item.get("case_id"), str):
            continue
        cid = item["case_id"]
        row = row_by_id.get(cid)
        case = case_by_id.get(cid) or {}
        if row is None:
            continue
        if row.get("blocked") or status.startswith("blocked"):
            outcome = "blocked"
        elif row.get("exception"):
            outcome = "exception"
            exceptions += 1
        else:
            outcome = "failed"
            failed += 1
        if item.get("outcome") != outcome:
            errs.append(
                f"{cid}: non_passing_cases.outcome={item.get('outcome')!r} "
                f"与 per_case 派生 {outcome!r} 不一致")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            errs.append(f"{cid}: non_passing_cases 缺非空 reason")
        inputs = case.get("inputs") if isinstance(case.get("inputs"), list) else []
        expected_inputs = [
            {"name": inp.get("name"), "shape": inp.get("shape")}
            for inp in inputs if isinstance(inp, dict)
        ]
        if item.get("inputs") != expected_inputs:
            errs.append(f"{cid}: non_passing_cases.inputs 与 caseset 输入 shape 不一致")
        first = inputs[0] if inputs and isinstance(inputs[0], dict) else {}
        dtype = first.get("dtype") if isinstance(first.get("dtype"), str) and first.get("dtype") else "unknown"
        if item.get("dtype") != dtype:
            errs.append(f"{cid}: non_passing_cases.dtype={item.get('dtype')!r} 与 caseset {dtype!r} 不一致")
        meta = case.get("perf_shape_classification")
        meta = meta if isinstance(meta, dict) else {}
        if item.get("shape_class") != meta.get("class"):
            errs.append(f"{cid}: non_passing_cases.shape_class 与 caseset 不一致")
        if item.get("input_bytes") != meta.get("input_bytes"):
            errs.append(f"{cid}: non_passing_cases.input_bytes 与 caseset 不一致")
        if not isinstance(item.get("custom"), dict) or not isinstance(item.get("baseline"), dict):
            errs.append(f"{cid}: non_passing_cases 缺 custom/baseline 明细")
    for key, expected in (
            ("non_passing", len(expected_rows)), ("failed", failed), ("exceptions", exceptions)):
        if (s or {}).get(key) != expected:
            errs.append(f"summary.{key}={(s or {}).get(key)!r} 与未通过明细派生 {expected} 不一致")


def gate_task3(d, errs):
    """性能证据**完整性**门：summary 完整 + scope=kernel_only(防混 e2e，缺 scope 也不放过)
    + 非 blocked(可采集) + 有性能用例 + per_case 与 caseset/evidence 按 case 对齐(防跑子集/伪造 summary)。
    注：达标/未达标由 perf_compare 判、**此门不重判**。
    T6：status=exception → 强制有仿真图 + 交叉一致 + sha 校验（_gate_small_shape_exception）。
    T8：blocked_wait_gpu_benchmark=正规挂起(不计完整性 FAILED)但仍卡 NPU 侧完整性；
        blocked_incomparable_timing_scope=双边口径不可比→FAILED。安全护栏(codex H4)：门放行挂起
        只代表 NPU 证据完整，整体绝不显 PASS——那由 run_workflow 映射为 BLOCKED_* + 非零退出。"""
    pr = _load(d, "perf_report.json")
    if not isinstance(pr, dict):
        errs.append("缺/坏 perf_report.json（Task3 未跑）")
        return
    _gate_cpp_extension_perf_collection(d, errs)
    s = pr.get("summary")
    has_summary = isinstance(s, dict)
    if not has_summary:
        errs.append("perf_report 缺 summary（产物不完整）")
        s = {}
    st = s.get("status")
    # gt3-6①：status 为 list/dict 时 `st not in _PERF_STATUS`（对 set 成员判定）会崩 unhashable →
    # 先 isinstance(str) 守卫，非字符串记 error 且不参与 set 判定。
    wait = isinstance(st, str) and st in _PERF_WAIT_STATUS
    if st is None:
        errs.append("perf summary 缺 status")
    elif not isinstance(st, str):
        errs.append(f"perf summary.status 非字符串（{type(st).__name__}）——产物损坏，不参与状态判定")
    elif st not in _PERF_STATUS:
        errs.append(f"perf status={st!r} 非法（须属 {sorted(_PERF_STATUS)}）")
    elif st == "no_perf_cases":
        errs.append("无性能用例（任务书若声明性能目标→用例缺陷）")
    elif st == "blocked":
        errs.append(f"性能 blocked·无法采集：{pr.get('notes')}")
    elif st == "blocked_incomparable_timing_scope":
        errs.append(f"性能 timing_scope 不可比·NPU/基线口径不一致（不出结论）：{pr.get('notes')}")
    # blocked_wait_gpu_benchmark：正规挂起，不计完整性 error；NPU 侧完整性在下方 per_case 卡。
    per = pr.get("per_case") if isinstance(pr.get("per_case"), list) else []
    for i, r in enumerate(per):
        if not isinstance(r, dict):
            errs.append(f"perf per_case[{i}] 非对象")
            continue
        cid = r.get("case_id")
        if not (isinstance(cid, str) and cid):  # gt3-6②：非空 list/dict 的 case_id 会让下游 Counter 崩
            errs.append(f"perf per_case[{i}] 缺/坏 case_id（{cid!r}）")
            continue
        # trivial 自动免测已移除；任何旧产物或伪造行都不得再借该字段绕过真实性能 scope。
        if r.get("trivial") is True:
            errs.append(f"{cid}: trivial 自动免测已废止；性能用例须提供真实、同口径的 kernel_only 测量")
            continue
        bl = r.get("blocked")  # gt3-8：blocked 强制 bool（非 bool 记 error 再参与判定；仅 True 视为 blocked）
        if bl is not None and not isinstance(bl, bool):
            errs.append(f"{cid}: blocked 非 bool（{bl!r}）")
        is_blocked = (bl is True)
        # gt3-2：wait 分支**先于** blocked-continue——挂起态所有性能行(含 blocked)强制 NPU 侧证据完整，
        # blocked 不得在 wait 态豁免 npu_us/npu_scope（旧洞：blocked-continue 先跑 → 标 blocked 即绕过）。
        if wait:
            if not _perf_finite_pos(r.get("npu_us")):
                errs.append(f"{cid}: 挂起态缺/坏 npu_us（NPU 证据不完整）")
            if r.get("npu_scope") != "kernel_only":
                errs.append(f"{cid}: npu_scope={r.get('npu_scope')!r} ≠ kernel_only")
            continue
        # gt3-1：blocked 行免 scope 校验只在 blocked-family（可挂起/不可采集）态成立；
        # status ∈ {ok, fail, exception} 下出现 blocked 行 = 零证据放行·口径矛盾 → 记 error（不再无条件 continue）。
        if is_blocked:
            if st not in _BLOCKED_OK_STATUS:
                errs.append(f"{cid}: status={st!r} 下出现 blocked 行"
                            "（零真实性能证据放行·口径矛盾）")
            continue
        if r.get("scope") != "kernel_only":  # 缺 scope(None) 也判失败
            errs.append(f"{cid}: scope={r.get('scope')!r} ≠ kernel_only（性能须 msprof op kernel-only）")
    # gt3-4：status=ok 与 0 性能用例自相矛盾（应为 no_perf_cases）→ 强制 perf_cases≥1 且 per_case 非空。
    if st == "ok":
        if not per:
            errs.append("status=ok 但 per_case 为空（0 性能证据自相矛盾，应为 no_perf_cases）")
        pc = s.get("perf_cases")
        if not (_is_int(pc) and pc >= 1):
            errs.append(f"status=ok 但 summary.perf_cases={pc!r}（须为≥1 整数；0 性能用例应为 no_perf_cases）")
    # per_case 与 caseset/evidence 按 case 对齐（补 T5 门延后 finding）：防跑性能子集 + 伪造 summary=ok。
    _gate_perf_case_alignment(pr, d, per, s, has_summary, st, errs)
    _gate_shape_report(pr, d, per, errs)
    _gate_non_passing_report(pr, d, per, s, errs)
    if st == "exception":
        _gate_small_shape_exception(pr, d, errs)
    print(f"  性能 status={st}(perf_compare 判) | 达标 {s.get('达标')}/{s.get('perf_cases')}")


def _gate_cpp_extension_perf_collection(d, errs):
    """从 evidence envelope 独立核 cpp_extension 的性能采集与 build receipt 同源。"""
    ev = _load(d, "evidence.json")
    cs = _load(d, "caseset.json")
    if not isinstance(ev, dict) or ev.get("runner_form") != "cpp_extension":
        return
    if not isinstance(cs, dict):
        errs.append("cpp_extension 性能门缺 caseset")
        return
    receipt = ev.get("cpp_extension_receipt")
    collect = ev.get("perf_collection")
    if not isinstance(receipt, dict) or not isinstance(collect, dict):
        errs.append("cpp_extension 性能门缺 build receipt/perf_collection")
        return
    provenance = collect.get("custom_provenance")
    expected_provenance = {
        "artifact": receipt.get("artifact"),
        "namespace": (receipt.get("load") or {}).get("namespace"),
        "invocation_plan": "cpp_extension_invocation_plan.json",
        "invocation_plan_sha256": (
            receipt.get("bindings") or {}).get("invocation_plan_sha256"),
        "vendor": {
            "library_path": (receipt.get("vendor") or {}).get("library_path"),
            "library_sha256": (receipt.get("vendor") or {}).get("library_sha256"),
            "symbols_owned": (receipt.get("vendor") or {}).get("symbols_owned"),
        },
    }
    checkpoint = collect.get("collection_checkpoint")
    perf_ids = [case.get("id") for case in (cs.get("cases") or [])
                if isinstance(case, dict)
                and "性能" in (case.get("dims") or [])]
    records = collect.get("records")
    record_ids = [row.get("case_id") for row in records
                  if isinstance(row, dict)] if isinstance(records, list) else None
    if (collect.get("custom_kind") != "cpp_extension"
            or provenance != expected_provenance):
        errs.append("cpp_extension perf_collection 的 ELF/vendor/namespace provenance 与 receipt 漂移")
    if (not isinstance(checkpoint, dict)
            or checkpoint.get("complete") is not True
            or checkpoint.get("planned_case_ids") != perf_ids
            or record_ids != perf_ids
            or not isinstance(records, list)
            or len(record_ids) != len(records)):
        errs.append("cpp_extension perf_collection 非完整性能 caseset 或 case 顺序漂移")
        return
    ev_by_id = {row.get("case_id"): row for row in (ev.get("evidence") or [])
                if isinstance(row, dict)}
    for record in records:
        cid = record["case_id"]
        custom = record.get("custom") if isinstance(record.get("custom"), dict) else {}
        evidence_perf = (ev_by_id.get(cid) or {}).get("perf")
        if not isinstance(evidence_perf, dict):
            errs.append(f"{cid}: cpp_extension evidence 缺 perf")
            continue
        if custom.get("us") != evidence_perf.get("us"):
            errs.append(f"{cid}: cpp_extension evidence.perf.us 与原始采集记录漂移")
        if evidence_perf.get("scope") != "kernel_only":
            errs.append(f"{cid}: cpp_extension evidence.perf.scope 非 kernel_only")


_GATES = {"task1": gate_task1, "task2": gate_task2, "task3": gate_task3}


def main(argv):
    ap = argparse.ArgumentParser(description="机器可校验验收门（三级，读 reports 产物 JSON）")
    ap.add_argument("--stage", required=True, choices=list(_GATES))
    ap.add_argument("--dir", required=True, help="run_workflow 的 --out 产物目录")
    a = ap.parse_args(argv)
    print(f"=== 验收门 stage={a.stage} dir={a.dir} ===")
    errs = []
    _GATES[a.stage](a.dir, errs)
    for e in errs:
        print(f"  ✗ {e}")
    passed = not errs
    print(f"STATUS: {'PASSED' if passed else 'FAILED'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
