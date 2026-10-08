# -*- coding: utf-8 -*-
"""judge 一段式判定流转测试（spotrs 卡为载体，s2-A1）：残差对阈值单步判定、
相对线/均值线与缺 mean 两分支（HT-3）、fail-closed 边界与错误面。

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

RESIDUAL_KEYS = {"ran", "ratio", "threshold", "formula",
                 "ratio_cpu", "ratio_cpu_mean", "pass", "eps"}


def _case(a, b, ratio_cpu=0.0, status="ok", ratio_cpu_mean=0.0):
    return {
        "A32": np.asarray(a, dtype=np.float64),
        "B32": np.asarray(b, dtype=np.float64),
        "ratio_cpu": ratio_cpu,
        "ratio_cpu_status": status,
        "ratio_cpu_mean": ratio_cpu_mean,   # HT-3：potrs 阈值第二支
    }


def _dut(out, info=0, status="ok"):
    return {"out32": np.asarray(out, dtype=np.float64), "info": info, "status": status}


# 基础系统：A=diag(2,4)（SPD），真解 X=[1,2]ᵀ，B=A·X=[2,8]ᵀ（全部精确）。
A = np.array([[2.0, 0.0], [0.0, 4.0]])
X = np.array([[1.0], [2.0]])
B = np.array([[2.0], [8.0]])


def test_positive_numeric_pass_formal_pending():
    """正例：被测即真解 → 残差 0 ≤ 阈值 0 → 数值 PASS（一段式终审），formal 恒待裁；
    residual 段字段齐全（ratio/threshold/formula/ratio_cpu/ratio_cpu_mean/eps）。"""
    v = verdict.judge(CARD, _case(A, B), _dut(X.copy()))
    assert v["error"] is None
    assert v["numeric"] == "PASS"
    assert v["formal"] == "PENDING_RULING"          # 数值/正式分离（spec §2.3）
    assert v["flags"] == []
    assert "layer1" not in v and "fallback" not in v    # s2-A1：旧两层 schema 退役
    res = v["residual"]
    assert set(res) == RESIDUAL_KEYS
    assert res["ran"] is True
    assert res["ratio"] == 0.0
    assert res["threshold"] == 0.0                  # max(5·0, 3·0)
    assert res["formula"] == thresholds.POTRF_POTRS_FORMULA
    assert res["ratio_cpu"] == 0.0 and res["ratio_cpu_mean"] == 0.0
    assert res["eps"] == "2^-24"                    # 归一基准披露（issue A4）
    assert res["pass"] is True


def test_golden_not_consumed():
    """s2-A1：golden 退出判定——case 带严重错误的 golden64/golden32，被测仍为真解
    → 残差 0 → PASS（golden 降为自测参考件，judge 不消费）。"""
    case = _case(A, B)
    case["golden64"] = np.full((2, 1), 777.0)
    case["golden32"] = np.full((2, 1), np.float32(-888.0))
    v = verdict.judge(CARD, case, _dut(X.copy()))
    assert v["error"] is None and v["numeric"] == "PASS"
    assert v["residual"]["ratio"] == 0.0


def test_relative_line_exempts():
    """手算（相对线豁免例，HT-3）：n=100 单位阵系统 2 元素加 2e-3 →
    ratio = 4e-3·2^24/(100+4e-3) ≈ 671.06。双零阈值（ratio_cpu=0、mean=0）判
    FAIL；ratio_cpu=400 → 相对线 5·400=2000 豁免 → 数值 PASS。"""
    n = 100
    a = np.eye(n)
    x = np.ones((n, 1))
    b = x.copy()                                           # A=I → B=X
    out = x.copy()
    out[0, 0] += 2e-3
    out[1, 0] += 2e-3
    expected_ratio = 4e-3 * 2 ** 24 / (100 + 4e-3)         # 手算 ≈ 671.06

    v_zero = verdict.judge(CARD, _case(a, b, ratio_cpu=0.0), _dut(out.copy()))
    assert v_zero["residual"]["threshold"] == 0.0          # max(5·0, 3·0)
    assert v_zero["residual"]["ratio"] == pytest.approx(expected_ratio, rel=1e-12)
    assert v_zero["numeric"] == "FAIL"

    v_up = verdict.judge(CARD, _case(a, b, ratio_cpu=400.0), _dut(out.copy()))
    assert v_up["residual"]["threshold"] == 2000.0         # max(5·400, 3·0)
    assert v_up["residual"]["ratio"] == pytest.approx(expected_ratio, rel=1e-12)
    assert v_up["numeric"] == "PASS"


def test_mean_line_dominates():
    """HT-3 均值线例：单 case ratio_cpu 偏低（0.1）而算子均值偏高（mean=200）→
    阈值取均值线 3·200=600；残差 ≈ 671.06 超线 → FAIL（若只看相对线 5·0.1=0.5
    会误判，均值线托住整体偏难的用例集）。"""
    n = 100
    a = np.eye(n)
    x = np.ones((n, 1))
    b = x.copy()
    out = x.copy()
    out[0, 0] += 2e-3
    out[1, 0] += 2e-3
    v = verdict.judge(CARD, _case(a, b, ratio_cpu=0.1, ratio_cpu_mean=200.0),
                      _dut(out.copy()))
    assert v["residual"]["threshold"] == 600.0             # max(5·0.1, 3·200)
    assert v["numeric"] == "FAIL"


def test_missing_mean_single_line_passes():
    """缺 mean 兼容（HT-3）侧 1：case 无 ratio_cpu_mean，残差 ≈ 671.06 ≤ 单支
    5·ratio_cpu=1000（ratio_cpu=200）→ 数值 PASS，证据注明单支兼容口径。"""
    n = 100
    a = np.eye(n)
    x = np.ones((n, 1))
    b = x.copy()
    out = x.copy()
    out[0, 0] += 2e-3
    out[1, 0] += 2e-3
    case = _case(a, b, ratio_cpu=200.0)
    del case["ratio_cpu_mean"]                             # v2 包无 mean
    v = verdict.judge(CARD, case, _dut(out))
    assert v["error"] is None
    res = v["residual"]
    assert res["ran"] is True
    assert res["threshold"] == 1000.0                      # 单支 5·ratio_cpu
    assert "单支" in res["formula"]
    assert res["ratio_cpu_mean"] is None
    assert res["pass"] is True
    assert v["numeric"] == "PASS"


def test_missing_mean_beyond_line_insufficient():
    """缺 mean 兼容侧 2（fail-closed）：残差超单支 5·ratio_cpu 线 → 不判 FAIL，
    记证据不足（error 指认缺 ratio_cpu_mean、待 v3 包），pass=null。"""
    n = 100
    a = np.eye(n)
    x = np.ones((n, 1))
    b = x.copy()
    out = x.copy()
    out[0, 0] += 2e-3
    out[1, 0] += 2e-3
    case = _case(a, b, ratio_cpu=0.0)
    del case["ratio_cpu_mean"]
    v = verdict.judge(CARD, case, _dut(out))
    res = v["residual"]
    assert res["ran"] is True
    assert res["threshold"] is None and res["pass"] is None  # 两支公式不可算
    assert res["formula"] == thresholds.POTRF_POTRS_FORMULA
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None
    assert "证据不足" in v["error"] and "ratio_cpu_mean" in v["error"] and "v3" in v["error"]


def test_residual_unavailable_prep_failed():
    """ratio_cpu 准备失败 → 残差基线不可用（spec §2.4）：不跑残差、数值 FAIL、
    error 指认（ran=False 的 FAIL 属不可裁，上层记证据不足）。"""
    v = verdict.judge(CARD, _case(A, B, status="prep_failed"), _dut(X.copy()))
    assert v["numeric"] == "FAIL"
    assert v["residual"]["ran"] is False and v["residual"]["ratio"] is None
    assert v["error"] is not None and "残差基线不可用" in v["error"]


def test_residual_unavailable_missing_ratio_cpu_keys():
    case = _case(A, B)
    del case["ratio_cpu"], case["ratio_cpu_status"]
    v = verdict.judge(CARD, case, _dut(X.copy()))
    assert v["numeric"] == "FAIL"
    assert v["residual"]["ran"] is False
    assert v["error"] is not None and "残差基线不可用" in v["error"]


def test_nan_ratio_cpu_collapses_to_error_verdict():
    """ratio_cpu 非有限（A0 的 prep_failed 内容转 NaN 同路径）：阈值公式有限性
    校验抛错，judge 收敛为内部异常 error verdict（不可裁，不判精度）。"""
    v = verdict.judge(CARD, _case(A, B, ratio_cpu=float("nan")), _dut(X.copy()))
    assert v["numeric"] == "FAIL"
    assert v["residual"]["ran"] is False
    assert v["error"] is not None and "非有限值" in v["error"]


def test_dut_error_status_yields_error_verdict():
    for bad_status in ("error", "prep_failed"):
        v = verdict.judge(CARD, _case(A, B), _dut(X.copy(), status=bad_status))
        assert v["numeric"] == "FAIL" and v["residual"]["ran"] is False
        assert v["error"] is not None and bad_status in v["error"]


def test_dut_nonzero_info_yields_error_verdict():
    v = verdict.judge(CARD, _case(A, B), _dut(X.copy(), info=2))
    assert v["numeric"] == "FAIL" and v["residual"]["ran"] is False
    assert v["error"] is not None and "info" in v["error"]


def test_nan_dut_output_fails_with_error():
    """fail-closed：被测含 NaN → 残差接口拒算，数值 FAIL、ratio=null、error 指认
    ——残差接口本身绝不对 NaN 返回数值，也不放行。"""
    out = X.copy()
    out[0, 0] = np.nan
    v = verdict.judge(CARD, _case(A, B), _dut(out))
    res = v["residual"]
    assert res["ran"] is True and res["ratio"] is None
    assert res["pass"] is False
    assert res["eps"] == "2^-24"             # 异常路径同样披露归一基准
    assert v["numeric"] == "FAIL"
    assert v["error"] is not None and "残差不可计算" in v["error"]


def test_zero_output_zero_denominator_fails_closed():
    """fail-closed：全零解 → DPOT02 的 ‖x‖₁=0 零分母 → 残差不可计算，数值 FAIL、
    error 指认零分母（不崩溃不放行）。"""
    v = verdict.judge(CARD, _case(A, B), _dut(np.zeros((2, 1))))
    assert v["numeric"] == "FAIL"
    assert v["residual"]["ran"] is True and v["residual"]["ratio"] is None
    assert v["error"] is not None and "零分母" in v["error"]


def test_judge_never_raises_on_garbage_case():
    """judge 不外抛：case_arrays 缺件收敛为 error verdict（fail-closed）。"""
    v = verdict.judge(CARD, {}, _dut(np.ones((2, 1))))
    assert v["numeric"] == "FAIL" and v["residual"]["ran"] is False
    assert v["error"] is not None and "残差基线不可用" in v["error"]


def test_formal_never_pass_across_outcomes():
    """数值/正式分离：无论数值 PASS/FAIL/错误面，formal 恒为 PENDING_RULING。"""
    verdicts = [
        verdict.judge(CARD, _case(A, B), _dut(X.copy())),
        verdict.judge(CARD, _case(A, B), _dut(X * 1.5)),
        verdict.judge(CARD, _case(A, B), _dut(X.copy(), status="error")),
    ]
    for v in verdicts:
        assert v["formal"] == "PENDING_RULING"
        assert v["numeric"] in ("PASS", "FAIL")
