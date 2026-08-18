import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import parse_atk_report
from _policy import load_policy


class ReportParserTest(unittest.TestCase):
    def test_non_finite_label_does_not_change_rate(self):
        cases = [
            {"passed": True, "non_finite_input": True},
            {"passed": True},
        ]
        totals = parse_atk_report.totals_of("accuracy", cases)
        self.assertEqual(totals["cases"], 2)
        self.assertEqual(totals["passed"], 2)
        self.assertEqual(totals["failed"], 0)
        self.assertEqual(totals["pass_rate"], 1.0)

    def test_standard_summary_checks_every_case(self):
        cases = [
            {"standard": {"acc": "default"}},
            {"standard": {"acc": "equal"}},
        ]
        summary = parse_atk_report.summarize_standard_acc(cases)
        self.assertFalse(summary["consistent"])
        self.assertEqual(summary["present_cases"], 2)
        self.assertEqual(len(summary["values"]), 2)

    def test_standard_summary_rejects_missing_values(self):
        summary = parse_atk_report.summarize_standard_acc([{}, {}])
        self.assertFalse(summary["consistent"])
        self.assertEqual(summary["present_cases"], 0)

    def test_explicit_invalid_case_is_removed_from_denominator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "excluded_cases.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "cases": [{
                    "id": "1",
                    "stage": "smoke",
                    "cause": "invalid_case_data",
                    "reason": "attr 无法形成有效调用",
                    "evidence": "evidence/smoke.log",
                }],
            }), encoding="utf-8")
            excluded, active = parse_atk_report.load_exclusions(
                path,
                [{"id": "1"}, {"id": "2"}],
                [{"id": "1", "passed": False}, {"id": "2", "passed": True}],
                {},
                set(load_policy()["case_exclusions"]["allowed_causes"]),
            )
        self.assertEqual(["1"], [case["id"] for case in excluded])
        self.assertEqual(["2"], [case["id"] for case in active])

    def test_passed_case_cannot_be_excluded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "excluded_cases.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "cases": [{
                    "id": "1",
                    "stage": "accuracy",
                    "cause": "invalid_case_data",
                    "reason": "错误记录",
                    "evidence": "evidence/accuracy.log",
                }],
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_atk_report.load_exclusions(
                    path,
                    [{"id": "1"}],
                    [{"id": "1", "passed": True}],
                    {},
                    set(load_policy()["case_exclusions"]["allowed_causes"]),
                )


if __name__ == "__main__":
    unittest.main()


class AccuracyVerdictColumnTest(unittest.TestCase):
    """精度判定要读全部 `*_精度通过` 列，但不能退化成"任一为真即通过"。

    真实事故（median 验收）：ATK 把常规精度结果挂在基准节点列
    （cpu_0_精度通过），把"报错与预期一致"的 error-match 用例挂在待验收算子节点列
    （pyaclnn_0_精度通过），同一行只有一个单元有值。只按"哪列数据多"选一列，
    error-match 用例被误判成失败——解析得 80.56%，ATK 自报 83.33%。

    但并集的 OR 语义会在多节点拓扑下把"一个节点过、另一个节点挂"判成通过，
    那是假通过。所以非空单元互相矛盾时判不通过并记冲突，交给人裁决。
    """

    HEADER = ["编号", "cpu_0_精度通过", "pyaclnn_0_精度通过", "精度详情",
              "执行结果", "失败原因", "数据范围"]

    def _parse(self, rows):
        return parse_atk_report.parse_accuracy(self.HEADER, rows, {})

    def test_normal_case_reads_the_benchmark_column(self):
        cases, meta = self._parse([["1", "True", None, "", "", "", ""]])
        self.assertTrue(cases[0]["passed"])
        self.assertEqual(
            ["cpu_0_精度通过", "pyaclnn_0_精度通过"], meta["verdict_columns"])

    def test_error_match_case_reads_the_candidate_column(self):
        cases, _ = self._parse([["0", None, "True", "", "", "", ""]])
        self.assertTrue(cases[0]["passed"])
        self.assertNotIn("verdict_conflict", cases[0])

    def test_failure_in_the_benchmark_column_stays_a_failure(self):
        cases, _ = self._parse([["2", "False", None, "", "", "", ""]])
        self.assertFalse(cases[0]["passed"])

    def test_disagreeing_columns_do_not_become_a_pass(self):
        cases, _ = self._parse([["3", "False", "True", "", "", "", ""]])
        self.assertFalse(cases[0]["passed"])
        self.assertEqual(
            {"cpu_0_精度通过": "False", "pyaclnn_0_精度通过": "True"},
            cases[0]["verdict_conflict"])

    def test_agreeing_columns_need_no_conflict_record(self):
        cases, _ = self._parse([["4", "True", "True", "", "", "", ""]])
        self.assertTrue(cases[0]["passed"])
        self.assertNotIn("verdict_conflict", cases[0])

    def test_empty_row_is_not_a_pass(self):
        cases, _ = self._parse([["5", None, None, "", "", "", ""]])
        self.assertFalse(cases[0]["passed"])

    def test_mixed_report_matches_atk_self_reported_rate(self):
        # median global_float 的形态：1 条 error-match + 5 条常规，其中 1 条失败。
        # 单列读取会把 error-match 判失败，通过率从 5/6 掉到 4/6。
        rows = [
            ["0", None, "True", "", "", "", ""],
            ["1", "True", None, "", "", "", ""],
            ["2", "True", None, "", "", "", ""],
            ["3", "True", None, "", "", "", ""],
            ["4", "True", None, "", "", "", ""],
            ["5", "False", None, "", "", "", ""],
        ]
        cases, _ = self._parse(rows)
        totals = parse_atk_report.totals_of("accuracy", cases)
        self.assertEqual(6, totals["cases"])
        self.assertEqual(5, totals["passed"])
        self.assertEqual(round(5 / 6, 4), totals["pass_rate"])
