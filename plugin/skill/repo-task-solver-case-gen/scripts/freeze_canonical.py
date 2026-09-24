#!/usr/bin/env python3
"""Z 卡·波 0：冻结 canonical cases（实数 S1 spec 2.1；复数 S2c 见 s2 spec §4）。

只读解析 0923 Cholesky 任务目录（--task-dir）的 <op>/cases.json 与
<op>/bench_result.json，产出 canonical JSON（--out）。算子册由 --book 选择：
real=spotrf/spotrs/spotri（缺省，产物即 reports/solver-s1/canonical_cases.json）；
complex=cpotrf/cpotrs/cpotri（S2c，产物 reports/solver-s2c/canonical_complex.json）。
两册规则同构：去重/对账/子集选择/std 补充/seed 同一套代码，仅算子名、op_code
（cu seed 百万位）、spec 指向与产物注记按册切换。实数规则见
reports/solver-s1/canonical_report.md。
"""
import argparse
import datetime
import json, os, sys

# 路径由 CLI 传入（skill 脚本不写死机器路径）；TASK_DIR/OUT 在 main 里赋值。
TASK_DIR = None
TASK_DIR_REL = None
OUT = None

# 算子册（--book）：real=S1 实数三算子；complex=S2c 复数三算子（s2 spec §4，seed 规则同构）。
# op_code 是 cu seed 的百万位，全局唯一：复数接 4/5/6 续排，两册 seed 空间不相交。
BOOKS = {
    "real": {
        "ops": ["spotrf", "spotrs", "spotri"],
        "op_code": {"spotrf": 1, "spotrs": 2, "spotri": 3},
        "spec": "dev-doc/solver/solver-s1-cholesky-spec.md#2.1",
        "subset_note": "true=本片生成；false=期望集状态「未生成」（spec 2.1，不声称全覆盖）",
    },
    "complex": {
        "ops": ["cpotrf", "cpotrs", "cpotri"],
        "op_code": {"cpotrf": 4, "cpotrs": 5, "cpotri": 6},
        "spec": "dev-doc/solver/solver-s2-spec.md#4",
        "subset_note": "true=本片（S2c）生成；false=期望集状态「未生成」（不声称全覆盖）",
    },
}
OPS = None                              # 以下三个全局在 main 里按 --book 赋值
OP_CODE = None
STD_CASES = None
UPLO_CASES = {0: "L", 1: "U"}          # cases.json 0/1 -> L/U（spec 0 节枚举表）
UPLO_BENCH = {"LOWER": "L", "UPPER": "U"}
N_MIN, N_MAX = 16, 512                  # 本片子集候选池的 n 界（选择规则见报告）
STD_SEED_BASE = 20250912                # cholesky_precision README §1.4：seed = 20250912 + n
STD_CASES_BASE = {                      # 每算子 2 条 std 补充（编号 9001 起；两册同构，按 op 尾名取）
    "potrf": [{"n": 128, "uplo": "L"}, {"n": 512, "uplo": "U"}],
    "potrs": [{"n": 128, "uplo": "L", "nrhs": 16}, {"n": 512, "uplo": "U", "nrhs": 32}],
    "potri": [{"n": 128, "uplo": "L"}, {"n": 512, "uplo": "U"}],
}

def cu_seed(op, ordinal):
    return 923000000 + OP_CODE[op] * 1000000 + ordinal

def geo(op, rec):
    if op.endswith("potrs"):
        return (rec["n"], rec["nrhs"], rec["uplo"], rec["lda"], rec["ldb"])
    return (rec["n"], rec["uplo"], rec["lda"])

def main():
    global TASK_DIR, TASK_DIR_REL, OUT, OPS, OP_CODE, STD_CASES
    ap = argparse.ArgumentParser(description="冻结 canonical_cases.json（spec 2.1）")
    ap.add_argument("--task-dir", required=True,
                    help="算子族任务目录（含 <op>/cases.json 与 <op>/bench_result.json）")
    ap.add_argument("--task-dir-label", default=None,
                    help="写进产物 task_dir 字段的相对标识；缺省用 --task-dir 原文")
    ap.add_argument("--out", required=True, help="canonical JSON 输出路径")
    ap.add_argument("--book", choices=tuple(BOOKS), default="real",
                    help="算子册：real=spotrf/spotrs/spotri（缺省，S1 产物同构）；"
                         "complex=cpotrf/cpotrs/cpotri（S2c）")
    args = ap.parse_args()
    book = BOOKS[args.book]
    OPS = book["ops"]
    OP_CODE = book["op_code"]
    STD_CASES = {op: STD_CASES_BASE[op[1:]] for op in OPS}
    TASK_DIR = args.task_dir
    TASK_DIR_REL = args.task_dir_label or args.task_dir
    OUT = args.out
    all_cases, recon = [], {}
    for op in OPS:
        cj = json.load(open(os.path.join(TASK_DIR, op, "cases.json")))
        bj = json.load(open(os.path.join(TASK_DIR, op, "bench_result.json")))
        raw = cj["cases"]

        # 归一 + 去重（键 = 全参数组合，含 iters/check 等全部字段；保首见序）
        seen, canon = set(), []
        for c in raw:
            key = tuple(sorted((k, v) for k, v in c.items()))
            if key in seen:
                continue
            seen.add(key)
            canon.append({
                "n": c["n"], "nrhs": c.get("nrhs"), "uplo": UPLO_CASES[c["uplo"]],
                "lda": c["lda"], "ldb": c.get("ldb"),
            })

        # bench 索引：几何键 -> 条目序号列表（0 起）
        bench_file = f"{op}/bench_result.json"
        bindex = {}
        for i, b in enumerate(bj["cases"]):
            rec = {"n": b["n"], "nrhs": b.get("nrhs"), "uplo": UPLO_BENCH[b["uplo"]],
                   "lda": b["lda"], "ldb": b.get("ldb")}
            bindex.setdefault(geo(op, rec), []).append(i)
        bench_dup = sum(len(v) - 1 for v in bindex.values())

        matched = 0
        for c in canon:
            hits = bindex.get(geo(op, c), [])
            c["_bench"] = {"file": bench_file, "entry": hits[0]} if hits else None
            matched += 1 if hits else 0

        # 本片 cu 子集：候选池 N_MIN<=n<=N_MAX，逐 uplo 侧取 min-n / 下中位 / max-n
        pool = {u: [c for c in canon if c["uplo"] == u and N_MIN <= c["n"] <= N_MAX]
                for u in ("L", "U")}
        subset = set()
        for u in ("L", "U"):
            p = sorted(pool[u], key=lambda c: (c["n"], c["nrhs"] or 0))
            assert len(p) >= 3, f"{op}/{u} 候选池不足 3"
            if op.endswith("potrs"):
                lo = next(c for c in p if c["nrhs"] == 1)                    # min-n 且 nrhs=1
                hi = next(c for c in reversed(p) if c["nrhs"] > 1)           # max-n 且 nrhs>1
                mid = p[(len(p) - 1) // 2]
                if id(mid) in (id(lo), id(hi)):
                    mid = p[(len(p) - 1) // 2 + 1]
            else:
                lo, mid, hi = p[0], p[(len(p) - 1) // 2], p[-1]
            subset |= {id(lo), id(mid), id(hi)}

        # 落卡：cu 主数据（<op>-0001 起，保序）
        n_sel = 0
        for k, c in enumerate(canon, start=1):
            sel = id(c) in subset
            n_sel += 1 if sel else 0
            all_cases.append({
                "case_id": f"{op}-{k:04d}", "op": op, "source": "cu",
                "n": c["n"], "nrhs": c["nrhs"], "uplo": c["uplo"],
                "lda": c["lda"], "ldb": c["ldb"], "seed": cu_seed(op, k),
                "bench_key": c["_bench"], "s1_subset": sel,
            })
        # std 补充（<op>-9001 起，bench 无匹配 -> null，恒入本片子集）
        for j, s in enumerate(STD_CASES[op], start=1):
            all_cases.append({
                "case_id": f"{op}-{9000 + j:04d}", "op": op, "source": "std",
                "n": s["n"], "nrhs": s.get("nrhs"), "uplo": s["uplo"],
                "lda": s["n"], "ldb": s["n"] if op.endswith("potrs") else None,
                "seed": STD_SEED_BASE + s["n"], "bench_key": None, "s1_subset": True,
            })

        assert n_sel == 6 and matched == len(canon) and bench_dup == 0
        recon[op] = {
            "raw_total": len(raw), "dedup_total": len(canon),
            "dup_removed": len(raw) - len(canon),
            "bench_total": len(bj["cases"]), "bench_matched": matched,
            "bench_dup_entries": bench_dup,
            "s1_cu_selected": n_sel, "s1_std_added": len(STD_CASES[op]),
            "not_generated": len(canon) - n_sel,
        }

    doc = {
        "schema": "solver-s1/canonical_cases@1",  # 格式版本号：两册字段形状相同，共用一个 schema
        "spec": book["spec"],
        "frozen_at": datetime.date.today().isoformat(),
        "task_dir": TASK_DIR_REL,
    }
    if args.book != "real":
        # 实数产物字节回归保护：real 册输出字段集与 S1 冻结件逐字节一致，book 字段只在非 real 册出现
        doc["book"] = args.book
    doc.update({
        "conventions": {
            "uplo": {"cases.json": {"0": "L", "1": "U"},
                     "bench_result.json": {"LOWER": "L", "UPPER": "U"}},
            "bench_key": "file 相对 task_dir；entry 为该文件 cases 数组 0 起序号；无匹配为 null",
            "seed": {"cu": "923000000 + op_code*1000000 + 编号（op_code: "
                           + " ".join(f"{o}={OP_CODE[o]}" for o in OPS) + "）",
                     "std": "20250912 + n（repos/solver_tasks-main/cholesky_precision/README.md §1.4）"},
            "s1_subset": book["subset_note"],
            "null_fields": "potrf/potri 无 nrhs/ldb，置 null（spec 2.1）",
        },
        "reconciliation": recon,
        "cases": all_cases,
    })
    with open(OUT, "w", encoding="utf-8") as f:   # 逐 case 单行，头部缩进
        head = {k: v for k, v in doc.items() if k != "cases"}
        s = json.dumps(head, ensure_ascii=False, indent=2)
        f.write(s[:-2] + ',\n  "cases": [\n')
        f.write(",\n".join("    " + json.dumps(c, ensure_ascii=False) for c in all_cases))
        f.write("\n  ]\n}\n")
    print(json.dumps(recon, ensure_ascii=False, indent=1))
    sel = [c for c in all_cases if c["s1_subset"]]
    for c in sel:
        print(c["case_id"], c["source"], "n=%d" % c["n"], "nrhs=%s" % c["nrhs"],
              "uplo=" + c["uplo"], "seed=%d" % c["seed"],
              "bench=%s" % (c["bench_key"]["entry"] if c["bench_key"] else None))
    print("total cases:", len(all_cases), "subset:", len(sel))

if __name__ == "__main__":
    main()
