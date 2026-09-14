#!/usr/bin/env python3
"""探测跑测侧环境并生成 env.sh。

跑测侧要 atk、torch_npu、CANN 与健康 NPU 四样齐全，缺一不可。
退出码 0 可用，2 缺件。
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
import stage_clock  # noqa: E402 - 同目录量具，给 stage JSON 记阶段墙钟

# 找 CANN 环境脚本的落点，按优先级排。ASCEND_HOME_PATH 已设时优先用它的同级。
SET_ENV_CANDIDATES = (
    "/usr/local/Ascend/ascend-toolkit/set_env.sh",
    "/usr/local/Ascend/latest/set_env.sh",
    "/usr/local/Ascend/set_env.sh",
)


def _reexec_under_cann():
    """没 source CANN 环境时自己 source 一遍再重入本脚本。

    本脚本判 cann、npu、torch_npu 三件全靠 set_env.sh 设的环境变量，而调用方
    每敲一条命令多半是一个新 shell，上一条的 source 不留存。A1 又恰好是**唯一
    一条没法先 `source evidence/env.sh` 自举的命令**——env.sh 要等本脚本跑完才
    存在。所以少一句 source 就三件全缺、退 2，报出一个假阻塞，每个算子重演一遍。

    这里自己找 set_env.sh 重入，找不到才按未 source 继续（大概率随后退 2，
    那时缺的是真的）。`_PROBE_ENV_REEXEC` 防 set_env.sh 不设
    ASCEND_TOOLKIT_HOME 时无限重入。
    """
    if os.environ.get("ASCEND_TOOLKIT_HOME") or os.environ.get("_PROBE_ENV_REEXEC"):
        return
    home = os.environ.get("ASCEND_HOME_PATH", "")
    candidates = ([str(Path(home).parent / "set_env.sh")] if home else [])
    candidates += list(SET_ENV_CANDIDATES)
    for script in candidates:
        if not Path(script).is_file():
            continue
        print(f"未 source CANN 环境，自动 source {script} 后重入。")
        argv = " ".join(shlex.quote(a) for a in [sys.executable, *sys.argv])
        os.execvpe(
            "bash",
            ["bash", "-c", f"source {shlex.quote(script)} >/dev/null 2>&1 && exec {argv}"],
            # 把真正 source 成功的那份记下来。env.sh 里要写它，而它**推不出来**——
            # 见 find_set_env。
            dict(os.environ, _PROBE_ENV_REEXEC="1", _PROBE_ENV_SET_ENV=script),
        )
    print(f"这些路径下都没有 set_env.sh：{'、'.join(candidates)}。\n"
          f"CANN 装在别处时先手工 source 它的 set_env.sh 再跑本脚本。", file=sys.stderr)


SOC_TO_BUILD_FLAG = {
    "910_93": "ascend910_93",
    "910b": "ascend910b",
    "910_95": "ascend950",
    "310p": "ascend310p",
}


def _origin(name):
    """这个包是从哪装的。

    **版本号分辨不出构建**：ATK 上游 master 与我们打过补丁的分支，`PACKAGE_VERSION`
    都是 `26.8.8`。报告要能归因到具体哪一份 atk，只能靠装机来源（pip 记在
    `direct_url.json` 里的 url 与 sha256）。装机方式没留下来源时返回空 dict。
    """
    try:
        import importlib.metadata as metadata
        raw = metadata.distribution(name).read_text("direct_url.json")
    except Exception:  # noqa: BLE001 - 取不到来源不该影响探环境
        return {}
    if not raw:
        return {}
    try:
        info = json.loads(raw)
    except ValueError:
        return {}
    return {"url": info.get("url", ""),
            "hash": (info.get("archive_info") or {}).get("hash", "")}


def _module(name):
    try:
        mod = __import__(name)
    except Exception as exc:  # noqa: BLE001 - 缺件原因要原样报给用户
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "version": getattr(mod, "__version__", getattr(mod, "PACKAGE_VERSION", "unknown")),
        # path 与 origin 一起看：path 认得出 PYTHONPATH 挂上来的检出，
        # origin 认得出装进 site-packages 的是哪个 wheel。
        "path": getattr(mod, "__file__", "unknown"),
        "origin": _origin(name),
    }


# **进程名可以是空的，不能用 `(\S+)` 去要求它。** 实测卡上有两个
# `Process id:907864  Process name:                  Process memory(MB):134`，
# 名字整列是空格，旧正则匹配不上，这两张卡就被判成空闲——而 runner 在它们
# 上面挂起不返回。判据是**设备上有没有别人的进程**，跟他叫什么无关。
DEVICE_PROC = re.compile(
    r"Process id:(\d+)\s+Process name:(.*?)\s*Process memory\(MB\):(\d+)")


def chip_map():
    """logic device id → (npu id, chip id)，取自 `npu-smi info -m` 的 Chip Logic ID 列。

    `npu-smi info -t proc-mem` 只收 `-i <npu> -c <chip>`，而跑测各处传的是
    ACL/torch 的 logic device id（`--devices` 的取值）。两者的对应关系**只能查，
    不能按 `npu*2+chip` 算**——每卡芯片数随机型变（A2 是 1，A3 是 2）。
    Mcu 行的 Chip Logic ID 是 `-`，跳过。
    """
    try:
        out = subprocess.run(["npu-smi", "info", "-m"], capture_output=True,
                             text=True, timeout=20, check=False).stdout
    except Exception:  # noqa: BLE001
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].isdigit() or not parts[2].isdigit():
            continue
        table[int(parts[2])] = (parts[0], parts[1])
    return table


def own_pids():
    """本进程及其全部后代的 pid。

    隔离复验跑在全量轮之后，那时跑测脚本自己拉起过 atk。把自己的进程算成别人的
    占用会直接把跑测拦死，所以按**进程树**排除，不按进程名排除——进程名判据
    正是上一版栽的地方。
    """
    try:
        out = subprocess.run(["ps", "-eo", "pid,ppid"], capture_output=True,
                             text=True, timeout=20, check=False).stdout
    except Exception:  # noqa: BLE001
        return set()
    children = {}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        children.setdefault(int(parts[1]), []).append(int(parts[0]))
    seen, stack = set(), [os.getpid()]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, []))
    return seen


def device_busy(device_ids):
    """这些 logic device 上有**任何**别人的进程时，把它们列成人话。

    **判据是设备上有没有进程，不是进程叫不叫 atk。** 上一版拿 `ps` 找命令行里
    有 `atk` 的进程，于是别人用 `python engine/main.py …` 占着卡时闸门直接放行。
    实测代价：别人跑非 atk 的训练脚本占着卡，好用例被判成失败，换到空闲卡上
    单卡重跑全过——一轮「缺陷」里近两成是这么来的。

    共卡的后果不止性能失真：隔离复验会把别人的 aicore 异常算成本算子的失败，
    而这一层没有任何报错，只是结论错。

    npu-smi 不可用或映射表读不出来时返回空不拦——把跑测卡死在一个诊断步骤上更糟。
    """
    table = chip_map()
    if not table:
        return []
    mine = own_pids()
    hits = []
    for want in sorted(device_ids):
        if int(want) not in table:
            continue
        npu, chip = table[int(want)]
        try:
            out = subprocess.run(
                ["npu-smi", "info", "-t", "proc-mem", "-i", npu, "-c", chip],
                capture_output=True, text=True, timeout=20, check=False).stdout
        except Exception:  # noqa: BLE001
            continue
        for pid, name, mem in DEVICE_PROC.findall(out):
            if int(pid) in mine:
                continue
            hits.append(f"device {want}（npu {npu} chip {chip}）："
                        f"pid {pid} {name.strip() or '(进程名为空)'} 占 {mem} MB")
    return hits


def _free_busy():
    """现查一遍所有 logic device 的占用。返回 (全部, 空闲, 被占, 占用明细)。"""
    logic = sorted(chip_map())
    busy_lines = device_busy(logic)
    busy_ids = sorted({int(m.group(1)) for m in
                       (re.search(r"device (\d+)", ln) for ln in busy_lines) if m})
    return logic, [d for d in logic if d not in busy_ids], busy_ids, busy_lines


def free_devices():
    """现在没人占的 logic device 号。

    **跑测各处要卡时都调它现查，不要沿用 A1 写进 env.json 的那份。** A1 到隔离
    复验之间隔着编译、部署、精度全量，几十分钟里别人随时会占上来。

    返回的是 logic device 号（`--devices` 要填的号），不是 `npu-smi info` 表里的
    物理卡号——A3 一张卡两个芯片，两者数量差一倍。
    """
    return _free_busy()[1]


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
    # 卡号给多算子并行分卡用：一个算子占一张卡，共卡会静默毁掉性能结论。
    # 健康卡号来自 `npu-smi info` 的表，logic device 号来自 `-m` 的 Chip Logic ID：
    # A3 一张卡两个芯片，前者数卡、后者才是 `--devices` 要填的号，两者不是一回事。
    ids = sorted({int(m) for m in re.findall(r"^\| (\d+)\s+\S+\s+\| OK", out.stdout,
                                            re.MULTILINE)})
    logic, free, busy_ids, busy_lines = _free_busy()
    return {"available": healthy > 0, "healthy_chips": healthy, "device_ids": ids,
            "logic_devices": logic, "free_devices": free,
            "busy_devices": busy_ids, "busy_detail": busy_lines}


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


# CANN 版本号的落点。装机目录下没有统一的版本文件，逐个试。
CANN_VERSION_FILES = ("compiler/version.info", "version.info",
                      "aarch64-linux/ascend_toolkit_install.info",
                      "x86_64-linux/ascend_toolkit_install.info")


def _cann_version(cann_home):
    """读 CANN 版本号，读不到返回空串——它只进报告抬头，不参与任何判据。"""
    for name in CANN_VERSION_FILES:
        path = Path(cann_home or "") / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip().lower() == "version" and value.strip():
                return value.strip().strip('"')
    return ""


def _vendor_lib(opp_root):
    """在装出来的 opp 根下找 libcust_opapi.so。"""
    if not opp_root or not Path(opp_root).is_dir():
        return None
    matches = sorted(Path(opp_root).glob("vendors/*/op_api/lib/libcust_opapi.so"))
    return matches[0] if matches else None


def find_set_env(cann_home):
    """env.sh 里该 source 哪个 set_env.sh。返回真实存在的路径，找不到返回空串。

    **不能拿 `ASCEND_TOOLKIT_HOME` 的父目录推。** set_env.sh 会把它解到带版本号的
    真实目录（`.../Ascend/cann-9.0.0-beta.1`），那个父目录下根本没有 set_env.sh；
    只有 `<装机根>/ascend-toolkit/` 这类中间目录才有。

    推错的后果是**静默的**：env.sh 里那句 source 带着 `2>/dev/null`，于是下游每条
    命令都在没有 CANN 环境的情况下跑。真机上表现为 `build.sh` 回落到另一处 Ascend
    安装，cmake 报「找不到 ASC 包」——报错方向与真正的原因差了三层。

    所以这里只认**文件真的在**的路径，一个都不在时如实返回空串，让 env.sh 自己喊。
    """
    candidates = []
    # 1) 本进程刚才 source 成功的那份，最可信
    if sourced := os.environ.get("_PROBE_ENV_SET_ENV"):
        candidates.append(sourced)
    if cann_home:
        # 2) 装机目录自己带的，以及它的父目录（`latest` 形态时在这儿）
        candidates += [str(Path(cann_home) / "set_env.sh"),
                       str(Path(cann_home).parent / "set_env.sh")]
    # 3) 标准落点
    candidates += list(SET_ENV_CANDIDATES)
    return next((path for path in candidates if Path(path).is_file()), "")


# 一个验收任务最多占几张卡。**机器是公用的**，`auto` 现查到几张就用几张会让一次
# 验收把空闲卡吃光，别人的任务只能等或被迫共卡，而共卡会让性能结论静默作废。
# 定在 4 不是拍的：实测耗时对并发度**非单调**，用满卡本来就不是最优
# （单卡 15 轮 12.6 min，8 卡 4 轮 1 min 36 s，收益早已进入平坦段）。
# 独占机器时用各脚本的 `--max-devices 0` 解开。
MAX_AUTO_DEVICES = 4


def cap_devices(devices, limit=None):
    """把现查到的空闲卡截到上限。`limit=0` 表示不限。返回 (用哪几张, 说明)。"""
    limit = MAX_AUTO_DEVICES if limit is None else int(limit)
    if limit <= 0 or len(devices) <= limit:
        return list(devices), ""
    return list(devices)[:limit], (
        f"（现查到 {len(devices)} 张，按上限只用 {limit} 张——机器公用，"
        f"独占时加 --max-devices 0）")


def write_env_sh(path, cann_home, python_exe, opp_root, standalone_lib="",
                 pythonpath=""):
    """生成 env.sh。build_install.py 装完包后会再调一次把生效变量补上。

    `standalone_lib` 给了就按自足工程写：那类包装出来是普通共享库，没有 opp
    vendor 层，靠 LD_LIBRARY_PATH 生效，`ASCEND_CUSTOM_OPP_PATH` 与
    `ATK_CUSTOM_OPP_PATH` 在这条链路上没有落点，写了反而让人以为设了就生效。

    `pythonpath` 是 torch 扩展编出来的落点。**ATK 的 worker 是另起的进程**，
    它靠这个变量找注册模块；少了它探针报的是「实现没装进来」，指向装包。
    """
    set_env = find_set_env(cann_home)
    lines = [
        "#!/bin/bash",
        "# 由 probe_env.py 生成。跑测侧每条命令都要先 source 它。",
    ]
    if set_env:
        lines.append(f"source {set_env} >/dev/null 2>&1")
    else:
        # 静默地少一套 CANN 环境，后面每一步都会以错误的方向报错。宁可吵。
        lines += ['echo "env.sh: 找不到 CANN 的 set_env.sh，本 shell 没有 CANN 环境。" >&2',
                  'echo "        构建与跑测都会走到别的 Ascend 安装上，先手工 source 再来。" >&2']
    lines.append(f'export PATH="{Path(python_exe).parent}:$PATH"')
    if pythonpath:
        lines += [
            "",
            "# torch 扩展的落点。ATK 的 worker 是另起的进程，靠它找注册模块。",
            f'export PYTHONPATH="{pythonpath}:$PYTHONPATH"',
        ]
    if standalone_lib:
        lines += [
            "",
            "# 自足工程装出来的是普通共享库，没有 opp vendor 层。",
            "# 待验收实现靠 LD_LIBRARY_PATH 被加载，跑测前用 /proc/self/maps 核。",
            f'export LD_LIBRARY_PATH="{standalone_lib}:$LD_LIBRARY_PATH"',
        ]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return Path(standalone_lib)
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

    _reexec_under_cann()

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
        "cann_version": _cann_version(os.environ.get("ASCEND_TOOLKIT_HOME", "")),
        "set_env": find_set_env(os.environ.get("ASCEND_TOOLKIT_HOME", "")),
        "opp_root": opp_root,
        "atk_cli": shutil.which("atk") or "",
    }

    missing = [name for name in ("atk", "torch", "torch_npu") if not report[name]["available"]]
    if not report["set_env"]:
        # env.sh 里没有 source 就等于后面每条命令都没有 CANN 环境，
        # 而失败会在 A2 才暴露、且报错指向 cmake。拦在这里。
        missing.append("set_env.sh")
    if not report["npu"]["available"]:
        missing.append("npu")
    if not report["cann_home"]:
        missing.append("cann")
    report["missing"] = missing

    lib = write_env_sh(args.write_env_sh, report["cann_home"], sys.executable, opp_root)
    report["custom_opp_lib"] = str(lib) if lib else ""

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(stage_clock.stamp(report), handle, ensure_ascii=False, indent=2)

    print(f"python      {report['python']} ({report['python_version']})")
    for name in ("atk", "torch", "torch_npu"):
        item = report[name]
        print(f"{name:<11} "
              f"{item['version'] if item['available'] else '缺失 — ' + item['error']}")
    npu = report["npu"]
    print(f"npu         "
          f"{'健康 %s 片' % npu.get('healthy_chips') if npu['available'] else '不可用 — ' + npu['error']}")
    if npu.get("logic_devices"):
        free, busy = npu.get("free_devices", []), npu.get("busy_devices", [])
        print(f"空闲卡号    {free or '**一张都没有**'}  "
              f"（--devices 填这里的号，**一个算子占一张卡**）")
        if busy:
            print(f"被占卡号    {busy}  ——跑测会拒绝用它们，共卡的结论不可信：")
            for line in npu.get("busy_detail", [])[:8]:
                print(f"            {line}")
    print(f"soc         {report['soc']['raw'] or '认不出'} "
          f"→ build.sh --soc={report['soc']['build_flag'] or '?'}")
    print(f"cann        {report['cann_home'] or '未 source set_env.sh'}"
          f"{'  ' + report['cann_version'] if report['cann_version'] else ''}")
    print(f"set_env     {report['set_env'] or '**找不到**，env.sh 里没法 source'}")
    print(f"自定义算子包 {report['custom_opp_lib'] or '未安装（A2 会装）'}")
    print(f"写入        {args.out}、{args.write_env_sh}")

    if missing:
        print(f"\n阻塞·未验收 @A1：缺 {'、'.join(missing)}。", file=sys.stderr)
        if "cann" in missing or "npu" in missing:
            # 本脚本已经自己试过 source（_reexec_under_cann），走到这里说明
            # set_env.sh 不在标准落点，或者 source 了也没设上——是真缺，不是假阻塞。
            print("本脚本已自动找过 set_env.sh 仍然缺件。CANN 装在非标准路径时，"
                  "手工 source 它的 set_env.sh 再跑一次。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
