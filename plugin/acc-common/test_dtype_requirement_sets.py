"""任务书主表、保持集与回归扩展 dtype 契约测试（纯 stdlib）。"""

import copy
import unittest

import dtype_requirement_sets as DRS


SHA_TASK = "1" * 64
SHA_SOURCE = "2" * 64


def _evidence(membership, source_kind="taskdoc", sha=SHA_TASK):
    return {
        "membership": membership,
        "source_kind": source_kind,
        "source_sha256": sha,
        "cite": "task_doc.snapshot.md:21-32",
        "quote": "逐字的 dtype 来源说明",
        "interpretation": "该来源把此 dtype 归入对应需求集合",
    }


def _contract():
    return {
        "schema": "oprunway.dtype_requirement_sets",
        "schema_version": 1,
        "main_table_required": ["complex64", "float16", "float32"],
        "preservation_required": ["bool", "float16", "float32", "int64"],
        "regression_extension": ["bool", "int64"],
        "members": [
            {
                "dtype": "bool",
                "memberships": ["preservation_required", "regression_extension"],
                "evidence": [
                    _evidence("preservation_required"),
                    _evidence("regression_extension", "source_reference", SHA_SOURCE),
                ],
            },
            {
                "dtype": "complex64",
                "memberships": ["main_table_required"],
                "evidence": [_evidence("main_table_required")],
            },
            {
                "dtype": "float16",
                "memberships": ["main_table_required", "preservation_required"],
                "evidence": [
                    _evidence("main_table_required"),
                    _evidence("preservation_required"),
                ],
            },
            {
                "dtype": "float32",
                "memberships": ["main_table_required", "preservation_required"],
                "evidence": [
                    _evidence("main_table_required"),
                    _evidence("preservation_required"),
                ],
            },
            {
                "dtype": "int64",
                "memberships": ["preservation_required", "regression_extension"],
                "evidence": [
                    _evidence("preservation_required"),
                    _evidence("regression_extension", "source_reference", SHA_SOURCE),
                ],
            },
        ],
    }


class DtypeRequirementSetsTest(unittest.TestCase):
    def test_valid_contract_projects_required_union_and_content_digest(self):
        resolved = DRS.resolve(_contract())
        self.assertEqual(
            resolved["required_dtypes"],
            ["bool", "complex64", "float16", "float32", "int64"],
        )
        self.assertEqual(len(resolved["sha256"]), 64)
        self.assertEqual(
            DRS.from_spec({"dtype_requirement_sets": _contract()}), resolved)
        self.assertIsNone(DRS.from_spec({}))

    def test_every_membership_requires_its_own_source_evidence(self):
        raw = _contract()
        raw["members"][2]["evidence"] = [
            _evidence("main_table_required")]
        with self.assertRaisesRegex(DRS.DtypeRequirementSetsError,
                                    "evidence.*preservation_required"):
            DRS.resolve(raw)

    def test_declared_sets_and_member_projection_must_match_exactly(self):
        for mutate in (
            lambda raw: raw["main_table_required"].append("uint32"),
            lambda raw: raw["members"][0]["memberships"].remove(
                "regression_extension"),
            lambda raw: raw["members"].append(copy.deepcopy(raw["members"][0])),
        ):
            raw = _contract()
            mutate(raw)
            with self.subTest(raw=raw):
                with self.assertRaises(DRS.DtypeRequirementSetsError):
                    DRS.resolve(raw)

    def test_regression_extension_is_exact_preservation_minus_main_table(self):
        raw = _contract()
        raw["regression_extension"] = ["bool"]
        raw["members"][4]["memberships"] = ["preservation_required"]
        raw["members"][4]["evidence"] = [_evidence("preservation_required")]
        with self.assertRaisesRegex(DRS.DtypeRequirementSetsError,
                                    "preservation_required.*main_table_required"):
            DRS.resolve(raw)

    def test_dtype_and_set_order_is_canonical_not_an_untracked_axis(self):
        raw = _contract()
        raw["main_table_required"].reverse()
        with self.assertRaisesRegex(DRS.DtypeRequirementSetsError, "排序"):
            DRS.resolve(raw)
        raw = _contract()
        raw["members"][2]["memberships"].reverse()
        with self.assertRaisesRegex(DRS.DtypeRequirementSetsError, "顺序"):
            DRS.resolve(raw)

    def test_coherent_source_rewrite_cannot_match_frozen_receipt_digest(self):
        resolved = DRS.resolve(_contract())
        forged_raw = _contract()
        forged_raw["members"][0]["evidence"][0]["quote"] = "同步改写的伪来源"
        forged = DRS.resolve(forged_raw)
        with self.assertRaisesRegex(DRS.DtypeRequirementSetsError, "外部冻结摘要"):
            DRS.validate_receipt(forged, expected_sha256=resolved["sha256"])

    def test_unknown_keys_bad_sha_and_cross_membership_evidence_fail_closed(self):
        mutations = []
        raw = _contract()
        raw["operator"] = "Roll"
        mutations.append(raw)
        raw = _contract()
        raw["members"][0]["evidence"][0]["source_sha256"] = "bad"
        mutations.append(raw)
        raw = _contract()
        raw["members"][0]["evidence"][0]["membership"] = "main_table_required"
        mutations.append(raw)
        for raw in mutations:
            with self.subTest(raw=raw):
                with self.assertRaises(DRS.DtypeRequirementSetsError):
                    DRS.resolve(raw)


if __name__ == "__main__":
    unittest.main()
