"""Explicit golden marker and call-status-derived expected-exception contract."""

import hashlib
import json


MARKER_SCHEMA = "oprunway.golden_expected_exception_marker"
MARKER_VERSION = 1
SCHEMA = "oprunway.expected_exception"
VERSION = 1
OBSERVED_SCHEMA = "oprunway.observed_exception"
OBSERVED_VERSION = 1

_EXPECTED_RETURN_CATEGORIES = frozenset({
    "stage1_nonzero", "executor_null",
})
_OBSERVED_RETURN_CATEGORIES = _EXPECTED_RETURN_CATEGORIES | {
    "stage2_nonzero", "call_succeeded",
}


def _exact(value, keys, where):
    if not isinstance(value, dict) or set(value) != set(keys):
        got = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ValueError(f"{where} 键须恰为 {sorted(keys)}，得 {got}")


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} 须为非空字符串")
    return value


def _sha(value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_marker(value, where="golden_expected_exception_marker"):
    _exact(value, {"schema", "schema_version", "reference", "expected"}, where)
    if value["schema"] != MARKER_SCHEMA or value["schema_version"] != MARKER_VERSION \
            or isinstance(value["schema_version"], bool):
        raise ValueError(f"{where} schema/version 非法")
    reference = value["reference"]
    _exact(reference, {"class", "phase", "message"}, f"{where}.reference")
    if reference["phase"] != "golden":
        raise ValueError(f"{where}.reference.phase 须为 golden")
    _text(reference["class"], f"{where}.reference.class")
    _text(reference["message"], f"{where}.reference.message")
    expected = value["expected"]
    _exact(expected, {"phase", "return_categories", "output_written"},
           f"{where}.expected")
    if expected["phase"] != "execute":
        raise ValueError(f"{where}.expected.phase 须为 execute")
    categories = expected["return_categories"]
    if (not isinstance(categories, list) or not categories
            or any(not isinstance(item, str) or item not in _EXPECTED_RETURN_CATEGORIES
                   for item in categories)
            or len(categories) != len(set(categories))):
        raise ValueError(f"{where}.expected.return_categories 非法")
    if expected["output_written"] is not False:
        raise ValueError(f"{where}.expected.output_written 当前仅允许 false")
    return {
        "schema": MARKER_SCHEMA, "schema_version": MARKER_VERSION,
        "reference": dict(reference),
        "expected": {
            "phase": "execute", "return_categories": sorted(categories),
            "output_written": False,
        },
    }


def marker_or_none(value):
    if not isinstance(value, dict) or value.get("schema") != MARKER_SCHEMA:
        return None
    return normalize_marker(value)


def contract_from_marker(value):
    marker = normalize_marker(value)
    return {
        "schema": SCHEMA, "schema_version": VERSION,
        "authorization": {
            "kind": "golden_explicit_marker_v1",
            "marker_sha256": _sha(marker),
        },
        "reference": dict(marker["reference"]),
        "expected": dict(marker["expected"]),
    }


def normalize_contract(value, where="expected_exception"):
    _exact(value, {"schema", "schema_version", "authorization", "reference", "expected"},
           where)
    if value["schema"] != SCHEMA or value["schema_version"] != VERSION \
            or isinstance(value["schema_version"], bool):
        raise ValueError(f"{where} schema/version 非法")
    authorization = value["authorization"]
    _exact(authorization, {"kind", "marker_sha256"}, f"{where}.authorization")
    if authorization["kind"] != "golden_explicit_marker_v1" \
            or not isinstance(authorization["marker_sha256"], str) \
            or len(authorization["marker_sha256"]) != 64 \
            or any(c not in "0123456789abcdef" for c in authorization["marker_sha256"]):
        raise ValueError(f"{where}.authorization 非法")
    marker = normalize_marker({
        "schema": MARKER_SCHEMA, "schema_version": MARKER_VERSION,
        "reference": value["reference"], "expected": value["expected"],
    }, where=f"{where}.marker_projection")
    if authorization["marker_sha256"] != _sha(marker):
        raise ValueError(f"{where}.authorization.marker_sha256 与契约内容漂移")
    return contract_from_marker(marker)


def _normalize_call_status(value, where="call_status"):
    _exact(value, {"schema", "schema_version", "stage1_ret", "workspace_size",
                   "executor_null", "stage2_called", "stage2_ret"}, where)
    if value["schema"] != "oprunway.cpp_extension_call_status" \
            or type(value["schema_version"]) is not int or value["schema_version"] != 1 \
            or type(value["stage1_ret"]) is not int \
            or type(value["workspace_size"]) is not int or value["workspace_size"] < 0 \
            or type(value["executor_null"]) is not bool \
            or type(value["stage2_called"]) is not bool \
            or (value["stage2_ret"] is not None and type(value["stage2_ret"]) is not int):
        raise ValueError(f"{where} 类型/值非法")
    bad_stage1 = value["stage1_ret"] != 0 or value["executor_null"]
    if (bad_stage1 and (value["stage2_called"] or value["stage2_ret"] is not None)) \
            or (not value["stage2_called"] and value["stage2_ret"] is not None) \
            or (value["stage2_called"] and value["stage2_ret"] is None):
        raise ValueError(f"{where} 两段式状态组合非法")
    return dict(value)


def observed_from_call_status(call_status, *, output_written):
    status = _normalize_call_status(call_status)
    if type(output_written) is not bool:
        raise ValueError("output_written 须为 bool")
    if status["stage1_ret"] != 0:
        category = "stage1_nonzero"
    elif status["executor_null"]:
        category = "executor_null"
    elif status["stage2_called"] and status["stage2_ret"] != 0:
        category = "stage2_nonzero"
    else:
        category = "call_succeeded"
    return {
        "schema": OBSERVED_SCHEMA, "schema_version": OBSERVED_VERSION,
        "phase": "execute", "return_category": category,
        "call_status": status, "output_written": output_written,
    }


def normalize_observed(value, where="observed_exception"):
    _exact(value, {"schema", "schema_version", "phase", "return_category",
                   "call_status", "output_written"}, where)
    if value["schema"] != OBSERVED_SCHEMA or value["schema_version"] != OBSERVED_VERSION \
            or isinstance(value["schema_version"], bool) or value["phase"] != "execute" \
            or value["return_category"] not in _OBSERVED_RETURN_CATEGORIES:
        raise ValueError(f"{where} schema/version/phase/return_category 非法")
    rebuilt = observed_from_call_status(
        value["call_status"], output_written=value["output_written"])
    if rebuilt != value:
        raise ValueError(f"{where} return_category 与 call_status 漂移")
    return rebuilt


def compare(contract, observed):
    expected = normalize_contract(contract)["expected"]
    if observed is None:
        return False, "expected_exception_but_no_trusted_call_observation"
    try:
        actual = normalize_observed(observed)
    except ValueError as ex:
        return False, f"observed_exception_invalid: {ex}"
    if actual["phase"] != expected["phase"]:
        return False, f"exception_phase_mismatch: {actual['phase']} != {expected['phase']}"
    if actual["return_category"] not in expected["return_categories"]:
        return False, f"return_category_mismatch: {actual['return_category']}"
    if actual["output_written"] is not expected["output_written"]:
        return False, "output_written_mismatch"
    return True, "expected_exception_matched"
