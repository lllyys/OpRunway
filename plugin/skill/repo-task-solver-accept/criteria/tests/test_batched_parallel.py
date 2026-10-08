# -*- coding: utf-8 -*-
"""S3 并行裁定的机械断言（D3 卡）：批量逐矩阵判定按矩阵区间多进程分块后，
batched_parallel.judge_parallel 的输出与 verdict.judge 串行**逐位相同**。

覆盖清单 → 具名测试对照：

- 混合过/不过批的并行等价（含逐矩阵配对兜底）    → test_parallel_equals_serial_mixed
- 逐矩阵 error 的全局序号前缀（跨区间平移）      → test_parallel_error_index_shift
- potrsBatched 标量 info 通路并行等价            → test_potrs_scalar_parallel_equals_serial
- batch=1 / jobs>batch 的退化                    → test_batch1_and_jobs_over_batch
- 结构错误（case 级 error verdict）回退串行      → test_structural_error_falls_back_serial
- 非批量卡直通 verdict.judge                     → test_non_batched_passthrough
- 自检环境变量（OPRUNWAY_BATCHED_SELFCHECK）     → test_selfcheck_env_passes
- 区间切分不重不漏                               → test_split_bounds_covers

等价断言用 dict ==（浮点不做近似——同一实现同一输入，要求逐位相同）。
case 基材与 flags 口径按阶段 1 新 A6 重基（golden64 基准、HT-3 mean 第二支、
HT-16 flags 恒空）；混合批扰动幅度取 δ=0.011（A6 单套容差 2⁻¹³ + 动态锚点门
max_abs>0.01 才触发兜底，旧微扰 56·2⁻²⁴ 在 A6 下直接过 layer1 不再 FAIL）。
"""
import numpy as np
import pytest

import batched_parallel
import cards_cholesky
import verdict

POTRFB = cards_cholesky.get_card("spotrfBatched")
POTRSB = cards_cholesky.get_card("spotrsBatched")

# 与 test_batched_criteria 同一基材：A=[[4,2],[2,5]]，L=[[2,0],[1,2]]；
# potrs 基材 A=diag(2,4)，X=[1,2]ᵀ，B=[2,8]ᵀ。
A64 = np.array([[4.0, 2.0], [2.0, 5.0]])
L_GOLD = np.array([[2.0, 0.0], [1.0, 2.0]])
AS = np.array([[2.0, 0.0], [0.0, 4.0]])
XS = np.array([[1.0], [2.0]])
BS = np.array([[2.0], [8.0]])


def _stack(mat, batch):
    return np.stack([np.asarray(mat)] * batch)


def _potrfb_case(batch, ratio_cpu, mean=0.0):
    return {"A64": _stack(A64, batch), "A32": _stack(A64, batch),
            "golden64": _stack(L_GOLD, batch), "golden32": _stack(L_GOLD, batch),
            "uplo": "L", "batch": batch,
            "ratio_cpu": np.asarray(ratio_cpu, dtype=np.float64),
            "ratio_cpu_status": "ok",
            "ratio_cpu_mean": mean}      # HT-3：potrf 阈值第二支（批内共享标量）


def _potrsb_case(batch, ratio_cpu, mean=0.0):
    return {"A64": _stack(AS, batch), "A32": _stack(AS, batch),
            "B64": _stack(BS, batch), "B32": _stack(BS, batch),
            "golden64": _stack(XS, batch), "golden32": _stack(XS, batch),
            "batch": batch,
            "ratio_cpu": np.asarray(ratio_cpu, dtype=np.float64),
            "ratio_cpu_status": "ok",
            "ratio_cpu_mean": mean}


def _mixed_dut(batch):
    """混合批：矩阵 1 与 batch-1 各带一处扰动（δ=0.011 越动态锚点门 → 兜底
    FAIL），其余精确。mean=0 时阈值 = 5·ratio_cpu ≤ 15 ≪ ratio≈5.29e4，
    扰动矩阵必 FAIL、精确矩阵必 PASS。"""
    outs = [L_GOLD.copy() for _ in range(batch)]
    delta = 0.011
    outs[1][1, 1] += delta
    outs[batch - 1][1, 1] += delta
    return {"out32": np.stack(outs),
            "info": np.zeros(batch, dtype=np.int32), "status": "ok"}


@pytest.mark.parametrize("jobs", [2, 3, 5, 8])
def test_parallel_equals_serial_mixed(jobs):
    """并行裁定主断言：混合过/不过批在各分块数下与串行逐位相同。"""
    batch = 5
    case = _potrfb_case(batch, [0.01, 0.01, 3.0, 3.0, 0.01])
    dut = _mixed_dut(batch)
    serial = verdict.judge(POTRFB, case, dut)
    parallel = batched_parallel.judge_parallel(POTRFB, case, dut, jobs=jobs)
    assert parallel == serial
    # 断言基准自身有效：确实是有失败有通过的混合批（A6 下 flags 恒空，HT-16）。
    assert (serial["numeric"] == "FAIL" and serial["flags"] == []
            and 0 < serial["fail_count"] < batch)
    assert serial["first_fail_index"] == 1


def test_parallel_error_index_shift():
    """infoArray 非 0 落在第二个区间：first_error 的矩阵序号必须是全局序号。"""
    batch = 6
    case = _potrfb_case(batch, [0.01] * batch)
    info = np.zeros(batch, dtype=np.int32)
    info[4] = 2                       # 第 4 个矩阵报非正定 → 该矩阵 error
    dut = {"out32": _stack(L_GOLD, batch), "info": info, "status": "ok"}
    serial = verdict.judge(POTRFB, case, dut)
    parallel = batched_parallel.judge_parallel(POTRFB, case, dut, jobs=3)
    assert parallel == serial
    assert serial["error"] is not None and serial["error"].startswith("矩阵 4:")


def test_potrs_scalar_parallel_equals_serial():
    """potrsBatched 标量 info 通路（info 不切片、逐区间原样透传）并行等价。"""
    batch = 3
    case = _potrsb_case(batch, [0.5] * batch)
    dut = {"out32": _stack(XS, batch), "info": 0, "status": "ok"}
    serial = verdict.judge(POTRSB, case, dut)
    parallel = batched_parallel.judge_parallel(POTRSB, case, dut, jobs=2)
    assert parallel == serial
    assert serial["numeric"] == "PASS" and serial["flags"] == []


def test_batch1_and_jobs_over_batch():
    """batch=1（无可切分区间）与 jobs>batch 都必须与串行逐位相同。"""
    case = _potrfb_case(1, [0.01])
    dut = {"out32": _stack(L_GOLD, 1),
           "info": np.zeros(1, dtype=np.int32), "status": "ok"}
    serial = verdict.judge(POTRFB, case, dut)
    for jobs in (1, 4):
        assert batched_parallel.judge_parallel(POTRFB, case, dut, jobs=jobs) == serial
    assert serial["numeric"] == "PASS" and serial["batch"] == 1


def test_structural_error_falls_back_serial():
    """结构错误（out32 二维标量化）走回退：输出就是串行的规范 error verdict。"""
    case = _potrfb_case(2, [0.01, 0.01])
    dut = {"out32": L_GOLD, "info": np.zeros(2, dtype=np.int32), "status": "ok"}
    serial = verdict.judge(POTRFB, case, dut)
    parallel = batched_parallel.judge_parallel(POTRFB, case, dut, jobs=2)
    assert parallel == serial
    assert serial["fail_count"] is None and serial["error"] is not None


def test_non_batched_passthrough():
    """非批量卡：judge_parallel 是 verdict.judge 的直通（既有六算子零漂移）。"""
    card = cards_cholesky.get_card("spotrf")
    case = {"A64": A64, "A32": A64, "golden64": L_GOLD,
            "uplo": "L", "ratio_cpu": 0.01, "ratio_cpu_status": "ok",
            "ratio_cpu_mean": 0.0}
    dut = {"out32": L_GOLD.copy(), "info": 0, "status": "ok"}
    assert (batched_parallel.judge_parallel(card, case, dut, jobs=8)
            == verdict.judge(card, case, dut))


def test_selfcheck_env_passes(monkeypatch):
    """OPRUNWAY_BATCHED_SELFCHECK 非空时并行路径内建串行全比对不触发断言。"""
    monkeypatch.setenv("OPRUNWAY_BATCHED_SELFCHECK", "1")
    batch = 4
    case = _potrfb_case(batch, [0.01, 3.0, 0.01, 3.0])
    dut = _mixed_dut(batch)
    parallel = batched_parallel.judge_parallel(POTRFB, case, dut, jobs=3)
    assert parallel == verdict.judge(POTRFB, case, dut)


@pytest.mark.parametrize("batch,jobs", [(1, 1), (5, 2), (5, 3), (7, 7), (7, 20)])
def test_split_bounds_covers(batch, jobs):
    """矩阵区间切分：连续、不重不漏覆盖 [0, batch)，段数不超过 min(jobs, batch)。"""
    bounds = batched_parallel.split_bounds(batch, jobs)
    assert bounds[0][0] == 0 and bounds[-1][1] == batch
    assert len(bounds) == min(jobs, batch)
    for (lo, hi), (lo2, _hi2) in zip(bounds, bounds[1:]):
        assert hi == lo2 and hi > lo
    assert bounds[-1][1] > bounds[-1][0]
