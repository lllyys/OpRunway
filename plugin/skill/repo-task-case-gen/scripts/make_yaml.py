"""从物化后的 must_cover.json 推导设计 YAML。

设计 YAML 里没有一个字段是新信息：输入类型由 `parameters` 的语义契约唯一确定，
dtype / rank / 维度值 / tuple_numbers 全部照 `combos` 里的实际取值写，其余是常量。
让模型手写它，等于让模型手工重算一遍已经算好的东西——错的只会是它，不会是源。

以前这些推导关系由 check_atk_capabilities.py 单独校验一遍（P1/P2/P9/P10/P11/
P13/P17/P18/P19/P20/P21），但那个脚本校验的是本文件的产物——脚本自己刚生成
的东西不需要再校验，已删除。仍有价值的三条（语义轴指向存在的 attr、序列语义
不与标量/none 混用、序列语义有合法的 runtime_container）校验的是 agent 手写
的 axes/extract 规则，移到本文件的 `_check_semantic_axes`。

必须在**物化之后**运行：tuple_numbers 取自 combos 里 attr 列表的实际长度，
dim_numbers 取自实际 shape，物化前这些字段还不存在。

退出码：0 完成；2 声明不完整或推导冲突。
"""

import argparse
import json
import sys
from pathlib import Path

from _case_utils import load_json
from _expressibility import (DEFAULT_TOKEN, EMPTY_GROUP, INT_DTYPE_RANGES,
                             TYPE_BY_SEMANTICS, check_axis_values,
                             check_contracts)
import _stage_card

CHANNELS = ("inputs", "method_inputs", "tensor_input")

# yaml 块允许的头部键。三个接线键各管一侧：api_type 绑基线执行器、
# aclnn_api_type 绑待验收算子执行器、generate 绑生成器；缺哪个就静默走该侧的
# ATK 默认路径，不会报「没配」——所以拼错必须在这里当场拦下。
YAML_HEADER_KEYS = frozenset({
    "name", "aclnn_name", "kernel_name", "version", "api",
    "api_type", "aclnn_api_type", "generate", "standard"})

# make_yaml 固定写死的三个键，作用是让 ATK 的默认展开退化成恒等。
DERIVED_KEYS = ("dtype_numbers", "extra_numbers", "shape_distributions")
MAX_LENGTH = 2 ** 31

# 生态标准的数值分布（experimental_standard.md）：均匀与正态各半，
# 值域 [-5, 5]，正态 μ ∈ [-5, 5]、σ ∈ [0.1, 2]。
# 这套参数是给**浮点**写的：连续取值下单张张量仍有近 numel 个不同值。
DEFAULT_RANGE = [-5, 5]
DEFAULT_STD = [0.1, 2]

# 整型套用浮点值域会退化。ATK 的正态数据先采样再按 dtype 截断转换
# （`atk/case_generator/generator/data_types/data_torch.py:296-303`），
# σ ≤ 2 时整张整型张量只剩个位数取值，无符号 dtype 撞上负 μ 直接整张清零。
# 真机实测（roll，2026-08-15）：uint32 分面唯一值中位数为 1，17 条用例是常量张量。
# 区间表在 _expressibility 里，decl 期的 check_value_ranges 用的是同一份。
# 取值个数的上限。判别力只需要「远多于一个」，再宽就会让算术类算子的
# 中间结果溢出，把数据设计问题伪装成实现缺陷。
INT_SPAN_CAP = 2 ** 16


class DeclarationError(Exception):
    pass


def _require(condition, message):
    if not condition:
        raise DeclarationError(message)


def _unique(values):
    """去重且保序稳定：先按类型名再按值排，避免 int 与 str 混排时崩。"""
    return sorted(set(values), key=lambda value: (type(value).__name__, value))


def value_distribution(dtypes, contract):
    """按 dtype 集合推导取值范围与正态参数。

    纯浮点 / 复数输入一字不动地守生态标准——实测填充率 0.96 以上，本来就够。

    含整型时按各整型 dtype 值域的**交集**放大：交集保证最窄的那个 dtype 不会
    整体饱和成常量，`INT_SPAN_CAP` 保证不会宽到让算术溢出。
    σ 随宽度走，否则 μ 撒得再开、单张张量还是挤在几个取值上。

    `parameters` 里显式声明 `range` 时一律优先：算子有定义域限制（log、除法）
    时那是唯一正确的值域，推导不该覆盖它。
    """
    declared = contract.get("range")
    if declared:
        return list(declared), list(contract.get("std", DEFAULT_STD))

    spans = [INT_DTYPE_RANGES[str(dtype).lower()] for dtype in dtypes
             if str(dtype).lower() in INT_DTYPE_RANGES]
    if not spans:
        return list(DEFAULT_RANGE), list(DEFAULT_STD)

    low = max(span[0] for span in spans)
    high = min(span[1] for span in spans)
    if high - low + 1 > INT_SPAN_CAP:
        if low >= 0:
            high = low + INT_SPAN_CAP - 1
        else:
            low, high = -(INT_SPAN_CAP // 2), INT_SPAN_CAP // 2 - 1
    width = high - low + 1
    return [low, high], [max(width / 16, DEFAULT_STD[0]), max(width / 4, DEFAULT_STD[1])]


def _tensor_input(name, contract, combos):
    shape_key = contract.get("shape_key", "shape")
    dtype_key = contract.get("dtype_key", "dtype")
    shapes = [combo[shape_key] for combo in combos if shape_key in combo]
    dtypes = [combo[dtype_key] for combo in combos if dtype_key in combo]
    _require(shapes, f"{name}: combos 里没有 {shape_key!r}，物化跑了吗")
    _require(dtypes, f"{name}: combos 里没有 {dtype_key!r}")

    dim_values = _unique(size for shape in shapes for size in shape)
    over = [(index, shape) for index, shape in enumerate(shapes)
            if any(size > 2 ** 20 for size in shape)]
    _require(not over,
             f"{name}: {len(over)} 条 combo 的单维超过 2^20，"
             f"例如 combos[{over[0][0]}] shape={over[0][1]}；"
             "把体量摊到多个维度，或把该轴组合列入 infeasible" if over else "")

    value_range, std = value_distribution(_unique(dtypes), contract)
    config = {
        "name": name,
        "type": TYPE_BY_SEMANTICS[("tensor", contract["runtime_container"])],
        "dtypes": {"values": _unique(dtypes)},
        "shapes": {
            "dim_numbers": {"values": _unique(len(shape) for shape in shapes)},
            "dim_values": {"values": dim_values},
            "max_length": MAX_LENGTH,
        },
        # 两个 random_type 缺一不可。ATK 在 `random_types` 只有一项时恒取正态
        # （`atk/configs/design_config.py:183`），`values` 里的均匀区间一次也
        # 不会生效——生态标准要求的「均匀与正态各半」就此静默失效。
        "ranges": {"valid": {
            "values": [value_range],
            "random_types": [
                {"name": "default"},
                {"name": "nd", "mean": list(value_range), "std": std},
            ],
        }},
    }
    if contract["runtime_container"] != "single":
        config["tuple_numbers"] = {"values": [1]}
    return config


def _values_for(name, contract, combos):
    key = contract.get("combo_key", name)
    present = [combo[key] for combo in combos if key in combo]
    _require(present, f"{name}: combos 里没有 {key!r}；"
                      "物化脚本要把每个 attr/scalar 参数的取值写进 combo")
    return present


def _attr_input(name, contract, combos, element_kind):
    container = contract["runtime_container"]
    dtype = contract.get("dtype")
    _require(dtype, f"{name}: 契约缺 dtype（attr/scalar 的 dtype 推不出来，必须声明）")

    config = {
        "name": name,
        "type": TYPE_BY_SEMANTICS[(element_kind, container)],
        "dtypes": {"values": [dtype]},
    }
    values = _values_for(name, contract, combos)

    if container == "single":
        # 取值由生成器插件逐条覆写，YAML 只需给一个合法占位。
        # nullable 时占位必须是 "default"：P20 认这个字符串，不是 JSON null。
        sample = DEFAULT_TOKEN if contract.get("nullable") else values[0]
        config["ranges"] = {"valid": {"values": [sample]}}
        return config

    lengths = _unique(len(value) for value in values)
    _require(0 not in lengths, f"{name}: combos 里{EMPTY_GROUP}")
    config["tuple_numbers"] = {"values": lengths}
    config["ranges"] = {"valid": {"values": [[values[0][0]]]}}
    return config


def _omitted_input(name):
    return {"name": name, "type": "attr", "dtypes": {"values": ["non_param"]},
            "ranges": {"valid": {"values": [DEFAULT_TOKEN]}}}


def baseline_parameter_names(symbol):
    """从基线函数名反射形参名，取不到返回 None。

    走的是 `align_signatures.baseline_signature` 那条路径，两处同源，但不能
    只信它的 `parameters`：那是 `torch.overrides` 的测试替身，替身是给
    `__torch_function__` 测试写的桩，不保证形参齐全——`torch.median`/
    `torch.sum` 的替身都少列了 `keepdim`。真机实测：真实 builtin 完全接受
    `keepdim`，替身缺的是替身自己，不是算子契约。

    权威源是 aten schema（`overloads`，来自 `torch._C._jit_get_schemas_for_operator`），
    这里取全部重载形参名的并集，而不是挑一个重载——挑一个重载要按参数个数
    匹配，某个分面的入参数量可能对不上任何一个重载（比如 median 的全张量
    分面只有 1 个入参），并集不需要这一步匹配就能覆盖所有分面。

    torch 装不上、接口名解析不了、替身与全部重载都取不到形参名，返回 None——
    「取不到」是一种结论，与「核对通过」必须分开，见 check_baseline_binding。
    """
    try:
        import torch  # noqa: F401
    except Exception:
        return None
    try:
        from align_signatures import aten_param_names, baseline_signature
        info = baseline_signature(symbol)
    # _runtime_guard 把导入失败翻成 SystemExit(4)，那不是 Exception 的子类。
    except (Exception, SystemExit):
        return None
    overloads = info.get("overloads") or []
    names = {item["name"] for item in (info.get("parameters") or [])}
    for schema in overloads:
        names.update(aten_param_names(schema))
    if not names:
        return None
    # 名单可不可信，取决于它是从哪张表来的：
    # aten 重载表就是形参契约本身，inspect 读的是真实 Python 签名，两者都全；
    # torch.overrides 的替身是测试桩，只保证位置大致对得上，可能漏形参。
    # 只有替身、又没有重载能补时，「名字不在名单里」判不了，不能当违规。
    trusted = info.get("source") != "torch.overrides" or bool(overloads)
    return sorted(names), trusted


def check_baseline_binding(names, input_names, trusted=True):
    """YAML 的 inputs 名必须落在基线形参名里。

    带 name 的输入在 dataset reload 之后一律进 kwargs，args 恒为空
    （L0 `binding.inputs`）。名字对不上基线形参名，基线调用直接
    TypeError，而这件事要到冻结那次跑测才炸——roll 那轮 42/81 条
    失败就是这么来的，代价是读 ATK 源码 11 次、改插件 3 次。

    aclnn 独有的参数同样会落在这里，那是对的：它们必须由基线侧适配器
    pop 掉，而 `align_signatures.py` 的 `baseline_adapter.required` 已经
    在报这件事，两处指向同一个动作。
    """
    if names is None:
        return ["取不到基线形参名，无法核对 YAML 输入名；"
                "先确认基线函数名可解析，或按 signature_alignment.json 的 "
                "semantic_review 从官方文档补齐形参名"]
    allowed = set(names)
    stray = [name for name in input_names if name not in allowed]
    if not stray:
        return []
    if trusted:
        return [f"{name!r} 不是基线的形参名（基线形参：{names}）；"
                "带 name 的输入全部进 kwargs，名字对不上基线调用直接 TypeError"
                for name in stray]
    # 真机事故（median，2026-08-16）：替身给 torch.median 只列了 input/dim，
    # 门禁拿这份不全的名单硬拒了合法的 keepdim，agent 只能在验收期中途改量具，
    # S2 白耗 22 分钟。名单可能不全时判不了，出口是补名单，不是改量具。
    return [f"判不了 {stray}：基线形参名只来自 torch.overrides 的测试替身"
            f"（当前名单 {names}），替身不保证形参齐全，也没有 aten 重载可以交叉验证。\n"
            "  → 查官方文档确认这些名字是不是合法形参，把依据（查的哪份文档、哪一节）写进 evidence/constraints.md，"
            "再用 --baseline-names 显式给出完整形参名重跑。\n"
            "  → 不要因为这条报错去改量具：名单不全是 torch 替身的性质，不是量具缺陷。"]


def _check_semantic_axes(must_cover, contracts):
    """填完具体取值之后再判一次：这次喂的是 combos 在各轴上的实际取值。

    decl 期 `make_must_cover.py` 已经拿 dims 判过一遍，判据同一份。
    物化脚本可能填进 dims 里没有的取值，所以这一遍不能省。
    """
    axes = must_cover.get("axes") or []
    combos = must_cover.get("combos") or []
    projection = {axis: [combo.get(axis) for combo in combos] for axis in axes}
    return check_axis_values(projection, contracts, must_cover.get("extract"))


def build_design(must_cover, baseline_names=None):
    header = must_cover.get("yaml")
    _require(isinstance(header, dict) and header,
             "声明缺 yaml 头部块（name / aclnn_name / api / generate / standard 等）")
    contracts = must_cover.get("parameters") or {}
    _require(contracts, "must_cover 缺 parameters；每个 YAML 输入都要有语义契约")
    combos = must_cover.get("combos") or []
    _require(combos, "must_cover 缺 combos")

    unknown = sorted(set(header) - YAML_HEADER_KEYS)
    _require(not unknown,
             f"yaml 块有未知头部键：{', '.join(unknown)}；"
             "拼错的接线键会静默走 ATK 默认路径。若确属合法字段，"
             "登记进 knowledge_gaps 并补进 artifact-contracts.json")
    # 内置真值这条路上 `name` 只是 CPU 节点的标签：真正执行的是 `api_type`
    # 指到的那个插件。不写 api_type，ATK 走内置 function 执行器去 eval(name)，
    # 而 YAML 的输入名是 aclnn 的形参名，喂给任何 torch 函数都是
    # unexpected keyword argument——那要到第一轮跑测才炸。
    _require(must_cover.get("baseline_kind") != "cann_builtin"
             or (header.get("api_type") or "").strip(),
             "baseline_kind 是 cann_builtin，yaml 块必须写 api_type："
             "CPU 节点没有可调用的 torch 基线，只能由 function_<op>.py 供"
             "出参的形状与 dtype，注册名要和 api_type 一字不差"
             "（references/builtin-baseline-design.md#没有-torch-基线s2-怎么写）")

    design = dict(header)
    # 生成器插件按 must_cover 逐条产出用例，这三个字段只是让 ATK 的
    # 默认展开退化成恒等，不承载设计意图。
    design.update(dict(zip(DERIVED_KEYS, (1, 0, [[0, 1.0]]))))

    # 每个参数契约互相独立，一个写错不妨碍检查其余的。挨个抛异常会让
    # 「三个契约都缺 dtype」变成跑三轮，所以这里攒齐一次报。
    channels = {channel: [] for channel in CHANNELS}
    contract_problems = check_contracts(contracts)
    problems = [message for _, message in contract_problems]
    broken = {key for key, _ in contract_problems}
    for name, contract in contracts.items():
        # 契约键可写成 "inputs.x" 限定通道，与 _atk_capabilities._contract_for 同构
        channel, _, bare = name.rpartition(".")
        channel = channel or contract.get("channel") or "inputs"
        if channel not in CHANNELS:
            problems.append(f"{name}: 未知通道 {channel!r}")
            continue
        if bare in broken:
            # 契约本身不成立，再往下推导只会刷出一串派生错误
            continue

        try:
            if contract.get("omitted"):
                config = _omitted_input(bare)
            elif contract.get("element_kind") == "tensor":
                config = _tensor_input(bare, contract, combos)
            else:
                config = _attr_input(bare, contract, combos,
                                     contract["element_kind"])
        except DeclarationError as exc:
            problems.append(str(exc))
            continue
        if contract.get("aclnn_name"):
            config["aclnn_name"] = contract["aclnn_name"]
        channels[channel].append(config)

    problems.extend(_check_semantic_axes(must_cover, contracts))

    names = [config["name"] for config in channels["inputs"]]
    if len(names) != len(set(names)):
        problems.append(f"inputs 有重名：{names}")
    if len(channels["tensor_input"]) > 1:
        problems.append("tensor_input 只能有一个")
    # `baseline_kind` 是 cann_builtin 时没有 torch 基线：真值来自先跑一轮内置实现
    # 存盘、再由 `node -b cpu --task accuracy_load` 读回来（builtin-baseline.md）。
    # YAML 的输入名照 aclnn 自己的形参名写，拿 torch 形参名去量它是量错了东西——
    # bernoulli 的 prob / seed / offset 在任何 torch 重载里都不存在，这道门禁会
    # 把唯一正确的写法判成违规。这一侧的核对由 check_signature_contract.py 做，
    # 判据更强（集合、顺序、多余项三判，不是子集判定）。
    if must_cover.get("baseline_kind") != "cann_builtin":
        if baseline_names:
            resolved, trusted = baseline_names, True
        else:
            resolved, trusted = baseline_parameter_names(header.get("name")) or (None, True)
        problems.extend(check_baseline_binding(resolved, names, trusted))
    if problems:
        if len(problems) == 1:
            raise DeclarationError(problems[0])
        listed = "\n".join(f"  {index}. {text}"
                            for index, text in enumerate(problems, 1))
        raise DeclarationError(f"参数契约有 {len(problems)} 个问题：\n{listed}")

    for channel in CHANNELS:
        if not channels[channel]:
            continue
        design[channel] = (channels[channel][0] if channel == "tensor_input"
                           else channels[channel])
    return design


def main():
    parser = argparse.ArgumentParser(description="从物化后的 must_cover 推导设计 YAML")
    parser.add_argument("-m", "--must-cover", required=True)
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--baseline-names", type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
                        help="基线完整形参名，逗号分隔；只在 torch 替身给不全、"
                             "且已从官方文档确认并把依据写进证据时使用")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("需要 pyyaml 才能写设计 YAML") from exc

    try:
        design = build_design(load_json(args.must_cover), args.baseline_names)
    except DeclarationError as exc:
        print(f"推导失败：{exc}", file=sys.stderr)
        return 2

    Path(args.output).write_text(
        yaml.safe_dump(design, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8")
    inputs = sum(len(design.get(channel, []) or []) for channel in
                 ("inputs", "method_inputs"))
    print(f"推导 {inputs} 个输入 → {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
