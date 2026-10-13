# -*- coding: utf-8 -*-
"""LU/QR 四册冻结通路回归（LQ-10，case-gen）。

Cholesky 三册通路 `freeze_canonical.main` 与 `freeze_luqr` 是同文件两条独立通路。
本文件用合成任务目录锁住 LU/QR 通路的关键不变量，防回归：

- `--book` 分发：`main` 认出 `lu_real` 并落到 `freeze_luqr`，产物 schema 为
  `solver-luqr/canonical_cases@1`（不同于 Cholesky 的 `solver-s1/...`）
- 去重键 = 全字段组合（含 iters/check）：重复条目入 `dup_removed` 对账
- cases.json 缺省回退：getri 只提供 cases_brief.json（1014 口径），仍可冻结入册
- bench 覆盖可为子集：无匹配 case 记 `bench_key=None`、入 `bench_missing`，不 fail
- 网格刷新（LQ-7）：维度越界（任务书 §2.2）与单 case 主输入 > 4GiB（假定 A-4）的 case
  在冻结阶段裁掉，逐算子入 `dropped_out_of_range` / `dropped_over_4gib`，不进册
- seed 空间：`SEED_BASE + op_code*1e6 + 编号`，op_code 按册续排（lu_real 11~14）
- Cholesky 三册通路不受影响（`BOOKS` 与 `LUQR_BOOKS` op_code 不相交）
"""
import json
import sys

import freeze_canonical as fc


def _w(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _mk_lu_real(tmp):
    """合成 lu_real 册：含重复、int/str trans、bench 子集、越界 n。"""
    _w(tmp / "sgetrf" / "cases.json", {"cases": [
        {"m": 64, "n": 64, "lda": 64, "pivot": True, "iters": 25, "check": True},
        {"m": 64, "n": 64, "lda": 64, "pivot": True, "iters": 25, "check": True},   # dup
        {"m": 128, "n": 96, "lda": 128, "pivot": False, "iters": 25, "check": True},
    ]})
    _w(tmp / "sgetrf" / "bench_result.json", {"cases": [
        {"m": 64, "n": 64, "lda": 64, "pivot": True, "iters": 25, "check": True},
        {"m": 128, "n": 96, "lda": 128, "pivot": False, "iters": 25, "check": True},
    ]})
    # 1014：getri 只提供 cases_brief.json（无 cases.json）——覆盖回退路径
    _w(tmp / "sgetri" / "cases_brief.json", {"cases": [
        {"n": 8, "lda": 8, "pivot": True, "iters": 25, "check": True},
        {"n": 16, "lda": 16, "pivot": False, "iters": 25, "check": True},
    ]})
    _w(tmp / "sgetri" / "bench_result.json", {"cases": [
        {"n": 8, "lda": 8, "pivot": True, "iters": 25, "check": True},
        {"n": 16, "lda": 16, "pivot": False, "iters": 25, "check": True},
    ]})
    _w(tmp / "sgetribatched" / "cases.json", {"cases": [
        {"n": 8, "batch": 100, "lda": 8, "ldc": 8, "iters": 25, "check": True},
        {"n": 16, "batch": 32, "lda": 16, "ldc": 16, "iters": 25, "check": True},
    ]})
    _w(tmp / "sgetribatched" / "bench_result.json", {"cases": [       # bench 仅覆盖 1/2
        {"n": 8, "batch": 100, "lda": 8, "ldc": 8, "iters": 25, "check": True},
    ]})
    _w(tmp / "smatinvbatched" / "cases.json", {"cases": [
        {"n": 16, "batch": 64, "lda": 16, "lda_inv": 16, "iters": 25, "check": True},
        {"n": 48, "batch": 8, "lda": 48, "lda_inv": 48, "iters": 25, "check": True},  # n>32 越界
    ]})
    _w(tmp / "smatinvbatched" / "bench_result.json", {"cases": [
        {"n": 16, "batch": 64, "lda": 16, "lda_inv": 16, "iters": 25, "check": True},
        {"n": 48, "batch": 8, "lda": 48, "lda_inv": 48, "iters": 25, "check": True},
    ]})


def test_luqr_main_dispatch_and_canonical(tmp_path):
    _mk_lu_real(tmp_path)
    out = tmp_path / "canonical_cases.json"
    argv = sys.argv
    sys.argv = ["freeze_canonical.py", "--book", "lu_real",
                "--task-dir", str(tmp_path), "--out", str(out)]
    try:
        rc = fc.main()
    finally:
        sys.argv = argv
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == "solver-luqr/canonical_cases@1"
    assert doc["book"] == "lu_real"
    # 去重：sgetrf raw 3 -> dedup 2，dup_removed 1
    r = doc["reconciliation"]
    assert r["sgetrf"]["raw_total"] == 3 and r["sgetrf"]["dedup_total"] == 2
    assert r["sgetrf"]["dup_removed"] == 1
    # bench 子集：sgetribatched 2 case 仅 1 有 bench，不 fail、记 missing
    assert r["sgetribatched"]["bench_matched"] == 1
    assert r["sgetribatched"]["bench_missing"] == 1
    # 网格刷新：smatinvbatched n=48>32 越界裁掉（raw2 -> kept1），入对账
    assert r["smatinvbatched"]["dropped_out_of_range"] == 1
    assert r["smatinvbatched"]["kept_total"] == 1
    # getri 仅有 cases_brief.json：bench 两条皆命中
    assert r["sgetri"]["bench_matched"] == 2

    cases = {c["case_id"]: c for c in doc["cases"]}
    # seed 空间：lu_real op_code sgetrf=11 -> 923000000 + 11e6 + 1
    assert cases["sgetrf-0001"]["seed"] == 923000000 + 11 * 1000000 + 1
    # sgetribatched 第二条无 bench -> bench_key None
    assert cases["sgetribatched-0002"]["bench_key"] is None
    assert cases["sgetribatched-0001"]["bench_key"]["entry"] == 0
    # 全部 case 入册（纯脚本形态不裁子集）
    assert all(c["s1_subset"] is True for c in doc["cases"])
    # getri 在册（仅 cases_brief.json 亦可冻结）；Xlarft 范围外
    assert "sgetri" in doc["reconciliation"]
    assert "xlarft" not in doc["reconciliation"]


def test_luqr_book_registry_integrity():
    # 四册齐备，op_code 全局唯一且与 Cholesky 三册不相交
    assert set(fc.LUQR_BOOKS) == {"lu_real", "lu_complex", "qr_real", "qr_complex"}
    cholesky_codes = {c for b in fc.BOOKS.values() for c in b["op_code"].values()}
    luqr_codes = [c for b in fc.LUQR_BOOKS.values() for c in b["op_code"].values()]
    assert len(luqr_codes) == len(set(luqr_codes))                 # 册内/跨册唯一
    assert not (set(luqr_codes) & cholesky_codes)                  # 与 Cholesky 不相交
    for book in fc.LUQR_BOOKS.values():
        assert len(book["ops"]) == 4
        # 每个 op 的基础算子都在字段表里（几何键可解析）
        for base in book["ops"].values():
            assert base in fc.LUQR_OP_FIELDS


def test_luqr_case_bytes_model():
    """LQ-7 单 case 主输入字节下界模型（实数 4B / 复数 8B，与 contract_check 同式）。"""
    assert fc._luqr_case_bytes("getrf", False, {"m": 100, "n": 100, "lda": 100}) == 100 * 100 * 4
    assert fc._luqr_case_bytes("getrf", True, {"m": 100, "n": 100, "lda": 100}) == 100 * 100 * 8
    # 批量含 batch 因子
    assert (fc._luqr_case_bytes("getribatched", True, {"n": 32, "lda": 32, "batch": 1000})
            == 32 * 32 * 8 * 1000)
    # gels：(ldda*n + lddb*nrhs)*item
    assert (fc._luqr_case_bytes("gels", False, {"m": 8, "n": 4, "nrhs": 2, "ldda": 8, "lddb": 8})
            == (8 * 4 + 8 * 2) * 4)


def test_luqr_grid_refresh_g4ib_dtype_dependent():
    """复数批量超 4GiB 裁掉、实数同维度保留（内存约束随 dtype 变化）。"""
    big = {"n": 1024, "batch": 600, "lda": 1024, "ldc": 1024, "iters": 25, "check": True}
    assert fc._luqr_case_bytes("getribatched", True, big) > fc.FOUR_GIB
    assert fc._luqr_case_bytes("getribatched", False, big) < fc.FOUR_GIB
    kept, oor_n, g4_n, _ = fc._luqr_refresh_grid("getribatched", True, [big])
    assert kept == [] and g4_n == 1 and oor_n == 0
    kept, oor_n, g4_n, _ = fc._luqr_refresh_grid("getribatched", False, [big])
    assert len(kept) == 1 and g4_n == 0


def test_luqr_grid_refresh_drops_out_of_range():
    """维度越界裁掉并记首因（m>4096 / matinvbatched n>32）。"""
    over = {"m": 5000, "n": 64, "lda": 5000, "pivot": True}
    ok = {"m": 64, "n": 64, "lda": 64, "pivot": True}
    kept, oor_n, g4_n, ex = fc._luqr_refresh_grid("getrf", False, [over, ok])
    assert kept == [ok] and oor_n == 1 and g4_n == 0
    assert ex and ex[0]["field"] == "m" and ex[0]["range"] == [1, 4096]
    mat = {"n": 48, "batch": 8, "lda": 48, "lda_inv": 48}
    kept, oor_n, _, _ = fc._luqr_refresh_grid("matinvbatched", False, [mat])
    assert kept == [] and oor_n == 1