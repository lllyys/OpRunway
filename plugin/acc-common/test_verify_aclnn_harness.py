#!/usr/bin/env python3
"""aclnn_py harness 信任门的纯确定性单测；不 build、不访问 NPU。"""

import json
import os
import tempfile
import unittest
from unittest import mock

import content_address
import run_workflow
import verify_aclnn_harness as H


def _variant(nullable):
    return {
        "symbol": "Reduce",
        "slot_contract": [
            {"name": "self", "role": "in", "nullable": False},
            {"name": "dim", "role": "attr", "nullable": False, "ctype": "int64"},
            {"name": "keepDim", "role": "attr", "nullable": False, "ctype": "bool"},
            {"name": "valuesOut", "role": "out", "nullable": False},
            {"name": "indicesOut", "role": "out", "nullable": nullable},
        ],
    }


def _case(cid, dtype, nullable, size):
    slots = [
        {"role": "in", "name": "self", "input_idx": 0},
        {"role": "attr", "name": "dim", "ctype": "int64", "value": 0},
        {"role": "attr", "name": "keepDim", "ctype": "bool", "value": False},
        {"role": "out", "name": "valuesOut", "output_idx": 0},
        ({"role": "out_null", "name": "indicesOut"} if nullable
         else {"role": "out", "name": "indicesOut", "output_idx": 1}),
    ]
    outputs = [{
        "name": "valuesOut",
        "role": "value",
        "out_shape": [size],
        "policy": {"kind": "torch_allclose"},
    }]
    if not nullable:
        outputs.append({
            "name": "indicesOut",
            "role": "index",
            "out_shape": [size],
            "policy": {"kind": "exact"},
        })
    return {
        "id": cid,
        "inputs": [{"name": "self", "dtype": dtype, "shape": [size]}],
        "expected": {"outputs": outputs},
        "aclnn_call": {"symbol": "Reduce", "slots": slots},
    }


def _fixtures():
    preflight = {
        "status": "READY_WAIT_NPU_TRUST_GATE",
        "bindings": {"spec_sha256": "unused", "pr_head_sha": "a" * 40},
        "variants": [_variant(True), _variant(False)],
    }
    caseset = {
        "op": "Reduce",
        "dtype_required": ["float32", "float16"],
        "cases": [
            _case("f32_scalar_large", "float32", True, 64),
            _case("f32_scalar_small", "float32", True, 1),
            _case("f16_multi_small", "float16", False, 1),
            _case("f32_multi_large", "float32", False, 128),
        ],
    }
    return caseset, preflight


class SelectionTest(unittest.TestCase):
    def test_minimum_witness_covers_dtype_variants_attrs_and_multi_output(self):
        caseset, preflight = _fixtures()
        selected, coverage = H.select_cases(caseset, preflight)
        self.assertEqual(
            [case["id"] for case in selected],
            ["f16_multi_small", "f32_scalar_small"])
        self.assertEqual(coverage["selected_count"], 2)
        self.assertEqual(coverage["full_case_count"], 4)
        self.assertIn("capability:scalar_attr", coverage["covered"])
        self.assertIn("capability:multi_output", coverage["covered"])
        self.assertIn("dtype:float32", coverage["covered"])
        self.assertIn("dtype:float16", coverage["covered"])

    def test_missing_required_dtype_fails_closed(self):
        caseset, preflight = _fixtures()
        caseset["dtype_required"].append("int32")
        with self.assertRaisesRegex(ValueError, "dtype:int32"):
            H.select_cases(caseset, preflight)

    def test_slot_order_must_bind_unique_preflight_variant(self):
        caseset, preflight = _fixtures()
        slots = caseset["cases"][0]["aclnn_call"]["slots"]
        slots[0], slots[1] = slots[1], slots[0]
        with self.assertRaisesRegex(ValueError, "无法唯一绑定"):
            H.select_cases(caseset, preflight)


class ReceiptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.spec = {"op": "Reduce", "runner_form": "aclnn_py"}
        self.caseset, self.preflight = _fixtures()
        selected, coverage = H.select_cases(self.caseset, self.preflight)
        payload = {
            "schema": H._SCHEMA,
            "schema_version": 1,
            "status": H._STATUS_TRUSTED,
            "scope": "harness-only",
            "acceptance_verdict": None,
            "bindings": H._receipt_bindings(
                self.spec, self.caseset, self.preflight),
            "coverage": coverage,
            "checks": [
                {"case_id": case["id"], "result": "pass", "outputs": []}
                for case in selected
            ],
        }
        os.makedirs(os.path.join(self.root, "work"))
        content_address.write_artifact(
            self.root, "work/aclnn_preflight.json",
            H._PREFLIGHT_DOMAIN, self.preflight)
        content_address.write_artifact(
            self.root, "work/aclnn_harness_trust.json",
            H._TRUST_DOMAIN, payload)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_receipt_is_reusable_for_same_full_caseset(self):
        receipt = H.validate_receipt(
            self.root, "work/aclnn_harness_trust.json",
            self.spec, self.caseset)
        self.assertEqual(receipt["status"], H._STATUS_TRUSTED)

    def test_caseset_drift_is_rejected(self):
        changed = json.loads(json.dumps(self.caseset))
        changed["cases"][0]["inputs"][0]["shape"] = [65]
        with self.assertRaisesRegex(ValueError, "caseset_sha256 已漂移"):
            H.validate_receipt(
                self.root, "work/aclnn_harness_trust.json",
                self.spec, changed)


class WorkflowHardGateTest(unittest.TestCase):
    def test_aclnn_py_cannot_enter_adapter_without_trust_receipt(self):
        caseset, _ = _fixtures()
        with tempfile.TemporaryDirectory() as td:
            spec_path = os.path.join(td, "spec.json")
            out_dir = os.path.join(td, "report")
            with open(spec_path, "w", encoding="utf-8") as out:
                json.dump({"op": "Reduce", "runner_form": "aclnn_py"}, out)
            with mock.patch.object(
                    run_workflow.gen_cases, "gen_cases", return_value=caseset), \
                    mock.patch.dict(
                        run_workflow.repo_adapter.MODES,
                        {"aclnn_py": mock.Mock(side_effect=AssertionError(
                            "adapter 不应在信任门前启动"))},
                        clear=False):
                with self.assertRaisesRegex(SystemExit, "CP-C harness 真机信任门"):
                    run_workflow.run(
                        spec_path, mode="aclnn_py", out_dir=out_dir)


if __name__ == "__main__":
    unittest.main()
