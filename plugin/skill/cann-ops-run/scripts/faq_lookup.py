"""Read-only FAQ lookup for cann-ops-run failure-recovery hook.

Never raises: malformed JSON, missing files, missing signatures all return None.
Filters out fix_kind='patch' (those need user-controlled git ops, not silent retry)."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from . import _error_sig
except ImportError:
    import _error_sig  # type: ignore


FAQ_JSON_REL = Path("cann-ops-report") / "faq" / "known_fixes.json"

_NON_SOURCE_KINDS = {"env", "build_flag", "cmd_arg", "upgrade"}


def _faq_path() -> Path:
    return Path.cwd() / FAQ_JSON_REL


def lookup_from_log(
    *,
    repo: str,
    op: str,
    failure_type: str,
    log_path,
    precomputed_signature: str | None = None,
) -> dict | None:
    """Return matching fix entry, or None."""
    faq = _faq_path()
    if not faq.exists():
        return None
    try:
        data = json.loads(faq.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    sig = precomputed_signature
    if sig is None:
        try:
            line = _error_sig.first_error_line(log_path)
            if not line:
                return None
            sig = _error_sig.signature(line)
        except Exception:
            return None

    key = f"{repo}::{op}::{failure_type}::{sig}"
    entry = data.get(key)
    if entry is None:
        return None
    if entry.get("fix_kind") not in _NON_SOURCE_KINDS:
        return None
    return entry


def lookup_all_failed(failed_ops: list[dict]) -> list[dict]:
    """Batch lookup. failed_ops: list of {repo, op, failure_type, log_path}."""
    hits = []
    for f in failed_ops:
        e = lookup_from_log(
            repo=f["repo"], op=f["op"],
            failure_type=f["failure_type"],
            log_path=f["log_path"],
        )
        if e is not None:
            hits.append({
                "repo": f["repo"], "op": f["op"],
                "failure_type": f["failure_type"],
                "fix_entry": e,
            })
    return hits


def _cli(argv=None) -> int:
    """CLI 入口：读收尾闸门产出的 postrun_actions.json，批量查 FAQ。

    包成命令而不是让 agent 在对话里 import，是因为手写 python 片段等于把这段代码
    读一遍再写一遍——函数签名和返回结构每次都要重新载入上下文。
    """
    import argparse
    import json as _json
    import sys as _sys

    ap = argparse.ArgumentParser(
        prog="faq_lookup.py",
        description="对 postrun_actions.json 的 failed_ops 批量查 FAQ 已知修复方案")
    ap.add_argument(
        "--actions",
        default=str(Path.cwd() / "cann-ops-report" / "postrun_actions.json"),
        help="收尾闸门产出的 postrun_actions.json，默认 CWD/cann-ops-report/ 下那份")
    args = ap.parse_args(argv)

    src = Path(args.actions)
    if not src.is_file():
        print(f"[fatal] 找不到 {src}。先跑一轮 runner，闸门会生成它。", file=_sys.stderr)
        return 1

    failed = _json.loads(src.read_text(encoding="utf-8")).get("failed_ops", [])
    hits = lookup_all_failed(failed)
    print(f"{len(failed)} 个失败算子，FAQ 命中 {len(hits)} 个。", file=_sys.stderr)
    if not hits:
        print("没有命中，直接进 P5.7 自动续跑，不必向用户提 FAQ。", file=_sys.stderr)
    _json.dump(hits, _sys.stdout, ensure_ascii=False, indent=2)
    _sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
