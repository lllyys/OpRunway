"""run_state.json 原子读写与状态机。

状态枚举：
  PENDING / RUNNING / PASS / BUILD_FAIL / INSTALL_FAIL /
  RUN_EXIT_FAIL / RUN_PATTERN_FAIL / UNCERTAIN /
  TIMEOUT / SKIPPED_NO_ARTIFACT / SKIPPED_NO_RUN_ARTIFACT / SKIPPED_USER

UNCERTAIN：四层日志判定 L3 兜底状态——exit==0 但既无强成功也无强失败信号。
  跑测中不阻塞，标记后跑完由 agent 集中判定，最终落到 PASS 或 RUN_PATTERN_FAIL。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# 所有运行产物写到用户当前工作目录的 cann-ops-report/<repo>/test/ 子目录（每仓独立）。
# skill 安装目录（__file__ 所在位置）不写任何输出文件。
REPORT_ROOT = Path.cwd() / "cann-ops-report"


def repo_test_dir(repo: str) -> Path:
    return REPORT_ROOT / repo / "test"


def _state_file(repo: str) -> Path:
    return repo_test_dir(repo) / "run_state.json"

VALID_STATUSES = {
    "PENDING", "RUNNING", "PASS",
    "BUILD_FAIL", "INSTALL_FAIL",
    "RUN_EXIT_FAIL", "RUN_PATTERN_FAIL", "UNCERTAIN",
    "TIMEOUT",
    "SKIPPED_NO_ARTIFACT",      # 源码层：无 examples/test_*.cpp，根本没 build
    "SKIPPED_NO_RUN_ARTIFACT",  # 运行层（T2）：build/install 过了但 run 空退没真跑（如缺 eager 示例）
    "SKIPPED_USER",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_repo(repo: str) -> dict:
    """读单仓状态：{"created_at", "updated_at", "ops": {...}}。"""
    f = _state_file(repo)
    if not f.exists():
        return {"created_at": _now_iso(), "updated_at": _now_iso(), "ops": {}}
    return json.loads(f.read_text(encoding="utf-8"))


def load() -> dict:
    """聚合视图（兼容旧 API）：扫描 cann-ops-report/*/test/run_state.json。"""
    repos = {}
    if REPORT_ROOT.is_dir():
        for d in sorted(REPORT_ROOT.iterdir()):
            if (d / "test" / "run_state.json").exists():
                repos[d.name] = {"ops": load_repo(d.name)["ops"]}
    return {"updated_at": _now_iso(), "repos": repos}


def _atomic_write(repo: str, data: dict) -> None:
    """原子写单仓状态：先写临时文件再 rename。"""
    f = _state_file(repo)
    f.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".run_state.", dir=str(f.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, f)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def init_repo(repo: str, ops: list[str]) -> None:
    """首次为某仓初始化状态，已存在的算子保留旧状态。"""
    repo_state = load_repo(repo)
    for op in ops:
        if op not in repo_state["ops"]:
            repo_state["ops"][op] = {
                "phase1": {"status": "PENDING", "attempts": 0},
                "phase2": {"status": "PENDING", "attempts": 0},
                "phase3": {"status": "PENDING", "attempts": 0},
                "phase4": {"status": "PENDING", "attempts": 0},
            }
    repo_state["updated_at"] = _now_iso()
    _atomic_write(repo, repo_state)


def update_op(
    repo: str,
    op: str,
    phase: str,
    status: str,
    duration_s: float | None = None,
    log_path: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """原子更新某算子某 phase 的状态。"""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    if phase not in {"phase1", "phase2", "phase3", "phase4"}:
        raise ValueError(f"invalid phase: {phase}")

    repo_state = load_repo(repo)
    op_state = repo_state["ops"].setdefault(op, {})
    phase_state = op_state.setdefault(phase, {"status": "PENDING", "attempts": 0})

    phase_state["status"] = status
    phase_state["attempts"] = phase_state.get("attempts", 0) + 1
    phase_state["last_update"] = _now_iso()
    if duration_s is not None:
        phase_state["duration_s"] = round(duration_s, 1)
    if log_path is not None:
        phase_state["log_path"] = log_path
    if extra:
        phase_state.update(extra)

    repo_state["updated_at"] = _now_iso()
    _atomic_write(repo, repo_state)


def get_op(repo: str, op: str) -> dict:
    return load_repo(repo)["ops"].get(op, {})


def repo_summary(repo: str, phase: str) -> dict[str, int]:
    """返回某仓某 phase 各状态计数。"""
    counts: dict[str, int] = {}
    ops = load_repo(repo)["ops"]
    for op_state in ops.values():
        s = op_state.get(phase, {}).get("status", "PENDING")
        counts[s] = counts.get(s, 0) + 1
    return counts


# 失败类状态（写 SUMMARY.md 时归入"失败明细"）。postrun.py 复用同一集合，避免两处漂移。
FAIL_STATUSES = {"BUILD_FAIL", "INSTALL_FAIL", "RUN_EXIT_FAIL", "RUN_PATTERN_FAIL", "TIMEOUT"}
_FAIL_STATUSES = FAIL_STATUSES  # 旧内部名别名（同一对象），保留兼容


def write_summary_md(phase: str = "phase1", soc: str = "") -> Path:
    """把 run_state 渲染成给人看的简洁 SUMMARY.md（覆盖写，多仓各一行）。"""
    data = load()
    lines = [
        "# 跑测摘要（示例跑测）",
        "",
        f"> 更新：{_now_iso()}" + (f" · SOC: {soc}" if soc else ""),
        "",
        "示例跑测 = 编译算子包 → 安装 → NPU 真机跑各算子的示例程序。每仓一行：",
        "**通过** 真机示例成功；**失败** 编译/安装/运行任一步失败；**跳过** 无示例可跑；",
        "**待复核** 退出码 0 但无明确成败信号；**探索(解/总)** 失败算子中已探明可行方案的个数。",
        "",
        "| 仓 | 通过 | 失败 | 跳过 | 待复核 | 探索(解/总) |",
        "|---|---|---|---|---|---|",
    ]
    fail_rows = []
    explore_stats = _exploration_stats()
    for repo in sorted(data["repos"]):
        ops = data["repos"][repo]["ops"]
        cnt: dict[str, int] = {}
        for op, st in sorted(ops.items()):
            s = st.get(phase, {}).get("status", "PENDING")
            cnt[s] = cnt.get(s, 0) + 1
            if s in _FAIL_STATUSES:
                fail_rows.append(f"| {_md_cell(repo)} | {_md_cell(op)} | {s} | {_md_cell(st[phase].get('log_path', '—'))} |")
        total = len(ops)
        n_fail = sum(cnt.get(s, 0) for s in _FAIL_STATUSES)
        n_skip = (cnt.get("SKIPPED_NO_ARTIFACT", 0) + cnt.get("SKIPPED_NO_RUN_ARTIFACT", 0)
                  + cnt.get("SKIPPED_USER", 0))
        solved, explored = explore_stats.get(repo, (0, 0))
        exp_cell = f"{solved}/{explored}" if explored else "—"
        lines.append(f"| {repo} | {cnt.get('PASS', 0)}/{total} | {n_fail} | {n_skip} | {cnt.get('UNCERTAIN', 0)} | {exp_cell} |")

    if fail_rows:
        lines += [
            "", "## 失败明细", "",
            "类型含义：BUILD/INSTALL_FAIL 编译/安装失败；RUN_EXIT_FAIL 示例运行退出码非 0；"
            "RUN_PATTERN_FAIL 命中段错误等强失败信号；TIMEOUT 超时。",
            "", "| 仓 | 算子 | 类型 | 日志 |", "|---|---|---|---|",
        ] + fail_rows

    explore_rows = _collect_exploration_rows()
    if explore_rows:
        lines += [
            "", "## 探索结果（P6）", "",
            "失败复现确认后 agent 自主探索的结论。SOLVED=找到经复测验证的可行方案（多为绕开 vendor 路径的对照跑法，"
            "证明问题在仓侧实现而非环境）；UNSOLVED=未找到方案但根因已定位。均可作社区 issue 材料。",
            "", "| 仓 | 算子 | 结论 | 方案 / 根因 |", "|---|---|---|---|",
        ] + explore_rows
    lines.append("")

    out = REPORT_ROOT / "SUMMARY.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _exploration_dirs() -> list[Path]:
    """各仓的 explorations 目录（cann-ops-report/<repo>/test/explorations）。"""
    if not REPORT_ROOT.is_dir():
        return []
    return sorted(d / "test" / "explorations" for d in REPORT_ROOT.iterdir()
                  if (d / "test" / "explorations").is_dir())


def _md_cell(s: str) -> str:
    """转义 markdown 表格单元格（| 和换行会破表）。"""
    return str(s).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _verdict_of(f: Path) -> Optional[str]:
    """读探索文件首行判 verdict：'SOLVED' / 'UNSOLVED' / None（空文件/读失败/畸形）。"""
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    head = lines[0] if lines else ""
    if "UNSOLVED" in head:
        return "UNSOLVED"
    if "SOLVED" in head:    # 必须显式 SOLVED，畸形/空首行不算解决
        return "SOLVED"
    return None


def _explore_files(expl_dir: Path):
    """yield 该目录下非下划线开头的 *.md（探索单算子文件）。"""
    for f in sorted(expl_dir.glob("*.md")):
        if not f.name.startswith("_"):
            yield f


def _exploration_stats() -> dict[str, tuple[int, int]]:
    """repo → (SOLVED 数, 已探索数)。无产物时为空 dict；畸形文件不计入。"""
    stats: dict[str, tuple[int, int]] = {}
    for expl_dir in _exploration_dirs():
        repo = expl_dir.parent.parent.name
        solved = explored = 0
        for f in _explore_files(expl_dir):
            v = _verdict_of(f)
            if v is None:
                continue
            explored += 1
            if v == "SOLVED":
                solved += 1
        stats[repo] = (solved, explored)
    return stats


def _collect_exploration_rows() -> list[str]:
    """从 <repo>/test/explorations/<op>.md 抽取 P6 结论行（无产物/畸形返回空/跳过）。"""
    rows = []
    for expl_dir in _exploration_dirs():
        repo = expl_dir.parent.parent.name
        for f in _explore_files(expl_dir):
            verdict = _verdict_of(f)
            if verdict is None:
                continue
            hint = ""
            for l in f.read_text(encoding="utf-8").splitlines():
                stripped = l.lstrip("- ").strip()
                if stripped.startswith(("方案", "修复在仓")):
                    hint = stripped.split(":", 1)[-1].split("：", 1)[-1].strip()
                    break
            rows.append(f"| {_md_cell(repo)} | {_md_cell(f.stem)} | {verdict} | {_md_cell(hint[:70])} |")
    return rows
