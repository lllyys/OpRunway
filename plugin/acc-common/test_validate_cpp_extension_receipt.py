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

        vendor_sha = hashlib.sha256(b"vendor").hexdigest()
        vendor_path = "/opt/vendor/lib.so"
        build_receipt = {
            "schema": "oprunway.vendor_build_receipt",
            "schema_version": 1,
            "status": "VERIFIED",
            "source": {
                "repo": "https://example.invalid/ops.git",
                "pr_head_sha": "a" * 40,
            },
            "build": {
                "argv": ["bash", "build.sh", "--ops=x"],
                "cwd": "/work/ops",
                "returncode": 0,
            },
            "artifact": {
                "library_path": vendor_path,
                "library_sha256": vendor_sha,
            },
        }
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
                "library_path": vendor_path,
                "library_sha256": vendor_sha,
                "symbols_owned": ["aclnnX"],
                "build_receipt": build_receipt,
                "build_receipt_sha256": G._canonical_sha(build_receipt),
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

    def test_rejects_missing_full_pr_head_build_binding(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = self._fixture(root)
            envelope["cpp_extension_receipt"]["vendor"]["build_receipt"][
                "source"]["pr_head_sha"] = "a" * 7
            evidence[0]["cpp_extension_receipt_sha256"] = G._canonical_sha(
                envelope["cpp_extension_receipt"])
            errors = []
            G._gate_cpp_extension_receipt(
                root, caseset, envelope, evidence, errors)
            self.assertTrue(any("被测来源→构建→安装 ELF" in e for e in errors))


class LocalCheckoutSourceBindingTest(unittest.TestCase):
    """本地来源通路的**信任基石**：build receipt 的 local_root_digest ↔ source_facts 的 root_digest。

    它替代了 PR 通路「build 产物对应哪个 PR head」的绑定。少了这条等值校验，
    vendor `.so` 与被测源码之间就没有机器可核的对应关系。
    """

    DIGEST = "c" * 64

    def _relocalize(self, envelope, evidence, *, root_digest=DIGEST):
        """把 fixture 的 build receipt 从 PR 形态改成本地形态，并重算受影响的摘要。"""
        vendor = envelope["cpp_extension_receipt"]["vendor"]
        br = vendor["build_receipt"]
        br["source"] = {
            "dut_source": "local_checkout",
            "repo": "/local/ops-nn",
            "local_root_digest": root_digest,
        }
        vendor["build_receipt_sha256"] = G._canonical_sha(br)
        evidence[0]["cpp_extension_receipt_sha256"] = G._canonical_sha(
            envelope["cpp_extension_receipt"])

    @staticmethod
    def _write_source_facts(root, *, dut_source="local_checkout", root_digest=DIGEST):
        payload = {"dut_source": dut_source}
        if dut_source == "local_checkout":
            payload["local_checkout"] = {"root_digest": root_digest, "op_subdir": "op"}
        else:
            payload["pr"] = {"head_sha": "a" * 40}
        _write_json(os.path.join(root, "source_facts.json"),
                    {"domain": "oprunway/source-facts/v1", "payload": payload})

    def _run(self, root, caseset, envelope, evidence):
        errors = []
        G._gate_cpp_extension_receipt(root, caseset, envelope, evidence, errors)
        return errors

    def test_matching_local_digest_passes(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self._relocalize(envelope, evidence)
            self._write_source_facts(root)
            self.assertEqual([], self._run(root, caseset, envelope, evidence))

    def test_digest_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self._relocalize(envelope, evidence)
            self._write_source_facts(root, root_digest="d" * 64)   # 与收据不等
            errors = self._run(root, caseset, envelope, evidence)
            self.assertTrue(any("不相等" in e for e in errors), errors)

    def test_local_receipt_without_source_facts_is_blocked(self):
        """⭐ 本地锚的可信度**全部**来自等值校验——没有对照物就等于没绑定。"""
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self._relocalize(envelope, evidence)
            errors = self._run(root, caseset, envelope, evidence)
            self.assertTrue(any("找不到 source_facts.json" in e for e in errors), errors)

    def test_receipt_cannot_disguise_local_source_as_pull_request(self):
        """⭐ 绕过路径：source_facts 说 local，收据说 PR + 随便填 40 位 hex。

        若不先核「两边 dut_source 一致」，校验就会走进 PR 分支，
        local_root_digest 那条等值校验**根本不会执行** → 绑定完全失效。
        """
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            # 收据保持 PR 形态（fixture 默认就是），source_facts 声明 local
            self._write_source_facts(root)
            errors = self._run(root, caseset, envelope, evidence)
            self.assertTrue(any("来源不一致" in e for e in errors), errors)

    def test_pull_request_receipt_without_source_facts_keeps_legacy_behaviour(self):
        """PR 通路不能被这条新校验打断——实测真机报告目录里本来就没有 source_facts.json。"""
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self.assertEqual([], self._run(root, caseset, envelope, evidence))

    def test_source_facts_under_work_dir_is_found(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self._relocalize(envelope, evidence)
            _write_json(os.path.join(root, "work", "source_facts.json"),
                        {"domain": "oprunway/source-facts/v1",
                         "payload": {"dut_source": "local_checkout",
                                     "local_checkout": {"root_digest": self.DIGEST,
                                                        "op_subdir": "op"}}})
            self.assertEqual([], self._run(root, caseset, envelope, evidence))


if __name__ == "__main__":
    unittest.main()
