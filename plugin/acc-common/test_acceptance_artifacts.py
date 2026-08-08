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
        "execution_identity": {
            "public_op": "Widget", "kernel_op_type": "InternalWidget",
            "resolution": "derived_exact_source_candidate",
            "source_binding": {
                "schema": "oprunway.kernel_identity_spec_binding",
                "schema_version": 1, "identity_sha256": "a" * 64,
                "candidate": {
                    "kernel_op_type": "InternalWidget",
                    "source_path": "op/op_host/widget_def.cpp",
                    "source_sha256": "b" * 64, "line": 1,
                },
            },
        },
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
        candidate["execution_identity"] = {
            "public_op": "Widget", "kernel_op_type": "InternalWidget"}
        record = A.build_attempt_record(candidate)
        self.assertEqual(record["schema"], "oprunway.workflow_attempt_record")
        self.assertEqual(record["schema_version"], A.ATTEMPT_RECORD_SCHEMA_VERSION)
        self.assertEqual(record["status"], "not_publishable")
        self.assertIsNone(record["acceptance_verdict"])
        self.assertEqual(record["pipeline_state"], "BLOCKED_WAIT_EXTERNAL")
        self.assertEqual(record["execution_identity"], candidate["execution_identity"])
        self.assertNotIn("precision_verdict", record)

    def test_pre_execution_failure_metadata_is_preserved_without_verdict_semantics(self):
        candidate = _candidate("BLOCKED(pre-execution)", "BLOCKED_PRE_EXECUTION", False)
        candidate["pre_execution_failure"] = {
            "stage": "target_closure", "error_code": "OPS_INFO_MISSING",
            "vendor_attempt_sha256": "a" * 64,
        }
        record = A.build_attempt_record(candidate)
        self.assertIs(record["formal_eligible"], False)
        self.assertIsNone(record["acceptance_verdict"])
        self.assertEqual(record["pre_execution_failure"],
                         candidate["pre_execution_failure"])

    def test_generic_attempt_writer_cannot_overwrite_pre_execution_terminal(self):
        candidate = _candidate("BLOCKED(pre-execution)", "BLOCKED_PRE_EXECUTION", False)
        with tempfile.TemporaryDirectory() as out_dir:
            with open(os.path.join(out_dir, A.PRE_EXECUTION_TERMINAL_FILE),
                      "w", encoding="utf-8") as marker:
                marker.write("{}")
            with self.assertRaises(A.ArtifactNameConflictError):
                A.write_attempt_record(out_dir, candidate)

    def test_orphan_pre_execution_payload_blocks_formal_publish(self):
        with tempfile.TemporaryDirectory() as out_dir:
            with open(os.path.join(out_dir, "vendor_build_attempt.json"),
                      "w", encoding="utf-8") as out:
                out.write("{}")
            with self.assertRaisesRegex(A.ArtifactNameConflictError, "incomplete"):
                A.publish_acceptance_json(out_dir, _candidate())
            self.assertFalse(os.path.lexists(os.path.join(out_dir, "acceptance.json")))

    def test_orphan_atomic_temp_blocks_formal_publish(self):
        with tempfile.TemporaryDirectory() as out_dir:
            orphan = os.path.join(
                out_dir, A.ARTIFACT_TEMP_PREFIX + "dead-round.acceptance.json.tmp")
            with open(orphan, "w", encoding="utf-8") as out:
                out.write("partial\n")
            with self.assertRaisesRegex(A.ArtifactNameConflictError, "临时|orphan"):
                A.publish_acceptance_json(out_dir, _candidate())
            self.assertTrue(os.path.isfile(orphan))
            self.assertFalse(os.path.lexists(os.path.join(out_dir, "acceptance.json")))

    def test_legacy_renderer_temp_orphan_blocks_formal_publish(self):
        with tempfile.TemporaryDirectory() as out_dir:
            orphan = os.path.join(out_dir, "验收报告.md.tmp")
            with open(orphan, "w", encoding="utf-8") as out:
                out.write("partial\n")
            with self.assertRaisesRegex(A.ArtifactNameConflictError, "临时|orphan"):
                A.publish_acceptance_json(out_dir, _candidate())
            self.assertTrue(os.path.isfile(orphan))
            self.assertFalse(os.path.lexists(os.path.join(out_dir, "acceptance.json")))

    def test_transaction_pins_directory_fd_and_rejects_root_swap(self):
        with tempfile.TemporaryDirectory() as parent:
            out_dir = os.path.join(parent, "report")
            os.mkdir(out_dir)
            guard = A.artifact_path_guard.prepare_existing_directory(out_dir)
            displaced = out_dir + ".displaced"
            with self.assertRaisesRegex(A.ArtifactNameConflictError, "替换"):
                with A.artifact_transaction(out_dir, guard=guard) as transaction:
                    os.rename(out_dir, displaced)
                    os.mkdir(out_dir)
                    transaction.atomic_write_json("attempt_record.json", {"x": 1})
            self.assertFalse(os.path.lexists(os.path.join(
                out_dir, "attempt_record.json")))

    def test_failed_atomic_replace_cleans_only_current_round_temp(self):
        with tempfile.TemporaryDirectory() as out_dir:
            os.mkdir(os.path.join(out_dir, "acceptance.json"))
            unrelated = os.path.join(out_dir, ".unrelated.tmp")
            with open(unrelated, "w", encoding="utf-8") as out:
                out.write("keep\n")
            guard = A.artifact_path_guard.prepare_existing_directory(out_dir)
            with self.assertRaises(OSError):
                with A.artifact_transaction(
                        out_dir, guard=guard) as transaction:
                    transaction.atomic_write_json("acceptance.json", {"x": 1})
            self.assertFalse(any(
                name.startswith(A.ARTIFACT_TEMP_PREFIX)
                for name in os.listdir(out_dir)))
            self.assertTrue(os.path.isfile(unrelated))

    def test_public_writers_reject_a_symlink_report_root(self):
        with tempfile.TemporaryDirectory() as root:
            real = os.path.join(root, "real")
            os.mkdir(real)
            link = os.path.join(root, "report-link")
            os.symlink(real, link)
            with self.assertRaisesRegex(A.ArtifactNameConflictError, "符号链接"):
                A.publish_acceptance_json(link, _candidate())
            self.assertEqual(os.listdir(real), [])

    def test_formal_publisher_rejects_missing_execution_identity(self):
        with tempfile.TemporaryDirectory() as root:
            candidate = _candidate()
            del candidate["execution_identity"]
            with self.assertRaisesRegex(A.FormalAcceptanceError, "execution_identity"):
                A.publish_acceptance_json(root, candidate)

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
