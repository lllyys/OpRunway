#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""expectations.py —— S1-Cholesky E 卡：族级期望集与期望项状态映射（accept 骨架的判定层）。

契约：dev-doc/solver/solver-s1-cholesky-spec.md §2.3′（七类期望项）、§2.5（report
结构与状态枚举）、§2.1（未生成 case 保留在期望集）、§0（数值判定/正式结论/证据不足
三层术语）。本模块只做「期望集怎么组、单项状态怎么定」，不做 I/O 与流程编排
（那是 accept_run.py 的职责），也不含任何残差或阈值实现（判据只在 criteria，
AGENTS.md §2 机械门纪律）。

七类期望项（spec §2.3′，五类扩为七类）：接口精度、P项性能、bufferSize、内存证据、
batched、确定性、info 契约。状态枚举（spec §2.5）：数值PASS｜数值FAIL｜证据不足｜待裁。
本片（模拟被测）各类的如实状态：

- 接口精度：随包交付的 case 逐条按 criteria.judge 的数值判定映射（见下）；canonical
  在册但未随包交付的 case 保留为「未生成」→ 证据不足（spec §2.1，不声称全覆盖）。
- P项性能：参考比值（方向 被测/基线）有被测耗时才算，正式门禁走任务书 §3.3 机制
  （2026-10 定稿：NPU 平均单次 kernel 耗时 ≤ GPU 参考耗时/0.35，GPU 参考值取
  bench_result.json 预填，NPU 侧 msprof op 采集；按采样证据逐 case 判定；缺证据时为证据不足）；
  无被测耗时则参考项证据不足（spec §2.3′）。
- bufferSize / 内存证据 / batched：本片未实现、未交付 → 证据不足（spec §1/§2.5）；
  任务书 2026-10 定稿 §3.4 内存不做要求——内存证据仅存证不设门。
- 确定性 / info 契约：任务书 §3.2 新增两类；本片模拟被测下均记证据不足（spec §2.3′）。

judge verdict → 接口精度状态的映射（s2-A1 一段式残差判定 + judge 的 error 语义）：

1. numeric == PASS → 数值PASS（残差 ≤ 阈值）。
2. numeric == FAIL 且 residual.ran 且 pass 非 null → 数值FAIL：残差层已对被测数据
   裁决（残差超阈，或被测输出退化到残差不可计算——ratio=null，error 指认原因）。
3. numeric == FAIL 且 residual 未运行（ran=False：被测状态非 ok / info≠0 / 输入
   缺失 / ratio_cpu 基线不可用 / judge 内部异常）→ 证据不足：没有可裁的证据，
   按 judge 文档「不可裁/证据问题」不得当精度 FAIL 上报。
4. numeric == FAIL 且 residual.ran 且 pass 为 null（缺 ratio_cpu_mean、残差超单支
   5·ratio_cpu，HT-3 兼容口径）→ 证据不足：两支公式只可证一支，待含 mean 的包复判。

formal 恒 PENDING_RULING（spec §2.3：正式裁定待任务方出具），随每个接口精度 item 的
evidence 携带；族级结论由 family_conclusion 给出且在本片恒为「不得通过」类。
残差超阈致数值FAIL 的接口精度项，evidence 另附任务书 §3.2.2 注的申诉指引
（APPEAL_NOTE，纯静态句，不参与判定与状态流转）。

S3 批量增量（S3 spec §4/§6，D3 卡；HT-16 起 T8 暂定聚合标记摘除，批量数值结论升正式）：

- 接口精度项识别批量 verdict schema（顶层含 batch，无 residual 单段）：case 级
  error（fail_count 为 null）→ 证据不足；numeric PASS → 数值PASS；FAIL 中存在真
  数值失败（fail_count > error_count）→ 数值FAIL（evidence 携带 batch/fail_count/
  first_fail_index/worst_index/worst 与整批 diagnostics）；FAIL 全由逐矩阵 error
  构成 → 证据不足（按 judge 文档「error 非空属不可裁」口径）。
- batched 期望项按算子分型：批量算子数值判定可展示且升正式（接口精度项逐 case
  携带逐矩阵一段式残差结论）；覆盖缺口如实声明（六例二维代表子集、无 std 蓝本，
  覆盖类别保留证据不足）；单矩阵算子的 batched 接口由对应 *Batched 后补包独立
  承载，本包如实记证据不足。
残差超阈致数值FAIL 的接口精度项，evidence 另附任务书 §3.2.2 注的申诉指引
（APPEAL_NOTE，纯静态句，不参与判定与状态流转）。
"""

from collections import OrderedDict

EXPECTATIONS_VER = "s2a1-D6"  # s2-A1 一段式：接口精度映射改 residual 单层；承 s3-D4

# S3 spec §1：batched 覆盖缺口的统一声明文本（batched 期望项与声明边界共用）。
BATCHED_COVERAGE_GAP = (
    "batched 六例为 (n, batch) 二维代表子集、无 std 蓝本；任务书要求的随机 SPD/HPD、"
    "非正定、INF/NAN、确定性等覆盖类别保留证据不足，不因六例而缩减（S3 spec §1）")

# ---- 七类期望项（spec §2.3′；字面即 report.expectation[].kind 的取值）----
KIND_ACCURACY = "接口精度"
KIND_PERF = "P项性能"
KIND_BUFFER = "bufferSize"
KIND_MEMORY = "内存证据"
KIND_BATCHED = "batched"
KIND_DETERMINISM = "确定性"
KIND_INFO = "info 契约"
KINDS = (KIND_ACCURACY, KIND_PERF, KIND_BUFFER, KIND_MEMORY,
         KIND_BATCHED, KIND_DETERMINISM, KIND_INFO)

# ---- 状态枚举（spec §2.5）----
ST_PASS = "数值PASS"
ST_FAIL = "数值FAIL"
ST_NO_EVIDENCE = "证据不足"
ST_PENDING = "待裁"
STATUSES = (ST_PASS, ST_FAIL, ST_NO_EVIDENCE, ST_PENDING)

FORMAL_PENDING = "PENDING_RULING"

# 申诉通道指引（任务书 §3.2.2 各节注文）：纯静态句，只随 evidence 展示，
# 不参与任何判定或状态流转。
APPEAL_NOTE = ("任务书 §3.2.2 注：残差超阈值判定不通过时，可举证算子实现无 bug "
               "并分析误差产生的原因；申诉时随本报告提交该分析")


def make_item(kind, item, status, evidence):
    """组一个期望项（spec §2.5 report.expectation 元素）；kind/status 越界即抛。"""
    if kind not in KINDS:
        raise ValueError(f"未知期望类 {kind!r}，只允许 {KINDS}")
    if status not in STATUSES:
        raise ValueError(f"未知状态 {status!r}，只允许 {STATUSES}")
    return {"item": item, "kind": kind, "status": status, "evidence": evidence}


# ---------------------------------------------------------------------------
# case 全集（spec §2.1：未生成的实物 case 保留在期望集）
# ---------------------------------------------------------------------------

def case_universe(operator, index_cases, baseline_rows):
    """该算子的期望 case 全集：perf_baseline（覆盖 canonical 全量）∪ index（随包子集）。

    包内没有 canonical_cases.json（spec §2.2 包契约），但 perf_baseline.json 逐
    canonical case 落行（含 matched=false 行），据此还原全集；index.json 只含随包
    交付的子集。返回 OrderedDict：case_id -> index 条目（未随包交付则为 None），
    顺序 = baseline 序（canonical 保序），index 独有的排尾。
    """
    by_id = {}
    for entry in index_cases:
        if entry.get("op") == operator:
            by_id[entry["case_id"]] = entry
    universe = OrderedDict()
    for row in baseline_rows:
        cid = row["case_id"]
        universe[cid] = by_id.get(cid)
    for cid, entry in by_id.items():
        if cid not in universe:
            universe[cid] = entry
    return universe


# ---------------------------------------------------------------------------
# 接口精度项
# ---------------------------------------------------------------------------

def _accuracy_name(case_id):
    return f"接口精度/{case_id}"


def accuracy_item_insufficient(case_id, reason, detail):
    """证据不足的接口精度项：未生成/证据缺失/指纹错配/不可裁，evidence 指认原因。"""
    return make_item(KIND_ACCURACY, _accuracy_name(case_id), ST_NO_EVIDENCE,
                     {"reason": reason, "detail": detail, "formal": FORMAL_PENDING})


def _accuracy_item_from_batched(case_id, verdict):
    """批量 verdict → 接口精度项（映射规则见模块文档 S3 段）。

    数值可展示、不可裁不冒充：case 级 error 与「失败全由逐矩阵 error 构成」都记
    证据不足；只有存在真数值失败（fail_count > error_count，error 矩阵必 FAIL 故
    差值即纯数值失败数）或全过时才出数值状态。evidence 携带批量三元组与整批
    diagnostics（HT-16：T8 标记摘除，数值结论升正式，flags 恒空）。
    """
    error = verdict.get("error")
    if verdict.get("fail_count") is None:
        return accuracy_item_insufficient(
            case_id, "不可裁", f"批量 judge 未能开审（case 级校验/被测问题）: {error}")
    diagnostics = verdict.get("diagnostics") or {}
    error_count = diagnostics.get("error_count") or 0
    numeric = verdict.get("numeric")
    if numeric != "PASS" and verdict.get("fail_count", 0) <= error_count:
        return accuracy_item_insufficient(
            case_id, "逐矩阵证据问题",
            f"失败矩阵全部为不可裁 error（{error_count} 个），无真数值失败: {error}")
    status = ST_PASS if numeric == "PASS" else ST_FAIL
    evidence = {
        "judged_by": "batched",          # 批量通路：逐矩阵三层，case=全部矩阵通过
        "batch": verdict.get("batch"),
        "fail_count": verdict.get("fail_count"),
        "first_fail_index": verdict.get("first_fail_index"),
        "worst_index": verdict.get("worst_index"),
        "worst": verdict.get("worst"),
        "diagnostics": diagnostics,
        "flags": list(verdict.get("flags") or []),
        "formal": verdict.get("formal", FORMAL_PENDING),
        "error": error,
    }
    return make_item(KIND_ACCURACY, _accuracy_name(case_id), status, evidence)


def accuracy_item_from_verdict(case_id, verdict):
    """把 criteria.judge 的 verdict 映射为接口精度项（映射规则见模块文档 1–4；
    批量 schema（顶层含 batch，S3）分流到 _accuracy_item_from_batched）。"""
    if "batch" in verdict:
        return _accuracy_item_from_batched(case_id, verdict)
    residual = verdict.get("residual") or {}
    error = verdict.get("error")
    if verdict.get("numeric") != "PASS" and not residual.get("ran"):
        return accuracy_item_insufficient(
            case_id, "不可裁",
            f"残差未运行（被测状态/输入问题或残差基线不可用）: {error}")
    if (verdict.get("numeric") != "PASS" and residual.get("ran")
            and residual.get("pass") is None):
        # HT-3 缺 mean 兼容口径：超单支线不判 FAIL，证据不足待含 mean 的包复判。
        return accuracy_item_insufficient(
            case_id, "残差证据不足",
            f"缺 ratio_cpu_mean 且残差超单支 5·ratio_cpu: {error}")
    status = ST_PASS if verdict.get("numeric") == "PASS" else ST_FAIL
    evidence = {
        "judged_by": "residual",         # s2-A1 一段式：残差即唯一判定层
        "residual": residual,
        "flags": list(verdict.get("flags") or []),
        "formal": verdict.get("formal", FORMAL_PENDING),
        "error": error,
    }
    # 只在「残差算出 ratio 且超阈」的数值FAIL 上附申诉指引——任务书注文只覆盖
    # 残差超阈情形；残差不可计算的 FAIL（ratio 为 None）不属申诉通道。
    if status == ST_FAIL and residual.get("ratio") is not None:
        evidence["appeal"] = APPEAL_NOTE
    return make_item(KIND_ACCURACY, _accuracy_name(case_id), status, evidence)


def accuracy_item_ungenerated(case_id):
    """未生成 case（canonical 在册、S1 子集外）→ 证据不足（spec §2.1）。"""
    return accuracy_item_insufficient(
        case_id, "未生成",
        "canonical 在册、未随包交付（S1 子集外），不声称全覆盖（spec §2.1）")


# ---------------------------------------------------------------------------
# info 契约项（HT-8：case_purpose=="info" 的用例，B2 卡口径）
# ---------------------------------------------------------------------------

def info_item_from_verdict(case_id, verdict):
    """info 契约用例的 verdict（_judge_info_inner schema，顶层带 "info" 键）→
    KIND_INFO 项。info 与数值精度各出独立结论（HT-12 口径）：
    verdict.error 非空（status 非 ok / k_expected 缺失 / info 非标量）→ 证据不足；
    否则随 numeric 出 数值PASS｜数值FAIL（evidence 携带 expected/actual）。"""
    error = verdict.get("error")
    info = verdict.get("info")
    if error or info is None:
        return make_item(
            KIND_INFO, f"{KIND_INFO}/{case_id}", ST_NO_EVIDENCE,
            {"reason": "不可裁", "detail": f"judge 未能开审（被测/输入问题）: {error}",
             "formal": FORMAL_PENDING})
    status = ST_PASS if verdict.get("numeric") == "PASS" else ST_FAIL
    return make_item(
        KIND_INFO, f"{KIND_INFO}/{case_id}", status,
        {"expected": info.get("expected"), "actual": info.get("actual"),
         "pass": bool(info.get("pass")),
         "formal": verdict.get("formal", FORMAL_PENDING), "error": None})


def info_item_insufficient(case_id, reason, detail):
    """证据不足的 info 契约项：指纹错配/证据缺失/被测输出缺失（accept_run 装载
    路径，judge 未开审即拦截的情形）；独立结论不混入接口精度（HT-8/HT-12）。"""
    return make_item(KIND_INFO, f"{KIND_INFO}/{case_id}", ST_NO_EVIDENCE,
                     {"reason": reason, "detail": detail, "formal": FORMAL_PENDING})


# ---------------------------------------------------------------------------
# P 项性能与其余五类
# ---------------------------------------------------------------------------

def perf_formal_gate_item(operator, report=None, error=None):
    """Formal performance item, recomputed by criteria.performance from sample evidence."""
    if report is None:
        return make_item(KIND_PERF, f"P项性能/正式门禁/{operator}", ST_NO_EVIDENCE,
                         {"reason": error or "未提供 msprof op 性能采集证据",
                          "performance_verdict": "INSUFFICIENT"})
    status = {"PASS": ST_PASS, "FAIL": ST_FAIL, "INSUFFICIENT": ST_NO_EVIDENCE}[
        report["performance_verdict"]]
    return make_item(KIND_PERF, f"P项性能/正式门禁/{operator}", status, report)


def perf_reference_item(operator, status, evidence):
    """性能参考比值项（方向 被测/基线，spec 第 3 节 D 行口径）；状态由调用方定：
    有被测耗时 → 待裁（参考值并报、无独立判据），无 → 证据不足，指纹错配 → 证据不足。"""
    item = make_item(KIND_PERF, f"P项性能/参考比值/{operator}", status, evidence)
    item["required"] = False  # 可选展示；正式性能门禁单独进入必测统计。
    return item


def is_batched_operator(operator):
    """批量算子判别（S3：*Batched 后缀即批量四算子的命名不变量）。"""
    return str(operator).endswith("Batched")


def batched_status_item(operator):
    """batched 期望项按算子分型（S3 spec §1 覆盖缺口；HT-16 起 T8 摘除）。

    批量算子：数值判定已升正式（接口精度项逐 case 携带逐矩阵三层结论，不再记
    「T8 暂定」）；本项保留以如实声明覆盖缺口——六例二维代表子集、无 std 蓝本，
    任务书要求的覆盖类别证据不足。单矩阵算子：batched 接口由对应 *Batched
    后补包独立承载，本包不含 batched 证据。
    """
    if is_batched_operator(operator):
        return make_item(
            KIND_BATCHED, f"{KIND_BATCHED}/{operator}", ST_NO_EVIDENCE,
            {"reason": "覆盖类别证据不足",
             "detail": "数值判定已升正式（接口精度项逐 case 携带逐矩阵一段式残差"
                       "结论，HT-16）；期望集覆盖类别按缺口声明保留证据不足（S3 spec §1）",
             "coverage_gap": BATCHED_COVERAGE_GAP,
             "formal": FORMAL_PENDING})
    return make_item(
        KIND_BATCHED, f"{KIND_BATCHED}/{operator}", ST_NO_EVIDENCE,
        {"reason": "证据不足",
         "detail": "batched 接口由对应 *Batched 算子的后补包独立承载（S3 批次），"
                   "本算子包不含 batched 证据",
         "formal": FORMAL_PENDING})


def fixed_insufficient_items(operator):
    """本片恒为证据不足的五类项（spec §1/§2.3′/§2.5，模拟被测如实记录）；
    batched 项按算子分型（batched_status_item，S3 spec §1/§6）。"""
    front = (
        (KIND_BUFFER, "bufferSize 查询证据未交付（本片模拟被测，未实现该通路）"),
        (KIND_MEMORY, "内存证据未交付（本片模拟被测，未实现该通路）；"
                      "任务书 2026-10 定稿 §3.4 内存不做要求，此项仅存证不设门"),
    )
    back = (
        (KIND_DETERMINISM,
         "任务书 §3.2 要求合法 SPD 复跑 bit-wise 一致；本片无被测执行，复跑证据由"
         "验收侧 stream_check --rerun ≥5 出具（HT-10，独立结论不入数值判定）"),
        (KIND_INFO,
         "任务书 §3.2 要求非正定给正确正值下标 k；本片模拟被测，未执行该场景"),
    )
    def _fixed(kind, detail):
        return make_item(kind, f"{kind}/{operator}", ST_NO_EVIDENCE,
                         {"reason": "证据不足", "detail": detail,
                          "formal": FORMAL_PENDING})
    return ([_fixed(k, d) for k, d in front]
            + [batched_status_item(operator)]
            + [_fixed(k, d) for k, d in back])


# ---------------------------------------------------------------------------
# 族级结论与声明边界
# ---------------------------------------------------------------------------

def family_conclusion(items):
    """族级数值层结论字符串 + 状态计数。存在数值FAIL/证据不足/待裁任一项即不得通过；
    formal 恒 PENDING_RULING，任何情况下都不构成正式验收结论（spec §2.3/§5）。"""
    counts = {st: 0 for st in STATUSES}
    for it in items:
        if it.get("required") is False:
            continue
        counts[it["status"]] += 1
    if counts[ST_FAIL]:
        verdict = f"族级不得通过：存在 数值FAIL {counts[ST_FAIL]} 项"
    elif counts[ST_NO_EVIDENCE]:
        verdict = f"族级不得通过：存在 证据不足 {counts[ST_NO_EVIDENCE]} 项"
    elif counts[ST_PENDING]:
        verdict = f"族级不得通过：存在 待裁 {counts[ST_PENDING]} 项"
    else:
        verdict = "族级具证项数值全过；formal 恒 PENDING_RULING，不构成正式验收结论"
    detail = "、".join(f"{st} {n}" for st, n in counts.items())
    return f"{verdict}（{detail}）", counts


def declared_boundary(operator, items, warnings, ungenerated):
    """report.声明边界（spec §2.5/§5）：族级结论 + 本片不可声明项，如实列未证；
    批量算子另列覆盖缺口（S3 spec §1；HT-16 起 T8 摘除）。items 已含逐 case info
    契约结论时（HT-8，固定占位项已被 accept_run 摘除），未证项清单同步去掉该类。"""
    conclusion, _ = family_conclusion(items)
    lines = [
        conclusion,
        "本报告只出数值判定与证据状态；formal 恒 PENDING_RULING（正式裁定待任务方出具），"
        "不构成正式验收结论（spec §0/§2.3）",
        f"未生成 case {ungenerated} 项保留在期望集为证据不足，不声称全覆盖（spec §2.1）",
    ]
    unproven = (["bufferSize", "内存证据", "确定性", "info 契约"]
                if is_batched_operator(operator)
                else ["bufferSize", "内存证据", "batched", "确定性", "info 契约"])
    if any(it["kind"] == KIND_INFO for it in items):
        unproven.remove("info 契约")     # HT-8：info 契约已随包出独立结论
    lines.append(f"未证项（spec §5）：{'、'.join(unproven)}——本片模拟被测，均证据不足")
    if is_batched_operator(operator):
        lines.append(f"覆盖缺口：{BATCHED_COVERAGE_GAP}")
    lines.append("性能项按 msprof op 平均 kernel 耗时 ≤ 冻结 GPU avg_ms/0.35 判定；"
                 "见正式门禁项逐 case 证据与覆盖统计，性能通过不等于总体验收通过")
    if warnings:
        lines.append(f"告警（仅告警不阻断，spec §2.2 指纹分级）：{'；'.join(warnings)}")
    return lines
