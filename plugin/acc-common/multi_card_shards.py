#!/usr/bin/env python3
"""确定性多卡 caseset 分片与按 case identity 汇总。

本模块只管理身份、分母和证据拼接，不执行 NPU、也不重判 precision/perf。
每个 shard 的实际执行必须在独立目录/进程完成，再把结果 envelope 交给 ``merge``。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil


class ShardContractError(RuntimeError):
    pass


SCHEMA = "oprunway.multi_card_shard_manifest"
RESULT_SCHEMA = "oprunway.multi_card_shard_result"
MERGED_SCHEMA = "oprunway.multi_card_merged_evidence"
VERSION = 2
PARTITIONS = frozenset({
    "case_order_round_robin_v1",
    "stochastic_formal_witness_affinity_v1",
    "atomic_profile_affinity_round_robin_v1",
})


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _case_ids(caseset):
    rows = caseset.get("cases") if isinstance(caseset, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ShardContractError("caseset.cases 须为非空列表")
    ids = [row.get("id") if isinstance(row, dict) else None for row in rows]
    if any(not isinstance(cid, str) or not cid for cid in ids) or len(set(ids)) != len(ids):
        raise ShardContractError("caseset case id 缺失或重复")
    return ids


def _common_smoke_case_ids(caseset):
    selected = (((caseset.get("perf_case_policy") or {}).get("selection") or {})
                .get("selected_case_ids") or [])
    rows = {row["id"]: row for row in caseset["cases"]}
    known = set(_case_ids(caseset))
    def eligible(row):
        tensors = [item for item in row.get("inputs", [])
                   if item.get("kind", "tensor") == "tensor"]
        return (tensors and all(isinstance(item.get("shape"), list)
                                and item["shape"] and 0 not in item["shape"]
                                for item in tensors)
                and (not isinstance(row.get("stochastic"), dict)
                     or row["stochastic"].get("purpose") == "structural_boundary_exact"))
    candidates = [cid for cid in selected if cid in known and eligible(rows[cid])]
    if not candidates:
        candidates = [row["id"] for row in caseset["cases"]
                      if eligible(row)]
    if not candidates:
        raise ShardContractError("无法从能力契约选择通用、非空的 common smoke case")
    # smoke 是环境/身份/输出写入探针，不是 atomic correctness profile。
    # 即使选中 case 带 atomic binding，也只投影这一条 case；subset_caseset 会
    # 同步把 ledger 收敛为该 case 的内部自洽见证，父 coverage authority 仍由
    # 正式 parent caseset 单独校验。
    return [candidates[0]]


def build_manifest(spec, caseset, source_facts, devices):
    case_ids = _case_ids(caseset)
    if (not isinstance(devices, list) or not devices
            or any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in devices)
            or len(set(devices)) != len(devices)):
        raise ShardContractError("devices 须为非空、互异的非负整数列表")
    buckets = [[] for _ in devices]
    partition = "case_order_round_robin_v1"
    stochastic_cases = [row for row in caseset.get("cases", [])
                        if isinstance(row.get("stochastic"), dict)]
    formal_ids = [row["id"] for row in stochastic_cases
                  if row["stochastic"].get("purpose") != "structural_boundary_exact"]
    formal_contracts = [{"case_id": row["id"], "stochastic": row["stochastic"]}
                        for row in stochastic_cases if row["id"] in set(formal_ids)]
    atomic = caseset.get("atomic_attr_ledger")
    cells = atomic.get("cells") if isinstance(atomic, dict) else None
    if formal_ids:
        formal = set(formal_ids); buckets[0].extend(formal_ids)
        structural = [cid for cid in case_ids if cid not in formal]
        for index, cid in enumerate(structural):
            buckets[index % len(devices)].append(cid)
        partition = "stochastic_formal_witness_affinity_v1"
    elif isinstance(cells, list) and cells:
        by_case = {row.get("case_id"): row.get("profile_id") for row in cells}
        groups, group_index = [], {}
        for cid in case_ids:
            key = ("profile", by_case[cid]) if cid in by_case else ("case", cid)
            if key not in group_index:
                group_index[key] = len(groups); groups.append([])
            groups[group_index[key]].append(cid)
        for index, group in enumerate(groups):
            buckets[index % len(devices)].extend(group)
        partition = "atomic_profile_affinity_round_robin_v1"
    else:
        for index, cid in enumerate(case_ids):
            buckets[index % len(devices)].append(cid)
    order = {cid: index for index, cid in enumerate(case_ids)}
    shards = []
    for index, device in enumerate(devices):
        if not buckets[index]:
            raise ShardContractError(
                "devices 多于可分配 affinity group/case，禁止生成空 shard")
        shards.append({
            "shard_id": f"shard-{index:03d}", "device_id": device,
            "case_ids": sorted(buckets[index], key=order.__getitem__),
        })
    value = {
        "schema": SCHEMA, "schema_version": VERSION,
        "bindings": {
            "spec_sha256": digest(spec), "caseset_sha256": digest(caseset),
            "provenance_sha256": digest(source_facts),
        },
        "case_order": case_ids, "shards": shards,
        "partition": partition,
        "common_smoke_case_ids": _common_smoke_case_ids(caseset),
        "affinity": {"partition": partition, "formal_case_ids": formal_ids,
                     "formal_contract_sha256": digest(formal_contracts)},
    }
    validate_manifest(value, spec, caseset, source_facts)
    return value


def validate_manifest(manifest, spec, caseset, source_facts):
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA \
            or manifest.get("schema_version") != VERSION:
        raise ShardContractError("shard manifest schema/version 非法")
    expected_bindings = {
        "spec_sha256": digest(spec), "caseset_sha256": digest(caseset),
        "provenance_sha256": digest(source_facts),
    }
    if manifest.get("bindings") != expected_bindings:
        raise ShardContractError("shard manifest spec/caseset/provenance 摘要漂移")
    expected = _case_ids(caseset)
    if manifest.get("case_order") != expected:
        raise ShardContractError("shard manifest case_order 与 caseset 漂移")
    if manifest.get("common_smoke_case_ids") != _common_smoke_case_ids(caseset):
        raise ShardContractError("common smoke case 身份与父 caseset 确定性规划漂移")
    shards = manifest.get("shards")
    if not isinstance(shards, list) or not shards:
        raise ShardContractError("shard manifest.shards 须为非空列表")
    shard_ids, devices, flattened = [], [], []
    if manifest.get("partition") not in PARTITIONS:
        raise ShardContractError("shard manifest.partition 不在严格词表")
    affinity = manifest.get("affinity")
    if (not isinstance(affinity, dict)
            or set(affinity) != {"partition", "formal_case_ids", "formal_contract_sha256"}
            or affinity.get("partition") != manifest.get("partition")
            or not isinstance(affinity.get("formal_case_ids"), list)
            or any(cid not in expected for cid in affinity["formal_case_ids"])
            or not isinstance(affinity.get("formal_contract_sha256"), str)
            or len(affinity["formal_contract_sha256"]) != 64):
        raise ShardContractError("shard manifest affinity 契约非法")
    stochastic_by_id = {row["id"]: row.get("stochastic") for row in caseset.get("cases", [])
                        if isinstance(row, dict) and isinstance(row.get("stochastic"), dict)}
    formal_contracts = [{"case_id": cid, "stochastic": stochastic_by_id.get(cid)}
                        for cid in affinity["formal_case_ids"]]
    if (any(row["stochastic"] is None for row in formal_contracts)
            or digest(formal_contracts) != affinity["formal_contract_sha256"]):
        raise ShardContractError("stochastic formal seed/offset contract 与父 caseset 漂移")
    for row in shards:
        if not isinstance(row, dict) or set(row) != {"shard_id", "device_id", "case_ids"}:
            raise ShardContractError("shard row 字段不精确")
        shard_ids.append(row["shard_id"]); devices.append(row["device_id"])
        if (not isinstance(row["shard_id"], str) or not row["shard_id"]
                or not isinstance(row["device_id"], int) or isinstance(row["device_id"], bool)
                or row["device_id"] < 0):
            raise ShardContractError("shard_id/device_id 非法")
        if not isinstance(row["case_ids"], list) or not row["case_ids"]:
            raise ShardContractError("shard.case_ids 须为非空列表")
        flattened.extend(row["case_ids"])
    if len(set(shard_ids)) != len(shard_ids) or len(set(devices)) != len(devices):
        raise ShardContractError("shard_id/device_id 重复")
    unknown = sorted(set(flattened) - set(expected))
    duplicates = sorted({cid for cid in flattened if flattened.count(cid) > 1})
    missing = sorted(set(expected) - set(flattened))
    if unknown or duplicates or missing or len(flattened) != len(expected):
        raise ShardContractError(
            f"shard 分母不闭合：unknown={unknown}, duplicate={duplicates}, missing={missing}")
    expected_buckets = [[] for _ in shards]
    if manifest["partition"] == "stochastic_formal_witness_affinity_v1":
        formal_ids = affinity["formal_case_ids"]
        expected_buckets[0].extend(formal_ids)
        structural = [cid for cid in expected if cid not in set(formal_ids)]
        for index, cid in enumerate(structural):
            expected_buckets[index % len(shards)].append(cid)
    elif manifest["partition"] == "atomic_profile_affinity_round_robin_v1":
        cells = (caseset.get("atomic_attr_ledger") or {}).get("cells") or []
        by_case = {row.get("case_id"): row.get("profile_id") for row in cells}
        groups, positions = [], {}
        for cid in expected:
            key = ("profile", by_case[cid]) if cid in by_case else ("case", cid)
            if key not in positions:
                positions[key] = len(groups); groups.append([])
            groups[positions[key]].append(cid)
        for index, group in enumerate(groups):
            expected_buckets[index % len(shards)].extend(group)
    else:
        for index, cid in enumerate(expected):
            expected_buckets[index % len(shards)].append(cid)
    if [row["case_ids"] for row in shards] != expected_buckets:
        raise ShardContractError("shard affinity/partition 与父 caseset 确定性规划漂移")
    return manifest


def subset_caseset(caseset, case_ids, *, manifest, shard, work_dir, require_empty=True,
                   _attach_common_smoke=True):
    """从父 caseset 生成显式 shard projection，并重算所有受影响分母账。"""
    if shard not in manifest.get("shards", []) or shard.get("case_ids") != case_ids:
        raise ShardContractError("subset shard/case_ids 与 manifest 漂移")
    absolute_work_dir = os.path.abspath(work_dir)
    initially_empty = (not os.path.lexists(absolute_work_dir)
                       or (os.path.isdir(absolute_work_dir)
                           and not os.listdir(absolute_work_dir)))
    if require_empty and not initially_empty:
        raise ShardContractError("shard projection 只能落入预先不存在或为空的独立 work-dir")
    wanted = set(case_ids)
    rows = [row for row in caseset["cases"] if row["id"] in wanted]
    if [row["id"] for row in rows] != [cid for cid in _case_ids(caseset) if cid in wanted]:
        raise ShardContractError("subset case order 漂移")
    value = json.loads(json.dumps(caseset))
    value["cases"] = rows
    total = len(rows)
    value.update({"work_dir": absolute_work_dir, "pool_max": total,
                  "emitted": total, "requested_target": total,
                  "shard_projection": {
                      "schema": "oprunway.caseset_shard_projection",
                      "schema_version": VERSION,
                      "parent_caseset_sha256": manifest["bindings"]["caseset_sha256"],
                      "manifest_sha256": digest(manifest),
                      "shard_id": shard["shard_id"], "device_id": shard["device_id"],
                      "scope": "precision_only", "case_ids": list(case_ids),
                      "work_dir_initially_empty": True,
                  }})
    value["dtype_tested"] = sorted({item.get("dtype") for row in rows
                                     for item in row.get("inputs", []) if item.get("dtype")})
    ledger = value.get("multi_input_ledger")
    if isinstance(ledger, dict):
        coverage = {"cases": total, "broadcasted": 0, "rank_mismatch": 0,
                    "rank0_tensor": 0, "host_scalar": 0,
                    "mixed_dtype": 0, "mixed_format": 0}
        profiles = set()
        for row in rows:
            contract = row.get("parameter_contract") or {}
            profiles.add(contract.get("profile_id"))
            relations = contract.get("relations") or {}
            shape = relations.get("shape") or {}; dtype = relations.get("dtype") or {}
            coverage["broadcasted"] += int(shape.get("broadcasted") is True)
            coverage["rank_mismatch"] += int(shape.get("rank_mismatch") is True)
            coverage["rank0_tensor"] += int(shape.get("rank0_tensor") is True)
            coverage["host_scalar"] += sum(
                1 for item in contract.get("inputs", []) if item.get("kind") == "host_scalar")
            coverage["mixed_dtype"] += int(dtype.get("mixed_dtype") is True)
            coverage["mixed_format"] += int(any(
                item.get("format") != contract.get("inputs", [{}])[0].get("format")
                for item in contract.get("inputs", [])[1:] if item.get("kind") == "tensor"))
        ledger["profiles"] = len(profiles - {None}); ledger["case_coverage"] = coverage
        ledger["profile_coverage"] = {k: v for k, v in coverage.items() if k != "cases"}
        ledger["required_coverage"] = {
            k: coverage[k] for k in ("broadcasted", "rank_mismatch", "rank0_tensor",
                                     "mixed_dtype", "mixed_format")}
    perf = value.get("perf_case_policy")
    if isinstance(perf, dict):
        selected = [cid for cid in (perf.get("selection") or {}).get("selected_case_ids", [])
                    if cid in wanted]
        excluded = [cid for cid in (perf.get("selection") or {}).get(
            "excluded_precision_case_ids", []) if cid in wanted]
        degenerate = [cid for cid in (perf.get("selection") or {}).get(
            "excluded_degenerate_case_ids", []) if cid in wanted]
        by_dtype = {}
        classes = {"small": 0, "large": 0}
        for row in rows:
            cid = row["id"]
            if cid in selected:
                dtype = (row.get("inputs") or [{}])[0].get("dtype")
                by_dtype[dtype] = by_dtype.get(dtype, 0) + 1
                cls = (row.get("perf_shape_classification") or {}).get("class")
                if cls in classes: classes[cls] += 1
        perf["counts"] = classes
        perf["selection"].update({
            "selected_case_ids": selected, "excluded_precision_case_ids": excluded,
            "excluded_degenerate_case_ids": degenerate, "selected_by_dtype": by_dtype,
            "selected_total": len(selected), "precision_total": total,
        })
    atomic = value.get("atomic_attr_ledger")
    if isinstance(atomic, dict):
        cells = [row for row in atomic.get("cells", []) if row.get("case_id") in wanted]
        profiles = {row.get("profile_id") for row in cells}
        excluded_cells = [row for row in atomic.get("excluded_cells", [])
                          if row.get("profile_id") in profiles]
        atomic.update({
            "profiles": len(profiles),
            "structure_denominator_total": len(cells) + len(excluded_cells),
            "planned": len(cells), "excluded": len(excluded_cells),
            "emitted": len(cells), "cells": cells, "excluded_cells": excluded_cells,
        })
        value["atomic_attr_ledger_sha256"] = digest(atomic)
    layout = value.get("layout_ledger")
    if isinstance(layout, dict):
        layout["cases"] = [row for row in layout.get("cases", [])
                           if row.get("case_id") in wanted]
        if layout["cases"]:
            value["layout_ledger_sha256"] = digest(layout)
        else:
            value.pop("layout_ledger", None); value.pop("layout_ledger_sha256", None)
    contract = value.get("stochastic_contract")
    if isinstance(contract, dict):
        bindings = [row.get("stochastic") for row in rows
                    if isinstance(row.get("stochastic"), dict)]
        formal = [row for row in bindings
                  if row.get("purpose") != "structural_boundary_exact"]
        if formal:
            ledger = value.get("stochastic_ledger")
            roles = {row.get("role") for row in bindings}
            ledger["roles"] = [role for role in ledger.get("roles", []) if role in roles]
            ledger["coverage_roles"] = [row for row in ledger.get("coverage_roles", [])
                                        if row.get("role") in roles]
        else:
            value.pop("stochastic_contract", None)
            value.pop("stochastic_ledger", None)
    # 每张卡消费同一份由父 caseset 确定性派生的最小 smoke template；实际目录
    # 仅在 runner 中实例化，不参与 common template 摘要。
    smoke_ids = manifest.get("common_smoke_case_ids")
    if smoke_ids and _attach_common_smoke:
        smoke_shard = {"shard_id": "common-smoke", "device_id": 0,
                       "case_ids": smoke_ids}
        smoke_manifest = copy.deepcopy(manifest)
        smoke_manifest["shards"] = [smoke_shard]
        template = subset_caseset(
            caseset, smoke_ids, manifest=smoke_manifest, shard=smoke_shard,
            work_dir="/__OPRUNWAY_COMMON_SMOKE_WORKDIR__", require_empty=False,
            _attach_common_smoke=False)
        template.pop("common_smoke_caseset_template", None)
        template["execution_scope"] = "environment_identity_smoke"
        atomic = template.get("atomic_attr_ledger")
        if isinstance(atomic, dict):
            # smoke 不冒领父 atomic coverage；只保留选中 probe 对应的实际 cell。
            cells = atomic.get("cells") or []
            atomic["profiles"] = len({row.get("profile_id") for row in cells})
            atomic["atomic_rows"] = len({
                (row.get("row_id"), row.get("row_sha256")) for row in cells})
            atomic["independent_combinations"] = len({
                (row.get("q_id"), row.get("q_sha256")) for row in cells})
            atomic["excluded_cells"] = []
            atomic["excluded"] = 0
            if atomic.get("schema_version") == 1:
                atomic["expected"] = len(cells)
                atomic.pop("excluded_cells", None)
                atomic.pop("excluded", None)
                atomic.pop("structure_denominator_total", None)
                atomic.pop("planned", None)
            else:
                atomic["structure_denominator_total"] = len(cells)
                atomic["planned"] = len(cells)
            atomic["emitted"] = len(cells)
            template["atomic_attr_ledger_sha256"] = digest(atomic)
        value["common_smoke_caseset_template"] = template
    return value


def validate_subset_caseset(caseset, manifest, shard):
    projection = caseset.get("shard_projection") if isinstance(caseset, dict) else None
    expected = {
        "schema": "oprunway.caseset_shard_projection", "schema_version": VERSION,
        "parent_caseset_sha256": manifest["bindings"]["caseset_sha256"],
        "manifest_sha256": digest(manifest), "shard_id": shard["shard_id"],
        "device_id": shard["device_id"], "scope": "precision_only",
        "case_ids": shard["case_ids"], "work_dir_initially_empty": True,
    }
    if projection != expected or _case_ids(caseset) != shard["case_ids"]:
        raise ShardContractError("shard caseset projection/分母与 manifest 漂移")
    total = len(shard["case_ids"])
    if any(caseset.get(key) != total for key in ("pool_max", "emitted", "requested_target")):
        raise ShardContractError("shard caseset 顶层分母未重算")
    return caseset


def materialize_case_assets(parent_caseset, projected_caseset, destination):
    """只复制 shard case 引用的相对路径顶层目录；拒绝逃逸与缺失。"""
    source_root = parent_caseset.get("work_dir")
    if not isinstance(source_root, str) or not os.path.isabs(source_root):
        raise ShardContractError("parent caseset.work_dir 须为绝对路径")
    destination = os.path.abspath(destination)
    roots = set()
    smoke_template = projected_caseset.get("common_smoke_caseset_template") or {}
    for case in projected_caseset["cases"] + list(smoke_template.get("cases") or []):
        paths = [item.get(key) for item in case.get("inputs", [])
                 for key in ("path", "base_storage_path")]
        paths += [case.get("expected", {}).get("golden_path")]
        for rel in paths:
            if rel is None:
                continue
            if not isinstance(rel, str) or not rel or os.path.isabs(rel):
                raise ShardContractError("case asset path 须为非空相对路径")
            normalized = os.path.normpath(rel)
            if normalized == ".." or normalized.startswith("../"):
                raise ShardContractError("case asset path 越界")
            roots.add(normalized.split(os.sep)[0])
    os.makedirs(destination, exist_ok=True)
    for name in sorted(roots):
        source = os.path.join(source_root, name); target = os.path.join(destination, name)
        if not os.path.isdir(source):
            raise ShardContractError(f"case asset 目录不存在: {source}")
        shutil.copytree(source, target, dirs_exist_ok=True)


def build_result(manifest, shard, shard_caseset, envelope, execution_identity):
    if shard not in manifest["shards"]:
        raise ShardContractError("result shard 不属于 manifest")
    validate_subset_caseset(shard_caseset, manifest, shard)
    evidence = envelope.get("evidence") if isinstance(envelope, dict) else None
    if not isinstance(evidence, list):
        raise ShardContractError("shard envelope.evidence 须为列表")
    ids = [row.get("case_id") for row in evidence if isinstance(row, dict)]
    if ids != shard["case_ids"]:
        raise ShardContractError(
            f"shard evidence case_ids 漂移：expected={shard['case_ids']}, actual={ids}")
    if not isinstance(execution_identity, dict) \
            or execution_identity.get("device_id") != shard["device_id"] \
            or execution_identity.get("current_device") != shard["device_id"]:
        raise ShardContractError("shard execution_identity actual device 与 manifest 漂移")
    receipt = envelope.get("cpp_extension_receipt")
    if (not isinstance(receipt, dict)
            or receipt.get("schema") != "oprunway.cpp_extension_receipt"
            or receipt.get("status") != "VERIFIED"):
        raise ShardContractError("shard 缺 VERIFIED 正式 cpp_extension_receipt")
    smoke = execution_identity.get("pre_execution_device_smoke")
    if (not isinstance(smoke, dict)
            or smoke.get("schema") != "oprunway.multi_card_pre_execution_device_smoke"
            or smoke.get("schema_version") != VERSION
            or smoke.get("device_id") != shard["device_id"]
            or smoke.get("current_device") != shard["device_id"]
            or smoke.get("work_dir_initially_absent") is not True
            or not isinstance(smoke.get("successful_output_case_ids"), list)
            or not smoke["successful_output_case_ids"]
            or smoke.get("common_smoke_case_ids") != manifest.get("common_smoke_case_ids")
            or smoke.get("common_smoke_caseset_sha256")
            != digest(shard_caseset.get("common_smoke_caseset_template"))
            or smoke.get("dut_library_sha256") != receipt.get("vendor", {}).get("library_sha256")
            or smoke.get("symbol_identity_sha256")
            != digest(receipt.get("vendor", {}).get("symbol_identity"))
            or smoke.get("soc") != receipt.get("runtime", {}).get("soc")
            or smoke.get("cann_version") != receipt.get("runtime", {}).get("cann_version")
            or smoke.get("source_provenance_sha256")
            != receipt.get("vendor", {}).get("build_receipt_sha256")):
        raise ShardContractError("shard 缺同卡执行前 device smoke receipt")
    output_receipt = {
        "schema": "oprunway.multi_card_shard_output_receipt",
        "schema_version": VERSION,
        "manifest_sha256": digest(manifest),
        "shard_id": shard["shard_id"],
        "case_ids": list(ids),
        "evidence_sha256": digest(evidence),
    }
    execution_plan = {
        "schema": "oprunway.multi_card_shard_execution_plan",
        "schema_version": VERSION,
        "manifest_sha256": digest(manifest),
        "shard_id": shard["shard_id"],
        "device_id": shard["device_id"],
        "case_ids": list(ids),
        "projection_sha256": digest(shard_caseset.get("shard_projection")),
    }
    return {
        "schema": RESULT_SCHEMA, "schema_version": VERSION,
        "manifest_sha256": digest(manifest), "shard_id": shard["shard_id"],
        "device_id": shard["device_id"], "case_ids": list(ids),
        "shard_caseset_sha256": digest(shard_caseset),
        "shard_caseset": shard_caseset,
        "receipt_bindings": {
            "parent_spec_sha256": manifest["bindings"]["spec_sha256"],
            "parent_caseset_sha256": manifest["bindings"]["caseset_sha256"],
            "source_facts_sha256": manifest["bindings"]["provenance_sha256"],
            "projection_sha256": digest(shard_caseset.get("shard_projection")),
            "execution_plan_sha256": digest(execution_plan),
            "cpp_extension_receipt_sha256": digest(receipt),
            "output_receipt_sha256": digest(output_receipt),
        },
        "receipts": {"execution_plan": execution_plan,
                     "cpp_extension": receipt, "output": output_receipt},
        "execution_identity": execution_identity,
        "evidence_sha256": digest(evidence), "evidence": evidence,
    }


def merge_results(manifest, results, spec, caseset, source_facts):
    validate_manifest(manifest, spec, caseset, source_facts)
    if not isinstance(results, list) or len(results) != len(manifest["shards"]):
        raise ShardContractError("shard result 数量与 manifest 不一致")
    by_id = {row["shard_id"]: row for row in manifest["shards"]}
    observed, evidence_by_case, common = set(), {}, None
    for result in results:
        if not isinstance(result, dict) or result.get("schema") != RESULT_SCHEMA \
                or result.get("schema_version") != VERSION \
                or result.get("manifest_sha256") != digest(manifest):
            raise ShardContractError("shard result schema/manifest 摘要漂移")
        sid = result.get("shard_id")
        if sid in observed or sid not in by_id:
            raise ShardContractError("shard result 重复或越界")
        observed.add(sid); shard = by_id[sid]
        if result.get("device_id") != shard["device_id"] \
                or result.get("case_ids") != shard["case_ids"]:
            raise ShardContractError("shard result device/case 身份漂移")
        if not isinstance(result.get("shard_caseset_sha256"), str) \
                or len(result["shard_caseset_sha256"]) != 64:
            raise ShardContractError("shard result 缺 caseset projection 摘要")
        projected = result.get("shard_caseset")
        if not isinstance(projected, dict) or digest(projected) != result["shard_caseset_sha256"]:
            raise ShardContractError("shard caseset projection 内容/摘要漂移")
        validate_subset_caseset(projected, manifest, shard)
        expected_projected = subset_caseset(
            caseset, shard["case_ids"], manifest=manifest, shard=shard,
            work_dir=projected.get("work_dir"), require_empty=False)
        if projected != expected_projected:
            raise ShardContractError("shard caseset 不是父 caseset 的确定性投影")
        bindings = result.get("receipt_bindings")
        expected_bindings = {
            "parent_spec_sha256": manifest["bindings"]["spec_sha256"],
            "parent_caseset_sha256": manifest["bindings"]["caseset_sha256"],
            "source_facts_sha256": manifest["bindings"]["provenance_sha256"],
            "projection_sha256": digest(projected.get("shard_projection")),
        }
        if not isinstance(bindings, dict) or any(bindings.get(k) != v for k, v in expected_bindings.items()):
            raise ShardContractError("shard receipt parent/projection/source 绑定漂移")
        for key in ("execution_plan_sha256", "cpp_extension_receipt_sha256",
                    "output_receipt_sha256"):
            if not isinstance(bindings.get(key), str) or len(bindings[key]) != 64:
                raise ShardContractError(f"shard receipt 缺 {key}")
        receipts = result.get("receipts")
        if (not isinstance(receipts, dict)
                or digest(receipts.get("execution_plan")) != bindings["execution_plan_sha256"]
                or digest(receipts.get("cpp_extension")) != bindings["cpp_extension_receipt_sha256"]
                or digest(receipts.get("output")) != bindings["output_receipt_sha256"]):
            raise ShardContractError("shard 正式 receipt 内容/摘要漂移")
        cpp_receipt = receipts.get("cpp_extension")
        if (not isinstance(cpp_receipt, dict)
                or cpp_receipt.get("schema") != "oprunway.cpp_extension_receipt"
                or cpp_receipt.get("status") != "VERIFIED"):
            raise ShardContractError("shard cpp_extension receipt 非 VERIFIED 正式 schema")
        evidence = result.get("evidence")
        if digest(evidence) != result.get("evidence_sha256"):
            raise ShardContractError("shard evidence 摘要漂移")
        identity = result.get("execution_identity")
        if not isinstance(evidence, list) or [row.get("case_id") for row in evidence
                                              if isinstance(row, dict)] != shard["case_ids"]:
            raise ShardContractError("shard evidence case identity 与 manifest 漂移")
        if (not isinstance(identity, dict)
                or identity.get("device_id") != shard["device_id"]
                or identity.get("current_device") != shard["device_id"]):
            raise ShardContractError("shard result execution identity/device 漂移")
        stable = {k: identity.get(k) for k in (
            "soc", "cann_version", "dut_library_sha256",
            "dut_symbol_identity_sha256", "source_provenance_sha256")}
        if any(not isinstance(v, str) or not v for v in stable.values()):
            raise ShardContractError("shard execution identity 缺稳定字段")
        if common is None: common = stable
        elif stable != common: raise ShardContractError("shard execution identity 跨卡漂移")
        for row in evidence:
            cid = row.get("case_id")
            if cid in evidence_by_case:
                raise ShardContractError(f"重复 evidence case_id={cid!r}")
            evidence_by_case[cid] = row
    missing = [cid for cid in manifest["case_order"] if cid not in evidence_by_case]
    if missing or set(evidence_by_case) != set(manifest["case_order"]):
        raise ShardContractError(f"merged evidence 分母不闭合：missing={missing}")
    merged = [evidence_by_case[cid] for cid in manifest["case_order"]]
    return {
        "schema": MERGED_SCHEMA, "schema_version": VERSION,
        "manifest_sha256": digest(manifest), "bindings": manifest["bindings"],
        "execution_identity_common": common,
        "shard_results_sha256": [digest(row) for row in sorted(results, key=lambda x: x["shard_id"])],
        "shard_results": sorted(results, key=lambda x: x["shard_id"]),
        "evidence": merged, "evidence_sha256": digest(merged),
    }


def assemble_envelope(manifest, merged, shard_envelopes):
    """从每 shard 的正式 envelope 组装多卡 envelope；不复制任一 template receipt。"""
    results = merged.get("shard_results") if isinstance(merged, dict) else None
    if not isinstance(results, list) or len(shard_envelopes) != len(results):
        raise ShardContractError("assemble shard envelope/result 数量不一致")
    by_id = {row["shard_id"]: row for row in results}
    common_keys = ("evidence_grade", "op", "performance_collected", "repo_mode",
                   "runner_form", "source_provenance", "task_scope")
    common = None; stochastic = {}; seen = set()
    formal_ids = set((manifest.get("affinity") or {}).get("formal_case_ids") or [])
    formal_owner = next((row["shard_id"] for row in manifest["shards"]
                         if formal_ids & set(row["case_ids"])), None)
    for envelope in shard_envelopes:
        if not isinstance(envelope, dict):
            raise ShardContractError("shard envelope 非 object")
        evidence = envelope.get("evidence")
        ids = [row.get("case_id") for row in evidence if isinstance(row, dict)] \
            if isinstance(evidence, list) else None
        owners = [row for row in manifest["shards"] if row["case_ids"] == ids]
        if len(owners) != 1 or owners[0]["shard_id"] in seen:
            raise ShardContractError("shard envelope case projection 重复或越界")
        sid = owners[0]["shard_id"]; seen.add(sid); result = by_id[sid]
        if (digest(evidence) != result.get("evidence_sha256")
                or digest(envelope.get("cpp_extension_receipt"))
                != result.get("receipt_bindings", {}).get("cpp_extension_receipt_sha256")):
            raise ShardContractError("shard envelope evidence/cpp receipt 与 result 漂移")
        current = {key: envelope.get(key) for key in common_keys}
        if common is None: common = current
        elif current != common: raise ShardContractError("shard envelope 公共语义字段漂移")
        extra = {key: value for key, value in envelope.items()
                 if key.startswith("stochastic_")}
        if extra:
            if sid != formal_owner or stochastic:
                raise ShardContractError("stochastic formal envelope 必须且只能来自 formal affinity shard")
            stochastic = extra
    if seen != set(by_id):
        raise ShardContractError("shard envelope 分母未闭合")
    return {**(common or {}), **stochastic, "evidence": merged["evidence"],
            "multi_card_receipt": merged}


def materialize_merged_assets(manifest, shard_envelope_paths, destination):
    """把每 shard 自己产生的 case golden/output 投影到正式 merged 目录；拒绝覆盖。"""
    destination = os.path.abspath(destination)
    for path in shard_envelope_paths:
        envelope = _load(path)
        ids = [row.get("case_id") for row in envelope.get("evidence", [])
               if isinstance(row, dict)]
        owners = [row for row in manifest["shards"] if row["case_ids"] == ids]
        if len(owners) != 1:
            raise ShardContractError("materialize envelope case projection 越界")
        source = os.path.dirname(os.path.abspath(path))
        for cid in ids:
            for rel in (cid, os.path.join("cpp_extension_out", cid)):
                src = os.path.join(source, rel); dst = os.path.join(destination, rel)
                if not os.path.isdir(src):
                    continue
                if os.path.lexists(dst):
                    raise ShardContractError(f"merged asset 目标已存在，禁止覆盖: {rel}")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copytree(src, dst)


def _load(path):
    with open(path, encoding="utf-8") as src: return json.load(src)


def _dump(value, path):
    with open(path, "w", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2); out.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    plan = sub.add_parser("plan")
    for name in ("spec", "caseset", "source_facts", "out"): plan.add_argument("--"+name.replace("_","-"), required=True)
    plan.add_argument("--devices", required=True)
    check = sub.add_parser("check")
    for name in ("manifest", "spec", "caseset", "source_facts"): check.add_argument("--"+name.replace("_","-"), required=True)
    merge = sub.add_parser("merge")
    merge.add_argument("--manifest", required=True); merge.add_argument("--results", nargs="+", required=True); merge.add_argument("--out", required=True)
    for name in ("spec", "caseset", "source_facts"):
        merge.add_argument("--"+name.replace("_", "-"), required=True)
    project = sub.add_parser("project")
    project.add_argument("--manifest", required=True); project.add_argument("--caseset", required=True)
    project.add_argument("--shard-id", required=True); project.add_argument("--work-dir", required=True)
    project.add_argument("--out", required=True)
    project.add_argument("--materialize-assets", action="store_true")
    assemble = sub.add_parser("assemble")
    assemble.add_argument("--manifest", required=True); assemble.add_argument("--merged", required=True)
    assemble.add_argument("--envelopes", nargs="+", required=True); assemble.add_argument("--out", required=True)
    assemble.add_argument("--materialize-assets", action="store_true")
    ns = ap.parse_args(argv)
    if ns.cmd == "plan":
        value=build_manifest(_load(ns.spec),_load(ns.caseset),_load(ns.source_facts),[int(x) for x in ns.devices.split(",")]); _dump(value,ns.out)
    elif ns.cmd == "check": validate_manifest(_load(ns.manifest),_load(ns.spec),_load(ns.caseset),_load(ns.source_facts))
    elif ns.cmd == "project":
        manifest, caseset = _load(ns.manifest), _load(ns.caseset)
        rows = [row for row in manifest["shards"] if row["shard_id"] == ns.shard_id]
        if len(rows) != 1: raise ShardContractError("shard-id 不唯一或不存在")
        shard = rows[0]
        projected = subset_caseset(caseset, shard["case_ids"], manifest=manifest,
                                   shard=shard, work_dir=ns.work_dir)
        if ns.materialize_assets:
            materialize_case_assets(caseset, projected, ns.work_dir)
        _dump(projected, ns.out)
    elif ns.cmd == "merge":
        merged = merge_results(_load(ns.manifest), [_load(p) for p in ns.results],
                               _load(ns.spec), _load(ns.caseset), _load(ns.source_facts))
        _dump(merged, ns.out)
    else:
        _dump(assemble_envelope(_load(ns.manifest), _load(ns.merged),
                                [_load(p) for p in ns.envelopes]), ns.out)
        if ns.materialize_assets:
            materialize_merged_assets(_load(ns.manifest), ns.envelopes,
                                      os.path.join(os.path.dirname(os.path.abspath(ns.out)), "work"))
    return 0


if __name__ == "__main__": raise SystemExit(main())
