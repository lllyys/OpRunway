"""cann-doc-quickstart-check 产物读写(机读台账)。所有产物落 CWD/cann-ops-report/doccheck/<repo>/quickstart/。

steps.json 是逐步台账;每条记录「文档原文 + 实际执行 + agent 判定」三段。
verdict 由 agent 看真实输出后判定(本 skill 的纪律:不替文档猜、不探索)。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# 与其它 skill 一致:产物根 = 进程 CWD 下的 cann-ops-report
REPORT_ROOT = Path.cwd() / "cann-ops-report"
DOCCHECK_ROOT = REPORT_ROOT / "doccheck"

# 单步判定枚举:UNJUDGED=已执行待 agent 判;其余为终态
VERDICTS = {"UNJUDGED", "OK", "FAIL", "DOC_AMBIGUOUS", "DOC_MISSING"}
# 三类「卡点」——遇到即停(SKILL.md「卡住即停」)
BLOCKER_VERDICTS = {"FAIL", "DOC_AMBIGUOUS", "DOC_MISSING"}


def repo_dir(repo: str) -> Path:
    return DOCCHECK_ROOT / repo


def meta_path(repo: str) -> Path:
    return repo_dir(repo) / "doc_meta.json"


def steps_path(repo: str) -> Path:
    return repo_dir(repo) / "steps.json"


def logs_dir(repo: str) -> Path:
    return repo_dir(repo) / "steps"


def slug(text: str, maxlen: int = 24) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip())
    return s[:maxlen].strip("_") or "step"


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_steps(repo: str) -> list:
    p = steps_path(repo)
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_steps(repo: str, steps: list) -> None:
    _atomic_write(steps_path(repo), steps)


def upsert_step(repo: str, record: dict) -> None:
    """按 idx 覆盖式写入一条步骤记录(execute 阶段调)。"""
    steps = load_steps(repo)
    idx = record["idx"]
    for i, s in enumerate(steps):
        if s.get("idx") == idx:
            steps[i] = {**s, **record}
            break
    else:
        steps.append(record)
    steps.sort(key=lambda s: s.get("idx", 0))
    save_steps(repo, steps)


def set_verdict(repo: str, idx: int, verdict: str, defect: str | None = None,
                fix: str | None = None, injected_fix: str | None = None) -> bool:
    """agent 给某步打判定 + (可选)缺陷描述 + 修订建议 + (探索趟)注入的修复。返回是否找到该步。

    injected_fix:**探索趟**专用——为绕过本步的文档缺陷,实际注入了哪些「文档外但属本 skill
    提出的修订建议」的东西(如 `source set_env.sh`、`--soc ascend910b→ascend910_93`、`http_proxy`)。
    忠实趟此字段恒空。
    """
    if verdict not in VERDICTS:
        raise ValueError(f"invalid verdict: {verdict}; allowed {sorted(VERDICTS)}")
    steps = load_steps(repo)
    for s in steps:
        if s.get("idx") == idx:
            s["verdict"] = verdict
            if defect is not None:
                s["defect"] = defect
            if fix is not None:
                s["fix_suggestion"] = fix
            if injected_fix is not None:
                s["injected_fix"] = injected_fix
            save_steps(repo, steps)
            return True
    return False


def load_meta(repo: str) -> dict:
    p = meta_path(repo)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_meta(repo: str, meta: dict) -> None:
    _atomic_write(meta_path(repo), meta)
