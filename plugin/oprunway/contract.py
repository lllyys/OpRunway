"""Validation for the small, declarative OpRunway acceptance contract."""

from __future__ import annotations

import re
from typing import Any

from .util import WorkflowError, digest_json


SCHEMA = "oprunway.acceptance_spec"
SCHEMA_VERSION = 1
STATUS_VALUES = {
    "PASS",
    "DUT_FAIL",
    "PLUGIN_ERROR",
    "UNSUPPORTED",
    "NEEDS_INPUT",
    "BLOCKED",
}
PERFORMANCE_MODES = {"none", "measure"}
BUILD_PROFILES = {"cann_ops_package_v1"}
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SOC = re.compile(r"^ascend[0-9][a-z0-9_]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CASE_INPUT_KEYS = {"index", "dtype", "shape", "value"}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError("INVALID_SPEC", f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError("INVALID_SPEC", f"{label} must be a non-empty string")
    return value


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise WorkflowError("INVALID_SPEC", f"{label} must be in [{minimum}, {maximum}]")
    return value


def _relative_scope(value: Any, label: str) -> str:
    result = _string(value, label)
    parts = result.split("/")
    if result.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise WorkflowError("INVALID_SPEC", f"{label} must be a normalized relative path")
    return result


def _required_cases(value: Any) -> int:
    if not isinstance(value, list) or not value:
        raise WorkflowError("INVALID_SPEC", "task.required_cases must be a non-empty list")
    if len(value) > 10000:
        raise WorkflowError("INVALID_SPEC", "task.required_cases is unreasonably large")
    fingerprints: set[str] = set()
    for case_index, signature in enumerate(value):
        signature = _mapping(signature, f"task.required_cases[{case_index}]")
        inputs = signature.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise WorkflowError("INVALID_SPEC", f"required case {case_index} inputs must be non-empty")
        seen_indices: set[int] = set()
        for input_index, criterion in enumerate(inputs):
            criterion = _mapping(criterion, f"required case {case_index} input {input_index}")
            if set(criterion) - _CASE_INPUT_KEYS or set(criterion) == {"index"}:
                raise WorkflowError("INVALID_SPEC", f"required case {case_index} has invalid input criteria")
            index = _integer(criterion.get("index"), "required case input index", 0, 255)
            if index in seen_indices:
                raise WorkflowError("INVALID_SPEC", f"required case {case_index} repeats input index {index}")
            seen_indices.add(index)
            if "dtype" in criterion:
                _string(criterion["dtype"], "required case dtype")
            if "shape" in criterion:
                shape = criterion["shape"]
                if not isinstance(shape, list) or len(shape) > 8 or any(
                    isinstance(dim, bool) or not isinstance(dim, int) or dim < 0 for dim in shape
                ):
                    raise WorkflowError("INVALID_SPEC", "required case shape must contain 0..8 non-negative dims")
        fingerprint = digest_json(signature)
        if fingerprint in fingerprints:
            raise WorkflowError("INVALID_SPEC", f"required case {case_index} is duplicated")
        fingerprints.add(fingerprint)
    return len(value)


def validate_spec(value: Any) -> dict[str, Any]:
    spec = _mapping(value, "spec")
    if spec.get("schema") != SCHEMA or spec.get("schema_version") != SCHEMA_VERSION:
        raise WorkflowError("INVALID_SPEC", f"spec must be {SCHEMA} v{SCHEMA_VERSION}")

    operator = _mapping(spec.get("operator"), "operator")
    for field in ("name", "aclnn_name", "op_type"):
        text = _string(operator.get(field), f"operator.{field}")
        if not _IDENTIFIER.fullmatch(text):
            raise WorkflowError("INVALID_SPEC", f"operator.{field} is not a safe identifier")
    token = _string(operator.get("build_token"), "operator.build_token")
    if not _TOKEN.fullmatch(token):
        raise WorkflowError("INVALID_SPEC", "operator.build_token is not a safe snake_case token")
    _relative_scope(operator.get("source_subdir"), "operator.source_subdir")

    task = _mapping(spec.get("task"), "task")
    taskdoc_sha = _string(task.get("taskdoc_sha256"), "task.taskdoc_sha256")
    if not _SHA256.fullmatch(taskdoc_sha):
        raise WorkflowError("INVALID_SPEC", "task.taskdoc_sha256 must be lowercase SHA-256")
    hardware = task.get("hardware")
    if not isinstance(hardware, list) or not hardware or len(hardware) != len(set(hardware)):
        raise WorkflowError("INVALID_SPEC", "task.hardware must be a non-empty unique list")
    if any(not isinstance(item, str) or not _SOC.fullmatch(item) for item in hardware):
        raise WorkflowError("INVALID_SPEC", "task.hardware must use canonical safe CANN SoC tokens")
    dimensions = _mapping(task.get("dimensions"), "task.dimensions")
    if dimensions.get("precision") is not True:
        raise WorkflowError("INVALID_SPEC", "precision is the mandatory acceptance dimension")
    if dimensions.get("performance") not in PERFORMANCE_MODES:
        raise WorkflowError("INVALID_SPEC", f"performance must use {sorted(PERFORMANCE_MODES)}")
    precision = _mapping(task.get("precision"), "task.precision")
    atk_accuracy = precision.get("atk_accuracy")
    if isinstance(atk_accuracy, str):
        accuracy_name = atk_accuracy
    elif isinstance(atk_accuracy, dict) and len(atk_accuracy) == 1:
        accuracy_name, accuracy_options = next(iter(atk_accuracy.items()))
        if not isinstance(accuracy_options, dict):
            raise WorkflowError("INVALID_SPEC", "task.precision ATK options must be an object")
    else:
        raise WorkflowError(
            "INVALID_SPEC", "task.precision.atk_accuracy must be a name or one-name options object"
        )
    if not isinstance(accuracy_name, str) or not _TOKEN.fullmatch(accuracy_name):
        raise WorkflowError("INVALID_SPEC", "task.precision ATK comparator name is invalid")
    limitations = task.get("unvalidated_requirements")
    if not isinstance(limitations, list) or any(
        not isinstance(item, str) or not item.strip() for item in limitations
    ) or len(limitations) != len(set(limitations)):
        raise WorkflowError("INVALID_SPEC", "task.unvalidated_requirements must be a unique string list")
    required_count = _required_cases(task.get("required_cases"))
    performance_cases = task.get("performance_required_cases")
    if not isinstance(performance_cases, list) or len(performance_cases) != len(set(performance_cases)) or any(
        isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < required_count
        for index in performance_cases
    ):
        raise WorkflowError("INVALID_SPEC", "task.performance_required_cases has invalid required-case indexes")
    performance_mode = dimensions.get("performance")
    if (performance_mode == "measure") != bool(performance_cases):
        raise WorkflowError(
            "INVALID_SPEC", "measure requires a non-empty performance_required_cases list; none requires []"
        )

    runner = _mapping(spec.get("runner"), "runner")
    if runner.get("form") != "atk_aclnn":
        raise WorkflowError("INVALID_SPEC", "runner.form must be atk_aclnn")
    _string(runner.get("atk_version"), "runner.atk_version")
    _integer(runner.get("device"), "runner.device", 0, 255)
    _integer(runner.get("case_timeout_seconds"), "runner.case_timeout_seconds", 1, 1800)
    _integer(runner.get("stage_timeout_seconds"), "runner.stage_timeout_seconds", 1, 7200)
    _integer(runner.get("workflow_timeout_seconds"), "runner.workflow_timeout_seconds", 60, 7200)
    _integer(runner.get("seed"), "runner.seed", 0, 2**31 - 1)

    build = _mapping(spec.get("build"), "build")
    if build.get("profile") not in BUILD_PROFILES:
        raise WorkflowError("INVALID_SPEC", f"build.profile must use {sorted(BUILD_PROFILES)}")
    vendor = _string(build.get("vendor_name"), "build.vendor_name")
    if not _TOKEN.fullmatch(vendor):
        raise WorkflowError("INVALID_SPEC", "build.vendor_name is not a safe token")
    _integer(build.get("jobs"), "build.jobs", 1, 64)

    return spec


def spec_digest(spec: dict[str, Any]) -> str:
    return digest_json(validate_spec(spec))


def validate_soc(value: Any) -> str:
    soc = _string(value, "target_soc")
    if not _SOC.fullmatch(soc):
        raise WorkflowError("INVALID_INPUT", "target_soc must be a canonical safe CANN SoC token")
    return soc
