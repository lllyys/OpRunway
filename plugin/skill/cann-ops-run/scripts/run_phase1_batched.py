#!/usr/bin/env python3
"""
Phase 1 优化版：合并 build + 串行 install + run_example 受限并发
- 仓间：4 个仓并行（build/ 独立）
- 仓内：
  * 一次性 build 全部目标算子（--ops=op1,op2,...）
  * 一次性 install
  * run_example 逐个跑（共享 NPU，避免争抢）
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import (  # noqa: E402
    resolve_ops, OpsResolutionError, CANN_SET_ENV_SH, parse_repo_mapping, detect_soc,
    persist_quickstart_record,
)
from quickstart_probe import discover_command_templates  # noqa: E402

_DEFAULT_BUILD_TEMPLATE = "bash build.sh --pkg --soc={soc} --ops={op} -j{jobs}"
_DEFAULT_RUN_TEMPLATE = "bash build.sh --run_example {op} eager cust --vendor_name=custom"

# 所有产物按仓写到 CWD/cann-ops-report/<repo>/test/，不写 skill 安装目录
REPORT_ROOT = Path.cwd() / "cann-ops-report"

# SOC 由 main() 从 --soc 参数填入，子进程通过 fork 继承
SOC: str = ""

# 目标算子源（CSV / 文件路径）由 main() 填入；为空时各仓回退到 cann-950-feature-scan 产物
CLI_OPS: str = ""
CLI_OPS_FILE: str = ""

# cann-issue-track retest 注入的额外参数
BUILD_EXTRA_ARGS: str = ""
RUN_EXTRA_ARGS: str = ""
ENV_EXTRA: dict[str, str] = {}

# set_env.sh 推导统一走 utils，避免双处实现漂移（修 utils 不生效的隐性坑）
SET_ENV_SH = CANN_SET_ENV_SH

# 用户已确认「某仓解析不出命令模板时改用默认模板」；由 main() 从 --force-default-template 填入
FORCE_DEFAULT_TEMPLATE: bool = False


def _available_cores() -> int:
    """当前进程实际可用核数（感知容器 cgroup / CPU 亲和性，比 cpu_count 准）。"""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:        # 非 Linux
        return os.cpu_count() or 16


# build 并行度 -j：默认全核，靠 OS 调度自适应回收（某仓编完，空核自动给其余仓）。
# 由 main() 用 --jobs 覆盖；0/未给 → 全核。子进程经 _worker_config 继承。
JOBS: int = _available_cores()


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Phase 1 batched runner (仓内合并 build + 仓间并发)")
    ap.add_argument("--repo-mapping", required=True,
                    help="仓名到本地路径的映射，CSV 格式：repo1=path1,repo2=path2,...")
    ap.add_argument("--soc", default="",
                    help="目标 SOC（如 ascend910b / ascend950 等）。不给则 T4 用 acl.get_soc_name() "
                         "自动探测+映射；探不到才报错让 skill 询问用户")
    ap.add_argument("--ops", default="",
                    help="目标算子 CSV，例如 op1,op2,op3")
    ap.add_argument("--ops-file", default="",
                    help="目标算子文件（.json / 一行一算子纯文本）")
    ap.add_argument("--env-extra", default="",
                    help="额外环境变量，K=V,K2=V2 格式；由 cann-issue-track retest 注入")
    ap.add_argument("--build-extra-args", default="",
                    help="追加到 build.sh 的额外构建参数（如 -DFOO=1）")
    ap.add_argument("--run-extra-args", default="",
                    help="追加到 run_example 命令的额外运行参数")
    ap.add_argument("--jobs", type=int, default=0,
                    help="build 并行度 -j；0=自动取全部可用核（默认，靠 OS 调度自适应）")
    ap.add_argument("--force-default-template", action="store_true",
                    help="某仓 QUICKSTART.md 解析不出命令模板时，用户已确认改用默认模板；"
                         "不传此参数时该仓会跳过跑测并报 TEMPLATE_UNRESOLVED，等 skill 问过用户再决定")
    return ap


def _parse_env_extra(s: str) -> dict[str, str]:
    """'K=V,L=W' → {'K': 'V', 'L': 'W'}; empty string → {}."""
    if not s:
        return {}
    out = {}
    for part in s.split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _compose_build_cmd(*, soc: str, ops_csv: str, build_extra_args: str = "",
                        build_template: str = _DEFAULT_BUILD_TEMPLATE) -> str:
    cmd = build_template.format(soc=soc, op=ops_csv, jobs=JOBS)
    if build_extra_args:
        cmd += f" {build_extra_args}"
    return cmd


def _compose_run_cmd(*, op: str, run_extra_args: str = "",
                      run_template: str = _DEFAULT_RUN_TEMPLATE) -> str:
    cmd = run_template.format(op=op)
    if run_extra_args:
        cmd += f" {run_extra_args}"
    return cmd


# parse_repo_mapping 复用 utils.parse_repo_mapping（两个 runner 共用，避免重复实现）

# REPO_PATHS 在 main() 里由 CLI 参数填入，仓内子函数通过它查路径
REPO_PATHS: dict[str, str] = {}


def _worker_config() -> dict:
    """打包跑测配置，传给 ProcessPool worker（spawn 平台不继承全局，必须显式传）。"""
    return {
        "soc": SOC, "cli_ops": CLI_OPS, "cli_ops_file": CLI_OPS_FILE,
        "build_extra_args": BUILD_EXTRA_ARGS, "run_extra_args": RUN_EXTRA_ARGS,
        "env_extra": ENV_EXTRA, "repo_paths": REPO_PATHS, "set_env_sh": SET_ENV_SH,
        "jobs": JOBS, "force_default_template": FORCE_DEFAULT_TEMPLATE,
    }


def _init_worker(config: dict) -> None:
    """在每个 worker 进程里恢复模块全局；fork 平台是冗余、spawn 平台是必需。"""
    global SOC, CLI_OPS, CLI_OPS_FILE, BUILD_EXTRA_ARGS, RUN_EXTRA_ARGS, ENV_EXTRA, REPO_PATHS, SET_ENV_SH, JOBS, FORCE_DEFAULT_TEMPLATE
    SOC = config["soc"]
    CLI_OPS = config["cli_ops"]
    CLI_OPS_FILE = config["cli_ops_file"]
    BUILD_EXTRA_ARGS = config["build_extra_args"]
    RUN_EXTRA_ARGS = config["run_extra_args"]
    ENV_EXTRA = config["env_extra"]
    REPO_PATHS = config["repo_paths"]
    SET_ENV_SH = config["set_env_sh"]
    JOBS = config["jobs"]
    FORCE_DEFAULT_TEMPLATE = config["force_default_template"]

# 判定逻辑统一来自 utils.py（避免漂移）
from utils import classify_run_status  # noqa: E402


def extract_ops(repo: str) -> list:
    """按优先级解析目标算子：CLI --ops > --ops-file > cann-950-feature-scan 产物。"""
    return resolve_ops(repo, cli_ops=CLI_OPS or None, cli_ops_file=CLI_OPS_FILE or None)


def find_op_dir(repo_path: Path, op: str) -> Path | None:
    """定位算子目录"""
    for p in repo_path.rglob(op):
        if not (p.is_dir() and p.name == op):
            continue
        sp = str(p)
        if "/3rd/" in sp or "/fast_kernel_launch_example/" in sp:
            continue
        if (p / "op_kernel").exists() or (p / "op_host").exists():
            return p
    return None


def has_examples(op_dir: Path) -> bool:
    ex = op_dir / "examples"
    return ex.is_dir() and any(ex.rglob("test_*.cpp"))


def _kill_process_group(proc: "subprocess.Popen") -> None:
    """SIGTERM then SIGKILL the process group started with start_new_session.

    Falls back to killing just the process if the platform lacks process groups.
    """
    import os as _os
    import signal as _signal
    try:
        pgid = _os.getpgid(proc.pid)
        _os.killpg(pgid, _signal.SIGTERM)
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            _os.killpg(pgid, _signal.SIGKILL)
    except (ProcessLookupError, AttributeError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def run_shell(cmd: str, cwd: Path, log_path: Path, timeout: int) -> dict:
    """运行 shell 命令（带 set_env.sh），日志逐行流式落盘。

    长 build（几十分钟）期间日志文件随时可 tail；不再等命令结束才一次性写入。
    返回结构与旧实现一致（stdout/stderr 分流，便于 classify_log）。
    """
    import threading

    full_cmd = f"source {SET_ENV_SH} && {cmd}"

    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()

    # merge ENV_EXTRA into subprocess env
    env = os.environ.copy()
    env.update(ENV_EXTRA)

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    write_lock = threading.Lock()

    with open(log_path, "w", encoding="utf-8", errors="replace") as logf:
        logf.write(f"$ cd {cwd}\n$ {cmd}\n[timeout={timeout}s]\n\n--- STREAM ---\n")
        logf.flush()

        # start_new_session=True puts the shell into its own process group so a
        # timeout can kill the ENTIRE tree (make/cmake/ccec/.run, …), not just
        # the bash wrapper — otherwise descendants leak and keep holding the NPU.
        proc = subprocess.Popen(
            ["bash", "-c", full_cmd],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=env,
            start_new_session=True,
        )

        def pump(stream, sink: list[str], tag: str) -> None:
            for line in iter(stream.readline, ""):
                sink.append(line)
                with write_lock:
                    logf.write(f"{tag}{line}")
                    logf.flush()
            stream.close()

        threads = [
            threading.Thread(target=pump, args=(proc.stdout, stdout_lines, "")),
            threading.Thread(target=pump, args=(proc.stderr, stderr_lines, "[stderr] ")),
        ]
        for t in threads:
            t.start()

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            proc.wait()
            timed_out = True

        for t in threads:
            t.join()

        exit_code = 124 if timed_out else proc.returncode
        stdout = "".join(stdout_lines)
        stderr = "".join(stderr_lines)
        if timed_out:
            stderr += f"\n[TIMEOUT after {timeout}s]\n"

        duration = time.time() - start
        logf.write(f"\n[exit={exit_code} duration={duration:.1f}s timeout={timeout}s]\n")

    return {
        "exit_code": exit_code,
        "duration_s": duration,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
    }


def run_repo_optimized(repo: str) -> dict:
    """
    优化的仓级跑测：
    1. 过滤出有 examples 的目标算子
    2. 一次性 build 全部
    3. 一次性 install
    4. 逐个 run_example
    """
    repo_path_str = REPO_PATHS.get(repo)
    if not repo_path_str:
        return {"repo": repo, "status": "REPO_PATH_MISSING",
                "hint": f"调用方未提供 {repo} 的本地路径（--repo-mapping）"}
    repo_path = Path(repo_path_str)
    if not repo_path.exists():
        return {"repo": repo, "status": "REPO_NOT_FOUND", "path": repo_path_str}

    repo_log_dir = REPORT_ROOT / repo / "test" / "logs"
    repo_log_dir.mkdir(parents=True, exist_ok=True)

    templates = discover_command_templates(repo_path)
    missing = [k for k in ("build", "run") if templates[k] is None]
    if missing and not FORCE_DEFAULT_TEMPLATE:
        print(f"[{repo}] ✗ 未能从 {templates['source']} 解析出 {'/'.join(missing)} 命令模板，"
              f"跳过该仓。确认要用默认模板就带 --force-default-template 重跑，"
              f"或者先检查/补齐该仓的 QUICKSTART.md。", flush=True)
        return {"repo": repo, "status": "TEMPLATE_UNRESOLVED", "missing": missing,
                "source": templates["source"]}
    persist_quickstart_record(repo, templates)
    build_template = templates["build"] or _DEFAULT_BUILD_TEMPLATE
    run_template = templates["run"] or _DEFAULT_RUN_TEMPLATE

    target_ops = extract_ops(repo)
    
    # Step 0: 算子分类
    buildable_ops = []   # 有 examples，要参与 build
    skipped_ops = []     # 无 examples 或目录找不到
    
    for op in target_ops:
        op_dir = find_op_dir(repo_path, op)
        if op_dir and has_examples(op_dir):
            buildable_ops.append(op)
        else:
            skipped_ops.append(op)
    
    result = {
        "repo": repo,
        "total": len(target_ops),
        "buildable": len(buildable_ops),
        "skipped_no_artifact": len(skipped_ops),
        "ops_status": {},
        "phase_durations": {},
    }
    
    for op in skipped_ops:
        result["ops_status"][op] = "SKIPPED_NO_ARTIFACT"
    
    if not buildable_ops:
        result["status"] = "NO_BUILDABLE_OPS"
        return result
    
    print(f"[{repo}] 开始：{len(buildable_ops)} 个可 build / {len(skipped_ops)} 个 SKIP", flush=True)
    
    # Step 1: 合并 build（一次性 build 全部目标算子）
    ops_csv = ",".join(buildable_ops)
    build_log = repo_log_dir / "_BATCH.phase1.build.log"
    build_cmd = _compose_build_cmd(soc=SOC, ops_csv=ops_csv, build_extra_args=BUILD_EXTRA_ARGS,
                                    build_template=build_template)
    
    print(f"[{repo}] Building {len(buildable_ops)} ops...", flush=True)
    build_t0 = time.time()
    build_res = run_shell(build_cmd, repo_path, build_log, timeout=3600)
    result["phase_durations"]["build"] = time.time() - build_t0
    
    if build_res["exit_code"] != 0:
        # 全部算子标记 BUILD_FAIL（合并 build，共享同一份 build 日志）
        for op in buildable_ops:
            result["ops_status"][op] = "BUILD_FAIL"
            result.setdefault("ops_log", {})[op] = str(build_log)
        result["status"] = "BUILD_FAIL"
        result["build_log"] = str(build_log)
        print(f"[{repo}] ❌ Build failed (exit={build_res['exit_code']}, {result['phase_durations']['build']:.0f}s)", flush=True)
        return result
    
    print(f"[{repo}] ✓ Build done in {result['phase_durations']['build']:.0f}s", flush=True)
    
    # Step 2: 一次性 install
    # 只认本次 build 产出的 .run（mtime ≥ build 开始），避免拿到上一次构建的旧包
    all_pkgs = sorted((repo_path / "build_out").glob("cann-ops-*linux*.run"))
    fresh_pkgs = [p for p in all_pkgs if p.stat().st_mtime >= build_t0 - 1]
    pkg_candidates = fresh_pkgs or all_pkgs   # 没有更新的就退回全部（容错）
    install_log = repo_log_dir / "_BATCH.phase1.install.log"
    if not pkg_candidates:
        for op in buildable_ops:
            result["ops_status"][op] = "INSTALL_FAIL"
            result.setdefault("ops_log", {})[op] = str(install_log)
        result["status"] = "INSTALL_FAIL_NO_PKG"
        return result

    pkg = pkg_candidates[-1]
    install_cmd = f"{pkg} --quiet"

    print(f"[{repo}] Installing pkg...", flush=True)
    install_t0 = time.time()
    install_res = run_shell(install_cmd, repo_path, install_log, timeout=600)
    result["phase_durations"]["install"] = time.time() - install_t0

    if install_res["exit_code"] != 0:
        # fallback 不带 --quiet；写独立日志，保留 --quiet 那次的失败现场
        install_log = repo_log_dir / "_BATCH.phase1.install.fallback.log"
        install_res = run_shell(str(pkg), repo_path, install_log, timeout=600)
        if install_res["exit_code"] != 0:
            for op in buildable_ops:
                result["ops_status"][op] = "INSTALL_FAIL"
                result.setdefault("ops_log", {})[op] = str(install_log)
            result["status"] = "INSTALL_FAIL"
            return result
    
    print(f"[{repo}] ✓ Install done in {result['phase_durations']['install']:.0f}s", flush=True)
    
    # Step 3: 逐个 run_example（NPU 共享，串行）
    print(f"[{repo}] Running {len(buildable_ops)} examples...", flush=True)
    run_t0 = time.time()
    pass_count = 0
    
    for i, op in enumerate(buildable_ops, 1):
        run_log = repo_log_dir / f"{op}.phase1.run.log"
        run_cmd = _compose_run_cmd(op=op, run_extra_args=RUN_EXTRA_ARGS, run_template=run_template)
        
        run_res = run_shell(run_cmd, repo_path, run_log, timeout=300)
        
        # 单点判定（含 TIMEOUT + T2 空退归 SKIPPED_NO_RUN_ARTIFACT），与 phase_examples 共用
        status, verdict_reason = classify_run_status(
            run_res["stdout"], run_res["stderr"], run_res["exit_code"],
            run_res["duration_s"], run_res["timed_out"],
        )
        if status == "PASS":
            pass_count += 1

        result["ops_status"][op] = status
        result.setdefault("ops_log", {})[op] = str(run_log)
        result.setdefault("ops_run_dur", {})[op] = run_res["duration_s"]
        result.setdefault("ops_verdict_reason", {})[op] = verdict_reason
        symbol = {"PASS": "✅", "UNCERTAIN": "❓", "SKIPPED_NO_RUN_ARTIFACT": "⏭️"}.get(status, "❌")
        print(f"[{repo}] [{i}/{len(buildable_ops)}] {symbol} {op}: {status}", flush=True)
    
    result["phase_durations"]["run"] = time.time() - run_t0
    result["pass_count"] = pass_count
    result["status"] = "DONE"
    
    return result


def sync_to_state_json(repo_results):
    """把 batched 跑测结果同步写入 run_state.json（主进程统一写，无并发冲突）。

    见 SKILL.md 「报告与续跑」节：诊断模式与下次续跑都依赖此文件。
    """
    # 延迟导入，避免 ProcessPool worker 进程也加载 state.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from state import init_repo, update_op  # noqa: E402

    for r in repo_results:
        repo = r.get("repo")
        if not repo:
            continue
        ops_status = r.get("ops_status", {})
        if not ops_status:
            continue
        # 先初始化（保持 PENDING 占位）
        init_repo(repo, list(ops_status.keys()))
        # 再逐个写状态
        durations = r.get("phase_durations", {})
        # 合并 build 没有 per-op build 时长，build/install 阶段按算子数平摊；
        # run 阶段有真实 per-op 时长（ops_run_dur），优先用它。
        share = (durations.get("build", 0) + durations.get("install", 0)) / max(1, len(ops_status))
        verdict_reasons = r.get("ops_verdict_reason", {})
        ops_log = r.get("ops_log", {})
        ops_run_dur = r.get("ops_run_dur", {})
        for op, status in ops_status.items():
            extra = {"mode": "batched"}
            if status == "BUILD_FAIL":
                extra["step"] = "build"
            if op in verdict_reasons:
                extra["verdict_reason"] = verdict_reasons[op]
            if status == "UNCERTAIN":
                extra["note"] = "exit==0 but no strong pass/fail signal — agent review needed"
            # 跑到 run 阶段的算子用真实 run 时长；build/install 失败的用平摊值
            duration = ops_run_dur[op] if op in ops_run_dur else share
            update_op(repo, op, "phase1", status,
                      duration_s=duration,
                      log_path=ops_log.get(op),
                      extra=extra)


def generate_report(repo_results, total_time):
    """生成最终报告"""
    sync_to_state_json(repo_results)

    print(f"\n\n{'='*80}")
    print(f"📊 Phase 1 跑测最终报告（合并 build + 仓间并发）")
    print(f"{'='*80}\n")
    
    grand_total_pass = 0
    grand_total_ops = 0
    grand_status_counts = {}
    
    full_report = {
        "phase": 1,
        "mode": "batched_build_with_repo_concurrency",
        "timestamp": datetime.now().isoformat(),
        "total_duration_seconds": total_time,
        "repos": {},
    }
    
    for r in repo_results:
        repo = r["repo"]
        total = r.get("total", 0)
        pass_count = r.get("pass_count", 0)
        durations = r.get("phase_durations", {})
        
        grand_total_pass += pass_count
        grand_total_ops += total
        
        for op, status in r.get("ops_status", {}).items():
            grand_status_counts[status] = grand_status_counts.get(status, 0) + 1
        
        # 仓内状态分布
        repo_status_counts = {}
        for status in r.get("ops_status", {}).values():
            repo_status_counts[status] = repo_status_counts.get(status, 0) + 1
        
        pct = 100 * pass_count // total if total > 0 else 0
        build_t = durations.get("build", 0)
        install_t = durations.get("install", 0)
        run_t = durations.get("run", 0)
        
        print(f"┌─ {repo} ({total} 个算子)")
        print(f"│  ⏱️  build={build_t:.0f}s  install={install_t:.0f}s  run={run_t:.0f}s")
        print(f"│  ✅ PASS: {pass_count}/{total} ({pct}%)")
        for status, count in sorted(repo_status_counts.items(), key=lambda x: -x[1]):
            if status != "PASS":
                symbol = "⏱️" if status == "TIMEOUT" else "❌"
                print(f"│  {symbol} {status}: {count}")
        print(f"└─\n")
        
        full_report["repos"][repo] = {
            "total": total,
            "passed": pass_count,
            "phase_durations": durations,
            "status_counts": repo_status_counts,
            "ops_status": r.get("ops_status", {}),
            "build_log": r.get("build_log"),
        }
    
    print(f"{'─'*80}")
    pass_pct = 100 * grand_total_pass // grand_total_ops if grand_total_ops > 0 else 0
    print(f"  TOTAL: {grand_total_pass}/{grand_total_ops} PASS ({pass_pct}%)")
    print(f"{'─'*80}\n")
    
    print(f"📋 状态分布:")
    for status, count in sorted(grand_status_counts.items(), key=lambda x: -x[1]):
        pct = 100 * count // grand_total_ops if grand_total_ops > 0 else 0
        print(f"   {status:25s} {count:3d} ({pct}%)")
    
    full_report["total_operators"] = grand_total_ops
    full_report["total_passed"] = grand_total_pass
    full_report["pass_rate"] = pass_pct
    full_report["status_distribution"] = grand_status_counts
    
    report_file = REPORT_ROOT / "phase1_report_final.json"
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    with open(report_file, 'w') as f:
        json.dump(full_report, f, indent=2, ensure_ascii=False)

    from state import write_summary_md
    summary_file = write_summary_md(phase="phase1", soc=SOC)

    print(f"\n📄 详细报告: {report_file}")
    print(f"📄 摘要(给人看): {summary_file}")
    print(f"⏱️  总耗时: {total_time/60:.1f} 分钟")
    
    return full_report


def main():
    ap = _build_parser()
    args = ap.parse_args()

    global SOC, CLI_OPS, CLI_OPS_FILE, BUILD_EXTRA_ARGS, RUN_EXTRA_ARGS, ENV_EXTRA, JOBS, FORCE_DEFAULT_TEMPLATE
    FORCE_DEFAULT_TEMPLATE = args.force_default_template
    SOC = args.soc
    if not SOC:  # T4：--soc 未给 → 自动探测 acl.get_soc_name() 并映射，探不到才报错让 skill 问用户
        _det = detect_soc(SET_ENV_SH)
        SOC = _det.get("build_soc") or ""
        if SOC:
            print(f"   SOC 自动探测：{_det.get('raw')} → {SOC}")
        else:
            ap.error("--soc 未给且自动探测失败（acl 不可用 / 无 NPU 权限 / 无 CANN）；请显式传 --soc")
    CLI_OPS = args.ops
    CLI_OPS_FILE = args.ops_file
    BUILD_EXTRA_ARGS = args.build_extra_args
    RUN_EXTRA_ARGS = args.run_extra_args
    ENV_EXTRA = _parse_env_extra(args.env_extra)
    JOBS = args.jobs if args.jobs > 0 else _available_cores()

    REPO_PATHS.update(parse_repo_mapping(args.repo_mapping))
    target_repos = list(REPO_PATHS.keys())
    if not target_repos:
        ap.error("--repo-mapping 至少要包含一项")

    print(f"📋 启动 Phase 1 跑测")
    if len(target_repos) == 1:
        print(f"   策略：单仓模式（{target_repos[0]}） + 仓内合并 build")
    else:
        print(f"   策略：仓间并发({len(target_repos)}) + 仓内合并 build")
    print(f"   SOC: {SOC}")
    print(f"   build -j: {JOBS}（全核自适应；--jobs 可覆盖）")
    print(f"   set_env.sh: {SET_ENV_SH}")
    print()

    from state import init_repo  # noqa: E402
    for repo in target_repos:
        try:
            ops = extract_ops(repo)
        except OpsResolutionError as e:
            print(f"   {repo:20s}  ✗ {e}", flush=True)
            return 2
        # 跑前先把全部目标算子播种为 PENDING（init_repo 幂等、保留旧状态）。worker 崩了（EXEC_ERROR
        # 整仓无 ops_status，sync 会跳过）时，算子仍停在 PENDING → postrun 闸门据此判 ACTION_REQUIRED，
        # 不会把崩掉的仓当「完成」漏掉。
        init_repo(repo, ops)
        print(f"   {repo:20s}  {len(ops):2d} 个目标算子（path={REPO_PATHS[repo]}）")
    print()

    total_start = time.time()

    repo_results = []
    if len(target_repos) == 1:
        # 场景 B：单仓直接跑（不进 ProcessPool，便于实时日志）
        try:
            result = run_repo_optimized(target_repos[0])
            repo_results.append(result)
        except Exception as e:
            repo_results.append({"repo": target_repos[0], "status": "EXEC_ERROR", "error": str(e)})
    else:
        # 场景 A：仓间并发 4 worker（显式把配置注入每个 worker，spawn 平台也安全）
        with ProcessPoolExecutor(max_workers=len(target_repos),
                                 initializer=_init_worker,
                                 initargs=(_worker_config(),)) as executor:
            future_to_repo = {executor.submit(run_repo_optimized, repo): repo for repo in target_repos}
            for future in as_completed(future_to_repo):
                try:
                    result = future.result()
                    repo_results.append(result)
                except Exception as e:
                    repo = future_to_repo[future]
                    print(f"💥 {repo}: {e}", flush=True)
                    repo_results.append({"repo": repo, "status": "EXEC_ERROR", "error": str(e)})

    total_time = time.time() - total_start
    generate_report(repo_results, total_time)

    unresolved = [r for r in repo_results if r.get("status") == "TEMPLATE_UNRESOLVED"]

    # T1 防绕过闸门：扫 run_state 出待办队列；有失败/待复核则整轮 ACTION_REQUIRED + 退出码 3
    from postrun import postrun_gate
    completion, actions_path, code = postrun_gate(phase="phase1")
    print(f"\n🚦 整轮状态：{completion}  (postrun_actions: {actions_path})", flush=True)
    if code != 0:
        print("   ⚠ ACTION_REQUIRED：有失败/待复核算子待 agent 处理（FAQ→续跑→P6 / UNCERTAIN 复核）；"
              "直跑 runner 不算收尾。退出码 3（≠ 退出码 2 的 usage/解析失败）。", flush=True)

    if unresolved:
        print(f"\n⛔ {len(unresolved)} 个仓未跑：命令模板解析不出来，等用户确认要不要用默认模板：",
              flush=True)
        for r in unresolved:
            print(f"   {r['repo']:20s}  缺 {'/'.join(r['missing'])}  ← {r['source']}", flush=True)
        print("   确认用默认模板就带 --force-default-template 重跑这些仓；"
              "不确认就先检查/补齐对应仓的 QUICKSTART.md。", flush=True)
        return 4  # 优先于退出码 3：这是需要用户明确决策的问题，不是"失败/待复核算子"能自动处理的

    return code


if __name__ == '__main__':
    sys.exit(main())
