import sys
import unittest
from pathlib import Path

from _paths import SCRIPTS


sys.path.insert(0, str(SCRIPTS))

import _case_utils as case_utils


class CaseUtilsTest(unittest.TestCase):
    def test_flattens_tuple_inputs(self):
        case = {
            "inputs": [
                {
                    "name": "x",
                    "type": "tensor",
                    "dtype": "fp32",
                    "shape": [2, 3],
                },
                [
                    {
                        "name": "dims",
                        "type": "attr_tuple",
                        "dtype": "int",
                        "range_values": 1,
                    },
                    {
                        "name": "dims",
                        "type": "attr_tuple",
                        "dtype": "int",
                        "range_values": 2,
                    },
                ],
            ]
        }
        self.assertEqual(len(case_utils.tensor_inputs(case)), 1)
        self.assertEqual(case_utils.attr_value(case, "dims"), (1, 2))
        self.assertEqual(case_utils.numel([2, 3]), 6)

    def test_signature_is_stable(self):
        values = {"shape": [2, 3], "meta": {"b": 2, "a": 1}}
        first = case_utils.signature(values, ["shape", "meta"])
        second = case_utils.signature(values, ["shape", "meta"])
        self.assertEqual(first, second)

    def test_extract_axis_reads_scalar_dtype(self):
        case = {
            "inputs": [
                {"name": "self", "type": "tensor", "dtype": "fp16", "shape": [2]},
                {
                    "name": "prob",
                    "type": "scalar",
                    "dtype": "bf16",
                    "range_values": 0.5,
                },
            ]
        }
        rule = {"from": "scalar_dtype", "index": 0}
        self.assertEqual(case_utils.extract_axis(case, rule), "bf16")

    def test_extract_axis_returns_none_for_missing_scalar_index(self):
        case = {
            "inputs": [
                {
                    "name": "prob",
                    "type": "scalar",
                    "dtype": "fp32",
                    "range_values": 0.5,
                },
            ]
        }
        rule = {"from": "scalar_dtype", "index": 1}
        self.assertIsNone(case_utils.extract_axis(case, rule))

    def test_default_attr_token_extracts_as_none_semantics(self):
        case = {
            "inputs": [{
                "name": "dims",
                "type": "attr",
                "dtype": "int",
                "range_values": "default",
            }]
        }
        self.assertIsNone(case_utils.attr_value(case, "dims"))


if __name__ == "__main__":
    unittest.main()


class InputBytesAxisTest(unittest.TestCase):
    """性能分档必须按总字节数，不是元素数。

    实测依据：同一个 numel=2097152 换 dtype，device 耗时 8.64us(1B) →
    11.03us(8B) 单调变化；而同一个 numel 段内按元素数分档时，各字节宽度
    耗时极差只有 1.01 倍。元素数不是驱动量。
    """

    def test_bytes_axis_scales_with_dtype_width(self):
        from _case_utils import extract_axis
        rule = {"from": "input_bytes", "index": 0}
        for dtype, width in (("int8", 1), ("fp16", 2), ("fp32", 4), ("complex64", 8)):
            case = {"inputs": [{"shape": [16, 15], "dtype": dtype, "type": "tensor"}]}
            self.assertEqual(extract_axis(case, rule), 240 * width, dtype)

    def test_unknown_dtype_yields_none_not_crash(self):
        from _case_utils import extract_axis
        case = {"inputs": [{"shape": [4], "dtype": "made_up", "type": "tensor"}]}
        self.assertIsNone(extract_axis(case, {"from": "input_bytes", "index": 0}))

    def test_numel_axis_still_works(self):
        from _case_utils import extract_axis
        case = {"inputs": [{"shape": [1024, 31], "dtype": "fp32", "type": "tensor"}]}
        self.assertEqual(extract_axis(case, {"from": "input_numel", "index": 0}), 31744)


class CompositeAttrFlatteningTest(unittest.TestCase):
    """attrs 型复合输入必须投影成扁平的元素序列。

    真实事故：ATK 把 `type: attrs` 的每个元素展成一条 InputCaseConfig，
    元素取值又包在单元素列表里（range_values=[v]）。不脱这层壳时，
    长度 1 的输入恰好等于 combo（[v] == [v]）而长度 >1 的变成 [[a],[b]]，
    于是只有多元素组合投影不上——roll 的 121 条里 65 条这样失配，
    check_coverage 报「必测集缺口 65 组」，而生成其实完全正确。
    """

    @staticmethod
    def _case(name, values, kind="attrs"):
        return {"inputs": [[{"name": name, "type": kind, "range_values": [v]}
                            for v in values]]}

    def test_single_element_list_attr(self):
        from _case_utils import attr_value
        self.assertEqual(attr_value(self._case("dims", [-4]), "dims"), [-4])

    def test_multi_element_list_attr_is_flat(self):
        from _case_utils import attr_value
        self.assertEqual(attr_value(self._case("dims", [0, 6]), "dims"), [0, 6])

    def test_tuple_type_stays_a_tuple(self):
        from _case_utils import attr_value
        got = attr_value(self._case("dims", [1, 2], kind="attr_tuple"), "dims")
        self.assertEqual(got, (1, 2))

    def test_plain_scalar_attr_is_unchanged(self):
        from _case_utils import attr_value
        case = {"inputs": [{"name": "dim", "type": "attr", "range_values": 3}]}
        self.assertEqual(attr_value(case, "dim"), 3)


class FlatAttrUnwrapTest(unittest.TestCase):
    """单值 flat attr 的 range_values 要脱掉单元素壳。

    真实事故（median 验收）：ATK 把 `type: attr` 的取值编码成
    range_values=[v]，而组合表的标量轴值是 v（check_design 的 P10 分支要求
    标量 attr 轴在 combo 里存标量）。不脱壳时 signature() 拿 [v] 比 v 永远
    失配，含 attr 轴的分面 C3/C5 全灭——59 条命中 0 组，误报成设计错误。

    复合输入（attrs/attr_tuple）走上面的分支，这里只管单 spec 的 flat attr。
    """

    @staticmethod
    def _case(kind, range_values):
        return {"inputs": [{"name": "dim", "type": kind,
                            "range_values": range_values}]}

    def test_single_value_is_unwrapped(self):
        from _case_utils import attr_value
        self.assertEqual(attr_value(self._case("attr", [0]), "dim"), 0)

    def test_scalar_type_is_unwrapped_too(self):
        from _case_utils import attr_value
        self.assertEqual(attr_value(self._case("scalar", [2]), "dim"), 2)

    def test_bare_scalar_is_unchanged(self):
        from _case_utils import attr_value
        self.assertEqual(attr_value(self._case("attr", 3), "dim"), 3)

    def test_multi_value_list_is_not_unwrapped(self):
        from _case_utils import attr_value
        self.assertEqual(attr_value(self._case("attr", [0, 1]), "dim"), [0, 1])

    def test_default_token_still_becomes_none(self):
        from _case_utils import attr_value
        self.assertIsNone(attr_value(self._case("attr", "default"), "dim"))
