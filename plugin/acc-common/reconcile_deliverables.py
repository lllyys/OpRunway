#!/usr/bin/env python3
"""把「任务书要求的必选交付件」逐条对到「PR 实际交付了什么」，缺口落成结构化条目。

这是 CP-B0（只读任务书）与 `fetch_source`（只读 PR）之间一直缺的那次碰面：
前者产出必选/可选交付件清单，后者产出 `changed_files` / `key_files` / `target_dir`，
在此之前**没有任何一处**把两边对起来，于是「PR 少交付了一个必选层」全流程无人发现，
只能靠 agent 手写进 `spec.task_pr_gaps` 兜底——手写就会写错（把「验收基准」当「交付件」）。

**不做模糊名字匹配。** 「OpenCV C++ 适配层」这类描述没法可靠地自动映射到文件，猜错的
代价是「静默判成已覆盖」，那比不判更坏。本脚本只做两件确定性的事：

1. **核对已指认的归宿**：编排层/人在 `deliverable_mapping.json` 里逐条写明某必选件落在
   PR 的哪些路径或哪些符号上，本脚本按 `pr_facts` 逐条**验证**——路径必须逐字命中
   `changed_files`（或作为目录前缀命中其下某个改动文件），符号必须逐字出现在某个
   `key_files` 正文里。验不上就是缺口，不是通过。
2. **把没有归宿的必选件落成缺口**：没写映射、写了 `absent`、写了 `uncertain`、
   或写了 `present` 却验不上——四种都进 `gaps`，各自带机读 reason。

fail-closed 边界：
- 清单本身没过 CP-B0 的完整性门（`deliverable_inventory.complete=false`）→ 绝不 RECONCILED，
  因为「清单可能还漏着必选件」时说「必选件全部有归宿」是假话；
- `pr_facts.changed_files` 为空或 PR 侧自报 `blocked` → 无从验证，全部必选件按
  `pr_evidence_unavailable` 落缺口，不按「查不到 = 没问题」放行；
- `key_files` 只是 PR 的**部分**文件，符号验不上只证明「本脚本没看到」，故落缺口交人复核，
  不反过来断言「PR 里没有这个符号」；
- 映射工件必须绑定当轮任务书字节、校验工件摘要与 `pr_facts` 字节，任一漂移即 BLOCKED，
  否则上一轮的「已指认」能被原样搬到换了 PR 的这一轮。

本脚本**不产验收裁决**（`acceptance_verdict` 恒为 null），也不改写 spec；
它的产物是给编排层与报告消费的缺口清单。

用法:
  python3 reconcile_deliverables.py --root <workdir> [--pr-facts pr_facts.json]
      [--mapping deliverable_mapping.json] [--out deliverable_reconciliation.json]
"""

import argparse
import hashlib
import json
import os
import re
import sys

import content_address
import source_provenance
import validate_taskdoc_input as taskdoc


_RECONCILIATION_DOMAIN = "oprunway/deliverable-reconciliation/v1"
_RECONCILIATION_SCHEMA = "oprunway.deliverable_reconciliation"
_MAPPING_SCHEMA = "oprunway.deliverable_mapping"
_DRY_RUN_ENV = "OPRUNWAY_DELIVERABLE_RECONCILE_DRY_RUN"

_DISPOSITIONS = frozenset({"present", "absent", "uncertain"})
# 太短的「符号」在几十 KB 的关键文件里必然命中，等于自动放行；逐字符号至少要有辨识度。
_MIN_SYMBOL_CHARS = 3

#: 允许进入交付件对账的任务书校验状态**白名单**（audit#31）。
#: `NEEDS_USER` 表示清单还等着人补事实——此时对账出来的「必选件全有归宿」没有意义；
#: 未知/新增状态同样拒，绝不「只拒已知的坏值」。
_ACCEPTED_TASKDOC_STATUSES = frozenset({"PASSED", "PASSED_WITH_PENDING"})

#: 合法 C/C++ 标识符——symbol 归宿必须是**完整标识符**，不是任意子串（audit#34）。
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class ReconcileError(ValueError):
    """结构性契约错误；一律 BLOCKED，不降级为缺口条目。"""


def _load_json_strict(path, what):
    try:
        with open(path, "rb") as src:
            raw = src.read()
    except OSError as ex:
        raise ReconcileError(f"无法读取{what} {path!r}: {ex}") from ex
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=taskdoc._reject_duplicate_keys)
    except taskdoc.TaskdocValidationError as ex:
        raise ReconcileError(f"{what} {path!r}: {ex}") from ex
    except (UnicodeError, json.JSONDecodeError) as ex:
        raise ReconcileError(f"无法解析{what} {path!r}: {ex}") from ex
    return payload, hashlib.sha256(raw).hexdigest()


def _str_list(value, where):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReconcileError(f"{where} 须为字符串数组")
    out = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ReconcileError(f"{where}[{index}] 须为非空字符串")
        out.append(item.strip())
    return out


def _load_pr_facts(root, rel):
    """读 `fetch_source` 产的 pr_facts；只取对账要用的三类事实。"""
    payload, digest = _load_json_strict(
        content_address.safe_path(root, rel), "pr_facts")
    if not isinstance(payload, dict):
        raise ReconcileError("pr_facts 须为 JSON object")
    changed = payload.get("changed_files")
    if changed is None:
        changed = []
    if not isinstance(changed, list) or any(not isinstance(p, str)
                                            for p in changed):
        raise ReconcileError("pr_facts.changed_files 须为字符串数组")
    key_files = payload.get("key_files")
    if key_files is None:
        key_files = {}
    if not isinstance(key_files, dict):
        raise ReconcileError("pr_facts.key_files 须为 object")
    texts = {path: body for path, body in key_files.items()
             if isinstance(body, str)}
    blocked = payload.get("blocked")
    if blocked is not None and not isinstance(blocked, str):
        raise ReconcileError("pr_facts.blocked 须为字符串或缺省")
    return {
        "sha256": digest,
        "payload": payload,
        "pr_url": payload.get("pr_url"),
        "head_sha": payload.get("head_sha"),
        "changed_files": sorted(changed),
        "key_files": texts,
        "blocked": blocked,
    }


def _bind_pr_facts_to_source(root, source_rel, pr_facts):
    """把裸 `pr_facts.json` 钉到**内容寻址**的 `source_facts` 上（audit#32）。

    对账的全部证据（`changed_files` / `key_files`）都来自 `pr_facts.json`——而它是一份
    普通 JSON，不是内容寻址工件。以前这里**一条都不核**：谁往 `changed_files` 里多写一行、
    往 `key_files` 里塞一段文本，就能给任意交付件造出「归宿」。

    这里复用 `source_provenance.bind()`（provenance 的唯一解释处：判据是「实得形态是否与
    声明的输入形态一致」——`git_pr`/`local_source` 如愿实得都无条件放行，只有「声明要测 PR
    却只拿到本地快照」才要显式授权，其余全拒），再逐项核对改动集与关键文件字节摘要/ref。
    返回 `(bindings, degradations)` 供收据留痕。
    """
    try:
        source = content_address.read_artifact(root, source_rel, taskdoc._SOURCE_DOMAIN)
    except content_address.ContentAddressError as ex:
        raise ReconcileError(f"读不到内容寻址的 source_facts：{ex}") from ex
    if not isinstance(source, dict):
        raise ReconcileError("source_facts payload 须为 JSON object")
    try:
        bindings, degradations = source_provenance.bind(source, pr_facts["payload"])
    except source_provenance.ProvenanceError as ex:
        raise ReconcileError(f"pr_facts 与 source_facts 的源身份绑定不成立：{ex}") from ex

    source_changed = source.get("changed_files")
    if not isinstance(source_changed, list) or any(
            not isinstance(p, str) for p in source_changed):
        raise ReconcileError("source_facts.changed_files 缺失或非字符串数组")
    if sorted(source_changed) != pr_facts["changed_files"]:
        raise ReconcileError(
            "pr_facts.changed_files 与内容寻址的 source_facts 不一致——"
            "对账证据只认 CP-A 记过的那份改动集，裸 pr_facts 不得单方面增删")

    index = source.get("key_files")
    if not isinstance(index, list):
        raise ReconcileError("source_facts.key_files 缺失或非数组")
    recorded = {}
    for item in index:
        if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                or not isinstance(item.get("bytes_sha256"), str)):
            raise ReconcileError("source_facts.key_files 条目缺 path / bytes_sha256")
        recorded[item["path"]] = item
    extra = sorted(set(pr_facts["key_files"]) - set(recorded))
    if extra:
        raise ReconcileError(
            f"pr_facts.key_files 多出 source_facts 未记录的文件 {extra}——"
            "凭空多出的正文可以给任意符号造出「归宿」")
    for path, text in pr_facts["key_files"].items():
        want = recorded[path]
        got = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if got != want["bytes_sha256"]:
            raise ReconcileError(
                f"pr_facts.key_files[{path!r}] 正文摘要与 source_facts 不符（正文被改过）")
    return bindings, degradations


def _load_mapping(root, rel, bindings, pr_sha256):
    """读交付件归宿映射；缺文件是合法状态（等于一条都没指认）。"""
    if rel is None:
        return {}
    path = content_address.safe_path(root, rel)
    if not os.path.exists(path):
        return {}
    payload, _ = _load_json_strict(path, "deliverable_mapping")
    if not isinstance(payload, dict):
        raise ReconcileError("deliverable_mapping 须为 JSON object")
    if payload.get("schema") != _MAPPING_SCHEMA:
        raise ReconcileError(
            f"deliverable_mapping.schema 必须是 {_MAPPING_SCHEMA}")
    if payload.get("schema_version") != 1:
        raise ReconcileError("deliverable_mapping.schema_version 不受支持")
    expected = {
        "taskdoc_bytes_sha256": bindings["taskdoc_bytes_sha256"],
        "taskdoc_validation_digest": bindings["validation_digest"],
        "pr_facts_sha256": pr_sha256,
    }
    for key, want in expected.items():
        got = payload.get(key)
        if got != want:
            raise ReconcileError(
                f"deliverable_mapping.{key} 与当轮事实不符: 声明={got!r}, "
                f"实际={want!r}；换了任务书、校验工件或 PR 就必须重做指认，"
                "旧指认不得搬到新一轮")
    entries = payload.get("mappings")
    if not isinstance(entries, list):
        raise ReconcileError("deliverable_mapping.mappings 须为数组")
    out = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ReconcileError(f"mappings[{index}] 须为 object")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise ReconcileError(f"mappings[{index}].id 缺失或非字符串")
        entry_id = entry_id.strip()
        if entry_id in out:
            raise ReconcileError(f"mappings 对 {entry_id} 重复指认")
        disposition = entry.get("disposition")
        if disposition not in _DISPOSITIONS:
            raise ReconcileError(
                f"mappings[{entry_id}].disposition 不在受控词表 "
                f"{sorted(_DISPOSITIONS)}")
        paths = _str_list(entry.get("paths"), f"mappings[{entry_id}].paths")
        symbols = _str_list(entry.get("symbols"),
                            f"mappings[{entry_id}].symbols")
        rationale = entry.get("rationale")
        if disposition == "present" and not paths:
            # audit#34：symbol-only 覆盖不再被接受。符号必须绑在**指认的文件**里，
            # 否则「某个词在 PR 的某份关键文件里出现过」就成了交付证据。
            raise ReconcileError(
                f"mappings[{entry_id}]: disposition=present 必须指认至少一条 paths"
                "（symbols 只能作为已指认文件内的**附加**证据，不能单独成立）"
                "——「我看过了，有」不是归宿")
        if disposition != "present" and not (isinstance(rationale, str)
                                             and rationale.strip()):
            raise ReconcileError(
                f"mappings[{entry_id}]: disposition={disposition} 必须给出 "
                "rationale（查了哪里、为什么认不出或确认没有）")
        for symbol in symbols:
            if len(symbol) < _MIN_SYMBOL_CHARS:
                raise ReconcileError(
                    f"mappings[{entry_id}].symbols 的 {symbol!r} 过短"
                    f"（< {_MIN_SYMBOL_CHARS} 字符）：短串在关键文件里必然命中，"
                    "等于自动放行")
            if not _IDENTIFIER_RE.match(symbol):
                raise ReconcileError(
                    f"mappings[{entry_id}].symbols 的 {symbol!r} 不是合法标识符——"
                    "符号归宿只认完整标识符，任意子串会把注释/文案当成交付证据")
        out[entry_id] = {
            "id": entry_id,
            "disposition": disposition,
            "paths": paths,
            "symbols": symbols,
            "rationale": rationale if isinstance(rationale, str) else None,
        }
    return out


def _verify_path(path, changed_files):
    """路径归宿**只认逐字命中的具体文件**（audit#33）。

    旧实现还接受「目录前缀命中任意一个改动文件」——于是 `ops-cv/`、甚至仓根这种宽泛祖先目录，
    只要 PR 动了任何一个文件就算这件交付物「有归宿」。那不是归宿，是「PR 非空」。
    需要指认一个目录级交付件时，请把该目录下的**具体文件**逐条列进 `paths`。
    """
    if path in changed_files:
        return {"path": path, "match": "file", "changed_file": path}
    return None


def _strip_c_like_literals(text):
    """去掉 C/C++ 注释与字符串/字符字面量，避免注释或文案里的词冒充符号定义（audit#34）。"""
    without_comments = re.sub(r"/\*.*?\*/|//[^\n]*", " ", text, flags=re.S)
    return re.sub(r'"(?:\\.|[^"\\])*"' r"|'(?:\\.|[^'\\])*'", " ", without_comments)


def _verify_symbol(symbol, key_files, allowed_files):
    """符号归宿：必须**绑定到指认的文件**、是完整标识符、且按 token 边界命中（audit#34）。

    旧实现是「长度 ≥3 的原始子串在任意 key_file 里出现即算数」——普通英文词、注释、
    字符串文案都能当成交付证据。symbol-only 的指认（没有 paths）不再被接受：
    一个孤立的词证明不了「这件交付物在这个 PR 里」。
    """
    if not _IDENTIFIER_RE.match(symbol):
        return None
    pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(symbol) + r"(?![A-Za-z0-9_])")
    for source in sorted(key_files):
        if source not in allowed_files:
            continue
        if pattern.search(_strip_c_like_literals(key_files[source])):
            return {"symbol": symbol, "match": "key_file", "key_file": source}
    return None


def _reconcile_entry(entry, mapping, pr_facts):
    """单件交付物 → (归宿证据 | None, 缺口 | None)。"""
    base = {"id": entry["id"], "name": entry["name"],
            "requirement": entry["requirement"]}
    if pr_facts["blocked"] or not pr_facts["changed_files"]:
        reason = ("pr_side_blocked" if pr_facts["blocked"]
                  else "pr_evidence_unavailable")
        detail = (f"pr_facts 自报 blocked={pr_facts['blocked']!r}"
                  if pr_facts["blocked"]
                  else "pr_facts.changed_files 为空，PR 侧事实没取到")
        return None, dict(base, reason=reason, detail=detail,
                          rationale=None)
    declared = mapping.get(entry["id"])
    if declared is None:
        return None, dict(
            base, reason="unmapped",
            detail="没有任何指认：须在 deliverable_mapping.json 里给出它在 PR 里的"
                   "文件/目录/符号，或显式记为 absent / uncertain",
            rationale=None)
    if declared["disposition"] == "absent":
        return None, dict(base, reason="missing_in_pr",
                          detail="指认方声明本 PR 未交付该件",
                          rationale=declared["rationale"])
    if declared["disposition"] == "uncertain":
        return None, dict(base, reason="undetermined",
                          detail="指认方认不出归宿，须由编排层或人指认",
                          rationale=declared["rationale"])
    evidence, unverified = [], []
    for path in declared["paths"]:
        hit = _verify_path(path, pr_facts["changed_files"])
        (evidence if hit else unverified).append(hit or {"path": path})
    # 符号只在**本条指认的文件**里查（audit#34）：跨文件乱查等于「PR 里哪儿有这个词都算」。
    allowed_files = set(declared["paths"])
    for symbol in declared["symbols"]:
        hit = _verify_symbol(symbol, pr_facts["key_files"], allowed_files)
        (evidence if hit else unverified).append(hit or {"symbol": symbol})
    if unverified:
        return None, dict(
            base, reason="evidence_not_found",
            detail="指认为 present，但这些归宿在 PR 事实里查无实据："
                   f"{json.dumps(unverified, ensure_ascii=False, sort_keys=True)}"
                   "；key_files 只含 PR 的部分文件，故此处只说『没查到』，"
                   "不断言『PR 里没有』，须人复核",
            rationale=declared["rationale"])
    return dict(base, evidence=evidence, rationale=declared["rationale"]), None


def evaluate(root, validation_rel="taskdoc_validation.json",
             taskdoc_rel="task_doc.md", source_rel="source_facts.json",
             pr_facts_rel="pr_facts.json",
             mapping_rel="deliverable_mapping.json", contract_path=None):
    """对账必选交付件；返回结构化结果，绝不产验收裁决。"""
    result = {
        "schema": _RECONCILIATION_SCHEMA,
        "schema_version": 1,
        "scope": "taskdoc-deliverables-vs-pr",
        "acceptance_verdict": None,
        "status": "BLOCKED",
        "op": None,
        "inventory_complete": False,
        "taskdoc_validation_status": None,
        "covered": [],
        "gaps": [],
        "optional_findings": [],
        "errors": [],
        "bindings": {},
        # 源 provenance 的机读**降级**挂账（正常通路为空表）；恒存在，空表 ≠ 工具没记。
        "provenance_degradations": [],
        # 源 provenance 的机读**中性形态事实**（如本地源码没有上游 commit）。它不是降级，
        # 但报告必须原样带着——尤其不得据此声称「已绑定 PR head」。
        "provenance_form_facts": [],
    }
    try:
        receipt = taskdoc.evaluate(root, validation_rel, taskdoc_rel=taskdoc_rel,
                                   source_rel=source_rel,
                                   contract_path=contract_path)
        result["taskdoc_validation_status"] = receipt["status"]
        # audit#31：以前只拒 `BLOCKED`，于是 `NEEDS_USER`（还等着人补事实）与任何将来新增/
        # 拼错的状态都会继续往下跑，甚至可能落成 `RECONCILED`。改成**白名单**：
        # 只有明确通过的两个状态可以进对账，其余一律 fail-closed。
        if receipt["status"] not in _ACCEPTED_TASKDOC_STATUSES:
            raise ReconcileError(
                f"任务书校验状态={receipt['status']!r} 不在可对账白名单 "
                f"{sorted(_ACCEPTED_TASKDOC_STATUSES)}，交付件清单不可信，无从对账："
                + "；".join(receipt["errors"] or ["（无 errors 明细）"]))
        result["op"] = receipt["op"]
        inventory = receipt["deliverable_inventory"] or {}
        result["inventory_complete"] = bool(inventory.get("complete"))
        pr_facts = _load_pr_facts(root, pr_facts_rel)
        mapping = _load_mapping(root, mapping_rel, receipt["bindings"],
                                pr_facts["sha256"])
        # audit#32：mapping 的轮次绑定先判（错轮的指认与源身份无关，报错要指得准），
        # 再把裸 pr_facts 钉到内容寻址的 source_facts 上。
        provenance_bindings, provenance_degradations = _bind_pr_facts_to_source(
            root, source_rel, pr_facts)
        result["provenance_degradations"] = provenance_degradations
        result["provenance_form_facts"] = source_provenance.form_facts(
            provenance_bindings)
        result["bindings"] = {
            "taskdoc_bytes_sha256": receipt["bindings"]["taskdoc_bytes_sha256"],
            "taskdoc_validation_digest": receipt["bindings"]["validation_digest"],
            "contract_digest": receipt["bindings"]["contract_digest"],
            "pr_facts_sha256": pr_facts["sha256"],
            "pr_url": pr_facts["pr_url"],
            # PR head **只认 source_provenance 绑出来的那个**，不再抄 pr_facts 自报值。
            # 本地源码形态下它是 null，且那是正确值——读产物的人据 declared_source_form
            # 区分「本来就没有上游 commit」与「本该绑却没绑」。
            "pr_head_sha": provenance_bindings["pr_head_sha"],
            "provenance_kind": provenance_bindings["provenance_kind"],
            source_provenance.DECLARED_FORM_KEY: provenance_bindings[
                source_provenance.DECLARED_FORM_KEY],
        }
        known = {entry["id"] for entry in receipt["deliverables"]}
        unknown = sorted(set(mapping) - known)
        if unknown:
            raise ReconcileError(
                f"deliverable_mapping 指认了清单里没有的交付件 id: {unknown}；"
                "对账只对得上 CP-B0 抽出来的清单，凭空多出的条目只会制造假覆盖")
        for entry in receipt["deliverables"]:
            covered, gap = _reconcile_entry(entry, mapping, pr_facts)
            if entry["requirement"] != "required":
                result["optional_findings"].append(covered or gap)
                continue
            if covered is not None:
                result["covered"].append(covered)
            else:
                result["gaps"].append(gap)
        if not result["inventory_complete"]:
            result["gaps"].insert(0, {
                "id": None,
                "name": inventory.get("owner_item"),
                "requirement": "required",
                "reason": "inventory_incomplete",
                "detail": "CP-B0 交付件清单尚未覆盖任务书里全部交付定性标记"
                          f"（未覆盖 {len(inventory.get('uncovered_sites', []))} 处，"
                          f"owner_status={inventory.get('owner_status')!r}）；"
                          "清单可能还漏着必选件，此时不能宣称必选件全部有归宿",
                "rationale": None,
            })
        result["status"] = "RECONCILED" if not result["gaps"] else "GAPS"
    except (ReconcileError, taskdoc.TaskdocValidationError,
            content_address.ContentAddressError) as ex:
        result["status"] = "BLOCKED"
        result["errors"] = [str(ex)]
    return result


def _exit_code(status):
    if status == "RECONCILED":
        return 0
    if status == "GAPS":
        return 2
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="任务书必选交付件 ↔ PR 实际交付物对账；不产验收裁决")
    ap.add_argument("--root", required=True, help="CP-A 取材工作区根目录")
    ap.add_argument("--validation", default="taskdoc_validation.json",
                    help="root 内任务书校验工件相对路径")
    ap.add_argument("--taskdoc", default="task_doc.md",
                    help="root 内任务书原文相对路径")
    ap.add_argument("--source", default="source_facts.json",
                    help="root 内 source facts 相对路径")
    ap.add_argument("--pr-facts", default="pr_facts.json",
                    help="root 内 pr_facts 相对路径")
    ap.add_argument("--mapping", default="deliverable_mapping.json",
                    help="root 内交付件归宿指认相对路径（不存在即视为一条未指认）")
    ap.add_argument("--out", default=None, help="root 内对账产物相对路径（可选）")
    args = ap.parse_args(argv)
    result = evaluate(args.root, args.validation, taskdoc_rel=args.taskdoc,
                      source_rel=args.source, pr_facts_rel=args.pr_facts,
                      mapping_rel=args.mapping)
    if args.out and os.environ.get(_DRY_RUN_ENV) != "1":
        content_address.write_artifact(
            os.path.abspath(args.root), args.out, _RECONCILIATION_DOMAIN, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return _exit_code(result["status"])


if __name__ == "__main__":
    sys.exit(main())
