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
import argparse, hashlib, json, math, os, sys
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
_VERDICT_ENUM = {"pass", "fail", "needs_review", "passed_with_risk", "passed_with_gaps"}
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


def _perf_finite_pos(x):
    """有限正数（拒 bool/None/NaN/inf/≤0）——挂起态 NPU 侧 us 完整性用。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


# §trivial-met 复核阈值：perf_compare 默认 perf_min_numel=4096；退化 case（numel<此）免测 perf。门据此复核
# 「trivial 声明」防伪造（大 case 谎报 trivial 跳 perf）。默认 shape 阶梯里退化 case<256、大 shape≥65535，
# 4096 落两者间的大间隙、无误伤；spec 若上调 perf_min_numel 超此，超出段的 trivial 会被门要求 scope（fail-closed 更严）。
_GATE_TRIVIAL_MAX_NUMEL = 4096


def _broadcast_shape(shapes):
    """numpy 广播规则纯 py 实现：右对齐、每维 1 可广播到 N、冲突→None；维须非 bool 非负 int 否则 None。"""
    out_rev, maxlen = [], max((len(s) for s in shapes), default=0)
    for i in range(maxlen):
        dim = 1
        for s in shapes:
            if i >= len(s):
                continue
            dd = s[len(s) - 1 - i]
            if not isinstance(dd, int) or isinstance(dd, bool) or dd < 0:
                return None
            if dd == 1:
                continue
            if dim == 1:
                dim = dd
            elif dim != dd:
                return None
        out_rev.append(dim)
    return list(reversed(out_rev))


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


def _caseset_numels(d):
    """{case_id: numel}（据全部输入 **broadcast 输出** numel，codex #1 防广播蒙混）；坏/不可广播 → None。
    供 gate_task3 trivial 复核。"""
    cs = _load(d, "caseset.json")
    out = {}
    if not (isinstance(cs, dict) and isinstance(cs.get("cases"), list)):
        return out
    for c in cs["cases"]:
        if not (isinstance(c, dict) and c.get("id")):
            continue
        inp = c.get("inputs") or []
        shapes = [it["shape"] for it in inp if isinstance(it, dict) and isinstance(it.get("shape"), list)]
        n = None
        if shapes and len(shapes) == len(inp):
            bs = _broadcast_shape(shapes)
            if bs is not None:
                n = 1
                for dd in bs:
                    n *= dd
        out[c["id"]] = n
    return out


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
    out = _load_verified(np, ot, prov["out_sha256"], cid, "out", errs)
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
    numel_by_id = _caseset_numels(d)              # §trivial-met 复核用：据 caseset numel 防伪造 trivial
    for i, r in enumerate(per):
        if not isinstance(r, dict):
            errs.append(f"perf per_case[{i}] 非对象")
            continue
        cid = r.get("case_id")
        if not (isinstance(cid, str) and cid):  # gt3-6②：非空 list/dict 的 case_id 会让下游 Counter 崩
            errs.append(f"perf per_case[{i}] 缺/坏 case_id（{cid!r}）")
            continue
        # §trivial-met（用户 2026-07-15，评审 #2）：perf_compare 标退化 case（numel<阈值）免测、无 scope。
        #  门放行但**据 caseset numel 复核**——大 case 谎报 trivial 跳 perf → error（gate-must-check-effective-object）。
        if r.get("trivial") is True:
            n = numel_by_id.get(cid)
            if isinstance(n, int) and 0 < n < _GATE_TRIVIAL_MAX_NUMEL:
                continue
            errs.append(f"{cid}: 标 trivial 但 caseset numel={n}（须 0<numel<{_GATE_TRIVIAL_MAX_NUMEL}；"
                        "疑伪造 trivial 跳 perf 完整性）")
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
    if st == "exception":
        _gate_small_shape_exception(pr, d, errs)
    print(f"  性能 status={st}(perf_compare 判) | 达标 {s.get('达标')}/{s.get('perf_cases')}")


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
