#!/usr/bin/env python3
"""`vendor_build_receipt` 的校验分流 + **确定性生产路径**单测（纯本地，不碰 NPU）。

重点在两件事：

1. **降级挂账的新口径**：本地源码是一等输入形态 → 显式声明 `local_source` 的收据必须**无降级**；
   声明 `git_pr` 却只拿到快照、以及**未声明形态的老收据**，仍必须挂 `pr_head_unbound`
   （老现场一条不改照样过门，新口径只对显式声明者生效）；
2. **merkle 必须在 build 之前算**：生产路径只接受 build 前落下的摘要凭据，自己不去摘源码树；
   build 往树里写了产物之后，收据里的 source merkle 仍是 build 前那个值，
   而 `build.tree_state_at_emit` 如实记下「树已经变了」。
"""
import json
import os
import tempfile
import unittest

import fetch_source as fs
import vendor_build_receipt as V


_OP = "gaussian_blur"
_TREE = {
    _OP + "/op_host/op_api/aclnn_gaussian_blur.h": "aclnnStatus aclnnGaussianBlur();\n",
    _OP + "/op_host/gaussian_blur_def.cpp": "// def\n",
    "other_op/op_host/other_def.cpp": "// 不属于本轮子树\n",
}


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.root = os.path.join(self.d, "src")
        for rel, body in _TREE.items():
            full = os.path.join(self.root, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as out:
                out.write(body)
        self.elf = os.path.join(self.d, "libcust_opapi.so")
        with open(self.elf, "wb") as out:
            out.write(b"vendor-elf")

    def tearDown(self):
        self.tmp.cleanup()

    def _produce(self, form=V.FORM_LOCAL_SOURCE, digest=None, **over):
        kwargs = dict(
            declared_source_form=form,
            library_path=self.elf,
            build_argv=["bash", "build.sh", "--op=" + _OP],
            build_cwd=self.root,
            returncode=0,
            snapshot_digest=digest if digest is not None
            else V.take_snapshot_digest(self.root, _OP),
        )
        kwargs.update(over)
        return V.produce_receipt(**kwargs)


class SnapshotDigestTest(_Fixture):
    def test_digest_uses_the_intake_algorithm_verbatim(self):
        """两个 merkle 必须与 intake 侧**同一份算法**算出来，否则两端永远对不上账。"""
        digest = V.take_snapshot_digest(self.root, _OP)
        want_subtree = fs._snapshot_merkle(self.root, fs._walk_snapshot(self.root, _OP))
        want_whole = fs._snapshot_merkle(self.root, fs._walk_snapshot(self.root, ""))
        self.assertEqual(digest["snapshot_subtree_sha256"], want_subtree)
        self.assertEqual(digest["snapshot_sha256"], want_whole)
        self.assertNotEqual(want_subtree, want_whole, "子树与整树本就不同，别混用")
        self.assertEqual(digest["taken_stage"], "pre_build")
        self.assertEqual(digest["subtree_file_count"], 2)

    def test_subtree_digest_matches_what_intake_records(self):
        """与 `scan_pr_snapshot --target-dir` 记的 `snapshot_merkle_sha256` 逐字相等。"""
        out = os.path.join(self.d, "cp-a")
        os.makedirs(out, exist_ok=True)
        with open(fs.scan_pr_snapshot(self.root, out, target_dir=_OP),
                  encoding="utf-8") as src:
            facts = json.load(src)
        digest = V.take_snapshot_digest(self.root, _OP)
        self.assertEqual(facts["snapshot_merkle_sha256"],
                         digest["snapshot_subtree_sha256"])
        self.assertEqual(facts["snapshot_scope"], digest["subtree_scope"])

    def test_missing_scope_or_root_fails_loud(self):
        with self.assertRaises(V.VendorBuildReceiptError):
            V.take_snapshot_digest(os.path.join(self.d, "nope"), _OP)
        with self.assertRaises(V.VendorBuildReceiptError):
            V.take_snapshot_digest(self.root, "not_here")

    def test_algorithm_drift_invalidates_a_digest(self):
        digest = V.take_snapshot_digest(self.root, _OP)
        digest["algorithm"]["logic_sha256"] = "0" * 64
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "算法"):
            self._produce(digest=digest)


class ProduceReceiptTest(_Fixture):
    def test_local_source_receipt_passes_validate_with_no_degradation(self):
        receipt = self._produce()
        summary = V.validate(
            receipt, library_path=os.path.realpath(self.elf),
            library_sha256=V._sha256_file(self.elf), normalize_path=True)
        self.assertEqual(summary["degradations"], [],
                         "本地源码是一等输入形态，声明即所得就不该挂降级账")
        self.assertIsNone(summary["pr_head_sha"], "null 是这条形态的正确值")
        self.assertEqual(summary["snapshot_subtree_scope"], _OP)
        self.assertEqual(receipt["schema_version"], V.SCHEMA_VERSION)
        self.assertEqual(receipt["source"][V.DECLARED_FORM_KEY], V.FORM_LOCAL_SOURCE)

    def test_merkle_is_taken_before_build_not_after(self):
        """build 会往树里写产物 —— 收据里的 source merkle 必须仍是 **build 前**那个值。

        这正是人手拼收据时踩过的坑：事后再摘，摘到的是「源码 + 产物」，
        与 CP-A 记的那份字节永远对不上。
        """
        digest = V.take_snapshot_digest(self.root, _OP)
        # 模拟 build：往被摘的子树里写一个产物（`_walk_snapshot` 只按名跳过 build/output，
        # 生成物落在别处照样会改 merkle）。
        with open(os.path.join(self.root, _OP, "generated_tiling.h"), "w",
                  encoding="utf-8") as out:
            out.write("// build 产物\n")
        receipt = self._produce(digest=digest)
        self.assertEqual(receipt["source"]["snapshot_subtree_sha256"],
                         digest["snapshot_subtree_sha256"])
        state = receipt["build"]["tree_state_at_emit"]
        self.assertFalse(state["matches_pre_build"],
                         "build 动过树是常态——记下来才看得出摘要取自 build 前")
        self.assertNotEqual(state["snapshot_subtree_sha256"],
                            receipt["source"]["snapshot_subtree_sha256"])

    def test_production_path_never_digests_the_tree_itself(self):
        """结构性杜绝错法：不给 build 前摘要就产不出收据，没有「现场自己摘一遍」的口子。"""
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "恰好给一个"):
            V.produce_receipt(
                declared_source_form=V.FORM_LOCAL_SOURCE, library_path=self.elf,
                build_argv=["bash"], build_cwd=self.root, returncode=0)

    def test_pr_route_needs_a_head_and_rejects_both_at_once(self):
        receipt = V.produce_receipt(
            declared_source_form=V.FORM_GIT_PR, library_path=self.elf,
            build_argv=["bash", "build.sh"], build_cwd=self.root, returncode=0,
            repo="cann/ops-cv", pr_head_sha="a" * 40)
        self.assertEqual(receipt["source"]["pr_head_sha"], "a" * 40)
        self.assertEqual(receipt["degradations"], [])
        with self.assertRaises(V.VendorBuildReceiptError):
            self._produce(form=V.FORM_GIT_PR, pr_head_sha="a" * 40)

    def test_declared_git_pr_over_a_snapshot_books_the_degradation(self):
        """本该绑 PR head 却只拿到一份本地快照 = 降级，这条一个字都没放松。"""
        receipt = self._produce(form=V.FORM_GIT_PR)
        self.assertEqual(receipt["degradations"], [V.DEGRADATION_PR_HEAD_UNBOUND])
        V.validate(receipt, library_path=os.path.realpath(self.elf),
                   library_sha256=V._sha256_file(self.elf), normalize_path=True)

    def test_failed_build_produces_nothing(self):
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "returncode"):
            self._produce(returncode=1)

    def test_bad_argv_or_elf_fails_loud(self):
        with self.assertRaises(V.VendorBuildReceiptError):
            self._produce(build_argv=[])
        with self.assertRaises(V.VendorBuildReceiptError):
            self._produce(build_argv=["bash", ""])
        with self.assertRaises(V.VendorBuildReceiptError):
            self._produce(library_path=os.path.join(self.d, "missing.so"))

    def test_out_of_vocabulary_form_is_rejected(self):
        with self.assertRaises(V.VendorBuildReceiptError):
            self._produce(form="snapshot_only")

    def test_cli_round_trip(self):
        digest_path = os.path.join(self.d, "prebuild.json")
        receipt_path = os.path.join(self.d, "receipt.json")
        V.main(["snapshot-digest", "--source-root", self.root,
                "--subtree-scope", _OP, "--out", digest_path])
        V.main(["emit", "--declared-source-form", V.FORM_LOCAL_SOURCE,
                "--snapshot-digest", digest_path, "--library", self.elf,
                "--build-cwd", self.root, "--returncode", "0",
                "--build-argv", "bash", "--build-argv", "build.sh",
                "--out", receipt_path])
        with open(receipt_path, encoding="utf-8") as src:
            receipt = json.load(src)
        self.assertEqual(receipt["build"]["argv"], ["bash", "build.sh"])
        self.assertEqual(receipt["degradations"], [])
        V.validate(receipt, library_path=os.path.realpath(self.elf),
                   library_sha256=V._sha256_file(self.elf), normalize_path=True)


class DegradationVocabularyTest(_Fixture):
    """校验侧：声明形态与降级挂账必须**成对**；老收据按最严一档兼容。"""

    def _validated(self, receipt):
        return V.validate(
            receipt, library_path=os.path.realpath(self.elf),
            library_sha256=V._sha256_file(self.elf), normalize_path=True)

    def test_declared_local_source_may_not_book_a_degradation(self):
        receipt = self._produce()
        receipt["degradations"] = [V.DEGRADATION_PR_HEAD_UNBOUND]
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "degradations"):
            self._validated(receipt)

    def test_declared_git_pr_over_snapshot_must_book_it(self):
        receipt = self._produce(form=V.FORM_GIT_PR)
        receipt["degradations"] = []
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "degradations"):
            self._validated(receipt)

    def test_legacy_undeclared_snapshot_keeps_the_old_strict_rule(self):
        """未声明形态 = 老收据：仍必须挂 `pr_head_unbound`，与改动前逐字同规矩。"""
        receipt = self._produce()
        del receipt["source"][V.DECLARED_FORM_KEY]
        receipt["degradations"] = []
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "degradations"):
            self._validated(receipt)
        receipt["degradations"] = [V.DEGRADATION_PR_HEAD_UNBOUND]
        self.assertEqual(self._validated(receipt)["degradations"],
                         [V.DEGRADATION_PR_HEAD_UNBOUND], "老现场收据不得被判死")

    def test_declared_local_source_over_a_real_pr_is_rejected(self):
        receipt = V.produce_receipt(
            declared_source_form=V.FORM_GIT_PR, library_path=self.elf,
            build_argv=["bash"], build_cwd=self.root, returncode=0,
            repo="cann/ops-cv", pr_head_sha="a" * 40)
        receipt["source"][V.DECLARED_FORM_KEY] = V.FORM_LOCAL_SOURCE
        with self.assertRaises(V.VendorBuildReceiptError):
            self._validated(receipt)

    def test_schema_v1_may_not_declare_a_form(self):
        receipt = V.produce_receipt(
            declared_source_form=V.FORM_GIT_PR, library_path=self.elf,
            build_argv=["bash"], build_cwd=self.root, returncode=0,
            repo="cann/ops-cv", pr_head_sha="a" * 40)
        receipt["schema_version"] = V.SCHEMA_VERSION_LEGACY
        del receipt["source"]["provenance_kind"]
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "schema_version=1"):
            self._validated(receipt)

    def test_synthesized_head_on_a_snapshot_receipt_is_still_rejected(self):
        receipt = self._produce()
        receipt["source"]["pr_head_sha"] = "e" * 40
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "捏造 PR head"):
            self._validated(receipt)


if __name__ == "__main__":
    unittest.main()
