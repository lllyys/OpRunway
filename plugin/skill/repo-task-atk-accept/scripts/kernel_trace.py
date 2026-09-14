#!/usr/bin/env python3
"""Profiler 产物里逐次调用的 Kernel 总耗时。

**这份判据只写一次。** 两个消费者要的是同一件事：`run_kit_perf.py` 与性能
量测件都拿整个列表按 `summarize()` 算中位数与 p90——那是任务书规定的两个读数，
判据在本模块「统计量」一节。各写一份的话，认列规则改一处漏一处。

三条实测约束，改之前先看：

| 约束 | 依据 |
| --- | --- |
| 产物只有 `kernel_details.csv`，没有自带件按的 `op_statistic.csv` | 文件名随 torch_npu 版本变，实测这台机器上后者不生成 |
| 列名一律探测，不写死 | 步号列写死时列名一变，`row.get` 每行返回 `None`，去重后除数是 1，倍率整体大出一个采样步数而退出码仍是 0 |
| 步号列可以整个不存在 | 一次调用只有一个 kernel 的路径 torch_npu 不写这一列（实测 SpMM 那轮 50 条里 25 条 fp32 如此，同轮 c64 多一个 `aclnnInplaceCopy` 就有） |

没有步号列时按**每个 kernel 名的第几次出现**分组：一次调用里每个 kernel 各跑
一次，同一个名字的第 j 次出现就属于第 j 次调用。两种分法在有步号列的产物上
逐条等价（真机 25 条差异 0）。各 kernel 名次数不齐时分不出组，返回空列表报缺，
**宁可报缺也不出错数**。
"""
from __future__ import annotations

import csv
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

DETAILS = "kernel_details.csv"


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _probe(fields, match):
    """按内容判据认列，不写死列名。"""
    return next((f for f in fields if match(_norm(f))), None)


def _num(value):
    if value in (None, "", "nan"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def newest(root, pattern=f"**/{DETAILS}"):
    """产物目录下最新的一份 `kernel_details.csv`，没有则 `None`。"""
    hits = list(Path(root).glob(pattern))
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime_ns)


def per_call_us(path):
    """逐次调用的 Kernel 总耗时，按调用序排列。认不出时返回空列表。"""
    path = Path(path)
    by_step, by_name = defaultdict(float), defaultdict(float)
    seen = Counter()
    names = Counter()
    try:
        handle = path.open(encoding="utf-8-sig", newline="")
    except OSError:
        return []
    with handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        col = _probe(fields, lambda n: n.startswith("duration"))
        step_col = _probe(fields, lambda n: n in ("stepid", "step", "iteration"))
        name_col = _probe(fields, lambda n: n == "name")
        if col is None:
            return []
        for row in reader:
            got = _num(row.get(col))
            if got is None:
                continue
            if step_col is not None:
                step = (row.get(step_col) or "").strip()
                if step:
                    by_step[step] += got
            if name_col is not None:
                name = (row.get(name_col) or "").strip()
                if name:
                    by_name[seen[name]] += got
                    seen[name] += 1
                    names[name] += 1
    if by_step:
        return [by_step[key] for key in sorted(by_step, key=_step_order)]
    if by_name and len(set(names.values())) == 1:
        return [by_name[i] for i in sorted(by_name)]
    return []


def _step_order(step):
    try:
        return (0, int(step), "")
    except ValueError:
        return (1, 0, step)


def active_tail(values, samples):
    """schedule 的前 warmup 步不进产物，多出来的尾步是调度边界，取末尾 samples 个。"""
    return values[-samples:] if samples and len(values) > samples else values


# ---------------------------------------------------------------- 统计量

# 任务书对 NPU 侧性能读数规定的是**两个统计量**，不是一个平均值：
#
#   「NPU侧每个case至少预热10次、正式采样30次，报告耗时中位数及90%分位耗时
#     （即约90%的正式采样耗时不高于该值）。每轮测试均须执行设备同步后计时。」
#     —— aclsparseSpGemm(A2A3) 任务书 4.198，SpMM(A2A3) 4.183，SpMM(950) 4.182
#        三份逐字相同
#
# 括号里那句就是 p90 的判据，按它取**最小的那个观测值**，使不高于它的采样占比
# 达到九成：`ordered[ceil(0.9 * n) - 1]`。**不做线性插值**——插出来的数不是任何
# 一次真实采样，而任务书要的是「采样耗时不高于该值」这件事本身。
#
# 这个口径两处消费者要认同一套（本模块与 assets/perf_harness*.py 骨架件），
# 骨架件是拷给算子改的、不能 import 本仓脚本，所以各写一份而由
# `tests/test_kernel_trace.py` 拿同一份语料把两处钉在一起。
def percentile_at_least(values, ratio):
    """不高于返回值的采样占比达到 `ratio` 的最小观测值。空列表返回 None。"""
    ordered = sorted(values)
    if not ordered:
        return None
    index = math.ceil(ratio * len(ordered)) - 1
    return ordered[min(max(index, 0), len(ordered) - 1)]


def summarize(values):
    """把逐次调用的耗时汇成任务书要求的读数。空列表返回 None。

    `under_test_us` 取中位数——**倍率判据落在这个数上**，与任务书「报告耗时
    中位数」对齐；`p90_us` 是任务书并列要求的第二个数，两个都要进报告。
    平均值不出：首次调用与偶发抖动是离群点，平均被它们拉走，而任务书要的
    两个统计量都不受单点影响。
    """
    ordered = sorted(values)
    if not ordered:
        return None
    return {"under_test_us": round(statistics.median(ordered), 3),
            "p90_us": round(percentile_at_least(ordered, 0.9), 3),
            "min_us": round(ordered[0], 3),
            "max_us": round(ordered[-1], 3),
            "samples": len(ordered)}
