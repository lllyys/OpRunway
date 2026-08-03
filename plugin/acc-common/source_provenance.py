#!/usr/bin/env python3
"""源码 provenance 档位的**唯一解释处**——intake 产的 `completeness` 与各门之间的那一层。

`fetch_source.build_source_facts` 早就把 `completeness` 分了三档（`complete` /
`snapshot_only` / `blocked`），并在 docstring 里写明第二档「既非 complete 也非 blocked：
事实是诚实可表达的，但**门没有放松**，要不要授权这条降级路由由编排层/人另行决定」。
可是 `preflight_aclnn` 一类的门只写死 `!= "complete"` 就抛，于是那句「另行决定」在工具里
**没有任何落点**：本地快照通路能取材、能 build、能跑，却在第一道静态门就恒 BLOCKED。

本模块补的就是那个落点，而且只补落点、不放松事实：

- `complete` —— 仍是唯一**无条件**放行的档，且必须两侧都有 40 位 head_sha 且逐字相等；
- `snapshot_only` —— 只有当编排层把 `OPRUNWAY_ALLOW_DEGRADED_PROVENANCE` 显式设成
  被授权的 provenance kind（当前只有 `local_snapshot`）时才放行，且仍逐条硬校：
  provenance_kind 对得上、两侧 head_sha **都必须是 null**（谁合成一个 40 位 hex 就当场
  报错，律令 5.8）、两侧 merkle 都在且逐字相等；
- 其余（含 `blocked`）—— 一律拒。

放行时返回的 `degradations` 是**机读的降级挂账**，会被门写进收据。它不是装饰：
`pr_head_unbound` 就是「本轮 DUT 没有绑定任何上游 commit」这件事的机读形式，
下游报告必须原样带着它，不得声称已绑 PR head。

不按算子身份分支：本模块只读 `source_facts` / `pr_facts` 两份中立事实包的字段。
"""

import os
import re


TIER_COMPLETE = "complete"
TIER_SNAPSHOT_ONLY = "snapshot_only"

#: 编排层授权降级路由的唯一开关。值必须**逐字等于**被授权的 provenance kind，
#: 不接受 `1` / `true` 这类真值——授权要指名道姓，避免一个泛真值把将来新增的
#: 其它降级档一起放行。
AUTHORIZE_ENV = "OPRUNWAY_ALLOW_DEGRADED_PROVENANCE"

PROVENANCE_GIT_PR = "gitcode_pr"
PROVENANCE_LOCAL_SNAPSHOT = "local_snapshot"

#: `snapshot_only` 一档必然成立的降级事实，逐条写进收据。
DEGRADATION_PR_HEAD_UNBOUND = "pr_head_unbound"
DEGRADATION_CHANGED_FILES_NOT_DIFF = "changed_files_is_subtree_not_pr_diff"

_HEX40 = re.compile(r"[0-9a-fA-F]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ProvenanceError(ValueError):
    """provenance 档位不可接受；调用方按自身约定收敛成 BLOCKED。"""


def _tier(source):
    completeness = source.get("completeness")
    if not isinstance(completeness, dict):
        raise ProvenanceError("source_facts.completeness 缺失或非 object")
    return completeness.get("status")


def bind(source, pr_facts, getenv=None):
    """校验 `source_facts` ↔ `pr_facts` 的源身份绑定，返回 `(bindings, degradations)`。

    `bindings` 恒含 `provenance_kind` / `pr_head_sha` / `snapshot_merkle_sha256`
    三个键（不适用的落 None，**绝不省略**——省略会让读产物的人分不清「没这回事」
    和「工具忘了记」）。任一条不满足即抛 `ProvenanceError`。
    """
    getenv = getenv or os.environ.get
    if not isinstance(source, dict) or not isinstance(pr_facts, dict):
        raise ProvenanceError("source_facts / pr_facts 须为 JSON object")
    source_pr = source.get("pr")
    if not isinstance(source_pr, dict):
        raise ProvenanceError("source_facts.pr 须为 JSON object")

    tier = _tier(source)
    kind = source_pr.get("provenance_kind") or PROVENANCE_GIT_PR
    source_head = source_pr.get("head_sha")
    facts_head = pr_facts.get("head_sha")

    if tier == TIER_COMPLETE:
        if not source_head or facts_head != source_head:
            raise ProvenanceError("pr_facts.head_sha 与 source_facts 绑定不一致")
        if not _HEX40.match(str(source_head)):
            raise ProvenanceError(f"source_facts.pr.head_sha 非 40 位 commit SHA: {source_head!r}")
        return ({
            "provenance_kind": kind,
            "pr_head_sha": source_head,
            "snapshot_merkle_sha256": source_pr.get("snapshot_merkle_sha256"),
        }, [])

    if tier != TIER_SNAPSHOT_ONLY:
        raise ProvenanceError(f"source_facts completeness 不是 complete（status={tier!r}）")

    # —— 以下是降级档：先要授权，再逐条硬校 ——
    authorized = (getenv(AUTHORIZE_ENV) or "").strip()
    if authorized != kind:
        raise ProvenanceError(
            f"source_facts completeness={tier!r}（provenance_kind={kind!r}）属降级取源路由，"
            f"未获授权：需显式设 {AUTHORIZE_ENV}={kind}。"
            "授权只解除『必须绑 PR head』这一条，其余事实一条不放松")
    if kind != PROVENANCE_LOCAL_SNAPSHOT:
        raise ProvenanceError(f"未知的降级 provenance_kind={kind!r}，不予放行")
    if source_head is not None or facts_head is not None:
        raise ProvenanceError(
            "local_snapshot 档下 head_sha 必须为 null（两侧皆然）；"
            f"实得 source={source_head!r} pr_facts={facts_head!r}——不接受任何合成的 commit id")
    merkle = source_pr.get("snapshot_merkle_sha256")
    if not isinstance(merkle, str) or not _HEX64.match(merkle):
        raise ProvenanceError(
            f"local_snapshot 档缺合法 snapshot_merkle_sha256（实得 {merkle!r}）")
    if pr_facts.get("snapshot_merkle_sha256") != merkle:
        raise ProvenanceError("pr_facts.snapshot_merkle_sha256 与 source_facts 不一致")
    scope = source_pr.get("snapshot_scope")
    if not isinstance(scope, str):
        raise ProvenanceError(
            "local_snapshot 档缺 snapshot_scope（merkle 没有范围就无法与真机 build 侧对账）")
    return ({
        "provenance_kind": kind,
        "pr_head_sha": None,
        "snapshot_merkle_sha256": merkle,
        "snapshot_scope": scope,
    }, [DEGRADATION_PR_HEAD_UNBOUND, DEGRADATION_CHANGED_FILES_NOT_DIFF])


#: 真机 adapter 的取源形态词表 → intake 的 provenance_kind 词表。两侧历史上就不同名
#: （`git_fetch` / `gitcode_pr`），映射写在这一处，别在门里各自拼字符串。
ADAPTER_KIND_TO_INTAKE = {
    "git_fetch": PROVENANCE_GIT_PR,
    PROVENANCE_LOCAL_SNAPSHOT: PROVENANCE_LOCAL_SNAPSHOT,
}


def check_config_against_preflight(cfg, preflight_bindings):
    """真机执行**起跑前**的源身份预核：形态必须同一；有 head 可比时逐字比。

    `local_snapshot` 起跑前拿不到任何实测字节（merkle 要 build 段现算），故这里只挡
    「通路都不是同一条」这种错配，真正的字节对账在 `check_build_identity`。
    """
    bindings = preflight_bindings if isinstance(preflight_bindings, dict) else {}
    adapter_kind = cfg.get("source_mode") or "git_fetch"
    intake_kind = ADAPTER_KIND_TO_INTAKE.get(adapter_kind)
    if intake_kind is None:
        raise ProvenanceError(f"未知取源形态 source_mode={adapter_kind!r}")
    if bindings.get("provenance_kind") != intake_kind:
        raise ProvenanceError(
            f"真机取源形态({adapter_kind}) 与 CP-C0 事实包的 provenance_kind"
            f"({bindings.get('provenance_kind')!r}) 不是同一条通路")
    if adapter_kind == PROVENANCE_LOCAL_SNAPSHOT:
        if bindings.get("pr_head_sha") is not None:
            raise ProvenanceError("local_snapshot 通路下 CP-C0 不应绑定任何 PR head")
        return
    if cfg.get("head_sha") != bindings.get("pr_head_sha"):
        raise ProvenanceError("真机配置 head_sha 与 CP-C0 已绑定的 PR head 不一致")


def check_build_identity(provenance, cfg, preflight_bindings):
    """核「真机 build 实际取到的源」↔「执行配置」↔「CP-C0 已绑定的源」三者同一，返回降级挂账。

    `git_fetch`：三处的 40 位 head SHA 必须逐字相等——与改动前逐字等价。
    `local_snapshot`：无 head 可绑，改核**同 scope 的子树 merkle**；
    两侧 scope 对不上就 fail-closed（宁可停，也不产一个「看起来绑过」的空收据）。
    """
    if not isinstance(provenance, dict):
        raise ProvenanceError("build provenance 缺失或非 object")
    bindings = preflight_bindings if isinstance(preflight_bindings, dict) else {}
    adapter_kind = cfg.get("source_mode") or "git_fetch"
    intake_kind = ADAPTER_KIND_TO_INTAKE.get(adapter_kind)
    if intake_kind is None:
        raise ProvenanceError(f"未知取源形态 source_mode={adapter_kind!r}")
    if bindings.get("provenance_kind") != intake_kind:
        raise ProvenanceError(
            f"真机取源形态({adapter_kind}) 与 CP-C0 事实包的 provenance_kind"
            f"({bindings.get('provenance_kind')!r}) 不是同一条通路")
    if provenance.get("provenance_kind") != adapter_kind:
        raise ProvenanceError("build provenance.provenance_kind 与执行配置不一致")

    if adapter_kind != PROVENANCE_LOCAL_SNAPSHOT:
        head = cfg.get("head_sha")
        if provenance.get("head_sha") != head:
            raise ProvenanceError("build provenance.head_sha 与执行配置不一致")
        if provenance.get("head_sha") != bindings.get("pr_head_sha"):
            raise ProvenanceError("build head 与 CP-C0 PR head 不一致")
        return []

    if provenance.get("head_sha") is not None or bindings.get("pr_head_sha") is not None:
        raise ProvenanceError("local_snapshot 档下 head_sha 必须两侧皆 null")
    if provenance.get("snapshot_sha256") != cfg.get("snapshot_sha256"):
        raise ProvenanceError("build provenance.snapshot_sha256 与执行配置不一致")
    intake_scope = bindings.get("snapshot_scope")
    build_scope = provenance.get("snapshot_subtree_scope")
    if not isinstance(build_scope, str):
        raise ProvenanceError("build provenance 缺 snapshot_subtree_scope")
    if intake_scope != build_scope:
        raise ProvenanceError(
            f"CP-C0 事实包的快照范围({intake_scope!r}) 与真机 build 的算子子树范围"
            f"({build_scope!r}) 不同——两个 merkle 不可比。请让 fetch_source --target-dir 与 "
            "OPRUNWAY_ACLNN_OP_SUBDIR 指向同一段子树")
    if provenance.get("snapshot_subtree_sha256") != bindings.get("snapshot_merkle_sha256"):
        raise ProvenanceError(
            "真机 build 的算子子树 merkle 与 CP-C0 事实包不一致——"
            "真机跑的不是 CP-A 读过的那份字节")
    return [DEGRADATION_PR_HEAD_UNBOUND]
