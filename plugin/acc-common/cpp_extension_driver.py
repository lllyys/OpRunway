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
import tempfile

import vendor_build_receipt


class DriverError(RuntimeError):
    pass


# 逻辑 dtype 名 → torch dtype 名。**必须与 `repo_adapter.SUPPORTED_NP_BY_FORM["cpp_extension"]`
# 同步**：那张表宣称本通路收发得了哪些 dtype，这张表是它在真机上的兑现处。少一条 = 能力表说支持、
# 驱动当场拒（声明与实现不一致，本仓判定比缺能力更坏）。同步由
# `test_dtype_capability_closure.py` 的双向对账钉死，别单改一边。
_TORCH_DTYPES = {
    "float32": "float32",
    "float16": "float16",
    "bfloat16": "bfloat16",
    "int64": "int64",
    "int32": "int32",
    "int16": "int16",
    "int8": "int8",
    "uint8": "uint8",
    "uint32": "uint32",        # 2026-08-06 · 真机实测往返（见 repo_adapter 同处 provenance 注）
    "complex64": "complex64",  # 同上
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
    源身份、构建命令和最终 ELF 摘要重新对账后纳入 Extension receipt。

    ⚠ 逐条校验**不在本文件里写第二遍**：三处消费方（本 driver、离线 adapter、验收门）
    共用 :mod:`vendor_build_receipt`。改动前那三份手抄件里，每一份都无条件要求 40 位
    PR head —— 于是「无 `.git` 的本地快照」这条通路要么恒 BLOCKED、要么只能靠捏造
    head 过门。分流规则与词表映射见该模块 docstring。
    """
    path = _require_env_path("OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT")
    receipt = _load(path)
    # driver 侧独立再校一遍，不依赖 adapter 已经校过——两处都是信任边界。
    try:
        vendor_build_receipt.validate(
            receipt, library_path=vendor, library_sha256=_sha_file(vendor),
            # 真机侧拿到的是 realpath 后的绝对路径，故按 realpath 比对；
            # 离线复核方比的是收据里逐字记录的字符串（不碰文件系统）。
            normalize_path=True)
    except vendor_build_receipt.VendorBuildReceiptError as ex:
        raise DriverError(str(ex)) from ex
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


#: 本轮自定义算子符号的来源包，落在 receipt 的 runtime provenance 里（与 `cann_version` /
#: `soc` 同一层）。验收门用 `vendor_build_receipt.custom_opp_path` 从 `vendor.library_path`
#: 重算并逐字对账 —— 「这一轮的 aclnnXxx 由哪个 vendor 提供」因此是**机器可核事实**，
#: 而不是现场某个人记得自己 source 过什么。
RUNTIME_CUSTOM_OPP_KEY = "ascend_custom_opp_path"


def bind_custom_opp_path(library):
    """在**任何算子调用之前**把本轮 DUT 的自定义算子包绑进进程环境；返回最终生效值。

    torch_npu 运行时 getenv ``ASCEND_CUSTOM_OPP_PATH`` 去找 `libcust_opapi.so`。没有它，
    `aclnnXxxGetWorkspaceSize` 只会在 CANN 内置 `libopapi.so` 里找不到，于是**每一条** case
    都落成 `execution_failed`，报 ``not in libopapi.so, or libopapi.so not found``。

    改动前 driver 从不设这个变量：能跑通全靠人**手动 source 过** vendor 的
    `bin/set_env.bash`。那是一份没有被任何产物记录的环境状态 —— 于是「上一轮 164 条执行
    通过」在干净现场一条都复现不出来，而两轮的 codegen 产物逐字节相同。可复现性缺陷不在
    代码生成里，在这条隐式环境依赖里。现在它被收进 driver：值只从**已被 build receipt
    绑定**的那个 vendor `.so` 反推（规则见 `vendor_build_receipt.custom_opp_path`），
    与谁 source 过什么无关。

    已有冲突值 = fail-closed，不是覆盖也不是追加：环境里若已指着**别的** vendor 包，
    这一轮就可能跑在别人的符号上，而报告写的却是本轮 PR 的身份（AGENTS.md 5.8）。
    路径列表按 `os.pathsep` 拆开、realpath 去重后必须恰好只剩本轮这一个包。

    ⚠ 只管 ``ASCEND_CUSTOM_OPP_PATH``。`set_env.bash` 里另一条 ``LD_LIBRARY_PATH`` 由动态
    加载器在 **exec 时**读取，进程内改已经晚了；而 DUT `.so` 本身是 driver 用绝对路径
    `ctypes.CDLL(..., RTLD_GLOBAL)` 装进来的，不经过搜索路径。真机实测：只设本变量即可让
    符号解析成功，故这里不假装能设 `LD_LIBRARY_PATH`。
    """
    try:
        expected = vendor_build_receipt.custom_opp_path(library)
    except vendor_build_receipt.VendorBuildReceiptError as ex:
        # driver 对外只抛 DriverError（adapter 按它归因）；布局判据仍由那一处解释。
        raise DriverError(str(ex)) from ex
    seen, entries = set(), []
    for item in (os.environ.get(vendor_build_receipt.CUSTOM_OPP_ENV) or "").split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        real = os.path.realpath(item)
        if real not in seen:
            seen.add(real)
            entries.append(real)
    if entries and entries != [expected]:
        raise DriverError(
            f"{vendor_build_receipt.CUSTOM_OPP_ENV} 已指向 {entries!r}，与本轮收据绑定的 vendor 包 "
            f"{expected!r} 不一致——本轮可能跑在别的 vendor 的符号上，fail-closed。"
            "请在干净环境里跑（本 driver 自己会设这个变量），不要预先 source 其它 vendor 的 set_env.bash")
    os.environ[vendor_build_receipt.CUSTOM_OPP_ENV] = expected
    return expected


def _bind_vendor(plan):
    vendor = _require_env_path("OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY")
    build_provenance = _vendor_build_provenance(vendor)
    # 顺序是判据的一部分：收据先核过这个 `.so` 的身份，才轮到从它反推符号来源包；
    # 而绑定必须发生在 CDLL / torch.ops 调用**之前**。
    custom_opp = bind_custom_opp_path(vendor)
    symbols = sorted({"aclnn" + row["symbol"] for row in plan["cases"]})
    handle = ctypes.CDLL(vendor, mode=ctypes.RTLD_GLOBAL)
    missing = [symbol for symbol in symbols if not hasattr(handle, symbol)]
    if missing:
        raise DriverError(f"指定 vendor library 缺 DUT symbols: {missing}")
    return handle, custom_opp, {
        "library_path": vendor,
        "library_sha256": _sha_file(vendor),
        "symbols_owned": symbols,
        "binding": "ctypes.CDLL(exact_path, RTLD_GLOBAL) before torch.ops.load_library",
        "build_receipt": build_provenance,
        "build_receipt_sha256": _canonical_sha(build_provenance),
        # 源身份摘要（含 `degradations` 机读挂账）。它是 build_receipt 的**派生视图**，
        # 不是新事实：离线复核方会用同一个函数重算并逐字比对，谁改一处都对不上。
        # 落这一份的理由是可见性：`pr_head_unbound` 这类降级必须在报告的第一层就看得见，
        # 而不是埋在嵌套收据里等人自己翻（AGENTS.md 5.8）。
        "source_provenance": vendor_build_receipt.summarize(build_provenance),
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
        # ⚠ 刻意**不写** `or []`：`[]` 是「声明为标量输出」（rank-0 归约），`None`/缺键是
        # 「压根没人声明过输出形状」。旧写法把两者折成同一个值，于是形状未知的 case 被静默
        # 分配成 0-d dst，真机侧报的是「src and dst must have the same shape」——一条本该
        # 停在本地的 harness 缺陷，被写成了 DUT 的拒绝理由。缺声明由 `_empty_output` fail-closed。
        "out_shape": expected.get("out_shape"),
    }]


def _empty_output(torch, output):
    dtype_name = output.get("compare_dtype")
    torch_name = _TORCH_DTYPES.get(dtype_name)
    if torch_name is None:
        raise DriverError(f"输出 {output.get('name')}: 不支持 dtype={dtype_name!r}")
    shape = output.get("out_shape")
    if shape is None:
        raise DriverError(
            f"输出 {output.get('name')}: caseset 未声明 out_shape，无法分配 dst——"
            "不按输入形状或任何算子语义猜（猜错会让真机把 harness 的错报成 DUT 的拒绝）")
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


#: 逐 case 失败的受控归类（进 `out_manifest.failed[].error_kind`）。按**失败发生在哪一步**分，
#: 不按猜测的成因分：三者对下游都是「这条 case 没有可比结果」，但归因写死在事实上。
FAILED_MATERIALIZE = "input_materialization_failed"   # 输入落盘字节 → NPU 张量这一步就没成
FAILED_EXECUTE = "execution_failed"                   # 真正调 DUT entrypoint（含同步、返回元数）失败
FAILED_READBACK = "output_readback_failed"            # 调用成功但输出读回/落盘失败
_FAILED_KIND_BY_PHASE = {
    "materialize": FAILED_MATERIALIZE,
    "execute": FAILED_EXECUTE,
    "readback": FAILED_READBACK,
}


def _invoke_all(bundle, work, manifest, plan, caseset, artifact):
    """逐 case 执行整份 invocation plan；**单条失败不中断整轮**。

    改动前这里是一句裸调用：第一条 case 抛异常就把整个 driver 带走，于是 169 条里第 1 条被
    DUT 拒（`aclnnXxxGetWorkspaceSize` 返回非零 → TORCH_CHECK 抛 RuntimeError）就等于**一件
    产物都不产**——既拿不到其余 168 条的精度证据，也没有任何机读事实说明第 1 条为什么挂。
    这与 `golden_unavailable` 的一等状态设计（保留身份、其余继续、由门判 BLOCKED）自相矛盾。

    现在：每条 case 单独 try/except，失败的进 `out_manifest.failed[]`（case 身份 + **逐字**
    错误原文 + 按阶段归类），`progress.json` 同步计数，然后**继续跑下一条**，最后仍写
    `complete: True`（= 这一轮把 plan 走完了，不是「采了个子集」）。

    ⚠ 这不放松任何判定：`failed` 里的 case 在 evidence 侧是 `status=execution_failed`（无
    metrics）、在 validator 侧功能维恒 fail、在验收门里必须被反向核到确实落成失败。「跳过了」
    绝不等于「通过了」（AGENTS.md 5.8）。
    """
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
    failed = []
    progress_path = os.path.join(out_root, "progress.json")
    manifest_path = os.path.join(out_root, "out_manifest.json")
    total = len(plan["cases"])
    last_case = [None]

    def _progress(status, current=None):
        _atomic_dump(progress_path, {
            "schema_version": 1, "status": status, "current_case_id": current,
            "last_attempted_case_id": last_case[0],
            "completed_cases": len(produced), "failed_cases": len(failed),
            "attempted_cases": len(produced) + len(failed), "total_cases": total,
        })

    def _snapshot(complete):
        _atomic_dump(manifest_path, {
            "schema_version": 1, "complete": complete,
            "produced": produced, "failed": failed})

    _progress("running")
    for row in plan["cases"]:
        case = by_id.get(row["case_id"])
        if case is None:
            # plan↔caseset 对不上是**整轮**的绑定破损，不是某条 case 跑挂了：照旧当场炸。
            raise DriverError(f"plan case {row['case_id']!r} 不在 caseset")
        _progress("running", current=case["id"])
        cdir = os.path.join(out_root, case["id"])
        phase = "materialize"
        try:
            args, outputs, output_contracts = materialize_invocation(
                torch, np, work, case, row)
            phase = "execute"
            result = getattr(namespace, row["entrypoint"])(*args)
            torch.npu.synchronize()
            returned = list(result)
            if len(returned) != len(outputs):
                raise DriverError(
                    f"{case['id']}: Extension 返回 {len(returned)} 输出，期望 {len(outputs)}")
            phase = "readback"
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
        except Exception as ex:  # noqa: BLE001 —— 单条 case 的任何失败都只归这条，不带走整轮
            if os.path.isdir(cdir):
                # 半截产物必须清掉：下游按 manifest 读字节，留一堆残缺 out_k.bin 只会制造
                # 「看起来有产物」的假象。
                shutil.rmtree(cdir, ignore_errors=True)
            failed.append({
                "case_id": case["id"],
                "entrypoint": row["entrypoint"],
                "phase": phase,
                "error_kind": _FAILED_KIND_BY_PHASE[phase],
                "error_type": type(ex).__name__,
                # **逐字原文**：不截断、不改写、不翻译。归因要拿得出原话（AGENTS.md 5.8）。
                "error": str(ex),
            })
            last_case[0] = case["id"]
            _snapshot(False)
            _progress("running")
            continue
        produced.append({"case_id": case["id"], "outputs": out_rows})
        last_case[0] = case["id"]
        _snapshot(False)
        _progress("running")
    _snapshot(True)
    _progress("complete")
    return torch, schemas, {
        "planned": total, "produced": len(produced), "failed": len(failed),
        "failed_case_ids": [row["case_id"] for row in failed],
    }


def run(bundle, work):
    bundle, work = os.path.realpath(bundle), os.path.realpath(work)
    if not os.path.isdir(bundle) or not os.path.isdir(work):
        raise DriverError("bundle/work 须为存在目录")
    manifest = _load(os.path.join(bundle, "extension_manifest.json"))
    plan = _load(os.path.join(work, "cpp_extension_invocation_plan.json"))
    caseset = _load(os.path.join(work, "cpp_extension_caseset.json"))
    if plan.get("caseset_sha256") != _canonical_sha(caseset):
        raise DriverError("invocation plan 与 caseset 摘要不一致")
    _handle, custom_opp, vendor = _bind_vendor(plan)
    build_argv, artifact = _build(bundle, manifest)
    torch, schemas, invocation = _invoke_all(
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
        # 本轮自定义算子符号的来源包（由 `_bind_vendor` 在任何算子调用前实际设入进程环境的值）。
        # 它和 `vendor.library_path` 是同源的两面：门会用同一条规则重算并逐字对账。
        RUNTIME_CUSTOM_OPP_KEY: custom_opp,
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
        # 本轮逐 case 执行的分母台账：`failed > 0` 时 receipt 自己就说得出「哪些没跑成」，
        # 不必翻 out_manifest 才知道这一轮不是满堂彩（AGENTS.md 5.8）。
        "invocation": invocation,
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
    # 性能是**另一个进程**：精度阶段设进环境的 ASCEND_CUSTOM_OPP_PATH 不会自己跟过来。
    # 这里按同一条规则重新绑定，并要求与精度阶段 receipt 记下的值逐字相同——两阶段测的
    # 必须是同一个 vendor 包的符号，否则「精度验 A、性能测 B」这类假象没有任何门看得出来。
    recorded = (receipt.get("runtime") or {}).get(RUNTIME_CUSTOM_OPP_KEY)
    bound = bind_custom_opp_path(vendor_path)
    if recorded != bound:
        raise DriverError(
            f"性能阶段自定义算子来源包与精度阶段 receipt 不一致："
            f"receipt.runtime.{RUNTIME_CUSTOM_OPP_KEY}={recorded!r}，本阶段反推 {bound!r}")
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
