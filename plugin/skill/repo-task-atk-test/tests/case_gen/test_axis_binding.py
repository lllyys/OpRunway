"""dims 声明的两个出处校验。

真机来源：median 在 1.2 上验收过七次，每次的 must_cover 条数都不同。
生成器本身是确定的（同一份声明连跑五次输出字节一致），变的是声明：
`rank` 写过 [1..8]、[1,2,3,5]、[1,2,3,4]、[1,2,3]，也整根没写过；
`shape_form` 写过 dense·odd_tail、aligned·misaligned_tail、
normal·single_elem·empty 三套命名；`reduce_axis_pos` 写过 none、all、first。
覆盖率次次 100%，因为分母就是这份声明自己。

这里锁的是「取值有没有出处」，不是「取值好不好看」。
"""

import sys
import unittest
from pathlib import Path

from _paths import SKILL_ROOT
from tempfile import TemporaryDirectory

sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _coverage_strategy  # noqa: E402
from _axis_binding import (  # noqa: E402
    NOT_APPLICABLE,
    PINNED_AXIS_VALUES,
    check_axis_vocabulary,
    check_dtype_source,
)


class AxisVocabularyTest(unittest.TestCase):
    def test_full_vocabulary_passes(self):
        dims = {"rank": list(PINNED_AXIS_VALUES["rank"]),
                "shape_form": list(PINNED_AXIS_VALUES["shape_form"])}
        self.assertEqual([], check_axis_vocabulary(dims))

    def test_not_applicable_is_the_only_way_to_degenerate(self):
        """全张量归约没有「归约轴位置」，但退化要留痕，不能随手改成 ['none']。"""
        self.assertEqual([], check_axis_vocabulary(
            {"reduce_axis_pos": [NOT_APPLICABLE]}))
        problems = check_axis_vocabulary({"reduce_axis_pos": ["none"]})
        self.assertEqual(1, len(problems))
        self.assertIn(NOT_APPLICABLE, problems[0])

    def test_truncated_rank_axis_is_rejected(self):
        """rank 缩到 [1,2,3,4] 是历次用例数不同的最大单一来源。"""
        problems = check_axis_vocabulary({"rank": [1, 2, 3, 4]})
        self.assertEqual(1, len(problems))
        self.assertIn("rank", problems[0])

    def test_renamed_values_are_rejected(self):
        problems = check_axis_vocabulary({"shape_form": ["dense", "odd_tail"]})
        self.assertEqual(1, len(problems))
        self.assertIn("shape_form", problems[0])

    def test_reordered_values_are_rejected(self):
        """顺序进 itertools.product，换序就换 combos，必须一并钉死。"""
        reversed_rank = list(reversed(PINNED_AXIS_VALUES["rank"]))
        self.assertEqual(1, len(check_axis_vocabulary({"rank": reversed_rank})))

    def test_unpinned_axes_are_left_alone(self):
        """dtype、keepdim 这类轴的取值来自工程和签名，不由词表管。"""
        self.assertEqual([], check_axis_vocabulary(
            {"dtype": ["fp16", "int8"], "keepdim": [True, False]}))

    def test_size_bands_stay_in_sync_with_the_coverage_engine(self):
        self.assertEqual(list(_coverage_strategy.SIZE_BANDS),
                         PINNED_AXIS_VALUES[_coverage_strategy.SIZE_AXIS])


class DtypeSourceTest(unittest.TestCase):
    """dtype 轴的取值只能来自待验收算子工程声明的那张表。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.readme = Path(self.tmp.name) / "README.md"
        # median 工程 README 的原文写法：中文顿号分隔的 C 侧名字。
        self.readme.write_text(
            "| 数据类型 | FLOAT16、FLOAT、INT32、BF16、INT64、INT16、UINT8、INT8 |",
            encoding="utf-8")

    def test_declared_dtypes_found_in_the_source_pass(self):
        self.assertEqual([], check_dtype_source(
            ["fp16", "fp32", "int32", "bf16", "int64", "int16", "uint8", "int8"],
            self.readme))

    def test_dtype_absent_from_the_source_is_rejected(self):
        """任务书只写「所有走入 aicore 的数据类型」，凭空多一个 fp64 要拦下。"""
        problems = check_dtype_source(["fp16", "fp64"], self.readme)
        self.assertTrue(any("fp64" in message and "找不到" in message
                            for message in problems), problems)

    def test_substring_does_not_count_as_a_declaration(self):
        """UINT8 里有 INT8、FLOAT16 里有 FLOAT，子串命中会放过没声明的 dtype。"""
        narrow = Path(self.tmp.name) / "narrow.md"
        narrow.write_text("支持 UINT8、FLOAT16", encoding="utf-8")
        self.assertEqual([], check_dtype_source(["uint8", "fp16"], narrow))
        for absent in ("int8", "fp32"):
            with self.subTest(dtype=absent):
                self.assertTrue(
                    any(absent in message and "找不到" in message
                        for message in check_dtype_source([absent], narrow)))

    def test_unknown_dtype_name_falls_back_to_the_literal_token(self):
        """别名表没收录的 dtype 不放行也不误拦：按字面找。"""
        exotic = Path(self.tmp.name) / "exotic.md"
        exotic.write_text("支持 atb_customize", encoding="utf-8")
        self.assertEqual([], check_dtype_source(["atb_customize"], exotic))
        self.assertEqual(1, len(check_dtype_source(["nonexistent"], exotic)))

    def test_missing_source_file_is_reported(self):
        problems = check_dtype_source(["fp16"], Path(self.tmp.name) / "nope.md")
        self.assertEqual(1, len(problems))
        self.assertIn("nope.md", problems[0])


class DtypeOmissionTest(unittest.TestCase):
    """工程声明了的 dtype 不能漏。

    只查「声明的能不能找到」挡的是凭空写，挡不住少写：8 种写成 5 种照样过门禁，
    用例数还是每轮不一样，而漏掉的那几种根本没被验收。
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.readme = Path(self.tmp.name) / "README.md"
        self.readme.write_text(
            "| 数据类型 | FLOAT16、FLOAT、INT32、BF16、INT64、INT16、UINT8、INT8 |",
            encoding="utf-8")

    def test_complete_declaration_passes(self):
        self.assertEqual([], check_dtype_source(
            ["fp16", "fp32", "int32", "bf16", "int64", "int16", "uint8", "int8"],
            self.readme))

    def test_omitted_dtype_is_reported(self):
        problems = check_dtype_source(["fp16", "fp32"], self.readme)
        self.assertEqual(1, len(problems))
        for dtype in ("int32", "bf16", "int64", "int16", "uint8", "int8"):
            self.assertIn(dtype, problems[0])

    def test_exclusion_with_a_reason_is_accepted(self):
        """median 的 README 里 `bool` 是 keepDim 属性的类型，不是输入 dtype。

        没有这个出口，量具会拿一份不全的名单硬拒合法声明，把 agent 逼到
        跑测中途去改量具——median 那轮已经这样发生过一次。
        """
        self.readme.write_text("FLOAT16、INT32 | keepDim | bool |",
                               encoding="utf-8")
        excludes = [{"dtype": "bool", "why": "keepDim 属性的类型，不是输入 dtype"}]
        self.assertEqual([], check_dtype_source(
            ["fp16", "int32"], self.readme, excludes))

    def test_exclusion_without_a_reason_is_rejected(self):
        problems = check_dtype_source(
            ["fp16"], self.readme, [{"dtype": "int32"}])
        self.assertTrue(any("why" in message for message in problems))

    def test_exclusion_cannot_cover_a_dtype_absent_from_the_source(self):
        """豁免只用来解释出处里的误报，不是给声明开的后门。"""
        problems = check_dtype_source(
            ["fp16", "fp32", "int32", "bf16", "int64", "int16", "uint8", "int8"],
            self.readme, [{"dtype": "fp64", "why": "随手写的"}])
        self.assertTrue(any("fp64" in message for message in problems))


class DtypeSourceInventoryTest(unittest.TestCase):
    """出处里到底写了哪些 dtype——覆盖门禁换判据要拿这份清单当事实。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.readme = Path(self.tmp.name) / "README.md"
        self.readme.write_text(
            "| 数据类型 | FLOAT16、FLOAT、INT32、BF16、INT64、INT16、UINT8、INT8 |",
            encoding="utf-8")

    def test_inventory_lists_every_dtype_in_the_source(self):
        from _axis_binding import dtype_source_inventory
        self.assertEqual(
            sorted(["fp16", "fp32", "int32", "bf16", "int64", "int16",
                    "uint8", "int8"]),
            sorted(dtype_source_inventory(self.readme)))

    def test_inventory_agrees_with_the_two_way_check(self):
        # 同一份出处，两套判据必须看到同一张表，否则门禁之间会互相打架。
        from _axis_binding import dtype_source_inventory
        found = dtype_source_inventory(self.readme)
        self.assertEqual([], check_dtype_source(found, self.readme))

    def test_unreadable_source_yields_an_empty_inventory(self):
        from _axis_binding import dtype_source_inventory
        self.assertEqual(
            [], dtype_source_inventory(Path(self.tmp.name) / "missing.md"))


if __name__ == "__main__":
    unittest.main()
