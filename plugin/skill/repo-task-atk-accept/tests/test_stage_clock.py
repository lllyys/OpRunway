"""阶段计时：每份 stage JSON 落盘时记这一步花了多久。

**两个耗时口径不能互相覆盖。** 精度轮的 `elapsed_seconds` 是 ATK 自报的执行耗时，
阶段墙钟含 ATK 起停与报告解析，实测差了 3 秒；覆盖掉就再也看不见框架开销。
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import stage_clock  # noqa: E402


def test_stamps_both_fields():
    got = stage_clock.stamp({"mode": "smoke"})
    assert got["started_at"]
    assert isinstance(got["elapsed_seconds"], float)


def test_existing_elapsed_is_not_overwritten():
    """已有 elapsed_seconds 的记录改记 stage_elapsed_seconds，两个数都留着。"""
    got = stage_clock.stamp({"mode": "accuracy", "elapsed_seconds": 146.3})
    assert got["elapsed_seconds"] == 146.3
    assert isinstance(got["stage_elapsed_seconds"], float)


def test_stamps_in_place():
    """原地改并返回同一个对象，调用点可以直接写进 json.dump。"""
    record = {"mode": "tiling"}
    assert stage_clock.stamp(record) is record


def test_started_at_is_stable_across_calls():
    """同一次进程里两份产物记同一个开始时刻，阶段边界才对得齐。"""
    first = stage_clock.stamp({})["started_at"]
    assert stage_clock.stamp({})["started_at"] == first
