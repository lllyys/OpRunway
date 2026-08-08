import copy
import json
import os
import tempfile
import unittest
from unittest import mock

import content_address
import fetch_source
import source_provenance
import validate_acceptance_state
import validate_preparation_state
import vendor_build_receipt
import precision_retest_contract


class CallerTrustedInputContractTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name
        self.scope = "experimental/math/demo"
        target = os.path.join(self.root, *self.scope.split("/"))
        os.makedirs(os.path.join(target, "op_host"))
        os.makedirs(os.path.join(target, "examples"))
        self.header = os.path.join(target, "op_host", "aclnn_demo.h")
        with open(self.header, "w", encoding="utf-8") as dst:
            dst.write("aclnnDemoGetWorkspaceSize\naclnnDemo\n")
        self.op_def = os.path.join(target, "op_host", "demo_def.cpp")
        with open(self.op_def, "w", encoding="utf-8") as dst:
            dst.write("OP_ADD(FixtureOp);\n")
        with open(os.path.join(target, "examples", "test_aclnn_demo.cpp"),
                  "w", encoding="utf-8") as dst:
            dst.write("aclnnDemoGetWorkspaceSize(); aclnnDemo();\n")
        self.task = os.path.join(self.root, "task.md")
        with open(self.task, "w", encoding="utf-8") as dst:
            dst.write("# caller supplied task\naclnnDemo\n")

    def tearDown(self):
        self.td.cleanup()

    def _facts(self):
        raw = fetch_source.scan_pr_snapshot(
            self.root, self.root, target_dir=self.scope)
        with open(raw, encoding="utf-8") as src:
            pr = json.load(src)
        return pr, fetch_source.build_source_facts(self.task, pr)

    def _binding_receipt(self, source):
        """由同一真实 target scope 重扫 build 身份，供 current gate 对账。"""
        digest = vendor_build_receipt.take_snapshot_digest(
            self.root, self.scope)
        self.assertEqual(
            source["pr"]["content_anchor"], digest["content_anchor"])
        self.assertEqual(
            source["derived"]["kernel_identity"], digest["kernel_identity"])
        return {
            "schema": vendor_build_receipt.SCHEMA,
            "schema_version": vendor_build_receipt.SCHEMA_VERSION,
            "status": "VERIFIED",
            "degradations": [],
            "source": {
                "provenance_kind": vendor_build_receipt.PROVENANCE_LOCAL_SNAPSHOT,
                "repo": self.root,
                "pr_head_sha": None,
                "snapshot_subtree_scope": self.scope,
                "snapshot_sha256": digest["snapshot_sha256"],
                "snapshot_subtree_sha256": digest["snapshot_subtree_sha256"],
                "content_anchor": source["pr"]["content_anchor"],
            },
            "build": {
                "argv": ["bash", "build.sh"],
                "cwd": self.root,
                "returncode": 0,
                "returncode_source": vendor_build_receipt.RETURNCODE_SOURCE_MEASURED,
                "source_snapshot_digest": digest,
            },
            vendor_build_receipt.TARGET_KERNEL_DELIVERY_KEY: {
                "request": {"expected_op_type": "FixtureOp"},
            },
        }

    def test_transport_identity_is_advisory_but_content_anchor_is_hard(self):
        pr, source = self._facts()
        self.assertEqual(source["contract_version"], 2)
        self.assertEqual(source["completeness"]["status"], "complete")
        self.assertEqual(source["input_association"]["policy"],
                         "caller_trusted_pair_v1")
        drifted_transport = copy.deepcopy(pr)
        drifted_transport.update({
            "provenance_kind": "gitcode_pr", "head_sha": "a" * 40,
            "declared_source_form": "unknown-transport-form"})
        bindings, degradations = source_provenance.bind(source, drifted_transport)
        self.assertEqual(bindings["content_anchor"], source["pr"]["content_anchor"])
        self.assertEqual(degradations, [])
        drifted_transport["content_anchor"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(source_provenance.ProvenanceError, "content_anchor"):
            source_provenance.bind(source, drifted_transport)

    def test_preparation_accepts_new_contract_and_rejects_anchor_mutation(self):
        _pr, source = self._facts()
        validate_preparation_state._validate_source_payload(source)
        bad = copy.deepcopy(source)
        bad["pr"]["content_anchor"]["sha256"] = "bad"
        with self.assertRaises(content_address.ContentAddressError):
            validate_preparation_state._validate_source_payload(bad)

    def test_acceptance_gate_ignores_transport_kind_and_rejects_content_drift(self):
        _pr, source = self._facts()
        build_receipt = self._binding_receipt(source)
        summary = vendor_build_receipt.summarize(build_receipt)
        with mock.patch.object(
                validate_acceptance_state.source_facts_lookup,
                "find_source_facts", return_value=source):
            errs = []
            validate_acceptance_state._gate_build_receipt_source_binding(
                self.root, summary, errs, build_receipt=build_receipt)
            self.assertEqual(errs, [])
            bad = copy.deepcopy(summary)
            bad["content_anchor"]["sha256"] = "f" * 64
            validate_acceptance_state._gate_build_receipt_source_binding(
                self.root, bad, errs, build_receipt=build_receipt)
            self.assertTrue(any("content_anchor" in row for row in errs))

    def test_vendor_fresh_receipt_requires_snapshot_even_for_online_transport(self):
        digest = vendor_build_receipt.take_snapshot_digest(self.root, self.scope)
        self.assertEqual(digest["content_anchor"],
                         fetch_source._content_anchor_from_snapshot(
                             self.root, self.scope,
                             fetch_source._walk_snapshot(self.root, self.scope)))
        with self.assertRaisesRegex(vendor_build_receipt.VendorBuildReceiptError,
                                    "必须给 snapshot_digest"):
            vendor_build_receipt.produce_receipt(
                declared_source_form="git_pr", build_result={},
                repo="transport/repo", pr_head_sha="a" * 40)
        for field, value in (("scope", "elsewhere"), ("sha256", "0" * 64),
                             ("file_count", digest["content_anchor"]["file_count"] + 1)):
            bad = copy.deepcopy(digest)
            bad["content_anchor"][field] = value
            with self.assertRaisesRegex(vendor_build_receipt.VendorBuildReceiptError,
                                        "content_anchor"):
                vendor_build_receipt._validate_snapshot_digest(bad)

    def test_fresh_outer_receipt_rejects_legacy_before_artifact_reads(self):
        envelope = {"runner_form": "cpp_extension",
                    "cpp_extension_receipt": {
                        "schema": "oprunway.cpp_extension_receipt",
                        "schema_version": 1, "status": "VERIFIED"}}
        errs = []
        validate_acceptance_state._gate_cpp_extension_receipt(
            self.root, {}, envelope, [], errs)
        self.assertTrue(any("只接受 VERIFIED current" in row for row in errs), errs)

    def test_cp_f_identity_uses_content_not_locator(self):
        _pr, source = self._facts()
        anchor = source["pr"]["content_anchor"]
        base = {"content_anchor": anchor, "build_receipt_sha256": "a" * 64,
                "runner_form": "cpp_extension",
                "transport": {"repo": "fork/a", "head_sha": "b" * 40}}
        self.assertEqual(
            precision_retest_contract.validate_source_identity(base)[2], anchor)
        changed_locator = copy.deepcopy(base)
        changed_locator["transport"] = {"repo": "other/b", "head_sha": None}
        self.assertEqual(
            precision_retest_contract.validate_source_identity(changed_locator)[2], anchor)
        for key, value in (("scope", None), ("sha256", "bad"),
                           ("file_count", 0)):
            bad = copy.deepcopy(base)
            bad["content_anchor"][key] = value
            with self.assertRaises(precision_retest_contract.RetestContractError):
                precision_retest_contract.validate_source_identity(bad)

    def test_malformed_new_marker_never_falls_back_to_legacy(self):
        pr, source = self._facts()
        for bad in (
                dict(source, contract_version=1),
                dict(source, input_association={"policy": "caller_trusted_pair_v1"})):
            with self.assertRaises(source_provenance.ProvenanceError):
                source_provenance.bind(bad, pr)


if __name__ == "__main__":
    unittest.main()
