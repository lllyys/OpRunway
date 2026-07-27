import json
import os
import tempfile
import unittest

import render_acceptance_markdown as R


class RenderAcceptanceMarkdownTest(unittest.TestCase):
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
            self.assertIn("./repro/show_case.sh b", text)
            self.assertIn("./repro/review.sh show 1", text)
            self.assertIn("性能未执行", text)
