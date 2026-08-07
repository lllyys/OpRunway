#!/usr/bin/env python3
"""机校多卡 precision verdict 与单卡 verdict 的逐 case 等价性。"""

import argparse
import hashlib
import json

import multi_card_shards
import cpp_extension_adapter


def _read(path):
    with open(path, "rb") as src:
        raw = src.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _projection(verdict):
    rows = verdict.get("per_case")
    if not isinstance(rows, list):
        raise ValueError("verdict.per_case 须为列表")
    result = {}
    for row in rows:
        cid = row.get("case_id")
        if not isinstance(cid, str) or not cid or cid in result:
            raise ValueError("verdict case_id 缺失或重复")
        # precision verdict 的逐 case 完整行就是等价对象；只挑四个字段会漏掉
        # stochastic predicate、dtype metric、执行状态等决定性信息。
        result[cid] = row
    summary = {key: verdict.get(key) for key in (
        "status", "overall", "summary", "precision_status", "counts") if key in verdict}
    return result, summary


def build_equivalence(multi, single, manifest, spec, caseset, source_facts, merged,
                      single_evidence, single_receipt,
                      single_work,
                      *, multi_sha, single_sha, manifest_sha):
    multi_card_shards.validate_manifest(manifest, spec, caseset, source_facts)
    recomputed = multi_card_shards.merge_results(
        manifest, merged.get("shard_results"), spec, caseset, source_facts)
    if merged != recomputed:
        raise ValueError("merged receipt 不是 parent inputs/results 的确定性重算结果")
    validated_single_receipt = cpp_extension_adapter.validate_receipt(
        single_work, caseset)
    if validated_single_receipt != single_receipt:
        raise ValueError("single receipt 未绑定正式单卡 work 落盘工件")
    if single_evidence.get("cpp_extension_receipt") != validated_single_receipt:
        raise ValueError("single evidence 未逐字绑定 single cpp_extension receipt")
    single_receipt_sha = multi_card_shards.digest(single_receipt)
    if any(row.get("cpp_extension_receipt_sha256") != single_receipt_sha
           for row in single_evidence.get("evidence", [])):
        raise ValueError("single evidence per-case receipt 摘要漂移")
    actual, actual_summary = _projection(multi)
    expected, expected_summary = _projection(single)
    partition = manifest.get("partition")
    has_stochastic_contract = isinstance(caseset.get("stochastic_contract"), dict)
    controlled = has_stochastic_contract
    if controlled:
        if partition != "stochastic_formal_witness_affinity_v1":
            raise ValueError("controlled stochastic caseset 未使用受信 formal affinity partition")
        shards = manifest.get("shards") or []
        formal = (manifest.get("affinity") or {}).get("formal_case_ids") or []
        # 不按算子名分支：由 stochastic affinity capability 驱动；所有 formal witness
        # 必须落同一 shard，跨卡只拼 structural evidence，禁止投票。
        owners = {row.get("shard_id") for row in shards
                  if any(cid in row.get("case_ids", []) for cid in formal)}
        formal_single_shard = bool(formal) and len(owners) == 1
        cases_by_id = {row.get("id"): row for row in caseset.get("cases", [])
                       if isinstance(row, dict)}
        evidence_by_id = {row.get("case_id"): row for row in merged.get("evidence", [])
                          if isinstance(row, dict)}
        single_by_id = {row.get("case_id"): row
                        for row in single_evidence.get("evidence", [])
                        if isinstance(row, dict)}
        formal_contract_equal = all(
            isinstance(cases_by_id.get(cid, {}).get("stochastic"), dict)
            and evidence_by_id.get(cid, {}).get("stochastic")
            == cases_by_id[cid]["stochastic"]
            and single_by_id.get(cid, {}).get("stochastic")
            == cases_by_id[cid]["stochastic"]
            for cid in formal)
        formal_owner_results = [row for row in merged.get("shard_results", [])
                                if set(formal) & set(row.get("case_ids") or [])]
        multi_collection_complete = (
            len(formal_owner_results) == 1
            and formal_owner_results[0].get("receipts", {}).get(
                "cpp_extension", {}).get("stochastic_collection", {}).get("status")
            == "complete")
        single_collection_complete = (
            single_receipt.get("stochastic_collection", {}).get("status") == "complete")
        multi_collection_device = (formal_owner_results[0].get("receipts", {}).get(
            "cpp_extension", {}).get("stochastic_collection", {}).get("device")
            if len(formal_owner_results) == 1 else None)
        single_collection_device = single_receipt.get(
            "stochastic_collection", {}).get("device")
        owner_identity = (formal_owner_results[0].get("execution_identity")
                          if len(formal_owner_results) == 1 else None)
        collection_device_equal = (
            isinstance(multi_collection_device, dict)
            and multi_collection_device == single_collection_device
            and isinstance(owner_identity, dict)
            and owner_identity.get("device_id") == multi_collection_device.get("index")
            and owner_identity.get("current_device") == multi_collection_device.get("index"))
    else:
        formal_single_shard = True
        formal_contract_equal = True
        multi_collection_complete = single_collection_complete = True
        multi_collection_device = single_collection_device = None
        collection_device_equal = True
    vendor = single_receipt.get("vendor") if isinstance(single_receipt, dict) else {}
    runtime = single_receipt.get("runtime") if isinstance(single_receipt, dict) else {}
    single_identity = {
        "soc": runtime.get("soc"), "cann_version": runtime.get("cann_version"),
        "dut_library_sha256": vendor.get("library_sha256"),
        "dut_symbol_identity_sha256": multi_card_shards.digest(vendor.get("symbol_identity")),
        "source_provenance_sha256": vendor.get("build_receipt_sha256"),
    }
    execution_identity_equal = single_identity == merged.get("execution_identity_common")
    result = {
        "schema": "oprunway.multi_card_precision_equivalence", "schema_version": 1,
        "scope": "precision_only", "manifest_sha256": manifest_sha,
        "multi_verdict_sha256": multi_sha, "single_verdict_sha256": single_sha,
        "verdict_sha256_equal": multi_sha == single_sha,
        "complete_verdict_equal": multi == single,
        "expected_case_order": manifest.get("case_order"),
        "case_set_equal": set(actual) == set(expected),
        "case_order_equal": list(actual) == manifest.get("case_order"),
        "per_case_verdict_equal": actual == expected,
        "summary_equal": actual_summary == expected_summary,
        "comparison_mode": ("controlled_stochastic_no_vote" if controlled
                            else "complete_precision_projection"),
        "formal_single_shard": formal_single_shard,
        "formal_seed_offset_contract_equal": formal_contract_equal,
        "multi_formal_collection_complete": multi_collection_complete,
        "single_formal_collection_complete": single_collection_complete,
        "multi_formal_collection_device": multi_collection_device,
        "single_formal_collection_device": single_collection_device,
        "formal_collection_device_equal": collection_device_equal,
        "case_count": len(actual),
        "parent_bindings": manifest.get("bindings"),
        "execution_identity_common": merged.get("execution_identity_common"),
        "single_execution_identity": single_identity,
        "single_execution_identity_equal": execution_identity_equal,
    }
    result["passed"] = all(result[key] is True for key in
                           ("verdict_sha256_equal", "complete_verdict_equal",
                            "case_set_equal", "case_order_equal", "per_case_verdict_equal",
                            "summary_equal", "formal_single_shard"))
    result["passed"] = result["passed"] and result["formal_seed_offset_contract_equal"] is True
    result["passed"] = result["passed"] and result["single_execution_identity_equal"] is True
    result["passed"] = (result["passed"]
                        and result["multi_formal_collection_complete"] is True
                        and result["single_formal_collection_complete"] is True
                        and result["formal_collection_device_equal"] is True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--multi", required=True); parser.add_argument("--single", required=True)
    parser.add_argument("--manifest", required=True); parser.add_argument("--out", required=True)
    parser.add_argument("--spec", required=True); parser.add_argument("--caseset", required=True)
    parser.add_argument("--source-facts", required=True); parser.add_argument("--merged", required=True)
    parser.add_argument("--single-evidence", required=True)
    parser.add_argument("--single-receipt", required=True)
    parser.add_argument("--single-work", required=True)
    args = parser.parse_args()
    multi, multi_sha = _read(args.multi); single, single_sha = _read(args.single)
    manifest, manifest_sha = _read(args.manifest)
    spec, _ = _read(args.spec); caseset, _ = _read(args.caseset)
    source, _ = _read(args.source_facts); merged, _ = _read(args.merged)
    single_evidence, _ = _read(args.single_evidence)
    single_receipt, _ = _read(args.single_receipt)
    result = build_equivalence(multi, single, manifest, spec, caseset, source, merged,
                               single_evidence, single_receipt,
                               args.single_work,
                               multi_sha=multi_sha,
                               single_sha=single_sha, manifest_sha=manifest_sha)
    with open(args.out, "w", encoding="utf-8") as dst:
        json.dump(result, dst, ensure_ascii=False, indent=2); dst.write("\n")
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
