#!/usr/bin/env python3
"""renderer —— H3 渲染后端：Contract IR → overlay 五件 C++（contract-ir.md +
template-contract.md 的可执行体）。

发射器只消费 IR、签名表、本模块内的模板常量；不查算子名、不做隐式推断。同一 IR 两次
渲染逐字节相同（确定性契约，R2）。sasum 输出逐字节复现 spike overlay-rev1（对拍基准）。

两条发射路径：builtin_kernel golden 走 L1 逐字节路径（sasum，逐字节复现 rev1）；
cblas_call golden 走通用数据驱动路径（sgemm、sger——按 params/buffers/movement/args/
status_plan/upload_guard/verify 遍历，无算子名特判）。L1 路径外若 golden 也非 cblas_call
则 UNSUPPORTED_CONTRACT。

入口：render_overlay(ir, csv_bytes) -> {相对路径: bytes}
"""

from __future__ import annotations

import ir_validator as iv

# ---------------------------------------------------------------------------
# 版权头（两种注释风格；逐字来自 overlay-rev1，随 skill 分发）
# ---------------------------------------------------------------------------
_LICENSE_LINES = [
    "Copyright (c) 2026 Huawei Technologies Co., Ltd.",
    "This program is free software, you can redistribute it and/or modify it under the terms "
    "and conditions of",
    'CANN Open Software License Agreement Version 2.0 (the "License").',
    "Please refer to the License for details. You may not use this file except in compliance "
    "with the License.",
    'THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER '
    "EXPRESS OR IMPLIED,",
    "INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A "
    "PARTICULAR PURPOSE.",
    "See LICENSE in the root of the software repository for the full text of the License.",
]

CTYPE_MEMBER = {"int": "int64_t", "const float*": "BlasFillMode", "float*": "float"}


def _c_header() -> str:
    body = "\n".join(" * " + ln for ln in _LICENSE_LINES)
    return "/**\n" + body + "\n */\n"


def _cmake_header() -> str:
    dashes = "# " + "-" * 106
    body = "\n".join("# " + ln for ln in _LICENSE_LINES)
    return dashes + "\n" + body + "\n" + dashes + "\n"


def _class_name(ir) -> str:
    op = ir["op"]
    arch = _arch_dir(ir)
    return op[:1].upper() + op[1:] + arch[:1].upper() + arch[1:] + "Test"


def _arch_dir(ir) -> str:
    # arch_dir 不进 IR（渲染参数）；sasum spike 定 arch22。发射时由调用方经 ir 附带。
    return ir.get("_arch_dir", "arch22")


def _param_struct(ir) -> str:
    op = ir["op"]
    struct = op[:1].upper() + op[1:] + "Param"
    # 成员声明序 = 读列序 = 列投影序（binds.param 出现序）；成员名 = 被绑定 param 名。
    # 类型/读法按列 reader 派发（非 ctype，避开 const float* 三义碰撞）。
    role_of = {pp["name"]: pp["role"] for pp in ir["params"]}
    members, reads = [], []
    seen = set()
    for col in ir["columns"]:
        b = col["binds"]
        if "param" not in b:
            continue
        pname = b["param"]
        if pname in seen:
            continue
        seen.add(pname)
        reader = col["reader"]
        mtype = _member_type(reader)
        if reader == "int64":
            init = "0" if role_of[pname] == "dim" else "1"  # dim→0, layout→1
        else:
            init = _member_init(reader)
        members.append(f"    {mtype} {pname} = {init};")
        reads.append(f'        {pname} = {_reader_parse(reader, col["name"])};')
    return struct, "\n".join(members), "\n".join(reads)


def _wrap_comment(prose: str, width: int = 100) -> str:
    """把一段中文说明按显示宽度折成 // 注释行（确定性；中文字符计 2 宽）。"""
    def w(s):
        return sum(2 if ord(ch) > 0x2500 else 1 for ch in s)
    lines, cur = [], ""
    for ch in prose:
        if w("// " + cur + ch) > width:
            lines.append("// " + cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append("// " + cur)
    return "\n".join(lines)


_L1_ROLES = {"handle", "dim", "layout", "vector", "out_scalar"}


def _assert_renderable(ir):
    """L1 形硬门：本形外一律 UNSUPPORTED_CONTRACT（真 raise，-O 下不失效）。"""
    for i, pp in enumerate(ir["params"]):
        if pp["role"] not in _L1_ROLES:
            raise iv.ContractReject("ast_gate", f"params[{i}].role", pp["role"],
                                    "L1 形支持角色 " + str(sorted(_L1_ROLES))
                                    + "（矩阵/enum/scalar 随 Step 4）")
        if pp["ctype"] not in CTYPE_MEMBER and pp["role"] not in ("handle",):
            raise iv.ContractReject("param_gate", f"params[{i}].ctype", pp["ctype"],
                                    "L1 形支持的 ctype " + str(sorted(CTYPE_MEMBER)))
    if ir["verify"][0][0] != "scalar":
        raise iv.ContractReject("param_gate", "verify[0]", ir["verify"][0][0],
                                ["scalar"] )
    if ir["golden"]["body"] != "builtin_kernel":
        raise iv.ContractReject("signature_table", "golden.body", ir["golden"]["body"],
                                ["builtin_kernel"])


# 枚举 cxx 映射（enum_kind → {CSV token: C++ 枚举 token}）；op 轴的转置枚举。
ENUM_CXX = {"op": {"N": "ACLBLAS_OP_N", "T": "ACLBLAS_OP_T", "C": "ACLBLAS_OP_C"}}


def render_ast(node, prefix="p.", enum_ref=None, wide=False):
    """标签数组 AST → C++ 表达式串（通用；shape 与 cond 共用；contract-ir.md §4）。
    prefix：ref 前缀（test 结构体用 "p."，wrapper/golden 用 "" 裸参数）。
    enum_ref：{参数名: enum_kind}，用于把 eq(enum_ref, str) 里的 str 渲成 C++ 枚举 token。
    wide：span/尺寸上下文置真——整型 ref 渲成 static_cast<int64_t>(...)，使 (dim-1)*|inc|
    这类乘积在 int64 中运算，绝不触发 32 位有符号溢出（含 abs(INT_MIN)）；cond 上下文置假。"""
    enum_ref = enum_ref or {}
    tag = node[0]
    if tag == "int":
        return str(node[1])
    if tag == "ref":
        base = prefix + node[1]
        return f"static_cast<int64_t>({base})" if wide else base
    if tag == "str":
        return '"' + node[1] + '"'
    if tag in ("add", "sub", "mul"):
        op = {"add": " + ", "sub": " - ", "mul": " * "}[tag]
        return ("(" + render_ast(node[1], prefix, enum_ref, wide) + op
                + render_ast(node[2], prefix, enum_ref, wide) + ")")
    if tag == "max":
        return ("std::max<int64_t>(" + render_ast(node[1], prefix, enum_ref, wide) + ", "
                + render_ast(node[2], prefix, enum_ref, wide) + ")")
    if tag in ("le", "lt", "eq"):
        op = {"le": " <= ", "lt": " < ", "eq": " == "}[tag]
        # eq(enum ref, str)：str 渲成 C++ 枚举 token
        if tag == "eq":
            a, b = node[1], node[2]
            for x, y in ((a, b), (b, a)):
                if x[0] == "ref" and x[1] in enum_ref and y[0] == "str":
                    tok = ENUM_CXX[enum_ref[x[1]]][y[1]]
                    return "(" + prefix + x[1] + " == " + tok + ")"
        return ("(" + render_ast(node[1], prefix, enum_ref, wide) + op
                + render_ast(node[2], prefix, enum_ref, wide) + ")")
    if tag in ("or", "and"):
        op = {"or": " || ", "and": " && "}[tag]
        return ("(" + render_ast(node[1], prefix, enum_ref, wide) + op
                + render_ast(node[2], prefix, enum_ref, wide) + ")")
    if tag == "cond":
        return ("(" + render_ast(node[1], prefix, enum_ref, wide) + " ? "
                + render_ast(node[2], prefix, enum_ref, wide) + " : "
                + render_ast(node[3], prefix, enum_ref, wide) + ")")
    raise iv.ContractReject("ast_gate", "render", tag, "已知 AST 节点")


def _enum_ref(ir):
    return {p["name"]: p["enum"]["enum_kind"] for p in ir["params"]
            if p["role"] == "enum"}


# 角色 → param.h 成员 C++ 类型（不按 ctype，避开 const float* 的三义碰撞）
def _member_type(reader):
    return {"int64": "int64_t", "fill": "BlasFillMode", "float": "float",
            "enum": "aclblasOperation_t"}[reader]


def _member_init(reader):
    return {"int64": "0", "fill": 'parseFill("RANDOM_10")', "float": "0.0f",
            "enum": "ACLBLAS_OP_N"}[reader]


def _reader_parse(reader, key):
    return {"int64": f'parseInt64(require("{key}"))',
            "fill": f'parseFill(require("{key}"))',
            "float": f'parseFloat(require("{key}"))',
            "enum": f'parseOpTrans(require("{key}"))'}[reader]


def _load_table():
    import json
    from pathlib import Path
    return json.loads(
        (Path(__file__).resolve().parent / "signature_table.json").read_text("utf-8"))


def render_param_h(ir) -> str:
    struct, members, reads = _param_struct(ir)
    cols = [c["name"] for c in ir["columns"] if "param" in c["binds"]]
    base_cols = [c["name"] for c in ir["columns"] if "base" in c["binds"]
                 or c["binds"].get("seed")]
    prose = ("本任务包的投影列在此显式读取：" + "、".join(cols) + "；基座列（"
             + "/".join(base_cols) + "）由 BlasTestParamBase 读取。包契约（README）："
             "投影列缺失或为空必须抛错，不回退 ReadMap 默认值（fail-closed）。")
    note = _wrap_comment(prose)
    return (
        _c_header() + "\n#pragma once\n\n"
        "#include <cstdint>\n#include <stdexcept>\n#include <string>\n\n"
        '#include "acl/acl.h"\n#include "cann_ops_blas.h"\n#include "csv_loader.h"\n\n'
        + note + "\n"
        f"struct {struct} : public BlasTestParamBase {{\n"
        + members + "\n\n"
        f"    {struct}(const csv_map& map) : BlasTestParamBase(map)\n    {{\n"
        "        auto require = [&](const char* key) -> std::string {\n"
        "            std::string value = ReadMap(map, key);\n"
        "            if (value.empty()) {\n"
        '                throw std::runtime_error(std::string("missing or empty CSV column: ")'
        " + key);\n"
        "            }\n"
        "            return value;\n"
        "        };\n"
        + reads + "\n    }\n};\n")


def render_golden_h(ir) -> str:
    # 状态检查从已验 IR 消费（方案乙）；kernel_block 仍从签名表取（builtin 信任边界）。
    entry = _load_table()[ir["golden"]["symbol"]]
    checks = ir["status_plan"]["checks"]
    lines = []
    for c in checks:
        if c["kind"] == "null_check":
            lines.append(f'    if ({c["param"]} == nullptr)\n        return {c["status"]};')
        elif c["kind"] == "quick_return":
            lines.append("    if (n <= 0 || incx <= 0) {\n        *result = 0.0f;\n"
                         f"        return {c['status']};\n    }}")
    kernel = entry["behavior"]["kernel_block"].rstrip("\n")
    sig = ("aclblasHandle_t handle, const int64_t n, const float* x, "
           "const int64_t incx, float* result")
    return (
        _c_header() + "\n#pragma once\n\n#include <cmath>\n#include <cstdint>\n\n"
        '#include "acl/acl.h"\n#include "cann_ops_blas.h"\n\n'
        f'inline aclblasStatus_t {ir["symbol"]}_cpu(\n    {sig})\n{{\n'
        + "\n".join(lines) + "\n\n" + kernel + "\n"
        "    return ACLBLAS_STATUS_SUCCESS;\n}\n")


def render_wrapper_h(ir) -> str:
    sig = ("aclblasHandle_t handle, const int64_t n, const float* x, "
           "const int64_t incx, float* result")
    return (
        _c_header() + "\n#pragma once\n\n"
        "#include <algorithm>\n#include <cmath>\n#include <cstdint>\n#include <memory>\n\n"
        '#include "acl/acl.h"\n#include "cann_ops_blas.h"\n#include "device.h"\n\n'
        "inline std::unique_ptr<DeviceBuffer> tryAllocAndCopySasum(const void* hostPtr, "
        "size_t bytes)\n{\n"
        "    if (hostPtr == nullptr)\n        return nullptr;\n"
        "    auto buf = std::make_unique<DeviceBuffer>(bytes);\n"
        "    buf->copyFromHost(hostPtr, bytes);\n    return buf;\n}\n\n"
        "// rev1（Step 2 冻结基准修订）：收敛为单一 aclblasSasum 调用点，消 A2' 调用计数歧义；\n"
        "// 同步与回读仅在 ret==SUCCESS 时执行（API 错误码不被 INTERNAL_ERROR 覆盖）。\n"
        "// 候选对拍基准：Step 3 真机复验通过后方生效。\n"
        f'inline aclblasStatus_t {ir["symbol"]}_npu(\n    {sig})\n{{\n'
        "    if (handle == nullptr) {\n        return ACLBLAS_STATUS_NOT_INITIALIZED;\n    }\n"
        "    const bool quickReturn = n <= 0 || incx <= 0;\n"
        "    const size_t dataBytes = quickReturn ? 0 : static_cast<size_t>"
        "((n - 1) * incx + 1) * sizeof(float);\n\n"
        "    auto dX = quickReturn ? nullptr : tryAllocAndCopySasum(x, dataBytes);\n"
        "    auto dResult =\n        (result != nullptr) ? std::make_unique<DeviceBuffer>"
        "(sizeof(float)) : nullptr;\n\n"
        f'    aclblasStatus_t ret = {ir["symbol"]}(\n'
        "        handle, static_cast<int>(n), dX ? static_cast<const float*>(dX->ptr()) : "
        "nullptr,\n        static_cast<int>(incx), dResult ? static_cast<float*>"
        "(dResult->ptr()) : nullptr);\n\n"
        "    if (ret == ACLBLAS_STATUS_SUCCESS) {\n"
        "        if (aclrtSynchronizeDevice() != ACL_SUCCESS)\n"
        "            return ACLBLAS_STATUS_INTERNAL_ERROR;\n"
        "        if (dResult)\n            dResult->copyToHost(result, sizeof(float));\n    }\n\n"
        "    return ret;\n}\n")


def render_cmake(ir) -> str:
    return _cmake_header() + "\nops_blas_add_gtest_tests(${OPS_BLAS})\n"


_RULE = "// " + "-" * 75


def render_test_cpp(ir) -> str:
    cls = _class_name(ir)
    _s, _m, _r = _param_struct(ir)
    struct = _s
    op = ir["op"]
    sym = ir["symbol"]
    smoke = ir["smoke"]
    # 冒烟实参：按 §9 物化（本 skill 当前只支持 scalar-verify L1 形）
    sargs = []
    for a in smoke["null_handle"]["args"]:
        if a is None:
            sargs.append("nullptr")
        elif a == "&local":
            sargs.append("&result")
        elif isinstance(a, list) and a[0] == "int":
            sargs.append(str(a[1]))
    smoke_call = f'{sym}_npu({", ".join(sargs)})'
    verify_token = ir["verify"][0][0]
    if verify_token != "scalar":
            raise iv.ContractReject("param_gate", "verify[0]", verify_token, ["scalar"])
    head = (
        _c_header() + "\n#include <cmath>\n#include <cstdint>\n#include <vector>\n\n"
        '#include "verify.h"\n#include "blas_test.h"\n#include "csv_loader.h"\n'
        f'#include "{op}_param.h"\n#include "{op}_golden.h"\n'
        f'#include "{op}_npu_wrapper.h"\n\n'
        f"class {cls} : public BlasTest<{struct}> {{}};\n\n"
        f"TEST_F({cls}, NullHandle)\n{{\n    float result = 0.0f;\n"
        f"    aclblasStatus_t ret = {smoke_call};\n"
        f'    EXPECT_EQ(ret, {smoke["null_handle"]["expect"]});\n}}\n\n'
        f"INSTANTIATE_TEST_SUITE_P(\n    {op[:1].upper()+op[1:]}, {cls}, "
        f"::testing::ValuesIn(GetCasesFromCsv<{struct}>(ReplaceFileExtension2Csv(__FILE__))),\n"
        f"    PrintCaseInfoString<{struct}>);\n\n")
    err = (
        _RULE + "\n// Error path test (expectResult != SUCCESS)\n" + _RULE + "\n"
        f"static void TestErrorPath(const {struct}& p, aclblasHandle_t handle)\n{{\n"
        "    const int64_t xLen = (p.n > 0 && p.x.method != BlasFillMode::M_NULLPTR) ? "
        "p.n : 0;\n"
        "    std::vector<float> xHost = makeBlasArray(xLen, p.x, p.randomSeed);\n"
        "    const float* xPtr = xHost.empty() ? nullptr : xHost.data();\n\n"
        "    float result = 0.0f;\n"
        "    float* resultPtr = &result; // 本任务包无 result 列（无 NULLPTR 期望行）\n\n"
        f"    aclblasStatus_t ret = {sym}_npu(handle, p.n, xPtr, p.incx, resultPtr);\n"
        "    EXPECT_EQ(static_cast<int>(ret), static_cast<int>(p.expectResult));\n}\n\n")
    noop = (
        _RULE + "\n// Quick-return path (n <= 0 or incx <= 0)\n" + _RULE + "\n"
        f"static void TestNoOpPath(const {struct}& p, aclblasHandle_t handle)\n{{\n"
        "    const int64_t xLen = (p.n > 0) ? p.n : 0;\n"
        "    std::vector<float> xHost = makeBlasArray(xLen, p.x, p.randomSeed);\n"
        "    const float* xPtr = xHost.empty() ? nullptr : xHost.data();\n\n"
        "    float result = 123.0f;\n"
        f"    aclblasStatus_t ret = {sym}_npu(handle, p.n, xPtr, p.incx, &result);\n"
        "    EXPECT_EQ(static_cast<int>(ret), static_cast<int>(p.expectResult));\n"
        "    if (ret == ACLBLAS_STATUS_SUCCESS) {\n"
        '        EXPECT_FLOAT_EQ(result, 0.0f) << "[" << p.caseName << "] early return should '
        'produce result=0.0f, got "\n                                      << result;\n    }\n}\n\n')
    vfy = (
        _RULE + "\n// Precision verification helper\n" + _RULE + "\n"
        f"static void Verify{op[:1].upper()+op[1:]}Result(float result, float golden, "
        "const std::string& caseName)\n{\n"
        "    VerifyConfig cfg;\n    applyMixedTolerance(cfg, ACL_FLOAT, golden);\n"
        "    EXPECT_TRUE(Verifier::verifyScalar(result, golden, cfg, caseName));\n}\n\n")
    norm = (
        _RULE + "\n// Normal path test helper\n" + _RULE + "\n"
        f"static void TestNormalPath(const {struct}& p, aclblasHandle_t handle)\n{{\n"
        "    const int64_t xLen = static_cast<int64_t>((p.n - 1) * p.incx + 1);\n"
        "    std::vector<float> xHost = makeBlasArray(xLen, p.x, p.randomSeed);\n\n"
        "    float result = 0.0f;\n"
        f"    aclblasStatus_t ret = {sym}_npu(handle, p.n, xHost.data(), p.incx, &result);\n"
        "    EXPECT_EQ(static_cast<int>(ret), static_cast<int>(p.expectResult));\n"
        "    if (ret != ACLBLAS_STATUS_SUCCESS)\n        return;\n\n"
        "    float golden = 0.0f;\n"
        f"    {sym}_cpu(handle, p.n, xHost.data(), p.incx, &golden);\n\n"
        f"    Verify{op[:1].upper()+op[1:]}Result(result, golden, p.caseName);\n}}\n\n")
    main = (
        _RULE + "\n// Main parameterized test\n" + _RULE + "\n"
        f"TEST_P({cls}, CsvDriven)\n{{\n    const auto& p = GetParam();\n\n"
        "    if (p.expectResult != ACLBLAS_STATUS_SUCCESS) {\n"
        f"        TestErrorPath(p, {cls}::handle_);\n"
        "    } else if (p.n <= 0 || p.incx <= 0) {\n"
        f"        TestNoOpPath(p, {cls}::handle_);\n    }} else {{\n"
        f"        TestNormalPath(p, {cls}::handle_);\n    }}\n}}\n")
    return head + err + noop + vfy + norm + main


def render_overlay(ir, csv_bytes: bytes) -> dict:
    """IR + CSV 字节 → {overlay 相对路径: bytes}。确定性：同输入两次调用逐字节相同。
    非 L1 形先经 _assert_renderable 以 UNSUPPORTED_CONTRACT 停机。"""
    import re as _re
    for k in ("op", "family"):
        if not _re.match(r"^[a-z][a-z0-9_]*$", ir[k]):
            raise iv.ContractReject("param_gate", k, ir[k], "^[a-z][a-z0-9_]*$")
    arch = _arch_dir(ir)
    op = ir["op"]
    root = f"test/{ir['family']}/{op}"
    if _is_l1(ir):  # builtin_kernel → L1 逐字节路径（sasum）
        _assert_renderable(ir)
        golden = render_golden_h(ir)
        wrapper = render_wrapper_h(ir)
        test = render_test_cpp(ir)
    else:  # cblas_call → 通用数据驱动路径（sgemm、sger）
        golden = render_golden_cblas(ir)
        wrapper = render_wrapper_cblas(ir)
        test = render_test_cblas(ir)
    return {
        f"{root}/CMakeLists.txt": render_cmake(ir).encode(),
        f"{root}/{op}_param.h": render_param_h(ir).encode(),
        f"{root}/{op}_golden.h": golden.encode(),
        f"{root}/{arch}/{op}_test.cpp": test.encode(),
        f"{root}/{arch}/{op}_npu_wrapper.h": wrapper.encode(),
        f"{root}/{arch}/{op}_test.csv": csv_bytes,
    }


# ===========================================================================
# 通用 cblas_call 渲染路径（L3：矩阵/向量混合；sgemm、sger 走此路，sasum 仍走 L1）
# ===========================================================================

def _sig(params):
    """device 签名参数串（按 IR params 的 ctype/name）。"""
    return ", ".join(f'{p["ctype"]} {p["name"]}' for p in params)


def _render_checks(ir):
    """状态检查（null_check/cond/quick_return）→ C++ 行；裸参数前缀 + enum token。"""
    er = _enum_ref(ir)
    out = []
    for c in ir["status_plan"]["checks"]:
        if c["kind"] == "null_check":
            out.append(f'    if ({c["param"]} == nullptr)\n        return {c["status"]};')
        else:
            expr = render_ast(c["cond_ast"], prefix="", enum_ref=er)
            out.append(f'    if {expr}\n        return {c["status"]};')
    return "\n".join(out)


def _cblas_call_expr(ir):
    """cblas_<op>(<args>)：按 golden.args 的 const/param+transform 渲染。"""
    parts = []
    for a in ir["golden"]["args"]:
        if "const" in a:
            parts.append(a["const"])
        else:
            name, tf = a["param"], a["transform"]
            if tf == "pass":
                parts.append(name)
            elif tf == "cast_int":
                parts.append(f"static_cast<int>({name})")
            elif tf == "deref":
                parts.append(f"*{name}")
            elif tf == "enum_map":
                parts.append(f"ToCblasOp({name})")
    return ir["golden"]["symbol"] + "(" + ", ".join(parts) + ")"


def render_golden_cblas(ir) -> str:
    params = ir["params"]
    out = [p for p in params if p["dir"] in ("out", "inout")][0]
    return (
        _c_header() + "\n#pragma once\n\n#include <algorithm>\n#include <climits>\n\n"
        '#include "acl/acl.h"\n#include "cann_ops_blas.h"\n#include "cblas_compat.h"\n\n'
        f'inline aclblasStatus_t {ir["symbol"]}_cpu(\n    {_sig(params)})\n{{\n'
        + _render_checks(ir) + "\n\n"
        + f"    {_cblas_call_expr(ir)};\n    return ACLBLAS_STATUS_SUCCESS;\n}}\n")


# 搬运尺寸安全上限（元素数）：任何 device buffer 的 span 超过它即视为非法/退化，走一元素
# 哨兵搬运而非按危险 span 分配。约 1.3 亿元素（float 约 512MB），远高于任何真用例、远低于
# INT_MIN 步长派生的天量 span。测试与 wrapper 用同一常量、同一 span 公式，尺寸口径一致。
_SPAN_CAP = "1LL << 27"
# 全体 device buffer 元素总量上限（约 2.7 亿元素 ≈ 1GB float）：防多缓冲各自 < 单 buffer 上限
# 却叠加成 OOM（Step 4/5 复核点）。对**所有行**（含错误/非 full，它们同样真造数真上卡）守;
# 超限报 harness 基础设施错误（非设备状态）。
_TOTAL_CAP = "1LL << 28"


def _span_size_lines(ir, prefix, indent="    "):
    """为每个 device buffer 发射 span_/ok_/elems_ 三行（int64 checked span → 哨兵或全量）。
    prefix：wrapper 用 ""（裸参数），test 用 "p."。span 以 wide=True 渲染，全程 int64 运算，
    绝不触发 32 位溢出（含 abs(INT_MIN)）；ok 判合法且 ≤ 上限；elems 非法即降为 1（哨兵）。"""
    er = _enum_ref(ir)
    out = []
    for b in ir["buffers"]:
        nm = b["param"]
        expr = render_ast(b["span_ast"], prefix=prefix, enum_ref=er, wide=True)
        out.append(f"{indent}const int64_t span_{nm} = {expr};")
        out.append(f"{indent}const bool ok_{nm} = (span_{nm} >= 1 && span_{nm} <= kMaxSpanElems);")
        # elems 在 test 中对矩阵输入用不到（其 host 由 makeBlasMatrix 定尺寸），标 maybe_unused
        # 免 -Werror；wrapper 里恒用于搬运字节数。
        out.append(f"{indent}[[maybe_unused]] const size_t elems_{nm} = "
                   f"ok_{nm} ? static_cast<size_t>(span_{nm}) : 1;")
    return out


def render_wrapper_cblas(ir) -> str:
    """通用 cblas_call wrapper（A′：设备权威 + 语义保持的降级搬运）。

    - handle 前置空检查是唯一宿主安全例外（验的是 harness 安全契约，非设备 null-handle 行为）；
      其余一切状态（含无效维度/步长/lda 与 quick-return）均由真设备裁决并原样返回。
    - 每 device buffer 按 int64 checked span 定尺寸：合法且 ≤ 上限才全量搬运，否则一元素哨兵——
      绝不按危险 span（INT_MIN/退化维度/超大）分配或搬运；M_NULLPTR 保持传 nullptr，指针语义保真。
    - 恒一个设备调用点；仅在“全量搬运（ok）且设备返回 SUCCESS”时回读，探针即便意外 SUCCESS
      也不按 span 回读（否则把设备的状态错误放大成宿主越界/OOM）。"""
    params = ir["params"]
    bufs = {b["param"]: b for b in ir["buffers"]}
    guard = ir["upload_guard"] or {}
    guarded = set(guard.get("guarded_params", []))
    kdim = guard.get("dim")  # 守卫维度显式来自 IR（不再硬搜 "k"）
    up = [m["param"] for m in ir["movement"] if m["stage"] == "upload"]
    allocs = [m["param"] for m in ir["movement"] if m["stage"] == "alloc_out"]
    reads = [m["param"] for m in ir["movement"] if m["stage"] == "readback"]
    checks = ir["status_plan"]["checks"]
    hname = next(p["name"] for p in params if p["role"] == "handle")  # 按角色，非字面名
    hcheck = next((c for c in checks
                   if c["kind"] == "null_check" and c["param"] == hname), None)
    lines = [f"    static constexpr int64_t kMaxSpanElems = {_SPAN_CAP};"]
    if hcheck is not None:
        lines.append(f"    if ({hname} == nullptr) {{")
        lines.append(f"        return {hcheck['status']};  "
                     "// 宿主安全契约（非设备 null-handle 行为）")
        lines.append("    }")
    lines += _span_size_lines(ir, prefix="")
    for p in params:  # device buffer 指针，缺省 nullptr（M_NULLPTR 保持 nullptr）
        if p["name"] in bufs and p["mem"] == "device":
            lines.append(f"    std::unique_ptr<DeviceBuffer> d_{p['name']};")
            lines.append(f"    float* p_{p['name']} = nullptr;")
    for name in up:  # 上卡（inout 也上卡）：只搬 elems_<name>（哨兵或全量），带 guard 抑制
        cond = f"{name} != nullptr"
        if name in guarded and kdim:
            cond += f" && {kdim} > 0"
        lines.append(f"    if ({cond}) {{")
        lines.append(f"        const size_t bytes_{name} = elems_{name} * sizeof(float);")
        lines.append(f"        d_{name} = std::make_unique<DeviceBuffer>(bytes_{name});")
        lines.append(f"        d_{name}->copyFromHost({name}, bytes_{name});")
        lines.append(f"        p_{name} = static_cast<float*>(d_{name}->ptr());")
        lines.append("    }")
    for name in allocs:  # 纯输出分配（alloc_out）
        lines.append(f"    if ({name} != nullptr) {{")
        lines.append(f"        d_{name} = std::make_unique<DeviceBuffer>"
                     f"(elems_{name} * sizeof(float));")
        lines.append(f"        p_{name} = static_cast<float*>(d_{name}->ptr());")
        lines.append("    }")
    callargs = [f"p_{p['name']}" if (p["name"] in bufs and p["mem"] == "device")
                else p["name"] for p in params]
    lines.append(f"    aclblasStatus_t ret = {ir['symbol']}(" + ", ".join(callargs) + ");")
    lines.append("    if (ret == ACLBLAS_STATUS_SUCCESS) {")
    lines.append("        if (aclrtSynchronizeDevice() != ACL_SUCCESS)")
    lines.append(f"            return {ir['status_plan']['sync_fail_status']};")
    for name in reads:  # 仅全量搬运（ok）且指针非空才回读；探针（哨兵）不按 span 回读
        lines.append(f"        if (p_{name} != nullptr && ok_{name})")
        lines.append(f"            d_{name}->copyToHost({name}, elems_{name} * sizeof(float));")
    lines.append("    }")
    lines.append("    return ret;")
    return (
        _c_header() + "\n#pragma once\n\n"
        "#include <algorithm>\n#include <cstdint>\n#include <memory>\n\n"
        '#include "acl/acl.h"\n#include "cann_ops_blas.h"\n#include "device.h"\n\n'
        f'inline aclblasStatus_t {ir["symbol"]}_npu(\n    {_sig(params)})\n{{\n'
        + "\n".join(lines) + "\n}\n")


def _fill_col_of(ir, pname):
    """返回绑定到该 param 的 fill 列名（无则 None）。"""
    for c in ir["columns"]:
        if c["kind"] == "fill" and c["binds"].get("param") == pname:
            return c["name"]
    return None


def render_test_cblas(ir) -> str:
    """通用 cblas_call test.cpp（A′：span 门控造数 + 探针/全量二分 + 单设备调用点）。

    - 先按与 wrapper 同公式同上限算每 buffer 的 span/ok/elems；full = 期望 SUCCESS 且非
      quick-return。full 行须所有 span 合法，否则报 harness 基础设施错误（不伪装成设备状态）。
    - host 造数按 elems（哨兵或全量），绝不对错误/退化行按危险 span 造数；M_NULLPTR→nullptr。
    - 恒一个设备调用点；EXPECT 状态由设备裁决。非 full 行只验状态码即返回，不算 golden/回读；
      full 行才快照→调→golden→逐元素比对。"""
    cls = _class_name(ir)
    struct = ir["op"][:1].upper() + ir["op"][1:] + "Param"
    op = ir["op"]
    er = _enum_ref(ir)
    params = ir["params"]
    out = [p for p in params if p["dir"] in ("out", "inout")][0]
    bufs = {b["param"]: b for b in ir["buffers"]}
    NULLPTR = "BlasFillMode::M_NULLPTR"

    span_lines = [f"    static constexpr int64_t kMaxSpanElems = {_SPAN_CAP};",
                  f"    static constexpr int64_t kMaxTotalElems = {_TOTAL_CAP};"]
    span_lines += _span_size_lines(ir, prefix="p.")
    qr = next((c for c in ir["status_plan"]["checks"]
               if c["kind"] == "quick_return"), None)
    qr_expr = render_ast(qr["cond_ast"], prefix="p.", enum_ref=er) if qr is not None else None
    full_cond = "(p.expectResult == ACLBLAS_STATUS_SUCCESS)"
    if qr_expr is not None:
        full_cond += f" && !{qr_expr}"
    ok_all = " && ".join(f"ok_{b['param']}" for b in ir["buffers"]) or "true"
    # 总量守卫用实际 elems（哨兵 1 或有界全量,恒 ≤ 单 buffer 上限,故求和不溢出）,且对**所有行**
    # 生效——错误/非 full 行同样真造数、真上卡,多个合法中等缓冲叠加也可能 OOM(复核点)。
    total_elems = " + ".join(
        f"static_cast<int64_t>(elems_{b['param']})" for b in ir["buffers"]) or "0"

    # host 造数 + 指针（M_NULLPTR→nullptr 保真；matrix !ok 退一元素哨兵，绝不 make 天量）
    host, ptr_of, seed_i = [], {}, 0
    for p in params:
        if _fill_col_of(ir, p["name"]) is None:
            continue
        nm = p["name"]
        # fill 成员名 = 被绑定 param 名（param.h 成员按 param 命名，读自 <nm>_fill 列）
        fm = f"p.{nm}"
        seed = "p.randomSeed" if seed_i == 0 else f"p.randomSeed + {seed_i}"
        seed_i += 1
        ct = "const float*" if p["dir"] == "in" else "float*"
        if p["role"] == "vector":
            # elems_<nm> 始终安全（哨兵 1 或有界全量），makeBlasArray 直接用
            make = f"makeBlasArray(static_cast<int64_t>(elems_{nm}), {fm}, {seed})"
        else:  # matrix：!ok（超大）退一元素哨兵，绝不 make 天量矩阵
            s = p["shape"]
            rows = render_ast(s["rows_ast"], prefix="p.", enum_ref=er)
            cols = render_ast(s["cols_ast"], prefix="p.", enum_ref=er)
            ld = render_ast(s["ld_ast"], prefix="p.", enum_ref=er)
            make = (f"ok_{nm} ? makeBlasMatrix({rows}, {cols}, {ld}, {fm}, {seed})"
                    f"\n                            : std::vector<float>(1, 0.0f)")
        host.append(f"    std::vector<float> h_{nm};")
        host.append(f"    {ct} ptr_{nm} = nullptr;")
        # BlasFillMode 是结构体(含 .method 枚举),判空取 .method（真 frame ABI，见 fill.h）
        host.append(f"    if ({fm}.method != {NULLPTR}) {{")
        host.append(f"        h_{nm} = {make};")
        host.append(f"        ptr_{nm} = h_{nm}.data();")
        host.append("    }")
        ptr_of[nm] = f"ptr_{nm}"

    def arg(p):
        if p["role"] == "handle":
            return "handle_"
        if p["name"] in ptr_of:
            return ptr_of[p["name"]]
        if p["role"] == "scalar":
            return f"&p.{p['name']}"
        return f"p.{p['name']}"

    npu_call = f"{ir['symbol']}_npu(" + ", ".join(arg(p) for p in params) + ")"
    gold_args = [f"golden_{out['name']}.data()" if p["name"] == out["name"] else arg(p)
                 for p in params]
    gold_call = f"{ir['symbol']}_cpu(" + ", ".join(gold_args) + ")"
    out_elems = f"elems_{out['name']}"
    cmp_block = (
        "    VerifyConfig cfg;\n"
        f"    applyMixedTolerance(cfg, ACL_FLOAT, golden_{out['name']}.data(), "
        f"static_cast<size_t>({out_elems}));\n"
        f"    EXPECT_TRUE(Verifier::verifyVector(h_{out['name']}.data(), "
        f"golden_{out['name']}.data(),\n        static_cast<size_t>({out_elems}), 1, cfg, "
        "p.caseName));")
    smoke_block = ""
    if ir["smoke"] is not None:
        sargs = []
        for a in ir["smoke"]["null_handle"]["args"]:
            if a is None:
                sargs.append("nullptr")
            elif a == "&local":
                sargs.append("&result")
            elif isinstance(a, list) and a[0] == "int":
                sargs.append(str(a[1]))
        decl = "    float result = 0.0f;\n" if "&result" in sargs else ""
        smoke_block = (
            f"TEST_F({cls}, NullHandle)\n{{\n" + decl
            + f"    aclblasStatus_t ret = {ir['symbol']}_npu({', '.join(sargs)});\n"
            f'    EXPECT_EQ(ret, {ir["smoke"]["null_handle"]["expect"]});\n}}\n\n')

    # int ABI 域门：成员是 parseInt64 存的 int64_t，wrapper 形参是 IR ctype（int）。CSV 值超
    # int 域时，test 按 64 位算 span、wrapper 按窄化后的 int 重算，二者尺寸不一致会越界读宿主
    # 缓冲。故凡窄于 int64 的 int 型 dim/layout 成员，不能无损装回 ctype 即报基础设施错误——
    # 该值对本 int-ABI 算子不可表达。门后所有整型输入 ≤2^31，(dim-1)*|inc| 的 int64 运算亦
    # 不可能溢出（F-02 的“极端值仍溢出”一并消解）。
    narrow = [p for p in params if p["role"] in ("dim", "layout")
              and "int" in p["ctype"] and "64" not in p["ctype"]]
    range_checks = " || ".join(
        f"static_cast<int>(p.{p['name']}) != p.{p['name']}" for p in narrow)

    body = ["    const auto& p = GetParam();", ""]
    if range_checks:
        body.append(f"    if ({range_checks}) {{")
        body.append('        FAIL() << "harness: integer field out of device int range: "'
                    " << p.caseName;")
        body.append("        return;  // CSV 值超 int ABI 域，对本算子不可表达（非设备状态）")
        body.append("    }")
        body.append("")
    body += span_lines
    body.append("")
    body.append(f"    const bool full = {full_cond};")
    body.append(f"    if (full && !({ok_all})) {{")
    body.append('        FAIL() << "harness: buffer span exceeds cap for a SUCCESS row: "'
                " << p.caseName;")
    body.append("        return;  // 合法但过大——基础设施上限，不伪装成设备状态码")
    body.append("    }")
    body.append(f"    if (({total_elems}) > kMaxTotalElems) {{")
    body.append('        FAIL() << "harness: total buffer elems exceed budget: " << p.caseName;')
    body.append("        return;  // 多缓冲叠加 OOM 守卫（所有行）——基础设施上限，非设备状态")
    body.append("    }")
    body.append("")
    body += host
    body.append("")
    # 快照只在 full 行做（调用前，wrapper 成功时原地回读）；非 full 行不复制完整输出（F-02）。
    body.append(f"    std::vector<float> golden_{out['name']};")
    body.append(f"    if (full) golden_{out['name']} = h_{out['name']};")
    body.append("")
    body.append(f"    aclblasStatus_t ret = {npu_call};")
    body.append("    EXPECT_EQ(static_cast<int>(ret), static_cast<int>(p.expectResult));")
    body.append("    if (!full) return;  // 探针/错误行：状态由设备裁决，只验状态码")
    body.append("")
    body.append(f"    {gold_call};")
    body.append("")
    body.append(cmp_block)
    return (
        _c_header() + "\n#include <cstdint>\n#include <vector>\n\n"
        '#include "verify.h"\n#include "blas_test.h"\n#include "csv_loader.h"\n'
        f'#include "{op}_param.h"\n#include "{op}_golden.h"\n'
        f'#include "{op}_npu_wrapper.h"\n\n'
        f"class {cls} : public BlasTest<{struct}> {{}};\n\n"
        + smoke_block
        + f"INSTANTIATE_TEST_SUITE_P(\n    {op[:1].upper()+op[1:]}, {cls}, "
        f"::testing::ValuesIn(GetCasesFromCsv<{struct}>(ReplaceFileExtension2Csv(__FILE__))),\n"
        f"    PrintCaseInfoString<{struct}>);\n\n"
        f"TEST_P({cls}, CsvDriven)\n{{\n" + "\n".join(body) + "\n}\n")


def _is_l1(ir):
    return ir["golden"]["body"] == "builtin_kernel"
