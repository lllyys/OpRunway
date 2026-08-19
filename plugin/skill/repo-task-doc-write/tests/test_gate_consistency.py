"""L1 一致性门。判别力最强的一层：跨章节对不上就是硬伤。"""

import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _checks  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"
SAMPLE = SKILL_ROOT / "tests" / "fixtures" / "real_sample.md"
DEFECTS = SKILL_ROOT / "tests" / "fixtures" / "defects"


def spine():
    return json.loads((SKILL_ROOT / "references"
                       / "taskdoc-elements.json").read_text(encoding="utf-8"))


def layer1(path, context=None):
    return _checks.run_layer(parser.parse(path), spine(), "L1", context or {
        "expected_header": {}, "decisions": {}})


def rules(findings):
    return sorted({f.rule for f in findings})


class ConsistencyLayerTest(unittest.TestCase):
    def test_golden_passes_layer_one(self):
        self.assertEqual([], layer1(GOLDEN))

    def test_d01_missing_parameter_is_caught(self):
        found = layer1(DEFECTS / "d01_missing_param.md")
        self.assertIn("signature_matches_param_table", rules(found))
        self.assertTrue(any("windowSizeLen" in f.message for f in found))

    def test_d03_range_conflicts_with_distribution(self):
        found = layer1(DEFECTS / "d03_range_conflict.md")
        self.assertIn("range_matches_distribution", rules(found))

    def test_d07_model_missing_from_perf_table(self):
        found = layer1(DEFECTS / "d07_missing_model.md")
        self.assertIn("models_covered_by_perf_table", rules(found))
        self.assertTrue(any("910B4" in f.message for f in found))

    def test_threshold_value_from_the_wrong_dtype_row_is_caught(self):
        # F3：把 FLOAT16 的 rtol 从 2^-9 抄成 2^-3（那是 FLOAT8 E5M2 的
        # atol）。旧判据只问「2^-N 这个 token 在标准里出没出现过」，标准里
        # 本来就有 2^-3，所以旧判据看不出这是抄错列。
        text = GOLDEN.read_text(encoding="utf-8")
        broken = text.replace(
            "| rtol | 2^-9 (1.95e-3) | 2^-6 (1.56e-2) |",
            "| rtol | 2^-3 (1.95e-3) | 2^-6 (1.56e-2) |", 1)
        self.assertNotEqual(text, broken)
        tmp = DEFECTS / "_tmp_wrong_threshold.md"
        tmp.write_text(broken, encoding="utf-8")
        try:
            found = layer1(tmp)
        finally:
            tmp.unlink()
        self.assertIn("thresholds_match_standard", rules(found))
        self.assertTrue(any("2^-3" in f.message for f in found))

    def test_swapped_fp16_bf16_thresholds_are_caught(self):
        # F3：FLOAT16 与 BFLOAT16 两列阈值对调——最可能发生的抄写错误。
        text = GOLDEN.read_text(encoding="utf-8")
        broken = text.replace(
            "| rtol | 2^-9 (1.95e-3) | 2^-6 (1.56e-2) |\n"
            "    | atol | 2^-9 (1.95e-3) | 2^-6 (1.56e-2) |",
            "| rtol | 2^-6 (1.56e-2) | 2^-9 (1.95e-3) |\n"
            "    | atol | 2^-6 (1.56e-2) | 2^-9 (1.95e-3) |", 1)
        self.assertNotEqual(text, broken)
        tmp = DEFECTS / "_tmp_swapped_threshold.md"
        tmp.write_text(broken, encoding="utf-8")
        try:
            found = layer1(tmp)
        finally:
            tmp.unlink()
        self.assertIn("thresholds_match_standard", rules(found))

    def test_d05_attr_range_conflict_is_caught(self):
        # D05：样例 §2.4 window_size 值域写 (0, ∞)，§3.5 却给了 [1,100] 平均分布。
        # 它是 Attr，生成规则在「Attr 覆盖规则」列，Tensor 列写的是「-」。
        # 判据原来用 `dist or attrs` 取规则，而「-」是真值，or 一路短路成「-」，
        # 下一行的 rule == "-" 就把整行跳过了——Attr 参数的值域从不被核对。
        found = layer1(SAMPLE)
        conflicts = [f for f in found
                     if f.rule == "range_matches_distribution"
                     and "window_size" in f.message]
        self.assertNotEqual([], conflicts)

    def test_real_sample_carries_the_expected_defects(self):
        # 回归锚：上游样例的缺陷清单稳定，抓多抓少都说明判据动了。
        found = rules(layer1(SAMPLE))
        for rule in ("signature_matches_param_table",
                     "range_matches_distribution",
                     "models_covered_by_perf_table"):
            with self.subTest(rule=rule):
                self.assertIn(rule, found)

    def test_missing_generation_table_is_a_red_not_a_skip(self):
        # F4：§3.5 生成规则表换成一句话，range_matches_distribution 和
        # params_covered_by_generation_table 原来都是「表不在就 return []」，
        # 静默跳过——五层全 0，验收侧彻底失去数据生成规则却拿到通过。
        text = GOLDEN.read_text(encoding="utf-8")
        start = text.index("| 参数名 | Tensor值域分布 | Attr 覆盖规则 |")
        end = text.index("\n\n", text.index("| textLength | - | hasText=true"))
        broken = text[:start] + "输入数据按正态分布生成，具体见测试代码。" + text[end:]
        self.assertNotEqual(text, broken)
        tmp = DEFECTS / "_tmp_no_generation_table.md"
        tmp.write_text(broken, encoding="utf-8")
        try:
            found = layer1(tmp)
        finally:
            tmp.unlink()
        self.assertTrue(found, "§3.5 表格缺失时 L1 必须判红，不能静默通过")

    def test_missing_param_table_is_a_red_not_a_skip(self):
        # F4：§2.4 参数表换成一句话，signature_matches_param_table /
        # dtypes_covered_by_threshold_table 等都依赖这张表，表不在就该判红。
        text = GOLDEN.read_text(encoding="utf-8")
        start = text.index("| 参数名 | 输入／输出/属性 | 描述 |")
        end = text.index("### 2.5 算子实现约束")
        broken = text[:start] + "参数说明详见接口定义。\n\n" + text[end:]
        self.assertNotEqual(text, broken)
        tmp = DEFECTS / "_tmp_no_param_table.md"
        tmp.write_text(broken, encoding="utf-8")
        try:
            found = layer1(tmp)
        finally:
            tmp.unlink()
        self.assertTrue(found, "§2.4 表格缺失时 L1 必须判红，不能静默通过")

    def test_style_mismatch_reports_differently_than_missing(self):
        # D02 与 D01 是两种病：命名风格不一致 vs 真的漏参。报错要分得开。
        found = layer1(SAMPLE)
        messages = " ".join(f.message for f in found)
        self.assertIn("风格", messages)


if __name__ == "__main__":
    unittest.main()
