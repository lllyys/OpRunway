#!/usr/bin/env python3
"""阶段计时：每份 stage JSON 落盘时记这一步花了多久。

**没有它，一轮验收的耗时归不了因。** 实测 SpGeMM 一轮 22 分 29 秒，其中 400 秒
落在阶段之间——只有编译与精度记了耗时，其余阶段一个时间都不落，只能靠产物的
mtime 反推是哪一步在花时间。

计时从模块导入算起，即脚本进程起来那一刻，量的是这一个阶段的墙钟，**含 ATK 起停、
用例落盘、报告解析**，与 ATK 自报的执行耗时不是一回事。两个数都留着：差值就是
框架开销，实测精度轮 ATK 自报 146 秒、阶段墙钟 149 秒。
"""
from __future__ import annotations

import time
from datetime import datetime

_STARTED = time.time()
_STARTED_AT = datetime.now().astimezone().isoformat(timespec="seconds")


def stamp(record):
    """把开始时刻与阶段墙钟写进 stage 记录，原地改并返回它。

    记录里已经有 `elapsed_seconds` 时（精度轮记的是 ATK 自报的耗时）不覆盖它，
    阶段墙钟改记 `stage_elapsed_seconds`——覆盖会把两个不同口径的数混成一个，
    而框架开销正是靠这两个数的差值才看得见。
    """
    record["started_at"] = _STARTED_AT
    key = "stage_elapsed_seconds" if "elapsed_seconds" in record else "elapsed_seconds"
    record[key] = round(time.time() - _STARTED, 1)
    return record
