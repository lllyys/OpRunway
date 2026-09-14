"""执行剖面与工程形态两条轴的纯逻辑测试。

**只测能用假目录跑完的部分。** 构建、装包、跑测都要真机，一条都不在这里测
（仓根 CLAUDE.md「开发流程」）。这里测的是两条轴的分流判据本身——判错时
不报错，只是结论错，真机上翻出来一次要几十分钟。
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_install  # noqa: E402
import run_atk  # noqa: E402


# ---- 轴 1：执行剖面 ------------------------------------------------------

def _facts(tmp_path, **fields):
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("backend,expect", [
    ("aclnn", "aclnn"),
    ("npu", "npu"),
])
def test_profile_by_backend(tmp_path, backend, expect):
    assert run_atk._profile(_facts(tmp_path, backend=backend))["atk_backend"] == expect


def test_profile_missing_field_defaults_to_aclnn(tmp_path):
    """缺 backend 按 aclnn 走：加剖面之前的用例包都没有这个字段。"""
    assert run_atk._profile(_facts(tmp_path))["atk_backend"] == "aclnn"


def test_profile_unreadable_defaults_to_aclnn(tmp_path):
    assert run_atk._profile(str(tmp_path / "nope.json"))["atk_backend"] == "aclnn"


def test_profile_unknown_backend_is_none(tmp_path):
    """认不出返回 None，调用方退 3。静默换剖面比报错糟得多。"""
    assert run_atk._profile(_facts(tmp_path, backend="cuda")) is None


@pytest.mark.parametrize("profile,expect", [
    ("aclnn", "aclnn"),
    ("npu", "npu"),
])
def test_node_command_under_test_backend(profile, expect):
    command = run_atk._node_command("accuracy", "cases.json", "golden", "0", None,
                                    profile=run_atk.PROFILES[profile])
    assert command[:4] == ["atk", "node", "--backend", expect]


def test_node_command_defaults_to_active_profile():
    """make_repro.py 不传 profile，拿的是 main() 设的那份。"""
    assert run_atk._ACTIVE_PROFILE is run_atk.PROFILES["aclnn"]
    command = run_atk._node_command("accuracy", "cases.json", "golden", "0", None)
    assert command[3] == "aclnn"


def _statistic(prefix):
    return [{"编号": "7",
             f"{prefix}_0_Device性能（us）": "12.5",
             "cpu_0_Device性能（us）": "40.0"}]


@pytest.mark.parametrize("profile,prefix", [("aclnn", "pyaclnn"), ("npu", "npu")])
def test_device_times_uses_node_prefix(profile, prefix):
    times = run_atk._device_times(_statistic(prefix), "cpu_0",
                                  run_atk.PROFILES[profile]["node_prefix"])
    assert times[7] == {"aclnn": 12.5, "cpu": 40.0}


def test_device_times_wrong_prefix_loses_under_test_column():
    """前缀对不上时被测那列被当成标杆——这就是写死前缀的静默失真。"""
    times = run_atk._device_times(_statistic("npu"), "cpu_0", "pyaclnn")
    assert "aclnn" not in times[7]


def test_profiles_have_the_same_field_set():
    """两个剖面的字段必须齐平，缺字段的表现是 KeyError 崩在跑测中途。"""
    keys = [set(p) for p in run_atk.PROFILES.values()]
    assert keys[0] == keys[1]


# ---- 轴 2：工程形态 ------------------------------------------------------

def _op_dir_opp(root):
    op = root / "ops-math" / "experimental" / "math" / "roll"
    (op / "op_kernel").mkdir(parents=True)
    (op / "op_host").mkdir()
    return op


def _op_dir_standalone(root):
    op = root / "ops-sparse" / "sparse" / "densetosparse"
    arch = op / "arch22"
    arch.mkdir(parents=True)
    (arch / "densetosparse_host.cpp").write_text("", encoding="utf-8")
    (arch / "densetosparse_kernel.cpp").write_text("", encoding="utf-8")
    return op


def test_is_op_dir_recognises_both_shapes(tmp_path):
    assert build_install._is_op_dir(_op_dir_opp(tmp_path))
    assert build_install._is_op_dir(_op_dir_standalone(tmp_path))


def test_is_op_dir_rejects_plain_directory(tmp_path):
    plain = tmp_path / "docs"
    plain.mkdir()
    assert not build_install._is_op_dir(plain)


def test_repo_root_tells_opp_vendor_from_standalone(tmp_path):
    op = _op_dir_opp(tmp_path)
    repo = tmp_path / "ops-math"
    (repo / "build.sh").write_text("", encoding="utf-8")
    (repo / "cmake").mkdir()
    (repo / "cmake" / "func.cmake").write_text("", encoding="utf-8")
    assert build_install._repo_root(op) == (repo, build_install.KIND_OPP_VENDOR)

    op2 = _op_dir_standalone(tmp_path)
    repo2 = tmp_path / "ops-sparse"
    (repo2 / "build.sh").write_text("", encoding="utf-8")
    (repo2 / "cmake").mkdir()
    (repo2 / "cmake" / "package.cmake").write_text("", encoding="utf-8")
    assert build_install._repo_root(op2) == (repo2, build_install.KIND_STANDALONE)


def test_repo_root_returns_pair_when_absent(tmp_path):
    op = _op_dir_opp(tmp_path)
    assert build_install._repo_root(op) == (None, None)


def test_find_op_dir_locates_standalone_op(tmp_path):
    _op_dir_standalone(tmp_path)
    found, problem = build_install._find_op_dir(tmp_path / "ops-sparse",
                                                "DenseToSparse")
    assert problem is None
    assert found.name == "densetosparse"


# ---- 模式与剖面的兼容性 --------------------------------------------------

def test_standalone_flag_gates_tiling_and_cxx():
    """A2.6 与 A3.5 共用 `standalone` 一个开关：两者都要 aclnn 两段式接口。

    分成两个字段的话，两处判据会各自漂——而漂了之后跑出来的是
    「install.json 里没有 vendor_dir」这种指向 A2 的报错，方向是错的。
    """
    assert run_atk.PROFILES["aclnn"]["standalone"] is True
    assert run_atk.PROFILES["npu"]["standalone"] is False


def test_npu_profile_declares_no_custom_opp():
    """npu 剖面没有 opp vendor 层，`--builtin-baseline` 那一档也就不成立。"""
    assert run_atk.PROFILES["npu"]["custom_opp"] is False
    assert run_atk.PROFILES["aclnn"]["custom_opp"] is True


def test_npu_profile_does_not_read_frozen_inputs():
    """`--input_data` 读的是张量字节，喂不进 attr 编码的用例。"""
    assert run_atk.PROFILES["npu"]["frozen_inputs"] is False
    assert run_atk.PROFILES["aclnn"]["frozen_inputs"] is True


# ---- 冒烟的分档轴 --------------------------------------------------------

ATTR_CASE = {"id": 1, "inputs": [
    {"name": "format_id", "type": "attr", "dtype": "int", "range_values": [3]},
    {"name": "nnz", "type": "attr", "dtype": "int", "range_values": [40]},
]}
TENSOR_CASE = {"id": 2, "inputs": [
    {"name": "self", "type": "tensor", "dtype": "float16", "shape": [4]}]}


def test_case_dtype_prefers_tensor(monkeypatch):
    monkeypatch.setattr(run_atk, "_GROUP_ATTR", "format_id")
    assert run_atk._case_dtype(TENSOR_CASE) == "float16"


def test_case_dtype_falls_back_to_group_attr(monkeypatch):
    """纯 attr 用例取不到 dtype，冒烟会塌成一条——按分档轴分才拦得住整类失败。"""
    monkeypatch.setattr(run_atk, "_GROUP_ATTR", "format_id")
    assert run_atk._case_dtype(ATTR_CASE) == "format_id=3"


def test_case_dtype_without_group_attr_is_unchanged(monkeypatch):
    """不设分档轴时行为与加它之前逐字相同。"""
    monkeypatch.setattr(run_atk, "_GROUP_ATTR", "")
    assert run_atk._case_dtype(ATTR_CASE) is None


# ---- aclnn 路的回归闸 ----------------------------------------------------
#
# **这一节钉的是「加 sparse 剖面之前的行为」，不是「现在的行为」。**
# 剖面是靠默认值让 aclnn 路不变的，而默认值是约定不是机制：
# 改 PROFILES 表、改 _profile 的兜底、给某个共享函数换个默认参数，
# 三种改法都能悄悄改掉 aclnn 路，而 sparse 那侧的测试全绿。
#
# 每条的期望值取自加剖面之前那一版的源码，改这些值之前先确认
# aclnn 链路真的要改。

def test_aclnn_command_is_unchanged():
    """加剖面之前 `_node_command` 拼出来的就是这一串。"""
    command = run_atk._node_command("accuracy", "cases.json", "golden", "0", None)
    assert command == ["atk", "node", "--backend", "aclnn", "--devices", "0",
                       "node", "--backend", "cpu", "--task", "accuracy_load",
                       "--output_path", "golden",
                       "task", "-c", "cases.json", "--task", "accuracy"]


def test_aclnn_performance_command_is_unchanged():
    command = run_atk._node_command("performance", "perf.json", "golden", "3", None)
    assert command[:6] == ["atk", "node", "--backend", "aclnn", "--devices", "3"]
    assert command[-1] == "performance_device"


def test_aclnn_defaults_hold_when_facts_says_nothing(tmp_path):
    """老用例包没有 backend 字段，每一项都必须落回 aclnn 那一档。"""
    profile = run_atk._profile(_facts(tmp_path))
    assert profile == run_atk.PROFILES["aclnn"]
    assert profile["atk_backend"] == "aclnn"
    assert profile["node_prefix"] == "pyaclnn"
    assert profile["custom_opp"] is True      # 仍然要 ATK_CUSTOM_OPP_PATH
    assert profile["symbol_check"] is True    # 仍然核 import aclnnXxx success
    assert profile["frozen_inputs"] is True   # 仍然要 inputs/
    assert profile["standalone"] is True      # 仍然跑 A2.6 与 A3.5
    assert profile["param_reject"] is True    # 仍然按 EZ1001 判接口层拒绝


def test_aclnn_smoke_axis_is_dtype(monkeypatch):
    """冒烟仍按 dtype 抽，分档轴不介入。"""
    monkeypatch.setattr(run_atk, "_GROUP_ATTR", "")
    case = {"id": 1, "inputs": [
        {"name": "self", "type": "tensor", "dtype": "int8", "shape": [4]}]}
    assert run_atk._case_dtype(case) == "int8"


def test_repo_root_callers_all_unpack_the_pair():
    """`_repo_root` 从返回 None 改成返回二元组，漏一个调用点就是运行时崩。"""
    import inspect
    src = inspect.getsource(build_install)
    calls = [l.strip() for l in src.splitlines() if "_repo_root(" in l and "def " not in l]
    assert calls, "找不到调用点，测试本身失效了"
    for line in calls:
        assert "," in line.split("=")[0], f"没有解包二元组：{line}"


# ---- torch 扩展：npu 剖面下 A2 要把它编出来 ------------------------------

import subprocess  # noqa: E402

import probe_env  # noqa: E402


def test_no_register_module_means_no_extension_step(tmp_path):
    # aclnn 剖面不给 --register-module，这一步整段不做，也不是错。
    assert build_install._torch_extension(tmp_path, "") == (None, "")


def test_project_without_a_matching_setup_is_not_an_error(tmp_path):
    (tmp_path / "setup.py").write_text("name='别的包'", encoding="utf-8")
    assert build_install._torch_extension(tmp_path, "ops_x_npu") == (None, "")


def test_extension_dir_comes_from_where_the_so_lands(tmp_path, monkeypatch):
    ext = tmp_path / "torch_extension"
    ext.mkdir()
    (ext / "setup.py").write_text('name="ops_x_npu"', encoding="utf-8")

    def fake_run(cmd, **kwargs):
        # `--inplace` 的落点随包布局走：这里故意落在仓根而不是 setup.py 边上。
        (tmp_path / "ops_x_npu.cpython-313.so").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(build_install.subprocess, "run", fake_run)
    got, err = build_install._torch_extension(tmp_path, "ops_x_npu")
    assert err == ""
    assert got == tmp_path


def test_failed_extension_build_stops_a2(tmp_path, monkeypatch):
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "setup.py").write_text('name="ops_x_npu"', encoding="utf-8")
    monkeypatch.setattr(build_install.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    assert build_install._torch_extension(tmp_path, "ops_x_npu") == (None, "failed")


def test_env_sh_carries_the_extension_path(tmp_path):
    # ATK 的 worker 是另起的进程，少了这行探针报的是「实现没装进来」。
    path = tmp_path / "env.sh"
    probe_env.write_env_sh(path, "", sys.executable, str(tmp_path),
                           standalone_lib=str(tmp_path), pythonpath="/opt/ext")
    assert 'export PYTHONPATH="/opt/ext:$PYTHONPATH"' in path.read_text(encoding="utf-8")


def test_env_sh_without_extension_has_no_pythonpath_line(tmp_path):
    path = tmp_path / "env.sh"
    probe_env.write_env_sh(path, "", sys.executable, str(tmp_path),
                           standalone_lib=str(tmp_path))
    assert "PYTHONPATH" not in path.read_text(encoding="utf-8")


# ---- 公用机器：auto 不许占满空闲卡 --------------------------------------


def test_auto_is_capped_so_one_acceptance_does_not_eat_the_machine(tmp_path):
    got, note = probe_env.cap_devices([str(i) for i in range(15)])
    assert got == ["0", "1", "2", "3"]
    assert "上限" in note and "--max-devices 0" in note


def test_fewer_free_cards_than_the_cap_are_all_used(tmp_path):
    got, note = probe_env.cap_devices(["2", "5"])
    assert got == ["2", "5"] and note == ""


def test_zero_means_no_limit_for_an_exclusive_machine():
    got, note = probe_env.cap_devices([str(i) for i in range(15)], 0)
    assert len(got) == 15 and note == ""


def test_an_explicit_limit_wins_over_the_default():
    got, _ = probe_env.cap_devices(["0", "1", "2", "3", "4", "5"], 2)
    assert got == ["0", "1"]


def test_explicit_device_list_is_never_capped():
    # 显式列卡号是人做的决定，量具不改它——共卡也一样，那是 --allow-shared-device 的事。
    import run_kit_perf
    assert run_kit_perf.pick_devices("0,1,2,3,4,5,6,7", 50) == list("01234567")
