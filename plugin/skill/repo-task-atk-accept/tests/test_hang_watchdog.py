"""run_atk_task.py 的挂死判定回归测试。

真实事故：ATK 建任务失败或 celery 在任务体外崩溃时，进程不退出，
进度条停在 0/N 直到超时，看起来只是「算子跑得久」，方向就此排错。

这里用假的 ATK 进程复现三种形态：两种必须被判定挂死并杀掉，
一种正常结束的必须原样放行——误杀比不检测更糟。
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _paths import SKILL_ROOT

RUNNER = SKILL_ROOT / "scripts" / "run_atk_task.py"

# 建任务全失败：日志有定论，但进程永远不结束
FAKE_CREATE_FAIL = """
import sys, time
print("create_tasks start!!!, case_config_name:torch.demo, all_cases_num: 2", flush=True)
print("create_tasks fail, case_id: 0, error: not other task, please check node config", flush=True)
print("create_tasks fail, case_id: 1, error: not other task, please check node config", flush=True)
print("create_tasks over!!!, case_config_name:torch.demo, all_cases_num: 2", flush=True)
while True:
    print("ATK任务进度:   0%|          | 0/2 [00:01<?, ?it/s]", end="\\r", flush=True)
    time.sleep(0.2)
"""

# celery 在任务体之外崩溃：结果永不发布，回调不触发
FAKE_OUTSIDE_BODY = """
import time
print("create_tasks start!!!, case_config_name:torch.demo, all_cases_num: 2", flush=True)
print("create_tasks over!!!, case_config_name:torch.demo, all_cases_num: 2", flush=True)
print("RuntimeWarning: Exception raised outside body: IndexError('list index out of range')", flush=True)
while True:
    print("ATK任务进度:   0%|          | 0/2 [00:01<?, ?it/s]", end="\\r", flush=True)
    time.sleep(0.2)
"""

# 正常跑完：必须放行，退出码原样返回
FAKE_HEALTHY = """
print("create_tasks start!!!, case_config_name:torch.demo, all_cases_num: 2", flush=True)
print("create_tasks over!!!, case_config_name:torch.demo, all_cases_num: 2", flush=True)
print("ATK任务进度: 100%|##########| 2/2 [00:02<00:00,  1it/s]", flush=True)
print("Total Task: 2, success 2, failed 0", flush=True)
"""

# 用例级失败但任务正常结束：不是挂死，必须放行
FAKE_CASE_FAILURE = """
print("create_tasks start!!!, case_config_name:torch.demo, all_cases_num: 2", flush=True)
print("create_tasks over!!!, case_config_name:torch.demo, all_cases_num: 2", flush=True)
print("case 0 run opp failed: 标杆输出为空，请检查标杆是否运行失败或者没有输出", flush=True)
print("Total Task: 2, success 0, failed 2", flush=True)
"""


# 慢速推进：进度确实在走，用户必须能看到它在走
FAKE_SLOW_PROGRESS = """
import time
print("create_tasks start!!!, case_config_name:torch.demo, all_cases_num: 4", flush=True)
print("create_tasks over!!!, case_config_name:torch.demo, all_cases_num: 4", flush=True)
for done in range(1, 5):
    time.sleep(0.6)
    print(f"ATK任务进度:  {done*25}%|##  | {done}/4 [00:01<00:03,  1it/s]", flush=True)
print("Total Task: 4, success 4, failed 0", flush=True)
"""


class HangWatchdogTest(unittest.TestCase):
    def run_watchdog(self, fake_source, stall_timeout=3, extra=()):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake = root / "fake_atk.py"
            fake.write_text(fake_source, encoding="utf-8")
            log = root / "run.log"
            done = subprocess.run(
                [sys.executable, str(RUNNER), "-o", str(log),
                 "--stall-timeout", str(stall_timeout), "--poll", "1",
                 *extra, "--", sys.executable, str(fake)],
                capture_output=True, text=True, timeout=90)
            return done

    def test_create_tasks_all_failed_is_killed(self):
        done = self.run_watchdog(FAKE_CREATE_FAIL)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("建任务失败", done.stderr)

    def test_exception_outside_body_is_killed(self):
        done = self.run_watchdog(FAKE_OUTSIDE_BODY)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("任务体之外", done.stderr)

    def test_healthy_run_is_not_killed(self):
        done = self.run_watchdog(FAKE_HEALTHY)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("正常结束", done.stdout)

    def test_case_level_failure_is_not_killed(self):
        # 用例失败进汇总表，任务本身是正常结束的，误杀会丢掉结果
        done = self.run_watchdog(FAKE_CASE_FAILURE)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class ProgressReportTest(unittest.TestCase):
    """进度本来只喂给挂死判定，一行都不外露，几十分钟的跑测在终端里全黑。"""

    def test_progress_is_printed_while_running(self):
        done = HangWatchdogTest().run_watchdog(
            FAKE_SLOW_PROGRESS, extra=("--progress-every", "0"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        # 中间某几拍可能落在轮询间隔之外，但「开始走」和「走完」必须都看得到
        self.assertIn("进度 1/4", done.stdout)
        self.assertIn("进度 4/4", done.stdout)
        self.assertIn("预计剩余", done.stdout)

    def test_elapsed_reported_on_normal_exit(self):
        done = HangWatchdogTest().run_watchdog(FAKE_HEALTHY)
        self.assertIn("用时", done.stdout)

    def test_throttle_suppresses_intermediate_lines(self):
        # 节流窗口内不刷屏，但收官的 4/4 必须照打
        done = HangWatchdogTest().run_watchdog(
            FAKE_SLOW_PROGRESS, extra=("--progress-every", "3600"))
        lines = [line for line in done.stdout.splitlines() if "进度 " in line]
        self.assertIn("进度 4/4", done.stdout)
        self.assertLessEqual(len(lines), 2, done.stdout)


if __name__ == "__main__":
    unittest.main()
