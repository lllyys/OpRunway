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


if __name__ == "__main__":
    unittest.main()
