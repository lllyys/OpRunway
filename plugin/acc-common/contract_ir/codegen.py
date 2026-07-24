#!/usr/bin/env python3
"""U7 通用 codegen —— 吃一份契约 IR（符合 contract_ir.schema.v1.json）→ 机械 emit 类型化 aclnn binding.cpp。

承 CLAUDE.md 最高律令 #0：**零 op 名分支**。所有 emit 一律由 IR 的结构字段驱动
（kind / binding / cardinality / direction / output_mapping / storage_alias …），换任意域内算子零改。

铁律：
  - 域外（applicability.in_domain=false）或任一字段 provenance.state ∈ {needs_source,conflict,out_of_domain}
    且 fail_closed → **拒绝生成、打印原因、退非零**，绝不硬凑（尤其 data-dependent out 尺寸算不出时）。
  - 输出反转映射一律读 IR 的 output_mapping，绝不按位置恒等。
  - inout 回读一律读 storage_alias.readback_binding（唯一 ground truth），绝不据 const 判。

⚠ 本 codegen emit 的 C++ 在**真机编译**前未经验证（covered≠真机绿，见 task#5）。emit 的 ACL 调用形态
锚自各算子真 example（test_aclnn_*.cpp）；binding 里凡拿不准处以 `// FAIL_CLOSED:` 注释显式标出、不静默。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "contract_ir.schema.v1.json")

# ACL dtype 通用分派表（非 per-op；平台条件 dtype 由 IR 的 value_domain.selector.platform_predicate 决定，此处只做名→ACL 常量映射）
_ACL_DTYPE = {
    "FLOAT": "ACL_FLOAT", "FLOAT16": "ACL_FLOAT16", "BF16": "ACL_BF16",
    "DOUBLE": "ACL_DOUBLE", "INT8": "ACL_INT8", "UINT8": "ACL_UINT8",
    "INT16": "ACL_INT16", "UINT16": "ACL_UINT16", "INT32": "ACL_INT32",
    "UINT32": "ACL_UINT32", "INT64": "ACL_INT64", "UINT64": "ACL_UINT64",
    "BOOL": "ACL_BOOL", "COMPLEX64": "ACL_COMPLEX64", "COMPLEX32": "ACL_COMPLEX32",
}


class FailClosed(Exception):
    """契约要求停手：拿不准就不生成，绝不硬凑。"""


def _load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _validate(ir):
    """IR 必须先过 Schema。jsonschema 缺失则退化到结构性必填检查（仍 fail-closed）。"""
    schema = _load_schema()
    try:
        import jsonschema  # noqa
        jsonschema.Draft202012Validator(schema).validate(ir)
    except ImportError:
        for k in schema.get("required", []):
            if k not in ir:
                raise FailClosed(f"IR 缺必填顶层字段 `{k}`（jsonschema 未装、走结构性检查）")


_BLOCKING_STATES = ("needs_source", "conflict", "out_of_domain")


def _check_fail_closed(ir):
    """**状态驱动**的 fail-closed（不靠输入自觉设 fail_closed 旗标——codex 审出旗标可绕）：
    凡 codegen **要消费的 binding 相关字段** provenance.state ∈ {needs_source,conflict,out_of_domain} → 一律停手。
    唯一例外：`acceptance`（含 threshold_source）是 **validator 侧**关注、不参与 binding 生成——其 needs_source 不阻断
    binding（阈值待真机对拍属正常，见 G4 降级 tier）。"""
    if not ir.get("applicability", {}).get("in_domain", False):
        axes = ir.get("applicability", {}).get("out_of_domain_axes", [])
        raise FailClosed(f"算子在契约适用域外（{axes or ir.get('applicability', {}).get('reason', '')}）——标『不支持的接口能力』，不归某类 adapter")
    blockers = []

    def walk(node, path, under_acceptance):
        if isinstance(node, dict):
            if not under_acceptance and isinstance(node.get("provenance"), dict):
                st = node["provenance"].get("state")
                if st in _BLOCKING_STATES:
                    blockers.append(f"{path}: state={st} · {node['provenance'].get('conflict_resolution', '')[:80]}")
            for k, v in node.items():
                walk(v, f"{path}.{k}", under_acceptance or k == "acceptance")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]", under_acceptance)

    walk(ir, "ir", False)
    if blockers:
        raise FailClosed("以下 binding 相关字段未 resolved（状态驱动、不靠 fail_closed 旗标），须补源/人裁后才能 codegen：\n  - " + "\n  - ".join(blockers))


def _params_by_id(ir):
    return {p["logical_value_id"]: p for p in ir.get("parameters", [])}


def _emit_shape_check(out_id, out_desc):
    """host_oracle 的 out 尺寸若依赖输入数据值（value_dependency 含求值式）且无 manifest 供——停手。"""
    modes = out_desc.get("shape_materialization", {}).get("modes", [])
    has_manifest = any(m.get("mode") == "manifest" for m in modes)
    for m in modes:
        if m.get("mode") == "host_oracle" and not has_manifest:
            vd = m.get("value_dependency", "")
            # 依赖张量数据值（reduce/max/看内容）→ 离线 IR 给不出 → fail-closed
            if any(tok in vd for tok in ("reduce", "max(", "看数据", "self 的值", "InputsDataDependency")):
                raise FailClosed(
                    f"输出 `{out_id}` 的 extent 依赖输入张量的**数据值**（{vd}），"
                    f"离线 IR/ABI 机械求不出；须用例显式给 out 尺寸或真机 reduce 回填（见 shape_materialization）"
                )


def _construct_param(p):
    """按 kind/binding/cardinality emit 一个参数的构造代码（C++ 片段列表）。"""
    pid = p["logical_value_id"]
    kind = p["kind"]
    binding = p.get("binding")
    card = p.get("cardinality", {}).get("kind", "single")
    lines = []
    if kind == "tensor" and binding == "device_tensor":
        lines.append(f"  // {pid}: 单张量，H2D 到 device，建 aclTensor")
        lines.append(f"  aclTensor* {pid} = CreateAclTensorFromCase(\"{pid}\");")
    elif kind == "tensor_list":
        n = p["cardinality"].get("length_symbol", "N")
        lines.append(f"  // {pid}: 张量列表，长度符号={n}（n 由 runner 从 case manifest 读；constraint_graph 保证并列列表同长）")
        lines.append(f"  std::vector<aclTensor*> {pid}_elems = CreateAclTensorListElems(\"{pid}\");")
        lines.append(f"  aclTensorList* {pid} = aclCreateTensorList({pid}_elems.data(), {pid}_elems.size());")
    elif kind == "scalar" and binding == "device_tensor":
        lines.append(f"  // {pid}: 标量张量（numel==1），H2D，建 aclTensor")
        lines.append(f"  aclTensor* {pid} = CreateAclTensorFromCase(\"{pid}\");")
    elif kind == "scalar" and binding == "host_scalar":
        lines.append(f"  // {pid}: host 标量，按值直传（免 malloc）")
        lines.append(f"  auto {pid} = ReadHostScalarFromCase(\"{pid}\");")
    elif kind == "array":
        lines.append(f"  // {pid}: aclIntArray（长度/元素来自 IR，顺序不外推）")
        lines.append(f"  aclIntArray* {pid} = CreateAclIntArrayFromCase(\"{pid}\");")
    elif kind == "attr":
        lines.append(f"  // {pid}: attr，按值传")
        lines.append(f"  auto {pid} = ReadAttrFromCase(\"{pid}\");")
    else:
        raise FailClosed(f"参数 `{pid}` 的 kind={kind}/binding={binding} 组合本 codegen 未支持——停手不硬凑（域外或待扩）")
    return lines


def generate(ir):
    """IR → binding.cpp 文本。全程结构驱动、零 op 名分支。"""
    _validate(ir)
    _check_fail_closed(ir)

    entry = ir["aclnn_entry"]
    exec_sym = entry["exec_symbol"]
    gw_sym = entry["getworkspace_symbol"]
    header = entry.get("include_header", "aclnn/aclnn_base.h")
    op = ir.get("op", "op")

    pmap = _params_by_id(ir)
    stage1_ids = ir["abi_signature"]["stage1"]["ordered_param_ids"]

    # 输出尺寸可求性闸（data-dependent 停手）
    for out in ir.get("outputs", []):
        _emit_shape_check(out["logical_value_id"], out)

    lines = []
    lines.append(f"// AUTO-GENERATED by contract_ir/codegen.py — 勿手改。op={op}")
    lines.append("// 承 CLAUDE.md #0：本文件由通用模板机械 emit、无 op 名分支；改逻辑改 codegen.py 不改此处。")
    lines.append("#include <vector>")
    lines.append("#include <cstdint>")
    lines.append(f'#include "{header}"')
    lines.append('#include "acl/acl.h"')
    lines.append("")
    lines.append("// 运行期辅助（由 runner 提供）：从当前 case 读输入/建 device buffer/写回")
    lines.append("aclTensor* CreateAclTensorFromCase(const char* name);")
    lines.append("std::vector<aclTensor*> CreateAclTensorListElems(const char* name);  // n 从 case manifest 内部读")
    lines.append("aclIntArray* CreateAclIntArrayFromCase(const char* name);")
    lines.append("int64_t ReadHostScalarFromCase(const char* name);")
    lines.append("int64_t ReadAttrFromCase(const char* name);")
    lines.append("void ReadbackToFile(int golden_col, const char* aclnn_slot);      // D2H aclnn_slot 的 buffer → 写到 golden 列 golden_col（据 output_mapping）")
    lines.append("void ReadbackToFileList(int golden_col, const char* aclnn_slot);  // 列表输出同理")
    lines.append("")
    lines.append(f"// binding：单入口 op={op}（{exec_sym}）")
    lines.append("int RunCaseBinding(aclrtStream stream) {")
    lines.append("  uint64_t workspaceSize = 0;")
    lines.append("  aclOpExecutor* executor = nullptr;")
    lines.append("")

    # 1. 构造 stage1 各功能参
    for pid in stage1_ids:
        p = pmap.get(pid)
        if p is None:
            raise FailClosed(f"abi_signature.stage1 引用了不存在的参数 `{pid}`")
        lines.extend(_construct_param(p))
    lines.append("")

    # 2. stage1 GetWorkspaceSize 调用（按 IR 位序 + 尾随 plumbing）
    call_args = list(stage1_ids) + ["&workspaceSize", "&executor"]
    lines.append(f"  auto ret = {gw_sym}(" + ", ".join(call_args) + ");")
    lines.append("  if (ret != ACL_SUCCESS) return ret;")
    lines.append("  void* workspace = nullptr;")
    lines.append("  if (workspaceSize > 0) {")
    lines.append("    ret = aclrtMalloc(&workspace, workspaceSize, ACL_MEM_MALLOC_HUGE_FIRST);")
    lines.append("    if (ret != ACL_SUCCESS) return ret;  // malloc 失败即返回，不以空 workspace 执行")
    lines.append("  }")
    lines.append(f"  ret = {exec_sym}(workspace, workspaceSize, executor, stream);")
    lines.append("  if (ret != ACL_SUCCESS) return ret;")
    lines.append("  aclrtSynchronizeStream(stream);")
    lines.append("")

    # 3. 回读输出：**真消费** output_mapping（把 aclnn 数据槽对到正确 golden 列，非位置恒等）
    #    + storage_alias.readback_binding（inout 的 D2H 源 = 自身 buffer，非 vestigial out）
    lines.append("  // 回读：按 output_mapping 把 aclnn 输出槽对到 golden 列（消费反转、非位置恒等）")
    out_params = [p for p in ir.get("parameters", []) if p.get("direction") in ("out", "inout")]
    if not out_params:
        raise FailClosed("无 direction∈{out,inout} 的参数——无输出可回读（Sleep 型属域外 behavioral）")
    out_edges = ir.get("output_mapping", {}).get("output_edges", [])
    if not out_edges:
        raise FailClosed("有输出但 output_mapping.output_edges 缺失——**绝不默认位置恒等**（会把索引写进值缓冲，静默灾难）")
    # api_param_id 索引 aclnn 数据输出槽（out_params 按 abi_position 升序）
    ordered_out = sorted(out_params, key=lambda q: q.get("abi_position", 0))
    for e in sorted(out_edges, key=lambda x: x["source_output_id"]):
        api_id, gold = e["api_param_id"], e["source_output_id"]
        if not (0 <= api_id < len(ordered_out)):
            raise FailClosed(f"output_mapping.api_param_id={api_id} 越界（仅 {len(ordered_out)} 个 aclnn 输出槽）")
        p = ordered_out[api_id]
        pid = p["logical_value_id"]
        if p.get("direction") == "inout":
            if not p.get("storage_alias", {}).get("readback_binding"):
                raise FailClosed(f"inout 输出 `{pid}` 无 storage_alias.readback_binding=true——D2H 回读源不定，拒绝生成（const 不可信）")
            lines.append(f"  // {pid}: inout，从自身 buffer 回读（readback_binding；const 不可信）")
        fn = "ReadbackToFileList" if p.get("kind") == "tensor_list" else "ReadbackToFile"
        lines.append(f"  {fn}(/*golden_col=*/{gold}, /*aclnn_slot=*/\"{pid}\");")
    lines.append("")

    # 4. 清理：销毁所建 ACL 对象 + 释放 workspace（codex #13.5 成功路径；全 error-path RAII 留 task#13）
    lines.append("  // 清理（避免泄漏，成功路径）：销毁所建 ACL 对象 + 释放 workspace")
    for pid in stage1_ids:
        p = pmap[pid]
        kind, binding = p["kind"], p.get("binding")
        if kind == "tensor_list":
            lines.append(f"  aclDestroyTensorList({pid});")
        elif kind in ("tensor", "scalar") and binding == "device_tensor":
            lines.append(f"  aclDestroyTensor({pid});")
        elif kind == "array":
            lines.append(f"  aclDestroyIntArray({pid});")
        # host_scalar / attr 按值传、无 ACL 对象可销
    lines.append("  if (workspace) { aclrtFree(workspace); }")
    lines.append("")
    lines.append("  return ACL_SUCCESS;")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="U7 通用 codegen：契约 IR → 类型化 binding.cpp")
    ap.add_argument("ir_json", help="一份符合 contract_ir.schema.v1.json 的 IR 实例路径")
    ap.add_argument("-o", "--out", help="输出 binding.cpp 路径；缺省打印到 stdout")
    args = ap.parse_args(argv)

    with open(args.ir_json, encoding="utf-8") as f:
        ir = json.load(f)
    try:
        code = generate(ir)
    except FailClosed as e:
        sys.stderr.write(f"[FAIL-CLOSED] 拒绝生成 binding：\n{e}\n")
        return 4
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(code)
        sys.stderr.write(f"✓ 已生成 {args.out}\n")
    else:
        sys.stdout.write(code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
