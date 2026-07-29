#!/usr/bin/env python3
"""严格合并同口径 perf_msprof 重采结果。

本脚本只补齐采集缺口，不做性能裁决：

* primary 保留完整 case 顺序；
* 默认 retry 只能是 primary 的子集；显式给出 required_case_ids 时，允许补入该清单中的新增 case；
* op/scope/warmup/repeat/device/collection/baseline_source 必须逐项相同；
* 只允许用双边均为有效 kernel-only NPU 计时的 retry 记录替换 primary 中的无效记录；
* 已经双边有效的 primary 记录禁止被重采覆盖，避免择优挑数；
* 输出记录输入文件 sha256、实际替换 case 与来源，便于审计。
"""

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path


class PerfRetryMergeError(ValueError):
    """重采文档不满足同口径、子集或替换纪律。"""


_CONTRACT_FIELDS = (
    "op",
    "scope",
    "warmup",
    "repeat",
    "device",
    "side_timeout_s",
    "collection",
    "baseline_source",
)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _record_map(doc, label):
    records = doc.get("records")
    if not isinstance(records, list):
        raise PerfRetryMergeError(f"{label}.records 必须是数组")
    out = {}
    for pos, rec in enumerate(records):
        if not isinstance(rec, dict) or not isinstance(rec.get("case_id"), str) or not rec["case_id"]:
            raise PerfRetryMergeError(f"{label}.records[{pos}] 缺合法 case_id")
        cid = rec["case_id"]
        if cid in out:
            raise PerfRetryMergeError(f"{label}.records 出现重复 case_id={cid!r}")
        out[cid] = rec
    return out


def _valid_side(side):
    if not isinstance(side, dict):
        return False
    value = side.get("us")
    return (
        side.get("behavior") == "npu"
        and side.get("scope") == "kernel_only"
        and side.get("execution_path") == "device_kernel"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def valid_pair(rec):
    """记录是否包含可比较的双边 kernel-only NPU 计时。"""
    return (
        isinstance(rec, dict)
        and rec.get("custom_timed") is True
        and rec.get("baseline_timed") is True
        and rec.get("comparability") == "fair"
        and _valid_side(rec.get("custom"))
        and _valid_side(rec.get("baseline"))
    )


def _check_contract(primary, retry, label):
    for field in _CONTRACT_FIELDS:
        if retry.get(field) != primary.get(field):
            raise PerfRetryMergeError(
                f"{label}.{field} 与 primary 不一致："
                f"{retry.get(field)!r} != {primary.get(field)!r}"
            )


def merge_retry_docs(primary, retries, *, primary_source=None, retry_sources=None,
                     required_case_ids=None):
    """返回合并后的新文档；不修改输入对象。"""
    if not isinstance(primary, dict):
        raise PerfRetryMergeError("primary 必须是对象")
    primary_map = _record_map(primary, "primary")
    primary_order = [rec["case_id"] for rec in primary["records"]]
    order = list(required_case_ids) if required_case_ids is not None else primary_order
    if (not order or any(not isinstance(cid, str) or not cid for cid in order)
            or len(set(order)) != len(order)):
        raise PerfRetryMergeError("required_case_ids 必须是非空、无重复的 case_id 数组")
    extra_primary = sorted(set(primary_map) - set(order))
    if extra_primary:
        raise PerfRetryMergeError(
            f"primary 含 required_case_ids 不存在的 case：{extra_primary}")
    current = {cid: copy.deepcopy(rec) for cid, rec in primary_map.items()}
    retry_sources = list(retry_sources or [None] * len(retries))
    if len(retry_sources) != len(retries):
        raise PerfRetryMergeError("retry_sources 数量必须与 retries 相同")

    replacements, additions = [], []
    retry_audit = []
    for index, (retry, source) in enumerate(zip(retries, retry_sources), start=1):
        label = f"retry[{index}]"
        if not isinstance(retry, dict):
            raise PerfRetryMergeError(f"{label} 必须是对象")
        _check_contract(primary, retry, label)
        retry_map = _record_map(retry, label)
        unknown = sorted(set(retry_map) - set(order))
        if unknown:
            scope = "primary" if required_case_ids is None else "required_case_ids"
            raise PerfRetryMergeError(f"{label} 含 {scope} 不存在的 case：{unknown}")

        usable, added = [], []
        for cid, rec in retry_map.items():
            if cid not in current:
                current[cid] = copy.deepcopy(rec)
                additions.append({"case_id": cid, "retry_index": index})
                added.append(cid)
                if valid_pair(rec):
                    usable.append(cid)
                continue
            if valid_pair(current[cid]):
                raise PerfRetryMergeError(
                    f"{label} 试图覆盖已双边有效的 case_id={cid!r}；"
                    "禁止用重采择优替换有效数据"
                )
            if not valid_pair(rec):
                continue
            current[cid] = copy.deepcopy(rec)
            usable.append(cid)
            replacements.append({"case_id": cid, "retry_index": index})
        retry_audit.append(
            {
                "index": index,
                "source": Path(source).name if source else None,
                "sha256": _sha256(source) if source else None,
                "record_count": len(retry_map),
                "used_case_ids": usable,
                "added_case_ids": added,
            }
        )

    merged = copy.deepcopy(primary)
    merged["records"] = [current[cid] for cid in order if cid in current]
    missing = [cid for cid in order if cid not in current]
    merged["collection_checkpoint"] = {
        "complete": not missing,
        "completed": len(merged["records"]),
        "planned": len(order),
        "planned_case_ids": order,
    }
    merged["retry_merge"] = {
        "policy": "same_contract_valid_pair_only_no_valid_overwrite",
        "primary": {
            "source": Path(primary_source).name if primary_source else None,
            "sha256": _sha256(primary_source) if primary_source else None,
        },
        "retries": retry_audit,
        "replacements": replacements,
        "additions": additions,
        "missing_case_ids": missing,
        "remaining_invalid_case_ids": [
            cid for cid in order if cid not in current or not valid_pair(current[cid])],
    }
    return merged


def main(argv=None):
    parser = argparse.ArgumentParser(description="严格合并 perf_msprof 主采集与同口径子集重采")
    parser.add_argument("primary", help="完整主采集 perf_collect.json")
    parser.add_argument("out", help="合并输出 JSON")
    parser.add_argument("--retry", action="append", required=True, help="子集重采 JSON；可重复")
    parser.add_argument(
        "--caseset",
        help="可选：从 caseset.perf_case_policy.selection.selected_case_ids 读取最终完整顺序，"
             "允许 retry 补入新选中的性能 case")
    args = parser.parse_args(argv)

    primary = json.loads(Path(args.primary).read_text(encoding="utf-8"))
    retries = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.retry]
    required_case_ids = None
    if args.caseset:
        caseset = json.loads(Path(args.caseset).read_text(encoding="utf-8"))
        required_case_ids = (
            (caseset.get("perf_case_policy") or {}).get("selection") or {}
        ).get("selected_case_ids")
        if not isinstance(required_case_ids, list):
            raise PerfRetryMergeError(
                "caseset 缺 perf_case_policy.selection.selected_case_ids")
    merged = merge_retry_docs(
        primary,
        retries,
        primary_source=args.primary,
        retry_sources=args.retry,
        required_case_ids=required_case_ids,
    )
    Path(args.out).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    audit = merged["retry_merge"]
    print(
        json.dumps(
            {
                "records": len(merged["records"]),
                "replaced": len(audit["replacements"]),
                "remaining_invalid": audit["remaining_invalid_case_ids"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
