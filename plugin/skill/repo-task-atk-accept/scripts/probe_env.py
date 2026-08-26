#!/usr/bin/env python3
"""探测跑测侧环境并生成 env.sh。

跑测侧要 atk、torch_npu、CANN 与健康 NPU 四样齐全，缺一不可。
退出码 0 可用，2 缺件。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SOC_TO_BUILD_FLAG = {
    "910_93": "ascend910_93",
    "910b": "ascend910b",
    "910_95": "ascend950",
    "310p": "ascend310p",
}


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
        return {"available": False, "error": "npu-smi 不在 PATH，CANN 环境没 source"}
    try:
        out = subprocess.run(["npu-smi", "info"], capture_output=True, text=True,
                             timeout=30, check=False)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    if out.returncode != 0:
        return {"available": False, "error": out.stderr.strip()[:200]}
    healthy = out.stdout.count("| OK ")
    return {"available": healthy > 0, "healthy_chips": healthy}


def _soc():
    """从 torch_npu 拿芯片型号，再翻成 build.sh 的 --soc 取值。"""
    try:
        import torch_npu
        raw = torch_npu.npu.get_device_name(0)
    except Exception as exc:  # noqa: BLE001
        return {"raw": "", "build_flag": "", "error": f"{type(exc).__name__}: {exc}"}
    lowered = raw.lower().replace("ascend", "")
    for key, flag in SOC_TO_BUILD_FLAG.items():
        if lowered.startswith(key):
            return {"raw": raw, "build_flag": flag}
    return {"raw": raw, "build_flag": "",
            "error": f"认不出 {raw}，用 --soc 手工指定 build.sh 的取值"}


def _vendor_lib(opp_root):
    """在装出来的 opp 根下找 libcust_opapi.so。"""
    if not opp_root or not Path(opp_root).is_dir():
        return None
    matches = sorted(Path(opp_root).glob("vendors/*/op_api/lib/libcust_opapi.so"))
    return matches[0] if matches else None


def write_env_sh(path, cann_home, python_exe, opp_root):
    """生成 env.sh。build_install.py 装完包后会再调一次把三行补上。"""
    cann = cann_home or "/usr/local/Ascend/ascend-toolkit/latest"
    lines = [
        "#!/bin/bash",
        "# 由 probe_env.py 生成。跑测侧每条命令都要先 source 它。",
        f"source {Path(cann).parent}/set_env.sh >/dev/null 2>&1",
        f'export PATH="{Path(python_exe).parent}:$PATH"',
    ]
    lib = _vendor_lib(opp_root)
    if lib:
        vendor = lib.parents[2]
        lines += [
            "",
            "# 自定义算子包：ASCEND_CUSTOM_OPP_PATH 给 CANN 找 kernel，",
            "# ATK_CUSTOM_OPP_PATH 直接点到 so，绕开 ATK 对 vendor 名的白名单。",
            f'export ASCEND_CUSTOM_OPP_PATH="{vendor}"',
            f'export ATK_CUSTOM_OPP_PATH="{lib}"',
            f'export LD_LIBRARY_PATH="{lib.parent}:$LD_LIBRARY_PATH"',
        ]
    else:
        lines.append("\n# 还没装自定义算子包。build_install.py 装完会重写这几行。")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--op", default="", help="算子名，只用于打印")
    parser.add_argument("--opp-root", default="", help="自定义算子包安装根，默认 opp/")
    parser.add_argument("-o", "--out", default="env.json")
    parser.add_argument("--write-env-sh", default="evidence/env.sh")
    args = parser.parse_args()

    opp_root = args.opp_root or str(Path("opp").resolve())

    report = {
        "op": args.op,
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "atk": _module("atk"),
        "torch": _module("torch"),
        "torch_npu": _module("torch_npu"),
        "npu": _npu(),
        "soc": _soc(),
        "cann_home": os.environ.get("ASCEND_TOOLKIT_HOME", ""),
        "opp_root": opp_root,
        "atk_cli": shutil.which("atk") or "",
    }

    missing = [name for name in ("atk", "torch", "torch_npu") if not report[name]["available"]]
    if not report["npu"]["available"]:
        missing.append("npu")
    if not report["cann_home"]:
        missing.append("cann")
    report["missing"] = missing

    lib = write_env_sh(args.write_env_sh, report["cann_home"], sys.executable, opp_root)
    report["custom_opp_lib"] = str(lib) if lib else ""

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print(f"python      {report['python']} ({report['python_version']})")
    for name in ("atk", "torch", "torch_npu"):
        item = report[name]
        print(f"{name:<11} "
              f"{item['version'] if item['available'] else '缺失 — ' + item['error']}")
    npu = report["npu"]
    print(f"npu         "
          f"{'健康 %s 片' % npu.get('healthy_chips') if npu['available'] else '不可用 — ' + npu['error']}")
    print(f"soc         {report['soc']['raw'] or '认不出'} "
          f"→ build.sh --soc={report['soc']['build_flag'] or '?'}")
    print(f"cann        {report['cann_home'] or '未 source set_env.sh'}")
    print(f"自定义算子包 {report['custom_opp_lib'] or '未安装（A2 会装）'}")
    print(f"写入        {args.out}、{args.write_env_sh}")

    if missing:
        print(f"\n阻塞·未验收 @A1：缺 {'、'.join(missing)}。", file=sys.stderr)
        if "cann" in missing or "npu" in missing:
            print("先 source /usr/local/Ascend/ascend-toolkit/set_env.sh 再来。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
