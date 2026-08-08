#!/usr/bin/env python3
"""CP-C 前置失败的不可发布契约与唯一收口器（stdlib-only）。

本模块不重判算子精度/性能。它只证明：可信 CP-A/spec 已绑定到一次受控的
vendor build/receipt preflight 失败，并在 Task1/DUT/profiler 之前把正式出口封死。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import stat
from contextlib import contextmanager

import acceptance_artifacts
import artifact_path_guard
import content_address
import kernel_identity
import source_facts_lookup


SCHEMA = "oprunway.vendor_build_attempt"
SCHEMA_VERSION = 1
STATUS = "FAILED_PRE_EXECUTION"
TERMINAL_SCHEMA = "oprunway.pre_execution_terminal"
TERMINAL_VERSION = 1
VENDOR_ATTEMPT_FILE = "vendor_build_attempt.json"
DETAIL_REPORT_FILE = "前置执行失败明细.md"
_PRODUCER_TOOL = "pre_execution_failure.py"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_CODE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_CONTROLLED_STAGES = {
    "build", "target_preflight", "package_resolution", "target_closure",
    "source_post_build", "vendor_receipt", "receipt_preflight",
}

# finalizer 必须清掉所有可能让旧轮重新长成正式裁决的 downstream。payload 先写，
# marker 最后作为 commit manifest；无 marker 的半事务也不能产正式裁决。
_DOWNSTREAM_FILES = (
    "acceptance.json", "attempt_record.json", "verdict.json", "perf_report.json",
    "caseset.json", "evidence.json", "golden.py", "验收报告.md", "精度失败明细.md",
    "性能失败明细.md", "markdown_report_error.json", "目标内核交付失败明细.md",
    DETAIL_REPORT_FILE, VENDOR_ATTEMPT_FILE,
)


def _controlled_terminal_paths(out_dir):
    """finalizer 唯一会写/清理的根级路径；``work/`` 从不在其中。"""
    names = tuple(dict.fromkeys(_DOWNSTREAM_FILES + (
        acceptance_artifacts.PRE_EXECUTION_TERMINAL_FILE,
        acceptance_artifacts.ARTIFACT_LOCK_FILE,
    )))
    return tuple(os.path.join(out_dir, name) for name in names)


class PreExecutionFailureError(RuntimeError):
    """failure attempt 或它与 CP-A/spec 的绑定不可信。"""


@contextmanager
def _artifact_transaction(out_dir, guard):
    try:
        with acceptance_artifacts.artifact_transaction(
                out_dir, guard=guard) as transaction:
            yield transaction
    except acceptance_artifacts.ArtifactNameConflictError as ex:
        raise PreExecutionFailureError(str(ex)) from ex


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _open_below_report_root_nofollow(path, report_root):
    """当可信输入位于报告根内时，逐段 openat/no-follow；外部输入保持兼容。"""
    path_abs = os.path.abspath(path)
    root_abs = os.path.abspath(report_root)
    try:
        below = os.path.commonpath((path_abs, root_abs)) == root_abs
    except ValueError:
        below = False
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if not below:
        return os.open(path_abs, flags)
    relative = os.path.relpath(path_abs, root_abs)
    parts = relative.split(os.path.sep)
    if relative in {".", ".."} or any(part in {"", ".", ".."} for part in parts):
        raise PreExecutionFailureError("可信输入不得等于报告根或逃逸报告根")
    directory_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                       | getattr(os, "O_NOFOLLOW", 0))
    directory_fd = os.open(root_abs, directory_flags)
    try:
        for part in parts[:-1]:
            child_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        return os.open(parts[-1], flags, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)


def _read_retained_json(path, *, role, report_root):
    """一次 no-follow 读取得到 JSON、原字节、摘要与 inode 绑定。"""
    if not isinstance(path, str) or not path:
        raise PreExecutionFailureError(f"{role} 可信输入路径缺失")
    try:
        fd = _open_below_report_root_nofollow(path, report_root)
        try:
            status = os.fstat(fd)
            if not stat.S_ISREG(status.st_mode):
                raise PreExecutionFailureError(f"{role} 可信输入不是普通文件")
            chunks = []
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(fd)
        raw = b"".join(chunks)
        payload = json.loads(raw)
    except PreExecutionFailureError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise PreExecutionFailureError(
            f"{role} 可信输入不可 no-follow 读取（父链可能含符号链接）：{ex}") from ex
    return {
        "role": role, "path": os.path.abspath(path), "report_root": report_root,
        "raw": raw, "payload": payload,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "device": status.st_dev, "inode": status.st_ino,
    }


def _verify_retained_inputs(retained_inputs):
    for retained in retained_inputs:
        current = _read_retained_json(
            retained["path"], role=retained["role"],
            report_root=retained["report_root"])
        if ((current["device"], current["inode"])
                != (retained["device"], retained["inode"])
                or current["sha256"] != retained["sha256"]):
            raise PreExecutionFailureError(
                f"{retained['role']} 可信输入在终态提交前换绑或内容漂移")


def _retained_manifest(out_dir, retained_inputs):
    return [{
        "role": item["role"],
        "path": os.path.relpath(item["path"], out_dir),
        "sha256": item["sha256"],
    } for item in retained_inputs]


def _source_facts_from_retained(retained):
    envelope = retained["payload"]
    if not isinstance(envelope, dict):
        raise PreExecutionFailureError("source_facts envelope 须为 object")
    payload = envelope.get("payload")
    try:
        digest = content_address.content_digest(
            source_facts_lookup.SOURCE_FACTS_DOMAIN, payload)
    except content_address.ContentAddressError as ex:
        raise PreExecutionFailureError(f"source_facts envelope 非法：{ex}") from ex
    if (envelope.get("schema_version") != 1
            or envelope.get("domain") != source_facts_lookup.SOURCE_FACTS_DOMAIN
            or envelope.get("digest") != digest):
        raise PreExecutionFailureError("source_facts envelope 摘要不可信")
    import validate_preparation_state
    try:
        validate_preparation_state._validate_source_payload(payload)
    except content_address.ContentAddressError as ex:
        raise PreExecutionFailureError(f"source_facts payload 不可信：{ex}") from ex
    return payload, envelope["digest"]


def _canonical_sha(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def attempt_digest(attempt):
    """返回排除自签字段后的 canonical SHA。"""
    if not isinstance(attempt, dict):
        raise PreExecutionFailureError("vendor build attempt 须为 JSON object")
    return _canonical_sha({key: value for key, value in attempt.items()
                           if key != "attempt_sha256"})


def classify_failure(stage, text):
    """兼容解释器：只供旧异常/人读诊断；生产路径优先消费异常的结构化 code。"""
    exact = (
        ("build returncode=", "BUILD_NONZERO"),
        ("ELF 仍不存在", "ELF_ABSENT"),
        ("ELF 在这次构建窗口内一个字节都没变", "ELF_UNCHANGED"),
        ("PACKAGE_OPP_ROOT_MISSING:", "PACKAGE_ROOT_NOT_FOUND"),
        ("PACKAGE_ROOT_NOT_FOUND:", "PACKAGE_ROOT_NOT_FOUND"),
        ("PACKAGE_OPP_ROOT_AMBIGUOUS:", "PACKAGE_ROOT_AMBIGUOUS"),
        ("PACKAGE_ROOT_AMBIGUOUS:", "PACKAGE_ROOT_AMBIGUOUS"),
        ("OPS_INFO_MISSING:", "OPS_INFO_MISSING"),
        ("TARGET_SOC_MISMATCH:", "TARGET_SOC_MISMATCH"),
        ("TARGET_OP_MISMATCH:", "TARGET_OP_MISMATCH"),
        ("build 把被测子树改掉", "SOURCE_SUBTREE_DRIFT"),
    )
    for needle, code in exact:
        if needle in str(text):
            return code
    return {
        "build": "BUILD_FAILED",
        "package_resolution": "PACKAGE_RESOLUTION_FAILED",
        "target_closure": "TARGET_CLOSURE_FAILED",
        "source_post_build": "SOURCE_POST_BUILD_FAILED",
        "receipt_preflight": "LIVE_RECEIPT_PREFLIGHT_FAILED",
    }.get(stage, "PRE_EXECUTION_FAILED")


def _snapshot_from_digest(snapshot_digest):
    if not isinstance(snapshot_digest, dict):
        raise PreExecutionFailureError("vendor attempt 缺 build 前 snapshot digest")
    anchor = snapshot_digest.get("content_anchor")
    identity = snapshot_digest.get("kernel_identity")
    try:
        kernel_identity.validate(identity, content_anchor=anchor, require_exact=True)
    except kernel_identity.KernelIdentityError as ex:
        raise PreExecutionFailureError(f"snapshot kernel identity 非法：{ex}") from ex
    if (snapshot_digest.get("schema") != "oprunway.source_snapshot_digest"
            or snapshot_digest.get("schema_version") != 1
            or snapshot_digest.get("taken_stage") != "pre_build"
            or not isinstance(anchor, dict)
            or not _HEX64.fullmatch(snapshot_digest.get("snapshot_sha256") or "")
            or not _HEX64.fullmatch(
                snapshot_digest.get("snapshot_subtree_sha256") or "")
            or snapshot_digest.get("subtree_scope") != anchor.get("scope")):
        raise PreExecutionFailureError("vendor attempt 的 build 前 snapshot envelope/anchor 非法")
    return {
        "provenance_kind": "local_snapshot",
        "snapshot_sha256": snapshot_digest["snapshot_sha256"],
        "snapshot_subtree_sha256": snapshot_digest["snapshot_subtree_sha256"],
        "snapshot_subtree_scope": snapshot_digest["subtree_scope"],
        "content_anchor": copy.deepcopy(anchor),
        "kernel_identity": copy.deepcopy(identity),
    }


def _snapshot_from_facts(source_facts):
    pr = source_facts.get("pr") if isinstance(source_facts, dict) else None
    derived = source_facts.get("derived") if isinstance(source_facts, dict) else None
    anchor = pr.get("content_anchor") if isinstance(pr, dict) else None
    identity = derived.get("kernel_identity") if isinstance(derived, dict) else None
    try:
        kernel_identity.validate(identity, content_anchor=anchor, require_exact=True)
    except kernel_identity.KernelIdentityError as ex:
        raise PreExecutionFailureError(f"CP-A kernel identity 非法：{ex}") from ex
    return {
        "provenance_kind": pr.get("provenance_kind"),
        "snapshot_sha256": None,
        "snapshot_subtree_sha256": pr.get("snapshot_merkle_sha256"),
        "snapshot_subtree_scope": pr.get("snapshot_scope"),
        "content_anchor": copy.deepcopy(anchor),
        "kernel_identity": copy.deepcopy(identity),
    }


def build_vendor_attempt(*, snapshot_digest, target_request, failure_stage,
                         error_code, error_text, declared_source_form=None,
                         build_result=None, failure_subject="vendor_emit",
                         trusted_context=None, failed_claim=None):
    """构造 vendor emit 失败工件；调用方须已通过 build 前树对账。"""
    source = _snapshot_from_digest(snapshot_digest)
    context = trusted_context or {
        "basis": "vendor_emit_prebuild",
        "snapshot_digest_valid": True,
        "pre_build_tree_matched": True,
    }
    return _build_attempt(
        source_snapshot=source, target_request=target_request,
        failure_stage=failure_stage, error_code=error_code, error_text=error_text,
        declared_source_form=declared_source_form, build_result=build_result,
        failure_subject=failure_subject, trusted_context=context,
        failed_claim=failed_claim)


def build_live_preflight_attempt(*, source_facts, target_request, error_text,
                                 failed_claim=None):
    """构造 live receipt preflight 失败工件；可信的是 CP-A/spec，不是失败 receipt claim。"""
    return _build_attempt(
        source_snapshot=_snapshot_from_facts(source_facts),
        target_request=target_request,
        failure_stage="receipt_preflight",
        error_code="LIVE_RECEIPT_PREFLIGHT_FAILED",
        error_text=error_text, declared_source_form=source_facts.get(
            "declared_source_form"), build_result=None,
        failure_subject="live_receipt_preflight",
        trusted_context={
            "basis": "workflow_current_inputs",
            "cp_a_source_facts_valid": True,
            "spec_binding_valid": True,
            "failed_receipt_claim_trusted": False,
        }, failed_claim=failed_claim)


def _build_attempt(*, source_snapshot, target_request, failure_stage, error_code,
                   error_text, declared_source_form, build_result,
                   failure_subject, trusted_context, failed_claim):
    if not isinstance(target_request, dict) or not isinstance(
            target_request.get("expected_op_type"), str):
        raise PreExecutionFailureError("vendor attempt target_request 缺 expected_op_type")
    if not isinstance(error_text, str) or not error_text.strip() \
            or not isinstance(error_code, str) or not _CODE.fullmatch(error_code):
        raise PreExecutionFailureError("vendor attempt failure code/text 非法")
    attempt = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "status": STATUS,
        "formal_eligible": False, "acceptance_verdict": None,
        "failure_subject": failure_subject,
        "failure": {"stage": failure_stage, "error_code": error_code,
                    "error_text": _redacted_error(failure_stage, error_code)},
        "source_snapshot": copy.deepcopy(source_snapshot),
        "source_transport": {"declared_source_form": declared_source_form},
        "target_request": copy.deepcopy(target_request),
        "trusted_context": copy.deepcopy(trusted_context),
        "build": _safe_build_facts(build_result, failure_stage, error_code),
        "failed_claim": copy.deepcopy(failed_claim),
        "producer": {"tool": _PRODUCER_TOOL,
                     "logic_sha256": _sha256_file(os.path.abspath(__file__))},
    }
    attempt["attempt_sha256"] = attempt_digest(attempt)
    validate_vendor_attempt(attempt)
    return attempt


def _safe_build_facts(build_result, failure_stage, error_code):
    """只持久化实测且可审的安全子集；完整 argv/env 不进入可转发产物。"""
    if build_result is None:
        return None
    if not isinstance(build_result, dict):
        raise PreExecutionFailureError("build_result 须为 object 或 null")
    argv = build_result.get("argv")
    execution = build_result.get("execution")
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv):
        raise PreExecutionFailureError("build_result.argv 非实参数组")
    if not isinstance(execution, dict):
        raise PreExecutionFailureError("build_result.execution 缺失")
    argv_sha = _canonical_sha(argv)
    safe_execution = {
        key: copy.deepcopy(execution.get(key))
        for key in ("started_at", "ended_at", "duration_s", "library_path",
                    "library_before", "library_after") if key in execution
    }
    safe = {
        "argv_sha256": argv_sha,
        "executable_basename": os.path.basename(argv[0]),
        "argument_count": len(argv),
        "cwd": build_result.get("cwd"),
        "returncode": build_result.get("returncode"),
        "returncode_source": build_result.get("returncode_source"),
        "execution": safe_execution,
    }
    # 在计算 attempt digest 之前校原始实测事实的安全子集。否则 NaN 等
    # 非 JSON 值会从 canonicalizer 泄漏成无类型异常，生产端也无法保证自己
    # 写出的内容满足消费端契约。
    _validate_safe_build(safe, failure_stage, error_code)
    return safe


def _redacted_error(stage, code):
    return f"{code}: {stage} 受控失败（详细日志仅保留在执行控制台）"


def _nonbool_int(value, *, minimum=0):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _library_state(value, *, allow_none):
    if value is None:
        return allow_none
    return (isinstance(value, dict)
            and set(value) == {"mtime_ns", "size", "sha256"}
            and _nonbool_int(value.get("mtime_ns"))
            and _nonbool_int(value.get("size"))
            and _HEX64.fullmatch(value.get("sha256") or "") is not None)


def _validate_safe_build(build, stage, code):
    if stage not in _CONTROLLED_STAGES:
        raise PreExecutionFailureError("vendor attempt failure stage 非受控值")
    requires_absent = (stage in {"target_preflight", "receipt_preflight"}
                       or code == "BUILD_EXECUTION_ERROR")
    requires_measured = (stage in {
        "build", "package_resolution", "target_closure", "source_post_build",
        "vendor_receipt"} and code != "BUILD_EXECUTION_ERROR")
    if build is None:
        if requires_measured:
            raise PreExecutionFailureError(
                f"vendor attempt {stage}/{code} 缺实测 build facts")
        return
    if requires_absent:
        raise PreExecutionFailureError(
            f"vendor attempt {stage}/{code} 不得携带伪造的 build facts")
    expected = {"argv_sha256", "executable_basename", "argument_count", "cwd",
                "returncode", "returncode_source", "execution"}
    execution = build.get("execution")
    execution_keys = {"started_at", "ended_at", "duration_s", "library_path",
                      "library_before", "library_after"}
    duration = execution.get("duration_s") if isinstance(execution, dict) else None
    if (set(build) != expected
            or not _HEX64.fullmatch(build.get("argv_sha256") or "")
            or not isinstance(build.get("executable_basename"), str)
            or not build["executable_basename"]
            or os.path.basename(build["executable_basename"]) != build["executable_basename"]
            or not _nonbool_int(build.get("argument_count"), minimum=1)
            or not isinstance(build.get("cwd"), str) or not build["cwd"]
            or not isinstance(build.get("returncode"), int)
            or isinstance(build.get("returncode"), bool)
            or build.get("returncode_source") != "measured"
            or not isinstance(execution, dict) or set(execution) != execution_keys
            or not _RFC3339.fullmatch(execution.get("started_at") or "")
            or not _RFC3339.fullmatch(execution.get("ended_at") or "")
            or isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration < 0
            or not isinstance(execution.get("library_path"), str)
            or not execution["library_path"]
            or not _library_state(execution.get("library_before"), allow_none=True)
            or not _library_state(execution.get("library_after"), allow_none=True)):
        raise PreExecutionFailureError("vendor attempt build safe facts 非严格实测结构")
    if code == "ELF_ABSENT" and execution["library_after"] is not None:
        raise PreExecutionFailureError("ELF_ABSENT 与 library_after 状态冲突")
    if code == "ELF_UNCHANGED" and execution["library_before"] != execution["library_after"]:
        raise PreExecutionFailureError("ELF_UNCHANGED 与前后 ELF 状态冲突")
    if code == "BUILD_NONZERO" and build["returncode"] == 0:
        raise PreExecutionFailureError("BUILD_NONZERO 与实测 returncode=0 冲突")
    if stage in {"package_resolution", "target_closure", "source_post_build",
                 "vendor_receipt"} and build["returncode"] != 0:
        raise PreExecutionFailureError(
            f"{stage} 只可能发生在 build returncode=0 之后")


def validate_vendor_attempt(attempt):
    expected_top = {
        "schema", "schema_version", "status", "formal_eligible",
        "acceptance_verdict", "failure_subject", "failure", "source_snapshot",
        "source_transport", "target_request", "trusted_context", "build",
        "failed_claim", "producer", "attempt_sha256",
    }
    if (not isinstance(attempt, dict) or set(attempt) != expected_top
            or attempt.get("schema") != SCHEMA
            or attempt.get("schema_version") != SCHEMA_VERSION
            or attempt.get("status") != STATUS
            or attempt.get("formal_eligible") is not False
            or attempt.get("acceptance_verdict") is not None
            or attempt.get("attempt_sha256") != attempt_digest(attempt)):
        raise PreExecutionFailureError("vendor build attempt envelope/digest 非法")
    producer = attempt.get("producer")
    if (not isinstance(producer, dict) or set(producer) != {"tool", "logic_sha256"}
            or producer.get("tool") != _PRODUCER_TOOL
            or producer.get("logic_sha256") != _sha256_file(os.path.abspath(__file__))):
        raise PreExecutionFailureError("vendor build attempt producer/plugin logic 未绑定当前实现")
    source = attempt.get("source_snapshot")
    if (not isinstance(source, dict) or set(source) != {
            "provenance_kind", "snapshot_sha256", "snapshot_subtree_sha256",
            "snapshot_subtree_scope", "content_anchor", "kernel_identity"}):
        raise PreExecutionFailureError("vendor build attempt 缺 source_snapshot")
    try:
        candidate = kernel_identity.validate(
            source.get("kernel_identity"), content_anchor=source.get("content_anchor"),
            require_exact=True)
    except kernel_identity.KernelIdentityError as ex:
        raise PreExecutionFailureError(f"vendor attempt source identity 非法：{ex}") from ex
    target = attempt.get("target_request")
    if (not isinstance(target, dict)
            or target.get("expected_op_type") != candidate["kernel_op_type"]):
        raise PreExecutionFailureError(
            "vendor attempt target expected_op_type 与 source kernel identity 不一致")
    failure = attempt.get("failure")
    if (not isinstance(failure, dict)
            or set(failure) != {"stage", "error_code", "error_text"}
            or not isinstance(failure.get("stage"), str)
            or not _CODE.fullmatch(failure.get("error_code") or "")
            or not isinstance(failure.get("error_text"), str)
            or failure["error_text"] != _redacted_error(
                failure.get("stage"), failure.get("error_code"))):
        raise PreExecutionFailureError("vendor attempt failure 结构非法")
    subject = attempt.get("failure_subject")
    context = attempt.get("trusted_context")
    transport = attempt.get("source_transport")
    if not isinstance(transport, dict) or set(transport) != {"declared_source_form"}:
        raise PreExecutionFailureError("vendor attempt source_transport 结构非法")
    if subject == "vendor_emit":
        if context != {"basis": "vendor_emit_prebuild",
                       "snapshot_digest_valid": True,
                       "pre_build_tree_matched": True}:
            raise PreExecutionFailureError(
                "vendor emit attempt 未证明 snapshot-digest 与 build 前实际树已对账")
        required_target = {
            "requested_soc", "selected_op", "expected_op_type",
            "installed_opp_root", "cmake_cache_path"}
        package_keys = set(target) & {"package_opp_root", "package_search_root"}
        if (set(target) != required_target | package_keys or len(package_keys) != 1
                or attempt.get("failed_claim") is not None):
            raise PreExecutionFailureError("vendor emit target/failed_claim 结构非法")
    elif subject == "live_receipt_preflight":
        if context != {"basis": "workflow_current_inputs",
                       "cp_a_source_facts_valid": True,
                       "spec_binding_valid": True,
                       "failed_receipt_claim_trusted": False}:
            raise PreExecutionFailureError(
                "live preflight attempt 混淆可信 CP-A/spec 与失败 receipt claim")
        claim = attempt.get("failed_claim")
        if (set(target) != {"expected_op_type"}
                or not isinstance(claim, dict)
                or set(claim) != {"kind", "trusted", "sha256"}
                or claim.get("kind") != "vendor_build_receipt_claim"
                or claim.get("trusted") is not False
                or (claim.get("sha256") is not None
                    and not _HEX64.fullmatch(claim.get("sha256")))):
            raise PreExecutionFailureError("live preflight target/failed_claim 结构非法")
    else:
        raise PreExecutionFailureError("vendor attempt failure_subject 非受控值")
    build = attempt.get("build")
    _validate_safe_build(build, failure["stage"], failure["error_code"])
    return attempt


def write_vendor_attempt(path, attempt):
    validate_vendor_attempt(attempt)
    try:
        guard = artifact_path_guard.prepare_parent(path)
        artifact_path_guard.assert_leaf_safe(path)
        artifact_path_guard.assert_stable(guard)
    except artifact_path_guard.ArtifactPathError as ex:
        raise PreExecutionFailureError(str(ex)) from ex
    # 复用内容无关的原子写原语；失败时不会留下半 JSON。
    import vendor_build_receipt
    vendor_build_receipt.atomic_write(path, attempt)
    return path


def _read_spec(path):
    if os.path.islink(path):
        raise PreExecutionFailureError("spec 原件不得是符号链接")
    try:
        with open(path, "rb") as src:
            raw = src.read()
        spec = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise PreExecutionFailureError(f"spec 原件不可读：{ex}") from ex
    if not isinstance(spec, dict):
        raise PreExecutionFailureError("spec 顶层须为 object")
    return spec, hashlib.sha256(raw).hexdigest()


def _validate_bindings(spec, facts, attempt):
    validate_vendor_attempt(attempt)
    try:
        identity = kernel_identity.resolve(spec, facts, require_explicit=True)
    except kernel_identity.KernelIdentityError as ex:
        raise PreExecutionFailureError(f"spec↔CP-A execution identity 非法：{ex}") from ex
    source = attempt["source_snapshot"]
    facts_source = _snapshot_from_facts(facts)
    if (source.get("provenance_kind") != facts_source.get("provenance_kind")
            or source.get("content_anchor") != facts_source.get("content_anchor")
            or source.get("kernel_identity") != facts_source.get("kernel_identity")
            or source.get("snapshot_subtree_sha256")
            != facts_source.get("snapshot_subtree_sha256")
            or source.get("snapshot_subtree_scope")
            != facts_source.get("snapshot_subtree_scope")):
        raise PreExecutionFailureError("vendor attempt source snapshot 与原始 CP-A source_facts 漂移")
    if attempt["target_request"].get("expected_op_type") != identity["kernel_op_type"]:
        raise PreExecutionFailureError("vendor attempt target 与 spec public/internal identity 漂移")
    form = (attempt.get("source_transport") or {}).get("declared_source_form")
    if form != facts.get("declared_source_form"):
        raise PreExecutionFailureError("vendor attempt provenance declaration 与 CP-A 漂移")
    return identity


def _remove_downstream(transaction):
    for name in _DOWNSTREAM_FILES:
        if transaction.lexists(name):
            try:
                transaction.unlink(name)
            except OSError as ex:
                raise PreExecutionFailureError(
                    f"清不掉 stale downstream artifact {name!r}：{ex}") from ex


def finalize(*, out_dir, spec, spec_sha256, source_facts, source_facts_digest,
             vendor_attempt, trusted_paths=(), retained_inputs=()):
    """在同一 artifact lock 下清旧 downstream，写 payload，最后提交 marker。"""
    if (not _HEX64.fullmatch(spec_sha256 or "")
            or not _HEX64.fullmatch(source_facts_digest or "")):
        raise PreExecutionFailureError(
            "pre-execution finalizer 缺可信 spec/source_facts envelope 摘要")
    identity = _validate_bindings(spec, source_facts, vendor_attempt)
    try:
        guard = artifact_path_guard.prepare_report_root(
            out_dir, trusted_paths,
            mutation_paths=_controlled_terminal_paths(out_dir))
    except artifact_path_guard.ArtifactPathError as ex:
        raise PreExecutionFailureError(str(ex)) from ex
    out_dir = guard["path"]
    failure = vendor_attempt["failure"]
    target_delivery_failure = failure["stage"] in {
        "target_preflight", "package_resolution", "target_closure",
        "receipt_preflight"}
    state = ("BLOCKED_TARGET_KERNEL_DELIVERY_CLOSURE"
             if target_delivery_failure else "BLOCKED_PRE_EXECUTION")
    overall = ("BLOCKED(target kernel delivery closure 未通过)"
               if target_delivery_failure else "BLOCKED(pre-execution failure)")
    binding = {
        "vendor_attempt_sha256": vendor_attempt["attempt_sha256"],
        "spec_sha256": spec_sha256,
        "source_facts_digest": source_facts_digest,
        "stage": failure["stage"], "error_code": failure["error_code"],
        "failure_subject": vendor_attempt["failure_subject"],
    }
    candidate = {
        "op": spec.get("op"), "execution_identity": identity,
        "repo_mode": "cpp_extension",
        "overall": overall, "state": state, "exit_code": 1,
        "requires_human_cp": False,
        "gate": {"passed": False, "errors": {
            "pre_execution": [failure["error_text"]]}},
        "diagnostic_sources": {},
        "pre_execution_failure": binding,
    }
    record = acceptance_artifacts.build_attempt_record(candidate)
    with _artifact_transaction(out_dir, guard) as transaction:
        _verify_retained_inputs(retained_inputs)
        _remove_downstream(transaction)
        vendor_path = acceptance_artifacts._atomic_write_json(
            transaction, VENDOR_ATTEMPT_FILE, vendor_attempt)
        detail = (
            "# 前置执行失败明细（非正式验收报告）\n\n"
            "本轮在 Task1、golden/caseset、外部 driver、DUT 调用和 profiler 之前停止。\n\n"
            "- `formal_eligible = false`\n"
            "- `acceptance_verdict = null`\n"
            "- 本文件不得命名、引用或渲染为正式验收报告。\n"
            f"- 阶段：`{failure['stage']}`\n"
            f"- 错误码：`{failure['error_code']}`\n"
            f"- 失败详情：{failure['error_text']}\n")
        record["diagnostic_sources"] = {
            "pre_execution_failure": {
                "kind": "non_formal_chinese_detail", "path": DETAIL_REPORT_FILE,
                "sha256": hashlib.sha256(detail.encode("utf-8")).hexdigest(),
            },
            "vendor_build_attempt": {
                "kind": "vendor_build_attempt", "path": VENDOR_ATTEMPT_FILE,
                "sha256": transaction.sha256(VENDOR_ATTEMPT_FILE),
            },
        }
        detail_path = _atomic_write_text(transaction, DETAIL_REPORT_FILE, detail)
        attempt_path = acceptance_artifacts._atomic_write_json(
            transaction, acceptance_artifacts.ATTEMPT_RECORD_FILE, record)
        _verify_retained_inputs(retained_inputs)
        marker = {
            "schema": TERMINAL_SCHEMA, "schema_version": TERMINAL_VERSION,
            "status": "blocked_pre_execution", "formal_eligible": False,
            "acceptance_verdict": None, "op": spec.get("op"),
            "execution_identity": identity, "binding": binding,
            "retained_inputs": _retained_manifest(out_dir, retained_inputs),
            "artifacts": {
                "vendor_build_attempt": {
                    "path": VENDOR_ATTEMPT_FILE,
                    "sha256": transaction.sha256(VENDOR_ATTEMPT_FILE)},
                "detail_report": {
                    "path": DETAIL_REPORT_FILE,
                    "sha256": transaction.sha256(DETAIL_REPORT_FILE)},
                "attempt_record": {
                    "path": acceptance_artifacts.ATTEMPT_RECORD_FILE,
                    "sha256": transaction.sha256(
                        acceptance_artifacts.ATTEMPT_RECORD_FILE)},
            },
        }
        acceptance_artifacts._atomic_write_json(
            transaction, acceptance_artifacts.PRE_EXECUTION_TERMINAL_FILE, marker)
        _verify_retained_inputs(retained_inputs)
    return {
        "attempt_record": attempt_path,
        "detail_report": os.path.join(out_dir, DETAIL_REPORT_FILE),
        "terminal_marker": os.path.join(
            out_dir, acceptance_artifacts.PRE_EXECUTION_TERMINAL_FILE),
        "overall": overall, "state": state,
    }


def failed_claim_from_path(path):
    """只记录失败对象字节 SHA，不把其自报字段升级为可信绑定。"""
    if not isinstance(path, str) or not path or os.path.islink(path) \
            or not os.path.isfile(path):
        return {"kind": "vendor_build_receipt_claim", "trusted": False,
                "sha256": None}
    return {"kind": "vendor_build_receipt_claim", "trusted": False,
            "sha256": _sha256_file(path)}


def _atomic_write_text(transaction, filename, text):
    return transaction.atomic_write_bytes(filename, text.encode("utf-8"))


def validate_terminal_marker(out_dir):
    marker_path = os.path.join(
        out_dir, acceptance_artifacts.PRE_EXECUTION_TERMINAL_FILE)
    if os.path.islink(marker_path):
        raise PreExecutionFailureError("pre-execution terminal marker 不得是符号链接")
    try:
        with open(marker_path, encoding="utf-8") as src:
            marker = json.load(src)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise PreExecutionFailureError(f"pre-execution terminal marker 不可读：{ex}") from ex
    artifacts = marker.get("artifacts") if isinstance(marker, dict) else None
    expected = {
        "vendor_build_attempt": VENDOR_ATTEMPT_FILE,
        "detail_report": DETAIL_REPORT_FILE,
        "attempt_record": acceptance_artifacts.ATTEMPT_RECORD_FILE,
    }
    expected_marker = {
        "schema", "schema_version", "status", "formal_eligible",
        "acceptance_verdict", "op", "execution_identity", "binding", "artifacts",
        "retained_inputs"}
    binding = marker.get("binding") if isinstance(marker, dict) else None
    retained_inputs = marker.get("retained_inputs") if isinstance(marker, dict) else None
    if (not isinstance(marker, dict) or set(marker) != expected_marker
            or marker.get("schema") != TERMINAL_SCHEMA
            or marker.get("schema_version") != TERMINAL_VERSION
            or marker.get("status") != "blocked_pre_execution"
            or marker.get("formal_eligible") is not False
            or marker.get("acceptance_verdict") is not None
            or not isinstance(marker.get("op"), str) or not marker["op"]
            or not isinstance(marker.get("execution_identity"), dict)
            or not isinstance(binding, dict) or set(binding) != {
                "vendor_attempt_sha256", "spec_sha256", "source_facts_digest",
                "stage", "error_code", "failure_subject"}
            or any(not _HEX64.fullmatch(binding.get(key) or "") for key in (
                "vendor_attempt_sha256", "spec_sha256", "source_facts_digest"))
            or not isinstance(retained_inputs, list)
            or len(retained_inputs) not in {2, 3}
            or not isinstance(artifacts, dict) or set(artifacts) != set(expected)):
        raise PreExecutionFailureError("pre-execution terminal commit manifest 非法")
    roles = []
    retained_by_role = {}
    for item in retained_inputs:
        if (not isinstance(item, dict)
                or set(item) != {"role", "path", "sha256"}
                or item.get("role") not in {
                    "spec", "source_facts", "vendor_build_attempt"}
                or not isinstance(item.get("path"), str)
                or not item["path"] or os.path.isabs(item["path"])
                or os.path.normpath(item["path"]) != item["path"]
                or not _HEX64.fullmatch(item.get("sha256") or "")):
            raise PreExecutionFailureError(
                "pre-execution retained trusted input manifest 非法")
        roles.append(item["role"])
        retained_path = os.path.abspath(os.path.join(out_dir, item["path"]))
        current = _read_retained_json(
            retained_path, role=item["role"], report_root=out_dir)
        if current["sha256"] != item["sha256"]:
            raise PreExecutionFailureError(
                f"pre-execution retained 可信输入 {item['role']} 摘要漂移")
        retained_by_role[item["role"]] = current
    if (len(set(roles)) != len(roles)
            or set(roles) not in (
                {"spec", "source_facts"},
                {"spec", "source_facts", "vendor_build_attempt"})):
        raise PreExecutionFailureError(
            "pre-execution retained trusted input roles 非法")
    retained_spec = retained_by_role["spec"]
    if retained_spec["sha256"] != binding["spec_sha256"]:
        raise PreExecutionFailureError(
            "pre-execution retained spec raw SHA 与 binding.spec_sha256 漂移")
    retained_facts, retained_facts_digest = _source_facts_from_retained(
        retained_by_role["source_facts"])
    if retained_facts_digest != binding["source_facts_digest"]:
        raise PreExecutionFailureError(
            "pre-execution retained source_facts digest 与 binding 漂移")
    retained_spec_payload = retained_spec["payload"]
    if not isinstance(retained_spec_payload, dict):
        raise PreExecutionFailureError("pre-execution retained spec 须为 object")
    try:
        retained_identity = kernel_identity.resolve(
            retained_spec_payload, retained_facts, require_explicit=True)
    except kernel_identity.KernelIdentityError as ex:
        raise PreExecutionFailureError(
            f"pre-execution retained spec/facts execution identity 非法：{ex}") from ex
    if (retained_spec_payload.get("op") != marker["op"]
            or retained_identity != marker["execution_identity"]):
        raise PreExecutionFailureError(
            "pre-execution retained spec/facts 与 marker op/execution identity 漂移")
    for key, filename in expected.items():
        item = artifacts.get(key)
        path = os.path.join(out_dir, filename)
        if (not isinstance(item, dict) or set(item) != {"path", "sha256"}
                or item.get("path") != filename
                or not _HEX64.fullmatch(item.get("sha256") or "")
                or os.path.islink(path) or not os.path.isfile(path)
                or _sha256_file(path) != item["sha256"]):
            raise PreExecutionFailureError(
                f"pre-execution terminal artifact {key} 缺失或摘要漂移")
    try:
        with open(os.path.join(out_dir, VENDOR_ATTEMPT_FILE), encoding="utf-8") as src:
            vendor = json.load(src)
        with open(os.path.join(
                out_dir, acceptance_artifacts.ATTEMPT_RECORD_FILE), encoding="utf-8") as src:
            record = json.load(src)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise PreExecutionFailureError(f"pre-execution terminal payload 不可解析：{ex}") from ex
    validate_vendor_attempt(vendor)
    retained_vendor = retained_by_role.get("vendor_build_attempt")
    if retained_vendor is not None and retained_vendor["payload"] != vendor:
        raise PreExecutionFailureError(
            "pre-execution retained vendor attempt 与 terminal payload 漂移")
    source = vendor["source_snapshot"]
    try:
        candidate = kernel_identity.validate(
            source["kernel_identity"],
            content_anchor=source["content_anchor"], require_exact=True)
        expected_source_binding = kernel_identity.spec_execution(
            source["kernel_identity"])["source_binding"]
    except kernel_identity.KernelIdentityError as ex:
        raise PreExecutionFailureError(
            f"pre-execution terminal execution identity 非法：{ex}") from ex
    identity = marker["execution_identity"]
    expected_identity = {
        "public_op": marker["op"],
        "kernel_op_type": candidate["kernel_op_type"],
        "resolution": "explicit_spec_binding",
        "source_binding": expected_source_binding,
    }
    diagnostics = record.get("diagnostic_sources") if isinstance(record, dict) else None
    if (not isinstance(record, dict)
            or record.get("schema") != acceptance_artifacts.ATTEMPT_RECORD_SCHEMA
            or record.get("schema_version")
            != acceptance_artifacts.ATTEMPT_RECORD_SCHEMA_VERSION
            or record.get("formal_eligible") is not False
            or record.get("acceptance_verdict") is not None
            or record.get("op") != marker["op"]
            or identity != expected_identity
            or record.get("execution_identity") != identity
            or record.get("pre_execution_failure") != binding
            or vendor.get("attempt_sha256") != binding["vendor_attempt_sha256"]
            or vendor["failure"]["stage"] != binding["stage"]
            or vendor["failure"]["error_code"] != binding["error_code"]
            or vendor["failure_subject"] != binding["failure_subject"]
            or not isinstance(diagnostics, dict)
            or set(diagnostics) != {"pre_execution_failure", "vendor_build_attempt"}
            or diagnostics["pre_execution_failure"] != {
                "kind": "non_formal_chinese_detail", "path": DETAIL_REPORT_FILE,
                "sha256": artifacts["detail_report"]["sha256"]}
            or diagnostics["vendor_build_attempt"] != {
                "kind": "vendor_build_attempt", "path": VENDOR_ATTEMPT_FILE,
                "sha256": artifacts["vendor_build_attempt"]["sha256"]}):
        raise PreExecutionFailureError(
            "pre-execution terminal marker 与 vendor/attempt/detail 交叉绑定非法")
    return marker


def finalize_from_files(*, out_dir, spec_path, source_facts_path,
                        vendor_attempt, vendor_attempt_path=None):
    boundary_inputs = [spec_path, source_facts_path]
    if vendor_attempt_path is not None:
        boundary_inputs.append(vendor_attempt_path)
    try:
        artifact_path_guard.assert_report_root_not_within_trusted(
            out_dir, boundary_inputs)
    except artifact_path_guard.ArtifactPathError as ex:
        raise PreExecutionFailureError(str(ex)) from ex
    spec_retained = _read_retained_json(
        spec_path, role="spec", report_root=out_dir)
    spec = spec_retained["payload"]
    if not isinstance(spec, dict):
        raise PreExecutionFailureError("spec 顶层须为 object")
    spec_sha = spec_retained["sha256"]
    facts_retained = _read_retained_json(
        source_facts_path, role="source_facts", report_root=out_dir)
    facts, facts_digest = _source_facts_from_retained(facts_retained)
    retained_inputs = [spec_retained, facts_retained]
    trusted_paths = [spec_path, source_facts_path]
    if vendor_attempt_path is not None:
        vendor_retained = _read_retained_json(
            vendor_attempt_path, role="vendor_build_attempt",
            report_root=out_dir)
        persisted_attempt = vendor_retained["payload"]
        if vendor_attempt is not None and persisted_attempt != vendor_attempt:
            raise PreExecutionFailureError(
                "vendor build attempt 内存对象与可信原件漂移")
        vendor_attempt = persisted_attempt
        trusted_paths.append(vendor_attempt_path)
        retained_inputs.append(vendor_retained)
    if not isinstance(vendor_attempt, dict):
        raise PreExecutionFailureError("vendor build attempt 可信输入缺失")
    return finalize(
        out_dir=out_dir, spec=spec, spec_sha256=spec_sha,
        source_facts=facts, source_facts_digest=facts_digest,
        vendor_attempt=vendor_attempt,
        trusted_paths=tuple(trusted_paths),
        retained_inputs=tuple(retained_inputs))


def main(argv=None):
    ap = argparse.ArgumentParser(description="将 CP-C vendor failure 收口成标准不可发布 attempt")
    ap.add_argument("--failure-attempt", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--source-facts", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    result = finalize_from_files(
        out_dir=args.out, spec_path=args.spec,
        source_facts_path=args.source_facts, vendor_attempt=None,
        vendor_attempt_path=args.failure_attempt)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, PreExecutionFailureError) as ex:
        print(f"[pre-execution-finalizer] REFUSED: {ex}", file=os.sys.stderr)
        raise SystemExit(2)
