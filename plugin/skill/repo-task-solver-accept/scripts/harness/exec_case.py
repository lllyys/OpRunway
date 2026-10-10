#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""exec_case.py —— harness 的 S2 执行段（Python 侧）：编译执行件、起子进程、归类失败。

一个 case 一个子进程（Q4 裁定）。子进程崩溃、超时、算子报错都只杀掉它自己，
判定父进程照常收证据——这就是「崩溃隔离」要的东西。

编译一次，多 case 复用：`compile_executor` 对公开头编译 harness_exec.cpp，
编译宏按**交付头实际声明**的算子生成（op_abi.available_ops），头里没有的算子一行
都不编进去。编译依赖四项（PROBE §6.2）：

| 类别 | 具体 |
| --- | --- |
| 头（被测） | `<repo>/include/cann_ops_solver{,_common}.h` |
| 头（ACL） | `$ASCEND_HOME_PATH/<arch>-linux/include` |
| 库（被测） | `<repo>/build/libops_solver.so` |
| 库（ACL） | `$ASCEND_HOME_PATH/lib64/libascendcl.so` |

语言标准 C++17，普通 g++ 即可——`src/` 才被 `set_source_files_properties(... LANGUAGE ASC)`，
`test/*/CMakeLists.txt` 用的是 CXX。

失败归类按退出码与信号查表，不靠解析日志文本。「三键」指被测输出
`{out32, info, status}`（协议见 `evidence.py` 模块头）：

| 归类 | 判据 | 是否算执行失败 |
| --- | --- | --- |
| `ok` | 退 0 | 否 |
| `spec_error` / `io_error` | 退 10 / 11 | 是（harness 自己的问题，不是被测） |
| `unsupported` | 退 12 | 是 |
| `acl_error` | 退 13 | 是 |
| `op_error` | 退 14 | 是（被测返回非成功） |
| `rerun_mismatch` | 退 15 | 否——算子跑完了，确定性结论独立出（见下） |
| `crash` | 被信号杀（退出码 < 0） | 是 |
| `timeout` | 超 `--timeout` | 是 |
| `protocol_error` | 退 0 但三键读不回来（缺件、元素数与声明形状不符） | 是 |
| `unknown` | 其它退出码 | 是 |

`protocol_error` 是唯一不由退出码定的归类：子进程自报成功、三键却读不成结构时，
这份输出不能当数值证据用——归执行协议错误，不记 `ok`。

**确定性结论独立于数值结论**：复跑失配时算子本身跑完了，`status` 仍是 `ok`，
失配只进 `determinism` 子记录（本仓 HT-12 口径，判据就在这一句）。

**路径一律绝对**：spec 里的输入、输出、result 路径与执行件路径在落盘前解析成绝对
路径——子进程的 CWD 是被测仓根（`--repo`），相对路径会从那里解析，找错位置。
"""

import hashlib
import json
import os
import platform
import signal
from pathlib import Path

import numpy as np

import op_abi
import proc

HERE = Path(__file__).resolve().parent
EXEC_SRC = HERE / "harness_exec.cpp"

# 子进程退出码 → 归类。与 harness_exec.cpp 顶部的退出码表逐项对位。
EXIT_KIND = {0: "ok", 10: "spec_error", 11: "io_error", 12: "unsupported",
             13: "acl_error", 14: "op_error", 15: "rerun_mismatch"}

# 这些归类下算子确实跑完了，三键可用；其余归类的 out32 不参与数值统计。
EXECUTED_KINDS = ("ok", "rerun_mismatch")

# 退出码说成功、三键却读不回来时的归类（不在 EXIT_KIND 里，由读回结果定）。
PROTOCOL_ERROR_KIND = "protocol_error"

# case 现场里属于「上一轮产物」的文件：起子进程前一律删掉，免得读到旧输出。
STALE_CASE_OUTPUTS = ("out/out32.bin", "out/info.bin", "out/result.txt",
                      "out/out32.bin.diff.bin", "out/info.bin.diff.bin",
                      "out/out32.bin.fail.bin", "out/info.bin.fail.bin")

NP_DTYPE = {"float32": np.float32, "complex64": np.complex64}


def abspath(p):
    """落进 spec 或交给子进程的路径一律先解析成绝对路径（不要求已存在）。"""
    return Path(p).expanduser().resolve()


class ExecError(RuntimeError):
    """编译或调用前置不满足——报错停下。"""


def arch_include(ascend_home):
    return str(Path(ascend_home) / f"{platform.machine()}-linux" / "include")


def compile_command(repo, ascend_home, out_bin, ops, cxx="g++", build_dir=None):
    """逐字编译命令。`ops` 是要编进来的算子名集合（已对过交付头）。"""
    repo = abspath(repo)
    ascend_home = abspath(ascend_home)
    out_bin = abspath(out_bin)
    build_dir = abspath(build_dir) if build_dir else repo / "build"
    lib = build_dir / "libops_solver.so"
    acl = Path(ascend_home) / "lib64" / "libascendcl.so"
    return ([cxx, "-std=c++17", "-O2", "-Wall", "-o", str(out_bin), str(EXEC_SRC),
             "-I" + str(repo / "include"), "-I" + arch_include(ascend_home)]
            + op_abi.compile_defines(ops)
            + [str(lib), str(acl), "-ldl"])


def compile_executor(repo, ascend_home, out_bin, ops=None, cxx="g++",
                     build_dir=None, timeout=600):
    """对公开头编译执行件。返回 {cmd, returncode, output, bin, sha256, ops}。

    `ops=None` 时按交付头自动取可用算子——这是「按交付头实际形态分流」的落点：
    头里声明了什么就编什么，不按任务书钉死。
    """
    repo = abspath(repo)
    header = repo / "include" / "cann_ops_solver.h"
    if not header.is_file():
        raise ExecError(f"找不到公开头: {header}")
    if not EXEC_SRC.is_file():
        raise ExecError(f"找不到执行件源码: {EXEC_SRC}")
    header_text = header.read_text(encoding="utf-8", errors="replace")
    available = op_abi.available_ops(header_text)
    if ops is None:
        ops = sorted(available)
    else:
        unknown = [o for o in ops if o not in available]
        if unknown:
            raise ExecError(
                f"交付头里没有这些算子的入口声明: {unknown}；"
                f"头内可用: {sorted(available)}")
    out_bin = abspath(out_bin)
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    cmd = compile_command(repo, ascend_home, out_bin, ops, cxx, build_dir)
    p = proc.run(cmd, timeout=timeout, log_path=out_bin.with_name(
        out_bin.name + ".compile.log"))
    rec = {"cmd": p["cmd"],
           "returncode": p["returncode"], "output": p["output"],
           "timed_out": p["timed_out"],
           "bin": str(out_bin), "ops": list(ops),
           "available_in_header": sorted(available),
           "header_entries": sorted(op_abi.header_entries(header_text))}
    if p["timed_out"]:
        raise ExecError(f"执行件编译超时（{timeout}s）:\n{p['output'][-2000:]}")
    if p["returncode"] != 0:
        raise ExecError(
            f"执行件编译失败（退 {p['returncode']}）:\n{p['output'][-2000:]}")
    rec["sha256"] = sha256_file(out_bin)
    return rec


def sha256_file(path):
    """文件内容摘要。实际加载库与执行件都靠它对账。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def loaded_lib_record(lib_path, provenance=None):
    """实际加载库的身份与 S1 产物核对（审计 #10）。

    `lib_path` 是执行子进程对算子函数地址取 `dladdr` 得到的 `.so` 路径；本函数给它
    记 sha256，再与 S1 构建取证（`build_dut.py --out` 的 `artifacts` 表）里的产物
    哈希逐个比。`match` 三态：

    | 取值 | 含义 |
    | --- | --- |
    | `True` | 摘要命中 S1 产物之一，跑的就是这次构建出来的库 |
    | `False` | 有 S1 取证但一个都没命中——旧产物混入或跑错目标 |
    | `None` | 没有 S1 取证（结构演练通路），明确跳过核对并记原因 |
    """
    if not lib_path:
        return {"path": None, "sha256": None, "match": False if provenance else None,
                "note": "执行子进程没回填 lib_path（dladdr 取不到）"}
    rec = {"path": str(lib_path), "sha256": None, "match": None, "note": ""}
    p = Path(lib_path)
    if p.is_file():
        try:
            rec["sha256"] = sha256_file(p)
        except OSError as exc:
            rec["note"] = f"读不到实际加载库: {type(exc).__name__}: {exc}"
    else:
        rec["note"] = "实际加载库的路径不在本机（判定机与执行机不同？）"
    artifacts = (provenance or {}).get("artifacts") or {}
    if not artifacts:
        rec["note"] = (rec["note"] + "；" if rec["note"] else "") + \
            "无 S1 构建取证（--provenance 未给），跳过与产物哈希的核对"
        return rec
    if rec["sha256"] is None:
        rec["match"] = False
        return rec
    hit = [rel for rel, meta in artifacts.items()
           if (meta or {}).get("sha256") == rec["sha256"]]
    rec["match"] = bool(hit)
    rec["matched_artifacts"] = hit
    if not hit:
        rec["note"] = (rec["note"] + "；" if rec["note"] else "") + \
            "摘要不在 S1 产物表内：实际跑的不是这次构建的库"
    return rec


# ---------------------------------------------------------------------------
# spec 与 result 文本
# ---------------------------------------------------------------------------

def write_spec(path, spec):
    """写 `key=value` 行文本。值为 None 时写空串——子进程按缺省处理。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# harness_exec spec（一个 case 一份，可脱离编排层独立复跑）"]
    for k in sorted(spec):
        v = spec[k]
        lines.append(f"{k}={'' if v is None else v}")
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return text


def parse_result(text):
    """解析 result 文本为字典；数值键转成数值，其余留字符串。"""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if v == "":
            out[k] = None
            continue
        if k.endswith("_ms"):
            try:
                out[k] = float(v)
                continue
            except ValueError:
                pass
        if k in ("n", "batch", "nrhs", "runs", "exit", "out32_bytes", "info_len",
                 "rerun_first_diff_run", "device_roundtrip_ret") or k.endswith("_ret"):
            try:
                out[k] = int(v)
                continue
            except ValueError:
                pass
        out[k] = v
    return out


def classify(returncode, timed_out, result):
    """退出码与信号 → 归类。返回 (kind, status, detail)。

    `status` 是三键里的那个 status：`ok` 之外的取值都让判定侧把该 case 记执行失败
    （SKILL.md 检查条件「被测输出 status 非 ok」）。复跑失配例外——算子跑完了，
    status 仍是 ok，确定性结论单独出（HT-12 口径）。
    """
    if timed_out:
        return "timeout", "timeout", "子进程超时被杀"
    if returncode is not None and returncode < 0:
        name = signal.Signals(-returncode).name if -returncode in \
            [s.value for s in signal.Signals] else str(-returncode)
        return "crash", f"crash:{name}", f"子进程被信号 {name} 杀死"
    kind = EXIT_KIND.get(returncode, "unknown")
    detail = (result or {}).get("detail") or ""
    if kind == "ok":
        return "ok", "ok", detail
    if kind == "rerun_mismatch":
        return kind, "ok", detail or "复跑 bit-wise 失配"
    return kind, f"{kind}:exit={returncode}", detail


# ---------------------------------------------------------------------------
# 跑一个 case
# ---------------------------------------------------------------------------

def _runtime_env(repo, ascend_home, build_dir=None, extra=None):
    """运行时环境：LD_LIBRARY_PATH 指到 build/（build.sh --run 自己也这么干）。"""
    build_dir = abspath(build_dir) if build_dir else abspath(repo) / "build"
    env = dict(os.environ)
    paths = [str(build_dir), str(abspath(ascend_home) / "lib64")]
    old = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = ":".join(paths + ([old] if old else []))
    if extra:
        env.update(extra)
    return env


def run_case(executor, case_dir, spec, inputs, repo, ascend_home,
             build_dir=None, timeout=1800, cwd=None):
    """跑一个 case：落输入 bin → 起子进程 → 读三键 → 归类。

    `inputs` 是 {"in_a": ndarray[, "in_b": ndarray]}，按 C 序裸二进制落盘；
    片上布局口径由 spec 的 `layout` 字段声明（现工程行主序，任务书列主序，
    两套口径在这一个字段上分流）。

    返回记录含 out32/info（ndarray）、status、kind、result 全文与日志路径。
    通过与否都不在这里判——判定是 criteria 的事。
    """
    case_dir = abspath(case_dir)
    (case_dir / "in").mkdir(parents=True, exist_ok=True)
    (case_dir / "out").mkdir(parents=True, exist_ok=True)
    # 目录复用时先抹掉上一轮产物：否则子进程中途失败不写输出，读回来的是旧输出，
    # 新 result 与旧数组能拼成一件看着完整的证据（审计 #2）。
    stale = clear_stale_outputs(case_dir)

    spec = dict(spec)
    for key, arr in inputs.items():
        a = np.ascontiguousarray(arr)
        p = case_dir / "in" / f"{key}.bin"
        a.tofile(p)
        spec[key] = str(p)
    spec.setdefault("in_b", None)
    spec["out_a"] = str(case_dir / "out" / "out32.bin")
    spec["out_info"] = str(case_dir / "out" / "info.bin")
    spec["result"] = str(case_dir / "out" / "result.txt")
    spec_text = write_spec(case_dir / "case.spec", spec)

    p = proc.run([abspath(executor), case_dir / "case.spec"],
                 cwd=abspath(cwd) if cwd else abspath(repo),
                 env=_runtime_env(repo, ascend_home, build_dir),
                 timeout=timeout, log_path=case_dir / "exec.log")
    returncode = None if p["timed_out"] else p["returncode"]
    log = p["output"]

    result_path = case_dir / "out" / "result.txt"
    result = parse_result(result_path.read_text(encoding="utf-8", errors="replace")) \
        if result_path.is_file() else {}
    result.setdefault("runs", int(spec.get("runs", 1)))
    result.setdefault("rerun_runs_completed", 0)
    kind, status, detail = classify(returncode, p["timed_out"], result)

    # 三键只要落了盘就读回来，不管归类——子进程在失败轮也写第 1 轮输出（留证优先），
    # 这份输出不参与判定（status 非 ok 即记执行失败），但必须进留证件，否则
    # 「输入 + 输出 + 日志」缺一角，留证件就不是可独立重判的。
    # 分成员读：out32 读不成也要尽量把 info 读回来，两边各自记错。
    out32, info, key_errors = read_three_keys_members(case_dir, spec, result)
    three_key_error = "；".join(key_errors) if key_errors else None
    if three_key_error and kind in EXECUTED_KINDS:
        # 子进程自报跑完、三键却读不成结构：归执行协议错误，不记 ok（审计 #6）
        kind = PROTOCOL_ERROR_KIND
        status = f"{PROTOCOL_ERROR_KIND}:three_key"
        detail = (detail + "；" if detail else "") + three_key_error

    return {
        "kind": kind, "status": status, "detail": detail,
        "returncode": p["returncode"], "timed_out": p["timed_out"],
        "wall_seconds": p["seconds"], "result": result,
        "spec_text": spec_text, "log": log or "",
        "log_path": str(case_dir / "exec.log"), "case_dir": str(case_dir),
        "out32": out32, "info": info, "three_key_error": three_key_error,
        "cleared_stale": stale,
    }


def clear_stale_outputs(case_dir):
    """删掉 case 现场里上一轮留下的输出与 result；返回删掉的相对路径清单。"""
    case_dir = Path(case_dir)
    gone = []
    for rel in STALE_CASE_OUTPUTS:
        p = case_dir / rel
        if p.is_file():
            p.unlink()
            gone.append(rel)
    return gone


def read_out32(case_dir, spec, result):
    """读回 out32：dtype 与形状取 result 声明，缺则回落 spec。"""
    out_bin = Path(case_dir) / "out" / "out32.bin"
    if not out_bin.is_file():
        raise ExecError(f"{out_bin}: 缺 out32 输出文件")
    dtype = NP_DTYPE[result.get("out32_dtype") or spec["dtype"]]
    shape = tuple(int(x) for x in str(spec["out32_shape"]).split(",") if x != "")
    if out_bin.stat().st_size % np.dtype(dtype).itemsize:
        raise ExecError(f"{out_bin}: 输出字节尾不完整")
    if np.dtype(dtype) != np.dtype(NP_DTYPE[spec["dtype"]]):
        raise ExecError("out32 dtype 与 spec 不一致")
    out32 = np.fromfile(out_bin, dtype=dtype)
    want = int(np.prod(shape)) if shape else out32.size
    if out32.size != want:
        raise ExecError(f"{out_bin}: 元素数 {out32.size} 与声明形状 {shape} 不符")
    return out32.reshape(shape)


def read_info(case_dir, spec):
    """读回 info：`info_kind=array` 留 int32 数组，否则收成 int64 标量。"""
    info_bin = Path(case_dir) / "out" / "info.bin"
    if not info_bin.is_file():
        raise ExecError(f"{info_bin}: 缺 info 输出文件")
    expected = int(spec.get("batch", 1)) if spec.get("info_kind") == "array" else 1
    if info_bin.stat().st_size != expected * 4:
        raise ExecError(f"{info_bin}: info 期望 {expected * 4} 字节，得到 {info_bin.stat().st_size}")
    info = np.fromfile(info_bin, dtype=np.int32)
    if spec.get("info_kind") != "array":
        if info.size != 1:
            raise ExecError(f"{info_bin}: 标量 info 期望 1 个元素，得到 {info.size}")
        return np.int64(int(info[0]))
    return info


def read_three_keys(case_dir, spec, result):
    """从裸二进制 + result 的形状声明组装 out32 与 info（npz 依赖留在 Python 侧）。

    严格口径：任一成员读不成就抛 `ExecError`。要「坏一个还留另一个」用
    `read_three_keys_members`。
    """
    return read_out32(case_dir, spec, result), read_info(case_dir, spec)


def read_three_keys_members(case_dir, spec, result):
    """分成员读三键，返回 (out32, info, 错误清单)。

    留证优先：out32 结构不符时 info 仍可能是有效现场，反之同理——所以两边各读各记，
    不让第一个错误吞掉另一个成员（审计 #6）。
    """
    out32 = info = None
    errors = []
    for name, fn in (("out32", lambda: read_out32(case_dir, spec, result)),
                     ("info", lambda: read_info(case_dir, spec))):
        try:
            value = fn()
        except (ExecError, OSError, KeyError, ValueError) as exc:
            errors.append(f"{name} 读回失败: {type(exc).__name__}: {exc}")
            continue
        if name == "out32":
            out32 = value
        else:
            info = value
    return out32, info, errors


def rerun_record(result):
    """复跑子记录，与 stream_check 的 determinism 子记录同形（runs/consistent/first_diff）。

    `consistent` 三态，判据只看请求轮数与完成轮数对不对得上（审计 #3）：

    | 取值 | 判据 |
    | --- | --- |
    | `False` | 子进程报 `rerun_consistent=0`——某轮与第 1 轮字节不一致 |
    | `True` | 报 `1` **且** 完成轮数 ≥ 请求轮数，规定比较全做完了 |
    | `None` | 其余——规定轮数没跑完（中途失败、算子返回非成功），确定性无结论 |

    `None` 不是「通过」：上层按「不是 PASS 就留证」处理，报告里单独记 `det_unknown`。
    """
    runs = int(result.get("runs") or 1)
    if runs < 2:
        return None
    raw_done = result.get("rerun_runs_completed")
    completed = int(raw_done) if raw_done is not None else None
    flag = str(result.get("rerun_consistent"))
    if flag == "0":
        consistent = False
    elif flag == "1" and completed is not None and completed >= runs:
        consistent = True
    else:
        consistent = None
    first_diff = None
    if consistent is False:
        first_diff = {"run": result.get("rerun_first_diff_run"),
                      "key": result.get("rerun_first_diff_key"),
                      "detail": "字节域不一致（bit-wise）"}
    rec = {"runs": runs, "runs_completed": completed, "consistent": consistent,
           "first_diff": first_diff,
           "per_run_fnv": {k: v for k, v in result.items() if "_fnv" in k}}
    if consistent is None:
        rec["note"] = (f"请求 {runs} 轮，完成 {completed} 轮："
                       "规定比较未做完，确定性不出结论")
    return rec


def dump_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, default=str)
        fh.write("\n")
