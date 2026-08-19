"""骨架的结构不变量。骨架是唯一可编辑对象，视图从它派生。"""

import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _checks  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

# 权威来源：docs/development/taskdoc-source/origin/checklist-14.md。
# 骨架里改动某要素的 checklist_items 需要同步改这张表——这不是负担，
# 是刻意为之：逐条核对比自动推导更能拦住系统性错位（本文件历史上出现过一次）。
CHECKLIST_CLAIMANTS = {
    1: {
        "2.5.non_contiguous", "2.5.broadcast", "2.5.dynamic_shape",
        "2.5.inplace_view", "2.5.deterministic", "2.5.empty_tensor",
        "6.references", "7.notes", "8.environment",
    },
    2: {"2.1.operator_name"},
    3: {"1.background", "1.language", "1.repo"},
    4: {"2.1.operator_name", "2.1.formula", "2.1.algorithm", "2.1.baseline"},
    5: {"2.2.project_mode"},
    6: {"2.3.signature"},
    7: {
        "2.3.signature", "2.4.param_name", "2.4.direction", "2.4.description",
        "2.4.data_type", "2.4.dtype", "2.4.format", "2.4.shape",
        "2.4.value_range", "2.4.error_behavior",
    },
    8: {"3.1.hardware", "3.1.cann_version", "3.1.third_party"},
    9: {
        "3.2.reference_api", "3.2.threshold_table", "3.2.verdict_formula",
        "3.2.random_strategy",
    },
    10: {"3.3.baseline_env", "3.3.criterion", "3.3.case_table"},
    11: {"3.4.memory"},
    12: {"3.5.tooling", "3.5.param_mapping", "3.5.generation_rules"},
    13: {"4.deliverables"},
    14: {"5.pr_target"},
}

REQUIRED_KEYS = {
    "section", "name", "form", "audience", "required", "condition",
    "decision_owner", "agent_may_propose", "checks", "vague_forbidden",
    "checklist_items", "failure",
}
FORMS = {"table_column", "section_text", "table", "code_block", "fixed"}
AUDIENCES = {"developer", "acceptance"}
REQUIREDNESS = {"always", "conditional", "optional"}
OWNERS = {"human", "agent", "template"}


def spine():
    return json.loads(SPINE.read_text(encoding="utf-8"))


class ElementsSpineTest(unittest.TestCase):
    def setUp(self):
        self.data = spine()
        self.elements = self.data["elements"]

    def test_every_element_declares_all_keys(self):
        missing = []
        for key, element in self.elements.items():
            gap = REQUIRED_KEYS - set(element)
            if gap:
                missing.append(f"{key}: 缺 {sorted(gap)}")
        self.assertEqual([], missing)

    def test_enumerated_fields_use_known_values(self):
        bad = []
        for key, element in self.elements.items():
            if element["form"] not in FORMS:
                bad.append(f"{key}.form={element['form']}")
            if element["required"] not in REQUIREDNESS:
                bad.append(f"{key}.required={element['required']}")
            if element["decision_owner"] not in OWNERS:
                bad.append(f"{key}.decision_owner={element['decision_owner']}")
            if not element["audience"] or set(element["audience"]) - AUDIENCES:
                bad.append(f"{key}.audience={element['audience']}")
        self.assertEqual([], bad)

    def test_sections_are_declared(self):
        known = set(self.data["sections"])
        unknown = sorted({e["section"] for e in self.elements.values()} - known)
        self.assertEqual([], unknown)

    def test_conditional_elements_carry_a_condition(self):
        bad = []
        for key, element in self.elements.items():
            conditional = element["required"] == "conditional"
            if conditional and not element["condition"]:
                bad.append(f"{key}: conditional 但 condition 为空")
            if not conditional and element["condition"]:
                bad.append(f"{key}: 非 conditional 却给了 condition")
        self.assertEqual([], bad)

    def test_checks_are_registered(self):
        registry = set(self.data["check_registry"])
        unknown = []
        for key, element in self.elements.items():
            for check in element["checks"]:
                name = check.split(":", 1)[0]
                if name not in registry:
                    unknown.append(f"{key}: {check}")
        self.assertEqual([], unknown)

    def test_registry_names_are_all_implemented(self):
        # F13：旧测试拿骨架比骨架自己的 registry，三者今天恰好一致纯属巧合。
        # 往骨架加一条判据却忘了实现、或名字打错，都得在这里响亮地红。
        registry = set(self.data["check_registry"])
        self.assertEqual(set(), registry - set(_checks.CHECKS),
                         "registry 里有名字没有实现")
        self.assertEqual(set(), set(_checks.CHECKS) - registry,
                         "实现了判据却没登记进 registry")

    def test_every_implemented_check_belongs_to_exactly_one_layer(self):
        # F13：判据实现了、登记了，但没挂进任何一层，run_layer 永远取不到它。
        # 反过来，一条判据挂进两层会被跑两遍，findings 靠 _dedupe 兜住看不出来。
        seen = {}
        for layer, names in _checks.LAYERS.items():
            for name in names:
                seen.setdefault(name, []).append(layer)
        self.assertEqual(set(), set(_checks.CHECKS) - set(seen),
                         "有判据不属于任何一层")
        self.assertEqual([], [f"{n}: {ls}" for n, ls in seen.items()
                              if len(ls) > 1])

    def test_unimplemented_check_name_raises_instead_of_silently_skipping(self):
        # F13：run_layer 遇到 CHECKS 里没有的名字必须炸，不能静默 continue。
        # 静默的后果是骨架上写着一条判据、门禁却从不跑它，而门禁照样退 0。
        spine = {"elements": {"9.fake": {
            "section": "1", "required": "always", "vague_forbidden": False,
            "failure": "占位", "checks": ["no_such_check_exists"]}}}
        doc = parser.parse(SKILL_ROOT / "references" / "golden-task-doc.md")
        with self.assertRaises(KeyError) as caught:
            _checks.run_layer(doc, spine, "L0", {})
        self.assertIn("no_such_check_exists", str(caught.exception))

    def test_checklist_items_are_in_range(self):
        bad = []
        for key, element in self.elements.items():
            for item in element["checklist_items"]:
                if not 1 <= item <= 14:
                    bad.append(f"{key}: checklist {item}")
        self.assertEqual([], bad)

    def test_every_element_states_a_failure_mode(self):
        # failure 回答「写错了怎么炸」。空的或写成「不正确」等于没写。
        bad = [key for key, element in self.elements.items()
               if len(element["failure"]) < 8]
        self.assertEqual([], bad)

    def test_human_owned_elements_dominate(self):
        # 红线 A：关键要素由人拍板。骨架里 human 占比低于七成说明写偏了。
        owners = [e["decision_owner"] for e in self.elements.values()]
        human = owners.count("human")
        self.assertGreaterEqual(human / len(owners), 0.7)

    def test_all_fourteen_checklist_items_are_claimed(self):
        claimed = {i for e in self.elements.values() for i in e["checklist_items"]}
        self.assertEqual(set(range(1, 15)), claimed)

    def test_checklist_claims_match_the_archived_rows(self):
        # 覆盖测试只验「都有人认领」，验不出「认领得对不对」：把 3.1.hardware
        # 从 [8] 改成 [9] 照样全绿，因为 3.1.cann_version 还占着第 8 条。
        # 这里逐条对着归档 checklist 钉死认领集合，集合比较不看顺序。
        actual = {i: set() for i in range(1, 15)}
        for key, element in self.elements.items():
            for item in element["checklist_items"]:
                actual.setdefault(item, set()).add(key)
        self.assertEqual(CHECKLIST_CLAIMANTS, actual)

    def test_question_batches_reference_real_elements(self):
        known = set(self.elements)
        unknown = []
        for batch in self.data["question_batches"]:
            for key in batch["elements"]:
                if key not in known:
                    unknown.append(f"批 {batch['id']}: {key}")
        self.assertEqual([], unknown)

    def test_every_batched_element_is_human_owned(self):
        # 批次是拿去问人的，不该混进 agent 或 template 拍板的要素。
        bad = []
        for batch in self.data["question_batches"]:
            for key in batch["elements"]:
                if self.elements[key]["decision_owner"] != "human":
                    bad.append(f"批 {batch['id']}: {key}")
        self.assertEqual([], bad)


if __name__ == "__main__":
    unittest.main()
