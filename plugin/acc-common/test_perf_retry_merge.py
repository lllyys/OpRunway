import copy

import pytest

import perf_retry_merge as M


def _side(us=10.0):
    return {
        "behavior": "npu",
        "us": us,
        "scope": "kernel_only",
        "execution_path": "device_kernel",
    }


def _record(case_id, valid=True):
    rec = {
        "case_id": case_id,
        "custom": _side(10.0),
        "baseline": _side(20.0),
        "custom_timed": True,
        "baseline_timed": True,
        "comparability": "fair",
        "speedup": 2.0,
    }
    if not valid:
        rec["baseline"] = {
            "behavior": "execution_failed",
            "us": None,
            "scope": None,
            "execution_path": None,
        }
        rec["baseline_timed"] = False
        rec["comparability"] = None
        rec["speedup"] = None
    return rec


def _doc(records):
    return {
        "op": "Witness",
        "scope": "kernel_only",
        "warmup": 5,
        "repeat": 20,
        "device": 1,
        "collection": {
            "custom": {"collector": "msprof_cli", "warmup": 5, "repeat": 20},
            "baseline": {"collector": "msprof_cli", "warmup": 5, "repeat": 20},
        },
        "baseline_source": "torch_npu",
        "records": records,
        "skipped": [],
    }


def test_merge_replaces_only_invalid_primary_and_preserves_order():
    primary = _doc([_record("a", valid=True), _record("b", valid=False), _record("c", valid=False)])
    retry1 = _doc([_record("b", valid=True), _record("c", valid=False)])
    retry2 = _doc([_record("c", valid=True)])

    merged = M.merge_retry_docs(primary, [retry1, retry2])

    assert [r["case_id"] for r in merged["records"]] == ["a", "b", "c"]
    assert all(M.valid_pair(r) for r in merged["records"])
    assert merged["retry_merge"]["remaining_invalid_case_ids"] == []
    assert merged["retry_merge"]["replacements"] == [
        {"case_id": "b", "retry_index": 1},
        {"case_id": "c", "retry_index": 2},
    ]
    assert not M.valid_pair(primary["records"][1])  # 输入不被原地修改


def test_merge_rejects_contract_mismatch():
    primary = _doc([_record("a", valid=False)])
    retry = _doc([_record("a", valid=True)])
    retry["repeat"] = 21
    with pytest.raises(M.PerfRetryMergeError, match="repeat"):
        M.merge_retry_docs(primary, [retry])


def test_merge_rejects_unknown_case():
    primary = _doc([_record("a", valid=False)])
    retry = _doc([_record("other", valid=True)])
    with pytest.raises(M.PerfRetryMergeError, match="primary 不存在"):
        M.merge_retry_docs(primary, [retry])


def test_merge_can_add_new_required_cases_and_records_invalid_addition():
    primary = _doc([_record("a", valid=True), _record("b", valid=False)])
    retry = _doc([
        _record("b", valid=True),
        _record("c", valid=True),
        _record("d", valid=False),
    ])
    merged = M.merge_retry_docs(
        primary, [retry], required_case_ids=["a", "b", "c", "d"])
    assert [r["case_id"] for r in merged["records"]] == ["a", "b", "c", "d"]
    assert merged["retry_merge"]["replacements"] == [
        {"case_id": "b", "retry_index": 1}]
    assert merged["retry_merge"]["additions"] == [
        {"case_id": "c", "retry_index": 1},
        {"case_id": "d", "retry_index": 1},
    ]
    assert merged["retry_merge"]["remaining_invalid_case_ids"] == ["d"]
    assert merged["collection_checkpoint"] == {
        "complete": True,
        "completed": 4,
        "planned": 4,
        "planned_case_ids": ["a", "b", "c", "d"],
    }


def test_required_cases_reject_primary_or_retry_outside_contract():
    primary = _doc([_record("a", valid=True)])
    with pytest.raises(M.PerfRetryMergeError, match="primary 含"):
        M.merge_retry_docs(primary, [], required_case_ids=["b"])
    with pytest.raises(M.PerfRetryMergeError, match="required_case_ids 不存在"):
        M.merge_retry_docs(
            primary, [_doc([_record("x", valid=True)])],
            required_case_ids=["a", "b"])


def test_merge_rejects_overwrite_of_already_valid_primary():
    primary = _doc([_record("a", valid=True)])
    retry = _doc([_record("a", valid=True)])
    with pytest.raises(M.PerfRetryMergeError, match="禁止用重采择优"):
        M.merge_retry_docs(primary, [retry])


def test_invalid_retry_is_audited_but_not_used():
    primary = _doc([_record("a", valid=False)])
    retry = copy.deepcopy(primary)
    merged = M.merge_retry_docs(primary, [retry])
    assert merged["retry_merge"]["replacements"] == []
    assert merged["retry_merge"]["remaining_invalid_case_ids"] == ["a"]
