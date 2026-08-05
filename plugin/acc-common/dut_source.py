"""被测对象（DUT）来源判别式 —— Layer 0 受控词表 + 读侧唯一入口。

**为什么单独一个模块**：`dut_source` 判别式要被多处消费，词表和「缺省即 `pull_request`」的
兜底规则各写一份的话，只要有一处忘了 fail-closed，伪造一个未知取值就能绕过整条来源绑定链。
判别式只留一份实现。

**当前接入状态**（别把「计划接入」读成「已经接入」）：

| 消费者 | 状态 |
|---|---|
| `fetch_source`（产） | ✅ 已接 |
| `validate_preparation_state` | ✅ 已接 |
| `preflight_aclnn` / `verify_aclnn_harness` | ⬜ 待接（`aclnn_py` 侧静态对账） |
| `cpp_extension_adapter` / `cpp_extension_driver` / `validate_acceptance_state` | ⬜ 待接（vendor build receipt 绑定，**主验收链**） |
| `render_acceptance_markdown` | ⬜ 待接（provenance 强度如实标注） |
| `precision_retest_contract` / `precision_retest_runner` | ⬜ 待接（CP-F 验收后复测） |

⚠ 待接的没接完之前，本地来源**尚未**穿过完整 CP-C → 裁决链，不得对外宣称一等通路可用。

纯 stdlib、无任何 agent/CLI 依赖，可被 Layer 1 任意脚本 import。

两条来源通路是**平级**的，不是「主 + 降级」：

| `dut_source` | 被测代码怎么来 | provenance 锚 | 强度 |
|---|---|---|---|
| `pull_request`（缺省） | 在线 gitcode PR 链接 | `pr.head_sha`（40 位 hex） | 可证明「验的就是这个 PR 的这个 commit」 |
| `local_checkout` | 本地已 clone 的目录 | `local_checkout.root_digest`（64 位 sha） | 只能证明「验的就是这份字节」，**不能**证明它等于线上任何 PR |

⚠ 强度差异必须在报告里如实标注（见 `render_acceptance_markdown`），
本模块只负责判别，不负责粉饰。
"""

PULL_REQUEST = "pull_request"
LOCAL_CHECKOUT = "local_checkout"
ALL = (PULL_REQUEST, LOCAL_CHECKOUT)

# payload 里承载各自事实的键；两者**互斥出现**。
FACTS_KEY = {PULL_REQUEST: "pr", LOCAL_CHECKOUT: "local_checkout"}


class DutSourceError(ValueError):
    """来源判别式不合法（未知取值、两条通路的事实混装、锚缺失）。"""


def of(payload, *, where="payload"):
    """读出并校验 `payload.dut_source`；缺省 `pull_request`，未知取值 fail-closed。

    ⚠ **缺省不能省**：改动之前产出的收据里根本没有这个键，把「缺席」当成非法会让
    所有既有 PR 通路收据一夜之间失效。缺席 == `pull_request` 是有意的向后兼容。
    但**取值写错**（拼错、伪造）与缺席是两回事，必须当场拒。
    """
    if not isinstance(payload, dict):
        raise DutSourceError(f"{where} 须为 JSON object 才能读 dut_source")
    value = payload.get("dut_source", PULL_REQUEST)
    if value not in ALL:
        raise DutSourceError(
            f"{where}.dut_source={value!r} 不在受控词表 {list(ALL)}（未知来源一律 fail-closed，不猜）")
    return value


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


def _is_hex(value, length):
    return (isinstance(value, str) and len(value) == length
            and all(c in "0123456789abcdefABCDEF" for c in value))
