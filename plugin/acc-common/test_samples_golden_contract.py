"""随插件分发的样例 golden 的**契约与诚实性**守门测试。

为什么单开一个文件（2026-07-23）：本轮新增 Im2col / UpsampleNearest3d / UpsampleNearestExact2d
三份样例 golden，`grep -l 'Im2col\\|Upsample' test_*.py` **零命中**——于是一个真洞溜了进去：
`Im2col/golden.py` 的 `GOLDEN_PROVENANCE` 白纸黑字写「**本文件不为 numel=0 编造输出**」，
而实测 `out_shape([(1,1,0)], …)` 返回 `(4, 2)`——**声明与实现打架，且 fail-closed 被委托给了 torch**
（换个 torch 替身结论就变）。同批的 `UpsampleNearestExact2d` 却有这道闸：**三份 golden，两份防了、一份没防**。

本仓明写 `GOLDEN_PROVENANCE` 的措辞**会被后续 agent 逐字照抄**（含糊一份、抄错一片），
所以「文件里怎么写的」必须能被机器对上「代码怎么做的」。一条断言就能当场逮住那个洞。

跑: cd plugin/acc-common && python3 -m unittest test_samples_golden_contract -v
⚠ 本文件**不校 golden 的数值**（那要真 torch，本机没有；见各 golden 的诚实边界）——只校**契约与自洽**。
"""
import importlib.util
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAMPLES_GOLDEN = os.path.join(_HERE, "..", "samples", "golden")

# 声明了「不支持空 Tensor / 只支持某种空形态」的算子，及其**唯一合法**的含 0 输入（None = 一个都不合法）。
# 依据各 golden.py 自己引的算子约束（aclnn CheckInputDims / 任务书原文），改这张表前先回原文核。
_EMPTY_POLICY = {
    "Im2col": {"legal": [(0, 3, 4, 4)],                       # 只有「4 维且 N==0」合法
               "illegal": [(1, 1, 0), (2, 3, 0), (2, 0, 4, 4), (2, 3, 0, 4), (2, 3, 4, 0)]},
    "UpsampleNearestExact2d": {"legal": [], "illegal": [(0, 3, 4, 4), (2, 0, 4, 4), (2, 3, 0, 4)]},
    "UpsampleNearest3d": {"legal": [], "illegal": [(0, 3, 2, 4, 4), (2, 0, 2, 4, 4), (2, 3, 2, 0, 4)]},
}

# 各算子跑 out_shape 所需的最小 attrs（值取自对应 spec 的默认，只为把函数调起来）。
_ATTRS = {
    "Im2col": {"kernel_size": [2, 2], "dilation": [1, 1], "padding": [1, 1], "stride": [1, 1]},
    "UpsampleNearestExact2d": {"output_size": [4, 4]},
    "UpsampleNearest3d": {"output_size": [2, 4, 4]},
}


def _load(op):
    path = os.path.join(_SAMPLES_GOLDEN, op, "golden.py")
    spec = importlib.util.spec_from_file_location(f"_sample_golden_{op.lower()}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sample_ops():
    """样例 golden 目录下的算子名（不写死清单——新增算子自动纳入守门）。"""
    root = os.path.realpath(_SAMPLES_GOLDEN)
    return sorted(d for d in os.listdir(root)
                  if os.path.isfile(os.path.join(root, d, "golden.py")))


class SampleGoldenContractTest(unittest.TestCase):
    """三项必需导出 + out_shape 可选；**不写死算子清单**，新增的自动被守。"""

    def test_all_samples_export_required_contract(self):
        ops = _sample_ops()
        self.assertGreaterEqual(len(ops), 4, f"样例 golden 至少应有 4 个，实得 {ops}")
        for op in ops:
            mod = _load(op)
            for attr in ("golden_fn", "GOLDEN_SOURCE", "GOLDEN_PROVENANCE"):
                self.assertTrue(hasattr(mod, attr), f"{op}/golden.py 缺 {attr}")
            self.assertTrue(callable(mod.golden_fn), op)
            for attr in ("GOLDEN_SOURCE", "GOLDEN_PROVENANCE"):
                v = getattr(mod, attr)
                self.assertIsInstance(v, str, f"{op}.{attr}")
                self.assertTrue(v.strip(), f"{op}.{attr} 不得为空串")
            osh = getattr(mod, "out_shape", None)
            self.assertTrue(osh is None or callable(osh), f"{op}.out_shape 导出了但不可调用")

    def test_elementwise_samples_do_not_export_out_shape(self):
        """缺省语义（不导出 = 输出同输入形状）必须保持——4 份 elementwise 样例一律不导出。

        它们一旦导出，`gen_cases` 就会走「声明优先」分支、`repo_adapter` 就会改产扩展 manifest，
        而三份已跑通真机的 runner 只认传统行 → 静默改变既有算子的通路。"""
        for op in ("IsClose", "Sign", "Equal", "Neg"):
            self.assertIsNone(getattr(_load(op), "out_shape", None),
                              f"{op} 是 elementwise，不该导出 out_shape（缺省语义即同形）")


class EmptyTensorFailClosedTest(unittest.TestCase):
    """**本文件的存在理由**：声称「不为 numel=0 编造输出」的 golden，必须真的拒。

    fail-closed 不能委托给 torch——那样换个替身/换个 torch 版本结论就变，
    且 `--dry-run` 阶段（不 import torch）根本走不到那层拦截。"""

    def test_illegal_empty_shapes_rejected_by_out_shape(self):
        for op, pol in _EMPTY_POLICY.items():
            mod = _load(op)
            osh = getattr(mod, "out_shape", None)
            self.assertTrue(callable(osh), f"{op} 应导出 out_shape（它是形变类算子）")
            for shp in pol["illegal"]:
                with self.assertRaises(ValueError, msg=f"{op} 应拒非法空形态 {shp}"):
                    osh([shp], dict(_ATTRS[op]))

    def test_legal_empty_shape_still_accepted(self):
        """对照：算子确实允许的那种空形态不得被误杀（否则就成了矫枉过正）。"""
        for op, pol in _EMPTY_POLICY.items():
            osh = _load(op).out_shape
            for shp in pol["legal"]:
                out = osh([shp], dict(_ATTRS[op]))
                self.assertEqual(int(out[0]), 0, f"{op}{shp} 的合法空形态应产 N=0 的输出，得 {out}")

    def test_provenance_claim_matches_behavior(self):
        """诚实性对账：`GOLDEN_PROVENANCE` 里声称「不为 numel=0 编造输出」的，行为必须相符。

        这正是 Im2col 那个洞的形状——声明写了、代码没做。"""
        for op in _EMPTY_POLICY:
            mod = _load(op)
            prov = mod.GOLDEN_PROVENANCE
            if "numel=0" not in prov and "空Tensor" not in prov and "空 Tensor" not in prov:
                continue                                   # 没声称就不苛求
            bad = _EMPTY_POLICY[op]["illegal"][0]
            with self.assertRaises(ValueError, msg=f"{op} 的 provenance 声称拒空，行为必须相符"):
                mod.out_shape([bad], dict(_ATTRS[op]))


if __name__ == "__main__":
    unittest.main()
