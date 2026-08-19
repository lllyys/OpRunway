"""L2 明确性门。抓的是读得懂但没有一步能照着执行的写法。"""

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
VAGUE_WORDS = json.loads((SKILL_ROOT / "references" / "vague-words.json")
                         .read_text(encoding="utf-8"))["words"]


def spine():
    return json.loads((SKILL_ROOT / "references"
                       / "taskdoc-elements.json").read_text(encoding="utf-8"))


def layer2(path, context=None):
    text = path.read_text(encoding="utf-8")
    signals = json.loads((SKILL_ROOT / "references"
                          / "random-operator-signals.json").read_text(
                              encoding="utf-8"))["signal_keywords"]
    base = {"random_signal_hit": any(w in text for w in signals),
            "decisions": {}, "expected_header": {}}
    base.update(context or {})
    return _checks.run_layer(parser.parse(path), spine(), "L2", base)


def rules(findings):
    return sorted({f.rule for f in findings})


class ClarityLayerTest(unittest.TestCase):
    def test_golden_passes_layer_two(self):
        self.assertEqual([], layer2(GOLDEN))

    def test_missing_param_table_is_a_red_not_a_skip_at_layer_two(self):
        # F4：no_unbounded_range / error_column_not_uniform 也吃 §2.4 参数表，
        # 表不在时同样不能悄悄 return []。
        text = GOLDEN.read_text(encoding="utf-8")
        start = text.index("| 参数名 | 输入／输出/属性 | 描述 |")
        end = text.index("### 2.5 算子实现约束")
        broken = text[:start] + "参数说明详见接口定义。\n\n" + text[end:]
        self.assertNotEqual(text, broken)
        tmp = DEFECTS / "_tmp_no_param_table_l2.md"
        tmp.write_text(broken, encoding="utf-8")
        try:
            found = layer2(tmp)
        finally:
            tmp.unlink()
        self.assertTrue(found, "§2.4 表格缺失时 L2 必须判红，不能静默通过")

    def test_d06_vague_performance_criterion_is_caught(self):
        found = layer2(DEFECTS / "d06_vague_perf.md")
        self.assertIn("no_vague_word", rules(found))
        self.assertIn("perf_criterion_has_comparator", rules(found))
        self.assertTrue(any("持平" in f.message for f in found))

    def test_missing_gpu_driver_and_cuda_version_is_caught(self):
        # F6 复现：D09 明写「GPU 标杆无驱动/CUDA 版本，性能对标不可复现」，
        # 但 3.1.third_party 的判据只查字面量「torch」在不在，删掉驱动/CUDA
        # 那半句话照样过。§3.1 是模板明文要求写驱动/CUDA 的地方
        # （task-doc-template.md §3.1 说明：「性能对标 GPU 时还要写标杆环境
        # 的驱动与 CUDA 版本」）。
        text = GOLDEN.read_text(encoding="utf-8")
        broken = text.replace(
            "；性能标杆 GPU（A100）环境使用 NVIDIA 驱动 535.104.05、CUDA 12.2",
            "")
        self.assertNotEqual(text, broken)
        tmp = DEFECTS / "_tmp_no_gpu_driver.md"
        tmp.write_text(broken, encoding="utf-8")
        try:
            found = layer2(tmp)
        finally:
            tmp.unlink()
        self.assertIn("env_lists_third_party_versions", rules(found))

    def test_gpu_driver_without_cuda_is_still_incomplete(self):
        text = GOLDEN.read_text(encoding="utf-8")
        broken = text.replace(
            "NVIDIA 驱动 535.104.05、CUDA 12.2", "NVIDIA 驱动 535.104.05")
        self.assertNotEqual(text, broken)
        tmp = DEFECTS / "_tmp_no_cuda.md"
        tmp.write_text(broken, encoding="utf-8")
        try:
            found = layer2(tmp)
        finally:
            tmp.unlink()
        self.assertIn("env_lists_third_party_versions", rules(found))

    def test_d08_uniform_error_column_is_caught(self):
        found = layer2(DEFECTS / "d08_uniform_error.md")
        self.assertIn("error_column_not_uniform", rules(found))

    def test_sample_unbounded_range_is_caught(self):
        found = layer2(SAMPLE)
        self.assertIn("no_unbounded_range", rules(found))
        self.assertTrue(any("替代写法" in f.message for f in found))

    def test_sample_random_signal_demands_a_strategy(self):
        # D12：样例 §3.5 写了「正态分布」命中信号词，却没给对比策略。
        found = layer2(SAMPLE)
        self.assertIn("random_strategy_when_signaled", rules(found))

    def _inject(self, heading, sentence):
        """把一句话插到指定标题下，返回临时文档路径。"""
        text = GOLDEN.read_text(encoding="utf-8")
        self.assertIn(heading, text)
        broken = text.replace(heading, heading + "\n\n" + sentence, 1)
        self.assertNotEqual(text, broken)
        return broken

    def test_vague_word_in_reference_section_is_legal(self):
        # F8：作用域证明的一半——「持平」在 §6 参考资料里合法。
        # 骨架里挂 no_vague_word 的 34 个要素覆盖 §1-§5 与 §7，§6 一个都没有，
        # 所以同一个词在 §6 不该被抓。断言前先确认这个词真在黑名单里：
        # 拿一个不在词表里的词做实验，删掉作用域逻辑断言也照样成立，
        # 那样测的就不是作用域而是词表为空。
        self.assertIn("持平", VAGUE_WORDS)
        broken = self._inject("## 6. 参考资料",
                              "7. 性能与标杆持平的说明见上游 issue 列表。")
        tmp = DEFECTS / "_tmp_vague_in_six.md"
        tmp.write_text(broken, encoding="utf-8")
        try:
            found = layer2(tmp)
        finally:
            tmp.unlink()
        self.assertEqual([], [f for f in found if f.rule == "no_vague_word"])

    def test_the_same_vague_word_in_precision_section_is_caught(self):
        # F8：作用域证明的另一半——同一句话搬进 §3.2 就该判红。
        # 两条测试必须用同一个词、同一句话，差别只有所在章节，
        # 否则证明的是「词表生效」而不是「作用域生效」。
        self.assertIn("持平", VAGUE_WORDS)
        broken = self._inject("### 3.2 精度要求",
                              "7. 性能与标杆持平的说明见上游 issue 列表。")
        tmp = DEFECTS / "_tmp_vague_in_three_two.md"
        tmp.write_text(broken, encoding="utf-8")
        try:
            found = layer2(tmp)
        finally:
            tmp.unlink()
        vague = [f for f in found if f.rule == "no_vague_word"]
        self.assertNotEqual([], vague)
        self.assertEqual({"3.2"}, {f.section for f in vague})
        self.assertTrue(any("持平" in f.message for f in vague))

    def test_reference_section_may_use_advisory_wording(self):
        text = GOLDEN.read_text(encoding="utf-8")
        self.assertIn("## 6.", text)


if __name__ == "__main__":
    unittest.main()
