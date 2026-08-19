"""共享事实的两份副本必须一致。

skill 部署到别的机器上就是一个普通目录，必须自包含，所以两个 skill
各留一份副本而不是软链。副本会漂，用 sha256 拓红。
"""

import hashlib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MINE = REPO_ROOT / "skill" / "repo-task-doc-write" / "references"
THEIRS = REPO_ROOT / "skill" / "repo-task-atk-test" / "references"

SHARED = ("experimental_standard.md", "random-operator-signals.json")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SharedFactsSyncTest(unittest.TestCase):
    def test_shared_facts_match(self):
        drifted = []
        for name in SHARED:
            mine, theirs = MINE / name, THEIRS / name
            self.assertTrue(mine.is_file(), f"{name} 未拷贝到撰写 skill")
            if digest(mine) != digest(theirs):
                drifted.append(name)
        self.assertEqual([], drifted,
                         "共享事实漂了，把权威那份拷过去，别就地改")


if __name__ == "__main__":
    unittest.main()
