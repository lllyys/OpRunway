"""Task 2 · repo_adapter — caseset.json -> evidence.json（纯采集、不判定）。

统一接口，按仓/模式换实现：
- mock          : 本地干跑，kernel 输出 = golden（可注入 defect），perf = 确定性 mock。无需 NPU。
- new_example   : 真机 build/run PR 自带工程（留桩，需 NPU + VPN，之后填）。
证据只记「测到什么」（metric value / us / 路径），pass/fail 交给 validator（ADR 0007）。
"""
import hashlib, json, math, os, posixpath, re, shlex, shutil, subprocess, sys, uuid
import numpy as np
import precision_policy
import gen_cases  # T7：复用 bf16 位级 codec（_f32_to_bf16_uint16/_bf16_uint16_to_f32）+ 原生 dtype 表

_NP = {"float32": np.float32, "float16": np.float16, "bfloat16": np.float32}  # **runner-supported**（真机 new_example）；bf16 逻辑=fp32-on-grid（本轮扩，runner.cpp 加 ACL_BF16 分支）；int 仍 Track C
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")     # case_id / host / op：拒空白、slash、shell 特殊字符
_SOC_RE = re.compile(r"^ascend[0-9a-z_]+$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")  # 远端路径：拒 shell 特殊字符（防 scp/ssh 拼接注入）


def _check_id(label, val):
    """ID（host/op/vendor/case_id 等）安全校验（finding #17）：拒空白/斜杠/shell 特殊字符、
    **首字符 '-'**（防被 ssh/scp/远端命令当选项，如 '-rf'）、以及 '.'/'..'。"""
    if not isinstance(val, str) or not _ID_RE.match(val) or val.startswith("-") or val in (".", ".."):
        raise ValueError(f"非法 {label}: {val!r}（拒首字符 '-'、'.'/'..'、空白/斜杠/shell 特殊字符）")


def _check_remote_path(label, val):
    """远端路径安全校验（finding #17）：**须绝对** + 无 shell 特殊字符 + **无 '..' 组件**（防穿越）+
    各组件不以 '-' 开头（防被 rm/mkdir/tar/cp 当选项）；posixpath.normpath 兜底再核一次。"""
    if not isinstance(val, str) or not _PATH_RE.match(val):
        raise ValueError(f"非法路径 {label}: {val!r}（含非法字符）")
    if not val.startswith("/"):
        raise ValueError(f"非法路径 {label}: {val!r}（远端路径须绝对，防相对路径拼接歧义）")
    parts = val.split("/")
    if ".." in parts:
        raise ValueError(f"非法路径 {label}: {val!r}（禁 '..' 组件，防目录穿越）")
    for seg in parts:
        if seg.startswith("-"):
            raise ValueError(f"非法路径 {label}: {val!r}（组件 {seg!r} 首字符 '-'，防被当选项）")
    norm = posixpath.normpath(val)
    if not norm.startswith("/") or ".." in norm.split("/"):
        raise ValueError(f"非法路径 {label}: {val!r}（normpath 规范化后仍非绝对/含 '..'）")


def _safe(work_dir, rel):
    """把 caseset 里的相对路径钉在 work_dir 内，拒绝绝对路径 / .. 穿越。"""
    base = os.path.normpath(os.path.abspath(work_dir))
    p = os.path.normpath(os.path.join(base, rel))
    if p != base and not p.startswith(base + os.sep):
        raise ValueError(f"path escapes work_dir: {rel}")
    return p


def _expected_storage(dtype):
    """逻辑 dtype -> **物理落盘/字节 dtype 名**（白名单，findings #6/#7）：bf16→uint16、native→自身、未知→拒。
    存储 dtype 一律**从逻辑 dtype 反推**，绝不采 caseset 自声明的 storage_dtype（那是可伪造的攻击面）。"""
    if dtype == gen_cases._BF16:
        return "uint16"
    if dtype in gen_cases._NATIVE:
        return dtype
    raise ValueError(f"未知/未支持 dtype {dtype!r}（storage 白名单：{sorted(gen_cases._NATIVE)} + bfloat16）")


def materialize_input(logical, meta):
    """X_logical（numpy 逻辑值）-> X_bin 物理字节缓冲（storage dtype），**独立于 logical 另造**
    （canonical harness 职责#2/#3：喂 kernel 的物理字节与喂 golden 的逻辑值分两份）。

    finding #9：**native 路径绝不做值 cast**——`logical.dtype` 必须已等于 storage dtype，否则 ValueError
    （旧洞：`ascontiguousarray(uint16, dtype=float32)` 把 100→100.0 值转换、污染送真机的字节）。
    bf16：逻辑须 fp32-on-grid → encode 成 uint16 位模式（唯一合法的「变 dtype」路径，且是位重解释非值 cast）。"""
    dtn = meta["dtype"]
    expected = _expected_storage(dtn)                 # 未知 dtype 在此 fail-fast
    arr = np.asarray(logical)
    if dtn == gen_cases._BF16:
        if arr.dtype != np.float32:
            raise ValueError(f"materialize_input: bf16 逻辑值须 float32-on-grid，得 {arr.dtype}（拒值 cast）")
        return gen_cases._f32_to_bf16_uint16(arr)
    if str(arr.dtype) != expected:                    # native：dtype 必须已相符，不静默 cast（finding #9）
        raise ValueError(f"materialize_input: 逻辑数组 dtype={arr.dtype} ≠ 期望 storage {expected}"
                         f"（native 路径拒值 cast，防污染真机字节）")
    return np.ascontiguousarray(arr)


def readback_output(raw_storage, meta):
    """X_bin 物理字节（storage dtype）读回 -> 逻辑 numpy（与 golden 比对用）。
    bf16：uint16 位模式 -> fp32-on-grid；native：dtype 须已相符（finding #9：拒值 cast）。round-trip 与
    materialize_input 互逆（codex#9）。"""
    dtn = meta["dtype"]
    expected = _expected_storage(dtn)                 # 未知 dtype fail-fast
    arr = np.asarray(raw_storage)
    if dtn == gen_cases._BF16:
        if arr.dtype != np.uint16:
            raise ValueError(f"readback_output: bf16 物理字节须 uint16，得 {arr.dtype}")
        return gen_cases._bf16_uint16_to_f32(arr)
    if str(arr.dtype) != expected:                    # native：拒值 cast（finding #9）
        raise ValueError(f"readback_output: 物理数组 dtype={arr.dtype} ≠ 期望 storage {expected}（拒值 cast）")
    return np.ascontiguousarray(arr)


def _sha256_file(path):
    """对文件字节算 sha256（A 方案 provenance 用）。文件必须已落盘（缺失 → OSError，不静默兜 None）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _precision_evidence(case, out, golden, out_path, work_dir, ascendoptest_bool=None):
    """采集层构建 evidence.precision——**误差分布确定性复算**（compute_metrics）但**不判 pass/fail**。

    结构化 policy/tolerance_policy_id/threshold(digest) 一律**从 caseset.expected 抄**（三处一致的一环，
    adapter 不自造阈值），只 metrics 是重算。有 acceptance_policy 时另算 acceptance_metrics。

    A 方案（evidence↔产物 provenance 绑定）：对落盘的 golden/out 产物字节算 sha256 + numel，写入
    `provenance`，供门 gate_task2 **独立重算校验**——门按此路径读产物、先校 sha、再依 caseset policy 重算
    metrics 并与本处自报的 metrics 逐字段比对，不符即 FAILED。目的是让「metrics 可被证明是从磁盘产物算出」。
    ⚠ 已知边界（诚实、勿夸大）：A 只证「metrics 由 golden/out 这两文件算出」，**不证**「这两文件来自一次
       真 NPU 跑测」——同时控制产物+evidence 的攻击者把 out 写成 golden 的副本 → bad_count=0 是「真的」，
       只是它没测 NPU。产物↔真机来源的绑定须 OPRUNWAY_DONE 哨兵 / raw log hash / msprof 输出绑定（本轮不做）。
    """
    exp = case["expected"]
    policy = exp["policy"]
    prec = {"standard": exp["standard"],
            "tolerance_policy_id": exp["tolerance_policy_id"],
            "policy": policy,
            "threshold": exp["threshold"],
            # oracle_source **据 caseset 声明的 golden_source 据实映射**（不再写死 cpu_ref）：
            # torch→torch_ref、numpy→analytical_ref；来源缺失/不可识别 → fail-closed（不默认）。
            "oracle_source": precision_policy.oracle_source_from_golden(exp.get("golden_source")),
            "not_settled": bool(policy.get("not_settled", False)),
            "metrics": precision_policy.compute_metrics(out, golden, policy),
            "golden_path": exp["golden_path"], "out_path": out_path,
            "provenance": {"golden_sha256": _sha256_file(_safe(work_dir, exp["golden_path"])),
                           "out_sha256": _sha256_file(_safe(work_dir, out_path)),
                           "numel": int(np.asarray(golden).size)}}
    ap = exp.get("acceptance_policy")
    if ap:
        prec["acceptance_policy"] = ap
        prec["acceptance_tolerance_policy_id"] = exp.get("acceptance_tolerance_policy_id")
        prec["acceptance_metrics"] = precision_policy.compute_metrics(out, golden, ap)
    if ascendoptest_bool is not None or "ascendoptest_bool" in exp:
        prec["ascendoptest_bool"] = ascendoptest_bool  # 真机 compare.py 交叉核对，桩位（现 None）
    return prec


def _inject_defect(out, policy):
    """mock 注入缺陷：按 floor(numel*error_rate)+1 个坏点（非单点，避免大数组随机飘），让 validator 现 fail。"""
    n = int(out.size)
    if n == 0:
        return out
    err = float((policy or {}).get("error_rate", 0.0))
    k = min(n, int(math.floor(n * err)) + 1)
    flat = out.reshape(-1)
    for i in range(k):
        flat[i] = (~flat[i]) if out.dtype == bool else (flat[i] + 10)
    return out


def _mock_us(numel):
    """确定性 mock kernel 耗时（us）：与输出元素数成比例 + 常数启动。"""
    return round(numel / 2.0e5 + 1.5, 3)


def run_mock(caseset, work_dir, defect_cases=None):
    defect_cases = set(defect_cases or [])
    ev = []
    for c in caseset["cases"]:
        # 加载并校验所有 input（v0 mock 也核，防 caseset 契约漂移）。
        # T7/finding #7：x{j}.npy 存**物理**（bf16→uint16 位模式）。storage dtype **从逻辑 dtype 反推**
        # （_expected_storage 白名单），**不采**自声明 storage_dtype；自声明若与反推不符 → 直接拒（防伪造）。
        for inp in c["inputs"]:
            arr = np.load(_safe(work_dir, inp["path"]))
            expected_storage = _expected_storage(inp["dtype"])
            declared = inp.get("storage_dtype")
            if declared is not None and declared != expected_storage:
                raise ValueError(f"{c['id']} input {inp['name']}: 自声明 storage_dtype={declared!r} "
                                 f"≠ 据逻辑 dtype={inp['dtype']!r} 反推 {expected_storage!r}（拒伪造 storage）")
            if list(arr.shape) != list(inp["shape"]) or str(arr.dtype) != expected_storage:
                raise ValueError(f"{c['id']} input {inp['name']}: got {arr.dtype}{list(arr.shape)} "
                                 f"≠ 期望 storage {expected_storage}{inp['shape']}（逻辑 dtype={inp['dtype']}）")
        golden = np.load(_safe(work_dir, c["expected"]["golden_path"]))
        out = golden.copy()  # mock：完美 NPU = golden
        if c["id"] in defect_cases and out.size:  # 注入缺陷 → 让 validator 现 fail
            _inject_defect(out, c["expected"].get("policy"))
        out_path = f"{c['id']}/out.npy"
        np.save(_safe(work_dir, out_path), out)
        # §1.4 空 Tensor 功能用例（Layer A：expected.compare=na、无 policy）→ 无精度 metrics、status ok（validator→na）。
        if c["expected"].get("compare") == "na":
            ev.append({"case_id": c["id"], "status": "ok",
                       "precision": {"na": True, "note": "空Tensor numel=0，无精度 metrics（validator→na）"},
                       "perf": {"scope": "kernel_only", "us": _mock_us(int(golden.size))}})
            continue
        ev.append({
            "case_id": c["id"], "status": "ok",
            "precision": _precision_evidence(c, out, golden, out_path, work_dir),
            "perf": {"scope": "kernel_only", "us": _mock_us(int(golden.size))},  # 用输出 size（广播正确）
        })
    return {"op": caseset["op"], "repo_mode": "mock", "evidence": ev}


def user_root():
    """**用户工作目录**根。默认 = 进程 CWD；`OPRUNWAY_WORK_DIR` 可覆盖。

    工程约定「零持久化配置；所有产物落用户 CWD」——运行时产物（spec / runner / caseset / evidence / 报告）
    一律落这里，**绝不写插件安装目录**（真实 `/plugin install` 后插件在 `~/.claude/plugins/cache/…`，
    插件一升版就整目录换掉、用户产物被冲）。
    """
    return os.path.realpath(os.environ.get("OPRUNWAY_WORK_DIR") or os.getcwd())


def _plugin_root():
    """插件安装根 = `plugin/`（本文件在 `plugin/acc-common/` 下，故上溯一层）。
    用于「ops_root 不得落在插件目录内」的守卫——须覆盖**整个插件**（skills/ agents/ commands/ acc-common/ …），
    不能只挡 acc-common 子树，否则 OPRUNWAY_OPS_DIR 指向 plugin/skills/ 仍能绕过。"""
    return os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


def _contains(root, path):
    """path 是否在 root 之内（含 root 自身）。用 commonpath，避免 startswith 对 `/`、
    `/a` vs `/ab` 这类前缀歧义误判。两者均须已 realpath。"""
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:                                         # 跨盘 / 一相对一绝对 → 判不在内
        return False


def _reject_symlink_segments(root, path, label):
    """校验 `path` 相对 `root` 的**每一段**（含最后一段）都不是符号链接。

    ⚠ 为什么「只查最终组件」不够（本函数存在的理由）：`os.path.islink(<ops_root>/<op>/golden.py)` 只看
    **文件名那一层**；`<ops_root>/<op>` **目录本身**若是软链，open/import/scp 会静默跟随出去——
    ① `ops_root()` 的「不得落在插件安装目录内」守卫在 join `<op>` **之前**就做完了，目录段软链正好从它下面
    绕过去。故从 root 起逐段查。⚠ 本函数**只挡静态软链、不解 TOCTOU**（校完到真正使用之间仍有窗口）。

    `find_runner`（runner.cpp）与 `gen_cases.load_golden`（golden.py）都经 `op_dir()` 取目录 → 守卫只此一份。

    边界（说准、别让读者以为全防住）：`root` **自身不查**——env 覆盖时 `ops_root()` 已 realpath（软链已解掉）；
    默认 `<user_root>/.oprunway/ops` 时 `user_root` 已 realpath，但 `.oprunway` / `ops` 这两段**未逐段查**：
    它们不由 `<op>` 名决定，且插件树守卫是对 `realpath(root)` 做的、软链绕不过去；代价是用户把 `.oprunway`
    软链到别处时产物随之落到别处（用户自己的配置，不视作攻击面）。
    """
    rel = os.path.relpath(path, root)
    if os.path.isabs(rel) or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise ValueError(f"{label} 逃出 ops_root: {path!r}（root={root!r}）")
    cur = root
    for seg in rel.split(os.sep):
        if seg in ("", os.curdir):
            continue
        cur = os.path.join(cur, seg)
        if os.path.islink(cur):
            raise ValueError(f"{label} 的路径段是符号链接，拒绝（防路径逃逸/换靶）: {cur!r}")


def ops_root():
    """per-op **输入**产物根。默认 `<user_root>/.oprunway/ops`；`OPRUNWAY_OPS_DIR` 可覆盖（须绝对路径）。

    与 `reports/`（跑测**输出**、且在 .gitignore 里）分开：spec / runner / golden 是流水线的**输入**，
    性质不同、生命周期不同，不混在同一目录。

    ⚠ 无论默认还是 override，**ops_root 不得落在插件安装目录内**（否则「产物不写插件目录」的保证被绕过、
    且插件样例会被误标成 user 来源）。override 为空串按未设处理；相对路径拒绝（防 CWD 漂移）。
    """
    env = os.environ.get("OPRUNWAY_OPS_DIR")
    if env:
        if not os.path.isabs(env):
            raise ValueError(f"OPRUNWAY_OPS_DIR 须为绝对路径: {env!r}")
        root = os.path.realpath(env)
    else:
        root = os.path.join(user_root(), ".oprunway", "ops")
    if _contains(_plugin_root(), os.path.realpath(root)):
        raise ValueError(f"ops_root 不得落在插件安装目录内: {root!r}（产物须落用户工作目录）")
    return root


def op_dir(op_name):
    """单个算子的输入目录：`<ops_root>/<op>/`（spec.json · runner.cpp · golden.py）。

    ⚠ 返回前 **从 ops_root 起逐段拒软链**（`_reject_symlink_segments`）：下游对 runner.cpp / golden.py 的
    `os.path.islink` 只挡最终**文件名**那一层，挡不住 `<ops_root>/<op>` **目录段**本身是软链的情形
    （详见 `_reject_symlink_segments` 的理由段）。两个消费方（`find_runner` / `gen_cases.load_golden`）
    都经本函数取目录，故守卫写在这里、只一份。
    """
    _check_id("op_name", op_name)
    root = ops_root()
    d = os.path.join(root, op_name)
    _reject_symlink_segments(root, d, f"算子目录({op_name})")
    return d


def find_runner(op_name):
    """按算子名找 runner.cpp，返回 `(path, "user", remote_name)`。

    **只查用户目录** `<ops_root>/<op>/oprunway_<op>_runner.cpp`——acc-runner 为本次任务生成的、或用户自带的。
    **引擎不含任何算子 runner、绝不回退到插件自带样例**（fallback 已退役 2026-07-20，撤销 a7c8417 的「可以带
    样例」兜底）：缺 runner 直接 **fail-closed** 报错，要求先经 acc-runner 生成或用户放置。样例 runner 现只在顶层
    `samples/runners/` 作**只读参考 / 生成器骨架种子**，**不是**引擎运行时的回退靶（runner 是引擎的**输出**、非组件）。

    安全（runner 会被 scp 到远端，是真实注入面）：
    - `op_name` 经 `_check_id` 校验；`remote_name` **由已校验的 op_name 定死**（`oprunway_<lower>_runner.cpp`），
      **不从解析后的本地路径取 basename**——否则符号链接可把远端文件名变成 `bad;rm...` 注入远端命令。
    - **软链分两层挡，缺一不可**：最终文件 `oprunway_<op>_runner.cpp` 那一层由下文 `os.path.islink` 挡；
      其上的 `<ops_root>/<op>` **目录段**由 `op_dir()` 的 `_reject_symlink_segments` 逐段挡。
      ⚠ 本行旧注释只写「拒符号链接（os.path.islink）」，读起来像已全防住——实则 `islink` 只看最终组件，
      目录段软链会被静默跟随（旧洞，已由 `op_dir()` 补上）。两层合起来挡住**静态**软链（最终文件 + 目录段）。⚠ **不封 TOCTOU**：校完到真正 open/import/scp
      之间的窗口仍在，攻击者可在此期间 rename 换靶；真封堵要 O_NOFOLLOW/openat 逐级打开（本仓
      perf_sim_plot._safe_open_write 是那个路子，此处未跟进）。另 root 自身与 `.oprunway`/`ops`
      两段未逐段查（realpath 会抹掉「root 本身是软链」），如实记账、别当已全防住。
    - 权限错误/异常文件类型一律抛错（fail-closed，不静默兜底）。
    """
    _check_id("op_name", op_name)
    name = f"oprunway_{op_name.lower()}_runner.cpp"          # 远端文件名的唯一真相源（已校验，无注入）

    # 不 realpath，先按声明路径查：目录段软链已在 op_dir() 里逐段拒，最终文件那一层的 islink 见下。
    upath = os.path.join(op_dir(op_name), name)
    try:
        st = os.lstat(upath)                                 # lstat：不跟随软链
    except FileNotFoundError:
        st = None                                            # 真不存在 → fail-closed 报错（不回退样例）
    except OSError as ex:
        raise ValueError(f"用户 runner 不可访问: {upath!r}: {ex}")
    if st is not None:
        if os.path.islink(upath):                            # 仅最终组件；目录段由 op_dir() 逐段拒
            raise ValueError(f"用户 runner 是符号链接，拒绝（防路径逃逸/远端注入）: {upath!r}")
        if not os.path.isfile(upath):
            raise ValueError(f"用户 runner 路径存在但不是普通文件: {upath!r}")
        return upath, "user", name

    raise ValueError(
        f"缺 runner: {name}（引擎不回退插件样例，fail-closed）\n"
        f"  用户目录（应放这里）: {upath}\n"
        f"  → 新算子需先由 acc-runner 生成 runner.cpp 落到用户目录（可照 samples/runners/ 的只读样例）；"
        f"或设 OPRUNWAY_OPS_DIR / OPRUNWAY_WORK_DIR 指向正确的工作目录。")


def _ne_cfg():
    """真机配置——**零硬编码、无私有默认值**。

    ⚠ 机器名 / 远端路径 / 被测仓路径**必须由调用方（编排层经 `OPRUNWAY_*` 环境变量）显式提供**，
    缺失即报错——绝不用某台私有机器的名字/路径兜底（否则别人拿到插件默认连一台不存在的机器、
    找一个不存在的路径，直接失败）。由 orchestrator 在 CP-D 前 `AskUserQuestion` 问清后灌进 env。

    **传输模式** `OPRUNWAY_TARGET`：
      - `local`  —— 目标机就是本机，直接跑（无 ssh/scp）；此时 `OPRUNWAY_SSH_HOST` 不需要。
      - `remote`（默认）—— ssh 到 `OPRUNWAY_SSH_HOST`；host 必填。

    仅 `soc` / `vendor` / `setenv` 保留"常见值"默认（它们是昇腾工具链的通用约定，非某台机私有）。
    """
    g = os.environ.get
    target = (g("OPRUNWAY_TARGET") or "remote").strip().lower()
    if target not in ("local", "remote"):
        raise ValueError(f"OPRUNWAY_TARGET 须为 'local' 或 'remote'，得到 {target!r}")

    def _req(key, why):
        v = (g(key) or "").strip()
        if not v:
            raise ValueError(
                f"缺 {key}（{why}）——本函数**不提供私有默认值**。\n"
                f"  请由编排层在 CP-D 前询问用户（本机直连 / ssh 远端 + 路径）后经 {key} 传入。")
        return v

    cfg = {"target": target,
           # 被测仓 / 远端工作根 / 用户态 opp 都无私有默认，必须显式提供
           "rroot": _req("OPRUNWAY_REMOTE_DIR", "远端（或本机）工作根目录"),
           "ops":   _req("OPRUNWAY_OPS_REPO", "被测算子仓路径；不存在时须先 clone（Track: 按需 clone）"),
           "opp":   _req("OPRUNWAY_OPP", "用户态 custom opp 目录（避免写共享 opp/vendors）"),
           # 被测 op 源码子路径（相对 OPS 仓，如 experimental/math/is_close）——run_on_npu.sh 据此算 OPHASH 绑源、
           # 落 opp provenance；**必填**（旧启发 experimental/math/$OP 对多数 op 路径不存在→恒定空 hash→未绑源→
           # stale opp 假通过，codex 门坐实）。由编排层 CP-D 前探测/问用户（哪份源是被测 PR 的 op、在仓内哪个子路径）。
           "op_src": _req("OPRUNWAY_OP_SRC",
                          "被测 op 源码子路径（相对 OPS 仓，如 experimental/math/is_close）——绑 opp provenance 用"),
           "opp_rebuild": (g("OPRUNWAY_OPP_REBUILD") or "0").strip(),  # =1 授权从当前源重建 opp（含 rm -rf $V）
           "soc":   g("OPRUNWAY_SOC", "ascend910_93"),       # 昇腾通用约定，非私有机名
           "vendor": g("OPRUNWAY_VENDOR", "oprunway"),
           "setenv": g("OPRUNWAY_SETENV", "/usr/local/Ascend/ascend-toolkit/set_env.sh")}
    # op_src 安全校验：须为安全的**嵌套**相对路径。除路径逃逸/注入外，还须堵 `.` / `./` / 裸子树根（如 `experimental`）
    #   /`.` 段/尾斜杠——否则 run_on_npu.sh 里 SRC=$OPS/. 会把 OPHASH 绑到**整仓**、`case $OP_SRC in experimental/*`
    #   不匹配 → 跳 `--experimental`、且 provenance stamp **非算子专属**（同仓不同算子得同 WANT_PROV）→ 算子 B 复用
    #   算子 A 的 opp 假通过：与 line-16 `$OP_SRC` 修的**同类洞、走另一扇门**。故用 normpath 归一后强制 canonical + ≥2 段。
    _osrc = cfg["op_src"]
    _seg = _osrc.split("/")
    if (_osrc.startswith("/") or ".." in _seg or "." in _seg              # 无前导 /、无 ..、无 . 段
            or _osrc != posixpath.normpath(_osrc)                          # 须 canonical（拒 ./、尾斜杠、// 等归一差异）
            or "/" not in _osrc                                            # 须嵌套 ≥2 段（拒仓根 . 与裸子树根 experimental/math）
            or not _PATH_RE.match(_osrc)):                                 # 仅安全字符（防 scp/ssh 拼接注入）
        raise ValueError(f"OPRUNWAY_OP_SRC={cfg['op_src']!r} 须为安全的嵌套相对路径"
                         f"（相对 OPS 仓、无前导 /、无 ./.. 段、须 ≥2 段指向具体算子源目录如 experimental/math/is_close、仅 [A-Za-z0-9_./-]）")
    # host 仅 remote 模式必填；local 模式忽略
    cfg["host"] = _req("OPRUNWAY_SSH_HOST",
                       "远端机器名（ssh）；若本机即目标机，设 OPRUNWAY_TARGET=local 即可免此项"
                       ) if target == "remote" else None
    return cfg


# ── 传输层：local / remote 各一实现 ─────────────────────────────────────────
# run_new_example 里所有跨机操作只经这三个原语；local 模式直接在本机跑、不碰 ssh/scp。
# 安全沿用既有校验：host 过 _check_id、远端路径过 _check_remote_path，故拼进命令无注入面。

def _shell(host, script, *, timeout, check, capture=False):
    """执行一段 shell：remote 走 `ssh host bash -l -s`，local 走本机 `bash -l -s`。
    脚本经 stdin 喂给 `bash -l -s`（唯一脚本入参 = script）。"""
    argv = (["ssh", host, "bash", "-l", "-s"] if host else ["bash", "-l", "-s"])
    return subprocess.run(argv, input=script,
                          capture_output=capture, text=True, timeout=timeout, check=check)


def _copy_to(host, local_path, remote_path, *, timeout, check=True):
    """本地文件 → 目标机。remote 用 scp；local 用 cp（同机拷贝，目标即真实路径）。"""
    if host:
        subprocess.run(["scp", "-q", local_path, f"{host}:{remote_path}"],
                       check=check, timeout=timeout)
    else:
        os.makedirs(os.path.dirname(remote_path) or ".", exist_ok=True)
        shutil.copy2(local_path, remote_path)


def _copy_from(host, remote_path, local_path, *, timeout, check=True, quiet_stderr=False):
    """目标机文件 → 本地。remote 用 scp；local 用 cp。"""
    if host:
        subprocess.run(["scp", "-q", f"{host}:{remote_path}", local_path],
                       check=check, timeout=timeout,
                       stderr=(subprocess.DEVNULL if quiet_stderr else None))
    else:
        if not os.path.exists(remote_path):
            if check:
                raise FileNotFoundError(remote_path)
            return
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        shutil.copy2(remote_path, local_path)


def run_new_example(caseset, work_dir, defect_cases=None):
    """真机跑测：部署用例 → a3 建双 exe(custom + 内置 TBE) → 正确性 + msprof 双测 → 拉回 → 真 evidence + 真基线。

    ⚠ 共享机：只写用户目录；op 走用户态 ASCEND_CUSTOM_OPP_PATH，不碰共享 opp/vendors。
    精度 = 真 NPU out vs 本机 golden；性能 = msprof kernel-only Task Duration(us)；基线 = 同法测的内置 TBE。
    远端编排在 new_example/run_on_npu.sh。返回 evidence；真基线写 work_dir/_real_baseline.json（run_workflow 优先用）。
    """
    cfg = _ne_cfg()
    host, rroot, ops, opp = cfg["host"], cfg["rroot"], cfg["ops"], cfg["opp"]
    soc, vendor = cfg["soc"], cfg["vendor"]
    _check_id("op_name", caseset["op"])          # 原始算子名（驱动 runner 文件名/OPRUNWAY_OPNAME）
    if host is not None:         # remote 才有 host；local 模式 host=None、不 ssh
        _check_id("host", host)
    _check_id("vendor", vendor)
    if not _SOC_RE.match(soc): raise ValueError(f"非法 soc: {soc!r}")
    for k, p in (("remote_dir", rroot), ("ops_repo", ops), ("opp", opp), ("setenv", cfg["setenv"])):
        _check_remote_path(k, p)
    # local 模式下 rroot/ops/opp 是**本机真实目录**，且 §部署 会对 rroot/cases 执行 `rm -rf`。
    # 必须与 work_dir 双向不相交，否则用户把 rroot 指到含产物的目录 → 静默删。remote 模式 rroot 在远端、天然不相交。
    if host is None:
        wd = os.path.realpath(work_dir)
        for k, p in (("remote_dir", rroot), ("ops_repo", ops), ("opp", opp)):
            rp = os.path.realpath(p)
            if _contains(wd, rp) or _contains(rp, wd):
                raise ValueError(
                    f"local 模式下 {k}={p!r} 与 work_dir={work_dir!r} 相交——"
                    f"§部署会对其执行 rm -rf，拒绝以防误删。请指向独立的专用 scratch 目录。")
    here = os.path.dirname(os.path.abspath(__file__))
    # runner：**只查用户目录**（引擎不回退插件样例，fallback 已退役 2026-07-20）；runner_source 恒 "user"，
    # 缺 runner 则 find_runner 直接 fail-closed 报错。runner_name 由 find_runner 从**已校验的 op_name** 定死
    # （不取 basename），远端 scp 文件名无注入面。
    runner, runner_source, runner_name = find_runner(caseset["op"])
    npu_sh = os.path.join(here, "new_example", "run_on_npu.sh")   # 通用编排脚本（非 per-op），留在插件内
    n = len(caseset["cases"])
    perf_ids = [c["id"] for c in caseset["cases"] if "性能" in c.get("dims", [])]

    # 1) 校验输入 + npy→bin（广播 materialize）+ manifest（op 无关：输入按序 x{j}.bin、attr 按 attr_order）
    attr_order = caseset.get("attr_order", [])
    manifest = []
    for c in caseset["cases"]:
        cid = c["id"]
        _check_id("case_id", cid)
        dtn = c["inputs"][0]["dtype"]
        if dtn not in _NP:  # T7：int16/int32/bfloat16 的 runner.cpp 分支属 **Track C**（挂真机+pr_facts）
            raise ValueError(f"{cid}: runner v1 仅支持 {sorted(_NP)}；dtype {dtn!r} 属 Track C——"
                             f"runner.cpp 新 dtype 分支须从算子 example/op_def 抠+真机验证，见 doc/oprunway-todo.md gap")
        if any(inp["dtype"] != dtn for inp in c["inputs"]):  # runner v1 全输入同 dtype，拒静默强转
            raise ValueError(f"{cid}: 多输入 dtype 不一致 {[i['dtype'] for i in c['inputs']]}"
                             f"（runner v1 要求同 dtype；混合 dtype 需 per-input manifest）")
        arrs = []
        for inp in c["inputs"]:
            arr = np.load(_safe(work_dir, inp["path"]))
            # finding #6：storage dtype **从逻辑 dtype 反推**（白名单），不采自声明 storage_dtype（可伪造）；
            # 自声明若与反推不符 → 直接拒（旧洞：伪造 storage_dtype=uint16 + 后续值 cast 污染真机 x{j}.bin）。
            expected_storage = _expected_storage(inp["dtype"])
            declared = inp.get("storage_dtype")
            if declared is not None and declared != expected_storage:
                raise ValueError(f"{cid} {inp['name']}: 自声明 storage_dtype={declared!r} ≠ 据逻辑 "
                                 f"dtype={inp['dtype']!r} 反推 {expected_storage!r}（拒伪造 storage）")
            if list(arr.shape) != list(inp["shape"]) or str(arr.dtype) != expected_storage:
                raise ValueError(f"{cid} {inp['name']}: npy {arr.dtype}{list(arr.shape)} "
                                 f"≠ 期望 storage {expected_storage}{inp['shape']}（逻辑 {inp['dtype']}）")
            arrs.append(arr)
        out_shape = np.broadcast_shapes(*[a.shape for a in arrs])
        # §1.4 空 Tensor 功能用例（compare=na）：runner 已处理 numel=0（空入空出），放行部署；非 na 的 numel=0=异常。
        if int(np.prod(out_shape)) == 0 and c["expected"].get("compare") != "na":
            raise ValueError(f"{cid}: 非 na 的 numel=0（异常；空 Tensor 功能用例应标 expected.compare=na）")
        exp_dt = np.bool_ if c["expected"].get("verify_mode") == "exact" else _NP[dtn]
        golden = np.load(_safe(work_dir, c["expected"]["golden_path"]))
        if golden.shape != out_shape or golden.dtype != exp_dt:
            raise ValueError(f"{cid}: golden {golden.dtype}{golden.shape} ≠ 期望 "
                             f"{np.dtype(exp_dt).name}{tuple(out_shape)}")
        for j, arr in enumerate(arrs):
            if arr.shape != out_shape:
                arr = np.broadcast_to(arr, out_shape).copy()   # 广播为独立缓冲（不与 npy 共 buffer）
            # `x{j}.npy` gen_cases 已存**物理 storage** 字节（bf16→uint16 位模式、native→逻辑；上文 L452 已校
            # dtype==expected_storage）→ **直接落 .bin**。旧代码再过 materialize_input(期望逻辑 fp32→encode)对 bf16
            # 是二次 encode（uint16 当逻辑喂→raise）；native 时逻辑==物理才未暴露。bf16 放开后此路必经，故改直写。
            np.ascontiguousarray(arr).tofile(_safe(work_dir, f"{cid}/x{j + 1}.bin"))
        at = c["attrs"]
        attr_vals = ["1" if at.get(a) is True else ("0" if at.get(a) is False else str(at.get(a)))
                     for a in attr_order]
        dims = list(out_shape)
        manifest.append(" ".join([cid, dtn] + attr_vals + [str(len(dims))] + [str(d) for d in dims]))
    with open(os.path.join(work_dir, "manifest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(manifest) + "\n")
    with open(os.path.join(work_dir, "perfcases_list.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(perf_ids) + ("\n" if perf_ids else ""))

    # 2) 部署（tar 送 bin + manifest + perfcases_list，排除 npy/out.bin）+ runner cpp + 编排脚本。
    #    两模式同构：先把 tar 送到目标机 /tmp，再解到 rroot/cases（local 时"送"= 本地 cp、"目标机"= 本机）。
    q = shlex.quote
    # 每次运行唯一 token：本地暂存 tgz 与远端 /tmp tgz 都带它，避免并发/多用户共享机上两个验收互相覆盖：
    # 本地——两次跑共享 work_dir 父目录时若定名 `_deploy.tgz` 会互相覆盖（且失败不清理会留垃圾）；
    # 远端——A 上传、B 覆盖、A 解出 B 的用例 → "测到的不是本次输入"。local 模式同样中招，故都用唯一路径。
    token = uuid.uuid4().hex[:16]
    # tgz 写到 work_dir **外面**（父目录）：否则「边打包 work_dir、边把 tgz 写进 work_dir」会让
    # GNU tar（Linux/server）报 "file changed as we read it" → exit 1（BSD tar/Mac 宽容、GNU tar 严）。
    tar = os.path.join(os.path.dirname(os.path.abspath(work_dir.rstrip("/"))), f"_deploy_{token}.tgz")
    try:
        subprocess.run(["tar", "czf", tar, "-C", work_dir, "--exclude=*.npy", "--exclude=out.bin",
                        "."], check=True, timeout=300)
        tmp_tgz = f"/tmp/oprunway_deploy_{token}.tgz"
        _copy_to(host, tar, tmp_tgz, timeout=300)
        qcases = q(rroot + "/cases")
        # 远端命令对支持者加 `--` 终止选项解析（finding #17，纵深防御；配合上文路径/ID 校验）。
        _shell(host,
               f"rm -rf -- {qcases} && mkdir -p -- {qcases} && "
               f"tar xzf {q(tmp_tgz)} -C {qcases} && rm -f -- {q(tmp_tgz)} && "
               f"cp -- {qcases}/perfcases_list.txt {q(rroot + '/perfcases_list.txt')}\n",
               timeout=300, check=True)
        _copy_to(host, runner, f"{rroot}/{runner_name}", timeout=120)
        _copy_to(host, npu_sh, f"{rroot}/run_on_npu.sh", timeout=120)
    finally:
        if os.path.exists(tar):
            os.remove(tar)   # 无论成败都清理本地暂存 tgz（失败不清理会在共享父目录留垃圾）

    # 3) 远程编排（建双 exe + 正确性 + msprof 双测）；靠双哨兵 + returncode 判成败
    script = (f"source {q(cfg['setenv'])} 2>/dev/null || true\n"
              f"export OPRUNWAY_OPS_REPO={q(ops)} OPRUNWAY_OPP={q(opp)} OPRUNWAY_RUN_DIR={q(rroot)}\n"
              f"export OPRUNWAY_SOC={soc} OPRUNWAY_VENDOR={vendor} "
              f"OPRUNWAY_SETENV={q(cfg['setenv'])}\n"
              f"export OPRUNWAY_RUNNER={q(runner_name)} OPRUNWAY_OPNAME={q(caseset['op'])} "
              f"OPRUNWAY_OP_SRC={q(cfg['op_src'])} OPRUNWAY_OPP_REBUILD={q(cfg['opp_rebuild'])}\n"
              f"bash {q(rroot + '/run_on_npu.sh')}\n")
    r = _shell(host, script, timeout=2400, check=False, capture=True)
    blob = (r.stdout or "") + (r.stderr or "")
    done = f"OPRUNWAY_DONE total={n} ok={n} fail=0"
    label = "本机" if host is None else "远程"
    if r.returncode != 0 or done not in blob or "OPRUNWAY_NPU_DONE" not in blob:
        raise RuntimeError(f"[new_example] {label}跑测失败 rc={r.returncode}:\n{blob[-2000:]}")

    # 4) 拉回 out.bin + perf_result.txt（local 时 = 本机 cp）
    for c in caseset["cases"]:
        # na（空 Tensor 功能用例）：步骤 5 对 na 直接 skip（不读 out.bin）→ na 用 check=False，**解耦对 runner
        # 是否落空 out.bin 的依赖**（runner numel==0 未落文件也不硬崩）；非 na 仍 check=True（真失败照崩、不掩盖）。
        is_na = c["expected"].get("compare") == "na"
        _copy_from(host, f"{rroot}/cases/{c['id']}/out.bin",
                   _safe(work_dir, f"{c['id']}/out.bin"), timeout=120, check=not is_na, quiet_stderr=is_na)
    prp = os.path.join(work_dir, "perf_result.txt")
    if os.path.exists(prp):
        os.remove(prp)   # 删本地旧文件，防拷贝失败时解析 stale
    _copy_from(host, f"{rroot}/perf_result.txt", prp, timeout=120, check=False, quiet_stderr=True)

    # 解析 perf_result（每行 "case_id custom_us tbe_us"；NA=未测到）→ perf_us / 真基线 base_us
    perf_us, base_us = {}, {}
    pr = os.path.join(work_dir, "perf_result.txt")
    if os.path.exists(pr):
        with open(pr, encoding="utf-8") as pf:
            for line in pf:
                parts = line.split()
                if len(parts) != 3:
                    continue
                cid, cus, tus = parts
                for d, v in ((perf_us, cus), (base_us, tus)):
                    try:
                        fv = float(v)
                        if math.isfinite(fv) and fv > 0:  # 有限正数（拒 NaN/inf/≤0）
                            d[cid] = round(fv, 3)
                    except ValueError:
                        pass

    # 5) 采集 evidence（真 NPU out vs 本机 golden；perf = msprof kernel-only）
    ev = []
    for c in caseset["cases"]:
        cid = c["id"]
        dtn = c["inputs"][0]["dtype"]         # per-case 逻辑 dtype（修：本段旧误用 manifest 循环残留 dtn；多 dtype 会错）
        # §1.4 空 Tensor 功能用例（compare=na）：runner 空入空出、无精度可判 → na 证据（validator→na）。
        if c["expected"].get("compare") == "na":
            ev.append({"case_id": cid, "status": "skipped_empty",
                       "precision": {"na": True,
                                     "note": "空Tensor numel=0，真机空入空出、无精度 metrics（validator→na）"}})
            continue
        golden = np.load(_safe(work_dir, c["expected"]["golden_path"]))
        obin = _safe(work_dir, f"{cid}/out.bin")
        if golden.dtype == np.bool_:          # exact/bool：out.bin 是 uint8 0/1
            raw = np.fromfile(obin, dtype=np.uint8)
            if raw.size != golden.size:
                raise RuntimeError(f"{cid}: out.bin {raw.size}B ≠ 期望 {golden.size}（形状/传输异常）")
            if raw.size and not np.isin(raw, (0, 1)).all():
                raise RuntimeError(f"{cid}: out.bin 含非 0/1 值，非法 bool 输出")
            out = raw.reshape(golden.shape).astype(bool) if golden.size else raw.astype(bool)
        else:                                 # numerical：out.bin 是 **storage** dtype（bf16→uint16、native→逻辑）
            # storage-aware 读回：bf16 的 out.bin 是 uint16 位模式 → readback_output 解码回 fp32-on-grid；
            # native(fp32/fp16) storage==logical，readback_output 断言 dtype 相符（不做值 cast）。
            storage = np.dtype(_expected_storage(dtn))
            raw = np.fromfile(obin, dtype=storage)
            if raw.size != golden.size:
                raise RuntimeError(f"{cid}: out.bin {raw.size} elem ≠ 期望 {golden.size}（形状/传输异常）")
            dec = readback_output(raw, {"dtype": dtn})
            out = dec.reshape(golden.shape) if golden.size else dec
        # A 方案：把 readback 逻辑数组另落 out.npy（供门 gate_task2 以 np.load 统一重算；out.bin 原始 dump 保留
        # 作原始产物）。provenance 的 out_sha256 绑定 out.npy（门重算所依的那份字节）。
        out_npy_rel = f"{cid}/out.npy"
        np.save(_safe(work_dir, out_npy_rel), out)
        prec = _precision_evidence(c, out, golden, out_npy_rel, work_dir, ascendoptest_bool=None)
        prec["ascendoptest_bool"] = None   # 待 NPU：真机接 compare.py bool 作交叉核对（现桩位）
        prec.setdefault("ascendoptest_bool_note", "待 NPU：真机接 AscendOpTest compare.py bool 交叉核对")
        ev.append({"case_id": cid, "status": "ok",
                   "precision": prec,
                   "perf": {"scope": "kernel_only", "us": perf_us.get(cid),
                            "note": "msprof op Task Duration(us) 中位（真 kernel-only）"}})

    # 真基线：同法 msprof 测得的内置 TBE → 写 _real_baseline.json（run_workflow 优先用）
    real_base = {"source": "tbe", "scope": "kernel_only",
                 "per_case": [{"case_id": cid, "us": us, "env": "builtin-TBE msprof"}
                              for cid, us in base_us.items()]}
    with open(os.path.join(work_dir, "_real_baseline.json"), "w", encoding="utf-8") as f:
        json.dump(real_base, f, ensure_ascii=False, indent=2)
    # runner_source 进 evidence（provenance）：fallback 已退役后恒 "user"（引擎不回退插件样例）；
    # 门据此 fail-closed——非 user 一律 BLOCKED（防伪造 evidence 冒充「验收了用户自己的算子」）。
    return {"op": caseset["op"], "repo_mode": "new_example",
            "runner_source": runner_source, "runner_path": runner, "evidence": ev}


MODES = {"mock": run_mock, "new_example": run_new_example}

# --- P3 · catlass adapter（generated_harness）注册：实现在自有模块 catlass_adapter.py，此处仅加法接入 ---
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from catlass_adapter import CATLASS_MODES  # noqa: E402  catlass/catlass_mock
    MODES.update(CATLASS_MODES)
except Exception:  # 缺 catlass_adapter/其依赖时不影响既有 mock/new_example
    pass


def main(argv):
    caseset_path, work_dir, out_path = argv[0], argv[1], argv[2]
    mode = argv[3] if len(argv) > 3 else "mock"
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}, supported={list(MODES)}")
    defect = argv[4].split(",") if len(argv) > 4 and argv[4] else None
    with open(caseset_path, encoding="utf-8") as cf:
        caseset = json.load(cf)
    evidence = MODES[mode](caseset, work_dir, defect_cases=defect)
    with open(out_path, "w", encoding="utf-8") as of:
        json.dump(evidence, of, ensure_ascii=False, indent=2)
    print(f"[repo_adapter/{mode}] {len(evidence['evidence'])} evidence -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
