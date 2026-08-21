"""运行期文档和脚本报错里不许出现自造的抽象词。

用户读到「签名只有一个合法出处」「候选符号」时，得先猜这两个字指什么，
猜错就照错的做。真正的术语指向一个存在的东西（`must_cover.json`、`aclnn_name`），
而这些词只是把一句大白话压成两个字，压掉的正是「指什么」。

替换的方向是**中文说清楚**，不是换成英文：
- ✗ 签名只有一个合法出处
- ✗ signature_source 必须合法          （换成英文一样看不懂）
- ✓ 签名只能从待验收算子工程目录里的头文件读（`aclnn_<op>.h`，算子对外的接口声明）

要指一个具体的东西（文件、字段、命令行参数）时，先用中文说它是什么，
再把名字放进括号。名字单独出现而不说明它是什么，和自造缩写一样难懂。

新增禁用词时必须同时给替换写法：只禁不给替换，下一个人只会换一个同样难懂的词。
"""

import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]

# 禁用词 → 该怎么写。替换项要能指向一个具体的东西或动作。
BANNED = {
    "出处": "照哪份文件写的 / 依据（谁要求的、写在哪）",
    "来源文件": "说清是哪份文件、里面的哪张表",
    "候选符号": "待验收算子的接口名",
    "符号": "接口名 / 函数名",
    "投影": "取某根轴的实际取值",
    "薄壳": "适配器",
}

# 这些搭配里含禁用词但指向明确，是行业通用写法，不算违规。
ALLOWED_CONTEXTS = {
    "符号": ("符号前缀", "无符号", "_symbol_prefix"),
}

SCANNED = [
    SKILL_ROOT / "SKILL.md",
    SKILL_ROOT / "case-gen" / "SKILL.md",
    SKILL_ROOT / "acceptance" / "SKILL.md",
    *sorted((SKILL_ROOT / "references").glob("*.md")),
]
SCANNED += sorted((SKILL_ROOT / "scripts").glob("*.py"))


# 术语表末尾那节就是这张禁用清单本身，它必须写出这些词才说得清要改什么。
RULE_SECTION = "## 怎么写才算说清了"


def offenders(word):
    hits = []
    for path in SCANNED:
        stop = False
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith(RULE_SECTION):
                stop = True
            if stop or word not in line:
                continue
            if any(ok in line for ok in ALLOWED_CONTEXTS.get(word, ())):
                continue
            hits.append(f"{path.name}:{number}: {line.strip()[:70]}")
    return hits


class PlainLanguageTest(unittest.TestCase):
    def test_no_banned_words_in_runtime_files(self):
        for word, replacement in BANNED.items():
            with self.subTest(word=word):
                self.assertEqual([], offenders(word),
                                 f"「{word}」改写成：{replacement}")

    def test_every_banned_word_carries_a_replacement(self):
        for word, replacement in BANNED.items():
            with self.subTest(word=word):
                self.assertTrue(replacement.strip(), "只禁不给替换等于没规则")

    def test_the_rule_is_written_down_where_authors_will_read_it(self):
        """禁用清单只在测试里存在的话，改文档的人不会知道它。"""
        text = (SKILL_ROOT / "references" / "glossary.md").read_text(encoding="utf-8")
        self.assertIn("怎么写", text)
        for word in BANNED:
            with self.subTest(word=word):
                self.assertIn(word, text, "禁用词要连同替换写法列进术语表")


if __name__ == "__main__":
    unittest.main()
