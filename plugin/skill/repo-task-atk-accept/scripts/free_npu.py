#!/usr/bin/env python3
"""开跑那一刻现查空闲卡。卡号不写死。

两条判据一起用，缺一条都会挑到被占的卡：

| 判据 | 取自 | 漏掉它会怎样 |
| --- | --- | --- |
| HBM 占用低于阈值 | `npu-smi info` 每颗芯片那一行 | 挑到跑着大模型的卡，用例整片 OOM 假失败 |
| 整块板卡上没有别人的进程 | `npu-smi info -t proc-mem -i <板卡号>` | 占用很小的残留上下文看不出来，跑测时抢 stream |

A3 一块板卡两颗芯片：`proc-mem` 按板卡问，跑测脚本的 `--device` 按芯片给。

标准输出只有一行逗号分隔的芯片号，供 `$(...)` 直接吃；判据与被排除的卡打在
标准错误上。退 0 有空闲卡，退 4 一张都没有。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

CHIP = re.compile(
    r"\|\s*(\d+)\s+(\d+)\s+\|\s*[0-9A-Fa-f:.]+\s*\|.*?(\d+)\s*/\s*(\d+)\s*\|?\s*$")
BOARD = re.compile(r"\|\s*(\d+)\s+Ascend")
PROC = re.compile(r"Process id:(\d+)\s+Process name:(\S*)")


def _smi(*args):
    try:
        got = subprocess.run(["npu-smi", *args], capture_output=True, text=True,
                             timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"npu-smi 调不起来：{exc}", file=sys.stderr)
        return None
    return got.stdout


def chips(text):
    """返回 [(板卡号, 芯片号, 已用 MB)]。"""
    out, board = [], None
    for line in text.splitlines():
        head = BOARD.match(line)
        if head:
            board = int(head.group(1))
        hit = CHIP.match(line)
        if hit and board is not None:
            out.append((board, int(hit.group(2)), int(hit.group(3))))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hbm-mb", type=int, default=4096,
                    help="HBM 已用超过它就算被占。默认 4096")
    ap.add_argument("--mine", default="",
                    help="进程名里含这个子串的算自己的，不据此排除整板")
    ap.add_argument("-n", "--max", type=int, help="最多返回几张")
    ns = ap.parse_args()

    info = _smi("info")
    if info is None:
        return 4
    rows = chips(info)
    if not rows:
        print("npu-smi info 里一行芯片都没解析出来。", file=sys.stderr)
        return 4

    foreign = set()
    for board in sorted({b for b, _, _ in rows}):
        text = _smi("info", "-t", "proc-mem", "-i", str(board)) or ""
        others = [pid for pid, name in PROC.findall(text)
                  if not (ns.mine and ns.mine in name)]
        if others:
            foreign.add(board)

    free, busy = [], []
    for board, chip, used in rows:
        why = ("HBM %d MB" % used if used >= ns.hbm_mb
               else "板卡 %d 上有别人的进程" % board if board in foreign else "")
        (busy if why else free).append((chip, why))
    if ns.max:
        free = free[:ns.max]

    print("空闲 " + (",".join(str(c) for c, _ in free) or "无"), file=sys.stderr)
    for chip, why in busy:
        print(f"排除 {chip}：{why}", file=sys.stderr)
    if not free:
        return 4
    print(",".join(str(c) for c, _ in free))
    return 0


if __name__ == "__main__":
    sys.exit(main())
