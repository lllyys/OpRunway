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

失败归类按退出码与信号查表，不靠解析日志文本：

| 归类 | 判据 | 是否算执行失败 |
| --- | --- | --- |
| `ok` | 退 0 | 否 |
| `spec_error` / `io_error` | 退 10 / 11 | 是（harness 自己的问题，不是被测） |
| `unsupported` | 退 12 | 是 |
| `acl_error` | 退 13 | 是 |
| `op_error` | 退 14 | 是（被测返回非成功） |
| `rerun_mismatch` | 退 15 | 否——算子跑完了，确定性结论独立（HT-12 口径） |
| `crash` | 被信号杀（退出码 < 0） | 是 |
| `timeout` | 超 `--timeout` | 是 |
| `unknown` | 其它退出码 | 是 |
"""

import json
import os
import platform
import shlex
import signal
import subprocess
import time
from pathlib import Path

import numpy as np

import op_abi

HERE = Path(__file__).resolve().parent
EXEC_SRC = HERE / "harness_exec.cpp"

# 子进程退出码 → 归类。与 harness_exec.cpp 顶部的退出码表逐项对位。
EXIT_KIND = {0: "ok", 10: "spec_error", 11: "io_error", 12: "unsupported",
             13: "acl_error", 14: "op_error", 15: "rerun_mismatch"}

# 这些归类下算子确实跑完了，三键可用；其余归类的 out32 不参与数值统计。
EXECUTED_KINDS = ("ok", "rerun_mismatch")

NP_DTYPE = {"float32": np.float32, "complex64": np.complex64}


class ExecError(RuntimeError):
    """编译或调用前置不满足——报错停下。"""


def arch_include(ascend_home):
    return str(Path(ascend_home) / f"{platform.machine()}-linux" / "include")


def compile_command(repo, ascend_home, out_bin, ops, cxx="g++", build_dir=None):
    """逐字编译命令。`ops` 是要编进来的算子名集合（已对过交付头）。"""
    repo = Path(repo)
    build_dir = Path(build_dir) if build_dir else repo / "build"
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
    repo = Path(repo)
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
    out_bin = Path(out_bin)
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    cmd = compile_command(repo, ascend_home, out_bin, ops, cxx, build_dir)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors="replace", timeout=timeout)
    rec = {"cmd": " ".join(shlex.quote(c) for c in cmd),
           "returncode": p.returncode, "output": p.stdout,
           "bin": str(out_bin), "ops": list(ops),
           "available_in_header": sorted(available),
           "header_entries": sorted(op_abi.header_entries(header_text))}
    if p.returncode != 0:
        raise ExecError(f"执行件编译失败（退 {p.returncode}）:\n{p.stdout[-2000:]}")
    import hashlib
    rec["sha256"] = hashlib.sha256(out_bin.read_bytes()).hexdigest()
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
    build_dir = Path(build_dir) if build_dir else Path(repo) / "build"
    env = dict(os.environ)
    paths = [str(build_dir), str(Path(ascend_home) / "lib64")]
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
    case_dir = Path(case_dir)
    (case_dir / "in").mkdir(parents=True, exist_ok=True)
    (case_dir / "out").mkdir(parents=True, exist_ok=True)

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

    log_path = case_dir / "exec.log"
    t0 = time.time()
    timed_out = False
    try:
        p = subprocess.run([str(executor), str(case_dir / "case.spec")],
                           cwd=str(cwd) if cwd else str(Path(repo)),
                           env=_runtime_env(repo, ascend_home, build_dir),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors="replace", timeout=timeout)
        returncode, log = p.returncode, p.stdout
    except subprocess.TimeoutExpired as exc:
        returncode, timed_out = None, True
        log = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
    wall = round(time.time() - t0, 3)
    log_path.write_text(log or "", encoding="utf-8")

    result_path = case_dir / "out" / "result.txt"
    result = parse_result(result_path.read_text(encoding="utf-8", errors="replace")) \
        if result_path.is_file() else {}
    kind, status, detail = classify(returncode, timed_out, result)

    # 三键只要落了盘就读回来，不管归类——子进程在失败轮也写第 1 轮输出（留证优先），
    # 这份输出不参与判定（status 非 ok 即记执行失败），但必须进留证件，否则
    # 「输入 + 输出 + 日志」缺一角，留证件就不是可独立重判的。
    out32 = info = None
    three_key_error = None
    if (case_dir / "out" / "out32.bin").is_file():
        try:
            out32, info = read_three_keys(case_dir, spec, result)
        except (ExecError, OSError, KeyError, ValueError) as exc:
            three_key_error = f"{type(exc).__name__}: {exc}"

    return {
        "kind": kind, "status": status, "detail": detail,
        "returncode": returncode, "timed_out": timed_out,
        "wall_seconds": wall, "result": result,
        "spec_text": spec_text, "log": log or "",
        "log_path": str(log_path), "case_dir": str(case_dir),
        "out32": out32, "info": info, "three_key_error": three_key_error,
    }


def read_three_keys(case_dir, spec, result):
    """从裸二进制 + result 的形状声明组装 out32 与 info（npz 依赖留在 Python 侧）。"""
    case_dir = Path(case_dir)
    dtype = NP_DTYPE[result.get("out32_dtype") or spec["dtype"]]
    shape = tuple(int(x) for x in str(spec["out32_shape"]).split(",") if x != "")
    out_bin = case_dir / "out" / "out32.bin"
    info_bin = case_dir / "out" / "info.bin"
    out32 = np.fromfile(out_bin, dtype=dtype)
    want = int(np.prod(shape)) if shape else out32.size
    if out32.size != want:
        raise ExecError(f"{out_bin}: 元素数 {out32.size} 与声明形状 {shape} 不符")
    out32 = out32.reshape(shape)
    info = np.fromfile(info_bin, dtype=np.int32)
    if spec.get("info_kind") != "array":
        if info.size != 1:
            raise ExecError(f"{info_bin}: 标量 info 期望 1 个元素，得到 {info.size}")
        info = np.int64(int(info[0]))
    return out32, info


def rerun_record(result):
    """复跑子记录，与 stream_check 的 determinism 子记录同形（runs/consistent/first_diff）。"""
    runs = int(result.get("runs") or 1)
    if runs < 2:
        return None
    consistent = str(result.get("rerun_consistent")) == "1"
    first_diff = None
    if not consistent:
        first_diff = {"run": result.get("rerun_first_diff_run"),
                      "key": result.get("rerun_first_diff_key"),
                      "detail": "字节域不一致（bit-wise）"}
    return {"runs": runs, "consistent": consistent, "first_diff": first_diff,
            "per_run_fnv": {k: v for k, v in result.items() if "_fnv" in k}}


def dump_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, default=str)
        fh.write("\n")
