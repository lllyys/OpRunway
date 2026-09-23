# -*- coding: utf-8 -*-
"""spotrf/spotri 判据卡测试：2.3′ 比对目标（还原主判、诊断降级、potri 双目标）、
存储侧口径、U/L 双侧、扰动反例与卡接线。"""
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


def _potrf_case(uplo, golden, ratio_cpu=0.0, status="ok"):
    return {"A64": A64.copy(), "A32": A64.copy(), "golden32": golden.copy(),
            "uplo": uplo, "ratio_cpu": ratio_cpu, "ratio_cpu_status": status}


def _dut(out, info=0, status="ok"):
    return {"out32": np.asarray(out, dtype=np.float64), "info": info, "status": status}


def test_spotrf_pass_lower_ignores_unstored_side():
    """U/L 双侧之 L + 存储侧口径：被测非存储侧塞垃圾值，还原只用存储侧因子，
    recon = L·Lᵀ = A 逐位命中 → 主判目标（recon_vs_A，spec §2.3′）PASS。"""
    out = L_GOLD.copy()
    out[0, 1] = 777.0                       # uplo=L 的上三角是输入残留位，无效
    v = verdict.judge(POTRF, _potrf_case("L", L_GOLD), _dut(out))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert v["layer1"]["max_abs"] == 0.0
    assert v["layer1"]["targets"][0]["name"] == "recon_vs_A"
    assert v["fallback"]["ran"] is False


def test_spotrf_pass_upper_ignores_unstored_side():
    """U/L 双侧之 U：同一矩阵的上三角因子路径（recon = Uᵀ·U）。"""
    out = U_GOLD.copy()
    out[1, 0] = -888.0
    v = verdict.judge(POTRF, _potrf_case("U", U_GOLD), _dut(out))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert v["fallback"]["ran"] is False


def test_spotrf_recon_primary_factor_only_diagnostic():
    """2.3′ 比对目标：主判是还原对原 A，「F vs golden F」只是诊断。

    因子 [[2,0],[1,-2]] 与 golden 因子在 (1,1) 差 4，但 L·Lᵀ 仍精确等于 A →
    主判 PASS 不走兜底；诊断项如实并报 factor 失配，不影响裁决。"""
    out = np.array([[2.0, 0.0], [1.0, -2.0]])       # 还原验证：fh@fh.T == A64
    v = verdict.judge(POTRF, _potrf_case("L", L_GOLD), _dut(out))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert v["layer1"]["max_abs"] == 0.0            # recon_vs_A 逐位命中
    assert v["fallback"]["ran"] is False
    diag = v["layer1"]["diagnostics"][0]
    assert diag["name"] == "factor_vs_golden"
    assert diag["matched_ratio"] == pytest.approx(2 / 3)   # (1,1) 元素失配
    assert diag["max_abs"] == 4.0


def test_spotrf_missing_golden32_only_degrades_diagnostic():
    """诊断降级语义：golden32 缺失只让诊断项记 error，主判照常 PASS。"""
    case = _potrf_case("L", L_GOLD)
    del case["golden32"]
    v = verdict.judge(POTRF, case, _dut(L_GOLD.copy()))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    diag = v["layer1"]["diagnostics"][0]
    assert "error" in diag and "golden32" in diag["error"]


def test_spotrf_zero_output_fails_hand_value():
    """手算 #6（zero 扰动）：零因子 → recon=0 对 A 全不符（matched_ratio=0）；
    兜底 DPOT01 分子 ‖A‖₁=7 → ratio = 7/(2·7·2^-24) = 2^23 = 8388608；
    阈值取地板 1（ratio_cpu=0）→ 数值 FAIL。"""
    v = verdict.judge(POTRF, _potrf_case("L", L_GOLD), _dut(np.zeros((2, 2))))
    assert v["layer1"]["matched_ratio"] == 0.0             # 存储侧 3 元素全不符
    fb = v["fallback"]
    assert fb["ran"] is True
    assert fb["ratio"] == 8388608.0                        # 手算精确值 2^23
    assert fb["threshold"] == 1.0
    assert fb["formula"] == thresholds.TIGHTEN_FORMULA
    assert v["numeric"] == "FAIL"


def test_spotrf_scale_perturbation_fails():
    """scale 扰动：因子整体 ×1.001 → recon 偏 ×1.002001，layer1 全不符、
    DPOT01 兜底也远超阈值 → FAIL。"""
    v = verdict.judge(POTRF, _potrf_case("L", L_GOLD), _dut(L_GOLD * 1.001))
    assert v["numeric"] == "FAIL"
    assert v["fallback"]["ran"] is True
    assert v["fallback"]["ratio"] > v["fallback"]["threshold"]


def test_spotri_pass_ignores_unstored_side():
    """spotri 正例（双目标都过）：A=diag(2,4) 的准确逆 diag(0.5,0.25)，
    直审逐位命中且 A·A⁻¹=I 精确；非存储侧垃圾不进任一目标。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]])
    golden = np.array([[0.5, 0.0], [0.0, 0.25]])
    out = golden.copy()
    out[0, 1] = 66.0
    case = {"A64": a.copy(), "A32": a.copy(), "golden32": golden, "uplo": "L",
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(POTRI, case, _dut(out))
    assert v["error"] is None and v["numeric"] == "PASS"
    names = [t["name"] for t in v["layer1"]["targets"]]
    assert names == ["ainv_vs_golden", "a_ainv_vs_identity"]


def test_spotri_fallback_hand_value():
    """手算 #7：dut 逆 diag(0.5, 0.375) → 双目标皆挂（直审差 0.125、A·A⁻¹ 对 I
    差 0.5）；兜底 DPOT03 ratio = 0.5/(2·4·0.5·2^-24) = 2^21 = 2097152 > 地板 1
    → 数值 FAIL。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]])
    golden = np.array([[0.5, 0.0], [0.0, 0.25]])
    out = np.array([[0.5, 0.0], [0.0, 0.375]])
    case = {"A64": a.copy(), "A32": a.copy(), "golden32": golden, "uplo": "L",
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(POTRI, case, _dut(out))
    assert v["layer1"]["pass_fixed"] is False and v["layer1"]["pass_ulp"] is False
    assert v["layer1"]["matched_ratio"] == pytest.approx(2 / 3)   # 最差目标聚合
    fb = v["fallback"]
    assert fb["ran"] is True
    assert fb["ratio"] == 2097152.0                        # 手算精确值 2^21
    assert fb["threshold"] == 1.0
    assert v["numeric"] == "FAIL"


def test_spotri_dual_target_identity_fails_direct_passes():
    """2.3′ 双目标 AND（方向 1）：直审过、A·A⁻¹ 对 I 挂 → layer1 整体不过。

    A=diag(2,65536)，golden 逆 diag(0.5,2^-16)；dut 把 (1,1) 加 1e-6：
    直审误差 1e-6 双解释都过（< 32·ULP=1.907e-6）；单位阵目标 (1,1) 误差
    65536·1e-6 ≈ 0.0655 → 挂。兜底手算：ratio = 0.065536/(2·65536·0.5·2^-24)
    = 1e-6·2^24 ≈ 16.777 > 地板 1 → 数值 FAIL。"""
    a = np.array([[2.0, 0.0], [0.0, 65536.0]])
    golden = np.array([[0.5, 0.0], [0.0, 2.0 ** -16]])
    out = golden.copy()
    out[1, 1] += 1e-6
    case = {"A64": a.copy(), "A32": a.copy(), "golden32": golden, "uplo": "L",
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(POTRI, case, _dut(out))
    direct, identity = v["layer1"]["targets"]
    assert direct["name"] == "ainv_vs_golden"
    assert direct["pass_fixed"] is True and direct["pass_ulp"] is True
    assert identity["name"] == "a_ainv_vs_identity"
    assert identity["matched_ratio"] == pytest.approx(0.75)   # 4 元素挂 1 个
    assert identity["pass_fixed"] is False
    assert v["layer1"]["pass_fixed"] is False                 # AND 聚合
    assert v["flags"] == []
    fb = v["fallback"]
    assert fb["ran"] is True
    assert fb["ratio"] == pytest.approx(1e-6 * 2 ** 24, rel=1e-9)   # 手算
    assert fb["threshold"] == 1.0
    assert v["numeric"] == "FAIL"


def test_spotri_dual_target_direct_fails_identity_passes():
    """2.3′ 双目标 AND（方向 2）：A·A⁻¹ 对 I 过、直审挂 → layer1 整体仍不过。

    A=diag(1e-3,1e-3)，golden 逆 diag(1000,1000)；dut 存储侧 (1,0) 塞 1e-3：
    直审 golden 为 0 处误差 1e-3 » atol → 挂（matched 2/3）；单位阵目标
    误差 1e-3·1e-3 = 1e-6 < atol 且 < 32·ULP → 过。兜底 DPOT03 手算：
    ratio = 1e-6/(2·1e-3·1000.001·2^-24) ≈ 8.389 > 地板 1 → 数值 FAIL。"""
    a = np.array([[1e-3, 0.0], [0.0, 1e-3]])
    golden = np.array([[1000.0, 0.0], [0.0, 1000.0]])
    out = golden.copy()
    out[1, 0] = 1e-3
    case = {"A64": a.copy(), "A32": a.copy(), "golden32": golden, "uplo": "L",
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(POTRI, case, _dut(out))
    direct, identity = v["layer1"]["targets"]
    assert direct["matched_ratio"] == pytest.approx(2 / 3)
    assert direct["pass_fixed"] is False
    assert identity["matched_ratio"] == 1.0
    assert identity["pass_fixed"] is True and identity["pass_ulp"] is True
    assert v["layer1"]["pass_fixed"] is False                 # AND 聚合
    assert v["flags"] == []
    fb = v["fallback"]
    assert fb["ran"] is True
    expected = (1e-3 * 1e-3) * 2.0 ** 24 / (2 * 1e-3 * (1000.0 + 1e-3))   # 手算
    assert fb["ratio"] == pytest.approx(expected, rel=1e-9)
    assert v["numeric"] == "FAIL"


def test_missing_a64_yields_error_verdict():
    """主判目标需要 A64（spotrf 卡，spec §2.3′）：缺键收敛为 error verdict，不外抛。"""
    case = _potrf_case("L", L_GOLD)
    del case["A64"]
    v = verdict.judge(POTRF, case, _dut(np.zeros((2, 2))))
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None and "A64" in v["error"]


def test_card_wiring():
    """卡接线（spec §2.3/§2.3′）：残差 kind、阈值公式与目标/诊断成员逐卡对号。"""
    assert POTRF.residual_kind == "DPOT01"
    assert POTRF.fallback_formula == thresholds.TIGHTEN_FORMULA
    assert POTRF.fallback_threshold(0.0) == 1.0
    potrs = cards_cholesky.get_card("spotrs")
    assert potrs.residual_kind == "DPOT02"
    assert potrs.fallback_formula == thresholds.FLOATUP_FORMULA
    assert potrs.fallback_threshold(0.0) == 30.0
    assert POTRI.residual_kind == "DPOT03"
    assert POTRI.fallback_formula == thresholds.TIGHTEN_FORMULA
    assert POTRI.fallback_threshold(0.0) == 1.0
    for card in (POTRF, potrs, POTRI):
        assert callable(card.layer1_targets) and callable(card.layer1_diagnostics)


def test_get_card_unknown_op_raises():
    with pytest.raises(ValueError):
        cards_cholesky.get_card("sgetrf")
    with pytest.raises(ValueError):
        cards_cholesky.get_card("dpotrf")   # canonical 是 s 前缀（spec §0）
