# -*- coding: utf-8 -*-
"""info 契约用例派生口径的渲染契约（accept 侧，criteria/render_verify.py）。

full 切片包里包内 verify 曾按 derive_info_cases 的 s1 默认派生，包内 gen_data 则按
切片顶层 package_scope 全量派生——两侧 case_id 集合不同，开发者算子全对也因「缺被测
输出」退 1（v4 六包命中）。渲染副本自此显式传 require_s1，口径与 gen 侧同一事实源：

- 口径函数三型（full → False、s1 → True、缺字段 → True）→ test_info_require_s1_by_scope
- 派生调用唯一且显式带 require_s1（防回落到默认值）→ test_derive_call_passes_require_s1
- 两种渲染形态都带该函数（纯脚本六算子与批量四算子）→ 两条测试各自参数化全算子
"""
import importlib.util

import pytest

pytest.importorskip("scipy")

import render_verify

_OPS = ("spotrf", "spotrs", "spotri", "cpotrf", "cpotrs", "cpotri")
_BATCHED = ("spotrfBatched", "spotrsBatched", "cpotrfBatched", "cpotrsBatched")
_ALL = _OPS + _BATCHED


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
