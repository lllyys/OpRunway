from pathlib import Path

from scripts import dedup


def test_make_key() -> None:
    assert dedup.make_key("ops-transformer", "grouped_matmul", "BUILD_FAIL") \
        == "ops-transformer::grouped_matmul::BUILD_FAIL"


def test_is_submitted_empty(tmp_cwd: Path) -> None:
    assert dedup.is_submitted("ops-transformer", "grouped_matmul", "BUILD_FAIL") is False


def test_mark_and_lookup(tmp_cwd: Path) -> None:
    dedup.mark_submitted(
        repo="ops-transformer",
        op="grouped_matmul",
        failure_type="BUILD_FAIL",
        issue_url="https://github.com/ascend/ops-transformer/issues/42",
        phase="phase1",
        submitted_via="api",
    )
    assert dedup.is_submitted("ops-transformer", "grouped_matmul", "BUILD_FAIL") is True

    record = dedup.get_record("ops-transformer", "grouped_matmul", "BUILD_FAIL")
    assert record["issue_url"] == "https://github.com/ascend/ops-transformer/issues/42"
    assert record["phase"] == "phase1"
    assert record["submitted_via"] == "api"
    assert "submitted_at" in record


def test_mark_overwrites_force(tmp_cwd: Path) -> None:
    dedup.mark_submitted(repo="r", op="o", failure_type="BUILD_FAIL",
                          issue_url="url1", phase="phase1", submitted_via="api")
    dedup.mark_submitted(repo="r", op="o", failure_type="BUILD_FAIL",
                          issue_url="url2", phase="phase1", submitted_via="api")
    assert dedup.get_record("r", "o", "BUILD_FAIL")["issue_url"] == "url2"


def test_mark_persists_soc_when_given(tmp_cwd: Path) -> None:
    dedup.mark_submitted(
        repo="ops-nn", op="qbmm", failure_type="BUILD_FAIL",
        issue_url="https://x/issues/1", phase="phase1", submitted_via="api",
        soc="ascend950",
    )
    rec = dedup.get_record("ops-nn", "qbmm", "BUILD_FAIL")
    assert rec["soc"] == "ascend950"


def test_mark_omits_soc_when_none(tmp_cwd: Path) -> None:
    dedup.mark_submitted(
        repo="ops-nn", op="qbmm", failure_type="BUILD_FAIL",
        issue_url="https://x/issues/1", phase="phase1", submitted_via="api",
    )
    rec = dedup.get_record("ops-nn", "qbmm", "BUILD_FAIL")
    assert "soc" not in rec


def test_split_new_vs_submitted(tmp_cwd: Path) -> None:
    dedup.mark_submitted(repo="r", op="o1", failure_type="BUILD_FAIL",
                          issue_url="url1", phase="phase1", submitted_via="api")
    keys = [("r", "o1", "BUILD_FAIL"), ("r", "o2", "BUILD_FAIL"), ("r", "o3", "TIMEOUT")]
    new, already = dedup.split_new_vs_submitted(keys)
    assert new == [("r", "o2", "BUILD_FAIL"), ("r", "o3", "TIMEOUT")]
    assert already == [("r", "o1", "BUILD_FAIL")]
