import copy

import pytest

import finalize_clean_acceptance as F


def _docs():
    spec = {"op": "Median", "runner_form": "aclnn_py"}
    evidence = {
        "evidence_grade": "acceptance_candidate",
        "runner_source": "user",
        "runner_form": "aclnn_py",
        "repo_mode": "aclnn_py",
    }
    verdict = {
        "overall": {
            "verdict": "pass",
            "counts": {
                "fail": 0, "uncertain": 0, "risk": 0, "gaps": 0,
                "golden_blocked": 0, "contract_problems": 0,
            },
            "risk": [],
            "uncertain": [],
        },
        "catlass_compare_na": ["c0"],
    }
    perf = {
        "summary": {
            "status": "ok", "blocked": 0, "perf_cases": 2, "达标": 2,
            "cases_scored": 2, "non_passing": 0,
        }
    }
    return spec, evidence, verdict, perf


def test_build_clean_acceptance():
    acc = F.build_clean_acceptance(*_docs(), {})
    assert acc["overall"] == "PASS"
    assert acc["state"] == "PASSED"
    assert acc["exit_code"] == 0
    assert acc["gate"] == {"passed": True, "errors": {}}


def test_build_clean_acceptance_allows_receipt_gated_cpp_extension_source():
    spec, evidence, verdict, perf = copy.deepcopy(_docs())
    spec["runner_form"] = "cpp_extension"
    evidence.update(
        runner_form="cpp_extension",
        runner_source="generated_official_cpp_extension",
        repo_mode="cpp_extension",
    )
    acc = F.build_clean_acceptance(spec, evidence, verdict, perf, {})
    assert acc["overall"] == "PASS"
    assert acc["repo_mode"] == "cpp_extension"


@pytest.mark.parametrize("mutator", [
    lambda s, e, v, p: e.update(runner_source="builtin"),
    lambda s, e, v, p: v["overall"].update(verdict="needs_review"),
    lambda s, e, v, p: p["summary"].update(达标=1),
])
def test_build_clean_acceptance_refuses_non_clean(mutator):
    docs = list(copy.deepcopy(_docs()))
    mutator(*docs)
    with pytest.raises(F.FinalizeError):
        F.build_clean_acceptance(*docs, {})


def test_build_clean_acceptance_refuses_gate_error():
    with pytest.raises(F.FinalizeError):
        F.build_clean_acceptance(*_docs(), {"task3": ["missing"]})
