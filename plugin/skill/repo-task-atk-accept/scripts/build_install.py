#!/usr/bin/env python3
"""把待验收算子目录合进母仓、构建、装包，并验证符号确实可见。

社区算子目录是母仓的一个子目录，独立构建不出来，必须回母仓跑 build.sh。
退出码 0 装好且符号可见，2 构建或安装失败，3 符号不可见，4 入参路径不对。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BUILD_TIMEOUT = 5400
INSTALL_TIMEOUT = 600


def _find_op_dir(project, op):
    """待验收工程可能是算子目录本身，也可能是带着母仓路径前缀的解压包。

    **按目录结构找，不按名字找。** aclnn 接口名与算子目录名常常不同：
    aclnnIndexFillTensor 的目录是 index_fill，不是 index_fill_tensor。
    名字只在有多个候选时用来消歧。
    """
    project = Path(project).resolve()
    if not project.is_dir():
        return None, f"{project} 不是目录"
    if (project / "op_kernel").is_dir() or (project / "op_host").is_dir():
        return project, None

    matches = [p for p in project.rglob("*")
               if p.is_dir() and "build" not in p.parts
               and ((p / "op_kernel").is_dir() or (p / "op_host").is_dir())]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, (f"{project} 下找不到含 op_kernel/ 或 op_host/ 的目录。"
                      f"--project 要指到算子目录或它的解压根。")

    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", op).lower()
    named = [p for p in matches if p.name == snake]
    if len(named) == 1:
        return named[0], None
    prefixed = [p for p in matches if snake.startswith(p.name)]
    if len(prefixed) == 1:
        return prefixed[0], None
    listed = "、".join(str(p) for p in matches)
    return None, f"{project} 下有多个候选（{listed}），--project 指具体一个。"


def _target_in_parent(parent, op_dir, op):
    """算子在母仓里的落点：优先沿用解压包自带的相对路径，否则按现有同名目录找。"""
    parent = Path(parent).resolve()
    snake = op_dir.name
    parts = op_dir.parts
    for anchor in ("experimental", "index", "math", "conversion"):
        if anchor in parts:
            relative = Path(*parts[parts.index(anchor):])
            if (parent / relative).parent.is_dir():
                return parent / relative
    existing = [p for p in parent.rglob(snake)
                if p.is_dir() and "build" not in p.parts and "build_out" not in p.parts]
    if len(existing) == 1:
        return existing[0]
    return parent / "experimental" / "math" / snake


def _force_rmtree(path):
    """装出来的 opp 目录里有只读的 scripts/，直接 rmtree 会 PermissionError。"""
    def _chmod_retry(func, target, _exc):
        os.chmod(Path(target).parent, 0o755)
        os.chmod(target, 0o755)
        func(target)

    shutil.rmtree(path, onexc=_chmod_retry)


def _run(command, cwd, timeout, log_path):
    started = time.time()
    with open(log_path, "w", encoding="utf-8") as handle:
        proc = subprocess.run(command, cwd=cwd, stdout=handle,
                              stderr=subprocess.STDOUT, timeout=timeout, check=False)
    return proc.returncode, time.time() - started


def _tail(path, count=25):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-count:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--op", required=True, help="算子名，如 Roll")
    parser.add_argument("--project", required=True, help="待验收算子工程目录")
    parser.add_argument("--parent-repo", required=True, help="母仓根，含 build.sh")
    parser.add_argument("--soc", required=True, help="build.sh 的 --soc 取值")
    parser.add_argument("--opp-root", default="opp", help="装到哪，默认 opp/")
    parser.add_argument("--vendor-name", default="", help="默认 <op 小写>_atk")
    parser.add_argument("--jobs", default="16", help="build.sh -j，默认 16")
    parser.add_argument("--skip-build", action="store_true", help="复用已有 build_out")
    parser.add_argument("--no-clean", action="store_true",
                        help="不清 build/，只在连续构建同一个算子时用")
    parser.add_argument("-o", "--out", default="install.json")
    args = parser.parse_args()

    op_dir, problem = _find_op_dir(args.project, args.op)
    if problem:
        print(problem, file=sys.stderr)
        return 4

    parent = Path(args.parent_repo).resolve()
    if not (parent / "build.sh").is_file():
        print(f"{parent} 下没有 build.sh，不是母仓根。见 build-deploy.md「母仓对照」。",
              file=sys.stderr)
        return 4

    snake = op_dir.name
    vendor_name = args.vendor_name or f"{snake}_atk"
    logs = Path("evidence")
    logs.mkdir(parents=True, exist_ok=True)
    record = {
        "op": args.op,
        "op_dir": str(op_dir),
        "parent_repo": str(parent),
        "soc": args.soc,
        "vendor_name": vendor_name,
    }

    # 1. 同步进母仓
    target = _target_in_parent(parent, op_dir, args.op)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(op_dir, target)
    record["target"] = str(target)
    print(f"同步      {op_dir}\n       -> {target}")

    # 2. 构建
    build_log = logs / "build.log"
    if args.skip_build:
        print("构建      跳过（--skip-build）")
        record["build"] = {"skipped": True}
    else:
        if not args.no_clean:
            # 母仓 build/ 是共享且有状态的：换一个 --ops 再构建时，
            # libcust_opapi.so 会停在上一个算子那次链接，obj 编了也不重链，
            # 装出来的包里只有上一个算子的符号。清掉才不会串。
            for stale in ("build", "build_out"):
                path = parent / stale
                if path.is_dir():
                    shutil.rmtree(path)
            print("清理      已删 build/ 与 build_out/（换算子必须清，否则符号会串）")
        command = ["bash", "build.sh", "--pkg", "--experimental",
                   f"--soc={args.soc}", f"--ops={snake}",
                   f"--vendor_name={vendor_name}", f"-j{args.jobs}"]
        print(f"构建      {' '.join(command)}")
        print(f"          日志 {build_log}，最长 {BUILD_TIMEOUT}s")
        try:
            code, elapsed = _run(command, parent, BUILD_TIMEOUT, build_log)
        except subprocess.TimeoutExpired:
            print(f"\n构建超过 {BUILD_TIMEOUT}s 没结束。", file=sys.stderr)
            return 2
        record["build"] = {"command": " ".join(command), "returncode": code,
                           "elapsed_seconds": round(elapsed, 1), "log": str(build_log)}
        if code != 0:
            print(f"\n构建失败，退出码 {code}。日志尾部：", file=sys.stderr)
            for line in _tail(build_log):
                print(f"  {line}", file=sys.stderr)
            return 2
        print(f"          成功，耗时 {elapsed:.0f}s")

    # 3. 装包
    packages = sorted((parent / "build_out").glob(f"cann-ops-*{vendor_name}*.run"))
    if not packages:
        packages = sorted((parent / "build_out").glob("cann-ops-*.run"))
    if not packages:
        print(f"{parent}/build_out 下没有 .run 包。构建没产出，看 {build_log}。",
              file=sys.stderr)
        return 2
    package = max(packages, key=lambda p: p.stat().st_mtime)

    opp_root = Path(args.opp_root).resolve()
    if opp_root.exists():
        _force_rmtree(opp_root)
    opp_root.mkdir(parents=True)

    install_log = logs / "install.log"
    command = [str(package), "--quiet", f"--install-path={opp_root}"]
    print(f"装包      {package.name} -> {opp_root}")
    try:
        code, _ = _run(command, parent / "build_out", INSTALL_TIMEOUT, install_log)
    except subprocess.TimeoutExpired:
        print(f"\n装包超过 {INSTALL_TIMEOUT}s 没结束。", file=sys.stderr)
        return 2
    record["install"] = {"package": str(package), "returncode": code,
                         "opp_root": str(opp_root), "log": str(install_log)}
    if code != 0:
        print(f"\n装包失败，退出码 {code}。日志尾部：", file=sys.stderr)
        for line in _tail(install_log):
            print(f"  {line}", file=sys.stderr)
        return 2

    # 4. 验证符号可见
    libs = sorted(opp_root.glob("vendors/*/op_api/lib/libcust_opapi.so"))
    if not libs:
        print(f"\n{opp_root} 下没有 vendors/*/op_api/lib/libcust_opapi.so。"
              f"构建时 --ops={snake} 可能没匹配上算子目录名。", file=sys.stderr)
        return 3
    lib = libs[0]
    symbol = f"aclnn{args.op}GetWorkspaceSize"
    nm = subprocess.run(["nm", "-D", str(lib)], capture_output=True, text=True, check=False)
    if symbol not in nm.stdout:
        exported = sorted({m for m in re.findall(r"aclnn\w+GetWorkspaceSize", nm.stdout)})
        print(f"\n{lib} 里没有 {symbol}。", file=sys.stderr)
        print(f"包里实际导出的是：{'、'.join(exported) or '（一个都没有）'}", file=sys.stderr)
        print("--op 的大小写要与 aclnn 接口名一致，见 build-deploy.md"
              "「装完了但符号找不到」。", file=sys.stderr)
        return 3

    vendor_dir = lib.parents[2]
    record["symbol"] = symbol
    record["custom_opp_lib"] = str(lib)
    record["vendor_dir"] = str(vendor_dir)

    # 5. 重写 env.sh 把三个环境变量补上
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from probe_env import write_env_sh  # noqa: E402 - 同目录量具，装完才有值可写

    write_env_sh("evidence/env.sh", os.environ.get("ASCEND_TOOLKIT_HOME", ""),
                 sys.executable, str(opp_root))

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)

    print(f"符号      {symbol} 可见")
    print(f"vendor    {vendor_dir}")
    print(f"env.sh    已重写，含 ASCEND_CUSTOM_OPP_PATH 与 ATK_CUSTOM_OPP_PATH")
    print(f"写入      {args.out}")
    print("\n下一条命令起都要先 source evidence/env.sh。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
