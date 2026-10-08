# -*- coding: utf-8 -*-
"""A 卡测试公共件：scipy 守卫与 criteria 目录挂载。

spec 波 1 A 行具名覆盖清单 → 测试对照表（每项至少一个具名测试）：

- U/L 双侧            → test_residual_ratio: test_dpot01_upper_equals_lower、
                        test_dpot03_upper_equals_lower；
                        test_judge_cards: test_spotrf_pass_lower / test_spotrf_pass_upper
- HT-7 比对目标     → test_judge_cards: test_spotrf_direct_fails_but_recon_diagnosis_healthy_rescued_by_fallback、
                        test_missing_a64_only_degrades_diagnostic、
                        test_spotri_direct_pass_small_identity_error_no_longer_primary、
                        test_spotri_direct_fails_identity_error_residual_catch
- potrs 多 RHS 最差列 → test_residual_ratio: test_dpot02_multi_rhs_takes_worst_column
- 双门反例            → test_judge_flow: test_double_gate_matched_ratio_counterexample、
                        test_double_gate_max_abs_counterexample
- 单套容差边界例       → test_judge_flow: test_tolerance_boundary_within_passes、
                        test_tolerance_boundary_outside_falls_back（HT-14 原 T3 位）
- HT-1 动态锚点       → test_judge_flow: test_max_abs_dynamic_anchor_large_golden_passes、
                        test_max_abs_floor_dominates_small_golden、
                        test_g_low_anchors_max_error_point_not_largest_golden；
                        test_thresholds: test_max_abs_limit_dynamic_anchor；
                        复数独立锚点 test_complex_criteria:
                        test_c_sides_independent_anchor_limits
- ±inf/NaN 收窄口径   → test_judge_flow: test_inf_same_sign_passes /
                        test_inf_opposite_sign_fails /
                        test_inf_overflow_narrowing_same_sign_passes /
                        test_nan_both_sides_passes / test_nan_single_side_counts_mismatch /
                        test_all_inf_points_vacuous_abs_gate
- HT-3 相对线/均值线与缺 mean 兼容 → test_judge_flow: test_potrs_relative_line_exempts、
                        test_potrs_mean_line_dominates、
                        test_missing_mean_single_line_passes、
                        test_missing_mean_beyond_line_insufficient；阈值式
                        test_thresholds: test_potrf_potrs_threshold_new_formula
- NaN/shape/零分母    → test_residual_ratio: test_nan_raises / test_inf_raises /
                        test_shape_mismatch_raises / test_zero_denominator_raises 等；
                        test_judge_flow: test_nan_dut_output_fails_with_error
- 独立手算期望值 ≥3   → 带「手算」注释的断言：DPOT01（2.25/(14ε)）、
                        DPOT02（2^21）、DPOT03（2^21）、spotrf 零输出（2^23）、
                        potri 单目标例（1e-6·2^24）、单套挂起侧例（3e-4·2^24/(2·(1+3e-4))）、
                        锚点选择例（0.1·2^24/(4·(1e4+5e-2+1e6))）、
                        相对线例（4e-3·2^24/(100+4e-3)）
"""
import pathlib
import sys

import pytest

# 执行环境前置：正式跑在远程容器（容器内补装 scipy，spec §1）；无 scipy 的环境整目录跳过。
pytest.importorskip("scipy")

CRITERIA_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(CRITERIA_DIR) not in sys.path:
    sys.path.insert(0, str(CRITERIA_DIR))
