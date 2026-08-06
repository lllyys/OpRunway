#!/usr/bin/env python3
"""`vendor_build_receipt` 的校验分流 + **确定性生产路径**单测（纯本地，不碰 NPU）。

重点在三件事：

1. **降级挂账的新口径**：本地源码是一等输入形态 → 显式声明 `local_source` 的收据必须**无降级**；
   声明 `git_pr` 却只拿到快照、以及**未声明形态的老收据**，仍必须挂 `pr_head_unbound`
   （老现场一条不改照样过门，新口径只对显式声明者生效）；
2. **merkle 必须在 build 之前算**：生产路径只接受 build 前落下的摘要凭据，自己不去摘源码树；
   build 往树里写了产物之后，收据里的 source merkle 仍是 build 前那个值，
   而 `build.tree_state_at_emit` 如实记下「树已经变了」；
3. ⭐ **`build.returncode` 必须是实测值**（2026-08-06 补的 fail-open）：`emit` 真跑构建命令，
   收据记 `returncode_source=measured`；自报值一律拒；老收据（没有这个键）在摘要里落
   `unproven_legacy`，**与 `measured` 长得不一样**。

本文件里凡是「构建命令」都是**真的会被执行**的 `sys.executable -c …`：如果哪天有人把
`emit` 改回「只记录不执行」，这些用例会立刻发现——因为 sentinel 文件不会出现、
ELF 不会被改写。
"""
import json
import os
import sys
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

#: 「真跑」的构建命令本体：把第 1 个实参当 ELF 路径重写、第 2 个当 sentinel、按第 3 个退出。
#: 其余实参（`--pkg` / `-j16` 这类）原样忽略——真实构建命令的实参几乎全长那样。
_BUILD_PY = (
    "import sys\n"
    "elf, sentinel, rc = sys.argv[1], sys.argv[2], int(sys.argv[3])\n"
    "open(elf, 'wb').write(sentinel.encode())\n"
    "open(sentinel, 'w').write('built')\n"
    "sys.exit(rc)\n"
)


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
            out.write(b"vendor-elf-before-any-build")
        self._builds = 0

    def tearDown(self):
        self.tmp.cleanup()

    # —— 构建命令 ——————————————————————————————————————————————
    def _argv(self, rc=0, extra=()):
        """一条**会被真的执行**的构建命令；每次产出不同的 ELF 字节 + 一个 sentinel。"""
        self._builds += 1
        self.sentinel = os.path.join(self.d, f"built-{self._builds}.stamp")
        return [sys.executable, "-c", _BUILD_PY,
                self.elf, self.sentinel, str(rc)] + list(extra)

    def _noop_argv(self):
        """什么都不做的命令：ELF 一个字节都不会变 → 收据必须产不出来。"""
        return [sys.executable, "-c", "pass"]

    def _run(self, rc=0):
        return V.run_build(self._argv(rc), self.root, self.elf)

    def _produce(self, form=V.FORM_LOCAL_SOURCE, digest=None, **over):
        # ⚠ 摘要必须在 build **之前**取，这里的求值顺序就是那条纪律。
        digest = V.take_snapshot_digest(self.root, _OP) if digest is None else digest
        kwargs = dict(declared_source_form=form,
                      build_result=self._run(),
                      snapshot_digest=digest)
        kwargs.update(over)
        return V.produce_receipt(**kwargs)

    def _validated(self, receipt):
        return V.validate(
            receipt, library_path=os.path.realpath(self.elf),
            library_sha256=V._sha256_file(self.elf), normalize_path=True)


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


class RunBuildTest(_Fixture):
    """⭐ `build.returncode` 的**实测性**——本模块没有「只记录不执行」模式。"""

    def test_run_build_actually_executes_the_command(self):
        result = self._run()
        self.assertTrue(os.path.isfile(self.sentinel),
                        "构建命令必须真的跑过——sentinel 不在就说明又变回「只记录」了")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result[V.RETURNCODE_SOURCE_KEY], V.RETURNCODE_SOURCE_MEASURED)
        self.assertEqual(result["execution"]["library_path"], os.path.realpath(self.elf))
        self.assertNotEqual(result["execution"]["library_before"],
                            result["execution"]["library_after"])

    def test_nonzero_returncode_is_reported_verbatim_and_blocks_the_receipt(self):
        result = self._run(rc=3)
        self.assertEqual(result["returncode"], 3, "实测就是 3，不许被抹平成 0")
        digest = V.take_snapshot_digest(self.root, _OP)
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "returncode"):
            V.produce_receipt(declared_source_form=V.FORM_LOCAL_SOURCE,
                              build_result=result, snapshot_digest=digest)

    def test_build_that_never_touches_the_library_is_rejected(self):
        """`-- /usr/bin/true` + 一个预先存在的 `.so` = 伪造路径，必须当场停。"""
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "一个字节都没变"):
            V.run_build(self._noop_argv(), self.root, self.elf)

    def test_missing_library_after_build_is_rejected(self):
        missing = os.path.join(self.d, "never-built.so")
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "仍不存在"):
            V.run_build(self._noop_argv(), self.root, missing)

    def test_unrunnable_command_or_bad_cwd_fails_loud(self):
        with self.assertRaises(V.VendorBuildReceiptError):
            V.run_build([os.path.join(self.d, "no-such-binary")], self.root, self.elf)
        with self.assertRaises(V.VendorBuildReceiptError):
            V.run_build(self._argv(), os.path.join(self.d, "no-such-dir"), self.elf)
        with self.assertRaises(V.VendorBuildReceiptError):
            V.run_build([], self.root, self.elf)

    def test_produce_refuses_a_hand_made_build_result(self):
        """⭐ 生产侧唯一入口：拼一个「长得像」的 dict 也产不出收据。"""
        digest = V.take_snapshot_digest(self.root, _OP)
        forged = {"argv": ["bash", "build.sh"], "cwd": self.root, "returncode": 0,
                  V.RETURNCODE_SOURCE_KEY: V.RETURNCODE_SOURCE_DECLARED,
                  "execution": {"library_path": self.elf,
                                "library_after": V._library_state(self.elf)}}
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "run_build"):
            V.produce_receipt(declared_source_form=V.FORM_LOCAL_SOURCE,
                              build_result=forged, snapshot_digest=digest)

    def test_library_touched_between_build_and_emit_is_rejected(self):
        result = self._run()
        with open(self.elf, "ab") as out:
            out.write(b"someone-else-wrote-here")
        digest = V.take_snapshot_digest(self.root, _OP)
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "又被动过"):
            V.produce_receipt(declared_source_form=V.FORM_LOCAL_SOURCE,
                              build_result=result, snapshot_digest=digest)


class ReturncodeSourceValidationTest(_Fixture):
    """⭐ 校验侧：实测 / 自报 / 老收据三档在下游**必须长得不一样**。"""

    def test_measured_receipt_reports_measured(self):
        receipt = self._produce()
        self.assertEqual(receipt["build"][V.RETURNCODE_SOURCE_KEY],
                         V.RETURNCODE_SOURCE_MEASURED)
        self.assertEqual(self._validated(receipt)["build_returncode_source"],
                         V.RETURNCODE_SOURCE_MEASURED)

    def test_declared_returncode_source_is_rejected_by_name(self):
        receipt = self._produce()
        receipt["build"][V.RETURNCODE_SOURCE_KEY] = V.RETURNCODE_SOURCE_DECLARED
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "自报"):
            self._validated(receipt)

    def test_out_of_vocabulary_returncode_source_is_rejected(self):
        for bogus in ("MEASURED", "", None, 0, V.RETURNCODE_SOURCE_UNPROVEN_LEGACY):
            receipt = self._produce()
            receipt["build"][V.RETURNCODE_SOURCE_KEY] = bogus
            with self.assertRaises(V.VendorBuildReceiptError):
                self._validated(receipt)

    def test_legacy_receipt_without_the_key_is_unproven_not_measured(self):
        """老收据仍能过门（兼容），但摘要**不替它宣称实测过**。"""
        receipt = self._produce()
        del receipt["build"][V.RETURNCODE_SOURCE_KEY]
        summary = self._validated(receipt)
        self.assertEqual(summary["build_returncode_source"],
                         V.RETURNCODE_SOURCE_UNPROVEN_LEGACY)
        self.assertNotEqual(summary["build_returncode_source"],
                            V.RETURNCODE_SOURCE_MEASURED,
                            "「没人知道」不许在下游长成「实测过」")

    def test_summarize_and_validate_agree(self):
        """driver 落的派生视图与离线复核方重算的结果逐字比对，两侧必须同形。"""
        receipt = self._produce()
        self.assertEqual(V.summarize(receipt), self._validated(receipt))


class ProduceReceiptTest(_Fixture):
    def test_local_source_receipt_passes_validate_with_no_degradation(self):
        receipt = self._produce()
        summary = self._validated(receipt)
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
        result = self._run()
        # 模拟 build 的副产物：往被摘的子树里写一个文件（`_walk_snapshot` 只按名跳过
        # build/output，生成物落在别处照样会改 merkle）。
        with open(os.path.join(self.root, _OP, "generated_tiling.h"), "w",
                  encoding="utf-8") as out:
            out.write("// build 产物\n")
        receipt = V.produce_receipt(declared_source_form=V.FORM_LOCAL_SOURCE,
                                    build_result=result, snapshot_digest=digest)
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
            V.produce_receipt(declared_source_form=V.FORM_LOCAL_SOURCE,
                              build_result=self._run())

    def test_pr_route_needs_a_head_and_rejects_both_at_once(self):
        receipt = V.produce_receipt(
            declared_source_form=V.FORM_GIT_PR, build_result=self._run(),
            repo="cann/ops-cv", pr_head_sha="a" * 40)
        self.assertEqual(receipt["source"]["pr_head_sha"], "a" * 40)
        self.assertEqual(receipt["degradations"], [])
        with self.assertRaises(V.VendorBuildReceiptError):
            self._produce(form=V.FORM_GIT_PR, pr_head_sha="a" * 40)

    def test_declared_git_pr_over_a_snapshot_books_the_degradation(self):
        """本该绑 PR head 却只拿到一份本地快照 = 降级，这条一个字都没放松。"""
        receipt = self._produce(form=V.FORM_GIT_PR)
        self.assertEqual(receipt["degradations"], [V.DEGRADATION_PR_HEAD_UNBOUND])
        self._validated(receipt)

    def test_out_of_vocabulary_form_is_rejected(self):
        with self.assertRaises(V.VendorBuildReceiptError):
            self._produce(form="snapshot_only")

    def test_cli_round_trip(self):
        digest_path = os.path.join(self.d, "prebuild.json")
        receipt_path = os.path.join(self.d, "receipt.json")
        V.main(["snapshot-digest", "--source-root", self.root,
                "--subtree-scope", _OP, "--out", digest_path])
        argv = self._argv()
        V.main(["emit", "--declared-source-form", V.FORM_LOCAL_SOURCE,
                "--snapshot-digest", digest_path, "--library", self.elf,
                "--build-cwd", self.root, "--returncode", "0"]
               + [f"--build-argv={a}" for a in argv]
               + ["--out", receipt_path])
        with open(receipt_path, encoding="utf-8") as src:
            receipt = json.load(src)
        self.assertTrue(os.path.isfile(self.sentinel),
                        "⭐ emit 必须**真的执行**构建命令，不是把 --returncode 抄进收据")
        self.assertEqual(receipt["build"]["argv"], argv)
        self.assertEqual(receipt["build"][V.RETURNCODE_SOURCE_KEY],
                         V.RETURNCODE_SOURCE_MEASURED)
        self.assertEqual(receipt["degradations"], [])
        self._validated(receipt)

    def test_cli_build_argv_accepts_dash_leading_args(self):
        """构建实参以 `-` 开头（真实 build 命令的常态）必须能原样进收据。

        回归点（2026-08-05 GaussianBlur 干净现场实测）：本用例原先只喂不带 `-` 的实参，
        于是「`--build-argv --pkg` 分开写会被 argparse 当成另一个选项、当场
        `expected one argument`」这条从没被测到——而真实构建命令**每一个**实参都长这样。
        等号形式是这条 CLI 的正确用法，用例把它钉住。
        """
        digest_path = os.path.join(self.d, "prebuild2.json")
        receipt_path = os.path.join(self.d, "receipt2.json")
        argv = self._argv(extra=["--pkg", "--soc=ascend950", "-j16"])
        V.main(["snapshot-digest", "--source-root", self.root,
                "--subtree-scope", _OP, "--out", digest_path])
        V.main(["emit", "--declared-source-form", V.FORM_LOCAL_SOURCE,
                "--snapshot-digest", digest_path, "--library", self.elf,
                "--build-cwd", self.root]
               + [f"--build-argv={a}" for a in argv]
               + ["--out", receipt_path])
        with open(receipt_path, encoding="utf-8") as src:
            receipt = json.load(src)
        self.assertEqual(receipt["build"]["argv"], argv)

    def test_cli_has_no_record_only_mode(self):
        """⭐ 「只给 --returncode、不执行任何 build」这条路必须**根本不存在**。"""
        digest_path = os.path.join(self.d, "prebuild3.json")
        receipt_path = os.path.join(self.d, "receipt3.json")
        V.main(["snapshot-digest", "--source-root", self.root,
                "--subtree-scope", _OP, "--out", digest_path])
        # 断言锚在**语义**上：必须点名缺 --build-argv，且明说不存在「只给 returncode、不执行」这种模式。
        # 不锚具体文案（换个说法就假绿），也不只锚「抛了异常」（抛别的原因也会过）。
        with self.assertRaisesRegex(V.VendorBuildReceiptError, r"--build-argv"):
            V.main(["emit", "--declared-source-form", V.FORM_LOCAL_SOURCE,
                    "--snapshot-digest", digest_path, "--library", self.elf,
                    "--build-cwd", self.root, "--returncode", "0",
                    "--out", receipt_path])
        self.assertFalse(os.path.exists(receipt_path), "拒了就不该落盘")
        # ⭐ 真正要钉的是「这条路根本不存在」：argparse 层面就不该有能单独产收据的自报模式。
        with self.assertRaises(V.VendorBuildReceiptError) as caught:
            V.main(["emit", "--declared-source-form", V.FORM_LOCAL_SOURCE,
                    "--snapshot-digest", digest_path, "--library", self.elf,
                    "--build-cwd", self.root, "--returncode", "0",
                    "--out", receipt_path])
        self.assertIn("不执行", str(caught.exception),
                      "报错必须说清「没有只记录不执行的模式」，否则下一个人会以为是漏传参数")

    def test_cli_expected_returncode_must_match_the_measured_one(self):
        """`--returncode` 只是期望值断言；实测说了算，对不上就不产收据。"""
        digest_path = os.path.join(self.d, "prebuild4.json")
        receipt_path = os.path.join(self.d, "receipt4.json")
        V.main(["snapshot-digest", "--source-root", self.root,
                "--subtree-scope", _OP, "--out", digest_path])
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "实测"):
            V.main(["emit", "--declared-source-form", V.FORM_LOCAL_SOURCE,
                    "--snapshot-digest", digest_path, "--library", self.elf,
                    "--build-cwd", self.root, "--returncode", "0"]
                   + [f"--build-argv={a}" for a in self._argv(rc=7)]
                   + ["--out", receipt_path])
        self.assertFalse(os.path.exists(receipt_path))


class DegradationVocabularyTest(_Fixture):
    """校验侧：声明形态与降级挂账必须**成对**；老收据按最严一档兼容。"""

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
            declared_source_form=V.FORM_GIT_PR, build_result=self._run(),
            repo="cann/ops-cv", pr_head_sha="a" * 40)
        receipt["source"][V.DECLARED_FORM_KEY] = V.FORM_LOCAL_SOURCE
        with self.assertRaises(V.VendorBuildReceiptError):
            self._validated(receipt)

    def test_schema_v1_may_not_declare_a_form(self):
        receipt = V.produce_receipt(
            declared_source_form=V.FORM_GIT_PR, build_result=self._run(),
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
