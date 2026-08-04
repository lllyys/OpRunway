#!/usr/bin/env python3
"""校验任务书输入是否足以同时充当开发要求与验收依据。

本脚本只复核 CP-B0 落盘的 `taskdoc_validation.json`：逐项是否覆盖契约、判 `satisfied`
的项是否附了任务书逐字原文、条件项适用性是否自洽、用户决策是否绑定当前 source facts
与当轮 item 状态。它按契约的 `on_unsatisfied` 机械派生阻断/待确认清单，
**不重判任务书内容本身是否正确**，也不读取 PR、caseset、evidence 或 verdict，
不产生任何验收裁决（`acceptance_verdict` 恒为 null）。

fail-closed 边界（每条都对应一种曾被审出来的绕过路径）：
- 任务书原始字节必须与 `source_facts.taskdoc.bytes_sha256` 相符——否则换掉任务书、
  留着旧事实包，就能让上一轮的引用与用户决策继续生效；
- 同一条引用不得同时支撑两个校验项——否则任取一句真原文即可把多项标成 satisfied；
- `stop` 路由的项只能由 `supplied`（补齐事实）解除，不能 `waived`——豁免掉 Golden、
  目标硬件或验收完成条件会让下游拿不到必需事实却继续往前跑；
- 决策必须自报它针对的 item 状态并与当轮实际状态相符——否则旧决策可被搬到新一轮；
- 契约路径不是运行时开关，只有进程内调用（测试）能替换。

交付件清单（`deliverables`）是 `delivery_scope` 的机器可读半边：引文回答「任务书怎么说」，
清单回答「于是本次必须交付哪几件、哪几件可选」。契约声明的受控标记词表（必选/可选一类）
在任务书里出现几处，就必须被清单条目或显式豁免覆盖几处；`delivery_scope` 判 `satisfied`
却仍有未覆盖标记，即结构性错误。它挡的是实测过的那条路：摘一句「aclnn 为必选交付项」
就把交付范围判过，而任务书另外三处「必选」（适配层、kernel、接口分层）一句没抽，
下游于是拿不到任何可对账的必选交付件清单，「PR 少交付一层」全流程无人发现。

已知未封的口子（有意留待后续，别误以为已经堵上）：
- 引用去重只按归一后全文相等，同一句话取不同重叠子串仍可分撑多项——要真堵住得给每项拆
  `required_facets`、逐 facet 绑定引用；
- `deliverable_scan_exemptions` 只要一段自由文本 rationale，把每一处标记都豁免掉挡不住；
  收据会把豁免逐条摆出来供编排层与人复核，但脚本本身不判「这处标记该不该算交付件」；
- 标记词表按「交付件定性」收窄（不收 `必须`/`须` 这类泛义务词），任务书若用词表外的写法
  写「必须交付 X」，本门扫不到——那不是判它已覆盖，而是这道门对该写法根本没生效，
  须扩词表（改契约、不改脚本）；
- `load_contract` 只锁 `resolution_actions_by_route.stop` 这一条不变量，**不锁 18 项各自的路由**，
  逐项路由由 `test_validate_taskdoc_input.py` 的 `_EXPECTED` 表锁定；
- `decisions` 只绑到 item 的当轮 status，没有 round id / nonce，同状态的旧决策仍可重放；
- `perf_required=false` 只要一段自由文本 rationale，谎报「任务书没有性能要求」挡不住；
- `source_facts.json` 由 `content_address` 的普通 loader 读入，其内部重复键不受本脚本的严格
  loader 保护。
"""

import argparse
import hashlib
import json
import os
import re
import sys

import content_address


_SOURCE_DOMAIN = "oprunway/source-facts/v1"
_RECEIPT_DOMAIN = "oprunway/taskdoc-validation-receipt/v1"
_CONTRACT_SCHEMA = "oprunway.taskdoc_validation_contract"
_VALIDATION_SCHEMA = "oprunway.taskdoc_validation"
_DEFAULT_CONTRACT = "taskdoc_validation_contract.json"
_DRY_RUN_ENV = "OPRUNWAY_TASKDOC_VALIDATION_DRY_RUN"

_REQUIREMENTS = frozenset({"must", "conditional", "conditional_perf", "optional"})
_ROUTES = frozenset({"stop", "list_pending", "use_workflow_default"})
_UNSATISFIED_KEYS = frozenset({"missing", "ambiguous"})
_ITEM_STATUSES = frozenset({"satisfied", "ambiguous", "missing", "not_applicable"})
_DECISION_ACTIONS = frozenset({"supplied", "waived"})
_DELIVERABLE_REQUIREMENTS = frozenset({"required", "optional"})
# 交付件 id 会被下游对账工件按字符串引用，也会进报告；限成短标识，避免路径/换行混进来。
_DELIVERABLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

_WHITESPACE = re.compile(r"\s+")
# 中日韩表意文字与全角标点。任务书正文常在这些字符之间换行，那里的空白是排版
# 产物、不是词边界；ASCII 之间的空白则是真词边界，删掉会让 "1 23" 与 "12 3" 等价。
_CJK = "⺀-〿㐀-䶿一-鿿豈-﫿︰-﹏＀-￯"
_CJK_GAP = re.compile(f"(?<=[{_CJK}])\\s+(?=[{_CJK}])")


class TaskdocValidationError(ValueError):
    """结构性契约错误；一律导致 BLOCKED，不降级为待用户决策。"""


def _normalize(text):
    """比对前归一空白：删 CJK 之间的排版空白，其余空白折成单空格。

    只删 CJK 之间的空白，跨行中文引用才能逐字匹配；ASCII 之间保留一个空格，
    数字与标识符的词边界就不会被抹掉。
    """
    return _WHITESPACE.sub(" ", _CJK_GAP.sub("", text)).strip()


def _is_sha(value, length=64):
    return (isinstance(value, str) and len(value) == length
            and all(c in "0123456789abcdef" for c in value))


def _nonempty_str(value):
    return isinstance(value, str) and value.strip() != ""


def _reject_duplicate_keys(pairs):
    """重复键会被默认 loader 静默取最后一个——人看前一个、机器用后一个。"""
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise TaskdocValidationError(f"JSON 存在重复键: {key!r}")
        seen[key] = value
    return seen


def _load_json(path, what):
    try:
        with open(path, "r", encoding="utf-8") as src:
            return json.load(src, object_pairs_hook=_reject_duplicate_keys)
    except TaskdocValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise TaskdocValidationError(f"无法读取{what} {path!r}: {ex}") from ex


def _digest(value):
    return hashlib.sha256(content_address.canonical_json_bytes(value)).hexdigest()


def load_contract(path=None):
    """读取并校验校验项契约自身；契约不合法即结构性错误。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            _DEFAULT_CONTRACT)
    contract = _load_json(path, "校验契约")
    if not isinstance(contract, dict):
        raise TaskdocValidationError("校验契约须为 JSON object")
    if contract.get("schema") != _CONTRACT_SCHEMA:
        raise TaskdocValidationError(f"校验契约 schema 必须是 {_CONTRACT_SCHEMA}")
    if contract.get("schema_version") != 1:
        raise TaskdocValidationError("校验契约 schema_version 不受支持")
    min_quote = contract.get("min_quote_chars")
    if not isinstance(min_quote, int) or isinstance(min_quote, bool) or min_quote < 1:
        raise TaskdocValidationError("校验契约 min_quote_chars 必须是正整数")
    by_route = contract.get("resolution_actions_by_route")
    if not isinstance(by_route, dict) or frozenset(by_route) != _ROUTES:
        raise TaskdocValidationError(
            f"校验契约 resolution_actions_by_route 的键必须严格等于 {sorted(_ROUTES)}")
    for route, actions in by_route.items():
        if (not isinstance(actions, list) or not actions
                or any(action not in _DECISION_ACTIONS for action in actions)
                or len(set(actions)) != len(actions)):
            raise TaskdocValidationError(
                f"校验契约 resolution_actions_by_route.{route} 必须是 "
                f"{sorted(_DECISION_ACTIONS)} 的非空无重复子集")
    if by_route["stop"] != ["supplied"]:
        raise TaskdocValidationError(
            "校验契约 resolution_actions_by_route.stop 必须恰为 [\"supplied\"]："
            "阻断项只能靠补齐事实解除，豁免会让下游拿不到必需事实却继续往前跑")
    items = contract.get("items")
    if not isinstance(items, list) or not items:
        raise TaskdocValidationError("校验契约 items 须为非空数组")
    seen = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise TaskdocValidationError(f"校验契约 items[{index}] 须为 object")
        item_id = item.get("id")
        if not _nonempty_str(item_id):
            raise TaskdocValidationError(f"校验契约 items[{index}].id 缺失或非字符串")
        if item_id in seen:
            raise TaskdocValidationError(f"校验契约 id 重复: {item_id}")
        seen.add(item_id)
        if not _nonempty_str(item.get("title")):
            raise TaskdocValidationError(f"校验契约 {item_id}.title 缺失")
        if item.get("requirement") not in _REQUIREMENTS:
            raise TaskdocValidationError(
                f"校验契约 {item_id}.requirement 不在受控词表 {sorted(_REQUIREMENTS)}")
        routes = item.get("on_unsatisfied")
        if not isinstance(routes, dict) or frozenset(routes) != _UNSATISFIED_KEYS:
            raise TaskdocValidationError(
                f"校验契约 {item_id}.on_unsatisfied 键必须严格等于 "
                f"{sorted(_UNSATISFIED_KEYS)}")
        for key, route in routes.items():
            if route not in _ROUTES:
                raise TaskdocValidationError(
                    f"校验契约 {item_id}.on_unsatisfied.{key} 不在受控词表 "
                    f"{sorted(_ROUTES)}")
    _check_inventory_spec(contract.get("deliverable_inventory"), seen)
    return contract


def _check_inventory_spec(inventory, item_ids):
    """契约必须自带交付件清单规格；缺了这块，交付件清单这道门整体失效。"""
    if not isinstance(inventory, dict):
        raise TaskdocValidationError(
            "校验契约必须声明 deliverable_inventory（owner_item + markers）："
            "缺这一块等于交付件清单这道门被整体关掉")
    owner = inventory.get("owner_item")
    if owner not in item_ids:
        raise TaskdocValidationError(
            f"校验契约 deliverable_inventory.owner_item 必须是受控项之一，得 {owner!r}")
    markers = inventory.get("markers")
    if (not isinstance(markers, dict)
            or frozenset(markers) != _DELIVERABLE_REQUIREMENTS):
        raise TaskdocValidationError(
            "校验契约 deliverable_inventory.markers 的键必须严格等于 "
            f"{sorted(_DELIVERABLE_REQUIREMENTS)}")
    flat = []
    for modality in sorted(markers):
        words = markers[modality]
        if not isinstance(words, list) or not words:
            raise TaskdocValidationError(
                f"校验契约 deliverable_inventory.markers.{modality} 必须是非空数组")
        for word in words:
            if not _nonempty_str(word) or _normalize(word) != word:
                raise TaskdocValidationError(
                    f"校验契约 deliverable_inventory.markers.{modality} 的标记 "
                    f"{word!r} 必须是已归一（无多余空白）的非空字符串——"
                    "扫描在归一后的任务书上做，未归一的标记永远扫不中")
            flat.append(word)
    if len(set(flat)) != len(flat):
        raise TaskdocValidationError(
            "校验契约 deliverable_inventory.markers 存在重复标记："
            "同一个词同时算必选与可选会让 requirement 一致性检查自相矛盾")


def _load_validation(root, validation_rel):
    payload = _load_json(content_address.safe_path(root, validation_rel),
                         "任务书校验工件")
    if not isinstance(payload, dict):
        raise TaskdocValidationError("taskdoc_validation 须为 JSON object")
    if payload.get("schema") != _VALIDATION_SCHEMA:
        raise TaskdocValidationError(
            f"taskdoc_validation.schema 必须是 {_VALIDATION_SCHEMA}")
    if payload.get("schema_version") != 1:
        raise TaskdocValidationError("taskdoc_validation.schema_version 不受支持")
    return payload


def _load_taskdoc(root, taskdoc_rel):
    """返回 (归一正文, 原始字节 sha256)——摘要用于与事实包对账。"""
    path = content_address.safe_path(root, taskdoc_rel)
    try:
        with open(path, "rb") as src:
            raw = src.read()
    except OSError as ex:
        raise TaskdocValidationError(f"无法读取任务书原文 {path!r}: {ex}") from ex
    try:
        text = raw.decode("utf-8")
    except UnicodeError as ex:
        raise TaskdocValidationError(f"任务书原文不是合法 UTF-8: {ex}") from ex
    return _normalize(text), hashlib.sha256(raw).hexdigest()


def _bind_source_facts(root, source_rel, declared_digest, taskdoc_sha256):
    """双向绑定：事实包摘要要对得上，任务书原始字节也要对得上事实包声明的 SHA。

    只核前者时，把 task_doc.md 换掉、事实包原样留着，旧引用与旧决策就会继续生效。
    """
    if not _is_sha(declared_digest):
        raise TaskdocValidationError(
            "taskdoc_validation.source_facts_digest 必须是 64 位小写 sha256")
    try:
        payload = content_address.read_artifact(root, source_rel, _SOURCE_DOMAIN)
    except content_address.ContentAddressError as ex:
        raise TaskdocValidationError(f"source_facts 不可信: {ex}") from ex
    actual = content_address.content_digest(_SOURCE_DOMAIN, payload)
    if actual != declared_digest:
        raise TaskdocValidationError(
            f"source_facts 摘要漂移: declared={declared_digest}, actual={actual}；"
            "任务书或 PR 事实已变，须重做本轮任务书校验与用户决策")
    taskdoc_facts = payload.get("taskdoc") if isinstance(payload, dict) else None
    if not isinstance(taskdoc_facts, dict):
        raise TaskdocValidationError(
            "source_facts.taskdoc 必须是 JSON object，无法取出任务书字节摘要")
    declared_bytes = taskdoc_facts.get("bytes_sha256")
    if not _is_sha(declared_bytes):
        raise TaskdocValidationError(
            "source_facts.taskdoc.bytes_sha256 缺失或不是 64 位小写 sha256，"
            "无法证明本轮校验读的就是事实包锚定的任务书")
    if declared_bytes != taskdoc_sha256:
        raise TaskdocValidationError(
            f"任务书字节与事实包不符: source_facts={declared_bytes}, "
            f"实际读到={taskdoc_sha256}；任务书已被替换或事实包未刷新，"
            "本轮校验与用户决策整体失效")
    return actual


def _check_quotes(item_id, quotes, taskdoc_norm, min_quote_chars, owner=None,
                  owner_id=None):
    """引用必须逐字出自任务书，且不得跨项复用同一条。

    `owner_id` 让一组引用记在别的项名下：交付件清单的引文属于 `owner_item` 这一项，
    条目之间可以共用同一句原文（同一项内不算复用），但仍不得与另外 17 项抢同一句。
    """
    holder = item_id if owner_id is None else owner_id
    if not isinstance(quotes, list) or not quotes:
        raise TaskdocValidationError(
            f"{item_id}: 必须给出非空 quotes（任务书原文引用）")
    for index, quote in enumerate(quotes):
        if not isinstance(quote, dict) or not _nonempty_str(quote.get("text")):
            raise TaskdocValidationError(
                f"{item_id}.quotes[{index}] 必须是含非空 text 的 object")
        text = _normalize(quote["text"])
        if len(text) < min_quote_chars:
            raise TaskdocValidationError(
                f"{item_id}.quotes[{index}] 引用过短（归一后 {len(text)} 字符 < "
                f"{min_quote_chars}），不足以证明任务书已明确该项")
        if text not in taskdoc_norm:
            raise TaskdocValidationError(
                f"{item_id}.quotes[{index}] 未逐字出现在任务书原文中: {text[:60]!r}")
        if owner is not None:
            previous = owner.get(text)
            if previous is not None and previous != holder:
                raise TaskdocValidationError(
                    f"{item_id}.quotes[{index}] 与 {previous} 复用同一条引用: "
                    f"{text[:60]!r}；一条原文不能同时充当两项的证据")
            owner[text] = holder


def _occurrences(needle, haystack):
    """`needle` 在 `haystack` 里的全部出现区间 [起, 止)；重叠出现也逐个给出。"""
    spans = []
    start = haystack.find(needle)
    while start != -1:
        spans.append((start, start + len(needle)))
        start = haystack.find(needle, start + 1)
    return spans


def _scan_modality_sites(taskdoc_norm, markers):
    """扫出任务书里全部「交付定性标记」出现位置。

    只按契约声明的受控词表按结构扫，与算子、领域、仓形态无关：任务书写了几处
    「必选/可选」，就有几处必须在清单里有归宿。

    同类标记互相包含时只留最长的那条（「必须交付」里的「须交付」不再单算一处），
    否则一处写法会被记成两处、清单永远对不平。**跨类包含一律都留**：短的那个若是
    required、长的那个是 optional，丢掉短的就等于把一处必选悄悄抹掉。
    """
    found = []
    for modality in sorted(markers):
        for word in markers[modality]:
            for start, end in _occurrences(word, taskdoc_norm):
                found.append({
                    "offset": start,
                    "end": end,
                    "marker": word,
                    "modality": modality,
                    "context": taskdoc_norm[max(0, start - 40):end + 40],
                })
    sites = []
    for site in found:
        if any(other is not site
               and other["modality"] == site["modality"]
               and other["offset"] <= site["offset"]
               and site["end"] <= other["end"]
               and (other["end"] - other["offset"]) > (site["end"] - site["offset"])
               for other in found):
            continue
        sites.append(site)
    sites.sort(key=lambda site: (site["offset"], site["marker"]))
    return sites


def _marker_modalities(text, markers):
    """`text` 里出现了哪几类定性标记（用于「声明 ↔ 原文」一致性检查）。"""
    return {modality for modality in markers
            if any(word in text for word in markers[modality])}


def _check_deliverable_entries(entries, taskdoc_norm, min_quote_chars,
                               quote_owner, owner_item, markers):
    """逐条校验交付件清单；返回 (归一后的清单, 引文覆盖区间)。"""
    if not isinstance(entries, list):
        raise TaskdocValidationError(
            "taskdoc_validation.deliverables 必须显式给出数组："
            "任务书完全没有交付定性标记时才允许是空数组，缺这个键不等于「没有交付件」")
    listed, spans, seen = [], [], set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise TaskdocValidationError(f"deliverables[{index}] 须为 object")
        entry_id = entry.get("id")
        if not _nonempty_str(entry_id) or not _DELIVERABLE_ID_RE.match(entry_id):
            raise TaskdocValidationError(
                f"deliverables[{index}].id 必须是 [A-Za-z0-9_.-] 组成的短标识"
                "（下游对账工件按它逐条指认归宿）")
        if entry_id in seen:
            raise TaskdocValidationError(f"deliverables 存在重复 id: {entry_id}")
        seen.add(entry_id)
        if not _nonempty_str(entry.get("name")):
            raise TaskdocValidationError(
                f"deliverables[{entry_id}].name 缺失：须写清交付件名称或层级")
        requirement = entry.get("requirement")
        if requirement not in _DELIVERABLE_REQUIREMENTS:
            raise TaskdocValidationError(
                f"deliverables[{entry_id}].requirement 不在受控词表 "
                f"{sorted(_DELIVERABLE_REQUIREMENTS)}")
        _check_quotes(f"deliverables[{entry_id}]", entry.get("quotes"),
                      taskdoc_norm, min_quote_chars, owner=quote_owner,
                      owner_id=owner_item)
        modalities = set()
        for quote in entry["quotes"]:
            text = _normalize(quote["text"])
            spans.extend(_occurrences(text, taskdoc_norm))
            modalities |= _marker_modalities(text, markers)
        # 只在「引文里只出现一类标记」时判冲突：一句话同时写了必选与可选
        # （常见于「A 为必选、B 为可选」）本就无法机械归类，交给判断。
        if len(modalities) == 1 and requirement not in modalities:
            only = sorted(modalities)[0]
            raise TaskdocValidationError(
                f"deliverables[{entry_id}] 声明 requirement={requirement}，"
                f"但其引文里只出现 {only} 类定性标记；"
                "把必选写成可选就是让下游漏掉一件必交付物")
        listed.append({
            "id": entry_id,
            "name": entry["name"],
            "requirement": requirement,
            "quotes": [{"text": quote["text"]} for quote in entry["quotes"]],
        })
    return listed, spans


def _check_deliverable_exemptions(exemptions, taskdoc_norm, min_quote_chars):
    """校验「这处标记不是交付件」的显式豁免；返回 (记录, 引文覆盖区间)。

    豁免引文**不进跨项引用去重表**：它不是任何项的证据，只是一句「这处标记与交付件无关」
    的指认；把它记进去只会平白挡掉别的项引用同一句原文。
    """
    if not isinstance(exemptions, list):
        raise TaskdocValidationError(
            "taskdoc_validation.deliverable_scan_exemptions 须为数组")
    recorded, spans = [], []
    for index, exemption in enumerate(exemptions):
        if not isinstance(exemption, dict):
            raise TaskdocValidationError(
                f"deliverable_scan_exemptions[{index}] 须为 object")
        quote = exemption.get("quote")
        if not isinstance(quote, dict):
            raise TaskdocValidationError(
                f"deliverable_scan_exemptions[{index}].quote 须为含 text 的 object")
        if not _nonempty_str(exemption.get("rationale")):
            raise TaskdocValidationError(
                f"deliverable_scan_exemptions[{index}] 必须给出 rationale："
                "说清这处定性标记为什么不是交付件，否则等于无声跳过一处必选")
        _check_quotes(f"deliverable_scan_exemptions[{index}]", [quote],
                      taskdoc_norm, min_quote_chars)
        text = _normalize(quote["text"])
        spans.extend(_occurrences(text, taskdoc_norm))
        recorded.append({"quote": {"text": quote["text"]},
                         "rationale": exemption["rationale"]})
    return recorded, spans


def _check_deliverables(payload, taskdoc_norm, min_quote_chars, quote_owner,
                        inventory_spec, owner_status):
    """交付件清单门：任务书里每一处交付定性标记都必须有归宿。

    `owner_item` 判 `satisfied` 却仍有未覆盖标记 → 结构性错误（BLOCKED）：
    「摘到一句相关原文」不等于「交付范围已抽全」。`owner_item` 本就没判 satisfied 时
    不额外阻断（该项已按契约路由停在用户那儿），但收据仍如实记 `complete=false`
    与未覆盖清单，绝不把「没查清」记成「已覆盖」。
    """
    owner_item = inventory_spec["owner_item"]
    markers = inventory_spec["markers"]
    listed, spans = _check_deliverable_entries(
        payload.get("deliverables"), taskdoc_norm, min_quote_chars,
        quote_owner, owner_item, markers)
    recorded, exempt_spans = _check_deliverable_exemptions(
        payload.get("deliverable_scan_exemptions", []), taskdoc_norm,
        min_quote_chars)
    spans = spans + exempt_spans
    sites = _scan_modality_sites(taskdoc_norm, markers)
    uncovered = [site for site in sites
                 if not any(lo <= site["offset"] and site["end"] <= hi
                            for lo, hi in spans)]
    if uncovered and owner_status == "satisfied":
        preview = " ｜ ".join(site["context"] for site in uncovered[:3])
        raise TaskdocValidationError(
            f"{owner_item} 判 satisfied，但任务书里还有 {len(uncovered)} 处交付定性标记"
            f"（{sorted({site['marker'] for site in uncovered})}）"
            "既未进 deliverables 清单、也未列入 deliverable_scan_exemptions："
            f"{preview}；漏抽的必选交付件下游无从对账")
    summary = {
        "owner_item": owner_item,
        "owner_status": owner_status,
        "complete": not uncovered,
        "modality_sites": len(sites),
        "uncovered_sites": [
            {"marker": site["marker"], "modality": site["modality"],
             "context": site["context"]}
            for site in uncovered],
        "exemptions": recorded,
        "required_ids": sorted(entry["id"] for entry in listed
                               if entry["requirement"] == "required"),
        "optional_ids": sorted(entry["id"] for entry in listed
                               if entry["requirement"] == "optional"),
    }
    return listed, summary


def _resolve_applicability(item_id, requirement, declared, status, perf_required):
    """条件项的适用性必须显式且与 status 自洽，不靠脚本猜。"""
    if requirement in ("must", "optional"):
        if declared is not None and declared is not True:
            raise TaskdocValidationError(
                f"{item_id}: requirement={requirement} 恒适用，不得声明 applicable=false")
        if status == "not_applicable":
            raise TaskdocValidationError(
                f"{item_id}: requirement={requirement} 不允许 status=not_applicable")
        return True
    if requirement == "conditional_perf":
        if declared is not None and declared is not perf_required:
            raise TaskdocValidationError(
                f"{item_id}.applicable={declared} 与顶层 perf_required="
                f"{perf_required} 不一致")
        applicable = perf_required
    else:
        if not isinstance(declared, bool):
            raise TaskdocValidationError(
                f"{item_id}: requirement=conditional 必须显式声明 applicable 布尔值")
        applicable = declared
    if applicable and status == "not_applicable":
        raise TaskdocValidationError(
            f"{item_id}: applicable=true 与 status=not_applicable 冲突")
    if not applicable and status != "not_applicable":
        raise TaskdocValidationError(
            f"{item_id}: applicable=false 时 status 必须为 not_applicable，"
            f"实际 {status!r}")
    return applicable


def _resolve_perf_required(payload, taskdoc_norm, min_quote_chars):
    perf_required = payload.get("perf_required")
    if not isinstance(perf_required, bool):
        raise TaskdocValidationError(
            "taskdoc_validation.perf_required 必须显式给出布尔值")
    evidence = payload.get("perf_evidence")
    if perf_required:
        _check_quotes("perf_required", evidence, taskdoc_norm, min_quote_chars)
    else:
        if evidence:
            raise TaskdocValidationError(
                "perf_required=false 却带着非空 perf_evidence：任务书要么有性能要求、"
                "要么没有，留着旧证据会让两个性能项被静默判为不适用")
        if not _nonempty_str(payload.get("perf_required_rationale")):
            raise TaskdocValidationError(
                "perf_required=false 时必须给出 perf_required_rationale")
    return perf_required


def _route_items(items, by_id, taskdoc_norm, min_quote_chars, perf_required,
                 quote_owner):
    """逐项校验并按契约派生路由；返回 {item_id: 路由条目}。"""
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise TaskdocValidationError(f"items[{index}] 须为 object")
        if not _nonempty_str(item.get("id")):
            raise TaskdocValidationError(f"items[{index}].id 缺失或非字符串")
    seen_ids = [item["id"] for item in items]
    if len(seen_ids) != len(set(seen_ids)):
        raise TaskdocValidationError("taskdoc_validation.items 存在重复 id")
    missing_ids = sorted(set(by_id) - set(seen_ids))
    extra_ids = sorted(set(seen_ids) - set(by_id))
    if missing_ids or extra_ids:
        raise TaskdocValidationError(
            f"校验项未与契约逐项对齐: 缺失={missing_ids}, 多余={extra_ids}")

    routed = {}
    for item in items:
        item_id = item["id"]
        spec = by_id[item_id]
        status = item.get("status")
        if status not in _ITEM_STATUSES:
            raise TaskdocValidationError(
                f"{item_id}.status 不在受控词表 {sorted(_ITEM_STATUSES)}")
        applicable = _resolve_applicability(
            item_id, spec["requirement"], item.get("applicable"), status,
            perf_required)
        if status == "satisfied":
            _check_quotes(item_id, item.get("quotes"), taskdoc_norm,
                          min_quote_chars, owner=quote_owner)
            continue
        if not applicable:
            if not _nonempty_str(item.get("rationale")):
                raise TaskdocValidationError(
                    f"{item_id}: 判定不适用必须给出 rationale")
            # 条件项的「该场景不存在」是从任务书语义推出来的，必须锚在原文上；
            # 性能两项的不适用已由 perf_required 的证据统一背书，不再重复要求。
            if spec["requirement"] == "conditional":
                _check_quotes(item_id, item.get("quotes"), taskdoc_norm,
                              min_quote_chars, owner=quote_owner)
            continue
        if not _nonempty_str(item.get("rationale")):
            raise TaskdocValidationError(
                f"{item_id}: status={status} 必须给出 rationale（缺什么、哪里模糊）")
        routed[item_id] = {
            "id": item_id,
            "title": spec["title"],
            "requirement": spec["requirement"],
            "status": status,
            "route": spec["on_unsatisfied"][status],
            "expects": spec.get("expects"),
            "unsatisfied_note": spec.get("unsatisfied_note"),
            "rationale": item["rationale"],
        }
    return routed


def _collect_decisions(payload, routed, actions_by_route):
    """用户决策只能作用于本轮真正派生出来的项，且动作受该项路由约束。"""
    decisions = payload.get("decisions", [])
    if not isinstance(decisions, list):
        raise TaskdocValidationError("taskdoc_validation.decisions 须为数组")
    resolved = {}
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            raise TaskdocValidationError(f"decisions[{index}] 须为 object")
        item_id = decision.get("id")
        if not _nonempty_str(item_id):
            raise TaskdocValidationError(f"decisions[{index}].id 缺失或非字符串")
        entry = routed.get(item_id)
        if entry is None:
            raise TaskdocValidationError(
                f"decisions[{index}].id={item_id!r} 不在本轮阻断/待确认项内，"
                "不得对已满足或不适用的项追加决策")
        if item_id in resolved:
            raise TaskdocValidationError(f"decisions 对 {item_id} 重复决策")
        action = decision.get("action")
        if action not in _DECISION_ACTIONS:
            raise TaskdocValidationError(
                f"decisions[{index}].action 不在受控词表 {sorted(_DECISION_ACTIONS)}")
        allowed = actions_by_route[entry["route"]]
        if action not in allowed:
            raise TaskdocValidationError(
                f"decisions[{index}]: {item_id} 走 {entry['route']} 路由，"
                f"只接受 {allowed}；{action!r} 会让下游拿不到必需事实却继续往前跑")
        if decision.get("resolved_status") != entry["status"]:
            raise TaskdocValidationError(
                f"decisions[{index}].resolved_status="
                f"{decision.get('resolved_status')!r} 与 {item_id} 当轮实际状态 "
                f"{entry['status']!r} 不符：决策必须针对本轮的判定，不能沿用旧轮结论")
        if decision.get("source") != "user":
            raise TaskdocValidationError(
                f"decisions[{index}].source 必须为 \"user\"（决策只能来自用户）")
        if action == "supplied" and not _nonempty_str(decision.get("value")):
            raise TaskdocValidationError(
                f"decisions[{index}]: action=supplied 必须给出非空 value（补充的事实）")
        if action == "waived" and not _nonempty_str(decision.get("rationale")):
            raise TaskdocValidationError(
                f"decisions[{index}]: action=waived 必须给出非空 rationale")
        resolved[item_id] = decision
    return resolved


def evaluate(root, validation_rel, taskdoc_rel="task_doc.md",
             source_rel="source_facts.json", contract_path=None):
    """复核任务书输入校验工件，返回收据；本函数不产生验收裁决。

    `contract_path` 只供进程内调用（测试）替换契约，CLI 不暴露——否则一份放宽的
    契约就能把整道门降级成 PASSED。
    """
    receipt = {
        "schema": "oprunway.taskdoc_validation_receipt",
        "schema_version": 1,
        "scope": "taskdoc-input-only",
        "acceptance_verdict": None,
        "status": "BLOCKED",
        "op": None,
        "perf_required": None,
        "deliverables": [],
        "deliverable_inventory": None,
        "blocking_items": [],
        "pending_items": [],
        "workflow_default_items": [],
        "decided_items": [],
        "confirmed_constraints_candidates": [],
        "errors": [],
        "bindings": {},
    }
    try:
        contract = load_contract(contract_path)
        min_quote_chars = contract["min_quote_chars"]
        actions_by_route = contract["resolution_actions_by_route"]
        by_id = {item["id"]: item for item in contract["items"]}
        payload = _load_validation(root, validation_rel)
        taskdoc_norm, taskdoc_sha256 = _load_taskdoc(root, taskdoc_rel)
        receipt["bindings"] = {
            "source_facts_digest": _bind_source_facts(
                root, source_rel, payload.get("source_facts_digest"),
                taskdoc_sha256),
            "taskdoc_bytes_sha256": taskdoc_sha256,
            "validation_digest": _digest(payload),
            "contract_digest": _digest(contract),
        }
        if not _nonempty_str(payload.get("op")):
            raise TaskdocValidationError("taskdoc_validation.op 必须是非空字符串")
        receipt["op"] = payload["op"]

        perf_required = _resolve_perf_required(payload, taskdoc_norm, min_quote_chars)
        receipt["perf_required"] = perf_required

        items = payload.get("items")
        if not isinstance(items, list):
            raise TaskdocValidationError("taskdoc_validation.items 须为数组")
        quote_owner = {}
        routed = _route_items(items, by_id, taskdoc_norm, min_quote_chars,
                              perf_required, quote_owner)
        inventory_spec = contract["deliverable_inventory"]
        owner_status = next(item["status"] for item in items
                            if item["id"] == inventory_spec["owner_item"])
        receipt["deliverables"], receipt["deliverable_inventory"] = (
            _check_deliverables(payload, taskdoc_norm, min_quote_chars,
                                quote_owner, inventory_spec, owner_status))
        decisions = _collect_decisions(payload, routed, actions_by_route)

        for item_id, entry in sorted(routed.items()):
            decision = decisions.get(item_id)
            if decision is not None:
                resolved = dict(entry)
                resolved["decision"] = {
                    "action": decision["action"],
                    "value": decision.get("value"),
                    "rationale": decision.get("rationale"),
                }
                receipt["decided_items"].append(resolved)
                if decision["action"] == "supplied":
                    receipt["confirmed_constraints_candidates"].append({
                        "key": f"taskdoc:{item_id}",
                        "value": decision["value"],
                        "source": "user",
                    })
                continue
            if entry["route"] == "stop":
                receipt["blocking_items"].append(entry)
            elif entry["route"] == "list_pending":
                receipt["pending_items"].append(entry)
            else:
                receipt["workflow_default_items"].append(entry)

        if receipt["blocking_items"]:
            receipt["status"] = "NEEDS_USER"
        elif receipt["pending_items"]:
            receipt["status"] = "PASSED_WITH_PENDING"
        else:
            receipt["status"] = "PASSED"
    except (TaskdocValidationError, content_address.ContentAddressError) as ex:
        receipt["status"] = "BLOCKED"
        receipt["errors"] = [str(ex)]
    return receipt


def _exit_code(status):
    if status in ("PASSED", "PASSED_WITH_PENDING"):
        return 0
    if status == "NEEDS_USER":
        return 2
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="复核任务书输入校验工件；不产生验收裁决")
    ap.add_argument("--root", required=True, help="CP-A 取材工作区根目录")
    ap.add_argument("--validation", default="taskdoc_validation.json",
                    help="root 内任务书校验工件相对路径")
    ap.add_argument("--taskdoc", default="task_doc.md",
                    help="root 内任务书原文相对路径")
    ap.add_argument("--source", default="source_facts.json",
                    help="root 内 source facts 相对路径")
    ap.add_argument("--out", default=None, help="root 内收据相对路径（可选）")
    args = ap.parse_args(argv)
    receipt = evaluate(args.root, args.validation, taskdoc_rel=args.taskdoc,
                       source_rel=args.source)
    dry_run = os.environ.get(_DRY_RUN_ENV) == "1"
    if args.out and not dry_run:
        content_address.write_artifact(
            os.path.abspath(args.root), args.out, _RECEIPT_DOMAIN, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return _exit_code(receipt["status"])


if __name__ == "__main__":
    sys.exit(main())
