#!/usr/bin/env python3
"""容器内官方 C++ Extension build/load/invoke driver。

由 ``cpp_extension_adapter`` 以 argv 调用。它只在已经准备好的 bundle/work 上工作：
build_ext --inplace → 加载精确 ELF → 逐 case 调独立 torch.ops entrypoint → 落
out_manifest 与内容寻址 receipt。机器连接、容器进入、文件同步由 argv 外层负责。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile

import cann_version
import cpp_extension_adapter
import cpp_extension_identity
import tensor_shape_attrs
import vendor_build_receipt


class DriverError(RuntimeError):
    pass


class LayoutContractError(DriverError):
    """布局身份/物理 transport 漂移；必须中止整轮，不能降格成某 case 的精度失败。"""


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


#: 输出缓冲的运行期预填值。键集必须与 ``_TORCH_DTYPES`` 完全相同；新增 dtype 若没有
#: 可识别的预填值，宁可在分配前 fail-closed，也不能退回未初始化的 ``torch.empty``。
#: tuple[0] 是实际填充值，tuple[1] 是可安全写入 JSON 的诊断表示。
_OUTPUT_SENTINELS = {
    "float32": (float("nan"), "nan"),
    "float16": (float("nan"), "nan"),
    "bfloat16": (float("nan"), "nan"),
    "int64": (0x5A5A5A5A, "0x5a5a5a5a"),
    "int32": (0x5A5A5A5A, "0x5a5a5a5a"),
    "int16": (0x5A5A, "0x5a5a"),
    "int8": (0x5A, "0x5a"),
    "uint8": (0x5A, "0x5a"),
    "uint32": (0x5A5A5A5A, "0x5a5a5a5a"),
    "complex64": (complex(float("nan"), float("nan")), "complex(nan,nan)"),
    # bool 只有两个合法值，无法选择一个“不可能是真输出”的哨兵；仍用已知值初始化，
    # 但回读必须显式记 skipped_bool，不能把它包装成通过了写入检查。
    "bool": (False, "false"),
}


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class _AclCannPackageVersion(ctypes.Structure):
    """``aclCANNPackageVersion`` 的 ABI 镜像（acl/acl_rt.h）。"""

    _fields_ = [
        ("version", ctypes.c_char * 128),
        ("majorVersion", ctypes.c_char * 64),
        ("minorVersion", ctypes.c_char * 64),
        ("releaseVersion", ctypes.c_char * 64),
        ("patchVersion", ctypes.c_char * 64),
        ("reserved", ctypes.c_char * 128),
    ]


class _DlInfo(ctypes.Structure):
    _fields_ = [
        ("dli_fname", ctypes.c_char_p),
        ("dli_fbase", ctypes.c_void_p),
        ("dli_sname", ctypes.c_char_p),
        ("dli_saddr", ctypes.c_void_p),
    ]


def _defining_elf(symbol):
    """用 ``dladdr`` 取得当前进程该函数指针的实际定义 ELF，并现场摘要。"""
    libdl_name = ctypes.util.find_library("dl") or "libdl.so.2"
    libdl = ctypes.CDLL(libdl_name)
    dladdr = libdl.dladdr
    dladdr.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DlInfo)]
    dladdr.restype = ctypes.c_int
    info = _DlInfo()
    address = ctypes.cast(symbol, ctypes.c_void_p)
    if dladdr(address, ctypes.byref(info)) == 0 or not info.dli_fname:
        raise DriverError("dladdr 无法定位 aclsysGetCANNVersion 的定义 ELF")
    try:
        decoded = info.dli_fname.decode("utf-8", "strict")
    except UnicodeDecodeError as ex:
        raise DriverError("ACL runtime 定义 ELF 路径不是 UTF-8") from ex
    path = os.path.realpath(decoded)
    if not os.path.isabs(path) or not os.path.isfile(path):
        raise DriverError(f"ACL runtime 定义 ELF 不存在或非绝对普通文件：{path!r}")
    return {"path": path, "sha256": _sha_file(path)}


def probe_runtime_cann_version():
    """调用当前进程实际加载的 AscendCL API；失败也返回结构化 unknown。

    禁止回退 ``CANN_VERSION`` / ``ASCEND_TOOLKIT_VERSION`` 或版本文件：那些值只能
    说明环境/安装树自称什么，不能证明本进程正在调用哪一个 runtime ELF。
    """
    probe = {
        "api": cann_version.PROBE_API,
        "package": cann_version.PROBE_PACKAGE,
        "returncode": None,
        "returncode_source": cann_version.PROBE_NOT_CALLED,
        "defining_elf": None,
    }
    try:
        acl = ctypes.CDLL("libascendcl.so", mode=ctypes.RTLD_GLOBAL)
        fn = getattr(acl, cann_version.PROBE_API)
        fn.argtypes = [ctypes.c_int, ctypes.POINTER(_AclCannPackageVersion)]
        fn.restype = ctypes.c_int
        probe["defining_elf"] = _defining_elf(fn)
        version = _AclCannPackageVersion()
        # ACL_PKG_NAME_CANN 是 aclCANNPackageName 的第一个枚举值（0）。收据同时记录
        # 受控 token；adapter 校 token，不把这个数字当另一份版本语义真源。
        rc = int(fn(0, ctypes.byref(version)))
        probe["returncode"] = rc
        probe["returncode_source"] = cann_version.PROBE_RETURN_MEASURED
        if rc != 0:
            return cann_version.unknown_observation(
                probe, f"{cann_version.PROBE_API} 返回非零 rc={rc}")
        raw = bytes(version.version).split(b"\0", 1)[0].decode("utf-8", "strict")
        observation = cann_version.normalize_observation(raw)
        observation["probe"] = probe
        if observation["status"] == cann_version.OBS_INVALID:
            observation["error"] = f"ACL API 返回的 CANN version 无法解析：{raw!r}"
        cann_version.validate_observation_record(observation)
        return observation
    except Exception as ex:  # noqa: BLE001 —— 探针失败须落证，不得让 env 自报顶上
        return cann_version.unknown_observation(
            probe, f"{type(ex).__name__}: {ex}")


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
        vendor_build_receipt.validate_for_acceptance(
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
    try:
        handle, symbol_identity = cpp_extension_identity.attest(vendor, plan)
        identity_summary = cpp_extension_identity.validate(
            symbol_identity, invocation_plan=plan,
            library_path=vendor, library_sha256=_sha_file(vendor))
    except cpp_extension_identity.CppExtensionIdentityError as ex:
        raise DriverError(f"DUT 双符号/实际定义 ELF 身份门未过：{ex}") from ex
    return handle, custom_opp, {
        "library_path": vendor,
        "library_sha256": _sha_file(vendor),
        # 兼容展示字段；不再是自报清单，而是从下方 dladdr 身份收据派生的双符号全集。
        "symbols_owned": identity_summary["symbols"],
        "symbol_identity": symbol_identity,
        "binding": (
            "ctypes.CDLL(exact_path, RTLD_GLOBAL) + per-symbol dladdr "
            "before torch.ops.load_library"),
        "build_receipt": build_provenance,
        "build_receipt_sha256": _canonical_sha(build_provenance),
        # 源身份摘要（含 `degradations` 机读挂账）。它是 build_receipt 的**派生视图**，
        # 不是新事实：离线复核方会用同一个函数重算并逐字比对，谁改一处都对不上。
        # 落这一份的理由是可见性：`pr_head_unbound` 这类降级必须在报告的第一层就看得见，
        # 而不是埋在嵌套收据里等人自己翻（AGENTS.md 5.8）。
        "source_provenance": vendor_build_receipt.summarize(build_provenance),
    }


def _validate_input_slot(item, slot, case_id):
    if not isinstance(item, dict) or not isinstance(slot, dict):
        raise DriverError(f"{case_id}: input item/slot 须为 object")
    expected = {
        key: item.get(key)
        for key in ("name", "kind", "binding", "shape", "dtype", "format")
    }
    actual = {key: slot.get(key) for key in expected}
    if actual != expected:
        raise DriverError(
            f"{case_id}: plan slot 与 caseset input 契约不一致：slot={actual}, input={expected}")
    if expected["kind"] != "tensor" or expected["binding"] != "device_tensor":
        raise DriverError(
            f"{case_id}: input {expected['name']!r} 非 tensor/device_tensor")
    shape = expected["shape"]
    if not isinstance(shape, list) or any(
            isinstance(dim, bool) or not isinstance(dim, int) or dim < 0 for dim in shape):
        raise DriverError(
            f"{case_id}: input {expected['name']!r} shape={shape!r} 非非负整数数组")
    if expected["format"] not in ("nd", "torch_npu_rank_default"):
        raise DriverError(
            f"{case_id}: input {expected['name']!r} format={expected['format']!r} 非受控值")
    layout_fields = (
        "storage_representation", "layout_requirement_id", "layout_receipt_sha256")
    layout_enabled = any(key in item for key in (
        "storage_representation", "base_storage_path", "layout_requirement_id",
        "layout_receipt", "layout_receipt_sha256"))
    if layout_enabled:
        if item.get("storage_representation") != cpp_extension_adapter.BASE_STORAGE_V1:
            raise LayoutContractError(
                f"{case_id}: input {expected['name']!r} storage representation 非 base_storage_v1")
        if "path" in item:
            raise LayoutContractError(
                f"{case_id}: base_storage_v1 input 不得同时携带 legacy path")
        expected_layout = {key: item.get(key) for key in layout_fields}
        actual_layout = {key: slot.get(key) for key in layout_fields}
        if actual_layout != expected_layout:
            raise LayoutContractError(
                f"{case_id}: plan slot 与 caseset input layout/requirement/digest 不一致")
        if expected["format"] != tensor_shape_attrs.TENSOR_FORMAT_ND:
            raise LayoutContractError(
                f"{case_id}: N7 v1 layout input 只支持 format=nd")
    elif any(key in slot for key in layout_fields):
        raise LayoutContractError(
            f"{case_id}: legacy input slot 不得凭空声明 layout 字段")


def _input_tensor(torch, np, work, item, *, slot=None, case_id=None):
    if slot is not None:
        _validate_input_slot(item, slot, case_id or item.get("name") or "<unknown-case>")
    layout_enabled = item.get("storage_representation") is not None
    storage_path = item.get("base_storage_path") if layout_enabled else item.get("path")
    if layout_enabled and item.get("storage_representation") != cpp_extension_adapter.BASE_STORAGE_V1:
        raise LayoutContractError(
            f"{case_id}: storage_representation={item.get('storage_representation')!r} 非受控值")
    arr = np.load(_safe(work, storage_path), allow_pickle=False)
    declared_shape = item.get("shape")
    if not layout_enabled and declared_shape is not None and list(arr.shape) != declared_shape:
        raise DriverError(
            f"{case_id or item.get('name')}: {item.get('name')!r} 落盘 shape={list(arr.shape)} "
            f"≠ 逐输入契约 {declared_shape}")
    if layout_enabled:
        receipt = item.get("layout_receipt")
        expected_numel = (receipt or {}).get("base_storage_numel")
        if list(arr.shape) != [expected_numel]:
            raise LayoutContractError(
                f"{case_id}: {item.get('name')!r} base storage shape={list(arr.shape)} "
                f"≠ [{expected_numel}]")
        if not bool(arr.flags.c_contiguous):
            raise LayoutContractError(
                f"{case_id}: base storage .npy 必须是一维 contiguous 存储")
    dtype = item["dtype"]
    if dtype == "bfloat16":
        if str(arr.dtype) != "uint16":
            raise DriverError(
                f"{item.get('name')}: bf16 输入 storage 须为 uint16，得 {arr.dtype}")
        tensor = torch.from_numpy(arr if layout_enabled else np.ascontiguousarray(arr)).view(
            torch.bfloat16)
    else:
        name = _TORCH_DTYPES.get(dtype)
        if name is None:
            raise DriverError(f"不支持输入 dtype={dtype!r}")
        tensor = torch.from_numpy(arr if layout_enabled else np.ascontiguousarray(arr))
        target = getattr(torch, name)
        if tensor.dtype != target:
            raise DriverError(
                f"{item.get('name')}: numpy storage dtype→torch {tensor.dtype} ≠ {target}")
    base = tensor.npu()
    if not layout_enabled:
        return base
    receipt = item["layout_receipt"]
    view = torch.as_strided(
        base,
        size=tuple(receipt["logical_shape"]),
        stride=tuple(receipt["strides"]),
        storage_offset=receipt["storage_offset"],
    )
    _observe_runtime_layout(
        view, item, role=tensor_shape_attrs.LAYOUT_ROLE_INPUT,
        case_id=case_id, tensor_name=item.get("name"),
        tensor_index=slot.get("input_idx") if isinstance(slot, dict) else 0)
    return view


def _runtime_storage(tensor):
    if hasattr(tensor, "untyped_storage"):
        return tensor.untyped_storage(), True
    return tensor.storage(), False


def _runtime_base_numel(tensor):
    storage, untyped = _runtime_storage(tensor)
    if untyped:
        return int(storage.nbytes()) // int(tensor.element_size())
    return int(storage.size())


def _runtime_storage_ptr(tensor):
    storage, _untyped = _runtime_storage(tensor)
    return int(storage.data_ptr())


def _observe_runtime_layout(tensor, contract, *, role, case_id,
                            tensor_name, tensor_index):
    """从真实 NPU tensor 元数据重建 receipt，并与外部 case receipt/digest 对账。"""
    shape = (contract.get("shape") if role == tensor_shape_attrs.LAYOUT_ROLE_INPUT
             else contract.get("out_shape"))
    try:
        is_contiguous = bool(tensor.is_contiguous())
        observed = {
            "case_id": case_id,
            "tensor_name": tensor_name,
            "tensor_index": tensor_index,
            "role": role,
            "format": tensor_shape_attrs.TENSOR_FORMAT_ND,
            "logical_shape": list(tensor.shape),
            "layout_kind": (
                tensor_shape_attrs.LAYOUT_CONTIGUOUS if is_contiguous
                else tensor_shape_attrs.LAYOUT_NONCONTIGUOUS),
            "layout_capability": (
                tensor_shape_attrs.LAYOUT_CAPABILITY_CONTIGUOUS if is_contiguous
                else tensor_shape_attrs.LAYOUT_CAPABILITY_SPAN_SEPARABLE),
            "strides": list(tensor.stride()),
            "storage_offset": int(tensor.storage_offset()),
            "base_storage_numel": _runtime_base_numel(tensor),
        }
        receipt = tensor_shape_attrs.assert_layout_preserved(
            contract.get("layout_receipt"), observed,
            expected_shape=shape,
            expected_role=role,
            expected_case_id=case_id,
            expected_tensor_name=tensor_name,
            expected_tensor_index=tensor_index,
            expected_receipt_sha256=contract.get("layout_receipt_sha256"),
            where=f"{case_id}.{role}[{tensor_index}]",
        )
    except (tensor_shape_attrs.TensorShapeAttrError, AttributeError, TypeError, ValueError) as ex:
        raise LayoutContractError(
            f"{case_id}: {role} tensor#{tensor_index} 实际布局未保持：{ex}") from ex
    return {
        "layout_receipt": receipt,
        "layout_receipt_sha256": tensor_shape_attrs.layout_receipt_sha256(receipt),
        "storage_data_ptr": _runtime_storage_ptr(tensor),
    }


def _assert_layout_observation_roundtrip(before, after, where):
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise LayoutContractError(f"{where}: 调用前后布局 observation 缺失")
    if before.get("storage_data_ptr") != after.get("storage_data_ptr"):
        raise LayoutContractError(f"{where}: base storage ptr 调用前后漂移")
    for key in ("layout_receipt", "layout_receipt_sha256"):
        if _canonical_sha(before.get(key)) != _canonical_sha(after.get(key)):
            raise LayoutContractError(f"{where}: {key} 调用前后漂移")
    return after


def _expected_outputs(case):
    expected = case.get("expected") or {}
    outputs = expected.get("outputs")
    if isinstance(outputs, list):
        return outputs
    if any(key in expected for key in (
            "layout_requirement_id", "layout_receipt", "layout_receipt_sha256")):
        output = dict(expected)
        output.setdefault("role", "value")
        if not output.get("name"):
            slot = next((item for item in (
                (case.get("aclnn_call") or {}).get("slots") or [])
                if isinstance(item, dict) and item.get("role") == "out"
                and item.get("output_idx") == 0), None)
            output["name"] = slot.get("name") if isinstance(slot, dict) else None
        return [output]
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


def _empty_output(torch, output, *, case_id=None, output_index=None):
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
    sentinel = _OUTPUT_SENTINELS.get(dtype_name)
    if sentinel is None:
        raise DriverError(
            f"输出 {output.get('name')}: dtype={dtype_name!r} 缺输出写入哨兵——"
            "能力表已扩展但可信门未同步，fail-closed")
    layout_enabled = any(key in output for key in (
        "layout_requirement_id", "layout_receipt", "layout_receipt_sha256"))
    # 在 CPU 上构造已知字节再搬到 NPU：本通路的输入 transport 已逐 dtype 见证；相比直接在
    # NPU 上调用 fill kernel，这不额外假定 uint32/complex64 的设备端 fill 能力。
    if not layout_enabled:
        return torch.full(
            tuple(shape), sentinel[0], dtype=getattr(torch, torch_name), device="cpu").npu()
    if case_id is None or output_index is None:
        raise LayoutContractError("layout output 分配缺 case_id/output_index 外部身份")
    receipt = output.get("layout_receipt")
    if not isinstance(receipt, dict):
        raise LayoutContractError(
            f"{case_id}: output#{output_index} 缺外部 layout_receipt")
    base = torch.full(
        (receipt.get("base_storage_numel"),), sentinel[0],
        dtype=getattr(torch, torch_name), device="cpu").npu()
    view = torch.as_strided(
        base,
        size=tuple(receipt["logical_shape"]),
        stride=tuple(receipt["strides"]),
        storage_offset=receipt["storage_offset"],
    )
    _observe_runtime_layout(
        view, output, role=tensor_shape_attrs.LAYOUT_ROLE_OUTPUT,
        case_id=case_id, tensor_name=output.get("name"),
        tensor_index=output_index)
    return view


OUTPUT_CHECK_PASSED = "passed"
OUTPUT_CHECK_SKIPPED_BOOL = "skipped_bool"
OUTPUT_CHECK_SKIPPED_EMPTY = "skipped_empty"
OUTPUT_CHECK_FAILED_ALL_SENTINEL = "failed_all_sentinel"


def _output_written_check(torch, tensor, contract, *, case_id, output_index):
    """在任何 dtype 转换/落盘前确认输出不再是整块预填哨兵；返回 JSON-safe 诊断。"""
    dtype_name = contract.get("compare_dtype")
    sentinel = _OUTPUT_SENTINELS.get(dtype_name)
    if sentinel is None:
        raise DriverError(
            f"{case_id}: 输出#{output_index} dtype={dtype_name!r} 缺输出写入哨兵，fail-closed")
    value = tensor.detach().contiguous().cpu()
    numel = int(value.numel())
    diagnostic = {
        "status": None,
        "output_index": output_index,
        "output_name": contract.get("name"),
        "dtype": dtype_name,
        "sentinel": sentinel[1],
        "numel": numel,
        "sentinel_hits": None,
        "hit_ratio": None,
    }
    if numel == 0:
        diagnostic["status"] = OUTPUT_CHECK_SKIPPED_EMPTY
        return diagnostic
    if dtype_name == "bool":
        diagnostic["status"] = OUTPUT_CHECK_SKIPPED_BOOL
        return diagnostic
    if dtype_name in ("float32", "float16", "bfloat16"):
        matches = torch.isnan(value)
    elif dtype_name == "complex64":
        # torch.isnan(complex) 在任一分量为 NaN 时即为真；这里要求实部、虚部都仍是预填 NaN。
        matches = torch.isnan(value.real) & torch.isnan(value.imag)
    else:
        matches = value == sentinel[0]
    hits = int(matches.sum().item())
    diagnostic["sentinel_hits"] = hits
    diagnostic["hit_ratio"] = hits / numel
    if hits == numel:
        diagnostic["status"] = OUTPUT_CHECK_FAILED_ALL_SENTINEL
        raise OutputNotWrittenError(
            f"{case_id}: 输出#{output_index}({contract.get('name')}) 的 {numel} 个元素"
            f"全部仍等于预填哨兵 {sentinel[1]}——DUT 未写入该输出缓冲；"
            "这是 harness/调用侧故障，不是算子精度问题",
            diagnostic)
    diagnostic["status"] = OUTPUT_CHECK_PASSED
    return diagnostic


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
    cid = case.get("id")
    parameter_contract = case.get("parameter_contract")
    parameter_digest = row.get("parameter_contract_sha256")
    if (parameter_contract is None) != (parameter_digest is None):
        raise DriverError(
            f"{cid}: case.parameter_contract 与 plan.parameter_contract_sha256 在场性不一致")
    if parameter_digest is not None and _canonical_sha(parameter_contract) != parameter_digest:
        raise DriverError(f"{cid}: parameter_contract 摘要与 invocation plan 漂移")
    input_slots = {}
    for slot in row["slots"]:
        if slot.get("role") != "in":
            continue
        index = slot.get("input_idx")
        if isinstance(index, bool) or not isinstance(index, int) or index in input_slots:
            raise DriverError(f"{cid}: input_idx={index!r} 非唯一整数")
        input_slots[index] = slot
    if set(input_slots) != set(range(len(case["inputs"]))):
        raise DriverError(
            f"{cid}: plan input_idx={sorted(input_slots)} 未完整覆盖 "
            f"case.inputs[0..{len(case['inputs']) - 1}]")
    inputs = [
        _input_tensor(
            torch, np, work, item,
            slot=(input_slots[index] if parameter_digest is not None
                  or item.get("storage_representation") is not None else None),
            case_id=cid)
        for index, item in enumerate(case["inputs"])
    ]
    output_contracts = _expected_outputs(case)
    outputs = [_empty_output(
        torch, item, case_id=cid, output_index=index)
        for index, item in enumerate(output_contracts)]
    scalar_dtypes = row.get("host_scalar_dtypes") or {}
    active_attr_contracts = row.get("active_attr_contracts")
    if active_attr_contracts is not None:
        actual_attr_contracts = [{
            "name": slot.get("name"), "attr_ctype": slot.get("ctype")}
            for slot in row["slots"] if slot.get("role") == "attr"]
        if actual_attr_contracts != active_attr_contracts:
            raise DriverError(
                f"{cid}: invocation plan attr slot/name/ctype 漂移")
    args = []
    for slot_index, slot in enumerate(row["slots"]):
        role = slot["role"]
        if role == "in":
            args.append(inputs[int(slot["input_idx"])])
        elif role == "attr":
            if active_attr_contracts is not None:
                attr_type = (tensor_shape_attrs.ATTR_INT_ARRAY
                             if slot.get("ctype") == "int_array"
                             else tensor_shape_attrs.ATTR_SCALAR)
                try:
                    tensor_shape_attrs.normalize_attr_value(
                        slot.get("value"), attr_type=attr_type,
                        where=f"{cid}.slots[{slot_index}].value")
                except tensor_shape_attrs.TensorShapeAttrError as ex:
                    raise DriverError(f"{cid}: attr slot 值违反显式类型：{ex}") from ex
            if slot.get("binding") == "host_scalar":
                name = slot.get("name")
                if slot.get("ctype") != "scalar" or slot.get("kind") != "scalar" \
                        or scalar_dtypes.get(name) != slot.get("dtype"):
                    raise DriverError(
                        f"{cid}: host scalar slot {name!r} 与 entrypoint dtype 契约不一致")
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
FAILED_NOT_WRITTEN = "output_not_written"             # 读回成功，但整块仍是预填哨兵
_FAILED_KIND_BY_PHASE = {
    "materialize": FAILED_MATERIALIZE,
    "execute": FAILED_EXECUTE,
    "readback": FAILED_READBACK,
}


class OutputNotWrittenError(DriverError):
    """让 readback 阶段覆盖通用 phase→kind 映射，并携带可独立复核的诊断。"""

    error_kind = FAILED_NOT_WRITTEN

    def __init__(self, message, diagnostic):
        super().__init__(message)
        self.output_written_diagnostic = diagnostic


def _layout_enabled(value):
    return isinstance(value, dict) and any(key in value for key in (
        "layout_requirement_id", "layout_receipt", "layout_receipt_sha256"))


def _capture_layout_before(case, row, args, outputs, output_contracts):
    cid = case["id"]
    input_arg_positions = {
        int(slot["input_idx"]): position
        for position, slot in enumerate(row["slots"])
        if slot.get("role") == "in"
    }
    record = {"case_id": cid, "inputs": [], "outputs": []}
    for index, item in enumerate(case.get("inputs") or []):
        if not _layout_enabled(item):
            continue
        observation = _observe_runtime_layout(
            args[input_arg_positions[index]], item,
            role=tensor_shape_attrs.LAYOUT_ROLE_INPUT,
            case_id=cid, tensor_name=item.get("name"), tensor_index=index)
        record["inputs"].append({
            "layout_requirement_id": item["layout_requirement_id"],
            "tensor_index": index,
            "name": item["name"],
            "expected_layout_receipt_sha256": item["layout_receipt_sha256"],
            "before": observation,
            "after": None,
        })
    for index, item in enumerate(output_contracts):
        if not _layout_enabled(item):
            continue
        observation = _observe_runtime_layout(
            outputs[index], item,
            role=tensor_shape_attrs.LAYOUT_ROLE_OUTPUT,
            case_id=cid, tensor_name=item.get("name"), tensor_index=index)
        record["outputs"].append({
            "layout_requirement_id": item["layout_requirement_id"],
            "tensor_index": index,
            "name": item["name"],
            "expected_layout_receipt_sha256": item["layout_receipt_sha256"],
            "before": observation,
            "after": None,
        })
    return record if record["inputs"] or record["outputs"] else None


def _capture_layout_after(case, row, args, returned, output_contracts, record):
    if record is None:
        return None
    cid = case["id"]
    input_arg_positions = {
        int(slot["input_idx"]): position
        for position, slot in enumerate(row["slots"])
        if slot.get("role") == "in"
    }
    for item in record["inputs"]:
        index = item["tensor_index"]
        contract = case["inputs"][index]
        item["after"] = _observe_runtime_layout(
            args[input_arg_positions[index]], contract,
            role=tensor_shape_attrs.LAYOUT_ROLE_INPUT,
            case_id=cid, tensor_name=item["name"], tensor_index=index)
        _assert_layout_observation_roundtrip(
            item["before"], item["after"], f"{cid}.inputs[{index}]")
    for item in record["outputs"]:
        index = item["tensor_index"]
        contract = output_contracts[index]
        item["after"] = _observe_runtime_layout(
            returned[index], contract,
            role=tensor_shape_attrs.LAYOUT_ROLE_OUTPUT,
            case_id=cid, tensor_name=item["name"], tensor_index=index)
        _assert_layout_observation_roundtrip(
            item["before"], item["after"], f"{cid}.outputs[{index}]")
    return record


def _invoke_all(bundle, work, manifest, plan, caseset, artifact, *, layout_contract=None):
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
    layout_cases = []
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
            layout_record = _capture_layout_before(
                case, row, args, outputs, output_contracts)
            phase = "execute"
            result = getattr(namespace, row["entrypoint"])(*args)
            torch.npu.synchronize()
            returned = list(result)
            if len(returned) != len(outputs):
                raise DriverError(
                    f"{case['id']}: Extension 返回 {len(returned)} 输出，期望 {len(outputs)}")
            layout_record = _capture_layout_after(
                case, row, args, returned, output_contracts, layout_record)
            phase = "readback"
            os.makedirs(cdir)
            out_rows = []
            for index, (tensor, contract) in enumerate(zip(returned, output_contracts)):
                rel = f"{case['id']}/out_{index}.bin"
                written = _output_written_check(
                    torch, tensor, contract, case_id=case["id"], output_index=index)
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
                    "output_written_check": written["status"],
                    "output_written_diagnostic": written,
                })
        except Exception as ex:  # noqa: BLE001 —— 单条 case 的任何失败都只归这条，不带走整轮
            if isinstance(ex, LayoutContractError):
                # 布局漂移不是 DUT 数值失败：一旦降格进 failed[]，后续 precision/perf 仍可能消费
                # 已被 contiguous/换槽的张量。必须整轮 fail-closed，不产 VERIFIED receipt。
                raise
            if os.path.isdir(cdir):
                # 半截产物必须清掉：下游按 manifest 读字节，留一堆残缺 out_k.bin 只会制造
                # 「看起来有产物」的假象。
                shutil.rmtree(cdir, ignore_errors=True)
            failure = {
                "case_id": case["id"],
                "entrypoint": row["entrypoint"],
                "phase": phase,
                "error_kind": getattr(ex, "error_kind", _FAILED_KIND_BY_PHASE[phase]),
                "error_type": type(ex).__name__,
                # **逐字原文**：不截断、不改写、不翻译。归因要拿得出原话（AGENTS.md 5.8）。
                "error": str(ex),
            }
            diagnostic = getattr(ex, "output_written_diagnostic", None)
            if diagnostic is not None:
                # 半截 out_k.bin 仍按既有纪律删除；可复核物证留在 failed[]，避免残缺目录被
                # 下游误当成可比较产物，同时保留哨兵、numel、命中数/比例。
                failure["output_written_check"] = diagnostic["status"]
                failure["output_written_diagnostic"] = diagnostic
            failed.append(failure)
            last_case[0] = case["id"]
            _snapshot(False)
            _progress("running")
            continue
        produced_row = {"case_id": case["id"], "outputs": out_rows}
        if layout_record is not None:
            produced_row["layout_observations"] = layout_record
            layout_cases.append(layout_record)
        produced.append(produced_row)
        last_case[0] = case["id"]
        _snapshot(False)
        _progress("running")
    _snapshot(True)
    _progress("complete")
    invocation = {
        "planned": total, "produced": len(produced), "failed": len(failed),
        "failed_case_ids": [row["case_id"] for row in failed],
    }
    if layout_contract is not None:
        invocation["_layout_execution"] = {
            "schema": cpp_extension_adapter.LAYOUT_EXECUTION_SCHEMA,
            "schema_version": cpp_extension_adapter.LAYOUT_EXECUTION_VERSION,
            "layout_ledger_sha256": layout_contract["sha256"],
            "cases": layout_cases,
        }
    return torch, schemas, invocation


def run(bundle, work):
    bundle, work = os.path.realpath(bundle), os.path.realpath(work)
    if not os.path.isdir(bundle) or not os.path.isdir(work):
        raise DriverError("bundle/work 须为存在目录")
    manifest = _load(os.path.join(bundle, "extension_manifest.json"))
    plan = _load(os.path.join(work, "cpp_extension_invocation_plan.json"))
    caseset = _load(os.path.join(work, "cpp_extension_caseset.json"))
    if plan.get("caseset_sha256") != _canonical_sha(caseset):
        raise DriverError("invocation plan 与 caseset 摘要不一致")
    if (plan.get("manifest_sha256") != _canonical_sha(manifest)
            or plan.get("namespace") != manifest.get("namespace")):
        raise DriverError(
            "invocation plan 与 N2 闭合生成物 manifest 的摘要/namespace 不一致；"
            "正式 build receipt 不得消费漂移或 development 生成物")
    try:
        layout_contract = cpp_extension_adapter.validate_invocation_layout_contract(
            caseset, manifest, plan)
        structure_contract = (
            cpp_extension_adapter.validate_invocation_tensor_shape_attr_contract(
                caseset, plan))
    except cpp_extension_adapter.CppExtensionAdapterError as ex:
        raise LayoutContractError(
            f"caseset/plan tensor shape/attr/layout contract 非法：{ex}") from ex
    _handle, custom_opp, vendor = _bind_vendor(plan)
    build_argv, artifact = _build(bundle, manifest)
    torch, schemas, invocation = _invoke_all(
        bundle, work, manifest, plan, caseset, artifact,
        layout_contract=layout_contract)
    layout_execution = invocation.pop("_layout_execution", None)
    if layout_contract is not None:
        try:
            cpp_extension_adapter.validate_layout_execution(caseset, layout_execution)
        except cpp_extension_adapter.CppExtensionAdapterError as ex:
            raise LayoutContractError(
                f"driver 实际 layout execution 未闭合：{ex}") from ex

    artifact_rel = os.path.relpath(artifact, work).replace(os.sep, "/")
    if artifact_rel.startswith("../"):
        raise DriverError("Extension ELF 不在 work 根内")
    try:
        import torch_npu
        torch_npu_version = torch_npu.__version__
    except AttributeError:
        torch_npu_version = "unknown"
    cann_observation = probe_runtime_cann_version()
    runtime = {
        "torch_version": str(torch.__version__),
        "torch_npu_version": str(torch_npu_version),
        # 兼容既有报告/CP-F 的扁平展示字段；其值只能从下方 ACL runtime probe 的
        # 规范化结果派生，绝不再读取 CANN_VERSION/ASCEND_TOOLKIT_VERSION。
        "cann_version": cann_observation.get("normalized") or "unknown",
        "cann": cann_observation,
        "soc": os.environ.get("OPRUNWAY_SOC") or "unknown",
        # 本轮自定义算子符号的来源包（由 `_bind_vendor` 在任何算子调用前实际设入进程环境的值）。
        # 它和 `vendor.library_path` 是同源的两面：门会用同一条规则重算并逐字对账。
        RUNTIME_CUSTOM_OPP_KEY: custom_opp,
    }
    if any(runtime[key] == "unknown" for key in (
            "torch_version", "torch_npu_version", "soc", RUNTIME_CUSTOM_OPP_KEY)):
        raise DriverError(
            "runtime provenance 不完整；须提供 OPRUNWAY_SOC，且 torch/torch_npu 须可识别")
    receipt = {
        "schema": "oprunway.cpp_extension_receipt",
        "schema_version": cann_version.RECEIPT_SCHEMA_VERSION,
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
    # 显式 ND 才有这份收据；默认 rank-derived 通路为保持 legacy payload 字节不写键、不写 null。
    # 在场时逐字镜像，不在 driver 里猜默认或按算子分支。
    if "tensor_format_receipt" in manifest:
        receipt["tensor_format_receipt"] = manifest["tensor_format_receipt"]
    if "multi_input_receipt" in manifest:
        receipt["multi_input_receipt"] = manifest["multi_input_receipt"]
    if layout_contract is not None:
        receipt["layout_ledger_sha256"] = layout_contract["sha256"]
        receipt["layout_execution"] = layout_execution
    if structure_contract is not None:
        receipt["tensor_shape_attr_bindings_sha256"] = structure_contract["bindings_sha256"]
        if structure_contract["atomic_ledger_sha256"] is not None:
            receipt["atomic_attr_ledger_sha256"] = structure_contract["atomic_ledger_sha256"]
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
    try:
        layout_contract = cpp_extension_adapter.validate_caseset_layout_contract(caseset)
        if layout_contract is None:
            if "layout_ledger_sha256" in receipt or "layout_execution" in receipt:
                raise cpp_extension_adapter.CppExtensionAdapterError(
                    "legacy receipt 凭空声明 layout execution")
        else:
            if receipt.get("layout_ledger_sha256") != layout_contract["sha256"]:
                raise cpp_extension_adapter.CppExtensionAdapterError(
                    "receipt.layout_ledger_sha256 与 caseset 漂移")
            cpp_extension_adapter.validate_layout_execution(
                caseset, receipt.get("layout_execution"))
        structure_contract = (
            cpp_extension_adapter.validate_caseset_tensor_shape_attr_contract(caseset))
        if structure_contract is not None:
            structure_contract = (
                cpp_extension_adapter.validate_invocation_tensor_shape_attr_contract(
                    caseset, _load(os.path.join(
                        work, "cpp_extension_invocation_plan.json"))))
        if structure_contract is None:
            if ("tensor_shape_attr_bindings_sha256" in receipt
                    or "atomic_attr_ledger_sha256" in receipt):
                raise cpp_extension_adapter.CppExtensionAdapterError(
                    "legacy receipt 凭空声明 tensor shape/attr binding")
        else:
            if receipt.get("tensor_shape_attr_bindings_sha256") \
                    != structure_contract["bindings_sha256"]:
                raise cpp_extension_adapter.CppExtensionAdapterError(
                    "receipt.tensor_shape_attr_bindings_sha256 与 caseset 漂移")
            if receipt.get("atomic_attr_ledger_sha256") \
                    != structure_contract["atomic_ledger_sha256"]:
                raise cpp_extension_adapter.CppExtensionAdapterError(
                    "receipt.atomic_attr_ledger_sha256 与 caseset 漂移")
    except cpp_extension_adapter.CppExtensionAdapterError as ex:
        raise LayoutContractError(
            f"性能阶段前 tensor shape/attr/layout contract 未闭合：{ex}") from ex
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
    expected_vendor = receipt.get("vendor") or {}
    expected_perf_vendor = {
        key: expected_vendor.get(key)
        for key in ("library_path", "library_sha256", "symbols_owned", "symbol_identity")
    }
    if vendor != expected_perf_vendor:
        raise DriverError("性能计划的 vendor 双符号/实际 ELF 身份与精度阶段 receipt 漂移")
    try:
        cpp_extension_identity.validate(
            vendor.get("symbol_identity"), invocation_plan=invocation,
            library_path=vendor_path, library_sha256=vendor.get("library_sha256"))
        _perf_vendor_handle, actual_identity = cpp_extension_identity.attest(
            vendor_path, invocation)
    except cpp_extension_identity.CppExtensionIdentityError as ex:
        raise DriverError(f"性能阶段 DUT 身份门未过：{ex}") from ex
    if actual_identity != vendor.get("symbol_identity"):
        raise DriverError("性能阶段实际加载的双符号定义者与精度阶段身份收据漂移")
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
