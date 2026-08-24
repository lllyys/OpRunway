"""三份主 SKILL.md 与两个顶层别名的路由、frontmatter、阶段边界与注册。"""

import json
import re
import unittest
from pathlib import Path

from _paths import SKILL_ROOT, nested_side_page, require_nested_source


SKILL_PATHS = {
    "repo-task-atk-test": SKILL_ROOT / "SKILL.md",
    "repo-task-case-gen": nested_side_page("case-gen"),
    "repo-task-atk-accept": nested_side_page("acceptance"),
}
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STAGE_ROW = re.compile(r"^\| S([0-5]) ", re.MULTILINE)
OLD_TRIGGER = (
    "当需要用 ATK 验收社区算子工程、核对任务书的精度与性能要求、"
    "生成验收证据或判断提交能否放行时使用。"
)
PLUGIN_SKILLS = {
    "./skill/repo-task-atk-test",
    "./skill/repo-task-atk-test/case-gen",
    "./skill/repo-task-atk-test/acceptance",
}
TOP_LEVEL_ALIASES = {
    "repo-task-case-gen": "case-gen",
    "repo-task-atk-accept": "acceptance",
}


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
    return parsed, parts[2]


class SkillRoutingTest(unittest.TestCase):
    def setUp(self):
        require_nested_source(self, "父页路由与下游 manifest")

    def test_all_three_skill_files_exist(self):
        for name, path in SKILL_PATHS.items():
            with self.subTest(name=name):
                self.assertTrue(path.is_file(), f"缺少 {path.relative_to(SKILL_ROOT)}")

    def test_frontmatter_is_valid_and_names_match(self):
        for expected_name, path in SKILL_PATHS.items():
            with self.subTest(path=path.relative_to(SKILL_ROOT)):
                frontmatter, _ = parse_frontmatter(path)
                name = frontmatter.get("name", "")
                description = frontmatter.get("description", "")
                self.assertEqual(expected_name, name)
                self.assertLessEqual(len(name), 64)
                self.assertRegex(name, NAME_PATTERN)
                self.assertTrue(description)
                self.assertLessEqual(len(description), 1024)
                self.assertNotRegex(description, r"^[我你]")

    def test_descriptions_route_to_the_other_side(self):
        parent, _ = parse_frontmatter(SKILL_PATHS["repo-task-atk-test"])
        case_gen, _ = parse_frontmatter(SKILL_PATHS["repo-task-case-gen"])
        acceptance, _ = parse_frontmatter(SKILL_PATHS["repo-task-atk-accept"])
        self.assertTrue(parent["description"].startswith(OLD_TRIGGER))
        self.assertIn("只有任务书时走 repo-task-case-gen", parent["description"])
        self.assertIn(
            "有交接包、工程目录和任务书时走 repo-task-atk-accept",
            parent["description"],
        )
        self.assertIn("repo-task-case-gen", parent["description"])
        self.assertIn("repo-task-atk-accept", parent["description"])
        self.assertIn("repo-task-atk-accept", case_gen["description"])
        self.assertIn("repo-task-case-gen", acceptance["description"])
        for phrase in (
            "为社区算子任务书生成 ATK 测试用例",
            "准备验收用例",
            "把用例冻结封印",
            "为多个 PR 复用同一套用例",
        ):
            self.assertIn(phrase, case_gen["description"])
        for phrase in (
            "在 NPU 上构建跑测",
            "核对精度与性能是否满足任务书",
            "判断提交能否放行",
        ):
            self.assertIn(phrase, acceptance["description"])
        self.assertNotIn("在 NPU 上构建跑测", case_gen["description"])
        self.assertNotIn("生成 ATK 测试用例", acceptance["description"])

    def test_parent_is_only_a_router(self):
        _, body = parse_frontmatter(SKILL_PATHS["repo-task-atk-test"])
        self.assertNotIn("mark_step.py", body)
        self.assertEqual([], STAGE_ROW.findall(body))
        self.assertNotRegex(body, r"^### S[0-5]\b")

    def test_case_generation_page_owns_only_s1_and_s2(self):
        _, body = parse_frontmatter(SKILL_PATHS["repo-task-case-gen"])
        self.assertEqual({"1", "2"}, set(STAGE_ROW.findall(body)))
        self.assertIn("seal_bundle.py", body)
        self.assertNotIn("check_soc_binding", body)
        self.assertIn(
            "默认只按任务书 §2.3 声明的那一份接口签名构造用例",
            body,
        )
        self.assertNotIn("默认只按工程声明的那一份接口签名构造用例", body)
        self.assertIn(
            "每个分面使用独立 YAML、必测集、用例 JSON、冻结目录。",
            body,
        )
        self.assertIn("每个接口分面仍只运行一次 `atk case`。", body)
        self.assertNotIn("`verdict.py` 按 sha256", body)
        self.assertNotIn("conclusion/verdict_<分面>.json", body)
        self.assertIn("进度行由阶段号、阶段名和当前门禁状态组成：", body)

    def test_acceptance_page_owns_only_s0_and_s3_to_s5(self):
        _, body = parse_frontmatter(SKILL_PATHS["repo-task-atk-accept"])
        self.assertEqual({"0", "3", "4", "5"}, set(STAGE_ROW.findall(body)))
        self.assertIn("check_bundle.py", body)
        self.assertIn("rewire_adapter.py", body)
        self.assertNotIn("make_must_cover", body)
        for sentence in (
            "每个分面使用独立 YAML、必测集、用例 JSON、冻结目录和 `verdict.json`。",
            "`verdict.py` 按 sha256 把 coverage 与 results 配成一对，一次只裁一个分面；",
            "分面各出一份 `conclusion/verdict_<分面>.json`，报告再把它们汇总。",
            "最终裁决必须汇总全部任务书要求的接口分面。",
        ):
            self.assertIn(sentence, body)
        self.assertIn("进度行由阶段号、阶段名和当前门禁状态组成：", body)
        self.assertIn(
            "- S3 构建、安装、SoC/op_api/ABI 绑定或第二条冒烟失败",
            body,
        )
        self.assertNotIn(
            "S3 构建、安装、SoC/op_api/ABI 绑定或第二条冒烟失败时停止。",
            body,
        )

    def test_each_page_stays_within_its_line_budget(self):
        limits = {
            "repo-task-atk-test": 80,
            "repo-task-case-gen": 300,
            "repo-task-atk-accept": 300,
        }
        for name, path in SKILL_PATHS.items():
            with self.subTest(name=name):
                count = len(path.read_text(encoding="utf-8").splitlines())
                self.assertLessEqual(count, limits[name])

    def test_downstream_manifest_registers_all_three_skills(self):
        plugin_root = SKILL_ROOT.parents[1]
        manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
        if not manifest_path.is_file():
            self.skipTest("上游工作树没有下游 plugin.json")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        registered = set(manifest.get("skills", []))
        self.assertTrue(
            PLUGIN_SKILLS <= registered,
            f"plugin.json 缺少验收 skill 注册：{sorted(PLUGIN_SKILLS - registered)}",
        )
        for relative in PLUGIN_SKILLS:
            with self.subTest(relative=relative):
                skill_file = plugin_root / relative.removeprefix("./") / "SKILL.md"
                self.assertTrue(skill_file.is_file(), f"注册路径缺 SKILL.md：{relative}")


class TopLevelSkillAliasTest(unittest.TestCase):
    def setUp(self):
        require_nested_source(self, "顶层别名只存在于嵌套源")

    def test_alias_directories_exist_and_only_contain_skill_file(self):
        for alias_name in TOP_LEVEL_ALIASES:
            alias_dir = SKILL_ROOT.parent / alias_name
            with self.subTest(alias=alias_name):
                if not alias_dir.is_dir():
                    self.fail(f"缺少顶层别名目录：{alias_dir}")
                contents = sorted(path.name for path in alias_dir.iterdir())
                self.assertEqual(["SKILL.md"], contents)

    def test_alias_frontmatter_matches_nested_skill(self):
        for alias_name, nested_dir in TOP_LEVEL_ALIASES.items():
            alias_path = SKILL_ROOT.parent / alias_name / "SKILL.md"
            nested_path = SKILL_ROOT / nested_dir / "SKILL.md"
            with self.subTest(alias=alias_name):
                self.assertTrue(alias_path.is_file(), f"缺少顶层别名：{alias_path}")
                alias_frontmatter, _ = parse_frontmatter(alias_path)
                nested_frontmatter, _ = parse_frontmatter(nested_path)
                self.assertEqual(
                    nested_frontmatter.get("name"), alias_frontmatter.get("name")
                )
                self.assertEqual(
                    nested_frontmatter.get("description"),
                    alias_frontmatter.get("description"),
                )

    def test_alias_body_points_to_existing_nested_skill(self):
        for alias_name, nested_dir in TOP_LEVEL_ALIASES.items():
            alias_dir = SKILL_ROOT.parent / alias_name
            alias_path = alias_dir / "SKILL.md"
            pointer = f"../repo-task-atk-test/{nested_dir}/SKILL.md"
            with self.subTest(alias=alias_name):
                self.assertTrue(alias_path.is_file(), f"缺少顶层别名：{alias_path}")
                _, body = parse_frontmatter(alias_path)
                self.assertIn(pointer, body)
                self.assertTrue((alias_dir / pointer).is_file())

    def test_alias_body_stays_a_short_pointer(self):
        for alias_name in TOP_LEVEL_ALIASES:
            alias_path = SKILL_ROOT.parent / alias_name / "SKILL.md"
            with self.subTest(alias=alias_name):
                self.assertTrue(alias_path.is_file(), f"缺少顶层别名：{alias_path}")
                _, body = parse_frontmatter(alias_path)
                self.assertLessEqual(len(body.strip().splitlines()), 8)
                self.assertNotIn("mark_step.py", body)
                self.assertEqual([], STAGE_ROW.findall(body))
                self.assertNotIn("| 阶段 |", body)
                for line in alias_path.read_text(encoding="utf-8").splitlines():
                    self.assertLessEqual(len(line), 100)


if __name__ == "__main__":
    unittest.main()
