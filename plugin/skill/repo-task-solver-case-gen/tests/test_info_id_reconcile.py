# -*- coding: utf-8 -*-
"""info 契约用例 id 的两侧对账（case-gen 侧，2026-10-09）。

full 切片包里包内 verify 曾按 derive_info_cases 的 s1 默认派生 info 契约用例，而
index 由 gen_data 按切片顶层 package_scope 全量派生——两侧 case_id 集合不同，verify
要的那几条没有被测输出，开发者算子全对也整包退 1（v4 六包命中）。渲染副本改为显式
传 require_s1，装包自检另加一条对账把这类口径分叉永久拦在装包时：

- full 切片两侧集合相等（s1 与非 s1 混合基座才看得出分叉）→
  test_full_slice_ids_match
- s1 切片同样相等（防反向破坏）→ test_s1_slice_ids_match
- 两侧集合不等时对账必红（把切片 scope 改掉模拟口径分叉）→
  test_reconcile_catches_scope_divergence
- 包内 verify 缺口径函数时对账必红（渲染协议漂移）→
  test_reconcile_catches_missing_helper

verify 侧的 id 不靠复算推断：真跑包内那份 verify_accuracy.py（--dut-out 指向空目录，
逐 case 记「缺被测输出」），报告里的 case_id 就是它实际会判的那一批。
"""
import importlib.util
import json
import shutil

import pytest

import build_package as bp
import gen_data_cholesky as gd


def _purescript_cases():
    """spotrf：8 例 s1 子集（s1 scope 下限）+ 4 例非 s1，两段 n 交错。"""
    cases = [{"case_id": f"spotrf-s{i:03d}", "op": "spotrf", "source": "cu",
              "n": n, "nrhs": None, "uplo": "L" if i % 2 else "U",
              "lda": n, "ldb": None, "seed": 4200 + i, "s1_subset": True}
             for i, n in enumerate((2, 4, 8, 9, 10, 12, 14, 16), start=1)]
    cases += [{"case_id": f"spotrf-x{i:03d}", "op": "spotrf", "source": "cu",
               "n": n, "nrhs": None, "uplo": "L" if i % 2 else "U",
               "lda": n, "ldb": None, "seed": 5200 + i}
              for i, n in enumerate((3, 5, 6, 7), start=1)]
    return cases


def _batched_cases():
    """spotrfBatched：恰 6 例 s1 子集（s1 scope 要求 6）+ 1 例非 s1。"""
    cases = [{"case_id": f"spotrfBatched-s{i:03d}", "op": "spotrfBatched",
              "source": "cu", "n": n, "nrhs": None, "uplo": "L", "lda": n,
              "ldb": None, "seed": 930000700 + i, "batch": 16, "s1_subset": True}
             for i, n in enumerate((2, 4, 5, 6, 7, 8), start=1)]
    cases.append({"case_id": "spotrfBatched-x001", "op": "spotrfBatched",
                  "source": "cu", "n": 3, "nrhs": None, "uplo": "L", "lda": 3,
                  "ldb": None, "seed": 930000801, "batch": 16})
    return cases


def _build(tmp_path, op, cases, scope):
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    (staging / "perf_baseline.json").write_text('{"note": "test stub"}\n',
                                                encoding="utf-8")
    canonical = tmp_path / f"canonical-{op}.json"
    canonical.write_text(json.dumps(
        {"schema": "solver-s1/canonical_cases@1", "frozen_at": "2026-10-09",
         "cases": cases}, ensure_ascii=False, indent=1), encoding="utf-8")
    out = tmp_path / "pkg" / op
    rc = bp.main(["--canonical", str(canonical), "--staging", str(staging),
                  "--out", str(out), "--op", op, "--scope", scope,
                  "--selfcheck", str(tmp_path / f"selfcheck-{op}.json")])
    assert rc == 0, f"{op}/{scope} 装包退出码 {rc}（应 0）"
    return out


def _verify_case_ids(pkg, tmp_path, tag):
    """真跑包内 verify，取报告里它判过的全部 case_id。"""
    spec = importlib.util.spec_from_file_location(
        f"_pkg_verify_{tag}", pkg / "verify_accuracy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    empty = tmp_path / f"dut-empty-{tag}"
    empty.mkdir(exist_ok=True)
    report = tmp_path / f"verify-{tag}.json"
    mod.main(["--package", str(pkg), "--dut-out", str(empty),
              "--report", str(report)])
    doc = json.loads(report.read_text(encoding="utf-8"))
    return sorted(row["case_id"] for row in doc["cases"])


def _index_ids(pkg):
    index = json.loads((pkg / "cases" / "index.json").read_text(encoding="utf-8"))
    all_ids = sorted(e["case_id"] for e in index["cases"])
    info_ids = sorted(e["case_id"] for e in index["cases"]
                      if e.get("case_purpose") == "info")
    return all_ids, info_ids


def _slice_doc(pkg):
    return json.loads((pkg / "canonical_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("op, cases", [
    ("spotrf", _purescript_cases()),
    ("spotrfBatched", _batched_cases()),
])
def test_full_slice_ids_match(tmp_path, op, cases):
    pkg = _build(tmp_path, op, cases, "full")
    assert _slice_doc(pkg)["package_scope"] == "full"
    all_ids, info_ids = _index_ids(pkg)
    assert info_ids, "full 切片应派生出 info 契约用例"
    assert _verify_case_ids(pkg, tmp_path, f"{op}-full") == all_ids


@pytest.mark.parametrize("op, cases", [
    ("spotrf", _purescript_cases()),
    ("spotrfBatched", _batched_cases()),
])
def test_s1_slice_ids_match(tmp_path, op, cases):
    pkg = _build(tmp_path, op, cases, "s1")
    assert _slice_doc(pkg)["package_scope"] == "s1"
    all_ids, info_ids = _index_ids(pkg)
    assert info_ids, "s1 切片应派生出 info 契约用例"
    assert _verify_case_ids(pkg, tmp_path, f"{op}-s1") == all_ids


@pytest.fixture(scope="module")
def full_purescript_pkg(tmp_path_factory):
    """两条负例共用一个真装的 full 包（装一次约 2 s，各自改动前先复制）。"""
    return _build(tmp_path_factory.mktemp("recon-neg"), "spotrf",
                  _purescript_cases(), "full")


def _clone(pkg, tmp_path):
    copy = tmp_path / "pkg-copy"
    shutil.copytree(pkg, copy)
    return copy


def test_reconcile_catches_scope_divergence(full_purescript_pkg, tmp_path):
    """对账不是摆设：index 按 full 派生、切片改称 s1，两侧集合即分叉。"""
    pkg = _clone(full_purescript_pkg, tmp_path)
    index = json.loads((pkg / "cases" / "index.json").read_text(encoding="utf-8"))
    doctored = dict(_slice_doc(pkg), package_scope="s1")
    with pytest.raises(bp.SelfCheckError) as exc:
        bp.assert_info_ids_reconciled(pkg, "spotrf", index["cases"], doctored,
                                      gd, False)
    assert "info id 对账不符" in str(exc.value)


def test_reconcile_catches_missing_helper(full_purescript_pkg, tmp_path):
    """包内 verify 没有口径函数时拒绝对账，不静默放行。"""
    pkg = _clone(full_purescript_pkg, tmp_path)
    index = json.loads((pkg / "cases" / "index.json").read_text(encoding="utf-8"))
    (pkg / "verify_accuracy.py").write_text("OP = 'spotrf'\n", encoding="utf-8")
    with pytest.raises(bp.SelfCheckError) as exc:
        bp.assert_info_ids_reconciled(pkg, "spotrf", index["cases"],
                                      _slice_doc(pkg), gd, False)
    assert "info_require_s1" in str(exc.value)
