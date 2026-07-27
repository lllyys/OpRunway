#!/usr/bin/env python3
"""人读展示单个验收 case 的定义、数据摘要、golden 与原始结果。"""

from __future__ import annotations

import argparse
import json
import math
import os


class InspectError(RuntimeError):
    pass


def _load(path):
    with open(path, encoding="utf-8") as src:
        return json.load(src)


def _safe(root, rel):
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        raise InspectError(f"非法相对路径 {rel!r}")
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, rel))
    if path != root and not path.startswith(root + os.sep):
        raise InspectError(f"路径逃出 work：{rel!r}")
    return path


def _scalar(value):
    value = value.item() if hasattr(value, "item") else value
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _array_summary(array, logical_dtype=None):
    import numpy as np
    import gen_cases

    raw_dtype = str(array.dtype)
    logical = (gen_cases._bf16_uint16_to_f32(array)
               if logical_dtype == "bfloat16" and raw_dtype == "uint16"
               else np.asarray(array))
    flat = logical.reshape(-1)
    head = [_scalar(x) for x in flat[:8]]
    tail = [_scalar(x) for x in flat[-8:]] if flat.size > 8 else head
    finite = flat[np.isfinite(flat)] if np.issubdtype(flat.dtype, np.number) else flat
    return {
        "logical_dtype": logical_dtype or str(logical.dtype),
        "storage_dtype": raw_dtype,
        "shape": list(array.shape),
        "numel": int(flat.size),
        "min": _scalar(finite.min()) if finite.size else None,
        "max": _scalar(finite.max()) if finite.size else None,
        "head": head,
        "tail": tail,
    }


def inspect_case(report_root, case_id):
    import numpy as np

    report_root = os.path.realpath(report_root)
    work = os.path.join(report_root, "work")
    caseset = _load(os.path.join(report_root, "caseset.json"))
    verdict = _load(os.path.join(report_root, "verdict.json"))
    evidence = _load(os.path.join(report_root, "evidence.json"))
    case = next((row for row in caseset["cases"] if row.get("id") == case_id), None)
    if case is None:
        raise InspectError(f"case_id 不在 caseset：{case_id}")
    verdict_row = next(
        (row for row in verdict.get("per_case") or [] if row.get("case_id") == case_id),
        None)
    evidence_row = next(
        (row for row in evidence.get("evidence") or [] if row.get("case_id") == case_id),
        None)

    inputs = []
    for item in case.get("inputs") or []:
        path = _safe(work, item["path"])
        array = np.load(path, allow_pickle=False)
        inputs.append({
            **item,
            "file": path,
            "summary": _array_summary(array, item.get("dtype")),
        })
    outputs = []
    for item in (case.get("expected") or {}).get("outputs") or []:
        path = _safe(work, item["golden_path"])
        array = np.load(path, allow_pickle=False)
        outputs.append({
            "index": item.get("index"), "name": item.get("name"),
            "role": item.get("role"), "out_shape": item.get("out_shape"),
            "compare": item.get("compare"), "compare_dtype": item.get("compare_dtype"),
            "policy": item.get("policy"), "threshold": item.get("threshold"),
            "golden_path": item.get("golden_path"), "golden_file": path,
            "golden_summary": _array_summary(array, item.get("compare_dtype")),
        })
    return {
        "schema": "oprunway.repro_case_description",
        "schema_version": 1,
        "case_id": case_id,
        "dims": case.get("dims") or [],
        "tags": case.get("tags") or [],
        "inputs": inputs,
        "attrs": case.get("attrs") or {},
        "aclnn_call": case.get("aclnn_call"),
        "expected": {
            "golden_source": (case.get("expected") or {}).get("golden_source"),
            "verify_mode": (case.get("expected") or {}).get("verify_mode"),
            "case_origin": (case.get("expected") or {}).get("case_origin"),
            "rule_ref": (case.get("expected") or {}).get("rule_ref"),
            "outputs": outputs,
        },
        "original_verdict": verdict_row,
        "original_evidence": evidence_row,
        "acceptance_verdict": None,
        "note": "只读展示本轮冻结 case；不执行 NPU、不生成或改写验收裁决",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="展示单个验收 case 的完整定义与数据摘要")
    parser.add_argument("--report-root", required=True)
    parser.add_argument("--case-id", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(
        inspect_case(args.report_root, args.case_id),
        ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
