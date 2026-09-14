#!/usr/bin/env python3
"""把算子任务自带的跑测件归一成用例包，替代 S2/S3 的用例设计与生成。

自带件与本仓用例包差的是**契约里那几项**，不是内容：自带件当场算 CPU 标杆，
用例包要冻好的 golden；自带件的插件散在任务目录，用例包按 `function_<op>.py`
找；`facts.json` / `perf/` 自带件里没有。本脚本补齐这几项，冻结仍走 S4 的
`freeze_golden.py`，**两条路在 S4 合流**。

自带件不全时不猜：缺哪一类就打印缺什么、S2 里哪一步补，退 4。
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from case_shape import attr_band, attr_value, is_attr_only  # noqa: E402

# 自带件里四类文件的识别式。**按内容认，不按文件名认**：任务方的命名不统一，
# 见过 accuracy_cases.json / cases.json / *_cases.json 三种。
ADAPT_MARK = "api_type"          # 适配脚本会把 api_type 改写成执行器注册名
EXECUTOR_MARK = "BaseApi"        # 执行器插件继承它
COMPARE_MARK = "ACCURACY_REGISTRY"   # 比对器插件往它注册


def _read(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _scan(kit):
    """扫自带件，返回 (清单, 缺件说明)。清单每项是路径或 None。"""
    pys = [p for p in sorted(kit.rglob("*.py")) if "__pycache__" not in p.parts]
    jsons = sorted(kit.rglob("*.json"))

    found = {
        "cases": None,        # 用例清单
        "adapt": None,        # 用例适配脚本，可无
        "executor": None,     # 执行器插件（待测钩子 + CPU 标杆）
        "compare": None,      # 精度比对器，可无（用 ATK 内置阈值时）
    }
    for path in jsons:
        if ".atk_generated" in path.parts:
            continue
        try:
            data = json.loads(_read(path))
        except ValueError:
            continue
        if isinstance(data, list) and data and isinstance(data[0], dict) \
                and "inputs" in data[0] and "id" in data[0]:
            if found["cases"] is None or len(data) > len(
                    json.loads(_read(found["cases"]))):
                found["cases"] = path
    for path in pys:
        text = _read(path)
        if found["adapt"] is None and ADAPT_MARK in text and "def adapt" in text:
            found["adapt"] = path
        if found["executor"] is None and EXECUTOR_MARK in text and "@register" in text:
            found["executor"] = path
        if found["compare"] is None and COMPARE_MARK in text:
            found["compare"] = path

    missing = []
    if found["cases"] is None:
        missing.append("用例清单（一个 JSON 数组，每项含 id 与 inputs）"
                       "—— 自带件里没有就走 S2/S3 自己设计生成")
    if found["executor"] is None:
        missing.append("执行器插件（继承 BaseApi 并 @register 的类）"
                       "—— 没有它 ATK 不知道怎么把算子调起来，"
                       "照 plugin-authoring.md「npu 剖面的待测钩子」写一份")
    return found, missing


def _cases(found, kit, out, python):
    """产出 out/cases.json。有适配脚本就跑它取产物，没有就直接拷。

    **不自己实现适配逻辑。** 适配脚本改的是 api_type 与 standard 的形状，
    那是任务方对自己用例的解释，重写一遍等于替它做主。
    """
    if found["adapt"] is None:
        shutil.copy(found["cases"], out / "cases.json")
        print(f"用例      直接拷（没有适配脚本）{found['cases'].name} -> cases.json")
        return 0
    before = {p: p.stat().st_mtime for p in kit.rglob("*.json")}
    code = subprocess.run([python, str(found["adapt"])], cwd=found["adapt"].parent,
                          capture_output=True, text=True, check=False)
    if code.returncode != 0:
        print(f"\n{found['adapt'].name} 退出码 {code.returncode}：", file=sys.stderr)
        print((code.stderr or code.stdout).strip()[-1500:], file=sys.stderr)
        return 2
    fresh = [p for p in kit.rglob("*.json")
             if p not in before or p.stat().st_mtime > before[p]]
    if not fresh:
        print(f"\n{found['adapt'].name} 跑完了但没写出新的 JSON。"
              f"它的输出路径与本脚本的假定不同，手工跑一遍看它写到哪，"
              f"再用 --cases 直接指过来。", file=sys.stderr)
        return 2
    picked = max(fresh, key=lambda p: p.stat().st_size)
    shutil.copy(picked, out / "cases.json")
    print(f"用例      {found['adapt'].name} -> {picked.name} -> cases.json")
    return 0


PLUGIN_TEMPLATE = '''"""{op} 的执行器与比对器，由 adopt_kit.py 从任务自带件生成。

**不要改这个文件。** 它只做两件事：把自带件目录挂进 `sys.path`，再 import
那几个模块触发它们的 `@register`。算子语义、CPU 标杆、比对判据全在
`kit/` 底下的原件里，改这里等于让用例包和自带件说两套话。

文件名是 `function_*.py`，跑测侧按这个式子在用例包根下找（用例包契约第 5 项）。
"""

import sys
from pathlib import Path

# 自带件整棵树原样搬进 kit/，相对结构不变——原件里有
# `sys.path.insert(0, parents[1] / "common")` 这类相对引用，拆平就断了。
_KIT = Path(__file__).resolve().parent / "kit"
for _sub in ({modules}):
    _path = str((_KIT / _sub).parent)
    if _path not in sys.path:
        sys.path.insert(0, _path)

{imports}
'''


def _plugins(found, kit, out, op):
    """把自带件整棵树搬进 out/kit/，再生成 out/function_<op>.py 做入口。"""
    dest = out / "kit"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(kit, dest, ignore=shutil.ignore_patterns(
        "__pycache__", ".atk_generated", "*.pyc", ".git"))
    mods, imports = [], []
    for key in ("executor", "compare"):
        path = found.get(key)
        if path is None:
            continue
        rel = path.relative_to(kit)
        mods.append(str(rel))
        imports.append(f"import {rel.stem}  # noqa: F401 - "
                       f"import 即注册（{key}）")
    text = PLUGIN_TEMPLATE.format(
        op=op,
        modules=", ".join(repr(m) for m in mods) + ("," if len(mods) == 1 else ""),
        imports="\n".join(imports))
    (out / f"function_{op}.py").write_text(text, encoding="utf-8")
    print(f"插件      kit/ 整棵树 + function_{op}.py（import {len(mods)} 个模块触发注册）")


def _registered_name(path):
    """执行器插件的 @register 注册名，用例的 api_type 要与它一致。"""
    hits = re.findall(r'@register\(\s*["\']([^"\']+)["\']', _read(path))
    return hits[0] if hits else ""


def _perf(cases, out, band_attr, limit):
    """写 perf/cases.json 与 perf/manifest.json。

    条数不超过 `limit` 时全量进性能轮——纯 attr 用例一条几十毫秒，
    抽样省不下多少，而抽样引入的是「哪些没测」这个额外问题。

    档位按 `band_attr` 在**本批用例里的四分位**切，不写死门槛：
    张量算子的字节门槛（case_shape 的 SMALL_BYTES）在没有张量的用例上没有意义。
    """
    perf_dir = out / "perf"
    perf_dir.mkdir(parents=True, exist_ok=True)
    picked = cases if len(cases) <= limit else cases[::max(1, len(cases) // limit)][:limit]
    # 文件名固定 cases.json：ATK 拿用例文件基名当 golden 子目录名，改名找不到 golden。
    with open(perf_dir / "cases.json", "w", encoding="utf-8") as handle:
        json.dump(picked, handle, ensure_ascii=False)
    print(f"性能子集  {len(picked)}/{len(cases)} 条 -> perf/cases.json"
          + ("（全量）" if len(picked) == len(cases) else "（等距抽样）"))

    if not band_attr:
        print("档位      跳过：没给 --band-attr。跑测侧会退回按 dtype 分组，"
              "报告里写明做不了分规模档的对比")
        return
    values = sorted(v for v in (attr_value(c, band_attr) for c in picked)
                    if isinstance(v, (int, float)))
    if len(values) < 4:
        print(f"档位      跳过：{band_attr} 在子集里只取到 {len(values)} 个数值，"
              f"切不出四分位", file=sys.stderr)
        return
    cuts = (values[len(values) // 4], values[len(values) * 3 // 4])
    manifest = {"bands": {str(c["id"]): attr_band(c, band_attr, cuts) for c in picked},
                "band_attr": band_attr, "band_cuts": list(cuts)}
    with open(perf_dir / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    counts = Counter(manifest["bands"].values())
    print(f"档位      按 {band_attr} 四分位切 {cuts}："
          + "、".join(f"{k} {v}" for k, v in sorted(counts.items())))


def _fingerprints(found, kit):
    """自带件里被采纳那几个文件的 sha256。

    **golden 是自带件的手写实现算出来的**，那份实现换一个字节，标杆就换了一批。
    不记指纹时验收结论回溯不到它是拿哪一版算的——自带件在任务仓里，
    本仓管不到它的版本。
    """
    out = {}
    for key, path in found.items():
        if path is None:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        out[str(path.relative_to(kit))] = digest
    return out


def _facts(args, out, cases, api_type):
    """写 facts.json。**backend 在这里定死**，跑测侧只读不重判。"""
    path = out / "facts.json"
    if path.exists() and not args.overwrite_facts:
        print(f"事实表    {path} 已存在，保留（要覆盖加 --overwrite-facts）")
        return
    facts = {
        "op": args.op,
        "aclnn_name": args.op,
        "backend": args.backend,
        "backend_source": "adopt_kit：按 --backend 记录，由 S1 判定",
        "baseline": f"{api_type}（自带件的 CPU 标杆）" if api_type else "自带件的 CPU 标杆",
        "baseline_source": "taskdoc",
        "adopted_from": str(Path(args.kit).resolve()),
        "adopted_files": args.fingerprints,
        "accuracy": {"kind": "torch", "acc": "自带件的比对器"},
        "performance": {"kind": args.perf_kind},
        "non_contiguous": {"required": False,
                           "source": "adopt_kit：纯 attr 用例，张量由执行器现造，"
                                     "ATK 的 --slice_input 切不到"},
        "params": [],
        "case_count": len(cases),
    }
    if args.group_attr:
        facts["group_attr"] = args.group_attr
        facts["group_labels"] = [x for x in args.group_labels.split(",") if x.strip()]
    if args.perf_kind == "threshold":
        facts["performance"]["criterion"] = args.perf_criterion
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(facts, handle, ensure_ascii=False, indent=2)
    print(f"事实表    backend={args.backend}、performance.kind={args.perf_kind}、"
          f"{len(args.fingerprints)} 个自带件指纹 -> facts.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", required=True,
                        help="任务自带的跑测件目录，整棵树会被搬进用例包的 kit/")
    parser.add_argument("--op", required=True,
                        help="算子名。跑测侧的 --op 要与它逐字相同")
    parser.add_argument("-o", "--out", required=True,
                        help="用例包目录 <输出目录>/<op>/，不存在会建")
    parser.add_argument("--backend", default="npu", choices=("npu", "aclnn"),
                        help="执行剖面，写进 facts.json 供跑测侧读。默认 npu")
    parser.add_argument("--perf-kind", default="threshold",
                        choices=("none", "builtin", "cross_dtype", "threshold"),
                        help="性能形态，取值含义见 interface-facts.md。默认 threshold")
    parser.add_argument("--perf-criterion", default="",
                        help="perf-kind=threshold 时任务书里那句要求的原话")
    parser.add_argument("--group-attr", default="",
                        help="精度表按哪个 attr 参数分组（稀疏算子通常是 format_id）。"
                             "不给就退回按 dtype 分，而纯 attr 用例取不到 dtype，"
                             "整张表会塌成一行")
    parser.add_argument("--group-labels", default="",
                        help="--group-attr 的取值到名字，逗号分隔按下标对应，"
                             "如 csr,csc,coo,blocked_ell。取值含义在任务书里")
    parser.add_argument("--band-attr", default="",
                        help="规模轴是哪个 attr 参数（稀疏算子通常是 nnz）。"
                             "不给就不出分规模档的对比")
    parser.add_argument("--perf-limit", type=int, default=200,
                        help="性能子集条数上限，超过就等距抽样。默认 200")
    parser.add_argument("--cases", default="",
                        help="直接指定用例清单，跳过自动识别")
    parser.add_argument("--python", default=sys.executable,
                        help="跑自带适配脚本的解释器")
    parser.add_argument("--overwrite-facts", action="store_true",
                        help="facts.json 已存在时覆盖它")
    args = parser.parse_args()

    kit = Path(args.kit).resolve()
    if not kit.is_dir():
        print(f"{kit} 不是目录。", file=sys.stderr)
        return 3
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    found, missing = _scan(kit)
    if args.cases:
        found["cases"] = Path(args.cases).resolve()
        missing = [m for m in missing if not m.startswith("用例清单")]
    print(f"自带件    {kit}")
    for key, label in (("cases", "用例清单"), ("adapt", "用例适配脚本"),
                       ("executor", "执行器插件"), ("compare", "精度比对器")):
        path = found[key]
        print(f"  {label:<8}{path.relative_to(kit) if path else '（没有）'}")
    if missing:
        print("\n自带件不全，缺的这几类没法猜：", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        print("\n补齐后重跑；或者整条走 S2/S3 自己设计用例，"
              "见 SKILL.md「用例从哪来」。", file=sys.stderr)
        return 4

    code = _cases(found, kit, out, args.python)
    if code:
        return code
    with open(out / "cases.json", encoding="utf-8") as handle:
        cases = json.load(handle)

    api_type = _registered_name(found["executor"])
    used = {c.get("api_type") for c in cases}
    if api_type and api_type not in used:
        print(f"\n用例的 api_type 是 {sorted(used)}，执行器注册的是 {api_type!r}，"
              f"对不上。", file=sys.stderr)
        print("ATK 按 api_type 找执行器，对不上时静默走内置路径，"
              "测的不是自带件那个钩子。先跑自带件的适配脚本，或用 --cases "
              "指到它的产物。", file=sys.stderr)
        return 4

    attr_only = sum(1 for c in cases if is_attr_only(c))
    print(f"用例数    {len(cases)}（纯 attr {attr_only} 条）")

    args.fingerprints = _fingerprints(found, kit)
    _plugins(found, kit, out, args.op)
    _perf(cases, out, args.band_attr, args.perf_limit)
    _facts(args, out, cases, api_type)

    print(f"\n归一完成  {out}")
    print(f"下一步    S4 冻结 golden，两条路在这里合流：")
    print(f"  cd {out} && python3 <skill>/scripts/freeze_golden.py \\")
    print(f"      -c cases.json -p function_{args.op}.py --facts facts.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
