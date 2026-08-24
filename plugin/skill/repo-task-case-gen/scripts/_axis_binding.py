"""dims 声明的取值从哪来：轴取值词表，以及 dtype 轴要对上公开声明。

覆盖率的分母是 `dims`，而 `dims` 一直是 agent 手写的——门禁拿这份声明当分母
去核对 combos，等于自己出题自己答，永远 100%。真机上因此发生过：同一个算子
七次验收，`rank` 写过 [1..8]、[1,2,3,5]、[1,2,3]，也整根没写过；`shape_form`
写过三套互不相同的命名；用例数从 76 条到 500 多条不等，而每次的覆盖率都是满分。

生成器没有随机性（同一份声明连跑五次输出字节一致），所以要稳住用例数，
只能稳住声明。这里管两件事：

- 语义轴的取值是**策略层约定**，换个名字不带来任何信息，钉死词表；
- dtype 轴的取值是**接口事实**：生成侧对任务书 §2.4 张量 dtype 列，
  验收侧或旧流程对待验收算子工程的公开声明。

没过 repo-task-doc-write 门禁的存量任务书常常不列具体 dtype。生成侧遇到这种情况
停在待确认，旧流程才去工程的 README 或头文件里读公开声明。
"""

import re

from _dtype_vocab import DTYPE_SOURCE_ALIASES

# 该轴对本算子不适用时的唯一写法。写 "none"/"all"/"flatten" 都能表达同一个
# 意思，但三份声明就长成三个样子，`n/a` 是为了让「不适用」只有一种形状。
NOT_APPLICABLE = "n/a"

# rank 的 1~8 不是这里定的，是生态标准的「维度范围 1~8 维」
# （experimental_standard.md#用例生成）。其余轴是本 skill 的策略层约定。
PINNED_AXIS_VALUES = {
    "rank": [1, 2, 3, 4, 5, 6, 7, 8],
    "size_class": ["small", "medium", "large"],
    "shape_form": ["normal", "empty", "single_element", "unaligned_tail"],
    "op_axis_pos": ["first", "middle", "last"],
    "reduce_axis_pos": ["first", "middle", "last"],
}

def check_axis_vocabulary(dims):
    """钉死的轴要么取满词表，要么整根退化成 `n/a`，没有第三种写法。

    取满和退化都是可复现的：同一个算子、同一份工程，下一轮还是这批组合。
    取子集不行——「只测 rank 1~4」和「这根轴不适用」是两回事，前者是把分母
    悄悄缩小，后者有断言、看得见。真的测不了某个取值，写进 `infeasible`
    并给出 `why`，那条会从分母里扣掉且留痕。

    顺序也算：`itertools.product` 按声明顺序展开，换序就换 combos。
    """
    problems = []
    for axis, expected in PINNED_AXIS_VALUES.items():
        values = (dims or {}).get(axis)
        if values is None:
            continue
        if list(values) == expected or list(values) == [NOT_APPLICABLE]:
            continue
        problems.append(
            f"dims.{axis} = {list(values)} 不是合法写法。\n"
            f"    取满：{expected}\n"
            f"    或整根不适用：[\"{NOT_APPLICABLE}\"]\n"
            "    个别取值测不了写进 infeasible 并给 why，不要缩短这根轴——"
            "缩短的是覆盖率分母，缩完照样 100%。")
    return problems


def _axis_index(rank, position):
    return {"first": 0, "middle": rank // 2, "last": rank - 1}[position]


def structural_infeasible(dims):
    """钉死的轴之间本来就分不开的取值组合。

    这几根轴的取值是钉死的，它们之间哪些组合在结构上根本产生不出两个不同的
    用例，也就是钉死的：rank=1 时首轴就是末轴，单元素与空张量形态下没有规模
    之分。物化脚本再怎么写都分不开，`check_coverage` 的重复 combo 门禁必然拦下。

    以前这件事要靠 `check_coverage` 一轮一轮地退：真机上一个算子在这里返工了
    三轮，每轮都要重跑物化。这些组合与算子无关，声明期就能全部说出来。

    返回的每一条都能直接粘进声明的 `infeasible`。
    """
    dims = dims or {}
    ranks = [r for r in (dims.get("rank") or []) if isinstance(r, int)]
    entries = []

    for axis in ("op_axis_pos", "reduce_axis_pos"):
        positions = [p for p in (dims.get(axis) or []) if p != NOT_APPLICABLE]
        # 按 first → last → middle 的顺序留取值：撞上的时候丢掉 middle，
        # 而不是丢掉 last。rank2 的张量有首轴有末轴、没有中间轴，
        # 报「rank2 没有 middle」比报「rank2 没有 last」好读得多。
        ordered = [p for p in ("first", "last", "middle") if p in positions]
        for rank in ranks:
            seen = {}
            for position in ordered:
                if position not in ("first", "middle", "last"):
                    continue
                index = _axis_index(rank, position)
                if index in seen:
                    entries.append({
                        "rank": rank, axis: position,
                        "why": f"rank{rank} 的 {position} 与 {seen[index]} "
                               f"是同一根轴（都是第 {index} 轴），生成不出两个用例",
                    })
                else:
                    seen[index] = position

    forms = dims.get("shape_form") or []
    bands = [b for b in (dims.get("size_class") or []) if b != NOT_APPLICABLE]
    for form in ("single_element", "empty"):
        if form not in forms:
            continue
        for band in bands[1:]:
            entries.append({
                "shape_form": form, "size_class": band,
                "why": f"{form} 形态的形状与规模无关，"
                       f"{band} 与 {bands[0]} 会物化成同一个形状",
            })
    return entries


def _source_text(source_path):
    return source_path.read_text(encoding="utf-8", errors="replace").upper()


def _found_in(text, dtype):
    """按词边界找一个 dtype 的任一别名。

    子串会误命中：`UINT8` 里含 `INT8`、`FLOAT16` 里含 `FLOAT`。
    """
    aliases = DTYPE_SOURCE_ALIASES.get(str(dtype), (str(dtype),))
    return aliases, any(
        re.search(rf"\b{re.escape(alias.upper())}\b", text)
        for alias in aliases)


def dtype_inventory_text(text):
    """一段数据类型声明文本里出现了哪些 dtype。"""
    text = str(text).upper()
    return [dtype for dtype in DTYPE_SOURCE_ALIASES if _found_in(text, dtype)[1]]


def dtype_source_inventory(source_path):
    """工程声明的数据类型表里出现了哪些 dtype。

    这份清单就是 dtype 轴照着写的那张表：轴取值照它写，覆盖门禁也照它判。
    读不出文件时返回空表——调用方另有一条读文件失败的报错路径，
    这里不重复报。
    """
    try:
        text = _source_text(source_path)
    except OSError:
        return []
    return dtype_inventory_text(text)


def check_dtype_text(dtype_values, text, label, excludes=()):
    """dtype 轴与一段公开的数据类型声明文本**双向**对齐。

    `label` 是这段文本的可读来源名，所有问题都用它定位来源。

    两个方向都要查，只查一头挡不住漂移：
    - 声明了却找不到 → 凭空写的 dtype；
    - 找得到却没声明 → 漏测。8 种写成 5 种时覆盖率照样 100%，
      漏掉的三种根本没进过验收。

    文件里出现的 dtype 名不一定都是输入 dtype——median 的 README 里 `bool` 是
    `keepDim` 属性的类型。`excludes` 是这类误报的出口，每条要写 `why`：
    没有出口，量具就会拿一份不全的名单硬拒合法声明，把 agent 逼到跑测中途
    去改量具。

    按词边界匹配：`UINT8` 里含 `INT8`、`FLOAT16` 里含 `FLOAT`，
    子串命中会把没声明的 dtype 放行。
    """
    text = str(text).upper()

    def found(dtype):
        return _found_in(text, dtype)

    problems = []
    declared = {str(value) for value in dtype_values or ()}
    for dtype in dtype_values or ():
        aliases, hit = found(dtype)
        if hit:
            continue
        problems.append(
            f"dtype 取值 {dtype} 在 {label} 里找不到（查的是 "
            f"{'、'.join(aliases)}）。\n"
            "    dtype 轴只能照工程声明的数据类型表写，不能凭空加。")

    waived = set()
    for index, entry in enumerate(excludes or ()):
        dtype = str((entry or {}).get("dtype", ""))
        if not dtype:
            problems.append(f"dtype_source_excludes[{index}] 缺 dtype")
            continue
        if not str((entry or {}).get("why", "")).strip():
            problems.append(
                f"dtype_source_excludes 里的 {dtype} 缺 why。\n"
                "    豁免是从验收范围里拿掉一个 dtype，理由要留在声明里可复核。")
            continue
        if not found(dtype)[1]:
            problems.append(
                f"dtype_source_excludes 里的 {dtype} 在 {label} 里本来就"
                "没出现，这条豁免没有对象。\n"
                "    豁免只用来解释这类误报（如属性的类型名被当成输入 "
                "dtype），不是给声明开的后门。")
            continue
        waived.add(dtype)

    omitted = [dtype for dtype in DTYPE_SOURCE_ALIASES
               if dtype not in declared and dtype not in waived
               and found(dtype)[1]]
    if omitted:
        problems.append(
            f"{label} 里出现了 {'、'.join(omitted)}，但 dtype 轴没声明。\n"
            "    工程声明支持的类型都要测，漏掉的那几种覆盖率看不出来。\n"
            "    确实不是输入 dtype（比如属性或输出的类型名）就写进声明的 "
            "dtype_source_excludes，每条附 why。")
    return problems


def check_dtype_source(dtype_values, source_path, excludes=()):
    """dtype 轴与待验收算子工程的公开声明**双向**对齐。

    没过 repo-task-doc-write 门禁的存量任务书常只写「支持所有走入 aicore 的
    数据类型」，列不出具体名字。生成侧遇到这种情况停在待确认，旧流程才从
    工程声明（README 的数据类型表或头文件注释）读取。
    """
    try:
        text = _source_text(source_path)
    except OSError as exc:
        return [f"读不出数据类型表 {source_path}：{exc}"]
    return check_dtype_text(dtype_values, text, source_path, excludes)
