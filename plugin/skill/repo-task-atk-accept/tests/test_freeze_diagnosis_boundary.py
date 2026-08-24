"""冻结输入兼容入口指向验收侧 golden 冻结量具。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _paths import SCRIPTS, SKILL_ROOT, require_nested_source

FREEZE = SCRIPTS / "freeze_inputs.py"


class GoldenCompatibilityEntryTest(unittest.TestCase):
    def setUp(self):
        require_nested_source(self, "生成侧兼容入口到验收侧 golden 量具的边界")

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
