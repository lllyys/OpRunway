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


#: `complete` / `snapshot_only` 两档各自**唯一**允许的 provenance_kind。
#: 档位与取源形态必须成对，否则「档位说绑了 PR head、kind 说只有本地字节」这类
#: 自相矛盾的事实包会从两条分支的缝里漏过去。
_TIER_REQUIRED_KIND = {
    TIER_COMPLETE: PROVENANCE_GIT_PR,
    TIER_SNAPSHOT_ONLY: PROVENANCE_LOCAL_SNAPSHOT,
}


def _require_present(mapping, key, where):
    """取 `mapping[key]`，**键不存在即报错**。

    ⚠ 这是本模块所有硬校的地基：`.get()` 会把「字段根本没写」与「显式写了 null」
    压成同一个 `None`，于是「两侧字段都缺」经 `None == None` 就成了「校验通过」——
    审计里 C4/C5 两条 Critical 正是这么来的。要比之前，先证两边都真的说过话。
    """
    if key not in mapping:
        raise ProvenanceError(
            f"{where}.{key} 键缺失——「没写这个字段」与「显式写 null」不是一回事，不得混同")
    return mapping[key]


def _require_hex40(value, where):
    if not isinstance(value, str) or not _HEX40.fullmatch(value):
        raise ProvenanceError(f"{where} 须为 40 位 commit SHA 字符串（实得 {value!r}）")
    return value


def _require_hex64(value, where):
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ProvenanceError(f"{where} 须为 64 位小写十六进制摘要（实得 {value!r}）")
    return value


def _require_explicit_none(mapping, key, where):
    value = _require_present(mapping, key, where)
    if value is not None:
        raise ProvenanceError(f"{where}.{key} 须显式为 null（实得 {value!r}）")


def _require_kind(mapping, where):
    kind = _require_present(mapping, "provenance_kind", where)
    if not isinstance(kind, str) or not kind:
        raise ProvenanceError(f"{where}.provenance_kind 须为非空字符串（实得 {kind!r}）")
    return kind


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
    if tier not in _TIER_REQUIRED_KIND:
        raise ProvenanceError(f"source_facts completeness 不是 complete（status={tier!r}）")
    # 取源形态：两份事实包都必须**显式**声明，且逐字相等，且与档位成对。
    kind = _require_kind(source_pr, "source_facts.pr")
    facts_kind = _require_kind(pr_facts, "pr_facts")
    if facts_kind != kind:
        raise ProvenanceError(
            f"pr_facts.provenance_kind({facts_kind!r}) 与 source_facts.pr.provenance_kind"
            f"({kind!r}) 不是同一条取源通路")
    if kind != _TIER_REQUIRED_KIND[tier]:
        raise ProvenanceError(
            f"completeness={tier!r} 只允许 provenance_kind={_TIER_REQUIRED_KIND[tier]!r}，"
            f"实得 {kind!r}——档位与取源形态自相矛盾，不予放行")

    if tier == TIER_COMPLETE:
        source_head = _require_hex40(
            _require_present(source_pr, "head_sha", "source_facts.pr"),
            "source_facts.pr.head_sha")
        facts_head = _require_hex40(
            _require_present(pr_facts, "head_sha", "pr_facts"), "pr_facts.head_sha")
        if facts_head != source_head:
            raise ProvenanceError("pr_facts.head_sha 与 source_facts 绑定不一致")
        return ({
            "provenance_kind": kind,
            "pr_head_sha": source_head,
            "snapshot_merkle_sha256": source_pr.get("snapshot_merkle_sha256"),
        }, [])

    # —— 以下是降级档：先要授权，再逐条硬校 ——
    authorized = (getenv(AUTHORIZE_ENV) or "").strip()
    if authorized != kind:
        raise ProvenanceError(
            f"source_facts completeness={tier!r}（provenance_kind={kind!r}）属降级取源路由，"
            f"未获授权：需显式设 {AUTHORIZE_ENV}={kind}。"
            "授权只解除『必须绑 PR head』这一条，其余事实一条不放松")
    # head_sha：两侧都必须**显式**写着 null。缺字段不算——那是「没人说过」，不是「说了没有」。
    _require_explicit_none(source_pr, "head_sha", "source_facts.pr")
    _require_explicit_none(pr_facts, "head_sha", "pr_facts")
    merkle = _require_hex64(
        _require_present(source_pr, "snapshot_merkle_sha256", "source_facts.pr"),
        "source_facts.pr.snapshot_merkle_sha256")
    facts_merkle = _require_hex64(
        _require_present(pr_facts, "snapshot_merkle_sha256", "pr_facts"),
        "pr_facts.snapshot_merkle_sha256")
    if facts_merkle != merkle:
        raise ProvenanceError("pr_facts.snapshot_merkle_sha256 与 source_facts 不一致")
    scope = _require_present(source_pr, "snapshot_scope", "source_facts.pr")
    if not isinstance(scope, str):
        raise ProvenanceError(
            "local_snapshot 档缺 snapshot_scope（merkle 没有范围就无法与真机 build 侧对账）")
    facts_scope = _require_present(pr_facts, "snapshot_scope", "pr_facts")
    if not isinstance(facts_scope, str):
        raise ProvenanceError("pr_facts.snapshot_scope 须为字符串（空串= 仓根，属合法显式值）")
    if facts_scope != scope:
        raise ProvenanceError(
            f"pr_facts.snapshot_scope({facts_scope!r}) 与 source_facts"
            f"({scope!r}) 不一致——两个 merkle 的覆盖范围对不上就不可比")
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
    if not isinstance(cfg, dict):
        raise ProvenanceError("执行配置 cfg 须为 JSON object")
    if not isinstance(preflight_bindings, dict):
        raise ProvenanceError("CP-C0 preflight bindings 缺失或非 object")
    bindings = preflight_bindings
    adapter_kind = cfg.get("source_mode") or "git_fetch"
    intake_kind = ADAPTER_KIND_TO_INTAKE.get(adapter_kind)
    if intake_kind is None:
        raise ProvenanceError(f"未知取源形态 source_mode={adapter_kind!r}")
    if _require_kind(bindings, "preflight.bindings") != intake_kind:
        raise ProvenanceError(
            f"真机取源形态({adapter_kind}) 与 CP-C0 事实包的 provenance_kind"
            f"({bindings.get('provenance_kind')!r}) 不是同一条通路")
    if adapter_kind == PROVENANCE_LOCAL_SNAPSHOT:
        # 键必须在：畸形 bindings 少了 `pr_head_sha` 时，`.get()` 会返回 None、
        # 与「显式声明没绑 head」撞成同一件事，等于让缺字段自动过门。
        _require_explicit_none(bindings, "pr_head_sha", "preflight.bindings")
        return
    cfg_head = _require_hex40(
        _require_present(cfg, "head_sha", "执行配置 cfg"), "执行配置 cfg.head_sha")
    bound_head = _require_hex40(
        _require_present(bindings, "pr_head_sha", "preflight.bindings"),
        "preflight.bindings.pr_head_sha")
    if cfg_head != bound_head:
        raise ProvenanceError("真机配置 head_sha 与 CP-C0 已绑定的 PR head 不一致")


def check_build_identity(provenance, cfg, preflight_bindings):
    """核「真机 build 实际取到的源」↔「执行配置」↔「CP-C0 已绑定的源」三者同一，返回降级挂账。

    `git_fetch`：三处的 40 位 head SHA 必须逐字相等——与改动前逐字等价。
    `local_snapshot`：无 head 可绑，改核**同 scope 的子树 merkle**；
    两侧 scope 对不上就 fail-closed（宁可停，也不产一个「看起来绑过」的空收据）。
    """
    if not isinstance(provenance, dict):
        raise ProvenanceError("build provenance 缺失或非 object")
    if not isinstance(cfg, dict):
        raise ProvenanceError("执行配置 cfg 须为 JSON object")
    if not isinstance(preflight_bindings, dict):
        raise ProvenanceError("CP-C0 preflight bindings 缺失或非 object")
    bindings = preflight_bindings
    adapter_kind = cfg.get("source_mode") or "git_fetch"
    intake_kind = ADAPTER_KIND_TO_INTAKE.get(adapter_kind)
    if intake_kind is None:
        raise ProvenanceError(f"未知取源形态 source_mode={adapter_kind!r}")
    if _require_kind(bindings, "preflight.bindings") != intake_kind:
        raise ProvenanceError(
            f"真机取源形态({adapter_kind}) 与 CP-C0 事实包的 provenance_kind"
            f"({bindings.get('provenance_kind')!r}) 不是同一条通路")
    if _require_kind(provenance, "build provenance") != adapter_kind:
        raise ProvenanceError("build provenance.provenance_kind 与执行配置不一致")

    if adapter_kind != PROVENANCE_LOCAL_SNAPSHOT:
        # 三处 head 都必须**显式存在且是合法 40 位 hex**，再逐字比。
        # 只比相等的旧写法在「三方都没有该字段」时会靠 None == None 直接放行。
        head = _require_hex40(
            _require_present(cfg, "head_sha", "执行配置 cfg"), "执行配置 cfg.head_sha")
        build_head = _require_hex40(
            _require_present(provenance, "head_sha", "build provenance"),
            "build provenance.head_sha")
        bound_head = _require_hex40(
            _require_present(bindings, "pr_head_sha", "preflight.bindings"),
            "preflight.bindings.pr_head_sha")
        if build_head != head:
            raise ProvenanceError("build provenance.head_sha 与执行配置不一致")
        if build_head != bound_head:
            raise ProvenanceError("build head 与 CP-C0 PR head 不一致")
        return []

    # local_snapshot：两侧 head 必须**显式**为 null（缺键 ≠ 声明没有）。
    _require_explicit_none(provenance, "head_sha", "build provenance")
    _require_explicit_none(bindings, "pr_head_sha", "preflight.bindings")
    build_whole = _require_hex64(
        _require_present(provenance, "snapshot_sha256", "build provenance"),
        "build provenance.snapshot_sha256")
    cfg_whole = _require_hex64(
        _require_present(cfg, "snapshot_sha256", "执行配置 cfg"), "执行配置 cfg.snapshot_sha256")
    if build_whole != cfg_whole:
        raise ProvenanceError("build provenance.snapshot_sha256 与执行配置不一致")
    intake_scope = _require_present(bindings, "snapshot_scope", "preflight.bindings")
    build_scope = provenance.get("snapshot_subtree_scope")
    if not isinstance(build_scope, str):
        raise ProvenanceError("build provenance 缺 snapshot_subtree_scope")
    if not isinstance(intake_scope, str):
        raise ProvenanceError("preflight.bindings.snapshot_scope 须为字符串")
    if intake_scope != build_scope:
        raise ProvenanceError(
            f"CP-C0 事实包的快照范围({intake_scope!r}) 与真机 build 的算子子树范围"
            f"({build_scope!r}) 不同——两个 merkle 不可比。请让 fetch_source --target-dir 与 "
            "OPRUNWAY_ACLNN_OP_SUBDIR 指向同一段子树")
    build_subtree = _require_hex64(
        _require_present(provenance, "snapshot_subtree_sha256", "build provenance"),
        "build provenance.snapshot_subtree_sha256")
    intake_merkle = _require_hex64(
        _require_present(bindings, "snapshot_merkle_sha256", "preflight.bindings"),
        "preflight.bindings.snapshot_merkle_sha256")
    if build_subtree != intake_merkle:
        raise ProvenanceError(
            "真机 build 的算子子树 merkle 与 CP-C0 事实包不一致——"
            "真机跑的不是 CP-A 读过的那份字节")
    return [DEGRADATION_PR_HEAD_UNBOUND]
