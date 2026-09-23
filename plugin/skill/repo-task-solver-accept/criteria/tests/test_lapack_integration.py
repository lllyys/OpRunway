# -*- coding: utf-8 -*-
"""真实 LAPACK 链路正例（scipy）：s 前缀被测 vs d 前缀 golden，judge 数值 PASS。

数据构造对齐 README 数据规则（生态标准 §1.2 均匀分布 + A = B·Bᵀ + n·I，
seed = 20250912+n）；ratio_cpu 用「被测即 CPU 参考链路」的自指定义
（README 2.3 potrs 同款），并用同一 residual_ratio 实现计算（spec §2.4）。

2.3′ 后主判目标随卡走（potrf 还原对 A、potri 双目标）；真实链路允许 layer1
边缘元素挂靠兜底，健康链路残差在底噪水平，数值终审仍为 PASS。
"""
import pytest

pytest.importorskip("scipy")  # spec 波 1 A 行：scipy importorskip 守卫
import numpy as np
from scipy.linalg.lapack import dpotrf, dpotri, spotrf, spotri, spotrs

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
    """一次性准备三接口共用的 FP32 被测链路与 FP64 golden 链路。"""
    a64 = _spd(N, 20250912 + N)
    a32 = a64.astype(np.float32)
    f32, info_f = spotrf(a32.copy(), lower=1)
    assert info_f == 0
    fg64, info_g = dpotrf(a64.copy(), lower=1)
    assert info_g == 0
    return {"a64": a64, "a32": a32, "f32": f32, "fg64": fg64}


def test_spotrf_real_chain_numeric_pass(chain):
    golden32 = np.tril(chain["fg64"]).astype(np.float32)   # 存储侧半三角另侧置 0
    ratio_cpu = residual_ratio("DPOT01", a=chain["a64"],
                               factor=chain["f32"], uplo="L")   # s 链路自指
    case = {"A64": chain["a64"], "A32": chain["a32"], "golden32": golden32,
            "uplo": "L", "ratio_cpu": ratio_cpu, "ratio_cpu_status": "ok"}
    v = verdict.judge(cards_cholesky.get_card("spotrf"), case,
                      {"out32": chain["f32"], "info": 0, "status": "ok"})
    assert v["error"] is None
    assert v["layer1"]["targets"][0]["name"] == "recon_vs_A"    # 2.3′ 主判目标
    assert v["layer1"]["diagnostics"][0]["name"] == "factor_vs_golden"
    assert v["numeric"] == "PASS"
    assert v["formal"] == "PENDING_RULING"


def test_spotrs_real_chain_numeric_pass(chain):
    rng = np.random.default_rng(20250912 + N + 1)
    x_true = rng.uniform(-5.0, 5.0, (N, 4))
    b64 = chain["a64"] @ x_true
    b32 = b64.astype(np.float32)
    x32, info = spotrs(chain["f32"], b32.copy(), lower=1)
    assert info == 0
    golden32 = np.linalg.solve(chain["a64"], b64).astype(np.float32)
    ratio_cpu = residual_ratio("DPOT02", a=chain["a32"], b=b32, x=x32)  # 自指
    case = {"A64": chain["a64"], "A32": chain["a32"], "B64": b64, "B32": b32,
            "golden32": golden32, "uplo": "L",
            "ratio_cpu": ratio_cpu, "ratio_cpu_status": "ok"}
    v = verdict.judge(cards_cholesky.get_card("spotrs"), case,
                      {"out32": x32, "info": 0, "status": "ok"})
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert v["formal"] == "PENDING_RULING"


def test_spotri_real_chain_numeric_pass(chain):
    c32, info = spotri(chain["f32"].copy(), lower=1)
    assert info == 0
    cg64, info_g = dpotri(chain["fg64"].copy(), lower=1)
    assert info_g == 0
    golden32 = np.tril(cg64).astype(np.float32)            # 存储侧半三角另侧置 0
    ratio_cpu = residual_ratio("DPOT03", a=chain["a32"],
                               ainv=np.tril(c32), uplo="L")     # 自指
    case = {"A64": chain["a64"], "A32": chain["a32"], "golden32": golden32,
            "uplo": "L", "ratio_cpu": ratio_cpu, "ratio_cpu_status": "ok"}
    v = verdict.judge(cards_cholesky.get_card("spotri"), case,
                      {"out32": np.tril(c32), "info": 0, "status": "ok"})
    assert v["error"] is None
    names = [t["name"] for t in v["layer1"]["targets"]]
    assert names == ["ainv_vs_golden", "a_ainv_vs_identity"]    # 2.3′ 双目标
    assert v["numeric"] == "PASS"
    assert v["formal"] == "PENDING_RULING"
