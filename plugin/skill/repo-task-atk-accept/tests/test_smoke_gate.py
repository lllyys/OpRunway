"""冒烟的选例与拦截判据。

这道闸从前按剖面开关：`param_reject` 为假就整条打开，无条件进全量。
sparse 走的正是 npu 剖面，于是它在那条路上等于不存在——两次整批同因的
口径问题都是这么漏到全量轮的，每次多付一轮全量。
"""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_atk  # noqa: E402


def _case(cid, **attrs):
    return {"id": cid, "inputs": [{"name": k, "range_values": [v]}
                                  for k, v in attrs.items()]}


def test_axis_sampling_takes_one_case_per_value():
    cases = [_case(i, beta_id=i % 3, value_mode=i % 5) for i in range(30)]
    picked = run_atk._smoke_axis_cases(cases, ["beta_id", "value_mode"], already=set())
    assert set(picked) == {f"beta_id={v}" for v in range(3)} | {
        f"value_mode={v}" for v in range(5)}
    # 并集不是叉积：3 + 5 而不是 15
    assert len(picked) == 8


def test_axis_sampling_skips_cases_the_dtype_pass_already_took():
    cases = [_case(i, beta_id=i % 3) for i in range(9)]
    picked = run_atk._smoke_axis_cases(cases, ["beta_id"], already={0, 1})
    assert 0 not in picked.values() and 1 not in picked.values()
    assert picked == {"beta_id=2": 2}


def test_axis_sampling_ignores_attrs_no_case_carries():
    cases = [_case(i, beta_id=0) for i in range(3)]
    assert run_atk._smoke_axis_cases(cases, ["nonexistent"], set()) == {}


def test_smoke_axes_only_covers_what_facts_declares(tmp_path):
    """不自动枚举所有 attr——那会把冒烟撑成几十条，就不是冒烟了。"""
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({}), encoding="utf-8")
    assert run_atk._smoke_axes(facts) == []

    facts.write_text(json.dumps({"smoke": {"axes": ["beta_id", "value_mode"]}}),
                     encoding="utf-8")
    assert run_atk._smoke_axes(facts) == ["beta_id", "value_mode"]

    assert run_atk._smoke_axes(tmp_path / "nope.json") == []


def test_case_attr_reads_the_first_range_value():
    case = _case(3, beta_id=0, value_mode=5)
    assert run_atk._case_attr(case, "beta_id") == 0
    assert run_atk._case_attr(case, "value_mode") == 5
    assert run_atk._case_attr(case, "absent") is None


def test_resolve_local_leaves_variable_paths_alone():
    """`make_repro.py` 拼的是带 `$PKG` 的命令，resolve 会把变量名当相对目录。"""
    assert run_atk._resolve_local("$PKG/golden") == "$PKG/golden"
    assert run_atk._resolve_local("") == ""
    assert run_atk._resolve_local(None) is None


def test_resolve_local_absolutizes_existing_relative_paths(tmp_path, monkeypatch):
    (tmp_path / "kit_fixes").mkdir()
    monkeypatch.chdir(tmp_path)
    resolved = run_atk._resolve_local("kit_fixes")
    assert Path(resolved).is_absolute()
    assert Path(resolved).name == "kit_fixes"
    # 不存在的原样返回，交给下游按自己的方式报错
    assert run_atk._resolve_local("no_such_dir") == "no_such_dir"
