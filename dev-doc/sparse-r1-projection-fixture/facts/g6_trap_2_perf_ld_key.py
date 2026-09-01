#!/usr/bin/env python3
"""按事实表 FACTS 生成 CSV 驱动 GTest 用例。只编辑 FACTS 区；通用代码区禁止修改。"""

# ===== FACTS 区开始 =====
FACTS = {'schema_version': 1,
 'generator_version': 1,
 'op': 'g6_trap2',
 'family': 'synth6',
 'symbol': 'aclblasG6Trap2',
 'returns': 'aclblasStatus_t',
 'params': [{'name': 'handle', 'ctype': 'aclblasHandle_t', 'role': 'handle'},
            {'name': 'n', 'ctype': 'int', 'role': 'dim'},
            {'name': 'A',
             'ctype': 'float*',
             'role': 'matrix',
             'dtype': 'float32',
             'dir': 'inout',
             'rows': 'n',
             'cols': 'n',
             'ld': 'lda'},
            {'name': 'lda', 'ctype': 'int', 'role': 'layout', 'kind': 'ld', 'of': 'A'}],
 'constraints': ['n >= 1'],
 'golden': {'kind': 'cblas', 'symbol': 'cblas_ssyrk'},
 'verify': ['full'],
 'perf': {'key': ['n', 'lda'],
          'sweep': False,
          'rows': [{'n': 1, 'lda': 16},
                   {'n': 1, 'lda': 32},
                   {'n': 2, 'lda': 16},
                   {'n': 2, 'lda': 32},
                   {'n': 3, 'lda': 16},
                   {'n': 3, 'lda': 32},
                   {'n': 4, 'lda': 16},
                   {'n': 4, 'lda': 32},
                   {'n': 5, 'lda': 16},
                   {'n': 5, 'lda': 32},
                   {'n': 6, 'lda': 16},
                   {'n': 6, 'lda': 32},
                   {'n': 7, 'lda': 16},
                   {'n': 7, 'lda': 32},
                   {'n': 8, 'lda': 16},
                   {'n': 8, 'lda': 32},
                   {'n': 9, 'lda': 16},
                   {'n': 9, 'lda': 32},
                   {'n': 10, 'lda': 16},
                   {'n': 10, 'lda': 32},
                   {'n': 11, 'lda': 16},
                   {'n': 11, 'lda': 32},
                   {'n': 12, 'lda': 16},
                   {'n': 12, 'lda': 32},
                   {'n': 13, 'lda': 16},
                   {'n': 13, 'lda': 32},
                   {'n': 14, 'lda': 16},
                   {'n': 14, 'lda': 32},
                   {'n': 15, 'lda': 16},
                   {'n': 15, 'lda': 32},
                   {'n': 16, 'lda': 16},
                   {'n': 16, 'lda': 32},
                   {'n': 17, 'lda': 16},
                   {'n': 17, 'lda': 32},
                   {'n': 18, 'lda': 16},
                   {'n': 18, 'lda': 32},
                   {'n': 19, 'lda': 16},
                   {'n': 19, 'lda': 32},
                   {'n': 20, 'lda': 16},
                   {'n': 20, 'lda': 32},
                   {'n': 21, 'lda': 16},
                   {'n': 21, 'lda': 32},
                   {'n': 22, 'lda': 16},
                   {'n': 22, 'lda': 32},
                   {'n': 23, 'lda': 16},
                   {'n': 23, 'lda': 32},
                   {'n': 24, 'lda': 16},
                   {'n': 24, 'lda': 32},
                   {'n': 25, 'lda': 16},
                   {'n': 25, 'lda': 32},
                   {'n': 26, 'lda': 16},
                   {'n': 26, 'lda': 32},
                   {'n': 27, 'lda': 16},
                   {'n': 27, 'lda': 32},
                   {'n': 28, 'lda': 16},
                   {'n': 28, 'lda': 32},
                   {'n': 29, 'lda': 16},
                   {'n': 29, 'lda': 32},
                   {'n': 30, 'lda': 16},
                   {'n': 30, 'lda': 32},
                   {'n': 31, 'lda': 16},
                   {'n': 31, 'lda': 32},
                   {'n': 32, 'lda': 16},
                   {'n': 32, 'lda': 32},
                   {'n': 33, 'lda': 16},
                   {'n': 33, 'lda': 32},
                   {'n': 34, 'lda': 16},
                   {'n': 34, 'lda': 32},
                   {'n': 35, 'lda': 16},
                   {'n': 35, 'lda': 32},
                   {'n': 36, 'lda': 16},
                   {'n': 36, 'lda': 32},
                   {'n': 37, 'lda': 16},
                   {'n': 37, 'lda': 32},
                   {'n': 38, 'lda': 16},
                   {'n': 38, 'lda': 32},
                   {'n': 39, 'lda': 16},
                   {'n': 39, 'lda': 32},
                   {'n': 40, 'lda': 16},
                   {'n': 40, 'lda': 32},
                   {'n': 41, 'lda': 16},
                   {'n': 41, 'lda': 32},
                   {'n': 42, 'lda': 16},
                   {'n': 42, 'lda': 32},
                   {'n': 43, 'lda': 16},
                   {'n': 43, 'lda': 32},
                   {'n': 44, 'lda': 16},
                   {'n': 44, 'lda': 32},
                   {'n': 45, 'lda': 16},
                   {'n': 45, 'lda': 32},
                   {'n': 46, 'lda': 16},
                   {'n': 46, 'lda': 32},
                   {'n': 47, 'lda': 16},
                   {'n': 47, 'lda': 32},
                   {'n': 48, 'lda': 16},
                   {'n': 48, 'lda': 32},
                   {'n': 49, 'lda': 16},
                   {'n': 49, 'lda': 32},
                   {'n': 50, 'lda': 16},
                   {'n': 50, 'lda': 32},
                   {'n': 51, 'lda': 16},
                   {'n': 51, 'lda': 32},
                   {'n': 52, 'lda': 16},
                   {'n': 52, 'lda': 32},
                   {'n': 53, 'lda': 16},
                   {'n': 53, 'lda': 32},
                   {'n': 54, 'lda': 16},
                   {'n': 54, 'lda': 32},
                   {'n': 55, 'lda': 16},
                   {'n': 55, 'lda': 32},
                   {'n': 56, 'lda': 16},
                   {'n': 56, 'lda': 32},
                   {'n': 57, 'lda': 16},
                   {'n': 57, 'lda': 32},
                   {'n': 58, 'lda': 16},
                   {'n': 58, 'lda': 32},
                   {'n': 59, 'lda': 16},
                   {'n': 59, 'lda': 32},
                   {'n': 60, 'lda': 16},
                   {'n': 60, 'lda': 32},
                   {'n': 61, 'lda': 16},
                   {'n': 61, 'lda': 32},
                   {'n': 62, 'lda': 16},
                   {'n': 62, 'lda': 32},
                   {'n': 63, 'lda': 16},
                   {'n': 63, 'lda': 32},
                   {'n': 64, 'lda': 16},
                   {'n': 64, 'lda': 32},
                   {'n': 65, 'lda': 16},
                   {'n': 65, 'lda': 32},
                   {'n': 66, 'lda': 16},
                   {'n': 66, 'lda': 32},
                   {'n': 67, 'lda': 16},
                   {'n': 67, 'lda': 32},
                   {'n': 68, 'lda': 16},
                   {'n': 68, 'lda': 32},
                   {'n': 69, 'lda': 16},
                   {'n': 69, 'lda': 32},
                   {'n': 70, 'lda': 16},
                   {'n': 70, 'lda': 32},
                   {'n': 71, 'lda': 16},
                   {'n': 71, 'lda': 32},
                   {'n': 72, 'lda': 16},
                   {'n': 72, 'lda': 32},
                   {'n': 73, 'lda': 16},
                   {'n': 73, 'lda': 32},
                   {'n': 74, 'lda': 16},
                   {'n': 74, 'lda': 32},
                   {'n': 75, 'lda': 16},
                   {'n': 75, 'lda': 32},
                   {'n': 76, 'lda': 16},
                   {'n': 76, 'lda': 32},
                   {'n': 77, 'lda': 16},
                   {'n': 77, 'lda': 32},
                   {'n': 78, 'lda': 16},
                   {'n': 78, 'lda': 32},
                   {'n': 79, 'lda': 16},
                   {'n': 79, 'lda': 32},
                   {'n': 80, 'lda': 16},
                   {'n': 80, 'lda': 32},
                   {'n': 81, 'lda': 16},
                   {'n': 81, 'lda': 32},
                   {'n': 82, 'lda': 16},
                   {'n': 82, 'lda': 32},
                   {'n': 83, 'lda': 16},
                   {'n': 83, 'lda': 32},
                   {'n': 84, 'lda': 16},
                   {'n': 84, 'lda': 32},
                   {'n': 85, 'lda': 16},
                   {'n': 85, 'lda': 32},
                   {'n': 86, 'lda': 16},
                   {'n': 86, 'lda': 32},
                   {'n': 87, 'lda': 16},
                   {'n': 87, 'lda': 32},
                   {'n': 88, 'lda': 16},
                   {'n': 88, 'lda': 32},
                   {'n': 89, 'lda': 16},
                   {'n': 89, 'lda': 32},
                   {'n': 90, 'lda': 16},
                   {'n': 90, 'lda': 32},
                   {'n': 91, 'lda': 16},
                   {'n': 91, 'lda': 32},
                   {'n': 92, 'lda': 16},
                   {'n': 92, 'lda': 32},
                   {'n': 93, 'lda': 16},
                   {'n': 93, 'lda': 32},
                   {'n': 94, 'lda': 16},
                   {'n': 94, 'lda': 32},
                   {'n': 95, 'lda': 16},
                   {'n': 95, 'lda': 32},
                   {'n': 96, 'lda': 16},
                   {'n': 96, 'lda': 32},
                   {'n': 97, 'lda': 16},
                   {'n': 97, 'lda': 32},
                   {'n': 98, 'lda': 16},
                   {'n': 98, 'lda': 32},
                   {'n': 99, 'lda': 16},
                   {'n': 99, 'lda': 32},
                   {'n': 100, 'lda': 16},
                   {'n': 100, 'lda': 32}],
          'meta': {'source': '合成负例：声明的 lda 会被物化无条件重算，两行同 n 不同 lda 会塌成同一行为'}},
 'sources': {'params': '合成负例：蓝图 §C 陷阱2——perf.key 含 kind=ld 的 layout'}}
# ===== FACTS 区结束 =====
# ===== 通用代码区（由 repo-task-blas-case-gen 渲染，禁止修改）=====

import ast
import csv
import itertools
from pathlib import Path
import sys


GENERATOR_VERSION = 1

# 每个 2^n 处给出 (2^n-1, 2^n, 2^n+1) 三元组，夹住 tiling 的「差一个/刚好一块/多一个」。
# 加退化 1、2、3 与一个大尺寸。文档写了维度上限就在 cases.dim_tiers 里裁剪。
MAT_DIM_TIERS = [1, 2, 3, 15, 16, 17, 63, 64, 65, 255, 256, 257, 1024]
# 纯向量长度再加中、大两个大值：100003 约 fp32 400 KB（medium），1050001 约 fp32 4 MB、
# fp16 2 MB（large），让归约类算子的规模覆盖真正落进 medium 与 large 两档。
VEC_DIM_TIERS = [1, 2, 3, 15, 16, 17, 63, 64, 65, 255, 256, 257, 1024, 100003, 1050001]
# 覆盖单批、双批和小奇数批量。
BATCH_TIERS = [1, 2, 5]
# min 使用最小合法值，pad 制造非对齐的额外间隔。
LD_TIERS = ["min", "pad"]
STRIDE_TIERS = ["min", "pad"]
# 0 和负步长只由 edge_cases 显式给出。
INC_TIERS = [1, 3]
# 覆盖单位元、零、负数和分数。
REAL_SCALAR_TIERS = [1.0, 0.0, -1.5, 0.5]
COMPLEX_SCALAR_TIERS = [
    (1.0, 0.0),
    (0.0, 0.0),
    (0.5, -1.5),
    (-2.0, 1.0),
]
# 与 CSV 框架的数据填充词表保持一致。
FILL_TIERS = [
    "RANDOM_NORM_1",
    "VALUE_NORM_0",
    "RANDOM_ALTER",
    "RANDOM_EXTREME",
]
# L0 使用两个小尺寸，ED 使用中等对齐尺寸。
L0_SIZES = [4, 8]
ED_SIZE = 16
SEED_BASE = 20260000
# 单用例 host 侧缓冲的设计上限。
DEFAULT_MAX_FOOTPRINT_BYTES = 4 * 1024 ** 3
DTYPE_BYTES = {
    "float16": 2,
    "bfloat16": 2,
    "float32": 4,
    "float64": 8,
    "complex64": 8,
    "complex128": 16,
    "int8": 1,
    "uint8": 1,
    "int16": 2,
    "uint16": 2,
    "int32": 4,
    "int64": 8,
}
COMPLEX_DTYPES = {"complex64", "complex128"}
SCALAR_ROLES = {"scalar", "inout_scalar", "out_scalar"}
BUFFER_ROLES = {"vector", "fixed_vector", "matrix", "int_array"}
PF_SWEEP_SIZES = [64, 128, 256, 512, 1024, 2048, 4096]


class GeneratorError(Exception):
    """表示带生成阶段上下文的确定性错误。"""


def _param_map(facts):
    return {param["name"]: param for param in facts["params"]}


def _profile_map(facts):
    return {profile["name"]: profile for profile in facts.get("dtype_profiles", [])}


def _case_options(facts):
    cases = facts.get("cases", {})
    return {
        "dim_tiers": cases.get("dim_tiers", MAT_DIM_TIERS),
        "vec_dim_tiers": cases.get("vec_dim_tiers", VEC_DIM_TIERS),
        "batch_tiers": cases.get("batch_tiers", BATCH_TIERS),
        "inc_tiers": cases.get("inc_tiers", INC_TIERS),
        "fill_tiers": cases.get("fill_tiers", FILL_TIERS),
        "max_footprint_bytes": cases.get(
            "max_footprint_bytes", DEFAULT_MAX_FOOTPRINT_BYTES
        ),
    }


def _expression_names(expression):
    tree = ast.parse(expression, mode="eval")
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id not in {"max", "min", "rows", "cols", "len"}
    }


def _dimension_kinds(facts):
    matrix_names = set()
    vector_names = set()
    batch_names = set()
    for param in facts["params"]:
        role = param["role"]
        if role == "matrix":
            for field in ("rows", "cols"):
                matrix_names.update(_expression_names(param[field]))
        elif role in {"vector", "int_array"}:
            vector_names.update(_expression_names(param["len"]))
        batch = param.get("batch")
        if isinstance(batch, dict):
            batch_names.add(batch["count"])
    return matrix_names, vector_names, batch_names


def _scalar_axis_values(param, facts):
    if "values" in param:
        return list(param["values"]), "scalar_value"
    if "dtype" in param:
        tiers = COMPLEX_SCALAR_TIERS if param["dtype"] in COMPLEX_DTYPES else REAL_SCALAR_TIERS
        return list(tiers), "scalar_value"
    return list(range(len(REAL_SCALAR_TIERS))), "scalar_tier"


def build_axes(facts):
    """按 params 顺序派生轴；单值轴仍保留在返回值中。"""
    options = _case_options(facts)
    matrix_names, vector_names, batch_names = _dimension_kinds(facts)
    profiles = facts.get("dtype_profiles", [])
    profile_inserted = False
    axes = []
    for param in facts["params"]:
        name = param["name"]
        role = param["role"]
        enum_kind = param.get("enum_kind", "op")
        if role == "enum":
            if enum_kind in {"dtype", "compute"}:
                if profiles and not profile_inserted:
                    axes.append(
                        {
                            "name": "profile",
                            "values": [profile["name"] for profile in profiles],
                            "kind": "profile",
                        }
                    )
                    profile_inserted = True
                continue
            axes.append({"name": name, "values": list(param["values"]), "kind": "op_enum"})
        elif role == "dim":
            if name in batch_names:
                values = options["batch_tiers"]
                kind = "batch_dim"
            elif name in matrix_names:
                values = options["dim_tiers"]
                kind = "mat_dim"
            elif name in vector_names:
                values = options["vec_dim_tiers"]
                kind = "vec_dim"
            else:
                values = options["dim_tiers"]
                kind = "mat_dim"
            axes.append({"name": name, "values": list(values), "kind": kind})
        elif role == "layout":
            kind = param["kind"]
            values = {
                "ld": LD_TIERS,
                "stride": STRIDE_TIERS,
                "inc": options["inc_tiers"],
                "batch": options["batch_tiers"],
            }[kind]
            axes.append({"name": name, "values": list(values), "kind": kind})
        elif role in {"scalar", "inout_scalar"}:
            values, kind = _scalar_axis_values(param, facts)
            axes.append({"name": name, "values": values, "kind": kind})
        elif role in {"vector", "matrix"}:
            direction = param.get("dir", "in")
            if direction not in {"in", "inout"} or "producer" in param:
                continue
            if role == "matrix" and param.get("conditioning"):
                axes.append(
                    {
                        "name": f"{name}_matrix_type",
                        "values": list(param["conditioning"]),
                        "kind": "matrix_type",
                    }
                )
            else:
                axes.append(
                    {
                        "name": f"{name.lower()}_fill",
                        "values": list(options["fill_tiers"]),
                        "kind": "fill",
                    }
                )
        elif role == "fixed_vector" and param.get("dir") in {"in", "inout"}:
            axes.append(
                {
                    "name": name,
                    "values": list(range(len(param["samples"]))),
                    "kind": "fixed_vector",
                }
            )
    # 一条不变量覆盖所有轴来源：轴取值必须唯一，否则 pairwise 用索引配对会不收敛。
    for axis in axes:
        hashable = [tuple(v) if isinstance(v, list) else v for v in axis["values"]]
        if len(hashable) != len(set(hashable)):
            raise GeneratorError(f"轴 {axis['name']!r} 含重复取值：{axis['values']}")
    return axes


class _Evaluator:
    def __init__(self, facts, state):
        self.params = _param_map(facts)
        self.state = state

    def evaluate(self, expression):
        return self._visit(ast.parse(expression, mode="eval").body)

    def _visit(self, node):
        if isinstance(node, ast.Name):
            return self.state[node.id]
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            left = self._visit(node.left)
            right = self._visit(node.right)
            operations = {
                ast.Add: lambda: left + right,
                ast.Sub: lambda: left - right,
                ast.Mult: lambda: left * right,
                ast.FloorDiv: lambda: left // right,
                ast.Mod: lambda: left % right,
            }
            return operations[type(node.op)]()
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return -self._visit(node.operand)
            if isinstance(node.op, ast.Not):
                return not self._visit(node.operand)
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                for value in node.values:
                    if not self._visit(value):
                        return False
                return True
            for value in node.values:
                if self._visit(value):
                    return True
            return False
        if isinstance(node, ast.Compare):
            values = [self._visit(node.left)] + [self._visit(item) for item in node.comparators]
            for left, operation, right in zip(values, node.ops, values[1:]):
                comparisons = {
                    ast.Eq: left == right,
                    ast.NotEq: left != right,
                    ast.Lt: left < right,
                    ast.LtE: left <= right,
                    ast.Gt: left > right,
                    ast.GtE: left >= right,
                }
                if not comparisons[type(operation)]:
                    return False
            return True
        if isinstance(node, ast.IfExp):
            branch = node.body if self._visit(node.test) else node.orelse
            return self._visit(branch)
        if isinstance(node, ast.Call):
            name = node.func.id
            if name in {"rows", "cols", "len"}:
                param_name = node.args[0].id
                return self._buffer_size(name, self.params[param_name])
            values = [self._visit(argument) for argument in node.args]
            return max(values) if name == "max" else min(values)
        raise ValueError(f"不支持的表达式节点 {type(node).__name__}")

    def _buffer_size(self, function, param):
        if function in {"rows", "cols"}:
            return self.evaluate(param[function])
        length = param.get("len")
        return length if isinstance(length, int) else self.evaluate(length)


def _profile_for_selection(facts, selection):
    profiles = facts.get("dtype_profiles", [])
    if not profiles:
        return None
    name = selection.get("profile", profiles[0]["name"])
    return _profile_map(facts)[name]


def _dtype_token(token):
    mapping = {
        "FP16": "float16",
        "FP32": "float32",
        "BF16": "bfloat16",
        "INT8": "int8",
    }
    try:
        return mapping[token]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"未知 aclDataType 记号 {token!r}") from exc


def _param_dtype(param, profile):
    if "dtype" in param:
        return param["dtype"]
    if param["role"] in SCALAR_ROLES:
        return profile["scalar_dtype"]
    return _dtype_token(profile["assign"][param["dtype_from"]])


def _first_target(param):
    target = param.get("of")
    return target[0] if isinstance(target, list) else target


def _complete_selection(axes, partial):
    selection = dict(partial)
    for axis in axes:
        selection.setdefault(axis["name"], axis["values"][0])
    return selection


def _materialize(facts, axes, partial, overrides=None):
    selection = _complete_selection(axes, partial)
    overrides = dict(overrides or {})
    params = _param_map(facts)
    profile = _profile_for_selection(facts, selection)
    state = {}
    if profile:
        state.update(profile["assign"])
        state["profile"] = profile["name"]
    for param in facts["params"]:
        name = param["name"]
        role = param["role"]
        if role == "enum" and param.get("enum_kind", "op") in {"op", "algo"}:
            state[name] = selection[name]
        elif role == "dim":
            state[name] = selection[name]
    for param in facts["params"]:
        name = param["name"]
        role = param["role"]
        if role in {"scalar", "inout_scalar"}:
            value = selection[name]
            if "dtype_from" in param and "values" not in param:
                tiers = (
                    COMPLEX_SCALAR_TIERS
                    if profile["scalar_dtype"] in COMPLEX_DTYPES
                    else REAL_SCALAR_TIERS
                )
                value = tiers[value]
            state[name] = value
        elif role in {"vector", "matrix"}:
            direction = param.get("dir", "in")
            if direction in {"in", "inout"} and "producer" not in param:
                if role == "matrix" and param.get("conditioning"):
                    state[f"{name.lower()}_fill"] = _case_options(facts)["fill_tiers"][0]
                    state[f"{name}_matrix_type"] = selection[f"{name}_matrix_type"]
                else:
                    state[f"{name.lower()}_fill"] = selection[f"{name.lower()}_fill"]
        elif role == "fixed_vector" and param.get("dir") in {"in", "inout"}:
            state[name] = list(param["samples"][selection[name]])
    for key, value in overrides.items():
        if key in params and params[key]["role"] != "layout":
            state[key] = value
    evaluator = _Evaluator(facts, state)
    for param in facts["params"]:
        if param["role"] != "layout" or param["kind"] == "stride":
            continue
        name = param["name"]
        if name in overrides:
            state[name] = overrides[name]
            continue
        value = selection[name]
        if param["kind"] == "ld":
            rows_value = evaluator.evaluate(params[_first_target(param)]["rows"])
            value = max(1, rows_value) if value == "min" else rows_value + 5
        state[name] = value
    evaluator = _Evaluator(facts, state)
    for param in facts["params"]:
        if param["role"] != "layout" or param["kind"] != "stride":
            continue
        name = param["name"]
        if name in overrides:
            state[name] = overrides[name]
            continue
        target = params[_first_target(param)]
        minimum = _buffer_base_elements(target, state, evaluator)
        state[name] = minimum if selection[name] == "min" else minimum + 7
    for key, value in overrides.items():
        if key in state or key in params:
            state[key] = value
            continue
        for param in facts["params"]:
            if param["role"] != "fixed_vector" or not key.startswith(param["name"]):
                continue
            suffix = key[len(param["name"]):]
            if suffix.isdigit() and param["name"] in state:
                state[param["name"]][int(suffix)] = value
                break
    return selection, state, profile


def _buffer_base_elements(param, state, evaluator):
    role = param["role"]
    if role == "matrix":
        if param.get("storage", "full") == "packed":
            rows = evaluator.evaluate(param["rows"])
            return max(0, rows * (rows + 1) // 2)
        return max(0, state[param["ld"]] * evaluator.evaluate(param["cols"]))
    if role == "vector":
        length = evaluator.evaluate(param["len"])
        if length <= 0:
            return 0
        increment = param["inc"]
        inc = increment if isinstance(increment, int) else state[increment]
        return 1 + (length - 1) * abs(inc)
    length = param["len"]
    return length if isinstance(length, int) else evaluator.evaluate(length)


def _footprint(facts, state, profile):
    evaluator = _Evaluator(facts, state)
    total = 0
    for param in facts["params"]:
        role = param["role"]
        if role not in BUFFER_ROLES:
            continue
        dtype = _param_dtype(param, profile) if role != "int_array" else param.get("dtype", "int32")
        elements = _buffer_base_elements(param, state, evaluator)
        batch = param.get("batch")
        if batch:
            count = state[batch["count"]]
            if batch["model"] == "strided" and count > 0:
                elements += (count - 1) * state[batch["stride"]]
            else:
                elements *= count
        total += DTYPE_BYTES[dtype] * max(0, elements)
    return total


def _row_is_valid(facts, state, profile):
    evaluator = _Evaluator(facts, state)
    if any(not evaluator.evaluate(item) for item in facts.get("constraints", [])):
        return False
    limit = _case_options(facts)["max_footprint_bytes"]
    return _footprint(facts, state, profile) <= limit


def _header_columns(facts):
    profiles = facts.get("dtype_profiles", [])
    profile_has_complex = any(
        profile["scalar_dtype"] in COMPLEX_DTYPES for profile in profiles
    )
    columns = ["case_name", "description"]
    for param in facts["params"]:
        name = param["name"]
        role = param["role"]
        direction = param.get("dir", "in")
        if role in {"handle", "out_scalar", "int_array"}:
            continue
        if role in {"enum", "dim", "layout"}:
            columns.append(name)
        elif role in {"scalar", "inout_scalar"}:
            if param.get("dtype") in COMPLEX_DTYPES:
                columns.extend([f"{name}_re", f"{name}_im"])
            elif "dtype_from" in param and profile_has_complex:
                columns.extend([f"{name}_re", f"{name}_im"])
            else:
                columns.append(name)
        elif role in {"vector", "matrix"}:
            if direction in {"in", "inout"} and "producer" not in param:
                columns.append(f"{name.lower()}_fill")
                if role == "matrix" and param.get("conditioning"):
                    columns.append(f"{name}_matrix_type")
        elif role == "fixed_vector" and direction in {"in", "inout"}:
            columns.extend(f"{name}{index}" for index in range(param["len"]))
    columns.append("expect_result")
    for param in facts["params"]:
        name = param["name"]
        if param.get("nullable", False):
            columns.append(f"null{name[:1].upper()}{name[1:]}")
        if "batch" in param:
            columns.append(f"{name}_batch_pattern")
    columns.append("random_seed")
    return columns


def _body_mapping(facts, state, profile, expect, control_overrides=None):
    result = {}
    profile_has_complex = any(
        item["scalar_dtype"] in COMPLEX_DTYPES
        for item in facts.get("dtype_profiles", [])
    )
    for param in facts["params"]:
        name = param["name"]
        role = param["role"]
        direction = param.get("dir", "in")
        if role in {"enum", "dim", "layout"}:
            result[name] = state[name]
        elif role in {"scalar", "inout_scalar"}:
            value = state[name]
            is_complex = param.get("dtype") in COMPLEX_DTYPES
            is_complex = is_complex or ("dtype_from" in param and profile_has_complex)
            if is_complex:
                if profile and profile["scalar_dtype"] not in COMPLEX_DTYPES:
                    value = (value, 0.0)
                result[f"{name}_re"], result[f"{name}_im"] = value
            else:
                result[name] = value
        elif role in {"vector", "matrix"}:
            if direction in {"in", "inout"} and "producer" not in param:
                result[f"{name.lower()}_fill"] = state[f"{name.lower()}_fill"]
                if role == "matrix" and param.get("conditioning"):
                    result[f"{name}_matrix_type"] = state[f"{name}_matrix_type"]
        elif role == "fixed_vector" and direction in {"in", "inout"}:
            for index, value in enumerate(state[name]):
                result[f"{name}{index}"] = value
    result["expect_result"] = expect
    for param in facts["params"]:
        name = param["name"]
        if param.get("nullable", False):
            result[f"null{name[:1].upper()}{name[1:]}"] = 0
        if "batch" in param:
            result[f"{name}_batch_pattern"] = "UNIFORM"
    result.update(control_overrides or {})
    return result


def _description(axes, selection):
    parts = []
    for axis in axes:
        value = selection[axis["name"]]
        if axis["kind"] in {"scalar_tier", "fixed_vector"}:
            value = f"tier{value}"
        parts.append(f"{axis['name']}={value}")
    return " ".join(parts)


def _make_body(facts, axes, partial, expect="ACLBLAS_STATUS_SUCCESS", overrides=None):
    selection, state, profile = _materialize(facts, axes, partial, overrides)
    valid = _row_is_valid(facts, state, profile)
    controls = {
        key: int(value) if key.startswith("null") and isinstance(value, bool) else value
        for key, value in (overrides or {}).items()
        if key.startswith("null") or key.endswith("_batch_pattern")
    }
    body = _body_mapping(facts, state, profile, expect, controls)
    return selection, state, profile, body, valid


def _l0_rows(facts, axes, report):
    op_axes = [axis for axis in axes if axis["kind"] == "op_enum"]
    profile_axes = [axis for axis in axes if axis["kind"] == "profile"]
    op_values = [axis["values"] for axis in op_axes]
    profile_values = profile_axes[0]["values"] if profile_axes else [None]
    combinations = itertools.product(*op_values) if op_values else [()]
    rows = []
    for op_values_row in combinations:
        op_partial = {
            axis["name"]: value for axis, value in zip(op_axes, op_values_row)
        }
        for profile_name in profile_values:
            for size in L0_SIZES:
                partial = dict(op_partial)
                if profile_name is not None:
                    partial["profile"] = profile_name
                for axis in axes:
                    if axis["kind"] in {"mat_dim", "vec_dim"}:
                        partial[axis["name"]] = size
                    elif axis["kind"] in {"batch_dim", "batch"}:
                        partial[axis["name"]] = 2
                selection, _, _, body, valid = _make_body(facts, axes, partial)
                if not valid:
                    report["rows_dropped"] += 1
                    continue
                description_axes = op_axes + profile_axes
                description = _description(description_axes, selection)
                suffix = f" size={size}" if description else f"size={size}"
                rows.append((description + suffix, body))
    return rows


def _pair_key(left_axis, left_value, right_axis, right_value):
    return left_axis, left_value, right_axis, right_value


def _selection_pairs(selection, variable_axes):
    pairs = set()
    for left in range(len(variable_axes)):
        for right in range(left + 1, len(variable_axes)):
            left_value = variable_axes[left]["values"].index(
                selection[variable_axes[left]["name"]]
            )
            right_value = variable_axes[right]["values"].index(
                selection[variable_axes[right]["name"]]
            )
            pairs.add(_pair_key(left, left_value, right, right_value))
    return pairs


def _candidate_for_seed(facts, axes, variable_axes, uncovered, seed, report):
    assigned = {seed[0]: seed[1], seed[2]: seed[3]}
    remaining = [index for index in range(len(variable_axes)) if index not in assigned]
    attempts = [0]

    def ordered_values(axis_index, current):
        scores = []
        for value_index in range(len(variable_axes[axis_index]["values"])):
            score = 0
            for other_axis, other_value in current.items():
                left, right = sorted((axis_index, other_axis))
                pair = (
                    left,
                    value_index if left == axis_index else other_value,
                    right,
                    other_value if right == other_axis else value_index,
                )
                score += pair in uncovered
            scores.append((-score, value_index))
        return [value for _, value in sorted(scores)]

    exhausted = [False]

    def search(position, current):
        if attempts[0] >= 2000:
            exhausted[0] = True
            return None
        if position == len(remaining):
            attempts[0] += 1
            partial = {
                axis["name"]: axis["values"][current[index]]
                for index, axis in enumerate(variable_axes)
            }
            selection, _, _, body, valid = _make_body(facts, axes, partial)
            if valid:
                return selection, body
            report["rows_dropped"] += 1
            return None
        axis_index = remaining[position]
        for value_index in ordered_values(axis_index, current):
            current[axis_index] = value_index
            result = search(position + 1, current)
            if result is not None:
                return result
        current.pop(axis_index, None)
        return None

    candidate = search(0, dict(assigned))
    if candidate is not None:
        return candidate, "found"
    return None, "search_exhausted" if exhausted[0] else "infeasible"


def _pairwise_rows(facts, axes, report):
    variable_axes = [axis for axis in axes if len(axis["values"]) > 1]
    if len(variable_axes) < 2:
        selection, _, _, body, valid = _make_body(facts, axes, {})
        report["pairs_total"] = 0
        report["pairs_covered"] = 0
        if not valid:
            report["rows_dropped"] += 1
            return []
        return [(_description(variable_axes, selection) or "baseline", body)]
    uncovered = set()
    for left in range(len(variable_axes)):
        for right in range(left + 1, len(variable_axes)):
            for left_value in range(len(variable_axes[left]["values"])):
                for right_value in range(len(variable_axes[right]["values"])):
                    uncovered.add(_pair_key(left, left_value, right, right_value))
    report["pairs_total"] = len(uncovered)
    rows = []
    while uncovered:
        seed = min(uncovered)
        candidate, outcome = _candidate_for_seed(
            facts, axes, variable_axes, uncovered, seed, report
        )
        if candidate is None:
            uncovered.remove(seed)
            left, left_value, right, right_value = seed
            pair = {
                "left": variable_axes[left]["name"],
                "left_value": variable_axes[left]["values"][left_value],
                "right": variable_axes[right]["name"],
                "right_value": variable_axes[right]["values"][right_value],
            }
            if outcome == "search_exhausted":
                # 搜索预算耗尽 ≠ 已证明不可行；不静默降级，直接失败。
                raise GeneratorError(f"pairwise 搜索预算耗尽，未能判定值对：{pair}")
            report["pairs_infeasible"].append(pair)
            continue
        selection, body = candidate
        covered = _selection_pairs(selection, variable_axes) & uncovered
        uncovered.difference_update(covered)
        rows.append((_description(variable_axes, selection), body))
    unresolved = len(report["pairs_infeasible"])
    report["pairs_covered"] = report["pairs_total"] - len(uncovered) - unresolved
    return rows


def _edge_rows(facts, axes):
    partial = {}
    for axis in axes:
        if axis["kind"] in {"mat_dim", "vec_dim"}:
            partial[axis["name"]] = ED_SIZE
        elif axis["kind"] in {"batch_dim", "batch"}:
            partial[axis["name"]] = 2
        elif axis["kind"] in {"ld", "stride"}:
            partial[axis["name"]] = "min"
    rows = []
    for edge in facts.get("edge_cases", []):
        _, _, _, body, _ = _make_body(
            facts,
            axes,
            partial,
            expect=edge["expect"],
            overrides=edge["set"],
        )
        rows.append((edge["name"], body))
    return rows


def _perf_rows(facts, axes, report):
    perf = facts.get("perf")
    if not perf:
        return []
    base = {}
    for axis in axes:
        if axis["kind"] in {"mat_dim", "vec_dim"}:
            base[axis["name"]] = ED_SIZE
        elif axis["kind"] in {"batch_dim", "batch"}:
            base[axis["name"]] = 2
        elif axis["kind"] in {"ld", "stride"}:
            base[axis["name"]] = "min"
    rows = []
    for perf_row in perf["rows"]:
        partial = dict(base)
        partial.update({key: perf_row[key] for key in perf["key"]})
        _, _, _, body, valid = _make_body(facts, axes, partial)
        if not valid:
            # 显式声明的 perf 行不能被静默丢；它是声明，不是"尽量生成"。
            raise GeneratorError(
                f"perf.rows 显式行不满足 constraints 或 footprint：{perf_row}"
            )
        description = "pf " + " ".join(
            f"{key}={perf_row[key]}" for key in perf["key"]
        )
        rows.append((description, body))
    if perf.get("sweep", False):
        matrix_axes = [axis for axis in axes if axis["kind"] == "mat_dim"]
        for size in PF_SWEEP_SIZES:
            partial = dict(base)
            for axis in matrix_axes:
                partial[axis["name"]] = size
            _, _, _, body, valid = _make_body(facts, axes, partial)
            if not valid:
                report["rows_dropped"] += 1
                break
            rows.append((f"pf sweep={size}", body))
    return rows


def _stage(name, function, *args):
    try:
        return function(*args)
    except GeneratorError:
        raise
    except Exception as exc:
        raise GeneratorError(f"{name}: {exc}") from exc


def _assemble_rows(header, blocks, report):
    rows = []
    global_index = 0
    for block_name, block_rows in blocks:
        report["blocks"][block_name] = len(block_rows)
        for block_index, (description, body) in enumerate(block_rows, 1):
            global_index += 1
            body["case_name"] = f"TC_{block_name}_{block_index:03d}"
            body["description"] = description
            body["random_seed"] = SEED_BASE + global_index
            rows.append([body[column] for column in header])
    return rows


def generate(facts):
    """生成确定性 CSV 表头、行和覆盖报告。"""
    axes = _stage("轴派生", build_axes, facts)
    report = {
        "axes": [{"name": axis["name"], "values": len(axis["values"])} for axis in axes],
        "pairs_total": 0,
        "pairs_covered": 0,
        "pairs_infeasible": [],
        "rows_dropped": 0,
        "blocks": {},
    }
    blocks = []
    for name, function, arguments in (
        ("L0", _l0_rows, (facts, axes, report)),
        ("PW", _pairwise_rows, (facts, axes, report)),
        ("ED", _edge_rows, (facts, axes)),
        ("PF", _perf_rows, (facts, axes, report)),
    ):
        blocks.append((name, _stage(name, function, *arguments)))
    header = _stage("表头投影", _header_columns, facts)
    rows = _stage("行合并", _assemble_rows, header, blocks, report)
    return {"header": header, "rows": rows, "report": report}


def write_csv(path, header, rows):
    """使用 UTF-8、LF 和 QUOTE_MINIMAL 写出 CSV。"""
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def main():
    try:
        result = generate(FACTS)
        output = Path(__file__).resolve().parent / f"{FACTS['op']}_test.csv"
        _stage("写 CSV", write_csv, output, result["header"], result["rows"])
    except GeneratorError as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        return 2
    print(f"{output.name}: {len(result['rows'])} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
