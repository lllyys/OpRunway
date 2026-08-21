"""校验生成器注册、枚举和用例覆盖。

输入：YAML、生成器、必测集和用例集。
输出：validation.json。
退出码：0 通过；2 存在问题；3 无法检查。
"""

import argparse
import ast
import json
import os
import re
import sys

import check_coverage as cc
from _atk_capabilities import check_cases, load_capabilities
from _case_utils import (extract_axis, file_sha256, iter_cases, iter_input_specs,
                         load_json, numel, signature)
import _stage_card

# 与 atk/common/registry.py:165 的 Generate 模式正则保持一致：registry 就是用它数装饰器的
DECORATOR_RE = r'@GENERATOR_REGISTRY\.register\(["\'](.+?)["\']\)'


def read_generate_name(yaml_path):
    """读取 YAML 的 generate 字段。"""
    text = open(yaml_path, encoding="utf-8").read()
    try:
        import yaml
        value = (yaml.safe_load(text) or {}).get("generate")
        if value:
            return str(value)
    except ImportError:
        pass
    match = re.search(r"^\s*generate\s*:\s*(\S+)", text, re.M)
    return match.group(1).strip("\"'") if match else None


def declared_pairs(plugin_path):
    """静态解析装饰器名与类名。"""
    tree = ast.parse(open(plugin_path, encoding="utf-8").read())
    pairs = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call) or not deco.args:
                continue
            target = deco.func
            if not (isinstance(target, ast.Attribute) and target.attr == "register"):
                continue
            owner = target.value
            if isinstance(owner, ast.Name) and owner.id == "GENERATOR_REGISTRY":
                arg = deco.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    pairs.append((arg.value, node.name))
    return pairs


def register_plugin(plugin_path):
    """按 ATK 方式加载生成器注册表。"""
    from atk.case_generator.generator.base_generator import CaseGenerator
    from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY

    before = set(GENERATOR_REGISTRY.get_register_keys())
    GENERATOR_REGISTRY.register_from_python_file(os.path.abspath(plugin_path), "Generate")
    added = sorted(set(GENERATOR_REGISTRY.get_register_keys()) - before)

    module = sys.modules.get(os.path.splitext(os.path.basename(plugin_path))[0])
    subclasses = []
    if module is not None:
        for name, obj in vars(module).items():
            if not (isinstance(obj, type) and issubclass(obj, CaseGenerator)
                    and obj is not CaseGenerator):
                continue
            # 只数**本文件里定义**的类。`from ... import DefaultGenerator` 这类导入进来的
            # 基类也在 vars(module) 里，但 ATK 的 registry 扫的是文件文本，导入名不会被
            # 计入配对队列——把它算进来会让 C1 稳定误报「子类数 > 装饰器数」。
            if getattr(obj, "__module__", None) != module.__name__:
                continue
            subclasses.append(name)
    return GENERATOR_REGISTRY, added, subclasses


def check_registration(plugin_path, generate_name, failures, notes):
    """C1：注册名是否落在作者写的那个类上。"""
    source = open(plugin_path, encoding="utf-8").read()
    textual = re.findall(DECORATOR_RE, source)
    declared = declared_pairs(plugin_path)

    # 装饰器样式的文本出现在注释或 docstring 里也会被 registry 的 findall 计入，从而顶偏配对
    if len(textual) > len(declared):
        failures.append(
            f"C1 文件里有 {len(textual)} 处 @GENERATOR_REGISTRY.register 文本，但只有 "
            f"{len(declared)} 个真装饰器。多出的来自注释或 docstring——ATK 数装饰器用的是"
            "对整个文件做正则，注释里的示例同样会被计入并顶偏配对。\n"
            "  → 把 docstring/注释里的装饰器示例改写（例如去掉 @ 或写成 register(...)）。")

    try:
        registry, added, subclasses = register_plugin(plugin_path)
    except ImportError as exc:
        # 退出码 3 而不是 0：这两项没跑成不等于通过，见模块 docstring
        print(f"C1/C2 需要能 import atk，当前失败：{exc}\n"
              "  → 换用装了 ATK 的解释器（probe_env.py 探出的那个）重跑。\n"
              "     不要跳过这两项：注册错配与枚举表失效都不报错，跳过等于没检查。",
              file=sys.stderr)
        sys.exit(3)

    if len(subclasses) != len(declared):
        failures.append(
            f"C1 文件里有 {len(subclasses)} 个 CaseGenerator 子类 {subclasses}，"
            f"但只有 {len(declared)} 个装饰器。ATK 按位置配对，多出的类会抢走注册名，"
            "最后一个类直接不注册，且**不报任何错**。\n"
            "  → 共享逻辑的基类不要继承 CaseGenerator，改成不继承的 mixin；"
            "或给每个子类都加装饰器。")

    expected = dict(declared)
    if generate_name not in expected:
        failures.append(
            f"C1 YAML 的 generate: {generate_name!r} 在 {os.path.basename(plugin_path)} "
            f"里没有对应的装饰器（文件里有：{sorted(expected)}）。")
        return

    if generate_name not in registry.get_register_keys():
        failures.append(
            f"C1 注册名 {generate_name!r} 没有出现在注册表里（本次新增：{added}）。"
            "装饰器数少于 CaseGenerator 子类数时，末尾的类会被 registry 的 break 丢掉。")
        return

    actual = registry[generate_name].__name__
    if actual != expected[generate_name]:
        failures.append(
            f"C1 注册错配：YAML 写 generate: {generate_name!r}，源码里该装饰器挂在 "
            f"{expected[generate_name]} 上，实际解析到的却是 {actual}。\n"
            "  → 这条不会报错，用例照常生成，但生成用的是另一个类的枚举表。"
            "停止本轮。修复后开始新的生成轮次。")
    else:
        notes.append(f"C1 通过：{generate_name} → {actual}")


def check_enum_table(plugin_path, generate_name, must_cover, enum_attr, failures, notes):
    """C2：枚举表真的被加载进那个类了吗。"""
    from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY

    if generate_name not in GENERATOR_REGISTRY.get_register_keys():
        return  # C1 已经报过了，不重复刷屏

    cls = GENERATOR_REGISTRY[generate_name]
    table = getattr(cls, enum_attr, None)
    wanted = len(must_cover.get("combos", []))

    if table is None:
        notes.append(
            f"C2 跳过：{cls.__name__} 没有 {enum_attr} 类属性。"
            f"若本算子不用类属性承载枚举表，用 --enum-attr 指定实际名字。")
        return
    if len(table) != wanted:
        failures.append(
            f"C2 枚举表长度不符：{cls.__name__}.{enum_attr} 有 {len(table)} 条，"
            f"must_cover.json 有 {wanted} 条。\n"
            "  → 为 0 通常意味着解析到的是基类（见 C1）；非 0 但对不上，"
            "检查 constraint 里加载的 json 路径是不是本次这份。")
    else:
        notes.append(f"C2 通过：{cls.__name__}.{enum_attr} 已加载 {wanted} 条")


def check_override_effective(generate_name, failures, notes):
    """C2b：覆写真的会被调用吗。

    C1 只证明注册名指向了正确的类，C2 只证明枚举表挂上了类属性。
    两者都通过、而覆写仍然完全不生效的情况真实发生过：
    基类列表写成 `(CaseGenerator, Mixin)` 时，MRO 让 CaseGenerator 的同名方法胜出，
    mixin 变成死代码。ATK 不报错，退出码 0，只是产出退化成默认随机集。

    这里直接查 MRO 上这两个方法解析到谁，把该错误挡在唯一一次 atk case 之前。
    """
    from atk.case_generator.generator.base_generator import CaseGenerator
    from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY

    if generate_name not in GENERATOR_REGISTRY.get_register_keys():
        return  # C1 已经报过了

    cls = GENERATOR_REGISTRY[generate_name]
    dead = [name for name in ("_get_case_numbers", "generate")
            if getattr(cls, name, None) is getattr(CaseGenerator, name, None)]

    if not dead:
        notes.append(f"C2b 通过：{cls.__name__} 的 _get_case_numbers/generate 覆写均生效")
        return

    bases = "、".join(b.__name__ for b in cls.__bases__)
    failures.append(
        f"C2b {cls.__name__} 的 {'、'.join(dead)} 仍解析到 CaseGenerator，覆写没有生效。\n"
        f"  → 当前基类顺序是 ({bases})。承载覆写的 mixin 必须排在 CaseGenerator **前面**，"
        f"否则 MRO 让 CaseGenerator 的同名方法胜出。\n"
        "     这个错误不会抛异常：atk case 照样退出 0，只是产出条数退化成 "
        "max(各输入 dtype 数) × dtype_numbers。详见 plugin-authoring.md 的硬约束二。")


def check_case_set_is_enum(cases, wanted_sigs, axes, rules, failures):
    """C5：核对用例集仅包含枚举组合。"""
    stray = [c.get("id") for c in cases
             if signature({a: extract_axis(c, rules[a]) for a in axes}, axes)
             not in wanted_sigs]
    if stray:
        failures.append(
            f"C5 有 {len(stray)} 条用例不在枚举表内（用例号 {stray[:8]}）。\n"
            "  → 这批是 ATK 自主产出的：extra 通道的边界用例，或枚举表装不满容量后的"
            "随机跨输入配对。它们不进覆盖核算，却会增加无设计依据的执行输入。\n"
            "     修法见 plugin-authoring.md：YAML 设 extra_numbers: 0，"
            "constraint 覆写 _get_case_numbers 返回 len(MUST_COVER)，"
            "五类边界场景写进枚举表。")


def check_runtime_input_structure(cases, failures, notes, backend="pyaclnn"):
    """C6：按版本化能力矩阵检查生成后的运行期参数结构。"""
    found, passed = check_cases(cases, backend, load_capabilities())
    failures.extend(f"C6 {item}" for item in found)
    notes.extend(f"C6 {item}" for item in passed)


# 随机数种子的参数名。`seed` 单独出现就算；`offset` 一类词在切片、嵌入等
# 算子里是普通参数，只有当同一条用例里已经有 seed 时才把它一起当种子看。
SEED_WORDS = ("seed",)
SEED_COMPANION_WORDS = ("offset", "generator", "philox")


def _name_tokens(name):
    """把 randSeed / random_seed / seedValue 都拆成小写词。"""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", "_", str(name or ""))
    return {token for token in re.split(r"[^0-9a-zA-Z]+", spaced.lower()) if token}


def seed_parameter_names(cases, extra_names=()):
    """用例里出现的种子类参数名。

    词表判不出的名字（philoxState 之类）由 interface.json 的 seed_parameters
    补进来——那份名单是 S1 读工程头文件得到的，比词表可靠。
    """
    names = set(extra_names)
    for case in iter_cases(cases):
        specs = list(iter_input_specs(case))
        primary = {spec.get("name") for spec in specs
                   if _name_tokens(spec.get("name")) & set(SEED_WORDS)}
        names |= {n for n in primary if n}
        if not primary:
            continue
        for spec in specs:
            if _name_tokens(spec.get("name")) & set(SEED_COMPANION_WORDS):
                names.add(spec.get("name"))
    return {n for n in names if n}


def _declared_value(spec):
    return spec.get("range_values", spec.get("value"))


def _is_interval(value):
    """`[0, 100]` 是取值区间，ATK 会在里面随机取；`[42, 42]` 是钉死的常量。"""
    return (isinstance(value, list) and len(value) == 2
            and not isinstance(value[0], (list, dict))
            and value[0] != value[1])


def check_seed_is_pinned(cases, failures, notes, extra_names=()):
    """C7：种子类参数必须在用例数据里钉成同一个常量。

    不钉死，两次跑测（冒烟与全量、复现、与 CANN 内置实现比对）拿到的随机数流
    就不是同一条，任何逐元素判据都不成立，报告也复现不出来。
    判据从用例数据推导：同一个参数名在全部用例里只能有一个取值，
    且那个取值不能是区间。
    """
    all_cases = list(iter_cases(cases))
    names = seed_parameter_names(all_cases, extra_names)
    if not names:
        return
    for name in sorted(names):
        seen, intervals = set(), []
        for case in all_cases:
            for spec in iter_input_specs(case):
                if spec.get("name") != name:
                    continue
                value = _declared_value(spec)
                if _is_interval(value):
                    intervals.append(case.get("id"))
                seen.add(json.dumps(value, sort_keys=True, ensure_ascii=False))
        if intervals:
            failures.append(
                f"C7 种子参数 {name!r} 在用例 {intervals[:8]} 里写的是取值区间。\n"
                "  → 区间会让 ATK 每次跑测各取一个值，两轮拿到的随机数流不同。\n"
                "     在 constraint 里把它逐条覆写成同一个常量，见 "
                "case-design.md#种子类参数。")
        elif len(seen) > 1:
            failures.append(
                f"C7 种子参数 {name!r} 在用例集里有 {len(seen)} 个不同取值。\n"
                "  → 种子按用例变化时，复现与和 CANN 内置实现的逐位比对都不成立。\n"
                "     在 constraint 里把它钉成同一个常量，见 "
                "case-design.md#种子类参数。")
        elif seen:
            notes.append(f"C7 种子参数 {name!r} 全用例钉在 {seen.pop()}")
        # seen 为空：interface.json 的名单里有这个参数，但这份用例集里没有它
        # （分面拆分之后很正常）。没出现就没什么好钉的，不报也不记。


def _numeric_list(value):
    """shape 之外的列表轴（shifts、dims）可能嵌套，numel 只能吃纯数值列表。

    以前直接对任意 list 调 numel，遇到 attrs 轴的嵌套取值就崩在
    math.prod 上——诊断函数本身把诊断打断了。
    """
    return (isinstance(value, list)
            and all(isinstance(item, (int, float)) and not isinstance(item, bool)
                    for item in value))


def diagnose_missing(combo, axes, produced_values):
    """C4：为缺失组合查找最近邻。"""
    best, best_diff = None, None
    for values in produced_values:
        diff = [a for a in axes if signature({a: combo.get(a)}, [a])
                != signature({a: values.get(a)}, [a])]
        if best_diff is None or len(diff) < len(best_diff):
            best, best_diff = values, diff
            if not diff:
                break
    if best is None:
        return "用例集是空的"
    if len(best_diff) == len(axes):
        return ("没有任何轴对得上的产出用例：先查 C1/C2（覆写是否生效），"
                "再查生成容量 max_dtype_count × dtype_numbers 是否装得下枚举表")

    hints = []
    for axis in best_diff:
        want, got = combo.get(axis), best.get(axis)
        seen = sum(1 for v in produced_values
                   if signature({axis: v.get(axis)}, [axis]) == signature({axis: want}, [axis]))
        if seen:
            hints.append(
                f"{axis}: 期望 {want!r} 在产出集中出现 {seen} 次（配在别的组合上），"
                "说明生成得出来——查这条组合是没被产出，还是执行期被排除了")
        elif (_numeric_list(want) and _numeric_list(got)
              and numel(got) < numel(want)):
            hints.append(
                f"{axis}: 期望 {want}（{numel(want)} 元素）一次都没出现，最近邻是 "
                f"{got}（{numel(got)} 元素）规模更小——符合钉住的值被 after_case_config "
                "裁剪改掉的特征，去核对该值是否满足 constraint 里的规模预算")
        else:
            hints.append(
                f"{axis}: 期望 {want!r} 在产出集中一次都没出现（最近邻 {got!r}）——"
                "查 YAML 的取值池是否含该值、枚举表是否被覆写生效")
    return "；".join(hints)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Phase A 用例集自检（C1 注册 / C2 枚举表 / C3 覆盖 / "
            "C4 诊断 / C5 用例集纯度 / C6 运行期结构 / C7 种子钉死）"
        ))
    parser.add_argument("-y", "--yaml", required=True, help="用例设计 YAML")
    parser.add_argument("-p", "--plugin", required=True, help="constraint 插件文件")
    parser.add_argument("-m", "--must-cover", required=True,
                        help="物化后的组合表（*_materialized.json）。未物化的 must_cover.json 只有语义轴，没有 shape 和 attr 取值，会命中 0 组")
    parser.add_argument(
        "-j", "--case-json",
        help=(
            "atk case 的产物。生成前自检时省略：只跑 C1/C2 这类静态检查，"
            "把插件接线问题挡在唯一一次 atk case 之前"
        ))
    parser.add_argument("-o", "--output", default="validate.json")
    parser.add_argument("--interface", default="evidence/interface.json",
                        help="derive_interface.py 的产物；读 seed_parameters，"
                             "补 C7 词表判不出来的种子参数名")
    parser.add_argument("--enum-attr", default="MUST_COVER", help="承载枚举表的类属性名")
    parser.add_argument(
        "--skip-static", action="store_true",
        help=(
            "跳过 C1/C2/C2b。生成后复检时用：这三项是插件的静态属性，"
            "生成前自检已经验过，而生成后不允许再改插件。"
            "只有它们需要 import atk，跳过后本次检查不再付 ATK 启动开销"
        ))
    parser.add_argument(
        "--backend",
        default="pyaclnn",
        choices=("pyaclnn", "npu", "aclnn", "kernel", "torch"),
        help="运行后端；默认按完整验收的 pyaclnn 绑定限制检查",
    )
    args = parser.parse_args()
    _stage_card.announce(__file__)

    failures, notes = [], []

    generate_name = read_generate_name(args.yaml)
    if not generate_name:
        raise SystemExit(f"{args.yaml} 里没有 generate 字段，无法确定该用哪个生成器。")

    must_cover = load_json(args.must_cover)
    cc.check_dims(must_cover)

    if args.skip_static:
        if not args.case_json:
            raise SystemExit("--skip-static 只用于生成后复检，必须同时给 -j。")
        notes.append("C1/C2/C2b 跳过：生成前自检已验过，且生成后不允许再改插件")
    else:
        check_registration(args.plugin, generate_name, failures, notes)
        check_enum_table(args.plugin, generate_name, must_cover, args.enum_attr,
                         failures, notes)
        check_override_effective(generate_name, failures, notes)

    # 生成前自检：只有静态检查能跑，跑完就返回，不碰生成产物
    if not args.case_json:
        for note in notes:
            print(f"  {note}")
        if not failures:
            print("生成前自检通过（C1 注册 / C2 枚举表 / C2b 覆写生效），可以运行 atk case。")
            return 0
        print("", file=sys.stderr)
        for failure in failures:
            print(f"✗ {failure}", file=sys.stderr)
        print("\n生成前自检未通过，不要运行 atk case。", file=sys.stderr)
        return 2

    # C3：读回轴值与比对完全复用 check_coverage，避免同一套匹配规则出现第二个副本
    axes = must_cover["axes"]
    rules = must_cover.get("extract", {})
    wanted = {}
    for combo in must_cover["combos"]:
        wanted.setdefault(signature(combo, axes), combo)

    produced_values, produced = [], set()
    for case in iter_cases(load_json(args.case_json)):
        values = {a: extract_axis(case, rules[a]) for a in axes}
        produced_values.append(values)
        produced.add(signature(values, axes))

    missing = [combo for sig, combo in wanted.items() if sig not in produced]
    if missing:
        gap = f"C3 必测集缺口 {len(missing)}/{len(wanted)} 组"
        # 读回轴值用的是 agent 手写的 extract 规则，规则写错和用例真没覆盖，
        # 从数据上长得一样。几乎全不命中时，前者的可能性远大于后者。
        if len(missing) > len(wanted) * 0.8:
            gap += ("。\n  → 缺口占比这么高，更像 extract 规则没把轴读对"
                    "（读的键、索引或 from 类型不对），先拿一条用例手工核规则，"
                    "不要先去补用例")
        failures.append(gap)

    all_cases = list(iter_cases(load_json(args.case_json)))
    check_case_set_is_enum(all_cases, set(wanted), axes, rules, failures)
    check_runtime_input_structure(all_cases, failures, notes, args.backend)
    extra_seed_names = []
    if os.path.exists(args.interface):
        with open(args.interface, encoding="utf-8") as handle:
            extra_seed_names = json.load(handle).get("seed_parameters") or []
    check_seed_is_pinned(all_cases, failures, notes, extra_names=extra_seed_names)
    if not failures:
        notes.append(f"C5 通过：{len(all_cases)} 条用例全部来自枚举表")

    # C4：只对缺口做诊断，全覆盖时不做——最近邻搜索是 O(缺口 × 用例)
    diagnoses = [{"combo": combo, "diagnosis": diagnose_missing(combo, axes, produced_values)}
                 for combo in missing[:10]]

    report = {
        "generate": generate_name,
        "case_file_sha256": file_sha256(args.case_json),
        "must_cover_total": len(wanted),
        "must_cover_hit": len(wanted) - len(missing),
        "missing": missing,
        "diagnoses": diagnoses,
        "checks_passed": notes,
        "failures": failures,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    for note in notes:
        print(f"  {note}")
    print(f"C3 必测集 {len(wanted)} 组，命中 {report['must_cover_hit']} 组 → {args.output}")

    if not failures:
        print("检查全部通过，可以进 Phase B。")
        return 0

    print("", file=sys.stderr)
    for failure in failures:
        print(f"✗ {failure}", file=sys.stderr)
    for item in diagnoses:
        print(f"\n  缺口 {item['combo']}\n    ↳ {item['diagnosis']}", file=sys.stderr)
    print("\n断言未通过，不要进 Phase B。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
