#!/usr/bin/env python3
"""aclnn_py CP-C 真机 harness 信任门。

本脚本不是算子验收裁决器。它从完整 caseset 中确定性选择一个最小见证集：

* 覆盖本轮全部输入 dtype；
* 覆盖每个静态签名/slot 变体（参数顺序随之被真实调用）；
* 若接口含标量 attr / 多输出，则至少真实执行一例；
* 每个拉回输出都与 caseset 中绑定的 CPU golden 按既定 policy 对拍。

成功只产 ``TRUSTED_FOR_CP_D`` 的内容寻址收据；正式 Task2/Task3 的用例、精度
标准与性能采集策略均不在这里修改。CP-D 会重新生成完整 caseset，并在启动
adapter 前复核这份收据与当前 spec/caseset/执行逻辑仍完全绑定。
"""

import argparse
import copy
import hashlib
import json
import math
import os
import sys

import aclnn_adapter
import content_address
import repo_adapter
import validator


_PREFLIGHT_DOMAIN = "oprunway/aclnn-preflight/v1"
_TRUST_DOMAIN = "oprunway/aclnn-harness-trust/v1"
_SCHEMA = "oprunway.aclnn_harness_trust"
_STATUS_TRUSTED = "TRUSTED_FOR_CP_D"
_LOGIC_FILES = (
    "verify_aclnn_harness.py",
    "aclnn_adapter.py",
    "repo_adapter.py",
    "precision_policy.py",
    "validator.py",
    "content_address.py",
    "aclnn_runtime/__init__.py",
    "aclnn_runtime/base.py",
    "aclnn_runtime/acl_consts.py",
    "aclnn_runtime/aclnn_driver.py",
    "aclnn_runtime/aclnn_runner.py",
)


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


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _logic_hashes():
    here = os.path.dirname(os.path.abspath(__file__))
    return {
        rel: _file_sha(os.path.join(here, *rel.split("/")))
        for rel in _LOGIC_FILES
    }


def _shape_numel(shape):
    if not isinstance(shape, list):
        return None
    n = 1
    for dim in shape:
        if not isinstance(dim, int) or isinstance(dim, bool) or dim < 0:
            return None
        n *= dim
    return n


def _case_cost(case):
    """以输入+输出 numel 估算见证成本；只用于确定性择小，不改变正式用例。"""
    total = 0
    for inp in case.get("inputs") or []:
        n = _shape_numel(inp.get("shape"))
        if n is None:
            return math.inf
        total += n
    expected = case.get("expected") or {}
    outputs = expected.get("outputs")
    if isinstance(outputs, list):
        for out in outputs:
            n = _shape_numel(out.get("out_shape"))
            if n is None:
                return math.inf
            total += n
    return total


def _call_contract(case):
    call = case.get("aclnn_call")
    if not isinstance(call, dict) or not isinstance(call.get("slots"), list):
        raise ValueError(f"{case.get('id')}: 缺 aclnn_call.slots")
    contract = []
    for index, slot in enumerate(call["slots"]):
        if not isinstance(slot, dict):
            raise ValueError(f"{case.get('id')}: aclnn_call.slots[{index}] 非 object")
        role = slot.get("role")
        if role not in {"in", "attr", "out", "out_null"}:
            raise ValueError(f"{case.get('id')}: slot role={role!r} 非法")
        item = {
            "name": slot.get("name"),
            "role": "out" if role == "out_null" else role,
            "nullable": role == "out_null",
        }
        if role == "attr":
            item["ctype"] = slot.get("ctype")
        contract.append(item)
    return call.get("symbol"), contract


def _variant_index(case, variants):
    symbol, contract = _call_contract(case)
    hits = [
        i for i, item in enumerate(variants)
        if item.get("symbol") == symbol and item.get("slot_contract") == contract
    ]
    if len(hits) != 1:
        raise ValueError(
            f"{case.get('id')}: aclnn_call 无法唯一绑定 preflight variant，命中={hits}")
    return hits[0], contract


def _case_coverage(case, variants):
    cid = case.get("id")
    if not isinstance(cid, str) or not cid:
        raise ValueError("caseset case 缺非空 id")
    expected = case.get("expected")
    if not isinstance(expected, dict):
        raise ValueError(f"{cid}: 缺 expected")
    outputs = expected.get("outputs")
    if isinstance(outputs, list):
        if not outputs:
            raise ValueError(f"{cid}: expected.outputs 为空")
        if any(_shape_numel(out.get("out_shape")) in (None, 0) for out in outputs):
            return set(), math.inf
        if any(not isinstance(out.get("policy"), dict) for out in outputs):
            return set(), math.inf
    idx, contract = _variant_index(case, variants)
    coverage = {f"variant:{idx}"}
    for inp in case.get("inputs") or []:
        dtype = inp.get("dtype")
        if not isinstance(dtype, str) or not dtype:
            raise ValueError(f"{cid}: input dtype 缺失")
        coverage.add(f"dtype:{dtype}")
    if any(slot["role"] == "attr" for slot in contract):
        coverage.add("capability:scalar_attr")
    if sum(slot["role"] == "out" and not slot["nullable"] for slot in contract) >= 2:
        coverage.add("capability:multi_output")
    return coverage, _case_cost(case)


def select_cases(caseset, preflight):
    """返回最小确定性见证集及覆盖说明；不可覆盖时 fail-closed。"""
    cases = caseset.get("cases")
    variants = preflight.get("variants")
    if not isinstance(cases, list) or not cases:
        raise ValueError("caseset.cases 须为非空 array")
    if not isinstance(variants, list) or not variants:
        raise ValueError("preflight.variants 须为非空 array")

    required_dtypes = caseset.get("dtype_required")
    if not isinstance(required_dtypes, list) or not required_dtypes:
        required_dtypes = sorted({
            inp.get("dtype")
            for case in cases for inp in (case.get("inputs") or [])
            if isinstance(inp, dict) and isinstance(inp.get("dtype"), str)
        })
    if not required_dtypes or any(not isinstance(x, str) or not x for x in required_dtypes):
        raise ValueError("caseset.dtype_required 缺失/非法，无法证明每种 dtype 已见证")

    required = {f"dtype:{dtype}" for dtype in required_dtypes}
    required.update(f"variant:{i}" for i in range(len(variants)))
    contracts = [
        slot for variant in variants for slot in (variant.get("slot_contract") or [])
        if isinstance(slot, dict)
    ]
    if any(slot.get("role") == "attr" for slot in contracts):
        required.add("capability:scalar_attr")
    if any(
        sum(slot.get("role") == "out" and not slot.get("nullable", False)
            for slot in (variant.get("slot_contract") or [])) >= 2
        for variant in variants
    ):
        required.add("capability:multi_output")

    candidates = []
    for case in cases:
        coverage, cost = _case_coverage(case, variants)
        if coverage:
            candidates.append((case, coverage, cost))
    uncovered = set(required)
    selected = []
    remaining = list(candidates)
    while uncovered:
        useful = [
            item for item in remaining if item[1] & uncovered
        ]
        if not useful:
            raise ValueError(f"caseset 无法覆盖 harness 信任门要求: {sorted(uncovered)}")
        useful.sort(key=lambda item: (
            -len(item[1] & uncovered),
            item[2],
            item[0]["id"],
        ))
        chosen = useful[0]
        selected.append(chosen[0])
        uncovered -= chosen[1]
        remaining.remove(chosen)
    return selected, {
        "required": sorted(required),
        "covered": sorted(required),
        "selected_case_ids": [case["id"] for case in selected],
        "selected_count": len(selected),
        "full_case_count": len(cases),
        "selection_rule": "greedy(max-uncovered, min-numel, case-id)",
    }


def _judge_evidence(selected, evidence):
    by_id = {}
    for item in evidence:
        cid = item.get("case_id") if isinstance(item, dict) else None
        if not isinstance(cid, str) or cid in by_id:
            raise ValueError(f"harness evidence case_id 缺失或重复: {cid!r}")
        by_id[cid] = item
    expected_ids = [case["id"] for case in selected]
    if set(by_id) != set(expected_ids):
        raise ValueError(
            f"harness evidence case 集不一致: expected={sorted(expected_ids)}, "
            f"actual={sorted(by_id)}")
    checks = []
    for case in selected:
        cid = case["id"]
        item = by_id[cid]
        if item.get("status") != "ok":
            raise ValueError(f"{cid}: harness evidence status={item.get('status')!r}")
        precision = item.get("precision")
        if not isinstance(precision, dict):
            raise ValueError(f"{cid}: evidence.precision 缺失")
        outputs = precision.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            outputs = [precision]
        output_checks = []
        for index, out in enumerate(outputs):
            policy, metrics = out.get("policy"), out.get("metrics")
            verdict, detail = validator._judge_by_policy(policy, metrics)
            if verdict != "pass":
                raise ValueError(
                    f"{cid} output#{index}: CPU golden 对拍未过: {verdict} ({detail})")
            output_checks.append({
                "index": index,
                "name": out.get("name"),
                "role": out.get("role"),
                "policy_kind": policy.get("kind"),
                "result": verdict,
                "detail": detail,
                "golden_sha256": (out.get("provenance") or {}).get("golden_sha256"),
                "out_sha256": (out.get("provenance") or {}).get("out_sha256"),
            })
        checks.append({"case_id": cid, "result": "pass", "outputs": output_checks})
    return checks


def _receipt_bindings(spec, caseset, preflight):
    return {
        "spec_sha256": _sha(spec),
        "caseset_sha256": _sha(caseset),
        "preflight_digest": content_address.content_digest(
            _PREFLIGHT_DOMAIN, preflight),
        "pr_head_sha": (preflight.get("bindings") or {}).get("pr_head_sha"),
        "logic_files": _logic_hashes(),
    }


def validate_receipt(root, receipt_rel, spec, caseset):
    """供 CP-D 使用：复核信任门收据与本轮完整 caseset/当前逻辑仍绑定。"""
    receipt = content_address.read_artifact(root, receipt_rel, _TRUST_DOMAIN)
    preflight = content_address.read_artifact(
        root, "work/aclnn_preflight.json", _PREFLIGHT_DOMAIN)
    if not isinstance(receipt, dict):
        raise ValueError("harness trust receipt payload 须为 object")
    if receipt.get("schema") != _SCHEMA or receipt.get("schema_version") != 1:
        raise ValueError("harness trust receipt schema/version 不受支持")
    if receipt.get("status") != _STATUS_TRUSTED:
        raise ValueError(f"harness trust status 非可信: {receipt.get('status')!r}")
    if receipt.get("acceptance_verdict") is not None:
        raise ValueError("harness trust receipt 不得携带算子验收裁决")
    bindings = receipt.get("bindings")
    if not isinstance(bindings, dict):
        raise ValueError("harness trust receipt.bindings 缺失")
    expected = {
        "spec_sha256": _sha(spec),
        "caseset_sha256": _sha(caseset),
        "preflight_digest": content_address.content_digest(
            _PREFLIGHT_DOMAIN, preflight),
        "pr_head_sha": (preflight.get("bindings") or {}).get("pr_head_sha"),
        "logic_files": _logic_hashes(),
    }
    for key, value in expected.items():
        if bindings.get(key) != value:
            raise ValueError(f"harness trust receipt {key} 已漂移")
    coverage = receipt.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("harness trust receipt.coverage 缺失")
    selected = coverage.get("selected_case_ids")
    if not isinstance(selected, list) or not selected:
        raise ValueError("harness trust receipt 未记录非空见证集")
    full_ids = {case.get("id") for case in caseset.get("cases") or []}
    if any(cid not in full_ids for cid in selected):
        raise ValueError("harness trust receipt 见证 case 不属于本轮 caseset")
    checks = receipt.get("checks")
    if not isinstance(checks, list) or {x.get("case_id") for x in checks} != set(selected):
        raise ValueError("harness trust receipt 对拍检查与见证集不一致")
    return receipt


def run_gate(root, spec_rel, caseset_rel, preflight_rel, out_rel):
    """执行真机见证并原子落内容寻址收据；失败不写 TRUSTED 收据。"""
    root = os.path.abspath(root)
    spec = _strict_json(content_address.safe_path(root, spec_rel))
    caseset = _strict_json(content_address.safe_path(root, caseset_rel))
    preflight = content_address.read_artifact(
        root, preflight_rel, _PREFLIGHT_DOMAIN)
    if not isinstance(spec, dict) or not isinstance(caseset, dict):
        raise ValueError("spec/caseset 须为 object")
    if spec.get("runner_form") != "aclnn_py":
        raise ValueError("harness trust gate 仅适用于 runner_form=aclnn_py")
    if caseset.get("op") != spec.get("op"):
        raise ValueError("spec.op 与 caseset.op 不一致")
    if preflight.get("status") != "READY_WAIT_NPU_TRUST_GATE":
        raise ValueError(
            f"aclnn preflight 未就绪: {preflight.get('status')!r}")
    if (preflight.get("bindings") or {}).get("spec_sha256") != _sha(spec):
        raise ValueError("aclnn preflight 与当前 spec 不绑定")
    if os.environ.get("OPRUNWAY_ACLNN_REAL") != "1":
        raise ValueError("真机 harness 信任门须显式设置 OPRUNWAY_ACLNN_REAL=1")

    work = content_address.safe_path(root, "work")
    if not os.path.isdir(work):
        raise ValueError("报告根下缺 work/（须先生成完整 caseset + golden）")
    selected, coverage = select_cases(caseset, preflight)
    witness = copy.deepcopy(caseset)
    witness["cases"] = copy.deepcopy(selected)
    witness["emitted"] = len(selected)
    witness["dtype_tested"] = sorted({
        inp["dtype"] for case in selected for inp in case.get("inputs") or []
    })

    cfg = aclnn_adapter._aclnn_cfg()
    proj = aclnn_adapter.find_aclnn_project(
        spec["op"], cfg["ops_root"], cfg["op_subdir"])
    out_dir = os.path.join(work, "aclnn_trust_out")
    provenance = aclnn_adapter._run_aclnn_real(
        cfg, proj, witness, work, out_dir)
    evidence = repo_adapter.build_multi_output_evidence(
        witness, work, out_dir)
    checks = _judge_evidence(selected, evidence)
    payload = {
        "schema": _SCHEMA,
        "schema_version": 1,
        "status": _STATUS_TRUSTED,
        "scope": "harness-only",
        "acceptance_verdict": None,
        "bindings": _receipt_bindings(spec, caseset, preflight),
        "coverage": coverage,
        "checks": checks,
        "build_provenance": provenance,
        "note": (
            "仅证明通用 aclnn_py harness 对当前 PR 签名、dtype 与 CPU golden 的最小见证；"
            "不替代、不裁剪正式 Task2/Task3。"),
    }
    content_address.write_artifact(root, out_rel, _TRUST_DOMAIN, payload)
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="aclnn_py CP-C 真机 harness 信任门（不产算子验收裁决）")
    ap.add_argument("--root", required=True, help="报告根（其下含 work/ 与 caseset）")
    ap.add_argument("--spec", required=True, help="root 内 spec 相对路径")
    ap.add_argument("--caseset", default="caseset.json", help="root 内完整 caseset")
    ap.add_argument("--preflight", default="work/aclnn_preflight.json",
                    help="root 内 CP-C0 内容寻址工件")
    ap.add_argument("--out", default="work/aclnn_harness_trust.json",
                    help="root 内信任门收据")
    args = ap.parse_args(argv)
    try:
        payload = run_gate(
            args.root, args.spec, args.caseset, args.preflight, args.out)
    except (content_address.ContentAddressError, OSError, RuntimeError,
            TypeError, ValueError, json.JSONDecodeError, UnicodeError) as ex:
        print(json.dumps({
            "schema": _SCHEMA,
            "schema_version": 1,
            "status": "BLOCKED",
            "acceptance_verdict": None,
            "reason": str(ex),
        }, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
