"""可表达性判定：同一条规则，decl 期与物化期同一份实现。

设计链是 make_must_cover → 物化 → make_yaml 三步。判定挂在第三步时，
agent 要写完 decl、跑完 must_cover、写完物化脚本、跑完物化，
才被告知第一步就定死的取值 ATK 表达不出来。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _expressibility as expr  # noqa: E402

SCRIPTS = SKILL_ROOT / "scripts"
EXAMPLE = SKILL_ROOT / "assets" / "example"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _decl_fixture import project_from  # noqa: E402


class SemanticKindTest(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual("none", expr.semantic_kind(None))
        self.assertEqual("none", expr.semantic_kind("default"))
        self.assertEqual("scalar", expr.semantic_kind(3))
        self.assertEqual("sequence", expr.semantic_kind([1, 2]))
        self.assertEqual("empty_sequence", expr.semantic_kind([]))


class CheckContractsTest(unittest.TestCase):
    def test_legal_contract_passes(self):
        self.assertEqual([], expr.check_contracts({
            "x": {"element_kind": "tensor", "runtime_container": "single"}}))

    def test_illegal_semantics_combination_is_named(self):
        problems = expr.check_contracts({
            "x": {"element_kind": "tensor", "runtime_container": "bag"}})
        self.assertEqual(["x"], [key for key, _ in problems])

    def test_attr_without_dtype_is_rejected(self):
        problems = expr.check_contracts({
            "n": {"element_kind": "attr", "runtime_container": "single"}})
        self.assertEqual(1, len(problems))
        self.assertIn("dtype", problems[0][1])

    def test_omitted_contract_is_skipped(self):
        self.assertEqual([], expr.check_contracts({"n": {"omitted": True}}))

    def test_channel_qualified_key_reports_the_bare_name(self):
        problems = expr.check_contracts({
            "method_inputs.n": {"element_kind": "attr",
                                "runtime_container": "single"}})
        self.assertEqual(["n"], [key for key, _ in problems])


class AttrDtypeDomainTest(unittest.TestCase):
    """attr 的 dtype 在不在白名单里，decl 期就能判。

    真机事故（roll，2026-08-16）：shifts/dims 是 aclIntArray，契约写了
    `int64_t`（reference 的表当时也这么写），要到跑完 atk case 才被 C6
    判 146 处 unsupported_attr_array_dtype，全链路重生成一轮。
    """

    def test_int64_in_an_attr_array_is_rejected_at_declaration_time(self):
        problems = expr.check_contracts({
            "shifts": {"element_kind": "attr", "runtime_container": "tuple",
                       "dtype": "int64_t"}})
        self.assertEqual(["shifts"], [key for key, _ in problems])
        self.assertIn("int64_t", problems[0][1])

    def test_int_in_an_attr_array_passes(self):
        self.assertEqual([], expr.check_contracts({
            "shifts": {"element_kind": "attr", "runtime_container": "tuple",
                       "dtype": "int"}}))

    def test_int64_is_still_legal_for_a_scalar_attr(self):
        # 两张白名单不是同一份：标量 attr 收 int64_t，数组不收。
        self.assertEqual([], expr.check_contracts({
            "n": {"element_kind": "attr", "runtime_container": "single",
                  "dtype": "int64_t"}}))


class ValueRangeTest(unittest.TestCase):
    """显式 range 与 dtype 相不相容，decl 期就能判。

    真机事故（roll，2026-08-16）：input 写 `range: [-5, 5]`，dtype 轴含
    uint8/uint32，正态采样负均值后按 dtype 截断整张清零，5 条用例退化成
    常量张量。这条要到 S2 末尾冻结才被抓到，那时 atk case 已经跑过。
    """

    PARAMS = {"input": {"element_kind": "tensor", "runtime_container": "single",
                        "range": [-5, 5]}}

    def test_negative_range_on_unsigned_dtype_is_rejected(self):
        problems = expr.check_value_ranges(["fp32", "uint8"], self.PARAMS)
        self.assertEqual(1, len(problems))
        self.assertIn("uint8", problems[0])

    def test_signed_only_dtypes_pass(self):
        self.assertEqual(
            [], expr.check_value_ranges(["fp16", "int8", "int32"], self.PARAMS))

    def test_no_declared_range_is_not_our_business(self):
        self.assertEqual([], expr.check_value_ranges(
            ["uint8"], {"input": {"element_kind": "tensor",
                                  "runtime_container": "single"}}))

    def test_range_wider_than_the_dtype_is_rejected(self):
        problems = expr.check_value_ranges(
            ["int8"], {"x": {"range": [0, 1000]}})
        self.assertEqual(1, len(problems))
        self.assertIn("int8", problems[0])

    def test_each_parameter_is_reported_once(self):
        problems = expr.check_value_ranges(["uint8", "uint32"], self.PARAMS)
        self.assertEqual(1, len(problems))

    # 真机事故（bernoulli，2026-08-17）：种子是 int64_t 的 attr，按
    # case-design.md#种子类参数 必须钉成常量，而张量 dtype 轴里有 uint8，
    # 钉死的写法被判成「超出 uint8」，S2 死锁——报错给的两条出路都做不到。
    SEED = {"seed": {"element_kind": "attr", "runtime_container": "single",
                     "dtype": "int64_t", "range": [20260817, 20260817]}}

    def test_attr_range_is_measured_by_its_own_dtype_not_the_tensor_axis(self):
        self.assertEqual(
            [], expr.check_value_ranges(["fp32", "uint8", "bool"], self.SEED))

    def test_attr_range_still_rejected_when_its_own_dtype_cannot_hold_it(self):
        params = {"n": {"element_kind": "attr", "runtime_container": "single",
                        "dtype": "int8_t", "range": [0, 1000]}}
        problems = expr.check_value_ranges(["fp32"], params)
        self.assertEqual(1, len(problems))
        self.assertIn("int8_t", problems[0])

    def test_scalar_range_uses_its_own_dtype(self):
        params = {"p": {"element_kind": "scalar", "runtime_container": "single",
                        "dtype": "float", "range": [0, 1]}}
        self.assertEqual([], expr.check_value_ranges(["uint8"], params))

    def test_int_span_normalises_c_names_and_atk_tokens(self):
        self.assertEqual(expr.int_span("int64"), expr.int_span("int64_t"))
        self.assertEqual(expr.int_span("int64"), expr.int_span("int"))
        self.assertIsNone(expr.int_span("float"))


class CheckAxisValuesTest(unittest.TestCase):
    PARAMS = {"dims": {"element_kind": "attr", "runtime_container": "list",
                       "dtype": "int64_t"}}
    EXTRACT = {"dims": {"from": "attr", "name": "dims",
                        "runtime_container": "list"}}

    def test_empty_group_is_rejected_at_declaration_time(self):
        problems = expr.check_axis_values(
            {"dims": [[], [0], [0, 1]]}, self.PARAMS, self.EXTRACT)
        self.assertEqual(1, len(problems))
        self.assertIn("空组", problems[0])

    def test_mixed_sequence_and_scalar_needs_a_facet_split(self):
        problems = expr.check_axis_values(
            {"dims": [0, [0, 1]]}, self.PARAMS, self.EXTRACT)
        self.assertEqual(1, len(problems))
        self.assertIn("分面", problems[0])

    def test_sequence_needs_a_group_runtime_container(self):
        problems = expr.check_axis_values(
            {"dims": [[0, 1]]}, self.PARAMS,
            {"dims": {"from": "attr", "name": "dims"}})
        self.assertEqual(1, len(problems))
        self.assertIn("runtime_container", problems[0])

    def test_axis_pointing_at_a_missing_contract(self):
        problems = expr.check_axis_values(
            {"dims": [[0]]}, {}, self.EXTRACT)
        self.assertEqual(1, len(problems))
        self.assertIn("parameters", problems[0])

    def test_non_attr_axes_are_out_of_scope(self):
        # dtype / shape 这类轴不是 attr，可表达性由别处管。
        self.assertEqual([], expr.check_axis_values(
            {"dtype": ["fp32"]}, self.PARAMS,
            {"dtype": {"from": "input_dtype", "index": 0}}))


class MakeMustCoverGateTest(unittest.TestCase):
    """decl 期就要报，不能拖到 make_yaml。"""

    def _decl(self):
        return json.loads((EXAMPLE / "decl.json").read_text(encoding="utf-8"))

    def _run(self, decl):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decl.json"
            path.write_text(json.dumps(decl, ensure_ascii=False),
                            encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPTS / "make_must_cover.py"),
                 "-d", str(path), "-o", str(Path(tmp) / "out.json"),
                 *project_from(tmp, EXAMPLE / "README.md")],
                capture_output=True, text=True)

    def test_example_declaration_still_passes(self):
        self.assertEqual(0, self._run(self._decl()).returncode)

    def test_empty_group_axis_fails_at_step_one(self):
        decl = self._decl()
        decl["dims"]["dims_axis"] = [[], [0]]
        decl["parameters"]["dims_axis"] = {
            "element_kind": "attr", "runtime_container": "list",
            "dtype": "int64_t", "combo_key": "dims_axis"}
        decl["axes"].append("dims_axis")
        decl["extract"]["dims_axis"] = {
            "from": "attr", "name": "dims_axis", "runtime_container": "list"}
        decl["coverage_policy"]["main_effects"].append("dims_axis")
        decl["coverage_policy"]["baseline"]["dims_axis"] = [0]
        result = self._run(decl)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("空组", result.stderr)

    def test_missing_attr_dtype_fails_at_step_one(self):
        decl = self._decl()
        decl["parameters"]["dim"].pop("dtype")
        result = self._run(decl)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("dtype", result.stderr)


if __name__ == "__main__":
    unittest.main()
