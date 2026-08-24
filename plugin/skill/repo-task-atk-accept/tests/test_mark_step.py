"""mark_step.py：耗时归因表必须能指出该优化哪一步。

实测一轮验收里约束确认 46 分钟、用例生成 11 分钟，当时这个结论是人工翻日志刨的。
这里锁住的是「刨出来的那张表」本身正确：相邻两笔的间隔归给前一步，
占比之和为 100%，中文列在终端里对得齐。
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _paths import SKILL_ROOT

sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from mark_step import (  # noqa: E402
    display_width, durations, human, read_marks, summarize)

SCRIPT = SKILL_ROOT / "scripts" / "mark_step.py"
BASE = 1_000_000.0
MARKS = [
    {"step": "0", "name": "受理", "at": BASE},
    {"step": "1", "name": "确认约束", "at": BASE + 130},
    {"step": "3", "name": "设计用例与插件", "at": BASE + 2893},
]


class TestDurations(unittest.TestCase):
    def test_gap_belongs_to_previous_step(self):
        rows = durations(MARKS, now=BASE + 3553)
        self.assertEqual([row["seconds"] for row in rows], [130, 2763, 660])

    def test_last_step_closes_at_now(self):
        rows = durations(MARKS[:1], now=BASE + 42)
        self.assertEqual(rows[0]["seconds"], 42)

    def test_clock_skew_does_not_produce_negative(self):
        skewed = [{"step": "0", "name": "受理", "at": BASE},
                  {"step": "1", "name": "确认约束", "at": BASE - 10}]
        rows = durations(skewed, now=BASE + 1)
        self.assertTrue(all(row["seconds"] >= 0 for row in rows))


class TestFormatting(unittest.TestCase):
    def test_hours_shown_once_past_an_hour(self):
        self.assertEqual(human(3600 + 19 * 60), "1h19m")
        self.assertEqual(human(130), "2m10s")

    def test_cjk_counted_as_two_columns(self):
        self.assertEqual(display_width("受理"), 4)
        self.assertEqual(display_width("0 受理"), 6)

    def test_table_columns_line_up(self):
        lines = summarize(MARKS, now=BASE + 3553)
        # 中文名长短不一，用 len 补空格会排歪。耗时是右对齐的，
        # 所以对齐的是它的**结束列**，2m10s 和 46m03s 的起始列本就差一格。
        ends = set()
        for line in lines:
            found = re.search(r"\d+[hm]\d+[ms]", line)
            self.assertIsNotNone(found, line)
            ends.add(display_width(line[:found.end()]))
        self.assertEqual(len(ends), 1, lines)

    def test_shares_sum_to_one_hundred(self):
        rows = durations(MARKS, now=BASE + 3553)
        total = sum(row["seconds"] for row in rows)
        shares = [row["seconds"] / total * 100 for row in rows]
        self.assertAlmostEqual(sum(shares), 100.0)

    def test_slowest_step_is_visible_in_output(self):
        lines = summarize(MARKS, now=BASE + 3553)
        slow = [line for line in lines if "确认约束" in line][0]
        self.assertIn("46m03s", slow)
        self.assertIn("78%", slow)


class TestCli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=30)

    def test_mark_then_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            timeline = Path(temp_dir) / "evidence" / "timeline.jsonl"
            first = self.run_cli("-o", str(timeline), "0", "受理")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("→ 第 0 步 受理", first.stdout)

            second = self.run_cli("-o", str(timeline), "1", "确认约束")
            self.assertEqual(second.returncode, 0, second.stderr)
            # 记下一步时顺带报出上一步用了多久，用户不必等到收尾
            self.assertIn("第 0 步 受理 用时", second.stdout)

            self.assertEqual(len(read_marks(timeline)), 2)
            table = self.run_cli("-o", str(timeline), "--summary")
            self.assertEqual(table.returncode, 0, table.stderr)
            self.assertIn("耗时归因", table.stdout)
            self.assertIn("合计", table.stdout)

    def test_summary_without_timeline_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            done = self.run_cli(
                "-o", str(Path(temp_dir) / "none.jsonl"), "--summary")
            self.assertEqual(done.returncode, 2)

    def test_missing_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            done = self.run_cli("-o", str(Path(temp_dir) / "t.jsonl"), "3")
            self.assertEqual(done.returncode, 2)

    def test_marking_a_stage_prints_its_card(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            timeline = Path(tmp) / "timeline.jsonl"
            result = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "mark_step.py"),
                 "-o", str(timeline), "2", "用例生成"],
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        # 卡在阶段入口再出现一次，才叫「在合适的时机知道要领」。
        self.assertIn("S2 用例生成", result.stdout)
        self.assertIn("出口门禁：", result.stdout)
        self.assertIn("<op>_decl.json", result.stdout)

    def test_marking_a_non_stage_step_prints_no_card(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            timeline = Path(tmp) / "timeline.jsonl"
            result = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "mark_step.py"),
                 "-o", str(timeline), "9", "临时打点"],
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("出口门禁：", result.stdout)


if __name__ == "__main__":
    unittest.main()
