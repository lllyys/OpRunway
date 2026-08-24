"""仓级文档的行文门禁。规则正文在 .claude/rules/prose-style.md。

两个 skill 各自的 test_document_style.py 只管自己目录里的文件，仓根的
README.md、CLAUDE.md 与 docs/skills/ 谁都管不到。这一份补上那块。

新文档基线是 0：写进来就必须合规，不开新欠账。存量欠账的棘轮在验收 skill
自己的 PROSE_BASELINE 里，与本文件无关。
"""

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 判据实现复用验收 skill 的测试模块，与 repo-task-doc-write 的做法一致：
# 抄一份会漂，而 tests/ 本来就不参与发布。按路径加载并起别名，避开同名
# 文件在同一次 pytest 收集里撞 sys.modules 的坑（见 pytest.ini）。
_ATK_TESTS = REPO_ROOT / "skill" / "repo-task-case-gen" / "tests"
sys.path.insert(0, str(_ATK_TESTS))
_ATK_STYLE_MODULE = _ATK_TESTS / "test_document_style.py"
_spec = importlib.util.spec_from_file_location(
    "_repo_docs_atk_style", _ATK_STYLE_MODULE)
_atk_style = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_atk_style)
prose_violations = _atk_style.prose_violations
prose_lines = _atk_style.prose_lines

SCANNED = [REPO_ROOT / "README.md", REPO_ROOT / "CLAUDE.md",
           *sorted((REPO_ROOT / "docs" / "skills").rglob("*.md"))]

# 篇幅预算。超了就是没筛干净——README 只讲怎么做，为什么进 docs/skills/。
LINE_BUDGET = {
    "README.md": 70,
    "CLAUDE.md": 100,
    "design.md": 60,
    "quickstart.md": 50,
}


class RepoDocsStyleTest(unittest.TestCase):
    def test_scanned_set_is_not_empty(self):
        """docs/skills/ 被改名或搬走时，别让门禁静悄悄地扫了个空。"""
        names = {p.name for p in SCANNED}
        self.assertIn("README.md", names)
        self.assertIn("CLAUDE.md", names)
        self.assertGreaterEqual(
            len([p for p in SCANNED if p.name == "design.md"]), 2,
            "每个 skill 都该有一份 docs/skills/<name>/design.md")

    def test_no_prose_violations(self):
        failures = []
        for path in SCANNED:
            for rule, line, note in prose_violations(path):
                rel = path.relative_to(REPO_ROOT)
                failures.append(f"{rel}:{line} 违反规则{rule}——{note}")
        self.assertEqual(
            [], failures,
            "行文规则见 .claude/rules/prose-style.md：\n" + "\n".join(failures))

    def test_lines_within_100_chars(self):
        # 用 prose_lines 而不是裸行：表格、标题、代码块不受宽度约束，
        # 与验收 skill 的 test_prose_lines_stay_within_width 同一把尺子。
        failures = []
        for path in SCANNED:
            for number, line in prose_lines(path):
                if len(line) > 100:
                    rel = path.relative_to(REPO_ROOT)
                    failures.append(f"{rel}:{number} 共 {len(line)} 字符")
        self.assertEqual([], failures, "一行不超过 100 字符：\n" + "\n".join(failures))

    def test_within_line_budget(self):
        failures = []
        for path in SCANNED:
            budget = LINE_BUDGET.get(path.name)
            if budget is None:
                continue
            actual = len(path.read_text(encoding="utf-8").splitlines())
            if actual > budget:
                rel = path.relative_to(REPO_ROOT)
                failures.append(f"{rel} 共 {actual} 行，预算 {budget}")
        self.assertEqual(
            [], failures,
            "超预算就是没筛干净，删内容而不是抬预算：\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
