"""CP-F 精度重测的确定性契约基础层。

本模块只负责 directive、relaxed spec、attempt 身份和原子工件；不执行 NPU、
不计算精度 metrics，也不产 pass/fail。裁决继续由 validator.py 与验收门负责。
"""

import copy
import datetime
import hashlib
import json
import math
import os
import re
import tempfile

import content_address
import precision_policy


SCHEMA_VERSION = 1
ATTEMPT_KINDS = frozenset({"same_policy_rerun", "relaxed_rerun", "replay_only"})
DIRECTIVE_STATUSES = frozenset({"drafted", "confirmed", "expired", "revoked"})
BASE_ARTIFACTS = (
    "spec", "caseset", "evidence", "verdict", "acceptance",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
_DIRECTIVE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_UTC_TIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_OVERRIDE_KEYS = frozenset({
    "standard", "tolerance", "error_rate", "threshold", "max_ratio", "eps",
    "rtol", "atol",
})
_OVERRIDE_KEYS_BY_STANDARD = {
    precision_policy.ASCENDOPTEST_DEFAULT:
        frozenset({"standard", "tolerance", "error_rate"}),
    precision_policy.ECOSYSTEM_MERE_MARE:
        frozenset({"standard", "threshold", "max_ratio", "eps"}),
    precision_policy.TORCH_ALLCLOSE:
        frozenset({"standard", "rtol", "atol"}),
}
_RELAXED_SPEC_DOMAIN = "oprunway/precision-retest-relaxed-spec/v1"
_DIRECTIVE_DOMAIN = "oprunway/precision-retest-directive/v1"
_MANIFEST_DOMAIN = "oprunway/precision-retest-manifest/v1"
_RECEIPT_DOMAIN = "oprunway/precision-retest-receipt/v1"


class RetestContractError(ValueError):
    """CP-F 输入或工件不满足严格契约。"""


def _require_object(value, where):
    if not isinstance(value, dict):
        raise RetestContractError(f"{where} 须为对象，得 {type(value).__name__}")
    return value


def _require_sha256(value, where):
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise RetestContractError(f"{where} 须为小写 sha256")
    return value


def _checked_number(value, where):
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0):
        raise RetestContractError(f"{where} 须为有限非负数，得 {value!r}")
    return value


def sha256_file(path):
    """流式计算文件 SHA-256；拒绝非普通文件与符号链接。"""
    if os.path.islink(path) or not os.path.isfile(path):
        raise RetestContractError(f"待绑定工件须为普通文件且非符号链接: {path!r}")
    digest = hashlib.sha256()
    with open(path, "rb") as src:
        while True:
            block = src.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def fingerprint_base_artifacts(paths):
    """绑定首次验收五类基础工件，缺项或额外项均拒绝。"""
    _require_object(paths, "paths")
    expected = set(BASE_ARTIFACTS)
    actual = set(paths)
    if actual != expected:
        raise RetestContractError(
            f"基础工件键须严格等于 {sorted(expected)}，缺 {sorted(expected - actual)} "
            f"多 {sorted(actual - expected)}")
    return {name: {"path": os.path.abspath(os.fspath(paths[name])),
                   "sha256": sha256_file(paths[name])}
            for name in BASE_ARTIFACTS}


def verify_base_artifacts(directive, reports_dir):
    """复核 directive 绑定的基础工件仍位于本报告目录且字节未漂移。"""
    d = validate_directive(directive, require_confirmed=True)
    root = os.path.realpath(os.fspath(reports_dir))
    if not os.path.isdir(root) or os.path.islink(reports_dir):
        raise RetestContractError(f"reports_dir 须为真实目录且非符号链接: {reports_dir!r}")
    verified = {}
    for name in BASE_ARTIFACTS:
        recorded = d["base_artifacts"][name]
        absolute = os.path.abspath(recorded["path"])
        path = os.path.realpath(absolute)
        if path != absolute:
            raise RetestContractError(
                f"base_artifacts.{name}.path 含符号链接路径段: {absolute!r}")
        try:
            if os.path.commonpath((root, path)) != root:
                raise RetestContractError(
                    f"base_artifacts.{name}.path 逃逸 reports_dir: {path!r}")
        except RetestContractError:
            raise
        except ValueError as ex:
            raise RetestContractError(
                f"base_artifacts.{name}.path 与 reports_dir 不同卷") from ex
        actual = sha256_file(path)
        if actual != recorded["sha256"]:
            raise RetestContractError(
                f"drift_blocked:base_{name}_sha256_mismatch "
                f"recorded={recorded['sha256']} actual={actual}")
        verified[name] = {"path": path, "sha256": actual}
    return verified


def load_strict_json(path, where):
    """读取严格 JSON 对象，拒绝 NaN/Inf、坏编码和非对象顶层。"""
    try:
        with open(path, encoding="utf-8") as src:
            value = json.load(
                src,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    RetestContractError(f"{where}: 非法 JSON 常量 {token}")),
            )
    except RetestContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise RetestContractError(f"{where}: 无法读取 JSON: {ex}") from ex
    return _require_object(value, where)


def build_case_bindings(caseset, work_dir, case_ids):
    """从原 caseset 冻结 case 结构与实际输入文件字节。

    ``case_digest`` 绑定整条 case JSON；``input_sha256`` 绑定 runner 实际消费的
    ``work/<input.path>``。禁止缺 path、重复输入名、路径逃逸和符号链接。
    """
    cs = _require_object(caseset, "caseset")
    cases = cs.get("cases")
    if not isinstance(cases, list) or not cases:
        raise RetestContractError("caseset.cases 须为非空列表")
    if (not isinstance(case_ids, list) or not case_ids
            or any(not isinstance(x, str) or not x for x in case_ids)
            or len(case_ids) != len(set(case_ids))):
        raise RetestContractError("case_ids 须为非空、无重复的非空字符串列表")
    by_id = {}
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            raise RetestContractError(f"caseset.cases[{index}] 缺合法 id")
        if case["id"] in by_id:
            raise RetestContractError(f"caseset 有重复 case_id={case['id']!r}")
        by_id[case["id"]] = case
    missing = [cid for cid in case_ids if cid not in by_id]
    if missing:
        raise RetestContractError(f"directive case 不在原 caseset: {missing}")
    root = os.path.abspath(os.fspath(work_dir))
    if not os.path.isdir(root) or os.path.islink(root):
        raise RetestContractError(f"work_dir 须为真实目录且非符号链接: {work_dir!r}")
    bindings = {}
    for cid in case_ids:
        case = by_id[cid]
        inputs = case.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise RetestContractError(f"{cid}: inputs 须为非空列表")
        input_hashes = {}
        for index, inp in enumerate(inputs):
            if not isinstance(inp, dict):
                raise RetestContractError(f"{cid}: inputs[{index}] 须为对象")
            name, relative = inp.get("name"), inp.get("path")
            if not isinstance(name, str) or not name or name in input_hashes:
                raise RetestContractError(f"{cid}: input 名缺失或重复: {name!r}")
            if not isinstance(relative, str) or not relative:
                raise RetestContractError(f"{cid}.{name}: 缺 input.path")
            try:
                path = content_address.safe_path(root, relative)
            except content_address.ContentAddressError as ex:
                raise RetestContractError(f"{cid}.{name}: input.path 非法: {ex}") from ex
            input_hashes[name] = sha256_file(path)
        golden_hashes = {}
        expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
        golden_paths = []
        if isinstance(expected.get("golden_path"), str):
            golden_paths.append(expected["golden_path"])
        for output in expected.get("outputs") or []:
            if isinstance(output, dict) and isinstance(output.get("golden_path"), str):
                golden_paths.append(output["golden_path"])
        for relative in golden_paths:
            if relative in golden_hashes:
                continue
            try:
                path = content_address.safe_path(root, relative)
            except content_address.ContentAddressError as ex:
                raise RetestContractError(
                    f"{cid}: golden.path 非法: {ex}") from ex
            golden_hashes[relative] = sha256_file(path)
        if not golden_hashes:
            raise RetestContractError(f"{cid}: 缺可冻结的 golden 文件")
        bindings[cid] = {
            "case_digest": content_address.content_digest(
                "oprunway/precision-retest-case/v1", case),
            "input_sha256": input_hashes,
            "golden_sha256": golden_hashes,
        }
    return bindings


def prepare_attempt_inputs(directive, reports_dir):
    """执行 F2 的本地、无 compute 部分：基础工件复核 + case/input 冻结。"""
    d = validate_directive(directive, require_confirmed=True)
    base = verify_base_artifacts(d, reports_dir)
    caseset = load_strict_json(base["caseset"]["path"], "caseset")
    base_reports_dir = os.path.dirname(base["caseset"]["path"])
    work_dir = os.path.join(base_reports_dir, "work")
    bindings = build_case_bindings(caseset, work_dir, d["case_ids"])
    return {
        "base_artifacts": base,
        "base_reports_dir": base_reports_dir,
        "case_bindings": bindings,
    }


def _canonical_sha(value):
    return hashlib.sha256(content_address.canonical_json_bytes(value)).hexdigest()


def publish_owner_lock(lock_path, owner):
    """完整 owner JSON 的单一原子发布；无空锁/半 JSON 可见窗口。"""
    lock = os.path.abspath(os.fspath(lock_path))
    parent = os.path.dirname(lock)
    if os.path.lexists(lock):
        raise RetestContractError("lock 已存在")
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(lock) + ".owner.", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(owner, out, ensure_ascii=False, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(tmp, lock)
        except FileExistsError as ex:
            raise RetestContractError("lock 已存在") from ex
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _cpp_extension_base_binding(evidence, caseset, reports_dir, case_ids):
    """从首次 cpp_extension 证据冻结可执行身份；不依赖已清理的远端绝对路径。"""
    receipt = evidence.get("cpp_extension_receipt")
    if (not isinstance(receipt, dict)
            or receipt.get("schema") != "oprunway.cpp_extension_receipt"
            or receipt.get("schema_version") != 1
            or receipt.get("status") != "VERIFIED"):
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_receipt_missing")
    work = os.path.join(os.path.realpath(os.fspath(reports_dir)), "work")
    receipt_path = os.path.join(work, "cpp_extension_receipt.json")
    plan_path = os.path.join(work, "cpp_extension_invocation_plan.json")
    manifest_path = os.path.join(work, "cpp_extension", "extension_manifest.json")
    disk_receipt = load_strict_json(receipt_path, "base cpp_extension receipt")
    plan = load_strict_json(plan_path, "base cpp_extension invocation plan")
    extension_manifest = load_strict_json(
        manifest_path, "base cpp_extension manifest")
    if disk_receipt != receipt:
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_receipt_file_mismatch")
    bindings = receipt.get("bindings")
    if not isinstance(bindings, dict):
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_bindings_missing")
    expected = {
        "caseset_sha256": _canonical_sha(caseset),
        "manifest_sha256": _canonical_sha(extension_manifest),
        "invocation_plan_sha256": _canonical_sha(plan),
    }
    for key, value in expected.items():
        if bindings.get(key) != value:
            raise RetestContractError(
                f"drift_blocked:base_cpp_extension_{key}_mismatch")
    if plan.get("caseset_sha256") != expected["caseset_sha256"]:
        raise RetestContractError(
            "drift_blocked:base_invocation_plan_caseset_mismatch")
    rows = plan.get("cases")
    if not isinstance(rows, list):
        raise RetestContractError(
            "drift_blocked:base_invocation_plan_cases_missing")
    by_id = {}
    for row in rows:
        cid = row.get("case_id") if isinstance(row, dict) else None
        if not isinstance(cid, str) or not cid or cid in by_id:
            raise RetestContractError(
                "drift_blocked:base_invocation_plan_case_id_invalid")
        by_id[cid] = row
    missing = [cid for cid in case_ids if cid not in by_id]
    if missing:
        raise RetestContractError(
            f"drift_blocked:base_invocation_plan_missing_cases={missing}")
    vendor = receipt.get("vendor")
    build_receipt = vendor.get("build_receipt") if isinstance(vendor, dict) else None
    source = build_receipt.get("source") if isinstance(build_receipt, dict) else None
    runtime = receipt.get("runtime")
    if (not isinstance(vendor, dict) or not isinstance(build_receipt, dict)
            or not isinstance(source, dict) or not isinstance(runtime, dict)):
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_provenance_incomplete")
    build = build_receipt.get("build")
    if (not isinstance(source.get("repo"), str) or not source["repo"]
            or not isinstance(build, dict)
            or not isinstance(build.get("argv"), list) or not build["argv"]
            or any(not isinstance(x, str) or not x for x in build["argv"])):
        raise RetestContractError(
            "drift_blocked:base_vendor_build_invocation_incomplete")
    if vendor.get("build_receipt_sha256") != _canonical_sha(build_receipt):
        raise RetestContractError(
            "drift_blocked:base_vendor_build_receipt_sha256_mismatch")
    receipt_digest = _canonical_sha(receipt)
    evidence_rows = evidence.get("evidence")
    cases = caseset.get("cases") or []
    if not isinstance(evidence_rows, list) or len(evidence_rows) != len(cases):
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_evidence_count_mismatch")
    if [row.get("case_id") if isinstance(row, dict) else None
            for row in evidence_rows] != [case.get("id") for case in cases]:
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_evidence_case_sequence_mismatch")
    for row in evidence_rows:
        if (not isinstance(row, dict)
                or row.get("cpp_extension_receipt_sha256") != receipt_digest):
            raise RetestContractError(
                "drift_blocked:base_evidence_cpp_extension_receipt_mismatch")
    return {
        "schema": "oprunway.precision_retest.cpp_extension_binding",
        "schema_version": 1,
        "base_receipt_sha256": receipt_digest,
        "base_caseset_sha256": expected["caseset_sha256"],
        "base_manifest_sha256": expected["manifest_sha256"],
        "base_namespace": extension_manifest.get("namespace"),
        "base_invocation_plan_sha256": expected["invocation_plan_sha256"],
        "base_spec_sha256": bindings.get("spec_sha256"),
        "base_pr_head": source.get("pr_head_sha"),
        "base_build_receipt_sha256": vendor.get("build_receipt_sha256"),
        "base_vendor_build_argv": copy.deepcopy(
            build["argv"]),
        "base_source_repo": source.get("repo"),
        "base_vendor_elf_sha256": vendor.get("library_sha256"),
        "base_soc": runtime.get("soc"),
        "base_toolkit": runtime.get("cann_version"),
        "selected_invocations": [copy.deepcopy(by_id[cid]) for cid in case_ids],
    }


def materialize_attempt(directive, reports_dir, execution_identity):
    """原子创建一个准备完成的 attempt 目录，不执行 NPU。

    所有校验和工件构造先完成，最后才占用编号；占号后的各 JSON 都使用原子写。
    若写盘中断，目录因缺 ``attempt.receipt.json`` 保持为可识别的未完成现场。
    """
    d = validate_directive(directive, require_confirmed=True)
    prepared = prepare_attempt_inputs(d, reports_dir)
    spec = load_strict_json(prepared["base_artifacts"]["spec"]["path"], "base spec")
    caseset = load_strict_json(prepared["base_artifacts"]["caseset"]["path"], "caseset")
    evidence = load_strict_json(
        prepared["base_artifacts"]["evidence"]["path"], "base evidence")
    op = spec.get("op")
    if not isinstance(op, str) or not op or caseset.get("op") != op:
        raise RetestContractError(
            f"base spec/caseset op 不一致: spec={op!r}, caseset={caseset.get('op')!r}")
    runner_form = spec.get("runner_form") or "cpp"
    if runner_form != d["source_identity"]["runner_form"]:
        raise RetestContractError(
            f"drift_blocked:runner_form_mismatch directive="
            f"{d['source_identity']['runner_form']!r} spec={runner_form!r}")
    identity = copy.deepcopy(_require_object(execution_identity, "execution_identity"))
    required_identity = {
        "soc", "toolkit", "vendor_elf_sha256", "golden_source_sha256",
    }
    if set(identity) != required_identity:
        raise RetestContractError(
            f"execution_identity 键须严格等于 {sorted(required_identity)}")
    for key in ("soc", "toolkit"):
        if not isinstance(identity.get(key), str) or not identity[key]:
            raise RetestContractError(f"execution_identity.{key} 须为非空字符串")
    for key in ("vendor_elf_sha256", "golden_source_sha256"):
        _require_sha256(identity.get(key), f"execution_identity.{key}")
    runner_binding = None
    base_provenance = evidence.get("execution_provenance")
    if runner_form == "cpp_extension":
        runner_binding = _cpp_extension_base_binding(
            evidence, caseset, prepared["base_reports_dir"], d["case_ids"])
        if runner_binding.get("base_spec_sha256") != _canonical_sha(spec):
            raise RetestContractError(
                "drift_blocked:base_cpp_extension_spec_sha256_mismatch")
        golden_source_path = os.path.join(
            os.path.dirname(prepared["base_artifacts"]["spec"]["path"]),
            "golden.py")
        golden_source_sha256 = sha256_file(golden_source_path)
        base_provenance = {
            "head_sha": runner_binding["base_pr_head"],
            "soc": runner_binding["base_soc"],
            "toolkit_version": runner_binding["base_toolkit"],
            "build_receipt_sha256":
                runner_binding["base_build_receipt_sha256"],
            "vendor_elf_sha256": runner_binding["base_vendor_elf_sha256"],
            # cpp_extension 首轮 envelope 尚无该字段；改从基础 spec 同目录的
            # 授权 golden.py 实际字节取证，不接受本轮自报替代。
            "golden_source_sha256": golden_source_sha256,
        }
        runner_binding["base_golden_source_sha256"] = golden_source_sha256
    elif not isinstance(base_provenance, dict):
        raise RetestContractError(
            "drift_blocked:base_execution_provenance_missing；首次 evidence 未保存实际 "
            "PR/build/SoC/toolkit 身份，不能用本轮自报 identity 代替")
    expected_provenance = {
        "head_sha": d["source_identity"]["pr_head"],
        "soc": identity["soc"],
        "toolkit_version": identity["toolkit"],
        "build_receipt_sha256": d["source_identity"]["build_receipt_sha256"],
    }
    for field, expected in expected_provenance.items():
        actual = base_provenance.get(field)
        if actual != expected:
            raise RetestContractError(
                f"drift_blocked:{field}_mismatch base={actual!r} expected={expected!r}")
    # vendor ELF/golden source 需要基础 evidence 提供真实 hash；旧产物缺字段时明确 BLOCKED，
    # 不把本轮 execution_identity 的值当作首次事实。
    for field in ("vendor_elf_sha256", "golden_source_sha256"):
        actual = base_provenance.get(field)
        if actual != identity[field]:
            raise RetestContractError(
                f"drift_blocked:{field}_mismatch base={actual!r} "
                f"expected={identity[field]!r}")
    manifest = build_attempt_manifest(
        d, prepared["case_bindings"], identity, runner_binding,
        os.path.realpath(os.fspath(reports_dir)))
    relaxed = (derive_relaxed_spec(spec, d)
               if d["attempt_kind"] == "relaxed_rerun" else None)
    attempts_root = os.path.join(prepared["base_reports_dir"], "attempts")
    directive_artifact = make_directive_artifact(d)
    number, attempt_dir, reused = _allocate_idempotent_attempt(
        attempts_root, directive_artifact, manifest, relaxed)
    if reused:
        return {"attempt": number, "attempt_dir": attempt_dir,
                "manifest": manifest, "relaxed_spec": relaxed,
                "idempotent_reuse": True}
    return {"attempt": number, "attempt_dir": attempt_dir,
            "manifest": manifest, "relaxed_spec": relaxed}


def validate_directive(directive, *, require_confirmed=False):
    """严格校验人工 directive，并返回深拷贝。

    本函数只校结构和受控词表；case 是否属于原 caseset、基础 hash 是否匹配由准备门校。
    """
    d = copy.deepcopy(_require_object(directive, "directive"))
    allowed = {
        "schema_version", "directive_id", "directive_status", "attempt_kind",
        "case_ids", "base_artifacts", "source_identity", "human_instruction",
        "confirmed_by", "confirmed_at", "precision_override",
    }
    unknown = set(d) - allowed
    if unknown:
        raise RetestContractError(f"directive 含未知字段: {sorted(unknown)}")
    if d.get("schema_version") != SCHEMA_VERSION:
        raise RetestContractError(f"directive.schema_version 须为 {SCHEMA_VERSION}")
    did = d.get("directive_id")
    if not isinstance(did, str) or _DIRECTIVE_ID_RE.fullmatch(did) is None:
        raise RetestContractError("directive_id 须为 1..128 位小写字母数字及 ._-")
    status = d.get("directive_status")
    if status not in DIRECTIVE_STATUSES:
        raise RetestContractError(f"未知 directive_status={status!r}")
    if require_confirmed and status != "confirmed":
        raise RetestContractError("执行前 directive_status 必须为 confirmed")
    kind = d.get("attempt_kind")
    if kind not in ATTEMPT_KINDS:
        raise RetestContractError(f"未知 attempt_kind={kind!r}")
    case_ids = d.get("case_ids")
    if (not isinstance(case_ids, list) or not case_ids
            or any(not isinstance(x, str) or not x for x in case_ids)
            or len(case_ids) != len(set(case_ids))):
        raise RetestContractError("case_ids 须为非空、无重复的非空字符串列表")
    base = _require_object(d.get("base_artifacts"), "base_artifacts")
    if set(base) != set(BASE_ARTIFACTS):
        raise RetestContractError(f"base_artifacts 键须严格等于 {sorted(BASE_ARTIFACTS)}")
    for name in BASE_ARTIFACTS:
        item = _require_object(base[name], f"base_artifacts.{name}")
        if (set(item) != {"path", "sha256"}
                or not isinstance(item.get("path"), str)
                or not os.path.isabs(item["path"])):
            raise RetestContractError(f"base_artifacts.{name} 须仅含 path/sha256")
        _require_sha256(item.get("sha256"), f"base_artifacts.{name}.sha256")
    source = _require_object(d.get("source_identity"), "source_identity")
    required_source = {"pr_head", "build_receipt_sha256", "runner_form"}
    if set(source) != required_source:
        raise RetestContractError(
            f"source_identity 键须严格等于 {sorted(required_source)}")
    if (not isinstance(source.get("pr_head"), str)
            or _GIT_COMMIT_RE.fullmatch(source["pr_head"]) is None):
        raise RetestContractError(
            "source_identity.pr_head 须为 40..64 位小写十六进制完整提交 ID")
    _require_sha256(source.get("build_receipt_sha256"),
                    "source_identity.build_receipt_sha256")
    if source.get("runner_form") not in ("cpp", "aclnn_py", "cpp_extension"):
        raise RetestContractError("source_identity.runner_form 不受支持")
    if not isinstance(d.get("human_instruction"), str) or not d["human_instruction"].strip():
        raise RetestContractError("human_instruction 须为非空字符串")
    if status == "confirmed":
        if not isinstance(d.get("confirmed_by"), str) or not d["confirmed_by"].strip():
            raise RetestContractError("confirmed directive 必须记录 confirmed_by")
        if (not isinstance(d.get("confirmed_at"), str)
                or _UTC_TIME_RE.fullmatch(d["confirmed_at"]) is None):
            raise RetestContractError(
                "confirmed directive 的 confirmed_at 须为 UTC ISO 8601（末尾 Z）")
    override = d.get("precision_override")
    if kind == "relaxed_rerun":
        _validate_override(override)
    elif override is not None:
        raise RetestContractError(
            f"{kind} 不得携带 precision_override；政策变化只允许 relaxed_rerun")
    if kind == "replay_only" and status == "confirmed":
        # replay 的 policy/digest 等值由准备门对原 case 逐项校；这里确保它没有政策入口。
        if "precision_override" in d and d["precision_override"] is not None:
            raise RetestContractError("replay_only 不得改变精度政策")
    return d


def _validate_override(override):
    value = _require_object(override, "precision_override")
    unknown = set(value) - _OVERRIDE_KEYS
    if unknown:
        raise RetestContractError(f"precision_override 含越权字段: {sorted(unknown)}")
    if not value:
        raise RetestContractError("relaxed_rerun 的 precision_override 不得为空")
    standard = value.get("standard")
    if standard is None:
        raise RetestContractError(
            "relaxed_rerun 必须显式给出 precision_override.standard")
    if standard not in precision_policy.STANDARDS:
        raise RetestContractError(f"未知 precision standard={standard!r}")
    allowed = _OVERRIDE_KEYS_BY_STANDARD.get(standard)
    if allowed is None:
        raise RetestContractError(
            f"precision standard={standard!r} 没有可放宽的数值政策")
    incompatible = set(value) - allowed
    if incompatible:
        raise RetestContractError(
            f"precision standard={standard!r} 不接受字段: "
            f"{sorted(incompatible)}")
    numeric = set(value) - {"standard"}
    if not numeric:
        raise RetestContractError(
            "relaxed_rerun 必须至少给出一个实际数值字段，禁止仅改 standard/no-op")
    for key in _OVERRIDE_KEYS - {"standard"}:
        if key in value:
            _checked_number(value[key], f"precision_override.{key}")
    return value


def derive_relaxed_spec(base_spec, directive):
    """从原 spec + confirmed directive 确定性派生完整 relaxed spec。

    只允许写入 ``precision.acceptance_policy``。原平台 standard、oracle、覆盖轴及其它
    spec 字段保持不变，使 validator 仍可从 spec 复算 canonical acceptance。
    """
    base = copy.deepcopy(_require_object(base_spec, "base_spec"))
    d = validate_directive(directive, require_confirmed=True)
    if d["attempt_kind"] != "relaxed_rerun":
        raise RetestContractError("只有 relaxed_rerun 可派生 relaxed spec")
    precision = base.get("precision")
    if precision is None:
        precision = {}
        base["precision"] = precision
    if not isinstance(precision, dict):
        raise RetestContractError("base_spec.precision 须为对象")
    override = copy.deepcopy(d["precision_override"])
    base_standard = precision.get("standard")
    target_standard = override["standard"]
    if base_standard != target_standard:
        required = {
            precision_policy.ASCENDOPTEST_DEFAULT: {"tolerance", "error_rate"},
            precision_policy.ECOSYSTEM_MERE_MARE:
                {"threshold", "max_ratio", "eps"},
            precision_policy.TORCH_ALLCLOSE: {"rtol", "atol"},
        }[target_standard]
        missing = required - set(override)
        if missing:
            raise RetestContractError(
                f"跨 precision family override={target_standard!r} 必须完整给出 "
                f"{sorted(required)}，缺 {sorted(missing)}")
    precision["acceptance_policy"] = override
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "directive_id": d["directive_id"],
        "base_spec_sha256": content_address.content_digest(
            "oprunway/spec-json/v1", base_spec),
        "override_diff": {"precision.acceptance_policy": override},
    }
    base["precision_retest"] = provenance
    return content_address.make_artifact(_RELAXED_SPEC_DOMAIN, base)


def make_directive_artifact(directive):
    return content_address.make_artifact(
        _DIRECTIVE_DOMAIN, validate_directive(directive))


def allocate_attempt(attempts_root):
    """原子占用下一个四位 attempt 目录；并发者通过 mkdir 的原子性竞争。"""
    root = os.path.abspath(os.fspath(attempts_root))
    os.makedirs(root, exist_ok=True)
    for number in range(1, 10000):
        name = f"{number:04d}"
        path = os.path.join(root, name)
        try:
            os.mkdir(path)
        except FileExistsError:
            continue
        return name, path
    raise RetestContractError("attempt 编号已耗尽（0001..9999）")


def _allocate_idempotent_attempt(attempts_root, directive_artifact,
                                 manifest_artifact, relaxed_artifact):
    """受目录锁保护地按 directive_id 幂等分配；同 ID 异内容拒绝。"""
    root = os.path.abspath(os.fspath(attempts_root))
    os.makedirs(root, exist_ok=True)
    lock = os.path.join(root, ".allocation.lock")
    owner = {
        "schema_version": 1,
        "status": "running",
        "pid": os.getpid(),
        "operation": "allocate_precision_retest_attempt",
        "started_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "directive_digest": directive_artifact["digest"],
        "manifest_digest": manifest_artifact["digest"],
    }
    try:
        publish_owner_lock(lock, owner)
    except RetestContractError as ex:
        raise RetestContractError(
            "attempt allocation 正由另一 owner 执行；请稍后重试") from ex
    try:
        wanted = directive_artifact["payload"]["directive_id"]
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if not re.fullmatch(r"\d{4}", name):
                continue
            if os.path.islink(path) or not os.path.isdir(path) \
                    or os.path.realpath(path) != os.path.abspath(path) \
                    or os.path.commonpath((root, os.path.realpath(path))) != root:
                raise RetestContractError(
                    f"numeric attempt entry 非受控真实目录: {name}")
            dpath = os.path.join(path, "directive.json")
            mpath = os.path.join(path, "attempt.manifest.json")
            if not os.path.isfile(dpath):
                continue
            existing_d = load_strict_json(dpath, "existing directive")
            payload = existing_d.get("payload") if isinstance(existing_d, dict) else None
            if not isinstance(payload, dict) or payload.get("directive_id") != wanted:
                continue
            if not os.path.isfile(mpath):
                raise RetestContractError(
                    f"directive_id={wanted!r} 已存在半写 attempt={name}")
            existing_m = load_strict_json(mpath, "existing manifest")
            if existing_d != directive_artifact or existing_m != manifest_artifact:
                raise RetestContractError(
                    f"directive_id={wanted!r} 已绑定不同内容，拒绝复用")
            prep = os.path.join(path, "preparation.json")
            relaxed_path = os.path.join(path, "spec.relaxed.json")
            if not os.path.isfile(prep) or (
                    relaxed_artifact is not None
                    and (not os.path.isfile(relaxed_path)
                         or load_strict_json(relaxed_path, "existing relaxed spec")
                         != relaxed_artifact)):
                raise RetestContractError(
                    f"directive_id={wanted!r} 命中未完成准备现场 {name}")
            return name, path, True
        name, path = allocate_attempt(root)
        content_address.atomic_write_json(
            path, "directive.json", directive_artifact)
        content_address.atomic_write_json(
            path, "attempt.manifest.json", manifest_artifact)
        if relaxed_artifact is not None:
            content_address.atomic_write_json(
                path, "spec.relaxed.json", relaxed_artifact)
        content_address.atomic_write_json(
            path, "preparation.json", {
                "schema_version": SCHEMA_VERSION,
                "attempt": name,
                "status": "prepared",
                "acceptance_verdict": None,
                "note": "仅完成 CP-F 本地准备与冻结；尚未执行 NPU、尚无重测裁决",
            })
        return name, path, False
    finally:
        if os.path.isfile(lock) and not os.path.islink(lock):
            os.unlink(lock)


def recover_stale_lock(lock_path, attempts_root, expected_digest, operation):
    """显式恢复协议：只处理已死 owner，先原子标 abandoned；绝不按 mtime 猜锁。

    调用者必须提供预期 digest/operation；execute 锁还要求 attempt 无最终 receipt。
    """
    lock = os.path.abspath(os.fspath(lock_path))
    root = os.path.realpath(os.fspath(attempts_root))
    real_parent = os.path.realpath(os.path.dirname(lock))
    if os.path.commonpath((root, real_parent)) != root:
        raise RetestContractError("lock 逃逸受控 attempts root")
    owner_path = (os.path.join(lock, "owner.json")
                  if os.path.isdir(lock) else lock)
    owner = load_strict_json(owner_path, "lock owner")
    if (owner.get("status") != "running"
            or owner.get("operation") != operation
            or expected_digest not in (
                owner.get("manifest_digest"), owner.get("directive_digest"))):
        raise RetestContractError("lock owner operation/digest 不匹配")
    pid = owner.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        raise RetestContractError("lock owner pid 非法")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    except PermissionError as ex:
        raise RetestContractError("无法证明 lock owner 已退出") from ex
    else:
        raise RetestContractError("lock owner 仍存活，拒绝恢复")
    if operation == "execute_precision_attempt" \
            and os.path.lexists(os.path.join(os.path.dirname(lock),
                                             "attempt.receipt.json")):
        raise RetestContractError("已有 final receipt，拒绝恢复 execute lock")
    abandoned = lock + ".abandoned." + expected_digest[:12]
    if os.path.lexists(abandoned):
        raise RetestContractError("abandoned 标记已存在，拒绝覆盖")
    os.replace(lock, abandoned)
    return abandoned


def build_attempt_manifest(directive, case_bindings, execution_identity,
                           runner_binding=None, base_artifact_scope=None):
    """构造冻结 manifest；不读取/猜测 case，调用方必须传入已核验绑定。"""
    d = validate_directive(directive, require_confirmed=True)
    bindings = _require_object(case_bindings, "case_bindings")
    if set(bindings) != set(d["case_ids"]):
        raise RetestContractError(
            f"case_bindings 与 directive.case_ids 不一致：缺 "
            f"{sorted(set(d['case_ids']) - set(bindings))} 多 "
            f"{sorted(set(bindings) - set(d['case_ids']))}")
    for cid, binding in bindings.items():
        item = _require_object(binding, f"case_bindings.{cid}")
        if not isinstance(item.get("case_digest"), str):
            raise RetestContractError(f"{cid}: 缺 case_digest")
        _require_sha256(item["case_digest"], f"{cid}.case_digest")
        inputs = item.get("input_sha256")
        if not isinstance(inputs, dict) or not inputs:
            raise RetestContractError(f"{cid}: input_sha256 须为非空对象")
        for name, digest in inputs.items():
            if not isinstance(name, str) or not name:
                raise RetestContractError(f"{cid}: input 名非法")
            _require_sha256(digest, f"{cid}.input_sha256.{name}")
        goldens = item.get("golden_sha256")
        if not isinstance(goldens, dict) or not goldens:
            raise RetestContractError(f"{cid}: golden_sha256 须为非空对象")
        for path, digest in goldens.items():
            if not isinstance(path, str) or not path:
                raise RetestContractError(f"{cid}: golden 路径非法")
            _require_sha256(digest, f"{cid}.golden_sha256.{path}")
    identity = copy.deepcopy(_require_object(execution_identity, "execution_identity"))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "directive_id": d["directive_id"],
        "attempt_kind": d["attempt_kind"],
        "planned_case_ids": list(d["case_ids"]),
        "base_artifacts": copy.deepcopy(d["base_artifacts"]),
        "source_identity": copy.deepcopy(d["source_identity"]),
        "case_bindings": copy.deepcopy(bindings),
        "execution_identity": identity,
    }
    if runner_binding is not None:
        payload["runner_binding"] = copy.deepcopy(
            _require_object(runner_binding, "runner_binding"))
    if base_artifact_scope is not None:
        scope = os.path.realpath(os.fspath(base_artifact_scope))
        if not os.path.isabs(scope) or not os.path.isdir(scope):
            raise RetestContractError(
                "base_artifact_scope 须为存在的绝对目录")
        payload["base_artifact_scope"] = scope
    return content_address.make_artifact(_MANIFEST_DOMAIN, payload)


def build_completion_receipt(manifest_artifact, outputs, gate):
    """构造完成 sentinel；只有 gate passed 且输出摘要齐全才允许生成。"""
    artifact = _require_object(manifest_artifact, "manifest_artifact")
    if artifact.get("domain") != _MANIFEST_DOMAIN:
        raise RetestContractError("manifest domain 不匹配")
    manifest_digest = _require_sha256(artifact.get("digest"), "manifest.digest")
    if artifact.get("schema_version") != 1 or "payload" not in artifact:
        raise RetestContractError("manifest artifact envelope 不完整")
    actual_manifest_digest = content_address.content_digest(
        _MANIFEST_DOMAIN, artifact["payload"])
    if manifest_digest != actual_manifest_digest:
        raise RetestContractError("manifest artifact 摘要不匹配")
    out = _require_object(outputs, "outputs")
    required = {"evidence_sha256", "verdict_sha256", "result_sha256"}
    if set(out) != required:
        raise RetestContractError(f"outputs 键须严格等于 {sorted(required)}")
    for key in required:
        _require_sha256(out[key], f"outputs.{key}")
    gate_obj = _require_object(gate, "gate")
    if gate_obj.get("passed") is not True or gate_obj.get("errors") not in ({}, None):
        raise RetestContractError("gate 未干净通过，不得生成完成 receipt")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": manifest_digest,
        "outputs": copy.deepcopy(out),
        "gate": {"passed": True, "errors": {}},
        "lifecycle": "completed",
    }
    return content_address.make_artifact(_RECEIPT_DOMAIN, payload)


def build_attempt_receipt(manifest_artifact, outputs, gate, completed_at):
    """构造“执行已结束”收据；gate 可通过或失败，均不等于验收 PASS。"""
    artifact = _require_object(manifest_artifact, "manifest_artifact")
    if artifact.get("domain") != _MANIFEST_DOMAIN:
        raise RetestContractError("manifest domain 不匹配")
    digest = _require_sha256(artifact.get("digest"), "manifest.digest")
    if artifact.get("schema_version") != 1 or "payload" not in artifact:
        raise RetestContractError("manifest artifact envelope 不完整")
    if digest != content_address.content_digest(
            _MANIFEST_DOMAIN, artifact["payload"]):
        raise RetestContractError("manifest artifact 摘要不匹配")
    out = _require_object(outputs, "outputs")
    required = {"evidence_sha256", "verdict_sha256", "result_sha256"}
    if set(out) != required:
        raise RetestContractError(f"outputs 键须严格等于 {sorted(required)}")
    for key in required:
        _require_sha256(out[key], f"outputs.{key}")
    gate_obj = _require_object(gate, "gate")
    if not isinstance(gate_obj.get("passed"), bool):
        raise RetestContractError("gate.passed 须为 bool")
    errors = gate_obj.get("errors")
    if not isinstance(errors, dict):
        raise RetestContractError("gate.errors 须为对象")
    if gate_obj["passed"] != (not errors):
        raise RetestContractError("gate.passed 与 gate.errors 是否为空矛盾")
    if not isinstance(completed_at, str) or _UTC_TIME_RE.fullmatch(completed_at) is None:
        raise RetestContractError("completed_at 须为 UTC ISO 8601（末尾 Z）")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": digest,
        "outputs": copy.deepcopy(out),
        "gate": copy.deepcopy(gate_obj),
        "completed_at": completed_at,
        "lifecycle": "completed",
        "acceptance_verdict": None,
    }
    return content_address.make_artifact(_RECEIPT_DOMAIN, payload)
