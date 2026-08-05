#!/usr/bin/env python3
"""字段驱动生成官方 torch_npu C++ Extension 适配层。

本模块只生成源码，不 build、不 import torch、不访问 NPU。生成形态严格取自 Ascend/op-plugin
`examples/cpp_extension_base`：

* ``torch_npu.utils.cpp_extension.NpuExtension`` + ``BuildExtension``；
* ``#include "npu_cpp_extension.h"``；
* ``EXEC_NPU_CMD_EXT(aclnnXxx, ...)``（**标准 4 参 stage2** 才走这条）；
* ``TORCH_LIBRARY`` / ``TORCH_LIBRARY_IMPL(..., PrivateUse1, ...)``；
* Python 侧由 ``torch.ops.load_library`` 加载独立共享库。

不复制旧 ``pytorch_npu_helper.hpp``，不含算子身份分派。符号、参数顺序、属性类型与输出活动集全部
来自 spec 的 ``params`` / ``call_variants``；域外类型 fail-closed。

---

## stage2 两种实参结构（改动⑮的 cpp_extension 侧对应物）

执行段 ``aclnn<Op>`` 有两种**已观察到**的实参结构，词表与 :mod:`aclnn_runtime.aclnn_runner`
完全同一套（:data:`STAGE2_STANDARD` / :data:`STAGE2_EXTENDED`），本模块**不另建解析器**、
也不自己读 header：形态从 CP-C0 预检产物（`preflight_aclnn`，它调的就是
``parse_aclnn_signature``）或 spec 显式声明取得。

* ``standard`` —— ``(workspace, workspaceSize, executor, stream)``：走官方
  ``EXEC_NPU_CMD_EXT``，与本改动之前**逐字节相同**；
* ``extended`` —— ``(workspace, workspaceSize, executor, <stage1 实参原样重复>, stream)``：
  **官方宏走不了**。宏的执行段落在 op-plugin 的 ``ExecuteApiFunc()``，那里把 phase-2 函数指针
  写死成 ``int (*)(void *, uint64_t, aclOpExecutor *, const aclrtStream)`` 并按 4 参调用
  （`op_plugin/utils/op_api_common.cpp`）。拿 4 参 argtypes 去调 10 参 native 函数 =
  段错误或**静默错值**，全链无门能拦。故 extended 由本模块生成**手写两段式派发**，
  骨架逐句照抄 ``EXEC_NPU_CMD_V1_EXT``、全程复用官方 helper（``GetApiFunc`` /
  ``InitExecCommonCtx`` / ``GetAclStream`` / ``SetExecConfig`` / ``ConvertTypes`` /
  ``ConvertToOpApiFunc`` / ``call`` / ``InitExecSubTheadCtx`` / ``ReleaseConvertTypes`` /
  ``UnInitExecCommonCtx`` / ``RunAclCall``），只把执行段那一行 ``ExecuteApiFunc``（固定 4 参）
  换成由实参元组推出的精确函数指针调用，并自备 workspace buffer。

任何第三种取值（含 ``absent``、``null``、未知串）一律 fail-closed —— 绝不「猜成 4 参」。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path


class CppExtensionCodegenError(ValueError):
    pass


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: **标量** attr 的 C ABI 宽度表。数组属性不在这里——见 :func:`_is_int_array`。
_ATTR_CPP_TYPES = {
    "bool": "bool",
    "int64": "int64_t",
    "int32": "int64_t",  # Torch schema 的 int 对应 C++ int64_t；ACLNN 转换层再按签名处理。
    "float32": "double",
    "float64": "double",
}
_ATTR_SCHEMA_TYPES = {
    "bool": "bool",
    "int64": "int",
    "int32": "int",
    "float32": "float",
    "float64": "float",
}

#: 数组属性的受控能力名。与 `gen_cases._ATTR_ARRAY_CTYPE` **同一个词**（`aclIntArray *` 形参）。
#: ⚠ 刻意**不新增** `int64[]` 这类 dtype：数组性由**值结构**派生（见 :func:`_is_int_array`），
#: spec 里仍写 `dtype: ["int64"]` + `default: [k, k]`，与 `gen_cases._attr_ctype` 口径一致。
#: 之所以不 import gen_cases 拿这个常量：gen_cases 顶层 `import numpy`，而本模块是纯 stdlib 的
#: Layer 1 生成器（本地即可跑、不拉 compute 依赖）。词一致由下面的注释与单测约束。
_ATTR_ARRAY_CTYPE = "int_array"
_ATTR_ARRAY_CPP_TYPE = "at::IntArrayRef"
_ATTR_ARRAY_SCHEMA_TYPE = "int[]"

# ── stage2 实参结构（词表与 aclnn_runtime.aclnn_runner 同一套，不另起名）──────────────
STAGE2_STANDARD = "standard"
STAGE2_EXTENDED = "extended"
STAGE2_FORMS = (STAGE2_STANDARD, STAGE2_EXTENDED)

#: 形态是**从哪来的**——落进 manifest，让「这次按几参调的」在收据里可审（AGENTS.md 5.8）。
STAGE2_SOURCE_PREFLIGHT = "preflight_header"     # CP-C0 静态门从 PR head header 解析所得
STAGE2_SOURCE_SPEC = "spec_declared"             # spec.call_variants[i].stage2_form 显式声明
STAGE2_SOURCE_DEFAULT = "default_unverified"     # 两处都没说 → 沿用历史行为，并挂账

#: 生成代码实际走的派发分支。
DISPATCH_MACRO = "exec_npu_cmd_ext_macro"
DISPATCH_EXTENDED = "generated_extended_two_stage"

#: 「本次 stage2 形态没有任何 header 级证据」的机读挂账。
DEGRADATION_STAGE2_UNVERIFIED = "stage2_form_unverified"

# ── 张量 ACL 存储格式（`spec.aclnn_tensor_format`）───────────────────────────────
#: op-plugin 的 `ConvertType(const at::Tensor&)` **按 rank 猜 ACL 存储格式**：3→`ACL_FORMAT_NCL`、
#: 4→`ACL_FORMAT_NCHW`、5→`ACL_FORMAT_NCDHW`、其余→`ACL_FORMAT_ND`。这是 op-plugin 自家算子的约定，
#: 不是 aclnn 两段式的通用契约：接口若按 `GetStorageFormat() == FORMAT_ND` 校格式，一条 rank-3 的
#: 普通 ND 图像张量就会被 L2 侧当场拒成 `ACLNN_ERR_PARAM_INVALID`（161002），而 Python 侧
#: `torch_npu.get_npu_format` 明明报 ND —— 该格式是**转换那一步**贴上去的，不是张量本来的属性。
#: 故这里把它变成一条**显式声明**：ABI 事实源（header/docs/example）说要 ND 就写 `nd`，
#: 没人核过就沿用历史默认并如实记在 manifest 里，谁都不猜（AGENTS.md 5.1）。
TENSOR_FORMAT_TORCH_NPU_DEFAULT = "torch_npu_rank_default"
TENSOR_FORMAT_ND = "nd"
TENSOR_FORMATS = (TENSOR_FORMAT_TORCH_NPU_DEFAULT, TENSOR_FORMAT_ND)
TENSOR_FORMAT_SOURCE_SPEC = "spec_declared"
TENSOR_FORMAT_SOURCE_DEFAULT = "default_unverified"

#: 生成 ND 转换器时用的函数名（只在 `nd` 档出现在生成源码里）。
_ND_CONVERTER = "OprunwayConvertNdTensor"

#: preflight 产物里表示「静态签名对账通过」的唯一状态。
_PREFLIGHT_READY = "READY_WAIT_NPU_TRUST_GATE"


def _ident(value, where):
    if not isinstance(value, str) or not _IDENT_RE.fullmatch(value):
        raise CppExtensionCodegenError(f"{where}={value!r} 不是合法 C/C++ 标识符")
    return value


def _load(path):
    with open(path, encoding="utf-8") as fh:
        value = json.load(fh)
    if not isinstance(value, dict):
        raise CppExtensionCodegenError("spec 须为 JSON object")
    return value


def _canonical_digest(spec):
    raw = json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _text_sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_int_array(value):
    """值结构判定：非空 `list[int]`（bool 元素拒）。与 `gen_cases._is_int_array` 逐字同口径。"""
    return (isinstance(value, list) and bool(value)
            and all(isinstance(x, int) and not isinstance(x, bool) for x in value))


def _attr_type(param):
    """attr → `(C++ 形参类型, Torch schema 类型, 受控能力名)`；域外一律 fail-closed。

    数组与标量的分流**只看 `default` 的值结构**（op-中立：任何声明了 `list[int]` attr 的算子
    零改即用），与 `gen_cases._attr_ctype` 在只有 spec、没有 per-case 取值时的判法一致——
    codegen 本来就只有 spec，靠这条得出与 caseset 里 slot `ctype` 相同的答案。
    """
    name = param.get("name")
    default = param.get("default")
    if _is_int_array(default):
        return _ATTR_ARRAY_CPP_TYPE, _ATTR_ARRAY_SCHEMA_TYPE, _ATTR_ARRAY_CTYPE
    if isinstance(default, list):
        # 是 list 却不是合法的非空 int 数组：**不许**顺着掉进标量分支。
        # 那会生成一个 `int64_t ksize` 形参去接一个 Python list —— 静态期看不出、
        # 真机上是 schema 不匹配或 ABI 错位。宁可停（AGENTS.md 5.1 域外 fail-closed）。
        raise CppExtensionCodegenError(
            f"attr {name!r} 的 default={default!r} 是 list 但不是非空 list[int]，"
            f"既非标量也非受支持的 {_ATTR_ARRAY_CTYPE}——fail-closed")
    dtypes = param.get("dtype")
    if not isinstance(dtypes, list) or len(dtypes) != 1 or dtypes[0] not in _ATTR_CPP_TYPES:
        raise CppExtensionCodegenError(
            f"attr {name!r} dtype 须为单值 {sorted(_ATTR_CPP_TYPES)}，得 {dtypes!r}"
            f"（数组属性请给非空 list[int] 的 default → {_ATTR_ARRAY_CTYPE}）")
    return (_ATTR_CPP_TYPES[dtypes[0]], _ATTR_SCHEMA_TYPES[dtypes[0]], dtypes[0])


def _preflight_stage2_by_symbol(preflight, spec_digest):
    """CP-C0 预检产物 → `{symbol: (dispatch_form, 原始 stage2_form)}`；绑定不上即 fail-closed。

    只认**同一份 spec** 上跑出来的、状态为 `READY_WAIT_NPU_TRUST_GATE` 的预检：
    `bindings.spec_sha256` 与本次 canonical digest 逐字相同（两边都是 sort_keys 紧凑 JSON 的
    sha256，可直接比）。否则「按 A 份 spec 核过的 header 形态」会被用来生成 B 份 spec 的桥。
    """
    if not isinstance(preflight, dict):
        raise CppExtensionCodegenError("preflight 产物须为 JSON object")
    if preflight.get("status") != _PREFLIGHT_READY:
        raise CppExtensionCodegenError(
            f"preflight.status={preflight.get('status')!r} 非 {_PREFLIGHT_READY}——"
            "静态签名未对账通过的预检不得用来决定 stage2 派发形态")
    bindings = preflight.get("bindings")
    if not isinstance(bindings, dict) or bindings.get("spec_sha256") != spec_digest:
        raise CppExtensionCodegenError(
            "preflight.bindings.spec_sha256 与本次 spec 摘要不一致——"
            "预检与生成必须绑同一份 spec（fail-closed）")
    table = {}
    for row in preflight.get("signatures") or []:
        if not isinstance(row, dict):
            raise CppExtensionCodegenError("preflight.signatures 项须为 object")
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise CppExtensionCodegenError("preflight.signatures 项缺 symbol")
        if symbol in table:
            raise CppExtensionCodegenError(
                f"preflight 里 aclnn{symbol} 出现多份签名，无法唯一定形态")
        table[symbol] = (row.get("stage2_dispatch_form"), row.get("stage2_form"))
    if not table:
        raise CppExtensionCodegenError("preflight 无 signatures，定不出任何 stage2 形态")
    return table


def _resolve_stage2(variant, index, symbol, preflight_table):
    """定出该变体的 stage2 形态 → `(form, source)`；两处都没说才落缺省档并挂账。

    优先级刻意如此：**header 解析 > spec 自报 > 缺省**。
    `preflight_table` 在场就以它为准，且**不允许 spec 反对**——header 是 ABI 的事实源
    （AGENTS.md 5.1），spec 自报只是在没有预检产物时的次优锚。
    """
    declared = variant.get("stage2_form")
    if preflight_table is not None:
        if symbol not in preflight_table:
            raise CppExtensionCodegenError(
                f"call_variants[{index}].symbol={symbol!r} 在 preflight 产物里没有签名——"
                "定不出 stage2 实参结构，fail-closed")
        dispatch, raw = preflight_table[symbol]
        if dispatch not in STAGE2_FORMS:
            raise CppExtensionCodegenError(
                f"aclnn{symbol} 的 stage2 形态不可派发（preflight stage2_form={raw!r}）——"
                f"只支持 {list(STAGE2_FORMS)}；绝不按 4 参猜着调")
        if declared is not None and declared != dispatch:
            raise CppExtensionCodegenError(
                f"call_variants[{index}] 自报 stage2_form={declared!r}，"
                f"但 PR head header 解析出的是 {dispatch!r}——以 header 为准，冲突即 fail-closed")
        return dispatch, STAGE2_SOURCE_PREFLIGHT
    if declared is not None:
        if declared not in STAGE2_FORMS:
            raise CppExtensionCodegenError(
                f"call_variants[{index}].stage2_form={declared!r} 非受控值，"
                f"须属 {list(STAGE2_FORMS)}（fail-closed，不缺省）")
        return declared, STAGE2_SOURCE_SPEC
    # 两处都没说：沿用历史行为（官方宏 = 标准 4 参），但**必须挂账**。
    # 这不是「不在禁用表里就放行」——它落的是与改动前逐字节相同的那条路径，
    # 而 `stage2_form_unverified` 会一路进 manifest → receipt → 报告，让「这次没人核过
    # 执行段实参结构」成为可见事实，而不是一个静默假设。
    return STAGE2_STANDARD, STAGE2_SOURCE_DEFAULT


def _resolve_tensor_format(spec):
    """定出本次生成用哪种 ACL 存储格式 → `(format, source)`；词表外一律 fail-closed。

    缺席即历史默认（op-plugin 按 rank 猜），并记 `default_unverified` —— 与改动前**逐字节相同**
    的那条路径，只是「这次没人核过张量格式」变成 manifest 里可读的事实，而不是静默假设。
    """
    declared = spec.get("aclnn_tensor_format")
    if declared is None:
        return TENSOR_FORMAT_TORCH_NPU_DEFAULT, TENSOR_FORMAT_SOURCE_DEFAULT
    if declared not in TENSOR_FORMATS:
        raise CppExtensionCodegenError(
            f"spec.aclnn_tensor_format={declared!r} 非受控值，须属 {list(TENSOR_FORMATS)}"
            "（fail-closed，不缺省）")
    return declared, TENSOR_FORMAT_SOURCE_SPEC


def _contract(spec, preflight_table=None):
    if spec.get("runner_form") != "cpp_extension":
        raise CppExtensionCodegenError(
            f"cpp_extension_codegen 只接受 runner_form='cpp_extension'，得 {spec.get('runner_form')!r}")
    params = spec.get("params")
    variants = spec.get("call_variants")
    if not isinstance(params, list) or not params:
        raise CppExtensionCodegenError("spec.params 须为非空列表")
    if not isinstance(variants, list) or not variants:
        raise CppExtensionCodegenError("runner_form=cpp_extension 时 call_variants 须为非空列表")

    seen = set()
    rows = []
    for i, p in enumerate(params):
        if not isinstance(p, dict):
            raise CppExtensionCodegenError(f"params[{i}] 须为 object")
        name = _ident(p.get("name"), f"params[{i}].name")
        if name in seen:
            raise CppExtensionCodegenError(f"params 含重名 {name!r}")
        seen.add(name)
        io = p.get("io")
        if io not in ("in", "attr", "out"):
            raise CppExtensionCodegenError(f"params[{i}].io={io!r} 不在 in/attr/out")
        row = {"name": name, "io": io}
        if io == "attr":
            row["cpp_type"], row["schema_type"], row["attr_ctype"] = _attr_type(p)
        rows.append(row)

    in_names = [p["name"] for p in rows if p["io"] == "in"]
    out_names = [p["name"] for p in rows if p["io"] == "out"]
    if not in_names or not out_names:
        raise CppExtensionCodegenError("cpp_extension 至少须有一个 in 和一个 out")

    normalized = []
    for i, v in enumerate(variants):
        if not isinstance(v, dict):
            raise CppExtensionCodegenError(f"call_variants[{i}] 须为 object")
        symbol = _ident(v.get("symbol"), f"call_variants[{i}].symbol")
        if symbol.startswith("aclnn"):
            raise CppExtensionCodegenError(
                f"call_variants[{i}].symbol 须为不带 aclnn 前缀的基名，得 {symbol!r}")
        active = v.get("active_outputs")
        active_attrs = v.get("active_attrs")
        if not isinstance(active, list) or not active:
            raise CppExtensionCodegenError(f"call_variants[{i}].active_outputs 须为非空列表")
        attr_names = [p["name"] for p in rows if p["io"] == "attr"]
        if not isinstance(active_attrs, list):
            raise CppExtensionCodegenError(
                f"call_variants[{i}].active_attrs 须为列表；扩展 ABI 不猜测属性槽")
        if (len(active_attrs) != len(set(active_attrs))
                or any(x not in attr_names for x in active_attrs)):
            raise CppExtensionCodegenError(
                f"call_variants[{i}].active_attrs={active_attrs!r} "
                f"须为 attr 名集 {attr_names!r} 的无重复子集")
        if len(active) != len(set(active)) or any(x not in out_names for x in active):
            raise CppExtensionCodegenError(
                f"call_variants[{i}].active_outputs={active!r} 须为 out 名集 {out_names!r} 的无重复子集")
        stage2_form, stage2_source = _resolve_stage2(v, i, symbol, preflight_table)
        entry = {
            "index": i,
            "symbol": symbol,
            "active_attrs": list(active_attrs),
            "active_outputs": list(active),
            "entrypoint": f"invoke_v{i}",
            "stage2_form": stage2_form,
            "stage2_form_source": stage2_source,
            "dispatch": (DISPATCH_MACRO if stage2_form == STAGE2_STANDARD
                         else DISPATCH_EXTENDED),
        }
        # 真机 native 调用的**实参个数**：standard 恒 4；extended = 框架三参 + 该变体
        # 实际出现的 stage1 实参 + stream。记下来，别让读收据的人默认成 4 参
        # （与 `preflight_aclnn._stage2_record` 的 `stage2_call_arity` 同一算法）。
        entry["stage2_call_arity"] = (
            4 if stage2_form == STAGE2_STANDARD
            else 3 + len(_variant_params(rows, entry)) + 1)
        normalized.append(entry)
    return rows, normalized


def _cpp_arg(param, active):
    name, io = param["name"], param["io"]
    if io == "in":
        return f"const at::Tensor& {name}"
    if io == "attr":
        return f"{param['cpp_type']} {name}"
    if name in active:
        return f"const at::Tensor& {name}"
    return f"const c10::optional<at::Tensor>& {name}"


def _schema_arg(param, active):
    name, io = param["name"], param["io"]
    if io == "in":
        return f"Tensor {name}"
    if io == "attr":
        return f"{param['schema_type']} {name}"
    if name in active:
        return f"Tensor {name}"
    # 不给 optional 参数写 schema default：若后面还有 active output，Torch schema 不允许
    # “带默认值参数在无默认值参数之前”。调用端始终按 spec.params 顺序显式传 None。
    return f"Tensor? {name}"


def _variant_params(params, variant):
    active_attrs = set(variant["active_attrs"])
    return [p for p in params if p["io"] != "attr" or p["name"] in active_attrs]


def _render_standard_body(variant, call_args):
    """标准 4 参 stage2：官方宏，一行。与本改动之前**逐字节相同**。"""
    return f"    EXEC_NPU_CMD_EXT(aclnn{variant['symbol']}, {call_args});"


def _render_nd_converter():
    """生成「按公共 ND 格式建 aclTensor」的转换器。

    逐句对齐 op-plugin 的 `ConvertType(const at::Tensor&)`（storage 维度按 `nbytes/itemsize` 拉平、
    view 形状/步长/偏移原样传、dtype 走官方 `ConvertType(at::ScalarType)`），**只换 format 一项**。
    可选输出（未激活的 out 槽）保持官方语义：未给值即 `nullptr`。
    """
    return f"""namespace {{

// OpRunway 生成：按 spec 声明的 ACL 存储格式（ND）建 aclTensor。
// op-plugin 的 ConvertType(at::Tensor) 按 **rank** 贴格式（3→NCL / 4→NCHW / 5→NCDHW），
// 而本接口的 ABI 事实源要求公共 ND；rank-3 张量会因此被 L2 侧判 ACLNN_ERR_PARAM_INVALID。
// 除 format 外的每个字段都与官方转换逐句一致，不改语义。
inline aclTensor *{_ND_CONVERTER}(const at::Tensor &at_tensor)
{{
    static const auto aclCreateTensorFunc = GET_OP_API_FUNC(aclCreateTensor);
    TORCH_CHECK(aclCreateTensorFunc != nullptr, "aclCreateTensor 未从 CANN 动态库解析到");
    TORCH_CHECK(at_tensor.defined(), "ND 张量转换收到未定义的 at::Tensor");
    const auto itemsize = at_tensor.itemsize();
    TORCH_CHECK(itemsize > 0, "ND 张量转换收到 itemsize=0 的 at::Tensor");
    const int64_t storage_dims[1] = {{
        static_cast<int64_t>(at_tensor.storage().nbytes() / itemsize)}};
    return aclCreateTensorFunc(
        at_tensor.sizes().data(), at_tensor.sizes().size(),
        ConvertType(at_tensor.scalar_type()),
        at_tensor.strides().data(), at_tensor.storage_offset(), ACL_FORMAT_ND,
        storage_dims, 1, const_cast<void *>(at_tensor.storage().data()));
}}

inline aclTensor *{_ND_CONVERTER}(const c10::optional<at::Tensor> &opt_tensor)
{{
    if (!opt_tensor.has_value() || !opt_tensor.value().defined()) {{
        return nullptr;
    }}
    return {_ND_CONVERTER}(opt_tensor.value());
}}

}}  // namespace"""


def _convert_expr(param, tensor_format):
    """该实参在 stage1 转换里的表达式：张量按声明格式走，其余一律官方 `ConvertType`。"""
    if param["io"] in ("in", "out") and tensor_format == TENSOR_FORMAT_ND:
        return f"{_ND_CONVERTER}({param['name']})"
    return f"ConvertType({param['name']})"


def _render_extended_body(variant, call_args, arg_count, first_input,
                          variant_params=None, tensor_format=TENSOR_FORMAT_TORCH_NPU_DEFAULT):
    """extended stage2：按官方 helper 手写两段式派发。

    为什么不能用 ``EXEC_NPU_CMD_EXT``：宏的执行段最终落到 op-plugin 的 ``ExecuteApiFunc()``，
    那里把 phase-2 函数指针写死成 ``int (*)(void *, uint64_t, aclOpExecutor *, const aclrtStream)``
    并按 4 参调用。extended 形态的 native 函数要收 ``3 + N + 1`` 个实参，错 arity 调用在
    aarch64 上会从垃圾寄存器取 stream —— 段错误或**静默错值**，全链无门能拦。

    生成的骨架**逐句对齐** ``op_api_common_base.h`` 的 ``EXEC_NPU_CMD_V1_EXT``（步骤 1→8），
    只把第 7 步里那一行 ``ExecuteApiFunc(...)`` 换成由**实参元组**推出的精确函数指针调用：

    ====  官方 V1_EXT 步骤                       本模块生成的对应物
    1     ``GetApiFunc``                         同（含 static 函数指针缓存）
    2     ``InitExecCommonCtx()``                同
    3     ``GetAclStream()``                     同
    4     ``hit_cache_ext``                      **刻意跳过**，见下方偏离①
    5     ``SetExecConfig()``                    同（这才是「确定性算法」的落点）
    6     ``ConvertTypes`` → ``call(stage1)``    同，另加 ``TORCH_CHECK`` 兜住非零返回
    7     ``ExecuteApiFunc(4 参)``               **换成** ``call(op_api_func, exec_params)``
    8     ``RunAclCall``                         同
    ====

    workspace 也得自己来：官方 4 参路径靠 ``ExecuteApiFunc`` 在 .so 内部申请，
    ``OpPreparation::unsafe_empty_workspace`` 又**没有**从 ``libtorch_npu.so`` 导出（实测
    ``nm -D`` 查无此符号），扩展里链不到。故这里用 ``at::empty`` 在与首个输入同一台设备上要
    一块 byte buffer；该张量**按值捕获**进 ``acl_call``，生命周期覆盖到执行段跑完
    （``RunAclCall`` 可能把 lambda 丢到下发线程）。``workspace_size == 0`` 时传 ``nullptr``。

    两处**刻意的偏离**，都不是遗漏：
      1. 不走 ``hit_cache_ext``：aclnn 执行缓存的重放路径（``ExecuteCachedOp``）同样把 phase-2
         写死成 4 参，缓存命中就等于绕回错 arity 调用。宁可每次都走完整两段式；
      2. 只实现 V1（``EXEC_NPU_CMD_V1_EXT``）语义，不实现 task_queue_enable==2 的 V2 分支：
         V2 的 ``ExecuteApiFuncV2`` 同样是固定 arity，且其参数拷贝链依赖 ``CopyTypesV2``
         的一整套重载。V1 语义在任何 task queue 设置下都是正确的（只是不吃那条优化）。
    """
    picks = ",\n        ".join(
        f"std::get<{i}>(converted_params)" for i in range(arg_count))
    symbol = f"aclnn{variant['symbol']}"
    if tensor_format == TENSOR_FORMAT_ND:
        # 逐槽显式转换：张量走生成的 ND 转换器，其余原样交给官方 ConvertType。
        # 元组布局与 `ConvertTypes(...)` 逐项相同（含末两项 stage1 专有出参），故下面
        # picks / ReleaseConvertTypes 一个字都不用改。
        exprs = ",\n        ".join(
            _convert_expr(p, tensor_format) for p in (variant_params or []))
        convert_line = (f"""auto converted_params = std::make_tuple(
        {exprs},
        workspace_size_addr, executor_addr);""")
    else:
        convert_line = (
            f"auto converted_params = ConvertTypes({call_args}, "
            f"workspace_size_addr, executor_addr);")
    return f"""    // stage2 = extended（框架三参 + stage1 实参原样重复 + stream，共 {variant['stage2_call_arity']} 参）。
    // 官方 EXEC_NPU_CMD_EXT 的执行段固定按 4 参调 phase-2，对这条 ABI 会静默错调，故手写派发。
    // 骨架逐句对齐 op_api_common_base.h 的 EXEC_NPU_CMD_V1_EXT，只换掉执行段那一行。
    static void *op_api_addr = nullptr;
    static void *get_workspace_size_addr = nullptr;
    GetApiFunc("{symbol}", "{symbol}GetWorkspaceSize", op_api_addr, get_workspace_size_addr);
    InitExecCommonCtx();
    auto acl_stream = GetAclStream();
    SetExecConfig();  // 官方步骤 5：确定性算法等执行配置的唯一落点。
    uint64_t workspace_size = 0;
    uint64_t *workspace_size_addr = &workspace_size;
    aclOpExecutor *executor = nullptr;
    aclOpExecutor **executor_addr = &executor;
    {convert_line}
    auto get_workspace_size_func = ConvertToOpApiFunc(converted_params, get_workspace_size_addr);
    auto workspace_status = call(get_workspace_size_func, converted_params);
    TORCH_CHECK(workspace_status == 0, "{symbol}GetWorkspaceSize failed, ret=", workspace_status);
    // workspace 自申请：官方 4 参路径由 .so 内部的 ExecuteApiFunc 代劳，而
    // OpPreparation::unsafe_empty_workspace 未从 libtorch_npu.so 导出，扩展里链不到。
    // 张量按值捕获进 acl_call，生命周期覆盖到执行段跑完。
    at::Tensor workspace_tensor;
    void *workspace_addr = nullptr;
    if (workspace_size != 0) {{
        workspace_tensor = at::empty({{static_cast<int64_t>(workspace_size)}},
                                     {first_input}.options().dtype(at::kByte));
        workspace_addr = workspace_tensor.data_ptr();
    }}
    // 执行段实参 = 框架三参 + **本次已转换好的 stage1 实参原样重复** + stream。
    // 复用 converted_params 的前 {arg_count} 项，绝不二次 ConvertTypes（那会再造一批 acl 对象）；
    // 末两项 workspace_size_addr / executor_addr 是 stage1 专有出参，不进 stage2。
    auto exec_params = std::make_tuple(
        workspace_addr, workspace_size, executor,
        {picks},
        acl_stream);
    auto op_api_func = ConvertToOpApiFunc(exec_params, op_api_addr);
    auto acl_call = [exec_params, converted_params, workspace_tensor, op_api_func,
                     acl_stream]() -> int {{
        (void)workspace_tensor;  // 捕获只为续命：buffer 必须活过下面这次执行。
        InitExecSubTheadCtx(acl_stream);
        auto api_ret = call(op_api_func, exec_params);
        ReleaseConvertTypes(converted_params);
        UnInitExecCommonCtx();
        return api_ret;
    }};
    RunAclCall("{symbol}", acl_call);"""


def _render_cpp(namespace, params, variants,
                tensor_format=TENSOR_FORMAT_TORCH_NPU_DEFAULT):
    functions = []
    schemas = []
    impls = []
    first_input = next(p["name"] for p in params if p["io"] == "in")
    for v in variants:
        variant_params = _variant_params(params, v)
        active = set(v["active_outputs"])
        args = ", ".join(_cpp_arg(p, active) for p in variant_params)
        call_args = ", ".join(p["name"] for p in variant_params)
        returned = ", ".join(v["active_outputs"])
        if v["stage2_form"] == STAGE2_STANDARD:
            if tensor_format != TENSOR_FORMAT_TORCH_NPU_DEFAULT:
                # 官方 EXEC_NPU_CMD_EXT 宏内部自己调 ConvertTypes，插不进别的张量格式；
                # 而那条路径是「与改动前逐字节相同」的红线，不为此改写。声明了非默认格式却
                # 落在 standard 派发上 → fail-closed，绝不悄悄按 rank 猜格式跑过去。
                raise CppExtensionCodegenError(
                    f"call_variants[{v['index']}] 的 stage2 形态是 {STAGE2_STANDARD}（走官方宏），"
                    f"无法施加 spec.aclnn_tensor_format={tensor_format!r}——"
                    "该格式当前只在手写 extended 派发下实现，fail-closed")
            body = _render_standard_body(v, call_args)
        elif v["stage2_form"] == STAGE2_EXTENDED:
            body = _render_extended_body(v, call_args, len(variant_params), first_input,
                                         variant_params=variant_params,
                                         tensor_format=tensor_format)
        else:
            # `_resolve_stage2` 已把词表外的值拦死；这里是最后一道，防将来有人扩词表却忘了改这。
            raise CppExtensionCodegenError(
                f"未知 stage2 形态 {v['stage2_form']!r}，无法生成派发（fail-closed）")
        functions.append(
            f"""std::vector<at::Tensor> {v['entrypoint']}({args})
{{
    const c10::OptionalDeviceGuard device_guard(device_of({first_input}));
{body}
    return {{{returned}}};
}}""")
        schema_args = ", ".join(_schema_arg(p, active) for p in variant_params)
        schemas.append(f'    m.def("{v["entrypoint"]}({schema_args}) -> Tensor[]");')
        impls.append(f'    m.impl("{v["entrypoint"]}", &{v["entrypoint"]});')
    prelude = ([_render_nd_converter()] if tensor_format == TENSOR_FORMAT_ND else [])
    return f"""// Generated by OpRunway. Do not hand-edit.
#include <tuple>
#include <vector>
#include <torch/library.h>
#include <torch/extension.h>
#include "npu_cpp_extension.h"

{os.linesep.join(prelude + functions)}

TORCH_LIBRARY({namespace}, m)
{{
{os.linesep.join(schemas)}
}}

TORCH_LIBRARY_IMPL({namespace}, PrivateUse1, m)
{{
{os.linesep.join(impls)}
}}
"""


def _render_setup(module_name):
    return f"""# Generated by OpRunway. Do not hand-edit.
import glob
import os

import torch_npu
from setuptools import setup
from torch.utils.cpp_extension import BuildExtension
from torch_npu.utils.cpp_extension import NpuExtension

ROOT = os.path.dirname(os.path.realpath(__file__))
TORCH_NPU_ROOT = os.path.dirname(os.path.abspath(torch_npu.__file__))

setup(
    name={module_name!r},
    version="1.0",
    ext_modules=[
        NpuExtension(
            name={module_name!r},
            sources=glob.glob(os.path.join(ROOT, "csrc", "*.cpp")),
            extra_compile_args=[
                "-I" + os.path.join(TORCH_NPU_ROOT, "include", "third_party", "acl", "inc"),
                "-I" + os.path.join(TORCH_NPU_ROOT, "include", "third_party", "op-plugin"),
                "-I" + os.path.join(
                    TORCH_NPU_ROOT, "include", "third_party", "op-plugin", "op_plugin", "include"),
            ],
        )
    ],
    cmdclass={{"build_ext": BuildExtension.with_options(use_ninja=False)}},
)
"""


def generate(spec, out_dir, preflight=None):
    """spec（+ 可选 CP-C0 预检产物）→ Extension bundle + manifest。

    `preflight` 给的是 `preflight_aclnn.evaluate()` 的 payload。给了就以它解析出的
    header stage2 形态为准（并强制它与本次 spec 摘要绑定）；不给则退回 spec 自报 /
    历史缺省，并把 `stage2_form_unverified` 写进 manifest 的 `degradations`。
    """
    digest = _canonical_digest(spec)
    preflight_table = (None if preflight is None
                       else _preflight_stage2_by_symbol(preflight, digest))
    params, variants = _contract(spec, preflight_table)
    tensor_format, tensor_format_source = _resolve_tensor_format(spec)
    namespace = f"oprunway_{digest[:16]}"
    module_name = f"{namespace}_lib"
    out = Path(out_dir)
    csrc = out / "csrc"
    csrc.mkdir(parents=True, exist_ok=True)

    cpp = _render_cpp(namespace, params, variants, tensor_format)
    setup = _render_setup(module_name)
    (csrc / "oprunway_extension.cpp").write_text(cpp, encoding="utf-8")
    (out / "setup.py").write_text(setup, encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "runner_form": "cpp_extension",
        "spec_sha256": digest,
        "namespace": namespace,
        "module_name": module_name,
        # 本次张量按哪种 ACL 存储格式建：`torch_npu_rank_default` = op-plugin 按 rank 猜
        # （历史行为）；`nd` = 按 spec 声明的公共 ND。来源一并落盘，别让读收据的人以为「默认就对」。
        "tensor_acl_format": tensor_format,
        "tensor_acl_format_source": tensor_format_source,
        "official_pattern": {
            "source": "Ascend/op-plugin examples/cpp_extension_base",
            "ascend_pytorch_master_commit": "c255c0003f1ddff0e34190e417dc29b1c6f566a3",
            "op_plugin_gitlink_commit": "ab6984979cb97ab9a2e48b19332e313203ea7c3c",
            "header": "npu_cpp_extension.h",
            "macro": "EXEC_NPU_CMD_EXT",
            "builder": "torch_npu.utils.cpp_extension.NpuExtension",
            "loader": "torch.ops.load_library",
            # extended stage2 走不了官方宏（宏的执行段固定 4 参），改按官方 helper 手写两段式。
            # 逐变体实际走哪条见 `variants[].dispatch`。
            # 逐个都是 op_api_common_base.h 里**已声明且从 libtorch_npu.so 导出**的符号
            # （2026-08-05 于真机容器 torch_npu 2.10.0 用 nm -D 逐条核过）。
            "extended_stage2_helpers": [
                "GetApiFunc", "InitExecCommonCtx", "GetAclStream", "SetExecConfig",
                "ConvertTypes", "ConvertToOpApiFunc", "call", "InitExecSubTheadCtx",
                "ReleaseConvertTypes", "UnInitExecCommonCtx", "RunAclCall",
            ],
            "extended_stage2_deviations": [
                "no_hit_cache_ext:执行缓存的重放路径同样固定 4 参，命中即绕回错 arity 调用",
                "v1_semantics_only:不实现 task_queue_enable==2 的 V2 分支（ExecuteApiFuncV2 同样固定 arity）",
                "self_allocated_workspace:OpPreparation::unsafe_empty_workspace 未从 libtorch_npu.so"
                " 导出，改用 at::empty 按首个输入的 device 申请 byte buffer，按值捕获续命至执行结束",
            ],
        },
        # 机读降级挂账（恒存在；空表 = 工具记过、没有降级）。
        "degradations": sorted({
            DEGRADATION_STAGE2_UNVERIFIED for v in variants
            if v["stage2_form_source"] == STAGE2_SOURCE_DEFAULT
        }),
        "variants": variants,
        "files": {
            "cpp": {
                "path": "csrc/oprunway_extension.cpp",
                "sha256": _text_sha256(cpp),
            },
            "setup": {
                "path": "setup.py",
                "sha256": _text_sha256(setup),
            },
        },
    }
    (out / "extension_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成官方 torch_npu C++ Extension 适配层（不 build）")
    ap.add_argument("spec")
    ap.add_argument("--out", required=True)
    ap.add_argument("--preflight", default=None,
                    help="CP-C0 预检产物（preflight_aclnn 的 JSON）；给了就以 header 解析的 "
                         "stage2 形态为准，不给则退回 spec 自报 / 历史缺省并挂账")
    ns = ap.parse_args(argv)
    preflight = _load(ns.preflight) if ns.preflight else None
    print(json.dumps(
        generate(_load(ns.spec), ns.out, preflight), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
