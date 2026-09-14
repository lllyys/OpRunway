#!/usr/bin/env python3
"""性能采集期间的现场级排他锁。

**管的是本现场自己起的第二个 NPU 任务**，与 `probe_env.device_busy` 分工不同：
那个查设备上有没有**别人**的进程，靠 `own_pids()` 把自己的进程树排除掉；一个
验收现场里前后两条 Bash 命令是两棵进程树，谁也不是谁的后代，于是两轮性能采集
在同一张卡上重叠跑，两边都查不出问题。实测代价：SpMM 那轮自带件 50 条
（25 分钟）与判据表 8 场景重叠在卡 0 上，中间还并发了十几次最小复现，
采出来的倍率互相污染，而退出码全是 0。

两种用法。量具自己持锁走函数：

    import perf_lock
    with perf_lock.hold(Path("stage"), "自带件 50 条", devices):
        ...

另起的量测件不必 import 任何东西，用命令包住它：

    python3 <skill>/scripts/perf_lock.py --stage stage --who "判据表 8 场景" -- \\
        python3 my_harness.py --cases ... -o ...

拿不到锁退 3 并打印谁在持有、从什么时候起，**不排队等**——性能轮一跑几十分钟，
排队等于把这条命令挂死在终端里。持锁进程已经不在了（被 Ctrl-C 或杀掉）时，
陈锁自动抢过来，不要求人去手工删。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LOCK_NAME = "perf.lock"
BUSY = 3


def _alive(pid):
    """持锁进程还在不在。签 0 不发信号，只问内核这个 pid 认不认。

    **EPERM 算「在」。** 机器公用，持锁的可能是别人跑的一轮，那时 `os.kill`
    报的是没权限而不是查无此进程；把它当成不在，锁就会被抢走，闸等于没有。
    """
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _holder_line(info):
    who = info.get("who") or "(没记跑的是什么)"
    return (f"pid {info.get('pid')} 从 {info.get('started_at')} 起在跑「{who}」"
            f"，卡 {info.get('devices') or '未记'}")


def acquire(stage_dir, who, devices=(), path=None):
    """拿到锁返回锁文件路径，没拿到返回 `None` 并把持有者打到 stderr。"""
    path = Path(path) if path else Path(stage_dir) / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "pid": os.getpid(),
        "who": who,
        "devices": [str(d) for d in devices],
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }, ensure_ascii=False)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        info = _read(path)
        if _alive(info.get("pid")):
            print(f"性能采集已在跑：{_holder_line(info)}。"
                  f"\n性能轮期间不并发别的 NPU 任务——同一张卡上重叠采样，两边的数"
                  f"都不可比。等它跑完再来，或者换一个现场。"
                  f"\n锁文件：{path}", file=sys.stderr)
            return None
        print(f"接手陈锁（{_holder_line(info)}，进程已不在）。", file=sys.stderr)
        try:
            path.unlink()
        except OSError:
            pass
        return acquire(stage_dir, who, devices, path)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    return path


def release(path):
    """只删自己写的那把锁，别人的不动。"""
    if path is None:
        return
    path = Path(path)
    if _read(path).get("pid") == os.getpid():
        with contextlib.suppress(OSError):
            path.unlink()


@contextlib.contextmanager
def hold(stage_dir, who, devices=()):
    """拿不到锁时抛 `Busy`，调用方按退出码 3 处理。"""
    path = acquire(stage_dir, who, devices)
    if path is None:
        raise Busy(who)
    try:
        yield path
    finally:
        release(path)


class Busy(RuntimeError):
    """有别的性能采集在跑。"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", default="stage", help="现场的 stage 目录")
    ap.add_argument("--who", required=True, help="这一轮跑的是什么，进锁文件与报错")
    ap.add_argument("--devices", default="", help="占哪几张卡，逗号分隔，只进锁文件")
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="`--` 之后是持锁要跑的命令")
    ns = ap.parse_args()
    cmd = ns.cmd[1:] if ns.cmd and ns.cmd[0] == "--" else ns.cmd
    if not cmd:
        print("`--` 之后要给一条命令。", file=sys.stderr)
        return BUSY
    devices = [d for d in ns.devices.split(",") if d.strip()]
    path = acquire(ns.stage, ns.who, devices)
    if path is None:
        return BUSY
    try:
        return subprocess.run(cmd, check=False).returncode
    finally:
        release(path)


if __name__ == "__main__":
    sys.exit(main())
