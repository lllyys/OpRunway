"""性能采集期间的现场级排他锁。

拦的是**本现场自己起的第二个 NPU 任务**——`probe_env.device_busy` 靠 `own_pids()`
把自己的进程树排掉，而一个现场里前后两条命令是两棵进程树，那道闸看不见这一类。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import perf_lock  # noqa: E402

DEAD_PID = 999999999          # 取不到的 pid，`os.kill(pid, 0)` 报 ESRCH
ALIVE_PID = 1                 # init 一定在，且不是本进程——release 不会误删


def write_lock(stage, pid, who="别的一轮"):
    stage.mkdir(parents=True, exist_ok=True)
    path = stage / perf_lock.LOCK_NAME
    path.write_text(json.dumps({"pid": pid, "who": who, "devices": ["0"],
                                "started_at": "2026-09-09T08:23:48+00:00"}),
                    encoding="utf-8")
    return path


def test_acquire_writes_holder(tmp_path):
    """拿到锁时锁文件记得下是谁、占哪几张卡。"""
    path = perf_lock.acquire(tmp_path / "stage", "自带件 50 条", ["0", "1"])
    assert path is not None
    info = json.loads(path.read_text(encoding="utf-8"))
    assert info["pid"] == os.getpid()
    assert info["who"] == "自带件 50 条"
    assert info["devices"] == ["0", "1"]


def test_second_acquire_refused_while_holder_alive(tmp_path, capsys):
    """持锁进程还活着时第二个采集拿不到锁，报错里带得出持有者。"""
    stage = tmp_path / "stage"
    write_lock(stage, ALIVE_PID, who="自带件 50 条")
    assert perf_lock.acquire(stage, "判据表 8 场景", ["0"]) is None
    err = capsys.readouterr().err
    assert "自带件 50 条" in err and str(ALIVE_PID) in err


def test_stale_lock_taken_over(tmp_path, capsys):
    """持锁进程已经不在时接手陈锁，不要求人去手工删。"""
    stage = tmp_path / "stage"
    write_lock(stage, DEAD_PID)
    path = perf_lock.acquire(stage, "判据表 8 场景", ["0"])
    assert path is not None
    assert json.loads(path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert "陈锁" in capsys.readouterr().err


def test_release_only_removes_own_lock(tmp_path):
    """别人的锁不动——释放时不能把正在跑的那一轮的锁删掉。"""
    stage = tmp_path / "stage"
    path = write_lock(stage, ALIVE_PID)
    perf_lock.release(path)
    assert path.exists()


def test_hold_releases_on_exception(tmp_path):
    """量测件中途崩了也要把锁还回去，否则下一轮永远拿不到。"""
    stage = tmp_path / "stage"
    with pytest.raises(ValueError):
        with perf_lock.hold(stage, "自带件 50 条", ["0"]):
            raise ValueError("量测件崩了")
    assert not (stage / perf_lock.LOCK_NAME).exists()


def test_hold_raises_busy_when_locked(tmp_path):
    """拿不到锁时抛 Busy，调用方据此退 3。"""
    stage = tmp_path / "stage"
    write_lock(stage, ALIVE_PID)
    with pytest.raises(perf_lock.Busy):
        with perf_lock.hold(stage, "判据表 8 场景", ["0"]):
            pass


def test_wrapper_runs_command_and_frees_lock(tmp_path):
    """命令包住的用法：跑完把锁还回去，退出码是子命令的。"""
    stage = tmp_path / "stage"
    got = subprocess.run(
        [sys.executable, str(SCRIPTS / "perf_lock.py"), "--stage", str(stage),
         "--who", "外部量测", "--devices", "0", "--",
         sys.executable, "-c", "print('measured')"],
        capture_output=True, text=True)
    assert got.returncode == 0
    assert "measured" in got.stdout
    assert not (stage / perf_lock.LOCK_NAME).exists()


def test_wrapper_refuses_when_locked(tmp_path):
    """有别的采集在跑时包住的命令一次都不执行，退 3。"""
    stage = tmp_path / "stage"
    write_lock(stage, ALIVE_PID)
    marker = tmp_path / "ran.txt"
    got = subprocess.run(
        [sys.executable, str(SCRIPTS / "perf_lock.py"), "--stage", str(stage),
         "--who", "判据表 8 场景", "--",
         sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"],
        capture_output=True, text=True)
    assert got.returncode == perf_lock.BUSY
    assert not marker.exists()


def test_wrapper_propagates_child_exit_code(tmp_path):
    """子命令的退出码原样带出来，不被锁吞掉。"""
    stage = tmp_path / "stage"
    got = subprocess.run(
        [sys.executable, str(SCRIPTS / "perf_lock.py"), "--stage", str(stage),
         "--who", "外部量测", "--", sys.executable, "-c", "raise SystemExit(4)"],
        capture_output=True, text=True)
    assert got.returncode == 4
