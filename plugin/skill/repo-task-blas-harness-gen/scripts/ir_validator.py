#!/usr/bin/env python3
"""ir_validator —— contract-ir.md v3 的机械门（Step 2 冻结判据的可执行体）。

三个公开入口，对应 §0 的三个 H2 判定层（pre_ir 层在 package_loader）：

- validate_facts(facts)      参数门 param_gate：顶层身份、逐角色 FACTS 键白名单、
                             归一化+对账、role/nullable/dtype 值域。
- translate_shape_expr(src)  ast_gate 的表达式半程：FACTS 形状表达式 → 标签数组 AST，
                             文法外构造停机。
- validate_ir(ir)            IR 结构门：§2-§9 全部形态、§12a 跨段不变量、
                             signature_table 查表与单源一致性。

拒绝一律抛 ContractReject（.payload 为 {code, gate, field_path, observed, allowed}），
多处违规报首个命中（§11：层序，层内按文档序）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CODE = "UNSUPPORTED_CONTRACT"
TABLE_PATH = Path(__file__).resolve().parent / "signature_table.json"

IDENT_LOWER = re.compile(r"^[a-z][a-z0-9_]*$")
IDENT_C = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

CTYPES = {"aclblasHandle_t", "int", "const float*", "float*", "aclblasOperation_t"}
ROLES = ("handle", "enum", "dim", "layout", "scalar", "vector", "matrix", "out_scalar")
REJECTED_ROLES = {"inout_scalar", "fixed_vector", "int_array"}
DIR_ASSIGN = {"handle": "in", "enum": "in", "dim": "in", "layout": "in",
              "scalar": "in", "vector": None, "matrix": None, "out_scalar": "out"}
DIR_ALLOWED = {"vector": {"in"}, "matrix": {"in", "inout"}}
MEM_ASSIGN = {"handle": "host", "enum": "host", "dim": "host", "layout": "host",
              "scalar": "host", "vector": "device", "matrix": "device",
              "out_scalar": "device"}
# §0 逐角色必填键（基础键外；缺失即 param_gate）
FACTS_REQUIRED = {
    "handle": set(), "dim": set(),
    "enum": {"values", "enum_kind"},
    "layout": {"kind", "of"},
    "scalar": {"dtype", "mem"},
    "vector": {"dtype", "len"},
    "matrix": {"dtype", "rows", "cols", "ld"},
    "out_scalar": {"dtype"},
}
# §0 逐角色 FACTS 键白名单（基础键 name/ctype/role 之外）
FACTS_KEYS = {
    "handle": set(), "dim": set(),
    "enum": {"values", "enum_kind"},
    "layout": {"kind", "of"},
    "scalar": {"dtype", "mem", "nullable", "values"},
    "vector": {"dtype", "dir", "len", "inc", "nullable"},
    "matrix": {"dtype", "dir", "rows", "cols", "ld", "order", "storage", "nullable"},
    "out_scalar": {"dtype", "mem", "nullable"},
}
IR_PARAM_KEYS = {"name", "ctype", "role", "dir", "mem", "dtype", "enum", "layout",
                 "shape", "order", "storage"}
TOP_KEYS = {"version", "facts_schema_version", "op", "family", "symbol", "returns",
            "params", "columns", "buffers", "movement", "golden", "verify",
            "status_plan", "smoke", "upload_guard"}
KIND_MAP = {"id": "case_name", "expect": "expect_result", "seed": "random_seed"}
IR_KINDS = {"case_name", "description", "dim", "enum", "scalar", "layout", "fill",
            "expect_result", "random_seed"}
READER_BY_KIND = {"dim": "int64", "layout": "int64", "fill": "fill", "enum": "enum",
                  "scalar": "float", "expect_result": "status", "case_name": "base",
                  "description": "base", "random_seed": "base"}
SHAPE_NODES = {"int": 1, "ref": 1, "mul": 2, "add": 2, "sub": 2, "max": 2, "cond": 3}
COND_ONLY = {"le": 2, "lt": 2, "eq": 2, "or": 2, "and": 2}
MOVEMENT_BY_DIR = {"in": ["upload"], "inout": ["upload", "readback"],
                   "out": ["alloc_out", "readback"]}
VOCAB = ["ACLBLAS_STATUS_SUCCESS", "ACLBLAS_STATUS_NOT_INITIALIZED",
         "ACLBLAS_STATUS_ALLOC_FAILED", "ACLBLAS_STATUS_INVALID_VALUE",
         "ACLBLAS_STATUS_MAPPING_ERROR", "ACLBLAS_STATUS_EXECUTION_FAILED",
         "ACLBLAS_STATUS_INTERNAL_ERROR", "ACLBLAS_STATUS_NOT_SUPPORTED",
         "ACLBLAS_STATUS_ARCH_MISMATCH", "ACLBLAS_STATUS_HANDLE_IS_NULLPTR",
         "ACLBLAS_STATUS_INVALID_ENUM", "ACLBLAS_STATUS_UNKNOWN"]
GOLDEN_CONSTS = {"CblasColMajor"}


class ContractReject(Exception):
    def __init__(self, gate, field_path, observed, allowed):
        self.payload = {"code": CODE, "gate": gate, "field_path": field_path,
                        "observed": observed, "allowed": allowed}
        super().__init__(f"{CODE}[{gate}] {field_path}: {observed!r} not in {allowed}")


def _rej(gate, path, observed, allowed):
    raise ContractReject(gate, path, observed, allowed)


STATUS_PLANS_DIR = (Path(__file__).resolve().parent.parent / "assets" / "status-plans")
ARG_TRANSFORM = {"enum": "enum_map", "scalar": "deref", "dim": "cast_int",
                 "layout": "cast_int", "vector": "pass", "matrix": "pass"}
CHECK_KIND_KEYS = {
    "null_check": {"kind", "param", "status"},
    "quick_return": {"kind", "cond_ast", "status", "writes_zero"},
    "cond": {"kind", "cond_ast", "status"},
}


# matrix_phys 可选：转置依赖的物理矩阵换位表（matrix 参数名 → 决定换位的 enum 参数名）。
# compile 的 sidecar 路径读它（默认 {}），故须在闭键内允许，否则转置型 cblas 算子无法只加数据
# 接入（Step 4/5 复核指出的泛化矛盾）。sger 无转置，省略即空。
_SIDECAR_KEYS = {"_doc", "format_version", "symbol", "status_checks",
                 "sync_fail_status", "smoke", "upload_guard", "matrix_phys"}


def load_status_plan_sidecar(symbol):
    """受信状态策略侧车（方案乙）：assets/status-plans/<symbol>.json；缺则 None。
    闭 schema：文件名==内部 symbol==入参 symbol；闭键；禁 kernel/C++ 字段。"""
    if not isinstance(symbol, str) or not re.match(  # 非 str 先判，防 TypeError；防路径穿越
            r"^[A-Za-z_][A-Za-z0-9_]*$", symbol):
        _rej("signature_table", "golden.symbol", symbol, "C 标识符")
    f = STATUS_PLANS_DIR / (symbol + ".json")
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _rej("signature_table", f"sidecar[{symbol}]", str(exc), "合法 JSON")
    if not isinstance(data, dict):
        _rej("signature_table", f"sidecar[{symbol}]", type(data).__name__, "JSON 对象")
    extra = set(data) - _SIDECAR_KEYS
    if extra:
        _rej("signature_table", f"sidecar[{symbol}]", sorted(extra),
             sorted(_SIDECAR_KEYS))
    if data.get("format_version") != 1:
        _rej("signature_table", f"sidecar[{symbol}].format_version",
             data.get("format_version"), [1])
    if data.get("symbol") != symbol:
        _rej("signature_table", f"sidecar[{symbol}].symbol", data.get("symbol"),
             [symbol])
    for req in ("status_checks", "sync_fail_status", "smoke", "upload_guard"):
        if req not in data:
            _rej("signature_table", f"sidecar[{symbol}].{req}", None, "必填")
    if "matrix_phys" in data and not isinstance(data["matrix_phys"], dict):
        _rej("signature_table", f"sidecar[{symbol}].matrix_phys",
             type(data["matrix_phys"]).__name__, ["dict（matrix→enum 换位表）"])
    return data


def resolve_behavior_source(symbol):
    """返回 (kind, source)：'table' 或 'sidecar'；恰一来源，两有两无都 fail-closed。"""
    table = load_signature_table()
    in_table = symbol in table and not symbol.startswith("_")
    sidecar = load_status_plan_sidecar(symbol)
    if in_table and sidecar is not None:
        _rej("signature_table", "golden.symbol", symbol, "行为源须唯一：表与侧车都有")
    if in_table:
        return "table", table[symbol]
    if sidecar is not None:
        return "sidecar", sidecar
    _rej("signature_table", "golden.symbol", symbol,
         "无行为源（签名表与 status-plans 侧车均无）")


def derive_cblas_golden(params):
    """cblas_call 的 args/ret 从 params 按角色派生（方案甲）。
    v3 通用路径只支持 void/out_param（矩阵原地输出，如 sger/sgemm）；out_scalar 形态
    （返回值型，如 sasum）收窄为不支持——它走 builtin_kernel L1，从不进 cblas 通用路径。
    这样避免「out_scalar 既作 cblas 入参又作返回值」的自相矛盾派生（Step 4/5 F-03）。"""
    outs = [p for p in params if p["dir"] in ("out", "inout")]
    o = outs[0]
    if o["role"] == "out_scalar":
        _rej("signature_table", "golden.body", "cblas_call + out_scalar",
             "cblas_call 只支持 out_param（矩阵原地）；out_scalar 型走 builtin_kernel L1")
    has_matrix = any(p["role"] == "matrix" for p in params)
    args = []
    if has_matrix:
        args.append({"const": "CblasColMajor"})
    for p in params:
        if p["role"] == "handle":
            continue
        args.append({"param": p["name"], "transform": ARG_TRANSFORM[p["role"]]})
    return args, {"kind": "out_param", "param": o["name"]}


def load_signature_table():
    return json.loads(TABLE_PATH.read_text(encoding="utf-8"))


def check_signature(symbol: str) -> dict:
    """signature_table 门：golden.symbol 查表，miss 即停。"""
    table = load_signature_table()
    entries = sorted(k for k in table if not k.startswith("_"))
    if symbol not in entries:
        _rej("signature_table", "golden.symbol", symbol, entries)
    return table[symbol]


# ---------------------------------------------------------------- param_gate

def validate_facts(facts: dict) -> None:
    """参数门：FACTS 顶层身份与 params 白名单/归一化/对账。首个命中即停。"""
    g = "param_gate"
    for key, pat in (("op", IDENT_LOWER), ("family", IDENT_LOWER), ("symbol", IDENT_C)):
        v = facts.get(key)
        if not isinstance(v, str) or not pat.match(v):
            _rej(g, key, v, f"regex {pat.pattern}")
    if facts.get("returns") != "aclblasStatus_t":
        _rej(g, "returns", facts.get("returns"), ["aclblasStatus_t"])
    params = facts.get("params")
    if not isinstance(params, list) or not params:
        _rej(g, "params", params, "非空列表")
    names = [p.get("name") for p in params if isinstance(p, dict)]
    for i, p in enumerate(params):
        path = f"params[{i}]"
        if not isinstance(p, dict):
            _rej(g, path, p, "dict")
        role = p.get("role")
        if role in REJECTED_ROLES:
            _rej(g, f"{path}.role", role, list(ROLES))
        if role not in ROLES:
            _rej(g, f"{path}.role", role, list(ROLES))
        allowed = {"name", "ctype", "role"} | FACTS_KEYS[role]
        for key in p:
            if key not in allowed:
                _rej(g, f"{path}.{key}", p[key], sorted(allowed))
        for key in sorted(FACTS_REQUIRED[role]):
            if key not in p:
                _rej(g, f"{path}.{key}", None, f"{role} 必填键")
        if p.get("ctype") not in CTYPES:
            _rej(g, f"{path}.ctype", p.get("ctype"), sorted(CTYPES))
        # 归一化后对账（F-04：先按 FACTS 默认归一，再对指派）
        nullable = p.get("nullable", False)
        if not isinstance(nullable, bool):
            _rej(g, f"{path}.nullable", nullable, [False])
        if nullable:
            _rej(g, f"{path}.nullable", True, [False])
        mem = p.get("mem", "device")  # FACTS 默认 device
        want_mem = MEM_ASSIGN[role]
        if role in ("scalar", "out_scalar", "vector", "matrix", "handle",
                    "dim", "enum", "layout"):
            if "mem" in p or role in ("scalar", "out_scalar"):
                if mem != want_mem:
                    _rej(g, f"{path}.mem", mem, [want_mem])
        if role in ("vector", "matrix"):
            d = p.get("dir", "in")
            if d not in DIR_ALLOWED[role]:
                _rej(g, f"{path}.dir", d, sorted(DIR_ALLOWED[role]))
        if role in ("vector", "matrix", "scalar", "out_scalar"):
            if p.get("dtype") != "float32":
                _rej(g, f"{path}.dtype", p.get("dtype"), ["float32"])
        if role == "matrix":
            if p.get("order", "col_major") != "col_major":
                _rej(g, f"{path}.order", p.get("order"), ["col_major"])
            if p.get("storage", "full") != "full":
                _rej(g, f"{path}.storage", p.get("storage"), ["full"])
        if role == "layout":
            if p.get("kind") not in ("inc", "ld"):
                _rej(g, f"{path}.kind", p.get("kind"), ["inc", "ld"])
            if p.get("of") not in names:
                _rej(g, f"{path}.of", p.get("of"), names)
    if params[0].get("role") != "handle":
        _rej(g, "params[0].role", params[0].get("role"), ["handle"])
    outs = [p for p in params
            if p.get("role") == "out_scalar" or p.get("dir") == "inout"]
    if len(outs) != 1:
        _rej(g, "params", f"{len(outs)} 个输出", "恰一输出（out 或 inout）")
    golden = facts.get("golden")
    if not isinstance(golden, dict) or golden.get("kind") != "cblas":
        _rej(g, "golden.kind",
             golden.get("kind") if isinstance(golden, dict) else golden, ["cblas"])
    # golden.symbol 须为 C 标识符 str（缺/ null 也 ContractReject，不放 KeyError/TypeError
    # 逃逸到 compile 或侧车路径拼接，F-05）。
    sym = golden.get("symbol")
    if not isinstance(sym, str) or not IDENT_C.match(sym):
        _rej(g, "golden.symbol", sym, f"regex {IDENT_C.pattern}")


# ------------------------------------------------------------------ ast_gate

_TOKEN = re.compile(r"\s*(\d+|[A-Za-z_][A-Za-z0-9_]*|[()*+-]|.)")


def translate_shape_expr(src) -> list:
    """FACTS 形状表达式 → 标签数组 AST（+ - * 与括号、标识符、整数）。
    文法外 token（如 //、比较、三目）→ ast_gate 停机。"""
    g = "ast_gate"
    if isinstance(src, int):
        return ["int", src]
    if not isinstance(src, str):
        _rej(g, "shape_expr", src, "str 或 int")
    tokens, pos = [], 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        tok = m.group(1)
        pos = m.end()
        if tok.strip():
            tokens.append(tok)
    for tok in tokens:
        if not (tok.isdigit() or IDENT_C.match(tok) or tok in "()*+-"):
            _rej(g, "shape_expr", src, "文法：int/ident/()/+/-/*")
    idx = 0

    def peek():
        return tokens[idx] if idx < len(tokens) else None

    def parse_expr():
        nonlocal idx
        node = parse_term()
        while peek() in ("+", "-"):
            op = "add" if tokens[idx] == "+" else "sub"
            idx += 1
            node = [op, node, parse_term()]
        return node

    def parse_term():
        nonlocal idx
        node = parse_atom()
        while peek() == "*":
            idx += 1
            node = ["mul", node, parse_atom()]
        return node

    def parse_atom():
        nonlocal idx
        tok = peek()
        if tok is None:
            _rej(g, "shape_expr", src, "表达式不完整")
        idx += 1
        if tok == "(":
            node = parse_expr()
            if peek() != ")":
                _rej(g, "shape_expr", src, "括号闭合")
            idx += 1
            return node
        if tok.isdigit():
            return ["int", int(tok)]
        if IDENT_C.match(tok):
            return ["ref", tok]
        _rej(g, "shape_expr", src, "文法：int/ident/()/+/-/*")

    node = parse_expr()
    if idx != len(tokens):
        _rej(g, "shape_expr", src, "文法：int/ident/()/+/-/*")
    return node


def _check_ast(node, ctx, path, names, enum_names=frozenset()):
    g = "ast_gate"
    if not isinstance(node, list) or not node or not isinstance(node[0], str):
        _rej(g, path, node, "标签数组节点")
    tag, args = node[0], node[1:]
    if tag == "str":
        _rej(g, path, node, "str 仅许作 eq 一侧")
    table = dict(SHAPE_NODES)
    if ctx == "cond":
        table.update(COND_ONLY)
    if tag not in table:
        _rej(g, f"{path}.{tag}", tag, sorted(table))
    if len(args) != table[tag]:
        _rej(g, f"{path}.{tag}", len(args), f"arity {table[tag]}")
    if tag == "int":
        if not isinstance(args[0], int) or isinstance(args[0], bool):
            _rej(g, f"{path}.int", args[0], "int（非 bool）")
        return
    if tag == "ref":
        if args[0] not in names:
            _rej(g, f"{path}.ref", args[0], sorted(names))
        return
    if tag == "eq":
        str_sides = [a for a in args if isinstance(a, list) and a[:1] == ["str"]]
        if len(str_sides) > 1:
            _rej(g, f"{path}.eq", node, "str 至多一侧")
        if len(str_sides) == 1:
            other = args[0] if args[1] is str_sides[0] else args[1]
            s = str_sides[0]
            if len(s) != 2 or not isinstance(s[1], str):
                _rej(g, f"{path}.str", s, '["str", s]')
            if not (isinstance(other, list) and other[:1] == ["ref"]
                    and len(other) == 2 and other[1] in enum_names):
                _rej(g, f"{path}.eq", other, "str 对侧须为 enum 参数 ref")
            return
        for a in args:
            _check_ast(a, "cond", f"{path}.eq", names, enum_names)
        return
    if tag == "cond":
        _check_ast(args[0], "cond", f"{path}.cond", names, enum_names)
        _check_ast(args[1], ctx, f"{path}.cond", names, enum_names)
        _check_ast(args[2], ctx, f"{path}.cond", names, enum_names)
        return
    child_ctx = "cond" if (ctx == "cond" and tag in ("or", "and")) else ctx
    for a in args:
        _check_ast(a, child_ctx if tag in ("or", "and", "le", "lt") else ctx,
                   f"{path}.{tag}", names, enum_names)


# ------------------------------------------------------------------ IR 结构门

def validate_ir(ir: dict) -> None:
    g = "param_gate"
    if not isinstance(ir, dict):
        _rej(g, "<top>", type(ir).__name__, ["dict"])
    if set(ir) != TOP_KEYS:
        _rej(g, "<top>", sorted(set(ir) ^ TOP_KEYS), sorted(TOP_KEYS))
    # 分段类型门：非 list/非 dict 段一律 ContractReject（不放任 Type/AttributeError 逃逸，F-04）。
    for key in ("params", "columns", "buffers", "movement", "verify"):
        if not isinstance(ir[key], list):
            _rej(g, key, type(ir[key]).__name__, ["list"])
    for key in ("golden", "status_plan"):
        if not isinstance(ir[key], dict):
            _rej(g, key, type(ir[key]).__name__, ["dict"])
    if ir["smoke"] is not None and not isinstance(ir["smoke"], dict):
        _rej(g, "smoke", type(ir["smoke"]).__name__, ["dict", "null"])
    if ir["upload_guard"] is not None and not isinstance(ir["upload_guard"], dict):
        _rej(g, "upload_guard", type(ir["upload_guard"]).__name__, ["dict", "null"])
    for i, p in enumerate(ir["params"]):
        if not isinstance(p, dict):
            _rej(g, f"params[{i}]", type(p).__name__, ["dict"])
    if not ir["params"]:
        _rej(g, "params", "空", "非空参数列表")
    if ir["version"] != 3:
        _rej(g, "version", ir["version"], [3])
    for key, pat in (("op", IDENT_LOWER), ("family", IDENT_LOWER),
                     ("symbol", IDENT_C)):
        if not isinstance(ir[key], str) or not pat.match(ir[key]):
            _rej(g, key, ir[key], f"regex {pat.pattern}")
    if ir["returns"] != "aclblasStatus_t":
        _rej(g, "returns", ir["returns"], ["aclblasStatus_t"])

    params = ir["params"]
    names = [p["name"] for p in params]
    by_name = {p["name"]: p for p in params}
    enum_names = frozenset(p["name"] for p in params if p.get("role") == "enum")
    IR_ROLE_KEYS = {
        "handle": {"name", "ctype", "role", "dir", "mem"},
        "dim": {"name", "ctype", "role", "dir", "mem"},
        "enum": {"name", "ctype", "role", "dir", "mem", "enum"},
        "layout": {"name", "ctype", "role", "dir", "mem", "layout"},
        "scalar": {"name", "ctype", "role", "dir", "mem", "dtype"},
        "out_scalar": {"name", "ctype", "role", "dir", "mem", "dtype"},
        "vector": {"name", "ctype", "role", "dir", "mem", "dtype", "shape"},
        "matrix": {"name", "ctype", "role", "dir", "mem", "dtype", "shape",
                   "order", "storage"},
    }
    for i, p in enumerate(params):
        path = f"params[{i}]"
        role = p.get("role")
        if role not in IR_ROLE_KEYS:
            _rej(g, f"{path}.role", role, list(ROLES))
        if set(p) != IR_ROLE_KEYS[role]:
            _rej(g, path, sorted(p), sorted(IR_ROLE_KEYS[role]))
        if role not in ROLES:
            _rej(g, f"{path}.role", role, list(ROLES))
        want_dir = DIR_ASSIGN[role]
        if want_dir is not None and p.get("dir") != want_dir:
            _rej(g, f"{path}.dir", p.get("dir"), [want_dir])
        if want_dir is None and p.get("dir") not in DIR_ALLOWED[role]:
            _rej(g, f"{path}.dir", p.get("dir"), sorted(DIR_ALLOWED[role]))
        if p.get("mem") != MEM_ASSIGN[role]:
            _rej(g, f"{path}.mem", p.get("mem"), [MEM_ASSIGN[role]])
        if role in ("vector", "matrix", "scalar", "out_scalar"):
            if p.get("dtype") != "float32":
                _rej(g, f"{path}.dtype", p.get("dtype"), ["float32"])
        if role == "enum":
            if not isinstance(p["enum"], dict) or set(p["enum"]) != {"values", "enum_kind"}:
                _rej(g, f"{path}.enum", sorted(p["enum"]) if isinstance(p["enum"], dict)
                     else type(p["enum"]).__name__, ["enum_kind", "values"])
        if role == "layout":
            if not isinstance(p["layout"], dict) or set(p["layout"]) != {"kind", "of"}:
                _rej(g, f"{path}.layout", sorted(p["layout"]) if isinstance(p["layout"], dict)
                     else type(p["layout"]).__name__, ["kind", "of"])
        if role == "vector":
            if not isinstance(p["shape"], dict) or set(p["shape"]) != {"len_ast"}:
                _rej(g, f"{path}.shape", sorted(p["shape"]) if isinstance(p["shape"], dict)
                     else type(p["shape"]).__name__, ["len_ast"])
            _check_ast(p["shape"]["len_ast"], "shape", f"{path}.shape.len_ast", names, enum_names)
        if role == "matrix":
            if not isinstance(p["shape"], dict) \
                    or set(p["shape"]) != {"rows_ast", "cols_ast", "ld_ast"}:
                _rej(g, f"{path}.shape", sorted(p["shape"]) if isinstance(p["shape"], dict)
                     else type(p["shape"]).__name__, ["cols_ast", "ld_ast", "rows_ast"])
            if p.get("order") != "col_major" or p.get("storage") != "full":
                _rej(g, f"{path}.order/storage",
                     (p.get("order"), p.get("storage")), ["col_major", "full"])
            for k in ("rows_ast", "cols_ast", "ld_ast"):
                _check_ast(p["shape"][k], "shape", f"{path}.shape.{k}", names, enum_names)
        if role == "layout" and p["layout"]["of"] not in names:
            _rej(g, f"{path}.layout.of", p["layout"]["of"], sorted(names))
    if params[0]["role"] != "handle":
        _rej(g, "params[0].role", params[0]["role"], ["handle"])
    outs = [p for p in params if p.get("dir") in ("out", "inout")]
    if len(outs) != 1:
        _rej(g, "params", f"{len(outs)} 个输出", "恰一输出")

    for i, c in enumerate(ir["columns"]):
        path = f"columns[{i}]"
        if not isinstance(c, dict):  # 先判字典再 set()，不放 TypeError 逃逸（F-04）
            _rej(g, path, type(c).__name__, ["dict"])
        if set(c) != {"name", "kind", "binds", "reader"}:
            _rej(g, path, sorted(c), ["binds", "kind", "name", "reader"])
        kind = c["kind"]  # IR 闭集；ABI→IR 映射只发生在编译器（F-05）
        if kind not in IR_KINDS:
            _rej(g, f"{path}.kind", c["kind"], sorted(IR_KINDS))
        if c["reader"] != READER_BY_KIND[kind]:
            _rej(g, f"{path}.reader", c["reader"], [READER_BY_KIND[kind]])
        b = c["binds"]
        ok = (isinstance(b, dict) and len(b) == 1 and (
            ("param" in b and b["param"] in names) or "base" in b
            or b.get("seed") is True))
        if not ok:
            _rej(g, f"{path}.binds", b, '{"param":…}|{"base":…}|{"seed":true}')

    device_data = {p["name"] for p in params
                   if p["role"] in ("vector", "matrix", "out_scalar")}
    for i, b in enumerate(ir["buffers"]):  # 先保每条是 dict，再取 param（F-04：不放 Type/KeyError）
        if not isinstance(b, dict) or "param" not in b:
            _rej(g, f"buffers[{i}]", type(b).__name__ if not isinstance(b, dict)
                 else sorted(b), "含 param 的 dict")
    buf_params = [b["param"] for b in ir["buffers"]]
    if len(buf_params) != len(set(buf_params)):
        _rej(g, "buffers", buf_params, "无重复 param")
    bufs = {b["param"]: b for b in ir["buffers"]}
    if set(bufs) != device_data:
        _rej(g, "buffers", sorted(set(bufs) ^ device_data), sorted(device_data))
    for i, b in enumerate(ir["buffers"]):
        if not isinstance(b, dict) or set(b) != {"param", "span_ast", "dtype", "mem", "dir"}:
            _rej(g, f"buffers[{i}]", sorted(b) if isinstance(b, dict) else type(b).__name__,
                 ["dir", "dtype", "mem", "param", "span_ast"])
        p = by_name[b["param"]]
        for k in ("dtype", "mem", "dir"):
            if b[k] != p.get(k):
                _rej(g, f"buffers[{i}].{k}", b[k], [p.get(k)])
        _check_ast(b["span_ast"], "shape", f"buffers[{i}].span_ast", names, enum_names)
    mv: dict = {}
    for i, m in enumerate(ir["movement"]):
        if not isinstance(m, dict) or set(m) != {"param", "stage"}:
            _rej(g, f"movement[{i}]", sorted(m) if isinstance(m, dict) else type(m).__name__,
                 ["param", "stage"])
        mv.setdefault(m["param"], []).append(m["stage"])
    for name in device_data:
        want = MOVEMENT_BY_DIR[by_name[name]["dir"]]
        if mv.get(name) != want:
            _rej(g, f"movement[{name}]", mv.get(name), want)
    if set(mv) != device_data:
        _rej(g, "movement", sorted(set(mv) ^ device_data), sorted(device_data))

    # 行为源门（方案乙）：resolve 唯一行为源（表 XOR 侧车）；status_plan.checks/smoke 与源
    # 逐字复核（H3 落盘后重验也走这里，堵 F-06 篡改面）。builtin_kernel 额外核表的
    # device_signature/args/ret；cblas_call 的 args/ret 与派生结果逐字比对。
    golden = ir["golden"]
    if set(golden) != {"kind", "symbol", "body", "accum", "args", "ret"}:
        _rej(g, "golden", sorted(golden),
             ["kind", "symbol", "body", "accum", "args", "ret"])
    if golden["kind"] != "cblas":
        _rej(g, "golden.kind", golden["kind"], ["cblas"])
    if golden["body"] not in ("builtin_kernel", "cblas_call"):
        _rej(g, "golden.body", golden["body"], ["builtin_kernel", "cblas_call"])
    if not isinstance(golden["symbol"], str) or not re.match(
            r"^[A-Za-z_][A-Za-z0-9_]*$", golden["symbol"]):
        _rej(g, "golden.symbol", golden["symbol"], "C 标识符")
    if not isinstance(golden["args"], list):  # 先判类型再遍历（F-04）
        _rej(g, "golden.args", type(golden["args"]).__name__, ["list"])
    if not isinstance(golden["ret"], dict):   # 先判字典再 set()（F-04）
        _rej(g, "golden.ret", type(golden["ret"]).__name__, ["dict"])
    if set(golden["ret"]) != {"kind", "param"}:
        _rej(g, "golden.ret", sorted(golden["ret"]), ["kind", "param"])
    if (golden["body"] == "builtin_kernel") != (golden["accum"] is not None):
        _rej("param_gate", "golden.accum", golden["accum"],
             "builtin_kernel 必填 accum / cblas_call 恒 null")
    src_kind, src = resolve_behavior_source(golden["symbol"])
    behavior = src["behavior"] if src_kind == "table" else src
    if golden["body"] == "builtin_kernel":
        if src_kind != "table" or src["golden_body"] != "builtin_kernel":
            _rej("signature_table", "golden.body", golden["body"], ["builtin_kernel(表)"])
        if src["device_signature"] != [p["ctype"] for p in params]:
            _rej("signature_table", "params.ctype",
                 [p["ctype"] for p in params], src["device_signature"])
        if golden["args"] != src["args"] or golden["ret"] != src["ret"]:
            _rej("signature_table", "golden.args/ret", "≠表", "签名表 args/ret")
    else:  # cblas_call：args/ret 必须等于从 params 派生的结果（防篡改）
        d_args, d_ret = derive_cblas_golden(params)
        if golden["args"] != d_args or golden["ret"] != d_ret:
            _rej("signature_table", "golden.args/ret", "≠派生", "params 派生的 cblas args/ret")
    if ir["status_plan"]["checks"] != behavior["status_checks"]:
        _rej("signature_table", "status_plan.checks", "≠行为源", f"{src_kind} status_checks")
    if ir["smoke"] != behavior["smoke"]:
        _rej("signature_table", "smoke", "≠行为源", f"{src_kind} smoke")
    for i, a in enumerate(golden["args"]):
        if not isinstance(a, dict) or set(a) not in ({"const"}, {"param", "transform"}):
            _rej(g, f"golden.args[{i}]", sorted(a) if isinstance(a, dict)
                 else type(a).__name__, ['{"const"}', '{"param","transform"}'])
        if "const" in a and a["const"] not in GOLDEN_CONSTS:
            _rej(g, f"golden.args[{i}].const", a["const"], sorted(GOLDEN_CONSTS))
        if "param" in a:
            if a["param"] not in names:
                _rej(g, f"golden.args[{i}].param", a["param"], sorted(names))
            if a["transform"] not in set(ARG_TRANSFORM.values()):
                _rej(g, f"golden.args[{i}].transform", a["transform"],
                     sorted(set(ARG_TRANSFORM.values())))
    if golden["ret"]["kind"] not in ("out_param", "return_value"):
        _rej(g, "golden.ret.kind", golden["ret"]["kind"],
             ["out_param", "return_value"])
    sp = ir["status_plan"]
    if set(sp) != {"vocab", "checks", "sync_fail_status"}:
        _rej(g, "status_plan", sorted(sp), ["checks", "sync_fail_status", "vocab"])
    if sp["vocab"] != VOCAB:
        _rej(g, "status_plan.vocab", sp["vocab"], "公开头 12 值全集")
    if sp["sync_fail_status"] not in VOCAB:
        _rej(g, "status_plan.sync_fail_status", sp["sync_fail_status"], "vocab")
    # sync_fail_status 也须等于行为源（F-01 尾：不止 VOCAB，须逐字对行为源，防篡改）。
    if sp["sync_fail_status"] != behavior["sync_fail_status"]:
        _rej("signature_table", "status_plan.sync_fail_status",
             sp["sync_fail_status"], f"{src_kind} sync_fail_status")
    if not isinstance(sp["checks"], list):
        _rej(g, "status_plan.checks", type(sp["checks"]).__name__, ["list"])
    for i, c in enumerate(sp["checks"]):
        if not isinstance(c, dict):
            _rej(g, f"status_plan.checks[{i}]", type(c).__name__, ["dict"])
    qrs = [c for c in sp["checks"] if c.get("kind") == "quick_return"]
    if len(qrs) > 1:
        _rej(g, "status_plan.checks", f"{len(qrs)} 个 quick_return", "至多一个")
    for i, c in enumerate(sp["checks"]):
        if c.get("kind") not in CHECK_KIND_KEYS:
            _rej(g, f"status_plan.checks[{i}].kind", c.get("kind"),
                 sorted(CHECK_KIND_KEYS))
        if set(c) != CHECK_KIND_KEYS[c["kind"]]:
            _rej(g, f"status_plan.checks[{i}]", sorted(c),
                 sorted(CHECK_KIND_KEYS[c["kind"]]))
        if c["status"] not in VOCAB:
            _rej(g, f"status_plan.checks[{i}].status", c["status"], "vocab")
        if c["kind"] == "null_check" and c["param"] not in names:
            _rej(g, f"status_plan.checks[{i}].param", c["param"], sorted(names))
        if c["kind"] in ("quick_return", "cond"):
            _check_ast(c["cond_ast"], "cond",
                       f"status_plan.checks[{i}].cond_ast", names, enum_names)
        if c["kind"] == "quick_return" and not isinstance(c["writes_zero"], bool):
            _rej(g, f"status_plan.checks[{i}].writes_zero",
                 c["writes_zero"], [True, False])

    for i, v in enumerate(ir["verify"]):
        if not isinstance(v, list) or len(v) != 2:
            _rej(g, f"verify[{i}]", v, "[token, param] 二元")
        token, pname = v
        if token not in ("scalar", "full"):
            _rej(g, f"verify[{i}]", token, ["scalar", "full"])
        if pname != outs[0]["name"]:
            _rej(g, f"verify[{i}]", pname, [outs[0]["name"]])
    if golden["ret"]["kind"] == "out_param" and golden["ret"]["param"] != outs[0]["name"]:
        _rej(g, "golden.ret.param", golden["ret"]["param"], [outs[0]["name"]])

    ug = ir["upload_guard"]
    if ug != behavior.get("upload_guard"):
        _rej("signature_table", "upload_guard", ug, "行为源 upload_guard")
    if ug is not None:
        # 精确键集（_src 可选）；缺 guarded_params 即拒，不放 KeyError 逃逸（F-06）。
        if set(ug) - {"_src"} != {"kind", "guarded_params", "dim"}:
            _rej(g, "upload_guard", sorted(ug), ["dim", "guarded_params", "kind", "(_src)"])
        if ug["kind"] != "k_positive":
            _rej(g, "upload_guard.kind", ug["kind"], ["k_positive"])
        dims = {pp["name"] for pp in params if pp["role"] == "dim"}
        if ug["dim"] not in dims:
            _rej(g, "upload_guard.dim", ug["dim"], sorted(dims))
        up_set = {m["param"] for m in ir["movement"] if m["stage"] == "upload"}
        matrix_names = {pp["name"] for pp in params if pp["role"] == "matrix"}
        if not isinstance(ug["guarded_params"], list):
            _rej(g, "upload_guard.guarded_params", type(ug["guarded_params"]).__name__, ["list"])
        for gp in ug["guarded_params"]:
            if gp not in names:
                _rej(g, "upload_guard.guarded_params", gp, sorted(names))
            if gp not in up_set:
                _rej(g, "upload_guard.guarded_params", gp, "须 ⊆ upload movement")
            # guarded 参数须为 matrix（k_positive 守卫的是依赖维度的矩阵上卡；对齐文档 §11a）。
            if gp not in matrix_names:
                _rej(g, "upload_guard.guarded_params", gp, "须为 matrix 角色")
    smoke = ir["smoke"]
    if smoke is not None:
        if set(smoke) != {"null_handle"}:
            _rej(g, "smoke", sorted(smoke), ["null_handle"])
        nh = smoke["null_handle"]
        if not isinstance(nh, dict) or set(nh) != {"expect", "args"}:
            _rej(g, "smoke.null_handle", sorted(nh) if isinstance(nh, dict)
                 else type(nh).__name__, ["args", "expect"])
        if nh["expect"] not in VOCAB:
            _rej(g, "smoke.null_handle.expect", nh["expect"], "vocab")
        if not isinstance(nh["args"], list) or len(nh["args"]) != len(params):
            _rej(g, "smoke.null_handle.args",
                 len(nh["args"]) if isinstance(nh["args"], list) else type(nh["args"]).__name__,
                 len(params))
        if nh["args"][0] is not None:
            _rej(g, "smoke.null_handle.args[0]", nh["args"][0], [None])
