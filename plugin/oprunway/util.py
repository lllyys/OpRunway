"""Small, dependency-free helpers shared by the acceptance engine."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import secrets
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


_COMMAND_TOKEN_ENV = "OPRUNWAY_COMMAND_TOKEN"


class WorkflowError(RuntimeError):
    """A typed workflow failure that must never become a DUT verdict."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise WorkflowError("INVALID_JSON_VALUE", f"value is not canonical JSON: {exc}") from exc
    return rendered.encode("utf-8")


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: os.PathLike[str] | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_plain_file(path: os.PathLike[str] | str, label: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise WorkflowError("INVALID_INPUT", f"{label} must be a regular non-symlink file: {candidate}")
    return candidate.resolve()


def require_directory(path: os.PathLike[str] | str, label: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_dir():
        raise WorkflowError("INVALID_INPUT", f"{label} must be a real directory: {candidate}")
    return candidate.resolve()


def load_json(path: os.PathLike[str] | str) -> Any:
    source = require_plain_file(path, "JSON input")
    try:
        with source.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError("INVALID_JSON", f"cannot read JSON {source}: {exc}") from exc


def atomic_write_json(path: os.PathLike[str] | str, value: Any) -> Path:
    target = Path(path)
    temporary: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise WorkflowError("UNSAFE_OUTPUT", f"refuse symlink output: {target}")
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_bytes(value))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except WorkflowError:
        raise
    except OSError as exc:
        raise WorkflowError("OUTPUT_WRITE_FAILED", f"cannot write JSON output {target}: {exc}") from exc
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return target.resolve()


@dataclass(frozen=True)
class CommandReceipt:
    argv: list[str]
    cwd: str
    started_at: str
    elapsed_seconds: float
    returncode: int | None
    timed_out: bool
    stdout_path: str
    stderr_path: str
    stdout_sha256: str
    stderr_sha256: str
    processes_drained: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _process_has_command_token(pid: int, token: str) -> bool:
    marker = f"{_COMMAND_TOKEN_ENV}={token}".encode("ascii")
    try:
        environment = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except (FileNotFoundError, ProcessLookupError):
        return False
    except PermissionError as exc:
        raise WorkflowError(
            "COMMAND_ISOLATION_UNAVAILABLE", f"cannot prove command ownership for process {pid}"
        ) from exc
    except OSError as exc:
        raise WorkflowError(
            "COMMAND_TERMINATION_FAILED", f"cannot inspect process {pid}: {exc}"
        ) from exc
    return marker in environment


def _command_token_processes(token: str) -> list[int]:
    """Return live Linux processes descended from a tokenized command."""
    proc = Path("/proc")
    if not proc.is_dir():
        raise WorkflowError(
            "COMMAND_ISOLATION_UNAVAILABLE", "tokenized command isolation requires Linux /proc"
        )
    matches: list[int] = []
    try:
        entries = list(proc.iterdir())
    except OSError as exc:
        raise WorkflowError("COMMAND_TERMINATION_FAILED", f"cannot inspect /proc: {exc}") from exc
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid <= 1 or pid == os.getpid():
            continue
        if os.geteuid() != 0:
            try:
                if entry.stat().st_uid != os.geteuid():
                    continue
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise WorkflowError(
                    "COMMAND_TERMINATION_FAILED", f"cannot identify process {pid}: {exc}"
                ) from exc
        if _process_has_command_token(pid, token):
            matches.append(pid)
    return sorted(matches)


def _require_command_isolation(token: str) -> None:
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise WorkflowError(
            "COMMAND_ISOLATION_UNAVAILABLE", "command isolation requires Linux pidfd support"
        )
    pidfd: int | None = None
    try:
        pidfd = os.pidfd_open(os.getpid())
        signal.pidfd_send_signal(pidfd, 0)
    except OSError as exc:
        raise WorkflowError(
            "COMMAND_ISOLATION_UNAVAILABLE", f"Linux pidfd isolation is unavailable: {exc}"
        ) from exc
    finally:
        if pidfd is not None:
            os.close(pidfd)
    _command_token_processes(token)


def _signal_command_processes(
    pids: Sequence[int], token: str, signum: signal.Signals
) -> None:
    for pid in pids:
        try:
            pidfd = os.pidfd_open(pid)
        except ProcessLookupError:
            continue
        except OSError as exc:
            raise WorkflowError(
                "COMMAND_TERMINATION_FAILED", f"cannot bind process {pid} for cleanup: {exc}"
            ) from exc
        try:
            if _process_has_command_token(pid, token):
                signal.pidfd_send_signal(pidfd, signum)
        except ProcessLookupError:
            pass
        except OSError as exc:
            raise WorkflowError(
                "COMMAND_TERMINATION_FAILED", f"cannot signal process {pid}: {exc}"
            ) from exc
        finally:
            os.close(pidfd)


def _signal_process_group(pid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass
    except OSError as exc:
        raise WorkflowError(
            "COMMAND_TERMINATION_FAILED", f"cannot signal command process group {pid}: {exc}"
        ) from exc


def _drain_command_processes(token: str, *, immediate: bool) -> None:
    """Stop token-bearing workers even when they detached into a new process group."""
    started = time.monotonic()
    terminate_at = started if immediate else started + 1.0
    kill_at = terminate_at + 3.0
    deadline = started + 10.0
    while True:
        pids = _command_token_processes(token)
        if not pids:
            return
        now = time.monotonic()
        if now >= kill_at:
            _signal_command_processes(pids, token, signal.SIGKILL)
        elif now >= terminate_at:
            _signal_command_processes(pids, token, signal.SIGTERM)
        if now >= deadline:
            raise WorkflowError(
                "COMMAND_TERMINATION_FAILED",
                f"tokenized command left live processes after cleanup: {pids}",
            )
        time.sleep(0.1)


def run_command(
    argv: Sequence[str],
    *,
    cwd: os.PathLike[str] | str,
    timeout_seconds: int,
    stdout_path: os.PathLike[str] | str,
    stderr_path: os.PathLike[str] | str,
    env: Mapping[str, str] | None = None,
) -> CommandReceipt:
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise WorkflowError("INVALID_COMMAND", "argv must be a non-empty string list")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise WorkflowError("INVALID_TIMEOUT", "command timeout must be a positive integer")
    workdir = require_directory(cwd, "command cwd")
    stdout_file = Path(stdout_path)
    stderr_file = Path(stderr_path)
    stdout_file.parent.mkdir(parents=True, exist_ok=True)
    stderr_file.parent.mkdir(parents=True, exist_ok=True)
    if stdout_file.is_symlink() or stderr_file.is_symlink():
        raise WorkflowError("UNSAFE_OUTPUT", "command log must not be a symlink")

    started_at = utc_now()
    started = time.monotonic()
    timed_out = False
    returncode: int | None = None
    command_token = secrets.token_hex(16)
    child_env = os.environ.copy() if env is None else dict(env)
    child_env[_COMMAND_TOKEN_ENV] = command_token
    _require_command_isolation(command_token)
    processes_drained = False
    termination_error: WorkflowError | None = None
    with stdout_file.open("wb") as stdout, stderr_file.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=workdir,
                env=child_env,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        except OSError as exc:
            raise WorkflowError("COMMAND_START_FAILED", f"cannot start command {argv[0]}: {exc}") from exc
        try:
            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                _signal_process_group(process.pid, signal.SIGTERM)
                try:
                    _signal_command_processes(
                        _command_token_processes(command_token), command_token, signal.SIGTERM
                    )
                except WorkflowError as exc:
                    termination_error = exc
                try:
                    returncode = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    _signal_process_group(process.pid, signal.SIGKILL)
                    try:
                        _signal_command_processes(
                            _command_token_processes(command_token), command_token, signal.SIGKILL
                        )
                    except WorkflowError as exc:
                        if termination_error is None:
                            termination_error = exc
                    try:
                        returncode = process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        termination_error = termination_error or WorkflowError(
                            "COMMAND_TERMINATION_FAILED",
                            "timed-out command did not exit after SIGKILL",
                        )
            try:
                _drain_command_processes(command_token, immediate=timed_out)
                processes_drained = True
            except WorkflowError as exc:
                if termination_error is None:
                    termination_error = exc
        finally:
            if process.poll() is None:
                _signal_process_group(process.pid, signal.SIGKILL)
                try:
                    returncode = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    termination_error = termination_error or WorkflowError(
                        "COMMAND_TERMINATION_FAILED",
                        "command remained alive after final SIGKILL",
                    )
        if termination_error is not None:
            raise termination_error

    return CommandReceipt(
        argv=list(argv),
        cwd=str(workdir),
        started_at=started_at,
        elapsed_seconds=round(time.monotonic() - started, 6),
        returncode=returncode,
        timed_out=timed_out,
        stdout_path=str(stdout_file.resolve()),
        stderr_path=str(stderr_file.resolve()),
        stdout_sha256=sha256_file(stdout_file),
        stderr_sha256=sha256_file(stderr_file),
        processes_drained=processes_drained,
    )
