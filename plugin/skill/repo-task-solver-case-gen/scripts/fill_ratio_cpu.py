#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fill_ratio_cpu.py —— S1-Cholesky B2 卡（波 1.5；S2c B2c 卡扩入复数三算子）：
index.json 的 ratio_cpu 回填。

契约：dev-doc/solver/solver-s1-cholesky-spec.md §2.4（ratio_cpu 语义）、§2.2（index
schema）、第 3 节波 1.5 行。B1 的 gen_data_cholesky.py 生成 npz 与 index.json 骨架
（ratio_cpu/ratio_cpu_status 留 null），本脚本对同一冻结输入跑低精度同前缀完整准备链
（实数 FP32 s 前缀 / 复数 complex64 c 前缀）回填这两个字段，产出填充后的 index.json 副本与逐 case 报告；npz 一概不改不重生成
（B 冻结产物由 F 逐字节装包，spec 第 3 节）。

ratio_cpu 语义（spec §2.4）：FP32 s 前缀链路对同一冻结输入跑完整准备链
（potrs/potri 先 spotrf），用 criteria 的 residual_ratio 同一实现计算——本脚本不含
任何残差公式，残差只此一份（AGENTS.md §2 机械门纪律）。数组口径与判据卡的
fallback_kwargs 逐字段相同（cards_cholesky.py，README 各章残差口径）：

- spotrf: F32 = spotrf(A32)；DPOT01(a=A64, factor=F32, uplo)（README 1.3：分子分母用 A64）
- spotrs: F32 = spotrf(A32) → X32 = spotrs(F32, B32)；DPOT02(a=A32, b=B32, x=X32)
  （README 2.3：残差对实现实际输入 A32/B32 升 FP64 算）
- spotri: F32 = spotrf(A32) → C32 = spotri(F32)；DPOT03(a=A32, ainv=C32, uplo)
  （README 3.3：A 用实现实际输入升精度）

与 README ratio_cpu 定义的关系（如实声明，非冲突）：README potrs 2.2 的 ratio_cpu
就是 CPU 同精度 FP32 参考链路（本脚本同口径）；README potrf 1.3 / potri 3.2 的
ratio_cpu 用纯 FP64 参考链路（~1e-13..1e-11），spec §2.4 把三算子统一到 FP32 s 链路
（README 1.6 的 ratio_cpu32 列即此量，实测 0.000~0.008 ulp）。两种口径下
10·ratio_cpu 都远小于收紧式地板 1，阈值取值不受影响；本脚本以 spec 为唯一契约。

prep_failed（spec §2.4）：准备链失败（任一 LAPACK 例程 info≠0，或链路输出含非有限值）
记 ratio_cpu_status="prep_failed"、ratio_cpu=null，该 case 兜底不可用（thresholds 的
阈值函数对 null 拒算，judge 走 error 路径）。启动时先跑一条人工构造的非正定用例
自测该路径（波 1.5 完成条件），自测不过即停。

画像核对（波 1.5 完成条件，对照 README 实测）：potrf 底噪 <1（README 1.6 ratio_cpu32
0.000~0.008）；potrs 相对 potrf 上浮（README 2.4 实测 0.118~5.748，高一个量级以上，
随规模/病态度增长）；potri 与 potrf 同为平坦底噪（README 3.4 DPOT03 0.000~0.009）。
potrf/potri 底噪 <1 是硬断言（不满足即退出码 3）；potrs 上浮画像输出统计并在报告里
给出布尔判读（本片 case 规模 16~512、cu 构造对角占优 κ 小，上浮幅度预期小于 README
的 128~8192 全量表，故不设硬倍数线，如实报数）。

S2c 复数增量（s2 spec §4 + B2c 任务卡）：算子册扩入 cpotrf/cpotrs/cpotri，准备链
用 c 前缀完整链路（cpotrs/cpotri 前置 cpotrf 同精度，complex64）；残差仍唯一取
criteria 的 residual_ratio（DPOT01/02/03 形状不变，复模与升精度由 criteria 侧实现）。
复数画像不设硬门——实数区间不得盲目继承（B2c 任务卡），统计如实输出、判读交
验证段；prep_failed 自测 s/c 两前缀各走一条非正定用例。

CLI：
    fill_ratio_cpu.py --cases <dir> --out <dir> [--criteria <dir>] [--ops s1,s2]
--cases 指含 index.json 与 *.npz 的目录（B1 产物的 cases/）；--criteria 指
repo-task-solver-accept 的 criteria 目录，缺省按镜像树/发布位的相对布局
../../repo-task-solver-accept/criteria 解析。产出：<out>/cases/index.json（填充副本）
与 <out>/ratio_cpu_report.json。
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

TOOL = "fill_ratio_cpu.py"
TOOL_VER = "s2c-b2c-r1"  # S2c：复数三算子入册（c 前缀准备链）；承 s1-b2-r1
REAL_OPS = ("spotrf", "spotrs", "spotri")
COMPLEX_OPS = ("cpotrf", "cpotrs", "cpotri")
SUPPORTED_OPS = REAL_OPS + COMPLEX_OPS
F32 = np.float32
C64 = np.complex64
ACCEPTED_INDEX_SCHEMAS = ("solver-s1/cases-index@1",)  # 两册字段形状相同，共用一个 schema


def prep_kinds(op):
    """准备链数值域：复数走 c 前缀（s2 spec §4：potrs/potri 前置 cpotrf 同精度，
    complex64），实数走 s 前缀（float32）。"""
    if op in COMPLEX_OPS:
        return C64, "c"
    return F32, "s"


class ContractError(RuntimeError):
    """输入不满足 spec 契约或运行前置——报错停下，不猜测、不静默降级。"""


class PrepFailed(RuntimeError):
    """低精度准备链失败（info≠0 或输出非有限）——按 spec §2.4 记 prep_failed。"""


# ---------------------------------------------------------------------------
# criteria 装载（残差实现唯一来源）
# ---------------------------------------------------------------------------

def load_verdict(criteria_dir):
    """把 criteria 目录挂 sys.path 后 import verdict（verdict 自带该导入形态）。"""
    crit = Path(criteria_dir).resolve()
    if not (crit / "verdict.py").is_file():
        raise ContractError(
            f"criteria 目录 {crit} 下没有 verdict.py——"
            "用 --criteria 指向 repo-task-solver-accept 的 criteria 目录"
        )
    sys.path.insert(0, str(crit))
    import verdict  # noqa: E402
    if Path(getattr(verdict, "__file__", "")).resolve().parent != crit:
        raise ContractError(f"import 到的 verdict 不在 {crit}（sys.path 污染？）")
    return verdict


def _lapack():
    try:
        from scipy.linalg import lapack
    except ImportError as exc:
        raise ContractError(
            "缺 scipy：低精度准备链走 scipy.linalg.lapack 的 s/c 前缀例程，"
            "按 spec §1 应在远程容器内补装 scipy 并记录版本后再运行本脚本"
        ) from exc
    return lapack


# ---------------------------------------------------------------------------
# 低精度准备链（实数 s 前缀 spec §2.4 / 复数 c 前缀 s2 spec §4；被测精度参考实现，scipy→LAPACK）
# ---------------------------------------------------------------------------

def _check_low(name, arr, case_id, dt32):
    """低精度例程输出必须仍是该精度（实数 float32 / 复数 complex64）且有限——
    README 1.5 条 7 的降型自检 + 非有限拦截。"""
    if arr.dtype != dt32:
        raise ContractError(
            f"{case_id}: {name} dtype={arr.dtype}，低精度链路输出应为 {np.dtype(dt32).name}"
            "（scipy 静默降型坑，README 1.5 条 7）"
        )
    if not np.isfinite(arr).all():
        raise PrepFailed(f"{case_id}: {name} 含 NaN/Inf")


def _low_potrf(lapack, a32, uplo, case_id, prefix, dt32):
    f32, info = getattr(lapack, prefix + "potrf")(a32, lower=(uplo == "L"), clean=1)
    if info != 0:
        raise PrepFailed(f"{case_id}: {prefix}potrf info={info}")
    _check_low("F32", f32, case_id, dt32)
    return f32


def run_chain(verdict_mod, lapack, case, arrays):
    """跑该 case 的低精度完整准备链（实数 FP32 s 前缀 / 复数 complex64 c 前缀）
    并返回 residual_ratio 值。

    抛 PrepFailed 表示准备失败（调用方记 prep_failed）；其余异常是脚本或数据错误，
    照常向上抛（fail-closed，不吞进 prep_failed）。
    """
    cid, op, uplo = case["case_id"], case["op"], case["uplo"]
    dt32, prefix = prep_kinds(op)
    a32 = np.ascontiguousarray(arrays["A32"])
    if a32.dtype != dt32:
        raise ContractError(
            f"{cid}: A32 dtype={a32.dtype}，{op} 的准备链输入应为 {np.dtype(dt32).name}"
            "（npz 与算子册不匹配？）")
    f32 = _low_potrf(lapack, a32, uplo, cid, prefix, dt32)
    base = op[1:]                                       # potrf/potrs/potri（s/c 前缀共路）
    if base == "potrf":
        return verdict_mod.residual_ratio(
            "DPOT01", a=arrays["A64"], factor=f32, uplo=uplo)
    if base == "potrs":
        b32 = np.ascontiguousarray(arrays["B32"])
        x32, info = getattr(lapack, prefix + "potrs")(f32, b32, lower=(uplo == "L"))
        if info != 0:
            raise PrepFailed(f"{cid}: {prefix}potrs info={info}")
        _check_low("X32", x32, cid, dt32)
        return verdict_mod.residual_ratio("DPOT02", a=a32, b=b32, x=x32)
    if base == "potri":
        c32, info = getattr(lapack, prefix + "potri")(f32, lower=(uplo == "L"))
        if info != 0:
            raise PrepFailed(f"{cid}: {prefix}potri info={info}")
        _check_low("C32", c32, cid, dt32)
        return verdict_mod.residual_ratio("DPOT03", a=a32, ainv=c32, uplo=uplo)
    raise ContractError(f"{cid}: op={op!r} 不在 {SUPPORTED_OPS}")


# ---------------------------------------------------------------------------
# prep_failed 路径自测（人工构造非正定用例，波 1.5 完成条件）
# ---------------------------------------------------------------------------

def prep_failed_selftest(verdict_mod, lapack):
    """人工非正定阵走同一条链路，必须落 prep_failed（potrf info>0）；s/c 两前缀
    各验一条（复数探针在 cpotrf 步即触发，不依赖 criteria 的复数残差支持）。

    实数 A = [[1,2],[2,1]]（特征值 3 与 -1，对称非正定，spotrf 应报 info=2）；
    复数 A = [[1,2-i],[2+i,1]]（Hermitian 非正定，特征值 1±√5）。自测用与真实
    case 完全相同的 run_chain 入口，不另写第二条路径。返回逐探针结果列表。
    """
    probes = [
        ("selftest-nonspd", "spotrf",
         np.array([[1.0, 2.0], [2.0, 1.0]]),
         "A=[[1,2],[2,1]]（对称非正定，特征值 3/-1）"),
        ("selftest-nonhpd", "cpotrf",
         np.array([[1.0, 2.0 - 1.0j], [2.0 + 1.0j, 1.0]]),
         "A=[[1,2-i],[2+i,1]]（Hermitian 非正定，特征值 1±√5）"),
    ]
    results = []
    for cid, op, a64, desc in probes:
        dt32, _ = prep_kinds(op)
        case = {"case_id": cid, "op": op, "uplo": "L"}
        arrays = {"A64": a64, "A32": a64.astype(dt32)}
        try:
            ratio = run_chain(verdict_mod, lapack, case, arrays)
        except PrepFailed as exc:
            results.append({"case": desc, "chain": "run_chain 同一入口",
                            "outcome": str(exc), "status": "prep_failed",
                            "passed": True})
            continue
        raise ContractError(
            f"prep_failed 自测未触发（{op}）：非正定阵竟返回 ratio={ratio}——"
            "准备链的 info 检查失效，停止回填"
        )
    return results


# ---------------------------------------------------------------------------
# 画像核对（波 1.5 完成条件；README 对照见模块 docstring）
# ---------------------------------------------------------------------------

def _stats(vals):
    if not vals:
        return None
    v = sorted(vals)
    return {"count": len(v), "min": v[0], "max": v[-1],
            "median": float(np.median(v))}


def profile_check(rows, ops):
    """逐算子统计 + 判读。实数沿 S1：potrf/potri 底噪 <1 硬断言、potrs 上浮布尔
    判读；复数三算子只输出画像统计，不设硬门——实数区间不得盲目继承（B2c 任务
    卡），判读交验证段。本次回填面没有 case 的算子不参与判读（单册回填的常态）；
    有 case 但全 prep_failed 的实数算子仍触发硬断言（沿 S1 语义）。"""
    rows_by_op = {op: [r for r in rows if r["op"] == op] for op in ops}
    by_op = {op: [r["ratio_cpu"] for r in rs if r["ratio_cpu_status"] == "ok"]
             for op, rs in rows_by_op.items()}
    stats = {op: _stats(by_op[op]) for op in ops if rows_by_op[op]}
    hard_fail = []
    for op in ("spotrf", "spotri"):
        if not rows_by_op.get(op):
            continue                    # 该算子不在本次回填面——底噪门不适用
        bad = [v for v in by_op[op] if not v < 1.0]
        if bad or not by_op[op]:
            hard_fail.append(f"{op} 底噪断言不满足（应全部 <1，越界值 {bad}）")
    s_potrf = stats.get("spotrf")
    s_potrs = stats.get("spotrs")
    checks = {}
    if rows_by_op.get("spotrf"):
        checks["potrf_noise_below_1"] = not any("spotrf" in m for m in hard_fail)
    if rows_by_op.get("spotri"):
        checks["potri_noise_below_1"] = not any("spotri" in m for m in hard_fail)
    if rows_by_op.get("spotrs"):
        checks["potrs_uplift_over_potrf_max"] = bool(
            s_potrs and s_potrf and s_potrs["max"] > s_potrf["max"])
        checks["potrs_all_below_floor_30"] = bool(s_potrs and s_potrs["max"] < 30.0)
    complex_present = [op for op in COMPLEX_OPS if rows_by_op.get(op)]
    if complex_present:
        checks["complex_profile"] = {
            "ops": complex_present,
            "hard_gate": None,
            "note": "复数画像不设硬门（实数区间不得盲目继承，B2c 任务卡）；"
                    "统计如实输出，判读交验证段",
        }
    checks["readme_reference"] = {
        "potrf": "README 1.6 ratio_cpu32 实测 0.000~0.008 ulp（平坦底噪，实数）",
        "potrs": "README 2.4 ratio_cpu 实测 0.118~5.748 ulp（上浮，随规模/病态度涨，实数）",
        "spotri": "README 3.4 DPOT03(FP32) 实测 0.000~0.009 ulp（平坦底噪，实数）",
    }
    return stats, checks, hard_fail


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def default_criteria(script_path):
    """镜像树与发布位同构的相对布局：../../repo-task-solver-accept/criteria。"""
    return script_path.resolve().parent.parent.parent / "repo-task-solver-accept" / "criteria"


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog=TOOL,
        description="S1-Cholesky ratio_cpu 回填（spec §2.4；产填充 index.json 与逐 case 报告）",
    )
    parser.add_argument("--cases", required=True,
                        help="B1 产物 cases/ 目录（含 index.json 与 *.npz，只读）")
    parser.add_argument("--out", required=True,
                        help="输出目录（产 <out>/cases/index.json 与 <out>/ratio_cpu_report.json）")
    parser.add_argument("--criteria", default=None,
                        help="repo-task-solver-accept 的 criteria 目录（缺省按镜像树相对布局解析）")
    parser.add_argument("--ops", default=",".join(SUPPORTED_OPS),
                        help="逗号分隔的算子子集，默认两册六算子（与 index 实际所含取交集）")
    args = parser.parse_args(argv)

    ops = tuple(s.strip() for s in args.ops.split(",") if s.strip())
    bad = [o for o in ops if o not in SUPPORTED_OPS]
    if bad or not ops:
        raise ContractError(f"--ops 含不支持的算子 {bad}（可选：{','.join(SUPPORTED_OPS)}）")

    criteria_dir = Path(args.criteria) if args.criteria else default_criteria(Path(__file__))
    verdict_mod = load_verdict(criteria_dir)
    lapack = _lapack()

    selftest = prep_failed_selftest(verdict_mod, lapack)
    for st in selftest:
        print(f"[b2] prep_failed 自测通过：{st['outcome']}")

    cases_dir = Path(args.cases)
    index_path = cases_dir / "index.json"
    if not index_path.is_file():
        raise ContractError(f"{cases_dir} 下没有 index.json（应指向 B1 产物 cases/ 目录）")
    with index_path.open(encoding="utf-8") as fh:
        index = json.load(fh)
    if index.get("schema") not in ACCEPTED_INDEX_SCHEMAS:
        raise ContractError(
            f"index schema={index.get('schema')!r} 不在 {ACCEPTED_INDEX_SCHEMAS}")

    rows = []
    n_ok = n_prep_failed = 0
    for entry in index["cases"]:
        if entry["op"] not in ops:
            continue
        cid = entry["case_id"]
        npz_path = cases_dir / Path(entry["npz"]).name
        with np.load(npz_path) as npz:
            arrays = {k: npz[k] for k in npz.files}
        missing = {"A64", "A32"} - set(arrays)
        if entry["op"].endswith("potrs"):
            missing |= {"B64", "B32"} - set(arrays)
        if missing:
            raise ContractError(f"{cid}: npz 缺数组 {sorted(missing)}（spec §2.2）")
        try:
            ratio = run_chain(verdict_mod, lapack, entry, arrays)
        except PrepFailed as exc:
            entry["ratio_cpu"] = None
            entry["ratio_cpu_status"] = "prep_failed"
            n_prep_failed += 1
            print(f"[b2] {cid}: prep_failed（{exc}）——兜底不可用")
        else:
            entry["ratio_cpu"] = float(ratio)
            entry["ratio_cpu_status"] = "ok"
            n_ok += 1
            print(f"[b2] {cid}: op={entry['op']} n={entry['n']} uplo={entry['uplo']} "
                  f"source={entry['source']} ratio_cpu={ratio:.6g}")
        rows.append({k: entry[k] for k in
                     ("case_id", "op", "source", "n", "nrhs", "uplo",
                      "ratio_cpu", "ratio_cpu_status")})

    if not rows:
        raise ContractError(f"index.json 里没有 ops={','.join(ops)} 的 case")

    stats, checks, hard_fail = profile_check(rows, ops)

    import scipy
    index["ratio_cpu_fill"] = {
        "tool": {"name": TOOL, "ver": TOOL_VER},
        "spec": "dev-doc/solver/solver-s1-cholesky-spec.md#2.4",
        "chain": "低精度完整准备链（实数 FP32 s 前缀 / 复数 complex64 c 前缀；"
                 "potrs/potri 先同前缀 potrf），scipy.linalg.lapack",
        "residual_impl": "repo-task-solver-accept/criteria/verdict.residual_ratio（唯一实现）",
        "env": {"numpy": np.__version__, "scipy": scipy.__version__},
    }

    out_cases = Path(args.out) / "cases"
    out_cases.mkdir(parents=True, exist_ok=True)
    out_index = out_cases / "index.json"
    with out_index.open("w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    report = {
        "schema": "solver-s1/ratio-cpu-report@1",
        "spec": "dev-doc/solver/solver-s1-cholesky-spec.md#2.4 + #3 波1.5",
        "tool": index["ratio_cpu_fill"]["tool"],
        "env": index["ratio_cpu_fill"]["env"],
        "inputs": {"cases_dir": str(cases_dir), "index": str(index_path)},
        "filled": {"ok": n_ok, "prep_failed": n_prep_failed, "total": len(rows)},
        "prep_failed_selftest": selftest,
        "per_case": rows,
        "profile_stats": stats,
        "profile_checks": checks,
        "hard_fail": hard_fail,
    }
    report_path = Path(args.out) / "ratio_cpu_report.json"
    with report_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"[b2] 回填完成：ok={n_ok} prep_failed={n_prep_failed} → {out_index}")
    for op in ops:
        s = stats.get(op)
        if s:
            print(f"[b2] {op}: min={s['min']:.6g} median={s['median']:.6g} "
                  f"max={s['max']:.6g}（{s['count']} case）")
    print(f"[b2] 画像判读：{ {k: v for k, v in checks.items() if k != 'readme_reference'} }")
    if hard_fail:
        for msg in hard_fail:
            print(f"[b2] 硬断言失败：{msg}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ContractError as exc:
        print(f"[b2] 契约/前置错误：{exc}", file=sys.stderr)
        sys.exit(2)
