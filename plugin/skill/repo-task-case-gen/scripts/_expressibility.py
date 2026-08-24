"""参数契约与轴取值的可表达性判定，decl 期与物化期共用一份。

同一个问题要在两个时点回答：`make_must_cover.py` 拿 decl 的 dims 问
「这个设计 ATK 表达得出来吗」，`make_yaml.py` 拿物化后的 combos 再问一次
「填进来的取值仍然表达得出来吗」。

判定挂在第二次时，agent 要写完 decl、跑完 must_cover、写完物化脚本、
跑完物化，到第四步才知道第一步就定死的取值不可表达。前移一次，
但两处必须是同一份实现——写两份的那一刻就开始漂移。

本模块不是 CLI。
"""

# (element_kind, runtime_container) → YAML type。反查
# references/atk-parameter-capabilities.json 的 flat_types / group_types，
# 两边同源，不重新起一套写法。
TYPE_BY_SEMANTICS = {
    ("tensor", "single"): "tensor",
    ("tensor", "list"): "tensors",
    ("tensor", "tuple"): "tensor_tuple",
    ("scalar", "single"): "scalar",
    ("scalar", "list"): "scalars",
    ("scalar", "tuple"): "scalar_tuple",
    ("attr", "single"): "attr",
    ("attr", "list"): "attrs",
    ("attr", "tuple"): "attr_tuple",
}

GROUP_CONTAINERS = frozenset({"list", "tuple"})
DEFAULT_TOKEN = "default"

# 整型 dtype 的可表示区间。放这里而不是 make_yaml 里，是因为同一份事实
# 要在两个时点用：decl 期判「声明的 range 这些 dtype 装得下吗」，
# 推导期算「没声明 range 时按 dtype 交集取多宽」。
INT_DTYPE_RANGES = {
    "int8": (-2 ** 7, 2 ** 7 - 1), "uint8": (0, 2 ** 8 - 1),
    "int16": (-2 ** 15, 2 ** 15 - 1), "uint16": (0, 2 ** 16 - 1),
    "int32": (-2 ** 31, 2 ** 31 - 1), "uint32": (0, 2 ** 32 - 1),
    "int64": (-2 ** 63, 2 ** 63 - 1), "uint64": (0, 2 ** 64 - 1),
}

# (element_kind, runtime_container) → 能力域里该查哪张 dtype 白名单。
# 数组与标量不是同一份：aclIntArray 只收 int，标量 attr 才收 int64_t。
_DTYPE_DOMAIN = {
    ("attr", "single"): "attr_single_dtypes",
    ("attr", "list"): "attr_array_dtypes",
    ("attr", "tuple"): "attr_array_dtypes",
    ("scalar", "single"): "scalar_dtypes",
    ("scalar", "list"): "scalar_dtypes",
    ("scalar", "tuple"): "scalar_dtypes",
}

_CAPABILITIES = None


def _dtype_domain(element, container, backend="pyaclnn"):
    """能力域里该 dtype 位置的白名单，取不到时返回 None（不拦）。"""
    global _CAPABILITIES
    key = _DTYPE_DOMAIN.get((element, container))
    if not key:
        return None
    if _CAPABILITIES is None:
        try:
            from _atk_capabilities import load_capabilities
            _CAPABILITIES = load_capabilities()
        except Exception:  # 能力域文件缺失时不把整条链路拦死
            _CAPABILITIES = {}
    domain = ((_CAPABILITIES.get(backend) or {}).get(key))
    return set(domain) if domain else None

# 空组那句话只写在这一处。两个脚本各写一遍时，改了一处另一处就成了旧闻。
EMPTY_GROUP = ("出现空序列；ATK 26.8.8 复合组最少一个成员，空组表达不出来；"
               "缺省参数用显式 default token 表示")


def semantic_kind(value):
    """一个取值在运行期会 lowering 成什么形态。"""
    if value is None or value == DEFAULT_TOKEN:
        return "none"
    if isinstance(value, (list, tuple)):
        return "empty_sequence" if len(value) == 0 else "sequence"
    return "scalar"


def check_contracts(parameters):
    """契约本身合法吗——不需要任何取值就能判，所以 decl 期就该判完。

    返回 `[(契约键, 问题), ...]`。键让调用方知道哪几个契约不能再往下推导，
    避免同一个契约在后面的推导里再报一串派生错误。
    """
    problems = []
    for name, contract in (parameters or {}).items():
        bare = name.rpartition(".")[2]
        if (contract or {}).get("omitted"):
            continue
        element = (contract or {}).get("element_kind")
        container = (contract or {}).get("runtime_container")
        if (element, container) not in TYPE_BY_SEMANTICS:
            problems.append(
                (bare, f"{bare}: 契约的 {element!r}/{container!r} 不是合法组合"))
            continue
        if element in ("attr", "scalar") and not contract.get("dtype"):
            problems.append(
                (bare, f"{bare}: 契约缺 dtype"
                       "（attr/scalar 的 dtype 推不出来，必须声明）"))
            continue
        # dtype 在不在白名单里，decl 期就能判。判在生成后（C6）意味着
        # 要先跑完 atk case 才知道第一步写的 dtype 表达不出来：真机上
        # shifts/dims 写了 int64_t，146 处 unsupported_attr_array_dtype，
        # 全链路重生成一轮。
        allowed = _dtype_domain(element, container)
        dtype = contract.get("dtype")
        if allowed and dtype and dtype not in allowed:
            problems.append(
                (bare, f"{bare}: dtype {dtype!r} 不在 {element}/{container} 的"
                       f"能力域白名单里，可选 {sorted(allowed)}"))
    return problems


def int_span(dtype):
    """一个 dtype 名的整型可表示区间，取不到返回 None。

    契约里的 dtype 用的是 C 名（`int64_t`），dtype 轴用的是 ATK 令牌
    （`int64`），同一件事两种写法，在这里归一。
    """
    key = str(dtype).lower()
    if key.endswith("_t"):
        key = key[:-2]
    if key == "int":  # aclIntArray 的成员就是 int64_t
        key = "int64"
    return INT_DTYPE_RANGES.get(key)


def _own_dtype(contract):
    """这个参数的 dtype 是不是写在契约里、与 dtype 轴无关。

    张量的 dtype 来自 combo，所以量它要用 dtype 轴；attr / scalar 的 dtype
    推不出来、必须在契约里声明（见 check_contracts），量它只能用它自己那个。
    """
    if (contract or {}).get("element_kind") not in ("attr", "scalar"):
        return None
    return (contract.get("dtype") or "").strip() or None


def check_value_ranges(dtypes, parameters):
    """显式声明的 range，每个 dtype 都装得下吗。

    `range` 一旦写了就无条件压过按 dtype 的推导（算子有定义域限制时那是对的），
    但没有任何东西核对它与 dtype 相不相容。真机事故（roll，2026-08-16）：
    input 写 `range: [-5, 5]`，dtype 轴里有 uint8/uint32——ATK 正态采样负均值后
    按 dtype 截断，整张清零，5 条用例退化成常量张量，测不出任何错。
    这条要到 S2 末尾冻结时才被 freeze_inputs 抓到，那时已经跑过 atk case。

    判据是可表示性，不是宽窄：区间越界的那一半会被截断，越界就拦。

    **拿哪个 dtype 去量，按参数分**。dtype 轴上的是张量的 dtype，attr 与
    scalar 的 dtype 写在自己的契约里。混着用会误拦：真机事故
    （bernoulli，2026-08-17）——种子是 `int64_t` 的 attr，按 case-design.md
    的「种子类参数」必须钉成常量（`range: [S, S]`），而张量 dtype 轴里有
    uint8，于是这条钉死的写法被判成「超出 uint8 的可表示区间」。钉死是规范
    要求的唯一写法，报错给的两条出路（去掉 range / 换个每个 dtype 都装得下
    的区间）都做不到，S2 在这里死锁。
    """
    problems = []
    for name, contract in (parameters or {}).items():
        declared = (contract or {}).get("range")
        if not declared or len(declared) != 2:
            continue
        low, high = declared
        own = _own_dtype(contract)
        for dtype in ([own] if own else (dtypes or ())):
            span = int_span(dtype)
            if not span:
                continue
            if low < span[0] or high > span[1]:
                problems.append(
                    f"{name!r} 声明 range {list(declared)}，超出 dtype {dtype} "
                    f"的可表示区间 {list(span)}；越界部分会被按 dtype 截断，"
                    "无符号 dtype 撞上负值会整张清零。\n"
                    "     去掉显式 range 交给 make_yaml 按 dtype 交集推导，"
                    "或按算子定义域给一个每个 dtype 都装得下的区间")
                break
    return problems


def check_axis_values(values_by_axis, parameters, extract):
    """轴取值 ATK 表达得出来吗。

    decl 期喂 `dims`（轴的取值全集），填完具体取值后喂 combos 在该轴上的实际取值。
    两种取值来源，同一套判据。

    只看 extract 规则声明 `from: attr` 的轴：别的轴（dtype、shape）的
    可表达性由数据生成那一侧管，不在这里重复判。
    """
    problems = []
    for axis, values in (values_by_axis or {}).items():
        rule = (extract or {}).get(axis) or {}
        if rule.get("from") != "attr":
            continue
        name = rule.get("name")
        if name not in (parameters or {}):
            problems.append(
                f"语义轴 {axis!r} 的 extract 指向 {name!r}，"
                "但 parameters 里没有这个契约")
            continue
        kinds = {semantic_kind(value) for value in values}
        if "empty_sequence" in kinds:
            problems.append(f"{name!r} 的取值里{EMPTY_GROUP}")
        if "sequence" in kinds and ("none" in kinds or "scalar" in kinds):
            problems.append(
                f"{name!r} 在同一设计里混合 {sorted(kinds)}；"
                "单个输入不能既是平面 attr 又是复合 attr，请拆分接口分面")
        if "sequence" in kinds and \
                rule.get("runtime_container") not in GROUP_CONTAINERS:
            problems.append(
                f"{name!r} 是序列语义，extract 必须写 "
                "runtime_container=list 或 tuple")
    return problems
