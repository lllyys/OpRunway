"""Task 2 · repo_adapter — caseset.json -> evidence.json（纯采集、不判定）。

统一接口，按仓/模式换实现：
- mock          : 本地干跑，kernel 输出 = golden（可注入 defect），perf = 确定性 mock。无需 NPU。
- new_example   : 真机 build/run PR 自带工程（留桩，需 NPU + VPN，之后填）。
证据只记「测到什么」（metric value / us / 路径），pass/fail 交给 validator（ADR 0007）。
"""
import json, math, os, re, shlex, subprocess, sys
import numpy as np

_NP = {"float32": np.float32, "float16": np.float16}
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")     # case_id / host / op：拒空白、slash、shell 特殊字符
_SOC_RE = re.compile(r"^ascend[0-9a-z_]+$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")  # 远端路径：拒 shell 特殊字符（防 scp/ssh 拼接注入）


def _snake(camel):  # IsClose → is_close, Sign → sign（build.sh --ops + experimental/math/<snake>）
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).lower()


def _safe(work_dir, rel):
    """把 caseset 里的相对路径钉在 work_dir 内，拒绝绝对路径 / .. 穿越。"""
    base = os.path.normpath(os.path.abspath(work_dir))
    p = os.path.normpath(os.path.join(base, rel))
    if p != base and not p.startswith(base + os.sep):
        raise ValueError(f"path escapes work_dir: {rel}")
    return p


def _metric(verify_mode, out, golden):
    """采集：按 verify_mode 测误差值（不判 pass/fail）。"""
    if verify_mode == "exact":
        return "exact_mismatch", int(np.count_nonzero(out != golden))
    g, o = golden.astype(np.float64), out.astype(np.float64)
    rel = np.abs(o - g) / (np.abs(g) + 1e-7)
    return "max_rel_err", (float(rel.max()) if rel.size else 0.0)


def _mock_us(numel):
    """确定性 mock kernel 耗时（us）：与输出元素数成比例 + 常数启动。"""
    return round(numel / 2.0e5 + 1.5, 3)


def run_mock(caseset, work_dir, defect_cases=None):
    defect_cases = set(defect_cases or [])
    ev = []
    for c in caseset["cases"]:
        # 加载并校验所有 input（v0 mock 也核，防 caseset 契约漂移）
        for inp in c["inputs"]:
            arr = np.load(_safe(work_dir, inp["path"]))
            if list(arr.shape) != list(inp["shape"]) or str(arr.dtype) != inp["dtype"]:
                raise ValueError(f"{c['id']} input {inp['name']}: got {arr.dtype}{list(arr.shape)} "
                                 f"≠ caseset {inp['dtype']}{inp['shape']}")
        golden = np.load(_safe(work_dir, c["expected"]["golden_path"]))
        out = golden.copy()  # mock：完美 NPU = golden
        if c["id"] in defect_cases and out.size:  # 注入缺陷 → 让 validator 现 fail
            flat = out.reshape(-1)
            flat[0] = (~flat[0]) if out.dtype == bool else (flat[0] + 1)
        out_path = f"{c['id']}/out.npy"
        np.save(_safe(work_dir, out_path), out)
        metric, value = _metric(c["expected"]["verify_mode"], out, golden)
        ev.append({
            "case_id": c["id"], "status": "ok",
            "precision": {"metric": metric, "value": value,
                          "threshold": c["expected"]["threshold"],
                          "golden_path": c["expected"]["golden_path"], "out_path": out_path},
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
    op = _snake(caseset["op"])   # 算子 snake 名驱动 build/目录（IsClose→is_close, Sign→sign）
    if not _ID_RE.match(host): raise ValueError(f"非法 host: {host!r}")
    if not _ID_RE.match(op): raise ValueError(f"非法 op: {op!r}")
    if not _ID_RE.match(vendor): raise ValueError(f"非法 vendor: {vendor!r}")
    if not _SOC_RE.match(soc): raise ValueError(f"非法 soc: {soc!r}")
    for k, p in (("remote_dir", rroot), ("ops_repo", ops), ("opp", opp), ("setenv", cfg["setenv"])):
        if not _PATH_RE.match(p): raise ValueError(f"非法路径 {k}: {p!r}")
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
        if not _ID_RE.match(cid):
            raise ValueError(f"非法 case_id: {cid!r}")
        dtn = c["inputs"][0]["dtype"]
        if dtn not in _NP:
            raise ValueError(f"{cid}: runner v1 不支持 dtype {dtn}（仅 {sorted(_NP)}）")
        if any(inp["dtype"] != dtn for inp in c["inputs"]):  # runner v1 全输入同 dtype，拒静默强转
            raise ValueError(f"{cid}: 多输入 dtype 不一致 {[i['dtype'] for i in c['inputs']]}"
                             f"（runner v1 要求同 dtype；混合 dtype 需 per-input manifest）")
        arrs = []
        for inp in c["inputs"]:
            arr = np.load(_safe(work_dir, inp["path"]))
            if list(arr.shape) != list(inp["shape"]) or str(arr.dtype) != inp["dtype"]:
                raise ValueError(f"{cid} {inp['name']}: npy {arr.dtype}{list(arr.shape)} "
                                 f"≠ caseset {inp['dtype']}{inp['shape']}")
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
                arr = np.broadcast_to(arr, out_shape).copy()
            arr.astype(_NP[dtn]).tofile(_safe(work_dir, f"{cid}/x{j + 1}.bin"))
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
    subprocess.run(["ssh", host,
                    f"rm -rf {qcases} && mkdir -p {qcases} && "
                    f"tar xzf /tmp/oprunway_deploy.tgz -C {qcases} && rm -f /tmp/oprunway_deploy.tgz && "
                    f"cp {qcases}/perfcases_list.txt {q(rroot + '/perfcases_list.txt')}"],
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
            raw = np.fromfile(obin, dtype=golden.dtype)
            if raw.size != golden.size:
                raise RuntimeError(f"{cid}: out.bin {raw.size} elem ≠ 期望 {golden.size}（形状/传输异常）")
            out = raw.reshape(golden.shape) if golden.size else raw
        metric, value = _metric(c["expected"]["verify_mode"], out, golden)
        ev.append({"case_id": cid, "status": "ok",
                   "precision": {"metric": metric, "value": value,
                                 "threshold": c["expected"]["threshold"],
                                 "golden_path": c["expected"]["golden_path"],
                                 "out_path": f"{cid}/out.bin"},
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
