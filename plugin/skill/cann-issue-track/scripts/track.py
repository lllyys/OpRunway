"""cann-issue-track 的命令行门面:把 SKILL.md 里原先的内联 python 片段收成子命令。

设计边界(重要):**只包装机械动作,不碰判断**。
P2「读评论理解方案」永远是 agent 的活(见 SKILL.md 红线:方案理解不得退化成正则匹配),
本模块没有、也不该有任何"分类器"。

子命令:
  scope     P0   读 state.json,按 status 分组打印(替代 agent 自己遍历渲染)
  fetch     P1   拉评论 + issue 开关状态 + body,落盘,更新 last_checked_at,打印分类结论
  discover  P3.0 自动发现 soc / repo_path,只把真没发现的报成 MISSING
  plan      P3.1 读 plans/<id>.json → build_plan,打印要用户确认的副作用
  retest    P3.2/3.3 应用方案复测,直接给出 PASS/PARTIAL/FAIL/UNCERTAIN 三分支判定
  followup  partial 分支:开 follow-up issue 并注册回 state.json
  finish    P4   写 FAQ + 生成回评 + (确认后)发评论/关 issue
  register  手动注册流:把用户给的 issue URL 写进 state.json

**外发动作一律默认 dry-run**:followup / finish 不带 `--execute` 时只打印将要发出的内容,
不调任何上游 API、不改 state.json。真发前必须走 SKILL.md 的「外发动作确认规则」。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _error_sig  # noqa: E402
import apply_plan  # noqa: E402
import context_discovery  # noqa: E402
import faq_writer  # noqa: E402
import fetch_comments  # noqa: E402
import paths  # noqa: E402
import reply_builder  # noqa: E402
import retest_orchestrator  # noqa: E402
import upstream_writer  # noqa: E402



def _load_dedup():
    """state.json 的读写归 cann-issue-report 的 dedup 模块所有(单一权威),这里只借用。

    它内部是 `from . import paths` 的包内相对导入,裸 `sys.path.insert` + `import dedup`
    会 ImportError(而且 `paths` 这个名字两个 skill 都有,靠 sys.path 撞运气很危险)。
    故显式把 cann-issue-report/scripts 按包加载成 `report_issues_scripts`,让相对导入自洽。
    """
    import importlib
    import importlib.util
    pkg_dir = Path(__file__).resolve().parents[2] / "cann-issue-report" / "scripts"
    name = "report_issues_scripts"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, pkg_dir / "__init__.py", submodule_search_locations=[str(pkg_dir)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{name}.dedup")


dedup = _load_dedup()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _out(payload: dict) -> int:
    """机读结果走 stdout JSON;人读提示各子命令自己 print 到 stderr。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _issue_id(issue_url: str) -> str:
    m = re.search(r"/issues/([^/?#]+)", issue_url or "")
    return m.group(1) if m else ""


# ── P0 scope ──────────────────────────────────────────────────────────────────

# status → 展示分组(与 SKILL.md P0 表一致;无 status 字段的老记录按 submitted 处理)
_GROUPS = [
    ("⏳ 等待回复", {"submitted", ""}),
    ("🔁 有回复待处理", {"replied_discuss", "replied_pr_pending"}),
    ("🔁 方案已选，等复测", {"plan_selected"}),
    ("❌ 复测失败待追问", {"retest_fail"}),
    ("✅ 已闭环", {"closed_pass", "closed_by_track_issues",
                 "closed_by_track_issues_partial", "closed_upstream"}),
]
_HIDDEN = {"deleted_upstream"}


def cmd_scope(args: argparse.Namespace) -> int:
    records = dedup.load_all()
    groups: dict[str, list] = {label: [] for label, _ in _GROUPS}
    other: list = []
    for key, rec in sorted(records.items()):
        repo, op, failure_type = (key.split("::") + ["", ""])[:3]
        status = rec.get("status", "")
        if status in _HIDDEN:
            continue
        item = {"repo": repo, "op": op, "failure_type": failure_type,
                "status": status or "submitted", "issue_url": rec.get("issue_url", ""),
                "soc": rec.get("soc", ""), "last_checked_at": rec.get("last_checked_at", "")}
        for label, statuses in _GROUPS:
            if status in statuses:
                groups[label].append(item)
                break
        else:
            other.append(item)
    if other:
        groups["❓ 其它状态"] = other

    total = sum(len(v) for v in groups.values())
    print(f"已提交 {total} 个 issue：", file=sys.stderr)
    for label, items in groups.items():
        if not items:
            continue
        print(f"  {label} ({len(items)})：", file=sys.stderr)
        for it in items:
            print(f"    - {it['repo']} / {it['op']} #{_issue_id(it['issue_url'])}"
                  f"  [{it['failure_type']}]", file=sys.stderr)
    return _out({"total": total, "groups": groups})


# ── P1 fetch ──────────────────────────────────────────────────────────────────

def cmd_fetch(args: argparse.Namespace) -> int:
    url = args.issue_url
    iid = _issue_id(url)
    comments = fetch_comments.fetch(url, raise_on_error=False)
    state = fetch_comments.fetch_issue_state(url, raise_on_error=False)

    body = state.get("body", "") if isinstance(state, dict) else ""
    if body:
        bp = paths.ISSUES_DIR / "bodies" / args.repo / f"{iid}.txt"
        Path(bp).parent.mkdir(parents=True, exist_ok=True)
        Path(bp).write_text(body, encoding="utf-8")

    cp = paths.COMMENTS_DIR / args.repo / f"{iid}.json"
    Path(cp).parent.mkdir(parents=True, exist_ok=True)
    Path(cp).write_text(json.dumps(comments, ensure_ascii=False, indent=2), encoding="utf-8")

    def _update(status: str, **extra) -> None:
        try:
            dedup.update_status(args.repo, args.op, args.failure_type, status,
                                last_checked_at=_now(), **extra)
        except KeyError:
            print(f"[warn] state.json 里没有 {args.repo}::{args.op}::{args.failure_type}，"
                  f"跳过状态回写", file=sys.stderr)

    # 判定优先级:开关状态 > 拉取异常 > 评论内容。与 SKILL.md P1 表一一对应。
    if isinstance(state, dict) and state.get("state") == "closed":
        _update("closed_upstream", closed_at=state.get("closed_at", ""))
        return _out({"classification": "closed_upstream", "issue_id": iid,
                     "comments_path": str(cp), "actionable": False,
                     "note": "上游 maintainer 已关闭，跳过 P2"})

    if isinstance(comments, dict) and comments.get("status") == "deleted_upstream":
        _update("deleted_upstream")
        return _out({"classification": "deleted_upstream", "issue_id": iid, "actionable": False})

    if isinstance(comments, dict) and comments.get("status") == "fetch_failed":
        # 拉取失败**不改 status**(否则会把网络问题记成业务状态)
        print(f"[warn] 拉取失败：{comments.get('error', '')}", file=sys.stderr)
        return _out({"classification": "fetch_failed", "issue_id": iid, "actionable": False,
                     "error": comments.get("error", "")})

    items = comments if isinstance(comments, list) else []
    submitter = (args.submitter or "").lower()
    external = [c for c in items
                if not submitter or (c.get("author") or "").lower() != submitter]
    if not items:
        cls = "no_reply"
    elif submitter and not external:
        cls = "self_only"
    else:
        cls = "has_external_comments"
    _update(args.keep_status or "submitted")
    return _out({"classification": cls, "issue_id": iid, "comments_path": str(cp),
                 "comment_count": len(items), "external_count": len(external),
                 "actionable": cls == "has_external_comments",
                 "note": "进 P2 由 agent 自己读 comments 判方案" if cls == "has_external_comments"
                         else "跳过"})


# ── P3.0 discover ─────────────────────────────────────────────────────────────

def cmd_discover(args: argparse.Namespace) -> int:
    body = ""
    if args.issue_id:
        bp = Path(paths.ISSUES_DIR / "bodies" / args.repo / f"{args.issue_id}.txt")
        if bp.exists():
            body = bp.read_text(encoding="utf-8", errors="replace")
    soc = context_discovery.discover_soc(repo=args.repo, op=args.op,
                                         failure_type=args.failure_type, issue_body=body)
    repo_path = context_discovery.discover_repo_path(args.repo)
    missing = [k for k, v in (("soc", soc), ("repo_path", repo_path)) if not v]
    if missing:
        print(f"[需询问用户] 未能自动发现：{', '.join(missing)}（其余已发现，一次问完这几项即可）",
              file=sys.stderr)
    return _out({"soc": soc or "", "repo_path": repo_path or "", "missing": missing})


# ── P3.1 plan ─────────────────────────────────────────────────────────────────

def cmd_plan(args: argparse.Namespace) -> int:
    pp = Path(paths.PLANS_DIR / f"{args.issue_id}.json")
    if not pp.exists():
        print(f"[ERROR] 方案文件不存在：{pp}（P2 选定方案后应先落盘）", file=sys.stderr)
        return 1
    solution = json.loads(pp.read_text(encoding="utf-8"))
    try:
        plan = apply_plan.build_plan(solution=solution, context={
            "repo": args.repo, "op": args.op, "failure_type": args.failure_type,
            "repo_path": args.repo_path, "issue_id": args.issue_id})
    except (ValueError, RuntimeError) as exc:
        print(f"[ERROR] build_plan 失败：{exc}", file=sys.stderr)
        return 1
    # 需要用户点头的副作用,单独摘出来提示(upgrade/patch/clean)
    needs_ok = bool(plan.get("requires_user_action")) or bool(plan.get("pre_cleanup_commands")) \
        or plan.get("kind") == "patch"
    if needs_ok:
        print(f"[需用户确认] kind={plan.get('kind')}", file=sys.stderr)
        for cmd in plan.get("pre_cleanup_commands", []) or []:
            print(f"    清理命令：{cmd}", file=sys.stderr)
        if plan.get("branch"):
            print(f"    已建分支：{plan['branch']}（不自动 git apply）", file=sys.stderr)
    return _out({"plan": plan, "needs_user_confirmation": needs_ok})


# ── P3.2 / P3.3 retest ────────────────────────────────────────────────────────

def cmd_retest(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8")) if args.plan_file \
        else {"kind": args.kind or "env", "ops_test_args": []}
    if "plan" in plan:            # 允许直接喂 `track plan` 的输出
        plan = plan["plan"]
    print(f"正在对 {args.repo}/{args.op} 应用 {plan.get('kind')} 方案并复测，请稍候…",
          file=sys.stderr)
    result = retest_orchestrator.retest(plan=plan, context={
        "repo": args.repo, "op": args.op, "repo_path": args.repo_path,
        "soc": args.soc, "failure_type": args.failure_type})
    print(f"[复测结果] verdict={result['verdict']}  ({result['reason']})", file=sys.stderr)
    if result["verdict"] == "PARTIAL":
        print("    → 原问题已修复但有全新失败面：走 references/partial-pass.md", file=sys.stderr)
    elif result["verdict"] == "UNCERTAIN":
        print("    → 没真正跑起来/无强信号：先人工复核日志，不要据此回评上游", file=sys.stderr)
    return _out(result)


# ── partial 分支:follow-up issue ──────────────────────────────────────────────

_ERR_GREP = re.compile(r"ERROR|undefined|failed\.|exit=", re.IGNORECASE)


def _error_snippet(log_path: str, limit: int = 1500) -> str:
    if not log_path:                       # Path("") 会变成 '.'(目录),必须先挡掉
        return ""
    p = Path(log_path)
    if not p.is_file():
        return ""
    hits = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
            if _ERR_GREP.search(ln)]
    return "\n".join(hits)[-limit:]


def cmd_followup(args: argparse.Namespace) -> int:
    repo_url = args.issue_url.rsplit("/issues/", 1)[0]
    orig_id = _issue_id(args.issue_url)
    failed = [f"{args.op} run_example（{args.op_status or 'run 期失败'}）"]
    title = (f"[{args.repo}] {args.op} fails at runtime on {args.soc} "
             f"(follow-up to #{orig_id})")
    body = reply_builder.build_followup_issue_body(
        repo=args.repo, op=args.op, soc=args.soc,
        source_issue_url=args.issue_url,
        fix_kind=args.kind, fix_summary=args.payload,
        failed_examples=failed,
        error_snippet=_error_snippet(args.log))

    if not args.execute:
        print("─" * 60, file=sys.stderr)
        print(f"将向 {repo_url} 新建 issue：\n\n标题：{title}\n\n{body}", file=sys.stderr)
        print("─" * 60, file=sys.stderr)
        print("[dry-run] 未发出。经用户确认后加 --execute 重跑。", file=sys.stderr)
        return _out({"dry_run": True, "title": title, "body": body, "repo_url": repo_url})

    os.environ.pop("CANN_OPS_DRY_RUN", None)
    url = upstream_writer.create_issue(repo_url, title=title, body=body)
    # 红线:follow-up 必须注册进 state.json,否则下轮无法追踪
    dedup.mark_submitted(repo=args.repo, op=args.op, failure_type="RUN_EXIT_FAIL",
                         issue_url=url, phase="phase1",
                         submitted_via="track_issues_followup", soc=args.soc,
                         status="submitted", parent_issue_url=args.issue_url)
    print(f"[已创建] {url}（已注册到 state.json）", file=sys.stderr)
    return _out({"dry_run": False, "followup_url": url, "title": title})


# ── P4 finish ─────────────────────────────────────────────────────────────────

def cmd_finish(args: argparse.Namespace) -> int:
    kind, payload = args.kind, args.payload
    if args.outcome == "pass":
        body = reply_builder.build_pass_reply(repo=args.repo, op=args.op, soc=args.soc,
                                              fix_kind=kind, fix_summary=payload)
        action, new_status = "发评论 + 关闭 issue", "closed_by_track_issues"
    elif args.outcome == "partial":
        if not args.followup_url:
            print("[ERROR] partial 分支必须先建 follow-up issue 并传 --followup-url"
                  "（没有 follow-up URL 就不该关原 issue）", file=sys.stderr)
            return 1
        body = reply_builder.build_partial_pass_reply(
            repo=args.repo, op=args.op, soc=args.soc, fix_kind=kind, fix_summary=payload,
            original_failure_type=args.failure_type,
            failed_examples=[f"{args.op} run_example（{args.op_status or 'run 期失败'}）"],
            followup_issue_url=args.followup_url)
        action, new_status = "发评论 + 关闭 issue", "closed_by_track_issues_partial"
    else:  # fail
        body = reply_builder.build_fail_reply(
            repo=args.repo, op=args.op, soc=args.soc, fix_kind=kind, fix_summary=payload,
            error_snippet=_error_snippet(args.log, 500))
        action, new_status = "发追问评论（不关 issue）", ""

    if not args.execute:
        print("─" * 60, file=sys.stderr)
        print(f"将向 {args.issue_url} 执行：{action}\n\n{body}", file=sys.stderr)
        print("─" * 60, file=sys.stderr)
        print("[dry-run] 未发出、FAQ 未写。经用户确认后加 --execute 重跑。", file=sys.stderr)
        return _out({"dry_run": True, "outcome": args.outcome, "body": body, "action": action})

    os.environ.pop("CANN_OPS_DRY_RUN", None)
    faq_written = False
    if args.outcome in ("pass", "partial"):
        # 方案确实让原始失败恢复了 → 照写 FAQ
        faq_writer.upsert(repo=args.repo, op=args.op, failure_type=args.failure_type,
                          error_signature=_error_sig.signature(
                              _error_sig.first_error_line(args.log)) if args.log else "",
                          fix_kind=kind, fix_payload={"summary": payload},
                          source_issue_url=args.issue_url, verified_phase="phase1",
                          soc=args.soc)
        faq_written = True

    upstream_writer.post_comment(args.issue_url, body)
    reply_dir = Path(paths.REPLIES_DIR / args.repo)
    reply_dir.mkdir(parents=True, exist_ok=True)
    (reply_dir / f"{_issue_id(args.issue_url)}.json").write_text(
        json.dumps({"outcome": args.outcome, "body": body, "at": _now()},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    if new_status:
        upstream_writer.close_issue(args.issue_url)
        extra = {"closed_at": _now()}
        if args.followup_url:
            extra["followup_issue_url"] = args.followup_url
        try:
            dedup.update_status(args.repo, args.op, args.failure_type, new_status, **extra)
        except KeyError:
            print(f"[warn] state.json 无对应记录，状态未回写", file=sys.stderr)

    print(f"[已完成] {action}；FAQ {'已写' if faq_written else '未写'}", file=sys.stderr)
    return _out({"dry_run": False, "outcome": args.outcome, "faq_written": faq_written,
                 "status": new_status})


# ── 手动注册 ──────────────────────────────────────────────────────────────────

def cmd_register(args: argparse.Namespace) -> int:
    dedup.mark_submitted(repo=args.repo, op=args.op, failure_type=args.failure_type,
                         issue_url=args.issue_url, phase="phase1",
                         submitted_via="manual", soc=args.soc, status="submitted")
    print(f"[已注册] {args.repo}/{args.op} → {args.issue_url}", file=sys.stderr)
    return _out({"registered": True, "issue_url": args.issue_url})


# ── CLI ───────────────────────────────────────────────────────────────────────

def _add_target(p: argparse.ArgumentParser, *, need_ft: bool = True) -> None:
    p.add_argument("--repo", required=True)
    p.add_argument("--op", required=True)
    p.add_argument("--failure-type", required=need_ft, default="")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="track", description="cann-issue-track 命令行门面")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scope", help="P0：按 status 分组列出已提交 issue").set_defaults(fn=cmd_scope)

    p = sub.add_parser("fetch", help="P1：拉评论/开关状态/body 并分类")
    _add_target(p); p.add_argument("--issue-url", required=True)
    p.add_argument("--submitter", default="", help="提交者账号，用于识别 self_only")
    p.add_argument("--keep-status", default="", help="非终态时保持的原 status")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("discover", help="P3.0：自动发现 soc / repo_path")
    _add_target(p); p.add_argument("--issue-id", default="")
    p.set_defaults(fn=cmd_discover)

    p = sub.add_parser("plan", help="P3.1：读 plans/<id>.json 生成执行计划")
    _add_target(p); p.add_argument("--issue-id", required=True)
    p.add_argument("--repo-path", required=True)
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("retest", help="P3.2/3.3：复测并给出三分支判定")
    _add_target(p); p.add_argument("--repo-path", required=True)
    p.add_argument("--soc", required=True)
    p.add_argument("--plan-file", default="", help="track plan 的输出 JSON")
    p.add_argument("--kind", default="", help="无 plan-file 时的 kind")
    p.set_defaults(fn=cmd_retest)

    p = sub.add_parser("followup", help="partial 分支：开 follow-up issue（默认 dry-run）")
    _add_target(p); p.add_argument("--issue-url", required=True)
    p.add_argument("--soc", required=True)
    p.add_argument("--kind", required=True); p.add_argument("--payload", required=True)
    p.add_argument("--log", default="", help="run 日志路径，用于抽错误片段")
    p.add_argument("--op-status", default="", help="复测后的算子状态，如 RUN_EXIT_FAIL")
    p.add_argument("--execute", action="store_true", help="用户确认后真发")
    p.set_defaults(fn=cmd_followup)

    p = sub.add_parser("finish", help="P4：写 FAQ + 回评 + 关 issue（默认 dry-run）")
    _add_target(p); p.add_argument("--issue-url", required=True)
    p.add_argument("--outcome", required=True, choices=["pass", "partial", "fail"])
    p.add_argument("--soc", required=True)
    p.add_argument("--kind", required=True); p.add_argument("--payload", required=True)
    p.add_argument("--log", default="", help="原始失败日志，用于 FAQ 签名 / 错误摘录")
    p.add_argument("--followup-url", default="", help="partial 分支必填")
    p.add_argument("--op-status", default="")
    p.add_argument("--execute", action="store_true", help="用户确认后真发")
    p.set_defaults(fn=cmd_finish)

    p = sub.add_parser("register", help="手动注册流：把 issue URL 写进 state.json")
    _add_target(p); p.add_argument("--issue-url", required=True)
    p.add_argument("--soc", default="")
    p.set_defaults(fn=cmd_register)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
