#!/usr/bin/env python3
"""跑 atk case 生成用例，收敛到工作目录的 cases.json。

--dry-run 用 dtype_numbers=1 快速验证 YAML 与插件能不能组合出合法用例。
退出码 0 通过，2 atk case 失败或用例数不足，3 输入文件缺失。
"""

import argparse
import copy
import json
import os
import random
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_params  # noqa: E402
from case_shape import (  # noqa: E402
    all_tensor_items, dtype_of, first_tensor, guard, size_band,
)


# 生成侧只要 CPU 版 torch。装了 torch_npu 的机器上 `import torch` 会去自动加载
# 它，没 source CANN 时抛 RuntimeError，连带 atk 也起不来。关掉自动加载，
# 让「生成侧不需要 NPU 与 CANN」这句话成立。必须在任何 torch 导入之前设。
os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")
# 超时按「生成侧全程 ≤ 20 分钟」的预算切分：dry-run 60 + 正式生成 300 +
# golden 冻结 600 = 最坏 16 分钟。填了 focus_dtypes 时正式生成拆两轮，每轮各
# 300s 上限，但每轮的用例数也减半，实测总耗时不涨。
# **不要因为某个算子跑得慢就调大它**——先查清楚慢在哪，再决定改什么。
DRY_RUN_TIMEOUT = 60
FULL_RUN_TIMEOUT = 300
MIN_CASES = 100
# 设计目标 150–400（case-strategy.md「用例规模」）。超过这个数**只提示不拦**：
# 覆盖面是 YAML 设计决定的，条数只是结果，用户点名要更多条时没有理由拒绝。
# 但代价要说清楚——golden 体积近似线性，S4 的冻结超时是死的 600 秒。
SOFT_MAX_CASES = 500
# golden 体积的线性外推基准，来自 CLAUDE.md 真机事实表的 IndexFillTensor 实测。
GOLDEN_MB_PER_CASE = 198 / 200
# `facts.json` 有 focus_dtypes 时，那几种 dtype 在全量里占多少。剩下的均分余额。
# 只有「给已有算子扩 dtype」的任务会用到——没填 focus_dtypes 时一轮跑完，行为不变。
FOCUS_SHARE = 0.5


def _glob_here_then_gen(pattern, exclude=()):
    """按 pattern 找文件：工作目录优先，其次 gen/。

    S4 收尾会把 YAML 与约束器归进 gen/，两处都要认。**不合并成一个列表去重**——
    工作目录里有同名文件时以它为准，那是用户刚改的那份。

    `exclude` 在**决定要不要看 gen/ 之前**就生效。放到调用方去过滤是不行的：
    工作目录里只剩一个 `nodes.yaml` 时，过滤前它让 here 非空，gen/ 就永远轮不上，
    表现是归位之后报「没有 YAML」。实测踩过。"""
    def pick(base):
        return [p for p in sorted(base.glob(pattern)) if p.name not in exclude]
    here = pick(Path("."))
    if here:
        return here
    return pick(Path("gen"))


def _find_yaml(explicit):
    if explicit:
        path = Path(explicit)
        if not path.exists():
            print(f"{path} 不存在", file=sys.stderr)
            return None
        return path
    # 先看工作目录，再看 gen/。S4 冻完会把 YAML 归进 gen/（跑测侧不读它），
    # 归位之后回头重跑 S3 是常事，少了这条兜底就报「没有 YAML」。
    skip = ("nodes.yaml",)  # atk 的节点配置，不是我们写的用例设计
    candidates = (_glob_here_then_gen("*.yaml", skip)
                  + _glob_here_then_gen("*.yml", skip))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        print("工作目录和 gen/ 下都没有 YAML。"
              "按 references/yaml-authoring.md 先写 <op>.yaml。",
              file=sys.stderr)
    else:
        names = "、".join(p.name for p in candidates)
        print(f"工作目录下有多个 YAML（{names}），用 -f 点名要哪个。", file=sys.stderr)
    return None


def _stale_plugins(yaml_doc):
    """列出目录里存在、但 YAML 根本没引用的插件文件。

    重跑同一个算子时 `cases.json` 与 `golden/` 会被就地覆盖，**插件文件不会**。
    上一轮写的执行器留在原地，跟着用例包交到跑测侧就会被当成本轮的设计加载，
    而它描述的是另一套参数映射——精度大面积失败，谁也想不到是残留文件。
    """
    stale = []
    if str(yaml_doc.get("generate") or "default") == "default":
        stale += [p.name for p in _glob_here_then_gen("*_constraint.py")]
    if str(yaml_doc.get("api_type") or "function") == "function":
        stale += [p.name for p in sorted(Path(".").glob("function_*.py"))]
    return stale


# `@register("x")` 与 `@GENERATOR_REGISTRY.register("x")` 都匹配。
REGISTER_RE = re.compile(r"""@(?:\w+\.)?register\(\s*["']([^"']+)["']""")
# ATK 自带的执行方式注册名，取自 atk/tasks/api_execute/ 各模块的 @register。
# YAML 的 api_type / aclnn_api_type 填这些之外的值，就是在指名一个本地执行器。
ATK_API_TYPES = {
    "function", "aclnn_function", "triton_function", "fusion_function",
    "function_list", "dist_function", "function_tensorflow", "kernel_function",
    "atb_function", "tensor", "method", "xrun_aclnn",
    "graph_function", "graph_method", "graph_tensor",
}


def _registered_names(pattern):
    """目录里 pattern 匹配的插件文件各自注册了什么名字 -> {注册名: 文件名}。

    静态扫字符串，不 import：import 要 atk 在位，还会把插件的副作用带进来，
    而这里只想比对一个名字。
    """
    found = {}
    for path in _glob_here_then_gen(pattern):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for name in REGISTER_RE.findall(text):
            found.setdefault(name, path.name)
    return found


def _name_mismatches(yaml_doc, plugin=None):
    """YAML 里指名的注册名与插件文件里 @register 的对不上时列出来。

    这类错第一次真正暴露是 S4 跑标杆（「标杆一条都没跑出来」），白花一轮
    生成加冻结的时间；约束器那侧更糟——ATK 找不到注册名就退回默认生成器，
    约束**静默失效**，用例照样生成、条数照样够。所以在 dry-run 就静态比一次。
    """
    problems = []
    executors = _registered_names("function_*.py")
    # -p 指到目录外的文件时 glob 看不见它，补进来，否则合法配置被误报。
    constraints = _registered_names("*_constraint.py")
    if plugin is not None and Path(plugin).name not in constraints.values():
        constraints.update(_registered_names(str(plugin)))
    checks = [
        ("api_type", "function", executors, "CPU 执行器"),
        ("aclnn_api_type", "aclnn_function", executors, "aclnn 执行器"),
        ("generate", "default", constraints, "约束器"),
    ]
    for field, default, registered, role in checks:
        want = str(yaml_doc.get(field) or default)
        if want == default or want in ATK_API_TYPES:
            continue
        if want in registered:
            continue
        actual = ("、".join(f"{n}（{f}）" for n, f in sorted(registered.items()))
                  if registered else "目录里没有对应的插件文件")
        problems.append(f"YAML 的 {field}: {want} —— 没有{role}注册这个名字。实际注册的是：{actual}")
    return problems


def _find_plugin(yaml_path):
    """约束器按 <stem>_constraint.py 找，找不到就不传 -p。"""
    for guess in (Path(f"{yaml_path.stem}_constraint.py"),
                  Path("gen") / f"{yaml_path.stem}_constraint.py"):
        if guess.exists():
            return guess
    matches = _glob_here_then_gen("*_constraint.py")
    return matches[0] if len(matches) == 1 else None


PERF_BAND_TARGET = {"small": 0.4, "medium": 0.3, "large": 0.3}


def _yaml_dict(yaml_path):
    """读 YAML 成 dict，读不出来返回空 dict——拆分与配对都会因此退回默认行为。"""
    try:
        import yaml  # noqa: PLC0415  atk 自带
    except ImportError:
        return {}
    try:
        loaded = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  YAML 本身的错留给 atk case 去报
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _tensor_dtypes(yaml_doc):
    """YAML 里第一个张量输入声明的 dtype 全集。拆分的分母。"""
    for item in yaml_doc.get("inputs") or []:
        if not isinstance(item, dict) or item.get("type") not in {"tensor", "tensors"}:
            continue
        values = ((item.get("dtypes") or {}).get("values")) or []
        return [str(v) for v in values]
    return []


def _rounds(yaml_doc, focus, per_dtype=None):
    """按 focus_dtypes 把一轮生成拆成两轮，返回 [(要滤掉的 dtype, -dt 值), ...]。

    `per_dtype` 给定时（`--dtype-numbers`）用它当每 dtype 条数，不读 YAML 的
    `dtype_numbers`——拆轮次的算式不变，只是分母换了个来源。

    ATK 的 `dtype_numbers` 是**一个整数**，对每个 dtype 一视同仁
    （`design_config.py:477`、`base_generator.py:198`），没有按 dtype 加权的入口。
    所以加权只能靠跑两轮 `atk case`，用 `-df` 各滤掉对方那组，再合并：

        第 1 轮  -df <其余 dtype>  -dt <多>   -> 只出 focus 那几种
        第 2 轮  -df <focus>       -dt <少>   -> 只出其余那几种

    两组都是 ATK 自己按规模档与 shape 边界正常抽的，不是从一堆里挑剩的。
    **拆不出来就返回空**，调用方跑原来那一轮，行为不变：没填 focus_dtypes、
    focus 覆盖了全部 dtype、YAML 里读不到 dtype 列表，都走这条。
    """
    universe = _tensor_dtypes(yaml_doc)
    focus = [d for d in focus if d in universe]
    rest = [d for d in universe if d not in focus]
    if not focus or not rest:
        return []
    if per_dtype is None:
        try:
            per_dtype = int(yaml_doc.get("dtype_numbers"))
        except (TypeError, ValueError):
            return []
    if per_dtype < 1:
        return []

    total = per_dtype * len(universe)
    focus_n = max(1, round(total * FOCUS_SHARE / len(focus)))
    rest_n = max(1, round(total * (1 - FOCUS_SHARE) / len(rest)))
    return [(rest, focus_n), (focus, rest_n)]


def _mirror(case, mine, base, new_id):
    """复制一条用例，把 dtype 从 mine 换成 base，其余（shape、attr、值域）全不动。

    给 `performance.kind=cross_dtype` 用：任务书要的是「同样的活儿，换个 dtype
    快了还是慢了」，只有 shape 完全相同的两条用例比出来才是纯 dtype 效应。
    靠随机抽样撞不出同 shape 的两条，复制是唯一稳的做法。

    镜像件会一起进全量 `cases.json`，`freeze_golden.py` 照常给它们冻 golden，
    所以跑测侧不用做任何转换——它拿到的就是两条正常用例。
    """
    twin = copy.deepcopy(case)
    twin["id"] = new_id
    for item in all_tensor_items(twin):
        if item.get("dtype") == mine:
            item["dtype"] = base
    return twin


def paired_perf_subset(cases, number, seed, pairs):
    """`kind=cross_dtype` 专用：抽出成对的性能子集，返回 (子集, 镜像件, 配对表)。

    任务书那句「uint8 性能对比 fp16 劣化 5% 之内」要成立，两组得跑**同一批
    shape**——否则比值里混着 shape 效应，而同一 dtype 内不同 shape 的耗时差几个
    量级，5% 这个阈值早被淹掉。所以抽出的每条被测件都配一条只换 dtype 的镜像件。

    **`number` 在这里是对数，不是条数**：`--perf-number 50` 抽 50 对、落盘 100
    条。比值由每组各自的中位数算出，两组各要 50 个样本才和非成对形态下 50 条
    的统计量对齐；折半成 25 对等于把样本数砍掉一半。多组 pair 时 50 对按组均分。

    镜像件是新用例，调用方要把它们追加进全量再写盘，`freeze_golden.py` 才会
    给它们冻 golden。

    **配对表要落盘。** 只在内存里成对、写盘成扁平列表的话，跑测侧拿不到谁跟谁
    配对，只能按 dtype 分两堆取中位数相除——出不了逐对比值，也验不出配对被
    破坏（某条被剔掉而它的镜像还在，两组分布就歪了，而中位数相除照样出结论）。
    """
    per_pair = max(1, number // len(pairs))
    next_id = max((case.get("id", 0) for case in cases), default=-1) + 1
    random.seed(seed)

    subset, mirrors, meta = [], [], []
    for mine, base in pairs:
        by_band = defaultdict(list)
        for case in cases:
            if dtype_of(case) == mine:
                by_band[size_band(case)].append(case)
        picked = []
        for band, share in PERF_BAND_TARGET.items():
            pool = by_band.get(band, [])[:]
            random.shuffle(pool)
            picked += pool[:round(per_pair * share)]
        if len(picked) < per_pair:
            taken = {id(c) for c in picked}
            spare = [c for c in cases if dtype_of(c) == mine and id(c) not in taken]
            random.shuffle(spare)
            picked += spare[:per_pair - len(picked)]
        for case in picked:
            twin = _mirror(case, mine, base, next_id)
            next_id += 1
            subset += [case, twin]
            mirrors.append(twin)
            # 档位取**被测侧**那条。镜像件 shape 相同但 dtype 不同，字节数跟着变，
            # 按它自己算可能落进别的档，一对就跨了两档没法比。
            meta.append({"mine": case["id"], "base": twin["id"],
                         "band": size_band(case), "dtype": [mine, base]})
    subset.sort(key=lambda case: case["id"])
    return subset, mirrors, meta


def write_perf_subset(cases, out_dir, number, seed, picked=None, pair_meta=None):
    """抽性能子集：**先按规模档配额，再按 dtype 补齐**，落盘成 <out_dir>/cases.json。

    `picked` 给定时直接用它（`kind=cross_dtype` 的成对子集已经在上面挑好了），
    只走落盘与打印。

    配额优先的理由：性能轮要覆盖的是切分路径——UB 装不下时的循环切分、
    多核切分、尾核处理，那是**规模**决定的，不是 dtype。纯按 dtype × 档位
    均分桶时 large 桶数少，全量里有十几条也只进得去两三条。

    配额按目标占比算，某档不够就有多少拿多少，缺口留给别的档补齐。
    **不追全量里的 large 占比**：精度轮追 30% large 意味着几十条数 MB 的张量，
    golden 要几百 MB、冻结时间也上去，而 tiling 边界缺陷在 2^n±1 的小张量上
    就能暴露。全量只要够填满子集配额即可。

    **文件名固定叫 cases.json，换目录不换名。** ATK 拿用例文件基名当 golden 的
    子目录名（`atk/tasks/result_process.py:67`），改名后跑测侧找不到 golden。
    同一份 golden 因此被全量与子集共用，不用重造。
    """
    if picked is not None:
        # 成对子集的落盘条数是对数的两倍，门槛跟着走，否则「抽不够」永远不报。
        return _dump_perf_subset(picked, cases, out_dir, len(picked), pair_meta=pair_meta,
                                 pair_count=len(picked) // 2)

    by_band = defaultdict(list)
    for case in cases:
        by_band[size_band(case)].append(case)

    random.seed(seed)
    picked, taken = [], set()
    for band, share in PERF_BAND_TARGET.items():
        pool = [c for c in by_band.get(band, []) if id(c) not in taken]
        random.shuffle(pool)
        quota = round(number * share)
        for case in pool[:quota]:
            picked.append(case)
            taken.add(id(case))

    # 配额没填满就按 dtype × 档位补齐，顺带把 scalar 档带进来
    if len(picked) < number:
        rest = [c for c in cases if id(c) not in taken]
        buckets = defaultdict(list)
        for case in rest:
            buckets[f"{dtype_of(case)}/{size_band(case)}"].append(case)
        spare = []
        for key in sorted(buckets):
            group = buckets[key][:]
            random.shuffle(group)
            picked.append(group[0])
            taken.add(id(group[0]))
            spare.extend(group[1:])
        random.shuffle(spare)
        picked.extend(spare[:number - len(picked)])
    picked = picked[:number]
    picked.sort(key=lambda case: case["id"])
    return _dump_perf_subset(picked, cases, out_dir, number)


def _dump_perf_subset(picked, cases, out_dir, number, pair_count=None, pair_meta=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "cases.json", "w", encoding="utf-8") as handle:
        json.dump(picked, handle, ensure_ascii=False)

    # 跑测侧要按规模档出加速比，档位是这边的知识（size_band 的字节门槛）。
    # 把这边算好的结果传过去，别让那边重算——两套阈值不一致真机上出过事。
    manifest = {"bands": {str(case["id"]): size_band(case) for case in picked}}
    if pair_meta:
        manifest["pairs"] = pair_meta
    with open(out_dir / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    bands = Counter(size_band(case) for case in picked)
    source = Counter(size_band(case) for case in cases)
    paired = f"（{pair_count} 对同 shape 镜像）" if pair_count else ""
    print(f"性能子集  {len(picked)} 条{paired} -> {out_dir}/cases.json")
    print(f"档位清单  {len(manifest['bands'])} 条"
          f"{'、%d 对配对' % len(pair_meta) if pair_meta else ''} -> {out_dir}/manifest.json")
    print("规模档    " + "、".join(
        f"{band}:{bands.get(band, 0)}" for band in ("scalar", "small", "medium", "large")))
    thin = [band for band in PERF_BAND_TARGET
            if bands.get(band, 0) < round(number * PERF_BAND_TARGET[band] * 0.5)]
    if thin:
        detail = "、".join(f"{band}:{source.get(band, 0)}" for band in thin)
        print(f"抽不够    {'、'.join(thin)} 档不到配额的一半 —— 全量里只有 {detail}。"
              f"\n          性能结论不覆盖这些档的切分路径。全量里就没有，"
              f"调大 --perf-number 没用。回 S2 按这个顺序试："
              f"先把 max_length 提到 large 门槛的 2 倍，再看 dim_values 最大值。见 case-strategy.md「规模档」。")


def _run_once(command, timeout, produced):
    """跑一次 atk case。返回 0 表示成功且 produced 已就位，非 0 是 main 的退出码。"""
    if produced.exists():
        produced.unlink()          # 多轮时上一轮的结果还在，删掉免得误读成本轮的
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        # **不要在这里打 atk.log。** atk 在用例生成阶段不写它（实测卡死时 0 字节），
        # 打出来的是上一轮的陈旧内容，会把排查带偏。只报事实。
        log = Path("atk.log")
        size = log.stat().st_size if log.exists() else None
        state = "不存在" if size is None else f"{size} 字节"
        print(f"\natk case 超过 {timeout}s 没结束（atk.log {state}；"
              f"生成阶段 atk 不写它，为空是正常的，不说明卡在极早期）。\n"
              f"按这个顺序查：\n"
              f"  1. dim_numbers 有没有 ≥5 的秩。ATK 抽 shape 靠拒绝采样，"
              f"接受率随秩指数下降，高秩会一路重抽到 ATK 自己的 300s 守卫。"
              f"改法见 case-strategy.md「高秩不靠抽样」。\n"
              f"  2. dim_values 最大值 × 秩 是不是远超 max_length 的元素预算。\n"
              f"  3. 约束器里有没有 while / 递归。\n"
              f"确认是哪一条之前不要调大这里的超时。",
              file=sys.stderr)
        return 2

    if result.returncode != 0:
        print(f"\natk case 退出码 {result.returncode}：", file=sys.stderr)
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        for line in tail[-25:]:
            print(f"  {line}", file=sys.stderr)
        return 2

    if not produced.exists():
        print(f"\natk case 退出码 0 但没写出 {produced}。"
              f"检查 YAML 的 name 与 aclnn_name 是否为空。", file=sys.stderr)
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-f", "--yaml", default=None, help="用例设计 YAML，默认自动找")
    parser.add_argument("-p", "--plugin", default=None, help="约束器，默认自动找")
    parser.add_argument("-o", "--out", default="cases.json", help="收敛到哪，默认 cases.json")
    parser.add_argument("--perf-dir", default="perf",
                        help="性能子集落在哪，里面固定写 cases.json")
    parser.add_argument("--perf-number", type=int, default=50,
                        help="性能子集条数，默认 50；"
                             "performance.kind=cross_dtype 时是对数，落盘 2×")
    parser.add_argument("--perf-seed", type=int, default=42)
    parser.add_argument("-dt", "--dtype-numbers", type=int, default=None,
                        help="每个 dtype 生成几条，覆盖 YAML 的 dtype_numbers。"
                             "总量 = dtype 个数 × 这个数。缺省读 YAML；"
                             "--dry-run 固定用 1，本参数不生效")
    parser.add_argument("--dry-run", action="store_true",
                        help="用 dtype_numbers=1 快速验证，不产出 cases.json")
    args = parser.parse_args()

    if args.dtype_numbers is not None and args.dtype_numbers < 1:
        print(f"--dtype-numbers 要是正整数，收到 {args.dtype_numbers}", file=sys.stderr)
        return 3

    yaml_path = _find_yaml(args.yaml)
    if yaml_path is None:
        return 3

    plugin = Path(args.plugin) if args.plugin else _find_plugin(yaml_path)
    if args.plugin and not plugin.exists():
        print(f"{plugin} 不存在", file=sys.stderr)
        return 3

    if not shutil.which("atk"):
        print("atk 不在 PATH。先跑 probe_env.py 确认环境。", file=sys.stderr)
        return 3

    yaml_doc = _yaml_dict(yaml_path)
    label = "dry-run" if args.dry_run else "正式生成"
    if plugin:
        print(f"约束器：{plugin}")
    else:
        print("约束器：无（generate 应为 default）")

    stale = _stale_plugins(yaml_doc)
    if stale:
        print(f"目录里有 YAML 没引用的插件文件：{'、'.join(stale)}", file=sys.stderr)
        print("  重跑不会覆盖它们，留着会跟用例包一起交到跑测侧。", file=sys.stderr)
        print("  上一轮的残留就删掉；本轮要用就接进 YAML 的 generate / api_type。",
              file=sys.stderr)
        return 2

    mismatches = _name_mismatches(yaml_doc, plugin)
    if mismatches:
        print("\n注册名对不上：", file=sys.stderr)
        for line in mismatches:
            print(f"  - {line}", file=sys.stderr)
        print("  YAML 里写的必须与插件文件 @register(\"…\") 括号里的字符串逐字相同。",
              file=sys.stderr)
        return 2

    # 写了 CPU 执行器就查一遍 aclnn 的入参有没有被它悄悄丢掉。放在跑 atk 之前，
    # 丢参数是 S2 的设计缺陷，没必要先花时间生成一批用不了的用例。
    # 没写执行器时这一步什么都不做，见 baseline_params.py 的模块注释。
    facts = {}
    facts_path = Path("facts.json")
    if facts_path.exists():
        try:
            with open(facts_path, encoding="utf-8") as handle:
                facts = json.load(handle)
        except json.JSONDecodeError as exc:
            print(f"facts.json 不是合法 JSON：{exc}。先跑 check_facts.py。",
                  file=sys.stderr)
            return 3
        if not baseline_params.report(facts, yaml_path):
            return 2
    else:
        print("基线适配  facts.json 不在工作目录，跳过入参消费检查")

    if args.dtype_numbers is not None:
        if args.dry_run:
            print(f"每 dtype 条数  --dtype-numbers {args.dtype_numbers} 本轮不生效："
                  f"dry-run 固定用 -dt 1，它只验 YAML 与插件能不能组合出合法用例。")
        else:
            declared = yaml_doc.get("dtype_numbers")
            print(f"每 dtype 条数  {args.dtype_numbers}"
                  f"（--dtype-numbers 覆盖 YAML 的 dtype_numbers: {declared}）")

    focus = list(facts.get("focus_dtypes") or []) if facts else []
    rounds = [] if args.dry_run else _rounds(yaml_doc, focus, args.dtype_numbers)
    if rounds:
        kept = [[d for d in _tensor_dtypes(yaml_doc) if d not in drop] for drop, _ in rounds]
        print(f"dtype 加权  focus_dtypes={'、'.join(focus)}，"
              f"拆两轮跑：{kept[0]} ×{rounds[0][1]} + {kept[1]} ×{rounds[1][1]}")

    timeout = DRY_RUN_TIMEOUT if args.dry_run else FULL_RUN_TIMEOUT
    produced = Path("result") / yaml_path.stem / "json" / f"all_{yaml_path.stem}.json"
    cases = []
    for drop, number in (rounds or [(None, None)]):
        command = ["atk", "case", "-f", str(yaml_path)]
        if plugin:
            command += ["-p", str(plugin)]
        if args.dry_run:
            command += ["-dt", "1", "-en", "0"]
        elif args.dtype_numbers is not None and not drop:
            # 拆了轮次时每轮的 -dt 由 _rounds 按这个数算好了，不能再覆盖一次
            command += ["-dt", str(args.dtype_numbers)]
        if drop:
            # -df 是**滤除**列表（`atk case --help`），所以传的是这一轮不要的那组
            command += ["-df", ",".join(drop), "-dt", str(number)]
        print(f"{label}：{' '.join(command)}")

        code = _run_once(command, timeout, produced)
        if code:
            return code
        with open(produced, encoding="utf-8") as handle:
            batch = json.load(handle)
        if not guard(batch, sys.stderr):
            return 2
        cases += batch

    # 每轮的 id 都从 0 起，合并后必须重编——golden 按 id 存，重号会互相覆盖。
    # golden 是合并之后才冻的，所以重编在这里做是安全的。
    for index, case in enumerate(cases):
        case["id"] = index

    count = len(cases)
    spread = Counter(dtype_of(case) for case in cases)
    print(f"\n用例数    {count}")
    print(f"dtype     {'、'.join(f'{k}×{v}' for k, v in sorted(spread.items()))}")

    if args.dry_run:
        print(f"\ndry-run 通过。回填 dtype_numbers 后去掉 --dry-run 正式生成——"
              f"按 case-strategy.md「用例规模」那张表填即可，\n"
              f"          填了 focus_dtypes 也照表填，拆几轮、每轮 -dt 多少本脚本自己算。\n"
              f"          用户点名了每 dtype 要几条就用 --dtype-numbers N，不必改 YAML。")
        return 0

    if count < MIN_CASES:
        print(f"\n用例数 {count} 少于 {MIN_CASES}。这是 YAML 写窄了，"
              f"回 S2 加 dim_values 取值或 dtype，不要只调大 dtype_numbers。",
              file=sys.stderr)
        return 2

    if count > SOFT_MAX_CASES:
        print(f"\n条数提示  {count} 条超过设计目标 150–400。不拦，接着往下走，"
              f"但先看一眼代价：\n"
              f"          golden 体积按实测 200 条 198 MB 外推约 "
              f"{round(count * GOLDEN_MB_PER_CASE)} MB，"
              f"而 freeze_golden.py 的超时是 600 秒（builtin 基线 1800）。\n"
              f"          覆盖面是 YAML 设计决定的，不是条数决定的——多出来的用例"
              f"多半落在已有的等价类里。\n"
              f"          要提覆盖面，加 dim_values 取值或按 case-strategy.md"
              f"「必须单独构造的场景」补显式构造，比加条数有效。")

    # kind=cross_dtype 时性能子集要成对：先挑，镜像件追加进全量再落盘。
    pairs = []
    if (facts.get("performance") or {}).get("kind") == "cross_dtype":
        pairs = [tuple(pair) for pair in (facts["performance"].get("pairs") or [])
                 if isinstance(pair, list) and len(pair) == 2]
    picked, pair_meta = None, None
    if pairs:
        picked, mirrors, pair_meta = paired_perf_subset(
            cases, args.perf_number, args.perf_seed, pairs)
        cases += mirrors
        print(f"性能配对  {len(mirrors)} 条镜像用例（同 shape 换 dtype）已并入全量，"
              f"id {cases[-len(mirrors)]['id']} 起")

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(cases, handle, ensure_ascii=False)
    print(f"收敛到  {args.out}（{len(cases)} 条）")
    print()
    write_perf_subset(cases, args.perf_dir, args.perf_number, args.perf_seed,
                      picked, pair_meta)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    sys.exit(main())
