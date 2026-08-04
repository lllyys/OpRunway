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
import sys

import content_address
import validate_taskdoc_input as taskdoc


_RECONCILIATION_DOMAIN = "oprunway/deliverable-reconciliation/v1"
_RECONCILIATION_SCHEMA = "oprunway.deliverable_reconciliation"
_MAPPING_SCHEMA = "oprunway.deliverable_mapping"
_DRY_RUN_ENV = "OPRUNWAY_DELIVERABLE_RECONCILE_DRY_RUN"

_DISPOSITIONS = frozenset({"present", "absent", "uncertain"})
# 太短的「符号」在几十 KB 的关键文件里必然命中，等于自动放行；逐字符号至少要有辨识度。
_MIN_SYMBOL_CHARS = 3


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
        "pr_url": payload.get("pr_url"),
        "head_sha": payload.get("head_sha"),
        "changed_files": sorted(changed),
        "key_files": texts,
        "blocked": blocked,
    }


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
        if disposition == "present" and not (paths or symbols):
            raise ReconcileError(
                f"mappings[{entry_id}]: disposition=present 必须指认至少一条 "
                "paths 或 symbols——「我看过了，有」不是归宿")
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
        out[entry_id] = {
            "id": entry_id,
            "disposition": disposition,
            "paths": paths,
            "symbols": symbols,
            "rationale": rationale if isinstance(rationale, str) else None,
        }
    return out


def _verify_path(path, changed_files):
    """路径归宿只认逐字命中或逐字目录前缀，绝不做名字近似。"""
    if path in changed_files:
        return {"path": path, "match": "file", "changed_file": path}
    prefix = path.rstrip("/") + "/"
    hits = [p for p in changed_files if p.startswith(prefix)]
    if hits:
        return {"path": path, "match": "directory", "changed_file": hits[0],
                "changed_file_count": len(hits)}
    return None


def _verify_symbol(symbol, key_files):
    for source in sorted(key_files):
        if symbol in key_files[source]:
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
    for symbol in declared["symbols"]:
        hit = _verify_symbol(symbol, pr_facts["key_files"])
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
    }
    try:
        receipt = taskdoc.evaluate(root, validation_rel, taskdoc_rel=taskdoc_rel,
                                   source_rel=source_rel,
                                   contract_path=contract_path)
        result["taskdoc_validation_status"] = receipt["status"]
        if receipt["status"] == "BLOCKED":
            raise ReconcileError(
                "任务书校验工件自身 BLOCKED，交付件清单不可信，无从对账："
                + "；".join(receipt["errors"]))
        result["op"] = receipt["op"]
        inventory = receipt["deliverable_inventory"] or {}
        result["inventory_complete"] = bool(inventory.get("complete"))
        pr_facts = _load_pr_facts(root, pr_facts_rel)
        mapping = _load_mapping(root, mapping_rel, receipt["bindings"],
                                pr_facts["sha256"])
        result["bindings"] = {
            "taskdoc_bytes_sha256": receipt["bindings"]["taskdoc_bytes_sha256"],
            "taskdoc_validation_digest": receipt["bindings"]["validation_digest"],
            "contract_digest": receipt["bindings"]["contract_digest"],
            "pr_facts_sha256": pr_facts["sha256"],
            "pr_url": pr_facts["pr_url"],
            "pr_head_sha": pr_facts["head_sha"],
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
