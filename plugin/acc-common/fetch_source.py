"""① 取材 — 把「任务书 + 被测代码」取成中立 JSON/文件，供 acc-spec skill 消费。

被测代码有**两条平级来源通路**，由 `dut_source` 判别式区分，绝不互相伪装：

  · `pull_request`（`--pr`）  ：在线 gitcode PR 链接 → API 取元信息/改动文件/关键文件；
  · `local_checkout`（`--local-repo`）：本地已 clone 的代码仓目录 → 直接读盘。

两条都是一等通路。任务书本来就同时收本地路径与 http(s) 链接，于是「在线给 / 本地给」
四种组合都成立。**本地事实不塞进 `payload.pr`**：一个叫 `pr` 的字段里装本地 checkout 数据，
下游、报告、人读收据时都会把弱 provenance 误当成 PR provenance。

Layer 1 确定性脚本（工具中立、可移植）：纯 stdlib（urllib），无算子/仓目录硬编码
（GitCode API base 与常见分支 master/main 有默认值），无 Claude-Code 依赖。
gitcode token 走环境：优先 $GITCODE_TOKEN，退回 $OPRUNWAY_GITCODE_TOKEN_FILE 指向的文件（默认 ~/.gitcode_token）；
公开内容无 token 也尽量 raw 取。**token 不落盘、不进输出。**

用法:
  python3 fetch_source.py --taskdoc <path|url> --pr <gitcode PR url> --out <dir>
  python3 fetch_source.py --taskdoc <path|url> --local-repo <dir> --op-subdir <rel> \
                          [--base-ref <ref>] [--allow-dirty] --out <dir>
产出:
  <out>/task_doc.md      任务书原文（本地读或链接取）
  <out>/task_doc.snapshot.md 与原文逐字节相同的稳定引文锚（CP-A 即落，供 spec/golden 共用 SHA）
  <out>/pr_facts.json    被测代码事实（给了 --pr 或 --local-repo 才有）：op / 目标目录 /
                         changed_files / 关键文件内容（op 自带 example、op_def）——
                         供 ② 抽 spec、③ 锚定 runner。**文件名两条通路共用**（减少消费者改动），
                         内部靠 `dut_source` 判别；PR 通路另有 base·head，本地通路另有 local_checkout
  <out>/source_facts.json 内容寻址事实索引（同上条件）：任务书字节、PR head 或本地子树摘要、
                          关键文件 ref/摘要、接口派生事实与完整性状态——供非真机断点复用，
                          不含 token/关键文件正文
说明：链接失败/无权限时不静默——task_doc 取不到直接报错；PR 链接**形态不认识→直接报错（fail-loud，属用户输入错）、不产空壳**；
      PR 链接认识但字段取不到（网络/权限）→记进 pr_facts.notes 继续（属环境问题，与「URL 写错」错误信息分开）。
      `--pr` 与 `--local-repo` 互斥，同给 → 在任何文件读写之前 fail-loud。
"""
import argparse, hashlib, json, os, re, subprocess, sys, tempfile, urllib.parse, urllib.request

import content_address
import dut_source

# ---- DUT 来源判别式（受控词表在 `dut_source.py`，读侧唯一入口）------------------
# ⚠ 缺省是 `pull_request`：既有 PR 通路的 payload **不写这个键**，业务字段逐字节不变
# （唯一会变的是 `producer.logic_sha256`——那是工具自身源码的哈希，改工具必变，属设计）。
DUT_SOURCE_PR = dut_source.PULL_REQUEST
DUT_SOURCE_LOCAL = dut_source.LOCAL_CHECKOUT
DUT_SOURCES = dut_source.ALL

# ---- root_digest 的算法标识与排除规则 -------------------------------------------
# ⚠ 收据里记的**不是**一串看起来像 glob 的字符串（`"build/"` / `"*.pyc"` 那种写法会让读收据的人
# 以为是 glob 或前缀匹配，而实现是「任一路径段等名」——写法与语义不一致本身就是缺陷）。
# 改为**结构化 + 版本化**规则，校验端按受控值逐字核对，不接受任意排除策略：
#   · `excluded_segment_names` —— 相对路径的**任一路径段**等于该名字即排除
#     （`__pycache__` 每层都有，只排首段等于没排）；
#   · `excluded_basename_suffixes` —— basename 以该后缀结尾即排除。
DIGEST_ALGORITHM = "oprunway.local_subtree_merkle"
DIGEST_ALGORITHM_VERSION = 1
DEFAULT_DIGEST_EXCLUDE_DIRS = (".git", "__pycache__", "build", "build_out")
DEFAULT_DIGEST_EXCLUDE_SUFFIXES = (".pyc",)


def digest_policy(exclude_dirs=DEFAULT_DIGEST_EXCLUDE_DIRS,
                  exclude_suffixes=DEFAULT_DIGEST_EXCLUDE_SUFFIXES):
    """收据里那份**机器可核**的摘要策略。校验端按它逐字核对，不认「非空即可」。"""
    return {
        "algorithm": DIGEST_ALGORITHM,
        "algorithm_version": DIGEST_ALGORITHM_VERSION,
        "excluded_segment_names": sorted(exclude_dirs),
        "excluded_basename_suffixes": sorted(exclude_suffixes),
    }


# ---- completeness.warnings 受控词表 ---------------------------------------------
# ⚠ 必须是受控词表 + 事实一致性规则，不能「是字符串就收下」：否则把任意**阻塞**原因写成
# warning，再配上 `status=complete, reasons=[]`，就能让一份降级的来源以干净 pass 通过准备门。
WARN_CHANGED_FILES_UNAVAILABLE = "changed_files_unavailable"
WARN_DIRTY_WORKTREE_ALLOWED = "dirty_worktree_allowed"
SOURCE_WARNINGS = (WARN_CHANGED_FILES_UNAVAILABLE, WARN_DIRTY_WORKTREE_ALLOWED)

API = "https://api.gitcode.com/api/v5"
_BLOB_RE = re.compile(r"^https?://gitcode\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<ref>[^/]+)/(?P<path>.+)$")
# PR 链接三段抽取：容错 GitHub 风格单数 /pull/N、复数 /pulls/N、GitCode 原生 /merge_requests/N，
# 统一抽 owner/repo/编号（编号即 merge_request 号）。
# ⚠ 末尾必须是路径分隔符 / query / fragment / 串尾——**不能只用 `\b`**：`\d+\b` 在 `/pull/12-foo`、
# `/pull/12.xyz` 处也成立（数字与 `-`/`.` 之间有词边界），会把畸形 URL 当成 PR 12 放行 = fail-open。
_PR_RE = re.compile(r"^https?://gitcode\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/"
                    r"(?:merge_requests|pulls?)/(?P<num>\d+)(?=[/?#]|$)")


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


def _taskdoc_bytes(src):
    """读取任务书原始字节；本地文件绝不经过 universal-newline 文本层。"""
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
        return txt.encode("utf-8")
    else:
        with open(src, "rb") as f:
            raw = f.read()
        # 保持旧入口对本地任务书的 UTF-8 要求，但验证与落盘分离，避免 CRLF 被文本层改写。
        raw.decode("utf-8")
        return raw


def _assert_snapshot_compatible(raw, snapshot_path):
    """写任何新 CP-A 工件前先核既有快照，避免任务书改版留下半更新目录。"""
    if not snapshot_path or not os.path.exists(snapshot_path):
        return
    if os.path.islink(snapshot_path):
        raise RuntimeError(f"任务书快照不得是符号链接：{snapshot_path}")
    with open(snapshot_path, "rb") as src:
        old = src.read()
    old_digest = hashlib.sha256(old).hexdigest()
    new_digest = hashlib.sha256(raw).hexdigest()
    if old_digest != new_digest:
        raise RuntimeError(
            f"任务书快照已存在但内容与本次取到的原文不一致：{snapshot_path}\n"
            f"  既有快照 sha256: {old_digest}\n"
            f"  本次取到 sha256: {new_digest}\n"
            "  → 为避免 task_doc/source_facts 与旧引文锚混成半更新目录，本次未写任何新任务书工件；"
            "请另用新输出目录，或人工复核引用后显式移除旧快照再重跑。")


def _atomic_write_bytes(path, raw):
    parent = os.path.abspath(os.path.dirname(path) or ".")
    os.makedirs(parent, exist_ok=True)
    target = content_address.safe_path(parent, os.path.basename(path))
    fd, tmp = tempfile.mkstemp(prefix=".oprunway-taskdoc-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as out:
            fd = -1
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        content_address.safe_path(parent, os.path.basename(path))
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


def fetch_taskdoc(src, out_dir, extra_snapshot_paths=()):
    """任务书：取原始字节，先核所有既有快照，再原子写 task_doc.md。"""
    raw = _taskdoc_bytes(src)
    work_snapshot = os.path.join(out_dir, "task_doc.snapshot.md")
    for snapshot_path in (work_snapshot, *tuple(extra_snapshot_paths)):
        _assert_snapshot_compatible(raw, snapshot_path)
    dst = os.path.join(out_dir, "task_doc.md")
    _atomic_write_bytes(dst, raw)
    return dst


def _guess_op(paths):
    """从改动文件路径猜算子 snake 名 + 目标目录（experimental/math/<op> 或 math/<op> 等）。"""
    for p in paths:
        m = re.search(r"((?:experimental/)?[a-z_]+/)([a-z0-9_]+)/(?:op_host|op_kernel|op_api|examples)/", p)
        if m:
            return m.group(2), m.group(1) + m.group(2)
    return None, None


# aclnn 对外接口头：文件名形态 `aclnn_<...>.h`（**不预设它在算子目录下的哪一层**）。
# `*_impl.h` 是内部实现头、不是对外两段式接口 → 剔除（与 `aclnn_adapter.find_aclnn_project` 同口径）。
_ACLNN_HDR_RE = re.compile(r"(?:^|/)aclnn_[A-Za-z0-9_]+\.h$")


def _aclnn_headers(paths, target_dir):
    """改动文件里、**目标算子目录下**的 aclnn 接口头（剔 `*_impl.h`）→ **一等 key_file，不受任何截断**。

    为什么必须一等（2026-07-24 median PR6429 dogfood 实测逼出的修）：`aclnn_*.h` 是 aclnn 路由的
    **第一依据**——真符号名、形参顺序与类型、arity、几个输出，全在这份头里；acc-spec 的
    `call_variants` / `params[].out_role` / runner 的 slots↔签名逐项对账都只认它。
    旧实现把它混进「`_def.cpp` 或 `/op_host/` 下的文件」那一档、再 `[:4]` 截断 → 算子目录下
    op_host 文件一多就把接口头**挤掉**（PR6429 的头正是 `<op_subdir>/op_host/op_api/aclnn_median.h`），
    下游只能凭 example 的调用写法或算子名去猜符号——那正是「验的不是 PR、是 CANN 内置同名实现」的路。

    口径（**通用、按结构判，不按算子身份**）：
      · 只收 `target_dir`（= `_guess_op` 判出的算子目录）**之下**的路径——别把同 PR 里别的算子的头也收进来；
      · **不预设目录层级**：`op_api/`、`op_host/op_api/`、`op_api/include/` 等落点都算数
        （与 `aclnn_adapter.find_aclnn_project` 的有界递归口径一致；钉死一层会把真 PR 判成「非域内」）；
      · 剔 `*_impl.h`（内部实现头）；
      · **有意不设条数上限**：条数已被「文件名形态 × 单个算子目录」限死、天然只有一两份，
        再加一道截断就是把刚修好的洞原样挖回来。
    """
    pref = target_dir.rstrip("/") + "/"
    out = []
    for p in paths:
        s = str(p)
        if not s.startswith(pref) or s.endswith("_impl.h"):
            continue
        if _ACLNN_HDR_RE.search(s) and s not in out:
            out.append(s)
    return out


def _parse_pr_url(pr_url):
    """解析 gitcode PR 链接 → (owner, repo, num)。

    容错三种路径写法，统一抽 owner/repo/编号（编号即 GitCode 的 merge_request 号）：
      - GitCode 原生   /merge_requests/<编号>
      - GitHub 风格单数 /pull/<编号>（用户常按 GitHub 习惯粘这个 → 内部规范化为 merge_request 编号）
      - 复数           /pulls/<编号>
    形态不认识（host 非 gitcode.com / owner·repo·编号三段不全 / 编号非数字）→ 抛 ValueError
    （fail-loud，附可操作中文提示）。调用方据此明确失败、**绝不产空壳 pr_facts 往下传**。
    ⚠ 这只判「URL 形态」，不碰网络；能否真取到数据是另一回事（网络/token 失败在 fetch_pr 里记 notes）。"""
    m = _PR_RE.match((pr_url or "").strip())
    if not m:
        raise ValueError(
            f"无法解析 PR 链接：{pr_url!r}\n"
            "  期望形态：https://gitcode.com/<owner>/<repo>/merge_requests/<编号>\n"
            "  亦接受 GitHub 风格路径 /pull/<编号> 或 /pulls/<编号>（内部规范化为 merge_requests 编号）。\n"
            "  请检查：协议+host 是否为 http(s)://gitcode.com、owner/repo/编号三段是否齐全、编号为纯数字。"
        )
    return m["owner"], m["repo"], m["num"]


_ACLNN_WS_RE = re.compile(r"\baclnn(\w+)GetWorkspaceSize\s*\(")
_HCCL_RE = re.compile(r'hccl/hccl\.h|HcclComm|HcclGetCommName|\brankId\b')
_GEIR_RE = re.compile(r"ge::Session|->\s*AddGraph\s*\(|->\s*RunGraph\s*\(")


def _detect_interface_kind(key_files):
    """据 `pr_facts.key_files` 的迹象**机器判**算子接口形态（批 6b B-core）。

    返回 `(interface_kind, aclnn_entry|None, note)`。规则据实 clone 的 4 仓（ops-nn/transformer/
    collections/solver）分类得出（workflow wf_b07a40d8），落到「文件存在性 + 内容正则」的可判组合：

      - `aclnn_2stage`：某 `test_aclnn_*.cpp`（或 examples/*.cpp）里命中 `aclnn<X>GetWorkspaceSize(`
        且有配对的第二段 `aclnn<X>(… executor …)`，且**不含 HCCL**。→ 当前通路可放行的接口形态
        （逐算子仍须过 dtype∈{fp32,fp16} + golden 可搭 子闸，不在此判）。
      - `aclnn_2stage_distributed`：命中 aclnn 两段式**但含 HCCL 多卡通信**（MC2 族）→ 出单卡通路，BLOCKED-另立。
      - `library_header`：**零** test_aclnn / 零 op_def → handle 型 C 库（ops-solver aclsolver*）/ 纯头文件
        模板库（ops-collections）→ 非 aclnn 通路，BLOCKED-另立。
      - `unknown`：有 op_def 迹象但探不到确切 aclnn 两段式配对 → **fail-closed**，BLOCKED，不猜。

    ⚠ **aclnn 入口函数名从 test_aclnn 正则抽真实名**（`aclnnPromptFlashAttentionV3` 这类带版本后缀，
       ≠ 目录名派生的 `aclnn<Op>`）——供 runner 锚定，别再按 op 名猜（Equal 血教训 + transformer 实测 V3/V5）。
    ⚠ 探测**只用取到的 key_files**：取不到（网络/无 PR）→ `unknown`/`library_header`，下游 fail-closed，不假装是 aclnn。"""
    kf = key_files or {}
    # 先去 C/C++ 注释：注释掉的 aclnn 调用不算（codex 审：`// aclnnFooGetWorkspaceSize(...)` 曾被误判成 aclnn）。
    def _nc(c):
        c = re.sub(r"/\*.*?\*/", " ", c or "", flags=re.S)
        return re.sub(r"//[^\n]*", " ", c)
    examples = {p: _nc(c) for p, c in kf.items()
                if str(p).endswith(".cpp") and ("test_aclnn" in os.path.basename(str(p)) or "/examples/" in str(p))}
    # HCCL 跨**所有** key_files 查（codex 审加固）：MC2 算子的 `hccl/hccl.h` include 可能落在辅助文件、
    # 不在命中 aclnn 的那个 → 只查单文件会把分布式漏判成单卡 aclnn。跨文件查 = fail-closed 方向。
    _any_hccl = any(_HCCL_RE.search(_nc(c)) for c in kf.values())
    for p, c in examples.items():
        m = _ACLNN_WS_RE.search(c)
        if not m:
            continue
        # ⚠ 已知边界（codex 审）：多入口 example（如量化 matmul 先调 `aclnnTransQuantParamV2GetWorkspaceSize`
        #   再调主入口 `aclnnQuantMatmulV3`）抽到的是**第一个** WS 调用、可能是辅助入口——`aclnn_entry`
        #   仅作 runner 锚定的**线索**，gen_runner 仍须读 example 确认真入口（不改分类：还是 aclnn_2stage）。
        #   期1-A 放行的 6 个均单入口，不受影响。
        entry = "aclnn" + m.group(1)
        # 第二段配对：把所有 `aclnn*GetWorkspaceSize(` 调用抹掉后，仍能找到同名 `aclnn<Entry>(`
        # —— 即两段式的执行段。不认参数名（executor/exe 变量名可变、脆弱），只认「同名函数被调两次、
        # 其一非 GetWorkspaceSize」。
        _exec_seg = re.sub(r"\baclnn\w+GetWorkspaceSize\s*\(", "", c)
        if not re.search(r"\b" + re.escape(entry) + r"\s*\(", _exec_seg):
            continue
        if _any_hccl:
            return ("aclnn_2stage_distributed", entry,
                    f"aclnn 两段式但含 HCCL 多卡通信（{entry}，MC2 族）→ 出单卡单进程通路，BLOCKED-另立")
        return ("aclnn_2stage", entry,
                f"aclnn 两段式，入口 {entry}（从 test_aclnn 正则抽真实函数名、非目录名派生）；"
                f"逐算子仍须过 dtype∈{{fp32,fp16}} + golden 可搭 子闸")
    # geir 图引擎示例（`op::X` + `ge::Session` + AddGraph/RunGraph，如 ops-nn 的 celu/bnll 用 test_geir_*.cpp）——
    # 显式识别给准 note（否则笼统落 unknown）。同样 BLOCKED-另立：图引擎构建路径，非 aclnn 两段式。
    # 批 6b B-core 逐算子核暴露：ops-nn 不是清一色 aclnn，混有 geir 算子（探测器对它们本就 fail-closed，此处只给更准的类别）。
    if any(("test_geir" in os.path.basename(str(p))) or _GEIR_RE.search(c) for p, c in examples.items()):
        return ("geir", None,
                "GE IR 图引擎示例（op::X + ge::Session + AddGraph/RunGraph）→ 非 aclnn 两段式，BLOCKED-另立（图引擎构建路径）")
    has_def = any(str(p).endswith("_def.cpp") for p in kf)
    if not examples and not has_def:
        return ("library_header", None,
                "无 test_aclnn / 无 op_def → handle 型 C 库或纯头文件模板库（非 aclnn 两段式通路），BLOCKED-另立")
    return ("unknown", None,
            "有 op_def 迹象但探不到确切的 aclnn 两段式配对 → fail-closed，BLOCKED，不猜")


def fetch_pr(pr_url, out_dir):
    """PR：解析 gitcode PR 链接 → API 取 元信息 + 改动文件 + 关键文件（example/op_def），写 pr_facts.json。

    两种失败严格区分：
      · URL 形态不认识 → `_parse_pr_url` 抛 ValueError（fail-loud，属用户输入错），**在任何网络调用之前**中止、不落 pr_facts.json；
      · URL 认识但网络/token 取不到字段 → 不抛，记进 facts["notes"] 继续（属环境问题，错误信息与「URL 写错」不同，别让用户误改 URL）。"""
    owner, repo, num = _parse_pr_url(pr_url)  # 形态错 → 抛出（fail-loud），不产空壳
    facts = {"pr_url": pr_url, "notes": [], "source_repo": f"{owner}/{repo}"}
    st, pr = _get(f"{API}/repos/{owner}/{repo}/pulls/{num}")
    if st == 200 and isinstance(pr, dict):
        facts["title"] = pr.get("title")
        facts["state"] = pr.get("state")
        facts["base"] = (pr.get("base") or {}).get("ref")
        facts["head"] = (pr.get("head") or {}).get("ref")
        # U5：**被测对象 = PR head 那个 commit**，钉 sha 而非分支名。分支名不可靠有两个实测理由：
        #   ① merged PR 的 head 分支常被删；
        #   ② open PR 的 head 多在**贡献者 fork** 上，且 head.ref 可能字面就叫 "master"
        #      （实测 cann/ops-math MR 3400：head.repo=<fork>、head.ref="master"）——
        #      按分支名去 base 仓取会**静默取到 base 仓的 master**（实测 sha e16a230c ≠ head 9b494b2d），
        #      拿到完全不相干的代码却报告「取自 PR head」。
        # 实测结论（2026-07-22，真打 gitcode API）：**fork 的 head sha 可直接从 base 仓解析**
        #   （`contents?ref=<head_sha>` 对 base 仓 HTTP 200），故不需特判 fork 仓。
        facts["head_sha"] = (pr.get("head") or {}).get("sha")
        facts["head_repo"] = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
        # is_fork：**不知道就是 None，别默认「同仓」**（unknown 当成同仓会让下游少一层警觉）；
        # 比较前两边同样规范化（大小写/首尾空白），否则 Cann/Ops-Math 会被误判成 fork。
        _hr = (facts["head_repo"] or "").strip().casefold()
        facts["is_fork"] = (_hr != f"{owner}/{repo}".strip().casefold()) if _hr else None
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
    # ⚠ **只按 head_sha 取，不再按分支名兜底**（U5，2026-07-22 实测后收紧）。
    #   旧兜底 `head→base→master→main` 是**静默取错代码**的路：open PR 的 head.ref 可能字面叫 "master"，
    #   拿它去 base 仓会取到 base 的 master（实测两者 sha 不同），却仍被记成「取自 PR head」。
    #   宁可取不到（下游据 notes 判断），也不拿一份来源不明的代码冒充被测对象。
    head_sha = facts.get("head_sha")
    refs = [head_sha] if head_sha else []
    if not head_sha:
        # ⚠ 不能只记 note 就照常返回——下游（CP-A / acc-spec）没有机器硬门查 head_sha，
        # 「照常返回」等于让它带着无法溯源的取材继续抽 spec = fail-open。给一个**机读**的阻断状态。
        facts["blocked"] = "missing_head_sha"
        facts["notes"].append(
            "PR 元信息里没有 head.sha → **无法钉死被测 commit**，关键文件一律不取"
            "（不按分支名兜底：那会静默取到 base 仓同名分支的代码、与 PR 实际内容无关）。"
            "已置 blocked='missing_head_sha'：编排层须停下，**不得据此往下抽 spec / 产 runner**。")

    # 取仓顺序：base 仓优先，**404 时用同一个 sha 退到 head_repo**。
    # ⚠ 「fork 的 sha 一定能从 base 仓解析」只在 2026-07-22 实测的两个 PR 上观察到，
    #   **不是平台保证**——不能据此断定所有仓/所有 fork commit 都可达。退一层是廉价的保险，
    #   且因为**用的仍是同一个 sha**，不会重新引入「按分支名取错代码」的风险。
    _repos = [(owner, repo)]
    _hr = facts.get("head_repo")
    if _hr and "/" in _hr and _hr.strip().casefold() != f"{owner}/{repo}".strip().casefold():
        _repos.append(tuple(_hr.split("/", 1)))

    def _grab(rel):
        for r in refs:
            for o2, r2 in _repos:
                c = _repo_file(o2, r2, rel, r)
                if c:
                    return c, r
        return None, None

    key, key_ref = {}, {}
    hdrs = []
    if target_dir:
        # ⚠ 顺序即优先级，且 **aclnn 接口头不进任何截断档**（见 `_aclnn_headers` 的理由）。
        # 后两档仍各自设上限（防某些 PR 改上百个文件时把请求数打爆）。
        hdrs = _aclnn_headers(paths, target_dir)
        want = (hdrs
                + [p for p in paths if "/examples/" in p and p.endswith(".cpp")][:6]
                + [p for p in paths if p.endswith("_def.cpp") or "/op_host/" in p][:4])
        for rel in dict.fromkeys(want):     # 去重保序：接口头也落在 `/op_host/` 档里，别重复请求
            c, r = _grab(rel)
            if c:
                key[rel], key_ref[rel] = c, r
    facts["key_files"] = key
    facts["key_files_ref"] = key_ref  # 每个关键文件实际取自哪个 ref（供下游判新鲜度）
    facts["aclnn_headers"] = [p for p in hdrs if p in key]   # 一等接口头：真取到的那些（供下游只认它）
    # 一等接口头是否真取到 —— 下游（acc-spec 的 call_variants / out_role / runner arity）**只认它**，
    # 取不到就必须知道「是没改动、还是没取到」，不能让下游拿 example 的调用写法反推签名当权威。
    #
    # ⚠ 判据必须用 **`_aclnn_headers` 的结果**，不能拿 `_ACLNN_HDR_RE` 去扫整个 `key_files`（那是 fail-open）：
    # `key_files` 里还混着 `/op_host/` 那一档**不限目录、不剔 impl** 捞进来的文件，于是
    #   · 同 PR 里**别的算子**的 `aclnn_other.h`、
    #   · 本算子的内部实现头 `aclnn_median_impl.h`（它同样匹配 `aclnn_[A-Za-z0-9_]+\.h`）
    # 都会把这条「第一依据缺席」的告警**压掉** —— 而这两者都给不出本算子的对外签名，
    # 下游照样只能靠猜，却再也收不到警告。这正是本仓最忌的「假覆盖」。
    if not facts["aclnn_headers"]:
        facts["notes"].append(
            "本 PR 的改动文件里没有取到 aclnn 接口头（`aclnn_*.h`，已剔 `*_impl.h`）→ "
            "**aclnn 路由的第一依据缺席**：`call_variants` 的 symbol/形参顺序、多输出 out_role、runner arity "
            "都不得据 example 或算子名反推。要么该 PR 本就没改接口头（去 base 仓同目录取），要么取材失败——"
            "两种都须核实后再抽 spec。")
    # 批 6b B-core：据 key_files 机器判接口形态 + 抽真实 aclnn 入口（供 runner 锚定、scope gate 消费）。
    _ik, _entry, _ik_note = _detect_interface_kind(key)
    facts["interface_kind"], facts["aclnn_entry"] = _ik, _entry
    facts["notes"].append(f"接口形态(批6b探测)：{_ik_note}")
    # 现在只有 head_sha 一个 ref，取到的必定就是 head；stale 概念随兜底一并退役。
    # 保留一条正向记账：明确告知下游「这些文件确实钉在哪个 commit 上」。
    if key and head_sha:
        where = ("fork " + str(facts.get("head_repo"))) if facts.get("is_fork") else "同仓"
        facts["notes"].append("关键文件均取自 PR head commit %s（%s）" % (head_sha[:12], where))
    if not key:
        facts["notes"].append("未取到 example/op_def 关键文件内容（runner 锚定需另取）")
    return _dump_facts(facts, out_dir)


def _dump_facts(facts, out_dir):
    dst = os.path.join(out_dir, "pr_facts.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    return dst


# ════════════════════════════════════════════════════════════════════════════════
# 本地来源通路（`dut_source == "local_checkout"`）
# ════════════════════════════════════════════════════════════════════════════════

def _is_excluded(rel_parts, exclude_dirs=DEFAULT_DIGEST_EXCLUDE_DIRS,
                 exclude_suffixes=DEFAULT_DIGEST_EXCLUDE_SUFFIXES):
    """相对路径段元组是否命中排除清单（任一路径段命中目录名，或 basename 命中后缀）。"""
    if any(part in exclude_dirs for part in rel_parts):
        return True
    return bool(rel_parts) and rel_parts[-1].endswith(tuple(exclude_suffixes))


def resolve_op_subdir(repo_root, op_subdir):
    """把 `<repo_root>/<op_subdir>` 解析成真实路径并校验它没越出仓根；返回 `(root, base)`。"""
    root = os.path.realpath(repo_root)
    base = os.path.realpath(os.path.join(root, op_subdir))
    if base != root and not base.startswith(root + os.sep):
        raise RuntimeError(f"--op-subdir 越出了 --local-repo：{op_subdir!r} 解析到 {base}（仓根 {root}）")
    if not os.path.isdir(base):
        raise RuntimeError(f"被测子目录不存在或不是目录：{os.path.join(repo_root, op_subdir)}")
    return root, base


def compute_root_digest(repo_root, op_subdir,
                        exclude_dirs=DEFAULT_DIGEST_EXCLUDE_DIRS,
                        exclude_suffixes=DEFAULT_DIGEST_EXCLUDE_SUFFIXES):
    """被测子树 `<repo_root>/<op_subdir>` 的 Merkle 摘要——**本地通路的 provenance 锚**。

    它替代 PR 通路 `head_sha` 的锚定作用：本地 checkout 可能根本不是 git 仓，也可能 dirty，
    但被测的**字节**总是确定的。vendor build receipt 靠等值校验绑到这个值上，
    「build 出来的 `.so` 到底对应哪份源码」才有机器可核的答案。

    算法 `oprunway.local_subtree_merkle` v1（**逐字照此，改一处跨机就不可比**）：

      用 `os.scandir` + `lstat` **显式分类**每个条目（不跟随任何软链）：
        · 常规文件   → kind=b"f"，payload = 文件字节；mode 只取**可执行位**
        · 软链       → kind=b"l"，payload = `os.readlink()` 的目标字节（文件软链、**目录软链**都算）
        · 空目录     → kind=b"d"，payload = b""
        · 其它类型（FIFO / socket / 设备节点）→ **直接拒**，不猜语义
      排序：按 `os.fsencode(rel_path)` 的**字节序**升序
      逐条按长度分帧拼接：
        frame = kind + exec_bit(1 字节) + len(path_bytes).to_bytes(8,"big") + path_bytes
                     + len(digest).to_bytes(8,"big") + sha256(payload).digest()
      root_digest = sha256(所有 frame 顺序拼接).hexdigest()

    ⚠ 几个坑，每个都对应上面一处刻意的设计，别「简化」掉：

    | 坑 | 后果 | 处理 |
    |---|---|---|
    | 空目录被忽略 | 删掉目录里最后一个文件 → digest 不变 | 空目录以 `kind=b"d"` 计入 |
    | 软链与同内容常规文件碰撞 | 把文件换成指向别处的软链 → digest 不变 | `kind` 区分 `b"f"` / `b"l"` |
    | **目录软链漏计** | `os.walk` 把它放进 `dirnames` 又不递归 → 加删这类软链 digest 不变 | 自己 scandir + lstat，软链一律记 `b"l"` |
    | 可执行位变化 | build 脚本 644→755 会改构建行为却不改 digest | exec 位入帧 |
    | 遍历出错被静默吞掉 | 少读一棵子树 → digest 照样算得出来 | `scandir`/`lstat` 的 OSError 一律抛，不吞 |
    | 路径含 `\\0` / `\\n` | 用分隔符拼接会产生歧义 | 长度分帧 |

    ⚠ **本算法明确不覆盖的东西**（别以为它覆盖了）：

    · **硬链接拓扑**：两个硬链到同一 inode 的文件与两份同内容的独立文件算出同一值。
      被测语义上等价，故不建模；
    · **文件名的 Unicode 规范化**：`os.fsencode` 保留文件系统**呈现的**名字字节，
      它解决的是「非 UTF-8 文件名不被有损转码」，**并不**把 macOS 的 NFD 与 Linux 的 NFC
      统一。同一份代码在 macOS 与 Linux 上 checkout 后，若含非 ASCII 文件名，
      算出的 digest 可能不同。当前用法（同一台真机内取材 → 构建 → 校验）不受影响，
      跨机比对前必须先确认这一点；
    · **摘要范围只有 `op_subdir`**：仓级构建脚本、公共头文件、生成器都不在内。
      因此 `root_digest` 相同**不等于**构建出的 vendor `.so` 相同——它证明的是
      「被测算子子树的字节是这一份」，不是「整个构建输入闭包是这一份」。
      报告里必须按这个强度如实陈述（见 `render_acceptance_markdown` 的 provenance 节）;
    · **被排除的路径段本身**：`DEFAULT_DIGEST_EXCLUDE_DIRS` 里的 `build` / `build_out`
      按「任一路径段等名」剔除，剔的是**约定俗成的构建产物目录**——但这只是约定，
      工具没有、也无法证明这些目录里不含参与构建的源文件。所以子树内任何
      `**/build/**`、`**/build_out/**` 的改动**不会**改变 `root_digest`。
      这条排除是有意的（in-tree build 会让摘要随每次构建漂移，取材↔构建间的重算校验
      会永远失败），代价就是这块盲区。它**机器可核地**落在收据的
      `local_checkout.digest_policy.excluded_segment_names` 里，校验端逐字对账；
      把被测源码放进这类目录的仓，不适用本摘要。
    """
    _, base = resolve_op_subdir(repo_root, op_subdir)
    entries = []                                    # [(rel_bytes, kind, exec_bit, payload_digest)]

    def _walk(dir_path, dir_parts):
        try:
            with os.scandir(dir_path) as it:
                children = list(it)
        except OSError as exc:                      # ⚠ 不吞：少读一棵子树照样能算出 digest = 假摘要
            raise RuntimeError(f"读取目录失败，无法计算子树摘要：{dir_path}（{exc}）") from exc
        kept = 0
        for child in children:
            parts = dir_parts + (child.name,)
            if _is_excluded(parts, exclude_dirs, exclude_suffixes):
                continue
            kept += 1
            rel_b = os.fsencode(os.path.join(*parts))
            try:
                st = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise RuntimeError(f"lstat 失败，无法计算子树摘要：{child.path}（{exc}）") from exc
            if child.is_symlink():
                # 目录软链也走这里——`os.walk` 会把它当目录放进 dirnames 又不递归，于是整条不进摘要。
                entries.append((rel_b, b"l", b"\x00",
                                hashlib.sha256(os.fsencode(os.readlink(child.path))).digest()))
            elif child.is_dir(follow_symlinks=False):
                _walk(child.path, parts)
            elif child.is_file(follow_symlinks=False):
                h = hashlib.sha256()
                try:
                    with open(child.path, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
                except OSError as exc:
                    raise RuntimeError(f"读文件失败，无法计算子树摘要：{child.path}（{exc}）") from exc
                entries.append((rel_b, b"f", b"\x01" if st.st_mode & 0o111 else b"\x00", h.digest()))
            else:
                raise RuntimeError(
                    f"被测子树里有既非常规文件、也非目录/软链的条目：{child.path}"
                    f"（mode={st.st_mode:#o}）。FIFO/socket/设备节点的被测语义未定义，"
                    f"fail-closed —— 不猜、也不静默跳过（跳过就等于摘要少覆盖一块）。")
        # 空目录必须计入：否则「删掉目录里最后一个文件」digest 不变。
        # ⚠ 判据是**排除后**还剩几个条目：只剩 `__pycache__/` 的目录，对被测字节而言就是空的，
        #   与真空目录同表示是**有意的等价类**（两者都不贡献被测字节），不是碰撞缺陷。
        if dir_parts and kept == 0:
            entries.append((os.fsencode(os.path.join(*dir_parts)), b"d", b"\x00",
                            hashlib.sha256(b"").digest()))

    _walk(base, ())
    entries.sort(key=lambda e: e[0])                # 字节序，不是 str 序
    h = hashlib.sha256()
    for rel_b, kind, exec_bit, payload_digest in entries:
        h.update(kind)
        h.update(exec_bit)
        h.update(len(rel_b).to_bytes(8, "big"))
        h.update(rel_b)
        h.update(len(payload_digest).to_bytes(8, "big"))
        h.update(payload_digest)
    return h.hexdigest()


class GitProbeError(RuntimeError):
    """git 探测**失败**（≠「这不是 git 仓」）。两者必须分开，见 `probe_local_git` 的 ⚠。"""


def _git(repo_root, *args, binary=False, allow_fail=False):
    """在 repo_root 跑一条只读 git 命令。

    ⚠ **没有「失败就当空输出」这一档**。原先 `git status` 失败折叠成 `""` 会被读成
    「工作区干净」——那是**直接绕过 dirty fail-closed 门**：git 缺失、safe.directory 拒绝、
    仓损坏、超时，任何一种都能把一份 dirty 的 checkout 洗成 clean。
    `allow_fail=True` 只给「这条信息可有可无」的探测用（如 remote.origin.url 没配），
    它返回 None 而不是空串，调用方必须显式处理。
    """
    try:
        p = subprocess.run(("git", "-C", repo_root) + args,
                           capture_output=True, text=not binary, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        if allow_fail:
            return None
        raise GitProbeError(f"git {' '.join(args)} 执行失败：{exc}") from exc
    if p.returncode != 0:
        if allow_fail:
            return None
        stderr = p.stderr if isinstance(p.stderr, str) else (p.stderr or b"").decode("utf-8", "replace")
        raise GitProbeError(f"git {' '.join(args)} 失败（exit {p.returncode}）：{stderr.strip()[:400]}")
    return p.stdout


def _is_git_worktree(repo_root):
    """区分「这不是 git 仓」与「git 探测失败」——前者合法，后者必须阻断。

    判据：`git rev-parse --is-inside-work-tree` 输出 `true`。
    · 非 git 目录 → git 以非 0 退出、stderr 说 `not a git repository` → 返回 False；
    · git 不存在 / safe.directory 拒绝 / 仓损坏 → **抛 GitProbeError**，绝不当成「非 git 仓」
      放行（那会让 dirty 门整个消失）。
    """
    try:
        p = subprocess.run(("git", "-C", repo_root, "rev-parse", "--is-inside-work-tree"),
                           capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        raise GitProbeError(
            f"找不到 git 可执行文件，无法判定 {repo_root} 是否 git 仓。"
            f"本地通路的 dirty 门依赖它——探不到就不能放行（fail-closed）。") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitProbeError(f"git rev-parse 执行失败：{exc}") from exc
    if p.returncode == 0:
        return (p.stdout or "").strip() == "true"
    stderr = (p.stderr or "").lower()
    if "not a git repository" in stderr or "不是 git 仓库" in stderr:
        return False
    raise GitProbeError(
        f"git rev-parse 在 {repo_root} 上失败（exit {p.returncode}），但错误不是"
        f"「not a git repository」：{(p.stderr or '').strip()[:400]}\n"
        f"  → 不把它当成「非 git 仓」放行：那样 dirty 门会被静默跳过。"
        f"常见原因是 safe.directory 拒绝或仓损坏，请先修复再重跑。")


def _porcelain_z_records(out):
    """解析 `git status --porcelain=v1 -z` 的字节输出 → `[(xy, path), ...]`。

    ⚠ 为什么必须用 `-z` 而不是按行切：非 `-z` 输出会对含空格/引号/非 ASCII 的路径做 C-quoting，
    `line[3:].strip().strip('"')` 既反解不了转义、又会把路径首尾的真实空格吃掉 —— 记错的是
    「哪些文件脏」，而这份清单是要写进收据当证据的。`-z` 用 NUL 分隔、路径原样不转义。
    重命名/复制（R/C）在 `-z` 下**多占一个字段**（先新名、再原名）。

    ⚠ **R 与 C 的原名处置不同，别一视同仁**：
      · `R`（rename）——原名那份**真的没了**，两个名字都算脏。只记新名的话，
        把文件从被测子树挪出去会让 `dirty_files_in_op_subdir` 变 0，
        收据于是宣称「被测子树内没有未提交改动」，而子树里实际少了一个文件；
      · `C`（copy）——原文件**一个字节都没动**，脏的只有新拷出来的那份。
        把原名也记成脏会**虚构**一条脏文件（原文件在子树内、拷贝在子树外时，
        凭空把 provenance 说弱了）。所以 C 只消费原名字段用于推进索引，不记账。

    ⚠ R/C 的原名字段缺席（输出被截断）→ 抛错，不当成「刚好没有原名」。
    """
    fields = out.split(b"\x00")
    records, i = [], 0
    while i < len(fields):
        item = fields[i]
        i += 1
        if not item:
            continue
        if len(item) < 4:                           # 形如 "XY path"，短于此说明输出被截断了
            raise GitProbeError(f"git status --porcelain -z 输出异常字段：{item!r}")
        xy = item[:2].decode("ascii", "replace")
        records.append((xy, os.fsdecode(item[3:])))
        if "R" in xy or "C" in xy:                  # 下一字段是原名
            if i >= len(fields) or not fields[i]:
                raise GitProbeError(
                    f"git status --porcelain -z 的 {xy!r} 记录缺少原名字段（输出被截断）")
            if "R" in xy:                           # 只有 rename 的原名是「真的变了」
                records.append((xy, os.fsdecode(fields[i])))
            i += 1
    return records


def probe_local_git(repo_root, op_subdir=None):
    """探本地目录的 git 事实；**不是 git 仓就返回 None**（合法情形，只靠 root_digest 锚定）。

    返回 `{head_sha, remote_url, base_ref, dirty, dirty_files, dirty_files_in_op_subdir}`。

    ⚠ **「不是 git 仓」与「探测失败」必须分开**：前者返回 None（合法，走纯 root_digest 锚定），
    后者抛 `GitProbeError`。把探测失败当成「非 git 仓」就等于把 dirty fail-closed 门整个删掉。

    ⚠ `dirty` 取**整仓**口径而非只看 op_subdir：head_sha 是拿来描述整个 checkout 的，
    仓里任何一处未提交改动都让「这份 checkout == 这个 commit」不成立。
    但报错要能定位，所以另记 `dirty_files_in_op_subdir`——被测子树内的脏文件才直接动摇
    「head_sha ↔ 被测字节」的对应，子树外的属于「checkout 不干净」。两者都进收据。
    """
    if not _is_git_worktree(repo_root):
        return None
    head = (_git(repo_root, "rev-parse", "HEAD") or "").strip()
    remote = _git(repo_root, "config", "--get", "remote.origin.url", allow_fail=True)
    base_ref = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD", allow_fail=True)
    status = _git(repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                  binary=True)
    dirty_files = sorted({path for _xy, path in _porcelain_z_records(status)})
    in_op = []
    if op_subdir:
        pref = op_subdir.strip("/") + "/"
        in_op = [p for p in dirty_files if p == op_subdir.strip("/") or p.startswith(pref)]
    return {
        "head_sha": head or None,
        "remote_url": (remote or "").strip() or None,
        "base_ref": (base_ref or "").strip() or None,
        "dirty": bool(dirty_files),
        "dirty_files": dirty_files,
        "dirty_files_in_op_subdir": in_op,
    }


def _local_changed_files(repo_root, base_ref):
    """`git diff --name-only <merge-base> HEAD`；没给 base_ref 或不是 git 仓 → `"unavailable"`。

    ⚠ **绝不返回 `[]`**：空数组的语义是「这次什么都没改」，下游会据此认为 PR/改动为空。
    「算不出来」与「确实没改」必须分得开——前者是字符串 `"unavailable"`。

    ⚠ 两处刻意的写法：
      · **先把 base 与 HEAD 解析成定死的 commit sha、再显式取 merge-base**，而不是直接写
        `<base>...HEAD` 的三点糖。base 若是会移动的符号 ref（分支、`origin/master`），
        三点糖每次跑的对照点可能不同；无共同祖先时三点糖直接失败，而这里能给出可操作的错误。
      · `-z` + `os.fsdecode` 解析：非 `-z` 输出会对含空格/非 ASCII 的路径做 C-quoting，
        按行 split 会记错文件名。
    """
    if not base_ref:
        return "unavailable"
    base_sha = _git(repo_root, "rev-parse", "--verify", f"{base_ref}^{{commit}}", allow_fail=True)
    if base_sha is None:
        raise RuntimeError(
            f"--base-ref {base_ref!r} 在本地仓里解析不到 commit。\n"
            f"  请确认该 ref 已 fetch 到本地（如 `git fetch origin {base_ref}`），"
            f"或改用真实存在的 ref；也可以不给 --base-ref —— 那样 changed_files 记 'unavailable'。")
    merge_base = _git(repo_root, "merge-base", base_sha.strip(), "HEAD", allow_fail=True)
    if merge_base is None:
        raise RuntimeError(
            f"--base-ref {base_ref!r}（{base_sha.strip()[:12]}）与 HEAD **没有共同祖先**，"
            f"算不出改动清单。\n  这通常说明给错了 base（或仓是浅克隆、历史被截断）。"
            f"不给 --base-ref 可以继续，changed_files 记 'unavailable'。")
    out = _git(repo_root, "diff", "--name-only", "-z", merge_base.strip(), "HEAD", binary=True)
    return sorted({os.fsdecode(p) for p in out.split(b"\x00") if p})


def _local_key_files(repo_root, op_subdir):
    """按与 PR 通路**同一套结构规则**从本地磁盘挑关键文件并读内容。

    筛选规则复用 PR 通路（`_aclnn_headers` + examples/*.cpp + `_def.cpp`/`op_host`），
    差别只在「内容从盘上读」而非「API 取」。

    ⚠ **候选集固定为 `op_subdir` 子树全量，与 `changed_files` 无关**，两个理由：

    1. **锚覆盖**：本地通路每份关键文件的 `ref` 记的是 `root_digest`，而 `root_digest`
       只覆盖 `op_subdir`。若候选取自整仓 changed_files，子树外的文件也会被标上这个 ref ——
       **它根本没被这个锚覆盖**，却能通过准备门。那是 fail-open。
    2. **派生稳定**：若候选随 changed_files 变，同一份 checkout 给不给 `--base-ref`
       会挑出不同的 header/example/op_def，进而派生出不同的 `interface_kind` / `aclnn_entry` /
       spec。取证方式不该改变被测语义。

    ⚠ 与 PR 通路的**已知口径差异**（有意保留，不是遗漏）：PR 通路只能看到改动文件
    （API 只给这个），所以未改动的接口头它取不到；本地通路看得到整棵子树。
    差异方向是「本地更全」，落到下游是 `aclnn_headers` 更可能非空 —— 属收紧不属放宽。
    """
    _, base = resolve_op_subdir(repo_root, op_subdir)
    op_subdir = op_subdir.strip("/")
    candidates = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        rel_dir = os.path.relpath(dirpath, repo_root)
        parts = () if rel_dir == "." else tuple(rel_dir.split(os.sep))
        dirnames[:] = sorted(d for d in dirnames if not _is_excluded(parts + (d,)))
        for name in sorted(filenames):
            if not _is_excluded(parts + (name,)):
                candidates.append("/".join(parts + (name,)))
    hdrs = _aclnn_headers(candidates, op_subdir)
    want = (hdrs
            + [p for p in candidates if "/examples/" in p and p.endswith(".cpp")][:6]
            + [p for p in candidates if p.endswith("_def.cpp") or "/op_host/" in p][:4])
    prefix = op_subdir + "/"
    key = {}
    for rel in dict.fromkeys(want):
        if not rel.startswith(prefix):              # 双保险：候选已限定在子树内，这里再核一次
            continue
        full = os.path.join(repo_root, rel)
        # ⚠ 逃逸软链必须拒——而「拒」是**抛错**，不是 `continue`。
        #   静默跳过时这份关键文件只是从 key_files 里消失：`root_digest` 照算、
        #   `completeness` 照样 complete，一份少了接口头的事实包看上去完全正常。
        #   缺 `aclnn_*.h` 直接动摇 aclnn 路由的第一依据（symbol / 形参顺序 / out_role），
        #   这正是「证据不完整被静默升级为可裁决」。
        real = os.path.realpath(full)
        if real != base and not real.startswith(base + os.sep):
            raise RuntimeError(
                f"关键文件 {rel} 是逃逸软链，指向被测子树之外（{real}）。\n"
                f"  → 它的内容不在 root_digest 覆盖范围内，收进 key_files 等于用一份"
                f"没被锚定的字节去派生接口事实；跳过它则会产出一份「看起来完整、"
                f"实则少了接口头」的事实包。两者都不可接受，已中止。")
        try:
            with open(full, "rb") as fh:
                key[rel] = fh.read().decode("utf-8", "replace")
        except OSError as exc:
            raise RuntimeError(
                f"关键文件 {rel} 读取失败（{exc}）。\n"
                f"  → 不静默跳过：跳过后 completeness 仍会是 complete，"
                f"而事实包已经少了一份 header/example/op_def。请修复后重跑。") from exc
    return key, hdrs


def fetch_local(repo_root, op_subdir, out_dir, base_ref=None, allow_dirty=False):
    """本地来源：读盘取事实 → 写 `pr_facts.json`（与 PR 通路同名同形，靠 `dut_source` 判别）。

    dirty 处置：非 git 仓允许（只靠 root_digest）；git 仓 clean 允许；
    git 仓 dirty 且无 `--allow-dirty` → **不在这里抛错**，而是置 `blocked`，
    让 `build_source_facts` 落进 `completeness.reasons` —— 与 PR 通路
    `blocked='missing_head_sha'` 的处置**同形**：取材照样落盘供诊断，门在完整性判定处收。

    ⚠ 「门在别处收」只有配上**调用方一定会走完整性判定**才成立。所以：
      · `main()` 在 `completeness.status == "blocked"` 时**非 0 退出**（不能落盘就算成功）；
      · `facts["warnings"]` 把降级事实（dirty 放行、changed_files 算不出）显式记账，
        由 `build_source_facts` 交叉核对后写进 `completeness.warnings`——
        少写一条就等于让降级悄悄以干净 `complete` 通过。
    """
    op_subdir = op_subdir.strip("/")
    facts = {
        "dut_source": DUT_SOURCE_LOCAL,
        "notes": [],
        "warnings": [],
        "local_checkout": {
            "op_subdir": op_subdir,
            "digest_policy": digest_policy(),
        },
    }
    root_digest = compute_root_digest(repo_root, op_subdir)
    facts["local_checkout"]["root_digest"] = root_digest
    git = probe_local_git(repo_root, op_subdir)
    if git is not None:
        facts["local_checkout"]["git"] = {
            "head_sha": git["head_sha"], "remote_url": git["remote_url"],
            "base_ref": base_ref or git["base_ref"], "dirty": git["dirty"],
            "dirty_files": git["dirty_files"],
            "dirty_files_in_op_subdir": git["dirty_files_in_op_subdir"],
        }
        if git["dirty"] and not allow_dirty:
            facts["blocked"] = "dirty_worktree_not_allowed"
            facts["notes"].append(
                f"本地 worktree 有未提交改动（共 {len(git['dirty_files'])} 项，其中被测子树内 "
                f"{len(git['dirty_files_in_op_subdir'])} 项）→ git head "
                f"{(git['head_sha'] or '?')[:12]} 与实际被测字节不符，provenance 是假的。"
                f"已置 blocked='dirty_worktree_not_allowed'。要在开发期强行继续，"
                f"加 --allow-dirty（收据会全量记账 dirty 文件清单，报告顶部会标注）。")
        elif git["dirty"]:
            facts["warnings"].append(WARN_DIRTY_WORKTREE_ALLOWED)
            facts["notes"].append(
                f"⚠ --allow-dirty：worktree 有 {len(git['dirty_files'])} 项未提交改动"
                f"（被测子树内 {len(git['dirty_files_in_op_subdir'])} 项），"
                f"git head 不代表被测字节；provenance 只靠 root_digest。")
    else:
        facts["notes"].append("本地目录不是 git 仓 → 无 head_sha/base_ref，provenance 只靠 root_digest。")

    changed = _local_changed_files(repo_root, base_ref) if git is not None else "unavailable"
    facts["changed_files"] = changed
    if changed == "unavailable":
        facts["warnings"].append(WARN_CHANGED_FILES_UNAVAILABLE)
        facts["notes"].append(
            "未给 --base-ref（或非 git 仓）→ changed_files 记 'unavailable'。"
            "⚠ 这不是「没有改动」：算不出来与确实没改必须分得开。")

    key, hdrs = _local_key_files(repo_root, op_subdir)
    # ⚠ TOCTOU：摘要遍历 → git 探测 → 关键文件读取是三趟独立 I/O。目录若在中途被改，
    #   会拼出一个**从未真实存在过**的混合快照，而 key_files 的 ref 仍标着第一趟算的
    #   root_digest。这里重算一次并要求逐字相同——变了就停，不猜哪一半是真的。
    #   ⚠ 这道复算能逮住的是**改了没改回去**（编辑器保存、并发 build、rsync 落盘到一半），
    #   即取材期间的意外漂移。它**逮不住**「改完再原样改回来」：两次摘要相同，
    #   中间读到的却是替换版。要挡住那种情形得先做只读快照再取材，本批没做，如实挂账。
    recheck = compute_root_digest(repo_root, op_subdir)
    if recheck != root_digest:
        raise RuntimeError(
            f"取材期间被测子树发生了改动：开始时 root_digest={root_digest[:12]}…，"
            f"读完关键文件后重算={recheck[:12]}…。\n"
            f"  → 这会拼出一份从未真实存在过的「混合快照」，而关键文件的 ref 仍标着旧摘要。"
            f"已中止，未产出事实索引。请等目录稳定（或先停掉正在写它的进程）再重跑。")
    facts["key_files"] = key
    # 本地通路没有「ref」概念，锚就是 root_digest：每份关键文件都取自这一份子树快照。
    facts["key_files_ref"] = {p: root_digest for p in key}
    facts["aclnn_headers"] = [p for p in hdrs if p in key]
    if not facts["aclnn_headers"]:
        facts["notes"].append(
            "本地被测子树里没有取到 aclnn 接口头（`aclnn_*.h`，已剔 `*_impl.h`）→ "
            "**aclnn 路由的第一依据缺席**：`call_variants` 的 symbol/形参顺序、多输出 out_role、"
            "runner arity 都不得据 example 或算子名反推。核实后再抽 spec。")

    # op/target_dir：先用与 PR 通路**同一个**结构探测；探不到才退回 --op-subdir 的末段。
    op, target_dir = _guess_op(sorted(key) or ([] if changed == "unavailable" else changed))
    if target_dir and target_dir.strip("/") != op_subdir:
        facts["notes"].append(
            f"⚠ 结构探测判出的目标目录 {target_dir!r} 与 --op-subdir {op_subdir!r} 不一致；"
            f"以显式给定的 --op-subdir 为准（root_digest 也是按它算的）。")
    facts["op"] = op or (op_subdir.rsplit("/", 1)[-1] or None)
    facts["target_dir"] = op_subdir
    _ik, _entry, _ik_note = _detect_interface_kind(key)
    facts["interface_kind"], facts["aclnn_entry"] = _ik, _entry
    facts["notes"].append(f"接口形态(批6b探测)：{_ik_note}")
    pol = facts["local_checkout"]["digest_policy"]
    facts["notes"].append(
        f"被测来源 = 本地 checkout，子树摘要 root_digest={root_digest[:12]}…"
        f"（算法 {pol['algorithm']} v{pol['algorithm_version']}，排除路径段 "
        f"{pol['excluded_segment_names']} 与后缀 {pol['excluded_basename_suffixes']}）。\n"
        f"    ⚠ 摘要只覆盖 op_subdir，**不含**仓级构建脚本/公共头文件——它证明的是"
        f"「被测算子子树的字节是这一份」，不是「整个构建输入闭包是这一份」。\n"
        f"    ⚠ 本地 checkout **无法证明**它对应任何具体 PR。")
    if not key:
        facts["notes"].append("未取到 example/op_def 关键文件内容（runner 锚定需另取）")
    return _dump_facts(facts, out_dir)


def _is_str_list(value):
    """`list[str]` 且每项非空；**允许空表**。

    ⚠ 单独一个 helper 是为了不再出现 `if not value:` 这种「非空即可」的判据：
    字符串也非空，而字符串在下游生成式里会被**按字符迭代**成一份假清单。
    """
    return isinstance(value, list) and all(isinstance(p, str) and p for p in value)


def _is_path_list(value):
    """`_is_str_list` + **非空**——「必须有内容」的清单（如 PR 通路的 changed_files）。

    ⚠ 与 `_is_str_list` 分开，别合并：`dirty_files == []` 是干净 worktree 的**正确**表示，
    拿「非空」去要求它会把每一次干净取材都判成畸形收据。
    """
    return bool(value) and _is_str_list(value)


def build_source_facts(taskdoc_path, pr_facts, source_locator=None):
    """构造内容寻址的非真机事实索引；不做 NL 抽取、不确认 correspondence。

    `completeness=blocked` 的索引只供诊断，编排层不得把它作为 cache hit 放行。

    **来源判别式**取自 `pr_facts.dut_source`（缺省 `pull_request`），不另开参数——
    判别式只有一个真源，多一处就多一处「两边说法不一致」的洞。未知取值 fail-closed。

    两条通路的 payload 形状（`pr` / `local_checkout` 互斥出现，绝不互相伪装）：

      · `pull_request`  ：有 `pr`，**没有** `dut_source` 与 `local_checkout` 键
        —— 业务字段与本次改动前逐字节相同（`producer.logic_sha256` 例外，见模块头）；
      · `local_checkout`：有 `dut_source` + `local_checkout`，**没有** `pr` 键。

    `completeness` 另有 `warnings`（**仅非空时写入**，所以 PR 通路的 payload 不受影响）：
    `status` 只看 `reasons`。「算不出 changed_files」这类非阻塞事实必须留痕，
    但塞进 `reasons` 会直接把状态打成 blocked（`status` 没有第三态）。
    ⚠ 下游若有「reasons 为空即万事大吉」的假设，要同步改成也看 `warnings`。
    """
    with open(taskdoc_path, "rb") as src:
        task_raw = src.read()
    facts = pr_facts if isinstance(pr_facts, dict) else {}
    kind = dut_source.of(facts, where="pr_facts")
    is_local = kind == DUT_SOURCE_LOCAL
    local = facts.get("local_checkout") if isinstance(facts.get("local_checkout"), dict) else {}
    root_digest = local.get("root_digest")

    pr_url = facts.get("pr_url")
    owner = repo = number = None
    if pr_url:
        owner, repo, number = _parse_pr_url(pr_url)
    source_repo = facts.get("source_repo") or (
        f"{owner}/{repo}" if owner and repo else None)
    head_sha = facts.get("head_sha")
    # 关键文件的锚：PR 通路是 head_sha，本地通路是子树摘要 root_digest。
    # 「每份关键文件都取自被锚定的那一份快照」这条不变量两条通路同形，只是锚不同。
    anchor = root_digest if is_local else head_sha
    anchor_reason = "key_file_ref_not_root_digest" if is_local else "key_file_ref_not_head"

    key_files = facts.get("key_files") if isinstance(facts.get("key_files"), dict) else {}
    key_refs = facts.get("key_files_ref") if isinstance(facts.get("key_files_ref"), dict) else {}
    key_index, reasons, warnings = [], [], []
    for path in sorted(key_files):
        content = key_files[path]
        if not isinstance(content, str):
            reasons.append(f"key_file_not_text:{path}")
            continue
        raw = content.encode("utf-8")
        ref = key_refs.get(path)
        if not anchor or ref != anchor:
            reasons.append(f"{anchor_reason}:{path}")
        key_index.append({
            "path": path, "ref": ref,
            "bytes_sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
        })
    # 取材期动态阻断（PR 的 missing_head_sha / 本地的 dirty_worktree_not_allowed）——两条通路都保留。
    if facts.get("blocked"):
        reasons.append(str(facts["blocked"]))
    changed_files = facts.get("changed_files")
    if is_local:
        if not (isinstance(root_digest, str) and re.fullmatch(r"[0-9a-f]{64}", root_digest)):
            reasons.append("missing_root_digest")
        if not isinstance(local.get("op_subdir"), str) or not local.get("op_subdir"):
            reasons.append("missing_op_subdir")
        if local.get("digest_policy") != digest_policy():
            # 摘要策略必须逐字是本工具当前支持的那一份：策略不同则 digest 不可比，
            # 而外表看不出来。未知/弱化的排除策略一律 fail-closed。
            reasons.append("unsupported_digest_policy")
        if changed_files == "unavailable":
            # ⚠ 非阻塞：本地没给 base-ref 时算不出改动清单，属信息缺失、不是取材失败。
            warnings.append(WARN_CHANGED_FILES_UNAVAILABLE)
        elif not _is_path_list(changed_files):
            # ⚠ 只认 `list[str]` 与哨兵串 `"unavailable"` 两种形态。放任其它类型的话，
            #   `changed_files="abc"` 这种既非空、又不是 `"unavailable"` 的值会一路通过，
            #   到下面 payload 那句生成式里被**按字符迭代**成 `["a","b","c"]`——
            #   一份凭空捏出来的改动清单，形态上还完全合法。
            reasons.append("missing_changed_files")
        # ⚠ 判据是 `"git" in local` 而**不是** `local.get("git") is not None`：
        #   「非 git 仓」的唯一合法表示是整键缺席（`fetch_local` 就是这么写的）。
        #   `git: null` 在 `is not None` 下会被当成缺席，于是下面全部一致性校验整段跳过——
        #   一份写坏/被裁剪的收据只要把 git 置空就能免检。与 `validate_preparation_state`
        #   同一口径，两处必须一致，否则同一份收据在两道门里含义不同。
        if "git" in local:
            git = local["git"]
            dirty = git.get("dirty") if isinstance(git, dict) else None
            dirty_files = git.get("dirty_files") if isinstance(git, dict) else None
            if (not isinstance(git, dict) or not isinstance(dirty, bool)
                    or not _is_str_list(dirty_files)):
                reasons.append("malformed_local_git_facts")
            elif dirty != bool(dirty_files):
                # ⚠ 「说干净却列着脏文件」是本地通路最省事的一条降级洗白路：warnings 按
                #   `git.dirty` 派生，把它改成 false 就能让 dirty_worktree_allowed 整条消失。
                reasons.append("inconsistent_dirty_flag")
            elif dirty and not facts.get("blocked"):
                # dirty 但没被阻断 = 走了 --allow-dirty → 必须留下降级留痕。
                warnings.append(WARN_DIRTY_WORKTREE_ALLOWED)
    else:
        if not (isinstance(head_sha, str) and re.fullmatch(r"[0-9a-fA-F]{40}", head_sha)):
            reasons.append("missing_or_invalid_head_sha")
        if not isinstance(pr_url, str):
            reasons.append("missing_pr_url")
        if not isinstance(source_repo, str):
            reasons.append("missing_source_repo")
        if not isinstance(facts.get("head_repo"), str):
            reasons.append("missing_head_repo")
        if not isinstance(facts.get("is_fork"), bool):
            reasons.append("unknown_fork_status")
        if not isinstance(facts.get("state"), str):
            reasons.append("missing_pr_state")
        if not _is_path_list(changed_files):        # PR 通路只认非空 list[str]（无哨兵）
            reasons.append("missing_changed_files")
    # 两条通路共有：关键文件是 slot 对账的依据，缺了谁都不能往下走。
    if not key_index:
        reasons.append("missing_key_files")
    # ⚠ producer 自报的 warnings 与这里**从载重事实重新派生**的必须一致：
    #   · 多报（塞了词表外的串，或把阻塞原因伪装成 warning）→ 阻断；
    #   · 少报（降级发生了却没记）→ 阻断。
    #   只信 producer 自报等于让它自己给自己发合格证。
    declared = facts.get("warnings")
    if declared is not None:
        if not isinstance(declared, list) or any(not isinstance(w, str) for w in declared):
            reasons.append("malformed_declared_warnings")
        else:
            unknown = sorted(set(declared) - set(SOURCE_WARNINGS))
            if unknown:
                reasons.append("unknown_declared_warnings:" + ",".join(unknown))
            elif sorted(set(declared)) != sorted(set(warnings)):
                reasons.append("declared_warnings_mismatch")
    with open(__file__, "rb") as src:
        logic_sha = hashlib.sha256(src.read()).hexdigest()
    completeness = {
        "status": "complete" if not reasons else "blocked",
        "reasons": sorted(set(reasons)),
    }
    if warnings:                                    # 仅非空时写入 → PR 通路业务字段不变
        completeness["warnings"] = sorted(set(warnings))
    payload = {
        "contract_version": 1,
        "taskdoc": {
            # 本地绝对路径既不可移植、又会让同内容跨工作区无法命中；URL 可保留作来源定位，
            # 本地文件只记受控标签，内容身份只认 bytes_sha256。
            "source_locator": (source_locator if isinstance(source_locator, str)
                               and source_locator.startswith(("http://", "https://"))
                               else "<local-file>"),
            "bytes_sha256": hashlib.sha256(task_raw).hexdigest(),
            # CP-A 的 task_doc.snapshot.md 是逐字节复制，故同一摘要就是 spec/golden 的引文锚。
            "snapshot_sha256": hashlib.sha256(task_raw).hexdigest(),
            "size": len(task_raw),
        },
        "changed_files": (changed_files if changed_files == "unavailable" else sorted(
            p for p in (changed_files or []) if isinstance(p, str))),
        "key_files": key_index,
        "derived": {
            "op": facts.get("op"),
            "target_dir": facts.get("target_dir"),
            "aclnn_headers": sorted(
                p for p in (facts.get("aclnn_headers") or []) if isinstance(p, str)),
            "interface_kind": facts.get("interface_kind"),
            "aclnn_entry": facts.get("aclnn_entry"),
        },
        "completeness": completeness,
        "producer": {"tool": "fetch_source.py", "logic_sha256": logic_sha},
    }
    if is_local:
        payload["dut_source"] = DUT_SOURCE_LOCAL
        git = local.get("git") if isinstance(local.get("git"), dict) else None
        payload["local_checkout"] = {
            "root_digest": root_digest,
            "op_subdir": local.get("op_subdir"),
            # 结构化 + 版本化：校验端按受控值逐字核对，不接受任意排除策略（见 digest_policy）
            "digest_policy": local.get("digest_policy") or digest_policy(),
        }
        if git is not None:                         # 非 git 仓 → 整键缺席，不写空壳
            payload["local_checkout"]["git"] = {
                "head_sha": git.get("head_sha"),
                "remote_url": git.get("remote_url"),
                "base_ref": git.get("base_ref"),
                "dirty": bool(git.get("dirty")),
                "dirty_files": sorted(p for p in (git.get("dirty_files") or [])
                                      if isinstance(p, str)),
                "dirty_files_in_op_subdir": sorted(
                    p for p in (git.get("dirty_files_in_op_subdir") or []) if isinstance(p, str)),
            }
    else:
        payload["pr"] = {
            "canonical_url": pr_url,
            "source_repo": source_repo,
            "number": int(number) if number is not None else None,
            "head_sha": head_sha.lower() if isinstance(head_sha, str) else None,
            "head_repo": facts.get("head_repo"),
            "is_fork": facts.get("is_fork"),
            "state": facts.get("state"),
        }
    content_address.canonical_json_bytes(payload)
    return payload


def write_source_facts(taskdoc_path, pr_facts, out_dir, source_locator=None):
    """原子写 `source_facts.json` 内容寻址 envelope。"""
    return content_address.write_artifact(
        out_dir, "source_facts.json", "oprunway/source-facts/v1",
        build_source_facts(taskdoc_path, pr_facts, source_locator=source_locator))


def write_taskdoc_snapshot(taskdoc_path, snapshot_path):
    """把取到的任务书原文**逐字节原样**落成快照，返回 (sha256, path)。R12 / 批 3。

    ⚠ **必须逐字节复制，不许任何规范化**（不改行尾、不补末尾换行、不转码）——
    `verify_authorization` 按**行号 + 逐字子串**核引文；改动一个字节，行号就可能移位、
    引文就可能对不上，而那时报出来的是「引文与出处对不上」这种**看起来像 agent 编造引文**
    的错，真正的病因（快照被规范化过）却查不出来。故这里刻意用二进制读写。

    ⚠ **不覆盖已存在的快照**：快照是引文锚，已有 golden 的 `taskdoc_snapshot.sha256` 绑着它。
    静默覆盖 = 让所有既有引文锚一起失效却不报错。要换须显式删了重来（人为动作、留痕）。

    ⚠ **但「不覆盖」不等于「不吭声」**：上游任务书若已改版，安静地留着旧快照、还打印旧 sha256，
    调用方会以为刷新过了——**那是比覆盖更坏的静默**（验收基于一份自己都不知道过期的引文锚）。
    故内容不一致时 **fail-loud 抛错**，把两个指纹与处置方式一并说清，由人决定要不要换锚。"""
    if os.path.islink(snapshot_path):
        raise RuntimeError(f"任务书快照不得是符号链接：{snapshot_path}")
    if os.path.exists(snapshot_path):
        with open(snapshot_path, "rb") as f:
            old = f.read()
        with open(taskdoc_path, "rb") as f:
            new = f.read()
        old_d, new_d = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
        if old_d != new_d:
            raise RuntimeError(
                f"任务书快照已存在但**内容与本次取到的原文不一致**：{snapshot_path}\n"
                f"  既有快照 sha256: {old_d}\n"
                f"  本次取到 sha256: {new_d}\n"
                f"  → 说明上游任务书改版了。**不自动覆盖**：既有 golden 的引文锚"
                f"（taskdoc_snapshot.sha256 + cite 行号）绑在旧快照上，换掉会让它们一起失效。\n"
                f"  → 要换锚：先删掉这份快照重跑，**并逐个复核受影响 golden 的 cite 行号与 quote**"
                f"（行号极可能已移位）。这是人为动作，不该由脚本替你做。")
        return old_d, snapshot_path
    with open(taskdoc_path, "rb") as src:
        raw = src.read()
    _atomic_write_bytes(snapshot_path, raw)             # 逐字节 + 原子替换，不经文本层
    return hashlib.sha256(raw).hexdigest(), snapshot_path


def main(argv):
    ap = argparse.ArgumentParser(
        description="① 取材：任务书(md/链接) + 被测代码(在线 PR 链接 或 本地 checkout) → 中立 JSON/文件")
    ap.add_argument("--taskdoc", required=True, help="任务书 md 本地路径 或 http(s) 链接")
    ap.add_argument("--pr", default=None, help="gitcode PR 链接（与 --local-repo 互斥）")
    ap.add_argument("--local-repo", default=None, metavar="DIR",
                    help="本地已 clone 的被测代码仓根目录（与 --pr 互斥）")
    ap.add_argument("--op-subdir", default=None, metavar="REL",
                    help="被测算子子目录，相对 --local-repo（本地通路必填；root_digest 按它算）")
    ap.add_argument("--base-ref", default=None, metavar="REF",
                    help="本地通路可选：给了才算 changed_files（git diff <base>...HEAD）；"
                         "不给则记 'unavailable'（≠ 空数组）")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="本地通路逃生阀：允许 worktree 有未提交改动。"
                         "收据会全量记账 dirty 清单、报告顶部会标注 provenance 降级")
    ap.add_argument("--out", required=True, help="产出目录")
    ap.add_argument("--snapshot-into", default=None, metavar="DIR",
                    help="另把任务书原文逐字节落成 task_doc.snapshot.md 到该目录"
                         "（通常是 <ops_root>/<op>/），并打印 sha256——供 golden 契约块的引文锚绑定（R12）")
    a = ap.parse_args(argv)
    # 来源参数校验**前置到一切网络调用与产物写入之前**：否则任务书是链接时，会先发一次网络请求、
    # 先写出 task_doc.md，然后才报「PR 格式不认识」——半个产物已经落盘了，与 fail-loud 的承诺不符。
    # 这里只校形态（纯函数、不联网）；取不到 PR 的网络失败仍在 fetch_pr 内按环境问题处理。
    if a.pr and a.local_repo:
        ap.error("--pr 与 --local-repo 互斥：被测代码只能有一个来源。"
                 "在线 PR 用 --pr，本地 checkout 用 --local-repo + --op-subdir。")
    if a.local_repo:
        if not a.op_subdir:
            ap.error("--local-repo 必须同时给 --op-subdir（被测算子子目录，相对仓根）——"
                     "root_digest 与关键文件筛选都按它算，不猜。")
        if not os.path.isdir(a.local_repo):
            ap.error(f"--local-repo 不是目录：{a.local_repo}")
    if not a.pr and not a.local_repo:
        for opt, val in (("--op-subdir", a.op_subdir), ("--base-ref", a.base_ref),
                         ("--allow-dirty", a.allow_dirty)):
            if val:
                ap.error(f"{opt} 只在 --local-repo 通路有意义，但没给 --local-repo。")
    if a.pr:
        _parse_pr_url(a.pr)
    import precision_policy
    os.makedirs(a.out, exist_ok=True)
    extra_snapshots = ()
    if a.snapshot_into:
        extra_snapshots = (
            os.path.join(a.snapshot_into, precision_policy.TASKDOC_SNAPSHOT_NAME),)
    td = fetch_taskdoc(
        a.taskdoc, a.out, extra_snapshot_paths=extra_snapshots)
    print(f"[fetch] 任务书 → {td}")
    # CP-A 立即落工作区快照：spec 抽取前就能拿到 SHA，消除「spec 先留空 → gen_golden
    # 后补快照 → 再派 refine_spec 回填」的必然返工。这里仍只复制字节，不做任何 NL/判定。
    work_snapshot = os.path.join(a.out, precision_policy.TASKDOC_SNAPSHOT_NAME)
    work_digest, work_snapshot = write_taskdoc_snapshot(td, work_snapshot)
    print(f"[fetch] 工作区任务书快照 → {work_snapshot}")
    print(f"        sha256 = {work_digest}")
    if a.snapshot_into:
        sp = os.path.join(a.snapshot_into, precision_policy.TASKDOC_SNAPSHOT_NAME)
        digest, sp = write_taskdoc_snapshot(td, sp)
        print(f"[fetch] 任务书快照 → {sp}")
        print(f"        sha256 = {digest}")
        print(f"        ↑ 写进 golden.py 契约块的 taskdoc_snapshot.sha256；"
              f"引文 cite 用 {precision_policy.TASKDOC_SNAPSHOT_NAME}:<起>[-<止>]")
    if not a.pr and not a.local_repo:
        # ⚠ 不能安静地只产任务书就退出：后面 CP-C 的三道门（preflight / harness trust /
        # run_workflow）全都以 source_facts.json 为前置，会一路 BLOCKED，而用户看到的
        # 却是「取材成功」。这里把因果讲清并以非 0 退出，别让下游去猜。
        print("[fetch] ⚠ 未给 --pr / --local-repo → 未产 source_facts.json 与 pr_facts.json；",
              file=sys.stderr)
        print("        CP-C 三道门（preflight / harness trust / run_workflow）将 BLOCKED。",
              file=sys.stderr)
        print("        在线被测代码用 --pr <gitcode PR 链接>；"
              "本地 checkout 用 --local-repo <仓根> --op-subdir <算子子目录>。", file=sys.stderr)
        return 2
    if a.pr:
        pf = fetch_pr(a.pr, a.out)
    else:
        pf = fetch_local(a.local_repo, a.op_subdir, a.out,
                         base_ref=a.base_ref, allow_dirty=a.allow_dirty)
    facts = json.load(open(pf, encoding="utf-8"))
    sf = write_source_facts(td, facts, a.out, source_locator=a.taskdoc)
    changed = facts.get("changed_files")
    changed_desc = changed if changed == "unavailable" else f"{len(changed or [])}文件"
    print(f"[fetch] {'PR' if a.pr else '本地 checkout'} → {pf}  op={facts.get('op')} "
          f"目录={facts.get('target_dir')} 改动{changed_desc} "
          f"关键{len(facts.get('key_files', {}))}份")
    source_payload = content_address.read_artifact(
        a.out, "source_facts.json", "oprunway/source-facts/v1")
    print(f"[fetch] 事实索引 → {sf} dut_source="
          f"{source_payload.get('dut_source', DUT_SOURCE_PR)} completeness="
          f"{source_payload['completeness']['status']}")
    if source_payload["completeness"].get("warnings"):
        print(f"        warnings={source_payload['completeness']['warnings']}（非阻塞，但须留痕）")
    for n in facts.get("notes", []):
        print(f"  ⚠ {n}")
    if source_payload["completeness"]["status"] != "complete":
        # ⚠ 落盘 ≠ 成功。blocked 的事实索引只供诊断，编排层不得据它往下走。
        # shell 调用方只看得到退出码，这里返回 0 等于告诉它「取材成功」——那是 fail-open。
        print(f"[fetch] ✗ completeness=blocked reasons="
              f"{source_payload['completeness']['reasons']}", file=sys.stderr)
        print("        事实索引只可用于诊断，**不得**据它抽 spec / 产 runner / 跑验收。",
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
