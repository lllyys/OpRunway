"""faq_writer: append known_fixes.json + render FAQ.md; history on key collision."""
import json
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import faq_writer
import paths
from pathlib import Path


def _entry(fix_kind="env", payload=None):
    return {
        "fix_kind": fix_kind,
        "fix_payload": payload or {"ASCEND_GLOBAL_LOG_LEVEL": "1"},
        "source_issue_url": "https://github.com/x/y/issues/1",
        "verified_phase": "phase1",
        "soc": "ascend910b",
    }


def test_first_write_creates_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    faq_writer.upsert(
        repo="ops-transformer", op="grouped_matmul",
        failure_type="BUILD_FAIL", error_signature="abc123def456",
        **_entry(),
    )
    assert Path(paths.FAQ_JSON).exists()
    assert Path(paths.FAQ_MD).exists()
    data = json.loads(Path(paths.FAQ_JSON).read_text(encoding="utf-8"))
    key = "ops-transformer::grouped_matmul::BUILD_FAIL::abc123def456"
    assert key in data
    assert data[key]["fix_kind"] == "env"
    assert "verified_at" in data[key]


def test_collision_pushes_old_to_history(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    args = dict(repo="r", op="o", failure_type="F", error_signature="sig1")
    faq_writer.upsert(**args, **_entry(payload={"X": "1"}))
    faq_writer.upsert(**args, **_entry(payload={"X": "2"}))
    data = json.loads(Path(paths.FAQ_JSON).read_text(encoding="utf-8"))
    entry = data["r::o::F::sig1"]
    assert entry["fix_payload"] == {"X": "2"}
    assert len(entry["history"]) == 1
    assert entry["history"][0]["fix_payload"] == {"X": "1"}


def test_lookup_returns_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    faq_writer.upsert(
        repo="r", op="o", failure_type="F", error_signature="sig",
        **_entry(),
    )
    found = faq_writer.lookup(repo="r", op="o", failure_type="F", error_signature="sig")
    assert found is not None
    assert found["fix_kind"] == "env"


def test_lookup_miss_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert faq_writer.lookup(repo="r", op="o", failure_type="F", error_signature="x") is None


def test_md_render_includes_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    faq_writer.upsert(
        repo="ops-cv", op="resize", failure_type="BUILD_FAIL",
        error_signature="zzz",
        **_entry(),
    )
    md = Path(paths.FAQ_MD).read_text(encoding="utf-8")
    assert "ops-cv" in md
    assert "resize" in md
    assert "BUILD_FAIL" in md
    assert "ASCEND_GLOBAL_LOG_LEVEL" in md
