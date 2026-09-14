"""md 反解与签名抽取。模板改版时这里先红。"""

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _signature  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"
SAMPLE = SKILL_ROOT / "tests" / "fixtures" / "real_sample.md"

ACLNN_SIGNATURE = """
aclnnStatus aclnnSlidingTileAttentionGetWorkspaceSize(
    const aclTensor          *q,
    const aclTensor          *k,
    const aclTensor          *v,
    aclTensor                *output,
    const aclIntArray *const *windowSize,
    uint64_t                  windowSizeLen,
    int64_t                   textLength,
    bool                      hasText,
    const char               *seqShape,
    uint64_t                 *workspaceSize,
    aclOpExecutor           **executor)

aclnnStatus aclnnSlidingTileAttention(
    void          *workspace,
    uint64_t       workspaceSize,
    aclOpExecutor *executor,
    aclrtStream    stream)
"""

PYTHON_SIGNATURE = """
def sliding_tile_attention(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    window_size: list,
    text_length: int,
    has_text: bool = True,
    seq_shape: str = "30x48x80",
) -> torch.Tensor:
"""


class SignatureTest(unittest.TestCase):
    def test_extracts_aclnn_parameters_in_order(self):
        self.assertEqual(
            ["q", "k", "v", "output", "windowSize", "windowSizeLen",
             "textLength", "hasText", "seqShape"],
            _signature.parameter_names(ACLNN_SIGNATURE))

    def test_drops_workspace_and_executor_boilerplate(self):
        names = _signature.parameter_names(ACLNN_SIGNATURE)
        for noise in ("workspace", "workspaceSize", "executor", "stream"):
            with self.subTest(noise=noise):
                self.assertNotIn(noise, names)

    def test_extracts_python_parameters(self):
        self.assertEqual(
            ["q", "k", "v", "window_size", "text_length", "has_text",
             "seq_shape"],
            _signature.parameter_names(PYTHON_SIGNATURE))

    def test_returns_empty_for_prose(self):
        self.assertEqual([], _signature.parameter_names("这里没有签名。"))

    def test_differs_from_baseline_is_false_when_shared_order_matches(self):
        # F5：aclnn C 签名天然会多出 output、windowSizeLen 这类没有基线
        # 对应物的参数，直接比较整份列表几乎永远不相等。只看两边都出现
        # 的参数、按各自列表内的相对顺序比，golden 样例这份顺序没乱。
        baseline = _signature.parameter_names(PYTHON_SIGNATURE)
        acl = _signature.parameter_names(ACLNN_SIGNATURE)
        self.assertFalse(_signature.differs_from_baseline(baseline, acl))

    def test_differs_from_baseline_is_true_when_shared_params_reorder(self):
        swapped = ACLNN_SIGNATURE.replace(
            "const aclTensor          *q,\n    const aclTensor          *k,",
            "const aclTensor          *k,\n    const aclTensor          *q,")
        self.assertNotEqual(ACLNN_SIGNATURE, swapped)
        baseline = _signature.parameter_names(PYTHON_SIGNATURE)
        acl = _signature.parameter_names(swapped)
        self.assertTrue(_signature.differs_from_baseline(baseline, acl))

    def test_differs_from_baseline_fails_open_without_a_baseline(self):
        # §2.1 没有代码块（纯文字描述基线）时推不出结论，不能因此把
        # 一个永远打不开的必填条件强加给所有任务书。
        acl = _signature.parameter_names(ACLNN_SIGNATURE)
        self.assertFalse(_signature.differs_from_baseline([], acl))


class ParserTest(unittest.TestCase):
    def test_golden_parses_all_declared_sections(self):
        doc = parser.parse(GOLDEN)
        for number in ("1", "2.1", "2.2", "2.3", "2.4", "2.5",
                       "3.1", "3.2", "3.3", "3.4", "3.5", "4", "5", "6", "7", "8"):
            with self.subTest(number=number):
                self.assertIn(number, doc.sections)

    def test_param_table_is_found_with_nine_columns(self):
        doc = parser.parse(GOLDEN)
        tables = doc.sections["2.4"].tables
        self.assertTrue(tables, "§2.4 没解析出表格")
        self.assertEqual(9, len(tables[0].header))

    def test_code_block_carries_the_signature(self):
        doc = parser.parse(GOLDEN)
        blocks = doc.sections["2.3"].code_blocks
        self.assertTrue(blocks, "§2.3 没解析出代码块")
        self.assertIn("q", _signature.parameter_names("\n".join(blocks)))

    def test_sample_without_section_25_still_parses(self):
        # 向后兼容：老任务书缺 §2.5 时解析不崩，缺失交给门禁判。
        doc = parser.parse(SAMPLE)
        self.assertNotIn("2.5", doc.sections)
        self.assertIn("2.4", doc.sections)

    def test_line_numbers_point_at_the_source(self):
        doc = parser.parse(GOLDEN)
        text = GOLDEN.read_text(encoding="utf-8").splitlines()
        section = doc.sections["2.4"]
        self.assertIn("2.4", text[section.start_line - 1])

    def test_unparseable_document_raises(self):
        broken = SKILL_ROOT / "tests" / "fixtures" / "defects" / "no_headings.md"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("这份文档没有任何章节标题。\n", encoding="utf-8")
        with self.assertRaises(parser.ParseError):
            parser.parse(broken)

    def test_html_comments_are_stripped_from_section_bodies(self):
        # F1：make_taskdoc.py 的脚手架占位符是 HTML 注释。解析层不剥掉它，
        # 注释文本就会被当成「已填写内容」，check_nonempty 判不出空章节。
        doc = parser.parse(SKILL_ROOT / "tests" / "fixtures"
                           / "defects" / "d13_scaffold_comment.md")
        section = doc.sections["2.5"]
        self.assertEqual("", "".join(section.lines).strip())

    def test_comment_stripping_preserves_line_numbers(self):
        # 剥注释（包括跨行的）不能把行号搞乱，否则 finding 的 line 就指错地方。
        text = ("# op 任务书\n\n"
                "<!-- 这是一段\n跨越三行的\n多行注释 -->\n\n"
                "## 1. 任务概述\n\n占位\n\n"
                "## 3.1 环境\n\n占位\n")
        tmp = SKILL_ROOT / "tests" / "fixtures" / "defects" / "_tmp_multiline.md"
        tmp.write_text(text, encoding="utf-8")
        try:
            doc = parser.parse(tmp)
            raw_lines = text.splitlines()
            self.assertIn("3.1", raw_lines[doc.sections["3.1"].start_line - 1])
        finally:
            tmp.unlink()

    def test_comments_inside_code_blocks_are_left_alone(self):
        # 代码块里理论上不该出现 HTML 注释，但万一有，不该被解析层动。
        text = ("# op 任务书\n\n## 2.3 接口定义\n\n"
                "```\nint f(int a /* not html */);\n<!-- 不是注释, 是签名一部分 -->\n```\n\n"
                "## 3.1 环境\n\n占位\n")
        tmp = SKILL_ROOT / "tests" / "fixtures" / "defects" / "_tmp_code_comment.md"
        tmp.write_text(text, encoding="utf-8")
        try:
            doc = parser.parse(tmp)
            block = "\n".join(doc.sections["2.3"].code_blocks)
            self.assertIn("<!-- 不是注释", block)
        finally:
            tmp.unlink()


if __name__ == "__main__":
    unittest.main()
