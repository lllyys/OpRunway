"""cann-ops-run side FAQ lookup: read-only, returns matching fix or None.
Never raises; if FAQ file is missing or malformed, returns None."""
import json
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from pathlib import Path
import pytest
import faq_lookup


def _write_faq(tmp_path: Path, key: str, fix_kind: str, payload: dict) -> None:
    p = tmp_path / "cann-ops-report" / "faq" / "known_fixes.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        key: {
            "fix_kind": fix_kind,
            "fix_payload": payload,
            "source_issue_url": "https://x",
            "verified_at": "2026-05-21T10:00:00",
            "verified_phase": "phase1",
            "soc": "ascend910b",
            "history": [],
        }
    }), encoding="utf-8")


def test_lookup_hit(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_faq(tmp_path, "r::o::BUILD_FAIL::sig1", "env", {"K": "V"})
    hit = faq_lookup.lookup_from_log(
        repo="r", op="o", failure_type="BUILD_FAIL",
        log_path=tmp_path / "x.log",
        precomputed_signature="sig1",
    )
    assert hit is not None
    assert hit["fix_kind"] == "env"


def test_lookup_miss(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_faq(tmp_path, "r::o::BUILD_FAIL::sig1", "env", {"K": "V"})
    assert faq_lookup.lookup_from_log(
        repo="r", op="o", failure_type="BUILD_FAIL",
        log_path=tmp_path / "x.log",
        precomputed_signature="other",
    ) is None


def test_no_faq_file_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert faq_lookup.lookup_from_log(
        repo="r", op="o", failure_type="F",
        log_path=tmp_path / "x.log",
        precomputed_signature="x",
    ) is None


def test_malformed_faq_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "cann-ops-report" / "faq" / "known_fixes.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json", encoding="utf-8")
    assert faq_lookup.lookup_from_log(
        repo="r", op="o", failure_type="F",
        log_path=tmp_path / "x.log",
        precomputed_signature="x",
    ) is None


def test_signature_from_log_when_not_precomputed(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from _error_sig import signature, first_error_line
    log = tmp_path / "x.log"
    log.write_text("ERROR: foo bar\n", encoding="utf-8")
    sig = signature(first_error_line(log))
    _write_faq(tmp_path, f"r::o::F::{sig}", "env", {"K": "V"})
    hit = faq_lookup.lookup_from_log(
        repo="r", op="o", failure_type="F",
        log_path=log, precomputed_signature=None,
    )
    assert hit is not None


def test_filter_excludes_patch(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    sig = "abc"
    _write_faq(tmp_path, f"r::o::F::{sig}", "patch", {"diff_path": "x"})
    hit = faq_lookup.lookup_from_log(
        repo="r", op="o", failure_type="F",
        log_path=tmp_path / "x.log",
        precomputed_signature=sig,
    )
    assert hit is None  # patch is filtered out
