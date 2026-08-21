"""脚本归属与两个子 skill 的边界。"""

import ast
import re
import sys
import unittest
from collections import defaultdict
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _contracts  # noqa: E402


SCRIPT_REF = re.compile(r"`(\w+\.py)`")
REFERENCE_REF = re.compile(r"\.\./references/([\w.-]+\.(?:md|json))")
CARD_GAUGE = re.compile(r"量具 (\w+\.py)")
REFERENCE_SKILLS = frozenset({"case-gen", "acceptance", "shared"})


def local_imports(path, module_files):
    """返回脚本对同目录模块的 import，包含函数内的惰性 import。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module.split(".")[0]]
        for module in modules:
            if module in module_files:
                yield module_files[module], node.lineno


class ScriptOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.data = _contracts.load()
        self.ownership = self.data.get("scripts") or {}
        self.script_paths = {path.name: path for path in SCRIPTS.glob("*.py")}
        references = SKILL_ROOT / "references"
        self.reference_paths = {
            path.name: path
            for pattern in ("*.md", "*.json")
            for path in references.glob(pattern)
        }

    def test_inventory_covers_every_python_script(self):
        self.assertEqual(set(self.script_paths), set(self.ownership))
        for name, spec in self.ownership.items():
            with self.subTest(script=name):
                self.assertIn(spec.get("skill"), _contracts.SCRIPT_SKILLS)
                role = spec.get("role")
                self.assertIsInstance(role, str)
                self.assertTrue(role.strip(), f"{name} 缺 role")
                self.assertLessEqual(len(role), 40, f"{name} 的 role 超过 40 字")

    def test_ownership_matches_artifact_and_gate_stages(self):
        stage_skills = defaultdict(set)
        for spec in self.data["artifacts"].values():
            producer = spec.get("producer")
            if producer:
                stage_skills[producer].add(
                    self.data["stages"][spec["stage"]]["skill"])
        for spec in self.data["gate_inventory"].values():
            stage_skills[spec["script"]].add(
                self.data["stages"][spec["stage"]]["skill"])

        for name, spec in self.ownership.items():
            if spec["skill"] == "shared":
                continue
            with self.subTest(script=name):
                self.assertLessEqual(
                    stage_skills[name], {spec["skill"]},
                    f"{name} 出现在 {sorted(stage_skills[name])}，"
                    f"却标成 {spec['skill']}",
                )

    def test_skill_pages_only_name_scripts_on_their_side(self):
        pages = {
            "case-gen": SKILL_ROOT / "case-gen" / "SKILL.md",
            "acceptance": SKILL_ROOT / "acceptance" / "SKILL.md",
        }
        for skill, path in pages.items():
            names = SCRIPT_REF.findall(path.read_text(encoding="utf-8"))
            for name in names:
                with self.subTest(skill=skill, script=name):
                    self.assertIn(name, self.ownership, f"{path} 点名未登记脚本 {name}")
                    if name in self.ownership:
                        self.assertIn(
                            self.ownership[name]["skill"], {skill, "shared"})

    def test_skill_pages_only_name_references_on_their_side(self):
        ownership = self.data.get("references") or {}
        self.assertEqual(set(self.reference_paths), set(ownership))
        for name, spec in ownership.items():
            with self.subTest(reference=name):
                self.assertIn(spec.get("skill"), REFERENCE_SKILLS)

        pages = {
            "case-gen": SKILL_ROOT / "case-gen" / "SKILL.md",
            "acceptance": SKILL_ROOT / "acceptance" / "SKILL.md",
        }
        for skill, path in pages.items():
            text = path.read_text(encoding="utf-8")
            names = set(REFERENCE_REF.findall(text))
            names.update(name for name in ownership if name in text)
            for name in sorted(names):
                with self.subTest(skill=skill, reference=name):
                    self.assertIn(name, ownership, f"{path} 点名未登记 reference {name}")
                    if name in ownership:
                        self.assertIn(
                            ownership[name]["skill"], {skill, "shared"})

    def test_execution_references_are_routed_to_their_consuming_side(self):
        ownership = self.data.get("references") or {}
        expected = {
            "environment.md": "shared",
            "workdir-freeze.md": "case-gen",
            "execution.md": "acceptance",
        }
        for name, skill in expected.items():
            with self.subTest(reference=name):
                self.assertEqual(skill, ownership.get(name, {}).get("skill"))

        case_gen = (SKILL_ROOT / "case-gen" / "SKILL.md").read_text(
            encoding="utf-8")
        acceptance = (SKILL_ROOT / "acceptance" / "SKILL.md").read_text(
            encoding="utf-8")
        for name in ("environment.md", "workdir-freeze.md"):
            self.assertIn(name, case_gen)
        self.assertNotIn("execution.md", case_gen)
        for name in ("environment.md", "execution.md"):
            self.assertIn(name, acceptance)
        self.assertNotIn("workdir-freeze.md", acceptance)

    def test_router_only_names_shared_scripts(self):
        path = SKILL_ROOT / "SKILL.md"
        names = SCRIPT_REF.findall(path.read_text(encoding="utf-8"))
        for name in names:
            with self.subTest(script=name):
                self.assertIn(name, self.ownership, f"{path} 点名未登记脚本 {name}")
                if name in self.ownership:
                    self.assertEqual("shared", self.ownership[name]["skill"])

    def test_stage_cards_only_name_gauges_on_their_side(self):
        for skill in _contracts.SKILLS:
            for stage in _contracts.stages_of(self.data, skill):
                card = _contracts.render_card(self.data, stage)
                for name in CARD_GAUGE.findall(card):
                    with self.subTest(skill=skill, stage=stage, script=name):
                        self.assertIn(
                            name, self.ownership,
                            f"{stage} 作战卡点名未登记量具 {name}",
                        )
                        if name in self.ownership:
                            self.assertIn(
                                self.ownership[name]["skill"], {skill, "shared"})

    def test_imports_do_not_cross_ownership_boundaries(self):
        module_files = {path.stem: path.name for path in self.script_paths.values()}
        violations = []
        for source, path in sorted(self.script_paths.items()):
            if source not in self.ownership:
                continue
            source_skill = self.ownership[source]["skill"]
            allowed = {"shared"} if source_skill == "shared" else {
                source_skill, "shared"
            }
            for target, line in local_imports(path, module_files):
                if target not in self.ownership:
                    continue
                target_skill = self.ownership[target]["skill"]
                if target_skill not in allowed:
                    violations.append(
                        f"{source}:{line} ({source_skill}) -> "
                        f"{target} ({target_skill})"
                    )
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
