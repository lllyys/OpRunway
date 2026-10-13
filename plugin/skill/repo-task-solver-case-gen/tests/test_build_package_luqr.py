# -*- coding: utf-8 -*-
"""build_package.py LU/QR 支路回归（LQ-13，case-gen）。

`build_package.py` 原为 Cholesky 专用；LQ-13 扩展 16 接口后，本文件锁住新支路的
关键不变量（族分派 / 形状放宽 / golden 独立重算 / 批量路由 / 注册表与包版本），
并确认 Cholesky 原口径未受影响：

- 算子注册表：`LUQR_SINGLE_OPS`(12) + `LUQR_BATCHED_OPS`(4) = `LUQR_OPS`(16)，
  并入 `ALL_OPS`；`_is_luqr`/`_luqr_base` 族分派；
- `_luqr_gen_module_name`：LU/QR → ``gen_data_luqr.py``，Cholesky →
  ``gen_data_cholesky.py``（两族数据面全独立）；
- `_expected_a64_shape`：矩形口径逐算子（getri→(n,n)、*gqr→(m,n)、*mqr→(nq,k)、
  getrf/geqrf/gels→(m,n)），实/复拼写（orgqr/ungqr、ormqr/unmqr）都要命中；
- `_recompute_golden_luqr`：getrf 无 golden（返回 None、不调 LAPACK）；gqr/mqr
  低精度链路（sorgqr/sormqr 走 dorgqr/dormqr，cungqr/cunmqr 走 zungqr/zunmqr）；
- `spot_check_indices(0)` 返回空表（getrf-only 算子无 golden 可抽验，不得越界）；
- `main` 路由：LU/QR 批量算子路由到 `build_batched`（端到端装包冒烟）。
"""
import json

import numpy as np
import pytest

import build_package as bp


# ---------------------------------------------------------------------------
# 注册表与族分派
# ---------------------------------------------------------------------------

def test_luqr_op_registry_covers_16():
    assert len(bp.LUQR_SINGLE_OPS) == 12
    assert len(bp.LUQR_BATCHED_OPS) == 4
    assert len(bp.LUQR_OPS) == 16
    assert set(bp.LUQR_OPS) <= set(bp.ALL_OPS)
    # Cholesky 六算子仍在 ALL_OPS，且不被判为 LU/QR
    assert "spotrf" in bp.ALL_OPS
    assert not bp._is_luqr("spotrf")
    assert not bp._is_luqr("spotrfBatched")


@pytest.mark.parametrize("op,is_luqr", [
    ("sgetrf", True), ("cgetri", True), ("sorgqr", True), ("cunmqr", True),
    ("sgetribatched", True), ("cmatinvbatched", True),
    ("spotrf", False), ("cpotrsBatched", False),
])
def test_is_luqr_dispatch(op, is_luqr):
    assert bp._is_luqr(op) is is_luqr


@pytest.mark.parametrize("op,base", [
    ("sgetrf", "getrf"), ("cgetri", "getri"),
    ("sorgqr", "orgqr"), ("cungqr", "ungqr"),
    ("sormqr", "ormqr"), ("cunmqr", "unmqr"),
    ("sgeqrf", "geqrf"), ("sgels", "gels"),
])
def test_luqr_base_strips_prefix(op, base):
    assert bp._luqr_base(op) == base


@pytest.mark.parametrize("op,mod", [
    ("sgetrf", "gen_data_luqr.py"),
    ("cunmqr", "gen_data_luqr.py"),
    ("spotrf", "gen_data_cholesky.py"),
    ("cpotrsBatched", "gen_data_cholesky.py"),
])
def test_gen_module_name(op, mod):
    assert bp._luqr_gen_module_name(op) == mod


# ---------------------------------------------------------------------------
# 形状放宽（_expected_a64_shape）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("op,entry,shape", [
    ("sgetrf", {"m": 5, "n": 3}, (5, 3)),
    ("cgeqrf", {"m": 6, "n": 4}, (6, 4)),
    ("sgetri", {"n": 4}, (4, 4)),                  # getri 恒 (n,n)（无 m）
    ("sgels", {"m": 8, "n": 4}, (8, 4)),
    ("sorgqr", {"m": 6, "n": 4}, (6, 4)),
    ("cungqr", {"m": 7, "n": 5}, (7, 5)),
    ("sormqr", {"m": 6, "n": 5, "k": 2, "side": "LEFT"}, (6, 2)),
    ("cunmqr", {"m": 6, "n": 5, "k": 2, "side": "RIGHT"}, (5, 2)),
])
def test_expected_a64_shape_luqr(op, entry, shape):
    assert bp._expected_a64_shape(dict(entry, op=op)) == shape


def test_expected_a64_shape_cholesky_square():
    assert bp._expected_a64_shape({"op": "cpotrf", "n": 9}) == (9, 9)


# ---------------------------------------------------------------------------
# spot_check_indices 边界
# ---------------------------------------------------------------------------

def test_spot_check_indices_behaviour():
    assert bp.spot_check_indices(0) == []           # getrf-only：无 golden 可抽验
    assert bp.spot_check_indices(1) == [0]
    assert bp.spot_check_indices(4) == [0, 2, 3]


# ---------------------------------------------------------------------------
# golden 独立重算（_recompute_golden_luqr）——LAPACK 例程分派
# ---------------------------------------------------------------------------

class _SpyLapack:
    """记录被访问的 LAPACK 例程名，其余透传真件。"""

    def __init__(self, real):
        self._real = real
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        return getattr(self._real, name)


@pytest.fixture
def spy(monkeypatch):
    real = bp._lapack()
    s = _SpyLapack(real)
    monkeypatch.setattr(bp, "_lapack", lambda: s)
    return s


def test_recompute_getrf_returns_none_without_lapack(spy):
    assert bp._recompute_golden_luqr("sgetrf", {"A64": np.eye(3)}, "c", {}) is None
    assert bp._recompute_golden_luqr("cgetrf", {"A64": np.eye(3, dtype=np.complex128)},
                                     "c", {}) is None
    assert spy.calls == []                          # 无 golden，不应触碰 LAPACK


def _qr_pack(m, n, cx):
    from scipy.linalg import lapack
    rng = np.random.default_rng(7)
    if cx:
        M = (rng.random((m, n)) + 1j * rng.random((m, n))).astype(np.complex128)
        af, tau, _w, _i = lapack.zgeqrf(M, overwrite_a=0)
    else:
        M = rng.random((m, n)).astype(np.float64)
        af, tau, _w, _i = lapack.dgeqrf(M, overwrite_a=0)
    return np.ascontiguousarray(af), np.ascontiguousarray(tau)


@pytest.mark.parametrize("op,cx,expect,forbid", [
    ("sorgqr", False, "dorgqr", "zungqr"),
    ("cungqr", True, "zungqr", "dorgqr"),
])
def test_recompute_gqr_low_precision_prefix(spy, op, cx, expect, forbid):
    af, tau = _qr_pack(6, 4, cx)
    g = bp._recompute_golden_luqr(op, {"A64": af, "tau64": tau}, "c", {})
    assert g.shape == (6, 4)
    assert expect in spy.calls
    assert forbid not in spy.calls


@pytest.mark.parametrize("op,cx,expect,forbid", [
    ("sormqr", False, "dormqr", "zunmqr"),
    ("cunmqr", True, "zunmqr", "dormqr"),
])
def test_recompute_mqr_low_precision_prefix(spy, op, cx, expect, forbid):
    af, tau = _qr_pack(6, 2, cx)                    # side=LEFT → nq=m=6, k=2
    rng = np.random.default_rng(11)
    if cx:
        C = (rng.random((6, 5)) + 1j * rng.random((6, 5))).astype(np.complex128)
    else:
        C = rng.random((6, 5)).astype(np.float64)
    g = bp._recompute_golden_luqr(
        op, {"A64": af, "tau64": tau, "C64": C}, "c",
        {"side": "LEFT", "trans": "N"})
    assert g.shape == (6, 5)
    assert expect in spy.calls
    assert forbid not in spy.calls


def test_recompute_getri_uses_packed_lu(spy):
    from scipy.linalg import lapack
    rng = np.random.default_rng(3)
    A = rng.random((4, 4)).astype(np.float64)
    lu, piv, info = lapack.dgetrf(A, overwrite_a=0)
    assert info == 0
    g = bp._recompute_golden_luqr("sgetri", {"A64": A, "LU64": np.ascontiguousarray(lu),
                                             "ipiv": piv + 1}, "c", {})
    assert g.shape == (4, 4)
    assert "dgetri" in spy.calls


# ---------------------------------------------------------------------------
# 端到端装包冒烟（合成切片 + 合成 perf_baseline）
# ---------------------------------------------------------------------------

def _canonical(cases):
    return {"schema": "solver-luqr/canonical_cases@1", "book": "smoke", "cases": cases}


def _write_staging(tmp, op, cases):
    canon = tmp / f"{op}.json"
    canon.write_text(json.dumps(_canonical(cases), ensure_ascii=False),
                     encoding="utf-8")
    stage = tmp / "staging" / op
    stage.mkdir(parents=True)
    (stage / "perf_baseline.json").write_text(
        json.dumps({"op": op, "cases": []}), encoding="utf-8")
    return canon, tmp / "staging"


def test_end_to_end_single_luqr(tmp_path):
    cases = [
        {"case_id": f"sgetri-t{i}", "op": "sgetri", "source": "std",
         "n": 4 + i, "lda": 4 + i, "pivot": True,
         "seed": 100 + i, "s1_subset": True}
        for i in range(3)
    ]
    canon, staging = _write_staging(tmp_path, "sgetri", cases)
    out = tmp_path / "pkg"
    rc = bp.main(["--canonical", str(canon), "--staging", str(staging),
                  "--out", str(out), "--op", "sgetri", "--scope", "full"])
    assert rc == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["package_ver"] == bp.PACKAGE_VER_LUQR
    idx = json.loads((out / "cases" / "index.json").read_text(encoding="utf-8"))
    assert "ratio_cpu_mean" not in idx            # LU/QR 无 mean 第二支
    assert all(c["materialize"] == "gen" for c in idx["cases"])
    assert not list(out.rglob("*.npz"))            # 零数据
    assert "sgetri" in (out / "README.md").read_text(encoding="utf-8")
    # getri 有残差判据（DGET03）→ 精度条目 ratio_cpu_status 记 ok
    prec = [c for c in idx["cases"] if c.get("case_purpose") != "info"]
    assert prec and all(c["ratio_cpu_status"] == "ok" for c in prec)


def test_end_to_end_batched_luqr(tmp_path):
    cases = [
        {"case_id": f"sgetribatched-t{i}", "op": "sgetribatched", "source": "std",
         "n": 4, "batch": 8, "lda": 4, "ldc": 4,
         "seed": 200 + i, "s1_subset": True}
        for i in range(3)
    ]
    canon, staging = _write_staging(tmp_path, "sgetribatched", cases)
    out = tmp_path / "pkg"
    rc = bp.main(["--canonical", str(canon), "--staging", str(staging),
                  "--out", str(out), "--op", "sgetribatched", "--scope", "full"])
    assert rc == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["package_ver"] == bp.PACKAGE_VER_LUQR_BATCHED
    idx = json.loads((out / "cases" / "index.json").read_text(encoding="utf-8"))
    prec = [c for c in idx["cases"] if c.get("case_purpose") != "info"]
    # 批量回填：逐内容 ratio_cpu k 值列表（k=min(5,batch)）
    assert prec and all(isinstance(c["ratio_cpu"], list) for c in prec)
    assert all(len(c["ratio_cpu"]) == 5 for c in prec)
    assert not list(out.rglob("*.npz"))
    assert "sgetribatched" in (out / "README.md").read_text(encoding="utf-8")