# -*- coding: utf-8 -*-
"""批量 info 契约用例的模拟被测（accept 侧，scripts/sim_dut.py）。

info 分支此前对 k_expected 直接取整，批量条目的逐内容 k 值列表进来即 TypeError，
整条批量 info 契约项造不出被测输出（负例演练与装包门都空手）。sim_dut 自此按
sample_map 展开 infoArray：

- 逐槽展开与错值规则（正例 = 逐内容 k、负例逐内容走 info_dut_value）→
  test_info_array_values_positive / test_info_array_values_negative
- 槽位覆盖 fail-closed（越界 / 重复指派 / 漏槽）→ test_info_array_values_fail_closed
- CLI 正例产 (batch, n, n) 全零 out32 与 int32 infoArray，判定 PASS →
  test_cli_batched_info_positive_passes
- CLI 负例逐槽失配，判定 FAIL 且 first_fail_index 指到最小失配槽 →
  test_cli_batched_info_negative_fails
- sample_map 缺失即逐 case 记 skipped，不写半张 infoArray →
  test_cli_batched_info_missing_sample_map_skipped
- 非批量 info 用例（标量 k_expected）与 potrsBatched 标量 info 语义不变 →
  test_cli_single_info_scalar_unchanged / test_cli_batched_scalar_info_unchanged
- 求解族 out32 列数取 nrhs 而非 n（n>1、nrhs=1 的 potrsBatched 条目）→
  test_cli_batched_solve_info_cols_follow_nrhs
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")

import batched_a0
import cards_cholesky

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"

# 4 槽 2 内容的最小映射：内容 0 占槽 0/2（代表槽 0），内容 1 占槽 1/3（代表槽 1）。
_SAMPLE_MAP = [{"content_idx": 0, "rep_slot": 0, "slots": [0, 2]},
               {"content_idx": 1, "rep_slot": 1, "slots": [1, 3]}]
_BATCH = 4
_N = 3


def _sim_dut():
    spec = importlib.util.spec_from_file_location(
        "sim_dut_batched_info", _SCRIPTS / "sim_dut.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _package(tmp_path, entry, a32):
    """最小取材目录：cases/index.json 一条 + 同名 npz（sim_dut 只读这两样）。"""
    pkg = tmp_path / "pkg"
    (pkg / "cases").mkdir(parents=True, exist_ok=True)
    np.savez(pkg / "cases" / f"{entry['case_id']}.npz", A32=a32)
    doc = dict(entry, npz=f"cases/{entry['case_id']}.npz", arrays=["A32"])
    with open(pkg / "cases" / "index.json", "w", encoding="utf-8") as fh:
        json.dump({"cases": [doc]}, fh, ensure_ascii=False)
    return pkg


def _batched_entry(**over):
    entry = {"case_id": "spotrfBatched-x001-info1", "op": "spotrfBatched",
             "n": _N, "uplo": "L", "batch": _BATCH, "case_purpose": "info",
             "info_probe": "non_posdef", "k_expected": [2, 0],
             "sample_map": _SAMPLE_MAP}
    entry.update(over)
    return entry


def _run(mod, pkg, out, perturb):
    return mod.main(["--package", str(pkg), "--out", str(out),
                     "--perturb", perturb])


def _judge(entry, dut_path):
    """用权威实现判（criteria/batched_a0），不借渲染副本——副本另有回归。"""
    with np.load(dut_path) as z:
        dut = {"out32": z["out32"], "info": np.array(z["info"]),
               "status": str(np.asarray(z["status"]).item())}
    if np.ndim(dut["info"]) == 0:
        dut["info"] = int(dut["info"])
    card = cards_cholesky.get_card(entry["op"])
    return batched_a0.judge_a0(card, dict(entry), dut)


# ---------------------------------------------------------------------------
# info_array_values：展开与 fail-closed
# ---------------------------------------------------------------------------

def test_info_array_values_positive():
    mod = _sim_dut()
    got = mod.info_array_values([2, 0], _SAMPLE_MAP, _BATCH, "none")
    assert got.dtype == np.int32
    assert got.tolist() == [2, 0, 2, 0]      # 槽 0/2 是内容 0（k=2），槽 1/3 内容 1（k=0）


def test_info_array_values_negative():
    """负例逐内容复用 info_dut_value：k=2 → 3（下标漂移），k=0 → 1（误报非正定）。"""
    mod = _sim_dut()
    got = mod.info_array_values([2, 0], _SAMPLE_MAP, _BATCH, "scale")
    assert got.tolist() == [3, 1, 3, 1]
    assert all(a != b for a, b in zip(got.tolist(), [2, 0, 2, 0]))


@pytest.mark.parametrize("sample_map, why", [
    ([{"content_idx": 0, "rep_slot": 0, "slots": [0, 9]}], "槽位越界"),
    ([{"content_idx": 0, "rep_slot": 0, "slots": [0, 0, 1, 2, 3]}], "重复指派"),
    ([{"content_idx": 0, "rep_slot": 0, "slots": [0, 1]}], "漏槽"),
    ([{"content_idx": 7, "rep_slot": 0, "slots": [0, 1, 2, 3]}], "content_idx 越界"),
])
def test_info_array_values_fail_closed(sample_map, why):
    mod = _sim_dut()
    with pytest.raises(ValueError):
        mod.info_array_values([2, 0], sample_map, _BATCH, "none")


# ---------------------------------------------------------------------------
# CLI 端到端：造得出被测输出，且正负双向可分
# ---------------------------------------------------------------------------

def test_cli_batched_info_positive_passes(tmp_path):
    mod = _sim_dut()
    entry = _batched_entry()
    # 取材 npz 只带 k 个代表内容：槽数以条目的 batch 为准才对得上
    pkg = _package(tmp_path, entry, np.zeros((2, _N, _N), dtype=np.float32))
    out = tmp_path / "dut"
    assert _run(mod, pkg, out, "none") == 0
    with np.load(out / f"{entry['case_id']}.npz") as z:
        assert z["out32"].shape == (_BATCH, _N, _N)
        assert z["info"].dtype == np.int32
        assert z["info"].tolist() == [2, 0, 2, 0]
    v = _judge(entry, out / f"{entry['case_id']}.npz")
    assert v["numeric"] == "PASS", v
    assert v["error"] is None and v["flags"] == []


def test_cli_batched_info_negative_fails(tmp_path):
    mod = _sim_dut()
    entry = _batched_entry()
    pkg = _package(tmp_path, entry, np.zeros((2, _N, _N), dtype=np.float32))
    out = tmp_path / "dut"
    assert _run(mod, pkg, out, "scale") == 0
    v = _judge(entry, out / f"{entry['case_id']}.npz")
    assert v["numeric"] == "FAIL", v
    assert v["fail_count"] == 2            # 两个内容都失配
    assert v["first_fail_index"] == 0      # 最小失配槽位
    assert len(v["diagnostics"]["info_mismatches"]) == _BATCH


def test_cli_batched_info_missing_sample_map_skipped(tmp_path):
    mod = _sim_dut()
    entry = _batched_entry()
    entry.pop("sample_map")
    pkg = _package(tmp_path, entry, np.zeros((2, _N, _N), dtype=np.float32))
    out = tmp_path / "dut"
    assert _run(mod, pkg, out, "none") == 2        # 一个 case 都没写出
    assert not (out / f"{entry['case_id']}.npz").is_file()
    manifest = json.loads((out / "sim_manifest.json").read_text(encoding="utf-8"))
    assert manifest["written"] == []
    assert "sample_map" in manifest["skipped"][0]["reason"]


# ---------------------------------------------------------------------------
# 回归：标量 info 两条通路不受影响
# ---------------------------------------------------------------------------

def test_cli_single_info_scalar_unchanged(tmp_path):
    mod = _sim_dut()
    entry = {"case_id": "spotrf-t002-info1", "op": "spotrf", "n": _N, "uplo": "L",
             "case_purpose": "info", "info_probe": "non_posdef", "k_expected": 2}
    pkg = _package(tmp_path, entry, np.zeros((_N, _N), dtype=np.float32))
    for perturb, want in (("none", 2), ("scale", 3)):
        out = tmp_path / f"dut-{perturb}"
        assert _run(mod, pkg, out, perturb) == 0
        with np.load(out / f"{entry['case_id']}.npz") as z:
            assert z["out32"].shape == (_N, _N)
            assert np.ndim(z["info"]) == 0 and int(z["info"]) == want


def test_cli_batched_scalar_info_unchanged(tmp_path):
    """potrsBatched 族：k_expected 标量 -1，info 仍是标量；out32 仍按 batch 成三维。

    求解族的列数取 nrhs（接口契约 (batch, n, nrhs)），此处 nrhs=1、n=3，形状
    与分解族的方阵不同——列数口径另见
    test_cli_batched_solve_info_cols_follow_nrhs。
    """
    mod = _sim_dut()
    entry = _batched_entry(case_id="spotrsBatched-x001-info1", op="spotrsBatched",
                           info_probe="bad_param_uplo", k_expected=-1, nrhs=1)
    pkg = _package(tmp_path, entry, np.zeros((2, _N, _N), dtype=np.float32))
    for perturb, want in (("none", -1), ("scale", 0)):
        out = tmp_path / f"dut-{perturb}"
        assert _run(mod, pkg, out, perturb) == 0
        with np.load(out / f"{entry['case_id']}.npz") as z:
            assert z["out32"].shape == (_BATCH, _N, 1)
            assert np.ndim(z["info"]) == 0 and int(z["info"]) == want
        v = _judge(entry, out / f"{entry['case_id']}.npz")
        assert v["numeric"] == ("PASS" if want == -1 else "FAIL"), v


@pytest.mark.parametrize("nrhs", [1, 2])
def test_cli_batched_solve_info_cols_follow_nrhs(tmp_path, nrhs):
    """求解族 out32 = (batch, n, nrhs)：n>1 时方阵口径会把列数错写成 n。

    契约在 stream_check 的批量 info 支路已经是「求解族取 nrhs、其余取 n」，
    sim_dut 此前统一取 A32 的后两维（即 n×n），spotrsBatched/cpotrsBatched 的
    被测输出因此多出 n-nrhs 列。
    """
    mod = _sim_dut()
    entry = _batched_entry(case_id=f"spotrsBatched-x00{nrhs}-info1",
                           op="spotrsBatched", info_probe="bad_param_uplo",
                           k_expected=-1, nrhs=nrhs)
    pkg = _package(tmp_path, entry, np.zeros((2, _N, _N), dtype=np.float32))
    out = tmp_path / "dut"
    assert _run(mod, pkg, out, "none") == 0
    with np.load(out / f"{entry['case_id']}.npz") as z:
        assert z["out32"].shape == (_BATCH, _N, nrhs)      # 非 (_BATCH, _N, _N)
    assert _N > 1 and nrhs < _N                            # 两个口径真的能分开
