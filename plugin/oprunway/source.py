"""Caller-trusted source materialization facts and content anchors."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from .contract import spec_digest, validate_soc, validate_spec
from .util import (
    WorkflowError,
    atomic_write_json,
    require_directory,
    require_plain_file,
    sha256_file,
    utc_now,
)


ANCHOR_SCHEMA = "oprunway.source_content_anchor"
ANCHOR_VERSION = 1
BUILD_ANCHOR_SCHEMA = "oprunway.build_input_anchor"
STAGING_ROOT_IGNORES = frozenset({".git", ".oprunway", "build", "build_out", "reports"})
STAGING_ANYWHERE_IGNORES = frozenset({"__pycache__", ".pytest_cache"})


def is_transport_artifact(name: str) -> bool:
    """Return true for metadata files/directories never consumed as DUT source."""
    return name == ".DS_Store" or name == "__MACOSX" or name.startswith("._")


def staging_ignored_names(names: list[str], *, at_root: bool) -> set[str]:
    """Return files/directories omitted from the clean staged build tree."""
    blocked = set(names) & STAGING_ANYWHERE_IGNORES
    blocked |= {name for name in names if is_transport_artifact(name)}
    if at_root:
        blocked |= set(names) & STAGING_ROOT_IGNORES
    return blocked


def _scope_root(source_root: Path, scope: str) -> Path:
    target = source_root.joinpath(*PurePosixPath(scope).parts)
    if target.is_symlink() or not target.is_dir():
        raise WorkflowError("INVALID_SOURCE", f"source scope must be a real directory: {target}")
    resolved = target.resolve()
    if resolved != source_root and source_root not in resolved.parents:
        raise WorkflowError("INVALID_SOURCE", f"source scope escapes source root: {scope}")
    return resolved


def _manifest(
    source_root: os.PathLike[str] | str,
    scope: str,
    *,
    ignore_anywhere_transients: bool,
    ignore_root_transients: bool,
) -> list[dict[str, Any]]:
    root = require_directory(source_root, "source root")
    target = _scope_root(root, scope)
    manifest: list[dict[str, Any]] = []
    for current, directories, files in os.walk(target, followlinks=False):
        current_path = Path(current)
        at_root = current_path == target
        names = directories + files
        ignored = {name for name in names if is_transport_artifact(name)}
        if ignore_anywhere_transients:
            ignored |= set(names) & STAGING_ANYWHERE_IGNORES
        if ignore_root_transients and at_root:
            ignored |= set(names) & STAGING_ROOT_IGNORES
        directories[:] = [name for name in directories if name not in ignored]
        for name in sorted(directories):
            candidate = current_path / name
            if candidate.is_symlink():
                raise WorkflowError("INVALID_SOURCE", f"symlink directory in source scope: {candidate}")
        for name in sorted(files):
            if name in ignored:
                continue
            candidate = current_path / name
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise WorkflowError("INVALID_SOURCE", f"non-regular file in source scope: {candidate}")
            relative = candidate.relative_to(target).as_posix()
            manifest.append(
                {
                    "path": relative,
                    "mode": stat.S_IMODE(info.st_mode),
                    "size": info.st_size,
                    "sha256": sha256_file(candidate),
                }
            )
    if not manifest:
        raise WorkflowError("INVALID_SOURCE", f"source scope is empty: {scope}")
    return sorted(manifest, key=lambda entry: entry["path"])


def _manifest_anchor(manifest: list[dict[str, Any]], *, schema: str, scope: str) -> dict[str, Any]:
    digest = hashlib.sha256()
    for item in manifest:
        digest.update(item["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["mode"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(item["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(item["sha256"].encode("ascii"))
        digest.update(b"\0")
    return {
        "schema": schema,
        "schema_version": ANCHOR_VERSION,
        "algorithm": "content_mode_manifest_sha256_v2",
        "scope": scope,
        "sha256": digest.hexdigest(),
        "file_count": len(manifest),
        "total_bytes": sum(item["size"] for item in manifest),
    }


def _anchor(
    source_root: os.PathLike[str] | str,
    scope: str,
    *,
    schema: str,
    ignore_anywhere_transients: bool,
    ignore_root_transients: bool,
) -> dict[str, Any]:
    manifest = _manifest(
        source_root,
        scope,
        ignore_anywhere_transients=ignore_anywhere_transients,
        ignore_root_transients=ignore_root_transients,
    )
    return _manifest_anchor(manifest, schema=schema, scope=scope)


def content_anchor(source_root: os.PathLike[str] | str, scope: str) -> dict[str, Any]:
    return _anchor(
        source_root,
        scope,
        schema=ANCHOR_SCHEMA,
        ignore_anywhere_transients=True,
        ignore_root_transients=False,
    )


def build_input_anchor(source_root: os.PathLike[str] | str) -> dict[str, Any]:
    """Bind every non-generated input that can influence the fresh package build."""
    return _anchor(
        source_root,
        ".",
        schema=BUILD_ANCHOR_SCHEMA,
        ignore_anywhere_transients=True,
        ignore_root_transients=True,
    )


def build_input_snapshot(source_root: os.PathLike[str] | str) -> dict[str, tuple[int, int, str]]:
    """Return the exact pre-build file identities that must remain unchanged."""
    return {
        item["path"]: (item["mode"], item["size"], item["sha256"])
        for item in _manifest(
            source_root,
            ".",
            ignore_anywhere_transients=True,
            ignore_root_transients=True,
        )
    }


def build_input_snapshot_anchor(snapshot: dict[str, tuple[int, int, str]]) -> dict[str, Any]:
    """Aggregate an already-hashed post-build snapshot without reading large additions again."""
    manifest = [
        {"path": path, "mode": identity[0], "size": identity[1], "sha256": identity[2]}
        for path, identity in sorted(snapshot.items())
    ]
    if not manifest:
        raise WorkflowError("INVALID_SOURCE", "build input snapshot is empty")
    return _manifest_anchor(manifest, schema=BUILD_ANCHOR_SCHEMA, scope=".")


def create_source_facts(
    *,
    spec: dict[str, Any],
    taskdoc_path: os.PathLike[str] | str,
    source_root: os.PathLike[str] | str,
    target_soc: str,
    out_path: os.PathLike[str] | str,
) -> dict[str, Any]:
    checked = validate_spec(spec)
    target_soc = validate_soc(target_soc)
    taskdoc = require_plain_file(taskdoc_path, "task document")
    source = require_directory(source_root, "source root")
    taskdoc_sha = sha256_file(taskdoc)
    if taskdoc_sha != checked["task"]["taskdoc_sha256"]:
        raise WorkflowError(
            "TASKDOC_DRIFT",
            "task document SHA-256 does not match the acceptance spec",
        )
    hardware_match = target_soc in checked["task"]["hardware"]
    facts = {
        "schema": "oprunway.source_facts",
        "schema_version": 1,
        "created_at": utc_now(),
        "spec_sha256": spec_digest(checked),
        "input_association": {
            "schema": "oprunway.caller_trusted_input",
            "schema_version": 1,
            "policy": "caller_trusted_pair_v1",
            "correspondence": "asserted_by_caller",
        },
        "taskdoc": {
            "path": str(taskdoc),
            "sha256": taskdoc_sha,
        },
        "source": {
            "root": str(source),
            "content_anchor": content_anchor(source, checked["operator"]["source_subdir"]),
            "build_input_anchor": build_input_anchor(source),
        },
        "target": {
            "requested_soc": target_soc,
            "declared_hardware": checked["task"]["hardware"],
            "supported": hardware_match,
        },
        "status": "READY" if hardware_match else "UNSUPPORTED",
    }
    atomic_write_json(out_path, facts)
    return facts
