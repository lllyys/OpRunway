#!/usr/bin/env python3
"""校验 facts.json：字段齐备、dtype 在 ATK 词表内、每项带 source。

顺带判定要不要写 CPU 执行器（见 `_probe_baseline`），结论打印在末尾。
**这一项不影响退出码**：要不要写执行器是 S2 的活儿，不是 facts.json 的缺陷。

退出码 0 通过，2 内容不合格，3 JSON 读不出来，
4 基线接口是自己推断的、还没经用户确认（见 `baseline_source`）。
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
# 基线接口比别的字段多一档 `user`：任务书不写基线是常态，agent 推断出来的
# 候选必须先经用户拍板才能往下走，拍完把这里改成 user。
BASELINE_SOURCES = {"taskdoc", "opdoc", "inferred", "user"}
ATK_TYPES = {"tensor", "tensors", "tensor_tuple", "scalar", "scalars",
             "attr", "attrs", "attr_tuple"}
PERF_KINDS = {"none", "builtin", "cross_dtype", "threshold"}
# threshold 是「任务书给了要求，但比较对象不是另一个实现」的那一档：
# roofline 百分比、绝对耗时上限、带宽利用率都归这里。没有这一档时它们
# 只能填 none，而 none 的语义是「任务书没有性能要求」——要求就此丢失，
# 跑测侧永远拿不到，报告里写成「未评级(无基线)」，没人看得出少了一条。
# 精度基线是谁。torch 是默认；builtin 指 CANN 装机目录里的同名内置 aclnn 接口，
# 只有「优化/重构同一个接口」这类任务能用——两侧同名同签名才谈得上逐位可比。
# 精度标杆的三种来源。**plugin 那档是 ATK 的正规形态**，不是绕路：
# DesignConfig 的 name 是 Optional，api_type 单独就能定执行器
# （atk/configs/design_config.py:449、456）。
ACC_KINDS = {"torch", "builtin", "plugin"}
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
    if kind == "threshold":
        criterion = perf.get("criterion")
        if not isinstance(criterion, str) or not criterion.strip():
            _err(problems, "performance.kind=threshold 时必须给 criterion，"
                           "照抄任务书原话，形如 \"达到 compute/memory bound 的 80%\"。"
                           "跑测侧判不了这类指标，靠它把要求原样带进报告")
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
    _check_sampling(problems, perf)


def _check_sampling(problems, perf):
    """`performance.sampling` 的格式。任务书规定了预热与采样次数时才有这个字段。

    不强制必填——多数任务书不规定采样口径。**填了就三项都要**：跑测侧的外部
    量测件按 `{warmup, samples}` 采，`source` 是这两个数的出处，缺了它没法在
    报告里证明采的是验收口径而不是量测件的缺省值。
    """
    sampling = perf.get("sampling")
    if sampling is None:
        return
    if not isinstance(sampling, dict):
        _err(problems, "performance.sampling 要么不写，要么是 "
                       "{\"warmup\": 10, \"samples\": 30, \"source\": \"任务书 …\"}")
        return
    for field in ("warmup", "samples"):
        value = sampling.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            _err(problems, f"performance.sampling.{field} 缺失或不是正整数。"
                           f"照抄任务书规定的次数，这是验收口径不是量测件的调参")
    source = sampling.get("source")
    if not isinstance(source, str) or not source.strip():
        _err(problems, "performance.sampling.source 缺失：填任务书里规定这两个数的"
                       "那句话的出处，形如 \"任务书 性能要求 第 3 条\"")


def _has_tensor_input(facts):
    """用例形态：`params[]` 里有没有张量入参。

    有就是「真张量声明」——ATK 造好喂进来，输入字节冻得下、非连续切得到。
    没有就是「attr 编码」，张量由执行器按用例里的 seed 现造。
    **这条轴与 `backend` 正交**：aclnn 剖面必然是真张量，npu 剖面两种都有。
    """
    for param in facts.get("params") or []:
        if not isinstance(param, dict):
            continue
        if param.get("role") == "input" and param.get("atk_type") in {"tensor", "tensors"}:
            return True
    return False


def _check_top(problems, facts):
    for field in ("op", "aclnn_name", "baseline"):
        if not facts.get(field):
            _err(problems, f"顶层 {field} 缺失")

    baseline = str(facts.get("baseline", ""))
    acc_kind = (facts.get("accuracy") or {}).get("kind", "torch")
    if acc_kind == "plugin":
        # 标杆由执行器给出，不是 ATK 直接 eval 的 torch 接口名。两种情形都在这一档：
        # 接口存在但 ATK 调不动（要先把入参拼成一个结构，或按任务书换算值 dtype），
        # 以及压根没有语义等价的公开接口。**只有后者要降级说明结论强度**——
        # 判据是算值那一步是谁跑的，见 precision-standard.md。
        # 这里校不出是哪一种（执行器内部 checker 看不见），由写的人按那份判据表判。
        if not baseline.startswith("function_"):
            _err(problems, f"accuracy.kind=plugin 时 baseline={baseline!r} 要填执行器的"
                           f"注册名，形如 function_<op>_cpu，与 YAML 的 api_type 逐字相同")
    elif baseline and not baseline.startswith("torch."):
        _err(problems, f"baseline={baseline!r} 不是 torch.xxx 形式，CPU 标杆 eval 不出来。"
                       f"没有语义等价的公开接口，或者接口有但 ATK 按序透传调不动"
                       f"（入参要先拼成一个结构、值要换算 dtype）时，改填 "
                       f"accuracy.kind=plugin 并把 baseline 填成执行器的注册名")

    baseline_source = facts.get("baseline_source")
    if baseline_source is None:
        _err(problems, "顶层 baseline_source 缺失，取值 "
                       f"{'/'.join(sorted(BASELINE_SOURCES))}：任务书点名了填 taskdoc，"
                       "工程文档点名了填 opdoc，自己推断的填 inferred，"
                       "用户拍板过的填 user")
    elif baseline_source not in BASELINE_SOURCES:
        _err(problems, f"baseline_source={baseline_source!r} 不合法，"
                       f"只能是 {'/'.join(sorted(BASELINE_SOURCES))}")

    aclnn_name = str(facts.get("aclnn_name", ""))
    if aclnn_name.startswith("aclnn"):
        _err(problems, f"aclnn_name={aclnn_name!r} 带了 aclnn 前缀，"
                       f"应写 {aclnn_name[len('aclnn'):]!r}")

    focus = facts.get("focus_dtypes")
    if focus is not None:
        tensor_dtypes = set()
        for param in facts.get("params", []):
            if param.get("role") == "input" and param.get("atk_type") in {"tensor", "tensors"}:
                tensor_dtypes |= {str(d) for d in param.get("dtypes") or []}
                break
        if not isinstance(focus, list) or not focus:
            _err(problems, "focus_dtypes 要么不写，要么是非空列表，"
                           "形如 [\"uint8\"]——任务书点名要新增/重点测的那几种 dtype")
        else:
            stray = [d for d in focus if str(d) not in tensor_dtypes]
            if stray:
                _err(problems, f"focus_dtypes 里的 {'、'.join(map(str, stray))} "
                               f"不在第一个张量输入的 dtypes 里，生成时会整组落空")

    # backend 定的是**算子怎么被调起来**，判据是接口形态，不是算子属于哪个仓。
    # 两个取值对应 ATK 的两个后端，跑测侧读它选执行剖面（只读，不重判）。
    backend = facts.get("backend", "aclnn")
    if backend not in ("aclnn", "npu"):
        _err(problems, f"backend={backend!r} 不在 aclnn／npu 里。"
                       f"有 aclnn<Op>GetWorkspaceSize 两段式声明填 aclnn，"
                       f"算子注册进 torch 的 dispatch 填 npu——新建命名空间的"
                       f"（torch.ops.<ns>.<name>）与注册进 ATen 已有算子 NPU 键的"
                       f"（调用面是公开 torch API）都算")
    elif backend == "npu":
        _check_npu_backend(problems, facts)

    signature = facts.get("signature")
    if not isinstance(signature, dict) or not signature.get("text"):
        _err(problems, "signature.text 缺失，参数顺序没有依据")
    else:
        # 两段式的原型才有 GetWorkspaceSize。npu 剖面的接口是一段式的，
        # 原型照样要填——参数顺序的唯一依据是它，与后端无关。
        if backend == "aclnn" and "GetWorkspaceSize" not in signature["text"]:
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
        kind = accuracy.get("kind", "torch")
        if kind not in ACC_KINDS:
            _err(problems, f"accuracy.kind={kind!r} 不合法，只能是 {'/'.join(sorted(ACC_KINDS))}")
        elif kind == "builtin" and not str(facts.get("baseline", "")).startswith("torch."):
            _err(problems, "accuracy.kind=builtin 时 baseline 仍要填 torch 接口："
                           "冻 golden 那一轮要靠它给出输出的 shape 与 dtype，值不参与比对")
        _check_source(problems, "accuracy", accuracy)

    non_contiguous = facts.get("non_contiguous")
    if not isinstance(non_contiguous, dict) or not isinstance(
            non_contiguous.get("required"), bool):
        _err(problems, "non_contiguous.required 缺失或不是 true/false。"
                       "任务书参数表的「非连续Tensor」列打勾就填 true")
    else:
        _check_source(problems, "non_contiguous", non_contiguous)
        # 判据是**用例形态**，不是 backend：`--slice_input` 在 ATK 的后端基类里
        # 对已载入的输入张量做（`atk/tasks/backends/backend.py:160`），两个剖面
        # 都生效。切不到的只有一种情形——用例入参全是 attr，张量由执行器现造。
        # 那时填 true 会多跑一整轮，两轮字节完全相同，而且不报错。
        if non_contiguous["required"] and not _has_tensor_input(facts):
            _err(problems, "non_contiguous.required=true，但 params 里没有 "
                           "atk_type 为 tensor／tensors 的输入。"
                           "--slice_input 切的是用例声明的张量，入参全是 attr 时"
                           "切不到执行器现造的那份，这一轮会白跑且两轮字节相同。"
                           "改填 false；张量本该由用例声明的，回去补 params")

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



def _check_npu_backend(problems, facts):
    """backend=npu 时另外两个字段的取值受限。

    两条都不是「sparse 特殊」，是 npu 剖面的机制决定的：跑测侧那边没有 aclnn
    接口、没有 opp vendor 层，靠它们工作的档位在这条路上不成立。
    **不校验的话跑测侧退 3，而报错指向别处。**

    `non_contiguous.required` **不在这里限制**：`--slice_input` 切的是用例里
    声明的张量，与后端无关。算子按正常范式声明真张量时，这一路照样能跑。
    """
    acc_kind = (facts.get("accuracy") or {}).get("kind", "torch")
    if acc_kind == "builtin":
        _err(problems, "backend=npu 时 accuracy.kind 不能是 builtin："
                       "它是拿 CANN 装机目录里的内置同名 aclnn 接口当标杆，"
                       "这条路上没有 aclnn 接口。torch 有逐位对应、ATK 按序透传就"
                       "调得动的接口填 torch；接口有但要先拼输入结构或换算值 dtype "
                       "才调得动的，以及压根没有等价接口的，都写执行器并填 plugin")
    perf_kind = (facts.get("performance") or {}).get("kind")
    if perf_kind == "builtin":
        _err(problems, "backend=npu 时 performance.kind 不能是 builtin："
                       "它靠摘掉 opp vendor 层回落到内置实现，这条路上没有那一层。"
                       "跑测侧会退 3。任务书给了基线数值就填 threshold")

def _probe_baseline(facts):
    """判定要不要写 CPU 执行器。返回 (结论, 说明)。

    五条判据，前三条静态、后两条实测：

    1. `accuracy.kind=builtin` 必写——那一轮起 pyaclnn + cpu 两个节点，
       cpu 节点要给出 `out` 的形状与 dtype，pyaclnn 推不出来。
    2. 多输出算子必写——torch 返回具名元组，ATK 要普通元组才能拆成多个输出
       张量（`atk/tasks/api_execute/aclnn_base_api.py`）。
    3. 输出是张量列表（`role=output` 且 `atk_type=tensors`，即 `aclTensorList*`
       出参）必写——torch 返回的是普通 list，要包成「列表里一个元组」。
       **eval 探针查不出这条**：调用本身成功，错的是返回值的嵌套层数，
       到 S4 才表现为 golden 的 `output_info.json` 摊成每张量一项，
       NPU 侧参数个数对不上。
    4. 按 signature 的 C 类型造一组实参，真的 eval 一次基线。
       `TypeError` 就是 ATK 默认的 `eval(name)(*args)` 调不动它。
    5. 再逐个 dtype 各 eval 一次。基线在某个 dtype 上不支持时，
       那批用例的 golden 会在 S4 全灭，而形态探针看不出来。

    探针不验算得对不对，也不验取值相关的行为（除零、溢出）。
    非 TypeError 的异常一律报「待定」，因为多半是探针入参取值不合适，
    不能据此下结论。
    """
    if (facts.get("accuracy") or {}).get("kind") == "builtin":
        return "需要", ("accuracy.kind=builtin：cpu 节点要给出 out 的形状与 dtype，"
                        "写法见 references/builtin-baseline.md")
    if (facts.get("output") or {}).get("kind") == "multi":
        return "需要", "多输出算子：torch 返回具名元组，ATK 要普通元组才能拆开"
    listed = [p.get("name") for p in facts.get("params") or []
              if p.get("role") == "output" and p.get("atk_type") == "tensors"]
    if listed:
        return "需要", (f"出参 {'、'.join(listed)} 是张量列表（aclTensorList*）："
                        "torch 返回普通 list，要包成「列表里一个元组」，"
                        "照抄 assets/function_foreach_mul_list.py")

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
        return "待定", f"{call} 报 {type(exc).__name__}：{exc}。手动试一次这个调用。"

    bad = _probe_dtypes(torch, facts, baseline, ctypes_by_name)
    if bad:
        return "需要", ("基线在 " + "、".join(bad) + " 上不支持（" + call
                        + " 形态本身跑通）。这些 dtype 的 golden 会在 S4 全灭，"
                          "执行器里转成支持的 dtype 算完再转回")
    return "不需要", f"{call} 直接跑通，ATK 默认执行器够用"


def _probe_dtypes(torch, facts, baseline, ctypes_by_name):
    """逐个声明的 dtype 各 eval 一次，返回跑不通的那些。

    形态探针只用一个 dtype 造参，基线在别的 dtype 上不支持时它看不出来
    （实测 `torch.nn.functional.pdist` 不收 fp16）。造不出实参的 dtype
    直接跳过，不下结论。
    """
    declared = sorted({d for param in facts.get("params", [])
                       if param.get("role") == "input"
                       for d in param.get("dtypes") or []})
    bad = []
    for name in declared:
        if name not in PROBE_TORCH_DTYPE:
            continue
        args = []
        for param in facts.get("params", []):
            if param.get("role") != "input":
                continue
            ctype = ctypes_by_name.get(param.get("name", ""))
            dtypes = [name] if ctype == "aclTensor" else param.get("dtypes", [])
            try:
                args.append(_probe_value(torch, ctype, dtypes))
            except KeyError:
                return []
        try:
            eval(baseline)(*args)  # noqa: S307  探针，baseline 已校验为 torch.*
        except (TypeError, RuntimeError, NotImplementedError):
            bad.append(name)
        except Exception:  # noqa: BLE001  取值相关的异常不算 dtype 不支持
            continue
    return bad


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
    # 推断项要逐项报出来，交付简表的「遗留」行照抄。**不止 params**：
    # signature 是参数顺序的唯一依据，constraints 直接变成约束器里的条件，
    # 这两处推错了，用例整批是错的而四道出口判据一道都不响。
    inferred = [p["name"] for p in params if p.get("source") == "inferred"]
    if (facts.get("signature") or {}).get("source") == "inferred":
        inferred.append("signature（参数顺序没有文档依据）")
    guessed = [c.get("text", "?") for c in (facts.get("constraints") or [])
               if c.get("source") == "inferred"]
    inferred += [f"约束「{t[:24]}」" for t in guessed]
    dtypes = sorted({d for p in params for d in p.get("dtypes", [])})
    print(f"算子      {facts['op']}  (aclnn{facts['aclnn_name']})")
    print(f"基线      {facts['baseline']}")
    print(f"参数      {len(params)} 个：{'、'.join(p['name'] for p in params)}")
    print(f"dtype     {'、'.join(dtypes)}")
    print(f"秩        {facts['shape']['rank'][0]}–{facts['shape']['rank'][1]}")
    print(f"约束      {len(facts['constraints'])} 条")
    print(f"精度基线  {facts['accuracy'].get('kind', 'torch')}")
    print(f"性能      {facts['performance']['kind']}")
    if facts.get("focus_dtypes"):
        print(f"重点 dtype {'、'.join(facts['focus_dtypes'])} —— "
              f"gen_cases.py 会拆两轮生成，让它们在全量里占一半")
    if facts["non_contiguous"]["required"]:
        print("非连续    要测 —— 跑测侧会加 --slice_input non_contiguous")

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

    # 基线接口是精度比对的真值来源，选谁不该由 agent 独自定。任务书不写基线是常态，
    # 所以这条会经常触发——它便宜（一次一个算子），而选错的代价是 S3、S4 整轮重跑。
    if facts.get("baseline_source") == "inferred":
        print(f"\n阻塞      基线 {facts['baseline']} 是推断出来的，还没经用户确认。")
        print("          **正常路径是写 facts.json 之前就问**（见 SKILL.md S1）；"
              "走到这一步说明先写了再问。")
        print("          现在补问一次，把下面三项摆给用户，等回话再进 S2：")
        print(f"            1. 打算用哪个接口当基线：{facts['baseline']}")
        print("            2. 它的参数与 aclnn 签名是不是逐位对应"
              "（不对应就要写执行器，见 plugin-authoring.md）")
        print("            3. 依据是什么")
        print("          用户认可就把 baseline_source 改成 user 重跑；"
              "用户不认可就用用户指定的接口。")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
