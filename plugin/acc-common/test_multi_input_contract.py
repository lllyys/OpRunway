import copy
import unittest

import multi_input_contract as M


def _tensor(name, shape, dtype="float32", tensor_format="nd"):
    return {
        "name": name,
        "kind": "tensor",
        "binding": "device_tensor",
        "shape": list(shape),
        "dtype": dtype,
        "format": tensor_format,
    }


def _profile(left_shape=(2, 1, 4), right_shape=(1, 3, 1),
             left_dtype="float16", right_dtype="float32"):
    return {
        "profile_id": "broadcast_rank_mismatch",
        "inputs": [
            _tensor("left", left_shape, left_dtype),
            _tensor("right", right_shape, right_dtype),
        ],
        "output": {
            "name": "result",
            "kind": "tensor",
            "binding": "device_tensor",
            "format": "nd",
            "shape": {
                "rule": "broadcast",
                "operands": ["left", "right"],
            },
            "dtype": {
                "rule": "promote",
                "rule_id": "torch_tensor_tensor_v1",
                "operands": ["left", "right"],
            },
        },
    }


_EXPECTED_PARAMS = [
    {"name": "left", "kind": "tensor", "binding": "device_tensor"},
    {"name": "right", "kind": "tensor", "binding": "device_tensor"},
]


def _provenance(*kinds):
    return {
        "state": "resolved",
        "sources": [{"kind": kind, "cite": f"fixture:{kind}"} for kind in kinds],
    }


def _contract(profiles=None, required=None):
    profiles = profiles or [{
        "profile_id": "broadcast",
        "inputs": _profile()["inputs"],
    }]
    return {
        "schema_version": "multi_input.v1",
        "output": {
            "name": "result", "kind": "tensor", "binding": "device_tensor",
            "format": "nd",
            "shape": {
                "rule": "broadcast", "operands": ["left", "right"],
                "provenance": _provenance("taskbook", "op_def"),
            },
            "dtype": {
                "rule": "promote", "rule_id": "torch_tensor_tensor_v1",
                "operands": ["left", "right"],
                "provenance": _provenance("taskbook", "op_def"),
            },
        },
        "profiles": profiles,
        "required_coverage": required or {"broadcasted": 1, "mixed_dtype": 1},
    }


class BroadcastShapeTest(unittest.TestCase):
    def test_legal_broadcast_with_independent_shapes(self):
        resolved = M.resolve_profile(_profile(), _EXPECTED_PARAMS)
        self.assertEqual(resolved["output"]["shape"], [2, 3, 4])
        self.assertEqual(
            [item["shape"] for item in resolved["inputs"]],
            [[2, 1, 4], [1, 3, 1]],
        )
        self.assertTrue(resolved["relations"]["shape"]["broadcasted"])

    def test_rank_mismatch_broadcast(self):
        resolved = M.resolve_profile(
            _profile(left_shape=(2, 3, 4), right_shape=(4,)),
            _EXPECTED_PARAMS,
        )
        self.assertEqual(resolved["output"]["shape"], [2, 3, 4])
        self.assertTrue(resolved["relations"]["shape"]["rank_mismatch"])

    def test_rank_zero_tensor_is_not_rewritten_to_rank_one(self):
        resolved = M.resolve_profile(
            _profile(left_shape=(), right_shape=(2, 3)),
            _EXPECTED_PARAMS,
        )
        self.assertEqual(resolved["inputs"][0]["shape"], [])
        self.assertEqual(resolved["output"]["shape"], [2, 3])
        self.assertTrue(resolved["relations"]["shape"]["rank0_tensor"])

    def test_incompatible_broadcast_fails_closed(self):
        bad = _profile(left_shape=(2, 3), right_shape=(4,))
        with self.assertRaisesRegex(M.MultiInputContractError, "不可广播"):
            M.resolve_profile(bad, _EXPECTED_PARAMS)

    def test_bool_and_negative_dimensions_are_not_valid_shapes(self):
        for bad_shape in ([True, 3], [-1, 3], [2.0, 3]):
            with self.subTest(shape=bad_shape):
                bad = _profile()
                bad["inputs"][0]["shape"] = bad_shape
                with self.assertRaises(M.MultiInputContractError):
                    M.resolve_profile(bad, _EXPECTED_PARAMS)


class DtypeAndBindingTest(unittest.TestCase):
    def test_each_input_keeps_its_own_dtype_and_format(self):
        profile = _profile(left_dtype="bfloat16", right_dtype="float16")
        profile["inputs"][1]["format"] = "torch_npu_rank_default"
        resolved = M.resolve_profile(profile, _EXPECTED_PARAMS)
        self.assertEqual(
            [(x["dtype"], x["format"]) for x in resolved["inputs"]],
            [("bfloat16", "nd"), ("float16", "torch_npu_rank_default")],
        )
        self.assertEqual(resolved["output"]["dtype"], "float32")

    def test_torch_tensor_promotion_witnesses(self):
        witnesses = {
            ("int32", "int64"): "int64",
            ("float16", "float32"): "float32",
            ("int32", "float16"): "float16",
            ("bfloat16", "float16"): "float32",
            ("float32", "float64"): "float64",
        }
        for pair, expected in witnesses.items():
            with self.subTest(pair=pair):
                self.assertEqual(
                    M.promote_dtypes("torch_tensor_tensor_v1", pair), expected)

    def test_host_scalar_uses_the_same_parameter_identity_contract(self):
        profile = {
            "profile_id": "host_scalar",
            "inputs": [
                _tensor("self", (2, 3), "float32"),
                {"name": "prob", "kind": "scalar", "binding": "host_scalar",
                 "dtype": "float32", "value": 0.5},
            ],
            "output": {
                "name": "out", "kind": "tensor", "binding": "device_tensor",
                "format": "nd",
                "shape": {"rule": "follows", "operand": "self"},
                "dtype": {"rule": "follows", "operand": "self"},
            },
        }
        expected = [
            {"name": "self", "kind": "tensor", "binding": "device_tensor"},
            {"name": "prob", "kind": "scalar", "binding": "host_scalar"},
        ]
        resolved = M.resolve_profile(profile, expected)
        self.assertEqual(resolved["inputs"][1]["value"], 0.5)
        self.assertEqual(resolved["output"]["shape"], [2, 3])
        self.assertEqual(resolved["output"]["dtype"], "float32")

    def test_host_scalar_dtype_does_not_claim_mixed_tensor_dtype_coverage(self):
        profile = {
            "profile_id": "host_scalar",
            "inputs": [
                _tensor("self", (2, 3), "float16"),
                {"name": "prob", "kind": "scalar", "binding": "host_scalar",
                 "dtype": "float32", "value": 0.5},
            ],
            "output": {
                "name": "out", "kind": "tensor", "binding": "device_tensor", "format": "nd",
                "shape": {"rule": "follows", "operand": "self"},
                "dtype": {"rule": "follows", "operand": "self"},
            },
        }
        bundle = M.resolve_profiles([profile], [
            {"name": "self", "kind": "tensor", "binding": "device_tensor"},
            {"name": "prob", "kind": "scalar", "binding": "host_scalar"},
        ])
        self.assertEqual(bundle["coverage"]["host_scalar"], 1)
        self.assertEqual(bundle["coverage"]["mixed_dtype"], 0)

    def test_missing_or_wrong_binding_fails_before_execution(self):
        for mutation in ("missing", "wrong"):
            with self.subTest(mutation=mutation):
                bad = _profile()
                if mutation == "missing":
                    del bad["inputs"][1]["binding"]
                else:
                    bad["inputs"][1]["binding"] = "host_scalar"
                with self.assertRaisesRegex(M.MultiInputContractError, "binding"):
                    M.resolve_profile(bad, _EXPECTED_PARAMS)

    def test_illegal_promotion_reference_and_rule_fail_closed(self):
        for rule_id, operands in (
            ("made_up", ["left", "right"]),
            ("torch_tensor_tensor_v1", ["left", "missing"]),
        ):
            with self.subTest(rule_id=rule_id, operands=operands):
                bad = _profile()
                bad["output"]["dtype"]["rule_id"] = rule_id
                bad["output"]["dtype"]["operands"] = operands
                with self.assertRaises(M.MultiInputContractError):
                    M.resolve_profile(bad, _EXPECTED_PARAMS)

    def test_tensor_promotion_rejects_host_scalar_semantics(self):
        bad = _profile()
        bad["inputs"][1] = {
            "name": "right", "kind": "scalar", "binding": "host_scalar",
            "dtype": "float32", "value": 1.0,
        }
        with self.assertRaisesRegex(M.MultiInputContractError, "tensor"):
            M.resolve_profile(bad, [
                _EXPECTED_PARAMS[0],
                {"name": "right", "kind": "scalar", "binding": "host_scalar"},
            ])


class IdentityLedgerAndMutationTest(unittest.TestCase):
    def test_parameter_identity_is_ordered_and_exact(self):
        for expected in (
            list(reversed(_EXPECTED_PARAMS)),
            _EXPECTED_PARAMS[:1],
            _EXPECTED_PARAMS + [
                {"name": "extra", "kind": "tensor", "binding": "device_tensor"}
            ],
        ):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(M.MultiInputContractError, "参数身份"):
                    M.resolve_profile(_profile(), expected)

    def test_profile_set_ledger_counts_structural_axes(self):
        rank_mismatch = _profile(left_shape=(2, 3, 4), right_shape=(4,))
        rank_zero = _profile(left_shape=(), right_shape=(2, 3))
        rank_zero["profile_id"] = "rank0"
        same = _profile(left_shape=(2, 3), right_shape=(2, 3),
                        left_dtype="float32", right_dtype="float32")
        same["profile_id"] = "same"
        bundle = M.resolve_profiles(
            [rank_mismatch, rank_zero, same], _EXPECTED_PARAMS)
        self.assertEqual(bundle["coverage"], {
            "profiles": 3,
            "broadcasted": 2,
            "rank_mismatch": 2,
            "rank0_tensor": 1,
            "host_scalar": 0,
            "mixed_dtype": 2,
            "mixed_format": 0,
        })
        self.assertEqual(len(bundle["sha256"]), 64)
        self.assertEqual(bundle, M.resolve_profiles(
            [rank_mismatch, rank_zero, same], _EXPECTED_PARAMS))

    def test_mutation_changes_digest_or_is_rejected(self):
        original = M.resolve_profiles([_profile()], _EXPECTED_PARAMS)
        dtype_mutation = _profile()
        dtype_mutation["inputs"][1]["dtype"] = "float64"
        changed = M.resolve_profiles([dtype_mutation], _EXPECTED_PARAMS)
        self.assertNotEqual(original["sha256"], changed["sha256"])

        shape_mutation = copy.deepcopy(_profile())
        shape_mutation["inputs"][1]["shape"] = [5, 3]
        with self.assertRaisesRegex(M.MultiInputContractError, "不可广播"):
            M.resolve_profiles([shape_mutation], _EXPECTED_PARAMS)


class SpecContractTest(unittest.TestCase):
    def test_common_output_relation_expands_profiles_and_enforces_coverage(self):
        bundle = M.resolve_contract(_contract(), _EXPECTED_PARAMS)
        self.assertEqual(bundle["coverage"]["broadcasted"], 1)
        self.assertEqual(bundle["coverage"]["mixed_dtype"], 1)
        self.assertEqual(bundle["profiles"][0]["output"]["shape"], [2, 3, 4])
        self.assertEqual(bundle["profiles"][0]["output"]["dtype"], "float32")

    def test_missing_required_pattern_fails_closed(self):
        same = {
            "profile_id": "same",
            "inputs": _profile(left_shape=(2, 3), right_shape=(2, 3),
                               left_dtype="float32", right_dtype="float32")["inputs"],
        }
        with self.assertRaisesRegex(M.MultiInputContractError, "必需覆盖未满足"):
            M.resolve_contract(
                _contract([same], {"broadcasted": 1, "mixed_dtype": 1}),
                _EXPECTED_PARAMS)

    def test_promote_requires_taskbook_and_opdef_resolved_sources(self):
        for mutation in ("missing_opdef", "conflict"):
            contract = _contract()
            provenance = contract["output"]["dtype"]["provenance"]
            if mutation == "missing_opdef":
                provenance["sources"] = provenance["sources"][:1]
            else:
                provenance["state"] = "conflict"
            with self.subTest(mutation=mutation), self.assertRaises(
                    M.MultiInputContractError):
                M.resolve_contract(contract, _EXPECTED_PARAMS)

    def test_standalone_promote_derivation_reuses_the_same_source_gate(self):
        relation = _contract()["output"]["dtype"]
        self.assertEqual(M.derive_promoted_output_dtype(
            relation, [("left", "int32"), ("right", "float16")]), "float16")
        del relation["provenance"]
        with self.assertRaisesRegex(M.MultiInputContractError, "provenance"):
            M.derive_promoted_output_dtype(
                relation, [("left", "int32"), ("right", "float16")])


if __name__ == "__main__":
    unittest.main()
