# -*- coding: utf-8 -*-
"""spotrf/spotri 判据卡测试（s2-A1 一段式）：残差换基（A32）、存储侧口径、
U/L 双侧、扰动反例、fail-closed 与卡接线。"""
import pytest

pytest.importorskip("scipy")  # spec 波 1 A 行：scipy importorskip 守卫
import numpy as np

import cards_cholesky
import thresholds
import verdict

POTRF = cards_cholesky.get_card("spotrf")
POTRI = cards_cholesky.get_card("spotri")

# A=[[4,2],[2,5]]（SPD），准确因子 L=[[2,0],[1,2]]、U=Lᵀ。
A64 = np.array([[4.0, 2.0], [2.0, 5.0]])
L_GOLD = np.array([[2.0, 0.0], [1.0, 2.0]])
U_GOLD = np.array([[2.0, 1.0], [0.0, 2.0]])


def _potrf_case(uplo, ratio_cpu=0.0, status="ok", ratio_cpu_mean=0.0):
    return {"A32": A64.copy(), "uplo": uplo,
            "ratio_cpu": ratio_cpu, "ratio_cpu_status": status,
            "ratio_cpu_mean": ratio_cpu_mean}   # HT-3：potrf 阈值第二支


def _dut(out, info=0, status="ok"):
    return {"out32": np.asarray(out, dtype=np.float64), "info": info, "status": status}


def test_spotrf_pass_lower_ignores_unstored_side():
    """U/L 双侧之 L + 存储侧口径：被测非存储侧塞垃圾值，DPOT01 只取存储侧半三角
    参与还原 → 残差 0 → PASS，垃圾位不进比对。"""
    out = L_GOLD.copy()
    out[0, 1] = 777.0                       # uplo=L 的上三角是输入残留位，无效
    v = verdict.judge(POTRF, _potrf_case("L"), _dut(out))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert v["residual"]["ratio"] == 0.0
    assert v["residual"]["formula"] == thresholds.POTRF_POTRS_FORMULA


def test_spotrf_pass_upper_ignores_unstored_side():
    """U/L 双侧之 U：同一矩阵的上三角因子路径（还原取 Uᵀ·U）。"""
    out = U_GOLD.copy()
    out[1, 0] = -888.0
    v = verdict.judge(POTRF, _potrf_case("U"), _dut(out))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert v["residual"]["ratio"] == 0.0


def test_spotrf_residual_uses_a32_not_a64():
    """s2-A1 换基证明：case 同时带错误的 A64 与正确的 A32，残差只消费 A32 →
    PASS（若仍按旧口径吃 A64，残差会打满 FAIL）。"""
    case = _potrf_case("L")
    case["A64"] = np.eye(2) * 1e6           # 错误 A64：旧口径下残差必超阈
    v = verdict.judge(POTRF, case, _dut(L_GOLD.copy()))
    assert v["error"] is None and v["numeric"] == "PASS"
    assert v["residual"]["ratio"] == 0.0


def test_spotrf_sign_ambiguous_factor_passes():
    """一段式语义：因子 [[2,0],[1,-2]] 与标准因子符号不同，但 L·Lᵀ 精确等于 A →
    残差 0 → PASS。残差审的是重构正确性，不是元素逐位命中（符号歧义无害）。"""
    out = np.array([[2.0, 0.0], [1.0, -2.0]])       # fh @ fh.T == A64
    v = verdict.judge(POTRF, _potrf_case("L"), _dut(out))
    assert v["error"] is None and v["numeric"] == "PASS"
    assert v["residual"]["ratio"] == 0.0


def test_spotrf_zero_output_fails_hand_value():
    """手算（zero 扰动）：零因子 → recon=0，分子 ‖A‖₁=7 →
    ratio = 7/(2·7·2^-24) = 2^23 = 8388608；阈值 max(5·0, 3·0)=0 → 数值 FAIL。"""
    v = verdict.judge(POTRF, _potrf_case("L"), _dut(np.zeros((2, 2))))
    res = v["residual"]
    assert res["ran"] is True
    assert res["ratio"] == 8388608.0                       # 手算精确值 2^23
    assert res["threshold"] == 0.0
    assert v["numeric"] == "FAIL"
    assert v["error"] is None                              # 可裁的精度 FAIL，非证据问题


def test_spotrf_scale_perturbation_fails():
    """scale 扰动：因子整体 ×1.001 → recon 偏 ×1.002001，残差远超阈值 → FAIL。"""
    v = verdict.judge(POTRF, _potrf_case("L"), _dut(L_GOLD * 1.001))
    assert v["numeric"] == "FAIL"
    assert v["residual"]["ratio"] > v["residual"]["threshold"]


def test_missing_a32_yields_error_verdict():
    """残差件 A32 缺失（spotrf 卡）：残差不可计算 → 数值 FAIL、error 指认缺键
    （fail-closed，judge 不外抛）。"""
    case = _potrf_case("L")
    del case["A32"]
    v = verdict.judge(POTRF, case, _dut(L_GOLD.copy()))
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None and "A32" in v["error"]


def test_spotri_pass_ignores_unstored_side():
    """spotri 正例：A=diag(2,4) 的准确逆 diag(0.5,0.25) → DPOT03 残差 0 → PASS；
    非存储侧垃圾不进比对（双半三角镜像口径）。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]])
    out = np.array([[0.5, 0.0], [0.0, 0.25]])
    out[0, 1] = 66.0
    case = {"A32": a.copy(), "uplo": "L", "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(POTRI, case, _dut(out))
    assert v["error"] is None and v["numeric"] == "PASS"
    assert v["residual"]["ratio"] == 0.0
    assert v["residual"]["threshold"] == 0.1               # max(5·0, 0.1)（HT-5）


def test_spotri_fail_hand_value():
    """手算：dut 逆 diag(0.5, 0.375) → DPOT03 ratio = 0.5/(2·4·0.5·2^-24) =
    2^21 = 2097152 > 绝对线 0.1（ratio_cpu=0 → max(5·0, 0.1)，HT-5）→ 数值 FAIL。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]])
    out = np.array([[0.5, 0.0], [0.0, 0.375]])
    case = {"A32": a.copy(), "uplo": "L", "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(POTRI, case, _dut(out))
    res = v["residual"]
    assert res["ratio"] == 2097152.0                       # 手算精确值 2^21
    assert res["threshold"] == 0.1                         # HT-5：potri 绝对线
    assert res["formula"] == thresholds.POTRI_FORMULA
    assert v["numeric"] == "FAIL"


def test_spotri_zero_output_zero_denominator_fails_closed():
    """fail-closed（任务书边界）：potri 全零输出 → DPOT03 的 ‖C‖₁=0 零分母 →
    残差不可计算，数值 FAIL、error 指认零分母（不崩溃不放行）。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]])
    case = {"A32": a.copy(), "uplo": "L", "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(POTRI, case, _dut(np.zeros((2, 2))))
    assert v["numeric"] == "FAIL"
    assert v["residual"]["ran"] is True and v["residual"]["ratio"] is None
    assert v["error"] is not None and "零分母" in v["error"]


def test_card_wiring():
    """卡接线（s2-A1 成员收敛后）：残差 kind、阈值公式、mean 消费声明与残差喂数
    成员逐卡对号；layer1 成员已删除。"""
    assert POTRF.residual_kind == "DPOT01"
    assert POTRF.formula == thresholds.POTRF_POTRS_FORMULA
    assert POTRF.threshold_fn(0.5, 1.0) == 3.0          # 均值线主导
    assert POTRF.uses_mean is True
    potrs = cards_cholesky.get_card("spotrs")
    assert potrs.residual_kind == "DPOT02"
    assert potrs.formula == thresholds.POTRF_POTRS_FORMULA
    assert potrs.threshold_fn(0.5, 0.0) == 2.5          # 相对线主导
    assert potrs.uses_mean is True
    assert POTRI.residual_kind == "DPOT03"
    assert POTRI.formula == thresholds.POTRI_FORMULA
    assert POTRI.threshold_fn(0.0) == 0.1
    assert POTRI.threshold_fn(2.0) == 10.0
    assert POTRI.uses_mean is False                     # mean 线结构性不适用（HT-5）
    for card in (POTRF, potrs, POTRI):
        assert callable(card.residual_kwargs)
        assert not hasattr(card, "layer1_targets")      # s2-A1：layer1 成员删除
        assert not hasattr(card, "layer1_diagnostics")


def test_get_card_unknown_op_raises():
    with pytest.raises(ValueError):
        cards_cholesky.get_card("sgetrf")
    with pytest.raises(ValueError):
        cards_cholesky.get_card("dpotrf")   # canonical 是 s 前缀（spec §0）
