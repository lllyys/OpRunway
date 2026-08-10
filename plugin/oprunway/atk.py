"""ATK case generation, execution and strict report normalization."""

from __future__ import annotations

import csv
import dataclasses
import json
import math
import os
import re
import shutil
import time
import zipfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from posixpath import normpath
from typing import Any, Callable

from .build import _target_delivery, _vendor_root
from .contract import spec_digest, validate_spec
from .source import build_input_anchor
from .util import (
    CommandReceipt,
    WorkflowError,
    atomic_write_json,
    load_json,
    require_plain_file,
    run_command,
    sha256_file,
    utc_now,
)


def _ascii_path(path: os.PathLike[str] | str, label: str, *, file: bool = True) -> Path:
    if not isinstance(path, (str, os.PathLike)):
        raise WorkflowError("INVALID_INPUT", f"{label} path is missing")
    candidate = require_plain_file(path, label) if file else Path(path).resolve()
    rendered = str(candidate)
    if not rendered.isascii() or ".." in Path(path).parts:
        raise WorkflowError("ATK_PATH_UNSUPPORTED", f"{label} must use an ASCII normalized path")
    return candidate


def atk_preflight(atk_bin: os.PathLike[str] | str, expected_version: str, evidence_dir: Path) -> dict[str, Any]:
    raw = Path(atk_bin)
    if ".." in raw.parts:
        raise WorkflowError("ATK_PATH_UNSUPPORTED", "ATK executable path must be normalized")
    if len(raw.parts) == 1:
        located = shutil.which(str(raw))
        if located is None:
            raise WorkflowError("ATK_NOT_READY", f"ATK command is unavailable on PATH: {raw}")
        executable = Path(located).absolute()
    else:
        executable = raw.absolute()
    if not str(executable).isascii() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise WorkflowError("ATK_NOT_READY", f"ATK executable is unavailable: {executable}")
    executable_sha256 = sha256_file(executable)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    receipt = run_command(
        [str(executable), "--version"],
        cwd=evidence_dir,
        timeout_seconds=30,
        stdout_path=evidence_dir / "atk-version.stdout.log",
        stderr_path=evidence_dir / "atk-version.stderr.log",
    )
    if receipt.timed_out or receipt.returncode != 0:
        raise WorkflowError("ATK_NOT_READY", "cannot query installed ATK version")
    if sha256_file(executable) != executable_sha256:
        raise WorkflowError("ATK_IDENTITY_DRIFT", "ATK executable changed during version preflight")
    try:
        version = Path(receipt.stdout_path).read_text(encoding="utf-8", errors="strict").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkflowError("ATK_NOT_READY", "ATK version output is not valid UTF-8") from exc
    if version != expected_version:
        raise WorkflowError("ATK_VERSION_MISMATCH", f"expected ATK {expected_version}, got {version}")
    return {
        "path": str(executable),
        "sha256": executable_sha256,
        "version": version,
        "probe": receipt.to_dict(),
    }


def _validate_cases(
    path: Path, aclnn_name: str, expected_accuracy: str | dict[str, Any]
) -> tuple[list[dict[str, Any]], list[int]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError("ATK_CASESET_INVALID", f"cannot parse generated cases: {exc}") from exc
    if not isinstance(value, list) or not value:
        raise WorkflowError("ATK_CASESET_INVALID", "generated cases must be a non-empty list")
    ids: list[int] = []
    for index, case in enumerate(value):
        if not isinstance(case, dict):
            raise WorkflowError("ATK_CASESET_INVALID", f"case {index} is not an object")
        case_id = case.get("id")
        if isinstance(case_id, bool) or not isinstance(case_id, int):
            raise WorkflowError("ATK_CASESET_INVALID", f"case {index} has no integer id")
        if case.get("inputs") is None or not isinstance(case.get("inputs"), list):
            raise WorkflowError("ATK_CASESET_INVALID", f"case {case_id} inputs must be a list")
        declared = case.get("aclnn_name")
        normalized = declared[5:] if isinstance(declared, str) and declared.startswith("aclnn") else declared
        if normalized != aclnn_name:
            raise WorkflowError(
                "ATK_CASESET_INVALID", f"case {case_id} aclnn_name={declared!r} does not match {aclnn_name}"
            )
        standard = case.get("standard")
        if not isinstance(standard, dict) or standard.get("acc") != expected_accuracy:
            raise WorkflowError(
                "ATK_CASESET_INVALID",
                f"case {case_id} precision comparator differs from the acceptance spec",
            )
        ids.append(case_id)
    if len(ids) != len(set(ids)):
        raise WorkflowError("ATK_CASESET_INVALID", "generated case ids are not unique")
    return value, ids


_EMPTY_ATTR_TUPLE_NAME = "__oprunway_empty_tuple__"


def _coverage_projection(actual: Any) -> tuple[Any, Any, Any, bool] | None:
    if isinstance(actual, dict):
        return (
            actual.get("dtype"), actual.get("shape"), actual.get("range_values"),
            "range_values" in actual,
        )
    if not isinstance(actual, list) or not all(isinstance(item, dict) for item in actual):
        return None
    if len(actual) == 1:
        marker = actual[0]
        if (
            marker.get("name") == _EMPTY_ATTR_TUPLE_NAME
            and marker.get("type") == "attr_tuple"
            and marker.get("required") is True
            and isinstance(marker.get("dtype"), str)
            and bool(marker["dtype"])
            and "shape" in marker
            and marker["shape"] is None
            and marker.get("range_values") == "default"
        ):
            return None, None, [], True
    tuple_dtypes = [item.get("dtype") for item in actual]
    actual_dtype = (
        tuple_dtypes[0]
        if tuple_dtypes and all(dtype == tuple_dtypes[0] for dtype in tuple_dtypes)
        else None
    )
    return (
        actual_dtype, None, [item.get("range_values") for item in actual],
        all("range_values" in item for item in actual),
    )


def _coverage(cases: list[dict[str, Any]], required: list[dict[str, Any]]) -> list[int]:
    adjacency: list[list[int]] = []
    for signature in required:
        candidates: list[int] = []
        for case_index, case in enumerate(cases):
            actual_inputs = case["inputs"]
            satisfied = True
            for criterion in signature["inputs"]:
                index = criterion["index"]
                if index >= len(actual_inputs):
                    satisfied = False
                    break
                projection = _coverage_projection(actual_inputs[index])
                if projection is None:
                    satisfied = False
                    break
                actual_dtype, actual_shape, actual_value, actual_has_value = projection
                if "dtype" in criterion and actual_dtype != criterion["dtype"]:
                    satisfied = False
                if "shape" in criterion and actual_shape != criterion["shape"]:
                    satisfied = False
                if "value" in criterion and (
                    not actual_has_value or actual_value != criterion["value"]
                ):
                    satisfied = False
                if not satisfied:
                    break
            if satisfied:
                candidates.append(case_index)
        adjacency.append(candidates)

    case_owner: dict[int, int] = {}

    def assign(required_index: int, seen: set[int]) -> bool:
        for case_index in adjacency[required_index]:
            if case_index in seen:
                continue
            seen.add(case_index)
            owner = case_owner.get(case_index)
            if owner is None or assign(owner, seen):
                case_owner[case_index] = required_index
                return True
        return False

    for required_index in range(len(required)):
        if not assign(required_index, set()):
            raise WorkflowError(
                "ATK_COVERAGE_INCOMPLETE", f"generated cases do not satisfy required case {required_index}"
            )
    by_required = {owner: case_index for case_index, owner in case_owner.items()}
    return [cases[by_required[index]]["id"] for index in range(len(required))]


def generate_cases(
    *,
    spec: dict[str, Any],
    atk_bin: os.PathLike[str] | str,
    design_path: os.PathLike[str] | str,
    work_dir: os.PathLike[str] | str,
    out_path: os.PathLike[str] | str,
    generator_path: os.PathLike[str] | str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    checked = validate_spec(spec)
    design = _ascii_path(design_path, "ATK design file")
    generator = _ascii_path(generator_path, "ATK generator plugin") if generator_path else None
    work = Path(work_dir)
    if work.exists():
        raise WorkflowError("DIRTY_SESSION", f"ATK casegen work directory already exists: {work}")
    work.mkdir(parents=True)
    work = work.resolve()
    preflight = atk_preflight(atk_bin, checked["runner"]["atk_version"], work / "preflight")
    argv = [
        preflight["path"],
        "case",
        "-f",
        str(design),
        "--seed",
        str(checked["runner"]["seed"]),
    ]
    if generator:
        argv.extend(["--plugin_path", str(generator)])
    if sha256_file(preflight["path"]) != preflight["sha256"]:
        raise WorkflowError("ATK_IDENTITY_DRIFT", "ATK executable changed before case generation")
    command = run_command(
        argv,
        cwd=work,
        timeout_seconds=timeout_seconds or checked["runner"]["stage_timeout_seconds"],
        stdout_path=work / "atk-case.stdout.log",
        stderr_path=work / "atk-case.stderr.log",
    )
    if sha256_file(preflight["path"]) != preflight["sha256"]:
        raise WorkflowError("ATK_IDENTITY_DRIFT", "ATK executable changed during case generation")
    if command.timed_out:
        raise WorkflowError("ATK_CASEGEN_TIMEOUT", "ATK case generation exceeded stage timeout")
    if command.returncode != 0:
        raise WorkflowError("ATK_CASEGEN_FAILED", "ATK case generation failed")
    outputs = sorted((work / "result").rglob("json/*.json"))
    if len(outputs) != 1:
        raise WorkflowError("ATK_CASEGEN_FAILED", f"expected one ATK case JSON, found {len(outputs)}")
    cases_path = outputs[0].resolve()
    cases, ids = _validate_cases(
        cases_path,
        checked["operator"]["aclnn_name"],
        checked["task"]["precision"]["atk_accuracy"],
    )
    coverage_ids = _coverage(cases, checked["task"]["required_cases"])
    performance_ids = [coverage_ids[index] for index in checked["task"]["performance_required_cases"]]
    receipt = {
        "schema": "oprunway.atk_case_receipt",
        "schema_version": 1,
        "status": "VERIFIED",
        "created_at": utc_now(),
        "spec_sha256": spec_digest(checked),
        "atk": preflight,
        "design": {"path": str(design), "sha256": sha256_file(design)},
        "generator": (
            {"path": str(generator), "sha256": sha256_file(generator)} if generator else None
        ),
        "command": command.to_dict(),
        "cases": {
            "path": str(cases_path),
            "sha256": sha256_file(cases_path),
            "count": len(cases),
            "ids": ids,
            "required_case_ids": coverage_ids,
            "performance_case_ids": performance_ids,
        },
    }
    atomic_write_json(out_path, receipt)
    return receipt


_XLSX_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_XLSX_DOC_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_XLSX_PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL_REFERENCE = re.compile(r"^([A-Z]+)[1-9][0-9]*$")


def _xlsx_column(reference: str) -> int:
    match = _CELL_REFERENCE.fullmatch(reference)
    if match is None:
        raise WorkflowError("ATK_REPORT_INVALID", f"invalid XLSX cell reference: {reference!r}")
    column = 0
    for char in match.group(1):
        column = column * 26 + ord(char) - ord("A") + 1
    if not 1 <= column <= 16384:
        raise WorkflowError("ATK_REPORT_INVALID", f"XLSX column is out of range: {reference}")
    return column - 1


def _xlsx_cell(cell: ElementTree.Element, shared: list[str]) -> Any:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(f".//{_XLSX_MAIN}t"))
    value = cell.find(f"{_XLSX_MAIN}v")
    if value is None or value.text is None:
        return None
    raw = value.text
    if kind == "s":
        try:
            index = int(raw)
        except ValueError as exc:
            raise WorkflowError("ATK_REPORT_INVALID", "invalid XLSX shared-string index") from exc
        if not 0 <= index < len(shared):
            raise WorkflowError("ATK_REPORT_INVALID", "invalid XLSX shared-string index")
        return shared[index]
    if kind == "b":
        if raw not in {"0", "1"}:
            raise WorkflowError("ATK_REPORT_INVALID", "invalid XLSX boolean cell")
        return raw == "1"
    if kind in {"str", "e"}:
        return raw
    try:
        if re.fullmatch(r"[+-]?[0-9]+", raw):
            return int(raw)
        number = float(raw)
    except ValueError as exc:
        raise WorkflowError("ATK_REPORT_INVALID", f"invalid XLSX numeric cell: {raw!r}") from exc
    if not math.isfinite(number):
        raise WorkflowError("ATK_REPORT_INVALID", "non-finite XLSX numeric cell")
    return number


def _xlsx_rows(archive: zipfile.ZipFile, member: str, shared: list[str]) -> list[list[Any]]:
    root = ElementTree.fromstring(archive.read(member))
    result: list[list[Any]] = []
    for row in root.findall(f".//{_XLSX_MAIN}sheetData/{_XLSX_MAIN}row"):
        values: dict[int, Any] = {}
        for cell in row.findall(f"{_XLSX_MAIN}c"):
            reference = cell.get("r")
            if not isinstance(reference, str):
                raise WorkflowError("ATK_REPORT_INVALID", "XLSX cell lacks a reference")
            column = _xlsx_column(reference)
            if column in values:
                raise WorkflowError("ATK_REPORT_INVALID", f"duplicate XLSX cell: {reference}")
            values[column] = _xlsx_cell(cell, shared)
        width = max(values, default=-1) + 1
        result.append([values.get(index) for index in range(width)])
    return result


def _workbook_rows(workbook: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        with zipfile.ZipFile(workbook) as archive:
            members = archive.infolist()
            if len(members) > 2048 or sum(item.file_size for item in members) > 256 * 1024 * 1024:
                raise WorkflowError("ATK_REPORT_INVALID", "ATK workbook exceeds the XLSX safety limit")
            names = [item.filename for item in members]
            if len(names) != len(set(names)):
                raise WorkflowError("ATK_REPORT_INVALID", "ATK workbook has duplicate ZIP members")
            workbook_root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets = {
                item.get("Id"): item.get("Target")
                for item in relationships.findall(f"{_XLSX_PACKAGE_REL}Relationship")
            }
            sheets: dict[str, str] = {}
            for sheet in workbook_root.findall(f".//{_XLSX_MAIN}sheet"):
                name = sheet.get("name")
                target = targets.get(sheet.get(_XLSX_DOC_REL))
                if not isinstance(name, str) or not isinstance(target, str):
                    raise WorkflowError("ATK_REPORT_INVALID", "invalid XLSX sheet relationship")
                member = normpath(target.lstrip("/")) if target.startswith("/") \
                    else normpath(f"xl/{target}")
                if not member.startswith("xl/") or member.startswith("xl/../"):
                    raise WorkflowError("ATK_REPORT_INVALID", "XLSX worksheet path escapes xl/")
                if name in sheets:
                    raise WorkflowError("ATK_REPORT_INVALID", f"duplicate XLSX sheet name: {name}")
                sheets[name] = member
            if "statistic" not in sheets or "summary" not in sheets:
                raise WorkflowError("ATK_REPORT_INVALID", "ATK workbook lacks statistic/summary sheets")
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = [
                    "".join(node.text or "" for node in item.findall(f".//{_XLSX_MAIN}t"))
                    for item in shared_root.findall(f"{_XLSX_MAIN}si")
                ]
            statistic_values = _xlsx_rows(archive, sheets["statistic"], shared)
            summary_values = _xlsx_rows(archive, sheets["summary"], shared)
    except WorkflowError:
        raise
    except (
        OSError, KeyError, ValueError, RuntimeError, LookupError,
        zipfile.BadZipFile, ElementTree.ParseError,
    ) as exc:
        raise WorkflowError("ATK_REPORT_INVALID", f"cannot parse ATK workbook: {exc}") from exc
    headers = statistic_values[0] if statistic_values else None
    if not headers or any(not isinstance(item, str) for item in headers) \
            or len(headers) != len(set(headers)):
        raise WorkflowError("ATK_REPORT_INVALID", "ATK statistic header is invalid")
    rows = [dict(zip(headers, row)) for row in statistic_values[1:] if any(value is not None for value in row)]
    summary_headers = summary_values[0] if summary_values else None
    summary_row = summary_values[1] if len(summary_values) > 1 else None
    if not summary_headers or not summary_row or any(not isinstance(item, str) for item in summary_headers) \
            or len(summary_headers) != len(set(summary_headers)):
        raise WorkflowError("ATK_REPORT_INVALID", "ATK summary is empty or invalid")
    return rows, dict(zip(summary_headers, summary_row))


_DEVICE_TIME = re.compile(r"^pyaclnn_[^_]+_Device性能（us）$")


def _case_filter(case_ids: list[int]) -> str:
    return json.dumps(case_ids, separators=(",", ":"))


def _execution_environment(library: Path, custom_opp_root: str) -> dict[str, str]:
    env = os.environ.copy()
    env["ATK_CUSTOM_OPP_PATH"] = str(library)
    env["ASCEND_CUSTOM_OPP_PATH"] = custom_opp_root
    library_dir = str(library.parent)
    inherited = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = f"{library_dir}:{inherited}" if inherited else library_dir
    return env


def _run_independent_phases(
    accuracy: Callable[[], dict[str, Any]],
    performance: Callable[[], dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    def invoke(phase: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return phase()
        except WorkflowError as exc:
            if exc.code not in {
                "ATK_EXECUTION_TIMEOUT", "ATK_EXECUTION_FAILED", "COMMAND_START_FAILED",
            }:
                raise
            return {
                "command": None, "workbook": None, "rows": None,
                "summary": None, "normalized": None, "error": exc,
            }
        except OSError as exc:
            return {
                "command": None, "workbook": None, "rows": None,
                "summary": None, "normalized": None,
                "error": WorkflowError(
                    "ATK_EXECUTION_FAILED", f"cannot start ATK execution phase: {exc}"
                ),
            }

    accuracy_result = invoke(accuracy)
    performance_result = invoke(performance) if performance else None
    return accuracy_result, performance_result


def _settled_command_receipt(command: CommandReceipt) -> CommandReceipt:
    paths = (Path(command.stdout_path), Path(command.stderr_path))
    previous: tuple[tuple[int, int, str], ...] | None = None
    stable_since: float | None = None
    deadline = time.monotonic() + 10.0
    while True:
        try:
            current = tuple(
                (path.stat().st_size, path.stat().st_mtime_ns, sha256_file(path))
                for path in paths
            )
        except OSError as exc:
            raise WorkflowError("ATK_LOG_UNSTABLE", f"cannot settle ATK command logs: {exc}") from exc
        now = time.monotonic()
        if current == previous:
            stable_since = stable_since if stable_since is not None else now
            if now - stable_since >= 2.0:
                return dataclasses.replace(
                    command, stdout_sha256=current[0][2], stderr_sha256=current[1][2]
                )
        else:
            previous = current
            stable_since = None
        if now >= deadline:
            raise WorkflowError("ATK_LOG_UNSTABLE", "ATK command logs did not become stable")
        time.sleep(0.25)


def _normalize_rows(
    rows: list[dict[str, Any]], expected_ids: list[int], *, require_performance: bool
) -> dict[str, Any]:
    accuracy_columns = sorted({
        key for row in rows for key in row
        if isinstance(key, str) and key.endswith("_精度通过")
        and any(candidate.get(key) is not None for candidate in rows)
    })
    seen: list[int] = []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        case_id = row.get("编号")
        if isinstance(case_id, bool) or not isinstance(case_id, int):
            raise WorkflowError("ATK_REPORT_INVALID", f"report has invalid case id {case_id!r}")
        seen.append(case_id)
        accuracy_values = [row.get(key) for key in accuracy_columns]
        if not accuracy_values or any(not isinstance(value, bool) for value in accuracy_values):
            accuracy_passed: bool | None = None
        else:
            accuracy_passed = all(accuracy_values)
        device_values = (
            [
                value
                for key, value in row.items()
                if isinstance(key, str) and _DEVICE_TIME.fullmatch(key) and value is not None
            ]
            if require_performance else []
        )
        if not require_performance or not device_values:
            device_time_us: float | None = None
        elif len(device_values) != 1 or isinstance(device_values[0], bool) \
                or not isinstance(device_values[0], (int, float)) \
                or not math.isfinite(float(device_values[0])) or float(device_values[0]) <= 0:
            raise WorkflowError("ATK_REPORT_INVALID", f"case {case_id} has invalid device timing")
        else:
            device_time_us = float(device_values[0])
        execution_status = row.get("运行结果")
        if not isinstance(execution_status, str) or not execution_status:
            raise WorkflowError("ATK_REPORT_INVALID", f"case {case_id} has invalid execution status")
        normalized.append(
            {
                "id": case_id,
                "execution_status": execution_status,
                "accuracy_passed": accuracy_passed,
                "device_time_us": device_time_us,
                "failure_reason": row.get("失败原因"),
                "case_json": row.get("用例json信息"),
            }
        )
    if sorted(seen) != sorted(expected_ids) or len(seen) != len(expected_ids):
        raise WorkflowError(
            "ATK_REPORT_INCOMPLETE",
            f"ATK report denominator {len(seen)} does not match caseset {len(expected_ids)}",
        )
    execution_failed = [row for row in normalized if row["execution_status"] != "SUCCESS"]
    accuracy_failed = [row for row in normalized if row["accuracy_passed"] is False]
    accuracy_missing = [row for row in normalized if row["accuracy_passed"] is None]
    performance_missing = [row for row in normalized if row["device_time_us"] is None]
    return {
        "rows": normalized,
        "count": len(normalized),
        "execution_failed": len(execution_failed),
        "execution_failed_ids": [row["id"] for row in execution_failed],
        "accuracy_failed": len(accuracy_failed),
        "accuracy_failed_ids": [row["id"] for row in accuracy_failed],
        "accuracy_missing": len(accuracy_missing),
        "accuracy_missing_ids": [row["id"] for row in accuracy_missing],
        "performance_complete": not require_performance or (
            not execution_failed and not performance_missing
        ),
        "performance_missing": len(performance_missing) if require_performance else 0,
        "performance_execution_failed": len(execution_failed) if require_performance else 0,
        "performance_execution_failed_ids": (
            [row["id"] for row in execution_failed] if require_performance else []
        ),
    }


def _saved_outputs(
    root: Path, expected_ids: list[int], *, required_ids: list[int] | None = None
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: dict[str, set[int]] = {"dut": set(), "reference": set()}
    required = set(expected_ids if required_ids is None else required_ids)
    if not required.issubset(set(expected_ids)):
        raise WorkflowError("OUTPUT_EVIDENCE_MISSING", "required output ids are outside the caseset")
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".bin", ".pt"}:
            continue
        relative = path.relative_to(root)
        backend = next((part for part in relative.parts if part.startswith(("pyaclnn_", "cpu_"))), None)
        if backend is None:
            continue
        owner = "dut" if backend.startswith("pyaclnn_") else "reference"
        case_id = next((item for item in expected_ids if str(item) in relative.parts), None)
        if case_id is None:
            continue
        if path.stat().st_size == 0:
            if case_id in required:
                raise WorkflowError("OUTPUT_EVIDENCE_MISSING", f"ATK saved an empty output: {path}")
            continue
        seen[owner].add(case_id)
        evidence.append(
            {
                "owner": owner,
                "case_id": case_id,
                "path": str(path.resolve()),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    if not required.issubset(seen["dut"]) or not required.issubset(seen["reference"]):
        raise WorkflowError(
            "OUTPUT_EVIDENCE_MISSING",
            "ATK did not save both DUT and CPU reference output for every case",
        )
    return evidence


def _profile_evidence(root: Path, expected_ids: list[int]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: dict[int, set[str]] = {case_id: set() for case_id in expected_ids}
    required_headers = {
        "op_statistic": ("OP Type", "Total Time(us)"),
        "op_summary": ("OP Type", "Task Duration(us)"),
    }
    for kind, (op_header, duration_header) in required_headers.items():
        for path in sorted(root.rglob(f"{kind}_*.csv")):
            relative = path.relative_to(root)
            case_id = next((item for item in expected_ids if str(item) in relative.parts), None)
            if case_id is None or not any(part.startswith("pyaclnn_") for part in relative.parts):
                continue
            try:
                with path.open("r", encoding="utf-8", errors="strict", newline="") as stream:
                    reader = csv.DictReader(stream)
                    headers = reader.fieldnames or []
                    rows = list(reader)
            except (OSError, UnicodeDecodeError, csv.Error) as exc:
                raise WorkflowError("ATK_PROFILE_INVALID", f"cannot parse {kind} for case {case_id}") from exc
            if len(headers) != len(set(headers)) or not {op_header, duration_header}.issubset(headers) or not rows:
                raise WorkflowError("ATK_PROFILE_INVALID", f"invalid {kind} profile for case {case_id}")
            durations: list[float] = []
            for row in rows:
                raw = row.get(duration_header)
                try:
                    value = float(raw) if raw is not None else math.nan
                except (TypeError, ValueError):
                    continue
                if row.get(op_header) and math.isfinite(value) and value > 0:
                    durations.append(value)
            if not durations:
                raise WorkflowError("ATK_PROFILE_INVALID", f"{kind} has no positive timing for case {case_id}")
            if kind in seen[case_id]:
                raise WorkflowError("ATK_PROFILE_INVALID", f"duplicate {kind} profile for case {case_id}")
            seen[case_id].add(kind)
            evidence.append(
                {
                    "case_id": case_id,
                    "kind": kind,
                    "path": str(path.resolve()),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    missing = [case_id for case_id, kinds in seen.items() if kinds != set(required_headers)]
    if missing:
        raise WorkflowError("ATK_PROFILE_INCOMPLETE", f"missing raw NPU profile CSV for cases {missing}")
    return evidence


def _raw_profile_evidence(
    root: Path, expected_ids: list[int]
) -> tuple[list[dict[str, Any]], WorkflowError | None]:
    evidence: list[dict[str, Any]] = []
    error: WorkflowError | None = None
    try:
        for kind in ("op_statistic", "op_summary"):
            for path in sorted(root.rglob(f"{kind}_*.csv")):
                relative = path.relative_to(root)
                case_id = next((item for item in expected_ids if str(item) in relative.parts), None)
                try:
                    if not path.is_file() or case_id is None \
                            or not any(part.startswith("pyaclnn_") for part in relative.parts):
                        continue
                    evidence.append({
                        "case_id": case_id,
                        "kind": kind,
                        "path": str(path.resolve()),
                        "size": path.stat().st_size,
                        "sha256": sha256_file(path),
                    })
                except OSError as exc:
                    if error is None:
                        error = WorkflowError(
                            "ATK_PROFILE_INVALID", f"cannot inventory raw profile {path}: {exc}"
                        )
    except OSError as exc:
        error = WorkflowError("ATK_PROFILE_INVALID", f"cannot inventory raw profiles: {exc}")
    return evidence, error


def _loaded_library(logs: list[Path], workspace_symbol: str, expected_library: Path) -> dict[str, Any]:
    pattern = re.compile(rf"import\s+{re.escape(workspace_symbol)}\s+from\s+(.+?)\s+success!")
    matches: list[tuple[Path, Path]] = []
    for log in logs:
        text = log.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            candidate = Path(match.group(1).strip()).resolve()
            matches.append((candidate, log.resolve()))
    unique = {str(path) for path, _ in matches}
    if unique != {str(expected_library.resolve())}:
        raise WorkflowError(
            "LOADED_LIBRARY_MISMATCH",
            f"ATK loaded {sorted(unique)}, expected {expected_library.resolve()}",
        )
    log = matches[0][1]
    return {
        "library_path": str(expected_library.resolve()),
        "library_sha256": sha256_file(expected_library),
        "log_path": str(log),
        "log_sha256": sha256_file(log),
        "symbol": workspace_symbol,
    }


def run_cases(
    *,
    spec: dict[str, Any],
    atk_bin: os.PathLike[str] | str,
    case_receipt_path: os.PathLike[str] | str,
    build_receipt_path: os.PathLike[str] | str,
    work_dir: os.PathLike[str] | str,
    out_path: os.PathLike[str] | str,
    execution_plugin: os.PathLike[str] | str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    checked = validate_spec(spec)
    case_receipt = load_json(case_receipt_path)
    build_receipt = load_json(build_receipt_path)
    if not isinstance(case_receipt, dict) or not isinstance(build_receipt, dict):
        raise WorkflowError("EVIDENCE_MISMATCH", "case/build receipt must be objects")
    expected_spec = spec_digest(checked)
    if case_receipt.get("spec_sha256") != expected_spec or build_receipt.get("spec_sha256") != expected_spec:
        raise WorkflowError("EVIDENCE_MISMATCH", "case/build receipt is not bound to this spec")
    staged_source_root = build_receipt.get("staged_source_root")
    if not isinstance(staged_source_root, str) \
            or build_input_anchor(staged_source_root) != build_receipt.get("post_build_input_anchor"):
        raise WorkflowError("EVIDENCE_DRIFT", "post-build input tree changed before ATK execution")
    case_data = case_receipt.get("cases")
    if not isinstance(case_data, dict):
        raise WorkflowError("EVIDENCE_MISMATCH", "case receipt lacks caseset identity")
    cases_path = _ascii_path(case_data.get("path"), "ATK caseset")
    if sha256_file(cases_path) != case_data.get("sha256"):
        raise WorkflowError("EVIDENCE_DRIFT", "ATK caseset changed after generation")
    cases, ids = _validate_cases(
        cases_path,
        checked["operator"]["aclnn_name"],
        checked["task"]["precision"]["atk_accuracy"],
    )
    if ids != case_data.get("ids"):
        raise WorkflowError("EVIDENCE_DRIFT", "ATK case ids changed after generation")
    coverage_ids = _coverage(cases, checked["task"]["required_cases"])
    if coverage_ids != case_data.get("required_case_ids"):
        raise WorkflowError("EVIDENCE_DRIFT", "ATK required-case coverage differs from case receipt")
    vendor = build_receipt.get("vendor")
    if not isinstance(vendor, dict):
        raise WorkflowError("EVIDENCE_MISMATCH", "build receipt lacks vendor identity")
    library = _ascii_path(vendor.get("library_path"), "DUT vendor ELF")
    if sha256_file(library) != vendor.get("library_sha256"):
        raise WorkflowError("EVIDENCE_DRIFT", "DUT vendor ELF changed after build receipt")
    install_root = vendor.get("package_install_root")
    target = build_receipt.get("target")
    if not isinstance(install_root, str) or not isinstance(target, dict) \
            or target.get("soc") not in checked["task"]["hardware"] \
            or target.get("build_token") != checked["operator"]["build_token"]:
        raise WorkflowError("EVIDENCE_MISMATCH", "build receipt target identity is invalid")
    custom_opp_root = vendor.get("custom_opp_root")
    if not isinstance(custom_opp_root, str):
        raise WorkflowError("EVIDENCE_MISMATCH", "build receipt lacks fresh vendor root")
    if _vendor_root(Path(install_root), library) != Path(custom_opp_root).resolve():
        raise WorkflowError(
            "EVIDENCE_MISMATCH", "fresh vendor ELF and target delivery use different vendors"
        )
    actual_delivery = _target_delivery(
        Path(install_root), Path(custom_opp_root), target["soc"], target["build_token"],
        checked["operator"]["op_type"],
    )
    if actual_delivery != build_receipt.get("target_delivery"):
        raise WorkflowError("EVIDENCE_DRIFT", "installed target kernel delivery changed after build receipt")
    plugin = _ascii_path(execution_plugin, "ATK execution plugin") if execution_plugin else None
    plugin_identity = (
        {"path": str(plugin), "sha256": sha256_file(plugin)} if plugin else None
    )

    work = Path(work_dir)
    if work.exists():
        raise WorkflowError("DIRTY_SESSION", f"ATK execution work directory already exists: {work}")
    work.mkdir(parents=True)
    work = work.resolve()
    preflight = atk_preflight(atk_bin, checked["runner"]["atk_version"], work / "preflight")
    case_atk = case_receipt.get("atk", {})
    if not isinstance(case_atk, dict):
        raise WorkflowError("EVIDENCE_MISMATCH", "case receipt lacks ATK identity")
    if any(preflight.get(field) != case_atk.get(field) for field in ("version", "sha256")):
        raise WorkflowError("ATK_IDENTITY_DRIFT", "ATK executable differs between casegen and execution")
    performance_ids = [coverage_ids[index] for index in checked["task"]["performance_required_cases"]]
    if performance_ids != case_data.get("performance_case_ids"):
        raise WorkflowError("EVIDENCE_DRIFT", "ATK performance subset differs from case receipt")
    require_performance = bool(performance_ids)
    if not isinstance(vendor.get("custom_opp_root"), str):
        raise WorkflowError("EVIDENCE_MISMATCH", "build receipt lacks custom OPP root")
    env = _execution_environment(library, vendor["custom_opp_root"])
    stage_budget = timeout_seconds or checked["runner"]["stage_timeout_seconds"]
    stage_started = time.monotonic()

    accuracy_deadline = stage_started + (stage_budget / 2 if require_performance else stage_budget)
    stage_deadline = stage_started + stage_budget

    def execute(
        task: str,
        destination: Path,
        save_item: str,
        deadline: float,
        selected: list[int] | None = None,
    ) -> dict[str, Any]:
        remaining = int(deadline - time.monotonic())
        if remaining < 1:
            raise WorkflowError("ATK_EXECUTION_TIMEOUT", "ATK execution stage exhausted its outer budget")
        destination.mkdir()
        argv = [
            preflight["path"], "aclnn", str(cases_path),
            "--devices", str(checked["runner"]["device"]),
            "--task", task, "--output", str(destination),
            "--timeout", str(checked["runner"]["case_timeout_seconds"]),
            "--concurrency", "1", "--save_data", save_item,
        ]
        if selected:
            argv.extend(["--white_list", _case_filter(selected)])
        if plugin:
            argv.extend(["--plugin", str(plugin)])
        if sha256_file(preflight["path"]) != preflight["sha256"]:
            raise WorkflowError("ATK_IDENTITY_DRIFT", "ATK executable changed before execution")
        command = run_command(
            argv, cwd=work, timeout_seconds=remaining,
            stdout_path=destination / "atk-run.stdout.log",
            stderr_path=destination / "atk-run.stderr.log", env=env,
        )
        result: dict[str, Any] = {
            "command": command,
            "workbook": None,
            "rows": None,
            "summary": None,
            "error": None,
        }
        if command.timed_out:
            result["error"] = WorkflowError(
                "ATK_EXECUTION_TIMEOUT", f"ATK {task} exceeded the outer timeout"
            )
            return result
        if command.returncode != 0:
            result["error"] = WorkflowError(
                "ATK_EXECUTION_FAILED", f"ATK {task} returned a non-zero status"
            )
            return result
        try:
            workbooks = sorted((destination / "atk_output").rglob("*.xlsx"))
            if len(workbooks) != 1:
                raise WorkflowError(
                    "ATK_REPORT_MISSING",
                    f"expected one ATK {task} workbook, found {len(workbooks)}",
                )
            workbook = workbooks[0].resolve()
            rows, summary = _workbook_rows(workbook)
            result.update({"workbook": workbook, "rows": rows, "summary": summary})
        except WorkflowError as exc:
            result["error"] = exc
        return result

    def accuracy_phase() -> dict[str, Any]:
        phase = execute("accuracy", work / "accuracy", "output:bin", accuracy_deadline)
        if phase["error"] is not None:
            return phase
        try:
            phase["normalized"] = _normalize_rows(phase["rows"], ids, require_performance=False)
            accuracy_count = int(phase["summary"].get("总用例数"))
            if accuracy_count != len(cases):
                raise WorkflowError(
                    "ATK_REPORT_INCOMPLETE", "ATK accuracy denominator does not match caseset"
                )
        except (TypeError, ValueError):
            phase["error"] = WorkflowError(
                "ATK_REPORT_INVALID", "ATK accuracy summary total is not an integer"
            )
        except WorkflowError as exc:
            phase["error"] = exc
        return phase

    def performance_phase() -> dict[str, Any]:
        phase = execute(
            "performance_device", work / "performance", "profile:bin",
            stage_deadline, performance_ids,
        )
        if phase["error"] is not None:
            return phase
        try:
            phase["normalized"] = _normalize_rows(
                phase["rows"], performance_ids, require_performance=True
            )
            performance_count = int(phase["summary"].get("总用例数"))
            if performance_count != len(performance_ids):
                raise WorkflowError(
                    "ATK_REPORT_INCOMPLETE",
                    "ATK performance denominator differs from selected cases",
                )
        except (TypeError, ValueError):
            phase["error"] = WorkflowError(
                "ATK_REPORT_INVALID", "ATK performance summary total is not an integer"
            )
        except WorkflowError as exc:
            phase["error"] = exc
        return phase

    accuracy_phase_result, performance_phase_result = _run_independent_phases(
        accuracy_phase, performance_phase if require_performance else None
    )
    accuracy_error = accuracy_phase_result["error"]
    performance_error = performance_phase_result["error"] if performance_phase_result else None
    accuracy_command = accuracy_phase_result["command"]
    accuracy_workbook = accuracy_phase_result["workbook"]
    accuracy_summary = accuracy_phase_result["summary"]
    accuracy = accuracy_phase_result.get("normalized")
    performance_command = performance_phase_result["command"] if performance_phase_result else None
    performance_workbook = performance_phase_result["workbook"] if performance_phase_result else None
    performance_summary = performance_phase_result["summary"] if performance_phase_result else None
    performance = (
        performance_phase_result.get("normalized")
        if performance_phase_result else {
            "performance_complete": True,
            "performance_missing": 0,
            "performance_execution_failed": 0,
            "performance_execution_failed_ids": [],
            "rows": [],
        }
    )
    command_settle_error: WorkflowError | None = None
    if accuracy_command is not None:
        try:
            accuracy_command = _settled_command_receipt(accuracy_command)
        except WorkflowError as exc:
            command_settle_error = exc
    if performance_command is not None:
        try:
            performance_command = _settled_command_receipt(performance_command)
        except WorkflowError as exc:
            if command_settle_error is None:
                command_settle_error = exc

    successful_performance_ids = [
        row["id"] for row in performance.get("rows", [])
        if row["execution_status"] == "SUCCESS" and row["device_time_us"] is not None
    ] if performance else []
    profiles: list[dict[str, Any]] = []
    diagnostic_profiles: list[dict[str, Any]] = []
    profile_error: WorkflowError | None = None
    if require_performance:
        diagnostic_profiles, profile_error = _raw_profile_evidence(
            work / "performance" / "atk_output", performance_ids
        )
    if performance is not None and performance_error is None and require_performance:
        try:
            profiles = _profile_evidence(
                work / "performance" / "atk_output", successful_performance_ids
            )
        except (OSError, WorkflowError) as exc:
            performance_error = exc if isinstance(exc, WorkflowError) else WorkflowError(
                "ATK_PROFILE_INVALID", f"cannot validate raw profiles: {exc}"
            )

    workspace_symbol = f"aclnn{checked['operator']['aclnn_name']}GetWorkspaceSize"
    loaded: dict[str, Any] | None = None
    evidence_error: WorkflowError | None = command_settle_error
    try:
        logs = sorted((work / "accuracy" / "atk_output").rglob("*.log"))
        if accuracy_command:
            logs.extend([Path(accuracy_command.stdout_path), Path(accuracy_command.stderr_path)])
        if performance_command:
            logs.extend(sorted((work / "performance" / "atk_output").rglob("*.log")))
            logs.extend([Path(performance_command.stdout_path), Path(performance_command.stderr_path)])
        if sha256_file(cases_path) != case_data.get("sha256"):
            raise WorkflowError("EVIDENCE_DRIFT", "ATK caseset changed during execution")
        if sha256_file(preflight["path"]) != preflight["sha256"]:
            raise WorkflowError("ATK_IDENTITY_DRIFT", "ATK executable changed during execution")
        if sha256_file(library) != vendor.get("library_sha256"):
            raise WorkflowError("EVIDENCE_DRIFT", "DUT vendor ELF changed during execution")
        if plugin_identity is not None and (
            plugin is None or sha256_file(plugin) != plugin_identity["sha256"]
        ):
            raise WorkflowError("EVIDENCE_DRIFT", "ATK execution plugin changed during execution")
        loaded = _loaded_library(logs, workspace_symbol, library)
        if loaded.get("library_sha256") != vendor.get("library_sha256"):
            raise WorkflowError("LOADED_LIBRARY_MISMATCH", "ATK loaded vendor digest is not fresh")
    except (OSError, WorkflowError) as exc:
        if evidence_error is None:
            evidence_error = exc if isinstance(exc, WorkflowError) else WorkflowError(
                "EVIDENCE_DRIFT", f"cannot replay post-execution identity: {exc}"
            )
        loaded = None

    def evidence_sha(path: os.PathLike[str] | str, label: str) -> str | None:
        nonlocal evidence_error
        try:
            return sha256_file(path)
        except OSError as exc:
            if evidence_error is None:
                evidence_error = WorkflowError(
                    "EVIDENCE_DRIFT", f"cannot hash {label} after execution: {exc}"
                )
            return None

    case_receipt_sha256 = evidence_sha(case_receipt_path, "case receipt")
    build_receipt_sha256 = evidence_sha(build_receipt_path, "build receipt")
    accuracy_workbook_sha256 = (
        evidence_sha(accuracy_workbook, "accuracy workbook") if accuracy_workbook else None
    )
    performance_workbook_sha256 = (
        evidence_sha(performance_workbook, "performance workbook")
        if performance_workbook else None
    )

    if accuracy_error is not None or performance_error is not None \
            or profile_error is not None or evidence_error is not None:
        performance_attributable = (
            performance_command is not None
            and performance_error is None
            and profile_error is None
            and evidence_error is None
            and loaded is not None
        )
        incomplete = {
            "schema": "oprunway.atk_execution_receipt",
            "schema_version": 1,
            "status": "INCOMPLETE",
            "created_at": utc_now(),
            "spec_sha256": expected_spec,
            "case_receipt_sha256": case_receipt_sha256,
            "build_receipt_sha256": build_receipt_sha256,
            "atk": preflight,
            "commands": {
                "accuracy": accuracy_command.to_dict() if accuracy_command else None,
                "performance": performance_command.to_dict() if performance_command else None,
            },
            "phase_errors": {
                "accuracy": (
                    {"code": accuracy_error.code, "message": str(accuracy_error)}
                    if accuracy_error else None
                ),
                "performance": (
                    {"code": performance_error.code, "message": str(performance_error)}
                    if performance_error else None
                ),
                "evidence": (
                    {"code": evidence_error.code, "message": str(evidence_error)}
                    if evidence_error else None
                ),
                "diagnostic_profiles": (
                    {"code": profile_error.code, "message": str(profile_error)}
                    if profile_error else None
                ),
            },
            "accuracy_evidence": (
                {
                    "path": str(accuracy_workbook),
                    "sha256": accuracy_workbook_sha256,
                    "summary": accuracy_summary,
                    "normalized": accuracy,
                } if accuracy_workbook else None
            ),
            "performance_evidence": (
                {
                    "attributable": performance_attributable,
                    "path": str(performance_workbook) if performance_workbook else None,
                    "sha256": performance_workbook_sha256,
                    "summary": performance_summary,
                    "normalized": performance,
                    "profiles": profiles if performance_attributable else [],
                    "diagnostic_profiles": diagnostic_profiles,
                } if performance_phase_result else None
            ),
            "loaded_vendor": loaded,
            "execution_plugin": plugin_identity,
        }
        atomic_write_json(out_path, incomplete)
        phase_error = accuracy_error or performance_error or profile_error or evidence_error
        assert phase_error is not None
        raise phase_error

    expected_error_ids = [
        case["id"] for case in cases
        if isinstance(case.get("expected_error_msg"), str) and case["expected_error_msg"].strip()
    ]
    expected_error_set = set(expected_error_ids)
    output_ids = [case_id for case_id in ids if case_id not in expected_error_set]
    failed_ids = set(accuracy["execution_failed_ids"])
    required_output_ids = [case_id for case_id in output_ids if case_id not in failed_ids]
    outputs = _saved_outputs(
        work / "accuracy" / "atk_output", output_ids, required_ids=required_output_ids
    )
    assert loaded is not None and accuracy_command is not None and accuracy_workbook is not None
    assert case_receipt_sha256 is not None and build_receipt_sha256 is not None
    assert accuracy_workbook_sha256 is not None
    receipt = {
        "schema": "oprunway.atk_execution_receipt",
        "schema_version": 1,
        "status": "COMPLETE",
        "created_at": utc_now(),
        "spec_sha256": expected_spec,
        "case_receipt_sha256": case_receipt_sha256,
        "build_receipt_sha256": build_receipt_sha256,
        "atk": preflight,
        "commands": {
            "accuracy": accuracy_command.to_dict(),
            "performance": performance_command.to_dict() if performance_command else None,
        },
        "report": {
            "path": str(accuracy_workbook),
            "sha256": accuracy_workbook_sha256,
            "summary": accuracy_summary,
            **accuracy,
            "performance_complete": performance["performance_complete"],
            "performance_missing": performance["performance_missing"],
            "performance_execution_failed": performance["performance_execution_failed"],
            "performance_execution_failed_ids": performance["performance_execution_failed_ids"],
            "performance_case_ids": performance_ids,
            "expected_error_case_ids": expected_error_ids,
            "performance_rows": performance["rows"],
            "performance_workbook": (
                {
                    "path": str(performance_workbook),
                    "sha256": performance_workbook_sha256,
                    "summary": performance_summary,
                } if performance_workbook else None
            ),
        },
        "loaded_vendor": loaded,
        "execution_plugin": plugin_identity,
        "outputs": outputs,
        "profiles": profiles,
    }
    atomic_write_json(out_path, receipt)
    return receipt
