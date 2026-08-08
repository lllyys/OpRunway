#!/usr/bin/env python3
"""不可跟随父链软链的产物目录守卫（stdlib-only）。"""

from __future__ import annotations

import os
import stat
import tempfile


class ArtifactPathError(RuntimeError):
    pass


def _absolute(path):
    if not isinstance(path, str) or not path:
        raise ArtifactPathError("产物路径须为非空字符串")
    return os.path.abspath(path)


def _select_trusted_base(target, trusted_bases):
    candidates = []
    for base in trusted_bases or (os.getcwd(), tempfile.gettempdir()):
        if not isinstance(base, str) or not base:
            continue
        absolute = os.path.abspath(base)
        try:
            if os.path.commonpath((target, absolute)) == absolute:
                candidates.append(absolute)
        except ValueError:
            continue
    if not candidates:
        raise ArtifactPathError(
            "产物路径不在显式可信 base、当前工作目录或系统临时目录之下")
    base = max(candidates, key=len)
    try:
        st = os.lstat(base)
    except OSError as ex:
        raise ArtifactPathError(f"可信产物 base 不可复核：{base!r}") from ex
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ArtifactPathError(f"可信产物 base 自身是符号链接或非目录：{base!r}")
    # base 由调用方显式建立信任，允许它上方存在系统级 alias（macOS /var → /private/var）；
    # no-follow 从它的 canonical inode 向下开始，用户可控的后续分段仍逐项 lstat。
    return base, os.path.realpath(base)


def prepare_directory(path, *, trusted_bases=None):
    """从可信 base 向下逐段 no-follow 创建目录，并返回 inode guard。"""
    target = _absolute(path)
    base, current = _select_trusted_base(target, trusted_bases)
    base_st = os.lstat(current)
    identities = [(current, base_st.st_dev, base_st.st_ino)]
    relative = os.path.relpath(target, base)
    for name in (() if relative == "." else relative.split(os.path.sep)):
        if not name:
            continue
        current = os.path.join(current, name)
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            os.mkdir(current)
            st = os.lstat(current)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ArtifactPathError(f"产物目录父链含符号链接或非目录：{current!r}")
        identities.append((current, st.st_dev, st.st_ino))
    guard = {"path": current, "requested_path": target,
             "identities": tuple(identities)}
    assert_stable(guard)
    return guard


def prepare_parent(path, *, trusted_bases=None):
    return prepare_directory(
        os.path.dirname(_absolute(path)) or os.path.sep,
        trusted_bases=trusted_bases)


def prepare_existing_directory(path):
    """已有公共报告根：root 自身不得为软链；其上方视作调用方建立的稳定 base。"""
    target = _absolute(path)
    try:
        st = os.lstat(target)
    except OSError as ex:
        raise ArtifactPathError(f"报告根不存在或不可复核：{target!r}") from ex
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ArtifactPathError(f"报告根是符号链接或非目录：{target!r}")
    canonical = os.path.realpath(target)
    real_st = os.lstat(canonical)
    guard = {"path": canonical, "requested_path": target,
             "identities": ((canonical, real_st.st_dev, real_st.st_ino),)}
    assert_stable(guard)
    return guard


def assert_stable(guard):
    for path, device, inode in guard["identities"]:
        try:
            st = os.lstat(path)
        except OSError as ex:
            raise ArtifactPathError(f"产物目录提交前父链不可复核：{path!r}") from ex
        if (stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode)
                or (st.st_dev, st.st_ino) != (device, inode)):
            raise ArtifactPathError(f"产物目录在事务期间被替换：{path!r}")
    return guard["path"]


def assert_leaf_safe(path):
    try:
        st = os.lstat(_absolute(path))
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ArtifactPathError(f"产物落点已有符号链接或非普通文件：{path!r}")


def _paths_overlap(left, right):
    left = os.path.realpath(left)
    right = os.path.realpath(right)
    try:
        common = os.path.commonpath((left, right))
    except ValueError as ex:
        raise ArtifactPathError("产物路径与可信输入无法建立路径边界") from ex
    return common in (left, right)


def assert_report_root_not_within_trusted(out_dir, trusted_paths):
    """纯检查：在读取可信输入和创建报告根之前拒绝反向包含。"""
    out = _absolute(out_dir)
    out_boundary = os.path.realpath(out)
    for trusted in trusted_paths:
        if not isinstance(trusted, str) or not trusted:
            raise ArtifactPathError("可信输入路径缺失")
        trusted_boundary = os.path.realpath(os.path.abspath(trusted))
        try:
            common = os.path.commonpath((out_boundary, trusted_boundary))
        except ValueError as ex:
            raise ArtifactPathError("报告根与可信输入无法建立路径边界") from ex
        if common == trusted_boundary:
            raise ArtifactPathError(
                f"报告根不得等于可信输入或落在可信输入之下：{out!r}")
    return out


def prepare_report_root(out_dir, trusted_paths, *, mutation_paths=()):
    """准备报告根，并证明可信输入不会被本事务的具体落点改写。

    默认保持最严边界：报告根不能包含可信输入。只有调用方显式枚举本事务会
    写入或清理的全部 ``mutation_paths`` 时，才允许把可信输入保存在报告根的
    其它子树（标准 workflow 的 ``work/``）。mutation path 必须严格位于报告根
    下，且与每个可信输入均不互为祖先；本函数不授权递归清理报告根。
    """
    if not isinstance(trusted_paths, (tuple, list)) or not trusted_paths:
        raise ArtifactPathError("报告根必须与显式可信输入建立不重叠边界")
    if not isinstance(mutation_paths, (tuple, list)):
        raise ArtifactPathError("报告根 mutation paths 须为 tuple/list")
    out = assert_report_root_not_within_trusted(out_dir, trusted_paths)
    out_boundary = os.path.realpath(out)
    mutations = []
    for path in mutation_paths:
        mutation = _absolute(path)
        mutation_boundary = os.path.realpath(mutation)
        try:
            common = os.path.commonpath((out_boundary, mutation_boundary))
        except ValueError as ex:
            raise ArtifactPathError("报告根与 mutation path 无法建立路径边界") from ex
        if common != out_boundary or mutation_boundary == out_boundary:
            raise ArtifactPathError("受控 mutation path 必须严格位于报告根下")
        mutations.append(mutation)
    for trusted in trusted_paths:
        if not isinstance(trusted, str) or not trusted:
            raise ArtifactPathError("可信输入路径缺失")
        trusted_abs = os.path.abspath(trusted)
        trusted_boundary = os.path.realpath(trusted_abs)
        try:
            common = os.path.commonpath((out_boundary, trusted_boundary))
        except ValueError as ex:
            raise ArtifactPathError("报告根与可信输入无法建立路径边界") from ex
        if common == trusted_boundary:
            raise ArtifactPathError(
                f"报告根不得等于可信输入或落在可信输入之下：{out!r}")
        if common == out_boundary:
            if not mutations:
                raise ArtifactPathError(
                    f"报告根包含可信输入但调用方未声明受控 mutation paths：{out!r}")
            for mutation in mutations:
                if _paths_overlap(mutation, trusted_abs):
                    raise ArtifactPathError(
                        "受控终态路径不得与可信输入相等或互为祖先："
                        f"{mutation!r} ↔ {trusted_abs!r}")
    bases = [os.getcwd(), tempfile.gettempdir()]
    bases.extend(os.path.dirname(os.path.abspath(path))
                 for path in trusted_paths)
    return prepare_directory(out, trusted_bases=bases)
