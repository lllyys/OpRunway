# -*- coding: utf-8 -*-
"""S1 构建段的机械断言：钉死命令、四条纪律、产物与取证。

覆盖清单 → 测试对照：

- 命令逐字固定 → test_build_command_is_pinned
- `--soc` 必须显式 → test_build_command_requires_soc
- 禁用 `--run` / `--pkg` 并说清原因 → test_build_command_rejects_forbidden_flags
- 不是仓根就拒绝 → test_build_rejects_non_repo_root
- 产物缺失当失败，缺哪个说出来 → test_collect_artifacts_missing、test_build_fails_on_missing_artifact
- SOC 回显解析（判定目标 SOC 真生效，不是落回缺省） → test_soc_echo_parsing
- 源码身份：有 git 取 commit，无 git 取内容摘要且内容一变就变
  → test_source_identity_without_git、test_content_anchor_changes_with_content
- skip-build 只取证不重建 → test_skip_build_collects_without_running
"""
import pathlib
import sys

import pytest

HARNESS = pathlib.Path(__file__).resolve().parent.parent
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import build_dut  # noqa: E402


def _fake_repo(tmp_path, with_artifacts=True, ops=("cmatinv_batched",)):
    repo = tmp_path / "ops-solver"
    (repo / "include").mkdir(parents=True)
    (repo / "src" / "cmatinv_batched").mkdir(parents=True)
    (repo / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    (repo / "build.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (repo / "include" / "cann_ops_solver.h").write_text("// head\n", encoding="utf-8")
    (repo / "include" / "cann_ops_solver_common.h").write_text("// c\n", encoding="utf-8")
    (repo / "src" / "cmatinv_batched" / "host.cpp").write_text("int a;\n",
                                                               encoding="utf-8")
    if with_artifacts:
        (repo / "build").mkdir()
        (repo / "build" / "libops_solver.so").write_bytes(b"\x7fELF-fake")
        (repo / "build_out" / "lib64").mkdir(parents=True)
        (repo / "build_out" / "include").mkdir(parents=True)
        (repo / "build_out" / "lib64" / "libops_solver.so").write_bytes(b"\x7fELF-fake")
        for h in ("cann_ops_solver.h", "cann_ops_solver_common.h"):
            (repo / "build_out" / "include" / h).write_text("// installed\n",
                                                            encoding="utf-8")
        for op in ops:
            d = repo / "build" / "test" / op
            d.mkdir(parents=True)
            (d / f"{op}_test").write_bytes(b"bin")
    return repo


def test_build_command_is_pinned():
    assert build_dut.build_command(["spotrf", "spotrs"], "ascend950") == [
        "bash", "build.sh", "--ops=spotrf,spotrs", "--soc=ascend950"]


def test_build_command_requires_soc():
    with pytest.raises(build_dut.BuildError) as exc:
        build_dut.build_command(["spotrf"], None)
    assert "ascend910b" in str(exc.value)      # 说清缺省是什么，不只说「必填」
    with pytest.raises(build_dut.BuildError):
        build_dut.build_command([], "ascend950")


@pytest.mark.parametrize("flag,why_fragment", [
    ("--run", "证据"),
    ("--pkg", "联网"),
])
def test_build_command_rejects_forbidden_flags(flag, why_fragment):
    with pytest.raises(build_dut.BuildError) as exc:
        build_dut.build_command(["spotrf " + flag], "ascend950")
    assert flag in str(exc.value) and why_fragment in str(exc.value)


def test_forbidden_flags_table_is_closed():
    assert set(build_dut.FORBIDDEN_FLAGS) == {"--run", "--pkg"}


def test_build_rejects_non_repo_root(tmp_path):
    with pytest.raises(build_dut.BuildError) as exc:
        build_dut.build(tmp_path, ["spotrf"], "ascend950")
    assert "仓根" in str(exc.value)


def test_collect_artifacts_missing(tmp_path):
    repo = _fake_repo(tmp_path, with_artifacts=False)
    found, missing = build_dut.collect_artifacts(repo)
    assert found == {}
    assert missing == list(build_dut.REQUIRED_ARTIFACTS)
    repo2 = _fake_repo(tmp_path / "ok")
    found2, missing2 = build_dut.collect_artifacts(repo2)
    assert missing2 == []
    assert set(found2) == set(build_dut.REQUIRED_ARTIFACTS)
    assert all(len(v["sha256"]) == 64 for v in found2.values())


def test_build_fails_on_missing_artifact(tmp_path):
    repo = _fake_repo(tmp_path, with_artifacts=False)
    with pytest.raises(build_dut.BuildError) as exc:
        build_dut.build(repo, ["cmatinv_batched"], "ascend910_93", skip_build=True)
    assert "产物缺失" in str(exc.value)
    assert "build/libops_solver.so" in str(exc.value)


@pytest.mark.parametrize("line,expect", [
    ("-- SOC_VERSION=ascend910_93, NPU_ARCH=dav-2201",
     {"soc_version": "ascend910_93", "npu_arch": "dav-2201"}),
    ("-- SOC_VERSION=ascend950, NPU_ARCH=dav-3510",
     {"soc_version": "ascend950", "npu_arch": "dav-3510"}),
    ("no echo here", None),
])
def test_soc_echo_parsing(line, expect):
    assert build_dut.soc_echo("前缀\n" + line + "\n后缀") == expect


def test_source_identity_without_git(tmp_path):
    repo = _fake_repo(tmp_path)
    ident = build_dut.source_identity(repo)
    assert ident["git"] is None
    assert len(ident["content_anchor"]) == 64
    assert ident["anchored_files"] == 5        # 两个头 + 一个 src 文件


def test_content_anchor_changes_with_content(tmp_path):
    repo = _fake_repo(tmp_path)
    before = build_dut.source_identity(repo)["content_anchor"]
    (repo / "src" / "cmatinv_batched" / "host.cpp").write_text("int b;\n",
                                                               encoding="utf-8")
    assert build_dut.source_identity(repo)["content_anchor"] != before


def test_skip_build_collects_without_running(tmp_path):
    repo = _fake_repo(tmp_path)
    prov = build_dut.build(repo, ["cmatinv_batched"], "ascend910_93",
                           skip_build=True)
    assert prov["build"]["skipped"] is True
    assert prov["build"]["cmd"] == "bash build.sh --ops=cmatinv_batched --soc=ascend910_93"
    assert prov["missing"] == []
    assert prov["ops"] == ["cmatinv_batched"]
    assert prov["soc"] == "ascend910_93"
    assert prov["test_binaries"]["cmatinv_batched"]["path"] == \
        "build/test/cmatinv_batched/cmatinv_batched_test"
    assert len(prov["header_text_sha256"]) == 64
    assert prov["env"]["python"] == sys.version.split()[0]
    assert prov["env"]["device_id"] == 0


def test_test_binary_absent_records_none(tmp_path):
    repo = _fake_repo(tmp_path, ops=())
    assert build_dut.collect_test_binaries(repo, ["cmatinv_batched"]) == \
        {"cmatinv_batched": None}
