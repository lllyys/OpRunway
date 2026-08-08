#!/usr/bin/env python3
"""CP-C 构建/交付失败必须收口成不可发布的标准 attempt。"""

import copy
import json
import os
import tempfile
import math
import unittest
from unittest import mock

import content_address
import kernel_identity as K
import pre_execution_failure as P
import render_acceptance_markdown as R
import test_validate_cpp_extension_receipt as F
import vendor_build_receipt as V


class PreExecutionFailureContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.source = os.path.join(self.root, "source")
        scope = "experimental/math/floor_mod"
        definition = os.path.join(self.source, scope, "op_host", "floor_mod_def.cpp")
        os.makedirs(os.path.dirname(definition), exist_ok=True)
        with open(definition, "w", encoding="utf-8") as out:
            out.write("OP_ADD(FloorMod);\n")
        header = os.path.join(self.source, scope, "op_host", "op_api", "aclnn.h")
        os.makedirs(os.path.dirname(header), exist_ok=True)
        with open(header, "w", encoding="utf-8") as out:
            out.write("aclnnStatus aclnnRemainderTensorTensor();\n")
        self.digest = V.take_snapshot_digest(self.source, scope)
        self.payload = F.source_facts_payload(
            snapshot_merkle=self.digest["snapshot_subtree_sha256"],
            snapshot_scope=scope)
        self.payload["pr"]["content_anchor"] = copy.deepcopy(
            self.digest["content_anchor"])
        self.payload["derived"].update({
            "op": "Remainder",
            "target_dir": scope,
            "kernel_identity": copy.deepcopy(self.digest["kernel_identity"]),
        })
        self.spec = {
            "op": "Remainder", "runner_form": "cpp_extension",
            "execution": K.spec_execution(self.digest["kernel_identity"]),
        }
        self.facts_path = os.path.join(self.root, "source_facts.json")
        with open(self.facts_path, "w", encoding="utf-8") as out:
            json.dump(content_address.make_artifact(
                "oprunway/source-facts/v1", self.payload), out)
        self.spec_path = os.path.join(self.root, "spec.json")
        with open(self.spec_path, "w", encoding="utf-8") as out:
            json.dump(self.spec, out)
        self.vendor_attempt = P.build_vendor_attempt(
            snapshot_digest=self.digest,
            target_request={
                "requested_soc": "ascend910_93", "selected_op": "floor_mod",
                "expected_op_type": "FloorMod",
                "installed_opp_root": "/work/install/vendors/acme",
                "package_search_root": "/work/build",
                "cmake_cache_path": "/work/source/build/CMakeCache.txt",
            },
            failure_stage="target_closure",
            error_code="OPS_INFO_MISSING",
            error_text="OPS_INFO_MISSING: target ops-info 缺 FloorMod",
            declared_source_form="local_source",
            build_result={
                "argv": ["bash", "build.sh", "--soc=ascend910_93", "--ops=floor_mod"],
                "cwd": self.source, "returncode": 0,
                "returncode_source": "measured",
                "execution": {
                    "started_at": "2026-08-08T00:00:00Z",
                    "ended_at": "2026-08-08T00:00:01Z", "duration_s": 1.25,
                    "library_path": "/work/install/vendors/acme/op_api/lib/libcust.so",
                    "library_before": None,
                    "library_after": {
                        "mtime_ns": 1, "size": 1, "sha256": "a" * 64},
                },
            })

    def tearDown(self):
        self.tmp.cleanup()

    def _resign(self, attempt):
        attempt["attempt_sha256"] = P.attempt_digest(attempt)
        return attempt

    def test_public_api_and_internal_kernel_can_differ_and_finalize_without_task1(self):
        out = os.path.join(self.root, "reports", "attempt")
        result = P.finalize_from_files(
            out_dir=out, spec_path=self.spec_path,
            source_facts_path=self.facts_path,
            vendor_attempt=self.vendor_attempt)
        with open(result["attempt_record"], encoding="utf-8") as src:
            record = json.load(src)
        self.assertEqual(record["op"], "Remainder")
        self.assertEqual(record["execution_identity"]["kernel_op_type"], "FloorMod")
        self.assertIs(record["formal_eligible"], False)
        self.assertIsNone(record["acceptance_verdict"])
        self.assertEqual(record["pre_execution_failure"]["error_code"],
                         "OPS_INFO_MISSING")
        vendor_source = record["diagnostic_sources"]["vendor_build_attempt"]
        self.assertEqual(
            vendor_source["sha256"],
            P._sha256_file(os.path.join(out, vendor_source["path"])))
        self.assertTrue(os.path.isfile(result["detail_report"]))
        self.assertTrue(os.path.isfile(result["terminal_marker"]))
        with open(result["terminal_marker"], encoding="utf-8") as src:
            marker = json.load(src)
        self.assertEqual(set(marker["artifacts"]), {
            "vendor_build_attempt", "detail_report", "attempt_record"})
        for item in marker["artifacts"].values():
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
        for forbidden in ("acceptance.json", "verdict.json", "perf_report.json",
                          "caseset.json", "golden.py", "evidence.json", "验收报告.md"):
            self.assertFalse(os.path.lexists(os.path.join(out, forbidden)), forbidden)

    def test_identity_anchor_and_producer_tampering_are_rejected(self):
        variants = []
        changed_anchor = copy.deepcopy(self.vendor_attempt)
        changed_anchor["source_snapshot"]["content_anchor"]["sha256"] = "b" * 64
        variants.append(("content_anchor", self._resign(changed_anchor)))
        changed_type = copy.deepcopy(self.vendor_attempt)
        changed_type["target_request"]["expected_op_type"] = "Remainder"
        variants.append(("kernel", self._resign(changed_type)))
        changed_producer = copy.deepcopy(self.vendor_attempt)
        changed_producer["producer"]["logic_sha256"] = "c" * 64
        variants.append(("producer", self._resign(changed_producer)))
        changed_provenance = copy.deepcopy(self.vendor_attempt)
        changed_provenance["source_snapshot"]["provenance_kind"] = "gitcode_pr"
        variants.append(("provenance", self._resign(changed_provenance)))
        for label, attempt in variants:
            with self.subTest(label=label), self.assertRaises(P.PreExecutionFailureError):
                P.finalize_from_files(
                    out_dir=os.path.join(self.root, "reports", label),
                    spec_path=self.spec_path, source_facts_path=self.facts_path,
                    vendor_attempt=attempt)
        extra = copy.deepcopy(self.vendor_attempt)
        extra["unknown"] = "not allowed"
        with self.assertRaises(P.PreExecutionFailureError):
            P.validate_vendor_attempt(self._resign(extra))

    def test_source_facts_binding_is_checked_after_attempt_self_validation(self):
        other = copy.deepcopy(self.payload)
        other["pr"]["content_anchor"]["sha256"] = "d" * 64
        other["derived"]["kernel_identity"]["content_anchor"]["sha256"] = "d" * 64
        # 即使外层信封按被篡改 payload 重新签名，也不能冒充 CP-A 原始事实。
        other_path = os.path.join(self.root, "other_source_facts.json")
        with open(other_path, "w", encoding="utf-8") as out:
            json.dump(content_address.make_artifact("oprunway/source-facts/v1", other), out)
        with self.assertRaises(P.PreExecutionFailureError):
            P.finalize_from_files(
                out_dir=os.path.join(self.root, "reports", "tampered-facts"),
                spec_path=self.spec_path, source_facts_path=other_path,
                vendor_attempt=self.vendor_attempt)

    def test_missing_source_facts_envelope_digest_is_rejected(self):
        with self.assertRaisesRegex(P.PreExecutionFailureError, "envelope 摘要"):
            P.finalize(
                out_dir=os.path.join(self.root, "reports", "missing-envelope"),
                spec=self.spec, spec_sha256="a" * 64,
                source_facts=self.payload, source_facts_digest=None,
                vendor_attempt=self.vendor_attempt)

    def test_report_root_parent_symlink_and_trusted_input_overlap_are_rejected(self):
        outside = os.path.join(self.root, "outside")
        os.makedirs(outside)
        linked = os.path.join(self.root, "linked")
        os.symlink(outside, linked)
        with self.assertRaises(P.PreExecutionFailureError):
            P.finalize_from_files(
                out_dir=os.path.join(linked, "report"), spec_path=self.spec_path,
                source_facts_path=self.facts_path,
                vendor_attempt=self.vendor_attempt)
        self.assertEqual(os.listdir(outside), [])
        with self.assertRaises(P.PreExecutionFailureError):
            P.finalize_from_files(
                out_dir=os.path.dirname(self.spec_path), spec_path=self.spec_path,
                source_facts_path=self.facts_path,
                vendor_attempt=self.vendor_attempt)
        self.assertTrue(os.path.isfile(self.spec_path))

    def test_safe_build_facts_validation_is_exact_and_stage_aware(self):
        variants = []
        for label, mutate in (
                ("extra", lambda b: b.update(argv=["secret"])),
                ("argv_sha", lambda b: b.update(argv_sha256="x" * 64)),
                ("bool_count", lambda b: b.update(argument_count=True)),
                ("bool_rc", lambda b: b.update(returncode=False)),
                ("time", lambda b: b["execution"].update(started_at="yesterday")),
                ("duration", lambda b: b["execution"].update(duration_s=math.nan)),
                ("state", lambda b: b["execution"].update(
                    library_after={"mtime_ns": True, "size": -1, "sha256": "x"})),
        ):
            attempt = copy.deepcopy(self.vendor_attempt)
            mutate(attempt["build"])
            variants.append((label, attempt["build"]))
        no_build = copy.deepcopy(self.vendor_attempt)
        no_build["build"] = None
        variants.append(("stage_requires_build", no_build["build"]))
        for label, build in variants:
            with self.subTest(label=label), self.assertRaises(P.PreExecutionFailureError):
                P._validate_safe_build(build, "target_closure", "OPS_INFO_MISSING")

        raw = {
            "argv": ["build-tool"], "cwd": self.source, "returncode": 0,
            "returncode_source": "measured",
            "execution": copy.deepcopy(self.vendor_attempt["build"]["execution"]),
        }
        raw["execution"]["duration_s"] = math.nan
        with self.assertRaises(P.PreExecutionFailureError):
            P._safe_build_facts(raw, "target_closure", "OPS_INFO_MISSING")

        with self.assertRaises(P.PreExecutionFailureError):
            P._validate_safe_build(
                self.vendor_attempt["build"], "not_a_stage", "OPS_INFO_MISSING")
        nonzero_closure = copy.deepcopy(self.vendor_attempt["build"])
        nonzero_closure["returncode"] = 7
        with self.assertRaises(P.PreExecutionFailureError):
            P._validate_safe_build(
                nonzero_closure, "target_closure", "OPS_INFO_MISSING")
        with self.assertRaises(P.PreExecutionFailureError):
            P._validate_safe_build(
                self.vendor_attempt["build"], "build", "BUILD_NONZERO")
        with self.assertRaises(P.PreExecutionFailureError):
            P._validate_safe_build(
                self.vendor_attempt["build"], "receipt_preflight",
                "LIVE_RECEIPT_PREFLIGHT_FAILED")

    def test_marker_write_failure_leaves_only_blocking_orphan_payloads(self):
        real_json = P.acceptance_artifacts._atomic_write_json

        def fail_marker(out_dir, filename, payload):
            if filename == P.acceptance_artifacts.PRE_EXECUTION_TERMINAL_FILE:
                raise OSError("injected marker commit failure")
            return real_json(out_dir, filename, payload)

        out_dir = os.path.join(self.root, "reports", "marker-fault")
        with self.assertRaisesRegex(OSError, "marker commit"), mock.patch.object(
                P.acceptance_artifacts, "_atomic_write_json",
                side_effect=fail_marker):
            P.finalize_from_files(
                out_dir=out_dir,
                spec_path=self.spec_path, source_facts_path=self.facts_path,
                vendor_attempt=self.vendor_attempt)
        self.assertFalse(os.path.lexists(os.path.join(
            out_dir, P.acceptance_artifacts.PRE_EXECUTION_TERMINAL_FILE)))
        self.assertTrue(os.path.isfile(os.path.join(out_dir, P.VENDOR_ATTEMPT_FILE)))
        with self.assertRaises(P.acceptance_artifacts.ArtifactNameConflictError):
            P.acceptance_artifacts.publish_acceptance_json(out_dir, {
                "op": "Remainder",
                "execution_identity": P.kernel_identity.resolve(
                    self.spec, self.payload, require_explicit=True),
                "overall": "PASS", "state": "PASSED", "exit_code": 0,
                "repo_mode": "cpp_extension", "perf_status": "ok",
                "gate": {"passed": True, "errors": {}},
            })

    def test_trusted_base_allows_alias_above_base_but_rejects_alias_below(self):
        real = os.path.join(self.root, "real")
        base = os.path.join(real, "base")
        os.makedirs(base)
        alias = os.path.join(self.root, "alias")
        os.symlink(real, alias)
        trusted_alias_base = os.path.join(alias, "base")
        guard = P.artifact_path_guard.prepare_directory(
            os.path.join(trusted_alias_base, "report"),
            trusted_bases=(trusted_alias_base,))
        self.assertEqual(
            guard["path"], os.path.realpath(os.path.join(base, "report")))
        with self.assertRaises(P.artifact_path_guard.ArtifactPathError):
            P.artifact_path_guard.prepare_directory(
                os.path.join(alias, "untrusted-report"),
                trusted_bases=(self.root,))

    def test_terminal_consumer_recomputes_committed_payload_hashes(self):
        out_dir = os.path.join(self.root, "reports", "manifest-tamper")
        P.finalize_from_files(
            out_dir=out_dir, spec_path=self.spec_path,
            source_facts_path=self.facts_path,
            vendor_attempt=self.vendor_attempt)
        P.validate_terminal_marker(out_dir)
        marker_path = os.path.join(
            out_dir, P.acceptance_artifacts.PRE_EXECUTION_TERMINAL_FILE)
        with open(marker_path, encoding="utf-8") as src:
            marker = json.load(src)
        drifted = copy.deepcopy(marker)
        drifted["binding"]["stage"] = "build"
        with open(marker_path, "w", encoding="utf-8") as out:
            json.dump(drifted, out)
        with self.assertRaisesRegex(P.PreExecutionFailureError, "交叉绑定"):
            P.validate_terminal_marker(out_dir)
        with open(marker_path, "w", encoding="utf-8") as out:
            json.dump(marker, out)
        detail = os.path.join(out_dir, P.DETAIL_REPORT_FILE)
        with open(detail, "a", encoding="utf-8") as out:
            out.write("tampered\n")
        with self.assertRaisesRegex(P.PreExecutionFailureError, "摘要漂移"):
            P.validate_terminal_marker(out_dir)

    def test_terminal_consumer_rejects_resigned_semantic_forgery(self):
        """三件套可重算文件摘要，不代表攻击者可改写受信语义。"""
        for label, mutate in (
                ("execution_identity", lambda marker, record: (
                    marker.__setitem__("execution_identity", {"forged": True}),
                    record.__setitem__("execution_identity", {"forged": True}))),
                ("stage", lambda marker, record: (
                    marker["binding"].__setitem__("stage", "package_resolution"),
                    record["pre_execution_failure"].__setitem__(
                        "stage", "package_resolution"))),
                ("error_code", lambda marker, record: (
                    marker["binding"].__setitem__(
                        "error_code", "PACKAGE_ROOT_NOT_FOUND"),
                    record["pre_execution_failure"].__setitem__(
                        "error_code", "PACKAGE_ROOT_NOT_FOUND"))),
                ("failure_subject", lambda marker, record: (
                    marker["binding"].__setitem__(
                        "failure_subject", "live_receipt_preflight"),
                    record["pre_execution_failure"].__setitem__(
                        "failure_subject", "live_receipt_preflight"))),
                ("record_binding", lambda marker, record:
                    record["pre_execution_failure"].__setitem__(
                        "spec_sha256", "f" * 64)),
        ):
            with self.subTest(label=label):
                out_dir = os.path.join(self.root, "reports", "semantic-" + label)
                P.finalize_from_files(
                    out_dir=out_dir, spec_path=self.spec_path,
                    source_facts_path=self.facts_path,
                    vendor_attempt=self.vendor_attempt)
                marker_path = os.path.join(
                    out_dir, P.acceptance_artifacts.PRE_EXECUTION_TERMINAL_FILE)
                attempt_path = os.path.join(
                    out_dir, P.acceptance_artifacts.ATTEMPT_RECORD_FILE)
                with open(marker_path, encoding="utf-8") as src:
                    marker = json.load(src)
                with open(attempt_path, encoding="utf-8") as src:
                    record = json.load(src)
                mutate(marker, record)
                with open(attempt_path, "w", encoding="utf-8") as out:
                    json.dump(record, out)
                marker["artifacts"]["attempt_record"]["sha256"] = P._sha256_file(
                    attempt_path)
                with open(marker_path, "w", encoding="utf-8") as out:
                    json.dump(marker, out)
                with self.assertRaisesRegex(
                        P.PreExecutionFailureError, "交叉绑定|execution identity"):
                    P.validate_terminal_marker(out_dir)

    def test_renderer_cannot_promote_an_attempt_to_a_formal_report(self):
        out = os.path.join(self.root, "reports", "render")
        P.finalize_from_files(
            out_dir=out, spec_path=self.spec_path,
            source_facts_path=self.facts_path,
            vendor_attempt=self.vendor_attempt)
        # 即使并发/人工塞回一份伪 acceptance，renderer 也必须先认 durable marker。
        with open(os.path.join(out, "acceptance.json"), "w", encoding="utf-8") as fake:
            json.dump({"overall": "PASS"}, fake)
        with self.assertRaisesRegex(RuntimeError, "pre-execution terminal"):
            R.write_report(out)
        self.assertFalse(os.path.lexists(os.path.join(out, "验收报告.md")))

    def test_failure_code_classifier_is_stable_across_controlled_stages(self):
        cases = (
            ("build", "build returncode=7（实测）", "BUILD_NONZERO"),
            ("build", "构建结束后 vendor ELF 仍不存在", "ELF_ABSENT"),
            ("build", "vendor ELF 在这次构建窗口内一个字节都没变", "ELF_UNCHANGED"),
            ("package_resolution", "PACKAGE_ROOT_NOT_FOUND: zero", "PACKAGE_ROOT_NOT_FOUND"),
            ("package_resolution", "PACKAGE_ROOT_AMBIGUOUS: many", "PACKAGE_ROOT_AMBIGUOUS"),
            ("target_closure", "OPS_INFO_MISSING: no op", "OPS_INFO_MISSING"),
            ("target_closure", "TARGET_SOC_MISMATCH: cache", "TARGET_SOC_MISMATCH"),
            ("target_closure", "TARGET_OP_MISMATCH: cache", "TARGET_OP_MISMATCH"),
            ("source_post_build", "这次 build 把被测子树改掉了", "SOURCE_SUBTREE_DRIFT"),
        )
        for stage, text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(P.classify_failure(stage, text), expected)


if __name__ == "__main__":
    unittest.main()
