"""通用工具：subprocess 包装、日志落盘、PASS 判定、目标算子来源解析。"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 运行产物按仓写到 CWD/cann-ops-report/<repo>/test/（与 state.py 保持一致）
REPORT_ROOT = Path.cwd() / "cann-ops-report"

# CANN 环境激活脚本：CANN toolkit 安装后会自动设置 ASCEND_HOME_PATH，
# 从中推导 set_env.sh 路径；找不到时 fallback 到标准安装默认路径。
import os as _os
def _find_set_env_sh() -> str:
    ascend_home = _os.environ.get("ASCEND_HOME_PATH", "")
    if ascend_home:
        # 兼容两种官方安装布局：
        #   直装:    ASCEND_HOME_PATH=/xxx/Ascend/cann-X.Y.Z       → set_env.sh 在其下
        #   toolkit: ASCEND_HOME_PATH=/xxx/ascend-toolkit/latest/<arch> → set_env.sh 在上两级
        for candidate in (Path(ascend_home) / "set_env.sh",
                          Path(ascend_home).parent.parent / "set_env.sh"):
            if candidate.exists():
                return str(candidate)
    return str(Path.home() / "Ascend/ascend-toolkit/latest/set_env.sh")

CANN_SET_ENV_SH = _find_set_env_sh()

# SOC 不再硬编码。每个 runner 通过 --soc CLI 参数显式接收，由 skill 询问用户得到。

# 四层日志判定（见 SKILL.md「跑测结果判定」节）：
#   L0  exit_code != 0          → FAIL
#   L1  STRONG_FAIL 命中         → FAIL
#   L2  SUCCESS 命中             → PASS
#   L3  以上均不命中             → UNCERTAIN（由 agent 后续判定）
#
# 只有"出现这条就 100% 是该状态"的模式才能进强信号集合。
# 弱词（ERROR / error / failed / success）一律放 L3，避免 stderr warning 误判。

SUCCESS_PATTERNS = [
    re.compile(r"result\[\d+\]\s+is:"),                              # examples 数据输出
    re.compile(r"All tests passed"),
    re.compile(r"Test PASSED"),
    re.compile(r"\bpassed\b", re.IGNORECASE),                        # pytest 风格
    re.compile(r"PASS\b"),
    re.compile(r"execute samples? success", re.IGNORECASE),          # ops-transformer 风格
    re.compile(r"Example completed successfully", re.IGNORECASE),    # ops-transformer 风格
    re.compile(r"run .{1,80} success(?:fully)?\b", re.IGNORECASE),   # 通用 "run X success"
]

STRONG_FAIL_PATTERNS = [
    re.compile(r"Segmentation fault"),
    re.compile(r"core dumped"),
    re.compile(r"terminate called"),
    re.compile(r"std::terminate"),
    re.compile(r"Assertion .+ failed", re.IGNORECASE),
    re.compile(r"\bACL ERROR\b"),
    re.compile(r"\bEE\d{4}\b"),                                       # Ascend error code 如 EE9999
    re.compile(r"Kernel launch failed", re.IGNORECASE),
    re.compile(r"aclrt\w+ failed"),
    re.compile(r"Check failed:"),
]


def classify_log(stdout: str, stderr: str, exit_code: int) -> tuple[str, str]:
    """四层日志判定。

    返回 (verdict, reason)，verdict ∈ {"PASS", "FAIL", "UNCERTAIN"}。
    """
    if exit_code != 0:
        return "FAIL", f"exit_code={exit_code}"

    combined = stdout + "\n" + stderr
    for p in STRONG_FAIL_PATTERNS:
        m = p.search(combined)
        if m:
            return "FAIL", f"strong_fail:{m.group(0)!r}"

    for p in SUCCESS_PATTERNS:
        if p.search(stdout):
            return "PASS", "strong_pass_pattern"

    return "UNCERTAIN", "no_strong_signal"


def is_empty_run(stdout: str, stderr: str, duration_s: float, fast_threshold: float = 2.0) -> bool:
    """run 步 exit0 但**实际没真跑**（有效输出为空 + 极快）。

    用于 T2：把「空退没真跑」（如缺 eager 示例，`build.sh --run_example op eager` 啥也没干就退）
    从 UNCERTAIN 里分出来归 `SKIPPED_NO_RUN_ARTIFACT`，不占「待复核」。
    主信号是**有效输出为空**（去掉空白行后 0 行）；`<fast_threshold` 仅作辅助，避免误伤合法但慢的空输出。
    """
    eff = [l for l in (stdout + "\n" + stderr).splitlines() if l.strip()]
    return not eff and duration_s < fast_threshold


def classify_run_status(stdout: str, stderr: str, exit_code: int,
                        duration_s: float, timed_out: bool = False) -> tuple[str, str]:
    """run 步的**最终算子状态**：在 4 层 `classify_log` 之上加 TIMEOUT 与 T2 空退归类。

    单点决策，phase_examples 与 batched runner 共用，避免两处判定漂移。
    返回 (status, reason)，status ∈
      {PASS, RUN_EXIT_FAIL, RUN_PATTERN_FAIL, TIMEOUT, SKIPPED_NO_RUN_ARTIFACT, UNCERTAIN}。
    """
    if timed_out:
        return "TIMEOUT", "timed_out"
    verdict, reason = classify_log(stdout, stderr, exit_code)
    if verdict == "PASS":
        return "PASS", reason
    if verdict == "FAIL":
        return ("RUN_EXIT_FAIL" if exit_code != 0 else "RUN_PATTERN_FAIL"), reason
    # verdict == UNCERTAIN —— T2：空退没真跑 → SKIPPED_NO_RUN_ARTIFACT（不占待复核）
    if is_empty_run(stdout, stderr, duration_s):
        return "SKIPPED_NO_RUN_ARTIFACT", "exit0_empty_fast_no_run"
    return "UNCERTAIN", reason


def soc_name_to_build_soc(soc_name: str | None) -> str | None:
    """`acl.get_soc_name()` 原始名 → build.sh 短 soc 串（T4，与 cann-env-setup 同一映射）。

    Ascend910_9382 → ascend910_93;Ascend910B3/ProB → ascend910b;Ascend910A → ascend910;
    Ascend950* → ascend950;Ascend310P* → ascend310p;Ascend310B* → ascend310b。其它原样小写兜底。
    """
    if not soc_name:
        return None
    low = soc_name.strip().lower()
    m = re.match(r"ascend910_(\d{2})\d*", low)        # _93xx / _55xx
    if m:
        return f"ascend910_{m.group(1)}"
    if re.match(r"ascend910(pro)?b", low):
        return "ascend910b"
    if low.startswith("ascend910"):
        return "ascend910"
    if low.startswith("ascend950"):
        return "ascend950"
    if low.startswith("ascend310p"):
        return "ascend310p"
    if low.startswith("ascend310b"):
        return "ascend310b"
    return low


def detect_soc(set_env: str | None = None) -> dict:
    """用 CANN 运行时 `acl.get_soc_name()` 自动探测 soc 并映射到 build.sh 短串（T4）。

    set_env 默认用 CANN_SET_ENV_SH；acl 不可用/无 NPU 权限/无 CANN 时静默返回 None。
    返回 {raw, build_soc}。CANN 运行时把 [Warning] 打到 stdout，故用 marker 包 soc 名再 grep。
    """
    none = {"raw": None, "build_soc": None}
    # 整体兜底：探测属于「best-effort」，任何异常（路径/解码/子进程/映射）都静默归 None，绝不抛。
    try:
        se = set_env or CANN_SET_ENV_SH
        if not se or not Path(se).is_file():
            return dict(none)
        pycode = "import acl;acl.init();n=acl.get_soc_name();print('__SOC__'+str(n));acl.finalize()"
        res = subprocess.run(
            ["bash", "-lc", f'source "{se}" >/dev/null 2>&1; python3 -c "{pycode}"'],
            capture_output=True, text=True, timeout=60,
        )
        raw = None
        for line in res.stdout.splitlines():
            if line.startswith("__SOC__"):
                raw = line[len("__SOC__"):].strip()
                break
        if raw and not raw.lower().startswith("ascend"):
            raw = None
        return {"raw": raw, "build_soc": soc_name_to_build_soc(raw)}
    except Exception:
        return dict(none)


@dataclass
class CmdResult:
    cmd: str
    cwd: str
    exit_code: int
    duration_s: float
    stdout: str
    stderr: str
    log_path: Optional[Path] = None
    timed_out: bool = False

    def classify(self) -> tuple[str, str]:
        """四层判定，返回 (verdict, reason)。"""
        return classify_log(self.stdout, self.stderr, self.exit_code)

    def stdout_matches_success(self) -> bool:
        """[deprecated] 保留向后兼容。新代码请用 classify()。"""
        return any(p.search(self.stdout) for p in SUCCESS_PATTERNS)

    def passed(self) -> bool:
        """[deprecated] 保留向后兼容。新代码请用 classify()。"""
        return self.exit_code == 0 and self.stdout_matches_success()


def ensure_log_path(repo: str, op: str, phase: str) -> Path:
    repo_logs = REPORT_ROOT / repo / "test" / "logs"
    repo_logs.mkdir(parents=True, exist_ok=True)
    return repo_logs / f"{op}.{phase}.log"


def persist_quickstart_record(repo: str, templates: dict) -> Path:
    """把 quickstart_probe.discover_command_templates() 的解析结果落盘存档。

    纯审计用途：记录"这次跑测实际用的是什么命令模板、从哪个文档解析出来的"，
    不作为下次跑测的输入——不读它、只写它，避免变成一份会过期的隐性缓存。
    """
    path = REPORT_ROOT / repo / "test" / "quickstart_derived_cmds.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(templates, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_cmd(
    cmd: str,
    cwd: str | Path,
    timeout: int,
    log_path: Optional[Path] = None,
    env: Optional[dict] = None,
) -> CmdResult:
    """跑一条 shell 命令，全量捕获 stdout+stderr 并落盘。

    自动在 cmd 前 source CANN_SET_ENV_SH，确保 ASCEND_HOME_PATH 等环境变量
    在 subprocess 中可用（见 SKILL.md 「前置环境节点 P0」）。
    """
    start = time.time()
    timed_out = False
    wrapped_cmd = f"source {CANN_SET_ENV_SH} && {cmd}"
    try:
        proc = subprocess.run(
            wrapped_cmd,
            shell=True,
            executable="/bin/bash",
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        exit_code = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as e:
        exit_code = 124  # 约定超时退出码
        stdout = (e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")) or ""
        stderr = (e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")) or ""
        stderr += f"\n[TIMEOUT after {timeout}s]\n"
        timed_out = True

    duration = time.time() - start

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"$ cd {cwd}\n$ {cmd}\n[exit={exit_code} duration={duration:.1f}s timeout={timeout}s]\n"
            f"\n--- STDOUT ---\n{stdout}\n--- STDERR ---\n{stderr}\n",
            encoding="utf-8",
            errors="replace",
        )

    return CmdResult(
        cmd=cmd,
        cwd=str(cwd),
        exit_code=exit_code,
        duration_s=duration,
        stdout=stdout,
        stderr=stderr,
        log_path=log_path,
        timed_out=timed_out,
    )


def find_run_pkg(repo_path: Path) -> Optional[Path]:
    """找到 build_out 下的 cann-ops-*-linux*.run。"""
    candidates = sorted((repo_path / "build_out").glob("cann-ops-*linux*.run"))
    return candidates[-1] if candidates else None


def vendor_name_for(repo: str) -> str:
    """从仓名派生 vendor name。

    约定：`ops-X` → `custom_X`（剥掉 `ops-` 前缀加 `custom_`）。
    非 `ops-` 前缀仓名兜底为 `custom_<repo>`。
    """
    name = repo[4:] if repo.startswith("ops-") else repo
    return f"custom_{name}"


def append_ld_library_path(env: dict, repo: str, ascend_home: str) -> dict:
    """将该仓的 vendor lib 路径前置到 LD_LIBRARY_PATH（QUICKSTART §4）。"""
    vendor = vendor_name_for(repo)
    seg = f"{ascend_home}/opp/vendors/{vendor}/op_api/lib"
    cur = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = f"{seg}:{cur}" if cur else seg
    return env


class OpsResolutionError(RuntimeError):
    """目标算子来源解析失败（多源未命中或文件格式不对）。"""


def resolve_ops(
    repo: str,
    cli_ops: Optional[str] = None,
    cli_ops_file: Optional[str] = None,
    scan_root: Optional[Path] = None,
) -> list[str]:
    """按优先级解析目标算子清单。

    优先级（高→低）：
      1. ``cli_ops``：CSV 字符串，例如 ``op1,op2,op3``。skill 显式传入时使用。
      2. ``cli_ops_file``：路径，可以是 .json（含 ``unique_targets`` 列表 / 顶层 list）
         或纯文本（一行一个算子，``#`` 开头为注释，空行忽略）。
      3. ``cann-ops-report/<repo>/scan/_intermediate.json`` 的 ``unique_targets`` 字段
         （由 ``cann-950-feature-scan`` 生成；仅当用户在 P0.5 明确选择 A5/950 专项测试时，
         skill 才会不传 1/2 走到这条 fallback，不是无条件默认入口。scan_root 可覆盖根目录）。

    全部未命中 → 抛 ``OpsResolutionError``，要求 skill 与用户交互后重试。

    返回：去重后保持原始顺序的算子名列表。
    """
    if cli_ops:
        return _dedupe([s.strip() for s in cli_ops.split(",") if s.strip()])

    if cli_ops_file:
        return _read_ops_file(Path(cli_ops_file))

    root = scan_root or REPORT_ROOT
    intermediate = root / repo / "scan" / "_intermediate.json"
    if intermediate.exists():
        try:
            data = json.loads(intermediate.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise OpsResolutionError(f"{intermediate} 不是合法 JSON: {e}")
        ops = data.get("unique_targets")
        if not ops:
            raise OpsResolutionError(
                f"{intermediate} 缺少 unique_targets 字段或为空，"
                f"请重新运行 cann-950-feature-scan 扫描 {repo}"
            )
        return _dedupe(list(ops))

    raise OpsResolutionError(
        f"未指定目标算子来源：未传 --ops/--ops-file，且 {intermediate} 不存在。"
        f"请先用 cann-950-feature-scan 扫描，或显式提供 --ops 算子清单。"
    )


def _read_ops_file(path: Path) -> list[str]:
    if not path.exists():
        raise OpsResolutionError(f"--ops-file 指向的文件不存在: {path}")
    text = path.read_text(encoding="utf-8").strip()
    # 尝试 JSON：支持 {"unique_targets": [...]} 或顶层 list
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "unique_targets" in data:
            return _dedupe(list(data["unique_targets"]))
        if isinstance(data, list):
            return _dedupe([str(x) for x in data])
    except json.JSONDecodeError:
        pass
    # 纯文本：每行一个算子，# 注释，空行忽略
    ops: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        ops.append(s)
    if not ops:
        raise OpsResolutionError(f"--ops-file {path} 解析后为空")
    return _dedupe(ops)


def _dedupe(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def parse_repo_mapping(s: str) -> dict:
    """解析 --repo-mapping 参数：repo1=path1,repo2=path2 → {repo: path}。"""
    out: dict[str, str] = {}
    for entry in s.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError(f"--repo-mapping 项 {entry!r} 缺少 '='")
        name, path = entry.split("=", 1)
        out[name.strip()] = path.strip()
    return out
