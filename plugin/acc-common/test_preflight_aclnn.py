#!/usr/bin/env python3
"""aclnn 静态 CP-C0 预检单测；不加载 ACL、不访问 NPU。"""

import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

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
        # provenance_kind 两侧都必须**显式**声明（`fetch_pr` / `scan_pr_snapshot` 本就恒写）：
        # source_provenance.bind 不再默认成 gitcode_pr，也不再只读单侧。
        self._write("pr_facts.json", {
            "provenance_kind": "gitcode_pr",
            "head_sha": _HEAD,
            "key_files": {_PATH: _HEADER},
        })
        raw = _HEADER.encode()
        source = {
            "contract_version": 1,
            "pr": {"provenance_kind": "gitcode_pr", "head_sha": _HEAD},
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

    def _rewrite_as_local_source(self):
        """把 CP-A 两份事实包换成「本地源码」形态（`fetch_source --pr-snapshot` 产的样子）。"""
        merkle, scope = "b" * 64, "gaussian_blur"
        self._write("pr_facts.json", {
            "declared_source_form": "local_source",
            "provenance_kind": "local_snapshot",
            "head_sha": None,
            "snapshot_merkle_sha256": merkle,
            "snapshot_scope": scope,
            "key_files": {_PATH: _HEADER},
        })
        raw = _HEADER.encode()
        content_address.write_artifact(self.root, "source_facts.json", P._SOURCE_DOMAIN, {
            "contract_version": 1,
            "declared_source_form": "local_source",
            "pr": {"provenance_kind": "local_snapshot", "head_sha": None,
                   "snapshot_merkle_sha256": merkle, "snapshot_scope": scope},
            "key_files": [{
                "path": _PATH, "ref": "local_snapshot",
                "bytes_sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
            }],
            "derived": {"aclnn_headers": [_PATH], "interface_kind": "aclnn_2stage"},
            "completeness": {"status": "complete", "reasons": [], "form_facts": [
                "local_source_has_no_upstream_commit",
                "local_source_file_set_is_subtree_not_pr_diff"]},
        })

    def test_local_source_passes_without_any_degradation_authorization(self):
        """本地源码是一等输入形态：**不设** OPRUNWAY_ALLOW_DEGRADED_PROVENANCE 也必须过门。"""
        self._rewrite_as_local_source()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPRUNWAY_ALLOW_DEGRADED_PROVENANCE", None)
            result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "READY_WAIT_NPU_TRUST_GATE",
                         result["blocked_reasons"])
        self.assertEqual(result["provenance_degradations"], [],
                         "声明即所得不是降级")
        self.assertEqual(result["provenance_form_facts"],
                         ["local_source_has_no_upstream_commit",
                          "local_source_file_set_is_subtree_not_pr_diff"],
                         "中性形态事实必须机读可取，且与降级分栏记")
        self.assertIsNone(result["bindings"]["pr_head_sha"])
        self.assertEqual(result["bindings"]["declared_source_form"], "local_source")

    def test_undeclared_local_snapshot_is_still_a_degraded_route(self):
        """老事实包（未声明形态）+ 本地快照：仍是降级，没授权就 BLOCKED。"""
        self._rewrite_as_local_source()
        facts = json.load(open(
            os.path.join(self.root, "pr_facts.json"), encoding="utf-8"))
        del facts["declared_source_form"]
        self._write("pr_facts.json", facts)
        source = content_address.read_artifact(
            self.root, "source_facts.json", P._SOURCE_DOMAIN)
        del source["declared_source_form"]
        source["completeness"] = {"status": "snapshot_only",
                                  "reasons": ["pr_provenance_local_snapshot"]}
        content_address.write_artifact(
            self.root, "source_facts.json", P._SOURCE_DOMAIN, source)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPRUNWAY_ALLOW_DEGRADED_PROVENANCE", None)
            result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("OPRUNWAY_ALLOW_DEGRADED_PROVENANCE" in r
                            for r in result["blocked_reasons"]),
                        result["blocked_reasons"])

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
