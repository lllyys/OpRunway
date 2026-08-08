#!/usr/bin/env python3
"""Validate a required/generated/executable acceptance ledger.

The ledger is deliberately operator-neutral.  It records facts already emitted by
the normal acceptance workflow and makes denominator loss, unrecorded coverage
gaps, and provenance overstatement mechanically detectable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import content_address
import cpp_extension_adapter
import multi_card_shards
import multi_card_verdict_equivalence
import stochastic_contract
import validate_acceptance_state
import vendor_build_receipt


SCHEMA = "oprunway.multi_operator_rge_ledger"
SCHEMA_VERSION = 2
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORMAL_REQUIRED_ARTIFACTS = frozenset(
    {
        "source_facts",
        "spec",
        "caseset",
        "precision_evidence",
        "verdict",
        "perf_report",
        "acceptance",
        "extension_receipt",
    }
)
_FORMAL_OPTIONAL_ARTIFACTS = frozenset({"stochastic_formal_evidence"})
_FORMAL_ARTIFACTS = _FORMAL_REQUIRED_ARTIFACTS | _FORMAL_OPTIONAL_ARTIFACTS
_REQUIRED_ARTIFACTS = _FORMAL_REQUIRED_ARTIFACTS | {
    "taskdoc_snapshot",
    "vendor_build_receipt",
    "vendor_elf",
    "gate_task1",
    "gate_task2",
}
_PLAN_PHASES = tuple(f"N{index}" for index in range(11))
_PLAN_STATUSES = {
    "VERIFIED",
    "VERIFIED_WITH_STRUCTURED_GAPS",
    "PARTIALLY_VERIFIED",
}
_SOURCE_FACTS_DOMAIN = "oprunway/source-facts/v1"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_GITCODE_MR_URL = re.compile(
    r"^https://gitcode\.com/[^/]+/[^/]+/merge_requests/(?P<number>[1-9][0-9]*)$"
)
_CALLER_INPUT_ASSOCIATION = {
    "schema": "oprunway.caller_trusted_input",
    "schema_version": 1,
    "policy": "caller_trusted_pair_v1",
    "correspondence": "asserted_by_caller",
}
_CONTENT_ANCHOR_KEYS = frozenset(
    {"schema", "schema_version", "algorithm", "scope", "sha256", "file_count"}
)
_MULTICARD_INVENTORY_SCHEMA = "oprunway.multi_card_artifact_inventory"
_MULTICARD_INVENTORY_VERSION = 4
_MULTICARD_INVENTORY_FILENAME = "artifact-inventory-v4.json"
_MULTICARD_SINGLE_INPUT_PATHS = {
    "single_spec": "spec.json",
    "single_caseset": "caseset.json",
    "single_source_facts": "source_facts.json",
    "single_evidence": "evidence.json",
    "single_verdict": "verdict.json",
    "single_receipt": "work/cpp_extension_receipt.json",
}
_MULTICARD_TOP_ROLE_PATHS = {
    "parent_spec": "spec.json",
    "parent_caseset": "parent-caseset.json",
    "staged_caseset": "caseset.json",
    "source_facts": "source_facts.json",
    "vendor_build_receipt": "vendor-build-receipt.json",
    "manifest_alias": "manifest.json",
    "multi_manifest": "multi_card_manifest.json",
    "merged_alias": "merged.json",
    "merged_receipt": "multi_card_merged_evidence.json",
    "assembled_evidence": "evidence.json",
    "verdict": "verdict.json",
    "equivalence": "equivalence.json",
    "gate_log": "gate-task2.log",
    "gate_rc": "gate-task2.rc",
}
_MULTICARD_FORMAL_FIXED = frozenset(
    {
        "caseset.json",
        "cpp_extension_caseset.json",
        "cpp_extension_invocation_plan.json",
        "cpp_extension_receipt.json",
        "cpp_extension/extension_manifest.json",
        "device_identity.json",
        "shard_result.json",
        "evidence.json",
    }
)
_MULTICARD_SMOKE_FIXED = frozenset(
    {
        "caseset.json",
        "cpp_extension_caseset.json",
        "cpp_extension_invocation_plan.json",
        "cpp_extension_receipt.json",
        "cpp_extension/extension_manifest.json",
        "evidence.json",
    }
)
_PLAN_ALL_OPERATOR_ARTIFACTS = {
    "N0": frozenset({"taskdoc_snapshot", "source_facts", "spec", "caseset"}),
    "N1": frozenset({"precision_evidence"}),
    "N2": frozenset({"extension_receipt"}),
    "N3": frozenset({"extension_receipt"}),
    "N4": frozenset({"vendor_build_receipt", "vendor_elf", "extension_receipt"}),
    "N5": frozenset({"caseset"}),
    "N7": frozenset({"caseset"}),
    "N9": frozenset({"perf_report"}),
    "N10": frozenset({"acceptance", "gate_task1", "gate_task2"}),
}
_PLAN_ANY_OPERATOR_ARTIFACTS = {
    "N6": frozenset({"extension_receipt"}),
    "N8": frozenset({"stochastic_formal_evidence"}),
}
_PLAN_REQUIRED_TESTS = {
    "N0": frozenset({"plugin/acc-common/test_spec_change_gate.py"}),
    "N1": frozenset({"plugin/acc-common/test_cpp_extension_driver.py"}),
    "N2": frozenset({"plugin/acc-common/test_cpp_extension_codegen.py"}),
    "N3": frozenset({"plugin/acc-common/test_cann_version.py"}),
    "N4": frozenset(
        {
            "plugin/acc-common/test_cpp_extension_identity.py",
            "plugin/acc-common/test_vendor_build_receipt.py",
        }
    ),
    "N5": frozenset(
        {
            "plugin/acc-common/test_gen_cases_dry_run_ledger.py",
            "plugin/acc-common/test_dtype_requirement_sets_pipeline.py",
        }
    ),
    "N6": frozenset(
        {
            "plugin/acc-common/test_cpp_extension_multi_input.py",
            "plugin/acc-common/test_multi_input_contract.py",
        }
    ),
    "N7": frozenset(
        {
            "plugin/acc-common/test_cpp_extension_n7_layout.py",
            "plugin/acc-common/test_tensor_shape_attrs.py",
        }
    ),
    "N8": frozenset(
        {
            "plugin/acc-common/test_stochastic_contract.py",
            "plugin/acc-common/test_stochastic_validator.py",
        }
    ),
    "N9": frozenset({"plugin/acc-common/test_perf_n9_contract.py"}),
}
_N0_GATE_NAMES = frozenset(
    {"validate_taskdoc_input", "gen_cases_dry_run", "spec_change_gate_check"}
)
_N0_GATE_SCRIPTS = {
    "validate_taskdoc_input": "validate_taskdoc_input.py",
    "gen_cases_dry_run": "gen_cases.py",
    "spec_change_gate_check": "spec_change_gate.py",
}


class LedgerValidationError(ValueError):
    """Raised when an R/G/E ledger violates one or more invariants."""

    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("\n".join(self.errors))


def _content_anchor(value: Any, where: str, errors: list[str]) -> dict[str, Any]:
    """Validate the current caller-trusted source content anchor."""

    row = _as_dict(value, where, errors)
    if set(row) != _CONTENT_ANCHOR_KEYS:
        errors.append(f"{where}: expected exact content-anchor fields")
    if (
        row.get("schema") != "oprunway.source_content_anchor"
        or row.get("schema_version") != 1
        or row.get("algorithm") != "git_blob_manifest_sha256_v1"
        or not isinstance(row.get("scope"), str)
        or not isinstance(row.get("sha256"), str)
        or not _SHA256.fullmatch(row.get("sha256", ""))
        or isinstance(row.get("file_count"), bool)
        or not isinstance(row.get("file_count"), int)
        or row.get("file_count", 0) <= 0
    ):
        errors.append(f"{where}: unsupported source content anchor")
    return row


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_list(value: Any, where: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{where}: expected list")
        return []
    return value


def _as_dict(value: Any, where: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{where}: expected object")
        return {}
    return value


def _integer(mapping: dict[str, Any], key: str, where: str, errors: list[str]) -> int:
    value = mapping.get(key)
    if not _is_int(value) or value < 0:
        errors.append(f"{where}.{key}: expected non-negative integer")
        return 0
    return value


def _strings(value: Any, where: str, errors: list[str]) -> list[str]:
    values = _as_list(value, where, errors)
    if any(not isinstance(item, str) or not item for item in values):
        errors.append(f"{where}: expected non-empty strings")
        return []
    if len(values) != len(set(values)):
        errors.append(f"{where}: duplicate values")
    return values


def _single_gap(
    gaps: list[dict[str, Any]], kind: str, where: str, errors: list[str]
) -> dict[str, Any] | None:
    matched = [gap for gap in gaps if gap.get("kind") == kind]
    if len(matched) > 1:
        errors.append(f"{where}: duplicate {kind!r} gaps")
    return matched[0] if matched else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(content_address.canonical_json_bytes(value)).hexdigest()


def _same(
    actual: Any, expected: Any, where: str, errors: list[str], *, message: str = "drift"
) -> None:
    if actual != expected:
        errors.append(f"{where}: {message}; expected {expected!r}, got {actual!r}")


def _load_json(path: Path, where: str, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{where}: cannot read JSON artifact: {exc}")
        return {}
    return _as_dict(value, where, errors)


def _load_text(path: Path, where: str, errors: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{where}: cannot read text artifact: {exc}")
        return ""


def _artifact_path(
    artifacts: dict[str, Any], name: str, where: str, errors: list[str]
) -> Path | None:
    item = _as_dict(artifacts.get(name), f"{where}.artifacts.{name}", errors)
    raw = item.get("path")
    if not isinstance(raw, str) or not os.path.isabs(raw):
        return None
    path = Path(raw)
    if not path.is_file():
        errors.append(f"{where}.artifacts.{name}.path: artifact does not exist")
        return None
    return path


def _json_artifact(
    artifacts: dict[str, Any], name: str, where: str, errors: list[str]
) -> dict[str, Any]:
    path = _artifact_path(artifacts, name, where, errors)
    return {} if path is None else _load_json(path, f"{where}.artifacts.{name}", errors)


def _text_artifact(
    artifacts: dict[str, Any], name: str, where: str, errors: list[str]
) -> str:
    path = _artifact_path(artifacts, name, where, errors)
    return "" if path is None else _load_text(path, f"{where}.artifacts.{name}", errors)


def _supplemental_text(
    artifacts: dict[str, Any], name: Any, where: str, errors: list[str]
) -> str:
    if not isinstance(name, str) or name not in artifacts:
        errors.append(f"{where}: unknown supplemental artifact {name!r}")
        return ""
    raw = _as_dict(artifacts[name], f"ledger.supplemental_artifacts.{name}", errors)
    path_raw = raw.get("path")
    if not isinstance(path_raw, str) or not os.path.isabs(path_raw):
        return ""
    path = Path(path_raw)
    if not path.is_file():
        errors.append(f"{where}: supplemental artifact does not exist")
        return ""
    return _load_text(path, where, errors)


def _supplemental_json(
    artifacts: dict[str, Any], name: Any, where: str, errors: list[str]
) -> dict[str, Any]:
    if not isinstance(name, str) or name not in artifacts:
        errors.append(f"{where}: unknown supplemental artifact {name!r}")
        return {}
    raw = _as_dict(artifacts[name], f"ledger.supplemental_artifacts.{name}", errors)
    path_raw = raw.get("path")
    if not isinstance(path_raw, str) or not os.path.isabs(path_raw):
        return {}
    path = Path(path_raw)
    if not path.is_file():
        errors.append(f"{where}: supplemental artifact does not exist")
        return {}
    return _load_json(path, where, errors)


def _indexed_inventory_artifacts(
    value: Any,
    expected_names: frozenset[str],
    expected_root: Path,
    where: str,
    verify_artifacts: bool,
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    """Validate an inventory row and index it by basename.

    The inventory itself is only a directory of content-addressed evidence.  It
    is not trusted as a summary: every identity-bearing JSON file is parsed by
    the caller, and ``--verify-artifacts`` re-hashes every listed shard file.
    """

    rows = _as_list(value, where, errors)
    indexed: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        item_where = f"{where}[{index}]"
        item = _as_dict(raw, item_where, errors)
        path_raw = item.get("path")
        digest = item.get("sha256")
        size = item.get("bytes")
        if not isinstance(path_raw, str) or not os.path.isabs(path_raw):
            errors.append(f"{item_where}.path: expected absolute path")
            continue
        path = Path(path_raw)
        name = path.name
        if name in indexed:
            errors.append(f"{where}: duplicate basename {name!r}")
            continue
        indexed[name] = item
        try:
            path.relative_to(expected_root)
        except ValueError:
            errors.append(f"{item_where}.path: outside inventory operator/shard root")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            errors.append(f"{item_where}.sha256: expected lowercase SHA-256")
        if not _is_int(size) or size < 0:
            errors.append(f"{item_where}.bytes: expected non-negative integer")
        if not path.is_file():
            errors.append(f"{item_where}.path: inventory artifact does not exist")
            continue
        measured_size = path.stat().st_size
        if _is_int(size) and measured_size != size:
            errors.append(
                f"{item_where}.bytes: expected {size}, measured {measured_size}"
            )
        if verify_artifacts and isinstance(digest, str) and _SHA256.fullmatch(digest):
            measured = _sha256_file(path)
            if measured != digest:
                errors.append(
                    f"{item_where}.sha256: expected {digest}, measured {measured}"
                )
    names = set(indexed)
    if names != expected_names:
        errors.append(
            f"{where}: exact artifact set drift; "
            f"missing={sorted(expected_names - names)}, extra={sorted(names - expected_names)}"
        )
    return indexed


def _inventory_json(
    item: dict[str, Any] | None, where: str, errors: list[str]
) -> dict[str, Any]:
    if not isinstance(item, dict):
        errors.append(f"{where}: missing inventory JSON artifact")
        return {}
    path_raw = item.get("path")
    if not isinstance(path_raw, str):
        return {}
    return _load_json(Path(path_raw), where, errors)


def _inventory_text(
    item: dict[str, Any] | None, where: str, errors: list[str]
) -> str:
    if not isinstance(item, dict):
        errors.append(f"{where}: missing inventory text artifact")
        return ""
    path_raw = item.get("path")
    if not isinstance(path_raw, str):
        return ""
    return _load_text(Path(path_raw), where, errors)


def _path_has_symlink(base: Path, path: Path) -> bool:
    """Return whether any component below ``base`` is a symlink."""

    try:
        relative = path.relative_to(base)
    except ValueError:
        return True
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _v3_inventory_rows(
    value: Any,
    base: Path,
    where: str,
    verify_artifacts: bool,
    errors: list[str],
    *,
    absolute_paths: bool = False,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Validate content-addressed inventory rows and index role/effective path."""

    rows = _as_list(value, where, errors)
    by_role: dict[str, dict[str, Any]] = {}
    by_path: dict[str, dict[str, Any]] = {}
    base_real = Path(os.path.realpath(base))
    for index, raw in enumerate(rows):
        item_where = f"{where}[{index}]"
        item = _as_dict(raw, item_where, errors)
        if set(item) != {"role", "path", "sha256", "bytes"}:
            errors.append(f"{item_where}: fields must be exactly role/path/sha256/bytes")
        role, path_raw = item.get("role"), item.get("path")
        digest, size = item.get("sha256"), item.get("bytes")
        if not isinstance(role, str) or not role:
            errors.append(f"{item_where}.role: expected non-empty string")
            continue
        if role in by_role:
            errors.append(f"{where}: duplicate role {role!r}")
            continue
        if not isinstance(path_raw, str) or not path_raw:
            errors.append(f"{item_where}.path: expected non-empty string")
            continue
        raw_path = Path(path_raw)
        if absolute_paths:
            if not raw_path.is_absolute():
                errors.append(f"{item_where}.path: external authority path must be absolute")
                continue
            effective = raw_path
        else:
            if raw_path.is_absolute() or ".." in raw_path.parts:
                errors.append(f"{item_where}.path: internal artifact path must be relative and contained")
                continue
            effective = base / raw_path
        effective_real = Path(os.path.realpath(effective))
        if not absolute_paths:
            try:
                effective_real.relative_to(base_real)
            except ValueError:
                errors.append(f"{item_where}.path: realpath escapes artifact root")
                continue
            if _path_has_symlink(base, effective):
                errors.append(f"{item_where}.path: symlink component is forbidden")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            errors.append(f"{item_where}.sha256: expected lowercase SHA-256")
        if not _is_int(size) or size < 0:
            errors.append(f"{item_where}.bytes: expected non-negative integer")
        path_key = str(effective_real)
        if path_key in by_path and not absolute_paths:
            errors.append(f"{where}: duplicate effective path {path_key!r}")
        if not effective.is_file():
            errors.append(f"{item_where}.path: artifact does not exist or is not a regular file")
        else:
            measured_size = effective.stat().st_size
            if _is_int(size) and measured_size != size:
                errors.append(
                    f"{item_where}.bytes: expected {size}, measured {measured_size}"
                )
            if verify_artifacts and isinstance(digest, str) and _SHA256.fullmatch(digest):
                measured = _sha256_file(effective)
                if measured != digest:
                    errors.append(
                        f"{item_where}.sha256: expected {digest}, measured {measured}"
                    )
        normalized = {**item, "effective_path": effective}
        by_role[role] = normalized
        # External authorities are deliberately repeated per shard/stage role: the
        # same vendor/CANN ELF may define several executions.  Internal closure
        # rows, by contrast, must remain one path -> one row.
        by_path.setdefault(path_key, normalized)
    return by_role, by_path


def _regular_file_set(root: Path, where: str, errors: list[str]) -> set[str]:
    """Return the recursive realpath set while rejecting any symlink in the closure."""

    if not root.is_dir() or root.is_symlink():
        errors.append(f"{where}: work root missing/not-directory/symlink")
        return set()
    result: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            errors.append(f"{where}: symlink in work closure: {path}")
            continue
        if path.is_file():
            result.add(str(Path(os.path.realpath(path))))
    return result


def _validate_exact_work_closure(
    work: Path,
    inventory_paths: dict[str, dict[str, Any]],
    fixed_relative_paths: frozenset[str],
    where: str,
    errors: list[str],
) -> None:
    actual = _regular_file_set(work, where, errors)
    registered = set(inventory_paths)
    if actual != registered:
        errors.append(
            f"{where}: exact recursive closure drift; "
            f"missing={sorted(actual - registered)}, extra={sorted(registered - actual)}"
        )
    missing_fixed = sorted(
        rel for rel in fixed_relative_paths if not (work / rel).is_file()
    )
    if missing_fixed:
        errors.append(f"{where}: missing fixed artifacts {missing_fixed}")


def _v3_json(item: dict[str, Any] | None, where: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(item, dict) or not isinstance(item.get("effective_path"), Path):
        errors.append(f"{where}: missing v3 JSON artifact")
        return {}
    return _load_json(item["effective_path"], where, errors)


def _v3_text(item: dict[str, Any] | None, where: str, errors: list[str]) -> str:
    if not isinstance(item, dict) or not isinstance(item.get("effective_path"), Path):
        errors.append(f"{where}: missing v3 text artifact")
        return ""
    return _load_text(item["effective_path"], where, errors)


def _expected_external_authorities(
    receipt: dict[str, Any], prefix: str, where: str, errors: list[str]
) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    vendor = _as_dict(receipt.get("vendor"), f"{where}.vendor", errors)
    library_path, library_sha = vendor.get("library_path"), vendor.get("library_sha256")
    if not isinstance(library_path, str) or not os.path.isabs(library_path) \
            or not isinstance(library_sha, str) or _SHA256.fullmatch(library_sha) is None:
        errors.append(f"{where}.vendor: missing absolute vendor ELF identity")
    else:
        result[f"{prefix}.vendor_elf"] = (library_path, library_sha)
    runtime = _as_dict(receipt.get("runtime"), f"{where}.runtime", errors)
    cann = _as_dict(runtime.get("cann"), f"{where}.runtime.cann", errors)
    probe = _as_dict(cann.get("probe"), f"{where}.runtime.cann.probe", errors)
    defining = probe.get("defining_elf")
    if not isinstance(defining, dict):
        errors.append(f"{where}.runtime.cann.probe.defining_elf: required")
    else:
        path, digest = defining.get("path"), defining.get("sha256")
        if not isinstance(path, str) or not os.path.isabs(path) \
                or not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            errors.append(f"{where}.runtime.cann.probe.defining_elf: invalid identity")
        else:
            result[f"{prefix}.cann_defining_elf"] = (path, digest)
    return result


def _validate_n0_static_projection(
    claim: Any,
    operator_by_id: dict[str, dict[str, Any]],
    supplemental_artifacts: dict[str, Any],
    errors: list[str],
) -> set[str]:
    """Re-project N0 from three independently executed static-gate receipts.

    A phase label or a repository unit test is not evidence that a concrete
    operator spec passed the taskbook, dry-run planner, and spec-change gates.
    This projection therefore follows the saved batch receipt down to every
    command receipt, measured log/rc/output, and the semantic output of all
    three gates.  It also binds the executed producer scripts to this checkout.
    """

    where = "ledger.n0_static_gate_evidence"
    row = _as_dict(claim, where, errors)
    if row.get("status") != "VERIFIED":
        errors.append(f"{where}.status: expected VERIFIED")
    batch_name = row.get("batch_artifact")
    if not isinstance(batch_name, str) or batch_name not in supplemental_artifacts:
        errors.append(f"{where}.batch_artifact: unknown supplemental artifact")
        return set()
    batch_item = _as_dict(
        supplemental_artifacts.get(batch_name), f"{where}.batch_artifact", errors
    )
    batch_path_raw = batch_item.get("path")
    batch_path = Path(batch_path_raw) if isinstance(batch_path_raw, str) else Path("/")
    batch = _supplemental_json(
        supplemental_artifacts, batch_name, f"{where}.batch_artifact", errors
    )
    if (
        batch.get("schema") != "oprunway.n0_static_gate_batch"
        or batch.get("schema_version") != 1
        or batch.get("scope") != "static_only_no_build_no_dut"
        or batch.get("all_passed") is not True
    ):
        errors.append(f"{where}.batch_artifact: invalid N0 batch receipt")

    plugin = _as_dict(batch.get("plugin"), f"{where}.batch.plugin", errors)
    plugin_files = _as_dict(
        plugin.get("files"), f"{where}.batch.plugin.files", errors
    )
    if set(plugin_files) != set(_N0_GATE_SCRIPTS.values()):
        errors.append(f"{where}.batch.plugin.files: exact producer set drift")
    common = Path(__file__).resolve().parent
    for filename in _N0_GATE_SCRIPTS.values():
        path = common / filename
        expected = _sha256_file(path) if path.is_file() else None
        if plugin_files.get(filename) != expected:
            errors.append(
                f"{where}.batch.plugin.files.{filename}: current producer SHA drift"
            )

    claims = _as_list(row.get("operators"), f"{where}.operators", errors)
    claim_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_claim in enumerate(claims):
        item = _as_dict(raw_claim, f"{where}.operators[{index}]", errors)
        op_id = item.get("operator_id")
        if not isinstance(op_id, str) or op_id not in operator_by_id:
            errors.append(f"{where}.operators[{index}].operator_id: unknown operator")
        elif op_id in claim_by_id:
            errors.append(f"{where}.operators[{index}].operator_id: duplicate")
        else:
            claim_by_id[op_id] = item
    if set(claim_by_id) != set(operator_by_id):
        errors.append(f"{where}.operators: must account for every operator")

    batch_operators = _as_dict(
        batch.get("operators"), f"{where}.batch.operators", errors
    )
    required_refs = {batch_name}

    def measured_file(
        raw: Any, label: str, root: Path, *, expected_relative: str | None = None
    ) -> tuple[Path, str] | None:
        item = _as_dict(raw, label, errors)
        path_raw = item.get("path")
        if not isinstance(path_raw, str) or not os.path.isabs(path_raw):
            errors.append(f"{label}.path: expected absolute path")
            return None
        path = Path(path_raw)
        try:
            relative = Path(os.path.realpath(path)).relative_to(
                Path(os.path.realpath(root))
            )
        except ValueError:
            errors.append(f"{label}.path: escapes operator receipt root")
            return None
        if path.is_symlink() or not path.is_file():
            errors.append(f"{label}.path: expected regular non-symlink file")
            return None
        if expected_relative is not None and relative.as_posix() != expected_relative:
            errors.append(
                f"{label}.path: expected {expected_relative!r}, got {relative.as_posix()!r}"
            )
        digest = _sha256_file(path)
        if item.get("sha256") != digest:
            errors.append(f"{label}.sha256: measured file digest drift")
        if "bytes" in item and item.get("bytes") != path.stat().st_size:
            errors.append(f"{label}.bytes: measured file size drift")
        return path, digest

    for op_id, operator in operator_by_id.items():
        op_claim = claim_by_id.get(op_id, {})
        batch_key = op_claim.get("batch_key")
        receipt_name = op_claim.get("receipt_artifact")
        if not isinstance(batch_key, str) or batch_key not in batch_operators:
            errors.append(f"{where}.operators[{op_id}].batch_key: unknown batch row")
            continue
        if not isinstance(receipt_name, str) or receipt_name not in supplemental_artifacts:
            errors.append(
                f"{where}.operators[{op_id}].receipt_artifact: unknown supplemental artifact"
            )
            continue
        required_refs.add(receipt_name)
        batch_op = _as_dict(
            batch_operators.get(batch_key), f"{where}.batch.operators.{batch_key}", errors
        )
        receipt_item = _as_dict(
            supplemental_artifacts.get(receipt_name),
            f"{where}.operators[{op_id}].receipt_artifact",
            errors,
        )
        receipt_path_raw = receipt_item.get("path")
        if not isinstance(receipt_path_raw, str) or not os.path.isabs(receipt_path_raw):
            errors.append(f"{where}.operators[{op_id}].receipt_artifact.path: invalid")
            continue
        receipt_path = Path(receipt_path_raw)
        receipt_root = receipt_path.parent
        if (
            batch_op.get("receipt") != receipt_path_raw
            or batch_op.get("receipt_sha256") != receipt_item.get("sha256")
            or batch_op.get("root") != str(receipt_root)
            or batch_op.get("all_passed") is not True
            or batch_op.get("gate_returncodes") != {name: 0 for name in _N0_GATE_NAMES}
        ):
            errors.append(f"{where}.batch.operators.{batch_key}: receipt projection drift")
        receipt = _supplemental_json(
            supplemental_artifacts,
            receipt_name,
            f"{where}.operators[{op_id}].receipt_artifact",
            errors,
        )
        if (
            receipt.get("schema") != "oprunway.n0_static_operator_receipt"
            or receipt.get("schema_version") != 1
            or receipt.get("scope") != "static_only_no_build_no_dut"
            or receipt.get("operator") != batch_key
            or receipt.get("all_passed") is not True
        ):
            errors.append(f"{where}.operators[{op_id}].receipt_artifact: invalid receipt")

        inputs = _as_dict(
            receipt.get("inputs"), f"{where}.operators[{op_id}].inputs", errors
        )
        expected_inputs = {
            "spec.json",
            "source_facts.json",
            "task_doc.md",
            "taskdoc_validation.json",
            "work/spec_change_receipt.json",
        }
        if set(inputs) != expected_inputs:
            errors.append(f"{where}.operators[{op_id}].inputs: exact input set drift")
        input_files: dict[str, tuple[Path, str]] = {}
        for relative in expected_inputs:
            measured = measured_file(
                inputs.get(relative),
                f"{where}.operators[{op_id}].inputs[{relative}]",
                receipt_root,
                expected_relative=relative,
            )
            if measured is not None:
                input_files[relative] = measured
        formal_artifacts = _as_dict(
            operator.get("artifacts"), f"{where}.operators[{op_id}].formal", errors
        )
        for relative, formal_name in (
            ("spec.json", "spec"),
            ("source_facts.json", "source_facts"),
            ("task_doc.md", "taskdoc_snapshot"),
        ):
            measured = input_files.get(relative)
            formal = _as_dict(
                formal_artifacts.get(formal_name),
                f"{where}.operators[{op_id}].formal.{formal_name}",
                errors,
            )
            if measured is not None and measured[1] != formal.get("sha256"):
                errors.append(
                    f"{where}.operators[{op_id}].inputs[{relative}]: formal artifact SHA drift"
                )

        gates = _as_dict(
            receipt.get("gates"), f"{where}.operators[{op_id}].gates", errors
        )
        if set(gates) != _N0_GATE_NAMES:
            errors.append(f"{where}.operators[{op_id}].gates: exact gate set drift")
        gate_outputs: dict[str, Path] = {}
        for gate_name in _N0_GATE_NAMES:
            gate_where = f"{where}.operators[{op_id}].gates.{gate_name}"
            gate = _as_dict(gates.get(gate_name), gate_where, errors)
            if (
                gate.get("schema") != "oprunway.n0_static_gate_execution"
                or gate.get("schema_version") != 1
                or gate.get("gate") != gate_name
                or gate.get("returncode") != 0
                or gate.get("passed") is not True
            ):
                errors.append(f"{gate_where}: invalid measured gate receipt")
            command = _as_list(gate.get("command"), f"{gate_where}.command", errors)
            if (
                len(command) < 2
                or Path(str(command[1])).name != _N0_GATE_SCRIPTS[gate_name]
            ):
                errors.append(f"{gate_where}.command: producer script drift")
            expected_args = {
                "validate_taskdoc_input": [
                    "--root",
                    str(receipt_root),
                    "--out",
                    "taskdoc-validation-receipt.json",
                ],
                "gen_cases_dry_run": [
                    str(receipt_root / "spec.json"),
                    "--dry-run",
                    "--ledger-out",
                    str(receipt_root / "dry-run-ledger.json"),
                ],
                "spec_change_gate_check": [
                    "--spec",
                    str(receipt_root / "spec.json"),
                    "--out",
                    str(receipt_root),
                    "--check",
                ],
            }[gate_name]
            if command[2:] != expected_args:
                errors.append(f"{gate_where}.command: gate argv drift")
            measured_file(gate.get("log"), f"{gate_where}.log", receipt_root)
            rc_measured = measured_file(
                gate.get("rc_artifact"), f"{gate_where}.rc_artifact", receipt_root
            )
            if rc_measured is not None:
                try:
                    if rc_measured[0].read_text(encoding="ascii").strip() != "0":
                        errors.append(f"{gate_where}.rc_artifact: expected measured rc0")
                except OSError as exc:
                    errors.append(f"{gate_where}.rc_artifact: {exc}")
            outputs = _as_list(gate.get("outputs"), f"{gate_where}.outputs", errors)
            if len(outputs) != 1:
                errors.append(f"{gate_where}.outputs: expected one gate output")
            else:
                expected_output = {
                    "validate_taskdoc_input": "taskdoc-validation-receipt.json",
                    "gen_cases_dry_run": "dry-run-ledger.json",
                    "spec_change_gate_check": "work/spec_change_receipt.json",
                }[gate_name]
                output = measured_file(
                    outputs[0],
                    f"{gate_where}.outputs[0]",
                    receipt_root,
                    expected_relative=expected_output,
                )
                if output is not None:
                    gate_outputs[gate_name] = output[0]
                    if _as_dict(outputs[0], f"{gate_where}.outputs[0]", errors).get(
                        "exists"
                    ) is not True:
                        errors.append(f"{gate_where}.outputs[0].exists: expected true")
            execution = _as_dict(
                gate.get("execution_receipt"), f"{gate_where}.execution_receipt", errors
            )
            execution_measured = measured_file(
                execution, f"{gate_where}.execution_receipt", receipt_root
            )
            if execution_measured is not None:
                saved_execution = _load_json(
                    execution_measured[0], f"{gate_where}.execution_receipt", errors
                )
                expected_execution = dict(gate)
                expected_execution.pop("execution_receipt", None)
                if saved_execution != expected_execution:
                    errors.append(f"{gate_where}.execution_receipt: exact receipt drift")

        taskdoc_path = gate_outputs.get("validate_taskdoc_input")
        if taskdoc_path is not None:
            taskdoc_receipt = _load_json(
                taskdoc_path, f"{where}.operators[{op_id}].taskdoc_output", errors
            )
            payload = _as_dict(
                taskdoc_receipt.get("payload"),
                f"{where}.operators[{op_id}].taskdoc_output.payload",
                errors,
            )
            try:
                taskdoc_digest = content_address.content_digest(
                    "oprunway/taskdoc-validation-receipt/v1", payload
                )
            except content_address.ContentAddressError as exc:
                errors.append(f"{where}.operators[{op_id}].taskdoc_output: {exc}")
                taskdoc_digest = None
            if (
                taskdoc_receipt.get("domain")
                != "oprunway/taskdoc-validation-receipt/v1"
                or taskdoc_receipt.get("schema_version") != 1
                or taskdoc_receipt.get("digest") != taskdoc_digest
                or payload.get("status") != "PASSED"
                or payload.get("blocking_items") != []
                or payload.get("errors") != []
            ):
                errors.append(f"{where}.operators[{op_id}].taskdoc_output: semantic drift")
            bindings = _as_dict(
                payload.get("bindings"),
                f"{where}.operators[{op_id}].taskdoc_output.payload.bindings",
                errors,
            )
            facts = _load_json(
                input_files.get("source_facts.json", (Path("/"), ""))[0],
                f"{where}.operators[{op_id}].source_facts",
                errors,
            )
            if (
                bindings.get("taskdoc_bytes_sha256")
                != input_files.get("task_doc.md", (None, None))[1]
                or bindings.get("source_facts_digest") != facts.get("digest")
            ):
                errors.append(f"{where}.operators[{op_id}].taskdoc_output: input binding drift")

        dry_path = gate_outputs.get("gen_cases_dry_run")
        if dry_path is not None:
            dry = _load_json(dry_path, f"{where}.operators[{op_id}].dry_run", errors)
            core = dict(dry)
            digest = core.pop("ledger_digest", None)
            try:
                expected_digest = content_address.content_digest(
                    "oprunway/case-plan/v1", core
                )
            except content_address.ContentAddressError as exc:
                errors.append(f"{where}.operators[{op_id}].dry_run: {exc}")
                expected_digest = None
            spec_path = input_files.get("spec.json", (Path("/"), ""))[0]
            spec = _load_json(spec_path, f"{where}.operators[{op_id}].spec", errors)
            if (
                dry.get("schema") != "oprunway.gen_cases.dry_run_ledger"
                or dry.get("schema_version") != 1
                or digest != expected_digest
                or _as_dict(dry.get("spec_binding"), "dry.spec_binding", errors).get(
                    "sha256"
                )
                != _canonical_sha256(spec)
                or _as_dict(dry.get("planner_binding"), "dry.planner_binding", errors).get(
                    "gen_cases_py_sha256"
                )
                != plugin_files.get("gen_cases.py")
                or _as_dict(dry.get("planning"), "dry.planning", errors).get(
                    "case_target"
                )
                != operator.get("required", {}).get("case_target")
                or _as_dict(dry.get("summary"), "dry.summary", errors).get("emitted")
                != operator.get("generated", {}).get("case_count")
            ):
                errors.append(f"{where}.operators[{op_id}].dry_run: semantic projection drift")

        spec_change_path = gate_outputs.get("spec_change_gate_check")
        if spec_change_path is not None:
            spec_change = _load_json(
                spec_change_path, f"{where}.operators[{op_id}].spec_change", errors
            )
            if (
                spec_change.get("schema") != "oprunway.spec_change_receipt"
                or spec_change.get("schema_version") != 1
                or spec_change.get("spec_sha256")
                != input_files.get("spec.json", (None, None))[1]
            ):
                errors.append(f"{where}.operators[{op_id}].spec_change: semantic drift")

    if set(batch_operators) != {
        claim.get("batch_key") for claim in claim_by_id.values()
    }:
        errors.append(f"{where}.batch.operators: unclaimed or duplicate batch rows")
    if not isinstance(batch_path_raw, str) or batch_path.name != "n0-static-batch-receipt.json":
        errors.append(f"{where}.batch_artifact.path: unexpected batch filename")
    return required_refs


def _validate_n2_tensor_format(
    work: Path,
    manifest: dict[str, Any],
    receipt: dict[str, Any],
    where: str,
    errors: list[str],
) -> None:
    """Re-project N2 from manifest + driver receipt + generated C++ bytes.

    The human-facing ``format: nd`` spelling is only the requested semantic
    format.  N2 is complete only when the producer also records the effective
    ACL enum and the generated ``aclCreateTensor`` call uses that enum.
    """

    explicit = manifest.get("tensor_format_receipt")
    multi = manifest.get("multi_input_receipt")
    if explicit is not None:
        expected = {
            "requested": "nd",
            "effective_acl_format": "ACL_FORMAT_ND",
            "source": "spec_declared",
        }
        if explicit != expected or receipt.get("tensor_format_receipt") != expected:
            errors.append(f"{where}: explicit ND format receipt drift")
    elif multi is not None:
        if receipt.get("multi_input_receipt") != multi:
            errors.append(f"{where}: multi-input format receipt is not mirrored")
        tensors = _as_list(
            _as_dict(multi, f"{where}.multi_input_receipt", errors).get(
                "tensor_parameters"
            ),
            f"{where}.multi_input_receipt.tensor_parameters",
            errors,
        )
        if not tensors:
            errors.append(f"{where}.multi_input_receipt.tensor_parameters: empty")
        for index, raw in enumerate(tensors):
            item = _as_dict(
                raw, f"{where}.multi_input_receipt.tensor_parameters[{index}]", errors
            )
            expected = {
                "format": "nd",
                "requested_format": "nd",
                "effective_acl_format": "ACL_FORMAT_ND",
                "format_source": "spec_parameter_contract",
            }
            if any(item.get(key) != value for key, value in expected.items()):
                errors.append(
                    f"{where}.multi_input_receipt.tensor_parameters[{index}]: "
                    "requested/effective ACL_FORMAT_ND projection drift"
                )
    else:
        errors.append(f"{where}: N2 has no explicit tensor format receipt")

    source_path = work / "cpp_extension/csrc/oprunway_extension.cpp"
    source = _load_text(source_path, f"{where}.generated_cpp", errors)
    if re.search(
        r"return\s+aclCreateTensorFunc\([\s\S]*?ACL_FORMAT_ND\s*,", source
    ) is None:
        errors.append(
            f"{where}.generated_cpp: aclCreateTensor call does not use ACL_FORMAT_ND"
        )


def _validate_multicard_projection(
    record: dict[str, Any],
    operator: dict[str, Any],
    inventory: Any,
    inventory_path: Path,
    where: str,
    verify_artifacts: bool,
    errors: list[str],
) -> None:
    """Re-project the current formal multi-card run from its on-disk closure."""

    row = _as_dict(inventory, f"{where}.inventory", errors)
    if row.get("schema") != _MULTICARD_INVENTORY_SCHEMA \
            or row.get("schema_version") != _MULTICARD_INVENTORY_VERSION:
        errors.append(
            f"{where}.inventory: expected multi-card inventory schema v4"
        )
        return
    if row.get("generated_from") != {
        "task2_gate_replay_entry": "validate_acceptance_state._gate_multi_card_receipt",
        "equivalence_replay_entry":
            "multi_card_verdict_equivalence.build_equivalence",
        "inventory_scope": "precision_task2_transitive_closure",
    }:
        errors.append(f"{where}.inventory.generated_from: unsupported producer contract")
    expected_inventory_keys = {
        "schema", "schema_version", "artifact_root", "generated_from", "artifacts",
        "top_work_artifacts", "shards", "single_work", "single_artifacts",
        "single_root", "single_root_artifacts", "single_equivalence_inputs",
        "external_artifacts",
    }
    if set(row) != expected_inventory_keys:
        errors.append(
            f"{where}.inventory: exact top-level fields drift; "
            f"missing={sorted(expected_inventory_keys - set(row))}, "
            f"extra={sorted(set(row) - expected_inventory_keys)}")
    root_raw = row.get("artifact_root")
    if not isinstance(root_raw, str) or not os.path.isabs(root_raw):
        errors.append(f"{where}.inventory.artifact_root: expected absolute path")
        return
    root = Path(root_raw)
    root_real = Path(os.path.realpath(root))
    if root.is_symlink() or not root.is_dir() or root_real != root:
        errors.append(f"{where}.inventory.artifact_root: must be an existing canonical directory")
        return
    if Path(os.path.realpath(inventory_path.parent)) != root_real \
            or inventory_path.name != _MULTICARD_INVENTORY_FILENAME \
            or inventory_path.is_symlink():
        errors.append(
            f"{where}.inventory: file must be artifact_root/"
            f"{_MULTICARD_INVENTORY_FILENAME}"
        )

    top, _top_paths = _v3_inventory_rows(
        row.get("artifacts"), root, f"{where}.inventory.artifacts",
        verify_artifacts, errors)
    if set(top) != set(_MULTICARD_TOP_ROLE_PATHS):
        errors.append(
            f"{where}.inventory.artifacts: exact role set drift; "
            f"missing={sorted(set(_MULTICARD_TOP_ROLE_PATHS) - set(top))}, "
            f"extra={sorted(set(top) - set(_MULTICARD_TOP_ROLE_PATHS))}")
    for role, relative in _MULTICARD_TOP_ROLE_PATHS.items():
        item = top.get(role)
        if isinstance(item, dict) and item.get("path") != relative:
            errors.append(f"{where}.inventory.artifacts[{role}].path: expected {relative!r}")
    top_work = root / "work"
    _top_work_rows, top_work_paths = _v3_inventory_rows(
        row.get("top_work_artifacts"), root,
        f"{where}.inventory.top_work_artifacts", verify_artifacts, errors)
    top_work_prefix = str(top_work) + os.sep
    top_work_subset = {
        path: item for path, item in top_work_paths.items()
        if path.startswith(top_work_prefix)
    }
    if set(top_work_subset) != set(top_work_paths):
        errors.append(f"{where}.inventory.top_work_artifacts: paths escape artifact_root/work")
    _validate_exact_work_closure(
        top_work, top_work_subset, frozenset(),
        f"{where}.inventory.top_work", errors)

    spec = _v3_json(top.get("parent_spec"), f"{where}.spec", errors)
    parent_caseset = _v3_json(top.get("parent_caseset"), f"{where}.parent_caseset", errors)
    caseset = _v3_json(top.get("staged_caseset"), f"{where}.caseset", errors)
    source_facts = _v3_json(top.get("source_facts"), f"{where}.source_facts", errors)
    vendor_receipt = _v3_json(
        top.get("vendor_build_receipt"), f"{where}.vendor_build_receipt", errors)
    manifest_alias = _v3_json(top.get("manifest_alias"), f"{where}.manifest_alias", errors)
    manifest = _v3_json(top.get("multi_manifest"), f"{where}.manifest", errors)
    merged_alias = _v3_json(top.get("merged_alias"), f"{where}.merged_alias", errors)
    merged = _v3_json(top.get("merged_receipt"), f"{where}.merged", errors)
    envelope = _v3_json(top.get("assembled_evidence"), f"{where}.evidence", errors)
    multi_verdict = _v3_json(top.get("verdict"), f"{where}.verdict", errors)
    equivalence = _v3_json(top.get("equivalence"), f"{where}.equivalence", errors)
    if manifest_alias != manifest or top.get("manifest_alias", {}).get("sha256") \
            != top.get("multi_manifest", {}).get("sha256"):
        errors.append(f"{where}.inventory: manifest aliases differ")
    if merged_alias != merged or top.get("merged_alias", {}).get("sha256") \
            != top.get("merged_receipt", {}).get("sha256"):
        errors.append(f"{where}.inventory: merged aliases differ")

    formal_artifacts = _as_dict(
        operator.get("artifacts"), f"{where}.formal_artifacts", errors)
    formal_spec = _json_artifact(formal_artifacts, "spec", where, errors)
    formal_caseset = _json_artifact(formal_artifacts, "caseset", where, errors)
    formal_source = _json_artifact(formal_artifacts, "source_facts", where, errors)
    formal_build = _json_artifact(formal_artifacts, "vendor_build_receipt", where, errors)
    formal_verdict = _json_artifact(formal_artifacts, "verdict", where, errors)
    for name, actual, expected in (
        ("spec", spec, formal_spec), ("parent_caseset", parent_caseset, formal_caseset),
        ("staged_caseset", caseset, formal_caseset),
        ("source_facts", source_facts, formal_source),
        ("vendor_build_receipt", vendor_receipt, formal_build),
        ("verdict", multi_verdict, formal_verdict),
    ):
        if actual != expected:
            errors.append(f"{where}.{name}: multi/single formal projection drift")
    if envelope.get("evidence") != merged.get("evidence") \
            or envelope.get("multi_card_receipt") != merged:
        errors.append(f"{where}.evidence: merged evidence/receipt projection drift")

    try:
        multi_card_shards.validate_manifest(manifest, spec, caseset, source_facts)
    except multi_card_shards.ShardContractError as exc:
        errors.append(f"{where}.manifest: deterministic validation failed: {exc}")

    single_root_raw = row.get("single_root")
    if not isinstance(single_root_raw, str) or not os.path.isabs(single_root_raw):
        errors.append(f"{where}.inventory.single_root: expected absolute path")
        single_root = Path("/")
    else:
        single_root = Path(single_root_raw)
        if (
            single_root.is_symlink()
            or not single_root.is_dir()
            or Path(os.path.realpath(single_root)) != single_root
        ):
            errors.append(
                f"{where}.inventory.single_root: expected canonical non-symlink directory"
            )
    _single_root_rows, single_root_paths = _v3_inventory_rows(
        row.get("single_root_artifacts"),
        single_root,
        f"{where}.inventory.single_root_artifacts",
        verify_artifacts,
        errors,
    )
    _validate_exact_work_closure(
        single_root,
        single_root_paths,
        frozenset(_MULTICARD_SINGLE_INPUT_PATHS.values()),
        f"{where}.inventory.single_root",
        errors,
    )
    single_inputs, _single_input_paths = _v3_inventory_rows(
        row.get("single_equivalence_inputs"),
        single_root,
        f"{where}.inventory.single_equivalence_inputs",
        verify_artifacts,
        errors,
    )
    if set(single_inputs) != set(_MULTICARD_SINGLE_INPUT_PATHS):
        errors.append(
            f"{where}.inventory.single_equivalence_inputs: exact role set drift"
        )
    for role, relative in _MULTICARD_SINGLE_INPUT_PATHS.items():
        item = single_inputs.get(role)
        if not isinstance(item, dict) or item.get("path") != relative:
            errors.append(
                f"{where}.inventory.single_equivalence_inputs[{role}].path: "
                f"expected {relative!r}"
            )
            continue
        effective = item.get("effective_path")
        effective_key = str(effective) if isinstance(effective, Path) else ""
        root_item = single_root_paths.get(effective_key)
        if not isinstance(root_item, dict) or any(
            root_item.get(key) != item.get(key) for key in ("path", "sha256", "bytes")
        ):
            errors.append(
                f"{where}.inventory.single_equivalence_inputs[{role}]: "
                "not identical to registered single-root closure"
            )

    single_spec = _v3_json(single_inputs.get("single_spec"), f"{where}.single_spec", errors)
    single_caseset = _v3_json(
        single_inputs.get("single_caseset"), f"{where}.single_caseset", errors
    )
    single_source = _v3_json(
        single_inputs.get("single_source_facts"), f"{where}.single_source_facts", errors
    )
    single_evidence = _v3_json(
        single_inputs.get("single_evidence"), f"{where}.single_evidence", errors
    )
    single_verdict = _v3_json(
        single_inputs.get("single_verdict"), f"{where}.single_verdict", errors
    )
    single_receipt = _v3_json(
        single_inputs.get("single_receipt"), f"{where}.single_receipt", errors
    )
    for name, actual, expected in (
        ("spec", single_spec, formal_spec),
        ("caseset", single_caseset, formal_caseset),
        ("source_facts", single_source, formal_source),
        ("verdict", single_verdict, formal_verdict),
    ):
        if actual != expected:
            errors.append(f"{where}.single_{name}: fresh/formal projection drift")

    single_work_raw = row.get("single_work")
    if not isinstance(single_work_raw, str) or not os.path.isabs(single_work_raw):
        errors.append(f"{where}.inventory.single_work: expected absolute path")
        single_work = Path("/")
    else:
        single_work = Path(single_work_raw)
        if single_work.is_symlink() or Path(os.path.realpath(single_work)) != single_work \
                or single_work != single_root / "work":
            errors.append(
                f"{where}.inventory.single_work: must equal fresh single_root/work")
    single_rows, single_paths = _v3_inventory_rows(
        row.get("single_artifacts"), single_work,
        f"{where}.inventory.single_artifacts", verify_artifacts, errors)
    _validate_exact_work_closure(
        single_work, single_paths,
        frozenset({"cpp_extension_caseset.json", "cpp_extension_invocation_plan.json",
                   "cpp_extension_receipt.json",
                   "cpp_extension/extension_manifest.json"}),
        f"{where}.inventory.single_work", errors)
    receipt_input = single_inputs.get("single_receipt")
    if isinstance(receipt_input, dict) and receipt_input.get("effective_path") \
            != single_work / "cpp_extension_receipt.json":
        errors.append(f"{where}.inventory.single_work: receipt input path is not canonical")
    try:
        validated_single = cpp_extension_adapter.validate_receipt(
            str(single_work), single_caseset)
        if validated_single != single_receipt \
                or single_evidence.get("cpp_extension_receipt") != validated_single:
            errors.append(f"{where}.single_work: adapter receipt/evidence projection drift")
        single_manifest = _load_json(
            single_work / "cpp_extension/extension_manifest.json",
            f"{where}.single_work.manifest",
            errors,
        )
        _validate_n2_tensor_format(
            single_work,
            single_manifest,
            validated_single,
            f"{where}.single_work.tensor_format",
            errors,
        )
    except (OSError, ValueError, cpp_extension_adapter.CppExtensionAdapterError) as exc:
        errors.append(f"{where}.single_work: adapter validation failed: {exc}")

    single_gate_errors: list[str] = []
    single_source_item = single_inputs.get("single_source_facts")
    single_source_path = (
        single_source_item.get("effective_path")
        if isinstance(single_source_item, dict)
        else None
    )
    try:
        validate_acceptance_state.gate_task2(
            str(single_root), single_gate_errors, single_source_path
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        single_gate_errors.append(f"unexpected gate exception: {exc}")
    if single_gate_errors:
        errors.append(f"{where}.single_task2_replay: " + "; ".join(single_gate_errors))

    shard_rows = _as_list(row.get("shards"), f"{where}.inventory.shards", errors)
    manifest_shards_raw = _as_list(manifest.get("shards"), f"{where}.manifest.shards", errors)
    manifest_shards = [item for item in manifest_shards_raw if isinstance(item, dict)]
    manifest_by_id = {item.get("shard_id"): item for item in manifest_shards}
    seen_ids: list[str] = []
    on_disk_results: list[dict[str, Any]] = []
    external_expected = _expected_external_authorities(
        single_receipt, "single", f"{where}.single_receipt", errors)
    for index, raw_shard in enumerate(shard_rows):
        shard = _as_dict(raw_shard, f"{where}.inventory.shards[{index}]", errors)
        shard_where = f"{where}.inventory.shards[{index}]"
        sid, device_id = shard.get("shard_id"), shard.get("device_id")
        if not isinstance(sid, str) or not sid or sid in seen_ids:
            errors.append(f"{shard_where}.shard_id: missing/duplicate")
            continue
        seen_ids.append(sid)
        manifest_shard = manifest_by_id.get(sid)
        if not isinstance(manifest_shard, dict) or manifest_shard.get("device_id") != device_id:
            errors.append(f"{shard_where}: shard/device identity differs from manifest")
            continue
        formal_work = root / sid
        smoke_work = root / f"{sid}.pre-smoke"
        for key, expected_relative in (
            ("formal_work", sid), ("smoke_work", f"{sid}.pre-smoke")):
            if shard.get(key) != expected_relative:
                errors.append(f"{shard_where}.{key}: expected {expected_relative!r}")
        formal_rows, formal_paths = _v3_inventory_rows(
            shard.get("formal_artifacts"), root,
            f"{shard_where}.formal_artifacts", verify_artifacts, errors)
        smoke_rows, smoke_paths = _v3_inventory_rows(
            shard.get("smoke_artifacts"), root,
            f"{shard_where}.smoke_artifacts", verify_artifacts, errors)
        formal_prefix = str(formal_work) + os.sep
        smoke_prefix = str(smoke_work) + os.sep
        formal_subset = {path: item for path, item in formal_paths.items()
                         if path.startswith(formal_prefix)}
        smoke_subset = {path: item for path, item in smoke_paths.items()
                        if path.startswith(smoke_prefix)}
        if set(formal_subset) != set(formal_paths):
            errors.append(f"{shard_where}.formal_artifacts: paths escape formal work")
        if set(smoke_subset) != set(smoke_paths):
            errors.append(f"{shard_where}.smoke_artifacts: paths escape smoke work")
        _validate_exact_work_closure(
            formal_work, formal_subset, _MULTICARD_FORMAL_FIXED,
            f"{shard_where}.formal_work", errors)
        _validate_exact_work_closure(
            smoke_work, smoke_subset, _MULTICARD_SMOKE_FIXED,
            f"{shard_where}.smoke_work", errors)

        def artifact(relative: str, *, smoke: bool = False) -> dict[str, Any] | None:
            work = smoke_work if smoke else formal_work
            paths = smoke_paths if smoke else formal_paths
            return paths.get(str(Path(os.path.realpath(work / relative))))

        shard_caseset = _v3_json(artifact("caseset.json"), f"{shard_where}.caseset", errors)
        cpp_caseset = _v3_json(
            artifact("cpp_extension_caseset.json"), f"{shard_where}.cpp_caseset", errors)
        shard_envelope = _v3_json(
            artifact("evidence.json"), f"{shard_where}.evidence", errors)
        shard_receipt = _v3_json(
            artifact("cpp_extension_receipt.json"), f"{shard_where}.receipt", errors)
        device_identity = _v3_json(
            artifact("device_identity.json"), f"{shard_where}.device_identity", errors)
        shard_result = _v3_json(
            artifact("shard_result.json"), f"{shard_where}.result", errors)
        smoke_receipt = _v3_json(
            artifact("cpp_extension_receipt.json", smoke=True),
            f"{shard_where}.smoke_receipt", errors)
        shard_manifest = _v3_json(
            artifact("cpp_extension/extension_manifest.json"),
            f"{shard_where}.manifest", errors)
        smoke_manifest = _v3_json(
            artifact("cpp_extension/extension_manifest.json", smoke=True),
            f"{shard_where}.smoke_manifest", errors)
        _validate_n2_tensor_format(
            formal_work, shard_manifest, shard_receipt,
            f"{shard_where}.formal_tensor_format", errors)
        _validate_n2_tensor_format(
            smoke_work, smoke_manifest, smoke_receipt,
            f"{shard_where}.smoke_tensor_format", errors)
        if shard_caseset != cpp_caseset:
            errors.append(f"{shard_where}: caseset snapshots differ")
        if shard_envelope.get("cpp_extension_receipt") != shard_receipt:
            errors.append(f"{shard_where}: evidence receipt projection drift")
        for key, measured in (
            ("formal_receipt_sha256", artifact("cpp_extension_receipt.json").get("sha256")
             if artifact("cpp_extension_receipt.json") else None),
            ("smoke_receipt_sha256", artifact(
                "cpp_extension_receipt.json", smoke=True).get("sha256")
             if artifact("cpp_extension_receipt.json", smoke=True) else None),
            ("result_sha256", artifact("shard_result.json").get("sha256")
             if artifact("shard_result.json") else None),
            ("execution_identity_sha256", artifact("device_identity.json").get("sha256")
             if artifact("device_identity.json") else None),
        ):
            if shard.get(key) != measured:
                errors.append(f"{shard_where}.{key}: content digest drift")
        try:
            replayed = multi_card_shards.build_result(
                manifest, manifest_shard, shard_caseset, shard_envelope, device_identity)
            if replayed != shard_result:
                errors.append(f"{shard_where}.shard_result: exact replay drift")
        except multi_card_shards.ShardContractError as exc:
            errors.append(f"{shard_where}.shard_result: exact replay failed: {exc}")
        on_disk_results.append(shard_result)
        external_expected.update(_expected_external_authorities(
            shard_receipt, f"{sid}.formal", f"{shard_where}.formal_receipt", errors))
        external_expected.update(_expected_external_authorities(
            smoke_receipt, f"{sid}.smoke", f"{shard_where}.smoke_receipt", errors))

    expected_ids = [item.get("shard_id") for item in manifest_shards]
    if seen_ids != expected_ids or len(seen_ids) != len(set(seen_ids)):
        errors.append(f"{where}.inventory.shards: identity/order differs from manifest")
    external_rows, _ = _v3_inventory_rows(
        row.get("external_artifacts"), root, f"{where}.inventory.external_artifacts",
        verify_artifacts, errors, absolute_paths=True)
    if set(external_rows) != set(external_expected):
        errors.append(
            f"{where}.inventory.external_artifacts: exact authority role set drift; "
            f"missing={sorted(set(external_expected) - set(external_rows))}, "
            f"extra={sorted(set(external_rows) - set(external_expected))}")
    for role, (path, digest) in external_expected.items():
        item = external_rows.get(role)
        if isinstance(item, dict) and (item.get("path") != path or item.get("sha256") != digest):
            errors.append(f"{where}.inventory.external_artifacts[{role}]: identity drift")

    try:
        replayed_merged = multi_card_shards.merge_results(
            manifest, on_disk_results, spec, caseset, source_facts)
        if replayed_merged != merged:
            errors.append(f"{where}.merged: on-disk deterministic replay drift")
    except multi_card_shards.ShardContractError as exc:
        errors.append(f"{where}.merged: deterministic replay failed: {exc}")
    gate_errors: list[str] = []
    source_item = top.get("source_facts")
    source_path = str(source_item.get("effective_path")) if isinstance(source_item, dict) else None
    try:
        validate_acceptance_state._gate_multi_card_receipt(
            str(root), caseset, envelope, gate_errors, source_path)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        gate_errors.append(f"unexpected gate exception: {exc}")
    if gate_errors:
        errors.append(f"{where}.task2_replay: " + "; ".join(gate_errors))

    manifest_sha = top.get("multi_manifest", {}).get("sha256")
    multi_sha = top.get("verdict", {}).get("sha256")
    single_sha = formal_artifacts.get("verdict", {}).get("sha256")
    if all(isinstance(value, str) for value in (manifest_sha, multi_sha, single_sha)):
        try:
            replayed_equivalence = multi_card_verdict_equivalence.build_equivalence(
                multi_verdict, single_verdict, manifest, spec, caseset, source_facts,
                merged, single_evidence, single_receipt, str(single_work),
                multi_sha=multi_sha, single_sha=single_sha, manifest_sha=manifest_sha)
            if replayed_equivalence != equivalence:
                errors.append(f"{where}.equivalence: deterministic replay drift")
        except (KeyError, TypeError, ValueError,
                cpp_extension_adapter.CppExtensionAdapterError,
                multi_card_shards.ShardContractError) as exc:
            errors.append(f"{where}.equivalence: deterministic replay failed: {exc}")

    devices = [item.get("device_id") for item in manifest_shards]
    sizes = [len(item.get("case_ids", [])) for item in manifest_shards]
    produced, failed = [], []
    for result in on_disk_results:
        statuses = [item.get("status") for item in result.get("evidence", [])
                    if isinstance(item, dict)]
        produced.append(sum(status == "ok" for status in statuses))
        failed.append(sum(status == "execution_failed" for status in statuses))
    for key, actual in (
        ("devices", devices), ("shard_sizes", sizes),
        ("shard_produced", produced), ("shard_failed", failed),
        ("partition", manifest.get("partition")),
        ("merged_total", len(manifest.get("case_order", []))),
        ("single_verdict_sha256", single_sha), ("multi_verdict_sha256", multi_sha),
    ):
        _same(
            actual,
            record.get(key),
            f"{where}.{key}",
            errors,
            message="multi-card projection drift",
        )
    accuracy = _as_dict(
        multi_verdict.get("accuracy_summary"), f"{where}.verdict.accuracy_summary", errors)
    _same(accuracy.get("passed"), record.get("merged_passed"),
          f"{where}.merged_passed", errors, message="verdict projection drift")
    _same(accuracy.get("errored"), record.get("merged_failed"),
          f"{where}.merged_failed", errors, message="verdict projection drift")
    formal_ids = _as_list(
        _as_dict(manifest.get("affinity"), f"{where}.manifest.affinity", errors).get(
            "formal_case_ids"),
        f"{where}.manifest.affinity.formal_case_ids", errors)
    declared_affinity = record.get("formal_witness_affinity")
    if formal_ids:
        owners = [
            item.get("device_id") for item in manifest_shards
            if set(formal_ids) & set(item.get("case_ids", []))
        ]
        expected_affinity = {
            "count": len(formal_ids),
            "device": owners[0] if len(owners) == 1 else None,
            "cross_device_voting": False,
        }
        _same(expected_affinity, declared_affinity,
              f"{where}.formal_witness_affinity", errors,
              message="formal owner projection drift")
    elif declared_affinity is not None:
        errors.append(f"{where}.formal_witness_affinity: non-stochastic run cannot claim affinity")
    if equivalence.get("schema") != "oprunway.multi_card_precision_equivalence" \
            or equivalence.get("schema_version") != 1 \
            or equivalence.get("scope") != "precision_only" \
            or equivalence.get("passed") is not True:
        errors.append(f"{where}.equivalence: expected passed precision-only v1 receipt")
    gate_log = _v3_text(top.get("gate_log"), f"{where}.gate_log", errors)
    gate_rc = _v3_text(top.get("gate_rc"), f"{where}.gate_rc", errors)
    if gate_rc.strip() != "0" or "stage=task2" not in gate_log \
            or "STATUS: PASSED" not in gate_log:
        errors.append(f"{where}.gate_trace: expected recorded Task2 PASSED/rc0")


def _validate_repository_test_ref(
    ref: dict[str, Any],
    where: str,
    supplemental_artifacts: dict[str, Any],
    errors: list[str],
) -> None:
    path = ref.get("path")
    if (
        not isinstance(path, str)
        or not path.startswith("plugin/acc-common/test_")
        or not path.endswith(".py")
        or os.path.isabs(path)
        or ".." in Path(path).parts
    ):
        errors.append(f"{where}.path: invalid repository test path")
        return
    if ref.get("environment") != "A3" or ref.get("result") != "PASSED":
        errors.append(f"{where}: repository test must record A3/PASSED")
    log = _supplemental_text(
        supplemental_artifacts, ref.get("log_artifact"), f"{where}.log_artifact", errors
    )
    rc = _supplemental_text(
        supplemental_artifacts, ref.get("rc_artifact"), f"{where}.rc_artifact", errors
    )
    module = Path(path).stem
    if f"({module}." not in log:
        errors.append(f"{where}.log_artifact: log does not identify {module}")
    if re.search(r"^Ran [1-9][0-9]* tests? in ", log, re.MULTILINE) is None or re.search(
        r"^OK(?: \(skipped=[0-9]+\))?$", log, re.MULTILINE
    ) is None:
        errors.append(f"{where}.log_artifact: unittest terminal is not OK")
    if rc.strip() != "0":
        errors.append(f"{where}.rc_artifact: expected measured rc 0")


def _validate_artifacts(
    operator: dict[str, Any], where: str, verify_artifacts: bool, errors: list[str]
) -> dict[str, Any]:
    formal_root_raw = operator.get("formal_root")
    if not isinstance(formal_root_raw, str) or not os.path.isabs(formal_root_raw):
        errors.append(f"{where}.formal_root: expected absolute path")
        formal_root = None
    else:
        formal_root = Path(formal_root_raw)

    artifacts = _as_dict(operator.get("artifacts"), f"{where}.artifacts", errors)
    missing = sorted(_REQUIRED_ARTIFACTS - artifacts.keys())
    if missing:
        errors.append(f"{where}.artifacts: missing required entries {missing}")

    gates = _as_dict(operator.get("gates"), f"{where}.gates", errors)
    if gates.get("task3") == "PASSED" and "gate_task3" not in artifacts:
        errors.append(f"{where}.artifacts: task3 PASSED requires gate_task3")

    for name, raw in artifacts.items():
        item_where = f"{where}.artifacts.{name}"
        item = _as_dict(raw, item_where, errors)
        path_raw = item.get("path")
        digest = item.get("sha256")
        if not isinstance(path_raw, str) or not os.path.isabs(path_raw):
            errors.append(f"{item_where}.path: expected absolute path")
            continue
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            errors.append(f"{item_where}.sha256: expected lowercase SHA-256")
        path = Path(path_raw)
        if name in _FORMAL_ARTIFACTS and formal_root is not None:
            try:
                path.relative_to(formal_root)
            except ValueError:
                errors.append(f"{item_where}.path: formal artifact is outside formal_root")
        if verify_artifacts:
            if not path.is_file():
                errors.append(f"{item_where}.path: artifact does not exist")
            elif isinstance(digest, str) and _SHA256.fullmatch(digest):
                actual = _sha256_file(path)
                if actual != digest:
                    errors.append(
                        f"{item_where}.sha256: expected {digest}, measured {actual}"
                    )
    return artifacts


def _validate_artifact_map(
    artifacts: Any, where: str, verify_artifacts: bool, errors: list[str]
) -> dict[str, dict[str, Any]]:
    """Validate a generic named artifact map and optionally recompute hashes."""

    result = _as_dict(artifacts, where, errors)
    for name, raw in result.items():
        item_where = f"{where}.{name}"
        item = _as_dict(raw, item_where, errors)
        path_raw = item.get("path")
        digest = item.get("sha256")
        if not isinstance(path_raw, str) or not os.path.isabs(path_raw):
            errors.append(f"{item_where}.path: expected absolute path")
            continue
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            errors.append(f"{item_where}.sha256: expected lowercase SHA-256")
            continue
        if verify_artifacts:
            path = Path(path_raw)
            if not path.is_file():
                errors.append(f"{item_where}.path: artifact does not exist")
            else:
                actual = _sha256_file(path)
                if actual != digest:
                    errors.append(
                        f"{item_where}.sha256: expected {digest}, measured {actual}"
                    )
    return result


def _validate_stochastic_formal_projection(
    spec: dict[str, Any],
    caseset: dict[str, Any],
    evidence: dict[str, Any],
    receipt: dict[str, Any],
    artifacts: dict[str, Any],
    where: str,
    errors: list[str],
) -> None:
    """Bind the N8 phase claim to adapter-replayed formal stochastic evidence."""

    try:
        contract = stochastic_contract.from_spec(spec)
    except stochastic_contract.StochasticContractError as exc:
        errors.append(f"{where}.artifacts.spec.stochastic: {exc}")
        return
    formal_item = artifacts.get("stochastic_formal_evidence")
    stochastic_keys = (
        "stochastic_collection", "stochastic_formal_evidence", "stochastic_evaluation")
    if contract is None:
        if formal_item is not None or receipt.get("stochastic_collection") is not None \
                or any(evidence.get(key) is not None for key in stochastic_keys):
            errors.append(f"{where}: non-stochastic formal run claims N8 evidence")
        return
    if not isinstance(formal_item, dict):
        errors.append(f"{where}.artifacts.stochastic_formal_evidence: required")
        return
    if caseset.get("stochastic_contract") != contract:
        errors.append(f"{where}.artifacts.caseset.stochastic_contract: staged spec drift")
    receipt_path = artifacts.get("extension_receipt", {}).get("path")
    if not isinstance(receipt_path, str) or not os.path.isabs(receipt_path):
        errors.append(f"{where}.artifacts.extension_receipt.path: required for N8 replay")
        return
    work = str(Path(receipt_path).parent)
    try:
        validated = cpp_extension_adapter.validate_stochastic_collection(
            work, caseset, receipt)
    except (OSError, ValueError, cpp_extension_adapter.CppExtensionAdapterError) as exc:
        errors.append(f"{where}.artifacts.stochastic_formal_evidence: adapter replay failed: {exc}")
        return
    if not isinstance(validated, dict) or validated.get("status") != "complete":
        errors.append(f"{where}.artifacts.stochastic_formal_evidence: expected complete collection")
        return
    gate = validated.get("gate")
    evaluation = validated.get("evaluation")
    if not isinstance(gate, dict) or gate.get("ready_for_formal_precision") is not True \
            or gate.get("usable_for_verdict") is not False:
        errors.append(f"{where}.artifacts.stochastic_formal_evidence: precondition gate not ready")
    if not isinstance(evaluation, dict) or evaluation.get("status") not in {
        stochastic_contract.EVAL_SATISFIED, stochastic_contract.EVAL_FAILED}:
        errors.append(f"{where}.artifacts.stochastic_formal_evidence: invalid evaluation")
    formal = _json_artifact(
        artifacts, "stochastic_formal_evidence", where, errors)
    for name, actual, expected in (
        ("stochastic_collection", evidence.get("stochastic_collection"),
         receipt.get("stochastic_collection")),
        ("stochastic_formal_evidence", evidence.get("stochastic_formal_evidence"),
         validated.get("formal")),
        ("stochastic_evaluation", evidence.get("stochastic_evaluation"), evaluation),
        ("registered_formal", formal, validated.get("formal")),
    ):
        if actual != expected:
            errors.append(f"{where}.{name}: adapter-replayed stochastic projection drift")


def _validate_formal_artifact_projection(
    operator: dict[str, Any],
    artifacts: dict[str, Any],
    where: str,
    current_contract: bool,
    errors: list[str],
) -> None:
    """Re-project ledger facts from the registered formal workflow artifacts."""

    facts = _json_artifact(artifacts, "source_facts", where, errors)
    spec = _json_artifact(artifacts, "spec", where, errors)
    caseset = _json_artifact(artifacts, "caseset", where, errors)
    evidence = _json_artifact(artifacts, "precision_evidence", where, errors)
    verdict = _json_artifact(artifacts, "verdict", where, errors)
    perf = _json_artifact(artifacts, "perf_report", where, errors)
    acceptance_artifact = _json_artifact(artifacts, "acceptance", where, errors)
    receipt = _json_artifact(artifacts, "extension_receipt", where, errors)
    build_receipt = _json_artifact(artifacts, "vendor_build_receipt", where, errors)

    _validate_stochastic_formal_projection(
        spec, caseset, evidence, receipt, artifacts, where, errors)

    source = _as_dict(operator.get("source"), f"{where}.source", errors)
    taskbook = _as_dict(operator.get("taskbook"), f"{where}.taskbook", errors)
    required = _as_dict(operator.get("required"), f"{where}.required", errors)
    generated = _as_dict(operator.get("generated"), f"{where}.generated", errors)
    executable = _as_dict(operator.get("executable"), f"{where}.executable", errors)
    precision = _as_dict(operator.get("precision"), f"{where}.precision", errors)
    performance = _as_dict(operator.get("performance"), f"{where}.performance", errors)
    acceptance = _as_dict(operator.get("acceptance"), f"{where}.acceptance", errors)

    facts_payload = _as_dict(
        facts.get("payload"), f"{where}.artifacts.source_facts.payload", errors
    )
    if facts.get("domain") != _SOURCE_FACTS_DOMAIN or facts.get("schema_version") != 1:
        errors.append(f"{where}.artifacts.source_facts: unsupported envelope")
    try:
        measured_facts_digest = content_address.content_digest(
            _SOURCE_FACTS_DOMAIN, facts_payload
        )
    except content_address.ContentAddressError as exc:
        errors.append(f"{where}.artifacts.source_facts.digest: {exc}")
        measured_facts_digest = None
    if facts.get("digest") != measured_facts_digest:
        errors.append(f"{where}.artifacts.source_facts.digest: payload digest mismatch")
    _same(
        source.get("envelope_sha256"),
        facts.get("digest"),
        f"{where}.source.envelope_sha256",
        errors,
    )
    completeness = _as_dict(
        facts_payload.get("completeness"),
        f"{where}.artifacts.source_facts.payload.completeness",
        errors,
    )
    if completeness.get("status") != "complete" or completeness.get("reasons") != []:
        errors.append(f"{where}.artifacts.source_facts: completeness must be complete")
    pr = _as_dict(
        facts_payload.get("pr"), f"{where}.artifacts.source_facts.payload.pr", errors
    )
    derived = _as_dict(
        facts_payload.get("derived"),
        f"{where}.artifacts.source_facts.payload.derived",
        errors,
    )
    taskdoc_facts = _as_dict(
        facts_payload.get("taskdoc"),
        f"{where}.artifacts.source_facts.payload.taskdoc",
        errors,
    )
    _same(
        derived.get("aclnn_entry"),
        operator.get("api"),
        f"{where}.api",
        errors,
        message="source_facts API drift",
    )
    _same(
        taskdoc_facts.get("snapshot_sha256"),
        taskbook.get("snapshot_sha256"),
        f"{where}.taskbook.snapshot_sha256",
        errors,
    )
    taskdoc_artifact = _as_dict(
        artifacts.get("taskdoc_snapshot"), f"{where}.artifacts.taskdoc_snapshot", errors
    )
    _same(
        taskdoc_artifact.get("sha256"),
        taskbook.get("snapshot_sha256"),
        f"{where}.artifacts.taskdoc_snapshot.sha256",
        errors,
    )

    build_source = _as_dict(
        build_receipt.get("source"),
        f"{where}.artifacts.vendor_build_receipt.source",
        errors,
    )
    if build_receipt.get("schema") != "oprunway.vendor_build_receipt":
        errors.append(f"{where}.artifacts.vendor_build_receipt.schema: unsupported")
    if build_receipt.get("status") != "VERIFIED":
        errors.append(f"{where}.artifacts.vendor_build_receipt: expected VERIFIED")
    build = _as_dict(
        build_receipt.get("build"), f"{where}.artifacts.vendor_build_receipt.build", errors
    )
    if build.get("returncode") != 0 or build.get("returncode_source") != "measured":
        errors.append(f"{where}.artifacts.vendor_build_receipt.build: expected measured rc=0")

    caller_contract = (
        facts_payload.get("contract_version") == 2
        or "input_association" in facts_payload
        or "input_association" in source
        or "content_anchor" in source
    )
    if current_contract and not caller_contract:
        errors.append(
            f"{where}.source: current RGE ledger requires caller-trusted content anchor"
        )
    if not current_contract and caller_contract:
        errors.append(
            f"{where}.source: legacy RGE ledger cannot claim caller-trusted association"
        )
    if current_contract:
        association = _as_dict(
            facts_payload.get("input_association"),
            f"{where}.artifacts.source_facts.payload.input_association",
            errors,
        )
        if facts_payload.get("contract_version") != 2:
            errors.append(
                f"{where}.artifacts.source_facts.payload.contract_version: expected 2"
            )
        if association != _CALLER_INPUT_ASSOCIATION:
            errors.append(
                f"{where}.artifacts.source_facts.payload.input_association: "
                "expected caller_trusted_pair_v1"
            )
        _same(
            source.get("input_association"),
            association,
            f"{where}.source.input_association",
            errors,
            message="caller input association drift",
        )
        facts_anchor = _content_anchor(
            pr.get("content_anchor"),
            f"{where}.artifacts.source_facts.payload.pr.content_anchor",
            errors,
        )
        ledger_anchor = _content_anchor(
            source.get("content_anchor"), f"{where}.source.content_anchor", errors
        )
        _same(
            ledger_anchor,
            facts_anchor,
            f"{where}.source.content_anchor",
            errors,
            message="source-facts content anchor drift",
        )
        if not isinstance(completeness.get("transport_warnings", []), list):
            errors.append(
                f"{where}.artifacts.source_facts.payload.completeness."
                "transport_warnings: expected list"
            )
        if build_receipt.get("schema_version") != 3:
            errors.append(
                f"{where}.artifacts.vendor_build_receipt: caller-trusted input "
                "requires current v3 content snapshot"
            )
        build_anchor = _content_anchor(
            build_source.get("content_anchor"),
            f"{where}.artifacts.vendor_build_receipt.source.content_anchor",
            errors,
        )
        snapshot_digest = _as_dict(
            build.get("source_snapshot_digest"),
            f"{where}.artifacts.vendor_build_receipt.build.source_snapshot_digest",
            errors,
        )
        digest_anchor = _content_anchor(
            snapshot_digest.get("content_anchor"),
            f"{where}.artifacts.vendor_build_receipt.build."
            "source_snapshot_digest.content_anchor",
            errors,
        )
        for projected, label in (
            (build_anchor, "source.content_anchor"),
            (digest_anchor, "build.source_snapshot_digest.content_anchor"),
        ):
            _same(
                projected,
                facts_anchor,
                f"{where}.artifacts.vendor_build_receipt.{label}",
                errors,
                message="caller content anchor drift",
            )
        if build_source.get("provenance_kind") != "local_snapshot":
            errors.append(
                f"{where}.artifacts.vendor_build_receipt.source.provenance_kind: "
                "fresh caller input must be a materialized content snapshot"
            )
    else:
        # Legacy v1/v2 artifacts retain their original strict identity routing.  They
        # are still valid historical evidence, but are never relabelled caller-trusted.
        _same(
            facts_payload.get("declared_source_form"),
            source.get("declared_source_form"),
            f"{where}.source.declared_source_form",
            errors,
        )
        _same(
            pr.get("provenance_kind"),
            source.get("provenance_kind"),
            f"{where}.source.provenance_kind",
            errors,
        )
        if build_receipt.get("schema_version") not in (1, 2):
            errors.append(
                f"{where}.artifacts.vendor_build_receipt: legacy facts require v1/v2 receipt"
            )
        _same(
            build_source.get("declared_source_form"),
            source.get("declared_source_form"),
            f"{where}.artifacts.vendor_build_receipt.source.declared_source_form",
            errors,
        )
        _same(
            build_source.get("provenance_kind"),
            source.get("provenance_kind"),
            f"{where}.artifacts.vendor_build_receipt.source.provenance_kind",
            errors,
        )
        provenance = source.get("provenance_kind")
        if provenance == "local_snapshot":
            if any(
                pr.get(key) is not None
                for key in ("head_sha", "head_repo", "canonical_url")
            ):
                errors.append(
                    f"{where}.artifacts.source_facts.payload.pr: "
                    "legacy local source carries PR identity"
                )
            _same(
                pr.get("snapshot_merkle_sha256"),
                source.get("snapshot_subtree_sha256"),
                f"{where}.source.snapshot_subtree_sha256",
                errors,
            )
            _same(
                pr.get("snapshot_scope"),
                source.get("snapshot_scope"),
                f"{where}.source.snapshot_scope",
                errors,
            )
            _same(
                build_source.get("snapshot_sha256"),
                source.get("snapshot_full_sha256"),
                f"{where}.source.snapshot_full_sha256",
                errors,
            )
            _same(
                build_source.get("snapshot_subtree_sha256"),
                source.get("snapshot_subtree_sha256"),
                f"{where}.artifacts.vendor_build_receipt.source.snapshot_subtree_sha256",
                errors,
            )
            _same(
                build_source.get("snapshot_subtree_scope"),
                source.get("snapshot_scope"),
                f"{where}.artifacts.vendor_build_receipt.source.snapshot_subtree_scope",
                errors,
            )
            if build_source.get("pr_head_sha") is not None:
                errors.append(
                    f"{where}.artifacts.vendor_build_receipt.source: "
                    "legacy local source has PR head"
                )
        elif provenance == "gitcode_pr":
            for key in ("head_sha", "head_repo", "canonical_url"):
                if not isinstance(pr.get(key), str) or not pr.get(key):
                    errors.append(
                        f"{where}.artifacts.source_facts.payload.pr.{key}: required"
                    )
            if not isinstance(pr.get("head_sha"), str) or not _HEX40.fullmatch(
                pr["head_sha"]
            ):
                errors.append(
                    f"{where}.artifacts.source_facts.payload.pr.head_sha: expected 40 hex"
                )
            url_match = _GITCODE_MR_URL.fullmatch(str(pr.get("canonical_url", "")))
            if url_match is None or int(url_match.group("number")) != pr.get("number"):
                errors.append(
                    f"{where}.artifacts.source_facts.payload.pr.canonical_url: "
                    "expected exact GitCode MR URL matching pr.number"
                )
            for ledger_key, facts_key in (
                ("pr_head_sha", "head_sha"),
                ("pr_head_repo", "head_repo"),
                ("pr_url", "canonical_url"),
            ):
                _same(
                    source.get(ledger_key),
                    pr.get(facts_key),
                    f"{where}.source.{ledger_key}",
                    errors,
                )
            _same(
                build_source.get("pr_head_sha"),
                pr.get("head_sha"),
                f"{where}.artifacts.vendor_build_receipt.source.pr_head_sha",
                errors,
            )
            _same(
                build_source.get("repo"),
                pr.get("head_repo"),
                f"{where}.artifacts.vendor_build_receipt.source.repo",
                errors,
            )

    if spec.get("runner_form") != "cpp_extension":
        errors.append(f"{where}.artifacts.spec.runner_form: expected cpp_extension")
    if not caller_contract and spec.get("declared_source_form") is not None:
        _same(
            spec.get("declared_source_form"),
            source.get("declared_source_form"),
            f"{where}.artifacts.spec.declared_source_form",
            errors,
        )
    _same(
        sorted(spec.get("dtype_required", [])),
        sorted(required.get("dtypes", [])),
        f"{where}.required.dtypes",
        errors,
        message="spec projection drift",
    )
    _same(
        sorted(spec.get("hardware", [])),
        sorted(required.get("hardware", [])),
        f"{where}.required.hardware",
        errors,
        message="spec projection drift",
    )
    _same(
        _as_dict(spec.get("precision"), f"{where}.artifacts.spec.precision", errors).get(
            "case_target"
        ),
        required.get("case_target"),
        f"{where}.required.case_target",
        errors,
        message="spec projection drift",
    )

    op_name = spec.get("op")
    for artifact_name, artifact in (
        ("caseset", caseset),
        ("precision_evidence", evidence),
        ("verdict", verdict),
        ("perf_report", perf),
        ("acceptance", acceptance_artifact),
    ):
        _same(
            artifact.get("op"),
            op_name,
            f"{where}.artifacts.{artifact_name}.op",
            errors,
        )
    _same(
        caseset.get("task_pr_gaps"),
        spec.get("task_pr_gaps"),
        f"{where}.artifacts.caseset.task_pr_gaps",
        errors,
        message="spec/caseset gap projection drift",
    )
    cases = _as_list(caseset.get("cases"), f"{where}.artifacts.caseset.cases", errors)
    case_ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(case_ids) != len(cases) or any(not isinstance(case_id, str) or not case_id for case_id in case_ids):
        errors.append(f"{where}.artifacts.caseset.cases: every case needs an id")
    if len(case_ids) != len(set(case_ids)):
        errors.append(f"{where}.artifacts.caseset.cases: duplicate case id")
    for key in ("emitted", "requested_target"):
        _same(
            caseset.get(key),
            generated.get("case_count"),
            f"{where}.artifacts.caseset.{key}",
            errors,
        )
    _same(
        len(cases),
        generated.get("case_count"),
        f"{where}.generated.case_count",
        errors,
        message="caseset case count drift",
    )
    _same(
        sorted(caseset.get("dtype_required", [])),
        sorted(required.get("dtypes", [])),
        f"{where}.required.dtypes",
        errors,
        message="caseset required dtype drift",
    )
    _same(
        sorted(caseset.get("dtype_tested", [])),
        sorted(generated.get("dtypes", [])),
        f"{where}.generated.dtypes",
        errors,
        message="caseset tested dtype drift",
    )
    atomic = caseset.get("atomic_attr_ledger")
    if atomic is None:
        projected_denominator = len(cases)
        projected_excluded = 0
    else:
        atomic = _as_dict(atomic, f"{where}.artifacts.caseset.atomic_attr_ledger", errors)
        projected_denominator = atomic.get("structure_denominator_total")
        projected_excluded = atomic.get("excluded")
        _same(atomic.get("planned"), len(cases), f"{where}.artifacts.caseset.atomic_attr_ledger.planned", errors)
        _same(atomic.get("emitted"), len(cases), f"{where}.artifacts.caseset.atomic_attr_ledger.emitted", errors)
    _same(
        required.get("structure_denominator_total"),
        projected_denominator,
        f"{where}.required.structure_denominator_total",
        errors,
        message="caseset structure denominator drift",
    )
    _same(
        required.get("structured_excluded"),
        projected_excluded,
        f"{where}.required.structured_excluded",
        errors,
        message="caseset structured exclusion drift",
    )
    perf_selection = _as_dict(
        _as_dict(
            caseset.get("perf_case_policy"),
            f"{where}.artifacts.caseset.perf_case_policy",
            errors,
        ).get("selection"),
        f"{where}.artifacts.caseset.perf_case_policy.selection",
        errors,
    )
    _same(
        perf_selection.get("precision_total"),
        len(cases),
        f"{where}.artifacts.caseset.perf_case_policy.selection.precision_total",
        errors,
    )
    _same(
        perf_selection.get("selected_total"),
        performance.get("perf_cases"),
        f"{where}.performance.perf_cases",
        errors,
        message="caseset performance selection drift",
    )

    if receipt.get("schema") != "oprunway.cpp_extension_receipt" or receipt.get(
        "schema_version"
    ) != 2 or receipt.get("status") != "VERIFIED":
        errors.append(f"{where}.artifacts.extension_receipt: expected v2 VERIFIED")
    bindings = _as_dict(
        receipt.get("bindings"), f"{where}.artifacts.extension_receipt.bindings", errors
    )
    _same(
        bindings.get("spec_sha256"),
        generated.get("spec_contract_sha256"),
        f"{where}.generated.spec_contract_sha256",
        errors,
        message="extension receipt binding drift",
    )
    _same(
        bindings.get("caseset_sha256"),
        generated.get("caseset_contract_sha256"),
        f"{where}.generated.caseset_contract_sha256",
        errors,
        message="extension receipt binding drift",
    )
    invocation = _as_dict(
        receipt.get("invocation"), f"{where}.artifacts.extension_receipt.invocation", errors
    )
    if invocation.get("schema") != "oprunway.cpp_extension_invocation_accounting" or invocation.get(
        "schema_version"
    ) != 1:
        errors.append(f"{where}.artifacts.extension_receipt.invocation: unsupported schema")
    for receipt_key, ledger_key in (
        ("planned", "planned"),
        ("produced", "produced"),
        ("failed", "failed"),
        ("excluded", "invocation_excluded"),
    ):
        _same(
            invocation.get(receipt_key),
            executable.get(ledger_key),
            f"{where}.executable.{ledger_key}",
            errors,
            message="invocation receipt drift",
        )
    _same(invocation.get("total"), len(cases), f"{where}.artifacts.extension_receipt.invocation.total", errors)
    case_records = _as_list(
        invocation.get("case_records"),
        f"{where}.artifacts.extension_receipt.invocation.case_records",
        errors,
    )
    record_ids = [row.get("case_id") for row in case_records if isinstance(row, dict)]
    if sorted(record_ids) != sorted(case_ids) or len(record_ids) != len(set(record_ids)):
        errors.append(f"{where}.artifacts.extension_receipt.invocation.case_records: case identity mismatch")
    outcome_counts = {
        outcome: sum(
            1 for row in case_records if isinstance(row, dict) and row.get("outcome") == outcome
        )
        for outcome in ("produced", "failed", "excluded")
    }
    for outcome, ledger_key in (
        ("produced", "produced"),
        ("failed", "failed"),
        ("excluded", "invocation_excluded"),
    ):
        _same(
            outcome_counts[outcome],
            executable.get(ledger_key),
            f"{where}.artifacts.extension_receipt.invocation.case_records",
            errors,
            message=f"{outcome} outcome count drift",
        )
    failed_ids = sorted(
        row.get("case_id")
        for row in case_records
        if isinstance(row, dict) and row.get("outcome") == "failed"
    )
    _same(
        sorted(invocation.get("failed_case_ids", [])),
        failed_ids,
        f"{where}.artifacts.extension_receipt.invocation.failed_case_ids",
        errors,
    )

    runtime = _as_dict(
        receipt.get("runtime"), f"{where}.artifacts.extension_receipt.runtime", errors
    )
    _same(
        runtime.get("cann_version"),
        executable.get("cann_version"),
        f"{where}.executable.cann_version",
        errors,
        message="runtime receipt drift",
    )
    cann = _as_dict(runtime.get("cann"), f"{where}.artifacts.extension_receipt.runtime.cann", errors)
    probe = _as_dict(cann.get("probe"), f"{where}.artifacts.extension_receipt.runtime.cann.probe", errors)
    if cann.get("status") != "measured" or probe.get("api") != "aclsysGetCANNVersion" or probe.get(
        "returncode"
    ) != 0:
        errors.append(f"{where}.artifacts.extension_receipt.runtime.cann: expected measured API probe")

    build_artifact = _as_dict(
        build_receipt.get("artifact"),
        f"{where}.artifacts.vendor_build_receipt.artifact",
        errors,
    )
    vendor = _as_dict(
        receipt.get("vendor"), f"{where}.artifacts.extension_receipt.vendor", errors
    )
    vendor_elf = _as_dict(
        artifacts.get("vendor_elf"), f"{where}.artifacts.vendor_elf", errors
    )
    _same(
        build_artifact.get("library_path"),
        vendor_elf.get("path"),
        f"{where}.artifacts.vendor_build_receipt.artifact.library_path",
        errors,
        message="vendor ELF path drift",
    )
    for projected, projected_where in (
        (build_artifact.get("library_sha256"), "vendor_build_receipt.artifact"),
        (vendor.get("library_sha256"), "extension_receipt.vendor"),
        (vendor_elf.get("sha256"), "vendor_elf"),
    ):
        _same(
            projected,
            vendor_elf.get("sha256"),
            f"{where}.artifacts.{projected_where}.library_sha256",
            errors,
            message="vendor ELF identity drift",
        )
    try:
        vendor_build_receipt.validate_for_acceptance(
            build_receipt,
            library_path=vendor_elf.get("path"),
            library_sha256=vendor_elf.get("sha256"),
            normalize_path=True,
        )
    except (TypeError, vendor_build_receipt.VendorBuildReceiptError) as exc:
        errors.append(
            f"{where}.artifacts.vendor_build_receipt: formal acceptance validation "
            f"failed: {exc}"
        )
    embedded_build = _as_dict(
        vendor.get("build_receipt"),
        f"{where}.artifacts.extension_receipt.vendor.build_receipt",
        errors,
    )
    _same(
        embedded_build,
        build_receipt,
        f"{where}.artifacts.extension_receipt.vendor.build_receipt",
        errors,
        message="external build receipt drift",
    )
    _same(
        vendor.get("build_receipt_sha256"),
        _canonical_sha256(build_receipt),
        f"{where}.artifacts.extension_receipt.vendor.build_receipt_sha256",
        errors,
    )

    if evidence.get("runner_form") != "cpp_extension" or evidence.get(
        "runner_source"
    ) != "generated_official_cpp_extension":
        errors.append(f"{where}.artifacts.precision_evidence: runner identity mismatch")
    _same(
        evidence.get("cpp_extension_receipt"),
        receipt,
        f"{where}.artifacts.precision_evidence.cpp_extension_receipt",
        errors,
        message="receipt mirror drift",
    )
    receipt_sha = _canonical_sha256(receipt)
    evidence_rows = _as_list(
        evidence.get("evidence"), f"{where}.artifacts.precision_evidence.evidence", errors
    )
    evidence_ids = [row.get("case_id") for row in evidence_rows if isinstance(row, dict)]
    if sorted(evidence_ids) != sorted(case_ids) or len(evidence_ids) != len(set(evidence_ids)):
        errors.append(f"{where}.artifacts.precision_evidence.evidence: case identity mismatch")
    if any(
        isinstance(row, dict) and row.get("cpp_extension_receipt_sha256") != receipt_sha
        for row in evidence_rows
    ):
        errors.append(f"{where}.artifacts.precision_evidence.evidence: receipt digest drift")
    errored_ids = sorted(
        row.get("case_id")
        for row in evidence_rows
        if isinstance(row, dict) and row.get("status") == "execution_failed"
    )
    _same(
        errored_ids,
        failed_ids,
        f"{where}.artifacts.precision_evidence.evidence",
        errors,
        message="execution failure identity drift",
    )

    accuracy = _as_dict(
        verdict.get("accuracy_summary"), f"{where}.artifacts.verdict.accuracy_summary", errors
    )
    for verdict_key, ledger_key in (
        ("total", "total"),
        ("executed", "executed"),
        ("passed", "passed"),
        ("failed", "numerical_failed"),
        ("errored", "execution_errored"),
        ("uncertain", "uncertain"),
        ("na", "not_applicable"),
    ):
        _same(
            accuracy.get(verdict_key),
            precision.get(ledger_key),
            f"{where}.precision.{ledger_key}",
            errors,
            message="verdict projection drift",
        )
    verdict_overall = _as_dict(
        verdict.get("overall"), f"{where}.artifacts.verdict.overall", errors
    )
    _same(
        verdict_overall.get("verdict"),
        str(precision.get("verdict", "")).lower(),
        f"{where}.precision.verdict",
        errors,
        message="verdict projection drift",
    )

    perf_summary = _as_dict(
        perf.get("summary"), f"{where}.artifacts.perf_report.summary", errors
    )
    for perf_key in ("perf_cases", "measured", "blocked"):
        _same(
            perf_summary.get(perf_key),
            performance.get(perf_key),
            f"{where}.performance.{perf_key}",
            errors,
            message="performance report projection drift",
        )
    _same(
        str(perf_summary.get("status", "")).upper(),
        performance.get("status"),
        f"{where}.performance.status",
        errors,
        message="performance report projection drift",
    )
    _same(
        perf.get("perf_mode"),
        performance.get("mode"),
        f"{where}.performance.mode",
        errors,
        message="performance report projection drift",
    )
    per_case = _as_list(perf.get("per_case"), f"{where}.artifacts.perf_report.per_case", errors)
    _same(len(per_case), performance.get("perf_cases"), f"{where}.artifacts.perf_report.per_case", errors)
    per_case_ids = [row.get("case_id") for row in per_case if isinstance(row, dict)]
    selected_ids = perf_selection.get("selected_case_ids", [])
    if sorted(per_case_ids) != sorted(selected_ids) or len(per_case_ids) != len(set(per_case_ids)):
        errors.append(f"{where}.artifacts.perf_report.per_case: caseset selection identity mismatch")
    measured_rows = sum(
        1
        for row in per_case
        if isinstance(row, dict)
        and row.get("blocked") is False
        and isinstance(row.get("npu_us"), (int, float))
        and not isinstance(row.get("npu_us"), bool)
        and row.get("npu_us") > 0
    )
    blocked_rows = sum(
        1 for row in per_case if isinstance(row, dict) and row.get("blocked") is True
    )
    _same(measured_rows, performance.get("measured"), f"{where}.artifacts.perf_report.per_case", errors, message="measured row count drift")
    _same(blocked_rows, performance.get("blocked"), f"{where}.artifacts.perf_report.per_case", errors, message="blocked row count drift")

    artifact_acceptance = (
        acceptance_artifact.get("overall"),
        acceptance_artifact.get("state"),
        acceptance_artifact.get("exit_code"),
        acceptance_artifact.get("requires_human_cp"),
        _as_dict(
            acceptance_artifact.get("gate"),
            f"{where}.artifacts.acceptance.gate",
            errors,
        ).get("passed"),
    )
    ledger_acceptance = (
        acceptance.get("result"),
        acceptance.get("state"),
        acceptance.get("exit_code"),
        acceptance.get("human_cp"),
        acceptance.get("gate_passed"),
    )
    _same(
        ledger_acceptance,
        artifact_acceptance,
        f"{where}.acceptance",
        errors,
        message="acceptance artifact projection drift",
    )
    _same(
        acceptance_artifact.get("repo_mode"),
        "cpp_extension",
        f"{where}.artifacts.acceptance.repo_mode",
        errors,
    )
    _same(
        acceptance_artifact.get("precision_verdict"),
        str(verdict_overall.get("verdict", "")).lower(),
        f"{where}.artifacts.acceptance.precision_verdict",
        errors,
    )
    _same(
        str(acceptance_artifact.get("perf_status", "")).upper(),
        performance.get("status"),
        f"{where}.artifacts.acceptance.perf_status",
        errors,
    )

    gates = _as_dict(operator.get("gates"), f"{where}.gates", errors)
    for stage in ("task1", "task2", "task3"):
        artifact_name = f"gate_{stage}"
        if artifact_name not in artifacts:
            continue
        log = _text_artifact(artifacts, artifact_name, where, errors)
        expected_label = gates.get(stage)
        terminal = "PASSED" if expected_label == "PASSED" else "FAILED"
        if f"stage={stage}" not in log or f"STATUS: {terminal}" not in log:
            errors.append(f"{where}.artifacts.{artifact_name}: gate log/status identity mismatch")


def _validate_operator(
    operator: dict[str, Any], index: int, ledger_version: int,
    verify_artifacts: bool, errors: list[str]
) -> None:
    operator_id = operator.get("operator_id")
    where = f"operators[{index}]({operator_id or '?'})"
    if not isinstance(operator_id, str) or not operator_id:
        errors.append(f"{where}.operator_id: expected non-empty string")
    if not isinstance(operator.get("api"), str) or not operator.get("api"):
        errors.append(f"{where}.api: expected non-empty string")

    taskbook = _as_dict(operator.get("taskbook"), f"{where}.taskbook", errors)
    if taskbook.get("input_form") not in {"local_file", "online_url"}:
        errors.append(f"{where}.taskbook.input_form: unsupported input form")

    source = _as_dict(operator.get("source"), f"{where}.source", errors)
    for key in ("envelope_sha256",):
        value = source.get(key)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            errors.append(f"{where}.source.{key}: expected lowercase SHA-256")
    if ledger_version == SCHEMA_VERSION:
        if source.get("input_association") != _CALLER_INPUT_ASSOCIATION:
            errors.append(
                f"{where}.source.input_association: expected caller_trusted_pair_v1"
            )
        _content_anchor(source.get("content_anchor"), f"{where}.source.content_anchor", errors)
    else:
        # Do not silently upgrade old ledgers: absent caller association means the
        # original declared-form/provenance identity contract remains mandatory.
        declared = source.get("declared_source_form")
        provenance = source.get("provenance_kind")
        allowed_pairs = {
            ("local_source", "local_snapshot"),
            ("git_pr", "gitcode_pr"),
        }
        if (declared, provenance) not in allowed_pairs:
            errors.append(
                f"{where}.source: incompatible legacy declared/provenance pair "
                f"{declared!r}/{provenance!r}"
            )
        if provenance == "local_snapshot":
            for key in ("snapshot_subtree_sha256", "snapshot_full_sha256"):
                value = source.get(key)
                if not isinstance(value, str) or not _SHA256.fullmatch(value):
                    errors.append(f"{where}.source.{key}: expected lowercase SHA-256")
            if not isinstance(source.get("snapshot_scope"), str) or not source.get(
                "snapshot_scope"
            ):
                errors.append(f"{where}.source.snapshot_scope: expected non-empty string")
        elif provenance == "gitcode_pr":
            if not isinstance(source.get("pr_head_sha"), str) or not _HEX40.fullmatch(
                source["pr_head_sha"]
            ):
                errors.append(f"{where}.source.pr_head_sha: expected exact 40-hex head")
            for key in ("pr_head_repo", "pr_url"):
                if not isinstance(source.get(key), str) or not source.get(key):
                    errors.append(f"{where}.source.{key}: expected non-empty string")

    required = _as_dict(operator.get("required"), f"{where}.required", errors)
    required_dtypes = _strings(required.get("dtypes"), f"{where}.required.dtypes", errors)
    required_hardware = _strings(
        required.get("hardware"), f"{where}.required.hardware", errors
    )
    case_target = _integer(required, "case_target", f"{where}.required", errors)
    denominator = _integer(
        required, "structure_denominator_total", f"{where}.required", errors
    )
    structured_excluded = _integer(
        required, "structured_excluded", f"{where}.required", errors
    )
    if denominator != case_target + structured_excluded:
        errors.append(
            f"{where}.required: structure_denominator_total must equal "
            "case_target + structured_excluded"
        )

    generated = _as_dict(operator.get("generated"), f"{where}.generated", errors)
    generated_count = _integer(generated, "case_count", f"{where}.generated", errors)
    generated_dtypes = _strings(
        generated.get("dtypes"), f"{where}.generated.dtypes", errors
    )
    missing_required = _strings(
        generated.get("missing_required_dtypes"),
        f"{where}.generated.missing_required_dtypes",
        errors,
    )
    if generated_count != case_target:
        errors.append(f"{where}: generated.case_count must equal required.case_target")
    unexpected_dtypes = sorted(set(generated_dtypes) - set(required_dtypes))
    if unexpected_dtypes:
        errors.append(f"{where}: generated dtypes not required: {unexpected_dtypes}")
    expected_missing = sorted(set(required_dtypes) - set(generated_dtypes))
    if sorted(missing_required) != expected_missing:
        errors.append(
            f"{where}: missing_required_dtypes must equal required minus generated"
        )

    executable = _as_dict(operator.get("executable"), f"{where}.executable", errors)
    planned = _integer(executable, "planned", f"{where}.executable", errors)
    produced = _integer(executable, "produced", f"{where}.executable", errors)
    failed = _integer(executable, "failed", f"{where}.executable", errors)
    invocation_excluded = _integer(
        executable, "invocation_excluded", f"{where}.executable", errors
    )
    tested_hardware = _strings(
        executable.get("tested_hardware"), f"{where}.executable.tested_hardware", errors
    )
    if planned != generated_count:
        errors.append(f"{where}: executable.planned must equal generated.case_count")
    if planned != produced + failed + invocation_excluded:
        errors.append(
            f"{where}: executable.planned must equal produced + failed + invocation_excluded"
        )
    if not set(tested_hardware).issubset(required_hardware):
        errors.append(f"{where}: tested_hardware must be a subset of required.hardware")

    precision = _as_dict(operator.get("precision"), f"{where}.precision", errors)
    precision_total = _integer(precision, "total", f"{where}.precision", errors)
    precision_executed = _integer(precision, "executed", f"{where}.precision", errors)
    passed = _integer(precision, "passed", f"{where}.precision", errors)
    numerical_failed = _integer(
        precision, "numerical_failed", f"{where}.precision", errors
    )
    execution_errored = _integer(
        precision, "execution_errored", f"{where}.precision", errors
    )
    uncertain = _integer(precision, "uncertain", f"{where}.precision", errors)
    not_applicable = _integer(
        precision, "not_applicable", f"{where}.precision", errors
    )
    precision_verdict = precision.get("verdict")
    if precision_total != generated_count:
        errors.append(f"{where}: precision.total must equal generated.case_count")
    if precision_executed != produced:
        errors.append(f"{where}: precision.executed must equal executable.produced")
    if execution_errored != failed:
        errors.append(
            f"{where}: precision.execution_errored must equal executable.failed"
        )
    if precision_total != (
        passed + numerical_failed + execution_errored + uncertain + not_applicable
    ):
        errors.append(f"{where}: precision partitions do not sum to precision.total")
    expected_precision = (
        "PASS"
        if numerical_failed == execution_errored == uncertain == 0
        else "FAIL"
    )
    if precision_verdict != expected_precision:
        errors.append(
            f"{where}.precision.verdict: expected {expected_precision}, got {precision_verdict!r}"
        )

    performance = _as_dict(
        operator.get("performance"), f"{where}.performance", errors
    )
    perf_cases = _integer(performance, "perf_cases", f"{where}.performance", errors)
    measured = _integer(performance, "measured", f"{where}.performance", errors)
    blocked = _integer(performance, "blocked", f"{where}.performance", errors)
    if perf_cases != measured + blocked:
        errors.append(f"{where}: perf_cases must equal measured + blocked")
    expected_perf_status = "MEASURED" if blocked == 0 else "BLOCKED"
    if performance.get("status") != expected_perf_status:
        errors.append(
            f"{where}.performance.status: expected {expected_perf_status}, "
            f"got {performance.get('status')!r}"
        )

    raw_gaps = _as_list(operator.get("structured_gaps"), f"{where}.structured_gaps", errors)
    gaps: list[dict[str, Any]] = []
    gap_ids: list[str] = []
    for gap_index, raw_gap in enumerate(raw_gaps):
        gap = _as_dict(raw_gap, f"{where}.structured_gaps[{gap_index}]", errors)
        gaps.append(gap)
        gap_id = gap.get("gap_id")
        if not isinstance(gap_id, str) or not gap_id:
            errors.append(f"{where}.structured_gaps[{gap_index}].gap_id: expected string")
        else:
            gap_ids.append(gap_id)
        if gap.get("status") != "UNVALIDATED":
            errors.append(
                f"{where}.structured_gaps[{gap_index}].status: expected UNVALIDATED"
            )
        if gap.get("source_kind") not in {
            "formal_task_pr_gap",
            "derived_coverage_limit",
        }:
            errors.append(
                f"{where}.structured_gaps[{gap_index}].source_kind: unsupported"
            )
        _as_dict(gap.get("details"), f"{where}.structured_gaps[{gap_index}].details", errors)
    if len(gap_ids) != len(set(gap_ids)):
        errors.append(f"{where}.structured_gaps: duplicate gap_id")

    dtype_gap = _single_gap(gaps, "dtype_unsupported", where, errors)
    if missing_required:
        if dtype_gap is None:
            errors.append(f"{where}: missing required dtypes require dtype_unsupported gap")
        elif sorted(dtype_gap.get("details", {}).get("dtypes", [])) != sorted(
            missing_required
        ):
            errors.append(f"{where}: dtype_unsupported gap must mirror missing dtypes")
    elif dtype_gap is not None:
        errors.append(f"{where}: dtype_unsupported gap exists without missing dtypes")

    perf_gap = _single_gap(gaps, "performance_requirement_unvalidated", where, errors)
    requires_perf_comparison = required.get("performance_requires_comparison")
    if not isinstance(requires_perf_comparison, bool):
        errors.append(
            f"{where}.required.performance_requires_comparison: expected boolean"
        )
    if requires_perf_comparison and performance.get("mode") == "measure_only":
        if perf_gap is None:
            errors.append(
                f"{where}: measure_only comparison requirement requires structured gap"
            )

    missing_hardware = sorted(set(required_hardware) - set(tested_hardware))
    hardware_gap = _single_gap(gaps, "hardware_not_tested", where, errors)
    if missing_hardware:
        if hardware_gap is None:
            errors.append(f"{where}: untested required hardware requires structured gap")
        elif sorted(hardware_gap.get("details", {}).get("hardware", [])) != missing_hardware:
            errors.append(f"{where}: hardware_not_tested gap must mirror missing hardware")
    elif hardware_gap is not None:
        errors.append(f"{where}: hardware_not_tested gap exists without missing hardware")

    gates = _as_dict(operator.get("gates"), f"{where}.gates", errors)
    if gates.get("task1") not in {"PASSED", "FAILED"} or gates.get("task2") not in {
        "PASSED",
        "FAILED",
    }:
        errors.append(f"{where}.gates: task1/task2 must be PASSED or FAILED")
    if precision_verdict == "PASS" and expected_perf_status == "MEASURED":
        expected_task3 = "PASSED"
    elif precision_verdict == "FAIL":
        expected_task3 = "NOT_COMPLETED_PRECISION_FAIL"
    else:
        expected_task3 = "NOT_COMPLETED_PERF_BLOCKED"
    if gates.get("task3") != expected_task3:
        errors.append(
            f"{where}.gates.task3: expected {expected_task3}, got {gates.get('task3')!r}"
        )

    acceptance = _as_dict(operator.get("acceptance"), f"{where}.acceptance", errors)
    gate_passed = acceptance.get("gate_passed")
    if not isinstance(gate_passed, bool):
        errors.append(f"{where}.acceptance.gate_passed: expected boolean")
    # `run_workflow` 的验收入口无条件执行 Task1 + Task2；精度失败时 Task3
    # 按正式语义不执行，不能反过来把前两级门的失败洗成「证据门已过」。因此这里从
    # 机器门枚举派生 acceptance gate，而不是继续相信 acceptance/ledger 的自报布尔值。
    # Task3 若实际参与并通过，前面的 expected_task3 对账已经要求其为 PASSED；
    # `NOT_COMPLETED_*` 只表达正式状态机没有执行它，不改变 Task1/Task2 的合取。
    evidence_gate_passed = (
        gates.get("task1") == "PASSED" and gates.get("task2") == "PASSED"
    )
    if isinstance(gate_passed, bool) and gate_passed != evidence_gate_passed:
        errors.append(
            f"{where}.acceptance.gate_passed: expected {evidence_gate_passed} "
            "from task1/task2 gates"
        )
    human_cp = acceptance.get("human_cp")
    if not isinstance(human_cp, bool):
        errors.append(f"{where}.acceptance.human_cp: expected boolean")
    if gate_passed is False:
        expected_acceptance = (
            "BLOCKED(验收门未过)",
            "BLOCKED_EVIDENCE_INCOMPLETE",
            1,
            False,
        )
    elif precision_verdict == "FAIL":
        expected_acceptance = ("FAIL(精度)", "FAILED_PRECISION", 1, False)
    elif expected_perf_status == "BLOCKED":
        expected_acceptance = (
            "BLOCKED(measure_only 性能实测未完成)",
            "BLOCKED_PERF_MEASUREMENT_INCOMPLETE",
            1,
            False,
        )
    elif gaps:
        expected_acceptance = ("PASSED_WITH_GAPS", "PASSED_WITH_GAPS", 2, True)
    elif performance.get("mode") == "measure_only":
        expected_acceptance = (
            "PASS(性能仅实测未裁决)",
            "PASSED_PRECISION_PERF_MEASURED_ONLY",
            0,
            False,
        )
    else:
        expected_acceptance = ("PASS", "PASSED", 0, False)
    actual_acceptance = (
        acceptance.get("result"),
        acceptance.get("state"),
        acceptance.get("exit_code"),
        human_cp,
    )
    if actual_acceptance != expected_acceptance:
        errors.append(
            f"{where}.acceptance: expected {expected_acceptance}, got {actual_acceptance}"
        )

    artifacts = _validate_artifacts(operator, where, verify_artifacts, errors)
    _validate_formal_artifact_projection(
        operator, artifacts, where, ledger_version == SCHEMA_VERSION, errors
    )


def validation_errors(
    ledger: Any, *, verify_artifacts: bool = False, allow_historical: bool = False
) -> list[str]:
    """Return all validation errors without short-circuiting."""

    errors: list[str] = []
    root = _as_dict(ledger, "ledger", errors)
    if root.get("schema") != SCHEMA:
        errors.append(f"ledger.schema: expected {SCHEMA!r}")
    ledger_version = root.get("schema_version")
    if ledger_version == 1 and not allow_historical:
        errors.append(
            "ledger.schema_version: historical v1 requires explicit read-only mode"
        )
        ledger_version = SCHEMA_VERSION
    elif ledger_version not in (1, SCHEMA_VERSION):
        errors.append(f"ledger.schema_version: expected current {SCHEMA_VERSION}")
        ledger_version = SCHEMA_VERSION

    authority = _as_dict(root.get("authority"), "ledger.authority", errors)
    actual_taskbook_forms = _strings(
        authority.get("actual_taskbook_input_forms"),
        "ledger.authority.actual_taskbook_input_forms",
        errors,
    )
    actual_provenance_raw = authority.get("actual_dut_provenance_kinds")
    actual_provenance = (
        _strings(
            actual_provenance_raw,
            "ledger.authority.actual_dut_provenance_kinds",
            errors,
        )
        if actual_provenance_raw is not None
        else []
    )
    supplemental_artifacts = _validate_artifact_map(
        root.get("supplemental_artifacts"),
        "ledger.supplemental_artifacts",
        verify_artifacts,
        errors,
    )

    operators_raw = _as_list(root.get("operators"), "ledger.operators", errors)
    operators: list[dict[str, Any]] = []
    operator_ids: list[str] = []
    for index, raw in enumerate(operators_raw):
        operator = _as_dict(raw, f"ledger.operators[{index}]", errors)
        operators.append(operator)
        operator_id = operator.get("operator_id")
        if isinstance(operator_id, str):
            operator_ids.append(operator_id)
        _validate_operator(operator, index, ledger_version, verify_artifacts, errors)
    if not operators:
        errors.append("ledger.operators: expected at least one operator")
    if len(operator_ids) != len(set(operator_ids)):
        errors.append("ledger.operators: duplicate operator_id")

    operator_by_id = {
        op.get("operator_id"): op
        for op in operators
        if isinstance(op.get("operator_id"), str)
    }
    n0_supplemental = _validate_n0_static_projection(
        root.get("n0_static_gate_evidence"),
        operator_by_id,
        supplemental_artifacts,
        errors,
    )
    runtime_cann = _as_dict(
        authority.get("runtime_cann"), "ledger.authority.runtime_cann", errors
    )
    if (
        runtime_cann.get("status") != "MEASURED"
        or runtime_cann.get("probe_api") != "aclsysGetCANNVersion"
        or not isinstance(runtime_cann.get("version"), str)
        or not _SHA256.fullmatch(str(runtime_cann.get("library_sha256", "")))
    ):
        errors.append("ledger.authority.runtime_cann: expected measured API/ELF identity")
    for op_id, op in operator_by_id.items():
        _same(
            op.get("executable", {}).get("cann_version"),
            runtime_cann.get("version"),
            f"operators[{op_id}].executable.cann_version",
            errors,
            message="authority runtime CANN drift",
        )
        receipt = _json_artifact(
            op.get("artifacts", {}), "extension_receipt", f"operators[{op_id}]", errors
        )
        defining_elf = (
            receipt.get("runtime", {})
            .get("cann", {})
            .get("probe", {})
            .get("defining_elf", {})
        )
        _same(
            defining_elf.get("sha256"),
            runtime_cann.get("library_sha256"),
            f"operators[{op_id}].artifacts.extension_receipt.runtime.cann.probe.defining_elf",
            errors,
            message="authority runtime ELF drift",
        )
    operator_gap_ids = {
        gap.get("gap_id")
        for op in operators
        for gap in op.get("structured_gaps", [])
        if isinstance(gap, dict) and isinstance(gap.get("gap_id"), str)
    }
    workflow_gaps_raw = _as_list(
        root.get("workflow_plan_gaps"), "ledger.workflow_plan_gaps", errors
    )
    workflow_gap_ids: list[str] = []
    for index, raw_gap in enumerate(workflow_gaps_raw):
        gap = _as_dict(raw_gap, f"ledger.workflow_plan_gaps[{index}]", errors)
        gap_id = gap.get("gap_id")
        if not isinstance(gap_id, str) or not gap_id:
            errors.append(
                f"ledger.workflow_plan_gaps[{index}].gap_id: expected non-empty string"
            )
        else:
            workflow_gap_ids.append(gap_id)
        if gap.get("status") != "NOT_EXERCISED":
            errors.append(
                f"ledger.workflow_plan_gaps[{index}].status: expected NOT_EXERCISED"
            )
        if not isinstance(gap.get("reason"), str) or not gap.get("reason"):
            errors.append(
                f"ledger.workflow_plan_gaps[{index}].reason: expected non-empty string"
            )
    if len(workflow_gap_ids) != len(set(workflow_gap_ids)):
        errors.append("ledger.workflow_plan_gaps: duplicate gap_id")
    all_gap_ids = operator_gap_ids | set(workflow_gap_ids)

    plan_raw = _as_list(
        root.get("workflow_plan_evidence"), "ledger.workflow_plan_evidence", errors
    )
    seen_phases: list[str] = []
    phase_ref_index: dict[str, dict[str, set[Any]]] = {}
    for index, raw_phase in enumerate(plan_raw):
        phase = _as_dict(raw_phase, f"ledger.workflow_plan_evidence[{index}]", errors)
        phase_id = phase.get("phase")
        phase_where = f"ledger.workflow_plan_evidence[{index}]({phase_id or '?'})"
        if not isinstance(phase_id, str):
            errors.append(f"{phase_where}.phase: expected string")
        else:
            seen_phases.append(phase_id)
        status = phase.get("status")
        if status not in _PLAN_STATUSES:
            errors.append(f"{phase_where}.status: unsupported status {status!r}")
        refs = _as_list(phase.get("evidence_refs"), f"{phase_where}.evidence_refs", errors)
        if not refs:
            errors.append(f"{phase_where}.evidence_refs: expected at least one reference")
        artifact_refs: set[tuple[str, str]] = set()
        supplemental_refs: set[str] = set()
        test_refs: set[str] = set()
        test_bindings: set[tuple[Any, Any]] = set()
        for ref_index, raw_ref in enumerate(refs):
            ref_where = f"{phase_where}.evidence_refs[{ref_index}]"
            ref = _as_dict(raw_ref, ref_where, errors)
            kind = ref.get("kind")
            if kind == "artifact":
                op_id = ref.get("operator_id")
                artifact = ref.get("artifact")
                if op_id not in operator_by_id:
                    errors.append(f"{ref_where}.operator_id: unknown operator")
                elif artifact not in operator_by_id[op_id].get("artifacts", {}):
                    errors.append(f"{ref_where}.artifact: unknown registered artifact")
                elif isinstance(op_id, str) and isinstance(artifact, str):
                    artifact_refs.add((op_id, artifact))
            elif kind == "supplemental_artifact":
                if ref.get("artifact") not in supplemental_artifacts:
                    errors.append(f"{ref_where}.artifact: unknown supplemental artifact")
                elif isinstance(ref.get("artifact"), str):
                    supplemental_refs.add(ref["artifact"])
            elif kind == "repository_test":
                _validate_repository_test_ref(
                    ref, ref_where, supplemental_artifacts, errors
                )
                if isinstance(ref.get("path"), str):
                    test_refs.add(ref["path"])
                test_bindings.add((ref.get("log_artifact"), ref.get("rc_artifact")))
            else:
                errors.append(f"{ref_where}.kind: unsupported reference kind {kind!r}")
        if isinstance(phase_id, str):
            phase_ref_index[phase_id] = {
                "artifacts": artifact_refs,
                "supplemental": supplemental_refs,
                "tests": test_refs,
                "test_bindings": test_bindings,
            }
            for artifact in _PLAN_ALL_OPERATOR_ARTIFACTS.get(phase_id, frozenset()):
                missing_ops = sorted(
                    op_id
                    for op_id in operator_by_id
                    if (op_id, artifact) not in artifact_refs
                )
                if missing_ops:
                    errors.append(
                        f"{phase_where}: required artifact {artifact!r} missing for {missing_ops}"
                    )
            for artifact in _PLAN_ANY_OPERATOR_ARTIFACTS.get(phase_id, frozenset()):
                if status != "PARTIALLY_VERIFIED" \
                        and not any(name == artifact for _, name in artifact_refs):
                    errors.append(
                        f"{phase_where}: required operator artifact {artifact!r} missing"
                    )
            missing_tests = sorted(
                _PLAN_REQUIRED_TESTS.get(phase_id, frozenset()) - test_refs
            )
            if missing_tests:
                errors.append(f"{phase_where}: required repository tests missing {missing_tests}")
        gap_refs = _strings(
            phase.get("structured_gap_ids"), f"{phase_where}.structured_gap_ids", errors
        )
        unknown_gaps = sorted(set(gap_refs) - all_gap_ids)
        if unknown_gaps:
            errors.append(f"{phase_where}: unknown structured gap ids {unknown_gaps}")
        if status == "VERIFIED" and gap_refs:
            errors.append(f"{phase_where}: VERIFIED phase cannot reference open gaps")
        if status in {"VERIFIED_WITH_STRUCTURED_GAPS", "PARTIALLY_VERIFIED"} and not gap_refs:
            errors.append(f"{phase_where}: status requires at least one structured gap")
    if tuple(seen_phases) != _PLAN_PHASES:
        errors.append(
            "ledger.workflow_plan_evidence: phases must be exactly ordered N0 through N10"
        )
    missing_n0_supplemental = sorted(
        n0_supplemental - phase_ref_index.get("N0", {}).get("supplemental", set())
    )
    if missing_n0_supplemental:
        errors.append(
            "ledger.workflow_plan_evidence[N0]: missing static-gate evidence refs "
            f"{missing_n0_supplemental}"
        )

    # Online locator/repository/ref/head observations are deliberately not an
    # acceptance denominator.  Historical diagnostic rows may remain as
    # supplemental artifacts; formal authority comes only from each operator's
    # source content anchor (or its unchanged legacy identity route).
    multicard_raw = _as_list(
        root.get("multi_card_precision_equivalence"),
        "ledger.multi_card_precision_equivalence",
        errors,
    )
    multicard_operator_ids: list[str] = []
    multicard_inventory_artifacts: list[str] = []
    for index, raw_record in enumerate(multicard_raw):
        record = _as_dict(
            raw_record, f"ledger.multi_card_precision_equivalence[{index}]", errors
        )
        op_id = record.get("operator_id")
        record_where = f"ledger.multi_card_precision_equivalence[{index}]({op_id or '?'})"
        operator = operator_by_id.get(op_id)
        if operator is None:
            errors.append(f"{record_where}.operator_id: unknown operator")
            continue
        multicard_operator_ids.append(op_id)
        if record.get("status") != "VERIFIED":
            errors.append(f"{record_where}.status: expected VERIFIED")
        if record.get("execution_scope") != "precision_only":
            errors.append(f"{record_where}.execution_scope: expected precision_only")
        if record.get("performance_collected") is not False:
            errors.append(f"{record_where}: precision equivalence cannot claim performance")
        devices = _as_list(record.get("devices"), f"{record_where}.devices", errors)
        if (
            len(devices) < 2
            or any(not _is_int(device) or device < 0 for device in devices)
            or len(devices) != len(set(devices))
        ):
            errors.append(f"{record_where}.devices: expected at least two unique ids")
        vectors: dict[str, list[Any]] = {}
        for key in ("shard_sizes", "shard_produced", "shard_failed"):
            values = _as_list(record.get(key), f"{record_where}.{key}", errors)
            vectors[key] = values
            if len(values) != len(devices) or any(
                not _is_int(value) or value < 0 for value in values
            ):
                errors.append(
                    f"{record_where}.{key}: expected one non-negative count per device"
                )
        generated_count = operator.get("generated", {}).get("case_count")
        produced = operator.get("executable", {}).get("produced")
        failed = operator.get("executable", {}).get("failed")
        if sum(vectors.get("shard_sizes", [])) != generated_count:
            errors.append(f"{record_where}: shard_sizes must cover generated case_count")
        if sum(vectors.get("shard_produced", [])) != produced:
            errors.append(f"{record_where}: shard_produced must equal single produced")
        if sum(vectors.get("shard_failed", [])) != failed:
            errors.append(f"{record_where}: shard_failed must equal single failed")
        if record.get("merged_total") != generated_count:
            errors.append(f"{record_where}.merged_total: must equal generated case_count")
        if record.get("merged_passed") != operator.get("precision", {}).get("passed"):
            errors.append(f"{record_where}.merged_passed: must equal single precision pass")
        if record.get("merged_failed") != failed:
            errors.append(f"{record_where}.merged_failed: must equal single execution failed")
        single_sha = record.get("single_verdict_sha256")
        multi_sha = record.get("multi_verdict_sha256")
        formal_verdict_sha = operator.get("artifacts", {}).get("verdict", {}).get("sha256")
        if (
            record.get("equivalent") is not True
            or single_sha != multi_sha
            or single_sha != formal_verdict_sha
        ):
            errors.append(
                f"{record_where}: equivalence requires identical single/multi formal verdict SHA"
            )
        inventory_name = record.get("inventory_artifact")
        if not isinstance(inventory_name, str) or not inventory_name:
            errors.append(f"{record_where}.inventory_artifact: expected non-empty string")
            inventory_name = ""
        artifact_refs = _strings(
            record.get("artifact_refs"), f"{record_where}.artifact_refs", errors)
        if artifact_refs != [inventory_name]:
            errors.append(f"{record_where}.artifact_refs: expected only inventory_artifact")
        unknown_artifacts = sorted(set(artifact_refs) - supplemental_artifacts.keys())
        if unknown_artifacts:
            errors.append(f"{record_where}: unknown artifact refs {unknown_artifacts}")
        if inventory_name:
            multicard_inventory_artifacts.append(inventory_name)
            inventory_item = supplemental_artifacts.get(inventory_name)
            inventory_path_raw = (
                inventory_item.get("path") if isinstance(inventory_item, dict) else None)
            inventory_path = (
                Path(inventory_path_raw)
                if isinstance(inventory_path_raw, str) and os.path.isabs(inventory_path_raw)
                else Path(f"/missing-{_MULTICARD_INVENTORY_FILENAME}"))
            inventory = _supplemental_json(
                supplemental_artifacts, inventory_name,
                f"{record_where}.inventory_artifact", errors)
            _validate_multicard_projection(
                record, operator, inventory, inventory_path, record_where,
                verify_artifacts, errors)
    if len(multicard_operator_ids) != len(set(multicard_operator_ids)):
        errors.append("ledger.multi_card_precision_equivalence: duplicate operator_id")
    if set(multicard_operator_ids) != set(operator_by_id):
        errors.append(
            "ledger.multi_card_precision_equivalence must account for every operator"
        )
    if len(multicard_inventory_artifacts) != len(set(multicard_inventory_artifacts)):
        errors.append(
            "ledger.multi_card_precision_equivalence: duplicate inventory_artifact"
        )
    n10_supplemental = phase_ref_index.get("N10", {}).get("supplemental", set())
    required_n10_supplemental = {
        artifact
        for record in multicard_raw
        if isinstance(record, dict)
        for artifact in record.get("artifact_refs", [])
        if isinstance(artifact, str)
    }
    missing_n10_supplemental = sorted(
        required_n10_supplemental - n10_supplemental
    )
    if missing_n10_supplemental:
        errors.append(
            "ledger.workflow_plan_evidence[N10]: missing multi-card evidence refs "
            f"{missing_n10_supplemental}"
        )

    regression_raw = _as_list(
        root.get("regression_evidence"), "ledger.regression_evidence", errors
    )
    regression_ids: list[str] = []
    regression_bindings: set[tuple[str, str]] = set()
    for index, raw_record in enumerate(regression_raw):
        record = _as_dict(raw_record, f"ledger.regression_evidence[{index}]", errors)
        record_id = record.get("evidence_id")
        if not isinstance(record_id, str) or not record_id:
            errors.append(
                f"ledger.regression_evidence[{index}].evidence_id: expected string"
            )
        else:
            regression_ids.append(record_id)
        if record.get("result") != "OK":
            errors.append(f"ledger.regression_evidence[{index}].result: expected OK")
        if not _is_int(record.get("tests_run")) or record.get("tests_run", 0) <= 0:
            errors.append(
                f"ledger.regression_evidence[{index}].tests_run: expected positive integer"
            )
        artifact_name = record.get("artifact")
        if artifact_name not in supplemental_artifacts:
            errors.append(
                f"ledger.regression_evidence[{index}].artifact: unknown supplemental artifact"
            )
            regression_log = ""
        else:
            regression_log = _supplemental_text(
                supplemental_artifacts,
                artifact_name,
                f"ledger.regression_evidence[{index}].artifact",
                errors,
            )
            ran = [int(value) for value in re.findall(r"^Ran ([0-9]+) tests? in ", regression_log, re.MULTILINE)]
            if not ran or ran[-1] != record.get("tests_run"):
                errors.append(
                    f"ledger.regression_evidence[{index}].tests_run: log terminal mismatch"
                )
            ok_match = re.findall(
                r"^OK(?: \(skipped=([0-9]+)\))?$", regression_log, re.MULTILINE
            )
            measured_skipped = int(ok_match[-1] or 0) if ok_match else None
            if measured_skipped != record.get("skipped"):
                errors.append(
                    f"ledger.regression_evidence[{index}].skipped: log terminal mismatch"
                )
        rc_artifact = record.get("rc_artifact")
        if rc_artifact is not None and rc_artifact not in supplemental_artifacts:
            errors.append(
                f"ledger.regression_evidence[{index}].rc_artifact: unknown supplemental artifact"
            )
        elif isinstance(rc_artifact, str):
            regression_rc = _supplemental_text(
                supplemental_artifacts,
                rc_artifact,
                f"ledger.regression_evidence[{index}].rc_artifact",
                errors,
            )
            if regression_rc.strip() != "0":
                errors.append(
                    f"ledger.regression_evidence[{index}].rc_artifact: expected measured rc 0"
                )
            if isinstance(artifact_name, str):
                regression_bindings.add((artifact_name, rc_artifact))
        if not _is_int(record.get("skipped")) or record.get("skipped", -1) < 0:
            errors.append(
                f"ledger.regression_evidence[{index}].skipped: expected non-negative integer"
            )
    if len(regression_ids) != len(set(regression_ids)):
        errors.append("ledger.regression_evidence: duplicate evidence_id")
    referenced_test_bindings = {
        binding
        for refs in phase_ref_index.values()
        for binding in refs.get("test_bindings", set())
        if isinstance(binding, tuple)
    }
    unknown_test_bindings = sorted(referenced_test_bindings - regression_bindings)
    if unknown_test_bindings:
        errors.append(
            "ledger.workflow_plan_evidence: repository tests must bind an OK regression "
            f"log/rc pair; missing {unknown_test_bindings}"
        )

    measured_forms = sorted(
        {
            op.get("taskbook", {}).get("input_form")
            for op in operators
            if isinstance(op.get("taskbook"), dict)
            and isinstance(op.get("taskbook", {}).get("input_form"), str)
        }
    )
    if sorted(actual_taskbook_forms) != measured_forms:
        errors.append(
            "ledger.authority.actual_taskbook_input_forms must exactly mirror operators"
        )
    measured_provenance = sorted(
        {
            op.get("source", {}).get("provenance_kind")
            for op in operators
            if isinstance(op.get("source"), dict)
            and isinstance(op.get("source", {}).get("provenance_kind"), str)
        }
    )
    if actual_provenance_raw is not None and sorted(actual_provenance) != measured_provenance:
        errors.append(
            "ledger.authority.actual_dut_provenance_kinds must exactly mirror operators"
        )
    return errors


def validate(
    ledger: Any, *, verify_artifacts: bool = False, allow_historical: bool = False
) -> None:
    """Raise :class:`LedgerValidationError` unless every invariant holds."""

    errors = validation_errors(
        ledger,
        verify_artifacts=verify_artifacts,
        allow_historical=allow_historical,
    )
    if errors:
        raise LedgerValidationError(errors)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument(
        "--verify-artifacts",
        action="store_true",
        help="recompute every recorded artifact SHA-256 (run in the artifact environment)",
    )
    parser.add_argument(
        "--historical-read-only",
        action="store_true",
        help=(
            "read legacy v1 identity ledgers without upgrading them; output is "
            "historical evidence, never a current completion verdict"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"RGE_LEDGER_INVALID: cannot read ledger: {exc}", file=sys.stderr)
        return 1
    errors = validation_errors(
        ledger,
        verify_artifacts=args.verify_artifacts,
        allow_historical=args.historical_read_only,
    )
    if errors:
        print("RGE_LEDGER_INVALID", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    label = (
        "RGE_LEDGER_HISTORICAL_VALID"
        if args.historical_read_only and ledger.get("schema_version") == 1
        else "RGE_LEDGER_VALID"
    )
    print(
        f"{label} "
        f"operators={len(ledger['operators'])} "
        f"artifacts_verified={'yes' if args.verify_artifacts else 'no'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
