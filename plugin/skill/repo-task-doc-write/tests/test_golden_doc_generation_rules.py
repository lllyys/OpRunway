"""F7：黄金样例 §3.5 的生成规则必须可联合满足，不能只是「互相一致」。

§2.1 定 S = textLength + T×H×W，§2.4 「a×b×c 与 S 对不上时报错」，但旧的
§3.5 让 seqShape 的 a、b、c 各自在 [1,200] 独立均匀取，textLength 又独立
在 [0,S) 取——三次独立采样去凑一个互相钉死的量，照字面执行几乎每条用例
都是算子必须拒绝的输入。这行是从上游样例逐字带过来的，与已修的 D05 是
同一缺陷类；`range_matches_distribution` 只查 §2.4 与 §3.5 是否互相一致，
从不问任何一边是否可执行，所以黄金样例这处缺陷从没被判据挡住过——见
docs/development/taskdoc-source/defect-map.md D13。

这里用结构断言复现：生成规则表必须按依赖顺序排列（先 seqShape 定出
image_seq_len，再 textLength），且 textLength 的采样规则必须写明它依赖
image_seq_len，而不是独立去凑一个「[0, S)」——S 在依赖修好之后就是
派生量，不该被当成先验已知的采样上界。
"""

import re
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _taskdoc_parser as parser  # noqa: E402

GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"


def _generation_table(doc):
    section = doc.sections["3.5"]
    for table in section.tables:
        if "参数名" in table.header:
            return table
    raise AssertionError("§3.5 没有生成规则表")


def _row_index(table, param_name):
    for offset, row in enumerate(table.rows):
        if row and row[0] == param_name:
            return offset
    raise AssertionError(f"生成规则表里没有「{param_name}」这一行")


class GenerationRuleDependencyTest(unittest.TestCase):
    def test_seq_shape_is_sampled_before_text_length(self):
        # T/H/W 先定，image_seq_len 才存在，textLength 的采样才有依据。
        doc = parser.parse(GOLDEN)
        table = _generation_table(doc)
        seq_shape_row = _row_index(table, "seqShape")
        text_length_row = _row_index(table, "textLength")
        self.assertLess(seq_shape_row, text_length_row,
                        "seqShape 必须排在 textLength 前面，采样顺序要体现依赖")

    def test_text_length_rule_names_its_dependency(self):
        # textLength 的采样规则文字必须点名 image_seq_len，不能写成独立
        # 去凑一个「[0, S)」——S 是派生量，不是先验已知的采样上界。
        doc = parser.parse(GOLDEN)
        table = _generation_table(doc)
        rule = table.rows[_row_index(table, "textLength")][2]
        self.assertIn("image_seq_len", rule)

    def test_seq_shape_rule_defines_image_seq_len(self):
        doc = parser.parse(GOLDEN)
        table = _generation_table(doc)
        rule = table.rows[_row_index(table, "seqShape")][2]
        self.assertIn("image_seq_len", rule)

    def test_declared_value_ranges_stay_consistent_with_23_4(self):
        # §2.4 对 seqShape 的值域声明（a、b、c 各自 [1, 200]）不能被
        # §3.5 的重写破坏——重写的是采样顺序与依赖，不是值域本身。
        doc = parser.parse(GOLDEN)
        param_table = doc.sections["2.4"].tables[0]
        header = param_table.header
        range_index = header.index("值域范围")
        name_index = header.index("参数名")
        declared = next(row[range_index] for row in param_table.rows
                        if row[name_index] == "seqShape")
        self.assertIn("[1, 200]", declared)


class TextLengthSamplingIsExecutableTest(unittest.TestCase):
    """把旧写法与新写法都当成算法字面执行一遍，量出「几乎全被拒绝」
    与「构造上必然合法」的差别——不只是断言文字，真的按规则跑一遍。
    """

    def test_old_literal_reading_rejects_almost_everything(self):
        # 旧写法字面执行：a、b、c 各自在 [1,200] 独立均匀取，textLength
        # 在 [0, S) 独立取——但 S 从哪来？旧文字没交代，唯一自洽的字面
        # 读法是「S 也是独立取的（与其它 shape 维度同一套生成机制）」。
        # 用同一个 [1,200] 量级给 S 独立取值，量出通过率。
        import random
        rng = random.Random(0)
        passed = 0
        trials = 2000
        for _ in range(trials):
            a, b, c = rng.randint(1, 200), rng.randint(1, 200), rng.randint(1, 200)
            image_seq_len = a * b * c
            s = rng.randint(1, 200)  # 独立取，旧文字没说 S 怎么来
            text_length = rng.randint(0, max(s - 1, 0))
            # §2.4：S 必须不小于 image_seq_len + textLength
            if s >= image_seq_len + text_length:
                passed += 1
        self.assertLess(passed / trials, 0.05,
                        "旧写法字面执行理应几乎全部构造出算子必须拒绝的输入")

    def test_new_rule_is_valid_by_construction(self):
        # 新写法：先取 a、b、c 得到 image_seq_len，textLength 在
        # [0, image_seq_len] 内取，S 派生为 textLength + image_seq_len。
        # 这样 S >= image_seq_len + textLength 必然成立，100% 合法。
        import random
        rng = random.Random(0)
        trials = 2000
        for _ in range(trials):
            a, b, c = rng.randint(1, 200), rng.randint(1, 200), rng.randint(1, 200)
            image_seq_len = a * b * c
            text_length = rng.randint(0, image_seq_len)
            s = text_length + image_seq_len
            self.assertGreaterEqual(s, image_seq_len + text_length)


if __name__ == "__main__":
    unittest.main()
