#!/usr/bin/env python3
"""正式验收产物发布边界的纯契约测试。"""

import json
import os
import tempfile
import unittest

import acceptance_artifacts as A
import run_workflow as W


def _candidate(overall="PASS", state="PASSED", gate_passed=True, perf_status="ok"):
    return {
        "op": "Widget",
        "overall": overall,
        "state": state,
        "exit_code": 0,
        "repo_mode": "cpp_extension",
        "perf_status": perf_status,
        "gate": {"passed": gate_passed, "errors": {}},
    }


class FormalAcceptanceBoundaryTest(unittest.TestCase):

    def test_existing_deterministic_terminal_states_remain_formal(self):
        """产物门只防未完成/阻塞，不把合法 FAIL 或人工 CP 终态洗掉。"""
        pairs = (
            ("PASS", "PASSED"),
            ("PASS(无性能要求)", "PASSED"),
            ("PASS(性能仅实测未裁决)", "PASSED_PRECISION_PERF_MEASURED_ONLY"),
            ("FAIL(精度)", "FAILED_PRECISION"),
            ("性能未达成(failed)", "FAILED_PERFORMANCE"),
            ("PASSED_WITH_RISK", "PASSED_WITH_RISK"),
            ("PASSED_WITH_GAPS", "PASSED_WITH_GAPS"),
        )
        for overall, state in pairs:
            with self.subTest(overall=overall, state=state):
                self.assertTrue(A.formal_acceptance_allowed(_candidate(overall, state)))

    def test_workflow_and_artifact_gate_share_the_same_canonical_state_implementation(self):
        """workflow 不得留第二份映射；renderer 只调用 artifact helper。"""
        self.assertIs(W._canonical_state, A.canonical_state)
        self.assertIs(W._STATE_MAP, A.CANONICAL_STATE_BY_OVERALL)

    def test_gate_and_incomplete_states_fail_closed_without_shadow_enum(self):
        passed_with_gate_errors = _candidate()
        passed_with_gate_errors["gate"]["errors"] = {"task2": ["missing"]}
        passed_without_gate_errors_object = _candidate()
        del passed_without_gate_errors_object["gate"]["errors"]
        candidates = (
            _candidate(gate_passed=False),
            _candidate(gate_passed=1),
            _candidate("BLOCKED(验收门未过)", "BLOCKED_EVIDENCE_INCOMPLETE"),
            _candidate("BLOCKED_FUTURE_REASON", "BLOCKED_FUTURE_REASON"),
            _candidate("NEEDS_REVIEW", "NEEDS_REVIEW"),
            passed_with_gate_errors,
            passed_without_gate_errors_object,
            _candidate("BLOCKED(验收门未过)", "PASSED"),
            _candidate("NEEDS_REVIEW", "PASSED"),
            {"state": "PASSED", "gate": {}},
            None,
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assertFalse(A.formal_acceptance_allowed(candidate))
                with self.assertRaises(A.FormalAcceptanceError):
                    A.assert_formal_acceptance_allowed(candidate)

    def test_contradictory_or_unknown_terminal_pairs_fail_closed(self):
        candidates = (
            _candidate("PASS", "FAILED_PRECISION"),
            _candidate("FAIL(精度)", "PASSED"),
            _candidate("性能未达成(failed)", "FAILED_PRECISION"),
            _candidate("NOT_A_REAL_OVERALL", "PASSED"),
            _candidate("PASS", "NOT_A_REAL_STATE"),
        )
        for candidate in candidates:
            with self.subTest(overall=candidate["overall"], state=candidate["state"]):
                self.assertFalse(A.formal_acceptance_allowed(candidate))
                with self.assertRaises(A.FormalAcceptanceError):
                    A.assert_formal_acceptance_allowed(candidate)

    def test_attempt_record_has_no_acceptance_verdict(self):
        candidate = _candidate("BLOCKED_WAIT_EXTERNAL", "BLOCKED_WAIT_EXTERNAL")
        record = A.build_attempt_record(candidate)
        self.assertEqual(record["schema"], "oprunway.workflow_attempt_record")
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["status"], "not_publishable")
        self.assertIsNone(record["acceptance_verdict"])
        self.assertEqual(record["pipeline_state"], "BLOCKED_WAIT_EXTERNAL")
        self.assertNotIn("precision_verdict", record)

    def test_each_writer_rejects_the_other_kind_of_candidate(self):
        with tempfile.TemporaryDirectory() as formal_root, \
                tempfile.TemporaryDirectory() as attempt_root:
            formal = _candidate("FAIL(精度)", "FAILED_PRECISION")
            A.publish_acceptance_json(formal_root, formal)
            self.assertTrue(os.path.isfile(os.path.join(formal_root, "acceptance.json")))
            with self.assertRaises(ValueError):
                A.write_attempt_record(attempt_root, formal)

            blocked = _candidate(
                "BLOCKED(验收门未过)", "BLOCKED_EVIDENCE_INCOMPLETE", False)
            with self.assertRaises(A.FormalAcceptanceError):
                A.publish_acceptance_json(attempt_root, blocked)
            path = A.write_attempt_record(attempt_root, blocked)
            with open(path, encoding="utf-8") as src:
                self.assertIsNone(json.load(src)["acceptance_verdict"])

    def test_writers_fail_closed_instead_of_leaving_both_summary_names(self):
        """公共 writer 直接复用时也必须维护 formal/attempt 总结名互斥。"""
        formal = _candidate("FAIL(精度)", "FAILED_PRECISION")
        blocked = _candidate(
            "BLOCKED(验收门未过)", "BLOCKED_EVIDENCE_INCOMPLETE", False)

        with tempfile.TemporaryDirectory() as root:
            A.publish_acceptance_json(root, formal)
            with self.assertRaisesRegex(RuntimeError, r"互斥"):
                A.write_attempt_record(root, blocked)
            self.assertTrue(os.path.isfile(os.path.join(root, "acceptance.json")))
            self.assertFalse(os.path.lexists(os.path.join(root, "attempt_record.json")))

        with tempfile.TemporaryDirectory() as root:
            A.write_attempt_record(root, blocked)
            with self.assertRaisesRegex(RuntimeError, r"互斥"):
                A.publish_acceptance_json(root, formal)
            self.assertTrue(os.path.isfile(os.path.join(root, "attempt_record.json")))
            self.assertFalse(os.path.lexists(os.path.join(root, "acceptance.json")))


if __name__ == "__main__":
    unittest.main()
