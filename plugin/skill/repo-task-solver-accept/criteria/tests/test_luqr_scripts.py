# -*- coding: utf-8 -*-
"""LQ-22 机械断言：LU/QR 支路在 accept 侧脚本与 criteria 增量。

覆盖（改动清单见 lq-plan §1/§2，本文件只锁行为不回述实现）：

- sim_dut.sim_dut_from_arrays：getrf 无 golden → 现场低精度 getrf 合成打包 LU+ipiv
  （1 基）；geqrf 附 tau；批量两族 infoArray；无 golden 且非 getrf 抛错；
- expectations.is_batched_operator：lowercase 批量后缀（sgetribatched 等）判 True；
- accept_run.get_card/supported_ops/load_dut_out：LU/QR 16 接口统一取卡；被测输出
  ipiv/tau 透传；
- batched_a0.judge_representatives：ipiv 二维（batch, n）内容数组放行（LU/QR
  getriBatched 增量，与 verdict 批量校验同口径）；judge_a0 端到端 PASS；
- stream_check：_gen_for/_card_for 按算子族分流；LU/QR 单矩阵 + 批量 A0 端到端。
"""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")
from scipy.linalg import lapack as sla

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent.parent / "scripts"
_CRITERIA = _HERE.parent
_GEN_DIR = (_HERE.parent.parent.parent.parent
            / "skill" / "repo-task-solver-case-gen" / "scripts")

for _p in (str(_SCRIPTS), str(_CRITERIA)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"{name}_lq22", _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# sim_dut：LU/QR 输出契约（getrf ipiv / geqrf tau / 批量 infoArray）
# ---------------------------------------------------------------------------

def test_sim_dut_getrf_synth_lu_ipiv():
    sim = _load("sim_dut")
    import verdict
    a32 = np.array([[2.0, 1.0], [1.0, 3.0]], dtype=np.float32)
    out = sim.sim_dut_from_arrays("sgetrf", {"A32": a32}, "none")
    assert set(out) == {"out32", "info", "status", "ipiv"}
    assert out["out32"].shape == (2, 2)
    assert out["info"] == 0 and out["status"] == "ok"
    ipiv = np.asarray(out["ipiv"])
    assert ipiv.ndim == 1 and ipiv.shape[0] == 2
    assert ipiv.min() >= 1                      # 1 基主元
    recon = verdict.restore_lu(out["out32"], ipiv)
    np.testing.assert_allclose(recon, a32, rtol=1e-6, atol=1e-6)


def test_sim_dut_geqrf_carries_tau():
    sim = _load("sim_dut")
    golden32 = np.array([[1.0, 2.0, 3.0], [0.0, 1.0, 4.0],
                         [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]], dtype=np.float32)
    tau32 = np.array([1.5, 1.25, 1.1], dtype=np.float32)
    out = sim.sim_dut_from_arrays(
        "sgeqrf", {"A32": golden32, "golden32": golden32, "tau32": tau32}, "none")
    assert set(out) == {"out32", "info", "status", "tau"}
    np.testing.assert_array_equal(np.asarray(out["tau"]), tau32)
    np.testing.assert_array_equal(np.asarray(out["out32"]), golden32)
    assert out["info"] == 0


def test_sim_dut_batched_info_array():
    sim = _load("sim_dut")
    golden32 = np.zeros((3, 2, 2), dtype=np.float32)
    out = sim.sim_dut_from_arrays(
        "sgetribatched", {"A32": golden32, "golden32": golden32}, "none")
    info = np.asarray(out["info"])
    assert info.dtype == np.int32 and info.shape == (3,)   # 逐矩阵 infoArray
    assert not info.any()
    assert np.asarray(out["out32"]).shape == (3, 2, 2)


def test_sim_dut_no_golden_non_getrf_raises():
    sim = _load("sim_dut")
    with pytest.raises(ValueError):
        sim.sim_dut_from_arrays("sgetri", {"A32": np.eye(3, dtype=np.float32)}, "none")


# ---------------------------------------------------------------------------
# expectations：lowercase 批量后缀
# ---------------------------------------------------------------------------

def test_is_batched_operator_lowercase_suffix():
    import expectations as exp
    for op in ("sgetribatched", "cgetribatched", "smatinvbatched", "cmatinvbatched",
               "spotrfBatched", "spotrsBatched"):
        assert exp.is_batched_operator(op) is True, op
    for op in ("sgetrf", "sgeqrf", "sgels", "sgetri"):
        assert exp.is_batched_operator(op) is False, op


# ---------------------------------------------------------------------------
# accept_run：统一取卡 + ipiv/tau 透传
# ---------------------------------------------------------------------------

def test_accept_run_get_card_dispatch():
    ar = _load("accept_run")
    assert ar.get_card("sgetrf").residual_kind == "DGET01"
    assert ar.get_card("sgeqrf").residual_kind == "DQRT01"
    assert ar.get_card("sgetribatched").batched is True
    # Cholesky 通路零漂移：spotrf 仍落 cards_cholesky
    assert ar.get_card("spotrf").residual_kind == "DPOT01"


def test_accept_run_supported_ops_includes_luqr():
    ar = _load("accept_run")
    sup = ar.supported_ops()
    for op in ("sgetrf", "cgetri", "sgetribatched", "smatinvbatched",
               "sgeqrf", "cungqr", "sormqr", "cgels"):
        assert op in sup, op


def test_accept_run_load_dut_out_passthrough(tmp_path):
    ar = _load("accept_run")
    dut_dir = tmp_path / "dut"
    dut_dir.mkdir()
    ipiv = np.array([1, 2], dtype=np.int32)
    tau = np.array([1.5, 1.25], dtype=np.float32)
    np.savez(dut_dir / "sgetrf-0001.npz", out32=np.zeros((2, 2), dtype=np.float32),
             info=np.int64(0), status=np.str_("ok"), ipiv=ipiv)
    dut, why = ar.load_dut_out(dut_dir, "sgetrf-0001")
    assert why is None and "ipiv" in dut
    np.testing.assert_array_equal(np.asarray(dut["ipiv"]), ipiv)
    # 缺 ipiv/tau 的接口不伪造该键
    np.savez(dut_dir / "sgels-0001.npz", out32=np.zeros((2, 1), dtype=np.float32),
             info=np.int64(0), status=np.str_("ok"))
    dut2, _ = ar.load_dut_out(dut_dir, "sgels-0001")
    assert "ipiv" not in dut2 and "tau" not in dut2
    # tau 透传（geqrf）
    np.savez(dut_dir / "sgeqrf-0001.npz", out32=np.zeros((2, 2), dtype=np.float32),
             info=np.int64(0), status=np.str_("ok"), tau=tau)
    dut3, _ = ar.load_dut_out(dut_dir, "sgeqrf-0001")
    np.testing.assert_array_equal(np.asarray(dut3["tau"]), tau)


# ---------------------------------------------------------------------------
# batched_a0：ipiv 二维内容数组放行 + judge_a0 端到端
# ---------------------------------------------------------------------------

def _a0_getribatched_fixture(k=2, n=3):
    """真 LU/ipiv/逆：逆对「LU32 按 ipiv 还原后的 a」求（DGET03 喂数同一 a），
    使代表槽正例残差仅 f64 eps 量级。"""
    import cards_luqr
    import verdict
    card = cards_luqr.get_card("sgetribatched")
    rng = np.random.default_rng(4)
    A64 = np.empty((k, n, n))
    LU64 = np.empty((k, n, n))
    LU32 = np.empty((k, n, n), dtype=np.float32)
    ipiv = np.empty((k, n), dtype=np.int32)
    golden = np.empty((k, n, n))
    for i in range(k):
        A64[i] = rng.standard_normal((n, n)) + 4 * np.eye(n)
        lu, piv, info = sla.dgetrf(A64[i], overwrite_a=0)
        assert info == 0
        LU64[i] = lu
        LU32[i] = lu.astype(np.float32)
        ipiv[i] = piv + 1
        golden[i] = np.linalg.inv(verdict.restore_lu(LU32[i], ipiv[i]))
    contents = {
        "A64": A64, "A32": A64.astype(np.float32),
        "golden64": golden, "golden32": golden.astype(np.float32),
        "LU64": LU64, "LU32": LU32,
        "ipiv": ipiv,                                 # 二维（batch, n）
    }
    sample_map = [{"content_idx": 0, "rep_slot": 0, "slots": [0, 2]},
                  {"content_idx": 1, "rep_slot": 1, "slots": [1, 3]}]
    batch = 4
    out32 = np.zeros((batch, n, n), dtype=np.float32)
    for e in sample_map:
        c = e["content_idx"]
        for s in e["slots"]:
            out32[s] = golden[c]
    dut = {"out32": out32, "info": np.zeros(batch, dtype=np.int32), "status": "ok"}
    case_arrays = dict(contents)
    case_arrays.update({"batch": batch, "sample_map": sample_map,
                        "ratio_cpu": [0.1, 0.1], "ratio_cpu_status": "ok"})
    return card, case_arrays, dut


def test_batched_a0_ipiv_2d_is_accepted():
    import batched_a0
    card, case_arrays, dut = _a0_getribatched_fixture()
    v = batched_a0.judge_a0(card, case_arrays, dut, jobs=1)
    assert v["error"] is None, v
    assert v["batch"] == 2 and v["fail_count"] == 0
    assert v["numeric"] == "PASS", v


def test_batched_a0_ipiv_wrong_first_dim_raises():
    import batched_a0
    card, case_arrays, dut = _a0_getribatched_fixture()
    case_arrays["ipiv"] = np.ones((5, 3), dtype=np.int32)   # 首维 != k
    v = batched_a0.judge_a0(card, case_arrays, dut, jobs=1)
    assert v["error"] is not None and "ipiv" in v["error"], v


# ---------------------------------------------------------------------------
# stream_check：生成器/判据卡分流 + LU/QR 端到端
# ---------------------------------------------------------------------------

def _load_stream_check():
    return _load("stream_check")


def test_stream_check_gen_and_card_dispatch():
    mod = _load_stream_check()
    (_, cards_m, _, _, gen_m, _, gen_luqr_m, cards_luqr_m) = mod._load_modules(
        str(_GEN_DIR))
    assert mod._gen_for("sgetrf", gen_m, gen_luqr_m) is gen_luqr_m
    assert mod._gen_for("sgeqrf", gen_m, gen_luqr_m) is gen_luqr_m
    assert mod._gen_for("spotrf", gen_m, gen_luqr_m) is gen_m
    assert mod._card_for("sgetribatched", cards_m, cards_luqr_m).batched is True
    assert mod._card_for("sgeqrf", cards_m, cards_luqr_m).residual_kind == "DQRT01"
    assert mod._card_for("spotrf", cards_m, cards_luqr_m).residual_kind == "DPOT01"


def _luqr_canonical(tmp_path):
    """最小合成册：sgetrf 单矩阵 + sgetribatched 批量（A0，batch 小）各 1 例。"""
    cases = [
        {"case_id": "lq22-getrf", "op": "sgetrf", "source": "cu", "m": 6, "n": 6,
         "lda": 6, "seed": 922001, "s1_subset": True},
        {"case_id": "lq22-getrib", "op": "sgetribatched", "source": "cu", "n": 4,
         "batch": 6, "lda": 4, "seed": 922002, "s1_subset": True},
    ]
    path = tmp_path / "canonical_luqr.json"
    path.write_text(json.dumps({"schema": "solver-luqr/canonical@1", "cases": cases}),
                    encoding="utf-8")
    return path


def test_stream_check_luqr_end_to_end(tmp_path):
    mod = _load_stream_check()
    canonical = _luqr_canonical(tmp_path)
    report = tmp_path / "report.json"
    rc = mod.main(["--canonical", str(canonical), "--gen-dir", str(_GEN_DIR),
                   "--report", str(report), "--select", "all"])
    rep = json.load(open(report))
    assert rc == 0, rep["summary"]
    assert rep["summary"]["numeric_fail"] == 0 and rep["summary"]["error"] == 0
    by_id = {c["case_id"]: c for c in rep["cases"]}
    # 单矩阵：DGET01 还原 LU vs A（getrf 无 golden，现场合成 LU+ipiv）
    g = by_id["lq22-getrf"]
    assert g["numeric"] == "PASS" and g["residual"]["ran"] is True
    assert g["residual"]["pass"] is True
    # 批量：A0 通路（mode=a0，代表内容 k=min(5,batch)）
    b = by_id["lq22-getrib"]
    assert b["mode"] == "a0" and b["canonical_batch"] == 6
    assert b["sampled_contents"] == 5 and b["fail_count"] == 0
    assert b["numeric"] == "PASS"
    # info 契约用例（两族各派 1）：独立结论数值 PASS
    info = [c for c in rep["cases"] if c.get("case_purpose") == "info"]
    assert len(info) == 2 and all(c["numeric"] == "PASS" for c in info)


def test_stream_check_luqr_batch_consistency_mismatch(tmp_path):
    """A0 一致性失配负例：扰动命中非 rep 槽 → 数值 FAIL 带槽位号证据。"""
    mod = _load_stream_check()
    canonical = _luqr_canonical(tmp_path)
    gen_luqr = importlib.import_module("gen_data_luqr")
    case = json.load(open(canonical))["cases"][1]
    smap = gen_luqr.derive_sample_map(case["seed"], case["batch"])
    reps = {int(e["rep_slot"]) for e in smap}
    bad_slot = next(s for s in range(case["batch"]) if s not in reps)
    report = tmp_path / "bad.json"
    rc = mod.main(["--canonical", str(canonical), "--gen-dir", str(_GEN_DIR),
                   "--report", str(report), "--select", "all", "--ops", "sgetribatched",
                   "--perturb", "scale", "--perturb-index", str(bad_slot)])
    rep = json.load(open(report))
    rec = next(c for c in rep["cases"]
               if c["case_id"] == "lq22-getrib")
    assert rc == 1
    assert rec["numeric"] == "FAIL" and rec["error"] is None
    assert rec["first_fail_index"] == bad_slot
    assert rec["diagnostics"]["a0_mismatches"], rec["diagnostics"]