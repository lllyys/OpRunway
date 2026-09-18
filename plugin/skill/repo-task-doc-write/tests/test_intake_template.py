"""intake 模板：字段小节结构、key 集合与骨架一致、判据与答题块齐备。"""

import json
import re
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_ROOT / "assets" / "intake-template.md"
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"

FACT_KEYS = {"1.background", "1.language", "1.repo", "3.1.hardware",
             "3.1.cann_version", "3.1.third_party", "3.3.baseline_env",
             "3.3.criterion", "3.4.memory", "5.pr_target"}
DECISION_KEYS = {"2.2.project_mode", "2.1.baseline"}

FIELD_RE = re.compile(r"^### (\S.*)$", re.M)
ANSWER_RE = re.compile(r"```answer\n.*?```", re.S)


def parse_sections(text):
    """### 标题 → 小节正文。"""
    sections = {}
    matches = list(FIELD_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[m.group(1).strip()] = text[m.end():end]
    return sections


class IntakeTemplateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = TEMPLATE.read_text(encoding="utf-8")
        cls.sections = parse_sections(cls.text)
        cls.elements = json.loads(
            SPINE.read_text(encoding="utf-8"))["elements"]

    def test_sections_cover_every_human_element_exactly(self):
        human = {k for k, v in self.elements.items()
                 if v["decision_owner"] == "human"}
        keys = {name for name in self.sections if name in self.elements}
        self.assertEqual(human, keys)

    def test_every_required_key_exists_in_spine(self):
        for key in FACT_KEYS | DECISION_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, self.elements)

    def test_required_sections_have_criterion(self):
        for key in FACT_KEYS | DECISION_KEYS:
            with self.subTest(key=key):
                self.assertIn("判据", self.sections[key], f"{key} 缺判据行")

    def test_every_element_section_has_example_and_answer_block(self):
        for name, body in self.sections.items():
            if name not in self.elements:
                continue
            with self.subTest(key=name):
                self.assertIn("例：", body, f"{name} 缺示例行")
                self.assertRegex(body, ANSWER_RE)

    def test_premise_section_present_with_answer_block(self):
        self.assertIn("是否随机算子", self.sections)
        self.assertRegex(self.sections["是否随机算子"], ANSWER_RE)
        self.assertIn("_premises.random_operator",
                      self.sections["是否随机算子"])

    def test_header_links_decisions_format(self):
        self.assertIn("decisions-format.md", self.text)

    def test_optional_keys_all_have_reserved_sections(self):
        optional = {k for k, v in self.elements.items()
                    if v["decision_owner"] == "human"} \
            - FACT_KEYS - DECISION_KEYS
        for key in optional:
            with self.subTest(key=key):
                self.assertIn(key, self.sections, f"可选面缺 {key} 小节")
                self.assertRegex(self.sections[key], ANSWER_RE)


if __name__ == "__main__":
    unittest.main()
