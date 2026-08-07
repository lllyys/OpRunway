"""N7 张量结构/数组属性/布局契约的纯 stdlib 测试。"""

import copy
import hashlib
import json
import os
import unittest

import tensor_shape_attrs as T

SHA_TASKDOC = "a" * 64
SHA_SPEC = "b" * 64
SHA_FACTS = "c" * 64


def _source_parse():
    return {
        "source": "taskdoc_pr_compose",
        "source_sha256": SHA_FACTS,
        "cite": "experimental/math/roll/op_api/aclnn_roll.cpp:109",
        "quote": "When dims is empty, shifts size must be 1.",
        "interpretation": "右侧轴数组为空且左侧数组为单元素，是有源码依据的长度关系例外",
    }


def _source_binding():
    return {
        "compose_kind": "spec_taskdoc_compose",
        "spec_sha256": SHA_SPEC,
        "taskdoc_snapshot_sha256": SHA_TASKDOC,
        "source_facts_sha256": SHA_FACTS,
    }


def _row(row_id, attrs, applicability=None):
    row = {"id": row_id, "attrs": attrs, "source_parse": _source_parse()}
    if applicability is not None:
        row["applicability"] = applicability
    return row


def _applicability(*, ranks=(1, 2, 3, 4, 5, 6, 7, 8), nonempty=("dims",)):
    return {
        "rank_domain": {"input": "x", "allowed_ranks": list(ranks)},
        "required_nonempty_attrs": list(nonempty),
        "source_parse": {
            "source": "taskdoc_pr_compose",
            "source_sha256": SHA_FACTS,
            "cite": "task_doc.snapshot.md:29-31",
            "quote": "dims 取值范围在[-x.dim(), x.dim() - 1]之内",
            "interpretation": "非空 dims 行只对 rank 1..8 的具名输入 x 可执行",
        },
    }


def _canonical_sha(value):
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _layout(shape, strides, *, role=T.LAYOUT_ROLE_INPUT,
            kind=T.LAYOUT_CONTIGUOUS, offset=0, base=1,
            case_id="case-0", tensor_name="x", tensor_index=0):
    return {
        "case_id": case_id,
        "tensor_name": tensor_name,
        "tensor_index": tensor_index,
        "role": role,
        "format": T.TENSOR_FORMAT_ND,
        "logical_shape": list(shape),
        "layout_kind": kind,
        "layout_capability": (
            T.LAYOUT_CAPABILITY_CONTIGUOUS
            if kind == T.LAYOUT_CONTIGUOUS
            else T.LAYOUT_CAPABILITY_SPAN_SEPARABLE),
        "strides": list(strides),
        "storage_offset": offset,
        "base_storage_numel": base,
    }


def _make_layout_receipt(declaration, expected_shape, *,
                         expected_role=T.LAYOUT_ROLE_INPUT,
                         expected_case_id="case-0", expected_tensor_name="x",
                         expected_tensor_index=0):
    return T.make_layout_receipt(
        declaration,
        expected_shape=expected_shape,
        expected_role=expected_role,
        expected_case_id=expected_case_id,
        expected_tensor_name=expected_tensor_name,
        expected_tensor_index=expected_tensor_index,
    )


def _validate_layout_receipt(receipt, expected_shape, *,
                             expected_role=T.LAYOUT_ROLE_INPUT,
                             expected_case_id="case-0", expected_tensor_name="x",
                             expected_tensor_index=0):
    return T.validate_layout_receipt(
        receipt,
        expected_shape=expected_shape,
        expected_role=expected_role,
        expected_case_id=expected_case_id,
        expected_tensor_name=expected_tensor_name,
        expected_tensor_index=expected_tensor_index,
    )


def _assert_layout_preserved(receipt, observed, expected_shape, *,
                             expected_role=T.LAYOUT_ROLE_INPUT,
                             expected_case_id="case-0", expected_tensor_name="x",
                             expected_tensor_index=0, expected_receipt_sha256=None):
    return T.assert_layout_preserved(
        receipt, observed,
        expected_shape=expected_shape,
        expected_role=expected_role,
        expected_case_id=expected_case_id,
        expected_tensor_name=expected_tensor_name,
        expected_tensor_index=expected_tensor_index,
        expected_receipt_sha256=(expected_receipt_sha256
                                 or T.layout_receipt_sha256(receipt)),
    )


class ShapeContractTest(unittest.TestCase):
    def test_rank0_empty_and_singleton_are_three_distinct_structures(self):
        rank0 = T.make_shape_receipt([])
        empty = T.make_shape_receipt([0])
        singleton = T.make_shape_receipt([1])
        self.assertEqual(
            (rank0["rank"], rank0["numel"], rank0["structural_kind"]),
            (0, 1, T.SHAPE_RANK0),
        )
        self.assertEqual(
            (empty["rank"], empty["numel"], empty["structural_kind"]),
            (1, 0, T.SHAPE_EMPTY),
        )
        self.assertEqual(
            (singleton["rank"], singleton["numel"], singleton["structural_kind"]),
            (1, 1, T.SHAPE_NONEMPTY),
        )

    def test_mutation_rank0_to_singleton_fails_identity(self):
        with self.assertRaisesRegex(T.TensorShapeAttrError, "rank0 不得"):
            T.assert_shape_identity([], [1], where="case.inputs[0].shape")

    def test_shape_rejects_bool_negative_and_rank_overflow(self):
        for bad in ([True], [-1], [1] * 9):
            with self.subTest(shape=bad):
                with self.assertRaises(T.TensorShapeAttrError):
                    T.normalize_shape(bad)


class AttrContractTest(unittest.TestCase):
    TYPES = {"shifts": T.ATTR_INT_ARRAY, "dims": T.ATTR_INT_ARRAY}
    GROUPS = [{
        "id": "shift_dim_lengths",
        "members": ["shifts", "dims"],
        "relation": T.CONSTRAINT_EQUAL_LENGTH,
        "exceptions": [{
            "lengths": {"shifts": 1, "dims": 0},
            "source_parse": _source_parse(),
        }],
    }]

    def test_empty_int_array_requires_and_obeys_explicit_type(self):
        self.assertEqual(
            T.normalize_attr_value([], attr_type=T.ATTR_INT_ARRAY), [])
        with self.assertRaisesRegex(T.TensorShapeAttrError, "显式声明"):
            T.normalize_attr_value([], attr_type=T.ATTR_SCALAR)

    def test_empty_int_array_still_rejects_bad_elements(self):
        for bad in ([True], [1.5], [1, "2"], [[1]]):
            with self.subTest(value=bad):
                with self.assertRaises(T.TensorShapeAttrError):
                    T.normalize_attr_value(bad, attr_type=T.ATTR_INT_ARRAY)

    def test_atomic_rows_keep_equal_lengths_and_sourced_flatten_exception(self):
        result = T.normalize_atomic_attr_rows(
            [
                _row("multi_axis", {"shifts": [3, -2], "dims": [0, -1]}),
                _row("flatten", {"shifts": [5], "dims": []}),
            ],
            attr_types=self.TYPES,
            constraint_groups=self.GROUPS,
            source_binding=_source_binding(),
        )
        self.assertEqual(result["rows"][1]["attrs"]["dims"], [])
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(
            [row["resolution"] for row in result["constraint_ledger"]],
            ["relation_satisfied", "source_parsed_exception"],
        )
        self.assertEqual(
            result["constraint_ledger"][1]["source_parse"], _source_parse())

    def test_independent_cartesian_illegal_pair_is_rejected(self):
        # 如果上游把 shifts/dims 两域独立相乘，这一行就是会混入的非法组合。
        with self.assertRaisesRegex(T.TensorShapeAttrError, "没有唯一的来源解析例外"):
            T.normalize_atomic_attr_rows(
                [_row("bad", {"shifts": [3, -2], "dims": []})],
                attr_types=self.TYPES,
                constraint_groups=self.GROUPS,
                source_binding=_source_binding(),
            )

    def test_constraint_exception_without_full_source_parse_is_rejected(self):
        groups = copy.deepcopy(self.GROUPS)
        del groups[0]["exceptions"][0]["source_parse"]["quote"]
        with self.assertRaisesRegex(T.TensorShapeAttrError, "键集合"):
            T.normalize_atomic_attr_rows(
                [_row("flatten", {"shifts": [5], "dims": []})],
                attr_types=self.TYPES,
                constraint_groups=groups,
                source_binding=_source_binding(),
            )

    def test_atomic_row_must_supply_every_declared_member(self):
        with self.assertRaisesRegex(T.TensorShapeAttrError, "atomic row"):
            T.normalize_atomic_attr_rows(
                [_row("missing", {"shifts": [1]})],
                attr_types=self.TYPES,
                constraint_groups=self.GROUPS,
                source_binding=_source_binding(),
            )

    def test_same_length_cartesian_cross_is_rejected_by_row_binding(self):
        contract = T.normalize_atomic_attr_rows(
            [
                _row("left", {"shifts": [1], "dims": [0]}),
                _row("right", {"shifts": [2], "dims": [1]}),
            ],
            attr_types=self.TYPES,
            constraint_groups=self.GROUPS,
            source_binding=_source_binding(),
        )
        with self.assertRaisesRegex(T.TensorShapeAttrError, "不得把各属性列"):
            T.bind_atomic_attr_row(
                contract, "left", {"shifts": [1], "dims": [1]},
                expected_contract_sha256=contract["sha256"],
            )

    def test_atomic_rows_reject_duplicate_id_content_and_source_drift(self):
        base = _row("a", {"shifts": [1], "dims": [0]})
        mutations = [
            [base, _row("a", {"shifts": [2], "dims": [1]})],
            [base, _row("b", {"shifts": [1], "dims": [0]})],
        ]
        for rows in mutations:
            with self.subTest(rows=rows):
                with self.assertRaises(T.TensorShapeAttrError):
                    T.normalize_atomic_attr_rows(
                        rows, attr_types=self.TYPES,
                        constraint_groups=self.GROUPS,
                        source_binding=_source_binding())
        contract = T.normalize_atomic_attr_rows(
            [base], attr_types=self.TYPES,
            constraint_groups=self.GROUPS,
            source_binding=_source_binding())
        forged = copy.deepcopy(contract)
        forged["source_binding"]["taskdoc_snapshot_sha256"] = "d" * 64
        # 即使伪造者同步重算了内嵌摘要，外部冻结摘要仍会拒绝。
        del forged["sha256"]
        forged["sha256"] = _canonical_sha(forged)
        with self.assertRaisesRegex(T.TensorShapeAttrError, "外部冻结摘要"):
            T.validate_atomic_attr_contract(
                forged, expected_sha256=contract["sha256"])

    def test_atomic_contract_digest_is_deterministic_under_object_key_order(self):
        left = T.normalize_atomic_attr_rows(
            [_row("one", {"shifts": [2], "dims": [-1]})],
            attr_types={"shifts": T.ATTR_INT_ARRAY, "dims": T.ATTR_INT_ARRAY},
            constraint_groups=self.GROUPS,
            source_binding=_source_binding(),
        )
        source_binding = _source_binding()
        right = T.normalize_atomic_attr_rows(
            [{
                "source_parse": dict(reversed(list(_source_parse().items()))),
                "attrs": {"dims": [-1], "shifts": [2]},
                "id": "one",
            }],
            attr_types={"dims": T.ATTR_INT_ARRAY, "shifts": T.ATTR_INT_ARRAY},
            constraint_groups=copy.deepcopy(self.GROUPS),
            source_binding=dict(reversed(list(source_binding.items()))),
        )
        self.assertEqual(left["sha256"], right["sha256"])
        self.assertEqual(
            T.bind_atomic_attr_row(
                right, "one", {"dims": [-1], "shifts": [2]},
                expected_contract_sha256=left["sha256"])["row_sha256"],
            left["rows"][0]["row_sha256"],
        )

    def test_non_string_object_keys_fail_as_contract_errors(self):
        with self.assertRaisesRegex(T.TensorShapeAttrError, "键须为字符串"):
            T.normalize_declared_attrs(
                self.TYPES, {"shifts": [1], "dims": [], 7: [0]},
            )

    def test_profile_applicability_binds_named_rank_nonempty_attrs_and_source(self):
        contract = T.normalize_atomic_attr_rows(
            [_row("axis", {"shifts": [1], "dims": [-1]}, _applicability())],
            attr_types=self.TYPES,
            constraint_groups=self.GROUPS,
            source_binding=_source_binding(),
        )
        row = contract["rows"][0]
        self.assertEqual(len(row["applicability_sha256"]), 64)
        self.assertEqual(
            row["applicability"]["source_parse"]["source_sha256"], SHA_FACTS)

        executable = T.evaluate_atomic_attr_applicability(
            contract, "axis",
            [{"name": "x", "kind": "tensor", "shape": [2, 3]}],
            expected_contract_sha256=contract["sha256"],
        )
        self.assertEqual(executable["status"], "executable")
        self.assertEqual(executable["reasons"], [])
        self.assertEqual(executable["rank_domain"]["actual_rank"], 2)
        self.assertEqual(executable["applicability_sha256"],
                         row["applicability_sha256"])

        excluded = T.evaluate_atomic_attr_applicability(
            contract, "axis",
            [{"name": "x", "kind": "tensor", "shape": []}],
            expected_contract_sha256=contract["sha256"],
        )
        self.assertEqual(excluded["status"], "excluded")
        self.assertEqual(
            [reason["kind"] for reason in excluded["reasons"]],
            ["rank_outside_domain"],
        )

    def test_required_nonempty_attr_is_an_audited_exclusion_not_silent_drop(self):
        contract = T.normalize_atomic_attr_rows(
            [_row("flatten", {"shifts": [1], "dims": []},
                  _applicability(ranks=(0, 1, 2), nonempty=("dims",)))],
            attr_types=self.TYPES,
            constraint_groups=self.GROUPS,
            source_binding=_source_binding(),
        )
        receipt = T.evaluate_atomic_attr_applicability(
            contract, "flatten",
            [{"name": "x", "kind": "tensor", "shape": [3]}],
            expected_contract_sha256=contract["sha256"],
        )
        self.assertEqual(receipt["status"], "excluded")
        self.assertEqual(receipt["required_nonempty_attrs"], [{
            "attr": "dims", "actual_length": 0, "matched": False,
        }])
        self.assertEqual(receipt["reasons"], [{
            "kind": "required_attr_empty", "attr": "dims",
        }])

    def test_applicability_source_and_predicate_digest_cannot_be_coherently_rewritten(self):
        contract = T.normalize_atomic_attr_rows(
            [_row("axis", {"shifts": [1], "dims": [0]}, _applicability())],
            attr_types=self.TYPES,
            constraint_groups=self.GROUPS,
            source_binding=_source_binding(),
        )
        forged = copy.deepcopy(contract)
        row = forged["rows"][0]
        row["applicability"]["source_parse"]["quote"] = "forged rank authority"
        row["applicability_sha256"] = _canonical_sha(row["applicability"])
        row_body = {key: value for key, value in row.items() if key != "row_sha256"}
        row["row_sha256"] = _canonical_sha(row_body)
        for ledger_row in forged["constraint_ledger"]:
            ledger_row["row_sha256"] = row["row_sha256"]
        forged_body = {key: value for key, value in forged.items() if key != "sha256"}
        forged["sha256"] = _canonical_sha(forged_body)
        with self.assertRaisesRegex(T.TensorShapeAttrError, "外部冻结摘要"):
            T.validate_atomic_attr_contract(
                forged, expected_sha256=contract["sha256"])

    def test_applicability_is_strict_and_profile_identity_cannot_be_forged(self):
        mutations = [
            _applicability(ranks=(True,)),
            _applicability(ranks=(1, 1)),
            _applicability(nonempty=("unknown",)),
        ]
        for applicability in mutations:
            with self.subTest(applicability=applicability):
                with self.assertRaises(T.TensorShapeAttrError):
                    T.normalize_atomic_attr_rows(
                        [_row("axis", {"shifts": [1], "dims": [0]},
                              applicability)],
                        attr_types=self.TYPES,
                        constraint_groups=self.GROUPS,
                        source_binding=_source_binding(),
                    )

        contract = T.normalize_atomic_attr_rows(
            [_row("axis", {"shifts": [1], "dims": [0]}, _applicability())],
            attr_types=self.TYPES,
            constraint_groups=self.GROUPS,
            source_binding=_source_binding(),
        )
        for profile_inputs in (
            [],
            [{"name": "other", "kind": "tensor", "shape": [2]}],
            [{"name": "x", "kind": "tensor", "shape": [2]},
             {"name": "x", "kind": "tensor", "shape": [2]}],
            [{"name": "x", "kind": "scalar", "shape": [2]}],
        ):
            with self.subTest(profile_inputs=profile_inputs):
                with self.assertRaisesRegex(T.TensorShapeAttrError, "唯一绑定"):
                    T.evaluate_atomic_attr_applicability(
                        contract, "axis", profile_inputs,
                        expected_contract_sha256=contract["sha256"],
                    )

    def test_legacy_row_has_identical_shape_without_applicability_fields(self):
        contract = T.normalize_atomic_attr_rows(
            [_row("legacy", {"shifts": [1], "dims": [0]})],
            attr_types=self.TYPES,
            constraint_groups=self.GROUPS,
            source_binding=_source_binding(),
        )
        self.assertEqual(
            set(contract["rows"][0]),
            {"id", "attrs", "source_parse", "row_sha256"},
        )
        receipt = T.evaluate_atomic_attr_applicability(
            contract, "legacy",
            [{"name": "anything", "kind": "tensor", "shape": []}],
            expected_contract_sha256=contract["sha256"],
        )
        self.assertEqual(receipt, {
            "status": "executable",
            "applicability_sha256": None,
            "rank_domain": None,
            "required_nonempty_attrs": [],
            "reasons": [],
        })


class CyclicIndexContractTest(unittest.TestCase):
    def test_negative_axes_normalize_cyclically(self):
        receipt = T.normalize_cyclic_indices(
            [-4, -1, 0, 3], 4,
            duplicate_policy=T.CYCLIC_DUPLICATES_ALLOW,
        )
        self.assertEqual(receipt["normalized"], [0, 3, 0, 3])
        self.assertTrue(receipt["had_negative"])

    def test_rank0_accepts_only_empty_axis_array(self):
        receipt = T.normalize_cyclic_indices(
            [], 0, duplicate_policy=T.CYCLIC_DUPLICATES_ALLOW)
        self.assertEqual(receipt["normalized"], [])
        with self.assertRaisesRegex(T.TensorShapeAttrError, "rank 0"):
            T.normalize_cyclic_indices(
                [0], 0, duplicate_policy=T.CYCLIC_DUPLICATES_ALLOW)

    def test_duplicate_policy_is_explicit_and_checked_after_normalization(self):
        with self.assertRaisesRegex(T.TensorShapeAttrError, "重复轴"):
            T.normalize_cyclic_indices(
                [0, -4], 4,
                duplicate_policy=T.CYCLIC_DUPLICATES_REJECT,
            )
        self.assertEqual(
            T.normalize_cyclic_indices(
                [0, -4], 4,
                duplicate_policy=T.CYCLIC_DUPLICATES_ALLOW)["normalized"],
            [0, 0],
        )

    def test_out_of_range_axis_fails_before_generation(self):
        for bad in ([-5], [4]):
            with self.subTest(value=bad):
                with self.assertRaisesRegex(T.TensorShapeAttrError, "越界"):
                    T.normalize_cyclic_indices(
                        bad, 4, duplicate_policy=T.CYCLIC_DUPLICATES_ALLOW)


class LayoutContractTest(unittest.TestCase):
    def test_generated_layout_declarations_are_deterministic_and_identity_bound(self):
        contiguous = T.derive_layout_declaration(
            [2, 3], layout_kind=T.LAYOUT_CONTIGUOUS,
            role=T.LAYOUT_ROLE_INPUT, case_id="case-0",
            tensor_name="x", tensor_index=0)
        self.assertEqual(contiguous["strides"], [3, 1])
        self.assertEqual(contiguous["storage_offset"], 0)
        self.assertEqual(contiguous["base_storage_numel"], 6)

        left = T.derive_layout_declaration(
            [2, 3], layout_kind=T.LAYOUT_NONCONTIGUOUS,
            role=T.LAYOUT_ROLE_INPUT, case_id="case-0",
            tensor_name="x", tensor_index=0)
        right = T.derive_layout_declaration(
            [2, 3], layout_kind=T.LAYOUT_NONCONTIGUOUS,
            role=T.LAYOUT_ROLE_INPUT, case_id="case-0",
            tensor_name="x", tensor_index=0)
        self.assertEqual(left, right)
        self.assertEqual(left["strides"], [6, 2])
        self.assertEqual(left["storage_offset"], 1)
        self.assertEqual(left["base_storage_numel"], 12)
        receipt = _make_layout_receipt(left, [2, 3])
        self.assertEqual(receipt["layout_kind"], T.LAYOUT_NONCONTIGUOUS)

    def test_generated_noncontiguous_rejects_unobservable_structures(self):
        for shape in ([], [0, 3], [1], [1, 1]):
            with self.subTest(shape=shape):
                with self.assertRaisesRegex(
                        T.TensorShapeAttrError, "unsupported_layout"):
                    T.derive_layout_declaration(
                        shape, layout_kind=T.LAYOUT_NONCONTIGUOUS,
                        role=T.LAYOUT_ROLE_INPUT, case_id="case-0",
                        tensor_name="x", tensor_index=0)

    def test_rank0_nd_layout_keeps_empty_shape_and_stride_vectors(self):
        receipt = _make_layout_receipt(_layout([], [], base=1), [])
        self.assertEqual(receipt["logical_shape"], [])
        self.assertEqual(receipt["strides"], [])
        self.assertEqual(receipt["rank"], 0)
        self.assertEqual(receipt["structural_kind"], T.SHAPE_RANK0)

    def test_empty_tensor_is_not_rank0_and_can_have_zero_base_storage(self):
        receipt = _make_layout_receipt(
            _layout([0, 3], [3, 1], base=0), [0, 3])
        self.assertEqual(receipt["structural_kind"], T.SHAPE_EMPTY)
        self.assertEqual(receipt["minimum_base_storage_numel"], 0)

    def test_noncontiguous_input_layout_round_trips(self):
        declaration = _layout(
            [6, 4], [1, 6], kind=T.LAYOUT_NONCONTIGUOUS, base=24)
        receipt = _make_layout_receipt(declaration, [6, 4])
        self.assertEqual(receipt["layout_kind"], T.LAYOUT_NONCONTIGUOUS)
        self.assertEqual(receipt["minimum_base_storage_numel"], 24)
        self.assertEqual(
            _validate_layout_receipt(receipt, [6, 4]),
            receipt,
        )

    def test_mutation_rank0_layout_to_singleton_fails_binding(self):
        declaration = _layout([], [], base=1)
        declaration["logical_shape"] = [1]
        declaration["strides"] = [1]
        with self.assertRaisesRegex(T.TensorShapeAttrError, "rank0 不得"):
            _make_layout_receipt(declaration, [])

    def test_mutation_silent_contiguous_fails_preservation(self):
        receipt = _make_layout_receipt(
            _layout([6, 4], [1, 6], kind=T.LAYOUT_NONCONTIGUOUS, base=24),
            [6, 4])
        observed = _layout([6, 4], [4, 1], base=24)
        with self.assertRaisesRegex(T.TensorShapeAttrError, "物理布局未保持"):
            _assert_layout_preserved(receipt, observed, [6, 4])

    def test_mutation_missing_layout_contract_fails_closed(self):
        with self.assertRaisesRegex(T.TensorShapeAttrError, "须为 object"):
            _make_layout_receipt(None, [2, 3])

    def test_mutation_receipt_derived_fields_cannot_self_report(self):
        receipt = _make_layout_receipt(
            _layout([6, 4], [1, 6], kind=T.LAYOUT_NONCONTIGUOUS, base=24),
            [6, 4])
        receipt["minimum_base_storage_numel"] = 23
        with self.assertRaisesRegex(T.TensorShapeAttrError, "漂移"):
            _validate_layout_receipt(receipt, [6, 4])

    def test_receipt_derived_fields_are_type_strict(self):
        receipt = _make_layout_receipt(_layout([], [], base=1), [])
        for key, bad in (
            ("schema_version", True),
            ("rank", 0.0),
            ("numel", True),
            ("minimum_base_storage_numel", 1.0),
        ):
            mutated = copy.deepcopy(receipt)
            mutated[key] = bad
            with self.subTest(key=key, bad=bad):
                with self.assertRaises(T.TensorShapeAttrError):
                    _validate_layout_receipt(mutated, [])

    def test_coherent_forged_receipt_cannot_self_prove(self):
        original = _make_layout_receipt(
            _layout([6, 4], [1, 6], kind=T.LAYOUT_NONCONTIGUOUS, base=24),
            [6, 4])
        forged = _make_layout_receipt(
            _layout([6, 4], [4, 1], base=24), [6, 4])
        # declaration + derived + observed 全部同步改成连续仍不够：外部冻结摘要不随它改。
        with self.assertRaisesRegex(T.TensorShapeAttrError, "摘要漂移"):
            _assert_layout_preserved(
                forged, _layout([6, 4], [4, 1], base=24),
                [6, 4],
                expected_receipt_sha256=T.layout_receipt_sha256(original),
            )

    def test_layout_receipt_digest_is_deterministic_under_key_order(self):
        declaration = _layout(
            [6, 4], [1, 6], kind=T.LAYOUT_NONCONTIGUOUS, base=24)
        left = _make_layout_receipt(declaration, [6, 4])
        right = _make_layout_receipt(
            dict(reversed(list(declaration.items()))), [6, 4])
        self.assertEqual(
            T.layout_receipt_sha256(left), T.layout_receipt_sha256(right))

    def test_coherent_role_or_slot_swap_is_rejected_by_external_identity(self):
        mutations = [
            _layout([2, 3], [3, 1], role=T.LAYOUT_ROLE_OUTPUT,
                    base=6, tensor_name="x", tensor_index=0),
            _layout([2, 3], [3, 1], base=6,
                    tensor_name="other", tensor_index=1),
        ]
        for declaration in mutations:
            receipt = _make_layout_receipt(
                declaration, [2, 3], expected_role=declaration["role"],
                expected_tensor_name=declaration["tensor_name"],
                expected_tensor_index=declaration["tensor_index"])
            with self.subTest(declaration=declaration):
                with self.assertRaises(T.TensorShapeAttrError):
                    _assert_layout_preserved(
                        receipt, declaration, [2, 3],
                        expected_role=T.LAYOUT_ROLE_INPUT,
                        expected_tensor_name="x", expected_tensor_index=0,
                        expected_receipt_sha256=T.layout_receipt_sha256(receipt),
                    )

    def test_rank0_and_empty_storage_offset_boundaries(self):
        rank0 = _make_layout_receipt(
            _layout([], [], offset=2, base=3), [])
        self.assertEqual(rank0["minimum_base_storage_numel"], 3)
        empty = _make_layout_receipt(
            _layout([0, 3], [3, 1], offset=5, base=5), [0, 3])
        self.assertEqual(empty["minimum_base_storage_numel"], 5)
        for declaration, shape in (
            (_layout([], [], offset=2, base=2), []),
            (_layout([0, 3], [3, 1], offset=5, base=4), [0, 3]),
        ):
            with self.subTest(layout=declaration):
                with self.assertRaisesRegex(T.TensorShapeAttrError, "容不下"):
                    _make_layout_receipt(declaration, shape)

    def test_singleton_stride_is_contiguous_but_still_identity_bearing(self):
        receipt = _make_layout_receipt(
            _layout([1, 3], [99, 1], base=3), [1, 3])
        self.assertEqual(receipt["layout_kind"], T.LAYOUT_CONTIGUOUS)
        observed = _layout([1, 3], [3, 1], base=3)
        with self.assertRaisesRegex(T.TensorShapeAttrError, "物理布局未保持"):
            _assert_layout_preserved(receipt, observed, [1, 3])

    def test_non_span_separable_layout_is_explicitly_unsupported_not_overlap(self):
        declaration = _layout(
            [2, 3], [3, 2], kind=T.LAYOUT_NONCONTIGUOUS, base=8)
        with self.assertRaisesRegex(T.TensorShapeAttrError, "unsupported_layout") as cm:
            _make_layout_receipt(declaration, [2, 3])
        self.assertNotIn("产生内部重叠", str(cm.exception))

    def test_output_layout_positive_path_is_independent(self):
        receipt = _make_layout_receipt(
            _layout([2, 3], [3, 1], role=T.LAYOUT_ROLE_OUTPUT,
                    tensor_name="out", base=6),
            [2, 3], expected_role=T.LAYOUT_ROLE_OUTPUT,
            expected_tensor_name="out")
        self.assertEqual(receipt["role"], T.LAYOUT_ROLE_OUTPUT)
        self.assertEqual(receipt["tensor_name"], "out")

    def test_input_output_roles_are_separate(self):
        declaration = _layout([2, 3], [3, 1], role=T.LAYOUT_ROLE_OUTPUT, base=6)
        with self.assertRaisesRegex(T.TensorShapeAttrError, "input/output 布局必须分账"):
            _make_layout_receipt(declaration, [2, 3])

    def test_nd_format_is_controlled(self):
        declaration = _layout([2], [1], base=2)
        declaration["format"] = "nchw"
        with self.assertRaisesRegex(T.TensorShapeAttrError, "受控词表"):
            _make_layout_receipt(declaration, [2])

    def test_layout_rejects_false_noncontiguous_overlap_and_short_storage(self):
        mutations = [
            _layout([2, 3], [3, 1], kind=T.LAYOUT_NONCONTIGUOUS, base=6),
            _layout([2, 3], [1, 1], kind=T.LAYOUT_NONCONTIGUOUS, base=4),
            _layout([6, 4], [1, 6], kind=T.LAYOUT_NONCONTIGUOUS, base=23),
        ]
        for declaration in mutations:
            with self.subTest(layout=declaration):
                with self.assertRaises(T.TensorShapeAttrError):
                    _make_layout_receipt(
                        declaration, [2, 3]
                        if declaration["logical_shape"] == [2, 3] else [6, 4])


@unittest.skipUnless(
    os.environ.get("OPRUNWAY_NPU_TEST") == "1",
    "仅在 NPU 目标环境显式启用",
)
class RealNpuLayoutContractTest(unittest.TestCase):
    """最小真机见证：不调用 DUT，只核对 torch_npu 实际张量的结构事实。"""

    def test_rank0_empty_and_noncontiguous_view_keep_physical_identity(self):
        import torch
        import torch_npu  # noqa: F401  # 显式注册 NPU backend

        device = torch.device("npu:0")
        scalar = torch.tensor(3.0, device=device)
        empty = torch.empty((0, 3), device=device)
        self.assertEqual(list(scalar.shape), [])
        self.assertEqual(scalar.dim(), 0)
        self.assertEqual(scalar.numel(), 1)
        self.assertEqual(list(empty.shape), [0, 3])
        self.assertEqual(empty.dim(), 2)
        self.assertEqual(empty.numel(), 0)

        base = torch.arange(24, dtype=torch.float32, device=device)
        view = torch.as_strided(base, (6, 4), (1, 6))
        declaration = _layout(
            list(view.shape), list(view.stride()),
            kind=T.LAYOUT_NONCONTIGUOUS,
            offset=view.storage_offset(), base=base.numel(),
            case_id="npu-layout-witness", tensor_name="input0", tensor_index=0,
        )
        receipt = _make_layout_receipt(
            declaration, [6, 4], expected_case_id="npu-layout-witness",
            expected_tensor_name="input0")
        observed = _layout(
            list(view.shape), list(view.stride()),
            kind=T.LAYOUT_NONCONTIGUOUS,
            offset=view.storage_offset(), base=base.numel(),
            case_id="npu-layout-witness", tensor_name="input0", tensor_index=0,
        )
        _assert_layout_preserved(
            receipt, observed, [6, 4],
            expected_case_id="npu-layout-witness", expected_tensor_name="input0")

        contiguous = view.contiguous()
        overwritten = _layout(
            list(contiguous.shape), list(contiguous.stride()),
            offset=contiguous.storage_offset(), base=contiguous.numel(),
            case_id="npu-layout-witness", tensor_name="input0", tensor_index=0,
        )
        with self.assertRaisesRegex(T.TensorShapeAttrError, "物理布局未保持"):
            _assert_layout_preserved(
                receipt, overwritten, [6, 4],
                expected_case_id="npu-layout-witness", expected_tensor_name="input0")


if __name__ == "__main__":
    unittest.main()
