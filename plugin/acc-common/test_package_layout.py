#!/usr/bin/env python3
"""CPack package OPP 根解析器的行为测试（纯文件系统，不碰 DUT/NPU）。"""

import json
import os
import tempfile
import unittest

import package_layout as P


VENDOR = "fixture_vendor_math"
OP_TYPE = "FixtureOp"


class PackageOppRootResolverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _candidate(self, parent, *, vendor=VENDOR, op_type=OP_TYPE):
        candidate = os.path.join(self.root, parent, "packages", "vendors", vendor)
        os.makedirs(os.path.join(candidate, "op_api", "lib"), exist_ok=True)
        config = os.path.join(
            candidate, "op_impl", "ai_core", "tbe", "config",
            "ascend_fixture", "fixture-ops-info.json")
        os.makedirs(os.path.dirname(config), exist_ok=True)
        with open(config, "w", encoding="utf-8") as out:
            json.dump({op_type: {"opFile": "fixture_op"}}, out)
        return candidate

    def test_one_exact_candidate_returns_its_canonical_absolute_root(self):
        candidate = self._candidate("_CPack_Packages/Linux/External/run-a")
        resolved = P.resolve_package_opp_root(self.root, VENDOR, OP_TYPE)
        self.assertEqual(resolved, os.path.realpath(candidate))
        self.assertTrue(os.path.isabs(resolved))

    def test_cpack_parent_name_is_not_part_of_the_contract(self):
        candidate = self._candidate("alternate/staging/tree")
        self.assertEqual(
            P.resolve_package_opp_root(self.root, VENDOR, OP_TYPE),
            os.path.realpath(candidate))

    def test_zero_exact_candidate_fails_closed(self):
        with self.assertRaisesRegex(P.PackageLayoutError, "PACKAGE_OPP_ROOT_MISSING"):
            P.resolve_package_opp_root(self.root, VENDOR, OP_TYPE)

    def test_multiple_exact_candidates_fail_with_sorted_evidence(self):
        second = self._candidate("z/run")
        first = self._candidate("a/run")
        with self.assertRaises(P.PackageLayoutError) as caught:
            P.resolve_package_opp_root(self.root, VENDOR, OP_TYPE)
        message = str(caught.exception)
        self.assertIn("PACKAGE_OPP_ROOT_AMBIGUOUS", message)
        self.assertLess(message.index(os.path.realpath(first)),
                        message.index(os.path.realpath(second)))

    def test_non_directory_or_symlink_search_root_is_rejected(self):
        file_root = os.path.join(self.root, "not-a-directory")
        with open(file_root, "w", encoding="utf-8") as out:
            out.write("x")
        with self.assertRaisesRegex(P.PackageLayoutError, "PACKAGE_SEARCH_ROOT_UNAVAILABLE"):
            P.resolve_package_opp_root(file_root, VENDOR, OP_TYPE)
        real_root = os.path.join(self.root, "real-search-root")
        os.makedirs(real_root)
        link_root = os.path.join(self.root, "linked-search-root")
        os.symlink(real_root, link_root)
        with self.assertRaisesRegex(P.PackageLayoutError, "PACKAGE_SEARCH_ROOT_SYMLINK"):
            P.resolve_package_opp_root(link_root, VENDOR, OP_TYPE)
        with self.assertRaisesRegex(P.PackageLayoutError, "PACKAGE_SEARCH_ROOT_UNAVAILABLE"):
            P.resolve_package_opp_root("relative/search", VENDOR, OP_TYPE)

    def test_candidate_symlink_and_nested_escape_are_rejected(self):
        vendor_parent = os.path.join(self.root, "candidate-link", "packages", "vendors")
        os.makedirs(vendor_parent, exist_ok=True)
        with tempfile.TemporaryDirectory() as outside_root:
            outside = os.path.join(outside_root, VENDOR)
            os.makedirs(outside)
            os.symlink(outside, os.path.join(vendor_parent, VENDOR))
            with self.assertRaisesRegex(P.PackageLayoutError, "PACKAGE_LAYOUT_SYMLINK"):
                P.resolve_package_opp_root(self.root, VENDOR, OP_TYPE)

        os.unlink(os.path.join(vendor_parent, VENDOR))
        candidate = self._candidate("candidate-link")
        config_root = os.path.join(candidate, "op_impl", "ai_core", "tbe", "config")
        for base, dirs, files in os.walk(config_root, topdown=False):
            for name in files:
                os.unlink(os.path.join(base, name))
            for name in dirs:
                os.rmdir(os.path.join(base, name))
        os.rmdir(config_root)
        external_config = os.path.join(self.root, "external-config")
        os.makedirs(external_config)
        os.symlink(external_config, config_root)
        with self.assertRaisesRegex(P.PackageLayoutError, "PACKAGE_LAYOUT_SYMLINK"):
            P.resolve_package_opp_root(self.root, VENDOR, OP_TYPE)

    def test_exact_vendor_candidate_that_is_not_a_directory_is_rejected(self):
        parent = os.path.join(self.root, "not-a-directory", "packages", "vendors")
        os.makedirs(parent)
        with open(os.path.join(parent, VENDOR), "w", encoding="utf-8") as out:
            out.write("not a package root")
        with self.assertRaisesRegex(P.PackageLayoutError, "PACKAGE_CANDIDATE_INVALID"):
            P.resolve_package_opp_root(self.root, VENDOR, OP_TYPE)

    def test_wrong_vendor_or_wrong_op_is_not_an_exact_candidate(self):
        self._candidate("wrong-vendor", vendor="another_vendor")
        with self.assertRaisesRegex(P.PackageLayoutError, "PACKAGE_OPP_ROOT_MISSING"):
            P.resolve_package_opp_root(self.root, VENDOR, OP_TYPE)
        self._candidate("wrong-op", op_type="AnotherOp")
        with self.assertRaisesRegex(P.PackageLayoutError, "PACKAGE_OPP_ROOT_MISSING"):
            P.resolve_package_opp_root(self.root, VENDOR, OP_TYPE)

    def test_invalid_op_identity_and_malformed_ops_info_fail_closed(self):
        with self.assertRaisesRegex(P.PackageLayoutError, "EXPECTED_OP_INVALID"):
            P.resolve_package_opp_root(self.root, VENDOR, "")
        candidate = self._candidate("malformed")
        config = os.path.join(
            candidate, "op_impl", "ai_core", "tbe", "config",
            "ascend_fixture", "fixture-ops-info.json")
        with open(config, "w", encoding="utf-8") as out:
            out.write("{")
        with self.assertRaisesRegex(P.PackageLayoutError, "PACKAGE_CANDIDATE_INVALID"):
            P.resolve_package_opp_root(self.root, VENDOR, OP_TYPE)

    def test_invalid_vendor_identity_cannot_widen_the_search(self):
        self._candidate("valid")
        for value in ("", ".", "..", "vendors/name", "vendors\\name"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(P.PackageLayoutError, "EXPECTED_VENDOR_INVALID"):
                    P.resolve_package_opp_root(self.root, value, OP_TYPE)

    def test_repeated_resolution_is_deterministic(self):
        candidate = self._candidate("stable/run")
        results = [P.resolve_package_opp_root(self.root, VENDOR, OP_TYPE)
                   for _ in range(3)]
        self.assertEqual(results, [os.path.realpath(candidate)] * 3)


if __name__ == "__main__":
    unittest.main()
