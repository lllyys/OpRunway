"""接线字段的受控改写：用例语义未变才算数。

真机实测（roll，2026-08-15）：aclnn 适配器接线要改 YAML 重跑 atk case，
与「S2 后冻结」纪律冲突，skill 没给出口，agent 手工自证了十次。

这些用例锁的是判据本身——「只有接线字段变了」怎么判。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import rewire_adapter as rewire  # noqa: E402

SCRIPT = SKILL_ROOT / "scripts" / "rewire_adapter.py"


def case(case_id, dtype="fp32", **wiring):
    return dict({"id": case_id,
                 "inputs": [{"name": "x", "type": "tensor", "dtype": dtype,
                             "shape": [4], "range_values": [-5, 5]}]},
                **wiring)


class ParseSetTest(unittest.TestCase):
    def test_parses_key_value(self):
        self.assertEqual({"aclnn_api_type": "my_exec"},
                         rewire.parse_set(["aclnn_api_type=my_exec"]))

    def test_rejects_non_wiring_keys(self):
        # generate 换了生成器就换了用例本身，那不是接线改写，是重新设计。
        with self.assertRaises(ValueError) as caught:
            rewire.parse_set(["generate=other"])
        self.assertIn("generate", str(caught.exception))

    def test_rejects_missing_value(self):
        with self.assertRaises(ValueError):
            rewire.parse_set(["aclnn_api_type"])


class DiffCasesTest(unittest.TestCase):
    def test_wiring_only_change_is_clean(self):
        old = [case(0), case(1)]
        new = [case(0, aclnn_api_type="my_exec"),
               case(1, aclnn_api_type="my_exec")]
        self.assertEqual([], rewire.diff_cases(old, new))

    def test_changed_dtype_is_reported(self):
        problems = rewire.diff_cases([case(0)], [case(0, dtype="fp16")])
        self.assertEqual(1, len(problems))
        self.assertIn("0", problems[0])

    def test_changed_case_count_is_reported(self):
        problems = rewire.diff_cases([case(0), case(1)], [case(0)])
        self.assertTrue(problems)
        self.assertIn("条数", problems[0])

    def test_changed_case_ids_are_reported(self):
        problems = rewire.diff_cases([case(0)], [case(9)])
        self.assertTrue(problems)

    def test_strip_wiring_does_not_mutate_the_input(self):
        original = case(0, aclnn_api_type="my_exec")
        rewire.strip_wiring(original)
        self.assertIn("aclnn_api_type", original)


class CliTest(unittest.TestCase):
    def test_non_wiring_key_exits_three(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-f", "/nope.yaml",
             "-j", "/nope.json", "--atk-cli", "/nope",
             "--set", "generate=x", "-o", "/tmp/x.json"],
            capture_output=True, text=True)
        self.assertEqual(3, result.returncode)
        self.assertIn("generate", result.stderr)

    def test_missing_yaml_exits_three(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-f", "/nope.yaml",
             "-j", "/nope.json", "--atk-cli", "/nope",
             "--set", "aclnn_api_type=x", "-o", "/tmp/x.json"],
            capture_output=True, text=True)
        self.assertEqual(3, result.returncode)


class DisclosureTest(unittest.TestCase):
    def test_skill_no_longer_disclaims_a_missing_script(self):
        # Plan B 写下这句话时脚本还不存在，附了免责说明。现在兑现了，
        # 免责说明留着就是假信息。
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("rewire_adapter.py", text)
        self.assertNotIn("脚本未产出前", text)


if __name__ == "__main__":
    unittest.main()
