import copy
import unittest

import precision_policy as P


def _relation():
    return {
        "rule": "promote",
        "rule_id": "torch_tensor_tensor_v1",
        "operands": ["left", "right"],
        "provenance": {
            "state": "resolved",
            "sources": [
                {"kind": "taskbook", "cite": "task.md:27"},
                {"kind": "op_def", "cite": "op_def.cpp:42"},
            ],
        },
    }


def _spec(with_relation=True):
    out = {"name": "out", "io": "out", "dtype": ["float16", "float32", "int64"]}
    if with_relation:
        out["dtype_relation"] = _relation()
    return {
        "params": [
            {"name": "left", "io": "in", "dtype": ["int32", "float16", "float32"]},
            {"name": "right", "io": "in", "dtype": ["float16", "float32", "int64"]},
            out,
        ]
    }


class MultiInputPrecisionPolicyTest(unittest.TestCase):
    def test_explicit_promote_relation_derives_mixed_input_output(self):
        self.assertEqual(P.derive_output_dtype(
            _spec(), [("left", "int32"), ("right", "float16")]), "float16")

    def test_legacy_spec_still_rejects_mixed_input_dtype(self):
        with self.assertRaisesRegex(ValueError, "多输入 dtype 不一致"):
            P.derive_output_dtype(
                _spec(with_relation=False),
                [("left", "int32"), ("right", "float16")])

    def test_relation_missing_source_or_operand_fails_closed(self):
        for mutation in ("source", "operand"):
            spec = _spec()
            relation = spec["params"][-1]["dtype_relation"]
            if mutation == "source":
                relation["provenance"]["sources"].pop()
            else:
                relation["operands"][1] = "missing"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                P.derive_output_dtype(
                    spec, [("left", "int32"), ("right", "float16")])

    def test_derived_dtype_must_be_declared_by_output_parameter(self):
        spec = copy.deepcopy(_spec())
        spec["params"][-1]["dtype"] = ["float32"]
        with self.assertRaisesRegex(ValueError, "输出参数允许集"):
            P.derive_output_dtype(
                spec, [("left", "int32"), ("right", "float16")])

    def test_host_scalar_relation_follows_tensor_without_entering_promote(self):
        relation = {
            "rule": "follows", "operand": "self",
            "provenance": {
                "state": "resolved",
                "sources": [{"kind": "taskbook", "cite": "task.md:31"}],
            },
        }
        spec = {
            "params": [
                {"name": "self", "io": "in", "dtype": ["float16"]},
                {"name": "prob", "io": "attr", "kind": "scalar",
                 "binding": "host_scalar", "dtype": ["float16", "float32"]},
                {"name": "out", "io": "out", "dtype": ["float16"],
                 "dtype_relation": relation},
            ],
        }
        self.assertEqual(
            P.derive_output_dtype(spec, [("self", "float16")]), "float16")


if __name__ == "__main__":
    unittest.main()
