#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fake_exec.py —— 测试替身：按 harness_exec 的 spec/result 协议行事，不碰 NPU。

存在理由：编排与取证两段的逻辑能不能在没有真机的地方验，取决于执行段是不是一个
可替换的进程。本替身实现同一份协议（spec 文本 + 裸二进制三键 + result 文本 +
退出码表），于是编排层在本地也走完整通路——包括崩溃、超时、复跑失配三条异常支路。

行为由环境变量选：

| 变量 | 取值 |
| --- | --- |
| `FAKE_MODE` | `ok`（缺省）/ `op_error` / `crash` / `timeout` / `rerun_mismatch` / `unsupported` / `short_out` |
| `FAKE_OUT` | out32 的字节来源文件；缺省把 `in_a` 原样回写 |
| `FAKE_INFO` | 逗号分隔 int32；缺省全 0 |
"""

import os
import signal
import sys
import time
from pathlib import Path

import numpy as np

EXIT = {"ok": 0, "spec_error": 10, "io_error": 11, "unsupported": 12,
        "acl_error": 13, "op_error": 14, "rerun_mismatch": 15}


def parse_spec(path):
    kv = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        kv[k.strip()] = v.strip()
    return kv


def main(argv):
    if len(argv) != 2:
        return EXIT["spec_error"]
    spec = parse_spec(argv[1])
    mode = os.environ.get("FAKE_MODE", "ok")
    result_path = Path(spec["result"])
    result_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "crash":
        os.kill(os.getpid(), signal.SIGSEGV)
    if mode == "timeout":
        time.sleep(600)

    itemsize = 8 if spec["dtype"] == "complex64" else 4
    shape = tuple(int(x) for x in spec["out32_shape"].split(",") if x)
    nbytes = int(np.prod(shape)) * itemsize
    info_len = int(spec["batch"]) if spec.get("info_kind") == "array" else 1
    runs = int(spec["runs"])

    lines = {"op": spec["op"], "abi": spec["abi"], "call_shape": spec["call_shape"],
             "dtype": spec["dtype"], "n": spec["n"], "batch": spec["batch"],
             "nrhs": spec.get("nrhs", "0"), "runs": str(runs),
             "out32_bytes": str(nbytes), "out32_dtype": spec["dtype"],
             "rerun_runs_requested": str(runs), "rerun_runs_completed": str(runs),
             "info_len": str(info_len), "info_dtype": "int32",
             "lib_path": "/fake/libops_solver.so"}

    if mode == "unsupported":
        lines.update({"status": "unsupported", "detail": "替身：算子未编进来",
                      "exit": str(EXIT["unsupported"])})
        _flush(result_path, lines)
        return EXIT["unsupported"]

    src = os.environ.get("FAKE_OUT")
    payload = Path(src).read_bytes() if src else Path(spec["in_a"]).read_bytes()
    if mode == "short_out":
        payload = payload[: max(0, len(payload) - itemsize)]
    Path(spec["out_a"]).parent.mkdir(parents=True, exist_ok=True)
    Path(spec["out_a"]).write_bytes(payload)

    raw = os.environ.get("FAKE_INFO")
    info = (np.array([int(x) for x in raw.split(",")], dtype=np.int32) if raw
            else np.zeros(info_len, dtype=np.int32))
    Path(spec["out_info"]).write_bytes(info.tobytes())

    for r in range(1, runs + 1):
        lines[f"run{r}_ms"] = "1.000"
        lines[f"run{r}_ret"] = "0"
        lines[f"run{r}_out32_fnv"] = f"0x{r if mode == 'rerun_mismatch' else 1:016x}"
        lines[f"run{r}_info_fnv"] = "0x0000000000000001"

    if mode == "op_error":
        lines.update({"status": "op_error", "detail": "替身：算子返回 -1",
                      "run1_ret": "-1", "rerun_consistent": "unknown",
                      "rerun_runs_completed": "0",
                      "exit": str(EXIT["op_error"])})
        _flush(result_path, lines)
        return EXIT["op_error"]

    if mode == "rerun_mismatch" and runs >= 2:
        Path(spec["out_a"] + ".diff.bin").write_bytes(payload[::-1] or b"\0")
        Path(spec["out_info"] + ".diff.bin").write_bytes(
            (info + 1).astype(np.int32).tobytes())
        lines.update({"status": "rerun_mismatch", "rerun_consistent": "0",
                      "rerun_first_diff_run": "2", "rerun_first_diff_key": "out32",
                      "detail": "替身：第 2 轮 out32 与第 1 轮字节不一致",
                      "exit": str(EXIT["rerun_mismatch"])})
        _flush(result_path, lines)
        return EXIT["rerun_mismatch"]

    lines.update({"status": "ok", "detail": "", "rerun_consistent": "1",
                  "exit": "0"})
    _flush(result_path, lines)
    return 0


def _flush(path, lines):
    path.write_text("".join(f"{k}={v}\n" for k, v in lines.items()),
                    encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
