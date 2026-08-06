#!/usr/bin/env python3
"""aclnn 静态 CP-C0 预检单测；不加载 ACL、不访问 NPU。"""

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest

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
_ROOT_DIGEST = "b" * 64
_OP_SUBDIR = "experimental/index/reduce"
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
        self._write("pr_facts.json", {
            "head_sha": _HEAD,
            "key_files": {_PATH: _HEADER},
        })
        raw = _HEADER.encode()
        source = {
            "contract_version": 1,
            "pr": {"head_sha": _HEAD},
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

    def test_pull_request_bindings_carry_no_dut_source_key(self):
        """PR 通路回归钉：payload 形状与接入判别式之前逐字一致。

        `dut_source` 键只在本地分支写。这条钉住的是「PR 通路 payload 不变」这个承诺——
        它一旦被写成无条件赋值，所有既有 PR 收据的绑定摘要会集体漂移。
        """
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "READY_WAIT_NPU_TRUST_GATE")
        self.assertNotIn("dut_source", result["bindings"])
        self.assertNotIn("local_root_digest", result["bindings"])
        self.assertEqual(result["bindings"]["pr_head_sha"], _HEAD)


class LocalCheckoutTest(unittest.TestCase):
    """本地来源通路：锚是 root_digest，绝不与 PR 锚互相伪装。"""

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

    @staticmethod
    def _local_pr_facts():
        return {
            "dut_source": "local_checkout",
            "local_checkout": {
                "op_subdir": _OP_SUBDIR,
                "root_digest": _ROOT_DIGEST,
                # ⚠ 故意保留：`git.head_sha` 是合法的信息字段，不是锚。
                # 下面 test_missing_root_digest_never_falls_back_to_git_head
                # 就是靠它证明没有「哪个字段有值用哪个」的兜底。
                "git": {"head_sha": _HEAD, "dirty": False, "dirty_files": []},
            },
            "key_files": {_PATH: _HEADER},
        }

    @staticmethod
    def _local_source():
        raw = _HEADER.encode()
        return {
            "contract_version": 1,
            "dut_source": "local_checkout",
            "local_checkout": {
                "op_subdir": _OP_SUBDIR,
                "root_digest": _ROOT_DIGEST,
            },
            "key_files": [{
                "path": _PATH,
                "ref": _ROOT_DIGEST,
                "bytes_sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }],
            "derived": {
                "aclnn_headers": [_PATH],
                "interface_kind": "aclnn_2stage",
            },
            "completeness": {"status": "complete", "reasons": []},
        }

    def test_local_checkout_binds_root_digest_and_never_pr_head(self):
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "READY_WAIT_NPU_TRUST_GATE")
        self.assertEqual(result["bindings"]["dut_source"], "local_checkout")
        self.assertEqual(
            result["bindings"]["local_root_digest"], _ROOT_DIGEST)
        # 64 位摘要绝不能借 PR 的键名出场——下游是按键名认通路的。
        self.assertNotIn("pr_head_sha", result["bindings"])

    def test_root_digest_mismatch_between_pr_facts_and_source_is_blocked(self):
        source = self._local_source()
        source["local_checkout"]["root_digest"] = "c" * 64
        self._write_source(source)
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("local_root_digest", result["blocked_reasons"][0])

    def test_local_pr_facts_against_pr_source_is_blocked_on_dut_source(self):
        source = self._local_source()
        source.pop("dut_source")
        source.pop("local_checkout")
        source["pr"] = {"head_sha": _HEAD}
        self._write_source(source)
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        reason = result["blocked_reasons"][0]
        self.assertIn("dut_source", reason)
        self.assertIn("不一致", reason)

    def test_pr_pr_facts_against_local_source_is_blocked_on_dut_source(self):
        self._write("pr_facts.json", {
            "head_sha": _HEAD, "key_files": {_PATH: _HEADER}})
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        reason = result["blocked_reasons"][0]
        self.assertIn("dut_source", reason)
        self.assertIn("不一致", reason)

    def test_missing_root_digest_never_falls_back_to_git_head(self):
        facts = self._local_pr_facts()
        facts["local_checkout"].pop("root_digest")
        self._write("pr_facts.json", facts)
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("root_digest", result["blocked_reasons"][0])
        # 缺锚就是缺锚：既不许退回 git.head_sha，也不许留半截绑定。
        self.assertNotIn("pr_head_sha", result["bindings"])
        self.assertNotIn("local_root_digest", result["bindings"])

    def test_misspelled_dut_source_is_blocked_not_defaulted(self):
        facts = self._local_pr_facts()
        facts["dut_source"] = "local"           # 拼错，不在受控词表
        self._write("pr_facts.json", facts)
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("受控词表", result["blocked_reasons"][0])

    def test_source_facts_mixing_pr_and_local_facts_is_blocked(self):
        source = self._local_source()
        source["pr"] = {"head_sha": _HEAD}      # 两条通路的事实混装
        self._write_source(source)
        result = P.evaluate(self.root, "spec.json")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("混装", result["blocked_reasons"][0])


if __name__ == "__main__":
    unittest.main()
