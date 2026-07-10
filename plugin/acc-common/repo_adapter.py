"""Task 2 · repo_adapter — caseset.json -> evidence.json（纯采集、不判定）。

统一接口，按仓/模式换实现：
- mock          : 本地干跑，kernel 输出 = golden（可注入 defect），perf = 确定性 mock。无需 NPU。
- new_example   : 真机 build/run PR 自带工程（留桩，需 NPU + VPN，之后填）。
证据只记「测到什么」（metric value / us / 路径），pass/fail 交给 validator（ADR 0007）。
"""
import hashlib, json, math, os, posixpath, re, shlex, subprocess, sys
import numpy as np
import precision_policy
import gen_cases  # T7：复用 bf16 位级 codec（_f32_to_bf16_uint16/_bf16_uint16_to_f32）+ 原生 dtype 表

_NP = {"float32": np.float32, "float16": np.float16}  # **runner-supported**（真机 new_example 用）；int/bf16 属 Track C
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


def _snake(camel):  # IsClose → is_close, Sign → sign（build.sh --ops + experimental/math/<snake>）
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).lower()


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
            "oracle_source": "cpu_ref",          # numpy 融合 golden = host CPU 参考
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
        ev.append({
            "case_id": c["id"], "status": "ok",
            "precision": _precision_evidence(c, out, golden, out_path, work_dir),
            "perf": {"scope": "kernel_only", "us": _mock_us(int(golden.size))},  # 用输出 size（广播正确）
        })
    return {"op": caseset["op"], "repo_mode": "mock", "evidence": ev}


def _ne_cfg():
    """真机配置——零硬编码：全部可用环境变量覆盖，默认给 a3 常见值。"""
    g = os.environ.get
    return {"host": g("OPRUNWAY_SSH_HOST", "ascend-a3"),
            "rroot": g("OPRUNWAY_REMOTE_DIR", "/home/lys/oprunway_run"),
            "ops": g("OPRUNWAY_OPS_REPO", "/home/lys/ops-math"),
            "opp": g("OPRUNWAY_OPP", "/home/lys/oprunway_opp"),
            "soc": g("OPRUNWAY_SOC", "ascend910_93"),
            "op": g("OPRUNWAY_OP", "is_close"),
            "vendor": g("OPRUNWAY_VENDOR", "oprunway"),
            "setenv": g("OPRUNWAY_SETENV", "/usr/local/Ascend/ascend-toolkit/set_env.sh")}


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
    op = _snake(caseset["op"])   # 算子 snake 名驱动 build/目录（IsClose→is_close, Sign→sign）
    _check_id("host", host)
    _check_id("op", op)
    _check_id("vendor", vendor)
    if not _SOC_RE.match(soc): raise ValueError(f"非法 soc: {soc!r}")
    for k, p in (("remote_dir", rroot), ("ops_repo", ops), ("opp", opp), ("setenv", cfg["setenv"])):
        _check_remote_path(k, p)
    here = os.path.dirname(os.path.abspath(__file__))
    runner_name = f"oprunway_{caseset['op'].lower()}_runner.cpp"   # 按算子选 runner
    runner = os.path.join(here, "new_example", runner_name)
    if not os.path.exists(runner):
        raise ValueError(f"缺 runner: {runner_name}（新算子需先写 new_example/{runner_name}）")
    npu_sh = os.path.join(here, "new_example", "run_on_npu.sh")
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
        if int(np.prod(out_shape)) == 0:
            raise ValueError(f"{cid}: new_example v1 不支持空 Tensor（numel=0，runner 会绕过 NPU）")
        exp_dt = np.bool_ if c["expected"].get("verify_mode") == "exact" else _NP[dtn]
        golden = np.load(_safe(work_dir, c["expected"]["golden_path"]))
        if golden.shape != out_shape or golden.dtype != exp_dt:
            raise ValueError(f"{cid}: golden {golden.dtype}{golden.shape} ≠ 期望 "
                             f"{np.dtype(exp_dt).name}{tuple(out_shape)}")
        for j, arr in enumerate(arrs):
            if arr.shape != out_shape:
                arr = np.broadcast_to(arr, out_shape).copy()   # 广播 materialize 为独立 X_bin（不与 npy 共 buffer）
            # T7：经 materialize_input 落物理字节（当前 dtn∈fp32/fp16，storage=逻辑；bf16/int 已在上文 Track C 拦截）
            materialize_input(arr, {"dtype": dtn}).tofile(_safe(work_dir, f"{cid}/x{j + 1}.bin"))
        at = c["attrs"]
        attr_vals = ["1" if at.get(a) is True else ("0" if at.get(a) is False else str(at.get(a)))
                     for a in attr_order]
        dims = list(out_shape)
        manifest.append(" ".join([cid, dtn] + attr_vals + [str(len(dims))] + [str(d) for d in dims]))
    with open(os.path.join(work_dir, "manifest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(manifest) + "\n")
    with open(os.path.join(work_dir, "perfcases_list.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(perf_ids) + ("\n" if perf_ids else ""))

    # 2) 部署（tar 送 bin + manifest + perfcases_list，排除 npy/out.bin）+ runner cpp + 编排脚本
    q = shlex.quote
    tar = os.path.join(work_dir, "_deploy.tgz")
    subprocess.run(["tar", "czf", tar, "-C", work_dir, "--exclude=*.npy", "--exclude=out.bin",
                    "--exclude=_deploy.tgz", "."], check=True, timeout=300)
    subprocess.run(["scp", "-q", tar, f"{host}:/tmp/oprunway_deploy.tgz"], check=True, timeout=300)
    qcases = q(rroot + "/cases")
    # 远端命令对支持者加 `--` 终止选项解析（finding #17，纵深防御；配合上文路径/ID 校验）。
    subprocess.run(["ssh", host,
                    f"rm -rf -- {qcases} && mkdir -p -- {qcases} && "
                    f"tar xzf /tmp/oprunway_deploy.tgz -C {qcases} && rm -f -- /tmp/oprunway_deploy.tgz && "
                    f"cp -- {qcases}/perfcases_list.txt {q(rroot + '/perfcases_list.txt')}"],
                   check=True, timeout=300)
    subprocess.run(["scp", "-q", runner, f"{host}:{rroot}/{runner_name}"], check=True, timeout=120)
    subprocess.run(["scp", "-q", npu_sh, f"{host}:{rroot}/run_on_npu.sh"], check=True, timeout=120)
    os.remove(tar)

    # 3) 远程编排（建双 exe + 正确性 + msprof 双测）；靠双哨兵 + returncode 判成败
    script = (f"source {q(cfg['setenv'])} 2>/dev/null || true\n"
              f"export OPRUNWAY_OPS_REPO={q(ops)} OPRUNWAY_OPP={q(opp)} OPRUNWAY_RUN_DIR={q(rroot)}\n"
              f"export OPRUNWAY_SOC={soc} OPRUNWAY_OP={op} OPRUNWAY_VENDOR={vendor} "
              f"OPRUNWAY_SETENV={q(cfg['setenv'])}\n"
              f"export OPRUNWAY_RUNNER={q(runner_name)} OPRUNWAY_OPNAME={q(caseset['op'])}\n"
              f"bash {q(rroot + '/run_on_npu.sh')}\n")
    r = subprocess.run(["ssh", host, "bash -l -s"], input=script,
                       capture_output=True, text=True, timeout=2400)
    blob = r.stdout + r.stderr
    done = f"OPRUNWAY_DONE total={n} ok={n} fail=0"
    if r.returncode != 0 or done not in blob or "OPRUNWAY_NPU_DONE" not in blob:
        raise RuntimeError(f"[new_example] 远程跑测失败 rc={r.returncode}:\n{blob[-2000:]}")

    # 4) 拉回 out.bin + perf_result.txt
    for c in caseset["cases"]:
        subprocess.run(["scp", "-q", f"{host}:{rroot}/cases/{c['id']}/out.bin",
                        _safe(work_dir, f"{c['id']}/out.bin")], check=True, timeout=120)
    prp = os.path.join(work_dir, "perf_result.txt")
    if os.path.exists(prp):
        os.remove(prp)   # 删本地旧文件，防 scp 失败时解析 stale
    subprocess.run(["scp", "-q", f"{host}:{rroot}/perf_result.txt", prp],
                   check=False, timeout=120, stderr=subprocess.DEVNULL)

    # 解析 perf_result（每行 "case_id custom_us tbe_us"；NA=未测到）→ perf_us / 真基线 base_us
    perf_us, base_us = {}, {}
    pr = os.path.join(work_dir, "perf_result.txt")
    if os.path.exists(pr):
        for line in open(pr, encoding="utf-8"):
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
        golden = np.load(_safe(work_dir, c["expected"]["golden_path"]))
        obin = _safe(work_dir, f"{cid}/out.bin")
        if golden.dtype == np.bool_:          # exact/bool：out.bin 是 uint8 0/1
            raw = np.fromfile(obin, dtype=np.uint8)
            if raw.size != golden.size:
                raise RuntimeError(f"{cid}: out.bin {raw.size}B ≠ 期望 {golden.size}（形状/传输异常）")
            if raw.size and not np.isin(raw, (0, 1)).all():
                raise RuntimeError(f"{cid}: out.bin 含非 0/1 值，非法 bool 输出")
            out = raw.reshape(golden.shape).astype(bool) if golden.size else raw.astype(bool)
        else:                                 # numerical：out.bin 同输入 dtype
            # finding #8：storage-aware 读回——当前 new_example v1 仅 fp32/fp16（storage==logical，Track C 已拦
            # int/bf16）。显式断言 storage==logical，杜绝「numerical out 却非原生落盘」被 np.fromfile(golden.dtype)
            # 静默按逻辑 dtype 误读。bf16 放开前须在此补 uint16→f32 读回分支（走 readback_output）。
            if _expected_storage(dtn) != dtn:
                raise RuntimeError(f"{cid}: numerical out storage({_expected_storage(dtn)})≠logical({dtn})——"
                                   f"new_example v1 未接 storage-aware 读回（bf16 须补 readback_output 分支）")
            raw = np.fromfile(obin, dtype=golden.dtype)
            if raw.size != golden.size:
                raise RuntimeError(f"{cid}: out.bin {raw.size} elem ≠ 期望 {golden.size}（形状/传输异常）")
            out = readback_output(raw, {"dtype": dtn}).reshape(golden.shape) if golden.size else raw
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
    return {"op": caseset["op"], "repo_mode": "new_example", "evidence": ev}


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
    caseset = json.load(open(caseset_path, encoding="utf-8"))
    evidence = MODES[mode](caseset, work_dir, defect_cases=defect)
    json.dump(evidence, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[repo_adapter/{mode}] {len(evidence['evidence'])} evidence -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
