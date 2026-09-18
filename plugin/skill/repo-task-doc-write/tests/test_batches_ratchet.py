"""问题批次的棘轮不变量。

批次是拿去问人的：每条 human 拍板要素恰好进一个批，非 human 的一个不进；
批的规模有上下界，批号从 1 连续递增。批次重排时这些不变量兜底。
"""

import json
import unittest
from collections import Counter
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"

# 单批要素数的上下界：空批没有存在意义，超过 6 条一次问不完。
MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 6


def spine():
    return json.loads(SPINE.read_text(encoding="utf-8"))


class BatchesRatchetTest(unittest.TestCase):
    def setUp(self):
        self.data = spine()
        self.elements = self.data["elements"]
        self.batches = self.data["question_batches"]

    def test_every_human_element_appears_in_exactly_one_batch(self):
        counts = Counter(key for batch in self.batches
                         for key in batch["elements"])
        human = {key for key, element in self.elements.items()
                 if element["decision_owner"] == "human"}
        missing = sorted(human - set(counts))
        self.assertEqual([], missing, "human 拍板要素没进任何批")
        duplicated = sorted(key for key in human if counts[key] > 1)
        self.assertEqual([], duplicated, "human 拍板要素出现在多个批")

    def test_non_human_elements_stay_out_of_batches(self):
        bad = sorted(f"批 {batch['id']}: {key}"
                     for batch in self.batches
                     for key in batch["elements"]
                     if key in self.elements
                     and self.elements[key]["decision_owner"] != "human")
        self.assertEqual([], bad, "非 human 拍板要素不该进批次")

    def test_batch_sizes_are_within_bounds(self):
        bad = [f"批 {batch['id']}: {len(batch['elements'])} 个要素"
               for batch in self.batches
               if not MIN_BATCH_SIZE <= len(batch["elements"]) <= MAX_BATCH_SIZE]
        self.assertEqual([], bad)

    def test_batched_keys_exist_in_elements(self):
        unknown = [f"批 {batch['id']}: {key}"
                   for batch in self.batches
                   for key in batch["elements"]
                   if key not in self.elements]
        self.assertEqual([], unknown)

    def test_batch_ids_start_at_one_and_increase_consecutively(self):
        ids = [batch["id"] for batch in self.batches]
        self.assertEqual(list(range(1, len(ids) + 1)), ids)


if __name__ == "__main__":
    unittest.main()
