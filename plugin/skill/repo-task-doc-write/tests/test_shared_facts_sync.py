"""共享事实的两份副本必须一致。

skill 部署到别的机器上就是一个普通目录，必须自包含，所以撰写侧与生成侧
各留一份副本而不是软链。副本会漂，用 sha256 拓红。
解析器与签名抽取器也是共享事实，生成侧读任务书必须和撰写侧门禁用同一把尺子。
"""

import hashlib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_WRITE = REPO_ROOT / "skill" / "repo-task-doc-write"
CASE_GEN = REPO_ROOT / "skill" / "repo-task-case-gen"

SHARED = (
    ("references/experimental_standard.md", "references/experimental_standard.md"),
    ("references/random-operator-signals.json",
     "references/random-operator-signals.json"),
    ("scripts/_taskdoc_parser.py", "scripts/_taskdoc_parser.py"),
    ("scripts/_signature.py", "scripts/_signature.py"),
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SharedFactsSyncTest(unittest.TestCase):
    def test_shared_facts_match(self):
        drifted = []
        for doc_write_name, case_gen_name in SHARED:
            mine = DOC_WRITE / doc_write_name
            theirs = CASE_GEN / case_gen_name
            self.assertTrue(
                mine.is_file(), f"权威方 doc-write 缺 {doc_write_name}")
            self.assertTrue(
                theirs.is_file(),
                f"{case_gen_name} 未从权威方 doc-write 拷到生成侧")
            if digest(mine) != digest(theirs):
                drifted.append(f"{doc_write_name} ↔ {case_gen_name}")
        self.assertEqual([], drifted,
                         "共享事实漂了；doc-write 是权威方，"
                         "把权威那份拷过去，别就地改")


if __name__ == "__main__":
    unittest.main()
