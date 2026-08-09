#!/usr/bin/env python3
"""N6：多输入 caseset → Extension 生成/计划/物化的契约接缝。"""

import copy
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

import cpp_extension_adapter as A
import cpp_extension_codegen as C
import cpp_extension_driver as D
import gen_cases as GC
from test_gen_cases_multi_input import (
    _binary_golden,
    _binary_spec,
    _golden,
    _host_scalar_golden,
    _host_scalar_spec,
)


def _caseset(spec, golden):
    work = tempfile.TemporaryDirectory()
    with mock.patch.object(GC, "load_golden", return_value=_golden(golden)):
        caseset = GC.gen_cases(spec, work.name)
    return work, caseset


class MultiInputCodegenTest(unittest.TestCase):
    def test_per_parameter_formats_drive_conversion_without_global_guess(self):
        spec = _binary_spec()
        with tempfile.TemporaryDirectory() as out:
            manifest = C.generate(spec, out)
            with open(os.path.join(out, "csrc", "oprunway_extension.cpp"), encoding="utf-8") as src:
                cpp = src.read()
        receipt = manifest["multi_input_receipt"]
        self.assertEqual(
            [(row["name"], row["io"], row["format"])
             for row in receipt["tensor_parameters"]],
            [("left", "in", "nd"),
             ("right", "in", "torch_npu_rank_default"),
             ("out", "out", "nd")],
        )
        nd_rows = [row for row in receipt["tensor_parameters"]
                   if row["format"] == "nd"]
        self.assertTrue(nd_rows)
        self.assertTrue(all(row["requested_format"] == "nd"
                            and row["effective_acl_format"] == "ACL_FORMAT_ND"
                            and row["format_source"] == "spec_parameter_contract"
                            for row in nd_rows))
        default_row = next(row for row in receipt["tensor_parameters"]
                           if row["format"] == "torch_npu_rank_default")
        self.assertNotIn("effective_acl_format", default_row)
        self.assertEqual(
            receipt["contract_sha256"],
            C.multi_input_contract.resolve_spec_contract(spec)["sha256"],
        )
        self.assertNotIn("tensor_acl_format", manifest)
        self.assertIn("OprunwayConvertNdTensor(left)", cpp)
        self.assertIn("ConvertType(right)", cpp)
        self.assertIn("OprunwayConvertNdTensor(out)", cpp)
        self.assertNotIn("EXEC_NPU_CMD_EXT(", cpp)
        self.assertEqual(
            manifest["variants"][0]["dispatch"],
            C.DISPATCH_STANDARD_PARAMETER_CONTRACT,
        )

    def test_host_scalar_is_typed_by_profile_not_python_wrapped_number_semantics(self):
        spec = _host_scalar_spec()
        with tempfile.TemporaryDirectory() as out:
            manifest = C.generate(spec, out)
            with open(os.path.join(out, "csrc", "oprunway_extension.cpp"), encoding="utf-8") as src:
                cpp = src.read()
        variant = manifest["variants"][0]
        self.assertEqual(variant["host_scalar_dtypes"], {"prob": "float16"})
        self.assertEqual(variant["entrypoint"], "invoke_v0_s0")
        self.assertIn("const at::Scalar& prob", cpp)
        self.assertIn("Scalar prob", cpp)
        self.assertIn(
            "ConvertType(at::Scalar(static_cast<at::Half>(prob.toDouble())))", cpp)
        self.assertNotIn("ConvertType(prob)", cpp)
        self.assertNotIn("EXEC_NPU_CMD_EXT(", cpp)

    def test_global_format_and_parameter_contract_may_not_compete(self):
        spec = _binary_spec()
        spec["aclnn_tensor_format"] = "nd"
        with self.assertRaisesRegex(C.CppExtensionCodegenError, "全局.*逐参数"):
            C.generate(spec, tempfile.mkdtemp())


class MultiInputAdapterTest(unittest.TestCase):
    def test_plan_binds_case_contract_and_scalar_dtype_entrypoint(self):
        for spec, golden, expected_entrypoint in (
            (_binary_spec(), _binary_golden, "invoke_v0"),
            (_host_scalar_spec(), _host_scalar_golden, "invoke_v0_s0"),
        ):
            work, caseset = _caseset(spec, golden)
            try:
                with tempfile.TemporaryDirectory() as out:
                    manifest = C.generate(spec, out)
                plan = A.build_invocation_plan(caseset, manifest)
            finally:
                work.cleanup()
            self.assertTrue(plan["cases"])
            self.assertEqual(
                {row["entrypoint"] for row in plan["cases"]},
                {expected_entrypoint},
            )
            self.assertEqual(
                plan["multi_input_contract_sha256"],
                manifest["multi_input_receipt"]["contract_sha256"],
            )
            self.assertTrue(all(len(row["parameter_contract_sha256"]) == 64
                                for row in plan["cases"]))

    def test_slot_shape_dtype_format_mutation_is_rejected(self):
        spec = _binary_spec()
        work, caseset = _caseset(spec, _binary_golden)
        try:
            with tempfile.TemporaryDirectory() as out:
                manifest = C.generate(spec, out)
            bad = copy.deepcopy(caseset)
            first = bad["cases"][0]
            input_slot = next(slot for slot in first["aclnn_call"]["slots"]
                              if slot["role"] == "in")
            input_slot["shape"] = [999]
            with self.assertRaisesRegex(A.CppExtensionAdapterError, "parameter_contract"):
                A.build_invocation_plan(bad, manifest)
        finally:
            work.cleanup()

    def test_manifest_receipt_must_be_mirrored_by_driver(self):
        manifest = {"multi_input_receipt": {
            "schema": "oprunway.cpp_extension_multi_input_receipt",
            "schema_version": 1,
            "contract_sha256": "a" * 64,
            "tensor_parameters": [],
            "host_scalar_parameters": [],
            "profile_count": 1,
        }}
        A._validate_multi_input_receipt(
            manifest, {"multi_input_receipt": copy.deepcopy(manifest["multi_input_receipt"])})
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "multi_input_receipt"):
            A._validate_multi_input_receipt(manifest, {})

    def test_nd_effective_acl_literal_and_source_mutations_are_rejected(self):
        spec = _binary_spec()
        with tempfile.TemporaryDirectory() as out:
            manifest = C.generate(spec, out)
        for key, value in (("requested_format", "nchw"),
                           ("effective_acl_format", "ACL_FORMAT_NCHW"),
                           ("format_source", "self_reported")):
            bad = copy.deepcopy(manifest)
            row = next(item for item in bad["multi_input_receipt"]["tensor_parameters"]
                       if item["format"] == "nd")
            row[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(
                    A.CppExtensionAdapterError, "requested/effective/source"):
                A._validate_multi_input_receipt(
                    bad, {"multi_input_receipt": bad["multi_input_receipt"]})

    def test_evidence_carries_the_exact_parameter_identity(self):
        spec = _binary_spec()
        work, caseset = _caseset(spec, _binary_golden)
        try:
            evidence = [{"case_id": case["id"]} for case in caseset["cases"]]
            with tempfile.TemporaryDirectory() as out:
                manifest = C.generate(spec, out)
            receipt = {"multi_input_receipt": manifest["multi_input_receipt"]}
            A._bind_multi_input_evidence(caseset, evidence, receipt)
        finally:
            work.cleanup()
        for case, row in zip(caseset["cases"], evidence):
            self.assertEqual(row["parameter_contract"], case["parameter_contract"])
            self.assertEqual(row["parameter_contract_sha256"], A._canonical_sha(
                case["parameter_contract"]))
            self.assertEqual(
                row["multi_input_contract_sha256"],
                caseset["multi_input_ledger"]["contract_sha256"],
            )


class MultiInputDriverTest(unittest.TestCase):
    def test_on_disk_shape_must_match_per_input_contract(self):
        item = {
            "name": "right", "path": "case/x2.npy", "shape": [2, 1],
            "dtype": "float32", "kind": "tensor", "binding": "device_tensor",
            "format": "nd",
        }
        slot = {
            "role": "in", "name": "right", "input_idx": 1, "shape": [2, 1],
            "dtype": "float32", "kind": "tensor", "binding": "device_tensor",
            "format": "nd",
        }
        with tempfile.TemporaryDirectory() as work:
            os.makedirs(os.path.join(work, "case"))
            np.save(os.path.join(work, item["path"]), np.zeros((1, 2), dtype=np.float32))
            with self.assertRaisesRegex(D.DriverError, "落盘 shape"):
                D._input_tensor(None, np, work, item, slot=slot, case_id="case")

    def test_case_item_and_plan_slot_must_match_before_torch(self):
        item = {
            "name": "left", "path": "unused.npy", "shape": [], "dtype": "float16",
            "kind": "tensor", "binding": "device_tensor", "format": "nd",
        }
        slot = {
            "role": "in", "name": "left", "input_idx": 0, "shape": [], "dtype": "float32",
            "kind": "tensor", "binding": "device_tensor", "format": "nd",
        }
        with self.assertRaisesRegex(D.DriverError, "slot.*input"):
            D._validate_input_slot(item, slot, "rank0")


if __name__ == "__main__":
    unittest.main()
