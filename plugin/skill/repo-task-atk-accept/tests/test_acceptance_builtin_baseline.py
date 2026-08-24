"""内置实现当真值这条路的知识与门禁。

这些事实都是从 ATK 源码读出来的，不是猜的；钉在这里是为了不被后续精简删掉。
真机上少任何一条，agent 都会现场翻 ATK 源码重推一遍。
"""


import json
import unittest

from _paths import SKILL_ROOT


class SpineRegistrationTest(unittest.TestCase):
    """新产物必须进骨架，否则完备性不可判定（CLAUDE.md §3.1）。"""

    def setUp(self):
        self.data = json.loads(
            (SKILL_ROOT / "references" / "artifact-contracts.json")
            .read_text(encoding="utf-8"))["artifacts"]

    def test_four_builtin_artifacts_are_registered(self):
        for name, producer, stage in (
                ("evidence/opp_library_<side>.json", "resolve_opp_library.py", "S3"),
                ("evidence/golden_builtin/", "capture_reference.py", "S3"),
                ("evidence/golden_provenance.json", "capture_reference.py", "S3"),
                ("evidence/golden_source.json", "check_golden_source.py", "S4")):
            with self.subTest(artifact=name):
                self.assertIn(name, self.data)
                self.assertEqual(producer, self.data[name]["producer"])
                self.assertEqual(stage, self.data[name]["stage"])

    def test_they_are_conditional_on_the_baseline_kind(self):
        for name in ("evidence/opp_library_<side>.json", "evidence/golden_builtin/",
                     "evidence/golden_provenance.json", "evidence/golden_source.json"):
            with self.subTest(artifact=name):
                self.assertIn("cann_builtin", self.data[name]["condition"])

    def test_they_point_at_the_new_reference(self):
        for name in ("evidence/opp_library_<side>.json", "evidence/golden_builtin/",
                     "evidence/golden_provenance.json", "evidence/golden_source.json"):
            with self.subTest(artifact=name):
                self.assertTrue(
                    self.data[name]["spec"].startswith("references/builtin-baseline.md"))


if __name__ == "__main__":
    unittest.main()
