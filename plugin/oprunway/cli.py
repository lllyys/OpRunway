"""Single command-line entry for the minimal OpRunway workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .contract import spec_digest, validate_spec
from .util import WorkflowError, atomic_write_json, load_json, utc_now
from .workflow import run_acceptance


def _spec(path: str) -> dict[str, Any]:
    return validate_spec(load_json(path))


def _physical_device(value: str) -> int:
    try:
        device = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("physical device must be an integer in [0, 255]") from exc
    if not 0 <= device <= 255:
        raise argparse.ArgumentTypeError("physical device must be an integer in [0, 255]")
    return device


def _attempt(args: argparse.Namespace, exc: WorkflowError, spec_sha256: str | None) -> None:
    out = getattr(args, "out", None)
    if not out and getattr(args, "session_dir", None):
        session = Path(args.session_dir)
        # Never create or modify an existing directory that was rejected as
        # dirty. A workflow receipt proves this invocation created the session.
        if (session / "receipts" / "workflow.json").is_file():
            out = str(session / "reports" / "attempt.json")
    if not out:
        return
    if exc.code.startswith("INVALID_") or exc.code in {
        "TASKDOC_DRIFT",
        "TASK_CASE_BUNDLE_MISMATCH",
        "TASK_CASE_BUNDLE_DRIFT",
        "SESSION_PATH_UNSUPPORTED",
    }:
        status = "NEEDS_INPUT"
    elif exc.code == "UNSUPPORTED_TARGET":
        status = "UNSUPPORTED"
    elif exc.code in {
        "ATK_NOT_READY",
        "ATK_VERSION_MISMATCH",
        "COMMAND_START_FAILED",
        "OUTPUT_WRITE_FAILED",
        "WORKFLOW_TIMEOUT",
        "ATK_EXECUTION_TIMEOUT",
        "BUILD_TIMEOUT",
        "COMMAND_ISOLATION_UNAVAILABLE",
        "COMMAND_TERMINATION_FAILED",
    }:
        status = "BLOCKED"
    else:
        status = "PLUGIN_ERROR"
    atomic_write_json(
        out,
        {
            "schema": "oprunway.attempt",
            "schema_version": 1,
            "created_at": utc_now(),
            "stage": args.command,
            "status": status,
            "error": {"code": exc.code, "message": str(exc)},
            "spec_sha256": spec_sha256,
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oprunway", description="Minimal ATK-backed NPU acceptance")
    commands = parser.add_subparsers(dest="command", required=True)

    accept = commands.add_parser("accept")
    accept.add_argument("--spec", required=True)
    accept.add_argument("--taskdoc", required=True)
    accept.add_argument("--source-root", required=True)
    accept.add_argument("--design", required=True)
    accept.add_argument("--generator")
    accept.add_argument("--execution-plugin")
    accept.add_argument("--task-cases-root")
    accept.add_argument("--atk-bin", default="atk")
    accept.add_argument("--target-soc", required=True)
    accept.add_argument("--session-dir", required=True)
    accept.add_argument("--physical-device", required=True, type=_physical_device)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    spec_sha256 = None
    try:
        spec = _spec(args.spec)
        spec_sha256 = spec_digest(spec)
        result = run_acceptance(
            spec=spec,
            taskdoc_path=args.taskdoc,
            source_root=args.source_root,
            design_path=args.design,
            generator_path=args.generator,
            execution_plugin=args.execution_plugin,
            task_cases_root=args.task_cases_root,
            atk_bin=args.atk_bin,
            target_soc=args.target_soc,
            session_dir=args.session_dir,
            physical_device=args.physical_device,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        status = result["acceptance"]["verdict"]["status"]
        return 0 if status == "PASS" else 1 if status == "DUT_FAIL" else 3
    except WorkflowError as exc:
        try:
            _attempt(args, exc, spec_sha256)
        except WorkflowError as receipt_error:
            print(f"[attempt:{receipt_error.code}] {receipt_error}", file=sys.stderr)
        print(f"[{args.command}:{exc.code}] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
