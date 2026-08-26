#!/usr/bin/env python3
"""拉起 ATK 任务并把 xlsx 报告解析成 JSON。

四个模式共用一套节点拓扑与报告解析：
  smoke        抽样用例跑一轮精度，确认部署可用
  accuracy     全量用例比对冻结 golden
  performance  按 facts.json 的性能形态跑 performance_device
  isolate      把失败用例逐条单独重跑，把真实失败与 aicore 连带失败分开

退出码 0 跑完且报告已解析，2 任务失败，3 输入缺失。
**退出码 0 不等于精度通过**，结论看输出 JSON 的 passed。
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

TIMEOUT = 7200

# 冒烟执行失败率超过它就判部署有问题，拦在 A3；低于它说明部署好，个别用例是算子缺陷。
SMOKE_BLOCK_RATE = 0.2


def _node_command(mode, cases, golden, devices, plugin):
    """aclnn 侧实算，cpu 侧读冻结 golden，不重算标杆。"""
    command = ["atk", "node", "--backend", "aclnn", "--devices", devices]
    if mode == "performance":
        command += ["node", "--backend", "cpu", "--task", "accuracy_load",
                    "--output_path", str(Path(golden).resolve()),
                    "task", "-c", str(cases), "--task", "performance_device"]
    else:
        command += ["node", "--backend", "cpu", "--task", "accuracy_load",
                    "--output_path", str(Path(golden).resolve()),
                    "task", "-c", str(cases), "--task", "accuracy"]
    if plugin:
        command += ["-p", str(plugin)]
    return command


def _newest_report(stem, since):
    """取本轮产出的报告。

    **必须卡 since。** 任务失败到没写出报告时，只取「最新」会拿到上一轮的报告；
    上一轮多半是通过的，于是这一轮被误判成通过。隔离复验里全是单用例连跑，
    这个坑会把真实失败判成连带失败——真机上就这样把 IndexFillTensor 的
    id=152 漏掉过。
    """
    reports = glob.glob(f"atk_output/{stem}_*/report/*.xlsx")
    fresh = [r for r in reports if os.path.getmtime(r) >= since]
    return max(fresh, key=os.path.getmtime) if fresh else None


def _sheet_rows(book, name):
    if name not in book.sheetnames:
        return []
    sheet = book[name]
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(cell) if cell is not None else "" for cell in rows[0]]
    out = []
    for row in rows[1:]:
        if all(cell is None or str(cell) == "None" for cell in row):
            continue
        out.append(dict(zip(header, row)))
    return out


def _parse_report(path, mode):
    """summary 表的列名随任务类型变：精度任务是「通过用例个数/通过率/精度是否达标」，
    性能任务是「device性能通过率/平均device性能比/device性能是否达标」。"""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    summary = _sheet_rows(book, "summary")
    failed = _sheet_rows(book, "failed cases")
    accuracy_false = _sheet_rows(book, "accuracy false cases")
    statistic = _sheet_rows(book, "statistic")
    book.close()

    result = {
        "report": path,
        "total": 0,
        "succeeded": 0,
        "failed": 0,
        "matched": 0,
        "pass_rate": 0.0,
        "passed": False,
        "failed_ids": sorted({int(row["编号"]) for row in failed
                              if str(row.get("编号", "")).isdigit()}),
        "accuracy_false_ids": sorted({int(row["编号"]) for row in accuracy_false
                                      if str(row.get("编号", "")).isdigit()}),
    }

    if summary:
        row = summary[0]

        def _int(key):
            try:
                return int(float(row.get(key)))
            except (TypeError, ValueError):
                return 0

        def _float(key):
            try:
                return float(row.get(key))
            except (TypeError, ValueError):
                return 0.0

        result["node"] = row.get("名称", "")
        result["total"] = _int("总用例数")
        if mode == "performance":
            result["succeeded"] = result["total"] - len(result["failed_ids"])
            result["failed"] = len(result["failed_ids"])
            result["pass_rate"] = _float("device性能通过率")
            result["avg_ratio"] = row.get("平均device性能比")
            result["passed"] = str(row.get("device性能是否达标", "")).strip().lower() == "pass"
        else:
            result["succeeded"] = _int("执行成功用例个数")
            result["failed"] = _int("执行失败用例个数")
            result["matched"] = _int("通过用例个数")
            result["pass_rate"] = _float("通过率")
            result["passed"] = str(row.get("精度是否达标", "")).strip().lower() == "pass"

    result["statistic"] = statistic
    return result


def _device_times(statistic):
    """从 statistic 表按用例取 aclnn 与 cpu 的 Device 耗时。"""
    times = {}
    for row in statistic:
        ident = row.get("编号")
        if not str(ident).isdigit():
            continue
        entry = {}
        for key, value in row.items():
            if "Device性能" not in str(key):
                continue
            try:
                entry["aclnn" if key.startswith("pyaclnn") else "cpu"] = float(value)
            except (TypeError, ValueError):
                continue
        if entry:
            times[int(ident)] = entry
    return times


def _run_subset(ids, cases, golden, devices, plugin, work_dir):
    """把指定 id 的用例单独写一份跑一轮。目录换、文件名不换——
    golden 的子目录名取自用例文件基名，换名就找不到 golden。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    with open(cases, encoding="utf-8") as handle:
        allcases = json.load(handle)
    wanted = [case for case in allcases if case["id"] in set(ids)]
    subset = work_dir / "cases.json"
    with open(subset, "w", encoding="utf-8") as handle:
        json.dump(wanted, handle, ensure_ascii=False)

    command = _node_command("smoke", subset, golden, devices, plugin)
    started = time.time() - 1  # 留 1 秒余量，防文件系统时间戳取整
    try:
        with open(work_dir / "run.log", "w", encoding="utf-8") as handle:
            subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT,
                           timeout=1800, check=False)
    except subprocess.TimeoutExpired:
        return None
    report = _newest_report("cases", started)
    # 没产出报告 = 这一轮挂到连报告都没写出来，按失败算，不能沿用上一轮的。
    return _parse_report(report, "smoke") if report else None


def _isolate(args, cases, golden, plugin):
    """aicore 异常会让同批次后续用例连带失败。逐条单独重跑才知道哪些是真的。"""
    source = Path(args.ids_from)
    if not source.exists():
        print(f"{source} 不存在，isolate 模式要先跑 accuracy。", file=sys.stderr)
        return 3
    with open(source, encoding="utf-8") as handle:
        failed_ids = json.load(handle).get("failed_ids", [])

    if not failed_ids:
        print("没有执行失败的用例，不需要隔离复验。")
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump({"mode": "isolate", "checked": 0, "real_failures": [],
                       "cascade": []}, handle, ensure_ascii=False, indent=2)
        return 0

    checked = failed_ids[:args.max_isolate]
    print(f"隔离复验  {len(checked)} 条（共 {len(failed_ids)} 条失败）", flush=True)
    real, cascade = [], []
    for index, ident in enumerate(checked, 1):
        result = _run_subset([ident], cases, golden, args.devices, plugin,
                             Path("isolate") / str(ident))
        if result is None or result["failed"]:
            real.append(ident)
            mark = "真实失败"
        else:
            cascade.append(ident)
            mark = "连带失败"
        print(f"  [{index}/{len(checked)}] id={ident} {mark}", flush=True)

    record = {
        "mode": "isolate",
        "checked": len(checked),
        "not_checked": failed_ids[args.max_isolate:],
        "real_failures": real,
        "cascade": cascade,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)

    print(f"\n真实失败  {len(real)} 条：{real[:12]}")
    print(f"连带失败  {len(cascade)} 条（单独跑能过，是前一条把设备打挂后的余波）")
    print(f"写入      {args.out}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True,
                        choices=["smoke", "accuracy", "performance", "isolate"])
    parser.add_argument("--ids-from", default="accuracy.json",
                        help="isolate 模式从这份报告读 failed_ids")
    parser.add_argument("--max-isolate", type=int, default=60,
                        help="isolate 模式最多逐条复验多少条")
    parser.add_argument("-c", "--cases", required=True)
    parser.add_argument("--golden", default="golden")
    parser.add_argument("--facts", default="facts.json")
    parser.add_argument("--devices", default="0")
    parser.add_argument("-p", "--plugin", default=None, help="执行器，默认自动找")
    parser.add_argument("--builtin-baseline", action="store_true",
                        help="性能基线轮：摘掉自定义算子包，让 pyaclnn 回落到 CANN "
                             "内置的同名实现。只在 facts 的 performance.kind 是 "
                             "builtin 时用，且要与被测轮跑同一套用例。")
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args()

    cases = Path(args.cases)
    golden = Path(args.golden)
    if not cases.exists():
        print(f"{cases} 不存在。", file=sys.stderr)
        return 3
    if not golden.is_dir():
        print(f"{golden}/ 不存在。用例包里应该带冻结好的 golden。", file=sys.stderr)
        return 3
    if args.builtin_baseline:
        if args.mode != "performance":
            print("--builtin-baseline 只用于 --mode performance。", file=sys.stderr)
            return 3
        for name in ("ATK_CUSTOM_OPP_PATH", "ASCEND_CUSTOM_OPP_PATH"):
            os.environ.pop(name, None)
        print("基线轮      已摘掉自定义算子包，本轮测的是 CANN 内置实现")
    elif not os.environ.get("ATK_CUSTOM_OPP_PATH"):
        print("ATK_CUSTOM_OPP_PATH 没设。先 source evidence/env.sh，"
              "否则测的是 CANN 内置的同名算子，不是待验收实现。", file=sys.stderr)
        return 3

    plugin = args.plugin
    if plugin is None:
        matches = sorted(Path(".").glob("function_*.py"))
        plugin = str(matches[0]) if len(matches) == 1 else None

    if args.mode == "isolate":
        return _isolate(args, cases, golden, plugin)

    command = _node_command(args.mode, cases, golden, args.devices, plugin)
    print(f"模式      {args.mode}")
    print(f"命令      {' '.join(command)}")

    # 基线轮与被测轮都是 performance 模式，日志名要分开，否则后跑的覆盖先跑的。
    stem = f"{args.mode}_builtin" if args.builtin_baseline else args.mode
    log_path = Path("evidence") / f"{stem}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    # 报告的时间下界留 1 秒余量，防文件系统时间戳取整把本轮报告排除掉。
    report_since = started - 1
    try:
        with open(log_path, "w", encoding="utf-8") as handle:
            proc = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT,
                                  timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        print(f"\n任务超过 {TIMEOUT}s 没结束，看 {log_path}。", file=sys.stderr)
        return 2
    elapsed = time.time() - started

    # 两轮性能都用同一份 perf/cases.json，报告目录名也一样，
    # 靠 mtime 取最新的那份。所以同一个工作目录里两轮不能并发跑。
    report = _newest_report(cases.stem, report_since)
    if report is None:
        print(f"\natk 退出码 {proc.returncode}，但没生成报告。日志尾部：", file=sys.stderr)
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]:
            print(f"  {line}", file=sys.stderr)
        return 2

    result = _parse_report(report, args.mode)
    result["mode"] = args.mode
    result["command"] = " ".join(command)
    result["elapsed_seconds"] = round(elapsed, 1)
    result["log"] = str(log_path)
    result["returncode"] = proc.returncode

    if args.mode == "performance":
        result["device_times"] = _device_times(result.get("statistic", []))
        result["builtin_baseline"] = args.builtin_baseline
    result.pop("statistic", None)

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    print(f"\n总用例    {result['total']}")
    print(f"执行成功  {result['succeeded']}    执行失败 {result['failed']}")
    if args.mode == "performance":
        times = result.get("device_times") or {}
        samples = sorted(v["aclnn"] for v in times.values() if "aclnn" in v)
        if samples:
            print(f"Device 耗时  {len(samples)} 条，中位数 {samples[len(samples) // 2]:.2f} us，"
                  f"区间 [{samples[0]:.2f}, {samples[-1]:.2f}] us")
        print(f"性能基线  {result.get('avg_ratio') or '无（报表未给出对比比值）'}")
    else:
        print(f"精度通过  {result['matched']}    通过率 {result['pass_rate']}%")
        print(f"达标      {'是' if result['passed'] else '否'}")
    print(f"报告      {report}")
    print(f"写入      {args.out}（耗时 {elapsed:.0f}s）")

    if result["failed_ids"]:
        head = result["failed_ids"][:12]
        more = "…" if len(result["failed_ids"]) > 12 else ""
        print(f"失败用例  {head}{more}")

    if args.mode == "smoke" and result["failed"]:
        # 部署或适配坏了会让绝大多数用例都跑不起来；只挂零星几条说明部署是好的，
        # 是个别用例触发了算子缺陷——那要进 A4 测准，不能在这里拦死。
        rate = result["failed"] / result["total"] if result["total"] else 1.0
        if rate > SMOKE_BLOCK_RATE:
            print(f"\n阻塞·未验收 @A3：冒烟 {result['failed']}/{result['total']} 条执行失败"
                  f"（{rate:.0%} > {SMOKE_BLOCK_RATE:.0%}）。部署或适配有问题，"
                  f"按 troubleshooting.md 定位，不要跑全量。", file=sys.stderr)
            return 2
        print(f"\n冒烟有 {result['failed']}/{result['total']} 条执行失败"
              f"（{rate:.0%}，未超 {SMOKE_BLOCK_RATE:.0%}）。"
              f"部署是好的，这几条是算子缺陷，进 A4 测准后用 --mode isolate 复验。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
