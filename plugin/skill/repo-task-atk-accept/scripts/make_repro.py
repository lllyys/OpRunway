#!/usr/bin/env python3
"""生成最小复现包 repro/：一份 env.sh、一份 rerun.sh、一份失败用例。

**它回答的只有一个问题：改完 kernel 之后，那几条挂掉的用例过了没。**
不重现裁决逻辑、不出 verdict.json——结果直接看 ATK 自己产的 xlsx。
拿到包的人不需要装本 skill，也不需要读任何 Python。

atk 命令由 `run_atk.py` 的 `_node_command` 现拼，不是抄一份。抄一份的话
run_atk 改了拓扑、改了 `--slice_input`，复现包会悄悄跑成另一件事。

    python make_repro.py --pkg ../input --verdict ../report/verdict.json -o ../repro
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_atk import (PROFILES, _load_node,  # noqa: E402
                     _node_command, _slice_ratio, _profile)

README = """# {op} 最小复现包

改完 kernel、重装算子包之后，用它把失败用例再跑一遍。**不需要装 skill。**

## 1. 填三个变量

`rerun.sh` 开头三行，默认值是本轮验收的真实取值。改完 kernel 重装之后，
**通常只需要改 `CUSTOM_OPP`**：

| 变量 | 填什么 | 本轮取值 |
| --- | --- | --- |
| `PKG` | 用例包目录，`cases.json`、`golden/`、`facts.json` 都在里面 | `{pkg}` |
| `DEVICE` | 卡号。**一个算子占一张卡**，别和人共用 | `{device}` |
| `CUSTOM_OPP` | 重装后的 `libcust_opapi.so` 全路径。空着就用 `env.sh` 里本轮那份 | 见 `env.sh` |

CPU 标杆执行器（`{plugin}`）已经写死在命令里，跟着 `PKG` 走，不用单独填。

`PKG` 默认指向 `{pkg}`。**把这个目录整包拷到别处时，`PKG` 要改成用例包的新位置**
——复现包本身不带 golden（几百 MB），golden 一直由用例包提供。

## 2. 跑

```bash
{commands}
```

每条跑完在 `atk_output/<任务>_<时间戳>/report/` 下出一份 xlsx，看 `summary` 表的
**「精度是否达标」**列。失败用例的编号在 `failed cases` 与
`accuracy false cases` 两张表里。

## 3. 换算子时要改什么

这个包是给 **{op}** 生成的，换算子不要手改，回跑测 skill 重出一份。真要手改，
下面三处必须同时改，漏一处的表现是「标杆输出为空」而不是报错：

- `PKG` 指向新算子的用例包
- `failed/cases.json` 换成新算子的失败用例
- **文件名必须还叫 `cases.json`**。ATK 拿用例文件的基名当 golden 的子目录名，
  改成 `failed.json` 就会去找 `golden/{baseline_dir}/failed/`，一条都找不到

## 本轮失败用例

{failed_summary}
"""

# 两种剖面的「测的是不是待验收实现」守卫。这一段不能省：不设变量时跑的是
# 别处的同名实现，报告一切正常，通过率甚至好看——是本链路最贵的静默错误。
GUARD_OPP = """CUSTOM_OPP=${CUSTOM_OPP:-}       # 重装后的 libcust_opapi.so，空则用 env.sh 里那份

source "$HERE/env.sh"
[ -n "$CUSTOM_OPP" ] && export ATK_CUSTOM_OPP_PATH="$CUSTOM_OPP"

if [ -z "${ATK_CUSTOM_OPP_PATH:-}" ]; then
  echo "ATK_CUSTOM_OPP_PATH 没设。不设的话测的是 CANN 内置的同名算子," >&2
  echo "不是待验收实现——结果看着正常，其实测错了对象。" >&2
  exit 3
fi
echo "被测实现  $ATK_CUSTOM_OPP_PATH"
"""


GUARD_LDPATH = """LIB_DIR=${LIB_DIR:-}             # 重装后的 lib64 目录，空则用 env.sh 里那份

source "$HERE/env.sh"
[ -n "$LIB_DIR" ] && export LD_LIBRARY_PATH="$LIB_DIR:$LD_LIBRARY_PATH"

# 自足工程装出来是普通共享库，没有 opp vendor 层，所以核的是
# LD_LIBRARY_PATH 里第一段目录存不存在，而不是某个变量设没设。
FIRST_LIB=${LD_LIBRARY_PATH%%:*}
if [ -z "$FIRST_LIB" ] || [ ! -d "$FIRST_LIB" ]; then
  echo "LD_LIBRARY_PATH 第一段不是个存在的目录（$FIRST_LIB）。" >&2
  echo "待验收实现靠它被加载，不设的话跑的是别处的同名实现——" >&2
  echo "结果看着正常，其实测错了对象。改开头的 LIB_DIR 指到装包的 lib64/。" >&2
  exit 3
fi
echo "被测实现  $FIRST_LIB"
"""


KIT_README = """
## 任务方自带的跑测脚本

在 `op-testcase/` 下，分两份：

| 目录 | 是什么 |
| --- | --- |
| `original/` | 任务仓交付的原件，**一个字节没动**。`MANIFEST.md` 记着每个文件本轮的 sha256，与用例包 `facts.json` 的 `adopted_files` 同源——比对它就知道手上这份与验收那一轮是不是同一份 |
| `fixed/` | {fixed_what} |

{run_hint}

它跑的是**任务方自己的口径**：他们的用例、他们手写的 CPU 标杆、他们的比对器。
跑出来的结论是「待测实现与任务方的参考实现一致」，**不是「待测实现正确」**。
两条路线各证一件事，不要互相替代：

| 跑什么 | 证明什么 |
| --- | --- |
| `bash rerun.sh accuracy` | 独立验收：本仓生成的用例，覆盖任务方漏掉的场景 |
| `op-testcase/` 那条 | 开发者达到了任务书写明的交付门槛 |
{kit_fixes}"""


KIT_FIXES_HEAD = """
### 本轮为了让原件跑起来做的改动

**原件一个字节没改**，改动全在 `op-testcase/fixed/` 里，逐条如下。
结论要连着这几行一起读：

"""


NO_CXX = """
## 为什么没有 C++ 半边

A3.5 的独立执行器按 aclnn 两段式接口（`GetWorkspaceSize` + 主函数）拼调用，
本算子不暴露那套接口，拼不出来。所以本包只有 ATK 一条复现路线，
`report/report.md` 里那条「独立执行器」结论也标的是不适用。
"""


RERUN = """#!/bin/bash
# {op} 最小复现。由 make_repro.py 生成，开头几个变量按需改，见 README.md。
set -u

HERE=$(cd "$(dirname "$0")" && pwd)

PKG=${{PKG:-{pkg}}}      # 用例包：cases.json / golden/ / facts.json 都在里面
DEVICE=${{DEVICE:-{device}}}                 # 卡号，一个算子占一张
{guard}
echo "用例包    $PKG"

case "${{1:-}}" in
{branches}
  *)
    echo "用法：bash rerun.sh {usage}" >&2
    exit 2
    ;;
esac
"""

BRANCH = """  {name})
    # {desc}
    {guard}exec {command}
    ;;
"""


# atk 命令按语义分段：每个 node / task 子命令起一行。一个参数一行没法读，
# 全挤一行又看不出拓扑，而这份脚本是给人改的。
SEGMENT_HEADS = ("node", "task")


def _quote(command):
    """把命令列表拼成分段的多行 shell。带 $PKG / $HERE 变量，不能加单引号。"""
    lines, current = [], []
    for part in map(str, command):
        if part in SEGMENT_HEADS and current and current != ["atk"]:
            lines.append(" ".join(current))
            current = []
        current.append(part)
    lines.append(" ".join(current))
    return " \\\n      ".join(lines)


def _branches(plugin, slice_ratio, kind, has_failed, load_node, profile):
    """按本轮实际形态生成子命令。跑不了的形态不生成——给一条注定报错的命令
    比不给更糟，拿到包的人会以为是自己环境坏了。

    `profile` 定被测节点用哪个 ATK 后端，与验收那一轮同一份，
    否则复现包跑的是另一个执行路径而看不出来。"""
    out, names, descs = [], [], []

    def add(name, desc, mode, cases, guard="", slicing=0.0):
        names.append(name)
        descs.append((name, desc))
        command = _node_command(mode, cases, "$PKG/golden", "$DEVICE", plugin,
                                slicing, load_node=load_node, profile=profile)
        out.append(BRANCH.format(name=name, desc=desc, guard=guard,
                                 command=_quote(command)))

    if has_failed:
        add("failed", "只跑本轮未通过的用例（执行失败 + 精度不符），比对冻好的 golden",
            "accuracy", "$HERE/failed/cases.json", slicing=slice_ratio)
    add("accuracy", "全量精度，与验收时的 A3 同一条命令",
        "accuracy", "$PKG/cases.json", slicing=slice_ratio)
    add("perf", "性能子集，采 Device 耗时",
        "performance", "$PKG/perf/cases.json")
    if kind == "builtin" and profile["custom_opp"]:
        add("perf-baseline",
            "摘掉自定义算子包，同一批用例测 CANN 内置实现，作性能基线",
            "performance", "$PKG/perf/cases.json",
            guard="unset ATK_CUSTOM_OPP_PATH ASCEND_CUSTOM_OPP_PATH\n    ")
    return "".join(out), names, descs


# ---- C++ 那半边 ---------------------------------------------------------
#
# **复用 A3.5 已经产出的东西，不重新生成。** `run_cxx.py` 跑完在 `--work` 目录下
# 留了 `runner.cpp`（固定模板）、`op_call.inc`（按本算子签名生成的十行宏）和
# `desc/`（本轮真实喂进去的字节）。这里把前两个拼成一个 .cpp、把 desc/ 原样拷走。
#
# 为什么拼成一个文件：链路内部两个执行器共用一份 `runner.cpp`，所以拆开；
# 复现包是给外人的，两个代码文件只增加理解成本。
#
# 为什么不重新生成 desc/：重新生成要 torch、要重走签名解析，拿到的字节未必与
# 本轮一致。直接拷才证明得了「复现的就是验收那一轮」。

CXX_BUILD = r"""#!/bin/bash
# 编 {op}_repro。三个路径按自己机器填，其余不用动。
set -e
OPDIR=${{OPDIR:?算子目录，含 op_api/aclnn_*.h}}
REPO=${{REPO:-$OPDIR}}          # 算子所在母仓，内部头（aclnn_util.h 等）在里面
VENDOR=${{VENDOR:?算子包 vendors/<name> 目录}}
# set_env.sh 会读一堆没定义的变量，`set -u` 下会当场死掉，所以这里不开 -u。
source "${{CANN_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}}"

HERE=$(cd "$(dirname "$0")" && pwd)
# -lcust_opapi 必须排在 -lopapi 前面：两者导出同名符号，链接器取先出现的那份，
# 顺序反了会静默连到 CANN 内置实现。程序启动打印的 BOUND 行是硬断言，核一眼。
g++ -std=c++17 -O2 "$HERE/{op}_repro.cpp" -o "$HERE/{op}_repro" \
  -I"$ASCEND_TOOLKIT_HOME/include" -I"$OPDIR/op_api" \
  -I"$REPO/common/inc/op_api" -I"$REPO/third_party/opbase/include/nnopbase" \
  -L"$VENDOR/op_api/lib" -lcust_opapi -Wl,-rpath,"$VENDOR/op_api/lib" \
  -L"$ASCEND_TOOLKIT_HOME/lib64" -Wl,-rpath,"$ASCEND_TOOLKIT_HOME/lib64" \
  -lascendcl -lnnopbase -lopapi -ldl
echo "编译完成  $HERE/{op}_repro"
"""

CXX_RUN = r"""#!/bin/bash
# 跑 {op} 的失败用例。用法：./run_cxx.sh <卡号> [用例id...]，不给 id 就全跑。
# 卡号现查：npu-smi info。别和人共用一张卡，别人的 aicore 异常会算成本算子的。
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
VENDOR=${{VENDOR:?算子包 vendors/<name> 目录}}
source "${{CANN_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}}"
export ASCEND_CUSTOM_OPP_PATH="$VENDOR"
export LD_LIBRARY_PATH="$VENDOR/op_api/lib:${{LD_LIBRARY_PATH:-}}"

# 关掉 core dump。这个程序每次退出都被 SIGABRT 杀掉（CANN 在 teardown 里写坏
# host 堆），判定行在那之前已经打印，但内核会为每次退出转储几十 MB。
# 实测开着 3~13 秒一条、偶发到 166 秒，关掉稳定 2 秒。
ulimit -c 0

[ $# -ge 1 ] || {{ echo "用法: ./run_cxx.sh <卡号> [用例id...]" >&2; exit 2; }}
DEV=$1; shift
IDS="$*"
[ -n "$IDS" ] || IDS=$(ls "$HERE/desc" | sed -n 's/^case\([0-9]*\)\.txt$/\1/p' | sort -n)
exec "$HERE/{op}_repro" "$HERE/desc" "$DEV" $IDS
"""

CXX_README = """
## C++ 独立执行器（不依赖 ATK / torch / 本 skill）

```bash
export OPDIR=<算子目录>       # 含 op_api/aclnn_*.h
export REPO=<算子所在母仓>     # 内部头在里面
export VENDOR=<算子包 vendors/<name> 目录>

./build_cxx.sh
./run_cxx.sh <卡号>           # 卡号现查：npu-smi info
```

每条一行 `CASE <id> OK|FAIL <阶段> <返回码>`，开头 `BOUND <so>` 是符号真实落点，
**必须落在 `$VENDOR` 下的 `libcust_opapi.so`**；落到 CANN `lib64` 说明测的是内置
同名实现，结论作废。

**它回答「跑不跑得起来、崩在哪一步、错误码原文」，不判精度。** 精度这样看：

```bash
python3 compare.py --golden <用例包>/golden
```

`compare.py` 用的是 ATK 的同一个比较器，**口径与验收报告一致**，不自己造容差。
它要 torch 与 atk；C++ 那半边保持零 Python 依赖。

**它和 `rerun.sh` 的关键差别是 tiling。** ATK 必须拉起 torch_npu，而 torch_npu 会让
GE 先注册 CANN 内置的 tiling，算子包自带那份被拒（日志里 `MergeFunctions: op type
<T> tiling func has been registered.`）。这个程序不碰 torch_npu，用的是**算子包自带的
tiling**。存在同名内置算子时，两边测的不是同一份实现，结论不一致是正常的。

`desc/` 是 `inputs/` 转成的裸字节 + 文本描述符，与 ATK 那半边**同一批用例、同一批
字节**，打包时转好。描述符是纯文本，改 shape/dtype 做单变量实验直接改它，不碰代码。
"""


CXX_COMPARE = r'''#!/usr/bin/env python3
"""比对 run_cxx.sh 落盘的输出与 golden。**口径与验收报告一致**——用的是 ATK 的
同一个比较器，不自己造容差。

    python3 compare.py --golden <用例包>/golden [--desc desc]

要 torch 与 atk（比较器只吃两个张量，不碰 device、不碰 celery）。
C++ 那半边不做这件事：它不依赖 Python，只回答「跑不跑得起来、崩在哪一步」。
"""
import argparse
import json
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--golden", required=True, help="用例包的 golden 目录")
    ap.add_argument("--desc", default="desc", help="描述符与输出所在目录")
    ap.add_argument("--cases", default="failed/cases.json")
    args = ap.parse_args()

    import torch
    from atk.configs.case_config import CaseConfig
    from atk.tasks.post_process.mixed_tolerance_benchmark_compare import (
        MixedToleranceBenchmarkAccuracyCompare as Comparator)

    golden = Path(args.golden)
    manifest = golden / "manifest.json"
    baseline = (json.loads(manifest.read_text(encoding="utf-8")).get("baseline_dir")
                if manifest.exists() else "cpu_0")
    by_id = {int(c["id"]): c for c in json.loads(Path(args.cases).read_text("utf-8"))}

    desc = Path(args.desc)
    specs = sorted(desc.glob("case*.txt"), key=lambda p: int(p.name[4:-4]))
    if not specs:
        print(f"{desc}/ 下没有 case*.txt", file=sys.stderr)
        return 3

    bad = 0
    for spec in specs:
        case_id = int(spec.name[4:-4])
        outs = []
        for line in spec.read_text(encoding="utf-8").splitlines():
            f = line.split()
            if f and f[0] == "OUT" and f[1] == "tensor":
                outs.append({"dtype": f[2], "shape": [int(x) for x in f[3].split(",")],
                             "stride": [int(x) for x in f[4].split(",")], "file": f[7]})
        case = by_id.get(case_id)
        if case is None:
            print(f"CASE {case_id}  跳过：cases.json 里没有它")
            continue
        comparator = Comparator(CaseConfig(**case))
        for index, out in enumerate(outs):
            blob = desc / out["file"]
            gold_path = golden / baseline / "cases" / str(case_id) / f"output_{index}.pt"
            if not blob.exists() or not gold_path.exists():
                print(f"CASE {case_id}  跳过：缺 "
                      f"{'输出' if not blob.exists() else 'golden'}")
                continue
            gold = torch.load(gold_path, weights_only=False)
            if isinstance(gold, (list, tuple)):
                gold = gold[0]
            dtype = getattr(torch, out["dtype"].replace("torch.", ""))
            flat = torch.frombuffer(bytearray(blob.read_bytes()),
                                    dtype=torch.uint8).view(dtype)
            actual = torch.flatten(torch.as_strided(flat, tuple(out["shape"]),
                                                    tuple(out["stride"])))
            gold = torch.flatten(gold)
            acc = comparator.compute_accuracy_result(actual, gold, f"output_{index}.pt")
            diff = torch.abs(actual.to(torch.float64) - gold.to(torch.float64))
            ok = bool(acc.result)
            bad += 0 if ok else 1
            print(f"CASE {case_id} out{index}  {'PASS' if ok else 'FAIL'}  "
                  f"max_abs={float(diff.max()) if diff.numel() else 0.0:.6g}  "
                  f"{acc.error_info}")
    print(f"\n不通过 {bad} 项")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _build_cxx(outdir, op, cxx_work, wanted_ids):
    """拼出单文件 .cpp、拷失败用例的描述符、写两个脚本。缺东西就跳过并说清原因。

    **只拷 `wanted_ids` 那几条。** A3.5 的 work 目录里通常躺着全量的描述符
    （几十上百 MB），整包拷走既没必要也让复现包没法发。
    """
    work = Path(cxx_work)
    runner, inc, desc = work / "runner.cpp", work / "op_call.inc", work / "desc"
    missing = [str(p) for p in (runner, inc, desc) if not p.exists()]
    if missing:
        print(f"复现包    跳过 C++ 半边，缺 {'、'.join(missing)}"
              f"——A3.5 没跑过，或 --cxx-work 指错了目录")
        return False

    body = runner.read_text(encoding="utf-8")
    marker = '#include "op_call.inc"'
    if marker not in body:
        print(f"复现包    跳过 C++ 半边，{runner} 里没有 {marker}")
        return False
    head = ("/* 由 make_repro.py 生成：固定运行时 + 本算子的调用宏，拼成一个文件。\n"
            " * 编译见 build_cxx.sh，跑见 run_cxx.sh。 */\n")
    (outdir / (op + "_repro.cpp")).write_text(
        head + body.replace(marker, inc.read_text(encoding="utf-8")), encoding="utf-8")

    out_desc = outdir / "desc"
    out_desc.mkdir(exist_ok=True)
    copied, absent = [], []
    for case_id in sorted(wanted_ids):
        spec = desc / f"case{case_id}.txt"
        if not spec.exists():
            absent.append(case_id)
            continue
        shutil.copy2(spec, out_desc / spec.name)
        # 描述符第 8 列是数据文件名，跟着一起拷；缺了 runner 直接报错退出。
        for line in spec.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 8 and parts[1] == "tensor":
                blob = desc / parts[7]
                if blob.exists():
                    shutil.copy2(blob, out_desc / blob.name)
        copied.append(case_id)
    if absent:
        print(f"复现包    C++ 半边缺 {len(absent)} 条的描述符：{absent[:12]}"
              f"——A3.5 那轮没跑到它们（`--ids-from` 取的集合更小）")
    if not copied:
        print("复现包    跳过 C++ 半边，一条描述符都没对上")
        return False
    for name, text in (("build_cxx.sh", CXX_BUILD), ("run_cxx.sh", CXX_RUN)):
        path = outdir / name
        path.write_text(text.format(op=op), encoding="utf-8")
        path.chmod(0o755)
    (outdir / "compare.py").write_text(CXX_COMPARE, encoding="utf-8")
    print(f"复现包    C++ 半边 {len(copied)} 条用例：{copied[:12]}")
    return True


def _copy_tree(src, dest):
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", ".git"))


def _copy_op_testcase(pkg, outdir, facts_data):
    """把任务方自带的跑测件搬进 `op-testcase/`，原件与修复件分开放。

    **无论本轮有没有改过都要搬。** 复现包是交给人手工复测的，
    而任务书要求的交付门槛就是那套脚本 —— 手上没有它，复测的人只能自己去
    任务仓找，找到的未必是验收那一版。

    分两份的原因：原件的 sha256 是「跑的是任务方交付的那一份」的唯一凭据，
    改一个字节这条链就断了。所以修复一律另起一份，`fixed/` 里放的是本轮
    实际跑通的那些脚本，原样从用例包的 `kit_fixes/` 搬来——
    **本函数不构造命令**，跑法由落盘那一方写进 `kit_fixes/run.sh`。
    """
    src = pkg / "kit"
    if not src.is_dir():
        return None
    root = outdir / "op-testcase"
    dest = root / "original"
    _copy_tree(src, dest)

    fixed_src = pkg / "kit_fixes"
    has_fixed = fixed_src.is_dir()
    if has_fixed:
        _copy_tree(fixed_src, root / "fixed")

    # 入口脚本：按内容认，不按文件名认。任务方的命名不统一。
    entries = sorted(p.relative_to(dest) for p in dest.rglob("*.sh")
                     if "atk" in p.read_text(encoding="utf-8", errors="replace"))
    entry = str(entries[0]) if entries else ""

    frozen = facts_data.get("adopted_files") or {}
    lines = ["# 自带件指纹", "",
             "本轮采纳时记下的 sha256。与任务仓那份比对，不同即本轮改过。", "",
             "| 文件 | sha256 |", "| --- | --- |"]
    for name, digest in sorted(frozen.items()):
        lines.append(f"| `{name}` | `{digest}` |")
    if not frozen:
        lines.append("| （用例包的 facts.json 里没有 adopted_files） | — |")
    (dest / "MANIFEST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"entry": entry, "has_fixed": has_fixed,
            "run": (root / "fixed" / "run.sh").exists()}


PERF_README = """

## 性能复现（与精度分开的一份）

`perf/` 里是本轮采性能数用的那套件与它的输入：{files}。

**它和 `bash rerun.sh perf` 不是一回事。** 后者跑的是 ATK 的性能轮，采的是
单次调用的 Device 耗时；任务书要的口径 ATK 表达不了时那一轮整轮不跑，
报告里的倍率全部来自这里的量测件。要复现性能结论就跑这一份：

{run_hint}

采样次数是任务书规定的验收口径，量测件不带默认值，取不到会停下来问。
`--probe-only` 每条只调一次，先看单次耗时与估计总时长，再决定跑不跑完整轮。
"""


def _copy_perf_kit(pkg, stage, outdir):
    """把性能量测件与它的输入搬进 `perf/`。搬不到就返回 None。

    **为什么必须单独有这一份。** `rerun.sh perf` 跑的是 ATK 那轮，而任务书口径
    ATK 表达不了时那轮整轮不跑——性能数全部来自外部量测件。不搬它，拿到复现包的人
    手上就只有精度那一半，性能结论复现不了，而 README 上看不出少了什么。

    与自带件那半边同一个规矩：**本函数不构造命令**，跑法从本轮实际跑过的
    `run.sh` 原样搬；没有就在 README 里写明跑法待补，不猜一条。
    """
    stage = Path(stage)
    root = outdir / "perf"
    picked, notes = [], []
    # 量测件按内容认：外部性能这条路上它可能叫 perf_harness_<op>.py、
    # run_profile_<op>.py，甚至就叫 benchmark.py。按名字认会漏。
    for cand in sorted(pkg.glob("kit_fixes/*.py")) + sorted(stage.parent.glob("*.py")):
        try:
            text = cand.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "under_test_us" in text or "kernel_total_us" in text:
            picked.append(cand)
    # 输入：交换格式、分片用例、基线表。它们是「这批数是怎么来的」的全部凭据。
    for name in ("kit_perf_exchange.json", "performance_external.json"):
        got = stage / name
        if got.is_file():
            picked.append(got)
    if not picked:
        return None
    root.mkdir(parents=True, exist_ok=True)
    for src in picked:
        shutil.copy(src, root / src.name)
        notes.append(src.name)
    for sub in ("p_table_shards", "kit_perf"):
        got = stage / sub
        if got.is_dir():
            _copy_tree(got, root / sub)
            notes.append(f"{sub}/")
    run_sh = pkg / "kit_fixes" / "run_perf.sh"
    if run_sh.is_file():
        shutil.copy(run_sh, root / "run.sh")
        notes.append("run.sh")
    return {"files": notes, "has_run": run_sh.is_file()}


def build(pkg, verdict, outdir, env_sh, facts, cxx_work="cxx"):
    pkg = Path(pkg)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if Path(env_sh).exists():
        shutil.copy(env_sh, outdir / "env.sh")
    else:
        (outdir / "env.sh").write_text(
            "#!/bin/bash\n# A1 的 env.sh 没找到，手工 source CANN 的 set_env.sh "
            "并 export ATK_CUSTOM_OPP_PATH。\n", encoding="utf-8")

    # 装的是执行失败 + 精度不符两类，由 verdict.py 合好放进 repro.case_ids。
    failed_ids = verdict["repro"].get("case_ids") or []
    if failed_ids:
        with open(pkg / "cases.json", encoding="utf-8") as handle:
            allcases = json.load(handle)
        wanted = [case for case in allcases if case["id"] in set(failed_ids)]
        # 目录换、文件名不换：ATK 拿用例文件基名当 golden 的子目录名。
        (outdir / "failed").mkdir(exist_ok=True)
        with open(outdir / "failed" / "cases.json", "w", encoding="utf-8") as handle:
            json.dump(wanted, handle, ensure_ascii=False)
        real = verdict["accuracy"].get("exec_failed_ids") or []
        false = verdict["accuracy"].get("accuracy_false_ids") or []
        summary = (f"{len(wanted)} 条：**执行失败 {len(real)} 条**（跑不起来）、"
                   f"**精度不符 {len(false)} 条**（跑起来了但算错）。\n\n"
                   f"id：{[c['id'] for c in wanted]}\n\n"
                   f"都在 `failed/cases.json` 里，跑 `bash rerun.sh failed` 只测它们。")
    else:
        summary = "本轮没有未通过的用例，所以没有 `failed/` 目录。"

    plugin = next((f"$PKG/{p.name}" for p in sorted(pkg.glob("function_*.py"))), "")
    kind = (json.loads(Path(facts).read_text(encoding="utf-8"))
            .get("performance") or {}).get("kind", "none")
    # 基线节点从**真实的** golden/manifest.json 读，命令里写的却是 $PKG/golden。
    # 拿变量名去读 manifest 会静默退回 cpu_0，内置基线的包就复现错了对象。
    slicing = _slice_ratio(facts)
    # facts 里 backend 认不出时按 aclnn 走：run_atk.py 那边早就退 3 了，
    # 单独跑本脚本时给个能用的包，比崩在这里强。
    profile = _profile(facts) or PROFILES["aclnn"]
    branches, names, descs = _branches(plugin, slicing, kind, bool(failed_ids),
                                       _load_node(pkg / "golden"), profile)

    device = verdict.get("env", {}).get("device", "0")
    # 复现包与用例包的相对位置固定（repro/ 与 input/ 同级），写死相对路径，
    # 整包拷走时 README 让改这一个变量。
    pkg_rel = "$HERE/../input"
    rerun = outdir / "rerun.sh"
    rerun.write_text(RERUN.format(op=verdict["op"], pkg=pkg_rel, device=device,
                                  plugin=plugin, branches=branches,
                                  guard=(GUARD_OPP if profile["custom_opp"]
                                         else GUARD_LDPATH),
                                  usage="|".join(names)), encoding="utf-8")
    rerun.chmod(0o755)

    commands = "\n".join(f"bash rerun.sh {name}    # {desc}" for name, desc in descs)
    (outdir / "README.md").write_text(
        README.format(op=verdict["op"], pkg=pkg_rel, device=device,
                      plugin=plugin or "（用例包里没有，留空）",
                      commands=commands, failed_summary=summary,
                      baseline_dir=verdict["accuracy"].get("baseline_dir", "cpu_0")),
        encoding="utf-8")
    # 自带件那一半：有就搬，改动清单从 facts 的 kit_fixes 读（跑测侧改自带件时写进去）。
    facts_data = json.loads(Path(facts).read_text(encoding="utf-8"))
    kit = _copy_op_testcase(pkg, outdir, facts_data)
    if kit is not None:
        fixes = facts_data.get("kit_fixes") or []
        fix_text = ""
        if fixes:
            fix_text = KIT_FIXES_HEAD + "".join(f"- {line}\n" for line in fixes)
        if kit["run"]:
            run_hint = ("```bash\ncd op-testcase/fixed && bash run.sh\n```\n\n"
                        "`fixed/` 跑得起来，`original/` 在这台机器上跑不起来"
                        "（原因见下面的改动清单）。")
        elif kit["has_fixed"]:
            run_hint = ("`fixed/` 里有本轮用到的修复件但没有 `run.sh`，"
                        "跑法见 `fixed/` 里的说明。")
        elif kit["entry"]:
            run_hint = f"```bash\ncd op-testcase/original && bash {kit['entry']}\n```"
        else:
            run_hint = "原件里没找到 `.sh` 入口，跑法见 `original/README`。"
        fixed_what = ("本轮实际跑通的那一份。原件在这台机器上跑不起来，"
                      "改了哪里见下" if kit["has_fixed"]
                      else "本轮不需要，原件直接跑得起来，所以没有这个目录")
        with open(outdir / "README.md", "a", encoding="utf-8") as handle:
            handle.write(KIT_README.format(fixed_what=fixed_what,
                                           run_hint=run_hint,
                                           kit_fixes=fix_text))

    # 性能那一半：外部量测件采的数，`rerun.sh perf` 那条 ATK 命令复现不了。
    perf_kit = _copy_perf_kit(pkg, Path(facts).parent.parent / "work" / "stage", outdir)
    if perf_kit is None:
        perf_kit = _copy_perf_kit(pkg, Path("stage"), outdir)
    if perf_kit is not None:
        run_hint = ("```bash\ncd perf && bash run.sh\n```"
                    if perf_kit["has_run"] else
                    "本轮的命令没落进 `kit_fixes/run_perf.sh`，跑法待补——"
                    "量测件的 `--help` 里有全部参数。")
        with open(outdir / "README.md", "a", encoding="utf-8") as handle:
            handle.write(PERF_README.format(
                run_hint=run_hint,
                files="、".join(f"`{n}`" for n in perf_kit["files"])))

    if not profile["standalone"]:
        # A3.5 的 C++ 执行器按 aclnn 两段式拼，npu 剖面没有那套接口。
        # 不生成半个跑不起来的目录，改在 README 里写明为什么没有。
        (outdir / "README.md").write_text(
            (outdir / "README.md").read_text(encoding="utf-8") + NO_CXX,
            encoding="utf-8")
    elif failed_ids and _build_cxx(outdir, verdict["op"], cxx_work, failed_ids):
        with open(outdir / "README.md", "a", encoding="utf-8") as handle:
            handle.write(CXX_README)

    if slicing > 0:
        with open(outdir / "README.md", "a", encoding="utf-8") as handle:
            handle.write(
                "\n## 非连续张量\n\n本算子 `facts.non_contiguous.required` 为真，"
                f"精度命令带 `--slice_input non_contiguous --slice_input_ratio {slicing}`，"
                "与验收时一致。连续与非连续在同一轮里按这个比例混跑，哪几条被切由用例 id "
                "定，重跑时不会变。**改掉这个比例测的就不是同一件事**——非连续路径是"
                "任务书要求覆盖的场景，比例调小之后失败用例可能「看起来好了」。\n")
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pkg", default="../input", help="用例包目录")
    parser.add_argument("--verdict", default="../report/verdict.json")
    parser.add_argument("--facts", default="../input/facts.json")
    parser.add_argument("--env-sh", default="evidence/env.sh")
    parser.add_argument("-o", "--outdir", default="../repro")
    parser.add_argument("--cxx-work", default="cxx",
                        help="A3.5 的 --work 目录，从里面取 runner.cpp / op_call.inc / desc/。"
                             "没跑过 A3.5 就没有这些，C++ 半边会跳过并说明原因")
    args = parser.parse_args()

    source = Path(args.verdict)
    if not source.exists():
        print(f"{source} 不存在，先跑 verdict.py。", file=sys.stderr)
        return 3
    with open(source, encoding="utf-8") as handle:
        verdict = json.load(handle)
    names = build(args.pkg, verdict, args.outdir, args.env_sh, args.facts,
                  args.cxx_work)
    print(f"复现包    {args.outdir}（bash rerun.sh {'|'.join(names)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
