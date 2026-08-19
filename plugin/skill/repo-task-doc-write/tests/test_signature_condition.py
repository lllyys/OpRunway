"""F5：3.5.param_mapping 的必填条件必须来自文档，不是 decisions.json。

旧实现把 signature_differs_from_baseline 算成
`bool(decisions["3.5.param_mapping"]["human_reply"])`——用要素自己要求
记录的那条 decisions 去判断它自己要不要变必填，是自我指涉，agent 删掉
那条记录或把 human_reply 清空，条件就跟着消失，永不触发。spec §5.4
定义的条件是文档属性：§2.3 参数顺序与 §2.1 标杆接口是否一致。
"""

import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _checks  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402
import check_taskdoc  # noqa: E402

GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"
DECISIONS = SKILL_ROOT / "tests" / "fixtures" / "decisions_golden.json"
DEFECTS = SKILL_ROOT / "tests" / "fixtures" / "defects"

_SWAP_QK = (
    "    const aclTensor          *q,\n"
    "    const aclTensor          *k,\n",
    "    const aclTensor          *k,\n"
    "    const aclTensor          *q,\n",
)


def spine():
    return json.loads((SKILL_ROOT / "references"
                       / "taskdoc-elements.json").read_text(encoding="utf-8"))


def _swapped_signature_doc(name):
    text = GOLDEN.read_text(encoding="utf-8")
    swapped = text.replace(_SWAP_QK[0], _SWAP_QK[1], 1)
    assert swapped != text, "替换没有命中，golden 样例的接口定义写法变了"
    tmp = DEFECTS / name
    tmp.write_text(swapped, encoding="utf-8")
    return tmp


class SignatureConditionTest(unittest.TestCase):
    def test_golden_signature_order_matches_baseline(self):
        doc = parser.parse(GOLDEN)
        context = check_taskdoc.build_context(doc, {}, spine())
        self.assertFalse(context["signature_differs_from_baseline"])

    def test_reordered_signature_flips_the_condition(self):
        tmp = _swapped_signature_doc("_tmp_swapped_signature.md")
        try:
            doc = parser.parse(tmp)
            context = check_taskdoc.build_context(doc, {}, spine())
        finally:
            tmp.unlink()
        self.assertTrue(context["signature_differs_from_baseline"])

    def test_deleting_the_decisions_entry_no_longer_matters(self):
        # 红线 A 要禁的旧洞：删掉/清空 3.5.param_mapping 的记录曾经能让
        # 条件消失。顺序没乱时条件本来就是 False，这两种操作现在完全不
        # 影响它——因为条件根本不读 decisions.json 了。
        doc = parser.parse(GOLDEN)
        full = json.loads(DECISIONS.read_text(encoding="utf-8"))
        base = check_taskdoc.build_context(doc, full, spine())

        popped = dict(full)
        popped.pop("3.5.param_mapping", None)
        after_pop = check_taskdoc.build_context(doc, popped, spine())

        emptied = json.loads(DECISIONS.read_text(encoding="utf-8"))
        emptied["3.5.param_mapping"]["human_reply"] = ""
        after_empty = check_taskdoc.build_context(doc, emptied, spine())

        self.assertEqual(base["signature_differs_from_baseline"],
                         after_pop["signature_differs_from_baseline"])
        self.assertEqual(base["signature_differs_from_baseline"],
                         after_empty["signature_differs_from_baseline"])

    def test_reordered_signature_makes_l3_catch_the_missing_record(self):
        # 端到端：顺序真的乱了，3.5.param_mapping 变必填；decisions.json
        # 没有这条记录时，L3 的 human_reply_recorded 必须抓到它。
        tmp = _swapped_signature_doc("_tmp_swapped_signature_l3.md")
        try:
            doc = parser.parse(tmp)
            decisions = json.loads(DECISIONS.read_text(encoding="utf-8"))
            decisions.pop("3.5.param_mapping", None)
            context = check_taskdoc.build_context(doc, decisions, spine())
            found = _checks.run_layer(doc, spine(), "L3", context)
        finally:
            tmp.unlink()
        self.assertTrue(any("3.5.param_mapping" in f.message for f in found))


if __name__ == "__main__":
    unittest.main()
