# -*- coding: utf-8 -*-
"""A 卡测试公共件：scipy 守卫与 criteria 目录挂载。

spec 波 1 A 行具名覆盖清单 → 测试对照表（每项至少一个具名测试）：

- U/L 双侧            → test_residual_ratio: test_dpot01_upper_equals_lower、
                        test_dpot03_upper_equals_lower；
                        test_judge_cards: test_spotrf_pass_lower / test_spotrf_pass_upper
- 2.3′ 比对目标       → test_judge_cards: test_spotrf_recon_primary_factor_only_diagnostic、
                        test_spotrf_missing_golden32_only_degrades_diagnostic、
                        test_spotri_dual_target_identity_fails_direct_passes、
                        test_spotri_dual_target_direct_fails_identity_passes
- potrs 多 RHS 最差列 → test_residual_ratio: test_dpot02_multi_rhs_takes_worst_column
- 双门反例            → test_judge_flow: test_double_gate_matched_ratio_counterexample、
                        test_double_gate_max_abs_counterexample
- T3 两套阈值分歧例   → test_judge_flow: test_t3_standard_set_disagreement_only、
                        test_t3_t4_combined_disagreement
- T4 分歧例           → test_judge_flow: test_t4_interpretation_disagreement_fallback_pass
- 上浮豁免例          → test_judge_flow: test_floatup_threshold_exempts
- NaN/shape/零分母    → test_residual_ratio: test_nan_raises / test_inf_raises /
                        test_shape_mismatch_raises / test_zero_denominator_raises 等；
                        test_judge_flow: test_nan_dut_output_fails_with_error
- 独立手算期望值 ≥3   → 带「手算」注释的断言：DPOT01（2.25/(14ε)）、
                        DPOT02（2^21）、DPOT03（2^21）、spotrf 零输出（2^23）、
                        potri 双目标例（1e-6·2^24）、T3 例（5e-5·2^24/(1+5e-5)）、
                        T4 例（48/(3+48·2^-24)）、上浮例（120/(3+120·2^-24)）
"""
import pathlib
import sys

import pytest

# 执行环境前置：正式跑在远程容器（容器内补装 scipy，spec §1）；无 scipy 的环境整目录跳过。
pytest.importorskip("scipy")

CRITERIA_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(CRITERIA_DIR) not in sys.path:
    sys.path.insert(0, str(CRITERIA_DIR))
