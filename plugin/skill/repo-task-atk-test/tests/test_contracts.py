"""产物契约骨架：结构完整性与四条一致性不变量。

这些用例断言的是「两处是否一致」，不是「某处是否正确」。
正确性由证据分级保证（CLAUDE.md §3.2），一致性由这里保证，两者不互相替代。

与 test_document_style.py 里按事故索引的字符串断言的区别：
那些只对已发生的事有效，这些对将来新增的产物同样有效。
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _contracts  # noqa: E402

STAGES = ("S0", "S1", "S2", "S3", "S4", "S5")
RENDER_VIEWS = SKILL_ROOT / "scripts" / "render_views.py"
MARK_STEP = SKILL_ROOT / "scripts" / "mark_step.py"


class SpineStructureTest(unittest.TestCase):
    def setUp(self):
        self.data = _contracts.load()

    def test_every_stage_is_declared(self):
        self.assertEqual(tuple(self.data["stages"]), STAGES)

    def test_every_stage_declares_a_known_skill(self):
        for stage, block in self.data["stages"].items():
            with self.subTest(stage=stage):
                self.assertIn("skill", block, f"{stage} 缺 skill")
                self.assertIn(block["skill"], _contracts.SKILLS)

    def test_spine_declares_script_ownership_skills(self):
        self.assertIn("scripts", self.data)
        self.assertEqual(
            frozenset({"case-gen", "acceptance", "shared"}),
            _contracts.SCRIPT_SKILLS,
        )

    def test_stages_of_returns_each_skill_stage_in_spine_order(self):
        self.assertEqual(["S1", "S2"],
                         _contracts.stages_of(self.data, "case-gen"))
        self.assertEqual(["S0", "S3", "S4", "S5"],
                         _contracts.stages_of(self.data, "acceptance"))

    def test_s2_ends_with_the_seal_gate(self):
        self.assertEqual("封印", self.data["stages"]["S2"]["gates"][-1])

    def test_s0_carries_the_five_card_slots(self):
        block = self.data["stages"]["S0"]
        for slot in ("name", "core", "lookup_topics", "gates", "forbidden"):
            with self.subTest(slot=slot):
                self.assertIn(slot, block, f"S0 缺卡槽位 {slot}")

    def test_every_stage_carries_the_five_card_slots(self):
        for stage, block in self.data["stages"].items():
            with self.subTest(stage=stage):
                for slot in ("name", "core", "lookup_topics", "gates", "forbidden"):
                    self.assertIn(slot, block, f"{stage} 缺卡槽位 {slot}")
                self.assertTrue(block["core"].strip(), f"{stage} 的核心一句话为空")

    def test_every_artifact_declares_a_known_stage_and_owner(self):
        for name, spec in self.data["artifacts"].items():
            with self.subTest(artifact=name):
                self.assertIn(spec["stage"], STAGES)
                self.assertIn(spec["owner"], _contracts.OWNERS)

    def test_every_artifact_resolves_to_a_spec_anchor(self):
        for name, spec in self.data["artifacts"].items():
            with self.subTest(artifact=name):
                self.assertTrue(spec.get("spec"), f"{name} 没有规范锚点")

    def test_fields_are_either_detailed_or_explicitly_waived(self):
        # 日志一类的产物没有字段可登记，但必须写明理由，
        # 否则「没写字段」和「不需要字段」分不开，完备性就不可判定。
        for name, spec in self.data["artifacts"].items():
            with self.subTest(artifact=name):
                has_fields = bool(spec.get("fields"))
                waived = bool(spec.get("fields_not_applicable"))
                self.assertTrue(has_fields or waived,
                                f"{name} 既没有 fields 也没有 fields_not_applicable")
                self.assertFalse(has_fields and waived,
                                 f"{name} 同时写了 fields 和 fields_not_applicable")

    def test_every_field_answers_the_four_questions(self):
        for name, spec in self.data["artifacts"].items():
            for field, contract in (spec.get("fields") or {}).items():
                with self.subTest(artifact=name, field=field):
                    self.assertIn(contract["owner"], _contracts.OWNERS)
                    for key in ("source", "consumer", "failure"):
                        self.assertTrue(contract.get(key),
                                        f"{name}.{field} 缺 {key}")

    def test_agent_fields_are_enumerable(self):
        rows = _contracts.agent_fields(self.data)
        self.assertTrue(rows, "骨架里一个 agent 决策字段都没有，决策点清单会是空的")
        for artifact, field, contract in rows:
            with self.subTest(artifact=artifact, field=field):
                self.assertEqual(contract["owner"], "agent")

    def test_artifacts_of_returns_only_that_stage(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                for spec in _contracts.artifacts_of(self.data, stage).values():
                    self.assertEqual(spec["stage"], stage)


class SpecAnchorTest(unittest.TestCase):
    """锁 L4：agent 要自己判断的字段，必须有规范可读。

    一个 owner=agent 的字段没有可解析的规范锚点，就是一个洞——
    agent 只能靠猜或去读 ATK 源码。roll 那轮 34 次源码探索里，
    有一半是这种洞造成的。
    """

    def setUp(self):
        self.data = _contracts.load()

    def test_every_artifact_spec_anchor_resolves(self):
        for name, spec in self.data["artifacts"].items():
            with self.subTest(artifact=name):
                _contracts.resolve_anchor(spec["spec"])

    def test_declared_template_exists(self):
        for name, spec in self.data["artifacts"].items():
            template = spec.get("template")
            if not template:
                continue
            with self.subTest(artifact=name):
                self.assertTrue((SKILL_ROOT / template).exists(),
                                f"{name} 声明的模板 {template} 不存在")

    def test_declared_producer_exists(self):
        for name, spec in self.data["artifacts"].items():
            producer = spec.get("producer")
            if not producer:
                continue
            with self.subTest(artifact=name):
                self.assertTrue((SKILL_ROOT / "scripts" / producer).exists(),
                                f"{name} 声明的量具 {producer} 不存在")

    def test_unresolvable_anchor_is_rejected(self):
        with self.assertRaises(_contracts.ContractError):
            _contracts.resolve_anchor("references/case-design.md#这个小节不存在")
        with self.assertRaises(_contracts.ContractError):
            _contracts.resolve_anchor("references/不存在的文件.md#随便")

    def test_every_referenced_script_exists(self):
        # Plan A 删掉四个脚本后，骨架里 make_manifest.py / check_atk_capabilities.py
        # 的引用留在 consumed_by 里，L4 只锁 spec 锚点、test_declared_producer_exists
        # 只锁 producer，两条都看不到 consumed_by——于是 S3 卡上「出口门禁：…/manifest」
        # 指着一个不存在的脚本挂了一轮。这条把量具引用的两个位置一起锁住。
        for artifact, field, script in _contracts.script_refs(self.data):
            with self.subTest(artifact=artifact, field=field, script=script):
                self.assertTrue((SKILL_ROOT / "scripts" / script).exists(),
                                f"{artifact}.{field} 指向不存在的量具 {script}")

    def test_script_refs_covers_both_producer_and_consumers(self):
        # 只收 producer 的话这条锁等于 test_declared_producer_exists 的复读，
        # 上面那条真正要覆盖的是 consumed_by 那一侧。
        fields = {field for _, field, _ in _contracts.script_refs(self.data)}
        self.assertEqual({"producer", "consumed_by"}, fields)


class GaugeReflectionTest(unittest.TestCase):
    """锁 L1：量具认得的字段集合 == 骨架登记的字段集合。

    量具加了字段没登记，或登记了量具不认，都在这里红。
    这条不变量对将来新增的字段同样有效，不需要为每个新字段补一条断言。
    """

    def setUp(self):
        self.data = _contracts.load()

    def test_decl_keys_match_the_spine(self):
        import make_must_cover
        registered = set(self.data["artifacts"]["<op>_decl.json"]["fields"])
        self.assertEqual(set(make_must_cover.DECL_KEYS), registered)

    def test_yaml_keys_match_the_spine(self):
        import make_yaml
        registered = set(self.data["artifacts"]["<op>.yaml"]["fields"])
        exported = (set(make_yaml.YAML_HEADER_KEYS)
                    | set(make_yaml.DERIVED_KEYS)
                    | set(make_yaml.CHANNELS))
        self.assertEqual(exported, registered)


class DecisionPointsViewTest(unittest.TestCase):
    """决策点清单是骨架的视图，不是并列维护的第二张表。

    签入仓库是为了让 agent 能直接读；与骨架不一致就红，
    避免它退化成又一份要手工同步的文档。
    """

    def test_rendered_view_matches_the_checked_in_file(self):
        data = _contracts.load()
        rendered = _contracts.render_decision_points(data)
        checked_in = (SKILL_ROOT / "references" / "decision-points.md") \
            .read_text(encoding="utf-8")
        self.assertEqual(rendered, checked_in,
                         "决策点清单与骨架不同步，跑 scripts/render_views.py --write")

    def test_every_agent_field_appears_in_the_view(self):
        data = _contracts.load()
        rendered = _contracts.render_decision_points(data)
        for artifact, field, _ in _contracts.agent_fields(data):
            with self.subTest(artifact=artifact, field=field):
                self.assertIn(f"`{artifact}` 的 `{field}`", rendered)


class CardCoverageTest(unittest.TestCase):
    """锁 L2：卡里的产物与骨架该阶段的产物双向相等。

    单向包含不够：只查「骨架有的卡里都有」，卡里可以多出不存在的产物；
    只查「卡里有的骨架都有」，新增产物可以不进卡。两个方向都要。

    卡曾经同时贴在 SKILL.md 里，那份副本已删：SKILL.md 每次调用都进上下文，
    而 mark_step 进阶段时又原样打一遍，同一段话付两次钱。
    现在卡只有渲染这一条路，这里改锁「渲染出来的卡与骨架一致」
    加「阶段入口真的把卡送到眼前」。
    """

    def setUp(self):
        self.data = _contracts.load()
        paths = [SKILL_ROOT / "SKILL.md",
                 SKILL_ROOT / "case-gen" / "SKILL.md",
                 SKILL_ROOT / "acceptance" / "SKILL.md"]
        self.skills = {
            path.relative_to(SKILL_ROOT).as_posix():
                path.read_text(encoding="utf-8")
            for path in paths
        }

    def test_card_artifacts_equal_spine_artifacts(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                card = _contracts.render_card(self.data, stage)
                in_card = set(_contracts.card_artifacts_in_skill(card, stage))
                in_spine = set(_contracts.artifacts_of(self.data, stage))
                self.assertEqual(in_card, in_spine)

    def test_card_gate_names_match_the_spine(self):
        for stage, block in self.data["stages"].items():
            with self.subTest(stage=stage):
                card = _contracts.render_card(self.data, stage)
                for gate in block["gates"]:
                    self.assertIn(gate, card)

    def test_mark_step_delivers_the_card_at_stage_entry(self):
        # 卡不再有第二份副本，唯一的送达路径就是这条命令。它一旦不打卡，
        # agent 进阶段时手上什么都没有，而 SKILL.md 里已经没有可退回去读的正文。
        with tempfile.TemporaryDirectory() as work:
            timeline = Path(work) / "timeline.jsonl"
            for stage in STAGES:
                with self.subTest(stage=stage):
                    result = subprocess.run(
                        [sys.executable, str(MARK_STEP), "-o", str(timeline),
                         stage[1:], self.data["stages"][stage]["name"]],
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(_contracts.render_card(self.data, stage).strip(),
                                  result.stdout)

    def test_skill_entry_does_not_carry_a_second_copy_of_the_cards(self):
        # 防回退：卡贴回 SKILL.md 不会有任何东西红，只会每次调用多烧 2.4K token。
        for path, text in self.skills.items():
            for stage in STAGES:
                with self.subTest(path=path, stage=stage):
                    self.assertNotIn(f"### {stage} ", text,
                                     f"{stage} 的卡又被贴回 {path}，"
                                     "它由 mark_step.py 在阶段入口渲染，不要留副本")

    def test_skill_entry_tells_how_to_get_the_card(self):
        for path in ("case-gen/SKILL.md", "acceptance/SKILL.md"):
            with self.subTest(path=path):
                self.assertIn("mark_step.py", self.skills[path])

    def test_card_carries_every_gate_of_its_stage(self):
        """有哪些门必须随卡送到，不能靠 agent 想起来去查。

        清单全文从「S2 前必读」降级成按需查之后，索引若不随卡送达，
        就退回到「被门逐个拦下才知道有这道门」——那正是这份清单
        当初存在的理由。检查什么、前提是什么仍留在 gate_lookup.py 里。
        """
        for stage in STAGES:
            card = _contracts.render_card(self.data, stage)
            for gate_id, spec in self.data["gate_inventory"].items():
                if spec["stage"] != stage:
                    continue
                with self.subTest(stage=stage, gate=gate_id):
                    self.assertIn(gate_id, card)

    def test_card_lookup_topics_are_queryable(self):
        import atk_lookup
        for block in self.data["stages"].values():
            for topic in block["lookup_topics"]:
                with self.subTest(topic=topic):
                    self.assertIn(topic, atk_lookup.TOPICS)


class TemplateFactRefTest(unittest.TestCase):
    """锁 L3：模板里关于 ATK 行为的断言，必须指向 L0 里真实存在的键。

    模板与事实层各自演进是「四层各自正确、合起来失效」的第三条缝。
    引用写成 `# [L0:key]` 只锁键路径存在——L0 删掉或改名这个键才会红，
    值变了不会。引用写成 `# [L0:key=值]` 才额外锁住数值本身：
    L0 的值变了，模板照抄的旧行为会在这里被标红。
    两种写法都支持，取决于该事实是否是可比较的标量
    （嵌套结构，如 `binding.inputs`，没有单一标量可比，允许不带 `=值`）。
    """

    def setUp(self):
        import json
        self.facts = json.loads(
            (SKILL_ROOT / "references" / "atk-parameter-capabilities.json")
            .read_text(encoding="utf-8"))

    def _resolve(self, key, expected=None):
        node = self.facts
        for part in key.split("."):
            self.assertIsInstance(node, dict, f"L0 引用 {key} 中途不是对象")
            self.assertIn(part, node, f"L0 里没有 {key}")
            node = node[part]
        if expected is not None:
            self.assertEqual(str(node), expected,
                             f"L0.{key} 现在是 {node!r}，模板里挂的期望值 {expected!r} 已经过期")
        return node

    def test_every_template_l0_ref_resolves(self):
        for template in sorted((SKILL_ROOT / "assets" / "example").glob("*.py")):
            text = template.read_text(encoding="utf-8")
            for key, expected in _contracts.l0_refs(text):
                with self.subTest(template=template.name, key=key):
                    self._resolve(key, expected)

    def test_templates_carry_at_least_one_fact_ref(self):
        # 三份契约相关的模板必须挂在 L0 上；纯示范脚本不强制。
        for name in ("constraint.py", "function_example.py", "aclnn_executor.py"):
            text = (SKILL_ROOT / "assets" / "example" / name) \
                .read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertTrue(_contracts.l0_refs(text),
                                f"{name} 没有任何 L0 引用，事实变了不会有人知道")

    def test_value_mismatch_is_rejected(self):
        # 证明「值变了」这条真的会红，不是只证明它不崩：
        # 构造一份与真实 L0 冲突的期望值，_resolve 必须抛出。
        with self.assertRaises(AssertionError):
            self._resolve("group_min_length", expected="2")
        # 值对得上时不应该抛。
        self._resolve("group_min_length", expected="1")


class RenderViewsCliTest(unittest.TestCase):
    """`render_views.py --check` 的 CLI 出口码，直接跑子进程核对。

    `DecisionPointsViewTest` 只在进程内测 render_decision_points 本身；
    `--check` 真正被 CI 与 agent 调用的是这条命令行路径，出口码语义
    （0 一致 / 2 不一致）没有任何测试直接验证过，这里补上。
    """

    def test_check_exits_zero_when_view_is_in_sync(self):
        result = subprocess.run(
            [sys.executable, str(RENDER_VIEWS), "--check"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("一致", result.stdout)

    def test_cards_subcommand_prints_every_stage(self):
        # 骨架一改，SKILL.md 的卡就要重贴。没有这条命令时维护者只能自己
        # 拼 python -c 调 render_card，五张卡贴错一张 L2 才会红，来回一轮。
        result = subprocess.run(
            [sys.executable, str(RENDER_VIEWS), "--cards"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for stage in STAGES:
            self.assertIn(f"### {stage} ", result.stdout)


if __name__ == "__main__":
    unittest.main()
