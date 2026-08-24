"""量具失败时必须自带下一步动作，不能只丢一个调用栈。

真机连踩两次同一形状的坑：`align_signatures.py` 用系统 python3 跑、缺 torch，
`probe_atk_capabilities.py` 在 CANN 未加载时被 torch_npu 的后端扩展拖崩。
两次的输出都是几十行栈，没有一个字说该换哪个解释器——agent 只能再试一遍。
"""

import ast
import sys
import unittest
from pathlib import Path

from _paths import SCRIPTS


sys.path.insert(0, str(SCRIPTS))

from _runtime_guard import RUNTIME_EXIT, runtime_imports  # noqa: E402
from probe_env import cann_set_env_scripts  # noqa: E402


class RuntimeGuardTest(unittest.TestCase):
    def test_import_failure_becomes_an_actionable_exit(self):
        with self.assertRaises(SystemExit) as caught:
            with runtime_imports("torch"):
                raise ImportError("No module named 'torch'")
        self.assertEqual(RUNTIME_EXIT, caught.exception.code)

    def test_backend_load_failure_is_caught_too(self):
        # torch_npu 在 CANN 未加载时抛的是 RuntimeError，不是 ImportError。
        with self.assertRaises(SystemExit):
            with runtime_imports("torch_npu"):
                raise RuntimeError("Failed to load the backend extension")

    def test_guard_stays_out_of_the_way_when_imports_work(self):
        with runtime_imports("torch"):
            value = 1
        self.assertEqual(1, value)

    def test_unrelated_errors_are_not_swallowed(self):
        # 反例：门禁自身的判定失败必须照常冒出去，不能被翻译成「解释器不对」。
        with self.assertRaises(ValueError):
            with runtime_imports("torch"):
                raise ValueError("设计不合法")

    def test_scripts_needing_torch_are_wired_to_the_guard(self):
        for name in ("align_signatures.py", "probe_atk_capabilities.py"):
            with self.subTest(script=name):
                tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
                calls = [
                    node for node in ast.walk(tree)
                    if isinstance(node, ast.withitem)
                    and isinstance(node.context_expr, ast.Call)
                    and getattr(node.context_expr.func, "id", "") == "runtime_imports"
                ]
                self.assertTrue(calls, f"{name} 没有把运行时导入包进 runtime_imports")


class CannSetEnvDiscoveryTest(unittest.TestCase):
    def test_discovery_returns_absolute_existing_paths(self):
        # 本机通常没有 CANN，返回空表也算通过；有的话必须是真实存在的绝对路径。
        for path in cann_set_env_scripts():
            self.assertTrue(Path(path).is_absolute())
            self.assertTrue(Path(path).is_file())
            self.assertEqual("set_env.sh", Path(path).name)

    def test_discovery_deduplicates_symlinked_roots(self):
        paths = cann_set_env_scripts()
        reals = [str(Path(p).resolve()) for p in paths]
        self.assertEqual(len(reals), len(set(reals)))


if __name__ == "__main__":
    unittest.main()
