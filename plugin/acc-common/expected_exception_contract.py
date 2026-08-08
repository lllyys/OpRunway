"""Expected-exception result contract shared by producer, runner and judges."""

SCHEMA = "oprunway.expected_exception"
VERSION = 1
OBSERVED_SCHEMA = "oprunway.observed_exception"
OBSERVED_VERSION = 1


def _exact(value, keys, where):
    if not isinstance(value, dict) or set(value) != set(keys):
        got = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ValueError(f"{where} 键须恰为 {sorted(keys)}，得 {got}")


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} 须为非空字符串")
    return value


def normalize_contract(value, where="expected_exception"):
    _exact(value, {"schema", "schema_version", "reference", "expected"}, where)
    if value["schema"] != SCHEMA or value["schema_version"] != VERSION \
            or isinstance(value["schema_version"], bool):
        raise ValueError(f"{where} schema/version 非法")
    reference = value["reference"]
    _exact(reference, {"class", "phase", "message"}, f"{where}.reference")
    if reference["phase"] != "golden":
        raise ValueError(f"{where}.reference.phase 须为 golden")
    expected = value["expected"]
    _exact(expected, {"class", "phase", "message_policy"}, f"{where}.expected")
    if expected["phase"] != "execute":
        raise ValueError(f"{where}.expected.phase 须为 execute")
    policy = expected["message_policy"]
    _exact(policy, {"kind", "value"}, f"{where}.expected.message_policy")
    if policy["kind"] != "exact":
        raise ValueError(f"{where}.expected.message_policy.kind 仅支持 exact")
    for path, text in (("reference.class", reference["class"]),
                       ("reference.message", reference["message"]),
                       ("expected.class", expected["class"]),
                       ("expected.message_policy.value", policy["value"])):
        _text(text, f"{where}.{path}")
    return {
        "schema": SCHEMA, "schema_version": VERSION,
        "reference": dict(reference),
        "expected": {"class": expected["class"], "phase": "execute",
                     "message_policy": dict(policy)},
    }


def from_golden_exception(error):
    name, message = type(error).__name__, str(error)
    _text(message, "golden exception message")
    return normalize_contract({
        "schema": SCHEMA, "schema_version": VERSION,
        "reference": {"class": name, "phase": "golden", "message": message},
        "expected": {"class": name, "phase": "execute",
                     "message_policy": {"kind": "exact", "value": message}},
    })


def observed_exception(*, error_class, phase, message):
    value = {"schema": OBSERVED_SCHEMA, "schema_version": OBSERVED_VERSION,
             "class": error_class, "phase": phase, "message": message}
    return normalize_observed(value)


def normalize_observed(value, where="observed_exception"):
    _exact(value, {"schema", "schema_version", "class", "phase", "message"}, where)
    if value["schema"] != OBSERVED_SCHEMA or value["schema_version"] != OBSERVED_VERSION \
            or isinstance(value["schema_version"], bool):
        raise ValueError(f"{where} schema/version 非法")
    if value["phase"] != "execute":
        raise ValueError(f"{where}.phase 须为 execute")
    _text(value["class"], f"{where}.class")
    _text(value["message"], f"{where}.message")
    return dict(value)


def compare(contract, observed):
    expected = normalize_contract(contract)["expected"]
    if observed is None:
        return False, "expected_exception_but_execution_succeeded"
    try:
        actual = normalize_observed(observed)
    except ValueError as ex:
        return False, f"observed_exception_invalid: {ex}"
    if actual["class"] != expected["class"]:
        return False, f"exception_class_mismatch: {actual['class']} != {expected['class']}"
    if actual["phase"] != expected["phase"]:
        return False, f"exception_phase_mismatch: {actual['phase']} != {expected['phase']}"
    if actual["message"] != expected["message_policy"]["value"]:
        return False, "exception_message_mismatch"
    return True, "expected_exception_matched"
