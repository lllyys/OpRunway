"""脚手架渲染与派生视图。视图从骨架派生，骨架是唯一可编辑对象。"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _taskdoc_parser as parser  # noqa: E402

MAKE = SKILL_ROOT / "scripts" / "make_taskdoc.py"
RENDER = SKILL_ROOT / "scripts" / "render_views.py"
CHECKLIST = SKILL_ROOT / "references" / "manual-checklist.md"


def spine():
    return json.loads((SKILL_ROOT / "references"
                       / "taskdoc-elements.json").read_text(encoding="utf-8"))


class ScaffoldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "Roll_task_doc.md"
        result = subprocess.run(
            [sys.executable, str(MAKE), "--op", "aclnnRoll",
             "--out", str(self.out)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scaffold_has_every_section(self):
        doc = parser.parse(self.out)
        for number in spine()["sections"]:
            with self.subTest(number=number):
                self.assertIn(number, doc.sections)

    def test_scaffold_title_carries_the_operator_name(self):
        self.assertIn("aclnnRoll", self.out.read_text(encoding="utf-8"))

    def test_scaffold_param_table_header_is_correct(self):
        doc = parser.parse(self.out)
        self.assertEqual(9, len(doc.sections["2.4"].tables[0].header))

    def test_scaffold_fails_structure_gate_but_not_parse(self):
        # 脚手架是空壳：结构解析得动（不是 3），内容判据不满足（是 2）。
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "check_taskdoc.py"),
             "--doc", str(self.out), "--layers", "L0"],
            capture_output=True, text=True)
        self.assertEqual(2, result.returncode)

    def test_refuses_to_overwrite(self):
        result = subprocess.run(
            [sys.executable, str(MAKE), "--op", "aclnnRoll",
             "--out", str(self.out)], capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertIn("已存在", result.stderr + result.stdout)


class ViewsTest(unittest.TestCase):
    def test_checklist_is_in_sync_with_the_spine(self):
        result = subprocess.run([sys.executable, str(RENDER)],
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(CHECKLIST.read_text(encoding="utf-8"), result.stdout)

    def test_checklist_splits_mechanical_from_manual(self):
        text = CHECKLIST.read_text(encoding="utf-8")
        self.assertIn("已被机械判据覆盖", text)
        self.assertIn("仍需人读", text)

    def test_every_checklist_item_appears_once(self):
        text = CHECKLIST.read_text(encoding="utf-8")
        numbers = sorted(int(n) for n in re.findall(r"^\|\s*(\d+)\s*\|", text,
                                                    re.MULTILINE))
        self.assertEqual(list(range(1, 15)), numbers)


if __name__ == "__main__":
    unittest.main()
