"""物化并冻结输入数据，以及首轮跑测的 golden。

两个阶段用途不同，冻结拓扑也不同，不能互换：

输入冻结（默认模式）在 Phase A 做，用 CPU 单节点即可。
上调只发生在 `load_dataset` 之后的内存里，`input.bin` 不受影响。

golden 冻结（`--golden`）必须挂在**真实执行拓扑**的那次跑测上。
`opp_tasks.py:316-323` 在主节点是 aclnn 时会把 `is_save_output_info` 置 False，
刻意不保存被上调过的 output_info。用 CPU 单节点冻结会绕过这个保护，
把 fp16/bf16 上调后的 `torch.float32` 存进 output_info，
复跑时 pyaclnn 据此分配输出张量，算子在 GetWorkspaceSize 阶段就会拒绝：
`Tensor valuesOut expected dtype is DT_BFLOAT16 but found DT_FLOAT`。

因此 `--golden` 会检查产物里确实存在待验收算子后端的输出目录，拓扑不对就拒绝冻结。

冻结之后的复跑用 `--input_data` 加 `node -b cpu --task accuracy_load --output_path`，
CPU 不再执行，只提供 golden 与 output_info。


原始说明：Phase A 物化并冻结输入数据。

`atk case` 产出的是规格不是数据：张量只有分布描述符，真实张量要到执行期
由 `celery_create_dataset` 现算。这带来两个问题：

1. 「这条用例造得出数据吗」被推迟到部署之后才回答，失败还会被误读成算子缺陷。
2. 每个后端节点各自重算一遍输入，输入是否真的同一份没有证据。

ATK 原生支持消费物化数据：`--save_data input:bin` 落盘，`--input_data DIR` 消费
（`dataset_executor.py:148,159`，CLI 接线见 `base_task.py:143`）。

本脚本在 Phase A 用 CPU 单节点物化一次，把输入冻结成 `frozen_inputs/`，
并按用例记录摘要。此后所有执行一律 `--input_data` 消费同一份输入。

造不出数据的用例在这里就被剔除并留痕，不再进入部署。

冻结时顺带核两件事：基线插件是否真的跑通（这次跑测的 CPU 节点就是它），
以及单条用例的输入是否超字节预算——都是脚本自己刚产出的数据，原地核对
比留到 S3/S4 才发现更省。

退出码：0 全部物化；2 存在造不出数据、测不出错、基线执行失败或超预算的用例；
3 检查本身没跑成。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from _case_utils import iter_cases, load_json, tensor_inputs
from _runtime_guard import runtime_imports
import _stage_card

# NumPy 2.0 删掉了 np.Inf / np.NaN 等别名，值本身没变（np.Inf is np.inf）。
# ATK 26.8.8 的非有限值数据生成仍在用旧别名，环境装了 numpy>=2 时
# nan / inf / -inf 用例会在 celery_create_dataset 整批失败。
# 这里只在物化子进程里把别名补回，不改数值语义，不影响本进程与其他任何进程。
# 冻结之后的执行期不再生成数据，与 numpy 版本无关。
ALIAS_SHIM = '''
import numpy as _np
for _old, _new in (("Inf", "inf"), ("NaN", "nan"), ("NAN", "nan"),
                   ("Infinity", "inf"), ("PINF", "inf")):
    if not hasattr(_np, _old):
        setattr(_np, _old, getattr(_np, _new))
if not hasattr(_np, "NINF"):
    _np.NINF = -_np.inf
'''


# 输出不由输入算出的算子类别。这不是「豁免」，是判据的前提不成立：
# 常量输入之所以测不出错，是因为输出由输入算出（搬运类的错置换与对置换在
# 全同值输入上逐元素相等）。随机生成类的输出由随机数流决定，输入张量只
# 提供 shape——真机实测（bernoulli，2026-08-17）：任务书原话就是「输入的张量
# 用于指定 shape」，34 条用例卡在这里，而把 range 撑宽一点纯属做无用功。
# 类别本身不是自由声明：它定死了必需轴、默认交互组和比较器
# （scripts/_coverage_strategy.py 的 OPERATOR_CLASSES），改它要付整套代价。
VALUE_FREE_OUTPUT_CLASSES = frozenset({"generation"})


def constant_check_mode(operator_class):
    return ("not_applicable" if operator_class in VALUE_FREE_OUTPUT_CLASSES
            else "enforced")


def constant_tensors(payload):
    """挑出「整张只有一个取值」的输入张量，返回 (序号, dtype, 元素数)。

    这种输入测不出错：搬运类算子的输出是输入的置换，输入全同值时错的置换与
    对的置换逐元素相等，用例必然通过，却照样计入通过率分母——静默放行缺陷实现
    是验收最坏的结果。真机实测（roll，2026-08-15）曾有 17 条，占通过总数四成，
    成因是取值范围没按 dtype 定，无符号 dtype 撞上负均值整张清零。

    非有限值或 dtype 极大值构成的常量是边界用例本身要的形态，不算在内。
    全零不算边界证据：无符号 dtype 的最小值就是 0，与被清零的张量同形。
    """
    import numpy as np
    import torch

    def as_array(value):
        # torch 没有 uint32，ATK 落盘成 {"__type__": "uint32_tensor", "data": ndarray}
        if torch.is_tensor(value):
            item = value.detach().cpu()
            return (item.float() if item.dtype in (torch.bfloat16, torch.float16)
                    else item).numpy(), str(value.dtype).replace("torch.", "")
        if isinstance(value, dict) and "data" in value:
            return (np.asarray(value["data"]),
                    str(value.get("__type__", "")).replace("_tensor", ""))
        return None, None

    found, stack, index = [], list(payload) if isinstance(payload, (list, tuple)) \
        else [payload], 0
    while stack:
        item = stack.pop(0)
        array, name = as_array(item)
        if array is None:
            if isinstance(item, (list, tuple)):
                stack = list(item) + stack
            continue
        index += 1
        flat = np.asarray(array).reshape(-1)
        if flat.size < 2 or np.unique(flat).size != 1:
            continue
        if flat.dtype.kind in "fc" and not np.isfinite(flat[0]):
            continue
        if flat.dtype.kind in "iu":
            info = np.iinfo(flat.dtype)
            # 全零不算边界证据：无符号 dtype 的最小值就是 0，与被清零的张量同形。
            if flat[0] == info.max or (flat[0] == info.min and info.min != 0):
                continue
        found.append((index - 1, name, int(flat.size)))
    return found


DEFAULT_BUDGET_BYTES = 2 * 1024 ** 3

BASELINE_FAILURE = re.compile(r"case (\d+) run opp failed")


def baseline_failures(log):
    """从冻结那次跑测的日志里挑出基线执行失败的用例号。

    冻结用的是 CPU 单节点，跑的就是 agent 写的基线插件。它挂了却让
    冻结通过，等于把「基线插件写错了」这件事推迟到 S3 冒烟甚至 S4 全量
    才暴露——roll 那轮正是这样炸掉 42/81 条：冻结那次跑测基线已经全线
    失败，旧版只核输入落盘不核基线执行，门禁照样放行。
    """
    return sorted(set(BASELINE_FAILURE.findall(log)), key=int)


def human(size):
    for unit in ("B", "KB", "MB", "GB", "TB", "PB", "EB"):
        if abs(size) < 1024 or unit == "EB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}B"
        size /= 1024.0
    return f"{size}B"


def smoke_range(cases, smoke_case):
    """把冒烟用例号换算成 ATK 的 `-s/-e`，直接可抄进命令。

    reference 说「冒烟跑哪条取 frozen_inputs.json 的 smoke_case」，
    示例命令写的却是固定的 `-s 0 -e 1`，中间那步换算没人写。
    真机上 agent 为「smoke_case=1 到底对应 -s 几」翻了八次工具、
    读了 pick_smoke_case 的源码。序号是脚本手上的事实，直接给出来。

    `-e` 是排他上界，所以是 [位置, 位置+1)。
    """
    if smoke_case is None:
        return None
    for index, case in enumerate(cases):
        if str(case.get("id")) == str(smoke_case):
            return [index, index + 1]
    return None


def pick_smoke_case(cases, frozen):
    """挑一条**常规通路**用例给冒烟用，返回用例号。

    冒烟门禁要求成功 1 条、失败 0 条，所以它只能跑正常通路。
    默认拿 0 号是危险的：物化脚本习惯把定向用例排在前面，
    `expected_error_msg` 用例本来就该报错，空张量也可能被算子直接拒绝，
    两者都会让冒烟"失败"，把构建安装的几十分钟成本连带作废——
    而待验收对象其实没问题。真机上踩过（median，2026-08-14）。
    """
    for case in cases:
        case_id = str(case.get("id"))
        if case_id not in frozen or case.get("expected_error_msg"):
            continue
        shapes = [spec.get("shape") for spec in tensor_inputs(case)]
        if not shapes or any(not shape or 0 in shape for shape in shapes):
            continue
        return case_id
    return None


def fail(message, code=2):
    print(message, file=sys.stderr)
    sys.exit(code)


def numpy_needs_shim(python):
    """探测该解释器的 numpy 是否已经删掉旧别名。"""
    probe = "import numpy,sys;sys.exit(0 if hasattr(numpy,'Inf') else 3)"
    try:
        return subprocess.run([python, "-c", probe], capture_output=True).returncode == 3
    except Exception:
        return False


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def find_input_root(atk_output, since):
    """定位本轮跑测产出的输入目录。

    ATK 的布局是 <run>/input/<yaml名>/<case_id>/input.bin，
    而 --input_data 期望的正是 <yaml名> 这一层。
    """
    candidates = []
    for root, dirs, files in os.walk(atk_output):
        if "input.bin" not in files:
            continue
        case_dir = os.path.dirname(root) if False else root
        parent = os.path.dirname(case_dir)
        if os.path.getmtime(case_dir) >= since:
            candidates.append(parent)
    if not candidates:
        return None
    # 同一轮的 case 目录共享同一个父目录，取出现次数最多的那个
    return max(set(candidates), key=candidates.count)


def golden_path_conflict(baseline_kind):
    """真值来自 CANN 内置实现时，golden 不在这一步冻。

    两条路的产物形状不同：常规验收的 golden 是基线节点当场算出来的，
    冻的是 output_info 的摘要；内置真值是先单独跑一轮存盘再搬过去的目录。
    混用会冻出一份没人消费的摘要，然后拿它当真值来历写进报告。
    """
    if baseline_kind != "cann_builtin":
        return None
    return ("baseline_kind 是 cann_builtin，真值不由这一步产生。\n"
            "  → 走 capture_reference.py：内置那一轮跑完之后取证并搬运，\n"
            "     命令见 references/builtin-baseline.md#两步跑测。")


def freeze_golden(args, wanted, log):
    """冻结首轮跑测的 golden，并校验冻结拓扑正确。"""
    stage = os.path.abspath(args.frozen_dir)
    runs = [os.path.join(stage, "atk_output", name)
            for name in os.listdir(os.path.join(stage, "atk_output"))] \
        if os.path.isdir(os.path.join(stage, "atk_output")) else []
    runs = [path for path in runs if os.path.isdir(os.path.join(path, "output"))]
    if not runs:
        fail("没找到本轮的 output 产物。\n"
             "  → 确认命令里保留了 --save_data output:bin，且跑测确实执行到了比对阶段。", 3)
    output_root = os.path.join(max(runs, key=os.path.getmtime), "output")

    backends = sorted(os.listdir(output_root))
    baseline = [name for name in backends if name.startswith("cpu")]
    candidate = [name for name in backends if not name.startswith("cpu")]
    if not baseline:
        fail(f"output 下没有 cpu 节点目录（现有 {backends}），没有可用的 golden。", 3)
    if not candidate:
        fail(
            f"output 下只有 {backends}，说明这次冻结**不是**真实执行拓扑。\n"
            "  → 主节点不是 aclnn 时，ATK 会把被上调过的 output_info 一并保存"
            "（`opp_tasks.py:316-323` 的 is_save_output_info 保护不生效）。\n"
            "     用这份 golden 复跑，fp16/bf16 用例会在 GetWorkspaceSize 阶段被算子拒绝。\n"
            "     正确做法是把 --golden 挂在待验收算子后端与 cpu 同时在场的那次跑测上。", 2)

    node = baseline[0]
    case_root = os.path.join(output_root, node)
    frozen, missing, upcast = {}, [], []
    for case_id in wanted:
        found = None
        for save_name in os.listdir(case_root):
            probe = os.path.join(case_root, save_name, case_id, "output_info.json")
            if os.path.exists(probe):
                found = probe
                break
        if not found:
            missing.append(case_id)
            continue
        info = load_json(found)
        first = info[0][0] if isinstance(info[0], list) else info[0]
        frozen[case_id] = {"output_info_sha256": sha256_bytes(found),
                           "dtype": first.get("dtype")}

    report = {
        "mode": "golden",
        "case_json": args.case_json,
        "case_json_sha256": sha256_bytes(args.case_json),
        "golden_dir": output_root,
        "baseline_node": node,
        "candidate_nodes": candidate,
        "total": len(wanted),
        "frozen": len(frozen),
        "missing_ids": missing,
        "cases": frozen,
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print(f"已冻结 golden {len(frozen)}/{len(wanted)} 条（标杆节点 {node}，"
          f"待验收算子节点 {candidate}）→ {output_root}")
    print(f"摘要写入 {args.output}")
    print("复跑时加：node -b cpu --task accuracy_load --output_path " + output_root)

    if missing:
        print(f"\n✗ {len(missing)} 条用例没有 golden：{missing[:12]}", file=sys.stderr)
        return 2
    return 0


OWNER_FILE = ".frozen_owner.json"


def _owner_of(frozen_dir):
    try:
        with open(os.path.join(frozen_dir, OWNER_FILE), encoding="utf-8") as src:
            return json.load(src).get("case_json")
    except (OSError, ValueError):
        return None


def write_frozen_owner(frozen_dir, case_json):
    """记下这个冻结目录是哪份用例 JSON 的，供下一次冻结核对。"""
    with open(os.path.join(frozen_dir, OWNER_FILE), "w", encoding="utf-8") as sink:
        json.dump({"case_json": os.path.abspath(case_json)}, sink,
                  ensure_ascii=False)


def claim_frozen_dir(frozen_dir, case_json):
    """一个冻结目录只归一份用例 JSON。换了一份就停，不清空别人的。

    本函数落地前，冻结是无条件 `rmtree` 后重建的。两个接口分面先后冻结到同一个
    `frozen/`，第二次会把第一次的输入整目录删掉；而用例号从 0 自增，两边号段
    重叠——第一个分面的全量精度于是一半读到别人的输入、一半 FileNotFoundError，
    跑完还出得了报告。真机上因此白跑一整轮全量，且只是「用例数对不上」这种
    很难联想到根因的表象。

    删别人的目录不是可以补救的操作，所以这里退出码 2，让 agent 改用各自的目录。
    """
    if not os.path.exists(frozen_dir):
        return
    owner = _owner_of(frozen_dir)
    if owner is None:
        # 认不出归属的目录只可能是上一轮留下的：本轮同一份用例 JSON 重跑，
        # 或者手工建的空目录。放行，但把归属补上。
        return
    if owner == os.path.abspath(case_json):
        return
    fail(f"{frozen_dir} 已经是 {owner} 的冻结目录，本次要冻结的是 "
         f"{os.path.abspath(case_json)}。\n"
         "  冻结会清空目录重建，接着写下去会把上一个分面的输入删掉，而用例号"
         "两边重叠，\n"
         "  跑出来的精度是拿别人的输入算的——报告照出，数字全错。\n"
         "  → 每个接口分面用各自的冻结目录，比如 frozen/ 与 frozen_int8/。", 2)


def main():
    parser = argparse.ArgumentParser(description="物化并冻结输入或 golden")
    parser.add_argument("-j", "--case-json", required=True)
    parser.add_argument("--atk-cli", required=True, help="probe_env.py 探出的绝对路径")
    parser.add_argument("-d", "--frozen-dir", default="frozen_inputs",
                        help="冻结输入的落盘目录，执行期用 --input_data 指向它。"
                             "目录会被清空重建，每个接口分面必须用各自的目录")
    parser.add_argument("--atk-output", default="atk_output",
                        help="ATK 的产出根目录，用于定位本轮 input.bin")
    parser.add_argument("-p", "--plugin",
                        help="执行插件路径。CPU 基线接口名与 torch 不一致时必须传，"
                             "否则物化会在基线调用处失败")
    parser.add_argument("--golden", action="store_true",
                        help="冻结 golden。必须配合 --device 用真实执行拓扑跑一次")
    parser.add_argument("--device", help="--golden 时待验收算子节点使用的 device")
    parser.add_argument("--backend", default="pyaclnn", help="--golden 时的待验收算子后端")
    parser.add_argument("--input-data", help="--golden 时消费的已冻结输入目录")
    parser.add_argument("--budget-bytes", type=int, default=DEFAULT_BUDGET_BYTES,
                        help=f"单条用例总输入字节预算，默认 {DEFAULT_BUDGET_BYTES}（2GiB）")
    parser.add_argument("--interface", default="evidence/interface.json",
                        help="derive_interface.py 的产物；读 baseline_kind，"
                             "内置真值那条路不在这一步冻 golden")
    parser.add_argument("--must-cover",
                        help="物化后的组合表（*_materialized.json）；读 operator_class。"
                             "generation 类的输出不由输入算出，常量输入不构成「测不出错」")
    parser.add_argument("-o", "--output", default="frozen_inputs.json")
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    _stage_card.announce(__file__)

    cases = list(iter_cases(load_json(args.case_json)))
    if not cases:
        fail("用例集为空，没有可物化的用例。")
    wanted = [str(case.get("id")) for case in cases]

    started = time.time() - 1
    if args.golden:
        baseline_kind = "torch"
        if os.path.exists(args.interface):
            with open(args.interface, encoding="utf-8") as handle:
                baseline_kind = json.load(handle).get("baseline_kind", "torch")
        conflict = golden_path_conflict(baseline_kind)
        if conflict:
            fail(conflict, 2)
        if not args.device:
            fail("--golden 需要 --device：golden 必须在真实执行拓扑下冻结。", 3)
        if not args.input_data:
            fail("--golden 需要 --input-data：golden 要与已冻结的输入配对。", 3)
        stage_dir = os.path.abspath(args.frozen_dir)
        shutil.rmtree(stage_dir, ignore_errors=True)
        os.makedirs(stage_dir, exist_ok=True)
        command = [args.atk_cli,
                   "node", "-b", args.backend, "--devices", str(args.device),
                   "-o", stage_dir,
                   "node", "-b", "cpu", "-o", stage_dir,
                   "task", "-c", args.case_json, "-tk", "accuracy",
                   "--input_data", args.input_data,
                   "--save_data", "output:bin"]
    else:
        command = [args.atk_cli, "node", "-b", "cpu",
                   "task", "-c", args.case_json, "-tk", "accuracy",
                   "--save_data", "input:bin"]
    if args.plugin:
        command += ["-p", args.plugin]
    print(f"物化 {len(wanted)} 条用例，只用 CPU 后端，不判精度：")
    print("  " + " ".join(command))

    env = dict(os.environ)
    shim_dir = None
    interpreter = os.path.join(os.path.dirname(args.atk_cli), "python3")
    if os.path.exists(interpreter) and numpy_needs_shim(interpreter):
        shim_dir = tempfile.mkdtemp(prefix="atk_np_alias_")
        with open(os.path.join(shim_dir, "sitecustomize.py"), "w",
                  encoding="utf-8") as handle:
            handle.write(ALIAS_SHIM)
        env["PYTHONPATH"] = shim_dir + os.pathsep + env.get("PYTHONPATH", "")
        print("  该解释器的 numpy 已删除 np.Inf / np.NaN 别名，"
              "物化子进程内补回别名（np.Inf is np.inf，数值语义不变）。")

    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=args.timeout, env=env)
    except FileNotFoundError:
        fail(f"找不到 ATK CLI {args.atk_cli}，用 probe_env.py 输出的绝对路径。", 3)
    except subprocess.TimeoutExpired:
        fail(f"物化超过 {args.timeout}s 未结束。", 3)
    finally:
        if shim_dir:
            shutil.rmtree(shim_dir, ignore_errors=True)

    log = re.sub(r"\x1b\[[0-9;]*m", "", done.stdout + done.stderr)

    if args.golden:
        return freeze_golden(args, wanted, log)

    input_root = find_input_root(args.atk_output, started)
    if input_root is None:
        # 子进程的退出码和 stderr 以前直接丢掉，只报「没找到 input.bin」。
        # 真机上根因是 ATK 缺 pytz，命令一调就抛 ModuleNotFoundError，
        # 任务 0 秒结束、什么都没产出——而提示把 agent 指向 --atk-output，
        # 排查方向完全错，来回花了六次调用才手工复现出真正的报错。
        # 命令没跑成时，先把它自己的最后几行说出来。
        if done.returncode != 0:
            tail = [line for line in log.splitlines() if line.strip()][-8:]
            fail(f"物化命令以退出码 {done.returncode} 结束，ATK 没有跑完：\n"
                 + "\n".join(f"  {line}" for line in tail)
                 + "\n  → 先按上面的报错修环境（常见是 ATK 的依赖缺包），"
                   "再重跑冻结。", 3)
        fail("没找到本轮产出的 input.bin。\n"
             "  → 确认 --atk-output 指向 ATK 的产出根目录，"
             "且命令里保留了 --save_data input:bin（缺了它跑完即删）。", 3)

    claim_frozen_dir(args.frozen_dir, args.case_json)
    if os.path.exists(args.frozen_dir):
        shutil.rmtree(args.frozen_dir)
    shutil.copytree(input_root, args.frozen_dir)
    write_frozen_owner(args.frozen_dir, args.case_json)

    # operator_class 决定「常量输入测不出错」这条判据成不成立，见
    # VALUE_FREE_OUTPUT_CLASSES。取不到就按 enforced 走，不静默放宽。
    operator_class = None
    if args.must_cover:
        try:
            operator_class = load_json(args.must_cover).get("operator_class")
        except (OSError, ValueError) as exc:
            fail(f"读不出 --must-cover：{exc}", 3)

    frozen, missing, degenerate = {}, [], {}
    for case_id in wanted:
        path = os.path.join(args.frozen_dir, case_id, "input.bin")
        if not os.path.exists(path):
            missing.append(case_id)
            continue
        frozen[case_id] = {"sha256": sha256_file(path),
                           "bytes": os.path.getsize(path)}
        with runtime_imports("torch"):
            import torch
            payload = torch.load(path, weights_only=False)
        flat = constant_tensors(payload)
        if flat:
            degenerate[case_id] = [
                {"input_index": index, "dtype": name, "numel": numel}
                for index, name, numel in flat]

    reasons = sorted({
        line.strip()
        for line in re.findall(r"^.*(?:Error|error|removed in the NumPy)[^\n]*$", log, re.M)
        if "Traceback" not in line
    })[:6]

    over_budget = sorted(
        (case_id for case_id, item in frozen.items()
         if item["bytes"] > args.budget_bytes),
        key=int)
    failed_baseline = baseline_failures(log)

    report = {
        "case_json": args.case_json,
        "case_json_sha256": sha256_bytes(args.case_json),
        "frozen_dir": os.path.abspath(args.frozen_dir),
        "total": len(wanted),
        "materialized": len(frozen),
        "unmaterialized_ids": missing,
        "inputs": frozen,
        "constant_input_cases": degenerate,
        "constant_input_check": constant_check_mode(operator_class),
        "operator_class": operator_class,
        "over_budget_cases": over_budget,
        "budget_bytes": args.budget_bytes,
        "baseline_failed_cases": failed_baseline,
        "smoke_case": pick_smoke_case(cases, frozen),
        "smoke_range": smoke_range(cases, pick_smoke_case(cases, frozen)),
        "diagnostics": reasons,
        "numpy_alias_shim": bool(shim_dir),
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    total_bytes = sum(item["bytes"] for item in frozen.values())
    print(f"已冻结 {len(frozen)}/{len(wanted)} 条，合计 {total_bytes / 1048576:.1f} MB "
          f"→ {args.frozen_dir}")
    print(f"摘要与用例集绑定写入 {args.output}")
    print(f"此后所有执行加 --input_data {os.path.abspath(args.frozen_dir)}")
    smoke, span = report["smoke_case"], report["smoke_range"]
    print(f"冒烟用例：{smoke}（常规通路），命令里写 -s {span[0]} -e {span[1]}"
          if smoke and span else
          "冒烟用例：无常规通路用例可选，冒烟前先确认用例集是否全是定向用例")

    if failed_baseline:
        print(f"\n✗ {len(failed_baseline)} 条用例的基线执行失败："
              f"{failed_baseline[:12]}", file=sys.stderr)
        print("  → 基线插件是本轮的验收方产出，改它不受冻结约束。"
              "先修 function_<op>.py 再重跑冻结。", file=sys.stderr)
        print("  → 常见成因：用例输入带 name 时全部进 kwargs，"
              "args 恒为空；YAML 输入名必须等于基线函数形参名。", file=sys.stderr)
        return 2

    if over_budget:
        print(f"\n✗ {len(over_budget)} 条用例的单条输入超预算 "
              f"{human(args.budget_bytes)}：{over_budget[:12]}", file=sys.stderr)
        print("  → 把大规模档摊到多个维度，或把该轴组合列入 infeasible。",
              file=sys.stderr)
        return 2

    if degenerate and report["constant_input_check"] == "not_applicable":
        ids = sorted(degenerate, key=lambda value: (len(value), value))
        print(f"\n注意：{len(ids)} 条用例的输入是常量张量，本算子类别不判这一条："
              f"{ids[:12]}")
        print("  operator_class 是 generation：输出不由输入算出，由随机数流决定"
              "（scripts/_coverage_strategy.py 的 OPERATOR_CLASSES）。\n"
              "  常量输入照样能验出输出的差异，不构成「测不出错」。已记进 "
              f"{args.output} 的 constant_input_cases 备查。")
        degenerate = {}

    if degenerate:
        ids = sorted(degenerate, key=lambda value: (len(value), value))
        print(f"\n✗ {len(ids)} 条用例的输入是常量张量，测不出错：{ids[:12]}", file=sys.stderr)
        sample = degenerate[ids[0]][0]
        print(f"  例：用例 {ids[0]} 的输入 #{sample['input_index']}"
              f"（{sample['dtype']}，{sample['numel']} 元素）整张只有一个取值；\n"
              "     搬运类算子的错误置换与正确置换在这种输入上逐元素相等，用例必然通过。",
              file=sys.stderr)
        print("  → 取值范围要按 dtype 定，无符号 dtype 不能用负均值"
              "（正态采样后按 dtype 截断会整张清零）；\n"
              "     改 decl 的 range 后重跑 make_yaml.py 与 atk case，"
              "属 S12 的量具缺陷例外，重生成要留痕。", file=sys.stderr)
        return 2

    if not missing:
        return 0

    print(f"\n✗ {len(missing)} 条用例造不出数据：{missing[:12]}", file=sys.stderr)
    for reason in reasons:
        print(f"  ↳ {reason}", file=sys.stderr)
    print("  → 这是工具链或用例规格问题，不是待验收算子的缺陷。\n"
          "     属于必测集的用例必须按覆盖缺口上报，不能静默丢弃。",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
