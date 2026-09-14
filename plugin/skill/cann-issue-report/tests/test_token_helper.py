from pathlib import Path

import pytest

from scripts import token_helper


def test_get_from_env_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITEE_TOKEN", "abc123")
    assert token_helper.get_from_env() == "abc123"


def test_get_from_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITEE_TOKEN", raising=False)
    assert token_helper.get_from_env() is None


def test_write_to_shell_creates_file_if_absent(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    token_helper.write_to_shell(rc, "tok123")
    content = rc.read_text(encoding="utf-8")
    assert "export GITEE_TOKEN=tok123" in content


def test_write_to_shell_appends(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("# existing\nalias foo=bar\n", encoding="utf-8")
    token_helper.write_to_shell(rc, "tok123")
    content = rc.read_text(encoding="utf-8")
    assert "# existing" in content
    assert "alias foo=bar" in content
    assert "export GITEE_TOKEN=tok123" in content


def test_detect_existing_line(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("export GITEE_TOKEN=old\n", encoding="utf-8")
    assert token_helper.has_existing_export(rc) is True


def test_detect_no_existing_line(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("alias x=y\n", encoding="utf-8")
    assert token_helper.has_existing_export(rc) is False


def test_env_var_for_platform() -> None:
    assert token_helper.env_var_for_platform("gitee") == "GITEE_TOKEN"
    assert token_helper.env_var_for_platform("gitcode") == "GITCODE_TOKEN"
    assert token_helper.env_var_for_platform("github") is None


def test_get_from_env_gitcode_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITCODE_TOKEN", "gc-tok-xyz")
    assert token_helper.get_from_env("GITCODE_TOKEN") == "gc-tok-xyz"


def test_write_to_shell_gitcode_token(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    token_helper.write_to_shell(rc, "gc-tok", env_var="GITCODE_TOKEN")
    content = rc.read_text(encoding="utf-8")
    assert "export GITCODE_TOKEN=gc-tok" in content


def test_overwrite_existing_gitcode_token(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("export GITCODE_TOKEN=old\n", encoding="utf-8")
    token_helper.overwrite_existing(rc, "new", env_var="GITCODE_TOKEN")
    content = rc.read_text(encoding="utf-8")
    assert "export GITCODE_TOKEN=old" not in content
    assert "export GITCODE_TOKEN=new" in content


def test_overwrite_existing_replaces_line(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("# c1\nexport GITEE_TOKEN=old\n# c2\n", encoding="utf-8")
    token_helper.overwrite_existing(rc, "new")
    content = rc.read_text(encoding="utf-8")
    assert "export GITEE_TOKEN=old" not in content
    assert "export GITEE_TOKEN=new" in content
    assert "# c1" in content
    assert "# c2" in content
