#!/usr/bin/env python3
"""N6：多输入 profile 从 spec 到 caseset/aclnn_call 的确定性闭环。"""

import copy
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

import gen_cases as GC
import multi_input_contract as MIC


def _provenance(*kinds):
    return {
        "state": "resolved",
        "sources": [
            {"kind": kind, "cite": f"fixture:{kind}"} for kind in kinds
        ],
    }


def _tensor(name, shape, dtype, tensor_format):
    return {
        "name": name,
        "kind": "tensor",
        "binding": "device_tensor",
        "shape": list(shape),
        "dtype": dtype,
        "format": tensor_format,
    }


def _binary_spec():
    dtype_relation = {
        "rule": "promote",
        "rule_id": "torch_tensor_tensor_v1",
        "operands": ["left", "right"],
        "provenance": _provenance("taskbook", "op_def"),
    }
    output = {
        "name": "out",
        "kind": "tensor",
        "binding": "device_tensor",
        "format": "nd",
        "shape": {
            "rule": "broadcast",
            "operands": ["left", "right"],
            "provenance": _provenance("taskbook", "op_def"),
        },
        "dtype": dtype_relation,
    }
    profiles = [
        {
            "profile_id": "broadcast",
            "inputs": [
                _tensor("left", (2, 1, 4), "float16", "nd"),
                _tensor("right", (1, 3, 1), "float32", "torch_npu_rank_default"),
            ],
        },
        {
            "profile_id": "rank_mismatch",
            "inputs": [
                _tensor("left", (2, 3, 4), "float32", "nd"),
                _tensor("right", (4,), "float32", "torch_npu_rank_default"),
            ],
        },
        {
            "profile_id": "rank0_tensor",
            "inputs": [
                _tensor("left", (), "float16", "nd"),
                _tensor("right", (2, 3), "float16", "torch_npu_rank_default"),
            ],
        },
    ]
    return {
        "op": "WitnessBinary",
        "runner_form": "cpp_extension",
        "verify_mode": "numerical",
        "operator_class": "floating_compute",
        "params_source": "fixture",
        "params": [
            {"name": "left", "io": "in", "kind": "tensor",
             "binding": "device_tensor", "format": "nd",
             "dtype": ["float16", "float32"]},
            {"name": "right", "io": "in", "kind": "tensor",
             "binding": "device_tensor", "format": "torch_npu_rank_default",
             "dtype": ["float16", "float32"]},
            {"name": "out", "io": "out", "kind": "tensor",
             "binding": "device_tensor", "format": "nd",
             "dtype": ["float16", "float32"],
             "dtype_relation": copy.deepcopy(dtype_relation)},
        ],
        "precision": {
            "oracle": "ascendoptest",
            "standard": "ascendoptest_default",
            "case_source": "generated",
            "reference_case_material_role": "reference_only",
            "case_target": len(profiles),
        },
        "call_variants": [{
            "when": {"always": True},
            "symbol": "WitnessBinary",
            "active_attrs": [],
            "active_outputs": ["out"],
        }],
        "multi_input_contract": {
            "schema_version": "multi_input.v1",
            "output": output,
            "profiles": profiles,
            "required_coverage": {
                "broadcasted": 3,
                "rank_mismatch": 2,
                "rank0_tensor": 1,
                "mixed_dtype": 1,
                "mixed_format": 3,
            },
        },
    }


def _host_scalar_spec():
    dtype_relation = {
        "rule": "follows",
        "operand": "self",
        "provenance": _provenance("taskbook"),
    }
    output = {
        "name": "out",
        "kind": "tensor",
        "binding": "device_tensor",
        "format": "nd",
        "shape": {
            "rule": "follows",
            "operand": "self",
            "provenance": _provenance("taskbook"),
        },
        "dtype": dtype_relation,
    }
    return {
        "op": "WitnessTensorScalar",
        "runner_form": "cpp_extension",
        "verify_mode": "numerical",
        "params_source": "fixture",
        "params": [
            {"name": "self", "io": "in", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["float16"]},
            {"name": "prob", "io": "attr", "kind": "scalar",
             "binding": "host_scalar", "dtype": ["float16", "float32"]},
            {"name": "seed", "io": "attr", "dtype": ["int64"], "default": 7},
            {"name": "offset", "io": "attr", "dtype": ["int64"], "default": 0},
            {"name": "out", "io": "out", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["float16"],
             "dtype_relation": copy.deepcopy(dtype_relation)},
        ],
        "precision": {
            "oracle": "ascendoptest",
            "standard": "ascendoptest_default",
            "case_source": "generated",
            "reference_case_material_role": "reference_only",
            "case_target": 1,
        },
        "call_variants": [{
            "when": {"always": True},
            "symbol": "WitnessTensorScalar",
            "active_attrs": ["prob", "seed", "offset"],
            "active_outputs": ["out"],
        }],
        "multi_input_contract": {
            "schema_version": "multi_input.v1",
            "output": output,
            "profiles": [{
                "profile_id": "host_scalar",
                "inputs": [
                    _tensor("self", (2, 3), "float16", "nd"),
                    {"name": "prob", "kind": "scalar", "binding": "host_scalar",
                     "dtype": "float16", "value": 0.5},
                ],
            }],
            "required_coverage": {"host_scalar": 1, "mixed_dtype": 0},
        },
    }


def _binary_golden(inputs, _attrs):
    promoted = MIC.promote_dtypes(
        "torch_tensor_tensor_v1",
        [str(np.asarray(value).dtype) for value in inputs],
    )
    return np.add(inputs[0], inputs[1]).astype(GC._compute_np(promoted))


def _host_scalar_golden(inputs, attrs):
    return np.multiply(inputs[0], attrs["prob"])


def _golden(fn):
    return GC.Golden(fn, "fixture stdlib/numpy witness", "fixture", None, None)


class MultiInputCasesetTest(unittest.TestCase):
    def test_independent_inputs_survive_generation_and_dry_run(self):
        spec = _binary_spec()
        with tempfile.TemporaryDirectory() as work:
            with mock.patch.object(
                    GC, "load_golden", return_value=_golden(_binary_golden)):
                caseset = GC.gen_cases(spec, work)
            with mock.patch.object(
                    GC, "load_golden", side_effect=ValueError("缺 golden: multi-input fixture")):
                dry = GC._build_dry_run_ledger(spec)

            self.assertEqual(len(caseset["cases"]), 3)
            self.assertEqual(caseset["dtype_tested"], ["float16", "float32"])
            self.assertEqual(caseset["multi_input_ledger"]["case_coverage"], {
                "cases": 3,
                "broadcasted": 3,
                "rank_mismatch": 2,
                "rank0_tensor": 1,
                "host_scalar": 0,
                "mixed_dtype": 1,
                "mixed_format": 3,
            })
            by_id = {case["parameter_contract"]["profile_id"]: case
                     for case in caseset["cases"]}
            rank0 = by_id["rank0_tensor"]
            self.assertEqual([item["shape"] for item in rank0["inputs"]], [[], [2, 3]])
            rank0_payload = np.load(
                os.path.join(work, rank0["inputs"][0]["path"]),
                allow_pickle=False,
            )
            self.assertEqual(
                rank0_payload.shape, (),
                "rank0 metadata=[] 时 .npy 实体不得被 ascontiguousarray 升成 (1,)",
            )
            self.assertEqual(rank0["expected"]["out_shape"], [2, 3])
            broadcast = by_id["broadcast"]
            self.assertEqual(
                [(item["shape"], item["dtype"], item["format"])
                 for item in broadcast["inputs"]],
                [([2, 1, 4], "float16", "nd"),
                 ([1, 3, 1], "float32", "torch_npu_rank_default")],
            )
            self.assertEqual(broadcast["expected"]["compare_dtype"], "float32")
            self.assertEqual(
                [(slot["role"], slot["name"], slot.get("shape"), slot.get("dtype"),
                  slot.get("format")) for slot in broadcast["aclnn_call"]["slots"]],
                [("in", "left", [2, 1, 4], "float16", "nd"),
                 ("in", "right", [1, 3, 1], "float32", "torch_npu_rank_default"),
                 ("out", "out", [2, 3, 4], "float32", "nd")],
            )
            self.assertIn("multi_input_contract.py", dry["planner_binding"]["logic_files"])
            self.assertIsNone(dry["planning"]["input_ranks"])
            self.assertEqual(len(dry["planning"]["input_rank_profiles"]), 3)
            self.assertEqual(
                dry["coverage"]["multi_input_ledger"],
                caseset["multi_input_ledger"],
            )
            GC._render_dry_run_ledger(dry)

    def test_host_scalar_is_not_materialized_as_a_device_tensor(self):
        spec = _host_scalar_spec()
        with tempfile.TemporaryDirectory() as work, mock.patch.object(
                GC, "load_golden", return_value=_golden(_host_scalar_golden)):
            caseset = GC.gen_cases(spec, work)
        case = caseset["cases"][0]
        self.assertEqual(len(case["inputs"]), 1)
        self.assertEqual(case["attrs"]["prob"], 0.5)
        self.assertEqual(case["expected"]["compare_dtype"], "float16")
        slots = {slot["name"]: slot for slot in case["aclnn_call"]["slots"]}
        self.assertEqual(
            {key: slots["prob"][key] for key in ("role", "ctype", "kind", "binding", "dtype", "value")},
            {"role": "attr", "ctype": "scalar", "kind": "scalar",
             "binding": "host_scalar", "dtype": "float16", "value": 0.5},
        )
        self.assertEqual(caseset["multi_input_ledger"]["profile_coverage"]["mixed_dtype"], 0)

    def test_mutations_fail_closed(self):
        mutations = {}
        bad_constraint = _binary_spec()
        bad_constraint["multi_input_contract"]["profiles"][0]["inputs"][1][
            "value_constraints"] = {"nonzero": False}
        mutations["nonzero 只能为 true"] = bad_constraint
        bad_target = _binary_spec()
        bad_target["precision"]["case_target"] += 1
        mutations["完整矩阵"] = bad_target

        bad_broadcast = _binary_spec()
        bad_broadcast["multi_input_contract"]["profiles"][0]["inputs"][1]["shape"] = [5, 3]
        mutations["不可广播"] = bad_broadcast

        bad_binding = _binary_spec()
        del bad_binding["multi_input_contract"]["profiles"][0]["inputs"][1]["binding"]
        mutations["binding"] = bad_binding

        bad_relation = _binary_spec()
        bad_relation["params"][-1]["dtype_relation"]["operands"] = ["right", "left"]
        mutations["不得各持一份"] = bad_relation

        for message, spec in mutations.items():
            with self.subTest(message=message), tempfile.TemporaryDirectory() as parent:
                work = os.path.join(parent, "must-not-pass")
                with mock.patch.object(GC, "load_golden", return_value=_golden(_binary_golden)):
                    with self.assertRaisesRegex(ValueError, message):
                        GC.gen_cases(spec, work)

    def test_generation_is_byte_deterministic_for_each_input(self):
        spec = _binary_spec()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second, \
                mock.patch.object(GC, "load_golden", return_value=_golden(_binary_golden)):
            a = GC.gen_cases(spec, first)
            b = GC.gen_cases(copy.deepcopy(spec), second)
            self.assertEqual(
                [{k: v for k, v in case.items() if k != "inputs"} for case in a["cases"]],
                [{k: v for k, v in case.items() if k != "inputs"} for case in b["cases"]],
            )
            for left, right in zip(a["cases"], b["cases"]):
                for left_item, right_item in zip(left["inputs"], right["inputs"]):
                    with open(os.path.join(first, left_item["path"]), "rb") as fh:
                        left_bytes = fh.read()
                    with open(os.path.join(second, right_item["path"]), "rb") as fh:
                        right_bytes = fh.read()
                    self.assertEqual(left_bytes, right_bytes)


if __name__ == "__main__":
    unittest.main()
