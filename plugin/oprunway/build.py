"""Fresh CANN custom-operator build with a compact, auditable receipt."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any

from .contract import spec_digest, validate_soc, validate_spec
from .source import (
    build_input_anchor,
    build_input_snapshot,
    build_input_snapshot_anchor,
    content_anchor,
)
from .util import (
    WorkflowError,
    atomic_write_json,
    load_json,
    require_directory,
    require_plain_file,
    run_command,
    sha256_file,
    utc_now,
)


def _snapshot_files(root: Path, suffix: str) -> dict[str, tuple[int, int, str]]:
    result: dict[str, tuple[int, int, str]] = {}
    for path in root.rglob(f"*{suffix}"):
        if path.is_symlink() or not path.is_file():
            continue
        info = path.stat()
        result[str(path.resolve())] = (info.st_mtime_ns, info.st_size, sha256_file(path))
    return result


def _changed_files(
    before: dict[str, tuple[int, int, str]], after: dict[str, tuple[int, int, str]]
) -> list[Path]:
    return [Path(path) for path, identity in after.items() if before.get(path) != identity]


def _select_package(candidates: list[Path], source_root: Path) -> tuple[Path, list[dict[str, Any]]]:
    identities: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.is_symlink() or not stat.S_ISREG(candidate.stat().st_mode):
            raise WorkflowError("PACKAGE_IDENTITY_FAILED", f"package is not a regular file: {candidate}")
        identities.append(
            {
                "path": str(candidate.resolve()),
                "size": candidate.stat().st_size,
                "sha256": sha256_file(candidate),
            }
        )
    digests = {item["sha256"] for item in identities}
    if not identities or len(digests) != 1:
        raise WorkflowError(
            "PACKAGE_IDENTITY_FAILED",
            f"expected one fresh package content identity, found {len(digests)}",
        )
    selected = min(
        (Path(item["path"]) for item in identities),
        key=lambda path: (len(path.relative_to(source_root).parts), path.as_posix()),
    )
    return selected, identities


def _remaining_timeout(deadline: float, cap: int, label: str) -> int:
    remaining = deadline - time.monotonic()
    if remaining < 1:
        raise WorkflowError("BUILD_TIMEOUT", f"fresh DUT build budget exhausted before {label}")
    return min(cap, max(1, int(remaining)))


def _read_nm_symbols(library: Path, log_path: Path, deadline: float) -> set[str]:
    try:
        result = subprocess.run(
            ["nm", "-D", "--defined-only", str(library)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_remaining_timeout(deadline, 60, "symbol probe"),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkflowError("SYMBOL_PROBE_FAILED", f"nm timed out for {library}") from exc
    log_path.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        raise WorkflowError("SYMBOL_PROBE_FAILED", f"nm failed for {library}")
    symbols: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields:
            symbols.add(fields[-1])
    return symbols


def _discover_library(
    install_root: Path, symbols: list[str], evidence_dir: Path, deadline: float
) -> tuple[Path, Path]:
    matches: list[tuple[Path, Path]] = []
    for index, candidate in enumerate(sorted(install_root.rglob("*.so"))):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        log = evidence_dir / f"nm-{index}.txt"
        defined = _read_nm_symbols(candidate, log, deadline)
        if all(symbol in defined for symbol in symbols):
            matches.append((candidate.resolve(), log.resolve()))
    if len(matches) != 1:
        raise WorkflowError(
            "LIBRARY_IDENTITY_FAILED",
            f"expected one installed ELF defining {symbols}, found {len(matches)}",
        )
    return matches[0]


def _vendor_root(install_root: Path, library: Path) -> Path:
    for parent in library.parents:
        if parent.parent.name == "vendors" and install_root in parent.parents:
            return parent.resolve()
    raise WorkflowError("LIBRARY_IDENTITY_FAILED", "fresh vendor ELF is not below install_root/vendors/<vendor>")


def _cache_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if not line or line.startswith(("#", "//")) or ":" not in line or "=" not in line:
            continue
        name_and_type, value = line.split("=", 1)
        name, _type = name_and_type.split(":", 1)
        if name in values:
            raise WorkflowError("BUILD_TARGET_UNPROVEN", f"duplicate CMake cache key {name}: {path}")
        values[name] = value
    return values


def _cache_matches_request(
    values: dict[str, str], soc: str, token: str, vendor: str
) -> bool:
    selected_tokens = {
        item.strip()
        for field in ("ASCEND_OP_NAME", "COMPILED_OPS", "NEED_COMPILE_OPS")
        for item in re.split(r"[;,]", values.get(field, ""))
        if item.strip()
    }
    return (
        values.get("ASCEND_COMPUTE_UNIT") == soc
        and values.get("VENDOR_NAME") == vendor
        and token in selected_tokens
    )


def _find_requested_cache(
    source_root: Path,
    before: dict[str, tuple[int, int, str]],
    soc: str,
    token: str,
    vendor: str,
) -> tuple[Path, dict[str, str]]:
    after = _snapshot_files(source_root, "CMakeCache.txt")
    candidates = _changed_files(before, after)
    accepted: list[tuple[Path, dict[str, str]]] = []
    for candidate in candidates:
        try:
            values = _cache_values(candidate)
        except (OSError, UnicodeDecodeError, WorkflowError):
            continue
        if _cache_matches_request(values, soc, token, vendor):
            accepted.append((candidate.resolve(), values))
    if len(accepted) != 1:
        raise WorkflowError(
            "BUILD_TARGET_UNPROVEN",
            f"expected one changed CMakeCache request soc={soc} op={token}, found {len(accepted)}",
        )
    return accepted[0]


def _find_cache(
    source_root: Path,
    before: dict[str, tuple[int, int, str]],
    soc: str,
    token: str,
    op_type: str,
    vendor: str,
) -> Path:
    cache, values = _find_requested_cache(source_root, before, soc, token, vendor)
    if values.get(f"OP_CACHE_{token}_{soc}") != op_type:
        raise WorkflowError(
            "BUILD_TARGET_UNPROVEN",
            f"expected one changed CMakeCache binding soc={soc} op={token}, found 0",
        )
    return cache


def _target_delivery(
    install_root: Path, vendor_root: Path, soc: str, token: str, op_type: str
) -> list[dict[str, Any]]:
    install_root = require_directory(install_root, "package install root")
    vendor_root = require_directory(vendor_root, "fresh vendor root")
    if vendor_root.parent.name != "vendors" or install_root not in vendor_root.parents:
        raise WorkflowError("TARGET_KERNEL_MISSING", "fresh vendor root is outside install_root/vendors")
    ops_info: list[Path] = []
    expected_name = f"aic-{soc}-ops-info.json"
    for path in sorted(vendor_root.rglob("aic-*-ops-info.json")):
        if path.is_symlink() or not path.is_file() or path.parent.parent.name != "config" \
                or path.parent.name != soc or path.name != expected_name:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowError("TARGET_KERNEL_MISSING", f"invalid installed ops-info: {path}") from exc
        definition = value.get(op_type) if isinstance(value, dict) else None
        if not isinstance(definition, dict):
            continue
        op_file = definition.get("opFile", {}).get("value")
        op_interface = definition.get("opInterface", {}).get("value")
        if op_file != token or op_interface != token:
            continue
        ops_info.append(path.resolve())
    if len(ops_info) != 1:
        raise WorkflowError(
            "TARGET_KERNEL_MISSING",
            f"expected one exact {soc} ops-info binding {token}/{op_type}, found {len(ops_info)}",
        )

    binary_configs = [
        path.resolve()
        for path in sorted(vendor_root.rglob("binary_info_config.json"))
        if not path.is_symlink() and path.is_file()
        and path.parent.name == soc and path.parent.parent.name == "config"
        and path.parent.parent.parent.name == "kernel"
    ]
    if len(binary_configs) != 1:
        raise WorkflowError(
            "TARGET_KERNEL_MISSING",
            f"expected one exact {soc} binary_info_config.json, found {len(binary_configs)}",
        )
    binary_config = binary_configs[0]
    try:
        binary_value = json.loads(binary_config.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowError("TARGET_KERNEL_MISSING", f"invalid kernel delivery config: {binary_config}") from exc
    op_delivery = binary_value.get(op_type) if isinstance(binary_value, dict) else None
    binaries = op_delivery.get("binaryList") if isinstance(op_delivery, dict) else None
    if not isinstance(binaries, list) or not binaries or any(not isinstance(item, dict) for item in binaries):
        raise WorkflowError("TARGET_KERNEL_MISSING", f"{op_type} has no delivered binary list for {soc}")

    kernel_root = binary_config.parent.parent.parent.resolve()
    delivered_files: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(binaries):
        for field in ("binPath", "jsonPath"):
            relative = item.get(field)
            pure = PurePosixPath(relative) if isinstance(relative, str) else None
            if pure is None or pure.is_absolute() or not pure.parts \
                    or any(part in {"", ".", ".."} for part in pure.parts):
                raise WorkflowError(
                    "TARGET_KERNEL_MISSING", f"binaryList[{index}].{field} is not a normalized relative path"
                )
            delivered = kernel_root.joinpath(*pure.parts)
            if delivered.is_symlink() or not delivered.is_file() or delivered.stat().st_size <= 0:
                raise WorkflowError("TARGET_KERNEL_MISSING", f"delivered kernel file is missing: {relative}")
            resolved = delivered.resolve()
            if kernel_root not in resolved.parents:
                raise WorkflowError("TARGET_KERNEL_MISSING", f"delivered kernel file escapes kernel root: {relative}")
            delivered_files[str(resolved)] = {
                "path": resolved.relative_to(install_root).as_posix(),
                "size": resolved.stat().st_size,
                "sha256": sha256_file(resolved),
            }

    info = ops_info[0]
    return [{
        "requested_soc": soc,
        "delivery_soc": soc,
        "op_type": op_type,
        "build_token": token,
        "ops_info": {
            "path": info.relative_to(install_root).as_posix(),
            "size": info.stat().st_size,
            "sha256": sha256_file(info),
        },
        "binary_config": {
            "path": binary_config.relative_to(install_root).as_posix(),
            "size": binary_config.stat().st_size,
            "sha256": sha256_file(binary_config),
        },
        "kernel_files": sorted(delivered_files.values(), key=lambda item: item["path"]),
    }]


def _validate_facts(spec: dict[str, Any], facts: dict[str, Any], source_root: Path, soc: str) -> None:
    if facts.get("schema") != "oprunway.source_facts" or facts.get("schema_version") != 1:
        raise WorkflowError("INVALID_FACTS", "source facts schema mismatch")
    if facts.get("spec_sha256") != spec_digest(spec):
        raise WorkflowError("INVALID_FACTS", "source facts are not bound to this spec")
    if facts.get("status") != "READY" or facts.get("target", {}).get("requested_soc") != soc:
        raise WorkflowError("UNSUPPORTED_TARGET", "source facts do not authorize this target")
    source_facts = facts.get("source")
    if not isinstance(source_facts, dict):
        raise WorkflowError("INVALID_FACTS", "source facts lack source anchors")
    actual = content_anchor(source_root, spec["operator"]["source_subdir"])
    if actual != source_facts.get("content_anchor"):
        raise WorkflowError("SOURCE_DRIFT", "staged source does not match source facts")
    actual_build = build_input_anchor(source_root)
    if actual_build != source_facts.get("build_input_anchor"):
        raise WorkflowError("SOURCE_DRIFT", "staged build inputs do not match source facts")


def build_operator(
    *,
    spec: dict[str, Any],
    facts_path: os.PathLike[str] | str,
    source_root: os.PathLike[str] | str,
    install_root: os.PathLike[str] | str,
    target_soc: str,
    out_path: os.PathLike[str] | str,
    evidence_dir: os.PathLike[str] | str,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    checked = validate_spec(spec)
    target_soc = validate_soc(target_soc)
    facts = load_json(facts_path)
    if not isinstance(facts, dict):
        raise WorkflowError("INVALID_FACTS", "source facts must be an object")
    source = require_directory(source_root, "staged source root")
    _validate_facts(checked, facts, source, target_soc)
    build_script = require_plain_file(source / "build.sh", "CANN build script")
    destination = Path(install_root)
    if destination.exists():
        raise WorkflowError("DIRTY_SESSION", f"install root already exists: {destination}")
    destination.mkdir(parents=True)
    destination = destination.resolve()
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    evidence = evidence.resolve()

    source_anchor_before = content_anchor(source, checked["operator"]["source_subdir"])
    build_anchor_before = build_input_anchor(source)
    build_inputs_before = build_input_snapshot(source)
    runs_before = _snapshot_files(source, ".run")
    caches_before = _snapshot_files(source, "CMakeCache.txt")
    build = checked["build"]
    operator = checked["operator"]
    argv = [
        "bash",
        str(build_script),
        "--pkg",
        f"--soc={target_soc}",
        f"--ops={operator['build_token']}",
        f"--vendor_name={build['vendor_name']}",
        f"-j{build['jobs']}",
    ]
    if operator["source_subdir"].startswith("experimental/"):
        argv.append("--experimental")
    stage_timeout = timeout_seconds or checked["runner"]["stage_timeout_seconds"]
    deadline = time.monotonic() + stage_timeout
    build_receipt = run_command(
        argv,
        cwd=source,
        timeout_seconds=_remaining_timeout(deadline, stage_timeout, "build command"),
        stdout_path=evidence / "build.stdout.log",
        stderr_path=evidence / "build.stderr.log",
    )
    if build_receipt.timed_out:
        raise WorkflowError("BUILD_TIMEOUT", "fresh DUT build exceeded stage timeout")
    if build_receipt.returncode != 0:
        raise WorkflowError("BUILD_FAILED", "fresh DUT build failed; attribution is not yet proven")

    packages = _changed_files(runs_before, _snapshot_files(source, ".run"))
    package, package_copies = _select_package(packages, source)
    install_receipt = run_command(
        [str(package), "--quiet", f"--install-path={destination}"],
        cwd=source,
        timeout_seconds=_remaining_timeout(deadline, min(600, stage_timeout), "package install"),
        stdout_path=evidence / "install.stdout.log",
        stderr_path=evidence / "install.stderr.log",
    )
    if install_receipt.timed_out or install_receipt.returncode != 0:
        raise WorkflowError("INSTALL_FAILED", "fresh custom OPP package install failed")

    symbols = [f"aclnn{operator['aclnn_name']}GetWorkspaceSize", f"aclnn{operator['aclnn_name']}"]
    library, nm_log = _discover_library(destination, symbols, evidence, deadline)
    custom_opp_root = _vendor_root(destination, library)
    cache, cache_values = _find_requested_cache(
        source,
        caches_before,
        target_soc,
        operator["build_token"],
        build["vendor_name"],
    )
    binding_key = f"OP_CACHE_{operator['build_token']}_{target_soc}"
    binding_value = cache_values.get(binding_key)
    delivery_error: dict[str, str] | None = None
    try:
        delivery = _target_delivery(
            destination, custom_opp_root, target_soc, operator["build_token"], operator["op_type"]
        )
    except WorkflowError as exc:
        if exc.code != "TARGET_KERNEL_MISSING" or binding_value not in {None, operator["op_type"]}:
            raise
        delivery = []
        delivery_error = {"code": exc.code, "message": str(exc)}
    if delivery_error is None and binding_value != operator["op_type"]:
        raise WorkflowError(
            "BUILD_TARGET_UNPROVEN",
            f"expected one changed CMakeCache binding soc={target_soc} "
            f"op={operator['build_token']}, found 0",
        )
    source_anchor_after = content_anchor(source, operator["source_subdir"])
    if source_anchor_after != source_anchor_before:
        raise WorkflowError("SOURCE_MUTATED", "build changed bytes inside the DUT source scope")
    build_inputs_after = build_input_snapshot(source)
    if any(build_inputs_after.get(path) != identity for path, identity in build_inputs_before.items()):
        raise WorkflowError("SOURCE_MUTATED", "build changed a pre-existing bound build input")
    post_build_anchor = build_input_snapshot_anchor(build_inputs_after)
    _remaining_timeout(deadline, stage_timeout, "build receipt publication")

    receipt = {
        "schema": "oprunway.build_receipt",
        "schema_version": 1,
        "status": "TARGET_DELIVERY_MISSING" if delivery_error else "VERIFIED",
        "created_at": utc_now(),
        "spec_sha256": spec_digest(checked),
        "source_content_anchor": source_anchor_before,
        "build_input_anchor": build_anchor_before,
        "staged_source_root": str(source),
        "post_build_input_anchor": post_build_anchor,
        "target": {"soc": target_soc, "build_token": operator["build_token"]},
        "build": build_receipt.to_dict(),
        "install": install_receipt.to_dict(),
        "package": {
            "path": str(package.resolve()),
            "size": package.stat().st_size,
            "sha256": sha256_file(package),
            "equivalent_paths": [
                item for item in package_copies if item["path"] != str(package.resolve())
            ],
        },
        "cmake_cache": {"path": str(cache), "sha256": sha256_file(cache)},
        "target_binding": {
            "key": binding_key,
            "expected": operator["op_type"],
            "actual": binding_value,
        },
        "vendor": {
            "package_install_root": str(destination),
            "custom_opp_root": str(custom_opp_root),
            "library_path": str(library),
            "library_sha256": sha256_file(library),
            "symbols": symbols,
            "nm_log_path": str(nm_log),
            "nm_log_sha256": sha256_file(nm_log),
        },
        "target_delivery": delivery,
        "target_delivery_error": delivery_error,
    }
    atomic_write_json(out_path, receipt)
    return receipt
