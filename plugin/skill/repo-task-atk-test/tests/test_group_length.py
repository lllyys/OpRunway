"""可变长复合组：三条事实必须查得到、模板必须挂在它们上面。

S2 的空组那次，L0 里有 group_min_length，plugin-authoring.md 里有
「不要把缺省参数表示为空的复合输入组」——但后者是写插件时才读的文件，
而空组是在 decl 设计期定死的。代价是推翻 decl、拆双分面、重写三个文件。

这一组用例锁的是「事实在不在、模板认不认」，不是「ATK 行为对不对」。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import atk_lookup  # noqa: E402
import _contracts  # noqa: E402

L0 = json.loads((SKILL_ROOT / "references" / "atk-parameter-capabilities.json")
                .read_text(encoding="utf-8"))
TEMPLATE = SKILL_ROOT / "assets" / "example" / "constraint.py"
LOOKUP = SKILL_ROOT / "scripts" / "atk_lookup.py"


class GroupLengthFactsTest(unittest.TestCase):
    def test_l0_records_where_the_length_comes_from(self):
        block = L0["group_length"]
        self.assertEqual("yaml_tuple_numbers", block["source"])

    def test_l0_records_that_length_ignores_combo_order(self):
        # 这条是最贵的一条：以为长度按 combo 顺序对应，逐个改元素就会错位。
        self.assertFalse(L0["group_length"]["follows_combo_order"])

    def test_l0_records_the_runtime_type(self):
        self.assertEqual("list[InputCaseConfig]",
                         L0["group_length"]["runtime_type"])

    def test_l0_points_at_the_canonical_min_length_key(self):
        # 下限不在这里再写一遍，指向已有的键，避免两处维护同一个数。
        key = L0["group_length"]["min_length_key"]
        self.assertIn(key, L0)
        self.assertEqual(1, L0[key])

    def test_every_fact_carries_source_evidence(self):
        evidence = L0["group_length"]["evidence"]
        self.assertTrue(evidence)
        for item in evidence:
            with self.subTest(item=item):
                self.assertIn(":", item, "证据要写到行号")


class GroupLengthLookupTest(unittest.TestCase):
    def test_topic_is_queryable(self):
        self.assertIn("group_length", atk_lookup.TOPICS)

    def test_lookup_prints_the_facts(self):
        result = subprocess.run(
            [sys.executable, str(LOOKUP), "group_length"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("follows_combo_order", result.stdout)


class GroupRebuildTemplateTest(unittest.TestCase):
    def test_template_shows_how_to_rebuild_a_whole_group(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("def rebuild_group(", text)
        self.assertIn("model_copy", text)

    def test_template_hangs_the_rebuild_on_the_facts(self):
        # 事实变了模板要跟着红，这正是锁 L3 的作用。
        keys = {key for key, _ in _contracts.l0_refs(
            TEMPLATE.read_text(encoding="utf-8"))}
        self.assertIn("group_length.follows_combo_order", keys)
        self.assertIn("group_length.runtime_type", keys)

    def test_template_still_parses(self):
        import ast
        ast.parse(TEMPLATE.read_text(encoding="utf-8"))


class GroupLengthReferenceTest(unittest.TestCase):
    def test_design_time_rule_is_written_down(self):
        text = (SKILL_ROOT / "references" / "plugin-authoring.md") \
            .read_text(encoding="utf-8")
        self.assertIn("整组重建", text)
        self.assertIn("tuple_numbers", text)


if __name__ == "__main__":
    unittest.main()
