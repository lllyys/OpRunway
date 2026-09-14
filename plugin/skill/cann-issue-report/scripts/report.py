#!/usr/bin/env python3
"""cann-issue-report 的命令行门面：把失败算子转成上游 issue 的每一步机械动作。

用法：<python> <skill>/scripts/report.py <子命令> [...]，**cwd 在项目根**
（产物落 `CWD/cann-ops-report/issues/`）。

设计同 cann-issue-track 的 track.py：机读结果走 stdout（JSON），人读提示走 stderr。
把这些包成子命令，是因为在对话里手写 python 片段等于把同一段代码读一遍再写一遍——
每次调用都要把函数签名和返回结构重新载入上下文。

子命令：
  scope    读 run_state.json，按 (repo, failure_type) 分组列出失败算子
  resolve  从 git remote 推 platform/owner/repo，写 repos.json 缓存
  drafts   生成 per-op 草稿
  urls     为草稿构造 prefilled URL（用户自己提时用）
  submit   调 gh CLI / Gitee API / GitCode API 提交（agent 帮提时用）
  mark     把已提交的 issue URL 写回 state.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import (  # noqa: E402
    failures,
    mark_submitted,
    orchestrate,
    paths,
    repo_resolver,
    submit as submit_mod,
    token_helper,
    url_builder,
)


def _emit(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _die(msg: str, code: int = 1):
    print(f"[fatal] {msg}", file=sys.stderr)
    raise SystemExit(code)


def _parse_repo_mapping(pairs: list[str]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for pair in pairs:
        if "=" not in pair:
            _die(f"--repo-mapping 要写成 repo=path，收到：{pair}", 2)
        name, _, path = pair.partition("=")
        out[name.strip()] = Path(path.strip()).expanduser()
    return out


# ── scope ────────────────────────────────────────────────────────────────
def cmd_scope(args) -> int:
    try:
        grouped = failures.load_failures()
    except FileNotFoundError as e:
        _die(str(e))
    out = {}
    for repo, by_type in grouped.items():
        out[repo] = {ft: [r.op for r in recs] for ft, recs in by_type.items()}
        print(f"{repo}: {sum(len(v) for v in out[repo].values())} 个失败"
              f"（{', '.join(f'{k}={len(v)}' for k, v in out[repo].items())}）",
              file=sys.stderr)
    if not out:
        print("没有失败算子，无需提 issue。", file=sys.stderr)
    _emit(out)
    return 0


# ── resolve ──────────────────────────────────────────────────────────────
def cmd_resolve(args) -> int:
    mapping = _parse_repo_mapping(args.repo_mapping)
    resolved, unresolved = {}, []
    for name, path in mapping.items():
        cached = repo_resolver.read_cache(name)
        if cached:
            resolved[name] = list(cached)
            continue
        info = repo_resolver.resolve_from_remote(path)
        if info:
            repo_resolver.write_cache(name, info, source="git-remote")
            resolved[name] = list(info)
        else:
            unresolved.append(name)
    if unresolved:
        print(f"[warn] 推不出上游平台的仓：{', '.join(unresolved)}。"
              f"把它们攒成一次 AskUserQuestion 问完，别逐仓来回问。", file=sys.stderr)
    _emit({"resolved": resolved, "unresolved": unresolved})
    return 0


# ── drafts ───────────────────────────────────────────────────────────────
def cmd_drafts(args) -> int:
    mapping = _parse_repo_mapping(args.repo_mapping)
    result = orchestrate.generate_drafts(
        repo_paths=mapping,
        soc=args.soc,
        granularities=["per_op"],
        skip_resolved=not args.force,
    )
    out = {}
    for repo, info in result.items():
        files = [str(p) for p in info.get("per_op_files", [])]
        skipped = [list(t) for t in info.get("skipped_already_submitted", [])]
        out[repo] = {"drafts": files, "skipped_already_submitted": skipped}
        print(f"{repo}: 生成 {len(files)} 篇草稿，跳过已提交 {len(skipped)} 项",
              file=sys.stderr)
    print(f"草稿目录：{os.fspath(paths.DRAFTS_DIR)}", file=sys.stderr)
    _emit(out)
    return 0


# ── urls ─────────────────────────────────────────────────────────────────
def _read_draft(path: Path) -> tuple[str, str]:
    """草稿首个 `# ` 标题行是 issue 标题，其余是正文。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return line[2:].strip(), "\n".join(lines[i + 1:]).lstrip("\n")
    return path.stem, text


def cmd_urls(args) -> int:
    out, degraded = [], 0
    for raw in args.draft:
        p = Path(raw)
        if not p.is_file():
            _die(f"草稿不存在：{p}")
        title, body = _read_draft(p)
        res = url_builder.build_prefilled_url(
            platform=args.platform, owner=args.owner, repo=args.repo,
            title=title, body=body, labels=args.label or [])
        degraded += res.degraded
        out.append({"draft": str(p), "title": title,
                    "url": res.url, "degraded": res.degraded})
    if degraded:
        print(f"[warn] {degraded} 篇因 URL 超长降级成空白 issue 页，"
              f"要在交互里告诉用户正文得从草稿里拷。", file=sys.stderr)
    _emit(out)
    return 0


# ── submit ───────────────────────────────────────────────────────────────
def cmd_submit(args) -> int:
    p = Path(args.draft)
    if not p.is_file():
        _die(f"草稿不存在：{p}")
    title, body = _read_draft(p)
    labels = args.label or []

    if args.platform == "github":
        issue_url = submit_mod.submit_github(
            owner=args.owner, repo=args.repo, title=title,
            body_file=p, labels=labels)
    else:
        env_var = token_helper.env_var_for_platform(args.platform)
        token = args.token or (os.environ.get(env_var) if env_var else None)
        if not token:
            _die(f"缺 token：设 {env_var}，或用 --token 传入")
        fn = (submit_mod.submit_gitee if args.platform == "gitee"
              else submit_mod.submit_gitcode)
        issue_url = fn(owner=args.owner, repo=args.repo, title=title,
                       body=body, labels=labels, token=token)

    print(f"已提交：{issue_url}", file=sys.stderr)
    marked = mark_submitted.mark_from_draft_path(
        draft_path=p, issue_url=issue_url, phase=args.phase,
        submitted_via="api", soc=args.soc, status="submitted")
    _emit({"issue_url": issue_url, "marked": marked, "draft": str(p)})
    return 0


# ── mark ─────────────────────────────────────────────────────────────────
def cmd_mark(args) -> int:
    p = Path(args.draft)
    if not p.is_file():
        _die(f"草稿不存在：{p}")
    marked = mark_submitted.mark_from_draft_path(
        draft_path=p, issue_url=args.issue_url, phase=args.phase,
        submitted_via="manual", soc=args.soc, status="submitted")
    print(f"已记录 {marked} 条 → {os.fspath(paths.STATE_FILE)}", file=sys.stderr)
    _emit({"issue_url": args.issue_url, "marked": marked, "draft": str(p)})
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="report.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scope", help="列出各仓失败算子，按 failure_type 分组")
    s.set_defaults(fn=cmd_scope)

    s = sub.add_parser("resolve", help="推 platform/owner/repo 并写缓存")
    s.add_argument("--repo-mapping", nargs="+", required=True, metavar="repo=path")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("drafts", help="生成 per-op 草稿")
    s.add_argument("--repo-mapping", nargs="+", required=True, metavar="repo=path")
    s.add_argument("--soc", required=True, help="来自用户，不猜")
    s.add_argument("--force", action="store_true", help="连已提交过的也重新起草")
    s.set_defaults(fn=cmd_drafts)

    s = sub.add_parser("urls", help="为草稿构造 prefilled URL")
    s.add_argument("--platform", required=True, choices=["github", "gitee", "gitcode"])
    s.add_argument("--owner", required=True)
    s.add_argument("--repo", required=True)
    s.add_argument("--label", action="append")
    s.add_argument("draft", nargs="+")
    s.set_defaults(fn=cmd_urls)

    s = sub.add_parser("submit", help="调上游 API 提交一篇草稿")
    s.add_argument("--platform", required=True, choices=["github", "gitee", "gitcode"])
    s.add_argument("--owner", required=True)
    s.add_argument("--repo", required=True)
    s.add_argument("--draft", required=True)
    s.add_argument("--phase", default="phase1")
    s.add_argument("--soc")
    s.add_argument("--token", help="不传则读 GITEE_TOKEN / GITCODE_TOKEN")
    s.add_argument("--label", action="append")
    s.set_defaults(fn=cmd_submit)

    s = sub.add_parser("mark", help="把用户自己提的 issue URL 写回 state.json")
    s.add_argument("--draft", required=True)
    s.add_argument("--issue-url", required=True)
    s.add_argument("--phase", default="phase1")
    s.add_argument("--soc")
    s.set_defaults(fn=cmd_mark)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
