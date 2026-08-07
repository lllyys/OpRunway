"""多输入物化契约：逐参数 shape / dtype / format / binding 的唯一解析层。

本模块刻意保持 stdlib-only。它不造随机数、不 import torch/numpy，也不认识任何算子名；只把已经
由任务书、IR 与 case planner 给出的逐输入事实规范化，并在真机执行前解析两类关系：

* shape：``fixed`` / ``follows`` / ``broadcast``；
* dtype：``fixed`` / ``follows`` / ``promote``。

为什么单独成层：旧 elementwise 路径用一个 ``shape`` / ``dtype`` 同时代表所有输入，广播时甚至可能
先把输入扩成输出形状再交 runner。这样 caseset 无法证明 ABI 实际收到的是 ``(4, 1)`` 与 ``(1, 5)``，
也表达不了 rank-0 张量或混合 dtype。本契约保留每个输入的物理身份；输出 shape/dtype 是关系求值结果，
不能反向改写输入。

受控结构（JSON 友好）::

    {
      "profile_id": "rank_mismatch",
      "inputs": [
        {"name":"x", "kind":"tensor", "binding":"device_tensor",
         "shape":[2,3,4], "dtype":"float16", "format":"nd"},
        {"name":"y", "kind":"tensor", "binding":"device_tensor",
         "shape":[4], "dtype":"float32", "format":"nd"}
      ],
      "output": {
        "name":"out", "kind":"tensor", "binding":"device_tensor", "format":"nd",
        "shape":{"rule":"broadcast", "operands":["x","y"]},
        "dtype":{"rule":"promote", "rule_id":"torch_tensor_tensor_v1",
                 "operands":["x","y"]}
      }
    }

``kind=scalar,binding=host_scalar`` 与 tensor 共用同一有序参数身份表；但
``torch_tensor_tensor_v1`` 明确只接受 tensor operand，避免把 tensor-scalar 的 wrapped-number 语义
冒充 tensor-tensor promote。host scalar 常见输出规则是 ``follows(self)``。
"""

from __future__ import annotations

import hashlib
import json
import math
import re


SCHEMA_VERSION = "multi_input.v1"

KIND_TENSOR = "tensor"
KIND_SCALAR = "scalar"
BINDING_DEVICE_TENSOR = "device_tensor"
BINDING_HOST_SCALAR = "host_scalar"

TENSOR_FORMAT_ND = "nd"
TENSOR_FORMAT_RANK_DEFAULT = "torch_npu_rank_default"
TENSOR_FORMATS = (TENSOR_FORMAT_ND, TENSOR_FORMAT_RANK_DEFAULT)

PROMOTE_TORCH_TENSOR_TENSOR_V1 = "torch_tensor_tensor_v1"
PROMOTION_RULES = (PROMOTE_TORCH_TENSOR_TENSOR_V1,)

_DTYPES = (
    "bool",
    "uint8",
    "uint32",
    "int8",
    "int16",
    "int32",
    "int64",
    "float16",
    "bfloat16",
    "float32",
    "float64",
    "complex64",
    "complex128",
)
DTYPES = frozenset(_DTYPES)

_PROFILE_ID_RE = re.compile(r"[A-Za-z0-9_.-]+\Z")
_ABSENT = object()

_SIGNED_BITS = {"int8": 8, "int16": 16, "int32": 32, "int64": 64}
_SIGNED_BY_BITS = {v: k for k, v in _SIGNED_BITS.items()}
_FLOATS = frozenset({"float16", "bfloat16", "float32", "float64"})
_COMPLEX = frozenset({"complex64", "complex128"})


class MultiInputContractError(ValueError):
    """多输入契约非法；继承 ValueError 以复用现有 fail-closed 收敛路径。"""


_PROVENANCE_SOURCES = frozenset({"taskbook", "op_def", "header", "example"})
_COVERAGE_KEYS = (
    "broadcasted", "rank_mismatch", "rank0_tensor", "host_scalar",
    "mixed_dtype", "mixed_format",
)


def _unknown_keys(obj, allowed, where):
    unknown = sorted(k for k in obj if k not in allowed and not str(k).startswith("_"))
    if unknown:
        raise MultiInputContractError(f"{where} 含未知字段 {unknown}（fail-closed）")


def _object(value, where):
    if not isinstance(value, dict):
        raise MultiInputContractError(f"{where} 须为 object，实得 {type(value).__name__}")
    return value


def _name(value, where):
    if not isinstance(value, str) or not value.strip():
        raise MultiInputContractError(f"{where} 须为非空字符串，实得 {value!r}")
    return value.strip()


def _dtype(value, where):
    if not isinstance(value, str) or value not in DTYPES:
        raise MultiInputContractError(
            f"{where}={value!r} 非受控 dtype，须属 {list(_DTYPES)}")
    return value


def _provenance(value, where, required_sources=()):
    """关系来源账本；关系只有在多源已对账为 ``resolved`` 时才可进入 planner。"""
    record = _object(value, where)
    _unknown_keys(record, {"state", "sources"}, where)
    if record.get("state") != "resolved":
        raise MultiInputContractError(
            f"{where}.state={record.get('state')!r} 非 'resolved'；来源缺失/冲突不得进入 planner")
    sources = record.get("sources")
    if not isinstance(sources, list) or not sources:
        raise MultiInputContractError(f"{where}.sources 须为非空来源数组")
    normalized, seen = [], set()
    for index, raw in enumerate(sources):
        item_where = f"{where}.sources[{index}]"
        item = _object(raw, item_where)
        _unknown_keys(item, {"kind", "cite"}, item_where)
        kind = item.get("kind")
        cite = item.get("cite")
        if kind not in _PROVENANCE_SOURCES:
            raise MultiInputContractError(
                f"{item_where}.kind={kind!r} 非受控来源，须属 {sorted(_PROVENANCE_SOURCES)}")
        if kind in seen:
            raise MultiInputContractError(f"{where}.sources 来源 {kind!r} 重复")
        if not isinstance(cite, str) or not cite.strip():
            raise MultiInputContractError(f"{item_where}.cite 须为非空定位")
        seen.add(kind)
        normalized.append({"kind": kind, "cite": cite.strip()})
    missing = sorted(set(required_sources) - seen)
    if missing:
        raise MultiInputContractError(
            f"{where} 缺必需来源 {missing}；promote 必须由任务书语义与 op_def 能力双源对账")
    return {"state": "resolved", "sources": normalized}


def _format(value, where):
    if not isinstance(value, str) or value not in TENSOR_FORMATS:
        raise MultiInputContractError(
            f"{where}={value!r} 非受控 tensor format，须属 {list(TENSOR_FORMATS)}")
    return value


def _shape(value, where):
    if not isinstance(value, (list, tuple)):
        raise MultiInputContractError(
            f"{where} 须为非负整数数组；rank-0 张量用 []，实得 {value!r}")
    out = []
    for axis, dim in enumerate(value):
        if isinstance(dim, bool) or not isinstance(dim, int) or dim < 0:
            raise MultiInputContractError(
                f"{where}[{axis}]={dim!r} 非非负整数；rank-0 张量须用空数组 []")
        out.append(int(dim))
    return out


def resolve_broadcast_shape(shapes):
    """按 NumPy/PyTorch trailing-dimension 规则确定性解析广播输出 shape。

    ``[]`` 是真 rank-0 tensor；不会改写成 ``[1]``。维度 0 只可与 0/1 配对，例如
    ``[0, 3]`` 与 ``[1, 3]`` 合法并得到 ``[0, 3]``，与 ``[2, 3]`` 不兼容。
    """
    if not isinstance(shapes, (list, tuple)) or not shapes:
        raise MultiInputContractError("broadcast 至少需要一个 tensor shape")
    normalized = [_shape(s, f"broadcast.shapes[{i}]") for i, s in enumerate(shapes)]
    max_rank = max(len(s) for s in normalized)
    reversed_result = []
    for offset in range(1, max_rank + 1):
        dims = [s[-offset] if len(s) >= offset else 1 for s in normalized]
        non_unit = {d for d in dims if d != 1}
        if len(non_unit) > 1:
            axis = max_rank - offset
            raise MultiInputContractError(
                f"输入 shape {normalized} 在输出轴 {axis} 的维度 {dims} 不可广播")
        reversed_result.append(next(iter(non_unit)) if non_unit else 1)
    return list(reversed(reversed_result))


def _torch_promote_pair(left, right):
    if left == right:
        return left
    if left == "bool":
        return right
    if right == "bool":
        return left

    if left in _COMPLEX or right in _COMPLEX:
        if left == "complex128" or right == "complex128" or left == "float64" or right == "float64":
            return "complex128"
        return "complex64"

    if left in _FLOATS or right in _FLOATS:
        float_types = {d for d in (left, right) if d in _FLOATS}
        if "float64" in float_types:
            return "float64"
        if "float32" in float_types or float_types == {"float16", "bfloat16"}:
            return "float32"
        # tensor-tensor promotion keeps the sole low-precision floating dtype even when the other
        # operand is integral (matching torch.promote_types, not Python-scalar wrapping semantics).
        return next(iter(float_types))

    # Integral tensor promotion. uint8 is the only unsigned dtype in this v1 contract.
    if left == "uint8" or right == "uint8":
        other = right if left == "uint8" else left
        if other == "uint8":
            return "uint8"
        bits = _SIGNED_BITS.get(other)
        if bits is None:
            raise MultiInputContractError(
                f"torch tensor promote 暂不支持整数对 ({left}, {right})")
        return "int16" if bits <= 8 else _SIGNED_BY_BITS[bits]
    lbits, rbits = _SIGNED_BITS.get(left), _SIGNED_BITS.get(right)
    if lbits is None or rbits is None:
        raise MultiInputContractError(
            f"torch tensor promote 暂不支持 dtype 对 ({left}, {right})")
    return _SIGNED_BY_BITS[max(lbits, rbits)]


def promote_dtypes(rule_id, dtypes):
    """按受控规则提升 tensor dtype；未知规则/空 operand/未知 dtype 一律拒绝。"""
    if rule_id not in PROMOTION_RULES:
        raise MultiInputContractError(
            f"dtype promote rule_id={rule_id!r} 非受控值，须属 {list(PROMOTION_RULES)}")
    if not isinstance(dtypes, (list, tuple)) or len(dtypes) < 2:
        raise MultiInputContractError("dtype promote 至少需要两个 operand dtype")
    values = [_dtype(v, f"promote.dtypes[{i}]") for i, v in enumerate(dtypes)]
    result = values[0]
    for value in values[1:]:
        result = _torch_promote_pair(result, value)
    return result


def _scalar_value(value, dtype, where):
    if dtype == "bool":
        if not isinstance(value, bool):
            raise MultiInputContractError(f"{where} 对 bool dtype 须为 bool，实得 {value!r}")
        return value
    if dtype in _SIGNED_BITS or dtype == "uint8":
        if isinstance(value, bool) or not isinstance(value, int):
            raise MultiInputContractError(f"{where} 对 {dtype} 须为整数，实得 {value!r}")
        bits = 8 if dtype == "uint8" else _SIGNED_BITS[dtype]
        lo, hi = ((0, 2 ** bits - 1) if dtype == "uint8"
                  else (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1))
        if not lo <= value <= hi:
            raise MultiInputContractError(
                f"{where}={value!r} 超出 {dtype} 范围 [{lo}, {hi}]")
        return value
    if dtype in _FLOATS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MultiInputContractError(f"{where} 对 {dtype} 须为实数，实得 {value!r}")
        if not math.isfinite(float(value)):
            raise MultiInputContractError(f"{where}={value!r} 非有限 host scalar")
        return value
    raise MultiInputContractError(
        f"{where} 的 host_scalar dtype={dtype!r} 当前无 JSON 标量编码（fail-closed）")


def _normalize_input(raw, index):
    where = f"profile.inputs[{index}]"
    item = _object(raw, where)
    _unknown_keys(item, {"name", "kind", "binding", "shape", "dtype", "format", "value",
                         "value_constraints"}, where)
    for key in ("name", "kind", "binding", "dtype"):
        if key not in item:
            raise MultiInputContractError(f"{where} 缺必填字段 {key!r}")
    name = _name(item["name"], f"{where}.name")
    kind, binding = item["kind"], item["binding"]
    dtype = _dtype(item["dtype"], f"{where}.dtype")
    if kind == KIND_TENSOR:
        if binding != BINDING_DEVICE_TENSOR:
            raise MultiInputContractError(
                f"{where} kind='tensor' 的 binding 必须是 '{BINDING_DEVICE_TENSOR}'，实得 {binding!r}")
        for key in ("shape", "format"):
            if key not in item:
                raise MultiInputContractError(f"{where} tensor 缺必填字段 {key!r}")
        if "value" in item:
            raise MultiInputContractError(f"{where} tensor 不得带 host scalar value")
        constraints = item.get("value_constraints", {})
        if not isinstance(constraints, dict) or set(constraints) - {"nonzero"}:
            raise MultiInputContractError(
                f"{where}.value_constraints 仅支持 object {{'nonzero': true}}")
        if "nonzero" in constraints and constraints["nonzero"] is not True:
            raise MultiInputContractError(f"{where}.value_constraints.nonzero 只能为 true")
        result = {
            "name": name,
            "kind": KIND_TENSOR,
            "binding": BINDING_DEVICE_TENSOR,
            "shape": _shape(item["shape"], f"{where}.shape"),
            "dtype": dtype,
            "format": _format(item["format"], f"{where}.format"),
        }
        if constraints:
            result["value_constraints"] = {"nonzero": True}
        return result
    if kind == KIND_SCALAR:
        if binding != BINDING_HOST_SCALAR:
            raise MultiInputContractError(
                f"{where} kind='scalar' 的 binding 必须是 '{BINDING_HOST_SCALAR}'，实得 {binding!r}")
        if "value" not in item:
            raise MultiInputContractError(f"{where} host scalar 缺必填字段 'value'")
        forbidden = [k for k in ("shape", "format") if k in item]
        if forbidden:
            raise MultiInputContractError(
                f"{where} host scalar 不得声明 tensor 字段 {forbidden}")
        return {
            "name": name,
            "kind": KIND_SCALAR,
            "binding": BINDING_HOST_SCALAR,
            "dtype": dtype,
            "value": _scalar_value(item["value"], dtype, f"{where}.value"),
        }
    raise MultiInputContractError(
        f"{where}.kind={kind!r} 非受控值，须属 {[KIND_TENSOR, KIND_SCALAR]}")


def _operand_names(rule, key, by_name, where, minimum=1):
    values = rule.get(key, _ABSENT)
    if not isinstance(values, list) or len(values) < minimum:
        raise MultiInputContractError(
            f"{where}.{key} 须为至少 {minimum} 项的参数名数组，实得 {values!r}")
    if len(values) != len(set(values)):
        raise MultiInputContractError(f"{where}.{key} 含重复参数名 {values!r}")
    for value in values:
        if not isinstance(value, str) or value not in by_name:
            raise MultiInputContractError(
                f"{where}.{key} 引用了不存在的参数 {value!r}")
    return list(values)


def _single_operand(rule, by_name, where):
    operand = rule.get("operand", _ABSENT)
    if not isinstance(operand, str) or operand not in by_name:
        raise MultiInputContractError(
            f"{where}.operand={operand!r} 未绑定到已有输入参数")
    return operand


def _resolve_shape_rule(raw, by_name, where, *, require_provenance=False):
    rule = _object(raw, where)
    kind = rule.get("rule", _ABSENT)
    provenance = None
    if require_provenance:
        provenance = _provenance(
            rule.get("provenance", _ABSENT), f"{where}.provenance",
            required_sources=("taskbook",))
    if kind == "broadcast":
        _unknown_keys(rule, {"rule", "operands", "provenance"}, where)
        operands = _operand_names(rule, "operands", by_name, where)
        non_tensors = [name for name in operands if by_name[name]["kind"] != KIND_TENSOR]
        if non_tensors:
            raise MultiInputContractError(
                f"{where} broadcast operand 必须是 tensor，实得 host scalar {non_tensors}")
        input_shapes = [by_name[name]["shape"] for name in operands]
        output_shape = resolve_broadcast_shape(input_shapes)
        relation = {
            "rule": "broadcast",
            "operands": operands,
            "input_shapes": [
                {"name": name, "shape": list(by_name[name]["shape"])} for name in operands
            ],
            "output_shape": list(output_shape),
            "broadcasted": any(by_name[name]["shape"] != output_shape for name in operands),
            "rank_mismatch": len({len(by_name[name]["shape"]) for name in operands}) > 1,
            "rank0_tensor": any(not by_name[name]["shape"] for name in operands),
        }
        if provenance is not None:
            relation["provenance"] = provenance
        return output_shape, relation
    if kind == "follows":
        _unknown_keys(rule, {"rule", "operand", "provenance"}, where)
        operand = _single_operand(rule, by_name, where)
        if by_name[operand]["kind"] != KIND_TENSOR:
            raise MultiInputContractError(f"{where} follows operand={operand!r} 不是 tensor")
        output_shape = list(by_name[operand]["shape"])
        relation = {
            "rule": "follows", "operand": operand, "output_shape": output_shape,
            "broadcasted": False, "rank_mismatch": False,
            "rank0_tensor": not output_shape,
        }
        if provenance is not None:
            relation["provenance"] = provenance
        return output_shape, relation
    if kind == "fixed":
        _unknown_keys(rule, {"rule", "value", "provenance"}, where)
        output_shape = _shape(rule.get("value", _ABSENT), f"{where}.value")
        relation = {
            "rule": "fixed", "output_shape": output_shape,
            "broadcasted": False, "rank_mismatch": False,
            "rank0_tensor": not output_shape,
        }
        if provenance is not None:
            relation["provenance"] = provenance
        return output_shape, relation
    raise MultiInputContractError(
        f"{where}.rule={kind!r} 非受控 shape rule，须属 ['broadcast', 'follows', 'fixed']")


def _resolve_dtype_rule(raw, by_name, where, *, require_provenance=False):
    rule = _object(raw, where)
    kind = rule.get("rule", _ABSENT)
    provenance = None
    if require_provenance:
        required = ("taskbook", "op_def") if kind == "promote" else ("taskbook",)
        provenance = _provenance(
            rule.get("provenance", _ABSENT), f"{where}.provenance",
            required_sources=required)
    if kind == "promote":
        _unknown_keys(rule, {"rule", "rule_id", "operands", "provenance"}, where)
        operands = _operand_names(rule, "operands", by_name, where, minimum=2)
        non_tensors = [name for name in operands if by_name[name]["kind"] != KIND_TENSOR]
        if non_tensors:
            raise MultiInputContractError(
                f"{where} 的 torch tensor promote operand 必须全是 tensor，实得 {non_tensors}")
        rule_id = rule.get("rule_id", _ABSENT)
        operand_dtypes = [by_name[name]["dtype"] for name in operands]
        output_dtype = promote_dtypes(rule_id, operand_dtypes)
        relation = {
            "rule": "promote", "rule_id": rule_id, "operands": operands,
            "operand_dtypes": [
                {"name": name, "dtype": by_name[name]["dtype"]} for name in operands
            ],
            "output_dtype": output_dtype,
            "mixed_dtype": len(set(operand_dtypes)) > 1,
        }
        if provenance is not None:
            relation["provenance"] = provenance
        return output_dtype, relation
    if kind == "follows":
        _unknown_keys(rule, {"rule", "operand", "provenance"}, where)
        operand = _single_operand(rule, by_name, where)
        output_dtype = by_name[operand]["dtype"]
        relation = {
            "rule": "follows", "operand": operand, "output_dtype": output_dtype,
            "mixed_dtype": False,
        }
        if provenance is not None:
            relation["provenance"] = provenance
        return output_dtype, relation
    if kind == "fixed":
        _unknown_keys(rule, {"rule", "value", "provenance"}, where)
        output_dtype = _dtype(rule.get("value", _ABSENT), f"{where}.value")
        relation = {
            "rule": "fixed", "output_dtype": output_dtype, "mixed_dtype": False,
        }
        if provenance is not None:
            relation["provenance"] = provenance
        return output_dtype, relation
    raise MultiInputContractError(
        f"{where}.rule={kind!r} 非受控 dtype rule，须属 ['promote', 'follows', 'fixed']")


def _expected_identity(expected_parameters):
    if expected_parameters is None:
        return None
    if not isinstance(expected_parameters, list) or not expected_parameters:
        raise MultiInputContractError("expected_parameters 须为非空参数身份数组")
    result = []
    for index, raw in enumerate(expected_parameters):
        where = f"expected_parameters[{index}]"
        item = _object(raw, where)
        _unknown_keys(item, {"name", "kind", "binding"}, where)
        missing = [key for key in ("name", "kind", "binding") if key not in item]
        if missing:
            raise MultiInputContractError(f"{where} 缺参数身份字段 {missing}")
        result.append((
            _name(item["name"], f"{where}.name"), item["kind"], item["binding"]
        ))
    if len(result) != len({name for name, _, _ in result}):
        raise MultiInputContractError("expected_parameters 参数名重复")
    return result


def resolve_profile(profile, expected_parameters=None, *, require_provenance=False):
    """校验并解析一条物化 profile；不修改传入对象。"""
    raw = _object(profile, "profile")
    _unknown_keys(raw, {"profile_id", "inputs", "output"}, "profile")
    for key in ("profile_id", "inputs", "output"):
        if key not in raw:
            raise MultiInputContractError(f"profile 缺必填字段 {key!r}")
    profile_id = raw["profile_id"]
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise MultiInputContractError(
            f"profile.profile_id={profile_id!r} 非安全标识（仅允许字母、数字、_.-）")
    inputs_raw = raw["inputs"]
    if not isinstance(inputs_raw, list) or not inputs_raw:
        raise MultiInputContractError("profile.inputs 须为非空有序数组")
    inputs = [_normalize_input(item, i) for i, item in enumerate(inputs_raw)]
    names = [item["name"] for item in inputs]
    if len(names) != len(set(names)):
        duplicates = sorted(name for name in set(names) if names.count(name) > 1)
        raise MultiInputContractError(f"profile.inputs 参数名重复 {duplicates}")

    actual_identity = [(item["name"], item["kind"], item["binding"]) for item in inputs]
    expected_identity = _expected_identity(expected_parameters)
    if expected_identity is not None and actual_identity != expected_identity:
        raise MultiInputContractError(
            f"profile 参数身份与调用契约不一致：期望 {expected_identity}，实得 {actual_identity}；"
            "顺序/name/kind/binding 必须逐字一致")

    by_name = {item["name"]: item for item in inputs}
    output = _object(raw["output"], "profile.output")
    _unknown_keys(output, {"name", "kind", "binding", "format", "shape", "dtype"},
                  "profile.output")
    missing = [key for key in ("name", "kind", "binding", "format", "shape", "dtype")
               if key not in output]
    if missing:
        raise MultiInputContractError(f"profile.output 缺必填字段 {missing}")
    if output["kind"] != KIND_TENSOR or output["binding"] != BINDING_DEVICE_TENSOR:
        raise MultiInputContractError(
            "profile.output 当前必须是 kind='tensor', binding='device_tensor'")

    output_shape, shape_relation = _resolve_shape_rule(
        output["shape"], by_name, "profile.output.shape",
        require_provenance=require_provenance)
    output_dtype, dtype_relation = _resolve_dtype_rule(
        output["dtype"], by_name, "profile.output.dtype",
        require_provenance=require_provenance)
    normalized_output = {
        "name": _name(output["name"], "profile.output.name"),
        "kind": KIND_TENSOR,
        "binding": BINDING_DEVICE_TENSOR,
        "shape": output_shape,
        "dtype": output_dtype,
        "format": _format(output["format"], "profile.output.format"),
    }
    return {
        "profile_id": profile_id,
        "inputs": inputs,
        "output": normalized_output,
        "relations": {"shape": shape_relation, "dtype": dtype_relation},
    }


def _canonical_sha256(value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def resolve_profiles(profiles, expected_parameters=None):
    """解析 profile 集并导出广播/秩/host-scalar 覆盖账本与稳定摘要。"""
    if not isinstance(profiles, list) or not profiles:
        raise MultiInputContractError("profiles 须为非空数组（0 profile 不得冒充覆盖）")
    resolved = [resolve_profile(profile, expected_parameters) for profile in profiles]
    ids = [profile["profile_id"] for profile in resolved]
    if len(ids) != len(set(ids)):
        duplicates = sorted(value for value in set(ids) if ids.count(value) > 1)
        raise MultiInputContractError(f"profiles.profile_id 重复 {duplicates}")

    coverage = {
        "profiles": len(resolved),
        "broadcasted": sum(bool(p["relations"]["shape"]["broadcasted"]) for p in resolved),
        "rank_mismatch": sum(bool(p["relations"]["shape"]["rank_mismatch"]) for p in resolved),
        "rank0_tensor": sum(bool(p["relations"]["shape"]["rank0_tensor"]) for p in resolved),
        "host_scalar": sum(
            any(item["kind"] == KIND_SCALAR for item in p["inputs"]) for p in resolved),
        # host scalar 与 tensor-tensor promotion 是两套语义；coverage.mixed_dtype 只统计
        # device tensor 轴，不能因一个 float scalar 配一个 bf16 tensor 就谎称覆盖混合 tensor dtype。
        "mixed_dtype": sum(
            len({item["dtype"] for item in p["inputs"]
                 if item["kind"] == KIND_TENSOR}) > 1 for p in resolved),
        "mixed_format": sum(
            len({item["format"] for item in p["inputs"] if item["kind"] == KIND_TENSOR}) > 1
            for p in resolved),
    }
    body = {"schema_version": SCHEMA_VERSION, "profiles": resolved, "coverage": coverage}
    return {**body, "sha256": _canonical_sha256(body)}


def _required_coverage(value):
    if value is None:
        return {}
    record = _object(value, "multi_input_contract.required_coverage")
    _unknown_keys(record, set(_COVERAGE_KEYS), "multi_input_contract.required_coverage")
    out = {}
    for key, minimum in record.items():
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
            raise MultiInputContractError(
                f"multi_input_contract.required_coverage.{key} 须为非负整数，得 {minimum!r}")
        out[key] = int(minimum)
    return out


def resolve_contract(contract, expected_parameters):
    """解析 spec 级公共输出关系 + 逐输入 profile，并执行必需覆盖下限门。"""
    raw = _object(contract, "multi_input_contract")
    _unknown_keys(
        raw, {"schema_version", "output", "profiles", "required_coverage"},
        "multi_input_contract")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise MultiInputContractError(
            f"multi_input_contract.schema_version={raw.get('schema_version')!r}，须为 {SCHEMA_VERSION!r}")
    if "output" not in raw or "profiles" not in raw:
        raise MultiInputContractError("multi_input_contract 缺 output/profiles")
    profiles = raw["profiles"]
    if not isinstance(profiles, list) or not profiles:
        raise MultiInputContractError("multi_input_contract.profiles 须为非空数组")
    expanded = []
    for index, profile in enumerate(profiles):
        item = _object(profile, f"multi_input_contract.profiles[{index}]")
        _unknown_keys(item, {"profile_id", "inputs"}, f"multi_input_contract.profiles[{index}]")
        expanded.append({
            "profile_id": item.get("profile_id"),
            "inputs": item.get("inputs"),
            "output": raw["output"],
        })
    resolved = [
        resolve_profile(item, expected_parameters, require_provenance=True)
        for item in expanded
    ]
    ids = [item["profile_id"] for item in resolved]
    if len(ids) != len(set(ids)):
        duplicates = sorted(value for value in set(ids) if ids.count(value) > 1)
        raise MultiInputContractError(f"multi_input_contract profile_id 重复 {duplicates}")
    coverage = {
        "profiles": len(resolved),
        "broadcasted": sum(bool(p["relations"]["shape"]["broadcasted"]) for p in resolved),
        "rank_mismatch": sum(bool(p["relations"]["shape"]["rank_mismatch"]) for p in resolved),
        "rank0_tensor": sum(bool(p["relations"]["shape"]["rank0_tensor"]) for p in resolved),
        "host_scalar": sum(any(i["kind"] == KIND_SCALAR for i in p["inputs"]) for p in resolved),
        "mixed_dtype": sum(
            len({i["dtype"] for i in p["inputs"] if i["kind"] == KIND_TENSOR}) > 1
            for p in resolved),
        "mixed_format": sum(
            len({i["format"] for i in p["inputs"] if i["kind"] == KIND_TENSOR}) > 1
            for p in resolved),
    }
    required = _required_coverage(raw.get("required_coverage"))
    missing = {
        key: {"required": minimum, "observed": coverage[key]}
        for key, minimum in required.items() if coverage[key] < minimum
    }
    if missing:
        raise MultiInputContractError(
            f"multi_input_contract 必需覆盖未满足 {missing}（不得用退化 profile 冒充覆盖）")
    body = {
        "schema_version": SCHEMA_VERSION,
        "profiles": resolved,
        "coverage": coverage,
        "required_coverage": required,
    }
    return {**body, "sha256": _canonical_sha256(body)}


def resolve_spec_contract(spec):
    """解析 spec 顶层 ``multi_input_contract`` 并与 IO 参数表逐字段交叉。

    字段缺席返回 ``None``，让 legacy 路径保持原样；字段在场但任一身份、允许 dtype、静态
    parameter format 或输出关系不一致时拒绝。gen_cases/codegen 共用本入口，避免 planner 和
    Extension 各自接受不同的参数契约。
    """
    raw_spec = _object(spec, "spec")
    if "multi_input_contract" not in raw_spec:
        return None
    params = raw_spec.get("params")
    if not isinstance(params, list) or not params:
        raise MultiInputContractError("multi_input_contract 需要非空 spec.params")
    expected, outputs, by_name = [], [], {}
    for index, raw_param in enumerate(params):
        where = f"spec.params[{index}]"
        param = _object(raw_param, where)
        name, io = param.get("name"), param.get("io")
        if not isinstance(name, str) or not name or name in by_name:
            raise MultiInputContractError(f"{where}.name 缺失或重复 {name!r}")
        by_name[name] = param
        if io == "in":
            if param.get("kind") != KIND_TENSOR \
                    or param.get("binding") != BINDING_DEVICE_TENSOR:
                raise MultiInputContractError(
                    f"multi_input_contract 的 tensor 参数 {name!r} 必须显式声明 "
                    "kind='tensor', binding='device_tensor'")
            _format(param.get("format"), f"{where}.format")
            expected.append({"name": name, "kind": KIND_TENSOR,
                             "binding": BINDING_DEVICE_TENSOR})
        elif io == "attr" and param.get("binding") == BINDING_HOST_SCALAR:
            if param.get("kind") != KIND_SCALAR:
                raise MultiInputContractError(
                    f"host_scalar 参数 {name!r} 必须显式声明 kind='scalar'")
            expected.append({"name": name, "kind": KIND_SCALAR,
                             "binding": BINDING_HOST_SCALAR})
        elif io == "out":
            outputs.append(param)
    if len(outputs) != 1:
        raise MultiInputContractError(
            f"multi_input_contract v1 当前要求恰一个 out 参数，实得 "
            f"{[p.get('name') for p in outputs]}")
    out_param = outputs[0]
    if out_param.get("kind") != KIND_TENSOR \
            or out_param.get("binding") != BINDING_DEVICE_TENSOR:
        raise MultiInputContractError(
            "multi_input_contract 的 out 参数必须显式声明 tensor/device_tensor")
    _format(out_param.get("format"), "spec out 参数 format")

    contract = _object(raw_spec["multi_input_contract"], "multi_input_contract")
    contract_output = _object(contract.get("output"), "multi_input_contract.output")
    if out_param.get("dtype_relation") != contract_output.get("dtype"):
        raise MultiInputContractError(
            "multi_input_contract.output.dtype 必须与 spec out 参数的 dtype_relation 逐字段相同；"
            "planner 与 precision validator 不得各持一份可漂移的关系真相")
    bundle = resolve_contract(contract, expected)
    for profile in bundle["profiles"]:
        for item in profile["inputs"]:
            param = by_name[item["name"]]
            allowed = param.get("dtype") or []
            if item["dtype"] not in allowed:
                raise MultiInputContractError(
                    f"profile {profile['profile_id']!r} 参数 {item['name']!r} "
                    f"dtype={item['dtype']!r} 不在 spec 允许集 {allowed}")
            if item["kind"] == KIND_TENSOR and item["format"] != param["format"]:
                raise MultiInputContractError(
                    f"profile {profile['profile_id']!r} 参数 {item['name']!r} "
                    f"format={item['format']!r} 与 spec.params 声明 {param['format']!r} 不一致；"
                    "同一 ABI 参数的 format 不得逐 case 漂移")
        output = profile["output"]
        if output["name"] != out_param["name"] or output["format"] != out_param["format"]:
            raise MultiInputContractError(
                f"profile {profile['profile_id']!r} 输出 identity/format 与 spec out 参数不一致")
        if output["dtype"] not in (out_param.get("dtype") or []):
            raise MultiInputContractError(
                f"profile {profile['profile_id']!r} 输出 dtype={output['dtype']!r} "
                f"不在 spec out 允许集 {out_param.get('dtype') or []}")
    return bundle


def derive_output_dtype_relation(dtype_relation, case_input_dtypes):
    """供 precision_policy 共用：从显式、有来源账本的关系派生 tensor 输出 dtype。

    ``promote`` 走 taskbook×op_def 双源与受控表；``follows`` / ``fixed`` 走 taskbook 来源。
    host scalar 不伪装进 ``case_input_dtypes``，因此 tensor-scalar 与 tensor-tensor 的 dtype 语义
    在这里物理分开。
    """
    if not isinstance(case_input_dtypes, (list, tuple)) or not case_input_dtypes:
        raise MultiInputContractError("case_input_dtypes 须为非空 (name,dtype) 序列")
    by_name = {}
    for index, item in enumerate(case_input_dtypes):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise MultiInputContractError(
                f"case_input_dtypes[{index}] 须为 (name,dtype)，得 {item!r}")
        name, dtype = item
        if not isinstance(name, str) or not name or name in by_name:
            raise MultiInputContractError(f"case_input_dtypes 参数名缺失或重复 {name!r}")
        by_name[name] = {
            "name": name, "kind": KIND_TENSOR, "binding": BINDING_DEVICE_TENSOR,
            "dtype": _dtype(dtype, f"case_input_dtypes[{index}].dtype"),
        }
    output_dtype, relation = _resolve_dtype_rule(
        dtype_relation, by_name, "dtype_relation", require_provenance=True)
    return output_dtype


def derive_promoted_output_dtype(dtype_relation, case_input_dtypes):
    """兼容窄入口：要求关系确为 tensor-tensor ``promote``。"""
    relation = _object(dtype_relation, "dtype_relation")
    if relation.get("rule") != "promote":
        raise MultiInputContractError(
            f"dtype_relation.rule={relation.get('rule')!r}；本入口只接受显式 promote")
    return derive_output_dtype_relation(relation, case_input_dtypes)


__all__ = [
    "BINDING_DEVICE_TENSOR",
    "BINDING_HOST_SCALAR",
    "DTYPES",
    "KIND_SCALAR",
    "KIND_TENSOR",
    "MultiInputContractError",
    "PROMOTE_TORCH_TENSOR_TENSOR_V1",
    "PROMOTION_RULES",
    "SCHEMA_VERSION",
    "TENSOR_FORMATS",
    "promote_dtypes",
    "resolve_broadcast_shape",
    "resolve_contract",
    "resolve_spec_contract",
    "derive_output_dtype_relation",
    "derive_promoted_output_dtype",
    "resolve_profile",
    "resolve_profiles",
]
