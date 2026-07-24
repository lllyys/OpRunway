"""torch 对标 · aclnn_py repo-adapter —— ctypes-aclnn runner form 的 fetch/build/install/exec/collect 编排。

canon 归属（trust tier 待 bureau:review）：这是 repo-adapter 的**新 harness form / adapter mode**
（按能力/仓/框架扩，同 catlass `generated_harness` 先例；蓝图 §6），注册进 `repo_adapter.MODES["aclnn_py"]`。
**无 per-op runner 源**——op 工程（PR checkout）即 DUT，`aclnn_runtime` 的 ctypes runner 完全 op-中立
（从 header 正则拿 op 名、从签名推 arity）。换任意「域内」aclnn 算子（无状态 / 标准两段式 / 无 opaque
descriptor）工具零改即跑。

⚠ 边界（诚实，承 catlass_adapter 同款纪律）：
- **判定不在此**：本模块只做 fetch/build/install/exec/collect（产 out_k.bin），evidence 组装含
  `compute_metrics` 误差复算走 `repo_adapter.build_multi_output_evidence`（OpRunway 侧），pass/fail 归
  validator/perf_compare（ADR 0007）。本模块一律不算 metrics、不下结论。
- **真机全部待验**（承 golden-branch-handoff「covered≠真机绿」）：build.sh install / ctypes 在 9.0.1
  运行时 / 多输出 arity / bf16 窄化 —— 均须 a3 `oprunway_prov` 容器实证。real 通路默认 fail-closed，须显式
  `OPRUNWAY_ACLNN_REAL=1` + 人工确认副作用（build install 写用户态 vendor 目录）才跑（同 catlass `OPRUNWAY_CATLASS_REAL`）。
- **零硬编码 / 副作用隔离**：机器名 / 远端路径 / op 子路径 / soc / vendor / PR-head-sha 全经 `OPRUNWAY_*`
  传入（无私有默认，缺关键项 fail-closed）；install 落**用户态 vendor 目录**（`--install-path`），运行时
  `ASCEND_CUSTOM_OPP_PATH` 指该目录，绝不写共享 CANN 的 opp/vendors（CLAUDE.md a3 共享机告警）。

✅ §9.4 张力已按 §9.6 a3 实测配方收敛（2026-07-24 D0/D1/D2 坐实，覆盖蓝图 §5.1 三签名旧约定）：
  PR6429（ops-nn 框架内实验算子）**无 per-op build.sh / 无 op_graph**——DUT 是 **ops-<族>仓 checkout**：
  仓根有 `build.sh`、op 在子目录（如 `experimental/index/median/`，含 `op_host/` + `op_api/aclnn_*.h` 手写接口）。
  故 `find_aclnn_project` 判据改为「ops 仓形态」（仓根 build.sh + op 子目录 op_host/op_api），`_run_aclnn_real`
  的 build 按 §9.6 实测配方：取源(fetch PR head)→依赖门(pigz/dos2unix)→仓根 `build.sh --pkg --experimental
  --ops=<snake> --soc=<soc> --vendor_name=<v> --no_force`→install(`.run --install-path=<用户目录>`)。
  op 子路径 / soc / vendor / PR-ref 全从 cfg（`OPRUNWAY_ACLNN_*`，承 spec/pr_facts 字段）取，**绝无按算子名分支**。
"""

from __future__ import annotations

import os
import posixpath
import re


# op 子路径 / PR ref / 取源 URL 的字符白名单（防拼进 ssh/git 命令的注入面；配合 shlex.quote 纵深防御）。
_SUBDIR_RE = re.compile(r"^[A-Za-z0-9_./-]+$")            # op 子路径：拒 shell 特殊字符、空白
#: PR head 引用**只认两种形态**（审计 High#2）：40 位 commit SHA，或 `refs/merge-requests/<N>/head`。
#: 旧白名单 `^[A-Za-z0-9][A-Za-z0-9_./-]*$` 把 `main` / 短 SHA / `refs/heads/*` 全放过 → 验收可静默偏离指定 PR。
_REF_RE = re.compile(r"(?:[0-9a-fA-F]{40}|refs/merge-requests/[1-9][0-9]*/head)")   # 用 fullmatch
_HEX40_RE = re.compile(r"[0-9a-fA-F]{40}")                                          # 用 fullmatch
_REPO_URL_RE = re.compile(r"^https?://[A-Za-z0-9._~/:@%-]+$")  # 取源 URL：仅 http(s) + 安全字符（拒 ?&;#|` 等）
_SYMBOL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")        # aclnn 符号名（拼进远端 nm/grep 命令）；用 fullmatch
_SEG_RE = re.compile(r"[A-Za-z0-9_.-]+")                  # caseset 相对路径的**每一段**；用 fullmatch


# ── 相对路径守卫（caseset 输入 / 远端 manifest 输出；审计 High#4）────────────────────────

def _safe_case_rel(rel, case_ids, label, expect_cid=None):
    """校验 caseset / 远端 manifest 里的**相对路径**（如 `c0/x1.bin`），返回原串。

    旧洞（审计 High#4）：`inp["path"]` / manifest 的 `o["path"]` 只经 `_safe()` 做**词法 containment**，
    既没过字符白名单、也没要求 canonical、更没逐段查软链，就直接拼进 `host:path` 交给 scp/cp——
    传统 scp 是「远端跑 shell」，路径里的空格 / `$(...)` / `;` 就是远端命令注入面；本地软链段则能把
    读写引到工作目录之外。

    要求（全部 fail-closed，绝不放宽）：canonical POSIX 相对路径、**≥2 段**、逐段 `[A-Za-z0-9_.-]+`、
    拒绝绝对 / 空段 / `.` / `..` / 首字符 `-`，且**首段必须是已校验的 case ID**。
    """
    if not isinstance(rel, str) or not rel:
        raise ValueError(f"{label} 相对路径缺失/非字符串: {rel!r}（fail-closed）")
    if rel.startswith("/") or "\\" in rel or rel != posixpath.normpath(rel):
        raise ValueError(f"{label} 相对路径 {rel!r} 非 canonical POSIX 相对路径（拒绝对/`./`/`//`/尾斜杠/反斜杠）")
    segs = rel.split("/")
    if len(segs) < 2:
        raise ValueError(f"{label} 相对路径 {rel!r} 须为 `<case_id>/<文件名>` 形态（≥2 段）")
    for s in segs:
        if not s or s in (".", "..") or s.startswith("-") or not _SEG_RE.fullmatch(s):
            raise ValueError(f"{label} 相对路径 {rel!r} 的段 {s!r} 非法"
                             f"（仅 [A-Za-z0-9_.-]、非空、非 ./..、不以 - 开头）")
    if segs[0] not in case_ids:
        raise ValueError(f"{label} 相对路径 {rel!r} 的首段 {segs[0]!r} 不是本 caseset 已校验的 case ID"
                         f"（不接受落在 case 目录之外的路径）")
    if expect_cid is not None and segs[0] != expect_cid:
        raise ValueError(f"{label} 相对路径 {rel!r} 的首段与所属 case {expect_cid!r} 不符（拒绝跨 case 写入）")
    return rel


def _reject_symlink_rel(base, rel, label):
    """从 `base` 起**逐段 lstat 拒软链**（含最后一段）。补 `_safe()` 只做词法 containment 的洞：
    软链段会让 scp/cp 静默跟随到工作目录之外（读侧泄露、写侧越界写）。尚未创建的段天然非软链，放行。"""
    cur = os.path.abspath(base)
    for s in rel.split("/"):
        cur = os.path.join(cur, s)
        if os.path.islink(cur):
            raise ValueError(f"{label} 的路径段是符号链接，拒绝（防路径逃逸/换靶）: {cur!r}")
    return cur


def _required_symbols(caseset):
    """本次 caseset 需要的 aclnn 符号集（**字段驱动**：取各 case 的 `aclnn_call.symbol`，绝无算子名分支）。

    用途：build/install 后核验 `libcust_opapi.so` 里**确实**有这些符号——否则「.so 在」不等于「被测算子在」
    （旧缓存 .so、装错 vendor、build 悄悄跳过该算子都会漏过去）。缺 `aclnn_call` / 符号名非法 → fail-closed。"""
    syms = []
    for c in caseset.get("cases") or []:
        call = c.get("aclnn_call")
        if not isinstance(call, dict):
            raise ValueError(
                f"case {c.get('id')!r} 缺 aclnn_call —— aclnn_py 的调用形态须由 spec.call_variants 声明、"
                f"gen_cases 逐 case 完全解析；缺它无法核验 .so 符号，fail-closed")
        sym = call.get("symbol")
        if not isinstance(sym, str) or not _SYMBOL_RE.fullmatch(sym):
            raise ValueError(f"case {c.get('id')!r} 的 aclnn_call.symbol={sym!r} 非法（须 C 标识符）")
        if sym not in syms:
            syms.append(sym)
    if not syms:
        raise ValueError("caseset 无任何 aclnn_call.symbol —— 无法核验被测符号是否真在 .so 里，fail-closed")
    return syms


# ── DUT 定位（find_aclnn_project）──────────────────────────────────────────────

def _aclnn_ops_root(explicit=None):
    """aclnn DUT 的 **ops 仓 checkout 根**（仓根有 `build.sh`，op 在子目录 op_subdir）。

    显式入参优先；否则 env `OPRUNWAY_ACLNN_OPS_DIR`（**须绝对路径**）。无私有默认——缺失即 fail-closed
    （承工程约定「零硬编码」：由编排层经 env 传入本地 ops 仓 checkout 根，用于**形态核验**——
    确认是域内 aclnn 算子；真实 build 由 `_run_aclnn_real` 在容器内按 PR-ref 重新取源，两者应同 PR）。"""
    root = explicit if explicit is not None else os.environ.get("OPRUNWAY_ACLNN_OPS_DIR")
    if not root:
        raise ValueError(
            "缺 aclnn ops 仓 checkout 根——请设 OPRUNWAY_ACLNN_OPS_DIR（绝对路径，指向仓根含 build.sh 的 ops 仓 checkout），"
            "或显式传入 ops_root。本函数不提供私有默认（零硬编码）。")
    if not os.path.isabs(root):
        raise ValueError(f"aclnn ops 仓 checkout 根须为绝对路径: {root!r}")
    return os.path.realpath(root)


def _safe_op_subdir(op_subdir):
    """校验 op 子路径（相对 ops 仓根，如 `experimental/index/median`）为安全的嵌套相对路径。

    须：非空、非绝对、无 `..`/`.` 段、canonical（== normpath，拒 `./`/尾斜杠/`//`）、仅安全字符、
    各段不以 `-` 开头（防拼进命令被当选项）。缺/非法 → fail-closed（承 `repo_adapter._ne_cfg` 的 op_src 同纪律）。"""
    if not isinstance(op_subdir, str) or not op_subdir:
        raise ValueError(
            "缺 op 子路径（OPRUNWAY_ACLNN_OP_SUBDIR，相对 ops 仓根，如 experimental/index/median；从 spec/pr_facts target_dir 取）")
    seg = op_subdir.split("/")
    if (op_subdir.startswith("/") or ".." in seg or "." in seg
            or op_subdir != posixpath.normpath(op_subdir)
            or not _SUBDIR_RE.match(op_subdir)
            or any(s.startswith("-") for s in seg)):
        raise ValueError(
            f"OPRUNWAY_ACLNN_OP_SUBDIR={op_subdir!r} 须为安全的嵌套相对子路径"
            f"（相对 ops 仓根、无前导 /、无 ./.. 段、canonical、仅 [A-Za-z0-9_./-]、段不以 - 开头）")
    return op_subdir


def find_aclnn_project(op, ops_root=None, op_subdir=None):
    """按 **ops-<族>仓形态** 核验 aclnn DUT（§9.4/§9.6 实测收敛，取代蓝图 §5.1 三签名旧约定）。

    判据（据**稳定形态特征**、绝无按算子名分支）：
      · DUT 根 = ops 仓 checkout，**仓根有 `build.sh`**（非 per-op）；
      · op 在子目录 `<root>/<op_subdir>`（op_subdir 从 spec/pr_facts/cfg 字段取，如 experimental/index/median），
        该子目录含 `op_host/`（算子实现）+ `op_api/aclnn_*.h`（**手写** aclnn 两段式接口头）。
      · **无** op_graph、**无** per-op build.sh（ops-nn CMake 框架内实验算子的真实形态）。
    **缺任一 → fail-closed（不回退、不硬塞、不自动归某类 adapter）**（承 runner-is-output：op 工程即 DUT，
    缺件说明该 PR 非域内标准 aclnn 两段式或未 checkout）。返回**仓根**绝对路径（build.sh 所在）。

    安全（op 工程会被 build/加载符号，是真实注入/换靶面）——**复用 repo_adapter 的守卫**：
    - `op` 经 `_check_id`（拒首字符 '-'、'.'/'..'、空白/斜杠/shell 特殊字符）；
    - `op_subdir` 经 `_safe_op_subdir`（嵌套相对、canonical、白名单）；
    - 从 ops_root 起**逐段拒软链**（`_reject_symlink_segments`）：挡 `<root>/<op_subdir>` 任一目录段是软链的换靶。
    - ⚠ 审计 Medium#6：**所有必需节点自身也逐段查**——旧版逐段守卫只走到 `op_path`，仓根 `build.sh`、
      `op_host/`、`op_api/`、`aclnn_*.h` **自身**仍可以是软链（指向仓外的另一份源）；形态核验一过，
      下游就按「这是被测 PR 的源」跑下去。故这些节点一律要求「非软链 + 真实文件/目录」。
    """
    import repo_adapter as RA
    RA._check_id("op_name", op)
    root = _aclnn_ops_root(ops_root)
    sub = _safe_op_subdir(op_subdir)
    op_path = os.path.join(root, sub)
    RA._reject_symlink_segments(root, op_path, f"aclnn op 子目录({op}@{sub})")

    def _real(node, label, want_dir):
        """节点须存在、逐段非软链、且是**真实**目录/普通文件（非软链、非 fifo/设备）。缺 → 记 missing。"""
        RA._reject_symlink_segments(root, node, f"aclnn {label}")     # 自身在内逐段拒软链
        if want_dir:
            return os.path.isdir(node) and not os.path.islink(node)
        return os.path.isfile(node) and not os.path.islink(node)

    missing = []
    if not _real(os.path.join(root, "build.sh"), "仓根 build.sh", want_dir=False):
        missing.append("仓根 build.sh(普通文件、非软链)")
    if not _real(op_path, f"op 子目录({sub})", want_dir=True):
        missing.append(f"op 子目录 {sub}/(真实目录)")
    else:
        if not _real(os.path.join(op_path, "op_host"), "op_host", want_dir=True):
            missing.append("<op_subdir>/op_host/(真实目录)")
        op_api = os.path.join(op_path, "op_api")
        if not _real(op_api, "op_api", want_dir=True):
            missing.append("<op_subdir>/op_api/(真实目录)")
        elif not any(n.startswith("aclnn_") and n.endswith(".h") and not n.endswith("_impl.h")
                     and _real(os.path.join(op_api, n), f"op_api/{n}", want_dir=False)
                     for n in sorted(os.listdir(op_api))):
            missing.append("<op_subdir>/op_api/aclnn_*.h(手写 aclnn 接口头，普通文件、非软链)")
    if missing:
        raise ValueError(
            f"aclnn DUT 非 ops-<族>仓形态，缺签名件 {missing}（root={root!r} op_subdir={sub!r}）——"
            f"fail-closed（不硬塞、不回退、不自动归某类 adapter）。"
            f"域内假设（§9.4/§9.6 实测收敛）：ops 仓 checkout（仓根 build.sh）+ op 子目录含 op_host/ + op_api/aclnn_*.h。")
    return root


# ── 真机配置（零硬编码、无私有默认；仅 soc/setenv/device 保留昇腾通用约定）────────────────────────

def _aclnn_cfg():
    """aclnn_py 真机跑测配置——**零硬编码**（承 repo_adapter._ne_cfg 同纪律）。

    机器名 / 远端工作根 / op 子路径 / 用户态 vendor 目录 / vendor 名 / 取源 URL / PR-ref**必须经 `OPRUNWAY_*`
    显式提供**（承 spec/pr_facts 字段），缺失即 fail-closed。`OPRUNWAY_TARGET` local/remote 决定是否 ssh。
    仅 soc / setenv / device 保留昇腾通用约定默认（非某台机私有）。`snake_op` 缺省从 op_subdir 末段派生（字段驱动）。"""
    g = os.environ.get
    target = (g("OPRUNWAY_TARGET") or "remote").strip().lower()
    if target not in ("local", "remote"):
        raise ValueError(f"OPRUNWAY_TARGET 须为 'local' 或 'remote'，得 {target!r}")

    def _req(key, why):
        v = (g(key) or "").strip()
        if not v:
            raise ValueError(f"缺 {key}（{why}）——本函数不提供私有默认值，请由编排层经 env 传入（承 spec/pr_facts 字段）。")
        return v

    op_subdir = _safe_op_subdir(_req(
        "OPRUNWAY_ACLNN_OP_SUBDIR", "op 子路径（相对 ops 仓根，如 experimental/index/median）——从 spec/pr_facts target_dir 取"))
    pr_ref = _req("OPRUNWAY_ACLNN_PR_REF", "PR head 引用（40 位 SHA 或 refs/merge-requests/<PR>/head）")
    # 审计 High#2：**只认** 40 位 SHA / refs/merge-requests/<N>/head 两种形态（fullmatch）。
    # 旧正则把 `main` / 短 SHA / `refs/heads/*` 都放过 → 取到的根本不是被测 PR，验收证据却照产。
    if not _REF_RE.fullmatch(pr_ref):
        raise ValueError(
            f"OPRUNWAY_ACLNN_PR_REF={pr_ref!r} 非法——只接受 **40 位 commit SHA** 或 "
            f"`refs/merge-requests/<N>/head`（拒分支名 / 短 SHA / refs/heads/*：那会让验收静默偏离指定 PR）")
    # PR facts 的 head SHA（用于 fetch 后 `git rev-parse` 比对 + 绑进 build provenance）。
    # ref 本身就是 SHA → 它即期望值；ref 是**可移动**的 merge-request ref → 必须显式给期望 SHA，
    # 否则「取到的到底是不是被测 commit」无从核验（fail-closed，不静默接受漂移）。
    head_sha = (g("OPRUNWAY_ACLNN_PR_HEAD_SHA") or "").strip()
    if head_sha and not _HEX40_RE.fullmatch(head_sha):
        raise ValueError(f"OPRUNWAY_ACLNN_PR_HEAD_SHA={head_sha!r} 非法（须 40 位 commit SHA）")
    if _HEX40_RE.fullmatch(pr_ref):
        if head_sha and head_sha.lower() != pr_ref.lower():
            raise ValueError(f"OPRUNWAY_ACLNN_PR_HEAD_SHA={head_sha!r} 与 PR_REF={pr_ref!r} 不符（同一 commit 两个说法）")
        head_sha = pr_ref
    elif not head_sha:
        raise ValueError(
            "PR_REF 是可移动的 merge-request 引用 → 必须同时给 OPRUNWAY_ACLNN_PR_HEAD_SHA"
            "（40 位 head commit SHA，从 pr_facts 取）：否则 fetch 到的 commit 无从核验，"
            "验收可能实际跑在另一份代码上（fail-closed）")
    head_sha = head_sha.lower()
    base_repo = _req("OPRUNWAY_ACLNN_BASE_REPO",
                     "取源 git 远端 URL（PR head 从 base 仓取，如 https://gitcode.com/cann/ops-nn.git）")
    if not _REPO_URL_RE.match(base_repo):
        raise ValueError(f"OPRUNWAY_ACLNN_BASE_REPO={base_repo!r} 非法（仅 http(s):// + 安全字符，拒 ?&;#|` 等注入面）")
    proxy = (g("OPRUNWAY_ACLNN_PROXY") or "").strip()          # 取源联网代理（可选；容器直连 gitcode 时留空）
    if proxy and not _REPO_URL_RE.match(proxy):
        raise ValueError(f"OPRUNWAY_ACLNN_PROXY={proxy!r} 非法（仅 http(s):// + 安全字符）")

    cfg = {"target": target,
           "ops_root": _aclnn_ops_root(),                       # 本地 ops 仓 checkout 根（形态核验用）
           "op_subdir": op_subdir,                              # op 相对仓根子路径
           "rroot": _req("OPRUNWAY_REMOTE_DIR", "远端（或本机）工作根目录"),
           "vendor_dir": _req("OPRUNWAY_ACLNN_VENDOR_DIR",
                              "用户态 vendor 安装目录（install-path；隔离共享 opp/vendors，绝不写共享 CANN）"),
           "vendor_name": _req("OPRUNWAY_ACLNN_VENDOR_NAME",
                               "build.sh --vendor_name 值（install 落地目录自动补 _nn 后缀，§9.6）"),
           "base_repo": base_repo,
           "pr_ref": pr_ref,
           "head_sha": head_sha,                                # 期望的 PR head commit（provenance 锚）
           "proxy": proxy,
           "soc": (g("OPRUNWAY_ACLNN_SOC") or g("OPRUNWAY_SOC") or "ascend910_93").strip(),  # 昇腾通用约定
           "snake_op": (g("OPRUNWAY_ACLNN_SNAKE_OP") or "").strip(),   # 缺省从 op_subdir 末段派生
           "device": g("OPRUNWAY_NPU_DEVICE", "0"),
           "setenv": g("OPRUNWAY_SETENV", "/usr/local/Ascend/ascend-toolkit/set_env.sh")}
    if not cfg["snake_op"]:
        cfg["snake_op"] = op_subdir.split("/")[-1]              # 字段驱动：op 名 snake = op 子路径末段（如 median）
    cfg["host"] = _req("OPRUNWAY_SSH_HOST",
                       "远端机器名（ssh）；本机即目标机时设 OPRUNWAY_TARGET=local 免此项"
                       ) if target == "remote" else None
    return cfg


def _pcontains(root, path):
    """POSIX 路径包含判定（两者均须 canonical 绝对路径）：path == root 或 path 在 root 之下。"""
    return path == root or path.startswith(root.rstrip("/") + "/")


def _check_dedicated_root(label, val):
    """专用根目录（远端工作根 / vendor 安装根）的额外要求：**canonical + 深度 ≥2**。

    `_check_remote_path` 只挡字符/`..`/相对，`/`、`/tmp`、`//x`、`/a/` 都能过——而这些位置上的
    `rm -rf`/install 影响面远超「本次验收的 scratch 目录」。故另加一道：必须是 normpath 自身、
    且至少两段（拒根目录与顶层目录）。"""
    import repo_adapter as RA
    RA._check_remote_path(label, val)
    if val != posixpath.normpath(val):
        raise ValueError(f"{label}={val!r} 非 canonical 绝对路径（拒 `//`、尾斜杠、`/./`）")
    segs = [s for s in val.split("/") if s]
    if len(segs) < 2:
        raise ValueError(f"{label}={val!r} 太浅（根目录 / 顶层目录不得作专用工作根——本模块会在其下 rm -rf / install）")
    return val


def _aclnn_paths(cfg):
    """从 cfg 计算远端路径（checkout / vendor 内容根 / custom lib / 用例目录 / 输出目录）+ 安全校验。

    ⚠ 审计 High#5：除逐项字符/绝对性校验外，另做**相交守卫**——部署阶段会对 `rcases`/`rout` 执行
    `rm -rf`，而 `vendor_dir` / `setenv` / `checkout` 都是用户经 env 给的独立配置：实测
    `vendor_dir=<rroot>/aclnn_cases` 能过旧校验，随后部署就把刚装好的 vendor 删了。故此处令
    「删除目标」与「vendor / setenv / checkout」两两不相交，任一相交即 fail-closed。"""
    import repo_adapter as RA
    rroot, vendor_dir, vn = cfg["rroot"], cfg["vendor_dir"], cfg["vendor_name"]
    _check_dedicated_root("remote_dir", rroot)
    _check_dedicated_root("vendor_dir", vendor_dir)
    RA._check_remote_path("setenv", cfg["setenv"])
    RA._check_id("vendor_name", vn)
    RA._check_id("snake_op", cfg["snake_op"])
    if not RA._SOC_RE.match(cfg["soc"]):
        raise ValueError(f"非法 soc: {cfg['soc']!r}")
    if not str(cfg["device"]).isdigit():
        raise ValueError(f"非法 device（须非负整数）: {cfg['device']!r}")
    checkout = rroot + "/aclnn_src"                             # 容器内 PR head 重取源落点（op-中立）
    RA._check_remote_path("checkout", checkout)
    vc = vendor_dir + "/vendors/" + vn + "_nn"                  # install 落地目录（--vendor_name 自动补 _nn，§9.6）
    paths = {"checkout": checkout,
             "vc": vc,
             "lib": vc + "/op_api/lib/libcust_opapi.so",        # provenance 门 + ctypes 加载目标
             "rcases": rroot + "/aclnn_cases",
             "rout": rroot + "/aclnn_out"}
    # 相交守卫：rm -rf 的目标 vs 必须存活的东西（双向 commonpath 语义）。
    setenv = posixpath.normpath(cfg["setenv"])
    for dl, dp in (("aclnn_cases(rm -rf 目标)", paths["rcases"]), ("aclnn_out(rm -rf 目标)", paths["rout"])):
        for kl, kp in (("vendor_dir", vendor_dir), ("vendor 内容根", vc), ("checkout", checkout), ("setenv", setenv)):
            if _pcontains(dp, kp) or _pcontains(kp, dp):
                raise ValueError(
                    f"{dl}={dp!r} 与 {kl}={kp!r} 相交——部署会对前者 rm -rf，会连带删掉后者，拒绝。"
                    f"请把 OPRUNWAY_REMOTE_DIR / OPRUNWAY_ACLNN_VENDOR_DIR 指向互不包含的专用目录。")
    if _pcontains(vendor_dir, checkout) or _pcontains(checkout, vendor_dir):
        raise ValueError(f"vendor_dir={vendor_dir!r} 与 checkout={checkout!r} 相交（重建会 rm -rf vendor 内容根），拒绝")
    return paths


# ── build / install 脚本组装（§9.6 实测配方）─────────────────────────────────────

#: 脚本模板占位符（单遍替换，见 `_render`）。
_PLACEHOLDER_RE = re.compile(r"@@[A-Z0-9_]+@@")


def _render(tmpl, repl):
    """占位符**单遍**替换 + 未知占位符 fail-closed。

    为什么不是「循环 str.replace」（审计纵深）：串行替换会**二次扫描**已替换进去的值——`base_repo`
    白名单允许 `@`，`https://x@@SOC@@y` 就能让后一轮把它当占位符再替一次。单遍 `re.sub` 杜绝这条路。"""
    def _one(m):
        k = m.group(0)
        if k not in repl:
            raise ValueError(f"脚本模板含未提供的占位符 {k}（fail-closed，防漏替换后原样发到远端）")
        return repl[k]
    return _PLACEHOLDER_RE.sub(_one, tmpl)


#: 远端 shell 的公共守卫函数（build / deploy / exec / perf 共用一份，避免漂移）。
#: 承审计 High#5 / Medium#7：**副作用发生前**在同一个 shell 里逐段拒软链、核专用根属主与权限、
#: 且 `source set_env.sh` 失败必须立即退出（不再 `|| true` 吞掉，防用了登录 shell 里残留的另一套 CANN）。
_SH_GUARDS = r'''set -u
oprw_fail() { echo "$1"; exit 6; }
oprw_guard_seg() {
  p="$1"; lbl="$2"; cur=""
  case "$p" in /*) ;; *) oprw_fail "OPRUNWAY_ACLNN_GUARD_FAIL $lbl 非绝对路径: $p" ;; esac
  oldifs="$IFS"; IFS='/'; set -f
  for seg in $p; do
    [ -n "$seg" ] || continue
    cur="$cur/$seg"
    if [ -L "$cur" ]; then IFS="$oldifs"; set +f; oprw_fail "OPRUNWAY_ACLNN_GUARD_FAIL $lbl 路径段是软链: $cur"; fi
  done
  IFS="$oldifs"; set +f
}
oprw_guard_root() {
  r="$1"; lbl="$2"
  oprw_guard_seg "$r" "$lbl"
  if [ -e "$r" ]; then
    [ -d "$r" ] || oprw_fail "OPRUNWAY_ACLNN_GUARD_FAIL $lbl 不是目录: $r"
    [ -O "$r" ] || oprw_fail "OPRUNWAY_ACLNN_GUARD_FAIL $lbl 非当前用户所有（共享机换靶面）: $r"
    if [ -n "$(find "$r" -maxdepth 0 \( -perm -0020 -o -perm -0002 \) 2>/dev/null)" ]; then
      oprw_fail "OPRUNWAY_ACLNN_GUARD_FAIL $lbl 可被同组/他人写: $r"
    fi
  fi
}
oprw_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else echo ""; fi
}
oprw_setenv() {
  se="$1"
  oprw_guard_seg "$se" setenv
  [ -f "$se" ] && [ -r "$se" ] || { echo "OPRUNWAY_ACLNN_SETENV_MISSING $se"; exit 2; }
  set +u; source "$se"; rc=$?; set -u
  [ $rc -eq 0 ] || { echo "OPRUNWAY_ACLNN_SETENV_FAIL $se rc=$rc"; exit 2; }
  [ -n "${ASCEND_TOOLKIT_HOME:-}" ] || { echo OPRUNWAY_ACLNN_NO_TOOLKIT; exit 2; }
  [ -d "$ASCEND_TOOLKIT_HOME" ] || { echo "OPRUNWAY_ACLNN_NO_TOOLKIT $ASCEND_TOOLKIT_HOME"; exit 2; }
  [ -d "$ASCEND_TOOLKIT_HOME/lib64" ] || { echo "OPRUNWAY_ACLNN_NO_TOOLKIT_LIB $ASCEND_TOOLKIT_HOME"; exit 2; }
  OPRW_TKVER=$( { cat "$ASCEND_TOOLKIT_HOME/version.cfg" 2>/dev/null || cat "$ASCEND_TOOLKIT_HOME/version.info" 2>/dev/null || true; } | tr -c 'A-Za-z0-9._:=-' '_' )
  [ -n "$OPRW_TKVER" ] || OPRW_TKVER=unknown
  echo "OPRUNWAY_ACLNN_ENV toolkit=$ASCEND_TOOLKIT_HOME tkver=$OPRW_TKVER"
}
'''


def _build_args(cfg):
    """仓根 build.sh 的实参串（**单一事实源**：脚本与 provenance stamp 共用，杜绝两处漂移）。
    值均已过白名单（soc/_SOC_RE、snake_op/vendor_name/_check_id），无 shell 元字符。"""
    return (f"--pkg --experimental --soc={cfg['soc']} --ops={cfg['snake_op']} "
            f"--vendor_name={cfg['vendor_name']} --no_force")


def _prov_prefix(cfg, symbols):
    """build 产物的 **provenance 指纹前缀**（stamp 第一行的静态部分；toolkit/版本在远端补齐）。

    审计 High#1：旧幂等门只看 `libcust_opapi.so` **在不在**——不绑仓 / PR commit / op / SoC / 构建参数，
    于是「复用工作目录验收新 PR」会实际跑**上一个 PR 的 .so**，却照产 BUILD_DONE 与验收证据。
    对齐本项目 canon 已有的「opp provenance-bound + fail-closed 门」先例：指纹不符即清理重建。"""
    return ("repo=" + cfg["base_repo"] + "|ref=" + cfg["pr_ref"] + "|sha=" + cfg["head_sha"]
            + "|subdir=" + cfg["op_subdir"] + "|op=" + cfg["snake_op"] + "|soc=" + cfg["soc"]
            + "|vendor=" + cfg["vendor_name"] + "|args=" + _build_args(cfg)
            + "|syms=" + ",".join(symbols) + "|")


def _reuse_build(cfg):
    """是否允许复用已装 vendor：**验收模式默认强制重建**（不给「缓存 .so 冒充新 PR」留口子）。
    须显式 `OPRUNWAY_ACLNN_REUSE_BUILD=1` 才开，且开了也必须逐项校 provenance stamp；
    `OPRUNWAY_ACLNN_REBUILD=1` 一票否决复用。"""
    if os.environ.get("OPRUNWAY_ACLNN_REBUILD") == "1":
        return False
    return os.environ.get("OPRUNWAY_ACLNN_REUSE_BUILD") == "1"


def _build_install_script(cfg, paths, symbols):
    """组装容器内 **取源→依赖门→build→install** 一段 shell（§9.6 实测配方 + provenance 门）。

    · 取源：`git init` + **强制** `remote set-url origin`（配置的 base_repo 不再被已存在的 .git 静默忽略）
      + `fetch --depth 1` + `reset --hard FETCH_HEAD` + `clean -ffdx`（清残留未跟踪文件/旧 build_out），
      随后 `rev-parse HEAD` 与期望 head SHA **比对**，不符即 fail-closed（审计 High#2/#3）；
    · 依赖门：`--pkg` 硬门要 pigz(≥2.4)+dos2unix，缺则装零联网 shim（pigz→gzip 剥 -p、dos2unix→sed）前置 PATH；
    · build：先 `rm -rf build_out`，再 `build.sh <_build_args>`；产物要求**恰好一个本轮新包**（旧版
      `ls *.run | head -n1` 任取一个，可能装上一轮的包）；
    · install：`--install-path=<vendor_dir>`（用户目录，绝不写共享 opp）→ 校 `libcust_opapi.so` 存在
      **且确有本次 caseset 需要的 aclnn 符号** → 写 provenance stamp（含 .so SHA256）。
    · 复用：默认关（`_reuse_build`）；开了也要 stamp 逐项相符 + .so 指纹相符 + 符号在，任一不符→清 vendor 重建。
    值经白名单校验（_aclnn_paths / _aclnn_cfg）+ shlex.quote 纵深防御；`${...}` 保持字面（占位符替换、非 f-string）。"""
    import shlex
    q = shlex.quote
    if not symbols:
        raise ValueError("_build_install_script 需要非空 aclnn 符号集（用于核验 .so 里确有被测算子）——fail-closed")
    for s in symbols:
        if not _SYMBOL_RE.fullmatch(s):
            raise ValueError(f"非法 aclnn 符号名 {s!r}（须 C 标识符）")
    proxy_prefix = (f"http_proxy={q(cfg['proxy'])} https_proxy={q(cfg['proxy'])} " if cfg["proxy"] else "")
    tmpl = _SH_GUARDS + r'''oprw_setenv @@SETENV@@
VROOT=@@VENDOR_DIR@@
RROOT=@@RROOT@@
oprw_guard_root "$VROOT" vendor_dir
oprw_guard_root "$RROOT" remote_dir
VC="$VROOT/vendors/@@VENDOR_NAME@@_nn"
LIB="$VC/op_api/lib/libcust_opapi.so"
STAMP="$VC/oprunway_build_provenance.txt"
WANT=@@PROV_PREFIX@@"toolkit=$ASCEND_TOOLKIT_HOME|tkver=$OPRW_TKVER"
oprw_check_syms() {
  for s in @@SYMBOLS@@; do
    if command -v nm >/dev/null 2>&1 && nm -D "$1" 2>/dev/null | grep -q -- "$s"; then continue; fi
    if grep -a -q -F -- "$s" "$1"; then continue; fi
    echo "OPRUNWAY_ACLNN_NOSYM $s"; exit 3
  done
}
if [ @@REUSE@@ = 1 ] && [ -f "$LIB" ] && [ -f "$STAMP" ]; then
  oprw_guard_seg "$VC" vendor_content
  oprw_guard_seg "$LIB" vendor_lib
  GOT_PROV=$(sed -n '1p' "$STAMP" || true)
  GOT_SO=$(sed -n '2p' "$STAMP" || true)
  CUR_SO=$(oprw_sha256 "$LIB")
  if [ "$GOT_PROV" = "prov=$WANT" ] && [ -n "$CUR_SO" ] && [ "$GOT_SO" = "so=$CUR_SO" ]; then
    oprw_check_syms "$LIB"
    echo "OPRUNWAY_ACLNN_HEAD_SHA=@@HEAD_SHA@@"
    echo OPRUNWAY_ACLNN_BUILD_SKIP
    echo OPRUNWAY_ACLNN_BUILD_DONE
    exit 0
  fi
  echo OPRUNWAY_ACLNN_STAMP_MISMATCH
fi
oprw_guard_seg "$VC" vendor_content
rm -rf -- "$VC"
SHIM="$RROOT/_shimbin"
oprw_guard_seg "$SHIM" shim
mkdir -p -- "$SHIM"
if ! command -v pigz >/dev/null 2>&1; then
cat > "$SHIM/pigz" <<'PIGZ_EOF'
#!/bin/sh
a=""
while [ $# -gt 0 ]; do
  case "$1" in
    -p) shift 2 ;;
    -p*) shift ;;
    *) a="$a $1"; shift ;;
  esac
done
exec gzip $a
PIGZ_EOF
chmod +x "$SHIM/pigz"
fi
if ! command -v dos2unix >/dev/null 2>&1; then
cat > "$SHIM/dos2unix" <<'D2U_EOF'
#!/bin/sh
for f in "$@"; do
  case "$f" in -*) continue ;; esac
  sed -i 's/\r$//' "$f"
done
D2U_EOF
chmod +x "$SHIM/dos2unix"
fi
export PATH="$SHIM:$PATH"
CKO=@@CHECKOUT@@
oprw_guard_seg "$CKO" checkout
mkdir -p -- "$CKO"
cd "$CKO" || { echo OPRUNWAY_ACLNN_FETCH_FAIL; exit 3; }
[ -d .git ] || git init -q
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin @@BASE_REPO@@ || { echo OPRUNWAY_ACLNN_FETCH_FAIL; exit 3; }
else
  git remote add origin @@BASE_REPO@@ || { echo OPRUNWAY_ACLNN_FETCH_FAIL; exit 3; }
fi
@@PROXY@@git fetch --depth 1 origin @@PR_REF@@ || { echo OPRUNWAY_ACLNN_FETCH_FAIL; exit 3; }
git reset -q --hard FETCH_HEAD || { echo OPRUNWAY_ACLNN_FETCH_FAIL; exit 3; }
git clean -q -ffdx || { echo OPRUNWAY_ACLNN_FETCH_FAIL; exit 3; }
GOT_SHA=$(git rev-parse HEAD 2>/dev/null || true)
if [ "$GOT_SHA" != @@HEAD_SHA@@ ]; then
  echo "OPRUNWAY_ACLNN_HEAD_MISMATCH got=$GOT_SHA want=@@HEAD_SHA@@"; exit 3
fi
echo "OPRUNWAY_ACLNN_HEAD_SHA=$GOT_SHA"
[ -d "$CKO/@@OP_SUBDIR@@" ] || { echo "OPRUNWAY_ACLNN_NO_OP_SUBDIR @@OP_SUBDIR@@"; exit 3; }
rm -rf -- build_out
bash build.sh @@BUILD_ARGS@@ || { echo OPRUNWAY_ACLNN_BUILD_FAIL; exit 3; }
shopt -s nullglob
RUNS=(build_out/*.run)
shopt -u nullglob
[ ${#RUNS[@]} -ge 1 ] || { echo OPRUNWAY_ACLNN_NORUN; exit 3; }
[ ${#RUNS[@]} -eq 1 ] || { echo "OPRUNWAY_ACLNN_RUNPKG_AMBIGUOUS count=${#RUNS[@]}"; exit 3; }
RUN="${RUNS[0]}"
bash "$RUN" --quiet --install-path=@@VENDOR_DIR@@ || { echo OPRUNWAY_ACLNN_INSTALL_FAIL; exit 3; }
[ -f "$LIB" ] || { echo OPRUNWAY_ACLNN_NOLIB; exit 3; }
oprw_guard_seg "$LIB" vendor_lib
oprw_check_syms "$LIB"
CUR_SO=$(oprw_sha256 "$LIB")
printf '%s\n%s\n' "prov=$WANT" "so=$CUR_SO" > "$STAMP" || { echo OPRUNWAY_ACLNN_STAMP_FAIL; exit 3; }
[ -n "$CUR_SO" ] || echo OPRUNWAY_ACLNN_STAMP_PARTIAL
echo OPRUNWAY_ACLNN_BUILD_DONE
'''
    # vendor_name / soc / snake_op / op_subdir / head_sha / 符号 已过白名单（无 shell 元字符）→ 原样注入
    # （它们要么处在 `_nn` 拼接上下文、要么须被 shell 词分割）；其余路径 / URL / ref 一律 shlex.quote。
    repl = {"@@SETENV@@": q(cfg["setenv"]), "@@VENDOR_DIR@@": q(cfg["vendor_dir"]),
            "@@VENDOR_NAME@@": cfg["vendor_name"], "@@REUSE@@": "1" if _reuse_build(cfg) else "0",
            "@@RROOT@@": q(cfg["rroot"]), "@@CHECKOUT@@": q(paths["checkout"]),
            "@@BASE_REPO@@": q(cfg["base_repo"]), "@@PROXY@@": proxy_prefix,
            "@@PR_REF@@": q(cfg["pr_ref"]), "@@HEAD_SHA@@": cfg["head_sha"],
            "@@OP_SUBDIR@@": cfg["op_subdir"], "@@BUILD_ARGS@@": _build_args(cfg),
            "@@PROV_PREFIX@@": q(_prov_prefix(cfg, symbols)), "@@SYMBOLS@@": " ".join(symbols)}
    return _render(tmpl, repl)


def _deploy_reset_script(cfg, paths):
    """部署前的**清目录**一段 shell（原先是裸 `rm -rf -- <rcases> <rout> && mkdir -p`）。

    审计 High#5：远端删除前必须在**同一个 shell 内**再守一道——逐段拒软链（软链段会把 rm -rf 引到别处）、
    专用根须归当前用户且不可被他人写（共享机）、删除目标须**严格落在**工作根之下且不与 vendor 相交
    （Python 侧 `_aclnn_paths` 已做词法相交守卫，这里是运行时的第二道）。"""
    import shlex
    q = shlex.quote
    tmpl = _SH_GUARDS + r'''RROOT=@@RROOT@@
oprw_guard_root "$RROOT" remote_dir
oprw_guard_root @@VENDOR_DIR@@ vendor_dir
for d in @@RCASES@@ @@ROUT@@; do
  oprw_guard_seg "$d" 用例/输出目录
  case "$d" in
    "$RROOT"/?*) ;;
    *) oprw_fail "OPRUNWAY_ACLNN_GUARD_FAIL 删除目标不在工作根之下: $d" ;;
  esac
  rm -rf -- "$d"
done
mkdir -p -- @@RCASES@@ "$RROOT/aclnn_runtime"
echo OPRUNWAY_ACLNN_DEPLOY_RESET_DONE
'''
    return _render(tmpl, {"@@RROOT@@": q(cfg["rroot"]), "@@VENDOR_DIR@@": q(cfg["vendor_dir"]),
                          "@@RCASES@@": q(paths["rcases"]), "@@ROUT@@": q(paths["rout"])})


_RUNTIME_FILES = ("__init__.py", "base.py", "acl_consts.py", "aclnn_runner.py",
                  "aclnn_driver.py", "perf_msprof.py")

# 运行时 env 前置段（exec / perf 共用，避免两份漂移）。占位符由 `_render` 替换。
# ⚠ `source ... || true` 已按审计 Medium#7 改为 `oprw_setenv`（失败立即退出 + 校 CANN 环境真起来），
#   否则 set_env.sh 加载失败会被吞掉、跑在登录 shell 里残留的另一套 CANN 上。
_ENV_PREAMBLE = _SH_GUARDS + r'''oprw_setenv @@SETENV@@
VROOT=@@VENDOR_DIR@@
oprw_guard_root "$VROOT" vendor_dir
VC="$VROOT/vendors/@@VENDOR_NAME@@_nn"
oprw_guard_seg "$VC" vendor_content
[ -d "$VC" ] || { echo "OPRUNWAY_ACLNN_NO_VENDOR $VC"; exit 2; }
export ASCEND_OPP_PATH="$VROOT"
export ASCEND_CUSTOM_OPP_PATH="$VC:${ASCEND_CUSTOM_OPP_PATH:-}"
export LD_LIBRARY_PATH="$VC/op_api/lib:${ASCEND_TOOLKIT_HOME}/lib64:${ASCEND_TOOLKIT_HOME}/devlib:${LD_LIBRARY_PATH:-}"
cd @@RROOT@@ || { echo OPRUNWAY_ACLNN_NO_RROOT; exit 2; }
'''


def _exec_script(cfg, paths):
    """组装容器内 **运行时 env + 跑 aclnn_driver** 一段 shell（§9.6 运行时 env；driver 只产 out_k.bin、不判定）。

    运行时 env（§9.6）：`ASCEND_CUSTOM_OPP_PATH` 指 vendor 内容根、`LD_LIBRARY_PATH` 前置 vendor op_api/lib
    + CANN lib64/devlib。⚠ 另置 `ASCEND_OPP_PATH=<vendor_dir>`（install-path）：`aclnn_runner._find_custom_opapi_libs`
    以 `Path($ASCEND_OPP_PATH).glob('vendors/*/op_api/lib/libcust_opapi.so')` **单路径** glob custom lib——
    须指到含 `vendors/` 的 install-path 才 glob 得到（不改 aclnn_runner，令 env 迁就其契约）。真机待验。"""
    import shlex
    q = shlex.quote
    tmpl = _ENV_PREAMBLE + (
        'python -m aclnn_runtime.aclnn_driver @@CASESET@@ @@ROUT@@ --work-dir @@RCASES@@ '
        '--device @@DEVICE@@ && echo OPRUNWAY_ACLNN_EXEC_DONE '
        '|| { echo OPRUNWAY_ACLNN_EXEC_FAIL; exit 4; }\n')
    repl = {"@@SETENV@@": q(cfg["setenv"]), "@@VENDOR_DIR@@": q(cfg["vendor_dir"]),
            "@@VENDOR_NAME@@": cfg["vendor_name"], "@@RROOT@@": q(cfg["rroot"]),
            "@@CASESET@@": q(paths["rcases"] + "/caseset.json"), "@@ROUT@@": q(paths["rout"]),
            "@@RCASES@@": q(paths["rcases"]), "@@DEVICE@@": str(cfg["device"])}
    return _render(tmpl, repl)


# ── perf：kernel-only msprof 采集（custom ctypes-aclnn vs torch_npu 基线）────────────

#: OpRunway 侧据 spec 落的性能采集计划（run_workflow 写 → 本模块读 → 随部署上送容器）。
PERF_PLAN_FILE = "_perf_plan.json"
#: 采集端产物（容器内 perf_msprof 落 → 拉回 work/）。
PERF_COLLECT_FILE = "perf_collect.json"
#: 供 run_workflow 的 `_REAL_BASELINE_SOURCES["torch_npu"]` 消费的真基线文件名。
TORCH_NPU_BASELINE_FILE = "_torch_npu_baseline.json"


def _perf_script(cfg, paths):
    """组装容器内 **msprof 性能采集** 一段 shell（`python -m aclnn_runtime.perf_msprof`）。

    与 exec 共用运行时 env 前置段；另置 `OPRUNWAY_ACLNN_REAL=1`——采集模块自己有真机 gate
    （`perf_msprof._require_real_gate`），容器侧不显式带上就跑不起来（fail-closed 方向正确）。
    采集只产计时数与行为分类，**不判定**（裁决归 perf_compare）。
    """
    import shlex
    q = shlex.quote
    tmpl = _ENV_PREAMBLE + (
        'export OPRUNWAY_ACLNN_REAL=1\n'
        'python -m aclnn_runtime.perf_msprof @@CASESET@@ @@PLAN@@ @@OUT@@ --work-dir @@RCASES@@ '
        '&& echo OPRUNWAY_ACLNN_PERF_DONE || { echo OPRUNWAY_ACLNN_PERF_FAIL; exit 5; }\n')
    repl = {"@@SETENV@@": q(cfg["setenv"]), "@@VENDOR_DIR@@": q(cfg["vendor_dir"]),
            "@@VENDOR_NAME@@": cfg["vendor_name"], "@@RROOT@@": q(cfg["rroot"]),
            "@@CASESET@@": q(paths["rcases"] + "/caseset.json"),
            "@@PLAN@@": q(paths["rcases"] + "/" + PERF_PLAN_FILE),
            "@@OUT@@": q(paths["rout"] + "/" + PERF_COLLECT_FILE),
            "@@RCASES@@": q(paths["rcases"])}
    return _render(tmpl, repl)


def load_perf_plan(work):
    """读 `work/_perf_plan.json`（run_workflow 据 spec.perf 落）。不存在 → None（= 本次不采性能）。"""
    import json
    path = os.path.join(work, PERF_PLAN_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    if not isinstance(plan, dict):
        raise ValueError(f"{PERF_PLAN_FILE} 须为 JSON object，得 {type(plan).__name__}")
    return plan


def _perf_enabled(plan):
    """是否采性能：须有 plan、且真机 gate 已开、且未显式关（`OPRUNWAY_ACLNN_PERF=0`）。

    默认**开**——「没采到」必须表现为 `us=None` → perf_compare blocked，而不是悄悄跳过。
    """
    if not plan:
        return False
    if os.environ.get("OPRUNWAY_ACLNN_REAL") != "1":
        return False
    return os.environ.get("OPRUNWAY_ACLNN_PERF", "1") != "0"


def collect_perf(cfg, paths, caseset, work, evidence_list, plan):
    """真机性能采集编排：精度先筛 → 选 case → 上送 plan → 容器内 msprof → 拉回 → 组两份产物。

    返回 `(custom_perf_by_case, baseline_doc, notes)`。**任何一步失败都不抛穿**（由调用方落成
    `us=None` + 空基线 → perf_compare 逐 case blocked）——但失败原因原样进 `notes` 与基线的
    `excluded`，**绝不静默、绝不冒充达标**。
    """
    import json
    import repo_adapter as RA
    from aclnn_runtime import perf_msprof as PM

    host = cfg["host"]
    notes = []
    # ① 精度先筛：只对**已过精度**的 case 测性能（算错的快不算快）。judge 复用 validator 那一套。
    try:
        pass_ids = PM.accuracy_pass_ids(evidence_list)
    except Exception as exc:                     # noqa: BLE001 —— 前筛失败即不筛，宁可多测也不误删
        pass_ids = None
        notes.append(f"精度前筛不可用（{exc}）→ 本次未做精度先筛（accuracy_filter=not_applied）")
    selected, skipped = PM.select_perf_cases(caseset, pass_ids)
    if not selected:
        notes.append("无可测性能的用例（无「性能」维用例，或全部未过精度先筛）")
        return {}, PM.build_baseline_document([], op=caseset.get("op"), skipped=skipped), notes

    # ② 上送 plan（含 torch 基线调用映射；缺映射 → 采集端 fail-closed，不猜 torch 形参）。
    remote_plan = {"op": caseset.get("op"),
                   "warmup": int(plan.get("warmup", PM.DEFAULT_WARMUP)),
                   "repeat": int(plan.get("repeat", PM.DEFAULT_REPEAT)),
                   "device": int(cfg["device"]),
                   "op_dir": plan.get("op_dir"),
                   "torch_baseline": plan.get("torch_baseline"),
                   "cases": selected, "skipped": skipped}
    plan_local = os.path.join(work, "_aclnn_perf_plan_sent.json")
    with open(plan_local, "w", encoding="utf-8") as f:
        json.dump(remote_plan, f, ensure_ascii=False)
    RA._copy_to(host, plan_local, paths["rcases"] + "/" + PERF_PLAN_FILE, timeout=120)

    # ③ 容器内采集（单独一段 shell：FAIL 先解耦 root-cause 再归因——采集失败 ≠ 精度通路失败）。
    script = _perf_script(cfg, paths)
    rp = RA._shell(host, script, timeout=7200, check=False, capture=True)
    blob = (rp.stdout or "") + (rp.stderr or "")
    if rp.returncode != 0 or "OPRUNWAY_ACLNN_PERF_DONE" not in blob:
        notes.append(f"[aclnn_py] 真机性能采集失败 rc={rp.returncode}（精度证据不受影响；"
                     f"性能一律 us=None → perf_compare 挂起，不冒充达标）:\n{blob[-1500:]}")
        return {}, PM.build_baseline_document([], op=caseset.get("op"), skipped=skipped), notes

    # ④ 拉回采集结果 → 组 custom us map + torch_npu 基线文档。
    local_collect = os.path.join(work, PERF_COLLECT_FILE)
    RA._copy_from(host, paths["rout"] + "/" + PERF_COLLECT_FILE, local_collect,
                  timeout=300, check=True)
    with open(local_collect, encoding="utf-8") as f:
        doc = json.load(f)
    records = doc.get("records") or []
    custom_map = PM.build_custom_perf_map(records, skipped=skipped)
    baseline = PM.build_baseline_document(records, op=caseset.get("op"),
                                          warmup=remote_plan["warmup"],
                                          repeat=remote_plan["repeat"], skipped=skipped)
    return custom_map, baseline, notes


# ── deploy / build / exec / collect（真机编排；gated，真机待验）──────────────────────────────

def _run_aclnn_real(cfg, proj, caseset, work_dir, out_dir):
    """真机 build(取源→依赖门→build→install，provenance 绑定)→deploy(runtime+caseset+输入)→exec→collect(out_k.bin)。

    **真机待验、gated**。复用 repo_adapter 传输原语（_shell/_copy_to/_copy_from，local/remote 同构、已过 ID/路径注入校验）。
    build 复用**默认关**（审计 High#1）：开 `OPRUNWAY_ACLNN_REUSE_BUILD=1` 也必须 provenance stamp
    （仓 URL + head SHA + op_subdir + snake_op + SoC + vendor + 构建参数 + 符号集 + toolkit + .so SHA256）逐项相符，
    任一不符即清 vendor 重建；`OPRUNWAY_ACLNN_REBUILD=1` 一票否决复用。
    exec：容器内 `python -m aclnn_runtime.aclnn_driver <caseset> <out_dir> --work-dir <cases> --device N`（driver 只产 out_k.bin）。
    build 与 exec **分两段 _shell**（FAIL 先解耦 root-cause 再归因，§5.3）。

    返回 **build provenance dict**（head_sha / 是否复用 / toolkit 行 …），由 `run_aclnn_py` 写进 evidence envelope
    ——「这份证据由哪个 commit 的 .so 产出」必须留痕，否则证据无从追溯（审计 High#1/#2）。
    """
    import json
    import shlex
    import repo_adapter as RA

    host, rroot = cfg["host"], cfg["rroot"]
    if host is not None:
        RA._check_id("host", host)
    RA._check_id("op_name", caseset["op"])
    paths = _aclnn_paths(cfg)                                   # 路径 + 安全校验（含 soc/vendor/device/相交守卫）
    symbols = _required_symbols(caseset)                        # 字段驱动：本次要在 .so 里核到的 aclnn 符号
    q = shlex.quote
    here = os.path.dirname(os.path.abspath(__file__))
    rcases, rout = paths["rcases"], paths["rout"]
    case_ids = []
    for c in caseset["cases"]:
        RA._check_id("case_id", c["id"])
        case_ids.append(c["id"])

    # local 模式下 rroot/vendor_dir 是**本机真实目录**，且下面 §deploy 对 rcases/rout 执行 `rm -rf`。
    # 必须与 work_dir 双向不相交，否则用户把 rroot 指到含产物的目录 → 静默删（承 run_new_example 同守卫）。
    if host is None:
        wd = os.path.realpath(work_dir)
        for k, p in (("remote_dir", rroot), ("vendor_dir", cfg["vendor_dir"])):
            rp = os.path.realpath(p)
            if RA._contains(wd, rp) or RA._contains(rp, wd):
                raise ValueError(
                    f"local 模式下 {k}={p!r} 与 work_dir={work_dir!r} 相交——部署会对其子目录执行 rm -rf，"
                    f"拒绝以防误删。请指向独立的专用 scratch 目录。")

    # 1) build + install（provenance 绑定、按 §9.6 实测配方；单独一段 shell，失败可精确归因取源/依赖/build/install）
    bscript = _build_install_script(cfg, paths, symbols)
    rb = RA._shell(host, bscript, timeout=3600, check=False, capture=True)
    bblob = (rb.stdout or "") + (rb.stderr or "")
    if rb.returncode != 0 or "OPRUNWAY_ACLNN_BUILD_DONE" not in bblob:
        raise RuntimeError(
            f"[aclnn_py] 真机 build/install 失败 rc={rb.returncode}"
            f"（本地已过形态核验的 DUT: {proj!r}；容器内按 PR-ref {cfg['pr_ref']!r} 重新取源 → {paths['checkout']!r}。"
            f"哨兵可解耦 root-cause：GUARD/SETENV/FETCH/HEAD_MISMATCH/BUILD/RUNPKG/INSTALL/NOLIB/NOSYM）:\n{bblob[-2000:]}")
    # 取源实得 commit 必须与期望 head SHA 一致——脚本内已比对并 fail-closed，此处再从输出取回、写进证据。
    got = re.search(r"OPRUNWAY_ACLNN_HEAD_SHA=([0-9a-fA-F]{40})", bblob)
    if not got or got.group(1).lower() != cfg["head_sha"]:
        raise RuntimeError(
            f"[aclnn_py] build 段未回报可核验的 head SHA（期望 {cfg['head_sha']}，得 "
            f"{got.group(1) if got else None}）——不接受来路不明的 .so，fail-closed:\n{bblob[-1500:]}")
    prov = {"head_sha": got.group(1).lower(), "pr_ref": cfg["pr_ref"], "base_repo": cfg["base_repo"],
            "op_subdir": cfg["op_subdir"], "snake_op": cfg["snake_op"], "soc": cfg["soc"],
            "vendor_name": cfg["vendor_name"], "build_args": _build_args(cfg), "symbols": list(symbols),
            "build_reused": "OPRUNWAY_ACLNN_BUILD_SKIP" in bblob,
            "stamp_mismatch_rebuilt": "OPRUNWAY_ACLNN_STAMP_MISMATCH" in bblob,
            "so_digest_unavailable": "OPRUNWAY_ACLNN_STAMP_PARTIAL" in bblob}
    env_line = re.search(r"OPRUNWAY_ACLNN_ENV toolkit=(\S+) tkver=(\S+)", bblob)
    if env_line:
        prov["toolkit"], prov["toolkit_version"] = env_line.group(1), env_line.group(2)

    # 2) deploy：caseset.json（含逐 case aclnn_call）+ 各 case 输入张量 + aclnn_runtime 子包 → 容器工作目录。
    #    （golden 在 OpRunway 侧另读，不上送——判定不在真机。）
    rr = RA._shell(host, _deploy_reset_script(cfg, paths), timeout=120, check=False, capture=True)
    rblob = (rr.stdout or "") + (rr.stderr or "")
    if rr.returncode != 0 or "OPRUNWAY_ACLNN_DEPLOY_RESET_DONE" not in rblob:
        raise RuntimeError(f"[aclnn_py] 部署清目录失败/被守卫拦下 rc={rr.returncode}:\n{rblob[-1500:]}")
    cs_local = os.path.join(work_dir, "_aclnn_caseset.json")
    with open(cs_local, "w", encoding="utf-8") as f:
        json.dump(caseset, f, ensure_ascii=False)
    RA._copy_to(host, cs_local, rcases + "/caseset.json", timeout=120)
    for c in caseset["cases"]:
        RA._shell(host, f"mkdir -p -- {q(rcases + '/' + c['id'])}\n", timeout=60, check=True)
        for inp in c["inputs"]:
            # 审计 High#4：相对路径过白名单 + canonical + 首段=本 case ID（词法），再逐段拒软链（物理），
            # 最后远端目标路径过 `_check_remote_path`——三道都过才敢拼进 `host:path` 交给 scp。
            rel = _safe_case_rel(inp.get("path"), case_ids, f"case {c['id']} 输入 {inp.get('name')!r}",
                                 expect_cid=c["id"])
            local = RA._safe(work_dir, rel)
            _reject_symlink_rel(work_dir, rel, f"case {c['id']} 输入 {inp.get('name')!r}")
            remote = rcases + "/" + rel
            RA._check_remote_path("input_remote_path", remote)
            RA._copy_to(host, local, remote, timeout=120)
    for fn in _RUNTIME_FILES:
        RA._copy_to(host, os.path.join(here, "aclnn_runtime", fn),
                    rroot + "/aclnn_runtime/" + fn, timeout=60)

    # 3) exec：容器内跑 driver（运行时 env 指用户态 vendor；判定不在此）。
    escript = _exec_script(cfg, paths)
    re_ = RA._shell(host, escript, timeout=2400, check=False, capture=True)
    eblob = (re_.stdout or "") + (re_.stderr or "")
    if re_.returncode != 0 or "OPRUNWAY_ACLNN_EXEC_DONE" not in eblob:
        raise RuntimeError(f"[aclnn_py] 真机 exec 失败 rc={re_.returncode}:\n{eblob[-2000:]}")

    # 4) collect：拉回 out_manifest.json + 各 out_k.bin（据 manifest 逐个拉）。
    #    ⚠ manifest 来自**远端**（审计 High#4：它是不可信输入）——其 `path` 与 `case_id` 必须过与输入侧
    #    同一套守卫（白名单 / canonical / 首段=该 case ID / 逐段拒软链），才敢拼进 scp 源与本地写入目标。
    os.makedirs(out_dir, exist_ok=True)
    RA._copy_from(host, rout + "/out_manifest.json",
                  os.path.join(out_dir, "out_manifest.json"), timeout=120, check=True)
    with open(os.path.join(out_dir, "out_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    for rec in manifest.get("produced", []):
        cid = rec.get("case_id")
        RA._check_id("manifest case_id", cid)
        if cid not in case_ids:
            raise ValueError(f"远端 manifest 的 case_id={cid!r} 不在本次 caseset 内——拒绝拉回来路不明的产物")
        for o in rec.get("outputs", []):
            rel = _safe_case_rel(o.get("path"), case_ids, f"远端 manifest(case {cid}) 输出", expect_cid=cid)
            remote = rout + "/" + rel
            RA._check_remote_path("output_remote_path", remote)
            _reject_symlink_rel(out_dir, rel, f"本地产物落点(case {cid})")
            RA._copy_from(host, remote, RA._safe(out_dir, rel), timeout=120, check=True)
    return prov


def run_aclnn_py(caseset, work, defect_cases=None):
    """aclnn_py 验收通路（ctypes-aclnn runner form）：build/install/deploy/exec/collect → OpRunway 侧组 evidence。

    返回 evidence envelope（`evidence_grade="acceptance_candidate"`、`runner_source="user"`、
    `runner_form="aclnn_py"`）。judge/perf 归 validator/perf_compare（本模块不判定）。

    ⚠ `defect_cases` 只为与 MODES 统一签名；**验收路径非空即拒**（C5——造坏点只在 mock 测试夹具做）。
    ⚠ 真机 exec gated：须 `OPRUNWAY_ACLNN_REAL=1`（并已人工确认 build install 写用户态 vendor 目录的副作用）。
    """
    if defect_cases:
        raise ValueError("run_aclnn_py 不接受 defect 注入——aclnn_py 是验收路径；造坏点只在 mock 测试夹具做（C5）")
    import repo_adapter as RA
    op = caseset["op"]
    RA._check_id("op_name", op)
    cfg = _aclnn_cfg()
    proj = find_aclnn_project(op, cfg["ops_root"], cfg["op_subdir"])   # ops 仓形态核验（缺件 fail-closed）

    # 输入 dtype 白名单据 runner_form 分派（aclnn_py 放开 int/bf16；蓝图 §6）。未支持 → fail-closed。
    allowed = RA.supported_np("aclnn_py")
    for c in caseset["cases"]:
        for inp in c["inputs"]:
            if inp["dtype"] not in allowed:
                raise ValueError(f"{c['id']} 输入 {inp['name']} dtype={inp['dtype']!r} 不在 aclnn_py 可收发集 "
                                 f"{sorted(allowed)}——fail-closed")

    out_dir = os.path.join(work, "aclnn_out")
    if os.environ.get("OPRUNWAY_ACLNN_REAL") != "1":
        raise RuntimeError(
            "aclnn_py 真机路径未启用——待 a3 `oprunway_prov` 容器（CANN 9.0.1 / torch_npu 2.10）+ 人工确认。\n"
            "  本地已完成：DUT 定位（find_aclnn_project ops 仓形态 + 软链守卫）、输入 dtype 白名单、build/install/exec/collect"
            " 编排（按 §9.6 实测配方）、evidence 组装管路（repo_adapter.build_multi_output_evidence 对拉回 out_k.bin 复算 metrics）。\n"
            "  真取源/build.sh install / ctypes 9.0.1 运行时 / 多输出 arity / bf16 窄化须真机（de-risk D0-D2 已坐实配方，端到端待接）。\n"
            "  确须真机跑请设 OPRUNWAY_ACLNN_REAL=1（并已人工确认 build install 写用户态 vendor 目录的副作用）。")
    _run_aclnn_real(cfg, proj, caseset, work, out_dir)
    # ① 先组精度 evidence（perf 未采 → us=None 占位）；精度先筛要用它的 policy+metrics。
    evidence = RA.build_multi_output_evidence(caseset, work, out_dir)
    envelope = {"op": op, "repo_mode": "aclnn_py", "evidence_grade": "acceptance_candidate",
                "runner_source": "user", "runner_path": os.path.join(proj, "build.sh"),
                "runner_form": "aclnn_py", "evidence": evidence}

    # ② 性能采集（kernel-only msprof + torch_npu 真机内基线）。**采不到就是 us=None**——
    #    下游 perf_compare 缺基线即挂起，绝不兜底、绝不冒充达标（承 run_workflow 的 High#2 纪律）。
    plan = load_perf_plan(work)
    if not _perf_enabled(plan):
        envelope["perf_collection"] = {
            "collected": False,
            "reason": ("缺 work/_perf_plan.json（spec 未声明 perf 采集计划）" if not plan
                       else "OPRUNWAY_ACLNN_PERF=0（显式关闭本次性能采集）"),
            "note": "性能一律 us=None → perf_compare 挂起（BLOCKED），不构成性能结论"}
        return envelope
    import json
    custom_map, baseline_doc, notes = collect_perf(
        cfg, _aclnn_paths(cfg), caseset, work, evidence, plan)
    # 把采到的 us 回填进已组好的 evidence（perf 与精度判定完全解耦，回填不影响任何精度字段）。
    for item in evidence:
        item["perf"] = RA._perf_entry(item.get("case_id"), custom_map)
    with open(os.path.join(work, TORCH_NPU_BASELINE_FILE), "w", encoding="utf-8") as f:
        json.dump(baseline_doc, f, ensure_ascii=False, indent=2)
    envelope["perf_collection"] = {
        "collected": True, "baseline_file": TORCH_NPU_BASELINE_FILE,
        "timed_cases": len(baseline_doc.get("per_case") or []),
        "excluded": baseline_doc.get("excluded") or [], "notes": notes}
    for n in notes:
        print(f"[aclnn_py perf] ⚠ {n}")
    return envelope


# 注册进 repo_adapter.MODES（比照 CATLASS_MODES 的加法接入范式）。
ACLNN_MODES = {"aclnn_py": run_aclnn_py}
