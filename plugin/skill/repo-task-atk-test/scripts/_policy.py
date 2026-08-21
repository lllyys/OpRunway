"""加载并校验验收政策。"""

import hashlib
import json
from pathlib import Path


class PolicyError(ValueError):
    """验收政策无法安全使用。"""


def interface_policy_path():
    return (
        Path(__file__).resolve().parents[1]
        / "references"
        / "interface-policy.json"
    )


def verdict_policy_path():
    return (
        Path(__file__).resolve().parents[1]
        / "references"
        / "verdict-policy.json"
    )


def _validate_schema(policy):
    if policy.get("schema_version") != 1:
        raise PolicyError("[POLICY_SCHEMA] schema_version 必须为 1")


def validate_interface_policy(policy):
    _validate_schema(policy)

    modes = policy.get("interface_modes")
    if not isinstance(modes, dict) or not modes:
        raise PolicyError("[POLICY_MODES] interface_modes 必须为非空对象")
    for name, mode in modes.items():
        backends = mode.get("backends") if isinstance(mode, dict) else None
        valid = (
            isinstance(backends, list)
            and backends
            and all(isinstance(item, str) and item for item in backends)
        )
        if not valid:
            raise PolicyError(
                f"[POLICY_BACKENDS] {name} 的 backends 必须为非空字符串列表"
            )
        if not isinstance(mode.get("acceptance_enabled"), bool):
            raise PolicyError(
                f"[POLICY_ENABLED] {name} 的 acceptance_enabled 必须为布尔值"
            )


def validate_verdict_policy(policy):
    _validate_schema(policy)

    accuracy = policy.get("accuracy")
    comparators = (
        accuracy.get("allowed_comparators")
        if isinstance(accuracy, dict)
        else None
    )
    valid = (
        isinstance(comparators, list)
        and comparators
        and all(isinstance(item, str) and item for item in comparators)
    )
    if not valid:
        raise PolicyError(
            "[POLICY_ACCURACY] allowed_comparators 必须为非空字符串列表"
        )

    exclusions = policy.get("case_exclusions")
    exclusion_causes = (
        exclusions.get("allowed_causes")
        if isinstance(exclusions, dict)
        else None
    )
    valid = (
        isinstance(exclusion_causes, list)
        and exclusion_causes
        and all(isinstance(item, str) and item for item in exclusion_causes)
        and len(exclusion_causes) == len(set(exclusion_causes))
    )
    if not valid:
        raise PolicyError(
            "[POLICY_EXCLUSIONS] allowed_causes 必须为非空且不重复的字符串列表"
        )

    rules = policy.get("failure_attribution")
    if not isinstance(rules, list):
        raise PolicyError("[POLICY_FAILURES] failure_attribution 必须为列表")
    identifiers = []
    for rule in rules:
        identifier = rule.get("id") if isinstance(rule, dict) else None
        markers = rule.get("markers") if isinstance(rule, dict) else None
        if not identifier or not isinstance(markers, list) or not markers:
            raise PolicyError(
                "[POLICY_FAILURE] 每条归因必须提供 id 和 markers"
            )
        identifiers.append(identifier)
    if len(identifiers) != len(set(identifiers)):
        raise PolicyError("[POLICY_FAILURE_ID] 归因 id 不得重复")

    allowed_causes = set(identifiers) | {"accuracy_gap"}
    conclusion_causes = policy.get("conclusion_causes")
    valid = (
        isinstance(conclusion_causes, list)
        and set(conclusion_causes) <= allowed_causes
    )
    if not valid:
        raise PolicyError(
            "[POLICY_CONCLUSION] conclusion_causes 包含未声明原因"
        )


def validate_policy(policy):
    validate_interface_policy(policy)
    validate_verdict_policy(policy)


def _load(policy_path):
    try:
        with policy_path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise PolicyError(f"[POLICY_LOAD] 无法加载验收政策：{error}") from error


def load_interface_policy(path=None):
    policy_path = Path(path) if path else interface_policy_path()
    policy = _load(policy_path)
    validate_interface_policy(policy)
    return policy


def load_verdict_policy(path=None):
    policy_path = Path(path) if path else verdict_policy_path()
    policy = _load(policy_path)
    validate_verdict_policy(policy)
    return policy


def load_policy(path=None):
    if path:
        policy = _load(Path(path))
        validate_policy(policy)
        return policy

    interface = load_interface_policy()
    verdict = load_verdict_policy()
    policy = {**interface, **verdict}
    validate_policy(policy)
    return policy


def policy_sha256():
    return {
        "interface": hashlib.sha256(
            interface_policy_path().read_bytes()
        ).hexdigest(),
        "verdict": hashlib.sha256(
            verdict_policy_path().read_bytes()
        ).hexdigest(),
    }
