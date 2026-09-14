#!/usr/bin/env python3
"""把自带件的精度用例补齐到任务书要求的 dtype 全集。只改用例清单，不碰自带件。

自带件的用例生成脚本把 dtype 写死在模块级常量里，CLI 参数碰不到它——实测一例
生成器只出 fp32 与 complex64 两种，而任务书要求四种。加条数补不上这个洞：
生成器的非 shape 轴按 `local_index % N` 轮转，扩到五倍也只是多采 shape。

**扩 dtype 不需要改任务方的脚本。** attr 编码的用例里 dtype 就是两个字段：
`inputs` 里的一个整数，与 `outputs.<i>.dtype` 的一个字符串。把已有用例复制一份
改这两处，就得到同一批 shape 在新 dtype 上的用例——shape 相同，dtype 间可横向比。

**dtype 与整数的对应关系是自带件特有的，本量具不猜。** 由 `--dtype-map` 给，
值取自自带件执行器里的 dtype 字典，人核一次。拿某个算子的编码去套另一个算子，
是此前推错两轮的那个坑。

精度阈值不用管：ATK 的混合容差按 dtype 自己选门槛
（`atk/configs/mixed_tolerance_benchmark_config.py`），补 dtype 后自动跟着走。

**目标条数默认 1000，不问用户。** dtype 数换了不用重算：每种 dtype 分到
`目标 ÷ 任务书 dtype 数` 条，生成脚本要出的是 `每种条数 × 生成器自己出的 dtype 数`。
条数不够时退 6 并把这个值算给执行者，跑完生成脚本再回来。

退出码：0 扩好了或本来就全覆盖；3 输入读不出；4 dtype 轴推断不出来；
5 `--dtype-map` 里没有要补的 dtype；6 条数不够且没给 `--generator-cmd`；
7 生成脚本跑挂了或没产出同名文件。
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def load_cases(path: Path):
    """读用例清单。裸数组与 `{"cases": [...]}` 两种形态都认。

    返回 `(cases, wrapper)`：`wrapper` 是包在外面的那个 dict，没有就是 None。
    写回时按原形态写，不把任务方的外层字段丢掉。
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict) and isinstance(payload.get("cases"), list):
        return payload["cases"], payload
    raise ValueError("不是用例清单：既不是 JSON 数组，也没有 cases 数组")


def attr_values(case):
    """取用例的 attr 名到取值。`range_values` 只有一个元素的才算 attr 轴。"""
    out = {}
    for item in case.get("inputs") or []:
        if not isinstance(item, dict):
            continue
        values = item.get("range_values")
        if isinstance(values, list) and len(values) == 1:
            out[item.get("name")] = values[0]
    return out


def case_dtype(case, vocab):
    """用例的 dtype：输出里那个**落在 dtype 全集内**的声明。

    不能取「第一个非空的输出 dtype」。SpGeMM 那类算子的输出既有值也有 CSR 索引，
    索引声明成 `int32` 且下标在前，取第一个就把整条用例的 dtype 判成 int32——
    随后 dtype 轴推断不出来，扩不了也说不清为什么。
    """
    outputs = case.get("outputs")
    if not isinstance(outputs, dict):
        return None
    for key in sorted(outputs, key=str):
        spec = outputs[key]
        if isinstance(spec, dict) and canon(spec.get("dtype")) in vocab:
            return canon(spec["dtype"])
    return None


def infer_dtype_attr(cases, dtype_map):
    """推断哪个 attr 是 dtype 轴，**判据落在产物上**：这个 attr 的取值与用例声明的
    输出 dtype 必须一一对应，且每一对都能在 `--dtype-map` 里对上。

    推不出来就退 4 让人给 `--dtype-attr`，不挑一个像的用。挑错时扩出来的用例
    dtype 字段与 attr 不一致，ATK 照跑，报表里看不出来。
    """
    pairs = {}
    for case in cases:
        dtype = case_dtype(case, dtype_map)
        if dtype is None:
            continue
        for name, value in attr_values(case).items():
            pairs.setdefault(name, set()).add((value, dtype))
    hits = []
    for name, seen in pairs.items():
        values = {v for v, _ in seen}
        dtypes = {d for _, d in seen}
        if len(values) < 2 or len(values) != len(seen) or len(dtypes) != len(seen):
            continue
        if all(dtype_map.get(d) == v for v, d in seen):
            hits.append((name, sorted(seen, key=str)))
    return hits


def dtype_counts(cases, vocab):
    counts = {}
    for case in cases:
        dtype = case_dtype(case, vocab)
        counts[dtype] = counts.get(dtype, 0) + 1
    return counts


def rename(name, src, dst):
    """把 name 里的源 dtype 换成目标 dtype。

    任务方的命名不统一，实测见过 ATK 词表名（`fp32`）与 torch 名（`float32`）
    两种，所以两种写法都试；都换不到就追加后缀，保证 name 唯一。
    """
    if not isinstance(name, str):
        return name
    for a, b in ((src, dst), (ALIAS.get(src, src), ALIAS.get(dst, dst))):
        if a and a in name:
            return name.replace(a, b)
    return f"{name}-{dst}"


ALIAS = {"fp16": "float16", "bf16": "bfloat16", "fp32": "float32", "fp64": "float64"}

# torch 名到 ATK 词表名。**两种写法都要认**：用例里的 `outputs.dtype` 用 ATK 词表
# （`fp32`），而 `dtype_map` 是照自带件执行器的 dtype 字典抄的，那里是 torch 名
# （`torch.float32`）。装机 ATK 自己两种都收（`_STANDARD_DTYPE_ALIASES`），
# 只有本量具较真的话，照执行器抄一遍就退 4，而报错说的是「推断不出来」，方向是错的。
CANON = {"float16": "fp16", "bfloat16": "bf16", "float32": "fp32",
         "float64": "fp64", "half": "fp16"}


def canon(name):
    """归一到 ATK 词表名。词表外的（complex64、int32）原样返回。"""
    return CANON.get(name, name) if isinstance(name, str) else name


def expand(cases, dtype_attr, dtype_map, wanted, src_dtype):
    """按 `wanted` 补齐 dtype。已有的 dtype 不动，新增的从最大 id 之后接着排。

    **原用例的 id 一个不改**——验收报告要和任务方的自测报告逐条对照，重排 id
    这件事在报表里没有痕迹，对不上时也查不出是谁动的。
    """
    have = {d for d in dtype_counts(cases, dtype_map) if d}
    missing = [d for d in wanted if d not in have]
    ids = [c.get("id") for c in cases if isinstance(c.get("id"), int)]
    next_id = max(ids) + 1 if ids else 0
    source = [c for c in cases if case_dtype(c, dtype_map) == src_dtype]
    added = []
    for dtype in missing:
        for case in source:
            new = copy.deepcopy(case)
            new["id"] = next_id
            next_id += 1
            new["name"] = rename(new.get("name"), src_dtype, dtype)
            for item in new.get("inputs") or []:
                if isinstance(item, dict) and item.get("name") == dtype_attr:
                    item["range_values"] = [dtype_map[dtype]]
            # 只改声明成源 dtype 的那些输出。SpGeMM 那类算子的 CSR 索引输出声明的是
            # 整数 dtype，跟着改会把结构输出也说成浮点，比对器随即按浮点容差比索引。
            for spec in (new.get("outputs") or {}).values():
                raw = spec.get("dtype") if isinstance(spec, dict) else None
                if canon(raw) == src_dtype:
                    # 源写 `float32` 就写 `float16`，源写 `fp32` 就写 `fp16`。
                    # 混着写时 ATK 两种都认，但同一份用例里两种风格并存，
                    # 下一个读它的人会以为这是两类用例。
                    spec["dtype"] = dtype if raw == canon(raw) else ALIAS.get(dtype, dtype)
            added.append(new)
    return cases + added, missing, added


def run_generator(template, count, want_name):
    """跑自带件自己的用例生成脚本，把条数扩到 `count`，返回它产出的用例清单。

    **传给它的不是目标总条数。** 补 dtype 时条数还会涨，所以这里给的是
    `目标 ÷ 任务书 dtype 数 × 生成器自己出的 dtype 数`。这个倒推是内部量，
    不该让执行者算，更不该让用户看见。

    产物按**与原件同名**认。生成脚本常常一次产好几个算子的用例（实测一例同时产
    addmm 与 spgemm 两份，条数还一样），按条数或时间认都会挑错那一份。
    """
    out_dir = Path(tempfile.mkdtemp(prefix="kit_gen_"))
    command = template.replace("{n}", str(count)).replace("{dir}", str(out_dir))
    print(f"条数不够，先跑生成脚本出 {count} 条")
    done = subprocess.run(command, shell=True, capture_output=True, text=True)
    if done.returncode != 0:
        print(f"[退 7] 生成脚本退 {done.returncode}：{(done.stderr or done.stdout)[-500:]}")
        return None
    hit = out_dir / want_name
    if not hit.exists():
        names = "、".join(sorted(x.name for x in out_dir.iterdir())) or "什么都没产出"
        print(f"[退 7] 生成脚本没产出与原件同名的 {want_name}。{out_dir} 下有：{names}。"
              f"命令模板里的 {{dir}} 要落在生成脚本真正的输出目录上")
        return None
    try:
        grown, wrapper = load_cases(hit)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[退 7] 生成脚本产出的 {hit} 读不出：{exc}")
        return None
    print(f"生成脚本出了 {len(grown)} 条")
    return grown, wrapper


def write(path: Path, cases, wrapper):
    """按原形态写回：裸数组进裸数组，包过一层的把外层字段原样带上。"""
    payload = cases if wrapper is None else {**wrapper, "cases": cases}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", required=True, type=Path,
                        help="自带件的用例清单，或生成器扩过条数的那一份")
    parser.add_argument("--dtype-map", required=True,
                        help='dtype 名到 attr 取值，如 \'{"fp16":0,"fp32":2}\'；'
                             "也可以给 @<文件路径>。值取自自带件执行器的 dtype 字典")
    parser.add_argument("--dtypes", required=True,
                        help="任务书要求的 dtype 全集，逗号分隔，用 ATK 词表名")
    parser.add_argument("--dtype-attr", help="dtype 轴的 attr 名。不给就按取值对应关系推断")
    parser.add_argument("--from-dtype", help="拿哪个 dtype 的用例当复制源。"
                                             "不给就用条数最多的那个")
    parser.add_argument("--generator-cmd",
                        help="自带件用例生成脚本的调用模板，`{n}` 是条数、`{dir}` 是"
                             "输出目录，本命令自己填。条数不够时用它扩，"
                             "不用执行者分两步跑")
    parser.add_argument("--target-total", type=int, default=1000,
                        help="扩完的目标总条数，默认 1000。给 0 表示不管条数，"
                             "只补 dtype")
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        cases, wrapper = load_cases(args.cases)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[退 3] 读不出 {args.cases}：{exc}")
        return 3
    if not cases:
        print(f"[退 3] {args.cases} 里一条用例都没有")
        return 3

    raw = args.dtype_map
    try:
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        dtype_map = json.loads(raw)
        if not isinstance(dtype_map, dict) or not dtype_map:
            raise ValueError("要是非空的 JSON 对象")
        dtype_map = {canon(k): v for k, v in dtype_map.items()}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[退 3] --dtype-map 读不出：{exc}")
        return 3

    wanted = [canon(d.strip()) for d in args.dtypes.split(",") if d.strip()]

    cap = args.target_total // len(wanted) if (args.target_total and wanted) else 0
    goal = cap * len(wanted)
    if cap and goal != args.target_total:
        print(f"目标 {args.target_total} 除不尽 {len(wanted)} 种 dtype，"
              f"按每种 {cap} 条算，实际 {goal} 条")

    if cap and args.generator_cmd and len(cases) < goal:
        have = len({d for d in dtype_counts(cases, dtype_map) if d})
        grown = run_generator(args.generator_cmd, cap * have, args.cases.name)
        if grown is None:
            return 7
        cases, wrapper = grown

    if cap:
        # **上界也要判。** 只判下界的话，生成脚本被直接传了目标条数（而不是倒推值）
        # 时，补完 dtype 就是目标的两倍，而每一步的退出码都是 0。
        kept, seen, dropped = [], {}, 0
        for item in cases:
            dtype = case_dtype(item, dtype_map)
            if dtype and seen.get(dtype, 0) >= cap:
                dropped += 1
                continue
            seen[dtype] = seen.get(dtype, 0) + 1
            kept.append(item)
        if dropped:
            print(f"每种 dtype 留前 {cap} 条，裁掉 {dropped} 条——"
                  f"目标 {goal} ÷ {len(wanted)} 种 dtype = 每种 {cap} 条")
            cases = kept

    before = dtype_counts(cases, dtype_map)
    print(f"用例 {len(cases)} 条（" +
          "、".join(f"{d} {n}" for d, n in sorted(before.items(), key=str)) +
          f"），任务书要求 {'、'.join(wanted)}")

    missing = [d for d in wanted if d not in before]
    if not missing and goal and len(cases) < goal:
        need = cap * len([d for d in before if d])
        print(f"[退 6] dtype 已全覆盖但只有 {len(cases)} 条，目标 {goal} 条。"
              f"给 --generator-cmd 让本命令自己扩条数（它要出 {need} 条）")
        return 6
    if not missing:
        # 原样写一份到 -o，**即使一条都没扩**。这样 A1 的流程不分情况：
        # 跑一次脚本，`facts.kit.cases` 一律指 -o。少了这份副本，「已全覆盖」
        # 那一支就得让执行者改指原件，而分支只在这一句打印里说过。
        write(args.output, cases, wrapper)
        print(f"已经全覆盖，一条未扩。原样写到 {args.output}")
        return 0
    absent = [d for d in missing if d not in dtype_map]
    if absent:
        print(f"[退 5] --dtype-map 里没有 {'、'.join(absent)}，"
              f"补不了。映射取自自带件执行器的 dtype 字典，人核一次再给全")
        return 5

    dtype_attr = args.dtype_attr
    if dtype_attr is None:
        hits = infer_dtype_attr(cases, dtype_map)
        if len(hits) != 1:
            names = "、".join(n for n, _ in hits) or "一个都没有"
            print(f"[退 4] dtype 轴推断不出来：符合「attr 取值与输出 dtype 一一对应"
                  f"且与 --dtype-map 一致」的 attr 有 {names}。用 --dtype-attr 指定")
            return 4
        dtype_attr, evidence = hits[0]
        print(f"dtype 轴推断为 `{dtype_attr}`，依据：" +
              "、".join(f"{v}→{d}" for v, d in evidence))

    src = args.from_dtype
    if src is None:
        src = max((d for d in before if d), key=lambda d: before[d])
    if src not in before:
        print(f"[退 3] --from-dtype {src} 在用例里一条都没有")
        return 3
    print(f"复制源：{src}（{before[src]} 条）")

    projected = len(cases) + len(missing) * before[src]
    if goal and projected < goal:
        # 条数不够时不硬凑。复制出来的是同 shape 同 seed 的重复件，条数涨了信息没涨；
        # 新的 shape 只有自带件自己的生成脚本出得来。
        have = len([d for d in before if d])
        need = cap * have
        print(f"[退 6] 扩完只有 {projected} 条，目标 {goal} 条。"
              f"给 --generator-cmd 让本命令自己扩条数（它要出 {need} 条），"
              f"模板形如 '<python> <生成脚本> <条数参数> {{n}} --output-dir {{dir}}'。"
              f"条数参数名各任务不同，从生成脚本的 --help 读")
        return 6

    merged, missing, added = expand(cases, dtype_attr, dtype_map, wanted, src)
    write(args.output, merged, wrapper)

    after = dtype_counts(merged, dtype_map)
    print(f"补 {'、'.join(missing)}，各 {before[src]} 条，共新增 {len(added)} 条")
    print(f"扩后 {len(merged)} 条：" +
          "、".join(f"{d} {n}" for d, n in sorted(after.items(), key=str)) +
          f" -> {args.output}")
    print("原用例的 id 一个未改，新增的接在最大 id 之后。"
          "**报告里新增这批单独成节**，不与原件合并算通过率。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
