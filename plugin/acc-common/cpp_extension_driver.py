#!/usr/bin/env python3
"""容器内官方 C++ Extension build/load/invoke driver。

由 ``cpp_extension_adapter`` 以 argv 调用。它只在已经准备好的 bundle/work 上工作：
build_ext --inplace → 加载精确 ELF → 逐 case 调独立 torch.ops entrypoint → 落
out_manifest 与内容寻址 receipt。机器连接、容器进入、文件同步由 argv 外层负责。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import re
import tempfile


class DriverError(RuntimeError):
    pass


_TORCH_DTYPES = {
    "float32": "float32",
    "float16": "float16",
    "bfloat16": "bfloat16",
    "int64": "int64",
    "int32": "int32",
    "int16": "int16",
    "int8": "int8",
    "uint8": "uint8",
    "bool": "bool",
}


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_sha(value):
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _load(path):
    with open(path, encoding="utf-8") as src:
        return json.load(
            src,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"非法 JSON 常量 {token}")))


def _atomic_dump(path, value):
    """同目录原子写 JSON；设备/进程异常时不留下半截证据。"""
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".tmp.", dir=os.path.dirname(path))
    try:
        out = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        os.unlink(tmp)
        raise
    try:
        with out:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _safe(root, rel):
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        raise DriverError(f"非法相对路径 {rel!r}")
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, rel))
    if path != root and not path.startswith(root + os.sep):
        raise DriverError(f"路径逃出根目录 {rel!r}")
    return path


def _require_env_path(key):
    value = (os.environ.get(key) or "").strip()
    if not value or not os.path.isabs(value) or not os.path.isfile(value):
        raise DriverError(f"{key} 须指向存在的绝对普通文件")
    return os.path.realpath(value)


def _vendor_build_provenance(vendor):
    """读取并核验 DUT vendor ELF 的独立构建收据。

    Extension 自身的 build/load 成功只证明调用桥可用，不能证明被加载的 vendor
    ELF 来自任务 PR。生产者须在构建/安装 DUT 时落一份内容可校验的收据；这里把
    完整 PR head、构建命令和最终 ELF 摘要重新对账后纳入 Extension receipt。
    """
    path = _require_env_path("OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT")
    receipt = _load(path)
    if (not isinstance(receipt, dict)
            or receipt.get("schema") != "oprunway.vendor_build_receipt"
            or receipt.get("schema_version") != 1
            or receipt.get("status") != "VERIFIED"):
        raise DriverError("vendor build receipt schema/status 非 VERIFIED v1")
    source = receipt.get("source")
    head = source.get("pr_head_sha") if isinstance(source, dict) else None
    if (not isinstance(head, str)
            or re.fullmatch(r"[0-9a-fA-F]{40}", head) is None
            or not isinstance(source.get("repo"), str)
            or not source["repo"].strip()):
        raise DriverError("vendor build receipt 缺完整 PR head/source repo")
    build = receipt.get("build")
    if (not isinstance(build, dict)
            or not isinstance(build.get("argv"), list)
            or not build["argv"]
            or any(not isinstance(x, str) or not x for x in build["argv"])
            or not isinstance(build.get("cwd"), str)
            or not build["cwd"]
            or build.get("returncode") != 0):
        raise DriverError("vendor build receipt 缺成功 build argv/cwd/returncode")
    artifact = receipt.get("artifact")
    if (not isinstance(artifact, dict)
            or os.path.realpath(artifact.get("library_path", "")) != vendor
            or artifact.get("library_sha256") != _sha_file(vendor)):
        raise DriverError("vendor build receipt 的安装 ELF 路径/摘要与现场文件不一致")
    return receipt


def _build(bundle, manifest):
    argv = [sys.executable, "setup.py", "build_ext", "--inplace"]
    run = subprocess.run(argv, cwd=bundle, check=False)
    if run.returncode != 0:
        raise DriverError(f"NpuExtension build 失败 rc={run.returncode}")
    module = manifest["module_name"]
    candidates = sorted(Path(bundle).glob(module + "*.so"))
    if len(candidates) != 1:
        raise DriverError(
            f"Extension ELF 必须唯一，pattern={module}*.so 命中 {len(candidates)}")
    return argv, os.path.realpath(candidates[0])


def _bind_vendor(plan):
    vendor = _require_env_path("OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY")
    build_provenance = _vendor_build_provenance(vendor)
    symbols = sorted({"aclnn" + row["symbol"] for row in plan["cases"]})
    handle = ctypes.CDLL(vendor, mode=ctypes.RTLD_GLOBAL)
    missing = [symbol for symbol in symbols if not hasattr(handle, symbol)]
    if missing:
        raise DriverError(f"指定 vendor library 缺 DUT symbols: {missing}")
    return handle, {
        "library_path": vendor,
        "library_sha256": _sha_file(vendor),
        "symbols_owned": symbols,
        "binding": "ctypes.CDLL(exact_path, RTLD_GLOBAL) before torch.ops.load_library",
        "build_receipt": build_provenance,
        "build_receipt_sha256": _canonical_sha(build_provenance),
    }


def _input_tensor(torch, np, work, item):
    arr = np.load(_safe(work, item["path"]), allow_pickle=False)
    dtype = item["dtype"]
    if dtype == "bfloat16":
        if str(arr.dtype) != "uint16":
            raise DriverError(
                f"{item.get('name')}: bf16 输入 storage 须为 uint16，得 {arr.dtype}")
        tensor = torch.from_numpy(np.ascontiguousarray(arr)).view(torch.bfloat16)
    else:
        name = _TORCH_DTYPES.get(dtype)
        if name is None:
            raise DriverError(f"不支持输入 dtype={dtype!r}")
        tensor = torch.from_numpy(np.ascontiguousarray(arr))
        target = getattr(torch, name)
        if tensor.dtype != target:
            raise DriverError(
                f"{item.get('name')}: numpy storage dtype→torch {tensor.dtype} ≠ {target}")
    return tensor.npu()


def _expected_outputs(case):
    expected = case.get("expected") or {}
    outputs = expected.get("outputs")
    if isinstance(outputs, list):
        return outputs
    return [{
        "name": "out",
        "role": "value",
        "compare_dtype": expected.get("compare_dtype") or case["inputs"][0]["dtype"],
        "out_shape": expected.get("out_shape") or [],
    }]


def _empty_output(torch, output):
    dtype_name = output.get("compare_dtype")
    torch_name = _TORCH_DTYPES.get(dtype_name)
    if torch_name is None:
        raise DriverError(f"输出 {output.get('name')}: 不支持 dtype={dtype_name!r}")
    shape = output.get("out_shape")
    if not isinstance(shape, list) or any(
            isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in shape):
        raise DriverError(f"输出 {output.get('name')}: out_shape 非非负整数数组")
    return torch.empty(tuple(shape), dtype=getattr(torch, torch_name), device="npu")


def _dump_output(torch, np, tensor, dtype, path):
    value = tensor.detach().contiguous().cpu()
    if dtype == "bfloat16":
        # repo_adapter 的统一 readback 口径：bf16 输出扩成 fp32 落盘。
        arr = value.to(torch.float32).numpy()
        disk_dtype = "float32"
    elif dtype == "bool":
        arr = value.to(torch.uint8).numpy()
        # numpy bool 与 uint8 都是单字节；保留逻辑 dtype，避免证据层把
        # 布尔 ABI 输出误判成整型输出。
        disk_dtype = "bool"
    else:
        arr = value.numpy()
        disk_dtype = str(arr.dtype)
    np.ascontiguousarray(arr).tofile(path)
    return disk_dtype, list(arr.shape)


def materialize_invocation(torch, np, work, case, row):
    """按已冻结 invocation-plan 物化一次 Extension 调用；供精度与性能共用。"""
    inputs = [_input_tensor(torch, np, work, item) for item in case["inputs"]]
    output_contracts = _expected_outputs(case)
    outputs = [_empty_output(torch, item) for item in output_contracts]
    args = []
    for slot in row["slots"]:
        role = slot["role"]
        if role == "in":
            args.append(inputs[int(slot["input_idx"])])
        elif role == "attr":
            args.append(slot["value"])
        elif role == "out":
            args.append(outputs[int(slot["output_idx"])])
        elif role == "out_null":
            args.append(None)
        else:
            raise DriverError(f"{case['id']}: 未知 slot role={role!r}")
    return args, outputs, output_contracts


def _invoke_all(bundle, work, manifest, plan, caseset, artifact):
    import numpy as np
    import torch
    import torch_npu  # noqa: F401

    torch.ops.load_library(artifact)
    namespace = getattr(torch.ops, manifest["namespace"])
    variants = {row["entrypoint"]: row for row in manifest["variants"]}
    schemas = {}
    for entrypoint in variants:
        packet = getattr(namespace, entrypoint)
        overload = packet.default
        schemas[entrypoint] = str(overload._schema)

    by_id = {case["id"]: case for case in caseset["cases"]}
    out_root = os.path.join(work, "cpp_extension_out")
    if os.path.lexists(out_root):
        if os.path.islink(out_root):
            raise DriverError("cpp_extension_out 不得为软链")
        shutil.rmtree(out_root)
    os.makedirs(out_root)
    produced = []
    progress_path = os.path.join(out_root, "progress.json")
    manifest_path = os.path.join(out_root, "out_manifest.json")
    total = len(plan["cases"])
    _atomic_dump(progress_path, {
        "schema_version": 1, "status": "running", "current_case_id": None,
        "completed_cases": 0, "total_cases": total,
    })
    for row in plan["cases"]:
        case = by_id.get(row["case_id"])
        if case is None:
            raise DriverError(f"plan case {row['case_id']!r} 不在 caseset")
        _atomic_dump(progress_path, {
            "schema_version": 1, "status": "running",
            "current_case_id": case["id"],
            "completed_cases": len(produced), "total_cases": total,
        })
        args, outputs, output_contracts = materialize_invocation(
            torch, np, work, case, row)
        result = getattr(namespace, row["entrypoint"])(*args)
        torch.npu.synchronize()
        returned = list(result)
        if len(returned) != len(outputs):
            raise DriverError(
                f"{case['id']}: Extension 返回 {len(returned)} 输出，期望 {len(outputs)}")
        cdir = os.path.join(out_root, case["id"])
        os.makedirs(cdir)
        out_rows = []
        for index, (tensor, contract) in enumerate(zip(returned, output_contracts)):
            rel = f"{case['id']}/out_{index}.bin"
            dtype, shape = _dump_output(
                torch, np, tensor, contract["compare_dtype"],
                os.path.join(out_root, rel))
            out_rows.append({
                "index": index,
                "name": contract.get("name"),
                "role": contract.get("role"),
                "path": rel,
                "dtype": dtype,
                "shape": shape,
            })
        produced.append({"case_id": case["id"], "outputs": out_rows})
        _atomic_dump(manifest_path, {
            "schema_version": 1, "complete": False, "produced": produced})
        _atomic_dump(progress_path, {
            "schema_version": 1, "status": "running",
            "current_case_id": None,
            "last_completed_case_id": case["id"],
            "completed_cases": len(produced), "total_cases": total,
        })
    _atomic_dump(manifest_path, {
        "schema_version": 1, "complete": True, "produced": produced})
    _atomic_dump(progress_path, {
        "schema_version": 1, "status": "complete", "current_case_id": None,
        "last_completed_case_id": produced[-1]["case_id"] if produced else None,
        "completed_cases": len(produced), "total_cases": total,
    })
    return torch, schemas


def run(bundle, work):
    bundle, work = os.path.realpath(bundle), os.path.realpath(work)
    if not os.path.isdir(bundle) or not os.path.isdir(work):
        raise DriverError("bundle/work 须为存在目录")
    manifest = _load(os.path.join(bundle, "extension_manifest.json"))
    plan = _load(os.path.join(work, "cpp_extension_invocation_plan.json"))
    caseset = _load(os.path.join(work, "cpp_extension_caseset.json"))
    if plan.get("caseset_sha256") != _canonical_sha(caseset):
        raise DriverError("invocation plan 与 caseset 摘要不一致")
    _handle, vendor = _bind_vendor(plan)
    build_argv, artifact = _build(bundle, manifest)
    torch, schemas = _invoke_all(
        bundle, work, manifest, plan, caseset, artifact)

    artifact_rel = os.path.relpath(artifact, work).replace(os.sep, "/")
    if artifact_rel.startswith("../"):
        raise DriverError("Extension ELF 不在 work 根内")
    try:
        import torch_npu
        torch_npu_version = torch_npu.__version__
    except AttributeError:
        torch_npu_version = "unknown"
    runtime = {
        "torch_version": str(torch.__version__),
        "torch_npu_version": str(torch_npu_version),
        "cann_version": (os.environ.get("ASCEND_TOOLKIT_VERSION")
                         or os.environ.get("CANN_VERSION") or "unknown"),
        "soc": os.environ.get("OPRUNWAY_SOC") or "unknown",
    }
    if "unknown" in runtime.values():
        raise DriverError(
            "runtime provenance 不完整；须提供 CANN_VERSION/ASCEND_TOOLKIT_VERSION 与 OPRUNWAY_SOC")
    receipt = {
        "schema": "oprunway.cpp_extension_receipt",
        "schema_version": 1,
        "status": "VERIFIED",
        "bindings": {
            "caseset_sha256": _canonical_sha(caseset),
            "manifest_sha256": _canonical_sha(manifest),
            "invocation_plan_sha256": _canonical_sha(plan),
            "spec_sha256": manifest["spec_sha256"],
        },
        "runtime": runtime,
        "build": {"argv": build_argv, "returncode": 0},
        "artifact": {"path": artifact_rel, "sha256": _sha_file(artifact)},
        "load": {
            "success": True,
            "loader": "torch.ops.load_library",
            "namespace": manifest["namespace"],
            "schemas": schemas,
        },
        "vendor": vendor,
    }
    _atomic_dump(
        os.path.join(work, "cpp_extension_receipt.json"), receipt)
    return receipt


def run_perf_only(bundle, work):
    """复用已验证 Extension ELF，走 perf_msprof 的统一双边 kernel-only 采集链。"""
    del bundle  # bundle 已由第一阶段 build receipt 内容寻址；性能只加载精确 ELF。
    work = os.path.realpath(work)
    receipt = _load(os.path.join(work, "cpp_extension_receipt.json"))
    plan = _load(os.path.join(work, "cpp_extension_perf_plan.json"))
    caseset = _load(os.path.join(work, "cpp_extension_caseset.json"))
    if (plan.get("caseset_sha256") != _canonical_sha(caseset)
            or plan.get("cpp_extension_receipt_sha256") != _canonical_sha(receipt)):
        raise DriverError("性能计划与本轮 caseset/build receipt 绑定漂移")
    artifact = _safe(work, receipt["artifact"]["path"])
    if _sha_file(artifact) != receipt["artifact"]["sha256"]:
        raise DriverError("性能阶段 Extension ELF 与第一阶段 receipt 漂移")
    cpp = plan.get("cpp_extension") or {}
    vendor = cpp.get("vendor") or {}
    vendor_path = vendor.get("library_path")
    if (not isinstance(vendor_path, str) or not os.path.isfile(vendor_path)
            or _sha_file(vendor_path) != vendor.get("library_sha256")):
        raise DriverError("性能阶段 vendor library 缺失或与第一阶段 receipt 漂移")
    if cpp.get("artifact") != receipt.get("artifact") \
            or cpp.get("namespace") != receipt.get("load", {}).get("namespace"):
        raise DriverError("性能计划的 Extension artifact/namespace 与 receipt 漂移")
    invocation_path = _safe(work, cpp.get("invocation_plan"))
    invocation = _load(invocation_path)
    if (cpp.get("invocation_plan_sha256")
            != receipt.get("bindings", {}).get("invocation_plan_sha256")
            or _canonical_sha(invocation) != cpp.get("invocation_plan_sha256")):
        raise DriverError("性能阶段 invocation plan 与第一阶段 receipt 漂移")
    os.environ["OPRUNWAY_ACLNN_REAL"] = "1"
    from aclnn_runtime import perf_msprof as PM
    return PM.collect(
        os.path.join(work, "cpp_extension_caseset.json"),
        work,
        plan,
        os.path.join(work, "cpp_extension_perf_collect.json"),
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--perf-only", action="store_true")
    ns = parser.parse_args(argv)
    result = (run_perf_only(ns.bundle, ns.work) if ns.perf_only
              else run(ns.bundle, ns.work))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
