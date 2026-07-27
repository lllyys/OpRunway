#!/usr/bin/env python3
"""cpp_extension adapter 的纯确定性契约测试；不 build、不加载 torch/NPU。"""

import copy
import json
import os
import tempfile
import unittest

import cpp_extension_adapter as A
import cpp_extension_codegen as C


def _spec():
    return {
        "op": "Witness",
        "runner_form": "cpp_extension",
        "params": [
            {"name": "self", "io": "in", "dtype": ["float32"]},
            {"name": "dim", "io": "attr", "dtype": ["int64"], "default": 0},
            {"name": "valuesOut", "io": "out", "dtype": ["<from_input>"]},
            {"name": "indicesOut", "io": "out", "dtype": ["int32"]},
        ],
        "call_variants": [
            {"symbol": "Witness", "active_attrs": [],
             "active_outputs": ["valuesOut"]},
            {"symbol": "WitnessDim", "active_attrs": ["dim"],
             "active_outputs": ["valuesOut", "indicesOut"]},
        ],
    }


def _caseset():
    return {
        "op": "Witness",
        "cases": [
            {
                "id": "c0",
                "aclnn_call": {
                    "symbol": "Witness",
                    "slots": [
                        {"role": "in", "name": "self", "input_idx": 0},
                        {"role": "out", "name": "valuesOut", "output_idx": 0},
                        {"role": "out_null", "name": "indicesOut"},
                    ],
                },
            },
            {
                "id": "c1",
                "aclnn_call": {
                    "symbol": "WitnessDim",
                    "slots": [
                        {"role": "in", "name": "self", "input_idx": 0},
                        {"role": "attr", "name": "dim", "ctype": "int64", "value": 0},
                        {"role": "out", "name": "valuesOut", "output_idx": 0},
                        {"role": "out", "name": "indicesOut", "output_idx": 1},
                    ],
                },
            },
        ],
    }


class CppExtensionAdapterContractTest(unittest.TestCase):
    def test_plan_binds_case_symbols_without_operator_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = C.generate(_spec(), td)
        plan = A.build_invocation_plan(_caseset(), manifest)
        self.assertEqual(
            [row["entrypoint"] for row in plan["cases"]],
            ["invoke_v0", "invoke_v1"])
        self.assertEqual(plan["namespace"], manifest["namespace"])

    def test_unknown_symbol_fails_closed(self):
        bad = copy.deepcopy(_caseset())
        bad["cases"][0]["aclnn_call"]["symbol"] = "Other"
        with tempfile.TemporaryDirectory() as td:
            manifest = C.generate(_spec(), td)
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "未绑定"):
            A.build_invocation_plan(bad, manifest)

    def test_output_arity_mismatch_fails_closed(self):
        bad = copy.deepcopy(_caseset())
        bad["cases"][1]["aclnn_call"]["slots"][-1]["role"] = "out_null"
        with tempfile.TemporaryDirectory() as td:
            manifest = C.generate(_spec(), td)
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "active outputs"):
            A.build_invocation_plan(bad, manifest)

    def test_prepare_requires_distinct_form(self):
        bad = _spec()
        bad["runner_form"] = "aclnn_py"
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(A.CppExtensionAdapterError, "cpp_extension"):
                A.prepare(bad, _caseset(), td)

    def test_prepare_snapshots_exact_caseset_for_remote_driver(self):
        with tempfile.TemporaryDirectory() as td:
            A.prepare(_spec(), _caseset(), td)
            path = os.path.join(td, "cpp_extension_caseset.json")
            with open(path, encoding="utf-8") as src:
                snapshot = json.load(src)
        self.assertEqual(snapshot, _caseset())

    def test_real_mode_fails_before_driver_without_explicit_gate(self):
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "真机路径未启用"):
            A.run_cpp_extension(_caseset(), "/tmp/not-used")


if __name__ == "__main__":
    unittest.main()
