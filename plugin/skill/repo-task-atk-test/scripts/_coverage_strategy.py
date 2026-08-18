"""Build and audit a deterministic, declared coverage denominator."""

import itertools
import random
import json


class CoveragePolicyError(ValueError):
    """The declared coverage policy is incomplete or inconsistent."""


def _key(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _contains(values, wanted):
    wanted_key = _key(wanted)
    return any(_key(value) == wanted_key for value in values)


def _validate(dims, policy, infeasible):
    """校验声明，把能一次说清的问题一次说完。

    以前是首错即抛：声明里有五个问题就要跑五轮才能看全，实测一轮验收在这里
    改了 17 次声明。真正必须提前中断的只有结构性问题——dims 不是可用的轴表时，
    后面所有「取值是否在分母里」的检查都无从谈起。其余问题一律攒齐再报。
    """
    failures = []
    # 结构可用的轴才拿来做取值归属检查，避免在坏轴上再报一串派生错误。
    usable = {}
    if not isinstance(dims, dict):
        failures.append("dims 必须是对象")
    else:
        for axis, values in dims.items():
            if not isinstance(axis, str) or not axis:
                failures.append("dims 轴名必须是非空字符串")
                continue
            if not isinstance(values, list) or not values:
                failures.append(f"dims.{axis} 必须是非空数组")
                continue
            if len({_key(value) for value in values}) != len(values):
                failures.append(f"dims.{axis} 含重复取值")
                continue
            usable[axis] = values

    if not isinstance(policy, dict):
        failures.append("缺少 coverage_policy")
    if failures and (not usable or not isinstance(policy, dict)):
        # 轴表或策略块本身塌了，后续检查全部依赖它们，继续查只会刷屏。
        # dims 为空是合法的退化形态（只做能力检查），不在此列。
        _raise(failures)

    if policy.get("strategy") != "anchored_interactions":
        failures.append("coverage_policy.strategy 只支持 anchored_interactions")

    baseline = policy.get("baseline")
    if not isinstance(baseline, dict) or set(baseline) != set(dims):
        failures.append("coverage_policy.baseline 必须包含全部且仅包含 dims 轴")
        baseline = baseline if isinstance(baseline, dict) else {}
    for axis, value in baseline.items():
        if axis in usable and not _contains(usable[axis], value):
            failures.append(f"baseline.{axis} 不在 dims 分母中")

    main_effects = policy.get("main_effects", list(dims))
    if not isinstance(main_effects, list) or any(
            not isinstance(axis, str) for axis in main_effects):
        failures.append("coverage_policy.main_effects 必须是轴名数组")
        main_effects = list(dims)
    else:
        if len(set(main_effects)) != len(main_effects):
            failures.append("coverage_policy.main_effects 含重复轴")
        unknown = [axis for axis in main_effects if axis not in dims]
        if unknown:
            failures.append(f"main_effects 含未知轴 {unknown}")
        missing = [axis for axis in dims if axis not in main_effects]
        if missing:
            failures.append(f"main_effects 缺少 dims 轴 {missing}")

    groups = policy.get("interaction_groups")
    parsed_groups = []
    if not isinstance(groups, list):
        failures.append("coverage_policy.interaction_groups 必须是数组")
    else:
        seen_groups = set()
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                failures.append(f"interaction_groups[{index}] 必须是对象")
                continue
            axes = group.get("axes")
            if (not isinstance(axes, list) or len(axes) < 2
                    or any(not isinstance(axis, str) for axis in axes)):
                failures.append(
                    f"interaction_groups[{index}].axes 至少包含两个轴")
                continue
            if len(set(axes)) != len(axes):
                failures.append(f"interaction_groups[{index}].axes 含重复轴")
            unknown = [axis for axis in axes if axis not in dims]
            if unknown:
                failures.append(f"interaction_groups[{index}] 含未知轴 {unknown}")
            if not isinstance(group.get("reason"), str) \
                    or not group["reason"].strip():
                failures.append(f"interaction_groups[{index}].reason 不能为空")
            group_key = tuple(sorted(axes))
            if group_key in seen_groups:
                failures.append(f"interaction_groups[{index}] 重复")
                continue
            seen_groups.add(group_key)
            if not unknown:
                parsed_groups.append(axes)

    targeted = policy.get("targeted")
    if (not isinstance(targeted, list)
            or any(not isinstance(tag, str) or not tag for tag in targeted)):
        failures.append("coverage_policy.targeted 必须是字符串标签数组")
        targeted = []
    elif len(set(targeted)) != len(targeted):
        failures.append("coverage_policy.targeted 含重复标签")

    max_cases = policy.get("max_cases")
    if not isinstance(max_cases, int) or isinstance(max_cases, bool) \
            or max_cases < 1:
        failures.append("coverage_policy.max_cases 必须是正整数")

    parsed_rules = []
    if not isinstance(infeasible, list):
        failures.append("infeasible 必须是数组")
    else:
        for index, rule in enumerate(infeasible):
            if not isinstance(rule, dict):
                failures.append(f"infeasible[{index}] 必须是对象")
                continue
            if not isinstance(rule.get("why"), str) or not rule["why"].strip():
                failures.append(f"infeasible[{index}] 缺少 why")
            constraints = {key: value for key, value in rule.items()
                           if key != "why"}
            if not constraints:
                failures.append(f"infeasible[{index}] 没有约束轴")
                continue
            unknown = [axis for axis in constraints if axis not in dims]
            if unknown:
                failures.append(f"infeasible[{index}] 含未知轴 {unknown}")
            off_denominator = [
                axis for axis, value in constraints.items()
                if axis in usable and not _contains(usable[axis], value)]
            for axis in off_denominator:
                failures.append(f"infeasible[{index}].{axis} 不在 dims 分母中")
            if not unknown and not off_denominator:
                parsed_rules.append(constraints)

    _raise(failures)
    return baseline, main_effects, parsed_groups, targeted, max_cases, parsed_rules


def _raise(failures):
    """有问题就一次性抛出全部，没问题就什么也不做。"""
    if not failures:
        return
    if len(failures) == 1:
        raise CoveragePolicyError(failures[0])
    listed = "\n".join(f"  {index}. {text}"
                        for index, text in enumerate(failures, 1))
    raise CoveragePolicyError(f"声明有 {len(failures)} 个问题：\n{listed}")


def _matching_rules(row, rules):
    return [
        rule for rule in rules
        if all(_key(row[axis]) == _key(value) for axis, value in rule.items())
    ]


# 规模三档与配比。字节阈值见 case-design.md，这里只管 combos 里的分布。
SIZE_AXIS = "size_class"
SIZE_BANDS = ("small", "medium", "large")
SIZE_TARGET = {"small": 0.40, "medium": 0.30, "large": 0.30}
SIZE_TOLERANCE = 0.12
# 配比在小样本上没有意义：几条 combos 谈不上 40/30/30。
RATIO_MIN_SAMPLE = 20

# 算子类别决定四件事：必需轴、默认三轴组、精度判据、dtype 配比。
# 以前这四处各判一次、各写一遍，现在收敛到一个声明字段上。
#
# `group` 是该类算子公认存在三方耦合的那组轴，每条都能说出机制：
#   movement    规模决定是否切核、操作轴位置决定是否跨步、尾块对齐决定尾块分支
#   elementwise dtype 决定向量指令位宽、规模决定切分、shape_form 决定尾块分支
#   reduction   归约轴位置决定核内还是跨核归约、规模决定是否跨核、dtype 决定累加精度
# 矩阵类暂缺：cube 的 tiling 分支没有可靠依据，不拿看起来合理的组合凑数。
OPERATOR_CLASSES = {
    "movement": {
        "arithmetic": False,
        "comparator": "equal",
        "axes": ("size_class", "shape_form", "op_axis_pos"),
        "group": ("size_class", "op_axis_pos", "shape_form"),
    },
    "elementwise": {
        "arithmetic": True,
        "comparator": "mixed_tolerance_bm",
        "axes": ("dtype", "size_class", "shape_form"),
        "group": ("dtype", "size_class", "shape_form"),
    },
    "reduction": {
        "arithmetic": True,
        "comparator": "mixed_tolerance_bm",
        "axes": ("dtype", "size_class", "reduce_axis_pos"),
        "group": ("reduce_axis_pos", "size_class", "dtype"),
    },
    # 随机生成类：输出不由输入算出，由随机数流决定。不做算术，所以不设浮点占比
    # 目标；能不能逐位比对取决于种子钉不钉得死，见 references/case-design.md#种子类参数。
    "generation": {
        "arithmetic": False,
        "comparator": "equal",
        "axes": ("dtype", "size_class", "shape_form"),
        "group": ("dtype", "size_class", "shape_form"),
    },
}

# 做算术的算子里浮点 dtype 的目标占比：精度问题只在浮点上出现，
# 整型运算通常精确，一条足够。搬运类不做算术，不强制。
FLOAT_DTYPES = frozenset({
    "fp16", "bf16", "fp32", "fp64", "hf32", "tf32", "complex64", "complex128",
    "float16", "float32", "float64", "bfloat16", "double", "float",
})
FLOAT_SHARE = 0.70
FLOAT_SHARE_TOLERANCE = 0.12

# 两两覆盖下限。实测一个五轴设计的「主效应 + 声明交互组」只买到 51%：
# 主效应把其余轴锚在 baseline 上，除声明的交互组外，其余轴对几乎不被触及。
# 同样条数改用两两覆盖阵列可以到 100%——阵列规模由最大两根轴取值数之积决定，
# 与轴的数量几乎无关，所以这个门槛不会推高用例数。
PAIRWISE_FLOOR = 0.90


def _pair_blocked(pair, rules):
    """只有当某条 infeasible 约束的轴全落在这一对里时，才判该组合不可行。"""
    return any(
        set(rule) <= set(pair)
        and all(_key(pair[axis]) == _key(value) for axis, value in rule.items())
        for rule in rules
    )


def pairwise_report(dims, combos, rules):
    """combos 的两两覆盖率。分母排除 infeasible 明确禁掉的组合。"""
    axes = sorted(dims)
    need = set()
    for a, b in itertools.combinations(axes, 2):
        for x in dims[a]:
            for y in dims[b]:
                if not _pair_blocked({a: x, b: y}, rules):
                    need.add((a, _key(x), b, _key(y)))
    hit = set()
    for combo in combos:
        if not isinstance(combo, dict):
            continue
        for a, b in itertools.combinations(axes, 2):
            if a in combo and b in combo:
                hit.add((a, _key(combo[a]), b, _key(combo[b])))
    hit &= need
    return {
        "required": len(need),
        "covered": len(hit),
        "rate": (len(hit) / len(need)) if need else 1.0,
        "worst_examples": sorted(f"{a}={x} × {b}={y}" for a, x, b, y in need - hit)[:8],
    }


def pairwise_rows(dims, infeasible=(), size_target=None, seed=0):
    """构造两两覆盖阵列，供算子专属必测集脚本打底。

    贪心逐行选取增益最大的候选。阵列规模由最大两根轴取值数之积决定，
    与轴的数量几乎无关——所以补轴几乎免费，不要为了省条数而少声明轴。

    `size_target` 给出规模三档的目标占比时，同增益的候选优先选还欠配额的档。
    """
    rnd = random.Random(seed)
    axes = sorted(dims)
    rules = []
    for entry in infeasible or ():
        if isinstance(entry, dict):
            constraints = {k: v for k, v in entry.items() if k != "why"}
            if constraints:
                rules.append(constraints)

    remaining = {
        (a, _key(x), b, _key(y))
        for a, b in itertools.combinations(axes, 2)
        for x in dims[a] for y in dims[b]
        if not _pair_blocked({a: x, b: y}, rules)
    }
    rows = []
    counts = {band: 0 for band in (size_target or {})}

    def debt(row):
        """该行所属规模档还欠多少配额；同增益时优先补欠得最多的档。"""
        band = row.get(SIZE_AXIS)
        if band not in counts:
            return 0.0
        placed = len(rows) or 1
        return size_target[band] - counts[band] / placed

    while remaining:
        best, best_score = None, (-1, 0.0)
        for _ in range(400):
            row = {a: rnd.choice(dims[a]) for a in axes}
            if _matching_rules(row, rules):
                continue
            gain = sum(
                1 for a, b in itertools.combinations(axes, 2)
                if (a, _key(row[a]), b, _key(row[b])) in remaining
            )
            score = (gain, debt(row))
            if score > best_score:
                best_score, best = score, row
        if best is None or best_score[0] <= 0:
            break
        rows.append(best)
        if best.get(SIZE_AXIS) in counts:
            counts[best[SIZE_AXIS]] += 1
        for a, b in itertools.combinations(axes, 2):
            remaining.discard((a, _key(best[a]), b, _key(best[b])))

    # 两两覆盖满足后，用最省的方式把规模配比补到目标：只改规模轴，不动其余轴。
    # 供体必须轮换——固定取第一条会把同一行反复克隆，把某个 dtype 堆到四成。
    cursor = 0
    for band, target in (size_target or {}).items():
        stalled = 0
        while rows and counts[band] / len(rows) < target - SIZE_TOLERANCE / 2:
            if stalled > len(rows):
                break
            source = rows[cursor % len(rows)]
            cursor += 1
            if source.get(SIZE_AXIS) == band:
                stalled += 1
                continue
            clone = dict(source)
            clone[SIZE_AXIS] = band
            if _matching_rules(clone, rules) or _key(clone) in {_key(r) for r in rows}:
                stalled += 1
                continue
            rows.append(clone)
            counts[band] += 1
            stalled = 0
    return rows


PROFILE_KEYS = ("arithmetic", "comparator", "axes", "group", "why")


def class_profile(operator_class, declared=None):
    """取算子类别的画像。

    `OPERATOR_CLASSES` 是已经积累到、可以直接用的那几类，命中就用它，
    声明文件不得覆盖——那几类的画像是固化事实，不是选择题。

    没命中不等于接不了：算子类别本来就开放，新类别的画像由声明文件的
    `class_profile` 块给出，脚本照它执行。这样判据仍然来自数据（声明 +
    combos 里每根轴的实际取值），只是数据多了一份，而不是让脚本去猜一个没见过
    的类别该有哪些轴。

    画像各项各管一件事，缺一项后面的判定就落空，所以一项都不能省：
    `axes` 是必需轴（缺一根就是覆盖盲区），`group` 是默认三轴交互组，
    `arithmetic` 决定要不要强制浮点占比，`comparator` 定这一类整份用例集用哪个
    比较器。`why` 记下这么定的依据。
    """
    if operator_class in OPERATOR_CLASSES:
        return OPERATOR_CLASSES[operator_class]
    if not operator_class:
        raise CoveragePolicyError("缺少 operator_class")
    if not isinstance(declared, dict) or not declared:
        raise CoveragePolicyError(
            f"operator_class {operator_class!r} 不在已固化的 {sorted(OPERATOR_CLASSES)} 里。\n"
            "    这不是「不支持」，是这一类的覆盖画像还没人写下来。\n"
            "    在声明文件加 class_profile 块补上：axes（必需轴，缺一根即覆盖盲区）、"
            "group（默认三轴交互组）、arithmetic（是否强制浮点占比）、"
            "comparator（整份用例集的比较器）、why（依据）。\n"
            "    依据只能来自任务书、基线接口语义或生态标准，不得从待验收算子实现反推。")
    missing = [key for key in PROFILE_KEYS if key not in declared]
    if missing:
        raise CoveragePolicyError(f"class_profile 缺少 {missing}，五项都要写")
    if not isinstance(declared["arithmetic"], bool):
        raise CoveragePolicyError("class_profile.arithmetic 必须是布尔值")
    for key in ("axes", "group"):
        values = declared[key]
        if (not isinstance(values, list) or not values
                or not all(isinstance(axis, str) and axis for axis in values)
                or len(set(values)) != len(values)):
            raise CoveragePolicyError(f"class_profile.{key} 必须是不重复的非空轴名数组")
    if not str(declared["why"]).strip():
        raise CoveragePolicyError("class_profile.why 不能为空")
    if not str(declared["comparator"]).strip():
        raise CoveragePolicyError("class_profile.comparator 不能为空")
    return {"arithmetic": declared["arithmetic"],
            "comparator": declared["comparator"],
            "axes": tuple(declared["axes"]), "group": tuple(declared["group"])}


def default_groups(operator_class, declared_groups=(), profile=None):
    """默认三轴组加上算子额外声明的组，去重后返回。"""
    groups = [list((profile or class_profile(operator_class))["group"])]
    seen = {tuple(sorted(groups[0]))}
    for axes in declared_groups:
        key = tuple(sorted(axes))
        if key not in seen:
            seen.add(key)
            groups.append(list(axes))
    return groups


def dtype_axis_floats(dims):
    """声明的 dtype 轴里的浮点取值。没有 dtype 轴时返回 None。

    float_share 门禁只在这根轴确实含浮点取值时才有判别力：轴里一个浮点都没有
    的分面（纯整型计算、搬运类整型分面），占比恒为 0，门禁在结构上不可能通过。

    判据取自轴的取值，不取自 must_cover 里的比较器声明。声明是一个可以随手写
    的标记，轴取值则决定生成什么用例、报告里出现什么 dtype，伪造不了。
    """
    if not isinstance(dims, dict) or "dtype" not in dims:
        return None
    values = dims.get("dtype")
    if not isinstance(values, (list, tuple)):
        return None
    return [v for v in values if str(v).lower() in FLOAT_DTYPES]


def float_equal_conflict(comparator, axis_floats, must_cover):
    """声明 equal 却在 dtype 轴上带浮点，判不判。

    这段话原先在 audit_coverage 里有两份逐字拷贝，只有 dtype 轴钉在工程
    声明表上那一支加了内置真值的豁免；另一支（回落路径）没加，走到那里时
    comparator_failures 要 equal、audit_coverage 要 mixed_tolerance_bm，
    两条判据互相否定，agent 无解。抽成一处，豁免只写一遍。
    """
    if comparator != "equal" or not axis_floats:
        return []
    if must_cover.get("baseline_kind") == "cann_builtin":
        # 两侧都在 NPU 上跑同一个 aclnn 接口，逐位相等正是要判的东西。
        return []
    return [f"比较器声明为 equal，但 dtype 轴含浮点取值 {axis_floats}。\n"
            "    浮点的位级相等在 NPU 上不成立，改声明 mixed_tolerance_bm——\n"
            "    ATK 按每个输出张量的 dtype 分别选路，整型会自动落到逐元素\n"
            "    相等，不必为此拆分面。唯一要单独走 equal 的是 int8。"]


def expected_comparator(must_cover, profile):
    """这份用例集该用哪个比较器。

    常规验收由算子类别定死。真值来自 CANN 内置实现时不一样：两侧都在 NPU 上
    跑同一个 aclnn 接口，任何一位不同都说明改动改变了输出，只能是 equal。
    「浮点的位级相等在 NPU 上不成立」那条讲的是跨后端比框架基线，不适用于这里。
    """
    if must_cover.get("baseline_kind") == "cann_builtin":
        return "equal"
    return profile.get("comparator")


def comparator_failures(must_cover, profile):
    """比较器由算子类别定死，不是逐个 dtype 现判的选择题。

    真机上出过这样一轮：一个搬运类算子（输出是输入元素的精确拷贝）整份声明了
    `mixed_tolerance_bm`，然后发现 ATK 对 int8 走量化标准、容忍差 1，于是把 int8
    单独拆成一份分面用 `equal`——多了一整套 YAML、必测集、用例 JSON、冻结目录和
    跑测命令，只为一个 dtype。

    可搬运类根本不做算术，**每一种 dtype 都该逐元素相等**，整份声明 `equal`
    就行，int8 自然跟着走 equal，一份都不用拆。会绕这一圈，是因为「默认声明
    `mixed_tolerance_bm`」被当成了所有算子的默认，而类别画像里的「完全相等」
    只写在文档表格里，没有任何判据认它。

    所以这里把它变成判据：类别定比较器，声明对不上就报，并说清拆分面的代价。
    """
    expected = expected_comparator(must_cover, profile)
    declared = must_cover.get("comparator")
    if not expected or declared is None or declared == expected:
        return []
    if must_cover.get("baseline_kind") == "cann_builtin":
        return [
            f"真值来自 CANN 内置实现时比较器只能是 equal，声明的却是 {declared}。\n"
            "    两侧都在 NPU 上跑同一个 aclnn 接口，比的是「改动有没有改变输出」，\n"
            "    不是「算得对不对」；任何一位不同都是要抓的东西。\n"
            "    见 references/builtin-baseline.md#比较器。"]
    hint = ("    搬运类的输出是输入元素的精确拷贝，不做算术，每种 dtype 都该逐元素\n"
            "    相等；整份用 equal，int8 不必单独拆一份分面。\n"
            "    注意 ATK 的 equal 走 torch.equal，只在**整张都是 NaN** 时特判；\n"
            "    含部分 NaN 的用例会误判失败，该分面要关掉 boundary.has_infnan。"
            if expected == "equal" else
            "    做算术的算子精度问题出在浮点上，浮点的位级相等在 NPU 上不成立；\n"
            "    声明 mixed_tolerance_bm，整型会自动落到逐元素相等。")
    return [f"operator_class 是 {must_cover.get('operator_class')!r}，"
            f"这一类的比较器是 {expected}，声明的却是 {declared}。\n" + hint]


def float_dtype_coverage(dims, combos, binding):
    """dtype 轴钉在工程声明表上时，浮点该怎么判。

    比例门禁（浮点占比 ≥70%）是为「dtype 轴由设计者自己挑」写的：挑得起，
    才谈得上按比例挑。轴取值改成照工程声明的数据类型表抄之后，浮点几个、
    整型几个是**工程的事实**，不是设计自由度——真机上两个算子的声明表都是
    3 种浮点对 5 种整型，混排分面的浮点占比结构上就到不了 70%。

    这时比例门禁只剩一个出口：按 dtype 把分面拆开，让整型那份吃
    `no_float_dtype` 豁免。而 case-design.md 同时写着「dtype 不构成拆分面的
    理由」。两条规则互相否定，agent 必然在这里返工，且必然拆出一份本不该
    存在的分面——真机两个算子各复现一次。

    所以 `--dtype-source` 指过那份文件时换判据，判的是同一件事（做算术的算子
    精度问题出在浮点上，浮点不能漏测），但换成一个对既定事实可满足的形式：

    - 工程声明表里的每种浮点，要么在这一分面的 dtype 轴上，要么在
      `dtype_source_excludes` 里写明去向；
    - 轴上的每个浮点取值，至少要被一条 combo 命中——声明了却一条用例都不生成，
      和没声明是一回事。

    返回 None 表示这条路不适用（没说 dtype 轴照哪份文件写的），调用方回落到比例门禁。
    """
    if not isinstance(binding, dict) or not binding.get("source"):
        return None
    axis_floats = dtype_axis_floats(dims)
    if axis_floats is None:
        return None
    source_floats = [d for d in (binding.get("declared_in_source") or [])
                     if str(d).lower() in FLOAT_DTYPES]
    excluded = {str(d) for d in (binding.get("excluded") or [])}
    missing = [d for d in source_floats
               if d not in {str(v) for v in axis_floats} and d not in excluded]
    hit = {str(combo.get("dtype")) for combo in combos
           if isinstance(combo, dict) and combo.get("dtype") is not None}
    unhit = [v for v in axis_floats if str(v) not in hit]
    return {"source": binding.get("source"),
            "source_floats": source_floats,
            "axis_floats": [str(v) for v in axis_floats],
            "excluded_floats": sorted(d for d in excluded
                                      if str(d).lower() in FLOAT_DTYPES),
            "missing_from_axis": missing,
            "declared_but_unused": [str(v) for v in unhit]}


def float_share_report(dims, combos):
    """浮点 dtype 在 combos 里的占比。没有 dtype 轴时返回 None。"""
    if not isinstance(dims, dict) or "dtype" not in dims:
        return None
    total = floats = 0
    for combo in combos:
        value = combo.get("dtype") if isinstance(combo, dict) else None
        if value is None:
            continue
        total += 1
        if str(value).lower() in FLOAT_DTYPES:
            floats += 1
    return {"total": total, "float_cases": floats,
            "share": (floats / total) if total else 0.0, "target": FLOAT_SHARE}


def size_ratio_report(dims, combos):
    """规模三档在 combos 里的占比。没有声明规模轴时返回 None。"""
    if not isinstance(dims, dict) or SIZE_AXIS not in dims:
        return None
    counts = {band: 0 for band in SIZE_BANDS}
    stray = sorted({
        str(combo.get(SIZE_AXIS)) for combo in combos
        if isinstance(combo, dict) and combo.get(SIZE_AXIS) not in SIZE_BANDS
    })
    for combo in combos:
        value = combo.get(SIZE_AXIS) if isinstance(combo, dict) else None
        if value in counts:
            counts[value] += 1
    total = sum(counts.values())
    return {
        "counts": counts,
        "total": total,
        "ratio": {b: (counts[b] / total if total else 0.0) for b in SIZE_BANDS},
        "target": SIZE_TARGET,
        "stray_values": stray,
    }


def build_anchored_rows(dims, policy, infeasible):
    """Return the baseline, main effects, and declared interaction projections."""
    baseline, main_effects, groups, _, max_cases, rules = _validate(
        dims, policy, infeasible)
    if _matching_rules(baseline, rules):
        raise CoveragePolicyError("coverage_policy.baseline 被 infeasible 排除")

    rows = []
    seen = set()

    def add(row):
        row_key = _key(row)
        if row_key not in seen:
            seen.add(row_key)
            rows.append(row)

    add(dict(baseline))
    for axis in main_effects:
        for value in dims[axis]:
            row = dict(baseline)
            row[axis] = value
            matches = _matching_rules(row, rules)
            if matches:
                declared_coupling = any(
                    set(rule) <= set(group)
                    for rule in matches
                    for group in groups
                )
                if (any(set(rule) <= {axis} for rule in matches)
                        or declared_coupling):
                    continue
                raise CoveragePolicyError(
                    f"轴 {axis!r} 的主效应被多轴约束遮蔽；"
                    "请声明对应 interaction group 或拆分接口分面")
            add(row)

    for axes in groups:
        for values in itertools.product(*(dims[axis] for axis in axes)):
            row = dict(baseline)
            row.update(zip(axes, values))
            matches = _matching_rules(row, rules)
            if matches:
                if any(set(rule) <= set(axes) for rule in matches):
                    continue
                raise CoveragePolicyError(
                    f"interaction group {axes} 的基线与组外约束冲突；"
                    "请扩大交互组或拆分接口分面")
            add(row)

    if len(rows) > max_cases:
        raise CoveragePolicyError(
            f"声明覆盖需要 {len(rows)} 条，超过 max_cases={max_cases}；"
            "不得静默截断，请调整交互组或预算")
    return rows


def audit_coverage(must_cover):
    """Audit the declared denominator before any case generation starts."""
    combos = must_cover.get("combos")
    selected = len(combos) if isinstance(combos, list) else 0
    report = {
        "strategy": None,
        "selected_cases": selected,
        "max_cases": None,
        "targeted_required": 0,
        "targeted_hit": 0,
        "duplicates": 0,
        "pairwise": None,
        "size_ratio": None,
    }
    failures = []
    policy = must_cover.get("coverage_policy")
    if not isinstance(policy, dict):
        failures.append("coverage_policy 缺失；覆盖分母不得由生成器或模型临时决定")
        return failures, report

    report["strategy"] = policy.get("strategy")
    report["max_cases"] = policy.get("max_cases")
    report["targeted_required"] = len(policy.get("targeted", [])) \
        if isinstance(policy.get("targeted"), list) else 0

    dims = must_cover.get("dims")
    infeasible = must_cover.get("infeasible", [])
    # 只校验策略本身是否自洽，不再要求 combos 复现某组预期取值。
    # combos 由 make_must_cover.py 从同一份策略确定性生成，
    # 再拿策略去比对自己的产物是循环的：模型若把策略和 combos 一起编错，
    # 这条断言照样通过。真正有判别力的是下面两组独立度量。
    try:
        _validate(dims, policy, infeasible)
    except CoveragePolicyError as exc:
        failures.append(f"coverage_policy 无效：{exc}")

    if not isinstance(combos, list) or not combos:
        failures.append("combos 必须是非空数组")
        return failures, report

    if isinstance(report["max_cases"], int) and selected > report["max_cases"]:
        failures.append(
            f"combos 有 {selected} 条，超过 max_cases={report['max_cases']}；"
            "不得静默截断")

    dim_keys = list(dims) if isinstance(dims, dict) else []
    projected = set()
    exact_seen = set()
    tags_seen = set()
    parsed_rules = []
    try:
        parsed_rules = _validate(dims, policy, infeasible)[-1]
    except CoveragePolicyError:
        pass

    for index, combo in enumerate(combos):
        if not isinstance(combo, dict):
            failures.append(f"combos[{index}] 必须是对象")
            continue
        absent = [axis for axis in dim_keys if axis not in combo]
        if absent:
            failures.append(f"combos[{index}] 缺少 dims 轴 {absent}")
            continue
        bad_values = [
            axis for axis in dim_keys
            if not _contains(dims[axis], combo[axis])
        ]
        if bad_values:
            failures.append(f"combos[{index}] 的轴 {bad_values} 不在 dims 分母中")
            continue
        row = {axis: combo[axis] for axis in dim_keys}
        if _matching_rules(row, parsed_rules):
            failures.append(f"combos[{index}] 命中 infeasible 声明")
        projected.add(_key(row))

        exact = {key: value for key, value in combo.items() if key != "coverage_tags"}
        exact_key = _key(exact)
        if exact_key in exact_seen:
            report["duplicates"] += 1
        exact_seen.add(exact_key)

        tags = combo.get("coverage_tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            failures.append(f"combos[{index}].coverage_tags 必须是字符串数组")
        else:
            tags_seen.update(tags)

    targeted = policy.get("targeted", []) if isinstance(policy, dict) else []
    if not isinstance(targeted, list) or any(
            not isinstance(tag, str) for tag in targeted):
        targeted = []
    targeted_hit = set(targeted) & tags_seen
    report["targeted_hit"] = len(targeted_hit)
    missing_tags = sorted(set(targeted) - tags_seen)
    if missing_tags:
        failures.append(f"combos 缺少定向覆盖标签 {missing_tags}")
    if report["duplicates"]:
        failures.append(f"combos 含 {report['duplicates']} 条重复用例")

    operator_class = must_cover.get("operator_class")
    report["operator_class"] = operator_class
    profile = None
    # 只有成形的组合设计才谈得上算子类别：单轴 dims 连一个轴对都没有，
    # 它的问题会在下游的覆盖比对里暴露，不在这里重复判。
    if isinstance(dims, dict) and len(dims) >= 2:
        try:
            profile = class_profile(operator_class,
                                    must_cover.get("class_profile"))
        except CoveragePolicyError as exc:
            failures.append(str(exc))
    if profile:
        failures.extend(comparator_failures(must_cover, profile))
        absent = [axis for axis in profile["axes"] if axis not in dims]
        if absent:
            failures.append(
                f"{operator_class} 类算子缺少必需轴 {absent}；缺一根就是覆盖盲区。\n"
                "    补轴几乎不增加用例数——覆盖阵列的规模由取值数最大的两根轴之积决定。\n"
                "    该概念对本算子确实不适用时，声明成单值轴（如 "
                '"contiguity": ["contiguous"]），单值轴对阵列规模零贡献，'
                "但把这个断言留了痕。")
        if profile["arithmetic"]:
            fs = float_share_report(dims, combos)
            report["float_share"] = fs
            axis_floats = dtype_axis_floats(dims)
            comparator = must_cover.get("comparator")
            bound = float_dtype_coverage(dims, combos,
                                         must_cover.get("dtype_binding"))
            no_float_axis = axis_floats == [] and not (fs and fs["float_cases"])
            if bound is not None and not no_float_axis:
                # dtype 轴钉在工程声明表上：判浮点漏没漏，不判占比。
                report["float_coverage"] = bound
                report["float_share_waived"] = "dtype_axis_bound_to_source"
                failures.extend(
                    float_equal_conflict(comparator, axis_floats, must_cover))
                if bound["missing_from_axis"]:
                    failures.append(
                        f"工程声明表 {bound['source']} 里的浮点 "
                        f"{bound['missing_from_axis']} 既不在 dtype 轴上，也没写进 "
                        "dtype_source_excludes。\n"
                        "    做算术的算子精度问题只出在浮点上，漏一种就是漏一整类；\n"
                        "    确实拆到了别的分面，就在 dtype_source_excludes 里写明去向。")
                if bound["declared_but_unused"]:
                    failures.append(
                        f"dtype 轴声明了浮点 {bound['declared_but_unused']}，"
                        "但一条 combo 都没用到。\n"
                        "    声明了却不生成用例，和没声明是一回事。")
            elif no_float_axis:
                # 轴里没有浮点取值，且 combos 也确实一条浮点都没有：
                # 占比恒为 0，门禁在这个分面上无判别力，记录豁免而不是判失败。
                report["float_share_waived"] = "no_float_dtype"
            else:
                failures.extend(
                    float_equal_conflict(comparator, axis_floats, must_cover))
                if fs and fs["total"] and fs["share"] < FLOAT_SHARE - FLOAT_SHARE_TOLERANCE:
                    failures.append(
                        f"浮点 dtype 占比 {fs['share'] * 100:.0f}%，低于 "
                        f"{FLOAT_SHARE * 100:.0f}%（容差 ±{FLOAT_SHARE_TOLERANCE}）。\n"
                        "    做算术的算子精度问题只出在浮点上，整型运算通常精确")

    if isinstance(dims, dict) and dims:
        pw = pairwise_report(dims, combos, parsed_rules)
        report["pairwise"] = pw
        if pw["rate"] < PAIRWISE_FLOOR:
            failures.append(
                f"两两覆盖率 {pw['rate'] * 100:.1f}%（{pw['covered']}/{pw['required']}），"
                f"低于下限 {PAIRWISE_FLOOR * 100:.0f}%。\n"
                "    主效应把其余轴锚在 baseline 上，只能覆盖到已声明交互组那几对；"
                "改用两两覆盖阵列打底，条数不会增加。\n"
                "    未覆盖示例：" + "、".join(pw["worst_examples"]))

        sr = size_ratio_report(dims, combos)
        report["size_ratio"] = sr
        if sr is not None:
            if sr["stray_values"]:
                failures.append(
                    f"{SIZE_AXIS} 出现非规模取值 {sr['stray_values']}；"
                    f"该轴只能取 {list(SIZE_BANDS)}，形态类（空张量、单元素、"
                    "非对齐尾块）请另立一根轴，不要和规模混在一起")
            elif sr["total"] >= RATIO_MIN_SAMPLE:
                off = {
                    band: round(sr["ratio"][band], 3)
                    for band in SIZE_BANDS
                    if abs(sr["ratio"][band] - SIZE_TARGET[band]) > SIZE_TOLERANCE
                }
                if off:
                    failures.append(
                        f"规模配比偏离目标 {SIZE_TARGET}，实际 {off}（容差 "
                        f"±{SIZE_TOLERANCE}）。\n"
                        "    性能验收要在三档上都测到硬件行为，中大档不足就测不到"
                        "多核切分和搬运流水")
    return failures, report
