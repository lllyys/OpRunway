from pathlib import Path

import pytest

from scripts import dedup, mark_submitted


def test_mark_from_draft_path_per_op(tmp_cwd: Path) -> None:
    draft = (tmp_cwd / "cann-ops-report" / "issues" / "drafts"
             / "ops-transformer" / "per_op" / "grouped_matmul__BUILD_FAIL.md")
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("body")
    mark_submitted.mark_from_draft_path(
        draft_path=draft,
        issue_url="https://github.com/ascend/ops-transformer/issues/42",
        phase="phase1",
    )
    assert dedup.is_submitted("ops-transformer", "grouped_matmul", "BUILD_FAIL")


def test_mark_from_draft_path_by_type_requires_failures_list(tmp_cwd: Path) -> None:
    draft = (tmp_cwd / "cann-ops-report" / "issues" / "drafts"
             / "ops-cv" / "by_type" / "BUILD_FAIL.md")
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("body")
    mark_submitted.mark_from_draft_path(
        draft_path=draft,
        issue_url="https://gitee.com/ascend/ops-cv/issues/I0001",
        phase="phase1",
        ops=["resize_bilinear"],
    )
    assert dedup.is_submitted("ops-cv", "resize_bilinear", "BUILD_FAIL")


def test_mark_unrecognized_path_raises(tmp_cwd: Path) -> None:
    draft = tmp_cwd / "random" / "file.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("body")
    with pytest.raises(ValueError, match="Unrecognized draft path"):
        mark_submitted.mark_from_draft_path(
            draft_path=draft, issue_url="x", phase="phase1")
