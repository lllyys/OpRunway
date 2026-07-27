#!/usr/bin/env python3
"""ACLNN ABI 的纯静态 CP-C0 预检（aclnn_py / cpp_extension 共用）。

只用 CP-A/B 已取到的 PR header 与 spec 做签名/slots 对账；不 clone、不 build、不加载
`.so`、不访问 NPU。成功状态只能是 ``READY_WAIT_NPU_TRUST_GATE``，不能替代后续真机
build、DUT 定义方核验或 harness trust gate。
"""

import argparse
import hashlib
import json
import os
import sys

import content_address
import gen_cases
import precision_policy
from aclnn_runtime.aclnn_runner import (
    AclnnRunner,
    AclnnRunnerError,
    parse_aclnn_signature,
)


_SOURCE_DOMAIN = "oprunway/source-facts/v1"
_PREFLIGHT_DOMAIN = "oprunway/aclnn-preflight/v1"


def _strict_json(path):
    with open(path, "r", encoding="utf-8") as src:
        value = json.load(
            src,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"非法 JSON 常量: {token}")),
        )
    content_address.canonical_json_bytes(value)
    return value


def _sha(value):
    return hashlib.sha256(content_address.canonical_json_bytes(value)).hexdigest()


def _self_sha256():
    with open(__file__, "rb") as src:
        return hashlib.sha256(src.read()).hexdigest()


def _abstract_slots(spec, variant):
    active_attrs = set(variant["active_attrs"])
    active_outputs = set(variant["active_outputs"])
    slots = []
    params = spec.get("params")
    if not isinstance(params, list):
        raise ValueError("spec.params 须为 JSON array")
    for index, param in enumerate(params):
        if not isinstance(param, dict):
            raise ValueError(
                f"spec.params[{index}] 须为 JSON object，得 {type(param).__name__}")
        role, name = param.get("io"), param.get("name")
        if role == "in":
            slots.append({"kind": "in", "name": name})
        elif role == "attr":
            if name in active_attrs:
                slots.append({
                    "kind": "attr",
                    "name": name,
                    "ctype": gen_cases._attr_ctype(param),
                })
        elif role == "out":
            slots.append({
                "kind": "out" if name in active_outputs else "out_null",
                "name": name,
            })
        else:
            raise ValueError(
                f"spec param {name!r} 的 io={role!r} 非 in/attr/out")
    return slots


def evaluate(root, spec_rel, pr_facts_rel="pr_facts.json",
             source_rel="source_facts.json"):
    """返回静态预检 payload；异常全部收敛成机读 ``BLOCKED``。"""
    root = os.path.abspath(root)
    result = {
        "schema": "oprunway.aclnn_preflight",
        "schema_version": 1,
        "status": "BLOCKED",
        "scope": "static-only",
        "acceptance_verdict": None,
        "required_next_gate": "NPU_BUILD_AND_HARNESS_TRUST_GATE",
        "bindings": {},
        "signatures": [],
        "variants": [],
        "blocked_reasons": [],
        "producer": {
            "tool": "preflight_aclnn.py",
            "logic_sha256": _self_sha256(),
        },
    }
    try:
        source = content_address.read_artifact(
            root, source_rel, _SOURCE_DOMAIN)
        if not isinstance(source, dict):
            raise ValueError("source_facts payload 须为 JSON object")
        source_completeness = source.get("completeness")
        if (not isinstance(source_completeness, dict)
                or source_completeness.get("status") != "complete"):
            raise ValueError("source_facts completeness 不是 complete")
        source_digest = content_address.content_digest(_SOURCE_DOMAIN, source)
        result["bindings"]["source_facts_digest"] = source_digest

        spec = _strict_json(content_address.safe_path(root, spec_rel))
        pr_facts = _strict_json(content_address.safe_path(root, pr_facts_rel))
        if not isinstance(spec, dict) or not isinstance(pr_facts, dict):
            raise ValueError("spec/pr_facts 须为 JSON object")
        result["bindings"]["spec_sha256"] = _sha(spec)
        result["bindings"]["pr_head_sha"] = pr_facts.get("head_sha")

        runner_form = spec.get("runner_form", "cpp")
        if runner_form not in ("aclnn_py", "cpp_extension"):
            result["status"] = "NOT_APPLICABLE"
            result["blocked_reasons"] = []
            return result
        result["bindings"]["runner_form"] = runner_form
        result["required_next_gate"] = (
            "CPP_EXTENSION_BUILD_LOAD_AND_HARNESS_TRUST_GATE"
            if runner_form == "cpp_extension"
            else "NPU_BUILD_AND_HARNESS_TRUST_GATE")

        source_pr = source.get("pr")
        if not isinstance(source_pr, dict):
            raise ValueError("source_facts.pr 须为 JSON object")
        source_head = source_pr.get("head_sha")
        if not source_head or pr_facts.get("head_sha") != source_head:
            raise ValueError("pr_facts.head_sha 与 source_facts 绑定不一致")

        key_files = pr_facts.get("key_files")
        if not isinstance(key_files, dict):
            raise ValueError("pr_facts.key_files 缺失或非 object")
        source_keys = {
            item["path"]: item for item in source.get("key_files") or []
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        source_derived = source.get("derived")
        if not isinstance(source_derived, dict):
            raise ValueError("source_facts.derived 须为 JSON object")
        header_paths = source_derived.get("aclnn_headers") or []
        if not header_paths:
            raise ValueError("source_facts 未声明 aclnn_headers")

        signatures = {}
        for path in sorted(header_paths):
            text = key_files.get(path)
            if not isinstance(text, str):
                raise ValueError(f"PR head 关键文件中缺接口头正文: {path}")
            raw = text.encode("utf-8")
            recorded = source_keys.get(path)
            if not recorded:
                raise ValueError(f"source_facts.key_files 未绑定接口头: {path}")
            if hashlib.sha256(raw).hexdigest() != recorded.get("bytes_sha256"):
                raise ValueError(f"接口头正文与 source_facts 摘要不一致: {path}")
            signature = parse_aclnn_signature(text)
            if signature.op_name in signatures:
                raise ValueError(
                    f"aclnn{signature.op_name} 签名在多份 header 重复，无法唯一绑定")
            signatures[signature.op_name] = (path, signature)
            result["signatures"].append({
                "symbol": signature.op_name,
                "header": path,
                "params": [dict(param) for param in signature.params],
                "bytes_sha256": recorded["bytes_sha256"],
            })

        variants = precision_policy.call_variants(spec)
        if not variants:
            raise ValueError(
                f"runner_form={runner_form} 但 spec.call_variants 缺失")
        for index, variant in enumerate(variants):
            symbol = variant["symbol"]
            if symbol not in signatures:
                raise ValueError(
                    f"call_variants[{index}] symbol={symbol!r} 在 PR head header 中无唯一签名")
            slots = _abstract_slots(spec, variant)
            _, signature = signatures[symbol]
            AclnnRunner._validate_slots_against_signature(
                slots, signature, symbol)
            result["variants"].append({
                "index": index,
                "symbol": symbol,
                "when": variant["when"],
                "slot_contract": [{
                    "name": slot["name"],
                    "role": ("out" if slot["kind"] == "out_null"
                             else slot["kind"]),
                    "nullable": slot["kind"] == "out_null",
                    **({"ctype": slot["ctype"]}
                       if slot["kind"] == "attr" else {}),
                } for slot in slots],
                "status": "STATIC_SIGNATURE_MATCH",
            })
        result["status"] = "READY_WAIT_NPU_TRUST_GATE"
        return result
    except (AclnnRunnerError, content_address.ContentAddressError,
            KeyError, OSError, TypeError, ValueError,
            json.JSONDecodeError, UnicodeError) as ex:
        result["blocked_reasons"].append(str(ex))
        return result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="ACLNN ABI 静态签名预检；成功后仍必须过对应真机 trust gate")
    ap.add_argument("--root", required=True, help="CP-A/B 工件根目录")
    ap.add_argument("--spec", required=True, help="root 内 spec 相对路径")
    ap.add_argument("--pr-facts", default="pr_facts.json",
                    help="root 内 pr_facts 相对路径")
    ap.add_argument("--source", default="source_facts.json",
                    help="root 内 source_facts 相对路径")
    ap.add_argument("--out", default=None, help="root 内输出相对路径（可选）")
    args = ap.parse_args(argv)
    result = evaluate(
        args.root, args.spec, pr_facts_rel=args.pr_facts,
        source_rel=args.source)
    if args.out:
        content_address.write_artifact(
            os.path.abspath(args.root), args.out, _PREFLIGHT_DOMAIN, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {
        "READY_WAIT_NPU_TRUST_GATE", "NOT_APPLICABLE"} else 2


if __name__ == "__main__":
    sys.exit(main())
