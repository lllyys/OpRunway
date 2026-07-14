#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Q1 样例隔离防回归：断言真 spec 样例不在 acc-spec 运行时路径下。

背景：真 <op>.spec.json 曾放 plugin/acc-common/specs/，被 taskdoc-to-spec.md 指作
「目标 schema」→ acc-spec 产 spec 前读到同题标准答案（软污染）。已迁到 repo 根
samples/specs/（纯人读参考）。本测试把「隔离」机器化，防有人把真样例塞回运行时路径。
"""
import glob
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))


class SpecIsolationTest(unittest.TestCase):
    def test_no_spec_json_in_runtime_specs_dir(self):
        """plugin/acc-common/specs/ 下不得存在任何 *.spec.json（目录不存在也算通过）。"""
        legacy_dir = os.path.join(_HERE, "specs")
        if not os.path.isdir(legacy_dir):
            return  # 目录已删 → 隔离成立
        leaked = glob.glob(os.path.join(legacy_dir, "*.spec.json"))
        self.assertEqual(
            leaked, [],
            "真 spec 样例回流到运行时路径 plugin/acc-common/specs/：%s；应放 samples/specs/" % leaked)

    def test_no_spec_json_directly_under_acc_common(self):
        """acc-common 根也不得直接摆真 *.spec.json（空模板是 *.jsonc，不受此限）。"""
        leaked = glob.glob(os.path.join(_HERE, "*.spec.json"))
        self.assertEqual(
            leaked, [],
            "acc-common 根出现真 spec 样例：%s；应放 samples/specs/" % leaked)

    def test_samples_specs_present(self):
        """迁移目的地 samples/specs/ 应存在且含参考样例（证迁移落地、非凭空删除）。"""
        samples = os.path.join(_ROOT, "samples", "specs")
        self.assertTrue(os.path.isdir(samples), "samples/specs/ 缺失：迁移目的地不存在")
        self.assertTrue(
            glob.glob(os.path.join(samples, "*.spec.json")),
            "samples/specs/ 下无 *.spec.json：样例未迁到位")

    def test_zero_truth_template_present(self):
        """零真值空模板须在（acc-spec 产 spec 时看结构就看它，不看真样例）。"""
        tmpl = os.path.join(_HERE, "spec_schema_template.jsonc")
        self.assertTrue(os.path.isfile(tmpl), "spec_schema_template.jsonc 缺失")


if __name__ == "__main__":
    unittest.main()
