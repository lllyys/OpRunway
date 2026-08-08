#!/usr/bin/env python3
"""生成 multi-card v3 Task2 gate 的逐字节传递闭包清单。"""

import argparse
import hashlib
import json
import os


SCHEMA = "oprunway.multi_card_artifact_inventory"
VERSION = 4


_SINGLE_EQUIVALENCE_INPUTS = {
    "single_spec": "spec.json",
    "single_caseset": "caseset.json",
    "single_source_facts": "source_facts.json",
    "single_evidence": "evidence.json",
    "single_verdict": "verdict.json",
    "single_receipt": "work/cpp_extension_receipt.json",
}


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _row(role, path, *, relative_to=None):
    actual = os.path.realpath(path)
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError(f"{role}: 工件须为普通非symlink文件: {path}")
    shown = os.path.relpath(path, relative_to) if relative_to else actual
    if relative_to and (shown == ".." or shown.startswith("../") or os.path.isabs(shown)):
        raise ValueError(f"{role}: 工件逃逸 artifact_root")
    return {"role": role, "path": shown, "sha256": _sha(path),
            "bytes": os.path.getsize(path)}


def _closure(work, prefix, *, relative_to):
    work = os.path.realpath(work)
    rows = []
    for base, dirs, files in os.walk(work, followlinks=False):
        if any(os.path.islink(os.path.join(base, name)) for name in dirs):
            raise ValueError(f"{prefix}: work closure 含 symlink directory")
        for name in sorted(files):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, work)
            rows.append(_row(f"{prefix}.file:{rel}", path, relative_to=relative_to))
    return sorted(rows, key=lambda row: row["role"])


def _load(path):
    with open(path, encoding="utf-8") as src:
        return json.load(src)


def _external(receipt, prefix):
    vendor = receipt["vendor"]["library_path"]
    cann = receipt["runtime"]["cann"]["probe"]["defining_elf"]["path"]
    return [_row(prefix + ".vendor_elf", vendor),
            _row(prefix + ".cann_defining_elf", cann)]


def build(report_root, single_work):
    root = os.path.realpath(report_root)
    single_work = os.path.realpath(single_work)
    single_root = os.path.dirname(single_work)
    if os.path.basename(single_work) != "work":
        raise ValueError("single_work 须为 fresh single root 下的固定 work 目录")
    top = {
        "parent_spec": "spec.json", "parent_caseset": "parent-caseset.json",
        "staged_caseset": "caseset.json", "source_facts": "source_facts.json",
        "vendor_build_receipt": "vendor-build-receipt.json",
        "manifest_alias": "manifest.json", "multi_manifest": "multi_card_manifest.json",
        "merged_alias": "merged.json", "merged_receipt": "multi_card_merged_evidence.json",
        "assembled_evidence": "evidence.json", "verdict": "verdict.json",
        "equivalence": "equivalence.json", "gate_log": "gate-task2.log",
        "gate_rc": "gate-task2.rc",
    }
    artifacts = [_row(role, os.path.join(root, rel), relative_to=root)
                 for role, rel in top.items()]
    manifest = _load(os.path.join(root, "multi_card_manifest.json"))
    shards, external = [], []
    for shard in manifest["shards"]:
        sid = shard["shard_id"]
        formal = os.path.join(root, sid)
        smoke = formal + ".pre-smoke"
        formal_receipt = _load(os.path.join(formal, "cpp_extension_receipt.json"))
        smoke_receipt = _load(os.path.join(smoke, "cpp_extension_receipt.json"))
        shards.append({
            "shard_id": sid, "device_id": shard["device_id"],
            "formal_work": os.path.relpath(formal, root),
            "formal_artifacts": _closure(formal, sid + ".formal", relative_to=root),
            "smoke_work": os.path.relpath(smoke, root),
            "smoke_artifacts": _closure(smoke, sid + ".smoke", relative_to=root),
            "formal_receipt_sha256": _sha(os.path.join(formal, "cpp_extension_receipt.json")),
            "smoke_receipt_sha256": _sha(os.path.join(smoke, "cpp_extension_receipt.json")),
            "result_sha256": _sha(os.path.join(formal, "shard_result.json")),
            "execution_identity_sha256": _sha(os.path.join(formal, "device_identity.json")),
        })
        external.extend(_external(formal_receipt, sid + ".formal"))
        external.extend(_external(smoke_receipt, sid + ".smoke"))
    single_receipt = _load(os.path.join(single_work, "cpp_extension_receipt.json"))
    external.extend(_external(single_receipt, "single"))
    return {
        "schema": SCHEMA, "schema_version": VERSION,
        "artifact_root": root,
        "generated_from": {
            "task2_gate_replay_entry": "validate_acceptance_state._gate_multi_card_receipt",
            "equivalence_replay_entry": "multi_card_verdict_equivalence.build_equivalence",
            "inventory_scope": "precision_task2_transitive_closure",
        },
        "artifacts": sorted(artifacts, key=lambda row: row["role"]),
        "top_work_artifacts": _closure(
            os.path.join(root, "work"), "top_work", relative_to=root),
        "shards": shards, "single_root": single_root, "single_work": single_work,
        "single_equivalence_inputs": sorted(
            (_row(role, os.path.join(single_root, rel), relative_to=single_root)
             for role, rel in _SINGLE_EQUIVALENCE_INPUTS.items()),
            key=lambda row: row["role"]),
        "single_root_artifacts": _closure(
            single_root, "single_root", relative_to=single_root),
        "single_artifacts": _closure(single_work, "single", relative_to=single_work),
        "external_artifacts": external,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-root", required=True)
    parser.add_argument("--single-work", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    value = build(args.report_root, args.single_work)
    with open(args.out, "w", encoding="utf-8") as dst:
        json.dump(value, dst, ensure_ascii=False, indent=2); dst.write("\n")


if __name__ == "__main__":
    main()
