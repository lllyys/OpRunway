import os
import shutil
import tempfile
import unittest

import check_acceptance_entrypoints as C


GOOD = """
三条真机通路：new_example、aclnn_py、cpp_extension。
SOURCE_ACQUIRED → HEAD_VERIFIED → BUILD_VERIFIED → WORKFLOW_STARTED。
远端入口使用 set -Eeuo pipefail。
历史 Median 60/60 PASS 只证明旧 caseset，不得沿用。
"""


class AcceptanceEntrypointGateTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        for rel in C.ENTRYPOINTS:
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(GOOD)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as f:
            f.write(text)

    def test_current_entrypoints_pass(self):
        self.assertEqual(C.collect(self.root), [])

    def test_two_paths_regression_fails(self):
        self.write("commands/op-acceptance.md", "只有两条真机通路")
        self.assertTrue(any("两条真机通路" in x for x in C.collect(self.root)))

    def test_active_old_pass_claim_fails(self):
        self.write("agents/op-acceptance.md", "Median 最新精度 60/60 PASS")
        self.assertTrue(any("旧 Median PASS" in x for x in C.collect(self.root)))

    def test_explicit_historical_old_pass_is_allowed(self):
        self.write("agents/op-acceptance.md", "历史 Median 60/60 PASS 已失效，不得沿用。")
        self.assertEqual(C.collect(self.root), [])

    def test_missing_source_gate_token_fails(self):
        self.write("agents/acc-verify-rootcause.md", GOOD.replace("HEAD_VERIFIED", "HEAD_OK"))
        self.assertTrue(any("HEAD_VERIFIED" in x for x in C.collect(self.root)))

    def test_missing_file_fails_closed(self):
        os.remove(os.path.join(self.root, "skills", "acc-runner", "SKILL.md"))
        self.assertTrue(any("读取失败" in x for x in C.collect(self.root)))


if __name__ == "__main__":
    unittest.main()
