#!/usr/bin/env python3
"""cpp_extension_driver 的纯静态 helper 测试；不 import torch、不 build。"""

import json
import os
import tempfile
import unittest
from unittest import mock

import cpp_extension_driver as D


class CppExtensionDriverStaticTest(unittest.TestCase):
    def test_canonical_digest_is_key_order_independent(self):
        self.assertEqual(D._canonical_sha({"a": 1, "b": 2}),
                         D._canonical_sha({"b": 2, "a": 1}))

    def test_safe_path_rejects_escape_and_absolute(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(D.DriverError):
                D._safe(td, "../x")
            with self.assertRaises(D.DriverError):
                D._safe(td, "/tmp/x")

    def test_vendor_path_is_explicit_and_must_exist(self):
        old = os.environ.get("OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY")
        try:
            os.environ.pop("OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY", None)
            with self.assertRaisesRegex(D.DriverError, "须指向"):
                D._require_env_path("OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY")
        finally:
            if old is not None:
                os.environ["OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY"] = old

    def test_vendor_build_receipt_binds_full_head_and_exact_elf(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = os.path.join(td, "libcust_opapi.so")
            with open(vendor, "wb") as dst:
                dst.write(b"vendor")
            receipt_path = os.path.join(td, "build-receipt.json")
            receipt = {
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
                    "library_path": vendor,
                    "library_sha256": D._sha_file(vendor),
                },
            }
            with open(receipt_path, "w", encoding="utf-8") as dst:
                json.dump(receipt, dst)
            with mock.patch.dict(
                    os.environ,
                    {"OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT":
                     receipt_path}):
                self.assertEqual(
                    D._vendor_build_provenance(vendor), receipt)

    def test_vendor_build_receipt_rejects_short_head(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = os.path.join(td, "lib.so")
            with open(vendor, "wb") as dst:
                dst.write(b"vendor")
            receipt_path = os.path.join(td, "receipt.json")
            with open(receipt_path, "w", encoding="utf-8") as dst:
                json.dump({
                    "schema": "oprunway.vendor_build_receipt",
                    "schema_version": 1,
                    "status": "VERIFIED",
                    "source": {"repo": "repo", "pr_head_sha": "a" * 7},
                    "build": {
                        "argv": ["build"], "cwd": "/work", "returncode": 0},
                    "artifact": {
                        "library_path": vendor,
                        "library_sha256": D._sha_file(vendor),
                    },
                }, dst)
            with mock.patch.dict(
                    os.environ,
                    {"OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT":
                     receipt_path}):
                # 收据自称 `gitcode_pr` 档（schema_version=1 的旧收据无 provenance_kind，
                # 按 PR 档解释），却只绑了个 7 位短 head——40 位是这一档的硬要求，短一位
                # 都不算绑上。断言串跟住 `vendor_build_receipt._validate_source` 的措辞。
                with self.assertRaisesRegex(
                        D.DriverError, "缺完整 PR head/source repo"):
                    D._vendor_build_provenance(vendor)

    def _write_receipt(self, td, receipt):
        path = os.path.join(td, "receipt.json")
        with open(path, "w", encoding="utf-8") as dst:
            json.dump(receipt, dst)
        return path

    def _local_snapshot_receipt(self, vendor):
        """无 `.git` 的本地快照：没有 PR head 可绑，改绑仓根 + 子目录 scope + 两个 merkle。"""
        return {
            "schema": "oprunway.vendor_build_receipt",
            "schema_version": 2,
            "status": "VERIFIED",
            "degradations": ["pr_head_unbound"],
            "source": {
                "provenance_kind": "local_snapshot",
                "repo": "repos/ops-witness-local-snapshot",
                "pr_head_sha": None,
                "snapshot_subtree_scope": "witness_op",
                "snapshot_sha256": "c" * 64,
                "snapshot_subtree_sha256": "d" * 64,
            },
            "build": {"argv": ["bash", "build.sh"], "cwd": "/w", "returncode": 0},
            "artifact": {
                "library_path": vendor,
                "library_sha256": D._sha_file(vendor),
            },
        }

    def test_local_snapshot_receipt_is_accepted_with_explicit_degradation(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = os.path.join(td, "lib.so")
            with open(vendor, "wb") as dst:
                dst.write(b"vendor")
            receipt = self._local_snapshot_receipt(vendor)
            path = self._write_receipt(td, receipt)
            with mock.patch.dict(
                    os.environ,
                    {"OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT": path}):
                self.assertEqual(D._vendor_build_provenance(vendor), receipt)
        summary = D.vendor_build_receipt.summarize(receipt)
        self.assertIsNone(summary["pr_head_sha"])
        self.assertEqual(summary["provenance_kind"], "local_snapshot")
        self.assertEqual(summary["degradations"], ["pr_head_unbound"])

    def test_local_snapshot_may_not_fabricate_a_pr_head(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = os.path.join(td, "lib.so")
            with open(vendor, "wb") as dst:
                dst.write(b"vendor")
            receipt = self._local_snapshot_receipt(vendor)
            receipt["source"]["pr_head_sha"] = "e" * 40
            path = self._write_receipt(td, receipt)
            with mock.patch.dict(
                    os.environ,
                    {"OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT": path}):
                with self.assertRaisesRegex(D.DriverError, "捏造 PR head"):
                    D._vendor_build_provenance(vendor)

    def test_local_snapshot_must_account_the_degradation(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = os.path.join(td, "lib.so")
            with open(vendor, "wb") as dst:
                dst.write(b"vendor")
            receipt = self._local_snapshot_receipt(vendor)
            del receipt["degradations"]
            path = self._write_receipt(td, receipt)
            with mock.patch.dict(
                    os.environ,
                    {"OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT": path}):
                with self.assertRaisesRegex(D.DriverError, "degradations"):
                    D._vendor_build_provenance(vendor)

    def test_perf_plan_must_bind_exact_caseset_and_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            caseset = {"op": "X", "cases": []}
            receipt = {
                "artifact": {"path": "cpp_extension/x.so", "sha256": "0" * 64},
                "load": {"namespace": "oprunway_x"},
            }
            plan = {
                "caseset_sha256": "f" * 64,
                "cpp_extension_receipt_sha256": D._canonical_sha(receipt),
            }
            for name, value in (
                    ("cpp_extension_caseset.json", caseset),
                    ("cpp_extension_receipt.json", receipt),
                    ("cpp_extension_perf_plan.json", plan)):
                with open(os.path.join(td, name), "w", encoding="utf-8") as dst:
                    json.dump(value, dst)
            with self.assertRaisesRegex(D.DriverError, "绑定漂移"):
                D.run_perf_only(td, td)


class CustomOppBindingTest(unittest.TestCase):
    """自定义算子符号来源必须由收据绑定，而不是由「谁 source 过 set_env.bash」决定。

    这批守的是一个**可复现性**缺陷：改动前 driver 从不设 `ASCEND_CUSTOM_OPP_PATH`，
    干净现场里 164 条 case 全部 `execution_failed`（`aclnnXxx ... not in libopapi.so`），
    而同一份 codegen 产物在人手动 source 过 vendor set_env.bash 的现场里却「跑通了」。
    成功依赖了一份没有被任何产物记录的环境状态 = 结果不可复现。
    """

    _ENV = "ASCEND_CUSTOM_OPP_PATH"

    def _lib(self, root, pkg="oprunway_witness"):
        """按 CANN 自定义算子包布局造一个见证 `.so`：<root>/vendors/<pkg>/op_api/lib/…"""
        pkg_dir = os.path.join(root, "vendors", pkg)
        lib_dir = os.path.join(pkg_dir, "op_api", "lib")
        os.makedirs(lib_dir)
        lib = os.path.join(lib_dir, "libcust_opapi.so")
        with open(lib, "wb") as dst:
            dst.write(b"vendor")
        return os.path.realpath(pkg_dir), os.path.realpath(lib)

    def test_binds_package_derived_from_receipt_bound_library(self):
        with tempfile.TemporaryDirectory() as td:
            pkg, lib = self._lib(td)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(self._ENV, None)
                self.assertEqual(D.bind_custom_opp_path(lib), pkg)
                self.assertEqual(os.environ[self._ENV], pkg)

    def test_layout_mismatch_is_fail_closed(self):
        """路径结构对不上不猜一个根出来——猜错就等于测在别的包上还说不出来。"""
        with tempfile.TemporaryDirectory() as td:
            stray = os.path.join(td, "lib.so")
            with open(stray, "wb") as dst:
                dst.write(b"vendor")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(self._ENV, None)
                with self.assertRaises(D.DriverError):
                    D.bind_custom_opp_path(stray)

    def test_conflicting_preexisting_value_is_fail_closed(self):
        """环境里已指着别的 vendor 包 → 拒，不覆盖也不追加（防跑在别人的符号上）。"""
        with tempfile.TemporaryDirectory() as td:
            _pkg, lib = self._lib(td)
            other, _ = self._lib(td, pkg="stale_other")
            with mock.patch.dict(os.environ, {self._ENV: other}):
                with self.assertRaisesRegex(D.DriverError, "不一致"):
                    D.bind_custom_opp_path(lib)

    def test_already_bound_to_same_package_is_accepted(self):
        """人先 source 过本轮 vendor 的 set_env.bash（值含尾随冒号）→ 同一个包，放行并归一化。"""
        with tempfile.TemporaryDirectory() as td:
            pkg, lib = self._lib(td)
            with mock.patch.dict(os.environ, {self._ENV: pkg + os.pathsep}):
                self.assertEqual(D.bind_custom_opp_path(lib), pkg)
                self.assertEqual(os.environ[self._ENV], pkg)


if __name__ == "__main__":
    unittest.main()
