#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stream_check.py —— 流式判定驱动（数据形态裁定 2026-09-23：不落盘，生成即测）。

逐 case 在同进程内完成：现场生成输入与 golden → 现场算 ratio_cpu（CPU 同精度链）→
产生被测输出（当前为 sim 通路；真实 NPU 被测经适配器接入，见 DUT 适配点注释）→
criteria.judge 判定 → 只留判定记录，数组即弃。全程不写数组文件（--dump 例外，调试用）。

确定性与追溯（supplement 23i）：显式 seed + 报告记录环境版本 + 生成与判定同进程闭环。
判定语义与包通路完全同源：build_case_arrays / run_chain / perturb_out32 / judge 均为
既有模块的同一实现，无平行副本。

用法：
    stream_check.py --canonical <canonical_cases.json> --gen-dir <case-gen 的 scripts 目录>
                    --report <out.json> [--ops spotrf,...] [--select s1|all]
                    [--max-n N] [--limit K] [--perturb none|scale|zero|nan|imag|conj]
                    [--dump <case_id> ...] [--dump-dir <目录>]

退出码：0 = 全部数值 PASS；1 = 存在数值 FAIL 或判定 error；2 = 输入/契约错误。
formal 恒 PENDING_RULING（阈值语义待裁），本工具不出具正式结论。
"""

import argparse
import json
import platform
import resource
import sys
import time
from pathlib import Path

import numpy as np

# ru_maxrss 单位：Linux 为 KB、macOS 为字节（2026-09-24 远程实证抓出的少报 1024 倍）
_RSS_DIV = 1024 if platform.system() == "Linux" else 1024 * 1024
TOOL = "stream_check.py"
TOOL_VER = "s2d-r2"  # r2: 修 ru_maxrss 单位（Linux=KB，macOS=字节）

_HERE = Path(__file__).resolve().parent
_CRITERIA = _HERE.parent / "criteria"


class ContractError(RuntimeError):
    pass


def _load_modules(gen_dir):
    """装入同源实现：criteria（权威判定）与 case-gen（生成/ratio 链）。"""
    for p in (str(_CRITERIA), str(gen_dir)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import verdict as verdict_mod            # noqa: E402
    import cards_cholesky as cards_mod       # noqa: E402
    import gen_data_cholesky as gen_mod      # noqa: E402
    import fill_ratio_cpu as ratio_mod       # noqa: E402
    if not (Path(gen_dir) / "gen_data_cholesky.py").is_file():
        raise ContractError(f"--gen-dir 里找不到 gen_data_cholesky.py: {gen_dir}")
    return verdict_mod, cards_mod, gen_mod, ratio_mod


def _sim_dut(golden32, mode):
    """sim 通路：复用 sim_dut.perturb_out32（同一实现，不复制逻辑）。

    DUT 适配点：真实 NPU 被测接入时，以「输入数组 → {out32, info, status}」的适配器
    替换本函数（协议同包通路的被测输出三键；采样与性能不在本工具范围）。
    """
    sys.path.insert(0, str(_HERE))
    try:
        from sim_dut import perturb_out32
    finally:
        sys.path.remove(str(_HERE))
    return {"out32": perturb_out32(golden32, mode), "info": 0, "status": "ok"}


def main(argv=None):
    ap = argparse.ArgumentParser(prog=TOOL, description=__doc__.splitlines()[0])
    ap.add_argument("--canonical", required=True, help="canonical_cases.json 路径")
    ap.add_argument("--gen-dir", required=True,
                    help="repo-task-solver-case-gen 的 scripts 目录（提供生成与 ratio 链）")
    ap.add_argument("--report", required=True, help="判定记录 JSON 输出路径")
    ap.add_argument("--ops", default=None, help="逗号分隔算子子集；缺省取 canonical 全部")
    ap.add_argument("--select", choices=("s1", "all"), default="all",
                    help="case 选择；流式默认 all（不落盘，无容量约束）")
    ap.add_argument("--max-n", type=int, default=None, help="只跑 n ≤ 此值的 case")
    ap.add_argument("--limit", type=int, default=None, help="最多跑前 K 个（抽样/冒烟）")
    ap.add_argument("--perturb", default="none",
                    choices=("none", "scale", "zero", "nan", "imag", "conj"),
                    help="sim 扰动模式（负例演练用；none=正例）")
    ap.add_argument("--dump", action="append", default=[],
                    help="指定 case_id 落盘其数组与被测输出（调试后门，可重复）")
    ap.add_argument("--dump-dir", default=None, help="--dump 的落盘目录")
    args = ap.parse_args(argv)

    try:
        verdict_mod, cards_mod, gen_mod, ratio_mod = _load_modules(args.gen_dir)
        with open(args.canonical, encoding="utf-8") as fh:
            canonical = json.load(fh)
        cases = canonical.get("cases")
        if not isinstance(cases, list):
            raise ContractError(f"{args.canonical}: 顶层缺 cases 数组")
        ops = set(o.strip() for o in args.ops.split(",")) if args.ops else None
        picked = [c for c in cases
                  if (ops is None or c.get("op") in ops)
                  and (args.select == "all" or c.get("s1_subset") is True)
                  and (args.max_n is None or c.get("n", 0) <= args.max_n)]
        if args.limit:
            picked = picked[: args.limit]
        if not picked:
            raise ContractError("筛选后没有 case 可跑（检查 --ops/--select/--max-n）")
        if args.dump and not args.dump_dir:
            raise ContractError("--dump 需要同时给 --dump-dir")
        lapack = ratio_mod._lapack()
    except ContractError as exc:
        print(f"[{TOOL}] 输入/契约错误: {exc}", file=sys.stderr)
        return 2

    records, t0 = [], time.time()
    n_pass = n_fail = n_err = n_prep = 0
    for k, case in enumerate(picked, 1):
        cid, op = case["case_id"], case["op"]
        tc = time.time()
        try:
            gen_mod.validate_case(case)
            arrays = gen_mod.build_case_arrays(case)
            try:
                ratio_cpu = ratio_mod.run_chain(verdict_mod, lapack, case, arrays)
                ratio_status = "ok"
                n_prep_flag = False
            except ratio_mod.PrepFailed as pf:
                ratio_cpu, ratio_status, n_prep_flag = None, "prep_failed", True
            dut = _sim_dut(arrays["golden32"], args.perturb)
            case_arrays = dict(arrays)
            case_arrays.update(case)
            case_arrays["ratio_cpu"] = ratio_cpu
            case_arrays["ratio_cpu_status"] = ratio_status
            v = verdict_mod.judge(cards_mod.get_card(op), case_arrays, dut)
            rec = {
                "case_id": cid, "op": op, "n": case.get("n"), "uplo": case.get("uplo"),
                "numeric": v.get("numeric"), "formal": v.get("formal"),
                "flags": v.get("flags"), "error": v.get("error"),
                "layer1": {kk: v["layer1"].get(kk) for kk in ("matched_ratio", "max_abs")},
                "fallback": {kk: v["fallback"].get(kk) for kk in ("ran", "ratio", "threshold")},
                "ratio_cpu": ratio_cpu, "ratio_cpu_status": ratio_status,
                "seconds": round(time.time() - tc, 3),
            }
            if v.get("error"):
                n_err += 1
            elif v.get("numeric") == "PASS":
                n_pass += 1
            else:
                n_fail += 1
            if n_prep_flag:
                n_prep += 1
            if cid in args.dump:
                dd = Path(args.dump_dir); dd.mkdir(parents=True, exist_ok=True)
                np.savez(dd / f"{cid}.npz", **arrays)
                np.savez(dd / f"{cid}.dut.npz", out32=dut["out32"],
                         info=np.int64(dut["info"]))
            records.append(rec)
            del arrays, case_arrays, dut                 # 即弃：流式的核心承诺
        except Exception as exc:                          # 单 case 失败不吞整轮
            n_err += 1
            records.append({"case_id": cid, "op": op, "error": f"{type(exc).__name__}: {exc}"})
        if k % 20 == 0 or k == len(picked):
            peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // _RSS_DIV
            print(f"[{TOOL}] {k}/{len(picked)} pass={n_pass} fail={n_fail} "
                  f"err={n_err} peak_rss={peak_mb}MB", file=sys.stderr)

    report = {
        "tool": {"name": TOOL, "ver": TOOL_VER},
        "params": {"canonical": str(args.canonical), "select": args.select,
                   "ops": sorted(ops) if ops else "all", "max_n": args.max_n,
                   "limit": args.limit, "perturb": args.perturb},
        "env": {"python": sys.version.split()[0], "numpy": np.__version__},
        "summary": {"total": len(picked), "numeric_pass": n_pass, "numeric_fail": n_fail,
                    "error": n_err, "prep_failed": n_prep,
                    "wall_seconds": round(time.time() - t0, 1),
                    "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // _RSS_DIV},
        "formal": "PENDING_RULING",
        "cases": records,
    }
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"[{TOOL}] 完成 {len(picked)} case: pass={n_pass} fail={n_fail} err={n_err} "
          f"→ {out}")
    return 0 if (n_fail == 0 and n_err == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
