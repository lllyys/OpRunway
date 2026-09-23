#!/usr/bin/env python3
"""从 0923 竞品 bench_result.json 为 canonical case 生成逐 case 性能基线。

用法（spec 2.5）::

    make_baseline.py --canonical <canonical_cases.json> --bench-dir <0923任务目录> --out <目录>

输出 ``<out>/<op>/perf_baseline.json``——逐算子一份，供 F 卡逐字节装入对应算子包
（spec 第 3 节：F 复用冻结产物不重生成）。每份是按 canonical 顺序的 JSON 数组，逐 case::

    {case_id, bench_key, min_ms, max_ms, avg_ms, matched, dup_count}

规则（spec 2.2 与 canonical conventions 冻结口径）：

- ``bench_key`` 为 null（无源条目）时 ``matched=false``，耗时字段全 null，``dup_count`` 亦 null；
- ``bench_key = {"file": 相对 bench-dir 路径, "entry": 该文件 cases 数组 0 起序号}``，
  定位后与 canonical 的 n/uplo/lda（potrs 另含 nrhs/ldb）逐字段核对，不符即报错退出；
  uplo 按 LOWER→L、UPPER→U 对照；
- bench 内与定位条目全参数相同的重复条目：取首条的耗时并记 ``dup_count``（含自身）；
  bench_key 指向非首现条目时告警并仍取首条。

退出码 0 成功；2 输入不合格（bench_key 非法、定位失败、参数不符、耗时缺失），
不合格时逐条列出问题且不写任何输出文件。纯标准库，只读 canonical 与 bench。
"""

import argparse
import json
from pathlib import Path
import sys

UPLO_FROM_BENCH = {"LOWER": "L", "UPPER": "U"}
# bench 条目里这些字段是测量元数据，不参与「全参数组合」重复判定
NON_PARAM_FIELDS = frozenset({"iters", "check", "perf"})
PERF_FIELDS = ("min_ms", "max_ms", "avg_ms")


class InputError(Exception):
    """输入不合格：canonical 或 bench 内容与契约不符。"""


class BenchFile:
    """一份 bench_result.json：条目数组、逐条全参数键与首现/重复计数索引。"""

    def __init__(self, path):
        self.path = path
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data.get("cases") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise InputError(f"{path}: 顶层没有 cases 数组")
        self.entries = entries
        self.keys = [self._param_key(entry, idx) for idx, entry in enumerate(entries)]
        self.first_index = {}
        self.key_count = {}
        for idx, key in enumerate(self.keys):
            self.first_index.setdefault(key, idx)
            self.key_count[key] = self.key_count.get(key, 0) + 1

    def _param_key(self, entry, idx):
        if not isinstance(entry, dict):
            raise InputError(f"{self.path}: 第 {idx} 条不是对象")
        return tuple(sorted((k, v) for k, v in entry.items() if k not in NON_PARAM_FIELDS))


def load_cases(canonical_path):
    with open(canonical_path, encoding="utf-8") as fh:
        data = json.load(fh)
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not cases:
        raise InputError(f"{canonical_path}: 读不到 case 清单")
    return cases


def check_entry_matches(case, entry):
    """核对定位到的 bench 条目与 canonical 参数，返回不符描述清单（空表示一致）。"""
    expected = [("n", case.get("n")), ("lda", case.get("lda"))]
    for field in ("nrhs", "ldb"):
        if case.get(field) is not None:
            expected.append((field, case.get(field)))
    mismatches = [
        f"{field}: canonical={want!r} bench={entry.get(field)!r}"
        for field, want in expected
        if entry.get(field) != want
    ]
    if UPLO_FROM_BENCH.get(entry.get("uplo")) != case.get("uplo"):
        mismatches.append(f"uplo: canonical={case.get('uplo')!r} bench={entry.get('uplo')!r}")
    return mismatches


def resolve_perf(case, bench_dir, bench_cache):
    """按 bench_key 定位并核对，返回 (首现序号, dup_count, perf 字典)。"""
    case_id = case.get("case_id")
    key = case["bench_key"]
    if (not isinstance(key, dict) or not isinstance(key.get("file"), str)
            or not isinstance(key.get("entry"), int)):
        raise InputError(f'{case_id}: bench_key 应为 {{"file", "entry"}}，得到 {key!r}')
    rel = key["file"]
    bench = bench_cache.get(rel)
    if bench is None:
        path = Path(bench_dir) / rel
        if not path.is_file():
            raise InputError(f"{case_id}: bench 文件不存在: {path}")
        bench = BenchFile(path)
        bench_cache[rel] = bench
    idx = key["entry"]
    if not 0 <= idx < len(bench.entries):
        raise InputError(f"{case_id}: entry {idx} 超界（{rel} 共 {len(bench.entries)} 条）")
    mismatches = check_entry_matches(case, bench.entries[idx])
    if mismatches:
        raise InputError(f"{case_id}: bench 条目 {rel}#{idx} 与 canonical 参数不符: "
                         + "; ".join(mismatches))
    param_key = bench.keys[idx]
    first = bench.first_index[param_key]
    dup_count = bench.key_count[param_key]
    if first != idx:
        print(f"警告 {case_id}: bench_key 指向第 {idx} 条，非首现（首现 {first}），"
              f"按 spec 2.2 取首条耗时", file=sys.stderr)
    perf = bench.entries[first].get("perf")
    if (not isinstance(perf, dict)
            or any(not isinstance(perf.get(f), (int, float)) for f in PERF_FIELDS)):
        raise InputError(f"{case_id}: {rel}#{first} 缺少数值耗时字段 perf.min_ms/max_ms/avg_ms")
    return first, dup_count, perf


def build_baseline(cases, bench_dir):
    """逐 case 产基线行，按 op 分组保序；发现的问题攒齐后一次抛出。"""
    bench_cache = {}
    rows_by_op = {}
    problems = []
    for case in cases:
        case_id = case.get("case_id")
        op = case.get("op")
        if not case_id or not op:
            problems.append(f"case 缺少 case_id 或 op: {case!r}")
            continue
        row = {
            "case_id": case_id,
            "bench_key": case.get("bench_key"),
            "min_ms": None,
            "max_ms": None,
            "avg_ms": None,
            "matched": False,
            "dup_count": None,
        }
        if case.get("bench_key") is not None:
            try:
                _, dup_count, perf = resolve_perf(case, bench_dir, bench_cache)
            except InputError as exc:
                problems.append(str(exc))
            else:
                row.update({f: perf[f] for f in PERF_FIELDS})
                row["matched"] = True
                row["dup_count"] = dup_count
        rows_by_op.setdefault(op, []).append(row)
    if problems:
        raise InputError("\n".join(problems))
    return rows_by_op


def write_outputs(rows_by_op, out_dir):
    written = []
    for op, rows in rows_by_op.items():
        op_dir = Path(out_dir) / op
        op_dir.mkdir(parents=True, exist_ok=True)
        path = op_dir / "perf_baseline.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        written.append(path)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--canonical", required=True, help="canonical_cases.json 路径")
    parser.add_argument("--bench-dir", required=True,
                        help="0923 任务目录（bench_key.file 的相对根）")
    parser.add_argument("--out", required=True,
                        help="输出目录，产物为 <out>/<op>/perf_baseline.json")
    args = parser.parse_args(argv)

    try:
        cases = load_cases(args.canonical)
        rows_by_op = build_baseline(cases, args.bench_dir)
    except InputError as exc:
        print(f"输入不合格，未写任何输出：\n{exc}", file=sys.stderr)
        return 2

    written = write_outputs(rows_by_op, args.out)
    for op, rows in rows_by_op.items():
        matched = sum(1 for r in rows if r["matched"])
        dup_ids = [r["case_id"] for r in rows if (r["dup_count"] or 0) > 1]
        line = (f"{op}: 共 {len(rows)} case，matched {matched}，"
                f"未匹配 {len(rows) - matched}，dup_count>1 共 {len(dup_ids)} 条")
        if dup_ids:
            line += "：" + ", ".join(dup_ids)
        print(line)
    for path in written:
        print(f"已写 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
