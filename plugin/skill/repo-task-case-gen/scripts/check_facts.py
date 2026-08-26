#!/usr/bin/env python3
"""校验 facts.json：字段齐备、dtype 在 ATK 词表内、每项带 source。

顺带判定要不要写 CPU 执行器（见 `_probe_baseline`），结论打印在末尾。
**这一项不影响退出码**：要不要写执行器是 S2 的活儿，不是 facts.json 的缺陷。

退出码 0 通过，2 内容不合格，3 JSON 读不出来。
"""

import argparse
import json
import os
import sys

TENSOR_DTYPES = {
    "fp64", "fp32", "fp16", "bf16", "hf32", "tf32",
    "int64", "int32", "int16", "int8",
    "uint64", "uint32", "uint16", "uint8",
    "bool", "complex64", "complex128", "fp8e4m3", "fp8e5m2",
}

ATTR_DTYPES = {
    "int", "int8_t", "int32_t", "int64_t", "uint8_t", "uint32_t", "uint64_t",
    "double", "float", "attr_bool", "string", "non_param",
}

# 文档里的写法 -> ATK 词表写法。用来把「没翻译」和「拼错了」分开报。
DOC_ALIASES = {
    "float": "fp32", "float32": "fp32", "float16": "fp16", "float64": "fp64",
    "double": "fp64", "bfloat16": "bf16", "half": "fp16",
    "int": "int32", "long": "int64", "short": "int16", "byte": "int8",
    "complex": "complex64",
}

SOURCES = {"taskdoc", "opdoc", "inferred"}
ATK_TYPES = {"tensor", "tensors", "tensor_tuple", "scalar", "scalars",
             "attr", "attrs", "attr_tuple"}
PERF_KINDS = {"none", "builtin", "cross_dtype"}
OUTPUT_KINDS = {"single", "multi", "inplace"}

DROP_PARAMS = {"workspacesize", "executor", "workspace", "stream"}

# ATK dtype -> torch dtype 名。只给探针造张量用，不是完整映射。
PROBE_TORCH_DTYPE = {
    "fp64": "float64", "fp32": "float32", "fp16": "float16", "bf16": "bfloat16",
    "int64": "int64", "int32": "int32", "int16": "int16", "int8": "int8",
    "uint8": "uint8", "bool": "bool", "complex64": "complex64",
}


def _err(problems, text):
    problems.append(text)


def _check_source(problems, where, node):
    source = node.get("source")
    if source is None:
        _err(problems, f"{where} 缺 source 字段")
    elif source not in SOURCES:
        _err(problems, f"{where} 的 source={source!r} 不合法，只能是 {'/'.join(sorted(SOURCES))}")


def _check_dtype(problems, where, dtype, atk_type):
    vocab = ATTR_DTYPES if atk_type in {"attr", "attrs", "attr_tuple"} else TENSOR_DTYPES
    if dtype in vocab:
        return
    lowered = dtype.lower()
    if lowered in vocab:
        _err(problems, f"{where} 的 dtype {dtype!r} 大小写不对，应写 {lowered!r}")
    elif lowered in DOC_ALIASES:
        _err(problems, f"{where} 的 dtype {dtype!r} 是文档写法，没翻译成 ATK 词表，"
                       f"应写 {DOC_ALIASES[lowered]!r}")
    else:
        _err(problems, f"{where} 的 dtype {dtype!r} 不在 ATK 词表里")


def _check_params(problems, facts):
    params = facts.get("params")
    if not isinstance(params, list) or not params:
        _err(problems, "params 缺失或为空，至少要有一个输入参数")
        return

    seen_output = False
    for index, param in enumerate(params):
        where = f"params[{index}]({param.get('name', '?')})"
        name = str(param.get("name", "")).lower()
        if name in DROP_PARAMS:
            _err(problems, f"{where} 是 pyaclnn 自动补的参数，不要列进 params")
            continue

        atk_type = param.get("atk_type")
        if atk_type not in ATK_TYPES:
            _err(problems, f"{where} 的 atk_type={atk_type!r} 不合法，"
                           f"只能是 {'/'.join(sorted(ATK_TYPES))}")
            atk_type = "tensor"

        role = param.get("role")
        if role not in {"input", "output"}:
            _err(problems, f"{where} 的 role={role!r} 不合法，只能是 input/output")
        if role == "output":
            seen_output = True
        elif seen_output:
            _err(problems, f"{where} 是 input 但排在 output 之后，params 要按签名顺序列")

        dtypes = param.get("dtypes")
        if not isinstance(dtypes, list) or not dtypes:
            _err(problems, f"{where} 的 dtypes 缺失或为空")
        else:
            for dtype in dtypes:
                _check_dtype(problems, where, str(dtype), atk_type)

        _check_source(problems, where, param)


def _check_multi_outputs(problems, output):
    """多输出算子必须逐个声明 dtype，`freeze_golden.py` 靠它查输出有没有摊反。

    dtype 写 ATK 词表里的值，或 `same_as_input`（与第一个输入张量同 dtype）。
    """
    outputs = output.get("outputs")
    if not isinstance(outputs, list) or len(outputs) < 2:
        _err(problems, "output.kind=multi 时必须给 outputs，按签名顺序逐个列，"
                       "形如 [{\"name\": \"valuesOut\", \"dtype\": \"same_as_input\"}, "
                       "{\"name\": \"indicesOut\", \"dtype\": \"int64\"}]")
        return
    for index, item in enumerate(outputs):
        where = f"output.outputs[{index}]"
        if not isinstance(item, dict) or not item.get("name"):
            _err(problems, f"{where} 缺 name")
            continue
        dtype = item.get("dtype")
        if dtype == "same_as_input":
            continue
        if dtype not in TENSOR_DTYPES:
            _err(problems, f"{where}({item['name']}) 的 dtype={dtype!r} 既不在 ATK 词表里，"
                           f"也不是 same_as_input")


def _check_perf(problems, facts):
    perf = facts.get("performance")
    if not isinstance(perf, dict):
        _err(problems, "performance 缺失，任务书没写性能要求时填 {\"kind\": \"none\"}")
        return
    kind = perf.get("kind")
    if kind not in PERF_KINDS:
        _err(problems, f"performance.kind={kind!r} 不合法，只能是 {'/'.join(sorted(PERF_KINDS))}")
    if kind == "cross_dtype":
        pairs = perf.get("pairs")
        if not isinstance(pairs, list) or not pairs:
            _err(problems, "performance.kind=cross_dtype 时必须给 pairs，"
                           "形如 [[\"int8\", \"int32\"]]")
        else:
            for pair in pairs:
                if not (isinstance(pair, list) and len(pair) == 2):
                    _err(problems, f"performance.pairs 的 {pair!r} 不是 [被测dtype, 基线dtype] 二元组")
    _check_source(problems, "performance", perf)


def _check_top(problems, facts):
    for field in ("op", "aclnn_name", "baseline"):
        if not facts.get(field):
            _err(problems, f"顶层 {field} 缺失")

    baseline = str(facts.get("baseline", ""))
    if baseline and not baseline.startswith("torch."):
        _err(problems, f"baseline={baseline!r} 不是 torch.xxx 形式，CPU 标杆 eval 不出来")

    aclnn_name = str(facts.get("aclnn_name", ""))
    if aclnn_name.startswith("aclnn"):
        _err(problems, f"aclnn_name={aclnn_name!r} 带了 aclnn 前缀，"
                       f"应写 {aclnn_name[len('aclnn'):]!r}")

    backend = facts.get("backend", "aclnn")
    if backend != "aclnn":
        _err(problems, f"backend={backend!r}，社区算子固定走 aclnn")

    signature = facts.get("signature")
    if not isinstance(signature, dict) or not signature.get("text"):
        _err(problems, "signature.text 缺失，参数顺序没有依据")
    else:
        if "GetWorkspaceSize" not in signature["text"]:
            _err(problems, "signature.text 不含 GetWorkspaceSize，取的不是一段式原型")
        _check_source(problems, "signature", signature)

    shape = facts.get("shape")
    if not isinstance(shape, dict):
        _err(problems, "shape 缺失，需要 rank 区间才能写 dim_numbers")
    else:
        rank = shape.get("rank")
        if not (isinstance(rank, list) and len(rank) == 2
                and all(isinstance(v, int) for v in rank) and rank[0] <= rank[1]):
            _err(problems, f"shape.rank={rank!r} 不是 [最小, 最大] 整数区间")
        _check_source(problems, "shape", shape)

    output = facts.get("output")
    if not isinstance(output, dict):
        _err(problems, "output 缺失")
    else:
        if output.get("kind") not in OUTPUT_KINDS:
            _err(problems, f"output.kind={output.get('kind')!r} 不合法，"
                           f"只能是 {'/'.join(sorted(OUTPUT_KINDS))}")
        elif output.get("kind") == "multi":
            _check_multi_outputs(problems, output)
        _check_source(problems, "output", output)

    accuracy = facts.get("accuracy")
    if not isinstance(accuracy, dict) or not accuracy.get("acc"):
        _err(problems, "accuracy.acc 缺失，社区任务书一般填 \"default\"")
    else:
        _check_source(problems, "accuracy", accuracy)

    constraints = facts.get("constraints")
    if not isinstance(constraints, list):
        _err(problems, "constraints 缺失，没有约束就填空列表 []")
    else:
        for index, item in enumerate(constraints):
            if not isinstance(item, dict) or not item.get("text"):
                _err(problems, f"constraints[{index}] 缺 text")
            else:
                _check_source(problems, f"constraints[{index}]", item)


def _parse_c_params(text):
    """从 signature.text 里取每个参数的 C 类型，键是参数名。

    两种写法都要收：`const aclTensor* x` 与 `const aclTensor *self`。
    """
    try:
        inner = text[text.index("(") + 1:text.rindex(")")]
    except ValueError:
        return {}
    table = {}
    for piece in inner.split(","):
        tokens = piece.replace("const", " ").replace("*", " ").split()
        if len(tokens) < 2:
            continue
        table[tokens[-1]] = " ".join(tokens[:-1])
    return table


def _probe_value(torch, ctype, dtypes):
    """按 C 类型造一个探针实参。造不出来就抛 KeyError，由调用方降级为「待定」。"""
    if ctype == "aclTensor":
        name = next((PROBE_TORCH_DTYPE[d] for d in dtypes
                     if d in PROBE_TORCH_DTYPE), "float32")
        dtype = getattr(torch, name)
        if dtype == torch.bool:
            return torch.zeros(3, 3, dtype=dtype)
        if not dtype.is_floating_point and not dtype.is_complex:
            return torch.ones(3, 3, dtype=dtype)
        return torch.rand(3, 3).to(dtype)
    if ctype == "aclTensorList":
        return [torch.rand(3, 3)]
    if ctype in {"aclIntArray", "aclBoolArray", "aclFloatArray"}:
        return [0]
    if ctype == "aclScalar":
        return 0.0
    if ctype in {"int64_t", "int32_t", "int", "uint64_t", "uint32_t", "int16_t"}:
        return 0
    if ctype == "bool":
        return False
    if ctype in {"float", "double"}:
        return 0.0
    raise KeyError(ctype)


def _probe_baseline(facts):
    """判定要不要写 CPU 执行器。返回 (结论, 说明)。

    两条判据，第一条静态、第二条实测：

    1. 多输出算子必写——torch 返回具名元组，ATK 要普通元组才能拆成多个输出
       张量（`atk/tasks/api_execute/aclnn_base_api.py`）。
    2. 其余算子按 signature 的 C 类型造一组实参，真的 eval 一次基线。
       `TypeError` 就是 ATK 默认的 `eval(name)(*args)` 调不动它。

    探针只验**类型形态**，不验 dtype 支持面，也不验算得对不对。
    非 TypeError 的异常一律报「待定」，因为多半是探针入参取值不合适，
    不能据此下结论。
    """
    if (facts.get("output") or {}).get("kind") == "multi":
        return "需要", "多输出算子：torch 返回具名元组，ATK 要普通元组才能拆开"

    # 装了 torch_npu 的机器上，没 source CANN 时 `import torch` 会去自动加载
    # torch_npu 后端并抛 RuntimeError（不是 ImportError）。生成侧只要 CPU 版
    # torch，关掉自动加载即可，不该因此要求 CANN 环境。
    os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")
    try:
        import torch  # noqa: F401
    except Exception as exc:  # noqa: BLE001  装没装、装坏没坏都只降级不中断
        return "待定", f"import torch 失败（{type(exc).__name__}：{exc}），探针跑不了"

    baseline = facts.get("baseline", "")
    ctypes_by_name = _parse_c_params((facts.get("signature") or {}).get("text", ""))
    args = []
    for param in facts.get("params", []):
        if param.get("role") != "input":
            continue
        name = param.get("name", "")
        ctype = ctypes_by_name.get(name)
        if ctype is None:
            return "待定", f"signature 里找不到参数 {name!r}，两者对不上，探针跳过"
        try:
            args.append(_probe_value(torch, ctype, param.get("dtypes", [])))
        except KeyError:
            return "待定", f"参数 {name!r} 的 C 类型 {ctype!r} 探针不认识，手动试一次"

    call = f"{baseline}({', '.join(type(a).__name__ for a in args)})"
    try:
        eval(baseline)(*args)  # noqa: S307  探针，baseline 已校验为 torch.*
    except TypeError as exc:
        return "需要", f"{call} 报 TypeError：{exc}"
    except Exception as exc:  # noqa: BLE001  非类型问题不下结论
        return "待定", f"{call} 报 {type(exc).__name__}：{exc}。多半是探针取值不合适，手动试一次。"
    return "不需要", f"{call} 直接跑通，ATK 默认执行器够用"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("facts", nargs="?", default="facts.json")
    args = parser.parse_args()

    try:
        with open(args.facts, encoding="utf-8") as handle:
            facts = json.load(handle)
    except FileNotFoundError:
        print(f"{args.facts} 不存在。先按 references/interface-facts.md 建事实表。",
              file=sys.stderr)
        return 3
    except json.JSONDecodeError as exc:
        print(f"{args.facts} 不是合法 JSON：{exc}", file=sys.stderr)
        return 3

    problems = []
    _check_top(problems, facts)
    _check_params(problems, facts)
    _check_perf(problems, facts)

    if problems:
        print(f"facts.json 有 {len(problems)} 处不合格：", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 2

    params = facts["params"]
    inferred = [p["name"] for p in params if p.get("source") == "inferred"]
    dtypes = sorted({d for p in params for d in p.get("dtypes", [])})
    print(f"算子      {facts['op']}  (aclnn{facts['aclnn_name']})")
    print(f"基线      {facts['baseline']}")
    print(f"参数      {len(params)} 个：{'、'.join(p['name'] for p in params)}")
    print(f"dtype     {'、'.join(dtypes)}")
    print(f"秩        {facts['shape']['rank'][0]}–{facts['shape']['rank'][1]}")
    print(f"约束      {len(facts['constraints'])} 条")
    print(f"性能      {facts['performance']['kind']}")

    verdict, detail = _probe_baseline(facts)
    print(f"\nCPU 执行器  {verdict}")
    print(f"           {detail}")
    if verdict == "需要":
        print("           写法见 references/plugin-authoring.md「执行器」，"
              "YAML 里接 api_type: function_<op>_cpu")
    elif verdict == "待定":
        print("           探针没结论，按 plugin-authoring.md 的判据表自己判一次")

    if inferred:
        print(f"\n推断项    {'、'.join(inferred)} —— 这些没有文档依据，报告里要单列。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
