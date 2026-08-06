#!/usr/bin/env python3
"""aclnn_py CP-C 真机 harness 信任门。

本脚本不是算子验收裁决器。它从完整 caseset 中确定性选择一个小见证集：

* 覆盖本轮全部输入 dtype；
* 覆盖每个静态签名/slot 变体（参数顺序随之被真实调用）；
* 若接口含标量 attr / 多输出，则至少真实执行一例；
* 每个拉回输出都与 caseset 中绑定的 CPU golden 按既定 policy 对拍。

成功只产 ``TRUSTED_FOR_CP_D`` 的内容寻址收据；正式 Task2/Task3 的用例、精度
标准与性能采集策略均不在这里修改。CP-D 会重新生成完整 caseset，并在启动
adapter 前复核这份收据与当前 spec/caseset/执行逻辑仍完全绑定。

**来源通路**：本门目前只接 ``dut_source == "pull_request"``，``local_checkout``
显式 fail-closed（见 ``_require_pull_request_path``）。
"""

import argparse
import copy
import hashlib
import json
import math
import os
import re
import shlex
import sys

import aclnn_adapter
import content_address
# ⚠ 只 import 判别式**内核**，不 import 聚合模块 `dut_source`：本门在来源这块的判定依赖
#   就是 `of()` + `PULL_REQUEST` 两个名字，而 `_LOGIC_FILES` 是逐字节哈希。多绑一个
#   `dut_source.py`，「改一个 URL 脱敏边界 / 调一下 source_facts 搜索顺序」就会作废
#   真机 harness 收据、白跑一次昂贵的 NPU 见证。绑定面 = 判定面，不多不少。
#   ⚠ 反过来也成立：哪天本门真要用 `dut_source` 里的东西，就**必须**同时把
#   `dut_source.py` 加进 `_LOGIC_FILES`——`LogicBindingCoverageTest` 会当场变红。
import dut_source_kind
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
    # ⚠ 判别式是本信任门的判定依赖（见 `_require_pull_request_path`），必须列进来：
    #   漏了它，`bindings.logic_files` 就覆盖不到判别逻辑——有人把 `of()` 改成
    #   「未知取值缺省 pull_request」，旧收据照样 revalidate 通过，等于开一个新的 fail-open 面。
    # ⚠ 这里绑的是**内核** `dut_source_kind.py`（受控词表 + `of`），不是聚合模块
    #   `dut_source.py`。后者还装着 URL 凭据策略 / build receipt 锚校验 /
    #   `source_facts.json` 查找三类与本门判定**无关**的职责，绑它 = 那三类改动一动
    #   就作废真机收据。内核零 import，逐字节哈希它 == 覆盖它的全部判定语义。
    "dut_source_kind.py",
    "gen_cases.py",
    "aclnn_runtime/__init__.py",
    "aclnn_runtime/base.py",
    "aclnn_runtime/acl_consts.py",
    "aclnn_runtime/aclnn_driver.py",
    "aclnn_runtime/aclnn_runner.py",
)
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _require_bindings(preflight):
    """取出 `preflight.bindings` 并把形态硬化在**任何**字段读取之前；判不出形态就停。

    单独拆出来是因为 `bindings` 有两个触碰点：来源通路门（`_require_pull_request_path`）
    与 `run_gate` 里更早的 spec 绑定核对。两处都必须走同一套形态判定，否则同一个畸形
    payload 会按触碰顺序给出两种不同的失败形态。
    """
    # ⚠ 这两道检查现在是 preflight 的**第一次触碰**，形态要自己扛住：payload 不是 object
    #   时 `.get` 会抛 AttributeError（不在调用方的收敛清单里），当场变成裸 traceback。
    if not isinstance(preflight, dict):
        raise ValueError("aclnn preflight payload 须为 JSON object")
    bindings = preflight.get("bindings")
    # ⚠ 这里**不能**简化成 `preflight.get("bindings") or {}`。`dut_source_kind.of()` 的
    #   「缺席即 pull_request」是给**旧收据**的向后兼容，前提是 payload 形态本身可信；
    #   `or {}` 会把缺席 / None / `[]` / 字符串一律抹平成空 object，于是「这份 preflight
    #   根本没有来源声明」和「它明确声明了 pull_request」在通路门里变成同一件事——
    #   本地通路的 preflight 只要 bindings 丢了形态（写坏、被裁剪、schema 换代），
    #   那道门就当场判它是 PR 通路**放行**，而通路门的全部意义就是拦住这一步。
    #   今天不出事靠的是下游 40-hex 硬化与 `provenance.head_sha != None` 的**间接**兜底，
    #   那是偶然的 fail-closed、不是设计。形态判不出来 = 来源判不出来 = 停。
    #   注：空 object `{}` 仍然放行——它是合法 JSON object，正是上面那条向后兼容要接的形态。
    if not isinstance(bindings, dict):
        raise ValueError(
            "aclnn_preflight.bindings 缺失或不是 JSON object，无法判定来源通路 —— fail-closed")
    return bindings


def _require_pull_request_path(preflight):
    """本轮 aclnn_py 真机 harness 信任门只接 `pull_request` 来源通路，其余一律 fail-closed。

    **为什么是显式 BLOCK 而不是接通**：`aclnn_adapter` 只能按 PR ref 在容器内重新取源
    build，构建端根本没有可与 `local_checkout.root_digest` 对账的锚。放它过去，vendor `.so`
    与被测字节之间就没有机器可核的对应关系了——收据看着齐全，绑定其实是空的。
    这是**如实挂账**：通路没接就说没接。

    **为什么要在三处各调一次**（不能只留一处）：`_validate_build_provenance` 是
    `run_gate` 与 `validate_receipt` 的共同必经点，是兜底；但两条入口都会**更早**碰到
    `aclnn_adapter._aclnn_cfg()`，那里在缺 `OPRUNWAY_ACLNN_PR_REF` 时抛的是
    「PR head 引用必填」——本地通路会因此拿到一个**误导性**报错，把「这条通路没接」
    说成「少配了个环境变量」。所以两条入口都要在碰 `_aclnn_cfg()` 之前先 BLOCK。
    """
    # 形态先硬化（含「空 object 仍按 pull_request 向后兼容」的边界），再判来源。
    bindings = _require_bindings(preflight)
    kind = dut_source_kind.of(bindings, where="aclnn_preflight.bindings")
    if kind != dut_source_kind.PULL_REQUEST:
        raise ValueError(
            f"aclnn_py 真机 harness 信任门尚未接入 dut_source={kind}："
            f"aclnn_adapter 只能按 PR ref 在容器内重新取源 build，"
            f"构建端没有可与 local_checkout.root_digest 对账的锚 → fail-closed")
    return kind


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


def _artifact_file(root, work_rel):
    if not isinstance(work_rel, str) or not work_rel:
        raise ValueError(f"case 数据路径须为 work/ 下非空相对路径，得 {work_rel!r}")
    rel = "work/" + work_rel.replace("/", os.sep)
    return content_address.safe_path(root, rel), rel.replace(os.sep, "/")


def _selected_data_manifest(root, selected):
    """绑定见证输入与 CPU golden 的真实字节，而不只绑定路径型 caseset。"""
    records, seen = [], set()
    for case in selected:
        cid = case["id"]
        for inp in case.get("inputs") or []:
            path, rel = _artifact_file(root, inp.get("path"))
            key = ("input", rel)
            if key in seen:
                raise ValueError(f"{cid}: 重复输入数据路径 {rel!r}")
            seen.add(key)
            records.append({
                "kind": "input", "case_id": cid, "name": inp.get("name"),
                "path": rel, "size": os.path.getsize(path),
                "sha256": _file_sha(path),
            })
        expected = case.get("expected") or {}
        outputs = expected.get("outputs")
        if isinstance(outputs, list):
            golden_items = [
                (i, out.get("name"), out.get("golden_path"))
                for i, out in enumerate(outputs)
            ]
        else:
            golden_items = [(0, None, expected.get("golden_path"))]
        for index, name, golden_rel in golden_items:
            path, rel = _artifact_file(root, golden_rel)
            key = ("golden", rel)
            if key in seen:
                raise ValueError(f"{cid}: 重复 golden 数据路径 {rel!r}")
            seen.add(key)
            records.append({
                "kind": "golden", "case_id": cid, "index": index,
                "name": name, "path": rel, "size": os.path.getsize(path),
                "sha256": _file_sha(path),
            })
    return sorted(records, key=lambda item: (
        item["case_id"], item["kind"], item.get("index", -1),
        item.get("name") or "", item["path"]))


def _golden_source_binding(spec):
    path = os.path.join(repo_adapter.op_dir(spec["op"]), "golden.py")
    return {"path_role": "OPRUNWAY_OPS_DIR/<op>/golden.py",
            "sha256": _file_sha(path)}


def _probe_runtime_environment(cfg):
    """只读探测 CP-D 当前 toolkit/version；与 build 脚本共用 oprw_setenv 守卫。"""
    script = aclnn_adapter._SH_GUARDS + (
        "oprw_setenv " + shlex.quote(cfg["setenv"]) + "\n")
    result = repo_adapter._shell(
        cfg["host"], script, timeout=120, check=False, capture=True)
    blob = (result.stdout or "") + (result.stderr or "")
    match = re.search(r"OPRUNWAY_ACLNN_ENV toolkit=(\S+) tkver=(\S+)", blob)
    if result.returncode != 0 or not match:
        raise RuntimeError(
            f"无法只读绑定当前 CANN toolkit 环境 rc={result.returncode}: "
            f"{blob[-1000:]}")
    return {"toolkit": match.group(1), "toolkit_version": match.group(2)}


def _execution_binding(cfg, caseset):
    public = {
        key: cfg[key] for key in (
            "target", "op_subdir", "vendor_name", "base_repo", "pr_ref",
            "head_sha", "soc", "snake_op", "device")
    }
    public["build_args"] = aclnn_adapter._build_args(cfg)
    public["symbols"] = list(aclnn_adapter._required_symbols(caseset))
    public["reuse_build"] = bool(aclnn_adapter._reuse_build(cfg))
    private_target = {
        key: cfg.get(key) for key in (
            "host", "rroot", "vendor_dir", "setenv", "ops_root", "op_dir",
            "proxy")
    }
    return {
        "config": public,
        # 私有主机名/路径不落工件，只以域分离摘要绑定同一执行目标。
        "target_digest": content_address.content_digest(
            "oprunway/aclnn-execution-target/v1", private_target),
        "runtime": _probe_runtime_environment(cfg),
    }


def _current_execution_binding(caseset):
    return _execution_binding(aclnn_adapter._aclnn_cfg(), caseset)


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
    """返回确定性小见证集及覆盖说明；不可覆盖时 fail-closed。"""
    cases = caseset.get("cases")
    variants = preflight.get("variants")
    if not isinstance(cases, list) or not cases:
        raise ValueError("caseset.cases 须为非空 array")
    if not isinstance(variants, list) or not variants:
        raise ValueError("preflight.variants 须为非空 array")

    # 见证的是**本轮实际要执行的输入 dtype**，不是任务书全集。dtype_required 可包含按
    # `dtype_unsupported_by_op_def` 正式挂账的差额；把它硬塞进 harness 门会篡改既有
    # passed_with_gaps 策略。直接从完整 caseset 实际输入派生，且与 dtype_tested 交叉核。
    required_dtypes = sorted({
        inp.get("dtype")
        for case in cases for inp in (case.get("inputs") or [])
        if isinstance(inp, dict) and isinstance(inp.get("dtype"), str)
    })
    if not required_dtypes:
        raise ValueError("caseset 无实际输入 dtype，无法建立 harness 见证")
    declared_tested = caseset.get("dtype_tested")
    if isinstance(declared_tested, list):
        bad = [x for x in declared_tested if not isinstance(x, str) or not x]
        if bad or not set(declared_tested).issubset(set(required_dtypes)):
            raise ValueError(
                f"caseset.dtype_tested={declared_tested!r} 与实际输入 dtype "
                f"{required_dtypes!r} 不一致")

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
                "golden_path": out.get("golden_path"),
                "out_path": out.get("out_path"),
                "golden_sha256": (out.get("provenance") or {}).get("golden_sha256"),
                "out_sha256": (out.get("provenance") or {}).get("out_sha256"),
            })
        checks.append({"case_id": cid, "result": "pass", "outputs": output_checks})
    return checks


def _receipt_bindings(root, spec, caseset, preflight, selected, execution):
    return {
        "spec_sha256": _sha(spec),
        "caseset_sha256": _sha(caseset),
        "preflight_digest": content_address.content_digest(
            _PREFLIGHT_DOMAIN, preflight),
        # 此处只可能是 PR 锚：本地通路已在 `run_gate` / `validate_receipt` /
        # `_validate_build_provenance` 三处前置 BLOCK，走不到这里。
        "pr_head_sha": (preflight.get("bindings") or {}).get("pr_head_sha"),
        "logic_files": _logic_hashes(),
        "golden_source": _golden_source_binding(spec),
        "selected_data": _selected_data_manifest(root, selected),
        "execution": execution,
    }


def _expected_output_contracts(case):
    expected = case.get("expected") or {}
    outputs = expected.get("outputs")
    if isinstance(outputs, list):
        return [{
            "index": index,
            "name": out.get("name"),
            "role": out.get("role"),
            "policy_kind": (out.get("policy") or {}).get("kind"),
            "golden_path": out.get("golden_path"),
        } for index, out in enumerate(outputs)]
    return [{
        "index": 0,
        "name": None,
        "role": None,
        "policy_kind": (expected.get("policy") or {}).get("kind"),
        "golden_path": expected.get("golden_path"),
    }]


def _validate_build_provenance(provenance, execution, preflight):
    # 通路门（兜底的那一处）：本函数是 run_gate 与 validate_receipt 的**共同**必经点，
    # 下面整段构建对账都以「锚 = PR head」为前提，非 PR 通路一个字段都对不上。
    _require_pull_request_path(preflight)
    if not isinstance(provenance, dict):
        raise ValueError("harness trust receipt.build_provenance 缺失")
    cfg = execution["config"]
    runtime = execution["runtime"]
    expected = {
        "head_sha": cfg["head_sha"],
        "pr_ref": cfg["pr_ref"],
        "base_repo": cfg["base_repo"],
        "op_subdir": cfg["op_subdir"],
        "snake_op": cfg["snake_op"],
        "soc": cfg["soc"],
        "vendor_name": cfg["vendor_name"],
        "build_args": cfg["build_args"],
        "symbols": cfg["symbols"],
        "toolkit": runtime["toolkit"],
        "toolkit_version": runtime["toolkit_version"],
    }
    for key, value in expected.items():
        if provenance.get(key) != value:
            raise ValueError(
                f"harness trust build_provenance.{key} 与当前执行环境不一致")
    if provenance.get("head_sha") != (
            (preflight.get("bindings") or {}).get("pr_head_sha")):
        raise ValueError("harness trust build head 与 CP-C0 PR head 不一致")
    for key in ("build_reused", "stamp_mismatch_rebuilt",
                "so_digest_unavailable"):
        if not isinstance(provenance.get(key), bool):
            raise ValueError(
                f"harness trust build_provenance.{key} 须为 bool")


def _validate_checks(root, selected, checks):
    selected_by_id = {case["id"]: case for case in selected}
    if not isinstance(checks, list) or len(checks) != len(selected):
        raise ValueError("harness trust receipt 对拍检查数量与见证集不一致")
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("harness trust receipt.checks 元素须为 object")
        cid = check.get("case_id")
        if cid not in selected_by_id or cid in seen:
            raise ValueError(
                f"harness trust receipt check case 缺失/重复/越界: {cid!r}")
        seen.add(cid)
        if check.get("result") != "pass":
            raise ValueError(f"{cid}: harness trust check 未通过")
        actual_outputs = check.get("outputs")
        expected_outputs = _expected_output_contracts(selected_by_id[cid])
        if not isinstance(actual_outputs, list) or not actual_outputs:
            raise ValueError(f"{cid}: harness trust check.outputs 须为非空 array")
        if len(actual_outputs) != len(expected_outputs):
            raise ValueError(f"{cid}: harness trust 输出数量与 caseset 不一致")
        for actual, expected in zip(actual_outputs, expected_outputs):
            if not isinstance(actual, dict):
                raise ValueError(f"{cid}: harness trust output check 非 object")
            for key in ("index", "name", "role", "policy_kind",
                        "golden_path"):
                if actual.get(key) != expected[key]:
                    raise ValueError(
                        f"{cid}: harness trust output.{key} 与 caseset 不一致")
            if actual.get("result") != "pass":
                raise ValueError(f"{cid}: harness trust output 未通过")
            out_path = actual.get("out_path")
            if not isinstance(out_path, str) or not out_path.startswith(
                    "aclnn_trust_out/" + cid + "/"):
                raise ValueError(f"{cid}: harness trust out_path 非见证输出目录")
            for path_key, sha_key in (
                    ("golden_path", "golden_sha256"),
                    ("out_path", "out_sha256")):
                path, _ = _artifact_file(root, actual.get(path_key))
                claimed = actual.get(sha_key)
                if not isinstance(claimed, str) or not _HEX64.fullmatch(claimed):
                    raise ValueError(f"{cid}: {sha_key} 非小写 SHA256")
                if _file_sha(path) != claimed:
                    raise ValueError(f"{cid}: {path_key} 实际字节已漂移")
    if seen != set(selected_by_id):
        raise ValueError("harness trust receipt 对拍检查未覆盖完整见证集")


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
    # 通路门（入口处）：必须赶在 `_current_execution_binding` 之前——它会调
    # `aclnn_adapter._aclnn_cfg()`，本地通路会先撞上「PR head 引用必填」这个误导性报错，
    # 而 `_validate_build_provenance` 里的同一道门要到本函数最后才触发。
    _require_pull_request_path(preflight)
    bindings = receipt.get("bindings")
    if not isinstance(bindings, dict):
        raise ValueError("harness trust receipt.bindings 缺失")
    selected, expected_coverage = select_cases(caseset, preflight)
    execution = _current_execution_binding(caseset)
    expected = _receipt_bindings(
        root, spec, caseset, preflight, selected, execution)
    for key, value in expected.items():
        if bindings.get(key) != value:
            raise ValueError(f"harness trust receipt {key} 已漂移")
    coverage = receipt.get("coverage")
    if coverage != expected_coverage:
        raise ValueError("harness trust receipt.coverage 与确定性见证选择不一致")
    _validate_build_provenance(
        receipt.get("build_provenance"), execution, preflight)
    _validate_checks(root, selected, receipt.get("checks"))
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
    # ⚠ 这是本函数里对 `bindings` 的**第一次**触碰，排在通路门之前，所以形态要在这里就硬化：
    #   `or {}` 下 None/`[]` 会报「与当前 spec 不绑定」（fail-closed，但把「形态判不出来」
    #   说成「绑错了 spec」），非空字符串 / 非空 list 则直接让 `.get` 抛 AttributeError——
    #   裸 traceback，不在调用方的收敛清单里。走同一套显式形态校验，报同一个错。
    if _require_bindings(preflight).get("spec_sha256") != _sha(spec):
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

    # 通路门（入口处）：必须赶在 `_aclnn_cfg()` 之前，理由同 `validate_receipt`。
    _require_pull_request_path(preflight)
    cfg = aclnn_adapter._aclnn_cfg()
    execution = _execution_binding(cfg, caseset)
    preflight_head = (preflight.get("bindings") or {}).get("pr_head_sha")
    # ⚠ 形态先硬化再交叉核：下面那条等值比较本身不挑形态，缺席/畸形时它只是「两边一样地
    #   畸形」就放行了。今天没出事纯粹是靠 `cfg.head_sha` 必是 40-hex 间接兜住——那是偶然的
    #   fail-closed，不是设计。CP-C0 没绑定合法 PR head 就没有可交叉核的锚，直接停。
    if not isinstance(preflight_head, str) or not _HEX40.fullmatch(preflight_head):
        raise ValueError(
            f"CP-C0 preflight 未绑定 40 位 PR head，无法交叉核：{preflight_head!r}")
    if cfg.get("head_sha") != preflight_head:
        raise ValueError(
            "真机配置 head_sha 与 CP-C0 已绑定的 PR head 不一致")
    proj = aclnn_adapter.find_aclnn_project(
        spec["op"], cfg["ops_root"], cfg["op_subdir"])
    out_dir = os.path.join(work, "aclnn_trust_out")
    provenance = aclnn_adapter._run_aclnn_real(
        cfg, proj, witness, work, out_dir)
    evidence = repo_adapter.build_multi_output_evidence(
        witness, work, out_dir)
    checks = _judge_evidence(selected, evidence)
    _validate_build_provenance(provenance, execution, preflight)
    payload = {
        "schema": _SCHEMA,
        "schema_version": 1,
        "status": _STATUS_TRUSTED,
        "scope": "harness-only",
        "acceptance_verdict": None,
        "bindings": _receipt_bindings(
            root, spec, caseset, preflight, selected, execution),
        "coverage": coverage,
        "checks": checks,
        "build_provenance": provenance,
        "note": (
            "仅证明通用 aclnn_py harness 对当前 PR 签名、dtype 与 CPU golden 的确定性小见证；"
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
