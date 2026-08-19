"""归档只读纪律与缺陷对照表的结构约束。"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPO_ROOT / "docs" / "development" / "taskdoc-source"
ORIGIN = SOURCE / "origin"
DEFECT_MAP = SOURCE / "defect-map.md"


def ids_under(heading, prefix):
    """只在指定小节里抽编号。

    全文扫 `| D\d\d |` 会把别处出现的编号一起捞进来：模板缺陷表里写一句
    「与 D05 同类」，样例缺陷表的断言就跟着红，而它根本没改。编号该属于
    哪张表由所在小节决定，不由它在文件里排第几决定。
    """
    text = DEFECT_MAP.read_text(encoding="utf-8")
    body = text.split(heading, 1)[1].split("\n## ", 1)[0]
    return re.findall(r"\|\s*(%s\d{2})\s*\|" % prefix, body)


class SourceArchiveTest(unittest.TestCase):
    def test_origin_files_are_archived(self):
        for name in ("task_doc_templete_v3.0.md",
                     "task_doc_example_v3.0.md",
                     "checklist-14.md"):
            with self.subTest(name=name):
                self.assertTrue((ORIGIN / name).is_file(), f"{name} 未归档")

    def test_checklist_has_fourteen_rows(self):
        text = (ORIGIN / "checklist-14.md").read_text(encoding="utf-8")
        rows = [l for l in text.splitlines()
                if re.match(r"^\|\s*\d+\s*\|", l)]
        self.assertEqual(14, len(rows), "checklist 应为 14 条")

    def test_defect_map_covers_thirteen_sample_defects(self):
        # D13：黄金样例 §3.5 的生成规则曾经不可联合满足（F7），补的一条
        # 缺陷记录——它不是上游样例本来就有编号的缺陷，是评审后新发现的。
        ids = ids_under("## 样例缺陷", "D")
        self.assertEqual([f"D{n:02d}" for n in range(1, 14)], ids)

    def test_defect_map_covers_five_template_defects(self):
        ids = ids_under("## 模板缺陷", "T")
        self.assertEqual([f"T{n:02d}" for n in range(1, 6)], ids)

    def test_every_defect_names_a_gate_or_repair(self):
        # 最后一列不得为空：缺陷必须指向一条判据或一个修复方向。
        text = DEFECT_MAP.read_text(encoding="utf-8")
        empty = []
        for line in text.splitlines():
            if not re.match(r"^\|\s*[DT]\d{2}\s*\|", line):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 5 or not cells[-1]:
                empty.append(line[:40])
        self.assertEqual([], empty)


if __name__ == "__main__":
    unittest.main()
