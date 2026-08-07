import copy
import inspect
import unittest

import stochastic_contract as S


def _contract():
    return {
        "kind": "binary_probability_sampler",
        "bindings": {"probability": "chance", "seed": "rng_seed", "offset": "rng_offset"},
        "probabilities": {"boundaries": [0.0, 1.0], "interior": [0.5]},
        "rng": {
            "seed": 20260807, "alternate_seed": 20260808,
            "offset": 0, "alternate_offset": 4,
            "offset_alignment": 4, "repeat_count": 2,
        },
        "oracle_precondition": {
            "method_kind": "same_device_npu",
            "callable": "torch.Tensor.binary_sample_",
            "predicate": "exact",
        },
        "statistics": {
            "predicate": "hoeffding_two_sided",
            "independence_predicate": "joint_and_hamming_hoeffding",
            "confidence": 0.999,
            "min_samples": 4096,
        },
    }


def _device():
    return {
        "type": "npu", "index": 0, "soc": "witness-soc",
        "runtime_fingerprint": "a" * 64,
    }


def _precondition(contract=None, mismatch=0):
    contract = S.normalize_contract(contract or _contract())
    plan = S.build_case_plan(contract)
    same = "b" * 64
    return {
        "schema": S.PRECONDITION_SCHEMA,
        "schema_version": 1,
        "contract_sha256": S.canonical_sha256(contract),
        "plan_sha256": S.canonical_sha256(plan),
        "status": "passed" if mismatch == 0 else "failed",
        "evidence_grade": "precondition",
        "usable_for_verdict": False,
        "execution": {
            "dut_device": _device(), "reference_device": _device(),
            "reference_method": "same_device_npu",
            "reference_callable": contract["oracle_precondition"]["callable"],
        },
        "case": {
            "probability": 0.5, "seed": 20260807, "offset": 0,
            "sample_count": 4096,
        },
        "comparison": {
            "predicate": "exact", "mismatch_count": mismatch,
            "sample_count": 4096,
            "dut_output_sha256": same,
            "reference_output_sha256": same if mismatch == 0 else "c" * 64,
        },
    }


def _formal(contract=None, receipt=None):
    contract = S.normalize_contract(contract or _contract())
    receipt = S.validate_precondition_receipt(contract, receipt or _precondition(contract))
    plan = S.build_case_plan(contract)
    return {
        "schema": S.FORMAL_SCHEMA,
        "schema_version": 1,
        "contract_sha256": S.canonical_sha256(contract),
        "plan_sha256": S.canonical_sha256(plan),
        "precondition_receipt_sha256": S.canonical_sha256(receipt),
        "device": _device(),
        "boundaries": [
            {"probability": 0.0, "sample_count": 17, "ones_count": 0},
            {"probability": 1.0, "sample_count": 17, "ones_count": 17},
        ],
        "groups": [{
            "probability": 0.5,
            "seed": 20260807,
            "offset": 0,
            "sample_count": 4096,
            "ones_count": 2048,
            "repeat_mismatch_counts": [0],
            "independence": [
                {"changed_role": "seed", "seed": 20260808, "offset": 0,
                 "sample_count": 4096, "ones_count": 2048,
                 "joint_ones_count": 1024, "hamming_count": 2048},
                {"changed_role": "offset", "seed": 20260807, "offset": 4,
                 "sample_count": 4096, "ones_count": 2048,
                 "joint_ones_count": 1024, "hamming_count": 2048},
            ],
        }],
    }


class ContractNormalizationTest(unittest.TestCase):
    def test_contract_is_strict_and_role_driven(self):
        params = [
            {"name": "chance", "io": "attr"},
            {"name": "rng_seed", "io": "attr"},
            {"name": "rng_offset", "io": "attr"},
        ]
        got = S.normalize_contract(_contract(), params=params)
        self.assertEqual(got["bindings"]["probability"], "chance")
        self.assertEqual(got["rng"]["offset_alignment"], 4)

    def test_missing_or_duplicate_role_binding_is_rejected(self):
        for mutate in (
                lambda c: c["bindings"].pop("offset"),
                lambda c: c["bindings"].update(offset="rng_seed")):
            c = _contract()
            mutate(c)
            with self.assertRaises(S.StochasticContractError):
                S.normalize_contract(c)

    def test_probability_boundaries_and_offset_alignment_are_fail_closed(self):
        cases = []
        c = _contract(); c["probabilities"]["boundaries"] = [1.0, 0.0]; cases.append(c)
        c = _contract(); c["probabilities"]["interior"] = [0.0]; cases.append(c)
        c = _contract(); c["rng"]["alternate_offset"] = 3; cases.append(c)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(S.StochasticContractError):
                S.normalize_contract(bad)

    def test_confidence_must_be_strong_enough_to_observe_independence(self):
        c = _contract()
        c["probabilities"]["interior"] = [0.001]
        with self.assertRaisesRegex(S.StochasticContractError, "hamming"):
            S.normalize_contract(c)

    def test_no_operator_identity_dispatch_exists(self):
        source = inspect.getsource(S)
        self.assertNotIn("if op ==", source)
        self.assertNotIn("if op in", source)
        self.assertNotIn("Bernoulli", source)


class PlanTest(unittest.TestCase):
    def test_plan_is_deterministic_and_uses_bound_parameter_names(self):
        first = S.build_case_plan(_contract())
        second = S.build_case_plan(copy.deepcopy(_contract()))
        self.assertEqual(first, second)
        self.assertEqual(first["contract_sha256"], S.canonical_sha256(S.normalize_contract(_contract())))
        roles = [row["role"] for row in first["cases"]]
        self.assertEqual(roles, [
            "boundary_0", "boundary_1", "interior_0_oracle_precondition",
            "interior_0_repeat_0", "interior_0_repeat_1",
            "interior_0_alternate_seed", "interior_0_alternate_offset",
        ])
        values = first["cases"][0]["values"]
        self.assertEqual(set(values), {"chance", "rng_seed", "rng_offset"})
        self.assertNotIn("op", first)


class PreconditionReceiptTest(unittest.TestCase):
    def test_matching_same_device_exact_receipt_is_ready_but_never_verdict_evidence(self):
        got = S.validate_precondition_receipt(_contract(), _precondition())
        self.assertEqual(got["status"], "passed")
        self.assertFalse(got["usable_for_verdict"])
        gate = S.precondition_gate(_contract(), got)
        self.assertEqual(gate["status"], "ready")
        self.assertTrue(gate["ready_for_formal_precision"])
        self.assertFalse(gate["usable_for_verdict"])

    def test_exact_mismatch_is_blocked_for_rootcause_not_formal_failure(self):
        gate = S.precondition_gate(_contract(), _precondition(mismatch=1))
        self.assertEqual(gate["status"], "blocked")
        self.assertFalse(gate["ready_for_formal_precision"])
        self.assertIn("root-cause", gate["reason"])

    def test_device_seed_offset_and_receipt_grade_mutations_are_rejected(self):
        mutations = []
        r = _precondition(); r["execution"]["reference_device"]["index"] = 1; mutations.append(r)
        r = _precondition(); r["case"]["seed"] += 1; mutations.append(r)
        r = _precondition(); r["case"]["offset"] += 4; mutations.append(r)
        r = _precondition(); r["usable_for_verdict"] = True; mutations.append(r)
        r = _precondition(); r["comparison"]["predicate"] = "isclose"; mutations.append(r)
        r = _precondition(mismatch=1)
        r["comparison"]["reference_output_sha256"] = r["comparison"]["dut_output_sha256"]
        mutations.append(r)
        for receipt in mutations:
            with self.subTest(receipt=receipt):
                gate = S.precondition_gate(_contract(), receipt)
                self.assertEqual(gate["status"], "blocked")
                self.assertFalse(gate["ready_for_formal_precision"])


class FormalEvidenceTest(unittest.TestCase):
    def test_balanced_distribution_reproducibility_and_independence_satisfy(self):
        receipt = _precondition()
        result = S.evaluate_formal_evidence(_contract(), receipt, _formal(receipt=receipt))
        self.assertEqual(result["status"], "satisfied")
        self.assertTrue(result["satisfied"])
        self.assertEqual(result["statistical_test_count"], 7)
        self.assertTrue(all(row["satisfied"] for row in result["checks"]))

    def test_failed_precondition_blocks_without_consuming_formal_counts(self):
        result = S.evaluate_formal_evidence(
            _contract(), _precondition(mismatch=3), {"deliberately": "not evidence"})
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["satisfied"])
        self.assertEqual(result["checks"], [])

    def test_boundary_distribution_repeat_and_independence_mutations_fail(self):
        mutations = []
        e = _formal(); e["boundaries"][0]["ones_count"] = 1; mutations.append(e)
        e = _formal(); e["groups"][0]["ones_count"] = 4096; mutations.append(e)
        e = _formal(); e["groups"][0]["repeat_mismatch_counts"] = [1]; mutations.append(e)
        e = _formal(); e["groups"][0]["independence"][0]["joint_ones_count"] = 2048
        e["groups"][0]["independence"][0]["hamming_count"] = 0; mutations.append(e)
        for evidence in mutations:
            with self.subTest(evidence=evidence):
                result = S.evaluate_formal_evidence(_contract(), _precondition(), evidence)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["satisfied"])
                self.assertTrue(any(not row["satisfied"] for row in result["checks"]))

    def test_seed_offset_and_precondition_digest_mutations_are_contract_errors(self):
        mutations = []
        e = _formal(); e["groups"][0]["seed"] += 1; mutations.append(e)
        e = _formal(); e["groups"][0]["independence"][1]["offset"] += 4; mutations.append(e)
        e = _formal(); e["precondition_receipt_sha256"] = "f" * 64; mutations.append(e)
        e = _formal(); e["groups"] = []; mutations.append(e)
        for evidence in mutations:
            with self.subTest(evidence=evidence), self.assertRaises(S.StochasticContractError):
                S.evaluate_formal_evidence(_contract(), _precondition(), evidence)


if __name__ == "__main__":
    unittest.main()
