import os
import subprocess
from pathlib import Path

import pytest

from scripts import env_info


def test_collect_includes_python_and_os(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASCEND_HOME_PATH", "")
    info = env_info.collect_env(repo_path=tmp_path, soc="ascend950")
    assert info["soc"] == "ascend950"
    assert "Python" in info["python_version"] or "3." in info["python_version"]
    assert info["os"]
    assert info["cann_version"] in {"unknown", "未知"} or info["cann_version"]


def test_collect_git_rev(fake_repo: Path) -> None:
    # add one commit so HEAD has a SHA
    subprocess.run(["git", "-C", str(fake_repo), "commit",
                    "--allow-empty", "-m", "init", "-q"], check=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    info = env_info.collect_env(repo_path=fake_repo, soc="ascend910b")
    assert len(info["git_rev"]) >= 7  # short or full SHA
    assert info["git_rev"] != "unknown"


def test_collect_cann_version_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Make a fake ASCEND_HOME_PATH with a version file
    fake_ascend = tmp_path / "ascend-toolkit" / "latest"
    fake_ascend.mkdir(parents=True)
    (fake_ascend / "version.info").write_text("Version=8.0.RC1.alpha001\n")
    monkeypatch.setenv("ASCEND_HOME_PATH", str(fake_ascend))
    info = env_info.collect_env(repo_path=tmp_path, soc="ascend950")
    assert info["cann_version"] == "8.0.RC1.alpha001"


def test_collect_handles_missing_git(tmp_path: Path) -> None:
    info = env_info.collect_env(repo_path=tmp_path / "nonexistent", soc="ascend950")
    assert info["git_rev"] == "unknown"
