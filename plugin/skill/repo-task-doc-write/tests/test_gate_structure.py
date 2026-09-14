"""L0 结构门。缺陷编号见 docs/development/taskdoc-source/defect-map.md。"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _checks  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

GATE = SKILL_ROOT / "scripts" / "check_taskdoc.py"
GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"
DEFECTS = SKILL_ROOT / "tests" / "fixtures" / "defects"


def spine():
    return json.loads(
        (SKILL_ROOT / "references" / "taskdoc-elements.json").read_text(
            encoding="utf-8"))


def layer0(path):
    return _checks.run_layer(parser.parse(path), spine(), "L0", {})


def rules(findings):
    return sorted({f.rule for f in findings})


class StructureLayerTest(unittest.TestCase):
    def test_golden_passes_layer_zero(self):
        # 红线 D：黄金样例必须真的过。没有这条，门禁可以靠什么都判红通过。
        self.assertEqual([], layer0(GOLDEN))

    def test_d10_inline_link_is_caught(self):
        # F11：「文件夹路径」从占位符词表里删掉了（见 vague-words.json），
        # D10 这份样例现在只靠 no_inline_link 抓，no_inline_link 本就够。
        found = layer0(DEFECTS / "d10_placeholder.md")
        self.assertIn("no_inline_link", rules(found))

    def test_scaffold_comment_leaves_section_empty(self):
        # F1：把 §2.5 换回 make_taskdoc.py 生成的脚手架注释，解析层已经把
        # 注释剥空，check_nonempty 必须判红，不能让注释文本冒充已填内容。
        found = layer0(DEFECTS / "d13_scaffold_comment.md")
        self.assertIn("nonempty", rules(found))
        self.assertTrue(any(f.section == "2.5" for f in found))

    def test_real_folder_path_wording_is_not_a_false_positive(self):
        # F11 复现：「文件夹路径」是个中文常用名词，混进占位符词表后，
        # 一条配了真实裸链接的正常描述也会被判「残留占位符」。
        # §3.5 挂了 no_placeholder（3.5.tooling），用真实场景的措辞复现。
        text = ("# op 任务书\n\n## 3.5 自验要求\n\n"
                "自测用例文件夹路径见：https://gitcode.com/example/tests\n")
        tmp = DEFECTS / "_tmp_real_folder_path.md"
        tmp.write_text(text, encoding="utf-8")
        try:
            found = layer0(tmp)
        finally:
            tmp.unlink()
        self.assertNotIn("no_placeholder", rules(found))

    def test_bare_placeholder_word_is_caught(self):
        # F1：「待填写」进了 placeholders 词表——就算不是脚手架注释，
        # 有人手打了这几个字留在正文里，也要被当成占位符抓出来。
        # §3.5 是 3.5.tooling 挂了 no_placeholder 的地方（D10 就在这一节）。
        text = ("# op 任务书\n\n## 3.5 自验要求\n\n待填写：稍后补充自验工具\n")
        tmp = DEFECTS / "_tmp_bare_placeholder.md"
        tmp.write_text(text, encoding="utf-8")
        try:
            found = layer0(tmp)
        finally:
            tmp.unlink()
        self.assertIn("no_placeholder", rules(found))
        self.assertTrue(any("待填写" in f.message for f in found))

    def test_d11_missing_section_is_caught(self):
        found = layer0(DEFECTS / "d11_missing_25.md")
        self.assertIn("section_present", rules(found))
        self.assertTrue(any("2.5" in f.message for f in found))

    def test_findings_point_at_a_line(self):
        for finding in layer0(DEFECTS / "d10_placeholder.md"):
            with self.subTest(rule=finding.rule):
                self.assertGreater(finding.line, 0)

    def test_image_links_are_not_flagged(self):
        # §8 固定内容里有 ![环境截图](./pics/xxx.png)，图片不算隐藏跳转链接。
        found = layer0(GOLDEN)
        self.assertEqual([], [f for f in found if f.rule == "no_inline_link"])


class GateCliTest(unittest.TestCase):
    def test_golden_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(GATE), "--doc", str(GOLDEN), "--layers", "L0"],
            capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_defect_exits_two(self):
        result = subprocess.run(
            [sys.executable, str(GATE), "--doc",
             str(DEFECTS / "d10_placeholder.md"), "--layers", "L0"],
            capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertIn("no_inline_link", result.stdout)

    def test_unparseable_exits_three(self):
        result = subprocess.run(
            [sys.executable, str(GATE), "--doc",
             str(DEFECTS / "no_headings.md"), "--layers", "L0"],
            capture_output=True, text=True)
        self.assertEqual(3, result.returncode)

    def test_missing_generation_table_exits_two_end_to_end(self):
        # F4 端到端复现：§3.5 生成规则表换成一句话，旧实现「判据本身」
        # 静默跳过——不传 --evidence-dir 就没有 acceptance_map 兜底，
        # 五层全 0，exit 0。--decisions 给全，把 L3 的干扰去掉，
        # 专门验证 L1（判据本身，不是兜底）已经能判红。
        text = GOLDEN.read_text(encoding="utf-8")
        start = text.index("| 参数名 | Tensor值域分布 | Attr 覆盖规则 |")
        end = text.index("\n\n", text.index("| textLength | - | hasText=true"))
        broken = text[:start] + "输入数据按正态分布生成，具体见测试代码。" + text[end:]
        self.assertNotEqual(text, broken)
        decisions = SKILL_ROOT / "tests" / "fixtures" / "decisions_golden.json"
        tmp = DEFECTS / "_tmp_cli_no_generation_table.md"
        tmp.write_text(broken, encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, str(GATE), "--doc", str(tmp),
                 "--decisions", str(decisions)],
                capture_output=True, text=True)
        finally:
            tmp.unlink()
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("通过 ·", result.stdout)


if __name__ == "__main__":
    unittest.main()
