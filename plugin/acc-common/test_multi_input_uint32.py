#!/usr/bin/env python3
"""uint32 作为通用 tensor dtype 进入 multi-input follows 关系的回归钉。"""

import copy
import unittest

import multi_input_contract as M


def _provenance(kind="taskbook"):
    return {
        "state": "resolved",
        "sources": [{"kind": kind, "cite": "fixture:uint32"}],
    }


def _spec(dtype="uint32"):
    relation = {
        "rule": "follows", "operand": "x", "provenance": _provenance(),
    }
    return {
        "params": [
            {"name": "x", "io": "in", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["uint32"]},
            {"name": "out", "io": "out", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["uint32"],
             "dtype_relation": copy.deepcopy(relation)},
        ],
        "multi_input_contract": {
            "schema_version": "multi_input.v1",
            "output": {
                "name": "out", "kind": "tensor", "binding": "device_tensor",
                "format": "nd",
                "shape": {"rule": "follows", "operand": "x",
                          "provenance": _provenance()},
                "dtype": copy.deepcopy(relation),
            },
            "profiles": [{
                "profile_id": "uint32_rank0",
                "inputs": [{
                    "name": "x", "kind": "tensor", "binding": "device_tensor",
                    "shape": [], "dtype": dtype, "format": "nd",
                }],
            }],
            "required_coverage": {"rank0_tensor": 1},
        },
    }


class MultiInputUint32Test(unittest.TestCase):
    def test_uint32_rank0_follows_is_resolved_without_operator_identity(self):
        resolved = M.resolve_spec_contract(_spec())
        profile = resolved["profiles"][0]
        self.assertEqual(profile["inputs"][0]["dtype"], "uint32")
        self.assertEqual(profile["output"]["dtype"], "uint32")
        self.assertEqual(profile["output"]["shape"], [])
        self.assertEqual(resolved["coverage"]["rank0_tensor"], 1)

    def test_unknown_unsigned_width_still_fails_closed(self):
        with self.assertRaisesRegex(M.MultiInputContractError, "非受控 dtype"):
            M.resolve_spec_contract(_spec("uint16"))


if __name__ == "__main__":
    unittest.main()
