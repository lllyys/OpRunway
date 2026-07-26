#!/usr/bin/env python3
"""run_workflow 的 runner_form→mode 路由门；不访问 NPU。"""

import unittest

import run_workflow as W


class WorkflowModeResolutionTest(unittest.TestCase):
    def test_omitted_mode_is_derived_from_runner_form(self):
        self.assertEqual(W._resolve_mode({}, None), "new_example")
        self.assertEqual(
            W._resolve_mode({"runner_form": "cpp"}, None), "new_example")
        self.assertEqual(
            W._resolve_mode({"runner_form": "aclnn_py"}, None), "aclnn_py")

    def test_explicit_real_machine_mismatch_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, "不匹配"):
            W._resolve_mode({"runner_form": "aclnn_py"}, "new_example")
        with self.assertRaisesRegex(SystemExit, "不匹配"):
            W._resolve_mode({"runner_form": "cpp"}, "aclnn_py")

    def test_explicit_non_acceptance_escape_remains_available(self):
        self.assertEqual(
            W._resolve_mode({"runner_form": "aclnn_py"}, "mock"), "mock")

    def test_unknown_runner_form_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, "不受支持"):
            W._resolve_mode({"runner_form": "opaque"}, None)


if __name__ == "__main__":
    unittest.main()
