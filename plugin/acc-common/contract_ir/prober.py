#!/usr/bin/env python3
"""U7 通用探测器 prober —— 给一个算子目录，只读 header/op_def/example → 产符合 contract_ir.schema.v1.json 的 IR。

承 CLAUDE.md 最高律令 #0：**零 op 名分支**。一切按目录里的文件结构 + C 签名类型驱动，换任意域内算子零改。

诚实边界（v1）：
  - **机械可抠的**（confidently resolved）：aclnn 两段式符号、stage1 有序参数表（kind/binding/cardinality）、
    op_def AddConfig→目标机 socs。
  - **须语义核的**（provenance.state=needs_source + fail_closed，交 example D2H / glue / 人核）：
    每参 direction（`const` 不可信、须 example 回读源坐实）、storage_alias、output_mapping（须 glue std::get→ViewCopy）、
    output 的 shape_materialization / value_domain / acceptance。
  → prober 出骨架，codegen 见 needs_source 即 fail-closed 停手（这是**对的**：拿不准不硬凑），
    深读/人核补全后再 codegen。绝不假装抠到了。
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "contract_ir.schema.v1.json")

# C 类型 → (kind, binding)。纯机械映射，非 per-op。
_TYPE_MAP = [
    (r"aclTensorList\s*\*", ("tensor_list", "device_tensor")),
    (r"aclScalarList\s*\*", ("scalar_list", "host_scalar")),
    (r"aclTensor\s*\*", ("tensor", "device_tensor")),
    (r"aclScalar\s*\*", ("scalar", "host_scalar")),
    (r"aclIntArray\s*\*", ("array", "host_array")),
    (r"aclFloatArray\s*\*", ("array", "host_array")),
    (r"aclBoolArray\s*\*", ("array", "host_array")),
    (r"(double|float|int64_t|int32_t|int16_t|int8_t|uint64_t|uint32_t|uint16_t|uint8_t|bool|char|int)\b(?!\s*\*)", ("scalar", "host_scalar")),
]
_SOC_A3 = {"ascend910b", "ascend910_93"}
_SOC_A5 = {"ascend950"}

_NEEDS = {"source": "header", "state": "needs_source", "fail_closed": True,
          "conflict_resolution": "prober v1 只抠签名；此字段须 example D2H / glue / 人核坐实（const 不可信）"}


def _find(op_dir, patterns):
    """按 pattern 找文件。primary = 路径最短者（通用规则、非按接口版本特判——去掉了旧的 `_v2` 字符串排除，
    codex #0：不得按接口版本/文件命名偷偷特判）。真正的多入口变体（如 *_v2）属 variant_axis，留 prober v2 处理。"""
    for pat in patterns:
        hits = [h for h in glob.glob(os.path.join(op_dir, pat), recursive=True) if "build" not in h]
        if hits:
            return sorted(hits, key=len)[0]
    return None


def _classify(ctype):
    for rx, (kind, binding) in _TYPE_MAP:
        if re.search(rx, ctype):
            return kind, binding
    return None, None


def _parse_signature(header_text, op):
    """抠 aclnn<Op>GetWorkspaceSize(...) 的有序形参 + 两段式符号。"""
    m = re.search(r"(aclnn\w+GetWorkspaceSize)\s*\(", header_text)
    if not m:
        raise SystemExit("[FAIL] header 里找不到 aclnn*GetWorkspaceSize 声明——非标准两段式，域外")
    gw_sym = m.group(1)
    exec_sym = gw_sym[:-len("GetWorkspaceSize")]
    # 读到匹配的右括号
    start = m.end()
    depth = 1
    i = start
    while i < len(header_text) and depth:
        if header_text[i] == "(":
            depth += 1
        elif header_text[i] == ")":
            depth -= 1
        i += 1
    param_str = header_text[start:i - 1]
    param_str = re.sub(r"\s+", " ", param_str).strip()
    raw = [p.strip() for p in param_str.split(",") if p.strip()]
    params, trailing, order = [], [], []
    pos = 0
    for r in raw:
        # 末尾两个 plumbing：uint64_t* workspaceSize / aclOpExecutor** executor
        if re.search(r"uint64_t\s*\*", r):
            trailing.append("workspaceSize_out")
            continue
        if re.search(r"aclOpExecutor\s*\*\s*\*", r):
            trailing.append("executor_out")
            continue
        name = r.split()[-1].lstrip("*")
        kind, binding = _classify(r)
        if kind is None:
            raise SystemExit(f"[FAIL] 参数 `{r}` 的 C 类型未识别——域外或待扩类型表")
        is_const = r.strip().startswith("const")
        params.append({"raw": r, "name": name, "kind": kind, "binding": binding, "is_const": is_const, "abi_position": pos})
        order.append(name)
        pos += 1
    return gw_sym, exec_sym, params, trailing, order


def _parse_socs(opdef_text):
    socs = re.findall(r'AddConfig\(\s*"([a-z0-9_]+)"', opdef_text)
    return list(dict.fromkeys(socs))


def _target(socs):
    if not socs:
        return {"state": "missing", "source": _NEEDS}
    has_a3 = bool(_SOC_A3 & set(socs))
    has_a5 = bool(_SOC_A5 & set(socs))
    tgt = "a3_and_a5" if (has_a3 and has_a5) else ("a3" if has_a3 else ("a5" if has_a5 else None))
    th = {"opdef_addconfig": socs, "state": "agreed" if tgt else "contested",
          "source": {"source": "op_def", "state": "resolved", "fail_closed": False,
                     "conflict_resolution": "⚠ 权威是**任务书**：prober 只抠 op_def socs。若任务书『适配硬件』不在此 socs 集内 → **可能选错了 PR**（硬约束 #1 Equal 血教训 / canon task-spec-authoritative-over-pr）或该任务在目标平台未落地——须先验证『任务书↔PR 对应』、**绝不按 op_def 自动定机**（task#7 人裁）"}}
    if tgt:
        th["resolved_target"] = tgt
    return th


def probe(op_dir):
    header = _find(op_dir, ["op_host/op_api/aclnn_*.h", "**/aclnn_*.h"])
    opdef = _find(op_dir, ["op_host/*_def.cpp", "**/*_def.cpp"])
    if not header:
        raise SystemExit(f"[FAIL] {op_dir} 下找不到 aclnn_*.h（域外或路径不符）")
    op = os.path.basename(op_dir)
    with open(header, encoding="utf-8") as f:
        htext = f.read()
    gw_sym, exec_sym, params, trailing, order = _parse_signature(htext, op)
    socs = []
    if opdef:
        with open(opdef, encoding="utf-8") as f:
            socs = _parse_socs(f.read())

    # 组 parameter_descriptor —— 签名可抠的 resolved；direction 按 const 启发但**标 needs_source**（const 不可信）
    pdescs, out_ids = [], []
    for p in params:
        # 启发方向：const→in、非 const 张量/列表→（out 候选，须核 inout）
        heur_dir = "in" if p["is_const"] else ("out" if p["kind"] in ("tensor", "tensor_list") else "in")
        card = {"kind": "list", "length_symbol": "N", "min_length": 0, "length_domain": "正整数含0"} if p["kind"] in ("tensor_list", "scalar_list") else {"kind": "single"}
        d = {
            "abi_position": p["abi_position"], "kind": p["kind"], "logical_value_id": p["name"],
            "direction": heur_dir, "presence": "required", "binding": p["binding"],
            "cardinality": card,
            "provenance": {"source": "header", "state": "resolved", "fail_closed": False,
                           "cite": f"{os.path.relpath(header, HERE)} (签名)",
                           "conflict_resolution": "kind/binding/cardinality/顺序 从 header 签名抠 resolved；direction 见其独立标注"},
        }
        if p["kind"] in ("tensor_list", "scalar_list"):
            d["element_descriptor"] = {"abi_position": 0, "kind": p["kind"].replace("_list", ""), "logical_value_id": f"{p['name']}_elem",
                                       "direction": heur_dir, "cardinality": {"kind": "single"}, "binding": p["binding"], "presence": "required",
                                       "provenance": {"source": "header", "state": "resolved", "fail_closed": False}}
        # direction 单独挂 needs_source（承 const 不可信）——codegen 会据此 fail-closed 直到人核
        d["storage_alias"] = {"storage_alias_group": p["name"], "const_untrusted": p["is_const"],
                              "provenance": dict(_NEEDS, cite=f"须 example D2H 源核 direction/alias（{os.path.relpath(header, HERE)}）")}
        pdescs.append(d)
        if heur_dir in ("out", "inout") or not p["is_const"] and p["kind"] in ("tensor", "tensor_list"):
            out_ids.append(p["name"])

    # 输出描述：shape/value_domain/acceptance 一律 needs_source（须 infershape/glue/任务书）
    outputs = []
    for oid in out_ids:
        outputs.append({
            "logical_value_id": oid,
            "shape_materialization": {"modes": [{"mode": "host_oracle", "extent_readback": "exact_prealloc"}],
                                      "provenance": dict(_NEEDS, cite="须 infershape + example 核 out extent 来源")},
            "value_domain": {"kind": "follows", "follows_ref": order[0] if order else oid,
                             "provenance": dict(_NEEDS, cite="须 op_def DataType + glue 核每输出 dtype 规则")},
            "acceptance": {"kind": "pointwise", "threshold_source": {"tier": "real_machine_pinned",
                           "provenance": dict(_NEEDS, cite="须任务书精度目标 / 真机对拍钉阈值")}},
        })

    ir = {
        "ir_version": "1.0", "op": op,
        "aclnn_entry": {"exec_symbol": exec_sym, "getworkspace_symbol": gw_sym,
                        "include_header": os.path.relpath(header, os.path.join(HERE, "..", "..", "..", "repos")) if "repos" in header else header,
                        "source": {"source": "header", "state": "resolved", "fail_closed": False, "cite": os.path.relpath(header, HERE)}},
        "applicability": {"in_domain": True, "reason": "prober v1 认为标准两段式；opaque/有状态须后续核"},
        "abi_signature": {"stage1": {"ordered_param_ids": order, "trailing": trailing},
                          "stage2": {"full_signature": f"{exec_sym}(void* workspace, uint64_t workspaceSize, aclOpExecutor* executor, aclrtStream stream)",
                                     "probe": "matched"}},
        "parameters": pdescs, "outputs": outputs,
        "output_mapping": {"output_edges": [], "provenance": dict(_NEEDS, cite="须 glue std::get→ViewCopy 核声明序→槽序（默认非恒等）")},
        "target_hardware": _target(socs),
    }
    return ir


def main(argv=None):
    ap = argparse.ArgumentParser(description="U7 通用探测器：算子目录 → 契约 IR 骨架（语义未抠处 fail-closed）")
    ap.add_argument("op_dir", help="算子目录，如 repos/ops-nn/foreach/foreach_add_list")
    ap.add_argument("-o", "--out", help="输出 IR JSON 路径；缺省打印 stdout")
    ap.add_argument("--validate", action="store_true", help="产出后按 Schema 校验")
    args = ap.parse_args(argv)
    ir = probe(os.path.abspath(args.op_dir))
    if args.validate:
        try:
            import jsonschema
            with open(SCHEMA_PATH, encoding="utf-8") as f:
                jsonschema.Draft202012Validator(json.load(f)).validate(ir)
            sys.stderr.write("✓ IR 骨架符合 Schema\n")
        except ImportError:
            sys.stderr.write("(jsonschema 未装，跳过校验)\n")
    text = json.dumps(ir, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        sys.stderr.write(f"✓ {args.out}（⚠ 语义字段 needs_source，codegen 会 fail-closed 直到人核补全）\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
