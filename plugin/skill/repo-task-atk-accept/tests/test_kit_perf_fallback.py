"""从 Profiler 产物按任务书口径取数（中位数与 90%分位）。

**这是主口径，不是回落。** 自带件汇总的 `kernel_total_us` 是总耗时除以步数，
一个平均值；任务书要的是「报告耗时中位数及90%分位耗时」，两个统计量它一个都不是。

**两个列名一律探测。** 步号列写死时列名一变就每行取到 `None`，去重后除数是 1，
倍率整体大出一个采样步数而退出码仍是 0——错得没有任何报错。

**步号列可以整个不存在。** 一次调用只有一个 kernel 的路径，torch_npu 不写这一列
（实测 SpMM 那轮 50 条里 25 条 fp32 如此）。那时调用次数改数每个 kernel 名出现的
次数，两法在有步号列的产物上逐条等价（实测 25 条差异 0）。
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_kit_perf  # noqa: E402

# 一步两个 kernel，共两步：每步 100 us，回落应得 100.0
ROWS = [
    ("5", "op_a", "60.0"),
    ("5", "op_b", "40.0"),
    ("6", "op_a", "62.0"),
    ("6", "op_b", "38.0"),
]


def write_details(work, case_id, header, rows):
    out = work / "tr0" / case_id / "host_ascend_pt" / "ASCEND_PROFILER_OUTPUT"
    out.mkdir(parents=True)
    lines = [",".join(header)] + [",".join(row) for row in rows]
    (out / "kernel_details.csv").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")
    return work


@pytest.mark.parametrize("step_field", ["Step Id", "StepId", "Step"])
def test_step_column_probed(tmp_path, step_field):
    """步号列的几种写法都认得出，除数取去重步数。"""
    work = write_details(tmp_path, "c0", [step_field, "Name", "Duration(us)"],
                         ROWS)
    assert run_kit_perf.profiler_stats(work, "c0")["under_test_us"] == pytest.approx(100.0)


def test_duration_column_probed(tmp_path):
    """耗时列按前缀认，不写死括号里的单位。"""
    work = write_details(tmp_path, "c0", ["Step Id", "Name", "Duration(ns)"],
                         ROWS)
    assert run_kit_perf.profiler_stats(work, "c0")["under_test_us"] == pytest.approx(100.0)


def test_no_step_column_falls_back_to_kernel_name_count(tmp_path):
    """没有步号列时数 kernel 名的出现次数，得数与步号口径相同。"""
    work = write_details(tmp_path, "c0", ["Cycle", "Name", "Duration(us)"],
                         ROWS)
    assert run_kit_perf.profiler_stats(work, "c0")["under_test_us"] == pytest.approx(100.0)


def test_single_kernel_per_call_has_no_step_column(tmp_path):
    """实测形态：一次调用一个 kernel、没有步号列，除数是调用次数不是 1。"""
    rows = [("op_a", "60.0"), ("op_a", "40.0"), ("op_a", "50.0")]
    work = write_details(tmp_path, "c0", ["Name", "Duration(us)"], rows)
    assert run_kit_perf.profiler_stats(work, "c0")["under_test_us"] == pytest.approx(50.0)


def test_blank_step_values_fall_back_to_kernel_name_count(tmp_path):
    """步号列在但整列是空的，回落到名字计数，而不是按一步算成 200。"""
    rows = [("", name, dur) for _, name, dur in ROWS]
    work = write_details(tmp_path, "c0", ["Step Id", "Name", "Duration(us)"],
                         rows)
    assert run_kit_perf.profiler_stats(work, "c0")["under_test_us"] == pytest.approx(100.0)


def test_uneven_kernel_counts_report_nothing(tmp_path):
    """各 kernel 名次数不齐时推不出调用次数，报缺而不是猜一个。"""
    rows = [("op_a", "60.0"), ("op_a", "40.0"), ("op_b", "50.0")]
    work = write_details(tmp_path, "c0", ["Name", "Duration(us)"], rows)
    assert run_kit_perf.profiler_stats(work, "c0") is None


def test_no_step_and_no_name_column_reports_nothing(tmp_path):
    """两个来源都没有时报缺，不拿行数当除数。"""
    rows = [("60.0",), ("40.0",)]
    work = write_details(tmp_path, "c0", ["Duration(us)"], rows)
    assert run_kit_perf.profiler_stats(work, "c0") is None


def test_missing_duration_column_reports_nothing(tmp_path):
    """耗时列认不出时报缺。"""
    work = write_details(tmp_path, "c0", ["Step Id", "Name", "Cost"], ROWS)
    assert run_kit_perf.profiler_stats(work, "c0") is None


def test_step_column_wins_over_kernel_name_count(tmp_path):
    """两个来源都在时以步号列为准。"""
    rows = ROWS + [("6", "op_a", "0.0")]
    work = write_details(tmp_path, "c0", ["Step Id", "Name", "Duration(us)"],
                         rows)
    # 步号 2 个 -> 100.0；按名字算 op_a 出现 3 次、op_b 2 次，次数不齐会报缺
    assert run_kit_perf.profiler_stats(work, "c0")["under_test_us"] == pytest.approx(100.0)


def test_divisor_is_not_row_count(tmp_path):
    """除数是调用次数不是行数：行数会算成平均单 kernel 耗时，差一个 kernel 个数。"""
    work = write_details(tmp_path, "c0", ["Step Id", "Name", "Duration(us)"],
                         ROWS)
    got = run_kit_perf.profiler_stats(work, "c0")["under_test_us"]
    assert got == pytest.approx(100.0)
    assert got != pytest.approx(200.0 / len(ROWS))


def test_no_product_reports_nothing(tmp_path):
    """产物根本不在时返回 None。"""
    assert run_kit_perf.profiler_stats(tmp_path, "c0") is None


def test_reading_is_median_and_p90_not_the_mean(tmp_path):
    """**取的必须是任务书那两个读数，不是平均值。**

    上面几条的语料每次调用耗时相同，平均与中位数恰好相等——那样的语料放行
    「悄悄改回取平均」这一类改动（实测：把实现换成 `sum/len` 后那些断言全过）。
    这里的四次调用是 100 / 100 / 100 / 500：中位数 100、90%分位 500、平均 200，
    三个数互不相同，取错哪一个都会在断言上暴露。
    """
    rows = [("1", "op_a", "100.0"), ("2", "op_a", "100.0"),
            ("3", "op_a", "100.0"), ("4", "op_a", "500.0")]
    work = write_details(tmp_path, "c0", ["Step Id", "Name", "Duration(us)"], rows)
    got = run_kit_perf.profiler_stats(work, "c0")
    assert got["under_test_us"] == pytest.approx(100.0)
    assert got["p90_us"] == pytest.approx(500.0)
    assert got["samples"] == 4
    assert got["under_test_us"] != pytest.approx(200.0)   # 平均值
