#!/usr/bin/env python3
"""源码 provenance 的**唯一解释处**——intake 产的事实包与各道门之间的那一层。

## 判据：不是「有没有拿到 PR head」，而是「实得形态是否与声明的输入形态一致」

改动前，档位只按「绑没绑上 PR head」判：拿到 head 就 `complete`，只有本地字节就
`snapshot_only`，后者必须由编排层显式设 `OPRUNWAY_ALLOW_DEGRADED_PROVENANCE` 才放行、
且强制挂 `pr_head_unbound` 降级账。可是**很多轮验收的输入本来就是一份本地代码**，不是 PR。
于是「正常的本地代码验收」每次都要人为解一道锁——既不合实际工作方式，也让「降级」这个信号
失去意义：真正该报警的情形被日常噪音淹没。

现在判据换成一句话：**入口声明要测什么形态，就必须实得什么形态**。

| 声明形态 `declared_source_form` | 实得 `provenance_kind` | 档位 | 授权 | 降级挂账 |
|---|---|---|---|---|
| `git_pr`（`fetch_source --pr`） | `gitcode_pr` | `complete` | 不需要 | 无 |
| `local_source`（`fetch_source --pr-snapshot`） | `local_snapshot` | `complete` | **不需要** | **无** |
| `git_pr` | `local_snapshot` | `snapshot_only` | **仍需** `OPRUNWAY_ALLOW_DEGRADED_PROVENANCE` | `pr_head_unbound` 等 |
| `local_source` | `gitcode_pr` | 任意 | —— | **一律拒**（声明本地却带着上游 commit） |

第二行是本次重构的全部意义：`head_sha=null` 是 `local_source` 形态的**正确值**，不是缺陷。
第三行一个字都没放松：本来要测 PR、结果只拿到快照，那才是「本该绑却没绑」= 降级。

## `pr_head_unbound` 的语义分家

它以前同时表示两件事，现在拆开、且在产物里机读可分：

- **中性事实**（`local_source` 形态必然如此）→ :data:`LOCAL_SOURCE_FORM_FACTS`，
  经 `bindings["source_form_facts"]` 往下传。报告必须原样带着（不得声称已绑 PR head），
  但它**不是**降级、不进降级台账；
- **降级**（本该绑却没绑）→ :data:`DEGRADATION_PR_HEAD_UNBOUND`，仍走 `degradations` 返回值。

## 未声明形态 = 按最严的一档对待

老事实包（本次重构之前产的）没有 `declared_source_form`。两侧都没有时按 `git_pr` 声明处理——
于是老的 local_snapshot 事实包仍落降级档、仍要授权，**与改动前逐字同规矩**，不因升级被静默放松。
一侧有一侧没有则 fail-closed：那两份事实包不是同一次取材产的。

不按算子身份分支：本模块只读 `source_facts` / `pr_facts` 两份中立事实包的字段。
"""

import os
import re


TIER_COMPLETE = "complete"
TIER_SNAPSHOT_ONLY = "snapshot_only"

#: 编排层授权**降级**路由的唯一开关。值必须**逐字等于**被授权的 provenance kind，
#: 不接受 `1` / `true` 这类真值——授权要指名道姓，避免一个泛真值把将来新增的
#: 其它降级档一起放行。
#: ⚠ 只有「声明 git_pr 却只实得 local_snapshot」这一条路由要它；正常的本地源码验收
#: （声明 local_source）**不需要任何环境变量**。
AUTHORIZE_ENV = "OPRUNWAY_ALLOW_DEGRADED_PROVENANCE"

#: **实得**取源形态（intake 词表）。
PROVENANCE_GIT_PR = "gitcode_pr"
PROVENANCE_LOCAL_SNAPSHOT = "local_snapshot"

#: **声明**输入形态（入口就定的受控词表）。刻意与实得词表不同名：一个说「你要测什么」，
#: 一个说「工具真拿到了什么」，两者比对才是档位判据。
DECLARED_FORM_KEY = "declared_source_form"
FORM_GIT_PR = "git_pr"
FORM_LOCAL_SOURCE = "local_source"
DECLARED_SOURCE_FORMS = (FORM_GIT_PR, FORM_LOCAL_SOURCE)

#: 各声明形态**如愿实得**时应有的 provenance_kind。
FORM_EXPECTED_KIND = {
    FORM_GIT_PR: PROVENANCE_GIT_PR,
    FORM_LOCAL_SOURCE: PROVENANCE_LOCAL_SNAPSHOT,
}

#: 「本该绑上游 commit 却没绑」的机读降级挂账 —— 只在**降级**路由上产生。
DEGRADATION_PR_HEAD_UNBOUND = "pr_head_unbound"
DEGRADATION_CHANGED_FILES_NOT_DIFF = "changed_files_is_subtree_not_pr_diff"

#: `local_source` 形态的**中性事实描述** —— 不是降级，是这条形态本来的样子。
#: 报告须原样带着（据此不得声称「已绑定 PR head」「changed_files 是 PR diff」），
#: 但不得把它们呈现成异常。名字与降级项刻意不同字面，展平到同一张表里也分得清。
FORM_FACT_NO_UPSTREAM_COMMIT = "local_source_has_no_upstream_commit"
FORM_FACT_FILE_SET_IS_SUBTREE = "local_source_file_set_is_subtree_not_pr_diff"
LOCAL_SOURCE_FORM_FACTS = (FORM_FACT_NO_UPSTREAM_COMMIT, FORM_FACT_FILE_SET_IS_SUBTREE)

#: 三条**被允许**的 (档位, 声明形态, 实得形态) 组合 —— allowlist，表外一律拒。
ROUTE_GIT_PR = "git_pr_bound"
ROUTE_LOCAL_SOURCE = "local_source_as_declared"
ROUTE_DEGRADED_SNAPSHOT = "declared_git_pr_got_local_snapshot"

_ROUTES = {
    (TIER_COMPLETE, FORM_GIT_PR, PROVENANCE_GIT_PR): ROUTE_GIT_PR,
    (TIER_COMPLETE, FORM_LOCAL_SOURCE, PROVENANCE_LOCAL_SNAPSHOT): ROUTE_LOCAL_SOURCE,
    (TIER_SNAPSHOT_ONLY, FORM_GIT_PR, PROVENANCE_LOCAL_SNAPSHOT): ROUTE_DEGRADED_SNAPSHOT,
}

#: 只有这一条路由要授权。
_ROUTES_NEEDING_AUTHORIZATION = frozenset({ROUTE_DEGRADED_SNAPSHOT})

_HEX40 = re.compile(r"[0-9a-fA-F]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ProvenanceError(ValueError):
    """provenance 不可接受；调用方按自身约定收敛成 BLOCKED。"""


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


def _read_declared(mapping, where):
    """读一份事实包自报的声明形态；未声明（键缺席或显式 null）→ None，词表外 → 报错。"""
    value = mapping.get(DECLARED_FORM_KEY)
    if value is None:
        return None
    if value not in DECLARED_SOURCE_FORMS:
        raise ProvenanceError(
            f"{where}.{DECLARED_FORM_KEY}={value!r} 非受控值，须属 "
            f"{list(DECLARED_SOURCE_FORMS)}（fail-closed，不猜、不归类）")
    return value


def declared_form(source, pr_facts):
    """定出本轮**声明**的输入形态 → `(form, explicitly_declared)`。

    两侧都未声明 → 按 `git_pr` 处理（老事实包的兼容路径：规矩与改动前逐字相同，
    不因本次重构被静默放松）；一侧声明一侧没有 → fail-closed。
    """
    from_source = _read_declared(source, "source_facts")
    from_facts = _read_declared(pr_facts, "pr_facts")
    if from_source is None and from_facts is None:
        return FORM_GIT_PR, False
    if from_source is None or from_facts is None:
        raise ProvenanceError(
            f"只有一侧事实包声明了 {DECLARED_FORM_KEY}"
            f"（source_facts={from_source!r} / pr_facts={from_facts!r}）——"
            "两份事实包不是同一次取材产的，不予放行")
    if from_source != from_facts:
        raise ProvenanceError(
            f"两份事实包声明的输入形态不一致：source_facts={from_source!r}、"
            f"pr_facts={from_facts!r}")
    return from_source, True


def _resolve_route(tier, form, kind):
    """(档位, 声明形态, 实得形态) → 路由名；allowlist 之外一律 fail-closed。"""
    route = _ROUTES.get((tier, form, kind))
    if route is not None:
        return route
    if form == FORM_LOCAL_SOURCE and kind == PROVENANCE_GIT_PR:
        raise ProvenanceError(
            f"声明输入形态为 {FORM_LOCAL_SOURCE}（本地源码），实得却是绑定上游 commit 的 "
            f"{PROVENANCE_GIT_PR}——声明与实得不是同一件事，fail-closed")
    raise ProvenanceError(
        f"不被接受的源身份组合：completeness={tier!r} × {DECLARED_FORM_KEY}={form!r} × "
        f"provenance_kind={kind!r}。可接受的只有 "
        f"{sorted(f'{t}/{f}/{k}' for t, f, k in _ROUTES)}")


def _bind_git_pr(source_pr, pr_facts, kind, form):
    head = _require_hex40(
        _require_present(source_pr, "head_sha", "source_facts.pr"),
        "source_facts.pr.head_sha")
    facts_head = _require_hex40(
        _require_present(pr_facts, "head_sha", "pr_facts"), "pr_facts.head_sha")
    if facts_head != head:
        raise ProvenanceError("pr_facts.head_sha 与 source_facts 绑定不一致")
    return {
        "provenance_kind": kind,
        DECLARED_FORM_KEY: form,
        "pr_head_sha": head,
        "snapshot_merkle_sha256": source_pr.get("snapshot_merkle_sha256"),
        "source_form_facts": [],
    }


def _bind_local_snapshot(source_pr, pr_facts, kind, form):
    """两侧字节身份的硬校 —— 正常 `local_source` 与降级路由**共用同一套**，一条不放松。

    head_sha 两侧都必须**显式**写着 null。缺字段不算——那是「没人说过」，不是「说了没有」。
    谁在这里合成一个 40 位 hex，就是拿 merkle 冒充 commit id（AGENTS.md 5.8），当场报错。
    """
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
            "本地源码档缺 snapshot_scope（merkle 没有范围就无法与真机 build 侧对账）")
    facts_scope = _require_present(pr_facts, "snapshot_scope", "pr_facts")
    if not isinstance(facts_scope, str):
        raise ProvenanceError("pr_facts.snapshot_scope 须为字符串（空串= 仓根，属合法显式值）")
    if facts_scope != scope:
        raise ProvenanceError(
            f"pr_facts.snapshot_scope({facts_scope!r}) 与 source_facts"
            f"({scope!r}) 不一致——两个 merkle 的覆盖范围对不上就不可比")
    return {
        "provenance_kind": kind,
        DECLARED_FORM_KEY: form,
        # null 在这一档是**正确值**，不是缺陷：本地源码本来就没有上游 commit。
        "pr_head_sha": None,
        "snapshot_merkle_sha256": merkle,
        "snapshot_scope": scope,
        "source_form_facts": (list(LOCAL_SOURCE_FORM_FACTS)
                              if form == FORM_LOCAL_SOURCE else []),
    }


def bind(source, pr_facts, getenv=None):
    """校验 `source_facts` ↔ `pr_facts` 的源身份绑定，返回 `(bindings, degradations)`。

    `bindings` 恒含 `provenance_kind` / `declared_source_form` / `pr_head_sha` /
    `snapshot_merkle_sha256` / `source_form_facts` 五个键（不适用的落 None 或空表，
    **绝不省略**——省略会让读产物的人分不清「没这回事」和「工具忘了记」）。

    `degradations` 只在**降级**路由上非空；`local_source` 如愿实得时它是空表，
    形态本身的中性事实在 `bindings["source_form_facts"]` 里。任一条不满足即抛
    `ProvenanceError`。
    """
    getenv = getenv or os.environ.get
    if not isinstance(source, dict) or not isinstance(pr_facts, dict):
        raise ProvenanceError("source_facts / pr_facts 须为 JSON object")
    source_pr = source.get("pr")
    if not isinstance(source_pr, dict):
        raise ProvenanceError("source_facts.pr 须为 JSON object")

    completeness = source.get("completeness")
    if not isinstance(completeness, dict):
        raise ProvenanceError("source_facts.completeness 缺失或非 object")
    tier = completeness.get("status")
    # 取源形态：两份事实包都必须**显式**声明实得 kind，且逐字相等。
    kind = _require_kind(source_pr, "source_facts.pr")
    facts_kind = _require_kind(pr_facts, "pr_facts")
    if facts_kind != kind:
        raise ProvenanceError(
            f"pr_facts.provenance_kind({facts_kind!r}) 与 source_facts.pr.provenance_kind"
            f"({kind!r}) 不是同一条取源通路")
    form, _explicit = declared_form(source, pr_facts)
    route = _resolve_route(tier, form, kind)

    if route in _ROUTES_NEEDING_AUTHORIZATION:
        # 「本来要测 PR、结果只拿到一份快照」—— 这一条一个字都没放松。
        authorized = (getenv(AUTHORIZE_ENV) or "").strip()
        if authorized != kind:
            raise ProvenanceError(
                f"本轮声明要测 {FORM_GIT_PR}（PR），实得却只有 {kind!r} 这份本地字节，"
                f"属降级取源路由，未获授权：需显式设 {AUTHORIZE_ENV}={kind}。"
                f"授权只解除『必须绑 PR head』这一条，其余事实一条不放松。"
                f"（若本轮**本来就是**以本地代码为输入，请用 fetch_source --pr-snapshot 取材，"
                f"那条路声明 {DECLARED_FORM_KEY}={FORM_LOCAL_SOURCE}，无需任何授权。）")

    if route == ROUTE_GIT_PR:
        return _bind_git_pr(source_pr, pr_facts, kind, form), []
    bindings = _bind_local_snapshot(source_pr, pr_facts, kind, form)
    if route == ROUTE_LOCAL_SOURCE:
        return bindings, []
    return bindings, [DEGRADATION_PR_HEAD_UNBOUND, DEGRADATION_CHANGED_FILES_NOT_DIFF]


#: 真机 adapter 的取源形态词表 → intake 的 provenance_kind 词表。两侧历史上就不同名
#: （`git_fetch` / `gitcode_pr`），映射写在这一处，别在门里各自拼字符串。
ADAPTER_KIND_TO_INTAKE = {
    "git_fetch": PROVENANCE_GIT_PR,
    PROVENANCE_LOCAL_SNAPSHOT: PROVENANCE_LOCAL_SNAPSHOT,
}


def _bindings_form(bindings, kind):
    """从 CP-C0 bindings 读回声明形态；未声明按 `git_pr`（老收据兼容，规矩不放松）。

    同时复核「声明 × 实得」这一对仍在 allowlist 内——CP-C0 之后的每一道门都重判一次，
    免得中途有人把 bindings 里的某一半换掉。
    """
    form = _read_declared(bindings, "preflight.bindings")
    if form is None:
        form = FORM_GIT_PR
    if FORM_EXPECTED_KIND[form] != kind and not (
            form == FORM_GIT_PR and kind == PROVENANCE_LOCAL_SNAPSHOT):
        raise ProvenanceError(
            f"CP-C0 事实包的 {DECLARED_FORM_KEY}={form!r} 与 provenance_kind={kind!r} "
            "不是被接受的组合（fail-closed）")
    return form


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
    bound_kind = _require_kind(bindings, "preflight.bindings")
    if bound_kind != intake_kind:
        raise ProvenanceError(
            f"真机取源形态({adapter_kind}) 与 CP-C0 事实包的 provenance_kind"
            f"({bindings.get('provenance_kind')!r}) 不是同一条通路")
    _bindings_form(bindings, bound_kind)
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

    `git_fetch`：三处的 40 位 head SHA 必须逐字相等。
    `local_snapshot`：无 head 可绑，改核**同 scope 的子树 merkle**；
    两侧 scope 对不上就 fail-closed（宁可停，也不产一个「看起来绑过」的空收据）。

    返回值只装**降级**：声明 `local_source` 时返回空表（那不是降级，是形态本身），
    声明 `git_pr` 却只实得快照时仍返回 `["pr_head_unbound"]`。
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
    bound_kind = _require_kind(bindings, "preflight.bindings")
    if bound_kind != intake_kind:
        raise ProvenanceError(
            f"真机取源形态({adapter_kind}) 与 CP-C0 事实包的 provenance_kind"
            f"({bindings.get('provenance_kind')!r}) 不是同一条通路")
    form = _bindings_form(bindings, bound_kind)
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
    if form == FORM_LOCAL_SOURCE:
        return []                       # 声明即所得，没有任何降级
    return [DEGRADATION_PR_HEAD_UNBOUND]


def form_facts(bindings):
    """从 bindings 取回中性形态事实（老 bindings 没有这个键时给空表）。"""
    if not isinstance(bindings, dict):
        raise ProvenanceError("bindings 须为 JSON object")
    facts = bindings.get("source_form_facts") or []
    if not isinstance(facts, list) or any(not isinstance(x, str) for x in facts):
        raise ProvenanceError("bindings.source_form_facts 须为字符串数组")
    return list(facts)
