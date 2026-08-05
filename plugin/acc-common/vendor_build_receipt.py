#!/usr/bin/env python3
"""`oprunway.vendor_build_receipt` 的**唯一校验处**（stdlib-only，不 import numpy/torch）。

这份收据回答的是一件事：**真机上被加载的那个 vendor `.so`，到底是从哪份源码、用哪条命令构建出来的**。
Extension 自身 build/load 成功只证明调用桥可用；DUT 身份必须由本收据独立绑定。

---

## 为什么要分流：本地快照没有 PR head

改动前，三处（`cpp_extension_driver._vendor_build_provenance`、
`cpp_extension_adapter._validate_vendor_build_receipt`、
`validate_acceptance_state._gate_cpp_extension_receipt`）各抄一份校验，且都**无条件**要求
`source.pr_head_sha` 是 40 位 hex。于是「PR 是一份无 `.git` 的本地快照」这条通路走进死路：

* 诚实填 `null` → 三处全部 fail-closed，产不出任何验收结论；
* 合成一个 40 位 hex 骗过门 → 那是**捏造 PR head**，直接违反 AGENTS.md 5.8。

`source_provenance` 早已为 intake 侧解决过同一个问题（`complete` / `snapshot_only` 两档）。
本模块把同一套契约延到 build receipt 侧：**按取源形态分流校验**，不放松任何一条可核事实。

## 三套词表的映射（不再造第四套）

| 层 | 字段 | 取值 | 定义处 |
|---|---|---|---|
| intake 事实包 | `provenance_kind` | `gitcode_pr` / `local_snapshot` | `fetch_source.py`、`source_provenance.py` |
| 真机执行配置 | `source_mode` | `git_fetch` / `local_snapshot` | adapter 侧 cfg |
| **本收据** | `source.provenance_kind` | **逐字沿用 intake 词表** | 本模块 |

收据侧刻意与 intake 同名同值，因此 receipt ↔ `source_facts` 可以**逐字**比对，不需要任何翻译。
执行配置那套 `git_fetch` 只在 adapter 边界出现，两者的换算表是
:data:`source_provenance.ADAPTER_KIND_TO_INTAKE`；本模块只**反转**它得到
:data:`RECEIPT_KIND_TO_SOURCE_MODE`，绝不重新拼写一遍字符串（重拼 = 第四套词表 = 必然漂移）。

## schema 版本与向后兼容

* `schema_version = 1`（历史）：**不得**出现 `source.provenance_kind`。它恒等于 `gitcode_pr`，
  逐条校验与改动前**逐字节相同**——旧收据、旧单测一条不改也照样过。
* `schema_version = 2`：**必须**显式声明 `source.provenance_kind`，按形态分流：
  * `gitcode_pr`     —— 与 v1 完全相同（40 位 head + 非空 repo）；
  * `local_snapshot` —— `pr_head_sha` 必须**显式 null**，改绑「仓根 + 算子子目录 + 整树 merkle +
    子树 merkle + 构建 argv + vendor ELF sha256」，并在收据顶层写死
    `degradations: ["pr_head_unbound"]` 作**机读挂账**。

「v1 带 kind」「v2 缺 kind」两种混搭一律 fail-closed：版本与字段必须成对，否则
「谁在用哪套规则校的」就成了读收据的人猜出来的。

本模块只读结构化字段，不含任何算子身份分派（AGENTS.md 5.1）。
"""

import os
import re

import source_provenance


class VendorBuildReceiptError(ValueError):
    """收据不可接受；调用方按自身约定收敛（raise 自己的异常 / 累计成 errs）。"""


SCHEMA = "oprunway.vendor_build_receipt"
#: 历史版本：无 `source.provenance_kind`，语义恒为 `gitcode_pr`。
SCHEMA_VERSION_LEGACY = 1
#: 当前版本：`source.provenance_kind` 必填，按形态分流。
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (SCHEMA_VERSION_LEGACY, SCHEMA_VERSION)

#: 收据侧取源形态词表 —— **逐字复用 intake 侧常量**，不另起名字。
PROVENANCE_GIT_PR = source_provenance.PROVENANCE_GIT_PR
PROVENANCE_LOCAL_SNAPSHOT = source_provenance.PROVENANCE_LOCAL_SNAPSHOT
KINDS = (PROVENANCE_GIT_PR, PROVENANCE_LOCAL_SNAPSHOT)

#: 「本轮 DUT 没有绑定任何上游 commit」的机读形式，同样复用 intake 侧常量。
DEGRADATION_PR_HEAD_UNBOUND = source_provenance.DEGRADATION_PR_HEAD_UNBOUND

#: 收据词表 → 真机执行配置 `cfg.source_mode` 词表。**由 intake 侧的映射反转得到**，
#: 保证三套词表只有一处定义：谁改了 `ADAPTER_KIND_TO_INTAKE`，这里自动跟着变。
RECEIPT_KIND_TO_SOURCE_MODE = {
    intake: adapter
    for adapter, intake in source_provenance.ADAPTER_KIND_TO_INTAKE.items()
}

_HEX40 = re.compile(r"[0-9a-fA-F]{40}\Z")
#: merkle 摘要统一小写（与 `source_provenance._require_hex64` 同口径）。
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")

#: 缺席哨兵：区分「键根本没写」与「显式写了 null」。两者用 `.get()` 都得到 `None`，
#: 而 local_snapshot 这一档的判据恰恰是「**显式**声明没有 PR head」——混同即等于让缺字段过门。
_ABSENT = object()


def _require_dict(value, where):
    if not isinstance(value, dict):
        raise VendorBuildReceiptError(f"{where} 须为 JSON object（实得 {type(value).__name__}）")
    return value


def _require_present(mapping, key, where):
    """取 `mapping[key]`，**键不存在即报错**（与 `source_provenance._require_present` 同一条纪律）。"""
    if key not in mapping:
        raise VendorBuildReceiptError(
            f"{where}.{key} 键缺失——「没写这个字段」与「显式写 null」不是一回事，不得混同")
    return mapping[key]


def _require_nonempty_str(value, where):
    if not isinstance(value, str) or not value.strip():
        raise VendorBuildReceiptError(f"{where} 须为非空字符串（实得 {value!r}）")
    return value


def _require_hex64(value, where):
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise VendorBuildReceiptError(f"{where} 须为 64 位小写十六进制摘要（实得 {value!r}）")
    return value


def _validate_envelope(receipt):
    _require_dict(receipt, "vendor build receipt")
    version = receipt.get("schema_version")
    if (receipt.get("schema") != SCHEMA
            or version not in SUPPORTED_SCHEMA_VERSIONS
            or receipt.get("status") != "VERIFIED"):
        raise VendorBuildReceiptError(
            f"vendor build receipt schema/status 非 VERIFIED（支持 schema_version "
            f"{list(SUPPORTED_SCHEMA_VERSIONS)}，实得 schema={receipt.get('schema')!r} "
            f"version={version!r} status={receipt.get('status')!r}）")
    return version


def _resolve_kind(receipt, source):
    """定出取源形态。版本与 `provenance_kind` 字段必须成对，混搭 fail-closed。"""
    version = receipt.get("schema_version")
    declared = source.get("provenance_kind", _ABSENT)
    if version == SCHEMA_VERSION_LEGACY:
        if declared is not _ABSENT:
            raise VendorBuildReceiptError(
                f"schema_version=1 的收据不得声明 source.provenance_kind（实得 {declared!r}）——"
                f"要按取源形态分流请升到 schema_version={SCHEMA_VERSION}")
        # v1 的语义恒为「绑了 PR head 的 gitcode PR」：与改动前逐字节同一套校验。
        return PROVENANCE_GIT_PR
    if declared is _ABSENT:
        raise VendorBuildReceiptError(
            f"schema_version={SCHEMA_VERSION} 的收据须显式声明 source.provenance_kind "
            f"（受控词表 {list(KINDS)}）——不缺省、不从别处推断")
    if declared not in KINDS:
        raise VendorBuildReceiptError(
            f"source.provenance_kind={declared!r} 非受控值，须属 {list(KINDS)}（fail-closed）")
    return declared


def _validate_degradations(receipt, expected):
    """收据顶层 `degradations` 必须**逐字**等于该形态应有的挂账表。

    不是「有就行」也不是「可以多写」：这张表会被下游报告原样带走，多一条=凭空挂账，
    少一条=把已知降级藏起来（AGENTS.md 5.8 两个方向都不允许）。
    v1 / gitcode_pr 无降级，故允许**键缺席**（历史收据没有这个键），但写了就必须是空表。
    """
    value = receipt.get("degradations", _ABSENT)
    if value is _ABSENT:
        if expected:
            raise VendorBuildReceiptError(
                f"该取源形态必须显式挂账 degradations={expected}，收据里却没有这个键——"
                "降级事实不得靠读者自己推断")
        return []
    if not isinstance(value, list) or value != list(expected):
        raise VendorBuildReceiptError(
            f"vendor build receipt.degradations 须逐字为 {list(expected)}（实得 {value!r}）")
    return list(expected)


def _validate_source(receipt):
    """校源身份并返回**归一化摘要**；`summarize` 复用同一实现，避免两处解释漂移。"""
    _validate_envelope(receipt)
    source = _require_dict(receipt.get("source"), "vendor build receipt.source")
    kind = _resolve_kind(receipt, source)

    if kind == PROVENANCE_GIT_PR:
        head = source.get("pr_head_sha")
        repo = source.get("repo")
        if (not isinstance(head, str) or not _HEX40.fullmatch(head)
                or not isinstance(repo, str) or not repo.strip()):
            raise VendorBuildReceiptError(
                "vendor build receipt 缺完整 PR head/source repo"
                f"（{PROVENANCE_GIT_PR} 档必须绑 40 位 commit SHA 与非空仓标识，"
                f"实得 head={head!r} repo={repo!r}）")
        degradations = _validate_degradations(receipt, [])
        return {
            "provenance_kind": kind,
            "pr_head_sha": head,
            "repo": repo,
            "snapshot_sha256": None,
            "snapshot_subtree_sha256": None,
            "snapshot_subtree_scope": None,
            "degradations": degradations,
        }

    # —— local_snapshot：无 head 可绑，改绑「范围 + 两个 merkle」——
    head = _require_present(source, "pr_head_sha", "vendor build receipt.source")
    if head is not None:
        raise VendorBuildReceiptError(
            f"{PROVENANCE_LOCAL_SNAPSHOT} 档的 source.pr_head_sha 须**显式** null（实得 {head!r}）——"
            "本地快照没有上游 commit，合成一个 40 位 hex 就是捏造 PR head（AGENTS.md 5.8）")
    repo = _require_nonempty_str(
        _require_present(source, "repo", "vendor build receipt.source"),
        "vendor build receipt.source.repo（本地快照的仓根标识）")
    scope = _require_present(
        source, "snapshot_subtree_scope", "vendor build receipt.source")
    if not isinstance(scope, str):
        raise VendorBuildReceiptError(
            "vendor build receipt.source.snapshot_subtree_scope 须为字符串"
            f"（空串= 仓根本身，属合法显式值；实得 {scope!r}）——"
            "merkle 没有范围就无法与 CP-A 事实包对账")
    whole = _require_hex64(
        _require_present(source, "snapshot_sha256", "vendor build receipt.source"),
        "vendor build receipt.source.snapshot_sha256（整树 merkle）")
    subtree = _require_hex64(
        _require_present(source, "snapshot_subtree_sha256", "vendor build receipt.source"),
        "vendor build receipt.source.snapshot_subtree_sha256（算子子树 merkle）")
    degradations = _validate_degradations(receipt, [DEGRADATION_PR_HEAD_UNBOUND])
    return {
        "provenance_kind": kind,
        "pr_head_sha": None,
        "repo": repo,
        "snapshot_sha256": whole,
        "snapshot_subtree_sha256": subtree,
        "snapshot_subtree_scope": scope,
        "degradations": degradations,
    }


def _validate_build(receipt):
    """构建命令：两种形态**同样强制**——没有成功的构建 argv 就没有「这个 so 是这么来的」。"""
    build = receipt.get("build")
    if (not isinstance(build, dict)
            or not isinstance(build.get("argv"), list)
            or not build["argv"]
            or any(not isinstance(x, str) or not x for x in build["argv"])
            or not isinstance(build.get("cwd"), str)
            or not build["cwd"]
            or build.get("returncode") != 0):
        raise VendorBuildReceiptError("vendor build receipt 缺成功 build argv/cwd/returncode")
    return build


def _validate_artifact(receipt, library_path, library_sha256, normalize_path):
    """安装 ELF：路径与摘要都必须与调用方现场绑定的那一个逐字相同。

    `normalize_path=True` 供**真机 driver** 使用（它拿到的是 realpath 后的绝对路径）；
    离线复核方（adapter / 验收门）比的是 receipt 里逐字记录的那个字符串，不做任何文件系统访问。
    """
    artifact = receipt.get("artifact")
    if not isinstance(artifact, dict):
        raise VendorBuildReceiptError("vendor build receipt.artifact 缺失或非 object")
    recorded = artifact.get("library_path")
    if normalize_path:
        recorded = os.path.realpath(recorded) if isinstance(recorded, str) else None
    if recorded != library_path or artifact.get("library_sha256") != library_sha256:
        raise VendorBuildReceiptError(
            "vendor build receipt 的安装 ELF 路径/摘要与实际绑定的 vendor library 不一致")
    return artifact


def validate(receipt, *, library_path, library_sha256, normalize_path=False):
    """完整校验一份收据，返回归一化的源身份摘要；任一条不满足即抛 `VendorBuildReceiptError`。"""
    summary = _validate_source(receipt)
    _validate_build(receipt)
    _validate_artifact(receipt, library_path, library_sha256, normalize_path)
    return summary


def summarize(receipt):
    """已过 :func:`validate` 的收据 → 同一份源身份摘要（幂等、无副作用）。

    刻意**不做**「跳过校验的快速读取」：摘要里每个字段都是判据，读它的地方就该确信它被校过。
    """
    return _validate_source(receipt)
