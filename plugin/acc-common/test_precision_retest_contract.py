"""CP-F 精度重测契约层测试；纯 stdlib/临时文件，不执行验收 compute。"""

import copy
import hashlib
import json
import os
import tempfile
import unittest

import precision_retest_contract as R


SHA_A = "a" * 64
SHA_B = "b" * 64


def _directive(kind="same_policy_rerun", status="confirmed"):
    base = {
        name: {"path": f"/reports/{name}.json", "sha256": SHA_A}
        for name in R.BASE_ARTIFACTS
    }
    value = {
        "schema_version": 1,
        "directive_id": "human-001",
        "directive_status": status,
        "attempt_kind": kind,
        "case_ids": ["case-1", "case-2"],
        "base_artifacts": base,
        "source_identity": {
            "pr_head": "d" * 40,
            "build_receipt_sha256": SHA_B,
            "runner_form": "aclnn_py",
        },
        "human_instruction": "按原标准重新测试两个失败 case",
        "confirmed_by": "lys",
        "confirmed_at": "2026-07-29T23:00:00Z",
        "precision_override": None,
    }
    if kind == "relaxed_rerun":
        value["human_instruction"] = (
            "按 AscendOpTest tolerance=0.001、error_rate=0.005 重测")
        value["precision_override"] = {
            "standard": "ascendoptest_default",
            "tolerance": 0.001,
            "error_rate": 0.005,
        }
    return value


class DirectiveTest(unittest.TestCase):
    def test_accepts_same_policy_and_relaxed(self):
        self.assertEqual(
            R.validate_directive(_directive())["attempt_kind"],
            "same_policy_rerun",
        )
        self.assertEqual(
            R.validate_directive(_directive("relaxed_rerun"))["precision_override"]["error_rate"],
            0.005,
        )

    def test_confirmed_requires_human_identity_and_time(self):
        for field in ("confirmed_by", "confirmed_at"):
            with self.subTest(field=field):
                value = _directive()
                value[field] = ""
                with self.assertRaises(R.RetestContractError):
                    R.validate_directive(value, require_confirmed=True)

    def test_case_ids_must_be_nonempty_unique(self):
        for bad in ([], ["x", "x"], [""]):
            with self.subTest(case_ids=bad):
                value = _directive()
                value["case_ids"] = bad
                with self.assertRaises(R.RetestContractError):
                    R.validate_directive(value)

    def test_replay_and_same_policy_reject_override(self):
        for kind in ("same_policy_rerun", "replay_only"):
            value = _directive(kind)
            value["precision_override"] = {"error_rate": 0.1}
            with self.subTest(kind=kind):
                with self.assertRaises(R.RetestContractError):
                    R.validate_directive(value)

    def test_relaxed_rejects_unknown_and_nonfinite_or_negative_fields(self):
        mutations = (
            {"oracle": "torch"},
            {"error_rate": float("inf")},
            {"error_rate": -0.1},
            {"standard": "made_up"},
            {"error_rate": 0.1},
            {"standard": "torch_allclose", "error_rate": 0.1},
            {"standard": "ascendoptest_default", "rtol": 0.1},
            {"standard": "exact", "tolerance": 0},
            {"standard": "ascendoptest_default"},
        )
        for override in mutations:
            value = _directive("relaxed_rerun")
            value["precision_override"] = override
            with self.subTest(override=override):
                with self.assertRaises(R.RetestContractError):
                    R.validate_directive(value)

    def test_cross_family_requires_complete_numeric_policy(self):
        base = {"op": "X", "precision": {
            "standard": "ascendoptest_default"}}
        value = _directive("relaxed_rerun")
        value["precision_override"] = {
            "standard": "torch_allclose", "atol": 0.005}
        with self.assertRaisesRegex(R.RetestContractError, "完整给出"):
            R.derive_relaxed_spec(base, value)
        value["precision_override"]["rtol"] = 0
        artifact = R.derive_relaxed_spec(base, value)
        self.assertEqual(
            artifact["payload"]["precision"]["acceptance_policy"],
            {"standard": "torch_allclose", "atol": 0.005, "rtol": 0})

    def test_rejects_unknown_top_level_field(self):
        value = _directive()
        value["silent_bypass"] = True
        with self.assertRaises(R.RetestContractError):
            R.validate_directive(value)


class RelaxedSpecTest(unittest.TestCase):
    def test_derives_full_spec_without_mutating_base(self):
        base = {
            "op": "AnyOp",
            "runner_form": "aclnn_py",
            "precision": {
                "oracle": "ascendoptest",
                "standard": "ascendoptest_default",
            },
            "perf": {"baseline": "torch_npu"},
        }
        original = copy.deepcopy(base)
        artifact = R.derive_relaxed_spec(base, _directive("relaxed_rerun"))
        payload = artifact["payload"]
        self.assertEqual(base, original)
        self.assertEqual(payload["precision"]["oracle"], "ascendoptest")
        self.assertEqual(payload["precision"]["acceptance_policy"]["error_rate"], 0.005)
        self.assertEqual(payload["perf"], original["perf"])
        self.assertEqual(payload["precision_retest"]["directive_id"], "human-001")

    def test_same_policy_cannot_derive_relaxed_spec(self):
        with self.assertRaises(R.RetestContractError):
            R.derive_relaxed_spec({"op": "X"}, _directive())


class ArtifactAndAttemptTest(unittest.TestCase):
    def test_fingerprint_requires_exact_file_set_and_hashes_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {}
            for name in R.BASE_ARTIFACTS:
                path = os.path.join(root, f"{name}.json")
                with open(path, "wb") as out:
                    out.write(name.encode())
                paths[name] = path
            got = R.fingerprint_base_artifacts(paths)
            self.assertEqual(
                got["spec"]["sha256"],
                hashlib.sha256(b"spec").hexdigest(),
            )
            bad = dict(paths)
            bad.pop("verdict")
            with self.assertRaises(R.RetestContractError):
                R.fingerprint_base_artifacts(bad)

    def test_allocate_attempt_skips_existing_and_never_reuses(self):
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, "0001"))
            first, first_path = R.allocate_attempt(root)
            second, second_path = R.allocate_attempt(root)
            self.assertEqual((first, second), ("0002", "0003"))
            self.assertTrue(os.path.isdir(first_path))
            self.assertTrue(os.path.isdir(second_path))

    def test_verify_base_artifacts_rejects_drift_and_path_escape(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            value = _directive()
            for name in R.BASE_ARTIFACTS:
                path = os.path.join(root, f"{name}.json")
                with open(path, "wb") as out:
                    out.write(name.encode())
                value["base_artifacts"][name] = {
                    "path": path,
                    "sha256": hashlib.sha256(name.encode()).hexdigest(),
                }
            verified = R.verify_base_artifacts(value, root)
            self.assertEqual(set(verified), set(R.BASE_ARTIFACTS))

            with open(value["base_artifacts"]["evidence"]["path"], "ab") as out:
                out.write(b"tampered")
            with self.assertRaisesRegex(R.RetestContractError, "evidence_sha256_mismatch"):
                R.verify_base_artifacts(value, root)

            escaped = copy.deepcopy(value)
            external = os.path.join(outside, "spec.json")
            with open(external, "wb") as out:
                out.write(b"spec")
            escaped["base_artifacts"]["spec"] = {
                "path": external,
                "sha256": hashlib.sha256(b"spec").hexdigest(),
            }
            with self.assertRaisesRegex(R.RetestContractError, "逃逸"):
                R.verify_base_artifacts(escaped, root)

    def test_verify_base_artifacts_rejects_intermediate_symlink(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, "artifacts")
            os.mkdir(target)
            os.symlink(target, os.path.join(root, "linked"))
            value = _directive()
            for name in R.BASE_ARTIFACTS:
                path = os.path.join(root, "linked", name + ".json")
                with open(path, "wb") as out:
                    out.write(name.encode())
                value["base_artifacts"][name] = {
                    "path": path, "sha256": R.sha256_file(path)}
            with self.assertRaisesRegex(
                    R.RetestContractError, "符号链接路径段"):
                R.verify_base_artifacts(value, root)

    def test_build_case_bindings_hashes_original_input_bytes(self):
        with tempfile.TemporaryDirectory() as work:
            os.makedirs(os.path.join(work, "case-1"))
            os.makedirs(os.path.join(work, "case-2"))
            for cid, payload in (("case-1", b"one"), ("case-2", b"two")):
                with open(os.path.join(work, cid, "x.npy"), "wb") as out:
                    out.write(payload)
                with open(os.path.join(work, cid, "g.npy"), "wb") as out:
                    out.write(b"golden-" + payload)
            caseset = {"cases": [
                {"id": cid, "attrs": {}, "inputs": [
                    {"name": "x", "path": f"{cid}/x.npy",
                     "dtype": "float32", "shape": [1]},
                ], "expected": {"golden_path": f"{cid}/g.npy"}}
                for cid in ("case-1", "case-2")
            ]}
            got = R.build_case_bindings(caseset, work, ["case-2", "case-1"])
            self.assertEqual(list(got), ["case-2", "case-1"])
            self.assertEqual(
                got["case-1"]["input_sha256"]["x"],
                hashlib.sha256(b"one").hexdigest(),
            )
            self.assertEqual(
                got["case-1"]["golden_sha256"]["case-1/g.npy"],
                hashlib.sha256(b"golden-one").hexdigest(),
            )

    def test_build_case_bindings_rejects_missing_case_path_escape_and_symlink(self):
        with tempfile.TemporaryDirectory() as work, tempfile.TemporaryDirectory() as outside:
            external = os.path.join(outside, "x.npy")
            with open(external, "wb") as out:
                out.write(b"x")
            base = {"cases": [{
                "id": "case-1",
                "inputs": [{"name": "x", "path": "../x.npy"}],
            }]}
            with self.assertRaises(R.RetestContractError):
                R.build_case_bindings(base, work, ["case-1"])
            with self.assertRaisesRegex(R.RetestContractError, "不在原 caseset"):
                R.build_case_bindings(base, work, ["missing"])

            os.symlink(external, os.path.join(work, "link.npy"))
            linked = copy.deepcopy(base)
            linked["cases"][0]["inputs"][0]["path"] = "link.npy"
            with self.assertRaises(R.RetestContractError):
                R.build_case_bindings(linked, work, ["case-1"])

    def test_manifest_binds_exact_case_set_and_input_hashes(self):
        cases = {
            cid: {"case_digest": SHA_A, "input_sha256": {"x": SHA_B},
                  "golden_sha256": {f"{cid}/g.npy": SHA_A}}
            for cid in ("case-1", "case-2")
        }
        artifact = R.build_attempt_manifest(
            _directive(), cases, {"soc": "A3", "toolkit": "8.3"},
        )
        self.assertEqual(
            artifact["payload"]["planned_case_ids"],
            ["case-1", "case-2"],
        )
        missing = dict(cases)
        missing.pop("case-2")
        with self.assertRaises(R.RetestContractError):
            R.build_attempt_manifest(
                _directive(), missing, {"soc": "A3", "toolkit": "8.3"})

    def test_directive_id_allocation_is_idempotent_and_locked(self):
        with tempfile.TemporaryDirectory() as root:
            directive = R.make_directive_artifact(_directive())
            cases = {
                cid: {"case_digest": SHA_A,
                      "input_sha256": {"x": SHA_B},
                      "golden_sha256": {f"{cid}/g.npy": SHA_A}}
                for cid in ("case-1", "case-2")
            }
            manifest = R.build_attempt_manifest(
                _directive(), cases, {"soc": "A3", "toolkit": "8.3"})
            first = R._allocate_idempotent_attempt(
                root, directive, manifest, None)
            second = R._allocate_idempotent_attempt(
                root, directive, manifest, None)
            self.assertEqual(first[:2], second[:2])
            self.assertTrue(second[2])
            changed = copy.deepcopy(manifest)
            changed["payload"]["execution_identity"]["soc"] = "other"
            changed["digest"] = R.content_address.content_digest(
                "oprunway/precision-retest-manifest/v1",
                changed["payload"])
            with self.assertRaisesRegex(
                    R.RetestContractError, "不同内容"):
                R._allocate_idempotent_attempt(
                    root, directive, changed, None)
            os.mkdir(os.path.join(root, ".allocation.lock"))
            with self.assertRaisesRegex(
                    R.RetestContractError, "另一 owner"):
                R._allocate_idempotent_attempt(
                    root, directive, manifest, None)

    def test_numeric_attempt_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            os.symlink(outside, os.path.join(root, "0001"))
            directive = R.make_directive_artifact(_directive())
            cases = {
                cid: {"case_digest": SHA_A,
                      "input_sha256": {"x": SHA_B},
                      "golden_sha256": {f"{cid}/g.npy": SHA_A}}
                for cid in ("case-1", "case-2")
            }
            manifest = R.build_attempt_manifest(
                _directive(), cases, {"soc": "A3", "toolkit": "8.3"})
            with self.assertRaisesRegex(
                    R.RetestContractError, "非受控真实目录"):
                R._allocate_idempotent_attempt(
                    root, directive, manifest, None)

    def test_stale_lock_requires_explicit_dead_owner_and_digest(self):
        with tempfile.TemporaryDirectory() as root:
            attempt = os.path.join(root, "0001")
            os.mkdir(attempt)
            lock = os.path.join(attempt, ".execute.lock")
            owner = {
                "status": "running", "pid": 2147483647,
                "operation": "execute_precision_attempt",
                "manifest_digest": SHA_A,
            }
            with open(lock, "w", encoding="utf-8") as out:
                json.dump(owner, out)
            with self.assertRaisesRegex(
                    R.RetestContractError, "operation/digest"):
                R.recover_stale_lock(
                    lock, root, SHA_B, "execute_precision_attempt")
            abandoned = R.recover_stale_lock(
                lock, root, SHA_A, "execute_precision_attempt")
            self.assertTrue(os.path.isfile(abandoned))
            self.assertFalse(os.path.exists(lock))

    def test_completion_receipt_requires_clean_gate_and_all_hashes(self):
        cases = {
            cid: {"case_digest": SHA_A, "input_sha256": {"x": SHA_B},
                  "golden_sha256": {f"{cid}/g.npy": SHA_A}}
            for cid in ("case-1", "case-2")
        }
        manifest = R.build_attempt_manifest(
            _directive(), cases, {"soc": "A3", "toolkit": "8.3"})
        outputs = {
            "evidence_sha256": SHA_A,
            "verdict_sha256": SHA_B,
            "result_sha256": "c" * 64,
        }
        receipt = R.build_completion_receipt(
            manifest, outputs, {"passed": True, "errors": {}})
        self.assertEqual(receipt["payload"]["lifecycle"], "completed")
        with self.assertRaises(R.RetestContractError):
            R.build_completion_receipt(
                manifest, outputs, {"passed": False, "errors": {"task2": ["bad"]}})
        tampered = copy.deepcopy(manifest)
        tampered["payload"]["planned_case_ids"] = ["case-1"]
        with self.assertRaises(R.RetestContractError):
            R.build_completion_receipt(
                tampered, outputs, {"passed": True, "errors": {}})

    def test_attempt_receipt_records_completed_gate_failure_without_pass(self):
        cases = {
            cid: {"case_digest": SHA_A, "input_sha256": {"x": SHA_B},
                  "golden_sha256": {f"{cid}/g.npy": SHA_A}}
            for cid in ("case-1", "case-2")
        }
        manifest = R.build_attempt_manifest(
            _directive(), cases, {"soc": "A3", "toolkit": "8.3"})
        outputs = {
            "evidence_sha256": SHA_A,
            "verdict_sha256": SHA_B,
            "result_sha256": "c" * 64,
        }
        receipt = R.build_attempt_receipt(
            manifest, outputs,
            {"passed": False, "errors": {"task2": ["bad"]}},
            "2026-07-29T23:59:00Z",
        )
        payload = receipt["payload"]
        self.assertEqual(payload["lifecycle"], "completed")
        self.assertFalse(payload["gate"]["passed"])
        self.assertIsNone(payload["acceptance_verdict"])

    def test_attempt_receipt_rejects_gate_contradiction_and_local_time(self):
        cases = {
            cid: {"case_digest": SHA_A, "input_sha256": {"x": SHA_B},
                  "golden_sha256": {f"{cid}/g.npy": SHA_A}}
            for cid in ("case-1", "case-2")
        }
        manifest = R.build_attempt_manifest(
            _directive(), cases, {"soc": "A3", "toolkit": "8.3"})
        outputs = {
            "evidence_sha256": SHA_A,
            "verdict_sha256": SHA_B,
            "result_sha256": "c" * 64,
        }
        with self.assertRaises(R.RetestContractError):
            R.build_attempt_receipt(
                manifest, outputs, {"passed": True, "errors": {"x": ["bad"]}},
                "2026-07-29T23:59:00Z")
        with self.assertRaises(R.RetestContractError):
            R.build_attempt_receipt(
                manifest, outputs, {"passed": True, "errors": {}},
                "2026-07-29T23:59:00-04:00")

    def test_materialize_attempt_writes_preparation_not_verdict(self):
        with tempfile.TemporaryDirectory() as root:
            work = os.path.join(root, "work")
            os.makedirs(os.path.join(work, "case-1"))
            os.makedirs(os.path.join(work, "case-2"))
            for cid in ("case-1", "case-2"):
                with open(os.path.join(work, cid, "x.npy"), "wb") as out:
                    out.write(cid.encode())
                with open(os.path.join(work, cid, "g.npy"), "wb") as out:
                    out.write(("golden-" + cid).encode())
            documents = {
                "spec": {
                    "op": "AnyOp", "runner_form": "aclnn_py",
                    "precision": {"standard": "ascendoptest_default"},
                },
                "caseset": {
                    "op": "AnyOp",
                    "cases": [{
                        "id": cid,
                        "inputs": [{"name": "x", "path": f"{cid}/x.npy",
                                    "shape": [1], "dtype": "float32"}],
                        "expected": {"golden_path": f"{cid}/g.npy"},
                    } for cid in ("case-1", "case-2")],
                },
                "evidence": {
                    "op": "AnyOp",
                    "execution_provenance": {
                        "head_sha": "d" * 40,
                        "soc": "A3",
                        "toolkit_version": "8.3",
                        "build_receipt_sha256": SHA_B,
                        "vendor_elf_sha256": SHA_A,
                        "golden_source_sha256": SHA_B,
                    },
                },
                "verdict": {"overall": {"verdict": "fail"}},
                "acceptance": {"overall": "FAIL"},
            }
            directive = _directive()
            for name, document in documents.items():
                path = os.path.join(root, f"{name}.json")
                with open(path, "w", encoding="utf-8") as out:
                    json.dump(document, out)
                directive["base_artifacts"][name] = {
                    "path": path,
                    "sha256": R.sha256_file(path),
                }
            result = R.materialize_attempt(directive, root, {
                "soc": "A3",
                "toolkit": "8.3",
                "vendor_elf_sha256": SHA_A,
                "golden_source_sha256": SHA_B,
            })
            attempt = result["attempt_dir"]
            self.assertTrue(os.path.isfile(os.path.join(attempt, "directive.json")))
            self.assertTrue(os.path.isfile(os.path.join(attempt, "attempt.manifest.json")))
            self.assertTrue(os.path.isfile(os.path.join(attempt, "preparation.json")))
            self.assertFalse(os.path.exists(os.path.join(attempt, "verdict.json")))
            self.assertFalse(os.path.exists(os.path.join(attempt, "acceptance.json")))

    def test_materialize_attempt_blocks_missing_base_execution_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            work = os.path.join(root, "work")
            os.makedirs(os.path.join(work, "case-1"))
            os.makedirs(os.path.join(work, "case-2"))
            for cid in ("case-1", "case-2"):
                with open(os.path.join(work, cid, "x.npy"), "wb") as out:
                    out.write(cid.encode())
                with open(os.path.join(work, cid, "g.npy"), "wb") as out:
                    out.write(("golden-" + cid).encode())
            documents = {
                "spec": {"op": "AnyOp", "runner_form": "aclnn_py"},
                "caseset": {"op": "AnyOp", "cases": [{
                    "id": cid,
                    "inputs": [{"name": "x", "path": f"{cid}/x.npy"}],
                    "expected": {"golden_path": f"{cid}/g.npy"},
                } for cid in ("case-1", "case-2")]},
                "evidence": {"op": "AnyOp"},
                "verdict": {"overall": {"verdict": "fail"}},
                "acceptance": {"overall": "FAIL"},
            }
            directive = _directive()
            for name, document in documents.items():
                path = os.path.join(root, f"{name}.json")
                with open(path, "w", encoding="utf-8") as out:
                    json.dump(document, out)
                directive["base_artifacts"][name] = {
                    "path": path, "sha256": R.sha256_file(path)}
            with self.assertRaisesRegex(
                    R.RetestContractError, "base_execution_provenance_missing"):
                R.materialize_attempt(directive, root, {
                    "soc": "A3", "toolkit": "8.3",
                    "vendor_elf_sha256": SHA_A,
                    "golden_source_sha256": SHA_B,
                })


if __name__ == "__main__":
    unittest.main()
