"""The only deterministic acceptance verdict producer."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from .atk import (
    _case_filter,
    _coverage,
    _generated_case_projection,
    _task_case_bundle_evidence,
    _validate_cases,
)
from .build import _cache_matches_request, _cache_values, _target_delivery, _vendor_root
from .contract import spec_digest, validate_spec
from .source import build_input_anchor
from .util import (
    WorkflowError,
    atomic_write_json,
    load_json,
    require_directory,
    require_plain_file,
    sha256_file,
    utc_now,
)


_NO_EXECUTION_BUNDLE = object()


def _receipt(path: os.PathLike[str] | str, schema: str) -> tuple[dict[str, Any], str]:
    value = load_json(path)
    if not isinstance(value, dict) or value.get("schema") != schema or value.get("schema_version") != 1:
        raise WorkflowError("EVIDENCE_INCOMPLETE", f"expected {schema} v1: {path}")
    return value, sha256_file(path)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError("EVIDENCE_INCOMPLETE", f"{label} must be an object")
    return value


def _evidence_file(item: Any, label: str) -> Path:
    if not isinstance(item, dict) or not isinstance(item.get("path"), str) \
            or not isinstance(item.get("sha256"), str):
        raise WorkflowError("EVIDENCE_INCOMPLETE", f"{label} file identity is missing")
    path = require_plain_file(item["path"], label)
    if sha256_file(path) != item["sha256"]:
        raise WorkflowError("EVIDENCE_DRIFT", f"{label} changed after receipt creation")
    size = item.get("size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0
                             or path.stat().st_size != size):
        raise WorkflowError("EVIDENCE_DRIFT", f"{label} size differs from its receipt")
    if label.startswith("ATK output") and path.stat().st_size == 0:
        raise WorkflowError("EVIDENCE_INCOMPLETE", f"{label} is empty")
    return path


def _successful_command(value: Any, label: str) -> dict[str, Any]:
    command = _object(value, f"{label} command")
    argv = command.get("argv")
    elapsed = command.get("elapsed_seconds")
    if not isinstance(argv, list) or not argv \
            or any(not isinstance(item, str) or not item for item in argv) \
            or not isinstance(command.get("cwd"), str) or not command["cwd"] \
            or command.get("returncode") != 0 or command.get("timed_out") is not False \
            or command.get("processes_drained") is not True \
            or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) \
            or not math.isfinite(float(elapsed)) or elapsed < 0:
        raise WorkflowError("EVIDENCE_INCOMPLETE", f"{label} command did not complete successfully")
    _evidence_file(
        {"path": command.get("stdout_path"), "sha256": command.get("stdout_sha256")},
        f"{label} stdout",
    )
    _evidence_file(
        {"path": command.get("stderr_path"), "sha256": command.get("stderr_sha256")},
        f"{label} stderr",
    )
    return command


def _validated_device_binding(
    checked: dict[str, Any],
    cases: dict[str, Any],
    execution: dict[str, Any],
    requested_physical_device: int,
) -> None:
    binding = _object(execution.get("device"), "execution device binding")
    expected_binding = {
        "physical_device": requested_physical_device,
        "logical_device": checked["runner"]["device"],
        "runtime_environment": {
            "ASCEND_RT_VISIBLE_DEVICES": str(requested_physical_device),
        },
    }
    if binding != expected_binding:
        raise WorkflowError(
            "EVIDENCE_MISMATCH",
            "execution device binding differs from the requested physical/logical mapping",
        )

    commands = _object(execution.get("commands"), "ATK execution commands")
    if set(commands) != {"accuracy", "performance"}:
        raise WorkflowError("EVIDENCE_INCOMPLETE", "ATK execution command set is invalid")
    execution_atk = _object(execution.get("atk"), "execution ATK identity")
    atk_path = execution_atk.get("path")
    atk_sha = execution_atk.get("sha256")
    if not isinstance(atk_path, str) or not isinstance(atk_sha, str):
        raise WorkflowError("EVIDENCE_INCOMPLETE", "execution ATK file identity is missing")
    _evidence_file({"path": atk_path, "sha256": atk_sha}, "execution ATK executable")
    case_data = _object(cases.get("cases"), "ATK caseset identity")
    cases_path = case_data.get("path")
    if not isinstance(cases_path, str):
        raise WorkflowError("EVIDENCE_INCOMPLETE", "ATK caseset path is missing")
    work_dir = execution.get("work_dir")
    if not isinstance(work_dir, str) or not work_dir:
        raise WorkflowError("EVIDENCE_INCOMPLETE", "ATK execution work directory is missing")
    work = require_directory(work_dir, "ATK execution work directory")
    plugin_identity = execution.get("execution_plugin")
    plugin_path: str | None = None
    if plugin_identity is not None:
        plugin_path = str(_evidence_file(plugin_identity, "ATK execution plugin"))
    performance_ids = case_data.get("performance_case_ids")
    if not isinstance(performance_ids, list) \
            or any(isinstance(item, bool) or not isinstance(item, int) for item in performance_ids):
        raise WorkflowError("EVIDENCE_INCOMPLETE", "ATK performance case identities are invalid")
    expected_environment = {
        "ASCEND_RT_VISIBLE_DEVICES": str(requested_physical_device),
    }
    for key in ("accuracy", "performance"):
        command = commands.get(key)
        if key == "performance" and not performance_ids:
            if command is not None:
                raise WorkflowError("EVIDENCE_MISMATCH", "unexpected ATK performance command")
            continue
        command = _successful_command(command, f"ATK {key}")
        task = "accuracy" if key == "accuracy" else "performance_device"
        destination = work / ("accuracy" if key == "accuracy" else "performance")
        expected_argv = [
            atk_path, "aclnn", cases_path,
            "--devices", str(checked["runner"]["device"]),
            "--task", task, "--output", str(destination),
            "--timeout", str(checked["runner"]["case_timeout_seconds"]),
            "--concurrency", "1", "--save_data",
            "output:bin" if key == "accuracy" else "profile:bin",
        ]
        if key == "performance":
            expected_argv.extend(["--white_list", _case_filter(performance_ids)])
        if plugin_path is not None:
            expected_argv.extend(["--plugin", str(Path(plugin_path).resolve())])
        if command.get("argv") != expected_argv \
                or command.get("cwd") != str(work) \
                or command.get("stdout_path") != str(destination / "atk-run.stdout.log") \
                or command.get("stderr_path") != str(destination / "atk-run.stderr.log"):
            raise WorkflowError("EVIDENCE_MISMATCH", f"ATK {key} command contract differs")
        if command.get("environment") != expected_environment:
            raise WorkflowError("EVIDENCE_MISMATCH", f"ATK {key} child environment differs")


def _validated_build_common(
    checked: dict[str, Any], facts_source: dict[str, Any], build: dict[str, Any]
) -> dict[str, Any]:
    if build.get("source_content_anchor") != facts_source.get("content_anchor"):
        raise WorkflowError("EVIDENCE_MISMATCH", "build source anchor differs from source facts")
    if build.get("build_input_anchor") != facts_source.get("build_input_anchor"):
        raise WorkflowError("EVIDENCE_MISMATCH", "build input anchor differs from source facts")
    staged_source_root = build.get("staged_source_root")
    if not isinstance(staged_source_root, str) \
            or build_input_anchor(staged_source_root) != build.get("post_build_input_anchor"):
        raise WorkflowError("EVIDENCE_DRIFT", "post-build input tree changed after build receipt")
    _successful_command(build.get("build"), "fresh build")
    _successful_command(build.get("install"), "fresh package install")
    package = _object(build.get("package"), "fresh package evidence")
    package_path = _evidence_file(package, "fresh package")
    if package_path.stat().st_size <= 0:
        raise WorkflowError("EVIDENCE_INCOMPLETE", "fresh package is empty")
    equivalent_paths = package.get("equivalent_paths")
    if not isinstance(equivalent_paths, list):
        raise WorkflowError("EVIDENCE_INCOMPLETE", "fresh package equivalent paths are invalid")
    for index, equivalent in enumerate(equivalent_paths):
        _evidence_file(equivalent, f"fresh package equivalent {index}")
        if equivalent.get("sha256") != package.get("sha256"):
            raise WorkflowError("EVIDENCE_MISMATCH", "fresh package copies differ")
    cache_path = _evidence_file(build.get("cmake_cache"), "fresh CMake cache")
    if cache_path.stat().st_size <= 0:
        raise WorkflowError("EVIDENCE_INCOMPLETE", "fresh CMake cache is empty")
    try:
        cache_values = _cache_values(cache_path)
    except (OSError, UnicodeDecodeError, WorkflowError) as exc:
        raise WorkflowError("EVIDENCE_INCOMPLETE", "fresh CMake cache is unreadable") from exc
    vendor = _object(build.get("vendor"), "build vendor evidence")
    expected_symbols = [
        f"aclnn{checked['operator']['aclnn_name']}GetWorkspaceSize",
        f"aclnn{checked['operator']['aclnn_name']}",
    ]
    if vendor.get("symbols") != expected_symbols:
        raise WorkflowError("EVIDENCE_MISMATCH", "fresh vendor symbol binding is invalid")
    nm_log = _evidence_file(
        {"path": vendor.get("nm_log_path"), "sha256": vendor.get("nm_log_sha256")},
        "fresh vendor nm log",
    )
    try:
        nm_symbols = {
            line.split()[-1]
            for line in nm_log.read_text(encoding="utf-8", errors="strict").splitlines()
            if line.split()
        }
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkflowError("EVIDENCE_INCOMPLETE", "fresh vendor nm log is unreadable") from exc
    if not set(expected_symbols).issubset(nm_symbols):
        raise WorkflowError("EVIDENCE_MISMATCH", "fresh vendor nm log lacks required symbols")
    library = _evidence_file(
        {"path": vendor.get("library_path"), "sha256": vendor.get("library_sha256")},
        "fresh vendor ELF",
    )
    build_target = _object(build.get("target"), "build target evidence")
    install_root = vendor.get("package_install_root")
    custom_opp_root = vendor.get("custom_opp_root")
    if not isinstance(install_root, str) or not isinstance(custom_opp_root, str) \
            or build_target.get("soc") not in checked["task"]["hardware"] \
            or build_target.get("build_token") != checked["operator"]["build_token"]:
        raise WorkflowError("EVIDENCE_MISMATCH", "build target identity is invalid")
    if _vendor_root(Path(install_root), library) != Path(custom_opp_root).resolve():
        raise WorkflowError(
            "EVIDENCE_MISMATCH", "fresh vendor ELF and target delivery use different vendors"
        )
    return {
        "cache_values": cache_values,
        "vendor": vendor,
        "library": library,
        "target": build_target,
        "install_root": Path(install_root),
        "custom_opp_root": Path(custom_opp_root),
    }


def _validated_caseset(
    checked: dict[str, Any],
    cases: dict[str, Any],
    execution_bundle: Any = _NO_EXECUTION_BUNDLE,
) -> dict[str, Any]:
    case_data = _object(cases.get("cases"), "ATK caseset receipt")
    cases_path = _evidence_file(case_data, "ATK caseset")
    generated, ids = _validate_cases(
        cases_path,
        checked["operator"]["aclnn_name"],
        checked["task"]["precision"]["atk_accuracy"],
    )
    if ids != case_data.get("ids") or case_data.get("count") != len(generated):
        raise WorkflowError("EVIDENCE_DRIFT", "generated case identities differ from receipt")
    coverage_ids = _coverage(generated, checked["task"]["required_cases"])
    if coverage_ids != case_data.get("required_case_ids"):
        raise WorkflowError("EVIDENCE_DRIFT", "required-case coverage differs from receipt")
    performance_ids = [
        coverage_ids[index] for index in checked["task"]["performance_required_cases"]
    ]
    if performance_ids != case_data.get("performance_case_ids"):
        raise WorkflowError("EVIDENCE_DRIFT", "performance-case coverage differs from receipt")
    recorded_bundle = cases.get("task_case_bundle")
    declared_bundle = checked["task"].get("case_bundle")
    if declared_bundle is None:
        if recorded_bundle is not None or (
            execution_bundle is not _NO_EXECUTION_BUNDLE and execution_bundle is not None
        ):
            raise WorkflowError("EVIDENCE_MISMATCH", "unexpected task case-bundle evidence")
    else:
        recorded_bundle = _object(recorded_bundle, "task case-bundle evidence")
        root = recorded_bundle.get("root")
        if not isinstance(root, str):
            raise WorkflowError("EVIDENCE_INCOMPLETE", "task case-bundle root is missing")
        actual_bundle = _task_case_bundle_evidence(checked, root)
        if actual_bundle != recorded_bundle or (
            execution_bundle is not _NO_EXECUTION_BUNDLE and execution_bundle != recorded_bundle
        ):
            raise WorkflowError("EVIDENCE_DRIFT", "task case-bundle evidence changed")
        if len(generated) != declared_bundle["expected_generated_case_count"] \
                or _generated_case_projection(generated) \
                != declared_bundle["generated_projection_sha256"]:
            raise WorkflowError("EVIDENCE_DRIFT", "task case-bundle projection changed")
    return case_data


def _validated_target_delivery_failure(
    checked: dict[str, Any],
    expected_spec: str,
    facts_source: dict[str, Any],
    build_receipt_path: os.PathLike[str] | str,
    case_receipt_path: os.PathLike[str] | str,
) -> tuple[str, str]:
    build, build_sha = _receipt(build_receipt_path, "oprunway.build_receipt")
    cases, cases_sha = _receipt(case_receipt_path, "oprunway.atk_case_receipt")
    if build.get("spec_sha256") != expected_spec or cases.get("spec_sha256") != expected_spec:
        raise WorkflowError("EVIDENCE_MISMATCH", "target failure receipts are not bound to this spec")
    if build.get("status") != "TARGET_DELIVERY_MISSING" or cases.get("status") != "VERIFIED":
        raise WorkflowError("EVIDENCE_INCOMPLETE", "target failure receipt status is not publishable")
    case_atk = _object(cases.get("atk"), "casegen ATK identity")
    if not all(isinstance(case_atk.get(field), str) and case_atk[field]
               for field in ("version", "sha256")):
        raise WorkflowError("EVIDENCE_INCOMPLETE", "casegen ATK identity is incomplete")
    _successful_command(case_atk.get("probe"), "casegen ATK version probe")
    _successful_command(cases.get("command"), "ATK case generation")
    case_data = _validated_caseset(checked, cases)
    count = case_data.get("count")
    case_ids = case_data.get("ids")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0 \
            or not isinstance(case_ids, list) or len(case_ids) != count \
            or len(case_ids) != len(set(case_ids)) \
            or any(isinstance(item, bool) or not isinstance(item, int) for item in case_ids):
        raise WorkflowError("EVIDENCE_INCOMPLETE", "generated case identities are invalid")

    common = _validated_build_common(checked, facts_source, build)
    target = common["target"]
    if not _cache_matches_request(
        common["cache_values"], target["soc"], target["build_token"], checked["build"]["vendor_name"]
    ):
        raise WorkflowError("EVIDENCE_MISMATCH", "fresh CMake cache request binding is invalid")
    binding = _object(build.get("target_binding"), "build target binding evidence")
    expected_key = f"OP_CACHE_{target['build_token']}_{target['soc']}"
    expected_type = checked["operator"]["op_type"]
    actual_binding = common["cache_values"].get(expected_key)
    if binding.get("key") != expected_key or binding.get("expected") != expected_type \
            or binding.get("actual") != actual_binding \
            or actual_binding not in {None, expected_type}:
        raise WorkflowError("EVIDENCE_MISMATCH", "missing target binding evidence is invalid")
    if build.get("target_delivery") != []:
        raise WorkflowError("EVIDENCE_MISMATCH", "missing target receipt contains delivery evidence")
    recorded_error = _object(build.get("target_delivery_error"), "target delivery error")
    if recorded_error.get("code") != "TARGET_KERNEL_MISSING" \
            or not isinstance(recorded_error.get("message"), str) \
            or not recorded_error["message"]:
        raise WorkflowError("EVIDENCE_INCOMPLETE", "target delivery error is invalid")
    try:
        _target_delivery(
            common["install_root"], common["custom_opp_root"], target["soc"],
            target["build_token"], expected_type,
        )
    except WorkflowError as exc:
        if exc.code != "TARGET_KERNEL_MISSING" or str(exc) != recorded_error["message"]:
            raise WorkflowError("EVIDENCE_DRIFT", "target delivery failure changed after receipt") from exc
    else:
        raise WorkflowError("EVIDENCE_DRIFT", "target delivery appeared after failure receipt")
    return build_sha, cases_sha


def _write_markdown(path: Path, acceptance: dict[str, Any]) -> None:
    verdict = acceptance["verdict"]
    evidence = acceptance.get("evidence", {})
    lines = [
        f"# {acceptance['operator']} 验收报告",
        "",
        f"- 终态：`{verdict['status']}`",
        f"- 原因代码：`{verdict['reason_code']}`",
        f"- 说明：{verdict['message']}",
        f"- spec SHA-256：`{acceptance['spec_sha256']}`",
        "",
        "## 证据",
        "",
    ]
    for name, item in evidence.items():
        lines.append(f"- {name}：`{item['sha256']}`")
    lines.extend(["", "该结论由确定性脚本生成；证据不完整时不会输出 PASS。", ""])
    limitations = acceptance.get("unvalidated_requirements", [])
    if limitations:
        lines.extend(["## 未验证条款", ""])
        lines.extend(f"- {item}" for item in limitations)
        lines.extend(["", "上述条款不包含在本次 PASS/DUT_FAIL 判定中。", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def finalize(
    *,
    spec: dict[str, Any],
    facts_path: os.PathLike[str] | str,
    out_path: os.PathLike[str] | str,
    requested_physical_device: int,
    build_receipt_path: os.PathLike[str] | str | None = None,
    case_receipt_path: os.PathLike[str] | str | None = None,
    execution_receipt_path: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    checked = validate_spec(spec)
    if isinstance(requested_physical_device, bool) \
            or not isinstance(requested_physical_device, int) \
            or not 0 <= requested_physical_device <= 255:
        raise WorkflowError(
            "INVALID_DEVICE",
            "requested_physical_device must be an integer in [0, 255]",
        )
    expected_spec = spec_digest(checked)
    facts, facts_sha = _receipt(facts_path, "oprunway.source_facts")
    if facts.get("spec_sha256") != expected_spec:
        raise WorkflowError("EVIDENCE_MISMATCH", "source facts are not bound to this spec")

    evidence: dict[str, dict[str, str]] = {
        "source_facts": {"path": str(Path(facts_path).resolve()), "sha256": facts_sha}
    }
    facts_source = _object(facts.get("source"), "source facts anchors")
    facts_status = facts.get("status")
    if facts_status not in {"READY", "UNSUPPORTED"}:
        raise WorkflowError("EVIDENCE_INCOMPLETE", "source facts status is invalid")
    facts_target = _object(facts.get("target"), "source facts target")
    requested_soc = facts_target.get("requested_soc")
    declared_hardware = facts_target.get("declared_hardware")
    supported = facts_target.get("supported")
    if declared_hardware != checked["task"]["hardware"] \
            or not isinstance(requested_soc, str) or not requested_soc \
            or type(supported) is not bool \
            or supported is not (requested_soc in checked["task"]["hardware"]) \
            or (facts_status == "READY") is not supported:
        raise WorkflowError("EVIDENCE_MISMATCH", "source facts target admission is invalid")
    if facts_status == "UNSUPPORTED":
        status = "UNSUPPORTED"
        reason = "TARGET_OUTSIDE_TASKDOC"
        message = "请求的 SoC 不在任务书声明硬件集合内；未执行 DUT。"
    else:
        if not build_receipt_path or not case_receipt_path:
            raise WorkflowError("EVIDENCE_INCOMPLETE", "READY target requires build and cases receipts")
        build_preview, _ = _receipt(build_receipt_path, "oprunway.build_receipt")
        if build_preview.get("target", {}).get("soc") != requested_soc:
            raise WorkflowError("EVIDENCE_MISMATCH", "build target differs from source facts")
        if build_preview.get("status") == "TARGET_DELIVERY_MISSING":
            if execution_receipt_path is not None:
                raise WorkflowError(
                    "EVIDENCE_MISMATCH",
                    "target-delivery failure must stop before ATK execution",
                )
            build_sha, cases_sha = _validated_target_delivery_failure(
                checked, expected_spec, facts_source, build_receipt_path, case_receipt_path
            )
            evidence.update(
                {
                    "build_receipt": {
                        "path": str(Path(build_receipt_path).resolve()),
                        "sha256": build_sha,
                    },
                    "case_receipt": {
                        "path": str(Path(case_receipt_path).resolve()),
                        "sha256": cases_sha,
                    },
                }
            )
            status = "DUT_FAIL"
            reason = "TARGET_DELIVERY_MISSING"
            message = "任务书要求的目标 SoC fresh build 未产生该算子的设备侧 target delivery。"
            acceptance = {
                "schema": "oprunway.acceptance",
                "schema_version": 1,
                "created_at": utc_now(),
                "operator": checked["operator"]["name"],
                "spec_sha256": expected_spec,
                "requested_physical_device": requested_physical_device,
                "verdict": {"status": status, "reason_code": reason, "message": message},
                "unvalidated_requirements": checked["task"]["unvalidated_requirements"],
                "evidence": evidence,
            }
            target = atomic_write_json(out_path, acceptance)
            _write_markdown(target.with_suffix(".md"), acceptance)
            return acceptance
        if not execution_receipt_path:
            raise WorkflowError("EVIDENCE_INCOMPLETE", "verified build requires an execution receipt")
        build, build_sha = _receipt(build_receipt_path, "oprunway.build_receipt")
        cases, cases_sha = _receipt(case_receipt_path, "oprunway.atk_case_receipt")
        execution, execution_sha = _receipt(execution_receipt_path, "oprunway.atk_execution_receipt")
        _validated_device_binding(
            checked, cases, execution, requested_physical_device
        )
        for name, value in (("build", build), ("cases", cases), ("execution", execution)):
            if value.get("spec_sha256") != expected_spec:
                raise WorkflowError("EVIDENCE_MISMATCH", f"{name} receipt is not bound to this spec")
        case_atk = _object(cases.get("atk"), "casegen ATK identity")
        execution_atk = _object(execution.get("atk"), "execution ATK identity")
        if any(case_atk.get(field) != execution_atk.get(field)
               for field in ("path", "version", "sha256")) \
                or not all(isinstance(case_atk.get(field), str) and case_atk[field]
                           for field in ("path", "version", "sha256")):
            raise WorkflowError("EVIDENCE_MISMATCH", "casegen and execution used different ATK identities")
        _evidence_file(
            {"path": case_atk["path"], "sha256": case_atk["sha256"]},
            "casegen ATK executable",
        )
        _successful_command(case_atk.get("probe"), "casegen ATK version probe")
        _successful_command(execution_atk.get("probe"), "execution ATK version probe")
        if build.get("status") != "VERIFIED" or cases.get("status") != "VERIFIED" \
                or execution.get("status") != "COMPLETE":
            raise WorkflowError("EVIDENCE_INCOMPLETE", "receipt status is not publishable")
        _successful_command(cases.get("command"), "ATK case generation")
        execution_commands = _object(execution.get("commands"), "ATK execution commands")
        _successful_command(execution_commands.get("accuracy"), "ATK accuracy")
        if checked["task"]["performance_required_cases"]:
            _successful_command(execution_commands.get("performance"), "ATK performance")
        elif execution_commands.get("performance") is not None:
            raise WorkflowError("EVIDENCE_MISMATCH", "unexpected ATK performance command")
        common = _validated_build_common(checked, facts_source, build)
        vendor = common["vendor"]
        build_target = common["target"]
        actual_delivery = _target_delivery(
            common["install_root"],
            common["custom_opp_root"],
            build_target["soc"],
            build_target["build_token"],
            checked["operator"]["op_type"],
        )
        if actual_delivery != build.get("target_delivery"):
            raise WorkflowError("EVIDENCE_DRIFT", "target kernel delivery changed after build receipt")
        loaded_vendor = _object(execution.get("loaded_vendor"), "loaded vendor evidence")
        if loaded_vendor.get("library_sha256") != vendor.get("library_sha256"):
            raise WorkflowError("EVIDENCE_MISMATCH", "executed ELF differs from fresh build ELF")
        if execution.get("case_receipt_sha256") != cases_sha or execution.get("build_receipt_sha256") != build_sha:
            raise WorkflowError("EVIDENCE_MISMATCH", "execution input receipt hashes differ")
        case_data = _validated_caseset(checked, cases, execution.get("task_case_bundle"))
        report = _object(execution.get("report"), "ATK execution report")
        _evidence_file(report, "ATK accuracy workbook")
        count = report.get("count")
        if count != cases.get("cases", {}).get("count") or not isinstance(count, int) or count <= 0:
            raise WorkflowError("EVIDENCE_INCOMPLETE", "execution denominator differs from generated cases")
        case_ids = cases.get("cases", {}).get("ids")
        if not isinstance(case_ids, list) or len(case_ids) != count or len(case_ids) != len(set(case_ids)) \
                or any(isinstance(item, bool) or not isinstance(item, int) for item in case_ids):
            raise WorkflowError("EVIDENCE_INCOMPLETE", "generated case identities are invalid")
        execution_failed = report.get("execution_failed")
        accuracy_failed = report.get("accuracy_failed")
        accuracy_missing = report.get("accuracy_missing")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in (execution_failed, accuracy_failed, accuracy_missing)):
            raise WorkflowError("EVIDENCE_INCOMPLETE", "execution counters are invalid")
        execution_failed_ids = report.get("execution_failed_ids")
        accuracy_failed_ids = report.get("accuracy_failed_ids")
        accuracy_missing_ids = report.get("accuracy_missing_ids")
        expected_error_ids = report.get("expected_error_case_ids")
        identity_groups = (
            (execution_failed_ids, execution_failed),
            (accuracy_failed_ids, accuracy_failed),
            (accuracy_missing_ids, accuracy_missing),
        )
        if any(
            not isinstance(ids, list) or len(ids) != total or len(ids) != len(set(ids))
            or any(isinstance(item, bool) or not isinstance(item, int) or item not in case_ids for item in ids)
            for ids, total in identity_groups
        ) or not isinstance(expected_error_ids, list) or len(expected_error_ids) != len(set(expected_error_ids)) \
                or any(isinstance(item, bool) or not isinstance(item, int) or item not in case_ids
                       for item in expected_error_ids):
            raise WorkflowError("EVIDENCE_INCOMPLETE", "accuracy failure case identities are invalid")
        expected_error_failures = set(accuracy_failed_ids) & set(expected_error_ids)

        loaded = loaded_vendor
        _evidence_file(
            {"path": loaded.get("log_path"), "sha256": loaded.get("log_sha256")},
            "ATK loaded-library log",
        )
        normal_case_ids = set(case_ids) - set(expected_error_ids)
        required_output_ids = normal_case_ids - set(execution_failed_ids)
        outputs = execution.get("outputs")
        if not isinstance(outputs, list):
            raise WorkflowError("EVIDENCE_INCOMPLETE", "ATK output evidence must be a list")
        output_coverage: dict[str, set[int]] = {"dut": set(), "reference": set()}
        for index, item in enumerate(outputs):
            if not isinstance(item, dict) or item.get("owner") not in output_coverage \
                    or item.get("case_id") not in normal_case_ids:
                raise WorkflowError("EVIDENCE_INCOMPLETE", "ATK output evidence identity is invalid")
            _evidence_file(item, f"ATK output {index}")
            output_coverage[item["owner"]].add(item["case_id"])
        if any(not required_output_ids.issubset(covered) for covered in output_coverage.values()):
            raise WorkflowError("EVIDENCE_INCOMPLETE", "successful normal cases lack DUT or CPU output evidence")

        performance_mode = checked["task"]["dimensions"]["performance"]
        performance_case_ids = report.get("performance_case_ids")
        expected_performance_ids = cases.get("cases", {}).get("performance_case_ids")
        performance_rows = report.get("performance_rows")
        performance_execution_failed = report.get("performance_execution_failed")
        performance_execution_failed_ids = report.get("performance_execution_failed_ids")
        performance_missing = report.get("performance_missing")
        if performance_case_ids != expected_performance_ids or not isinstance(performance_case_ids, list) \
                or len(performance_case_ids) != len(set(performance_case_ids)) \
                or any(isinstance(item, bool) or not isinstance(item, int) or item not in case_ids
                       for item in performance_case_ids):
            raise WorkflowError("EVIDENCE_INCOMPLETE", "performance case identities are invalid")
        if performance_mode == "measure":
            if not performance_case_ids:
                raise WorkflowError("EVIDENCE_INCOMPLETE", "performance denominator is invalid")
            if not isinstance(performance_rows, list) \
                    or len(performance_rows) != len(performance_case_ids):
                raise WorkflowError("EVIDENCE_INCOMPLETE", "performance denominator is invalid")
            if any(not isinstance(row, dict) for row in performance_rows):
                raise WorkflowError("EVIDENCE_INCOMPLETE", "performance rows are invalid")
            row_ids = [row.get("id") for row in performance_rows]
            row_times = [row.get("device_time_us") for row in performance_rows]
            row_statuses = [row.get("execution_status") for row in performance_rows]
            if any(isinstance(value, bool) or not isinstance(value, int) for value in row_ids) \
                    or sorted(row_ids) != sorted(performance_case_ids) \
                    or any(not isinstance(value, str) or not value for value in row_statuses) \
                    or any(value is not None and (
                        isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(float(value)) or float(value) <= 0
                    ) for value in row_times):
                raise WorkflowError("EVIDENCE_INCOMPLETE", "performance rows are invalid")
            failed_ids = [row_id for row_id, status in zip(row_ids, row_statuses) if status != "SUCCESS"]
            missing_ids = [row_id for row_id, value in zip(row_ids, row_times) if value is None]
            expected_complete = not failed_ids and not missing_ids
            if performance_execution_failed != len(failed_ids) \
                    or performance_execution_failed_ids != failed_ids \
                    or performance_missing != len(missing_ids) \
                    or report.get("performance_complete") is not expected_complete:
                raise WorkflowError("EVIDENCE_INCOMPLETE", "performance completion counters are invalid")
            _evidence_file(report.get("performance_workbook"), "ATK performance workbook")
            successful_ids = {
                row_id for row_id, status, value in zip(row_ids, row_statuses, row_times)
                if status == "SUCCESS" and value is not None
            }
            profiles = execution.get("profiles")
            if not isinstance(profiles, list):
                raise WorkflowError("EVIDENCE_INCOMPLETE", "performance profiles must be a list")
            profile_coverage = {case_id: set() for case_id in successful_ids}
            profile_identities: set[tuple[int, str]] = set()
            for index, item in enumerate(profiles):
                if not isinstance(item, dict) or item.get("case_id") not in profile_coverage \
                        or item.get("kind") not in {"op_statistic", "op_summary"}:
                    raise WorkflowError("EVIDENCE_INCOMPLETE", "performance profile identity is invalid")
                identity = (item["case_id"], item["kind"])
                if identity in profile_identities:
                    raise WorkflowError("EVIDENCE_INCOMPLETE", "duplicate performance profile identity")
                profile_identities.add(identity)
                _evidence_file(item, f"performance profile {index}")
                profile_coverage[item["case_id"]].add(item["kind"])
            if any(kinds != {"op_statistic", "op_summary"} for kinds in profile_coverage.values()):
                raise WorkflowError("EVIDENCE_INCOMPLETE", "raw performance profile coverage is incomplete")
        elif performance_case_ids != [] or performance_rows != [] \
                or performance_execution_failed != 0 or performance_execution_failed_ids != [] \
                or performance_missing != 0 or report.get("performance_complete") is not True \
                or report.get("performance_workbook") is not None or execution.get("profiles") != []:
            raise WorkflowError("EVIDENCE_INCOMPLETE", "performance evidence exists for a disabled dimension")

        performance_incomplete = performance_mode == "measure" and not report.get("performance_complete")
        if execution_failed or accuracy_missing or performance_incomplete:
            status = "PLUGIN_ERROR"
            reason = "FAILURE_NOT_ATTRIBUTED_TO_DUT"
            message = "存在精度或性能执行失败/缺失；尚未通过独立对照归因到 DUT。"
        elif expected_error_failures:
            status = "PLUGIN_ERROR"
            reason = "EXPECTED_ERROR_MISMATCH_NOT_ATTRIBUTED"
            message = "预期报错 case 未通过；在独立核对错误契约前不归因到 DUT。"
        elif accuracy_failed:
            status = "DUT_FAIL"
            reason = "NUMERICAL_MISMATCH"
            message = f"{accuracy_failed}/{count} 个完整执行 case 未通过任务书精度口径。"
        else:
            status = "PASS"
            reason = "ALL_REQUIRED_EVIDENCE_PASSED"
            message = f"{count}/{count} 个 case 完整执行并通过全部要求维度。"
        evidence.update(
            {
                "build_receipt": {"path": str(Path(build_receipt_path).resolve()), "sha256": build_sha},
                "case_receipt": {"path": str(Path(case_receipt_path).resolve()), "sha256": cases_sha},
                "execution_receipt": {
                    "path": str(Path(execution_receipt_path).resolve()),
                    "sha256": execution_sha,
                },
            }
        )

    acceptance = {
        "schema": "oprunway.acceptance",
        "schema_version": 1,
        "created_at": utc_now(),
        "operator": checked["operator"]["name"],
        "spec_sha256": expected_spec,
        "requested_physical_device": requested_physical_device,
        "verdict": {"status": status, "reason_code": reason, "message": message},
        "unvalidated_requirements": checked["task"]["unvalidated_requirements"],
        "evidence": evidence,
    }
    target = atomic_write_json(out_path, acceptance)
    _write_markdown(target.with_suffix(".md"), acceptance)
    return acceptance
