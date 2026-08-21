"""物化命令跑挂时，freeze_inputs 必须把子进程自己的报错说出来。

真机事故（roll，2026-08-16）：pypto 环境的 atk 缺 pytz，
`atk node -b cpu task …` 一调就在 click 里抛 ModuleNotFoundError，
任务「0 秒正常结束」、产物为空。freeze_inputs 只报「没找到 input.bin」，
并把 agent 指向 --atk-output——方向完全错，来回六次调用才手工复现出真因。

退出码和 stderr 当时就在手上，丢掉它们等于把根因藏起来。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
FREEZE = SKILL_ROOT / "scripts" / "freeze_inputs.py"

FAKE_CLI = """#!/bin/sh
echo "Traceback (most recent call last):" >&2
echo "  File \\"click/core.py\\", line 1353, in invoke" >&2
echo "ModuleNotFoundError: No module named 'pytz'" >&2
exit 1
"""


class MaterializeFailureTest(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.case_json = self.work / "cases.json"
        self.case_json.write_text(
            json.dumps([{"id": 0, "name": "torch.roll", "inputs": []}]),
            encoding="utf-8")
        self.cli = self.work / "atk"
        self.cli.write_text(FAKE_CLI, encoding="utf-8")
        self.cli.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def _run(self):
        return subprocess.run(
            [sys.executable, str(FREEZE),
             "-j", str(self.case_json), "--atk-cli", str(self.cli),
             "-d", str(self.work / "frozen"),
             "--atk-output", str(self.work / "atk_output"),
             "-o", str(self.work / "frozen_inputs.json")],
            capture_output=True, text=True, timeout=300,
            cwd=str(self.work), env={**os.environ, "PYTHONPATH": str(
                SKILL_ROOT / "scripts")})

    def test_child_error_is_surfaced_not_swallowed(self):
        result = self._run()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("pytz", result.stderr)
        self.assertIn("退出码 1", result.stderr)

    def test_wrong_hint_is_not_used_when_the_command_failed(self):
        # 命令根本没跑成时，把 agent 指向 --atk-output 只会浪费一轮排查。
        result = self._run()
        self.assertNotIn("--save_data input:bin（缺了它跑完即删）", result.stderr)


class FrozenDirOwnershipTest(unittest.TestCase):
    """一个冻结目录只归一份用例 JSON。

    真机事故（roll，2026-08-17）：两个接口分面先后 `freeze_inputs.py -d frozen`，
    第二次把第一次的冻结输入整目录 rmtree 掉。用例号两边都从 0 自增，重叠的
    号段读到的是另一个分面的输入，超出的号段报 FileNotFoundError——主分面
    88 条里 33 条挂、55 条拿错输入，全量精度整轮作废，而现象只是「用例数不对」。
    """

    def setUp(self):
        sys.path.insert(0, str(SKILL_ROOT / "scripts"))
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.frozen = os.path.join(self.tmp, "frozen")
        os.makedirs(self.frozen)

    def _claim(self, case_json):
        import freeze_inputs
        return freeze_inputs.claim_frozen_dir(self.frozen, case_json)

    def test_second_facet_cannot_wipe_the_first(self):
        import freeze_inputs
        freeze_inputs.write_frozen_owner(self.frozen, os.path.join(self.tmp, "a.json"))
        with self.assertRaises(SystemExit) as caught:
            self._claim(os.path.join(self.tmp, "b.json"))
        self.assertEqual(2, caught.exception.code)

    def test_message_names_both_facets_and_the_way_out(self):
        import freeze_inputs, io, contextlib
        freeze_inputs.write_frozen_owner(self.frozen, os.path.join(self.tmp, "a.json"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self._claim(os.path.join(self.tmp, "b.json"))
        text = err.getvalue()
        self.assertIn("a.json", text)
        self.assertIn("b.json", text)
        self.assertIn("各自的冻结目录", text)

    def test_rerunning_the_same_facet_is_allowed(self):
        import freeze_inputs
        same = os.path.join(self.tmp, "a.json")
        freeze_inputs.write_frozen_owner(self.frozen, same)
        self.assertIsNone(self._claim(same))

    def test_directory_without_an_owner_is_left_alone(self):
        # 上一轮留下的目录、或手工建的空目录：认不出归属就放行，不平白拦人。
        self.assertIsNone(self._claim(os.path.join(self.tmp, "a.json")))

    def test_help_says_the_directory_gets_wiped(self):
        result = subprocess.run(
            [sys.executable, str(FREEZE), "--help"],
            capture_output=True, text=True)
        self.assertIn("每个接口分面必须用各自的目录", result.stdout)


class GoldenCompatibilityEntryTest(unittest.TestCase):
    def test_old_golden_entry_points_at_the_new_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            case_json = Path(tmp) / "cases.json"
            case_json.write_text(
                json.dumps([{"id": 0, "name": "torch.roll", "inputs": []}]),
                encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(FREEZE), "--golden",
                 "-j", str(case_json), "--atk-cli", "/unused/atk"],
                capture_output=True, text=True, timeout=300,
                cwd=tmp, env={**os.environ, "PYTHONPATH": str(
                    SKILL_ROOT / "scripts")})
        self.assertEqual(3, result.returncode)
        self.assertEqual(
            "golden 冻结已移至 scripts/freeze_golden.py，参数相同",
            result.stderr.strip())


if __name__ == "__main__":
    unittest.main()
