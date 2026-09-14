#!/usr/bin/env python3
"""从一条 ATK 用例里把张量掏出来——**生成侧唯一一处认识输入形态的代码**。

`gen_cases.py`、`check_coverage.py`、`freeze_golden.py` 都从这里取。
碰到没见过的输入形态时**只改这个文件的 `tensor_items`**，加一个分支，
别回到三个脚本各写一份的老路。

已知形态：

| 形态 | `case["inputs"][i]` 长什么样 | 谁会用 |
| --- | --- | --- |
| 单张量 | `{"type": "tensor", ...}` | 绝大多数算子 |
| 张量列表 | `[{"type": "tensors", ...}, ...]` | 参数是 `aclTensorList*` 的算子 |

**不认识就抛 `UnknownInputShape`，绝不返回空值让调用方接着算。**
这条是拿真机事故换来的：早先这里认不出就返回 `{}` 接着算，一份张量列表算子的
217 条用例被整体判成 rank 0 / small / dtype 未知，报告完整、自信、全错，
退出码还是 0。卡死会喊，静默错报告不喊——要防的是后者。
"""

# ATK 里张量型输入的 type 取值，见 atk/case_generator/generator/parameter_types/
# 的 PARAMETER_REGISTRY 注册名：tensor（单张量）、tensors / tensor_tuple（列表）。
TENSOR_TYPES = ("tensor", "tensors", "tensor_tuple")
# 非张量参数在用例里也可能是 dict 列表：`aclIntArray*` 落成 [{"type": "attrs", ...}]。
# 少了这一组，第一个输入是 aclIntArray 的算子会被判成「没见过的形态」。
ATTR_TYPES = ("attr", "attrs", "attr_tuple", "scalar", "scalars")

DTYPE_BYTES = {
    "fp64": 8, "int64": 8, "uint64": 8, "complex64": 8, "complex128": 16,
    "fp32": 4, "int32": 4, "uint32": 4, "tf32": 4, "hf32": 4,
    "fp16": 2, "bf16": 2, "int16": 2, "uint16": 2,
    "int8": 1, "uint8": 1, "bool": 1, "fp8e4m3": 1, "fp8e5m2": 1,
}

# 规模按**字节数**分档，与 references/case-strategy.md「规模档」同一套阈值。
# 不按元素数：262144 个元素在 fp32 是 1 MB、在 int8 只有 256 KB，
# 而决定 UB 装不装得下、要不要多核切分的是字节数。
SMALL_BYTES = 32 * 1024
MEDIUM_BYTES = 2 * 1024 * 1024


class UnknownInputShape(Exception):
    """用例的输入结构不在已知形态里。调用方应当据此退非 0，不要吞掉。"""

    def __init__(self, case, detail):
        self.case_id = case.get("id") if isinstance(case, dict) else None
        super().__init__(detail)

    def advice(self):
        return (
            f"用例 id={self.case_id} 的输入结构不认识：{self}\n"
            f"已知形态：dict 且 type in {TENSOR_TYPES}（单张量）、"
            f"list[dict] 且元素 type in {TENSOR_TYPES}（张量列表）。\n"
            f"新形态请在 scripts/case_shape.py 的 tensor_items() 加一个分支，"
            f"改完按 SKILL.md「脚本不认识输入结构时」把改动同步回仓。"
        )


def classify(item):
    """一个 inputs 条目属于哪一类：tensor / tensors / attr / unknown。"""
    if isinstance(item, dict):
        return "tensor" if item.get("type") in TENSOR_TYPES else "attr"
    if isinstance(item, list):
        if not item:
            return "attr"                       # 空数组参数
        if all(isinstance(x, dict) for x in item):
            if all(x.get("type") in TENSOR_TYPES for x in item):
                return "tensors"
            if all(x.get("type") in ATTR_TYPES for x in item):
                return "attr"                   # aclIntArray* 这类，落成 attrs 的 dict 列表
            return "unknown"                    # dict 列表但两组都不是，没见过
        if any(isinstance(x, dict) for x in item):
            return "unknown"                    # 混了 dict 和别的，没见过
        return "attr"                           # 数值数组参数，如 Roll 的 dims
    return "attr"                               # 标量字面量


def tensor_items(case):
    """第一个张量型输入的条目：单张量返回 `[dict]`，张量列表返回 `list[dict]`。

    找不到任何张量输入就抛——本仓范围内的 aclnn 算子至少有一个张量输入，
    一条都找不到只可能是这里没看懂结构。
    """
    inputs = case.get("inputs", []) if isinstance(case, dict) else []
    for item in inputs:
        kind = classify(item)
        if kind == "tensor":
            return [item]
        if kind == "tensors":
            return list(item)
        if kind == "unknown":
            raise UnknownInputShape(case, f"inputs 里出现 {type(item).__name__}，内容 {str(item)[:120]}")
    raise UnknownInputShape(case, f"inputs 里没有任何张量输入，实际是 {[classify(i) for i in inputs]}")


def all_tensor_items(case):
    """**所有**张量型输入的条目，不止第一个。

    `tensor_items` 只看第一个输入，够用来分档与取 dtype；改写 dtype 时不够——
    多张量输入的算子（`aclnnAdd` 这类）几个输入的 dtype 是绑定的，只改第一个
    会造出一条 dtype 不自洽、aclnn 直接报错的用例。
    """
    items = []
    for item in case.get("inputs", []) if isinstance(case, dict) else []:
        kind = classify(item)
        if kind == "tensor":
            items.append(item)
        elif kind == "tensors":
            items.extend(item)
        elif kind == "unknown":
            raise UnknownInputShape(case, f"inputs 里出现 {type(item).__name__}，"
                                          f"内容 {str(item)[:120]}")
    return items


def is_attr_only(case):
    """这条用例一个张量输入都没有，参数全是 attr。

    aclnn 剖面下不存在这种用例（至少有一个张量入参）。npu 剖面下是常态：
    算子入口收的是描述符与标量，张量由执行器插件按其中的 seed 现造
    （ops-sparse 的 DenseToSparse 就是九个 int）。

    分档与取 dtype 都建立在张量上，所以这两件事对这类用例要换判据，
    见 `attr_band`。
    """
    inputs = case.get("inputs", []) if isinstance(case, dict) else []
    return bool(inputs) and all(classify(i) == "attr" for i in inputs)


def attr_value(case, name):
    """按参数名取一个 attr 的值。取不到返回 None。

    纯 attr 用例的规模由某个具体参数决定（稀疏算子是 nnz），
    哪个参数是规模轴由生成侧指定，本函数只负责取值。
    """
    for item in case.get("inputs", []) if isinstance(case, dict) else []:
        if not isinstance(item, dict) or item.get("name") != name:
            continue
        values = item.get("range_values")
        if isinstance(values, list) and values:
            return values[0]
        return item.get("value")
    return None


def attr_band(case, name, cuts):
    """纯 attr 用例的规模档：按 `name` 这个参数的值落进 `cuts` 划出的区间。

    `cuts` 是升序的两个门槛 `(小, 中)`，由生成侧从**本批用例的实际取值**
    算出来（四分位），不写死字节数——张量算子的字节门槛在这里没有意义。
    取不到值的落 `unknown`，**不落 small**：分不出档和「档位是小」是两回事。
    """
    value = attr_value(case, name)
    if not isinstance(value, (int, float)):
        return "unknown"
    if value < cuts[0]:
        return "small"
    if value < cuts[1]:
        return "medium"
    return "large"


def first_tensor(case):
    return tensor_items(case)[0]


def dtype_of(case):
    """第一个张量输入的 dtype。纯 attr 用例返回 `?`——它没有张量，也就没有 dtype。

    **返回 `?` 而不是抛**：抛的话调用方要在十几处各写一遍 try，而这里
    「取不到」是确定的事实，不是没见过的形态。分组轴另有出处，见 verdict.py。
    """
    if is_attr_only(case):
        return "?"
    return first_tensor(case).get("dtype") or "?"


def rank_of(case):
    if is_attr_only(case):
        return 0
    return len(first_tensor(case).get("shape") or [])


def numel_of(items):
    total = 0
    for tensor in items:
        n = 1
        for dim in tensor.get("shape") or []:
            n *= dim
        total += n
    return total


def size_band(case):
    """规模档。张量列表按整个列表的字节数求和——它们一起进 UB，一起被切。

    单元素单独一档：它是退化边界，和「小张量」不是一回事。
    """
    if is_attr_only(case):
        # 规模轴由生成侧用 `attr_band` 单独指定，这里不猜。
        return "unknown"
    items = tensor_items(case)
    numel = numel_of(items)
    if numel <= 1:
        return "scalar"
    total = sum(
        numel_of([tensor]) * DTYPE_BYTES.get(tensor.get("dtype"), 4)
        for tensor in items
    )
    if total < SMALL_BYTES:
        return "small"
    if total < MEDIUM_BYTES:
        return "medium"
    return "large"


def guard(cases, stream):
    """对整份 cases 跑一遍形态识别。认得出返回 True，认不出打指路并返回 False。

    **纯 attr 用例是合法形态，不是不认识的形态。** npu 剖面下张量由执行器插件
    按参数里的 seed 现造，用例里一个张量都没有（见 `is_attr_only`）。
    这里放行，取 dtype 与分档的两处各自按 `?` 与 `unknown` 走。
    """
    try:
        for case in cases:
            if is_attr_only(case):
                continue
            tensor_items(case)
    except UnknownInputShape as err:
        print(f"\n{err.advice()}", file=stream)
        return False
    return True
