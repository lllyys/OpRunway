"""签名解析器的白名单判定。反例全集来自 Codex 评审，缺一条即回归。"""

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from _review_signature import parse_signature  # noqa: E402


class TestParseSignature(unittest.TestCase):

    def assert_incomplete(self, text):
        result = parse_signature(text)
        self.assertEqual(result["status"], "incomplete", text)
        self.assertEqual(result["params"], [], "非 complete 不放行半解析结果")

    # --- Codex 评审要求的反例全集 ---

    def test_truncated_def(self):
        self.assert_incomplete("def f(x")

    def test_two_defs_is_multiple(self):
        result = parse_signature("def f(x):\ndef g(y):")
        self.assertEqual(result["status"], "multiple")
        self.assertEqual(result["params"], [])

    def test_c_bad_param_placeholder(self):
        self.assert_incomplete("aclnnStatus f(const aclTensor *x, ???);")

    def test_python_posonly_kwonly_separators(self):
        result = parse_signature("def f(x, /, *, y):")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["params"], ["x", "y"])

    def test_c_duplicate_param_names(self):
        self.assert_incomplete("void f(int a, int a)")

    def test_c_prototype_complete(self):
        result = parse_signature(
            "aclnnStatus aclnnFoo(const aclTensor *x, aclTensor *out)")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["params"], ["x", "out"])

    def test_python_annotations_and_defaults_ordered(self):
        result = parse_signature(
            'def apply(x: "aclTensor", y: int = 8, *, mode: str = "high") -> int:')
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["params"], ["x", "y", "mode"])

    def test_empty_and_prose(self):
        self.assert_incomplete("")
        self.assert_incomplete("确认 · 接口定义")

    def test_python_varargs_kwargs(self):
        result = parse_signature("def f(*args, **kw):")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["params"], ["args", "kw"])

    def test_c_void_means_zero_params(self):
        result = parse_signature("void f(void)")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["params"], [])

    # --- 白名单边界的补充裁量 ---

    def test_c_empty_parens_is_undeclared_not_zero(self):
        self.assert_incomplete("void f()")

    def test_c_param_missing_name_trailing_star(self):
        self.assert_incomplete("aclnnStatus f(const aclTensor *)")

    def test_c_untyped_single_identifier_param(self):
        self.assert_incomplete("void f(x)")

    def test_python_ellipsis_placeholder(self):
        self.assert_incomplete("def f(x, ...):")

    def test_mixed_def_and_c_prototype_is_multiple(self):
        result = parse_signature("def f(x):\nvoid g(int a)")
        self.assertEqual(result["status"], "multiple")
        self.assertEqual(result["params"], [])

    def test_python_duplicate_param_names(self):
        self.assert_incomplete("def f(x, x):")

    # --- checkpoint 终审反例（2026-09-17）：限定词不是类型核，尾随残片拒收 ---

    def test_c_qualifier_only_param_is_incomplete(self):
        self.assert_incomplete("void f(const aclTensor)")

    def test_c_struct_tag_without_name_is_incomplete(self):
        self.assert_incomplete("void f(struct Tensor)")

    def test_c_qualifier_pointer_without_type_is_incomplete(self):
        self.assert_incomplete("void f(const *x)")

    def test_c_extra_closing_paren_is_incomplete(self):
        self.assert_incomplete("void f(int a))")

    def test_c_trailing_garbage_is_incomplete(self):
        self.assert_incomplete("void f(int a) extra")

    def test_c_qualified_named_param_still_complete(self):
        self.assertEqual(
            {"status": "complete", "params": ["x"]},
            parse_signature("void f(const aclTensor *x)"))


if __name__ == "__main__":
    unittest.main()
