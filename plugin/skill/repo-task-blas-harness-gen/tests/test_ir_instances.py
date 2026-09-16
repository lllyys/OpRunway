#!/usr/bin/env python3
"""IR 实例与负例的机械冻结测试：contract-ir.md v1 的可判子集。"""

import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
D = Path(__file__).resolve().parent / "ir-instances"

TOP_KEYS = {"version", "facts_schema_version", "op", "family", "symbol", "returns",
            "params", "columns", "buffers", "movement", "golden", "verify",
            "status_plan", "smoke", "upload_guard"}
GATES = {"pre_ir", "param_gate", "ast_gate", "signature_table"}
CHECK_KINDS = {"null_check", "quick_return", "cond"}


class TestPositiveInstances(unittest.TestCase):
    def _load(self, name):
        return json.loads((D / f"{name}.json").read_text(encoding="utf-8"))

    def test_top_shape(self):
        for name in ("sasum", "sgemm", "sger"):
            ir = self._load(name)
            self.assertEqual(set(ir), TOP_KEYS, name)
            self.assertEqual(ir["version"], 3)
            self.assertEqual(ir["returns"], "aclblasStatus_t")

    def test_checks_are_ordered_discriminated_union(self):
        for name in ("sasum", "sgemm", "sger"):
            for c in self._load(name)["status_plan"]["checks"]:
                self.assertIn(c["kind"], CHECK_KINDS)
                self.assertTrue(c["status"].startswith("ACLBLAS_STATUS_"))

    def test_accum_rule(self):
        self.assertIsNotNone(self._load("sasum")["golden"]["accum"])
        self.assertIsNone(self._load("sgemm")["golden"]["accum"])

    def test_sgemm_specifics(self):
        sg = self._load("sgemm")
        self.assertEqual(sg["golden"]["args"][0], {"const": "CblasColMajor"})
        qr = [c for c in sg["status_plan"]["checks"] if c["kind"] == "quick_return"]
        self.assertEqual(len(qr), 1)
        self.assertIs(qr[0]["writes_zero"], False)
        self.assertIsNone(sg["smoke"])

    def test_params_invariants(self):
        for name in ("sasum", "sgemm", "sger"):
            params = self._load(name)["params"]
            self.assertEqual(params[0]["role"], "handle")
            outs = [p for p in params if p.get("dir") in ("out", "inout")]
            self.assertEqual(len(outs), 1, name)


class TestNegatives(unittest.TestCase):
    def test_seven_negatives_with_expected_payloads(self):
        expected = json.loads((D / "expected.json").read_text(encoding="utf-8"))
        self.assertEqual(len(expected), 7)
        for key, payload in expected.items():
            self.assertEqual(
                set(payload), {"code", "gate", "field_path", "observed", "allowed"}, key)
            self.assertIn(payload["gate"], GATES, key)
        for i in range(1, 8):
            json.loads((D / f"negative-{i}.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
