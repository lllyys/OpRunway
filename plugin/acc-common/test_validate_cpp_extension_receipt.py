"""cpp_extension 独立 build/load receipt 的完整性门单测。"""

import hashlib
import json
import os
import tempfile
import unittest

import validate_acceptance_state as G


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as dst:
        json.dump(value, dst, ensure_ascii=False, sort_keys=True)


class CppExtensionReceiptGateTest(unittest.TestCase):
    def _fixture(self, root):
        work = os.path.join(root, "work")
        artifact_rel = "cpp_extension/oprunway_test.so"
        artifact_path = os.path.join(work, artifact_rel)
        os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
        with open(artifact_path, "wb") as dst:
            dst.write(b"independent-extension")

        caseset = {"op": "X", "cases": [{"id": "x_000"}]}
        manifest = {
            "schema": "oprunway.cpp_extension_manifest",
            "schema_version": 1,
            "namespace": "oprunway_test",
            "spec_sha256": "1" * 64,
            "variants": [{"entrypoint": "invoke_v0"}],
        }
        plan = {
            "schema": "oprunway.cpp_extension_invocation_plan",
            "schema_version": 1,
        }
        _write_json(
            os.path.join(work, "cpp_extension", "extension_manifest.json"),
            manifest)
        _write_json(
            os.path.join(work, "cpp_extension_invocation_plan.json"), plan)
        _write_json(os.path.join(work, "cpp_extension_caseset.json"), caseset)

        receipt = {
            "schema": "oprunway.cpp_extension_receipt",
            "schema_version": 1,
            "status": "VERIFIED",
            "bindings": {
                "caseset_sha256": G._canonical_sha(caseset),
                "manifest_sha256": G._canonical_sha(manifest),
                "invocation_plan_sha256": G._canonical_sha(plan),
                "spec_sha256": manifest["spec_sha256"],
            },
            "artifact": {
                "path": artifact_rel,
                "sha256": G._sha256(artifact_path),
            },
            "load": {
                "success": True,
                "loader": "torch.ops.load_library",
                "namespace": manifest["namespace"],
                "schemas": {"invoke_v0": "oprunway_test::invoke_v0(Tensor x)"},
            },
            "runtime": {
                "torch_version": "2.x",
                "torch_npu_version": "2.x",
                "cann_version": "8.x",
                "soc": "Ascend",
            },
            "vendor": {
                "library_sha256": hashlib.sha256(b"vendor").hexdigest(),
                "symbols_owned": ["aclnnX"],
            },
        }
        envelope = {
            "runner_form": "cpp_extension",
            "cpp_extension_receipt": receipt,
        }
        evidence = [{
            "case_id": "x_000",
            "cpp_extension_receipt_sha256": G._canonical_sha(receipt),
        }]
        return caseset, envelope, evidence, artifact_path

    def test_accepts_fully_bound_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = self._fixture(root)
            errors = []
            G._gate_cpp_extension_receipt(
                root, caseset, envelope, evidence, errors)
            self.assertEqual([], errors)

    def test_rejects_artifact_drift(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, artifact_path = self._fixture(root)
            with open(artifact_path, "ab") as dst:
                dst.write(b"-tampered")
            errors = []
            G._gate_cpp_extension_receipt(
                root, caseset, envelope, evidence, errors)
            self.assertTrue(any("ELF sha256" in error for error in errors))

    def test_rejects_evidence_receipt_drift(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = self._fixture(root)
            evidence[0]["cpp_extension_receipt_sha256"] = "0" * 64
            errors = []
            G._gate_cpp_extension_receipt(
                root, caseset, envelope, evidence, errors)
            self.assertTrue(any("receipt digest" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
