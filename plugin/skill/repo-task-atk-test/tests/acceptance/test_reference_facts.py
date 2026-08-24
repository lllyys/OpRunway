"""验收侧 reference 手写事实的防漂移测试。"""

import unittest

from _paths import REFERENCES


class FlashRunKnowledgeGapsTest(unittest.TestCase):
    def _text(self, name):
        return (REFERENCES / name).read_text(encoding="utf-8")

    def test_experimental_build_switch_is_documented(self):
        # 缺它：社区任务算子几乎都在 experimental/ 下，不带开关时报的是「算子不存在」。
        text = self._text("build-deploy.md")
        self.assertIn("--experimental", text)
        self.assertIn("ops_config.txt", text)


if __name__ == "__main__":
    unittest.main()
