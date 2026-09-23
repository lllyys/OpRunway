#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""accept_run.py —— S1-Cholesky E 卡：accept 骨架的流程编排（消费包与被测输出，出 report）。

契约：dev-doc/solver/solver-s1-cholesky-spec.md §2.5（CLI 与 report 结构）、§2.2
（包 schema 与指纹分级）、§2.3/§2.3′（judge 消费与七类期望项）。裁决只调 criteria
（judge/卡，唯一实现）；期望集与状态映射只调 expectations（同目录）；本文件只做
I/O、指纹核对与装配。

CLI（spec §2.5，逐字）：
    accept_run.py --package <dir> --dut-out <dir> --report <file>
--package 指一个算子的包目录（cases/、perf_baseline.json、manifest.json，spec §2.2）；
--dut-out 指被测输出目录（<case_id>.npz 含 out32/info/status，spec §2.5）；--report
为输出 report.json 路径。report 结构（spec §2.5）：{operator, expectation: [{item,
kind, status, evidence}], flags, versions, 声明边界: [...]}，期望集按 §2.3′ 七类。

指纹分级（spec §2.2）：manifest.fingerprint 逐条核对 sha256——
- `cases/`（npz 与 index.json）、`perf_baseline.json` 错配或缺失 → **阻断对应结论**：
  该 case（或全部依赖 index 的接口精度项 / 性能参考项）记证据不足，evidence 指认错配；
- 其余文件（含 `verify_*.py` 副本漂移）→ **仅告警**：flags 记 `WARN:指纹漂移:<路径>`，
  不改变任何期望项状态。
manifest 里没有登记指纹的证据文件按无指纹处理：cases/index.json、perf_baseline.json
与逐 case npz 必须有指纹才可采信（捕捉旧产物混入这类无意错误，正确性不变量
fail-closed）；缺登记同样按阻断记证据不足。

被测耗时（可选约定，本片模拟被测不产出）：<dut-out>/perf.json 为 {case_id: 毫秒}
时计算参考比值（方向 被测/基线，基线取 perf_baseline 的 avg_ms/min_ms 并报），
参考项记待裁（正式门走任务书机制，T1）；文件不存在则参考项如实记证据不足。

退出码：0 = report 已写出（含存在 FAIL/证据不足的情形——结论在 report 里，不用退出
码表达）；2 = 包不可用（manifest/index 缺失或不可解析、operator 不在支持集），此时
不产出 report。
"""

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np

TOOL = "accept_run.py"
TOOL_VER = "s1-E1"

_SCRIPTS_DIR = Path(__file__).resolve().parent
_CRITERIA_DIR = _SCRIPTS_DIR.parent / "criteria"
for _p in (str(_SCRIPTS_DIR), str(_CRITERIA_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import expectations as exp          # noqa: E402  (同目录期望集模块)
import cards_cholesky               # noqa: E402  (criteria 判据卡)
import thresholds                   # noqa: E402  (criteria 阈值常量, CRITERIA_VER)
import verdict as verdict_mod       # noqa: E402  (criteria judge)


def _fail(msg):
    print(f"[accept_run] 错误: {msg}", file=sys.stderr)
    sys.exit(2)


def _load_json(path, what):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        _fail(f"{what} 缺失: {path}")
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"{what} 不可解析: {path}: {exc}")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 指纹核对（spec §2.2 分级）
# ---------------------------------------------------------------------------

def _is_blocking_path(rel):
    """阻断级证据文件：cases/ 全部（npz 含 golden）与 perf_baseline.json。"""
    return rel == "perf_baseline.json" or rel.startswith("cases/")


def check_fingerprints(package, manifest):
    """核对 manifest.fingerprint。返回 (mismatch_blocking: {rel: 描述},
    warnings: [str])。阻断级错配/缺失进前者；其余漂移只进告警。"""
    fp = manifest.get("fingerprint")
    mismatch, warnings = {}, []
    if not isinstance(fp, dict):
        mismatch["cases/index.json"] = "manifest.fingerprint 缺失或非法"
        mismatch["perf_baseline.json"] = "manifest.fingerprint 缺失或非法"
        return mismatch, ["WARN:指纹表缺失:manifest.fingerprint"]
    for rel, expected in sorted(fp.items()):
        path = package / rel
        if not path.is_file():
            desc = "文件缺失"
        else:
            actual = _sha256(path)
            desc = None if actual == expected else f"sha256 错配 {actual[:12]}…"
        if desc is None:
            continue
        if _is_blocking_path(rel):
            mismatch[rel] = desc
        else:
            warnings.append(f"WARN:指纹漂移:{rel}（{desc}）")
    # 必须登记指纹的证据文件缺登记 → 按阻断处理（无指纹不可采信）。
    for rel in ("cases/index.json", "perf_baseline.json"):
        if rel not in fp:
            mismatch.setdefault(rel, "指纹未登记")
    return mismatch, warnings


# ---------------------------------------------------------------------------
# 数据装载
# ---------------------------------------------------------------------------

def load_case_arrays(package, entry):
    """按 index 条目装载 case npz 并合并元数据（spec §2.2/§2.3 judge 的 case_arrays）。
    返回 (dict, None) 或 (None, 原因)。"""
    npz_rel = entry.get("npz")
    if not npz_rel:
        return None, "index 条目无 npz 路径"
    path = package / npz_rel
    if not path.is_file():
        return None, f"证据缺失: {npz_rel} 不存在"
    try:
        with np.load(path) as z:
            arrays = {k: z[k] for k in z.files}
    except Exception as exc:  # 损坏的 npz 属证据问题，不是裁决结论
        return None, f"证据不可读: {npz_rel}: {type(exc).__name__}: {exc}"
    arrays["uplo"] = entry.get("uplo")
    arrays["ratio_cpu"] = entry.get("ratio_cpu")
    arrays["ratio_cpu_status"] = entry.get("ratio_cpu_status")
    return arrays, None


def load_dut_out(dut_dir, case_id):
    """装载被测输出 npz（spec §2.5：out32/info/status）。返回 (dict, None) 或
    (None, 原因)。字段残缺按证据问题处理，不构造假 status 送审。"""
    path = dut_dir / f"{case_id}.npz"
    if not path.is_file():
        return None, f"被测输出缺失: {path.name}"
    try:
        with np.load(path) as z:
            missing = [k for k in ("out32", "info", "status") if k not in z.files]
            if missing:
                return None, f"被测输出不完整: 缺 {missing}"
            return {
                "out32": z["out32"],
                "info": int(np.asarray(z["info"]).item()),
                "status": str(np.asarray(z["status"]).item()),
            }, None
    except Exception as exc:
        return None, f"被测输出不可读: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# 期望集装配
# ---------------------------------------------------------------------------

def accuracy_items(package, dut_dir, operator, index_cases, baseline_rows,
                   mismatch_blocking):
    """逐 case 接口精度项：随包 case 送审 judge，未生成/被阻断/证据缺失如实记。"""
    card = cards_cholesky.get_card(operator)
    index_blocked = next(
        (rel for rel in ("cases/index.json",) if rel in mismatch_blocking), None)
    items, flags = [], set()
    universe = exp.case_universe(operator, index_cases, baseline_rows)
    ungenerated = 0
    for case_id, entry in universe.items():
        if entry is None:
            items.append(exp.accuracy_item_ungenerated(case_id))
            ungenerated += 1
            continue
        if index_blocked:
            items.append(exp.accuracy_item_insufficient(
                case_id, "指纹错配",
                f"{index_blocked} {mismatch_blocking[index_blocked]}，"
                "阻断全部依赖 index 的接口精度结论（spec §2.2）"))
            continue
        npz_rel = entry.get("npz")
        if npz_rel and npz_rel in mismatch_blocking:
            items.append(exp.accuracy_item_insufficient(
                case_id, "指纹错配",
                f"{npz_rel} {mismatch_blocking[npz_rel]}，阻断该 case 结论（spec §2.2）"))
            continue
        arrays, why = load_case_arrays(package, entry)
        if arrays is None:
            items.append(exp.accuracy_item_insufficient(case_id, "证据缺失", why))
            continue
        dut, why = load_dut_out(dut_dir, case_id)
        if dut is None:
            items.append(exp.accuracy_item_insufficient(case_id, "被测输出缺失", why))
            continue
        v = verdict_mod.judge(card, arrays, dut)
        item = exp.accuracy_item_from_verdict(case_id, v)
        flags.update(v.get("flags") or [])
        items.append(item)
    return items, flags, ungenerated


def perf_items(package, dut_dir, operator, baseline_rows, mismatch_blocking):
    """P 项性能两项：参考比值（被测/基线，可选 perf.json）与正式门禁（恒待裁，T1）。"""
    items = [exp.perf_formal_gate_item(operator)]
    if "perf_baseline.json" in mismatch_blocking:
        items.insert(0, exp.perf_reference_item(
            operator, exp.ST_NO_EVIDENCE,
            {"reason": "指纹错配",
             "detail": f"perf_baseline.json {mismatch_blocking['perf_baseline.json']}，"
                       "阻断性能参考结论（spec §2.2）"}))
        return items
    matched = [r for r in baseline_rows if r.get("matched")]
    coverage = {"baseline_rows": len(baseline_rows), "matched": len(matched),
                "unmatched": len(baseline_rows) - len(matched)}
    perf_path = dut_dir / "perf.json"
    if not perf_path.is_file():
        items.insert(0, exp.perf_reference_item(
            operator, exp.ST_NO_EVIDENCE,
            {"reason": "被测耗时未交付",
             "detail": "本片模拟被测不产出耗时；bench_result 基线仅自测参考（spec §2.3′）",
             "baseline_coverage": coverage}))
        return items
    try:
        with open(perf_path, "r", encoding="utf-8") as fh:
            dut_ms = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        items.insert(0, exp.perf_reference_item(
            operator, exp.ST_NO_EVIDENCE,
            {"reason": "被测耗时不可读", "detail": f"perf.json: {exc}",
             "baseline_coverage": coverage}))
        return items
    ratios = {}
    for row in matched:
        ms = dut_ms.get(row["case_id"])
        if isinstance(ms, (int, float)) and row.get("avg_ms"):
            ratios[row["case_id"]] = {
                "dut_ms": ms,
                "ratio_vs_avg": ms / row["avg_ms"],
                "ratio_vs_min": (ms / row["min_ms"]) if row.get("min_ms") else None,
            }
    items.insert(0, exp.perf_reference_item(
        operator, exp.ST_PENDING,
        {"direction": "被测/基线（<1 快于基线）", "baseline_coverage": coverage,
         "cases": ratios,
         "ruling": "参考比值无独立判据，正式门走任务书 §3.3（T1 待裁）"}))
    return items


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build_report(package, dut_dir):
    manifest = _load_json(package / "manifest.json", "manifest.json")
    operator = manifest.get("operator")
    if operator not in cards_cholesky.OPS:
        _fail(f"manifest.operator={operator!r} 不在支持集 {cards_cholesky.OPS}")
    index = _load_json(package / "cases" / "index.json", "cases/index.json")
    index_cases = index.get("cases")
    if not isinstance(index_cases, list):
        _fail("cases/index.json 无 cases 列表")
    baseline_rows = _load_json(package / "perf_baseline.json", "perf_baseline.json")
    if not isinstance(baseline_rows, list):
        _fail("perf_baseline.json 非列表")

    mismatch_blocking, warnings = check_fingerprints(package, manifest)

    acc_items, verdict_flags, ungenerated = accuracy_items(
        package, dut_dir, operator, index_cases, baseline_rows, mismatch_blocking)
    p_items = perf_items(package, dut_dir, operator, baseline_rows, mismatch_blocking)
    items = acc_items + p_items + exp.fixed_insufficient_items(operator)

    # flags：T 类分歧显式携带（spec §5）；T1 恒在（性能正式门待裁）；告警仅告警。
    flags = sorted(verdict_flags) + ["T1"] + sorted(warnings)
    versions = {
        "accept_run": TOOL_VER,
        "expectations": exp.EXPECTATIONS_VER,
        "criteria_ver": thresholds.CRITERIA_VER,
        "package": {k: manifest.get(k)
                    for k in ("package_ver", "criteria_ver", "renderer_ver")},
        "env": {"python": platform.python_version(), "numpy": np.__version__},
    }
    boundary = exp.declared_boundary(operator, items, warnings, ungenerated)
    return {
        "operator": operator,
        "expectation": items,
        "flags": flags,
        "versions": versions,
        "声明边界": boundary,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="S1 accept 骨架（spec §2.5 CLI）")
    ap.add_argument("--package", required=True, help="算子包目录（spec §2.2）")
    ap.add_argument("--dut-out", required=True, help="被测输出目录（<case_id>.npz）")
    ap.add_argument("--report", required=True, help="输出 report.json 路径")
    args = ap.parse_args(argv)

    package, dut_dir = Path(args.package), Path(args.dut_out)
    if not package.is_dir():
        _fail(f"包目录不存在: {package}")
    if not dut_dir.is_dir():
        _fail(f"被测输出目录不存在: {dut_dir}")

    report = build_report(package, dut_dir)
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    conclusion = report["声明边界"][0]
    print(f"[accept_run] {report['operator']}: {conclusion} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
