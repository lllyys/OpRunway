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


def source_facts_payload(provenance_kind="local_snapshot",
                         snapshot_merkle="c" * 64, snapshot_scope="op",
                         head_sha="a" * 40, completeness=None):
    """一份**过完整契约**的 `source_facts` payload（供本文件与渲染器单测共用）。

    ⚠ 不能只塞一个摘要：三级门复用 `validate_preparation_state._validate_source_payload`
    校这份对照物——digest 自洽只证明 payload 没被改过，证明不了它是一份完整、未降级的取材产物。
    最小 payload 也能自洽（`make_artifact` 谁都能调），所以契约那一层必须真的过。

    词表按 `source_provenance`：顶层 `declared_source_form ∈ {git_pr, local_source}`（**声明**），
    `pr.provenance_kind ∈ {gitcode_pr, local_snapshot}`（**实得**）。本地档的 `head_sha`
    是**显式 null**——本形态没有上游 commit，合成一个 40 位 hex 就是捏造（AGENTS.md 5.8）。
    """
    import source_provenance as SP
    is_local = provenance_kind == SP.PROVENANCE_LOCAL_SNAPSHOT
    if is_local:
        form = SP.FORM_LOCAL_SOURCE
        form_facts = list(SP.LOCAL_SOURCE_FORM_FACTS)
        pr = {"canonical_url": None, "source_repo": None, "number": None,
              "head_sha": None, "head_repo": None, "is_fork": None, "state": None,
              "provenance_kind": provenance_kind,
              # merkle 与 scope **成对**：没有范围的 merkle 与真机 build 侧不可比。
              "snapshot_merkle_sha256": snapshot_merkle,
              "snapshot_scope": snapshot_scope}
        # 本地档没有 base，`changed_files` 是「该子树下的全部文件」而非 PR diff；
        # 这件事由 `completeness.form_facts` 如实声明，不是降级。
        key_ref = SP.PROVENANCE_LOCAL_SNAPSHOT
    else:
        form = SP.FORM_GIT_PR
        form_facts = []
        pr = {"canonical_url": "https://gitcode.com/o/r/pull/1",
              "source_repo": "o/r", "number": 1, "head_sha": head_sha,
              "head_repo": "o/r", "is_fork": False, "state": "open",
              "provenance_kind": provenance_kind,
              # PR 档这两项**恒在场且为 None**（不是缺席）——反向排他校验比的是「显式 null」。
              "snapshot_merkle_sha256": None, "snapshot_scope": None}
        key_ref = head_sha
    return {
        "contract_version": 1,
        "declared_source_form": form,
        "taskdoc": {"bytes_sha256": "1" * 64, "snapshot_sha256": "1" * 64,
                    "size": 12, "source_locator": "task.md"},
        "pr": pr,
        "changed_files": ["op/x.h"],
        "key_files": [{"path": "op/x.h", "ref": key_ref,
                       "bytes_sha256": "2" * 64, "size": 9}],
        "derived": {"op": "X", "target_dir": "op", "aclnn_headers": ["op/x.h"],
                    "interface_kind": "aclnn_2stage", "aclnn_entry": "aclnnX"},
        "completeness": (completeness if completeness is not None
                         else {"status": "complete", "reasons": [],
                               "form_facts": form_facts}),
        "producer": {"tool": "fetch_source.py", "logic_sha256": "3" * 64},
    }


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
            # stage2_form 是 2026-08-05 新增的必填项：形态必须**可派发**，不许由 codegen 默认猜
            # 「标准 4 参」——GaussianBlur 的 extended 10 参 stage2 正是被那个默认猜法坑到的。
            "variants": [{"entrypoint": "invoke_v0", "stage2_form": "standard"}],
            # 2026-08-05：stage2 形态证据台账。空列表 = 无降级 = 形态已由 header/预检核过。
            # 缺这个键**不是**「没降级」而是「没人核过」，门会据此拒（见
            # validate_acceptance_state._gate_cpp_extension_stage2_evidence）——旧 manifest 因此失效，
            # 这是有意的 fail-closed 方向，不是回归。
            "degradations": [],
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
        # 2026-08-05：vendor `.so` 必须坐在 CANN 自定义算子包的安装布局里
        # （`<root>/vendors/<pkg>/op_api/lib/<lib>.so`）——门要从这条路径反推
        # `ASCEND_CUSTOM_OPP_PATH`，也就是「本轮 aclnnXxx 由哪个包提供」。反推不出来 =
        # 符号来源不可核，fail-closed。改动前这里写的 `/opt/vendor/lib.so` 不符合布局。
        vendor_pkg = "/opt/vendor_root/vendors/oprunway_test"
        vendor_path = vendor_pkg + "/op_api/lib/libcust_opapi.so"
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
                # driver 在任何算子调用前实际设入进程环境的自定义算子包（不再依赖谁 source 过
                # vendor 的 set_env.bash）。门按同一条布局规则从 vendor.library_path 重算对账。
                "ascend_custom_opp_path": vendor_pkg,
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
            # 2026-08-05：receipt 按 source 分流后，文案由「PR head→构建→安装 ELF」泛化成
            # 「源身份→构建→安装 ELF」（同一条链现在还要覆盖 local_snapshot 档）。
            # ⚠ 断言只跟着改文案是不够的——**这条守的是 fail-open**，所以同时钉死三件事：
            #   ① 确实被拒（errors 非空）；② 拒的理由确实是源身份绑定；
            #   ③ 错误里逐字点出那个截断的假 head，防止「因为别的原因恰好也报错」而假绿。
            self.assertTrue(errors, "截断的 7 位假 head 必须被拒，不得放行")
            self.assertTrue(any("源身份→构建→安装 ELF" in e for e in errors), errors)
            self.assertTrue(any("aaaaaaa" in e for e in errors), errors)

    def test_rejects_symbol_source_drift_from_vendor_library(self):
        """收据自报的符号来源包 ≠ 从 vendor ELF 反推的那个 → 拒。

        守的是这一类假象：精度阶段实际把 `ASCEND_CUSTOM_OPP_PATH` 指向了**另一个** vendor
        包（环境里继承来的上次安装），符号从那儿解析，报告写的却是本轮 PR 的身份。
        """
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = self._fixture(root)
            receipt = envelope["cpp_extension_receipt"]
            receipt["runtime"]["ascend_custom_opp_path"] = "/opt/other_root/vendors/stale_pkg"
            evidence[0]["cpp_extension_receipt_sha256"] = G._canonical_sha(receipt)
            errors = []
            G._gate_cpp_extension_receipt(
                root, caseset, envelope, evidence, errors)
            self.assertTrue(any("ascend_custom_opp_path" in e for e in errors), errors)

    def test_rejects_missing_symbol_source(self):
        """整个字段缺席 = 没人记「符号从哪来」。旧收据因此失效，这是有意的 fail-closed。"""
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = self._fixture(root)
            receipt = envelope["cpp_extension_receipt"]
            del receipt["runtime"]["ascend_custom_opp_path"]
            evidence[0]["cpp_extension_receipt_sha256"] = G._canonical_sha(receipt)
            errors = []
            G._gate_cpp_extension_receipt(
                root, caseset, envelope, evidence, errors)
            self.assertTrue(any("runtime provenance 不完整" in e for e in errors), errors)

    def test_rejects_vendor_library_outside_custom_opp_layout(self):
        """vendor `.so` 不在自定义算子包布局里 → 反推不出来源包 → 拒，不猜一个根出来。"""
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = self._fixture(root)
            receipt = envelope["cpp_extension_receipt"]
            receipt["vendor"]["library_path"] = "/opt/vendor/lib.so"
            receipt["runtime"]["ascend_custom_opp_path"] = "/opt/vendor"
            evidence[0]["cpp_extension_receipt_sha256"] = G._canonical_sha(receipt)
            errors = []
            G._gate_cpp_extension_receipt(
                root, caseset, envelope, evidence, errors)
            self.assertTrue(any("反推自定义算子包失败" in e for e in errors), errors)


class BuildReceiptSourceBindingTest(unittest.TestCase):
    """build receipt 的源锚 ↔ `source_facts` 的源锚：**三级门里唯一**核这一对的地方。

    ⚠ 合并记账（2026-08-06）：本类原名 `LocalCheckoutSourceBindingTest`，钉的是已废弃的
    `dut_source` / `local_root_digest` 词表。判别式已整份换成 `source_provenance` 的
    「声明 `declared_source_form` × 实得 `provenance_kind`」两轴模型，故用例逐条改绑新词表：

      · `gitcode_pr`    ：收据 `pr_head_sha` ↔ `source_facts.pr.head_sha`；
      · `local_snapshot`：**两侧字段不同名**——收据 `snapshot_subtree_sha256` ↔ 事实包
        `snapshot_merkle_sha256`（intake 只产一个、范围由 `--target-dir` 决定；收据产两个，
        可比的是**子树**那个），并先核 `snapshot_subtree_scope` ↔ `snapshot_scope` 相等。

    ⚠ 断言只钉「被拒 / 不被拒」与语义分支，**不钉具体中文措辞**：错误串由
    `validate_acceptance_state._gate_build_receipt_source_binding` 决定，措辞收紧不该让本类变红。
    """

    MERKLE = "c" * 64
    SCOPE = "op"

    def _relocalize(self, envelope, evidence, *, subtree=MERKLE, scope=SCOPE,
                    whole="e" * 64, form="local_source", degradations=None):
        """把 fixture 的 build receipt 从 PR 形态改成本地快照形态，并重算受影响的摘要。"""
        vendor = envelope["cpp_extension_receipt"]["vendor"]
        br = vendor["build_receipt"]
        br["schema_version"] = 2          # v1 恒等于 gitcode_pr，按形态分流必须升版
        br["source"] = {
            "provenance_kind": "local_snapshot",
            "declared_source_form": form,
            "repo": "/local/ops-nn",
            # 本地快照没有上游 commit：**显式 null**，缺席不算（缺席 = 没人说过）。
            "pr_head_sha": None,
            "snapshot_subtree_scope": scope,
            "snapshot_sha256": whole,
            "snapshot_subtree_sha256": subtree,
        }
        # 声明 local_source + 实得 local_snapshot = 声明即所得，**必须无降级**。
        br["degradations"] = [] if degradations is None else degradations
        vendor["build_receipt_sha256"] = G._canonical_sha(br)
        evidence[0]["cpp_extension_receipt_sha256"] = G._canonical_sha(
            envelope["cpp_extension_receipt"])

    @classmethod
    def _write_source_facts(cls, root, *, sub=None, **kw):
        """⚠ 必须写**真** content_address envelope（digest 由 payload 算出）+ **完整契约 payload**。

        手拼 `{"domain":…, "payload":…}` 或只塞一个摘要的最小 payload，都会被查找侧判 UNTRUSTED
        ——那样这些用例名义上在测「锚对不对得上」，实际全落在「对照物不可信」那条分支上。
        """
        import content_address
        kw.setdefault("snapshot_merkle", cls.MERKLE)
        kw.setdefault("snapshot_scope", cls.SCOPE)
        parts = [root] + ([sub] if sub else []) + ["source_facts.json"]
        _write_json(os.path.join(*parts), content_address.make_artifact(
            "oprunway/source-facts/v1", source_facts_payload(**kw)))

    def _run(self, root, caseset, envelope, evidence):
        errors = []
        G._gate_cpp_extension_receipt(root, caseset, envelope, evidence, errors)
        return errors

    def test_matching_local_subtree_merkle_passes(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self._relocalize(envelope, evidence)
            self._write_source_facts(root)
            self.assertEqual([], self._run(root, caseset, envelope, evidence))

    def test_subtree_merkle_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self._relocalize(envelope, evidence)
            self._write_source_facts(root, snapshot_merkle="d" * 64)   # 与收据不等
            self.assertTrue(self._run(root, caseset, envelope, evidence),
                            "子树 merkle 对不上必须阻断")

    def test_scope_mismatch_is_blocked(self):
        """⭐ 范围不同的两个 merkle **不可比**：对上了是巧合，对不上也说不清是字节变了还是范围变了。"""
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self._relocalize(envelope, evidence, scope="other/op")
            self._write_source_facts(root)                              # scope="op"
            self.assertTrue(self._run(root, caseset, envelope, evidence),
                            "两侧 snapshot scope 不同必须阻断，不得只比摘要")

    def test_local_receipt_without_source_facts_is_blocked(self):
        """⭐ 本地锚的可信度**全部**来自等值校验——没有对照物就等于没绑定。"""
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self._relocalize(envelope, evidence)
            self.assertTrue(self._run(root, caseset, envelope, evidence),
                            "本地档拿不到 source_facts 必须 BLOCKED")

    def test_receipt_cannot_disguise_local_source_as_pull_request(self):
        """⭐ 绕过路径：source_facts 说 local_snapshot，收据说 gitcode_pr + 随便填 40 位 hex。

        若不先核「两边 provenance_kind 一致」，校验就会走进 PR 分支，
        子树 merkle 那条等值校验**根本不会执行** → 绑定完全失效。
        """
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            # 收据保持 PR 形态（fixture 默认就是），source_facts 声明 local_snapshot
            self._write_source_facts(root)
            self.assertTrue(self._run(root, caseset, envelope, evidence),
                            "来源身份被伪装必须阻断")

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

        不复算 digest 的话，手写一份「只有一个与恶意收据同值的 merkle」的最小 JSON
        就能当本地来源的信任锚——本地锚的全部可信度就来自这条对账。
        """
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            self._relocalize(envelope, evidence)
            self._write_source_facts(root, snapshot_merkle="d" * 64)
            path = os.path.join(root, "source_facts.json")
            with open(path, encoding="utf-8") as src:
                doc = json.load(src)
            doc["payload"]["pr"]["snapshot_merkle_sha256"] = self.MERKLE   # 改成对上
            _write_json(path, doc)                                         # digest 不动
            self.assertTrue(self._run(root, caseset, envelope, evidence),
                            "被篡改的对照物必须整份判不可信，而不是当成锚对上了")

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
            self._write_source_facts(
                root, provenance_kind="gitcode_pr", head_sha="b" * 40)
            self.assertTrue(self._run(root, caseset, envelope, evidence),
                            "PR head 与 source_facts 不等必须阻断")

    def test_pull_request_anchor_matching_source_facts_passes(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, envelope, evidence, _ = CppExtensionReceiptGateTest()._fixture(root)
            head = envelope["cpp_extension_receipt"]["vendor"]["build_receipt"][
                "source"]["pr_head_sha"]
            self._write_source_facts(
                root, provenance_kind="gitcode_pr", head_sha=head.lower())
            self.assertEqual([], self._run(root, caseset, envelope, evidence))


if __name__ == "__main__":
    unittest.main()
