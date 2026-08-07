import copy
import os
import tempfile
import unittest
from unittest import mock

import gen_cases as G
import stochastic_contract as S


def _source(kind):
    return {"state": "resolved", "sources": [{"kind": kind, "cite": "fixture:1"}]}


def _spec():
    output_relation = {
        "rule": "follows", "operand": "self", "provenance": _source("taskbook")}
    return {
        "op": "CapabilityWitness",
        "runner_form": "cpp_extension",
        "verify_mode": "numerical",
        "operator_class": "floating_compute",
        "params_source": "fixture",
        "params": [
            {"name": "self", "io": "in", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["float32"]},
            {"name": "probability_value", "io": "attr", "kind": "scalar",
             "binding": "host_scalar", "dtype": ["float32"]},
            {"name": "generator_seed", "io": "attr", "dtype": ["int64"], "default": 17},
            {"name": "generator_offset", "io": "attr", "dtype": ["int64"], "default": 0},
            {"name": "out", "io": "out", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["float32"],
             "dtype_relation": copy.deepcopy(output_relation)},
        ],
        "precision": {
            "oracle": "ascendoptest", "standard": "ascendoptest_default",
            "case_source": "generated", "reference_case_material_role": "reference_only",
            "case_target": 7,
        },
        "call_variants": [{
            "when": {"always": True}, "symbol": "CapabilityWitness",
            "active_attrs": ["probability_value", "generator_seed", "generator_offset"],
            "active_outputs": ["out"],
        }],
        "multi_input_contract": {
            "schema_version": "multi_input.v1",
            "output": {
                "name": "out", "kind": "tensor", "binding": "device_tensor", "format": "nd",
                "shape": {"rule": "follows", "operand": "self",
                          "provenance": _source("taskbook")},
                "dtype": copy.deepcopy(output_relation),
            },
            "profiles": [{
                "profile_id": "statistical_witness",
                "inputs": [
                    {"name": "self", "kind": "tensor", "binding": "device_tensor",
                     "shape": [4096], "dtype": "float32", "format": "nd"},
                    {"name": "probability_value", "kind": "scalar",
                     "binding": "host_scalar", "dtype": "float32", "value": 0.5},
                ],
            }],
            "required_coverage": {"host_scalar": 1},
        },
        "stochastic": {
            "kind": "binary_probability_sampler",
            "bindings": {
                "probability": "probability_value",
                "seed": "generator_seed",
                "offset": "generator_offset",
            },
            "probabilities": {"boundaries": [0.0, 1.0], "interior": [0.5]},
            "rng": {
                "seed": 17, "alternate_seed": 18,
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
                "witness_profile_id": "statistical_witness",
            },
        },
    }


class StochasticGenerationTest(unittest.TestCase):
    def test_bool_input_generation_is_deterministic_and_covers_both_values(self):
        first = G._make_varied(G.np.random.default_rng(7), (32,), "bool")
        second = G._make_varied(G.np.random.default_rng(7), (32,), "bool")
        self.assertEqual(first.dtype, G.np.dtype("bool"))
        self.assertTrue(G.np.array_equal(first, second))
        self.assertEqual({False, True}, set(first.tolist()))

    def test_multi_profile_structural_coverage_is_boundary_exact_not_statistical_pooling(self):
        spec = _spec()
        profiles = spec["multi_input_contract"]["profiles"]
        for pid, shape in (("rank0", []), ("rank2", [2, 3])):
            profile = copy.deepcopy(profiles[0])
            profile["profile_id"] = pid
            profile["inputs"][0]["shape"] = shape
            profiles.append(profile)
        spec["precision"]["case_target"] = 9
        with tempfile.TemporaryDirectory() as work, mock.patch.object(
                G, "load_golden", side_effect=AssertionError("不得加载 golden")):
            caseset = G.gen_cases(spec, work)
        ledger = caseset["stochastic_ledger"]
        self.assertEqual(len(ledger["coverage_roles"]), 2)
        self.assertEqual(len(caseset["cases"]), 9)
        coverage = [c for c in caseset["cases"]
                    if c["stochastic"]["purpose"] == "structural_boundary_exact"]
        self.assertEqual([c["stochastic"]["probability"] for c in coverage], [0.0, 0.0])
        self.assertEqual([c["stochastic"]["sample_count"] for c in coverage], [1, 6])

    def test_role_plan_is_materialized_without_pointwise_golden(self):
        spec = _spec()
        with tempfile.TemporaryDirectory() as work, mock.patch.object(
                G, "load_golden", side_effect=AssertionError("stochastic 不得加载 golden")):
            caseset = G.gen_cases(spec, work)
            dry = G._build_dry_run_ledger(spec)
        plan = S.build_case_plan(spec["stochastic"])
        self.assertEqual(len(caseset["cases"]), len(plan["cases"]))
        self.assertEqual(
            caseset["stochastic_ledger"]["roles"],
            [row["role"] for row in plan["cases"]])
        self.assertEqual(
            caseset["stochastic_ledger"], dry["coverage"]["stochastic_ledger"])
        self.assertIn("stochastic_contract.py", dry["planner_binding"]["logic_files"])
        by_role = {case["stochastic"]["role"]: case for case in caseset["cases"]}
        boundary = by_role["boundary_0"]
        self.assertEqual(boundary["attrs"]["generator_seed"], 17)
        self.assertEqual(boundary["attrs"]["generator_offset"], 0)
        self.assertEqual(boundary["attrs"]["probability_value"], 0.0)
        self.assertEqual(boundary["parameter_contract"]["inputs"][1]["value"], 0.0)
        self.assertEqual(boundary["expected"]["compare"], "stochastic")
        self.assertIsNone(boundary["expected"]["golden_path"])
        self.assertFalse(os.path.exists(os.path.join(work, boundary["id"], "golden.npy")))

    def test_profile_role_and_contract_mutations_fail_closed(self):
        mutations = []
        bad = _spec(); bad["stochastic"]["statistics"]["witness_profile_id"] = "missing"
        mutations.append(bad)
        bad = _spec(); bad["multi_input_contract"]["profiles"][0]["inputs"][0]["shape"] = [1024]
        mutations.append(bad)
        bad = _spec(); bad["precision"]["case_target"] = 6; mutations.append(bad)
        bad = _spec(); bad["stochastic"]["bindings"]["seed"] = "not_a_param"; mutations.append(bad)
        for spec in mutations:
            with self.subTest(spec=spec), tempfile.TemporaryDirectory() as work, \
                    self.assertRaises(ValueError), mock.patch.object(
                        G, "load_golden", side_effect=AssertionError("不得加载")):
                G.gen_cases(spec, work)


if __name__ == "__main__":
    unittest.main()
