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

    def test_first_gauge_of_a_stage_delivers_that_stage_card(self):
        # 参数要能过 argparse：调用点在 parse_args 之后，
        # 文件存不存在由量具自己去报，与补卡无关。
        result = run("make_must_cover.py", self.work, "-d", "无.json",
                     "-o", "must_cover.json")
        self.assertIn(_contracts.render_card(self.data, "S2").strip(),
                      result.stderr)

    def test_card_goes_to_stderr_not_stdout(self):
        # `make_must_cover.py` 的 stdout 是要被读的(「生成 N 条组合 → 路径」)。
        result = run("make_must_cover.py", self.work, "-d", "无.json",
                     "-o", "must_cover.json")
        self.assertNotIn("作战卡", result.stdout)
        self.assertNotIn("产出物（缺一样门禁不过）", result.stdout)

    def test_it_records_the_mark_so_it_only_fires_once(self):
        # 不补记的话,同阶段每个量具都会重打一遍卡——那才是真的烧上下文。
        run("make_must_cover.py", self.work, "-d", "无.json",
            "-o", "must_cover.json")
        self.assertEqual(["2"], [m["step"] for m in timeline(self.work)])
        second = run("make_yaml.py", self.work, "-m", "无.json", "-o", "x.yaml")
        self.assertNotIn("产出物（缺一样门禁不过）", second.stderr)
        self.assertEqual(1, len(timeline(self.work)))

    def test_an_already_marked_stage_stays_silent(self):
        run("mark_step.py", self.work, "-o", "evidence/timeline.jsonl",
            "2", "用例生成")
        result = run("make_must_cover.py", self.work, "-d", "无.json",
                     "-o", "must_cover.json")
        self.assertNotIn("产出物（缺一样门禁不过）", result.stderr)



class DoesNotOverstepTest(unittest.TestCase):
    def test_bad_arguments_are_not_entering_a_stage(self):
        # 调用点在 parse_args 之后：参数写错时那次调用什么都没干，
        # 不该为它打一张卡、更不该把阶段标记成已进入。
        with TemporaryDirectory() as work:
            (Path(work) / "evidence").mkdir()
            result = run("check_coverage.py", work, "--must-cover", "无.json")
            self.assertNotIn("产出物（缺一样门禁不过）", result.stderr)
            self.assertEqual([], timeline(work))

    def test_outside_a_work_directory_it_creates_nothing(self):
        # evidence/ 不存在就说明这不是验收工作目录(跑测试、临时试脚本)。
        with TemporaryDirectory() as work:
            run("check_coverage.py", work, "-m", "无.json", "-j", "无.json")
            self.assertFalse((Path(work) / "evidence").exists())




class NeverBreaksTheGaugeTest(unittest.TestCase):
    """提示功能出任何问题都必须当没发生过。"""

    def test_broken_timeline_does_not_raise(self):
        with TemporaryDirectory() as work:
            (Path(work) / "evidence").mkdir()
            (Path(work) / "evidence" / "timeline.jsonl").write_text(
                "{不是 json\n", encoding="utf-8")
            _stage_card.announce(str(SCRIPTS / "make_must_cover.py"),
                                 str(Path(work) / "evidence" / "timeline.jsonl"))

    def test_any_failure_is_swallowed_not_just_bad_data(self):
        # 上一条只走到 JSONDecodeError，而它是 ValueError 的子类——
        # 把兜底收窄成 except ValueError 照样绿。真正要证的是「任意异常」：
        # 骨架读挂、渲染出错、磁盘只读，都不许把量具带下水。
        from unittest.mock import patch
        with TemporaryDirectory() as work:
            (Path(work) / "evidence").mkdir()
            path = Path(work) / "evidence" / "timeline.jsonl"
            with patch.object(_stage_card, "stage_of",
                              side_effect=RuntimeError("骨架读挂了")):
                _stage_card.announce(str(SCRIPTS / "make_must_cover.py"),
                                     str(path))
            self.assertFalse(path.exists())

    def test_unknown_script_name_is_silent(self):
        with TemporaryDirectory() as work:
            (Path(work) / "evidence").mkdir()
            path = Path(work) / "evidence" / "timeline.jsonl"
            _stage_card.announce("/nowhere/不存在的量具.py", str(path))
            self.assertFalse(path.exists())

    def test_exit_code_is_untouched(self):
        # 它不是门禁:不读产物、不判对错、不改退出码。
        with TemporaryDirectory() as work:
            (Path(work) / "evidence").mkdir()
            bare = run("check_coverage.py", work, "-m", "无.json", "-j", "无.json")
            marked = run("check_coverage.py", work, "-m", "无.json", "-j", "无.json")
            self.assertEqual(bare.returncode, marked.returncode)


if __name__ == "__main__":
    unittest.main()
