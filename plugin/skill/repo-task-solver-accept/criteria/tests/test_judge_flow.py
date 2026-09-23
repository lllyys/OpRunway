# -*- coding: utf-8 -*-
"""judge 数值判定流转测试（spotrs 卡为载体）：双门、T3/T4、上浮豁免、错误面。

容差主套按 spec §2.3′ 是任务书表（rtol 2^-10 / atol 2^-16），标准表 2^-13 为对照。
所有用例构造在 FP64 上用精确可表示或可独立手算的数。内核对 dtype 不敏感
（内部统一升 FP64），故不必先降 FP32 再比。
"""
import pytest

pytest.importorskip("scipy")  # spec 波 1 A 行：scipy importorskip 守卫
import numpy as np

import cards_cholesky
import thresholds
import verdict

CARD = cards_cholesky.get_card("spotrs")


def _case(a, b, golden, ratio_cpu=0.0, status="ok"):
    return {
        "A32": np.asarray(a, dtype=np.float64),
        "B32": np.asarray(b, dtype=np.float64),
        "golden32": np.asarray(golden, dtype=np.float64),
        "ratio_cpu": ratio_cpu,
        "ratio_cpu_status": status,
    }


def _dut(out, info=0, status="ok"):
    return {"out32": np.asarray(out, dtype=np.float64), "info": info, "status": status}


# 基础系统：A=diag(2,4)（SPD），真解 X=[1,2]ᵀ，B=A·X=[2,8]ᵀ（全部精确）。
A = np.array([[2.0, 0.0], [0.0, 4.0]])
GOLDEN = np.array([[1.0], [2.0]])
B = np.array([[2.0], [8.0]])


def test_positive_numeric_pass_formal_pending():
    """正例：被测与 golden 逐位相同 → 数值 PASS（layer1 终审），formal 恒待裁。"""
    v = verdict.judge(CARD, _case(A, B, GOLDEN), _dut(GOLDEN.copy()))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert v["formal"] == "PENDING_RULING"          # 数值/正式分离（spec §2.3）
    assert v["flags"] == []
    assert v["fallback"] == {"ran": False, "ratio": None, "threshold": None,
                             "formula": None, "pass": None}
    l1 = v["layer1"]
    assert l1["matched_ratio"] == 1.0
    assert l1["max_abs"] == 0.0
    assert l1["pass_fixed"] is True and l1["pass_ulp"] is True
    assert l1["targets"][0]["name"] == "x_vs_golden"        # spotrs 目标不变（2.3′）
    assert l1["standard"]["matched_ratio"] == 1.0           # 对照套并列展示
    assert l1["diagnostics"] == []                          # spotrs 无诊断项


def test_double_gate_matched_ratio_counterexample():
    """双门反例（门 1）：max_abs 门（fixed 解释）满足、通过率门不满足 → layer1 不过。

    n=100 单位阵系统，100 元素里 2 个加 2e-3（> 任务书 tol 2^-16+2^-10≈9.92e-4，
    但 ≤ fixed 上限 1e-2）→ matched_ratio=0.98 < 0.99。"""
    n = 100
    a = np.eye(n)
    golden = np.ones((n, 1))
    b = golden.copy()                # A=I → B=X
    out = golden.copy()
    out[0, 0] += 2e-3
    out[1, 0] += 2e-3
    v = verdict.judge(CARD, _case(a, b, golden), _dut(out))
    l1 = v["layer1"]
    assert l1["matched_ratio"] == pytest.approx(0.98)
    assert l1["max_abs"] == pytest.approx(2e-3, rel=1e-9)
    assert l1["max_abs"] <= thresholds.MAX_ABS_FIXED       # abs 门（fixed）本身是过的
    assert l1["pass_fixed"] is False and l1["pass_ulp"] is False   # 败在通过率门
    assert v["flags"] == []                    # 两套容差、两解释结论全一致（都不过）
    fb = v["fallback"]
    assert fb["ran"] is True
    # 手算：‖r‖₁=2·2e-3、‖x‖₁=100+4e-3、‖A‖₁=1 → ratio = 4e-3·2^24/100.004 ≈ 671.06
    assert fb["ratio"] == pytest.approx(4e-3 * 2 ** 24 / (100 + 4e-3), rel=1e-9)
    assert v["numeric"] == "FAIL"                          # 671.06 > 30


def test_double_gate_max_abs_counterexample():
    """双门反例（门 2）：通过率门满足、max_abs 门不满足 → layer1 不过。

    n=200，单个离群元素 +0.5（> 1e-2）→ matched_ratio=0.995 ≥ 0.99 但 max_abs 超限。"""
    n = 200
    a = np.eye(n)
    golden = np.ones((n, 1))
    b = golden.copy()
    out = golden.copy()
    out[0, 0] += 0.5
    v = verdict.judge(CARD, _case(a, b, golden), _dut(out))
    l1 = v["layer1"]
    assert l1["matched_ratio"] == pytest.approx(0.995)
    assert l1["max_abs"] == pytest.approx(0.5, rel=1e-12)
    assert l1["max_abs"] > thresholds.MAX_ABS_FIXED
    assert l1["pass_fixed"] is False and l1["pass_ulp"] is False   # 败在 abs 门
    assert v["flags"] == []                                # 两套容差、两解释全一致
    fb = v["fallback"]
    assert fb["ran"] is True
    # 手算：‖r‖₁=0.5、‖x‖₁=200.5、‖A‖₁=1 → ratio = 0.5·2^24/200.5 ≈ 41838.9
    assert fb["ratio"] == pytest.approx(0.5 * 2 ** 24 / 200.5, rel=1e-9)
    assert v["numeric"] == "FAIL"


def test_t3_standard_set_disagreement_only():
    """T3 分歧例（纯 T3）：golden 为 0 的元素误差 5e-5——超任务书 atol（2^-16≈1.53e-5）
    但在标准表 atol（2^-13≈1.22e-4）内 → 主套（任务书）判挂、对照套（标准表）判过
    → flag T3；主套两解释一致（都不过）→ 无 T4。

    fallback 手算：r=[0, −4·5e-5] → ‖r‖₁=2e-4；‖x‖₁=1+5e-5；‖A‖₁=4 →
    ratio = 2e-4·2^24/(4·(1+5e-5)) ≈ 838.8 > 30 → 数值 FAIL。"""
    golden = np.array([[1.0], [0.0]])
    b = A @ golden                                          # = [[2],[0]]
    out = golden.copy()
    out[1, 0] += 5e-5
    v = verdict.judge(CARD, _case(A, b, golden), _dut(out))
    l1 = v["layer1"]
    assert l1["matched_ratio"] == 0.5                      # 主套（任务书）：1/2 不符
    assert l1["standard"]["matched_ratio"] == 1.0          # 对照套（标准表）：全过
    assert l1["pass_fixed"] is False and l1["pass_ulp"] is False
    assert l1["standard"]["pass_fixed"] is True            # 对照套 fixed 解释过
    assert v["flags"] == ["T3"]
    fb = v["fallback"]
    assert fb["ran"] is True                               # 主套不过 → 走兜底
    assert fb["ratio"] == pytest.approx(
        (4 * 5e-5) * 2 ** 24 / (4 * (1 + 5e-5)), rel=1e-9)          # 手算
    assert fb["threshold"] == 30.0
    assert v["numeric"] == "FAIL"


def test_t3_t4_combined_disagreement():
    """T3+T4 组合例：误差 3e-4 在标准表 tol（2^-13·2≈2.44e-4）外、任务书 tol
    （2^-16+2^-10≈9.92e-4）内 → 主套过率全过但 max_abs 两解释分歧（T4）、
    对照套判挂（T3）→ 走兜底，兜底为数值终审。"""
    out = GOLDEN.copy()
    out[0, 0] += 3e-4
    v = verdict.judge(CARD, _case(A, B, GOLDEN), _dut(out))
    l1 = v["layer1"]
    assert l1["matched_ratio"] == 1.0                      # 主套（任务书）：全过
    assert l1["standard"]["matched_ratio"] == 0.5          # 对照套：1/2 元素不符
    assert l1["pass_fixed"] is True and l1["pass_ulp"] is False
    assert l1["standard"]["pass_fixed"] is False
    assert v["flags"] == ["T3", "T4"]
    fb = v["fallback"]
    assert fb["ran"] is True
    # 手算：‖r‖₁=2·3e-4、‖x‖₁=3+3e-4、‖A‖₁=4 → ratio ≈ 838.8 > 30
    assert fb["ratio"] == pytest.approx(
        (2 * 3e-4) * 2 ** 24 / (4 * (3 + 3e-4)), rel=1e-9)
    assert v["numeric"] == "FAIL"


def test_t4_interpretation_disagreement_fallback_pass():
    """手算 #4（T4 分歧例）：单元素误差 48·2^-24 ≈ 2.86e-6 —— 介于 32·ULP
    （1.907e-6）与 1e-2 之间 → pass_fixed=True、pass_ulp=False → flag T4、走兜底。

    两套容差该误差都在 tol 内（任务书 tol(2)≈1.97e-3、标准表 tol(2)≈3.66e-4）
    → 无 T3。兜底手算：r=[0, −4·48·2^-24] → ‖r‖₁=192·2^-24；‖x‖₁=3+48·2^-24；
    ‖A‖₁=4 → ratio = 48/(3+48·2^-24) ≈ 16.0 ≤ 30 → 数值 PASS。
    """
    delta = 48 * 2.0 ** -24
    out = GOLDEN.copy()
    out[1, 0] += delta
    v = verdict.judge(CARD, _case(A, B, GOLDEN), _dut(out))
    l1 = v["layer1"]
    assert l1["matched_ratio"] == 1.0
    assert l1["standard"]["matched_ratio"] == 1.0
    assert l1["pass_fixed"] is True and l1["pass_ulp"] is False
    assert v["flags"] == ["T4"]
    fb = v["fallback"]
    assert fb["ran"] is True
    assert fb["ratio"] == pytest.approx(48 / (3 + delta), rel=1e-12)   # 手算
    assert fb["threshold"] == 30.0
    assert fb["formula"] == thresholds.FLOATUP_FORMULA
    assert fb["pass"] is True
    assert v["numeric"] == "PASS"                          # fallback 为数值终审
    assert v["formal"] == "PENDING_RULING"                 # 带 T4 也绝不出正式 PASS


def test_floatup_threshold_exempts():
    """手算 #5（上浮豁免例）：兜底 ratio = 120/(3+120·2^-24) ≈ 40。

    地板 30 时判 FAIL；ratio_cpu=25 → 阈值 max(2·25, 30)=50，上浮豁免 → 数值 PASS。"""
    delta = 120 * 2.0 ** -24
    out = GOLDEN.copy()
    out[1, 0] += delta
    expected_ratio = 120 / (3 + delta)                     # 手算 ≈ 39.99996

    v_floor = verdict.judge(CARD, _case(A, B, GOLDEN, ratio_cpu=0.0), _dut(out.copy()))
    assert v_floor["fallback"]["threshold"] == 30.0
    assert v_floor["fallback"]["ratio"] == pytest.approx(expected_ratio, rel=1e-12)
    assert v_floor["numeric"] == "FAIL"

    v_up = verdict.judge(CARD, _case(A, B, GOLDEN, ratio_cpu=25.0), _dut(out.copy()))
    assert v_up["fallback"]["threshold"] == 50.0           # max(2·25, 30)
    assert v_up["fallback"]["ratio"] == pytest.approx(expected_ratio, rel=1e-12)
    assert v_up["numeric"] == "PASS"


def test_fallback_unavailable_prep_failed():
    """ratio_cpu 准备失败 → 兜底不可用（spec §2.4）：不跑残差、数值 FAIL、error 指认。"""
    out = GOLDEN.copy()
    out[0, 0] += 3e-4                          # T4 分歧 → 需要兜底（主套过率虽全过）
    v = verdict.judge(CARD, _case(A, B, GOLDEN, status="prep_failed"), _dut(out))
    assert v["numeric"] == "FAIL"
    assert v["fallback"]["ran"] is False and v["fallback"]["ratio"] is None
    assert v["error"] is not None and "兜底不可用" in v["error"]
    assert v["layer1"] is not None                         # layer1 证据保留


def test_fallback_unavailable_missing_ratio_cpu_keys():
    out = GOLDEN.copy()
    out[0, 0] += 3e-4
    case = _case(A, B, GOLDEN)
    del case["ratio_cpu"], case["ratio_cpu_status"]
    v = verdict.judge(CARD, case, _dut(out))
    assert v["numeric"] == "FAIL"
    assert v["fallback"]["ran"] is False
    assert v["error"] is not None and "兜底不可用" in v["error"]


def test_dut_error_status_yields_error_verdict():
    for bad_status in ("error", "prep_failed"):
        v = verdict.judge(CARD, _case(A, B, GOLDEN), _dut(GOLDEN.copy(), status=bad_status))
        assert v["numeric"] == "FAIL" and v["layer1"] is None
        assert v["error"] is not None and bad_status in v["error"]


def test_dut_nonzero_info_yields_error_verdict():
    v = verdict.judge(CARD, _case(A, B, GOLDEN), _dut(GOLDEN.copy(), info=2))
    assert v["numeric"] == "FAIL" and v["layer1"] is None
    assert v["error"] is not None and "info" in v["error"]


def test_nan_dut_output_fails_with_error():
    """NaN 输出：layer1 计不符且 max_abs=+inf（指认层），兜底残差抛异常被收敛，
    数值 FAIL、error 非空——残差接口本身绝不对 NaN 返回数值。"""
    out = GOLDEN.copy()
    out[0, 0] = np.nan
    v = verdict.judge(CARD, _case(A, B, GOLDEN), _dut(out))
    assert v["layer1"]["max_abs"] == float("inf")
    assert v["layer1"]["matched_ratio"] == 0.5             # NaN 元素计为不符
    assert v["fallback"]["ran"] is True and v["fallback"]["ratio"] is None
    assert v["fallback"]["pass"] is False
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None


def test_judge_never_raises_on_garbage_case():
    """judge 不外抛：case_arrays 缺件收敛为 error verdict（fail-closed）。"""
    v = verdict.judge(CARD, {}, _dut(np.ones((2, 1))))
    assert v["numeric"] == "FAIL" and v["layer1"] is None
    assert v["error"] is not None and "golden32" in v["error"]


def test_formal_never_pass_across_outcomes():
    """数值/正式分离：无论数值 PASS/FAIL/错误面，formal 恒为 PENDING_RULING。"""
    verdicts = [
        verdict.judge(CARD, _case(A, B, GOLDEN), _dut(GOLDEN.copy())),
        verdict.judge(CARD, _case(A, B, GOLDEN), _dut(np.zeros((2, 1)) + GOLDEN * 1.5)),
        verdict.judge(CARD, _case(A, B, GOLDEN), _dut(GOLDEN.copy(), status="error")),
    ]
    for v in verdicts:
        assert v["formal"] == "PENDING_RULING"
        assert v["numeric"] in ("PASS", "FAIL")
