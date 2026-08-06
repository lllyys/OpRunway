"""被测对象（DUT）来源判别式 —— Layer 0 受控词表 + 读侧唯一入口。

**为什么单独一个模块**：`dut_source` 判别式要被多处消费，词表和「缺省即 `pull_request`」的
兜底规则各写一份的话，只要有一处忘了 fail-closed，伪造一个未知取值就能绕过整条来源绑定链。
判别式只留一份实现。

**当前接入状态**。状态有**三种**，别把 ⛔ 读成 ✅，也别把它读成「排期没排上」：

| 消费者 | 状态 |
|---|---|
| `fetch_source`（产） | ✅ 已接 |
| `validate_preparation_state` | ✅ 已接 |
| `preflight_aclnn` | ✅ 已接（两条通路的 signature 对账完全同形，只有锚不同；本地通路写 `local_root_digest`） |
| `verify_aclnn_harness` | ⛔ 判别式已接，但 `local_checkout` **显式 fail-closed**（理由见下） |
| `cpp_extension_adapter` / `cpp_extension_driver` / `validate_acceptance_state` | ✅ 已接（vendor build receipt 绑定，**主验收链**） |
| `render_acceptance_markdown` | ✅ 已接（按 kind 渲染，provenance 强度如实标注） |
| `precision_retest_contract` / `precision_retest_runner` | ✅ 已接（CP-F 验收后复测） |

⚠ `verify_aclnn_harness` 那个 ⛔ 是**如实挂账**，不是待办：`aclnn_adapter` 只能按 PR ref 在
容器内重新取源 build，**构建端根本不存在可与 `local_root_digest` 对账的锚**。放它过去，
收据看着齐全、绑定其实是空的。所以 `aclnn_py` 的本地通路在这道门上是**结构性**
fail-closed——只要 `aclnn_adapter` 的取源方式不变，它就一直关着，不该被后续 session
当成「下一批补上就行」。真要走本地来源的完整验收，用已接通的 `cpp_extension` 主链
（`spec.runner_form = "cpp_extension"`）。

纯 stdlib（只再依赖同为 Layer 0 的 `content_address` 与 `dut_source_kind`）、
无任何 agent/CLI 依赖，可被 Layer 1 任意脚本 import。

**判别式内核在 `dut_source_kind.py`**，本模块把它原样再导出，所以**读侧唯一入口仍是这里**，
既有消费者一个字都不用改。拆的理由与硬约束写在那边的模块文档里，一句话版本：
`verify_aclnn_harness._LOGIC_FILES` 对判定依赖逐字节哈希，而那道门只依赖「判别式」这一小块；
本文件其余三类职责（URL 凭据策略、build receipt 锚校验、`source_facts.json` 查找）
改一个字就作废真机 harness 收据，是纯粹的过度绑定。

⚠ **拆分只挪了位置，没有放松任何绑定**：harness 门若哪天开始依赖本文件里的东西
（`identity` / `validate_build_receipt_source` / `url_has_userinfo` / `find_source_facts`），
`dut_source.py` **必须**同步进 `_LOGIC_FILES`，否则就是「判定依赖脱离哈希覆盖」。
`test_verify_aclnn_harness.LogicBindingCoverageTest` 用机械检查钉着这条，不靠人记。

**本模块也是「来源对照物 `source_facts.json` 怎么找、找到算不算数」的唯一实现**
（`find_source_facts`）。它原先是 `validate_acceptance_state._find_source_facts`，被
`render_acceptance_markdown` 跨模块引用一个**私有**名——复用方向是对的（两处各写一份
查找规则的话，报告陈述的 facts 就可能不是门校过的那一份），但私有名跨模块用，将来
改名会**静默炸 import**。既然来源判别本来就归本模块，规则也下沉到这里。

两条来源通路是**平级**的，不是「主 + 降级」：

| `dut_source` | 被测代码怎么来 | provenance 锚 | 强度 |
|---|---|---|---|
| `pull_request`（缺省） | 在线 gitcode PR 链接 | `pr.head_sha`（40 位 hex） | 可证明「验的就是这个 PR 的这个 commit」 |
| `local_checkout` | 本地已 clone 的目录 | `local_checkout.root_digest`（64 位 sha） | 只能证明「验的就是这份字节」，**不能**证明它等于线上任何 PR |

⚠ 强度差异必须在报告里如实标注（见 `render_acceptance_markdown`），
本模块只负责判别，不负责粉饰。
"""

import json
import os
import re

import content_address
# 判别式内核（受控词表 + `of`）原样再导出——读侧唯一入口仍是本模块，消费者不用改 import。
# ⚠ 再导出必须是 `from … import`（同一个对象），不能在这里重新定义一遍同名的类/常量：
#   那会造出两个 `DutSourceError`，`except dut_source.DutSourceError` 从此漏捕内核抛的异常。
from dut_source_kind import (  # noqa: F401  （对外再导出，本模块内部也确实在用）
    ALL,
    DutSourceError,
    LOCAL_CHECKOUT,
    PULL_REQUEST,
    of,
)

# ---- 来源 URL 的凭据判别（唯一实现）----------------------------------------------
# 为什么放在判别式模块：`source.repo` 是**来源身份**的一部分，两条通路都必填，而它的取值
# 可能是 `git config --get remote.origin.url` 的原值。`https://<user>:<token>@host/…`
# 一旦进了收据，就会被渲染进人读的验收报告，撞仓规 §2。
# 判别规则只留一份实现：产出侧（`fetch_source` 落 source_facts 前扣留）与读侧
# （本模块校收据）各写一套的话，两边迟早对同一个 URL 给出不同答案。
_URL_AUTHORITY_END_RE = re.compile(r"[/?#]")


def _split_url_authority(url):
    """`scheme://authority<rest>` → `(scheme, authority, rest)`；不是 `://` 形态返回 None。"""
    if not isinstance(url, str) or "://" not in url:
        return None
    scheme, rest = url.split("://", 1)
    m = _URL_AUTHORITY_END_RE.search(rest)
    return (scheme, rest, "") if m is None else (scheme, rest[:m.start()], rest[m.start():])


def url_has_userinfo(url):
    """`scheme://userinfo@host/…` 形态即判「带用户凭据」。

    ⚠ 只认 `://` 形式：scp 式 `git@host:path` 的 `@` 前面是用户名、不含任何密钥，
    拦它会把合法的 SSH remote 全部误伤。而 `https://user:pw@host/…`（密码）与
    `https://<token>@host/…`（PAT，连冒号都没有）都落在 `://` 形式里，一并拦下。

    ⚠ authority 的终止符取 `/` `?` `#` 里**最先出现**的那个，不能只切 `/`。只切 `/` 会把
    query 吞进 authority：`https://host?a=b@c` 这种**根本不含凭据**的 URL 会被判成带凭据
    （query 里的 `@`），于是一个合法 remote 被白白扣留、脱敏后还被截成 `https://***@c`。
    判过头与判不到同样是坏门。
    """
    parts = _split_url_authority(url)
    return bool(parts) and "@" in parts[1]


def redact_url_userinfo(url):
    """把 `scheme://userinfo@host/…` 脱敏成 `scheme://***@host/…`；不带 userinfo 的原样返回。

    ⚠ **只用于人读文本与旁路记账字段**：脱敏会改字节，而 CP-F 对 `repo` 是逐字比对，
    把脱敏值当载重字段用等于换个方式制造 BLOCK。

    ⚠ userinfo 里若含未转义的 `@`（不合规但确实存在），按 WHATWG URL 的口径以**最后一个**
    `@` 分隔——即 `@` 之前的字节**全部丢弃**。宁可多丢，也不能漏出半截 token。
    """
    parts = _split_url_authority(url)
    if not parts or "@" not in parts[1]:
        return url
    scheme, authority, rest = parts
    return f"{scheme}://***@{authority.rsplit('@', 1)[1]}{rest}"

# payload 里承载各自事实的键；两者**互斥出现**。
FACTS_KEY = {PULL_REQUEST: "pr", LOCAL_CHECKOUT: "local_checkout"}


# `DutSourceError`、`PULL_REQUEST` / `LOCAL_CHECKOUT` / `ALL`、`of()` 的**定义**在
# `dut_source_kind.py`（见文件头 import 与那边的模块文档），此处只再导出。


def assert_facts_key_exclusive(payload, *, where="payload"):
    """两条通路的事实键必须互斥出现——同时带 `pr` 与 `local_checkout` 即拒。

    这是「本地 provenance 伪装成 PR provenance」的直接堵口：一份收据若两个键都在，
    下游按哪个分支走就成了实现细节，来源身份不再可信。
    """
    kind = of(payload, where=where)
    present = sorted(k for k in FACTS_KEY.values() if k in payload)
    if len(present) > 1:
        raise DutSourceError(
            f"{where} 同时带着 {present}（两条来源通路的事实不得混装）——来源身份不可信，拒绝")
    want = FACTS_KEY[kind]
    if present and present[0] != want:
        raise DutSourceError(
            f"{where}.dut_source={kind} 却带着 {present[0]!r} 键（应为 {want!r}）")
    return kind


def identity(payload, *, where="payload"):
    """返回 `(kind, anchor_field, anchor_value)` —— 该来源的 provenance 锚。

    · `pull_request`  → `("pull_request", "pr_head_sha", <40 位 hex>)`
    · `local_checkout`→ `("local_checkout", "local_root_digest", <64 位 sha>)`

    锚缺失/形态不对 → `DutSourceError`。**不返回 None、不给空串兜底**：
    锚是「被测字节 ↔ 构建产物」绑定的唯一依据，缺了它整条信任链就断了，
    静默放行等于让 vendor `.so` 与被测源码失去机器可核的对应关系。

    ⚠ **所有下游读锚必须走这个函数**，不许自己按字段名去 payload 里翻 `head_sha`。
    本地收据里合法地存在 `local_checkout.git.head_sha`——它是**信息字段**（这份 checkout
    当时停在哪个 commit），**不是锚**：worktree 可能 dirty，它与被测字节没有绑定关系。
    任何「递归找 head_sha」或「哪个字段有值用哪个」的兜底写法，都会把这个信息字段
    当成 PR provenance 使用。
    """
    kind = assert_facts_key_exclusive(payload, where=where)
    facts = payload.get(FACTS_KEY[kind])
    if not isinstance(facts, dict):
        raise DutSourceError(f"{where}.{FACTS_KEY[kind]} 缺失或不是 JSON object（dut_source={kind}）")
    if kind == PULL_REQUEST:
        value = facts.get("head_sha")
        if not _is_hex(value, 40):
            raise DutSourceError(f"{where}.pr.head_sha 不是 40 位 hex：{value!r}")
        return kind, "pr_head_sha", value.lower()
    value = facts.get("root_digest")
    if not _is_hex(value, 64):
        raise DutSourceError(f"{where}.local_checkout.root_digest 不是 64 位 hex：{value!r}")
    return kind, "local_root_digest", value.lower()


# `expected_kind` 的「我确实没有对照物」哨兵。**不用 `None` 当默认值**：默认值会让
# 「忘了传」与「确认过没有对照物」在调用点长得一模一样，而前者正是本模块要堵的绕过路径。
NO_EXPECTED_KIND = "__no_expected_kind__"

# 两条通路各自的**锚字段名**（build receipt `source` 里的扁平键）。
# 与 `FACTS_KEY`（payload 里的事实**块**键名）不是一回事，别混用。
ANCHOR_FIELD = {PULL_REQUEST: "pr_head_sha", LOCAL_CHECKOUT: "local_root_digest"}


def validate_build_receipt_source(source, *, expected_kind, where="build_receipt.source"):
    """校验 `vendor_build_receipt.source` 并返回 `(kind, anchor_field, anchor_value)`。

    `vendor_build_receipt` 由**外部构建驱动**产出（本仓只消费），它回答的是
    「这个 vendor `.so` 是从哪份源码构建出来的」。两条来源通路各有自己的锚：

    ```jsonc
    "source": {
      "dut_source": "local_checkout",   // 缺省 "pull_request"
      "repo": "<必填，两条通路都要>",
      "pr_head_sha": "…40 位…",         // pull_request 时必填
      "local_root_digest": "…64 位…"    // local_checkout 时必填
    }
    ```

    ⚠ **`expected_kind` 是必填关键字，没有默认值**（有默认值整套设计就被绕过）。绕过路径是：
    `source_facts` 声明 `local_checkout`，而 `vendor_build_receipt` 声明 `pull_request`
    并填一个**任意 40 位 hex** 当 `pr_head_sha` → 校验走进 PR 分支 →
    `local_root_digest` 那条等值校验**根本不会执行** → vendor `.so` 与被测源码的绑定完全失效。
    所以调用方必须把 `source_facts` 那边的 `dut_source` 传进来做一致性前置校验，
    **先确认两边说的是同一条通路，再按通路分支**。

    手上确实没有对照物时（如 adapter/driver 只拿得到收据本身，对账在三级门做），
    显式传 `expected_kind=dut_source.NO_EXPECTED_KIND`。它与「忘了传」必须在调用点长得不一样：
    前者是一句写下来的声明，后者是省略——用 `None` 当默认值会把两者抹平成同一种写法。

    ⚠ **两条通路的锚字段互斥**：声明 `pull_request` 却同时带 `local_root_digest`（反之亦然）
    一律拒。锚都齐了的话，任何一个**按字段名直取**而不用本函数返回值的下游，
    都能自选一套来源身份——那正是判别式要消灭的分叉。
    """
    if not isinstance(source, dict):
        raise DutSourceError(f"{where} 缺失或不是 JSON object")
    kind = of(source, where=where)
    if expected_kind != NO_EXPECTED_KIND:
        if expected_kind not in ALL:
            raise DutSourceError(f"expected_kind={expected_kind!r} 不在受控词表 {list(ALL)}")
        if kind != expected_kind:
            raise DutSourceError(
                f"{where}.dut_source={kind} 与 source_facts 的 {expected_kind} 不一致——"
                f"两边必须先说同一条来源通路再分支校验，否则填一个任意 40 位 hex 当 pr_head_sha "
                f"就能让本地锚的等值校验整条跳过（vendor .so 与被测源码的绑定就此失效）")
    repo = source.get("repo")
    if not isinstance(repo, str) or not repo.strip():
        raise DutSourceError(f"{where}.repo 必填（两条通路都要）")
    # ⚠ `repo` 带用户凭据一律拒，**四处消费者一起拒**（adapter / 三级门 / CP-F / 渲染器都调本函数，
    #   产出方 `make_vendor_build_receipt.self_check` 也调，且它在 `atomic_write` 之前）。
    #   只在产出方拦是不够的：老收据、外部构建驱动产的收据、手改的收据都从读侧进来，
    #   而渲染器会把 `repo` 原样印进**人读的验收报告**——那才是凭据真正泄漏出去的那一步。
    # ⚠ **刻意不回显原值**：报错会进终端与 CI 日志，回显就是再泄漏一次。
    if url_has_userinfo(repo):
        raise DutSourceError(
            f"{where}.repo 是一个**带用户凭据**的 URL（`scheme://…@host/…`），拒绝采信。\n"
            f"  它会被渲染进人读验收报告的「源码仓」一行，撞仓规 §2（token/密码/私钥不得写进任何产物）。\n"
            f"  此处刻意不回显原值——回显就是再泄漏一次。\n"
            f"  → 重产收据时用 `--repo` 显式给一个不含凭据的仓名（如 `cann/ops-nn`），"
            f"并把本机 git remote 里的凭据挪进 credential helper。")
    other = ANCHOR_FIELD[PULL_REQUEST if kind == LOCAL_CHECKOUT else LOCAL_CHECKOUT]
    if other in source:
        raise DutSourceError(
            f"{where}.dut_source={kind} 却同时带着另一条通路的锚 {other!r}——"
            f"两套锚齐备时，任何按字段名直取的下游都能自选来源身份，拒绝")
    field = ANCHOR_FIELD[kind]
    value = source.get(field)
    if not _is_hex(value, 40 if kind == PULL_REQUEST else 64):
        raise DutSourceError(
            f"{where}.{field} 不是 {40 if kind == PULL_REQUEST else 64} 位 hex：{value!r}")
    return kind, field, value.lower()


def _is_hex(value, length):
    return (isinstance(value, str) and len(value) == length
            and all(c in "0123456789abcdefABCDEF" for c in value))


# ---- 来源对照物 `source_facts.json` 的发现规则（唯一实现）--------------------------
# `source_facts.json` 的内容寻址 domain（与 `fetch_source.write_source_facts` 同一个真源）。
SOURCE_FACTS_DOMAIN = "oprunway/source-facts/v1"
# 「找到了，但这份东西不可信」的哨兵。⚠ 与 `None`（自动发现没找到）**必须分开**：
# 前者说明有人放了一份对不上的对照物，后者只是没有对照物，两者的处置不同
# （见 `validate_acceptance_state._gate_build_receipt_source_binding` 的按通路分表）。
SOURCE_FACTS_UNTRUSTED = "__BAD__"


def find_source_facts(report_root, source_facts_path=None):
    """定位并**验摘要**读出 `source_facts.json`：显式路径 → `<d>/` → `<d>/work/`。

    三态返回：payload dict / `None`（自动发现时没找到）/ `SOURCE_FACTS_UNTRUSTED`（找到但不可信）。

    ⚠ **这条规则只能有一份实现**。三级门用它做 build receipt ↔ source_facts 的锚对账，
    渲染器用它决定报告里「worktree 干净度」那一行怎么写。两处各写一份的话，报告陈述的
    facts 就可能不是门校过的那一份文件——报告说 clean、门校的是另一份，谁也发现不了。

    ⚠ 实测：真机 cpp_extension 验收的报告目录（`reports/<Op>-spec-<x>/`）里**没有**
    `source_facts.json`——取材的 `--out` 与验收产物目录不是同一个。所以这里必须能被
    显式指路，且「找不到」的处置要按通路分（见 `_gate_build_receipt_source_binding`）。

    ⚠ **显式路径不存在 ≠ 没找到**。自动发现落空是常态（上面那条实测），可以按通路分处置；
    但调用方明确把 `--source-facts` 指过来，说明它认定有这份对照物——路径打错却退成
    「没找到」，等于一个 typo 就把整条对账悄悄关掉。所以显式路径缺席一律 UNTRUSTED。

    ⚠ **必须验内容寻址 envelope**。`fetch_source.write_source_facts` 落的是
    `{schema_version, domain, digest, payload}` 信封，`digest` 由 payload 算出。
    只 `json.load` 取 `payload` 而不复算 digest，等于「随手编一份最小 JSON
    （只写一个与恶意收据同值的 `local_checkout.root_digest`）就能当本地来源的信任锚」。
    没有 envelope 形态（`digest`/`payload` 缺失）同样拒：那不是 fetch_source 的产物。

    ⚠ **但 digest 自洽远远不够，还必须过完整契约**。digest 是可以自己重算的——
    用 `content_address.make_artifact` 包一个只含「与收据同值的 root_digest」的最小 payload，
    照样 digest 自洽。更要命的是 `completeness.status="blocked"` 的真实取材产物：
    它是 fetch_source 亲手产的、digest 完全正确，但仓规写死了「blocked 的事实索引只供诊断」。
    拿它当本地来源的信任锚，正是「不完整证据被静默升级为可裁决」。
    所以这里**复用** `validate_preparation_state._validate_source_payload`——
    它已经在校 taskdoc/key_files 锚/两条通路必填集/`completeness=complete 且 reasons=[]`/
    warnings 与载重事实一致/`producer.tool`。另写一份判据只会分叉。

    ⚠ `source_facts_path` 判「是否显式指定」用 `is not None` 而**不是** `bool()`：
    空字符串（空环境变量展开出来的常见形态）在 `bool()` 下会被当成「没显式指定」，
    于是悄悄退回自动发现，用户明确要求的那条对账就此关掉。空串按显式处理 → UNTRUSTED。

    ⚠ 惰性 import `validate_preparation_state`：它顶层 import `fetch_source`，而
    `fetch_source` 顶层 import 本模块——放到模块顶层就是循环导入。
    """
    explicit = source_facts_path is not None
    for path in ([source_facts_path] if explicit else
                 [os.path.join(report_root, "source_facts.json"),
                  os.path.join(report_root, "work", "source_facts.json")]):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as src:
                doc = json.load(src)
        except (OSError, ValueError):
            return SOURCE_FACTS_UNTRUSTED
        if not isinstance(doc, dict):
            return SOURCE_FACTS_UNTRUSTED
        payload = doc.get("payload")
        if not isinstance(payload, dict) or not isinstance(doc.get("digest"), str):
            return SOURCE_FACTS_UNTRUSTED
        try:
            actual = content_address.content_digest(SOURCE_FACTS_DOMAIN, payload)
        except content_address.ContentAddressError:
            return SOURCE_FACTS_UNTRUSTED
        if (doc.get("domain") != SOURCE_FACTS_DOMAIN
                or doc.get("schema_version") != 1
                or doc["digest"] != actual):
            return SOURCE_FACTS_UNTRUSTED
        import validate_preparation_state
        try:
            validate_preparation_state._validate_source_payload(payload)
        except content_address.ContentAddressError:
            return SOURCE_FACTS_UNTRUSTED
        return payload
    return SOURCE_FACTS_UNTRUSTED if explicit else None
