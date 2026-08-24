"""规模档 × rank → shape：与算子无关的那一段,只写一份。

CLAUDE.md §10.6 把它列为未落地,代价是每个算子手写一份物化脚本,
同一段推导重写一遍、同样的错重犯一遍,已经赔付过两次调用成本。
"""

import sys
import unittest
from pathlib import Path

from _paths import SKILL_ROOT

sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _shapes  # noqa: E402


class ShapeForTest(unittest.TestCase):
    def test_rank_decides_how_many_axes(self):
        for rank in (1, 2, 3, 4):
            with self.subTest(rank=rank):
                self.assertEqual(rank,
                                 len(_shapes.shape_for(rank, "medium")))

    def test_bigger_band_means_more_elements(self):
        import math
        sizes = [math.prod(_shapes.shape_for(3, band))
                 for band in ("small", "medium", "large")]
        self.assertEqual(sizes, sorted(sizes))

    def test_ragged_axis_rotates_with_the_index(self):
        # 同一档里的不同 combo 不该都是同一个形状,否则 dim_values
        # 只有一两个代表值,非对齐尾块也只在固定那根轴上出现。
        shapes = {tuple(_shapes.shape_for(3, "medium", index))
                  for index in range(3)}
        self.assertEqual(3, len(shapes))

    def test_ragged_can_be_turned_off(self):
        shape = _shapes.shape_for(3, "medium", 1, ragged=False)
        self.assertEqual(1, len(set(shape)))

    def test_no_axis_exceeds_the_single_dim_cap(self):
        for shape in (_shapes.shape_for(1, "large"),
                      _shapes.shape_for(2, "large")):
            with self.subTest(shape=shape):
                self.assertTrue(all(size <= _shapes.MAX_DIM for size in shape))

    def test_no_axis_collapses_to_zero(self):
        # 0 是空张量,属于 shape_form,不该由规模档意外造出来。
        for rank in (1, 2, 3, 4, 5):
            with self.subTest(rank=rank):
                self.assertTrue(all(size >= 1
                                    for size in _shapes.shape_for(rank, "small")))

    def test_custom_numel_overrides_the_default_ladder(self):
        shape = _shapes.shape_for(1, "small", numel={"small": 8})
        self.assertEqual([7], shape)

    def test_unknown_band_is_named_in_the_error(self):
        with self.assertRaises(KeyError):
            _shapes.shape_for(2, "huge")


class AxisForTest(unittest.TestCase):
    def test_positions(self):
        self.assertEqual(0, _shapes.axis_for(3, "first"))
        self.assertEqual(1, _shapes.axis_for(3, "middle"))
        self.assertEqual(2, _shapes.axis_for(3, "last"))

    def test_unknown_position_is_rejected_with_the_allowed_set(self):
        with self.assertRaises(ValueError) as caught:
            _shapes.axis_for(3, "somewhere")
        self.assertIn("first", str(caught.exception))


class TemplateUsesTheSharedModuleTest(unittest.TestCase):
    def test_materialize_template_imports_the_shared_shapes(self):
        text = (SKILL_ROOT / "assets" / "example" / "materialize.py") \
            .read_text(encoding="utf-8")
        self.assertIn("from _shapes import", text)
        self.assertIn("ATK_SKILL_DIR", text)

    def test_template_does_not_keep_its_own_copy_of_the_ladder(self):
        # 留一份副本就是留一处会漂移的知识。
        text = (SKILL_ROOT / "assets" / "example" / "materialize.py") \
            .read_text(encoding="utf-8")
        self.assertNotIn("def shape_for(", text)
        self.assertNotIn("def dim_for(", text)


class ShapeFormMappingTest(unittest.TestCase):
    """四个 shape_form 取值必须映射成两两不同的形状。

    真机事故（roll，2026-08-17）：物化脚本给 normal 也调了默认的
    `shape_for(...)`，而默认 `ragged=True` 会让某一根轴取 2^n-1——那正是
    unaligned_tail 的定义。两个形态撞成同一个形状，重复 combo 门禁退回，
    返工一轮重跑物化。
    """

    FORMS = ("normal", "empty", "single_element", "unaligned_tail")

    def test_four_forms_are_pairwise_distinct(self):
        from _shapes import shape_for_form
        for rank in (1, 2, 3, 4, 5, 6, 7, 8):
            for band in ("small", "medium", "large"):
                with self.subTest(rank=rank, size_class=band):
                    shapes = [tuple(shape_for_form(rank, band, form))
                              for form in self.FORMS]
                    self.assertEqual(len(shapes), len(set(shapes)))

    def test_normal_never_carries_an_unaligned_tail(self):
        from _shapes import shape_for_form
        for rank in (1, 2, 4, 8):
            shape = shape_for_form(rank, "medium", "normal")
            self.assertEqual(1, len(set(shape)), shape)

    def test_forms_stay_in_sync_with_the_pinned_vocabulary(self):
        import _axis_binding
        self.assertEqual(sorted(self.FORMS),
                         sorted(_axis_binding.PINNED_AXIS_VALUES["shape_form"]))

    def test_degenerate_rank_and_band_is_named_not_silently_wrong(self):
        from _shapes import shape_for_form, ShapeFormDegenerate
        with self.assertRaises(ShapeFormDegenerate):
            shape_for_form(8, "tiny", "normal", numel={"tiny": 1})

    def test_unknown_form_is_rejected(self):
        from _shapes import shape_for_form
        with self.assertRaises(ValueError):
            shape_for_form(2, "small", "ragged")


class StructuralInfeasibleTest(unittest.TestCase):
    """钉死轴之间分不开的组合，声明期就要说完。

    真机事故（roll，2026-08-17）：rank1 的首尾轴同轴、单元素形态下规模档无区分度，
    都是等物化跑完才由重复 combo 门禁一轮一轮退回来的，返工三轮。
    """

    DIMS = {"rank": [1, 2, 3], "size_class": ["small", "medium", "large"],
            "shape_form": ["normal", "empty", "single_element", "unaligned_tail"],
            "op_axis_pos": ["first", "middle", "last"]}

    def test_rank1_axis_positions_collapse(self):
        from _axis_binding import structural_infeasible
        entries = structural_infeasible(self.DIMS)
        hit = [e for e in entries if e.get("rank") == 1
               and "op_axis_pos" in e]
        self.assertEqual({"middle", "last"},
                         {e["op_axis_pos"] for e in hit})

    def test_rank2_middle_collides_with_last(self):
        from _axis_binding import structural_infeasible
        entries = structural_infeasible(self.DIMS)
        self.assertTrue(any(e.get("rank") == 2 and e.get("op_axis_pos") == "middle"
                            for e in entries), entries)

    def test_shapeless_forms_have_no_size_class(self):
        from _axis_binding import structural_infeasible
        entries = structural_infeasible(self.DIMS)
        for form in ("single_element", "empty"):
            bands = {e["size_class"] for e in entries
                     if e.get("shape_form") == form}
            self.assertEqual({"medium", "large"}, bands)

    def test_every_entry_carries_a_reason(self):
        from _axis_binding import structural_infeasible
        for entry in structural_infeasible(self.DIMS):
            self.assertTrue(entry.get("why"), entry)

    def test_axes_declared_not_applicable_produce_nothing(self):
        from _axis_binding import structural_infeasible
        dims = {"rank": [1, 2], "op_axis_pos": ["n/a"], "size_class": ["n/a"],
                "shape_form": ["single_element"]}
        self.assertEqual([], structural_infeasible(dims))


if __name__ == "__main__":
    unittest.main()
