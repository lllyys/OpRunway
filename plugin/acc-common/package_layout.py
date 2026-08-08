#!/usr/bin/env python3
"""从显式有界搜索根唯一解析 CPack package OPP 根（stdlib-only）。

本模块只回答 package staging 的**布局定位**问题。目标 SoC、目标 op 的 exact
kernel 资产、package↔installed 摘要与 ELF 符号仍由 :mod:`target_kernel_delivery`
裁决；这里不复制那套门。

调用方必须给出：

* 绝对 ``package_search_root``，搜索永不离开该根；
* 安装侧已知的 exact vendor 目录 basename；
* ops-info 顶层 exact op type。

零个或多个精确候选均 fail-closed，绝不采用 ``find | head -1``。
"""

from __future__ import annotations

import json
import os


class PackageLayoutError(ValueError):
    """package staging 布局无法唯一、可信地解析。"""

    def __init__(self, message, *, code="PACKAGE_RESOLUTION_FAILED"):
        super().__init__(message)
        self.code = code


def _text(value, where, code):
    if not isinstance(value, str) or not value.strip():
        raise PackageLayoutError(f"{code}: {where} 须为非空字符串")
    return value.strip()


def _search_root(value):
    raw = _text(value, "package_search_root", "PACKAGE_SEARCH_ROOT_UNAVAILABLE")
    if not os.path.isabs(raw):
        raise PackageLayoutError(
            "PACKAGE_SEARCH_ROOT_UNAVAILABLE: package_search_root 须为绝对路径")
    if os.path.islink(raw):
        raise PackageLayoutError(
            f"PACKAGE_SEARCH_ROOT_SYMLINK: package_search_root 不得是符号链接：{raw}")
    if not os.path.isdir(raw):
        raise PackageLayoutError(
            f"PACKAGE_SEARCH_ROOT_UNAVAILABLE: package_search_root 不存在或不是目录：{raw}")
    return os.path.realpath(raw)


def _vendor_basename(value):
    vendor = _text(value, "expected_vendor_dir", "EXPECTED_VENDOR_INVALID")
    if vendor in (".", "..") or "/" in vendor or "\\" in vendor \
            or os.path.basename(vendor) != vendor:
        raise PackageLayoutError(
            "EXPECTED_VENDOR_INVALID: expected_vendor_dir 必须是单个 exact basename")
    return vendor


def _contained(root, path, where):
    raw = os.path.abspath(path)
    real = os.path.realpath(raw)
    try:
        lexical = os.path.commonpath((root, raw)) == root
        resolved = os.path.commonpath((root, real)) == root
    except ValueError:
        lexical = resolved = False
    if not lexical or not resolved:
        raise PackageLayoutError(
            f"PACKAGE_LAYOUT_ESCAPE: {where} 逃出 package_search_root：{path}")
    return real


def _walk(root, *, where):
    """稳定遍历且不跟随链接；被检查路径里的链接一律显式拒绝。"""
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        _contained(root, base, where)
        dirs.sort()
        files.sort()
        for name in dirs:
            path = os.path.join(base, name)
            if os.path.islink(path):
                yield "symlink_dir", path
        for name in files:
            path = os.path.join(base, name)
            if os.path.islink(path):
                yield "symlink_file", path
        yield "directory", (base, tuple(dirs), tuple(files))


def _candidate_has_op(search_root, candidate, expected_op_type):
    candidate = _contained(search_root, candidate, "package OPP candidate")
    if os.path.islink(candidate) or not os.path.isdir(candidate):
        raise PackageLayoutError(
            f"PACKAGE_LAYOUT_SYMLINK: package OPP candidate 不是普通目录：{candidate}")
    op_api = os.path.join(candidate, "op_api")
    config_root = os.path.join(
        candidate, "op_impl", "ai_core", "tbe", "config")
    for path, label in ((op_api, "op_api"), (config_root, "ops-info config")):
        if os.path.islink(path):
            raise PackageLayoutError(
                f"PACKAGE_LAYOUT_SYMLINK: candidate {label} 是符号链接：{path}")
        if not os.path.isdir(path):
            return False
        _contained(candidate, path, f"candidate {label}")

    found = False
    for kind, row in _walk(config_root, where="candidate ops-info config"):
        if kind.startswith("symlink"):
            raise PackageLayoutError(
                f"PACKAGE_LAYOUT_SYMLINK: candidate ops-info 含符号链接：{row}")
        base, _dirs, files = row
        for name in files:
            if not name.endswith(".json"):
                continue
            path = _contained(candidate, os.path.join(base, name), "candidate ops-info")
            if not os.path.isfile(path):
                raise PackageLayoutError(
                    f"PACKAGE_CANDIDATE_INVALID: ops-info 不是普通文件：{path}")
            try:
                with open(path, encoding="utf-8") as src:
                    payload = json.load(src)
            except (OSError, UnicodeError, json.JSONDecodeError) as ex:
                raise PackageLayoutError(
                    f"PACKAGE_CANDIDATE_INVALID: ops-info 非法 JSON：{path}（{ex}）") from ex
            if not isinstance(payload, dict):
                raise PackageLayoutError(
                    f"PACKAGE_CANDIDATE_INVALID: ops-info 须为 JSON object：{path}")
            if expected_op_type in payload:
                found = True
    return found


def resolve_package_opp_root(package_search_root, expected_vendor_dir,
                             expected_op_type):
    """返回唯一精确 CPack package OPP 根的规范绝对路径。

    候选身份由 ``basename == expected_vendor_dir`` 与任一 ops-info 顶层包含
    ``expected_op_type`` 共同确定；父目录名、深度、仓名和算子名均不参与路径猜测。
    """
    root = _search_root(package_search_root)
    vendor = _vendor_basename(expected_vendor_dir)
    op_type = _text(expected_op_type, "expected_op_type", "EXPECTED_OP_INVALID")

    vendor_candidates = []
    for kind, row in _walk(root, where="package search tree"):
        if kind.startswith("symlink"):
            if os.path.basename(row) == vendor:
                raise PackageLayoutError(
                    f"PACKAGE_LAYOUT_SYMLINK: exact vendor candidate 是符号链接：{row}")
            continue
        base, dirs, files = row
        if vendor in files:
            raise PackageLayoutError(
                "PACKAGE_CANDIDATE_INVALID: exact vendor candidate 不是目录："
                + os.path.join(base, vendor))
        for name in dirs:
            path = os.path.join(base, name)
            if name == vendor and not os.path.islink(path):
                vendor_candidates.append(_contained(root, path, "package OPP candidate"))

    exact = sorted({path for path in vendor_candidates
                    if _candidate_has_op(root, path, op_type)})
    if not exact:
        raise PackageLayoutError(
            "PACKAGE_OPP_ROOT_MISSING: 有界搜索根中没有同时匹配 exact vendor/op 的 "
            f"package OPP root（vendor={vendor!r}, op_type={op_type!r}）",
            code="PACKAGE_ROOT_NOT_FOUND")
    if len(exact) != 1:
        raise PackageLayoutError(
            "PACKAGE_OPP_ROOT_AMBIGUOUS: 有界搜索根中存在多个 exact vendor/op 候选；"
            "拒绝 first-match：" + json.dumps(exact, ensure_ascii=False),
            code="PACKAGE_ROOT_AMBIGUOUS")
    return exact[0]
