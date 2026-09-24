# -*- coding: utf-8 -*-
"""复数三接口（cpotrf/cpotrs/cpotri）criteria 测试（S2 spec §4 契约增量表）。

S2c 验证增量覆盖清单 → 具名测试对照（每项至少一个）：

- 复数手算期望值 ≥2   → test_dpot01_complex_diag_hand_value（2.25/(14ε)）、
                        test_dpot01_complex_offdiag_imag_hand_value（1.0625/(14ε)）、
                        test_dpot02_complex_imag_residual_hand_value（2^21）、
                        test_dpot03_complex_hand_value（2^24/6）
- 纯虚部错误          → test_dpot01_complex_offdiag_imag_hand_value、
                        test_cpotrs_judge_pure_imag_error_not_diluted、
                        test_cpotri_judge_stored_imag_error_fails
- 漏共轭              → test_dpot01_complex_missing_conjugate_caught、
                        test_cpotrf_judge_missing_conjugate_fails
- U/L 双侧            → test_dpot01_complex_upper_equals_lower、
                        test_dpot03_complex_upper_equals_lower、
                        test_cpotrf_judge_pass_both_sides_ignore_unstored
- 无效半三角污染      → test_dpot01_complex_ignores_unstored_triangle、
                        test_dpot03_complex_ignores_unstored_triangle、
                        test_cpotrf_judge_pass_both_sides_ignore_unstored、
                        test_cpotri_judge_dual_targets_split_pass
- 实/复混搭拒绝       → test_residual_mixed_real_complex_rejected、
                        test_card_dtype_mismatch_error_verdict
                        （complex a + 实数 factor 方向由冻结用例
                        test_residual_ratio.test_complex_rejected 覆盖）
- 对角虚部校验        → test_diag_imag_nonzero_rejected（S2 spec §4 Hermitian 行）
- c/z 链路精度        → test_cpotrf_real_chain_numeric_pass /
                        test_cpotrs_real_chain_numeric_pass /
                        test_cpotri_real_chain_numeric_pass
- 卡接线（阈值同实数）→ test_c_card_wiring
- HT-1 复数独立锚点   → test_c_sides_independent_anchor_limits

内核对复数宽度不敏感（complex64/complex128 都先升 complex128 再算），故单元
用例直接用 complex128 构造以保手算精确；c/z 链路测试按包契约用 complex64
被测数据、complex128 golden（S2 spec §4 dtype 映射行）。
"""
import pytest

pytest.importorskip("scipy")  # spec 波 1 A 行：scipy importorskip 守卫
import numpy as np
from scipy.linalg.lapack import cpotrf, cpotri, cpotrs, zpotrf, zpotri

import cards_cholesky
import thresholds
import verdict
from verdict import residual_ratio

EPS = thresholds.EPS32   # 2^-24（spec §0；复数不变，S2 spec §4）

CPOTRF = cards_cholesky.get_card("cpotrf")
CPOTRS = cards_cholesky.get_card("cpotrs")
CPOTRI = cards_cholesky.get_card("cpotri")

# Hermitian 正定 2×2：A=[[4,2i],[-2i,5]]，准确因子 L=[[2,0],[-i,2]]、U=Lᴴ。
CA = np.array([[4.0, 2.0j], [-2.0j, 5.0]], dtype=np.complex128)
CL_GOLD = np.array([[2.0, 0.0], [-1.0j, 2.0]], dtype=np.complex128)
CU_GOLD = np.array([[2.0, 1.0j], [0.0, 2.0]], dtype=np.complex128)


# ---------------------------------------------------------------------------
# residual_ratio 复数通路：手算、漏共轭、U/L 双侧、存储侧口径与异常面
# ---------------------------------------------------------------------------

def test_dpot01_complex_diag_hand_value():
    """复数手算 #1：准确因子的 L[1,1] 扰成 2.5 → 差值存储侧只有 (1,1)=2.25 →
    ‖A‖₁ = max(6,7) = 7 → ratio = 2.25/(2·7·ε)，与实数手算 #1 同值（对角
    扰动无虚部，两通路公式同形状的交叉验证）。"""
    factor = np.array([[2.0, 0.0], [-1.0j, 2.5]], dtype=np.complex128)
    got = residual_ratio("DPOT01", a=CA, factor=factor, uplo="L")
    assert got == pytest.approx(2.25 / (2 * 7 * EPS), rel=1e-12)


def test_dpot01_complex_offdiag_imag_hand_value():
    """复数手算 #2（纯虚部错误）：L21 从 −i 扰成 −1.25i → recon−A 存储侧 =
    [[0,0],[−0.5i,0.5625]]，Hermitian 镜像后列绝对和 max(0.5, 1.0625) →
    num=1.0625 → ratio = 1.0625/(2·7·ε)。误差全在虚部，复模如实捕捉。"""
    factor = np.array([[2.0, 0.0], [-1.25j, 2.0]], dtype=np.complex128)
    got = residual_ratio("DPOT01", a=CA, factor=factor, uplo="L")
    assert got == pytest.approx(1.0625 / (2 * 7 * EPS), rel=1e-12)


def test_dpot01_complex_upper_equals_lower():
    """U/L 双侧：同一扰动的上三角存储（U′=L′ᴴ，Uᴴ·U 口径）给出同一 ratio。"""
    f_lower = np.array([[2.0, 0.0], [-1.25j, 2.0]], dtype=np.complex128)
    f_upper = f_lower.conj().T.copy()
    lo = residual_ratio("DPOT01", a=CA, factor=f_lower, uplo="L")
    up = residual_ratio("DPOT01", a=CA, factor=f_upper, uplo="U")
    assert up == pytest.approx(lo, rel=1e-12)
    assert lo == pytest.approx(1.0625 / (2 * 7 * EPS), rel=1e-12)


def test_dpot01_complex_missing_conjugate_caught():
    """漏共轭：准确因子两侧 ratio 都归 0（若实现漏共轭用 L·Lᵀ，准确因子的
    recon = [[4,−2i],[−2i,3]] ≠ A，本断言即失败——归 0 就是 L·Lᴴ 口径的
    证据）；共轭翻转因子 conj(L) 的 recon = conj(A)，差值 (1,0)=4i →
    镜像列和 num=4 → ratio = 4/(2·7·ε) ≫ 0，漏共轭实现被如实抓住。"""
    assert residual_ratio("DPOT01", a=CA, factor=CL_GOLD, uplo="L") == 0.0
    assert residual_ratio("DPOT01", a=CA, factor=CU_GOLD, uplo="U") == 0.0
    got = residual_ratio("DPOT01", a=CA, factor=CL_GOLD.conj(), uplo="L")
    assert got == pytest.approx(4 / (2 * 7 * EPS), rel=1e-12)


def test_dpot01_complex_ignores_unstored_triangle():
    """只用存储侧：a 与因子的非存储侧塞复数垃圾值，ratio 不变。"""
    f = np.array([[2.0, 0.0], [-1.25j, 2.0]], dtype=np.complex128)
    clean = residual_ratio("DPOT01", a=CA, factor=f, uplo="L")
    a_dirty = CA.copy()
    a_dirty[0, 1] = 777.0 + 5.0j          # uplo=L 时上三角不是存储侧
    f_dirty = f.copy()
    f_dirty[0, 1] = -999.0j
    assert residual_ratio("DPOT01", a=a_dirty, factor=f_dirty, uplo="L") == clean


def test_dpot02_complex_imag_residual_hand_value():
    """复数手算 #3（纯虚部残差）：A=diag(2,4)、x=[1,1]ᵀ、b=[2,4+i]ᵀ →
    r = b−A·x = [0,i]ᵀ → ‖r‖₁=1、‖x‖₁=2、‖A‖₁=4 →
    ratio = 1/(4·2·ε) = 2^21 = 2097152（精确）；准确解归 0。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]], dtype=np.complex128)
    x = np.array([[1.0], [1.0]], dtype=np.complex128)
    b = np.array([[2.0], [4.0 + 1.0j]], dtype=np.complex128)
    assert residual_ratio("DPOT02", a=a, b=b, x=x) == 2097152.0
    assert residual_ratio("DPOT02", a=a, b=a @ x, x=x) == 0.0


def test_dpot03_complex_hand_value():
    """复数手算 #4：A=diag(2,4) 的准确逆存储侧 (1,0) 塞 0.25i →
    C 镜像 = [[0.5,−0.25i],[0.25i,0.25]] → W = I−A·C = [[0,0.5i],[−i,0]] →
    num=1、‖A‖₁=4、‖C‖₁=0.75 → ratio = 1/(2·4·0.75·ε) = 2^24/6。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]], dtype=np.complex128)
    ainv = np.array([[0.5, 0.0], [0.25j, 0.25]], dtype=np.complex128)
    got = residual_ratio("DPOT03", a=a, ainv=ainv, uplo="L")
    assert got == pytest.approx(2.0 ** 24 / 6, rel=1e-12)


def test_dpot03_complex_upper_equals_lower():
    """U/L 双侧：同一 Hermitian 逆的两种半三角存储（上侧即共轭镜像）同 ratio。"""
    a = np.array([[2.0, 1.0j], [-1.0j, 2.0]], dtype=np.complex128)
    c_lower = 1.01 * np.array([[2 / 3, 0.0], [1.0j / 3, 2 / 3]], dtype=np.complex128)
    c_upper = c_lower.conj().T.copy()
    lo = residual_ratio("DPOT03", a=a, ainv=c_lower, uplo="L")
    up = residual_ratio("DPOT03", a=a, ainv=c_upper, uplo="U")
    assert up == pytest.approx(lo, rel=1e-12)
    assert lo > 0.0


def test_dpot03_complex_ignores_unstored_triangle():
    a = np.array([[2.0, 1.0j], [-1.0j, 2.0]], dtype=np.complex128)
    c = 1.01 * np.array([[2 / 3, 0.0], [1.0j / 3, 2 / 3]], dtype=np.complex128)
    clean = residual_ratio("DPOT03", a=a, ainv=c, uplo="L")
    a_dirty = a.copy()
    a_dirty[0, 1] = 55.0 - 3.0j
    c_dirty = c.copy()
    c_dirty[0, 1] = -66.0j
    assert residual_ratio("DPOT03", a=a_dirty, ainv=c_dirty, uplo="L") == clean


def test_residual_mixed_real_complex_rejected():
    """实/复混搭抛错（S2 spec §4 dtype 一致性）：三个 kind 各验一个方向；
    complex a + 实数 factor 的方向由冻结用例 test_complex_rejected 保住。"""
    ar = np.array([[4.0, 2.0], [2.0, 5.0]])
    with pytest.raises(TypeError):
        residual_ratio("DPOT01", a=ar, factor=CL_GOLD, uplo="L")
    with pytest.raises(TypeError):
        residual_ratio("DPOT02", a=CA, b=np.ones((2, 1)),
                       x=np.ones((2, 1), dtype=np.complex128))
    with pytest.raises(TypeError):
        residual_ratio("DPOT03", a=ar, ainv=CL_GOLD, uplo="L")


def test_diag_imag_nonzero_rejected():
    """对角虚部校验（S2 spec §4：对角虚部按 0 处理并校验）：a 非 Hermitian
    存储（对角虚部非 0）三个 kind 都抛错，抓错喂而非防恶意。"""
    a_bad = CA.copy()
    a_bad[0, 0] = 4.0 + 1e-3j
    with pytest.raises(ValueError):
        residual_ratio("DPOT01", a=a_bad, factor=CL_GOLD, uplo="L")
    with pytest.raises(ValueError):
        residual_ratio("DPOT02", a=a_bad, b=np.ones((2, 1), dtype=np.complex128),
                       x=np.ones((2, 1), dtype=np.complex128))
    with pytest.raises(ValueError):
        residual_ratio("DPOT03", a=a_bad, ainv=CL_GOLD, uplo="L")


def test_complex_returns_python_float():
    got = residual_ratio("DPOT01", a=CA, factor=CL_GOLD, uplo="L")
    assert isinstance(got, float)


# ---------------------------------------------------------------------------
# judge × 复数卡：拆实/虚、双目标、错卡拒绝（T7 已摘，issue C3）
# ---------------------------------------------------------------------------

def _cpotrf_case(uplo, golden, ratio_cpu=0.0, status="ok"):
    return {"A64": CA.copy(), "A32": CA.astype(np.complex64),
            "golden32": golden.copy(), "uplo": uplo,
            "ratio_cpu": ratio_cpu, "ratio_cpu_status": status}


def _dut(out, info=0, status="ok"):
    return {"out32": np.asarray(out, dtype=np.complex128), "info": info,
            "status": status}


def test_cpotrf_judge_pass_both_sides_ignore_unstored():
    """U/L 双侧 + 无效半三角污染（judge 级）：两侧准确因子逐位还原 A → PASS；
    被测非存储侧与 A64 非存储侧的复数垃圾都不进比对集。"""
    out_l = CL_GOLD.copy()
    out_l[0, 1] = 777.0 + 888.0j             # uplo=L 的上三角是输入残留位
    case_l = _cpotrf_case("L", CL_GOLD)
    case_l["A64"][0, 1] = -555.0j            # A64 非存储侧垃圾，不在 tril 比对集
    v = verdict.judge(CPOTRF, case_l, _dut(out_l))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert v["layer1"]["max_abs"] == 0.0
    names = [t["name"] for t in v["layer1"]["targets"]]
    assert names == ["recon_vs_A_re", "recon_vs_A_im"]   # 拆实/虚（任务书 §3.2 第 3 条）
    diag_names = [d["name"] for d in v["layer1"]["diagnostics"]]
    assert diag_names == ["factor_vs_golden_re", "factor_vs_golden_im"]
    assert v["fallback"]["ran"] is False
    assert v["flags"] == []                  # T7 已摘（issue C3 确认拆实/虚口径）

    out_u = CU_GOLD.copy()
    out_u[1, 0] = -999.0 - 111.0j
    v_u = verdict.judge(CPOTRF, _cpotrf_case("U", CU_GOLD), _dut(out_u))
    assert v_u["error"] is None and v_u["numeric"] == "PASS"
    assert v_u["layer1"]["max_abs"] == 0.0
    assert v_u["flags"] == []


def test_cpotrf_judge_missing_conjugate_fails():
    """漏共轭（judge 级）：被测给出共轭翻转因子 conj(L) → 实部目标全命中、
    虚部目标在 (1,0) 挂（recon 虚部 2 vs A 虚部 −2，err=4）——纯虚部错误只落
    在 _im 目标，不被实部合并稀释。兜底 DPOT01 手算：num=4 → ratio=4/(2·7·ε)
    → 超地板 1 → 数值 FAIL。"""
    v = verdict.judge(CPOTRF, _cpotrf_case("L", CL_GOLD), _dut(CL_GOLD.conj()))
    re_t, im_t = v["layer1"]["targets"]
    assert re_t["name"] == "recon_vs_A_re"
    assert re_t["matched_ratio"] == 1.0 and re_t["max_abs"] == 0.0
    assert im_t["name"] == "recon_vs_A_im"
    assert im_t["matched_ratio"] == pytest.approx(2 / 3)
    assert im_t["max_abs"] == 4.0
    assert v["layer1"]["pass"] is False                  # AND 聚合：单侧挂即挂
    fb = v["fallback"]
    assert fb["ran"] is True
    assert fb["ratio"] == pytest.approx(4 / (2 * 7 * EPS), rel=1e-12)   # 手算
    assert fb["threshold"] == 1.0
    assert v["numeric"] == "FAIL"
    assert v["flags"] == []


def test_cpotrs_judge_pure_imag_error_not_diluted():
    """纯虚部错误不合并稀释（复数任务书 §3.2 第 3 条）：100 元素解向量里
    2 个元素只在虚部加 2e-3 → 虚部 matched_ratio=0.98 单侧不达标；若按实虚
    合并 2N=200 元素计数会得 198/200=0.99「恰好达标」——本用例钉死不允许该
    稀释。实部目标全命中。"""
    n = 100
    a = np.eye(n, dtype=np.complex128)
    golden = np.ones((n, 1), dtype=np.complex128)
    b = golden.copy()                        # A=I → B=X
    out = golden.copy()
    out[0, 0] += 2e-3j
    out[1, 0] += 2e-3j
    case = {"A32": a, "B32": b, "golden32": golden,
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(CPOTRS, case, _dut(out))
    re_t, im_t = v["layer1"]["targets"]
    assert re_t["name"] == "x_vs_golden_re"
    assert re_t["matched_ratio"] == 1.0 and re_t["max_abs"] == 0.0
    assert im_t["name"] == "x_vs_golden_im"
    assert im_t["matched_ratio"] == pytest.approx(0.98)
    assert im_t["pass"] is False                         # 虚部单侧不达标
    # min 聚合而非 2N 合并：合并口径是 0.99 ≥ 0.99 会放过。
    assert v["layer1"]["matched_ratio"] == pytest.approx(0.98)
    assert v["layer1"]["matched_ratio"] < thresholds.REQUIRED_MATCHED_RATIO
    assert v["layer1"]["pass"] is False
    fb = v["fallback"]
    assert fb["ran"] is True
    # 手算：‖r‖₁=2·2e-3、‖A‖₁=1、‖x‖₁=98+2·|1+2e-3i| → ratio ≈ 671 > 30。
    xnorm = 98.0 + 2 * abs(1 + 2e-3j)
    assert fb["ratio"] == pytest.approx(4e-3 * 2 ** 24 / xnorm, rel=1e-12)
    assert fb["threshold"] == 30.0
    assert v["numeric"] == "FAIL"
    assert v["flags"] == []


def test_cpotri_judge_dual_targets_split_pass():
    """cpotri 双目标各拆实/虚（S2 spec §4，共 4 目标）：准确逆全过；被测非存储
    侧复数垃圾既不进直审也不进镜像乘积。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]], dtype=np.complex128)
    golden = np.array([[0.5, 0.0], [0.0, 0.25]], dtype=np.complex128)
    out = golden.copy()
    out[0, 1] = 66.0 - 77.0j
    case = {"A32": a, "golden32": golden, "uplo": "L",
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(CPOTRI, case, _dut(out))
    assert v["error"] is None and v["numeric"] == "PASS"
    names = [t["name"] for t in v["layer1"]["targets"]]
    assert names == ["ainv_vs_golden_re", "ainv_vs_golden_im",
                     "a_ainv_vs_identity_re", "a_ainv_vs_identity_im"]
    assert v["layer1"]["max_abs"] == 0.0
    assert v["flags"] == []


def test_cpotri_judge_stored_imag_error_fails():
    """cpotri 纯虚部错误（judge 级手算）：存储侧 (1,0) 塞 0.25i → 直审虚部
    matched 2/3、单位阵目标虚部 matched 0.5（W 虚部 [[0,−0.5],[1,0]]），两个
    实部目标全命中（虚部错误不被实部稀释）。兜底 DPOT03 = 复数手算 #4：
    ratio = 2^24/6 > 地板 1 → 数值 FAIL。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]], dtype=np.complex128)
    golden = np.array([[0.5, 0.0], [0.0, 0.25]], dtype=np.complex128)
    out = golden.copy()
    out[1, 0] = 0.25j
    case = {"A32": a, "golden32": golden, "uplo": "L",
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(CPOTRI, case, _dut(out))
    d_re, d_im, i_re, i_im = v["layer1"]["targets"]
    assert d_re["matched_ratio"] == 1.0
    assert d_im["matched_ratio"] == pytest.approx(2 / 3)
    assert i_re["matched_ratio"] == 1.0
    assert i_im["matched_ratio"] == pytest.approx(0.5)
    assert v["layer1"]["matched_ratio"] == pytest.approx(0.5)   # 最差目标聚合
    fb = v["fallback"]
    assert fb["ran"] is True
    assert fb["ratio"] == pytest.approx(2.0 ** 24 / 6, rel=1e-12)   # 手算
    assert fb["threshold"] == 1.0
    assert v["numeric"] == "FAIL"
    assert v["flags"] == []


def test_c_sides_independent_anchor_limits():
    """HT-1 复数两侧独立锚点：同一误差 2e-2，实部锚 g_low=1e4（limit 3.125e-2）过、
    虚部锚 g_low=32（limit 兜底 1e-2）挂——实/虚各自统计、各自锚点各自上限。
    虚部误差在两套容差内（tb tol(32)≈3.13e-2），只挂 abs 门 → 走兜底终审。"""
    a = np.array([[2.0 + 0.0j]], dtype=np.complex128)
    golden = np.array([[1e4 + 32.0j]], dtype=np.complex128)
    b = a @ golden
    out = golden + (2e-2 + 2e-2j)
    case = {"A32": a, "B32": b, "golden32": golden,
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(CPOTRS, case, _dut(out))
    re_t, im_t = v["layer1"]["targets"]
    assert re_t["name"] == "x_vs_golden_re"
    assert re_t["g_low"] == 10000.0
    assert re_t["max_abs_limit"] == 0.03125              # 32·ULP(1e4)
    assert re_t["pass"] is True
    assert im_t["name"] == "x_vs_golden_im"
    assert im_t["matched_ratio"] == 1.0                  # 容差内，只挂 abs 门
    assert im_t["g_low"] == 32.0
    assert im_t["max_abs_limit"] == thresholds.MAX_ABS_FIXED
    assert im_t["pass"] is False
    assert v["layer1"]["pass"] is False                  # AND 聚合
    assert v["fallback"]["ran"] is True
    assert v["numeric"] == "FAIL"                        # 兜底残差 ≈ 47.5 > 地板 30
    assert v["flags"] == []


def test_card_dtype_mismatch_error_verdict():
    """实/复错卡（S2 spec §4 dtype 一致性）：实数输出进复数卡、复数输出进
    实数卡，都收敛为 error verdict（fail-closed，judge 不外抛）。"""
    real_dut = {"out32": np.eye(2), "info": 0, "status": "ok"}
    v = verdict.judge(CPOTRF, _cpotrf_case("L", CL_GOLD), real_dut)
    assert v["numeric"] == "FAIL" and v["layer1"] is None
    assert v["error"] is not None and "TypeError" in v["error"]

    spotrf = cards_cholesky.get_card("spotrf")
    case_r = {"A64": np.array([[4.0, 2.0], [2.0, 5.0]]),
              "A32": np.array([[4.0, 2.0], [2.0, 5.0]]),
              "golden32": np.array([[2.0, 0.0], [1.0, 2.0]]), "uplo": "L",
              "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v2 = verdict.judge(spotrf, case_r, _dut(CL_GOLD.copy()))
    assert v2["numeric"] == "FAIL" and v2["layer1"] is None
    assert v2["error"] is not None and "TypeError" in v2["error"]


def test_c_card_wiring():
    """c 卡接线（S2 spec §4）：残差 kind、阈值公式与地板数值同实数书；
    z 前缀只用于 golden 链路，不登记卡。"""
    assert CPOTRF.residual_kind == "DPOT01"
    assert CPOTRF.fallback_formula == thresholds.TIGHTEN_FORMULA
    assert CPOTRF.fallback_threshold(0.0) == 1.0
    assert CPOTRS.residual_kind == "DPOT02"
    assert CPOTRS.fallback_formula == thresholds.FLOATUP_FORMULA
    assert CPOTRS.fallback_threshold(0.0) == 30.0
    assert CPOTRI.residual_kind == "DPOT03"
    assert CPOTRI.fallback_formula == thresholds.TIGHTEN_FORMULA
    assert CPOTRI.fallback_threshold(0.0) == 1.0
    for card in (CPOTRF, CPOTRS, CPOTRI):
        assert callable(card.layer1_targets) and callable(card.layer1_diagnostics)
    with pytest.raises(ValueError):
        cards_cholesky.get_card("zpotrf")


# ---------------------------------------------------------------------------
# c/z 真实链路（scipy）：c 前缀被测 vs z 前缀 golden（S2 spec §4 LAPACK 链路行）
# ---------------------------------------------------------------------------

N = 16


def _hpd(n, seed):
    """随机 Hermitian 正定：A = BᴴB + n·I（复数任务书 §3.2 数据规则同构），
    对角虚部显式置 0（S2 spec §4：对角虚部按 0 处理——gen 侧契约，此处防
    BLAS/FMA 舍入噪声落在对角虚部）。"""
    rng = np.random.default_rng(seed)
    b = rng.uniform(-5.0, 5.0, (n, n)) + 1j * rng.uniform(-5.0, 5.0, (n, n))
    a = b.conj().T @ b + n * np.eye(n)
    np.fill_diagonal(a, a.diagonal().real)
    return a


@pytest.fixture(scope="module")
def cchain():
    """一次性准备三接口共用的 complex64 被测链路与 complex128 golden 链路。"""
    a64 = _hpd(N, 20250912 + N)
    a32 = a64.astype(np.complex64)          # 降型（S2 spec §4 dtype 映射行）
    f32, info_f = cpotrf(a32.copy(), lower=1)
    assert info_f == 0
    fg64, info_g = zpotrf(a64.copy(), lower=1)
    assert info_g == 0
    return {"a64": a64, "a32": a32, "f32": f32, "fg64": fg64}


def test_cpotrf_real_chain_numeric_pass(cchain):
    golden32 = np.tril(cchain["fg64"]).astype(np.complex64)   # 存储侧另侧置 0
    ratio_cpu = residual_ratio("DPOT01", a=cchain["a64"],
                               factor=cchain["f32"], uplo="L")   # c 链路自指
    case = {"A64": cchain["a64"], "A32": cchain["a32"], "golden32": golden32,
            "uplo": "L", "ratio_cpu": ratio_cpu, "ratio_cpu_status": "ok"}
    v = verdict.judge(CPOTRF, case,
                      {"out32": cchain["f32"], "info": 0, "status": "ok"})
    assert v["error"] is None
    names = [t["name"] for t in v["layer1"]["targets"]]
    assert names == ["recon_vs_A_re", "recon_vs_A_im"]
    assert v["numeric"] == "PASS"
    assert "T7" not in v["flags"]
    assert v["formal"] == "PENDING_RULING"


def test_cpotrs_real_chain_numeric_pass(cchain):
    rng = np.random.default_rng(20250912 + N + 1)
    x_true = rng.uniform(-5.0, 5.0, (N, 4)) + 1j * rng.uniform(-5.0, 5.0, (N, 4))
    b64 = cchain["a64"] @ x_true
    b32 = b64.astype(np.complex64)
    x32, info = cpotrs(cchain["f32"], b32.copy(), lower=1)
    assert info == 0
    golden32 = np.linalg.solve(cchain["a64"], b64).astype(np.complex64)
    ratio_cpu = residual_ratio("DPOT02", a=cchain["a32"], b=b32, x=x32)   # 自指
    case = {"A64": cchain["a64"], "A32": cchain["a32"], "B64": b64, "B32": b32,
            "golden32": golden32, "uplo": "L",
            "ratio_cpu": ratio_cpu, "ratio_cpu_status": "ok"}
    v = verdict.judge(CPOTRS, case, {"out32": x32, "info": 0, "status": "ok"})
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert "T7" not in v["flags"]
    assert v["formal"] == "PENDING_RULING"


def test_cpotri_real_chain_numeric_pass(cchain):
    c32, info = cpotri(cchain["f32"].copy(), lower=1)
    assert info == 0
    cg64, info_g = zpotri(cchain["fg64"].copy(), lower=1)
    assert info_g == 0
    golden32 = np.tril(cg64).astype(np.complex64)             # 存储侧另侧置 0
    ratio_cpu = residual_ratio("DPOT03", a=cchain["a32"],
                               ainv=np.tril(c32), uplo="L")   # 自指
    case = {"A64": cchain["a64"], "A32": cchain["a32"], "golden32": golden32,
            "uplo": "L", "ratio_cpu": ratio_cpu, "ratio_cpu_status": "ok"}
    v = verdict.judge(CPOTRI, case,
                      {"out32": np.tril(c32), "info": 0, "status": "ok"})
    assert v["error"] is None
    names = [t["name"] for t in v["layer1"]["targets"]]
    assert names == ["ainv_vs_golden_re", "ainv_vs_golden_im",
                     "a_ainv_vs_identity_re", "a_ainv_vs_identity_im"]
    assert v["numeric"] == "PASS"
    assert "T7" not in v["flags"]
    assert v["formal"] == "PENDING_RULING"
