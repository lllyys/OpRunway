"""P3 · catlass repo-adapter —— CatlassBasicMatmul：spec → NPU 精度+性能 evidence（纯采集、不判定）。

canon 归属（trust tier 已核）：
- 这是 repo-adapter.md【canonical】三接入模式里的 **generated_harness**（我们自造 bin-IO 调用壳去包
  catlass 自带 example 的 kernel），非新造第 4 种模式；代码以 `harness_kind="generated_harness"` 落字段。
- 落 generated-harness-responsibilities.md【canonical】4 职责：bin-IO shim / layout 字节契约（X_logical 喂
  golden、X_bin 按声明 layout 摆物理字节，分两份造·禁共用 reshape）/ 固定 seed 数据注入 golden 同源 /
  性能测量栈双边同 timing_scope。
- 机制依 catlass-acceptance-mechanics.md【canonical/verified】：`build.sh <example> -DCATLASS_ARCH` →
  `./output/bin/<example> m n k deviceId` 打印 `Compare success/failed`（**只是仓内 smoke，非验收结论**）；
  golden=CPU host float32；性能=msprof op kernel-only Task Duration(us)。msTuner 是调优、非验收。
- ADR0002【canonical】：精度=真 NPU out vs 我们 numpy golden；性能=msprof kernel-only；catlass 自带对比只作 smoke。
- ADR0009【canonical】：一套泛化 workflow + 每仓一薄 adapter；catlass 差异是「数据」（CATLASS_PROFILE 承载）。
- 路线 A/B（catlass-to-aclnn-bridge.md）本 todo **不落真桥**，走「复用 example 工程 + 换入 bin-IO runner」最低风险路径。

⚠ 边界（诚实）：
- 判定不归本模块 —— adapter 只产 evidence，pass/fail 归 validator/perf_compare/validate_acceptance_state（ADR0007）。
- CatlassBasicMatmul 是 catlass **库自带 example**，无真实任务书↔PR → demo spec 为 **synthetic**，本模块产的一切
  「PASS」仅证明管路/门接通，**非 NPU 验收裁决**（acceptance BLOCKED-on-real-NPU / BLOCKED-on-real-provenance）。
- **真机全部待验**：runner 能否在 bisheng/ccec 编成、extern C 符号能否被 msprof 命中、Task Duration 实数、staging
  与 build.sh 交互 —— Mac 上只能写+静态审。真机路径 run_catlass 留桩、须 ascend-a5(arch 3510)+VPN+人工确认。
"""
import argparse, hashlib, json, math, os, re, subprocess, sys, time
import numpy as np

import catlass_parse  # 解析层（stdlib）
import precision_policy  # 精度口径 SSOT（据 spec 派生 cdtype/standard/policy，与 validator 同源）

_NP = {"float32": np.float32, "float16": np.float16}
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_ARCH_WHITELIST = ("2201", "3510")   # 白名单枚举（codex #7：非默认/兜底，仅枚举）
_HERE = os.path.dirname(os.path.abspath(__file__))
_CATLASS_DIR = os.path.join(_HERE, "catlass")   # runner/CMake/staging/orchestration 模板源


def _safe(work_dir, rel):
    """把相对路径钉在 work_dir 内，拒绝绝对路径 / .. 穿越（同 repo_adapter._safe）。"""
    base = os.path.normpath(os.path.abspath(work_dir))
    p = os.path.normpath(os.path.join(base, rel))
    if p != base and not p.startswith(base + os.sep):
        raise ValueError(f"path escapes work_dir: {rel}")
    return p


# ============================================================ 子任务①：arch 运行时探测
# SOC→arch 已知映射（**枚举、非猜测**；据 CLAUDE.md/ build.sh：950 系→3510，910/A3→2201）。
_SOC_ARCH = {"ascend950pr": "3510", "ascend950dt": "3510", "ascend950": "3510",
             "ascend910_9382": "2201", "ascend910b": "2201", "ascend910_93": "2201"}


def _soc_to_arch(soc):
    key = re.sub(r"[^a-z0-9_]", "", (soc or "").lower())
    for pat, arch in _SOC_ARCH.items():
        if key.startswith(pat):
            return arch
    return None


def _catlass_arch(work_dir=None, explicit=None):
    """运行时探测 CATLASS_ARCH —— **production 路径无任何默认/兜底 arch 字面量**（子任务①）。

    读序：显式入参 → env OPRUNWAY_CATLASS_ARCH → work_dir/environment.json（可选补充）→ **诚实报错**。
    environment.json schema（producer=部署探测脚本，可选）：{"catlass_arch":"3510"} 或 {"soc":"Ascend950PR_9579"}。
    白名单 {2201,3510}；探不到/非法一律 raise，绝不猜 3510。
    """
    src = None
    val = explicit
    if val:
        src = "explicit"
    if val is None:
        val = os.environ.get("OPRUNWAY_CATLASS_ARCH")
        if val:
            src = "env:OPRUNWAY_CATLASS_ARCH"
    if val is None and work_dir:
        ep = os.path.join(work_dir, "environment.json")
        if os.path.exists(ep):
            try:
                env = json.load(open(ep, encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                raise ValueError(f"environment.json 解析失败：{e}")
            if isinstance(env, dict):
                if env.get("catlass_arch"):
                    val, src = str(env["catlass_arch"]), "environment.json:catlass_arch"
                elif env.get("soc") or env.get("npu_model"):
                    soc = env.get("soc") or env.get("npu_model")
                    mapped = _soc_to_arch(soc)
                    if not mapped:
                        raise ValueError(
                            f"environment.json 的 soc={soc!r} 无法映射到已知 arch —— "
                            f"请显式给 catlass_arch 或 OPRUNWAY_CATLASS_ARCH（不猜）")
                    val, src = mapped, f"environment.json:soc({soc})"
    if val is None:
        raise ValueError(
            "探测不到 CATLASS_ARCH —— 请设 OPRUNWAY_CATLASS_ARCH ∈ {2201,3510}、或提供 "
            "work_dir/environment.json（{\"catlass_arch\":...} 或 {\"soc\":...}）。"
            "本模块**不设默认 arch、不猜 3510**（零硬编码）。")
    val = str(val).strip()
    if val not in _ARCH_WHITELIST:
        raise ValueError(f"非法 CATLASS_ARCH={val!r}（来源 {src}）；仅支持白名单 {_ARCH_WHITELIST}")
    return val, src


# ============================================================ 子任务②：arch 索引 profile + CMake arch 注入
# arch 决定 example/源码/ArchTag/dtype/runner（codex #8/#9；已核 examples/CMakeLists.txt 的 arch 列表）。
CATLASS_PROFILE = {
    "3510": {"example": "43_ascend950_basic_matmul", "src": "basic_matmul_tla.cpp",
             "archtag": "Ascend950", "dtype": "float32",
             "runner": "oprunway_catlass_basic_matmul_950_runner.cpp",
             "harness": "oprunway_catlass_basic_matmul_950",
             "kernel_symbol": "oprunway_catlass_basic_matmul_950",   # extern C 钉死名（真机回填校准）
             "layouts": {"A": "RowMajor", "B": "RowMajor", "C": "RowMajor"},
             "role": "主目标（ascend-a5 真 950/fp32，任务书目标平台）"},
    "2201": {"example": "00_basic_matmul", "src": "basic_matmul.cpp",
             "archtag": "AtlasA2", "dtype": "float16",
             "runner": "oprunway_catlass_basic_matmul_a2_runner.cpp",
             "harness": "oprunway_catlass_basic_matmul_a2",
             "kernel_symbol": "oprunway_catlass_basic_matmul_a2",
             "layouts": {"A": "RowMajor", "B": "RowMajor", "C": "RowMajor"},
             "role": "次目标（ascend-a3 910/A3 fp16，de-risk）"},
}


def catlass_profile(arch):
    if arch not in CATLASS_PROFILE:
        raise ValueError(f"无 arch={arch!r} 的 CATLASS_PROFILE（仅 {list(CATLASS_PROFILE)}）")
    return CATLASS_PROFILE[arch]


def cmake_arch_option(arch):
    """CMake arch 注入片段（子任务②）：build.sh 透传给 cmake 的 -DCATLASS_ARCH。"""
    if arch not in _ARCH_WHITELIST:
        raise ValueError(f"非法 arch={arch!r}，拒绝拼 CMake 选项（白名单 {_ARCH_WHITELIST}）")
    return f"-DCATLASS_ARCH={arch}"


def build_command(arch, harness=None):
    """拼 build.sh 命令（example 选择 + CMake arch 注入，子任务②）。

    真机跑 stage_into_catlass 注入后，`build.sh <harness> -DCATLASS_ARCH=<arch>`；harness 缺省用 profile 的。
    返回 (argv 列表, 展示串)；不在此执行（真机编排在 run_on_catlass_npu.sh）。
    """
    prof = catlass_profile(arch)
    target = harness or prof["harness"]
    if not _ID_RE.match(target):
        raise ValueError(f"非法 build target: {target!r}")
    argv = ["bash", "scripts/build.sh", target, cmake_arch_option(arch)]
    return argv, " ".join(argv)


# ============================================================ 子任务③：matmul golden + materialize（layout 字节契约）
def golden_catlass_matmul(a, b, out_dtype):
    """CPU host float32 matmul golden（对齐 catlass examples/common/golden/matmul.hpp 的 fp32 累加语义）。

    ComputeMatmul 在 ElementGolden=float 累加 A[i,k]*B[k,j] 再回落输出 dtype → 我们 numpy 同源：
    C = (A.f32 @ B.f32) 再 cast 到 out_dtype。golden_source 记此来源。
    """
    c = a.astype(np.float32) @ b.astype(np.float32)
    return c.astype(_NP[out_dtype])


GOLDEN_SOURCE = ("numpy f32 matmul（A.f32@B.f32 再回落 dtype），对齐 catlass "
                 "examples/common/golden/matmul.hpp CPU host float32 累加语义")


def split_logical_physical(arr, layout):
    """layout 字节契约（generated-harness 职责2）：**分两份造、禁共用一次 reshape**。

    - X_logical：喂 golden，按**逻辑形状**（numpy 逻辑索引算参考）。
    - X_bin（bytes）：喂 kernel，按算子**声明的 layout 摆物理字节**，设备直接读。
    RowMajor → 物理=C 序；ColumnMajor → 物理=F 序（转置摆放）。两者独立产出，非同一 buffer 的别名。
    返回 (x_logical: np.ndarray 独立副本, x_bin: bytes)。
    """
    x_logical = np.array(arr, copy=True)               # 独立副本，喂 golden（逻辑）
    contig = np.ascontiguousarray(arr)
    if layout == "RowMajor":
        x_bin = contig.tobytes(order="C")
    elif layout == "ColumnMajor":
        x_bin = contig.tobytes(order="F")              # 列主序物理字节 ≠ 逻辑行主序
    else:
        raise ValueError(f"未知 layout={layout!r}（仅 RowMajor/ColumnMajor）")
    return x_logical, x_bin


# ============================================================ Task-1 侧：matmul caseset builder（demo·synthetic）
# 注：matmul caseset **有意**由本 builder 产、不进 gen_cases 的 GOLDEN 字典——GOLDEN 是 elementwise 引擎
# （golden_fn(inputs, attrs)、plan=dtype×shape/broadcast/attr_matrix），与 matmul 的 (m,n,k) plan + A[m,k]/B[k,n]
# 专属输入构造结构不兼容，塞进去无法工作（T4-① 调查结论）。本 builder 是 adapter 自带 demo caseset 产生器，
# schema 与 gen_cases 产物一致（id/dims/tags/inputs/attrs/expected），供本 adapter 端到端跑穿；matmul 走独立
# plan，不套 elementwise broadcast。（若日后要统一 Task-1 生成器，是「单一 oracle」的产品级设计取舍，非本注释所指的即将落地项。）
_SEED = 2026


def _matmul_shapes(spec):
    """从 spec.cases 读可配置 shape（非死钉 1024³，codex #20）；缺省给功能/精度小 shape + 性能大 shape。"""
    cfg = (spec.get("cases") or {}) if isinstance(spec.get("cases"), dict) else {}
    func = cfg.get("functional") or [[16, 16, 16], [32, 64, 48]]
    perf = cfg.get("perf") or [[512, 512, 512]]
    return [tuple(s) for s in func], [tuple(s) for s in perf]


def build_matmul_caseset(spec, work_dir):
    """spec → matmul caseset（+ 每 case A/B/golden .npy）。A[m,k]/B[k,n]→C[m,n]，全 RowMajor。"""
    op = spec["op"]
    dtype = spec.get("precision", {}).get("dtype") or spec.get("dtype") or "float32"
    if dtype not in _NP:
        raise ValueError(f"matmul builder 暂支持 {sorted(_NP)}，spec dtype={dtype!r}")
    vmode = spec.get("verify_mode", "numerical")
    spec_standard = precision_policy.select_standard(spec)  # 平台层标准（显式或按 oracle+verify_mode 映射）
    layouts = spec.get("layouts") or {"A": "RowMajor", "B": "RowMajor", "C": "RowMajor"}
    rng = np.random.default_rng(_SEED)   # 固定 seed（职责3：可复现、golden 同源）
    func_shapes, perf_shapes = _matmul_shapes(spec)
    os.makedirs(work_dir, exist_ok=True)

    plan = [(["功能", "精度"], s, ["常规"]) for s in func_shapes]
    plan += [(["性能"], s, ["性能", "大shape"]) for s in perf_shapes]

    cases = []
    for i, (dims, (m, n, k), tags) in enumerate(plan):
        cid = f"{op.lower()}_{i:03d}"
        cdir = os.path.join(work_dir, cid)
        os.makedirs(cdir, exist_ok=True)
        dt = _NP[dtype]
        a = rng.uniform(-2.0, 2.0, size=(m, k)).astype(dt)
        b = rng.uniform(-2.0, 2.0, size=(k, n)).astype(dt)
        golden = golden_catlass_matmul(a, b, dtype)
        np.save(os.path.join(cdir, "A.npy"), a)
        np.save(os.path.join(cdir, "B.npy"), b)
        np.save(os.path.join(cdir, "golden.npy"), golden)
        # 精度口径 per-case（对齐 gen_cases / validator 权威）：compare_dtype **据 spec IO 矩阵派生**
        # （derive_output_dtype，与 validator 同源，绝不取自声明）；standard/policy/阈值 digest 全据 spec 复算。
        # matmul numerical fp32 → compare=rel_err（沿用 ascendoptest_default，向后兼容）。
        case_in_dts = [("A", dtype), ("B", dtype)]
        cdtype = precision_policy.derive_output_dtype(spec, case_in_dts)
        compare = "exact_equal" if vmode == "exact" else "rel_err"
        eff_std = precision_policy.effective_standard(spec_standard, cdtype, compare)
        policy = precision_policy.threshold_for(eff_std, cdtype)
        expected = {
            "golden_source": GOLDEN_SOURCE, "golden_path": f"{cid}/golden.npy",
            "verify_mode": vmode, "standard": eff_std, "compare_dtype": cdtype,
            "compare": compare,
            "tolerance_policy_id": precision_policy.tolerance_policy_id(eff_std, cdtype),
            "policy": policy, "threshold": precision_policy.threshold_digest(policy)}
        acc = precision_policy.resolve_acceptance(spec, eff_std, cdtype)
        if acc:  # spec 未声明 acceptance → None → 不私带（validator finding #3：防换入口重演 T5 洞）
            expected["acceptance_policy"], expected["acceptance_tolerance_policy_id"] = acc
        cases.append({
            "id": cid, "dims": dims, "tags": tags,
            "inputs": [{"name": "A", "shape": [m, k], "dtype": dtype,
                        "path": f"{cid}/A.npy", "layout": layouts["A"]},
                       {"name": "B", "shape": [k, n], "dtype": dtype,
                        "path": f"{cid}/B.npy", "layout": layouts["B"]}],
            "attrs": {}, "matmul": {"m": m, "n": n, "k": k}, "layout": layouts,
            "case_origin": "synthetic-demo（catlass 库 example，无真实 task_doc/PR）",
            "expected": expected,
        })
    return {"op": op, "spec_ref": spec.get("op"), "work_dir": work_dir, "attr_order": [],
            "harness_kind": "generated_harness",
            "provenance": spec.get("provenance", {"kind": "synthetic"}),
            "cases": cases}


# ============================================================ repo-adapter.md 的 7 方法
def discover(caseset, work_dir, arch=None):
    """① discover —— 探 arch、定 profile、校验 caseset，产 ctx（后 6 方法共享）。"""
    resolved, arch_src = _catlass_arch(work_dir, explicit=arch)
    prof = catlass_profile(resolved)
    op = caseset["op"]
    if not _ID_RE.match(op):
        raise ValueError(f"非法 op: {op!r}")
    cases = caseset["cases"]
    perf_ids = [c["id"] for c in cases if "性能" in c.get("dims", [])]
    for c in cases:
        if not _ID_RE.match(c["id"]):
            raise ValueError(f"非法 case_id: {c['id']!r}")
        if "matmul" not in c:
            raise ValueError(f"{c['id']}: 缺 matmul{{m,n,k}}（catlass adapter 只吃 matmul caseset）")
    return {"arch": resolved, "arch_src": arch_src, "profile": prof, "op": op,
            "work_dir": work_dir, "cases": cases, "perf_ids": perf_ids,
            "harness_kind": "generated_harness"}


def build(ctx, mode):
    """② build —— mock：不编译（记下**将要跑**的 build 命令）；real：stage + build.sh（真机·留桩）。"""
    argv, disp = build_command(ctx["arch"], ctx["profile"]["harness"])
    ctx["build_cmd"] = disp
    ctx["cmake_arch_option"] = cmake_arch_option(ctx["arch"])
    if mode == "mock":
        ctx["built"] = "mock:no-compile"      # 本地不编译（无 bisheng/ccec）
    else:
        ctx["built"] = "real:staged+build.sh"  # 真机编排见 run_on_catlass_npu.sh
    return ctx


def materialize_case(case, ctx):
    """③ materialize_case —— 固定 seed 数据 + layout 字节契约：X_logical 喂 golden、X_bin 摆物理字节。

    A/B 各分两份造（split_logical_physical，禁共用 reshape）；写 <cid>/A.bin、<cid>/B.bin（物理字节）。
    返回 manifest 行 "cid dtype m n k"（矩阵三维，非广播 out_shape）。
    """
    wd, prof = ctx["work_dir"], ctx["profile"]
    cid = case["id"]
    mm = case["matmul"]
    line_parts = [cid, prof["dtype"]]
    for inp in case["inputs"]:
        arr = np.load(_safe(wd, inp["path"]))
        if str(arr.dtype) != prof["dtype"]:
            raise ValueError(f"{cid} {inp['name']}: npy dtype {arr.dtype} ≠ profile {prof['dtype']}")
        layout = inp.get("layout", prof["layouts"].get(inp["name"], "RowMajor"))
        _x_logical, x_bin = split_logical_physical(arr, layout)   # 分两份：logical(→golden) / bin(→kernel)
        with open(_safe(wd, f"{cid}/{inp['name']}.bin"), "wb") as f:
            f.write(x_bin)
    line_parts += [str(mm["m"]), str(mm["n"]), str(mm["k"])]
    return " ".join(line_parts)


def run_correctness(ctx, defect_cases=None, real_outs=None):
    """④ run_correctness —— mock：kernel out=golden（可注入 defect）；real：读拉回的 out.bin。返回 {cid: out ndarray}。"""
    defect_cases = set(defect_cases or [])
    wd = ctx["work_dir"]
    outs = {}
    for c in ctx["cases"]:
        cid = c["id"]
        golden = np.load(_safe(wd, c["expected"]["golden_path"]))
        if real_outs is not None:      # 真机：out.bin 已按 dtype 读成 ndarray 传入
            out = real_outs[cid]
        else:                          # mock：完美 NPU = golden
            out = golden.copy()
            if cid in defect_cases and out.size:   # 注入缺陷 → 让 validator 现 fail
                out.reshape(-1)[0] = out.reshape(-1)[0] + out.dtype.type(1)
        np.save(_safe(wd, f"{cid}/out.npy"), out)
        outs[cid] = out
    return outs


def _mock_us(numel):
    """确定性 mock kernel 耗时（us）：与输出元素数成比例 + 常数启动（同 repo_adapter._mock_us 口径）。"""
    return round(numel / 2.0e5 + 1.5, 3)


def run_perf(ctx, real_perf=None):
    """⑤ run_perf —— mock：确定性 us；real：msprof kernel-only 中位（catlass_parse 解析）。返回 {cid: perf}。"""
    perfs = {}
    for c in ctx["cases"]:
        cid = c["id"]
        if cid not in ctx["perf_ids"]:
            continue
        if real_perf is not None:      # 真机：{cid: {us,p90,min,...}}
            perfs[cid] = {"scope": "kernel_only", **real_perf.get(cid, {"us": None}),
                          "note": "msprof op Task Duration(us) 中位（真 kernel-only）"}
        else:
            mm = c["matmul"]
            perfs[cid] = {"scope": "kernel_only",
                          "us": _mock_us(int(mm["m"]) * int(mm["n"])),
                          "note": "确定性 mock（非真机）"}
    return perfs


def parse_results(ctx, outs, perfs, defect_cases=None):
    """⑥ parse_results —— 组装 evidence（结构化 precision + perf）。schema 对齐 repo_adapter._precision_evidence。

    误差分布**确定性复算**（precision_policy.compute_metrics，据 caseset.expected.policy），**不判 pass/fail**；
    standard/tolerance_policy_id/policy/threshold(digest) 一律**从 caseset.expected 抄**（三处一致的一环，
    adapter 不自造阈值），故与 caseset / spec-canonical 全等，validator 据 spec 复算后要求全等即成立。
    spec 未声明 acceptance → expected 无 acceptance_policy → evidence 亦不带（防 finding #3 洞重演）。
    """
    wd = ctx["work_dir"]
    ev = []
    for c in ctx["cases"]:
        cid = c["id"]
        golden = np.load(_safe(wd, c["expected"]["golden_path"]))
        out = outs[cid]
        exp = c["expected"]
        policy = exp["policy"]
        prec = {"standard": exp["standard"],
                "tolerance_policy_id": exp["tolerance_policy_id"],
                "policy": policy,
                "threshold": exp["threshold"],
                # oracle_source **据 caseset 声明的 golden_source 据实映射**（不再写死 cpu_ref）：
                # catlass 的 numpy f32 matmul（GOLDEN_SOURCE 以 "numpy" 起头）→ analytical_ref；
                # 来源缺失/不可识别 → fail-closed（不默认）。
                "oracle_source": precision_policy.oracle_source_from_golden(exp.get("golden_source")),
                "not_settled": bool(policy.get("not_settled", False)),
                "metrics": precision_policy.compute_metrics(out, golden, policy),
                "golden_path": exp["golden_path"], "out_path": f"{cid}/out.npy",
                # A 方案 evidence↔产物绑定（同 repo_adapter._precision_evidence）：对落盘 golden/out 字节算
                # sha256+numel，供门 gate_task2 独立重算校验 metrics 真伪。catlass 若缺 provenance 会被新门判
                # FAILED，故此处必须同带。⚠ 边界同上：只证 metrics 由这两文件算出，不证文件来自真 NPU 跑测。
                "provenance": {"golden_sha256": _file_sha256(_safe(wd, exp["golden_path"])),
                               "out_sha256": _file_sha256(_safe(wd, f"{cid}/out.npy")),
                               "numel": int(np.asarray(golden).size)}}
        ap = exp.get("acceptance_policy")
        if ap:  # spec 未声明 acceptance → 无此键 → evidence 不带 acceptance_*（防 finding #3）
            prec["acceptance_policy"] = ap
            prec["acceptance_tolerance_policy_id"] = exp.get("acceptance_tolerance_policy_id")
            prec["acceptance_metrics"] = precision_policy.compute_metrics(out, golden, ap)
        entry = {"case_id": cid, "status": "ok", "precision": prec}
        if cid in ctx["perf_ids"]:
            entry["perf"] = perfs.get(cid, {"scope": "kernel_only", "us": None})
        else:   # 非性能用例：perf na（保持 kernel_only 口径字段一致）
            entry["perf"] = {"scope": "kernel_only", "us": None, "note": "非性能用例"}
        ev.append(entry)
    return ev


def collect_artifacts(ctx, extra=None):
    """⑦ collect_artifacts —— 落 artifact_manifest.json（provenance：commit/build cmd/版本/hash，codex #18）。"""
    wd = ctx["work_dir"]
    prof = ctx["profile"]
    manifest = {
        "op": ctx["op"], "arch": ctx["arch"], "arch_source": ctx["arch_src"],
        "harness_kind": ctx["harness_kind"], "harness": prof["harness"],
        "example": prof["example"], "archtag": prof["archtag"], "dtype": prof["dtype"],
        "kernel_symbol": prof["kernel_symbol"], "layouts": prof["layouts"],
        "build_cmd": ctx.get("build_cmd"), "cmake_arch_option": ctx.get("cmake_arch_option"),
        "runner_cpp": prof["runner"],
        "runner_sha256": _file_sha256(os.path.join(_CATLASS_DIR, prof["runner"])),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "catlass_commit": (extra or {}).get("catlass_commit", "待真机采集"),
        "cann_version": (extra or {}).get("cann_version", "待真机采集"),
        "bisheng_version": (extra or {}).get("bisheng_version", "待真机采集"),
        "msprof_version": (extra or {}).get("msprof_version", "待真机采集"),
        "raw_log_sha256": (extra or {}).get("raw_log_sha256"),
        "profile_csv_sha256": (extra or {}).get("profile_csv_sha256"),
    }
    with open(os.path.join(wd, "artifact_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def _file_sha256(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================ 两个 MODE：mock（本地）/ real（真机·留桩）
def _write_manifest(ctx):
    """逐 case materialize + 写 manifest.txt / perfcases_list.txt（真机跑测的中立契约）。"""
    wd = ctx["work_dir"]
    manifest = [materialize_case(c, ctx) for c in ctx["cases"]]
    with open(os.path.join(wd, "manifest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(manifest) + "\n")
    with open(os.path.join(wd, "perfcases_list.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(ctx["perf_ids"]) + ("\n" if ctx["perf_ids"] else ""))


def run_catlass_mock(caseset, work_dir, defect_cases=None):
    """本地端到端 mock：跑穿 7 阶段证明「管路接通」，**evidence_grade=development、非 NPU 验收**。

    kernel out=golden（可注入 defect）、perf=确定性 mock。无需 NPU/bisheng。
    """
    ctx = discover(caseset, work_dir)
    build(ctx, "mock")
    _write_manifest(ctx)                                   # ③ materialize（含 layout 字节契约）
    outs = run_correctness(ctx, defect_cases=defect_cases)  # ④
    perfs = run_perf(ctx)                                   # ⑤
    ev = parse_results(ctx, outs, perfs, defect_cases)      # ⑥
    collect_artifacts(ctx)                                  # ⑦
    return {"op": caseset["op"], "repo_mode": "catlass_mock",
            "harness_kind": "generated_harness", "evidence_grade": "development",
            "acceptance_note": "NON-ACCEPTANCE (mock evidence)：仅证管路/门接通，非 NPU 验收",
            "arch": ctx["arch"], "evidence": ev}


def run_catlass(caseset, work_dir, defect_cases=None):
    """真机采集（ascend-a5 arch 3510/fp32）：stage→build→run→pull→parse→collect。

    ⚠⚠ **本轮不跑真机**（CLAUDE.md #1/#3 副作用门 + generated_harness 高风险首跑须人工确认）。
    本地可跑的部分（arch 探测 / materialize / manifest）已就绪；真正 build/run/msprof 硬阻塞于
    ascend-a5(arch3510)+VPN+人工确认。为防误触发 ssh，默认 fail-fast，须显式 OPRUNWAY_CATLASS_REAL=1 opt-in。
    catlass **无 builtin-TBE 分母** → 不写 _real_baseline，perf 分母走外部 GPU（gpu_external，ADR0006）。
    evidence_grade=acceptance_candidate（真机 evidence 就绪后才谈裁决）。
    """
    ctx = discover(caseset, work_dir)
    build(ctx, "real")
    _write_manifest(ctx)   # 本地即可 materialize（写 A/B.bin + manifest），供真机部署
    if os.environ.get("OPRUNWAY_CATLASS_REAL") != "1":
        raise RuntimeError(
            "run_catlass 真机路径未启用 —— 待 ascend-a5(arch 3510/fp32)+VPN+人工确认。"
            "本地已完成 arch 探测/materialize/manifest；真 build/run/msprof/命中门须真机。"
            "确须真机跑请设 OPRUNWAY_CATLASS_REAL=1（并已人工确认 staging 改 catlass 工作副本的副作用）。")
    # —— 以下真机编排：调 catlass/{stage_into_catlass.sh, run_on_catlass_npu.sh}，解析真 CSV。
    #    本轮 Mac 无法验证，逻辑先备（结构镜像 repo_adapter.run_new_example）。
    return _run_catlass_real(ctx)   # 真机实现（留桩，见函数内 NotImplemented 边界）


def _run_catlass_real(ctx):
    """真机实现骨架（留桩）：deploy→stage→build.sh→run→msprof→pull→catlass_parse→collect。

    真值全部待 ascend-a5 验证：runner 能否编成、extern C 符号能否被 msprof `-k` 命中、Task Duration 实数。
    profile_hit_gate 逻辑就绪、真值未验（catlass_parse.profile_hit_gate 标 pending）。
    """
    raise NotImplementedError(
        "真机编排留桩：接入前须①ascend-a5(3510)+VPN ②人工确认 stage_into_catlass 注入 catlass 工作副本 "
        "③首跑 generated_harness 人工确认。届时用 catlass/run_on_catlass_npu.sh + catlass_parse 解析真 CSV。")


# ============================================================ 子任务⑥：外部 GPU 基线 schema 对齐（对接点）
# 注：gpu_baseline.py 由并行任务建，本模块**不建同名文件**，只定义 adapter 侧对接点 load_external_baseline。
# GPU 标杆最小字段契约（ADR0006，供外部 Task 3 产数据时对齐）：
GPU_BASELINE_CONTRACT = [
    "case_id", "device", "dtype", "shape", "attrs", "timing_scope", "warmup", "iters",
    "sync", "statistic", "unit", "value", "tool", "clock", "power", "data_transfer_included"]


def load_external_baseline(path, perf_case_ids):
    """校验外部 GPU 基线并转成 perf_compare 可吃的 baseline（子任务⑥）。

    校验：source 存在、scope==kernel_only（不符→blocked，ADR0006）、per_case 每项 case_id+us 有限>0；
    **规则可机检**：全部性能用例必须有 baseline（缺→blocked）；extras 忽略并告警。
    成功 → {source, scope, per_case:[{case_id,us,env}]}（perf_compare 同 schema）；
    失败 → 抛 BaselineBlocked（带 reason），绝不静默放过。
    """
    try:
        bl = json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise BaselineBlocked(f"外部 GPU 基线读/解析失败：{e}")
    if not isinstance(bl, dict):
        raise BaselineBlocked("外部 GPU 基线非对象")
    src = bl.get("source")
    if not src:
        raise BaselineBlocked("外部 GPU 基线缺 source")
    scope = bl.get("scope")
    if scope != "kernel_only":       # scope 不符即 blocked（ADR0006：双边同 scope）
        raise BaselineBlocked(f"BLOCKED_INCOMPARABLE_SCOPE：baseline scope={scope!r} ≠ kernel_only")
    per_in = bl.get("per_case")
    if not isinstance(per_in, list):
        raise BaselineBlocked("外部 GPU 基线 per_case 非列表")
    by_id, warnings = {}, []
    for i, r in enumerate(per_in):
        if not isinstance(r, dict) or not r.get("case_id"):
            warnings.append(f"per_case[{i}] 缺 case_id → 忽略")
            continue
        us = r.get("us", r.get("value"))
        try:
            fv = float(us)
        except (TypeError, ValueError):
            raise BaselineBlocked(f"{r['case_id']}: us={us!r} 非数值")
        if not (math.isfinite(fv) and fv > 0):
            raise BaselineBlocked(f"{r['case_id']}: us={fv} 非有限正数")
        by_id[r["case_id"]] = round(fv, 3)
    want = set(perf_case_ids)
    missing = want - set(by_id)
    if missing:                      # 性能用例未全覆盖 → blocked（规则可机检，codex #19）
        raise BaselineBlocked(f"外部 GPU 基线缺性能用例 {sorted(missing)}（全部性能用例必须有 baseline）")
    extras = set(by_id) - want
    if extras:
        warnings.append(f"外部 GPU 基线多出用例 {sorted(extras)} → 忽略（告警）")
    per_out = [{"case_id": cid, "us": by_id[cid], "env": f"gpu_external:{src}"}
               for cid in sorted(want)]
    return {"source": src, "scope": "kernel_only", "per_case": per_out,
            "baseline_source": "gpu_external", "warnings": warnings}


class BaselineBlocked(Exception):
    """外部 GPU 基线不合契约 → blocked（不出性能结论）。"""


# ============================================================ 注册 + CLI
CATLASS_MODES = {"catlass": run_catlass, "catlass_mock": run_catlass_mock}


def main(argv):
    ap = argparse.ArgumentParser(description="catlass repo-adapter（generated_harness）")
    ap.add_argument("caseset")
    ap.add_argument("work_dir")
    ap.add_argument("out")
    ap.add_argument("--mode", default="catlass_mock", choices=list(CATLASS_MODES))
    ap.add_argument("--defect", default=None)
    a = ap.parse_args(argv)
    caseset = json.load(open(a.caseset, encoding="utf-8"))
    defect = a.defect.split(",") if a.defect else None
    ev = CATLASS_MODES[a.mode](caseset, a.work_dir, defect_cases=defect)
    json.dump(ev, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[catlass_adapter/{a.mode}] {len(ev['evidence'])} evidence "
          f"(grade={ev.get('evidence_grade')}) -> {a.out}")


if __name__ == "__main__":
    main(sys.argv[1:])
