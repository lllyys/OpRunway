"""L3 拍板留痕与 L4 双受众。红线 A：agent 声称已确认不算数。"""

import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _acceptance_map  # noqa: E402
import _checks  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"
DECISIONS = SKILL_ROOT / "tests" / "fixtures" / "decisions_golden.json"


def spine():
    return json.loads((SKILL_ROOT / "references"
                       / "taskdoc-elements.json").read_text(encoding="utf-8"))


def run(layer, decisions):
    context = {"decisions": decisions, "random_signal_hit": False,
               "expected_header": {}}
    return _checks.run_layer(parser.parse(GOLDEN), spine(), layer, context)


class DecisionLayerTest(unittest.TestCase):
    def setUp(self):
        self.decisions = json.loads(DECISIONS.read_text(encoding="utf-8"))

    def test_complete_decisions_pass(self):
        self.assertEqual([], run("L3", self.decisions))

    def test_missing_decision_is_caught(self):
        self.decisions.pop("2.5.non_contiguous")
        found = run("L3", self.decisions)
        self.assertTrue(any("2.5.non_contiguous" in f.message for f in found))

    def test_empty_human_reply_is_caught(self):
        self.decisions["2.4.dtype"]["human_reply"] = "   "
        found = run("L3", self.decisions)
        self.assertTrue(any("2.4.dtype" in f.message for f in found))

    def test_terse_reply_still_counts_as_confirmation(self):
        # 门禁不做语义判断：「对」也算确认，空字符串才判红。
        self.decisions["2.4.dtype"]["human_reply"] = "对"
        self.assertEqual([], run("L3", self.decisions))

    def test_agent_owned_elements_need_no_reply(self):
        self.decisions.pop("6.references", None)
        self.assertEqual([], run("L3", self.decisions))


class AcceptanceMapTest(unittest.TestCase):
    def test_map_covers_the_intake_constraint_table(self):
        built = _acceptance_map.build(parser.parse(GOLDEN), spine(), {})
        for field in ("算子", "输入", "非连续 Tensor", "输出", "错误语义",
                      "精度", "性能", "环境", "提交"):
            with self.subTest(field=field):
                self.assertIn(field, built)

    def test_golden_leaves_no_unconfirmed_field(self):
        built = _acceptance_map.build(parser.parse(GOLDEN), spine(), {})
        pending = [k for k, v in built.items() if v == "待确认"]
        self.assertEqual([], pending)

    def test_scaffold_comment_does_not_leak_into_the_baton(self):
        # F1：§2.5 换回脚手架注释时，接力棒的「非连续 Tensor」字段必须是
        # 「待确认」，不能把注释里的 failure 提示文本当成真实取值捞出来。
        doc = parser.parse(SKILL_ROOT / "tests" / "fixtures" / "defects"
                           / "d13_scaffold_comment.md")
        built = _acceptance_map.build(doc, spine(), {})
        self.assertEqual("待确认", built["非连续 Tensor"])

    def test_bare_placeholder_in_the_field_is_not_taken_literally(self):
        # 同一类污染不需要靠 HTML 注释触发：正文里直接写占位词一样要挡。
        text = ("# op 任务书\n\n## 2.5 算子实现约束\n\n"
                "非连续 Tensor 支持：待填写\n")
        tmp = SKILL_ROOT / "tests" / "fixtures" / "_tmp_am_placeholder.md"
        tmp.write_text(text, encoding="utf-8")
        try:
            built = _acceptance_map.build(parser.parse(tmp), spine(), {})
        finally:
            tmp.unlink()
        self.assertEqual("待确认", built["非连续 Tensor"])

    def test_layer_four_flags_pending_fields(self):
        found = run("L4", json.loads(DECISIONS.read_text(encoding="utf-8")))
        self.assertEqual([], found)


if __name__ == "__main__":
    unittest.main()
