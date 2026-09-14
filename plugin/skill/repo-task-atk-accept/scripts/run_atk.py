#!/usr/bin/env python3
"""拉起 ATK 任务并把 xlsx 报告解析成 JSON。

四个模式共用一套节点拓扑与报告解析：
  smoke        每种 dtype 各取一条先跑一遍，只问跑不跑得起来，不问算得对不对
  accuracy     全量用例比对冻结 golden
  performance  按 facts.json 的 performance.kind 跑 performance_device，
               kind=builtin 时连着跑第二轮内置实现作基线
  isolate      失败用例各自单独跑一遍，分开「自己的问题」与「被前面某条污染」

退出码 0 跑完且报告已解析，2 任务失败，3 输入缺失。
**退出码 0 不等于精度通过**，结论看输出 JSON 的 passed。
"""

import argparse
import glob
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import probe_env
import stage_clock  # noqa: E402 - 同目录量具，给 stage JSON 记阶段墙钟

# 总时长兜底的下限。**主要拦截手段是下面的停滞检测，不是这个数。**
#
# 这个数原来写死 1800，标定依据是「187 条全量实测一两分钟」。用例条数后来扩到
# 1000 条而这里没跟着动，结果是跑得完的轮次被兜底杀掉——实测 2.81 s/条 × 1000
# ≈ 47 min，撞 30 分钟上限，整轮白跑。所以现在按条数推，见 `_round_timeout()`。
TIMEOUT_FLOOR = 1800

# 每条用例的墙钟上界。实测自带件路（标杆当场重算、单卡串行）1.6 s/条，
# 另一台机器 0.68 s/条；取 3 s/条留一倍余量。**这个数只用来算兜底，不用来判快慢。**
SECONDS_PER_CASE = 3.0

# 起 atk、建现场、加载算子包的固定开销，与条数无关。实测一次 atk 启动约 30 秒，
# 并发排队时更长，给 10 分钟。
ROUND_OVERHEAD_SECONDS = 600

# 隔离复验单批的兜底与全量同一套算法（`_round_timeout()`），按块内条数推。
#
# 原来是写死 600s，按单实例测的（一次 atk 启动约 30 秒，几乎与用例条数无关）。
# 卡数上去之后这个数不够：13 路并发共用一台主机，启动排队把墙钟拉长，实测有
# 一块撞上 600s 被杀，那一整块 13 条**按执行失败报进了结论**——多报 13 个缺陷，
# 比白占十几分钟卡糟得多。真正的挂死由下面的停滞检测在 180s 内拦住，
# 总时长只是它漏网时的兜底，本来就不该按并发度调。

# 日志多久没长就判定执行 worker 崩了。**ATK 没有超时兜底**：子进程段错误后
# 主进程一直等，进度条永远停在 0/N（troubleshooting.md「跑测长时间停在 0/N」）。
# 正常跑的时候日志一直在长，崩掉是戛然而止——所以量停滞，不量总时长。
STALL_SECONDS = 180

# 非连续用例在同一轮里按比例混进去，比例由 facts.non_contiguous.ratio 定。
#
# **哪条被切是可以复算的**，所以不需要拆成两轮。ATK 按用例 id 当种子决定切不切
# （`atk/tasks/backends/backend.py:151`）：
#
#     rng = random.Random(case_id); should_slice = rng.random() < ratio
#
# 同一个 ratio 下这个划分完全确定，`_layout_of()` 用同一个公式离线复算，
# 通过率照样分组报得出来。拆两轮的代价是整轮重跑：1000 条一轮 26 分钟，
# 而 TIMEOUT 是整次调用的上限，两轮串行必然撞上限（实测轮 2 被杀）。
#
# ratio 缺省 0.4。任务书不要求非连续时按 0.0 走，一条都不切。
DEFAULT_SLICE_RATIO = 0.4

# 「接口层参数校验拒绝」的报错特征。命中它说明 aclnn 入口在 GetWorkspaceSize
# 就把入参挡回来了，一条都没进 kernel——与「跑起来了但算错」是两回事，
# 报告里也不该混在一起。实测原文（装错算子目录那次）：
#   AclNN_Parameter_Error(EZ1001): Tensor self not implemented for DT_INT8,
#   should be in dtype support list [DT_INT32,DT_FLOAT16,DT_FLOAT,DT_INT64,...]
PARAM_REJECT_MARKERS = ("AclNN_Parameter_Error", "should be in dtype support list",
                        "not implemented for DT_")


# ---- 执行剖面 -------------------------------------------------------------
#
# 待验收算子**怎么被调起来**，由它暴露的接口形态定，不由它属于哪个仓定。
# `facts.json` 的 `backend` 选一个剖面，下面这张表把差异收在一处。
#
# 加这张表之前，`--backend aclnn` 与 `pyaclnn_0` 两个串写死在四处，
# 改一处漏三处的表现是「报表里解析不到被测节点」，通过率算成 0 而不报错。

PROFILES = {
    # aclnn 两段式 C 接口。ATK 用 ctypes dlopen 直调，被测节点叫 pyaclnn_<n>。
    "aclnn": {
        "atk_backend": "aclnn",
        "node_prefix": "pyaclnn",
        "custom_opp": True,      # 靠 ATK_CUSTOM_OPP_PATH 指定待验收实现
        "symbol_check": True,    # 日志里核 `import aclnnXxx... success!`
        "frozen_inputs": True,   # 没有 inputs/ 就退 3，见 main() 的 inputs 分支
        "standalone": True,      # A3.5 独立执行器（C++ 两段式）可用
        "param_reject": True,    # 冒烟失败时可按 EZ1001 判接口层拒绝
    },
    # 算子注册进 torch 的 dispatch（新建命名空间 `torch.ops.<ns>.<name>`，或注册进
    # ATen 已有算子的 NPU 键），ATK 起 torch 调那个 callable，
    # 被测节点叫 npu_<n>。没有 aclnn 两段式接口的仓（ops-sparse 这类）走这条。
    "npu": {
        "atk_backend": "npu",
        "node_prefix": "npu",
        "custom_opp": False,     # 没有 opp vendor 层，靠 LD_LIBRARY_PATH 加载普通 so
        "symbol_check": False,   # 没有 dlopen，日志里没有那行，核了必然空报
        "frozen_inputs": False,  # 没有 inputs/ 也能跑：用例自带编码（含 seed）。
                                 # **这不是「不读 inputs/」**——目录在就照读，
                                 # 判据是产物在不在，见 main() 的 inputs 分支
        "standalone": False,     # C++ 执行器按 aclnn 两段式拼，这条路不适用
        "param_reject": False,   # 参数不合法时抛的是 Python 异常，不是 EZ1001
    },
}

# `_node_command` 也被 make_repro.py 调用，那边没有 args。剖面由 main() 设一次，
# 之后全模块共用——不设时按 aclnn 走，与加剖面之前的行为逐字相同。
_ACTIVE_PROFILE = PROFILES["aclnn"]

# 纯 attr 用例的分档轴。空串时按 dtype 分，与加它之前逐字相同。
# 与 verdict.py 的 `_GROUP["attr"]` 同源——两处都读 `facts.json` 的 `group_attr`，
# 各写一套判据的话，冒烟抽的档与报告里的档对不上。
_GROUP_ATTR = ""


def _profile(facts_path):
    """读 facts.json 的 backend 定执行剖面。认不出返回 None，调用方退 3。

    缺字段按 `aclnn` 走：加剖面之前的用例包都没有这个字段，
    静默换剖面比缺字段更糟。
    """
    try:
        with open(facts_path, encoding="utf-8") as handle:
            backend = json.load(handle).get("backend") or "aclnn"
    except (OSError, ValueError):
        backend = "aclnn"
    return PROFILES.get(str(backend))


def _under_test_lib_ok(install_path):
    """npu 剖面下核「跑的是不是待验收实现」，返回 (通过?, 说明)。

    aclnn 剖面靠日志里的 `import aclnnXxx from <路径> success!` 核这件事
    （`_check_loaded_so`）。npu 剖面没有那行——算子注册进 torch 的 dispatch，
    由 `torch.ops.load_library` 或工程自己的 import 装进进程，
    ATK 一个字都不打。**不核就是本链路最贵的那类静默错误**：
    进程里若装的是别处的同名实现，报告一切正常，通过率甚至好看。

    判据取自进程自己的 `/proc/self/maps`：把注册模块 import 进来之后，
    待验收 so 必然出现在映射表里，且路径必须落在**本轮的产物根**下。

    产物根是两处，不是一处：装包目录 `install_root`，以及算子工程 `repo`。
    torch 扩展是 `setup.py` 就地编的，链接期的 RPATH 指向工程的 `build/`，
    **RPATH 的优先级高于 `LD_LIBRARY_PATH`**，所以进程里加载的往往是工程里
    那份而不是装包目录那份（2026-09-08 实测）。两份出自同一次构建，都算本轮
    产物；真正要拦的是落在 CANN 装机目录或**别的算子工程**下的同名实现。
    说明里带上命中的是哪一处，报告里才看得出测的是构建产物还是装包产物。
    """
    try:
        with open(install_path, encoding="utf-8") as handle:
            install = json.load(handle)
    except (OSError, ValueError):
        return False, f"{install_path} 读不到，A2 没跑或没落盘"
    module = install.get("register_module") or ""
    # 工程根按 A2 走的哪条路记在不同字段里：探到母仓时是 `repo`，
    # 显式给 `--parent-repo` 时是 `parent_repo`。少认一个就会把正常的
    # 「加载的是工程里那份」判成错配，而那是 torch 扩展的常态。
    roots = [str(Path(item).resolve()) for item in
             (install.get("install_root"), install.get("repo"),
              install.get("parent_repo")) if item]
    if not roots or not module:
        return False, (f"{install_path} 里缺 install_root/repo 或 register_module。"
                       f"npu 剖面靠这两项核待验收实现，A2 要把它们写进去")
    try:
        out = subprocess.run([sys.executable, "-c", PROBE_MAPS, module, *roots],
                             capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"探针跑不起来：{exc}"
    if out.returncode == 3:
        tail = (out.stderr or "").strip().splitlines()[-6:]
        return False, (f"import {module} 失败，待验收实现没装进来：\n  "
                       + "\n  ".join(tail))
    if out.returncode == 4:
        return False, (f"{module} 装进来了，但 /proc/self/maps 里没有 "
                       f"{'、'.join(roots)} 下的 so。进程里跑的不是本轮的实现")
    if out.returncode != 0:
        tail = (out.stderr or out.stdout or "").strip().splitlines()[-6:]
        return False, "探针异常退出：\n  " + "\n  ".join(tail)
    return True, out.stdout.strip().splitlines()[0]


# 探针跑在**子进程**里：import 待验收算子会把整套 torch 与 CANN 拉进来，
# 拉进本进程之后再也卸不掉，而 run_atk.py 后面还要起别的进程。
# 退出码分三档，调用方按档给不同的指路——只报「失败了」的话，
# 「没装进来」与「装的是别处那份」这两件事看不出区别。
PROBE_TIMEOUT = 300
PROBE_MAPS = r"""
import sys

module, roots = sys.argv[1], sys.argv[2:]
try:
    # 先 torch 再算子模块：torch 扩展链着 libc10.so，靠 torch 自己 import 时把它
    # 载进进程来解析。反过来先 import 扩展会报 `libc10.so: cannot open shared
    # object file`——那句指向动态库路径，真因却是 import 顺序。
    import torch  # noqa: F401
except BaseException:                             # noqa: BLE001 - 没有 torch 也继续
    pass
try:
    __import__(module)
except BaseException as exc:                      # noqa: BLE001 - 什么都要报出来
    import traceback
    traceback.print_exc()
    sys.exit(3)

paths = set()
try:
    with open("/proc/self/maps", encoding="utf-8") as handle:
        for line in handle:
            path = line.rstrip().rpartition(" ")[2]
            if path.startswith("/") and (path.endswith(".so") or ".so." in path):
                paths.add(path)
except OSError:
    # 没有 /proc 的平台（macOS）核不了，当作没核到，不当作通过。
    sys.exit(4)

inside = sorted(p for p in paths if any(p.startswith(r) for r in roots))
if not inside:
    sys.exit(4)
print("\n".join(inside))
"""

# 「这条用例真的跑到执行算子那一步了」的日志特征（`opp_executor.py:76`）。
# 它是区分真实失败与连带失败的判据，**不依赖任何报错文本**。
EXECUTED_MARK = re.compile(r"\[case (\d+)\]\[RUN OPP TASK\] Start Execute OPP")

# facts.json 的 performance.kind 词表。取值不在这里时不猜，停在 A5。
# threshold 与 none 的执行路径完全一样（一轮 performance_device），差别只在
# verdict.py 怎么写结论：none 是「任务书没要求」，threshold 是「有要求但本侧
# 判不了，把 criterion 原话递给人」。只有 builtin 会连跑第二轮。
PERF_KINDS = ("none", "builtin", "cross_dtype", "threshold")


def _kill_group(proc):
    """把 proc 连同它整个进程组收干净，先 TERM 后 KILL。

    ATK 的孙子进程（celery worker、9090 节点）不跟着主进程走，漏掉的后果不是
    脏一点，而是下一次跑测会静默连到这个残留节点上。见 `_run_logged` 的说明。
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            continue
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _run_logged(command, log_path, timeout, stall=STALL_SECONDS, cwd=None):
    """跑一条命令并把输出写进 log_path，日志停止增长超过 stall 秒就杀掉。

    返回 `(returncode, 被杀的原因)`。returncode 为 None 表示没能正常结束。

    为什么按停滞判而不按总时长判：ATK 没有超时兜底，执行 worker 段错误之后
    主进程会一直等下去，进度条停在 0/N 不动。按总时长等，一个崩掉的任务要
    白占卡到超时为止；按停滞判，三分钟就能收掉，而正常跑的任务日志一直在长，
    不会被误杀。总时长上限保留，只当停滞检测漏网时的兜底。

    杀的是**整个进程组**，不是单个子进程。ATK 一次跑测起一个主进程 + 若干 celery
    worker + 一个绑 127.0.0.1:9090 的节点进程；只 kill 主进程的话后两类活下来，
    下一轮跑测会把活派给这个残留节点，表现是用例全算完、`report/` 下只有
    `resume_data`、永远不出 xlsx，而 `ps` 里已经找不到属主。实测踩过。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as handle:
        # cwd 要能分开：ATK 把 atk_output/ 写在当前目录，多块并发跑在同一个目录里
        # 会互相覆盖，_newest_report 也会取到别人的报告。
        proc = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT,
                                cwd=cwd, start_new_session=True)
        deadline = time.time() + timeout
        last_size, last_grow = -1, time.time()
        while True:
            try:
                return proc.wait(timeout=5), ""
            except subprocess.TimeoutExpired:
                pass
            now = time.time()
            size = log_path.stat().st_size if log_path.exists() else 0
            if size != last_size:
                last_size, last_grow = size, now
            if now - last_grow > stall:
                _kill_group(proc)
                return None, (f"日志 {stall}s 没有新增，判定执行 worker 崩了。"
                              f"按 troubleshooting.md「跑测长时间停在 0/N」定位")
            if now > deadline:
                _kill_group(proc)
                return None, f"超过总时长上限 {timeout}s"


def _check_op(op, facts_path):
    """核对当前工作目录确实是这个算子的验收现场，不是的话返回一句话理由。

    工作目录搞错时脚本本来发现不了：别的算子的现场里 cases.json 与 golden/
    一样齐全，跑起来完全合法，只是跑的不是你要的算子。真机上出过在 1d 的现场里
    重复跑 1d、以为在跑 2d 的事，白烧了十分钟卡时。
    """
    if not op:
        return None
    try:
        with open(facts_path, encoding="utf-8") as handle:
            actual = json.load(handle).get("aclnn_name", "")
    except (OSError, ValueError):
        return f"{facts_path} 读不到，无法核对 --op {op}"
    if actual != op:
        return (f"--op {op} 与 {facts_path} 的 aclnn_name={actual!r} 不符：\n"
                f"  当前目录 {Path.cwd()} 是 {actual} 的验收现场。\n"
                f"  先 cd 到 {op} 的工作目录再跑。")
    return None


def _device_busy(devices):
    """目标卡上有别人的进程时列出来，判据与 A1 的空闲卡表同源。

    实现在 `probe_env.device_busy`：A1 要报「哪些卡空闲」，这里要拦「这张卡被占」，
    同一个判据两处用，分两份写迟早漂。
    """
    ids = [int(d.strip()) for d in devices.split(",") if d.strip().isdigit()]
    return probe_env.device_busy(ids)


def _check_loaded_so(log_path):
    """核对本轮实际加载的 so 落在自定义算子包的 vendor 目录下，越界的返回列表。

    pyaclnn 每次绑定都会打一行 `import aclnnXxxGetWorkspaceSize from <路径>
    success!`（`atk/tasks/backends/pyaclnn_backend.py:246`）。路径指向 CANN 装机
    目录就说明测的是**内置同名算子**而不是待验收实现——报告一切正常，通过率
    可能还很好看。这是整条链路上最贵的一种静默错误，所以每轮跑完都验，
    不只验一次。基线轮是故意摘掉自定义包的，调用方不要对它验。
    """
    lib = os.environ.get("ATK_CUSTOM_OPP_PATH", "")
    if not lib or not log_path.exists():
        return []
    vendor = str(Path(lib).parents[2])
    seen = set()
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "import aclnn" not in line or "success" not in line:
            continue
        _, _, tail = line.partition(" from ")
        path = tail.split(" success")[0].strip()
        if path and not path.startswith(vendor):
            seen.add(path)
    return sorted(seen)


# 自带件路：标杆当场算，没有冻结 golden。给了 --nodes 就走这条，
# 拓扑由自带件自己的 nodes yaml 描述，本脚本不再拼节点。
_NODES = None
_BASELINE_NODE = None


def _nodes_baseline(nodes):
    """从 nodes yaml 里认出标杆节点的 (backend, name)。

    ATK 用 f"{backend}_{name}" 去报告里找那一列，认错就整列读不到而不报错。
    判据是「backend 不是被测剖面那个」——自带件的拓扑固定两个节点，
    一个被测一个标杆。认不出时返回 None，调用方要求人把 --baseline-node 给死。
    """
    backend = name = None
    found = []
    for line in Path(nodes).read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text.startswith("- backend:") or text.startswith("backend:"):
            if backend:
                found.append((backend, name))
            backend, name = text.split(":", 1)[1].strip().strip("'\""), None
        elif text.startswith("name:") and backend:
            name = text.split(":", 1)[1].strip().strip("'\"")
    if backend:
        found.append((backend, name))
    under = _ACTIVE_PROFILE["atk_backend"] if _ACTIVE_PROFILE else "npu"
    others = [item for item in found if item[0] != under]
    return others[0] if len(others) == 1 else None


def _pkg_root(golden, cases):
    """用例包根。自带件路没有 golden，按用例文件所在目录算。"""
    return Path(cases).resolve().parent if _NODES else Path(golden).parent


def _load_node(golden):
    """基线节点的 (backend, name)。自带件路从 nodes yaml 取，其余从 golden 取。

    ATK 用**加载节点自己的** f"{backend}_{name}" 去 golden 里找目录
    （`atk/tasks/opp_tasks.py:465`），所以加载节点必须与冻结时那个节点同名。
    生成侧把基线目录记在 manifest.json 的 baseline_dir 里：torch 基线是
    `cpu_0`，内置 aclnn 基线是 `pyaclnn_builtin`。**不要按 cpu 写死**——喂进来一个
    内置基线的用例包时，cpu_builtin 里躺的是只给形状用的废数据，比对全错且不报错
    （那个 cpu 节点继承主节点的名字 `builtin`，`nodes_config.py:141`，不叫 cpu_0）。

    老用例包没有 manifest.json，按 cpu_0 走，行为与从前一致。"""
    if _NODES:
        got = _BASELINE_NODE or _nodes_baseline(_NODES)
        if got is None:
            raise SystemExit(f"从 {_NODES} 里认不出唯一的标杆节点，"
                             f"用 --baseline-node <backend>:<name> 给死。")
        return got
    try:
        with open(Path(golden) / "manifest.json", encoding="utf-8") as handle:
            baseline_dir = json.load(handle).get("baseline_dir")
    except (OSError, ValueError):
        baseline_dir = None
    if not baseline_dir:
        return "cpu", None
    backend, _, name = str(baseline_dir).partition("_")
    return backend, (name or None)


def _list_output(facts_path):
    """算子的出参里有没有张量列表（`aclTensorList*`）。

    张量列表出参的 `output_info.json` **本来就是** `[[{t0}, …, {tn}]]`：外层是
    「一个输出」，内层是那个列表的 n 个张量。拿它当「多套了一层」拦下来是误报，
    实测 ForeachMulList 卡在这里。判据从 `facts.json` 的参数类型取，不认算子名。
    """
    try:
        with open(facts_path, encoding="utf-8") as handle:
            facts = json.load(handle)
    except (OSError, ValueError):
        return False
    return any(p.get("role") == "output" and p.get("atk_type") == "tensors"
               for p in facts.get("params") or [])


def _check_output_info(golden, backend, name, list_output=False):
    """开跑前抽一份 output_info.json 看结构，多套一层就拦住。

    正确形态是一维、每个输出一项。ATK 的 aclnn 后端 `after_call` 返回 list，
    `get_output_data_infos`（`atk/common/utils.py:246`）碰上 list 会递归再整个
    append，于是存成 `[[{...}]]`；torch 标杆返回单个 tensor，存成 `[{...}]`。

    **不拦的后果是没有报错的挂死**：accuracy_load 把多的那层塞进 output_info_list，
    NPU 侧照它建输出张量后 worker 段错误，而 ATK 没有超时兜底，进度条永远停在 0/N。
    生成侧的 freeze_golden.py 会抹平这层，这里拦的是别处冻的或旧版本的用例包。

    `list_output` 为真时整条判据作废：那类算子的嵌套是对的，见 `_list_output`。
    """
    if list_output:
        return None
    root = Path(golden) / f"{backend}_{name or '0'}"
    if not root.is_dir():
        return None
    for path in sorted(root.rglob("output_info.json"))[:1]:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        if isinstance(data, list) and data and all(isinstance(x, list) for x in data):
            return path
    return None


def _node_command(mode, cases, golden, devices, plugin, slice_ratio=0.0,
                  load_node=None, inputs=None, profile=None):
    """被测节点实算，加载节点读冻结 golden，不重算标杆。

    `profile` 定被测节点用哪个 ATK 后端（见 PROFILES）。不给时用 main() 设的那份，
    main() 也没设时按 aclnn 走。

    `slice_ratio` 大于 0 时加 `--slice_input non_contiguous --slice_input_ratio`：
    ATK 按用例 id 当种子逐条决定切不切，切中的开 2 倍存储取步长 2 的视图再把原张量
    拷进去（`atk/configs/dataset_config.py:296-313`），**逻辑值不变**，所以冻好的
    golden 照样能比对，不用重冻。哪几条被切用 `_layout_of()` 复算得出来。
    性能轮不加——非连续会把耗时算进搬运。

    `golden` 原样进命令，**不在这里 resolve**：`make_repro.py` 要拼出带 `$PKG`
    变量的同一条命令，resolve 会把变量名当相对路径拼到 CWD 上。要绝对路径的
    调用方自己先 resolve。`load_node` 同理，给了就用，免得为了读 manifest 而
    要求 `golden` 是真实存在的路径。

    `inputs` 给了就加 `--input_data`，ATK 跳过造数直接读 `<inputs>/<用例 id>/input.bin`
    （`atk/tasks/executors/dataset_executor.py:125` 提前 return）。**这是两个执行器
    读到同一批字节的唯一办法**——不给它，ATK 按 `torch.manual_seed(用例 id)` 现造，
    上游改一次造数逻辑，golden 是老的、输入是新的，比对全错且不报错。
    切片不受影响：它在 `atk/tasks/backends/backend.py:160` 对已载入的数据做，
    与数据从哪来无关，所以冻的是切片前的字节，两侧口径一致。"""
    if _NODES:
        # 自带件自己描述拓扑，本脚本只把用例、插件与任务名接上去。
        command = ["atk", "task", "-c", str(cases), "-n", str(_NODES),
                   "--task", "performance_device" if mode == "performance" else "accuracy"]
        if plugin:
            command += ["-p", str(plugin)]
        return command
    backend, name = load_node or _load_node(golden)
    under_test = (profile or _ACTIVE_PROFILE)["atk_backend"]
    command = ["atk", "node", "--backend", under_test, "--devices", devices,
               "node", "--backend", backend]
    # 名字显式给死，免得加载节点的自动编号受拓扑里同后端节点个数影响
    # （编号按后端各自计数，`atk/configs/nodes_config.py:143`）。
    if name is not None:
        command += ["-n", name]
    command += ["--task", "accuracy_load",
                "--output_path", str(golden),
                "task", "-c", str(cases),
                "--task", "performance_device" if mode == "performance" else "accuracy"]
    if slice_ratio > 0 and mode != "performance":
        command += ["--slice_input", "non_contiguous",
                    "--slice_input_ratio", str(slice_ratio)]
    if inputs:
        command += ["--input_data", str(inputs)]
    if plugin:
        command += ["-p", str(plugin)]
    return command


def _newest_report(stem, since, root="."):
    """取本轮产出的报告。

    **必须卡 since。** 任务失败到没写出报告时，只取「最新」会拿到上一轮的报告；
    上一轮多半是通过的，于是这一轮被误判成通过。隔离复验里全是单用例连跑，
    这个坑会把真实失败判成连带失败——真机上就这样漏掉过一条真实失败。
    """
    reports = glob.glob(f"{root}/atk_output/{stem}_*/report/*.xlsx")
    fresh = [r for r in reports if os.path.getmtime(r) >= since]
    return max(fresh, key=os.path.getmtime) if fresh else None


def _sheet_rows(book, name):
    if name not in book.sheetnames:
        return []
    sheet = book[name]
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(cell) if cell is not None else "" for cell in rows[0]]
    out = []
    for row in rows[1:]:
        if all(cell is None or str(cell) == "None" for cell in row):
            continue
        out.append(dict(zip(header, row)))
    return out


def _truthy(value):
    return str(value).strip().lower() in {"true", "pass", "1"}


def _case_verdicts(statistic, load_node):
    """从 statistic 表逐用例判执行与精度，返回 (执行失败 ids, 精度不符 ids)。

    两条都是实测出来的 ATK 行为，缺一条都会让逐用例结论静默变空：

    1. **精度判定挂在加载节点的列上**（`<backend>_<name>_精度通过`），不在被测节点
       `pyaclnn_0` 的列上——比对是加载节点拿冻结 golden 做的，被测节点那列整列是
       `None`。
    2. **`failed cases` 与 `accuracy false cases` 两张表可能是空的**，即使 summary
       的通过数小于总用例数。

    所以逐用例结论以 statistic 为准；那两张表在有内容时并进来，不作唯一来源。
    汇总数（总数、通过数、通过率、是否达标）仍以 summary 为准，不在这里重算。
    """
    column = f"{load_node}_精度通过" if load_node else ""
    executed_failed, accuracy_false = set(), set()
    for row in statistic:
        ident = row.get("编号")
        if not str(ident).isdigit():
            continue
        ident = int(ident)
        if str(row.get("运行结果", "")).strip().upper() not in {"SUCCESS", ""}:
            executed_failed.add(ident)
        elif column in row and row.get(column) is not None \
                and not _truthy(row.get(column)):
            accuracy_false.add(ident)
    return executed_failed, accuracy_false


def _parse_report(path, mode, load_node=""):
    """summary 表的列名随任务类型变：精度任务是「通过用例个数/通过率/精度是否达标」，
    性能任务是「device性能通过率/平均device性能比/device性能是否达标」。"""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    summary = _sheet_rows(book, "summary")
    failed = _sheet_rows(book, "failed cases")
    accuracy_false = _sheet_rows(book, "accuracy false cases")
    statistic = _sheet_rows(book, "statistic")
    book.close()

    from_statistic, false_from_statistic = _case_verdicts(statistic, load_node)
    result = {
        "report": path,
        "total": 0,
        "succeeded": 0,
        "failed": 0,
        "matched": 0,
        "pass_rate": 0.0,
        "passed": False,
        "failed_ids": sorted(from_statistic | {int(row["编号"]) for row in failed
                                               if str(row.get("编号", "")).isdigit()}),
        "accuracy_false_ids": sorted(false_from_statistic
                                     | {int(row["编号"]) for row in accuracy_false
                                        if str(row.get("编号", "")).isdigit()}),
    }
    # 两张表里的执行失败优先：同一条不该既算执行失败又算精度不符。
    result["accuracy_false_ids"] = [i for i in result["accuracy_false_ids"]
                                    if i not in set(result["failed_ids"])]

    if summary:
        row = summary[0]

        def _int(key):
            try:
                return int(float(row.get(key)))
            except (TypeError, ValueError):
                return 0

        def _float(key):
            try:
                return float(row.get(key))
            except (TypeError, ValueError):
                return 0.0

        result["node"] = row.get("名称", "")
        result["total"] = _int("总用例数")
        if mode == "performance":
            result["succeeded"] = result["total"] - len(result["failed_ids"])
            result["failed"] = len(result["failed_ids"])
            result["pass_rate"] = _float("device性能通过率")
            result["avg_ratio"] = row.get("平均device性能比")
            result["passed"] = str(row.get("device性能是否达标", "")).strip().lower() == "pass"
        else:
            result["succeeded"] = _int("执行成功用例个数")
            result["failed"] = _int("执行失败用例个数")
            result["matched"] = _int("通过用例个数")
            result["pass_rate"] = _float("通过率")
            result["passed"] = str(row.get("精度是否达标", "")).strip().lower() == "pass"

    result["statistic"] = statistic
    return result


def _device_times(statistic, load_node="", node_prefix=""):
    """从 statistic 表按用例取待验收节点与标杆节点的 Device 耗时。

    待验收节点在报表里的列名前缀由剖面定：aclnn 剖面是 `pyaclnn_0`，
    npu 剖面是 `npu_0`。**不能只看前缀**：内置基线的用例包里标杆节点也是
    pyaclnn（`pyaclnn_builtin`），只按前缀分会让标杆的列覆盖掉被测的列。"""
    node_prefix = node_prefix or _ACTIVE_PROFILE["node_prefix"]
    times = {}
    for row in statistic:
        ident = row.get("编号")
        if not str(ident).isdigit():
            continue
        entry = {}
        for key, value in row.items():
            key = str(key)
            if "Device性能" not in key:
                continue
            is_baseline = bool(load_node) and key.startswith(load_node)
            try:
                entry["cpu" if is_baseline or not key.startswith(node_prefix)
                      else "aclnn"] = float(value)
            except (TypeError, ValueError):
                continue
        if entry:
            times[int(ident)] = entry
    return times


def _executed_cases(log_path):
    """本轮真正跑到「执行算子」那一步的用例 id。

    **这是区分真实失败与连带失败的判据，而且不依赖任何报错文本。**
    device context 被 aicore 异常打废之后，后面的用例在初始化阶段就抛
    `ACL stream synchronize failed`，走不到 `opp_executor.py` 打这行日志的地方；
    真凶自己是跑到了执行才挂的，这行一定有。

    七份真机日志逐份验过，等式一次不差：**执行到 OPP 的条数 = 通过数 + 自有失败数**
    （全量 43=42+1、装错算子 252=152+100、round1 1=0+1、round5 6=5+1、
    probe/slice 9=8+1）。

    早先按「批内最小 id 是真凶」判，那个假设不成立：`create_dataset` 是 4 并发，
    做完就往 device_run 队列里塞，**执行顺序不按 id**（实测 41 → 43 → 42）。
    报错文本也不能当判据——真凶与连带都报 507015，只是措辞不同。
    """
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {int(m.group(1)) for m in EXECUTED_MARK.finditer(text)}


def _resolve_local(path):
    """存在的相对路径转成绝对路径，其余原样返回。

    原样返回的两类：空值，以及带 `$PKG` 这种变量名的路径（`make_repro.py` 要拼出
    带变量的同一条命令，resolve 会把变量名当相对目录拼到 CWD 上）。
    """
    if not path:
        return path
    text = str(path)
    if "$" in text:
        return path
    candidate = Path(text)
    if candidate.is_absolute() or not candidate.exists():
        return path
    return candidate.resolve()


def _run_subset(ids, cases, golden, devices, plugin, work_dir, slice_ratio=0.0,
                inputs=None):
    """把指定 id 的用例单独写一份跑一轮，返回 (报告解析结果, 执行到 OPP 的 id 集合)。

    目录换、文件名不换——golden 的子目录名取自用例文件基名，换名就找不到 golden。

    `slice_ratio` 必须与全量轮同一个数。不一致时测的是两个东西：只在非连续输入下
    崩的用例会被重跑成连续、跑通、判成连带，然后从通过率分母里剔除——静默漏报。
    实测 case 67 就是这么丢的。

    **同一个 ratio 就够了，不必逐条指定布局。** ATK 按用例 id 当种子决定切不切，
    子集里的某条与它在全量轮里拿到的是同一个种子，所以布局自动一致。
    """
    # golden 与 plugin 在这里就地转绝对路径。**命令跑在 work_dir 里**，相对路径
    # 会按 work_dir 解析而不是按调用者的 cwd——实测一次 `-p ../input/kit_fixes`
    # 就这么失效，插件一个都没注册，整块 54 条全被报成「执行失败」，而且不报错。
    # 从前这条只写在下面的注释里，靠调用方自觉，没有任何机制拦。
    golden, plugin = _resolve_local(golden), _resolve_local(plugin)
    work_dir.mkdir(parents=True, exist_ok=True)
    with open(cases, encoding="utf-8") as handle:
        allcases = json.load(handle)
    wanted = [case for case in allcases if case["id"] in set(ids)]
    with open(work_dir / "cases.json", "w", encoding="utf-8") as handle:
        json.dump(wanted, handle, ensure_ascii=False)

    # **命令跑在 work_dir 里**，所以用例文件写成裸名，golden 与 plugin 由调用方给
    # 绝对路径。这样每一块的 atk_output/ 各归各的，多块并发才不会互相覆盖，
    # `_newest_report` 也不会取到别人的报告。
    command = _node_command("accuracy", "cases.json", golden, devices, plugin,
                            slice_ratio=slice_ratio, inputs=inputs)
    started = time.time() - 1  # 留 1 秒余量，防文件系统时间戳取整
    log_path = work_dir / "run.log"
    code, killed = _run_logged(command, log_path, _round_timeout(len(wanted)),
                               cwd=work_dir)
    # **块被杀掉也要把日志读出来。** `Start Execute OPP` 是崩溃前写下的，
    # 进程被杀不会让它消失——扔掉它等于把「有没有跑到执行」这条判据在最需要
    # 它的分支上关掉，整块只能一刀切成失败。实测一个 12 条的块里 0 条跑到执行，
    # 那 12 条全被报成执行失败，而它们其实一条都没判定过。
    if code is None:
        return None, _executed_cases(log_path), killed
    report = _newest_report("cases", started, work_dir)
    # 没产出报告 = 挂到连报告都没写出来。同样把日志交出去，由调用方按
    # 「跑到执行没有」分开，不要整块算失败。
    if not report:
        return None, _executed_cases(log_path), "没产出报告"
    backend, name = _load_node(golden)
    result = _parse_report(report, "accuracy", f"{backend}_{name}" if name else "")
    return result, _executed_cases(log_path), ""


def _filter_cases(cases_path, dtypes, exclude_ids):
    """按 dtype 或 id 过滤本轮要跑的用例，写成 <mode 目录>/cases.json。

    只给性能基线轮用：CANN 内置实现常常不支持任务书新增的那几种 dtype，
    也可能有跑不动的用例，两轮要在**同一批**用例上比才有意义。
    过滤掉哪些，报告里必须写进「不覆盖的范围」。

    **文件名固定叫 cases.json**：ATK 拿用例文件基名当 golden 的子目录名
    （`atk/tasks/result_process.py:67`），换名就一条 golden 都找不到。
    """
    with open(cases_path, encoding="utf-8") as handle:
        cases = json.load(handle)
    if exclude_ids:
        before = len(cases)
        dropped = {int(i) for i in exclude_ids.split(",") if i.strip()}
        cases = [c for c in cases if c["id"] not in dropped]
        print(f"id 剔除    {before} → {len(cases)} 条")
    if dtypes:
        before = len(cases)
        wanted = {d.strip() for d in dtypes.split(",") if d.strip()}
        cases = [c for c in cases if _case_dtype(c) in wanted]
        print(f"dtype 过滤 {before} → {len(cases)} 条，只留 {'、'.join(sorted(wanted))}")
    if not cases:
        print("过滤后一条不剩，检查 --dtypes 是否写成了 ATK 词表里的名字。",
              file=sys.stderr)
        return None
    out_dir = Path("subset")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "cases.json"
    with open(out_file, "w", encoding="utf-8") as handle:
        json.dump(cases, handle, ensure_ascii=False)
    print(f"本轮用例  {len(cases)} 条 -> {out_file}")
    return out_file


def _case_dtype(case):
    """用例的分档键：冒烟按它每档抽一条，`--dtypes` 按它筛。取不到返回 None。

    **张量列表参数（`type: tensors`）在用例里是一个 list**，不是 dict——只认 dict
    的话这类算子每条都判成 unknown，冒烟就只抽得出一条、`--dtypes` 也筛不出东西。
    列表内 dtype 由生成侧的约束器归一，取第一个元素的即可。

    纯 attr 用例一个张量都没有，取 dtype 必然是 None，冒烟同样塌成一条。
    此时改按 `facts.json` 的 `group_attr` 分——那是「整类会一起失败」的轴，
    与 dtype 在 aclnn 剖面里的作用相同。稀疏算子上它是格式：四条就能拦住
    「blocked_ell 整类跑不起来」，否则要跑完全量才发现。
    """
    for item in case.get("inputs", []):
        if isinstance(item, list):
            item = item[0] if item else None
        if isinstance(item, dict) and item.get("type") in ("tensor", "tensors"):
            return item.get("dtype")
    if _GROUP_ATTR:
        for item in case.get("inputs", []):
            if not isinstance(item, dict) or item.get("name") != _GROUP_ATTR:
                continue
            values = item.get("range_values")
            value = (values[0] if isinstance(values, list) and values
                     else item.get("value"))
            return f"{_GROUP_ATTR}={value}"
    return None


# 自带件跑不起来时，ATK 的报错方向常常不指向真因。这张表是实测过的对照：
# 左边是日志里认得出的特征串，右边是真因与去向。**没有它，执行者要在几百行
# celery 栈里自己找**，实测一轮冒烟要来回三四次才定位到一条。
#
# 只收「看见这行就能定去向」的。含糊的不收——报错方向本来就不可靠，
# 多一条猜出来的判据，执行者就多改一处不该改的地方。
KIT_SYMPTOMS = [
    ("without MKL",
     "CPU 标杆挑了本机算不了的存储格式（作者机有 MKL，这台没有）",
     "kit_fixes 里派生覆盖标杆方法，改用 numpy 算"),
    ("Float did not match Double",
     "标杆按更宽的 dtype 算，装机 ATK 的归一化不覆盖这一对",
     "kit_fixes 里派生覆盖，比对前把两侧对齐到同一 dtype"),
    ("Could not run 'aten::",
     "待验收实现没在 ATK 的 worker 进程里注册（主进程 import 过不算）",
     "kit_fixes 的插件入口里 import 注册模块，插件由每个 worker 自己加载"),
    ("not enough values to unpack",
     "ATK 按用例里的 name 把输入分流进了 kwargs，执行器只读 args",
     "adapt_cases 去掉 name，或派生执行器补一条 kwargs 回退"),
    ("validation error",
     "用例过不了装机 ATK 的 CaseConfig 校验",
     "adapt_cases 改那个字段；跑 kit_lint.py，它会打出装机那一版收什么类型"),
    ("No such file or directory",
     "路径在子目录轮里解析偏了，或修复件没落到指定位置",
     "命令里的路径一律相对 <现场>/work，脚本会自己 resolve"),
]


# ATK 自己的包装异常，不是真因。它们套在真异常外面，报出来会把人指向 ATK 而不是
# 自带件。命中它们时继续往下找，全是它们才退而报第一条。
WRAPPER_EXC = ("TerminalCaseFailure", "CeleryError", "WorkerLostError",
               "SoftTimeLimitExceeded")
EXC_LINE = re.compile(r"^\s*([A-Za-z_][\w.]*(?:Error|Exception|Failure))\s*:\s*(\S.*)$")
CASE_FAIL = re.compile(r"case (\d+) run opp failed:\s*(\S+)")


def _first_exception(plain):
    """日志里的首个真异常，返回 (类名, 原文)。全是包装异常时报第一条包装的。

    **首个而不是最后一个**：后面的多半是它的连锁反应。与构建日志那边同一条道理。
    """
    wrapped = None
    for raw in plain.splitlines():
        m = EXC_LINE.match(re.sub(r"\x1b\[[0-9;]*m", "", raw))
        if not m:
            continue
        name, body = m.group(1), m.group(2).strip()
        if any(w in name for w in WRAPPER_EXC):
            wrapped = wrapped or (name, body)
            continue
        return name, body
    return wrapped or (None, None)


def symptom_lines(log_path, limit=3):
    """日志里的失败真因。返回可直接打印的几行。

    两级：先按 KIT_SYMPTOMS 对照已知真因，连「去向」一起给；一条都没命中时**不留空**，
    退回通用摘取——首个真异常加它挂在哪条用例。

    **兜底这一级是泛化的关键。** KIT_SYMPTOMS 是在一个自带件上量出来的，换算子多半
    命中不了；没有兜底就等于把「去几百行 celery 栈里找真因」这件事原样还给执行者，
    而那正是这条链路上最贵的动作。
    """
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    out = []
    for marker, cause, fix in KIT_SYMPTOMS:
        if marker not in plain:
            continue
        for raw in plain.splitlines():
            if marker in raw:
                out.append(f"  日志    {raw.strip()[:160]}")
                break
        out.append(f"  真因    {cause}")
        out.append(f"  去向    {fix}")
        if len(out) >= limit * 3:
            break
    if out:
        return out
    name, body = _first_exception(plain)
    if not name:
        return []
    case = CASE_FAIL.search(plain)
    if case:
        out.append(f"  用例    case {case.group(1)} {case.group(2)}")
    out.append(f"  异常    {name}: {body[:160]}")
    out.append("  真因    **未匹配已知真因**，上面是日志里的首个异常")
    out.append("  去向    定位到之后把特征串补进 run_atk.py 的 KIT_SYMPTOMS，"
               "下一个人就不用再找一遍")
    return out


def reject_lines(log_path, limit=6):
    """日志里的参数校验拒绝原文，去重后按出现顺序取前几条。

    公开给 verdict.py 用：报告要把「接口层拒了」和「算错了」分开写，
    两处必须认同一套特征，不能各写各的。
    """
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    seen, out = set(), []
    for raw in text.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()
        hits = [line.index(m) for m in PARAM_REJECT_MARKERS if m in line]
        if not hits:
            continue
        line = line[min(hits):]
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _print_symptoms(log_path):
    """把认得出的真因打到屏幕上。**这是修跑循环里唯一该读的东西**——
    执行者去翻 run.log 全文的话，ATK 的 celery 栈占几百行，真因在中间。
    """
    lines = symptom_lines(log_path)
    if lines:
        print("\n日志里认得出的真因：", file=sys.stderr)
        for line in lines:
            print(line, file=sys.stderr)
        print(f"\n全文在 {log_path}。修复落 kit_fixes/，一轮只改一件事，"
              f"改完立刻重跑本命令。", file=sys.stderr)
        return
    # 走到这里说明连异常行都没有：任务没起来，或日志被截断。
    print(f"\n{log_path} 里一条异常都没有。多半是任务没起来（环境、路径、卡），"
          f"不是用例的问题。按 references/troubleshooting.md 开头的分层表定位。",
          file=sys.stderr)


def _smoke_axes(facts_path):
    """读 facts.json 的 smoke.axes：冒烟除 dtype 外还要覆盖哪几个判据轴。

    没填就只按 dtype 抽样。**填了才补**——自动枚举所有 attr 会把冒烟撑成几十条，
    那就不是冒烟了。
    """
    try:
        with open(facts_path, encoding="utf-8") as handle:
            facts_data = json.load(handle)
        axes = (facts_data.get("smoke") or {}).get("axes")
    except (OSError, ValueError):
        return []
    return [str(a) for a in (axes or [])]


def _smoke_axis_cases(allcases, axes, already):
    """每个判据轴的每个取值各取一条，返回 {"轴=取值": 用例 id}。

    取并集不取叉积：叉积会把条数撑成几十条。并集下每个取值至少被跑到一次，
    整批同因的口径问题就暴露得出来。
    """
    picked = {}
    for axis in axes:
        for case in allcases:
            value = _case_attr(case, axis)
            if value is None:
                continue
            key = f"{axis}={value}"
            if key in picked:
                continue
            picked[key] = case["id"]
    return {k: v for k, v in picked.items() if v not in already}


def _smoke(args, cases, golden, plugin, inputs=None):
    """每种 dtype 各取一条先跑一遍，把「装进去的实现根本不支持这个 dtype」
    拦在全量之前。十几条用例，几秒钟。

    为什么值这几秒：装错算子目录时 A2 的符号核查照样过（不同算子目录能实现
    同一套 aclnn 接口），要跑完全量才发现整类 dtype 全挂，而报告把它呈现成
    「精度不达标」。实测一次：全量四成用例挂，全是 GetWorkspaceSize 在接口层
    被 EZ1001 拒掉，一条都没进 kernel。
    """
    with open(cases, encoding="utf-8") as handle:
        allcases = json.load(handle)
    picked = {}
    for case in allcases:
        picked.setdefault(_case_dtype(case) or "unknown", case["id"])
    if not picked:
        print(f"{cases} 里一条用例都没有。", file=sys.stderr)
        return 3
    axis = _GROUP_ATTR or "dtype"
    # 判据轴上每个取值再各补一条。**整批同因的口径问题只在特定取值上出现**：
    # 实测一次，自带件在 beta=0 的用例里故意注入 NaN/Inf 当判据，而冒烟按 dtype
    # 取的都是 beta≠0 的用例，于是 26 分钟的全量轮跑完才发现，改完再付 26 分钟。
    extra = _smoke_axis_cases(allcases, _smoke_axes(args.facts), set(picked.values()))
    sampled = sorted(set(picked.values()) | set(extra.values()))
    print(f"冒烟抽样  {len(picked)} 种 {axis} 各 1 条："
          + "、".join(f"{d}=#{i}" for d, i in sorted(picked.items())))
    if extra:
        print("          判据轴各补 1 条："
              + "、".join(f"{k}=#{v}" for k, v in sorted(extra.items())))

    work_dir = Path("smoke")
    # 冒烟只问「这个 dtype 跑不跑得起来」，不做精度口径对齐，所以不切非连续。
    result, _, killed = _run_subset(sampled, cases, golden,
                                    args.devices, plugin, work_dir,
                                    inputs=inputs)
    if result is None:
        print(f"\n冒烟轮没出结果（{killed}）。", file=sys.stderr)
        _print_symptoms(work_dir / "run.log")
        return 2

    # **执行失败与精度不符都算冒烟没过。** 只看「跑不跑得起来」会放过整批同因的
    # 口径问题：实测两次，一次是 ATK 的归一化不覆盖 fp32↔fp64（比对阶段抛异常），
    # 一次是标杆自己注入 NaN/Inf，两次都是冒烟跑过了、全量轮才炸。
    exec_failed = set(result.get("failed_ids") or [])
    acc_false = set(result.get("accuracy_false_ids") or [])
    failed = exec_failed | acc_false
    bad = sorted(d for d, i in picked.items() if i in failed)
    bad_axes = sorted(k for k, i in extra.items() if i in failed)
    rejects = reject_lines(work_dir / "run.log")
    record = {"mode": "smoke", "sampled": dict(sorted(picked.items())),
              "sampled_axes": dict(sorted(extra.items())),
              "failed_dtypes": bad, "failed_axes": bad_axes,
              "exec_failed_ids": sorted(exec_failed),
              "accuracy_false_ids": sorted(acc_false),
              "param_reject_lines": rejects,
              "log": str(work_dir / "run.log")}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(stage_clock.stamp(record), handle, ensure_ascii=False, indent=2)
    print(f"写入      {args.out}")

    if not failed:
        print(f"冒烟通过  {len(sampled)} 条（{len(picked)} 种 {axis}"
              + (f" + {len(extra)} 个判据轴取值" if extra else "")
              + "）全部跑起来且比对通过，进 A3 全量")
        return 0

    print(f"\n冒烟失败  {len(sampled)} 条里 {len(failed)} 条没过："
          f"执行失败 {len(exec_failed)} 条、精度不符 {len(acc_false)} 条",
          file=sys.stderr)
    if bad:
        print(f"          {axis}：{'、'.join(bad)}", file=sys.stderr)
    if bad_axes:
        print(f"          判据轴：{'、'.join(bad_axes)}", file=sys.stderr)
    _print_symptoms(work_dir / "run.log")

    # **拦不拦看是不是整批同因，不看剖面。**
    #
    # 从前这里按剖面放行：npu 剖面认不出接口层拒绝的特征串，就整条闸打开
    # 无条件进全量。sparse 走的正是 npu 剖面，于是这道闸在那条路上等于不存在——
    # 两次整批同因的口径问题都是这么漏到全量轮的，每次 26 分钟。
    #
    # 判据换成条数：冒烟每种取值只有一条，**2 条以上一起挂就不是个别用例的事**，
    # 而全量轮要付 26 分钟，先停下来看一眼更便宜。孤例照常进全量——kernel 崩、
    # 超时这些正是隔离复验要测准的东西，在这里拦掉等于把一次能出结论的验收
    # 变成没有结论。
    if args.smoke_force:
        print("--smoke-force 已给，照常进 A3 全量。", file=sys.stderr)
        return 0
    if len(failed) < 2 and not rejects:
        print(f"只有 {len(failed)} 条没过，不是整批同因。照常进 A3 全量，"
              "交给隔离复验测准。", file=sys.stderr)
        return 0
    if not rejects:
        print(f"\n阻塞·未验收 @A2.5：{len(failed)} 条冒烟用例一起没过，"
              "是整批同因，不是个别用例。全量轮一轮的代价按条数算，"
              "先在这几条上把成因钉死再上全量。", file=sys.stderr)
        print("  1. 精度不符的先看比对口径：标杆与实际的 dtype 对不对得上、"
              "标杆自己有没有注入非有限值当判据。", file=sys.stderr)
        print("  2. 执行失败的先看 A2 装的是不是任务书点名的那份源码。",
              file=sys.stderr)
        print("  3. 确认是算子缺陷就照常进全量：本脚本加 --smoke-force。",
              file=sys.stderr)
        return 3
    print("aclnn 入口在 GetWorkspaceSize 就把入参挡回来了，一条都没进 kernel：",
          file=sys.stderr)
    for line in rejects:
        print(f"  {line}", file=sys.stderr)
    print("\n阻塞·未验收 @A2：装进去的实现声明不支持这些 dtype。"
          "两种可能，按顺序核：", file=sys.stderr)
    print("  1. A2 装错了算子目录。不同算子目录能实现同一套 aclnn 接口，"
          "符号核查判不出来。核 stage/install.json 的 op_dir 与 source，"
          "是不是任务书点名的那份源码。", file=sys.stderr)
    print("  2. 确实是这份源码没实现这些 dtype。那是算子缺陷，"
          "把上面的原文报给算子作者，不要改用例绕过去。", file=sys.stderr)
    return 3


def _pick_isolate_devices(args):
    """定隔离复验用哪几张卡。返回卡号列表，一张都没有时返回空（调用方退 3）。

    **必须在开跑前现查，不能沿用 A1 探到的那份。** A1 到 A3.5 之间隔着编译、
    部署、精度全量，几十分钟里别人随时会占上来；共卡的后果是把别人的 aicore
    异常算成本算子的失败，而这一层没有任何报错，只是结论错。

    显式给的卡号里有被占的，**摘掉继续**，不整轮退出：八张卡里被占一张就一条
    都不复验，比少用一张卡糟得多。
    """
    raw = (args.isolate_devices or "auto").strip()
    if raw == "auto":
        if args.allow_shared_device:
            # auto 的语义就是「只挑空的」，与 --allow-shared-device 直接冲突。
            # 静默按其中一个走会让人以为另一个生效了。
            print("--isolate-devices auto 与 --allow-shared-device 冲突：前者只挑"
                  "空闲卡，后者是明知共卡也要跑。要共卡就显式列出卡号。",
                  file=sys.stderr)
            return []
        free = probe_env.free_devices()
        if not free:
            print("现查一遍：一张空闲卡都没有。隔离复验在共卡上会把别人的 aicore "
                  "异常算成本算子的失败，所以不降级跑。", file=sys.stderr)
            for line in probe_env.device_busy(sorted(probe_env.chip_map()))[:8]:
                print(f"  {line}", file=sys.stderr)
            print("等卡空出来再跑，或显式 --isolate-devices <卡号> 配 "
                  "--allow-shared-device（结论要打折）。", file=sys.stderr)
            return []
        devices, capped = probe_env.cap_devices(
            [str(d) for d in free], getattr(args, "max_devices", None))
        print(f"空闲卡      用 {len(devices)} 张：{'、'.join(devices)}{capped}"
              f"（--isolate-devices auto；轮数 ≈ 污染型失败数 ÷ 卡数）")
        return devices

    devices = [d.strip() for d in raw.split(",") if d.strip()]
    if not devices:
        devices = [d.strip() for d in args.devices.split(",") if d.strip()]
    if args.allow_shared_device:
        return devices
    busy = _device_busy(",".join(devices))
    taken = {re.search(r"device (\d+)", line).group(1) for line in busy
             if re.search(r"device (\d+)", line)}
    if taken:
        for line in busy[:8]:
            print(f"  {line}")
        devices = [d for d in devices if d not in taken]
        print(f"摘掉被占的卡 {'、'.join(sorted(taken))}，"
              f"剩 {len(devices)} 张：{'、'.join(devices) or '无'}")
    if not devices:
        print("给的卡全被别人占着，隔离复验不在共卡上跑——那会把别人的 aicore "
              "异常算成本算子的失败。换卡，或先收掉这些进程。", file=sys.stderr)
        return []
    return devices


def _solo_recheck(failed, cases, golden, devices, plugin, slice_ratio, inputs):
    """把判失败的用例各自单独跑一遍，返回 (单独跑也挂的, 只在批内挂的, 单独跑算错的)。

    **归属判据「跑到执行才挂」有个盲区：真凶自己可以是通过的。** 算子越界写时，
    写的那条算得对、也不报错，坏掉的显存要等后面某条用例读到才炸——于是罪名
    落在受害者头上——实测存在这样的用例对：单跑与换一条前置用例都通过，换成
    特定的前一条就挂，而那一条自己一路显示通过。

    单独跑一遍就分得开：还挂就是它自己的问题，通过就是被前面某条污染的。
    每条一轮、按卡并发，十来条用例一轮就跑完，比误报一批缺陷便宜得多。

    **只对判失败的跑，不对全量跑**——全量单独跑是每条一次 atk 启动，那才是不可接受的。
    """
    if not failed:
        return failed, [], []
    print(f"\n单独复跑  {len(failed)} 条判失败的用例各自单独跑一遍，"
          f"{len(devices)} 张卡并发，分开「自己的问题」与「被前面某条污染」", flush=True)
    jobs = [(devices[i % len(devices)], [cid]) for i, cid in enumerate(sorted(failed))]

    # 每条完成打一行 `已完成 N/M`。**这一行是等待方的唯一心跳**：`wait_for.py`
    # 默认按 `(\d+)/(\d+)` 从本轮日志尾部抓进度，不打的话它整轮一个字都拿不到，
    # 100 条十来分钟里终端只有起跑抬头，看起来与卡死无法区分。
    # 打在完成时而不是派发时——派发是瞬间的，进度会一次跳满。
    progress_lock = threading.Lock()
    finished = [0]

    def _one(job):
        device, ids = job
        work_dir = Path("isolate") / "solo" / f"case{ids[0]}"
        outcome = (ids[0],) + _run_subset(ids, cases, golden, device, plugin,
                                          work_dir, slice_ratio, inputs=inputs)
        with progress_lock:
            finished[0] += 1
            print(f"已完成    {finished[0]}/{len(jobs)}", flush=True)
        return outcome

    with ThreadPoolExecutor(max_workers=min(len(jobs), len(devices))) as pool:
        outcomes = list(pool.map(_one, jobs))

    still, batch_only, wrong = [], [], []
    for case_id, result, executed, killed in outcomes:
        if result is None:
            # 单独跑连报告都没出来 = 它自己就把进程弄挂了，算它的问题。
            still.append(case_id)
            continue
        if case_id in set(result["failed_ids"]):
            still.append(case_id)
        elif case_id in set(result.get("accuracy_false_ids") or []):
            # 单独跑起来了但算错了。**不能算「批内污染」**——那会让一条真实的
            # 精度缺陷被 verdict.py 计进通过。
            wrong.append(case_id)
        else:
            batch_only.append(case_id)
    return still, batch_only, wrong


TILING_REJECTED = re.compile(
    r"MergeFunctions:op type (\S+) tiling func has been registered")


def _tiling_check(args, cases, golden, plugin, inputs=None):
    """查 ATK 实际用的是谁的 tiling：算子包自带的，还是 CANN 内置的同名实现。

    **这一步决定 A3 的结论能不能作为验收依据。** ATK 必须拉起 torch_npu，而
    torch_npu 会让 GE 先注册 CANN 内置的 tiling；GE 的合并是按 op type 先到先得，
    算子包自带那份后到就被丢弃。于是「改已有算子」这类任务里，ATK 测的是仓库里的
    旧实现，不是本次交付的代码——实测有算子因此漏报 6 条缺陷。

    换不掉：ATK 认的三个环境变量只用来定位 `libcust_opapi.so`，代码里不碰 tiling；
    `LD_PRELOAD` 装得进进程但抢不到注册；不拉 torch_npu 则 `npu` 设备类型不存在，
    ATK 跑不了。所以只能查出来、标注清楚，然后靠独立执行器补上。

    判据是 GE 自己打的一行 `MergeFunctions:op type <T> tiling func has been
    registered.`——出现它就说明该 op type 的 tiling 被拒了。
    """
    vendor_types = set()
    install = Path(args.install)
    if install.exists():
        vendor_dir = json.loads(install.read_text(encoding="utf-8")).get("vendor_dir")
        if vendor_dir:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import run_cxx
            vendor_types = run_cxx.vendor_op_types(vendor_dir)
    if not vendor_types:
        print(f"{install} 里没有 vendor_dir，或算子包下找不到 kernel——"
              f"没有它就不知道哪些 op type 是本算子包提供的。", file=sys.stderr)
        return 3

    with open(cases, encoding="utf-8") as handle:
        first = json.load(handle)[0]["id"]
    work_dir = Path("tiling")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    print(f"tiling 归属  拿用例 {first} 跑一轮，开 GE debug 日志查 tiling 注册结果")
    print(f"算子包提供   {sorted(vendor_types)}")

    # GE 的注册日志只在 debug 级别打，默认级别看不到。只跑一条，日志量可控。
    os.environ["ASCEND_GLOBAL_LOG_LEVEL"] = "0"
    os.environ["ASCEND_SLOG_PRINT_TO_STDOUT"] = "1"
    result, _, killed = _run_subset([first], cases, golden, args.devices, plugin,
                                    work_dir, slice_ratio=0.0, inputs=inputs)
    log = (work_dir / "run.log").read_text(encoding="utf-8", errors="ignore")
    rejected = set(TILING_REJECTED.findall(log))
    shadowed = sorted(rejected & vendor_types)

    record = {"mode": "tiling", "case": first, "vendor_op_types": sorted(vendor_types),
              "shadowed": shadowed, "log": str(work_dir / "run.log")}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(stage_clock.stamp(record), handle, ensure_ascii=False, indent=2)

    if shadowed:
        print(f"\n**存在同名内置 tiling**：{shadowed} 的 tiling 被 CANN 内置那份挡掉了")
        print("ATK 测的是内置实现，不是本次交付的代码。A3 的结论只能作参考，")
        print("**验收结论要以独立执行器的全量轮为准**——只有它用得上算子包自带的 tiling。")
        print(f"证据在 {work_dir}/run.log，搜 `tiling func has been registered`")
        print(f"写入      {args.out}")
        return 1
    print(f"\n没有同名内置 tiling 抢占，ATK 用的就是算子包自带那份。A3 的结论直接可用。")
    print(f"写入      {args.out}")
    return 0


def _isolate(args, cases, golden, plugin, inputs=None):
    """把失败用例各自单独跑一遍，分开「自己的问题」与「被前面某条污染」。

    **一轮，不迭代。** 每条失败用例一个作业，按卡并发，跑完就有结论。

    早先这里是「按卡切块、多轮迭代」，因为 aicore 异常会把 device context 打废、
    同批后面的用例全部在初始化阶段挂，一轮只能清到每块的第一个污染者。那套算法
    的轮数是 ⌈污染型失败数 ÷ 卡数⌉，最坏退化成一条一轮。改成一轮 solo 之后
    条数与轮数解耦：N 条失败 = N 个作业，摊到全部空闲卡上并发。

    **仍然必须做**，即使 device 不再被打废：实测存在与 context 无关的批内位置
    效应——实测存在这样的用例对：单跑通过、换一条前置用例也通过，换成特定的
    前一条就挂，而那一条自己一路显示通过。只有单独跑一遍才分得开真凶与受害者。

    **执行失败与精度不符都收**，两个维度都会有批内污染。

    代价是每条一次 `atk` 启动（实测 17~18 秒），所以**只对判失败的跑，不对全量跑**，
    并且 `--max-isolate` 是有意义的时间上限。
    """
    source = Path(args.ids_from)
    if not source.exists():
        print(f"{source} 不存在，isolate 模式要先跑 accuracy。", file=sys.stderr)
        return 3
    with open(source, encoding="utf-8") as handle:
        prior = json.load(handle)
    # **执行失败与精度不符都要复检。** 批内污染在两个维度上都会发生：前一条把
    # 显存弄脏，后一条可能直接崩，也可能跑完但算错。只收执行失败的话，精度那
    # 一类永远进不了复检、被当成真实缺陷写进报告——实测一轮里有 3 条批内精度
    # 不符的用例单独跑全部通过。
    failed_ids = sorted(set(prior.get("failed_ids") or [])
                        | set(prior.get("accuracy_false_ids") or []))

    if not failed_ids:
        print("没有失败用例，不需要隔离复验。")
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(stage_clock.stamp(
                {"mode": "isolate", "checked": 0, "exec_failures": [],
                 "cascade": [], "not_checked": [], "rounds": 0,
                 "aicore_evidence": []}), handle, ensure_ascii=False, indent=2)
        return 0

    # 0 = 不限。一轮 solo 之后成本与条数成正比（每条一次 atk 启动），
    # 所以这个截断就是时间上限；被截掉的会如实记成 not_checked / unknown。
    cap = args.max_isolate if args.max_isolate > 0 else len(failed_ids)
    checked = failed_ids[:cap]
    # **必须与被隔离的那一轮同口径。** 比例从 facts 读，与全量轮同一个来源，
    # 不由执行者在命令行上挑——挑错了测的是另一件事，而且不报错。
    slice_ratio = _slice_ratio(args.facts)
    if slice_ratio < 0:
        print("facts.json 的 non_contiguous.ratio 不在 (0, 1] 内。", file=sys.stderr)
        return 3
    devices = _pick_isolate_devices(args)
    if not devices:
        return 3
    print(f"隔离复验  {len(checked)} 条（共 {len(failed_ids)} 条失败），"
          f"一轮 solo，{len(devices)} 张卡并发（{'、'.join(devices)}），"
          f"非连续比例 {slice_ratio}（与全量同口径，布局逐条复算）", flush=True)

    # 上一次隔离复验留下的日志必须先清掉。目录名是写死的 `isolate/solo/case<id>`，
    # 不清的话两次运行的日志混在一起，而 `_aicore_evidence` 扫的是整个 `isolate/`
    # ——上一次的 EZ9999 会被当成这一次的证据，报告里就出现「本轮 0 条执行失败，
    # 但成因是 aicore 异常」这种自相矛盾的结论。
    if Path("isolate").exists():
        shutil.rmtree(Path("isolate"), ignore_errors=True)
        print("清理      删掉上一次的 isolate/（要留就先改名）")

    # 一轮 solo：每条失败用例各自单独跑一遍，按卡并发。
    # `cascade` 保留在产出里只为兼容 verdict.py 的字段契约——一轮 solo 之后
    # 「没跑到执行」这个类别不存在了，恒为空。
    real, batch_only, wrong = _solo_recheck(checked, cases, golden, devices,
                                           plugin, slice_ratio, inputs)
    cascade = []

    # aicore 只在日志里真有那几个关键词时才认领成因。**没有证据就不写 aicore**：
    # 触发隔离复验的是「有执行失败」，成因不一定是它。
    evidence = _aicore_evidence(Path("isolate"))

    record = {
        "mode": "isolate",
        "batch_only": sorted(batch_only),
        # 报告要显式列出这批，光给条数的话读报告的人无从判断失败集合可不可信。
        # `dim` 是它在**批内**挂在哪一维：exec = 跑挂了，accuracy = 跑完了但算错。
        "batch_only_detail": [
            {"id": i,
             "dim": "exec" if i in set(prior.get("failed_ids") or []) else "accuracy"}
            for i in sorted(batch_only)
        ],
        "checked": len(checked),
        "not_checked": failed_ids[cap:],
        "exec_failures": sorted(real),
        "accuracy_false": sorted(wrong),
        "cascade": sorted(cascade),
        "rounds": 1,
        "devices": devices,
        "slice_ratio": slice_ratio,
        "aicore_evidence": evidence,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(stage_clock.stamp(record), handle, ensure_ascii=False, indent=2)

    print(f"\n执行失败  {len(real)} 条：{sorted(real)[:12]}（单独跑也挂，是它自己的问题）")
    if batch_only:
        print(f"批内污染  {len(batch_only)} 条：{sorted(batch_only)[:12]}"
              f"（跑到执行才挂，但单独跑通过——挂它的是前面某条**自己没报错的**用例）")
    print(f"精度不符  {len(wrong)} 条：{sorted(wrong)[:12]}（单独跑起来了、算错了）")
    print(f"未判定    {len(record['not_checked'])} 条（`--max-isolate` 截断）")
    print(f"复跑规模  {len(checked)} 条 / {len(devices)} 张卡并发，一轮跑完")
    if evidence:
        print(f"aicore    日志里有 {'、'.join(evidence)}，失败成因是 aicore 异常")
    else:
        print("aicore    日志里没有 aicore 关键词，**失败成因未定**，"
              "报告里不要写成 aicore 异常")
    print(f"写入      {args.out}")
    return 0


# aicore 异常在日志里的关键词，见 troubleshooting.md「NPU aicore 异常」。
AICORE_KEYWORDS = ("aic-error", "DDR address out of range",
                   "Aicore kernel execute failed", "EZ9999", "EZ3002")


def _aicore_evidence(root):
    """在**本次**隔离复验的日志里找 aicore 关键词，找到哪些返回哪些。

    没有这一步的话，「有执行失败就复验」会把任何成因都叙述成 aicore——
    那是没有证据的归因。命中才认领，没命中就如实说成因未定。

    「本次」靠 `_isolate` 开跑前清掉旧的 `round*` 目录保证。轮目录名写死，
    不清就会捞到上一次运行的证据。
    """
    hits = set()
    for log in sorted(Path(root).rglob("run.log")):
        text = log.read_text(encoding="utf-8", errors="replace")
        hits |= {word for word in AICORE_KEYWORDS if word in text}
    return sorted(hits)


def _drop_custom_opp():
    """摘掉自定义算子包，让 pyaclnn 回落到 CANN 装机目录里的内置同名实现。"""
    for name in ("ATK_CUSTOM_OPP_PATH", "ASCEND_CUSTOM_OPP_PATH"):
        os.environ.pop(name, None)


def _round_timeout(n_cases):
    """整轮的总时长兜底：按条数推，不写死。

    真正的挂死由 `STALL_SECONDS` 的停滞检测在 180 秒内拦住，这个数只是它漏网时的
    兜底。**推小了比推大了糟**：跑得完的轮次被杀掉，整轮的钱白付，而报告上看不出
    是被兜底杀的还是算子挂的。
    """
    return max(TIMEOUT_FLOOR,
               int(ROUND_OVERHEAD_SECONDS + n_cases * SECONDS_PER_CASE))


def _case_count(cases_path):
    """数用例文件里有多少条，只给 `_round_timeout` 推兜底用。

    调用方手上是**文件路径**不是列表，所以条数要从文件里数。JSON 数组与
    `{"cases": [...]}` 两种形状都认；读不出来返回 0，让兜底落回 `TIMEOUT_FLOOR`,
    不因为数不出条数就把整轮拦掉。
    """
    try:
        with open(cases_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return 0
    if isinstance(data, dict):
        data = data.get("cases") or []
    return len(data) if isinstance(data, list) else 0


def _layout_of(case_id, ratio):
    """复算一条用例在 ATK 那边会不会被切成非连续。

    与 `atk/tasks/backends/backend.py:151-157` 同一个公式，同一个种子。ATK 那边
    `disable_id_seed` 为真时种子换成用例的 `default_seed`，本仓不开那个开关，
    所以种子就是用例 id。

    ratio 为 0 时一条都不切；为 1 时全切。
    """
    if ratio <= 0:
        return False
    if ratio >= 1:
        return True
    return random.Random(case_id).random() < ratio


def _layout_attr(facts_path):
    """读 facts.json 的 non_contiguous.attr / noncontiguous_values。

    **布局由谁决定跟剖面走。** aclnn 剖面下是 ATK 切（按 ratio），npu 剖面下 ATK
    不接管输入构造，布局写在用例自己的属性里（自带件的 `input_mode` 就是这种），
    这两条路上「哪几条是非连续」来自完全不同的地方。填了 attr 就按 attr 判，
    没填就按 ratio 复算。返回 (attr 名, 算非连续的取值集合)，没填返回 (None, set())。
    """
    try:
        with open(facts_path, encoding="utf-8") as handle:
            facts_data = json.load(handle)
        block = facts_data.get("non_contiguous") or {}
    except (OSError, ValueError):
        return None, set()
    attr = block.get("attr")
    if not attr:
        return None, set()
    return str(attr), set(block.get("noncontiguous_values") or [])


def _case_attr(case, name):
    """取用例某个 attr 的取值。取不到返回 None。"""
    for item in case.get("inputs", []):
        if item.get("name") == name:
            values = item.get("range_values") or []
            return values[0] if values else None
    return None


def _layout_split(cases_path, ratio, attr=None, attr_values=()):
    """按布局把用例 id 分成两组，返回 (连续 id 集合, 非连续 id 集合)。

    读用例文件而不是读报告：报告里没有布局字段（ATK 的 `slice_contiguous` 只活在
    内存对象上，见 `atk/configs/dataset_config.py:62`，不落盘），只能从用例侧算。
    """
    with open(cases_path, encoding="utf-8") as handle:
        allcases = json.load(handle)
    contig, noncontig = set(), set()
    for case in allcases:
        if attr:
            hit = _case_attr(case, attr) in attr_values
        else:
            hit = _layout_of(case["id"], ratio)
        (noncontig if hit else contig).add(case["id"])
    return contig, noncontig


def _slice_ratio(facts_path):
    """读 facts.json 定这一轮混多少非连续用例。读不到按 0.0，不猜。

    `required` 为假就是 0.0（一条都不切）；为真时取 `ratio`，没写用
    `DEFAULT_SLICE_RATIO`。ratio 不在 (0, 1] 里时按 0.0 处理并让调用方报错——
    静默按缺省跑会让报告写着「按 0.4 混」而实际不是。
    """
    try:
        with open(facts_path, encoding="utf-8") as handle:
            facts_data = json.load(handle)
        block = facts_data.get("non_contiguous") or {}
    except (OSError, ValueError):
        return 0.0
    if block.get("required") is not True:
        return 0.0
    ratio = block.get("ratio", DEFAULT_SLICE_RATIO)
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        return -1.0
    return ratio if 0 < ratio <= 1 else -1.0


def _perf_kind(facts_path):
    """读 facts.json 的 performance.kind，脚本只用它决定性能跑几轮。
    怎么比、评什么级仍归 verdict.py。读不到或取值不认识返回 None，调用方拦。"""
    try:
        with open(facts_path, encoding="utf-8") as handle:
            kind = (json.load(handle).get("performance") or {}).get("kind", "none")
    except (OSError, ValueError):
        return None
    return kind if kind in PERF_KINDS else None


def _run_round(args, cases, golden, plugin, builtin, out_path, inputs=None,
               slice_ratio=0.0, tag=""):
    """跑一轮 atk，解析报告写 out_path，返回 (退出码, result)。

    kind=builtin 的两轮都走这里，靠 builtin 分开日志名与输出文件。
    `slice_ratio` 由调用方给死，**不在这里读 facts**：基线轮与被测轮要用同一个数，
    在这里读会各读一次。`tag` 只进日志名，让两轮的日志各留一份。"""
    command = _node_command(args.mode, cases, golden, args.devices, plugin, slice_ratio,
                            inputs=inputs)
    print(f"模式      {args.mode}{'（基线轮）' if builtin else ''}")
    if slice_ratio > 0 and args.mode != "performance":
        attr, attr_values = _layout_attr(args.facts)
        contig, noncontig = _layout_split(cases, slice_ratio, attr, attr_values)
        source = f"用例的 {attr} 属性" if attr else "用例 id 复算 ATK 的切分"
        print(f"非连续    本轮 {len(noncontig)} 条非连续、{len(contig)} 条连续"
              f"（同一轮混跑，布局来自{source}）")
    print(f"命令      {' '.join(command)}")

    # 基线轮与被测轮都是 performance 模式，日志名要分开，否则后跑的覆盖先跑的。
    stem = f"{args.mode}_builtin" if builtin else args.mode
    if tag:
        stem = f"{stem}_{tag}"
    log_path = Path("evidence") / f"{stem}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    # 报告的时间下界留 1 秒余量，防文件系统时间戳取整把本轮报告排除掉。
    report_since = started - 1
    returncode, killed = _run_logged(
        command, log_path, _round_timeout(_case_count(cases)))
    if returncode is None:
        print(f"\n本轮被杀：{killed}。日志尾部：", file=sys.stderr)
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]:
            print(f"  {line}", file=sys.stderr)
        return 2, None
    elapsed = time.time() - started

    # 两轮性能都用同一份 perf/cases.json，报告目录名也一样，
    # 靠 mtime 取最新的那份。所以两轮只能一前一后跑，不能并发。
    # 加载的 so 越界时，报告本身是「正常」的——通过率甚至可能很好看。
    # 所以在解析报告之前就拦掉，别让一份测了内置算子的数据流进结论。
    # 基线轮是故意摘掉自定义包的，不验它。
    # npu 剖面没有 dlopen，日志里不会有那行；那条判据在 main() 开跑前用
    # /proc/self/maps 探过了（`_under_test_lib_ok`），这里不重复。
    if not builtin and _ACTIVE_PROFILE["symbol_check"]:
        wrong = _check_loaded_so(log_path)
        if wrong:
            vendor = Path(os.environ["ATK_CUSTOM_OPP_PATH"]).parents[2]
            print(f"\n阻塞·未验收：本轮加载的不是待验收实现。实际加载：", file=sys.stderr)
            for path in wrong:
                print(f"  {path}", file=sys.stderr)
            print(f"应当全部落在 {vendor} 下。重跑 A2，或重新 "
                  f"source evidence/env.sh 后再跑。", file=sys.stderr)
            return 2, None

    report = _newest_report(cases.stem, report_since)
    if report is None:
        print(f"\natk 退出码 {returncode}，但没生成报告。日志尾部：", file=sys.stderr)
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]:
            print(f"  {line}", file=sys.stderr)
        return 2, None

    backend, name = _load_node(golden)
    load_node = f"{backend}_{name}" if name else ""
    result = _parse_report(report, args.mode, load_node)
    result["mode"] = args.mode
    # 卡号写进结果，A5 的报告抬头才拿得到。`--devices auto` 之后卡号是脚本现挑的，
    # 人再去命令行里抄一遍必然抄错。
    result["device"] = args.devices
    result["command"] = " ".join(command)
    result["elapsed_seconds"] = round(elapsed, 1)
    result["log"] = str(log_path)
    result["returncode"] = returncode

    # 布局分组随结果落盘。**verdict 侧没有别的办法拿到这个划分**：ATK 的
    # `slice_contiguous` 只活在内存对象上（`atk/configs/dataset_config.py:62`），
    # 不进报告也不进日志。写在这里，报告与复现包读同一份，不各算各的。
    if slice_ratio > 0 and args.mode != "performance":
        attr, attr_values = _layout_attr(args.facts)
        contig, noncontig = _layout_split(cases, slice_ratio, attr, attr_values)
        result["layout"] = {
            "slice_ratio": slice_ratio,
            "attr": attr,
            "contiguous_ids": sorted(contig),
            "noncontiguous_ids": sorted(noncontig),
        }

    if args.mode == "performance":
        result["device_times"] = _device_times(result.get("statistic", []), load_node,
                                               _ACTIVE_PROFILE["node_prefix"])
        result["builtin_baseline"] = builtin
    result.pop("statistic", None)

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(stage_clock.stamp(result), handle, ensure_ascii=False, indent=2)

    print(f"\n总用例    {result['total']}")
    print(f"执行成功  {result['succeeded']}    执行失败 {result['failed']}")
    if args.mode == "performance":
        times = result.get("device_times") or {}
        samples = sorted(v["aclnn"] for v in times.values() if "aclnn" in v)
        if samples:
            print(f"Device 耗时  {len(samples)} 条，中位数 {samples[len(samples) // 2]:.2f} us，"
                  f"区间 [{samples[0]:.2f}, {samples[-1]:.2f}] us")
        print(f"性能基线  {result.get('avg_ratio') or '无（报表未给出对比比值）'}")
    else:
        print(f"精度通过  {result['matched']}    通过率 {result['pass_rate']}%")
        print(f"达标      {'是' if result['passed'] else '否'}")
        if result["accuracy_false_ids"]:
            head = result["accuracy_false_ids"][:12]
            more = "…" if len(result["accuracy_false_ids"]) > 12 else ""
            print(f"精度不符  {len(result['accuracy_false_ids'])} 条：{head}{more}"
                  f"（跑起来了但算错，与执行失败是两回事）")
    print(f"报告      {report}")
    print(f"写入      {out_path}（耗时 {elapsed:.0f}s）")

    if result["failed_ids"]:
        head = result["failed_ids"][:12]
        more = "…" if len(result["failed_ids"]) > 12 else ""
        print(f"失败用例  {head}{more}")

    if args.mode == "accuracy" and result["failed"] >= result["total"] > 0:
        # 全量**一条都没跑起来** = 部署或适配坏了，不是算子缺陷。同一个错误在
        # 隔离复验里再重复几十遍不产生新信息，所以在这里就拦掉。
        # 只挂一部分不拦：那正是要靠隔离复验测准的东西。
        print(f"\n阻塞·未验收 @A3：全量 {result['total']} 条**全部**执行失败。"
              f"部署或适配有问题，按 troubleshooting.md 分层定位，不要跑隔离复验。",
              file=sys.stderr)
        return 2, result
    return 0, result



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True,
                        choices=["smoke", "accuracy", "performance", "isolate", "tiling"])
    parser.add_argument("--op", default="",
                        help="算子名，逐字等于 facts.json 的 aclnn_name。"
                             "对不上直接退 3——这是唯一能把「跑错工作目录」"
                             "变成硬错误的办法。")
    parser.add_argument("--ids-from", default="accuracy.json",
                        help="isolate 模式从这份报告读 failed_ids")
    parser.add_argument("--max-devices", type=int, default=None,
                        help=f"auto 现查空闲卡时最多用几张，默认 "
                             f"{probe_env.MAX_AUTO_DEVICES}。机器公用，一个验收"
                             f"任务不该占满；独占机器时给 0 解开限制")
    parser.add_argument("--isolate-devices", default="auto",
                        help="隔离复验用哪几张卡，逗号分隔。默认 auto：开跑前现查"
                             "一遍全部空闲卡。**这是隔离复验唯一的提速手段**："
                             "一轮 solo 里每条失败用例是一个作业，摊到这些卡上并发，"
                             "耗时 ≈ ⌈失败数 ÷ 卡数⌉ × 一次 atk 启动。8 卡实测并发"
                             "无损耗（8 实例 31s，单实例 25s）。显式给卡号时，被别人"
                             "占着的会被摘掉并打印，一张不剩才退 3")
    parser.add_argument("--max-isolate", type=int, default=0,
                        help="isolate 模式最多复验多少条失败用例，0 不限。"
                             "**时间上限就靠这一个旋钮**：一轮 solo 之后成本与条数"
                             "成正比（每条一次 atk 启动，约 20 秒，按卡并发摊薄）。"
                             "被截掉的记成 not_checked，报告里是 unknown")
    parser.add_argument("-c", "--cases", required=True)
    parser.add_argument("--dtypes", default="",
                        help="逗号分隔，只跑这些 dtype 的用例。基线轮用："
                             "CANN 内置实现不支持的新增 dtype 没有基线可比，"
                             "过滤后要在报告里写明覆盖范围。")
    parser.add_argument("--exclude-ids", default="",
                        help="逗号分隔的用例 id，从本轮剔除。基线轮用："
                             "内置实现跑不动的用例没法比。")
    parser.add_argument("--golden", default="golden")
    parser.add_argument("--nodes", default="",
                        help="任务方自带件的 nodes yaml。给了就走自带件路："
                             "标杆当场算，不读冻结 golden，也不校验 golden 布局")
    parser.add_argument("--baseline-node", default="",
                        help="自带件路上标杆节点的 <backend>:<name>。"
                             "不给则从 --nodes 里认，认不出才要求给")
    parser.add_argument("--inputs", default="inputs",
                        help="生成侧冻结的输入张量目录，布局 <inputs>/<用例 id>/input.bin。"
                             "给了就走 ATK 的 --input_data，跳过运行时造数——**两个执行器"
                             "读到同一批字节的唯一办法**。相对路径按用例包根解析。"
                             "缺目录时退 3，不静默回落到现造")
    parser.add_argument("--facts", default="facts.json")
    parser.add_argument("--install", default="stage/install.json",
                        help="A2 的产物，--mode tiling 从里面取 vendor_dir")
    parser.add_argument("--devices", default="auto",
                        help="**一个算子占一张卡。** 默认 auto：开跑前现查一遍，"
                             "挑第一张空闲卡。并行验收多个算子时用 auto 会撞车"
                             "（两轮同时探到同一张空卡），那时给显式卡号，"
                             "取值见 env.json 的 npu.free_devices——**不是 "
                             "device_ids**，那份是物理卡号且含别人占着的卡。"
                             "共卡会静默毁掉性能结论。")
    parser.add_argument("--allow-shared-device", action="store_true",
                        help="目标卡上已有别的 atk 任务时照跑。只在明知道是"
                             "自己上一轮的残留进程时用。")
    parser.add_argument("-p", "--plugin", default=None, help="执行器，默认自动找")
    parser.add_argument("--builtin-baseline", action="store_true",
                        help="只跑基线轮：摘掉自定义算子包，让 pyaclnn 回落到 CANN "
                             "内置的同名实现。kind=builtin 时脚本本来就会自己接着"
                             "跑这一轮，这个开关只用于单独补跑它。")
    parser.add_argument("--builtin-out", default="",
                        help="基线轮的结果写哪。默认跟着 -o 走，同目录同名加 "
                             "_builtin——两个默认值各写各的时，改了 -o 忘了改它，"
                             "verdict.py 会去老地方找基线，报成「基线轮无数据」")
    parser.add_argument("--smoke-force", action="store_true",
                        help="冒烟判整批同因时照常进全量。**先把成因钉死再用它**："
                             "全量轮的代价按条数算，1000 条约半小时，"
                             "带着已知的整批同因问题跑完只会得到一份没法用的结论")
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args()
    if not args.builtin_out:
        out = Path(args.out)
        args.builtin_out = str(out.with_name(f"{out.stem}_builtin{out.suffix}"))

    mismatch = _check_op(args.op, args.facts)
    if mismatch:
        print(f"跑错工作目录了：\n{mismatch}", file=sys.stderr)
        return 3

    if args.devices.strip() == "auto":
        free = probe_env.free_devices()
        if not free:
            print("现查一遍：一张空闲卡都没有。共卡时性能结论静默作废，"
                  "隔离复验还会把别人的 aicore 异常算成本算子的失败。",
                  file=sys.stderr)
            for line in probe_env.device_busy(sorted(probe_env.chip_map()))[:8]:
                print(f"  {line}", file=sys.stderr)
            return 3
        args.devices = str(free[0])
        print(f"卡          {args.devices}（--devices auto，现查到 "
              f"{len(free)} 张空闲：{'、'.join(str(d) for d in free)}）")
    else:
        busy = _device_busy(args.devices)
        if busy and not args.allow_shared_device:
            print(f"卡 {args.devices} 上已经有 {len(busy)} 个进程占着：",
                  file=sys.stderr)
            for line in busy[:8]:
                print(f"  {line}", file=sys.stderr)
            print("一个算子占一张卡。共卡时性能结论静默作废，隔离复验还会把别人的"
                  "aicore 异常算成本算子的失败。换一张空卡（env.json 的 "
                  "npu.free_devices），或先收掉这些进程。**确认列出来的都是自己的"
                  "残留**时才用 --allow-shared-device。", file=sys.stderr)
            return 3

    # 早点 resolve：`_node_command` 不再自己 resolve，而 atk 的 --output_path
    # 相对 CWD 解析，冒烟与隔离复验都换到自己的 work_dir 里跑，相对路径会在那里
    # 指偏——**表现是 ATK 一条用例都不跑而只说「没产出报告」**，看不出是路径问题。
    cases = Path(args.cases).resolve()
    golden = Path(args.golden).resolve()
    if not cases.exists():
        print(f"{cases} 不存在。", file=sys.stderr)
        return 3
    global _NODES, _BASELINE_NODE
    if args.nodes:
        # 自带件路：标杆当场算，没有 golden 可校验。
        if not Path(args.nodes).is_file():
            print(f"{args.nodes} 不存在。", file=sys.stderr)
            return 3
        _NODES = Path(args.nodes).resolve()
        if args.baseline_node:
            backend, _, name = args.baseline_node.partition(":")
            _BASELINE_NODE = (backend, name or None)
    elif not golden.is_dir():
        print(f"{golden}/ 不存在。用例包里应该带冻结好的 golden，"
              f"跑任务方自带件时改用 --nodes <自带件的 nodes yaml>。", file=sys.stderr)
        return 3
    global _ACTIVE_PROFILE
    profile = _profile(args.facts)
    if profile is None:
        with open(args.facts, encoding="utf-8") as handle:
            bad = json.load(handle).get("backend")
        print(f"{args.facts} 的 backend={bad!r} 不在 {'/'.join(PROFILES)} 里。"
              f"被测节点用哪个 ATK 后端由它定，先修用例包。", file=sys.stderr)
        return 3
    global _GROUP_ATTR
    _ACTIVE_PROFILE = profile
    try:
        with open(args.facts, encoding="utf-8") as handle:
            _GROUP_ATTR = str(json.load(handle).get("group_attr") or "")
    except (OSError, ValueError):
        _GROUP_ATTR = ""
    print(f"执行剖面  backend={profile['atk_backend']}，"
          f"被测节点 {profile['node_prefix']}_0"
          + (f"，分档轴 {_GROUP_ATTR}" if _GROUP_ATTR else ""))

    # **模式与剖面的兼容性拦在最前面。** 放到后面的话，先报出来的是
    # 「核不到待验收实现」或「install.json 里没有 vendor_dir」——两句都指向 A2，
    # 而 A2 没有任何问题，是这一步在这条路上本来就不存在。
    if args.mode == "tiling" and not profile["standalone"]:
        print("npu 剖面不做 tiling 归属：这一步查的是 CANN 内置的同名 aclnn tiling "
              "有没有抢先注册，而这条路上没有 aclnn 接口，不存在这种竞争。"
              "跳过它直接进 A3，报告里由 verdict.py 标「不适用」。", file=sys.stderr)
        return 3

    if args.builtin_baseline:
        if args.mode != "performance":
            print("--builtin-baseline 只用于 --mode performance。", file=sys.stderr)
            return 3
        if not profile["custom_opp"]:
            print("--builtin-baseline 只用于 aclnn 剖面：它靠摘掉 opp vendor 层"
                  "回落到 CANN 内置同名实现，npu 剖面没有那一层。", file=sys.stderr)
            return 3
        _drop_custom_opp()
        print("基线轮      已摘掉自定义算子包，本轮测的是 CANN 内置实现")
    elif profile["custom_opp"]:
        if not os.environ.get("ATK_CUSTOM_OPP_PATH"):
            print("ATK_CUSTOM_OPP_PATH 没设。先 source evidence/env.sh，"
                  "否则测的是 CANN 内置的同名算子，不是待验收实现。", file=sys.stderr)
            return 3
    else:
        # npu 剖面：没有 opp vendor 层，改在开跑前用探针核一次待验收 so。
        # 这条不能省——它顶的是 aclnn 剖面里 `_check_loaded_so` 的位置。
        ok, detail = _under_test_lib_ok(args.install)
        if not ok:
            print(f"\n阻塞·未验收：核不到待验收实现。{detail}", file=sys.stderr)
            print("先 source evidence/env.sh（它设 LD_LIBRARY_PATH），"
                  "或重跑 A2。", file=sys.stderr)
            return 3
        print(f"被测实现  {detail}")

    plugin = args.plugin
    if plugin is None:
        # 在**用例包根**下找，不在 CWD 下找。CWD 是本轮的过程数据目录，
        # 用例包按只读语义单独放一份，两者不是同一个目录。
        # 用例包根 = golden 的父目录，这是契约里定死的布局。
        # 自带件路没有 golden，插件与用例摆在一起，按用例文件的目录找。
        matches = sorted(_pkg_root(golden, cases).glob("function_*.py"))
        plugin = str(matches[0]) if len(matches) == 1 else None

    # 相对路径按**用例包根**解析，与 plugin 同一套规则：CWD 是本轮的过程数据目录，
    # 用例包是只读的另一处。子集轮在自己的 work_dir 里跑，所以这里必须转成绝对路径。
    inputs = Path(args.inputs)
    if not inputs.is_absolute():
        inputs = _pkg_root(golden, cases) / inputs
    if inputs.is_dir():
        # **判据是产物在不在，不是剖面。** 剖面只决定「没有它算不算错」：
        # npu 剖面下自带件那种纯 attr 用例本来就没有 inputs/，而自产的 npu
        # 用例包声明的是真张量，S4 照样冻了——按剖面一刀切会把冻好的那份跳过，
        # 两个节点各自按 seed 现造，而这一条从来没有量具核过。
        inputs = inputs.resolve()
    elif not profile["frozen_inputs"]:
        # 用例把输入完整编码在参数里（含造数 seed）时不需要另冻，
        # ATK 的 --input_data 走的是张量字节，喂不进这种编码。
        inputs = None
        print("输入张量  由用例自身编码，不走 --input_data")
    else:
        print(f"\n{inputs}/ 不存在。没有它 ATK 会按 torch.manual_seed(用例 id) "
              f"现造输入，与冻结 golden 出自不同一次造数，比对结论不可信且不报错。",
              file=sys.stderr)
        print("回生成侧重跑 freeze_golden.py 出用例包（它同时冻 golden 与 inputs）。",
              file=sys.stderr)
        return 3
        frozen = sum(1 for d in inputs.iterdir() if (d / "input.bin").is_file())
        print(f"输入张量  {inputs}/（{frozen} 条，--input_data 直读，不现造）")

    if args.dtypes or args.exclude_ids:
        cases = _filter_cases(cases, args.dtypes, args.exclude_ids)
        if cases is None:
            return 3

    if args.mode == "isolate":
        return _isolate(args, cases, golden, plugin, inputs)

    kind = None
    if args.mode == "performance":
        kind = _perf_kind(args.facts)
        if kind is None:
            print(f"{args.facts} 读不到，或 performance.kind 不在 "
                  f"{'/'.join(PERF_KINDS)} 里。性能跑几轮由它决定，先修用例包。",
                  file=sys.stderr)
            return 3

    load_backend, load_name = _load_node(golden)
    if _NODES:
        # 自带件路：标杆节点当场算，golden 布局的两道校验都不适用。
        print(f"精度基线  {_NODES} 的 {load_backend}_{load_name or '0'} 节点当场算")
    else:
        print(f"精度基线  {golden}/{load_backend}_{load_name or '0'}/"
              f"（{'CANN 内置实现' if load_backend != 'cpu' else 'CPU torch 标杆'}）")

    # golden 目录名对不上是最常见的用例包缺陷，而 accuracy_load 拿不到标杆时
    # 要跑完一整轮才报「标杆输出为空」。一次 is_dir() 就能把它拦在开跑前。
    baseline_root = golden / f"{load_backend}_{load_name or '0'}"
    if not _NODES and not baseline_root.is_dir():
        print(f"\n{baseline_root}/ 不存在，accuracy_load 一条标杆都读不到，"
              f"跑完会报「标杆输出为空」。", file=sys.stderr)
        existing = [d.name for d in sorted(golden.iterdir()) if d.is_dir()]
        print(f"目录名来自 golden/manifest.json 的 baseline_dir；"
              f"golden/ 下现有：{'、'.join(existing) or '（没有子目录）'}", file=sys.stderr)
        print("回生成侧确认用例包，不要就地改 golden 或改 manifest.json。",
              file=sys.stderr)
        return 3

    nested = None if _NODES else _check_output_info(golden, load_backend, load_name,
                                                    _list_output(args.facts))
    if nested is not None:
        print(f"\n{nested} 的结构多套了一层 list（`[[{{...}}]]`，正确是 `[{{...}}]`）。",
              file=sys.stderr)
        print("照它跑会在执行阶段段错误，而且 ATK 不报错、只会永远停在 0/N。",
              file=sys.stderr)
        print("回生成侧重跑 freeze_golden.py 重出用例包，不要就地改 golden。",
              file=sys.stderr)
        return 3

    if args.mode == "tiling":
        return _tiling_check(args, cases, golden, plugin, inputs)

    if args.mode == "smoke":
        return _smoke(args, cases, golden, plugin, inputs)

    # 连续与非连续在**同一轮**里按比例混跑，比例由 facts.non_contiguous 决定，
    # 不由执行者挑。哪几条被切用 `_layout_of()` 复算，通过率分组报在 verdict 侧。
    #
    # 从前是拆成两轮（轮 1 全连续、轮 2 全非连续），拆的理由是「不知道切了哪些」。
    # 那个理由不成立：ATK 用用例 id 当种子，划分是确定的。而拆两轮的代价是整轮
    # 重跑，1000 条一轮 26 分钟，两轮串行必然撞 `_round_timeout()` 的上限。
    slice_ratio = _slice_ratio(args.facts) if args.mode == "accuracy" else 0.0
    if slice_ratio < 0:
        print("facts.json 的 non_contiguous.ratio 不在 (0, 1] 内。"
              "要么把 required 置为 false，要么给一个 (0, 1] 的比例。", file=sys.stderr)
        return 3
    code, _ = _run_round(args, cases, golden, plugin,
                         args.builtin_baseline, Path(args.out), inputs,
                         slice_ratio=slice_ratio)
    if code:
        return code

    if kind != "builtin" or args.builtin_baseline:
        return code

    # kind=builtin 要两轮：轮 1 待验收实现，轮 2 CANN 内置实现作基线。
    # 两轮必须是同一份用例，所以连在同一次调用里跑完，不靠执行者记得敲第二条命令。
    print("\nkind=builtin，接着跑基线轮：同一份用例，换成 CANN 内置实现。")
    _drop_custom_opp()
    print("基线轮      已摘掉自定义算子包，本轮测的是 CANN 内置实现")
    code, _ = _run_round(args, cases, golden, plugin, True, Path(args.builtin_out),
                         inputs, slice_ratio=slice_ratio)
    if code:
        print(f"\n轮 1 的耗时已经写进 {args.out}，只是基线轮没跑成。"
              f"补跑：同一条命令加 --builtin-baseline -o {args.builtin_out}。",
              file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
