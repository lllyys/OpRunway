"""Profiler 产物里逐次调用的 Kernel 总耗时。

`run_kit_perf.profiler_stats` 与性能量测骨架共用这一份认列规则**与统计量口径**：
两个消费者要的是同一件事——任务书规定的中位数与 90%分位。

末节把统计量口径钉在一起：`kernel_trace` 与 `assets/perf_harness.py` 各有一份
实现（骨架件是拷给算子改的，不能 import 本仓脚本），拿同一份语料比对，
与 `criterion_threshold` 那两处同一条纪律。
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import kernel_trace  # noqa: E402

# 一次调用两个 kernel，共三次调用：每次 100 / 200 / 300 us
ROWS = [
    ("1", "op_a", "60.0"), ("1", "op_b", "40.0"),
    ("2", "op_a", "120.0"), ("2", "op_b", "80.0"),
    ("3", "op_a", "180.0"), ("3", "op_b", "120.0"),
]


def write(tmp_path, header, rows, name=kernel_trace.DETAILS):
    out = tmp_path / "case" / "host_ascend_pt" / "ASCEND_PROFILER_OUTPUT"
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    path.write_text("\n".join([",".join(header)] + [",".join(r) for r in rows]) + "\n",
                    encoding="utf-8")
    return path


def test_groups_by_step_column(tmp_path):
    """有步号列时按它分组，一组一次调用。"""
    path = write(tmp_path, ["Step Id", "Name", "Duration(us)"], ROWS)
    assert kernel_trace.per_call_us(path) == [100.0, 200.0, 300.0]


def test_groups_by_kernel_occurrence_without_step_column(tmp_path):
    """没有步号列时按每个 kernel 名的第几次出现分组，结果与步号口径相同。"""
    rows = [(name, dur) for _, name, dur in ROWS]
    path = write(tmp_path, ["Name", "Duration(us)"], rows)
    assert kernel_trace.per_call_us(path) == [100.0, 200.0, 300.0]


def test_single_kernel_per_call(tmp_path):
    """实测形态：一次调用只有一个 kernel，torch_npu 不写步号列，每行就是一次调用。"""
    rows = [("op_a", "50.0"), ("op_a", "70.0"), ("op_a", "90.0")]
    path = write(tmp_path, ["Name", "Duration(us)"], rows)
    assert kernel_trace.per_call_us(path) == [50.0, 70.0, 90.0]


def test_step_column_wins(tmp_path):
    """两个来源都在时以步号列为准。"""
    rows = ROWS + [("3", "op_a", "0.0")]
    path = write(tmp_path, ["Step Id", "Name", "Duration(us)"], rows)
    assert kernel_trace.per_call_us(path) == [100.0, 200.0, 300.0]


def test_duration_column_probed_not_hardcoded(tmp_path):
    """耗时列按前缀认，不写死括号里的单位。"""
    path = write(tmp_path, ["Step Id", "Name", "Duration(ns)"], ROWS)
    assert kernel_trace.per_call_us(path) == [100.0, 200.0, 300.0]


@pytest.mark.parametrize("step_field", ["Step Id", "StepId", "Step", "Iteration"])
def test_step_column_spellings(tmp_path, step_field):
    """步号列的几种写法都认得出。"""
    path = write(tmp_path, [step_field, "Name", "Duration(us)"], ROWS)
    assert kernel_trace.per_call_us(path) == [100.0, 200.0, 300.0]


def test_uneven_kernel_counts_report_nothing(tmp_path):
    """各 kernel 名次数不齐时分不出组，报缺而不是猜一个分法。"""
    rows = [("op_a", "60.0"), ("op_a", "40.0"), ("op_b", "50.0")]
    path = write(tmp_path, ["Name", "Duration(us)"], rows)
    assert kernel_trace.per_call_us(path) == []


def test_no_step_and_no_name_column_reports_nothing(tmp_path):
    """两个来源都没有时报缺，不拿行数当调用次数。"""
    path = write(tmp_path, ["Duration(us)"], [("60.0",), ("40.0",)])
    assert kernel_trace.per_call_us(path) == []


def test_missing_duration_column_reports_nothing(tmp_path):
    """耗时列认不出时报缺。"""
    path = write(tmp_path, ["Step Id", "Name", "Cost"], ROWS)
    assert kernel_trace.per_call_us(path) == []


def test_unreadable_path_reports_nothing(tmp_path):
    """产物不在时返回空，不抛。"""
    assert kernel_trace.per_call_us(tmp_path / "nope.csv") == []


def test_steps_ordered_numerically_not_lexically(tmp_path):
    """步号按数值排序：字典序会把 10 排到 2 前面，逐次的列表就乱了。"""
    rows = [(str(i), "op_a", str(float(i))) for i in range(1, 12)]
    path = write(tmp_path, ["Step Id", "Name", "Duration(us)"], rows)
    assert kernel_trace.per_call_us(path) == [float(i) for i in range(1, 12)]


def test_newest_picks_latest_product(tmp_path):
    """同一个用例跑过多轮时取最新那份。"""
    old = write(tmp_path, ["Name", "Duration(us)"], [("op_a", "1.0")])
    import os
    import time
    time.sleep(0.01)
    new_dir = tmp_path / "case" / "host2_ascend_pt" / "ASCEND_PROFILER_OUTPUT"
    new_dir.mkdir(parents=True)
    new = new_dir / kernel_trace.DETAILS
    new.write_text("Name,Duration(us)\nop_a,2.0\n", encoding="utf-8")
    os.utime(new, (time.time() + 10, time.time() + 10))
    assert kernel_trace.newest(tmp_path) == new
    assert old.exists()


def test_active_tail_keeps_last_samples(tmp_path):
    """多出来的尾步是调度边界，取末尾 samples 个。"""
    assert kernel_trace.active_tail([1, 2, 3, 4, 5], 3) == [3, 4, 5]
    assert kernel_trace.active_tail([1, 2], 3) == [1, 2]
    assert kernel_trace.active_tail([1, 2], 0) == [1, 2]


# ------------------------------------------------------------ 统计量口径

# 任务书原话（sparse 三份逐字相同，SpGemm(A2A3) 4.198）：
#   「NPU侧每个case至少预热10次、正式采样30次，报告耗时中位数及90%分位耗时
#     （即约90%的正式采样耗时不高于该值）。」
# 括号里那句就是 p90 的判据，下面按它逐条核。

def _harness():
    """骨架件里的那一份实现。**不能 import 本仓脚本**，所以单独载进来。"""
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "assets" / "perf_harness.py"
    spec = importlib.util.spec_from_file_location("_perf_harness_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("n", [3, 5, 10, 11, 12, 15, 20, 25, 30, 40, 100])
def test_p90_is_the_smallest_value_with_ninety_percent_at_or_below(n):
    """任务书的定义：不高于该值的采样占比达到九成。**这是判据，不是某种插值。**"""
    values = [float(i) for i in range(1, n + 1)]
    got = kernel_trace.percentile_at_least(values, 0.9)
    at_or_below = sum(1 for v in values if v <= got)
    assert at_or_below / n >= 0.9
    # 最小性：再往下取一个观测值就不足九成了
    smaller = [v for v in values if v < got]
    if smaller:
        assert sum(1 for v in values if v <= max(smaller)) / n < 0.9


@pytest.mark.parametrize("n", [3, 5, 10, 11, 12, 15, 20, 25, 30, 40, 100])
def test_two_implementations_agree(n):
    """骨架件与本模块的 p90 必须逐条相同。

    两处认得不一样时，同一批采样在报告上会出现两个 90%分位，而两边都言之凿凿。
    """
    values = [float(i) * 1.7 for i in range(1, n + 1)]
    assert (_harness().percentile_at_least(values, 0.9)
            == kernel_trace.percentile_at_least(values, 0.9))


def test_summarize_reports_both_readings_and_no_mean():
    """任务书要中位数与 90%分位**两个**读数，平均值不在其中。"""
    # 三个数各不相同，这样「取了哪一个」在断言里分得出来：
    # 中位数 100、90%分位 500、平均 230——**尾部的两次抖动把平均拉高了 130%,
    # 而任务书要的两个读数都只动了该动的那个**。
    values = [100.0] * 8 + [500.0, 1000.0]
    got = kernel_trace.summarize(values)
    assert got["under_test_us"] == 100.0
    assert got["p90_us"] == 500.0
    assert got["max_us"] == 1000.0
    assert got["samples"] == 10
    assert "mean" not in got and "avg" not in got
    assert got["under_test_us"] != pytest.approx(sum(values) / len(values))


def test_summarize_agrees_with_harness():
    values = [12.5, 3.25, 88.0, 7.5, 41.0, 9.75, 60.0]
    mine = kernel_trace.summarize(values)
    theirs = _harness().summarize(values)
    for key in ("under_test_us", "p90_us", "min_us", "max_us"):
        assert mine[key] == theirs[key]


def test_summarize_on_empty_reports_nothing():
    """认不出逐次读数时返回 None，让调用方走「口径不符」那条路，不出错数。"""
    assert kernel_trace.summarize([]) is None
    assert kernel_trace.percentile_at_least([], 0.9) is None
