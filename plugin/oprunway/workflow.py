"""One clean-session path from caller-trusted inputs to a deterministic verdict."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from .atk import generate_cases, run_cases
from .build import build_operator
from .contract import spec_digest, validate_soc, validate_spec
from .source import build_input_anchor, content_anchor, create_source_facts, staging_ignored_names
from .util import (
    WorkflowError,
    atomic_write_json,
    require_directory,
    require_plain_file,
    sha256_file,
    utc_now,
)
from .verdict import finalize


_FORMAL_VERDICTS = {"PASS", "DUT_FAIL", "UNSUPPORTED"}


def _stage_source(origin: Path, destination: Path) -> None:
    def ignored(current: str, names: list[str]) -> set[str]:
        current_path = Path(current).resolve()
        return staging_ignored_names(names, at_root=current_path == origin)

    shutil.copytree(origin, destination, symlinks=True, ignore=ignored)


def _copy_input(source: os.PathLike[str] | str, destination: Path, label: str) -> Path:
    original = require_plain_file(source, label)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, destination, follow_symlinks=False)
    return destination.resolve()


def run_acceptance(
    *,
    spec: dict[str, Any],
    taskdoc_path: os.PathLike[str] | str,
    source_root: os.PathLike[str] | str,
    design_path: os.PathLike[str] | str,
    atk_bin: os.PathLike[str] | str,
    target_soc: str,
    session_dir: os.PathLike[str] | str,
    generator_path: os.PathLike[str] | str | None = None,
    execution_plugin: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    checked = validate_spec(spec)
    target_soc = validate_soc(target_soc)
    origin = require_directory(source_root, "caller source root")
    session = Path(session_dir)
    if session.exists():
        raise WorkflowError("DIRTY_SESSION", f"session directory already exists: {session}")
    if not str(session).isascii():
        raise WorkflowError("SESSION_PATH_UNSUPPORTED", "session directory must use an ASCII path")
    try:
        session.mkdir(parents=True)
    except FileExistsError as exc:
        raise WorkflowError("DIRTY_SESSION", f"session directory already exists: {session}") from exc
    except OSError as exc:
        raise WorkflowError("INVALID_INPUT", f"cannot create session directory {session}: {exc}") from exc
    session = session.resolve()
    inputs = session / "inputs"
    receipts = session / "receipts"
    reports = session / "reports"
    try:
        receipts.mkdir()
        reports.mkdir()
    except OSError as exc:
        raise WorkflowError("INVALID_INPUT", f"cannot initialize session directory {session}: {exc}") from exc

    started_at = utc_now()
    started = time.monotonic()
    budget = checked["runner"]["workflow_timeout_seconds"]
    timings: dict[str, float] = {}
    current_stage = "materialize"

    def timed(name: str, function: Callable[[], Any]) -> Any:
        nonlocal current_stage
        current_stage = name
        stage_started = time.monotonic()
        try:
            return function()
        finally:
            timings[name] = round(time.monotonic() - stage_started, 6)

    def remaining() -> int:
        value = budget - (time.monotonic() - started)
        if value < 1:
            raise WorkflowError("WORKFLOW_TIMEOUT", f"active workflow exceeded {budget}s")
        return max(1, int(value))

    def stage_timeout() -> int:
        return min(remaining(), checked["runner"]["stage_timeout_seconds"])

    workflow_path = receipts / "workflow.json"
    acceptance_path = reports / "acceptance.json"
    pending_acceptance_path = reports / "acceptance.pending.json"
    acceptance_published = False

    def rollback_acceptance() -> None:
        if not acceptance_published:
            return
        for final, pending in (
            (acceptance_path, pending_acceptance_path),
            (acceptance_path.with_suffix(".md"), pending_acceptance_path.with_suffix(".md")),
        ):
            if final.exists():
                try:
                    os.replace(final, pending)
                except OSError:
                    pass

    try:
        def materialize_inputs() -> tuple[Path, Path, Path | None, Path | None]:
            taskdoc_copy = _copy_input(taskdoc_path, inputs / "taskdoc.md", "task document")
            original_design = require_plain_file(design_path, "ATK design file")
            design_suffix = original_design.suffix or ".yaml"
            design_copy = _copy_input(
                original_design,
                inputs / f"design{design_suffix}",
                "ATK design file",
            )
            generator_copy = (
                _copy_input(generator_path, inputs / "case_generator.py", "ATK generator plugin")
                if generator_path else None
            )
            plugin_copy = (
                _copy_input(execution_plugin, inputs / "execution_plugin.py", "ATK execution plugin")
                if execution_plugin else None
            )
            return taskdoc_copy, design_copy, generator_copy, plugin_copy

        taskdoc, design, generator, plugin = timed("materialize_inputs", materialize_inputs)

        facts_path = receipts / "source_facts.json"
        facts = timed(
            "source_facts",
            lambda: create_source_facts(
                spec=checked,
                taskdoc_path=taskdoc,
                source_root=origin,
                target_soc=target_soc,
                out_path=facts_path,
            ),
        )
        source_anchor = facts["source"]["content_anchor"]
        staged_build_anchor = facts["source"]["build_input_anchor"]
        if facts["status"] == "UNSUPPORTED":
            remaining()
            acceptance = timed(
                "verdict",
                lambda: finalize(spec=checked, facts_path=facts_path, out_path=pending_acceptance_path),
            )
        else:
            staged = session / "staging" / "source"
            timed("stage_source", lambda: _stage_source(origin, staged))
            if content_anchor(staged, checked["operator"]["source_subdir"]) != source_anchor:
                raise WorkflowError("SOURCE_DRIFT", "staged DUT source differs from caller source")
            if build_input_anchor(staged) != staged_build_anchor:
                raise WorkflowError("SOURCE_DRIFT", "staged build inputs differ from caller source")
            remaining()
            case_path = receipts / "cases.json"
            timed(
                "atk_casegen",
                lambda: generate_cases(
                    spec=checked,
                    atk_bin=atk_bin,
                    design_path=design,
                    generator_path=generator,
                    work_dir=session / "atk-casegen",
                    out_path=case_path,
                    timeout_seconds=stage_timeout(),
                ),
            )
            build_path = receipts / "build.json"
            timed(
                "build",
                lambda: build_operator(
                    spec=checked,
                    facts_path=facts_path,
                    source_root=staged,
                    install_root=session / "install",
                    target_soc=target_soc,
                    out_path=build_path,
                    evidence_dir=session / "build-evidence",
                    timeout_seconds=stage_timeout(),
                ),
            )
            execution_path = receipts / "execution.json"
            timed(
                "atk_execution",
                lambda: run_cases(
                    spec=checked,
                    atk_bin=atk_bin,
                    case_receipt_path=case_path,
                    build_receipt_path=build_path,
                    execution_plugin=plugin,
                    work_dir=session / "atk-execution",
                    out_path=execution_path,
                    timeout_seconds=stage_timeout(),
                ),
            )
            remaining()
            acceptance = timed(
                "verdict",
                lambda: finalize(
                    spec=checked,
                    facts_path=facts_path,
                    build_receipt_path=build_path,
                    case_receipt_path=case_path,
                    execution_receipt_path=execution_path,
                    out_path=pending_acceptance_path,
                ),
            )
        verdict_status = acceptance.get("verdict", {}).get("status")
        if verdict_status not in _FORMAL_VERDICTS:
            raise WorkflowError(
                "NON_DUT_EXECUTION_FAILURE",
                f"deterministic result {verdict_status!r} is not a formal acceptance verdict",
            )
        current_stage = "publish_acceptance"
        remaining()
        pending_markdown = pending_acceptance_path.with_suffix(".md")
        acceptance_markdown = acceptance_path.with_suffix(".md")
        os.replace(pending_markdown, acceptance_markdown)
        try:
            os.replace(pending_acceptance_path, acceptance_path)
        except OSError:
            os.replace(acceptance_markdown, pending_markdown)
            raise
        acceptance_published = True
        workflow = {
            "schema": "oprunway.workflow_receipt",
            "schema_version": 1,
            "status": "COMPLETE",
            "started_at": started_at,
            "finished_at": utc_now(),
            "active_elapsed_seconds": round(time.monotonic() - started, 6),
            "active_budget_seconds": budget,
            "spec_sha256": spec_digest(checked),
            "inputs": {
                "taskdoc_sha256": sha256_file(taskdoc),
                "source_content_anchor": source_anchor,
                "design_sha256": sha256_file(design),
                "generator_sha256": sha256_file(generator) if generator else None,
                "execution_plugin_sha256": sha256_file(plugin) if plugin else None,
            },
            "timings_seconds": timings,
            "acceptance": {
                "path": str(acceptance_path),
                "sha256": sha256_file(acceptance_path),
                "status": acceptance["verdict"]["status"],
            },
        }
        atomic_write_json(workflow_path, workflow)
        return {"acceptance": acceptance, "workflow": workflow}
    except WorkflowError as exc:
        rollback_acceptance()
        atomic_write_json(
            workflow_path,
            {
                "schema": "oprunway.workflow_receipt",
                "schema_version": 1,
                "status": "ERROR",
                "started_at": started_at,
                "finished_at": utc_now(),
                "active_elapsed_seconds": round(time.monotonic() - started, 6),
                "active_budget_seconds": budget,
                "spec_sha256": spec_digest(checked),
                "failed_stage": current_stage,
                "error": {"code": exc.code, "message": str(exc)},
                "timings_seconds": timings,
            },
        )
        raise
    except Exception as exc:
        rollback_acceptance()
        wrapped = WorkflowError("UNEXPECTED_PLUGIN_ERROR", f"{type(exc).__name__}: {exc}")
        atomic_write_json(
            workflow_path,
            {
                "schema": "oprunway.workflow_receipt",
                "schema_version": 1,
                "status": "ERROR",
                "started_at": started_at,
                "finished_at": utc_now(),
                "active_elapsed_seconds": round(time.monotonic() - started, 6),
                "active_budget_seconds": budget,
                "spec_sha256": spec_digest(checked),
                "failed_stage": current_stage,
                "error": {"code": wrapped.code, "message": str(wrapped)},
                "timings_seconds": timings,
            },
        )
        raise wrapped from exc
