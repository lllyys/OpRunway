import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _paths import SKILL_ROOT, add_tests_to_path


ROOT = SKILL_ROOT
SCRIPTS = ROOT / "scripts"
EXAMPLE = ROOT / "assets" / "example"

add_tests_to_path()
from _decl_fixture import project_from  # noqa: E402


def run(*args):
    # 模板按 evidence/env.sh 的约定从 ATK_SKILL_DIR 找量具目录，
    # 测试里显式给上，顺便证明这条约定是可用的。
    env = dict(os.environ, ATK_SKILL_DIR=str(ROOT))
    return subprocess.run([sys.executable, *map(str, args)],
                          capture_output=True, text=True, env=env)


class AssetsExampleTest(unittest.TestCase):
    """assets/example 必须始终能跑通整条 S2 设计链。

    它是 agent 唯一可抄的目标文件样例。样例一旦和脚本漂移，agent 抄到的就是
    错的，只能回去读量具源码反推——median 那轮 S2 的 153 次调用里，40 次是
    读量具源码、17 次是对着门禁改声明试错，成本几乎全出在这里。

    所以这条测试守的不是脚本，是「样例仍然有效」这件事本身。
    """

    def test_design_chain_runs_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            must_cover = tmp / "must_cover.json"
            materialized = tmp / "materialized.json"
            design = tmp / "example.yaml"

            step = run(SCRIPTS / "make_must_cover.py",
                       "-d", EXAMPLE / "decl.json", "-o", must_cover,
                       *project_from(tmp, EXAMPLE / "README.md"))
            self.assertEqual(0, step.returncode, step.stderr)

            step = run(EXAMPLE / "materialize.py", must_cover, materialized)
            self.assertEqual(0, step.returncode, step.stderr)

            step = run(SCRIPTS / "make_yaml.py",
                       "-m", materialized, "-o", design)
            self.assertEqual(0, step.returncode, step.stderr)

            combos = json.loads(materialized.read_text(encoding="utf-8"))["combos"]
            tags = {tag for combo in combos for tag in combo.get("coverage_tags", [])}
            self.assertEqual({"empty", "single_element"}, tags)

    def test_function_template_wires_its_register_name_to_api_type(self):
        """模板必须自带「注册名怎么被选中」这一环，否则加载了也用不上。

        真机现象：agent 写完 function_ 插件后，无处可查 ATK 凭什么选它，
        为了确认 `api`/`api_type` 的语义连读了 11 次 ATK 源码。缺的就是这一行。
        """
        text = (EXAMPLE / "function_example.py").read_text(encoding="utf-8")
        self.assertIn("api_type", text)
        self.assertIn('@register("example_cpu")', text)
        self.assertIn('"api_type": "example_cpu"', text)

    def test_example_carries_no_operator_under_test(self):
        # 样例是模板，不是某个算子的验收产物。带上待验收算子名就会被照抄成特化。
        banned = ("median", "bernoulli", "remainder", "huber")
        for path in EXAMPLE.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8").lower()
            for name in banned:
                self.assertNotIn(name, text, f"{path.name} 出现算子名 {name}")

    def test_attr_dtype_uses_the_cpp_vocabulary(self):
        # attr 的 dtype 词表和 tensor 的不是一套（int64_t 而非 int64）。
        # 这一条踩过，模板必须把正确写法固化下来。
        decl = json.loads((EXAMPLE / "decl.json").read_text(encoding="utf-8"))
        capabilities = json.loads(
            (ROOT / "references" / "atk-parameter-capabilities.json")
            .read_text(encoding="utf-8"))
        allowed = set(capabilities["pyaclnn"]["attr_single_dtypes"])
        for name, contract in decl["parameters"].items():
            if contract.get("element_kind") != "attr":
                continue
            self.assertIn(contract["dtype"], allowed, name)


if __name__ == "__main__":
    unittest.main()


class AclnnExecutorTemplateTest(unittest.TestCase):
    """aclnn 执行器模板的两条硬约束。

    这是全流程风险最高的一处：适配器只给一次修正机会，
    而 skill 原来对它零样例——只有一串「不得枚举/不得伪造」的禁令，
    却没说合规的写法长什么样，也没说「运行时契约表」到底是什么东西。
    """

    TEMPLATE = EXAMPLE / "aclnn_executor.py"
    CONTRACT_TABLES = ("CPP_TO_PYTHON_TYPE", "PYTYPE_TO_CTYPE")

    def _executable_source(self):
        """模板里剥掉 docstring 的可执行部分。"""
        import ast
        tree = ast.parse(self.TEMPLATE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) \
                    and ast.get_docstring(node) is not None:
                node.body = node.body[1:]
        return ast.unparse(tree)

    def test_template_does_not_fake_an_untyped_null_pointer(self):
        # 禁令写在 plugin-authoring.md 里，模板本身必须是正面示范。
        # 查 AST 而不是文本：docstring 里出现 c_void_p 是在讲「别这么写」。
        code = self._executable_source()
        self.assertNotIn("c_void_p", code)
        self.assertIn("ctypes.POINTER(CPP_TO_PYTHON_TYPE[", code)

    def test_contract_tables_are_named_in_both_places(self):
        # 「运行时契约表」必须有确切身份，否则 agent 拿不到唯一合规的信息来源。
        doc = (ROOT / "references" / "plugin-authoring.md").read_text(encoding="utf-8")
        template = self.TEMPLATE.read_text(encoding="utf-8")
        for table in self.CONTRACT_TABLES:
            self.assertIn(table, doc, f"reference 没有点名 {table}")
            self.assertIn(table, template, f"模板没有点名 {table}")

    def test_template_locates_by_output_package_boundary_not_a_magic_number(self):
        # convert_input_data 返回 list 再 extend，一个 YAML 输入可能展开成多个 arg，
        # 所以按 len(input_args) 比魔数定位插入点是错的。出参永远在尾部。
        text = self.TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("len(output_packages)", text)

    def test_template_does_not_append_workspace_or_executor(self):
        # 后端绑定函数时固定追加 workspaceSize 和 executor，薄壳再补一次就错了。
        # 只看可执行代码：docstring 里出现这些词是在讲「别补」。
        for symbol in ("workspaceSize", "OpExecutor", "aclOpExecutor"):
            self.assertNotIn(symbol, self._executable_source())

    def test_alignment_report_keys_are_documented(self):
        # agent 靠这几个键决定要不要写适配器；漏一个就得自己 print 出来猜。
        doc = (ROOT / "references" / "plugin-authoring.md").read_text(encoding="utf-8")
        for key in ("aclnn_adapter.required", "aclnn_adapter.reasons",
                    "aclnn_adapter.determinable", "semantic_review"):
            self.assertIn(key, doc, f"reference 没有说明 {key}")


class GeneratorTemplateTest(unittest.TestCase):
    """生成器模板只许覆写 ATK 真实存在的字段。

    `CaseConfig` 和 `InputCaseConfig` 都不放开 extra，写未声明字段直接
    `ValueError: "CaseConfig" object has no field`。reference 一度要求
    「保留 combo id 和 coverage tags」——`coverage_tags` 根本不存在，照做必崩。
    """

    TEMPLATE = EXAMPLE / "constraint.py"

    CASE_FIELDS = {
        "id", "default_seed", "name", "aclnn_name", "triton_name",
        "kernel_name", "version", "expected_error_msg", "api", "api_type",
        "aclnn_api_type", "backward", "standard", "outputs", "inputs",
        "method_inputs", "tensor_input", "compute_times", "save_name",
        "is_boundary", "strategy",
    }
    INPUT_FIELDS = {
        "name", "type", "required", "dtype", "shape", "range_values",
        "backward", "align_32B", "outlier_values",
    }

    def _assigned_attributes(self):
        import ast
        tree = ast.parse(self.TEMPLATE.read_text(encoding="utf-8"))
        case, member = set(), set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Attribute):
                    continue
                base = ast.unparse(target.value)
                if base == "case":
                    case.add(target.attr)
                elif base.startswith("case.inputs"):
                    member.add(target.attr)
        return case, member

    def test_only_real_case_config_fields_are_written(self):
        case, member = self._assigned_attributes()
        self.assertTrue(case or member, "模板应当示范字段覆写")
        self.assertEqual(set(), case - self.CASE_FIELDS)
        self.assertEqual(set(), member - self.INPUT_FIELDS)

    def test_coverage_tags_is_never_written_onto_a_case(self):
        self.assertNotIn("coverage_tags",
                         self.TEMPLATE.read_text(encoding="utf-8"))
        doc = (ROOT / "references" / "plugin-authoring.md").read_text(encoding="utf-8")
        self.assertIn("没有 `coverage_tags` 字段", doc)

    def test_combo_is_indexed_with_index_minus_one(self):
        # generate() 里 self.index 在返回前已自增，所以本条组合是 index - 1。
        self.assertIn("self.index - 1",
                      self.TEMPLATE.read_text(encoding="utf-8"))


class ReplayTemplateTest(unittest.TestCase):
    """重放模板守住 S4 归因的四条硬事实。

    roll 那轮 27 次 ATK 探源里有 21 次花在「怎么自己调待验收算子库拿差异」上：
    equal 比较器是什么、冻结输入怎么读、acl 对象怎么造。
    skill 要求归因却不给工具，agent 只能读源码反推。
    """

    TEMPLATE = EXAMPLE / "replay_case.py"

    def source(self):
        return self.TEMPLATE.read_text(encoding="utf-8")

    def test_template_parses(self):
        import ast
        ast.parse(self.source())

    def test_frozen_input_is_read_with_the_uint_safe_loader(self):
        # torch 没有 uint32，裸 torch.load 读出来是 dict，当张量用会抛
        # AttributeError——roll 那轮 agent 自己的重放脚本就崩在这里。
        text = self.source()
        self.assertIn("torch_load_safe", text)
        self.assertNotIn("torch.load(", text)

    def test_output_is_allocated_from_the_baseline_result(self):
        # 出参的 shape 与 dtype 由期望值定义，猜出来的出参会让失败原因变成自造的。
        self.assertIn("torch.empty_like", self.source())

    def test_template_demands_a_passing_control_case(self):
        # 对照用例不过说明绑定或环境不对，这时失败用例的结论一律不成立。
        self.assertIn("对照", self.source())

    def test_template_forbids_cross_group_extrapolation(self):
        text = self.source()
        self.assertIn("unknown", text)
