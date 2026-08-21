"""内置实现当真值这条路的知识与门禁。

这些事实都是从 ATK 源码读出来的，不是猜的；钉在这里是为了不被后续精简删掉。
真机上少任何一条，agent 都会现场翻 ATK 源码重推一遍。
"""

import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import validate_cases  # noqa: E402

DOC = SKILL_ROOT / "references" / "builtin-baseline.md"
DESIGN_DOC = SKILL_ROOT / "references" / "builtin-baseline-design.md"
CASE_DESIGN = SKILL_ROOT / "references" / "case-design.md"
GLOSSARY = SKILL_ROOT / "references" / "glossary.md"
ATK_CLI = SKILL_ROOT / "references" / "atk-cli.md"
SKILL_FILES = [
    SKILL_ROOT / "SKILL.md",
    SKILL_ROOT / "case-gen" / "SKILL.md",
    SKILL_ROOT / "acceptance" / "SKILL.md",
]


class BuiltinBaselineKnowledgeTest(unittest.TestCase):
    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")

    def test_doc_exists_and_is_reachable_from_the_main_skill(self):
        case_gen = SKILL_FILES[1].read_text(encoding="utf-8")
        acceptance = SKILL_FILES[2].read_text(encoding="utf-8")
        self.assertIn("builtin-baseline-design.md", case_gen)
        self.assertNotIn("builtin-baseline.md", case_gen)
        self.assertIn("builtin-baseline.md", acceptance)
        self.assertNotIn("builtin-baseline-design.md", acceptance)

    def test_generation_rules_live_in_the_design_reference(self):
        design = DESIGN_DOC.read_text(encoding="utf-8")
        for heading in ("没有 torch 基线，S2 怎么写", "种子必须钉死", "比较器"):
            with self.subTest(heading=heading):
                self.assertIn(f"## {heading}", design)
                self.assertNotIn(f"## {heading}", self.text)

    def test_design_reference_states_when_to_read_and_where_to_run(self):
        design = DESIGN_DOC.read_text(encoding="utf-8")
        self.assertIn("interface.json.baseline_kind", design)
        self.assertIn("cann_builtin", design)
        self.assertIn("builtin-baseline.md", design)

    def test_seed_signature_source_is_the_task_document(self):
        design = DESIGN_DOC.read_text(encoding="utf-8")
        self.assertIn("只能读任务书 §2.3 的接口声明回答", design)
        self.assertIn("声明里没有种子入参就当作否", design)
        self.assertNotIn("只能读待验收算子工程目录里的头文件回答", design)

    def test_signature_alignment_uses_the_task_document_mode(self):
        design = DESIGN_DOC.read_text(encoding="utf-8")
        self.assertIn("--task-doc", design)
        self.assertNotIn("--header", design)
        self.assertNotIn("--aclnn-name", design)
        self.assertNotIn("--env", design)

    def test_glossary_uses_the_same_signature_source(self):
        glossary = GLOSSARY.read_text(encoding="utf-8")
        self.assertIn("默认只按任务书 §2.3 声明的那一份接口签名走", glossary)
        self.assertNotIn("默认只按工程声明的那一份接口签名走", glossary)

    def test_moved_heading_references_follow_the_design_doc(self):
        paths = [
            SKILL_ROOT / "scripts" / "make_yaml.py",
            SKILL_ROOT / "scripts" / "_coverage_strategy.py",
            SKILL_ROOT / "scripts" / "verdict.py",
            SKILL_ROOT / "references" / "experimental_standard.md",
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for heading in ("没有-torch-基线s2-怎么写", "比较器"):
            with self.subTest(heading=heading):
                self.assertNotIn(f"builtin-baseline.md#{heading}", text)
                self.assertIn(f"builtin-baseline-design.md#{heading}", text)

    def test_atk_cli_no_longer_carries_the_two_broken_commands(self):
        # 旧写法：单个 pyaclnn 节点 + `-tk run --save_data`，前者任务建不起来，
        # 后者一个 .pt 都存不下。留着会被直接抄去跑。
        cli = ATK_CLI.read_text(encoding="utf-8")
        self.assertNotIn("-tk run --save_data", cli)
        self.assertIn("builtin-baseline.md", cli)

    def test_load_directory_layout_is_spelled_out(self):
        # 标杆数据路径的四段全都要写明，少一段就得去读 ATK 源码。
        for token in ("--output_path", "<backend>_<name>", "output_<i>.pt",
                      "atk/common/utils.py:259"):
            self.assertIn(token, self.text, f"缺少 {token}")

    def test_default_node_names_are_stated(self):
        self.assertIn("pyaclnn_0", self.text)
        self.assertIn("cpu_0", self.text)

    def test_the_two_broken_writings_are_recorded_with_evidence(self):
        self.assertIn("not other task", self.text)
        self.assertIn("atk/tasks/task_creator/aclnn_task.py:54", self.text)
        self.assertIn("atk/tasks/executors/opp_executor.py:574-576", self.text)

    def test_cpu_node_is_required_for_output_shape(self):
        # 「跑内置那一轮为什么还要挂 CPU 节点」是这条路最反直觉的一点。
        self.assertIn("出参", self.text)
        self.assertIn("atk/tasks/executors/opp_executor.py:582-584", self.text)

    def test_counter_experiment_is_mandatory(self):
        # 没有反证实验，「自己跟自己比」和「真的一致」在报告上长得一模一样。
        self.assertIn("反证实验", self.text)
        self.assertIn("Fail", self.text)

    def test_every_script_on_this_path_is_named_with_its_command(self):
        # 只讲原理不给命令时，agent 会自己拼参数——真机上拼错过三次以上。
        for script in ("resolve_opp_library.py", "capture_reference.py",
                       "check_golden_source.py"):
            self.assertIn(script, self.text, f"{script} 没写进文档")

    def test_the_two_rounds_pin_the_library_explicitly(self):
        # 不钉死就是让 ATK 按同名去搜，那正是这条路要挡的事。
        self.assertIn("export ATK_CUSTOM_OPP_PATH", self.text)

    def test_the_gate_command_carries_the_second_round_log(self):
        # --candidate-log 必填：漏了它，第二轮加载了谁全链路没人核。
        self.assertIn("--candidate-log", self.text)

    def test_tamper_borrows_atk_own_pt_helpers(self):
        """改真值不能用裸 torch.load / torch.save。

        真机实测：裸 load 在 torch>=2.6 默认 weights_only=True，读 ATK 存的
        .pt 直接 UnpicklingError；裸 save 丢掉 sanitize_data 与
        pickle_protocol=4，uint 类张量存回去 ATK 读不出来。
        本机测试抓不到这条——测试自己造的 .pt 不是 ATK 格式。
        """
        source = (SKILL_ROOT / "scripts" / "capture_reference.py").read_text(
            encoding="utf-8")
        self.assertIn("from atk.common.utils import torch_load_safe", source)
        self.assertIn("torch_save_safe(", source)
        self.assertNotIn("torch.load(target)", source)
        self.assertNotIn("torch.save(flat", source)

    def test_conclusion_wording_is_constrained(self):
        self.assertIn("回归比对", self.text)
        self.assertIn("不能写成", self.text)


class SeedPinningTest(unittest.TestCase):
    """C7：种子必须钉死，判据从用例数据推导。"""

    @staticmethod
    def _case(case_id, seed_value, name="seed"):
        return {
            "id": case_id,
            "inputs": [
                {"name": "self", "type": "tensor", "dtype": "fp32", "shape": [8]},
                {"name": name, "type": "int", "range_values": seed_value},
            ],
        }

    def _run(self, cases, extra=()):
        failures, notes = [], []
        validate_cases.check_seed_is_pinned(cases, failures, notes,
                                            extra_names=extra)
        return failures, notes

    def test_same_constant_across_cases_passes(self):
        failures, notes = self._run([self._case(0, 42), self._case(1, 42)])
        self.assertEqual([], failures)
        self.assertTrue(any("C7" in note for note in notes))

    def test_seed_varying_by_case_is_refused(self):
        failures, _ = self._run([self._case(0, 42), self._case(1, 43)])
        self.assertEqual(1, len(failures))
        self.assertIn("2 个不同取值", failures[0])

    def test_interval_is_refused_even_when_identical_across_cases(self):
        # 两条用例写的是同一个区间，取值集合只有一个元素，但 ATK 每轮各取一个数。
        failures, _ = self._run([self._case(0, [0, 100]), self._case(1, [0, 100])])
        self.assertEqual(1, len(failures))
        self.assertIn("取值区间", failures[0])

    def test_pinned_pair_is_not_an_interval(self):
        failures, _ = self._run([self._case(0, [42, 42]), self._case(1, [42, 42])])
        self.assertEqual([], failures)

    def test_camel_case_seed_names_are_recognised(self):
        failures, _ = self._run([self._case(0, 1, name="randSeed"),
                                 self._case(1, 2, name="randSeed")])
        self.assertEqual(1, len(failures))

    def test_offset_alone_is_not_treated_as_a_seed(self):
        # 切片、嵌入类算子的 offset 是普通参数，逐条不同是正常设计。
        failures, _ = self._run([self._case(0, 0, name="offset"),
                                 self._case(1, 8, name="offset")])
        self.assertEqual([], failures)

    def test_offset_alongside_seed_is_treated_as_a_seed(self):
        cases = []
        for case_id, offset in ((0, 0), (1, 8)):
            case = self._case(case_id, 42)
            case["inputs"].append(
                {"name": "offset", "type": "int", "range_values": offset})
            cases.append(case)
        failures, _ = self._run(cases)
        self.assertEqual(1, len(failures))
        self.assertIn("offset", failures[0])

    def test_operators_without_a_seed_are_untouched(self):
        failures, notes = self._run([
            {"id": 0, "inputs": [{"name": "self", "type": "tensor",
                                  "dtype": "fp32", "shape": [8]}]}])
        self.assertEqual([], failures)
        self.assertEqual([], notes)

    def test_names_from_the_interface_are_checked_too(self):
        # 词表判不出 philoxState 这类名字，S1 读头文件得到的名单补进来。
        failures, _ = self._run([self._case(0, 1, name="philoxState"),
                                 self._case(1, 2, name="philoxState")],
                                extra=["philoxState"])
        self.assertEqual(1, len(failures))
        self.assertIn("philoxState", failures[0])

    def test_interface_names_absent_from_cases_are_ignored(self):
        # 名单里有、这份用例集里没有的参数，不该凭空报错。
        failures, notes = self._run(
            [{"id": 0, "inputs": [{"name": "self", "type": "tensor",
                                   "dtype": "fp32", "shape": [8]}]}],
            extra=["seed"])
        self.assertEqual([], failures)
        self.assertEqual([], notes)

    def test_case_design_documents_the_rule(self):
        text = CASE_DESIGN.read_text(encoding="utf-8")
        self.assertIn("## 种子类参数", text)
        self.assertIn("default_seed", text)
        self.assertIn("builtin-baseline-design.md", text)


class SpineRegistrationTest(unittest.TestCase):
    """新产物必须进骨架，否则完备性不可判定（CLAUDE.md §3.1）。"""

    def setUp(self):
        self.data = json.loads(
            (SKILL_ROOT / "references" / "artifact-contracts.json")
            .read_text(encoding="utf-8"))["artifacts"]

    def test_four_builtin_artifacts_are_registered(self):
        for name, producer, stage in (
                ("evidence/opp_library_<side>.json", "resolve_opp_library.py", "S3"),
                ("evidence/golden_builtin/", "capture_reference.py", "S3"),
                ("evidence/golden_provenance.json", "capture_reference.py", "S3"),
                ("evidence/golden_source.json", "check_golden_source.py", "S4")):
            with self.subTest(artifact=name):
                self.assertIn(name, self.data)
                self.assertEqual(producer, self.data[name]["producer"])
                self.assertEqual(stage, self.data[name]["stage"])

    def test_they_are_conditional_on_the_baseline_kind(self):
        for name in ("evidence/opp_library_<side>.json", "evidence/golden_builtin/",
                     "evidence/golden_provenance.json", "evidence/golden_source.json"):
            with self.subTest(artifact=name):
                self.assertIn("cann_builtin", self.data[name]["condition"])

    def test_they_point_at_the_new_reference(self):
        for name in ("evidence/opp_library_<side>.json", "evidence/golden_builtin/",
                     "evidence/golden_provenance.json", "evidence/golden_source.json"):
            with self.subTest(artifact=name):
                self.assertTrue(
                    self.data[name]["spec"].startswith("references/builtin-baseline.md"))


if __name__ == "__main__":
    unittest.main()


class MakeYamlBuiltinBranchTest(unittest.TestCase):
    """真机事故（bernoulli，2026-08-17）：`make_yaml.py` 拿 torch 形参名核对
    YAML 输入名，而内置真值这条路上根本没有 torch 基线。

    aclnn 的 `prob` / `seed` / `offset` 在任何 `torch.bernoulli` 重载里都不
    存在，这道子集判定把唯一正确的写法（照 aclnn 形参名写）判成违规，S2 无解。
    这一侧的核对改由 `check_signature_contract.py` 承担，判据更强：集合、
    顺序、多余项三判。
    """

    MUST_COVER = {
        "baseline_kind": "cann_builtin",
        "axes": [], "extract": {},
        "combos": [{"dtype": "fp32", "shape": [4, 4], "prob": 0.5, "seed": 7}],
        "parameters": {
            "self": {"element_kind": "tensor", "runtime_container": "single"},
            "prob": {"element_kind": "scalar", "runtime_container": "single",
                     "dtype": "float", "range": [0, 1]},
            "seed": {"element_kind": "attr", "runtime_container": "single",
                     "dtype": "int64_t", "range": [7, 7]},
        },
        "yaml": {"name": "torch.bernoulli", "aclnn_name": "Bernoulli",
                 "version": "v1", "api": "random", "generate": "g",
                 "api_type": "cpu_shape",
                 "standard": {"acc": "equal", "perf": "not_key"}},
    }

    # torch 装没装上不该改变这两条的结论，所以形参名直接给死。
    TORCH_NAMES = ["input", "generator", "out"]

    def _build(self, must_cover):
        import make_yaml
        return make_yaml.build_design(must_cover, self.TORCH_NAMES)

    def test_aclnn_parameter_names_are_accepted(self):
        design = self._build(json.loads(json.dumps(self.MUST_COVER)))
        self.assertEqual([item["name"] for item in design["inputs"]],
                         ["self", "prob", "seed"])

    def test_torch_baseline_is_still_checked(self):
        import make_yaml
        must_cover = json.loads(json.dumps(self.MUST_COVER))
        must_cover["baseline_kind"] = "torch"
        with self.assertRaises(make_yaml.DeclarationError) as caught:
            self._build(must_cover)
        self.assertIn("不是基线的形参名", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
