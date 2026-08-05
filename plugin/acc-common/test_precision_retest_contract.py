"""CP-F 精度重测契约层测试；纯 stdlib/临时文件，不执行验收 compute。"""

import copy
import hashlib
import json
import os
import tempfile
import unittest
import unittest.mock

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
        # PR 通路刻意省掉 `dut_source` 键：顺带见证「缺席即 pull_request」的向后兼容。
        "source_identity": {
            "repo": "repo",
            "pr_head_sha": "d" * 40,
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

    def test_pr_head_sha_must_be_exactly_40_hex_not_a_digest(self):
        """实测复现过的洞：旧 `^[0-9a-f]{40,64}$` 让 64 位摘要冒充 PR head 直接过。"""
        value = _directive()
        value["source_identity"]["pr_head_sha"] = "b" * 64
        with self.assertRaisesRegex(R.RetestContractError, "40 位 hex"):
            R.validate_directive(value)

    def test_source_identity_requires_repo_and_matching_anchor_field(self):
        for mutate, pattern in (
                (lambda s: s.pop("repo"), "repo"),
                (lambda s: s.pop("pr_head_sha"), "40 位 hex"),
                # PR directive 又塞一个本地锚 → 由 `dut_source` 的**互斥**校验先拦下，
                # 报的是「同时带着另一条通路的锚」而不是键集不等：两套锚齐备时，
                # 任何按字段名直取的下游都能自选来源身份，这比键集多一项更该先说。
                (lambda s: s.update(local_root_digest="c" * 64), "另一条通路的锚"),
                (lambda s: s.update(dut_source="made_up"), "受控词表"),
        ):
            with self.subTest(pattern=pattern):
                value = _directive()
                mutate(value["source_identity"])
                with self.assertRaisesRegex(R.RetestContractError, pattern):
                    R.validate_directive(value)

    def test_local_checkout_directive_needs_64_hex_root_digest(self):
        value = _directive()
        value["source_identity"] = {
            "dut_source": "local_checkout",
            "repo": "repo",
            "local_root_digest": "e" * 64,
            "build_receipt_sha256": SHA_B,
            "runner_form": "cpp_extension",
        }
        self.assertEqual(
            R.validate_directive(value)["source_identity"]["local_root_digest"],
            "e" * 64)
        value["source_identity"]["local_root_digest"] = "e" * 40
        with self.assertRaisesRegex(R.RetestContractError, "64 位 hex"):
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


class ProvenanceAnchorKeyTest(unittest.TestCase):
    """锚字段名 → provenance 键名的查表：登记项照常返回，未登记项 fail-closed。

    `materialize_attempt` 里有两处查表。第二处（基础收据自报的锚）在当前代码里**构造上
    不可达**——`expected_kind` 已保证基础收据与 directive 同通路、锚字段名必然相同，所以
    第一处会先拦下。正因为不可达，它只能在这里直接见证；不能因此把它写回裸下标。
    """

    def test_registered_fields_map_to_first_round_keys(self):
        self.assertEqual(
            R._provenance_anchor_key("pr_head_sha", "where"), "head_sha")
        self.assertEqual(
            R._provenance_anchor_key("local_root_digest", "where"),
            "local_root_digest")

    def test_unregistered_field_raises_contract_error(self):
        with self.assertRaisesRegex(R.RetestContractError, "没有登记"):
            R._provenance_anchor_key(
                "future_anchor", "base cpp_extension vendor build_receipt.source")


ROOT_DIGEST = "7" * 64
ELF_SHA = "e" * 64


class LocalCheckoutMaterializeTest(unittest.TestCase):
    """本地来源通路的 CP-F 冻结：锚是 `local_root_digest`，不是任何 40 位 hex。"""

    def _build(self, root, *, receipt_source=None, facts_digest=ROOT_DIGEST,
               write_source_facts=True):
        work = os.path.join(root, "work")
        for cid in ("case-1", "case-2"):
            os.makedirs(os.path.join(work, cid))
            with open(os.path.join(work, cid, "x.npy"), "wb") as out:
                out.write(cid.encode())
            with open(os.path.join(work, cid, "g.npy"), "wb") as out:
                out.write(("golden-" + cid).encode())
        golden_py = os.path.join(root, "golden.py")
        with open(golden_py, "w", encoding="utf-8") as out:
            out.write("# authorized golden\n")
        spec = {"op": "AnyOp", "runner_form": "cpp_extension",
                "precision": {"standard": "ascendoptest_default"}}
        caseset = {"op": "AnyOp", "cases": [{
            "id": cid,
            "inputs": [{"name": "x", "path": f"{cid}/x.npy",
                        "shape": [1], "dtype": "float32"}],
            "expected": {"golden_path": f"{cid}/g.npy"},
        } for cid in ("case-1", "case-2")]}
        ext_manifest = {"namespace": "oprunway_test",
                        "spec_sha256": R._canonical_sha(spec)}
        plan = {
            "caseset_sha256": R._canonical_sha(caseset),
            "manifest_sha256": R._canonical_sha(ext_manifest),
            "namespace": "oprunway_test",
            "cases": [{"case_id": cid, "entrypoint": "invoke_v0"}
                      for cid in ("case-1", "case-2")],
        }
        build_receipt = {
            "source": receipt_source if receipt_source is not None else {
                "dut_source": "local_checkout",
                "repo": "repo",
                "local_root_digest": ROOT_DIGEST,
            },
            "build": {"argv": ["bash", "build.sh"]},
        }
        receipt = {
            "schema": "oprunway.cpp_extension_receipt",
            "schema_version": 1,
            "status": "VERIFIED",
            "bindings": {
                "caseset_sha256": R._canonical_sha(caseset),
                "manifest_sha256": R._canonical_sha(ext_manifest),
                "invocation_plan_sha256": R._canonical_sha(plan),
                "spec_sha256": R._canonical_sha(spec),
            },
            "vendor": {
                "library_sha256": ELF_SHA,
                "build_receipt": build_receipt,
                "build_receipt_sha256": R._canonical_sha(build_receipt),
            },
            "runtime": {"soc": "A3", "cann_version": "8.3"},
        }
        evidence = {
            "op": "AnyOp",
            "cpp_extension_receipt": receipt,
            "evidence": [{"case_id": cid,
                          "cpp_extension_receipt_sha256": R._canonical_sha(receipt)}
                         for cid in ("case-1", "case-2")],
        }
        os.makedirs(os.path.join(work, "cpp_extension"))
        for relative, document in (
                ("cpp_extension_receipt.json", receipt),
                ("cpp_extension_invocation_plan.json", plan),
                (os.path.join("cpp_extension", "extension_manifest.json"),
                 ext_manifest)):
            with open(os.path.join(work, relative), "w", encoding="utf-8") as out:
                json.dump(document, out)
        if write_source_facts:
            R.content_address.atomic_write_json(
                root, "source_facts.json",
                R.content_address.make_artifact(
                    "oprunway/source-facts/v1",
                    {"dut_source": "local_checkout",
                     "local_checkout": {"root_digest": facts_digest}}))
        directive = _directive()
        directive["source_identity"] = {
            "dut_source": "local_checkout",
            "repo": "repo",
            "local_root_digest": ROOT_DIGEST,
            "build_receipt_sha256": R._canonical_sha(build_receipt),
            "runner_form": "cpp_extension",
        }
        documents = {
            "spec": spec, "caseset": caseset, "evidence": evidence,
            "verdict": {"overall": {"verdict": "fail"}},
            "acceptance": {"overall": "FAIL"},
        }
        for name, document in documents.items():
            path = os.path.join(root, f"{name}.json")
            with open(path, "w", encoding="utf-8") as out:
                json.dump(document, out)
            directive["base_artifacts"][name] = {
                "path": path, "sha256": R.sha256_file(path)}
        identity = {
            "soc": "A3", "toolkit": "8.3",
            "vendor_elf_sha256": ELF_SHA,
            "golden_source_sha256": R.sha256_file(golden_py),
        }
        return directive, identity

    def test_local_anchor_binds_and_freezes_source_facts(self):
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(root)
            result = R.materialize_attempt(directive, root, identity)
            binding = result["manifest"]["payload"]["runner_binding"]
            self.assertEqual(binding["base_source_identity"], {
                "dut_source": "local_checkout",
                "anchor_field": "local_root_digest",
                "anchor_value": ROOT_DIGEST,
            })
            self.assertNotIn("base_pr_head", binding)
            frozen = os.path.join(result["attempt_dir"], "source_facts.json")
            self.assertTrue(os.path.isfile(frozen))
            self.assertEqual(
                result["manifest"]["payload"]["source_facts"]["sha256"],
                R.sha256_file(frozen))

    def test_receipt_claiming_pull_request_with_any_40_hex_is_blocked(self):
        """directive 说 local、基础收据改口说 PR + 任意 40 位 hex → 本地锚校验会整条跳过。"""
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(root, receipt_source={
                "repo": "repo", "pr_head_sha": "a" * 40})
            with self.assertRaisesRegex(
                    R.RetestContractError,
                    "base_vendor_build_source_anchor_invalid"):
                R.materialize_attempt(directive, root, identity)

    def test_source_repo_mismatch_is_blocked(self):
        """人工确认的 `repo` 必须真参与对账——锚相等**不蕴含**仓相同。

        `local_root_digest` 只覆盖 `op_subdir` 子树：fork、vendored 目录、换个仓名重开的
        同一份代码，都能让两个不同的仓在该子树上字节全等。所以 directive 的 `repo` 与首轮
        build receipt 的 `repo` 不等时必须 BLOCKED，否则模块 docstring 宣称的那道门不存在。
        """
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(root, receipt_source={
                "dut_source": "local_checkout", "repo": "some/other-repo",
                "local_root_digest": ROOT_DIGEST})
            with self.assertRaisesRegex(
                    R.RetestContractError, "base_source_repo_mismatch"):
                R.materialize_attempt(directive, root, identity)

    def test_unregistered_anchor_key_blocks_within_contract_not_keyerror(self):
        """受控词表扩了而 `_PROVENANCE_ANCHOR_KEY` 没跟上时，必须仍落在契约内的异常上。

        `cp_f_prepare_attempt.py` 只 `except (OSError, RetestContractError)`；裸下标抛的
        `KeyError` 会穿过去变成裸 traceback，调用方就拿不到约定的
        `[CP-F prepare] BLOCKED: …` 单行机读输出。
        """
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(root)
            with unittest.mock.patch.dict(
                    R._PROVENANCE_ANCHOR_KEY, {}, clear=True):
                with self.assertRaises(R.RetestContractError) as caught:
                    R.materialize_attempt(directive, root, identity)
        self.assertIn("没有登记", str(caught.exception))
        # 逐字复刻 cp_f_prepare_attempt.py 的 except 元组：这条断言才是「机读契约没破」
        # 的实质见证，只断言异常类型不足以说明入口脚本收得住。
        self.assertIsInstance(caught.exception, (OSError, R.RetestContractError))

    def test_local_root_digest_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(root, receipt_source={
                "dut_source": "local_checkout", "repo": "repo",
                "local_root_digest": "9" * 64})
            with self.assertRaisesRegex(
                    R.RetestContractError, "local_root_digest_mismatch"):
                R.materialize_attempt(directive, root, identity)

    def test_missing_or_drifted_source_facts_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(root, write_source_facts=False)
            with self.assertRaisesRegex(
                    R.RetestContractError, "base_source_facts_missing"):
                R.materialize_attempt(directive, root, identity)
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(root, facts_digest="8" * 64)
            with self.assertRaisesRegex(
                    R.RetestContractError, "base_source_facts_anchor_mismatch"):
                R.materialize_attempt(directive, root, identity)

    def test_local_aclnn_py_refuses_to_fall_back_to_head_sha(self):
        """本地通路下 `execution_provenance.head_sha` 是 PR-ref 取源的产物，与本地字节无关。"""
        with tempfile.TemporaryDirectory() as root:
            work = os.path.join(root, "work")
            for cid in ("case-1", "case-2"):
                os.makedirs(os.path.join(work, cid))
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
                "evidence": {"op": "AnyOp", "execution_provenance": {
                    "head_sha": "d" * 40, "soc": "A3", "toolkit_version": "8.3",
                    "build_receipt_sha256": SHA_B,
                    "vendor_elf_sha256": SHA_A,
                    "golden_source_sha256": SHA_B,
                }},
                "verdict": {"overall": {"verdict": "fail"}},
                "acceptance": {"overall": "FAIL"},
            }
            directive = _directive()
            directive["source_identity"] = {
                "dut_source": "local_checkout", "repo": "repo",
                "local_root_digest": ROOT_DIGEST,
                "build_receipt_sha256": SHA_B, "runner_form": "aclnn_py",
            }
            for name, document in documents.items():
                path = os.path.join(root, f"{name}.json")
                with open(path, "w", encoding="utf-8") as out:
                    json.dump(document, out)
                directive["base_artifacts"][name] = {
                    "path": path, "sha256": R.sha256_file(path)}
            with self.assertRaisesRegex(
                    R.RetestContractError,
                    "base_execution_provenance_anchor_missing"):
                R.materialize_attempt(directive, root, {
                    "soc": "A3", "toolkit": "8.3",
                    "vendor_elf_sha256": SHA_A,
                    "golden_source_sha256": SHA_B,
                })


if __name__ == "__main__":
    unittest.main()
