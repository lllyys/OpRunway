# -*- coding: utf-8 -*-
"""spotrf/spotri 判据卡测试：HT-7 比对目标（potrf 直审主判、还原降诊断、
potri 收单）、存储侧口径、U/L 双侧、扰动反例与卡接线。"""
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


def _potrf_case(uplo, golden, ratio_cpu=0.0, status="ok", ratio_cpu_mean=0.0):
    return {"A64": A64.copy(), "A32": A64.copy(),
            "golden64": golden.copy(), "golden32": golden.astype(np.float32),
            "uplo": uplo, "ratio_cpu": ratio_cpu, "ratio_cpu_status": status,
            "ratio_cpu_mean": ratio_cpu_mean}   # HT-3：potrf 阈值第二支


def _dut(out, info=0, status="ok"):
    return {"out32": np.asarray(out, dtype=np.float64), "info": info, "status": status}


def test_spotrf_pass_lower_ignores_unstored_side():
    """U/L 双侧之 L + 存储侧口径（HT-7 直审主判）：被测非存储侧塞垃圾值，
    F vs golden 直审只取存储侧半三角 → 逐位命中 PASS（主判目标
    factor_vs_golden），垃圾位不进比对集。"""
    out = L_GOLD.copy()
    out[0, 1] = 777.0                       # uplo=L 的上三角是输入残留位，无效
    v = verdict.judge(POTRF, _potrf_case("L", L_GOLD), _dut(out))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert v["layer1"]["max_abs"] == 0.0
    assert v["layer1"]["targets"][0]["name"] == "factor_vs_golden"
    assert v["layer1"]["diagnostics"][0]["name"] == "recon_vs_A"
    assert v["fallback"]["ran"] is False


def test_spotrf_pass_upper_ignores_unstored_side():
    """U/L 双侧之 U：同一矩阵的上三角因子路径（直审取 triu）。"""
    out = U_GOLD.copy()
    out[1, 0] = -888.0
    v = verdict.judge(POTRF, _potrf_case("U", U_GOLD), _dut(out))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert v["fallback"]["ran"] is False


def test_spotrf_direct_fails_but_recon_diagnosis_healthy_rescued_by_fallback():
    """HT-7 对调后的两步结构兜底语义：主判 F vs golden 直审挂、还原比对（诊断
    与 DPOT01 兜底层）健康 → 数值 PASS。

    因子 [[2,0],[1,-2]] 与 golden 因子在 (1,1) 差 4 → 直审 matched 2/3 挂、走
    兜底；但 L·Lᵀ 仍精确等于 A → DPOT01 ratio=0 ≤ 地板 1（ratio_cpu=0）→ 终审
    PASS；诊断项 recon_vs_A 如实并报全命中，不影响裁决。（数学上两个因子
    对称正定等价——直审挂是符号歧义，还原层确认其无害。）"""
    out = np.array([[2.0, 0.0], [1.0, -2.0]])       # fh @ fh.T == A64
    v = verdict.judge(POTRF, _potrf_case("L", L_GOLD), _dut(out))
    t = v["layer1"]["targets"][0]
    assert t["name"] == "factor_vs_golden"
    assert t["matched_ratio"] == pytest.approx(2 / 3)   # (1,1) 元素直审失配
    assert v["layer1"]["pass"] is False
    diag = v["layer1"]["diagnostics"][0]
    assert diag["name"] == "recon_vs_A"
    assert diag["matched_ratio"] == 1.0             # 还原全命中
    fb = v["fallback"]
    assert fb["ran"] is True
    assert fb["ratio"] == 0.0                       # DPOT01：recon 逐位等于 A
    assert fb["threshold"] == 0.0                   # max(5·0, 3·0)（HT-3 双零）
    assert v["numeric"] == "PASS"                   # 兜底终审通过


def test_missing_a64_only_degrades_diagnostic():
    """诊断降级语义（HT-7 后 A64 是诊断/兜底件、非主判件）：A64 缺失只让诊断项
    记 error，主判（F vs golden64）照常 PASS——正例不走兜底，A64 不被触及。"""
    case = _potrf_case("L", L_GOLD)
    del case["A64"]
    v = verdict.judge(POTRF, case, _dut(L_GOLD.copy()))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    diag = v["layer1"]["diagnostics"][0]
    assert "error" in diag and "A64" in diag["error"]


def test_spotrf_zero_output_fails_hand_value():
    """手算 #6（zero 扰动）：零因子 vs golden 直审全不符（matched_ratio=0）；
    兜底 DPOT01 分子 ‖A‖₁=7 → ratio = 7/(2·7·2^-24) = 2^23 = 8388608；
    阈值 max(5·0, 3·0)=0（HT-3 双零）→ 数值 FAIL。"""
    v = verdict.judge(POTRF, _potrf_case("L", L_GOLD), _dut(np.zeros((2, 2))))
    assert v["layer1"]["matched_ratio"] == 0.0             # 存储侧 3 元素全不符
    fb = v["fallback"]
    assert fb["ran"] is True
    assert fb["ratio"] == 8388608.0                        # 手算精确值 2^23
    assert fb["threshold"] == 0.0
    assert fb["formula"] == thresholds.POTRF_POTRS_FORMULA
    assert v["numeric"] == "FAIL"


def test_spotrf_scale_perturbation_fails():
    """scale 扰动：因子整体 ×1.001 → recon 偏 ×1.002001，layer1 全不符、
    DPOT01 兜底也远超阈值 → FAIL。"""
    v = verdict.judge(POTRF, _potrf_case("L", L_GOLD), _dut(L_GOLD * 1.001))
    assert v["numeric"] == "FAIL"
    assert v["fallback"]["ran"] is True
    assert v["fallback"]["ratio"] > v["fallback"]["threshold"]


def test_spotri_pass_ignores_unstored_side():
    """spotri 正例（HT-7 单目标）：A=diag(2,4) 的准确逆 diag(0.5,0.25)，
    直审逐位命中；非存储侧垃圾不进比对集。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]])
    golden = np.array([[0.5, 0.0], [0.0, 0.25]])
    out = golden.copy()
    out[0, 1] = 66.0
    case = {"A64": a.copy(), "A32": a.copy(), "golden64": golden,
            "uplo": "L", "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(POTRI, case, _dut(out))
    assert v["error"] is None and v["numeric"] == "PASS"
    names = [t["name"] for t in v["layer1"]["targets"]]
    assert names == ["ainv_vs_golden"]                 # HT-7 收单


def test_spotri_fallback_hand_value():
    """手算 #7：dut 逆 diag(0.5, 0.375) → 直审 (1,1) 差 0.125 挂（matched 2/3）；
    兜底 DPOT03 ratio = 0.5/(2·4·0.5·2^-24) = 2^21 = 2097152 > 绝对线
    0.1（ratio_cpu=0 → max(5·0, 0.1)，HT-5 新式）→ 数值 FAIL。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]])
    golden = np.array([[0.5, 0.0], [0.0, 0.25]])
    out = np.array([[0.5, 0.0], [0.0, 0.375]])
    case = {"A64": a.copy(), "A32": a.copy(), "golden64": golden, "uplo": "L",
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(POTRI, case, _dut(out))
    assert v["layer1"]["pass"] is False
    assert v["layer1"]["matched_ratio"] == pytest.approx(2 / 3)
    fb = v["fallback"]
    assert fb["ran"] is True
    assert fb["ratio"] == 2097152.0                        # 手算精确值 2^21
    assert fb["threshold"] == 0.1                          # HT-5：potri 绝对线
    assert fb["formula"] == thresholds.POTRI_FORMULA
    assert v["numeric"] == "FAIL"


def test_spotri_direct_pass_small_identity_error_no_longer_primary():
    """HT-7 收单语义：A·A⁻¹ 对 I 撤出第一步，直审过即过。

    A=diag(2,65536)，golden 逆 diag(0.5,2^-16)；dut 把 (1,1) 加 1e-6：
    直审误差 1e-6 在容差内（g_low=2^-16 → tol≈2^-13·2^-16）→ layer1 PASS、
    不走兜底、数值 PASS。该误差对 I 的放大（≈0.0655）由 DPOT03 复核层在直审
    挂的 case 上把关（见 test_spotri_direct_fails_identity_error_residual_catch）。"""
    a = np.array([[2.0, 0.0], [0.0, 65536.0]])
    golden = np.array([[0.5, 0.0], [0.0, 2.0 ** -16]])
    out = golden.copy()
    out[1, 1] += 1e-6
    case = {"A64": a.copy(), "A32": a.copy(), "golden64": golden, "uplo": "L",
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(POTRI, case, _dut(out))
    t = v["layer1"]["targets"][0]
    assert t["name"] == "ainv_vs_golden" and t["pass"] is True
    assert v["layer1"]["pass"] is True
    assert v["flags"] == []
    assert v["fallback"]["ran"] is False
    assert v["numeric"] == "PASS"


def test_spotri_direct_fails_identity_error_residual_catch():
    """HT-7 收单后的复核层把关：直审挂（存储侧塞垃圾）→ 走 DPOT03 兜底，
    A·A⁻¹ 对 I 的残差形态在此终审。

    A=diag(1e-3,1e-3)，golden 逆 diag(1000,1000)；dut 存储侧 (1,0) 塞 1e-3：
    直审 golden 为 0 处误差 1e-3 » atol → 挂（matched 2/3）；兜底 DPOT03 手算：
    ratio = 1e-6/(2·1e-3·1000.001·2^-24) ≈ 8.389 > 绝对线 0.1 → 数值 FAIL。"""
    a = np.array([[1e-3, 0.0], [0.0, 1e-3]])
    golden = np.array([[1000.0, 0.0], [0.0, 1000.0]])
    out = golden.copy()
    out[1, 0] = 1e-3
    case = {"A64": a.copy(), "A32": a.copy(), "golden64": golden, "uplo": "L",
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(POTRI, case, _dut(out))
    t = v["layer1"]["targets"][0]
    assert t["name"] == "ainv_vs_golden"
    assert t["matched_ratio"] == pytest.approx(2 / 3)
    assert t["pass"] is False
    assert v["layer1"]["pass"] is False
    assert v["flags"] == []
    fb = v["fallback"]
    assert fb["ran"] is True
    expected = (1e-3 * 1e-3) * 2.0 ** 24 / (2 * 1e-3 * (1000.0 + 1e-3))   # 手算
    assert fb["ratio"] == pytest.approx(expected, rel=1e-9)
    assert v["numeric"] == "FAIL"


def test_missing_golden64_yields_error_verdict():
    """主判基准需要 golden64（HT-7，spec §2.2）：缺键收敛为 error verdict，不外抛。"""
    case = _potrf_case("L", L_GOLD)
    del case["golden64"]
    v = verdict.judge(POTRF, case, _dut(L_GOLD.copy()))
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None and "golden64" in v["error"]


def test_missing_a64_yields_error_verdict():
    """兜底件 A64 缺失（spotrf 卡）：主判挂后兜底不可计算 → error verdict（fail-closed）。"""
    case = _potrf_case("L", L_GOLD)
    del case["A64"]
    v = verdict.judge(POTRF, case, _dut(np.zeros((2, 2))))
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None and "A64" in v["error"]


def test_card_wiring():
    """卡接线（spec §2.3/§2.3′ + HT-3）：残差 kind、阈值公式、mean 消费声明与
    目标/诊断成员逐卡对号。"""
    assert POTRF.residual_kind == "DPOT01"
    assert POTRF.fallback_formula == thresholds.POTRF_POTRS_FORMULA
    assert POTRF.fallback_threshold(0.5, 1.0) == 3.0    # 均值线主导
    assert POTRF.fallback_uses_mean is True
    potrs = cards_cholesky.get_card("spotrs")
    assert potrs.residual_kind == "DPOT02"
    assert potrs.fallback_formula == thresholds.POTRF_POTRS_FORMULA
    assert potrs.fallback_threshold(0.5, 0.0) == 2.5    # 相对线主导
    assert potrs.fallback_uses_mean is True
    assert POTRI.residual_kind == "DPOT03"
    assert POTRI.fallback_formula == thresholds.POTRI_FORMULA
    assert POTRI.fallback_threshold(0.0) == 0.1
    assert POTRI.fallback_threshold(2.0) == 10.0
    assert POTRI.fallback_uses_mean is False            # mean 线结构性不适用（HT-5）
    for card in (POTRF, potrs, POTRI):
        assert callable(card.layer1_targets) and callable(card.layer1_diagnostics)


def test_get_card_unknown_op_raises():
    with pytest.raises(ValueError):
        cards_cholesky.get_card("sgetrf")
    with pytest.raises(ValueError):
        cards_cholesky.get_card("dpotrf")   # canonical 是 s 前缀（spec §0）
