#!/usr/bin/env python3
"""aclnn 静态 CP-C0 预检单测；不加载 ACL、不访问 NPU。"""

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import content_address
import preflight_aclnn as P


def _cli_rc(root, spec_rel="spec.json"):
    """跑一遍 CLI 取退出码（吞掉它打到 stdout 的 payload，别弄脏测试输出）。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return P.main(["--root", root, "--spec", spec_rel])


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
_MERKLE = "b" * 64
_SCOPE = "experimental/index/reduce"
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
        self.assertEqual(_cli_rc(self.root), 0)       # CLI 对该状态返回 0

    def test_illegal_form_is_blocked_not_not_applicable(self):
        """⭐ 写坏的 `runner_form` **不得**被判成「这道门不适用」（2026-08-05 审修门 High#2）。

        原写法是「不是 aclnn_py / cpp_extension 就 NOT_APPLICABLE」，于是 `null` / `""` / `0` /
        `"opaque"` 一并落进 NOT_APPLICABLE，而 CLI 对该状态返回 0——一份根本没声明合法形态的
        spec 拿到的是「不需要 ABI 预检」的绿灯。门看着有、实际拦不住。
        """
        for bad in (None, "", 0, "opaque"):
            with self.subTest(bad=bad):
                spec = _spec()
                spec["runner_form"] = bad
                self._write("spec.json", spec)
                result = P.evaluate(self.root, "spec.json")
                self.assertEqual(result["status"], "BLOCKED")
                self.assertIsNone(result["acceptance_verdict"])
                self.assertTrue(result["blocked_reasons"])
                self.assertIn("受控词表", result["blocked_reasons"][0])
                # CLI 必须以非 0 退出，否则编排层照样往下走
                self.assertEqual(_cli_rc(self.root), 2)

    def test_missing_form_key_falls_back_to_the_single_source_default(self):
        """键缺席 → 全仓唯一缺省（`cpp_extension`），照常做 ABI 预检、不早退。"""
        spec = _spec()
        spec.pop("runner_form")
        self._write("spec.json", spec)
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "READY_WAIT_NPU_TRUST_GATE")
        self.assertEqual(result["bindings"]["runner_form"], "cpp_extension")

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

    def test_pull_request_bindings_never_carry_a_local_anchor(self):
        """PR 通路回归钉：本地档的锚绝不出现在 PR 档的 bindings 里。

        ⚠ 本条原名 `test_pull_request_bindings_carry_no_dut_source_key`，钉的是已被
          合并裁定删除的 `dut_source` / `local_root_digest` 两个键。那两个键在主干里
          **已经不存在**，只留 `assertNotIn` 等于一条永远不会红的断言（假门）。
          改写成对**现行**词表的等价断言，并保留原来那两条作廉价的回归护栏
          （防的是有人把 ours 那套键重新引回来）。

        钉住的仍是同一个承诺：**PR 通路 payload 不因本地通路的接入而漂移**。
        `snapshot_merkle_sha256` 在这一档是「键在、值恒为 None」——省略它会让读产物的人
        分不清「没这回事」和「工具忘了记」；写成 merkle 则是拿本地锚冒充 PR 锚。
        `snapshot_scope` 整键不出场：PR 档根本没有「范围」这个概念。
        """
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "READY_WAIT_NPU_TRUST_GATE")
        bindings = result["bindings"]
        self.assertEqual(bindings["provenance_kind"], "gitcode_pr")
        self.assertEqual(bindings["pr_head_sha"], _HEAD)
        # 显式 null，不是缺席——两者语义不同，别互相顶替。
        self.assertIn("snapshot_merkle_sha256", bindings)
        self.assertIsNone(bindings["snapshot_merkle_sha256"])
        self.assertNotIn("snapshot_scope", bindings)
        # 本地档才有的中性形态事实，PR 档恒为空表。
        self.assertEqual(result["provenance_form_facts"], [])
        self.assertEqual(result["provenance_degradations"], [])
        # 回归护栏：ours 那套已删的键一个都不许回来。
        self.assertNotIn("dut_source", bindings)
        self.assertNotIn("local_root_digest", bindings)


class LocalSnapshotAnchorTest(unittest.TestCase):
    """本地源码通路在**这道门上**的拒绝侧：锚对不上就 BLOCKED，且绝不与 PR 锚互相伪装。

    三处分工写清楚，免得下一个人当成重复测试删掉：

    | 谁 | 钉什么 |
    |---|---|
    | `test_source_provenance` | `source_provenance.bind` 这个**判据本身**对不对（单元级） |
    | `AclnnPreflightTest` 的两条本地用例 | **放行**侧：声明即所得不算降级；未声明的快照仍须授权 |
    | 本类 | **拒绝**侧经 `evaluate` 走完整条路后，是否落成机读 `BLOCKED` + 点名的 `blocked_reasons` |

    第三件事单测覆盖不到，而它正是本仓最贵的那类缺陷（假门）的防线：`evaluate` 靠
    `ProvenanceError` 是 `ValueError` 子类才把它收敛进 payload。谁把那行
    `source_provenance.bind` 删掉、或把 `except` 子句收窄，`test_source_provenance`
    会全绿，而这道门已经不在了。

    ⚠ 本类原名 `LocalCheckoutTest`，钉的是已被合并裁定删除的 `dut_source`/`root_digest`
      那套锚。逐条改写到 `local_snapshot` 的等价断言上，**一条都没丢**，并补了 ours
      没有的 `snapshot_scope`（范围）维——ours 的锚只有子树摘要、没有范围。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self._write("spec.json", _spec())
        self._write("pr_facts.json", self._local_pr_facts())
        self._write_source(self._local_source())

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel, value):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as out:
            json.dump(value, out)

    def _write_source(self, value):
        content_address.write_artifact(
            self.root, "source_facts.json", P._SOURCE_DOMAIN, value)

    def _assert_blocked(self, *needles):
        """跑完整条 `evaluate`，断言它落成**机读** BLOCKED（不是 traceback），且理由点名。

        顺带对每一条拒绝路径都钉住「不留半截绑定」：门没过，一个锚都不该被发布出去。
        （ours 只在缺锚那一条上钉过这件事，这里升成所有拒绝路径的通用不变式。）
        """
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["acceptance_verdict"])
        self.assertTrue(result["blocked_reasons"])
        reason = result["blocked_reasons"][0]
        for needle in needles:
            self.assertIn(needle, reason)
        self.assertNotIn("pr_head_sha", result["bindings"])
        self.assertNotIn("snapshot_merkle_sha256", result["bindings"])
        return result

    @staticmethod
    def _local_pr_facts():
        return {
            "declared_source_form": "local_source",
            "provenance_kind": "local_snapshot",
            "head_sha": None,
            "snapshot_merkle_sha256": _MERKLE,
            "snapshot_scope": _SCOPE,
            # ⚠ 故意保留这个诱饵：本地快照记「这份字节大概取自哪个 commit」是**合法的
            #   信息字段**，但它不是锚。下面
            #   test_missing_merkle_never_falls_back_to_a_commit_field
            #   就是靠它证明没有「哪个字段有值就用哪个」的兜底。
            "upstream_hint": {"commit": _HEAD, "dirty": False},
            "key_files": {_PATH: _HEADER},
        }

    @staticmethod
    def _local_source():
        raw = _HEADER.encode()
        return {
            "contract_version": 1,
            "declared_source_form": "local_source",
            "pr": {
                "provenance_kind": "local_snapshot",
                "head_sha": None,
                "snapshot_merkle_sha256": _MERKLE,
                "snapshot_scope": _SCOPE,
            },
            "key_files": [{
                "path": _PATH,
                "ref": "local_snapshot",
                "bytes_sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }],
            "derived": {
                "aclnn_headers": [_PATH],
                "interface_kind": "aclnn_2stage",
            },
            "completeness": {"status": "complete", "reasons": [], "form_facts": [
                "local_source_has_no_upstream_commit",
                "local_source_file_set_is_subtree_not_pr_diff"]},
        }

    def test_local_snapshot_binds_its_own_anchor_and_never_the_pr_key(self):
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "READY_WAIT_NPU_TRUST_GATE",
                         result["blocked_reasons"])
        bindings = result["bindings"]
        self.assertEqual(bindings["provenance_kind"], "local_snapshot")
        self.assertEqual(bindings["snapshot_merkle_sha256"], _MERKLE)
        self.assertEqual(bindings["snapshot_scope"], _SCOPE)
        # ⭐ 64 位 merkle 绝不能借 PR 的键名出场——下游是**按键名**认通路的。
        #   这一档 `pr_head_sha` 是**显式 None**（键在、值为空），不是省略：
        #   省略会让读产物的人分不清「没这回事」与「工具忘了记」。
        self.assertIn("pr_head_sha", bindings)
        self.assertIsNone(bindings["pr_head_sha"])
        # 本地源码没有上游 commit 是这条通路的**中性事实**，不是降级。
        self.assertEqual(result["provenance_degradations"], [])

    def test_merkle_mismatch_between_fact_packs_is_blocked(self):
        source = self._local_source()
        source["pr"]["snapshot_merkle_sha256"] = "c" * 64
        self._write_source(source)
        self._assert_blocked("snapshot_merkle_sha256")

    def test_snapshot_scope_mismatch_is_blocked(self):
        """范围对不上，两个 merkle 就不可比——ours 的锚没有范围维，这条是新增覆盖。"""
        source = self._local_source()
        source["pr"]["snapshot_scope"] = "some/other/subtree"
        self._write_source(source)
        self._assert_blocked("snapshot_scope")

    def test_local_fact_pack_against_a_pr_source_is_blocked_on_kind(self):
        source = self._local_source()
        source.pop("declared_source_form")
        source["pr"] = {"provenance_kind": "gitcode_pr", "head_sha": _HEAD}
        self._write_source(source)
        self._assert_blocked("provenance_kind", "不是同一条取源通路")

    def test_pr_fact_pack_against_a_local_source_is_blocked_on_kind(self):
        self._write("pr_facts.json", {
            "provenance_kind": "gitcode_pr", "head_sha": _HEAD,
            "key_files": {_PATH: _HEADER}})
        self._assert_blocked("provenance_kind", "不是同一条取源通路")

    def test_missing_merkle_never_falls_back_to_a_commit_field(self):
        """⭐ ours 的净贡献：缺锚就是缺锚，**旁边那个信息字段不是备胎**。

        `_local_pr_facts` 里坐着一个合法的 `upstream_hint.commit`。任何「哪个字段有值
        就用哪个」的兜底都会把它当 provenance 锚使——而它什么都证明不了。
        """
        facts = self._local_pr_facts()
        facts.pop("snapshot_merkle_sha256")
        self._write("pr_facts.json", facts)
        result = self._assert_blocked("snapshot_merkle_sha256")
        # 诱饵 commit 一个字都不许渗进 blocked payload 的绑定里。
        self.assertNotIn(_HEAD, json.dumps(result["bindings"]))

    def test_out_of_vocabulary_kind_is_blocked_not_defaulted(self):
        """两侧**一致地**写了个词表外的取源形态——一致不等于合法，不得被归类进任一通路。"""
        facts = self._local_pr_facts()
        facts["provenance_kind"] = "local"      # 拼错，不在受控词表
        self._write("pr_facts.json", facts)
        source = self._local_source()
        source["pr"]["provenance_kind"] = "local"
        self._write_source(source)
        self._assert_blocked("不被接受的源身份组合")

    def test_mixing_a_pr_head_into_a_local_snapshot_pack_is_blocked(self):
        """两条通路的锚混装即拒：本地档硬性要求 `head_sha` **显式 null**。

        堵「本地 provenance 伪装成 PR provenance」，也堵反方向——在本地快照上合成
        一个 40 位 hex 就是捏造 PR head（AGENTS.md §5.8）。
        """
        source = self._local_source()
        source["pr"]["head_sha"] = _HEAD
        self._write_source(source)
        self._assert_blocked("head_sha", "显式")


if __name__ == "__main__":
    unittest.main()
