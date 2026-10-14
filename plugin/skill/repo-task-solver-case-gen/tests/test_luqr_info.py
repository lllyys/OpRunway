# -*- coding: utf-8 -*-
"""LU/QR 包数据生成器 info 契约与 A0 重生成协议回归（LQ-11/12，case-gen）。

锁住 `gen_data_luqr.py` 相对 `gen_data_cholesky.py` 的增量不变量：

- 包内自包含：本件不再 import 兄弟件（装包时被复制为 gen_data.py，兄弟件缺席）；
  render_verify 运行时校验的 5 个 API（build_batched_contents / derive_sample_map /
  build_batched_info_arrays / derive_batched_info_cases / build_case_arrays +
  derive_info_cases / build_info_arrays）必须齐备。
- 单算子 info 探针：getrf → singular_pivot（上三角第 k 阶对角置 0，info=k 自检）；
  getri → singular_lu（LU 第 k 阶 U(k,k) 置 0，info=k 自检）；QR 四算子 → bad_param
  （info=-1）；条数恒 INFO_CASES_PER_OP。
- 批量 info 探针：getriBatched/matinvBatched → singular_matrix（逐内容 k 列表，
  非奇异内容记 0；getriBatched 输入 LU/ipiv 第 k 阶对角置 0）。
- A0 内容协议：build_batched_contents == build_case_arrays（批量），含 golden；
  expand_sampled_rows 对 ipiv（2 维）保持批维前插。
- 端到端：main 在合成 canonical 上产出单算子 npz + 批量 sample_map + info 条目。
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

import gen_data_luqr as g

sys.path.insert(0, str(Path(g.__file__).resolve().parent))


def _case(cid, op, **kw):
    base = {"case_id": cid, "op": op, "source": "cu", "seed": 900000001,
            "s1_subset": True}
    base.update(kw)
    return base


def _cases():
    return [
        _case("sgetrf-1", "sgetrf", m=8, n=6, lda=8, pivot=True),
        _case("sgetrf-2", "sgetrf", m=12, n=12, lda=12, pivot=True),
        _case("cgetrf-1", "cgetrf", m=10, n=10, lda=10, pivot=True),
        _case("sgetri-1", "sgetri", n=5, lda=5, pivot=True),
        _case("sgeqrf-1", "sgeqrf", m=9, n=7, lda=9),
        _case("sgels-1", "sgels", m=8, n=5, nrhs=2, lda=8, ldb=8),
        _case("sgetribatched-1", "sgetribatched", n=6, batch=4, lda=6, ldc=6),
        _case("smatinvbatched-1", "smatinvbatched", n=6, batch=4, lda=6, lda_inv=6),
        _case("cgetribatched-1", "cgetribatched", n=5, batch=3, lda=5, ldc=5),
    ]


# ---------------------------------------------------------------------------
# 包内自包含 / API 面
# ---------------------------------------------------------------------------

REQUIRED_APIS = ("validate_case", "build_case_arrays", "build_batched_contents",
                 "derive_sample_map", "sampled_content_count", "expand_sampled_rows",
                 "derive_info_cases", "build_info_arrays",
                 "derive_batched_info_cases", "build_batched_info_arrays",
                 "ContractError")


def test_package_is_self_contained():
    src = Path(g.__file__).read_text(encoding="utf-8")
    assert "from gen_data_cholesky" not in src          # 包内兄弟件缺席
    for name in REQUIRED_APIS:
        assert hasattr(g, name), name


def test_single_ops_excludes_batched():
    assert set(g.SINGLE_OPS).isdisjoint(g.BATCHED_OPS)
    assert len(g.SINGLE_OPS) == 12 and len(g.BATCHED_OPS) == 4
    assert len(g.ALL_OPS) == 16


# ---------------------------------------------------------------------------
# 生成侧 4GiB 内存约束（LQ-14 / 假定 A-4）
# ---------------------------------------------------------------------------

def test_case_input_bytes_model():
    assert g.case_input_bytes("sgetrf", {"m": 100, "n": 100, "lda": 100}) == 100 * 100 * 4
    assert g.case_input_bytes("cgetrf", {"m": 100, "n": 100, "lda": 100}) == 100 * 100 * 8
    assert (g.case_input_bytes("cgetribatched", {"n": 32, "lda": 32, "batch": 1000})
            == 32 * 32 * 8 * 1000)
    assert (g.case_input_bytes("sgels", {"m": 8, "n": 4, "nrhs": 2, "ldda": 8, "lddb": 8})
            == (8 * 4 + 8 * 2) * 4)
    assert (g.case_input_bytes("sgetri", {"n": 8, "lda": 8}) == 8 * 8 * 4)


def test_validate_case_rejects_over_4gib():
    ok = _case("sgetribatched-ok", "sgetribatched", n=1024, batch=1000, lda=1024, ldc=1024)
    assert g.case_input_bytes("sgetribatched", ok) <= g.FOUR_GIB
    g.validate_case(ok)                                    # 未超限：放行
    big = _case("sgetribatched-big", "sgetribatched", n=1024, batch=1100, lda=1024, ldc=1024)
    assert g.case_input_bytes("sgetribatched", big) > g.FOUR_GIB
    with pytest.raises(g.ContractError):
        g.validate_case(big)


# ---------------------------------------------------------------------------
# 单算子 info
# ---------------------------------------------------------------------------

def test_derive_info_cases_single_only():
    info = g.derive_info_cases(_cases())
    assert info and all(e["op"] in g.SINGLE_OPS for e in info)
    by_op = {}
    for e in info:
        by_op.setdefault(e["op"], []).append(e)
    for lst in by_op.values():
        assert 1 <= len(lst) <= g.INFO_CASES_PER_OP      # 基座不足时可少于 3
        for e in lst:
            assert e["case_purpose"] == "info"
            if e["info_probe"] == "bad_param":
                assert e["k_expected"] == -1
            else:
                assert 1 <= e["k_expected"]


def test_build_info_arrays_singular_pivot_selfcheck():
    info = [e for e in g.derive_info_cases(_cases()) if e["op"] == "sgetrf"]
    by_id = {c["case_id"]: c for c in _cases()}
    assert info
    for e in info:
        base = by_id[e["base_case_id"]]
        arrs = g.build_info_arrays(e, g.build_case_arrays(base))
        assert set(arrs) == {"A64", "A32"}
        m, n = base["m"], base["n"]
        assert arrs["A64"].shape == (m, n)
        assert np.allclose(np.tril(arrs["A64"], -1), 0)      # 上三角
        k = e["k_expected"]
        assert arrs["A64"][k - 1, k - 1] == 0                 # 第 k 阶零主元


def test_build_info_arrays_complex_and_bad_param():
    cases = _cases()
    by_id = {c["case_id"]: c for c in cases}
    for e in g.derive_info_cases(cases):
        base = by_id[e["base_case_id"]]
        base_arrs = g.build_case_arrays(base)
        arrs = g.build_info_arrays(e, base_arrs)
        if e["info_probe"] == "bad_param":
            assert set(arrs) <= set(base_arrs)                # 原样输入面
        # 降型自检：A32 == A64.astype(f32)
        assert np.array_equal(arrs["A32"], arrs["A64"].astype(arrs["A32"].dtype))


# ---------------------------------------------------------------------------
# 批量 info
# ---------------------------------------------------------------------------

def test_derive_batched_info_cases_k_list():
    info = g.derive_batched_info_cases(_cases())
    assert info and all(e["op"] in g.BATCHED_OPS for e in info)
    for e in info:
        k = g.sampled_content_count(e["batch"])
        ks = e["k_expected"]
        assert isinstance(ks, list) and len(ks) == k
        assert all(isinstance(x, int) for x in ks)
        assert all(x >= 0 for x in ks)
        assert ks[0] >= 1                                    # 内容 0 必奇异


def test_build_batched_info_arrays_selfcheck():
    cases = _cases()
    for e in g.derive_batched_info_cases(cases):
        arrs = g.build_batched_info_arrays(e)
        n = e["n"]
        k = g.sampled_content_count(e["batch"])
        assert arrs["A64"].shape == (k, n, n)
        assert np.array_equal(arrs["A32"], arrs["A64"].astype(arrs["A32"].dtype))
        if e["op"].endswith("getribatched"):
            assert arrs["ipiv"].shape == (k, n)
            assert arrs["ipiv"].dtype == np.int32
        else:
            assert "ipiv" not in arrs
            # 非奇异内容（k_expected==0）保持原始随机阵
            for c, kk in enumerate(e["k_expected"]):
                if kk == 0:
                    assert not np.allclose(np.tril(arrs["A64"][c], -1), 0)


def test_build_batched_info_arrays_all_nonsingular_content():
    """k_expected 全 0 时不应触发奇异改造（非奇异自检路径）。"""
    cases = _cases()
    e = dict(g.derive_batched_info_cases(cases)[0])
    e["k_expected"] = [0] * g.sampled_content_count(e["batch"])
    arrs = g.build_batched_info_arrays(e)
    assert arrs["A64"].shape[0] == g.sampled_content_count(e["batch"])


# ---------------------------------------------------------------------------
# A0 内容协议
# ---------------------------------------------------------------------------

def test_build_batched_contents_matches_case_arrays():
    for c in _cases():
        if c["op"] not in g.BATCHED_OPS:
            continue
        a = g.build_case_arrays(c)
        b = g.build_batched_contents(c)
        assert set(a) == set(b)
        for key in a:
            assert np.array_equal(a[key], b[key])


def test_expand_sampled_rows_ipiv_2d():
    case = _case("sgetribatched-1", "sgetribatched", n=6, batch=4, lda=6, ldc=6)
    contents = g.build_batched_contents(case)
    smap = g.derive_sample_map(case["seed"], case["batch"])
    full = g.expand_sampled_rows(contents, smap, 0, case["batch"])
    assert full["ipiv"].shape == (case["batch"], case["n"])
    assert full["A64"].shape == (case["batch"], case["n"], case["n"])


# ---------------------------------------------------------------------------
# 端到端 main
# ---------------------------------------------------------------------------

def test_main_emits_info_entries(tmp_path):
    canonical = {"schema": "solver-luqr/canonical_cases@1", "cases": _cases()}
    cpath = tmp_path / "canonical_cases.json"
    cpath.write_text(json.dumps(canonical), encoding="utf-8")
    out = tmp_path / "out"
    ops = "sgetrf,sgetri,sgeqrf,sgels,sgetribatched,smatinvbatched"
    rc = g.main(["--canonical", str(cpath), "--out", str(out), "--ops", ops])
    assert rc == 0
    index = json.loads((out / "cases" / "index.json").read_text(encoding="utf-8"))
    infos = [e for e in index["cases"] if e.get("case_purpose") == "info"]
    assert infos
    for e in infos:
        assert "k_expected" in e and "info_probe" in e
        if e["op"] in g.BATCHED_OPS:
            assert "sample_map" in e and "npz" not in e
        else:
            assert (out / e["npz"]).is_file()