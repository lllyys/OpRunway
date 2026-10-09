# -*- coding: utf-8 -*-
"""真实 LAPACK 链路正例（scipy，s2-A1 换基口径）：s 前缀 FP32 被测链 + A32 基
自指 ratio_cpu，judge 一段式数值 PASS。

数据构造对齐 README 数据规则（生态标准 §1.2 均匀分布 + A = B·Bᵀ + n·I，
seed = 20250912+n）；ratio_cpu 用「被测即 CPU 参考链路」的自指定义
（README 2.3 potrs 同款），并用同一 residual_ratio 实现计算（spec §2.4）。

s2-A1 换基验收点（potrf 族 a 从 A64 改 A32）：ratio_cpu 与判定残差同为 A32 基、
同一实现、同一输入 → 二者逐位相等，阈值 max(5·ratio_cpu, 3·mean) ≥ ratio →
f32 链真算必 PASS。另钉住 A64 基与 A32 基的 potrf 残差数值确实不同
（冻结 ratio_cpu 需按新基重算的事实根据）。
"""
import pytest

pytest.importorskip("scipy")  # spec 波 1 A 行：scipy importorskip 守卫
import numpy as np
from scipy.linalg.lapack import spotrf, spotri, spotrs

import cards_cholesky
import verdict
from verdict import residual_ratio

N = 16


def _spd(n, seed):
    rng = np.random.default_rng(seed)
    b = rng.uniform(-5.0, 5.0, (n, n))
    return b @ b.T + n * np.eye(n)


@pytest.fixture(scope="module")
def chain():
    """一次性准备三接口共用的 FP32 被测链路。"""
    a64 = _spd(N, 20250912 + N)
    a32 = a64.astype(np.float32)
    f32, info_f = spotrf(a32.copy(), lower=1)
    assert info_f == 0
    return {"a64": a64, "a32": a32, "f32": f32}


def test_spotrf_real_chain_numeric_pass(chain):
    """potrf 换基后正例（s2-A1 验收清单项）：f32 链真算 + A32 基自指 ratio_cpu
    → 判定残差 == ratio_cpu（同实现同输入）→ 阈值 5·ratio_cpu 托住 → PASS。"""
    ratio_cpu = residual_ratio("DPOT01", a=chain["a32"],
                               factor=chain["f32"], uplo="L")   # A32 基自指
    case = {"A32": chain["a32"], "uplo": "L",
            "ratio_cpu": ratio_cpu, "ratio_cpu_status": "ok", "ratio_cpu_mean": 0.0}
    v = verdict.judge(cards_cholesky.get_card("spotrf"), case,
                      {"out32": chain["f32"], "info": 0, "status": "ok"})
    assert v["error"] is None
    assert v["residual"]["ratio"] == ratio_cpu           # 同实现同输入，逐位相等
    assert v["residual"]["threshold"] == 5.0 * ratio_cpu
    assert v["numeric"] == "PASS"
    assert v["formal"] == "PENDING_RULING"


def test_spotrf_basis_change_shifts_ratio(chain):
    """换基影响钉子：同一 f32 因子对 A64 基与 A32 基的 DPOT01 残差数值不同
    （A64 基分子含「A 降 f32 的舍入」项）——potrf 族冻结 ratio_cpu/mean 系 A64 基，
    换基后过期需重算（声明，重算另排）。"""
    r_a32 = residual_ratio("DPOT01", a=chain["a32"], factor=chain["f32"], uplo="L")
    r_a64 = residual_ratio("DPOT01", a=chain["a64"], factor=chain["f32"], uplo="L")
    assert r_a32 != r_a64


def test_spotrs_real_chain_numeric_pass(chain):
    rng = np.random.default_rng(20250912 + N + 1)
    x_true = rng.uniform(-5.0, 5.0, (N, 4))
    b64 = chain["a64"] @ x_true
    b32 = b64.astype(np.float32)
    x32, info = spotrs(chain["f32"], b32.copy(), lower=1)
    assert info == 0
    ratio_cpu = residual_ratio("DPOT02", a=chain["a32"], b=b32, x=x32)  # 自指
    case = {"A32": chain["a32"], "B32": b32,
            "ratio_cpu": ratio_cpu, "ratio_cpu_status": "ok", "ratio_cpu_mean": 0.0}
    v = verdict.judge(cards_cholesky.get_card("spotrs"), case,
                      {"out32": x32, "info": 0, "status": "ok"})
    assert v["error"] is None
    assert v["residual"]["ratio"] == ratio_cpu
    assert v["numeric"] == "PASS"
    assert v["formal"] == "PENDING_RULING"


def test_spotri_real_chain_numeric_pass(chain):
    c32, info = spotri(chain["f32"].copy(), lower=1)
    assert info == 0
    ratio_cpu = residual_ratio("DPOT03", a=chain["a32"],
                               ainv=np.tril(c32), uplo="L")     # 自指
    case = {"A32": chain["a32"], "uplo": "L",
            "ratio_cpu": ratio_cpu, "ratio_cpu_status": "ok"}
    v = verdict.judge(cards_cholesky.get_card("spotri"), case,
                      {"out32": np.tril(c32), "info": 0, "status": "ok"})
    assert v["error"] is None
    assert v["residual"]["ratio"] == ratio_cpu
    assert v["numeric"] == "PASS"
    assert v["formal"] == "PENDING_RULING"
