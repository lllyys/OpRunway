"""检查点清单：前提必须声明，前提不成立时的处置必须说得出。

真机上 S2 死锁四次（SKILL_FRICTION_bernoulli.md 的 F2/F3/F4/F5），四次的共性
不是判据错了，是**判据的适用条件没写出来**：判据诞生于 roll / median 两个算子
（torch 基线 + arithmetic/movement 类），把「当时那个算子的特性」写成了
「所有算子的判据」。换一类算子来，判据不是「判不了」而是「判失败」，
而报错给的出路全部违反 skill 自己的规范——agent 无论读多少文档都出不去。

所以每道检查点要显式回答两件事：

1. 判据在什么条件下成立（`premise.holds_when`）
2. 条件不成立时，它要拦的缺陷由谁接（`premise.when_broken`）

第 2 问是这份不变量的要害。「前提不成立就跳过」会直接踩回本 skill 最防的那件事
——报告 100% 通过而什么都没验。所以处置只有三种合法形态，判别标准是同一句话：

    前提不成立时，这道门要拦的缺陷还可能不可能发生？

- `retarget`  还可能。判据没错，量具用错了对象 → 换量具，一条检查都不少（F2）
- `delegate`  还可能。这一侧判不了 → 明确交给另一道门，必须点名是谁（F3）
- `not_applicable` 结构上不可能发生 → 才允许跳过，且必须落进证据（F5）

`delegate` 必须给 `delegated_to` 且指向真实存在的量具；
`not_applicable` 必须给 `evidence_key`，跳过的事实要留痕。
"""

import sys
import unittest
from collections import Counter
from pathlib import Path

from _paths import (SKILL_ROOT, entry_pages, is_nested_source, layout_side,
                    nested_side_page, side_page)

sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _contracts  # noqa: E402

STAGES = ("S0", "S1", "S2", "S3", "S4", "S5")


class GateInventoryStructureTest(unittest.TestCase):
    """检查点清单本身的结构。"""

    def setUp(self):
        self.data = _contracts.load()
        self.gates = self.data.get("gate_inventory")

    def test_inventory_exists(self):
        self.assertTrue(self.gates, "骨架里没有 gate_inventory，检查点清单无处可查")

    def test_every_gate_declares_the_required_slots(self):
        for gate_id, spec in self.gates.items():
            with self.subTest(gate=gate_id):
                for slot in ("stage", "script", "checks", "purpose", "premise"):
                    self.assertIn(slot, spec, f"{gate_id} 缺 {slot}")

    def test_every_gate_lands_on_a_known_stage(self):
        for gate_id, spec in self.gates.items():
            with self.subTest(gate=gate_id):
                self.assertIn(spec["stage"], STAGES)

    def test_every_gate_script_exists(self):
        for gate_id, spec in self.gates.items():
            side = layout_side()
            owner = self.data["scripts"][spec["script"]]["skill"]
            if side is not None and owner not in {side, "shared"}:
                continue
            with self.subTest(gate=gate_id):
                self.assertTrue((SKILL_ROOT / "scripts" / spec["script"]).exists(),
                                f"{gate_id} 指向不存在的量具 {spec['script']}")

    def test_purpose_is_one_of_the_four_kinds(self):
        # 检查点为什么存在，只有这四种答案。分不出类的说明这道门自己没想清楚
        # 要拦什么——那种门最容易在换一类算子时变成死锁。
        for gate_id, spec in self.gates.items():
            with self.subTest(gate=gate_id):
                self.assertIn(spec["purpose"], _contracts.GATE_PURPOSES)

    def test_checks_are_non_empty_strings(self):
        for gate_id, spec in self.gates.items():
            with self.subTest(gate=gate_id):
                self.assertTrue(spec["checks"], f"{gate_id} 一条检查点都没登记")
                for index, check in enumerate(spec["checks"]):
                    self.assertTrue(str(check).strip(),
                                    f"{gate_id}.checks[{index}] 是空的")


class PremiseContractTest(unittest.TestCase):
    """前提与处置：这份文件的要害。"""

    def setUp(self):
        self.data = _contracts.load()
        self.gates = self.data.get("gate_inventory") or {}

    def test_premise_declares_both_questions(self):
        for gate_id, spec in self.gates.items():
            with self.subTest(gate=gate_id):
                premise = spec["premise"]
                self.assertIn("holds_when", premise, f"{gate_id} 没说前提是什么")
                self.assertIn("when_broken", premise, f"{gate_id} 没说前提不成立怎么办")

    def test_unconditional_gates_say_so_explicitly(self):
        # 「无前提」要写成 null 而不是省略：省略了，「没想过」和「真的无前提」
        # 分不开，完备性不可判定（同 fields_not_applicable 的道理）。
        for gate_id, spec in self.gates.items():
            premise = spec["premise"]
            if premise["holds_when"] is None:
                with self.subTest(gate=gate_id):
                    self.assertIsNone(
                        premise["when_broken"],
                        f"{gate_id} 声明无前提，却给了 when_broken")

    def test_conditional_gates_bind_to_a_real_key(self):
        # 前提挂在哪个键上，决定了量具该读什么。四条真机死锁挂在三个不同的键上，
        # 把它们笼统归成「算子类别」会导出错误的修法。
        for gate_id, spec in self.gates.items():
            premise = spec["premise"]
            if premise["holds_when"] is None:
                continue
            with self.subTest(gate=gate_id):
                self.assertIn(premise.get("bound_to"), _contracts.PREMISE_KEYS,
                              f"{gate_id} 的前提挂在 {premise.get('bound_to')!r}，"
                              f"不在已知的键里：{sorted(_contracts.PREMISE_KEYS)}")

    def test_when_broken_is_one_of_the_three_dispositions(self):
        for gate_id, spec in self.gates.items():
            premise = spec["premise"]
            if premise["when_broken"] is None:
                continue
            with self.subTest(gate=gate_id):
                self.assertIn(premise["when_broken"], _contracts.DISPOSITIONS,
                              f"{gate_id} 的处置 {premise['when_broken']!r} 不合法")

    def test_delegate_names_a_real_gauge(self):
        """「交给别人接」必须点名是谁，而且那个量具真的存在。

        这是 delegate 与「悄悄跳过」的唯一区别。答不上来就不许跳过。
        """
        for gate_id, spec in self.gates.items():
            premise = spec["premise"]
            if premise["when_broken"] != "delegate":
                continue
            with self.subTest(gate=gate_id):
                target = premise.get("delegated_to")
                self.assertTrue(target, f"{gate_id} 声明 delegate 却没说交给谁")
                self.assertTrue((SKILL_ROOT / "scripts" / target).exists(),
                                f"{gate_id} 交给了不存在的量具 {target}")

    def test_not_applicable_leaves_a_trace(self):
        """跳过必须留痕，否则「判过了」和「没判」在证据里分不开。"""
        for gate_id, spec in self.gates.items():
            premise = spec["premise"]
            if premise["when_broken"] != "not_applicable":
                continue
            with self.subTest(gate=gate_id):
                self.assertTrue(premise.get("evidence_key"),
                                f"{gate_id} 跳过时没有落进证据的键")

    def test_retarget_says_what_it_switches_to(self):
        """换量具要说清换成拿什么量，否则下一个人看不出这里修过什么。"""
        for gate_id, spec in self.gates.items():
            premise = spec["premise"]
            if premise["when_broken"] != "retarget":
                continue
            with self.subTest(gate=gate_id):
                self.assertTrue(premise.get("retargets_to"),
                                f"{gate_id} 声明 retarget 却没说换成量什么")


class KnownDeadlockTest(unittest.TestCase):
    """四次真机死锁必须在清单里各有归宿。

    这条是回归锁：它们的修法已经落进量具了，但「为什么这么修」只活在
    一份摩擦记录里。记录会被归档，不变量不会。
    """

    def setUp(self):
        self.data = _contracts.load()
        self.gates = self.data.get("gate_inventory") or {}

    def _premise_of(self, gate_id):
        self.assertIn(gate_id, self.gates, f"清单里没有 {gate_id}")
        return self.gates[gate_id]["premise"]

    def test_f2_value_range_retargets_by_parameter_kind(self):
        # F2：种子是 int64_t 的 attr，判据却拿张量 dtype 轴去量，
        # 钉死常量的唯一写法被判越界。种子该不该被 range 检查？该。
        # 错的只是量错了对象——所以是 retarget，不是豁免。
        premise = self._premise_of("expressibility.value_ranges")
        self.assertEqual("parameter_kind", premise["bound_to"])
        self.assertEqual("retarget", premise["when_broken"])

    def test_f3_baseline_binding_delegates_to_signature_contract(self):
        # F3：cann_builtin 没有 torch 基线可反射形参名。但「契约与签名对不对得上」
        # 这个缺陷依然可能发生，所以不是豁免，是移交——而且接手的三判强于原来的
        # 子集判定。
        premise = self._premise_of("make_yaml.baseline_binding")
        self.assertEqual("baseline_kind", premise["bound_to"])
        self.assertEqual("delegate", premise["when_broken"])
        self.assertEqual("check_signature_contract.py", premise["delegated_to"])

    def test_f5_constant_input_is_not_applicable_for_generation(self):
        # F5：generation 类的输出由随机数流决定，输入只提供 shape。
        # 「常量输入测不出错」这句话对它没有意义——这才是真正的 not_applicable。
        premise = self._premise_of("freeze_inputs.constant_tensor")
        self.assertEqual("operator_class", premise["bound_to"])
        self.assertEqual("not_applicable", premise["when_broken"])
        self.assertTrue(premise["evidence_key"])

    def test_f4_is_not_filed_as_a_premise_problem(self):
        """F4 不是前提问题，登记成前提问题会把修法带偏。

        插件没加载时诊断指向「YAML 输入名必须等于基线形参名」，真因是
        `atk node` 入口不自动加载 `function_*.py`。要修的是 reference 的
        事实错误与诊断指向，不是给哪道门加前提。
        """
        premise = self._premise_of("freeze_inputs.baseline_plugin")
        self.assertIsNone(premise["holds_when"],
                          "F4 是诊断指向问题，不该被登记成有条件前提")


class GateInventoryCoverageTest(unittest.TestCase):
    """清单必须覆盖真正会拦下 agent 的那些量具。"""

    def setUp(self):
        self.data = _contracts.load()
        self.gates = self.data.get("gate_inventory") or {}

    def test_every_s2_blocking_script_is_inventoried(self):
        # S2 里任何一个会以退出码 2 拦下 agent 的量具都要在清单里，
        # 否则 agent 还是只能被逐个拦下才知道有这道门。
        inventoried = {spec["script"] for spec in self.gates.values()}
        for script in _contracts.S2_BLOCKING_SCRIPTS:
            with self.subTest(script=script):
                self.assertIn(script, inventoried,
                              f"{script} 会拦下 agent，但检查点清单里没有它")

    def test_every_s0_blocking_script_is_inventoried(self):
        inventoried = {spec["script"] for spec in self.gates.values()}
        for script in _contracts.S0_BLOCKING_SCRIPTS:
            with self.subTest(script=script):
                self.assertIn(script, inventoried,
                              f"{script} 会拦下 agent，但检查点清单里没有它")

    def test_atk_pitfall_gates_point_at_a_reference(self):
        """编码 ATK 反直觉行为的检查点，必须指向可读的规范。

        这类拦的不是 agent 的疏忽，是 ATK 的坑：agent 读了就能一次写对，
        读不到就必踩。没有规范锚点的，等于要求 agent 去读 ATK 源码。
        """
        for gate_id, spec in self.gates.items():
            if spec["purpose"] != "atk_pitfall":
                continue
            with self.subTest(gate=gate_id):
                anchor = spec.get("spec")
                self.assertTrue(anchor, f"{gate_id} 是 ATK 的坑，却没有规范锚点")
                _contracts.resolve_anchor(anchor)


class GateInventoryViewTest(unittest.TestCase):
    """检查点清单是骨架的视图，和决策点清单一样从骨架渲染。

    真机耗时的一个来源是：19 道门、48 条检查散在 8 个脚本、5 份 reference 里，
    没有任何一处能一览，agent 只能被逐个拦下才知道有这道门。
    视图签入仓库是为了让 agent 直接读到，渲染是为了它不会变成第二份手工同步的表。
    """

    def setUp(self):
        self.data = _contracts.load()
        self.view_path = SKILL_ROOT / "references" / "gate-inventory.md"

    def test_rendered_view_matches_the_checked_in_file(self):
        rendered = _contracts.render_gate_inventory(self.data)
        self.assertTrue(self.view_path.exists(), "gate-inventory.md 还没生成")
        self.assertEqual(rendered, self.view_path.read_text(encoding="utf-8"),
                         "检查点清单与骨架不同步，跑 scripts/render_views.py --write")

    def test_every_gate_appears_in_the_view(self):
        rendered = _contracts.render_gate_inventory(self.data)
        for gate_id in self.data["gate_inventory"]:
            with self.subTest(gate=gate_id):
                self.assertIn(gate_id, rendered)

    def test_every_check_line_appears_in_the_view(self):
        # 只渲染门的名字不够：agent 要的是「这 48 条具体检查什么」。
        rendered = _contracts.render_gate_inventory(self.data)
        for gate_id, spec in self.data["gate_inventory"].items():
            for check in spec["checks"]:
                with self.subTest(gate=gate_id, check=check):
                    self.assertIn(check, rendered)

    def test_conditional_gates_are_called_out_separately(self):
        # 换一类算子时要重看的就是这 9 道有前提的门。混在 19 道里读不出来。
        rendered = _contracts.render_gate_inventory(self.data)
        for gate_id, spec in _contracts.conditional_gates(self.data):
            with self.subTest(gate=gate_id):
                self.assertIn(spec["premise"]["holds_when"], rendered)

    def test_skill_entry_states_the_right_count_and_stage(self):
        """两个子 SKILL.md 把「多少道门、在哪个阶段」写成具体数字。

        它替代的是「通读全文」，所以这句话本身必须准：加了一道 S3 的门
        而这句没改，agent 就会以为 S3 不必查门，而它已经不再读清单全文了。
        """
        counts = Counter(
            spec["stage"] for spec in self.data["gate_inventory"].values()
        )
        pages = {"case-gen": side_page("case-gen")}
        if is_nested_source():
            pages["acceptance"] = nested_side_page("acceptance")
        for skill_name, path in pages.items():
            text = path.read_text(encoding="utf-8")
            for stage in _contracts.stages_of(self.data, skill_name):
                if stage not in counts:
                    continue
                with self.subTest(skill=skill_name, stage=stage):
                    self.assertIn(f"{stage} 有 {counts[stage]} 道", text)

    def test_view_is_reachable_from_the_skill_entry(self):
        # 视图不被入口指到就等于不存在。
        paths = entry_pages()
        skill = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertIn("gate-inventory.md", skill)


class AtkPitfallKnowledgeTest(unittest.TestCase):
    """ATK 的坑必须前置成可读知识，而不是留在量具里等着拦人。

    这类检查点拦的不是 agent 的判断力，是框架的反直觉行为：
    agent 读了就能一次写对，读不到就必踩。它们最该前置。
    """

    def setUp(self):
        self.data = _contracts.load()
        self.pitfalls = {
            gate_id: spec
            for gate_id, spec in self.data["gate_inventory"].items()
            if spec["purpose"] == "atk_pitfall"
        }

    def test_pitfalls_are_collected_in_one_place(self):
        path = SKILL_ROOT / "references" / "atk-pitfalls.md"
        self.assertTrue(path.exists(), "ATK 的坑没有集中成一份可读知识")
        text = path.read_text(encoding="utf-8")
        for gate_id in self.pitfalls:
            with self.subTest(gate=gate_id):
                self.assertIn(gate_id, text,
                              f"{gate_id} 是 ATK 的坑，但没写进 atk-pitfalls.md")

    def test_every_pitfall_says_what_happens_if_you_dont_know(self):
        # 「不知道会怎样」是这份知识的价值所在：没有后果描述的条目
        # 读起来像规则，agent 会当成可选项。
        text = (SKILL_ROOT / "references" / "atk-pitfalls.md") \
            .read_text(encoding="utf-8")
        self.assertIn("不知道会怎样", text)

    def test_every_pitfall_section_names_its_gate(self):
        """每条知识都能追回到拦它的那道门。

        锁的是可追溯性，不是逐字复制：知识文档要讲清机制和后果，
        逐字照抄骨架的 checks 只会把它变成骨架的复读，反而没人读。
        """
        text = (SKILL_ROOT / "references" / "atk-pitfalls.md") \
            .read_text(encoding="utf-8")
        for gate_id in self.pitfalls:
            with self.subTest(gate=gate_id):
                self.assertIn(f"门 `{gate_id}`", text,
                              f"{gate_id} 没有以「门 `{gate_id}`」的形态标注出处")


if __name__ == "__main__":
    unittest.main()
