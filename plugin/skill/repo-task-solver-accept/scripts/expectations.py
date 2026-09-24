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
  （T_A100 占位未裁，T1）→ 恒待裁；无被测耗时则参考项证据不足（spec §2.3′）。
- bufferSize / 内存证据 / batched：本片未实现、未交付 → 证据不足（spec §1/§2.5）。
- 确定性 / info 契约：任务书 §3.2 新增两类；本片模拟被测下均记证据不足（spec §2.3′）。

judge verdict → 接口精度状态的映射（spec §2.3 数值判定流转 + judge 的 error 语义）：

1. verdict.layer1 为 None（被测状态非 ok / info≠0 / 输入缺失等，judge 未能开审）
   → 证据不足：没有可裁的被测输出，不是精度结论。
2. numeric == PASS → 数值PASS（layer1 双解释一致通过，或 fallback 终审通过）。
3. numeric == FAIL 且 fallback.ran → 数值FAIL：终审层已对被测数据裁决（fallback 算出
   超阈，或被测输出退化到残差不可计算——layer1 已给出完整失败统计，指认层随 evidence
   携带）。
4. numeric == FAIL 且 fallback 未运行（error 非空，如 ratio_cpu 缺失致兜底不可用）
   → 证据不足：layer1 未双过而终审层缺证据，按 judge 文档「error 非空的 FAIL 属
   不可裁/证据问题」不得当精度 FAIL 上报。

formal 恒 PENDING_RULING（spec §2.3：T1/T3/T4 未裁），随每个接口精度 item 的
evidence 携带；族级结论由 family_conclusion 给出且在本片恒为「不得通过」类。
残差超阈致数值FAIL 的接口精度项，evidence 另附任务书 §3.2.2 注的申诉指引
（APPEAL_NOTE，纯静态句，不参与判定与状态流转）。
"""

from collections import OrderedDict

EXPECTATIONS_VER = "s1-E1"

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


def accuracy_item_from_verdict(case_id, verdict):
    """把 criteria.judge 的 verdict 映射为接口精度项（映射规则见模块文档 1–4）。"""
    layer1 = verdict.get("layer1")
    fallback = verdict.get("fallback") or {}
    error = verdict.get("error")
    if layer1 is None:
        return accuracy_item_insufficient(
            case_id, "不可裁",
            f"judge 未能开审（被测状态/输入问题）: {error}")
    if verdict.get("numeric") != "PASS" and not fallback.get("ran"):
        return accuracy_item_insufficient(
            case_id, "兜底证据不可用",
            f"layer1 未双过且 fallback 未运行: {error}")
    judged_by = "fallback" if fallback.get("ran") else "layer1"
    status = ST_PASS if verdict.get("numeric") == "PASS" else ST_FAIL
    evidence = {
        "judged_by": judged_by,          # 指认终审层（spec §2.3 流转）
        "layer1": layer1,
        "fallback": fallback,
        "flags": list(verdict.get("flags") or []),
        "formal": verdict.get("formal", FORMAL_PENDING),
        "error": error,
    }
    # 只在「fallback 算出 ratio 且超阈」的数值FAIL 上附申诉指引——任务书注文只覆盖
    # 残差超阈情形；残差不可计算的 FAIL（ratio 为 None）不属申诉通道。
    if status == ST_FAIL and fallback.get("ratio") is not None:
        evidence["appeal"] = APPEAL_NOTE
    return make_item(KIND_ACCURACY, _accuracy_name(case_id), status, evidence)


def accuracy_item_ungenerated(case_id):
    """未生成 case（canonical 在册、S1 子集外）→ 证据不足（spec §2.1）。"""
    return accuracy_item_insufficient(
        case_id, "未生成",
        "canonical 在册、未随包交付（S1 子集外），不声称全覆盖（spec §2.1）")


# ---------------------------------------------------------------------------
# P 项性能与其余五类
# ---------------------------------------------------------------------------

def perf_formal_gate_item(operator):
    """性能正式门禁项：走任务书 §3.3 机制，T_A100 开发者实测占位未裁 → 恒待裁（T1）。"""
    return make_item(
        KIND_PERF, f"P项性能/正式门禁/{operator}", ST_PENDING,
        {"basis": "任务书 §3.3：T_NPU ≤ T_A100/0.8，中位数口径，T_A100 开发者实测占位",
         "ruling": "T1 未裁：正式门走任务书机制，bench_result 仅自测参考（spec §2.3′）"})


def perf_reference_item(operator, status, evidence):
    """性能参考比值项（方向 被测/基线，spec 第 3 节 D 行口径）；状态由调用方定：
    有被测耗时 → 待裁（参考值并报、无独立判据），无 → 证据不足，指纹错配 → 证据不足。"""
    return make_item(KIND_PERF, f"P项性能/参考比值/{operator}", status, evidence)


def fixed_insufficient_items(operator):
    """本片恒为证据不足的五类项（spec §1/§2.3′/§2.5，模拟被测如实记录）。"""
    reasons = (
        (KIND_BUFFER, "bufferSize 查询证据未交付（本片模拟被测，未实现该通路）"),
        (KIND_MEMORY, "内存证据未交付（本片模拟被测，未实现该通路）"),
        (KIND_BATCHED,
         "batched 判据本片不做（spec §1），无证据；判定口径已由任务书 §3.2.2 官方定案"
         "（逐矩阵按同式判定，任一矩阵超阈该用例不过；ratio_cpu_mean 取 case 内均值），"
         "不再待标准确认"),
        (KIND_DETERMINISM,
         "任务书 §3.2 要求合法 SPD 复跑 bit-wise 一致；本片模拟被测，无独立复跑证据"),
        (KIND_INFO,
         "任务书 §3.2 要求非正定给正确正值下标 k；本片模拟被测，未执行该场景"),
    )
    return [make_item(kind, f"{kind}/{operator}", ST_NO_EVIDENCE,
                      {"reason": "证据不足", "detail": detail, "formal": FORMAL_PENDING})
            for kind, detail in reasons]


# ---------------------------------------------------------------------------
# 族级结论与声明边界
# ---------------------------------------------------------------------------

def family_conclusion(items):
    """族级数值层结论字符串 + 状态计数。存在数值FAIL/证据不足/待裁任一项即不得通过；
    formal 恒 PENDING_RULING，任何情况下都不构成正式验收结论（spec §2.3/§5）。"""
    counts = {st: 0 for st in STATUSES}
    for it in items:
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
    """report.声明边界（spec §2.5/§5）：族级结论 + 本片不可声明项，如实列未证。"""
    conclusion, _ = family_conclusion(items)
    lines = [
        conclusion,
        "本报告只出数值判定与证据状态；formal 恒 PENDING_RULING（T1/T3/T4 未裁），"
        "不构成正式验收结论（spec §0/§2.3）",
        f"未生成 case {ungenerated} 项保留在期望集为证据不足，不声称全覆盖（spec §2.1）",
        "未证项（spec §5）：bufferSize、内存证据、batched、确定性、info 契约"
        "——本片模拟被测，均证据不足",
        "性能正式门禁走任务书 §3.3 机制，bench_result 仅自测参考（T1 待裁）",
    ]
    if warnings:
        lines.append(f"告警（仅告警不阻断，spec §2.2 指纹分级）：{'；'.join(warnings)}")
    return lines
