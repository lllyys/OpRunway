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
    for row in plan["cases"]:
        case = by_id.get(row["case_id"])
        if case is None:
            raise DriverError(f"plan case {row['case_id']!r} 不在 caseset")
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
    with open(os.path.join(out_root, "out_manifest.json"), "w", encoding="utf-8") as out:
        json.dump({"schema_version": 1, "produced": produced}, out,
                  ensure_ascii=False, indent=2)
        out.write("\n")
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
    with open(os.path.join(work, "cpp_extension_receipt.json"),
              "w", encoding="utf-8") as out:
        json.dump(receipt, out, ensure_ascii=False, indent=2)
        out.write("\n")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--work", required=True)
    ns = parser.parse_args(argv)
    print(json.dumps(run(ns.bundle, ns.work), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
