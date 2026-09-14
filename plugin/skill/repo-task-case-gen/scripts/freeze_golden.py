#!/usr/bin/env python3
"""跑标杆并把输出冻结成 golden/，供跑测侧 accuracy_load 离线比对。

标杆是谁由 facts.json 的 accuracy.kind 决定：
  torch    （默认）只起 cpu 一个节点，标杆是 YAML name 指的 torch 接口
  builtin  起 pyaclnn + cpu 两个节点，标杆是 CANN 装机目录里的内置同名 aclnn 接口。
           cpu 节点只为给出输出的 shape/dtype，它的值不参与比对（随机类算子的
           CPU 与 NPU 随机流本来就不同）。需要 NPU 与 CANN。

多输出算子额外查一遍输出顺序（见 `_check_multi_outputs`）：golden 全绿只证明
标杆**能跑**，不证明**算对**，而输出摊反是唯一一类能被静态查出来的算错。

产出 golden/<backend>/<save_name>/<id>/ 的目录树与 golden/manifest.json。
退出码 0 通过，2 标杆执行失败、产出为空或输出顺序对不上，3 输入缺失。
"""

import argparse
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from case_shape import (  # noqa: E402
    UnknownInputShape, dtype_of, guard, rank_of, size_band,
)


# 生成侧只要 CPU 版 torch。装了 torch_npu 的机器上 `import torch` 会去自动加载
# 它，没 source CANN 时抛 RuntimeError，连带 atk 也起不来。关掉自动加载，
# 让「生成侧不需要 NPU 与 CANN」这句话成立。必须在任何 torch 导入之前设。
os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")
# 超时是**挂死探测器**，不是耗时预算——实测两个满规模用例包都在 35s 以内，
# 而且耗时由用例条数与进程启动决定，不随张量体积走：
#
#   用例包                    用例数  输入元素量  实测
#   IndexFillTensor            252    79M        35s
#   UpsampleNearestExact1d     211   176M        33s
#
# 取 180s（实测 5 倍余量）。**调大它之前先确认是真慢还是挂死**：ATK 挂死的样子是
# 用例全算完、`report/` 下只有 `resume_data`、主进程停在 `do_wait` 等一个不退的
# ResultProcess——这种情况多等多久都不会好，只会白烧时间。
TIMEOUT = 180
# builtin 基线在 NPU 上跑，比 CPU 标杆慢一个量级，单独一档。**这一档没实测过**，
# 有 kind=builtin 的算子跑过之后把实测值补进上表，再照 5 倍余量重切。
TIMEOUT_BUILTIN = 1800

# ATK dtype -> output_info.json 里的 torch dtype 名。这张表与 check_facts.py
# 里那份用途不同（那份用来造探针张量），两处各自维护。
# **认输入形态的代码不在此列**——它只有 case_shape.py 一份，见那个文件的开头。
ATK_TO_TORCH = {
    "fp64": "torch.float64", "fp32": "torch.float32", "fp16": "torch.float16",
    "bf16": "torch.bfloat16", "int64": "torch.int64", "int32": "torch.int32",
    "int16": "torch.int16", "int8": "torch.int8", "uint8": "torch.uint8",
    "bool": "torch.bool", "complex64": "torch.complex64",
}


def _case_input_dtypes(cases):
    """用例 id -> 第一个输入张量的 ATK dtype，用来解 same_as_input。

    输入形态由 case_shape 统一识别，这里不再自己判 dict/list——那份判断
    在三个脚本里各写一份时，张量列表算子直接被判成「没有张量输入」。
    """
    return {str(case.get("id")): dtype_of(case) for case in cases}


def _check_multi_outputs(out_dir, facts, cases):
    """查多输出算子的输出顺序有没有摊反。返回错误行列表，空表示通过。

    摊反了 golden 照样全绿——它跑得出来，只是 output_0 装的是本该在 output_1
    的那个张量。到 NPU 侧才全部比对失败，而报告会把它归成算子缺陷，
    结论完全反向。dtype 不同的多输出算子一比就抓到。
    """
    declared = (facts.get("output") or {}).get("outputs") or []
    if len(declared) < 2:
        return []
    by_id = _case_input_dtypes(cases)
    problems, checked = [], 0
    for info_path in sorted(Path(out_dir).rglob("output_info.json")):
        case_id = info_path.parent.name
        try:
            with open(info_path, encoding="utf-8") as handle:
                actual = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        checked += 1
        if len(actual) != len(declared):
            problems.append(f"用例 {case_id}：golden 有 {len(actual)} 个输出，"
                            f"facts.json 声明 {len(declared)} 个")
            continue
        for index, (want, got) in enumerate(zip(declared, actual)):
            dtype = want.get("dtype")
            if dtype == "same_as_input":
                dtype = by_id.get(case_id)
                if dtype is None:
                    continue
            expect = ATK_TO_TORCH.get(dtype)
            if expect is None or got.get("dtype") == expect:
                continue
            problems.append(f"用例 {case_id} 的 output_{index}（{want.get('name')}）："
                            f"golden 是 {got.get('dtype')}，声明是 {expect}")
        if len(problems) >= 5:
            break
    if not problems and checked:
        print(f"输出顺序  查了 {checked} 条，{len(declared)} 个输出的 dtype 都对得上")
    return problems


def _acc_kind(facts_path):
    """精度基线是谁。读不到 facts.json 就按默认的 torch 走。"""
    try:
        with open(facts_path, encoding="utf-8") as handle:
            return (json.load(handle).get("accuracy") or {}).get("kind", "torch")
    except (OSError, ValueError):
        return "torch"


def _find_builtin_symbol(aclnn_name):
    """在 CANN 装机目录里找内置基线接口的符号，返回 (命中的 so 列表, 搜过的 glob)。

    找不到时**如实报告，不替用户改基线**——任务书指定了对标内置实现，
    换基线是用户的决定。"""
    opp = os.environ.get("ASCEND_OPP_PATH")
    if not opp:
        return None, None
    pattern = str(Path(opp).parent / "lib64" / "libopapi*.so")
    symbol = f"aclnn{aclnn_name}GetWorkspaceSize"
    found = []
    for so in sorted(glob.glob(pattern)):
        result = subprocess.run(["nm", "-D", so], capture_output=True,
                                text=True, check=False)
        if symbol in (result.stdout or ""):
            found.append(so)
    return found, pattern


def _leaf_infos(data):
    """递归取出所有 {dtype, shape, stride} 叶子，与嵌套层数无关。"""
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        found = []
        for item in data:
            found.extend(_leaf_infos(item))
        return found
    return []


def _align_output_info(baseline_dir, shape_dir):
    """把 cpu 节点那份 output_info.json 覆盖到基线目录，返回 (覆盖数, 不一致清单)。

    为什么是覆盖而不是改结构：aclnn 的 out 是第一段接口的入参，冻结时 pyaclnn 节点
    正是拿 cpu 节点的 output_info 去申请 out 的（`pyaclnn_backend.py:278` 拿不到就抛
    「标杆输出为空」）。所以 cpu 那份就是**决定了 out 尺寸的那一份**，不是第二来源。

    两份描述的是同一个张量，只是序列化层数不同：cpu 执行器返回裸 tensor 存成
    `[{...}]`，pyaclnn 的 after_call 返回 list，`get_output_data_infos`
    （`atk/common/utils.py:246`）对 list 递归再整个 append，存成 `[[{...}]]`。
    跑测侧 accuracy_load 读到多的那层会拿 list 当输出描述，NPU 侧建出错误的 out，
    执行时 worker 段错误——**ATK 无超时兜底，只会永远停在 0/N**。

    覆盖前按叶子逐项比一遍：不一致说明内置实现的实际输出与申请时的尺寸不符，
    那是该被看见的真问题，停下来，不覆盖。
    """
    aligned, problems = 0, []
    for path in sorted(Path(baseline_dir).rglob("output_info.json")):
        source = Path(shape_dir) / path.relative_to(baseline_dir)
        if not source.is_file():
            problems.append(f"{path.parent.name}：{shape_dir} 下没有对应的 output_info.json")
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                mine = _leaf_infos(json.load(handle))
            with open(source, encoding="utf-8") as handle:
                theirs = _leaf_infos(json.load(handle))
        except (OSError, ValueError) as exc:
            problems.append(f"{path.parent.name}：读不出来（{exc}）")
            continue
        if mine != theirs:
            problems.append(f"用例 {path.parent.name}：基线输出是 {mine}，"
                            f"申请 out 时用的是 {theirs}")
            continue
        shutil.copyfile(source, path)
        aligned += 1
        if len(problems) >= 5:
            break
    return aligned, problems


def _baseline_dir(out_dir, kind):
    """golden 里哪个子目录是基线。目录名是节点的 f"{backend}_{name}"。"""
    want = "pyaclnn" if kind == "builtin" else "cpu"
    hit = sorted(p.name for p in Path(out_dir).iterdir()
                 if p.is_dir() and p.name.startswith(want))
    return hit[0] if hit else None


def _kill_group(expired):
    """把超时的 atk 连同它整个进程组收干净。

    `subprocess.run` 的超时处理只 kill 直接子进程，孙子进程照活。ATK 的拓扑是
    一个 atk 主进程 + 若干 celery worker + 一个绑 127.0.0.1:9090 的节点进程，
    漏掉后两类的后果不是脏一点，而是**下一次跑测会静默连到这个残留节点上**。
    """
    pid = getattr(expired, "pid", None)
    if pid is None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(2)


def _newest_task(base):
    """atk 把结果写在 <base>/atk_output/<任务名>_<时间戳>/，返回这一层。

    input 与 output 必须取**同一个**任务目录：分别取「最新的」会在重跑时配错，
    拿上一轮的输入配这一轮的 golden，而且不报错。
    """
    root = Path(base) / "atk_output"
    if not root.is_dir():
        return None
    tasks = [p for p in root.iterdir() if p.is_dir() and (p / "output").is_dir()]
    if not tasks:
        return None
    return max(tasks, key=lambda p: p.stat().st_mtime)


def _freeze_inputs(task_dir, dest):
    """把这一轮的输入张量搬成 `<dest>/<用例 id>/input.bin`，返回冻下来的条数。

    **为什么要冻输入。** ATK 不冻的话，跑测时按 `torch.manual_seed(case_id)` 现生成
    （`atk/tasks/dataset/base_dataset.py`）。那要求两侧的 atk 版本行为完全一致，
    一旦上游改了生成逻辑，golden 还是老的、输入变成新的，**比对全错且无痕**。
    冻下来之后跑测侧读文件，ATK 走 `--input_data`，自建执行器读同一份，
    「两侧输入是不是一样」这个问题就不存在了。

    布局摊平成 `<id>/input.bin`，因为 `atk task --input_data` 只认这一种
    （`atk/tasks/executors/dataset_executor.py` 的 `get_input_file_path`）；
    ATK 自己存盘时会多一层 save_name（`input/cases/<id>/input.bin`）。
    """
    src = Path(task_dir) / "input"
    if not src.is_dir():
        return 0
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    frozen = 0
    for case_dir in sorted(src.rglob("*")):
        if not case_dir.is_dir() or not case_dir.name.isdigit():
            continue
        files = [f for f in sorted(case_dir.iterdir()) if f.is_file()]
        if not files:
            continue
        target = dest / case_dir.name
        target.mkdir(parents=True, exist_ok=True)
        for item in files:
            shutil.copy2(item, target / item.name)
        frozen += 1
    return frozen


def _count_leaves(output_dir):
    """golden 的叶子是 <backend>/<save_name>/<id>/，数它有多少个。"""
    leaves = []
    for backend in sorted(p for p in output_dir.iterdir() if p.is_dir()):
        for save_name in sorted(p for p in backend.iterdir() if p.is_dir()):
            for case_id in sorted(p for p in save_name.iterdir() if p.is_dir()):
                if any(case_id.iterdir()):
                    leaves.append(f"{backend.name}/{save_name.name}/{case_id.name}")
    return leaves


def _missing_report(cases, leaves):
    """哪些用例没冻出 golden，以及它们的共同特征。返回要打印的行。

    只报「有几条跑不出来」等于让人自己去 diff 两百个目录名。修法总是回 S2 改
    YAML 或约束器，而改哪一处取决于失败的那批共享什么——dtype、秩、还是规模档。
    这里把这三样一起算出来。
    """
    done = {leaf.rsplit("/", 1)[-1] for leaf in leaves}
    missing = [case for case in cases if str(case.get("id")) not in done]
    if not missing:
        return []
    ids = [str(case.get("id")) for case in missing]
    shown = "、".join(ids[:20]) + ("…" if len(ids) > 20 else "")
    lines = [f"跑不出来的用例 id：{shown}"]
    try:
        facets = [("dtype", [dtype_of(c) for c in missing]),
                  ("秩", [str(rank_of(c)) for c in missing]),
                  ("规模档", [size_band(c) for c in missing])]
        total = {"dtype": Counter(dtype_of(c) for c in cases),
                 "秩": Counter(str(rank_of(c)) for c in cases),
                 "规模档": Counter(size_band(c) for c in cases)}
    except UnknownInputShape:
        # 这里是失败路径上的锦上添花，认不出结构就只报 id，不要盖掉上面
        # 那条真正要传达的「有几条跑不出来」。
        return lines
    for label, values in facets:
        counts = Counter(values)
        # 分母是全量里同一取值的条数：「fp16 5/5」是这个 dtype 整个塌了，
        # 「fp16 5/60」只是零星几条，两者要改的地方不一样。
        body = "、".join(f"{k} {n}/{total[label][k]}"
                         for k, n in counts.most_common())
        lines.append(f"  按{label}分：{body}")
    return lines


# S4 收尾归位。左边是生成侧专用、跑测侧一行都不读的文件，右边是 atk 在 CWD 留的
# 副产物。`function_<op>.py` 不在表里——跑测侧 `make_repro` 要在包根 glob 它。
# `nodes.yaml` 也不在——那是 atk 的节点配置，不是我们写的用例设计。
GEN_ONLY = ("*.yaml", "*.yml", "*_constraint.py", "env.gen.json", "atk.log")
SWEEP = ("result", "__pycache__")


def _tidy_package(root):
    """把生成侧专用的文件归进 gen/，扫掉 atk 的 CWD 副产物。返回要打印的行。

    **只在冻结成功后调。** 失败路径上的提示都写着「查 atk.log」，归位之后
    那句话就指错地方了；失败的包也不是交付物，现场留在原地才好排错。

    为什么要归位：交付给跑测侧的只有 `cases.json` / `facts.json` / `perf/` /
    `golden/` / `inputs/` / `function_<op>.py` 六样，其余的跑测侧一行都不读，
    却会被 A1 的 `cp -r` 原样搬进 `input/`。其中 `result/` 下是 `atk case` 写的
    xlsx，**长得就是一份验收报告**，翻到的人会拿它当结论。
    """
    root = Path(root)
    lines = []
    moved = []
    gen_dir = root / "gen"
    for pattern in GEN_ONLY:
        for item in sorted(root.glob(pattern)):
            if not item.is_file() or item.name == "nodes.yaml":
                continue
            gen_dir.mkdir(exist_ok=True)
            shutil.move(str(item), str(gen_dir / item.name))
            moved.append(item.name)
    swept = []
    for name in SWEEP:
        target = root / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            swept.append(name + "/")
    if moved:
        lines.append(f"归位      {'、'.join(moved)} → gen/（跑测侧不读，"
                     f"重跑 S3 时 gen_cases.py 照样找得到）")
    if swept:
        lines.append(f"扫除      {'、'.join(swept)}（atk 的 CWD 副产物，无人读）")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--cases", default="cases.json")
    parser.add_argument("-o", "--out", default="golden", help="冻结到哪，默认 golden/")
    parser.add_argument("--inputs", default="inputs",
                        help="输入张量冻到哪，默认 inputs/。跑测侧 ATK 走 "
                             "--input_data 读它，自建执行器读同一份")
    parser.add_argument("-p", "--plugin", default=None, help="执行器，默认自动找")
    parser.add_argument("--facts", default="facts.json",
                        help="读 accuracy.kind 定基线；多输出算子靠它查输出顺序")
    parser.add_argument("--devices", default="0",
                        help="accuracy.kind=builtin 时 pyaclnn 节点用哪张卡")
    parser.add_argument("--keep-raw", action="store_true",
                        help="保留 atk_output/，默认冻结后删掉")
    args = parser.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.exists():
        print(f"{cases_path} 不存在。先跑 gen_cases.py。", file=sys.stderr)
        return 3

    with open(cases_path, encoding="utf-8") as handle:
        cases = json.load(handle)
    if not guard(cases, sys.stderr):
        return 3
    expected = len(cases)

    plugin = args.plugin
    if plugin is None:
        matches = sorted(Path(".").glob("function_*.py"))
        plugin = str(matches[0]) if len(matches) == 1 else None

    if not shutil.which("atk"):
        print("atk 不在 PATH。先跑 probe_env.py。", file=sys.stderr)
        return 3

    out_dir = Path(args.out)
    kind = _acc_kind(args.facts)
    env = os.environ.copy()
    timeout = TIMEOUT
    if kind == "builtin":
        aclnn_name = ""
        try:
            with open(args.facts, encoding="utf-8") as handle:
                aclnn_name = json.load(handle).get("aclnn_name", "")
        except (OSError, ValueError):
            pass
        found, pattern = _find_builtin_symbol(aclnn_name)
        if pattern is None:
            print("ASCEND_OPP_PATH 没设。accuracy.kind=builtin 要在装机目录里找内置"
                  "基线接口，先 source set_env.sh。", file=sys.stderr)
            return 3
        if not found:
            print(f"\n阻塞·未生成 @S4：facts.json 要求以内置 aclnn{aclnn_name} 为精度基线，"
                  f"但这台机器的 CANN 里没有它。", file=sys.stderr)
            print(f"  找的符号  aclnn{aclnn_name}GetWorkspaceSize", file=sys.stderr)
            print(f"  搜过      {pattern}", file=sys.stderr)
            print("要不要改用别的基线由你定，脚本不替你决定。", file=sys.stderr)
            return 2
        print(f"内置基线  aclnn{aclnn_name}GetWorkspaceSize @ {found[0]}")
        # pyaclnn 要 torch_npu，不能关 torch 后端自动加载；同时摘掉自定义算子包，
        # 否则跑到的是待验收实现，等于拿被测算子给自己当标杆。
        env.pop("TORCH_DEVICE_BACKEND_AUTOLOAD", None)
        for name in ("ATK_CUSTOM_OPP_PATH", "ASCEND_CUSTOM_OPP_PATH"):
            env.pop(name, None)
        timeout = TIMEOUT_BUILTIN
        # pyaclnn 算不出 out 该多大（aclnn 的 out 是入参，调用方要先申请好），
        # 所以必须带一个 cpu 节点给出输出的 shape/dtype。它的值不参与比对。
        # 节点名写死成 builtin，golden 目录就叫 pyaclnn_builtin。**不能用自动编号**：
        # 跑测侧待验收节点（`--backend aclnn`）在 ATK 里的名字就是 pyaclnn_0，
        # 基线也叫 pyaclnn_0 的话加载节点会被改名成 pyaclnn_0_1（重名时的改名规则见
        # `atk/configs/nodes_config.py:149`），于是一条 golden 都匹配不上。
        command = ["atk", "node", "--backend", "pyaclnn", "--devices", args.devices,
                   "-n", "builtin",
                   "node", "--backend", "cpu",
                   "task", "-c", str(cases_path), "--task", "accuracy",
                   "--save_data", "output", "--save_data", "input"]
    else:
        # 只起 cpu 一个节点：生成侧要的是标杆输出，不是比对结论。
        command = ["atk", "node", "--backend", "cpu",
                   "task", "-c", str(cases_path), "--task", "accuracy",
                   "--save_data", "output", "--save_data", "input"]
    if plugin:
        command += ["-p", plugin]

    # 旧 golden 到这里才删。前面的校验（缺 cases、没 atk、内置符号找不到）
    # 都会带着旧 golden 退出——冻结失败不该顺手毁掉上一次冻好的那份。
    if out_dir.exists():
        shutil.rmtree(out_dir)

    print(f"标杆形态：{kind}")
    print(f"跑标杆：{' '.join(command)}")
    print(f"用例数：{expected}")
    started = time.time()
    # `start_new_session` 把 atk 放进自己的进程组，超时才杀得干净。**不加它，
    # subprocess 只杀直接子进程**：atk 的 celery worker 与那个监听 127.0.0.1:9090
    # 的节点进程会活下来，下一次跑测把活派给这个残留节点，表现是「算完了但永远
    # 不出报告」，而且 `ss` 里看得到 LISTEN、`ps` 里找不到属主。实测踩过。
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, check=False, env=env,
                                start_new_session=True)
    except subprocess.TimeoutExpired as expired:
        _kill_group(expired)
        print(f"\n标杆超过 {timeout}s 没结束（实测满规模用例包 35s 就够，"
              f"本批 {expected} 条）。", file=sys.stderr)
        print(f"先分清是慢还是挂死：看 atk_output/*/report/ 下有没有 xlsx。"
              f"只有 resume_data 就是挂死，调大 TIMEOUT 没用。", file=sys.stderr)
        return 2
    elapsed = time.time() - started

    stdout = result.stdout or ""
    stderr = result.stderr or ""

    task_dir = _newest_task(".")
    if task_dir is None:
        print(f"\n没找到 atk_output/*/output/。atk 退出码 {result.returncode}。", file=sys.stderr)
        for line in (stderr or stdout).strip().splitlines()[-25:]:
            print(f"  {line}", file=sys.stderr)
        return 2
    output_dir = task_dir / "output"

    leaves = _count_leaves(output_dir)
    if not leaves:
        print("\n标杆一条都没跑出来。最可能的两个原因：", file=sys.stderr)
        print("  1. YAML 的 name 不是可 eval 的 torch 接口名", file=sys.stderr)
        print("  2. 写了执行器但注册名与 api_type 对不上", file=sys.stderr)
        if kind == "builtin":
            print("  3. pyaclnn 起不来：没 source CANN，或这张卡上内置实现跑不了这批用例",
                  file=sys.stderr)
        for line in (stderr or stdout).strip().splitlines()[-25:]:
            print(f"  {line}", file=sys.stderr)
        return 2

    shutil.copytree(output_dir, out_dir)
    inputs_dir = Path(args.inputs)
    frozen_inputs = _freeze_inputs(task_dir, inputs_dir)
    if not args.keep_raw:
        shutil.rmtree(Path("atk_output"), ignore_errors=True)

    # 两个节点各落一份盘，只有基线那份算数：kind=builtin 时是 pyaclnn_*，
    # cpu_* 那份形状对但数值无意义，谁都可能顺手拿它当基线，所以写进 manifest。
    baseline = _baseline_dir(out_dir, kind)
    if baseline is None:
        want = "pyaclnn" if kind == "builtin" else "cpu"
        print(f"\ngolden 里没有 {want} 开头的子目录，基线节点一条都没落盘。",
              file=sys.stderr)
        return 2
    leaves = [leaf for leaf in leaves if leaf.startswith(baseline + "/")]

    if kind == "builtin":
        shape_dir = _baseline_dir(out_dir, "torch")
        if shape_dir is None:
            print("\ngolden 里没有 cpu_* 目录，拿不到申请 out 时用的输出描述。",
                  file=sys.stderr)
            return 2
        aligned, problems = _align_output_info(out_dir / baseline, out_dir / shape_dir)
        if problems:
            print("\n基线的输出描述与申请 out 时用的对不上：", file=sys.stderr)
            for item in problems:
                print(f"  - {item}", file=sys.stderr)
            print("\n内置实现的实际输出与申请时的尺寸不符，先查清楚再冻，不要覆盖掉。",
                  file=sys.stderr)
            return 2
        print(f"输出描述  {aligned} 份对齐成 {shape_dir}/ 那份"
              f"（它才是申请 out 时用的；pyaclnn 存盘会多套一层 list，跑测侧读到会段错误）")

    manifest = {
        "cases": expected,
        "golden_cases": len(leaves),
        # 不记 inputs_dir：布局由契约钉死成包根的 inputs/，两侧消费者都按这个
        # 默认值找。记一个可变的路径进来，等于宣称它可以不是 inputs/——写了
        # inputs.t 而消费者去找 inputs/ 时，两边都不报错，只是一条都读不到。
        # 实测踩过。计数留着，那是自证。
        "input_cases": frozen_inputs,
        "baseline": kind,
        "baseline_dir": baseline,
        "backends": sorted({p.name for p in out_dir.iterdir() if p.is_dir()}),
        "elapsed_seconds": round(elapsed, 1),
        "command": " ".join(command),
    }
    with open(out_dir / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    ratio = len(leaves) / expected if expected else 0
    print(f"\n标杆产出  {len(leaves)}/{expected} 条（{ratio:.1%}），耗时 {elapsed:.0f}s")
    print(f"基线目录  {baseline}/（跑测侧的 accuracy_load 要读这个）")
    print(f"冻结到    {out_dir}/")
    if frozen_inputs == expected:
        print(f"输入张量  {frozen_inputs}/{expected} 条 → {inputs_dir}/"
              f"（跑测侧两种执行器读同一份）")
    else:
        print(f"输入张量  {frozen_inputs}/{expected} 条 → {inputs_dir}/。"
              f"**缺的那些用例只能用 ATK 现生成的输入跑**，C++ 执行器跑不了它们。",
              file=sys.stderr)

    facts_path = Path(args.facts)
    if facts_path.exists():
        with open(facts_path, encoding="utf-8") as handle:
            facts = json.load(handle)
        problems = _check_multi_outputs(out_dir, facts, cases)
        if problems:
            print("\n多输出算子的输出顺序对不上：", file=sys.stderr)
            for item in problems:
                print(f"  - {item}", file=sys.stderr)
            print("\n两种可能，都要回 S2 改，不要动 golden：", file=sys.stderr)
            print("  1. CPU 执行器把输出摊反了，核对 function_<op>.py 的返回顺序", file=sys.stderr)
            print("  2. facts.json 的 output.outputs 顺序写错了，核对接口签名", file=sys.stderr)
            return 2
    elif (facts_path.name == "facts.json"):
        print(f"（没找到 {facts_path}，跳过输出顺序检查）")

    if len(leaves) < expected:
        print(f"\n有 {expected - len(leaves)} 条标杆跑不出来。这些用例在 NPU 上也无法比对，"
              f"留着只会变成假失败。回 S2 修 YAML 或执行器，不要把它们剔掉了事。",
              file=sys.stderr)
        for line in _missing_report(cases, leaves):
            print(line, file=sys.stderr)
        print("某个取值整批塌掉（分母与分子相等）就是那一档的参数组合非法，"
              "从约束器查起；零星几条散在各档就查 atk.log 里那几条的报错。",
              file=sys.stderr)
        return 2
    for line in _tidy_package(Path(".")):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
