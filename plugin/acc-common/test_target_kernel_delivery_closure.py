#!/usr/bin/env python3
"""目标 SoC kernel 交付闭环的行为测试。"""

import copy
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import vendor_build_receipt as V


SOC = "ascend910_93"
SELECTED_OP = "floor_mod"
OP_TYPE = "FloorMod"
KERNEL = "FloorMod_0123456789abcdef_high_performance"


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        h.update(src.read())
    return h.hexdigest()


class TargetKernelDeliveryClosureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.installed = os.path.join(self.root, "installed", "vendors", "fixture")
        self.package = os.path.join(self.root, "package", "vendors", "fixture")
        self.cache = os.path.join(self.root, "build", "CMakeCache.txt")
        os.makedirs(os.path.dirname(self.cache), exist_ok=True)
        with open(self.cache, "w", encoding="utf-8") as out:
            out.write(
                f"ASCEND_COMPUTE_UNIT:STRING={SOC}\n"
                f"ASCEND_OP_NAME:STRING={SELECTED_OP}\n")
        self.config_rel = os.path.join(
            "op_impl", "ai_core", "tbe", "config", SOC,
            f"aic-{SOC}-ops-info.json")
        self.metadata_rel = os.path.join(
            "op_impl", "ai_core", "tbe", "kernel", SOC,
            "ops_legacy", SELECTED_OP, KERNEL + ".json")
        self.object_rel = os.path.join(
            "op_impl", "ai_core", "tbe", "kernel", SOC,
            "ops_legacy", SELECTED_OP, KERNEL + ".o")
        config = {OP_TYPE: {"opFile": {"value": SELECTED_OP}}}
        metadata = {"binList": [{
            "binPath": KERNEL + ".o",
            "kernelList": [{"kernelName": KERNEL}],
        }]}
        for root in (self.installed, self.package):
            self._write_json(root, self.config_rel, config)
            self._write_json(root, self.metadata_rel, metadata)
            self._write(root, self.object_rel, b"synthetic-elf-object")
        self.argv = [
            "bash", "build.sh", f"--soc={SOC}", f"--ops={SELECTED_OP}"]

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, root, rel, payload):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as out:
            out.write(payload)
        return path

    def _write_json(self, root, rel, payload):
        return self._write(
            root, rel,
            (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode())

    def _build(self, **over):
        kwargs = dict(
            requested_soc=SOC,
            selected_op=SELECTED_OP,
            expected_op_type=OP_TYPE,
            installed_opp_root=self.installed,
            package_opp_root=self.package,
            cmake_cache_path=self.cache,
            build_argv=self.argv,
        )
        kwargs.update(over)
        with mock.patch.object(V, "_global_defined_symbols", return_value={KERNEL}):
            return V.build_target_kernel_delivery_closure(**kwargs)

    def _validate(self, closure, *, live=True):
        with mock.patch.object(V, "_global_defined_symbols", return_value={KERNEL}):
            return V.validate_target_kernel_delivery_closure(
                closure, build_argv=self.argv, live=live)

    def test_known_good_fixture_closes_config_metadata_object_symbol_and_package(self):
        closure = self._build()
        self.assertEqual(closure["schema"], "oprunway.target_kernel_delivery_closure")
        self.assertEqual(closure["schema_version"], 1)
        self.assertEqual(closure["status"], "VERIFIED")
        self.assertEqual(closure["request"]["requested_soc"], SOC)
        self.assertEqual(closure["request"]["expected_op_type"], OP_TYPE)
        self.assertEqual(len(closure["target_config"]["files"]), 1)
        self.assertEqual(len(closure["kernel_assets"]), 1)
        self.assertEqual(closure["kernel_assets"][0]["bins"][0]["kernel_names"], [KERNEL])
        self.assertEqual(self._validate(closure)["kernel_asset_count"], 1)

    def test_cann_901_top_level_bin_metadata_uses_kernel_list_symbols(self):
        """A3/CANN 9.0.1：顶层 kernelName 是 bin basename，不必是 ELF symbol。"""
        symbols = {KERNEL + "_0", KERNEL + "_2147483647"}
        metadata = {
            "binFileName": KERNEL,
            "binFileSuffix": ".o",
            "kernelName": KERNEL,
            "kernelList": [{"kernelName": name} for name in sorted(symbols)],
        }
        for root in (self.installed, self.package):
            self._write_json(root, self.metadata_rel, metadata)
        with mock.patch.object(V, "_global_defined_symbols", return_value=symbols):
            closure = V.build_target_kernel_delivery_closure(
                requested_soc=SOC, selected_op=SELECTED_OP,
                expected_op_type=OP_TYPE, installed_opp_root=self.installed,
                package_opp_root=self.package, cmake_cache_path=self.cache,
                build_argv=self.argv)
            self.assertEqual(
                closure["kernel_assets"][0]["bins"][0]["kernel_names"],
                sorted(symbols))
            self.assertNotIn(
                KERNEL, closure["kernel_assets"][0]["bins"][0]["kernel_names"])
            V.validate_target_kernel_delivery_closure(
                closure, build_argv=self.argv, live=True)

    def test_build_rc0_silent_skip_and_empty_target_config_are_fatal(self):
        for root in (self.installed, self.package):
            os.unlink(os.path.join(root, self.metadata_rel))
            os.unlink(os.path.join(root, self.object_rel))
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "metadata|kernel|目标"):
            self._build()
        self._write_json(self.installed, self.config_rel, {})
        self._write_json(self.package, self.config_rel, {})
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "ops-info|目标"):
            self._build()

    def test_unrelated_exact_soc_change_cannot_mask_selected_op_silent_skip(self):
        before = V.target_kernel_delivery.target_manifest(self.installed, SOC)
        unrelated = os.path.join(
            "op_impl", "ai_core", "tbe", "kernel", SOC,
            "ops_legacy", "another_op", "AnotherOp.o")
        self._write(self.installed, unrelated, b"unrelated build output")
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "UNCHANGED|未改变|陈旧"):
            self._build(target_assets_before=before)

    def test_config_or_metadata_only_change_cannot_mask_stale_kernel_object(self):
        for changed in ("config", "metadata"):
            with self.subTest(changed=changed):
                before = V.target_kernel_delivery.target_manifest(self.installed, SOC)
                rel = self.config_rel if changed == "config" else self.metadata_rel
                for root in (self.installed, self.package):
                    path = os.path.join(root, rel)
                    with open(path, encoding="utf-8") as src:
                        payload = json.load(src)
                    payload["freshness_probe"] = changed
                    self._write_json(root, rel, payload)
                with self.assertRaisesRegex(
                        V.VendorBuildReceiptError, "UNCHANGED|object|\.o|内核"):
                    self._build(target_assets_before=before)

    def test_wrong_soc_op_or_cmake_binding_is_fatal(self):
        for kwargs, code in (
                ({"requested_soc": "ascend910b"}, "TARGET_SOC_MISMATCH"),
                ({"selected_op": "remainder"}, "TARGET_OP_MISMATCH"),
                ({"expected_op_type": "Remainder"}, "OPS_INFO_MISSING")):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(V.VendorBuildReceiptError) as caught:
                    self._build(**kwargs)
                self.assertEqual(caught.exception.code, code)
        with open(self.cache, "w", encoding="utf-8") as out:
            out.write("ASCEND_COMPUTE_UNIT:STRING=ascend910b\n"
                      f"ASCEND_OP_NAME:STRING={SELECTED_OP}\n")
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "CMakeCache") as caught:
            self._build()
        self.assertEqual(caught.exception.code, "TARGET_SOC_MISMATCH")

    def test_op_type_mentioned_only_in_unrelated_config_value_is_not_an_op_entry(self):
        unrelated = {"Unrelated": {"note": OP_TYPE}}
        for root in (self.installed, self.package):
            self._write_json(root, self.config_rel, unrelated)
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "ops-info"):
            self._build()

    def test_package_unavailable_is_formally_ineligible(self):
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "package"):
            self._build(package_opp_root=None)
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "package|根|独立"):
            self._build(package_opp_root=self.installed)
        nested = os.path.join(self.installed, "nested-package")
        os.makedirs(nested, exist_ok=True)
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "package|根|独立"):
            self._build(package_opp_root=nested)
        for rel in (self.config_rel, self.metadata_rel, self.object_rel):
            package_path = os.path.join(self.package, rel)
            os.unlink(package_path)
            os.link(os.path.join(self.installed, rel), package_path)
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "inode|hardlink"):
            self._build()

    def test_symlink_and_escape_are_rejected(self):
        target = os.path.join(self.installed, self.object_rel)
        os.unlink(target)
        os.symlink(os.path.join(self.package, self.object_rel), target)
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "符号链接"):
            self._build()

        target = os.path.join(self.installed, self.metadata_rel)
        metadata = {"binList": [{
            "binPath": "../../../../../../../../outside.o",
            "kernelList": [{"kernelName": KERNEL}],
        }]}
        self._write_json(self.installed, self.metadata_rel, metadata)
        self._write_json(self.package, self.metadata_rel, metadata)
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "逃出|containment"):
            self._build()

    def test_missing_metadata_object_hash_or_symbol_is_rejected(self):
        metadata_path = os.path.join(self.installed, self.metadata_rel)
        with open(metadata_path, "rb") as src:
            metadata_bytes = src.read()
        os.unlink(metadata_path)
        with self.assertRaises(V.VendorBuildReceiptError):
            self._build()
        self._write(self.installed, self.metadata_rel, metadata_bytes)
        object_path = os.path.join(self.installed, self.object_rel)
        with open(object_path, "rb") as src:
            object_bytes = src.read()
        os.unlink(object_path)
        with self.assertRaises(V.VendorBuildReceiptError):
            self._build()
        self._write(self.installed, self.object_rel, object_bytes)

        closure = self._build()
        closure["kernel_assets"][0]["bins"][0]["installed_sha256"] = "0" * 64
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "sha256|摘要"):
            self._validate(closure, live=False)
        with mock.patch.object(V, "_global_defined_symbols", return_value=set()):
            with self.assertRaisesRegex(V.VendorBuildReceiptError, "全局符号|kernelName"):
                V.build_target_kernel_delivery_closure(
                    requested_soc=SOC, selected_op=SELECTED_OP,
                    expected_op_type=OP_TYPE, installed_opp_root=self.installed,
                    package_opp_root=self.package, cmake_cache_path=self.cache,
                    build_argv=self.argv)

    def test_consistent_tampering_is_caught_by_live_rehash(self):
        closure = self._build()
        installed_obj = os.path.join(self.installed, self.object_rel)
        self._write(self.installed, self.object_rel, b"tampered-both")
        forged = _sha(installed_obj)
        row = closure["kernel_assets"][0]["bins"][0]
        row["installed_sha256"] = forged
        row["package_sha256"] = forged
        closure["closure_sha256"] = V.target_kernel_delivery._canonical_sha(
            {k: v for k, v in closure.items() if k != "closure_sha256"})
        with self.assertRaisesRegex(
                V.VendorBuildReceiptError, "package|PACKAGE_INSTALL|manifest"):
            self._validate(closure, live=True)

    def test_live_metadata_identity_cannot_be_replaced_while_closure_keeps_old_bin(self):
        closure = self._build()
        forged_metadata = {"binList": [{
            "binPath": KERNEL + ".o",
            "kernelList": [{"kernelName": "OtherOp_kernel"}],
        }]}
        for root in (self.installed, self.package):
            self._write_json(root, self.metadata_rel, forged_metadata)
        digest = _sha(os.path.join(self.installed, self.metadata_rel))
        asset = closure["kernel_assets"][0]
        asset["metadata_installed_sha256"] = digest
        asset["metadata_package_sha256"] = digest
        for row in closure["target_manifest"]["files"]:
            if row["path"] == self.metadata_rel.replace(os.sep, "/"):
                row["sha256"] = digest
        closure["target_manifest"]["sha256"] = V.target_kernel_delivery._canonical_sha(
            closure["target_manifest"]["files"])
        closure["closure_sha256"] = V.target_kernel_delivery._canonical_sha(
            {key: value for key, value in closure.items() if key != "closure_sha256"})
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "metadata|op type|identity"):
            self._validate(closure, live=True)

    def test_mutations_of_required_closure_fields_fail_closed(self):
        base = self._build()
        mutations = (
            lambda x: x["request"].pop("requested_soc"),
            lambda x: x["target_config"].update(files=[]),
            lambda x: x.update(kernel_assets=[]),
            lambda x: x["kernel_assets"][0]["bins"][0].update(kernel_names=[]),
            lambda x: x["package"].update(status="unavailable"),
        )
        for mutate in mutations:
            candidate = copy.deepcopy(base)
            mutate(candidate)
            with self.subTest(candidate=candidate):
                with self.assertRaises(V.VendorBuildReceiptError):
                    self._validate(candidate, live=False)

    def test_malformed_nested_types_are_totalized_as_receipt_errors(self):
        base = self._build()
        mutations = (
            lambda value: value.update(target_config=["not-an-object"]),
            lambda value: value["kernel_assets"][0]["bins"][0].update(
                kernel_names=[[]]),
            lambda value: value["build_selection"].update(cmake_cache_sha256=1),
            lambda value: value["target_config"]["files"][0].update(
                installed_sha256=True),
            lambda value: value["kernel_assets"][0].update(
                metadata_installed_sha256=[]),
            lambda value: value["kernel_assets"][0]["bins"][0].update(
                installed_sha256=1),
            lambda value: value["target_manifest"]["files"][0].update(sha256=1),
            lambda value: value["target_manifest"]["files"][0].update(path=1),
        )
        for mutate in mutations:
            candidate = copy.deepcopy(base)
            mutate(candidate)
            candidate["closure_sha256"] = V.target_kernel_delivery._canonical_sha(
                {key: value for key, value in candidate.items()
                 if key != "closure_sha256"})
            with self.subTest(candidate=candidate):
                with self.assertRaises(V.VendorBuildReceiptError):
                    self._validate(candidate, live=False)

    def test_offline_validator_binds_manifest_hashes_and_expected_op_identity(self):
        base = self._build()
        forged_hash = copy.deepcopy(base)
        row = forged_hash["kernel_assets"][0]["bins"][0]
        row["installed_sha256"] = row["package_sha256"] = "a" * 64
        forged_hash["closure_sha256"] = V.target_kernel_delivery._canonical_sha(
            {key: value for key, value in forged_hash.items()
             if key != "closure_sha256"})
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "manifest|摘要|hash"):
            self._validate(forged_hash, live=False)

        forged_identity = copy.deepcopy(base)
        row = forged_identity["kernel_assets"][0]["bins"][0]
        row["kernel_names"] = row["global_symbols"] = ["OtherOp_kernel"]
        forged_identity["closure_sha256"] = V.target_kernel_delivery._canonical_sha(
            {key: value for key, value in forged_identity.items()
             if key != "closure_sha256"})
        with self.assertRaisesRegex(V.VendorBuildReceiptError, "op type|identity"):
            self._validate(forged_identity, live=False)


if __name__ == "__main__":
    unittest.main()
