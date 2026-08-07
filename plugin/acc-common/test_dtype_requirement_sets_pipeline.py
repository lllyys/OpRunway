#!/usr/bin/env python3
"""dtype requirement sets 从 spec 到 caseset/dry-run/staged gate 的闭环。"""

import copy
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

import dtype_requirement_sets as D
import gen_cases as G
import repo_adapter
import validate_acceptance_state as V


TASK_SHA = "a" * 64
SOURCE_SHA = "b" * 64


def _evidence(membership, source_kind, source_sha, cite):
    return {
        "membership": membership,
        "source_kind": source_kind,
        "source_sha256": source_sha,
        "cite": cite,
        "quote": "fixture dtype authority",
        "interpretation": f"fixture {membership}",
    }


def _contract():
    return {
        "schema": D.SCHEMA,
        "schema_version": D.SCHEMA_VERSION,
        "main_table_required": ["float32"],
        "preservation_required": ["float32", "int16"],
        "regression_extension": ["int16"],
        "members": [
            {
                "dtype": "float32",
                "memberships": ["main_table_required", "preservation_required"],
                "evidence": [
                    _evidence("main_table_required", "taskdoc", TASK_SHA, "task.md:1"),
                    _evidence("preservation_required", "source_reference", SOURCE_SHA,
                              "source.md:1"),
                ],
            },
            {
                "dtype": "int16",
                "memberships": ["preservation_required", "regression_extension"],
                "evidence": [
                    _evidence("preservation_required", "source_reference", SOURCE_SHA,
                              "source.md:1"),
                    _evidence("regression_extension", "taskdoc", TASK_SHA, "task.md:2"),
                ],
            },
        ],
    }


def _provenance():
    return {"state": "resolved",
            "sources": [{"kind": "taskbook", "cite": "task.md:1"}]}


def _spec(with_contract=True):
    relation = {"rule": "follows", "operand": "x", "provenance": _provenance()}
    value = {
        "op": "DtypeSetsWitness",
        "runner_form": "cpp_extension",
        "verify_mode": "numerical",
        "operator_class": "structural",
        "params_source": "fixture",
        "params": [
            {"name": "x", "io": "in", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["float32"]},
            {"name": "out", "io": "out", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["float32"],
             "dtype_relation": copy.deepcopy(relation)},
        ],
        "precision": {"oracle": "ascendoptest", "standard": "ascendoptest_default",
                      "case_source": "generated", "case_target": 1},
        "call_variants": [{"when": {"always": True}, "symbol": "DtypeSetsWitness",
                           "active_attrs": [], "active_outputs": ["out"]}],
        "multi_input_contract": {
            "schema_version": "multi_input.v1",
            "output": {"name": "out", "kind": "tensor", "binding": "device_tensor",
                       "format": "nd", "shape": copy.deepcopy(relation),
                       "dtype": copy.deepcopy(relation)},
            "profiles": [{"profile_id": "float32", "inputs": [{
                "name": "x", "kind": "tensor", "binding": "device_tensor",
                "shape": [2, 3], "dtype": "float32", "format": "nd",
            }]}],
            "required_coverage": {},
        },
        "dtype_required": ["float32", "int16"],
        "dtype_tested": ["float32"],
        "task_pr_gaps": [{
            "kind": "dtype_unsupported_by_op_def", "dtypes": ["int16"],
            "op_def_dtypes": ["float32"], "task_doc_ref": "task.md:2",
            "op_def_ref": "op_def.cpp:1",
        }],
    }
    if with_contract:
        value["dtype_requirement_sets"] = _contract()
    return value


def _golden():
    return G.Golden(lambda inputs, _attrs: np.asarray(inputs[0]).copy(),
                    "fixture", "fixture", None, None)


class DtypeRequirementSetsPipelineTest(unittest.TestCase):
    def _generate(self, spec):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        with mock.patch.object(G, "load_golden", return_value=_golden()):
            return G.gen_cases(spec, work.name)

    def test_receipt_is_mirrored_in_caseset_and_dry_run(self):
        spec = _spec()
        caseset = self._generate(spec)
        expected = D.from_spec(spec)
        self.assertEqual(caseset["dtype_requirement_sets_receipt"], expected)
        self.assertEqual(caseset["dtype_requirement_sets_sha256"], expected["sha256"])
        golden_root = tempfile.TemporaryDirectory()
        self.addCleanup(golden_root.cleanup)
        golden_path = os.path.join(golden_root.name, "golden.py")
        with open(golden_path, "w", encoding="utf-8") as dst:
            dst.write("# dependency bytes for dry-run fixture\n")
        with mock.patch.object(G, "load_golden", return_value=_golden()), \
                mock.patch.object(repo_adapter, "op_dir", return_value=golden_root.name):
            dry = G._build_dry_run_ledger(spec)
        self.assertEqual(dry["coverage"]["dtype_requirement_sets_receipt"], expected)
        self.assertEqual(
            dry["coverage"]["dtype_requirement_sets_sha256"], expected["sha256"])
        errors = []
        V._gate_dtype_requirement_sets_authority(caseset, spec, errors)
        self.assertEqual(errors, [])

    def test_coherent_caseset_rewrite_cannot_replace_staged_spec_authority(self):
        spec = _spec()
        caseset = self._generate(spec)
        forged_contract = copy.deepcopy(_contract())
        forged_contract["members"][1]["evidence"][1]["quote"] = "forged"
        forged = D.resolve(forged_contract)
        caseset["dtype_requirement_sets_receipt"] = forged
        caseset["dtype_requirement_sets_sha256"] = forged["sha256"]
        errors = []
        V._gate_dtype_requirement_sets_authority(caseset, spec, errors)
        self.assertTrue(any("staged spec" in error or "摘要" in error for error in errors), errors)

    def test_required_projection_drift_is_rejected_before_case_materialization(self):
        spec = _spec()
        spec["dtype_required"] = ["float32"]
        with self.assertRaisesRegex(ValueError, "dtype_required.*requirement sets"):
            self._generate(spec)

    def test_absent_contract_keeps_legacy_product_free_of_new_fields(self):
        caseset = self._generate(_spec(with_contract=False))
        self.assertNotIn("dtype_requirement_sets_receipt", caseset)
        self.assertNotIn("dtype_requirement_sets_sha256", caseset)


if __name__ == "__main__":
    unittest.main()
