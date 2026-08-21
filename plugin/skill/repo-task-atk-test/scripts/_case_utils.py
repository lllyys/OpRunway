"""共享用例解析和产物摘要。"""

import hashlib
import json
import math
import os
from pathlib import Path


TENSOR_TYPES = frozenset({"tensor", "tensors", "tensor_tuple"})
SCALAR_TYPES = frozenset({"scalar", "scalars", "scalar_tuple"})
ATTR_TYPES = frozenset({"attr", "attrs", "attr_tuple"})
TUPLE_TYPES = frozenset({"tensor_tuple", "scalar_tuple", "attr_tuple"})
# 复合输入的列表形态：ATK 把每个元素展成一条 InputCaseConfig，
# 元素自身的取值又包在一层单元素列表里（range_values=[v]）。
LIST_TYPES = frozenset({"tensors", "scalars", "attrs"})


def load_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_input_root(atk_output, since):
    """定位本轮跑测产出的、可直接传给 --input_data 的输入目录。"""
    candidates = []
    for root, _dirs, files in os.walk(atk_output):
        if "input.bin" not in files:
            continue
        parent = os.path.dirname(root)
        if os.path.getmtime(root) >= since:
            candidates.append(parent)
    if not candidates:
        return None
    return max(set(candidates), key=candidates.count)


def iter_cases(case_data):
    if isinstance(case_data, dict):
        return case_data.get("cases", [case_data])
    return case_data


def grouped_inputs(case):
    groups = list(case.get("inputs") or [])
    groups.extend(case.get("method_inputs") or [])
    if case.get("tensor_input"):
        groups.append(case["tensor_input"])
    return groups


def _flatten(value):
    if isinstance(value, list):
        for item in value:
            yield from _flatten(item)
    elif isinstance(value, dict):
        yield value


def iter_input_specs(case):
    for group in grouped_inputs(case):
        yield from _flatten(group)


def tensor_inputs(case):
    return [
        spec
        for spec in iter_input_specs(case)
        if spec.get("type") in TENSOR_TYPES
    ]


def scalar_inputs(case):
    return [
        spec
        for spec in iter_input_specs(case)
        if spec.get("type") in SCALAR_TYPES
    ]


def attr_value(case, name):
    def semantic_value(value):
        # ATK 的 StandardDataGenerator 把字符串 "default" 转为 Python None。
        # 覆盖核算比较公开接口语义，不比较 YAML 的编码令牌。
        return None if value == "default" else value

    for group in grouped_inputs(case):
        specs = list(_flatten(group))
        if not specs or specs[0].get("name") != name:
            continue
        values = [
            semantic_value(spec.get("range_values", spec.get("value")))
            for spec in specs
        ]
        kind = specs[0].get("type")
        if kind in TUPLE_TYPES or kind in LIST_TYPES:
            # 复合输入的语义值是**扁平的元素序列**。不脱这层壳时，长度 1 的
            # 复合输入恰好等于 combo（[v] == [v]），长度 >1 的却变成 [[a],[b]]，
            # 于是只有多元素的组合对不上——实测 121 条里 65 条这样失配。
            flat = [
                item[0] if isinstance(item, list) and len(item) == 1 else item
                for item in values
            ]
            return tuple(flat) if kind in TUPLE_TYPES else flat
        single = values[0] if len(values) == 1 else values
        # 单值 flat attr 的语义值是值池里的那个值。ATK 把它编码成
        # range_values=[v]，不脱这层壳提取结果就是 [v]，而组合表的标量轴值
        # 是 v，signature() 永远失配——含 attr 轴的分面 C3/C5 会全灭。
        # 复合类型在上面的分支已经返回，走到这里的只有标量语义的 attr。
        if isinstance(single, list) and len(single) == 1:
            return single[0]
        return single
    return None


def numel(shape):
    return math.prod(shape or [])


DTYPE_BYTES = {
    "bool": 1, "fp8e4m3": 1, "fp8e5m2": 1, "int8": 1, "uint8": 1,
    "bf16": 2, "fp16": 2, "int16": 2, "uint16": 2,
    "fp32": 4, "hf32": 4, "tf32": 4, "int32": 4, "uint32": 4,
    "complex64": 8, "fp64": 8, "int64": 8, "uint64": 8,
    "complex128": 16,
}

DTYPE_ALIASES = {
    "bfloat16": "bf16", "double": "fp64", "float": "fp32",
    "float16": "fp16", "float32": "fp32", "float64": "fp64", "int": "int32",
}


def dtype_bytes(dtype):
    """返回张量 dtype 的单元素字节数。"""
    name = str(dtype or "").lower()
    return DTYPE_BYTES.get(DTYPE_ALIASES.get(name, name))


def extract_axis(case, rule):
    source = rule.get("from")
    if source == "attr":
        return attr_value(case, rule["name"])
    if source == "scalar_dtype":
        scalars = scalar_inputs(case)
        index = rule.get("index", 0)
        if index >= len(scalars):
            return None
        return scalars[index].get("dtype")
    tensors = tensor_inputs(case)
    index = rule.get("index", 0)
    if index >= len(tensors):
        return None
    tensor = tensors[index]
    if source == "input_dtype":
        return tensor.get("dtype")
    if source == "input_shape":
        return tensor.get("shape")
    if source == "input_rank":
        return len(tensor.get("shape") or [])
    if source == "input_numel":
        return numel(tensor.get("shape"))
    if source == "input_bytes":
        # 性能分档用总字节数，不用元素数：同一个 numel 换 dtype，
        # 实测 device 耗时按字节宽度单调变化（1B 8.64us → 8B 11.03us）。
        width = dtype_bytes(tensor.get("dtype"))
        count = numel(tensor.get("shape"))
        return None if width is None or count is None else width * count
    raise ValueError(f"未知 extract.from: {source}")


def normalize(value):
    if isinstance(value, list):
        return tuple(normalize(item) for item in value)
    if isinstance(value, dict):
        return tuple((key, normalize(value[key])) for key in sorted(value))
    return value


def signature(values, axes):
    return "|".join(
        f"{axis}={normalize(values.get(axis))!r}"
        for axis in axes
    )
