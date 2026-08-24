"""测试路径解析同时支持嵌套源与按侧展开的产物。"""

import tempfile
import unittest
from pathlib import Path

from _paths import (is_nested_source, layout_side, require_nested_source,
                    side_page, skill_root)


class PathsTest(unittest.TestCase):
    def test_finds_skill_root_from_a_test_file(self):
        root = skill_root(Path(__file__))
        self.assertTrue((root / "references" / "artifact-contracts.json").is_file())
        if is_nested_source(root):
            self.assertIsNone(layout_side(root))
        else:
            self.assertIn(layout_side(root), {"case-gen", "acceptance"})

    def test_nested_source_uses_the_two_side_pages(self):
        root = skill_root(Path(__file__))
        require_nested_source(self, "两个嵌套侧入口的路径")
        self.assertEqual(root / "case-gen" / "SKILL.md",
                         side_page("case-gen", root))
        self.assertEqual(root / "acceptance" / "SKILL.md",
                         side_page("acceptance", root))

    def test_expanded_product_uses_its_root_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "references").mkdir()
            (root / "references" / "artifact-contracts.json").write_text(
                "{}", encoding="utf-8")
            page = root / "SKILL.md"
            page.write_text("---\nname: repo-task-case-gen\n---\n", encoding="utf-8")
            nested_test = root / "tests" / "test_example.py"

            self.assertEqual(root.resolve(), skill_root(nested_test))
            self.assertFalse(is_nested_source(root))
            self.assertEqual("case-gen", layout_side(root))
            self.assertEqual(page, side_page("case-gen", root))

    def test_expanded_product_rejects_the_other_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "references").mkdir()
            (root / "references" / "artifact-contracts.json").write_text(
                "{}", encoding="utf-8")
            (root / "SKILL.md").write_text(
                "---\nname: repo-task-atk-accept\n---\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                side_page("case-gen", root)


if __name__ == "__main__":
    unittest.main()
