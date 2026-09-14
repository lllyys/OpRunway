"""单算子冒烟构建：证明“环境真能编出算子包”。

跑通的最小闭环：source set_env.sh → cd 仓 → build.sh --pkg --ops=<一个算子> → 出 .run。
成功即说明 CANN toolkit / 编译器 / 仓 tag / vendored 依赖 全部就位。

零硬编码：set_env / soc / 仓路径 / 算子 都是参数；算子不给则自动挑一个最小可建的。

CLI:
  python3 smoke_build.py --repo-path <path> --soc ascend950 [--set-env <p>] [--op <name>] [--jobs N]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path


def _available_cores() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 8


def pick_smoke_op(repo_path: Path, explicit: str | None = None) -> str | None:
    """挑一个最小可建算子：优先显式指定，否则取第一个含 op_kernel/op_host 的算子目录名。"""
    if explicit:
        return explicit
    for kdir in sorted(repo_path.glob("**/op_kernel")):
        sp = str(kdir)
        if "/3rd/" in sp or "/fast_kernel_launch_example/" in sp:
            continue
        op = kdir.parent.name
        if op and op not in {"common"}:
            return op
    return None


def smoke_build(repo_path: str, soc: str, set_env: str | None,
                op: str | None, jobs: int, timeout: int) -> dict:
    rp = Path(repo_path)
    if not (rp / "build.sh").is_file():
        return {"ok": False, "stage": "precheck", "reason": f"{repo_path} 下无 build.sh"}

    target_op = pick_smoke_op(rp, op)
    if not target_op:
        return {"ok": False, "stage": "precheck", "reason": "未找到可建算子目录（op_kernel/op_host）"}

    j = jobs if jobs > 0 else _available_cores()
    src = f'source "{set_env}" && ' if set_env else ""
    cmd = f"{src}bash build.sh --pkg --soc={soc} --ops={target_op} -j{j}"

    t0 = time.time()
    try:
        res = subprocess.run(["bash", "-lc", cmd], cwd=str(rp),
                             capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "build", "op": target_op, "reason": f"超时 {timeout}s", "cmd": cmd}
    dur = round(time.time() - t0, 1)

    runs = list((rp / "build_out").glob("*.run")) if (rp / "build_out").is_dir() else []
    ok = res.returncode == 0 and len(runs) > 0
    tail = "\n".join((res.stdout + res.stderr).splitlines()[-15:])
    return {
        "ok": ok, "stage": "build", "op": target_op, "soc": soc, "jobs": j,
        "exit_code": res.returncode, "duration_s": dur,
        "run_pkg": str(runs[-1]) if runs else None,
        "cmd": cmd, "log_tail": tail,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="单算子冒烟构建，验证环境就绪")
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--soc", required=True, help="目标 SOC，如 ascend950（由 SKILL 询问用户）")
    ap.add_argument("--set-env", default=None, help="set_env.sh 路径；不给则假设环境已 source")
    ap.add_argument("--op", default=None, help="指定冒烟算子；不给则自动挑一个")
    ap.add_argument("--jobs", type=int, default=0, help="build -j；0=全核")
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    r = smoke_build(args.repo_path, args.soc, args.set_env, args.op, args.jobs, args.timeout)
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
