"""下游 manifest 与两个平级 skill 入口的注册门禁。"""

import json
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
EXPECTED_SKILLS = [
    "./skill/repo-task-case-gen",
    "./skill/repo-task-atk-accept",
    "./skill/repo-task-doc-write",
]


def parse_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError(f"{path} 缺 YAML frontmatter 分隔符")
    parsed = {}
    lines = parts[1].splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise ValueError(f"{path} frontmatter 行不可解析: {line!r}")
        value = value.strip()
        if value in {">", ">-", "|", "|-"}:
            folded = []
            index += 1
            while index < len(lines) and lines[index].startswith((" ", "\t")):
                folded.append(lines[index].strip())
                index += 1
            parsed[key.strip()] = " ".join(folded)
            continue
        parsed[key.strip()] = value
        index += 1
    return parsed


class DownstreamManifestTest(unittest.TestCase):
    def test_manifest_registers_exactly_the_three_top_level_skills(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(EXPECTED_SKILLS, manifest.get("skills"))

    def test_frontmatter_names_match_directories_and_route_each_other(self):
        descriptions = {}
        for directory in ("repo-task-case-gen", "repo-task-atk-accept"):
            path = PLUGIN_ROOT / "skill" / directory / "SKILL.md"
            with self.subTest(directory=directory):
                frontmatter = parse_frontmatter(path)
                self.assertEqual(directory, frontmatter.get("name"))
                self.assertTrue(frontmatter.get("description"))
                descriptions[directory] = frontmatter["description"]
        self.assertIn(
            "repo-task-atk-accept", descriptions["repo-task-case-gen"])
        self.assertIn(
            "repo-task-case-gen", descriptions["repo-task-atk-accept"])


if __name__ == "__main__":
    unittest.main()
