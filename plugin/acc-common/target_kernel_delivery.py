#!/usr/bin/env python3
"""目标 SoC kernel 安装交付闭环（stdlib-only）。

本模块只接受调用方显式给出的 SoC、构建选择名、op type、安装根、package 根和
CMakeCache；不从日志、路径或 metadata 反推这些身份。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess


SCHEMA = "oprunway.target_kernel_delivery_closure"
SCHEMA_VERSION = 1
STATUS = "VERIFIED"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_CACHE_LINE = re.compile(r"^([^#/:=]+)(?::[^=]+)?=(.*)$")


class TargetKernelDeliveryError(ValueError):
    """目标 kernel 交付闭环不成立。"""

    def __init__(self, message, *, code="TARGET_CLOSURE_FAILED"):
        super().__init__(message)
        self.code = code


def _is_hex64(value):
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise TargetKernelDeliveryError(f"MISSING_EXPLICIT_TARGET: {where} 须为非空字符串")
    return value.strip()


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_sha(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _root(path, where):
    value = _text(path, where)
    if not os.path.isabs(value):
        raise TargetKernelDeliveryError(f"{where} 须为绝对路径")
    if os.path.islink(value) or not os.path.isdir(value):
        raise TargetKernelDeliveryError(
            f"PACKAGE_ROOT_UNAVAILABLE: {where} 不存在、不是目录或是符号链接：{value}")
    return os.path.realpath(value)


def _contained(root, path, where):
    root = os.path.realpath(root)
    raw = os.path.abspath(path)
    try:
        lexical_within = os.path.commonpath((root, raw)) == root
    except ValueError:
        lexical_within = False
    if lexical_within:
        rel_lexical = os.path.relpath(raw, root)
        current = root
        for part in rel_lexical.split(os.sep):
            current = os.path.join(current, part)
            if os.path.islink(current):
                raise TargetKernelDeliveryError(
                    f"ASSET_SYMLINK: {where} 路径段是符号链接：{current}")
    real = os.path.realpath(raw)
    try:
        within = os.path.commonpath((root, real)) == root
    except ValueError:
        within = False
    if not within:
        raise TargetKernelDeliveryError(
            f"ASSET_PATH_ESCAPE: {where} 逃出 containment root：{path}")
    # macOS 的 /var -> /private/var 属于根路径本身的系统别名；若 lexical path 已在
    # real root 下则保留 lexical 路径以检查每个 symlink 段，否则用 real path 消除根别名。
    checked = raw if lexical_within else real
    rel = os.path.relpath(checked, root)
    current = root
    for part in rel.split(os.sep):
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise TargetKernelDeliveryError(
                f"ASSET_SYMLINK: {where} 路径段是符号链接：{current}")
    if not os.path.isfile(checked):
        raise TargetKernelDeliveryError(f"OBJECT_MISSING: {where} 不存在或不是普通文件：{checked}")
    return checked, rel.replace(os.sep, "/")


def _read_json(root, path, where):
    path, rel = _contained(root, path, where)
    try:
        with open(path, encoding="utf-8") as src:
            value = json.load(src)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise TargetKernelDeliveryError(f"UNSUPPORTED_DELIVERY_SCHEMA: {where} 非法 JSON：{ex}") from ex
    if not isinstance(value, dict):
        raise TargetKernelDeliveryError(
            f"UNSUPPORTED_DELIVERY_SCHEMA: {where} 须为 JSON object")
    return value, path, rel


def _walk_files(root):
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            path = os.path.join(base, name)
            if os.path.islink(path):
                raise TargetKernelDeliveryError(
                    f"ASSET_SYMLINK: 目标资产目录含符号链接：{path}")
        for name in sorted(files):
            path = os.path.join(base, name)
            if os.path.islink(path):
                raise TargetKernelDeliveryError(
                    f"ASSET_SYMLINK: 目标资产文件是符号链接：{path}")
            if not os.path.isfile(path):
                raise TargetKernelDeliveryError(f"OBJECT_MISSING: 目标资产不是普通文件：{path}")
            yield path


def _cache(path, build_cwd):
    cwd = _root(build_cwd, "build.cwd")
    path, rel = _contained(cwd, path, "CMakeCache")
    values = {}
    try:
        with open(path, encoding="utf-8") as src:
            for raw in src:
                match = _CACHE_LINE.match(raw.rstrip("\n"))
                if match:
                    values[match.group(1)] = match.group(2)
    except (OSError, UnicodeError) as ex:
        raise TargetKernelDeliveryError(f"CMakeCache 读取失败：{ex}") from ex
    return path, rel, values


def _argv_values(argv, option, cmake_key):
    flat = []
    for item in argv:
        try:
            flat.extend(shlex.split(item))
        except ValueError as ex:
            raise TargetKernelDeliveryError(f"build argv 无法确定性 token 化：{ex}") from ex
    values = []
    prefixes = (option + "=", f"-D{cmake_key}=", cmake_key + "=")
    for index, token in enumerate(flat):
        for prefix in prefixes:
            if token.startswith(prefix):
                values.append(token[len(prefix):])
        if token == option and index + 1 < len(flat):
            values.append(flat[index + 1])
    return values


def _assert_selection(argv, soc, selected_op, cache_values):
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv):
        raise TargetKernelDeliveryError("build argv 缺失或非法")
    soc_values = _argv_values(argv, "--soc", "ASCEND_COMPUTE_UNIT")
    op_values = _argv_values(argv, "--ops", "ASCEND_OP_NAME")
    if not soc_values or set(soc_values) != {soc}:
        raise TargetKernelDeliveryError(
            f"TARGET_SOC_MISMATCH: build argv 未唯一绑定 requested SoC={soc!r}，实得 {soc_values!r}",
            code="TARGET_SOC_MISMATCH")
    if not op_values or set(op_values) != {selected_op}:
        raise TargetKernelDeliveryError(
            f"TARGET_OP_MISMATCH: build argv 未唯一绑定 selected op={selected_op!r}，实得 {op_values!r}",
            code="TARGET_OP_MISMATCH")
    if cache_values.get("ASCEND_COMPUTE_UNIT") != soc:
        raise TargetKernelDeliveryError(
            "TARGET_SOC_MISMATCH: CMakeCache.ASCEND_COMPUTE_UNIT 与 requested SoC 不一致",
            code="TARGET_SOC_MISMATCH")
    if cache_values.get("ASCEND_OP_NAME") != selected_op:
        raise TargetKernelDeliveryError(
            "TARGET_OP_MISMATCH: CMakeCache.ASCEND_OP_NAME 与 selected op 不一致",
            code="TARGET_OP_MISMATCH")


def _kernel_names(row):
    # CANN 9.0.1 的真实 metadata 把 ``kernelName`` 作为 bin basename，
    # ``kernelList[].kernelName`` 才是 ELF 导出的逐 tiling 符号。存在 kernelList 时
    # 不能再把顶层 basename 冒充必须存在的 ELF symbol。
    values = []
    direct = row.get("kernelName") if isinstance(row, dict) else None
    listed = row.get("kernelList") if isinstance(row, dict) else None
    if listed is not None:
        if not isinstance(listed, list) or not listed:
            raise TargetKernelDeliveryError(
                "UNSUPPORTED_DELIVERY_SCHEMA: metadata kernelList 须为非空数组")
        for item in listed:
            if not isinstance(item, dict) or not isinstance(item.get("kernelName"), str) \
                    or not item["kernelName"]:
                raise TargetKernelDeliveryError(
                    "UNSUPPORTED_DELIVERY_SCHEMA: metadata kernelList[].kernelName 缺失")
            values.append(item["kernelName"])
    elif isinstance(direct, str) and direct:
        values.append(direct)
    return sorted(set(values))


def _bin_path(row):
    for key in ("binPath", "binFilePath", "binFileName", "binName"):
        value = row.get(key) if isinstance(row, dict) else None
        if isinstance(value, str) and value:
            if os.path.isabs(value):
                raise TargetKernelDeliveryError("ASSET_PATH_ESCAPE: metadata bin path 不得为绝对路径")
            return value if value.endswith(".o") else value + ".o"
    raise TargetKernelDeliveryError(
        "UNSUPPORTED_DELIVERY_SCHEMA: metadata binList entry 缺 binPath/binFilePath/binFileName")


def _metadata_bin_rows(metadata):
    """兼容 current custom binList 与 CANN 9.0.1 顶层单-bin metadata。"""
    bin_list = metadata.get("binList")
    if bin_list is not None:
        if not isinstance(bin_list, list) or not bin_list:
            raise TargetKernelDeliveryError(
                "EMPTY_BIN_LIST: target kernel metadata.binList 为空或非法")
        if not all(isinstance(item, dict) for item in bin_list):
            raise TargetKernelDeliveryError(
                "UNSUPPORTED_DELIVERY_SCHEMA: metadata.binList[] 须为 object")
        return bin_list
    # A3/CANN 9.0.1 实际格式：binFileName/binFileSuffix/kernelName/kernelList
    # 仍被归一成非空 bins；缺任一资产身份就 fail-closed，不从文件名猜。
    suffix = metadata.get("binFileSuffix")
    if suffix not in (None, ".o"):
        raise TargetKernelDeliveryError(
            f"UNSUPPORTED_DELIVERY_SCHEMA: metadata.binFileSuffix 非 .o：{suffix!r}")
    if isinstance(metadata.get("binFileName"), str) \
            and metadata.get("binFileName") \
            and (isinstance(metadata.get("kernelName"), str)
                 or metadata.get("kernelList") is not None):
        return [metadata]
    return []


def _metadata_bin_identity(metadata, metadata_path, installed_root):
    rows = []
    for item in _metadata_bin_rows(metadata):
        names = _kernel_names(item)
        if not names:
            raise TargetKernelDeliveryError(
                "KERNEL_SYMBOL_MISSING: metadata bin entry 缺 kernelName")
        object_path = os.path.normpath(
            os.path.join(os.path.dirname(metadata_path), _bin_path(item)))
        _, object_rel = _contained(
            installed_root, object_path, "target kernel metadata object")
        rows.append({"relative_path": object_rel, "kernel_names": names})
    return sorted(rows, key=lambda row: (row["relative_path"], row["kernel_names"]))


def _global_defined_symbols(path):
    readelf = shutil.which("readelf")
    if readelf:
        run = subprocess.run([readelf, "-Ws", path], check=False,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if run.returncode != 0:
            raise TargetKernelDeliveryError(
                f"KERNEL_SYMBOL_MISSING: readelf 失败 rc={run.returncode}: {run.stderr.strip()}")
        symbols = set()
        for line in run.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 8 and fields[4] in ("GLOBAL", "WEAK") \
                    and fields[6] != "UND":
                symbols.add(fields[7].split("@", 1)[0])
        return symbols
    nm = shutil.which("nm")
    if nm:
        run = subprocess.run([nm, "-g", path], check=False,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if run.returncode != 0:
            raise TargetKernelDeliveryError(
                f"KERNEL_SYMBOL_MISSING: nm 失败 rc={run.returncode}: {run.stderr.strip()}")
        symbols = set()
        for line in run.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[-2].upper() != "U":
                symbols.add(fields[-1])
        return symbols
    raise TargetKernelDeliveryError("KERNEL_SYMBOL_MISSING: readelf/nm 均不可用")


def _counterpart(package_root, rel, expected_sha, where, *, installed_root):
    path, got_rel = _contained(package_root, os.path.join(package_root, rel), where)
    installed_path, _ = _contained(
        installed_root, os.path.join(installed_root, rel), "installed counterpart")
    if os.path.samefile(path, installed_path):
        raise TargetKernelDeliveryError(
            f"PACKAGE_INSTALL_DRIFT: {where} 与安装侧是同一 inode/hardlink：{rel}")
    if got_rel != rel or _sha(path) != expected_sha:
        raise TargetKernelDeliveryError(
            f"PACKAGE_INSTALL_DRIFT: {where} 与安装侧路径/摘要不一致：{rel}")
    return _sha(path)


def target_manifest(root, soc, *, allow_missing=False):
    """构建窗口前/后的 exact-target 资产摘要，用于拒绝 rc0 陈旧资产复用。"""
    if allow_missing and isinstance(root, str) and os.path.isabs(root) \
            and not os.path.lexists(root):
        rows = []
        return {"files": rows, "sha256": _canonical_sha(rows)}
    root = _root(root, "installed_opp_root")
    rel_roots = (
        os.path.join("op_impl", "ai_core", "tbe", "config", soc),
        os.path.join("op_impl", "ai_core", "tbe", "kernel", soc),
    )
    rows = []
    for rel_root in rel_roots:
        path = os.path.join(root, rel_root)
        if not os.path.exists(path):
            continue
        if os.path.islink(path) or not os.path.isdir(path):
            raise TargetKernelDeliveryError(f"ASSET_SYMLINK: exact target 根非法：{path}")
        for file_path in _walk_files(path):
            _, rel = _contained(root, file_path, "目标资产")
            rows.append({"path": rel, "sha256": _sha(file_path)})
    rows.sort(key=lambda row: row["path"])
    return {"files": rows, "sha256": _canonical_sha(rows)}


def _manifest_map(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {"files", "sha256"} \
            or not isinstance(manifest.get("files"), list) \
            or manifest.get("sha256") != _canonical_sha(manifest.get("files")):
        raise TargetKernelDeliveryError(
            "OBJECT_HASH_MISMATCH: target manifest 结构或摘要非法")
    rows = manifest["files"]
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"} \
                or not isinstance(row["path"], str) or not row["path"] \
                or os.path.isabs(row["path"]) \
                or ".." in row["path"].replace("\\", "/").split("/") \
                or not _is_hex64(row["sha256"]) \
                or row["path"] in result:
            raise TargetKernelDeliveryError(
                "OBJECT_HASH_MISMATCH: target manifest 记录非法")
        result[row["path"]] = row["sha256"]
    if [row["path"] for row in rows] != sorted(result):
        raise TargetKernelDeliveryError("OBJECT_HASH_MISMATCH: target manifest 未规范排序")
    return result


def _assert_selected_assets_changed(before, current, *, configs, assets):
    """只认本闭包引用的 op 资产变化；同 SoC 其它 op 改写不能掩盖 silent skip。"""
    if not isinstance(before, dict) or not isinstance(before.get("files"), list):
        raise TargetKernelDeliveryError("TARGET_ASSETS_UNCHANGED: build 前 target manifest 非法")
    try:
        prior = _manifest_map(before)
        now = _manifest_map(current)
    except TargetKernelDeliveryError as ex:
        raise TargetKernelDeliveryError(
            f"TARGET_ASSETS_UNCHANGED: build 前/后 target manifest 非法：{ex}") from ex
    objects = sorted({row["relative_path"]
                      for asset in assets for row in asset["bins"]})
    if not objects or any(prior.get(path) == now.get(path) for path in objects):
        raise TargetKernelDeliveryError(
            "TARGET_ASSETS_UNCHANGED: build rc=0 但 selected op 至少一个 exact-target "
            "kernel object/.o 未改变；config、metadata 或同 SoC 其它资产改写不能掩盖 silent skip")


def build_closure(*, requested_soc, selected_op, expected_op_type,
                  installed_opp_root, package_opp_root, cmake_cache_path,
                  build_argv, build_cwd=None, target_assets_before=None,
                  symbol_inspector=None):
    symbol_inspector = symbol_inspector or _global_defined_symbols
    soc = _text(requested_soc, "requested_soc")
    selected = _text(selected_op, "selected_op")
    op_type = _text(expected_op_type, "expected_op_type")
    installed = _root(installed_opp_root, "installed_opp_root")
    package = _root(package_opp_root, "package_opp_root")
    if os.path.commonpath((installed, package)) in (installed, package):
        raise TargetKernelDeliveryError(
            "PACKAGE_ROOT_UNAVAILABLE: installed/package OPP 根必须互相独立且不得嵌套")
    cwd = build_cwd or os.path.dirname(os.path.realpath(cmake_cache_path))
    cache_path, cache_rel, cache_values = _cache(cmake_cache_path, cwd)
    _assert_selection(build_argv, soc, selected, cache_values)

    config_root = os.path.join(installed, "op_impl", "ai_core", "tbe", "config", soc)
    if not os.path.isdir(config_root) or os.path.islink(config_root):
        raise TargetKernelDeliveryError(
            "OPS_INFO_MISSING: exact target config 根不存在", code="OPS_INFO_MISSING")
    configs = []
    for path in _walk_files(config_root):
        if not path.endswith(".json"):
            continue
        obj, path, rel = _read_json(installed, path, "target ops-info")
        if os.path.getsize(path) <= 0 or op_type not in obj:
            continue
        digest = _sha(path)
        configs.append({"relative_path": rel, "installed_sha256": digest,
                        "package_sha256": _counterpart(
                            package, rel, digest, "package target ops-info",
                            installed_root=installed)})
    if not configs:
        raise TargetKernelDeliveryError(
            f"OPS_INFO_MISSING: exact target={soc!r} 的非空 ops-info 不含 op type={op_type!r}",
            code="OPS_INFO_MISSING")

    kernel_root = os.path.join(installed, "op_impl", "ai_core", "tbe", "kernel", soc)
    if not os.path.isdir(kernel_root) or os.path.islink(kernel_root):
        raise TargetKernelDeliveryError("OBJECT_MISSING: exact target kernel 根不存在")
    assets = []
    for metadata_path in _walk_files(kernel_root):
        if not metadata_path.endswith(".json"):
            continue
        metadata, metadata_path, metadata_rel = _read_json(
            installed, metadata_path, "target kernel metadata")
        bin_list = _metadata_bin_rows(metadata)
        if not bin_list:
            continue
        bins = []
        belongs = False
        for item in bin_list:
            names = _kernel_names(item)
            if not names:
                raise TargetKernelDeliveryError(
                    "KERNEL_SYMBOL_MISSING: metadata binList entry 缺 kernelName")
            selected_names = [name for name in names
                              if name == op_type or name.startswith(op_type + "_")]
            if selected_names:
                belongs = True
            rel_bin = _bin_path(item)
            object_path = os.path.normpath(os.path.join(os.path.dirname(metadata_path), rel_bin))
            object_path, object_rel = _contained(installed, object_path, "target kernel object")
            symbols = symbol_inspector(object_path)
            missing = sorted(set(names) - symbols)
            if missing:
                raise TargetKernelDeliveryError(
                    f"KERNEL_SYMBOL_MISSING: metadata kernelName 不是 .o 的 exact defined global symbol：{missing}")
            digest = _sha(object_path)
            bins.append({
                "relative_path": object_rel,
                "installed_sha256": digest,
                "package_sha256": _counterpart(package, object_rel, digest,
                                                "package target kernel object",
                                                installed_root=installed),
                "kernel_names": names,
                "global_symbols": sorted(symbols),
            })
        if belongs:
            metadata_sha = _sha(metadata_path)
            assets.append({
                "metadata_relative_path": metadata_rel,
                "metadata_installed_sha256": metadata_sha,
                "metadata_package_sha256": _counterpart(
                    package, metadata_rel, metadata_sha, "package target kernel metadata",
                    installed_root=installed),
                "bins": bins,
            })
    if not assets:
        raise TargetKernelDeliveryError(
            f"OBJECT_MISSING: exact target={soc!r} 没有属于 op type={op_type!r} 的 metadata/.o")

    current_manifest = target_manifest(installed, soc)
    if target_assets_before is not None:
        _assert_selected_assets_changed(
            target_assets_before, current_manifest, configs=configs, assets=assets)
    payload = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": STATUS,
        "request": {
            "requested_soc": soc,
            "selected_op": selected,
            "expected_op_type": op_type,
            "installed_opp_root": installed,
            "package_opp_root": package,
        },
        "build_selection": {
            "cmake_cache_path": cache_path,
            "cmake_cache_relative_path": cache_rel,
            "cmake_cache_sha256": _sha(cache_path),
            "cache_values": {
                "ASCEND_COMPUTE_UNIT": cache_values["ASCEND_COMPUTE_UNIT"],
                "ASCEND_OP_NAME": cache_values["ASCEND_OP_NAME"],
            },
        },
        "target_config": {"files": sorted(configs, key=lambda row: row["relative_path"])},
        "kernel_assets": sorted(assets, key=lambda row: row["metadata_relative_path"]),
        "package": {"status": "verified", "comparison": "exact_relative_path_sha256"},
        "target_manifest": current_manifest,
    }
    payload["closure_sha256"] = _canonical_sha(payload)
    return payload


def validate_closure(closure, *, build_argv, live=False, symbol_inspector=None):
    symbol_inspector = symbol_inspector or _global_defined_symbols
    if not isinstance(closure, dict) or closure.get("schema") != SCHEMA \
            or closure.get("schema_version") != SCHEMA_VERSION \
            or closure.get("status") != STATUS:
        raise TargetKernelDeliveryError("UNSUPPORTED_DELIVERY_SCHEMA: closure schema/status 非 current VERIFIED")
    recorded_sha = closure.get("closure_sha256")
    unsigned = {key: value for key, value in closure.items() if key != "closure_sha256"}
    if not isinstance(recorded_sha, str) or recorded_sha != _canonical_sha(unsigned):
        raise TargetKernelDeliveryError("OBJECT_HASH_MISMATCH: closure_sha256 与内容不一致")
    request = closure.get("request")
    selection = closure.get("build_selection")
    if not isinstance(request, dict) or not isinstance(selection, dict):
        raise TargetKernelDeliveryError("MISSING_EXPLICIT_TARGET: closure.request/build_selection 缺失")
    soc = _text(request.get("requested_soc"), "closure.request.requested_soc")
    selected = _text(request.get("selected_op"), "closure.request.selected_op")
    op_type = _text(request.get("expected_op_type"), "closure.request.expected_op_type")
    installed_value = _text(
        request.get("installed_opp_root"), "closure.request.installed_opp_root")
    package_value = _text(
        request.get("package_opp_root"), "closure.request.package_opp_root")
    if not os.path.isabs(installed_value) or not os.path.isabs(package_value):
        raise TargetKernelDeliveryError("MISSING_EXPLICIT_TARGET: package/install root 须为绝对路径")
    installed = _root(installed_value, "closure.request.installed_opp_root") if live \
        else os.path.realpath(installed_value)
    package = _root(package_value, "closure.request.package_opp_root") if live \
        else os.path.realpath(package_value)
    if os.path.commonpath((installed, package)) in (installed, package):
        raise TargetKernelDeliveryError(
            "PACKAGE_ROOT_UNAVAILABLE: installed/package OPP 根必须互相独立且不得嵌套")
    if closure.get("package") != {
            "status": "verified", "comparison": "exact_relative_path_sha256"}:
        raise TargetKernelDeliveryError("PACKAGE_ROOT_UNAVAILABLE: package/install 闭环未 VERIFIED")
    cache_path = _text(selection.get("cmake_cache_path"), "cmake_cache_path")
    if not os.path.isabs(cache_path) \
            or not _is_hex64(selection.get("cmake_cache_sha256")):
        raise TargetKernelDeliveryError("UNSUPPORTED_DELIVERY_SCHEMA: CMakeCache 路径/摘要非法")
    cache_values = selection.get("cache_values")
    if cache_values != {
            "ASCEND_COMPUTE_UNIT": soc, "ASCEND_OP_NAME": selected}:
        raise TargetKernelDeliveryError(
            "TARGET_SOC_MISMATCH: closure cache_values 与 request 不一致",
            code="TARGET_SOC_MISMATCH")
    _assert_selection(build_argv, soc, selected, cache_values)
    if live:
        cache_path, _, live_cache_values = _cache(cache_path, os.path.dirname(cache_path))
        if _sha(cache_path) != selection.get("cmake_cache_sha256"):
            raise TargetKernelDeliveryError("OBJECT_HASH_MISMATCH: CMakeCache 现场摘要漂移")
        _assert_selection(build_argv, soc, selected, live_cache_values)
    target_config = closure.get("target_config")
    configs = target_config.get("files") if isinstance(target_config, dict) else None
    assets = closure.get("kernel_assets")
    if not isinstance(configs, list) or not configs:
        raise TargetKernelDeliveryError(
            "OPS_INFO_MISSING: closure target config 为空", code="OPS_INFO_MISSING")
    if not isinstance(assets, list) or not assets:
        raise TargetKernelDeliveryError("OBJECT_MISSING: closure kernel assets 为空")
    manifest = _manifest_map(closure.get("target_manifest"))
    config_prefix = f"op_impl/ai_core/tbe/config/{soc}/"
    kernel_prefix = f"op_impl/ai_core/tbe/kernel/{soc}/"
    for row in configs:
        if not isinstance(row, dict) or set(row) != {
                "relative_path", "installed_sha256", "package_sha256"}:
            raise TargetKernelDeliveryError("UNSUPPORTED_DELIVERY_SCHEMA: target config record 非法")
        if row["installed_sha256"] != row["package_sha256"] \
                or not _is_hex64(row["installed_sha256"]):
            raise TargetKernelDeliveryError("PACKAGE_INSTALL_DRIFT: target config 摘要不闭合")
        rel = _text(row.get("relative_path"), "target config relative_path")
        if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
            raise TargetKernelDeliveryError("ASSET_PATH_ESCAPE: target config relative_path 非法")
        if not rel.replace("\\", "/").startswith(config_prefix) \
                or manifest.get(rel.replace("\\", "/")) != row["installed_sha256"]:
            raise TargetKernelDeliveryError(
                "OBJECT_HASH_MISMATCH: target config 未与 exact-target manifest 摘要闭合")
        if live:
            obj, path, _ = _read_json(installed, os.path.join(installed, rel),
                                      "target ops-info")
            if op_type not in obj or _sha(path) != row["installed_sha256"]:
                raise TargetKernelDeliveryError("OBJECT_HASH_MISMATCH: target ops-info 现场内容漂移")
            _counterpart(package, rel, row["package_sha256"], "package target ops-info",
                         installed_root=installed)
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != {
                "metadata_relative_path", "metadata_installed_sha256",
                "metadata_package_sha256", "bins"} \
                or not isinstance(asset.get("bins"), list) \
                or not asset["bins"]:
            raise TargetKernelDeliveryError("EMPTY_BIN_LIST: closure metadata bins 为空")
        meta_rel = _text(asset.get("metadata_relative_path"), "metadata_relative_path")
        if os.path.isabs(meta_rel) or ".." in meta_rel.replace("\\", "/").split("/"):
            raise TargetKernelDeliveryError("ASSET_PATH_ESCAPE: metadata_relative_path 非法")
        if (not _is_hex64(asset.get("metadata_installed_sha256"))
                or asset.get("metadata_installed_sha256")
                != asset.get("metadata_package_sha256")):
            raise TargetKernelDeliveryError("OBJECT_HASH_MISMATCH: metadata 摘要不闭合")
        normalized_meta_rel = meta_rel.replace("\\", "/")
        if not normalized_meta_rel.startswith(kernel_prefix) \
                or manifest.get(normalized_meta_rel) \
                != asset["metadata_installed_sha256"]:
            raise TargetKernelDeliveryError(
                "OBJECT_HASH_MISMATCH: metadata 未与 exact-target manifest 摘要闭合")
        if live:
            meta, meta_path, _ = _read_json(
                installed, os.path.join(installed, meta_rel), "target kernel metadata")
            if _sha(meta_path) != asset["metadata_installed_sha256"]:
                raise TargetKernelDeliveryError("OBJECT_HASH_MISMATCH: metadata 现场摘要漂移")
            _counterpart(package, meta_rel, asset["metadata_package_sha256"],
                         "package target kernel metadata", installed_root=installed)
            live_bins = _metadata_bin_rows(meta)
            if not live_bins:
                raise TargetKernelDeliveryError("EMPTY_BIN_LIST: metadata 现场 bins 为空")
            live_identity = _metadata_bin_identity(meta, meta_path, installed)
            recorded_identity = sorted(
                ({"relative_path": row.get("relative_path"),
                  "kernel_names": row.get("kernel_names")}
                 for row in asset["bins"]),
                key=lambda row: (str(row["relative_path"]),
                                 str(row["kernel_names"])))
            if live_identity != recorded_identity:
                raise TargetKernelDeliveryError(
                    "UNSUPPORTED_DELIVERY_SCHEMA: metadata 现场 bin 路径/kernelName identity "
                    "与 closure 不一致")
            if not any(name == op_type or name.startswith(op_type + "_")
                       for row in live_identity for name in row["kernel_names"]):
                raise TargetKernelDeliveryError(
                    "OBJECT_MISSING: metadata 现场 identity 不属于 expected op type")
        asset_belongs = False
        for row in asset["bins"]:
            if not isinstance(row, dict) or set(row) != {
                    "relative_path", "installed_sha256", "package_sha256",
                    "kernel_names", "global_symbols"} \
                    or not isinstance(row.get("kernel_names"), list) \
                    or not row["kernel_names"]:
                raise TargetKernelDeliveryError("KERNEL_SYMBOL_MISSING: closure kernel_names 为空")
            names = row["kernel_names"]
            symbols = row.get("global_symbols")
            names_are_text = all(isinstance(name, str) and name for name in names)
            symbols_are_text = (isinstance(symbols, list) and bool(symbols)
                                and all(isinstance(name, str) and name
                                        for name in symbols))
            if (not names_are_text
                    or names != sorted(set(names))
                    or not symbols_are_text
                    or symbols != sorted(set(symbols))
                    or not set(names) <= set(symbols)):
                raise TargetKernelDeliveryError(
                    "KERNEL_SYMBOL_MISSING: closure kernel/global symbol identity 非法")
            if any(name == op_type or name.startswith(op_type + "_") for name in names):
                asset_belongs = True
            digest = row.get("installed_sha256")
            if not _is_hex64(digest) or digest != row.get("package_sha256"):
                raise TargetKernelDeliveryError("OBJECT_HASH_MISMATCH: kernel object 摘要不闭合")
            object_rel = _text(row.get("relative_path"), "kernel object relative_path")
            if os.path.isabs(object_rel) \
                    or ".." in object_rel.replace("\\", "/").split("/"):
                raise TargetKernelDeliveryError("ASSET_PATH_ESCAPE: kernel object relative_path 非法")
            normalized_object_rel = object_rel.replace("\\", "/")
            if not normalized_object_rel.startswith(kernel_prefix) \
                    or manifest.get(normalized_object_rel) != digest:
                raise TargetKernelDeliveryError(
                    "OBJECT_HASH_MISMATCH: kernel object 未与 exact-target manifest 摘要闭合")
            if live:
                obj_path, _ = _contained(
                    installed, os.path.join(installed, object_rel), "target kernel object")
                if _sha(obj_path) != digest:
                    raise TargetKernelDeliveryError("OBJECT_HASH_MISMATCH: kernel object 现场摘要漂移")
                _counterpart(package, object_rel, digest, "package target kernel object",
                             installed_root=installed)
                symbols = symbol_inspector(obj_path)
                if not set(row["kernel_names"]) <= symbols:
                    raise TargetKernelDeliveryError(
                        "KERNEL_SYMBOL_MISSING: kernelName 与现场 .o 全局符号不闭合")
                if sorted(symbols) != row.get("global_symbols"):
                    raise TargetKernelDeliveryError(
                        "KERNEL_SYMBOL_MISSING: closure 全局符号清单与现场漂移")
        if not asset_belongs:
            raise TargetKernelDeliveryError(
                "OBJECT_MISSING: closure metadata identity 不属于 expected op type")
    if live:
        live_manifest = _manifest_map(target_manifest(installed, soc))
        recorded_manifest = _manifest_map(closure.get("target_manifest"))
        referenced = {row["relative_path"].replace("\\", "/") for row in configs}
        for asset in assets:
            referenced.add(asset["metadata_relative_path"].replace("\\", "/"))
            referenced.update(row["relative_path"].replace("\\", "/")
                              for row in asset["bins"])
        if any(live_manifest.get(path) != recorded_manifest.get(path)
               for path in referenced):
            raise TargetKernelDeliveryError(
                "OBJECT_HASH_MISMATCH: selected-op target manifest 与现场 closure 漂移")
    return {"requested_soc": soc, "selected_op": selected,
            "expected_op_type": op_type, "kernel_asset_count": len(assets),
            "status": STATUS}
