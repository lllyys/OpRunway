# -*- coding: utf-8 -*-
"""A 卡测试公共件：scipy 守卫与 criteria 目录挂载。

s2-A1 一段式覆盖清单 → 测试对照表（每项至少一个具名测试）：

- U/L 双侧            → test_residual_ratio: test_dpot01_upper_equals_lower、
                        test_dpot03_upper_equals_lower；
                        test_judge_cards: test_spotrf_pass_lower / test_spotrf_pass_upper
- potrf 换基正例（A32 基，f32 链真算 PASS）
                      → test_lapack_integration: test_spotrf_real_chain_numeric_pass
- 六算子正/负例（scale×2 与全零必 FAIL，potri 零分母 fail-closed 指认）
                      → test_one_step_negatives: test_positive_exact_passes /
                        test_scale_x2_fails / test_zero_output_fails
- potrs 多 RHS 最差列 → test_residual_ratio: test_dpot02_multi_rhs_takes_worst_column
- HT-3 相对线/均值线与缺 mean 两分支
                      → test_judge_flow: test_relative_line_exempts、
                        test_mean_line_dominates、test_missing_mean_single_line_passes、
                        test_missing_mean_beyond_line_insufficient；阈值式
                        test_thresholds: test_potrf_potrs_threshold_new_formula
- 缺 ratio_cpu/基线不可用 → test_judge_flow: test_residual_unavailable_prep_failed /
                        test_residual_unavailable_missing_ratio_cpu_keys
- NaN/Inf 传染 fail-closed → test_judge_flow: test_nan_dut_output_fails_with_error；
                        test_one_step_negatives: test_nan_output_fails_closed
- 批量 rep_slot 残差 + 余槽核对回归 → test_batched_a0（判定两层先后全量）
- 复数残差（共轭转置路径）正负例 → test_complex_criteria:
                        test_dpot01_complex_missing_conjugate_caught、
                        test_cpotrf_judge_missing_conjugate_fails、
                        test_cpotrf_real_chain_numeric_pass
- NaN/shape/零分母    → test_residual_ratio: test_nan_raises / test_inf_raises /
                        test_shape_mismatch_raises / test_zero_denominator_raises 等
- 独立手算期望值 ≥3   → 带「手算」注释的断言：DPOT01（2.25/(14ε)）、
                        DPOT02（2^21）、DPOT03（2^21）、spotrf 零输出（2^23）、
                        相对线例（4e-3·2^24/(100+4e-3)）、
                        复数漏共轭（4/(14ε)）、批量配对反例（(4δ+δ²)/(14ε)）
"""
import pathlib
import sys

import pytest

# 执行环境前置：正式跑在远程容器（容器内补装 scipy，spec §1）；无 scipy 的环境整目录跳过。
pytest.importorskip("scipy")

CRITERIA_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(CRITERIA_DIR) not in sys.path:
    sys.path.insert(0, str(CRITERIA_DIR))
