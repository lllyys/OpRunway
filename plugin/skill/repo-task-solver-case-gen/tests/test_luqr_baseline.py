# -*- coding: utf-8 -*-
"""LU/QR 性能基线支路回归（LQ-13，case-gen）。

`make_baseline.py` 原为 Cholesky 专用（几何键 n/lda/uplo/nrhs/ldb/batch，缺 perf 即
退 2）。本文件锁住新支路的关键不变量，并确认 Cholesky 原口径未受影响：

- 16 接口几何键取自 `freeze_canonical.LUQR_OP_FIELDS`（getrf 的 pivot、mqr 的
  side/trans、gels 的 ldda/lddb/lddx 等），逐字段不符即报错；
- 缺 GPU 计时（perf=null）仅对 LU/QR 放宽：matched=false + perf_evidence="missing_perf"，
  不抛错；Cholesky 三算子沿用原口径（缺耗时即 InputError）；
- 无 bench_key（bench 覆盖子集）记 matched=false、dup_count=null，不报错。
"""
import json

import make_baseline as mb


def _bench(tmp, rel, cases):
    path = tmp / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")


def test_luqr_op_registry_covers_16():
    assert len(mb.LUQR_OPS) == 16
    # 基础算子派生：orgqr/ungqr -> gqr、ormqr/unmqr -> mqr
    assert mb.LUQR_OPS["sorgqr"] == "gqr"
    assert mb.LUQR_OPS["cungqr"] == "gqr"
    assert mb.LUQR_OPS["sormqr"] == "mqr"
    assert mb.LUQR_OPS["cunmqr"] == "mqr"
    assert mb.LUQR_OPS["sgetribatched"] == "getribatched"
    # Cholesky 算子不在 LU/QR 表里
    assert mb.luqr_geometry_fields("spotrf") is None


def test_check_entry_matches_luqr_fields():
    case = {"op": "sormqr", "m": 8, "n": 4, "k": 2, "side": "LEFT",
            "trans": "N", "lda": 8, "ldc": 8}
    good = dict(case, iters=25, check=True)
    assert mb.check_entry_matches(case, good) == []
    bad = dict(good, side="RIGHT")
    msgs = mb.check_entry_matches(case, bad)
    assert msgs and "side" in msgs[0]

    gcase = {"op": "sgels", "m": 16, "n": 8, "nrhs": 3, "ldda": 16, "lddb": 16, "lddx": 16}
    assert mb.check_entry_matches(gcase, dict(gcase)) == []
    assert mb.check_entry_matches(gcase, dict(gcase, lddx=8)) != []


def test_check_entry_matches_cholesky_unchanged():
    case = {"op": "spotrs", "n": 8, "lda": 8, "uplo": "L", "nrhs": 2, "ldb": 8}
    entry = {"n": 8, "lda": 8, "uplo": "LOWER", "nrhs": 2, "ldb": 8}
    assert mb.check_entry_matches(case, entry) == []
    assert mb.check_entry_matches(case, dict(entry, uplo="UPPER")) != []


def test_luqr_missing_perf_lenient(tmp_path):
    _bench(tmp_path, "sgetribatched/bench_result.json",
           [{"n": 2, "batch": 100, "lda": 2, "ldc": 2, "iters": 25, "check": True,
             "perf": None}])
    case = {"case_id": "sgetribatched-0001", "op": "sgetribatched", "n": 2, "batch": 100,
            "lda": 2, "ldc": 2, "bench_key": {"file": "sgetribatched/bench_result.json",
                                              "entry": 0}}
    rows = mb.build_baseline([case], tmp_path)
    row = rows["sgetribatched"][0]
    assert row["matched"] is False
    assert row["perf_evidence"] == mb.PERF_EVIDENCE_MISSING
    assert row["min_ms"] is None and row["avg_ms"] is None
    assert row["dup_count"] == 1     # 定位成功，重复计数仍有意义


def test_luqr_matched_perf(tmp_path):
    _bench(tmp_path, "sgetrf/bench_result.json",
           [{"m": 64, "n": 64, "lda": 64, "pivot": True, "iters": 25, "check": True,
             "perf": {"min_ms": 0.1, "max_ms": 0.2, "avg_ms": 0.15}}])
    case = {"case_id": "sgetrf-0001", "op": "sgetrf", "m": 64, "n": 64, "lda": 64,
            "pivot": True, "bench_key": {"file": "sgetrf/bench_result.json", "entry": 0}}
    row = mb.build_baseline([case], tmp_path)["sgetrf"][0]
    assert row["matched"] is True and row["avg_ms"] == 0.15
    assert "perf_evidence" not in row


def test_cholesky_missing_perf_still_strict(tmp_path):
    """Cholesky 算子缺 perf 仍按原口径报错（退 2），不放宽。"""
    _bench(tmp_path, "spotrf/bench_result.json",
           [{"n": 8, "lda": 8, "uplo": "LOWER", "perf": None}])
    case = {"case_id": "spotrf-0001", "op": "spotrf", "n": 8, "lda": 8, "uplo": "L",
            "bench_key": {"file": "spotrf/bench_result.json", "entry": 0}}
    try:
        mb.build_baseline([case], tmp_path)
    except mb.InputError as exc:
        assert "perf" in str(exc)
    else:
        raise AssertionError("Cholesky 缺 perf 应仍抛 InputError")


def test_no_bench_key_is_not_matched(tmp_path):
    case = {"case_id": "sgetribatched-0002", "op": "sgetribatched", "n": 4, "batch": 8,
            "lda": 4, "ldc": 4, "bench_key": None}
    row = mb.build_baseline([case], tmp_path)["sgetribatched"][0]
    assert row["matched"] is False and row["dup_count"] is None
    assert "perf_evidence" not in row