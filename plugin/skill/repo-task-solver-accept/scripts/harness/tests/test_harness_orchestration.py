# -*- coding: utf-8 -*-
"""编排层的机械断言：现场造输入 → 执行 → 判定 → 取证 → 独立重判的整条本地闭环。

执行段换成 `fake_exec.py` 替身（同一 spec/result 协议、同一退出码表），于是异常
三支路（崩溃、超时、复跑失配）在没有真机的地方也能验。

覆盖清单 → 测试对照：

- 输入构造复用 case-gen 的矩阵构造件，不另建生成器
  → test_probe_case_reuses_case_gen_constructors
- spec 形状口径（单矩阵 / 批量 / potrs 列数） → test_out32_shape_table、test_build_spec_fields
- 通路演练正例：结构性 PASS、不留证、大数组被弃
  → test_probe_structural_pass_discards_arrays
- 结构性不合格（NaN 输出）：FAIL + 自动留证 + 留证件可独立重判
  → test_probe_nan_output_fails_and_exports
- 崩溃隔离：子进程被信号杀，父进程照常收证据、留证件无 dut.npz
  → test_crash_is_isolated_and_exported
- 超时归类 → test_timeout_classified
- 算子返回非成功 → test_op_error_recorded
- 复跑失配：确定性结论独立于数值结论，失配轮输出进留证件
  → test_rerun_mismatch_is_independent_conclusion
- criteria 判定唯一实现：golden 回灌出数值 PASS、放大 2 倍出数值 FAIL
  → test_criteria_judge_pass_on_golden、test_criteria_judge_fail_on_scaled
- 独立重判复现原结论（不连真机） → test_rejudge_reproduces_fail
- 判定分流：Cholesky 走 criteria，现工程算子走结构性
  → test_judge_auto_routing
"""
import json
import pathlib
import sys

import numpy as np
import pytest

HARNESS = pathlib.Path(__file__).resolve().parent.parent
TESTS = pathlib.Path(__file__).resolve().parent
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import evidence      # noqa: E402
import exec_case     # noqa: E402
import op_abi        # noqa: E402
import run_harness   # noqa: E402

GEN_DIR = HARNESS.parent.parent.parent / "repo-task-solver-case-gen" / "scripts"


@pytest.fixture(scope="module")
def mods():
    return run_harness.load_modules(GEN_DIR)


@pytest.fixture
def fake(tmp_path):
    """fake_exec 的可执行包装：用当前解释器跑它，不依赖 PATH 上的 python3。"""
    wrapper = tmp_path / "fake_exec"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{TESTS / "fake_exec.py"}" "$@"\n',
                       encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def _ctx(tmp_path, fake, judge, rerun=1, keep=()):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return {"executor": str(fake), "work_dir": tmp_path / "work",
            "evidence_dir": tmp_path / "ev", "repo": str(repo),
            "ascend_home": str(tmp_path / "ascend"), "build_dir": None,
            "device_id": 0, "timeout": 20, "cwd": str(repo), "rerun": rerun,
            "jobs": 1, "layout": "row_major", "keep_ids": set(keep),
            "provenance": {"soc": "ascend910_93"}, "ratio_mean": None,
            "judge": judge}


# ---------------------------------------------------------------------------
# 输入构造与 spec
# ---------------------------------------------------------------------------

def test_probe_case_reuses_case_gen_constructors(mods):
    case = run_harness.probe_case(mods, "cmatinv_batched", 6, 3, 777)
    A64 = case["_arrays"]["A64"]
    assert A64.shape == (3, 6, 6) and A64.dtype == np.complex128
    assert case["_arrays"]["A32"].dtype == np.complex64
    # 与直接调 case-gen 的构造件逐位相同——确认没有另写一套分布
    rng = np.random.default_rng(777)
    want = np.stack([mods["gen"].fill_hpd_cu(rng, 6) for _ in range(3)])
    assert np.array_equal(A64, want)
    # HPD 即非奇异，是求逆族的合法输入
    assert np.all(np.linalg.eigvalsh(A64) > 0)


def test_probe_case_real_op_uses_spd(mods):
    case = run_harness.probe_case(mods, "sgetri", 5, 1, 11)
    assert case["_arrays"]["A64"].shape == (5, 5)      # 非批量不堆批维
    assert case["_arrays"]["A32"].dtype == np.float32
    assert case["batch"] == 1


@pytest.mark.parametrize("op,case,want", [
    ("cmatinv_batched", {"n": 8, "batch": 4}, (4, 8, 8)),
    ("sgetri", {"n": 8, "batch": 1}, (8, 8)),
    ("spotrf", {"n": 16}, (16, 16)),
    ("spotrs", {"n": 16, "nrhs": 3}, (16, 3)),
    ("spotrsBatched", {"n": 9, "batch": 5, "nrhs": 1}, (5, 9, 1)),
])
def test_out32_shape_table(op, case, want):
    assert run_harness.out32_shape(op_abi.get(op), case) == want


def test_build_spec_fields():
    spec = run_harness.build_spec(op_abi.get("cmatinv_batched"),
                                  {"n": 8, "batch": 2, "uplo": "U"}, 3, 1)
    assert spec["op"] == "cmatinv_batched"
    assert spec["abi"] == "host"
    assert spec["call_shape"] == "outofplace_a_ainv"
    assert spec["dtype"] == "complex64"
    assert spec["info_kind"] == "scalar"
    assert spec["out32_shape"] == "2,8,8"
    assert spec["runs"] == 3 and spec["device_id"] == 1
    assert spec["lda"] == 8
    assert spec["layout"] == "row_major"       # 双口径分流点显式落进 spec


def test_build_spec_column_major_layout():
    spec = run_harness.build_spec(op_abi.get("spotrf"), {"n": 4}, 1, 0,
                                  layout="column_major")
    assert spec["layout"] == "column_major"
    assert spec["abi"] == "device"             # 任务书口径随表带出


# ---------------------------------------------------------------------------
# 通路演练（结构性判定）
# ---------------------------------------------------------------------------

def test_probe_structural_pass_discards_arrays(mods, tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ok")
    case = run_harness.probe_case(mods, "cmatinv_batched", 4, 2, 5)
    inv = np.stack([np.linalg.inv(m) for m in case["_arrays"]["A64"]]).astype(np.complex64)
    out_bin = tmp_path / "golden.bin"
    np.ascontiguousarray(inv).tofile(out_bin)
    monkeypatch.setenv("FAKE_OUT", str(out_bin))

    rec = run_harness.run_one(mods, case, op_abi.get("cmatinv_batched"),
                              _ctx(tmp_path, fake, "structural"))
    assert rec["exec"]["kind"] == "ok"
    assert rec["verdict"]["structural"] == "PASS"
    assert rec["verdict"]["numeric"] == "NOT_JUDGED"      # 结构性不冒充数值判定
    assert rec["evidence"] is None                        # 通过即弃，不留证
    assert rec["discarded_arrays"] >= 2
    sanity = rec["verdict"]["sanity"]
    # HPD 输入是 Hermitian，conj(Aᵀ) == A，两个残差必然相等——布局判别不出，如实说
    assert sanity["layout_match"] == "indistinguishable"
    assert sanity["inv_residual_direct"] < 1e-3
    assert sanity["inv_residual_transpose_conj"] < 1e-3
    assert all(c["pass"] for c in rec["verdict"]["checks"])
    assert rec["exec"]["lib_path"] == "/fake/libops_solver.so"


def test_probe_nan_output_fails_and_exports(mods, tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ok")
    case = run_harness.probe_case(mods, "cmatinv_batched", 4, 2, 6)
    bad = np.full((2, 4, 4), np.nan, dtype=np.complex64)
    out_bin = tmp_path / "nan.bin"
    bad.tofile(out_bin)
    monkeypatch.setenv("FAKE_OUT", str(out_bin))

    rec = run_harness.run_one(mods, case, op_abi.get("cmatinv_batched"),
                              _ctx(tmp_path, fake, "structural"))
    assert rec["verdict"]["structural"] == "FAIL"
    assert any("out32_finite" in f for f in rec["verdict"]["failures"])
    assert rec["evidence"]["self_contained"] is True
    bundle = pathlib.Path(rec["evidence"]["bundle"])
    # 留证件可独立重判：不连真机、不重新生成输入
    again, code = run_harness.rejudge(bundle, GEN_DIR)
    assert again["numeric"] == "FAIL" and code == 1
    assert again["original"]["reason"] == rec["evidence"]["reason"]


def test_crash_is_isolated_and_exported(mods, tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    case = run_harness.probe_case(mods, "cmatinv_batched", 4, 2, 7)
    rec = run_harness.run_one(mods, case, op_abi.get("cmatinv_batched"),
                              _ctx(tmp_path, fake, "structural"))
    assert rec["exec"]["kind"] == "crash"
    assert rec["exec"]["status"] == "crash:SIGSEGV"
    assert rec["verdict"] is None                       # 没有输出就不判
    bundle = pathlib.Path(rec["evidence"]["bundle"])
    # 崩溃没有被测输出，dut.npz 本就不该有——自包含判据按执行归类放宽，不虚报缺失
    assert not (bundle / "dut.npz").exists()
    assert rec["evidence"]["self_contained"] is True
    assert rec["evidence"]["missing"] == []
    assert (bundle / "inputs.npz").is_file()           # 输入与日志照样留下
    assert (bundle / "exec.log").is_file()
    rec2, code = run_harness.rejudge(bundle, GEN_DIR)
    assert rec2["rejudge"] == "no_dut" and code == 1   # 重判如实说没有可判的输出


def test_timeout_classified(mods, tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "timeout")
    case = run_harness.probe_case(mods, "cmatinv_batched", 4, 1, 8)
    ctx = _ctx(tmp_path, fake, "structural")
    ctx["timeout"] = 2
    rec = run_harness.run_one(mods, case, op_abi.get("cmatinv_batched"), ctx)
    assert rec["exec"]["kind"] == "timeout"
    assert rec["exec"]["status"] == "timeout"
    assert rec["evidence"]["reason"] == "执行失败：归类 timeout"


def test_op_error_recorded(mods, tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "op_error")
    case = run_harness.probe_case(mods, "cmatinv_batched", 4, 1, 9)
    rec = run_harness.run_one(mods, case, op_abi.get("cmatinv_batched"),
                              _ctx(tmp_path, fake, "structural"))
    assert rec["exec"]["kind"] == "op_error"
    assert rec["exec"]["status"] == "op_error:exit=14"
    assert rec["exec"]["op_ret"] == -1
    assert rec["verdict"] is None                       # status 非 ok 就不判
    # 失败轮也写了第 1 轮输出，留证件必须拿到它——否则「输入+输出+日志」缺一角
    bundle = pathlib.Path(rec["evidence"]["bundle"])
    assert (bundle / "dut.npz").is_file()
    assert evidence.load_dut_npz(bundle / "dut.npz")["status"] == "op_error:exit=14"
    again, code = run_harness.rejudge(bundle, GEN_DIR)
    assert again["rejudge"] == "exec_failed" and code == 1
    assert again["status"] == "op_error:exit=14"        # 不说成数值 FAIL


def test_rerun_mismatch_is_independent_conclusion(mods, tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "rerun_mismatch")
    case = run_harness.probe_case(mods, "cmatinv_batched", 4, 2, 10)
    inv = np.stack([np.linalg.inv(m) for m in case["_arrays"]["A64"]]).astype(np.complex64)
    out_bin = tmp_path / "g.bin"
    np.ascontiguousarray(inv).tofile(out_bin)
    monkeypatch.setenv("FAKE_OUT", str(out_bin))

    rec = run_harness.run_one(mods, case, op_abi.get("cmatinv_batched"),
                              _ctx(tmp_path, fake, "structural", rerun=3))
    assert rec["exec"]["kind"] == "rerun_mismatch"
    assert rec["exec"]["status"] == "ok"                   # 算子跑完了
    assert rec["verdict"]["structural"] == "PASS"          # 数值面照常判
    assert rec["determinism"]["consistent"] is False       # 确定性结论独立
    assert rec["determinism"]["first_diff"]["key"] == "out32"
    assert rec["determinism"]["runs"] == 3
    bundle = pathlib.Path(rec["evidence"]["bundle"])
    assert (bundle / "out32.bin.diff.bin").is_file()       # 失配轮输出进留证件


def test_manual_keep_overrides_discard(mods, tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ok")
    case = run_harness.probe_case(mods, "cmatinv_batched", 4, 1, 12)
    inv = np.linalg.inv(case["_arrays"]["A64"][0]).astype(np.complex64)
    out_bin = tmp_path / "g.bin"
    np.ascontiguousarray(inv.reshape(1, 4, 4)).tofile(out_bin)
    monkeypatch.setenv("FAKE_OUT", str(out_bin))
    rec = run_harness.run_one(mods, case, op_abi.get("cmatinv_batched"),
                              _ctx(tmp_path, fake, "structural",
                                   keep=[case["case_id"]]))
    assert rec["verdict"]["structural"] == "PASS"
    assert rec["evidence"]["reason"] == "人工 --keep 指定"


# ---------------------------------------------------------------------------
# criteria 判定（唯一实现）
# ---------------------------------------------------------------------------

def _spotrf_case(n=16, seed=923001001):
    return {"case_id": f"spotrf-n{n}", "op": "spotrf", "source": "cu",
            "uplo": "L", "n": n, "lda": n, "seed": seed,
            "ratio_cpu": 1.0, "ratio_cpu_status": "ok", "ratio_cpu_mean": 1.0}


def test_criteria_judge_pass_on_golden(mods, tmp_path, fake, monkeypatch):
    pytest.importorskip("scipy")
    monkeypatch.setenv("FAKE_MODE", "ok")
    case = _spotrf_case()
    arrays = mods["gen"].build_case_arrays(case)
    out_bin = tmp_path / "golden.bin"
    np.ascontiguousarray(arrays["golden32"]).tofile(out_bin)
    monkeypatch.setenv("FAKE_OUT", str(out_bin))

    rec = run_harness.run_one(mods, dict(case), op_abi.get("spotrf"),
                              _ctx(tmp_path, fake, "criteria", rerun=2))
    assert rec["exec"]["kind"] == "ok"
    assert rec["verdict"]["numeric"] == "PASS"
    assert rec["verdict"]["formal"] == "PENDING_RULING"
    assert rec["verdict"]["residual"]["ran"] is True
    assert rec["determinism"]["consistent"] is True
    assert rec["evidence"] is None


def test_criteria_judge_fail_on_scaled(mods, tmp_path, fake, monkeypatch):
    pytest.importorskip("scipy")
    monkeypatch.setenv("FAKE_MODE", "ok")
    case = _spotrf_case()
    arrays = mods["gen"].build_case_arrays(case)
    out_bin = tmp_path / "scaled.bin"
    np.ascontiguousarray(arrays["golden32"] * np.float32(2.0)).tofile(out_bin)
    monkeypatch.setenv("FAKE_OUT", str(out_bin))

    rec = run_harness.run_one(mods, dict(case), op_abi.get("spotrf"),
                              _ctx(tmp_path, fake, "criteria"))
    assert rec["verdict"]["numeric"] == "FAIL"
    assert rec["evidence"]["self_contained"] is True
    assert rec["evidence"]["reason"] == "数值结论 FAIL"


def test_rejudge_reproduces_fail(mods, tmp_path, fake, monkeypatch):
    pytest.importorskip("scipy")
    monkeypatch.setenv("FAKE_MODE", "ok")
    case = _spotrf_case()
    arrays = mods["gen"].build_case_arrays(case)
    out_bin = tmp_path / "scaled.bin"
    np.ascontiguousarray(arrays["golden32"] * np.float32(2.0)).tofile(out_bin)
    monkeypatch.setenv("FAKE_OUT", str(out_bin))
    rec = run_harness.run_one(mods, dict(case), op_abi.get("spotrf"),
                              _ctx(tmp_path, fake, "criteria"))
    bundle = pathlib.Path(rec["evidence"]["bundle"])

    again, code = run_harness.rejudge(bundle, GEN_DIR)
    assert code == 1
    assert again["numeric"] == "FAIL"
    # 重判用的是留证件里的数组，与当场判定同一结论同一残差
    assert again["verdict"]["residual"]["ratio"] == \
        pytest.approx(rec["verdict"]["residual"]["ratio"])


def test_rejudge_rejects_non_bundle(tmp_path):
    with pytest.raises(run_harness.ContractError):
        run_harness.rejudge(tmp_path, GEN_DIR)


def test_judge_auto_routing(mods):
    assert run_harness.has_criteria_card(mods, "spotrf") is True
    assert run_harness.has_criteria_card(mods, "cpotrsBatched") is True
    assert run_harness.has_criteria_card(mods, "cmatinv_batched") is False


def test_load_modules_rejects_wrong_gen_dir(tmp_path):
    with pytest.raises(run_harness.ContractError) as exc:
        run_harness.load_modules(tmp_path)
    assert "gen_data_cholesky.py" in str(exc.value)


def test_pick_cases_rejects_old_basis(mods, tmp_path):
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "index.json").write_text(
        json.dumps({"ratio_basis": "A64-f64"}), encoding="utf-8")
    (tmp_path / "canonical_cases.json").write_text(
        json.dumps({"cases": [_spotrf_case()]}), encoding="utf-8")
    with pytest.raises(run_harness.ContractError) as exc:
        run_harness._pick_cases(mods, tmp_path / "canonical_cases.json",
                                None, None, None)
    assert "旧基包不受理" in str(exc.value)


def _sign_package(tmp_path):
    import gzip
    import hashlib
    index = tmp_path / "cases/index.json"
    payload = index.read_bytes() if index.exists() else gzip.decompress((tmp_path / "cases/index.json.gz").read_bytes())
    (tmp_path / "perf_baseline.json").write_text("{}")
    fp = {"cases/index.json": hashlib.sha256(payload).hexdigest(),
          "perf_baseline.json": hashlib.sha256(b"{}").hexdigest()}
    (tmp_path / "manifest.json").write_text(json.dumps({"fingerprint": fp}))


def test_pick_cases_accepts_new_basis(mods, tmp_path):
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "index.json").write_text(
        json.dumps({"ratio_basis": "A32-f64",
                    "ratio_cpu_mean": {"spotrf": 0.1},
                    "cases": [_spotrf_case(), _spotrf_case(n=64)]}), encoding="utf-8")
    (tmp_path / "canonical_cases.json").write_text(
        json.dumps({"cases": [_spotrf_case(), _spotrf_case(n=64)]}),
        encoding="utf-8")
    _sign_package(tmp_path)
    picked, mean = run_harness._pick_cases(
        mods, tmp_path / "canonical_cases.json", "spotrf", 32, None)
    assert [c["n"] for c in picked] == [16]          # --max-n 生效
    assert mean == {"spotrf": 0.1}


def test_structural_check_catches_bad_info_shape():
    abi = op_abi.get("spotrfBatched")               # info_kind = array
    case = {"n": 4, "batch": 3, "nrhs": 0}
    dut = {"out32": np.zeros((3, 4, 4), dtype=np.float32),
           "info": np.int64(0), "status": "ok"}
    v = run_harness.structural_check(abi, case, {}, dut)
    assert v["structural"] == "FAIL"
    assert any("info_shape_array" in f for f in v["failures"])


def test_structural_check_requires_three_keys():
    v = run_harness.structural_check(op_abi.get("sgetri"), {"n": 4},
                                     {}, {"status": "crash:SIGSEGV"})
    assert v["structural"] == "FAIL"
    assert v["numeric"] == "NOT_JUDGED"
    assert any("out32_present" in f for f in v["failures"])
