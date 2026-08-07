#!/usr/bin/env python3
"""通用随机二元采样接口的确定性契约与统计谓词。

本模块只使用 Python 标准库，不导入 torch/numpy，也不调用具体算子。启用条件完全由
``spec.stochastic.kind == "binary_probability_sampler"`` 驱动；参数名、概率、seed、
offset 与参考方法都是数据，代码不按算子身份分派。

两类证据严格分离：

* RNG 前提收据只证明同设备、同 probability/seed/offset 下 DUT 与任务书指定参考的
  序列逐位一致。它固定 ``evidence_grade=precondition``、
  ``usable_for_verdict=false``，不一致只会阻断正式精度并进入归因；
* 正式随机证据在前提通过后，分别检查概率边界、同配置重复序列、变 seed/offset 的
  独立性见证和二元样本均值。统计门用 Hoeffding 界并对全部统计谓词作 Bonferroni
  分配，因此 ``statistics.confidence`` 是整份正式证据的整体置信下界，不是单项值。

这里返回的是随机谓词的 ``satisfied/failed/blocked``，不是算子总体 acceptance
裁决；总体裁决仍只由 validator + 三级验收门产生。
"""

from __future__ import annotations

import hashlib
import json
import math
import re


class StochasticContractError(ValueError):
    pass


KIND_BINARY_PROBABILITY_SAMPLER = "binary_probability_sampler"
REFERENCE_SAME_DEVICE_NPU = "same_device_npu"
PREDICATE_EXACT = "exact"
STAT_HOEFFDING = "hoeffding_two_sided"
INDEPENDENCE_JOINT = "joint_and_hamming_hoeffding"

PRECONDITION_SCHEMA = "oprunway.stochastic_precondition"
FORMAL_SCHEMA = "oprunway.stochastic_formal_evidence"
PLAN_SCHEMA = "oprunway.stochastic_case_plan"
SCHEMA_VERSION = 1

EVAL_SATISFIED = "satisfied"
EVAL_FAILED = "failed"
EVAL_BLOCKED = "blocked"

_HEX64 = re.compile(r"[0-9a-f]{64}")
_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1


def canonical_sha256(value):
    """内容寻址摘要；拒绝 NaN/Inf，供计划、前提与正式证据互绑。"""
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as ex:
        raise StochasticContractError(f"随机契约不可 canonical JSON：{ex}") from ex
    return hashlib.sha256(raw).hexdigest()


def _object(value, where, allowed, required):
    if not isinstance(value, dict):
        raise StochasticContractError(f"{where} 须为 object")
    extra = sorted(set(value) - set(allowed))
    missing = sorted(set(required) - set(value))
    if extra:
        raise StochasticContractError(f"{where} 含未知字段 {extra}")
    if missing:
        raise StochasticContractError(f"{where} 缺字段 {missing}")
    return value


def _token(value, where):
    if not isinstance(value, str) or not value.strip():
        raise StochasticContractError(f"{where} 须为非空字符串")
    return value


def _int(value, where, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise StochasticContractError(f"{where} 须为整数，得 {value!r}")
    if minimum is not None and value < minimum:
        raise StochasticContractError(f"{where} 须 ≥ {minimum}，得 {value}")
    if maximum is not None and value > maximum:
        raise StochasticContractError(f"{where} 须 ≤ {maximum}，得 {value}")
    return value


def _finite(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StochasticContractError(f"{where} 须为有限数，得 {value!r}")
    value = float(value)
    if not math.isfinite(value):
        raise StochasticContractError(f"{where} 须为有限数，得 {value!r}")
    return value


def _probability(value, where, interior=False):
    value = _finite(value, where)
    if interior:
        if not 0.0 < value < 1.0:
            raise StochasticContractError(f"{where} 须严格位于 (0,1)，得 {value}")
    elif not 0.0 <= value <= 1.0:
        raise StochasticContractError(f"{where} 须位于 [0,1]，得 {value}")
    return value


def _hex64(value, where):
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise StochasticContractError(f"{where} 须为 64 位小写 sha256")
    return value


def _normalize_device(value, where):
    value = _object(
        value, where,
        {"type", "index", "soc", "runtime_fingerprint"},
        {"type", "index", "soc", "runtime_fingerprint"})
    if value["type"] != "npu":
        raise StochasticContractError(f"{where}.type 必须为 'npu'")
    return {
        "type": "npu",
        "index": _int(value["index"], f"{where}.index", minimum=0),
        "soc": _token(value["soc"], f"{where}.soc"),
        "runtime_fingerprint": _hex64(
            value["runtime_fingerprint"], f"{where}.runtime_fingerprint"),
    }


def _statistical_test_count(contract):
    # 每个 interior probability：主序列均值 1 项；变 seed / 变 offset 各检查
    # alternate 均值、joint-one 比例、hamming 比例 3 项，共 7 项。
    return len(contract["probabilities"]["interior"]) * 7


def _epsilon(confidence, sample_count, test_count):
    """Hoeffding two-sided + Bonferroni family-wise epsilon。"""
    alpha = 1.0 - confidence
    return math.sqrt(math.log((2.0 * test_count) / alpha) / (2.0 * sample_count))


def normalize_contract(value, params=None):
    """规范化 ``spec.stochastic``，并可选核对角色绑定指向真实参数。

    ``params`` 只核名字存在与唯一，不规定它在 ACLNN ABI 中叫 attr 还是 host scalar；
    具体 binding 形态归接口 IR/N6，随机语义层只关心三个逻辑角色。
    """
    value = _object(
        value, "spec.stochastic",
        {"kind", "bindings", "probabilities", "rng", "oracle_precondition", "statistics"},
        {"kind", "bindings", "probabilities", "rng", "oracle_precondition", "statistics"})
    if value["kind"] != KIND_BINARY_PROBABILITY_SAMPLER:
        raise StochasticContractError(
            f"spec.stochastic.kind={value['kind']!r} 非受支持 capability "
            f"{KIND_BINARY_PROBABILITY_SAMPLER!r}")

    bindings = _object(
        value["bindings"], "spec.stochastic.bindings",
        {"probability", "seed", "offset"}, {"probability", "seed", "offset"})
    bindings = {role: _token(bindings[role], f"spec.stochastic.bindings.{role}")
                for role in ("probability", "seed", "offset")}
    if len(set(bindings.values())) != 3:
        raise StochasticContractError("随机 probability/seed/offset 三个逻辑角色必须绑定不同参数")
    if params is not None:
        if not isinstance(params, list):
            raise StochasticContractError("spec.params 须为列表，才能核随机角色绑定")
        names = [p.get("name") for p in params if isinstance(p, dict)]
        for role, name in bindings.items():
            if names.count(name) != 1:
                raise StochasticContractError(
                    f"随机角色 {role} 绑定 {name!r}，但 spec.params 中出现 {names.count(name)} 次")

    probabilities = _object(
        value["probabilities"], "spec.stochastic.probabilities",
        {"boundaries", "interior"}, {"boundaries", "interior"})
    boundaries = probabilities["boundaries"]
    if not isinstance(boundaries, list) or len(boundaries) != 2:
        raise StochasticContractError("随机概率边界须逐字声明为 [0.0, 1.0]")
    boundaries = [_probability(v, f"probabilities.boundaries[{i}]")
                  for i, v in enumerate(boundaries)]
    if boundaries != [0.0, 1.0]:
        raise StochasticContractError("随机概率边界须逐字声明为升序 [0.0, 1.0]")
    interior = probabilities["interior"]
    if not isinstance(interior, list) or not interior:
        raise StochasticContractError("随机概率须至少声明一个严格位于 (0,1) 的 interior 值")
    interior = [_probability(v, f"probabilities.interior[{i}]", interior=True)
                for i, v in enumerate(interior)]
    if len(set(interior)) != len(interior):
        raise StochasticContractError("probabilities.interior 不得重复")

    rng = _object(
        value["rng"], "spec.stochastic.rng",
        {"seed", "alternate_seed", "offset", "alternate_offset",
         "offset_alignment", "repeat_count"},
        {"seed", "alternate_seed", "offset", "alternate_offset",
         "offset_alignment", "repeat_count"})
    seed = _int(rng["seed"], "rng.seed", _INT64_MIN, _INT64_MAX)
    alt_seed = _int(rng["alternate_seed"], "rng.alternate_seed", _INT64_MIN, _INT64_MAX)
    offset = _int(rng["offset"], "rng.offset", minimum=0, maximum=_INT64_MAX)
    alt_offset = _int(
        rng["alternate_offset"], "rng.alternate_offset", minimum=0, maximum=_INT64_MAX)
    alignment = _int(rng["offset_alignment"], "rng.offset_alignment", minimum=1)
    repeat_count = _int(rng["repeat_count"], "rng.repeat_count", minimum=2)
    if seed == alt_seed:
        raise StochasticContractError("rng.alternate_seed 必须与 rng.seed 不同")
    if offset == alt_offset:
        raise StochasticContractError("rng.alternate_offset 必须与 rng.offset 不同")
    if offset % alignment or alt_offset % alignment:
        raise StochasticContractError(
            f"rng.offset/alternate_offset 必须满足 offset % {alignment} == 0")

    oracle = _object(
        value["oracle_precondition"], "spec.stochastic.oracle_precondition",
        {"method_kind", "callable", "predicate"},
        {"method_kind", "callable", "predicate"})
    if oracle["method_kind"] != REFERENCE_SAME_DEVICE_NPU:
        raise StochasticContractError(
            f"随机 oracle 前提 method_kind 必须为 {REFERENCE_SAME_DEVICE_NPU!r}")
    if oracle["predicate"] != PREDICATE_EXACT:
        raise StochasticContractError("随机 oracle 前提只接受 exact 逐位关系")
    oracle = {
        "method_kind": REFERENCE_SAME_DEVICE_NPU,
        "callable": _token(oracle["callable"], "oracle_precondition.callable"),
        "predicate": PREDICATE_EXACT,
    }

    stats = _object(
        value["statistics"], "spec.stochastic.statistics",
        {"predicate", "independence_predicate", "confidence", "min_samples",
         "witness_profile_id"},
        {"predicate", "independence_predicate", "confidence", "min_samples",
         "witness_profile_id"})
    if stats["predicate"] != STAT_HOEFFDING:
        raise StochasticContractError(
            f"statistics.predicate 必须为 {STAT_HOEFFDING!r}")
    if stats["independence_predicate"] != INDEPENDENCE_JOINT:
        raise StochasticContractError(
            f"statistics.independence_predicate 必须为 {INDEPENDENCE_JOINT!r}")
    confidence = _finite(stats["confidence"], "statistics.confidence")
    if not 0.5 < confidence < 1.0:
        raise StochasticContractError("statistics.confidence 须严格位于 (0.5,1)")
    min_samples = _int(stats["min_samples"], "statistics.min_samples", minimum=1)
    witness_profile_id = _token(
        stats["witness_profile_id"], "statistics.witness_profile_id")

    normalized = {
        "kind": KIND_BINARY_PROBABILITY_SAMPLER,
        "bindings": bindings,
        "probabilities": {"boundaries": boundaries, "interior": interior},
        "rng": {
            "seed": seed, "alternate_seed": alt_seed,
            "offset": offset, "alternate_offset": alt_offset,
            "offset_alignment": alignment, "repeat_count": repeat_count,
        },
        "oracle_precondition": oracle,
        "statistics": {
            "predicate": STAT_HOEFFDING,
            "independence_predicate": INDEPENDENCE_JOINT,
            "confidence": confidence,
            "min_samples": min_samples,
            "witness_profile_id": witness_profile_id,
        },
    }
    tests = _statistical_test_count(normalized)
    eps = _epsilon(confidence, min_samples, tests)
    weak = []
    for probability in interior:
        expected_hamming = 2.0 * probability * (1.0 - probability)
        if expected_hamming <= eps:
            weak.append(probability)
    if weak:
        raise StochasticContractError(
            "statistics.min_samples 在整体 confidence 下不足以形成正的独立性 hamming 下界："
            f"probabilities={weak}, epsilon={eps:.12g}；提高 min_samples 或调整 interior 见证")
    return normalized


def from_spec(spec):
    if not isinstance(spec, dict):
        raise StochasticContractError("spec 须为 object")
    if "stochastic" not in spec:
        return None
    return normalize_contract(spec["stochastic"], params=spec.get("params"))


def _bound_values(contract, probability, seed, offset):
    names = contract["bindings"]
    return {
        names["probability"]: probability,
        names["seed"]: seed,
        names["offset"]: offset,
    }


def build_case_plan(value):
    """生成与算子身份无关的稳定执行角色计划；相同契约逐字相同。"""
    contract = normalize_contract(value)
    rng = contract["rng"]
    cases = []
    for index, probability in enumerate(contract["probabilities"]["boundaries"]):
        cases.append({
            "role": f"boundary_{index}", "purpose": "boundary_exact",
            "probability": probability,
            "values": _bound_values(
                contract, probability, rng["seed"], rng["offset"]),
        })
    for index, probability in enumerate(contract["probabilities"]["interior"]):
        group = f"interior_{index}"
        base = _bound_values(contract, probability, rng["seed"], rng["offset"])
        cases.append({
            "role": f"{group}_oracle_precondition",
            "purpose": "rng_consumption_precondition", "probability": probability,
            "values": base, "reference": dict(contract["oracle_precondition"]),
            "usable_for_verdict": False,
        })
        for repeat in range(rng["repeat_count"]):
            cases.append({
                "role": f"{group}_repeat_{repeat}",
                "purpose": "same_seed_offset_reproducibility", "probability": probability,
                "repeat_index": repeat, "values": dict(base),
            })
        cases.append({
            "role": f"{group}_alternate_seed", "purpose": "independence_seed",
            "probability": probability,
            "values": _bound_values(
                contract, probability, rng["alternate_seed"], rng["offset"]),
        })
        cases.append({
            "role": f"{group}_alternate_offset", "purpose": "independence_offset",
            "probability": probability,
            "values": _bound_values(
                contract, probability, rng["seed"], rng["alternate_offset"]),
        })
    body = {
        "schema": PLAN_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "contract_sha256": canonical_sha256(contract),
        "witness_profile_id": contract["statistics"]["witness_profile_id"],
        "cases": cases,
    }
    return body


def _check_header(value, schema, contract, plan, where):
    if value.get("schema") != schema or value.get("schema_version") != SCHEMA_VERSION:
        raise StochasticContractError(
            f"{where} schema/version 必须为 {schema!r}/v{SCHEMA_VERSION}")
    if value.get("contract_sha256") != canonical_sha256(contract):
        raise StochasticContractError(f"{where}.contract_sha256 与规范化随机契约不一致")
    if value.get("plan_sha256") != canonical_sha256(plan):
        raise StochasticContractError(f"{where}.plan_sha256 与随机 case plan 不一致")


def validate_precondition_receipt(value, receipt):
    """校前提收据结构与自洽；合法的 mismatch 收据仍可返回，供 root-cause。"""
    contract = normalize_contract(value)
    plan = build_case_plan(contract)
    receipt = _object(
        receipt, "stochastic precondition receipt",
        {"schema", "schema_version", "contract_sha256", "plan_sha256", "status",
         "evidence_grade", "usable_for_verdict", "execution", "case", "comparison"},
        {"schema", "schema_version", "contract_sha256", "plan_sha256", "status",
         "evidence_grade", "usable_for_verdict", "execution", "case", "comparison"})
    _check_header(receipt, PRECONDITION_SCHEMA, contract, plan, "precondition receipt")
    if receipt["evidence_grade"] != "precondition" or receipt["usable_for_verdict"] is not False:
        raise StochasticContractError(
            "RNG 前提收据必须 evidence_grade='precondition' 且 usable_for_verdict=false")
    execution = _object(
        receipt["execution"], "precondition.execution",
        {"dut_device", "reference_device", "reference_method", "reference_callable"},
        {"dut_device", "reference_device", "reference_method", "reference_callable"})
    dut_device = _normalize_device(execution["dut_device"], "precondition.execution.dut_device")
    ref_device = _normalize_device(
        execution["reference_device"], "precondition.execution.reference_device")
    if dut_device != ref_device:
        raise StochasticContractError("RNG 前提 DUT 与参考必须在逐字段相同的 NPU device identity 上执行")
    oracle = contract["oracle_precondition"]
    if (execution["reference_method"] != oracle["method_kind"]
            or execution["reference_callable"] != oracle["callable"]):
        raise StochasticContractError("RNG 前提参考 method_kind/callable 与随机契约不一致")

    case = _object(
        receipt["case"], "precondition.case",
        {"probability", "seed", "offset", "sample_count"},
        {"probability", "seed", "offset", "sample_count"})
    probability = _probability(case["probability"], "precondition.case.probability", interior=True)
    if probability not in contract["probabilities"]["interior"]:
        raise StochasticContractError("RNG 前提 probability 不在契约 interior 见证集合")
    rng = contract["rng"]
    if case["seed"] != rng["seed"] or case["offset"] != rng["offset"]:
        raise StochasticContractError("RNG 前提 seed/offset 与契约基准值不一致")
    sample_count = _int(
        case["sample_count"], "precondition.case.sample_count",
        minimum=contract["statistics"]["min_samples"])

    comparison = _object(
        receipt["comparison"], "precondition.comparison",
        {"predicate", "mismatch_count", "sample_count",
         "dut_output_sha256", "reference_output_sha256"},
        {"predicate", "mismatch_count", "sample_count",
         "dut_output_sha256", "reference_output_sha256"})
    if comparison["predicate"] != PREDICATE_EXACT:
        raise StochasticContractError("RNG 前提 comparison.predicate 必须为 exact")
    mismatch = _int(
        comparison["mismatch_count"], "precondition.comparison.mismatch_count", minimum=0)
    if comparison["sample_count"] != sample_count:
        raise StochasticContractError("RNG 前提 case/comparison sample_count 不一致")
    if mismatch > sample_count:
        raise StochasticContractError("RNG 前提 mismatch_count 不得大于 sample_count")
    dut_sha = _hex64(comparison["dut_output_sha256"], "comparison.dut_output_sha256")
    ref_sha = _hex64(
        comparison["reference_output_sha256"], "comparison.reference_output_sha256")
    matched = mismatch == 0
    expected_status = "passed" if matched else "failed"
    if receipt["status"] != expected_status:
        raise StochasticContractError(
            f"RNG 前提 status={receipt['status']!r} 与 exact mismatch={mismatch} 不自洽")
    if matched and dut_sha != ref_sha:
        raise StochasticContractError("RNG 前提逐位零 mismatch 时 DUT/reference 输出 sha256 必须相同")
    if not matched and dut_sha == ref_sha:
        raise StochasticContractError("RNG 前提存在 mismatch 时 DUT/reference 输出 sha256 不得相同")
    return {
        "schema": PRECONDITION_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "contract_sha256": receipt["contract_sha256"],
        "plan_sha256": receipt["plan_sha256"],
        "status": expected_status,
        "evidence_grade": "precondition",
        "usable_for_verdict": False,
        "execution": {
            "dut_device": dut_device, "reference_device": ref_device,
            "reference_method": oracle["method_kind"],
            "reference_callable": oracle["callable"],
        },
        "case": {
            "probability": probability, "seed": rng["seed"], "offset": rng["offset"],
            "sample_count": sample_count,
        },
        "comparison": {
            "predicate": PREDICATE_EXACT, "mismatch_count": mismatch,
            "sample_count": sample_count,
            "dut_output_sha256": dut_sha, "reference_output_sha256": ref_sha,
        },
    }


def precondition_gate(value, receipt):
    """结构缺失/漂移或 exact 不一致都 BLOCKED；永不把前提包装成正式 pass。"""
    try:
        normalized = validate_precondition_receipt(value, receipt)
    except StochasticContractError as ex:
        return {
            "status": EVAL_BLOCKED, "ready_for_formal_precision": False,
            "usable_for_verdict": False, "reason": str(ex),
        }
    if normalized["status"] != "passed":
        return {
            "status": EVAL_BLOCKED, "ready_for_formal_precision": False,
            "usable_for_verdict": False,
            "reason": ("同机同配置 RNG exact 前提不一致；只进入 root-cause，"
                       "不得直接判 DUT 精度失败"),
        }
    return {
        "status": "ready", "ready_for_formal_precision": True,
        "usable_for_verdict": False,
        "reason": "RNG 消耗一致前提已满足；本收据自身仍不可用于裁决",
    }


def _count(value, where, n):
    value = _int(value, where, minimum=0)
    if value > n:
        raise StochasticContractError(f"{where}={value} 不得大于 sample_count={n}")
    return value


def _check_row(checks, name, observed, expected, epsilon, exact=False):
    delta = abs(observed - expected)
    satisfied = (delta == 0.0) if exact else (delta <= epsilon)
    checks.append({
        "name": name, "observed": observed, "expected": expected,
        "epsilon": 0.0 if exact else epsilon, "satisfied": satisfied,
    })


def evaluate_formal_evidence(value, receipt, evidence):
    """评价正式随机证据；前提不 ready 时返回 BLOCKED，不消费统计数字。"""
    contract = normalize_contract(value)
    gate = precondition_gate(contract, receipt)
    if not gate["ready_for_formal_precision"]:
        return {
            "status": EVAL_BLOCKED, "satisfied": None,
            "confidence": contract["statistics"]["confidence"],
            "checks": [], "reason": gate["reason"],
        }
    normalized_receipt = validate_precondition_receipt(contract, receipt)
    plan = build_case_plan(contract)
    evidence = _object(
        evidence, "stochastic formal evidence",
        {"schema", "schema_version", "contract_sha256", "plan_sha256",
         "precondition_receipt_sha256", "device", "boundaries", "groups"},
        {"schema", "schema_version", "contract_sha256", "plan_sha256",
         "precondition_receipt_sha256", "device", "boundaries", "groups"})
    _check_header(evidence, FORMAL_SCHEMA, contract, plan, "formal evidence")
    if evidence["precondition_receipt_sha256"] != canonical_sha256(normalized_receipt):
        raise StochasticContractError(
            "formal evidence.precondition_receipt_sha256 与已校前提收据不一致")
    device = _normalize_device(evidence["device"], "formal evidence.device")
    if device != normalized_receipt["execution"]["dut_device"]:
        raise StochasticContractError("正式随机证据 device 与 RNG 前提 device identity 不一致")

    boundaries = evidence["boundaries"]
    if not isinstance(boundaries, list):
        raise StochasticContractError("formal evidence.boundaries 须为列表")
    by_probability = {}
    for i, row in enumerate(boundaries):
        row = _object(
            row, f"boundaries[{i}]", {"probability", "sample_count", "ones_count"},
            {"probability", "sample_count", "ones_count"})
        probability = _probability(row["probability"], f"boundaries[{i}].probability")
        if probability in by_probability:
            raise StochasticContractError("formal evidence.boundaries 概率重复")
        n = _int(row["sample_count"], f"boundaries[{i}].sample_count", minimum=1)
        by_probability[probability] = (n, _count(row["ones_count"], f"boundaries[{i}].ones_count", n))
    if set(by_probability) != set(contract["probabilities"]["boundaries"]):
        raise StochasticContractError("formal evidence.boundaries 未逐项覆盖契约概率边界")

    groups = evidence["groups"]
    if not isinstance(groups, list):
        raise StochasticContractError("formal evidence.groups 须为列表")
    group_by_probability = {}
    for i, row in enumerate(groups):
        row = _object(
            row, f"groups[{i}]",
            {"probability", "seed", "offset", "sample_count", "ones_count",
             "repeat_mismatch_counts", "independence"},
            {"probability", "seed", "offset", "sample_count", "ones_count",
             "repeat_mismatch_counts", "independence"})
        probability = _probability(row["probability"], f"groups[{i}].probability", interior=True)
        if probability in group_by_probability:
            raise StochasticContractError("formal evidence.groups probability 重复")
        group_by_probability[probability] = row
    if set(group_by_probability) != set(contract["probabilities"]["interior"]):
        raise StochasticContractError("formal evidence.groups 未逐项覆盖契约 interior probabilities")

    confidence = contract["statistics"]["confidence"]
    test_count = _statistical_test_count(contract)
    checks = []
    for probability in contract["probabilities"]["boundaries"]:
        n, ones = by_probability[probability]
        expected_ones = int(probability * n)
        _check_row(
            checks, f"boundary:{probability}", float(ones), float(expected_ones), 0.0,
            exact=True)

    rng = contract["rng"]
    for probability in contract["probabilities"]["interior"]:
        row = group_by_probability[probability]
        if row["seed"] != rng["seed"] or row["offset"] != rng["offset"]:
            raise StochasticContractError(
                f"interior probability={probability} 的基准 seed/offset 与契约不一致")
        n = _int(
            row["sample_count"], f"group[{probability}].sample_count",
            minimum=contract["statistics"]["min_samples"])
        ones = _count(row["ones_count"], f"group[{probability}].ones_count", n)
        epsilon = _epsilon(confidence, n, test_count)
        _check_row(
            checks, f"mean:{probability}", ones / n, probability, epsilon)

        repeats = row["repeat_mismatch_counts"]
        expected_repeats = rng["repeat_count"] - 1
        if not isinstance(repeats, list) or len(repeats) != expected_repeats:
            raise StochasticContractError(
                f"group[{probability}].repeat_mismatch_counts 须有 {expected_repeats} 项")
        for repeat_index, mismatch in enumerate(repeats, start=1):
            mismatch = _count(
                mismatch, f"group[{probability}].repeat_mismatch_counts[{repeat_index - 1}]", n)
            _check_row(
                checks, f"reproducibility:{probability}:repeat{repeat_index}",
                float(mismatch), 0.0, 0.0, exact=True)

        independence = row["independence"]
        if not isinstance(independence, list):
            raise StochasticContractError(f"group[{probability}].independence 须为列表")
        by_role = {}
        for j, alt in enumerate(independence):
            alt = _object(
                alt, f"group[{probability}].independence[{j}]",
                {"changed_role", "seed", "offset", "sample_count", "ones_count",
                 "joint_ones_count", "hamming_count"},
                {"changed_role", "seed", "offset", "sample_count", "ones_count",
                 "joint_ones_count", "hamming_count"})
            role = alt["changed_role"]
            if role not in ("seed", "offset") or role in by_role:
                raise StochasticContractError(
                    f"group[{probability}].independence.changed_role 须唯一覆盖 seed/offset")
            by_role[role] = alt
        if set(by_role) != {"seed", "offset"}:
            raise StochasticContractError(
                f"group[{probability}].independence 未同时覆盖变 seed 与变 offset")

        for role in ("seed", "offset"):
            alt = by_role[role]
            wanted_seed = rng["alternate_seed"] if role == "seed" else rng["seed"]
            wanted_offset = rng["alternate_offset"] if role == "offset" else rng["offset"]
            if alt["seed"] != wanted_seed or alt["offset"] != wanted_offset:
                raise StochasticContractError(
                    f"group[{probability}] 变 {role} 见证未做到只改变该角色的契约值")
            if alt["sample_count"] != n:
                raise StochasticContractError(
                    f"group[{probability}] 变 {role} 见证 sample_count 与基准不同")
            alt_ones = _count(
                alt["ones_count"], f"group[{probability}].{role}.ones_count", n)
            joint = _count(
                alt["joint_ones_count"], f"group[{probability}].{role}.joint_ones_count", n)
            hamming = _count(
                alt["hamming_count"], f"group[{probability}].{role}.hamming_count", n)
            _check_row(
                checks, f"alternate_mean:{probability}:{role}",
                alt_ones / n, probability, epsilon)
            _check_row(
                checks, f"joint_ones:{probability}:{role}",
                joint / n, probability * probability, epsilon)
            _check_row(
                checks, f"hamming:{probability}:{role}",
                hamming / n, 2.0 * probability * (1.0 - probability), epsilon)

    satisfied = all(row["satisfied"] for row in checks)
    return {
        "status": EVAL_SATISFIED if satisfied else EVAL_FAILED,
        "satisfied": satisfied,
        "confidence": confidence,
        "confidence_method": "Hoeffding two-sided + Bonferroni family-wise allocation",
        "statistical_test_count": test_count,
        "checks": checks,
        "reason": ("全部随机谓词满足" if satisfied else
                   "至少一个概率/复现/独立性谓词未满足；具体见 checks"),
    }
