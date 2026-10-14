# -*- coding: utf-8 -*-
"""LU/QR ratio_cpu 回填支路回归（LQ-13，case-gen）。

`fill_ratio_cpu.run_chain_luqr` 是本册包 ratio_cpu 的唯一回填入口，口径必须与
验收侧 `render_verify._luqr_case_ratio` 同源。本文件锁住关键不变量：

- 16 接口逐算子可跑通，返回键集符合判据面（getri/getrf/QR 单键 ratio_cpu；
  gels 双键同写 ratio_cpu/ratio_opt_cpu）；
- *gqr/*mqr 准备链必须走**低精度前缀例程**（sorgqr/cungqr、sormqr/cunmqr）。
  dorgqr/zungqr 对低精度输入升型到 64/128，会丢同精度链路语义（scipy 静默降型坑，
  README 1.5 条 7）——用调用记录探针锁死，防止回归；
- 批量两族（getriBatched/matinvBatched）A0 回填可跑通且保留实/复前缀（复数不得被
  strip 成实数致复算错）。
"""
import sys
from pathlib import Path

import pytest

import fill_ratio_cpu as F
import gen_data_luqr as G

sys.path.insert(0, str(Path(F.__file__).resolve().parent))
VERDICT = F.load_verdict(F.default_criteria(Path(F.__file__)))
LAPACK = F._lapack()


class _SpyLapack:
    """记录被访问的 LAPACK 例程名（转发到底层真 lapack）。"""

    def __init__(self, real):
        self._real = real
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        return getattr(self._real, name)


SINGLE_CASES = {
    "sgetrf": ({"m": 7, "n": 5, "seed": 1}, ("ratio_cpu",)),
    "cgetrf": ({"m": 7, "n": 5, "seed": 2}, ("ratio_cpu",)),
    "sgetri": ({"n": 6, "seed": 3}, ("ratio_cpu",)),
    "cgetri": ({"n": 6, "seed": 4}, ("ratio_cpu",)),
    "sgeqrf": ({"m": 7, "n": 5, "seed": 5}, ("ratio_cpu",)),
    "cgeqrf": ({"m": 7, "n": 5, "seed": 6}, ("ratio_cpu",)),
    "sorgqr": ({"m": 7, "n": 5, "k": 3, "seed": 7}, ("ratio_cpu",)),
    "cungqr": ({"m": 7, "n": 5, "k": 3, "seed": 8}, ("ratio_cpu",)),
    "sormqr": ({"m": 6, "n": 4, "k": 3, "side": "LEFT", "trans": "N", "seed": 9},
               ("ratio_cpu",)),
    "cunmqr": ({"m": 6, "n": 4, "k": 3, "side": "LEFT", "trans": "N", "seed": 10},
               ("ratio_cpu",)),
    "sgels": ({"m": 7, "n": 7, "nrhs": 2, "seed": 11}, ("ratio_cpu", "ratio_opt_cpu")),
    "cgels": ({"m": 7, "n": 7, "nrhs": 2, "seed": 12}, ("ratio_cpu", "ratio_opt_cpu")),
}

BATCHED_CASES = ("sgetribatched", "cgetribatched", "smatinvbatched", "cmatinvbatched")


def _single(op, fields):
    case = dict(fields)
    case.update({"case_id": f"{op}-0001", "op": op})
    return case


@pytest.mark.parametrize("op", sorted(SINGLE_CASES))
def test_run_chain_luqr_keys_and_finite(op):
    fields, expected = SINGLE_CASES[op]
    case = _single(op, fields)
    arrays = G.build_case_arrays(case)
    updates = F.run_chain_luqr(VERDICT, LAPACK, case, arrays)
    assert tuple(updates) == expected
    for v in updates.values():
        assert isinstance(v, float) and v == v and abs(v) != float("inf")


def test_non_luqr_op_rejected():
    """1014 十六接口全有残差判据（getrs 已移除）；前缀合法但基名未知者仍 fail-closed。"""
    with pytest.raises(F.ContractError):
        F.run_chain_luqr(VERDICT, LAPACK, {"case_id": "x-0001", "op": "sgetxx"}, {})


def test_gels_writes_both_keys():
    case = _single("sgels", SINGLE_CASES["sgels"][0])
    arrays = G.build_case_arrays(case)
    updates = F.run_chain_luqr(VERDICT, LAPACK, case, arrays)
    assert set(updates) == {"ratio_cpu", "ratio_opt_cpu"}
    assert updates["ratio_cpu"] == updates["ratio_opt_cpu"]


@pytest.mark.parametrize("op,low,high", [
    ("sorgqr", "sorgqr", "dorgqr"),
    ("cungqr", "cungqr", "zungqr"),
    ("sormqr", "sormqr", "dormqr"),
    ("cunmqr", "cunmqr", "zunmqr"),
])
def test_gqr_mqr_use_low_precision_prefix(op, low, high):
    """锁死 dtype 升型修复：必须调用低精度前缀例程，不得用 d/z 例程。"""
    case = _single(op, SINGLE_CASES[op][0])
    arrays = G.build_case_arrays(case)
    spy = _SpyLapack(LAPACK)
    F.run_chain_luqr(VERDICT, spy, case, arrays)
    assert low in spy.calls, f"{op} 未走低精度例程 {low}：calls={spy.calls}"
    assert high not in spy.calls, f"{op} 误用升型例程 {high}：calls={spy.calls}"


@pytest.mark.parametrize("op", BATCHED_CASES)
def test_batched_a0_covers_all_four(op):
    entry = {"case_id": f"{op}-0001", "op": op, "n": 5, "batch": 23, "seed": 15}
    ratio, status, info = F.batched_fill_a0(entry, VERDICT, LAPACK)
    assert status == "ok" and info["prep_failed"] == 0
    k = G.sampled_content_count(entry["batch"])
    assert len(ratio) == k
    assert all(v is not None and 0.0 <= v < 5.0 for v in ratio)


def test_batched_a0_keeps_complex_prefix():
    """复数批量必须走 c 前缀（strip 会丢 dtype 致复算错）。"""
    entry = {"case_id": "cgetribatched-0001", "op": "cgetribatched",
             "n": 5, "batch": 23, "seed": 16}
    spy = _SpyLapack(LAPACK)
    ratio, status, _info = F.batched_fill_a0(entry, VERDICT, spy)
    assert status == "ok"
    # getriBatched 的打包 LU/ipiv 由 gen 侧备好，链路只调 getri（c 前缀）
    assert "cgetri" in spy.calls
    assert "sgetri" not in spy.calls and "dgetri" not in spy.calls
    assert all(v is not None for v in ratio)