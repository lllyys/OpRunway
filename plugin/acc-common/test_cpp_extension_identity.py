#!/usr/bin/env python3
"""cpp_extension 双符号实际定义 ELF 身份门（A3 上纯 CPU/动态链接器测试）。"""

import copy
import os
import subprocess
import tempfile
import unittest

import cpp_extension_identity as I


def _plan(*symbols):
    return {
        "schema": "oprunway.cpp_extension_invocation_plan",
        "schema_version": 1,
        "cases": [
            {"case_id": f"c{index}", "symbol": symbol}
            for index, symbol in enumerate(symbols)
        ],
    }


class RequiredSymbolsTest(unittest.TestCase):
    def test_derives_workspace_and_stage2_without_op_special_case(self):
        self.assertEqual(I.required_symbols(_plan("Zulu", "Alpha", "Zulu")), [
            {"entrypoint": "Alpha", "role": "workspace",
             "symbol": "aclnnAlphaGetWorkspaceSize"},
            {"entrypoint": "Alpha", "role": "stage2", "symbol": "aclnnAlpha"},
            {"entrypoint": "Zulu", "role": "workspace",
             "symbol": "aclnnZuluGetWorkspaceSize"},
            {"entrypoint": "Zulu", "role": "stage2", "symbol": "aclnnZulu"},
        ])

    def test_rejects_missing_cases_or_prefixed_symbol(self):
        for bad in ({}, {"cases": []}, {"cases": [{"symbol": "aclnnWitness"}]},
                    {"cases": [{"symbol": "bad-name"}]}):
            with self.subTest(bad=bad), self.assertRaises(I.CppExtensionIdentityError):
                I.required_symbols(bad)


class RuntimeIdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _compile(self, name, source, *extra):
        c_path = os.path.join(self.root, name + ".c")
        so_path = os.path.join(self.root, "lib" + name + ".so")
        with open(c_path, "w", encoding="utf-8") as out:
            out.write(source)
        subprocess.run(
            ["cc", "-shared", "-fPIC", c_path, "-o", so_path, *extra],
            check=True, cwd=self.root, capture_output=True, text=True)
        return so_path

    def test_attests_both_symbols_and_exact_defining_elf(self):
        vendor = self._compile(
            "vendor",
            "void aclnnWitness(void) {}\n"
            "void aclnnWitnessGetWorkspaceSize(void) {}\n")
        _handle, receipt = I.attest(vendor, _plan("Witness"))
        summary = I.validate(
            receipt, invocation_plan=_plan("Witness"),
            library_path=os.path.realpath(vendor),
            library_sha256=I.file_sha256(vendor))
        self.assertEqual(summary["symbols"], [
            "aclnnWitnessGetWorkspaceSize", "aclnnWitness"])
        for item in receipt["definitions"]:
            self.assertEqual(item["defining_library"], receipt["library"])

    def test_missing_workspace_symbol_fails_before_any_invocation(self):
        vendor = self._compile("stage2_only", "void aclnnWitness(void) {}\n")
        with self.assertRaisesRegex(I.CppExtensionIdentityError, "workspace"):
            I.attest(vendor, _plan("Witness"))

    def test_symbol_resolved_from_dependency_is_rejected_as_pollution(self):
        dep = self._compile(
            "dep",
            "void aclnnWitness(void) {}\n"
            "void aclnnWitnessGetWorkspaceSize(void) {}\n")
        self.assertTrue(os.path.isfile(dep))
        vendor = self._compile(
            "dependent_vendor",
            "extern void aclnnWitness(void);\n"
            "void keep_dependency(void) { aclnnWitness(); }\n",
            "-L" + self.root, "-ldep", "-Wl,-rpath,$ORIGIN", "-Wl,--no-as-needed")
        with self.assertRaisesRegex(I.CppExtensionIdentityError, "污染"):
            I.attest(vendor, _plan("Witness"))

    def test_offline_validator_rejects_forged_definition_or_missing_stage2(self):
        vendor = self._compile(
            "vendor2",
            "void aclnnWitness(void) {}\n"
            "void aclnnWitnessGetWorkspaceSize(void) {}\n")
        _handle, receipt = I.attest(vendor, _plan("Witness"))
        kwargs = {
            "invocation_plan": _plan("Witness"),
            "library_path": os.path.realpath(vendor),
            "library_sha256": I.file_sha256(vendor),
        }
        forged = copy.deepcopy(receipt)
        forged["definitions"][0]["defining_library"]["sha256"] = "0" * 64
        with self.assertRaises(I.CppExtensionIdentityError):
            I.validate(forged, **kwargs)
        missing = copy.deepcopy(receipt)
        missing["definitions"].pop()
        with self.assertRaisesRegex(I.CppExtensionIdentityError, "完整覆盖"):
            I.validate(missing, **kwargs)


class DistinctLibraryTest(unittest.TestCase):
    def test_rejects_same_path_or_same_fingerprint(self):
        dut = {"library": {"path": "/dut/lib.so", "sha256": "a" * 64}}
        for path, sha in (("/dut/lib.so", "b" * 64), ("/other/lib.so", "a" * 64)):
            with self.subTest(path=path, sha=sha), \
                    self.assertRaises(I.CppExtensionIdentityError):
                I.assert_distinct_library(dut, path, sha)
        self.assertTrue(I.assert_distinct_library(
            dut, "/baseline/lib.so", "b" * 64))


class RequiredSymbolLibraryTest(unittest.TestCase):
    def _provenance(self):
        library = {"path": "/cann/lib64/libopapi.so", "sha256": "b" * 64}
        common = {
            "source": "required_symbol_lib",
            "resolved_via": library["path"],
            "defining_lib": library["path"],
            "defining_lib_verified": True,
            "lib": library["path"],
            "lib_sha256": library["sha256"],
        }
        return {
            "required_symbol_lib": library,
            "symbols": [
                {**common, "symbol": "aclnnWitness"},
                {**common, "symbol": "aclnnWitnessGetWorkspaceSize"},
            ],
        }

    def test_accepts_exact_two_stage_baseline_identity(self):
        identity = I.validate_required_symbol_library(
            self._provenance(), expected_entrypoint="Witness")
        self.assertEqual(identity["entrypoint"], "Witness")
        self.assertEqual(identity["symbols"], [
            "aclnnWitnessGetWorkspaceSize", "aclnnWitness"])

    def test_rejects_wrong_fingerprint_missing_symbol_and_wrong_pair(self):
        mutations = []
        forged = self._provenance()
        forged["symbols"][0]["lib_sha256"] = "c" * 64
        mutations.append(forged)
        missing = self._provenance()
        missing["symbols"].pop()
        mutations.append(missing)
        wrong_pair = self._provenance()
        wrong_pair["symbols"][1]["symbol"] = "aclnnOtherGetWorkspaceSize"
        mutations.append(wrong_pair)
        for bad in mutations:
            with self.subTest(bad=bad), self.assertRaises(I.CppExtensionIdentityError):
                I.validate_required_symbol_library(bad, expected_entrypoint="Witness")


if __name__ == "__main__":
    unittest.main()
