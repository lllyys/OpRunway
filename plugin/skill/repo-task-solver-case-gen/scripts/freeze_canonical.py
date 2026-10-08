#!/usr/bin/env python3
"""Z 卡·波 0：冻结 canonical cases（实数 S1 spec 2.1；复数 S2c 见 s2 spec §4）。

只读解析 0923 Cholesky 任务目录（--task-dir）的 <op>/cases.json 与
<op>/bench_result.json，产出 canonical JSON（--out）。算子册由 --book 选择：
real=spotrf/spotrs/spotri（缺省，产物即 reports/solver-s1/canonical_cases.json）；
complex=cpotrf/cpotrs/cpotri（S2c，产物 reports/solver-s2c/canonical_complex.json）。
两册规则同构：去重/对账/子集选择/std 补充/seed 同一套代码，仅算子名、op_code
（cu seed 百万位）、spec 指向与产物注记按册切换。实数规则见
reports/solver-s1/canonical_report.md。

S3 batched 册（--book batched，契约 dev-doc/solver/solver-s3-batched-spec.md §1）：
四个批量算子 spotrfBatched/spotrsBatched/cpotrfBatched/cpotrsBatched 一次冻结成
一份产物（reports/solver-s3/canonical_batched.json）。实数两算子取 --task-dir
（实数册根），复数两算子取 --task-dir-complex（复数册根）；bench_key.file 相对
两册共同父目录（产物 task_dir.bench_root），make_baseline 以该父目录作 --bench-dir
即可一次跑四算子。与 real/complex 册的差异（S3 spec §1）：字段加 batch 且入去重键
与 bench 匹配键；potrsBatched 族 nrhs 显式记 1（官方仅支持 nrhs=1，cases.json 无该
字段）；冻结断言 lda==n、（potrs 族）ldb==n；2026-09-27 目录刷新后 154 原始 → 去重
151 逐算子对账（刷新前 192→188）；无 std
蓝本照实声明；子集为「二维代表子集」六例（确定性算法见 pick_batched_subset），
选中六例直接列入对账表。real/complex 册通路与产物逐字节不变。
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
    # S3 批量四算子（S3 spec §1）：op 名与判定卡一致取 camelCase；dirs 值 =（册, 任务子目录）。
    # op_code 接 7/8/9/10 续排，与既有六算子 seed 空间不相交。
    "batched": {
        "ops": ["spotrfBatched", "spotrsBatched", "cpotrfBatched", "cpotrsBatched"],
        "op_code": {"spotrfBatched": 7, "spotrsBatched": 8,
                    "cpotrfBatched": 9, "cpotrsBatched": 10},
        "dirs": {"spotrfBatched": ("real", "spotrfbatched"),
                 "spotrsBatched": ("real", "spotrsbatched"),
                 "cpotrfBatched": ("complex", "cpotrfbatched"),
                 "cpotrsBatched": ("complex", "cpotrsbatched")},
        "spec": "dev-doc/solver/solver-s3-batched-spec.md#1",
        "subset_note": "true=二维代表子集（S3 spec §1 确定性算法，逐 uplo 取 ①min-n·max-batch/"
                       "②max-n·min-batch/③下中位，共六例，不称覆盖）；false=期望集状态「未生成」",
    },
}
OPS = None                              # 以下三个全局在 main 里按 --book 赋值
OP_CODE = None
STD_CASES = None
UPLO_CASES = {0: "L", 1: "U"}          # cases.json 0/1 -> L/U（spec 0 节枚举表）
UPLO_BENCH = {"LOWER": "L", "UPPER": "U"}
N_MIN, N_MAX = 16, 512                  # 本片子集候选池的 n 界（选择规则见报告）
STD_SEED_BASE = 20250912                # cholesky_precision README §1.4：seed = 20250912 + n
                                        # （--std-seed-base 可覆盖；验收私集用私域基值）
STD_CASES_BASE = {                      # 每算子 2 条 std 补充（编号 9001 起；两册同构，按 op 尾名取）
    "potrf": [{"n": 128, "uplo": "L"}, {"n": 512, "uplo": "U"}],
    "potrs": [{"n": 128, "uplo": "L", "nrhs": 16}, {"n": 512, "uplo": "U", "nrhs": 32}],
    "potri": [{"n": 128, "uplo": "L"}, {"n": 512, "uplo": "U"}],
}

# 种子域基值（阶段 5 验收私集，原负责人补充描述 3）：公开集 cu 种子域 = 923000000 +
# op_code*1e6 + 编号。私集与公开集同规格网格、异种子——只换基值（--seed-base），对账/
# 子集/几何规则零改动，产物 schema 不变（消费同接口）。基值由验收侧私下指定且不进交付
# 包；本脚本进 skill 仓不携带任何私域值。默认值 = 公开域，产物逐字节不变。
SEED_BASE = 923000000

def cu_seed(op, ordinal):
    return SEED_BASE + OP_CODE[op] * 1000000 + ordinal

def geo(op, rec):
    if op.endswith("potrs"):
        return (rec["n"], rec["nrhs"], rec["uplo"], rec["lda"], rec["ldb"])
    return (rec["n"], rec["uplo"], rec["lda"])

# ---------------------------------------------------------------------------
# S3 batched 册（S3 spec §1）。real/complex 通路不经过以下任何函数。
# ---------------------------------------------------------------------------

def is_batched_potrs(op):
    """potrs 族批量算子（有 ldb/nrhs 维；nrhs 官方固定 1）。"""
    return op in ("spotrsBatched", "cpotrsBatched")


def geo_batched(rec):
    """batched 匹配键：batch 入键（S3 spec §1）；nrhs 不入键——bench_result.json
    无该字段（cu 固定 nrhs=1：spotrsbatched_bench.cu:208 / cpotrsbatched_bench.cu:212
    的 cntB = ldb*1），canonical 冻结时才显式写 1。potrf 族 ldb 为 None，同构入元组。"""
    return (rec["n"], rec["batch"], rec["uplo"], rec["lda"], rec.get("ldb"))


def pick_batched_subset(canon):
    """二维代表子集（S3 spec §1 确定性算法）：dedup 后按 (n, batch, lda, ldb) 全序
    排序（排序稳定，键相等保首见序），逐 uplo 取三条：
      ① n 最小者中 batch 最大的首条；
      ② n 最大者中 batch 最小的首条；
      ③ 排除①②后排序中位（下中位，下标 (len-1)//2）一条。
    返回 (选中 id 集合, 逐 uplo 角色表 [(uplo, 角色, 条目)…按①②③序])。"""
    picked, roles = set(), []
    for u in ("L", "U"):
        p = sorted((c for c in canon if c["uplo"] == u),
                   key=lambda c: (c["n"], c["batch"], c["lda"], c["ldb"] or 0))
        assert len(p) >= 3, f"uplo={u} 候选不足 3"
        n_min, n_max = p[0]["n"], p[-1]["n"]
        assert n_min < n_max, f"uplo={u} n 无跨度，①②将重合"
        lo_pool = [c for c in p if c["n"] == n_min]
        lo = [c for c in lo_pool if c["batch"] == max(x["batch"] for x in lo_pool)][0]
        hi_pool = [c for c in p if c["n"] == n_max]
        hi = [c for c in hi_pool if c["batch"] == min(x["batch"] for x in hi_pool)][0]
        rest = [c for c in p if c is not lo and c is not hi]
        mid = rest[(len(rest) - 1) // 2]
        picked |= {id(lo), id(mid), id(hi)}
        roles += [(u, "①min-n·max-batch", lo), (u, "②max-n·min-batch", hi),
                  (u, "③lower-median", mid)]
    assert len(picked) == 6, f"子集应 6 例，得 {len(picked)}"
    return picked, roles


def freeze_batched(args, book):
    """batched 册冻结：四算子（实数两目录 + 复数两目录）一次产出一份 canonical。
    对账入 192→188 与选中六例明细（S3 spec §1）；断言失败即停（fail-closed）。"""
    if not args.task_dir_complex:
        ap_err = "--book batched 需要 --task-dir-complex（复数册根目录）"
        print(ap_err, file=sys.stderr)
        return 2
    real_label = args.task_dir_label or args.task_dir
    cplx_label = args.task_dir_complex_label or args.task_dir_complex
    root_dir = {"real": args.task_dir, "complex": args.task_dir_complex}
    root_label = {"real": real_label, "complex": cplx_label}
    bench_root = os.path.dirname(real_label.rstrip("/"))
    assert bench_root == os.path.dirname(cplx_label.rstrip("/")), (
        "两册标识不同父目录，bench_root 无法唯一：%r / %r" % (real_label, cplx_label))

    all_cases, recon = [], {}
    for op in book["ops"]:
        which, sub = book["dirs"][op]
        base = os.path.join(root_dir[which], sub)
        cj = json.load(open(os.path.join(base, "cases.json")))
        bj = json.load(open(os.path.join(base, "bench_result.json")))
        raw = cj["cases"]
        potrs = is_batched_potrs(op)

        # 归一 + 去重（键 = 全参数组合，含 iters/check；保首见序）——与 real 册同规则
        seen, canon = set(), []
        for c in raw:
            key = tuple(sorted((k, v) for k, v in c.items()))
            if key in seen:
                continue
            seen.add(key)
            rec = {"n": c["n"], "batch": c["batch"], "uplo": UPLO_CASES[c["uplo"]],
                   "lda": c["lda"], "ldb": c.get("ldb")}
            # 冻结断言（S3 spec §1）：本片无 padding
            assert rec["lda"] == rec["n"], f"{op}: lda={rec['lda']} != n={rec['n']}"
            if potrs:
                assert rec["ldb"] == rec["n"], f"{op}: ldb={rec['ldb']} != n={rec['n']}"
            canon.append(rec)
        # 154→去重对账（2026-09-27 目录刷新实查 151；不同即目录/输入有误，停下）。
        # 刷新内容：删大 n（>4096）37 条、增 3 条 batch=1000000（n=2/8/18, uplo=L）。
        assert len(raw) == 154 and len(canon) == 151, (
            f"{op}: raw={len(raw)} dedup={len(canon)}，与刷新后对账（154→151）不符")

        # bench 索引（含 batch 的匹配键）；重复条目取首见序号，条数如实入对账
        bench_file = f"{os.path.basename(root_label[which].rstrip('/'))}/{sub}/bench_result.json"
        bindex = {}
        for i, b in enumerate(bj["cases"]):
            rec = {"n": b["n"], "batch": b["batch"], "uplo": UPLO_BENCH[b["uplo"]],
                   "lda": b["lda"], "ldb": b.get("ldb")}
            bindex.setdefault(geo_batched(rec), []).append(i)
        bench_dup = sum(len(v) - 1 for v in bindex.values())

        matched = 0
        for c in canon:
            hits = bindex.get(geo_batched(c), [])
            c["_bench"] = {"file": bench_file, "entry": hits[0]} if hits else None
            matched += 1 if hits else 0
        assert matched == len(canon), f"{op}: bench 匹配 {matched}/{len(canon)} 不全"

        subset, roles = pick_batched_subset(canon)

        # 落卡（<op>-0001 起保序；无 std 蓝本，S3 spec §1 照实声明）
        id2cid = {}
        for k, c in enumerate(canon, start=1):
            cid = f"{op}-{k:04d}"
            id2cid[id(c)] = cid
            all_cases.append({
                "case_id": cid, "op": op, "source": "cu",
                "n": c["n"], "batch": c["batch"],
                "nrhs": 1 if potrs else None,          # 官方仅支持 nrhs=1，显式写入
                "uplo": c["uplo"], "lda": c["lda"], "ldb": c["ldb"],
                "seed": cu_seed(op, k), "bench_key": c["_bench"],
                "s1_subset": id(c) in subset,
            })
        recon[op] = {
            "raw_total": len(raw), "dedup_total": len(canon),
            "dup_removed": len(raw) - len(canon),
            "bench_total": len(bj["cases"]), "bench_matched": matched,
            "bench_dup_entries": bench_dup,
            "s1_cu_selected": 6, "s1_std_added": 0,
            "not_generated": len(canon) - 6,
            # 选中六例直接列出（S3 spec §1「冻结时直接列出选中六例入对账表」）
            "s1_subset_cases": [
                {"case_id": id2cid[id(c)], "role": f"{u}/{role}",
                 "n": c["n"], "batch": c["batch"], "uplo": c["uplo"],
                 "lda": c["lda"], "ldb": c["ldb"],
                 "bench_entry": c["_bench"]["entry"] if c["_bench"] else None}
                for u, role, c in roles
            ],
        }

    doc = {
        "schema": "solver-s1/canonical_cases@1",  # 字段形状同族（加 batch 列），共用 schema
        "spec": book["spec"],
        "frozen_at": datetime.date.today().isoformat(),
        "task_dir": {"real": real_label, "complex": cplx_label, "bench_root": bench_root},
        "book": "batched",
        "conventions": {
            "uplo": {"cases.json": {"0": "L", "1": "U"},
                     "bench_result.json": {"LOWER": "L", "UPPER": "U"}},
            "bench_key": "file 相对 task_dir.bench_root（两册共同父目录）；"
                         "entry 为该文件 cases 数组 0 起序号；重复条目取首见序号",
            "seed": {"cu": f"{SEED_BASE} + op_code*1000000 + 编号（op_code: "
                           + " ".join(f"{o}={book['op_code'][o]}" for o in book["ops"]) + "）"},
            "batch": "batch 入去重键与 bench 匹配键（S3 spec §1）",
            "nrhs": "potrsBatched 族官方仅支持 nrhs=1（cu 的 cntB=ldb*1："
                    "spotrsbatched_bench.cu:208 / cpotrsbatched_bench.cu:212）；"
                    "cases.json 无该字段，冻结时显式写 1；potrfBatched 族置 null",
            "s1_subset": book["subset_note"],
            "std_supplement": "batched 无 std 蓝本，照实声明（S3 spec §1）：随机 SPD/HPD、"
                              "非正定、INF/NAN、确定性等期望集覆盖类别保留证据不足，"
                              "不因六例而缩减",
            "null_fields": "potrfBatched 族无 nrhs/ldb，置 null",
        },
        "reconciliation": recon,
    }
    write_canonical(args.out, doc, all_cases)
    print(json.dumps(recon, ensure_ascii=False, indent=1))
    sel = [c for c in all_cases if c["s1_subset"]]
    for c in sel:
        print(c["case_id"], "n=%d" % c["n"], "batch=%d" % c["batch"],
              "uplo=" + c["uplo"], "seed=%d" % c["seed"],
              "bench=%s" % (c["bench_key"]["entry"] if c["bench_key"] else None))
    print("total cases:", len(all_cases), "subset:", len(sel))
    return 0


def write_canonical(out, doc, all_cases):
    """产物写盘：头部缩进、逐 case 单行（real/complex/batched 三册同一写法）。"""
    with open(out, "w", encoding="utf-8") as f:
        head = {k: v for k, v in doc.items() if k != "cases"}
        s = json.dumps(head, ensure_ascii=False, indent=2)
        f.write(s[:-2] + ',\n  "cases": [\n')
        f.write(",\n".join("    " + json.dumps(c, ensure_ascii=False) for c in all_cases))
        f.write("\n  ]\n}\n")

def main():
    global TASK_DIR, TASK_DIR_REL, OUT, OPS, OP_CODE, STD_CASES, SEED_BASE, STD_SEED_BASE
    ap = argparse.ArgumentParser(description="冻结 canonical_cases.json（spec 2.1）")
    ap.add_argument("--task-dir", required=True,
                    help="算子族任务目录（含 <op>/cases.json 与 <op>/bench_result.json）")
    ap.add_argument("--task-dir-label", default=None,
                    help="写进产物 task_dir 字段的相对标识；缺省用 --task-dir 原文")
    ap.add_argument("--task-dir-complex", default=None,
                    help="batched 册专用：复数任务目录（与 --task-dir 同父目录）")
    ap.add_argument("--task-dir-complex-label", default=None,
                    help="batched 册专用：复数目录的相对标识；缺省用 --task-dir-complex 原文")
    ap.add_argument("--out", required=True, help="canonical JSON 输出路径")
    ap.add_argument("--book", choices=tuple(BOOKS), default="real",
                    help="算子册：real=spotrf/spotrs/spotri（缺省，S1 产物同构）；"
                         "complex=cpotrf/cpotrs/cpotri（S2c）；"
                         "batched=四批量算子（S3，--task-dir=实数册 + --task-dir-complex=复数册）")
    ap.add_argument("--seed-base", type=int, default=923000000,
                    help="cu 种子域基值（seed = 基值 + op_code*1000000 + 编号）；"
                         "缺省 923000000=公开域，产物逐字节不变。验收私集"
                         "（原负责人补充描述 3）用私域基值私下指定，不进交付包")
    ap.add_argument("--std-seed-base", type=int, default=20250912,
                    help="std 补充卡种子域基值（seed = 基值 + n）；缺省 20250912=公开域。"
                         "batched 册无 std 蓝本，该参数不生效")
    args = ap.parse_args()
    book = BOOKS[args.book]
    OPS = book["ops"]
    OP_CODE = book["op_code"]
    SEED_BASE = args.seed_base
    STD_SEED_BASE = args.std_seed_base
    if args.book == "batched":
        return freeze_batched(args, book)      # batched 册独立通路，不动 real/complex 逐字节输出
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
            "seed": {"cu": f"{SEED_BASE} + op_code*1000000 + 编号（op_code: "
                           + " ".join(f"{o}={OP_CODE[o]}" for o in OPS) + "）",
                     "std": f"{STD_SEED_BASE} + n（repos/solver_tasks-main/cholesky_precision/README.md §1.4）"},
            "s1_subset": book["subset_note"],
            "null_fields": "potrf/potri 无 nrhs/ldb，置 null（spec 2.1）",
        },
        "reconciliation": recon,
        "cases": all_cases,
    })
    write_canonical(OUT, doc, all_cases)          # 逐 case 单行，头部缩进
    print(json.dumps(recon, ensure_ascii=False, indent=1))
    sel = [c for c in all_cases if c["s1_subset"]]
    for c in sel:
        print(c["case_id"], c["source"], "n=%d" % c["n"], "nrhs=%s" % c["nrhs"],
              "uplo=" + c["uplo"], "seed=%d" % c["seed"],
              "bench=%s" % (c["bench_key"]["entry"] if c["bench_key"] else None))
    print("total cases:", len(all_cases), "subset:", len(sel))
    return 0

if __name__ == "__main__":
    sys.exit(main())
