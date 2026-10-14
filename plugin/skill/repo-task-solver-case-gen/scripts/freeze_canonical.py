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


# ---------------------------------------------------------------------------
# LU / QR 四册（--book lu_real|lu_complex|qr_real|qr_complex）
#
# 与 Cholesky 三册的差异：无 uplo 轴；几何键按「基础算子字段表」驱动；枚举
# trans/side 在 cases.json 为 int（N/T/C=0/1/2、LEFT/RIGHT=0/1），在
# bench_result.json 为字符串（"N"/"T"/"C"、"LEFT"/"RIGHT"）；本册不做 s1 子集
# 裁剪（纯脚本形态，全部 case 入册，s1_subset 恒 true）。1014 用例目录为每册
# 四算子（LU: getrf/getri/getriBatched/matinvBatched；QR: geqrf/gqr/mqr/gels），
# Xlarft 不在范围内（无用例目录）；getri 只提供 cases_brief.json（无 cases.json），
# 冻结时按此回退。
# ---------------------------------------------------------------------------

ENUM_TRANS = {0: "N", 1: "T", 2: "C"}       # cases.json int -> 字母
ENUM_SIDE = {0: "LEFT", 1: "RIGHT"}

# 基础算子 -> 参与几何键与落卡的字段（有序）
LUQR_OP_FIELDS = {
    "getrf": ["m", "n", "lda", "pivot"],
    "getri": ["n", "lda", "pivot"],
    "getribatched": ["n", "batch", "lda", "ldc"],
    "matinvbatched": ["n", "batch", "lda", "lda_inv"],
    "geqrf": ["m", "n", "lda"],
    "gqr": ["m", "n", "k", "lda"],
    "mqr": ["m", "n", "k", "side", "trans", "lda", "ldc"],
    "gels": ["m", "n", "nrhs", "ldda", "lddb", "lddx"],
}

# 任务书维度范围（LQ-7 网格刷新：越界 case 在冻结阶段裁掉，不进入册；gqr/mqr 的
# k 允许 0（结构性约束 0<=k<=min(m,n)，不在此表判））
LUQR_RANGES = {
    "getrf": {"m": (1, 4096), "n": (1, 4096)},
    "getri": {"n": (1, 4096)},
    "getribatched": {"n": (1, 4096), "batch": (1, 1000000)},
    "matinvbatched": {"n": (1, 32), "batch": (1, 1000000)},
    "geqrf": {"m": (1, 4096), "n": (1, 4096)},
    "gqr": {"m": (1, 4096), "n": (1, 4096), "k": (0, 4096)},
    "mqr": {"m": (1, 4096), "n": (1, 4096), "k": (0, 4096)},
    "gels": {"m": (1, 4096), "n": (1, 4096), "nrhs": (1, 128)},
}

# 4GiB 内存约束（假定 A-4 / 任务书「输入数据占用内存 ≤ 4G」）：单 case 主输入字节
# 下界估计超限即裁（LQ-7）。单元素字节：实数 float32=4、复数 complex64=8。
FOUR_GIB = 4 * (1024 ** 3)
LUQR_ITEM_BYTES = {False: 4, True: 8}


def _luqr_case_bytes(kind, complex_, c):
    """单 case 主输入数组字节数下界（LQ-7；与 verify/contract_check.py 同模型，
    只估矩阵本体，不含 workspace）。字段缺失按 0/缺省计。"""
    item = LUQR_ITEM_BYTES[bool(complex_)]
    m, n = c.get("m") or 0, c.get("n") or 0
    lda = c.get("lda") or m or n
    batch = c.get("batch") or 1
    if kind in ("getrf", "geqrf"):
        return lda * n * item
    if kind == "getri":
        return lda * n * item
    if kind in ("getribatched", "matinvbatched"):
        return lda * n * item * batch
    if kind == "gqr":
        return lda * (c.get("k") or n) * item
    if kind == "mqr":
        return (lda * (c.get("k") or n) + c.get("ldc", m) * n) * item
    if kind == "gels":
        return (c.get("ldda", m) * n + c.get("lddb", m) * (c.get("nrhs") or 1)) * item
    return 0


def _luqr_refresh_grid(kind, complex_, canon):
    """网格刷新（LQ-7）：裁掉维度越界与超 4GiB case。

    返回 (kept, dropped_out_of_range, dropped_over_4gib, oor_examples)；
    oor_examples 至多 5 条，供对账追溯（首个越界字段/值/允许范围）。
    """
    ranges = LUQR_RANGES[kind]
    kept, oor_n, g4_n, examples = [], 0, 0, []
    for c in canon:
        bad = [(f, c.get(f), rng) for f, rng in ranges.items()
               if c.get(f) is None or not (rng[0] <= c[f] <= rng[1])]
        if bad:
            oor_n += 1
            if len(examples) < 5:
                f, v, rng = bad[0]
                examples.append({"field": f, "v": v, "range": list(rng)})
            continue
        if _luqr_case_bytes(kind, complex_, c) > FOUR_GIB:
            g4_n += 1
            continue
        kept.append(c)
    return kept, oor_n, g4_n, examples

# 册 -> {family, complex, ops(op->基础算子), op_code, spec, taskbook}
LUQR_BOOKS = {
    "lu_real": {
        "family": "lu", "complex": False,
        "ops": {"sgetrf": "getrf", "sgetri": "getri",
                "sgetribatched": "getribatched", "smatinvbatched": "matinvbatched"},
        "op_code": {"sgetrf": 11, "sgetri": 12, "sgetribatched": 13, "smatinvbatched": 14},
        "spec": "lq-plan §1（实数 LU 册，任务书 §3.2）",
        "taskbook": "Atlas950_Sgetrf_Sgetri_SgetriBatched_SmatinvBatched_task_doc.md",
    },
    "lu_complex": {
        "family": "lu", "complex": True,
        "ops": {"cgetrf": "getrf", "cgetri": "getri",
                "cgetribatched": "getribatched", "cmatinvbatched": "matinvbatched"},
        "op_code": {"cgetrf": 15, "cgetri": 16, "cgetribatched": 17, "cmatinvbatched": 18},
        "spec": "lq-plan §1（复数 LU 册，任务书 §3.2）",
        "taskbook": "Atlas950_Cgetrf_Cgetri_CgetriBatched_CmatinvBatched_task_doc.md",
    },
    "qr_real": {
        "family": "qr", "complex": False,
        "ops": {"sgeqrf": "geqrf", "sorgqr": "gqr", "sormqr": "mqr", "sgels": "gels"},
        "op_code": {"sgeqrf": 19, "sorgqr": 20, "sormqr": 21, "sgels": 22},
        "spec": "lq-plan §1（实数 QR 册，任务书 §3.2；Xlarft 范围外）",
        "taskbook": "Atlas950_Sgeqrf_Sorgqr_Sormqr_Xlarft_SSgels_task_doc.md",
    },
    "qr_complex": {
        "family": "qr", "complex": True,
        "ops": {"cgeqrf": "geqrf", "cungqr": "gqr", "cunmqr": "mqr", "cgels": "gels"},
        "op_code": {"cgeqrf": 23, "cungqr": 24, "cunmqr": 25, "cgels": 26},
        "spec": "lq-plan §1（复数 QR 册，任务书 §3.2）",
        "taskbook": "Atlas950_Cgeqrf_Cungqr_Cunmqr_CCgels_task_doc.md",
    },
}


def _norm_luqr(kind, rec):
    """按基础算子字段表归一；cases.json 的 int 枚举转字母，bench 字符串原样保留。"""
    out = {}
    for f in LUQR_OP_FIELDS[kind]:
        v = rec.get(f)
        if f == "trans" and isinstance(v, int):
            v = ENUM_TRANS[v]
        elif f == "side" and isinstance(v, int):
            v = ENUM_SIDE[v]
        out[f] = v
    return out


def _geo_luqr(kind, rec):
    return tuple(_norm_luqr(kind, rec)[f] for f in LUQR_OP_FIELDS[kind])


def freeze_luqr(args, book):
    """LU/QR 册冻结：逐算子去重 → 网格刷新裁剪（维度越界 / 超 4GiB，LQ-7）→ bench
    匹配；裁剪后全量入册（不裁 s1 子集）。"""
    global OP_CODE, SEED_BASE
    OP_CODE = book["op_code"]        # cu_seed 依赖此全局；按册切换 seed 空间
    SEED_BASE = args.seed_base
    task_dir = args.task_dir
    task_label = args.task_dir_label or args.task_dir
    all_cases, recon = [], {}
    for op, kind in book["ops"].items():
        base = os.path.join(task_dir, op)
        cpath = os.path.join(base, "cases.json")
        if not os.path.exists(cpath):          # 1014：getri 只提供 cases_brief.json
            cpath = os.path.join(base, "cases_brief.json")
        cj = json.load(open(cpath))
        bj = json.load(open(os.path.join(base, "bench_result.json")))
        raw = cj["cases"]

        # 去重（键 = 全字段组合，含 iters/check；保首见序）——与 Cholesky 册同规则
        seen, canon = set(), []
        for c in raw:
            key = tuple(sorted((k, v) for k, v in c.items()))
            if key in seen:
                continue
            seen.add(key)
            canon.append(_norm_luqr(kind, c))
        dup_removed = len(raw) - len(canon)
        dedup_total = len(canon)              # 去重后、裁剪前

        # 网格刷新（LQ-7）：裁掉维度越界与超 4GiB 的内存违规 case（任务书 §2.2 /
        # 「输入数据占用内存 ≤ 4G」）。裁剪在 bench 匹配与编号之前，编号连续无空洞。
        canon, dropped_oor, dropped_4g, oor_examples = _luqr_refresh_grid(
            kind, book["complex"], canon)

        # bench 索引：几何键 -> 条目序号（0 起）
        bench_file = f"{op}/bench_result.json"
        bindex = {}
        for i, b in enumerate(bj["cases"]):
            bindex.setdefault(_geo_luqr(kind, b), []).append(i)
        bench_dup = sum(len(v) - 1 for v in bindex.values())

        matched = 0
        for c in canon:
            hits = bindex.get(_geo_luqr(kind, c), [])
            c["_bench"] = {"file": bench_file, "entry": hits[0]} if hits else None
            matched += 1 if hits else 0

        for k, c in enumerate(canon, start=1):
            entry = {"case_id": f"{op}-{k:04d}", "op": op, "source": "cu"}
            entry.update({f: c[f] for f in LUQR_OP_FIELDS[kind]})
            entry["seed"] = cu_seed(op, k)
            entry["bench_key"] = c["_bench"]
            entry["s1_subset"] = True          # 本册全量入册（纯脚本形态，不裁子集）
            all_cases.append(entry)

        # bench 覆盖可小于 case 集（如 sgetribatched 大 batch×n 无 GPU 实测，属预期）；
        # 未匹配 case 的 bench_key 记 null，由 S4 make_baseline 逐条列报，不作 fail。
        recon[op] = {
            "kind": kind, "raw_total": len(raw),
            "dedup_total": dedup_total, "dup_removed": dup_removed,
            "dropped_out_of_range": dropped_oor, "dropped_over_4gib": dropped_4g,
            "out_of_range_examples": oor_examples, "kept_total": len(canon),
            "bench_total": len(bj["cases"]), "bench_matched": matched,
            "bench_missing": len(canon) - matched, "bench_dup_entries": bench_dup,
        }

    doc = {
        "schema": "solver-luqr/canonical_cases@1",
        "spec": book["spec"],
        "frozen_at": datetime.date.today().isoformat(),
        "task_dir": task_label,
        "book": args.book,
        "conventions": {
            "trans": {"cases.json": ENUM_TRANS, "bench_result.json": {"N": "N", "T": "T", "C": "C"}},
            "side": {"cases.json": ENUM_SIDE, "bench_result.json": {"LEFT": "LEFT", "RIGHT": "RIGHT"}},
            "bench_key": "file 相对 task_dir；entry 为该文件 cases 数组 0 起序号；无匹配为 null",
            "bench_coverage": "bench_result.json 可仅覆盖部分 case（大 batch×n 无 GPU 实测），"
                              "未匹配记 bench_key=null，由 S4 make_baseline 逐条列报",
            "seed": {"cu": f"{SEED_BASE} + op_code*1000000 + 编号（op_code: "
                           + " ".join(f"{o}={book['op_code'][o]}" for o in book["ops"]) + "）"},
            "s1_subset": "本册全量 case 入册（纯脚本形态不裁子集），s1_subset 恒 true",
            "scope": "1014 用例目录为每册四算子（LU: getrf/getri/getriBatched/"
                     "matinvBatched；QR: geqrf/gqr/mqr/gels）；Xlarft 无用例目录，"
                     "不出现在 ops；getri 只提供 cases_brief.json（无 cases.json）",
            "grid_refresh": "网格刷新（LQ-7，假定裁掉）：维度越界（任务书 §2.2）与单 case 主输入"
                            "字节 > 4GiB（假定 A-4）的 case 在冻结阶段裁掉，逐算子入对账"
                            "（dropped_out_of_range / dropped_over_4gib），不进册",
        },
        "reconciliation": recon,
    }
    write_canonical(args.out, doc, all_cases)
    print(json.dumps(recon, ensure_ascii=False, indent=1))
    print("total cases:", len(all_cases))
    return 0


def write_canonical(out, doc, all_cases):
    """产物写盘：头部缩进、逐 case 单行（三册/四册同一写法）。"""
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
    ap.add_argument("--book", choices=tuple(BOOKS) + tuple(LUQR_BOOKS), default="real",
                    help="算子册：real=spotrf/spotrs/spotri（缺省，S1 产物同构）；"
                         "complex=cpotrf/cpotrs/cpotri（S2c）；"
                         "batched=四批量算子（S3，--task-dir=实数册 + --task-dir-complex=复数册）；"
                         "lu_real|lu_complex|qr_real|qr_complex=LU/QR 四册（lq-plan §1，--task-dir=该册任务目录）")
    ap.add_argument("--seed-base", type=int, default=923000000,
                    help="cu 种子域基值（seed = 基值 + op_code*1000000 + 编号）；"
                         "缺省 923000000=公开域，产物逐字节不变。验收私集"
                         "（原负责人补充描述 3）用私域基值私下指定，不进交付包")
    ap.add_argument("--std-seed-base", type=int, default=20250912,
                    help="std 补充卡种子域基值（seed = 基值 + n）；缺省 20250912=公开域。"
                         "batched 册无 std 蓝本，该参数不生效")
    args = ap.parse_args()
    if args.book in LUQR_BOOKS:
        return freeze_luqr(args, LUQR_BOOKS[args.book])   # LU/QR 四册独立通路，不动 Cholesky 三册
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
