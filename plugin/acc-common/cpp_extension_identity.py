#!/usr/bin/env python3
"""``cpp_extension`` DUT 的双符号/实际定义 ELF 身份证明（stdlib-only）。

本模块只回答执行身份，不执行算子、也不判精度或性能。对 invocation plan 中每个
``symbol`` 基名，正式通路都必须证明两段式 ACLNN 的两个函数：

* ``aclnn<symbol>GetWorkspaceSize``（workspace）；
* ``aclnn<symbol>``（stage2）。

``hasattr(CDLL, name)`` 只能证明动态加载器能从该 handle 的依赖搜索域找到名字，不能证明
名字由被测 vendor ELF 定义。这里对拿到的真实函数地址调用 ``dladdr``，并把定义 ELF 的
规范路径和 sha256 落成收据；定义者不是指定 vendor、缺任一符号或指纹不一致均 fail-closed。

driver、离线 adapter、三级门和 msprof 独立进程共用本文件，避免四份近似但不同的身份规则。
规则只依赖 invocation plan 的接口字段，不含任何算子身份分派。
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re


class CppExtensionIdentityError(ValueError):
    """DUT 的实际加载身份无法证明。"""


SCHEMA = "oprunway.cpp_extension_vendor_identity"
SCHEMA_VERSION = 1
ROLE_WORKSPACE = "workspace"
ROLE_STAGE2 = "stage2"
ROLES = (ROLE_WORKSPACE, ROLE_STAGE2)
RESOLVED_VIA = "ctypes.CDLL(exact_path, RTLD_GLOBAL)+dladdr"

_SYMBOL_BASE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")


class _DlInfo(ctypes.Structure):
    _fields_ = [
        ("dli_fname", ctypes.c_char_p),
        ("dli_fbase", ctypes.c_void_p),
        ("dli_sname", ctypes.c_char_p),
        ("dli_saddr", ctypes.c_void_p),
    ]


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_hex64(value, label):
    if not isinstance(value, str) or _HEX64_RE.fullmatch(value) is None:
        raise CppExtensionIdentityError(f"{label} 须为小写 64 位 sha256")
    return value


def required_symbol_pairs(invocation_plan):
    """由调用计划唯一派生 workspace/stage2 符号对；顺序确定且去重。"""
    if not isinstance(invocation_plan, dict):
        raise CppExtensionIdentityError("cpp_extension invocation plan 须为 object")
    cases = invocation_plan.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CppExtensionIdentityError(
            "cpp_extension invocation plan.cases 须为非空列表，无法派生 DUT 符号")
    bases = set()
    for index, row in enumerate(cases):
        base = row.get("symbol") if isinstance(row, dict) else None
        if (not isinstance(base, str) or not base
                or _SYMBOL_BASE_RE.fullmatch(base) is None
                or base.startswith("aclnn")):
            raise CppExtensionIdentityError(
                f"invocation plan.cases[{index}].symbol 须为不带 aclnn 前缀的 C 标识符基名，"
                f"得 {base!r}")
        bases.add(base)
    return [
        {
            "entrypoint": base,
            "workspace": f"aclnn{base}GetWorkspaceSize",
            "stage2": f"aclnn{base}",
        }
        for base in sorted(bases)
    ]


def required_symbols(invocation_plan):
    """返回身份收据中期望的逐符号记录骨架。"""
    result = []
    for pair in required_symbol_pairs(invocation_plan):
        result.extend((
            {"entrypoint": pair["entrypoint"], "role": ROLE_WORKSPACE,
             "symbol": pair["workspace"]},
            {"entrypoint": pair["entrypoint"], "role": ROLE_STAGE2,
             "symbol": pair["stage2"]},
        ))
    return result


def _defining_library(function):
    """用真实函数指针反查定义 ELF；任何 best-effort/unknown 都不够支撑正式验收。"""
    try:
        address = ctypes.cast(function, ctypes.c_void_p).value
    except (TypeError, ValueError) as ex:
        raise CppExtensionIdentityError("无法取得 ACLNN 函数指针地址") from ex
    if not address:
        raise CppExtensionIdentityError("ACLNN 函数指针地址为空")
    try:
        dladdr = ctypes.CDLL(None).dladdr
        dladdr.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DlInfo)]
        dladdr.restype = ctypes.c_int
        info = _DlInfo()
        found = dladdr(ctypes.c_void_p(address), ctypes.byref(info))
    except (AttributeError, OSError) as ex:
        raise CppExtensionIdentityError("当前运行时无法调用 dladdr 定位 ACLNN 定义 ELF") from ex
    if found == 0 or not info.dli_fname:
        raise CppExtensionIdentityError("dladdr 未返回 ACLNN 定义 ELF")
    try:
        raw = info.dli_fname.decode("utf-8", "strict")
    except UnicodeDecodeError as ex:
        raise CppExtensionIdentityError("dladdr 返回的定义 ELF 路径不是 UTF-8") from ex
    if not os.path.isabs(raw):
        raise CppExtensionIdentityError(f"dladdr 返回的定义 ELF 不是绝对路径：{raw!r}")
    path = os.path.realpath(raw)
    if not os.path.isfile(path) or os.path.islink(path):
        # realpath 后一般不会再是 symlink；保留显式检查，防异常文件系统语义。
        raise CppExtensionIdentityError(f"dladdr 定义 ELF 不存在或非普通文件：{path!r}")
    return path


def attest(library_path, invocation_plan):
    """加载精确 vendor ELF 并产双符号定义者收据；返回 ``(handle, receipt)``。"""
    if (not isinstance(library_path, str) or not os.path.isabs(library_path)
            or not os.path.isfile(library_path)):
        raise CppExtensionIdentityError("vendor library 须为存在的绝对普通文件")
    library_path = os.path.realpath(library_path)
    library_sha256 = file_sha256(library_path)
    expected = required_symbols(invocation_plan)
    try:
        handle = ctypes.CDLL(library_path, mode=ctypes.RTLD_GLOBAL)
    except OSError as ex:
        raise CppExtensionIdentityError(
            f"无法加载指定 vendor library {library_path!r}: {ex}") from ex
    definitions = []
    for item in expected:
        try:
            function = getattr(handle, item["symbol"])
        except AttributeError as ex:
            raise CppExtensionIdentityError(
                f"指定 vendor library 缺 {item['role']} 符号 {item['symbol']!r}") from ex
        defining_path = _defining_library(function)
        defining_sha = file_sha256(defining_path)
        if defining_path != library_path:
            raise CppExtensionIdentityError(
                f"{item['symbol']} 虽可解析，但实际定义 ELF={defining_path!r}，"
                f"不是本轮 DUT vendor={library_path!r}（依赖/全局命名空间污染）")
        if defining_sha != library_sha256:
            raise CppExtensionIdentityError(
                f"{item['symbol']} 的定义 ELF 指纹与本轮 DUT vendor 不一致")
        definitions.append({
            **item,
            "resolved_via": RESOLVED_VIA,
            "defining_library": {
                # 这里写的是 dladdr 实测值；上方已要求它与 vendor realpath 逐字一致。
                # 不把它覆盖成 expected path，否则路径别名会被收据悄悄抹平。
                "path": defining_path,
                "sha256": defining_sha,
            },
        })
    receipt = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "library": {"path": library_path, "sha256": library_sha256},
        "required_symbol_pairs": required_symbol_pairs(invocation_plan),
        "definitions": definitions,
    }
    # 生产者也走与离线消费方相同的确定性校验；未来 schema 漂移会在现场当场暴露。
    validate(receipt, invocation_plan=invocation_plan,
             library_path=library_path, library_sha256=library_sha256)
    return handle, receipt


def validate(receipt, *, invocation_plan, library_path, library_sha256):
    """离线复核身份收据与 invocation plan / vendor 指纹逐字一致。"""
    if not isinstance(receipt, dict):
        raise CppExtensionIdentityError("vendor.symbol_identity 缺失或非 object")
    if receipt.get("schema") != SCHEMA or receipt.get("schema_version") != SCHEMA_VERSION:
        raise CppExtensionIdentityError(
            f"vendor.symbol_identity 须为 {SCHEMA} v{SCHEMA_VERSION}")
    if not isinstance(library_path, str) or not library_path:
        raise CppExtensionIdentityError("vendor.library_path 缺失")
    _require_hex64(library_sha256, "vendor.library_sha256")
    expected_pairs = required_symbol_pairs(invocation_plan)
    expected = required_symbols(invocation_plan)
    if receipt.get("required_symbol_pairs") != expected_pairs:
        raise CppExtensionIdentityError(
            "vendor.symbol_identity.required_symbol_pairs 与 invocation plan 漂移")
    library = receipt.get("library")
    if (not isinstance(library, dict)
            or library.get("path") != library_path
            or library.get("sha256") != library_sha256):
        raise CppExtensionIdentityError(
            "vendor.symbol_identity.library 与实际绑定的 vendor 路径/指纹不一致")
    definitions = receipt.get("definitions")
    if not isinstance(definitions, list) or len(definitions) != len(expected):
        raise CppExtensionIdentityError(
            "vendor.symbol_identity.definitions 未完整覆盖 workspace/stage2 双符号")
    wanted = []
    for item in expected:
        wanted.append({
            **item,
            "resolved_via": RESOLVED_VIA,
            "defining_library": {
                "path": library_path,
                "sha256": library_sha256,
            },
        })
    if definitions != wanted:
        raise CppExtensionIdentityError(
            "vendor 双符号定义者/ELF 指纹与 invocation plan、实际加载对象不逐字一致")
    return {
        "library_path": library_path,
        "library_sha256": library_sha256,
        "symbols": [item["symbol"] for item in expected],
        "symbol_pairs": expected_pairs,
    }


def assert_distinct_library(dut_identity, other_path, other_sha256, *, label="标杆"):
    """通用 DUT/对照物隔离门：路径或 sha 同源均拒，不能把自己与自己作比较。"""
    if not isinstance(dut_identity, dict):
        raise CppExtensionIdentityError("DUT symbol identity 缺失，无法做 ELF 隔离")
    dut_library = dut_identity.get("library")
    if not isinstance(dut_library, dict):
        raise CppExtensionIdentityError("DUT symbol identity.library 缺失")
    _require_hex64(dut_library.get("sha256"), "DUT identity.library.sha256")
    _require_hex64(other_sha256, f"{label} ELF sha256")
    if dut_library.get("path") == other_path or dut_library.get("sha256") == other_sha256:
        raise CppExtensionIdentityError(
            f"DUT 与{label} ELF 同源（路径或 sha256 相同），禁止自己与自己比较")
    return True


def validate_required_symbol_library(provenance, *, expected_entrypoint=None):
    """校 ``AclnnRunner(required_symbol_lib=...)`` 的标杆双符号实际定义者。

    该 runner 已在真机用 ``dladdr`` 取证；本函数负责离线把两条记录收紧为一个完整
    workspace/stage2 对，并要求逐符号路径/sha 与指定标杆 ELF 完全相同。接口名可由调用计划
    传入；省略时也必须从两条符号自身唯一反推出同一基名。
    """
    if not isinstance(provenance, dict):
        raise CppExtensionIdentityError("标杆 runtime_provenance 缺失或非 object")
    library = provenance.get("required_symbol_lib")
    if (not isinstance(library, dict)
            or not isinstance(library.get("path"), str)
            or not os.path.isabs(library["path"])):
        raise CppExtensionIdentityError("标杆 required_symbol_lib 缺绝对 ELF 路径")
    library_sha = _require_hex64(
        library.get("sha256"), "标杆 required_symbol_lib.sha256")
    symbols = provenance.get("symbols")
    if not isinstance(symbols, list) or len(symbols) != 2:
        raise CppExtensionIdentityError("标杆须恰好记录 workspace/stage2 两个符号")
    by_name = {}
    for index, item in enumerate(symbols):
        name = item.get("symbol") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name or name in by_name:
            raise CppExtensionIdentityError(
                f"标杆 symbols[{index}].symbol 缺失或重复")
        if (item.get("source") != "required_symbol_lib"
                or item.get("resolved_via") != library["path"]
                or item.get("defining_lib") != library["path"]
                or item.get("defining_lib_verified") is not True
                or item.get("lib") != library["path"]
                or item.get("lib_sha256") != library_sha):
            raise CppExtensionIdentityError(
                f"标杆符号 {name!r} 的实际定义 ELF/指纹未逐字命中 required_symbol_lib")
        by_name[name] = item
    stage2 = [name for name in by_name if not name.endswith("GetWorkspaceSize")]
    workspace = [name for name in by_name if name.endswith("GetWorkspaceSize")]
    if len(stage2) != 1 or len(workspace) != 1 \
            or workspace[0] != stage2[0] + "GetWorkspaceSize":
        raise CppExtensionIdentityError("标杆两个符号不是同一接口的 workspace/stage2 配对")
    if (not stage2[0].startswith("aclnn")
            or len(stage2[0]) == len("aclnn")):
        raise CppExtensionIdentityError("标杆 stage2 符号不是合法 aclnn 接口")
    if expected_entrypoint is not None:
        if (not isinstance(expected_entrypoint, str)
                or _SYMBOL_BASE_RE.fullmatch(expected_entrypoint) is None):
            raise CppExtensionIdentityError("expected_entrypoint 须为 ACLNN 接口基名")
        if stage2[0] != "aclnn" + expected_entrypoint:
            raise CppExtensionIdentityError(
                "标杆双符号与调用计划要求的 ACLNN 接口不一致")
    return {
        "library": {"path": library["path"], "sha256": library_sha},
        "entrypoint": stage2[0][len("aclnn"):],
        "symbols": [workspace[0], stage2[0]],
    }
