#!/usr/bin/env python3
"""跑任务方自带的性能脚本，产出跑测侧的交换格式。

任务书的性能口径超出 ATK 能表达的范围时（分阶段耗时、中位数与 p90、Profiler
的 Kernel 总耗时），倍率只能来自任务方自己的脚本。本量具做三件事：

| 做 | 不做 |
| --- | --- |
| 现查空闲卡，一卡一进程分片并行 | 写死卡号。别人占着的卡会让整片用例假失败 |
| 按 `--help` **探测**自带件脚本收哪些参数 | 假定参数叫什么。各家命名不统一，猜错就是静默跑串 |
| 从 Profiler 产物按任务书口径出中位数与 90%分位 | 改自带件，或 monkey-patch 它的函数名 |

**不改自带件一个字节。** 原件的 sha256 是「跑的是任务方交付的那一份」的凭据。

**读数口径以任务书为准，不以自带件汇总的那一列为准。** 自带件汇总的
`kernel_total_us` 是总耗时除以步数（一个平均值），任务书要的是「耗时中位数及
90%分位耗时」，两个统计量它一个都不是。所以逐次调用的读数一律从 Profiler 产物
自己算（`kernel_trace.summarize`）；只有认不出逐次读数时才退回那一列，这时
交换格式里打 `stat_kind=mean` 并逐条进报告，不冒充成任务书口径。

逐次读数的除数是**调用次数**：有步号列时取它的去重个数，没有这一列时数每个
kernel 名出现了几次。两者在有步号列的产物上逐条等价（实测 25 条差异 0）。

**性能采集期间独占本现场**（`perf_lock`）：这一轮在跑时，本现场再起一个性能
量测会退 3。另起的量测件用 `perf_lock.py --who … -- <命令>` 包住，走同一把锁。

退出码：0 全部跑出数；4 有条目没跑出数（交换格式仍然落盘）；3 脚本或用例读不出、
必需参数探测不到、有别的性能采集在跑；2 一条都没跑出来。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path
import kernel_trace  # noqa: E402 - 同目录量具，Profiler 产物的认列规则只写一份
import perf_lock   # noqa: E402 - 同目录量具，性能采集期间的现场级排他锁
import stage_clock  # noqa: E402 - 同目录量具，给 stage JSON 记阶段墙钟

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- 参数探测

def probe_flags(python, script):
    """读脚本自己的 --help，看它收哪些参数。探测，不猜。"""
    got = subprocess.run([python, str(script), "--help"],
                         capture_output=True, text=True, timeout=300)
    text = got.stdout + got.stderr
    return {flag for flag in re.findall(r"--[a-z][a-z0-9-]*", text)}


# ---------------------------------------------------------------- 空闲卡

def pick_devices(spec, want, limit=None):
    """显式给的卡号原样用；`auto` 现查空闲卡，并按上限截断。

    **上限是给公用机器留余地的**，判据与取值在 `probe_env.MAX_AUTO_DEVICES`。
    """
    if spec and spec != "auto":
        return [d.strip() for d in spec.split(",") if d.strip()]
    sys.path.insert(0, str(HERE))
    import probe_env
    ceiling = probe_env.MAX_AUTO_DEVICES if limit is None else int(limit)
    ask = want if ceiling <= 0 else min(want, ceiling)
    got = subprocess.run([sys.executable, str(HERE / "free_npu.py"),
                          "-n", str(ask)], capture_output=True, text=True)
    sys.stderr.write(got.stderr)
    line = got.stdout.strip().splitlines()
    devices = line[-1].split(",") if line and line[-1] else []
    devices, capped = probe_env.cap_devices(devices, ceiling)
    if capped:
        print(f"空闲卡 {capped}", file=sys.stderr)
    return devices


# ---------------------------------------------------------------- 分片跑

def shard(python, script, cases, total, devices, work, flags, extra):
    """一卡一个进程，按用例序号切片。缺 --start/--end 时退化成单卡串行。"""
    sliceable = "--start" in flags and "--end" in flags
    if not sliceable:
        devices = devices[:1]
        print("自带件脚本没有 --start/--end，分不了片，单卡串行。", file=sys.stderr)
    n = min(len(devices), total) or 1
    procs = []
    for i in range(n):
        dev = devices[i]
        out = work / f"dev{dev}"
        cmd = [python, str(script), "--case-file", str(cases), "--device", str(dev)]
        if sliceable:
            cmd += ["--start", str(total * i // n), "--end", str(total * (i + 1) // n)]
        if "--output-dir" in flags:
            cmd += ["--output-dir", str(out)]
        if "--trace-root" in flags:
            cmd += ["--trace-root", str(work / f"tr{dev}")]
        cmd += extra
        log = work / f"dev{dev}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        handle = log.open("w", encoding="utf-8")
        procs.append((dev, subprocess.Popen(cmd, stdout=handle, stderr=handle), handle))
        print(f"dev{dev} 起了", file=sys.stderr)
    codes = {}
    for dev, proc, handle in procs:
        codes[dev] = proc.wait()
        handle.close()
    return codes


# ---------------------------------------------------------------- 收产物

def merge(work):
    rows = []
    for path in sorted(work.rglob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows += list(csv.DictReader(handle))
    return rows


def _num(value):
    if value in (None, "", "nan"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def profiler_stats(work, case_id):
    """从 Profiler 产物按任务书口径算这条用例的读数，认不出返回 `None`。

    **这是主口径，不是回落。** 任务书要的是「报告耗时中位数及90%分位耗时」，
    而自带件汇总的那一列是总耗时除以步数——**一个平均值，两个统计量一个都不是**。
    平均值与中位数在同一批数上不可互换：离群的首次调用会把它拉走，而任务书
    正是拿分位数把这种离群排除掉的。

    逐次调用的 Kernel 总耗时由 `kernel_trace.per_call_us` 认（认列规则与量测件
    共用一份，判据与实测约束都在那个模块的 docstring 里），再交
    `kernel_trace.summarize` 出中位数与 p90。

    **除数是调用次数，不是行数。** 一次调用含多个 kernel（实测 spgemm 为 4 个,
    5 步共 20 行），除以行数得到的是平均单 kernel 耗时，与 GPU 基线的
    `kernel_total_us` 不同量纲。
    """
    path = kernel_trace.newest(work, f"tr*/{case_id}/**/{kernel_trace.DETAILS}")
    if path is None:
        return None
    return kernel_trace.summarize(kernel_trace.per_call_us(path))


# ---------------------------------------------------------------- 基线

def load_baseline(path, metric):
    if not path:
        return {}
    path = Path(path)
    if path.suffix == ".json":
        return {k: float(v) for k, v in
                json.loads(path.read_text(encoding="utf-8")).items()}
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            got = _num(row.get(metric))
            if got is not None:
                out[row["id"]] = got
    return out


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--script", required=True, help="自带件的性能脚本")
    ap.add_argument("--cases", required=True, help="自带件的性能用例文件")
    ap.add_argument("--baseline", help="标杆：NCU 的 csv，或 id 到 us 的 json")
    ap.add_argument("--metric", default="kernel_total_us",
                    help="取哪一列。主口径 kernel_total_us，补充口径 median_us")
    ap.add_argument("--devices", default="auto", help="auto 现查，或逗号分隔的芯片号")
    ap.add_argument("--max-devices", type=int, default=None,
                    help="auto 时最多用几张卡，默认 4。机器公用，独占时给 0")
    ap.add_argument("--work", default="stage/kit_perf", help="分片工作目录")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--source", required=True, help="谁采的这批数，进报告")
    ap.add_argument("--criterion", default="", help="任务书性能要求的原话")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("rest", nargs="*", help="原样透传给自带件脚本的参数")
    ns = ap.parse_args()

    script, cases = Path(ns.script), Path(ns.cases)
    if not script.is_file() or not cases.is_file():
        print(f"读不到 {script if not script.is_file() else cases}", file=sys.stderr)
        return 3
    payload = json.loads(cases.read_text(encoding="utf-8"))
    items = payload.get("cases", payload) if isinstance(payload, dict) else payload
    total = len(items)

    flags = probe_flags(ns.python, script)
    for need in ("--case-file", "--device"):
        if need not in flags:
            print(f"自带件脚本没有 {need}，本量具驱动不了它。"
                  f"探到的参数：{sorted(flags)}", file=sys.stderr)
            return 3

    work = Path(ns.work)
    if work.exists():
        print(f"{work} 已存在。自带件脚本的 trace 目录多用 mkdir 不带 exist_ok，"
              f"残留会让它开跑就崩——先清掉再跑。", file=sys.stderr)
        return 3
    work.mkdir(parents=True)

    devices = pick_devices(ns.devices, total, ns.max_devices)
    if not devices:
        print("一张空闲卡都没有。", file=sys.stderr)
        return 3
    try:
        with perf_lock.hold(work.parent, ns.source, devices):
            codes = shard(ns.python, script, cases, total, devices, work,
                          flags, ns.rest)
    except perf_lock.Busy:
        return 3
    print(f"分片退出码 {codes}", file=sys.stderr)

    rows = merge(work)
    base = load_baseline(ns.baseline, ns.metric)
    out_cases, missing, off_spec = [], [], []
    seen = set()
    for row in rows:
        cid = str(row.get("id", "")).strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        # **先按任务书口径从 Profiler 产物算中位数与 p90**，自带件汇总的那一列
        # 是平均值，两个统计量一个都不是，只在算不出时兜底并标明口径。
        item = {"id": cid}
        stats = profiler_stats(work, cid)
        if stats:
            item.update(stats)
            item["stat_kind"] = "median_p90"
        else:
            under = _num(row.get(ns.metric))
            if under is None or under <= 0:
                missing.append(cid)
                continue
            item["under_test_us"] = under
            item["stat_kind"] = "mean"
            item["stat_note"] = (f"Profiler 逐次读数认不出，取自带件汇总列 "
                                 f"{ns.metric}（平均值），不是任务书要的中位数")
            off_spec.append(cid)
        if cid in base:
            item["baseline_us"] = base[cid]
        out_cases.append(item)

    doc = stage_clock.stamp({"source": ns.source, "unit": "us",
                             "cases": out_cases})
    if ns.criterion:
        doc["criterion"] = ns.criterion
    Path(ns.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    withb = sum(1 for c in out_cases if "baseline_us" in c)
    print(f"自带件性能  {ns.source}")
    print(f"用例        {len(out_cases)}/{total} 条跑出数，{withb} 条带基线 -> {ns.out}")
    if missing:
        print(f"没跑出数    {len(missing)} 条：{missing[:8]}，"
              f"逐条原因看 {work}/dev*.log")
    if off_spec:
        print(f"口径不符    {len(off_spec)} 条只有平均值：{off_spec[:8]}。"
              f"任务书要中位数与 90%分位，这几条 Profiler 逐次读数认不出，"
              f"报告会逐条标出来")
    if not out_cases:
        return 2
    return 4 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
