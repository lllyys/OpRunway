#!/usr/bin/env python3
"""来源对照物 `source_facts.json` 的**发现规则唯一实现**（找哪里、找到算不算数）。

## 为什么是单独一个文件，而不是塞进 `source_provenance`

规则本身属于「来源身份」这一族，直觉上该跟 :mod:`source_provenance` 住一起。**不能**：
`source_provenance.py` 在 `verify_aclnn_harness._LOGIC_FILES` 里被**逐字节哈希**，
而那道门的信任论证是一句很强的话——「该模块不依赖任何本仓模块 ⇒ 它的判定语义只由
本文件字节 + stdlib 决定 ⇒ 哈希这一个文件 ≈ 覆盖它的全部判定语义」。
`test_verify_aclnn_harness.LogicBindingCoverageTest` 用机械检查钉着这条
（本仓 import 必须为空，且整份 import 清单逐字钉住，**函数体内的惰性 import 也算**）。

而本模块的两个依赖都是本仓的、且都改不掉：

* `content_address` —— 验内容寻址信封（复算 digest），算法只此一份；
* `validate_preparation_state._validate_source_payload` —— 完整契约校验，
  而它自己 `import source_provenance`，所以放进 `source_provenance` 连惰性 import
  都还是一条回边。

把它们搬进 `source_provenance.py`，就等于把一个**没被哈希覆盖**的判定依赖
（`validate_preparation_state` 不在 `_LOGIC_FILES` 里，且它自己还拖着 `fetch_source`）
挂到那道门的绑定面上——门看着有、实际拦不住。为了让 import 通过而去改那条 pin，
正是「删门换绿」。故规则住在这里，`source_provenance.py` 保持零本仓依赖。

口径与仓内既有做法一致：被哈希的判定内核要小，URL 凭据策略 / 收据锚校验 /
`source_facts.json` 查找这三类职责都不该跟它绑在同一个文件里。凭据判别拆成
`url_credentials.py` 是同一个动作。

## 历史

规则原先是 `validate_acceptance_state._find_source_facts`，被 `render_acceptance_markdown`
跨模块引用那个**私有**名。复用方向是对的（两处各写一份查找规则的话，报告陈述的 facts
就可能不是门校过的那一份文件——报告说对上了、门校的是另一份，谁也发现不了），
但私有名跨模块用，将来改名会**静默炸 import**。所以下沉成一个公开名。

纯 stdlib + `content_address`（+ 惰性的 `validate_preparation_state`），
不依赖任何 agent/CLI 框架，可被 Layer 1 任意脚本 import。
"""

import json
import os

import content_address

#: `source_facts.json` 的内容寻址 domain（与 `fetch_source.write_source_facts` 同一个真源）。
SOURCE_FACTS_DOMAIN = "oprunway/source-facts/v1"

#: 「找到了，但这份东西不可信」的哨兵。⚠ 与 `None`（自动发现没找到）**必须分开**：
#: 前者说明有人放了一份对不上的对照物，后者只是没有对照物，两者的处置不同
#: （见 `validate_acceptance_state._gate_build_receipt_source_binding` 的按通路分表）。
SOURCE_FACTS_UNTRUSTED = "__BAD__"


def find_source_facts(report_root, source_facts_path=None):
    """定位并**验摘要**读出 `source_facts.json`：显式路径 → `<d>/` → `<d>/work/`。

    三态返回：payload dict / `None`（自动发现时没找到）/ `SOURCE_FACTS_UNTRUSTED`（找到但不可信）。

    ⚠ **这条规则只能有一份实现**。三级门用它做 build receipt ↔ source_facts 的锚对账，
    渲染器用它决定报告里「来源对照物」那一行怎么写。两处各写一份的话，报告陈述的
    facts 就可能不是门校过的那一份文件——报告说对上了、门校的是另一份，谁也发现不了。

    ⚠ 实测：真机 cpp_extension 验收的报告目录（`reports/<Op>-spec-<x>/`）里**没有**
    `source_facts.json`——取材的 `--out` 与验收产物目录不是同一个。所以这里必须能被
    显式指路，且「找不到」的处置要按通路分（见 `_gate_build_receipt_source_binding`）。

    ⚠ **显式路径不存在 ≠ 没找到**。自动发现落空是常态（上面那条实测），可以按通路分处置；
    但调用方明确把 `--source-facts` 指过来，说明它认定有这份对照物——路径打错却退成
    「没找到」，等于一个 typo 就把整条对账悄悄关掉。所以显式路径缺席一律 UNTRUSTED。

    ⚠ **必须验内容寻址 envelope**。`fetch_source.write_source_facts` 落的是
    `{schema_version, domain, digest, payload}` 信封，`digest` 由 payload 算出。
    只 `json.load` 取 `payload` 而不复算 digest，等于「随手编一份最小 JSON
    （只写一个与恶意收据同值的快照 merkle）就能当来源的信任锚」。
    没有 envelope 形态（`digest`/`payload` 缺失）同样拒：那不是 fetch_source 的产物。

    ⚠ **但 digest 自洽远远不够，还必须过完整契约**。digest 是可以自己重算的——
    用 `content_address.make_artifact` 包一个只含「与收据同值的 merkle」的最小 payload，
    照样 digest 自洽。更要命的是 `completeness.status="blocked"` 的真实取材产物：
    它是 fetch_source 亲手产的、digest 完全正确，但仓规写死了「blocked 的事实索引只供诊断」。
    拿它当来源的信任锚，正是「不完整证据被静默升级为可裁决」。
    所以这里**复用** `validate_preparation_state._validate_source_payload`——
    它已经在校 taskdoc / key_files 锚 / 两条通路各自的必填集 /
    `completeness=complete 且 reasons=[]` / `producer.tool`。另写一份判据只会分叉。

    ⚠ `source_facts_path` 判「是否显式指定」用 `is not None` 而**不是** `bool()`：
    空字符串（空环境变量展开出来的常见形态）在 `bool()` 下会被当成「没显式指定」，
    于是悄悄退回自动发现，用户明确要求的那条对账就此关掉。空串按显式处理 → UNTRUSTED。

    ⚠ 惰性 import `validate_preparation_state`：那是一个带 CLI、还会懒拉 numpy 的重模块，
    放到本文件顶层等于给纯读侧（渲染器）平白多一条重依赖。
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
