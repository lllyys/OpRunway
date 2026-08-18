import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from select_perf_cases import allocate, is_excluded


class PerfSelectionTest(unittest.TestCase):
    def test_grid_count_can_exceed_target(self):
        quota = allocate(["a", "b", "c"], 2)
        self.assertEqual(sum(quota.values()), 2)
        self.assertEqual(sum(value > 0 for value in quota.values()), 2)

    def test_target_is_distributed_exactly(self):
        quota = allocate(["a", "b", "c"], 50)
        self.assertEqual(sum(quota.values()), 50)
        self.assertLessEqual(max(quota.values()) - min(quota.values()), 1)

    def test_invalid_accuracy_case_is_not_selected(self):
        self.assertEqual(
            "invalid_case_data",
            is_excluded({"id": "7"}, {"7": "invalid_case_data"}),
        )


if __name__ == "__main__":
    unittest.main()
