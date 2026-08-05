"""CP-F Task-2-only 精度重测执行层。

只消费已准备完成的 attempt；不生成 case、不执行性能、不修改首次验收目录。
判定复用 repo_adapter → validator → validate_acceptance_state.gate_task2。
"""

import copy
import datetime
import json
import os
import re
import shutil

import content_address
import cpp_extension_adapter
import dut_source
import precision_policy
import precision_retest_contract as contract
import repo_adapter
import run_workflow
import validate_acceptance_state
import validator
import verify_aclnn_harness
import render_precision_retest_markdown


class RetestExecutionError(RuntimeError):
    """CP-F attempt 无法安全执行或完成。"""


def _read_envelope(path, expected_domain):
    value = contract.load_strict_json(path, os.path.basename(path))
    if value.get("schema_version") != 1 or value.get("domain") != expected_domain:
        raise RetestExecutionError(
            f"{os.path.basename(path)} envelope schema/domain 不匹配")
    payload = value.get("payload")
    actual = content_address.content_digest(expected_domain, payload)
    if value.get("digest") != actual:
        raise RetestExecutionError(f"{os.path.basename(path)} 摘要不匹配")
    if not isinstance(payload, dict):
        raise RetestExecutionError(f"{os.path.basename(path)} payload 须为对象")
    return value, payload


def selected_caseset(base_caseset, planned_case_ids):
    """按 manifest 顺序冻结原 case 子集，不改变 case 内容。"""
    if not isinstance(base_caseset, dict):
        raise RetestExecutionError("base caseset 须为对象")
    cases = base_caseset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise RetestExecutionError("base caseset.cases 须为非空列表")
    by_id = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise RetestExecutionError("base caseset 含缺/坏 case id")
        if case["id"] in by_id:
            raise RetestExecutionError(f"base caseset 重复 case id={case['id']!r}")
        by_id[case["id"]] = case
    if (not isinstance(planned_case_ids, list) or not planned_case_ids
            or len(planned_case_ids) != len(set(planned_case_ids))):
        raise RetestExecutionError("manifest.planned_case_ids 非法")
    missing = [cid for cid in planned_case_ids if cid not in by_id]
    if missing:
        raise RetestExecutionError(f"manifest case 不在 base caseset: {missing}")
    subset = copy.deepcopy(base_caseset)
    subset["cases"] = [copy.deepcopy(by_id[cid]) for cid in planned_case_ids]
    subset["precision_retest_scope"] = {
        "kind": "selected_original_cases",
        "planned_case_ids": list(planned_case_ids),
        "base_case_count": len(cases),
        "selected_case_count": len(planned_case_ids),
    }
    return subset


def _case_file_paths(case):
    paths = []
    for inp in case.get("inputs") or []:
        if isinstance(inp, dict) and isinstance(inp.get("path"), str):
            paths.append(inp["path"])
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    if isinstance(expected.get("golden_path"), str):
        paths.append(expected["golden_path"])
    outputs = expected.get("outputs")
    if isinstance(outputs, list):
        for output in outputs:
            if isinstance(output, dict) and isinstance(output.get("golden_path"), str):
                paths.append(output["golden_path"])
    return paths


def copy_selected_case_files(subset, base_work, attempt_work):
    """复制 runner/validator 所需输入与 golden；拒绝路径逃逸和符号链接。"""
    os.makedirs(attempt_work, exist_ok=True)
    copied = []
    seen = set()
    for case in subset.get("cases") or []:
        for relative in _case_file_paths(case):
            if relative in seen:
                continue
            seen.add(relative)
            try:
                source = content_address.safe_path(base_work, relative)
                target = content_address.safe_path(attempt_work, relative)
            except content_address.ContentAddressError as ex:
                raise RetestExecutionError(
                    f"{case.get('id')}: case 文件路径非法 {relative!r}: {ex}") from ex
            if os.path.islink(source) or not os.path.isfile(source):
                raise RetestExecutionError(
                    f"{case.get('id')}: case 文件缺失或为符号链接 {relative!r}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target)
            if contract.sha256_file(source) != contract.sha256_file(target):
                raise RetestExecutionError(
                    f"{case.get('id')}: 复制后字节摘要不一致 {relative!r}")
            copied.append(relative)
    if not copied:
        raise RetestExecutionError("选中 case 没有可复制的输入/golden 文件")
    return copied


def _verify_manifest_case_bindings(manifest, subset, attempt_work):
    bindings = manifest.get("case_bindings")
    if not isinstance(bindings, dict):
        raise RetestExecutionError("manifest.case_bindings 缺失")
    for case in subset["cases"]:
        cid = case["id"]
        binding = bindings.get(cid)
        if not isinstance(binding, dict):
            raise RetestExecutionError(f"{cid}: manifest 缺 case binding")
        digest = content_address.content_digest(
            "oprunway/precision-retest-case/v1", case)
        if binding.get("case_digest") != digest:
            raise RetestExecutionError(f"{cid}: case 结构已漂移")
        inputs = binding.get("input_sha256")
        if not isinstance(inputs, dict):
            raise RetestExecutionError(f"{cid}: manifest 缺 input hashes")
        for inp in case.get("inputs") or []:
            name, relative = inp.get("name"), inp.get("path")
            try:
                actual = contract.sha256_file(
                    content_address.safe_path(attempt_work, relative))
            except (contract.RetestContractError,
                    content_address.ContentAddressError) as ex:
                raise RetestExecutionError(f"{cid}.{name}: 无法复核输入: {ex}") from ex
            if inputs.get(name) != actual:
                raise RetestExecutionError(
                    f"{cid}.{name}: input bytes 与 manifest 不一致")
        goldens = binding.get("golden_sha256")
        if not isinstance(goldens, dict) or not goldens:
            raise RetestExecutionError(f"{cid}: manifest 缺 golden hashes")
        actual_paths = set(_case_file_paths(case))
        input_paths = {
            inp.get("path") for inp in case.get("inputs") or []
            if isinstance(inp, dict)
        }
        expected_golden_paths = actual_paths - input_paths
        if set(goldens) != expected_golden_paths:
            raise RetestExecutionError(
                f"{cid}: manifest golden 路径集合与 case 不一致")
        for relative, expected in goldens.items():
            try:
                actual = contract.sha256_file(
                    content_address.safe_path(attempt_work, relative))
            except (contract.RetestContractError,
                    content_address.ContentAddressError) as ex:
                raise RetestExecutionError(
                    f"{cid}: 无法复核 golden {relative!r}: {ex}") from ex
            if actual != expected:
                raise RetestExecutionError(
                    f"{cid}: golden bytes 与 manifest 不一致 {relative!r}")


def prepare_execution_caseset(base_caseset, manifest, effective_spec,
                              attempt_kind, base_work, attempt_work):
    """先验证冻结的原 case，再派生本轮生效的 acceptance policy。"""
    subset = selected_caseset(
        base_caseset, manifest.get("planned_case_ids"))
    copied = copy_selected_case_files(subset, base_work, attempt_work)
    # case_digest 绑定 F2 时的原 case。relaxed policy 是通过原身份门后
    # 才产生的受控执行视图，不能拿它反向对比原 case_digest。
    _verify_manifest_case_bindings(manifest, subset, attempt_work)
    if attempt_kind == "relaxed_rerun":
        subset = rebind_acceptance_policy(subset, effective_spec)
    return subset, copied


def _strict_work_json(work, relative):
    try:
        return contract.load_strict_json(
            content_address.safe_path(work, relative), relative)
    except (OSError, contract.RetestContractError,
            content_address.ContentAddressError) as ex:
        raise RetestExecutionError(f"无法读取 {relative}: {ex}") from ex


def _validate_cpp_extension_fresh_receipt(
        receipt, manifest, directive, generated_plan, generated_manifest):
    """把 fresh build/load/vendor receipt 回绑基础身份与冻结 invocation。"""
    binding = manifest.get("runner_binding")
    if (not isinstance(binding, dict)
            or binding.get("schema")
            != "oprunway.precision_retest.cpp_extension_binding"
            or binding.get("schema_version") != 1):
        raise RetestExecutionError(
            "cpp_extension attempt 缺受控 runner_binding")
    if generated_plan.get("cases") != binding.get("selected_invocations"):
        raise RetestExecutionError(
            "fresh invocation plan 与首次冻结调用序列漂移")
    generated_manifest_sha = cpp_extension_adapter._canonical_sha(
        generated_manifest)
    if (generated_manifest_sha != binding.get("base_manifest_sha256")
            or generated_manifest.get("spec_sha256")
            != binding.get("base_spec_sha256")
            or generated_manifest.get("namespace")
            != binding.get("base_namespace")
            or generated_plan.get("manifest_sha256")
            != generated_manifest_sha
            or generated_plan.get("namespace")
            != binding.get("base_namespace")):
        raise RetestExecutionError(
            "fresh Extension manifest/spec/namespace 与首次绑定漂移")
    receipt_bindings = receipt.get("bindings")
    receipt_load = receipt.get("load")
    if (not isinstance(receipt_bindings, dict)
            or receipt_bindings.get("manifest_sha256") != generated_manifest_sha
            or receipt_bindings.get("spec_sha256")
            != generated_manifest.get("spec_sha256")
            or receipt_bindings.get("invocation_plan_sha256")
            != cpp_extension_adapter._canonical_sha(generated_plan)
            or not isinstance(receipt_load, dict)
            or receipt_load.get("namespace") != generated_manifest.get("namespace")):
        raise RetestExecutionError(
            "fresh receipt bindings/load 未绑定实际 manifest/spec/invocation/namespace")
    vendor = receipt.get("vendor")
    build_receipt = vendor.get("build_receipt") if isinstance(vendor, dict) else None
    source = build_receipt.get("source") if isinstance(build_receipt, dict) else None
    runtime = receipt.get("runtime")
    identity = manifest.get("execution_identity")
    # fresh 收据是真机本轮刚产的，是整条链上**最有条件伪装**的一份：所以来源校验必须带
    # `expected_kind`。少了它，收据只要改口说 `pull_request` 并填一个任意 40 位 hex，
    # 本地锚的等值校验整条就不会执行。校验只此一处，锚字段名从返回值取，不按字面拼 key。
    try:
        directive_kind, _anchor_field, directive_anchor = (
            dut_source.validate_build_receipt_source(
                directive["source_identity"], where="directive.source_identity"))
    except (dut_source.DutSourceError, KeyError, TypeError) as ex:
        raise RetestExecutionError(
            f"directive.source_identity 来源锚不可信：{ex}") from ex
    try:
        fresh_kind, fresh_anchor_field, fresh_anchor = (
            dut_source.validate_build_receipt_source(
                source, expected_kind=directive_kind,
                where="fresh cpp_extension vendor build_receipt.source"))
    except dut_source.DutSourceError as ex:
        raise RetestExecutionError(
            f"fresh cpp_extension vendor build receipt 来源锚不可信：{ex}") from ex
    expected = {
        "source_anchor": directive_anchor,
        "vendor_elf": identity.get("vendor_elf_sha256")
            if isinstance(identity, dict) else None,
        "soc": identity.get("soc") if isinstance(identity, dict) else None,
        "toolkit": identity.get("toolkit") if isinstance(identity, dict) else None,
    }
    actual = {
        # 已按通路核过长度的锚值，不是裸 `.get`：收据是 local 时裸 get 拿到 None、
        # 是「64 位假 pr_head_sha」时裸 get 会原样收下。
        "source_anchor": fresh_anchor,
        "vendor_elf": vendor.get("library_sha256")
            if isinstance(vendor, dict) else None,
        "soc": runtime.get("soc") if isinstance(runtime, dict) else None,
        "toolkit": runtime.get("cann_version")
            if isinstance(runtime, dict) else None,
    }
    for field, wanted in expected.items():
        if actual[field] != wanted:
            raise RetestExecutionError(
                f"fresh cpp_extension {field} 身份漂移："
                f"actual={actual[field]!r} expected={wanted!r}")
    # 整块比三元组，**不只比锚值**：同一段 hex 在两条通路里含义完全不同（线上 commit
    # vs 本地子树摘要），只比值等于没比通路。旧 manifest 只有 `base_pr_head`、没有
    # `base_source_identity` → 这里直接不相等 → 拒执行；刻意不留旧键兼容兜底，
    # 那正是本批刚堵掉的「有值就用」。
    fresh_identity = {
        "dut_source": fresh_kind,
        "anchor_field": fresh_anchor_field,
        "anchor_value": fresh_anchor,
    }
    if binding.get("base_source_identity") != fresh_identity:
        raise RetestExecutionError(
            f"cpp_extension 基础 receipt 来源身份与本轮漂移："
            f"base={binding.get('base_source_identity')!r} fresh={fresh_identity!r}")
    if binding.get("base_vendor_elf_sha256") != expected["vendor_elf"] \
            or binding.get("base_soc") != expected["soc"] \
            or binding.get("base_toolkit") != expected["toolkit"] \
            or binding.get("base_build_receipt_sha256") \
            != directive["source_identity"]["build_receipt_sha256"]:
        raise RetestExecutionError(
            "cpp_extension 基础 receipt 身份与 directive/execution identity 漂移")
    fresh_build = build_receipt.get("build") if isinstance(build_receipt, dict) else None
    if (not isinstance(fresh_build, dict)
            or fresh_build.get("argv") != binding.get("base_vendor_build_argv")
            or source.get("repo") != binding.get("base_source_repo")):
        raise RetestExecutionError(
            "fresh vendor build argv/source repo 与首次 receipt 漂移")
    return {
        "base_receipt_sha256": binding["base_receipt_sha256"],
        "fresh_receipt_sha256": cpp_extension_adapter._canonical_sha(receipt),
        "base_invocation_plan_sha256":
            binding["base_invocation_plan_sha256"],
        "fresh_invocation_plan_sha256":
            cpp_extension_adapter._canonical_sha(generated_plan),
        "fresh_vendor_build_receipt_sha256":
            vendor.get("build_receipt_sha256"),
        "fresh_extension_elf_sha256":
            (receipt.get("artifact") or {}).get("sha256"),
    }


def _run_cpp_extension_task2_only(
        base_spec, subset, attempt_work, manifest, directive):
    """复用正式 adapter/driver 做 fresh build/load/invoke，禁止性能阶段。"""
    try:
        cpp_extension_adapter.prepare(base_spec, subset, attempt_work)
    except (OSError, RuntimeError, TypeError, ValueError) as ex:
        raise RetestExecutionError(
            f"cpp_extension Task-2-only prepare 失败: {ex}") from ex
    generated_plan = _strict_work_json(
        attempt_work, "cpp_extension_invocation_plan.json")
    generated_manifest = _strict_work_json(
        attempt_work, "cpp_extension/extension_manifest.json")
    binding = manifest.get("runner_binding")
    if not isinstance(binding, dict) \
            or generated_plan.get("cases") != binding.get("selected_invocations"):
        raise RetestExecutionError(
            "cpp_extension 生成 invocation 与首次冻结调用序列漂移")
    try:
        evidence = cpp_extension_adapter.run_cpp_extension_precision_only(
            subset, attempt_work)
    except (OSError, RuntimeError, TypeError, ValueError) as ex:
        raise RetestExecutionError(
            f"cpp_extension Task-2-only 执行失败: {ex}") from ex
    for forbidden in ("cpp_extension_perf_plan.json",
                      "cpp_extension_perf_collect.json"):
        if os.path.lexists(os.path.join(attempt_work, forbidden)):
            raise RetestExecutionError(
                f"Task-2-only 非法产生性能工件 {forbidden}")
    receipt = evidence.get("cpp_extension_receipt")
    execution = _validate_cpp_extension_fresh_receipt(
        receipt, manifest, directive, generated_plan, generated_manifest)
    evidence["precision_retest_execution"] = execution
    return evidence


def _write_json(root, name, value):
    return content_address.atomic_write_json(root, name, value)


def _frozen_source_facts_path(attempt, manifest, directive):
    """复核 F2 冻进 attempt 的 `source_facts.json`，返回给三级门的路径。

    没有这条线，本地来源通路的 CP-F 执行是**结构性**必 BLOCKED：
    `gate_task2` → `_gate_build_receipt_source_binding` 在 `local_checkout` 且找不到
    `source_facts.json` 时按设计阻断，而 attempt 目录只复制 case 输入与 golden，
    这份文件永远不会出现。F2 冻结 + 这里指路，才让本地锚有对照物可核。

    manifest 缺 `source_facts` 时按 directive 声明的通路分：PR 返回 `None`，门沿用既有
    PR 行为一个字节不变；本地则当场拒。**不能**只依赖「F2 已经 fail-closed 过」——
    directive.json 与 attempt.manifest.json 都是自洽 envelope，手搓一对声明
    `local_checkout` 却不带 `source_facts` 的工件，就绕过了 F2 那道门。

    只比 sha256，不在这里重判来源：manifest 是内容寻址 envelope 且已在
    `_read_envelope` 校过摘要，manifest 里的 sha256 又绑死了这份文件的内容，
    来源三元组在 F2 已对着 directive 核过。执行侧再判一次只会多出第二处判别逻辑。
    """
    try:
        kind = dut_source.of(
            directive.get("source_identity"), where="directive.source_identity")
    except dut_source.DutSourceError as ex:
        raise RetestExecutionError(
            f"directive.source_identity 来源判别式不合法：{ex}") from ex
    recorded = manifest.get("source_facts")
    if recorded is None:
        if kind == dut_source.LOCAL_CHECKOUT:
            raise RetestExecutionError(
                f"dut_source={kind} 的 attempt 必须带 F2 冻结的 source_facts.json，"
                f"manifest 里却没有——本地锚没有对照物即无绑定，拒绝执行")
        return None
    if not isinstance(recorded, dict):
        raise RetestExecutionError("manifest.source_facts 非法")
    try:
        path = content_address.safe_path(
            attempt, recorded.get("attempt_relpath"))
    except content_address.ContentAddressError as ex:
        raise RetestExecutionError(
            f"manifest.source_facts.attempt_relpath 非法: {ex}") from ex
    try:
        actual = contract.sha256_file(path)
    except contract.RetestContractError as ex:
        raise RetestExecutionError(
            f"attempt 冻结的 source_facts.json 缺失或不可读: {ex}") from ex
    if actual != recorded.get("sha256"):
        raise RetestExecutionError(
            f"attempt 冻结的 source_facts.json 字节漂移："
            f"actual={actual} manifest={recorded.get('sha256')!r}")
    return path


def rebind_acceptance_policy(caseset, relaxed_spec):
    """只重绑 acceptance 层；standard/oracle/case/input/golden 全部保持原样。"""
    rebound = copy.deepcopy(caseset)
    changed_outputs = 0
    for case in rebound.get("cases") or []:
        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise RetestExecutionError(f"{case.get('id')}: expected 缺失")
        outputs = expected.get("outputs")
        if isinstance(outputs, list):
            contracts = []
            for output in outputs:
                if not isinstance(output, dict):
                    raise RetestExecutionError(
                        f"{case.get('id')}: expected.outputs 含非对象")
                contracts.append({
                    "name": output.get("name"),
                    "role": output.get("role"),
                    "dtype": output.get("compare_dtype"),
                    "standard": output.get("standard"),
                    "tolerance_policy_id": output.get("tolerance_policy_id"),
                    "policy": copy.deepcopy(output.get("policy")),
                    "index_of": output.get("index_of"),
                })
            try:
                acceptance = precision_policy.derive_acceptance_contracts(
                    relaxed_spec, contracts)
            except (KeyError, TypeError, ValueError) as ex:
                raise RetestExecutionError(
                    f"{case.get('id')}: 无法据 relaxed spec 派生多输出 acceptance: {ex}") from ex
            if acceptance is None or len(acceptance) != len(outputs):
                raise RetestExecutionError(
                    f"{case.get('id')}: relaxed spec 未产生完整多输出 acceptance")
            for output, acc in zip(outputs, acceptance):
                before = copy.deepcopy(
                    output.get("acceptance_policy", output.get("policy")))
                for key in ("acceptance_policy", "acceptance_tolerance_policy_id"):
                    output.pop(key, None)
                if acc is not None:
                    output["acceptance_policy"] = acc["policy"]
                    output["acceptance_tolerance_policy_id"] = acc[
                        "tolerance_policy_id"]
                    changed_outputs += int(acc["policy"] != before)
            continue
        standard, dtype = expected.get("standard"), expected.get("compare_dtype")
        if not isinstance(standard, str) or not isinstance(dtype, str):
            # compare=na 的真空功能 case 没有数值 acceptance，可原样保留。
            if expected.get("compare") == "na":
                continue
            raise RetestExecutionError(
                f"{case.get('id')}: expected 缺 standard/compare_dtype")
        try:
            acceptance = precision_policy.resolve_acceptance(
                relaxed_spec, standard, dtype)
        except (KeyError, TypeError, ValueError) as ex:
            raise RetestExecutionError(
                f"{case.get('id')}: 无法据 relaxed spec 派生 acceptance: {ex}") from ex
        before = copy.deepcopy(
            expected.get("acceptance_policy", expected.get("policy")))
        for key in ("acceptance_policy", "acceptance_tolerance_policy_id"):
            expected.pop(key, None)
        if acceptance is None:
            raise RetestExecutionError(
                f"{case.get('id')}: relaxed policy 对 standard={standard!r} "
                f"dtype={dtype!r} 不生效，拒绝伪 relaxed attempt")
        expected["acceptance_policy"] = acceptance[0]
        expected["acceptance_tolerance_policy_id"] = acceptance[1]
        changed_outputs += int(acceptance[0] != before)
    if changed_outputs == 0:
        raise RetestExecutionError(
            "relaxed policy 未改变任何可裁数值输出，拒绝伪 relaxed attempt")
    return rebound


def _execute_precision_attempt_locked(attempt_dir):
    """执行 same-policy/relaxed Task-2-only 精度重测；需要真实 runner 环境。"""
    attempt = os.path.realpath(os.fspath(attempt_dir))
    if not os.path.isdir(attempt) or os.path.islink(attempt_dir):
        raise RetestExecutionError("attempt_dir 须为真实目录且非符号链接")
    receipt_path = os.path.join(attempt, "attempt.receipt.json")
    if os.path.lexists(receipt_path):
        raise RetestExecutionError(
            "attempt 已有完成收据，属于不可变历史；请创建新的 attempt")
    _, directive = _read_envelope(
        os.path.join(attempt, "directive.json"),
        "oprunway/precision-retest-directive/v1")
    _, manifest = _read_envelope(
        os.path.join(attempt, "attempt.manifest.json"),
        "oprunway/precision-retest-manifest/v1")
    directive = contract.validate_directive(
        directive, require_confirmed=True)
    if directive["attempt_kind"] not in ("same_policy_rerun", "relaxed_rerun"):
        raise RetestExecutionError(
            "当前执行入口只支持 same_policy_rerun/relaxed_rerun；replay 尚未接线")
    if manifest.get("directive_id") != directive["directive_id"]:
        raise RetestExecutionError("manifest.directive_id 与 directive 不一致")
    base = manifest.get("base_artifacts")
    if not isinstance(base, dict):
        raise RetestExecutionError("manifest.base_artifacts 缺失")
    if base != directive["base_artifacts"]:
        raise RetestExecutionError(
            "manifest.base_artifacts 与 confirmed directive 不一致")
    # 冻结事实的复核放在 NPU invoke 之前：来源对照物已被换过的 attempt 不值得再跑一遍。
    source_facts_path = _frozen_source_facts_path(attempt, manifest, directive)
    base_reports = os.path.dirname(base["caseset"]["path"])
    artifact_scope = manifest.get("base_artifact_scope")
    if not isinstance(artifact_scope, str) or not os.path.isabs(artifact_scope):
        raise RetestExecutionError("manifest.base_artifact_scope 缺失或非法")
    try:
        contract.verify_base_artifacts(directive, artifact_scope)
    except contract.RetestContractError as ex:
        raise RetestExecutionError(f"基础验收工件复核失败: {ex}") from ex
    spec = contract.load_strict_json(base["spec"]["path"], "base spec")
    base_caseset = contract.load_strict_json(
        base["caseset"]["path"], "base caseset")
    effective_spec = spec
    if directive["attempt_kind"] == "relaxed_rerun":
        _, effective_spec = _read_envelope(
            os.path.join(attempt, "spec.relaxed.json"),
            "oprunway/precision-retest-relaxed-spec/v1")
        if ((effective_spec.get("precision_retest") or {}).get("directive_id")
                != directive["directive_id"]):
            raise RetestExecutionError(
                "relaxed spec 未绑定当前 directive_id")
    base_work = os.path.join(base_reports, "work")
    attempt_work = os.path.join(attempt, "work")
    subset, copied = prepare_execution_caseset(
        base_caseset, manifest, effective_spec, directive["attempt_kind"],
        base_work, attempt_work)
    try:
        mode = run_workflow._resolve_mode(spec, None)
    except SystemExit as ex:
        raise RetestExecutionError(f"无法从 base spec 派生 runner mode: {ex}") from ex
    if mode == "aclnn_py":
        verify_aclnn_harness.validate_receipt(
            base_reports, "work/aclnn_harness_trust.json", spec, base_caseset)
    # attempt work 不写 _perf_plan.json，因此 aclnn_py 明确不采性能。
    evidence = (
        _run_cpp_extension_task2_only(
            spec, subset, attempt_work, manifest, directive)
        if mode == "cpp_extension"
        else repo_adapter.MODES[mode](subset, attempt_work)
    )
    verdict = validator.validate(effective_spec, subset, evidence)
    _write_json(attempt, "caseset.json", subset)
    _write_json(attempt, "evidence.json", evidence)
    _write_json(attempt, "verdict.json", verdict)
    errors = []
    validate_acceptance_state.gate_task2(
        attempt, errors, source_facts_path=source_facts_path)
    gate = {"passed": not errors, "errors": {"task2": errors} if errors else {}}
    _write_json(attempt, "attempt_gate.json", gate)
    result = {
        "schema_version": 1,
        "attempt_kind": directive["attempt_kind"],
        "policy_source": (
            "base_spec" if directive["attempt_kind"] == "same_policy_rerun"
            else f"relaxed:{directive['directive_id']}"),
        "precision_verdict": (verdict.get("overall") or {}).get("verdict"),
        "gate": gate,
        "perf_source": "inherited_from_base",
        "performance_retested": False,
        "requires_human_cp": directive["attempt_kind"] == "relaxed_rerun",
        "copied_case_files": copied,
        "base_acceptance_unchanged": True,
    }
    if mode == "cpp_extension":
        result["cpp_extension_execution"] = copy.deepcopy(
            evidence["precision_retest_execution"])
    _write_json(attempt, "retest_acceptance.json", result)
    output_hashes = {
        "evidence_sha256": contract.sha256_file(
            os.path.join(attempt, "evidence.json")),
        "verdict_sha256": contract.sha256_file(
            os.path.join(attempt, "verdict.json")),
        "result_sha256": contract.sha256_file(
            os.path.join(attempt, "retest_acceptance.json")),
    }
    completed_at = (datetime.datetime.now(datetime.timezone.utc)
                    .isoformat().replace("+00:00", "Z"))
    receipt = contract.build_attempt_receipt(
        _read_envelope(
            os.path.join(attempt, "attempt.manifest.json"),
            "oprunway/precision-retest-manifest/v1")[0],
        output_hashes, gate, completed_at)
    report_path = render_precision_retest_markdown.render_directory(
        attempt, receipt)
    if (os.path.islink(report_path) or not os.path.isfile(report_path)
            or os.path.getsize(report_path) == 0):
        raise RetestExecutionError("精度重测报告未成功生成，拒绝提交完成 receipt")
    _write_json(attempt, "attempt.receipt.json", receipt)
    return result


def execute_precision_attempt(attempt_dir, attempts_root):
    """以 O_EXCL owner lock 串行执行；失败保留 work，最终 receipt 仍是提交点。"""
    if os.path.islink(attempt_dir):
        raise RetestExecutionError("attempt_dir 本身不得为符号链接")
    if os.path.islink(attempts_root):
        raise RetestExecutionError("可信 attempts_root 本身不得为符号链接")
    trusted_root = os.path.realpath(os.fspath(attempts_root))
    if not os.path.isdir(trusted_root):
        raise RetestExecutionError("可信 attempts_root 须为存在的真实目录")
    attempt = os.path.realpath(os.fspath(attempt_dir))
    if not os.path.isdir(attempt):
        raise RetestExecutionError("attempt_dir 须为存在的真实目录")
    if (os.path.dirname(attempt) != trusted_root
            or not re.fullmatch(r"\d{4}", os.path.basename(attempt))):
        raise RetestExecutionError("attempt_dir 必须是可信 attempts_root 的直接四位子目录")
    receipt = os.path.join(attempt, "attempt.receipt.json")
    if os.path.lexists(receipt):
        raise RetestExecutionError(
            "attempt 已有完成收据，属于不可变历史；请创建新的 attempt")
    lock = os.path.join(attempt, ".execute.lock")
    if os.path.lexists(lock):
        raise RetestExecutionError(
            "attempt 正由另一 execution owner 执行")
    _, manifest = _read_envelope(
        os.path.join(attempt, "attempt.manifest.json"),
        "oprunway/precision-retest-manifest/v1")
    base = manifest.get("base_artifacts")
    caseset_path = ((base or {}).get("caseset") or {}).get("path")
    expected_root = (os.path.realpath(os.path.join(
        os.path.dirname(caseset_path), "attempts"))
        if isinstance(caseset_path, str) and os.path.isabs(caseset_path)
        else None)
    if expected_root != trusted_root:
        raise RetestExecutionError(
            "manifest base caseset 与外部可信 attempts_root 不一致")
    owner = {
        "schema_version": 1, "status": "running",
        "pid": os.getpid(),
        "operation": "execute_precision_attempt",
        "manifest_digest": content_address.content_digest(
            "oprunway/precision-retest-manifest/v1", manifest),
        "started_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    try:
        contract.publish_owner_lock(lock, owner)
    except contract.RetestContractError as ex:
        raise RetestExecutionError(
            "attempt 正由另一 execution owner 执行") from ex
    try:
        return _execute_precision_attempt_locked(attempt)
    finally:
        if os.path.isfile(lock) and not os.path.islink(lock):
            os.unlink(lock)


def execute_same_policy_attempt(attempt_dir, attempts_root):
    """兼容旧调用名；会按 directive 的实际 kind 路由。"""
    return execute_precision_attempt(attempt_dir, attempts_root)
