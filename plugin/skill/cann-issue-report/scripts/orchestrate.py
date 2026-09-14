"""Top-level glue: turn (repo_paths, soc, granularities) into draft files.

This module exposes a single function so the skill prompt can call it
in one shot once it has collected the user inputs (repos, soc, granularity).

Submission (Task 12) lives separately — orchestrate stops at draft generation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from . import dedup, draft_builder, env_info, failures
from .failures import FailureRecord


def generate_drafts(
    *,
    repo_paths: dict[str, Path],
    soc: str,
    granularities: Iterable[str],
    skip_resolved: bool = True,
) -> dict[str, dict]:
    """Generate drafts in selected granularities for each repo.

    Returns:
      {
        "<repo>": {
          "per_op_files": [Path, ...],
          "by_type_files": [Path, ...],
          "whole_repo_file": Path | None,
          "skipped_already_submitted": [(op, failure_type), ...],
        }
      }
    """
    granularities = set(granularities)
    grouped = failures.load_failures()
    out: dict[str, dict] = {}

    for repo, by_type in grouped.items():
        repo_path = repo_paths.get(repo)
        if repo_path is None:
            continue

        skipped: list[tuple[str, str]] = []
        if skip_resolved:
            filtered: dict[str, list[FailureRecord]] = {}
            for ft, recs in by_type.items():
                keep = []
                for r in recs:
                    if dedup.is_submitted(r.repo, r.op, r.failure_type):
                        skipped.append((r.op, r.failure_type))
                    else:
                        keep.append(r)
                if keep:
                    filtered[ft] = keep
            effective = filtered
        else:
            effective = by_type

        if not effective:
            out[repo] = {"per_op_files": [], "by_type_files": [],
                          "whole_repo_file": None,
                          "skipped_already_submitted": skipped}
            continue

        env = env_info.collect_env(repo_path=repo_path, soc=soc)
        per_op = (draft_builder.build_per_op(repo, effective, env=env, repo_path=repo_path)
                  if "per_op" in granularities else [])
        by_type_files = (draft_builder.build_by_type(repo, effective, env=env, repo_path=repo_path)
                          if "by_type" in granularities else [])
        whole_files = (draft_builder.build_whole_repo(repo, effective, env=env, repo_path=repo_path)
                       if "whole_repo" in granularities else [])
        out[repo] = {
            "per_op_files": per_op,
            "by_type_files": by_type_files,
            "whole_repo_file": whole_files[0] if whole_files else None,
            "skipped_already_submitted": skipped,
        }
    return out
