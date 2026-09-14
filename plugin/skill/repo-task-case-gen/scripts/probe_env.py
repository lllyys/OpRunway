#!/usr/bin/env python3
"""探测生成侧环境：能不能 import atk 与 torch。

退出码 0 可用，2 缺件。生成侧不需要 NPU 与 CANN，探到了也只是记录。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys



# 生成侧只要 CPU 版 torch。装了 torch_npu 的机器上 `import torch` 会去自动加载
# 它，没 source CANN 时抛 RuntimeError，连带 atk 也起不来。关掉自动加载，
# 让「生成侧不需要 NPU 与 CANN」这句话成立。必须在任何 torch 导入之前设。
os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")
def _module(name):
    try:
        mod = __import__(name)
    except Exception as exc:  # noqa: BLE001 - 缺件原因要原样报给用户
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "version": getattr(mod, "__version__", getattr(mod, "PACKAGE_VERSION", "unknown")),
        "path": getattr(mod, "__file__", "unknown"),
    }


def _npu():
    if not shutil.which("npu-smi"):
        return {"available": False, "error": "npu-smi 不在 PATH"}
    try:
        out = subprocess.run(
            ["npu-smi", "info"], capture_output=True, text=True, timeout=30, check=False)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    if out.returncode != 0:
        return {"available": False, "error": out.stderr.strip()[:200]}
    healthy = out.stdout.count("| OK ")
    return {"available": healthy > 0, "healthy_chips": healthy}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--out", default="env.json", help="写到哪，默认 env.json")
    args = parser.parse_args()

    report = {
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "atk": _module("atk"),
        "torch": _module("torch"),
        "npu": _npu(),
        "cann_home": os.environ.get("ASCEND_TOOLKIT_HOME", ""),
        "atk_cli": shutil.which("atk") or "",
    }

    missing = [name for name in ("atk", "torch") if not report[name]["available"]]
    report["missing"] = missing

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print(f"python      {report['python']} ({report['python_version']})")
    for name in ("atk", "torch"):
        item = report[name]
        state = f"{item['version']}" if item["available"] else f"缺失 — {item['error']}"
        print(f"{name:<11} {state}")
    print(f"npu         {'可用' if report['npu']['available'] else '不可用（生成侧不需要）'}")
    print(f"写入        {args.out}")

    if missing:
        print(f"\n阻塞·未生成 @S0：缺 {'、'.join(missing)}。"
              f"换一个装了这些包的解释器，或先 pip install。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
