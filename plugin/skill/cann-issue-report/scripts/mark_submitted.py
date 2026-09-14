"""Post-hoc state registration when user clicks 'I submitted via prefilled URL'.

Draft path layout determines what (repo, op, failure_type) tuples to mark:
    drafts/<repo>/per_op/<op>__<failure_type>.md   → exactly one tuple
    drafts/<repo>/by_type/<failure_type>.md        → caller must supply ops=[...]
    drafts/<repo>/whole_repo.md                    → caller must supply ops_by_type={...}
"""
from __future__ import annotations

from pathlib import Path

from . import dedup


def mark_from_draft_path(
    *,
    draft_path: Path,
    issue_url: str,
    phase: str,
    ops: list[str] | None = None,
    ops_by_type: dict[str, list[str]] | None = None,
    submitted_via: str = "manual",
    soc: str | None = None,
    status: str = "submitted",
) -> int:
    """Return number of (repo, op, failure_type) tuples marked."""
    p = Path(draft_path).resolve()
    parts = p.parts
    # Find the "drafts" anchor
    if "drafts" not in parts:
        raise ValueError(f"Unrecognized draft path (no 'drafts' anchor): {draft_path}")
    drafts_idx = parts.index("drafts")
    # Expect: .../drafts/<repo>/<granularity>/<file>.md  OR  .../drafts/<repo>/whole_repo.md
    if len(parts) - drafts_idx < 3:
        raise ValueError(f"Unrecognized draft path layout: {draft_path}")
    repo = parts[drafts_idx + 1]
    rest = parts[drafts_idx + 2:]

    marked = 0
    if rest[-1] == "whole_repo.md":
        if not ops_by_type:
            raise ValueError("whole_repo draft requires ops_by_type={failure_type: [ops]}")
        for failure_type, oplist in ops_by_type.items():
            for op in oplist:
                dedup.mark_submitted(repo=repo, op=op, failure_type=failure_type,
                                       issue_url=issue_url, phase=phase,
                                       submitted_via=submitted_via, soc=soc,
                                       status=status)
                marked += 1
        return marked

    granularity = rest[0]
    filename = rest[-1]

    if granularity == "per_op":
        # filename = <op>__<failure_type>.md
        stem = filename[:-3] if filename.endswith(".md") else filename
        if "__" not in stem:
            raise ValueError(f"Unrecognized per_op filename: {filename}")
        op, failure_type = stem.rsplit("__", 1)
        dedup.mark_submitted(repo=repo, op=op, failure_type=failure_type,
                               issue_url=issue_url, phase=phase,
                               submitted_via=submitted_via, soc=soc)
        return 1

    if granularity == "by_type":
        # filename = <failure_type>.md
        failure_type = filename[:-3] if filename.endswith(".md") else filename
        if not ops:
            raise ValueError("by_type draft requires ops=[...]")
        for op in ops:
            dedup.mark_submitted(repo=repo, op=op, failure_type=failure_type,
                                   issue_url=issue_url, phase=phase,
                                   submitted_via=submitted_via, soc=soc)
            marked += 1
        return marked

    raise ValueError(f"Unrecognized draft path granularity: {granularity}")
