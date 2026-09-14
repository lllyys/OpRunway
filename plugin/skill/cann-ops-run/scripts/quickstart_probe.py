"""从目标仓自己的 docs/QUICKSTART.md 解析该仓的 build / run_example 命令模板。

背景：不同 ops 仓的 build.sh 接口不保证完全一致（flag 名字、--vendor_name 取值等），
硬编码一套全局命令模板只在"跟当年验证过的那几个仓一致"时才准。QUICKSTART.md 是每个仓
自己维护、给人照着操作的权威说明，比命令行 --help 的自由文本更适合当解析源。

不做缓存：每次调用都重新读取并解析当前的 QUICKSTART.md。文档可能被仓维护者更新，
一份放久了的缓存会导致跑测悄悄用着已经不对的命令——这比每次多花一次文件解析的
开销危险得多，所以不提供"存在就复用"的路径。

解析不到时对应字段返回 None，调用方必须 fallback 到默认模板并记录警告，
不允许静默使用默认模板而不告知（参见 cann-ops-run 的 SKILL.md P0）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, TypedDict

QUICKSTART_REL_PATH = "docs/QUICKSTART.md"

_FENCE_RE = re.compile(r"```bash\s*\n(.*?)```", re.DOTALL)
_JOBS_RE = re.compile(r"-j\d+")


class CommandTemplates(TypedDict):
    build: Optional[str]
    run: Optional[str]
    source: str


def _fenced_bash_lines(text: str) -> list[str]:
    """展开所有 ```bash fenced 代码块，按非空行拍平返回。"""
    lines: list[str] = []
    for block in _FENCE_RE.findall(text):
        for line in block.splitlines():
            line = line.strip()
            if line:
                lines.append(line)
    return lines


def _extract_build_template(lines: list[str]) -> Optional[str]:
    """找形如 `bash build.sh --pkg ... --soc=X ... --ops=Y ...` 的一行，
    把 X/Y 换成占位符，-jN 换成 {jobs}（跑测的并发度是运行时决定的，不该抄文档里的数字）。
    """
    for line in lines:
        if "build.sh" not in line or "--pkg" not in line:
            continue
        soc_m = re.search(r"--soc=(\S+)", line)
        ops_m = re.search(r"--ops=(\S+)", line)
        if not (soc_m and ops_m):
            continue
        template = line.replace(f"--soc={soc_m.group(1)}", "--soc={soc}")
        template = template.replace(f"--ops={ops_m.group(1)}", "--ops={op}")
        template = _JOBS_RE.sub("-j{jobs}", template)
        if "{jobs}" not in template:
            template = f"{template} -j{{jobs}}"
        return template
    return None


def _extract_run_template(lines: list[str]) -> Optional[str]:
    """找形如 `bash build.sh --run_example <op> <mode> <pkg_mode> --vendor_name=<v>` 的一行，
    只把示例算子名换成占位符——mode / pkg_mode / vendor_name 是这个仓自己的约定，原样保留。
    """
    for line in lines:
        if "--run_example" not in line:
            continue
        m = re.search(r"--run_example\s+(\S+)", line)
        if not m:
            continue
        op_token = m.group(1)
        return line.replace(f"--run_example {op_token}", "--run_example {op}")
    return None


def discover_command_templates(repo_path: Path) -> CommandTemplates:
    """从 <repo_path>/docs/QUICKSTART.md 解析 build / run_example 命令模板。

    每次调用都重新读文件解析，两个字段互相独立——某一个解析不到不影响另一个。
    调用方对每个 None 字段各自 fallback 到默认模板，并各自记录一条警告。
    """
    doc_path = repo_path / QUICKSTART_REL_PATH
    if not doc_path.is_file():
        return {"build": None, "run": None, "source": str(doc_path)}

    text = doc_path.read_text(encoding="utf-8", errors="replace")
    lines = _fenced_bash_lines(text)
    return {
        "build": _extract_build_template(lines),
        "run": _extract_run_template(lines),
        "source": str(doc_path),
    }
