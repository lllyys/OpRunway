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




class DeliveryTest(unittest.TestCase):
    def setUp(self):
        from contextlib import ExitStack
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.work = work_dir(self.stack)
        self.data = _contracts.load()

    def test_it_never_back_fills_a_stage_the_run_has_passed(self):
        # probe_env.py 记在 S1,但 S3 装完包要带 --vendor-env 重跑一次。
        # 那时补一张 S1 的卡就是在错的阶段打错的卡。
        run("mark_step.py", self.work, "-o", "evidence/timeline.jsonl",
            "3", "编译安装部署")
        result = run("probe_env.py", self.work)
        self.assertNotIn(_contracts.render_card(self.data, "S1").strip(),
                         result.stderr)




class WiringTest(unittest.TestCase):
    def test_every_single_stage_gauge_is_wired(self):
        # 漏接一个,那个阶段就可能整段没有卡。反向核对而不是手工清点。
        for script in sorted({*(spec["producer"]
                                for spec in _contracts.load()["artifacts"].values()
                                if spec.get("producer")),
                              *(spec["script"] for spec in
                                _contracts.load()["gate_inventory"].values())}):
            with self.subTest(script=script):
                if _stage_card.stage_of(script) is None:
                    continue
                path = SCRIPTS / script
                if not path.is_file():
                    # 展开产物只带本侧 + shared；完整性由归属清单测试核对。
                    continue
                text = path.read_text(encoding="utf-8")
                self.assertIn("_stage_card.announce(__file__)", text)

    def test_cross_stage_gauges_are_not_guessed(self):
        # 跨阶段的量具不猜阶段:猜错就是在 S3 打出一张 S1 的卡。
        self.assertIsNone(_stage_card.stage_of("mark_step.py"))
        self.assertIsNone(_stage_card.stage_of("probe_env.py"))
        self.assertEqual("S2", _stage_card.stage_of("make_yaml.py"))




if __name__ == "__main__":
    unittest.main()
