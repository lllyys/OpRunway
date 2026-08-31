#!/usr/bin/env python3
"""校验 BLAS 任务包的事实表 FACTS，并投影 CSV 表头。

退出码 0 表示通过，2 表示内容不合格或功能尚未实现，
3 表示事实表 FACTS 读不出来。
"""

import argparse
import ast
from collections import Counter
import csv
import hashlib
import importlib.util
import json
import keyword
from pathlib import Path
import re
import sys
import tempfile


SCHEMA_VERSION = 1
GENERATOR_VERSION = 1
SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = SKILL_ROOT / "assets" / "template" / "gen_csv.py"
ACCURACY_TEMPLATE_PATH = SKILL_ROOT / "assets" / "template" / "verify_accuracy.py"
PERFORMANCE_TEMPLATE_PATH = SKILL_ROOT / "assets" / "template" / "verify_performance.py"
README_TEMPLATE_PATH = SKILL_ROOT / "assets" / "template" / "README.md"
COMMON_CODE_PREFIX = b"# ===== \xe9\x80\x9a\xe7\x94\xa8\xe4\xbb\xa3\xe7\xa0\x81\xe5\x8c\xba"
RENDER_CONSTANTS_START = "# ===== 渲染常量区开始 ====="
RENDER_CONSTANTS_END = "# ===== 渲染常量区结束 ====="
ROLES = (
    "handle", "enum", "dim", "layout", "scalar", "inout_scalar",
    "out_scalar", "vector", "fixed_vector", "matrix", "int_array",
)
DTYPES = {
    "float32", "float64", "float16", "bfloat16", "complex64", "complex128",
    "int8", "uint8", "int16", "uint16", "int32", "int64",
}
COMPLEX_DTYPES = {"complex64", "complex128"}
ENUM_KINDS = {"op", "dtype", "compute", "algo"}
DIRECTIONS = {"in", "inout", "out"}
LAYOUT_KINDS = {"ld", "inc", "stride", "batch"}
MATRIX_STORAGE = {
    "full", "upper", "lower", "symmetric", "hermitian", "triangular",
    "banded", "packed", "lu_factorized",
}
PRECISION_ROWS = {"FLOAT32", "FLOAT16", "BFLOAT16"}
VERIFY_TOKENS = {
    "full", "uplo_triangle", "non_uplo_exact", "hermitian_diag", "vector_inc",
    "scalar", "inout_scalars", "index_exact", "solution_residual",
    "lu_reconstruction", "inverse_residual", "qr_reconstruction",
    "orthogonality", "pivot_validity", "info_exact", "batch_each",
}
GOLDEN_KINDS = {"cblas", "lapacke", "loop", "composed"}
BATCH_MODELS = {"ptr_array", "strided", "contiguous_implicit"}
MEMORIES = {"host", "device"}
BATCH_PATTERNS = {"UNIFORM", "NULL_TABLE", "MIXED_SINGULAR"}
NULL_ELEMENT_RE = re.compile(r"^NULL_ELEMENT_\d+$")
FILL_METHODS = {"INDEX", "RANDOM", "VALUE"}
FILL_PATTERNS = {
    "NORM", "UPPER", "LOWER", "DIAG", "ALTER", "EXTREME", "ILLCOND",
    "BANDED",
}
FILL_NUMBER_RE = re.compile(
    r"^(?:N|P)?(?:INF|NAN|ALTER|EXTREME|ILLCOND|"
    r"(?:\d+(?:\.\d*)?|\.\d+)(?:E(?:N|P|[+-])?\d+(?:\.\d*)?)?)$"
)
PRODUCER_RE = re.compile(r"^([A-Za-z_]\w*)\((.*)\)$")
TOKEN_RE = re.compile(r"@@[A-Z0-9_]+@@")
PERF_META_KEYS = (
    "timing_scope", "device", "library", "warmup", "statistic", "source",
)
STATUS_VALUES = (
    "ACLBLAS_STATUS_SUCCESS", "ACLBLAS_STATUS_NOT_INITIALIZED",
    "ACLBLAS_STATUS_ALLOC_FAILED", "ACLBLAS_STATUS_INVALID_VALUE",
    "ACLBLAS_STATUS_MAPPING_ERROR", "ACLBLAS_STATUS_EXECUTION_FAILED",
    "ACLBLAS_STATUS_INTERNAL_ERROR", "ACLBLAS_STATUS_NOT_SUPPORTED",
    "ACLBLAS_STATUS_ARCH_MISMATCH", "ACLBLAS_STATUS_HANDLE_IS_NULLPTR",
    "ACLBLAS_STATUS_INVALID_ENUM", "ACLBLAS_STATUS_UNKNOWN",
)
# csv_loader.h:249-343 的 parse 表；行号以当前 ops-blas 主干为准。
ENUM_VALUES_BY_CTYPE = {
    "aclblasFillMode_t": ("UPPER", "LOWER"),
    "aclblasOperation_t": ("N", "T", "C"),
    "aclblasSideMode_t": ("LEFT", "RIGHT"),
    "aclblasDiagType_t": ("NON_UNIT", "UNIT"),
    "aclDataType": ("FP16", "FP32", "BF16", "INT8"),
    "aclblasComputeType_t": (
        "COMPUTE_16F", "COMPUTE_16F_PEDANTIC", "COMPUTE_32F",
        "COMPUTE_32F_PEDANTIC", "COMPUTE_32F_FAST_16F",
        "COMPUTE_32F_FAST_16BF", "COMPUTE_32F_FAST_TF32", "COMPUTE_64F",
        "COMPUTE_64F_PEDANTIC", "COMPUTE_32I", "COMPUTE_32I_PEDANTIC",
    ),
}
RESIDUAL_VERIFY_TOKENS = {
    "solution_residual", "lu_reconstruction", "inverse_residual",
    "qr_reconstruction", "orthogonality",
}
TOLERANCE_ROWS = {
    "FLOAT16": ("2^-9", "2^-9", "1e-1", 10, -14),
    "BFLOAT16": ("2^-6", "2^-6", "1e-0", 7, -126),
    "FLOAT32": ("2^-10", "2^-16", "1e-2", 23, -126),
}

TOP_KEYS = {
    "schema_version", "generator_version", "op", "family", "symbol", "returns",
    "params", "constraints", "golden", "verify", "edge_cases", "perf",
    "dtype_profiles", "sources", "cases",
}
REQUIRED_TOP_KEYS = {
    "schema_version", "generator_version", "op", "family", "symbol", "returns",
    "params", "golden", "verify", "sources",
}
BASE_PARAM_KEYS = {"name", "ctype", "role"}
ROLE_KEYS = {
    "handle": set(),
    "enum": {"values", "enum_kind"},
    "dim": set(),
    "layout": {"kind", "of"},
    "scalar": {"dtype", "dtype_from", "mem", "nullable", "values"},
    "inout_scalar": {"dtype", "dtype_from", "mem", "nullable", "values"},
    "out_scalar": {"dtype", "dtype_from", "mem", "nullable"},
    "vector": {
        "dtype", "dtype_from", "dir", "len", "inc", "nullable", "batch",
        "producer",
    },
    "fixed_vector": {"dtype", "dtype_from", "dir", "len", "nullable", "samples"},
    "matrix": {
        "dtype", "dtype_from", "dir", "rows", "cols", "ld", "order",
        "storage", "uplo", "diag", "kl", "ku", "conditioning", "nullable",
        "batch", "producer",
    },
    "int_array": {"dtype", "dir", "len", "producer", "nullable"},
}
BUFFER_ROLES = {"vector", "fixed_vector", "matrix", "int_array"}
SCALAR_ROLES = {"scalar", "inout_scalar", "out_scalar"}


class FactsPolicyError(ValueError):
    """表示 FACTS 区违反 AST 白名单。"""


def _err(problems, text):
    """追加一条校验问题。"""
    problems.append(text)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_KEY_INTEGER_RE = re.compile(r"^[+-]?\d+$")


def _normalized_perf_key(value):
    """必须与量具模板及 accept 的 _normalize_key_value 保持同一规则，否则两侧判重口径分叉。"""
    text = str(value).strip()
    return int(text) if _KEY_INTEGER_RE.fullmatch(text) else text


def _is_identifier(value, lowercase=False):
    if not isinstance(value, str) or not value.isidentifier() or keyword.iskeyword(value):
        return False
    return not lowercase or value == value.lower()


def _unknown_keys(problems, where, node, allowed, label="未开放属性"):
    for key in sorted(set(node) - allowed):
        _err(problems, f"{where} {label} {key}")


def _nonempty_string(problems, where, value):
    if not isinstance(value, str) or not value.strip():
        _err(problems, f"{where} 必须是非空字符串")
        return False
    return True


def _string_list(problems, where, value, nonempty=False, unique=False):
    if not isinstance(value, list) or (nonempty and not value):
        suffix = "非空字符串列表" if nonempty else "字符串列表"
        _err(problems, f"{where} 必须是{suffix}")
        return False
    if any(not isinstance(item, str) or not item for item in value):
        _err(problems, f"{where} 必须只含非空字符串")
        return False
    if unique and len(value) != len(set(value)):
        _err(problems, f"{where} 不得有重复值")
        return False
    return True


def _load_python_facts(path):
    source = path.read_text(encoding="utf-8")
    marker = source.find(COMMON_CODE_PREFIX.decode("utf-8"))
    facts_source = source if marker < 0 else source[:marker]
    tree = ast.parse(facts_source, filename=str(path))
    assignment = None
    for index, node in enumerate(tree.body):
        is_docstring = (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        if is_docstring:
            continue
        is_facts = (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "FACTS"
        )
        if not is_facts:
            raise FactsPolicyError(
                f"FACTS 区第 {node.lineno} 行含不允许的语句 "
                f"{type(node).__name__}"
            )
        if assignment is not None:
            raise FactsPolicyError(f"FACTS 区第 {node.lineno} 行重复赋值 FACTS")
        assignment = node.value
    if assignment is None:
        raise ValueError("没有找到顶层 FACTS = {...} 赋值")
    for node in ast.walk(assignment):
        if isinstance(node, ast.Dict):
            literals = [
                key.value for key in node.keys if isinstance(key, ast.Constant)
            ]
            if len(literals) != len(set(literals)):
                raise FactsPolicyError(
                    f"FACTS 字典含重复键（第 {node.lineno} 行附近）"
                )
    value = ast.literal_eval(assignment)
    if not isinstance(value, dict):
        raise ValueError("顶层 FACTS 必须是字典字面量")
    return value


def _valid_fill_tier(value):
    if not isinstance(value, str):
        return False
    parts = value.split("_")
    if not parts or parts[0] not in FILL_METHODS:
        return False
    index = 1
    pattern = "NORM"
    if index < len(parts) and parts[index] in FILL_PATTERNS:
        pattern = parts[index]
        index += 1
    structural = 2 if pattern == "BANDED" else 0
    remaining = parts[index:]
    # fill.h 允许从任一后续位置省略，未给的结构参数和数值沿用默认值。
    if len(remaining) > structural + 2:
        return False
    return all(FILL_NUMBER_RE.fullmatch(item) for item in remaining)


def _load_facts(filename):
    path = Path(filename)
    try:
        if path.suffix == ".py":
            return _load_python_facts(path)
        if path.suffix == ".json":
            with path.open(encoding="utf-8") as handle:
                return json.load(handle)
        raise ValueError("只接受 .py 或 .json 文件")
    except FactsPolicyError:
        raise
    except (OSError, UnicodeError, SyntaxError, ValueError, json.JSONDecodeError) as exc:
        print(f"{filename} 读不出来：{exc}", file=sys.stderr)
        return None


class _ExpressionChecker:
    """按事实表 FACTS 白名单递归检查表达式 AST。"""

    def __init__(self, problems, where, expression, params):
        self.problems = problems
        self.where = where
        self.expression = expression
        self.params = params

    def fail(self, node, detail=None):
        node_type = type(node).__name__
        message = f"{self.where} 的表达式 {self.expression!r} 含不允许的 {node_type}"
        if detail:
            message += f"：{detail}"
        _err(self.problems, message)

    def check(self):
        try:
            node = ast.parse(self.expression, mode="eval").body
        except SyntaxError as exc:
            _err(
                self.problems,
                f"{self.where} 的表达式 {self.expression!r} 语法错误：{exc.msg}",
            )
            return
        self.visit(node)

    def visit(self, node, string_enum=None, buffer_argument=False):
        if isinstance(node, ast.Name):
            self.visit_name(node, buffer_argument)
        elif isinstance(node, ast.Constant):
            self.visit_constant(node, string_enum)
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod)):
                self.fail(node.op)
                return
            self.visit(node.left)
            self.visit(node.right)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.Not)):
                self.fail(node.op)
                return
            self.visit(node.operand)
        elif isinstance(node, ast.Compare):
            if any(not isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE))
                   for op in node.ops):
                bad = next(op for op in node.ops if not isinstance(
                    op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)))
                self.fail(bad)
                return
            operands = [node.left, *node.comparators]
            enum_names = {
                item.id for item in operands
                if isinstance(item, ast.Name)
                and self.params.get(item.id, {}).get("role") == "enum"
            }
            enum_name = next(iter(enum_names), None)
            for item in operands:
                self.visit(item, string_enum=enum_name)
        elif isinstance(node, ast.BoolOp):
            if not isinstance(node.op, (ast.And, ast.Or)):
                self.fail(node.op)
                return
            for value in node.values:
                self.visit(value)
        elif isinstance(node, ast.IfExp):
            self.visit(node.test)
            self.visit(node.body)
            self.visit(node.orelse)
        elif isinstance(node, ast.Call):
            self.visit_call(node)
        else:
            self.fail(node)

    def visit_name(self, node, buffer_argument):
        param = self.params.get(node.id)
        if param is None:
            self.fail(node, f"Name {node.id!r} 不是已声明参数")
            return
        role = param.get("role")
        if role in {"dim", "enum", "layout"}:
            return
        if role in BUFFER_ROLES and buffer_argument:
            return
        if role in BUFFER_ROLES:
            self.fail(node, f"buffer {node.id!r} 只能作 rows/cols/len 的实参")
            return
        self.fail(node, f"参数 {node.id!r} 的 role={role} 不能用于表达式")

    def visit_constant(self, node, string_enum):
        if _is_int(node.value):
            return
        if isinstance(node.value, str) and string_enum is not None:
            values = self.params[string_enum].get("values", [])
            if node.value not in values:
                self.fail(node, f"字符串不在 enum 参数 {string_enum!r} 的 values 中")
            return
        self.fail(node)

    def visit_call(self, node):
        if not isinstance(node.func, ast.Name):
            self.fail(node.func)
            return
        name = node.func.id
        if name not in {"max", "min", "rows", "cols", "len"}:
            self.fail(node, f"函数 {name!r} 不在白名单")
            return
        if node.keywords:
            self.fail(node, "函数调用不允许关键字参数")
            return
        for arg in node.args:
            direct_buffer = name in {"rows", "cols", "len"} and isinstance(arg, ast.Name)
            self.visit(arg, buffer_argument=direct_buffer)


def _check_expression(problems, where, expression, params):
    if not isinstance(expression, str) or not expression.strip():
        _err(problems, f"{where} 必须是非空表达式字符串")
        return
    _ExpressionChecker(problems, where, expression, params).check()


def _check_dtype_choice(problems, where, param):
    has_dtype = "dtype" in param
    has_from = "dtype_from" in param
    if has_dtype == has_from:
        _err(problems, f"{where} 的 dtype 与 dtype_from 必须且只能出现一个")
        return
    if has_dtype and param["dtype"] not in DTYPES:
        _err(problems, f"{where} 的 dtype={param['dtype']!r} 不在 dtype 词表")
    if has_from and not _is_identifier(param["dtype_from"]):
        _err(problems, f"{where} 的 dtype_from 必须是参数名")


def _scalar_value_matches(value, dtype):
    if dtype in COMPLEX_DTYPES:
        return (
            isinstance(value, list)
            and len(value) == 2
            and all(_is_number(item) for item in value)
        )
    return _is_number(value)


def _check_scalar_values(problems, where, param):
    if "values" not in param:
        return
    values = param["values"]
    if not isinstance(values, list) or not values:
        _err(problems, f"{where}.values 必须是非空列表")
        return
    if "dtype" not in param:
        return
    for index, value in enumerate(values):
        if not _scalar_value_matches(value, param["dtype"]):
            _err(problems, f"{where}.values[{index}] 与 dtype={param['dtype']} 不匹配")


def _check_fixed_vector_samples(problems, where, param):
    direction = param.get("dir")
    if direction == "out":
        if "samples" in param:
            _err(problems, f"{where} 的 dir=out 时禁止出现 samples")
        return
    if direction not in {"in", "inout"}:
        return
    if "samples" not in param:
        _err(problems, f"{where} 的 dir={direction} 时 samples 必填")
        return
    samples = param["samples"]
    if not isinstance(samples, list) or not samples:
        _err(problems, f"{where}.samples 必须是非空列表")
        return
    length = param.get("len")
    for index, sample in enumerate(samples):
        sample_where = f"{where}.samples[{index}]"
        if not isinstance(sample, list) or len(sample) != length:
            _err(problems, f"{sample_where} 必须是长度等于 len 的列表")
        elif any(not _is_number(value) for value in sample):
            _err(problems, f"{sample_where} 必须只含 int 或 float")


def _check_nullable(problems, where, param):
    if "nullable" in param and not isinstance(param["nullable"], bool):
        _err(problems, f"{where} 的 nullable 必须是 bool")


def _check_direction(problems, where, param, required=False):
    if required and "dir" not in param:
        _err(problems, f"{where} 缺 dir")
        return
    direction = param.get("dir", "in")
    if direction not in DIRECTIONS:
        _err(problems, f"{where} 的 dir={direction!r} 不合法，只能是 in/inout/out")


def _check_producer_syntax(problems, where, producer):
    if not _nonempty_string(problems, where, producer):
        return
    match = PRODUCER_RE.fullmatch(producer)
    if not match:
        _err(problems, f"{where} 必须形如 lapacke_func(A)")
        return
    arguments = match.group(2).strip()
    if not arguments:
        return
    if any(not _is_identifier(item.strip()) for item in arguments.split(",")):
        _err(problems, f"{where} 的括号内只能列参数名")


def _check_batch_shape(problems, where, batch):
    if not isinstance(batch, dict):
        _err(problems, f"{where} 必须是字典")
        return
    allowed = {"model", "count", "stride", "table_mem", "element_mem"}
    _unknown_keys(problems, where, batch, allowed)
    model = batch.get("model")
    if model not in BATCH_MODELS:
        _err(problems, f"{where}.model={model!r} 不合法")
    if not _is_identifier(batch.get("count")):
        _err(problems, f"{where}.count 必须是参数名")
    if model == "strided" and not _is_identifier(batch.get("stride")):
        _err(problems, f"{where} 在 model=strided 时必须给 stride 参数名")
    if model != "strided" and "stride" in batch:
        _err(problems, f"{where} 只在 model=strided 时允许 stride")
    if model != "ptr_array" and "table_mem" in batch:
        _err(problems, f"{where} 只在 model=ptr_array 时允许 table_mem")
    for field in ("table_mem", "element_mem"):
        if field in batch and batch[field] not in MEMORIES:
            _err(problems, f"{where}.{field}={batch[field]!r} 不合法，只能是 host/device")


def _check_param_shape(problems, index, param):
    if not isinstance(param, dict):
        _err(problems, f"params[{index}] 必须是字典")
        return
    where = f"params[{index}]({param.get('name', '?')})"
    name = param.get("name")
    if not _is_identifier(name):
        _err(problems, f"{where} 的 name 必须是标识符")
    if not isinstance(param.get("ctype"), str) or not param.get("ctype", "").strip():
        _err(problems, f"{where} 的 ctype 必须是非空字符串")
    role = param.get("role")
    if role not in ROLES:
        _err(problems, f"{where} 的 role={role!r} 不合法，只能是 {'/'.join(ROLES)}")
        return
    for key in sorted(set(param) - BASE_PARAM_KEYS - ROLE_KEYS[role]):
        _err(problems, f"{where} role={role} 未开放属性 {key}")

    if role == "enum":
        values = param.get("values")
        values_valid = _string_list(problems, f"{where}.values", values, True, True)
        enum_kind = param.get("enum_kind", "op")
        if enum_kind not in ENUM_KINDS:
            _err(problems, f"{where} 的 enum_kind={param.get('enum_kind')!r} 不合法")
        ctype = param.get("ctype")
        allowed_values = ENUM_VALUES_BY_CTYPE.get(ctype)
        if values_valid and allowed_values is not None:
            for value in values:
                if value not in allowed_values:
                    allowed = "/".join(allowed_values)
                    _err(
                        problems,
                        f"{where}.values 含 {value!r}，{ctype} 只接受 {allowed}"
                        "（ACL_FLOAT 写成 FP32，ACLBLAS_UPPER/U 写成 UPPER）",
                    )
        elif values_valid:
            for value in values:
                if value in {"U", "L"} or value.startswith(("ACL_", "ACLBLAS_")):
                    _err(
                        problems,
                        f"{where}.values 含 {value!r}，enum 不接受 ACLBLAS_/ACL_ 全名或"
                        " U/L 缩写（ACL_FLOAT 写成 FP32，ACLBLAS_UPPER/U 写成 UPPER）",
                    )
        if enum_kind == "dtype" and ctype != "aclDataType":
            _err(problems, f"{where} 的 enum_kind=dtype 时 ctype 必须是 aclDataType")
        legacy_execution_type = name == "executionType" and ctype == "aclDataType"
        if (
            enum_kind == "compute"
            and ctype != "aclblasComputeType_t"
            and not legacy_execution_type
        ):
            _err(
                problems,
                f"{where} 的 enum_kind=compute 时 ctype 必须是 aclblasComputeType_t；"
                "只有任务书声明为 aclDataType 的 executionType 兼容该 ctype",
            )
    elif role == "layout":
        if param.get("kind") not in LAYOUT_KINDS:
            _err(problems, f"{where} 的 kind={param.get('kind')!r} 不合法")
        if param.get("kind") != "batch" and "of" not in param:
            _err(problems, f"{where} 的 kind={param.get('kind')} 必须给 of")
        if "of" in param:
            refs = param["of"] if isinstance(param["of"], list) else [param["of"]]
            if not refs or any(not _is_identifier(item) for item in refs):
                _err(problems, f"{where}.of 必须是参数名或非空参数名列表")
            elif len(refs) != len(set(refs)):
                _err(problems, f"{where}.of 不得重复参数名")
    elif role in SCALAR_ROLES:
        _check_dtype_choice(problems, where, param)
        if param.get("mem", "device") not in MEMORIES:
            _err(
                problems,
                f"{where} 的 mem={param.get('mem')!r} 不合法，只能是 host/device",
            )
        _check_nullable(problems, where, param)
        if role in {"scalar", "inout_scalar"}:
            _check_scalar_values(problems, where, param)
    elif role == "vector":
        _check_dtype_choice(problems, where, param)
        _check_direction(problems, where, param)
        if "len" not in param:
            _err(problems, f"{where} 缺 len")
        inc = param.get("inc")
        if not (_is_identifier(inc) or (_is_int(inc) and inc == 1)):
            _err(problems, f"{where}.inc 必须是 layout 参数名或整数字面量 1")
        _check_nullable(problems, where, param)
    elif role == "fixed_vector":
        _check_dtype_choice(problems, where, param)
        _check_direction(problems, where, param, required=True)
        if not _is_int(param.get("len")) or param.get("len", 0) <= 0:
            _err(problems, f"{where}.len 必须是正整数字面量")
        _check_nullable(problems, where, param)
        _check_fixed_vector_samples(problems, where, param)
    elif role == "matrix":
        _check_dtype_choice(problems, where, param)
        _check_direction(problems, where, param)
        if "rows" not in param:
            _err(problems, f"{where} 缺 rows")
        if "cols" not in param:
            _err(problems, f"{where} 缺 cols")
        storage = param.get("storage", "full")
        if storage not in MATRIX_STORAGE:
            _err(problems, f"{where} 的 storage={storage!r} 不合法")
        if storage == "packed" and "ld" in param:
            _err(problems, f"{where} 的 storage=packed 时不得有 ld")
        if storage != "packed" and not _is_identifier(param.get("ld")):
            _err(problems, f"{where}.ld 必须是 layout 参数名")
        if param.get("order", "col_major") not in {"col_major", "row_major"}:
            _err(problems, f"{where} 的 order 只能是 col_major/row_major")
        if storage in {"symmetric", "hermitian", "triangular"} and "uplo" not in param:
            _err(problems, f"{where} 的 storage={storage} 时 uplo 必填")
        if storage == "triangular" and "diag" not in param:
            _err(problems, f"{where} 的 storage=triangular 时 diag 必填")
        if storage == "banded":
            for field in ("kl", "ku"):
                if field not in param:
                    _err(problems, f"{where} 的 storage=banded 时 {field} 必填")
        if "conditioning" in param:
            _string_list(problems, f"{where}.conditioning", param["conditioning"])
        _check_nullable(problems, where, param)
    elif role == "int_array":
        if param.get("dtype", "int32") not in DTYPES:
            _err(problems, f"{where} 的 dtype={param.get('dtype')!r} 不在 dtype 词表")
        _check_direction(problems, where, param, required=True)
        if "len" not in param:
            _err(problems, f"{where} 缺 len")
        _check_nullable(problems, where, param)
        if param.get("dir") in {"in", "inout"} and "producer" not in param:
            _err(problems, f"{where} 的 dir={param.get('dir')} 时 producer 必填")

    if role in {"vector", "matrix"}:
        if "batch" in param:
            _check_batch_shape(problems, f"{where}.batch", param["batch"])
    if role in {"vector", "matrix", "int_array"} and "producer" in param:
        _check_producer_syntax(problems, f"{where}.producer", param["producer"])


def _param_map(problems, params):
    result = {}
    for index, param in enumerate(params):
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        if not _is_identifier(name):
            continue
        if name in result:
            _err(problems, f"params[{index}]({name}) 的 name 重复")
        else:
            result[name] = param
    return result


def _layout_refs(param):
    value = param.get("of", [])
    return value if isinstance(value, list) else [value]


def _require_layout(problems, where, params, name, kind, owner, optional_literal=False):
    if optional_literal and _is_int(name) and name == 1:
        return
    layout = params.get(name)
    if layout is None or layout.get("role") != "layout" or layout.get("kind") != kind:
        _err(problems, f"{where}={name!r} 必须引用 kind={kind} 的 layout 参数")
        return
    if kind != "batch" or "of" in layout:
        if owner not in _layout_refs(layout):
            _err(problems, f"{where} 引用 {name!r}，但其 layout.of 未反向包含 {owner!r}")


def _check_cross_references(problems, params, ordered):
    for name, param in ordered:
        role = param.get("role")
        where = f"params[{param['_index']}]({name})"
        if "dtype_from" in param:
            source_name = param["dtype_from"]
            source = params.get(source_name) if isinstance(source_name, str) else None
            source_kind = source.get("enum_kind", "op") if source else None
            source_is_valid = (
                source is not None
                and source.get("role") == "enum"
                and (
                    source_kind == "dtype"
                    or (role in SCALAR_ROLES and source_kind == "compute")
                )
            )
            if not source_is_valid:
                _err(
                    problems,
                    f"{where}.dtype_from 必须引用 enum_kind=dtype 的 enum 参数"
                    "（标量角色也可引用 compute）",
                )
        if role == "vector":
            _require_layout(
                problems, f"{where}.inc", params, param.get("inc"), "inc", name, True)
        if role == "matrix" and param.get("storage", "full") != "packed":
            _require_layout(problems, f"{where}.ld", params, param.get("ld"), "ld", name)
        if role in {"vector", "matrix"} and isinstance(param.get("batch"), dict):
            batch = param["batch"]
            count = batch.get("count")
            count_param = params.get(count)
            if count_param is None or not (
                count_param.get("role") == "dim"
                or (count_param.get("role") == "layout"
                    and count_param.get("kind") == "batch")
            ):
                _err(
                    problems,
                    f"{where}.batch.count={count!r} 必须引用 dim 或 kind=batch 的 layout",
                )
            elif count_param.get("role") == "layout":
                _require_layout(
                    problems, f"{where}.batch.count", params, count, "batch", name)
            if batch.get("model") == "strided":
                _require_layout(
                    problems, f"{where}.batch.stride", params, batch.get("stride"),
                    "stride", name)
        if role == "matrix":
            for field in ("uplo", "diag"):
                if field in param:
                    target = params.get(param[field])
                    if target is None or target.get("role") != "enum":
                        _err(problems, f"{where}.{field} 必须引用 enum 参数")
            for field in ("kl", "ku"):
                if field in param:
                    target = params.get(param[field])
                    if target is None or target.get("role") != "dim":
                        _err(problems, f"{where}.{field} 必须引用 dim 参数")
        if role in {"vector", "matrix", "int_array"} and "producer" in param:
            match = PRODUCER_RE.fullmatch(str(param["producer"]))
            if match and match.group(2).strip():
                for item in match.group(2).split(","):
                    ref = item.strip()
                    if _is_identifier(ref) and ref not in params:
                        _err(problems, f"{where}.producer 引用了未知参数 {ref!r}")

    reverse_fields = {"ld": "ld", "inc": "inc", "stride": "stride", "batch": "count"}
    for layout_name, layout in ordered:
        if layout.get("role") != "layout" or "of" not in layout:
            continue
        kind = layout.get("kind")
        field = reverse_fields.get(kind)
        for target_name in _layout_refs(layout):
            target = params.get(target_name)
            where = f"params[{layout['_index']}]({layout_name}).of"
            if target is None or target.get("role") not in BUFFER_ROLES:
                _err(problems, f"{where} 引用了非 buffer 参数 {target_name!r}")
                continue
            if kind in {"ld", "inc"}:
                reverse = target.get(field)
            else:
                batch = target.get("batch", {})
                reverse = batch.get(field) if isinstance(batch, dict) else None
            if reverse != layout_name:
                _err(
                    problems,
                    f"{where} 含 {target_name!r}，但该参数未反向引用 {layout_name!r}",
                )


def _check_param_expressions(problems, params, ordered, constraints):
    for name, param in ordered:
        where = f"params[{param['_index']}]({name})"
        role = param.get("role")
        if role == "vector" and "len" in param:
            _check_expression(problems, f"{where}.len", param["len"], params)
        elif role == "matrix":
            for field in ("rows", "cols"):
                if field in param:
                    _check_expression(problems, f"{where}.{field}", param[field], params)
        elif role == "int_array" and "len" in param:
            _check_expression(problems, f"{where}.len", param["len"], params)
    for index, expression in enumerate(constraints):
        _check_expression(problems, f"constraints[{index}]", expression, params)


def _is_output(param):
    role = param.get("role")
    if role in {"out_scalar", "inout_scalar"}:
        return True
    return role in BUFFER_ROLES and param.get("dir", "in") in {"out", "inout"}


def _check_profiles(problems, facts, params, dtype_sources):
    present = "dtype_profiles" in facts
    if dtype_sources and not present:
        _err(problems, "存在 dtype_from 时 dtype_profiles 必填")
        return
    if not dtype_sources and present:
        _err(problems, "没有参数使用 dtype_from 时不得出现 dtype_profiles")
        return
    if not present:
        return
    profiles = facts["dtype_profiles"]
    if not isinstance(profiles, list) or not profiles:
        _err(problems, "dtype_profiles 必须是非空列表")
        return
    names = set()
    allowed = {"name", "assign", "scalar_dtype", "golden_dtype", "precision_row"}
    required = {"name", "assign", "golden_dtype", "precision_row"}
    if dtype_sources:
        required.add("scalar_dtype")
    for index, profile in enumerate(profiles):
        where = f"dtype_profiles[{index}]"
        if not isinstance(profile, dict):
            _err(problems, f"{where} 必须是字典")
            continue
        _unknown_keys(problems, where, profile, allowed)
        for field in sorted(required - set(profile)):
            _err(problems, f"{where} 缺 {field}")
        name = profile.get("name")
        if not _is_identifier(name):
            _err(problems, f"{where}.name 必须是标识符")
        elif name in names:
            _err(problems, f"{where}.name={name!r} 重复")
        else:
            names.add(name)
        for field in ("scalar_dtype", "golden_dtype"):
            if profile.get(field) not in DTYPES:
                _err(problems, f"{where}.{field}={profile.get(field)!r} 不在 dtype 词表")
        if profile.get("precision_row") not in PRECISION_ROWS:
            _err(problems, f"{where}.precision_row={profile.get('precision_row')!r} 不合法")
        assign = profile.get("assign")
        if not isinstance(assign, dict):
            _err(problems, f"{where}.assign 必须是字典")
            continue
        for source in sorted(dtype_sources - set(assign)):
            _err(problems, f"{where}.assign 未覆盖 dtype_from 参数 {source!r}")
        for enum_name, value in assign.items():
            enum = params.get(enum_name)
            if (
                enum is None
                or enum.get("role") != "enum"
                or enum.get("enum_kind", "op") not in {"dtype", "compute"}
            ):
                _err(problems, f"{where}.assign 的键 {enum_name!r} 不是可赋值 enum 参数")
            elif value not in enum.get("values", []):
                _err(problems, f"{where}.assign[{enum_name!r}]={value!r} 不在 enum.values")
    for param_name, param in params.items():
        if param.get("role") not in {"scalar", "inout_scalar"}:
            continue
        if "values" not in param or "dtype_from" not in param:
            continue
        for profile_index, profile in enumerate(profiles):
            dtype = profile.get("scalar_dtype")
            if dtype not in DTYPES:
                continue
            for value_index, value in enumerate(param["values"]):
                if not _scalar_value_matches(value, dtype):
                    _err(
                        problems,
                        f"params({param_name}).values[{value_index}] 与 "
                        f"dtype_profiles[{profile_index}].scalar_dtype={dtype} 不匹配",
                    )


def _check_golden(problems, golden):
    if not isinstance(golden, dict):
        _err(problems, "golden 必须是字典")
        return
    _unknown_keys(problems, "golden", golden, {"kind", "symbol", "formula"})
    kind = golden.get("kind")
    if kind not in GOLDEN_KINDS:
        _err(problems, f"golden.kind={kind!r} 不合法")
    elif kind in {"cblas", "lapacke", "loop"}:
        _nonempty_string(problems, "golden.symbol", golden.get("symbol"))
    elif kind == "composed":
        _nonempty_string(problems, "golden.formula", golden.get("formula"))


def _check_verify(problems, verify):
    if not isinstance(verify, list) or not verify:
        _err(problems, "verify 必须是非空列表")
        return
    if len(verify) != len(set(map(repr, verify))):
        _err(problems, "verify 不得重复")
    for index, token in enumerate(verify):
        if token not in VERIFY_TOKENS:
            _err(problems, f"verify[{index}]={token!r} 不在 verify 词表")


def _control_columns(params):
    direct = {}
    batch = {}
    for name, param in params.items():
        if param.get("nullable", False):
            direct[f"null{name[:1].upper()}{name[1:]}"] = name
        if "batch" in param:
            batch[f"{name}_batch_pattern"] = name
    return direct, batch


def _fixed_vector_element(key, params):
    if not isinstance(key, str):
        return None
    matches = []
    for name, param in params.items():
        if param.get("role") != "fixed_vector":
            continue
        if param.get("dir") not in {"in", "inout"}:
            continue
        length = param.get("len")
        if not _is_int(length) or length <= 0 or not key.startswith(name):
            continue
        suffix = key[len(name):]
        if not re.fullmatch(r"[0-9]+", suffix):
            continue
        element = int(suffix)
        if suffix != str(element):
            continue
        matches.append((len(name), param, element, length))
    if not matches:
        return None
    _, param, element, length = max(matches, key=lambda item: item[0])
    return param, element, length


def _check_edge_cases(problems, edge_cases, params):
    if not isinstance(edge_cases, list):
        _err(problems, "edge_cases 必须是列表")
        return
    direct_roles = {"enum", "dim", "layout", "scalar", "inout_scalar"}
    direct = {name for name, param in params.items() if param.get("role") in direct_roles}
    null_columns, batch_columns = _control_columns(params)
    names = set()
    for index, case in enumerate(edge_cases):
        where = f"edge_cases[{index}]"
        if not isinstance(case, dict):
            _err(problems, f"{where} 必须是字典")
            continue
        _unknown_keys(problems, where, case, {"name", "set", "expect", "source"})
        for field in ("name", "set", "expect", "source"):
            if field not in case:
                _err(problems, f"{where} 缺 {field}")
        name = case.get("name")
        if not _is_identifier(name):
            _err(problems, f"{where}.name 必须是标识符")
        elif name in names:
            _err(problems, f"{where}.name={name!r} 重复")
        else:
            names.add(name)
        if case.get("expect") not in STATUS_VALUES:
            _err(problems, f"{where}.expect={case.get('expect')!r} 不在状态码词表")
        _nonempty_string(problems, f"{where}.source", case.get("source"))
        settings = case.get("set")
        if not isinstance(settings, dict):
            _err(problems, f"{where}.set 必须是字典")
            continue
        for key, value in settings.items():
            if key in direct:
                param = params[key]
                role = param.get("role")
                if role == "enum" and value not in param.get("values", []):
                    _err(problems, f"{where}.set[{key!r}] 不在该 enum 的声明 values 中")
                elif role in {"layout", "dim"} and not _is_int(value):
                    _err(problems, f"{where}.set[{key!r}] 必须是整数")
                elif role in {"scalar", "inout_scalar"} and not _scalar_value_matches(
                    value, param.get("dtype")
                ):
                    _err(problems, f"{where}.set[{key!r}] 与标量取值类型不符")
                continue
            if key in null_columns:
                if value not in {0, 1, False, True}:
                    _err(problems, f"{where}.set[{key!r}] 必须是 0 或 1")
                continue
            if key in batch_columns:
                valid = value in BATCH_PATTERNS or (
                    isinstance(value, str) and NULL_ELEMENT_RE.fullmatch(value))
                if not valid:
                    _err(problems, f"{where}.set[{key!r}] 的 batch pattern 不合法")
                continue
            fixed_element = _fixed_vector_element(key, params)
            if fixed_element is not None:
                param, element, length = fixed_element
                if element >= length:
                    _err(problems, f"{where}.set 键 {key!r} 越界（len={length}）")
                elif param.get("dtype") in COMPLEX_DTYPES:
                    _err(problems, f"{where}.set 不支持复数 fixed_vector 元素 {key!r}")
                elif not _is_number(value):
                    _err(problems, f"{where}.set[{key!r}] 必须是 int 或 float")
                continue
            _err(problems, f"{where}.set 含未知键 {key!r}")


def _check_perf(problems, facts, perf, params):
    if not isinstance(perf, dict):
        _err(problems, "perf 必须是字典")
        return
    _unknown_keys(
        problems,
        "perf",
        perf,
        {"key", "rows", "sweep", "meta", "threshold"},
    )
    keys = perf.get("key")
    if not isinstance(keys, list) or not keys:
        _err(problems, "perf.key 必须是非空参数名列表")
        keys = []
    elif any(not _is_identifier(item) for item in keys):
        _err(problems, "perf.key 必须只含参数名")
    elif len(keys) != len(set(keys)):
        _err(problems, "perf.key 不得重复")
    profile_names = {
        profile.get("name")
        for profile in facts.get("dtype_profiles", [])
        if isinstance(profile, dict)
    }
    for name in keys:
        if name == "profile":
            if not profile_names:
                _err(problems, "perf.key 含 'profile'，但 dtype_profiles 不存在")
            continue
        param = params.get(name)
        if param is None or param.get("role") not in {"enum", "dim", "layout"}:
            _err(problems, f"perf.key 的 {name!r} 不是 enum/dim/layout 参数")
        elif param.get("enum_kind", "op") in {"dtype", "compute"}:
            _err(problems, f"perf.key 的类型参数 {name!r} 必须改用虚拟键 'profile'")
    rows = perf.get("rows")
    if not isinstance(rows, list):
        _err(problems, "perf.rows 必须是列表")
        rows = []
    seen_rows = {}
    for index, row in enumerate(rows):
        where = f"perf.rows[{index}]"
        if not isinstance(row, dict):
            _err(problems, f"{where} 必须是字典")
            continue
        if isinstance(keys, list) and keys:
            ident = tuple(_normalized_perf_key(row.get(key)) for key in keys)
            if ident in seen_rows:
                _err(problems, f"{where} 与 perf.rows[{seen_rows[ident]}] 性能键重复")
            else:
                seen_rows[ident] = index
        _unknown_keys(problems, where, row, set(keys) | {"gpu_ms"})
        for key in keys:
            if key not in row:
                _err(problems, f"{where} 缺 key 参数 {key!r}")
            elif key == "profile" and row[key] not in profile_names:
                _err(problems, f"{where}.profile={row[key]!r} 不在 dtype_profiles 中")
            elif key != "profile":
                param = params.get(key, {})
                if param.get("role") == "enum" and row[key] not in param.get("values", []):
                    _err(problems, f"{where}.{key} 不在 enum.values 中")
                elif param.get("role") in {"dim", "layout"} and not _is_int(row[key]):
                    _err(problems, f"{where}.{key} 必须是整数")
        if "gpu_ms" in row and not _is_number(row["gpu_ms"]):
            _err(problems, f"{where}.gpu_ms 必须是数值")
    if "sweep" in perf and not isinstance(perf["sweep"], bool):
        _err(problems, "perf.sweep 必须是 bool")
    if "threshold" in perf:
        threshold = perf["threshold"]
        if not _is_number(threshold) or threshold <= 0:
            _err(problems, "perf.threshold 必须是大于 0 的数值")
    if "meta" in perf:
        meta = perf["meta"]
        if not isinstance(meta, dict):
            _err(problems, "perf.meta 必须是字典")
        else:
            _unknown_keys(problems, "perf.meta", meta, set(PERF_META_KEYS))
            for key, value in meta.items():
                if not isinstance(value, str):
                    _err(problems, f"perf.meta.{key} 必须是字符串")


def _check_cases(problems, cases):
    if not isinstance(cases, dict):
        _err(problems, "cases 必须是字典")
        return
    allowed = {
        "dim_tiers", "vec_dim_tiers", "batch_tiers", "inc_tiers",
        "fill_tiers", "max_footprint_bytes",
    }
    _unknown_keys(problems, "cases", cases, allowed)
    for field in ("dim_tiers", "vec_dim_tiers", "batch_tiers"):
        if field not in cases:
            continue
        values = cases[field]
        if (
            not isinstance(values, list)
            or not values
            or any(not _is_int(value) or value <= 0 for value in values)
        ):
            _err(problems, f"cases.{field} 必须是非空正整数列表")
    if "inc_tiers" in cases:
        values = cases["inc_tiers"]
        if (
            not isinstance(values, list)
            or not values
            or any(not _is_int(value) or value == 0 for value in values)
        ):
            _err(problems, "cases.inc_tiers 必须是非空、非零整数列表")
    if "fill_tiers" in cases:
        values = cases["fill_tiers"]
        if (
            not isinstance(values, list)
            or not values
            or any(not _valid_fill_tier(value)
                   for value in values)
        ):
            _err(problems, "cases.fill_tiers 含不符合 METHOD_PATTERN_VAL 的值")
    if "max_footprint_bytes" in cases:
        value = cases["max_footprint_bytes"]
        if not _is_int(value) or value <= 0:
            _err(problems, "cases.max_footprint_bytes 必须是正整数")


def _find_placeholders(value):
    if isinstance(value, str):
        return [value] if re.fullmatch(r"<[^<>]+>", value) else []
    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(_find_placeholders(item))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_find_placeholders(item))
        return result
    return []


def _check_top(problems, facts):
    if not isinstance(facts, dict):
        _err(problems, "事实表 FACTS 必须是字典")
        return {}, []
    for key in sorted(set(facts) - TOP_KEYS):
        _err(problems, f"未开放的顶层键 {key}")
    for key in sorted(REQUIRED_TOP_KEYS - set(facts)):
        _err(problems, f"顶层 {key} 缺失")
    if not _is_int(facts.get("schema_version")) or facts.get("schema_version") != SCHEMA_VERSION:
        _err(problems, "schema_version 当前只接受整数 1")
    if (
        not _is_int(facts.get("generator_version"))
        or facts.get("generator_version") != GENERATOR_VERSION
    ):
        _err(problems, f"generator_version 必须等于模板版本 {GENERATOR_VERSION}")
    if not _is_identifier(facts.get("op"), lowercase=True):
        _err(problems, "op 必须是小写标识符")
    for field in ("family", "symbol"):
        if not _is_identifier(facts.get(field)):
            _err(problems, f"{field} 必须是标识符")
    _nonempty_string(problems, "returns", facts.get("returns"))

    raw_params = facts.get("params")
    if not isinstance(raw_params, list) or not raw_params:
        _err(problems, "params 必须是非空有序列表")
        raw_params = []
    for index, param in enumerate(raw_params):
        _check_param_shape(problems, index, param)
    params = _param_map(problems, raw_params)
    ordered = []
    for index, param in enumerate(raw_params):
        if isinstance(param, dict) and param.get("name") in params:
            copy = dict(param)
            copy["_index"] = index
            ordered.append((param["name"], copy))
    first = raw_params[0] if raw_params else None
    if (
        not isinstance(first, dict)
        or first.get("role") != "handle"
        or first.get("ctype") != "aclblasHandle_t"
    ):
        _err(
            problems,
            "params[0] 必须是 role=handle 的 aclblasHandle_t 参数"
            "（aclblas 全部接口首参为 handle）",
        )
    handles = [(name, param) for name, param in ordered if param.get("role") == "handle"]
    if len(handles) > 1:
        _err(problems, "handle 参数至多一个")
    if ordered and not any(_is_output(param) for _, param in ordered):
        _err(problems, "params 至少要有一个输出或 inout 参数")
    return params, ordered


def validate(facts):
    problems = []
    params, ordered = _check_top(problems, facts)
    if not isinstance(facts, dict):
        return problems
    for placeholder in sorted(set(_find_placeholders(facts))):
        _err(problems, f"FACTS 仍含占位符 {placeholder!r}")
    constraints = facts.get("constraints", [])
    if not isinstance(constraints, list):
        _err(problems, "constraints 必须是表达式字符串列表")
        constraints = []
    _check_cross_references(problems, params, ordered)
    _check_param_expressions(problems, params, ordered, constraints)
    dtype_sources = {
        param["dtype_from"] for _, param in ordered if "dtype_from" in param
        and isinstance(param["dtype_from"], str)
    }
    _check_profiles(problems, facts, params, dtype_sources)
    _check_golden(problems, facts.get("golden"))
    _check_verify(problems, facts.get("verify"))
    _check_edge_cases(problems, facts.get("edge_cases", []), params)
    if "perf" in facts:
        _check_perf(problems, facts, facts["perf"], params)
    if "cases" in facts:
        _check_cases(problems, facts["cases"])
    sources = facts.get("sources")
    if not isinstance(sources, dict) or "params" not in sources:
        _err(problems, "sources 必须是至少含 params 的字典")
    elif any(not isinstance(value, str) or not value.strip() for value in sources.values()):
        _err(problems, "sources 的值必须都是非空字符串")
    return problems


def _header_columns(facts):
    profiles = facts.get("dtype_profiles", [])
    profile_has_complex = any(
        profile.get("scalar_dtype") in COMPLEX_DTYPES for profile in profiles)
    columns = ["case_name", "description"]
    for param in facts["params"]:
        name = param["name"]
        role = param["role"]
        direction = param.get("dir", "in")
        if role in {"handle", "out_scalar", "int_array"}:
            continue
        if role in {"enum", "dim", "layout"}:
            columns.append(name)
        elif role in SCALAR_ROLES:
            if param.get("dtype") in COMPLEX_DTYPES:
                columns.extend([f"{name}_re", f"{name}_im"])
            elif "dtype_from" in param and profile_has_complex:
                columns.extend([f"{name}_re", f"{name}_im"])
            else:
                columns.append(name)
        elif role in {"vector", "matrix"}:
            if direction in {"in", "inout"} and "producer" not in param:
                # fill 列用小写参数名（a_fill），与社区旧任务包和开发者 param.h 的读法一致。
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


def _print_summary(facts):
    params = facts["params"]
    counts = Counter(param["role"] for param in params)
    roles = "、".join(f"{role}={counts[role]}" for role in ROLES if counts[role])
    outputs = [param["name"] for param in params if _is_output(param)]
    print(f"算子        {facts['op']}")
    print(f"符号        {facts['symbol']}")
    print(f"参数        {len(params)} 个")
    print(f"角色分布    {roles}")
    print(f"输出参数    {'、'.join(outputs)}")
    print(f"profiles    {len(facts.get('dtype_profiles', []))} 个")
    print(f"edge_cases  {len(facts.get('edge_cases', []))} 个")
    print(f"perf        {'有' if 'perf' in facts else '无'}")


def _common_code(path):
    data = Path(path).read_bytes()
    index = data.find(COMMON_CODE_PREFIX)
    return None if index < 0 else data[index:]


def _common_code_hash(path):
    common = _common_code(path)
    return None if common is None else hashlib.sha256(common).hexdigest()


def _check_source_integrity(problems, path):
    try:
        candidate_hash = _common_code_hash(path)
        if candidate_hash is None:
            return False
        if candidate_hash != _common_code_hash(TEMPLATE_PATH):
            _err(problems, "通用代码区已被修改，请从模板重新复制")
        return True
    except OSError as exc:
        _err(problems, f"通用代码区读取失败：{exc}")
        return False


def _load_generator():
    module_spec = importlib.util.spec_from_file_location(
        "_repo_task_blas_case_generator", TEMPLATE_PATH
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError("无法加载 skill 自带的生成模板")
    module = importlib.util.module_from_spec(module_spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        module_spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module



def _render_derived(target_dir, facts, generator, generated):
    """把五件派生物渲染进 target_dir。gen_csv.py 是输入，不在此渲染。"""
    target = Path(target_dir)
    csv_path = target / f"{facts['op']}_test.csv"
    generator.write_csv(csv_path, generated["header"], generated["rows"])
    csv_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    _render_script(
        ACCURACY_TEMPLATE_PATH, target / "verify_accuracy.py", facts, csv_hash
    )
    _render_script(
        PERFORMANCE_TEMPLATE_PATH, target / "verify_performance.py", facts, csv_hash
    )
    _render_gpu_baseline(target / "gpu_baseline.csv", facts)
    _render_readme(target / "README.md", facts, generator, generated)


def _package_names(facts):
    return (
        "gen_csv.py",
        f"{facts['op']}_test.csv",
        "verify_accuracy.py",
        "verify_performance.py",
        "README.md",
        "gpu_baseline.csv",
    )


def _md_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def _markdown_table(headers, rows):
    lines = [
        "| " + " | ".join(_md_cell(item) for item in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(item) for item in row) + " |")
    return "\n".join(lines)


def _source_table(facts):
    rows = [
        ("symbol", facts["symbol"]),
        ("returns", facts["returns"]),
        ("family", facts["family"]),
        ("schema_version", facts["schema_version"]),
        ("generator_version", facts["generator_version"]),
    ]
    rows.extend((f"sources.{key}", value) for key, value in facts["sources"].items())
    return _markdown_table(("项目", "值"), rows)


def _signature(facts):
    def declaration(param):
        ctype = re.sub(r"\s*\*\s*", "*", param["ctype"].strip())
        ctype = ctype.replace("*const", "* const")
        return f"{ctype} {param['name']}"

    arguments = [
        declaration(param) for param in facts["params"]
    ]
    signature = f"{facts['returns']} {facts['symbol']}({', '.join(arguments)});"
    if len(signature) <= 100:
        return signature
    joined = ",\n    ".join(arguments)
    return f"{facts['returns']} {facts['symbol']}(\n    {joined}\n);"


def _six_files_table(facts):
    descriptions = {
        "gen_csv.py": "事实表 FACTS 与独立 CSV 生成器",
        f"{facts['op']}_test.csv": "CSV 驱动用例",
        "verify_accuracy.py": "精度验收入口",
        "verify_performance.py": "msprof kernel 性能验收入口",
        "README.md": "开发与验收契约",
        "gpu_baseline.csv": "GPU 性能基线与元数据",
    }
    rows = [(name, descriptions[name]) for name in _package_names(facts)]
    return _markdown_table(("文件", "用途"), rows)


def _scalar_is_complex(param, facts):
    if param.get("dtype") in COMPLEX_DTYPES:
        return True
    return "dtype_from" in param and any(
        profile["scalar_dtype"] in COMPLEX_DTYPES
        for profile in facts.get("dtype_profiles", [])
    )


def _column_contract_rows(facts, generator):
    rows = []
    params = {param["name"]: param for param in facts["params"]}
    header = _header_columns(facts)
    for column in header:
        source = "控制列"
        values = "非空字符串"
        note = "用例描述"
        if column == "case_name":
            values = "TC_L0_/TC_PW_/TC_ED_/TC_PF_ 加块内编号"
            note = "GTest 参数名，任务包内唯一"
        elif column == "description":
            values = "非空字符串"
            note = "轴取值、edge 名或性能键值"
        elif column == "expect_result":
            values = "见下方状态词表"
            note = "使用 csv_loader.h parseStatus 支持的全名"
        elif column == "random_seed":
            values = "正整数"
            note = "各缓冲依次使用 seed、seed+1……派生随机填充"
        elif column in params:
            param = params[column]
            source = column
            role = param["role"]
            if role == "enum":
                values = ", ".join(param["values"])
                note = f"枚举短记号：{values}"
            elif role == "dim":
                values = "整数"
                note = "维度"
            elif role == "layout":
                values = "整数"
                target = param.get("of", "目标 buffer")
                if isinstance(target, list):
                    target = "/".join(target)
                notes = {
                    "ld": f"{target} 的前导维度，≥ max(1, rows)",
                    "inc": f"{target} 的步长",
                    "stride": f"{target} 相邻 batch 的元素偏移",
                    "batch": "batch 数",
                }
                note = notes[param["kind"]]
            else:
                values = "实数"
                note = "实数标量"
        elif column.startswith("null"):
            source = next(
                (
                    name
                    for name in params
                    if f"null{name[:1].upper()}{name[1:]}" == column
                ),
                column[4:],
            )
            values = "0, 1"
            note = "1 时传 nullptr 且不分配该参数"
        elif column.endswith("_batch_pattern"):
            source = column[:-14]
            values = "UNIFORM, NULL_TABLE, MIXED_SINGULAR, NULL_ELEMENT_i"
            note = "统一缓冲、空表、混合奇异或第 i 个元素为空"
        elif column.endswith("_fill"):
            # 列名是小写参数名，反查原参数
            source = next(
                (name for name in params if name.lower() == column[:-5]), column[:-5]
            )
            values = "见下方 fill 词表"
            direction = params.get(source, {}).get("dir", "in")
            note = f"{source}（{direction}）的数据填充方式"
        elif column.endswith("_matrix_type"):
            source = column[:-12]
            values = ", ".join(params[source]["conditioning"])
            note = "fill.h 的 BlasLapackMatrixType 构造类型"
        elif column.endswith("_re") or column.endswith("_im"):
            source = column[:-3]
            values = "实数"
            component = "实部" if column.endswith("_re") else "虚部"
            note = f"复标量{component}"
        else:
            for name, param in params.items():
                if param["role"] != "fixed_vector" or not column.startswith(name):
                    continue
                suffix = column[len(name):]
                if suffix.isdigit():
                    source = name
                    values = "int 或 float"
                    note = f"fixed_vector 第 {suffix} 个元素"
                    break
        rows.append((column, source, values, note))
    return rows


def _column_vocab(generator, facts):
    fill_tiers = generator._case_options(facts)["fill_tiers"]
    fill_values = "\n".join(f"- `{value}`" for value in fill_tiers)
    status_values = "\n".join(f"- `{value}`" for value in STATUS_VALUES)
    return (
        "fill 词表（语法见 `fill.h` 的 `METHOD_PATTERN_VAL`）：\n\n"
        + fill_values
        + "\n\n`expect_result` 状态词表（`parseStatus` 全名）：\n\n"
        + status_values
    )


def _golden_requirements(facts):
    golden = facts["golden"]
    details = [f"- `golden.kind`: `{golden['kind']}`"]
    if "symbol" in golden:
        details.append(f"- `golden.symbol`: `{golden['symbol']}`")
    if "formula" in golden:
        details.append(f"- `golden.formula`: `{golden['formula']}`")
    profiles = facts.get("dtype_profiles", [])
    if profiles:
        dtypes = list(dict.fromkeys(profile["golden_dtype"] for profile in profiles))
    else:
        dtypes = list(
            dict.fromkeys(
                param["dtype"]
                for param in facts["params"]
                if "dtype" in param and _is_output(param)
            )
        )
    details.append(f"- golden dtype: `{', '.join(dtypes)}`")
    inout = [
        param["name"]
        for param in facts["params"]
        if param.get("dir") == "inout" or param["role"] == "inout_scalar"
    ]
    if inout:
        details.append("- in-place 参数先快照原值再计算 golden：" + ", ".join(inout))
    producers = [
        f"`{param['name']}` 由 `{param['producer']}` 产生"
        for param in facts["params"]
        if "producer" in param
    ]
    if producers:
        details.append("- producer 链：" + "；".join(producers))
    return "\n".join(details)


VERIFY_DESCRIPTIONS = {
    "full": ("全部输出元素", "逐元素 mixed tolerance"),
    "uplo_triangle": ("uplo 指定三角", "只核指定三角"),
    "non_uplo_exact": ("另一三角", "逐位相等（EXACT）"),
    "hermitian_diag": ("Hermitian 对角线", "虚部绝对值不超过 atol"),
    "vector_inc": ("向量有效元素", "按 inc 核值，跨步之外逐位不变"),
    "scalar": ("标量输出", "Verifier::verifyScalar"),
    "index_exact": ("索引输出", "Verifier::verifyInteger"),
    "inout_scalars": ("每个 inout 标量", "逐个按 scalar 校验"),
    "batch_each": ("每个 batch", "逐 batch 应用其余 token"),
    "solution_residual": ("线性方程解", "||A*X-B||F/(||A||F||X||F*n*eps) <= 30"),
    "lu_reconstruction": ("LU 分解", "||P*L*U-A||F/(||A||F*n*eps) <= 30"),
    "inverse_residual": ("矩阵逆", "||A*A^-1-I||F/(n*eps) <= 30"),
    "qr_reconstruction": ("QR 分解", "||Q*R-A||F/(||A||F*n*eps) <= 30"),
    "orthogonality": ("正交性", "||Q^T*Q-I||F/(n*eps) <= 30"),
    "pivot_validity": ("pivot", "每个 ipiv[i] 满足 1 <= ipiv[i] <= n"),
    "info_exact": ("info", "与 golden 的 info 完全相等"),
}


def _verify_table(facts):
    rows = [
        (token, *VERIFY_DESCRIPTIONS[token]) for token in facts["verify"]
    ]
    return _markdown_table(("token", "校验对象", "方式与阈值"), rows)


def _tolerance_text(facts):
    selected = {
        profile["precision_row"] for profile in facts.get("dtype_profiles", [])
    }
    if not selected:
        dtype_rows = {
            "float16": "FLOAT16",
            "bfloat16": "BFLOAT16",
            "float32": "FLOAT32",
            "complex64": "FLOAT32",
        }
        selected = {
            dtype_rows[param.get("dtype")]
            for param in facts["params"]
            if _is_output(param) and param.get("dtype") in dtype_rows
        }
    if not selected:
        selected = {"FLOAT32"}
    table = _markdown_table(
        ("precision_row", "rtol", "atol", "fixed_limit", "mantissa_bits", "emin"),
        (
            (name, *TOLERANCE_ROWS[name])
            for name in ("FLOAT16", "BFLOAT16", "FLOAT32")
            if name in selected
        ),
    )
    first = (
        "通过条件：逐元素 `|actual - golden| <= atol + rtol * |golden|`，\n"
        "`matched_ratio >= 0.99`，且每个元素都满足 `abs_error <= max(fixed_limit,\n"
        "32 * ULP_at_|golden|)`。ULP 使用表中的 mantissa_bits 与 emin。表值逐项来自\n"
        "`test/frame/verify.h` 的 `getMixedToleranceDefaults` 与\n"
        "`MixedToleranceStrategy::processElement`。"
    )
    if not RESIDUAL_VERIFY_TOKENS.intersection(facts["verify"]):
        return table + "\n\n" + first
    second = (
        "残差中的 `eps` 取 dtype 机器精度；阈值 30 出自 LAPACK 测试套件惯例\n"
        "`thresh=30`。"
    )
    return table + "\n\n" + first + "\n\n" + second


def _block_table(report):
    rules = {
        "L0": "op enum 全组合与 4/8 快速覆盖",
        "PW": "确定性 pairwise 且通过 constraints/footprint",
        "ED": "逐条 edge_cases.set 物化",
        "PF": "perf.rows 与可选 sweep",
    }
    rows = [(name, report["blocks"][name], rules[name]) for name in rules]
    return _markdown_table(("块", "条数", "规则"), rows)


def _gpu_baseline_section(facts):
    perf = facts.get("perf")
    if not perf:
        return "本算子无 GPU 基线，性能只采集不评判。"
    meta = {key: perf.get("meta", {}).get(key, "unspecified") for key in PERF_META_KEYS}
    meta_rows = [(key, value) for key, value in meta.items()]
    headers = ("id", *perf["key"], "gpu_ms")
    data_rows = []
    for index, row in enumerate(perf["rows"], 1):
        data_rows.append(
            (f"{facts['op']}-base-{index:03d}", *(row[key] for key in perf["key"]),
             row.get("gpu_ms", "只采集不评判"))
        )
    return (
        "元数据：\n\n"
        + _markdown_table(("键", "值"), meta_rows)
        + "\n\n基线行：\n\n"
        + _markdown_table(headers, data_rows)
    )


def _replace_tokens(template, values):
    rendered = template
    for token, value in values.items():
        rendered = rendered.replace(f"@@{token}@@", str(value))
    remaining = sorted(set(TOKEN_RE.findall(rendered)))
    if remaining:
        raise ValueError("模板仍含未替换 token：" + ", ".join(remaining))
    return rendered


def _write_text(path, text, executable=False):
    with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    if executable:
        Path(path).chmod(Path(path).stat().st_mode | 0o111)


def _render_script(template_path, output_path, facts, csv_hash):
    template = template_path.read_text(encoding="utf-8")
    perf = facts.get("perf", {})
    rendered = _replace_tokens(
        template,
        {
            "OP": facts["op"],
            "FAMILY": facts["family"],
            "CSV_NAME": f"{facts['op']}_test.csv",
            "PACKAGE_CSV_SHA256": csv_hash,
            "GENERATOR_VERSION": facts["generator_version"],
            "PERF_KEY": "\", \"".join(perf.get("key", [])),
            "PROFILE_ASSIGNS": json.dumps(
                {
                    profile["name"]: profile["assign"]
                    for profile in facts.get("dtype_profiles", [])
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "PERF_THRESHOLD": perf.get("threshold", 0.8),
        },
    )
    _write_text(output_path, rendered, executable=True)


def render_runtime(op, family, perf_key, out_dir, csv_sha256, threshold=0.8):
    """按最小事实把两个 verify 脚本渲染到 out_dir。

    accept 用它把任何任务包（含社区旧包）变成运行时包：脚本只需要 op、family、性能键列，
    这些都能从 CSV 文件名、工程目录和 gpu_baseline.csv 表头得到，不需要 FACTS。
    """
    facts = {
        "op": op,
        "family": family,
        "generator_version": GENERATOR_VERSION,
        "perf": {"key": list(perf_key), "threshold": threshold},
        "dtype_profiles": [],
    }
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    _render_script(ACCURACY_TEMPLATE_PATH, target / "verify_accuracy.py", facts, csv_sha256)
    _render_script(
        PERFORMANCE_TEMPLATE_PATH, target / "verify_performance.py", facts, csv_sha256
    )
    return [target / "verify_accuracy.py", target / "verify_performance.py"]


def _render_readme(path, facts, generator, generated):
    header_parts = _header_columns(facts)
    header_lines = []
    current = ""
    for index, column in enumerate(header_parts):
        token = column + ("," if index + 1 < len(header_parts) else "")
        if current and len(current) + len(token) > 96:
            header_lines.append(current)
            current = token
        else:
            current += token
    header_lines.append(current)
    header = "\n".join(header_lines)
    report = generated["report"]
    values = {
        "OP": facts["op"],
        "SOURCE_TABLE": _source_table(facts),
        "SIGNATURE": _signature(facts),
        "SIX_FILES": _six_files_table(facts),
        "COLUMN_CONTRACT": (
            _markdown_table(
                ("列", "来源参数", "取值", "说明"),
                _column_contract_rows(facts, generator),
            )
            + "\n\n"
            + _column_vocab(generator, facts)
        ),
        "HEADER": header,
        "GOLDEN_REQUIREMENTS": _golden_requirements(facts),
        "VERIFY_TABLE": _verify_table(facts),
        "TOLERANCE_TEXT": _tolerance_text(facts),
        "BLOCK_TABLE": _block_table(report),
        "PAIR_SUMMARY": (
            f"pairs 覆盖 {report['pairs_covered']}/{report['pairs_total']}，"
            f"infeasible {len(report['pairs_infeasible'])} 对。"
        ),
        "GPU_BASELINE": _gpu_baseline_section(facts),
        "PERF_KEY": ", ".join(facts.get("perf", {}).get("key", [])) or "无",
        "PERF_THRESHOLD": facts.get("perf", {}).get("threshold", 0.8),
    }
    template = README_TEMPLATE_PATH.read_text(encoding="utf-8")
    _write_text(path, _replace_tokens(template, values))


def _render_gpu_baseline(path, facts):
    perf = facts.get("perf", {})
    meta = perf.get("meta", {})
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        for key in PERF_META_KEYS:
            stream.write(f"# {key}={meta.get(key, 'unspecified')}\n")
        writer = csv.writer(stream, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        keys = perf.get("key", [])
        writer.writerow(["id", *keys, "gpu_ms"])
        for index, row in enumerate(perf.get("rows", []), 1):
            writer.writerow(
                [f"{facts['op']}-base-{index:03d}", *(row[key] for key in keys),
                 row.get("gpu_ms", "")]
            )


def _check_generation_report(problems, facts, generated):
    if generated.get("header") != _header_columns(facts):
        _err(problems, "generate 输出的 header 与 package.py 投影不一致")
    report = generated.get("report", {})
    blocks = report.get("blocks", {})
    if blocks.get("L0", 0) < 1:
        _err(problems, "L0 行数必须至少为 1")
    has_variable_axis = any(
        axis.get("values", 0) > 1 for axis in report.get("axes", [])
    )
    if has_variable_axis and blocks.get("PW", 0) < 1:
        _err(problems, "存在可变轴时 PW 行数必须至少为 1")


def _check_package(facts_path, facts):
    problems = []
    package_dir = facts_path.parent
    op_csv = package_dir / f"{facts['op']}_test.csv"
    if not op_csv.exists():
        return problems, None
    try:
        generator = _load_generator()
        generated = generator.generate(facts)
        second = generator.generate(facts)
    except Exception as exc:
        _err(problems, f"重生成失败：{exc}")
        return problems, None
    if generated != second:
        _err(problems, "同一事实表 FACTS 两次 generate 结果不一致")
        return problems, generated.get("report")
    _check_generation_report(problems, facts, generated)
    # 五件派生物必须与从同一 FACTS 重新渲染的结果逐字节一致：任何差异都表明包被改动。
    # 这一条通用规则封住派生物对可信渲染器的任何字节偏离；生成器输出的语义正确性
    # 另由 schema 与生成器内的几条不变量保证，不在此重复逐文件校验。
    with tempfile.TemporaryDirectory(prefix="blas-case-check-") as directory:
        _render_derived(directory, facts, generator, generated)
        for name in _package_names(facts)[1:]:
            deployed = package_dir / name
            fresh = Path(directory) / name
            if not deployed.is_file():
                _err(problems, f"任务包缺文件：{name}")
            elif deployed.read_bytes() != fresh.read_bytes():
                _err(problems, f"{name} 与从 FACTS 重新渲染的结果不一致，可能被改动")
    return problems, generated["report"]


def check_package(facts_path, require_rendered=True):
    """安全校验任务包；不导入或执行任务包内的 Python 文件。"""
    path = Path(facts_path).resolve()
    facts = _load_python_facts(path)
    problems = validate(facts)
    has_common_code = _check_source_integrity(problems, path)
    if not has_common_code:
        _err(problems, "gen_csv.py 缺通用代码区")
    csv_path = path.parent / f"{facts.get('op', '')}_test.csv"
    report = None
    if require_rendered and not csv_path.is_file():
        _err(problems, f"任务包缺文件：{csv_path.name}")
    if not problems and csv_path.is_file():
        package_problems, report = _check_package(path, facts)
        problems.extend(package_problems)
    return {
        "facts": facts,
        "header": _header_columns(facts) if not problems else None,
        "problems": problems,
        "report": report,
    }


def _print_problems(problems):
    print(f"事实表 FACTS 或任务包有 {len(problems)} 处不合格：", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)


def _run_check(args):
    try:
        facts = _load_facts(args.facts)
    except FactsPolicyError as exc:
        _print_problems([str(exc)])
        return 2
    if facts is None:
        return 3
    facts_path = Path(args.facts).resolve()
    problems = validate(facts)
    has_common_code = False
    if facts_path.suffix == ".py":
        has_common_code = _check_source_integrity(problems, facts_path)
    if problems:
        _print_problems(problems)
        return 2
    report = None
    if has_common_code:
        generator = _load_generator()
        try:
            generated = generator.generate(facts)
        except Exception as exc:
            _print_problems([f"生成失败：{exc}"])
            return 2
        generation_problems = []
        _check_generation_report(generation_problems, facts, generated)
        report = generated.get("report")
        if generation_problems:
            _print_problems(generation_problems)
            return 2
        if (facts_path.parent / f"{facts['op']}_test.csv").is_file():
            package_problems, report = _check_package(facts_path, facts)
            if package_problems:
                _print_problems(package_problems)
                return 2
    _print_summary(facts)
    if args.print_header:
        print(",".join(_header_columns(facts)))
    if report is not None:
        print(
            f"pairs 覆盖 {report['pairs_covered']}/{report['pairs_total']}，"
            f"infeasible {len(report['pairs_infeasible'])} 对"
        )
        print(f"rows_dropped {report['rows_dropped']}")
        print("六件        " + "、".join(_package_names(facts)))
    return 0


def _run_render(args):
    try:
        facts = _load_facts(args.facts)
    except FactsPolicyError as exc:
        _print_problems([str(exc)])
        return 2
    if facts is None:
        return 3
    facts_path = Path(args.facts).resolve()
    problems = validate(facts)
    has_common_code = False
    if facts_path.suffix == ".py":
        has_common_code = _check_source_integrity(problems, facts_path)
    if not has_common_code:
        _err(problems, "render 要求 --facts 指向含通用代码区的 gen_csv.py")
    if problems:
        _print_problems(problems)
        return 2
    csv_path = facts_path.parent / f"{facts['op']}_test.csv"
    try:
        generator = _load_generator()
        generated = generator.generate(facts)
        generation_problems = []
        _check_generation_report(generation_problems, facts, generated)
        if generation_problems:
            _print_problems(generation_problems)
            return 2
        _render_derived(facts_path.parent, facts, generator, generated)
        print(f"{csv_path.name}: {len(generated['rows'])} rows")
        print(f"解释器：{Path(sys.executable).resolve()}")
    except Exception as exc:
        print(f"六件渲染失败：{exc}", file=sys.stderr)
        return 2
    package_problems, _ = _check_package(facts_path, facts)
    if package_problems:
        _print_problems(package_problems)
        return 2
    print("六件：")
    for name in _package_names(facts):
        print(f"  {facts_path.parent / name}")
    return 0


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="校验事实表 FACTS")
    check.add_argument("--facts", required=True, help="gen_csv.py 或 JSON 事实表 FACTS")
    check.add_argument("--print-header", action="store_true", help="通过后打印 CSV 表头")
    check.set_defaults(handler=_run_check)
    render = subparsers.add_parser("render", help="渲染六件")
    render.add_argument("--facts", required=True, help="待渲染的 gen_csv.py")
    render.add_argument("arguments", nargs=argparse.REMAINDER)
    render.set_defaults(handler=_run_render)
    return parser


def main():
    args = _parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
