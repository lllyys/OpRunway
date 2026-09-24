# -*- coding: utf-8 -*-
"""judge 数值判定流转测试（spotrs 卡为载体）：双门（HT-1 动态锚点）、T3、
±inf/NaN 收窄口径、上浮豁免、错误面。

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
                             "formula": None, "pass": None, "eps": "2^-24"}
    l1 = v["layer1"]
    assert l1["matched_ratio"] == 1.0
    assert l1["max_abs"] == 0.0
    assert l1["pass"] is True
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
    assert l1["max_abs"] <= l1["max_abs_limit"]            # abs 门本身是过的
    assert l1["pass"] is False                             # 败在通过率门
    assert v["flags"] == []                                # 两套容差结论一致（都不过）
    fb = v["fallback"]
    assert fb["ran"] is True
    assert fb["eps"] == "2^-24"            # 残差 ratio 的归一基准披露（issue A4）
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
    assert l1["g_low"] == 1.0                              # 锚点即离群点的收窄 golden
    assert l1["max_abs"] > l1["max_abs_limit"]             # 兜底 1e-2 主导的上限
    assert l1["pass"] is False                             # 败在 abs 门
    assert v["flags"] == []                                # 两套容差结论一致
    fb = v["fallback"]
    assert fb["ran"] is True
    # 手算：‖r‖₁=0.5、‖x‖₁=200.5、‖A‖₁=1 → ratio = 0.5·2^24/200.5 ≈ 41838.9
    assert fb["ratio"] == pytest.approx(0.5 * 2 ** 24 / 200.5, rel=1e-9)
    assert v["numeric"] == "FAIL"


def test_t3_standard_set_disagreement_only():
    """T3 分歧例（纯 T3）：golden 为 0 的元素误差 5e-5——超任务书 atol（2^-16≈1.53e-5）
    但在标准表 atol（2^-13≈1.22e-4）内 → 主套（任务书）判挂、对照套（标准表）判过
    → flag T3。

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
    assert l1["pass"] is False
    assert l1["standard"]["pass"] is True                  # 对照套判过
    assert v["flags"] == ["T3"]
    fb = v["fallback"]
    assert fb["ran"] is True                               # 主套不过 → 走兜底
    assert fb["ratio"] == pytest.approx(
        (4 * 5e-5) * 2 ** 24 / (4 * (1 + 5e-5)), rel=1e-9)          # 手算
    assert fb["threshold"] == 30.0
    assert v["numeric"] == "FAIL"


def test_t3_main_pass_standard_fail_no_fallback():
    """T3 分歧例（主套过侧）：误差 3e-4 在标准表 tol（2^-13·2≈2.44e-4）外、任务书
    tol（2^-16+2^-10≈9.92e-4）内，且 ≤ 上限（g_low=1 → 兜底 1e-2 主导）→ 主套
    layer1 过（终审、不走兜底）、对照套判挂 → 记 T3、数值 PASS。"""
    out = GOLDEN.copy()
    out[0, 0] += 3e-4
    v = verdict.judge(CARD, _case(A, B, GOLDEN), _dut(out))
    l1 = v["layer1"]
    assert l1["matched_ratio"] == 1.0                      # 主套（任务书）：全过
    assert l1["standard"]["matched_ratio"] == 0.5          # 对照套：1/2 元素不符
    assert l1["pass"] is True and l1["standard"]["pass"] is False
    assert v["flags"] == ["T3"]
    assert v["fallback"]["ran"] is False                   # layer1 过即终审
    assert v["numeric"] == "PASS"


def test_max_abs_dynamic_anchor_large_golden_passes():
    """HT-1 分界例：golden=1e4 处造 2e-2 误差——32·ULP(1e4)=32·2^-10=3.125e-2 ≥ 2e-2
    → 新口径 layer1 过（旧固定 1e-2 上限会挂）；两套容差都在 tol 内 → 无 flag。"""
    golden = np.array([[1e4], [1e4]])
    b = A @ golden
    out = golden.copy()
    out[0, 0] += 2e-2
    v = verdict.judge(CARD, _case(A, b, golden), _dut(out))
    l1 = v["layer1"]
    assert l1["max_abs"] == pytest.approx(2e-2, rel=1e-9)
    assert l1["max_abs"] > thresholds.MAX_ABS_FIXED        # 旧固定口径会挂
    assert l1["g_low"] == 10000.0
    assert l1["max_abs_limit"] == 0.03125                  # 32·2^-10（ULP@1e4）
    assert l1["pass"] is True
    assert v["flags"] == []
    assert v["fallback"]["ran"] is False
    assert v["numeric"] == "PASS"


def test_max_abs_floor_dominates_small_golden():
    """HT-1 兜底例：g_low=1 处 32·ULP=32·2^-23≈3.8e-6 ≪ 兜底 1e-2 → 上限由兜底值
    主导；误差 2e-4 超 32·ULP 项但在兜底与两套容差内 → PASS、无 flag。"""
    out = GOLDEN.copy()
    out[0, 0] += 2e-4
    v = verdict.judge(CARD, _case(A, B, GOLDEN), _dut(out))
    l1 = v["layer1"]
    assert l1["g_low"] == 1.0
    assert l1["max_abs_limit"] == thresholds.MAX_ABS_FIXED           # 兜底主导
    assert l1["max_abs"] > 32 * float(np.spacing(np.float32(1.0)))   # 单靠 ULP 项会挂
    assert l1["pass"] is True
    assert v["flags"] == []
    assert v["numeric"] == "PASS"


def test_g_low_anchors_max_error_point_not_largest_golden():
    """HT-1 锚点选择例：max 误差在 golden=1e4 的点、更大 golden=1e6 在另一点 →
    limit 锚定误差点的 g_low（3.125e-2），不是最大 golden 的 32·ULP(1e6)=2.0。
    err=5e-2 > 3.125e-2 → abs 门挂（若错锚大 golden 会放过）→ 走兜底终审。"""
    golden = np.array([[1e4], [1e6]])
    b = A @ golden
    out = golden.copy()
    out[0, 0] += 5e-2
    v = verdict.judge(CARD, _case(A, b, golden), _dut(out))
    l1 = v["layer1"]
    assert l1["matched_ratio"] == 1.0                      # 两套容差内，只挂 abs 门
    assert l1["g_low"] == 10000.0
    assert l1["max_abs_limit"] == 0.03125
    assert l1["pass"] is False
    fb = v["fallback"]
    assert fb["ran"] is True
    # 手算：‖r‖₁=2·5e-2、‖x‖₁=1e4+5e-2+1e6、‖A‖₁=4 → ratio ≈ 0.415 ≤ 30
    assert fb["ratio"] == pytest.approx(
        0.1 * 2 ** 24 / (4 * (1e4 + 5e-2 + 1e6)), rel=1e-9)
    assert v["numeric"] == "PASS"                          # 兜底为数值终审


def test_inf_same_sign_passes():
    """±inf 同号：收窄 golden 与被测同为 +inf → 该点通过、不进 max_abs；锚点与
    max_abs 只由有限比对点给出。"""
    golden = np.array([[np.inf], [2.0]])
    v = verdict.judge(CARD, _case(A, B, golden), _dut(golden.copy()))
    l1 = v["layer1"]
    assert v["error"] is None
    assert l1["matched_ratio"] == 1.0
    assert l1["max_abs"] == 0.0 and l1["g_low"] == 2.0
    assert l1["pass"] is True
    assert v["fallback"]["ran"] is False
    assert v["numeric"] == "PASS"


def test_inf_opposite_sign_fails():
    """±inf 异号：golden +inf、被测 −inf → 计为不符、不进 max_abs；兜底残差对
    inf 拒算被收敛 → 数值 FAIL、error 指认。"""
    golden = np.array([[np.inf], [2.0]])
    out = np.array([[-np.inf], [2.0]])
    v = verdict.judge(CARD, _case(A, B, golden), _dut(out))
    l1 = v["layer1"]
    assert l1["matched_ratio"] == 0.5
    assert l1["max_abs"] == 0.0                            # 异号 inf 点不进 max_abs
    assert l1["pass"] is False
    assert v["fallback"]["ran"] is True and v["fallback"]["ratio"] is None
    assert v["numeric"] == "FAIL" and v["error"] is not None


def test_inf_overflow_narrowing_same_sign_passes():
    """golden64 上溢收窄：1e39 astype float32 自然成 +inf（RNE 上溢），被测 +inf
    同号 → 通过——上溢点在 float32 值域语义下与被测一致。"""
    golden = np.array([[1e39], [2.0]])
    out = np.array([[np.inf], [2.0]])
    v = verdict.judge(CARD, _case(A, B, golden), _dut(out))
    assert v["error"] is None
    assert v["layer1"]["matched_ratio"] == 1.0
    assert v["layer1"]["pass"] is True
    assert v["numeric"] == "PASS"


def test_nan_both_sides_passes():
    """双方 NaN → 通过点（收窄比对规则同理语义）、不进 max_abs。"""
    golden = np.array([[np.nan], [2.0]])
    v = verdict.judge(CARD, _case(A, B, golden), _dut(golden.copy()))
    l1 = v["layer1"]
    assert l1["matched_ratio"] == 1.0
    assert l1["max_abs"] == 0.0 and l1["g_low"] == 2.0
    assert v["numeric"] == "PASS"


def test_nan_single_side_counts_mismatch():
    """NaN 单方挂（golden 侧）：golden NaN、被测正常 → 该点计不符、不进 max_abs
    → layer1 挂走兜底（被测侧单方 NaN 由 test_nan_dut_output_fails_with_error 盯）。
    本例被测恰为真解 → 兜底残差 0 → 数值 PASS，钉住「兜底为数值终审」。"""
    golden = np.array([[np.nan], [2.0]])
    out = np.array([[1.0], [2.0]])
    v = verdict.judge(CARD, _case(A, B, golden), _dut(out))
    l1 = v["layer1"]
    assert l1["matched_ratio"] == 0.5
    assert l1["max_abs"] == 0.0 and l1["g_low"] == 2.0
    assert l1["pass"] is False
    assert v["fallback"]["ran"] is True
    assert v["fallback"]["ratio"] == 0.0
    assert v["numeric"] == "PASS"


def test_all_inf_points_vacuous_abs_gate():
    """空有限集：全部比对点同号 ±inf → 无有限点，max_abs=0.0、g_low=None、上限取
    兜底值（abs 门空真）；matched_ratio 门独立把关（本例 1.0）→ 数值 PASS。"""
    golden = np.array([[np.inf], [-np.inf]])
    v = verdict.judge(CARD, _case(A, B, golden), _dut(golden.copy()))
    l1 = v["layer1"]
    assert l1["matched_ratio"] == 1.0
    assert l1["max_abs"] == 0.0
    assert l1["g_low"] is None
    assert l1["max_abs_limit"] == thresholds.MAX_ABS_FIXED
    assert l1["pass"] is True
    assert v["numeric"] == "PASS"


def test_floatup_threshold_exempts():
    """手算 #5（上浮豁免例）：n=100 单位阵系统 2 元素加 2e-3 → 通过率门挂（0.98）
    走兜底，兜底 ratio = 4e-3·2^24/(100+4e-3) ≈ 671.06。地板 30 时判 FAIL；
    ratio_cpu=400 → 阈值 max(2·400, 30)=800，上浮豁免 → 数值 PASS。"""
    n = 100
    a = np.eye(n)
    golden = np.ones((n, 1))
    b = golden.copy()                                      # A=I → B=X
    out = golden.copy()
    out[0, 0] += 2e-3
    out[1, 0] += 2e-3
    expected_ratio = 4e-3 * 2 ** 24 / (100 + 4e-3)         # 手算 ≈ 671.06

    v_floor = verdict.judge(CARD, _case(a, b, golden, ratio_cpu=0.0), _dut(out.copy()))
    assert v_floor["layer1"]["pass"] is False              # 通过率门挂 → 走兜底
    assert v_floor["fallback"]["threshold"] == 30.0
    assert v_floor["fallback"]["ratio"] == pytest.approx(expected_ratio, rel=1e-12)
    assert v_floor["numeric"] == "FAIL"

    v_up = verdict.judge(CARD, _case(a, b, golden, ratio_cpu=400.0), _dut(out.copy()))
    assert v_up["fallback"]["threshold"] == 800.0          # max(2·400, 30)
    assert v_up["fallback"]["ratio"] == pytest.approx(expected_ratio, rel=1e-12)
    assert v_up["numeric"] == "PASS"


def test_fallback_unavailable_prep_failed():
    """ratio_cpu 准备失败 → 兜底不可用（spec §2.4）：不跑残差、数值 FAIL、error 指认。"""
    out = GOLDEN.copy()
    out[0, 0] += 0.5                           # max_abs 门挂 → 需要兜底
    v = verdict.judge(CARD, _case(A, B, GOLDEN, status="prep_failed"), _dut(out))
    assert v["numeric"] == "FAIL"
    assert v["fallback"]["ran"] is False and v["fallback"]["ratio"] is None
    assert v["error"] is not None and "兜底不可用" in v["error"]
    assert v["layer1"] is not None                         # layer1 证据保留


def test_fallback_unavailable_missing_ratio_cpu_keys():
    out = GOLDEN.copy()
    out[0, 0] += 0.5                           # max_abs 门挂 → 需要兜底
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
    """NaN 单方挂（被测侧）：layer1 计不符且不进 max_abs（收窄口径），兜底残差
    抛异常被收敛，数值 FAIL、error 非空——残差接口本身绝不对 NaN 返回数值。"""
    out = GOLDEN.copy()
    out[0, 0] = np.nan
    v = verdict.judge(CARD, _case(A, B, GOLDEN), _dut(out))
    assert v["layer1"]["max_abs"] == 0.0                   # NaN 点不进 max_abs 选取
    assert v["layer1"]["g_low"] == 2.0                     # 锚点取自唯一有限比对点
    assert v["layer1"]["matched_ratio"] == 0.5             # NaN 元素计为不符
    assert v["fallback"]["ran"] is True and v["fallback"]["ratio"] is None
    assert v["fallback"]["pass"] is False
    assert v["fallback"]["eps"] == "2^-24"             # 异常路径同样披露归一基准
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
