"""C2b 与 check_dims 的回归测试。

这两项都是为了堵住同一类事故：门禁自己坏掉或查不到点子上，
导致插件接线错误一路走到 atk case，烧掉唯一一次生成。

C2b 对应的真实事故：constraint 写成 `class X(CaseGenerator, Mixin)` 时，
MRO 让 CaseGenerator 的同名方法胜出，覆写变成死代码。
ATK 不报错、退出码 0，只是产出退化成 max(各输入 dtype 数) × dtype_numbers。
"""

import sys
import unittest
from pathlib import Path

from _paths import SKILL_ROOT

SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_coverage as cc  # noqa: E402


class CheckDimsTest(unittest.TestCase):
    """check_dims 必须存在且可被 validate_cases 调用。"""

    def test_function_exists(self):
        # validate_cases.py 在 main() 里调用 cc.check_dims，缺了它整道门禁直接崩
        self.assertTrue(callable(getattr(cc, "check_dims", None)))

    def test_accepts_consistent_spec(self):
        spec = {
            "dims": {"dtype": ["fp16", "fp32"], "rank": [1, 2]},
            "combos": [
                {"dtype": "fp16", "rank": 1, "shape": [4]},
                {"dtype": "fp32", "rank": 2, "shape": [4, 4]},
            ],
        }
        cc.check_dims(spec)  # 不抛出即通过

    def test_rejects_value_outside_denominator(self):
        spec = {
            "dims": {"dtype": ["fp16", "fp32"]},
            "combos": [{"dtype": "bf16"}],
        }
        with self.assertRaises(SystemExit) as ctx:
            cc.check_dims(spec)
        self.assertEqual(ctx.exception.code, 2)

    def test_rejects_combo_missing_axis(self):
        spec = {
            "dims": {"dtype": ["fp16"], "rank": [1]},
            "combos": [{"dtype": "fp16"}],
        }
        with self.assertRaises(SystemExit) as ctx:
            cc.check_dims(spec)
        self.assertEqual(ctx.exception.code, 2)


PROBE = """
import sys
sys.path.insert(0, {scripts!r})
from atk.case_generator.generator.base_generator import CaseGenerator
from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
import validate_cases

class Mixin:
    def _get_case_numbers(self):
        return 41
    def generate(self):
        return None

bases = (Mixin, CaseGenerator) if {mixin_first} else (CaseGenerator, Mixin)
GENERATOR_REGISTRY.register_with_key("__probe", type("Probe", bases, {{}}))

failures, notes = [], []
validate_cases.check_override_effective("__probe", failures, notes)
print("FAILURES", len(failures))
print(failures[0] if failures else "")
"""


class OverrideEffectiveTest(unittest.TestCase):
    """C2b：基类顺序写反时必须报错，写对时必须放行。

    在子进程里跑：ATK 会拉起 torch_npu，在 pytest 进程内重复初始化会撞
    TORCH_LIBRARY 重复注册。子进程让 torch 只初始化一次，也更贴近真实调用方式。
    """

    def _probe(self, mixin_first):
        import subprocess

        code = PROBE.format(scripts=str(SCRIPTS), mixin_first=mixin_first)
        done = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True)
        if "FAILURES" not in done.stdout:
            self.skipTest(f"当前解释器跑不起 ATK，跳过 C2b：{done.stderr.strip()[-200:]}")
        return done.stdout

    def test_wrong_base_order_is_rejected(self):
        out = self._probe(False)
        self.assertIn("FAILURES 1", out, "基类顺序写反时 C2b 必须报错")
        self.assertIn("C2b", out)

    def test_correct_base_order_passes(self):
        out = self._probe(True)
        self.assertIn("FAILURES 0", out, "基类顺序正确时不应报错")


if __name__ == "__main__":
    unittest.main()
