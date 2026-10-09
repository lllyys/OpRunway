# -*- coding: utf-8 -*-
"""六算子一段式正/负例矩阵（s2-A1 验收清单项）：对全部单矩阵六卡逐一钉住——

- 正例：精确输出 → 残差 0 → 数值 PASS；
- scale×2：整体 ×2 → 残差有限且超阈 → 数值 FAIL（error 为 None，真精度失败）；
- zero：全零输出 → potrf 族残差打满 FAIL；potrs/potri 族零分母 → 残差不可计算，
  数值 FAIL 且 error 指认零分母（fail-closed，不崩溃不放行）；
- nan：全块 NaN → 残差接口有限性校验拒算 → 数值 FAIL 且 error 指认。

基材全部精确可表示：实数 A=[[4,2],[2,5]]（L=[[2,0],[1,2]]）与 diag(2,4)；复数
Hermitian CA=[[4,2i],[-2i,5]]（L=[[2,0],[-i,2]]）与 diag(2,4)。
"""
import numpy as np
import pytest

pytest.importorskip("scipy")

import cards_cholesky
import verdict

# 实数基材
A_PF = np.array([[4.0, 2.0], [2.0, 5.0]])
L_PF = np.array([[2.0, 0.0], [1.0, 2.0]])
A_D = np.array([[2.0, 0.0], [0.0, 4.0]])
X_S = np.array([[1.0], [2.0]])
B_S = A_D @ X_S
C_I = np.array([[0.5, 0.0], [0.0, 0.25]])
# 复数基材
CA_PF = np.array([[4.0, 2.0j], [-2.0j, 5.0]], dtype=np.complex128)
CL_PF = np.array([[2.0, 0.0], [-1.0j, 2.0]], dtype=np.complex128)
CA_D = A_D.astype(np.complex128)
CX_S = np.array([[1.0], [1.0j]], dtype=np.complex128)
CB_S = CA_PF @ CX_S
CC_I = C_I.astype(np.complex128)

# op -> (case_arrays 基件, 精确输出, 零输出是否触发零分母)
_FIXTURES = {
    "spotrf": ({"A32": A_PF, "uplo": "L"}, L_PF, False),
    "spotrs": ({"A32": A_D, "B32": B_S}, X_S, True),
    "spotri": ({"A32": A_D, "uplo": "L"}, C_I, True),
    "cpotrf": ({"A32": CA_PF, "uplo": "L"}, CL_PF, False),
    "cpotrs": ({"A32": CA_PF, "B32": CB_S}, CX_S, True),
    "cpotri": ({"A32": CA_D, "uplo": "L"}, CC_I, True),
}


def _case(op):
    base, exact, zero_denom = _FIXTURES[op]
    case = {k: np.array(v) if isinstance(v, np.ndarray) else v
            for k, v in base.items()}
    case.update({"ratio_cpu": 0.0, "ratio_cpu_status": "ok", "ratio_cpu_mean": 0.0})
    return case, exact, zero_denom


def _dut(out):
    return {"out32": np.array(out), "info": 0, "status": "ok"}


@pytest.mark.parametrize("op", sorted(_FIXTURES))
def test_positive_exact_passes(op):
    """正例：精确输出 → 残差 0 ≤ 阈值 → 数值 PASS（复数链即共轭转置路径正例）。"""
    case, exact, _ = _case(op)
    v = verdict.judge(cards_cholesky.get_card(op), case, _dut(exact))
    assert v["error"] is None, v["error"]
    assert v["numeric"] == "PASS"
    assert v["residual"]["ratio"] == 0.0


@pytest.mark.parametrize("op", sorted(_FIXTURES))
def test_scale_x2_fails(op):
    """scale×2 必 FAIL：残差有限且超阈，error 为 None（真精度失败，非证据问题）。"""
    case, exact, _ = _case(op)
    v = verdict.judge(cards_cholesky.get_card(op), case, _dut(np.array(exact) * 2))
    assert v["numeric"] == "FAIL"
    assert v["error"] is None
    res = v["residual"]
    assert res["ratio"] is not None and np.isfinite(res["ratio"])
    assert res["ratio"] > res["threshold"]


@pytest.mark.parametrize("op", sorted(_FIXTURES))
def test_zero_output_fails(op):
    """全零必 FAIL：potrf 族残差打满（有限值）；potrs/potri 族零分母 →
    残差不可计算，error 指认零分母（fail-closed）。"""
    case, exact, zero_denom = _case(op)
    v = verdict.judge(cards_cholesky.get_card(op), case,
                      _dut(np.zeros_like(np.array(exact))))
    assert v["numeric"] == "FAIL"
    res = v["residual"]
    assert res["ran"] is True
    if zero_denom:
        assert res["ratio"] is None
        assert v["error"] is not None and "零分母" in v["error"]
    else:
        assert res["ratio"] is not None and res["ratio"] > res["threshold"]
        assert v["error"] is None


@pytest.mark.parametrize("op", sorted(_FIXTURES))
def test_nan_output_fails_closed(op):
    """全块 NaN 必 FAIL（fail-closed）：残差接口有限性校验拒算，error 指认
    NaN/Inf，不崩溃不放行。"""
    case, exact, _ = _case(op)
    out = np.full_like(np.array(exact), np.nan)
    v = verdict.judge(cards_cholesky.get_card(op), case, _dut(out))
    assert v["numeric"] == "FAIL"
    assert v["residual"]["ran"] is True and v["residual"]["ratio"] is None
    assert v["error"] is not None and "残差不可计算" in v["error"]
    assert "NaN" in v["error"]
