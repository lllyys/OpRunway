#!/usr/bin/env python3
"""aclnn 静态 CP-C0 预检单测；不加载 ACL、不访问 NPU。"""

import hashlib
import json
import os
import tempfile
import unittest

import content_address
import preflight_aclnn as P


_HEADER = """
aclnnStatus aclnnReduceGetWorkspaceSize(
    const aclTensor *self,
    int64_t dim,
    bool keepDim,
    aclTensor *valuesOut,
    aclTensor *indicesOut,
    uint64_t *workspaceSize,
    aclOpExecutor **executor);
aclnnStatus aclnnReduce(void *workspace, uint64_t workspaceSize,
                        aclOpExecutor *executor, aclrtStream stream);
"""
_HEAD = "a" * 40
_PATH = "experimental/index/reduce/op_host/op_api/aclnn_reduce.h"


def _spec(active_attrs=("dim", "keepDim")):
    return {
        "op": "Reduce",
        "runner_form": "aclnn_py",
        "params": [
            {"name": "self", "io": "in", "dtype": ["float32"]},
            {"name": "dim", "io": "attr", "dtype": ["int64"], "default": None},
            {"name": "keepDim", "io": "attr", "dtype": ["bool"], "default": False},
            {"name": "valuesOut", "io": "out", "dtype": ["<from_input>"]},
            {"name": "indicesOut", "io": "out", "dtype": ["int32"]},
        ],
        "call_variants": [
            {
                "when": {"attr": "dim", "is_null": True},
                "symbol": "Reduce",
                "active_attrs": list(active_attrs),
                "attrs": {"dim": 0, "keepDim": False},
                "active_outputs": ["valuesOut"],
            },
            {
                "when": {"always": True},
                "symbol": "Reduce",
                "active_attrs": ["dim", "keepDim"],
                "active_outputs": ["valuesOut", "indicesOut"],
            },
        ],
    }


class AclnnPreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self._write("spec.json", _spec())
        self._write("pr_facts.json", {
            "head_sha": _HEAD,
            "key_files": {_PATH: _HEADER},
        })
        raw = _HEADER.encode()
        source = {
            "contract_version": 1,
            "pr": {"head_sha": _HEAD},
            "key_files": [{
                "path": _PATH,
                "ref": _HEAD,
                "bytes_sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }],
            "derived": {
                "aclnn_headers": [_PATH],
                "interface_kind": "aclnn_2stage",
            },
            "completeness": {"status": "complete", "reasons": []},
        }
        content_address.write_artifact(
            self.root, "source_facts.json", P._SOURCE_DOMAIN, source)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel, value):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as out:
            json.dump(value, out)

    def test_matching_variants_only_ready_for_later_trust_gate(self):
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "READY_WAIT_NPU_TRUST_GATE")
        self.assertIsNone(result["acceptance_verdict"])
        self.assertEqual(
            result["required_next_gate"], "NPU_BUILD_AND_HARNESS_TRUST_GATE")
        self.assertEqual(
            {item["status"] for item in result["variants"]},
            {"STATIC_SIGNATURE_MATCH"})

    def test_skipping_real_signature_attrs_is_blocked(self):
        self._write("spec.json", _spec(active_attrs=()))
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("arity", result["blocked_reasons"][0])

    def test_header_tamper_against_source_digest_is_blocked(self):
        facts = json.load(open(
            os.path.join(self.root, "pr_facts.json"), encoding="utf-8"))
        facts["key_files"][_PATH] += "\n// changed"
        self._write("pr_facts.json", facts)
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("摘要不一致", result["blocked_reasons"][0])

    def test_unknown_symbol_is_blocked(self):
        spec = _spec()
        spec["call_variants"][1]["symbol"] = "Other"
        self._write("spec.json", spec)
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("无唯一签名", result["blocked_reasons"][0])

    def test_cpp_form_is_not_applicable_not_pass(self):
        spec = _spec()
        spec["runner_form"] = "cpp"
        self._write("spec.json", spec)
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "NOT_APPLICABLE")
        self.assertIsNone(result["acceptance_verdict"])

    def test_cpp_extension_reuses_static_abi_gate_but_has_distinct_next_gate(self):
        spec = _spec()
        spec["runner_form"] = "cpp_extension"
        self._write("spec.json", spec)
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "READY_WAIT_NPU_TRUST_GATE")
        self.assertEqual(result["bindings"]["runner_form"], "cpp_extension")
        self.assertEqual(
            result["required_next_gate"],
            "CPP_EXTENSION_BUILD_LOAD_AND_HARNESS_TRUST_GATE")

    def test_malformed_param_is_machine_blocked_not_traceback(self):
        spec = _spec()
        spec["params"][0] = "not-an-object"
        self._write("spec.json", spec)
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("spec.params[0]", result["blocked_reasons"][0])


if __name__ == "__main__":
    unittest.main()
