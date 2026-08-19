"""行文规则。规则正文在 .claude/rules/prose-style.md。

新 skill 的基线是 0：写进来就必须合规，没有存量豁免。
"""

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = Path(__file__).resolve().parents[1]

# 判据实现复用验收 skill 的测试模块。这是开发期依赖，不随 skill 发布——
# tests/ 本来就不参与部署，两个 skill 在同一个仓里，抄一份反而会漂。
#
# 两个 skill 的测试文件同名（都叫 test_document_style.py）。pytest 按
# 文件名当模块名放进 sys.modules，普通 `import test_document_style` 会
# 在同一个 pytest 会话里撞名——要么拿到自己这个正在初始化的模块（自我
# 循环导入），要么被 pytest 判成「import file mismatch」直接收集失败。
# 用 importlib 按路径加载并起一个不会撞名的别名，绕开这个坑。
_ATK_STYLE_MODULE = (REPO_ROOT / "skill" / "repo-task-atk-test"
                      / "tests" / "test_document_style.py")
_spec = importlib.util.spec_from_file_location(
    "_atk_test_document_style", _ATK_STYLE_MODULE)
_atk_style = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_atk_style)
prose_lines = _atk_style.prose_lines
prose_violations = _atk_style.prose_violations

# golden-task-doc.md 与 task-doc-template.md 是任务书与模板，行文按社区
# 模板走，不是 skill 文档。manual-checklist.md 是渲染产物，改动权在骨架。
#
# experimental_standard.md 是 test_shared_facts_sync.py 守着的共享事实，
# 权威副本在验收 skill 的 references/ 下，本仓这份必须逐字节相同。它在
# 权威那边已有已知欠账（验收 skill 自己的 test_document_style.py 里
# PROSE_BASELINE 记了 5 处结构违规，行宽违规还未纳入棘轮，是仓根
# CLAUDE.md §8 记录在案的既存失败）。本 skill 的零基线管的是自己写的
# 文档，不能要求一份外部同步进来的文件达到本地标准——真要改，
# 改权威副本，两边会一起变。
EXEMPT = {"golden-task-doc.md", "task-doc-template.md", "manual-checklist.md",
          "experimental_standard.md"}
# 开发侧素材归档里我们自己写的三份（README、defect-map、repair-log）也纳入。
# 它们不随 skill 发布，但和 skill 文档同一批人同一时期写，读者也是同一批，
# 没有理由适用另一套行文标准。接入时实测三份都已经零违规，基线依旧是 0。
#
# 只取顶层 *.md，不递归：origin/ 下是上游只读拷贝，纪律是一个字节都不改
# （见 skill 侧 CLAUDE.md「素材只读」），拿本地行文标准去要求它没有意义，
# 真判红了也不许修。
SOURCE_ARCHIVE = (REPO_ROOT / "docs" / "development" / "taskdoc-source")
SCANNED = [SKILL_ROOT / "SKILL.md", SKILL_ROOT / "CLAUDE.md",
           *sorted(p for p in (SKILL_ROOT / "references").glob("*.md")
                   if p.name not in EXEMPT),
           *sorted(SOURCE_ARCHIVE.glob("*.md"))]


class DocumentStyleTest(unittest.TestCase):
    def test_no_prose_structure_violations(self):
        failures = []
        for path in SCANNED:
            for rule, line, note in prose_violations(path):
                failures.append(f"{path.name}:{line} 规则{rule} {note}")
        self.assertEqual([], failures)

    def test_lines_stay_within_width(self):
        failures = []
        for path in SCANNED:
            for number, line in prose_lines(path):
                if len(line) > 100:
                    failures.append(f"{path.name}:{number}: {len(line)} 字符")
        self.assertEqual([], failures)

    def test_source_archive_prose_is_gated(self):
        # Task 3 起挂着的决定：repair-log.md 要不要接行文门禁。接了。
        # 断言它们真的在扫描集里——否则「已接入」只是注释里的一句话。
        names = {p.name for p in SCANNED}
        for name in ("README.md", "defect-map.md", "repair-log.md"):
            with self.subTest(name=name):
                self.assertIn(name, names)

    def test_upstream_origin_copies_are_never_gated(self):
        # 只读纪律优先于行文规则：origin/ 判红了也不许修，那就不该判。
        self.assertEqual([], [p for p in SCANNED if "origin" in p.parts])

    def test_golden_task_doc_is_exempt(self):
        # 黄金样例是任务书不是 skill 文档，行文按社区模板走，不受这条约束。
        self.assertNotIn(SKILL_ROOT / "references" / "golden-task-doc.md",
                         SCANNED)


if __name__ == "__main__":
    unittest.main()
