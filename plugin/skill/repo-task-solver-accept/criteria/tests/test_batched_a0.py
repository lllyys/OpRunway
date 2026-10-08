# -*- coding: utf-8 -*-
"""A0 抽样判定侧驱动的机械断言（HT-2 Step B）：batched_a0 的两层判定——
先余槽 bit-wise 一致性，后 rep_slot 逐内容三层判定（复用 verdict 零改动）。

覆盖清单 → 具名测试对照：

- 一致性核对：全一致 / out32 失配 / infoArray 失配 / 标量 info 跳过
                                                → test_consistency_*
- A0 全链路 PASS（单进程与多进程逐位相同）      → test_judge_a0_pass / test_judge_a0_jobs
- 代表内容差（rep 槽同差）→ 子批 FAIL，序号域是内容下标
                                                → test_judge_a0_rep_fail
- 余槽失配覆盖代表判定（FAIL + 槽位号证据）     → test_judge_a0_consistency_fail
- prep_failed 内容（ratio null→NaN）→ error 不判精度 → test_judge_a0_prep_failed_content
- 结构问题出 error verdict（fail_count=None）   → test_judge_a0_structural_errors
- potrsBatched 标量 info 通路                   → test_judge_a0_potrs_scalar
- 非批量卡拒绝                                  → test_judge_a0_rejects_non_batched

等价/结论断言用 dict == 与精确相等（同一实现同一输入，机械门口径）。
基材沿用 test_batched_parallel：A=[[4,2],[2,5]]，L=[[2,0],[1,2]]；扰动幅度
δ=0.011（越 A6 动态锚点门 → 兜底 FAIL）。
"""
import numpy as np
import pytest

import batched_a0
import cards_cholesky

POTRFB = cards_cholesky.get_card("spotrfBatched")
POTRSB = cards_cholesky.get_card("spotrsBatched")

# 3 个内容（n=2 SPD → L），rep 判定的子批基材（L 为对应 A 的下三角 Cholesky 因子）。
A_MAT = [np.array([[4.0, 2.0], [2.0, 5.0]]),
         np.array([[9.0, 1.0], [1.0, 6.0]]),
         np.array([[5.0, -2.0], [-2.0, 8.0]])]
L_MAT = [np.array([[2.0, 0.0], [1.0, 2.0]]),
         np.array([[3.0, 0.0], [1.0 / 3.0, np.sqrt(53.0) / 3.0]]),
         np.array([[np.sqrt(5.0), 0.0], [-2.0 / np.sqrt(5.0), np.sqrt(36.0 / 5.0)]])]

# potrs 基材：A=diag(2,4)，X=[1,2]ᵀ，B=[2,8]ᵀ（三内容同基材即可）。
AS = np.array([[2.0, 0.0], [0.0, 4.0]])
XS = np.array([[1.0], [2.0]])
BS = np.array([[2.0], [8.0]])

BATCH = 7
SAMPLE_MAP = [
    {"content_idx": 0, "rep_slot": 0, "slots": [0, 3, 5]},
    {"content_idx": 1, "rep_slot": 1, "slots": [1, 4]},
    {"content_idx": 2, "rep_slot": 2, "slots": [2, 6]},
]
K = len(SAMPLE_MAP)


def _expand(mats):
    """按 SAMPLE_MAP 把 k 个内容矩阵铺满 batch 个槽位（A0 展开语义）。"""
    full = np.empty((BATCH,) + np.asarray(mats[0]).shape, dtype=np.float32)
    for e in SAMPLE_MAP:
        for s in e["slots"]:
            full[s] = np.asarray(mats[e["content_idx"]], dtype=np.float32)
    return full


def _potrfb_contents():
    return {
        "A64": np.stack([np.asarray(a, dtype=np.float64) for a in A_MAT]),
        "A32": np.stack([np.asarray(a, dtype=np.float32) for a in A_MAT]),
        "golden64": np.stack([np.asarray(l, dtype=np.float64) for l in L_MAT]),
        "golden32": np.stack([np.asarray(l, dtype=np.float32) for l in L_MAT]),
    }


def _a0_case(contents, ratio_cpu=(0.01, 0.01, 0.01), batch=BATCH, mean=None):
    case = dict(contents)
    case.update({"uplo": "L", "batch": batch,
                 "ratio_cpu": list(ratio_cpu), "ratio_cpu_status": "ok",
                 "sample_map": SAMPLE_MAP})
    if mean is not None:
        case["ratio_cpu_mean"] = mean
    return case


def _dut(out32, info=None, status="ok"):
    return {"out32": out32,
            "info": np.zeros(BATCH, dtype=np.int32) if info is None else info,
            "status": status}


def test_consistency_ok_and_slot_mismatch():
    """全一致返回 []；余槽 out32 一位之差 → 一条失配证据（内容/槽位/参照/字段）。"""
    out32 = _expand(L_MAT)
    assert batched_a0.check_consistency(POTRFB, _dut(out32), SAMPLE_MAP) == []
    bad = out32.copy()
    bad[4] += 0.011                       # 槽 4 属内容 1（rep_slot=1）
    mism = batched_a0.check_consistency(POTRFB, _dut(bad), SAMPLE_MAP)
    assert mism == [{"content_idx": 1, "slot": 4, "ref_slot": 1, "field": "out32"}]


def test_consistency_info_array_and_scalar_skip():
    """infoArray 同内容槽位逐槽比对；标量 info（potrs 族）不逐槽。"""
    out32 = _expand(L_MAT)
    info = np.zeros(BATCH, dtype=np.int32)
    info[6] = 3                           # 槽 6 属内容 2，info 与 rep 槽 2 不同
    mism = batched_a0.check_consistency(POTRFB, _dut(out32, info), SAMPLE_MAP)
    assert mism == [{"content_idx": 2, "slot": 6, "ref_slot": 2, "field": "info"}]
    assert batched_a0.check_consistency(POTRSB, _dut(out32, 0), SAMPLE_MAP) == []


def test_judge_a0_pass():
    """被测 = 内容展开复制品 → 一致性过、代表槽逐内容 PASS（子批 batch=k）。"""
    case = _a0_case(_potrfb_contents(), mean=0.0)
    v = batched_a0.judge_a0(POTRFB, case, _dut(_expand(L_MAT)))
    assert v["numeric"] == "PASS" and v["error"] is None and v["batch"] == K
    assert v["fail_count"] == 0 and v["diagnostics"]["batch"] == K


def test_judge_a0_jobs_equals_serial():
    """jobs>1 的 A0 判定与串行逐位相同（复用 judge_parallel，结论不变）。"""
    case = _a0_case(_potrfb_contents(), mean=0.0)
    dut = _dut(_expand(L_MAT))
    assert batched_a0.judge_a0(POTRFB, case, dut, jobs=1) \
        == batched_a0.judge_a0(POTRFB, case, dut, jobs=3)


def test_judge_a0_rep_fail():
    """内容 2 全部槽位（含 rep 槽）同差 → 一致性过、子批 FAIL；序号域是内容下标。"""
    mats = [m.copy() for m in L_MAT]
    mats[2] = np.asarray(mats[2], dtype=np.float32)
    mats[2][1, 1] += 0.011
    out32 = _expand(mats)
    case = _a0_case(_potrfb_contents(), mean=0.0)
    v = batched_a0.judge_a0(POTRFB, case, _dut(out32))
    assert v["numeric"] == "FAIL" and v["error"] is None
    assert v["batch"] == K and v["fail_count"] == 1
    assert v["first_fail_index"] == 2     # 内容下标（子批序号域），非全批槽位号
    assert v["worst_index"] == 2


def test_judge_a0_consistency_fail():
    """余槽失配覆盖一切：数值 FAIL、槽位号证据（first_fail_index=最小失配槽位）。"""
    mats = [m.copy() for m in L_MAT]
    mats[2] = np.asarray(mats[2], dtype=np.float32)
    mats[2][1, 1] += 0.011                # 内容 2 精度也差（若进 rep 判定应 FAIL）
    out32 = _expand(mats)
    out32[3] += 0.011                     # 槽 3 属内容 0：一致性失配
    case = _a0_case(_potrfb_contents(), mean=0.0)
    v = batched_a0.judge_a0(POTRFB, case, _dut(out32))
    assert v["numeric"] == "FAIL" and v["error"] is None
    assert v["fail_count"] == 1 and v["first_fail_index"] == 3
    assert v["worst"] is None and v["formal"] == "PENDING_RULING"
    assert v["diagnostics"]["a0_mismatches"] == [
        {"content_idx": 0, "slot": 3, "ref_slot": 0, "field": "out32"}]


def test_judge_a0_prep_failed_content():
    """内容 1 ratio=null（prep_failed）：layer1 过仍 PASS；layer1 不过则阈值公式
    有限性校验抛错 → 该内容 error verdict（证据问题不判精度，与全量通路同向）。"""
    mats = [m.copy() for m in L_MAT]
    mats[1] = np.asarray(mats[1], dtype=np.float32)
    mats[1][1, 1] += 0.011
    case = _a0_case(_potrfb_contents(), ratio_cpu=(0.01, None, 0.01), mean=0.0)
    case["ratio_cpu_prep_failed"] = 1     # index 证据字段（判定侧不消费）
    v = batched_a0.judge_a0(POTRFB, case, _dut(_expand(mats)))
    assert v["numeric"] == "FAIL" and v["fail_count"] == 1
    assert v["error"] is not None and v["error"].startswith("矩阵 1:")
    assert "非有限值" in v["error"]       # NaN 基线 → 不可裁，不判精度失败
    # 对照：同基材 layer1 全过（不扰动）时，NaN 基线不触发兜底 → 全 PASS。
    v2 = batched_a0.judge_a0(POTRFB, case, _dut(_expand(L_MAT)))
    assert v2["numeric"] == "PASS" and v2["error"] is None


def test_judge_a0_structural_errors():
    """结构问题出 error verdict（fail_count=None，上层不得当精度 FAIL）。"""
    case = _a0_case(_potrfb_contents(), batch=BATCH)
    v = batched_a0.judge_a0(POTRFB, case, {"out32": L_MAT[0], "info": 0, "status": "ok"})
    assert v["fail_count"] is None and v["error"] is not None
    v = batched_a0.judge_a0(POTRFB, dict(case, batch=BATCH + 1), _dut(_expand(L_MAT)))
    assert v["fail_count"] is None and "不一致" in v["error"]
    v = batched_a0.judge_a0(POTRFB, {k: x for k, x in case.items() if k != "sample_map"},
                            _dut(_expand(L_MAT)))
    assert v["fail_count"] is None and "sample_map" in v["error"]
    v = batched_a0.judge_a0(POTRFB, case, {"out32": _expand(L_MAT), "info": 0, "status": "prep_failed"})
    assert v["fail_count"] is None and "prep" in v["error"]


def test_judge_a0_potrs_scalar():
    """potrsBatched 标量 info 通路：一致性核对不逐槽，代表判定照常。"""
    contents = {
        "A64": np.stack([AS] * K), "A32": np.stack([AS.astype(np.float32)] * K),
        "B64": np.stack([BS] * K), "B32": np.stack([BS.astype(np.float32)] * K),
        "golden64": np.stack([XS] * K), "golden32": np.stack([XS.astype(np.float32)] * K),
    }
    case = _a0_case(contents, ratio_cpu=(0.5, 0.5, 0.5), mean=0.0)
    v = batched_a0.judge_a0(POTRSB, case, _dut(_expand([XS] * K), info=0))
    assert v["numeric"] == "PASS" and v["error"] is None and v["batch"] == K


def test_judge_a0_rejects_non_batched():
    """非批量卡调用属消费方误用：显式拒绝（不静默降级）。"""
    card = cards_cholesky.get_card("spotrf")
    with pytest.raises(ValueError):
        batched_a0.judge_a0(card, {"sample_map": SAMPLE_MAP}, _dut(_expand(L_MAT)))


# ---------------------------------------------------------------------------
# HT-9：批量 info 契约条目（case_purpose=="info"，judge_a0 顶部分流，只比 info）
# ---------------------------------------------------------------------------

def _info_case(k_expected=(0, 2, 0)):
    case = _a0_case(_potrfb_contents())
    case.update({"case_purpose": "info", "info_probe": "non_posdef",
                 "k_expected": list(k_expected)})
    return case


def _info_from_k_expected(k_expected):
    """按 k_expected 经 sample_map 展开出全批期望 infoArray（契约正例的 DUT 语义）。"""
    info = np.zeros(BATCH, dtype=np.int32)
    for e in SAMPLE_MAP:
        for s in e["slots"]:
            info[s] = k_expected[e["content_idx"]]
    return info


def test_judge_a0_info_array_pass():
    """infoArray 分型：k_expected 经 sample_map 展开到全批槽位逐槽核对，全对 PASS。"""
    case = _info_case()
    v = batched_a0.judge_a0(POTRFB, case, _dut(_expand(L_MAT),
                                               _info_from_k_expected(case["k_expected"])))
    assert v["numeric"] == "PASS" and v["error"] is None
    assert v["batch"] == BATCH and v["fail_count"] == 0
    assert v["first_fail_index"] is None and v["worst_index"] is None
    assert v["worst"] is None and v["flags"] == []
    assert v["diagnostics"] == {"info_contract": True, "a0_contents": K,
                                "non_posdef_contents": [1]}
    assert v["formal"] == "PENDING_RULING"


def test_judge_a0_info_array_fail():
    """任一槽失配 → 数值 FAIL：fail_count=失配内容数、first_fail_index=最小失配
    槽位、diagnostics.info_mismatches 逐槽证据（error 恒 None）。"""
    case = _info_case((1, 3, 0))
    info = _info_from_k_expected(case["k_expected"])
    info[5] = 0                            # 槽 5 属内容 0（期望 1）：失配
    info[6] = 3                            # 槽 6 属内容 2（期望 0）：失配
    v = batched_a0.judge_a0(POTRFB, case, _dut(_expand(L_MAT), info))
    assert v["numeric"] == "FAIL" and v["error"] is None
    assert v["fail_count"] == 2 and v["first_fail_index"] == 5
    assert v["diagnostics"]["info_mismatches"] == [
        {"slot": 5, "content_idx": 0, "expected": 1, "actual": 0},
        {"slot": 6, "content_idx": 2, "expected": 0, "actual": 3}]


def test_judge_a0_info_scalar():
    """potrsBatched 标量分型：k_expected 直接比对（正例/失配各一，不进槽位展开）。"""
    contents = {
        "A64": np.stack([AS] * K), "A32": np.stack([AS.astype(np.float32)] * K),
        "B64": np.stack([BS] * K), "B32": np.stack([BS.astype(np.float32)] * K),
        "golden64": np.stack([XS] * K), "golden32": np.stack([XS.astype(np.float32)] * K),
    }
    case = _a0_case(contents)
    case.update({"case_purpose": "info", "info_probe": "bad_param_uplo",
                 "k_expected": -1})
    v = batched_a0.judge_a0(POTRSB, case, _dut(_expand([XS] * K), info=-1))
    assert v["numeric"] == "PASS" and v["fail_count"] == 0
    assert v["first_fail_index"] is None and v["error"] is None
    assert v["diagnostics"] == {"info_contract": True, "expected": -1, "actual": -1}
    v2 = batched_a0.judge_a0(POTRSB, case, _dut(_expand([XS] * K), info=0))
    assert v2["numeric"] == "FAIL" and v2["fail_count"] == 1
    assert v2["first_fail_index"] == 0 and v2["error"] is None
    assert v2["diagnostics"]["expected"] == -1 and v2["diagnostics"]["actual"] == 0


def test_judge_a0_info_structural_errors():
    """info 条目结构问题（k_expected 形态不符）出 error verdict（fail-closed）。"""
    case = _info_case()
    info = _info_from_k_expected(case["k_expected"])
    v = batched_a0.judge_a0(POTRFB, dict(case, k_expected=2),
                            _dut(_expand(L_MAT), info))
    assert v["fail_count"] is None and "k_expected 应为逐内容 k 值列表" in v["error"]
    v = batched_a0.judge_a0(POTRFB, dict(case, k_expected=[0, 2]),
                            _dut(_expand(L_MAT), info))
    assert v["fail_count"] is None and "应为 k=3 值列表" in v["error"]
    v = batched_a0.judge_a0(POTRFB, dict(case, k_expected=[0, "x", 0]),
                            _dut(_expand(L_MAT), info))
    assert v["fail_count"] is None and "不可取整" in v["error"]
    v = batched_a0.judge_a0(POTRFB, {k: x for k, x in case.items()
                                     if k != "k_expected"},
                            _dut(_expand(L_MAT), info))
    assert v["fail_count"] is None and "k_expected" in v["error"]
