"""Personal access token handling for Gitee and GitCode.

Lookup order:
    1. os.environ[<env_var>]  (GITEE_TOKEN or GITCODE_TOKEN)
    2. Caller falls back to AskUserQuestion (orchestrate.py — not here)

Optional convenience: after a successful single-use prompt, the user may
ask to persist the token to ~/.bashrc / ~/.zshrc / ~/.profile (their choice).
Writes are explicit and never automatic.

GitHub is handled by `gh` CLI's own auth — no token plumbing here.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


_DEFAULT_ENV_VAR = "GITEE_TOKEN"

_PLATFORM_ENV_VAR = {
    "gitee": "GITEE_TOKEN",
    "gitcode": "GITCODE_TOKEN",
    "github": None,  # gh CLI handles auth
}


def env_var_for_platform(platform: str) -> str | None:
    """Return the env var name a platform uses for its API token, or None."""
    return _PLATFORM_ENV_VAR.get(platform)


def _export_line_pattern(env_var: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*export\s+{re.escape(env_var)}\s*=.*$", re.MULTILINE)


def get_from_env(env_var: str = _DEFAULT_ENV_VAR) -> str | None:
    val = os.environ.get(env_var, "").strip()
    return val or None


def has_existing_export(rc_path: Path, env_var: str = _DEFAULT_ENV_VAR) -> bool:
    p = Path(rc_path)
    if not p.exists():
        return False
    return _export_line_pattern(env_var).search(p.read_text(encoding="utf-8")) is not None


def write_to_shell(rc_path: Path, token: str, env_var: str = _DEFAULT_ENV_VAR) -> None:
    """Append `export <env_var>=<token>` to rc_path. Creates file if missing."""
    p = Path(rc_path)
    line = f"\nexport {env_var}={token}\n"
    if not p.exists():
        p.write_text(line.lstrip("\n"), encoding="utf-8")
        return
    with p.open("a", encoding="utf-8") as f:
        f.write(line)


def overwrite_existing(rc_path: Path, token: str, env_var: str = _DEFAULT_ENV_VAR) -> None:
    """Replace any existing `export <env_var>=...` line with the new token."""
    p = Path(rc_path)
    if not p.exists():
        return write_to_shell(rc_path, token, env_var=env_var)
    text = p.read_text(encoding="utf-8")
    new_text = _export_line_pattern(env_var).sub(f"export {env_var}={token}", text)
    p.write_text(new_text, encoding="utf-8")
