"""内置实现当真值这条路的知识与门禁。

这些事实都是从 ATK 源码读出来的，不是猜的；钉在这里是为了不被后续精简删掉。
真机上少任何一条，agent 都会现场翻 ATK 源码重推一遍。
"""


import json
import sys
import unittest

from _paths import REFERENCES, SCRIPTS

sys.path.insert(0, str(SCRIPTS))

import validate_cases  # noqa: E402

CASE_DESIGN = REFERENCES / "case-design.md"


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
