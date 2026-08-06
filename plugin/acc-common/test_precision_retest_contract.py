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
#: 两条通路各自的锚 —— 长度与形态都由 `source_provenance` 词表决定，测试不复用同一段 hex
#: 冒充另一条通路（那正是 `_source_identity_record` 整块比要挡的东西）。
PR_HEAD = "d" * 40
SUBTREE_MERKLE = "7" * 64
WHOLE_MERKLE = "6" * 64
#: 子树 merkle 的覆盖范围。**空串也是合法显式值**（= 仓根），所以这里刻意用一个非空值，
#: 免得「忘了传 scope」和「scope 就是仓根」在断言里长得一样。
SCOPE = "experimental/index/median"


def _vendor_build_receipt(*, local, repo="repo", anchor=None, scope=None):
    """一份能过 `vendor_build_receipt.summarize()` 的收据。

    CP-F 迁到 `source_provenance` 之后**不再自己解释** `receipt["source"]` 的原始字段，
    只消费归一化摘要——所以测试 fixture 必须是一份**完整合法**的 vendor build receipt
    （信封 + 版本/`provenance_kind` 成对 + 按通路分流的锚 + build argv/cwd/实测 returncode），
    不能再像旧 `dut_source` 时代那样只拼一个 `{"source": …, "build": …}` 的残片。
    """
    build = {"argv": ["bash", "build.sh"], "cwd": "/src",
             "returncode": 0, "returncode_source": "measured"}
    if not local:
        # schema_version=1（历史档）：语义恒为绑了 PR head 的 gitcode PR。
        return {"schema": "oprunway.vendor_build_receipt", "schema_version": 1,
                "status": "VERIFIED",
                "source": {"repo": repo, "pr_head_sha": anchor or PR_HEAD},
                "build": build}
    return {"schema": "oprunway.vendor_build_receipt", "schema_version": 2,
            "status": "VERIFIED",
            "source": {"provenance_kind": "local_snapshot",
                       "declared_source_form": "local_source",
                       "pr_head_sha": None, "repo": repo,
                       "snapshot_subtree_scope": SCOPE if scope is None else scope,
                       "snapshot_sha256": WHOLE_MERKLE,
                       "snapshot_subtree_sha256": anchor or SUBTREE_MERKLE},
            "build": build, "degradations": []}


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
        # PR 通路刻意省掉 `provenance_kind` 键：顺带见证「缺席即 gitcode_pr」这条默认。
        # 它不是兜底猜测——本地 directive 漏写该键会带着 snapshot 锚撞进 PR 档，
        # 被 `validate_directive` 的键集严格相等校验当场拒（见下面那条本地用例）。
        "source_identity": {
            "repo": "repo",
            "pr_head_sha": PR_HEAD,
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
                # PR directive 又塞一个本地锚 → 由**互斥**校验先拦下，报的是「同时带着
                # 另一条通路的锚」而不是键集不等：两套锚齐备时，任何按字段名直取的下游
                # 都能自选来源身份，这比键集多一项更该先说。
                (lambda s: s.update(snapshot_subtree_sha256="c" * 64), "另一条通路的锚"),
                (lambda s: s.update(provenance_kind="made_up"), "受控词表"),
                # PR 档没有「范围」这个概念；带了它就是混装本地快照的字段。
                (lambda s: s.update(snapshot_subtree_scope="x"), "不得带"),
                # `repo` 会被逐字带进 manifest 与人读报告，带凭据即撞仓规 §2。
                # 产出方 `vendor_build_receipt.py` **不做**这道检查，读侧必须拦。
                (lambda s: s.update(repo="https://u:tok@gitcode.com/x.git"),
                 "用户凭据"),
        ):
            with self.subTest(pattern=pattern):
                value = _directive()
                mutate(value["source_identity"])
                with self.assertRaisesRegex(R.RetestContractError, pattern):
                    R.validate_directive(value)

    def test_credential_bearing_repo_is_refused_without_echoing_the_token(self):
        """⭐ 报错本身不得再泄漏一次：终端与 CI 日志都会留存。"""
        value = _directive()
        value["source_identity"]["repo"] = "https://u:s3cr3t-token@gitcode.com/x.git"
        with self.assertRaises(R.RetestContractError) as caught:
            R.validate_directive(value)
        self.assertNotIn("s3cr3t-token", str(caught.exception))

    def test_local_snapshot_directive_needs_64_hex_subtree_merkle_plus_scope(self):
        """本地档的锚是**子树 merkle + 覆盖范围**，两样缺一不可。

        ⭐ scope 是本轮从 `dut_source` 迁到 `source_provenance` 时新增的**载重**字段，
        不是记账字段：同一棵树按「整仓」和按「算子子树」摘出来是两个值，范围对不上的两个
        merkle 不可比（对上了是巧合，对不上也说不清是改了字节还是换了范围）。
        旧 `local_root_digest` 压根没有这一维，所以「只改名字」会留下一道假门。
        """
        value = _directive()
        value["source_identity"] = {
            "provenance_kind": "local_snapshot",
            "repo": "repo",
            "snapshot_subtree_sha256": SUBTREE_MERKLE,
            "snapshot_subtree_scope": SCOPE,
            "build_receipt_sha256": SHA_B,
            "runner_form": "cpp_extension",
        }
        checked = R.validate_directive(value)["source_identity"]
        self.assertEqual(checked["snapshot_subtree_sha256"], SUBTREE_MERKLE)
        self.assertEqual(checked["snapshot_subtree_scope"], SCOPE)
        # 空串 = 仓根，属合法显式值。
        value["source_identity"]["snapshot_subtree_scope"] = ""
        self.assertEqual(
            R.validate_directive(value)["source_identity"]["snapshot_subtree_scope"],
            "")
        # 少了 scope → 当场拒，且报因是「没有范围就无法对账」而**不是**「默认整仓」。
        # 归因要能直接指向下一步动作：补 scope，不是去查字节。
        value["source_identity"].pop("snapshot_subtree_scope")
        with self.assertRaisesRegex(R.RetestContractError, "须为字符串"):
            R.validate_directive(value)
        # 键集校验也确实把 scope 列进必填集（换一条路径见证同一件事：
        # 多写一个未知键同样拒，免得「键集里到底有没有 scope」只靠上面那条间接推断）。
        value["source_identity"]["snapshot_subtree_scope"] = SCOPE
        value["source_identity"]["silent_extra"] = "x"
        with self.assertRaisesRegex(R.RetestContractError, "键须严格等于"):
            R.validate_directive(value)
        value["source_identity"].pop("silent_extra")
        # 40 位 commit SHA 不是本地快照锚 —— 长度判据跟着新锚走。
        value["source_identity"]["snapshot_subtree_sha256"] = "e" * 40
        with self.assertRaisesRegex(R.RetestContractError, "64 位小写 hex"):
            R.validate_directive(value)

    def test_local_directive_forgetting_provenance_kind_is_refused(self):
        """「缺席即 gitcode_pr」这条默认**不构成放行路径**：漏写就撞键集校验。"""
        value = _directive()
        value["source_identity"] = {
            "repo": "repo",
            "snapshot_subtree_sha256": SUBTREE_MERKLE,
            "snapshot_subtree_scope": SCOPE,
            "build_receipt_sha256": SHA_B,
            "runner_form": "cpp_extension",
        }
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
            R._provenance_anchor_key("snapshot_subtree_sha256", "where"),
            "snapshot_subtree_sha256")
        # 受控词表与这张表必须逐字同集：漏登记一条就是「新通路悄悄拿旧键去对账」。
        self.assertEqual(
            set(R.SOURCE_ANCHOR_FIELD.values()), set(R._PROVENANCE_ANCHOR_KEY))

    def test_unregistered_field_raises_contract_error(self):
        with self.assertRaisesRegex(R.RetestContractError, "没有登记"):
            R._provenance_anchor_key(
                "future_anchor", "base cpp_extension vendor build_receipt.source")


ELF_SHA = "e" * 64


class LocalSnapshotMaterializeTest(unittest.TestCase):
    """本地来源通路的 CP-F 冻结：锚是 `snapshot_subtree_sha256` **加 scope**，不是任何 40 位 hex。

    ⚠ 本类原名 `LocalCheckoutMaterializeTest`，钉的是已被合并裁定删除的
      `dut_source` / `local_root_digest` 那套判别式。逐条改写到 `source_provenance`
      的等价断言上（**一条都没丢**），并补了 ours 没有的 `snapshot_subtree_scope`
      （范围）维——旧锚只有一个子树摘要、没有范围，所以只改名字会留下一道假门。
    """

    def _build(self, root, *, build_receipt=None, facts_digest=SUBTREE_MERKLE,
               facts_scope=SCOPE, directive_scope=SCOPE,
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
        if build_receipt is None:
            build_receipt = _vendor_build_receipt(local=True)
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
            # intake 侧的字段名与收据侧**不同名**（intake 只产一个 merkle，范围由
            # `--target-dir` 决定；收据产整树 + 子树两个），别按同名比。
            R.content_address.atomic_write_json(
                root, "source_facts.json",
                R.content_address.make_artifact(
                    "oprunway/source-facts/v1",
                    {"declared_source_form": "local_source",
                     "pr": {"provenance_kind": "local_snapshot",
                            "head_sha": None,
                            "snapshot_merkle_sha256": facts_digest,
                            "snapshot_scope": facts_scope}}))
        directive = _directive()
        directive["source_identity"] = {
            "provenance_kind": "local_snapshot",
            "repo": "repo",
            "snapshot_subtree_sha256": SUBTREE_MERKLE,
            "snapshot_subtree_scope": directive_scope,
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
                "provenance_kind": "local_snapshot",
                "anchor_field": "snapshot_subtree_sha256",
                "anchor_value": SUBTREE_MERKLE,
                "snapshot_subtree_scope": SCOPE,
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
            directive, identity = self._build(
                root, build_receipt=_vendor_build_receipt(
                    local=False, anchor="a" * 40))
            with self.assertRaisesRegex(
                    R.RetestContractError,
                    "base_vendor_build_source_anchor_invalid"):
                R.materialize_attempt(directive, root, identity)

    def test_source_repo_mismatch_is_blocked(self):
        """人工确认的 `repo` 必须真参与对账——锚相等**不蕴含**仓相同。

        `snapshot_subtree_sha256` 只覆盖算子子树：fork、vendored 目录、换个仓名重开的
        同一份代码，都能让两个不同的仓在该子树上字节全等。所以 directive 的 `repo` 与首轮
        build receipt 的 `repo` 不等时必须 BLOCKED，否则模块 docstring 宣称的那道门不存在。
        """
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(
                root, build_receipt=_vendor_build_receipt(
                    local=True, repo="some/other-repo"))
            with self.assertRaisesRegex(
                    R.RetestContractError, "base_source_repo_mismatch"):
                R.materialize_attempt(directive, root, identity)

    def test_receipt_scope_drift_is_blocked_even_when_the_merkle_matches(self):
        """⭐ 只比 merkle 值等于没比：范围不同的两个 merkle 本来就不可比。

        这一维在旧 `dut_source` 的 `local_root_digest` 下**根本不存在**，是本轮迁移新增的
        载重校验。收据宣称摘的是整仓、directive 说的是算子子树，两个值即便碰巧相等也不成立。
        """
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(
                root, build_receipt=_vendor_build_receipt(local=True, scope=""))
            with self.assertRaisesRegex(
                    R.RetestContractError, "base_source_identity_mismatch"):
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

    def test_subtree_merkle_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(
                root, build_receipt=_vendor_build_receipt(
                    local=True, anchor="9" * 64))
            with self.assertRaisesRegex(
                    R.RetestContractError, "base_source_identity_mismatch"):
                R.materialize_attempt(directive, root, identity)

    def test_missing_base_golden_source_is_blocked_with_an_actionable_reason(self):
        """base spec 同目录没有 `golden.py` → 必须报 `base_golden_source_missing`。

        ⭐ 归因错就等于下一步动作错。裸调 `sha256_file` 只会报「待绑定工件须为普通文件且非
        符号链接」，读的人会去查权限/软链；真实原因是**这轮验收产物根本不带自证材料**
        （2026-08-05 起由 `run_workflow` 在验收 `--out` 里 staging `spec.json` / `golden.py`，
        老产物没有）。正确动作是重跑一轮完整验收，不是手工拷一份 golden 凑数。
        """
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(root)
            os.remove(os.path.join(root, "golden.py"))
            with self.assertRaisesRegex(
                    R.RetestContractError, "base_golden_source_missing"):
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

    def test_source_facts_scope_mismatch_is_blocked_before_the_merkle(self):
        """⭐ scope 先于 merkle 比：范围对不上时两个 merkle 不可比，报因必须是 scope。

        归因错就等于下一步动作错——报 `anchor_mismatch` 会让人去查字节，真实原因是
        `fetch_source --target-dir` 与收据的 `--subtree-scope` 指的不是同一段子树。
        """
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(root, facts_scope="other/dir")
            with self.assertRaisesRegex(
                    R.RetestContractError, "base_source_facts_scope_mismatch"):
                R.materialize_attempt(directive, root, identity)

    def test_source_facts_claiming_the_other_channel_is_named_as_impersonation(self):
        """收据说本地、事实包说 PR → 必须报「来源身份被伪装」，不是普通的锚漂移。"""
        with tempfile.TemporaryDirectory() as root:
            directive, identity = self._build(root, write_source_facts=False)
            R.content_address.atomic_write_json(
                root, "source_facts.json",
                R.content_address.make_artifact(
                    "oprunway/source-facts/v1",
                    {"pr": {"provenance_kind": "gitcode_pr",
                            "head_sha": PR_HEAD,
                            "snapshot_merkle_sha256": None,
                            "snapshot_scope": None}}))
            with self.assertRaisesRegex(
                    R.RetestContractError,
                    "base_source_facts_provenance_kind_mismatch"):
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
                "provenance_kind": "local_snapshot", "repo": "repo",
                "snapshot_subtree_sha256": SUBTREE_MERKLE,
                "snapshot_subtree_scope": SCOPE,
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
