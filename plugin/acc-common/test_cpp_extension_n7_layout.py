#!/usr/bin/env python3
"""N7 runner seam：显式数组属性、base storage 与 input/output layout 收据。"""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

import cpp_extension_adapter as A
import cpp_extension_codegen as C
import cpp_extension_driver as D
import tensor_shape_attrs as TSA
import validate_acceptance_state as G


def _attr_spec():
    return {
        "op": "ArrayContractWitness",
        "runner_form": "cpp_extension",
        "aclnn_tensor_format": "nd",
        "params": [
            {"name": "x", "io": "in", "dtype": ["float32"]},
            {"name": "shifts", "io": "attr", "dtype": ["int64"],
             "attr_type": "int_array", "default": [1]},
            {"name": "dims", "io": "attr", "dtype": ["int64"],
             "attr_type": "int_array", "default": []},
            {"name": "out", "io": "out", "dtype": ["<from_input>"]},
        ],
        "call_variants": [{
            "symbol": "ArrayContractWitness",
            "active_attrs": ["shifts", "dims"],
            "active_outputs": ["out"],
            "stage2_form": "standard",
        }],
    }


def _attr_caseset():
    return {
        "op": "ArrayContractWitness",
        "cases": [{
            "id": "empty-dims",
            "attrs": {"shifts": [1], "dims": []},
            "aclnn_call": {
                "symbol": "ArrayContractWitness",
                "slots": [
                    {"role": "in", "name": "x", "input_idx": 0},
                    {"role": "attr", "name": "shifts",
                     "ctype": "int_array", "value": [1]},
                    {"role": "attr", "name": "dims",
                     "ctype": "int_array", "value": []},
                    {"role": "out", "name": "out", "output_idx": 0},
                ],
            },
        }],
    }


def _layout_receipt(case_id, name, tensor_index, role, shape, strides,
                    *, offset, base_numel):
    contiguous = TSA.contiguous_strides(shape) == tuple(strides)
    declaration = {
        "case_id": case_id,
        "tensor_name": name,
        "tensor_index": tensor_index,
        "role": role,
        "format": "nd",
        "logical_shape": list(shape),
        "layout_kind": (TSA.LAYOUT_CONTIGUOUS if contiguous
                        else TSA.LAYOUT_NONCONTIGUOUS),
        "layout_capability": (
            TSA.LAYOUT_CAPABILITY_CONTIGUOUS if contiguous
            else TSA.LAYOUT_CAPABILITY_SPAN_SEPARABLE),
        "strides": list(strides),
        "storage_offset": offset,
        "base_storage_numel": base_numel,
    }
    return TSA.make_layout_receipt(
        declaration,
        expected_shape=shape,
        expected_role=role,
        expected_case_id=case_id,
        expected_tensor_name=name,
        expected_tensor_index=tensor_index,
    )


def _layout_spec(*, rank_default=False):
    spec = {
        "op": "LayoutContractWitness",
        "runner_form": "cpp_extension",
        "params": [
            {"name": "x", "io": "in", "dtype": ["float32"]},
            {"name": "prob", "io": "attr", "dtype": ["float32"],
             "default": 0.5},
            {"name": "out", "io": "out", "dtype": ["<from_input>"]},
        ],
        "call_variants": [{
            "symbol": "LayoutContractWitness",
            "active_attrs": ["prob"],
            "active_outputs": ["out"],
            "stage2_form": "standard",
        }],
    }
    if not rank_default:
        spec["aclnn_tensor_format"] = "nd"
    return spec


def _layout_caseset():
    case_id = "layout-case"
    in_requirement = "taskdoc:layout:input-noncontiguous"
    out_requirement = "taskdoc:layout:output-noncontiguous"
    input_receipt = _layout_receipt(
        case_id, "x", 0, TSA.LAYOUT_ROLE_INPUT,
        [6, 4], [1, 6], offset=0, base_numel=24)
    output_receipt = _layout_receipt(
        case_id, "out", 0, TSA.LAYOUT_ROLE_OUTPUT,
        [6, 4], [1, 6], offset=0, base_numel=24)
    input_sha = TSA.layout_receipt_sha256(input_receipt)
    output_sha = TSA.layout_receipt_sha256(output_receipt)
    case = {
        "id": case_id,
        "inputs": [{
            "name": "x", "kind": "tensor", "binding": "device_tensor",
            "shape": [6, 4], "dtype": "float32", "format": "nd",
            "storage_representation": "base_storage_v1",
            "base_storage_path": f"{case_id}/x.base.npy",
            "layout_requirement_id": in_requirement,
            "layout_receipt": input_receipt,
            "layout_receipt_sha256": input_sha,
        }],
        "attrs": {"prob": 0.5},
        "expected": {
            "out_shape": [6, 4], "compare_dtype": "float32",
            "layout_requirement_id": out_requirement,
            "layout_receipt": output_receipt,
            "layout_receipt_sha256": output_sha,
        },
        "aclnn_call": {
            "symbol": "LayoutContractWitness",
            "slots": [
                {"role": "in", "name": "x", "input_idx": 0,
                 "kind": "tensor", "binding": "device_tensor",
                 "shape": [6, 4], "dtype": "float32", "format": "nd",
                 "storage_representation": "base_storage_v1",
                 "layout_requirement_id": in_requirement,
                 "layout_receipt_sha256": input_sha},
                {"role": "attr", "name": "prob", "ctype": "float32",
                 "value": 0.5},
                {"role": "out", "name": "out", "output_idx": 0,
                 "kind": "tensor", "binding": "device_tensor",
                 "shape": [6, 4], "dtype": "float32", "format": "nd",
                 "layout_requirement_id": out_requirement,
                 "layout_receipt_sha256": output_sha},
            ],
        },
    }
    ledger = {
        "schema": "oprunway.tensor_layout_ledger",
        "schema_version": 1,
        "cases": [{
            "case_id": case_id,
            "inputs": [{
                "requirement_id": in_requirement,
                "tensor_index": 0,
                "name": "x",
                "layout_receipt_sha256": input_sha,
            }],
            "outputs": [{
                "requirement_id": out_requirement,
                "tensor_index": 0,
                "name": "out",
                "layout_receipt_sha256": output_sha,
            }],
        }],
    }
    return {
        "op": "LayoutContractWitness",
        "layout_ledger": ledger,
        "layout_ledger_sha256": A._canonical_sha(ledger),
        "cases": [case],
    }


def _observation(receipt, pointer):
    return {
        "layout_receipt": copy.deepcopy(receipt),
        "layout_receipt_sha256": TSA.layout_receipt_sha256(receipt),
        "storage_data_ptr": pointer,
    }


def _layout_execution(caseset):
    case = caseset["cases"][0]
    inp = case["inputs"][0]
    out = case["expected"]
    input_before = _observation(inp["layout_receipt"], 4096)
    output_before = _observation(out["layout_receipt"], 8192)
    return {
        "schema": "oprunway.cpp_extension_layout_execution",
        "schema_version": 1,
        "layout_ledger_sha256": caseset["layout_ledger_sha256"],
        "cases": [{
            "case_id": case["id"],
            "inputs": [{
                "layout_requirement_id": inp["layout_requirement_id"],
                "tensor_index": 0,
                "name": "x",
                "expected_layout_receipt_sha256": inp["layout_receipt_sha256"],
                "before": input_before,
                "after": copy.deepcopy(input_before),
            }],
            "outputs": [{
                "layout_requirement_id": out["layout_requirement_id"],
                "tensor_index": 0,
                "name": "out",
                "expected_layout_receipt_sha256": out["layout_receipt_sha256"],
                "before": output_before,
                "after": copy.deepcopy(output_before),
            }],
        }],
    }


class ExplicitAttrTypeSeamTest(unittest.TestCase):
    def test_empty_int_array_reaches_cpp_schema_manifest_and_plan(self):
        with tempfile.TemporaryDirectory() as out:
            manifest = C.generate(_attr_spec(), out)
            cpp = Path(out, "csrc", "oprunway_extension.cpp").read_text(
                encoding="utf-8")
        self.assertIn("at::IntArrayRef dims", cpp)
        self.assertIn("int[] dims", cpp)
        params = manifest["attr_parameter_contract"]["parameters"]
        self.assertIn({
            "name": "dims", "attr_type": "int_array",
            "attr_ctype": "int_array", "source": "spec_declared",
        }, params)
        self.assertEqual(
            manifest["variants"][0]["active_attr_contracts"],
            [{"name": "shifts", "attr_ctype": "int_array"},
             {"name": "dims", "attr_ctype": "int_array"}],
        )
        plan = A.build_invocation_plan(_attr_caseset(), manifest)
        self.assertEqual(plan["cases"][0]["slots"][2]["value"], [])
        self.assertEqual(
            plan["cases"][0]["active_attr_contracts"],
            manifest["variants"][0]["active_attr_contracts"],
        )

    def test_attr_slot_ctype_mutation_is_rejected_not_just_name_matched(self):
        with tempfile.TemporaryDirectory() as out:
            manifest = C.generate(_attr_spec(), out)
        caseset = _attr_caseset()
        caseset["cases"][0]["aclnn_call"]["slots"][2]["ctype"] = "int64"
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "attr_ctype"):
            A.build_invocation_plan(caseset, manifest)

    def test_attr_slot_value_must_match_case_attrs_exactly(self):
        with tempfile.TemporaryDirectory() as out:
            manifest = C.generate(_attr_spec(), out)
        caseset = _attr_caseset()
        caseset["cases"][0]["aclnn_call"]["slots"][2]["value"] = [0]
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "slot.value"):
            A.build_invocation_plan(caseset, manifest)

    def test_host_scalar_may_not_opt_into_int_array(self):
        spec = _attr_spec()
        spec["params"][2].update({
            "kind": "scalar", "binding": "host_scalar",
        })
        with self.assertRaisesRegex(C.CppExtensionCodegenError, "host_scalar.*int_array"):
            C.generate(spec, tempfile.mkdtemp())
        for bad in (None, "", "array"):
            malformed = _attr_spec()
            malformed["params"][1]["attr_type"] = bad
            with self.subTest(attr_type=bad), self.assertRaisesRegex(
                    C.CppExtensionCodegenError, "attr_type"):
                C.generate(malformed, tempfile.mkdtemp())


class LayoutAdapterContractTest(unittest.TestCase):
    def _manifest(self, *, rank_default=False):
        with tempfile.TemporaryDirectory() as out:
            return C.generate(_layout_spec(rank_default=rank_default), out)

    def test_layout_ledger_and_slots_are_bound_into_plan(self):
        caseset = _layout_caseset()
        manifest = self._manifest()
        plan = A.build_invocation_plan(caseset, manifest)
        self.assertEqual(
            plan["layout_ledger_sha256"], caseset["layout_ledger_sha256"])
        self.assertEqual(
            plan["cases"][0]["slots"][0]["layout_receipt_sha256"],
            caseset["cases"][0]["inputs"][0]["layout_receipt_sha256"],
        )

    def test_layout_slot_or_requirement_mutation_fails_closed(self):
        manifest = self._manifest()
        mutations = []
        bad_slot = _layout_caseset()
        bad_slot["cases"][0]["aclnn_call"]["slots"][0][
            "layout_receipt_sha256"] = "0" * 64
        mutations.append(bad_slot)
        bad_requirement = _layout_caseset()
        bad_requirement["layout_ledger"]["cases"][0]["inputs"][0][
            "requirement_id"] = "taskdoc:layout:other"
        bad_requirement["layout_ledger_sha256"] = A._canonical_sha(
            bad_requirement["layout_ledger"])
        mutations.append(bad_requirement)
        for caseset in mutations:
            with self.subTest(caseset=caseset), self.assertRaises(
                    A.CppExtensionAdapterError):
                A.build_invocation_plan(caseset, manifest)

    def test_rank_default_plus_layout_is_rejected(self):
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "rank_default|ND|nd"):
            A.build_invocation_plan(_layout_caseset(), self._manifest(rank_default=True))

    def test_host_scalar_does_not_shift_tensor_index(self):
        caseset = _layout_caseset()
        case = caseset["cases"][0]
        case["parameter_contract"] = {
            "profile_id": "tensor-host-scalar",
            "inputs": [
                {"name": "x", "kind": "tensor", "binding": "device_tensor",
                 "shape": [6, 4], "dtype": "float32", "format": "nd"},
                {"name": "prob", "kind": "scalar", "binding": "host_scalar",
                 "dtype": "float32", "value": 0.5},
            ],
            "output": {
                "name": "out", "kind": "tensor", "binding": "device_tensor",
                "shape": [6, 4], "dtype": "float32", "format": "nd",
            },
        }
        validated = A.validate_caseset_layout_contract(caseset)
        self.assertEqual(validated["ledger"]["cases"][0]["inputs"][0][
            "tensor_index"], 0)
        mutated = copy.deepcopy(caseset)
        receipt = mutated["cases"][0]["inputs"][0]["layout_receipt"]
        receipt["tensor_index"] = 1
        with self.assertRaises(A.CppExtensionAdapterError):
            A.validate_caseset_layout_contract(mutated)


class LayoutReceiptAndGateTest(unittest.TestCase):
    def test_receipt_and_evidence_mirror_external_ledger_and_actual_layouts(self):
        caseset = _layout_caseset()
        execution = _layout_execution(caseset)
        validated = A.validate_layout_execution(caseset, execution)
        self.assertEqual(validated, execution)
        receipt = {
            "layout_ledger_sha256": caseset["layout_ledger_sha256"],
            "layout_execution": copy.deepcopy(execution),
        }
        evidence = [{
            "case_id": "layout-case",
            "layout_ledger_sha256": caseset["layout_ledger_sha256"],
            "layout_observations": copy.deepcopy(execution["cases"][0]),
        }]
        errors = []
        G._gate_cpp_extension_layout(caseset, receipt, evidence, errors)
        self.assertEqual(errors, [])

    def test_pointer_or_evidence_layout_mutation_is_rejected(self):
        caseset = _layout_caseset()
        execution = _layout_execution(caseset)
        execution["cases"][0]["outputs"][0]["after"][
            "storage_data_ptr"] = 12288
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "storage|ptr|指针"):
            A.validate_layout_execution(caseset, execution)

        execution = _layout_execution(caseset)
        receipt = {
            "layout_ledger_sha256": caseset["layout_ledger_sha256"],
            "layout_execution": execution,
        }
        evidence = [{
            "case_id": "layout-case",
            "layout_ledger_sha256": caseset["layout_ledger_sha256"],
            "layout_observations": copy.deepcopy(execution["cases"][0]),
        }]
        evidence[0]["layout_observations"]["outputs"][0]["after"][
            "layout_receipt"]["strides"] = [4, 1]
        errors = []
        G._gate_cpp_extension_layout(caseset, receipt, evidence, errors)
        self.assertTrue(errors)


@unittest.skipUnless(
    os.environ.get("OPRUNWAY_NPU_TEST") == "1",
    "仅在 A3 NPU 新目录显式启用",
)
class RealNpuBaseStorageLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        import torch_npu  # noqa: F401

        cls.torch = torch
        device = int(os.environ.get("OPRUNWAY_NPU_DEVICE", "0"))
        torch.npu.set_device(device)

    def test_base_storage_moves_to_npu_before_as_strided_roundtrip(self):
        caseset = _layout_caseset()
        case = caseset["cases"][0]
        item = case["inputs"][0]
        slot = case["aclnn_call"]["slots"][0]
        with tempfile.TemporaryDirectory() as work:
            os.makedirs(os.path.join(work, case["id"]))
            base = np.arange(24, dtype=np.float32)
            np.save(os.path.join(work, item["base_storage_path"]), base)
            view = D._input_tensor(
                self.torch, np, work, item, slot=slot, case_id=case["id"])
        self.assertEqual(list(view.shape), [6, 4])
        self.assertEqual(list(view.stride()), [1, 6])
        self.assertEqual(view.storage_offset(), 0)
        np.testing.assert_array_equal(
            view.cpu().numpy(), base.reshape(4, 6).transpose(1, 0))

    def test_output_sentinel_view_preserves_layout_and_rejects_contiguous_mutation(self):
        case = _layout_caseset()["cases"][0]
        output = dict(case["expected"])
        # legacy 单输出的名字来自外部 aclnn_call slot；receipt 自身不能反取身份。
        output["name"] = case["aclnn_call"]["slots"][2]["name"]
        view = D._empty_output(
            self.torch, output, case_id=case["id"], output_index=0)
        before = D._observe_runtime_layout(
            view, output, role=TSA.LAYOUT_ROLE_OUTPUT,
            case_id=case["id"], tensor_name="out", tensor_index=0)
        after = D._observe_runtime_layout(
            view, output, role=TSA.LAYOUT_ROLE_OUTPUT,
            case_id=case["id"], tensor_name="out", tensor_index=0)
        D._assert_layout_observation_roundtrip(before, after, "layout-case.out")
        with self.assertRaises(D.LayoutContractError):
            D._observe_runtime_layout(
                view.contiguous(), output, role=TSA.LAYOUT_ROLE_OUTPUT,
                case_id=case["id"], tensor_name="out", tensor_index=0)


if __name__ == "__main__":
    unittest.main()
