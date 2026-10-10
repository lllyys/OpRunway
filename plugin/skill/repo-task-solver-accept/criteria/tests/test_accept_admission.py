# -*- coding: utf-8 -*-
"""验收准入断言（accept 侧，scripts/accept_run.py 与 scripts/stream_check.py）。

以新为准裁定（2026-10-09）：只受理 index 顶层 ratio_basis == "A32-f64" 的包。旧包
缺该字段即 A64 基，其固化 ratio_cpu/ratio_cpu_mean 与现行残差实现不同基，判出来的
PASS/FAIL 都不成立；不做旧包兼容、不在读侧重算。

- 缺字段拒收（退 2、不产 report）→ test_accept_run_rejects_missing_basis
- 错值拒收 → test_accept_run_rejects_wrong_basis
- 正确值放行（退 0 且 report 落盘）→ test_accept_run_accepts_a32_basis
- stream_check 读到包 index 同口径拒收 → test_stream_check_rejects_old_basis
- 散册 canonical 无 index 时不在准入面内 → test_stream_check_without_index_not_gated
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")

import accept_run

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
_GEN_DIR = (Path(__file__).resolve().parent.parent.parent.parent
            / "repo-task-solver-case-gen" / "scripts")


def _package(tmp_path, index_extra):
    """最小包：manifest + cases/index.json + perf_baseline.json。

    index 条目空——准入断言在 index 读出后立即生效，走不到逐 case 装载。
    """
    pkg = tmp_path / "pkg"
    (pkg / "cases").mkdir(parents=True)
    index = {"cases": []}
    index.update(index_extra)
    with open(pkg / "cases" / "index.json", "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False)
    with open(pkg / "perf_baseline.json", "w", encoding="utf-8") as fh:
        json.dump([], fh)
    with open(pkg / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump({"operator": "spotrf", "fingerprint": {
            "cases/index.json": accept_run._sha256(pkg / "cases" / "index.json"),
            "perf_baseline.json": accept_run._sha256(pkg / "perf_baseline.json")}},
            fh, ensure_ascii=False)
    dut = tmp_path / "dut"
    dut.mkdir()
    return pkg, dut


def _run_accept(pkg, dut, report):
    return accept_run.main(["--package", str(pkg), "--dut-out", str(dut),
                            "--report", str(report)])


@pytest.mark.parametrize("extra", [{}, {"ratio_basis": None}])
def test_accept_run_rejects_missing_basis(tmp_path, extra, capsys):
    pkg, dut = _package(tmp_path, extra)
    report = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        _run_accept(pkg, dut, report)
    assert exc.value.code == 2
    assert "旧基包不受理" in capsys.readouterr().err
    assert not report.exists()


@pytest.mark.parametrize("basis", ["A64", "A64-f64", "a32-f64", ""])
def test_accept_run_rejects_wrong_basis(tmp_path, basis, capsys):
    pkg, dut = _package(tmp_path, {"ratio_basis": basis})
    report = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        _run_accept(pkg, dut, report)
    assert exc.value.code == 2
    assert "参考值基准非 A32-f64" in capsys.readouterr().err
    assert not report.exists()


def test_accept_run_accepts_a32_basis(tmp_path):
    pkg, dut = _package(tmp_path, {"ratio_basis": "A32-f64"})
    report = tmp_path / "report.json"
    assert _run_accept(pkg, dut, report) == 0
    doc = json.loads(report.read_text(encoding="utf-8"))
    assert doc["operator"] == "spotrf"


# ---------------------------------------------------------------------------
# stream_check：同一条准入，只在包根布局（canonical 同目录有 cases/index.json）生效
# ---------------------------------------------------------------------------

def _load_stream_check():
    spec = importlib.util.spec_from_file_location(
        "stream_check_admission", _SCRIPTS / "stream_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _canonical(tmp_path, with_index_basis):
    """最小合成册：一例 spotrf（n=8）。with_index_basis 非 None 时另落包根 index。"""
    root = tmp_path / "root"
    root.mkdir()
    doc = {"schema": "solver-s1/canonical_cases@1", "cases": [
        {"case_id": "adm-spotrf-8", "op": "spotrf", "source": "cu", "n": 8,
         "uplo": "L", "lda": 8, "seed": 880008, "s1_subset": True}]}
    path = root / "canonical_cases.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    if with_index_basis is not None:
        (root / "cases").mkdir()
        index = {"cases": []}
        if with_index_basis:
            index["ratio_basis"] = with_index_basis
        with open(root / "cases" / "index.json", "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False)
    return path


def test_stream_check_rejects_old_basis(tmp_path, capsys):
    mod = _load_stream_check()
    canonical = _canonical(tmp_path, "")          # index 存在但无 ratio_basis
    rc = mod.main(["--canonical", str(canonical), "--gen-dir", str(_GEN_DIR),
                   "--report", str(tmp_path / "r.json"), "--ops", "spotrf"])
    assert rc == 2
    assert "旧基包不受理" in capsys.readouterr().err


def test_stream_check_without_index_not_gated(tmp_path):
    """散册 canonical 没有 index：没有固化参考值可误用，不在准入面内。"""
    mod = _load_stream_check()
    canonical = _canonical(tmp_path, None)
    report = tmp_path / "r.json"
    rc = mod.main(["--canonical", str(canonical), "--gen-dir", str(_GEN_DIR),
                   "--report", str(report), "--ops", "spotrf"])
    assert rc == 0, report.read_text(encoding="utf-8")
    doc = json.loads(report.read_text(encoding="utf-8"))
    assert doc["summary"]["numeric_pass"] >= 1
