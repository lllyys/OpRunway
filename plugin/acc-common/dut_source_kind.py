"""被测来源（DUT）**判别式内核** —— 受控词表 + `of()`，Layer 0 的最小判定面。

公共入口仍然是 `dut_source`：它把本模块的名字原样再导出，既有消费者一个字都不用改
（`dut_source.of` / `dut_source.PULL_REQUEST` / `dut_source.DutSourceError` 照旧可用，
且 `DutSourceError` 是**同一个类对象**，`except dut_source.DutSourceError` 仍捕得到本模块抛的）。

## 为什么要从 `dut_source.py` 里拆出这一小块

`verify_aclnn_harness._LOGIC_FILES` 对这道门的判定依赖做**逐字节**哈希，真机收据一旦
revalidate 失败就得重跑一次昂贵的 NPU 见证。而那道门在来源这块的判定依赖**只有两个名字**：
`of()` 与 `PULL_REQUEST`（见 `verify_aclnn_harness._require_pull_request_path`）。

`dut_source.py` 同时还装着另外三类与该门判定**完全无关**的职责——URL 凭据策略
（`url_has_userinfo` / `redact_url_userinfo`）、build receipt 锚校验
（`identity` / `validate_build_receipt_source`）、`source_facts.json` 查找规则
（`find_source_facts`）。把它们和判别式塞进同一个被逐字节哈希的文件，等于让
「调一下 CP-E 的 source-facts 搜索顺序」「修一个 URL 脱敏边界」这种改动去作废真机收据。

## ⚠ 拆分不是放松绑定，方向恰恰相反

这里装的是「**判不判得出来源通路**」这一件完整的事，harness 门要的正是这件事的全部逻辑。
哪一天该门开始依赖 `dut_source` 里的别的东西（锚、URL 策略、source_facts 查找），
`dut_source.py` **必须**同步进 `_LOGIC_FILES`——否则就成了「判定依赖脱离哈希覆盖」的
fail-open。这条**不靠人记**：`test_verify_aclnn_harness.LogicBindingCoverageTest` 用机械
检查钉着（该门直接 import 的本仓模块必须全在 `_LOGIC_FILES`；该门从本模块读到的名字必须
恰好是钉住的那一小组、且唯一定义在本文件里；不许动态装载、不许给本模块起别名）。

## ⚠ 本模块必须保持零 import —— 这是整个绑定保证的来源

一个 import 都没有（stdlib、三方一律没有），所以本模块导出的任何东西都**只由本文件的字节
决定**：逐字节哈希本文件 == 覆盖它的全部判定语义。反过来，一旦引入依赖，判定逻辑就有一部分
住进了既不在 `_LOGIC_FILES`、版本也不被任何收据钉住的地方（三方包升个级、解释器换个版本，
就能悄悄改判定而旧收据照样 revalidate 通过）。所以要加依赖，就得连新依赖一起纳入
`_LOGIC_FILES` 并重算绑定面——那时拆分的收益也就没了。测试里有一条断言直接钉这个不变式。

两条来源通路是**平级**的，不是「主 + 降级」；强度差异见 `dut_source` 模块文档。
"""

PULL_REQUEST = "pull_request"
LOCAL_CHECKOUT = "local_checkout"
ALL = (PULL_REQUEST, LOCAL_CHECKOUT)


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
