# -*- coding: utf-8 -*-
"""执行段 Python 侧的机械断言：编译命令、spec/result 协议、失败归类、三键读回。

覆盖清单 → 测试对照：

- 编译命令带齐四项依赖与 C++17 → test_compile_command_has_four_dependencies
- 交付头里没有的算子不许编 → test_compile_executor_rejects_op_absent_from_header
- spec 与 result 往返（含数值键强制转型） → test_spec_roundtrip、test_parse_result_types
- 退出码归类全表 → test_classify_exit_table
- 崩溃与超时归类 → test_classify_crash_signal、test_classify_timeout
- 复跑失配时 status 仍是 ok（确定性结论独立） → test_classify_rerun_mismatch_keeps_status_ok
- 三键读回：标量 info / 数组 info / 形状不符拦截 → test_read_three_keys_*
- 复跑子记录形态与 stream_check 同形 → test_rerun_record_shapes
"""
import pathlib
import sys

import numpy as np
import pytest

HARNESS = pathlib.Path(__file__).resolve().parent.parent
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import exec_case  # noqa: E402
import op_abi     # noqa: E402


def test_compile_command_has_four_dependencies(tmp_path):
    repo, ascend = tmp_path / "ops-solver", tmp_path / "ascend"
    cmd = exec_case.compile_command(repo, ascend, tmp_path / "bin",
                                    ["cmatinv_batched"])
    joined = " ".join(cmd)
    assert "-std=c++17" in cmd
    assert "-I" + str(repo / "include") in cmd                  # 头（被测）
    assert "-linux/include" in joined                           # 头（ACL）
    assert str(repo / "build" / "libops_solver.so") in cmd      # 库（被测）
    assert str(ascend / "lib64" / "libascendcl.so") in cmd      # 库（ACL）
    assert "-DHARNESS_OP_CMATINV_BATCHED=1" in cmd
    assert "-ldl" in cmd                                        # dladdr 取实际加载库
    assert str(exec_case.EXEC_SRC) in cmd


def test_compile_command_honours_build_dir(tmp_path):
    cmd = exec_case.compile_command(tmp_path / "r", tmp_path / "a",
                                    tmp_path / "bin", ["sgetri"],
                                    build_dir=tmp_path / "elsewhere")
    assert str(tmp_path / "elsewhere" / "libops_solver.so") in cmd


def test_compile_executor_rejects_op_absent_from_header(tmp_path):
    repo = tmp_path / "ops-solver"
    (repo / "include").mkdir(parents=True)
    (repo / "include" / "cann_ops_solver.h").write_text(
        "aclError aclsolverSgetri(void*, long, float*, long, int*);\n",
        encoding="utf-8")
    with pytest.raises(exec_case.ExecError) as exc:
        exec_case.compile_executor(repo, tmp_path / "ascend", tmp_path / "bin",
                                   ops=["cmatinv_batched"])
    assert "cmatinv_batched" in str(exc.value)
    assert "sgetri" in str(exc.value)


def test_compile_executor_missing_header(tmp_path):
    with pytest.raises(exec_case.ExecError):
        exec_case.compile_executor(tmp_path, tmp_path, tmp_path / "bin")


def test_spec_roundtrip(tmp_path):
    spec = {"op": "cmatinv_batched", "n": 8, "batch": 2, "in_b": None,
            "runs": 3, "out32_shape": "2,8,8"}
    text = exec_case.write_spec(tmp_path / "case.spec", spec)
    assert "in_b=\n" in text
    back = exec_case.parse_result(text)
    assert back["op"] == "cmatinv_batched"
    assert back["n"] == 8 and back["runs"] == 3
    assert back["in_b"] is None
    assert back["out32_shape"] == "2,8,8"


def test_parse_result_types():
    r = exec_case.parse_result(
        "status=ok\nexit=0\nrun1_ms=12.500\nrun1_ret=-1\ninfo_len=4\n"
        "run1_out32_fnv=0x00000000000000ff\ndetail=\n")
    assert r["status"] == "ok"
    assert r["exit"] == 0 and isinstance(r["exit"], int)
    assert r["run1_ms"] == pytest.approx(12.5)
    assert r["run1_ret"] == -1
    assert r["info_len"] == 4
    assert r["run1_out32_fnv"] == "0x00000000000000ff"   # 指纹留字符串
    assert r["detail"] is None


def test_parse_result_ignores_noise():
    assert exec_case.parse_result("# 注释\n\nno-equals-sign\nk=v\n") == {"k": "v"}


@pytest.mark.parametrize("code,kind", [
    (0, "ok"), (10, "spec_error"), (11, "io_error"), (12, "unsupported"),
    (13, "acl_error"), (14, "op_error"), (15, "rerun_mismatch"), (7, "unknown"),
])
def test_classify_exit_table(code, kind):
    got, status, _ = exec_case.classify(code, False, {"detail": "d"})
    assert got == kind
    # 只有 ok 与复跑失配算「算子跑完了」
    assert (status == "ok") == (kind in exec_case.EXECUTED_KINDS)


def test_classify_crash_signal():
    kind, status, detail = exec_case.classify(-11, False, {})
    assert kind == "crash"
    assert status == "crash:SIGSEGV"
    assert "SIGSEGV" in detail


def test_classify_timeout():
    kind, status, _ = exec_case.classify(None, True, {})
    assert (kind, status) == ("timeout", "timeout")


def test_classify_rerun_mismatch_keeps_status_ok():
    kind, status, detail = exec_case.classify(15, False, {"detail": ""})
    assert kind == "rerun_mismatch"
    assert status == "ok"            # 算子跑完了，status 不记执行失败
    assert "失配" in detail


def test_read_three_keys_scalar_info(tmp_path):
    out = np.arange(9, dtype=np.float32).reshape(3, 3)
    (tmp_path / "out").mkdir()
    out.tofile(tmp_path / "out" / "out32.bin")
    np.int32(5).tofile(tmp_path / "out" / "info.bin")
    spec = {"dtype": "float32", "out32_shape": "3,3", "info_kind": "scalar"}
    got, info = exec_case.read_three_keys(tmp_path, spec, {})
    assert np.array_equal(got, out)
    assert info == 5 and np.ndim(info) == 0


def test_read_three_keys_array_info(tmp_path):
    out = np.zeros((2, 2, 2), dtype=np.complex64)
    (tmp_path / "out").mkdir()
    out.tofile(tmp_path / "out" / "out32.bin")
    np.array([0, 3], dtype=np.int32).tofile(tmp_path / "out" / "info.bin")
    spec = {"dtype": "complex64", "out32_shape": "2,2,2", "info_kind": "array", "batch": 2}
    got, info = exec_case.read_three_keys(tmp_path, spec, {})
    assert got.shape == (2, 2, 2) and got.dtype == np.complex64
    assert info.dtype == np.int32 and info.tolist() == [0, 3]


def test_three_keys_read_back_on_failure_path(tmp_path, monkeypatch):
    """失败轮也写了第 1 轮输出，归类不是 ok 也要读回来进留证件。"""
    tests_dir = pathlib.Path(__file__).resolve().parent
    wrapper = tmp_path / "fake_exec"
    wrapper.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{tests_dir / "fake_exec.py"}" "$@"\n',
        encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setenv("FAKE_MODE", "op_error")
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = {"op": "sgetri", "abi": "host", "call_shape": "inplace_a",
            "dtype": "float32", "info_kind": "scalar", "device_id": 0,
            "n": 3, "batch": 1, "nrhs": 0, "lda": 3, "runs": 1,
            "out32_shape": "3,3"}
    rec = exec_case.run_case(wrapper, tmp_path / "case", spec,
                             {"in_a": np.arange(9, dtype=np.float32).reshape(3, 3)},
                             repo, tmp_path / "ascend", timeout=30)
    assert rec["kind"] == "op_error"
    assert rec["out32"] is not None and rec["out32"].shape == (3, 3)
    assert rec["info"] == 0
    assert rec["three_key_error"] is None


def test_read_three_keys_shape_mismatch_raises(tmp_path):
    (tmp_path / "out").mkdir()
    np.zeros(4, dtype=np.float32).tofile(tmp_path / "out" / "out32.bin")
    np.int32(0).tofile(tmp_path / "out" / "info.bin")
    spec = {"dtype": "float32", "out32_shape": "3,3", "info_kind": "scalar"}
    with pytest.raises(exec_case.ExecError) as exc:
        exec_case.read_three_keys(tmp_path, spec, {})
    assert "声明形状" in str(exc.value)


def test_rerun_record_shapes():
    assert exec_case.rerun_record({"runs": 1}) is None
    ok = exec_case.rerun_record({"runs": 3, "rerun_runs_completed": 3, "rerun_consistent": "1",
                                 "run1_out32_fnv": "0x1"})
    assert ok == {"runs": 3, "runs_completed": 3, "consistent": True, "first_diff": None,
                  "per_run_fnv": {"run1_out32_fnv": "0x1"}}
    bad = exec_case.rerun_record({"runs": 2, "rerun_consistent": "0",
                                  "rerun_first_diff_run": 2,
                                  "rerun_first_diff_key": "info"})
    assert bad["consistent"] is False
    assert bad["first_diff"]["run"] == 2
    assert bad["first_diff"]["key"] == "info"


def test_runtime_env_puts_build_dir_first(tmp_path, monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/pre-existing")
    env = exec_case._runtime_env(tmp_path / "repo", tmp_path / "ascend")
    parts = env["LD_LIBRARY_PATH"].split(":")
    assert parts[0] == str(tmp_path / "repo" / "build")
    assert parts[-1] == "/pre-existing"


def test_exit_kind_matches_cpp_table():
    """C++ 侧的退出码表是唯一真相，Python 侧不许漂移。"""
    src = exec_case.EXEC_SRC.read_text(encoding="utf-8") + (exec_case.HERE / "executor_io.hpp").read_text(encoding="utf-8")
    for code, kind in exec_case.EXIT_KIND.items():
        camel = "k" + "".join(p.capitalize() for p in kind.split("_"))
        assert f"constexpr int {camel} = {code};" in src, (kind, code)


def test_np_dtype_covers_abi_dtypes():
    assert set(exec_case.NP_DTYPE) == set(op_abi.DTYPES)
