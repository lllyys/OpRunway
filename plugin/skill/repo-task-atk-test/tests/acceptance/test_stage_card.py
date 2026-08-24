"""量具补送作战卡：只在该送的时候送,且送完就闭嘴。

卡从 SKILL.md 移走之后,送达就取决于 agent 记不记得打卡,而没有任何门禁
依赖 timeline.jsonl——忘了不会有任何东西红。这里把「记得」变成量具的事。

这条提示自己不能变成新的成本或新的故障源,所以四件事都要钉住:
补记一笔(否则同阶段每个量具重打一遍卡)、打到 stderr(stdout 有人读)、
不越界(不在工作目录里就别造文件)、绝不抛异常(提示不能让量具跑挂)。
"""


import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _paths import SCRIPTS

sys.path.insert(0, str(SCRIPTS))

import _contracts  # noqa: E402
import _stage_card  # noqa: E402


def run(script, work, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          cwd=work, capture_output=True, text=True, timeout=60)


def work_dir(stack):
    work = stack.enter_context(TemporaryDirectory())
    (Path(work) / "evidence").mkdir()
    return work


def timeline(work):
    path = Path(work) / "evidence" / "timeline.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]




class DoesNotOverstepTest(unittest.TestCase):
    def test_help_is_not_entering_a_stage(self):
        with TemporaryDirectory() as work:
            (Path(work) / "evidence").mkdir()
            result = run("verdict.py", work, "--help")
            self.assertEqual([], timeline(work))
            self.assertNotIn("产出物（缺一样门禁不过）", result.stdout)


if __name__ == "__main__":
    unittest.main()
