"""外部性能结果的收编与裁决。

**只测纯逻辑。** 采数要真机和真算子，一条都不在这里测；这里测的是交换格式
的校验与倍率裁决——判错时报告会给出一个看着像结论的错结论。
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import collect_perf  # noqa: E402
import verdict  # noqa: E402


def _payload(**over):
    data = {"source": "任务方 benchmark.py", "unit": "us",
            "cases": [{"id": 0, "under_test_us": 100.0, "baseline_us": 50.0},
                      {"id": 1, "under_test_us": 40.0, "baseline_us": 50.0}]}
    data.update(over)
    return data


# ---- 交换格式的校验 ------------------------------------------------------

def test_valid_payload_passes():
    assert collect_perf._check(_payload(), {0, 1}) == []


def test_wrong_unit_is_rejected():
    """别的单位在这里换算等于把换算错误藏进结论。"""
    assert any("unit" in p for p in collect_perf._check(_payload(unit="ms"), {0, 1}))


def test_missing_source_is_rejected():
    """谁采的这批数要进报告，否则读的人分不清它和 ATK 那轮。"""
    data = _payload()
    del data["source"]
    assert any("source" in p for p in collect_perf._check(data, {0, 1}))


def test_duplicate_id_is_rejected():
    data = _payload(cases=[{"id": 0, "under_test_us": 1.0},
                           {"id": 0, "under_test_us": 2.0}])
    assert any("多次" in p for p in collect_perf._check(data, {0}))


def test_stray_id_is_rejected():
    """外部工具用的是另一批用例时，两侧的数对不上号。"""
    problems = collect_perf._check(_payload(), {0})
    assert any("不在用例包里" in p for p in problems)


@pytest.mark.parametrize("bad", [0, -1, "40"])
def test_non_positive_time_is_rejected(bad):
    data = _payload(cases=[{"id": 0, "under_test_us": bad}])
    assert any("under_test_us" in p for p in collect_perf._check(data, {0}))


def test_missing_baseline_is_allowed():
    """没有基线的用例照收，报告里如实写「算不了倍率」。"""
    data = _payload(cases=[{"id": 0, "under_test_us": 10.0}])
    assert collect_perf._check(data, {0}) == []


# ---- 倍率 ----------------------------------------------------------------

def test_ratio_direction_matches_taskdoc():
    """倍率 = 标杆耗时 / 待验收耗时，**大于 1 表示更快**。方向反了结论也反。"""
    rows, summary = collect_perf._summarize(_payload()["cases"])
    assert rows[0]["ratio"] == 0.5      # 100us vs 标杆 50us：慢一半
    assert rows[1]["ratio"] == 1.25     # 40us  vs 标杆 50us：快
    assert summary["with_baseline"] == 2


def test_summary_skips_cases_without_baseline():
    cases = _payload()["cases"] + [{"id": 2, "under_test_us": 7.0}]
    rows, summary = collect_perf._summarize(cases)
    assert summary == {"total": 3, "with_baseline": 2,
                       "ratio_median": 0.875, "ratio_min": 0.5, "ratio_max": 1.25,
                       # 读数口径的对账跟着汇总走：这批用例没给 p90，
                       # 报告要能说出「3 条都没有 90%分位」。
                       "stat_off_spec": 0, "stat_off_spec_ids": [],
                       "without_p90": 3}
    assert rows[2]["ratio"] is None


# ---- 门槛抠取与裁决 ------------------------------------------------------

@pytest.mark.parametrize("text,expect", [
    ("性能倍率达到 0.25 倍性能标杆以上", 0.25),
    ("不低于 1 倍", 1.0),
    ("不劣于标杆", None),
    ("不低于 GPU 基线表", None),
])
def test_criterion_threshold(text, expect):
    """抠不到就不判。**抠错一个数比抠不到糟得多**——前者给出的是看着像结论的错结论。"""
    assert verdict._criterion_threshold(text) == expect


def _external(rows, **over):
    _, summary = collect_perf._summarize(rows)
    data = {"source": "任务方 benchmark.py", "rows":
            collect_perf._summarize(rows)[0], **summary}
    data.update(over)
    return data


def test_threshold_verdict_fails_when_any_case_below():
    rows = [{"id": 0, "under_test_us": 100.0, "baseline_us": 50.0},   # 0.5
            {"id": 1, "under_test_us": 500.0, "baseline_us": 50.0}]   # 0.1
    out = verdict._threshold_with_external(_external(rows),
                                           "性能倍率达到 0.25 倍性能标杆以上")
    assert out["status"].startswith("不达标")
    assert out["failed_ids"] == [1]


def test_threshold_verdict_passes_when_all_above():
    rows = [{"id": 0, "under_test_us": 100.0, "baseline_us": 50.0},
            {"id": 1, "under_test_us": 40.0, "baseline_us": 50.0}]
    out = verdict._threshold_with_external(_external(rows),
                                           "性能倍率达到 0.25 倍性能标杆以上")
    assert out["status"].startswith("达标")
    assert out["failed_ids"] == []


def test_threshold_without_numeric_criterion_stays_manual():
    rows = [{"id": 0, "under_test_us": 100.0, "baseline_us": 50.0}]
    out = verdict._threshold_with_external(_external(rows), "不低于 GPU 基线表")
    assert out["status"].startswith("待人工判定")
    assert "failed_ids" not in out


def test_threshold_without_any_baseline_reports_absolute_only():
    rows = [{"id": 0, "under_test_us": 100.0}]
    out = verdict._threshold_with_external(_external(rows),
                                           "性能倍率达到 0.25 倍性能标杆以上")
    assert "status" not in out
    assert any("baseline_us" in n for n in out["notes"])


def test_verdict_and_report_share_one_computation():
    """总结论与报告正文由同一个函数算，分两处算迟早互相打架。"""
    rows = [{"id": 0, "under_test_us": 500.0, "baseline_us": 50.0}]
    facts = {"performance": {"kind": "threshold",
                             "criterion": "性能倍率达到 0.25 倍性能标杆以上"}}
    ext = _external(rows)
    status, kind = verdict._perf_status(facts, {"passed": True}, {"x": 1}, None,
                                        [], None, ext)
    body = verdict._threshold_with_external(ext, facts["performance"]["criterion"])
    assert kind == "threshold"
    assert status == body["status"]


# ---- 两条门槛：逐条与算术平均 -------------------------------------------
#
# 实测任务书原话：「所有场景的性能倍率均须大于 0.25 倍，全部量化场景性能倍率的
# 算术平均值须不低于 0.35 倍」。**只判逐条等于只判了一半。**

TWO_LINE = "所有场景的性能倍率均须大于 0.25 倍，全部量化场景性能倍率的算术平均值须不低于 0.35 倍"


@pytest.mark.parametrize("text,expect", [
    (TWO_LINE, 0.35),
    ("性能倍率达到 0.25 倍性能标杆以上", None),
    ("算术平均不低于 1.5 倍", 1.5),
])
def test_mean_threshold_extraction(text, expect):
    assert verdict._criterion_mean_threshold(text) == expect


def test_mean_below_floor_fails_even_when_每条都过():
    """逐条全过、均值不够 —— 这一档从前会被判成达标。"""
    rows = [{"id": 0, "under_test_us": 100.0, "baseline_us": 30.0},   # 0.3
            {"id": 1, "under_test_us": 100.0, "baseline_us": 32.0}]   # 0.32
    out = verdict._threshold_with_external(_external(rows), TWO_LINE)
    assert out["failed_ids"] == []
    assert out["status"].startswith("不达标")
    assert "算术平均" in out["status"]


def test_both_thresholds_met():
    rows = [{"id": 0, "under_test_us": 100.0, "baseline_us": 40.0},   # 0.4
            {"id": 1, "under_test_us": 100.0, "baseline_us": 50.0}]   # 0.5
    out = verdict._threshold_with_external(_external(rows), TWO_LINE)
    assert out["status"].startswith("达标")
    assert out["ratio_mean"] == 0.45


def test_single_threshold_criterion_has_no_mean_check():
    """只有一条门槛的任务书不受影响。"""
    rows = [{"id": 0, "under_test_us": 100.0, "baseline_us": 30.0}]
    out = verdict._threshold_with_external(rows and _external(rows),
                                           "性能倍率达到 0.25 倍性能标杆以上")
    assert out["status"].startswith("达标")
    assert "mean_threshold" not in out


# ---- 采样口径：上限不是固定值 ------------------------------------------

ASSETS = Path(__file__).resolve().parents[1] / "assets"
sys.path.insert(0, str(ASSETS))

import perf_harness  # noqa: E402


def test_sampling_is_never_reduced_by_default():
    """**默认一次都不减。** 次数是任务书规定的验收口径，减了数就不能用于验收。

    实测数：单次 400166 us、基线 289 us，差 1385 倍——结论早已定，但口径不许动。
    """
    assert perf_harness.plan_sampling(400166, 289, 10, 30)[:2] == (10, 30)
    assert perf_harness.plan_sampling(40e6, None, 10, 30)[:2] == (10, 30)
    assert perf_harness.BUDGET_S == 0 and perf_harness.STOP_FACTOR == 0


def test_probing_mode_marks_the_data_as_off_spec(tmp_path):
    """摸底口径要显式开，且开了就打标记——不打标记就会混进验收结论。"""
    warmup, samples, why = perf_harness.plan_sampling(400166, 289, 10, 30, 0, 50)
    assert (warmup, samples) == (1, perf_harness.MIN_SAMPLES) and "结论已定" in why
    warmup, samples, why = perf_harness.plan_sampling(40e6, None, 10, 30, 60e6, 0)
    assert samples == perf_harness.MIN_SAMPLES and "预算" in why


def test_a_case_near_the_threshold_is_sampled_in_full():
    # 3.5 倍，就在门槛附近——**这种才最需要采够**，摸底口径也不许碰它。
    assert perf_harness.plan_sampling(1000, 289, 10, 30, 0, 50)[:2] == (10, 30)


def test_collect_perf_refuses_off_spec_data():
    """摸底数看着和正常数一模一样，混进去报告上分辨不出来，所以在收编处拦。"""
    bad = {"unit": "us", "source": "x",
           "cases": [{"id": 1, "under_test_us": 1.0, "off_spec": True}]}
    assert any("摸底数" in p for p in collect_perf._check(bad, None))


def test_sampling_spec_must_have_a_source():
    """口径定不下来时脚本停下，不替人猜一个次数。"""
    class _Args:
        warmup = samples = None
        facts = ""
    assert perf_harness._sampling(_Args())[0] is None


def test_sampling_spec_comes_from_facts(tmp_path):
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"performance": {"sampling": {
        "warmup": 10, "samples": 30, "source": "任务书 3.2 第 198 条"}}}),
        encoding="utf-8")

    class _Args:
        warmup = samples = None
    _Args.facts = str(facts)
    got = perf_harness._sampling(_Args())
    assert got[:2] == (10, 30) and "198" in got[2]


def _probe(ident, ratio, seconds):
    return {"id": ident, "perf_ratio": ratio, "estimate_s": seconds}


def test_only_cases_settled_by_an_order_of_magnitude_count():
    """门槛附近的一条都不算——那些正是要采满才判得准的。"""
    rows = [_probe("远低于门槛", 0.0007, 1600),     # 门槛 0.25 的 1/357
            _probe("刚好低一点", 0.20, 1600),        # 不达标，但没有数量级差距
            _probe("达标", 0.9, 1600)]
    got = [r["id"] for r in perf_harness.settled_cases(rows, 0.25)]
    assert got == ["远低于门槛"]


def test_a_slow_but_passing_operator_is_never_interrupted():
    """算子本来就要跑很久而性能达标，是正常验收，不该打断。"""
    rows = [_probe("慢但达标", 0.9, 3600)]
    assert perf_harness.settled_cases(rows, 0.25) == []


def test_a_settled_case_that_is_quick_is_not_worth_asking_about():
    """结论虽已定，但十几秒就跑完——问的成本比跑还高。判据是合取。"""
    rows = [_probe("已定但很快", 0.0007, 16)]
    settled = perf_harness.settled_cases(rows, 0.25)
    assert settled and sum(r["estimate_s"] for r in settled) < perf_harness.ASK_AFTER_S


def test_no_threshold_means_no_judgement():
    """门槛抠不到就不判，与 verdict.py 同一条规矩：抠错比抠不到糟得多。"""
    assert perf_harness.settled_cases([_probe("x", 0.0007, 3600)], None) == []


# 门槛抽取的标定语料。前七条是**真实任务书原话**（2026-09 实测，两个算子），
# 后五条是边界。两处实现拿同一份语料钉：`verdict.py` 判结论、量测件判要不要
# 停下来问，认得不一样时一边说「结论已定」另一边说「待人工判定」。
CRITERIA = [
    ("性能倍率达到 0.25 倍性能标杆以上", 0.25),
    ("本行各dtype：NPU性能 > 0.25 × GPU A100性能", 0.25),
    ("float16/bfloat16/float32：NPU性能 ≥ 1.0 × GPU A100性能", 1.0),
    ("complex64性能 ≥ 0.8 × GPU A100性能", 0.8),
    ("所有场景的性能倍率均须大于NVIDIA A100的0.25倍，"
     "全部量化场景性能倍率的算术平均值须不低于NVIDIA A100的0.35倍", 0.25),
    ("float32性能不低于NVIDIA A100的1.0倍", 1.0),
    # `case×dtype` 里的 × 前面没有数字，不许命中它。
    ("以每个case×dtype组合为一个性能场景，性能倍率均须大于 0.25 倍", 0.25),
    # 耗时口径：与性能倍率互为倒数，折算过来。
    ("耗时不超过基线的 4 倍", 0.25),
    ("单次耗时须在标杆的 2 倍以内", 0.5),
    # 抠不出可比的数，一律不判。
    ("不劣于 aclnnMedian 小算子拼接版本", None),
    ("达到 compute bound 或 memory bound 的 80%", None),
    ("性能要求：无", None),
]


@pytest.mark.parametrize("criterion, want", CRITERIA)
def test_threshold_is_read_from_the_task_doc_wording(criterion, want):
    got = perf_harness.criterion_threshold(criterion)
    if want is None:
        assert got is None
    else:
        assert got == pytest.approx(want)


@pytest.mark.parametrize("criterion, want", CRITERIA)
def test_both_implementations_read_the_same_wording(criterion, want):
    """量测件与 verdict 必须认同一套写法，否则两边给出相反的提示。"""
    assert verdict._criterion_threshold(criterion) == pytest.approx(
        perf_harness.criterion_threshold(criterion))


def test_the_mean_floor_accepts_the_same_multiplier_forms():
    """均值门槛与逐条门槛认同一套：只认「倍」时用 × 的那份会漏掉一半要求。"""
    assert verdict._criterion_mean_threshold(
        "逐条 > 0.25 ×，算术平均值不低于 0.35 ×") == 0.35
    assert verdict._criterion_mean_threshold("只有逐条门槛 0.25 倍") is None


def test_the_two_constants_bracket_the_measured_anchors():
    # 自带件性能集 50 条分片并行实测 161 秒，属正常轮，不该触发。
    assert 161.0 < perf_harness.ASK_AFTER_S
    # 实测倍率 0.0007 对门槛 0.25，差 346 倍，远在数量级线之外。
    assert 0.25 / 0.0007 > perf_harness.SETTLED_MARGIN


# ------------------------------------------------- 任务书口径贯通到报告

# 任务书要求（sparse 三份逐字相同）：「报告耗时中位数及90%分位耗时」。
# 量测件把两个数都算了出来，此前 `_summarize` 只透传中位数，**90%分位在收编那一步
# 丢掉，报告里一个字都没有**。下面几条钉的就是这条链路。

def test_p90_survives_collection():
    """p90 与采样次数要跟着数走到报告，丢一个就是少报任务书要求的一项。"""
    cases = [{"id": 0, "under_test_us": 41.2, "p90_us": 44.9, "baseline_us": 34.688,
              "min_us": 40.0, "max_us": 50.0, "samples": 30}]
    rows, summary = collect_perf._summarize(cases)
    assert rows[0]["p90_us"] == 44.9
    assert rows[0]["samples"] == 30
    assert summary["without_p90"] == 0
    assert summary["stat_off_spec"] == 0


def test_mean_reading_is_marked_not_silently_mixed():
    """退回自带件平均列的条目要带 `stat_kind`，**不能和中位数那批长得一样**。"""
    cases = [{"id": 0, "under_test_us": 41.2, "p90_us": 44.9, "baseline_us": 34.7},
             {"id": 1, "under_test_us": 88.0, "baseline_us": 44.0,
              "stat_kind": "mean", "stat_note": "Profiler 逐次读数认不出"}]
    rows, summary = collect_perf._summarize(cases)
    assert rows[0]["stat_kind"] == "median_p90"
    assert rows[1]["stat_kind"] == "mean"
    assert summary["stat_off_spec"] == 1
    assert summary["stat_off_spec_ids"] == [1]
    # 倍率照算——那一条仍然是个性能场景，只是读数口径要标出来
    assert rows[1]["ratio"] == 0.5


def test_report_carries_per_scenario_readings():
    """报告要逐场景摆出两个读数，按来源分组的汇总替代不了它。"""
    external = {"source": "判据表", "rows": [
        {"id": 0, "label": "P-01 fp16", "source": "判据表", "under_test_us": 41.2,
         "p90_us": 44.9, "baseline_us": 34.688, "ratio": 0.842,
         "stat_kind": "median_p90"}]}
    got = verdict._threshold_with_external(external, "性能倍率均须大于 0.25 倍")
    scenarios = got["external_scenarios"]
    assert len(scenarios) == 1
    assert scenarios[0]["median_us"] == 41.2
    assert scenarios[0]["p90_us"] == 44.9
    assert scenarios[0]["stat"] == "中位数"


def test_off_spec_readings_are_called_out_in_the_report_body():
    """口径不符要写进正文，只放在 JSON 字段里读者看不见。"""
    external = {"source": "自带件", "rows": [
        {"id": 7, "under_test_us": 88.0, "baseline_us": 44.0, "ratio": 0.5,
         "stat_kind": "mean"}]}
    got = verdict._threshold_with_external(external, "性能倍率均须大于 0.25 倍")
    assert got["stat_off_spec_ids"] == [7]
    assert got["external_scenarios"][0]["stat"] == "平均值(非任务书口径)"
    assert any("不是任务书口径" in note for note in got["notes"])
