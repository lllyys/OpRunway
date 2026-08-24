"""生成知识指向验收门禁的跨侧边界测试。"""

import unittest

from _paths import REFERENCES, require_nested_source


class FlashRunKnowledgeGapsTest(unittest.TestCase):
    def setUp(self):
        require_nested_source(self, "生成侧知识到验收侧 SoC 门禁的边界")

    def _text(self, name):
        return (REFERENCES / name).read_text(encoding="utf-8")

    def test_product_name_to_build_soc_is_declared_unmappable(self):
        # 缺它：每个算子都要为「A2 对哪个 soc」去 grep 装机目录，且必然写成待确认项。
        text = self._text("intake.md")
        self.assertIn("没有一张能机械核对的对照表", text)
        self.assertIn("它不是待确认项", text)
        self.assertIn("check_soc_binding.py", text)


if __name__ == "__main__":
    unittest.main()
