"""① 取材 — 把「任务书(md 路径或链接) + PR 链接」取成中立 JSON/文件，供 acc-spec skill 消费。

Layer 1 确定性脚本（工具中立、可移植）：纯 stdlib（urllib），无算子/仓目录硬编码
（GitCode API base 与常见分支 master/main 有默认值），无 Claude-Code 依赖。
gitcode token 走环境：优先 $GITCODE_TOKEN，退回 $OPRUNWAY_GITCODE_TOKEN_FILE 指向的文件（默认 ~/.gitcode_token）；
公开内容无 token 也尽量 raw 取。**token 不落盘、不进输出。**

用法:
  python3 fetch_source.py --taskdoc <path|url> [--pr <gitcode PR url> | --pr-snapshot <dir>]
                          [--target-dir <仓内相对目录>] --out <dir>
产出:
  <out>/task_doc.md      任务书原文（本地读或链接取）
  <out>/task_doc.snapshot.md 与原文逐字节相同的稳定引文锚（CP-A 即落，供 spec/golden 共用 SHA）
  <out>/pr_facts.json    PR 事实（给了 --pr 或 --pr-snapshot 才有）：op / 目标仓·目录 / base·head /
                         changed_files / 关键文件内容（op 自带 example、op_def）——供 ② 抽 spec、③ 锚定 runner
  <out>/source_facts.json 内容寻址事实索引（同上）：任务书字节、PR head、关键文件 ref/摘要、
                          接口派生事实与完整性状态——供非真机断点复用，不含 token/关键文件正文
说明：链接失败/无权限时不静默——task_doc 取不到直接报错；PR 链接**形态不认识→直接报错（fail-loud，属用户输入错）、不产空壳**；
      PR 链接认识但字段取不到（网络/权限）→记进 pr_facts.notes 继续（属环境问题，与「URL 写错」错误信息分开）。

`--target-dir`：显式指定被测算子在仓内的相对目录，**逐字采用**、并以其末段作 op 名，绕过 `_guess_op` 的
      路径探测（探测器要求算子目录之上至少还有一层，仓根一级布局如 ops-cv 的 `gaussian_blur/` 探不到）。
      不给就完全按今天的行为走。

`--pr-snapshot`：降级取材通路，输入是**本地一份没有 git 的目录快照**（与 `--pr` 互斥）。
      产 `provenance_kind="local_snapshot"` + `head_sha=null`（**绝不合成 40 位 hex**）
      + `snapshot_merkle_sha256`（对「排序后的相对路径 × 文件字节」求的确定性摘要）。
      ⚠ merkle 只证「本地这份字节是什么」，**不证**它等于任何 PR head；`source_facts.completeness`
      因此落第三档 `snapshot_only`（既非 complete 也非 blocked），下游各门仍只认 `complete`，
      要不要放行这条降级路由由编排层/人另行决定，本脚本不替它松门。
"""
import argparse, hashlib, json, os, re, sys, tempfile, urllib.parse, urllib.request

import content_address

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


def _norm_target_dir(value):
    """规范化 `--target-dir` 覆盖值 → (op, target_dir)；形态不合法 fail-loud。

    ⚠ 这是**覆盖**不是**探测**：给了就逐字采用（末段即 op 名），不再跑 `_guess_op`。
    `_guess_op` 的正则要求算子目录之上至少还有一层（`<族>/<op>/op_host/…`），仓根一级布局
    （ops-cv 的 `gaussian_blur/op_host/…`）探不到 → 返回 (None, None) → key_files 全空 →
    接口形态被误判成 `library_header` 并 BLOCKED。改正则会动到既有多层目录语义，故走覆盖口。
    """
    if value is None:
        return None, None
    rel = str(value).strip().strip("/")
    parts = [seg for seg in rel.split("/") if seg]
    if (not rel or "\x00" in rel or os.path.isabs(str(value).strip())
            or len(parts) != len(rel.split("/")) or any(p in (".", "..") for p in parts)):
        raise ValueError(
            f"--target-dir 形态不合法：{value!r}\n"
            "  期望：仓内**相对**目录，用 `/` 分段，不含空段 / `.` / `..`（例：gaussian_blur 或 image/resize）。")
    return parts[-1], "/".join(parts)


def _key_file_candidates(paths, target_dir):
    """按同一优先级口径列出关键文件候选 → (一等接口头列表, 去重保序的候选列表)。

    `--pr` 与 `--pr-snapshot` **共用这一份口径**，免得两条取材路各自漂移。
    顺序即优先级；**aclnn 接口头不进任何截断档**（见 `_aclnn_headers` 的理由），
    后两档仍各自设上限（防某些 PR 改上百个文件时把请求数打爆）。
    """
    hdrs = _aclnn_headers(paths, target_dir)
    want = (hdrs
            + [p for p in paths if "/examples/" in p and p.endswith(".cpp")][:6]
            + [p for p in paths if p.endswith("_def.cpp") or "/op_host/" in p][:4])
    return hdrs, list(dict.fromkeys(want))     # 去重保序：接口头也落在 `/op_host/` 档里，别重复请求


def _apply_key_file_facts(facts, key, key_ref, hdrs):
    """把关键文件与接口形态派生事实写进 facts —— `--pr` 与 `--pr-snapshot` 共用。"""
    facts["key_files"] = key
    facts["key_files_ref"] = key_ref  # 每个关键文件实际取自哪个 ref（供下游判新鲜度）
    facts["aclnn_headers"] = [p for p in hdrs if p in key]   # 一等接口头：真取到的那些（供下游只认它）
    # 一等接口头是否真取到 —— 下游（acc-spec 的 call_variants / out_role / runner arity）**只认它**，
    # 取不到就必须知道「是没改动、还是没取到」，不能让下游拿 example 的调用写法反推签名当权威。
    #
    # ⚠ 判据必须用 **`_aclnn_headers` 的结果**，不能拿 `_ACLNN_HDR_RE` 去扫整个 `key_files`（那是 fail-open）：
    # `key_files` 里还混着 `/op_host/` 那一档**不限目录、不剔 impl** 捞进来的文件，于是
    #   · 同 PR 里**别的算子**的 `aclnn_other.h`、
    #   · 本算子的内部实现头 `aclnn_median_impl.h`（它同样匹配 `aclnn_[A-Za-z0-9_]+\\.h`）
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
    return facts


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


def fetch_pr(pr_url, out_dir, target_dir=None):
    """PR：解析 gitcode PR 链接 → API 取 元信息 + 改动文件 + 关键文件（example/op_def），写 pr_facts.json。

    `target_dir` 非 None 时**逐字覆盖** `_guess_op` 的探测结果（op 取其末段）；为 None 时行为与既往逐字一致。

    两种失败严格区分：
      · URL 形态不认识 → `_parse_pr_url` 抛 ValueError（fail-loud，属用户输入错），**在任何网络调用之前**中止、不落 pr_facts.json；
      · URL 认识但网络/token 取不到字段 → 不抛，记进 facts["notes"] 继续（属环境问题，错误信息与「URL 写错」不同，别让用户误改 URL）。"""
    owner, repo, num = _parse_pr_url(pr_url)  # 形态错 → 抛出（fail-loud），不产空壳
    _ov_op, _ov_dir = _norm_target_dir(target_dir)   # 形态错也在网络之前抛
    facts = {"pr_url": pr_url, "notes": [], "source_repo": f"{owner}/{repo}",
             "provenance_kind": "gitcode_pr"}
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
    if _ov_dir:
        op, target_dir = _ov_op, _ov_dir
        facts["notes"].append(f"target_dir 由 --target-dir 显式覆盖为 {target_dir}（未走 _guess_op 路径探测）")
    else:
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
        hdrs, want = _key_file_candidates(paths, target_dir)
        for rel in want:
            c, r = _grab(rel)
            if c:
                key[rel], key_ref[rel] = c, r
    _apply_key_file_facts(facts, key, key_ref, hdrs)
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


# 走目录快照时跳过的目录名：VCS 元数据 + 常见构建/缓存产物。
# ⚠ 跳过项**必须记进 pr_facts**（`snapshot_skipped_dir_names`），否则 merkle 覆盖了什么就成了暗知识。
_SNAPSHOT_SKIP_DIRS = frozenset({
    ".git", ".gitcode", ".github", ".hg", ".svn",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".cache",
    "build", "output", "node_modules", ".idea", ".vscode",
})


def _walk_snapshot(root, scope_rel=""):
    """列出快照目录下的**普通文件**相对路径（相对 root、`/` 分隔、已排序）。

    · 不跟随符号链接（目录与文件都跳过 symlink）——快照要的是「这份目录里的字节」，不是它指向别处的字节；
    · 按名跳过 `_SNAPSHOT_SKIP_DIRS`；
    · `scope_rel` 非空时只走该子树，但相对路径仍**以 root 为基准**（下游 `_aclnn_headers` 等按仓内路径匹配）。
    """
    base = os.path.join(root, *scope_rel.split("/")) if scope_rel else root
    out = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _SNAPSHOT_SKIP_DIRS
                             and not os.path.islink(os.path.join(dirpath, d)))
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            out.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return sorted(out)


def _snapshot_merkle(root, rel_paths):
    """对「排序后的相对路径 × 文件字节」求确定性 sha256。

    逐条先各自摘要再喂进总摘要，避免路径与内容拼接产生歧义（`a`+`bc` 与 `ab`+`c` 撞一起）。
    ⚠ 这个摘要**只回答「本地这份字节是什么」**，不回答「它等于哪个 commit」——
    绝不能被当成 head_sha 的替代品往下游 provenance 里塞。
    """
    h = hashlib.sha256()
    for rel in rel_paths:
        with open(os.path.join(root, *rel.split("/")), "rb") as f:
            blob = f.read()
        h.update(hashlib.sha256(rel.encode("utf-8")).digest())
        h.update(hashlib.sha256(blob).digest())
    return h.hexdigest()


def _read_snapshot_text(root, rel):
    """从快照里读一份关键文件的 UTF-8 文本；读不出（越界/二进制/IO 错）→ None，不抛。"""
    try:
        with open(content_address.safe_path(root, rel.replace("/", os.sep)), "rb") as f:
            return f.read().decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError, content_address.ContentAddressError):
        return None


def _assert_snapshot_dir(snapshot_dir):
    """`--pr-snapshot` 的形态检查 → 规范化后的绝对路径；不合法 fail-loud。

    单独抽出来是为了让 `main()` 能在**任何落盘之前**先校形态（与 `--pr` 的 URL 形态前置校验同口径），
    不至于先写出半个 task_doc.md 再报「快照目录不存在」。
    """
    root = os.path.abspath(os.fspath(snapshot_dir))
    if not os.path.isdir(root) or os.path.islink(root):
        raise ValueError(f"--pr-snapshot 须指向一个已存在的真实目录（非符号链接）：{snapshot_dir!r}")
    return root


def scan_pr_snapshot(snapshot_dir, out_dir, target_dir=None):
    """降级取材：把**本地一份没有 git 的目录快照**扫成 pr_facts.json（与 `--pr` 互斥）。

    产出与 `--pr` 同形，差别只在 provenance 三项：
      · `provenance_kind="local_snapshot"`；
      · `head_sha=None` —— **绝不合成 40 位 hex**（AGENTS.md 5.8：不捏造）。没有 git 就是没有 head；
      · `snapshot_merkle_sha256` —— 只证本地字节，**不证**它等于任何 PR head。

    关键文件的挑选口径与 `--pr` **逐字相同**（`_key_file_candidates`），只是内容从磁盘读而不是走 API。
    最终裁决层不得据此声称「已绑定 PR head」。
    """
    root = _assert_snapshot_dir(snapshot_dir)
    _ov_op, _ov_dir = _norm_target_dir(target_dir)
    if _ov_dir and not os.path.isdir(os.path.join(root, *_ov_dir.split("/"))):
        raise ValueError(f"--target-dir 在快照里不存在：{_ov_dir}（快照根 {root}）")

    paths = _walk_snapshot(root, _ov_dir or "")
    if _ov_dir:
        op, tdir = _ov_op, _ov_dir
    else:
        op, tdir = _guess_op(paths)

    facts = {
        "pr_url": None,
        "notes": [],
        "source_repo": None,
        "provenance_kind": "local_snapshot",
        "head_sha": None,                      # 没有 git 就是没有 head——不合成、不猜
        "snapshot_merkle_sha256": _snapshot_merkle(root, paths),
        "snapshot_scope": _ov_dir or "",
        "snapshot_file_count": len(paths),
        "snapshot_skipped_dir_names": sorted(_SNAPSHOT_SKIP_DIRS),
        "changed_files": paths,
        "op": op,
        "target_dir": tdir,
    }
    facts["notes"].append(
        "provenance=local_snapshot：输入是本地目录快照、无 git → **head_sha 为 null，未合成任何 commit id**。"
        f"snapshot_merkle_sha256 覆盖 {len(paths)} 个文件"
        f"（范围 {_ov_dir or '<快照根>'}，已跳过 {sorted(_SNAPSHOT_SKIP_DIRS)} 这些目录名），"
        "**只证本地字节是什么，不证它等于任何 PR head**；下游不得据此声称已绑定 PR head。")
    facts["notes"].append(
        "changed_files 实为「该子树下的全部文件」，**不是 PR diff**——本通路拿不到 base，无法算真实改动集。")
    if _ov_dir:
        facts["notes"].append(f"target_dir 由 --target-dir 显式覆盖为 {tdir}（未走 _guess_op 路径探测）")

    key, key_ref = {}, {}
    hdrs = []
    if tdir:
        hdrs, want = _key_file_candidates(paths, tdir)
        for rel in want:
            c = _read_snapshot_text(root, rel)
            if c is not None:
                key[rel], key_ref[rel] = c, "local_snapshot"
    _apply_key_file_facts(facts, key, key_ref, hdrs)
    if not tdir:
        facts["notes"].append(
            "未能判出算子目录（`_guess_op` 对仓根一级布局探不到）→ 关键文件一份没取。请显式给 --target-dir。")
    if not key:
        facts["notes"].append("未取到 example/op_def 关键文件内容（runner 锚定需另取）")
    return _dump_facts(facts, out_dir)


# `provenance_kind="local_snapshot"` 下**必然**成立、且全部只源于「没有 PR / 没有 git」这一个事实的
# reason。它们塞进 reasons 只会把真正的缺口淹掉，故在快照通路上折叠成单条
# `pr_provenance_local_snapshot`。⚠ 折叠的是**表述**不是**门**：completeness 落第三档
# `snapshot_only`，各门仍只认 `complete`，本函数一处门都不放松。
_SNAPSHOT_PROVENANCE_REASONS = frozenset({
    "missing_or_invalid_head_sha", "missing_pr_url", "missing_source_repo",
    "missing_head_repo", "unknown_fork_status", "missing_pr_state",
})
_SNAPSHOT_PROVENANCE_REASON = "pr_provenance_local_snapshot"


def build_source_facts(taskdoc_path, pr_facts, source_locator=None):
    """构造内容寻址的非真机事实索引；不做 NL 抽取、不确认 correspondence。

    `completeness` 三档：
      · `complete`      —— 绑死 PR head，唯一被下游各门接受的一档；
      · `snapshot_only` —— 输入是本地目录快照（`provenance_kind="local_snapshot"`），
                           除「无 PR/无 head」这一族必然缺口外别无缺口。**既非 complete 也非 blocked**：
                           事实是诚实可表达的，但**门没有放松**，要不要授权这条降级路由由编排层/人另行决定；
      · `blocked`       —— 其余任何缺口；只供诊断，编排层不得把它作为 cache hit 放行。
    """
    with open(taskdoc_path, "rb") as src:
        task_raw = src.read()
    facts = pr_facts if isinstance(pr_facts, dict) else {}
    pr_url = facts.get("pr_url")
    owner = repo = number = None
    if pr_url:
        owner, repo, number = _parse_pr_url(pr_url)
    source_repo = facts.get("source_repo") or (
        f"{owner}/{repo}" if owner and repo else None)
    head_sha = facts.get("head_sha")
    key_files = facts.get("key_files") if isinstance(facts.get("key_files"), dict) else {}
    key_refs = facts.get("key_files_ref") if isinstance(facts.get("key_files_ref"), dict) else {}
    key_index, reasons = [], []
    for path in sorted(key_files):
        content = key_files[path]
        if not isinstance(content, str):
            reasons.append(f"key_file_not_text:{path}")
            continue
        raw = content.encode("utf-8")
        ref = key_refs.get(path)
        if not head_sha or ref != head_sha:
            reasons.append(f"key_file_ref_not_head:{path}")
        key_index.append({
            "path": path, "ref": ref,
            "bytes_sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
        })
    if facts.get("blocked"):
        reasons.append(str(facts["blocked"]))
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
    if not facts.get("changed_files"):
        reasons.append("missing_changed_files")
    if not key_index:
        reasons.append("missing_key_files")
    provenance_kind = facts.get("provenance_kind") or "gitcode_pr"
    if provenance_kind == "local_snapshot":
        # key_file_ref_not_head:* 同属「没有 head 可绑」这一族必然缺口 → 一并折叠。
        reasons = [r for r in reasons
                   if r not in _SNAPSHOT_PROVENANCE_REASONS
                   and not r.startswith("key_file_ref_not_head:")]
        status = "blocked" if reasons else "snapshot_only"
        reasons = sorted(set(reasons) | {_SNAPSHOT_PROVENANCE_REASON})
    else:
        status = "complete" if not reasons else "blocked"
        reasons = sorted(set(reasons))
    with open(__file__, "rb") as src:
        logic_sha = hashlib.sha256(src.read()).hexdigest()
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
        "pr": {
            "canonical_url": pr_url,
            "source_repo": source_repo,
            "number": int(number) if number is not None else None,
            "head_sha": head_sha.lower() if isinstance(head_sha, str) else None,
            "head_repo": facts.get("head_repo"),
            "is_fork": facts.get("is_fork"),
            "state": facts.get("state"),
            # provenance_kind ∈ {gitcode_pr, local_snapshot}；后者的 merkle **只证本地字节**，
            # 不是 head_sha 的替代品，任何下游都不得据它声称「已绑定 PR head」。
            "provenance_kind": provenance_kind,
            "snapshot_merkle_sha256": (
                facts.get("snapshot_merkle_sha256")
                if isinstance(facts.get("snapshot_merkle_sha256"), str) else None),
        },
        "changed_files": sorted(
            p for p in (facts.get("changed_files") or []) if isinstance(p, str)),
        "key_files": key_index,
        "derived": {
            "op": facts.get("op"),
            "target_dir": facts.get("target_dir"),
            "aclnn_headers": sorted(
                p for p in (facts.get("aclnn_headers") or []) if isinstance(p, str)),
            "interface_kind": facts.get("interface_kind"),
            "aclnn_entry": facts.get("aclnn_entry"),
        },
        "completeness": {"status": status, "reasons": reasons},
        "producer": {"tool": "fetch_source.py", "logic_sha256": logic_sha},
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
    ap = argparse.ArgumentParser(description="① 取材：任务书(md/链接) + PR(链接) → 中立 JSON/文件")
    ap.add_argument("--taskdoc", required=True, help="任务书 md 本地路径 或 http(s) 链接")
    # `--pr` 与 `--pr-snapshot` 是两条**互斥**的取材通路：前者绑 PR head commit，后者只绑本地字节。
    # 交给 argparse 互斥组而不是手写 if，是为了让「同时给两个」在参数解析期就被拒（退出码 2），
    # 不至于走到「按哪个为准」这种要靠实现顺序才说得清的地方。
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--pr", default=None, help="gitcode PR 链接（可选）")
    src.add_argument("--pr-snapshot", default=None, metavar="DIR",
                     help="降级取材：本地一份**没有 git** 的目录快照。产 provenance_kind=local_snapshot、"
                          "head_sha=null（绝不合成 commit id）、snapshot_merkle_sha256；"
                          "source_facts.completeness 落 snapshot_only，下游各门仍只认 complete")
    ap.add_argument("--target-dir", default=None, metavar="REL_DIR",
                    help="显式指定被测算子在仓内的相对目录（如 gaussian_blur 或 image/resize），"
                         "**逐字采用**、末段即 op 名，绕过 _guess_op 的路径探测"
                         "（探测器要求算子目录之上至少还有一层，仓根一级布局探不到）")
    ap.add_argument("--out", required=True, help="产出目录")
    ap.add_argument("--snapshot-into", default=None, metavar="DIR",
                    help="另把任务书原文逐字节落成 task_doc.snapshot.md 到该目录"
                         "（通常是 <ops_root>/<op>/），并打印 sha256——供 golden 契约块的引文锚绑定（R12）")
    a = ap.parse_args(argv)
    # PR URL 形态校验**前置到一切网络调用与产物写入之前**：否则任务书是链接时，会先发一次网络请求、
    # 先写出 task_doc.md，然后才报「PR 格式不认识」——半个产物已经落盘了，与 fail-loud 的承诺不符。
    # 这里只校形态（纯函数、不联网）；取不到 PR 的网络失败仍在 fetch_pr 内按环境问题处理。
    if a.pr:
        _parse_pr_url(a.pr)
    # 同理前置：`--target-dir` 形态（纯函数）与 `--pr-snapshot` 目录形态都不联网，先校完再落任何盘。
    _norm_target_dir(a.target_dir)
    if a.pr_snapshot:
        _assert_snapshot_dir(a.pr_snapshot)
    if a.target_dir and not (a.pr or a.pr_snapshot):
        raise ValueError("--target-dir 只在给了 --pr 或 --pr-snapshot 时才有意义"
                         "（它覆盖的是 pr_facts 里的算子目录探测结果）；单独给会被静默忽略，故直接拒。")
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
    if a.pr or a.pr_snapshot:
        if a.pr:
            pf = fetch_pr(a.pr, a.out, target_dir=a.target_dir)
            _label = "PR"
        else:
            pf = scan_pr_snapshot(a.pr_snapshot, a.out, target_dir=a.target_dir)
            _label = "本地快照(降级)"
        facts = json.load(open(pf, encoding="utf-8"))
        sf = write_source_facts(td, facts, a.out, source_locator=a.taskdoc)
        print(f"[fetch] {_label} → {pf}  op={facts.get('op')} 目录={facts.get('target_dir')} "
              f"文件{len(facts.get('changed_files', []))}个 关键{len(facts.get('key_files', {}))}份")
        source_payload = content_address.read_artifact(
            a.out, "source_facts.json", "oprunway/source-facts/v1")
        print(f"[fetch] 事实索引 → {sf} completeness="
              f"{source_payload['completeness']['status']}")
        for n in facts.get("notes", []):
            print(f"  ⚠ {n}")


if __name__ == "__main__":
    main(sys.argv[1:])
