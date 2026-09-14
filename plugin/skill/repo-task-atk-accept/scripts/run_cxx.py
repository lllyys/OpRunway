#!/usr/bin/env python3
"""不依赖 ATK 执行的精度跑测：读冻结用例，用独立 C++ 执行器跑，复用 ATK 的精度判定。

与 `run_atk.py` 的差别只有一处——**谁把算子跑起来**。用例、输入张量、golden、
精度标准、报告字段全部相同，所以两条路径的结论可比。

它比 ATK 多做一件 ATK 做不到的事：**aicore 异常之后重启进程接着跑**。ATK 的
device_run worker 是常驻单进程且不复位，一条崩了后面全连带，所以要隔离复验；
这里崩了就换个进程，一遍跑完，每条用例的结论都是它自己的。

产出 `accuracy.json`，字段与 `run_atk.py` 一致，另加 `executor`、`matched_ratio`
与 `max_abs_error`——后两个 ATK 报表里没有。
"""

import argparse
import json
import math
import os
import re
import resource
import shutil
import subprocess
import sys
from pathlib import Path

# 非连续切片的开关与比例**只有一份真源**，从跑测侧那个脚本取。
# 两处各写一个 0.3 的下场，本仓在规模档阈值上已经吃过一次。
import probe_env
from run_atk import SLICE_RATIO, _non_contiguous

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "standalone_runner"


def apply_slicing(values, case_id, slice_ratio):
    """按 ATK 跑测时的同一套规则把输入切成非连续视图，返回切后的参数列表。

    **两侧口径必须逐步对齐，差一步就是测了另一个布局。** ATK 在
    `backend.set_input_dataset` 里先用 `random.Random(case_id)` 抽一次决定切不切
    （`rng.random() < ratio`），再拿**同一个** rng 交给 `apply_slice_input` 去选
    张量和轴。抽的次数和顺序错一次，选中的张量就换了一个，而两边都不会报错。

    切与不切由 `case_id` 定、与批次无关，所以离线重放拿得到同一个布局。
    真正的切法调 ATK 自己的 `apply_slice_input`，不重写。
    """
    import random
    from atk.configs.dataset_config import InputDataset

    rng = random.Random(case_id)
    if slice_ratio is not None and rng.random() >= slice_ratio:
        return values, False
    dataset = InputDataset()
    dataset.args = list(values)
    dataset.apply_slice_input("non_contiguous", rng=rng)
    return list(dataset.args), True

# 母仓全部算子的 GetWorkspaceSize 签名只用得到这些类型（扫 35 个签名实测）。
# **不在表里就停下**：猜一个最接近的会让 C++ 侧测的不是同一个调用。
CTYPE_TO_KIND = {
    "aclTensor*": "tensor",
    "aclScalar*": "scalar",
    "aclIntArray*": "intarray",
    "aclBoolArray*": "boolarray",
    "aclFloatArray*": "floatarray",
    "aclTensorList*": "tensorlist",   # 认得出，但**没实现**，见 UNSUPPORTED_KINDS
    "int64_t": "int",
    "int32_t": "int",
    "double": "double",
    "float": "double",
    "bool": "bool",
    "char*": "str",
}

# 认得出类型、但 runner 侧没有构造它的代码。**分开列是有意的**：认不出的类型
# 报「不在支持表里」，让人去补表；这些是表里有、实现没跟上，报错要说清是后者，
# 不然读的人会去改一张已经对了的表。
# 张量列表要按元素各自建 aclTensor 再组装，还要逐元素对齐非连续切片的口径，
# 工作量与其余九种不是一个量级；在补上之前这类算子只走 ATK。
UNSUPPORTED_KINDS = {
    "tensorlist": "张量列表（aclTensorList*）要逐元素建张量再组装，runner 侧还没实现",
}

# 走整数路径的标量 dtype。浮点与 bool 之外的都在这里——`int(value)` 而不是
# `float(value)`，否则 `3` 会写成 `3.0`，runner 那边 `std::stoll` 直接抛。
INT_SCALARS = {"int8", "int16", "int32", "int64",
               "uint8", "uint16", "uint32", "uint64", "bool"}

# **描述符里的 dtype 名一律用 torch 词表**（`TORCH_TO_NAME` 那一套），因为 runner
# 的 `toAclType` 只认这一套，认不出就 `exit(4)` 把整个进程打死。用例 JSON 里写的
# 是 ATK 词表（`fp32` / `fp64` / `bf16`），两者对不上——实测把 ATK 名直接写进
# 描述符，第一条用例就让 runner 退出，169 条全部报 `no_output`。
ATK_TO_DESC = {
    "fp64": "float64", "fp32": "float32", "fp16": "float16", "bf16": "bfloat16",
    "double": "float64", "float": "float32",
}


def desc_dtype(name):
    """把用例声明的 dtype 名翻成描述符用的名字。认不出就原样返回，由 runner 报错。"""
    return ATK_TO_DESC.get(name, name)

KIND_TO_MACRO = {
    "tensor": "A_TENSOR", "scalar": "A_SCALAR", "intarray": "A_INTARRAY",
    "boolarray": "A_BOOLARRAY", "floatarray": "A_FLOATARRAY",
    "tensorlist": "A_TENSORLIST", "int": "A_INT", "double": "A_DOUBLE",
    "bool": "A_BOOL", "str": "A_STR",
}

TORCH_TO_NAME = {
    "torch.float32": "float32", "torch.float": "float32",
    "torch.float16": "float16", "torch.half": "float16",
    "torch.bfloat16": "bfloat16", "torch.float64": "float64",
    "torch.double": "float64", "torch.int64": "int64", "torch.long": "int64",
    "torch.int32": "int32", "torch.int": "int32", "torch.int16": "int16",
    "torch.int8": "int8", "torch.uint8": "uint8", "torch.bool": "bool",
    "torch.complex64": "complex64", "torch.complex128": "complex128",
    # 无符号三兄弟：`runner.cpp` 的 `toAclType` 一直认得它们，缺的是这张表。
    # 缺了的后果不是报错而是**整类 dtype 被跳过**——`dtype_name` 返回 None，
    # `write_descriptor` 报「dtype 没映射」，跳过的用例不进报告，报告里那一行
    # 还照样按 ATK 轮的数打 ✓。实测 Roll 的 uint32 17 条全军覆没。
    "torch.uint16": "uint16", "torch.uint32": "uint32", "torch.uint64": "uint64",
}


def restore_uint(obj):
    """还原 ATK 存盘时对 uint16/32/64 张量做的包装。

    ATK 的 `torch_save_safe` 存盘前先过 `sanitize_data`：torch 这三种无符号 dtype
    没法直接 round-trip，于是存成
    `{"__type__": "uint32_tensor", "data": ndarray, "shape": ...}`
    （`atk/common/utils.py`，torch >= 2.3 才有这一段）。**冻结的
    `inputs/<id>/input.bin` 与 `golden/**/output_*.pt` 两处都经过它**，
    少还原任何一处，这类 dtype 就整类跑不了。

    **调 ATK 自己的 `restore_data`，不要手写解这个 dict**：它是 ATK 的私有约定，
    手写一份等于把约定复制进本仓，上游改了这边不会知道。老版本 ATK 没有这个
    函数时原样返回，退化成改动前的行为。
    """
    try:
        from atk.common.utils import restore_data
    except ImportError:
        return obj
    return restore_data(obj)


def _snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def find_header(op_dir, aclnn_name):
    """算子的 aclnn 头文件。签名是唯一权威来源，不从别处推。"""
    candidates = sorted(Path(op_dir).rglob(f"aclnn_{_snake(aclnn_name)}.h"))
    if not candidates:
        candidates = [p for p in sorted(Path(op_dir).rglob("op_api/aclnn_*.h"))
                      if f"aclnn{aclnn_name}GetWorkspaceSize" in p.read_text(errors="ignore")]
    return candidates[0] if candidates else None


def parse_signature(header, aclnn_name):
    """从头文件取 GetWorkspaceSize 的参数类型表，去掉固定的尾部两个。

    返回 (类型列表, 报错原因)。**认不出就返回原因，不猜**。
    """
    text = re.sub(r"//.*|/\*.*?\*/", " ", header.read_text(errors="ignore"), flags=re.S)
    text = " ".join(text.split())
    symbol = f"aclnn{aclnn_name}GetWorkspaceSize"
    at = text.find(symbol + "(")
    if at < 0:
        return None, f"{header} 里没有 {symbol}"
    depth, end = 0, -1
    for i in range(at + len(symbol), len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None, f"{symbol} 的参数表没闭合"
    params = [p.strip() for p in text[at + len(symbol) + 1:end].split(",") if p.strip()]
    if len(params) < 3:
        return None, f"{symbol} 只有 {len(params)} 个参数，尾部两个是固定的，不合法"
    tail = " ".join(params[-2:])
    if "workspaceSize" not in tail or "aclOpExecutor" not in tail:
        return None, f"{symbol} 的尾部两个参数不是 (uint64_t*, aclOpExecutor**)：{tail}"

    kinds = []
    for param in params[:-2]:
        ctype = param.replace("const", "").strip()
        ctype = re.sub(r"\s+\w+$", "", ctype).strip()      # 去掉形参名
        ctype = ctype.replace(" ", "")
        if ctype not in CTYPE_TO_KIND:
            return None, (f"{symbol} 的参数类型 `{ctype}` 不在支持表里"
                          f"（原文 `{param}`）。C++ 执行器跑不了这个算子，"
                          f"用 --executor atk。")
        kinds.append(CTYPE_TO_KIND[ctype])
    blocked = sorted({k for k in kinds if k in UNSUPPORTED_KINDS})
    if blocked:
        why = "；".join(UNSUPPORTED_KINDS[k] for k in blocked)
        return None, (f"{symbol} 用到 {'、'.join(blocked)}：{why}。"
                      f"这个算子只能走 ATK，把这句话照实写进报告的 A3.5 那栏——"
                      f"**不是「独立执行器未复现」，是根本没跑**。")
    return kinds, None


def gen_op_call(kinds, header, aclnn_name, dest):
    """按签名生成唯一随算子变化的那份代码。十来行，肉眼可核。"""
    args = ", ".join(f"{KIND_TO_MACRO[k]}({i})" for i, k in enumerate(kinds))
    dest.write_text(
        f'/* 由 run_cxx.py 按 {header.name} 的签名生成，不要手改。 */\n'
        f'#include "{header.name}"\n\n'
        f'#define OP_GET_WORKSPACE_SIZE(ws, ex) \\\n'
        f'    aclnn{aclnn_name}GetWorkspaceSize({args}, ws, ex)\n\n'
        f'#define OP_EXECUTE(w, ws, ex, s) aclnn{aclnn_name}(w, ws, ex, s)\n\n'
        f'/* dlsym + dladdr 用它证明符号连的是待验收实现，不是 CANN 内置同名实现。 */\n'
        f'#define OP_WS_SYMBOL_NAME "aclnn{aclnn_name}GetWorkspaceSize"\n',
        encoding="utf-8")
    return args


def tensor_blob(tensor):
    """按 stride 能触到的最远元素取出这个视图的连续内存，返回 (bytes, nbytes, span)。

    `span` 是从 `data_ptr` 起、stride 能触到的最远元素数。**不能按 numel 算**：
    非连续视图的 stride 会跨出去，少拷的部分算子照读不误，读到的是别的数据，
    而且不报错。它同时是描述符里 `storage_shape` 的取值，见 `write_descriptor`。
    """
    import torch
    if tensor.numel() == 0:
        return b"", 0, 0
    span = 1 + sum((s - 1) * st for s, st in zip(tensor.shape, tensor.stride()))
    flat = torch.as_strided(tensor, (span,), (1,))
    raw = flat.contiguous().view(torch.uint8).numpy().tobytes()
    return raw, len(raw), span


def dtype_name(dtype):
    return TORCH_TO_NAME.get(str(dtype))


def storage_shape(tensor, sliced):
    """照抄 ATK 的 storage shape 规则，**不能自己另定一套**。

    出处两处：`atk/tasks/api_execute/aclnn_base_api.py:128` 决定形状，
    `atk/tasks/backends/lib_interface/acl_wrapper.py:312` 决定为 None 时的兜底。

    | 情形 | storage shape |
    | --- | --- |
    | 没切成非连续 | **view 形状本身** |
    | 切成非连续 | `[shape[0] * 2] + shape[1:]`——ATK 就是开 2 倍存储取步长 2 造的视图 |
    | 0 维 | 跟 view 一样，退化成空 |

    早先这里填的是「stride 能触到的最远元素数」的一维形状。对连续张量它描述的是
    同一块内存，可**自定义 kernel 会读 storage shape 去算 tiling**，拿到一维形状就
    算出退化结果、一个字节都不写。症状是输出全零，不报错。CANN 内置 kernel 不看
    这个字段，所以换条 opp 路径又「好了」，更难查。
    **改这里之前先回去读上面两个出处。**
    """
    shape = list(tensor.shape)
    if not shape:
        return []
    if not sliced:
        return shape
    return [shape[0] * 2] + shape[1:]


def scalar_dtypes(case):
    """用例里每个 `type: scalar` 入参声明的 dtype，按 inputs 里的下标返回。

    **必须按声明的 dtype 建标量,不能一律 float64。** ATK 用
    `create_acl_scalar(value, data_type)` 按用例声明的 dtype 调 `aclCreateScalar`
    （`atk/tasks/backends/lib_interface/acl_wrapper.py`）；这边写死 float64 就是
    在调另一个 aclnn 重载。实测踩过：整数 dtype 的用例，ATK 按声明传触发 aicore
    异常、这边传 float64 跑通，两个执行器对同一条用例给出相反结论，而两边都不报错。
    """
    out = {}
    for index, item in enumerate(case.get("inputs") or []):
        if isinstance(item, dict) and item.get("type") == "scalar":
            out[index] = item.get("dtype")
    return out


def write_descriptor(case_id, values, out_infos, kinds, dest, sliced=False,
                     scalar_types=None):
    """把一条用例写成行式描述符 + 输入裸数据。返回 (输出规格列表, 报错原因)。

    `sliced` 是**这条用例**有没有被切成非连续。ATK 的口径是用例级的：一旦切了，
    每个入参张量都按 `[shape[0]*2] + shape[1:]` 报 storage，不只被切的那个
    （`atk/tasks/api_execute/aclnn_base_api.py:136`）。出参不走这条，见 `storage_shape`。
    """
    import torch
    lines = [f"CASE {case_id}"]
    out_specs = []
    if len(values) != len(kinds):
        return None, (f"用例 {case_id} 有 {len(values)} 个参数，"
                      f"签名要 {len(kinds)} 个（输入 {len(values) - len(out_infos)} "
                      f"+ 输出 {len(out_infos)}）")

    for index, (kind, value) in enumerate(zip(kinds, values)):
        if kind == "tensor":
            if not isinstance(value, torch.Tensor):
                return None, f"用例 {case_id} 第 {index} 个参数按签名是 tensor，实际是 {type(value).__name__}"
            name = dtype_name(value.dtype)
            if name is None:
                return None, f"用例 {case_id} 第 {index} 个参数的 dtype {value.dtype} 没映射"
            view = ",".join(map(str, value.shape)) or "-"
            stride = ",".join(map(str, value.stride())) or "-"
            if index >= len(kinds) - len(out_infos):
                # 出参只要形状与显存，**不落输入盘**：它的初值由 runner 清零，
                # 把 torch.empty 的未初始化内容写出去只是白占几百 MB。
                _, nbytes, _span = tensor_blob(value)
                out_name = f"case{case_id}_out{index}.bin"
                # 出参从不参与切片，storage 就是 view 形状。
                store = storage_shape(value, sliced=False)
                storage = ",".join(map(str, store)) or (view or "-")
                lines.append(f"OUT tensor {name} {view} {stride} {storage} {nbytes} {out_name}")
                out_specs.append({"file": out_name, "dtype": str(value.dtype),
                                  "shape": list(value.shape),
                                  "stride": list(value.stride()), "nbytes": nbytes})
            else:
                raw, nbytes, _span = tensor_blob(value)
                fname = f"case{case_id}_arg{index}.bin"
                store = storage_shape(value, sliced=sliced)
                storage = ",".join(map(str, store)) or (view or "-")
                # **显存要按 storage 形状给够，不是按 span。** 声明 storage 68 个元素
                # 却只申请 span 的 67 个，kernel 照 storage 访问就越界；申请是我们这边
                # 的事，ATK 那侧张量本来就是按 storage 建的（实测 storage_numel=68）。
                nbytes = max(nbytes, math.prod(store) * value.element_size())
                # 文件按 nbytes 补齐：runner 的 loadToDevice 读不满就判 short read。
                (dest / fname).write_bytes(raw.ljust(nbytes, b"\x00"))
                lines.append(f"IN tensor {name} {view} {stride} {storage} {nbytes} {fname}")
        elif kind == "intarray":
            items = ",".join(str(int(v)) for v in value) if len(value) else "-"
            lines.append(f"IN intarray {items}")
        elif kind == "floatarray":
            items = ",".join(repr(float(v)) for v in value) if len(value) else "-"
            lines.append(f"IN floatarray {items}")
        elif kind == "boolarray":
            items = ",".join("1" if v else "0" for v in value) if len(value) else "-"
            lines.append(f"IN boolarray {items}")
        elif kind == "scalar":
            # 声明缺失时才退回 float64，并且这条要能在报告里看见——静默退回
            # 等于把「测的不是同一个调用」这件事藏起来。
            name = desc_dtype((scalar_types or {}).get(index) or "float64")
            body = (repr(int(value)) if name in INT_SCALARS
                    else repr(float(value)))
            lines.append(f"IN scalar {name} {body}")
        elif kind == "int":
            lines.append(f"IN int {int(value)}")
        elif kind == "double":
            lines.append(f"IN double {float(value)!r}")
        elif kind == "bool":
            lines.append(f"IN bool {1 if value else 0}")
        elif kind == "str":
            lines.append(f"IN str {value}")
        else:
            return None, f"用例 {case_id} 第 {index} 个参数类型 {kind} 还没实现"
    lines.append("END")
    (dest / f"case{case_id}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_specs, None


MISSING_INCLUDE = re.compile(r"([\w./+-]+\.h): No such file or directory")


def _build_env(**extra):
    """构建期用的环境：**摘掉 `LD_PRELOAD`**，再叠加 extra。

    调用方可能为了换 tiling 归属而 `LD_PRELOAD` 某个 CANN 库，那是**给 runner 用的**。
    `g++` 和 `git` 继承了它就起不来（实测 `g++: error while loading shared libraries:
    libascendalog.so`），于是 `_repo_root` 拿不到母仓根、自动补 `-I` 一个目录都找不到，
    最后表现成「编译失败：aclnn_util.h 找不到」——与 preload 毫无字面关联，很难查。
    """
    env = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"}
    env.update(extra)
    return env


def _repo_root(op_dir):
    got = subprocess.run(["git", "-C", str(op_dir), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=False,
                         env=_build_env())
    return Path(got.stdout.strip()) if got.returncode == 0 else Path(op_dir)


def build_runner(work, header_dir, vendor_dir, cann_home, op_dir, parent_repo=None):
    """编译一次，之后所有用例共用这个二进制。**不是每条用例编一次。**

    算子头会 include 母仓的内部头（`aclnn_util.h` 之类），而这些头在母仓里的位置
    随仓库而变——ops-cv 放 `common/stub/op_api/` 与 `third_party/opbase/include/aclnnop/`，
    别的仓不一定。**按目录形状猜路径是本仓踩过的坑**（见 CLAUDE.md「构建落点靠观察」），
    所以这里不猜：编译一次，让编译器报缺哪个头，去母仓里找它，加上 -I 再编，
    最多 8 轮。加了哪些目录会打出来。

    `LC_ALL=C` 是必要的：本地化之后 gcc 的报错文本不含 `No such file or directory`，
    正则匹配不上，就退化成第一次编不过就放弃。
    """
    shutil.copy2(ASSETS / "runner.cpp", work / "runner.cpp")
    binary = work / "runner"
    # 两处都要找：算子目录自己的仓，以及 A2 用的母仓。**裸算子目录不在任何 git
    # 仓里时前者退化成算子目录本身**，而 `aclnn_util.h` 这类内部头只在母仓里，
    # 于是第一次编不过就放弃。实测 Roll 卡在这里。母仓路径取自 install.json，
    # 是 A2 真正构建时用的那个，不猜。
    roots = [_repo_root(op_dir)]
    if parent_repo and Path(parent_repo).is_dir():
        parent = Path(parent_repo).resolve()
        if parent not in roots:
            roots.append(parent)
    includes = [f"{cann_home}/include", str(header_dir), str(work)]
    env = _build_env(LC_ALL="C")
    added = []

    for _ in range(8):
        command = ["g++", "-std=c++17", "-O2", str(work / "runner.cpp"),
                   "-o", str(binary)]
        command += [f"-I{p}" for p in includes]
        # **-lcust_opapi 必须排在 -lopapi 前面。** 两者导出同名的
        # aclnn<Op>GetWorkspaceSize，链接器取先出现的那份；顺序反了就静默连到
        # CANN 内置实现，测的不是待验收算子。runner 启动时用 dladdr 打出真实落点，
        # 本脚本按那一行断言——顺序只是第一道，断言才是硬的。
        lib_dir = Path(vendor_dir) / "op_api" / "lib"
        if lib_dir.is_dir():
            command += [f"-L{lib_dir}", "-lcust_opapi", f"-Wl,-rpath,{lib_dir}"]
        command += [f"-L{cann_home}/lib64", "-lascendcl", "-lnnopbase", "-lopapi",
                    "-ldl"]
        result = subprocess.run(command, capture_output=True, text=True,
                                check=False, env=env)
        if result.returncode == 0:
            return binary, result, " ".join(command), added

        missing = MISSING_INCLUDE.findall(result.stderr)
        if not missing:
            return binary, result, " ".join(command), added
        grew = False
        for name in dict.fromkeys(missing):
            # **包含名带路径时要退到它的父目录。** `#include "opdev/platform.h"`
            # 匹配到 `<X>/opdev/platform.h`，要加的 -I 是 `<X>` 而不是 `<X>/opdev`，
            # 少退一层的话下一轮还是同一条报错，8 轮跑满仍编不过。
            depth = len(Path(name).parts) - 1
            # 取路径最短的那份：母仓里同名头常有多份（stub 与 third_party 各一），
            # 深的那些多半是第三方的副本。找不到就让下一轮把原始报错交出去。
            hits = sorted((h for r in roots for h in r.rglob(name)),
                          key=lambda p: len(p.parts))
            for hit in hits:
                incdir = str(hit.parents[depth])
                if incdir not in includes:
                    includes.append(incdir)
                    added.append(incdir)
                    grew = True
                    break
        if not grew:
            return binary, result, " ".join(command), added
    return binary, result, " ".join(command), added


CASE_LINE = re.compile(r"^CASE (\d+) (OK|FAIL) (\S+) (-?\d+)$")
BOUND_LINE = re.compile(r"^BOUND (\S+)$", re.MULTILINE)


def runtime_env(vendor_dir):
    """跑测要的环境：`ASCEND_CUSTOM_OPP_PATH` 直接取 vendor 目录，**不探不猜**。

    正确值是确定的，三处一致：算子包自带的 `bin/set_env.bash` 写的是它、ATK 用的是它、
    真机实测也是它。早先这里维护过一张「哪个对随算子变」的候选表，起因是某个算子在
    vendor 目录下输出全零——那是**没装算子包的 tiling 库**，不是路径的问题。
    根因修掉之后候选表就没有存在理由了，一并删掉。

    opp 根不行的原因也查清了：它下面没有 `vendors/config.ini`（CANN 装机目录里那份
    有 `load_priority=...`），自定义 vendor 不会被加载，kernel 静默回落到内置。

    返回 `(env, opp 路径)`。
    """
    env = dict(os.environ)
    vendor = Path(vendor_dir) if vendor_dir else None
    if not vendor or vendor.parent.name != "vendors":
        return env, env.get("ASCEND_CUSTOM_OPP_PATH", "")
    lib = vendor / "op_api" / "lib"
    if lib.is_dir():
        env["LD_LIBRARY_PATH"] = f"{lib}:{env.get('LD_LIBRARY_PATH', '')}"
    env["ASCEND_CUSTOM_OPP_PATH"] = str(vendor)
    tiling = optiling_so(vendor)
    if tiling:
        env["RUNNER_OPTILING_SO"] = str(tiling)
    return env, str(vendor)


def optiling_so(vendor_dir):
    """算子包自带的 tiling 库。找不到返回 None。

    **kernel 与 tiling 必须成套。** 只装 kernel 不装 tiling，aclnn 照跑、退出码 0，
    输出却是错的——实测见过全批只写几十个元素，装上之后与 golden 逐位相同。
    ATK 那边是 torch_npu 初始化时顺带 dlopen 了它，所以两条路径的结论会莫名其妙对不上。

    **反过来也要当心**：`ASCEND_CUSTOM_OPP_PATH` 指着 vendor 目录时，GE 会自己装
    算子包的 tiling，不设 `RUNNER_OPTILING_SO` **并不等于用内置 tiling**。要真的换成
    内置，得 `LD_PRELOAD` CANN 的 `opp/built-in/.../op_tiling/.../liboptiling.so`
    抢在前面注册。日志里 `<op>_tiling.cpp:` 那几行的模块标签是判据：
    `[OPS_NN]` = 算子包自带，`[OP_PROTO]` = CANN 内置。

    包里可能有两份：老式的 `op_tiling/liboptiling.so` 与新式的
    `op_tiling/lib/linux/<arch>/libcust_opmaster_rt2.0.so`。**要的是老式那份**——
    实测 ATK 装的就是它，装它之后结果与 ATK 一致。
    """
    if not vendor_dir:
        return None
    base = Path(vendor_dir) / "op_impl" / "ai_core" / "tbe" / "op_tiling"
    hit = base / "liboptiling.so"
    return hit if hit.is_file() else None


SELECTED_BIN = re.compile(r"Available bin for op (\S+) is (\S+?)\.?(?=\s|$)")


def vendor_op_types(vendor_dir):
    """算子包**自己提供**哪些 op type，取自 kernel 目录里 `.o` 的文件名前缀。

    断言只能管这些。算子内部会调 `StridedSlice`、`Transpose` 这类辅助算子，
    它们的 kernel 本来就该来自 CANN 内置——把它们一并要求在包里，断言永远不过。
    """
    root = Path(vendor_dir) / "op_impl" / "ai_core" / "tbe" / "kernel"
    return {path.name.split("_")[0] for path in root.rglob("*.o")} if root.is_dir() else set()


def check_kernel_source(binary, desc_dir, device, case_id, vendor_dir, env):
    """证明**跑起来的 kernel 二进制**来自待验收算子包，不是 CANN 内置那份。

    `BOUND` 断言盖不住这一层：一个自定义算子包分三块，各由不同机制选中，
    每块都有内置同名件，任意一块没接上就静默回落：

    | 组成 | 谁决定 | 断言 |
    | --- | --- | --- |
    | aclnn 接口 so | 链接顺序 | `check_binding` |
    | **kernel 二进制** | `ASCEND_CUSTOM_OPP_PATH` | **本函数** |
    | tiling 库 | 谁 dlopen 它 | runner 启动时装，装不上退 3 |

    实测代价：`ASCEND_CUSTOM_OPP_PATH` 填成 opp 根（下面没有 `vendors/config.ini`）时，
    kernel 静默回落到 `opp/built-in/`，fp32 逐位正确、uint8 直接 `Cannot find bin`——
    看着像「待验收实现不支持 uint8」，其实那批根本没测到待验收实现。

    判据取自 CANN 自己的日志行 `Available bin for op <X> is <path>`，
    只在这一次探针调用里开 `ASCEND_GLOBAL_LOG_LEVEL`，正式跑测不开（日志五万行起）。
    """
    # 必须 0（DEBUG）。`Available bin` 由 OP 模块打，级别 1 带不出来——实测。
    # 一次探针约五万行、几 MB，只在这一次调用开，正式跑测不开。
    probe_env_vars = dict(env, ASCEND_SLOG_PRINT_TO_STDOUT="1",
                          ASCEND_GLOBAL_LOG_LEVEL="0")
    proc = subprocess.run([str(binary), str(desc_dir), str(device), str(case_id)],
                          capture_output=True, text=True, timeout=300, check=False,
                          env=probe_env_vars)
    hits = {(m.group(1), m.group(2))
            for m in SELECTED_BIN.finditer(proc.stdout + proc.stderr)}
    if not hits:
        return None, None
    mine = vendor_op_types(vendor_dir)
    ours = sorted((op, path) for op, path in hits if op in mine)
    fell_back = [(op, path) for op, path in ours if not path.startswith(str(vendor_dir))]
    if fell_back:
        return ours, (
            f"这些 op type 由待验收算子包提供，跑起来的 kernel 却不是包里那份：\n"
            + "".join(f"  {op} → {path}\n" for op, path in fell_back)
            + "  测的是 CANN 内置实现，不是待验收算子。\n"
              "  核 ASCEND_CUSTOM_OPP_PATH 是不是指到 vendor 目录本身；"
              "指到 opp 根时，那下面要有 vendors/config.ini 才认得出 vendor。")
    if not ours:
        return None, None
    return ours, None


def check_binding(binary, desc_dir, device, case_id, vendor_dir, env):
    """证明 aclnn 符号连的是待验收实现，不是 CANN 内置的同名实现。

    **这不是可选的诊断，是判据。** 待验收算子包与 CANN 装机目录导出同一套
    `aclnn<Op>GetWorkspaceSize`，连错了不报错，只是测的对象换了个人——本仓为这类
    静默失真吃过两次亏（A2 的源码身份打印、跑测轮的 so 落点断言都是为它加的）。
    实测：链接顺序把 -lopapi 放前面时，5 条用例全报
    `EZ1001 ... should be in dtype support list [DT_FLOAT,...]`，
    看着像待验收实现不支持 uint8，其实调的是 CANN 内置那份。
    """
    proc = subprocess.run([str(binary), str(desc_dir), str(device), str(case_id)],
                          capture_output=True, text=True, timeout=120, check=False,
                          env=env)
    hit = BOUND_LINE.search(proc.stdout)
    if not hit:
        return None, "runner 没打出 BOUND 行，拿不到符号落点"
    where = hit.group(1)
    if vendor_dir and not where.startswith(str(vendor_dir)):
        return where, (f"aclnn 符号连到了 {where}，不在待验收算子包 {vendor_dir} 下。\n"
                       f"  跑下去测的是 CANN 内置的同名算子，不是待验收实现。")
    return where, None


def _no_core_dump():
    """给 runner 子进程关掉 core dump。**只影响我们拉起的进程,不动系统配置。**

    runner 每次退出都被 SIGABRT 杀掉——CANN 在 teardown 里写坏 host 堆,glibc
    检测到就 abort。判定行在这之前已经打印并 flush,结果不受影响,但内核会为
    每一次退出转储一份 19~69 MB 的 core。实测:开着转储 3~13 秒一条、偶发到
    166 秒,关掉稳定 2 秒;一轮 252 条约少写 10 GB 到系统盘。
    """
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run_all(binary, desc_dir, device, ids, timeout, env):
    """跑完所有用例，进程被打废就重启接着跑。

    **这是相对 ATK 的唯一实质差别。** ATK 的 worker 崩了不复位不重启，一条
    aicore 异常之后同批全连带，只能靠隔离复验倒推；这里重启一次接着跑，
    每条用例的结论都是自己跑出来的，没有「连带」这个概念。
    """
    remaining = list(ids)
    status, restarts = {}, 0
    while remaining:
        command = [str(binary), str(desc_dir), str(device)] + [str(i) for i in remaining]
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, check=False, env=env,
                              preexec_fn=_no_core_dump)
        seen = []
        for line in proc.stdout.splitlines():
            hit = CASE_LINE.match(line.strip())
            if not hit:
                continue
            case_id, verdict, stage, ret = hit.groups()
            status[int(case_id)] = {"ok": verdict == "OK", "stage": stage,
                                    "ret": int(ret)}
            seen.append(int(case_id))
        progressed = [i for i in remaining if i in seen]
        remaining = [i for i in remaining if i not in seen]
        if not progressed:
            # 一条都没判出来：不是用例的问题，是二进制起不来或卡死。
            # **把 runner 的 stderr 打到屏幕上**，不要只塞进 JSON——描述符里有
            # 认不出的 dtype 时 runner 直接 `exit(4)`，整批报 no_output，
            # 而真正的原因只有那一行 stderr 说得清。实测踩过：169 条全灭，
            # 屏幕上只有「exec_failed 169」，得翻 JSON 才知道是 dtype 名对不上。
            tail = proc.stderr.strip().splitlines()[-3:]
            print(f"\n整批 {len(remaining)} 条一条都没判出来，runner 没出任何结果。"
                  f"这不是用例的问题，是二进制起不来或立刻退出：", file=sys.stderr)
            for line in tail:
                print(f"  {line}", file=sys.stderr)
            for case_id in remaining:
                status[case_id] = {"ok": False, "stage": "no_output", "ret": 0,
                                   "stderr": proc.stderr.strip()[-400:]}
            break
        if remaining:
            restarts += 1
    return status, restarts


def judge(cases, golden_root, baseline, save_name, desc_dir, out_specs, status):
    """复用 ATK 的比较器，口径与 `--executor atk` 完全一致。

    **这是本脚本唯一 import atk 的地方。** 比较器不碰 device、不碰 celery，
    只吃两个张量（实测 7/7 与报表一致）。换 ATK 版本时只有这个函数要看。
    """
    import torch
    from atk.configs.case_config import CaseConfig
    from atk.tasks.post_process.mixed_tolerance_benchmark_compare import (
        MixedToleranceBenchmarkAccuracyCompare as Comparator)

    by_id = {int(c["id"]): c for c in cases}
    results = {}
    for case_id, state in status.items():
        entry = {"executed": state["stage"] not in ("parse", "no_output"),
                 "stage": state["stage"], "ret": state["ret"]}
        if not state["ok"]:
            entry["verdict"] = "exec_failed"
            results[case_id] = entry
            continue

        case = by_id.get(case_id)
        comparator = Comparator(CaseConfig(**case))
        passed, details = True, []
        for index, spec in enumerate(out_specs[case_id]):
            gold_path = (golden_root / baseline / save_name / str(case_id)
                         / f"output_{index}.pt")
            if not gold_path.exists():
                entry["verdict"] = "no_golden"
                passed = False
                break
            gold = restore_uint(torch.load(gold_path, weights_only=False))
            if isinstance(gold, (list, tuple)):
                gold = gold[0]
            gold = torch.flatten(gold)

            raw = (desc_dir / spec["file"]).read_bytes()
            dtype = getattr(torch, spec["dtype"].replace("torch.", ""))
            flat = torch.frombuffer(bytearray(raw), dtype=torch.uint8).view(dtype)
            actual = torch.flatten(torch.as_strided(
                flat, tuple(spec["shape"]), tuple(spec["stride"])))

            acc = comparator.compute_accuracy_result(actual, gold, f"output_{index}.pt")
            diff = torch.abs(actual.to(torch.float64) - gold.to(torch.float64))
            details.append({
                "output": index,
                "result": bool(acc.result),
                "error_info": acc.error_info,
                # ATK 报表里没有这两个数，独立执行器手里正好有两个张量，顺手算出来。
                "max_abs_error": float(diff.max()) if diff.numel() else 0.0,
                "mean_abs_error": float(diff.mean()) if diff.numel() else 0.0,
            })
            passed = passed and bool(acc.result)
        if "verdict" not in entry:
            entry["verdict"] = "passed" if passed else "accuracy_false"
        entry["outputs"] = details
        results[case_id] = entry
    return results


def _outputs_all_zero(desc_dir, out_specs, status):
    """跑通的那些用例，输出文件是不是逐字节全零。"""
    checked = False
    for case_id, state in status.items():
        if not state["ok"]:
            continue
        for spec in out_specs.get(case_id, []):
            path = desc_dir / spec["file"]
            if not path.exists():
                continue
            checked = True
            if any(path.read_bytes()):
                return False
    return checked


def _pick_device(spec):
    """定用哪张卡。auto 就现查一张空闲的，否则取给的第一个卡号。拿不到返回 None。

    现查而不是读 A1 的 env.json：A3.5 跑在精度全量之后，中间隔着几十分钟。
    """
    spec = (spec or "").strip()
    if spec != "auto":
        return spec.split(",")[0]
    free = probe_env.free_devices()
    if not free:
        print("现查一遍：一张空闲卡都没有。共卡时别人的 aicore 异常会算成本算子"
              "的失败，所以不降级跑。", file=sys.stderr)
        for line in probe_env.device_busy(sorted(probe_env.chip_map()))[:8]:
            print(f"  {line}", file=sys.stderr)
        return None
    print(f"卡        {free[0]}（--devices auto，现查到 {len(free)} 张空闲）")
    return str(free[0])


def _round_argv(args, slice_input, out, work):
    """重建某一轮子进程的参数。

    **逐字段列出，不拿 `sys.argv[1:]` 改写**：原始 argv 里可能压根没有 `-o` 或
    `--work`（用的是默认值），改写就要先判断在不在，漏一个就是两轮写进同一个
    文件、后一轮盖掉前一轮，而且不报错。
    """
    argv = ["--op", args.op, "-c", args.cases, "--golden", args.golden,
            "--facts", args.facts, "--install", args.install,
            "--devices", args.devices, "--timeout", str(args.timeout),
            "--slice-input", slice_input, "-o", out, "--work", work]
    for flag, value in (("--inputs", args.inputs), ("--ids", args.ids),
                        ("--ids-from", args.ids_from)):
        if value:
            argv += [flag, value]
    return argv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--op", required=True, help="算子名，逐字等于 facts.json 的 aclnn_name")
    parser.add_argument("-c", "--cases", default="../input/cases.json")
    parser.add_argument("--golden", default="../input/golden")
    parser.add_argument("--inputs", default="",
                        help="冻结的输入目录，默认取用例包根下的 inputs/")
    parser.add_argument("--facts", default="../input/facts.json")
    parser.add_argument("--install", default="stage/install.json",
                        help="A2 的产物，取 op_dir 与 vendor_dir")
    parser.add_argument("--devices", default="auto",
                        help="用哪张卡，只收一个卡号。默认 auto：现查一遍挑第一张"
                             "空闲卡。共卡时别人的 aicore 异常会算成本算子的失败")
    parser.add_argument("--ids", default="", help="只跑这些用例 id，逗号分隔；默认全量")
    parser.add_argument("--ids-from", default="",
                        help="从一份 accuracy.json 里取失败用例 id，做失败复核")
    parser.add_argument("--slice-input", choices=("auto", "on", "off"), default="auto",
                        help="非连续切片。auto 读 facts.json 的 non_contiguous.required，"
                             "与 run_atk.py 同一个判据")
    parser.add_argument("--noncontig-out", default="",
                        help="非连续轮的结果写哪。默认跟着 -o 走，同目录同名加 "
                             "_noncontig。--slice-input auto 且任务书要求非连续时"
                             "自动跑这一轮")
    parser.add_argument("--work", default="cxx", help="描述符与二进制落在哪")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("-o", "--out", default="accuracy_cxx.json")
    args = parser.parse_args()

    cases_path, golden_root = Path(args.cases), Path(args.golden)
    if not cases_path.exists():
        print(f"{cases_path} 不存在。", file=sys.stderr)
        return 3
    cases = json.loads(cases_path.read_text(encoding="utf-8"))

    facts = json.loads(Path(args.facts).read_text(encoding="utf-8")) \
        if Path(args.facts).exists() else {}
    if facts.get("aclnn_name") and facts["aclnn_name"] != args.op:
        print(f"--op {args.op} 与 {args.facts} 的 aclnn_name="
              f"{facts['aclnn_name']!r} 不符。", file=sys.stderr)
        return 3

    # **剖面拦在最前面。** 这个执行器按 aclnn 两段式拼调用：先 GetWorkspaceSize
    # 拿 workspace，再调主函数。npu 剖面没有那套接口，往下走会在解析签名时
    # 报「找不到符号」——报错指向算子工程，而工程没有任何问题。
    if str(facts.get("backend") or "aclnn") == "npu":
        print("npu 剖面不跑独立执行器：它按 aclnn 两段式接口拼调用"
              "（GetWorkspaceSize + 主函数），这条路上没有那套接口，拼不出来。"
              "报告里这一条标「不适用（非 aclnn 接口）」，由 verdict.py 自动写。",
              file=sys.stderr)
        return 3

    inputs_dir = Path(args.inputs) if args.inputs else golden_root.parent / "inputs"
    if not inputs_dir.is_dir():
        print(f"\n{inputs_dir}/ 不存在。C++ 执行器读的是**生成侧冻下来的输入**，"
              f"不自己造数。", file=sys.stderr)
        print("用例包是老版本（生成侧没冻输入）时，回生成侧重跑 freeze_golden.py；"
              "或者改用 --executor atk。", file=sys.stderr)
        return 3

    install = json.loads(Path(args.install).read_text(encoding="utf-8")) \
        if Path(args.install).exists() else {}
    op_dir = install.get("op_dir")
    if not op_dir:
        print(f"{args.install} 里没有 op_dir。先跑 A2 的 build_install.py。",
              file=sys.stderr)
        return 3

    header = find_header(op_dir, args.op)
    if header is None:
        print(f"{op_dir} 下找不到 aclnn{args.op} 的头文件。签名是 C++ 执行器的"
              f"唯一权威来源，找不到就跑不了。", file=sys.stderr)
        return 3
    kinds, why = parse_signature(header, args.op)
    if kinds is None:
        print(f"\n{why}", file=sys.stderr)
        return 4

    manifest_path = golden_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) \
        if manifest_path.exists() else {}
    baseline = manifest.get("baseline_dir", "cpu_0")
    # golden 的子目录名是**用例文件的基名**（ATK 的 result_process 就这么定的），
    # 不是写死的 "cases"。子集要换目录不换文件名，写死会在跑子集时一条都找不到。
    save_name = cases_path.stem

    # **两种内存布局各跑一轮，与 A3 同规矩**，两个通过率分开报、不合并。
    #
    # 只跑一轮的后果是静默的：有竞争 tiling 时独立执行器是**唯一有效**的验收
    # 路径，它没跑的那个布局就没有任何一轮测过待验收实现——ATK 那轮测的是被抢走
    # tiling 的内置实现。实测 Roll 漏掉的正是连续轮，报告上一个字都看不出来。
    #
    # 两轮各起一个进程：描述符、二进制、日志各自独立，任一轮崩掉不影响另一轮的
    # 结论。代价是多编译一次（几十秒）。子进程显式带 `--slice-input`，不会再
    # 走进这个分支。
    if args.slice_input == "auto" and _non_contiguous(args.facts):
        base = Path(args.out)
        other = (Path(args.noncontig_out) if args.noncontig_out
                 else base.with_name(f"{base.stem}_noncontig{base.suffix}"))
        worst = 0
        for which, out_path, work_dir in (("off", base, args.work),
                                          ("on", other, f"{args.work}_noncontig")):
            name = "连续" if which == "off" else "非连续"
            print(f"\n===== 布局轮次：{name} -> {out_path} =====", flush=True)
            code = subprocess.run(
                [sys.executable, str(Path(__file__).resolve())]
                + _round_argv(args, which, str(out_path), work_dir)).returncode
            if code != 0:
                print(f"{name}轮退出码 {code}", file=sys.stderr)
                worst = worst or code
        return worst

    work = Path(args.work)
    desc_dir = work / "desc"
    if desc_dir.exists():
        shutil.rmtree(desc_dir)
    desc_dir.mkdir(parents=True, exist_ok=True)

    wanted = None
    if args.ids_from:
        prior = json.loads(Path(args.ids_from).read_text(encoding="utf-8"))
        wanted = sorted(set(prior.get("failed_ids", []) +
                            prior.get("exec_failed_ids", []) +
                            prior.get("accuracy_false_ids", [])))
        print(f"失败复核  从 {args.ids_from} 取到 {len(wanted)} 条")
    elif args.ids:
        wanted = [int(x) for x in args.ids.split(",") if x.strip()]

    print(f"签名      aclnn{args.op}GetWorkspaceSize {kinds}  ← {header}")
    print(f"输入      {inputs_dir}/（生成侧冻结，与 ATK 读的是同一份）")

    slice_input = (_non_contiguous(args.facts) if args.slice_input == "auto"
                   else args.slice_input == "on")
    print(f"非连续切片  {'开' if slice_input else '关'}"
          f"（{'facts.non_contiguous' if args.slice_input == 'auto' else '--slice-input ' + args.slice_input}）"
          f"，**必须与 ATK 那轮同口径，否则测的是另一个内存布局**")

    import torch
    out_specs, prepared, skipped, sliced_ids = {}, [], [], []
    for case in cases:
        case_id = int(case["id"])
        if wanted is not None and case_id not in wanted:
            continue
        blob = inputs_dir / str(case_id) / "input.bin"
        info = golden_root / baseline / save_name / str(case_id) / "output_info.json"
        if not blob.exists() or not info.exists():
            skipped.append((case_id, "缺冻结输入" if not blob.exists() else "缺 output_info"))
            continue
        values = list(restore_uint(torch.load(blob, weights_only=False)))
        if slice_input:
            values, sliced = apply_slicing(values, case_id, SLICE_RATIO)
            if sliced:
                sliced_ids.append(case_id)
        infos = json.loads(info.read_text(encoding="utf-8"))
        if not isinstance(infos, list):
            infos = [infos]
        for item in infos:
            dtype = getattr(torch, item["dtype"].replace("torch.", ""))
            shape, stride = tuple(item["shape"]), tuple(item.get("stride") or ())
            values.append(torch.empty_strided(shape, stride, dtype=dtype)
                          if stride else torch.empty(shape, dtype=dtype))
        specs, why = write_descriptor(case_id, values, infos, kinds, desc_dir,
                                      sliced=case_id in sliced_ids,
                                      scalar_types=scalar_dtypes(case))
        if specs is None:
            skipped.append((case_id, why))
            continue
        out_specs[case_id] = specs
        prepared.append(case_id)

    if skipped:
        print(f"跳过      {len(skipped)} 条：")
        for case_id, why in skipped[:8]:
            print(f"          {case_id}: {why}")
        if len(skipped) > 8:
            print(f"          ……只列了前 8 条，其余 {len(skipped) - 8} 条见输出 json 的 skipped")
    if not prepared:
        print("\n一条用例都准备不出来，上面每条都写了原因。", file=sys.stderr)
        return 3

    cann_home = os.environ.get("ASCEND_TOOLKIT_HOME", "")
    if not cann_home:
        print("ASCEND_TOOLKIT_HOME 没设，先 source evidence/env.sh。", file=sys.stderr)
        return 3
    call = gen_op_call(kinds, header, args.op, work / "op_call.inc")
    print(f"生成      {work}/op_call.inc —— 唯一随算子变化的代码，肉眼可核：")
    print(f"          aclnn{args.op}GetWorkspaceSize({call}, ws, ex)")
    binary, build, command, added = build_runner(
        work, header.parent, install.get("vendor_dir", ""), cann_home, op_dir,
        install.get("parent_repo"))
    if added:
        print(f"补 -I     找到 {len(added)} 个内部头的目录（算子目录所在仓 + "
              f"install.json 记的母仓）：")
        for path in added:
            print(f"          {path}")
    if build.returncode != 0:
        print(f"\n编译失败：\n{command}\n", file=sys.stderr)
        print(build.stderr.strip()[-2500:], file=sys.stderr)
        return 2
    if slice_input:
        print(f"切成非连续  {len(sliced_ids)}/{len(prepared)} 条"
              f"（切哪条由 random.Random(case_id) 定，与 ATK 同一套）")
    print(f"编译      {binary}（一次编译，{len(prepared)} 条用例共用）")

    device = _pick_device(args.devices)
    if device is None:
        return 3
    vendor_dir = install.get("vendor_dir", "")
    # 断言用的探针用例：随便第一条就行，三道断言问的都是「装对没有」，与用例无关。
    probe = prepared[0]
    env, opp_root = runtime_env(vendor_dir)
    print(f"算子包      ASCEND_CUSTOM_OPP_PATH={opp_root or '（没设）'}"
          f"（取自 install.json 的 vendor_dir，不探不猜）")
    tiling = env.get("RUNNER_OPTILING_SO")
    if tiling:
        print(f"tiling 库  {tiling}（与 kernel 成套，缺它结果错且不报错）")
    else:
        print("tiling 库  算子包里没有 op_tiling/liboptiling.so，用的是 CANN 内置 tiling。"
              "\n          **kernel 若来自待验收包，两者不配套会静默算错**，"
              "结论只能当参考")
    bound, why = check_binding(binary, desc_dir, device, probe,
                               install.get("vendor_dir", ""), env)
    if why:
        print(f"\n{why}", file=sys.stderr)
        return 3
    print(f"符号落点  {bound}")

    kernels, why = check_kernel_source(binary, desc_dir, device, probe,
                                       install.get("vendor_dir", ""), env)
    if why:
        print(f"\n{why}", file=sys.stderr)
        return 3
    if kernels:
        print(f"kernel 落点  {'、'.join(op for op, _ in kernels)} 的 kernel 实测在"
              f"待验收算子包下（用例 {probe}，取自 CANN 的 Available bin 日志；"
              f"算子内部调的 StridedSlice/Transpose 等辅助算子来自内置，不在断言范围）")
    else:
        print("kernel 落点  没能从 CANN 日志里认出本包的 op type，**这一层没验到**——"
              "\n            结论只能当参考，别据此给算子作者报缺陷")

    status, restarts = run_all(binary, desc_dir, device, prepared, args.timeout, env)
    print(f"执行      {len(status)} 条，进程重启 {restarts} 次"
          f"（每次重启对应一条把 device 打废的用例）")

    results = judge(cases, golden_root, baseline, save_name, desc_dir, out_specs, status)

    # 判执行失败的再各自单独跑一遍：还挂就是它自己的问题，通过就是被前面某条
    # **自己没报错的**用例污染的。判据「谁 sync 失败谁负责」有这个盲区——
    # 真凶自己可以是通过的，坏掉的显存要等后面某条读到才炸，罪名落在受害者头上。
    # 判失败的通常十来条，一条一次进程、几秒钟。
    #
    # **复跑必须换一张现查的空闲卡。** 全量轮刚在这张卡上崩过几次，接着在同一张
    # 卡上复跑，通过的用例也会被判成「单独跑也挂」，于是批内污染被误报成真实缺陷。
    # 实测同一条用例：跑完全量的那张卡上复跑挂，换现查空闲卡 3/3 通过。
    # 判失败的再各自单独跑一遍，分开「自己的问题」与「被同批前面某条污染」。
    #
    # **执行失败与精度不符都要收。** 批内污染在两个维度上都会发生：前一条把
    # 显存弄脏，后一条可能直接崩，也可能跑完但算错。只收执行失败的话，精度那
    # 一类永远进不了复检，会被当成真实缺陷写进报告——实测有用例批内精度不符、
    # 单独跑 3/3 通过。
    #
    # **复跑必须换一张现查的空闲卡。** 全量轮刚在这张卡上崩过几次，接着在同一张
    # 卡上复跑，通过的用例也会被判成「单独跑也挂」，于是批内污染被误报成真实缺陷。
    # 实测同一条用例：跑完全量的那张卡上复跑挂，换现查空闲卡 3/3 通过。
    blamed = sorted(cid for cid, e in results.items()
                    if e["verdict"] in ("exec_failed", "accuracy_false"))
    batch_only = []
    if blamed:
        # 只调 `_pick_device("auto")` 不够：它按空闲列表的第一张挑，而全量轮跑完
        # 那张就又空闲了，于是原样挑回来。必须显式排除。
        others = [d for d in (probe_env.free_devices() or []) if str(d) != str(device)]
        solo_device = str(others[0]) if others else device
        note = (f"卡 {solo_device}（现查空闲，已排除全量轮的卡 {device}）" if others
                else f"卡 {device}（**没有别的空闲卡，只能复用全量轮那张**；"
                     f"刚崩过的卡会把通过的用例判成「单独跑也挂」，"
                     f"这一轮的 exec_failures 要打折看）")
        print(f"单独复跑  {len(blamed)} 条判失败的各自单独跑一遍，{note}")
        solo_status = {}
        for cid in blamed:
            one, _ = run_all(binary, desc_dir, solo_device, [cid], args.timeout, env)
            if cid in one:
                solo_status[cid] = one[cid]
        # 单独跑完要**重新判一次精度**，不能只看跑没跑起来——那样精度类的复检等于没做。
        solo_results = judge(cases, golden_root, baseline, save_name, desc_dir,
                             out_specs, solo_status)
        for cid in blamed:
            solo = solo_results.get(cid)
            if solo is None:
                continue
            results[cid]["batch_verdict"] = results[cid]["verdict"]
            results[cid]["solo_verdict"] = solo["verdict"]
            results[cid]["solo_device"] = solo_device
            if solo["verdict"] == "passed":
                batch_only.append(cid)
                results[cid]["verdict"] = "batch_only"
            else:
                results[cid]["verdict"] = solo["verdict"]
        if batch_only:
            print(f"          {len(batch_only)} 条单独跑通过 -> 改判「批内污染」："
                  f"{batch_only[:12]}")
            print(f"          **这些不是缺陷**，挂它们的是同批前面某条自己没报错的用例。"
                  f"剩下 {len(blamed) - len(batch_only)} 条单独跑也没过，是它自己的问题。")
        else:
            print(f"          {len(blamed)} 条单独跑也没过，归属无误")

    # 跑通了、却每条输出都是全零 = kernel 根本没执行，不是算子算错。
    # 两者在报表上长得一模一样（都进 accuracy_false），不点破就会去查算子实现，
    # 而真正的原因在环境。实测触发条件：ASCEND_CUSTOM_OPP_PATH 指到了 vendor 目录
    # 而不是 opp 根。
    ran = [e for e in results.values() if e["verdict"] in ("passed", "accuracy_false")]
    if (ran and all(e["verdict"] == "accuracy_false" for e in ran)
            and _outputs_all_zero(desc_dir, out_specs, status)):
        print(f"\n{len(ran)} 条全部跑通、且输出**逐字节全零**——kernel 没执行，"
              f"不是算子算错。", file=sys.stderr)
        print(f"  ASCEND_CUSTOM_OPP_PATH 现在是 {opp_root}", file=sys.stderr)
        print("  它必须指 opp 根（含 vendors/ 的那一层），指到 vendors/<name> 时"
              "host 侧照常返回 0、workspace 也算得出来，只是没有 kernel 可跑。",
              file=sys.stderr)
        return 3

    buckets = {}
    for entry in results.values():
        buckets[entry["verdict"]] = buckets.get(entry["verdict"], 0) + 1
    passed = buckets.get("passed", 0)
    total = len(results)
    record = {
        "executor": "cxx",
        "op": args.op,
        "total": total,
        "matched": passed,
        "pass_rate": round(passed / total * 100, 2) if total else 0.0,
        "passed": total > 0 and passed == total,
        "restarts": restarts,
        "slice_input": slice_input,
        "sliced_ids": sorted(sliced_ids),
        "buckets": buckets,
        "exec_failed_ids": sorted(i for i, e in results.items()
                                  if e["verdict"] == "exec_failed"),
        "accuracy_false_ids": sorted(i for i, e in results.items()
                                     if e["verdict"] == "accuracy_false"),
        # 批内挂、单独跑通过的。**不是缺陷，但报告要显式列出来**——只给个条数的话
        # 读报告的人无从判断这一轮的失败集合可不可信。`dim` 记的是它在批内挂在
        # 哪一维：exec = 跑挂了，accuracy = 跑完了但算错。
        "batch_only": [
            {"id": i,
             "dim": "exec" if e.get("batch_verdict") == "exec_failed" else "accuracy",
             "batch_stage": e.get("stage"),
             "batch_ret": e.get("ret"),
             "solo_device": e.get("solo_device")}
            for i, e in sorted(results.items()) if e["verdict"] == "batch_only"
        ],
        "skipped": [{"id": i, "why": w} for i, w in skipped],
        # **跑了多大范围只有这里知道，必须落账。** 下游若按「cxx 条数 >= ATK 条数」
        # 反推，一旦有用例被跳过就会把全量误标成「仅 ATK 判失败的」——实测 Roll
        # 222 vs 239 就是这么标反的。
        "scope": ("仅 ATK 判失败的" if args.ids_from
                  else "仅指定 id" if args.ids else "全量"),
        "signature": {"symbol": f"aclnn{args.op}GetWorkspaceSize",
                      "header": str(header), "kinds": kinds},
        "cases": {str(i): e for i, e in sorted(results.items())},
    }
    Path(args.out).write_text(json.dumps(record, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"\n通过      {passed}/{total}（{record['pass_rate']}%）")
    for name, count in sorted(buckets.items()):
        print(f"  {name:16s} {count}")
    print(f"写入      {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
