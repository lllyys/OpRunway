"""内置实现当真值这条路的知识与门禁。

这些事实都是从 ATK 源码读出来的，不是猜的；钉在这里是为了不被后续精简删掉。
真机上少任何一条，agent 都会现场翻 ATK 源码重推一遍。
"""


import unittest

from _paths import (REFERENCES, SKILL_ROOT, nested_side_page,
                    require_nested_source)


DOC = REFERENCES / "builtin-baseline.md"
DESIGN_DOC = REFERENCES / "builtin-baseline-design.md"
GLOSSARY = REFERENCES / "glossary.md"
ATK_CLI = REFERENCES / "atk-cli.md"
SKILL_FILES = [
    SKILL_ROOT / "SKILL.md",
    nested_side_page("case-gen"),
    nested_side_page("acceptance"),
]


class BuiltinBaselineKnowledgeTest(unittest.TestCase):
    def setUp(self):
        require_nested_source(self, "内置真值知识跨两侧边界")
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




if __name__ == "__main__":
    unittest.main()
