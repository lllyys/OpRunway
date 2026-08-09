import copy
import json
import os
import tempfile
import unittest
from unittest import mock

import expected_exception_contract as E
import cpp_extension_adapter
import cpp_extension_codegen
import cpp_extension_driver
import gen_cases
import repo_adapter
import validate_acceptance_state
import validator
from test_gen_cases_multi_input import (
    _binary_golden,
    _binary_spec,
    _golden,
    _host_scalar_spec,
)


def _marker(*categories):
    return {
        "schema": E.MARKER_SCHEMA,
        "schema_version": E.MARKER_VERSION,
        "reference": {
            "class": "ZeroDivisionError",
            "phase": "golden",
            "message": "integer division or modulo by zero",
        },
        "expected": {
            "phase": "execute",
            "return_categories": list(categories or ("stage1_nonzero",)),
            "output_written": False,
        },
    }


def _contract(*categories):
    return E.contract_from_marker(_marker(*categories))


def _case(*categories):
    return {
        "id": "zero",
        "dims": ["功能"],
        "inputs": [{"name": "x", "dtype": "int32", "shape": [1],
                    "path": "zero/x.bin"}],
        "expected": {
            "compare": "na", "standard": "na", "golden_path": None,
            "expected_exception": _contract(*categories),
        },
    }


def _call_status(*, stage1=7, executor_null=False,
                 stage2_called=False, stage2_ret=None):
    return {
        "schema": "oprunway.cpp_extension_call_status", "schema_version": 1,
        "stage1_ret": stage1, "workspace_size": 0,
        "executor_null": executor_null, "stage2_called": stage2_called,
        "stage2_ret": stage2_ret,
    }


def _isolation(call_status):
    return {
        "schema": cpp_extension_adapter.EXECUTION_ISOLATION_SCHEMA,
        "schema_version": 1,
        "mode": "subprocess_per_case_v1",
        "records": [{
            "case_id": "zero", "launch_id": "fresh-zero",
            "isolation_mode": "subprocess_per_case_v1",
            "termination_kind": "normal", "returncode": 0,
            "parent_pid": 101, "child_pid": 101, "outcome": "failed",
            "call_status": call_status,
        }],
    }


def _evidence(case, isolation, *, error_type="ForgedError", error="forged text"):
    with tempfile.TemporaryDirectory() as work:
        out = os.path.join(work, "cpp_extension_out")
        os.makedirs(out)
        with open(os.path.join(out, "out_manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"produced": [], "failed": [{
                "case_id": "zero", "phase": "execute",
                "error_kind": "execution_failed",
                "error_type": error_type, "error": error,
            }]}, fh)
        rows = repo_adapter.build_multi_output_evidence(
            {"cases": [case]}, work, out)
    cpp_extension_adapter.validate_execution_isolation(
        {"cases": [{"case_id": "zero"}]}, isolation)
    cpp_extension_adapter.bind_execution_isolation_evidence(rows, isolation)
    return rows


class ExpectedExceptionContractTest(unittest.TestCase):
    def test_explicit_marker_authorizes_and_content_binds_contract(self):
        contract = _contract("stage1_nonzero")
        self.assertEqual(contract["authorization"]["kind"],
                         "golden_explicit_marker_v1")
        self.assertEqual(len(contract["authorization"]["marker_sha256"]), 64)
        observed = E.observed_from_call_status(
            _call_status(), output_written=False)
        self.assertEqual(E.compare(contract, observed),
                         (True, "expected_exception_matched"))

    def test_success_or_different_return_category_does_not_match(self):
        self.assertEqual(E.compare(_contract(), None)[0], False)
        observed = E.observed_from_call_status(
            _call_status(stage1=0, stage2_called=True, stage2_ret=9),
            output_written=False)
        self.assertEqual(E.compare(_contract("stage1_nonzero"), observed)[0], False)

    def test_marker_schema_and_return_category_are_strict(self):
        for mutate in (
            lambda x: x.update(extra=True),
            lambda x: x["expected"].update(return_categories=[]),
            lambda x: x["expected"].update(return_categories=["failed_text_match"]),
            lambda x: x["expected"].update(return_categories=["stage2_nonzero"]),
            lambda x: x["expected"].update(output_written=True),
            lambda x: x["reference"].update(phase="execute"),
        ):
            bad = copy.deepcopy(_marker())
            mutate(bad)
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    E.contract_from_marker(bad)


class ValidatorExpectedExceptionTest(unittest.TestCase):
    def test_call_status_match_is_functional_pass_precision_na(self):
        row = validator._empty_row("zero")
        observed = E.observed_from_call_status(_call_status(), output_written=False)
        validator._judge_expected_exception(row, _contract(), observed)
        self.assertEqual((row["功能"], row["精度"]), ("pass", "na"))

    def test_failed_text_cannot_forge_a_different_call_status_category(self):
        case = _case("executor_null")
        rows = _evidence(
            case, _isolation(_call_status()),
            error_type="ZeroDivisionError",
            error="integer division or modulo by zero",
        )
        row = validator._empty_row("zero")
        validator._judge_expected_exception(
            row, case["expected"]["expected_exception"], rows[0]["exception"])
        self.assertEqual(row["功能"], "fail")
        self.assertEqual(rows[0]["exception"]["return_category"], "stage1_nonzero")


class AdapterExpectedExceptionTest(unittest.TestCase):
    def test_ledger_binds_contract_and_rejects_policy_mutation(self):
        caseset = {"cases": [_case()]}
        ledger = cpp_extension_adapter.validate_caseset_expected_exceptions(caseset)
        self.assertEqual(ledger["case_count"], 1)
        bad = copy.deepcopy(caseset)
        bad["cases"][0]["expected"]["expected_exception"]["expected"][
            "return_categories"] = ["failed_text_match"]
        with self.assertRaises(cpp_extension_adapter.CppExtensionAdapterError):
            cpp_extension_adapter.validate_caseset_expected_exceptions(bad)

    def test_invocation_plan_content_binds_expected_exception_ledger(self):
        spec = {
            "op": "Witness", "runner_form": "cpp_extension",
            "params": [
                {"name": "x", "io": "in", "dtype": ["int32"]},
                {"name": "out", "io": "out", "dtype": ["<from_input>"]},
            ],
            "call_variants": [{
                "symbol": "Witness", "active_attrs": [],
                "active_outputs": ["out"],
            }],
        }
        case = _case()
        case["aclnn_call"] = {
            "symbol": "Witness",
            "slots": [
                {"role": "in", "name": "x", "input_idx": 0},
                {"role": "out", "name": "out", "output_idx": 0},
            ],
        }
        caseset = {"op": "Witness", "cases": [case]}
        ledger = cpp_extension_adapter.validate_caseset_expected_exceptions(caseset)
        with tempfile.TemporaryDirectory() as root:
            manifest = cpp_extension_codegen.generate(spec, root)
        plan = cpp_extension_adapter.build_invocation_plan(caseset, manifest)
        self.assertEqual(
            plan["expected_exception_ledger_sha256"],
            cpp_extension_adapter._canonical_sha(ledger),
        )

    def test_per_case_subprocess_observation_ignores_failed_text(self):
        case = _case("stage1_nonzero")
        rows = _evidence(case, _isolation(_call_status()))
        self.assertEqual(rows[0]["status"], "expected_exception")
        self.assertEqual(rows[0]["exception"]["return_category"], "stage1_nonzero")
        self.assertEqual(rows[0]["execution_isolation_mode"],
                         "subprocess_per_case_v1")
        self.assertNotIn("ForgedError", json.dumps(rows[0]["exception"]))


class FormalGateExpectedExceptionTest(unittest.TestCase):
    def test_gate_recomputes_match_and_rejects_forged_verdict(self):
        cases = [_case()]
        evidence = _evidence(cases[0], _isolation(_call_status()))
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


class GeneratedMultiInputExpectedExceptionTest(unittest.TestCase):
    """压住 multi-input + golden context + expected marker 的真实组合接缝。"""

    def test_parameter_identity_order_survives_expected_exception_generation(self):
        spec = _binary_spec()
        spec["multi_input_contract"]["profiles"] = [
            spec["multi_input_contract"]["profiles"][0]]
        spec["multi_input_contract"].pop("required_coverage")
        spec["precision"]["case_target"] = 1
        invocation = {
            "schema": "oprunway.golden_invocation",
            "schema_version": 1,
            "mode": "keyword_case_context_v1",
        }

        def golden_fn(inputs, attrs, *, case_context):
            self.assertEqual(
                [row["name"] for row in case_context["inputs"]],
                ["left", "right"],
            )
            return _marker("stage1_nonzero", "executor_null")

        golden = gen_cases.Golden(
            golden_fn,
            "torch fixture",
            "multi-input expected-exception regression",
            lambda in_shapes, attrs: (2, 3, 4),
            {
                "source": "single_api",
                "method_kind": "torch_cpu",
                "authorization": {"kind": "impl_reference"},
                "invocation": invocation,
            },
        )
        with tempfile.TemporaryDirectory() as work, mock.patch.object(
                gen_cases, "load_golden", return_value=golden), mock.patch.dict(
                    os.environ, {"OPRUNWAY_OPS_DIR": os.path.join(work, "ops")}):
            caseset = gen_cases.gen_cases(spec, work)

        self.assertEqual(len(caseset["cases"]), 1)
        case = caseset["cases"][0]
        self.assertEqual([item["name"] for item in case["inputs"]], ["left", "right"])
        self.assertIsNotNone(case["expected"]["expected_exception"])

    def _marker_fixture(self, work):
        spec = _binary_spec()
        spec["multi_input_contract"]["profiles"] = [
            spec["multi_input_contract"]["profiles"][0]]
        spec["multi_input_contract"].pop("required_coverage")
        spec["precision"]["case_target"] = 1
        golden = gen_cases.Golden(
            lambda inputs, attrs, *, case_context: _marker(),
            "torch fixture", "derived binding fixture",
            lambda in_shapes, attrs: (2, 3, 4),
            {"source": "single_api", "method_kind": "torch_cpu",
             "authorization": {"kind": "impl_reference"},
             "invocation": {"schema": "oprunway.golden_invocation",
                            "schema_version": 1,
                            "mode": "keyword_case_context_v1"}},
        )
        with mock.patch.object(gen_cases, "load_golden", return_value=golden), \
                mock.patch.dict(os.environ, {
                    "OPRUNWAY_OPS_DIR": os.path.join(work, "ops")}):
            caseset = gen_cases.gen_cases(spec, work)
        manifest = cpp_extension_codegen.generate(
            spec, os.path.join(work, "cpp_extension"))
        return spec, caseset, manifest

    def _explicit_fixture(self, work):
        spec = _binary_spec()
        with mock.patch.object(
                gen_cases, "load_golden", return_value=_golden(_binary_golden)):
            caseset = gen_cases.gen_cases(spec, work)
        manifest = cpp_extension_codegen.generate(
            spec, os.path.join(work, "cpp_extension"))
        return spec, caseset, manifest

    def _host_scalar_marker_fixture(self, work):
        spec = _host_scalar_spec()
        golden = gen_cases.Golden(
            lambda inputs, attrs, *, case_context: inputs[0] * attrs["prob"],
            "torch fixture", "host scalar authority fixture",
            lambda in_shapes, attrs: (2, 3),
            {"source": "single_api", "method_kind": "torch_cpu",
             "authorization": {"kind": "impl_reference"},
             "invocation": {"schema": "oprunway.golden_invocation",
                            "schema_version": 1,
                            "mode": "keyword_case_context_v1"}},
        )
        with mock.patch.object(gen_cases, "load_golden", return_value=golden), \
                mock.patch.dict(os.environ, {
                    "OPRUNWAY_OPS_DIR": os.path.join(work, "ops")}):
            caseset = gen_cases.gen_cases(spec, work)
        manifest = cpp_extension_codegen.generate(
            spec, os.path.join(work, "cpp_extension"))
        return spec, caseset, manifest

    def test_slot_order_is_bound_independently_of_parameter_contract_digest(self):
        with tempfile.TemporaryDirectory() as work:
            _spec, caseset, manifest = self._explicit_fixture(work)
            original = cpp_extension_adapter.build_invocation_plan(caseset, manifest)
            changed = copy.deepcopy(caseset)
            changed["cases"][0]["aclnn_call"]["slots"].reverse()
            with self.assertRaises(cpp_extension_adapter.CppExtensionAdapterError):
                cpp_extension_adapter.build_invocation_plan(changed, manifest)
        self.assertEqual(
            len(original["cases"][0]["multi_input_case_binding_sha256"]), 64)

    def test_missing_contract_still_checks_output_static_identity(self):
        with tempfile.TemporaryDirectory() as work:
            _spec, caseset, manifest = self._marker_fixture(work)
            slot = next(row for row in caseset["cases"][0]["aclnn_call"]["slots"]
                        if row["role"] == "out")
            slot.update({"kind": "tensor", "binding": "device_tensor",
                         "format": "torch_npu_rank_default"})
            with self.assertRaisesRegex(
                    cpp_extension_adapter.CppExtensionAdapterError,
                    "output.*manifest|manifest.*output"):
                cpp_extension_adapter.build_invocation_plan(caseset, manifest)

    def test_evidence_binding_tamper_is_rejected_against_frozen_plan(self):
        with tempfile.TemporaryDirectory() as work:
            _spec, caseset, manifest = self._marker_fixture(work)
            plan = cpp_extension_adapter.build_invocation_plan(caseset, manifest)
            evidence = [{"case_id": caseset["cases"][0]["id"]}]
            receipt = {"multi_input_receipt": manifest["multi_input_receipt"]}
            cpp_extension_adapter._bind_multi_input_evidence(
                caseset, evidence, receipt)
            del evidence[0]["multi_input_case_binding_sha256"]
            with self.assertRaises(cpp_extension_adapter.CppExtensionAdapterError):
                cpp_extension_adapter.validate_multi_input_evidence_bindings(
                    caseset, plan, evidence, receipt)

    def test_plan_top_level_multi_input_contract_digest_is_checked(self):
        with tempfile.TemporaryDirectory() as work:
            _spec, caseset, manifest = self._marker_fixture(work)
            plan = cpp_extension_adapter.build_invocation_plan(caseset, manifest)
            evidence = [{"case_id": caseset["cases"][0]["id"]}]
            receipt = {"multi_input_receipt": manifest["multi_input_receipt"]}
            cpp_extension_adapter._bind_multi_input_evidence(
                caseset, evidence, receipt)
            plan["multi_input_contract_sha256"] = "0" * 64
            with self.assertRaisesRegex(
                    cpp_extension_adapter.CppExtensionAdapterError,
                    "plan.*contract|contract.*plan"):
                cpp_extension_adapter.validate_multi_input_evidence_bindings(
                    caseset, plan, evidence, receipt)

    def test_formal_gate_accepts_marker_case_without_repeated_contract(self):
        with tempfile.TemporaryDirectory() as work:
            spec, caseset, _manifest = self._marker_fixture(work)
        errors = []
        validate_acceptance_state._gate_golden_invocation_spec_authority(
            caseset, spec, errors)
        self.assertEqual(errors, [])

    def test_formal_gate_rejects_absent_contract_host_scalar_value_drift(self):
        with tempfile.TemporaryDirectory() as work:
            spec, caseset, _manifest = self._host_scalar_marker_fixture(work)
        case = caseset["cases"][0]
        case.pop("parameter_contract")
        self.assertNotIn("parameter_contract", case)
        case["attrs"]["prob"] = 0.75
        scalar = next(row for row in case["aclnn_call"]["slots"]
                      if row["role"] == "attr" and row["name"] == "prob")
        scalar["value"] = 0.75
        errors = []
        validate_acceptance_state._gate_golden_invocation_spec_authority(
            caseset, spec, errors)
        self.assertTrue(any("scalar" in item or "profile" in item
                            for item in errors), errors)

    def test_explicit_contract_rejects_extra_profile_input(self):
        with tempfile.TemporaryDirectory() as work:
            _spec, caseset, manifest = self._explicit_fixture(work)
            bad = copy.deepcopy(caseset)
            bad["cases"][0]["parameter_contract"]["inputs"].append({
                "name": "forged", "kind": "scalar", "binding": "host_scalar",
                "dtype": "float32", "value": 1.0,
            })
            with self.assertRaises(cpp_extension_adapter.CppExtensionAdapterError):
                cpp_extension_adapter.build_invocation_plan(bad, manifest)


if __name__ == "__main__":
    unittest.main()
