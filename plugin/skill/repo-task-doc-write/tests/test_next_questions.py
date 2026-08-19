"""追问调度：算出还缺哪些拍板项，按批次给出下一步。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "next_questions.py"
DECISIONS = SKILL_ROOT / "tests" / "fixtures" / "decisions_golden.json"


def run(decisions_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--decisions", str(decisions_path),
         "--json"], capture_output=True, text=True)
    return result.returncode, json.loads(result.stdout or "{}")


class NextQuestionsTest(unittest.TestCase):
    def test_complete_decisions_report_done(self):
        code, payload = run(DECISIONS)
        self.assertEqual(0, code)
        self.assertEqual([], payload["pending"])
        self.assertTrue(payload["done"])

    def test_empty_decisions_start_at_batch_one(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            handle.write("{}")
            path = handle.name
        code, payload = run(path)
        self.assertEqual(0, code)
        self.assertEqual(1, payload["next_batch"]["id"])
        self.assertEqual("前提", payload["next_batch"]["name"])
        self.assertFalse(payload["done"])

    def test_partial_decisions_skip_to_the_first_gap(self):
        full = json.loads(DECISIONS.read_text(encoding="utf-8"))
        for key in ("2.5.non_contiguous", "2.5.broadcast"):
            full.pop(key)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(full, handle, ensure_ascii=False)
            path = handle.name
        code, payload = run(path)
        self.assertEqual(6, payload["next_batch"]["id"])
        self.assertEqual(["2.5.non_contiguous", "2.5.broadcast"],
                         payload["next_batch"]["ask"])

    def test_payload_carries_failure_mode_for_each_question(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            handle.write("{}")
            path = handle.name
        _, payload = run(path)
        for item in payload["next_batch"]["detail"]:
            with self.subTest(item=item["key"]):
                self.assertTrue(item["failure"])
                self.assertIn("agent_may_propose", item)


if __name__ == "__main__":
    unittest.main()
