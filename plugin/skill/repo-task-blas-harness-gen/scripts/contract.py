#!/usr/bin/env python3
"""compile_contract —— H2 契约编译：H1 快照 → Contract IR（contract-ir.md v3）。

Contract IR（下称 IR）是机器可读的可执行契约，给本 skill 的 C++ 生成后端消费：
生成的 C++ 按它读列、按它调 golden、按它做 verify 与状态绑定；发射器只消费不判定。
IR 是内部产物，不进交付件；accept 对本 skill 零感知。

输入是 H1 的 package.json 快照（{"facts":…, "column_specs":…}，含哈希不进 IR）。
facts 须已通过 package_loader 三道门（ABI 版本门、装载纪律、同源断言）与预筛；FACTS
的语义校验在包渲出时已由 case-gen 执行过，本 skill 不重跑。编译过程本身执行参数门/
AST 门/签名表门（ir_validator），产出后由 validate_ir 收尾核结构与跨段不变量。
"""

from __future__ import annotations

import ir_validator as iv

# IR 自己的版本，与 FACTS 的 schema_version 独立演化；IR 结构一变就加一。
CONTRACT_VERSION = 3


def _shape(src):
    return iv.translate_shape_expr(src)


def _translate_shapes(facts):
    """ast_gate 预翻译：全部形状表达式先过文法门（§0 层序：先于签名表门）。"""
    asts = {}
    for p in facts["params"]:
        entry = {}
        for key in ("len", "inc", "rows", "cols", "ld"):
            if key in p:
                entry[key] = _shape(p[key])
        asts[p["name"]] = entry
    return asts


def _param_ir(p, asts, table_entry):
    role = p["role"]
    out = {"name": p["name"], "ctype": p["ctype"], "role": role,
           "dir": p.get("dir") or iv.DIR_ASSIGN[role],
           "mem": iv.MEM_ASSIGN[role]}
    if role in ("vector", "matrix", "scalar", "out_scalar"):
        out["dtype"] = p["dtype"]
    if role == "enum":
        out["enum"] = {"values": list(p["values"]), "enum_kind": p["enum_kind"]}
    if role == "layout":
        out["layout"] = {"kind": p["kind"], "of": p["of"]}
    if role == "vector":
        out["shape"] = {"len_ast": asts[p["name"]]["len"]}
    if role == "matrix":
        rows, cols = asts[p["name"]]["rows"], asts[p["name"]]["cols"]
        swap_on = (table_entry.get("matrix_phys") or {}).get(p["name"])
        if swap_on:
            cond = ["eq", ["ref", swap_on], ["str", "N"]]
            rows, cols = (["cond", cond, rows, cols], ["cond", cond, cols, rows])
        out["order"] = p.get("order", "col_major")
        out["storage"] = p.get("storage", "full")
        out["shape"] = {"rows_ast": rows, "cols_ast": cols,
                        "ld_ast": asts[p["name"]]["ld"]}
    return out


def _span(p_ir, facts_p):
    if p_ir["role"] == "out_scalar":
        return ["int", 1]
    if p_ir["role"] == "vector":
        len_ast = p_ir["shape"]["len_ast"]
        inc = facts_p.get("inc")
        if inc:
            abs_inc = ["max", ["ref", inc], ["sub", ["int", 0], ["ref", inc]]]
            return ["add", ["mul", ["sub", len_ast, ["int", 1]], abs_inc], ["int", 1]]
        return len_ast
    s = p_ir["shape"]
    return ["mul", ["max", ["int", 1], s["ld_ast"]],
            ["max", ["int", 1], s["cols_ast"]]]


def compile_contract(snapshot):
    """H1 快照 → IR。拒绝以 ir_validator.ContractReject / 停机码语义抛出。"""
    if not isinstance(snapshot, dict) or "facts" not in snapshot \
            or "column_specs" not in snapshot:
        raise ValueError("snapshot 须含 facts 与 column_specs（H1 package.json 形）")
    facts, specs = snapshot["facts"], snapshot["column_specs"]
    if not specs:
        raise ValueError("column_specs 为空：必须传入包 ABI 投影出的完整列清单")
    iv.validate_facts(facts)                      # 1. 参数门
    asts = _translate_shapes(facts)               # 2. AST 门（表达式预翻译）
    symbol = facts["golden"]["symbol"]
    src_kind, src = iv.resolve_behavior_source(symbol)  # 3. 行为源门（表 XOR 侧车）
    # matrix_phys（换位依赖）：表条目带；侧车（cblas_call）从 params 的 shape 引用推不出，
    # 侧车须显式给（sger 无换位→空）。语义闭合：键必须是 matrix 参数、值必须是 enum 参数，
    # 否则拼写错的条目会被静默忽略而生成语义错误的转置 harness（fail-open）。
    entry = src if src_kind == "table" else {"matrix_phys": src.get("matrix_phys", {})}
    mp = entry.get("matrix_phys") or {}
    _mtx = {p["name"] for p in facts["params"] if p.get("role") == "matrix"}
    _enm = {p["name"] for p in facts["params"] if p.get("role") == "enum"}
    for k, v in mp.items():
        if k not in _mtx:
            iv._rej("signature_table", "matrix_phys.key", k, sorted(_mtx))
        # v 先判 str 再入集，非字符串（list/dict 等）不放 TypeError 逃逸（fail-closed）
        if not isinstance(v, str) or v not in _enm:
            iv._rej("signature_table", f"matrix_phys[{k}]", v, sorted(_enm))
    params = [_param_ir(p, asts, entry) for p in facts["params"]]
    by_name = {p["name"]: p for p in params}
    facts_by_name = {p["name"]: p for p in facts["params"]}

    columns = []
    for spec in specs:
        kind = iv.KIND_MAP.get(spec["kind"], spec["kind"])
        if spec.get("source"):
            binds = {"param": spec["source"]}
        elif kind == "random_seed":
            binds = {"seed": True}
        else:
            binds = {"base": kind}
        columns.append({"name": spec["name"], "kind": kind, "binds": binds,
                        "reader": iv.READER_BY_KIND[kind]})

    device = [p for p in params if p["role"] in ("vector", "matrix", "out_scalar")]
    buffers = [{"param": p["name"], "span_ast": _span(p, facts_by_name[p["name"]]),
                "dtype": p["dtype"], "mem": p["mem"], "dir": p["dir"]}
               for p in device]
    movement = []
    for p in device:
        for stage in iv.MOVEMENT_BY_DIR[p["dir"]]:
            movement.append({"param": p["name"], "stage": stage})

    outs = [p for p in params if p["dir"] in ("out", "inout")]
    token = "scalar" if outs[0]["role"] == "out_scalar" else "full"

    # golden：builtin_kernel 从表取；cblas_call 的 args/ret 从 params 派生（方案甲）。
    body = src.get("golden_body", "cblas_call")
    if body == "builtin_kernel":
        g_args, g_ret, g_accum = src["args"], src["ret"], src["accum"]
    else:
        g_args, g_ret = iv.derive_cblas_golden(params)
        g_accum = None
    behavior = src["behavior"] if src_kind == "table" else src
    upload_guard = behavior.get("upload_guard")  # v3：物化搬运守卫（sger=None）
    ir = {
        "version": CONTRACT_VERSION,
        "facts_schema_version": facts["schema_version"],
        "op": facts["op"], "family": facts["family"], "symbol": facts["symbol"],
        "returns": facts["returns"],
        "params": params, "columns": columns,
        "buffers": buffers, "movement": movement,
        "golden": {"kind": "cblas", "symbol": symbol, "body": body,
                   "accum": g_accum, "args": g_args, "ret": g_ret},
        "verify": [[token, outs[0]["name"]]],
        "status_plan": {"vocab": list(iv.VOCAB),
                        "checks": behavior["status_checks"],
                        "sync_fail_status": behavior["sync_fail_status"]},
        "smoke": behavior["smoke"],
        "upload_guard": upload_guard,
    }
    iv.validate_ir(ir)  # IR 结构门 + §12a 跨段不变量收尾
    return ir
