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

import cann_version
import content_address
import cpp_extension_codegen
import cpp_extension_identity
import perf_mode
import perf_evidence_contract
import precision_policy
import stochastic_collector
import stochastic_contract
import tensor_shape_attrs
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
_STOCHASTIC_PRECONDITION = "stochastic_precondition.json"
_STOCHASTIC_FORMAL = "stochastic_formal_evidence.json"

LAYOUT_LEDGER_SCHEMA = "oprunway.tensor_layout_ledger"
LAYOUT_LEDGER_VERSION = 1
LAYOUT_EXECUTION_SCHEMA = "oprunway.cpp_extension_layout_execution"
LAYOUT_EXECUTION_VERSION = 1
BASE_STORAGE_V1 = "base_storage_v1"
_LAYOUT_INPUT_FIELDS = frozenset({
    "storage_representation", "base_storage_path", "layout_requirement_id",
    "layout_receipt", "layout_receipt_sha256",
})
_LAYOUT_OUTPUT_FIELDS = frozenset({
    "layout_requirement_id", "layout_receipt", "layout_receipt_sha256",
})
ATOMIC_ATTR_LEDGER_SCHEMA = "oprunway.atomic_attr_case_ledger"
ATOMIC_ATTR_LEDGER_VERSION = 1
ATOMIC_ATTR_LEDGER_APPLICABILITY_VERSION = 2
TENSOR_SHAPE_ATTR_BINDINGS_SCHEMA = "oprunway.tensor_shape_attr_bindings"
TENSOR_SHAPE_ATTR_BINDINGS_VERSION = 1
INVOCATION_ACCOUNTING_SCHEMA = "oprunway.cpp_extension_invocation_accounting"
INVOCATION_ACCOUNTING_VERSION = 1


def _canonical_sha(value):
    return hashlib.sha256(content_address.canonical_json_bytes(value)).hexdigest()


def _atomic_cell_id(cell):
    return _canonical_sha({key: cell[key] for key in (
        "profile_id", "row_id", "row_sha256", "q_id", "q_sha256")})


def _validate_atomic_applicability(value, *, expected_status, where):
    """严格重放 v2 cell 的适用性收据；不从算子名或属性名推断。"""
    _layout_exact_keys(value, {
        "status", "applicability_sha256", "rank_domain",
        "required_nonempty_attrs", "reasons"}, where)
    status = value.get("status")
    if status != expected_status:
        raise CppExtensionAdapterError(
            f"{where}.applicability.status={status!r}，应为 {expected_status!r}")
    app_sha = value.get("applicability_sha256")
    if app_sha is None:
        if (value.get("rank_domain") is not None
                or value.get("required_nonempty_attrs") != []
                or value.get("reasons") != []
                or status != "executable"):
            raise CppExtensionAdapterError(
                f"{where}.applicability 无谓词摘要却声明了判据/排除理由")
        return value
    _layout_sha256(app_sha, f"{where}.applicability_sha256")
    rank = value.get("rank_domain")
    _layout_exact_keys(rank, {
        "input", "allowed_ranks", "actual_rank", "matched"},
        f"{where}.rank_domain")
    _layout_nonempty_string(rank.get("input"), f"{where}.rank_domain.input")
    allowed = rank.get("allowed_ranks")
    if (not isinstance(allowed, list) or not allowed
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 0
                   for item in allowed)
            or len(allowed) != len(set(allowed))):
        raise CppExtensionAdapterError(
            f"{where}.rank_domain.allowed_ranks 须为非空、无重复的非负整数列表")
    actual_rank = rank.get("actual_rank")
    if isinstance(actual_rank, bool) or not isinstance(actual_rank, int) or actual_rank < 0:
        raise CppExtensionAdapterError(
            f"{where}.applicability.actual_rank 须为非负整数")
    if type(rank.get("matched")) is not bool \
            or rank["matched"] != (actual_rank in allowed):
        raise CppExtensionAdapterError(
            f"{where}.applicability rank matched 与 actual_rank/allowed_ranks 不一致")
    attrs = value.get("required_nonempty_attrs")
    if not isinstance(attrs, list):
        raise CppExtensionAdapterError(
            f"{where}.required_nonempty_attrs 须为列表")
    seen_attrs = set()
    for index, item in enumerate(attrs):
        item_where = f"{where}.required_nonempty_attrs[{index}]"
        _layout_exact_keys(item, {"attr", "actual_length", "matched"}, item_where)
        name = _layout_nonempty_string(item.get("attr"), f"{item_where}.attr")
        if name in seen_attrs:
            raise CppExtensionAdapterError(
                f"{where}.required_nonempty_attrs 属性 {name!r} 重复")
        seen_attrs.add(name)
        length = item.get("actual_length")
        if isinstance(length, bool) or not isinstance(length, int) or length < 0:
            raise CppExtensionAdapterError(
                f"{item_where}.actual_length 须为非负整数")
        if type(item.get("matched")) is not bool or item["matched"] != (length > 0):
            raise CppExtensionAdapterError(
                f"{item_where}.matched 与 actual_length 不一致")
    expected_reasons = []
    if not rank["matched"]:
        expected_reasons.append({
            "kind": "rank_outside_domain", "input": rank["input"],
            "actual_rank": actual_rank, "allowed_ranks": list(allowed),
        })
    expected_reasons.extend(
        {"kind": "required_attr_empty", "attr": item["attr"]}
        for item in attrs if not item["matched"])
    if not _layout_equal(value.get("reasons"), expected_reasons,
                         f"{where}.applicability.reasons"):
        raise CppExtensionAdapterError(
            f"{where}.applicability reasons 与 rank/非空属性判据不一致")
    derived_status = "excluded" if expected_reasons else "executable"
    if status != derived_status:
        raise CppExtensionAdapterError(
            f"{where}.applicability status 与确定性判据不一致")
    return value


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


def _multi_item_projection(item):
    keys = ("name", "kind", "binding", "shape", "dtype", "format")
    return {key: item.get(key) for key in keys}


def _layout_error(message, ex=None):
    error = CppExtensionAdapterError(message)
    if ex is not None:
        error.__cause__ = ex
    return error


def _layout_exact_keys(value, expected, where):
    if not isinstance(value, dict):
        raise CppExtensionAdapterError(f"{where} 须为 object")
    got = set(value)
    wanted = set(expected)
    if got != wanted:
        raise CppExtensionAdapterError(
            f"{where} 键集合漂移：缺 {sorted(wanted - got)}，多 {sorted(got - wanted)}")


def _layout_equal(left, right, where):
    try:
        return (content_address.canonical_json_bytes(left)
                == content_address.canonical_json_bytes(right))
    except (TypeError, ValueError) as ex:
        raise CppExtensionAdapterError(f"{where} 不是 canonical JSON：{ex}") from ex


def validate_caseset_golden_invocation(caseset):
    """复核 generated golden 新 ABI 的声明/context/receipt；legacy 返回 ``None``。

    下游不执行 golden.py，也不重新猜 logical dtype；只把 caseset 的 context 与同一 case
    已冻结的 ``inputs[].name/dtype`` 逐项对账。任一侧单改都使 receipt 或 caseset SHA 漂移。
    """
    if not isinstance(caseset, dict) or not isinstance(caseset.get("cases"), list):
        raise CppExtensionAdapterError("golden invocation caseset/cases 须为 object/list")
    cases = caseset["cases"]
    receipt = caseset.get("golden_invocation_receipt")
    context_cases = [
        case for case in cases
        if isinstance(case, dict) and "golden_case_context" in case
    ]
    if receipt is None:
        if context_cases:
            raise CppExtensionAdapterError(
                "legacy caseset 不得凭空带 golden_case_context")
        return None
    if not isinstance(receipt, dict):
        raise CppExtensionAdapterError("golden_invocation_receipt 须为 object")
    _layout_exact_keys(receipt, {
        "schema", "schema_version", "invocation", "invocation_sha256",
        "case_context_schema", "case_context_schema_version", "case_count",
        "case_contexts", "case_contexts_sha256",
    }, "golden_invocation_receipt")
    if (receipt.get("schema") != precision_policy.GOLDEN_INVOCATION_RECEIPT_SCHEMA
            or isinstance(receipt.get("schema_version"), bool)
            or receipt.get("schema_version")
            != precision_policy.GOLDEN_INVOCATION_RECEIPT_SCHEMA_VERSION):
        raise CppExtensionAdapterError("golden_invocation_receipt schema/version 非法")
    try:
        invocation = precision_policy.golden_invocation_contract(
            {"invocation": receipt.get("invocation")},
            where="caseset.golden_invocation_receipt")
    except ValueError as ex:
        raise CppExtensionAdapterError(f"golden invocation contract 非法：{ex}") from ex
    expected_invocation_sha = content_address.content_digest(
        precision_policy.GOLDEN_INVOCATION_CONTRACT_DOMAIN, invocation)
    if receipt.get("invocation_sha256") != expected_invocation_sha:
        raise CppExtensionAdapterError(
            "golden_invocation_receipt.invocation_sha256 漂移")
    if (receipt.get("case_context_schema")
            != precision_policy.GOLDEN_CASE_CONTEXT_SCHEMA
            or isinstance(receipt.get("case_context_schema_version"), bool)
            or receipt.get("case_context_schema_version")
            != precision_policy.GOLDEN_CASE_CONTEXT_SCHEMA_VERSION):
        raise CppExtensionAdapterError(
            "golden_invocation_receipt 的 case context schema/version 非法")
    records, seen = [], set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise CppExtensionAdapterError(f"cases[{index}] 须为 object")
        cid = case.get("id")
        if not isinstance(cid, str) or not cid or cid in seen:
            raise CppExtensionAdapterError(
                f"golden invocation case id 缺失或重复: {cid!r}")
        seen.add(cid)
        inputs = case.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise CppExtensionAdapterError(f"{cid}.inputs 须为非空列表")
        try:
            context = precision_policy.validate_golden_case_context(
                case.get("golden_case_context"), expected_inputs=inputs,
                where=f"{cid}.golden_case_context")
        except ValueError as ex:
            raise CppExtensionAdapterError(f"{cid} golden context 非法：{ex}") from ex
        records.append({
            "case_id": cid,
            "sha256": content_address.content_digest(
                precision_policy.GOLDEN_CASE_CONTEXT_DOMAIN, context),
        })
    if (isinstance(receipt.get("case_count"), bool)
            or receipt.get("case_count") != len(records)
            or receipt.get("case_contexts") != records
            or receipt.get("case_contexts_sha256") != content_address.content_digest(
                precision_policy.GOLDEN_CASE_CONTEXTS_DOMAIN, records)):
        raise CppExtensionAdapterError(
            "golden_invocation_receipt 的 case 分母/context 摘要与 caseset 漂移")
    return receipt


def _layout_nonempty_string(value, where):
    if not isinstance(value, str) or not value.strip():
        raise CppExtensionAdapterError(f"{where} 须为非空字符串")
    return value


def _layout_sha256(value, where):
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise CppExtensionAdapterError(f"{where} 须为 64 位小写 sha256")
    return value


def _layout_relative_path(value, where):
    path = _layout_nonempty_string(value, where)
    normalized = os.path.normpath(path)
    if os.path.isabs(path) or normalized in (".", "..") \
            or normalized.startswith(".." + os.sep):
        raise CppExtensionAdapterError(f"{where} 须为不逃逸根目录的相对路径")
    return path


def _invocation_plan_rows(plan):
    if not isinstance(plan, dict):
        raise CppExtensionAdapterError("invocation accounting 的 plan 须为 object")
    cases = plan.get("cases")
    excluded = plan.get("excluded", [])
    if not isinstance(cases, list) or not isinstance(excluded, list):
        raise CppExtensionAdapterError("invocation plan.cases/excluded 须为列表")
    rows = []
    seen = set()
    for partition, values in (("cases", cases), ("excluded", excluded)):
        for index, row in enumerate(values):
            if not isinstance(row, dict):
                raise CppExtensionAdapterError(
                    f"invocation plan.{partition}[{index}] 须为 object")
            cid = row.get("case_id")
            if not isinstance(cid, str) or not cid:
                raise CppExtensionAdapterError(
                    f"invocation plan.{partition}[{index}].case_id 缺失")
            if cid in seen:
                raise CppExtensionAdapterError(
                    f"invocation plan cases/excluded 重复 case_id={cid!r}")
            seen.add(cid)
            if partition == "excluded" and (
                    not isinstance(row.get("reason"), str) or not row["reason"]):
                raise CppExtensionAdapterError(
                    f"invocation plan.excluded[{index}].reason 缺失")
            rows.append({
                "case_id": cid,
                "partition": partition,
                "plan_row_sha256": _canonical_sha(row),
                **({"reason": row["reason"]} if partition == "excluded" else {}),
            })
    return rows


def _invocation_case_ids(values, where):
    if not isinstance(values, list) or any(
            not isinstance(value, str) or not value for value in values):
        raise CppExtensionAdapterError(f"{where} 须为非空 case_id 组成的列表（可为空）")
    if len(values) != len(set(values)):
        raise CppExtensionAdapterError(f"{where} 含重复 case_id")
    return list(values)


def build_invocation_accounting(plan, *, produced_case_ids, failed_case_ids):
    """生成 receipt.invocation；分母只来自冻结 plan，不从执行结果反推。"""
    plan_rows = _invocation_plan_rows(plan)
    executable = [row["case_id"] for row in plan_rows if row["partition"] == "cases"]
    produced = _invocation_case_ids(produced_case_ids, "produced_case_ids")
    failed = _invocation_case_ids(failed_case_ids, "failed_case_ids")
    if set(produced) & set(failed):
        raise CppExtensionAdapterError("produced/failed case_id 不得重叠")
    if set(produced) | set(failed) != set(executable):
        raise CppExtensionAdapterError(
            "produced∪failed 未完整且唯一覆盖 invocation plan.cases")
    outcomes = {cid: "produced" for cid in produced}
    outcomes.update({cid: "failed" for cid in failed})
    records = []
    for row in plan_rows:
        record = dict(row)
        record["outcome"] = (
            outcomes[row["case_id"]] if row["partition"] == "cases" else "excluded")
        records.append(record)
    return {
        "schema": INVOCATION_ACCOUNTING_SCHEMA,
        "schema_version": INVOCATION_ACCOUNTING_VERSION,
        "total": len(plan_rows),
        "planned": len(executable),
        "produced": len(produced),
        "failed": len(failed),
        "excluded": len(plan_rows) - len(executable),
        "failed_case_ids": [row["case_id"] for row in records
                            if row["outcome"] == "failed"],
        "case_records": records,
    }


def validate_invocation_accounting(plan, value, *, evidence=None):
    """重放 plan.cases∪excluded 分母，并可与最终 evidence outcome 交叉。"""
    _layout_exact_keys(value, {
        "schema", "schema_version", "total", "planned", "produced", "failed",
        "excluded", "failed_case_ids", "case_records",
    }, "receipt.invocation")
    if value.get("schema") != INVOCATION_ACCOUNTING_SCHEMA \
            or type(value.get("schema_version")) is not int \
            or value["schema_version"] != INVOCATION_ACCOUNTING_VERSION:
        raise CppExtensionAdapterError(
            f"receipt.invocation 须为 {INVOCATION_ACCOUNTING_SCHEMA} "
            f"v{INVOCATION_ACCOUNTING_VERSION}")
    plan_rows = _invocation_plan_rows(plan)
    records = value.get("case_records")
    if not isinstance(records, list) or len(records) != len(plan_rows):
        raise CppExtensionAdapterError(
            "receipt.invocation.case_records 未完整覆盖 plan.cases∪excluded")
    outcomes = []
    for index, (expected, record) in enumerate(zip(plan_rows, records)):
        keys = {"case_id", "partition", "plan_row_sha256", "outcome"}
        if expected["partition"] == "excluded":
            keys.add("reason")
        _layout_exact_keys(record, keys, f"receipt.invocation.case_records[{index}]")
        for key in ("case_id", "partition", "plan_row_sha256"):
            if record.get(key) != expected[key]:
                raise CppExtensionAdapterError(
                    f"receipt.invocation.case_records[{index}].{key} 与 plan 漂移")
        _layout_sha256(record.get("plan_row_sha256"),
                       f"receipt.invocation.case_records[{index}].plan_row_sha256")
        if expected["partition"] == "cases":
            if record.get("outcome") not in ("produced", "failed"):
                raise CppExtensionAdapterError(
                    f"receipt.invocation.case_records[{index}].outcome 非 produced/failed")
        else:
            if record.get("outcome") != "excluded" \
                    or record.get("reason") != expected["reason"]:
                raise CppExtensionAdapterError(
                    f"receipt.invocation.case_records[{index}] excluded reason/outcome 漂移")
        outcomes.append(record["outcome"])
    recomputed = {
        "total": len(plan_rows),
        "planned": sum(row["partition"] == "cases" for row in plan_rows),
        "produced": outcomes.count("produced"),
        "failed": outcomes.count("failed"),
        "excluded": outcomes.count("excluded"),
    }
    for key, expected in recomputed.items():
        if type(value.get(key)) is not int or value[key] != expected:
            raise CppExtensionAdapterError(
                f"receipt.invocation.{key}={value.get(key)!r} 与 plan/records 重算 {expected} 不一致")
    wanted_failed = [record["case_id"] for record in records
                     if record["outcome"] == "failed"]
    failed_ids = _invocation_case_ids(
        value.get("failed_case_ids"), "receipt.invocation.failed_case_ids")
    if failed_ids != wanted_failed:
        raise CppExtensionAdapterError(
            "receipt.invocation.failed_case_ids 未按 plan 顺序逐字绑定失败 records")
    if evidence is None:
        return value
    if not isinstance(evidence, list):
        raise CppExtensionAdapterError("cpp_extension evidence 须为列表")
    evidence_by_id = {}
    for index, row in enumerate(evidence):
        if not isinstance(row, dict):
            raise CppExtensionAdapterError(f"evidence[{index}] 须为 object")
        cid = row.get("case_id")
        if not isinstance(cid, str) or not cid or cid in evidence_by_id:
            raise CppExtensionAdapterError(
                f"evidence case_id 缺失/重复：{cid!r}")
        evidence_by_id[cid] = row
    expected_ids = {row["case_id"] for row in records}
    if set(evidence_by_id) != expected_ids:
        raise CppExtensionAdapterError(
            "evidence 未完整且唯一覆盖 receipt.invocation.case_records")
    status_outcomes = {
        "ok": "produced",
        "execution_failed": "failed",
        GOLDEN_UNAVAILABLE: "excluded",
    }
    for record in records:
        status = evidence_by_id[record["case_id"]].get("status")
        if status_outcomes.get(status) != record["outcome"]:
            raise CppExtensionAdapterError(
                f"{record['case_id']}: evidence.status={status!r} 与 "
                f"receipt invocation outcome={record['outcome']!r} 不一致")
    return value


def _case_output_contracts(case):
    expected = case.get("expected")
    if not isinstance(expected, dict):
        return []
    outputs = expected.get("outputs")
    if isinstance(outputs, list):
        return outputs
    return [expected]


def validate_caseset_tensor_shape_attr_contract(caseset):
    """重放 atomic A×Q 与 cyclic receipt；legacy 返回 ``None``。

    覆盖身份只从 caseset 顶层 ledger 与逐 case binding 交叉取得。adapter 不重新
    解释任务书，也不按属性名猜轴语义；它只证明 planner 落下的 P×A×Q 单元、
    raw attr、具名输入 rank 与摘要仍逐字一致。
    """
    if not isinstance(caseset, dict) or not isinstance(caseset.get("cases"), list):
        raise CppExtensionAdapterError("tensor_shape_attr caseset/cases 须为 object/list")
    binding_rows = []
    atomic_rows = []
    seen_case_ids = set()
    for case_index, case in enumerate(caseset["cases"]):
        if not isinstance(case, dict):
            raise CppExtensionAdapterError(f"cases[{case_index}] 须为 object")
        cid = case.get("id")
        if not isinstance(cid, str) or not cid or cid in seen_case_ids:
            raise CppExtensionAdapterError(f"tensor_shape_attr case id 缺失或重复: {cid!r}")
        seen_case_ids.add(cid)
        bindings = case.get("contract_bindings")
        if bindings is None:
            continue
        if not isinstance(bindings, dict) or not bindings:
            raise CppExtensionAdapterError(f"{cid}.contract_bindings 须为非空 object")
        unknown = set(bindings) - {
            "atomic_attr_row", "independent_attr_combination", "cyclic_indices"}
        if unknown:
            raise CppExtensionAdapterError(
                f"{cid}.contract_bindings 含未知字段 {sorted(unknown)}")
        atomic = bindings.get("atomic_attr_row")
        independent = bindings.get("independent_attr_combination")
        if (atomic is None) != (independent is None):
            raise CppExtensionAdapterError(
                f"{cid}: atomic_attr_row 与 independent_attr_combination 必须同时在场")
        if atomic is not None:
            _layout_exact_keys(atomic, {
                "row_id", "row_sha256", "atomic_rows_sha256", "attrs"},
                f"{cid}.atomic_attr_row")
            _layout_exact_keys(independent, {"q_id", "q_sha256", "attrs"},
                               f"{cid}.independent_attr_combination")
            for label, value in (
                    ("row_sha256", atomic.get("row_sha256")),
                    ("atomic_rows_sha256", atomic.get("atomic_rows_sha256")),
                    ("q_sha256", independent.get("q_sha256"))):
                _layout_sha256(value, f"{cid}.{label}")
            row_id = _layout_nonempty_string(atomic.get("row_id"), f"{cid}.row_id")
            q_id = _layout_nonempty_string(independent.get("q_id"), f"{cid}.q_id")
            attrs = case.get("attrs")
            if not isinstance(attrs, dict) or not isinstance(atomic.get("attrs"), dict) \
                    or not isinstance(independent.get("attrs"), dict):
                raise CppExtensionAdapterError(f"{cid}: atomic/Q/case attrs 须为 object")
            for source, label in ((atomic["attrs"], "atomic"),
                                  (independent["attrs"], "independent")):
                projected = {name: attrs.get(name) for name in source}
                if not _layout_equal(projected, source, f"{cid}.{label}_attrs"):
                    raise CppExtensionAdapterError(
                        f"{cid}: case attrs 与 {label} binding 不一致")
            if _canonical_sha(independent["attrs"]) != independent["q_sha256"]:
                raise CppExtensionAdapterError(f"{cid}: q_sha256 与独立属性组合不一致")
            profile = case.get("parameter_contract")
            profile_id = profile.get("profile_id") if isinstance(profile, dict) else None
            if not isinstance(profile_id, str) or not profile_id:
                raise CppExtensionAdapterError(
                    f"{cid}: atomic case 缺 parameter_contract.profile_id（P 轴身份）")
            profile_inputs = profile.get("inputs")
            if not isinstance(profile_inputs, list):
                raise CppExtensionAdapterError(
                    f"{cid}: atomic case 的 parameter_contract.inputs 缺失")
            atomic_rows.append({
                "profile_id": profile_id,
                "row_id": row_id,
                "row_sha256": atomic["row_sha256"],
                "q_id": q_id,
                "q_sha256": independent["q_sha256"],
                "case_id": cid,
                "atomic_rows_sha256": atomic["atomic_rows_sha256"],
                "atomic_attrs": atomic["attrs"],
                "profile_inputs": profile_inputs,
            })

        cyclic = bindings.get("cyclic_indices")
        if cyclic is not None:
            if not isinstance(cyclic, dict) or not cyclic:
                raise CppExtensionAdapterError(f"{cid}.cyclic_indices 须为非空 object")
            inputs = case.get("inputs")
            attrs = case.get("attrs")
            if not isinstance(inputs, list) or not isinstance(attrs, dict):
                raise CppExtensionAdapterError(f"{cid}: cyclic case 缺 inputs/attrs")
            for name, receipt in cyclic.items():
                _layout_nonempty_string(name, f"{cid}.cyclic attr name")
                _layout_exact_keys(receipt, {
                    "raw", "normalized", "rank", "had_negative",
                    "duplicate_policy", "input"}, f"{cid}.cyclic_indices.{name}")
                input_ref = receipt.get("input")
                _layout_exact_keys(input_ref, {
                    "name", "index", "shape", "shape_receipt_sha256"},
                    f"{cid}.cyclic_indices.{name}.input")
                index = input_ref.get("index")
                if isinstance(index, bool) or not isinstance(index, int) \
                        or not 0 <= index < len(inputs):
                    raise CppExtensionAdapterError(
                        f"{cid}.cyclic_indices.{name}.input.index 非合法 input index")
                item = inputs[index]
                if not isinstance(item, dict) or item.get("name") != input_ref.get("name") \
                        or item.get("shape") != input_ref.get("shape"):
                    raise CppExtensionAdapterError(
                        f"{cid}.cyclic_indices.{name} 未绑定具名 input shape")
                _layout_sha256(input_ref.get("shape_receipt_sha256"),
                               f"{cid}.cyclic_indices.{name}.shape_receipt_sha256")
                shape = item.get("shape")
                if _canonical_sha(tensor_shape_attrs.make_shape_receipt(shape)) \
                        != input_ref["shape_receipt_sha256"]:
                    raise CppExtensionAdapterError(
                        f"{cid}.cyclic_indices.{name} input shape receipt 漂移")
                if not _layout_equal(attrs.get(name), receipt.get("raw"),
                                     f"{cid}.cyclic_indices.{name}.raw"):
                    raise CppExtensionAdapterError(
                        f"{cid}.cyclic_indices.{name}.raw 未逐字绑定 DUT attr")
                try:
                    rebuilt = tensor_shape_attrs.normalize_cyclic_indices(
                        receipt["raw"], len(shape),
                        duplicate_policy=receipt["duplicate_policy"],
                        where=f"{cid}.cyclic_indices.{name}.recompute")
                except tensor_shape_attrs.TensorShapeAttrError as ex:
                    raise CppExtensionAdapterError(f"{cid}: {ex}") from ex
                rebuilt["input"] = input_ref
                if not _layout_equal(rebuilt, receipt,
                                     f"{cid}.cyclic_indices.{name}"):
                    raise CppExtensionAdapterError(
                        f"{cid}.cyclic_indices.{name} 与具名 rank 确定性重算不一致")
        binding_rows.append({"case_id": cid, "contract_bindings": bindings})

    ledger = caseset.get("atomic_attr_ledger")
    ledger_sha = caseset.get("atomic_attr_ledger_sha256")
    if (ledger is None) != (ledger_sha is None):
        raise CppExtensionAdapterError(
            "atomic_attr_ledger 与 atomic_attr_ledger_sha256 必须同时在场")
    if atomic_rows and ledger is None:
        raise CppExtensionAdapterError("atomic cases 缺顶层 atomic_attr_ledger")
    if ledger is not None:
        version = ledger.get("schema_version")
        if ledger.get("schema") != ATOMIC_ATTR_LEDGER_SCHEMA \
                or type(version) is not int \
                or version not in {
                    ATOMIC_ATTR_LEDGER_VERSION,
                    ATOMIC_ATTR_LEDGER_APPLICABILITY_VERSION}:
            raise CppExtensionAdapterError(
                f"atomic_attr_ledger 须为 {ATOMIC_ATTR_LEDGER_SCHEMA} "
                f"v{ATOMIC_ATTR_LEDGER_VERSION}/"
                f"v{ATOMIC_ATTR_LEDGER_APPLICABILITY_VERSION}")
        common_keys = {
            "schema", "schema_version", "atomic_rows_sha256", "source_binding",
            "profiles", "atomic_rows", "independent_combinations", "emitted", "cells"}
        if version == ATOMIC_ATTR_LEDGER_VERSION:
            _layout_exact_keys(
                ledger, common_keys | {"expected"}, "atomic_attr_ledger")
        else:
            _layout_exact_keys(ledger, common_keys | {
                "structure_denominator_total", "planned", "excluded",
                "excluded_cells"}, "atomic_attr_ledger")
        _layout_sha256(ledger_sha, "atomic_attr_ledger_sha256")
        if _canonical_sha(ledger) != ledger_sha:
            raise CppExtensionAdapterError("atomic_attr_ledger_sha256 与 ledger 重算不一致")
        table_sha = _layout_sha256(
            ledger.get("atomic_rows_sha256"), "atomic_attr_ledger.atomic_rows_sha256")
        source_binding = ledger.get("source_binding")
        _layout_exact_keys(source_binding, {
            "compose_kind", "spec_sha256", "taskdoc_snapshot_sha256",
            "source_facts_sha256"}, "atomic_attr_ledger.source_binding")
        if source_binding.get("compose_kind") != "spec_taskdoc_compose":
            raise CppExtensionAdapterError("atomic source_binding.compose_kind 非受控值")
        for key in ("spec_sha256", "taskdoc_snapshot_sha256", "source_facts_sha256"):
            _layout_sha256(source_binding.get(key), f"atomic source_binding.{key}")
        positive_keys = ["profiles", "atomic_rows", "independent_combinations", "emitted"]
        if version == ATOMIC_ATTR_LEDGER_VERSION:
            positive_keys.append("expected")
        else:
            positive_keys.extend(["structure_denominator_total", "planned"])
        for key in positive_keys:
            if isinstance(ledger.get(key), bool) or not isinstance(ledger.get(key), int) \
                    or ledger[key] < 1:
                raise CppExtensionAdapterError(f"atomic_attr_ledger.{key} 须为正整数")
        product = (ledger["profiles"] * ledger["atomic_rows"]
                   * ledger["independent_combinations"])
        if version == ATOMIC_ATTR_LEDGER_VERSION:
            if product != ledger["expected"] or ledger["expected"] != ledger["emitted"]:
                raise CppExtensionAdapterError("atomic P×A×Q 三重计数不一致")
        else:
            excluded = ledger.get("excluded")
            if isinstance(excluded, bool) or not isinstance(excluded, int) or excluded < 0:
                raise CppExtensionAdapterError("atomic_attr_ledger.excluded 须为非负整数")
            if (product != ledger["structure_denominator_total"]
                    or ledger["planned"] + excluded != product
                    or ledger["planned"] != ledger["emitted"]):
                raise CppExtensionAdapterError(
                    "atomic applicability 完整分母 P×A×Q 与 planned/excluded/emitted 不一致")
        cells = ledger.get("cells")
        if not isinstance(cells, list) or len(cells) != ledger["emitted"]:
            raise CppExtensionAdapterError("atomic cells 数与 emitted 不一致")
        normalized_cells = []
        for index, cell in enumerate(cells):
            keys = {
                "profile_id", "row_id", "row_sha256", "q_id", "q_sha256",
                "case_id"}
            if version == ATOMIC_ATTR_LEDGER_APPLICABILITY_VERSION:
                keys |= {"cell_id", "applicability"}
            _layout_exact_keys(cell, keys, f"atomic_attr_ledger.cells[{index}]")
            normalized_cells.append(cell)
        expected_cells = [{key: row[key] for key in (
            "profile_id", "row_id", "row_sha256", "q_id", "q_sha256", "case_id")}
                          for row in atomic_rows]
        emitted_projection = [{key: cell[key] for key in expected_cells[0]}
                              for cell in normalized_cells] if expected_cells else []
        if not _layout_equal(emitted_projection, expected_cells, "atomic cells"):
            raise CppExtensionAdapterError(
                "atomic cells 未按 case 顺序逐字绑定 P/row/Q/case")
        if any(row["atomic_rows_sha256"] != table_sha for row in atomic_rows):
            raise CppExtensionAdapterError("atomic case 的 table digest 与顶层 ledger 漂移")
        denominator_cells = list(normalized_cells)
        if version == ATOMIC_ATTR_LEDGER_APPLICABILITY_VERSION:
            excluded_cells = ledger.get("excluded_cells")
            if not isinstance(excluded_cells, list) \
                    or len(excluded_cells) != ledger["excluded"]:
                raise CppExtensionAdapterError(
                    "atomic applicability excluded_cells 数与 excluded/完整分母不一致")
            for index, cell in enumerate(excluded_cells):
                where = f"atomic_attr_ledger.excluded_cells[{index}]"
                _layout_exact_keys(cell, {
                    "profile_id", "row_id", "row_sha256", "q_id", "q_sha256",
                    "cell_id", "applicability"}, where)
                _validate_atomic_applicability(
                    cell["applicability"], expected_status="excluded", where=where)
            for index, cell in enumerate(normalized_cells):
                where = f"atomic_attr_ledger.cells[{index}]"
                app = _validate_atomic_applicability(
                    cell["applicability"], expected_status="executable", where=where)
                row = atomic_rows[index]
                rank = app.get("rank_domain")
                if rank is not None:
                    named = [item for item in row["profile_inputs"]
                             if isinstance(item, dict)
                             and item.get("name") == rank["input"]
                             and item.get("kind") == "tensor"]
                    if len(named) != 1 or not isinstance(named[0].get("shape"), list) \
                            or len(named[0]["shape"]) != rank["actual_rank"]:
                        raise CppExtensionAdapterError(
                            f"{where}.applicability.actual_rank 未绑定具名 profile input")
                    for attr_receipt in app["required_nonempty_attrs"]:
                        raw_attr = row["atomic_attrs"].get(attr_receipt["attr"])
                        if not isinstance(raw_attr, list) \
                                or len(raw_attr) != attr_receipt["actual_length"]:
                            raise CppExtensionAdapterError(
                                f"{where}.applicability 非空属性长度未绑定 case atomic attrs")
            denominator_cells.extend(excluded_cells)
            cell_ids = []
            for index, cell in enumerate(denominator_cells):
                where = f"atomic denominator cell[{index}]"
                for key in ("profile_id", "row_id", "q_id"):
                    _layout_nonempty_string(cell.get(key), f"{where}.{key}")
                for key in ("row_sha256", "q_sha256", "cell_id"):
                    _layout_sha256(cell.get(key), f"{where}.{key}")
                if cell["cell_id"] != _atomic_cell_id(cell):
                    raise CppExtensionAdapterError(
                        f"{where}.cell_id 与 P/A/Q identity 确定性重算不一致")
                cell_ids.append(cell["cell_id"])
            if len(cell_ids) != len(set(cell_ids)):
                raise CppExtensionAdapterError(
                    "atomic denominator cell_id identity 重复（executed/excluded 交叉或复制）")
        profiles = {row["profile_id"] for row in denominator_cells}
        rows = {(row["row_id"], row["row_sha256"]) for row in denominator_cells}
        qs = {(row["q_id"], row["q_sha256"]) for row in denominator_cells}
        triples = {(row["profile_id"], row["row_id"], row["q_id"])
                   for row in denominator_cells}
        if len({row["row_id"] for row in denominator_cells}) != len(rows):
            raise CppExtensionAdapterError("atomic row_id 对应多个 row_sha256 identity")
        if len({row["q_id"] for row in denominator_cells}) != len(qs):
            raise CppExtensionAdapterError("atomic q_id 对应多个 q_sha256 identity")
        if (len(profiles), len(rows), len(qs), len(triples)) != (
                ledger["profiles"], ledger["atomic_rows"],
                ledger["independent_combinations"], product):
            raise CppExtensionAdapterError(
                "atomic P/A/Q identity 不完整（丢单元、复制单元或 row/Q digest 漂移）")
    elif ledger_sha is not None:
        raise CppExtensionAdapterError("legacy caseset 凭空声明 atomic ledger digest")

    if not binding_rows and ledger is None:
        return None
    binding_ledger = {
        "schema": TENSOR_SHAPE_ATTR_BINDINGS_SCHEMA,
        "schema_version": TENSOR_SHAPE_ATTR_BINDINGS_VERSION,
        "cases": binding_rows,
    }
    return {
        "bindings": binding_ledger,
        "bindings_sha256": _canonical_sha(binding_ledger),
        "atomic_ledger": ledger,
        "atomic_ledger_sha256": ledger_sha,
    }


def validate_invocation_tensor_shape_attr_contract(caseset, plan=None):
    """把 invocation plan 的顶层摘要与逐 case binding 摘要接回 caseset。"""
    contract = validate_caseset_tensor_shape_attr_contract(caseset)
    if not isinstance(plan, dict):
        return contract
    top_keys = {"tensor_shape_attr_bindings_sha256", "atomic_attr_ledger_sha256"}
    rows = plan.get("cases")
    excluded = plan.get("excluded", [])
    if not isinstance(rows, list) or not isinstance(excluded, list):
        raise CppExtensionAdapterError("invocation plan.cases/excluded 须为列表")
    if contract is None:
        if set(plan) & top_keys or any(
                isinstance(row, dict) and "contract_bindings_sha256" in row
                for row in [*rows, *excluded]):
            raise CppExtensionAdapterError(
                "legacy invocation plan 凭空声明 tensor shape/attr binding")
        return None
    if plan.get("tensor_shape_attr_bindings_sha256") != contract["bindings_sha256"]:
        raise CppExtensionAdapterError(
            "invocation plan.tensor_shape_attr_bindings_sha256 与 caseset 漂移")
    if contract["atomic_ledger_sha256"] is None:
        if "atomic_attr_ledger_sha256" in plan:
            raise CppExtensionAdapterError(
                "无 atomic ledger 的 plan 不得凭空声明 atomic_attr_ledger_sha256")
    elif plan.get("atomic_attr_ledger_sha256") != contract["atomic_ledger_sha256"]:
        raise CppExtensionAdapterError(
            "invocation plan.atomic_attr_ledger_sha256 与 caseset 漂移")
    binding_by_id = {row["case_id"]: row["contract_bindings"]
                     for row in contract["bindings"]["cases"]}
    planned_ids = set()
    for row in [*rows, *excluded]:
        if not isinstance(row, dict):
            raise CppExtensionAdapterError(
                "invocation plan case/excluded row 须为 object")
        cid = row.get("case_id")
        if cid in planned_ids:
            raise CppExtensionAdapterError(
                f"invocation plan cases/excluded 重复 case_id={cid!r}")
        binding = binding_by_id.get(cid)
        if binding is None:
            if "contract_bindings_sha256" in row:
                raise CppExtensionAdapterError(
                    f"{cid}: 非 structure case 凭空声明 binding digest")
            continue
        planned_ids.add(cid)
        if row.get("contract_bindings_sha256") != _canonical_sha(binding):
            raise CppExtensionAdapterError(
                f"{cid}: plan structure binding digest 与 caseset 漂移")
    if planned_ids != set(binding_by_id):
        raise CppExtensionAdapterError(
            "invocation plan 未完整覆盖 tensor shape/attr binding cases")
    return contract


def _has_any_layout_field(value, fields):
    return isinstance(value, dict) and bool(set(value) & set(fields))


def _validate_layout_receipt(receipt, *, shape, role, case_id, name, index, digest, where):
    _layout_sha256(digest, f"{where}.layout_receipt_sha256")
    try:
        normalized = tensor_shape_attrs.validate_layout_receipt(
            receipt,
            expected_shape=shape,
            expected_role=role,
            expected_case_id=case_id,
            expected_tensor_name=name,
            expected_tensor_index=index,
            where=f"{where}.layout_receipt",
        )
        actual_sha = tensor_shape_attrs.layout_receipt_sha256(
            normalized, where=f"{where}.layout_receipt")
    except tensor_shape_attrs.TensorShapeAttrError as ex:
        raise CppExtensionAdapterError(f"{where}: {ex}") from ex
    if actual_sha != digest:
        raise CppExtensionAdapterError(
            f"{where}.layout_receipt_sha256 漂移：声明 {digest}，重算 {actual_sha}")
    if normalized.get("format") != tensor_shape_attrs.TENSOR_FORMAT_ND:
        raise CppExtensionAdapterError(f"{where}: N7 v1 layout 只支持 format=nd")
    return normalized


def validate_caseset_layout_contract(caseset):
    """从 caseset 外部 ledger 绑定每个布局 case/slot/receipt；legacy 返回 ``None``。"""
    if not isinstance(caseset, dict):
        raise CppExtensionAdapterError("caseset 须为 object")
    cases = caseset.get("cases")
    if not isinstance(cases, list):
        raise CppExtensionAdapterError("caseset.cases 须为列表")
    normalized_cases = []
    seen_case_ids = set()
    seen_requirement_ids = set()
    for case_index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise CppExtensionAdapterError(f"cases[{case_index}] 须为 object")
        cid = case.get("id")
        if not isinstance(cid, str) or not cid or cid in seen_case_ids:
            raise CppExtensionAdapterError(f"layout caseset case id 缺失或重复: {cid!r}")
        seen_case_ids.add(cid)
        call = case.get("aclnn_call")
        slots = call.get("slots") if isinstance(call, dict) else None
        inputs = case.get("inputs")
        if not isinstance(inputs, list):
            inputs = []
        if not isinstance(slots, list):
            slots = []
        input_slots = {}
        output_slots = {}
        for slot_index, slot in enumerate(slots):
            if not isinstance(slot, dict):
                raise CppExtensionAdapterError(f"{cid}: slots[{slot_index}] 须为 object")
            role = slot.get("role")
            if role == "in":
                tensor_index = slot.get("input_idx")
                if (isinstance(tensor_index, bool) or not isinstance(tensor_index, int)
                        or tensor_index in input_slots):
                    raise CppExtensionAdapterError(
                        f"{cid}: layout input_idx={tensor_index!r} 非唯一整数")
                input_slots[tensor_index] = slot
            elif role == "out":
                tensor_index = slot.get("output_idx")
                if (isinstance(tensor_index, bool) or not isinstance(tensor_index, int)
                        or tensor_index in output_slots):
                    raise CppExtensionAdapterError(
                        f"{cid}: layout output_idx={tensor_index!r} 非唯一整数")
                output_slots[tensor_index] = slot

        normalized_inputs = []
        for tensor_index, item in enumerate(inputs):
            has_layout = _has_any_layout_field(item, _LAYOUT_INPUT_FIELDS)
            slot = input_slots.get(tensor_index)
            slot_has_layout = _has_any_layout_field(slot, _LAYOUT_INPUT_FIELDS)
            if not has_layout and not slot_has_layout:
                continue
            if not isinstance(item, dict) or set(item) & _LAYOUT_INPUT_FIELDS != _LAYOUT_INPUT_FIELDS:
                raise CppExtensionAdapterError(
                    f"{cid}: inputs[{tensor_index}] layout 字段不完整")
            if not isinstance(slot, dict):
                raise CppExtensionAdapterError(
                    f"{cid}: inputs[{tensor_index}] 无对应 aclnn_call input slot")
            if set(slot) & (_LAYOUT_INPUT_FIELDS - {"base_storage_path", "layout_receipt"}) \
                    != (_LAYOUT_INPUT_FIELDS - {"base_storage_path", "layout_receipt"}):
                raise CppExtensionAdapterError(
                    f"{cid}: input slot#{tensor_index} layout 绑定字段不完整")
            if item.get("storage_representation") != BASE_STORAGE_V1:
                raise CppExtensionAdapterError(
                    f"{cid}: inputs[{tensor_index}].storage_representation 只支持 {BASE_STORAGE_V1}")
            if "path" in item:
                raise CppExtensionAdapterError(
                    f"{cid}: base_storage_v1 input 不得同时携带 legacy path")
            _layout_relative_path(
                item.get("base_storage_path"), f"{cid}.inputs[{tensor_index}].base_storage_path")
            name = _layout_nonempty_string(item.get("name"), f"{cid}.inputs[{tensor_index}].name")
            requirement_id = _layout_nonempty_string(
                item.get("layout_requirement_id"),
                f"{cid}.inputs[{tensor_index}].layout_requirement_id")
            if requirement_id in seen_requirement_ids:
                raise CppExtensionAdapterError(
                    f"layout requirement_id={requirement_id!r} 重复；一份 receipt 不得冒充双覆盖")
            seen_requirement_ids.add(requirement_id)
            if item.get("format") != tensor_shape_attrs.TENSOR_FORMAT_ND:
                raise CppExtensionAdapterError(
                    f"{cid}.inputs[{tensor_index}]: N7 v1 layout 只支持 format=nd")
            slot_projection = {
                key: slot.get(key) for key in (
                    "name", "kind", "binding", "shape", "dtype", "format",
                    "storage_representation", "layout_requirement_id",
                    "layout_receipt_sha256")
            }
            item_projection = {
                key: item.get(key) for key in slot_projection
            }
            if not _layout_equal(slot_projection, item_projection,
                                 f"{cid}.inputs[{tensor_index}].slot_binding"):
                raise CppExtensionAdapterError(
                    f"{cid}: input layout slot#{tensor_index} 与 case item 逐字段不一致")
            receipt = _validate_layout_receipt(
                item.get("layout_receipt"), shape=item.get("shape"),
                role=tensor_shape_attrs.LAYOUT_ROLE_INPUT,
                case_id=cid, name=name, index=tensor_index,
                digest=item.get("layout_receipt_sha256"),
                where=f"{cid}.inputs[{tensor_index}]")
            normalized_inputs.append({
                "requirement_id": requirement_id,
                "tensor_index": tensor_index,
                "name": name,
                "layout_receipt_sha256": item["layout_receipt_sha256"],
                "layout_receipt": receipt,
            })

        normalized_outputs = []
        for tensor_index, output in enumerate(_case_output_contracts(case)):
            has_layout = _has_any_layout_field(output, _LAYOUT_OUTPUT_FIELDS)
            slot = output_slots.get(tensor_index)
            slot_has_layout = _has_any_layout_field(slot, _LAYOUT_OUTPUT_FIELDS)
            if not has_layout and not slot_has_layout:
                continue
            if not isinstance(output, dict) or set(output) & _LAYOUT_OUTPUT_FIELDS \
                    != _LAYOUT_OUTPUT_FIELDS:
                raise CppExtensionAdapterError(
                    f"{cid}: expected output#{tensor_index} layout 字段不完整")
            if not isinstance(slot, dict):
                raise CppExtensionAdapterError(
                    f"{cid}: expected output#{tensor_index} 无对应 aclnn_call output slot")
            if set(slot) & _LAYOUT_OUTPUT_FIELDS != {
                    "layout_requirement_id", "layout_receipt_sha256"}:
                raise CppExtensionAdapterError(
                    f"{cid}: output slot#{tensor_index} layout 绑定字段不完整")
            slot_name = _layout_nonempty_string(
                slot.get("name"), f"{cid}.slots.output[{tensor_index}].name")
            declared_name = output.get("name")
            if declared_name is not None and declared_name != slot_name:
                raise CppExtensionAdapterError(
                    f"{cid}: expected output#{tensor_index}.name 与 slot.name 不一致")
            requirement_id = _layout_nonempty_string(
                output.get("layout_requirement_id"),
                f"{cid}.expected.output[{tensor_index}].layout_requirement_id")
            if requirement_id in seen_requirement_ids:
                raise CppExtensionAdapterError(
                    f"layout requirement_id={requirement_id!r} 重复；一份 receipt 不得冒充双覆盖")
            seen_requirement_ids.add(requirement_id)
            shape = output.get("out_shape")
            dtype = output.get("compare_dtype")
            expected_slot = {
                "name": slot_name,
                "kind": "tensor",
                "binding": "device_tensor",
                "shape": shape,
                "dtype": dtype,
                "format": tensor_shape_attrs.TENSOR_FORMAT_ND,
                "layout_requirement_id": requirement_id,
                "layout_receipt_sha256": output.get("layout_receipt_sha256"),
            }
            actual_slot = {key: slot.get(key) for key in expected_slot}
            if not _layout_equal(actual_slot, expected_slot,
                                 f"{cid}.outputs[{tensor_index}].slot_binding"):
                raise CppExtensionAdapterError(
                    f"{cid}: output layout slot#{tensor_index} 与 expected 逐字段不一致")
            receipt = _validate_layout_receipt(
                output.get("layout_receipt"), shape=shape,
                role=tensor_shape_attrs.LAYOUT_ROLE_OUTPUT,
                case_id=cid, name=slot_name, index=tensor_index,
                digest=output.get("layout_receipt_sha256"),
                where=f"{cid}.expected.output[{tensor_index}]")
            normalized_outputs.append({
                "requirement_id": requirement_id,
                "tensor_index": tensor_index,
                "name": slot_name,
                "layout_receipt_sha256": output["layout_receipt_sha256"],
                "layout_receipt": receipt,
            })
        if normalized_inputs or normalized_outputs:
            normalized_cases.append({
                "case_id": cid,
                "inputs": normalized_inputs,
                "outputs": normalized_outputs,
            })

    ledger_present = "layout_ledger" in caseset or "layout_ledger_sha256" in caseset
    if not normalized_cases:
        if ledger_present:
            raise CppExtensionAdapterError(
                "caseset 声明 layout_ledger，但没有任何 layout-enabled case")
        return None
    if "layout_ledger" not in caseset or "layout_ledger_sha256" not in caseset:
        raise CppExtensionAdapterError(
            "layout-enabled caseset 必须同时携带外部 layout_ledger/layout_ledger_sha256")
    ledger = caseset.get("layout_ledger")
    _layout_exact_keys(ledger, {"schema", "schema_version", "cases"}, "layout_ledger")
    if ledger.get("schema") != LAYOUT_LEDGER_SCHEMA \
            or type(ledger.get("schema_version")) is not int \
            or ledger.get("schema_version") != LAYOUT_LEDGER_VERSION:
        raise CppExtensionAdapterError(
            f"layout_ledger 须为 {LAYOUT_LEDGER_SCHEMA} v{LAYOUT_LEDGER_VERSION}")
    expected_ledger_cases = []
    for case in normalized_cases:
        expected_ledger_cases.append({
            "case_id": case["case_id"],
            "inputs": [{key: item[key] for key in (
                "requirement_id", "tensor_index", "name", "layout_receipt_sha256")}
                for item in case["inputs"]],
            "outputs": [{key: item[key] for key in (
                "requirement_id", "tensor_index", "name", "layout_receipt_sha256")}
                for item in case["outputs"]],
        })
    ledger_cases = ledger.get("cases")
    if not isinstance(ledger_cases, list):
        raise CppExtensionAdapterError("layout_ledger.cases 须为列表")
    for index, ledger_case in enumerate(ledger_cases):
        _layout_exact_keys(
            ledger_case, {"case_id", "inputs", "outputs"},
            f"layout_ledger.cases[{index}]")
        for role in ("inputs", "outputs"):
            entries = ledger_case.get(role)
            if not isinstance(entries, list):
                raise CppExtensionAdapterError(
                    f"layout_ledger.cases[{index}].{role} 须为列表")
            for entry_index, entry in enumerate(entries):
                _layout_exact_keys(
                    entry,
                    {"requirement_id", "tensor_index", "name", "layout_receipt_sha256"},
                    f"layout_ledger.cases[{index}].{role}[{entry_index}]")
    if content_address.canonical_json_bytes(ledger_cases) != \
            content_address.canonical_json_bytes(expected_ledger_cases):
        raise CppExtensionAdapterError(
            "layout_ledger 与 case/slot/requirement_id/receipt digest 逐字绑定漂移")
    ledger_sha = _layout_sha256(
        caseset.get("layout_ledger_sha256"), "layout_ledger_sha256")
    actual_sha = _canonical_sha(ledger)
    if ledger_sha != actual_sha:
        raise CppExtensionAdapterError(
            f"layout_ledger_sha256 漂移：声明 {ledger_sha}，重算 {actual_sha}")
    return {"ledger": ledger, "sha256": ledger_sha, "cases": normalized_cases}


def validate_invocation_layout_contract(caseset, manifest, plan=None):
    """把外部 layout ledger 绑定到 codegen format 与 invocation plan。"""
    contract = validate_caseset_layout_contract(caseset)
    if contract is None:
        if isinstance(plan, dict) and "layout_ledger_sha256" in plan:
            raise CppExtensionAdapterError(
                "legacy caseset 的 invocation plan 不得凭空声明 layout_ledger_sha256")
        return None
    multi = manifest.get("multi_input_receipt") if isinstance(manifest, dict) else None
    if multi is None:
        if manifest.get("tensor_acl_format") != tensor_shape_attrs.TENSOR_FORMAT_ND:
            raise CppExtensionAdapterError(
                "N7 layout 禁止 torch_npu_rank_default；manifest 必须显式 ND")
    else:
        formats = {(row.get("io"), row.get("name")): row.get("format")
                   for row in multi.get("tensor_parameters") or []
                   if isinstance(row, dict)}
        for case in contract["cases"]:
            for item in case["inputs"]:
                if formats.get(("in", item["name"])) != tensor_shape_attrs.TENSOR_FORMAT_ND:
                    raise CppExtensionAdapterError(
                        f"layout input {item['name']!r} 的 manifest 逐参数 format 非 ND")
            for item in case["outputs"]:
                if formats.get(("out", item["name"])) != tensor_shape_attrs.TENSOR_FORMAT_ND:
                    raise CppExtensionAdapterError(
                        f"layout output {item['name']!r} 的 manifest 逐参数 format 非 ND")
    if isinstance(plan, dict) and plan.get("layout_ledger_sha256") != contract["sha256"]:
        raise CppExtensionAdapterError(
            "invocation plan.layout_ledger_sha256 与 caseset 外部 ledger 漂移")
    return contract


def _attr_contract_by_name(manifest):
    contract = manifest.get("attr_parameter_contract")
    if contract is None:
        return None, None
    _layout_exact_keys(
        contract, {"schema", "schema_version", "parameters"},
        "manifest.attr_parameter_contract")
    if contract.get("schema") != cpp_extension_codegen.ATTR_PARAMETER_CONTRACT_SCHEMA \
            or type(contract.get("schema_version")) is not int \
            or contract.get("schema_version") != \
            cpp_extension_codegen.ATTR_PARAMETER_CONTRACT_VERSION:
        raise CppExtensionAdapterError("manifest.attr_parameter_contract schema 非法")
    parameters = contract.get("parameters")
    if not isinstance(parameters, list):
        raise CppExtensionAdapterError(
            "manifest.attr_parameter_contract.parameters 须为列表")
    by_name = {}
    for index, item in enumerate(parameters):
        _layout_exact_keys(
            item, {"name", "attr_type", "attr_ctype", "source"},
            f"manifest.attr_parameter_contract.parameters[{index}]")
        name = _layout_nonempty_string(
            item.get("name"),
            f"manifest.attr_parameter_contract.parameters[{index}].name")
        if name in by_name:
            raise CppExtensionAdapterError(
                f"manifest.attr_parameter_contract 参数 {name!r} 重复")
        if item.get("attr_type") not in tensor_shape_attrs.ATTR_TYPES:
            raise CppExtensionAdapterError(
                f"manifest attr {name!r}.attr_type 非受控值")
        if item.get("source") not in ("spec_declared", "legacy_inferred"):
            raise CppExtensionAdapterError(
                f"manifest attr {name!r}.source 非受控值")
        _layout_nonempty_string(item.get("attr_ctype"), f"manifest attr {name!r}.attr_ctype")
        by_name[name] = item
    if not any(item["source"] == "spec_declared" for item in parameters):
        raise CppExtensionAdapterError(
            "attr_parameter_contract 在场却没有任何 spec_declared opt-in 参数")
    for index, variant in enumerate(manifest.get("variants") or []):
        active = variant.get("active_attrs")
        expected = [{"name": name, "attr_ctype": by_name[name]["attr_ctype"]}
                    for name in active or [] if name in by_name]
        if not isinstance(active, list) or len(expected) != len(active) \
                or variant.get("active_attr_contracts") != expected:
            raise CppExtensionAdapterError(
                f"manifest.variants[{index}].active_attr_contracts 未逐字绑定参数 ctype")
    return contract, by_name


def _case_active_attr_contracts(cid, slots, attr_by_name, case_attrs):
    if attr_by_name is None:
        return None
    if not isinstance(case_attrs, dict):
        raise CppExtensionAdapterError(
            f"{cid}: manifest 声明 attr 参数契约，但 case.attrs 缺失/非 object")
    active = []
    for index, slot in enumerate(slots):
        if slot.get("role") != "attr":
            continue
        name = slot.get("name")
        declared = attr_by_name.get(name)
        if not isinstance(declared, dict):
            raise CppExtensionAdapterError(
                f"{cid}: slots[{index}] attr {name!r} 不在 manifest 参数契约")
        actual_ctype = slot.get("ctype")
        if actual_ctype != declared["attr_ctype"]:
            raise CppExtensionAdapterError(
                f"{cid}: attr_ctype 漂移：{name!r} slot={actual_ctype!r}, "
                f"manifest={declared['attr_ctype']!r}")
        try:
            tensor_shape_attrs.normalize_attr_value(
                slot.get("value"), attr_type=declared["attr_type"],
                where=f"{cid}.slots[{index}].value")
        except tensor_shape_attrs.TensorShapeAttrError as ex:
            raise CppExtensionAdapterError(f"{cid}: {ex}") from ex
        if name not in case_attrs or not _layout_equal(
                slot.get("value"), case_attrs[name],
                f"{cid}.slots[{index}].value_vs_case_attrs"):
            raise CppExtensionAdapterError(
                f"{cid}: attr slot.value 未逐字绑定 case.attrs[{name!r}]")
        active.append({"name": name, "attr_ctype": actual_ctype})
    return active


def _validate_case_parameter_contract(case, slots, manifest_receipt):
    """逐 case 对账 caseset item ↔ parameter_contract ↔ aclnn_call slot ↔ codegen manifest。"""
    cid = case.get("id")
    contract = case.get("parameter_contract")
    if not isinstance(contract, dict):
        raise CppExtensionAdapterError(
            f"{cid}: manifest 声明 multi_input，但 case 缺 parameter_contract")
    inputs = contract.get("inputs")
    output = contract.get("output")
    if not isinstance(inputs, list) or not inputs or not isinstance(output, dict):
        raise CppExtensionAdapterError(f"{cid}: parameter_contract inputs/output 非法")
    contract_tensors = [item for item in inputs if item.get("kind") == "tensor"]
    case_inputs = case.get("inputs")
    if not isinstance(case_inputs, list) or len(case_inputs) != len(contract_tensors):
        raise CppExtensionAdapterError(
            f"{cid}: parameter_contract tensor 数与 case.inputs 不一致")
    tensor_manifest = {
        (row.get("io"), row.get("name")): row
        for row in manifest_receipt.get("tensor_parameters") or []
    }
    scalar_manifest = {
        row.get("name"): row
        for row in manifest_receipt.get("host_scalar_parameters") or []
    }
    scalar_dtypes = {}
    seen_inputs = set()
    for slot_index, slot in enumerate(slots):
        role, name = slot.get("role"), slot.get("name")
        if role == "in":
            input_index = slot.get("input_idx")
            if isinstance(input_index, bool) or not isinstance(input_index, int) \
                    or not 0 <= input_index < len(contract_tensors) or input_index in seen_inputs:
                raise CppExtensionAdapterError(
                    f"{cid}: slots[{slot_index}] input_idx={input_index!r} 非完整唯一索引")
            seen_inputs.add(input_index)
            expected = contract_tensors[input_index]
            case_item = case_inputs[input_index]
            if (_multi_item_projection(slot) != _multi_item_projection(expected)
                    or _multi_item_projection(case_item) != _multi_item_projection(expected)):
                raise CppExtensionAdapterError(
                    f"{cid}: in slot/case item 与 parameter_contract[{input_index}] 不一致")
            static = tensor_manifest.get(("in", name))
            if not isinstance(static, dict) or any(
                    static.get(key) != expected.get(key)
                    for key in ("name", "kind", "binding", "format")):
                raise CppExtensionAdapterError(
                    f"{cid}: input {name!r} 与 manifest 逐参数 format/identity 不一致")
        elif role == "attr" and slot.get("binding") == "host_scalar":
            expected = next((item for item in inputs if item.get("name") == name), None)
            if not isinstance(expected, dict) or expected.get("kind") != "scalar" \
                    or expected.get("binding") != "host_scalar":
                raise CppExtensionAdapterError(
                    f"{cid}: host scalar slot {name!r} 未绑定 parameter_contract")
            if any(slot.get(key) != expected.get(key)
                   for key in ("name", "kind", "binding", "dtype", "value")) \
                    or (case.get("attrs") or {}).get(name) != expected.get("value"):
                raise CppExtensionAdapterError(
                    f"{cid}: host scalar {name!r} slot/attrs 与 parameter_contract 不一致")
            static = scalar_manifest.get(name)
            if not isinstance(static, dict) or expected["dtype"] not in (static.get("dtypes") or []):
                raise CppExtensionAdapterError(
                    f"{cid}: host scalar {name!r} dtype 未被 codegen manifest 覆盖")
            scalar_dtypes[name] = expected["dtype"]
        elif role == "out":
            if _multi_item_projection(slot) != _multi_item_projection(output):
                raise CppExtensionAdapterError(
                    f"{cid}: out slot 与 parameter_contract.output 不一致")
            static = tensor_manifest.get(("out", name))
            if not isinstance(static, dict) or any(
                    static.get(key) != output.get(key)
                    for key in ("name", "kind", "binding", "format")):
                raise CppExtensionAdapterError(
                    f"{cid}: output {name!r} 与 manifest 逐参数 format/identity 不一致")
            expected_output = case.get("expected") or {}
            if (expected_output.get("out_shape") != output.get("shape")
                    or expected_output.get("compare_dtype") != output.get("dtype")):
                raise CppExtensionAdapterError(
                    f"{cid}: expected 输出 shape/dtype 与 parameter_contract.output 不一致")
    if seen_inputs != set(range(len(contract_tensors))):
        raise CppExtensionAdapterError(
            f"{cid}: aclnn_call 未完整覆盖 parameter_contract tensor inputs")
    return _canonical_sha(contract), scalar_dtypes


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
    attr_contract, attr_by_name = _attr_contract_by_name(manifest)
    golden_invocation_receipt = validate_caseset_golden_invocation(caseset)
    golden_invocation_receipt_sha256 = (
        content_address.content_digest(
            precision_policy.GOLDEN_INVOCATION_RECEIPT_DOMAIN,
            golden_invocation_receipt)
        if golden_invocation_receipt is not None else None)
    layout_contract = validate_invocation_layout_contract(caseset, manifest)
    structure_contract = validate_caseset_tensor_shape_attr_contract(caseset)
    structure_by_id = ({row["case_id"]: row["contract_bindings"]
                        for row in structure_contract["bindings"]["cases"]}
                       if structure_contract is not None else {})
    manifest_multi = manifest.get("multi_input_receipt")
    caseset_multi = caseset.get("multi_input_ledger")
    if (manifest_multi is None) != (caseset_multi is None):
        raise CppExtensionAdapterError(
            "caseset.multi_input_ledger 与 manifest.multi_input_receipt 在场性不一致")
    multi_contract_sha = None
    if manifest_multi is not None:
        if not isinstance(manifest_multi, dict) or not isinstance(caseset_multi, dict):
            raise CppExtensionAdapterError("multi_input receipt/ledger 须为 object")
        multi_contract_sha = manifest_multi.get("contract_sha256")
        if caseset_multi.get("contract_sha256") != multi_contract_sha:
            raise CppExtensionAdapterError(
                "caseset 与 Extension manifest 的 multi_input contract 摘要漂移")
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
            excluded_row = {"case_id": cid, "reason": GOLDEN_UNAVAILABLE}
            if cid in structure_by_id:
                excluded_row["contract_bindings_sha256"] = _canonical_sha(
                    structure_by_id[cid])
            excluded.append(excluded_row)
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
        active_attr_contracts = _case_active_attr_contracts(
            cid, slots, attr_by_name, case.get("attrs"))
        parameter_contract_sha, scalar_dtypes = (None, {})
        if manifest_multi is not None:
            parameter_contract_sha, scalar_dtypes = _validate_case_parameter_contract(
                case, slots, manifest_multi)
        matches = [
            row for row in candidates
            if row.get("active_attrs") == active_attrs
            and row.get("active_outputs") == active_outputs
            and (attr_contract is None
                 or row.get("active_attr_contracts") == active_attr_contracts)
            and (manifest_multi is None
                 or row.get("host_scalar_dtypes") == scalar_dtypes)
        ]
        if len(matches) != 1:
            raise CppExtensionAdapterError(
                f"{cid}: symbol={symbol!r}, active attrs={active_attrs!r}, "
                f"active outputs={active_outputs!r} 匹配 Extension variant 数={len(matches)}")
        variant = matches[0]
        plan_row = {
            "case_id": cid,
            "symbol": symbol,
            "entrypoint": variant["entrypoint"],
            "slots": slots,
        }
        if parameter_contract_sha is not None:
            plan_row["parameter_contract_sha256"] = parameter_contract_sha
            plan_row["host_scalar_dtypes"] = dict(variant.get("host_scalar_dtypes") or {})
        if active_attr_contracts is not None:
            plan_row["active_attr_contracts"] = active_attr_contracts
        if cid in structure_by_id:
            binding = structure_by_id[cid]
            if not _layout_equal(case.get("contract_bindings"), binding,
                                 f"{cid}.contract_bindings"):
                raise CppExtensionAdapterError(
                    f"{cid}: structure binding 与 caseset 重算 ledger 漂移")
            plan_row["contract_bindings_sha256"] = _canonical_sha(binding)
        rows.append(plan_row)
    if not rows:
        raise CppExtensionAdapterError(
            "invocation plan 无任何可执行 case（全部被排除）——没有可跑的 DUT 调用，拒")
    plan = {
        "schema": "oprunway.cpp_extension_invocation_plan",
        "schema_version": 1,
        "caseset_sha256": _canonical_sha(caseset),
        "manifest_sha256": _canonical_sha(manifest),
        "namespace": manifest["namespace"],
        "cases": rows,
        # 分母台账：谁没进执行计划、为什么。空表 = 一条都没排除。
        "excluded": excluded,
    }
    if multi_contract_sha is not None:
        plan["multi_input_contract_sha256"] = multi_contract_sha
    if golden_invocation_receipt_sha256 is not None:
        plan["golden_invocation_receipt_sha256"] = golden_invocation_receipt_sha256
    if attr_contract is not None:
        plan["attr_parameter_contract_sha256"] = _canonical_sha(attr_contract)
    if layout_contract is not None:
        plan["layout_ledger_sha256"] = layout_contract["sha256"]
    if structure_contract is not None:
        plan["tensor_shape_attr_bindings_sha256"] = structure_contract["bindings_sha256"]
        if structure_contract["atomic_ledger_sha256"] is not None:
            plan["atomic_attr_ledger_sha256"] = structure_contract["atomic_ledger_sha256"]
    return plan


def _validate_runtime_layout_observation(observation, expected, role, where):
    _layout_exact_keys(
        observation, {"layout_receipt", "layout_receipt_sha256", "storage_data_ptr"}, where)
    pointer = observation.get("storage_data_ptr")
    if isinstance(pointer, bool) or not isinstance(pointer, int) or pointer <= 0:
        raise CppExtensionAdapterError(f"{where}.storage_data_ptr 须为正整数")
    digest = _layout_sha256(
        observation.get("layout_receipt_sha256"), f"{where}.layout_receipt_sha256")
    try:
        normalized = tensor_shape_attrs.assert_layout_preserved(
            expected["layout_receipt"], observation.get("layout_receipt"),
            expected_shape=expected["layout_receipt"]["logical_shape"],
            expected_role=role,
            expected_case_id=expected["layout_receipt"]["case_id"],
            expected_tensor_name=expected["name"],
            expected_tensor_index=expected["tensor_index"],
            expected_receipt_sha256=expected["layout_receipt_sha256"],
            where=where,
        )
    except tensor_shape_attrs.TensorShapeAttrError as ex:
        raise CppExtensionAdapterError(f"{where}: {ex}") from ex
    actual_digest = tensor_shape_attrs.layout_receipt_sha256(
        normalized, where=f"{where}.layout_receipt")
    if digest != actual_digest or digest != expected["layout_receipt_sha256"]:
        raise CppExtensionAdapterError(
            f"{where}.layout_receipt_sha256 未绑定外部 expected receipt")
    return {"layout_receipt": normalized,
            "layout_receipt_sha256": digest,
            "storage_data_ptr": pointer}


def validate_layout_execution(caseset, execution):
    """校验 driver 的 before/after 实测布局，身份只从 caseset 外部 ledger 取得。"""
    contract = validate_caseset_layout_contract(caseset)
    if contract is None:
        if execution is not None:
            raise CppExtensionAdapterError(
                "legacy caseset 不得凭空携带 layout_execution")
        return None
    _layout_exact_keys(
        execution, {"schema", "schema_version", "layout_ledger_sha256", "cases"},
        "layout_execution")
    if execution.get("schema") != LAYOUT_EXECUTION_SCHEMA \
            or type(execution.get("schema_version")) is not int \
            or execution.get("schema_version") != LAYOUT_EXECUTION_VERSION:
        raise CppExtensionAdapterError(
            f"layout_execution 须为 {LAYOUT_EXECUTION_SCHEMA} v{LAYOUT_EXECUTION_VERSION}")
    if execution.get("layout_ledger_sha256") != contract["sha256"]:
        raise CppExtensionAdapterError(
            "layout_execution.layout_ledger_sha256 与 caseset 外部 ledger 漂移")
    actual_cases = execution.get("cases")
    if not isinstance(actual_cases, list) or len(actual_cases) != len(contract["cases"]):
        raise CppExtensionAdapterError(
            "layout_execution case 数与外部 layout ledger 不一致（缺实际布局证据）")
    for case_index, (actual_case, expected_case) in enumerate(
            zip(actual_cases, contract["cases"])):
        where = f"layout_execution.cases[{case_index}]"
        _layout_exact_keys(actual_case, {"case_id", "inputs", "outputs"}, where)
        if actual_case.get("case_id") != expected_case["case_id"]:
            raise CppExtensionAdapterError(
                f"{where}.case_id 与外部 layout ledger 顺序/身份漂移")
        for role_key, layout_role in (
                ("inputs", tensor_shape_attrs.LAYOUT_ROLE_INPUT),
                ("outputs", tensor_shape_attrs.LAYOUT_ROLE_OUTPUT)):
            actual_items = actual_case.get(role_key)
            expected_items = expected_case[role_key]
            if not isinstance(actual_items, list) or len(actual_items) != len(expected_items):
                raise CppExtensionAdapterError(
                    f"{where}.{role_key} 数量与外部 layout ledger 不一致")
            for item_index, (actual, expected) in enumerate(
                    zip(actual_items, expected_items)):
                item_where = f"{where}.{role_key}[{item_index}]"
                _layout_exact_keys(actual, {
                    "layout_requirement_id", "tensor_index", "name",
                    "expected_layout_receipt_sha256", "before", "after",
                }, item_where)
                identity = {
                    "layout_requirement_id": expected["requirement_id"],
                    "tensor_index": expected["tensor_index"],
                    "name": expected["name"],
                    "expected_layout_receipt_sha256": expected["layout_receipt_sha256"],
                }
                if not _layout_equal(
                        {key: actual.get(key) for key in identity}, identity,
                        f"{item_where}.identity"):
                    raise CppExtensionAdapterError(
                        f"{item_where} requirement/slot/name/digest 与外部 ledger 漂移")
                before = _validate_runtime_layout_observation(
                    actual.get("before"), expected, layout_role, f"{item_where}.before")
                after = _validate_runtime_layout_observation(
                    actual.get("after"), expected, layout_role, f"{item_where}.after")
                if before["storage_data_ptr"] != after["storage_data_ptr"]:
                    raise CppExtensionAdapterError(
                        f"{item_where} base storage ptr 调用前后漂移")
                if content_address.canonical_json_bytes(before["layout_receipt"]) != \
                        content_address.canonical_json_bytes(after["layout_receipt"]):
                    raise CppExtensionAdapterError(
                        f"{item_where} 调用前后物理布局漂移")
    return execution


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
      唯一的产出。故改为从**已成功执行、可继续 profiler 调用**的 case 里选性能子集继续采，
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
    accuracy_passed = PM.accuracy_pass_ids(evidence)
    # measure_only records runtime cost and makes no performance pass claim.
    # Its executable partition is therefore transport-produced cases, even when
    # formal/statistical precision is FAIL/BLOCKED; failures remain in skipped.
    produced = {row.get("case_id") for row in evidence
                if isinstance(row, dict) and row.get("status") == "ok"}
    precision_ids = {
        case["id"] for case in caseset.get("cases") or []
        if "精度" in (case.get("dims") or [])
    }
    if not precision_ids:
        precision_ids = {case["id"] for case in caseset.get("cases") or []}
    not_passed = sorted(precision_ids - accuracy_passed)
    if not_passed and not measure_only:
        # 与 run_workflow 的 Task2 总门同口径：任何应裁精度 case 未通过，都不得提前采性能。
        # 性能 case 虽是 precision-pass 子集，但这个“子集”只在整份精度验收通过后做选择。
        return None, [{
            "case_id": cid,
            "reason": SKIPPED_PRECISION_OVERALL_GATE,
        } for cid in not_passed]
    eligible = produced if measure_only else accuracy_passed
    selected, skipped = PM.select_perf_cases(caseset, eligible)
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
        "execution_identity_expected": (
            perf_evidence_contract.expected_execution_identity(
                receipt, device_index=device)),
        "cases": selected,
        "skipped": skipped,
        # 精度台账：**分母完整落盘**，让「本轮为什么只采了这些 case」成为机读事实。
        # `gate_passed=False` 时下游必须继续把整体判成精度 FAIL —— 有实测耗时 ≠ 验收通过。
        "precision_gate": {
            "mode": mode,
            "gate_passed": not not_passed,
            "precision_case_total": len(precision_ids),
            "precision_passed_count": len(accuracy_passed & precision_ids),
            "precision_not_passed": not_passed,
            "note": ("measure_only：只测不比，性能子集取 transport 已成功产出、可执行的 case；"
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
                "symbol_identity": receipt["vendor"]["symbol_identity"],
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
    expected_identity = plan.get("execution_identity_expected")
    measure_only = plan.get("mode") == perf_mode.MODE_MEASURE_ONLY
    for record in records:
        cid = record["case_id"]
        custom = record.get("custom") if isinstance(record.get("custom"), dict) else {}
        try:
            perf_evidence_contract.validate_execution_identity(
                custom.get("execution_identity"), expected=expected_identity)
            if measure_only and custom.get("behavior") == "npu":
                perf_evidence_contract.validate_sampling_receipt(
                    custom, record.get("sampling_receipt"), case_id=cid,
                    warmup=plan.get("warmup"), repeat=plan.get("repeat"))
        except perf_evidence_contract.PerfEvidenceContractError as ex:
            raise CppExtensionAdapterError(
                f"{cid}: cpp_extension 性能 identity/sampling evidence 未闭合：{ex}") from ex


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
        summary = vendor_build_receipt.validate_for_acceptance(
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


def _validate_tensor_format_receipt(manifest, receipt):
    """校验「生成时生效的 tensor format」确实进了 driver receipt。

    driver 只原样镜像 manifest；结构的真源仍是 codegen。这样收据能直接
    说出 ``ACL_FORMAT_ND``，而不是只给一个 manifest hash 要人再反查。
    历史 rank-default 路径两侧都不写这个键，保持原字节。
    """
    tensor_format = manifest.get("tensor_acl_format")
    tensor_format_source = manifest.get("tensor_acl_format_source")
    # N2 之前的历史 manifest 可能两键都没有；它不得凭空带新收据。
    if tensor_format is None and tensor_format_source is None:
        if "tensor_format_receipt" in receipt:
            raise CppExtensionAdapterError(
                "receipt 声称了 tensor format，但 manifest 无对应生成事实")
        return
    try:
        expected = cpp_extension_codegen.tensor_format_receipt(
            tensor_format, tensor_format_source)
    except cpp_extension_codegen.CppExtensionCodegenError as ex:
        raise CppExtensionAdapterError(
            f"manifest tensor format 契约非法：{ex}") from ex
    recorded = receipt.get("tensor_format_receipt")
    if expected is None:
        if "tensor_format_receipt" in receipt:
            raise CppExtensionAdapterError(
                "rank-default manifest 不得凭空带 tensor_format_receipt")
        if "tensor_format_receipt" in manifest:
            raise CppExtensionAdapterError(
                "rank-default manifest 不得宣称显式 tensor format 收据")
        return
    if manifest.get("tensor_format_receipt") != expected:
        raise CppExtensionAdapterError(
            "manifest.tensor_format_receipt 与字段驱动的生效 format 不一致")
    if recorded != expected:
        raise CppExtensionAdapterError(
            "receipt.tensor_format_receipt 未原样镜像 manifest 的生效 format")


def _validate_multi_input_receipt(manifest, receipt):
    """多输入逐参数契约只由 codegen 生成，driver 必须逐字镜像。"""
    expected = manifest.get("multi_input_receipt")
    recorded = receipt.get("multi_input_receipt")
    if expected is None:
        if "multi_input_receipt" in receipt:
            raise CppExtensionAdapterError(
                "receipt 凭空声明 multi_input_receipt，但 manifest 无对应事实")
        return
    if not isinstance(expected, dict) \
            or expected.get("schema") != cpp_extension_codegen.MULTI_INPUT_RECEIPT_SCHEMA \
            or expected.get("schema_version") != cpp_extension_codegen.MULTI_INPUT_RECEIPT_VERSION:
        raise CppExtensionAdapterError("manifest.multi_input_receipt schema 非法")
    digest = expected.get("contract_sha256")
    if not isinstance(digest, str) or len(digest) != 64 \
            or any(char not in "0123456789abcdef" for char in digest):
        raise CppExtensionAdapterError(
            "manifest.multi_input_receipt.contract_sha256 非小写 sha256")
    tensors = expected.get("tensor_parameters")
    if not isinstance(tensors, list):
        raise CppExtensionAdapterError(
            "manifest.multi_input_receipt.tensor_parameters 非列表")
    for index, row in enumerate(tensors):
        if not isinstance(row, dict):
            raise CppExtensionAdapterError(
                f"multi_input tensor_parameters[{index}] 非对象")
        fmt = row.get("format")
        nd_fields = {
            "requested_format": cpp_extension_codegen.TENSOR_FORMAT_ND,
            "effective_acl_format": cpp_extension_codegen.ACL_FORMAT_ND_TOKEN,
            "format_source": (
                cpp_extension_codegen.MULTI_INPUT_FORMAT_SOURCE_PARAMETER_CONTRACT),
        }
        if fmt == cpp_extension_codegen.TENSOR_FORMAT_ND:
            if any(row.get(key) != value for key, value in nd_fields.items()):
                raise CppExtensionAdapterError(
                    f"multi_input tensor_parameters[{index}] ND requested/effective/source 漂移")
        elif fmt == cpp_extension_codegen.TENSOR_FORMAT_TORCH_NPU_DEFAULT:
            if any(key in row for key in nd_fields):
                raise CppExtensionAdapterError(
                    f"multi_input tensor_parameters[{index}] rank-default 冒领 ACL_FORMAT_ND")
        else:
            raise CppExtensionAdapterError(
                f"multi_input tensor_parameters[{index}].format 非受控值")
    if recorded != expected:
        raise CppExtensionAdapterError(
            "receipt.multi_input_receipt 未原样镜像 manifest 的逐参数契约")


def _validate_cann_runtime(runtime):
    """独立复算 ACL runtime probe；unknown/invalid 保留给三级门作 BLOCKED 判定。"""
    observation = runtime.get("cann") if isinstance(runtime, dict) else None
    try:
        cann_version.validate_observation_record(observation)
    except cann_version.CannVersionError as ex:
        raise CppExtensionAdapterError(f"receipt.runtime.cann: {ex}") from ex
    expected_flat = observation.get("normalized") or "unknown"
    if runtime.get("cann_version") != expected_flat:
        raise CppExtensionAdapterError(
            "receipt.runtime.cann_version 与 ACL probe 规范化结果不一致")
    defining = (observation.get("probe") or {}).get("defining_elf")
    if defining is not None:
        path = defining.get("path")
        if not isinstance(path, str) or not os.path.isabs(path) \
                or not os.path.isfile(path):
            raise CppExtensionAdapterError(
                "receipt.runtime.cann.probe.defining_elf 不在当前复核环境或非普通文件")
        if _file_sha(path) != defining.get("sha256"):
            raise CppExtensionAdapterError(
                "receipt.runtime.cann.probe.defining_elf sha256 与现场 ELF 漂移")
    return observation


def validate_receipt(work, caseset):
    """验证外部 driver 回传的 build/load receipt 与当前输入、源码、ELF 精确绑定。"""
    work = os.path.abspath(work)
    bundle = os.path.join(work, _BUNDLE)
    manifest = _strict_json(os.path.join(bundle, "extension_manifest.json"))
    plan = _strict_json(os.path.join(work, _PLAN))
    receipt = _strict_json(os.path.join(work, _RECEIPT))
    if receipt.get("schema") != "oprunway.cpp_extension_receipt" \
            or receipt.get("schema_version") != cann_version.RECEIPT_SCHEMA_VERSION \
            or receipt.get("status") != "VERIFIED":
        raise CppExtensionAdapterError(
            f"cpp_extension receipt schema/status 非 VERIFIED v{cann_version.RECEIPT_SCHEMA_VERSION}")

    golden_invocation_receipt = validate_caseset_golden_invocation(caseset)
    golden_invocation_receipt_sha256 = (
        content_address.content_digest(
            precision_policy.GOLDEN_INVOCATION_RECEIPT_DOMAIN,
            golden_invocation_receipt)
        if golden_invocation_receipt is not None else None)
    if golden_invocation_receipt_sha256 is None:
        if "golden_invocation_receipt_sha256" in plan:
            raise CppExtensionAdapterError(
                "legacy invocation plan 不得凭空声明 golden invocation receipt")
    elif plan.get("golden_invocation_receipt_sha256") \
            != golden_invocation_receipt_sha256:
        raise CppExtensionAdapterError(
            "invocation plan 的 golden invocation receipt 摘要与 caseset 漂移")
    expected = {
        "caseset_sha256": _canonical_sha(caseset),
        "manifest_sha256": _canonical_sha(manifest),
        "invocation_plan_sha256": _canonical_sha(plan),
        "spec_sha256": manifest.get("spec_sha256"),
    }
    if golden_invocation_receipt_sha256 is not None:
        expected["golden_invocation_receipt_sha256"] = (
            golden_invocation_receipt_sha256)
    bindings = receipt.get("bindings")
    if not isinstance(bindings, dict):
        raise CppExtensionAdapterError("receipt.bindings 缺失")
    if (golden_invocation_receipt_sha256 is None
            and "golden_invocation_receipt_sha256" in bindings):
        raise CppExtensionAdapterError(
            "legacy receipt 不得凭空声明 golden invocation receipt")
    for key, value in expected.items():
        _require_sha(f"expected.{key}", value)
        if bindings.get(key) != value:
            raise CppExtensionAdapterError(
                f"receipt.bindings.{key} 漂移：期望 {value}，得 {bindings.get(key)!r}")
    validate_invocation_accounting(plan, receipt.get("invocation"))

    _validate_tensor_format_receipt(manifest, receipt)
    _validate_multi_input_receipt(manifest, receipt)
    layout_contract = validate_invocation_layout_contract(caseset, manifest, plan)
    if layout_contract is None:
        if "layout_ledger_sha256" in receipt or "layout_execution" in receipt:
            raise CppExtensionAdapterError(
                "legacy receipt 不得凭空声明 layout ledger/execution")
    else:
        if receipt.get("layout_ledger_sha256") != layout_contract["sha256"]:
            raise CppExtensionAdapterError(
                "receipt.layout_ledger_sha256 与 caseset 外部 ledger 漂移")
        validate_layout_execution(caseset, receipt.get("layout_execution"))
    structure_contract = validate_invocation_tensor_shape_attr_contract(caseset, plan)
    if structure_contract is None:
        if ("tensor_shape_attr_bindings_sha256" in receipt
                or "atomic_attr_ledger_sha256" in receipt):
            raise CppExtensionAdapterError(
                "legacy receipt 不得凭空声明 tensor shape/attr binding")
    else:
        if receipt.get("tensor_shape_attr_bindings_sha256") \
                != structure_contract["bindings_sha256"]:
            raise CppExtensionAdapterError(
                "receipt.tensor_shape_attr_bindings_sha256 与 caseset 漂移")
        atomic_sha = structure_contract["atomic_ledger_sha256"]
        if atomic_sha is None:
            if "atomic_attr_ledger_sha256" in receipt:
                raise CppExtensionAdapterError(
                    "cyclic-only receipt 不得凭空声明 atomic_attr_ledger_sha256")
        elif receipt.get("atomic_attr_ledger_sha256") != atomic_sha:
            raise CppExtensionAdapterError(
                "receipt.atomic_attr_ledger_sha256 与 caseset 漂移")

    for key, rec in (manifest.get("files") or {}).items():
        if not isinstance(rec, dict):
            raise CppExtensionAdapterError(f"manifest.files.{key} 非 object")
        path = _safe(bundle, rec.get("path"))
        if not os.path.isfile(path) or _file_sha(path) != rec.get("sha256"):
            raise CppExtensionAdapterError(f"生成源码 {key} 缺失或摘要漂移")

    runtime = receipt.get("runtime")
    required_runtime = ("torch_version", "torch_npu_version", "cann_version", "cann", "soc",
                        "ascend_custom_opp_path")
    if not isinstance(runtime, dict) or any(not runtime.get(k) for k in required_runtime):
        raise CppExtensionAdapterError(
            f"receipt.runtime 须完整包含 {required_runtime}")
    _validate_cann_runtime(runtime)
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
            or not vendor.get("library_sha256") or not vendor.get("symbols_owned") \
            or not isinstance(vendor.get("symbol_identity"), dict):
        raise CppExtensionAdapterError(
            "receipt.vendor 缺库路径/摘要/双符号实际定义 ELF 身份")
    _require_sha("receipt.vendor.library_sha256", vendor["library_sha256"])
    try:
        identity = cpp_extension_identity.validate(
            vendor["symbol_identity"], invocation_plan=plan,
            library_path=vendor["library_path"],
            library_sha256=vendor["library_sha256"])
    except cpp_extension_identity.CppExtensionIdentityError as ex:
        raise CppExtensionAdapterError(f"receipt.vendor.symbol_identity: {ex}") from ex
    if vendor.get("symbols_owned") != identity["symbols"]:
        raise CppExtensionAdapterError(
            "receipt.vendor.symbols_owned 未由双符号实际定义 ELF 身份收据逐字派生")
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
    validate_stochastic_collection(work, caseset, receipt)
    return receipt


def _artifact_json(work, ref, expected_path, where):
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise CppExtensionAdapterError(f"{where} 工件引用须只含 path/sha256")
    if ref.get("path") != expected_path:
        raise CppExtensionAdapterError(f"{where}.path 必须为 {expected_path!r}")
    _require_sha(f"{where}.sha256", ref.get("sha256"))
    path = _safe(work, ref["path"])
    if not os.path.isfile(path) or _file_sha(path) != ref["sha256"]:
        raise CppExtensionAdapterError(f"{where} 文件缺失或 sha256 漂移")
    return _strict_json(path)


def validate_stochastic_collection(work, caseset, receipt):
    """随机收据复核；前提失败是 BLOCKED 状态，不是 precision fail。"""
    raw_contract = caseset.get("stochastic_contract")
    collection = receipt.get("stochastic_collection")
    if raw_contract is None:
        if collection is not None:
            raise CppExtensionAdapterError("非 stochastic caseset 不得冒领随机采集收据")
        return None
    try:
        contract = stochastic_contract.normalize_contract(raw_contract)
        plan, _cases, _n = stochastic_collector.validate_caseset_bindings(contract, caseset)
    except (stochastic_contract.StochasticContractError,
            stochastic_collector.StochasticCollectorError) as ex:
        raise CppExtensionAdapterError(f"stochastic caseset 契约非法：{ex}") from ex
    if not isinstance(collection, dict) \
            or collection.get("schema") != "oprunway.cpp_extension_stochastic_collection" \
            or collection.get("schema_version") != 1:
        raise CppExtensionAdapterError("stochastic collection schema/version 缺失")
    if collection.get("contract_sha256") != stochastic_contract.canonical_sha256(contract) \
            or collection.get("plan_sha256") != stochastic_contract.canonical_sha256(plan):
        raise CppExtensionAdapterError("stochastic collection contract/plan sha256 漂移")
    status = collection.get("status")
    if status == "blocked_collection":
        if collection.get("formal_evidence") is not None or not collection.get("reason"):
            raise CppExtensionAdapterError("blocked_collection 须无 formal 且有 reason")
        return {"status": status, "contract": contract, "plan": plan,
                "gate": {"status": stochastic_contract.EVAL_BLOCKED,
                         "ready_for_formal_precision": False,
                         "usable_for_verdict": False,
                         "reason": collection["reason"]}}
    if status not in ("blocked_precondition", "complete"):
        raise CppExtensionAdapterError(f"stochastic collection.status 非法：{status!r}")
    structural = collection.get("structural_execution")
    coverage_roles = {
        row["role"] for row in (caseset.get("stochastic_ledger") or {}).get("coverage_roles", [])
    }
    role_by_case = {
        case.get("id"): (case.get("stochastic") or {}).get("role")
        for case in caseset.get("cases") or [] if isinstance(case, dict)
    }
    if not isinstance(structural, dict) or structural.get("schema") != \
            "oprunway.stochastic_structural_execution" \
            or structural.get("schema_version") != 1 \
            or structural.get("planned") != len(coverage_roles):
        raise CppExtensionAdapterError("stochastic structural execution schema/分母非法")
    manifest = (_strict_json(os.path.join(work, _OUT, "out_manifest.json"))
                if coverage_roles else {"produced": [], "failed": []})
    produced_ids = {row.get("case_id") for row in manifest.get("produced") or []}
    failed_by_id = {row.get("case_id"): row for row in manifest.get("failed") or []
                    if isinstance(row, dict)}
    expected_produced, expected_failed = [], []
    for cid, role in role_by_case.items():
        if role not in coverage_roles:
            continue
        if cid in produced_ids:
            expected_produced.append({"case_id": cid, "role": role})
        elif cid in failed_by_id:
            failure = failed_by_id[cid]
            expected_failed.append({
                "case_id": cid, "role": role,
                "error_kind": failure.get("error_kind"), "error": failure.get("error"),
            })
        else:
            raise CppExtensionAdapterError(f"stochastic structural case={cid!r} 无执行结果")
    if structural.get("produced") != expected_produced \
            or structural.get("failed") != expected_failed:
        raise CppExtensionAdapterError("stochastic structural execution 与 out_manifest 漂移")
    reference = collection.get("reference_execution")
    if not isinstance(reference, dict) \
            or reference.get("runner") != "isolated_subprocess_without_dut_vendor_env" \
            or reference.get("manifest_path") != "stochastic_reference_execution.json":
        raise CppExtensionAdapterError("stochastic reference execution 身份/隔离形态缺失")
    reference_manifest = _safe(work, reference["manifest_path"])
    _require_sha("reference_execution.manifest_sha256", reference.get("manifest_sha256"))
    if not os.path.isfile(reference_manifest) \
            or _file_sha(reference_manifest) != reference["manifest_sha256"]:
        raise CppExtensionAdapterError("stochastic reference manifest 缺失或摘要漂移")
    manifest = _strict_json(reference_manifest)
    if manifest.get("device") != collection.get("device") \
            or manifest.get("custom_opp_path_present") is not False \
            or manifest.get("dut_vendor_env_present") is not False:
        raise CppExtensionAdapterError("stochastic reference manifest device/隔离环境漂移")
    oracle = contract["oracle_precondition"]
    if manifest.get("reference_method") != oracle["method_kind"] \
            or manifest.get("reference_callable") != oracle["callable"]:
        raise CppExtensionAdapterError("stochastic reference method/callable 与契约漂移")
    pre = _artifact_json(work, collection.get("precondition"),
                         _STOCHASTIC_PRECONDITION, "stochastic precondition")
    try:
        normalized = stochastic_contract.validate_precondition_receipt(contract, pre)
        gate = stochastic_contract.precondition_gate(contract, normalized)
    except stochastic_contract.StochasticContractError as ex:
        raise CppExtensionAdapterError(f"stochastic precondition 非法：{ex}") from ex
    device = collection.get("device")
    if device != normalized["execution"]["dut_device"]:
        raise CppExtensionAdapterError("stochastic collection.device 与前提 device 漂移")
    if status == "blocked_precondition":
        if gate["ready_for_formal_precision"] or collection.get("formal_evidence") is not None:
            raise CppExtensionAdapterError("blocked_precondition 不得带正式证据")
        return {"status": status, "contract": contract, "plan": plan,
                "precondition": normalized, "formal": None, "gate": gate}
    if not gate["ready_for_formal_precision"]:
        raise CppExtensionAdapterError("complete collection 的 RNG 前提未通过")
    formal = _artifact_json(work, collection.get("formal_evidence"),
                            _STOCHASTIC_FORMAL, "stochastic formal evidence")
    try:
        evaluation = stochastic_contract.evaluate_formal_evidence(
            contract, normalized, formal)
    except stochastic_contract.StochasticContractError as ex:
        raise CppExtensionAdapterError(f"stochastic formal evidence 非法：{ex}") from ex
    return {"status": status, "contract": contract, "plan": plan,
            "precondition": normalized, "formal": formal,
            "gate": gate, "evaluation": evaluation}


def source_provenance_summary(receipt):
    """从已校过的 receipt 取本轮 DUT 的源身份摘要（含机读降级挂账）；供 envelope/报告直读。"""
    vendor = receipt.get("vendor") if isinstance(receipt, dict) else None
    if not isinstance(vendor, dict):
        raise CppExtensionAdapterError("receipt.vendor 缺失，无法取源身份摘要")
    try:
        return vendor_build_receipt.summarize(vendor.get("build_receipt"))
    except vendor_build_receipt.VendorBuildReceiptError as ex:
        raise CppExtensionAdapterError(f"receipt.vendor.build_receipt: {ex}") from ex


def _build_execution_evidence(caseset, work, receipt):
    """随机 capability 不制造逐点 golden；其正式判据只来自独立统计工件。"""
    if caseset.get("stochastic_contract") is None:
        import repo_adapter as RA
        rows = RA.build_multi_output_evidence(
            caseset, work, os.path.join(work, _OUT))
        if caseset.get("execution_scope") == "environment_identity_smoke":
            _bind_stochastic_transport_outputs(rows, work)
        return rows
    validated = validate_stochastic_collection(work, caseset, receipt)
    invocation = validate_invocation_accounting(
        _strict_json(os.path.join(work, _PLAN)), receipt.get("invocation"))
    outcomes = {
        row["case_id"]: row["outcome"]
        for row in invocation["case_records"]
    }
    out_manifest = _strict_json(os.path.join(work, _OUT, "out_manifest.json"))
    failed = {
        row.get("case_id"): row for row in (out_manifest.get("failed") or [])
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }
    produced = {
        row.get("case_id"): row for row in (out_manifest.get("produced") or [])
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }
    rows = []
    for case in caseset.get("cases") or []:
        binding = case.get("stochastic")
        cid = case.get("id")
        evidence_row = {
            "case_id": cid,
            # Formal stochastic predicates are separate from transport success.
            # Preserve the driver's per-case outcome so a rejected rank/dtype is
            # evidence-incomplete (BLOCKED), never rewritten into a synthetic OK.
            "status": ("ok" if outcomes.get(cid) == "produced"
                       else "execution_failed"),
            "stochastic": binding,
            "precision": {
                "compare": "stochastic",
                "out_shape": (case.get("expected") or {}).get("out_shape"),
                "out_dtype": (case.get("expected") or {}).get("compare_dtype"),
            },
        }
        if outcomes.get(cid) == "failed":
            detail = failed.get(cid) or {}
            evidence_row["error"] = detail.get("error") or "driver execution failed"
            evidence_row["error_kind"] = detail.get("error_kind") or "execution_failed"
        rows.append(evidence_row)
    _bind_stochastic_transport_outputs(rows, work)
    return rows, validated


def _bind_stochastic_transport_outputs(rows, work):
    """把 stochastic runtime output 文件绑定到 evidence；不制造逐点 golden。"""
    manifest = _strict_json(os.path.join(work, _OUT, "out_manifest.json"))
    produced = {row.get("case_id"): row for row in manifest.get("produced") or []
                if isinstance(row, dict)}
    for evidence_row in rows:
        precision = evidence_row.get("precision") if isinstance(evidence_row, dict) else None
        if not isinstance(precision, dict) or precision.get("compare") != "stochastic" \
                or evidence_row.get("status") != "ok":
            continue
        cid = evidence_row.get("case_id")
        transport = []
        for output in produced.get(cid, {}).get("outputs") or []:
            rel = output.get("path") if isinstance(output, dict) else None
            path = (_safe(os.path.join(work, _OUT), rel)
                    if isinstance(rel, str) else None)
            if path is None or not os.path.isfile(path) or os.path.islink(path):
                raise CppExtensionAdapterError(
                    f"{cid}: stochastic produced output 缺失/逃逸/非普通文件")
            transport.append({
                "index": output.get("index"), "name": output.get("name"),
                "out_path": os.path.relpath(path, work),
                "out_sha256": _file_sha(path),
            })
        if not transport:
            raise CppExtensionAdapterError(
                f"{cid}: stochastic produced outcome 缺实际 output artifact")
        precision["transport_outputs"] = transport
        evidence_row["output_written_check"] = "passed"


def _bind_multi_input_evidence(caseset, evidence, receipt):
    """把逐参数执行身份原样带进 evidence；只绑定事实，不参与 pass/fail。"""
    multi = receipt.get("multi_input_receipt") if isinstance(receipt, dict) else None
    if multi is None:
        return evidence
    contract_sha = multi.get("contract_sha256")
    by_id = {case.get("id"): case for case in caseset.get("cases") or []}
    seen = set()
    for row in evidence:
        cid = row.get("case_id")
        case = by_id.get(cid)
        if not isinstance(case, dict):
            raise CppExtensionAdapterError(
                f"evidence case_id={cid!r} 不在 multi_input caseset")
        parameter_contract = case.get("parameter_contract")
        if not isinstance(parameter_contract, dict):
            raise CppExtensionAdapterError(
                f"{cid}: multi_input evidence 缺 caseset parameter_contract")
        row["multi_input_contract_sha256"] = contract_sha
        row["parameter_contract_sha256"] = _canonical_sha(parameter_contract)
        row["parameter_contract"] = parameter_contract
        seen.add(cid)
    # golden_unavailable 可以没有执行结果，但 build_multi_output_evidence 仍应给一条结构化状态；
    # 若将来消费方改变这一点，这里不能把缺 evidence 静默当完整。
    expected_ids = {case.get("id") for case in caseset.get("cases") or []}
    if seen != expected_ids:
        raise CppExtensionAdapterError(
            f"multi_input evidence case 集漂移：缺 {sorted(expected_ids - seen)}，"
            f"多 {sorted(seen - expected_ids)}")
    return evidence


def _bind_layout_evidence(caseset, evidence, receipt):
    """把已校过的实际 input/output before/after 布局原样镜像到 evidence。"""
    contract = validate_caseset_layout_contract(caseset)
    if contract is None:
        if isinstance(receipt, dict) and (
                "layout_ledger_sha256" in receipt or "layout_execution" in receipt):
            raise CppExtensionAdapterError(
                "legacy evidence 不得绑定凭空出现的 layout receipt")
        return evidence
    if not isinstance(receipt, dict) \
            or receipt.get("layout_ledger_sha256") != contract["sha256"]:
        raise CppExtensionAdapterError(
            "layout evidence 缺 receipt.layout_ledger_sha256 外部锚")
    execution = receipt.get("layout_execution")
    validate_layout_execution(caseset, execution)
    observations = {row["case_id"]: row for row in execution["cases"]}
    evidence_by_id = {
        row.get("case_id"): row for row in evidence if isinstance(row, dict)
    }
    for case in contract["cases"]:
        cid = case["case_id"]
        row = evidence_by_id.get(cid)
        if not isinstance(row, dict):
            raise CppExtensionAdapterError(
                f"layout evidence 缺 layout-enabled case {cid!r}")
        row["layout_ledger_sha256"] = contract["sha256"]
        row["layout_observations"] = observations[cid]
    return evidence


def _bind_tensor_shape_attr_evidence(caseset, evidence, receipt):
    """把 atomic/cyclic case binding 与顶层 ledger 摘要原样镜像进 evidence。"""
    contract = validate_caseset_tensor_shape_attr_contract(caseset)
    reserved = {
        "tensor_shape_attr_bindings_sha256", "atomic_attr_ledger_sha256",
        "contract_bindings", "contract_bindings_sha256",
    }
    if contract is None:
        if isinstance(receipt, dict) and set(receipt) & {
                "tensor_shape_attr_bindings_sha256", "atomic_attr_ledger_sha256"}:
            raise CppExtensionAdapterError(
                "legacy receipt 不得绑定 tensor shape/attr ledger")
        for row in evidence:
            if isinstance(row, dict) and set(row) & reserved:
                raise CppExtensionAdapterError(
                    f"{row.get('case_id')}: legacy evidence 凭空声明 structure binding")
        return evidence
    if not isinstance(receipt, dict) \
            or receipt.get("tensor_shape_attr_bindings_sha256") \
            != contract["bindings_sha256"]:
        raise CppExtensionAdapterError(
            "structure evidence 缺 receipt.tensor_shape_attr_bindings_sha256")
    atomic_sha = contract["atomic_ledger_sha256"]
    if atomic_sha is not None and receipt.get("atomic_attr_ledger_sha256") != atomic_sha:
        raise CppExtensionAdapterError(
            "structure evidence 缺 receipt.atomic_attr_ledger_sha256")
    binding_by_id = {row["case_id"]: row["contract_bindings"]
                     for row in contract["bindings"]["cases"]}
    evidence_by_id = {row.get("case_id"): row for row in evidence
                      if isinstance(row, dict)}
    for cid, binding in binding_by_id.items():
        row = evidence_by_id.get(cid)
        if not isinstance(row, dict):
            raise CppExtensionAdapterError(
                f"structure evidence 缺 case {cid!r}")
        row["tensor_shape_attr_bindings_sha256"] = contract["bindings_sha256"]
        if atomic_sha is not None:
            row["atomic_attr_ledger_sha256"] = atomic_sha
        row["contract_bindings"] = binding
        row["contract_bindings_sha256"] = _canonical_sha(binding)
    for cid, row in evidence_by_id.items():
        if cid not in binding_by_id and set(row) & reserved:
            raise CppExtensionAdapterError(
                f"{cid}: 非 structure case 不得冒领 structure evidence")
    return evidence


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
    built = _build_execution_evidence(caseset, work, receipt)
    if isinstance(built, tuple):
        evidence, stochastic = built
    else:
        evidence, stochastic = built, None
    _bind_multi_input_evidence(caseset, evidence, receipt)
    _bind_tensor_shape_attr_evidence(caseset, evidence, receipt)
    _bind_layout_evidence(caseset, evidence, receipt)
    validate_invocation_accounting(
        _strict_json(plan), receipt.get("invocation"), evidence=evidence)
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
        _bind_multi_input_evidence(caseset, evidence, receipt)
        _bind_tensor_shape_attr_evidence(caseset, evidence, receipt)
        _bind_layout_evidence(caseset, evidence, receipt)
        validate_invocation_accounting(
            _strict_json(plan), receipt.get("invocation"), evidence=evidence)
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
    if stochastic is not None:
        envelope["stochastic_collection"] = receipt["stochastic_collection"]
        # 前提内容不进入裁决 envelope；这里只带正式统计证据。三级门另读独立前提文件。
        envelope["stochastic_formal_evidence"] = stochastic.get("formal")
        envelope["stochastic_evaluation"] = stochastic.get("evaluation")
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
    built = _build_execution_evidence(caseset, root, receipt)
    if isinstance(built, tuple):
        evidence, stochastic = built
    else:
        evidence, stochastic = built, None
    _bind_multi_input_evidence(caseset, evidence, receipt)
    _bind_tensor_shape_attr_evidence(caseset, evidence, receipt)
    _bind_layout_evidence(caseset, evidence, receipt)
    validate_invocation_accounting(
        _strict_json(plan), receipt.get("invocation"), evidence=evidence)
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
        "task_scope": "task2_only",
        "performance_collected": False,
        "source_provenance": source_provenance_summary(receipt),
        "cpp_extension_receipt": receipt,
        "evidence": evidence,
    }
    if stochastic is not None:
        envelope["stochastic_collection"] = receipt["stochastic_collection"]
        envelope["stochastic_formal_evidence"] = stochastic.get("formal")
        envelope["stochastic_evaluation"] = stochastic.get("evaluation")
    return envelope


CPP_EXTENSION_MODES = {"cpp_extension": run_cpp_extension}
