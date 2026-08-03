import render_precision_retest_markdown as R


def test_render_keeps_base_and_retest_semantics_separate():
    text = R.render(
        {
            "directive_id": "human-1",
            "attempt_kind": "relaxed_rerun",
            "human_instruction": "按新标准重测",
            "case_ids": ["c1"],
        },
        {"overall": "FAIL"},
        {"overall": {"verdict": "pass", "counts": {"fail": 0}}},
        {
            "policy_source": "relaxed:human-1",
            "precision_verdict": "pass",
            "gate": {"passed": True, "errors": {}},
            "requires_human_cp": True,
            "perf_source": "inherited_from_base",
        },
        {
            "lifecycle": "completed",
            "completed_at": "2026-07-29T23:59:00Z",
            "acceptance_verdict": None,
        },
    )
    assert "基础总体裁决：`FAIL`" in text
    assert "validator 精度裁决：`pass`" in text
    assert "需要人工处置：`true`" in text
    assert "性能未重测" in text
    assert "receipt acceptance verdict：`None`" in text


def test_render_gate_failure_includes_errors_not_pass():
    text = R.render(
        {
            "directive_id": "human-2",
            "attempt_kind": "same_policy_rerun",
            "human_instruction": "重测",
            "case_ids": ["c1"],
        },
        {"overall": "FAIL"},
        {"overall": {"verdict": "fail", "counts": {"fail": 1}}},
        {
            "policy_source": "base_spec",
            "precision_verdict": "fail",
            "gate": {"passed": False, "errors": {"task2": ["missing"]}},
            "requires_human_cp": False,
            "perf_source": "inherited_from_base",
        },
        {
            "lifecycle": "completed",
            "completed_at": "2026-07-29T23:59:00Z",
            "acceptance_verdict": None,
        },
    )
    assert "Task 2 证据门：`FAILED`" in text
    assert '"missing"' in text
