import json
import os
import tempfile
import unittest

import render_acceptance_markdown as R


class RenderAcceptanceMarkdownTest(unittest.TestCase):
    def test_renders_structured_and_legacy_text_gaps(self):
        self.assertEqual(
            R._gap_items("单条自由文本"),
            ["单条自由文本"],
        )
        self.assertEqual(
            R._gap_line("缺少无 dim 的全局 overload"),
            "- 缺少无 dim 的全局 overload",
        )
        self.assertEqual(
            R._gap_line({
                "issue": "dtype_deferred",
                "impact": "暂缓",
                "pr_fact": "op_def 不支持",
            }),
            "- `dtype_deferred`：暂缓（PR 事实：op_def 不支持）",
        )
        self.assertEqual(
            R._gap_line({
                "kind": "dtype_deferred",
                "dtypes": ["int32"],
                "reason": "runner 未支持",
            }),
            '- `dtype_deferred`：runner 未支持；补充：{"dtypes": ["int32"]}',
        )

    def test_renders_existing_verdict_without_rejudging(self):
        with tempfile.TemporaryDirectory() as root:
            docs = {
                "acceptance.json": {
                    "op": "X", "overall": "FAIL(精度)", "state": "FAILED_PRECISION",
                    "precision_verdict": "fail", "perf_status": "skipped_precision_gate",
                    "repo_mode": "cpp_extension", "gate": {"passed": True},
                },
                "verdict.json": {
                    "op": "X", "standard": "ascendoptest_default",
                    "accuracy_summary": {
                        "total": 2, "passed": 1, "failed": 1,
                        "overall_pass_rate": 0.5,
                        "by_dtype": [{"dtype": "float32", "count": 2, "passed": 1,
                                      "failed": 1, "uncertain": 0, "pass_rate": 0.5}],
                    },
                    "overall": {"counts": {"total": 2, "fail": 1}},
                    "per_case": [
                        {"case_id": "a", "精度": "pass", "判据": "ok"},
                        {"case_id": "b", "精度": "fail", "判据": "mismatch=1"},
                    ],
                },
                "perf_report.json": {
                    "summary": {"status": "skipped_precision_gate", "planned_cases": 1,
                                "perf_cases": 0, "cases_scored": 0, "达标": 0},
                    "by_shape_class": [{"class": "small", "planned_cases": 1,
                                        "cases": 0, "cases_scored": 0, "达标": 0}],
                    "non_passing_cases": [],
                },
                "evidence.json": {"cpp_extension_receipt": {}},
                "caseset.json": {"op": "X", "task_pr_gaps": []},
            }
            for name, value in docs.items():
                with open(os.path.join(root, name), "w", encoding="utf-8") as out:
                    json.dump(value, out)
            path = R.write_report(root)
            with open(path, encoding="utf-8") as src:
                text = src.read()
            self.assertIn("# X 算子验收报告", text)
            self.assertIn("`FAIL(精度)`", text)
            self.assertIn("| `float32` | 2 | 1 | 1 |", text)
            self.assertIn("[精度失败明细.md](精度失败明细.md)", text)
            self.assertNotIn("./repro/show_case.sh b", text)
            self.assertIn("./repro/audit_case.sh 1", text)
            self.assertIn("性能未执行", text)
            detail = os.path.join(root, "精度失败明细.md")
            self.assertTrue(os.path.isfile(detail))
            with open(detail, encoding="utf-8") as src:
                detail_text = src.read()
            self.assertIn("./repro/review.sh show 1", detail_text)
            self.assertIn("./repro/audit_case.sh 1", detail_text)
            self.assertIn("`b`", detail_text)
            self.assertFalse(os.path.exists(os.path.join(root, "性能失败明细.md")))

    def test_splits_performance_non_passing_detail(self):
        with tempfile.TemporaryDirectory() as root:
            docs = {
                "acceptance.json": {
                    "op": "X", "overall": "FAIL(性能)", "state": "FAILED_PERFORMANCE",
                    "precision_verdict": "pass", "perf_status": "failed",
                    "repo_mode": "cpp_extension", "gate": {"passed": True},
                },
                "verdict.json": {
                    "op": "X", "standard": "s",
                    "accuracy_summary": {"total": 1, "passed": 1, "failed": 0,
                                         "overall_pass_rate": 1.0, "by_dtype": []},
                    "per_case": [{"case_id": "p0", "精度": "pass", "判据": "ok"}],
                },
                "perf_report.json": {
                    "summary": {"status": "failed", "planned_cases": 1,
                                "perf_cases": 1, "cases_scored": 1, "达标": 0},
                    "by_shape_class": [],
                    "non_passing_cases": [{
                        "case_id": "p0", "outcome": "failed", "reason": "ratio below threshold",
                        "dtype": "float16", "shape_class": "large",
                        "inputs": [{"name": "self", "shape": [128, 128]}],
                        "ratio": 0.5, "target_ratio": 1.0,
                        "custom": {"us": 4.0}, "baseline": {"us": 2.0},
                    }],
                },
                "evidence.json": {"cpp_extension_receipt": {}},
                "caseset.json": {
                    "op": "X", "task_pr_gaps": [],
                    "cases": [{
                        "id": "p0",
                        "inputs": [{"name": "self", "shape": [128, 128],
                                    "dtype": "float16"}],
                        "attrs": {"dim": 0, "keepDim": False},
                        "aclnn_call": {"symbol": "ExampleDim"},
                    }],
                },
            }
            for name, value in docs.items():
                with open(os.path.join(root, name), "w", encoding="utf-8") as out:
                    json.dump(value, out)
            path = R.write_report(root)
            with open(path, encoding="utf-8") as src:
                text = src.read()
            self.assertIn("[性能失败明细.md](性能失败明细.md)", text)
            self.assertNotIn("ratio below threshold", text)
            detail = os.path.join(root, "性能失败明细.md")
            with open(detail, encoding="utf-8") as src:
                detail_text = src.read()
            self.assertIn("ratio below threshold", detail_text)
            self.assertIn("`p0`", detail_text)
            self.assertIn("`[[128, 128]]`", detail_text)
            self.assertIn('属性：`{"dim": 0, "keepDim": false}`', detail_text)
            self.assertIn("DUT 接口：`ExampleDim`", detail_text)
            self.assertIn("要求阈值：`1.0`", detail_text)
            self.assertIn("缺单 case 性能重放能力", detail_text)
            self.assertFalse(os.path.exists(os.path.join(root, "精度失败明细.md")))
