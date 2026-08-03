#!/usr/bin/env python3
"""校验任务书输入是否足以同时充当开发要求与验收依据。

本脚本只复核 CP-B 第 0 步落盘的 `taskdoc_validation.json`：逐项是否覆盖契约、
判 `satisfied` 的项是否附了任务书逐字原文引用、条件项适用性是否自洽、用户决策
是否绑定当前 source facts。它按契约的 `on_unsatisfied` 机械派生阻断/待确认清单，
**不重判任务书内容本身是否正确**，也不读取 PR、caseset、evidence 或 verdict，
不产生任何验收裁决（`acceptance_verdict` 恒为 null）。
"""

import argparse
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
_WHITESPACE = re.compile(r"\s+")


class TaskdocValidationError(ValueError):
    """结构性契约错误；一律导致 BLOCKED，不降级为待用户决策。"""


def _normalize(text):
    """比对前删净空白。

    任务书正文常在中文之间换行，把空白折成单空格会凭空插入分隔符、让本来
    逐字的引用匹配不上；两侧同样删净则跨行、缩进、全角空格都不再影响判定。
    """
    return _WHITESPACE.sub("", text)


def _is_sha(value, length=64):
    return (isinstance(value, str) and len(value) == length
            and all(c in "0123456789abcdef" for c in value))


def _nonempty_str(value):
    return isinstance(value, str) and value.strip() != ""


def load_contract(path=None):
    """读取并校验校验项契约自身；契约不合法即结构性错误。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            _DEFAULT_CONTRACT)
    try:
        with open(path, "r", encoding="utf-8") as src:
            contract = json.load(src)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise TaskdocValidationError(f"无法读取校验契约 {path!r}: {ex}") from ex
    if not isinstance(contract, dict):
        raise TaskdocValidationError("校验契约须为 JSON object")
    if contract.get("schema") != _CONTRACT_SCHEMA:
        raise TaskdocValidationError(
            f"校验契约 schema 必须是 {_CONTRACT_SCHEMA}")
    if contract.get("schema_version") != 1:
        raise TaskdocValidationError("校验契约 schema_version 不受支持")
    min_quote = contract.get("min_quote_chars")
    if not isinstance(min_quote, int) or isinstance(min_quote, bool) or min_quote < 1:
        raise TaskdocValidationError("校验契约 min_quote_chars 必须是正整数")
    items = contract.get("items")
    if not isinstance(items, list) or not items:
        raise TaskdocValidationError("校验契约 items 须为非空数组")
    seen = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise TaskdocValidationError(f"校验契约 items[{index}] 须为 object")
        item_id = item.get("id")
        if not _nonempty_str(item_id):
            raise TaskdocValidationError(f"校验契约 items[{index}].id 缺失")
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
    return contract


def _load_validation(root, validation_rel):
    path = content_address.safe_path(root, validation_rel)
    try:
        with open(path, "r", encoding="utf-8") as src:
            payload = json.load(src)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise TaskdocValidationError(f"无法读取任务书校验工件 {path!r}: {ex}") from ex
    if not isinstance(payload, dict):
        raise TaskdocValidationError("taskdoc_validation 须为 JSON object")
    if payload.get("schema") != _VALIDATION_SCHEMA:
        raise TaskdocValidationError(
            f"taskdoc_validation.schema 必须是 {_VALIDATION_SCHEMA}")
    if payload.get("schema_version") != 1:
        raise TaskdocValidationError("taskdoc_validation.schema_version 不受支持")
    return payload


def _load_taskdoc(root, taskdoc_rel):
    path = content_address.safe_path(root, taskdoc_rel)
    try:
        with open(path, "r", encoding="utf-8") as src:
            return _normalize(src.read())
    except (OSError, UnicodeError) as ex:
        raise TaskdocValidationError(f"无法读取任务书原文 {path!r}: {ex}") from ex


def _bind_source_facts(root, source_rel, declared_digest):
    """任务书字节变化即让本轮校验与用户决策整体失效。"""
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
    return actual


def _check_quotes(item_id, quotes, taskdoc_norm, min_quote_chars):
    """判 satisfied 的唯一硬护栏：引用必须逐字出自任务书。"""
    if not isinstance(quotes, list) or not quotes:
        raise TaskdocValidationError(
            f"{item_id}: status=satisfied 必须给出非空 quotes（任务书原文引用）")
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


def _resolve_applicability(item_id, requirement, declared, status, rationale,
                           perf_required):
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
    if not applicable:
        if status != "not_applicable":
            raise TaskdocValidationError(
                f"{item_id}: applicable=false 时 status 必须为 not_applicable，"
                f"实际 {status!r}")
        if not _nonempty_str(rationale):
            raise TaskdocValidationError(
                f"{item_id}: 判定不适用必须给出 rationale")
    return applicable


def _resolve_perf_required(payload, taskdoc_norm, min_quote_chars):
    perf_required = payload.get("perf_required")
    if not isinstance(perf_required, bool):
        raise TaskdocValidationError(
            "taskdoc_validation.perf_required 必须显式给出布尔值")
    if perf_required:
        _check_quotes("perf_required", payload.get("perf_evidence"),
                      taskdoc_norm, min_quote_chars)
    elif not _nonempty_str(payload.get("perf_required_rationale")):
        raise TaskdocValidationError(
            "perf_required=false 时必须给出 perf_required_rationale")
    return perf_required


def _collect_decisions(payload, routed):
    """用户决策只能作用于本轮真正被派生出来的阻断项/待确认项。"""
    decisions = payload.get("decisions", [])
    if not isinstance(decisions, list):
        raise TaskdocValidationError("taskdoc_validation.decisions 须为数组")
    resolved = {}
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            raise TaskdocValidationError(f"decisions[{index}] 须为 object")
        item_id = decision.get("id")
        if item_id not in routed:
            raise TaskdocValidationError(
                f"decisions[{index}].id={item_id!r} 不在本轮阻断/待确认项内，"
                "不得对已满足或不适用的项追加决策")
        if item_id in resolved:
            raise TaskdocValidationError(f"decisions 对 {item_id} 重复决策")
        action = decision.get("action")
        if action not in _DECISION_ACTIONS:
            raise TaskdocValidationError(
                f"decisions[{index}].action 不在受控词表 {sorted(_DECISION_ACTIONS)}")
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
    """复核任务书输入校验工件，返回收据；本函数不产生验收裁决。"""
    receipt = {
        "schema": "oprunway.taskdoc_validation_receipt",
        "schema_version": 1,
        "scope": "taskdoc-input-only",
        "acceptance_verdict": None,
        "status": "BLOCKED",
        "op": None,
        "perf_required": None,
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
        by_id = {item["id"]: item for item in contract["items"]}
        payload = _load_validation(root, validation_rel)
        taskdoc_norm = _load_taskdoc(root, taskdoc_rel)
        receipt["bindings"]["source_facts_digest"] = _bind_source_facts(
            root, source_rel, payload.get("source_facts_digest"))
        receipt["bindings"]["taskdoc_normalized_chars"] = len(taskdoc_norm)
        if not _nonempty_str(payload.get("op")):
            raise TaskdocValidationError("taskdoc_validation.op 必须是非空字符串")
        receipt["op"] = payload["op"]

        perf_required = _resolve_perf_required(payload, taskdoc_norm, min_quote_chars)
        receipt["perf_required"] = perf_required

        items = payload.get("items")
        if not isinstance(items, list):
            raise TaskdocValidationError("taskdoc_validation.items 须为数组")
        seen_ids = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise TaskdocValidationError(f"items[{index}] 须为 object")
            seen_ids.append(item.get("id"))
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
                item.get("rationale"), perf_required)
            if status == "satisfied":
                _check_quotes(item_id, item.get("quotes"), taskdoc_norm,
                              min_quote_chars)
                continue
            if not applicable:
                continue
            if not _nonempty_str(item.get("rationale")):
                raise TaskdocValidationError(
                    f"{item_id}: status={status} 必须给出 rationale（缺什么、哪里模糊）")
            route = spec["on_unsatisfied"][status]
            routed[item_id] = {
                "id": item_id,
                "title": spec["title"],
                "requirement": spec["requirement"],
                "status": status,
                "route": route,
                "expects": spec.get("expects"),
                "unsatisfied_note": spec.get("unsatisfied_note"),
                "rationale": item["rationale"],
            }

        decisions = _collect_decisions(payload, routed)
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
    ap.add_argument("--contract", default=None,
                    help="校验项契约路径（默认取脚本同目录的受控契约）")
    ap.add_argument("--out", default=None, help="root 内收据相对路径（可选）")
    args = ap.parse_args(argv)
    receipt = evaluate(args.root, args.validation, taskdoc_rel=args.taskdoc,
                       source_rel=args.source, contract_path=args.contract)
    dry_run = os.environ.get(_DRY_RUN_ENV) == "1"
    if args.out and not dry_run:
        content_address.write_artifact(
            os.path.abspath(args.root), args.out, _RECEIPT_DOMAIN, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return _exit_code(receipt["status"])


if __name__ == "__main__":
    sys.exit(main())
