"""首轮真实拓扑的 golden 由 freeze_golden.py 独立冻结。"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _paths import SKILL_ROOT


FREEZE = SKILL_ROOT / "scripts" / "freeze_golden.py"


class GoldenPathConflictTest(unittest.TestCase):
    """CANN 内置真值与常规 golden 的产物形状不同，不能混用。"""

    def setUp(self):
        sys.path.insert(0, str(SKILL_ROOT / "scripts"))

    def test_builtin_baseline_is_refused_and_points_at_the_right_script(self):
        import freeze_golden
        problem = freeze_golden.golden_path_conflict("cann_builtin")
        self.assertIn("capture_reference.py", problem)
        self.assertIn("builtin-baseline.md", problem)

    def test_torch_baseline_is_unaffected(self):
        import freeze_golden
        self.assertIsNone(freeze_golden.golden_path_conflict("torch"))

    def test_absent_baseline_kind_behaves_like_torch(self):
        import freeze_golden
        self.assertIsNone(freeze_golden.golden_path_conflict(None))


class FreezeGoldenCliTest(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.work, True)
        self.case_json = self.work / "cases.json"
        self.case_json.write_text(
            json.dumps([{"id": 0, "name": "torch.roll", "inputs": []}]),
            encoding="utf-8")
        self.cli = self.work / "atk"
        self.stage = self.work / "golden"
        self._write_cli(
            "#!/bin/sh\n"
            "mkdir -p \"$FREEZE_GOLDEN_TEST_STAGE/atk_output/run/output/pyaclnn_0\"\n")

    def _write_cli(self, source):
        self.cli.write_text(source, encoding="utf-8")
        self.cli.chmod(0o755)

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, str(FREEZE),
             "-j", str(self.case_json), "--atk-cli", str(self.cli),
             "-d", str(self.stage), *map(str, extra)],
            capture_output=True, text=True, timeout=300,
            cwd=str(self.work), env={
                **os.environ,
                "FREEZE_GOLDEN_TEST_STAGE": str(self.stage),
                "PYTHONPATH": str(SKILL_ROOT / "scripts"),
            })

    def test_missing_device_is_an_input_error(self):
        result = self._run("--input-data", self.work / "inputs")
        self.assertEqual(3, result.returncode)
        self.assertIn("--device", result.stderr)

    def test_missing_input_data_is_an_input_error(self):
        result = self._run("--device", "0")
        self.assertEqual(3, result.returncode)
        self.assertIn("--input-data", result.stderr)

    def test_builtin_baseline_is_refused_with_the_existing_diagnosis(self):
        interface = self.work / "interface.json"
        interface.write_text(
            json.dumps({"baseline_kind": "cann_builtin"}), encoding="utf-8")
        result = self._run("--interface", interface)
        self.assertEqual(2, result.returncode)
        self.assertIn("baseline_kind 是 cann_builtin", result.stderr)
        self.assertIn("capture_reference.py", result.stderr)
        self.assertIn("builtin-baseline.md#两步跑测", result.stderr)

    def test_output_without_cpu_node_is_an_environment_error(self):
        result = self._run(
            "--device", "0", "--input-data", self.work / "inputs")
        self.assertEqual(3, result.returncode)
        self.assertIn("output 下没有 cpu 节点目录", result.stderr)

    def test_output_without_candidate_node_is_a_topology_error(self):
        self._write_cli(
            "#!/bin/sh\n"
            "mkdir -p \"$FREEZE_GOLDEN_TEST_STAGE/atk_output/run/output/cpu_0\"\n")
        result = self._run(
            "--device", "0", "--input-data", self.work / "inputs")
        self.assertEqual(2, result.returncode)
        self.assertIn("真实执行拓扑", result.stderr)
        self.assertIn("freeze_golden.py", result.stderr)

    def test_success_writes_a_case_bound_report(self):
        self._write_cli(
            "#!/bin/sh\n"
            "mkdir -p \"$FREEZE_GOLDEN_TEST_STAGE/atk_output/run/output/pyaclnn_0\"\n"
            "mkdir -p \"$FREEZE_GOLDEN_TEST_STAGE/atk_output/run/output/cpu_0/cases/0\"\n"
            "printf '[[{\"dtype\":\"float16\"}]]' > "
            "\"$FREEZE_GOLDEN_TEST_STAGE/atk_output/run/output/cpu_0/cases/0/"
            "output_info.json\"\n")
        report_path = self.work / "frozen_golden.json"
        result = self._run(
            "--device", "0", "--input-data", self.work / "inputs",
            "-o", report_path)
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        expected = hashlib.sha256(self.case_json.read_bytes()).hexdigest()
        self.assertEqual("golden", report["mode"])
        self.assertEqual(expected, report["case_json_sha256"])
        self.assertEqual("cpu_0", report["baseline_node"])
        self.assertEqual(["pyaclnn_0"], report["candidate_nodes"])
        self.assertEqual("float16", report["cases"]["0"]["dtype"])

    def test_help_succeeds(self):
        result = subprocess.run(
            [sys.executable, str(FREEZE), "--help"],
            capture_output=True, text=True, timeout=300)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--device", result.stdout)
        self.assertIn("--input-data", result.stdout)


if __name__ == "__main__":
    unittest.main()
