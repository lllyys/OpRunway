#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""proc.py —— harness 共用的子进程运行函数（构建段与执行段同一份实现）。

构建段（`build_dut.py`）与执行段（`exec_case.py`）都要起外部进程，两段要解决的
是同样三件事，所以实现只留一份：

| 事 | 做法 | 不这么做的后果 |
| --- | --- | --- |
| 后代进程隔离 | `start_new_session=True` 起独立进程组 | 超时只杀直接子进程，`build.sh` 拉起的 cmake 与编译器继续写构建目录 |
| 超时收尾 | 杀整个进程组（先 TERM 后 KILL）再 `wait` 回收 | 留下僵尸进程与仍在跑的编译器 |
| 日志完整 | stdout 与 stderr 直写文件再读回 | 超时时已输出的字节留在 `TimeoutExpired.stdout`（bytes），按 str 取就丢光 |

`run()` 的返回字段固定七项：`cmd`、`cwd`、`returncode`、`output`、`timed_out`、
`seconds`、`log_path`。超时时 `returncode` 记实际的信号退出码（被 SIGKILL 杀是
-9），归类由调用方按 `timed_out` 优先判——不靠 `returncode` 反推超时。
"""

import os
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path

# 杀组后等回收的宽限：先 SIGTERM 等这么久，还活着就 SIGKILL。
KILL_GRACE_SECONDS = 5


def _kill_group(proc):
    """杀掉整个进程组并回收。取不到组号（进程已退）时退回杀单个进程。"""
    # start_new_session makes the child's PID its process-group ID, even after exit.
    pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        proc.wait()
        return
    deadline = time.monotonic() + KILL_GRACE_SECONDS
    while time.monotonic() < deadline:
        proc.poll()
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def run(cmd, cwd=None, env=None, timeout=None, log_path=None):
    """起一个子进程跑到底或跑到超时，返回固定七项记录。

    `log_path` 给定时日志落那里（执行段的 `exec.log` 就是它）；不给则落临时文件，
    读回文本后删掉——两种情形下 `output` 都是全文，超时也不缺。
    """
    cmd = [str(c) for c in cmd]
    temp_log = None
    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        fd, name = tempfile.mkstemp(prefix="harness-proc-", suffix=".log")
        os.close(fd)
        temp_log = Path(name)
        log_path = temp_log

    t0 = time.time()
    timed_out = False
    with open(log_path, "wb") as fh:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd) if cwd else None, env=env,
            stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
            start_new_session=True)          # 进程组隔离：超时能连后代一起杀
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
        except BaseException:
            _kill_group(proc)
            raise

    output = log_path.read_text(encoding="utf-8", errors="replace")
    rec = {"cmd": " ".join(shlex.quote(c) for c in cmd),
           "cwd": str(cwd) if cwd else None,
           "returncode": proc.returncode, "output": output,
           "timed_out": timed_out, "seconds": round(time.time() - t0, 3),
           "log_path": str(log_path)}
    if temp_log is not None:
        temp_log.unlink(missing_ok=True)
        rec["log_path"] = None
    return rec
