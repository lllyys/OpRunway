#!/usr/bin/env python3
"""校验 facts.json：字段齐备、dtype 在 ATK 词表内、每项带 source。

退出码 0 通过，2 内容不合格，3 JSON 读不出来。
"""

import argparse
import json
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
    if inferred:
        print(f"\n推断项    {'、'.join(inferred)} —— 这些没有文档依据，报告里要单列。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
