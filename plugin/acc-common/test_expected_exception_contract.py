import copy
import json
import os
import tempfile
import unittest

import expected_exception_contract as E
import cpp_extension_adapter
import repo_adapter
import validate_acceptance_state
import validator


def _contract():
    return {
        "schema": E.SCHEMA,
        "schema_version": E.VERSION,
        "reference": {
            "class": "ZeroDivisionError",
            "phase": "golden",
            "message": "integer division or modulo by zero",
        },
        "expected": {
            "class": "ZeroDivisionError",
            "phase": "execute",
            "message_policy": {
                "kind": "exact",
                "value": "integer division or modulo by zero",
            },
        },
    }


class ExpectedExceptionContractTest(unittest.TestCase):
    def test_capture_and_match_exact(self):
        try:
            1 // 0
        except Exception as ex:
            got = E.from_golden_exception(ex)
        self.assertEqual(got, _contract())
        observed = E.observed_exception(
            error_class="ZeroDivisionError", phase="execute",
            message="integer division or modulo by zero")
        self.assertEqual(E.compare(_contract(), observed), (True, "expected_exception_matched"))

    def test_success_or_different_exception_does_not_match(self):
        self.assertEqual(E.compare(_contract(), None)[0], False)
        observed = E.observed_exception(
            error_class="RuntimeError", phase="execute",
            message="integer division or modulo by zero")
        self.assertEqual(E.compare(_contract(), observed)[0], False)

    def test_strict_schema_and_message_policy(self):
        for mutate in (
            lambda x: x.update(extra=True),
            lambda x: x["expected"]["message_policy"].update(kind="contains"),
            lambda x: x["reference"].update(phase="execute"),
            lambda x: x["expected"].update(phase="golden"),
        ):
            bad = copy.deepcopy(_contract())
            mutate(bad)
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    E.normalize_contract(bad)


class ValidatorExpectedExceptionTest(unittest.TestCase):
    def test_expected_exception_match_is_functional_pass_precision_na(self):
        row = validator._empty_row("zero")
        observed = E.observed_exception(
            error_class="ZeroDivisionError", phase="execute",
            message="integer division or modulo by zero")
        validator._judge_expected_exception(row, _contract(), observed)
        self.assertEqual((row["功能"], row["精度"]), ("pass", "na"))

    def test_expected_exception_success_or_mismatch_is_fail(self):
        for observed in (None, E.observed_exception(
                error_class="RuntimeError", phase="execute",
                message="integer division or modulo by zero")):
            row = validator._empty_row("zero")
            validator._judge_expected_exception(row, _contract(), observed)
            self.assertEqual(row["功能"], "fail")


class AdapterExpectedExceptionTest(unittest.TestCase):
    def test_ledger_binds_contract_and_rejects_policy_mutation(self):
        caseset = {"cases": [{"id": "zero", "dims": ["功能"],
                              "expected": {"compare": "na", "standard": "na",
                                           "golden_path": None,
                                           "expected_exception": _contract()}}]}
        ledger = cpp_extension_adapter.validate_caseset_expected_exceptions(caseset)
        self.assertEqual(ledger["case_count"], 1)
        bad = copy.deepcopy(caseset)
        bad["cases"][0]["expected"]["expected_exception"]["expected"][
            "message_policy"]["kind"] = "contains"
        with self.assertRaises(cpp_extension_adapter.CppExtensionAdapterError):
            cpp_extension_adapter.validate_caseset_expected_exceptions(bad)

    def test_failed_manifest_becomes_structured_observation(self):
        caseset = {"cases": [{"id": "zero", "dims": ["功能"],
                              "expected": {"compare": "na", "standard": "na",
                                           "golden_path": None,
                                           "expected_exception": _contract()}}]}
        with tempfile.TemporaryDirectory() as work:
            out = os.path.join(work, "cpp_extension_out")
            os.makedirs(out)
            with open(os.path.join(out, "out_manifest.json"), "w", encoding="utf-8") as fh:
                json.dump({"produced": [], "failed": [{
                    "case_id": "zero", "phase": "execute",
                    "error_kind": "execution_failed", "error_type": "ZeroDivisionError",
                    "error": "integer division or modulo by zero"}]}, fh)
            rows = repo_adapter.build_multi_output_evidence(caseset, work, out)
        self.assertEqual(rows[0]["status"], "expected_exception")
        self.assertEqual(rows[0]["exception"]["phase"], "execute")


class StrictEmptyAdapterTest(unittest.TestCase):
    def test_compare_na_skips_policy_but_keeps_shape_bytes_and_write_receipt(self):
        import numpy as np
        case = {"id": "empty", "inputs": [{"name": "x", "dtype": "float32",
                                               "shape": [0, 3]}],
                "expected": {"compare": "na", "golden_path": "empty/golden.npy",
                             "out_shape": [0, 3]}}
        with tempfile.TemporaryDirectory() as work:
            cdir = os.path.join(work, "empty")
            out = os.path.join(work, "cpp_extension_out")
            odir = os.path.join(out, "empty")
            os.makedirs(cdir); os.makedirs(odir)
            np.save(os.path.join(cdir, "golden.npy"), np.empty((0, 3), dtype=np.float32))
            open(os.path.join(odir, "out_0.bin"), "wb").close()
            diag = {"status": "skipped_empty", "numel": 0, "dtype": "float32",
                    "output_name": "y", "output_index": 0}
            with open(os.path.join(out, "out_manifest.json"), "w", encoding="utf-8") as fh:
                json.dump({"produced": [{"case_id": "empty", "outputs": [{
                    "index": 0, "path": "empty/out_0.bin", "dtype": "float32",
                    "shape": [0, 3], "output_written_check": "skipped_empty",
                    "output_written_diagnostic": diag}]}], "failed": []}, fh)
            rows = repo_adapter.build_multi_output_evidence(
                {"cases": [case]}, work, out)
        self.assertEqual(rows[0]["status"], "skipped_empty")
        self.assertNotIn("policy", rows[0]["precision"])
        self.assertEqual(rows[0]["precision"]["provenance"]["numel"], 0)
        self.assertEqual(rows[0]["output_written_check"], "skipped_empty")


class FormalGateExpectedExceptionTest(unittest.TestCase):
    def test_gate_recomputes_match_and_rejects_forged_verdict(self):
        cases = [{"id": "zero", "expected": {"expected_exception": _contract()}}]
        evidence = [{"case_id": "zero", "status": "expected_exception",
                     "exception": E.observed_exception(
                         error_class="ZeroDivisionError", phase="execute",
                         message="integer division or modulo by zero")}]
        verdict = {"per_case": [{"case_id": "zero", "功能": "pass", "精度": "na"}]}
        errors = []
        self.assertEqual(validate_acceptance_state._gate_expected_exceptions(
            cases, evidence, verdict, errors), {"zero"})
        self.assertEqual(errors, [])
        forged = copy.deepcopy(verdict)
        forged["per_case"][0]["功能"] = "fail"
        errors = []
        validate_acceptance_state._gate_expected_exceptions(
            cases, evidence, forged, errors)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
