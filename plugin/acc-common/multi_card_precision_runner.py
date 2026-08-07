#!/usr/bin/env python3
"""在独立进程/目录执行一个 precision-only shard，并产绑定结果。"""

import argparse
import copy
import json
import os
import shutil
import sys

import cpp_extension_adapter as adapter
import multi_card_shards as shards


def _read(path):
    with open(path, encoding="utf-8") as src:
        return json.load(src)


def _run_pre_execution_smoke(spec, caseset, work, shard, torch):
    """在独立、预先不存在的目录先跑同一 projection，证明本卡 DUT 可写输出。"""
    smoke_work = work + ".pre-smoke"
    if os.path.lexists(smoke_work):
        raise shards.ShardContractError("pre-smoke work-dir 必须预先不存在")
    shutil.copytree(work, smoke_work)
    smoke_caseset = copy.deepcopy(caseset.get("common_smoke_caseset_template"))
    if not isinstance(smoke_caseset, dict):
        raise shards.ShardContractError("projection 缺父 manifest 绑定的 common smoke template")
    smoke_caseset["work_dir"] = smoke_work
    smoke_caseset.pop("common_smoke_caseset_template", None)
    with open(os.path.join(smoke_work, "caseset.json"), "w", encoding="utf-8") as dst:
        json.dump(smoke_caseset, dst, ensure_ascii=False, indent=2)
    adapter.prepare(spec, smoke_caseset, smoke_work)
    envelope = adapter.run_cpp_extension_precision_only(smoke_caseset, smoke_work)
    receipt = envelope.get("cpp_extension_receipt")
    rows = envelope.get("evidence")
    successful = [row for row in rows if isinstance(row, dict)
                  and row.get("status") == "ok"
                  and row.get("output_written_check") == "passed"] \
        if isinstance(rows, list) else []
    if not successful or not isinstance(receipt, dict):
        raise shards.ShardContractError(
            "pre-smoke 未证明同卡 DUT 至少一个 case 正常写出 output")
    with open(os.path.join(smoke_work, "evidence.json"), "w", encoding="utf-8") as dst:
        json.dump(envelope, dst, ensure_ascii=False, indent=2)
    vendor = receipt.get("vendor") or {}
    runtime = receipt.get("runtime") or {}
    return {
        "schema": "oprunway.multi_card_pre_execution_device_smoke",
        "schema_version": shards.VERSION,
        "device_id": shard["device_id"],
        "current_device": int(torch.npu.current_device()),
        "device_name": str(torch.npu.get_device_name(shard["device_id"])),
        "work_dir": smoke_work,
        "work_dir_initially_absent": True,
        "successful_output_case_ids": [row["case_id"] for row in successful],
        "common_smoke_case_ids": list(shards._case_ids(smoke_caseset)),
        "common_smoke_caseset_sha256": shards.digest(
            caseset["common_smoke_caseset_template"]),
        "cpp_extension_receipt_sha256": shards.digest(receipt),
        "dut_library_sha256": vendor.get("library_sha256"),
        "symbol_identity_sha256": shards.digest(vendor.get("symbol_identity")),
        "soc": runtime.get("soc"),
        "cann_version": runtime.get("cann_version"),
        "source_provenance_sha256": vendor.get("build_receipt_sha256"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--caseset", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--shard-id", required=True)
    parser.add_argument("--work-dir", required=True)
    args = parser.parse_args()
    spec, caseset, manifest = map(_read, (args.spec, args.caseset, args.manifest))
    rows = [row for row in manifest.get("shards", []) if row.get("shard_id") == args.shard_id]
    if len(rows) != 1:
        raise shards.ShardContractError("shard-id 不唯一或不存在")
    shard = rows[0]
    actual_work = os.path.realpath(os.path.abspath(args.work_dir))
    declared_work = caseset.get("work_dir")
    if (not isinstance(declared_work, str) or not os.path.isabs(declared_work)
            or os.path.realpath(declared_work) != actual_work):
        raise shards.ShardContractError(
            "runner --work-dir 与 projection caseset.work_dir 未逐字/realpath 绑定")
    configured = os.environ.get("OPRUNWAY_CPP_EXTENSION_DEVICE")
    if configured != str(shard["device_id"]):
        raise shards.ShardContractError("进程 device 环境与 shard manifest 漂移")
    shards.validate_subset_caseset(caseset, manifest, shard)
    import torch
    import torch_npu  # noqa: F401
    torch.npu.set_device(shard["device_id"])
    pre_smoke = _run_pre_execution_smoke(spec, caseset, actual_work, shard, torch)
    if pre_smoke["current_device"] != shard["device_id"]:
        raise shards.ShardContractError("执行前 device smoke 与 shard manifest 漂移")
    adapter.prepare(spec, caseset, args.work_dir)
    envelope = adapter.run_cpp_extension_precision_only(caseset, args.work_dir)
    receipt = envelope["cpp_extension_receipt"]
    if (pre_smoke["dut_library_sha256"] != receipt["vendor"]["library_sha256"]
            or pre_smoke["symbol_identity_sha256"]
            != shards.digest(receipt["vendor"]["symbol_identity"])
            or pre_smoke["soc"] != receipt["runtime"]["soc"]
            or pre_smoke["cann_version"] != receipt["runtime"]["cann_version"]
            or pre_smoke["source_provenance_sha256"]
            != receipt["vendor"]["build_receipt_sha256"]):
        raise shards.ShardContractError(
            "pre-smoke 与正式 shard 的 device/SoC/CANN/DUT/source/双符号身份漂移")
    identity = {
        "device_id": shard["device_id"],
        "current_device": int(torch.npu.current_device()),
        "device_name": str(torch.npu.get_device_name(shard["device_id"])),
        "soc": receipt["runtime"]["soc"],
        "cann_version": receipt["runtime"]["cann_version"],
        "dut_library_sha256": receipt["vendor"]["library_sha256"],
        "dut_symbol_identity_sha256": shards.digest(receipt["vendor"]["symbol_identity"]),
        "source_provenance_sha256": receipt["vendor"]["build_receipt_sha256"],
        "pre_execution_device_smoke": pre_smoke,
    }
    if identity["current_device"] != shard["device_id"]:
        raise shards.ShardContractError("torch.npu current_device 与 shard manifest 漂移")
    result = shards.build_result(manifest, shard, caseset, envelope, identity)
    os.makedirs(args.work_dir, exist_ok=True)
    for name, value in (("evidence.json", envelope), ("device_identity.json", identity),
                        ("shard_result.json", result)):
        with open(os.path.join(args.work_dir, name), "w", encoding="utf-8") as dst:
            json.dump(value, dst, ensure_ascii=False, indent=2)
    print(json.dumps({"shard_id": args.shard_id, "device_id": shard["device_id"],
                      "evidence": len(envelope["evidence"]),
                      "shard_result_sha256": shards.digest(result)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (shards.ShardContractError, KeyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
