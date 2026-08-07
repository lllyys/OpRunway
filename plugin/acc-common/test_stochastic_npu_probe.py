import copy
import inspect
import json
import os
import tempfile
import unittest

import stochastic_npu_probe as P


def _contract():
    return {
        "kind": "binary_probability_sampler",
        "bindings": {
            "probability": "probability_value",
            "seed": "generator_seed",
            "offset": "generator_offset",
        },
        "probabilities": {"boundaries": [0.0, 1.0], "interior": [0.5]},
        "rng": {
            "seed": 17,
            "alternate_seed": 18,
            "offset": 0,
            "alternate_offset": 4,
            "offset_alignment": 4,
            "repeat_count": 2,
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
            "witness_profile_id": "statistical_witness",
        },
    }


class DevelopmentProbeTest(unittest.TestCase):
    def test_counts_are_binary_sequence_metrics(self):
        got = P._counts(bytes([0, 1, 1, 0]), bytes([0, 1, 0, 1]))
        self.assertEqual(got["sample_count"], 4)
        self.assertEqual(got["ones_count"], 2)
        self.assertEqual(got["other_ones_count"], 2)
        self.assertEqual(got["joint_ones_count"], 1)
        self.assertEqual(got["hamming_count"], 2)
        self.assertEqual(len(got["sha256"]), 64)

    def test_uncontrolled_callable_is_blocked_before_runtime_import(self):
        contract = _contract()
        contract["oracle_precondition"]["callable"] = "builtins.eval"
        got = P.run(contract)
        self.assertEqual(got["status"], "blocked")
        self.assertEqual(got["evidence_grade"], "development")
        self.assertFalse(got["usable_for_verdict"])
        self.assertIsNone(got["acceptance_verdict"])
        self.assertIn("受控形态", got["reason"])

    def test_bad_contract_cli_writes_structured_block_and_returns_three(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "contract.json")
            output = os.path.join(root, "result.json")
            bad = copy.deepcopy(_contract())
            bad["rng"]["alternate_offset"] = 3
            with open(source, "w", encoding="utf-8") as out:
                json.dump(bad, out)
            rc = P.main([source, "--out", output])
            self.assertEqual(rc, 3)
            with open(output, encoding="utf-8") as src:
                got = json.load(src)
            self.assertEqual(got["status"], "blocked")
            self.assertFalse(got["usable_for_verdict"])
            self.assertIsNone(got["acceptance_verdict"])

    def test_probe_has_no_operator_identity_dispatch(self):
        source = inspect.getsource(P)
        self.assertNotIn("if op ==", source)
        self.assertNotIn("if op in", source)


if __name__ == "__main__":
    unittest.main()
