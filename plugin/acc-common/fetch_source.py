"""① 取材 — 把「任务书(md 路径或链接) + PR 链接」取成中立 JSON/文件，供 acc-spec skill 消费。

Layer 1 确定性脚本（工具中立、可移植）：纯 stdlib（urllib），无算子/仓目录硬编码
（GitCode API base 与常见分支 master/main 有默认值），无 Claude-Code 依赖。
gitcode token 走环境：优先 $GITCODE_TOKEN，退回 $OPRUNWAY_GITCODE_TOKEN_FILE 指向的文件（默认 ~/.gitcode_token）；
公开内容无 token 也尽量 raw 取。**token 不落盘、不进输出。**

用法:
  python3 fetch_source.py --taskdoc <path|url> [--pr <gitcode PR url>] --out <dir>
产出:
  <out>/task_doc.md      任务书原文（本地读或链接取）
  <out>/pr_facts.json    PR 事实（给了 --pr 才有）：op / 目标仓·目录 / base·head / changed_files /
                         关键文件内容（op 自带 example、op_def）——供 ② 抽 spec、③ 锚定 runner
说明：链接失败/无权限时不静默——task_doc 取不到直接报错；PR 部分字段取不到记进 pr_facts.notes。
"""
import argparse, json, os, re, sys, urllib.parse, urllib.request

API = "https://api.gitcode.com/api/v5"
_BLOB_RE = re.compile(r"^https?://gitcode\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<ref>[^/]+)/(?P<path>.+)$")
_PR_RE = re.compile(r"^https?://gitcode\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/"
                    r"(?:merge_requests|pulls)/(?P<num>\d+)")


def _token():
    t = os.environ.get("GITCODE_TOKEN")
    if t:
        return t.strip()
    f = os.environ.get("OPRUNWAY_GITCODE_TOKEN_FILE", os.path.expanduser("~/.gitcode_token"))
    try:
        return open(f, encoding="utf-8").read().strip()
    except OSError:
        return None


_GITCODE_HOSTS = ("api.gitcode.com", "gitcode.com", "raw.gitcode.com")


def _get(url, params=None, timeout=30):
    """GET，返回 (status, body_text 或 parsed_json)。token 只对 gitcode host 加、经 query 传、不打印。"""
    p = dict(params or {})
    tok = _token()
    host = urllib.parse.urlparse(url).hostname or ""
    if tok and host in _GITCODE_HOSTS:  # 只给 gitcode 加 token，防泄漏到任意（非 gitcode）任务书链接
        p.setdefault("access_token", tok)
    if p:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(p)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            ct = r.headers.get("Content-Type", "")
            return r.status, (json.loads(body) if "json" in ct else body)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(e)[:200]


def _repo_file(owner, repo, path, ref=None):
    """取仓内单文件内容（gitcode contents API，base64）→ 文本；失败返回 None。"""
    st, d = _get(f"{API}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/contents/"
                 f"{urllib.parse.quote(path)}", {"ref": ref} if ref else None)
    if st == 200 and isinstance(d, dict) and d.get("content"):
        import base64
        try:
            return base64.b64decode(d["content"]).decode("utf-8", "replace")
        except (ValueError, TypeError):
            return None
    return None


def fetch_taskdoc(src, out_dir):
    """任务书：本地路径直接读；http(s) 链接取（gitcode blob → contents API；其它 → 直接 GET）。"""
    if re.match(r"^https?://", src):
        m = _BLOB_RE.match(src)
        if m:  # gitcode blob 链接 → contents API（可带 token 取私有）
            txt = _repo_file(m["owner"], m["repo"], m["path"], m["ref"])
            if txt is None:
                raise RuntimeError(f"取任务书失败（gitcode blob）：{src}")
        else:  # 其它链接（含 raw）直接 GET
            st, body = _get(src)
            if st != 200 or not isinstance(body, str):
                raise RuntimeError(f"取任务书失败 HTTP {st}：{src}")
            txt = body
    else:
        with open(src, encoding="utf-8") as f:
            txt = f.read()
    dst = os.path.join(out_dir, "task_doc.md")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(txt)
    return dst


def _guess_op(paths):
    """从改动文件路径猜算子 snake 名 + 目标目录（experimental/math/<op> 或 math/<op> 等）。"""
    for p in paths:
        m = re.search(r"((?:experimental/)?[a-z_]+/)([a-z0-9_]+)/(?:op_host|op_kernel|op_api|examples)/", p)
        if m:
            return m.group(2), m.group(1) + m.group(2)
    return None, None


def fetch_pr(pr_url, out_dir):
    """PR：解析 gitcode PR 链接 → API 取 元信息 + 改动文件 + 关键文件（example/op_def），写 pr_facts.json。"""
    m = _PR_RE.match(pr_url)
    facts = {"pr_url": pr_url, "notes": []}
    if not m:
        facts["notes"].append("PR 链接非 gitcode pulls/merge_requests 格式，未解析")
        return _dump_facts(facts, out_dir)
    owner, repo, num = m["owner"], m["repo"], m["num"]
    facts["source_repo"] = f"{owner}/{repo}"
    st, pr = _get(f"{API}/repos/{owner}/{repo}/pulls/{num}")
    if st == 200 and isinstance(pr, dict):
        facts["title"] = pr.get("title")
        facts["state"] = pr.get("state")
        facts["base"] = (pr.get("base") or {}).get("ref")
        facts["head"] = (pr.get("head") or {}).get("ref")
        facts["merged"] = pr.get("merged") if "merged" in pr else (pr.get("state") == "merged")
    else:
        facts["notes"].append(f"取 PR 元信息失败 HTTP {st}")
    st, files = _get(f"{API}/repos/{owner}/{repo}/pulls/{num}/files")
    paths = [f.get("filename") for f in files if isinstance(f, dict)] if isinstance(files, list) else []
    facts["changed_files"] = paths
    if not paths:
        facts["notes"].append("未取到改动文件列表（op/example 需人工或 --pr 换取）")
    op, target_dir = _guess_op(paths)
    facts["op"], facts["target_dir"] = op, target_dir
    # 关键文件：op 自带 example（runner 锚定用）+ op_def（支持 dtype）
    # merged PR 的 head 分支常被删 → 按 head→base→master→main 多 ref 兜底
    refs = [r for r in [facts.get("head"), facts.get("base"), "master", "main"] if r]

    def _grab(rel):
        for r in refs:
            c = _repo_file(owner, repo, rel, r)
            if c:
                return c, r
        return None, None

    key, key_ref = {}, {}
    if target_dir:
        want = ([p for p in paths if "/examples/" in p and p.endswith(".cpp")][:6]
                + [p for p in paths if p.endswith("_def.cpp") or "/op_host/" in p][:4])
        for rel in want:
            c, r = _grab(rel)
            if c:
                key[rel], key_ref[rel] = c, r
    facts["key_files"] = key
    facts["key_files_ref"] = key_ref  # 每个关键文件实际取自哪个 ref（供下游判新鲜度）
    stale = [rel for rel, r in key_ref.items() if facts.get("head") and r != facts["head"]]
    if stale:
        facts["notes"].append(f"{len(stale)} 个关键文件非取自 PR head（head 分支已删/为 fork/open PR）"
                              f"，兜底取自 base/master——内容可能与 PR 实际 head 有差，请核")
    if not key:
        facts["notes"].append("未取到 example/op_def 关键文件内容（runner 锚定需另取）")
    return _dump_facts(facts, out_dir)


def _dump_facts(facts, out_dir):
    dst = os.path.join(out_dir, "pr_facts.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    return dst


def main(argv):
    ap = argparse.ArgumentParser(description="① 取材：任务书(md/链接) + PR(链接) → 中立 JSON/文件")
    ap.add_argument("--taskdoc", required=True, help="任务书 md 本地路径 或 http(s) 链接")
    ap.add_argument("--pr", default=None, help="gitcode PR 链接（可选）")
    ap.add_argument("--out", required=True, help="产出目录")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    td = fetch_taskdoc(a.taskdoc, a.out)
    print(f"[fetch] 任务书 → {td}")
    if a.pr:
        pf = fetch_pr(a.pr, a.out)
        facts = json.load(open(pf, encoding="utf-8"))
        print(f"[fetch] PR → {pf}  op={facts.get('op')} 目录={facts.get('target_dir')} "
              f"改动{len(facts.get('changed_files', []))}文件 关键{len(facts.get('key_files', {}))}份")
        for n in facts.get("notes", []):
            print(f"  ⚠ {n}")


if __name__ == "__main__":
    main(sys.argv[1:])
