#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_dut.py —— harness 的 S1 构建段：钉死命令 + 构建取证。

一条命令出产物（PROBE §6.1，A2 轮真机实测退 0）：

    bash build.sh --ops=<op[,op...]> --soc=<soc>

四条纪律写进代码，不靠人记：

| 纪律 | 原因 |
| --- | --- |
| `--soc` 必须显式给 | 缺省是 `ascend910b`，不是目标 SOC（build.sh:145、CMakeLists.txt:19-21） |
| 禁 `--run` | 收尾 `rm -rf test/<op>/data/{input,output,golden}` 抹掉中间证据（build.sh:243） |
| 禁 `--pkg` | 要联网拉 makeself（cmake/third_party/makeself-fetch.cmake），run 包对 harness 无用 |
| 不复用开发者的 gen_data.py/verify_result.py | 两份脚本 argv 顺序相反，复用会静默串参 |

`build.sh` 每次 `rm -rf build` 后重建（build.sh:140），所以这条命令天然幂等、
无增量污染。

构建取证落 `build_provenance.json`，五类证据缺一不可——没有它们，后面的三键结果
无法回答「这是哪份源码在哪台机上产出的」：

1. 源码身份：git commit 与工作树是否 clean；无 git 元数据时落源码树的内容摘要。
2. 构建命令：逐字命令、CWD、退出码、stdout/stderr 全文。
3. 产物哈希：`build/libops_solver.so` 与 `build_out/` 下的库与头，逐个 sha256。
4. 实际加载库：执行子进程对算子函数地址取 `dladdr` 得到的 `.so` 路径与其 sha256
   （由 exec_case 回填，本阶段只记产物哈希供对账）。
5. 环境版本：CANN 版本、`npu-smi` 回显、g++/cmake/Python 版本、设备号。

用法：
    build_dut.py --repo <ops-solver 仓根> --ops cmatinv_batched --soc ascend910_93
                 --out <取证 JSON 路径> [--device 0] [--skip-build]

退出码：0 构建成功且产物齐备；2 入口参数或纪律违例；3 构建失败或产物缺失。
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

TOOL = "build_dut.py"
TOOL_VER = "h1-r1"

FORBIDDEN_FLAGS = {
    "--run": "收尾会 rm -rf test/<op>/data/{input,output,golden} 抹掉中间证据",
    "--pkg": "要联网拉 makeself，且 run 包对 harness 无用",
}

# 产物判据（PROBE §6.1 表，A2 轮真机逐项核过）
REQUIRED_ARTIFACTS = (
    "build/libops_solver.so",
    "build_out/lib64/libops_solver.so",
    "build_out/include/cann_ops_solver.h",
    "build_out/include/cann_ops_solver_common.h",
)


class BuildError(RuntimeError):
    """构建纪律违例或产物缺失——报错停下，不降级继续。"""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd, cwd=None, env=None, timeout=3600):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors="replace")
    return {"cmd": " ".join(shlex.quote(c) for c in cmd),
            "cwd": str(cwd) if cwd else None, "returncode": p.returncode,
            "output": p.stdout, "seconds": round(time.time() - t0, 3)}


def _tool_version(cmd):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=60, errors="replace")
        return p.stdout.strip().splitlines()[0] if p.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def source_identity(repo):
    """源码身份：优先 git 事实；无 git 元数据时落内容摘要（tar 上传的树常无 .git）。"""
    repo = Path(repo)
    git = {}
    if (repo / ".git").exists():
        for key, cmd in (("commit", ["git", "rev-parse", "HEAD"]),
                         ("subject", ["git", "log", "-1", "--format=%s"]),
                         ("committed_at", ["git", "log", "-1", "--format=%cI"]),
                         ("porcelain", ["git", "status", "--porcelain"])):
            r = _run(cmd + [], cwd=repo)
            if r["returncode"] == 0:
                git[key] = r["output"].strip()
        git["clean"] = (git.get("porcelain") == "")
        git.pop("porcelain", None)
    # 内容摘要：src/ 与 include/ 下全部文件按相对路径排序后逐个喂哈希。
    h = hashlib.sha256()
    count = 0
    for sub in ("include", "src"):
        root = repo / sub
        if not root.is_dir():
            continue
        for f in sorted(p for p in root.rglob("*") if p.is_file()):
            h.update(str(f.relative_to(repo)).encode())
            h.update(sha256_file(f).encode())
            count += 1
    return {"repo": str(repo), "git": git or None,
            "content_anchor": h.hexdigest(), "anchored_files": count}


def environment_facts(device_id):
    """环境版本：CANN、设备、工具链。缺项记 null，不编造。"""
    ascend = os.environ.get("ASCEND_HOME_PATH") or os.environ.get("ASCEND_TOOLKIT_HOME")
    # version.info 的位置随安装形态变：9.0.0 容器镜像只有 compiler/ 与 opp/ 两份子包
    # 版本（实测），包根那份不存在。逐个候选找，找到几份记几份。
    cann_version = {}
    if ascend:
        for rel in ("version.info", "compiler/version.info", "opp/version.info",
                    "runtime/version.info"):
            info = Path(ascend) / rel
            if info.is_file():
                cann_version[rel] = info.read_text(
                    encoding="utf-8", errors="replace").strip()
    driver_info = Path("/usr/local/Ascend/driver/version.info")
    if driver_info.is_file():
        cann_version["driver"] = driver_info.read_text(
            encoding="utf-8", errors="replace").strip()
    return {
        "driver_version_info": cann_version.pop("driver", None),
        "ascend_home": ascend,
        "cann_version_info": cann_version,
        "npu_smi": _tool_version(["npu-smi", "info", "-l"]),
        "gxx": _tool_version(["g++", "--version"]),
        "cmake": _tool_version(["cmake", "--version"]),
        "python": sys.version.split()[0],
        "uname": _tool_version(["uname", "-a"]),
        "device_id": device_id,
    }


def build_command(ops, soc):
    """钉死命令。`--soc` 必须显式，禁用项直接拒绝——不接受调用方传进来。"""
    if not ops:
        raise BuildError("--ops 不能为空")
    if not soc:
        raise BuildError("--soc 必须显式给：缺省是 ascend910b，不是目标 SOC")
    for item in list(ops) + [soc]:
        for flag, why in FORBIDDEN_FLAGS.items():
            if flag in str(item):
                raise BuildError(f"禁用 {flag}：{why}")
    return ["bash", "build.sh", "--ops=" + ",".join(ops), "--soc=" + soc]


def collect_artifacts(repo):
    """产物哈希与缺失清单。"""
    repo = Path(repo)
    found, missing = {}, []
    for rel in REQUIRED_ARTIFACTS:
        p = repo / rel
        if p.is_file():
            found[rel] = {"sha256": sha256_file(p), "bytes": p.stat().st_size}
        else:
            missing.append(rel)
    return found, missing


def collect_test_binaries(repo, ops):
    """开发者自带的 test 执行件（任务书性能章的 msprof 命令假定了这个路径）。"""
    repo = Path(repo)
    out = {}
    for op in ops:
        p = repo / "build" / "test" / op / f"{op}_test"
        out[op] = ({"path": str(p.relative_to(repo)), "sha256": sha256_file(p),
                    "bytes": p.stat().st_size} if p.is_file() else None)
    return out


def soc_echo(output):
    """构建日志里的 SOC 回显——判定目标 SOC 真的生效了，而不是落回缺省。"""
    m = re.search(r"SOC_VERSION=(\S+?),\s*NPU_ARCH=(\S+)", output)
    return {"soc_version": m.group(1), "npu_arch": m.group(2)} if m else None


def build(repo, ops, soc, device_id=0, skip_build=False, timeout=3600):
    """跑一次 S1 并返回取证记录。skip_build 只取证不重建（复用已有产物）。"""
    repo = Path(repo).resolve()
    if not (repo / "CMakeLists.txt").is_file() or not (repo / "build.sh").is_file():
        raise BuildError(f"{repo} 不像 ops-solver 仓根（缺 CMakeLists.txt 或 build.sh）")
    cmd = build_command(ops, soc)
    prov = {
        "tool": {"name": TOOL, "ver": TOOL_VER},
        "stage": "S1",
        "ops": list(ops),
        "soc": soc,
        "skip_build": bool(skip_build),
        "source": source_identity(repo),
        "env": environment_facts(device_id),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if skip_build:
        prov["build"] = {"cmd": " ".join(shlex.quote(c) for c in cmd),
                         "skipped": True, "returncode": None, "output": None}
    else:
        run = _run(cmd, cwd=repo, timeout=timeout)
        prov["build"] = run
        prov["soc_echo"] = soc_echo(run["output"] or "")
        if run["returncode"] != 0:
            prov["artifacts"], prov["missing"] = collect_artifacts(repo)
            raise BuildError(f"构建失败：退出码 {run['returncode']}；"
                             f"日志尾部 {(run['output'] or '')[-800:]!r}")
    found, missing = collect_artifacts(repo)
    prov["artifacts"] = found
    prov["missing"] = missing
    prov["test_binaries"] = collect_test_binaries(repo, ops)
    prov["header_text_sha256"] = sha256_file(repo / "include" / "cann_ops_solver.h")
    if missing:
        raise BuildError(f"构建后产物缺失：{missing}")
    return prov


def main(argv=None):
    ap = argparse.ArgumentParser(prog=TOOL, description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, help="ops-solver 仓根")
    ap.add_argument("--ops", required=True, help="逗号分隔算子名，透传 build.sh --ops")
    ap.add_argument("--soc", required=True, help="目标 SOC，必须显式给")
    ap.add_argument("--out", required=True, help="构建取证 JSON 输出路径")
    ap.add_argument("--device", type=int, default=0, help="设备号，只进取证记录")
    ap.add_argument("--skip-build", action="store_true",
                    help="不重建，只对已有产物取证（排错用）")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)

    ops = [o.strip() for o in args.ops.split(",") if o.strip()]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        prov = build(args.repo, ops, args.soc, args.device, args.skip_build,
                     args.timeout)
    except BuildError as exc:
        print(f"[{TOOL}] 构建段失败: {exc}", file=sys.stderr)
        return 3
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[{TOOL}] 构建段异常: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(prov, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    echo = prov.get("soc_echo") or {}
    print(f"[{TOOL}] 构建成功 ops={','.join(ops)} soc={args.soc} "
          f"回显={echo.get('soc_version')}/{echo.get('npu_arch')} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
