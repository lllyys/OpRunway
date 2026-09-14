"""Render markdown issue drafts in 3 granularities.

Templates live in ../templates/ and use <<<TOKEN>>> placeholders.
cann-ops-run failures always use bug_report.md; the agent uses other templates
for documentation / requirement / question issues as needed.

Output paths:
    CWD/cann-ops-report/issues/drafts/<repo>/per_op/<op>__<type>.md
    CWD/cann-ops-report/issues/drafts/<repo>/by_type/<type>.md
    CWD/cann-ops-report/issues/drafts/<repo>/whole_repo.md
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import log_extract, paths
from .failures import FailureRecord

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Human-readable failure type names (used in titles)
_FAILURE_NAMES = {
    "BUILD_FAIL": "构建失败",
    "INSTALL_FAIL": "安装失败",
    "RUN_EXIT_FAIL": "运行异常退出",
    "RUN_PATTERN_FAIL": "运行结果异常",
    "TIMEOUT": "运行超时",
}

# One-line description per failure type (填入 ## 问题描述)
_FAILURE_DESCS = {
    "BUILD_FAIL": (
        "在 `{soc}` 上编译 `{op}` 时失败，`build.sh` 以非零退出码结束。"
    ),
    "INSTALL_FAIL": (
        "在 `{soc}` 上编译 `{op}` 成功，但安装包（`.run`）执行时失败。"
    ),
    "RUN_EXIT_FAIL": (
        "在 `{soc}` 上运行 `{op}` example 时进程以非零退出码结束，说明运行时遇到错误。"
        "构建和安装已通过，问题出现在执行阶段。"
    ),
    "RUN_PATTERN_FAIL": (
        "在 `{soc}` 上运行 `{op}` example 时进程正常退出（exit=0），"
        "但输出未命中预期的成功标志（`execute samples success` / `Example completed successfully`），"
        "存在静默失败或输出格式变更的风险。"
    ),
    "TIMEOUT": (
        "在 `{soc}` 上运行 `{op}` example 超时（>600s），进程被强制终止。"
    ),
}


def _load_template(name: str) -> str:
    """Read a template file from ../templates/<name>.md."""
    path = _TEMPLATES_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def _render(template: str, **tokens: str) -> str:
    """Replace <<<TOKEN>>> placeholders in template. Unknown tokens left as-is."""
    result = template
    for key, value in tokens.items():
        result = result.replace(f"<<<{key.upper()}>>>", value)
    return result


def read_draft_title(draft_path: Path) -> str:
    """Extract issue title from the first '# ' line of a draft file."""
    try:
        first_line = Path(draft_path).read_text(encoding="utf-8").splitlines()[0]
        if first_line.startswith("# "):
            return first_line[2:].strip()
    except Exception:
        pass
    return Path(draft_path).stem.replace("__", " ").replace("_", " ")


def _op_title(repo: str, op: str, failure_type: str, soc: str) -> str:
    human = _FAILURE_NAMES.get(failure_type, failure_type)
    return f"[Bug] {repo}/{op}: {human}（{soc}）"


def _repo_title(repo: str, failure_type: Optional[str], n: int, soc: str) -> str:
    if failure_type:
        human = _FAILURE_NAMES.get(failure_type, failure_type)
        return f"[Bug] {repo}: {human}（{n} 个算子，{soc}）"
    return f"[Bug] {repo}: {n} 个算子跑测失败（{soc}）"


# ── sub-block renderers (return plain text, not full sections) ──────────────

def _ops_table_text(recs: list[FailureRecord]) -> str:
    rows = ["| repo | op | failure_type | phase | duration_s | attempts |",
            "|---|---|---|---|---|---|"]
    for r in recs:
        rows.append(f"| {r.repo} | {r.op} | {r.failure_type} | {r.phase} | "
                    f"{r.duration_s} | {r.attempts} |")
    return "\n".join(rows)


def _repro_text(recs: list[FailureRecord], soc: str) -> str:
    ops = ",".join(sorted({r.op for r in recs}))
    first_op = ops.split(",")[0]
    return (
        "（社区相对路径，省略本地绝对路径）\n"
        "```bash\n"
        "cd <repo_root>\n"
        f"bash build.sh --pkg --soc={soc} --ops={ops} -j16\n"
        f"bash build.sh --run_example {first_op} eager cust --vendor_name=custom\n"
        "```"
    )


def _log_excerpt_text(recs: list[FailureRecord]) -> str:
    parts = []
    for r in recs:
        parts.append(f"### {r.op} ({r.failure_type})")
        lines = _extract_log(Path(r.log_path), r.failure_type)
        parts.append(log_extract.format_as_code_block(lines))
    return "\n\n".join(parts)


def _extract_log(log_path: Path, failure_type: str, max_lines: int = 80) -> list[str]:
    """Extract log lines appropriate for the failure type."""
    if not log_path.exists():
        return ["(log file not found)"]
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ["(log file unreadable)"]

    if failure_type == "RUN_PATTERN_FAIL":
        # exit=0 means no error keywords — extract stdout section instead
        stdout_lines = _stdout_section(text)
        if stdout_lines:
            return stdout_lines[-max_lines:]

    # Keyword grep for BUILD_FAIL, RUN_EXIT_FAIL, INSTALL_FAIL, TIMEOUT
    matched = log_extract.extract_errors(log_path, max_lines=max_lines)
    if matched != ["(no error keywords matched in log)"]:
        return matched

    # Fallback: last 30 non-empty lines (better than empty placeholder)
    all_lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    return all_lines[-30:] if all_lines else ["(log empty)"]


def _stdout_section(text: str) -> list[str]:
    """Extract lines between '--- STDOUT ---' and '--- STDERR ---'."""
    lines = text.splitlines()
    result, in_stdout = [], False
    for line in lines:
        stripped = line.strip()
        if stripped == "--- STDOUT ---":
            in_stdout = True
            continue
        if stripped == "--- STDERR ---":
            break
        if in_stdout:
            result.append(line.rstrip())
    return result


def _env_tokens(env: dict[str, str]) -> dict[str, str]:
    return {
        "soc": env["soc"],
        "cann_version": env["cann_version"],
        "git_rev": env["git_rev"],
        "python_version": env["python_version"],
        "os": env["os"],
    }


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _drafts_root(repo: str) -> Path:
    return paths.DRAFTS_DIR._resolve() / repo


# ── public builders ──────────────────────────────────────────────────────────

def build_per_op(repo: str,
                 by_type: dict[str, list[FailureRecord]],
                 *, env: dict[str, str], repo_path: Path) -> list[Path]:
    """One file per (op, failure_type). Uses bug_report template."""
    template = _load_template("bug_report")
    written: list[Path] = []
    out_dir = _drafts_root(repo) / "per_op"
    for failure_type, recs in by_type.items():
        for r in recs:
            desc = _FAILURE_DESCS.get(failure_type, f"`{r.op}` 在 {env['soc']} 上跑测失败。")
            body = _render(
                template,
                title=_op_title(repo, r.op, failure_type, env["soc"]),
                description=desc.format(op=r.op, soc=env["soc"]),
                ops_table=_ops_table_text([r]),
                repro_commands=_repro_text([r], env["soc"]),
                log_excerpt=_log_excerpt_text([r]),
                labels=f"bug, ops-failure, soc:{env['soc']}, {failure_type.lower()}",
                **_env_tokens(env),
            )
            path = out_dir / f"{r.op}__{failure_type}.md"
            _write(path, body)
            written.append(path)
    return written


def build_by_type(repo: str,
                  by_type: dict[str, list[FailureRecord]],
                  *, env: dict[str, str], repo_path: Path) -> list[Path]:
    """One file per failure_type, listing all ops of that type."""
    template = _load_template("bug_report")
    written: list[Path] = []
    out_dir = _drafts_root(repo) / "by_type"
    for failure_type, recs in by_type.items():
        n = len(recs)
        body = _render(
            template,
            title=_repo_title(repo, failure_type, n, env["soc"]),
            description=(f"{n} 个算子在 `{env['soc']}` 上出现 "
                         f"{_FAILURE_NAMES.get(failure_type, failure_type)}。"),
            ops_table=_ops_table_text(recs),
            repro_commands=_repro_text(recs, env["soc"]),
            log_excerpt=_log_excerpt_text(recs),
            labels=f"bug, ops-failure, soc:{env['soc']}, {failure_type.lower()}",
            **_env_tokens(env),
        )
        path = out_dir / f"{failure_type}.md"
        _write(path, body)
        written.append(path)
    return written


def build_whole_repo(repo: str,
                     by_type: dict[str, list[FailureRecord]],
                     *, env: dict[str, str], repo_path: Path) -> list[Path]:
    """A single file per repo summarising all failures."""
    template = _load_template("bug_report")
    all_recs: list[FailureRecord] = [r for recs in by_type.values() for r in recs]
    n = len(all_recs)
    # Build per-type log sections
    log_parts = []
    for failure_type, recs in by_type.items():
        log_parts.append(f"**{_FAILURE_NAMES.get(failure_type, failure_type)}**\n")
        log_parts.append(_log_excerpt_text(recs))
    body = _render(
        template,
        title=_repo_title(repo, None, n, env["soc"]),
        description=f"{n} 个算子在 `{env['soc']}` 上跑测失败，涵盖 {len(by_type)} 种失败类型。",
        ops_table=_ops_table_text(all_recs),
        repro_commands=_repro_text(all_recs, env["soc"]),
        log_excerpt="\n\n".join(log_parts),
        labels=f"bug, ops-failure, soc:{env['soc']}",
        **_env_tokens(env),
    )
    path = _drafts_root(repo) / "whole_repo.md"
    _write(path, body)
    return [path]
