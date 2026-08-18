"""make_yaml.py：设计 YAML 由 must_cover 推导，不由模型手写。

被测的是「产物间一致性不再依赖模型」这件事：YAML 的 type 来自语义契约，
dtype / rank / 维度值 / tuple_numbers 来自 combos。以前这些推导关系由
check_atk_capabilities.py 单独校验一遍，但那个脚本查的是本文件的产物——
脚本自己刚生成的东西不需要再校验，已删除。仍有价值的三条校验的是 agent
手写的 axes/extract 规则，移到本文件的 `_check_semantic_axes`，见
`SemanticAxesTest`。
"""

import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from make_yaml import DeclarationError, build_design  # noqa: E402

HEADER = {
    "name": "torch.roll", "aclnn_name": "Roll", "version": "v1",
    "api": "pytorch", "aclnn_api_type": "aclnn_function",
    "generate": "roll_constraint",
    "standard": {"acc": "equal", "perf": "not_key"},
}


def must_cover(parameters, combos, header=None):
    return {"yaml": dict(header or HEADER), "parameters": parameters,
            "combos": combos}


TENSOR = {"element_kind": "tensor", "runtime_container": "single",
          "nullable": False}
ATTR_LIST = {"element_kind": "attr", "runtime_container": "list",
             "nullable": False, "dtype": "int"}

COMBOS = [
    {"dtype": "fp16", "shape": [2, 3], "dims": [0]},
    {"dtype": "fp32", "shape": [4, 5, 6], "dims": [0, 1, 2]},
]


class TestTypeDerivation(unittest.TestCase):
    def test_semantics_decide_type(self):
        cases = [
            (("tensor", "single"), "tensor"),
            (("tensor", "list"), "tensors"),
            (("tensor", "tuple"), "tensor_tuple"),
            (("scalar", "single"), "scalar"),
            (("attr", "list"), "attrs"),
            (("attr", "tuple"), "attr_tuple"),
        ]
        for (element, container), expected in cases:
            contract = {"element_kind": element, "runtime_container": container,
                        "nullable": False, "dtype": "int"}
            combos = [{"dtype": "fp16", "shape": [2, 3], "x": [1]}]
            design = build_design(must_cover({"x": contract}, combos))
            self.assertEqual(design["inputs"][0]["type"], expected,
                             f"{element}/{container}")

    def test_illegal_semantics_rejected(self):
        contract = {"element_kind": "attr", "runtime_container": "single_elem",
                    "nullable": False, "dtype": "int"}
        with self.assertRaises(DeclarationError):
            build_design(must_cover({"x": contract}, COMBOS))


class TestTensorProjection(unittest.TestCase):
    def setUp(self):
        self.design = build_design(
            must_cover({"input": TENSOR, "dims": ATTR_LIST}, COMBOS))
        self.tensor = self.design["inputs"][0]

    def test_dtypes_from_combos(self):
        self.assertEqual(self.tensor["dtypes"]["values"], ["fp16", "fp32"])

    def test_rank_from_combos(self):
        self.assertEqual(
            self.tensor["shapes"]["dim_numbers"]["values"], [2, 3])

    def test_dim_values_cover_every_shape(self):
        declared = set(self.tensor["shapes"]["dim_values"]["values"])
        used = {size for combo in COMBOS for size in combo["shape"]}
        # 手写 YAML 时这里最容易失守：声明的取值集不含实际用到的维度值，
        # 生成器又会覆写 shape，于是 YAML 一直在撒谎却没人发现。
        self.assertTrue(used <= declared)

    def test_oversized_dim_rejected(self):
        combos = [{"dtype": "fp16", "shape": [2 ** 21], "dims": [0]}]
        with self.assertRaises(DeclarationError) as ctx:
            build_design(must_cover({"input": TENSOR, "dims": ATTR_LIST}, combos))
        self.assertIn("2^20", str(ctx.exception))


class TestCompositeProjection(unittest.TestCase):
    def test_tuple_numbers_from_actual_lengths(self):
        design = build_design(
            must_cover({"input": TENSOR, "dims": ATTR_LIST}, COMBOS))
        self.assertEqual(design["inputs"][1]["tuple_numbers"]["values"], [1, 3])

    def test_empty_sequence_rejected(self):
        combos = [{"dtype": "fp16", "shape": [2, 3], "dims": []}]
        with self.assertRaises(DeclarationError) as ctx:
            build_design(must_cover({"input": TENSOR, "dims": ATTR_LIST}, combos))
        self.assertIn("空序列", str(ctx.exception))

    def test_missing_attr_dtype_rejected(self):
        contract = {"element_kind": "attr", "runtime_container": "list",
                    "nullable": False}
        with self.assertRaises(DeclarationError) as ctx:
            build_design(must_cover({"dims": contract}, COMBOS))
        self.assertIn("dtype", str(ctx.exception))

    def test_missing_combo_key_rejected(self):
        with self.assertRaises(DeclarationError) as ctx:
            build_design(must_cover({"axis": ATTR_LIST}, COMBOS))
        self.assertIn("axis", str(ctx.exception))


class TestNullableAndOmitted(unittest.TestCase):
    def test_nullable_single_uses_default_token(self):
        contract = {"element_kind": "attr", "runtime_container": "single",
                    "nullable": True, "dtype": "int"}
        design = build_design(
            must_cover({"dim": contract}, [{"dtype": "fp16", "dim": 0}]))
        config = design["inputs"][0]
        self.assertEqual(config["type"], "attr")
        self.assertIn("default", config["ranges"]["valid"]["values"])

    def test_omitted_uses_non_param(self):
        contract = {"element_kind": "attr", "runtime_container": "single",
                    "nullable": False, "omitted": True}
        design = build_design(
            must_cover({"out": contract}, [{"dtype": "fp16"}]))
        config = design["inputs"][0]
        self.assertEqual(config["type"], "attr")
        self.assertEqual(config["dtypes"]["values"], ["non_param"])

    def test_non_nullable_composite_has_no_default(self):
        design = build_design(
            must_cover({"input": TENSOR, "dims": ATTR_LIST}, COMBOS))
        self.assertNotIn("default", design["inputs"][1]["ranges"]["valid"]["values"])


class TestChannels(unittest.TestCase):
    def test_qualified_key_routes_channel(self):
        design = build_design(must_cover(
            {"tensor_input.input": TENSOR, "dims": ATTR_LIST}, COMBOS))
        self.assertEqual(design["tensor_input"]["name"], "input")
        self.assertEqual([c["name"] for c in design["inputs"]], ["dims"])

    def test_generator_counts_are_degenerate(self):
        design = build_design(
            must_cover({"input": TENSOR, "dims": ATTR_LIST}, COMBOS))
        self.assertEqual(design["dtype_numbers"], 1)
        self.assertEqual(design["extra_numbers"], 0)


class SemanticAxesTest(unittest.TestCase):
    """语义轴、extract 规则与参数契约三者自洽——这三样都是 agent 手写的。

    以前挂在 check_atk_capabilities 上（P4/P6/P7），那个脚本整体删除后
    移到这里，仍然只校验 agent 写的东西，不校验 make_yaml 自己的产物。
    """

    def test_axis_pointing_at_a_missing_attr_is_reported(self):
        parameters = {"x": TENSOR}
        combos = [{"dtype": "fp32", "shape": [2, 2], "shift": [1]}]
        source = must_cover(parameters, combos)
        source["axes"] = ["shift"]
        source["extract"] = {"shift": {"from": "attr", "name": "shifts"}}
        with self.assertRaises(DeclarationError) as caught:
            build_design(source)
        self.assertIn("shifts", str(caught.exception))

    def test_mixing_sequence_and_scalar_semantics_is_reported(self):
        parameters = {"x": TENSOR, "shifts": dict(ATTR_LIST)}
        combos = [{"dtype": "fp32", "shape": [2, 2], "shift": [1]},
                  {"dtype": "fp32", "shape": [2, 2], "shift": 3}]
        source = must_cover(parameters, combos)
        source["axes"] = ["shift"]
        source["extract"] = {"shift": {"from": "attr", "name": "shifts",
                                       "runtime_container": "list"}}
        with self.assertRaises(DeclarationError) as caught:
            build_design(source)
        self.assertIn("拆分接口分面", str(caught.exception))

    def test_sequence_axis_without_runtime_container_is_reported(self):
        parameters = {"x": TENSOR, "shifts": dict(ATTR_LIST)}
        combos = [{"dtype": "fp32", "shape": [2, 2], "shift": [1]}]
        source = must_cover(parameters, combos)
        source["axes"] = ["shift"]
        source["extract"] = {"shift": {"from": "attr", "name": "shifts"}}
        with self.assertRaises(DeclarationError) as caught:
            build_design(source)
        self.assertIn("runtime_container", str(caught.exception))


if __name__ == "__main__":
    unittest.main()


class ContractProblemsReportedTogetherTest(unittest.TestCase):
    """参数契约的问题要一次报全。

    每个契约互相独立，一个写错不妨碍检查其余的。挨个抛异常会把「三个契约都
    有问题」变成跑三轮——和覆盖声明校验是同一个成本来源。

    头部块 / parameters / combos 缺失仍然立刻中断：那是结构问题，后面每个
    参数都会连带报错，攒起来只会刷屏。
    """

    BASE = {
        "yaml": {"name": "torch.sum"},
        "combos": [{"dtype": "fp32", "shape": [4, 4], "dim": 1,
                    "keepdim": False}],
        "parameters": {
            "input": {"element_kind": "tensor", "runtime_container": "single"},
            "dim": {"element_kind": "attr", "runtime_container": "single",
                    "dtype": "int64_t", "combo_key": "dim"},
            "keepdim": {"element_kind": "attr", "runtime_container": "single",
                        "dtype": "bool", "combo_key": "keepdim"},
        },
    }

    def _build(self, mutate):
        import copy
        from make_yaml import DeclarationError, build_design
        spec = copy.deepcopy(self.BASE)
        mutate(spec)
        with self.assertRaises(DeclarationError) as caught:
            build_design(spec)
        return str(caught.exception)

    def test_three_broken_contracts_are_reported_in_one_run(self):
        def mutate(spec):
            spec["parameters"]["input"]["shape_key"] = "no_such_key"
            spec["parameters"]["dim"].pop("dtype")
            spec["parameters"]["keepdim"]["element_kind"] = "vector"

        message = self._build(mutate)
        self.assertIn("3 个问题", message)
        self.assertIn("no_such_key", message)
        self.assertIn("dim: 契约缺 dtype", message)
        self.assertIn("keepdim: 契约的 'vector'", message)

    def test_single_problem_stays_a_single_line(self):
        message = self._build(lambda spec: spec["parameters"]["dim"].pop("dtype"))
        self.assertNotIn("个问题", message)
        self.assertIn("dim: 契约缺 dtype", message)

    def test_structural_gap_still_stops_immediately(self):
        def mutate(spec):
            spec.pop("parameters")
            spec["parameters"] = {}
            spec["combos"] = []

        message = self._build(mutate)
        self.assertIn("parameters", message)
        self.assertNotIn("个问题", message)

    def test_unknown_yaml_header_key_is_rejected(self):
        must_cover = {
            "yaml": dict(HEADER, api_typ="roll_cpu"),
            "parameters": {"x": {"element_kind": "tensor",
                                 "runtime_container": "single"}},
            "combos": [{"dtype": "fp32", "shape": [2, 2]}],
        }
        with self.assertRaises(DeclarationError) as caught:
            build_design(must_cover)
        self.assertIn("api_typ", str(caught.exception))


class TestValueDistribution(unittest.TestCase):
    """取值范围与分布：用例数据必须测得出东西。

    roll 那轮的真机事故：`DEFAULT_RANGE = [-5, 5]` 对所有 dtype 无差别使用，
    ATK 先按 μ/σ 采样再按 dtype 截断，整型分面于是塌成常量张量——
    122 条用例里 17 条输入整张只有一个取值，全部判「通过」，占通过总数四成。
    常量输入对任何重排或少算的实现都恒成立，假通过的方向比假失败危险。

    再一条：ATK 在 `random_types` 只有一项时恒取正态
    （`atk/configs/design_config.py:183`），生态标准要求的「均匀与正态各半」
    会静默失效，`values` 里的均匀区间一次也不生效。
    """

    def _tensor_config(self, dtypes, contract=None):
        combos = [{"dtype": dtype, "shape": [4, 5], "x": [1]} for dtype in dtypes]
        parameters = {"input": dict(TENSOR, **(contract or {})), "x": ATTR_LIST}
        design = build_design(must_cover(parameters, combos))
        return design["inputs"][0]["ranges"]["valid"]

    def test_float_only_keeps_the_ecosystem_standard(self):
        # 浮点连续取值下判别力本来就够（实测填充率 0.96 以上），不要动标准。
        ranges = self._tensor_config(["fp16", "fp32", "bf16"])
        self.assertEqual([[-5, 5]], ranges["values"])
        nd = [item for item in ranges["random_types"] if item["name"] == "nd"][0]
        self.assertEqual([-5, 5], nd["mean"])
        self.assertEqual([0.1, 2], nd["std"])

    def test_integer_dtypes_widen_range_and_std(self):
        # int8 与 uint8 的交集是 [0, 127]：最窄的 dtype 不会整体饱和成常量。
        ranges = self._tensor_config(["int8", "uint8", "fp32"])
        self.assertEqual([[0, 127]], ranges["values"])
        nd = [item for item in ranges["random_types"] if item["name"] == "nd"][0]
        self.assertEqual([0, 127], nd["mean"])
        # σ 必须随宽度走，否则 μ 撒得再开、单张张量还是挤在几个取值上。
        self.assertGreater(nd["std"][0], 2)

    def test_unsigned_only_never_uses_a_negative_mean(self):
        # 负 μ 采样后按无符号 dtype 截断 = 整张清零，真机上出现过 900000 元素全 0。
        ranges = self._tensor_config(["uint8", "uint32"])
        self.assertGreaterEqual(ranges["values"][0][0], 0)
        for item in ranges["random_types"]:
            self.assertGreaterEqual(min(item.get("mean", [0])), 0)

    def test_integer_span_is_capped(self):
        # 宽到 2^31 会让算术类算子的中间结果溢出，把数据设计问题伪装成实现缺陷。
        ranges = self._tensor_config(["int32"])
        low, high = ranges["values"][0]
        self.assertLessEqual(high - low + 1, 2 ** 16)
        self.assertGreater(high - low + 1, 2 ** 8)

    def test_declared_range_wins_over_derivation(self):
        # 算子有定义域限制（log、除法、索引）时声明是唯一正确的值域。
        ranges = self._tensor_config(["int32"], {"range": [1, 9]})
        self.assertEqual([[1, 9]], ranges["values"])

    def test_uniform_half_is_actually_emitted(self):
        # 只写一项 nd 时 ATK 恒取正态，均匀那一半会静默消失。
        for dtypes in (["fp32"], ["int8"]):
            names = [item["name"]
                     for item in self._tensor_config(dtypes)["random_types"]]
            self.assertIn("default", names, dtypes)
            self.assertIn("nd", names, dtypes)
