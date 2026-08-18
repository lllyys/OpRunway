"""YAML 输入名必须等于基线函数形参名。

真机实测（roll，2026-08-15）：输入命名成 x 而基线形参是 input，
带 name 的输入在 dataset reload 之后全部进 kwargs（L0 binding.inputs），
基线调用直接 TypeError。全量 42/81 失败，根因在 S2 的一次命名。

判据不问 agent：基线形参名从基线符号反射得到。
"""

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import make_yaml  # noqa: E402

EXAMPLE = SKILL_ROOT / "assets" / "example"
SCRIPTS = SKILL_ROOT / "scripts"


def run_union(info, symbol="torch.demo"):
    """跑 `make_yaml.baseline_parameter_names`，`align_signatures.baseline_signature`
    换成直接返回 info 的桩——只测并集逻辑，不模拟 eval/inspect/torch.overrides
    的细节，那部分由 test_align_signatures.py 覆盖。子进程隔离，避免桩污染
    其余测试用的 sys.modules["torch"]。
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        driver = Path(temp_dir) / "driver.py"
        driver.write_text(textwrap.dedent(f'''
            import json, sys, types
            sys.modules["torch"] = types.ModuleType("torch")
            sys.path.insert(0, {str(SCRIPTS)!r})
            import align_signatures
            align_signatures.baseline_signature = lambda symbol: {info!r}
            import make_yaml
            print(json.dumps(make_yaml.baseline_parameter_names({symbol!r})))
        '''), encoding="utf-8")
        done = subprocess.run([sys.executable, str(driver)],
                              capture_output=True, text=True, timeout=30)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)


class CheckBaselineBindingTest(unittest.TestCase):
    def test_matching_names_pass(self):
        self.assertEqual(
            [], make_yaml.check_baseline_binding(
                ["input", "dim", "keepdim"], ["input", "dim", "keepdim"]))

    def test_renamed_input_is_rejected(self):
        problems = make_yaml.check_baseline_binding(
            ["input", "dim"], ["x", "dim"])
        self.assertEqual(1, len(problems))
        self.assertIn("'x'", problems[0])
        self.assertIn("kwargs", problems[0])

    def test_order_does_not_matter_only_membership(self):
        # 顺序由契约声明的语义顺序管，这条门禁只管名字对不对得上。
        self.assertEqual(
            [], make_yaml.check_baseline_binding(
                ["input", "dim"], ["dim", "input"]))

    def test_unknown_baseline_names_are_not_silently_passed(self):
        # 取不到形参名不等于「不需要核对」。静默放行就是把 roll 那轮的
        # 失败模式原样留着，只是换了个借口。
        problems = make_yaml.check_baseline_binding(None, ["x"])
        self.assertEqual(1, len(problems))
        self.assertIn("取不到", problems[0])


class DesignChainStillPassesTest(unittest.TestCase):
    """样例链路带上这道门禁之后仍然要能跑通。"""

    def test_example_input_names_match_the_baseline(self):
        decl = json.loads((EXAMPLE / "decl.json").read_text(encoding="utf-8"))
        names = list(decl["parameters"])
        baseline = make_yaml.baseline_parameter_names(decl["yaml"]["name"])
        if baseline is None:
            self.skipTest("本机没有 torch，取不到基线形参名")
        self.assertEqual([], make_yaml.check_baseline_binding(baseline, names))

    def test_renamed_contract_fails_make_yaml(self):
        decl = json.loads((EXAMPLE / "decl.json").read_text(encoding="utf-8"))
        if make_yaml.baseline_parameter_names(decl["yaml"]["name"]) is None:
            self.skipTest("本机没有 torch，取不到基线形参名")
        decl["parameters"]["not_a_baseline_param"] = \
            decl["parameters"].pop("input")
        decl["extract"]["dtype"] = {"from": "input_dtype", "index": 0}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            broken = tmp / "decl.json"
            broken.write_text(json.dumps(decl, ensure_ascii=False),
                              encoding="utf-8")
            must_cover = tmp / "must_cover.json"
            materialized = tmp / "materialized.json"
            design = tmp / "out.yaml"
            for command in (
                    [SCRIPTS / "make_must_cover.py", "-d", broken,
                     "-o", must_cover],
                    [EXAMPLE / "materialize.py", must_cover, materialized]):
                step = subprocess.run([sys.executable, *map(str, command)],
                                      capture_output=True, text=True)
                self.assertEqual(0, step.returncode, step.stderr)
            step = subprocess.run(
                [sys.executable, str(SCRIPTS / "make_yaml.py"),
                 "-m", str(materialized), "-o", str(design)],
                capture_output=True, text=True)
            self.assertEqual(2, step.returncode, step.stdout)
            self.assertIn("not_a_baseline_param", step.stderr)


class BaselineParameterNamesUnionTest(unittest.TestCase):
    """真机事故（median，2026-08-16）：`torch.overrides` 的测试替身给
    median 和 sum 都少列了 keepdim（替身是给 __torch_function__ 测试写的
    桩，不保证形参齐全；真实 builtin 完全接受 keepdim）。旧实现只信替身，
    硬门禁把合法的 keepdim 当成不存在的形参拒了。改用全部 aten 重载形参名
    的并集，不挑一个重载做参数个数匹配——median 的全张量分面入参数量对不
    上任何一个重载，并集不需要这一步匹配就能覆盖。
    """

    def test_median_dim_facet_keepdim_recovered_from_overloads(self):
        info = {
            "parameters": [{"name": "input"}, {"name": "dim"}],
            "overloads": [
                "aten::median(Tensor self) -> Tensor",
                "aten::median.dim(Tensor self, int dim, bool keepdim=False)"
                " -> (Tensor values, Tensor indices)",
            ],
        }
        names, trusted = run_union(info, "torch.median")
        self.assertIn("keepdim", names)
        self.assertTrue(trusted)
        self.assertEqual([], make_yaml.check_baseline_binding(
            names, ["input", "dim", "keepdim"], trusted))

    def test_median_full_tensor_facet_still_resolves(self):
        # 全张量分面的 aclnn 侧只有 1 个入参，对不上任何重载的参数个数——
        # 并集不需要按个数匹配，input 依然能核对通过。
        info = {
            "parameters": [{"name": "input"}, {"name": "dim"}],
            "overloads": ["aten::median(Tensor self) -> Tensor"],
        }
        names, _ = run_union(info, "torch.median")
        self.assertEqual([], make_yaml.check_baseline_binding(names, ["input"]))

    def test_sum_shim_missing_keepdim_recovered_from_dtype_overload(self):
        info = {
            "parameters": [{"name": "input"}, {"name": "dim"}],
            "overloads": [
                "aten::sum.dim_IntList(Tensor self, int[1]? dim, bool keepdim=False,"
                " *, ScalarType? dtype=None) -> Tensor",
            ],
        }
        names, _ = run_union(info, "torch.sum")
        self.assertIn("keepdim", names)

    def test_shim_only_names_are_undecidable_not_a_violation(self):
        # torch_npu 的自定义算子查不到 aten 重载，并集退化成只信替身。
        # 名单可能不全，此时「名字不在名单里」判不了，不能当违规硬拒——
        # 那正是 median 那轮逼 agent 中途改量具的姿势。
        info = {"parameters": [{"name": "input"}, {"name": "dim"}],
                "source": "torch.overrides", "overloads": []}
        names, trusted = run_union(info, "torch_npu.npu_demo")
        self.assertFalse(trusted)
        problems = make_yaml.check_baseline_binding(
            names, ["input", "dim", "keepdim"], trusted)
        self.assertEqual(1, len(problems))
        self.assertIn("判不了", problems[0])
        self.assertIn("--baseline-names", problems[0])

    def test_aten_backed_names_stay_a_hard_reject(self):
        # 有重载背书时名单是完整的，错名字仍然当场拒。
        info = {"parameters": [{"name": "input"}], "source": "torch.overrides",
                "overloads": ["aten::demo(Tensor self) -> Tensor"]}
        names, trusted = run_union(info, "torch.demo")
        self.assertTrue(trusted)
        problems = make_yaml.check_baseline_binding(names, ["x"], trusted)
        self.assertIn("不是基线的形参名", problems[0])

    def test_naming_mistake_is_still_rejected(self):
        # 原始事故（roll，2026-08-15）：输入名写成 x。并集扩大的是「合法基线
        # 形参」的集合，不是放行任意名字——这条不能被并集修复带回来。
        info = {
            "parameters": [{"name": "input"}, {"name": "shifts"}, {"name": "dims"}],
            "overloads": [],
        }
        names, _ = run_union(info, "torch.roll")
        problems = make_yaml.check_baseline_binding(names, ["x", "shifts"])
        self.assertEqual(1, len(problems))
        self.assertIn("'x'", problems[0])


if __name__ == "__main__":
    unittest.main()
