"""自带件性能集并进结论，以及出结论前的产物对账。

**采了不用是最贵的一种浪费**：真机时间已经花掉，而报告的覆盖面还写错。
实测一轮里 50 条采齐落盘却没进结论，报告写着「8 个场景」。
"""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verdict  # noqa: E402

EXTERNAL = {
    "source": "判据表",
    "rows": [{"id": "P-01", "under_test_us": 50.0, "baseline_us": 100.0, "ratio": 2.0},
             {"id": "P-02", "under_test_us": 100.0, "baseline_us": 20.0, "ratio": 0.2}],
    "total": 2, "with_baseline": 2,
    "ratio_median": 1.1, "ratio_min": 0.2, "ratio_max": 2.0,
}
KIT = {
    "source": "自带件泛化集",
    "cases": [{"id": "k-0", "under_test_us": 100.0, "baseline_us": 300.0},
              {"id": "k-1", "under_test_us": 100.0, "baseline_us": 50.0}],
}


def test_kit_cases_join_the_same_scenario_set():
    merged = verdict._merge_kit_perf(EXTERNAL, KIT)
    assert merged["total"] == 4
    assert {r["id"] for r in merged["rows"]} == {"P-01", "P-02", "k-0", "k-1"}
    assert merged["ratio_min"] == 0.2
    assert merged["ratio_max"] == 3.0
    # 每行都带来源，报告才分得开哪批测了多少条
    assert {r["source"] for r in merged["rows"]} == {"判据表", "自带件泛化集"}


def test_merge_is_a_noop_without_kit_results():
    assert verdict._merge_kit_perf(EXTERNAL, None) is EXTERNAL
    assert verdict._merge_kit_perf(EXTERNAL, {"cases": []}) is EXTERNAL


def test_merge_keeps_the_criterion_table_row_on_id_clash():
    kit = {"source": "自带件", "cases": [{"id": "P-01", "under_test_us": 1.0,
                                          "baseline_us": 1.0}]}
    merged = verdict._merge_kit_perf(EXTERNAL, kit)
    assert merged["total"] == 2
    assert [r for r in merged["rows"] if r["id"] == "P-01"][0]["ratio"] == 2.0


def test_by_source_counts_below_threshold_per_batch():
    merged = verdict._merge_kit_perf(EXTERNAL, KIT)
    groups = verdict._perf_by_source(merged["rows"], "倍率均须大于 0.25 倍")
    by = {g["source"]: g for g in groups}
    assert by["判据表"]["total"] == 2
    assert by["判据表"]["below_threshold"] == 1
    assert by["判据表"]["below_ids"] == ["P-02"]
    assert by["自带件泛化集"]["below_threshold"] == 0


def test_reconcile_flags_stage_files_nobody_read(tmp_path):
    (tmp_path / "accuracy.json").write_text(json.dumps({"failed_ids": [1, 2]}),
                                            encoding="utf-8")
    (tmp_path / "kit_perf_exchange.json").write_text(json.dumps({"cases": [{}] * 50}),
                                                     encoding="utf-8")
    (tmp_path / "mystery.json").write_text(json.dumps({"cases": [{}] * 7}),
                                           encoding="utf-8")
    (tmp_path / "scalars.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

    (tmp_path / "perf_p_table_raw.json").write_text(json.dumps({"rows": [{}] * 8}),
                                                    encoding="utf-8")

    found = verdict._reconcile_stage(tmp_path)
    # 读过的、只有标量的、已被加工进结论的中间产物都不报
    assert found == [{"file": "mystery.json", "rows": 7}]


def test_derived_stage_only_lists_products_actually_folded_in():
    """拿 DERIVED_STAGE 给一份没人读的产物消音，对账就白装了。"""
    source = (SCRIPTS / "verdict.py").read_text(encoding="utf-8")
    assert "performance_external.json" in source
    assert not (verdict.DERIVED_STAGE & set(verdict.CONSUMED_STAGE))


def test_reconcile_survives_unreadable_json(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert verdict._reconcile_stage(tmp_path) == []


def test_every_stage_file_verdict_loads_is_registered():
    """新增一处 `_load(stage / "x.json")` 就要往 CONSUMED_STAGE 加一行，
    否则对账会把自己读过的文件报成「采了没用」。"""
    source = (SCRIPTS / "verdict.py").read_text(encoding="utf-8")
    import re
    loaded = set(re.findall(r'_load\(stage / "([^"]+)"', source))
    assert loaded <= set(verdict.CONSUMED_STAGE), loaded - set(verdict.CONSUMED_STAGE)
