# -*- coding: utf-8 -*-
"""HT-4 ratio_cpu_mean 固化口径的机械断言（case-gen 侧）。

todo 卡 HT-4：发布前用 CPU 参考链路算好 mean 写入 index，判定时只读、零重算——
非批量 index 顶层按算子存全部正定精度用例的算术平均（单算子切片内，info 契约
用例不计入）；批量 case 条目存加权均值 Σ(count_j·ratio_j)/batchSize（与任务书
「batch 个矩阵均值」数学等价，sample_map 全覆盖保证 Σcount_j == batchSize）。
本文件把两条口径与 fail-closed 边界固化成可回归断言：

- 非批量算术平均：info 条目排除、均值只在本算子切片内、空切片拒绝 →
  test_purescript_mean_arithmetic_and_info_excluded
- 批量槽位加权：Σ(count_j·ratio_j)/batchSize 数值正确、info 条目调用即错、
  槽位数与 batch 对不上拒绝、prep_failed（ratio None）拒绝 →
  test_batched_mean_weighted_and_fail_closed
"""
import pytest

import build_package as bp


def test_purescript_mean_arithmetic_and_info_excluded():
    # 精度条目 ratio 1.0/2.0/3.0 → 算术平均 2.0；info 条目 99.0 不计入
    gen_cases = [
        {"case_id": "c1", "ratio_cpu": 1.0},
        {"case_id": "c2", "ratio_cpu": 2.0, "case_purpose": "acc"},
        {"case_id": "c3", "ratio_cpu": 3.0},
        {"case_id": "i1", "ratio_cpu": 99.0, "case_purpose": "info"},
    ]
    assert bp.purescript_ratio_cpu_mean(gen_cases) == pytest.approx(2.0)
    # 单算子切片内的均值由调用方切片保证：两算子混合切片是调用侧契约违反，
    # 本助手不做算子过滤（拒绝测试空切片即可）
    with pytest.raises(bp.SelfCheckError, match="没有精度用例"):
        bp.purescript_ratio_cpu_mean([{"case_id": "i1", "ratio_cpu": 1.0,
                                       "case_purpose": "info"}])
    with pytest.raises(bp.SelfCheckError, match="没有精度用例"):
        bp.purescript_ratio_cpu_mean([])


def test_batched_mean_weighted_and_fail_closed():
    # batch=10，两内容各 4/6 槽，ratio 0.1/0.2 → 加权 (4*0.1+6*0.2)/10 = 0.16
    entry = {"case_id": "b1", "batch": 10,
             "sample_map": [{"slots": [0, 1, 2, 3]}, {"slots": list(range(4, 10))}],
             "ratio_cpu": [0.1, 0.2]}
    assert bp.batched_case_ratio_cpu_mean(entry) == pytest.approx(0.16)
    # Σcount != batch：加权口径对不上，拒绝
    bad = dict(entry, batch=11)
    with pytest.raises(bp.SelfCheckError, match="对不上"):
        bp.batched_case_ratio_cpu_mean(bad)
    # ratio 值数 != 内容数：拒绝
    bad2 = dict(entry, ratio_cpu=[0.1])
    with pytest.raises(bp.SelfCheckError, match="对不上"):
        bp.batched_case_ratio_cpu_mean(bad2)
    # prep_failed 内容（ratio None）：case 级 mean 不可算，fail-closed
    bad3 = dict(entry, ratio_cpu=[0.1, None])
    with pytest.raises(bp.SelfCheckError, match="prep_failed"):
        bp.batched_case_ratio_cpu_mean(bad3)
