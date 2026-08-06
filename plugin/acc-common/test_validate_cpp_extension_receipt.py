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


def source_facts_payload(dut_source="local_checkout", root_digest="c" * 64,
                         head_sha="a" * 40, git=None, completeness=None,
                         op_subdir="op"):
    """一份**过完整契约**的 `source_facts` payload（供本文件与渲染器单测共用）。

    ⚠ 不能只塞一个 `root_digest`：三级门复用 `validate_preparation_state._validate_source_payload`
    校这份对照物——digest 自洽只证明 payload 没被改过，证明不了它是一份完整、未降级的取材产物。
    最小 payload 也能自洽（`make_artifact` 谁都能调），所以契约那一层必须真的过。
    """
    import fetch_source
    anchor = root_digest if dut_source == "local_checkout" else head_sha
    payload = {
        "contract_version": 1,
        "taskdoc": {"bytes_sha256": "1" * 64, "snapshot_sha256": "1" * 64,
                    "size": 12, "source_locator": "task.md"},
        "changed_files": ["op/x.h"],
        "key_files": [{"path": "op/x.h", "ref": anchor,
                       "bytes_sha256": "2" * 64, "size": 9}],
        "derived": {"op": "X", "target_dir": "op", "aclnn_headers": ["op/x.h"]},
        "completeness": (completeness if completeness is not None
                         else {"status": "complete", "reasons": []}),
        "producer": {"tool": "fetch_source.py", "logic_sha256": "3" * 64},
    }
    if dut_source == "local_checkout":
        payload["dut_source"] = dut_source
        payload["local_checkout"] = {
            "root_digest": root_digest, "op_subdir": op_subdir,
            "digest_policy": fetch_source.digest_policy()}
        if git is not None:
            payload["local_checkout"]["git"] = git
            # 降级留痕由**载重事实派生**，与真 producer 同一条规则：夹具少写这条，
            # 契约校验会以「降级没留痕」拒掉，用例就测不到它想测的分支。
            if git.get("dirty") and completeness is None:
                payload["completeness"]["warnings"] = [
                    fetch_source.WARN_DIRTY_WORKTREE_ALLOWED]
    else:
        # PR 通路的 payload **不写** `dut_source` 键（缺席即 pull_request，与产物形态一致）。
        payload["pr"] = {"canonical_url": "https://gitcode.com/o/r/pull/1",
                         "source_repo": "o/r", "number": 1, "head_sha": head_sha,
                         "head_repo": "o/r", "is_fork": False, "state": "open"}
    return payload


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
    def _facts_payload(dut_source="local_checkout", root_digest=DIGEST, head_sha="a" * 40):
        return source_facts_payload(
            dut_source=dut_source, root_digest=root_digest, head_sha=head_sha)

    @classmethod
    def _write_source_facts(cls, root, *, sub=None, **kw):
        """⚠ 必须写**真** content_address envelope（digest 由 payload 算出）+ **完整契约 payload**。

        手拼 `{"domain":…, "payload":…}` 或只塞一个 root_digest 的最小 payload，
        都会被 `dut_source.find_source_facts` 判 UNTRUSTED——那样这些用例名义上在测「锚对不对得上」，
        实际全落在「对照物不可信」那条分支上。
        """
        import content_address
        parts = [root] + ([sub] if sub else []) + ["source_facts.json"]
        _write_json(os.path.join(*parts), content_address.make_artifact(
            "oprunway/source-facts/v1", cls._facts_payload(**kw)))

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
            self._write_source_facts(root, sub="work")
            self.assertEqual([], self._run(root, caseset, envelope, evidence))

    def test_tampered_source_facts_envelope_is_blocked(self):
        """⭐ 对照物本身必须可信：payload 改了、digest 没改 → 不是「锚对不上」，是整份不可信。

        不复算 digest 的话，手写一份「只有一个与恶意收据同值的 root_digest」的最小 JSON
        就能当本地来源的信任锚——本地锚的全部可信度就来自这条对账。
        """
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self._relocalize(envelope, evidence)
            self._write_source_facts(root, root_digest="d" * 64)
            path = os.path.join(root, "source_facts.json")
            with open(path, encoding="utf-8") as src:
                doc = json.load(src)
            doc["payload"]["local_checkout"]["root_digest"] = self.DIGEST   # 改成对上
            _write_json(path, doc)                                          # digest 不动
            errors = self._run(root, caseset, envelope, evidence)
            self.assertTrue(any("不可读" in e or "不可信" in e for e in errors), errors)

    def test_explicit_source_facts_path_that_does_not_exist_is_blocked(self):
        """⭐ 显式指路指空 ≠ 自动发现落空：一个 typo 不能把整条对账悄悄关掉。"""
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            errors = []
            G._gate_cpp_extension_receipt(
                root, caseset, envelope, evidence, errors,
                source_facts_path=os.path.join(root, "nope", "source_facts.json"))
            self.assertTrue(errors, "显式 --source-facts 指不到文件必须阻断")

    def test_pull_request_anchor_must_match_source_facts_too(self):
        """⭐ 拿得到对照物时，PR 通路的锚也要核——不能只有本地通路被查。

        `preflight_aclnn` 那条 head 校验比的是 `pr_facts ↔ source_facts`，
        **不是** `build receipt ↔ source_facts`：build 出来的 `.so` 对应哪个 commit，
        在这条改动之前没人核过。
        """
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self._write_source_facts(root, dut_source="pull_request", head_sha="b" * 40)
            errors = self._run(root, caseset, envelope, evidence)
            self.assertTrue(any("不相等" in e for e in errors), errors)

    def test_pull_request_anchor_matching_source_facts_passes(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            head = envelope["cpp_extension_receipt"]["vendor"]["build_receipt"][
                "source"]["pr_head_sha"]
            self._write_source_facts(root, dut_source="pull_request", head_sha=head.lower())
            self.assertEqual([], self._run(root, caseset, envelope, evidence))


if __name__ == "__main__":
    unittest.main()
