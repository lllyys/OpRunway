"""随机 capability 的正式收据组装器（stdlib-only）。

输入只能是已执行 DUT/reference 的二元序列与 caseset 外部身份；本模块不 import torch、
不按算子名分派，也不写总体 verdict。它先生成独立 RNG 前提收据；exact 不一致时返回
``formal=None``，从物理上阻止正式统计证据产生。只有前提通过后才聚合概率边界、重复
序列及变 seed/offset 的统计计数。
"""

from __future__ import annotations

import hashlib
import json

import stochastic_contract as SC


class StochasticCollectorError(ValueError):
    pass


def device_identity(index, soc, torch_version, torch_npu_version):
    """父 driver 与隔离 reference worker 共用的同设备身份算法。"""
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise StochasticCollectorError("NPU device index 须为非负整数")
    body = {
        "type": "npu", "index": index,
        "soc": str(soc),
        "torch_version": str(torch_version),
        "torch_npu_version": str(torch_npu_version),
    }
    return {
        "type": "npu", "index": index, "soc": str(soc),
        "runtime_fingerprint": SC.canonical_sha256(body),
    }


def _binary(value, where, *, allow_empty=False):
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, (list, tuple)):
        if any(isinstance(item, bool) or item not in (0, 1) for item in value):
            raise StochasticCollectorError(f"{where} 含非二元值")
        data = bytes(value)
    else:
        raise StochasticCollectorError(f"{where} 须为 bytes 或 0/1 序列")
    if not data and not allow_empty:
        raise StochasticCollectorError(f"{where} 为空")
    if any(item not in (0, 1) for item in data):
        raise StochasticCollectorError(f"{where} 含非二元值")
    return data


def validate_caseset_bindings(contract, caseset):
    plan = SC.build_case_plan(contract)
    ledger = caseset.get("stochastic_ledger") if isinstance(caseset, dict) else None
    expected_ledger = {
        "schema": SC.PLAN_SCHEMA,
        "schema_version": SC.SCHEMA_VERSION,
        "contract_sha256": SC.canonical_sha256(contract),
        "plan_sha256": SC.canonical_sha256(plan),
        "witness_profile_id": contract["statistics"]["witness_profile_id"],
    }
    if not isinstance(ledger, dict):
        raise StochasticCollectorError("caseset 缺 stochastic_ledger")
    for key, value in expected_ledger.items():
        if ledger.get(key) != value:
            raise StochasticCollectorError(
                f"caseset.stochastic_ledger.{key} 与规范化 contract/plan 漂移")
    sample_count = ledger.get("sample_count")
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) \
            or sample_count < contract["statistics"]["min_samples"]:
        raise StochasticCollectorError("caseset.stochastic_ledger.sample_count 非法或低于 min_samples")
    coverage_rows = ledger.get("coverage_roles", [])
    if not isinstance(coverage_rows, list):
        raise StochasticCollectorError("stochastic_ledger.coverage_roles 须为列表")
    role_plan = list(plan["cases"])
    for i, row in enumerate(coverage_rows):
        if not isinstance(row, dict) or set(row) != {
                "role", "purpose", "probability", "values", "profile_id"} \
                or row.get("purpose") != "structural_boundary_exact" \
                or row.get("probability") != 0.0:
            raise StochasticCollectorError(f"coverage_roles[{i}] 非法")
        role_plan.append(row)
    expected_roles = [row["role"] for row in role_plan]
    if ledger.get("roles") != expected_roles or len(expected_roles) != len(set(expected_roles)):
        raise StochasticCollectorError("stochastic_ledger.roles 与正式/结构 role plan 漂移")
    cases = caseset.get("cases")
    if not isinstance(cases, list) or len(cases) != len(role_plan):
        raise StochasticCollectorError("stochastic cases 未完整覆盖 role plan")
    by_role = {}
    plan_by_role = {row["role"]: row for row in role_plan}
    for case in cases:
        binding = case.get("stochastic") if isinstance(case, dict) else None
        role = binding.get("role") if isinstance(binding, dict) else None
        if role not in plan_by_role or role in by_role:
            raise StochasticCollectorError(f"stochastic case role 缺失/重复/计划外：{role!r}")
        planned = plan_by_role[role]
        binding_sample_count = binding.get("sample_count") if isinstance(binding, dict) else None
        if planned.get("purpose") == "structural_boundary_exact":
            output_shape = ((case.get("parameter_contract") or {}).get("output") or {}).get("shape")
            expected_count = 1
            if not isinstance(output_shape, list):
                raise StochasticCollectorError(f"coverage role={role!r} 缺 output shape")
            for dim in output_shape:
                expected_count *= dim
            if binding_sample_count != expected_count:
                raise StochasticCollectorError(f"coverage role={role!r} sample_count 与 output shape 漂移")
        else:
            expected_count = sample_count
        expected = {
            "contract_sha256": expected_ledger["contract_sha256"],
            "plan_sha256": expected_ledger["plan_sha256"],
            "witness_profile_id": expected_ledger["witness_profile_id"],
            "role": role,
            "purpose": planned["purpose"],
            "probability": planned["probability"],
            "values": planned["values"],
            "sample_count": expected_count,
        }
        if binding != expected or (case.get("expected") or {}).get("stochastic") != expected:
            raise StochasticCollectorError(f"case role={role!r} 的 stochastic binding 漂移")
        attrs = case.get("attrs") or {}
        profile_inputs = ((case.get("parameter_contract") or {}).get("inputs") or [])
        scalar_values = {
            item.get("name"): item.get("value") for item in profile_inputs
            if isinstance(item, dict) and item.get("kind") == "scalar"
        }
        for name, value in planned["values"].items():
            actual = (scalar_values[name] if name in scalar_values else attrs.get(name))
            if actual != value:
                raise StochasticCollectorError(
                    f"case role={role!r} 的参数 {name!r} 未逐字兑现 role plan")
        by_role[role] = case
    if set(by_role) != set(plan_by_role):
        raise StochasticCollectorError("stochastic case role 集与 plan 不一致")
    return plan, by_role, sample_count


def _counts(left, right=None):
    result = {
        "sample_count": len(left),
        "ones_count": sum(left),
        "sha256": hashlib.sha256(left).hexdigest(),
    }
    if right is not None:
        if len(left) != len(right):
            raise StochasticCollectorError("随机序列长度不一致")
        result.update({
            "other_ones_count": sum(right),
            "other_sha256": hashlib.sha256(right).hexdigest(),
            "joint_ones_count": sum(a == 1 and b == 1 for a, b in zip(left, right)),
            "hamming_count": sum(a != b for a, b in zip(left, right)),
        })
    return result


def collect(contract_value, caseset, dut_sequences, reference_sequences, device):
    """返回 ``(precondition_receipt, formal_evidence_or_none)``。"""
    contract = SC.normalize_contract(contract_value)
    plan, _cases, sample_count = validate_caseset_bindings(contract, caseset)
    # Formal statistics depend only on the contract's witness roles.  Structural
    # profile boundaries are a separate execution/coverage partition: a rejected
    # rank or dtype remains recorded by the driver but cannot erase valid witness
    # statistics from another profile.
    roles = [row["role"] for row in plan["cases"]]
    if not isinstance(dut_sequences, dict) or set(dut_sequences) != set(roles):
        raise StochasticCollectorError("DUT 序列 role 集与 formal witness plan 漂移")
    dut = {role: _binary(
        dut_sequences[role], f"dut[{role}]")
        for role in roles}
    by_role = {case["stochastic"]["role"]: case for case in caseset["cases"]}
    for role, value in dut.items():
        if len(value) != by_role[role]["stochastic"]["sample_count"]:
            raise StochasticCollectorError(f"DUT role={role!r} 序列 sample_count 与 case binding 不一致")
    pre_roles = [row["role"] for row in plan["cases"]
                 if row["purpose"] == "rng_consumption_precondition"]
    if not pre_roles:
        raise StochasticCollectorError("stochastic plan 缺 interior RNG precondition role")
    if not isinstance(reference_sequences, dict) or set(reference_sequences) != set(pre_roles):
        raise StochasticCollectorError("reference 序列未逐项覆盖 RNG precondition roles")
    reference = {
        role: _binary(reference_sequences[role], f"reference[{role}]")
        for role in pre_roles
    }
    if any(len(value) != sample_count for value in reference.values()):
        raise StochasticCollectorError("reference 序列 sample_count 与 DUT 不一致")

    # B13 只要求至少一个 interior p 的 exact 前提；多 interior 时逐项 exact 后合并成
    # 一份收据，mismatch_count/sha 均覆盖按 plan 顺序拼接的完整前提集合。
    dut_pre = b"".join(dut[role] for role in pre_roles)
    ref_pre = b"".join(reference[role] for role in pre_roles)
    mismatch = sum(a != b for a, b in zip(dut_pre, ref_pre))
    first_pre = next(row for row in plan["cases"] if row["role"] == pre_roles[0])
    receipt = {
        "schema": SC.PRECONDITION_SCHEMA,
        "schema_version": SC.SCHEMA_VERSION,
        "contract_sha256": SC.canonical_sha256(contract),
        "plan_sha256": SC.canonical_sha256(plan),
        "status": "passed" if mismatch == 0 else "failed",
        "evidence_grade": "precondition",
        "usable_for_verdict": False,
        "execution": {
            "dut_device": json.loads(json.dumps(device)),
            "reference_device": json.loads(json.dumps(device)),
            "reference_method": contract["oracle_precondition"]["method_kind"],
            "reference_callable": contract["oracle_precondition"]["callable"],
        },
        "case": {
            "probability": first_pre["probability"],
            "seed": contract["rng"]["seed"],
            "offset": contract["rng"]["offset"],
            "sample_count": len(dut_pre),
        },
        "comparison": {
            "predicate": SC.PREDICATE_EXACT,
            "mismatch_count": mismatch,
            "sample_count": len(dut_pre),
            "dut_output_sha256": hashlib.sha256(dut_pre).hexdigest(),
            "reference_output_sha256": hashlib.sha256(ref_pre).hexdigest(),
        },
    }
    normalized_receipt = SC.validate_precondition_receipt(contract, receipt)
    if normalized_receipt["status"] != "passed":
        return normalized_receipt, None

    boundaries = []
    groups = []
    for index, probability in enumerate(contract["probabilities"]["boundaries"]):
        seq = dut[f"boundary_{index}"]
        boundaries.append({
            "probability": probability, "sample_count": len(seq),
            "ones_count": sum(seq),
        })
    rng = contract["rng"]
    for index, probability in enumerate(contract["probabilities"]["interior"]):
        prefix = f"interior_{index}"
        repeats = [dut[f"{prefix}_repeat_{i}"] for i in range(rng["repeat_count"])]
        base = repeats[0]
        independence = []
        for role in ("seed", "offset"):
            other = dut[f"{prefix}_alternate_{role}"]
            counts = _counts(base, other)
            independence.append({
                "changed_role": role,
                "seed": rng["alternate_seed"] if role == "seed" else rng["seed"],
                "offset": rng["alternate_offset"] if role == "offset" else rng["offset"],
                "sample_count": len(base),
                "ones_count": counts["other_ones_count"],
                "joint_ones_count": counts["joint_ones_count"],
                "hamming_count": counts["hamming_count"],
            })
        groups.append({
            "probability": probability,
            "seed": rng["seed"], "offset": rng["offset"],
            "sample_count": len(base), "ones_count": sum(base),
            "repeat_mismatch_counts": [
                sum(a != b for a, b in zip(base, other)) for other in repeats[1:]],
            "independence": independence,
        })
    formal = {
        "schema": SC.FORMAL_SCHEMA,
        "schema_version": SC.SCHEMA_VERSION,
        "contract_sha256": SC.canonical_sha256(contract),
        "plan_sha256": SC.canonical_sha256(plan),
        "precondition_receipt_sha256": SC.canonical_sha256(normalized_receipt),
        "device": json.loads(json.dumps(device)),
        "boundaries": boundaries,
        "coverage_boundaries": [],
        "groups": groups,
    }
    # 生产者先走消费方同一确定性评价，保证合法但统计失败的 evidence 仍可落盘。
    SC.evaluate_formal_evidence(contract, normalized_receipt, formal)
    return normalized_receipt, formal
