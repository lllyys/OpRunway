#!/usr/bin/env python3
"""快速重放 cpp_extension 验收报告中的精度失败 case。

本工具不生成新用例、不改 golden、不产 acceptance/verdict。它复用报告内已绑定的：
caseset、invocation plan、原始输入/golden、Extension ELF 与 exact vendor ELF，逐条重放并用
现有 precision policy 复算 metrics，供人工快速确认失败是否稳定复现。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
import tempfile

import cpp_extension_adapter
import cpp_extension_driver
import repo_adapter
import validator


class ReproError(RuntimeError):
    pass


def _load(path):
    with open(path, encoding="utf-8") as src:
        return json.load(src)


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _failed_roles(evidence_row):
    roles = []
    for output in (evidence_row.get("precision") or {}).get("outputs") or []:
        state, _why = validator._judge_by_policy(
            output.get("policy") or {}, output.get("metrics") or {})
        if state != "pass":
            roles.append(output.get("role") or output.get("name") or "?")
    return tuple(roles)


def _bind_vendor_before_torch(ctypes_module, vendor, symbols):
    """在 import torch/torch_npu 前绑定 exact vendor，与正式 driver 同序。"""
    handle = ctypes_module.CDLL(vendor, mode=ctypes_module.RTLD_GLOBAL)
    missing = [symbol for symbol in symbols if not hasattr(handle, symbol)]
    if missing:
        raise ReproError(f"指定 vendor library 缺 DUT symbols: {missing}")
    return handle


def _prepend_env_path(name, path):
    current = [item for item in (os.environ.get(name) or "").split(":") if item]
    os.environ[name] = ":".join(
        [path] + [item for item in current if item != path])


def _prepare_vendor_runtime_env(vendor):
    """从 receipt 已校验的 exact vendor ELF 恢复正式 workflow 的 OPP/runtime 路径。"""
    lib_dir = os.path.dirname(vendor)
    op_api_dir = os.path.dirname(lib_dir)
    vendor_root = os.path.dirname(op_api_dir)
    if (os.path.basename(lib_dir) != "lib"
            or os.path.basename(op_api_dir) != "op_api"):
        raise ReproError(
            f"vendor ELF 不符合 <vendor-root>/op_api/lib 结构: {vendor}")
    _prepend_env_path("ASCEND_CUSTOM_OPP_PATH", vendor_root)
    _prepend_env_path("LD_LIBRARY_PATH", lib_dir)
    toolkit = (os.environ.get("ASCEND_TOOLKIT_HOME") or "").strip()
    if toolkit:
        toolkit_lib = os.path.join(toolkit, "lib64")
        if os.path.isdir(toolkit_lib):
            _prepend_env_path("LD_LIBRARY_PATH", toolkit_lib)
    return vendor_root


def select_representatives(caseset, evidence, verdict, max_cases=5):
    """按 dtype × 失败输出组合稳定取首条，避免默认重放 58 个大 shape。"""
    case_by_id = {row["id"]: row for row in caseset["cases"]}
    evidence_by_id = {row["case_id"]: row for row in evidence["evidence"]}
    failed = [
        row["case_id"] for row in verdict["per_case"]
        if row.get("精度") != "pass"
    ]
    selected, seen = [], set()
    for case_id in failed:
        case = case_by_id.get(case_id)
        ev = evidence_by_id.get(case_id)
        if case is None or ev is None:
            raise ReproError(f"{case_id}: verdict/caseset/evidence 无法对齐")
        inputs = case.get("inputs") or []
        dtype = inputs[0].get("dtype") if inputs else "?"
        key = (dtype, _failed_roles(ev))
        if key in seen:
            continue
        seen.add(key)
        selected.append(case_id)
        if len(selected) >= max_cases:
            break
    if not selected:
        raise ReproError("报告中没有精度失败 case")
    return selected


def _resolve_cases(args, caseset, evidence, verdict):
    all_ids = {row["id"] for row in caseset["cases"]}
    if args.case_id:
        selected = list(dict.fromkeys(args.case_id))
    elif args.all_failures:
        selected = [
            row["case_id"] for row in verdict["per_case"]
            if row.get("精度") != "pass"
        ]
    else:
        selected = select_representatives(
            caseset, evidence, verdict, max_cases=args.max_cases)
    missing = [case_id for case_id in selected if case_id not in all_ids]
    if missing:
        raise ReproError(f"case_id 不在 caseset：{missing}")
    return selected


def reproduce(report_root, case_ids, out_dir=None):
    report_root = os.path.realpath(report_root)
    work = os.path.join(report_root, "work")
    caseset = _load(os.path.join(report_root, "caseset.json"))
    plan = _load(os.path.join(work, "cpp_extension_invocation_plan.json"))
    receipt = cpp_extension_adapter.validate_receipt(work, caseset)

    artifact = os.path.realpath(os.path.join(work, receipt["artifact"]["path"]))
    vendor = os.path.realpath(receipt["vendor"]["library_path"])
    if _sha_file(artifact) != receipt["artifact"]["sha256"]:
        raise ReproError("Extension ELF 摘要漂移")
    if _sha_file(vendor) != receipt["vendor"]["library_sha256"]:
        raise ReproError("vendor ELF 摘要漂移")

    _prepare_vendor_runtime_env(vendor)
    # 必须早于 import torch_npu：否则系统 op-api 可能先注册同名 aclnn symbol，
    # Extension 随后绑定到错误实现。handle 还须持有到全部调用结束。
    symbols = sorted({"aclnn" + row["symbol"] for row in plan["cases"]})
    vendor_handle = _bind_vendor_before_torch(ctypes, vendor, symbols)

    import numpy as np
    import torch
    import torch_npu  # noqa: F401

    torch.ops.load_library(artifact)
    namespace = getattr(torch.ops, receipt["load"]["namespace"])
    case_by_id = {row["id"]: row for row in caseset["cases"]}
    invocation_by_id = {row["case_id"]: row for row in plan["cases"]}

    if out_dir is None:
        out_dir = tempfile.mkdtemp(prefix="cpp_extension_repro_", dir=work)
    else:
        out_dir = os.path.realpath(out_dir)
        if os.path.exists(out_dir):
            raise ReproError(f"--out 已存在，拒绝覆盖：{out_dir}")
        os.makedirs(out_dir)
    produced = []
    for case_id in case_ids:
        case, row = case_by_id[case_id], invocation_by_id.get(case_id)
        if row is None:
            raise ReproError(f"{case_id}: invocation plan 缺失")
        args, outputs, contracts = cpp_extension_driver.materialize_invocation(
            torch, np, work, case, row)
        result = list(getattr(namespace, row["entrypoint"])(*args))
        torch.npu.synchronize()
        if len(result) != len(outputs):
            raise ReproError(
                f"{case_id}: Extension 返回 {len(result)} 输出，期望 {len(outputs)}")
        os.makedirs(os.path.join(out_dir, case_id))
        out_rows = []
        for index, (tensor, contract) in enumerate(zip(result, contracts)):
            rel = f"{case_id}/out_{index}.bin"
            dtype, shape = cpp_extension_driver._dump_output(
                torch, np, tensor, contract["compare_dtype"],
                os.path.join(out_dir, rel))
            out_rows.append({
                "index": index, "name": contract.get("name"),
                "role": contract.get("role"), "path": rel,
                "dtype": dtype, "shape": shape,
            })
        produced.append({"case_id": case_id, "outputs": out_rows})

    cpp_extension_driver._atomic_dump(
        os.path.join(out_dir, "out_manifest.json"),
        {"schema_version": 1, "complete": True, "produced": produced})
    subset = {
        **{key: value for key, value in caseset.items() if key != "cases"},
        "cases": [case_by_id[case_id] for case_id in case_ids],
    }
    rows = repo_adapter.build_multi_output_evidence(subset, work, out_dir)
    summary = []
    for row in rows:
        outputs = []
        for output in row["precision"]["outputs"]:
            state, reason = validator._judge_by_policy(
                output["policy"], output["metrics"])
            outputs.append({
                "name": output.get("name"), "role": output.get("role"),
                "state": state, "reason": reason,
                "metrics": output["metrics"], "policy": output["policy"],
                "out_path": output["out_path"],
                "golden_path": output["golden_path"],
            })
        summary.append({
            "case_id": row["case_id"],
            "reproduced_failure": any(x["state"] != "pass" for x in outputs),
            "outputs": outputs,
        })
    result = {
        "schema": "oprunway.cpp_extension_failure_repro",
        "schema_version": 1,
        "report_root": report_root,
        "source_receipt_sha256": cpp_extension_adapter._canonical_sha(receipt),
        "selected_cases": case_ids,
        "reproduced_failures": sum(x["reproduced_failure"] for x in summary),
        "results": summary,
        "out_dir": out_dir,
        "acceptance_verdict": None,
        "note": "人工复现产物，不生成或改写验收裁决",
    }
    cpp_extension_driver._atomic_dump(
        os.path.join(out_dir, "repro_summary.json"), result)
    del vendor_handle
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="重放 cpp_extension 报告中的精度失败 case；默认取 dtype×失败输出组合代表项")
    parser.add_argument("--report-root", required=True)
    parser.add_argument("--case-id", action="append",
                        help="指定 case，可重复；给出后覆盖默认代表集")
    parser.add_argument("--all-failures", action="store_true",
                        help="重放 verdict 中所有精度失败 case")
    parser.add_argument("--max-cases", type=int, default=5,
                        help="默认代表集最大 case 数（默认 5）")
    parser.add_argument("--out", help="输出目录；默认在报告 work 下创建唯一临时目录")
    args = parser.parse_args(argv)
    if args.max_cases < 1:
        parser.error("--max-cases 必须 >= 1")

    report = os.path.realpath(args.report_root)
    caseset = _load(os.path.join(report, "caseset.json"))
    evidence = _load(os.path.join(report, "evidence.json"))
    verdict = _load(os.path.join(report, "verdict.json"))
    selected = _resolve_cases(args, caseset, evidence, verdict)
    try:
        result = reproduce(report, selected, out_dir=args.out)
    except Exception as exc:
        print(
            f"重放执行异常（未形成精度复核结果）：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["reproduced_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
