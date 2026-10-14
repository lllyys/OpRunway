# -*- coding: utf-8 -*-
"""info 契约用例派生口径的渲染契约（accept 侧，criteria/render_verify.py）。

full 切片包里包内 verify 曾按 derive_info_cases 的 s1 默认派生，包内 gen_data 则按
切片顶层 package_scope 全量派生——两侧 case_id 集合不同，开发者算子全对也因「缺被测
输出」退 1（v4 六包命中）。渲染副本自此显式传 require_s1，口径与 gen 侧同一事实源：

- 口径函数三型（full → False、s1 → True、缺字段 → True）→ test_info_require_s1_by_scope
- 派生调用唯一且显式带 require_s1（防回落到默认值）→ test_derive_call_passes_require_s1
- 两种渲染形态都带该函数（纯脚本六算子与批量四算子）→ 两条测试各自参数化全算子
- skill 侧口径函数（stream_check 调的那份）同三型 → test_module_info_require_s1_by_scope
- full 册无 s1 标记时流式派生的 info id 集合 == 包 index 侧 == verify 渲染副本侧 →
  test_stream_info_ids_match_index_and_verify
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("scipy")

import render_verify

_OPS = ("spotrf", "spotrs", "spotri", "cpotrf", "cpotrs", "cpotri")
_BATCHED = ("spotrfBatched", "spotrsBatched", "cpotrfBatched", "cpotrsBatched")
_ALL = _OPS + _BATCHED

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
_GEN_DIR = (Path(__file__).resolve().parent.parent.parent.parent
            / "repo-task-solver-case-gen" / "scripts")


def _load_rendered(tmp_path, op):
    """把渲染出的精度副本当模块导入——断言的是成品件的行为，不是模板字面。"""
    text = render_verify.render(op)["verify_accuracy.py"]
    path = tmp_path / f"verify_accuracy_{op}.py"
    path.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"_rendered_verify_{op}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("op", _ALL)
def test_info_require_s1_by_scope(tmp_path, op):
    mod = _load_rendered(tmp_path, op)
    assert mod.info_require_s1({"package_scope": "full"}) is False
    assert mod.info_require_s1({"package_scope": "s1"}) is True
    assert mod.info_require_s1({}) is True        # 旧包无该字段：按 s1 口径


@pytest.mark.parametrize("op", _ALL)
def test_derive_call_passes_require_s1(op):
    text = render_verify.render(op)["verify_accuracy.py"]
    fn = "derive_batched_info_cases" if op.endswith("Batched") else "derive_info_cases"
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines) if f"gen_mod.{fn}(" in line]
    assert len(hits) == 1, f"{op}: 期望恰一处 {fn} 派生调用，实为 {len(hits)} 处"
    call = "\n".join(lines[hits[0]:hits[0] + 3])
    assert "require_s1=info_require_s1(canonical)" in call, (
        f"{op}: 派生调用未显式传 require_s1，会回落到 s1 默认：\n{call}")


# ---------------------------------------------------------------------------
# skill 侧口径：stream_check 的两处 info 派生调同一条规则
# ---------------------------------------------------------------------------

def test_module_info_require_s1_by_scope():
    """渲染副本之外，render_verify 自己也导出这条规则（stream_check 调它）。"""
    assert render_verify.info_require_s1({"package_scope": "full"}) is False
    assert render_verify.info_require_s1({"package_scope": "s1"}) is True
    assert render_verify.info_require_s1({}) is True


def _load_stream_check():
    spec = importlib.util.spec_from_file_location(
        "stream_check_info_scope", _SCRIPTS / "stream_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _full_scope_book(tmp_path):
    """full 册：四例 spotrf，一律不带 s1_subset 标记（全量精度用例决策下的形态）。

    同目录另落包根 index（ratio_basis 过准入），让流式走的是包根布局那条路。
    """
    root = tmp_path / "book"
    (root / "cases").mkdir(parents=True)
    doc = {"schema": "solver-s1/canonical_cases@1", "package_scope": "full",
           "cases": [{"case_id": f"scope-spotrf-{n}", "op": "spotrf",
                      "source": "cu", "n": n, "nrhs": None, "uplo": "L",
                      "lda": n, "ldb": None, "seed": 5100 + n}
                     for n in (2, 3, 4, 5)]}
    path = root / "canonical_cases.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with open(root / "cases" / "index.json", "w", encoding="utf-8") as fh:
        json.dump({"ratio_basis": "A32-f64", "cases": []}, fh, ensure_ascii=False)
    return path, doc


def test_stream_info_ids_match_index_and_verify(tmp_path):
    """三方同集：流式报告 / 包 index 侧派生 / verify 渲染副本侧派生。

    流式此前不传 require_s1，吃 derive_info_cases 的 s1 默认——full 册的精度条目
    没有 s1_subset 标记，info 用例一条都派生不出来，与另两方分叉。
    """
    canonical, doc = _full_scope_book(tmp_path)
    report = tmp_path / "stream.json"
    mod = _load_stream_check()
    rc = mod.main(["--canonical", str(canonical), "--gen-dir", str(_GEN_DIR),
                   "--report", str(report), "--ops", "spotrf"])
    assert rc == 0, report.read_text(encoding="utf-8")
    stream_ids = {r["case_id"] for r in
                  json.loads(report.read_text(encoding="utf-8"))["cases"]
                  if r.get("case_purpose") == "info"}

    if str(_GEN_DIR) not in sys.path:
        sys.path.insert(0, str(_GEN_DIR))
    import gen_data_cholesky as gen_mod
    # 包 index 侧：gen_data 写 index 时用的就是 package_scope=="full" 这一判断
    index_ids = {e["case_id"] for e in gen_mod.derive_info_cases(
        doc["cases"], require_s1=doc.get("package_scope") != "full")}
    # verify 渲染副本侧：副本自己那份口径函数
    rendered = _load_rendered(tmp_path, "spotrf")
    verify_ids = {e["case_id"] for e in gen_mod.derive_info_cases(
        doc["cases"], require_s1=rendered.info_require_s1(doc))}

    assert index_ids, "full 册该派生出 info 契约用例，基座选取先出了问题"
    assert stream_ids == index_ids == verify_ids
