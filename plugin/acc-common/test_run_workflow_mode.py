#!/usr/bin/env python3
"""run_workflow 的 runner_form→mode 路由门；不访问 NPU。"""

import unittest

import run_workflow as W


class WorkflowModeResolutionTest(unittest.TestCase):
    def test_omitted_mode_is_derived_from_runner_form(self):
        # ⚠ 派生逻辑与**准入**是两件事：cpp / aclnn_py 仍然派生得出 mode，只是不准入正式验收。
        #   这里带 allow_experimental_form=True 单测派生本身；准入见 AcceptanceFormGateTest。
        self.assertEqual(W._resolve_mode({}, None, allow_experimental_form=True), "new_example")
        self.assertEqual(
            W._resolve_mode({"runner_form": "cpp"}, None, allow_experimental_form=True),
            "new_example")
        self.assertEqual(
            W._resolve_mode({"runner_form": "aclnn_py"}, None, allow_experimental_form=True),
            "aclnn_py")
        self.assertEqual(
            W._resolve_mode({"runner_form": "cpp_extension"}, None), "cpp_extension")

    def test_explicit_real_machine_mismatch_is_rejected(self):
        # 「走错真机通路」是输入错，应当比准入门更早报出来——否则用户打错 mode 时
        # 收到的是「这条通路不准入」，看不出自己 mode 打错了。
        with self.assertRaisesRegex(SystemExit, "不匹配"):
            W._resolve_mode({"runner_form": "aclnn_py"}, "new_example")
        with self.assertRaisesRegex(SystemExit, "不匹配"):
            W._resolve_mode({"runner_form": "cpp"}, "aclnn_py")
        with self.assertRaisesRegex(SystemExit, "不匹配"):
            W._resolve_mode({"runner_form": "cpp_extension"}, "aclnn_py")

    def test_explicit_non_acceptance_escape_remains_available(self):
        """⭐ mock 逃生口不受准入门影响：它物理上就不产 acceptance.json，与 runner_form 准入无关。"""
        self.assertEqual(
            W._resolve_mode({"runner_form": "aclnn_py"}, "mock"), "mock")
        self.assertEqual(
            W._resolve_mode({"runner_form": "cpp"}, "mock"), "mock")

    def test_unknown_runner_form_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, "不受支持"):
            W._resolve_mode({"runner_form": "opaque"}, None)


class AcceptanceFormGateTest(unittest.TestCase):
    """⭐ 正式验收当前只准入 cpp_extension（唯一跑通完整 torch_parity 矩阵的通路）。

    门必须落在**两处**：入口拦正常调用路径，出口（写 acceptance.json / verdict.json 之前）
    拦绕过入口的路径。只拦入口是拦不住的——仓里已有先例：`repo_adapter.py` 的注释明写
    `repo_adapter.py cs wd acceptance.json catlass_mock` 是绕开 CLI 守卫的现成后门。
    """

    def test_entry_gate_blocks_unlisted_forms_with_actionable_message(self):
        for form in ("cpp", "aclnn_py"):
            with self.subTest(form=form):
                with self.assertRaises(SystemExit) as cm:
                    W._resolve_mode({"runner_form": form}, None)
                msg = str(cm.exception)
                self.assertIn("不用于正式验收", msg)
                self.assertIn("--allow-experimental-form", msg)   # 要讲清怎么绕
                self.assertIn("torch_parity", msg)                # 要讲清为什么堵

    def test_entry_gate_lets_cpp_extension_through(self):
        self.assertEqual(W._resolve_mode({"runner_form": "cpp_extension"}, None),
                         "cpp_extension")

    def test_escape_hatch_allows_run_but_never_acceptance_artifacts(self):
        """逃生阀只放行**执行**，不放行**产验收裁决**——出口门仍然拦。"""
        self.assertEqual(
            W._resolve_mode({"runner_form": "aclnn_py"}, None, allow_experimental_form=True),
            "aclnn_py")
        with self.assertRaises(SystemExit) as cm:
            W._assert_acceptance_form_allowed({"runner_form": "aclnn_py"}, "aclnn_py")
        self.assertIn("出口门", str(cm.exception))

    def test_exit_gate_blocks_even_when_entry_was_bypassed(self):
        for form in ("cpp", "aclnn_py"):
            with self.subTest(form=form):
                with self.assertRaisesRegex(SystemExit, "拒绝为 runner_form"):
                    W._assert_acceptance_form_allowed(
                        {"runner_form": form}, _MODE_OF[form])

    def test_exit_gate_lets_cpp_extension_through(self):
        W._assert_acceptance_form_allowed({"runner_form": "cpp_extension"}, "cpp_extension")

    def test_capability_tables_are_not_touched(self):
        """能力表 ≠ 准入表：删了将来想恢复要重新考证 dtype 支持面。"""
        import repo_adapter
        for form in ("cpp", "aclnn_py"):
            self.assertIn(form, repo_adapter.SUPPORTED_NP_BY_FORM)


_MODE_OF = {"cpp": "new_example", "aclnn_py": "aclnn_py", "cpp_extension": "cpp_extension"}


if __name__ == "__main__":
    unittest.main()
