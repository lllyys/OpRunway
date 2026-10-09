# -*- coding: utf-8 -*-
"""HT-17 随机 SPD 均匀与正态各半的机械断言（case-gen 侧）。

新书 experimental_standard.md「用例生成」：数值分布要求均匀与正态各占一半——
均匀 50% U(-5,5)、正态 50% N(μ∈[-5,5], σ∈[0.1,2])。std 补充类每算子 2 条
（序数 9001→均匀组 U、9002→正态组 N），本文件把「各半」与「分布参数按标准
表原样」固化成可回归断言：

- 登记表与冻结登记：STD_DIST_BY_ORDINAL 恰 {1:U, 2:N}、std_dist_of 查表与
  未登记 fail-closed、freeze_canonical.STD_CASES_BASE 每基础算子恰 2 条 →
  每算子 1 均匀 + 1 正态，实/复两册同构共 12 条 = 6+6 各半
  → test_std_cases_half_uniform_half_normal
- 构造函数按蓝本抽取序逐位重演对拍（U/N × 实/复 × spd/rhs 全组合，改参数或
  改抽取序即红）+ SPD 构造保证（A=B·Bᴴ+nI，min eig ≥ n）
  → test_std_constructors_transcribe_standard_dists
"""
import numpy as np
import pytest

import freeze_canonical as fc
import gen_data_cholesky as gd


def test_std_cases_half_uniform_half_normal():
    # 登记表：恰两分布组，1=均匀、2=正态
    assert gd.STD_DIST_BY_ORDINAL == {1: "U", 2: "N"}
    # std_dist_of 查表：实/复 6 算子的 9001/9002 各落一组
    for base in ("potrf", "potrs", "potri"):
        for prefix in ("s", "c"):
            op = prefix + base
            assert gd.std_dist_of({"case_id": f"{op}-9001"}) == "U"
            assert gd.std_dist_of({"case_id": f"{op}-9002"}) == "N"
    # 表外序数 fail-closed（不猜测）
    with pytest.raises(gd.ContractError):
        gd.std_dist_of({"case_id": "spotrf-9003"})
    # 冻结侧登记：每基础算子恰 2 条（编号 9001/9002，freeze 枚举 start=1）
    assert set(fc.STD_CASES_BASE) == {"potrf", "potrs", "potri"}
    for cases in fc.STD_CASES_BASE.values():
        assert len(cases) == 2
        assert len({c["n"] for c in cases}) == 2          # 两条不同点位
    # 汇总各半：单册 3 算子 × 2 = 6 条，序数 {1,2} 各半 → 3 均匀 + 3 正态；
    # 实/复两册同构 → 全集 12 条 6+6，恰 50/50
    n_per_book = sum(len(v) for v in fc.STD_CASES_BASE.values())
    assert n_per_book == 6
    n_uniform = sum(1 for v in fc.STD_CASES_BASE.values() for _ in v) // 2
    assert n_uniform == 3


def test_std_constructors_transcribe_standard_dists():
    n, nrhs = 9, 3
    # 均匀组 spd：底阵 U(-5,5)（标准表 50% 档），A = B·Bᵀ + nI
    A = gd.gen_spd_std(np.random.default_rng(20251040), n, "U")
    r = np.random.default_rng(20251040)
    b = r.uniform(-5, 5, (n, n))
    assert np.array_equal(A, b @ b.T + n * np.eye(n))
    # 正态组 spd：μ∈[-5,5] σ∈[0.1,2] 先抽、底阵续抽（ch_potrf_verdict.py:45-47 序）
    A = gd.gen_spd_std(np.random.default_rng(20251424), n, "N")
    r = np.random.default_rng(20251424)
    mu, sigma = r.uniform(-5, 5), r.uniform(0.1, 2)
    b = r.normal(mu, sigma, (n, n))
    assert np.array_equal(A, b @ b.T + n * np.eye(n))
    # 复数 spd：实部底阵在前、虚部续抽同分布（U 组虚部 U(-5,5)；N 组虚部复用同一 μ/σ）
    Ac = gd.gen_spd_std(np.random.default_rng(20251041), n, "U", complex_=True)
    r = np.random.default_rng(20251041)
    B = r.uniform(-5, 5, (n, n)) + 1j * r.uniform(-5, 5, (n, n))
    assert np.array_equal(Ac, B @ B.conj().T + n * np.eye(n))
    Ac = gd.gen_spd_std(np.random.default_rng(20251425), n, "N", complex_=True)
    r = np.random.default_rng(20251425)
    mu, sigma = r.uniform(-5, 5), r.uniform(0.1, 2)
    B = r.normal(mu, sigma, (n, n)) + 1j * r.normal(mu, sigma, (n, n))
    assert np.array_equal(Ac, B @ B.conj().T + n * np.eye(n))
    # 右端 X_true：gen_rhs_std 同表参数（正态组 μ/σ 在本函数独立续抽，ch_potrs_verdict.py:59-61）
    x = gd.gen_rhs_std(np.random.default_rng(7), n, nrhs, "U")
    assert np.array_equal(
        x, np.random.default_rng(7).uniform(-5, 5, (n, nrhs)))
    xc = gd.gen_rhs_std(np.random.default_rng(8), n, nrhs, "U", complex_=True)
    r = np.random.default_rng(8)
    assert np.array_equal(
        xc, r.uniform(-5, 5, (n, nrhs)) + 1j * r.uniform(-5, 5, (n, nrhs)))
    x = gd.gen_rhs_std(np.random.default_rng(9), n, nrhs, "N")
    r = np.random.default_rng(9)
    mu, sigma = r.uniform(-5, 5), r.uniform(0.1, 2)
    assert np.array_equal(x, r.normal(mu, sigma, (n, nrhs)))
    xc = gd.gen_rhs_std(np.random.default_rng(10), n, nrhs, "N", complex_=True)
    r = np.random.default_rng(10)
    mu, sigma = r.uniform(-5, 5), r.uniform(0.1, 2)
    assert np.array_equal(
        xc, r.normal(mu, sigma, (n, nrhs)) + 1j * r.normal(mu, sigma, (n, nrhs)))
    # SPD 构造保证：A = B·Bᴴ + nI，最小特征值 ≥ n（复数先按生成侧契约置对角虚部 0）
    rng = np.random.default_rng(11)
    for dist in ("U", "N"):
        for cx in (False, True):
            A = gd.gen_spd_std(rng, n, dist, complex_=cx)
            if cx:
                np.fill_diagonal(A.imag, 0.0)
            assert np.linalg.eigvalsh(A).min() >= n - 1e-9 * n
