#!/usr/bin/env python3
"""隔离进程中的同机 NPU 随机 reference 执行器。

父 driver 已加载 DUT vendor，因此 reference 不能在同一进程调用。worker 在 import torch_npu
之前删除自定义 OPP/vendor 环境，只消费 capability callable 与已冻结 case role；输出固定为
reference 中间工件，不含任何 verdict。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile

import stochastic_collector as COL
import stochastic_contract as SC


SCHEMA = "oprunway.stochastic_reference_execution"
_CALLABLE = re.compile(r"(?:torch|torch_npu)\.Tensor\.([A-Za-z_][A-Za-z0-9_]*)\Z")
_CUSTOM_ENV = "ASCEND_CUSTOM_OPP_PATH"


def _dump(path, value):
    root = os.path.dirname(os.path.abspath(path))
    os.makedirs(root, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _load(path):
    with open(path, encoding="utf-8") as src:
        return json.load(src)


def run(caseset, out_dir, device_index):
    if _CUSTOM_ENV in os.environ:
        raise RuntimeError(
            "reference worker 启动环境仍含 ASCEND_CUSTOM_OPP_PATH；父进程必须显式清除")
    if "OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY" in os.environ:
        raise RuntimeError("reference worker 启动环境仍含 DUT vendor library 入口")
    contract = SC.normalize_contract(caseset.get("stochastic_contract"))
    plan = SC.build_case_plan(contract)
    case_by_role = {
        case["stochastic"]["role"]: case for case in caseset.get("cases") or []
        if isinstance(case, dict) and isinstance(case.get("stochastic"), dict)
    }
    pre_roles = [row["role"] for row in plan["cases"]
                 if row["purpose"] == "rng_consumption_precondition"]
    callable_name = contract["oracle_precondition"]["callable"]
    match = _CALLABLE.fullmatch(callable_name)
    if match is None:
        raise RuntimeError("reference callable 非受控 Tensor method 形态")
    method_name = match.group(1)

    import torch
    import torch_npu

    if torch.npu.current_device() != device_index:
        torch.npu.set_device(device_index)
    if torch.npu.current_device() != device_index:
        raise RuntimeError("reference worker 未落到请求的 NPU device")
    soc = str(torch.npu.get_device_name(device_index))
    device = COL.device_identity(
        device_index, soc, torch.__version__, torch_npu.__version__)
    dtype_map = {
        "float32": torch.float32, "float16": torch.float16,
        "bfloat16": torch.bfloat16, "float64": torch.float64,
        "int64": torch.int64, "int32": torch.int32, "int16": torch.int16,
        "int8": torch.int8, "uint8": torch.uint8, "bool": torch.bool,
    }
    os.makedirs(out_dir, exist_ok=True)
    records = []
    for role in pre_roles:
        case = case_by_role.get(role)
        if not isinstance(case, dict):
            raise RuntimeError(f"reference role={role!r} 不在 caseset")
        binding = case["stochastic"]
        expected = case.get("expected") or {}
        dtype_name = expected.get("compare_dtype")
        if dtype_name not in dtype_map:
            raise RuntimeError(f"reference dtype={dtype_name!r} 非受控值")
        shape = expected.get("out_shape")
        if not isinstance(shape, list):
            raise RuntimeError(f"reference role={role!r} 缺 out_shape")
        generator = torch.Generator(device=f"npu:{device_index}")
        for name in ("manual_seed", "set_offset", "get_offset"):
            if not hasattr(generator, name):
                raise RuntimeError(f"NPU Generator 缺 {name}")
        values = binding["values"]
        names = contract["bindings"]
        seed, offset = values[names["seed"]], values[names["offset"]]
        probability = values[names["probability"]]
        generator.manual_seed(seed)
        generator.set_offset(offset)
        if generator.get_offset() != offset:
            raise RuntimeError("NPU Generator offset 回读不一致")
        tensor = torch.empty(shape, dtype=dtype_map[dtype_name], device=f"npu:{device_index}")
        method = getattr(tensor, method_name, None)
        if not callable(method):
            raise RuntimeError(f"NPU Tensor 缺 reference method {method_name!r}")
        method(probability, generator=generator)
        torch.npu.synchronize()
        values_cpu = tensor.to("cpu").byte().reshape(-1).tolist()
        if any(value not in (0, 1) for value in values_cpu):
            raise RuntimeError("reference 输出含非二元值")
        payload = bytes(values_cpu)
        filename = role + ".bin"
        path = os.path.join(out_dir, filename)
        with open(path, "wb") as out:
            out.write(payload)
        records.append({
            "role": role, "path": filename, "sha256": hashlib.sha256(payload).hexdigest(),
            "sample_count": len(payload), "probability": probability,
            "seed": seed, "offset": offset,
        })
    return {
        "schema": SCHEMA, "schema_version": 1,
        "status": "complete", "device": device,
        "reference_method": contract["oracle_precondition"]["method_kind"],
        "reference_callable": callable_name,
        "custom_opp_path_present": False,
        "dut_vendor_env_present": False,
        "records": records,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--caseset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--device", type=int, required=True)
    args = parser.parse_args(argv)
    result = run(_load(args.caseset), args.out_dir, args.device)
    _dump(args.out, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
