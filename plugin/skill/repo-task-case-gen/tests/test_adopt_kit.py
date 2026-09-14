"""自带件采纳的纯逻辑测试：识别、缺件、指纹、档位。

采纳之后的冻结要真机（跑 atk），一条都不在这里测。
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import adopt_kit  # noqa: E402
import case_shape  # noqa: E402


CASES = [
    {"id": 0, "api_type": "op_public",
     "inputs": [{"name": "nnz", "type": "attr", "dtype": "int", "range_values": [4]}]},
    {"id": 1, "api_type": "op_public",
     "inputs": [{"name": "nnz", "type": "attr", "dtype": "int", "range_values": [40]}]},
]

EXECUTOR = '''
from atk.tasks.api_execute import register
from atk.tasks.api_execute.base_api import BaseApi


@register("op_public")
class OpApi(BaseApi):
    pass
'''

COMPARE = '''
from atk.tasks.post_process import ACCURACY_REGISTRY
'''


def _kit(tmp_path, *, executor=True, compare=True, cases=True):
    kit = tmp_path / "kit"
    kit.mkdir()
    if cases:
        (kit / "accuracy_cases.json").write_text(json.dumps(CASES), encoding="utf-8")
    if executor:
        (kit / "function_ops.py").write_text(EXECUTOR, encoding="utf-8")
    if compare:
        (kit / "accuracy_ops.py").write_text(COMPARE, encoding="utf-8")
    return kit


def test_scan_finds_all_four_by_content(tmp_path):
    """按内容认，不按文件名认——任务方的命名不统一。"""
    kit = _kit(tmp_path)
    (kit / "adapt_cases.py").write_text(
        "def adapt(case):\n    case['api_type'] = 'x'\n    return case\n",
        encoding="utf-8")
    found, missing = adopt_kit._scan(kit)
    assert missing == []
    assert found["cases"].name == "accuracy_cases.json"
    assert found["executor"].name == "function_ops.py"
    assert found["compare"].name == "accuracy_ops.py"
    assert found["adapt"].name == "adapt_cases.py"


def test_scan_reports_missing_executor(tmp_path):
    """缺执行器是硬缺口：ATK 不知道怎么把算子调起来。"""
    _, missing = adopt_kit._scan(_kit(tmp_path, executor=False))
    assert len(missing) == 1 and "执行器插件" in missing[0]


def test_scan_reports_missing_cases(tmp_path):
    _, missing = adopt_kit._scan(_kit(tmp_path, cases=False))
    assert len(missing) == 1 and "用例清单" in missing[0]


def test_scan_tolerates_missing_optional_pieces(tmp_path):
    """比对器与适配脚本可缺，缺了不拦。"""
    found, missing = adopt_kit._scan(_kit(tmp_path, compare=False))
    assert missing == []
    assert found["compare"] is None and found["adapt"] is None


def test_scan_ignores_generated_copies(tmp_path):
    """自带件跑过一次后会留 .atk_generated/，那不是交付的用例清单。"""
    kit = _kit(tmp_path)
    gen = kit / ".atk_generated"
    gen.mkdir()
    (gen / "accuracy_cases.atk.json").write_text(
        json.dumps(CASES * 5), encoding="utf-8")
    found, _ = adopt_kit._scan(kit)
    assert found["cases"].parent == kit


def test_registered_name_read_from_executor(tmp_path):
    kit = _kit(tmp_path)
    assert adopt_kit._registered_name(kit / "function_ops.py") == "op_public"


def test_fingerprints_cover_every_adopted_file(tmp_path):
    """golden 是自带件的手写实现算的，那份换一个字节标杆就换一批。"""
    kit = _kit(tmp_path)
    found, _ = adopt_kit._scan(kit)
    prints = adopt_kit._fingerprints(found, kit)
    assert set(prints) == {"accuracy_cases.json", "function_ops.py", "accuracy_ops.py"}
    assert all(len(v) == 64 for v in prints.values())


def test_fingerprint_changes_with_content(tmp_path):
    kit = _kit(tmp_path)
    found, _ = adopt_kit._scan(kit)
    before = adopt_kit._fingerprints(found, kit)["function_ops.py"]
    (kit / "function_ops.py").write_text(EXECUTOR + "\n# 改一个字节\n", encoding="utf-8")
    after = adopt_kit._fingerprints(found, kit)["function_ops.py"]
    assert before != after


# ---- 纯 attr 用例的形态识别 ---------------------------------------------

def test_attr_only_cases_recognised():
    assert all(case_shape.is_attr_only(c) for c in CASES)


def test_tensor_case_is_not_attr_only():
    case = {"inputs": [{"name": "self", "type": "tensor", "dtype": "float32",
                        "shape": [4, 4]}]}
    assert not case_shape.is_attr_only(case)


@pytest.mark.parametrize("value,expect", [
    (3, "small"), (4, "medium"), (39, "medium"), (40, "large"), (99, "large"),
])
def test_attr_band_by_cuts(value, expect):
    case = {"inputs": [{"name": "nnz", "type": "attr", "range_values": [value]}]}
    assert case_shape.attr_band(case, "nnz", (4, 40)) == expect


def test_attr_band_unknown_when_value_absent():
    """取不到值落 unknown，**不落 small**：分不出档和「档位是小」是两回事。"""
    case = {"inputs": [{"name": "other", "type": "attr", "range_values": [1]}]}
    assert case_shape.attr_band(case, "nnz", (4, 40)) == "unknown"


# ---- facts 的两个卡点：backend 与 plugin 标杆 ---------------------------
#
# 这两条是「从任务书生成 sparse 用例」路上仅有的必需改动。加它们之前
# S1 硬拒 backend=npu，也硬拒非 torch 的标杆，整条路走不到 S2。

import check_facts  # noqa: E402


def _facts(**over):
    base = {
        "op": "Op", "aclnn_name": "Op", "backend": "aclnn",
        "baseline": "torch.foo", "baseline_source": "taskdoc",
        "accuracy": {"kind": "torch", "source": "taskdoc"},
        "performance": {"kind": "none", "source": "taskdoc"},
        "non_contiguous": {"required": False, "source": "taskdoc"},
    }
    base.update(over)
    return base


def _top_problems(facts):
    problems = []
    check_facts._check_top(problems, facts)
    return problems


def test_backend_npu_is_accepted():
    assert not [p for p in _top_problems(_facts(backend="npu")) if "backend" in p]


def test_backend_unknown_is_rejected():
    assert [p for p in _top_problems(_facts(backend="cuda")) if "backend" in p]


def test_npu_rejects_builtin_accuracy_baseline():
    """builtin 拿 CANN 内置的同名 aclnn 接口当标杆，这条路上没有 aclnn 接口。"""
    facts = _facts(backend="npu", accuracy={"kind": "builtin", "source": "taskdoc"})
    assert [p for p in _top_problems(facts) if "builtin" in p]


def test_npu_rejects_builtin_performance_baseline():
    facts = _facts(backend="npu",
                   performance={"kind": "builtin", "source": "taskdoc"})
    assert [p for p in _top_problems(facts) if "performance.kind" in p]


_TENSOR_PARAM = [{"name": "x", "role": "input", "atk_type": "tensor",
                  "dtypes": ["fp32"], "source": "taskdoc"}]
_ATTR_PARAM = [{"name": "seed", "role": "input", "atk_type": "attr",
                "dtypes": ["int"], "source": "taskdoc"}]


def test_npu_allows_non_contiguous():
    """`--slice_input` 切的是用例里声明的张量，与后端无关。

    **这条曾经被错误地限制过**：当时的前提是「sparse 用例里没有张量」，
    而那是任务方测试脚手架的选择，不是算子的性质。算子按正常范式声明真张量时，
    非连续这一路照样能跑。
    """
    facts = _facts(backend="npu", params=_TENSOR_PARAM,
                   non_contiguous={"required": True, "source": "taskdoc"})
    assert not [p for p in _top_problems(facts) if "non_contiguous" in p]


def test_non_contiguous_rejected_when_inputs_are_all_attr():
    """判据是用例形态不是 backend：入参全是 attr 时切不到执行器现造的那份。

    这一轮会白跑，两轮字节完全相同，而且 ATK 不报错。
    """
    facts = _facts(backend="npu", params=_ATTR_PARAM,
                   non_contiguous={"required": True, "source": "taskdoc"})
    assert [p for p in _top_problems(facts) if "non_contiguous" in p]


def test_non_contiguous_axis_is_independent_of_backend():
    """同一条判据在 aclnn 剖面上也成立——它判的是 params，不看 backend。"""
    attr_only = _facts(backend="aclnn", params=_ATTR_PARAM,
                       non_contiguous={"required": True, "source": "taskdoc"})
    with_tensor = _facts(backend="aclnn", params=_TENSOR_PARAM,
                         non_contiguous={"required": True, "source": "taskdoc"})
    assert [p for p in _top_problems(attr_only) if "non_contiguous" in p]
    assert not [p for p in _top_problems(with_tensor) if "non_contiguous" in p]


def test_npu_allows_plugin_accuracy_baseline():
    """稀疏这类要先拼输入结构才调得动公开接口的算子，只能走 plugin。

    曾有三处文档写「backend=npu 时 accuracy.kind 只能 torch」，照它走会把这类
    算子堵死在 S1，或者逼着挑一个不等价的 torch 接口当标杆。
    """
    facts = _facts(backend="npu", baseline="function_op_cpu",
                   accuracy={"kind": "plugin", "acc": "default", "source": "taskdoc"})
    assert not [p for p in _top_problems(facts) if "accuracy" in p]


def _perf_problems(facts):
    problems = []
    check_facts._check_perf(problems, facts)
    return problems


def test_sampling_needs_all_three_fields():
    """填了 performance.sampling 就三项都要，缺 source 时报告证明不了采的是验收口径。"""
    good = _facts(performance={"kind": "threshold", "criterion": "不低于 0.25 倍",
                               "sampling": {"warmup": 10, "samples": 30,
                                            "source": "任务书 性能要求 第 3 条"},
                               "source": "taskdoc"})
    assert not [p for p in _perf_problems(good) if "sampling" in p]
    for broken in ({"warmup": 10, "samples": 30},
                   {"warmup": 0, "samples": 30, "source": "任务书"},
                   {"samples": 30, "source": "任务书"}):
        facts = _facts(performance={"kind": "threshold", "criterion": "不低于 0.25 倍",
                                    "sampling": broken, "source": "taskdoc"})
        assert [p for p in _perf_problems(facts) if "sampling" in p], broken


def test_sampling_is_optional():
    """多数任务书不规定采样口径，不写这个字段不该报错。"""
    facts = _facts(performance={"kind": "threshold", "criterion": "不低于 0.25 倍",
                                "source": "taskdoc"})
    assert not [p for p in _perf_problems(facts) if "sampling" in p]


def test_plugin_baseline_accepts_registered_name():
    """torch 里没有语义等价接口时，标杆是自写的 CPU 执行器。"""
    facts = _facts(accuracy={"kind": "plugin", "source": "taskdoc"},
                   baseline="function_op_cpu")
    assert not [p for p in _top_problems(facts) if "baseline" in p]


def test_plugin_baseline_rejects_torch_name():
    """填 plugin 却给 torch 接口名，说明 kind 挑错了。"""
    facts = _facts(accuracy={"kind": "plugin", "source": "taskdoc"},
                   baseline="torch.foo")
    assert [p for p in _top_problems(facts) if "baseline" in p]


def test_torch_baseline_still_requires_torch_prefix():
    """缺省那档一个字节没变。"""
    assert [p for p in _top_problems(_facts(baseline="自写的")) if "baseline" in p]
