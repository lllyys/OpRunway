"""定位 / clone CANN 算子仓，并切到与 CANN 版本配套的 tag。

核心教训（来自实测）：算子仓的 master 是中间态（算子代码已升级但 opbase pin 落后），
直接编会撞 `OP_LOGE_FOR_INVALID_*` 缺符号。必须切到与 CANN 版本配套的 tag
（如 CANN 9.0.0 → 仓 tag v9.0.0）。本模块把这条固化成默认行为。

仓名/仓库 host/搜索根都是参数（零硬编码）；下面的默认值只是“常见值”，可被 SKILL 覆盖。

CLI:
  python3 repo_setup.py plan  --cann-version 9.0.0-beta.1 --repo ops-cv --search-root /home/x
  python3 repo_setup.py tags  --cann-version 9.0.0 --remote https://gitcode.com/cann/ops-cv.git
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

# 默认算子仓集合（这套工作流面向的四仓）；--repos 可覆盖成任意子集/超集。
DEFAULT_REPOS = ["ops-cv", "ops-math", "ops-nn", "ops-transformer"]
# 默认 git host（gitcode 上的 cann 组织）；--git-base 可换成任意前缀。
DEFAULT_GIT_BASE = "https://gitcode.com/cann"


# ---- 纯逻辑（可单测，无 I/O）----

def version_to_tag_candidates(version_full: str | None) -> list[str]:
    """CANN 版本 → 按“最具体优先”排序的候选 tag。

    "9.0.0-beta.1" → ["v9.0.0-beta.1", "9.0.0-beta.1", "v9.0.0", "9.0.0"]
    "9.0.0"        → ["v9.0.0", "9.0.0"]
    None           → []
    """
    if not version_full:
        return []
    cands: list[str] = []
    core = version_full.split("-", 1)[0]
    if "-" in version_full or version_full != core:
        cands += [f"v{version_full}", version_full]
    cands += [f"v{core}", core]
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def pick_matching_tag(candidates: list[str], available: list[str]) -> str | None:
    """候选里第一个在 available 中存在的 tag；都不在返回 None。"""
    avail = set(available)
    for c in candidates:
        if c in avail:
            return c
    return None


# ---- git I/O ----

def _git(args: list[str], cwd: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def list_local_tags(repo_path: str) -> list[str]:
    try:
        res = _git(["tag", "-l"], cwd=repo_path)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [t.strip() for t in res.stdout.splitlines() if t.strip()]


def list_remote_tags(remote_url: str) -> list[str]:
    """不 clone 也能列远端 tag（git ls-remote --tags）。"""
    try:
        res = _git(["ls-remote", "--tags", remote_url], timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return []
    tags: list[str] = []
    for line in res.stdout.splitlines():
        parts = line.split("refs/tags/")
        if len(parts) == 2:
            tags.append(parts[1].replace("^{}", "").strip())
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def locate_repo(name: str, search_roots: list[str]) -> str | None:
    """在搜索根下找已存在的算子仓（含 build.sh 的同名目录优先）。"""
    for root in search_roots:
        rp = Path(root)
        if not rp.is_dir():
            continue
        # 直接子目录同名
        direct = rp / name
        if (direct / "build.sh").is_file():
            return str(direct)
        # 一层递归找同名且含 build.sh 的目录
        for child in sorted(rp.glob(f"**/{name}")):
            if child.is_dir() and (child / "build.sh").is_file():
                return str(child)
    return None


def current_ref(repo_path: str) -> str | None:
    """当前 checkout 的 tag 或分支或短 sha（用于判断是不是停在 master）。"""
    try:
        t = _git(["describe", "--tags", "--exact-match"], cwd=repo_path)
        if t.returncode == 0 and t.stdout.strip():
            return t.stdout.strip()
        b = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
        if b.returncode == 0 and b.stdout.strip() and b.stdout.strip() != "HEAD":
            return b.stdout.strip()
        s = _git(["rev-parse", "--short", "HEAD"], cwd=repo_path)
        return s.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def plan_repo(name: str, cann_version: str | None, search_roots: list[str],
              git_base: str) -> dict:
    """对单仓产出“该怎么办”的计划（不执行任何写操作）。

    返回 path/clone_url/available_tags/target_tag/action/current_ref。
    action ∈ {checkout, clone_then_checkout, no_matching_tag, ...}
    """
    candidates = version_to_tag_candidates(cann_version)
    path = locate_repo(name, search_roots)
    clone_url = f"{git_base.rstrip('/')}/{name}.git"

    if path:
        tags = list_local_tags(path)
        target = pick_matching_tag(candidates, tags)
        cur = current_ref(path)
        action = "checkout" if target else "no_matching_tag_local"
        return {"repo": name, "path": path, "clone_url": clone_url,
                "available_tags_sample": tags[:20], "tag_candidates": candidates,
                "target_tag": target, "current_ref": cur, "action": action}

    tags = list_remote_tags(clone_url)
    target = pick_matching_tag(candidates, tags)
    action = "clone_then_checkout" if target else "no_matching_tag_remote"
    return {"repo": name, "path": None, "clone_url": clone_url,
            "available_tags_sample": tags[:20], "tag_candidates": candidates,
            "target_tag": target, "current_ref": None, "action": action}


def main() -> int:
    ap = argparse.ArgumentParser(description="算子仓定位/clone + 切配套 tag（plan 只读，apply 待 SKILL 确认后调）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="只读：给出每仓的 clone/checkout 计划")
    p_plan.add_argument("--cann-version", required=True, help="CANN 版本，如 9.0.0 或 9.0.0-beta.1")
    p_plan.add_argument("--repos", default=",".join(DEFAULT_REPOS), help="仓名 CSV")
    p_plan.add_argument("--search-root", action="append", default=[], help="搜索已存在仓的根（可多次）")
    p_plan.add_argument("--git-base", default=DEFAULT_GIT_BASE)
    p_plan.add_argument("--json", action="store_true")

    p_tags = sub.add_parser("tags", help="只读：列远端 tag 并给配套结果")
    p_tags.add_argument("--cann-version", required=True)
    p_tags.add_argument("--remote", required=True)

    args = ap.parse_args()

    if args.cmd == "tags":
        cands = version_to_tag_candidates(args.cann_version)
        avail = list_remote_tags(args.remote)
        print(json.dumps({"candidates": cands, "available": avail,
                          "matched": pick_matching_tag(cands, avail)}, ensure_ascii=False, indent=2))
        return 0

    repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    roots = args.search_root or []
    plans = [plan_repo(r, args.cann_version, roots, args.git_base) for r in repos]
    if args.json:
        print(json.dumps(plans, ensure_ascii=False, indent=2))
    else:
        for p in plans:
            print(f"[{p['repo']}] action={p['action']} target_tag={p['target_tag']} "
                  f"path={p['path'] or p['clone_url']} current={p['current_ref']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
