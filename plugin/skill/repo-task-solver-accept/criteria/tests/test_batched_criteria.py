# -*- coding: utf-8 -*-
"""S3 批量四卡（spotrfBatched/spotrsBatched/cpotrfBatched/cpotrsBatched）criteria
测试（S3 spec §4 判定卡增量 + §7 验证门 batched 行；s2-A1 一段式重基——逐矩阵
残差配对 c_i、diagnostics 收 batch/pass/fail/error/ratio_max、flags 恒空）。

覆盖清单 → 具名测试对照（每项至少一个）：

- 逐矩阵配对反例（配对线 vs max 聚合线）→ test_pairing_counterexample_paired_vs_max
- 单矩阵超限扰动指认序号              → test_single_matrix_overlimit_identifies_index
- potrsBatched 标量 info 与 prep 隔离 → test_potrs_batched_scalar_info_param_error_not_precision、
                                        test_potrs_batched_rejects_info_array、
                                        test_potrs_batched_prep_failure_isolated
- potrfBatched infoArray 逐矩阵消费   → test_potrf_batched_info_array_nonzero_identifies_matrix、
                                        test_potrf_batched_rejects_scalar_info
- batch=1 防标量化                    → test_batch1_keeps_batch_dim_and_passes、
                                        test_batch1_scalarized_inputs_rejected
- 复数批量（复模残差，flags 恒空）    → test_cpotrs_batched_pass
- 整批统计只入 diagnostics            → test_pairing_counterexample_paired_vs_max 的
                                        ratio_max 统计与 case FAIL 并存断言
- 卡接线与既有六卡零漂移              → test_batched_card_wiring
- canonical batch 对不上被测          → test_batch_field_mismatch_rejected

独立手算期望值 ≥2（带「手算」注释）：配对反例残差 DPOT01
ratio = (4δ+δ²)/(14·2⁻²⁴)（δ=0.011）；超限例残差 ratio = 21/(14·2⁻²⁴) =
1.5·2²⁴ = 25165824。
"""
import pytest

pytest.importorskip("scipy")  # spec 波 1 A 行：scipy importorskip 守卫
import numpy as np

import cards_cholesky
import thresholds
import verdict

POTRFB = cards_cholesky.get_card("spotrfBatched")
POTRSB = cards_cholesky.get_card("spotrsBatched")

# potrf 基材（与 test_judge_cards 同一 SPD 系统）：A=[[4,2],[2,5]]，L=[[2,0],[1,2]]。
A64 = np.array([[4.0, 2.0], [2.0, 5.0]])
L_GOLD = np.array([[2.0, 0.0], [1.0, 2.0]])
# potrs 基材（与 test_judge_flow 同一系统，nrhs=1）：A=diag(2,4)，X=[1,2]ᵀ，B=[2,8]ᵀ。
AS = np.array([[2.0, 0.0], [0.0, 4.0]])
XS = np.array([[1.0], [2.0]])
BS = np.array([[2.0], [8.0]])
# 复数基材（与 test_complex_criteria 同一 HPD 系统）：B = A·X 精确。
CA = np.array([[4.0, 2.0j], [-2.0j, 5.0]], dtype=np.complex128)
CX = np.array([[1.0], [1.0j]], dtype=np.complex128)
CB = CA @ CX


def _stack(mat, batch):
    return np.stack([np.asarray(mat)] * batch)


def _potrfb_case(batch, ratio_cpu, mean=0.0, uplo="L"):
    # golden64/golden32 仍随包交付、照常切片，但判定不消费（s2-A1 降为参考件）。
    return {"A64": _stack(A64, batch), "A32": _stack(A64, batch),
            "golden64": _stack(L_GOLD, batch), "golden32": _stack(L_GOLD, batch),
            "uplo": uplo, "batch": batch,
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


def _dutb(out_stack, info=None, status="ok"):
    """potrfBatched 族被测：info 缺省给全 0 infoArray（int32 (batch,)，S3 spec §4）。"""
    out = np.asarray(out_stack)
    if info is None:
        info = np.zeros(out.shape[0], dtype=np.int32)
    return {"out32": out, "info": info, "status": status}


def _dutsb(out_stack, info=0, status="ok"):
    """potrsBatched 族被测：info 缺省标量 0（仅报参数错，任务书接口说明第 69 行）。"""
    return {"out32": np.asarray(out_stack), "info": info, "status": status}


def test_pairing_counterexample_paired_vs_max():
    """逐矩阵配对反例（S3 spec §3/§4）：矩阵 0 微扰 c₀=1000、矩阵 1 精确
    c₁=20000，case 内均值 mean=10500。

    手算（δ=0.011，out[1,1]=2+δ）：recon−A 只在 (1,1) 差 4δ+δ²=0.044121，
    DPOT01 ratio = 0.044121/(2·7·2⁻²⁴) = 0.044121/(14·2⁻²⁴) ≈ 5.29e4。
    配对判定：f(c₀)=max(5·1000, 3·10500)=31500 < ratio → 矩阵 0 FAIL → case FAIL。
    反例锋面：若做 max 聚合（阈值取 f(c₁)=max(5·20000, 3·10500)=100000），
    ratio < 100000 会被放过——差矩阵的 c 放宽他家阈值，正是评审否决的聚合。"""
    delta = 0.011
    out0 = L_GOLD.copy()
    out0[1, 1] += delta
    v = verdict.judge(POTRFB, _potrfb_case(2, [1000.0, 20000.0], mean=10500.0),
                      _dutb(np.stack([out0, L_GOLD])))
    assert v["numeric"] == "FAIL"
    assert v["batch"] == 2 and v["fail_count"] == 1
    assert v["first_fail_index"] == 0 and v["worst_index"] == 0
    assert v["error"] is None                       # 可裁的精度 FAIL，非证据问题
    assert v["flags"] == []                         # HT-16：T8 摘除，flags 恒空

    worst = v["worst"]
    expected = (4 * delta + delta * delta) / (2 * 7 * thresholds.EPS32)   # 手算
    res = worst["residual"]
    assert res["ran"] is True
    assert res["ratio"] == pytest.approx(expected, rel=1e-12)
    assert res["threshold"] == 31500.0              # 配对 f(c₀)，不是 f(max c)
    assert res["formula"] == thresholds.POTRF_POTRS_FORMULA
    assert worst["numeric"] == "FAIL"
    assert thresholds.potrf_potrs_threshold(20000.0, 10500.0) == 100000.0
    assert res["ratio"] < 100000.0                  # max 聚合会放过 → 反例成立

    d = v["diagnostics"]                            # 整批统计只入 diagnostics
    assert d["pass_count"] == 1 and d["fail_count"] == 1 and d["error_count"] == 0
    assert d["ratio_max"] == pytest.approx(expected, rel=1e-12)   # 统计与 FAIL 并存


def test_single_matrix_overlimit_identifies_index():
    """负例承诺（S3 spec §4）：单矩阵扰动超出判据允许范围 → 整 case 数值 FAIL
    且报告指认矩阵序号。

    batch=3，矩阵 2 因子整体 ×2：recon=4A → 残差手算 ‖3A‖₁ 存储侧镜像口径 = 21
    → ratio = 21/(2·7·2⁻²⁴) = 1.5·2²⁴ = 25165824 > 阈值 max(5·0, 3·0)=0 → FAIL。
    矩阵 0/1 精确。"""
    v = verdict.judge(POTRFB, _potrfb_case(3, [0.0, 0.0, 0.0]),
                      _dutb(np.stack([L_GOLD, L_GOLD, 2.0 * L_GOLD])))
    assert v["numeric"] == "FAIL"
    assert v["batch"] == 3 and v["fail_count"] == 1
    assert v["first_fail_index"] == 2 and v["worst_index"] == 2
    worst = v["worst"]
    assert worst["residual"]["ratio"] == 25165824.0        # 手算精确值 1.5·2²⁴
    assert worst["residual"]["threshold"] == 0.0           # max(5·0, 3·0)（HT-3 双零）
    assert v["diagnostics"]["pass_count"] == 2
    assert v["diagnostics"]["ratio_max"] == 25165824.0


def test_potrs_batched_exact_passes_with_scalar_info():
    """potrsBatched 正例：nrhs=1 批量精确解 → 逐矩阵残差 0 全过、数值 PASS；标量
    info=0 是该接口的合法形态（任务书接口说明第 69 行）。"""
    v = verdict.judge(POTRSB, _potrsb_case(2, [0.0, 0.0]), _dutsb(_stack(XS, 2)))
    assert v["error"] is None and v["numeric"] == "PASS"
    assert v["batch"] == 2 and v["fail_count"] == 0
    assert v["first_fail_index"] is None
    assert v["flags"] == []                         # HT-16：flags 恒空
    assert v["worst"]["numeric"] == "PASS"          # 全过时 worst 仍给一行明细
    assert v["worst"]["residual"]["ratio"] == 0.0


def test_potrs_batched_scalar_info_param_error_not_precision():
    """info 分型（potrsBatched=标量仅报参数错，S3 spec §4）：info=-2 → error
    verdict——证据/用法问题（error 非空即隔离标记），不得当精度 FAIL 上报。"""
    v = verdict.judge(POTRSB, _potrsb_case(2, [0.0, 0.0]),
                      _dutsb(_stack(XS, 2), info=-2))
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None and "info" in v["error"]
    assert v["fail_count"] is None and v["worst"] is None   # 未逐矩阵开裁
    assert v["flags"] == []


def test_potrs_batched_rejects_info_array():
    """info 分型（fail-closed）：前置分解的 infoArray 喂进 potrsBatched 属用法错
    ——正定性由 prep 的 infoArray 反映，不进本卡（S3 spec §4 硬边界 5）。"""
    v = verdict.judge(POTRSB, _potrsb_case(2, [0.0, 0.0]),
                      _dutsb(_stack(XS, 2), info=np.zeros(2, dtype=np.int32)))
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None and "标量" in v["error"]


def test_potrs_batched_prep_failure_isolated():
    """prep 隔离（S3 spec §4 硬边界 5）：status="prep_failed"（前置分解失败）→
    error verdict，归 prep 不归目标接口；error 非空 = 不可裁，绝不是目标算子的
    精度结论。"""
    v = verdict.judge(POTRSB, _potrsb_case(2, [0.0, 0.0]),
                      _dutsb(_stack(XS, 2), status="prep_failed"))
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None and "prep_failed" in v["error"]
    assert v["worst"] is None and v["diagnostics"] is None


def test_potrf_batched_info_array_nonzero_identifies_matrix():
    """info 分型（potrfBatched=infoArray 逐矩阵消费）：info=[0,3] → 矩阵 1 记
    error（被测自称非正定），case FAIL 且 error 指认「矩阵 1」。"""
    v = verdict.judge(POTRFB, _potrfb_case(2, [0.0, 0.0]),
                      _dutb(_stack(L_GOLD, 2), info=np.array([0, 3], dtype=np.int32)))
    assert v["numeric"] == "FAIL"
    assert v["fail_count"] == 1 and v["first_fail_index"] == 1
    assert v["worst_index"] == 1
    assert v["error"] is not None and "矩阵 1" in v["error"] and "info" in v["error"]
    assert v["diagnostics"]["error_count"] == 1


def test_potrf_batched_rejects_scalar_info():
    """info 分型（fail-closed）：potrfBatched 的 info 是 (batch,) infoArray，
    标量 info 即接口错喂 → error verdict。"""
    v = verdict.judge(POTRFB, _potrfb_case(2, [0.0, 0.0]),
                      _dutb(_stack(L_GOLD, 2), info=0))
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None and "infoArray" in v["error"]


def test_batch1_keeps_batch_dim_and_passes():
    """batch=1 防标量化（正面）：三维 out32 (1,n,n)、(1,) infoArray、(1,)
    ratio_cpu → 正常逐矩阵裁决 PASS，batch=1 如实入报。"""
    v = verdict.judge(POTRFB, _potrfb_case(1, [0.0]), _dutb(L_GOLD[None, :, :]))
    assert v["error"] is None and v["numeric"] == "PASS"
    assert v["batch"] == 1 and v["fail_count"] == 0 and v["worst_index"] == 0
    assert v["flags"] == []


def test_batch1_scalarized_inputs_rejected():
    """batch=1 防标量化（反面，S3 spec §4）：二维 out32、标量 info、标量 ratio_cpu
    任何一处被标量化都 error verdict，不得静默按单矩阵裁（防误掩测试）。"""
    case = _potrfb_case(1, [0.0])
    v = verdict.judge(POTRFB, case, {"out32": L_GOLD.copy(),
                                     "info": np.zeros(1, dtype=np.int32),
                                     "status": "ok"})
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None and "三维" in v["error"]

    v = verdict.judge(POTRFB, case, _dutb(L_GOLD[None, :, :], info=0))
    assert v["numeric"] == "FAIL" and "infoArray" in v["error"]

    case_scalar_ratio = _potrfb_case(1, [0.0])
    case_scalar_ratio["ratio_cpu"] = 0.0
    v = verdict.judge(POTRFB, case_scalar_ratio, _dutb(L_GOLD[None, :, :]))
    assert v["numeric"] == "FAIL" and "ratio_cpu" in v["error"]


def test_batch_field_mismatch_rejected():
    """canonical batch 与被测 out32 首维不一致 → error verdict（错包/错输出
    对接的兜网，抓无意错喂）。"""
    case = _potrfb_case(2, [0.0, 0.0])
    case["batch"] = 3
    v = verdict.judge(POTRFB, case, _dutb(_stack(L_GOLD, 2)))
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None and "不一致" in v["error"]


def test_cpotrs_batched_pass():
    """复数批量（cpotrsBatched）：精确解逐矩阵残差 0 PASS；复模与共轭语义由
    residual_ratio 的 dtype 分流在批内逐矩阵完整成立（HT-16 flags 恒空）。"""
    case = {"A64": np.stack([CA, CA]), "A32": np.stack([CA, CA]),
            "B64": np.stack([CB, CB]), "B32": np.stack([CB, CB]),
            "golden64": np.stack([CX, CX]), "golden32": np.stack([CX, CX]),
            "batch": 2, "ratio_cpu": np.zeros(2), "ratio_cpu_status": "ok",
            "ratio_cpu_mean": 0.0}
    v = verdict.judge(cards_cholesky.get_card("cpotrsBatched"), case,
                      _dutsb(np.stack([CX, CX])))
    assert v["error"] is None and v["numeric"] == "PASS"
    assert v["batch"] == 2
    assert v["flags"] == []


def test_batched_card_wiring():
    """批量卡接线（S3 spec §4，s2-A1 成员名）：四卡注册、批维身份与 info 分型
    对号，判据成员与基卡逐字段同一（零新判据实现）；既有六卡身份零漂移；
    potri 无 batched 接口。"""
    expect = {"spotrfBatched": ("spotrf", "array"),
              "spotrsBatched": ("spotrs", "scalar"),
              "cpotrfBatched": ("cpotrf", "array"),
              "cpotrsBatched": ("cpotrs", "scalar")}
    assert cards_cholesky.BATCHED_OPS == tuple(expect)
    for op, (base_op, info_kind) in expect.items():
        card = cards_cholesky.get_card(op)
        base = cards_cholesky.get_card(base_op)
        assert card.batched is True and card.info_kind == info_kind
        assert card.base_op == base_op and card.op == op
        assert card.residual_kind == base.residual_kind
        assert card.formula == base.formula
        assert card.threshold_fn is base.threshold_fn
        assert card.uses_mean is base.uses_mean
        assert card.residual_kwargs is base.residual_kwargs
    for op in cards_cholesky.OPS:
        card = cards_cholesky.get_card(op)
        assert card.batched is False and card.info_kind == "scalar"
        assert card.base_op is None
    with pytest.raises(ValueError):
        cards_cholesky.get_card("spotriBatched")
