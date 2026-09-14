"""Paths must resolve relative to CWD at call time."""
from pathlib import Path
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import paths


def test_faq_paths_resolve_under_cwd(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert paths.FAQ_DIR == tmp_path / "cann-ops-report" / "faq"
    assert paths.FAQ_JSON == tmp_path / "cann-ops-report" / "faq" / "known_fixes.json"
    assert paths.FAQ_MD == tmp_path / "cann-ops-report" / "faq" / "FAQ.md"


def test_issues_subdirs(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert paths.COMMENTS_DIR == tmp_path / "cann-ops-report" / "issues" / "comments"
    assert paths.PLANS_DIR == tmp_path / "cann-ops-report" / "issues" / "plans"
    assert paths.REPLIES_DIR == tmp_path / "cann-ops-report" / "issues" / "replies"
    assert paths.PATCHES_DIR == tmp_path / "cann-ops-report" / "issues" / "patches"
