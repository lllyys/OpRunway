# -*- coding: utf-8 -*-
"""取证段的机械断言：三键往返、留证触发、自包含判据、通过即弃。

覆盖清单 → 测试对照：

- 三键 npz 往返（标量 info 与数组 info 两形态） → test_three_key_roundtrip_*
- 留证触发全表（五类任一命中就留） → test_should_keep_table
- 数值 PASS 且确定性无失配时不留证 → test_should_keep_pass_discards
- 人工 --keep 指定优先 → test_should_keep_manual
- 留证件自包含：文件齐备、带重判命令、复跑失配轮的输出搬进来
  → test_export_failure_bundle_self_contained、test_export_copies_diff_bins
- 执行未产出三键时留证件无 dut.npz，自检如实报缺 → test_bundle_without_dut
- 通过即弃真的清空容器 → test_discard_empties_containers
"""
import pathlib
import sys

import numpy as np
import pytest

HARNESS = pathlib.Path(__file__).resolve().parent.parent
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import evidence  # noqa: E402
import op_abi    # noqa: E402


def test_three_key_roundtrip_scalar_info(tmp_path):
    out = np.arange(4, dtype=np.float32).reshape(2, 2)
    dut = evidence.three_key(out, 7, "ok")
    evidence.save_dut_npz(tmp_path / "dut.npz", dut)
    back = evidence.load_dut_npz(tmp_path / "dut.npz")
    assert back["status"] == "ok"
    assert np.array_equal(back["out32"], out)
    assert back["info"] == 7 and np.ndim(back["info"]) == 0


def test_three_key_roundtrip_array_info(tmp_path):
    out = np.zeros((3, 2, 2), dtype=np.complex64)
    info = np.array([0, 1, 2], dtype=np.int32)
    evidence.save_dut_npz(tmp_path / "dut.npz",
                          evidence.three_key(out, info, "ok"))
    back = evidence.load_dut_npz(tmp_path / "dut.npz")
    assert back["info"].tolist() == [0, 1, 2]
    assert back["out32"].dtype == np.complex64


def test_three_key_omits_absent_keys():
    dut = evidence.three_key(None, None, "crash:SIGSEGV")
    assert dut == {"status": "crash:SIGSEGV"}


@pytest.mark.parametrize("kind", evidence.FAILED_KINDS)
def test_should_keep_table(kind):
    keep, reason = evidence.should_keep(kind, None, None)
    assert keep is True
    assert kind in reason


def test_should_keep_rerun_and_determinism():
    assert evidence.should_keep("rerun_mismatch", "PASS", None)[0] is True
    keep, reason = evidence.should_keep(
        "ok", "PASS", {"consistent": False, "runs": 3})
    assert keep is True and "确定性" in reason


def test_should_keep_numeric_fail():
    keep, reason = evidence.should_keep("ok", "FAIL", {"consistent": True})
    assert keep is True and "FAIL" in reason


def test_should_keep_pass_discards():
    assert evidence.should_keep("ok", "PASS", {"consistent": True}) == (False, None)
    assert evidence.should_keep("ok", None, None) == (False, None)


def test_should_keep_manual():
    keep, reason = evidence.should_keep("ok", "PASS", None, keep_ids={"c1"},
                                        case_id="c1")
    assert keep is True and "--keep" in reason


def _exec_rec(tmp_path, with_diff=False):
    case_dir = tmp_path / "work" / "c1"
    (case_dir / "out").mkdir(parents=True)
    if with_diff:
        (case_dir / "out" / "out32.bin.diff.bin").write_bytes(b"\x01\x02")
        (case_dir / "out" / "info.bin.diff.bin").write_bytes(b"\x03")
    return {"kind": "op_error", "status": "op_error:exit=14", "detail": "返回 -1",
            "returncode": 14, "wall_seconds": 1.0, "result": {"status": "op_error"},
            "spec_text": "op=cmatinv_batched\nruns=1\n", "log": "stderr 全文",
            "case_dir": str(case_dir), "out32": None, "info": None}


def test_export_failure_bundle_self_contained(tmp_path):
    case = {"case_id": "c1", "op": "cmatinv_batched", "n": 8, "batch": 2,
            "seed": 1, "uplo": "L"}
    inputs = {"A64": np.zeros((2, 8, 8), dtype=np.complex128),
              "A32": np.zeros((2, 8, 8), dtype=np.complex64), "B32": None}
    dut = evidence.three_key(np.zeros((2, 8, 8), dtype=np.complex64),
                             np.zeros(2, dtype=np.int32), "ok")
    bundle = evidence.export_failure(
        tmp_path / "ev" / "c1", case, op_abi.get("cmatinv_batched"),
        _exec_rec(tmp_path), inputs, dut, "执行失败：归类 op_error",
        provenance={"soc": "ascend910_93"}, verdict={"numeric": "FAIL"})
    ok, missing = evidence.bundle_is_self_contained(bundle)
    assert ok, missing
    assert (bundle / "provenance.json").is_file()
    rejudge = (bundle / "REJUDGE.md").read_text(encoding="utf-8")
    assert "run_harness.py --rejudge" in rejudge
    assert "执行失败：归类 op_error" in rejudge
    meta = __import__("json").loads((bundle / "case.json").read_text(encoding="utf-8"))
    assert meta["abi"]["op"] == "cmatinv_batched"
    assert meta["abi"]["call_shape"] == "outofplace_a_ainv"
    assert meta["verdict"] == {"numeric": "FAIL"}
    assert meta["inputs"]["A32"]["shape"] == [2, 8, 8]
    assert "B32" not in meta["inputs"]                   # None 输入不编造条目
    with np.load(bundle / "inputs.npz") as z:
        assert sorted(z.files) == ["A32", "A64"]
    assert (bundle / "case.spec").read_text(encoding="utf-8").startswith("op=")
    assert (bundle / "exec.log").read_text(encoding="utf-8") == "stderr 全文"


def test_export_copies_diff_bins(tmp_path):
    bundle = evidence.export_failure(
        tmp_path / "ev" / "c1", {"case_id": "c1", "op": "sgetri"},
        op_abi.get("sgetri"), _exec_rec(tmp_path, with_diff=True),
        {"A32": np.zeros(4, dtype=np.float32)},
        evidence.three_key(np.zeros(4, dtype=np.float32), 0, "ok"),
        "复跑 bit-wise 失配")
    assert (bundle / "out32.bin.diff.bin").read_bytes() == b"\x01\x02"
    assert (bundle / "info.bin.diff.bin").read_bytes() == b"\x03"


def test_bundle_without_dut(tmp_path):
    bundle = evidence.export_failure(
        tmp_path / "ev" / "c1", {"case_id": "c1", "op": "sgetri"},
        op_abi.get("sgetri"), _exec_rec(tmp_path),
        {"A32": np.zeros(4, dtype=np.float32)},
        evidence.three_key(None, None, "crash:SIGSEGV"), "执行失败：归类 crash")
    assert not (bundle / "dut.npz").exists()
    ok, missing = evidence.bundle_is_self_contained(bundle, require_dut=True)
    assert ok is False and missing == ["dut.npz"]
    ok2, _ = evidence.bundle_is_self_contained(bundle, require_dut=False)
    assert ok2 is True


def test_discard_empties_containers():
    a = {"A32": np.zeros(100, dtype=np.float32), "n": 8}
    b = {"out32": np.zeros(50, dtype=np.float32)}
    assert evidence.discard(a, b, {}) == 2
    assert a == {} and b == {}
