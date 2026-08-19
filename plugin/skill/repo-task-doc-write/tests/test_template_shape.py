"""修复版模板的章节与表头形态。模板改版时这里先红。"""

import json
import re
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_ROOT / "references" / "task-doc-template.md"
ORIGIN = (Path(__file__).resolve().parents[3] / "docs" / "development"
          / "taskdoc-source" / "origin" / "task_doc_templete_v3.0.md")

SPINE = json.loads((SKILL_ROOT / "references" / "taskdoc-elements.json")
                   .read_text(encoding="utf-8"))

PARAM_COLUMNS = ["参数名", "输入／输出/属性", "描述", "数据类型", "dtype类型",
                 "数据排布格式", "维度(shape)", "值域范围", "异常行为"]


def headings(path):
    return re.findall(r"^#{2,3}\s+(.+?)\s*$", path.read_text(encoding="utf-8"),
                      re.MULTILINE)


class TemplateShapeTest(unittest.TestCase):
    def test_keeps_every_original_section_number(self):
        # 红线 B：只做加法。原模板的章节号一个都不能少或改。
        origin_numbers = re.findall(r"^#{2,3}\s+([\d.]+)\s",
                                    ORIGIN.read_text(encoding="utf-8"),
                                    re.MULTILINE)
        new_numbers = re.findall(r"^#{2,3}\s+([\d.]+)\s",
                                 TEMPLATE.read_text(encoding="utf-8"),
                                 re.MULTILINE)
        self.assertEqual([], [n for n in origin_numbers if n not in new_numbers])

    def test_adds_implementation_constraint_section(self):
        # T03：§2.5 是新增的，加在 §2.4 之后而不是插在中间。
        numbers = re.findall(r"^#{2,3}\s+([\d.]+)\s",
                             TEMPLATE.read_text(encoding="utf-8"), re.MULTILINE)
        self.assertIn("2.5", numbers)
        self.assertLess(numbers.index("2.4"), numbers.index("2.5"))
        self.assertLess(numbers.index("2.5"), numbers.index("3.1"))

    def test_constraint_section_lists_all_six_items(self):
        # T03 的另一半：只验位置不验内容，§2.5 空着一张表也算「加上了」。
        # 六项的权威在骨架（2.5.* 六个要素），这里按骨架的 name 逐条对，
        # 而不是在测试里再抄一份——抄一份就有两个真相，改骨架不会红。
        expected = [e["name"] for k, e in SPINE["elements"].items()
                    if e["section"] == "2.5"]
        self.assertEqual(6, len(expected))
        text = TEMPLATE.read_text(encoding="utf-8")
        section = text.split("### 2.5")[1].split("## 3.")[0]
        for name in expected:
            with self.subTest(item=name):
                self.assertIn(name, section)

    def test_constraint_items_each_state_a_fill_criterion(self):
        # 六项每项都要给填写判据：只列约束项名字，填的人还是不知道写什么。
        # 表格每行两格，第二格不得为空——这是 §2.5 与原模板 T02 那批
        # 疑问句表头的区别所在。
        text = TEMPLATE.read_text(encoding="utf-8")
        section = text.split("### 2.5")[1].split("## 3.")[0]
        rows = [l for l in section.splitlines()
                if l.strip().startswith("|") and "---" not in l]
        naked = []
        for row in rows[1:]:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if len(cells) < 2 or not cells[1]:
                naked.append(cells[0] if cells else row[:30])
        self.assertEqual([], naked)

    def test_param_table_header_has_nine_columns(self):
        # 定位限定在 §2.4 切片内，并要求下一行是分隔行。
        # 全文扫「| 参数名」会把 §2.4 里那张逐列判据表的首行数据也认成表头
        # （它的第一格也写着「参数名」），于是文档里两张表的先后顺序
        # 变成了一条没人声明过的隐藏契约。
        text = TEMPLATE.read_text(encoding="utf-8")
        section = text.split("### 2.4")[1].split("### 2.5")[0].splitlines()
        header = None
        for index, line in enumerate(section):
            if not line.strip().startswith("| 参数名"):
                continue
            following = section[index + 1].strip() if index + 1 < len(section) else ""
            if not re.match(r"^\|[\s:|-]+\|$", following):
                continue
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            break
        self.assertIsNotNone(header, "§2.4 表头未找到（要有九列且下一行是分隔行）")
        self.assertEqual(PARAM_COLUMNS, header)

    def test_precision_requirement_is_mandatory_not_advisory(self):
        # T01：原模板写「建议参考生态标准」，把硬标准写成软建议。
        text = TEMPLATE.read_text(encoding="utf-8")
        section = text.split("### 3.2")[1].split("### 3.3")[0]
        self.assertNotIn("建议参考", section)
        self.assertIn("必须满足", section)

    def test_random_operator_branch_exists(self):
        # T04：随机类算子的对比策略要有位置写，四个取值要列出来。
        # 定位限定在 §3.2 切片内：四个取值散在文档任何角落都能让全文
        # assertIn 通过，但模板要的是「§3.2 里有这个分支」——写在 §7
        # 注意事项里当闲话提一句，填表的人不会在精度要求那节看到它。
        text = TEMPLATE.read_text(encoding="utf-8")
        section = text.split("### 3.2")[1].split("### 3.3")[0]
        for strategy in ("equal_vs_builtin_pinned_seed",
                         "deterministic_boundary_only",
                         "distribution_test", "self_consistency"):
            with self.subTest(strategy=strategy):
                self.assertIn(strategy, section)

    def test_param_column_guidance_is_not_a_question(self):
        # T02：原模板每列给的是疑问句，疑问句不是判据。
        text = TEMPLATE.read_text(encoding="utf-8")
        section = text.split("### 2.4")[1].split("### 2.5")[0]
        self.assertIn("填写判据", section)
        questions = [l for l in section.splitlines()
                     if l.strip().startswith("|") and l.count("？") > 1]
        self.assertEqual([], questions, "表格里还留着疑问句式的填写要求")


if __name__ == "__main__":
    unittest.main()
