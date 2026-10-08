# -*- coding: utf-8 -*-
"""HT-2 A0 抽样通路的机械断言（case-gen 侧）。

覆盖清单 → 具名测试对照：

- sample_map 派生确定性与不变量（覆盖/无重复/rep_slot 首槽/每内容非空）  →
  test_sample_map_deterministic_and_invariants
- 非法 k 拒绝                                                           →
  test_derive_rejects_bad_k
- batch<5 退化（k=batch，每内容恰一槽）                                  →
  test_sample_map_small_batch_degenerate
- 内容流谱系：contents == 同 case batch=k 全量直算（逐位）；A 段 == 任意
  batch 全量流头部                                                       →
  test_content_stream_batch_k_equivalence / test_content_stream_a_head
- 三流独立（先派生映射后构造内容不受影响）                              →
  test_three_streams_independent
- expand 往返（逐槽==指派内容、分段==整批、缺指派报错）                 →
  test_expand_roundtrip / test_expand_segment / test_expand_rejects_gap
- gen_batched_case 条目自洽（含 criteria 不可达的 not_computed 分支）    →
  test_gen_batched_case_entry
- fill_ratio_cpu.batched_fill_a0 与 gen 侧 ratio 逐位一致               →
  test_fill_a0_matches_gen
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

import gen_data_cholesky as gd

SCRIPTS = Path(gd.__file__).resolve().parent


def _case(op, n=6, batch=23, seed=902309001):
    potrs = gd.batched_base_op(op).endswith("potrs")
    return {"case_id": f"a0test-{op}", "op": op, "source": "cu", "n": n,
            "batch": batch, "nrhs": 1 if potrs else None, "uplo": "L",
            "lda": n, "ldb": n if potrs else None, "seed": seed}


# ---------------------------------------------------------------------------
# sample_map 派生
# ---------------------------------------------------------------------------

def test_sample_map_deterministic_and_invariants():
    for op in gd.BATCHED_OPS:
        case = _case(op)
        batch = case["batch"]
        sm1 = gd.derive_sample_map(case["seed"], batch)
        sm2 = gd.derive_sample_map(case["seed"], batch)
        assert sm1 == sm2                              # 派生确定性
        k = gd.sampled_content_count(batch)
        assert len(sm1) == k
        covered = sorted(s for e in sm1 for s in e["slots"])
        assert covered == list(range(batch))           # 全覆盖无重复
        for c, e in enumerate(sm1):
            assert e["content_idx"] == c
            assert e["slots"] and e["slots"][0] == e["rep_slot"]
        assert len(json.dumps(sm1)) > 0                # JSON 可序列化（列表形态无整型键）


def test_derive_rejects_bad_k():
    with pytest.raises(gd.ContractError):
        gd.derive_sample_map(1, 10, k=0)
    with pytest.raises(gd.ContractError):
        gd.derive_sample_map(1, 10, k=11)


def test_sample_map_small_batch_degenerate():
    # batch<5：k=batch，每内容恰一槽、即其 rep_slot（全代表，无填充槽）
    sm = gd.derive_sample_map(7, 3)
    assert len(sm) == 3
    for c, e in enumerate(sm):
        assert e["slots"] == [e["rep_slot"]]
    assert sorted(e["rep_slot"] for e in sm) == [0, 1, 2]


# ---------------------------------------------------------------------------
# 内容流谱系
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("op", gd.BATCHED_OPS)
def test_content_stream_batch_k_equivalence(op):
    # 强承诺：contents == 同 case 取 batch=k 的旧全量直算（同一 stream 同序消费）
    case = _case(op)
    k = gd.sampled_content_count(case["batch"])
    contents = gd.build_batched_contents(case)
    ref = gd._build_batched_arrays(dict(case, batch=k))
    assert set(contents) == set(ref)
    for name in contents:
        assert contents[name].tobytes() == ref[name].tobytes(), name
        assert contents[name].shape[0] == k


@pytest.mark.parametrize("op", ("spotrfBatched", "spotrsBatched"))
def test_content_stream_a_head(op):
    # A 段对账：内容 i == 任意 batch 全量流第 i 个矩阵（内容流取头不换种子）
    case = _case(op, batch=23)
    k = gd.sampled_content_count(23)
    contents = gd.build_batched_contents(case)
    full = gd._build_batched_arrays(case)
    for name in ("A64", "A32"):
        assert contents[name].tobytes() == full[name][:k].tobytes(), name


def test_three_streams_independent():
    # 摆放/填充流的消费不影响内容流：先派生映射再构造内容，与直接构造逐位一致
    case = _case("spotrsBatched")
    sm = gd.derive_sample_map(case["seed"], case["batch"])
    a = gd.build_batched_contents(case)
    b = gd.build_batched_contents(case)
    for name in a:
        assert a[name].tobytes() == b[name].tobytes(), name
    assert gd.derive_sample_map(case["seed"], case["batch"]) == sm


# ---------------------------------------------------------------------------
# expand 往返
# ---------------------------------------------------------------------------

def test_expand_roundtrip():
    case = _case("spotrsBatched")
    contents = gd.build_batched_contents(case)
    sm = gd.derive_sample_map(case["seed"], case["batch"])
    exp = gd.expand_sampled_rows(contents, sm, 0, case["batch"])
    for e in sm:
        for s in e["slots"]:
            for name in contents:
                assert exp[name][s].tobytes() == contents[name][e["content_idx"]].tobytes()


def test_expand_segment():
    case = _case("spotrfBatched")
    contents = gd.build_batched_contents(case)
    sm = gd.derive_sample_map(case["seed"], case["batch"])
    exp = gd.expand_sampled_rows(contents, sm, 0, case["batch"])
    for lo, hi in ((0, 1), (7, 19), (18, 23)):
        seg = gd.expand_sampled_rows(contents, sm, lo, hi)
        for name in contents:
            assert seg[name].tobytes() == exp[name][lo:hi].tobytes()


def test_expand_rejects_gap():
    case = _case("spotrfBatched")
    contents = gd.build_batched_contents(case)
    sm = gd.derive_sample_map(case["seed"], case["batch"])
    broken = [dict(e, slots=e["slots"][:-1]) for e in sm]   # 制造缺指派
    with pytest.raises(gd.ContractError):
        gd.expand_sampled_rows(contents, broken, 0, case["batch"])
    with pytest.raises(gd.ContractError):
        gd.expand_sampled_rows(contents, sm, 5, 3)


# ---------------------------------------------------------------------------
# gen_batched_case 条目与回填
# ---------------------------------------------------------------------------

def test_gen_batched_case_entry():
    criteria_dir = gd.resolve_criteria(None)
    for op in gd.BATCHED_OPS:
        case = _case(op)
        k = gd.sampled_content_count(case["batch"])
        entry, info = gd.gen_batched_case(case, criteria_dir)
        assert info["mode"] == "a0" and info["contents"] == k
        assert entry["materialize"] == "gen"
        assert entry["sampled_contents"] == k
        assert entry["sample_map"] == gd.derive_sample_map(case["seed"], case["batch"])
        assert entry["arrays"] == ["A64", "A32"] + (
            ["B64", "B32"] if gd.batched_base_op(op).endswith("potrs") else []) + [
            "golden64", "golden32"]
        if criteria_dir is None:
            assert entry["ratio_cpu_status"] == "not_computed"
            assert entry["ratio_cpu"] is None
        else:
            assert entry["ratio_cpu_status"] in ("ok", "prep_failed")
            assert len(entry["ratio_cpu"]) == k
            n_null = sum(1 for v in entry["ratio_cpu"] if v is None)
            assert entry.get("ratio_cpu_prep_failed", 0) == n_null
            if entry["ratio_cpu_status"] == "ok":
                assert n_null < k


def test_gen_batched_case_not_computed_without_criteria():
    case = _case("spotrfBatched")
    entry, info = gd.gen_batched_case(case, None)
    assert info["mode"] == "a0"
    assert entry["ratio_cpu_status"] == "not_computed"
    assert entry["ratio_cpu"] is None
    assert "ratio_cpu_prep_failed" not in entry


@pytest.mark.skipif(gd.resolve_criteria(None) is None,
                    reason="criteria 不可达（镜像树外单独运行）")
def test_fill_a0_matches_gen():
    # 回填工具与 gen 侧同一 run_chain 入口：ratio 值列表应逐位一致
    sys.path.insert(0, str(SCRIPTS))
    import fill_ratio_cpu as frc
    criteria_dir = gd.resolve_criteria(None)
    case = _case("spotrsBatched")
    entry_add, _ = gd.gen_batched_case(case, criteria_dir)
    index_entry = dict(case, **entry_add)              # index 条目形态（main 落盘即此）
    verdict_mod = frc.load_verdict(criteria_dir)
    ratio, status, binfo = frc.batched_fill_a0(index_entry, verdict_mod, frc._lapack())
    assert binfo["mode"] == "a0" and binfo["contents"] == index_entry["sampled_contents"]
    assert status == index_entry["ratio_cpu_status"]
    assert ratio == index_entry["ratio_cpu"]


# ---------------------------------------------------------------------------
# HT-9：批量 info 契约混合 case（每批量算子 1 例，B3）
# ---------------------------------------------------------------------------

def _subset_cases(op, ns=(5, 8, 12), batch=7):
    """s1 子集形态的合成 case（n 递增保证选择规则可预期：跳过最小取第 2 例）。"""
    potrs = gd.batched_base_op(op).endswith("potrs")
    return [{"case_id": f"ht9-{op}-{n}", "op": op, "source": "cu", "n": n,
             "batch": batch, "nrhs": 1 if potrs else None, "uplo": "L",
             "lda": n, "ldb": n if potrs else None, "seed": 902309001 + n,
             "s1_subset": True} for n in ns]


def test_derive_batched_info_cases_selection():
    for op in gd.BATCHED_OPS:
        sub = _subset_cases(op)
        d1 = gd.derive_batched_info_cases(sub)
        d2 = gd.derive_batched_info_cases(sub)
        assert d1 == d2 and len(d1) == 1       # 每批量算子恰 1 例、派生确定性
        e = d1[0]
        assert e["op"] == op and e["case_purpose"] == "info"
        assert e["case_id"] == f"ht9-{op}-8-info1"      # 跳过最小 n=5 取第 2 例
        assert e["base_case_id"] == f"ht9-{op}-8"
        assert "s1_subset" not in e
        assert e["info_probe"] == gd.INFO_PROBE_OF[
            "potrf" if "potrf" in op else
            "potri" if "potri" in op else "potrs"]
        if e["info_probe"] == "non_posdef":
            assert e["k_expected"] == [1, 4, 0, 0, 0]   # n=8：k 取 [1, n//2]，其余 0
            assert len(e["k_expected"]) == gd.sampled_content_count(e["batch"])
        else:
            assert e["k_expected"] == -1
    # 无 s1_subset 标记的 case 不参与派生
    assert gd.derive_batched_info_cases(
        [dict(c, s1_subset=None) for c in _subset_cases("spotrfBatched")]) == []


@pytest.mark.parametrize("op", gd.BATCHED_OPS)
def test_build_batched_info_arrays_contract(op):
    sub = _subset_cases(op)
    base = [c for c in sub if c["n"] == 8][0]
    (entry,) = gd.derive_batched_info_cases(sub)
    arrays = gd.build_batched_info_arrays(entry)    # 构造性自检（info==k_expected）即断言
    again = gd.build_batched_info_arrays(entry)
    for name in arrays:
        assert arrays[name].tobytes() == again[name].tobytes(), name
    k = gd.sampled_content_count(entry["batch"])
    is_potrs = gd.batched_base_op(op).endswith("potrs")
    assert set(arrays) == ({"A64", "A32", "B64", "B32"} if is_potrs
                           else {"A64", "A32"})
    for name in arrays:
        assert arrays[name].shape[0] == k
    ref = gd.build_batched_contents(base)
    if is_potrs:
        # bad_param_uplo：输入与 base 的输入段逐位一致（参数错与矩阵无关）
        for name in arrays:
            assert arrays[name].tobytes() == ref[name].tobytes(), name
    else:
        # non_posdef：k_expected>0 的内容与 base 输入不同（第 k 阶对角被减大数），
        # 正定内容逐位一致；独立用 scipy lapack 复核 info 契约
        from scipy.linalg import lapack
        gprefix = gd.op_kinds(gd.batched_base_op(op))[2]
        uplo_l = entry["uplo"] == "L"
        for c, kexp in enumerate(entry["k_expected"]):
            if kexp == 0:
                assert arrays["A64"][c].tobytes() == ref["A64"][c].tobytes(), c
            else:
                assert arrays["A64"][c].tobytes() != ref["A64"][c].tobytes(), c
            _, info = getattr(lapack, gprefix + "potrf")(
                np.array(arrays["A64"][c], copy=True), lower=uplo_l, clean=1)
            assert info == kexp, (c, info, kexp)
