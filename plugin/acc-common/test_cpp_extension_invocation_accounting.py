#!/usr/bin/env python3
"""cpp_extension receipt.invocation：plan 分母、逐 case 摘要与 evidence 闭环。"""

import copy
import unittest

import cann_version
import cpp_extension_adapter as A
import validate_acceptance_state as G


def _plan():
    return {
        "schema": "oprunway.cpp_extension_invocation_plan",
        "schema_version": 1,
        "cases": [
            {"case_id": "produced", "entrypoint": "invoke_v0", "slots": []},
            {"case_id": "failed", "entrypoint": "invoke_v0", "slots": []},
        ],
        "excluded": [{
            "case_id": "no-golden", "reason": A.GOLDEN_UNAVAILABLE,
            "contract_bindings_sha256": "1" * 64,
        }],
    }


def _evidence():
    return [
        {"case_id": "produced", "status": "ok"},
        {"case_id": "failed", "status": "execution_failed"},
        {"case_id": "no-golden", "status": "golden_unavailable"},
    ]


class InvocationAccountingContractTest(unittest.TestCase):
    def _receipt(self):
        return A.build_invocation_accounting(
            _plan(), produced_case_ids=["produced"], failed_case_ids=["failed"])

    def test_plan_cases_and_excluded_are_bound_once_with_row_digest(self):
        value = self._receipt()
        self.assertEqual(
            (value["total"], value["planned"], value["produced"],
             value["failed"], value["excluded"]),
            (3, 2, 1, 1, 1),
        )
        self.assertEqual(
            [(row["case_id"], row["partition"], row["outcome"])
             for row in value["case_records"]],
            [("produced", "cases", "produced"),
             ("failed", "cases", "failed"),
             ("no-golden", "excluded", "excluded")],
        )
        plan_rows = _plan()["cases"] + _plan()["excluded"]
        self.assertEqual(
            [row["plan_row_sha256"] for row in value["case_records"]],
            [A._canonical_sha(row) for row in plan_rows],
        )
        A.validate_invocation_accounting(_plan(), value, evidence=_evidence())

    def test_count_identity_digest_duplicate_and_omission_mutations_fail(self):
        mutations = []
        for key in ("total", "planned", "produced", "failed", "excluded"):
            bad = self._receipt()
            bad[key] += 1
            mutations.append((key, bad))
        bad = self._receipt()
        bad["case_records"].pop()
        mutations.append(("omitted", bad))
        bad = self._receipt()
        bad["case_records"][1] = copy.deepcopy(bad["case_records"][0])
        mutations.append(("duplicated", bad))
        bad = self._receipt()
        bad["case_records"][0]["case_id"] = "other"
        mutations.append(("identity", bad))
        bad = self._receipt()
        bad["case_records"][0]["plan_row_sha256"] = "0" * 64
        mutations.append(("digest", bad))
        bad = self._receipt()
        bad["case_records"][0]["outcome"] = "failed"
        mutations.append(("outcome", bad))
        bad = self._receipt()
        bad["failed_case_ids"] = ["produced"]
        mutations.append(("failed_ids", bad))
        bad = self._receipt()
        bad["case_records"][2]["reason"] = "other"
        mutations.append(("excluded_reason", bad))
        for label, value in mutations:
            with self.subTest(label=label), self.assertRaises(
                    A.CppExtensionAdapterError):
                A.validate_invocation_accounting(_plan(), value)

    def test_evidence_must_match_all_three_outcomes_without_duplicates(self):
        value = self._receipt()
        mutations = []
        bad = _evidence()[:-1]
        mutations.append(("missing", bad))
        bad = _evidence() + [copy.deepcopy(_evidence()[0])]
        mutations.append(("duplicate", bad))
        bad = _evidence()
        bad[0]["status"] = "execution_failed"
        mutations.append(("wrong_outcome", bad))
        bad = _evidence()
        bad[2]["status"] = "ok"
        mutations.append(("excluded_as_ok", bad))
        for label, evidence in mutations:
            with self.subTest(label=label), self.assertRaises(
                    A.CppExtensionAdapterError):
                A.validate_invocation_accounting(
                    _plan(), value, evidence=evidence)


class InvocationAccountingAcceptanceGateTest(unittest.TestCase):
    def test_current_receipt_is_replayed_against_plan_and_evidence(self):
        invocation = A.build_invocation_accounting(
            _plan(), produced_case_ids=["produced"], failed_case_ids=["failed"])
        receipt = {
            "schema_version": cann_version.RECEIPT_SCHEMA_VERSION,
            "invocation": invocation,
        }
        errors = []
        G._gate_cpp_extension_invocation_accounting(
            _plan(), receipt, _evidence(), errors)
        self.assertEqual(errors, [])

        for mutation in ("receipt", "evidence"):
            bad_receipt = copy.deepcopy(receipt)
            bad_evidence = _evidence()
            if mutation == "receipt":
                bad_receipt["invocation"]["case_records"][0][
                    "plan_row_sha256"] = "0" * 64
            else:
                bad_evidence[1]["status"] = "ok"
            errors = []
            G._gate_cpp_extension_invocation_accounting(
                _plan(), bad_receipt, bad_evidence, errors)
            self.assertTrue(errors, mutation)

    def test_historical_v1_receipt_does_not_invent_invocation_fields(self):
        errors = []
        G._gate_cpp_extension_invocation_accounting(
            _plan(), {"schema_version": 1}, _evidence(), errors)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
