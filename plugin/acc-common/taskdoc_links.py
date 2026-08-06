"""任务书链接抽取与取材 —— 把任务书正文里的链接解析成**受控词表**分类，并有界地取回仓内材料。

Layer 1 确定性脚本（工具中立、可移植）：纯 stdlib（urllib/json/hashlib/re/base64），
不 import numpy/torch，不依赖任何 agent/CLI 框架，无算子身份分派。

为什么要单独一支：`fetch_source.py` 取的是「任务书正文 + PR 事实」两件已知落点的东西；
任务书正文里**还挂着一批链接**（自测用例目录、精度标准、模板、参考仓……），它们才是
「任务书自带用例集」这类材料的入口。这条通路的判据只看**链接结构**，不看链接指向哪个算子。

三条硬约束（与 AGENTS.md 5.1 / 5.8 同源）：
  · **分类是受控词表**（`KINDS`），认不出就是 `unknown`，不硬塞、不猜；
  · **状态是受控词表**（`STATUSES`）且**没有 `ok` 兜底**——每条链接的结局都必须落到一个
    有具体含义的格子里，「取到了」和「没取但登记了」和「取失败」在机器上就得分得开；
  · **可变 ref 先钉死成 commit sha**：任务书写的是 `master` 这种会漂的名字，先解析成
    commit sha，此后**所有**请求都 pin 这个 sha —— 否则同一轮取材的几个文件可能来自不同 commit，
    产物摘要就不再可复现。

用法::

    python3 taskdoc_links.py --taskdoc <url|path> --out <dir> [--exclude <substr>]...

产物 ``<out>/taskdoc_links.json``（schema 见 `SCHEMA`）+ 内容寻址落盘的取回文件
``<out>/taskdoc_links/<sha256前16>/<basename>``。

退出码：0 正常 / 2 有 blocking 状态（见 `BLOCKING_STATUSES`）/ 1 参数或 IO 错。
"""
import argparse
import base64
import hashlib
import json
import os
import posixpath
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import content_address

SCHEMA = "oprunway.taskdoc_links"
SCHEMA_VERSION = 1

API = "https://api.gitcode.com/api/v5"

# token 只加给这几个 host —— 任务书里可以出现**任意**外部链接，token 绝不能跟着飞出去。
_TOKEN_HOSTS = frozenset({"api.gitcode.com", "gitcode.com", "raw.gitcode.com"})
# 判「这条链接是不是 gitcode 仓内链接」用的 host 白名单（allowlist，不是 denylist）。
_GITCODE_LINK_HOSTS = frozenset({"gitcode.com", "www.gitcode.com", "raw.gitcode.com", "api.gitcode.com"})

# ── 受控词表 ────────────────────────────────────────────────────────────────────
# kind：**只看链接结构**得出（host + 路径段形态 / 是否相对路径），与链接指向什么内容无关。
KINDS = frozenset({
    "gitcode_blob",            # gitcode.com/<o>/<r>/blob/<ref>/<path>          单文件
    "gitcode_tree",            # gitcode.com/<o>/<r>/tree/<ref>[/<path>]        目录
    "gitcode_relative",        # 相对路径（相对任务书自身所在目录），需 base 才能解析
    "gitcode_repo_root",       # gitcode.com/<o>/<r>                            仓根
    "gitcode_merge_request",   # gitcode.com/<o>/<r>/(merge_requests|pull|pulls)/<n>
    "gitcode_discussion",      # gitcode.com/.../discussions/<n>
    "external",               # 非 gitcode host 的绝对链接
    "unknown",                # 认不出的形态（锚点、mailto、gitcode 但路径形态不认识……）
})

# status：**没有 `ok`**。每个格子都有具体含义，下游据此分开处理。
STATUSES = frozenset({
    "fetched",                       # 单文件已取回并内容寻址落盘
    "listed",                        # 目录已列出（子项另作条目登记）
    "explicitly_excluded",           # 命中调用方传入的排除清单（条目记 excluded_by）
    "unsupported_recorded",          # 认得出形态、但本工具无取材通路 → **登记但不阻断**
    "not_found",                     # HTTP 404
    "external_not_fetched",          # 非 gitcode host，按设计不取（防把 token/流量带出去）
    "unresolvable_relative_no_base",  # 相对链接但任务书没有仓内坐标（如本地文件）→ 不猜
    "depth_limited",                 # 目录递归超过 max_dir_depth
    "resource_limited",              # 触到 max_entries / max_file_bytes / max_total_bytes
    "http_error",                    # 非 200 且非 404（含网络失败，status=0）
})

# 哪些状态算 blocking（CLI 退出码 2）：**取材没拿到、且不是设计上就不取**的那些。
# `explicitly_excluded` / `unsupported_recorded` / `external_not_fetched` 是**明示的不取**，不阻断；
# `unresolvable_relative_no_base` 阻断，因为它意味着「任务书自带材料这条路根本没走通」——
# 调用方须改用带仓内坐标的任务书链接，而不是被静默降级。
BLOCKING_STATUSES = frozenset({
    "not_found", "http_error", "resource_limited",
    "depth_limited", "unresolvable_relative_no_base",
})

# 资源上限缺省值。取材是**有界**的：任务书可以挂任意多链接、目录可以任意深，
# 没有上限就等于把网络预算交给别人写的文档决定。
DEFAULT_LIMITS = {
    "max_entries": 200,                    # 最多登记/请求多少条链接条目（含目录展开出来的子项）
    "max_file_bytes": 2 * 1024 * 1024,     # 单文件上限
    "max_total_bytes": 16 * 1024 * 1024,   # 本轮累计落盘上限
    "max_dir_depth": 2,                    # 目录有界递归深度（顶层被列的目录记作深度 1）
    "timeout_seconds": 30,
}


class TaskdocLinksError(ValueError):
    """参数、任务书读取或产物写入层面的硬错误（CLI 退出码 1）。"""


# ── 任务书链接抽取 ──────────────────────────────────────────────────────────────
# markdown 行内链接：`[label](target)` 与图片 `![label](target)`。
# label 不允许含方括号（不处理嵌套 markdown；真出现嵌套就落不到这条正则上、退给裸 URL 扫描）。
# target 允许 `<...>` 包裹（CommonMark 写法），后面可跟 `"title"`。
_MD_LINK_RE = re.compile(
    r'(?P<bang>!?)\[(?P<label>[^\[\]]*)\]\(\s*(?P<target><[^<>]*>|[^()\s]*)\s*(?:"[^"]*")?\)')

# 裸 URL：字符集**只收 RFC 3986 允许出现在 URI 里的 ASCII**。
# 这样写而不是 `[^\s]+` 是有意的：任务书是中文文档，URL 后面常直接跟「（」「。」这类全角标点
# （实测 GaussianBlur 任务书第 7 行 `https://gitcode.com/cann/ops-cv（预计…`），
# 用 `\S+` 会把全角标点一起吞进 URL。
_BARE_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")

# 裸 URL 末尾要剥掉的「句读」字符：URI 语法允许它们出现，但落在句末时几乎必然是标点。
_TRAILING_PUNCT = ".,;:!?'\""
_TRAILING_CLOSERS = {")": "(", "]": "[", "}": "{"}

# 任务书自身若是 gitcode blob 链接，据此抽出仓内坐标（= 相对链接的 base）。
_BLOB_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<ref>[^/]+)/(?P<path>.+)$")


def _strip_bare_url_tail(url):
    """剥掉裸 URL 末尾的句读与**不配对**的右括号；返回剥完的 URL。"""
    while url:
        tail = url[-1]
        if tail in _TRAILING_PUNCT:
            url = url[:-1]
            continue
        if tail in _TRAILING_CLOSERS:
            opener = _TRAILING_CLOSERS[tail]
            # 只在右括号**多于**左括号时才剥：`…/foo_(bar)` 这种括号是 URL 的一部分。
            if url.count(tail) > url.count(opener):
                url = url[:-1]
                continue
        break
    return url


def extract_links(text):
    """逐行扫 markdown 行内链接与裸 URL → ``[{line,label,raw_target,form,in_code_fence}]``。

    两种形态都必须支持：任务书里既有 `[自测用例目录](./self_test_case/…)` 这种 markdown 链接，
    也有直接甩一条 `https://gitcode.com/cann/ops-cv/tree/master/gaussian_blur` 的裸 URL
    （GaussianBlur 任务书第 236 行就是后者）——只认一种就会漏掉真正的取材入口。

    `line` 从 1 起。`in_code_fence` 如实标注「这条链接是否落在 ``` / ~~~ 围栏代码块里」：
    **不据此过滤**（过滤就等于替用户判断哪条链接算数），只把事实交给下游。
    """
    if not isinstance(text, str):
        raise TaskdocLinksError("extract_links 需要 str 文本")
    out = []
    fence = None            # 当前围栏标记（``` 或 ~~~）；None 表示不在围栏内
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        in_fence = fence is not None
        masked = list(line)     # 已被 markdown 链接吃掉的区间，裸 URL 扫描时抹掉，避免重复登记
        for m in _MD_LINK_RE.finditer(line):
            target = m.group("target").strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1].strip()
            if not target:
                continue        # `[label]()` 空目标：没有可解析的东西，不登记
            out.append({
                "line": lineno,
                "label": m.group("label"),
                "raw_target": target,
                "form": "markdown_image" if m.group("bang") else "markdown",
                "in_code_fence": in_fence,
            })
            for i in range(m.start(), m.end()):
                masked[i] = "\x00"
        for m in _BARE_URL_RE.finditer("".join(masked)):
            url = _strip_bare_url_tail(m.group(0))
            if not url:
                continue
            out.append({
                "line": lineno,
                "label": "",
                "raw_target": url,
                "form": "bare",
                "in_code_fence": in_fence,
            })
    return out


# ── 分类（只看链接结构） ────────────────────────────────────────────────────────
def _host_of(url):
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def classify(raw_target, base=None):
    """把一条链接归到 `KINDS` 里的一个格子；**判据只有链接结构**。

    `base` 只用于说明「相对链接以谁为坐标」，**不影响分类**——相对链接无论有没有 base
    都是 `gitcode_relative`，「有没有 base」体现在 status 上（`unresolvable_relative_no_base`）。
    这样分才不会把「链接是什么」和「这一轮能不能取到」混成一件事。
    """
    target = (raw_target or "").strip()
    if not target:
        return "unknown"
    if target.startswith("#"):
        return "unknown"                       # 页内锚点，没有可取的材料
    scheme_m = re.match(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*):", target)
    if scheme_m:
        scheme = scheme_m.group("scheme").lower()
        if scheme not in ("http", "https"):
            return "unknown"                   # mailto:/ftp:/ 自定义 scheme —— 认得出不认识，不猜
        host = _host_of(target)
        if host not in _GITCODE_LINK_HOSTS:
            return "external"
        parsed = urllib.parse.urlparse(target)
        segs = [s for s in parsed.path.split("/") if s]
        if "discussions" in segs:
            return "gitcode_discussion"
        if len(segs) >= 5 and segs[2] == "blob":
            return "gitcode_blob"
        if len(segs) >= 4 and segs[2] == "tree":
            return "gitcode_tree"
        if len(segs) >= 4 and segs[2] in ("merge_requests", "pull", "pulls") and segs[3].isdigit():
            return "gitcode_merge_request"
        if len(segs) == 2:
            return "gitcode_repo_root"
        return "unknown"
    if "://" in target:
        return "unknown"                       # 形如 `//host/path` 的协议相对链接：不猜协议
    return "gitcode_relative"


def _parse_gitcode_url(raw_target, kind):
    """把已分类的 gitcode 绝对链接拆成 ``(owner, repo, ref, path)``；拆不出返回 None。

    ⚠ 已知边界：`blob/<ref>/<path>` 里的 `<ref>` 只取**第一段**。分支名含 `/`（如 `release/1.0`）时
    切分点会落错——这不是能靠正则解决的歧义（服务端才知道哪段是 ref）。切错的后果是钉 ref 时
    `commits/<ref>` 404 → 落 `not_found`（blocking），**不会**静默取到别的东西。
    """
    parsed = urllib.parse.urlparse(raw_target)
    segs = [urllib.parse.unquote(s) for s in parsed.path.split("/") if s]
    if kind == "gitcode_blob":
        return segs[0], segs[1], segs[3], "/".join(segs[4:])
    if kind == "gitcode_tree":
        return segs[0], segs[1], segs[3], "/".join(segs[4:])
    if kind == "gitcode_repo_root":
        return segs[0], segs[1], None, ""
    if kind == "gitcode_merge_request":
        return segs[0], segs[1], None, "/".join(segs[2:])
    return None


# ── HTTP（纯 stdlib） ───────────────────────────────────────────────────────────
def _token():
    """gitcode token：优先 $GITCODE_TOKEN，退回 $OPRUNWAY_GITCODE_TOKEN_FILE（默认 ~/.gitcode_token）。

    与 `fetch_source._token` 同口径。**token 不落盘、不进输出、不打印。**
    """
    tok = os.environ.get("GITCODE_TOKEN")
    if tok:
        return tok.strip()
    path = os.environ.get("OPRUNWAY_GITCODE_TOKEN_FILE", os.path.expanduser("~/.gitcode_token"))
    try:
        with open(path, encoding="utf-8") as src:
            return src.read().strip()
    except OSError:
        return None


def _get(url, params=None, timeout=30):
    """GET → ``(status, body_text 或 parsed_json)``；token 只对 gitcode host 加、经 query 传、不打印。

    与 `fetch_source._get` 同口径（有意保持一致，别让两条取材路各自漂移）。
    网络层失败返回 ``(0, 原因)``，由调用方落 `http_error`——不抛，因为「某一条链接取不到」
    不该把整轮取材炸掉，但**必须留下机读状态**。
    """
    merged = dict(params or {})
    tok = _token()
    host = _host_of(url)
    if tok and host in _TOKEN_HOSTS:
        merged.setdefault("access_token", tok)
    if merged:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(merged)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, (json.loads(body) if "json" in ctype else body)
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read().decode("utf-8", "replace")[:300]
    except (urllib.error.URLError, OSError, ValueError) as ex:
        return 0, str(ex)[:200]


def _contents(owner, repo, path, ref, timeout):
    """gitcode contents API。目录返回 list、文件返回 dict —— **按响应类型分流，不靠路径猜类型**。"""
    url = (API + "/repos/" + urllib.parse.quote(owner) + "/" + urllib.parse.quote(repo)
           + "/contents/" + urllib.parse.quote(path))
    return _get(url, {"ref": ref} if ref else None, timeout=timeout)


# ── 内容寻址落盘 ────────────────────────────────────────────────────────────────
def _atomic_write_bytes(root, rel, data):
    """在 root 内原子写字节；路径经 `content_address.safe_path` 约束（拒逃逸、拒软链段）。"""
    os.makedirs(root, exist_ok=True)
    target = content_address.safe_path(root, rel)
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    target = content_address.safe_path(root, rel)   # mkdir 后重查，防并发方换入软链
    fd, tmp = tempfile.mkstemp(prefix=".oprunway-tdlink-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as out:
            fd = -1
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        content_address.safe_path(root, rel)
        os.replace(tmp, target)
        tmp = None
    finally:
        if fd != -1:
            os.close(fd)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
    return target


def _safe_basename(path):
    """取仓内路径的末段作落盘文件名；形态不安全直接抛（不静默改名）。"""
    name = posixpath.basename(path.rstrip("/"))
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise TaskdocLinksError("仓内路径末段不可作文件名: " + repr(path))
    return name


# ── 取材主流程 ──────────────────────────────────────────────────────────────────
class _Budget:
    """本轮取材的资源账本 —— 条目数、单文件字节、累计字节都由它一处裁定。"""

    def __init__(self, limits):
        self.limits = limits
        self.entries = 0
        self.total_bytes = 0
        self.ref_pins = {}          # (owner, repo, declared_ref) -> commit sha，钉一次全轮复用

    def take_entry(self):
        """登记一条**需要网络动作**的条目；超额返回 False（调用方落 resource_limited）。"""
        if self.entries >= self.limits["max_entries"]:
            return False
        self.entries += 1
        return True

    def take_bytes(self, size):
        if size > self.limits["max_file_bytes"]:
            return False
        if self.total_bytes + size > self.limits["max_total_bytes"]:
            return False
        self.total_bytes += size
        return True


def pin_ref(owner, repo, ref, budget):
    """把可变 ref（`master`/`main`/tag）解析成 commit sha；同一 (owner,repo,ref) 只解析一次。

    这是本模块最要紧的一条不变量：任务书写的是会漂的名字，**取材一开始就钉死**，
    之后所有 contents 请求都带同一个 sha。不钉的话，一轮取材里几个文件可能来自不同 commit，
    产物摘要就不再对应任何一个真实快照（= 不可复现）。

    返回 ``(sha 或 None, note)``；解析不到时返回 None，由调用方落 `not_found`/`http_error`。
    """
    key = (owner, repo, ref)
    if key in budget.ref_pins:
        return budget.ref_pins[key], "cached"
    url = (API + "/repos/" + urllib.parse.quote(owner) + "/" + urllib.parse.quote(repo)
           + "/commits/" + urllib.parse.quote(ref))
    status, data = _get(url, timeout=budget.limits["timeout_seconds"])
    if status == 200 and isinstance(data, dict) and isinstance(data.get("sha"), str):
        sha = data["sha"]
        budget.ref_pins[key] = sha
        return sha, "resolved"
    if status == 404:
        return None, "not_found"
    return None, "http_error status=" + str(status)


def _match_exclude(entry, excludes):
    """排除清单：对 raw_target 与已解析的仓内路径做**子串**匹配；命中返回该子串。

    子串而非正则，是因为这份清单由调用方（编排层/人）逐条写死，语义要一眼可核；
    正则容易写出比预期宽的匹配，把该取的材料一起挡掉。
    """
    haystacks = [entry.get("raw_target") or ""]
    resolved = entry.get("resolved") or {}
    if resolved.get("path"):
        haystacks.append(str(resolved["owner"]) + "/" + str(resolved["repo"]) + "/" + str(resolved["path"]))
    for needle in excludes:
        if not needle:
            continue
        for hay in haystacks:
            if needle in hay:
                return needle
    return None


def _resolve_relative(raw_target, base):
    """相对链接 → 仓内绝对路径；逃出仓根返回 None（fail-closed，不猜）。"""
    target = raw_target.split("#", 1)[0].split("?", 1)[0]
    joined = posixpath.normpath(posixpath.join(base["dir"] or "", target))
    if joined in (".", ""):
        return ""
    if joined.startswith("../") or joined == ".." or joined.startswith("/"):
        return None
    return joined


def resolve_and_fetch(links, base, out_dir, excludes=(), limits=None, budget=None):
    """逐条解析并取材；返回**扁平**的条目列表（目录展开出来的子项也在同一个列表里）。

    取材规则：
      · `gitcode_blob` / 指向文件的 `gitcode_relative` → contents API 单文件 → `fetched`；
      · `gitcode_tree` / 指向目录的 `gitcode_relative` → contents API 列目录 → `listed`，
        子项作为**新条目**追加（文件登记成 `gitcode_blob`、子目录登记成 `gitcode_tree`），
        有界递归到 `max_dir_depth`（顶层被列的目录记作深度 1），再深一层落 `depth_limited`；
      · `external` → `external_not_fetched`（按设计不取：防把 token/流量带到任意外站）；
      · `gitcode_discussion` / `gitcode_repo_root` / `gitcode_merge_request` / `unknown`
        → `unsupported_recorded`，**登记但不阻断**（gitcode v5 REST 无 discussions 端点，
        网页是 SPA 空壳，取不到就是取不到——但它出现在任务书里这件事必须留档）；
      · 命中 `excludes` → `explicitly_excluded` + `excluded_by`。

    ⚠ 文件/目录**不按路径形态猜**（有没有尾斜杠、有没有扩展名都不算数），一律打 contents API，
    按响应是 list 还是 dict 分流——这是唯一不会猜错的判据。
    """
    limits = _merge_limits(limits)
    budget = budget if budget is not None else _Budget(limits)
    excludes = tuple(excludes or ())
    os.makedirs(out_dir, exist_ok=True)

    queue = []
    for raw in links:
        entry = dict(raw)
        entry["kind"] = classify(entry.get("raw_target"), base)
        entry["resolved"] = None
        entry["depth"] = 0
        queue.append(entry)

    done = []
    while queue:
        entry = queue.pop(0)
        # 已经定案的条目（例如目录展开时就落了 `depth_limited` 的子目录）直接收下，不再解析一遍。
        if not entry.get("status"):
            _process_entry(entry, base, out_dir, excludes, limits, budget, queue)
        done.append(entry)
    return done


def _finish(entry, status, **extra):
    """给条目落一个受控 status（顺带保证 schema 字段齐全）。"""
    if status not in STATUSES:
        raise TaskdocLinksError("非受控 status: " + repr(status))
    entry["status"] = status
    entry.setdefault("sha256", None)
    entry.setdefault("bytes", None)
    entry.setdefault("local_path", None)
    entry.setdefault("excluded_by", None)
    entry.setdefault("detail", None)
    for key, val in extra.items():
        entry[key] = val
    return entry


def _process_entry(entry, base, out_dir, excludes, limits, budget, queue):
    kind = entry["kind"]
    if kind == "external":
        return _finish(entry, "external_not_fetched",
                       detail="非 gitcode host，按设计不取（防 token/流量外带）")
    if kind in ("gitcode_discussion", "gitcode_repo_root", "gitcode_merge_request", "unknown"):
        return _finish(entry, "unsupported_recorded",
                       detail="kind=" + kind + " 无取材通路（登记但不阻断）",
                       resolved=_resolved_or_none(entry))

    # 先解析坐标，再判排除 —— 排除清单要能按**仓内路径**匹配（例如 `design_template`）。
    if entry.get("resolved") and entry["resolved"].get("ref"):
        # 目录展开出来的子项：坐标（含已钉死的 sha）在展开那一刻就定好了，
        # 它的 `raw_target` 是**仓内路径**而不是 URL，再走一遍 URL 解析必然拆错。
        pass
    elif kind == "gitcode_relative":
        if not base:
            return _finish(entry, "unresolvable_relative_no_base",
                           detail="任务书没有仓内坐标（本地文件或非 gitcode 链接）→ 相对链接不猜")
        rel = _resolve_relative(entry["raw_target"], base)
        if rel is None:
            return _finish(entry, "unsupported_recorded",
                           detail="相对路径逃出仓根，拒绝解析")
        entry["resolved"] = {"owner": base["owner"], "repo": base["repo"],
                             "ref": base["ref"], "path": rel}
    else:
        parts = _parse_gitcode_url(entry["raw_target"], kind)
        if not parts:
            return _finish(entry, "unsupported_recorded", detail="gitcode 链接形态拆不出仓内坐标")
        owner, repo, declared_ref, path = parts
        entry["resolved"] = {"owner": owner, "repo": repo, "ref": None, "path": path,
                             "ref_declared": declared_ref}

    hit = _match_exclude(entry, excludes)
    if hit is not None:
        return _finish(entry, "explicitly_excluded", excluded_by=hit)

    if not budget.take_entry():
        return _finish(entry, "resource_limited", detail="超出 max_entries=" + str(limits["max_entries"]))

    resolved = entry["resolved"]
    if resolved["ref"] is None:
        declared = resolved.get("ref_declared")
        if not declared:
            return _finish(entry, "unsupported_recorded", detail="链接里没有 ref，无法钉死 commit")
        sha, note = pin_ref(resolved["owner"], resolved["repo"], declared, budget)
        if sha is None:
            status = "not_found" if note == "not_found" else "http_error"
            return _finish(entry, status, detail="钉 ref 失败: " + declared + " (" + note + ")")
        resolved["ref"] = sha

    status, data = _contents(resolved["owner"], resolved["repo"], resolved["path"],
                             resolved["ref"], limits["timeout_seconds"])
    if status == 404:
        return _finish(entry, "not_found", detail="contents API 404")
    if status != 200:
        return _finish(entry, "http_error", detail="contents API status=" + str(status))

    if isinstance(data, list):
        return _list_dir(entry, data, limits, budget, queue)
    if isinstance(data, dict):
        return _fetch_file(entry, data, out_dir, limits, budget)
    return _finish(entry, "http_error", detail="contents API 返回形态不认识")


def _resolved_or_none(entry):
    """给 unsupported 条目尽量补一份坐标（拆得出就补，拆不出就 None）——纯留档，不用于取材。"""
    parts = _parse_gitcode_url(entry["raw_target"], entry["kind"]) if entry["kind"].startswith("gitcode_") else None
    if not parts:
        return None
    owner, repo, ref, path = parts
    return {"owner": owner, "repo": repo, "ref": None, "path": path, "ref_declared": ref}


def _list_dir(entry, items, limits, budget, queue):
    depth = entry["depth"] + 1
    children = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        itype = item.get("type")
        if not isinstance(name, str) or itype not in ("file", "dir"):
            continue                     # 受控词表：submodule/symlink 等一律不展开（fail-closed）
        child_path = posixpath.join(entry["resolved"]["path"], name) if entry["resolved"]["path"] else name
        child = {
            "line": entry["line"],
            "label": name,
            "raw_target": child_path,
            "form": "directory_child",
            "in_code_fence": entry.get("in_code_fence", False),
            "kind": "gitcode_blob" if itype == "file" else "gitcode_tree",
            "resolved": {"owner": entry["resolved"]["owner"], "repo": entry["resolved"]["repo"],
                         "ref": entry["resolved"]["ref"], "path": child_path},
            "depth": depth,
            "parent_path": entry["resolved"]["path"],
        }
        children += 1
        if itype == "dir" and depth >= limits["max_dir_depth"]:
            # 就地定案：`status` 一落，主循环就不会再解析它（不需要额外的包装类型）。
            _finish(child, "depth_limited",
                    detail="目录递归深度超过 max_dir_depth=" + str(limits["max_dir_depth"]))
        queue.append(child)
    return _finish(entry, "listed", child_count=children)


def _fetch_file(entry, data, out_dir, limits, budget):
    raw_b64 = data.get("content")
    declared_size = data.get("size")
    if isinstance(declared_size, int) and declared_size > limits["max_file_bytes"]:
        return _finish(entry, "resource_limited",
                       detail="单文件 " + str(declared_size) + " 字节超过 max_file_bytes="
                              + str(limits["max_file_bytes"]))
    if not isinstance(raw_b64, str):
        return _finish(entry, "http_error", detail="contents API 未返回 content 字段")
    try:
        payload = base64.b64decode(raw_b64)
    except (ValueError, TypeError) as ex:
        return _finish(entry, "http_error", detail="content base64 解码失败: " + str(ex)[:120])
    if not budget.take_bytes(len(payload)):
        return _finish(entry, "resource_limited",
                       detail="落盘预算耗尽（单文件 " + str(len(payload)) + " 字节）")
    digest = hashlib.sha256(payload).hexdigest()
    rel = os.path.join("taskdoc_links", digest[:16], _safe_basename(entry["resolved"]["path"]))
    _atomic_write_bytes(out_dir, rel, payload)
    return _finish(entry, "fetched", sha256=digest, bytes=len(payload),
                   local_path=rel.replace(os.sep, "/"))


def _merge_limits(limits):
    merged = dict(DEFAULT_LIMITS)
    for key, val in (limits or {}).items():
        if key not in DEFAULT_LIMITS:
            raise TaskdocLinksError("未知 limits 字段: " + repr(key))
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            raise TaskdocLinksError("limits." + key + " 须为正整数，得 " + repr(val))
        merged[key] = val
    return merged


# ── 任务书自身的读取与 base 派生 ────────────────────────────────────────────────
def read_taskdoc(src, budget):
    """读任务书原始字节 + 派生仓内坐标 base。

    · gitcode blob 链接 → contents API 取正文，并把 `<ref>` 钉成 commit sha 作 base.ref；
    · 其它 http(s) 链接 / 本地路径 → 只取正文，**base=None**（相对链接因此落
      `unresolvable_relative_no_base`，不猜任务书在哪个仓的哪个目录）。
    """
    if re.match(r"^https?://", src or ""):
        m = _BLOB_RE.match(src)
        if m and m.group("host").lower() in _GITCODE_LINK_HOSTS:
            owner, repo, ref, path = m.group("owner"), m.group("repo"), m.group("ref"), m.group("path")
            sha, note = pin_ref(owner, repo, ref, budget)
            if sha is None:
                raise TaskdocLinksError("任务书 ref 钉不死: " + owner + "/" + repo + "@" + ref + " (" + note + ")")
            status, data = _contents(owner, repo, path, sha, budget.limits["timeout_seconds"])
            if status != 200 or not isinstance(data, dict) or not isinstance(data.get("content"), str):
                raise TaskdocLinksError("取任务书失败（gitcode blob，HTTP " + str(status) + "）: " + src)
            raw = base64.b64decode(data["content"])
            base = {"owner": owner, "repo": repo, "ref": sha, "ref_declared": ref,
                    "dir": posixpath.dirname(path), "source": "gitcode_blob_url"}
            return raw, base
        status, body = _get(src, timeout=budget.limits["timeout_seconds"])
        if status != 200 or not isinstance(body, str):
            raise TaskdocLinksError("取任务书失败 HTTP " + str(status) + ": " + src)
        return body.encode("utf-8"), None
    try:
        with open(src, "rb") as fh:
            raw = fh.read()
    except OSError as ex:
        raise TaskdocLinksError("读任务书失败: " + repr(src) + ": " + str(ex)) from ex
    return raw, None


def build(taskdoc_src, out_dir, excludes=(), limits=None):
    """全流程：读任务书 → 抽链接 → 分类 → 取材 → 产 `taskdoc_links.json`。

    返回 ``(artifact, artifact_path)``。
    """
    limits = _merge_limits(limits)
    budget = _Budget(limits)
    os.makedirs(out_dir, exist_ok=True)
    raw, base = read_taskdoc(taskdoc_src, budget)
    try:
        text = raw.decode("utf-8")
    except UnicodeError as ex:
        raise TaskdocLinksError("任务书不是 UTF-8 文本: " + str(ex)) from ex
    taskdoc_sha256 = hashlib.sha256(raw).hexdigest()

    links = extract_links(text)
    entries = resolve_and_fetch(links, base, out_dir, excludes, limits, budget)

    artifact = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "taskdoc_source": taskdoc_src,
        "taskdoc_sha256": taskdoc_sha256,
        "resolved_commit_sha": (base or {}).get("ref"),
        "base": base,
        "excludes": list(excludes or ()),
        "limits": limits,
        "budget_used": {"entries": budget.entries, "total_bytes": budget.total_bytes},
        "links": [_public_entry(e) for e in entries],
    }
    artifact["status_counts"] = _status_counts(artifact["links"])
    artifact["blocking"] = sorted({e["status"] for e in artifact["links"]}
                                  & BLOCKING_STATUSES)
    path = content_address.atomic_write_json(out_dir, "taskdoc_links.json", artifact)
    return artifact, path


_PUBLIC_KEYS = ("line", "label", "raw_target", "form", "in_code_fence", "kind", "resolved",
                "status", "sha256", "bytes", "local_path", "excluded_by", "detail",
                "depth", "parent_path", "child_count")


def _public_entry(entry):
    out = {}
    for key in _PUBLIC_KEYS:
        if key in entry:
            out[key] = entry[key]
    for key in ("line", "label", "raw_target", "kind", "status"):
        if key not in out:
            raise TaskdocLinksError("条目缺必需字段 " + key + ": " + repr(entry)[:200])
    if out["kind"] not in KINDS:
        raise TaskdocLinksError("非受控 kind: " + repr(out["kind"]))
    return out


def _status_counts(entries):
    counts = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="任务书链接抽取与取材（受控词表分类 + 钉死 commit sha 的有界取材）")
    parser.add_argument("--taskdoc", required=True, help="任务书 URL（gitcode blob 才有 base）或本地路径")
    parser.add_argument("--out", required=True, help="产物目录")
    parser.add_argument("--exclude", action="append", default=[],
                        help="排除清单（子串匹配 raw_target 或 <owner>/<repo>/<path>），可重复")
    for key in ("max_entries", "max_file_bytes", "max_total_bytes", "max_dir_depth", "timeout_seconds"):
        parser.add_argument("--" + key.replace("_", "-"), type=int, default=None,
                            help="资源上限，缺省 " + str(DEFAULT_LIMITS[key]))
    args = parser.parse_args(argv)

    limits = {}
    for key in ("max_entries", "max_file_bytes", "max_total_bytes", "max_dir_depth", "timeout_seconds"):
        val = getattr(args, key)
        if val is not None:
            limits[key] = val
    try:
        artifact, path = build(args.taskdoc, args.out, tuple(args.exclude), limits)
    except (TaskdocLinksError, content_address.ContentAddressError, OSError) as ex:
        sys.stderr.write("[taskdoc_links] 失败: " + str(ex) + "\n")
        return 1
    sys.stderr.write("[taskdoc_links] 写出 " + path + "；状态分布 "
                     + json.dumps(artifact["status_counts"], ensure_ascii=False) + "\n")
    if artifact["blocking"]:
        sys.stderr.write("[taskdoc_links] blocking 状态: "
                         + ", ".join(artifact["blocking"]) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
