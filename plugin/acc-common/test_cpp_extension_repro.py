import unittest
from unittest import mock

import cpp_extension_repro as R


class SelectRepresentativesTest(unittest.TestCase):
    def test_groups_by_dtype_and_failed_roles(self):
        caseset = {"cases": [
            {"id": "a", "inputs": [{"dtype": "float16"}]},
            {"id": "b", "inputs": [{"dtype": "float16"}]},
            {"id": "c", "inputs": [{"dtype": "float32"}]},
            {"id": "d", "inputs": [{"dtype": "float32"}]},
        ]}
        evidence = {"evidence": [
            {"case_id": "a", "precision": {"outputs": [
                {"role": "index", "policy": {"kind": "exact"},
                 "metrics": {"mismatch": 1, "numel": 1}}]}},
            {"case_id": "b", "precision": {"outputs": [
                {"role": "index", "policy": {"kind": "exact"},
                 "metrics": {"mismatch": 1, "numel": 1}}]}},
            {"case_id": "c", "precision": {"outputs": [
                {"role": "value", "policy": {"kind": "exact"},
                 "metrics": {"mismatch": 1, "numel": 1}}]}},
            {"case_id": "d", "precision": {"outputs": [
                {"role": "value", "policy": {"kind": "exact"},
                 "metrics": {"mismatch": 1, "numel": 1}},
                {"role": "index", "policy": {"kind": "exact"},
                 "metrics": {"mismatch": 1, "numel": 1}}]}},
        ]}
        verdict = {"per_case": [
            {"case_id": "a", "精度": "fail"},
            {"case_id": "b", "精度": "fail"},
            {"case_id": "c", "精度": "fail"},
            {"case_id": "d", "精度": "fail"},
        ]}
        self.assertEqual(
            R.select_representatives(caseset, evidence, verdict),
            ["a", "c", "d"],
        )

    def test_rejects_unaligned_report(self):
        with self.assertRaisesRegex(R.ReproError, "无法对齐"):
            R.select_representatives(
                {"cases": [{"id": "a", "inputs": [{"dtype": "float16"}]}]},
                {"evidence": []},
                {"per_case": [{"case_id": "a", "精度": "fail"}]},
            )

    @mock.patch.object(R, "_load")
    @mock.patch.object(R, "_resolve_cases", return_value=["a"])
    @mock.patch.object(R, "reproduce", side_effect=RuntimeError("executor is nullptr"))
    def test_main_distinguishes_execution_error_from_precision_failure(
            self, _reproduce, _resolve, load):
        load.side_effect = [
            {"cases": [{"id": "a"}]},
            {"evidence": []},
            {"per_case": [{"case_id": "a", "精度": "fail"}]},
        ]
        self.assertEqual(
            R.main(["--report-root", "/tmp/report", "--case-id", "a"]), 2)
