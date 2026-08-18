import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _coverage_strategy import (  # noqa: E402
    CoveragePolicyError,
    audit_coverage,
    build_anchored_rows,
    class_profile,
    comparator_failures,
    expected_comparator,
)


class CoverageStrategyTest(unittest.TestCase):
    def test_main_effects_and_declared_interaction_only(self):
        dims = {
            "dtype": ["a", "b"],
            "rank": [1, 2, 3],
            "mode": ["x", "y"],
        }
        policy = {
            "strategy": "anchored_interactions",
            "baseline": {"dtype": "a", "rank": 1, "mode": "x"},
            "interaction_groups": [{
                "axes": ["rank", "mode"],
                "reason": "rank and mode are semantically coupled",
            }],
            "targeted": [],
            "max_cases": 20,
        }

        rows = build_anchored_rows(dims, policy, [])

        self.assertEqual(7, len(rows))
        self.assertEqual(
            {(rank, mode) for rank in dims["rank"] for mode in dims["mode"]},
            {(row["rank"], row["mode"]) for row in rows},
        )
        self.assertEqual(set(dims["dtype"]), {row["dtype"] for row in rows})

    def test_size_and_shape_groups_are_unioned_not_fully_multiplied(self):
        dims = {
            "dtype": ["a", "b", "c"],
            "size_class": ["small", "typical", "large"],
            "rank": [1, 2],
            "shape_profile": ["balanced", "skewed"],
        }
        policy = {
            "strategy": "anchored_interactions",
            "baseline": {
                "dtype": "a",
                "size_class": "typical",
                "rank": 2,
                "shape_profile": "balanced",
            },
            "interaction_groups": [
                {
                    "axes": ["dtype", "size_class"],
                    "reason": "each data type needs multiple workloads",
                },
                {
                    "axes": ["rank", "shape_profile"],
                    "reason": "shape form depends on rank",
                },
            ],
            "targeted": [],
            "max_cases": 20,
        }

        rows = build_anchored_rows(dims, policy, [])

        self.assertEqual(12, len(rows))
        self.assertEqual(
            {(dtype, size) for dtype in dims["dtype"] for size in dims["size_class"]},
            {(row["dtype"], row["size_class"]) for row in rows},
        )
        self.assertEqual(
            {(rank, form) for rank in dims["rank"] for form in dims["shape_profile"]},
            {(row["rank"], row["shape_profile"]) for row in rows},
        )
        self.assertLess(len(rows), 3 * 3 * 2 * 2)

    def test_declared_infeasible_projection_is_skipped(self):
        dims = {"rank": [0, 1], "mode": ["flat", "multi"]}
        policy = {
            "strategy": "anchored_interactions",
            "baseline": {"rank": 1, "mode": "flat"},
            "interaction_groups": [{
                "axes": ["rank", "mode"],
                "reason": "mode validity depends on rank",
            }],
            "targeted": [],
            "max_cases": 10,
        }
        infeasible = [{"rank": 0, "mode": "multi", "why": "no axis"}]

        rows = build_anchored_rows(dims, policy, infeasible)

        self.assertEqual(3, len(rows))
        self.assertNotIn({"rank": 0, "mode": "multi"}, rows)

    def test_anchor_that_hides_coupling_is_rejected(self):
        dims = {"rank": [0, 1], "mode": ["single"]}
        policy = {
            "strategy": "anchored_interactions",
            "baseline": {"rank": 1, "mode": "single"},
            "interaction_groups": [],
            "targeted": [],
            "max_cases": 10,
        }
        infeasible = [{"rank": 0, "mode": "single", "why": "no axis"}]

        with self.assertRaisesRegex(CoveragePolicyError, "interaction group"):
            build_anchored_rows(dims, policy, infeasible)

    def test_main_effect_axis_cannot_be_silently_omitted(self):
        dims = {"dtype": ["a", "b"], "size": ["small", "large"]}
        policy = {
            "strategy": "anchored_interactions",
            "baseline": {"dtype": "a", "size": "small"},
            "main_effects": ["dtype"],
            "interaction_groups": [],
            "targeted": [],
            "max_cases": 10,
        }

        with self.assertRaisesRegex(CoveragePolicyError, "main_effects 缺少"):
            build_anchored_rows(dims, policy, [])

    def test_audit_checks_targeted_cases(self):
        must_cover = {
            "dims": {"dtype": ["a", "b"]},
            "coverage_policy": {
                "strategy": "anchored_interactions",
                "baseline": {"dtype": "a"},
                "interaction_groups": [],
                "targeted": ["empty"],
                "max_cases": 4,
            },
            "infeasible": [],
            "combos": [
                {"dtype": "a"},
                {"dtype": "b", "coverage_tags": ["empty"]},
            ],
        }
        # dims 只有一根轴，不构成完整覆盖设计，算子类别与配比检查不适用

        failures, report = audit_coverage(must_cover)

        self.assertEqual([], failures)
        self.assertEqual(2, report["selected_cases"])
        self.assertEqual(1, report["targeted_hit"])

    def test_audit_fails_closed_on_missing_policy(self):
        failures, _ = audit_coverage({"dims": {}, "combos": []})
        self.assertTrue(any("coverage_policy" in item for item in failures))

    def test_audit_rejects_budget_overflow_without_truncation(self):
        must_cover = {
            "dims": {"dtype": ["a", "b"]},
            "coverage_policy": {
                "strategy": "anchored_interactions",
                "baseline": {"dtype": "a"},
                "interaction_groups": [],
                "targeted": [],
                "max_cases": 1,
            },
            "infeasible": [],
            "combos": [{"dtype": "a"}, {"dtype": "b"}],
        }

        failures, report = audit_coverage(must_cover)

        self.assertEqual(2, report["selected_cases"])
        self.assertTrue(any("max_cases" in item for item in failures))

    def test_generic_module_has_no_operator_names(self):
        source = (SCRIPTS / "_coverage_strategy.py").read_text(encoding="utf-8")
        for operator_term in ("roll", "dims_mode", "shift_class"):
            self.assertNotIn(operator_term, source.lower())


class BuiltinComparatorTest(unittest.TestCase):
    """真值来自 CANN 内置实现时，浮点的位级相等成立。

    「浮点位级相等在 NPU 上不成立」那条判据说的是跨后端比框架基线。
    这里两侧都在 NPU 上跑同一个 aclnn 接口，任何一位不同都说明改动
    改变了输出，正是要抓的东西。
    """

    def _profile(self, name="elementwise"):
        return class_profile(name, None)

    def test_builtin_expects_equal_whatever_the_class_says(self):
        must_cover = {"operator_class": "elementwise", "comparator": "equal",
                      "baseline_kind": "cann_builtin"}
        self.assertEqual("equal", expected_comparator(
            must_cover, self._profile()))
        self.assertEqual([], comparator_failures(
            must_cover, self._profile()))

    def test_builtin_refuses_mixed_tolerance(self):
        must_cover = {"operator_class": "elementwise",
                      "comparator": "mixed_tolerance_bm",
                      "baseline_kind": "cann_builtin"}
        found = comparator_failures(must_cover, self._profile())
        self.assertEqual(1, len(found))
        self.assertIn("只能是 equal", found[0])

    def test_torch_baseline_keeps_the_class_comparator(self):
        must_cover = {"operator_class": "elementwise", "comparator": "equal",
                      "baseline_kind": "torch"}
        self.assertEqual(1, len(comparator_failures(
            must_cover, self._profile())))

    def test_absent_baseline_kind_behaves_like_torch(self):
        # 老的 must_cover.json 没有这个键，不能因此改判。
        must_cover = {"operator_class": "elementwise", "comparator": "equal"}
        self.assertEqual(1, len(comparator_failures(
            must_cover, self._profile())))

    def test_generation_class_uses_equal_and_does_no_arithmetic(self):
        profile = self._profile("generation")
        self.assertEqual("equal", profile["comparator"])
        self.assertFalse(profile["arithmetic"])
        self.assertIn("dtype", profile["axes"])


class FloatEqualWaiverTest(unittest.TestCase):
    """equal + 浮点这条判据按「跟谁比」分叉。"""

    def _failures(self, baseline_kind):
        dims = {"dtype": ["fp16", "fp32"], "size_class": ["small", "large"],
                "shape_form": ["normal", "empty"]}
        combos = [{"dtype": d, "size_class": s, "shape_form": f,
                   "coverage_tags": []}
                  for d in dims["dtype"] for s in dims["size_class"]
                  for f in dims["shape_form"]]
        must_cover = {"dims": dims, "combos": combos, "comparator": "equal",
                      "operator_class": "elementwise",
                      "coverage_policy": {
                          "strategy": "anchored_interactions",
                          "baseline": {"dtype": "fp16", "size_class": "small",
                                       "shape_form": "normal"},
                          "interaction_groups": [],
                          "targeted": [],
                          "max_cases": 16,
                      },
                      "dtype_binding": {"source": "README.md",
                                        "declared": ["fp16", "fp32"],
                                        "missing_from_axis": [],
                                        "uncovered": []},
                      "baseline_kind": baseline_kind}
        failures, _report = audit_coverage(must_cover)
        return failures

    def test_torch_baseline_still_refuses_equal_with_floats(self):
        self.assertTrue(any("位级相等" in item for item in self._failures("torch")))

    def test_builtin_baseline_waives_it(self):
        self.assertFalse(any("位级相等" in item
                             for item in self._failures("cann_builtin")))


if __name__ == "__main__":
    unittest.main()


class PairwiseAndSizeRatioTest(unittest.TestCase):
    """两两覆盖与规模配比门禁。

    真实事故：一个五轴设计声明了 baseline + 全部主效应 + 两个交互组，
    82 条 combos 只买到 51.1% 的两两覆盖——主效应把其余轴锚在 baseline 上，
    除声明的两个组外，其余 8 个轴对几乎不被触及。而同样条数改用两两覆盖
    阵列可以到 100%。同时 size_class 混进了 empty/boundary 这类形态取值，
    导致规模分布无法核算，中大档实际只占 12%，性能验收测不到多核与流水。
    """

    DIMS = {
        "dtype": ["fp16", "fp32", "int32"],
        "rank": [1, 2, 3, 4],
        "size_class": ["small", "medium", "large"],
        "shape_form": ["normal", "unaligned_tail"],
        "contiguity": ["contiguous", "strided"],
        "op_axis_pos": ["first", "last"],
    }

    def _policy(self, groups=()):
        return {
            "strategy": "anchored_interactions",
            "baseline": {"dtype": "fp32", "rank": 1, "size_class": "small",
                     "shape_form": "normal", "contiguity": "contiguous",
                     "op_axis_pos": "last"},
            "interaction_groups": list(groups),
            "targeted": [],
            "max_cases": 500,
        }

    def _spec(self, combos, groups=()):
        return {"dims": self.DIMS, "operator_class": "movement",
                "coverage_policy": self._policy(groups),
                "infeasible": [], "combos": combos}

    def test_baseline_anchored_design_is_rejected(self):
        from _coverage_strategy import build_anchored_rows
        rows = build_anchored_rows(self.DIMS, self._policy(), [])
        combos = [dict(r, coverage_tags=[]) for r in rows]
        failures, report = audit_coverage(self._spec(combos))
        self.assertLess(report["pairwise"]["rate"], 0.90)
        self.assertTrue(any("两两覆盖率" in f for f in failures), failures)

    def test_pairwise_array_clears_the_gate(self):
        from _coverage_strategy import pairwise_rows, SIZE_TARGET
        rows = pairwise_rows(self.DIMS, size_target=SIZE_TARGET, seed=1)
        combos = [dict(r, coverage_tags=[]) for r in rows]
        failures, report = audit_coverage(self._spec(combos))
        self.assertEqual(report["pairwise"]["rate"], 1.0)
        self.assertFalse(any("两两覆盖率" in f for f in failures), failures)
        self.assertFalse(any("规模配比" in f for f in failures), failures)

    def test_size_axis_rejects_shape_form_values(self):
        # empty / boundary 是形态不是规模，混进来规模分布就没法核算
        dims = dict(self.DIMS, size_class=["small", "medium", "large", "empty"])
        policy = dict(self._policy())
        spec = {"dims": dims, "operator_class": "movement",
                "coverage_policy": policy, "infeasible": [],
                "combos": [dict(r, coverage_tags=[])
                           for r in build_anchored_rows(dims, policy, [])]}
        failures, _ = audit_coverage(spec)
        self.assertTrue(any("非规模取值" in f for f in failures), failures)

    def test_size_ratio_deviation_is_rejected(self):
        from _coverage_strategy import RATIO_MIN_SAMPLE, pairwise_rows, SIZE_TARGET
        rows = pairwise_rows(self.DIMS, size_target=SIZE_TARGET, seed=1)
        # 把规模全部压成 small，模拟「用例都堆在小规模」——实测一个算子
        # 87.8% 的用例落在小档，中大档合计只有 12%。
        # 样本要够 RATIO_MIN_SAMPLE，否则配比本来就不该判。
        skewed = []
        while len(skewed) < RATIO_MIN_SAMPLE + 5:
            for index, row in enumerate(rows):
                skewed.append(dict(row, size_class="small", rank=index % 4 + 1,
                                   coverage_tags=[]))
                if len(skewed) >= RATIO_MIN_SAMPLE + 5:
                    break
        failures, report = audit_coverage(self._spec(skewed))
        self.assertGreaterEqual(report["size_ratio"]["total"], RATIO_MIN_SAMPLE)
        self.assertTrue(any("规模配比" in f for f in failures), failures)

    def test_small_sample_skips_the_ratio_check(self):
        from _coverage_strategy import RATIO_MIN_SAMPLE
        few = [dict(self._policy()["baseline"], coverage_tags=[])
               for _ in range(RATIO_MIN_SAMPLE - 1)]
        failures, _ = audit_coverage(self._spec(few))
        self.assertFalse(any("规模配比" in f for f in failures), failures)

    def test_adding_axes_does_not_inflate_the_array(self):
        # 覆盖阵列规模由最大两根轴之积决定，补轴几乎免费
        from _coverage_strategy import pairwise_rows
        base = len(pairwise_rows(self.DIMS, seed=0))
        wider = dict(self.DIMS, contiguity=["contiguous", "strided"],
                     axis_pos=["first", "middle", "last"])
        self.assertLessEqual(len(pairwise_rows(wider, seed=0)), base + 4)


class FloatShareWaiverTest(unittest.TestCase):
    """纯整型分面的 float_share 豁免。

    真实事故（median 验收）：skill 要求"混合类别按分面拆开"，拆完的整型分面
    浮点占比恒为 0，float_share 门禁在结构上不可能通过，验收在第 3 步卡死。

    豁免必须从 dtype 轴的取值推导，不能靠 must_cover 里的 comparator 声明——
    声明是随手能写的标记，任何分面填一行 equal 就能绕过门禁；轴取值决定生成
    什么用例、报告里出现什么 dtype，伪造不了。
    """

    AXES = {"rank": [1, 2, 3], "size_class": ["small", "medium", "large"],
            "reduce_axis_pos": ["first", "middle", "last"]}

    def _spec(self, dtypes, comparator=None):
        from _coverage_strategy import pairwise_rows, SIZE_TARGET
        dims = dict(self.AXES, dtype=list(dtypes))
        rows = pairwise_rows(dims, size_target=SIZE_TARGET, seed=1)
        spec = {
            "dims": dims,
            "operator_class": "reduction",
            "coverage_policy": {
                "strategy": "anchored_interactions",
                "baseline": {"dtype": dims["dtype"][0], "rank": 1,
                             "size_class": "small", "reduce_axis_pos": "last"},
                "main_effects": ["dtype", "rank", "size_class",
                                 "reduce_axis_pos"],
                "interaction_groups": [], "targeted": [], "max_cases": 500,
            },
            "infeasible": [],
            "combos": [dict(r, coverage_tags=[]) for r in rows],
        }
        if comparator is not None:
            spec["comparator"] = comparator
        return spec

    def test_int_only_axis_waives_the_gate(self):
        spec = self._spec(["int8", "int32", "int64"], comparator="equal")
        failures, report = audit_coverage(spec)
        self.assertEqual("no_float_dtype", report.get("float_share_waived"))
        self.assertFalse(any("浮点 dtype 占比" in f for f in failures), failures)

    def test_int_only_axis_waives_without_any_declaration(self):
        # 豁免来自轴取值，没写 comparator 也照样生效
        failures, report = audit_coverage(self._spec(["int32", "int64"]))
        self.assertEqual("no_float_dtype", report.get("float_share_waived"))
        self.assertFalse(any("浮点 dtype 占比" in f for f in failures), failures)

    def test_declaring_equal_cannot_waive_a_float_axis(self):
        # 后门检查：轴里有浮点时声明 equal 不但不豁免，还要判失败
        spec = self._spec(["fp16", "fp32", "int32"], comparator="equal")
        failures, report = audit_coverage(spec)
        self.assertNotIn("float_share_waived", report)
        self.assertTrue(any("比较器声明为 equal" in f for f in failures), failures)

    def test_float_axis_still_faces_the_gate(self):
        spec = self._spec(["fp32", "int8", "int16", "int32", "int64"])
        failures, report = audit_coverage(spec)
        self.assertNotIn("float_share_waived", report)
        self.assertLess(report["float_share"]["share"], 0.58)
        self.assertTrue(any("浮点 dtype 占比" in f for f in failures), failures)


class OneShotValidationTest(unittest.TestCase):
    """声明校验必须一次报全，不能首错即抛。

    真实成本（median 验收）：S2 里改了 17 次声明文件才把门禁过掉。校验挨个抛
    异常时，声明里有 N 个问题就要跑 N 轮才看得全，每轮还要重读一遍上下文。

    结构性问题（dims 塌了）仍然提前中断——后面的检查全依赖轴表，继续查只会
    刷出一串派生错误，反而更难读。
    """

    DIMS = {"dtype": ["fp16", "fp32"], "rank": [1, 2],
            "size_class": ["small", "large"]}

    def _validate(self, policy, infeasible=(), dims=None):
        from _coverage_strategy import _validate
        with self.assertRaises(CoveragePolicyError) as caught:
            _validate(self.DIMS if dims is None else dims, policy,
                      list(infeasible))
        return str(caught.exception)

    def test_every_independent_problem_is_reported_together(self):
        message = self._validate(
            {
                "strategy": "pairwise",
                "baseline": {"dtype": "fp64", "rank": 1},
                "main_effects": ["dtype", "rank"],
                "interaction_groups": [{"axes": ["dtype"], "reason": "x"}],
                "targeted": ["empty", "empty"],
                "max_cases": 0,
            },
            infeasible=[{"rank": 3, "why": "rank3 不适用"}, {"rank": 1}],
        )
        for expected in ("strategy 只支持", "baseline 必须包含", "baseline.dtype",
                         "main_effects 缺少", "axes 至少包含两个轴",
                         "targeted 含重复标签", "max_cases 必须是正整数",
                         "infeasible[0].rank", "infeasible[1] 缺少 why"):
            self.assertIn(expected, message)

    def test_single_problem_stays_a_single_line(self):
        message = self._validate({
            "strategy": "anchored_interactions",
            "baseline": {"dtype": "fp32", "rank": 1, "size_class": "small"},
            "main_effects": ["dtype", "rank", "size_class"],
            "interaction_groups": [], "targeted": [], "max_cases": 0,
        })
        self.assertEqual("coverage_policy.max_cases 必须是正整数", message)

    def test_broken_dims_stops_early_instead_of_cascading(self):
        message = self._validate(
            {"strategy": "anchored_interactions", "baseline": {},
             "interaction_groups": [], "targeted": [], "max_cases": 1},
            dims={"dtype": []},
        )
        self.assertIn("dims.dtype 必须是非空数组", message)
        self.assertNotIn("baseline", message)

    def test_empty_dims_is_still_a_legal_degenerate_form(self):
        from _coverage_strategy import _validate
        # 只做参数能力检查时不声明覆盖轴，这条路径必须保持可用。
        _validate({}, {"strategy": "anchored_interactions", "baseline": {},
                       "interaction_groups": [], "targeted": [],
                       "max_cases": 1}, [])


class GateCriteriaAreDocumentedTest(unittest.TestCase):
    """门禁判据必须能从 reference 查到，不必回来读这个文件。

    真机两轮都在这里花钱：huber 与 median 的 agent 各读了 4~5 次
    `_coverage_strategy.py`，找的都是同一类东西——轴能取什么值、
    某道门禁到底拿什么当判据。抽象描述不够，得把常量本身写进 reference。

    这条测试守的是「文档与常量没漂移」，不是常量本身。
    """

    REFERENCE = Path(__file__).resolve().parents[1] / "references" / "case-design.md"

    def _text(self):
        return self.REFERENCE.read_text(encoding="utf-8")

    def test_size_bands_are_listed_verbatim(self):
        from _coverage_strategy import SIZE_BANDS
        text = self._text()
        for band in SIZE_BANDS:
            self.assertIn(f"`{band}`", text)

    def test_documented_thresholds_match_the_constants(self):
        from _coverage_strategy import (FLOAT_SHARE, FLOAT_SHARE_TOLERANCE,
                                        PAIRWISE_FLOOR, RATIO_MIN_SAMPLE,
                                        SIZE_TOLERANCE)
        text = self._text()
        self.assertIn(f"{int(PAIRWISE_FLOOR * 100)}%", text)
        self.assertIn(f"{int(FLOAT_SHARE * 100)}%", text)
        self.assertIn(f"{int(FLOAT_SHARE_TOLERANCE * 100)}pp", text)
        self.assertIn(f"{int(SIZE_TOLERANCE * 100)}pp", text)
        self.assertIn(str(RATIO_MIN_SAMPLE), text)

    def test_float_share_is_documented_as_derived_from_the_dtype_axis(self):
        # 判据一旦退回「信声明」，纯整型分面又会被结构性堵死。
        text = self._text()
        self.assertIn("不看比较器声明", text)
        self.assertIn("一个浮点取值都没有", text)

    def test_every_declared_operator_class_is_documented(self):
        from _coverage_strategy import OPERATOR_CLASSES
        text = self._text()
        for name, profile in OPERATOR_CLASSES.items():
            with self.subTest(operator_class=name):
                self.assertIn(name, text)
                for axis in profile["axes"]:
                    self.assertIn(axis, text)


class ClassDecidesComparatorTest(unittest.TestCase):
    """比较器由算子类别定死，不是逐个 dtype 现判的选择题。

    真实事故（roll，2026-08-17）：搬运类算子整份声明了 mixed_tolerance_bm，
    随后发现 ATK 对 int8 走量化标准容忍差 1，于是把 int8 单独拆成一份分面用
    equal——多了一整套 YAML、必测集、用例 JSON、冻结目录和跑测命令，只为一个
    dtype，而覆盖率一条也没多。搬运类不做算术，每种 dtype 都该逐元素相等，
    整份 equal 就够，本来一份都不用拆。
    """

    DIMS = {"dtype": ["fp16", "fp32", "int8", "int32"],
            "size_class": ["small", "medium", "large"],
            "shape_form": ["normal", "empty", "single_element",
                           "unaligned_tail"],
            "op_axis_pos": ["first", "middle", "last"]}

    def _spec(self, operator_class, comparator):
        from _coverage_strategy import pairwise_rows, SIZE_TARGET
        rows = pairwise_rows(self.DIMS, size_target=SIZE_TARGET, seed=1)
        return {
            "dims": self.DIMS, "operator_class": operator_class,
            "comparator": comparator,
            "coverage_policy": {
                "strategy": "anchored_interactions",
                "baseline": {"dtype": "fp32", "size_class": "small",
                             "shape_form": "normal", "op_axis_pos": "last"},
                "main_effects": list(self.DIMS), "interaction_groups": [],
                "targeted": [], "max_cases": 600},
            "infeasible": [],
            "combos": [dict(r, coverage_tags=[]) for r in rows],
        }

    def _comparator_failures(self, operator_class, comparator):
        failures, _ = audit_coverage(self._spec(operator_class, comparator))
        return [f for f in failures if "比较器是" in f]

    def test_movement_declaring_mixed_tolerance_is_rejected(self):
        failures = self._comparator_failures("movement", "mixed_tolerance_bm")
        self.assertTrue(failures, "搬运类声明混合容差应当被拦下")
        self.assertIn("int8 不必单独拆一份分面", failures[0])

    def test_movement_declaring_equal_passes_with_floats_on_the_axis(self):
        self.assertEqual([], self._comparator_failures("movement", "equal"))

    def test_message_names_the_nan_caveat(self):
        # equal 走 torch.equal，只在整张都是 NaN 时特判；不说这句，
        # agent 会被含部分 NaN 的用例误判吓回去，再拆一次分面。
        failures = self._comparator_failures("movement", "mixed_tolerance_bm")
        self.assertIn("has_infnan", failures[0])

    def test_arithmetic_class_declaring_equal_is_rejected(self):
        failures = self._comparator_failures("reduction", "equal")
        self.assertTrue(failures, "算术类声明逐元素相等应当被拦下")

    def test_arithmetic_class_keeps_mixed_tolerance(self):
        self.assertEqual([],
                         self._comparator_failures("reduction", "mixed_tolerance_bm"))

    def test_undeclared_comparator_is_not_second_guessed(self):
        # 没声明 comparator 的老声明文件不平白被拦。
        spec = self._spec("movement", "equal")
        spec.pop("comparator")
        failures, _ = audit_coverage(spec)
        self.assertEqual([], [f for f in failures if "比较器是" in f])

    def test_every_fixed_class_carries_a_comparator(self):
        from _coverage_strategy import OPERATOR_CLASSES
        for name, profile in OPERATOR_CLASSES.items():
            with self.subTest(operator_class=name):
                self.assertIn(profile.get("comparator"),
                              ("equal", "mixed_tolerance_bm"))


class DtypeAxisBoundToSourceTest(unittest.TestCase):
    """dtype 轴钉在工程声明表上之后，浮点判据换成漏没漏，不再判占比。

    真实事故（2026-08-17 median + roll 双路真机跑测）：工程声明表是 3 浮点
    5 整型，混排分面的浮点占比结构上到不了 70%-12pp。比例门禁只剩一个出口
    ——按 dtype 拆分面让整型那份吃 `no_float_dtype` 豁免——而 case-design.md
    同时写着「dtype 不构成拆分面的理由」。两个算子各返工一次，各多拆出一份
    本不该存在的分面。

    比例门禁是为「dtype 轴由设计者自己挑」写的。轴取值改成照工程声明抄之后，
    浮点几个是工程的事实，对事实提比例要求本身就是错的类别。
    """

    AXES = {"rank": [1, 2, 3], "size_class": ["small", "medium", "large"],
            "reduce_axis_pos": ["first", "middle", "last"]}

    def _spec(self, dtypes, binding=None, comparator=None):
        from _coverage_strategy import pairwise_rows, SIZE_TARGET
        dims = dict(self.AXES, dtype=list(dtypes))
        rows = pairwise_rows(dims, size_target=SIZE_TARGET, seed=1)
        spec = {
            "dims": dims,
            "operator_class": "reduction",
            "coverage_policy": {
                "strategy": "anchored_interactions",
                "baseline": {"dtype": dims["dtype"][0], "rank": 1,
                             "size_class": "small", "reduce_axis_pos": "last"},
                "main_effects": ["dtype", "rank", "size_class",
                                 "reduce_axis_pos"],
                "interaction_groups": [], "targeted": [], "max_cases": 500,
            },
            "infeasible": [],
            "combos": [dict(r, coverage_tags=[]) for r in rows],
        }
        if binding is not None:
            spec["dtype_binding"] = binding
        if comparator is not None:
            spec["comparator"] = comparator
        return spec

    MEDIAN_TABLE = ["fp16", "bf16", "fp32", "int16", "int32", "int64", "uint8",
                    "int8"]

    def _binding(self, excluded=("int8",)):
        return {"source": "/proj/median/README.md",
                "declared_in_source": list(self.MEDIAN_TABLE),
                "excluded": list(excluded)}

    def test_mixed_facet_that_the_ratio_gate_rejected_now_passes(self):
        # median 的真实声明：7 种混排（int8 拆到量化分面），占比 43%。
        spec = self._spec(["fp16", "bf16", "fp32", "int16", "int32", "int64",
                           "uint8"], binding=self._binding())
        failures, report = audit_coverage(spec)
        self.assertLess(report["float_share"]["share"], 0.58)
        self.assertEqual("dtype_axis_bound_to_source",
                         report.get("float_share_waived"))
        self.assertFalse(any("浮点 dtype 占比" in f for f in failures), failures)

    def test_same_facet_without_binding_still_faces_the_ratio_gate(self):
        # 没有出处绑定就是老路径：轴取值是设计自由度，比例照判。
        spec = self._spec(["fp16", "bf16", "fp32", "int16", "int32", "int64",
                           "uint8"])
        failures, report = audit_coverage(spec)
        self.assertNotIn("float_share_waived", report)
        self.assertTrue(any("浮点 dtype 占比" in f for f in failures), failures)

    def test_dropping_a_declared_float_is_rejected(self):
        # 换判据不是放宽：漏一种浮点比占比低危险得多，占比低只是测得少。
        spec = self._spec(["fp16", "fp32", "int16", "int32", "int64", "uint8"],
                          binding=self._binding())
        failures, _ = audit_coverage(spec)
        self.assertTrue(any("bf16" in f and "dtype_source_excludes" in f
                            for f in failures), failures)

    def test_float_moved_to_another_facet_must_say_where(self):
        spec = self._spec(["fp16", "fp32", "int16", "int32", "int64", "uint8"],
                          binding=self._binding(excluded=("int8", "bf16")))
        failures, report = audit_coverage(spec)
        self.assertFalse(any("bf16" in f for f in failures), failures)
        self.assertIn("bf16", report["float_coverage"]["excluded_floats"])

    def test_declared_float_with_no_combo_is_rejected(self):
        spec = self._spec(["fp16", "bf16", "fp32", "int16", "int32", "int64",
                           "uint8"], binding=self._binding())
        spec["combos"] = [c for c in spec["combos"] if c.get("dtype") != "bf16"]
        failures, _ = audit_coverage(spec)
        self.assertTrue(any("一条 combo 都没用到" in f for f in failures), failures)

    def test_int_only_facet_keeps_its_own_waiver(self):
        # 纯整型分面（搬运类整型面）走的还是 no_float_dtype，那条豁免没被顶掉。
        spec = self._spec(["int16", "int32", "int64"],
                          binding=self._binding(excluded=("fp16", "bf16",
                                                          "fp32", "int8")))
        _, report = audit_coverage(spec)
        self.assertEqual("no_float_dtype", report.get("float_share_waived"))

    def test_equal_comparator_on_a_float_axis_is_still_rejected(self):
        # 后门检查：换判据不能顺手把「浮点不能声明 equal」也放过去。
        spec = self._spec(["fp16", "bf16", "fp32", "int16", "int32", "int64",
                           "uint8"], binding=self._binding(),
                          comparator="equal")
        failures, _ = audit_coverage(spec)
        self.assertTrue(any("比较器声明为 equal" in f for f in failures), failures)
