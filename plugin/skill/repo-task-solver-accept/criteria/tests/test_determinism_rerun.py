# -*- coding: utf-8 -*-
"""HT-10 确定性复跑检查的机械断言（accept 侧，scripts/stream_check.py）。

任务书 §3.2：合法 SPD 用例重复执行 bit-wise 一致——HT-12 裁定为独立检查项
（不并入数值判定流转）。HT-10 落地复跑驱动与比对实现于 stream_check --rerun N：

- 字节域比对语义：NaN 按位等同、-0.0 与 0.0 不同、shape/dtype 先行 →
  test_bitwise_same_byte_domain_semantics
- _rerun_compare 三键比对（out32/info/status），一致 None、失配带键 →
  test_rerun_compare_keys
- 端到端正例：--rerun 5 全链（单矩阵 + 批量 A0 + info 契约派生），精度用例
  determinism.consistent、info 用例无该键、summary 计数与退出码 →
  test_rerun_end_to_end_pass
- 端到端负例：非确定 DUT（monkeypatch _sim_dut 翻转首元素）→ determinism
  FAIL 带 first_diff.run/键、退出码 1、数值结论不受染（独立结论）→
  test_rerun_detects_nondeterministic_dut
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent.parent / "scripts"
_GEN_DIR = (_HERE.parent.parent.parent.parent
            / "skill" / "repo-task-solver-case-gen" / "scripts")


def _load_stream_check():
    spec = importlib.util.spec_from_file_location(
        "stream_check_ht10", _SCRIPTS / "stream_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _canonical(tmp_path):
    """最小合成册：单矩阵 spotrf ×4（跳最小取 3 → 派 3 个 info 变体）
    + 批量 spotrfBatched ×1（派 1 个混合 info），均 s1_subset。"""
    cases = [
        {"case_id": "ht10-single-16", "op": "spotrf", "source": "cu", "n": 16,
         "uplo": "L", "lda": 16, "seed": 911016, "s1_subset": True},
        {"case_id": "ht10-single-24", "op": "spotrf", "source": "cu", "n": 24,
         "uplo": "U", "lda": 24, "seed": 911024, "s1_subset": True},
        {"case_id": "ht10-single-32", "op": "spotrf", "source": "cu", "n": 32,
         "uplo": "L", "lda": 32, "seed": 911032, "s1_subset": True},
        {"case_id": "ht10-single-40", "op": "spotrf", "source": "cu", "n": 40,
         "uplo": "U", "lda": 40, "seed": 911040, "s1_subset": True},
        {"case_id": "ht10-batched-8", "op": "spotrfBatched", "source": "cu",
         "n": 8, "batch": 7, "nrhs": None, "uplo": "L", "lda": 8, "ldb": None,
         "seed": 911048, "s1_subset": True},
    ]
    path = tmp_path / "canonical.json"
    path.write_text(json.dumps({"schema": "solver-s1/canonical@1", "cases": cases}),
                    encoding="utf-8")
    return path


def _run(tmp_path, canonical, extra):
    mod = _load_stream_check()
    report = tmp_path / "report.json"
    argv = ["--canonical", str(canonical), "--gen-dir", str(_GEN_DIR),
            "--report", str(report)] + list(extra)
    rc = mod.main(argv)
    return rc, json.load(open(report))


def test_bitwise_same_byte_domain_semantics():
    mod = _load_stream_check()
    same = mod._bitwise_same
    nan = np.float32(np.nan)
    # NaN 按位等同（== 语义会 False，字节域必须 True）
    assert same(np.array([nan, 1.0], dtype=np.float32),
                np.array([nan, 1.0], dtype=np.float32)) is True
    # -0.0 与 0.0 位型不同（== 语义会 True，字节域必须 False）
    assert same(np.array([-0.0], dtype=np.float32),
                np.array([0.0], dtype=np.float32)) is False
    # shape/dtype 先行
    assert same(np.zeros(3, dtype=np.float32),
                np.zeros(3, dtype=np.float64)) is False
    assert same(np.zeros((2, 2), dtype=np.float32),
                np.zeros(4, dtype=np.float32)) is False
    assert same(np.arange(3, dtype=np.int32), np.arange(3, dtype=np.int32)) is True


def test_rerun_compare_keys():
    mod = _load_stream_check()
    a = {"out32": np.zeros((2, 2), dtype=np.float32), "info": 0, "status": "ok"}
    assert mod._rerun_compare(a, dict(a, out32=np.zeros((2, 2), dtype=np.float32),
                                      info=0, status="ok")) is None
    # out32 失配（字节域：NaN 复数也抓）
    b = dict(a, out32=np.full((2, 2), np.float32(np.nan)))
    assert mod._rerun_compare(a, b)["key"] == "out32"
    # info 失配（数组与标量两种形态）
    assert mod._rerun_compare(a, dict(a, info=1))["key"] == "info"
    arr_info = dict(a, info=np.zeros(2, dtype=np.int32))
    arr_info2 = dict(arr_info, info=np.ones(2, dtype=np.int32))
    assert mod._rerun_compare(arr_info, arr_info2)["key"] == "info"
    # status 失配优先
    assert mod._rerun_compare(a, dict(a, status="boom"))["key"] == "status"


def test_rerun_end_to_end_pass(tmp_path):
    canonical = _canonical(tmp_path)
    rc, rep = _run(tmp_path, canonical, ["--select", "s1", "--rerun", "5"])
    assert rc == 0, rep["summary"]
    assert rep["params"]["rerun"] == 5
    by_id = {c["case_id"]: c for c in rep["cases"]}
    # 精度用例（单矩阵 + 批量）：determinism 独立结论，5 次一致
    for cid in ("ht10-single-16", "ht10-single-24", "ht10-single-32",
                "ht10-single-40", "ht10-batched-8"):
        det = by_id[cid]["determinism"]
        assert det == {"runs": 5, "consistent": True, "first_diff": None}, cid
        assert by_id[cid]["numeric"] == "PASS"
    # info 契约用例（spotrf 派 3 + 批量派 1）：不参与复跑，无 determinism 键
    info_recs = [c for c in rep["cases"] if c.get("case_purpose") == "info"]
    assert len(info_recs) == 4
    assert all("determinism" not in c for c in info_recs)
    # summary 计数：det 只统计精度用例
    assert rep["summary"]["det_pass"] == 5 and rep["summary"]["det_fail"] == 0


def test_rerun_detects_nondeterministic_dut(tmp_path):
    canonical = _canonical(tmp_path)
    mod = _load_stream_check()
    real_sim = mod._sim_dut
    state = {"calls": 0}

    def flaky(golden32, mode, card, perturb_index=None):
        dut = real_sim(golden32, mode, card, perturb_index)
        state["calls"] += 1
        if state["calls"] >= 2:   # 首次之后每次翻转 out32 首元素——模拟非确定实现
            out = np.array(dut["out32"], copy=True)
            out.ravel()[0] = np.float32(out.ravel()[0] + np.float32(1.0))
            dut = dict(dut, out32=out)
        return dut

    mod._sim_dut = flaky
    report = tmp_path / "bad_report.json"
    rc = mod.main(["--canonical", str(canonical), "--gen-dir", str(_GEN_DIR),
                   "--report", str(report), "--select", "s1", "--limit", "1",
                   "--rerun", "5"])
    rep = json.load(open(report))
    # 确定性 FAIL：独立结论落证，数值判定不受染（sim 正例仍 numeric PASS）
    rec = rep["cases"][0]
    assert rec["determinism"]["consistent"] is False
    fd = rec["determinism"]["first_diff"]
    assert fd["run"] == 2 and fd["key"] == "out32", fd
    assert rec["numeric"] == "PASS"
    assert rep["summary"]["det_fail"] == 1 and rep["summary"]["det_pass"] == 0
    assert rc == 1
