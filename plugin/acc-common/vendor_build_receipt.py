#!/usr/bin/env python3
"""`oprunway.vendor_build_receipt` 的**唯一校验处**（stdlib-only，不 import numpy/torch）。

这份收据回答的是一件事：**真机上被加载的那个 vendor `.so`，到底是从哪份源码、用哪条命令构建出来的**。
Extension 自身 build/load 成功只证明调用桥可用；DUT 身份必须由本收据独立绑定。

---

## 两件事：**校验**（本模块历史职责）与**生产**（本次新增）

改动前这份收据只有校验入口，收据本身**是人手拼 JSON 拼出来的**——易错且不可复现。
文件下半段新增一条确定性生产路径（`take_snapshot_digest` / `run_build` / `produce_receipt`
+ CLI 两个子命令），两个 merkle 直接调 `fetch_source._walk_snapshot` + `_snapshot_merkle`
（**同一份算法**，不复刻、不另写），产出前先自过一遍 :func:`validate`。

## ⚠ `build.returncode` 必须是**实测值**——没有「只记录不执行」模式

这条是 2026-08-06 补的 fail-open。此前生产路径长这样：`emit --returncode 0` 把退出码
**当参数收下**，而本文件里连 `import subprocess` 都没有——脚本从头到尾没执行过任何 build。
于是「手工拼一串 argv + `--returncode 0`」就能产出一份 `status=VERIFIED` 的收据，
而收据 schema 里**记不下**「这个 0 是实跑来的还是调用方自报的」，三处消费方（driver /
adapter / 三级门）分辨不出。这正是本仓最贵的两类缺陷之一：**宣称有门、其实没门**。

现在两头都堵上：

1. **产出侧**：:func:`run_build` 用 `subprocess.run` **真跑** build，`returncode` 是实测值；
   :func:`produce_receipt` 只接受 `run_build` 的返回值，**拿不到就产不出收据**。
   CLI 的 `--returncode` 降级成**可选的期望值断言**（与实测不符即拒），不再是被记录的那个值。
   ⚠ 刻意**不留**「只记录不执行」的逃生阀：这条路径上不存在任何需要自报退出码的合法场景，
   留一个下来就等于把刚堵上的洞重新开成一个开关（这条口径承自 `make_vendor_build_receipt.py`——
   曾经并存的另一份产出方，**已在合并中删除**，本模块现在是唯一产出方）。
2. **校验侧**：收据新增 `build.returncode_source`。受控值只有 `measured`；显式写
   `declared` 当场点名拒。**整个键缺席**只表示「本字段引入之前产的老收据」，归一化摘要里
   落 `unproven_legacy`——它与 `measured` 在下游**长得不一样**，读收据的人一眼分得出
   「实测过」与「没人知道」。⚠ 老收据仍能过校验（兼容），但摘要不会替它宣称实测过。

⚠ **如实记账**：`run_build` 另核「`--library` 在构建窗口内是否被改写」（构建前后
`(mtime_ns, size, sha256)` 三项全同即 fail-closed），堵的是「`-- /usr/bin/true` 配一个
预先存在的 CANN 内置 `.so`」。但它只证明**该文件在窗口内被改写过**，**不证明它由那条 argv
产出**——一次 `touch` 就能骗过。这条缺口是已知的（已删除的 `make_vendor_build_receipt.py` 同款，
两份实现当年各自独立踩到同一处边界）。

⚠ **本模块不读 `source_facts`**，如实记账：收据里的两个 merkle 由 `--source-root` 现算，
「build 的这棵树是不是 CP-A 取材的那棵」由**三级门**（`validate_acceptance_state` 比
`snapshot_subtree_sha256` ↔ `source_facts.pr.snapshot_merkle_sha256`）来判，本模块**不当场核**。
已删除的 `make_vendor_build_receipt.py` 曾在构建前 + 构建后各做一次这项对账并 fail-closed；
该能力随它一并消失，现在指错 `--source-root` 要跑到验收门才 BLOCK，而**构建后**那次
（「这次 build 有没有把被测子树改掉」）已无任何门在做，只剩 `build.tree_state_at_emit` 供人工读。

⚠ **merkle 必须在 build 之前算**：build 会往源码树里写产物，事后再摘就摘到了「源码 + 产物」，
与 CP-A 记的那份字节永远对不上（实测：build 后整树 merkle 变成另一个值）。
故生产路径**结构性地**杜绝这个错法——`produce_receipt` 不会自己去摘源码树，它只接受
`take_snapshot_digest` 在 build 前落下的摘要文件；同时把**产出时刻**的树摘要一并记进
`build.tree_state_at_emit`，build 到底动没动源码树因此是可审的。

（另一种设想是「产出时自己检测树里已有构建产物就拒绝」。不采用：那需要枚举「构建产物长什么样」，
是 denylist；而且 `_walk_snapshot` 本就按名跳过 `build/`、`output/`，build 若把生成的头文件、
`.d`、`cmake-build-*` 写在别处，检测不到却照样改了 merkle —— 给的是假保证。）

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
    子树 merkle + 构建 argv + vendor ELF sha256」。这六项一条不放松。

`degradations` 的口径随 :mod:`source_provenance` 的重构一并改了：本地源码是**一等输入形态**，
不是降级，所以 `local_snapshot` 档**不再强制**写 `["pr_head_unbound"]`——

* 收据显式声明 `source.declared_source_form` 时，逐条硬校：
  `local_source` → 必须无降级；`git_pr` + `local_snapshot`（本该绑 PR head 却只拿到快照）
  → 必须挂 `["pr_head_unbound"]`；
* 未声明（旧收据）→ 按**最严**的一档处理，仍必须挂 `["pr_head_unbound"]`，与改动前逐字同规矩
  （同 :mod:`source_provenance` 对未声明事实包的兼容口径）。已经产出的现场收据因此一条不改
  照样过门，而「不再强制挂账」只对**显式声明了本地源码形态**的新收据成立。

「v1 带 kind」「v2 缺 kind」两种混搭一律 fail-closed：版本与字段必须成对，否则
「谁在用哪套规则校的」就成了读收据的人猜出来的。

本模块只读结构化字段，不含任何算子身份分派（AGENTS.md 5.1）。
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

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

#: 「本该绑上游 commit 却没绑」的机读降级形式，同样复用 intake 侧常量。
#: ⚠ 它**不**表示「没有上游 commit」——本地源码本来就没有，那是形态事实、不是降级。
DEGRADATION_PR_HEAD_UNBOUND = source_provenance.DEGRADATION_PR_HEAD_UNBOUND

#: **声明**的输入形态，逐字复用 intake 侧词表（`git_pr` / `local_source`）。
DECLARED_FORM_KEY = source_provenance.DECLARED_FORM_KEY
FORM_GIT_PR = source_provenance.FORM_GIT_PR
FORM_LOCAL_SOURCE = source_provenance.FORM_LOCAL_SOURCE
DECLARED_SOURCE_FORMS = source_provenance.DECLARED_SOURCE_FORMS

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

#: `build.returncode_source` —— 回答「收据里那个 returncode 是怎么来的」。
#: 它存在的理由见模块 docstring：没有这个字段时，实测值与自报值在下游**长得一模一样**。
RETURNCODE_SOURCE_KEY = "returncode_source"
#: 唯一被收据接受的取值：由 :func:`run_build` 用 `subprocess.run` 实测。
RETURNCODE_SOURCE_MEASURED = "measured"
#: 只作**显式拒绝值**存在（本模块永不产出）。别处若产了自报收据，这里当场点名拒——
#: 而不是让它在下游与实测的长得一样，等着被当成真裁决读走。
RETURNCODE_SOURCE_DECLARED = "declared"
#: **归一化摘要**里的第三档：收据里根本没有这个键 = 本字段引入之前产的老收据。
#: ⚠ 只在摘要里出现；**收据里写这个值一律拒**——「没记过」不许伪装成一个已知档位。
RETURNCODE_SOURCE_UNPROVEN_LEGACY = "unproven_legacy"


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


def _read_declared_form(source):
    """收据自报的**声明输入形态**；未声明 → None（旧收据），词表外 → fail-closed。"""
    value = source.get(DECLARED_FORM_KEY, _ABSENT)
    if value is _ABSENT or value is None:
        return None
    if value not in DECLARED_SOURCE_FORMS:
        raise VendorBuildReceiptError(
            f"vendor build receipt.source.{DECLARED_FORM_KEY}={value!r} 非受控值，"
            f"须属 {list(DECLARED_SOURCE_FORMS)}（fail-closed）")
    return value


def _expected_degradations(kind, form):
    """该 (实得形态 × 声明形态) 组合**允许**的 degradations 表；返回候选元组。

    判据与 :mod:`source_provenance` 同一套：降级 = 「本该绑上游 commit 却没绑」，
    而不是「没有上游 commit」。

    * 声明 `local_source` + 实得 `local_snapshot` → 声明即所得，**必须无降级**；
    * 声明 `git_pr` + 实得 `local_snapshot`       → 本该绑却没绑，**必须**挂 `pr_head_unbound`；
    * 声明 `git_pr` + 实得 `gitcode_pr`           → 无降级；
    * 未声明（旧收据）+ 实得 `local_snapshot`     → 按**最严**一档，仍必须挂 `pr_head_unbound`
      （与改动前逐字同规矩：不声明就不享受新口径，已产出的现场收据照样过门）；
    * 未声明 + 实得 `gitcode_pr`                  → 无降级。
    """
    if kind == PROVENANCE_GIT_PR:
        if form == FORM_LOCAL_SOURCE:
            raise VendorBuildReceiptError(
                f"收据声明 {DECLARED_FORM_KEY}={FORM_LOCAL_SOURCE}（本地源码），"
                f"实得却是绑定上游 commit 的 {PROVENANCE_GIT_PR}——声明与实得不是同一件事")
        return ([],)
    if form == FORM_LOCAL_SOURCE:
        return ([],)
    return ([DEGRADATION_PR_HEAD_UNBOUND],)         # 声明 git_pr、或旧收据未声明


def _validate_degradations(receipt, accepted):
    """收据顶层 `degradations` 必须**逐字**等于 `accepted` 里的某一张表。

    不是「有就行」也不是「可以多写」：这张表会被下游报告原样带走，多一条=凭空挂账，
    少一条=把已知降级藏起来（AGENTS.md 5.8 两个方向都不允许）。
    键缺席**等价于空表**（历史收据没有这个键）；空表不被接受时，缺席同样不被接受。
    """
    options = [list(item) for item in accepted]
    value = receipt.get("degradations", _ABSENT)
    if value is _ABSENT:
        if [] in options:
            return []
        raise VendorBuildReceiptError(
            f"该取源形态必须显式挂账 degradations={options[0]}，收据里却没有这个键——"
            "降级事实不得靠读者自己推断")
    if not isinstance(value, list) or value not in options:
        raise VendorBuildReceiptError(
            f"vendor build receipt.degradations 须逐字为 "
            f"{' 或 '.join(repr(item) for item in options)}（实得 {value!r}）")
    return list(value)


def _validate_source(receipt):
    """校源身份并返回**归一化摘要**；`summarize` 复用同一实现，避免两处解释漂移。"""
    version = _validate_envelope(receipt)
    source = _require_dict(receipt.get("source"), "vendor build receipt.source")
    kind = _resolve_kind(receipt, source)
    form = _read_declared_form(source)
    if form is not None and version == SCHEMA_VERSION_LEGACY:
        raise VendorBuildReceiptError(
            f"schema_version=1 的收据不得声明 source.{DECLARED_FORM_KEY}"
            f"（实得 {form!r}）——要按声明形态判降级请升到 schema_version={SCHEMA_VERSION}")
    accepted = _expected_degradations(kind, form)

    if kind == PROVENANCE_GIT_PR:
        head = source.get("pr_head_sha")
        repo = source.get("repo")
        if (not isinstance(head, str) or not _HEX40.fullmatch(head)
                or not isinstance(repo, str) or not repo.strip()):
            raise VendorBuildReceiptError(
                "vendor build receipt 缺完整 PR head/source repo"
                f"（{PROVENANCE_GIT_PR} 档必须绑 40 位 commit SHA 与非空仓标识，"
                f"实得 head={head!r} repo={repo!r}）")
        degradations = _validate_degradations(receipt, accepted)
        return {
            # 声明形态进摘要（2026-08-05）：报告第一层就要能分出「本轮本来就是本地代码」
            # 与「本来要测 PR、只拿到快照」——前者 degradations 为空是**正常**，后者才是降级。
            # 漏了它，evidence 里 declared_source_form 恒 null，两种情形在产物上长得一样。
            DECLARED_FORM_KEY: form,
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
    degradations = _validate_degradations(receipt, accepted)
    return {
        # 声明形态进摘要（2026-08-05）：报告第一层就要能分出「本轮本来就是本地代码」
        # 与「本来要测 PR、只拿到快照」——前者 degradations 为空是**正常**，后者才是降级。
        # 漏了它，evidence 里 declared_source_form 恒 null，两种情形在产物上长得一样。
        DECLARED_FORM_KEY: form,
        "provenance_kind": kind,
        "pr_head_sha": None,
        "repo": repo,
        "snapshot_sha256": whole,
        "snapshot_subtree_sha256": subtree,
        "snapshot_subtree_scope": scope,
        "degradations": degradations,
    }


def _validate_returncode_source(build):
    """定出 `build.returncode` 的**出身**；受控词表外一律 fail-closed。

    三档，且**下游必须分得开**（这正是本字段存在的全部理由）：

    * `measured`         —— :func:`run_build` 用 `subprocess.run` 实测；唯一可写进收据的值；
    * `declared`         —— 调用方自报。本模块**永不产出**，见到即当场点名拒：
      自报的退出码不是构建证据，而一份 `status=VERIFIED` 的收据会被当成机器可核事实读走；
    * 键**整个缺席**      —— 本字段引入之前产的老收据。兼容放行，但摘要里落
      `unproven_legacy`：它与 `measured` **长得不一样**，不替老收据宣称「实测过」。
    """
    value = build.get(RETURNCODE_SOURCE_KEY, _ABSENT)
    if value is _ABSENT:
        return RETURNCODE_SOURCE_UNPROVEN_LEGACY
    if value == RETURNCODE_SOURCE_MEASURED:
        return RETURNCODE_SOURCE_MEASURED
    if value == RETURNCODE_SOURCE_DECLARED:
        raise VendorBuildReceiptError(
            f"vendor build receipt.build.{RETURNCODE_SOURCE_KEY}="
            f"{RETURNCODE_SOURCE_DECLARED!r}：调用方自报的 returncode 不是构建证据，"
            "而这份收据存在的全部意义就是机器可核。请走真跑 build 的产出路径重产"
            f"（本模块 CLI 的 `emit` 会自己执行 --build-argv 并记 {RETURNCODE_SOURCE_MEASURED!r}）。")
    raise VendorBuildReceiptError(
        f"vendor build receipt.build.{RETURNCODE_SOURCE_KEY}={value!r} 非受控值，"
        f"须为 {RETURNCODE_SOURCE_MEASURED!r}；**整个键缺席**才表示本字段引入前的老收据"
        f"（摘要落 {RETURNCODE_SOURCE_UNPROVEN_LEGACY!r}），不得把它当成一个可写的取值。")


def _validate_build(receipt):
    """构建命令 + returncode 的出身。

    两种取源形态**同样强制**——没有成功的构建 argv 就没有「这个 so 是这么来的」；
    而没有 `returncode_source`，「这个 0 是实跑还是自报」就成了读者猜出来的（模块 docstring）。
    """
    build = receipt.get("build")
    if (not isinstance(build, dict)
            or not isinstance(build.get("argv"), list)
            or not build["argv"]
            or any(not isinstance(x, str) or not x for x in build["argv"])
            or not isinstance(build.get("cwd"), str)
            or not build["cwd"]
            or build.get("returncode") != 0):
        raise VendorBuildReceiptError("vendor build receipt 缺成功 build argv/cwd/returncode")
    return build, _validate_returncode_source(build)


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
    """完整校验一份收据，返回归一化摘要；任一条不满足即抛 `VendorBuildReceiptError`。

    = :func:`summarize`（信封 + 源身份 + 构建段）**再加**「安装 ELF 与调用方现场绑定的
    那一个逐字相同」。两者共用同一条实现，避免离线复核方与真机 driver 各解释一遍。
    """
    summary = summarize(receipt)
    _validate_artifact(receipt, library_path, library_sha256, normalize_path)
    return summary


def summarize(receipt):
    """收据 → 归一化摘要（幂等、无副作用）：源身份 + `build_returncode_source`。

    刻意**不做**「跳过校验的快速读取」：摘要里每个字段都是判据，读它的地方就该确信它被校过。

    ⚠ `build_returncode_source` 是 2026-08-06 加进摘要的，理由是**可见性**：
    「这份收据的 returncode 到底是不是实测的」必须在报告/证据的第一层就看得见，
    而不是埋在嵌套的 `build` 里等人自己翻（同 `degradations` 的口径，AGENTS.md 5.8）。
    driver 落的 `vendor.source_provenance` 与离线复核方重算的结果仍逐字比对，故两侧
    **必须**同时含这个键——只加一侧会让所有旧 driver 收据在新 adapter 上报「源身份不一致」。
    """
    summary = _validate_source(receipt)
    summary["build_returncode_source"] = _validate_build(receipt)[1]
    return summary


# ── 自定义算子包（custom opp vendor）的安装布局 ────────────────────────────────────
#
# 收据回答「这个 `.so` 从哪来」；下面这条规则回答紧挨着的第二个问题：**运行时到哪去找它**。
# 两者必须同源——否则 receipt 绑的是 A 包、真机跑的却是 B 包的符号，而没有任何产物看得出来。
# 故判据同样只写一处，driver / 性能 wrapper / 验收门共用。

#: CANN 自定义算子包的固定目录结构：``<install_root>/vendors/<pkg>/op_api/lib/<lib>.so``。
#: 这些是 **CANN 的安装约定**，不是某个算子或某个 vendor 的身份——换任何算子、任何 vendor
#: 名，这条反推规则零改即用（AGENTS.md 5.1）。
_VENDOR_LIB_DIR_TAIL = ("op_api", "lib")
_VENDOR_PARENT_DIR_NAME = "vendors"

#: torch_npu 运行时 **getenv** 这个变量去定位自定义算子的 `libcust_opapi.so`。不设它，
#: `aclnnXxx` / `aclnnXxxGetWorkspaceSize` 只会在 CANN 内置 `libopapi.so` 里找，于是逐条
#: 报 ``not in libopapi.so, or libopapi.so not found``。vendor 自带的 `bin/set_env.bash`
#: 设的就是它（外加 `LD_LIBRARY_PATH`）。
CUSTOM_OPP_ENV = "ASCEND_CUSTOM_OPP_PATH"


def custom_opp_path(library_path):
    """由 vendor `.so` 的**绝对路径**反推它所属自定义算子包的根。

    返回值就是 :data:`CUSTOM_OPP_ENV` 该取的值——与 vendor 安装时自己生成的
    `bin/set_env.bash` 里那一条逐字同义，但这里是从**已被 build receipt 绑定**的那个
    `.so` 反推出来的，因此和「谁 source 过哪个脚本」无关。

    纯字符串推导，不碰文件系统：真机 driver 传进来的已是 realpath，离线验收门传的是收据里
    逐字记录的字符串，两侧必须算出同一个值才算对上账。

    结构对不上一律 fail-closed —— 猜一个根出来，等于把「符号来自哪个 vendor」重新变成
    不可核事实。
    """
    if not isinstance(library_path, str) or not library_path.strip():
        raise VendorBuildReceiptError("vendor library_path 须为非空字符串，无法反推自定义算子包根")
    path = os.path.normpath(library_path)
    if not os.path.isabs(path):
        raise VendorBuildReceiptError(
            f"vendor library_path 须为绝对路径，得 {library_path!r}——相对路径标识不了一个 vendor 包")
    lib_dir = os.path.dirname(path)
    parent, lib_tail = os.path.split(lib_dir)
    pkg, api_tail = os.path.split(parent)
    if (api_tail, lib_tail) != _VENDOR_LIB_DIR_TAIL:
        raise VendorBuildReceiptError(
            f"vendor library_path 不符合自定义算子包布局 <root>/{_VENDOR_PARENT_DIR_NAME}/<pkg>/"
            f"{'/'.join(_VENDOR_LIB_DIR_TAIL)}/<lib>.so：得 {library_path!r}")
    vendors_dir, pkg_name = os.path.split(pkg)
    if not pkg_name or os.path.basename(vendors_dir) != _VENDOR_PARENT_DIR_NAME:
        raise VendorBuildReceiptError(
            f"vendor 包根 {pkg!r} 的父目录不是 {_VENDOR_PARENT_DIR_NAME}/——"
            f"这不是一个 CANN 自定义算子包的安装位置（fail-closed，不猜）")
    return pkg


# ── 确定性生产路径 ────────────────────────────────────────────────────────────
#
# 到本次改动为止，真机上那两轮验收的 vendor build receipt **都是人手拼 JSON 拼出来的**：
# 既容易拼错（少一个字段就 fail-closed，多填一个合成值就直接违反 5.8），也不可复现。
# 下面这条路径把它变成两条命令：build **之前**取一次源码树摘要，build 之后据它产收据。
#
# 顺序不是风格问题，是**正确性**问题：build 会往源码树里写产物，事后再摘就摘到了
# 「源码 + 产物」，与 CP-A 记的那份字节永远对不上。故 :func:`produce_receipt`
# **不提供**「现场自己摘一遍」的口子——没有 build 前摘要就产不出收据。

#: build 前源码树摘要的 envelope。它不是验收工件，只是生产路径的中间凭据。
SNAPSHOT_DIGEST_SCHEMA = "oprunway.source_snapshot_digest"
SNAPSHOT_DIGEST_VERSION = 1

_PRODUCER_TOOL = "vendor_build_receipt.py"


def _fetch_source():
    """惰性导入 intake 侧模块。

    两个 merkle 必须**用 intake 自己的算法**算（`_walk_snapshot` + `_snapshot_merkle`），
    不复刻第二份：复刻出来的哪怕只差一个跳过目录名，两端就永远对不上，而症状会表现成
    「字节没改却 merkle 不一致」，极难归因（同 `aclnn_adapter` 那段内联脚本的理由）。
    惰性导入是为了不给纯校验方（driver / adapter / 验收门）平白多一个模块依赖。
    """
    import fetch_source
    return fetch_source


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalized_scope(fetch_source, source_root, subtree_scope):
    """规范化子树 scope（空串= 整棵树），并确认它在树里真实存在。

    走 intake 侧的 `_norm_target_dir`，与 `fetch_source --target-dir` **同一条口径**：
    两个 merkle 的 scope 只要有一端漂了就不可比。
    """
    if subtree_scope in (None, ""):
        return ""
    _op, scope = fetch_source._norm_target_dir(subtree_scope)
    full = os.path.join(source_root, *scope.split("/"))
    if not os.path.isdir(full) or os.path.islink(full):
        raise VendorBuildReceiptError(
            f"子树 scope 在源码树里不存在（或是符号链接）：{scope}（树根 {source_root}）")
    return scope


def take_snapshot_digest(source_root, subtree_scope=""):
    """**build 之前**对源码树取一次摘要 → 可落盘的中间凭据（纯读，不改任何东西）。

    产出的两个 merkle 与 intake 侧 `scan_pr_snapshot` 逐字同算法、同 scope 语义：
    整树摘要给执行配置对账，子树摘要给「CP-A 读的字节 == 真机 build 的字节」对账。

    `algorithm.logic_sha256` 钉的是 `fetch_source.py` 本身：算法一改，旧摘要立即失效，
    不会被一份口径已变的摘要悄悄拿去产收据。
    """
    fetch_source = _fetch_source()
    root = os.path.realpath(os.fspath(source_root))
    if not os.path.isdir(root):
        raise VendorBuildReceiptError(f"源码树不存在或不是目录：{source_root!r}")
    scope = _normalized_scope(fetch_source, root, subtree_scope)
    whole_rels = fetch_source._walk_snapshot(root, "")
    subtree_rels = fetch_source._walk_snapshot(root, scope)
    return {
        "schema": SNAPSHOT_DIGEST_SCHEMA,
        "schema_version": SNAPSHOT_DIGEST_VERSION,
        # 取摘要的时机是这份凭据的全部意义所在，写进字面量让读的人一眼看见。
        "taken_stage": "pre_build",
        "source_root": root,
        "subtree_scope": scope,
        "snapshot_sha256": fetch_source._snapshot_merkle(root, whole_rels),
        "snapshot_subtree_sha256": fetch_source._snapshot_merkle(root, subtree_rels),
        "file_count": len(whole_rels),
        "subtree_file_count": len(subtree_rels),
        "algorithm": {
            "tool": "fetch_source.py",
            "logic_sha256": _sha256_file(
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "fetch_source.py")),
        },
    }


def _validate_snapshot_digest(digest):
    """校 build 前摘要凭据；算法漂了、字段缺了一律拒（宁可重摘，不产错账）。"""
    _require_dict(digest, "snapshot digest")
    if (digest.get("schema") != SNAPSHOT_DIGEST_SCHEMA
            or digest.get("schema_version") != SNAPSHOT_DIGEST_VERSION
            or digest.get("taken_stage") != "pre_build"):
        raise VendorBuildReceiptError(
            f"snapshot digest envelope 不受支持（须 schema={SNAPSHOT_DIGEST_SCHEMA}、"
            f"schema_version={SNAPSHOT_DIGEST_VERSION}、taken_stage=pre_build）")
    root = _require_nonempty_str(
        _require_present(digest, "source_root", "snapshot digest"),
        "snapshot digest.source_root")
    scope = _require_present(digest, "subtree_scope", "snapshot digest")
    if not isinstance(scope, str):
        raise VendorBuildReceiptError(
            "snapshot digest.subtree_scope 须为字符串（空串= 整棵树，属合法显式值）")
    whole = _require_hex64(
        _require_present(digest, "snapshot_sha256", "snapshot digest"),
        "snapshot digest.snapshot_sha256")
    subtree = _require_hex64(
        _require_present(digest, "snapshot_subtree_sha256", "snapshot digest"),
        "snapshot digest.snapshot_subtree_sha256")
    algorithm = _require_dict(digest.get("algorithm"), "snapshot digest.algorithm")
    recorded = algorithm.get("logic_sha256")
    current = _sha256_file(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_source.py"))
    if recorded != current:
        raise VendorBuildReceiptError(
            "snapshot digest 记的摘要算法（fetch_source.py logic_sha256）与当前不一致："
            f"记={recorded!r} 现={current!r}——两端算法不同则 merkle 不可比，请重取摘要")
    return root, scope, whole, subtree


def _library_state(path):
    """`(mtime_ns, size, sha256)` 三元组；文件不在 → None（**不是**空 dict，两件事要分得开）。"""
    if not os.path.isfile(path):
        return None
    st = os.stat(path)
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "sha256": _sha256_file(path)}


def _utc(epoch_seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))


def _validate_build_argv(build_argv, build_cwd):
    argv = list(build_argv or [])
    if not argv or any(not isinstance(x, str) or not x for x in argv):
        raise VendorBuildReceiptError("build_argv 须为非空字符串数组（逐个实参，不要整条命令行）")
    if not isinstance(build_cwd, str) or not build_cwd:
        raise VendorBuildReceiptError("build_cwd 须为非空字符串")
    if not os.path.isdir(build_cwd):
        raise VendorBuildReceiptError(f"build_cwd 不是已存在的目录：{build_cwd!r}")
    return argv


def run_build(build_argv, build_cwd, library_path):
    """**真跑** build，返回唯一能喂给 :func:`produce_receipt` 的构建结果（有副作用）。

    ⚠ **没有「只记录不执行」模式**，这是刻意的，见模块 docstring：把退出码当参数收下的那条
    路径产出的收据长得和实测的一模一样，等于「宣称有门其实没门」。本函数是收据里
    `build.returncode` 的**唯一**来源，`returncode_source` 因此恒为 `measured`。

    另核一条绑定（口径同已删除的 `make_vendor_build_receipt.py`）：**`library_path` 必须真的被这次
    构建改写过**——构建前后各取一次 `(mtime_ns, size, sha256)`，三项全同即 fail-closed。
    堵的是「argv 写 `/usr/bin/true`、`--library` 指一个预先存在的 CANN 内置 `.so`」。
    ⚠ 它只证明**该文件在构建窗口内被改写过**，不证明它由这条 argv 产出（`touch` 即可骗过）。

    ⚠ 不捕获子进程输出：真机 build 的日志又长又是操作者要看的，吞掉它比留着危险得多。
    也不设超时——vendor 全量构建本来就以十分钟计。
    """
    argv = _validate_build_argv(build_argv, build_cwd)
    target = os.path.realpath(os.fspath(library_path))
    before = _library_state(target)
    started = time.time()
    try:
        run = subprocess.run(argv, cwd=build_cwd, check=False)
    except (OSError, subprocess.SubprocessError) as ex:
        raise VendorBuildReceiptError(
            f"构建命令没能执行起来：{argv!r}（cwd={build_cwd!r}；{ex}）") from ex
    ended = time.time()
    after = _library_state(target)
    if after is None:
        raise VendorBuildReceiptError(
            f"构建结束后 vendor ELF 仍不存在或不是普通文件：{target}"
            f"（实测 returncode={run.returncode}）——build 与 install 是两步时要并进同一条命令"
            "（`bash -c \"./build.sh … && ./build_out/*.run --install-path=…\"`），"
            "否则这里根本没有文件可摘")
    if before is not None and before == after:
        raise VendorBuildReceiptError(
            f"vendor ELF 在这次构建窗口内一个字节都没变：{target}"
            "（mtime_ns / size / sha256 三项全同）——这份收据要证明的正是「这个 .so 由这次 build "
            "产出」，对着一个预先存在、构建根本没碰过的文件出收据 = 宣称有门其实没门（fail-closed）")
    return {
        "argv": argv,
        "cwd": build_cwd,
        "returncode": run.returncode,
        RETURNCODE_SOURCE_KEY: RETURNCODE_SOURCE_MEASURED,
        "execution": {
            "started_at": _utc(started),
            "ended_at": _utc(ended),
            "duration_s": round(ended - started, 3),
            "library_path": target,
            "library_before": before,
            "library_after": after,
        },
    }


def _validate_build_result(build_result):
    """`build_result` 必须是 :func:`run_build` 的返回值——**产出侧的唯一入口约束**。

    校的不是「长得像」，而是「只有真跑过才拿得到」的那几项：`returncode_source=measured`、
    构建窗口两端的 ELF 状态、以及构建后那份状态与磁盘上现在这个文件逐字相同。
    """
    result = _require_dict(build_result, "build_result")
    if result.get(RETURNCODE_SOURCE_KEY) != RETURNCODE_SOURCE_MEASURED:
        raise VendorBuildReceiptError(
            f"build_result.{RETURNCODE_SOURCE_KEY} 须为 {RETURNCODE_SOURCE_MEASURED!r}——"
            "构建结果只能来自 run_build()（真跑），本模块没有「只记录不执行」的产出路径")
    _validate_build_argv(result.get("argv"), result.get("cwd"))
    if not isinstance(result.get("returncode"), int):
        raise VendorBuildReceiptError("build_result.returncode 须为整数（run_build 的实测值）")
    execution = _require_dict(result.get("execution"), "build_result.execution")
    elf = _require_nonempty_str(
        _require_present(execution, "library_path", "build_result.execution"),
        "build_result.execution.library_path")
    after = execution.get("library_after")
    if not isinstance(after, dict) or after != _library_state(elf):
        raise VendorBuildReceiptError(
            f"build_result 记的构建后 ELF 状态与磁盘上现在的 {elf} 不一致——"
            "构建结束到产收据之间这个文件又被动过，绑定失效（fail-closed，不猜哪一份是真的）")
    return result


def produce_receipt(*, declared_source_form, build_result,
                    repo=None, snapshot_digest=None, pr_head_sha=None):
    """据 build 现场事实产一份**已自过 validate()** 的 vendor build receipt。

    三条路由与 :mod:`source_provenance` 一一对应：

    * `declared_source_form=local_source` + `snapshot_digest` → `local_snapshot`，**无降级**；
    * `declared_source_form=git_pr`       + `pr_head_sha`     → `gitcode_pr`，无降级；
    * `declared_source_form=git_pr`       + `snapshot_digest` → `local_snapshot`，
      挂 `["pr_head_unbound"]`（本该绑 PR head 却只拿到一份快照 = 降级）。

    ⚠ `build_result` 只能是 :func:`run_build` 的返回值——**拿不到就产不出收据**。
    这条约束替掉了原先那个 `returncode=0` 参数：退出码是实测出来的，不是调用方说了算的。

    ⚠ 本函数**不会**自己去摘源码树：`snapshot_digest` 必须是 :func:`take_snapshot_digest`
    在 build 之前落下的那一份。产出时会另摘一次当前树状态记进
    `build.tree_state_at_emit`，让「build 到底动没动源码树」在收据里可审。
    """
    if declared_source_form not in DECLARED_SOURCE_FORMS:
        raise VendorBuildReceiptError(
            f"declared_source_form={declared_source_form!r} 非受控值，"
            f"须属 {list(DECLARED_SOURCE_FORMS)}")
    if (snapshot_digest is None) == (pr_head_sha is None):
        raise VendorBuildReceiptError(
            "snapshot_digest 与 pr_head_sha 必须**恰好给一个**："
            "前者绑本地源码的字节身份，后者绑上游 commit，两者不是一回事，也不能都不给")
    result = _validate_build_result(build_result)
    argv, build_cwd = result["argv"], result["cwd"]
    returncode = result["returncode"]
    if returncode != 0:
        raise VendorBuildReceiptError(
            f"build returncode={returncode!r}（实测）：构建没成功就没有「这个 so 是这么来的」可言，"
            "不产收据（fail-closed）")
    elf = result["execution"]["library_path"]
    elf_sha = result["execution"]["library_after"]["sha256"]

    build = {"argv": argv, "cwd": build_cwd, "returncode": returncode,
             RETURNCODE_SOURCE_KEY: RETURNCODE_SOURCE_MEASURED,
             "execution": dict(result["execution"])}
    if snapshot_digest is not None:
        root, scope, whole, subtree = _validate_snapshot_digest(snapshot_digest)
        kind = PROVENANCE_LOCAL_SNAPSHOT
        source = {
            "provenance_kind": kind,
            DECLARED_FORM_KEY: declared_source_form,
            # 本地源码没有上游 commit —— 显式 null 是**正确值**，绝不合成 40 位 hex（5.8）。
            "pr_head_sha": None,
            "repo": repo or root,
            "snapshot_subtree_scope": scope,
            "snapshot_sha256": whole,
            "snapshot_subtree_sha256": subtree,
        }
        build["source_snapshot_digest"] = {
            "schema": SNAPSHOT_DIGEST_SCHEMA,
            "schema_version": SNAPSHOT_DIGEST_VERSION,
            "taken_stage": "pre_build",
            "source_root": root,
            "subtree_scope": scope,
            "file_count": snapshot_digest.get("file_count"),
            "subtree_file_count": snapshot_digest.get("subtree_file_count"),
        }
        build["tree_state_at_emit"] = _tree_state_at_emit(root, scope, whole, subtree)
    else:
        kind = PROVENANCE_GIT_PR
        if not isinstance(pr_head_sha, str) or not _HEX40.fullmatch(pr_head_sha):
            raise VendorBuildReceiptError(
                f"pr_head_sha 须为 40 位 commit SHA（实得 {pr_head_sha!r}）")
        source = {
            "provenance_kind": kind,
            DECLARED_FORM_KEY: declared_source_form,
            "pr_head_sha": pr_head_sha,
            "repo": _require_nonempty_str(repo, "repo（PR 通路必须显式给仓标识）"),
        }
    receipt = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": "VERIFIED",
        "source": source,
        "build": build,
        "artifact": {"library_path": elf, "library_sha256": elf_sha},
        "degradations": list(_expected_degradations(kind, declared_source_form)[0]),
        "producer": {
            "tool": _PRODUCER_TOOL,
            "logic_sha256": _sha256_file(os.path.abspath(__file__)),
        },
    }
    # 自过一遍门：产出的东西若过不了本模块自己的校验，就不该落盘。
    validate(receipt, library_path=elf, library_sha256=elf_sha, normalize_path=True)
    return receipt


def _tree_state_at_emit(root, scope, pre_whole, pre_subtree):
    """产出时刻再摘一次源码树，如实记录它与 build 前是否还一样。

    `matches_pre_build=False` 是**预期常态**（build 往树里写了产物），记它不是为了报警，
    而是为了让「收据里那两个 merkle 是 build 前的值」这件事在产物里可审——
    否则读收据的人无从判断摘要是 build 前取的还是事后补的。
    """
    fetch_source = _fetch_source()
    if not os.path.isdir(root):
        raise VendorBuildReceiptError(
            f"源码树在产出收据时已不可读：{root!r}——无法如实记录树状态，fail-closed")
    whole = fetch_source._snapshot_merkle(root, fetch_source._walk_snapshot(root, ""))
    subtree = fetch_source._snapshot_merkle(
        root, fetch_source._walk_snapshot(root, scope))
    return {
        "snapshot_sha256": whole,
        "snapshot_subtree_sha256": subtree,
        "matches_pre_build": (whole == pre_whole and subtree == pre_subtree),
    }


def _write_json(path, payload):
    with open(path, "w", encoding="utf-8") as out:
        json.dump(payload, out, ensure_ascii=False, indent=2, sort_keys=True)
        out.write("\n")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="vendor build receipt 的确定性生产路径（merkle 必须在 build 之前取）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("snapshot-digest",
                       help="**build 之前**跑：对源码树取两个 merkle，落成中间凭据")
    d.add_argument("--source-root", required=True, help="源码树根（本地源码/快照目录）")
    d.add_argument("--subtree-scope", default="",
                   help="算子子树相对路径（与 fetch_source --target-dir 同一段；空= 整棵树）")
    d.add_argument("--out", required=True, help="凭据落盘路径")

    e = sub.add_parser(
        "emit",
        help="**真跑 --build-argv 那条构建命令**，再据凭据 + 实测事实产收据（有副作用）")
    e.add_argument("--declared-source-form", required=True,
                   choices=list(DECLARED_SOURCE_FORMS),
                   help="本轮**声明**的输入形态；决定这份收据该不该挂降级")
    e.add_argument("--snapshot-digest", default=None,
                   help="snapshot-digest 子命令产的凭据（本地源码通路必给）")
    e.add_argument("--pr-head-sha", default=None, help="PR 通路的 40 位 head commit SHA")
    e.add_argument("--repo", default=None,
                   help="仓标识；本地源码通路缺省取凭据里的源码树根")
    e.add_argument("--library", required=True, help="被加载的 vendor ELF 绝对路径")
    e.add_argument("--build-cwd", required=True, help="构建命令的工作目录")
    e.add_argument("--returncode", type=int, default=None,
                   help="**期望**的构建退出码（可选断言）。⚠ 收据里记的是本命令**实测**到的值："
                        "`emit` 会真的执行 --build-argv，没有「只记录不执行」模式；"
                        "期望值与实测不符即拒、不产收据。")
    e.add_argument("--build-argv", action="append", default=[], metavar="ARG",
                   help="**要被执行的**构建命令的单个实参，按顺序重复给（不做任何 shell 切分，"
                        "不经 shell）。build 与 install 是两步时并进同一条："
                        "`--build-argv=bash --build-argv=-c --build-argv='./build.sh && ./x.run …'`。"
                        "⚠ 实参以 `-` 开头时**必须**写 `--build-argv=--pkg` 这种等号形式："
                        "分开写会被 argparse 当成另一个选项、当场报 `expected one argument`。"
                        "而真实构建命令的实参几乎全是 `--xxx` / `-jN`，故等号形式基本是常态。")
    e.add_argument("--out", required=True, help="收据落盘路径")

    args = ap.parse_args(argv)
    if args.cmd == "snapshot-digest":
        payload = take_snapshot_digest(args.source_root, args.subtree_scope)
        _write_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    digest = None
    if args.snapshot_digest:
        with open(args.snapshot_digest, "r", encoding="utf-8") as src:
            digest = json.load(src)
    if not args.build_argv:
        raise VendorBuildReceiptError(
            "emit 缺 --build-argv：本子命令**自己执行**构建命令并记实测退出码，"
            "没有「只给一个 returncode、不执行」的模式（模块 docstring 说明了为什么）")
    # ⚠ 顺序：先真跑 build，再产收据。`--returncode` 只是期望值断言，不是被记录的那个值。
    result = run_build(args.build_argv, args.build_cwd, args.library)
    if args.returncode is not None and args.returncode != result["returncode"]:
        raise VendorBuildReceiptError(
            f"--returncode 期望 {args.returncode}，实测 {result['returncode']}——"
            "以实测为准，不产收据（fail-closed）")
    receipt = produce_receipt(
        declared_source_form=args.declared_source_form,
        build_result=result,
        repo=args.repo, snapshot_digest=digest, pr_head_sha=args.pr_head_sha)
    _write_json(args.out, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except VendorBuildReceiptError as ex:
        print(f"[vendor_build_receipt] {ex}", file=sys.stderr)
        sys.exit(2)
