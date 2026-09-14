"""Maintain known_fixes.json (machine-readable) + FAQ.md (human-readable).

Key schema: <repo>::<op>::<failure_type>::<error_signature>
Collision policy: newer wins; previous record pushed onto history[].
Atomic writes via tempfile + os.replace.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from . import paths
except ImportError:
    import paths


def _key(repo: str, op: str, failure_type: str, error_signature: str) -> str:
    return f"{repo}::{op}::{failure_type}::{error_signature}"


def _load() -> dict:
    p = Path(paths.FAQ_JSON)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _atomic_save(data: dict) -> None:
    p = Path(paths.FAQ_JSON)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".faq.", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def upsert(
    *,
    repo: str,
    op: str,
    failure_type: str,
    error_signature: str,
    fix_kind: str,
    fix_payload: dict[str, Any],
    source_issue_url: str,
    verified_phase: str,
    soc: str,
) -> None:
    data = _load()
    k = _key(repo, op, failure_type, error_signature)
    new_entry = {
        "fix_kind": fix_kind,
        "fix_payload": fix_payload,
        "source_issue_url": source_issue_url,
        "verified_at": datetime.now().isoformat(timespec="seconds"),
        "verified_phase": verified_phase,
        "soc": soc,
        "history": [],
    }
    if k in data:
        old = {x: data[k][x] for x in data[k] if x != "history"}
        new_entry["history"] = [old] + data[k].get("history", [])
    data[k] = new_entry
    _atomic_save(data)
    _render_md(data)


def lookup(
    *, repo: str, op: str, failure_type: str, error_signature: str,
) -> dict | None:
    return _load().get(_key(repo, op, failure_type, error_signature))


def _render_md(data: dict) -> None:
    lines = ["# CANN ops 已知修复 FAQ\n",
             f"自动生成 — 共 {len(data)} 条已验证修复。\n"]
    for key, entry in sorted(data.items()):
        repo, op, failure_type, sig = key.split("::")
        lines.append(f"\n## {repo} · {op} · {failure_type}\n")
        lines.append(f"- error_signature: `{sig}`")
        lines.append(f"- fix_kind: **{entry['fix_kind']}**")
        lines.append(f"- payload: `{json.dumps(entry['fix_payload'], ensure_ascii=False)}`")
        lines.append(f"- 来源 issue: {entry['source_issue_url']}")
        lines.append(f"- 验证 phase / SOC: {entry['verified_phase']} / {entry['soc']}")
        lines.append(f"- verified_at: {entry['verified_at']}")
        if entry.get("history"):
            lines.append(f"- 历史版本数: {len(entry['history'])}")
    p = Path(paths.FAQ_MD)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
