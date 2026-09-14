"""Read cann-ops-run's run_state.json and emit a grouped failure dict.

Failure statuses (from cann-ops-run/scripts/state.py):
    BUILD_FAIL, INSTALL_FAIL, RUN_EXIT_FAIL, RUN_PATTERN_FAIL, TIMEOUT
Other statuses (PASS, PENDING, RUNNING, SKIPPED_*) are excluded.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import paths


FAILURE_STATUSES = {
    "BUILD_FAIL", "INSTALL_FAIL",
    "RUN_EXIT_FAIL", "RUN_PATTERN_FAIL", "TIMEOUT",
}


@dataclass(frozen=True)
class FailureRecord:
    repo: str
    op: str
    failure_type: str
    phase: str
    duration_s: float
    log_path: str
    attempts: int


def load_failures() -> dict[str, dict[str, list[FailureRecord]]]:
    """Return {repo: {failure_type: [FailureRecord, ...]}}.

    Aggregates every CWD/cann-ops-report/<repo>/test/run_state.json (per-repo
    layout). Raises FileNotFoundError with a user-friendly message if none exist.
    """
    repo_states = list(paths.iter_repo_states())
    if not repo_states:
        raise FileNotFoundError(
            f"{Path.cwd() / 'cann-ops-report'}/<repo>/test/ 下没有 run_state.json。\n"
            f"本 skill 的输入是跑测状态台账：先在这个 CWD 下跑一轮算子示例跑测"
            f"（cann-ops-run）生成它，或把已有的 cann-ops-report/ 放到这个 CWD 下。"
        )

    grouped: dict[str, dict[str, list[FailureRecord]]] = {}
    for repo, state_file in repo_states:
        repo_state = json.loads(state_file.read_text(encoding="utf-8"))
        for op, op_state in repo_state.get("ops", {}).items():
            for phase, phase_state in op_state.items():
                status = phase_state.get("status", "")
                if status not in FAILURE_STATUSES:
                    continue
                rec = FailureRecord(
                    repo=repo,
                    op=op,
                    failure_type=status,
                    phase=phase,
                    duration_s=float(phase_state.get("duration_s") or 0.0),
                    log_path=phase_state.get("log_path", ""),
                    attempts=int(phase_state.get("attempts") or 0),
                )
                grouped.setdefault(repo, {}).setdefault(status, []).append(rec)

    return grouped
