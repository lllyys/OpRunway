"""自带件路：标杆当场算，没有冻结 golden。

**破坏时全是静默的**：拓扑拼错时 ATK 照跑，报告里被测或标杆那一列整列读不到，
通过率算成 0 而日志里没有报错。真机上翻出来一轮几分钟。
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_atk  # noqa: E402

KIT_YAML = """nodes:
  - backend: npu
    name: npu_under_test
    devices: [0]
    task: ['accuracy']
    post_node: true
  - backend: cpu
    name: cpu_golden
    task: ['accuracy']
"""


@pytest.fixture
def kit(tmp_path, monkeypatch):
    path = tmp_path / "nodes_accuracy.yaml"
    path.write_text(KIT_YAML, encoding="utf-8")
    monkeypatch.setattr(run_atk, "_NODES", path)
    monkeypatch.setattr(run_atk, "_BASELINE_NODE", None)
    monkeypatch.setattr(run_atk, "_ACTIVE_PROFILE", run_atk.PROFILES["npu"])
    return path


def test_baseline_node_is_the_one_that_is_not_under_test(kit):
    assert run_atk._nodes_baseline(kit) == ("cpu", "cpu_golden")


def test_baseline_node_follows_the_profile(tmp_path, monkeypatch):
    """判据是「backend 不是被测剖面那个」，不是写死 cpu。

    aclnn 剖面的被测节点 backend 是 aclnn（报告里那一列的前缀才叫 pyaclnn）。
    写死 cpu 在这个拓扑上认错，而认错不报错。
    """
    path = tmp_path / "n.yaml"
    path.write_text("nodes:\n  - backend: aclnn\n    name: under\n"
                    "  - backend: npu\n    name: ref\n", encoding="utf-8")
    monkeypatch.setattr(run_atk, "_NODES", path)
    monkeypatch.setattr(run_atk, "_ACTIVE_PROFILE", run_atk.PROFILES["aclnn"])
    assert run_atk._nodes_baseline(path) == ("npu", "ref")


def test_ambiguous_topology_refuses_to_guess(tmp_path, monkeypatch):
    path = tmp_path / "n.yaml"
    path.write_text("nodes:\n  - backend: cpu\n    name: a\n  - backend: cpu\n"
                    "    name: b\n  - backend: npu\n    name: c\n", encoding="utf-8")
    monkeypatch.setattr(run_atk, "_ACTIVE_PROFILE", run_atk.PROFILES["npu"])
    assert run_atk._nodes_baseline(path) is None


def test_explicit_baseline_node_wins(kit, monkeypatch):
    monkeypatch.setattr(run_atk, "_BASELINE_NODE", ("cpu", "other"))
    assert run_atk._load_node("不存在的 golden") == ("cpu", "other")


def test_ambiguous_topology_without_override_raises(tmp_path, monkeypatch):
    path = tmp_path / "n.yaml"
    path.write_text("nodes:\n  - backend: cpu\n  - backend: cpu\n", encoding="utf-8")
    monkeypatch.setattr(run_atk, "_NODES", path)
    monkeypatch.setattr(run_atk, "_BASELINE_NODE", None)
    monkeypatch.setattr(run_atk, "_ACTIVE_PROFILE", run_atk.PROFILES["npu"])
    with pytest.raises(SystemExit):
        run_atk._load_node("g")


def test_command_delegates_topology_to_the_kit(kit):
    got = run_atk._node_command("accuracy", "cases.json", "golden", "0", "plug.py")
    assert got[:4] == ["atk", "task", "-c", "cases.json"]
    assert "-n" in got and str(kit) in got
    # 自带件自己描述拓扑，本脚本不许再拼节点，也不许读冻结 golden。
    assert "node" not in got and "accuracy_load" not in got and "golden" not in got
    assert got[got.index("--task") + 1] == "accuracy"
    assert got[got.index("-p") + 1] == "plug.py"


def test_performance_mode_switches_the_task(kit):
    got = run_atk._node_command("performance", "perf.json", "golden", "0", None)
    assert got[got.index("--task") + 1] == "performance_device"
    assert "-p" not in got


def test_pkg_root_falls_back_to_the_case_file_dir(kit, tmp_path):
    cases = tmp_path / "sub" / "cases.json"
    cases.parent.mkdir()
    cases.write_text("[]", encoding="utf-8")
    assert run_atk._pkg_root("golden", cases) == cases.parent


def test_pkg_root_keeps_the_package_layout_off_the_kit_path(monkeypatch, tmp_path):
    monkeypatch.setattr(run_atk, "_NODES", None)
    assert run_atk._pkg_root(tmp_path / "pkg" / "golden", "x.json") == tmp_path / "pkg"


# ---------------------------------------------------- verdict 认自带件布局

import json  # noqa: E402

import verdict  # noqa: E402


@pytest.fixture
def kit_pkg(tmp_path):
    """自带件路的 input/：只有 facts.json 与修复后的用例副本，没有用例包那套。"""
    pkg = tmp_path / "input"
    (pkg / "kit_fixes").mkdir(parents=True)
    (pkg / "kit_fixes" / "cases.json").write_text(json.dumps([
        {"id": 0, "name": "acc-000", "inputs": [{"dtype": "float32"}]},
        {"id": 1, "name": "acc-001", "inputs": [{"dtype": "complex64"}]},
    ]), encoding="utf-8")
    (pkg / "facts.json").write_text(json.dumps({
        "aclnn_name": "aclsparseSpGemm", "backend": "npu",
        "kit": {"cases": "kit_fixes/cases.json", "baseline": "cpu_golden"},
    }), encoding="utf-8")
    return pkg


def test_cases_come_from_the_kit_when_there_is_no_package(kit_pkg):
    facts = json.loads((kit_pkg / "facts.json").read_text(encoding="utf-8"))
    cases, source = verdict._cases_of(kit_pkg, facts)
    assert [c["id"] for c in cases] == [0, 1]
    assert source.endswith("kit_fixes/cases.json")


def test_case_list_also_accepts_the_wrapped_shape(kit_pkg):
    (kit_pkg / "kit_fixes" / "cases.json").write_text(
        json.dumps({"cases": [{"id": 7}]}), encoding="utf-8")
    facts = json.loads((kit_pkg / "facts.json").read_text(encoding="utf-8"))
    cases, _ = verdict._cases_of(kit_pkg, facts)
    assert [c["id"] for c in cases] == [7]


def test_package_path_is_untouched_by_the_kit_branch(tmp_path):
    pkg = tmp_path / "input"
    pkg.mkdir()
    (pkg / "cases.json").write_text(json.dumps([{"id": 3}]), encoding="utf-8")
    cases, source = verdict._cases_of(pkg, {"aclnn_name": "Roll"})
    assert [c["id"] for c in cases] == [3]
    assert source == "cases.json"


def test_baseline_is_the_kit_node_not_a_frozen_golden(kit_pkg):
    facts = json.loads((kit_pkg / "facts.json").read_text(encoding="utf-8"))
    assert verdict._acc_baseline(kit_pkg / "golden", facts) == ("kit", "cpu_golden")
    # 报告那一句不能说「生成侧冻结的 golden（cpu_golden/）」——那个目录不存在。
    line = verdict._acc_summary("kit", "cpu_golden", facts, {"passed": True})
    assert "当场算" in line and "生成侧冻结" not in line and "cpu_golden/" not in line


def test_op_falls_back_to_aclnn_name(kit_pkg):
    facts = json.loads((kit_pkg / "facts.json").read_text(encoding="utf-8"))
    assert (facts.get("op") or facts.get("aclnn_name")) == "aclsparseSpGemm"


# ---------------------------------------------------- 冒烟失败的真因摘取


@pytest.mark.parametrize("line, cause", [
    ('RuntimeError: "addmm_sparse" not implemented for \'Float\' without MKL support',
     "MKL"),
    ("RuntimeError: Float did not match Double", "dtype"),
    ("NotImplementedError: Could not run 'aten::addmm.out' with arguments", "worker"),
    ("ValueError: not enough values to unpack (expected 4, got 0)", "kwargs"),
])
def test_known_symptoms_are_named_with_a_destination(tmp_path, line, cause):
    log = tmp_path / "run.log"
    log.write_text("\n".join(["噪声"] * 50 + [line] + ["栈"] * 50), encoding="utf-8")
    out = "\n".join(run_atk.symptom_lines(log))
    assert cause in out or "真因" in out
    assert "去向" in out


def test_no_marker_match_reports_the_exception_but_guesses_no_destination(tmp_path):
    """不匹配时报异常、**不报去向**。

    报错方向本来就不指向真因，凑一条最像的「去向」会让执行者改错地方；
    但沉默更糟——那等于让他去翻全文。
    """
    log = tmp_path / "run.log"
    log.write_text("Traceback\n  File celery_tasks.py\nSomeUnknownError: 42",
                   encoding="utf-8")
    out = "\n".join(run_atk.symptom_lines(log))
    assert "SomeUnknownError: 42" in out
    assert "未匹配已知真因" in out


def test_missing_log_is_not_an_error(tmp_path):
    assert run_atk.symptom_lines(tmp_path / "nope.log") == []


# ---------------------------------------------------- 体检：静默类与正解

import kit_lint  # noqa: E402


def test_dead_branch_is_marked_invisible_to_smoke(tmp_path, monkeypatch):
    # 判据恒假时精度轮照常通过、通过率还好看，结论却是错的。
    monkeypatch.setattr(kit_lint, "findings", [])
    plugin = tmp_path / "cmp.py"
    plugin.write_text("if case.name == 'total_only':\n    pass\n", encoding="utf-8")
    kit_lint.check_dead_branches([{"name": "acc-spgemm-000"}], tmp_path)
    assert kit_lint.findings, "恒假分支没被查出来"
    assert all(f[4] == kit_lint.SILENT for f in kit_lint.findings)


def test_schema_error_names_what_the_installed_atk_accepts():
    # ATK 只说不合法，不说合法的是什么——少这句就得去 site-packages 读源码。
    class _Field:
        def __init__(self, annotation):
            self.annotation = annotation

    class _Model:
        model_fields = {"outputs": _Field("Optional[Union[str, int]]")}

    got = kit_lint._accepts(_Model, ["2 validation errors", "outputs.str", "  bad"])
    assert "outputs: Optional[Union[str, int]]" in got


def test_accepts_stays_quiet_when_the_field_is_unknown():
    class _Model:
        model_fields = {"outputs": None}

    assert kit_lint._accepts(_Model, ["something else"]) == ""


# ------------------------------------------- 兜底摘取：换算子也要给得出真因


def test_unknown_failure_still_names_the_exception_and_the_case(tmp_path):
    """KIT_SYMPTOMS 是在一个自带件上量的，换算子多半命中不了。

    没有兜底就等于把「去几百行 celery 栈里找真因」还给执行者。
    """
    log = tmp_path / "run.log"
    log.write_text(
        "[ERROR][ForkPoolWorker-2] [opp_tasks.py:332]  case 7 run opp failed: "
        "acc-foo-int8-007 run error!\n"
        "Traceback (most recent call last):\n"
        "ValueError: index 5 is out of bounds for axis 0\n",
        encoding="utf-8")
    out = "\n".join(run_atk.symptom_lines(log))
    assert "case 7 acc-foo-int8-007" in out
    assert "ValueError: index 5 is out of bounds" in out
    assert "未匹配已知真因" in out


def test_atk_wrapper_exception_is_not_reported_as_the_cause(tmp_path):
    """包装异常套在真异常外面，报它会把人指向 ATK 而不是自带件。"""
    log = tmp_path / "run.log"
    log.write_text("KeyError: 'nnz'\natk.TerminalCaseFailure: case already terminal\n",
                   encoding="utf-8")
    out = "\n".join(run_atk.symptom_lines(log))
    assert "KeyError" in out and "TerminalCaseFailure" not in out


def test_wrapper_alone_is_still_better_than_silence(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("atk.TerminalCaseFailure: case already terminal\n", encoding="utf-8")
    assert "TerminalCaseFailure" in "\n".join(run_atk.symptom_lines(log))


def test_known_symptom_still_wins_over_the_generic_fallback(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("RuntimeError: Float did not match Double\n", encoding="utf-8")
    out = "\n".join(run_atk.symptom_lines(log))
    assert "去向" in out and "未匹配已知真因" not in out


def test_a_log_without_any_exception_says_so(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("INFO 开始\nINFO 结束\n", encoding="utf-8")
    assert run_atk.symptom_lines(log) == []


# ------------------------------------------ 复现包：性能与精度各一份

import make_repro  # noqa: E402


@pytest.fixture
def perf_site(tmp_path):
    """一个自带件路的现场：性能数来自外部量测件，ATK 那轮没跑。"""
    pkg = tmp_path / "input"
    (pkg / "kit_fixes").mkdir(parents=True)
    (pkg / "kit_fixes" / "perf_harness_x.py").write_text(
        "# 量测件\nrow = {'under_test_us': 1.0}\n", encoding="utf-8")
    (pkg / "kit_fixes" / "run_perf.sh").write_text(
        "python3 perf_harness_x.py --facts ../facts.json\n", encoding="utf-8")
    stage = tmp_path / "work" / "stage"
    (stage / "p_table_shards").mkdir(parents=True)
    (stage / "p_table_shards" / "P-01.json").write_text("[]", encoding="utf-8")
    (stage / "kit_perf_exchange.json").write_text(
        json.dumps({"unit": "us", "cases": []}), encoding="utf-8")
    return pkg, stage


def test_performance_repro_is_its_own_package(perf_site, tmp_path):
    pkg, stage = perf_site
    out = tmp_path / "repro"
    out.mkdir()
    got = make_repro._copy_perf_kit(pkg, stage, out)
    assert got is not None and got["has_run"]
    # 量测件、交换格式、分片用例、跑法四样都要在，缺一样就复现不了。
    assert (out / "perf" / "perf_harness_x.py").is_file()
    assert (out / "perf" / "kit_perf_exchange.json").is_file()
    assert (out / "perf" / "p_table_shards" / "P-01.json").is_file()
    assert (out / "perf" / "run.sh").is_file()


def test_the_measuring_script_is_found_by_content_not_by_name(tmp_path):
    # 外部量测件的命名逐任务不同，按名字认会漏。
    pkg = tmp_path / "input"
    (pkg / "kit_fixes").mkdir(parents=True)
    (pkg / "kit_fixes" / "benchmark.py").write_text(
        "kernel_total_us = 0\n", encoding="utf-8")
    (pkg / "kit_fixes" / "helper.py").write_text("x = 1\n", encoding="utf-8")
    out = tmp_path / "repro"
    out.mkdir()
    got = make_repro._copy_perf_kit(pkg, tmp_path / "nope", out)
    assert got and (out / "perf" / "benchmark.py").is_file()
    assert not (out / "perf" / "helper.py").exists()


def test_no_external_measurement_means_no_perf_package(tmp_path):
    # ATK 那轮采得到数时不需要这一份，不建空壳目录。
    pkg = tmp_path / "input"
    pkg.mkdir()
    out = tmp_path / "repro"
    out.mkdir()
    assert make_repro._copy_perf_kit(pkg, tmp_path / "nope", out) is None
    assert not (out / "perf").exists()
