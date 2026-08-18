import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "save_failed_cases.py"


class SaveFailedCasesTest(unittest.TestCase):
    def test_saves_full_case_spec(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = root / "cases.json"
            output = root / "failed.json"
            cases.write_text(json.dumps({"cases": [
                {"id": 0, "inputs": [{"dtype": "fp16", "shape": [2]}]},
                {"id": 1, "inputs": [{"dtype": "fp32", "shape": [3]}]},
            ]}), encoding="utf-8")

            subprocess.run([
                sys.executable, str(SCRIPT), "-j", str(cases),
                "--ids", "[1]", "--stage", "smoke",
                "--log", "evidence/smoke.log", "-o", str(output),
            ], check=True, capture_output=True, text=True)

            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(["1"], value["ids"])
            self.assertEqual("fp32", value["cases"][0]["inputs"][0]["dtype"])
            self.assertEqual("evidence/smoke.log", value["evidence"])


if __name__ == "__main__":
    unittest.main()
