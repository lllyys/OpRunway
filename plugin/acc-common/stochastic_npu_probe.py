#!/usr/bin/env python3
"""同机 NPU 随机参考方法的 development 自检。

本入口只验证 ``stochastic_contract`` 描述的 reference callable 能否在真实 NPU 上
兑现 seed/offset、概率边界、重复序列与独立性统计。它不执行 DUT，产物固定为
``evidence_grade=development``、``usable_for_verdict=false``、
``acceptance_verdict=null``，不得代替正式 RNG 前提收据或精度证据。

调用方法按契约中的 ``oracle_precondition.callable`` 数据解析为 Tensor method；没有
任何算子名分支。当前 capability 的方法签名须兼容
``tensor.<method>(probability, generator=generator)``，不兼容即结构化 BLOCKED。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile

import stochastic_contract as SC


PROBE_SCHEMA = "oprunway.stochastic_npu_development_probe"
_CALLABLE = re.compile(r"(?:torch|torch_npu)\.Tensor\.([A-Za-z_][A-Za-z0-9_]*)")


def _atomic_dump(path, value):
    root = os.path.dirname(os.path.abspath(path))
    os.makedirs(root, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _blocked(contract, reason, environment=None):
    return {
        "schema": PROBE_SCHEMA,
        "schema_version": 1,
        "status": "blocked",
        "evidence_grade": "development",
        "usable_for_verdict": False,
        "acceptance_verdict": None,
        "contract_sha256": (SC.canonical_sha256(contract)
                            if isinstance(contract, dict) else None),
        "environment": environment,
        "reason": reason,
        "note": ("仅为同机 NPU reference self-check；未执行 DUT，"
                 "不得充当 RNG 前提收据或正式精度证据"),
    }


def _bytes(tensor):
    # capability 已限定二元输出；逐元素转 byte 避免引入 numpy 依赖。
    values = tensor.to("cpu").byte()
    flat = values.reshape(-1).tolist()
    if any(value not in (0, 1) for value in flat):
        raise SC.StochasticContractError("reference callable 输出不在二元值域 {0,1}")
    return bytes(flat)


def _counts(left, right=None):
    ones = sum(left)
    result = {"sample_count": len(left), "ones_count": ones,
              "sha256": hashlib.sha256(left).hexdigest()}
    if right is not None:
        if len(left) != len(right):
            raise SC.StochasticContractError("随机序列长度不一致")
        result.update({
            "other_ones_count": sum(right),
            "other_sha256": hashlib.sha256(right).hexdigest(),
            "joint_ones_count": sum(a == 1 and b == 1 for a, b in zip(left, right)),
            "hamming_count": sum(a != b for a, b in zip(left, right)),
        })
    return result


def run(contract_value, device_index=0):
    contract = SC.normalize_contract(contract_value)
    callable_name = contract["oracle_precondition"]["callable"]
    match = _CALLABLE.fullmatch(callable_name)
    if match is None:
        return _blocked(
            contract,
            "oracle_precondition.callable 须为 torch.Tensor.<method> 或 "
            "torch_npu.Tensor.<method> 的受控形态")
    method_name = match.group(1)
    environment = None
    try:
        import torch
        import torch_npu

        if isinstance(device_index, bool) or not isinstance(device_index, int) or device_index < 0:
            raise SC.StochasticContractError("device_index 须为非负整数")
        device = f"npu:{device_index}"
        generator = torch.Generator(device=device)
        for name in ("manual_seed", "set_offset", "get_offset"):
            if not hasattr(generator, name):
                raise SC.StochasticContractError(
                    f"当前 NPU Generator 缺 {name}，无法证明显式 seed/offset")
        probe_tensor = torch.empty(1, dtype=torch.float32, device=device)
        if not callable(getattr(probe_tensor, method_name, None)):
            raise SC.StochasticContractError(
                f"当前 NPU Tensor 不提供契约 reference method {method_name!r}")
        try:
            soc = str(torch.npu.get_device_name(device_index))
        except Exception:  # noqa: BLE001 —— development 诊断；未知仍进入 fingerprint
            soc = "unknown"
        identity = {
            "torch": str(torch.__version__),
            "torch_npu": str(torch_npu.__version__),
            "device": device,
            "soc": soc,
        }
        environment = {
            **identity,
            "runtime_fingerprint": SC.canonical_sha256(identity),
            "generator_has_explicit_offset": True,
        }

        sample_count = contract["statistics"]["min_samples"]
        rng = contract["rng"]

        def sample(probability, seed, offset):
            g = torch.Generator(device=device)
            g.manual_seed(seed)
            g.set_offset(offset)
            if g.get_offset() != offset:
                raise SC.StochasticContractError(
                    f"NPU Generator set_offset({offset}) 后回读 {g.get_offset()}，不一致")
            tensor = torch.empty(sample_count, dtype=torch.float32, device=device)
            getattr(tensor, method_name)(probability, generator=g)
            torch.npu.synchronize()
            return _bytes(tensor)

        boundary_rows = []
        raw_observations = {"boundaries": [], "groups": []}
        for probability in contract["probabilities"]["boundaries"]:
            sequence = sample(probability, rng["seed"], rng["offset"])
            counts = _counts(sequence)
            boundary_rows.append({
                "probability": probability,
                "sample_count": counts["sample_count"],
                "ones_count": counts["ones_count"],
            })
            raw_observations["boundaries"].append({"probability": probability, **counts})

        group_rows = []
        for probability in contract["probabilities"]["interior"]:
            repeats = [sample(probability, rng["seed"], rng["offset"])
                       for _ in range(rng["repeat_count"])]
            base = repeats[0]
            base_counts = _counts(base)
            repeat_mismatches = [sum(a != b for a, b in zip(base, other))
                                 for other in repeats[1:]]
            alt_sequences = {
                "seed": sample(probability, rng["alternate_seed"], rng["offset"]),
                "offset": sample(probability, rng["seed"], rng["alternate_offset"]),
            }
            independence = []
            raw_independence = []
            for role in ("seed", "offset"):
                compared = _counts(base, alt_sequences[role])
                seed = rng["alternate_seed"] if role == "seed" else rng["seed"]
                offset = rng["alternate_offset"] if role == "offset" else rng["offset"]
                independence.append({
                    "changed_role": role, "seed": seed, "offset": offset,
                    "sample_count": sample_count,
                    "ones_count": compared["other_ones_count"],
                    "joint_ones_count": compared["joint_ones_count"],
                    "hamming_count": compared["hamming_count"],
                })
                raw_independence.append({"changed_role": role, **compared})
            group_rows.append({
                "probability": probability,
                "seed": rng["seed"], "offset": rng["offset"],
                "sample_count": sample_count, "ones_count": base_counts["ones_count"],
                "repeat_mismatch_counts": repeat_mismatches,
                "independence": independence,
            })
            raw_observations["groups"].append({
                "probability": probability,
                "base": base_counts,
                "repeat_sha256": [hashlib.sha256(item).hexdigest() for item in repeats[1:]],
                "repeat_mismatch_counts": repeat_mismatches,
                "independence": raw_independence,
            })

        # 只为复用确定性谓词造一份**内存内 self-check** 前提；它不落到输出、也不声称
        # lane A 是 DUT。输出第一层固定 development/usable=false/null verdict。
        plan = SC.build_case_plan(contract)
        first_group = raw_observations["groups"][0]
        same_sha = first_group["base"]["sha256"]
        mismatch = first_group["repeat_mismatch_counts"][0]
        device_identity = {
            "type": "npu", "index": device_index, "soc": soc,
            "runtime_fingerprint": environment["runtime_fingerprint"],
        }
        self_receipt = {
            "schema": SC.PRECONDITION_SCHEMA, "schema_version": 1,
            "contract_sha256": SC.canonical_sha256(contract),
            "plan_sha256": SC.canonical_sha256(plan),
            "status": "passed" if mismatch == 0 else "failed",
            "evidence_grade": "precondition", "usable_for_verdict": False,
            "execution": {
                "dut_device": device_identity, "reference_device": device_identity,
                "reference_method": contract["oracle_precondition"]["method_kind"],
                "reference_callable": callable_name,
            },
            "case": {
                "probability": contract["probabilities"]["interior"][0],
                "seed": rng["seed"], "offset": rng["offset"],
                "sample_count": sample_count,
            },
            "comparison": {
                "predicate": "exact", "mismatch_count": mismatch,
                "sample_count": sample_count, "dut_output_sha256": same_sha,
                "reference_output_sha256": (
                    first_group["repeat_sha256"][0] if mismatch else same_sha),
            },
        }
        normalized_self_receipt = SC.validate_precondition_receipt(contract, self_receipt)
        formal = {
            "schema": SC.FORMAL_SCHEMA, "schema_version": 1,
            "contract_sha256": SC.canonical_sha256(contract),
            "plan_sha256": SC.canonical_sha256(plan),
            "precondition_receipt_sha256": SC.canonical_sha256(normalized_self_receipt),
            "device": device_identity, "boundaries": boundary_rows, "groups": group_rows,
        }
        evaluation = SC.evaluate_formal_evidence(contract, self_receipt, formal)
        return {
            "schema": PROBE_SCHEMA,
            "schema_version": 1,
            "status": evaluation["status"],
            "evidence_grade": "development",
            "usable_for_verdict": False,
            "acceptance_verdict": None,
            "contract_sha256": SC.canonical_sha256(contract),
            "plan_sha256": SC.canonical_sha256(plan),
            "environment": environment,
            "reference_callable": callable_name,
            "observations": raw_observations,
            "predicate_evaluation": evaluation,
            "note": ("真实 NPU reference self-check；未执行 DUT。只证明本机 Generator/参考方法"
                     "能兑现该随机契约，不得充当 RNG 前提收据或正式精度证据"),
        }
    except Exception as ex:  # noqa: BLE001 —— probe 必须结构化失败，不能只留 traceback
        return _blocked(contract, f"{type(ex).__name__}: {ex}", environment=environment)


def main(argv=None):
    parser = argparse.ArgumentParser(description="真实 NPU 随机 reference development probe")
    parser.add_argument("contract_json", help="含 spec.stochastic 或直接 stochastic object 的 JSON")
    parser.add_argument("--out", required=True, help="development probe JSON 输出路径")
    parser.add_argument("--device", type=int, default=0, help="NPU device index，缺省 0")
    args = parser.parse_args(argv)
    try:
        with open(args.contract_json, encoding="utf-8") as src:
            loaded = json.load(src)
        contract = loaded.get("stochastic") if isinstance(loaded, dict) and "stochastic" in loaded else loaded
        result = run(contract, device_index=args.device)
    except Exception as ex:  # noqa: BLE001
        result = _blocked(None, f"{type(ex).__name__}: {ex}")
    _atomic_dump(args.out, result)
    print(json.dumps({
        "status": result["status"], "evidence_grade": result["evidence_grade"],
        "usable_for_verdict": result["usable_for_verdict"], "out": os.path.abspath(args.out),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == SC.EVAL_SATISFIED else 3


if __name__ == "__main__":
    raise SystemExit(main())
