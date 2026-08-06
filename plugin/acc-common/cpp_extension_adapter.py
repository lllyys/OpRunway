#!/usr/bin/env python3
"""官方 torch_npu C++ Extension 的确定性准备、外部执行与收据验证。

本模块不内置 SSH、容器名或机器路径。真机编排器须通过
``OPRUNWAY_CPP_EXTENSION_DRIVER_JSON`` 提供 JSON argv；driver 接收
``--bundle`` 与 ``--work``，构建并加载独立 Extension、执行全量 caseset，
然后把输出和 ``cpp_extension_receipt.json`` 回传到 work。这里仅验证并组装
evidence，不判定 pass/fail。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import content_address
import cpp_extension_codegen
import perf_mode
import vendor_build_receipt


class CppExtensionAdapterError(RuntimeError):
    pass


_RECEIPT = "cpp_extension_receipt.json"
_PLAN = "cpp_extension_invocation_plan.json"
_CASESET = "cpp_extension_caseset.json"
_PERF_TEMPLATE = "cpp_extension_perf_template.json"
_PERF_PLAN = "cpp_extension_perf_plan.json"
_PERF_COLLECT = "cpp_extension_perf_collect.json"
_BUNDLE = "cpp_extension"
_OUT = "cpp_extension_out"


def _canonical_sha(value):
    return hashlib.sha256(content_address.canonical_json_bytes(value)).hexdigest()


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strict_json(path):
    with open(path, encoding="utf-8") as src:
        value = json.load(
            src,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"非法 JSON 常量 {token}")))
    content_address.canonical_json_bytes(value)
    return value


def _safe(root, rel):
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        raise CppExtensionAdapterError(f"相对路径非法: {rel!r}")
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, rel))
    if path != root and not path.startswith(root + os.sep):
        raise CppExtensionAdapterError(f"路径逃出根目录: {rel!r}")
    return path


def _variants_by_symbol(manifest):
    result = {}
    for row in manifest.get("variants") or []:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise CppExtensionAdapterError(
                f"extension manifest variant symbol 缺失: {symbol!r}")
        result.setdefault(symbol, []).append(row)
    if not result:
        raise CppExtensionAdapterError("extension manifest 无 variants")
    return result


#: caseset 里「无 golden」的一等状态标记（定义处 `gen_cases.GOLDEN_UNAVAILABLE`）。
GOLDEN_UNAVAILABLE = "golden_unavailable"


def build_invocation_plan(caseset, manifest):
    """把 caseset.aclnn_call 绑定到生成 Extension 的 entrypoint；不重推变体。

    `golden_unavailable` 的 case **不进执行计划**，并落进 `excluded` 台账。理由是形状：
    没有 golden 就没有 `expected.out_shape`，driver 也就无从分配 `dst`——真按 0-d 分配下去，
    真机报回来的是「src and dst must have the same shape」，一条纯 harness 的错会被记成
    DUT 的拒绝理由（AGENTS.md 5.8 的反面教材）。这些 case 的身份、输入字节、调用契约仍在
    caseset 里完整保留，evidence 侧记 `golden_unavailable`、验收门按 BLOCKED 记账，
    **不因为没执行就变成通过**。
    """
    variants = _variants_by_symbol(manifest)
    rows, seen, excluded = [], set(), []
    cases = caseset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CppExtensionAdapterError("caseset.cases 须为非空列表")
    for case in cases:
        cid = case.get("id")
        if not isinstance(cid, str) or not cid or cid in seen:
            raise CppExtensionAdapterError(f"case id 缺失或重复: {cid!r}")
        seen.add(cid)
        if (case.get("expected") or {}).get("golden_status") == GOLDEN_UNAVAILABLE:
            excluded.append({"case_id": cid, "reason": GOLDEN_UNAVAILABLE})
            continue
        call = case.get("aclnn_call")
        if not isinstance(call, dict):
            raise CppExtensionAdapterError(
                f"{cid}: cpp_extension case 缺 aclnn_call")
        symbol, slots = call.get("symbol"), call.get("slots")
        candidates = variants.get(symbol)
        if candidates is None:
            raise CppExtensionAdapterError(
                f"{cid}: aclnn_call.symbol={symbol!r} 未绑定生成 Extension variant")
        if not isinstance(slots, list) or not slots:
            raise CppExtensionAdapterError(f"{cid}: aclnn_call.slots 须为非空列表")
        active_attrs = []
        active_outputs = []
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict):
                raise CppExtensionAdapterError(f"{cid}: slots[{index}] 非 object")
            role, name = slot.get("role"), slot.get("name")
            if role not in ("in", "attr", "out", "out_null"):
                raise CppExtensionAdapterError(
                    f"{cid}: slots[{index}].role={role!r} 非受控词")
            if not isinstance(name, str) or not name:
                raise CppExtensionAdapterError(f"{cid}: slots[{index}].name 缺失")
            if role == "attr":
                active_attrs.append(name)
            if role == "out":
                active_outputs.append(name)
        matches = [
            row for row in candidates
            if row.get("active_attrs") == active_attrs
            and row.get("active_outputs") == active_outputs
        ]
        if len(matches) != 1:
            raise CppExtensionAdapterError(
                f"{cid}: symbol={symbol!r}, active attrs={active_attrs!r}, "
                f"active outputs={active_outputs!r} 匹配 Extension variant 数={len(matches)}")
        variant = matches[0]
        rows.append({
            "case_id": cid,
            "symbol": symbol,
            "entrypoint": variant["entrypoint"],
            "slots": slots,
        })
    if not rows:
        raise CppExtensionAdapterError(
            "invocation plan 无任何可执行 case（全部被排除）——没有可跑的 DUT 调用，拒")
    return {
        "schema": "oprunway.cpp_extension_invocation_plan",
        "schema_version": 1,
        "caseset_sha256": _canonical_sha(caseset),
        "manifest_sha256": _canonical_sha(manifest),
        "namespace": manifest["namespace"],
        "cases": rows,
        # 分母台账：谁没进执行计划、为什么。空表 = 一条都没排除。
        "excluded": excluded,
    }


_PREFLIGHT = "aclnn_preflight.json"
#: CP-C0 预检工件的内容寻址 domain。**定义处是 `preflight_aclnn._PREFLIGHT_DOMAIN`**；
#: 这里逐字重述而不 import，是因为 `preflight_aclnn` 顶层 import `gen_cases`（进而 numpy），
#: 而本模块要保持在纯 stdlib 的本地准备层。改域名时两处必须同改。
_PREFLIGHT_DOMAIN = "oprunway/aclnn-preflight/v1"


def _load_preflight(work):
    """work 里若躺着本轮 CP-C0 预检工件就读出来（供 codegen 定 stage2 形态）。

    做成「在场即用、缺席即退回 spec 自报/历史缺省并挂账」：`prepare()` 的三个调用方
    （run_workflow / precision_retest_runner / 单测）都不归本 lane 改，不能改签名硬要求。
    但**在场时一条都不放松**：必须是 `content_address` 的可校验 envelope（domain + digest 都核），
    坏文件绝不当成「没有预检」静默跳过——那正好把 fail-closed 变成 fail-open。
    形态本身的 fail-closed 在 codegen（状态、spec 摘要绑定、不可派发形态）。
    """
    path = os.path.join(work, _PREFLIGHT)
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    try:
        return content_address.read_artifact(work, _PREFLIGHT, _PREFLIGHT_DOMAIN)
    except content_address.ContentAddressError as ex:
        raise CppExtensionAdapterError(
            f"{_PREFLIGHT} 在场却不是可校验的 CP-C0 预检工件：{ex}") from ex


def prepare(spec, caseset, work, preflight=None):
    """生成 Extension bundle 与逐 case 调用计划；纯本地确定性准备，不 build。

    `preflight` 未显式传入时，回落读 `<work>/aclnn_preflight.json`（若在）。
    """
    # 同 `cpp_extension_codegen._contract`：经全仓唯一缺省真源判形态（P5）。只有**键缺席**吃缺省，
    # 显式声明成别的形态照旧当场拒——这里放的是「上游已按 cpp_extension 规划好」的那一种 spec。
    import repo_adapter                      # 惰性：repo_adapter 顶层 import 本模块的兄弟模块，避免环
    runner_form = repo_adapter.spec_runner_form(spec)
    if runner_form != "cpp_extension":
        raise CppExtensionAdapterError(
            f"prepare 仅接受 runner_form=cpp_extension，得 {runner_form!r}")
    work = os.path.abspath(work)
    if preflight is None:
        preflight = _load_preflight(work)
    for stale in (_PERF_PLAN, _PERF_COLLECT):
        path = os.path.join(work, stale)
        if os.path.lexists(path):
            if os.path.islink(path) or not os.path.isfile(path):
                raise CppExtensionAdapterError(
                    f"拒绝清理非普通 cpp_extension 性能暂存物: {path}")
            os.unlink(path)
    bundle = os.path.join(work, _BUNDLE)
    try:
        manifest = cpp_extension_codegen.generate(spec, bundle, preflight)
    except cpp_extension_codegen.CppExtensionCodegenError as ex:
        raise CppExtensionAdapterError(f"Extension codegen 失败：{ex}") from ex
    plan = build_invocation_plan(caseset, manifest)
    with open(os.path.join(work, _PLAN), "w", encoding="utf-8") as out:
        json.dump(plan, out, ensure_ascii=False, indent=2)
        out.write("\n")
    with open(os.path.join(work, _CASESET), "w", encoding="utf-8") as out:
        json.dump(caseset, out, ensure_ascii=False, indent=2)
        out.write("\n")
    perf = spec.get("perf") or {}
    # 性能口径由 `perf_mode` 一处解释（AGENTS.md §5.10）：字段缺席 = 历史严档 ratio_gated；
    # measure_only 须带任务书授权，且 spec 里不得再出现任何对照物/阈值字段（那由 resolve 校）。
    try:
        mode = perf_mode.resolve_spec_mode(spec)
    except perf_mode.PerfModeError as ex:
        raise CppExtensionAdapterError(f"spec.perf 口径非法：{ex}") from ex
    perf_template = {
        "schema": "oprunway.cpp_extension_perf_template",
        "schema_version": 1,
        "op": caseset.get("op"),
        "mode": mode,
        "warmup": perf.get("warmup", 5),
        "repeat": perf.get("repeat", 20),
        "side_timeout_s": perf.get("side_timeout_s", 120),
    }
    if not perf_mode.is_measure_only(mode):
        # 只测不比的档**不写任何对照物槽**：写成 `null` 也会让读计划的人以为「采过基线、没采到」，
        # 而且 `perf_msprof.collect` 对 measure_only 明确要求 baseline 缺席（fail-closed）。
        perf_template.update({
            "baseline": perf.get("baseline"),
            "torch_baseline": perf.get("torch_baseline"),
            "aclnn_baseline": perf.get("aclnn_baseline"),
        })
    else:
        perf_template["measure_only_authorization"] = (
            perf_mode.measure_only_authorization(perf))
    with open(os.path.join(work, _PERF_TEMPLATE), "w", encoding="utf-8") as out:
        json.dump(perf_template, out, ensure_ascii=False, indent=2)
        out.write("\n")
    return manifest, plan


#: 严档（ratio_gated）下「整份精度未通过 → 一条性能都不采」的挂账理由。
SKIPPED_PRECISION_OVERALL_GATE = "skipped_precision_overall_gate"
#: measure_only 下「这条 case 的精度**根本判不了**」（没执行成功 / 无 policy+metrics）的挂账理由。
#: 与 `perf_msprof.SKIPPED_ACCURACY_FAILED`（判过、没过）是两件事，不得混成同一个词。
SKIPPED_PRECISION_NOT_EVALUABLE = "skipped_precision_not_evaluable"


def _precision_evaluable_ids(evidence):
    """evidence 里**精度可判**的 case（有 policy + metrics 的完整精度块）。

    ⚠ 这不是第二套裁决：pass/fail 的唯一权威仍是 `perf_msprof.accuracy_pass_ids`
    （它内部调 `validator._judge_by_policy`）。本函数只回答「这条 case 到底有没有可判的精度块」，
    用来把 skipped 的**理由**写准——「算错了」和「压根没跑出来」在报告里必须分得开（AGENTS.md 5.8）。
    结构判据与 `accuracy_pass_ids` 保持一致：多输出看 `precision.outputs`，
    单输出旧证据回落到 `precision` 顶层。
    """
    ids = set()
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        cid = item.get("case_id")
        prec = item.get("precision")
        if not cid or not isinstance(prec, dict):
            continue
        outputs = prec.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            outputs = [prec] if prec.get("policy") is not None else []
        if not outputs:
            continue
        if all(isinstance(out, dict) and out.get("policy") is not None
               and out.get("metrics") is not None for out in outputs):
            ids.add(cid)
    return ids


def _write_perf_plan(caseset, work, evidence, receipt):
    """精度先筛后生成第二阶段性能计划；device 必须由真机调用方显式给出。

    两档口径**分开处理**（AGENTS.md §5.10）：

    · ``ratio_gated``（缺省、历史行为，**逐字不变**）：任何应裁精度 case 未通过 →
      一条性能都不采。理由是比值裁决要拿这批数去和标杆比，算错的快不算快。
    · ``measure_only``：口径本身不产任何达标结论，性能维只是「这颗 kernel 实测多少微秒」。
      此时若沿用总门，精度一 fail 就等于 msprof 零数据 —— 而「只输出绝对耗时」恰恰是本档
      唯一的产出。故改为从**已成功执行且精度可判为 pass** 的 case 里选性能子集继续采，
      **分母一条不丢**：每条落选的性能 case 都进 `skipped` 并写明真实原因。
      这不放松任何结论：性能计划里显式带着 `precision_gate` 台账，且本档不产比值、
      不贡献 pass/fail，最终裁决仍由 validator 按精度出（这里出的一定还是 FAIL）。
    """
    from aclnn_runtime import perf_msprof as PM

    template = _strict_json(os.path.join(work, _PERF_TEMPLATE))
    try:
        # 计划口径只认 prepare() 落盘的模板（它由 spec 经 perf_mode 校过），不看运行期环境。
        mode = perf_mode.normalize(template.get("mode", perf_mode.DEFAULT_MODE))
    except perf_mode.PerfModeError as ex:
        raise CppExtensionAdapterError(f"cpp_extension 性能模板口径非法：{ex}") from ex
    measure_only = perf_mode.is_measure_only(mode)
    passed = PM.accuracy_pass_ids(evidence)
    precision_ids = {
        case["id"] for case in caseset.get("cases") or []
        if "精度" in (case.get("dims") or [])
    }
    if not precision_ids:
        precision_ids = {case["id"] for case in caseset.get("cases") or []}
    not_passed = sorted(precision_ids - passed)
    if not_passed and not measure_only:
        # 与 run_workflow 的 Task2 总门同口径：任何应裁精度 case 未通过，都不得提前采性能。
        # 性能 case 虽是 precision-pass 子集，但这个“子集”只在整份精度验收通过后做选择。
        return None, [{
            "case_id": cid,
            "reason": SKIPPED_PRECISION_OVERALL_GATE,
        } for cid in not_passed]
    selected, skipped = PM.select_perf_cases(caseset, passed)
    if measure_only:
        # `select_perf_cases` 对所有未 pass 的 case 一律记 `skipped_accuracy_failed`；
        # 其中「精度块根本不存在」的那些其实是**没跑出来/判不了**，理由要改写准。
        evaluable = _precision_evaluable_ids(evidence)
        for item in skipped:
            if (item.get("reason") == PM.SKIPPED_ACCURACY_FAILED
                    and item.get("case_id") not in evaluable):
                item["reason"] = SKIPPED_PRECISION_NOT_EVALUABLE
    if not selected:
        return None, skipped
    raw_device = os.environ.get("OPRUNWAY_CPP_EXTENSION_DEVICE")
    if raw_device is None:
        raise CppExtensionAdapterError(
            "cpp_extension 性能采集缺 OPRUNWAY_CPP_EXTENSION_DEVICE；多卡环境不猜 device")
    try:
        device = int(raw_device)
    except ValueError as ex:
        raise CppExtensionAdapterError(
            "OPRUNWAY_CPP_EXTENSION_DEVICE 须为非负整数") from ex
    if device < 0:
        raise CppExtensionAdapterError(
            "OPRUNWAY_CPP_EXTENSION_DEVICE 须为非负整数")
    plan = {
        **template,
        "schema": "oprunway.cpp_extension_perf_plan",
        "custom_kind": "cpp_extension",
        "caseset_sha256": _canonical_sha(caseset),
        "cpp_extension_receipt_sha256": _canonical_sha(receipt),
        "device": device,
        "cases": selected,
        "skipped": skipped,
        # 精度台账：**分母完整落盘**，让「本轮为什么只采了这些 case」成为机读事实。
        # `gate_passed=False` 时下游必须继续把整体判成精度 FAIL —— 有实测耗时 ≠ 验收通过。
        "precision_gate": {
            "mode": mode,
            "gate_passed": not not_passed,
            "precision_case_total": len(precision_ids),
            "precision_passed_count": len(passed & precision_ids),
            "precision_not_passed": not_passed,
            "note": ("measure_only：只测不比，性能子集仅取精度已 pass 的 case；"
                     "本子集**不表示**精度或整体通过（AGENTS.md 5.8/5.10）")
            if measure_only else "ratio_gated：整份精度通过才进入性能采集",
        },
        "cpp_extension": {
            "artifact": receipt["artifact"],
            "namespace": receipt["load"]["namespace"],
            "invocation_plan": _PLAN,
            "invocation_plan_sha256": receipt["bindings"]["invocation_plan_sha256"],
            "vendor": {
                "library_path": receipt["vendor"]["library_path"],
                "library_sha256": receipt["vendor"]["library_sha256"],
                "symbols_owned": receipt["vendor"]["symbols_owned"],
            },
        },
    }
    path = os.path.join(work, _PERF_PLAN)
    with open(path, "w", encoding="utf-8") as out:
        json.dump(plan, out, ensure_ascii=False, indent=2)
        out.write("\n")
    return plan, skipped


def _validate_perf_collection(plan, document):
    """拒绝 partial/stale/换 ELF 的性能采集结果。"""
    checkpoint = document.get("collection_checkpoint")
    records = document.get("records")
    if (document.get("custom_kind") != "cpp_extension"
            or document.get("baseline_source") != plan.get("baseline")
            or document.get("custom_provenance") != plan.get("cpp_extension")
            or not isinstance(checkpoint, dict)
            or checkpoint.get("complete") is not True
            or checkpoint.get("planned_case_ids") != plan.get("cases")
            or not isinstance(records, list)):
        raise CppExtensionAdapterError(
            "cpp_extension perf_collect 非完整本轮双边采集或 provenance 漂移")
    ids = [row.get("case_id") for row in records if isinstance(row, dict)]
    if ids != plan.get("cases") or len(ids) != len(records):
        raise CppExtensionAdapterError(
            "cpp_extension perf_collect records 与性能计划 case 序列不一致")


def _require_sha(label, value):
    if not isinstance(value, str) or len(value) != 64:
        raise CppExtensionAdapterError(f"{label} 须为 64 位 sha256")
    try:
        int(value, 16)
    except ValueError as ex:
        raise CppExtensionAdapterError(f"{label} 非十六进制 sha256") from ex


def _validate_vendor_build_receipt(vendor):
    """离线复核 vendor 构建收据；逐条判据由 :mod:`vendor_build_receipt` 一处解释。

    改动前这里是三份手抄件之一，且无条件要求 40 位 PR head——本地快照通路因此无解。
    现按 `source.provenance_kind` 分流（`gitcode_pr` 行为逐字不变；`local_snapshot` 改绑
    仓根 + 子目录 scope + 两个 merkle + 显式 `degradations`），映射表见该模块。
    """
    build_receipt = vendor.get("build_receipt")
    # ⚠ 这里只校收据自身（schema/status、来源锚、build argv/cwd/returncode、ELF 绑定，
    #   逐条判据都在 `vendor_build_receipt` 一处解释）；与 `source_facts` 的**来源身份一致性**
    #   前置校验在三级门（`validate_acceptance_state`）里做——adapter 手上没有 source_facts。
    try:
        summary = vendor_build_receipt.validate(
            build_receipt,
            library_path=vendor.get("library_path"),
            library_sha256=vendor.get("library_sha256"))
    except vendor_build_receipt.VendorBuildReceiptError as ex:
        raise CppExtensionAdapterError(f"receipt.vendor.build_receipt: {ex}") from ex
    expected = _canonical_sha(build_receipt)
    _require_sha("receipt.vendor.build_receipt_sha256", expected)
    if vendor.get("build_receipt_sha256") != expected:
        raise CppExtensionAdapterError(
            "receipt.vendor.build_receipt_sha256 漂移")
    # driver 落的源身份摘要是派生视图：**在场就必须与重算结果逐字相同**。
    # 允许缺席只为兼容更早 driver 落的收据——事实本身（head / merkle / degradations）
    # 已在上面直接从 build_receipt 校过，缺这份视图不会少判任何一条。
    recorded = vendor.get("source_provenance")
    if recorded is not None and recorded != summary:
        raise CppExtensionAdapterError(
            "receipt.vendor.source_provenance 与 build_receipt 重算的源身份不一致")
    return summary


def validate_receipt(work, caseset):
    """验证外部 driver 回传的 build/load receipt 与当前输入、源码、ELF 精确绑定。"""
    work = os.path.abspath(work)
    bundle = os.path.join(work, _BUNDLE)
    manifest = _strict_json(os.path.join(bundle, "extension_manifest.json"))
    plan = _strict_json(os.path.join(work, _PLAN))
    receipt = _strict_json(os.path.join(work, _RECEIPT))
    if receipt.get("schema") != "oprunway.cpp_extension_receipt" \
            or receipt.get("schema_version") != 1 \
            or receipt.get("status") != "VERIFIED":
        raise CppExtensionAdapterError("cpp_extension receipt schema/status 非 VERIFIED v1")

    expected = {
        "caseset_sha256": _canonical_sha(caseset),
        "manifest_sha256": _canonical_sha(manifest),
        "invocation_plan_sha256": _canonical_sha(plan),
        "spec_sha256": manifest.get("spec_sha256"),
    }
    bindings = receipt.get("bindings")
    if not isinstance(bindings, dict):
        raise CppExtensionAdapterError("receipt.bindings 缺失")
    for key, value in expected.items():
        _require_sha(f"expected.{key}", value)
        if bindings.get(key) != value:
            raise CppExtensionAdapterError(
                f"receipt.bindings.{key} 漂移：期望 {value}，得 {bindings.get(key)!r}")

    for key, rec in (manifest.get("files") or {}).items():
        if not isinstance(rec, dict):
            raise CppExtensionAdapterError(f"manifest.files.{key} 非 object")
        path = _safe(bundle, rec.get("path"))
        if not os.path.isfile(path) or _file_sha(path) != rec.get("sha256"):
            raise CppExtensionAdapterError(f"生成源码 {key} 缺失或摘要漂移")

    runtime = receipt.get("runtime")
    required_runtime = ("torch_version", "torch_npu_version", "cann_version", "soc",
                        "ascend_custom_opp_path")
    if not isinstance(runtime, dict) or any(not runtime.get(k) for k in required_runtime):
        raise CppExtensionAdapterError(
            f"receipt.runtime 须完整包含 {required_runtime}")
    build = receipt.get("build")
    if not isinstance(build, dict) or not isinstance(build.get("argv"), list) \
            or not build["argv"] or build.get("returncode") != 0:
        raise CppExtensionAdapterError("receipt.build 须含成功的非空 argv")
    artifact = receipt.get("artifact")
    if not isinstance(artifact, dict):
        raise CppExtensionAdapterError("receipt.artifact 缺失")
    so_path = _safe(work, artifact.get("path"))
    _require_sha("receipt.artifact.sha256", artifact.get("sha256"))
    if not os.path.isfile(so_path) or _file_sha(so_path) != artifact["sha256"]:
        raise CppExtensionAdapterError("Extension ELF 缺失或摘要漂移")

    load = receipt.get("load")
    if not isinstance(load, dict) or load.get("success") is not True \
            or load.get("loader") != "torch.ops.load_library" \
            or load.get("namespace") != manifest.get("namespace"):
        raise CppExtensionAdapterError("Extension load receipt 不完整或 namespace/loader 漂移")
    schemas = load.get("schemas")
    wanted = {v["entrypoint"] for v in manifest["variants"]}
    if not isinstance(schemas, dict) or set(schemas) != wanted \
            or any(not isinstance(v, str) or not v for v in schemas.values()):
        raise CppExtensionAdapterError("Extension runtime schemas 与生成 entrypoints 不一致")
    vendor = receipt.get("vendor")
    if not isinstance(vendor, dict) or not vendor.get("library_path") \
            or not vendor.get("library_sha256") or not vendor.get("symbols_owned"):
        raise CppExtensionAdapterError("receipt.vendor 缺库路径/摘要/符号归属")
    _require_sha("receipt.vendor.library_sha256", vendor["library_sha256"])
    # 符号来源包 ↔ vendor ELF 必须同源：按同一条布局规则从 library_path 重算，与 driver
    # 实际设进环境的那个值逐字对账。对不上 = 收据说不清「本轮的 aclnnXxx 从哪来」。
    try:
        derived_opp = vendor_build_receipt.custom_opp_path(vendor["library_path"])
    except vendor_build_receipt.VendorBuildReceiptError as ex:
        raise CppExtensionAdapterError(f"receipt.vendor.library_path: {ex}") from ex
    if runtime.get("ascend_custom_opp_path") != derived_opp:
        raise CppExtensionAdapterError(
            "receipt.runtime.ascend_custom_opp_path 与 vendor.library_path 反推的自定义算子包"
            f"不一致：收据记 {runtime.get('ascend_custom_opp_path')!r}，重算 {derived_opp!r}")
    _validate_vendor_build_receipt(vendor)
    return receipt


def source_provenance_summary(receipt):
    """从已校过的 receipt 取本轮 DUT 的源身份摘要（含机读降级挂账）；供 envelope/报告直读。"""
    vendor = receipt.get("vendor") if isinstance(receipt, dict) else None
    if not isinstance(vendor, dict):
        raise CppExtensionAdapterError("receipt.vendor 缺失，无法取源身份摘要")
    try:
        return vendor_build_receipt.summarize(vendor.get("build_receipt"))
    except vendor_build_receipt.VendorBuildReceiptError as ex:
        raise CppExtensionAdapterError(f"receipt.vendor.build_receipt: {ex}") from ex


def _driver_argv():
    raw = os.environ.get("OPRUNWAY_CPP_EXTENSION_DRIVER_JSON")
    if not raw:
        raise CppExtensionAdapterError(
            "缺 OPRUNWAY_CPP_EXTENSION_DRIVER_JSON；cpp_extension 不猜 SSH/container 入口")
    try:
        argv = json.loads(raw)
    except json.JSONDecodeError as ex:
        raise CppExtensionAdapterError("CPP Extension driver JSON 非法") from ex
    if not isinstance(argv, list) or not argv \
            or any(not isinstance(x, str) or not x for x in argv):
        raise CppExtensionAdapterError("CPP Extension driver 须为非空 JSON string argv")
    return argv


def run_cpp_extension(caseset, work, defect_cases=None):
    """执行显式外部 driver，验证 receipt 后复用确定性 evidence 组装。"""
    if defect_cases:
        raise CppExtensionAdapterError("cpp_extension 验收通路禁止 defect 注入")
    if os.environ.get("OPRUNWAY_CPP_EXTENSION_REAL") != "1":
        raise CppExtensionAdapterError(
            "真机路径未启用；须显式设 OPRUNWAY_CPP_EXTENSION_REAL=1")
    bundle = os.path.join(os.path.abspath(work), _BUNDLE)
    plan = os.path.join(os.path.abspath(work), _PLAN)
    if not os.path.isfile(os.path.join(bundle, "extension_manifest.json")) \
            or not os.path.isfile(plan):
        raise CppExtensionAdapterError("缺 prepare() 生成的 bundle/invocation plan")
    driver = _driver_argv()
    argv = driver + ["--bundle", bundle, "--work", os.path.abspath(work)]
    result = subprocess.run(argv, check=False)
    if result.returncode != 0:
        raise CppExtensionAdapterError(
            f"CPP Extension 外部 driver 失败 rc={result.returncode}")
    receipt = validate_receipt(work, caseset)
    import repo_adapter as RA
    evidence = RA.build_multi_output_evidence(
        caseset, work, os.path.join(work, _OUT))
    perf_plan, skipped = _write_perf_plan(caseset, work, evidence, receipt)
    perf_collection = None
    if perf_plan is not None:
        perf_result = subprocess.run(
            driver + ["--bundle", bundle, "--work", os.path.abspath(work),
                      "--perf-only"],
            check=False)
        if perf_result.returncode != 0:
            raise CppExtensionAdapterError(
                f"CPP Extension kernel-only 性能 driver 失败 rc={perf_result.returncode}")
        from aclnn_runtime import perf_msprof as PM
        perf_collection = _strict_json(os.path.join(work, _PERF_COLLECT))
        _validate_perf_collection(perf_plan, perf_collection)
        records = perf_collection.get("records") or []
        perf_by_case = PM.build_custom_perf_map(records, skipped=skipped)
        evidence = RA.build_multi_output_evidence(
            caseset, work, os.path.join(work, _OUT), perf_by_case=perf_by_case)
        if not perf_mode.is_measure_only(perf_plan.get("mode", perf_mode.DEFAULT_MODE)):
            baseline = PM.build_baseline_document(
                records, op=caseset.get("op"),
                warmup=perf_plan["warmup"], repeat=perf_plan["repeat"],
                skipped=skipped, source=perf_plan["baseline"])
            baseline_file = {
                "torch_npu": "_torch_npu_baseline.json",
                "aclnn_builtin": "_aclnn_builtin_baseline.json",
            }.get(perf_plan["baseline"])
            if baseline_file is None:
                raise CppExtensionAdapterError(
                    f"cpp_extension 不支持性能 baseline={perf_plan['baseline']!r}")
            with open(os.path.join(work, baseline_file),
                      "w", encoding="utf-8") as out:
                json.dump(baseline, out, ensure_ascii=False, indent=2)
                out.write("\n")
    digest = _canonical_sha(receipt)
    for row in evidence:
        row["cpp_extension_receipt_sha256"] = digest
    envelope = {
        "op": caseset["op"],
        "repo_mode": "cpp_extension",
        "runner_form": "cpp_extension",
        "runner_source": "generated_official_cpp_extension",
        "runner_path": receipt["artifact"]["path"],
        "evidence_grade": "acceptance_candidate",
        # 源身份摘要提到 envelope 第一层：`local_snapshot` 档的 `pr_head_unbound`
        # 必须在报告里一眼可见，而不是埋在 receipt.vendor.build_receipt 里（5.8）。
        "source_provenance": source_provenance_summary(receipt),
        "cpp_extension_receipt": receipt,
        "evidence": evidence,
    }
    if perf_collection is not None:
        envelope["perf_collection"] = perf_collection
        # 性能采集的口径与分母台账原样带走：measure_only 下性能子集可能小于全部性能 case，
        # 「为什么少」必须在证据里查得到，且不得被读成「精度通过」。
        envelope["perf_selection"] = {
            "mode": perf_plan.get("mode", perf_mode.DEFAULT_MODE),
            "precision_gate": perf_plan.get("precision_gate"),
            "selected": list(perf_plan.get("cases") or []),
            "skipped": list(skipped or []),
        }
    return envelope


def run_cpp_extension_precision_only(caseset, work):
    """执行 cpp_extension Task-2-only；明确不生成/执行任何性能计划。

    CP-F 必须重新执行 DUT，但不得因精度通过而隐式进入原 adapter 的第二阶段性能采集。
    build/load/vendor/调用收据仍完全复用正式 driver 与 ``validate_receipt``。
    """
    if os.environ.get("OPRUNWAY_CPP_EXTENSION_REAL") != "1":
        raise CppExtensionAdapterError(
            "真机路径未启用；须显式设 OPRUNWAY_CPP_EXTENSION_REAL=1")
    root = os.path.abspath(work)
    bundle = os.path.join(root, _BUNDLE)
    plan = os.path.join(root, _PLAN)
    if not os.path.isfile(os.path.join(bundle, "extension_manifest.json")) \
            or not os.path.isfile(plan):
        raise CppExtensionAdapterError("缺 prepare() 生成的 bundle/invocation plan")
    for forbidden in (_PERF_PLAN, _PERF_COLLECT):
        if os.path.lexists(os.path.join(root, forbidden)):
            raise CppExtensionAdapterError(
                f"Task-2-only work 不得含性能工件 {forbidden}")
    driver = _driver_argv()
    result = subprocess.run(
        driver + ["--bundle", bundle, "--work", root], check=False)
    if result.returncode != 0:
        raise CppExtensionAdapterError(
            f"CPP Extension 外部 driver 失败 rc={result.returncode}")
    receipt = validate_receipt(root, caseset)
    import repo_adapter as RA
    evidence = RA.build_multi_output_evidence(
        caseset, root, os.path.join(root, _OUT))
    digest = _canonical_sha(receipt)
    for row in evidence:
        row["cpp_extension_receipt_sha256"] = digest
    return {
        "op": caseset["op"],
        "repo_mode": "cpp_extension",
        "runner_form": "cpp_extension",
        "runner_source": "generated_official_cpp_extension",
        "runner_path": receipt["artifact"]["path"],
        "evidence_grade": "acceptance_candidate",
        "task_scope": "task2_only",
        "performance_collected": False,
        "source_provenance": source_provenance_summary(receipt),
        "cpp_extension_receipt": receipt,
        "evidence": evidence,
    }


CPP_EXTENSION_MODES = {"cpp_extension": run_cpp_extension}
