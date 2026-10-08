#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_package.py —— S1-Cholesky F 卡：正式包装配（复用冻结产物逐字节装包）。

契约：dev-doc/solver/solver-s1-cholesky-spec.md §2.2（包 schema 与 manifest）、
§2.5（CLI）、第 3 节 F 行与写入所有权（**F 复用 B 冻结产物逐字节装包，不重生成；
抽验只核对不覆盖**）。本脚本不含任何数据构造与判据实现：npz 与 perf_baseline
一律字节复制，golden 抽验按算子 dtype 走 d 前缀（实数，spec §2.2）或 z 前缀
（复数，dev-doc/solver/solver-s2-spec.md §4）链路独立重算后**只比对**。

装配来源（--staging 多目录按序发现；任一来源缺失即停，fail-closed，不猜测）：

- cases 数据：第一个含 ``cases/index.json`` 的 staging 目录。index 取 B2 回填件
  （含 ratio_cpu），逐 case npz 取同目录 ``cases/<case_id>.npz``（B1 冻结件）。
- perf_baseline：第一个含 ``<op>/perf_baseline.json``（或根下
  ``perf_baseline.json``）的 staging 目录（C 卡产物）。
- 渲染器：第一个含 ``repo-task-solver-accept/criteria/render_verify.py`` 的
  staging 目录（镜像树根）；verify 双件在装包时经该确定性入口渲染进包
  （D 卡断言复渲逐字节一致，故装包渲染 == D 冻结副本）。

包内容（spec §2.2）：``cases/<case_id>.npz``（逐字节复用）、``cases/index.json``
（B2 件按算子过滤：顶层元数据保留、条目逐字段原样，只做子集选取不改写内容）、
``perf_baseline.json``（逐字节复用）、``verify_accuracy.py`` 与 ``verify_perf.py``
（渲染件）、``manifest.json``（本脚本唯一新生成的实质内容：身份、出处、env、指纹）。

包自检清单（spec 第 3 节 F 行「逐项打钩」；任一项不过 → 退出码 3，不出包）：

1. 逐字节复用——npz 与 perf_baseline 落包后 sha256 与源文件相等；
2. 校验字段（spec §2.2）——全部 case ``A32 == A64.astype(f32)``（B、golden 同理；
   复数 case 按 S2 spec §4 降型到 complex64，字段名不变）；
3. 抽验 3 case——确定性取包内首/中/尾三个 case，实数按 spec §2.2 的 d 前缀链路
   （spotrf=dpotrf；spotrs=dpotrf→dpotrs；spotri=dpotrf→dpotri 存储侧半三角另侧置 0）、
   复数按 S2 spec §4 的 z 前缀链路同构（cpotrf=zpotrf；cpotrs=zpotrf→zpotrs；
   cpotri=zpotrf→zpotri）独立重算 golden64/golden32，与冻结值逐字节一致；
   **只比对，绝不写回**；
4. index 一致——包内 index 条目与源 index 该算子条目逐字段相等，npz/arrays 与实物对得上；
5. 指纹——manifest.fingerprint 覆盖包内除 manifest.json 外全部文件（含 verify 双件；
   分级消费见 accept_run：cases/ 与 perf_baseline 错配阻断，verify 漂移仅告警）。

S3 批量四包（spec：dev-doc/solver/solver-s3-batched-spec.md §5，下称 S3 spec；
Mr.0 2026-09-24 裁定「全都现场造，无论大小」）——**纯脚本装包**，与单算子包路径
完全分流（build_batched），单算子六包路径一个字节不动：

- 包**不携带任何数据数组**：无 cases/*.npz、无 golden、无 ratio_cpu 数组。包内容 =
  gen_data.py（gen_data_cholesky.py 副本）+ canonical 切片 + verify 双件（确定性渲染）
  + sim_dut.py + README（batched 模板）+ perf_baseline.json（逐字节复用）+
  cases/index.json + manifest.json，KB 级。
- index 全部条目记 ``materialize:"gen"``；条目内容来自**装包自检的现场生成运行**：
  装包时对该算子的包内切片（--scope 口径：full=dedup 全量 / s1=二维代表子集）
  逐 case 跑 gen_data（A0 抽样通路，
  HT-2：不落数组，k=min(5,batch) 个代表内容的 golden 与逐内容 ratio_cpu 一并算出），
  sample_map 槽位映射与 ratio k 值列表写入 index **仅作参考**（S3 spec §3：验收与
  自测侧判定时现场重造内容重算，不消费包内参考）。自检数据随后整目录删除，包内
  零数据由 rglob 断言兜底。
- 开发者流程「先造数后测」（S3 spec §5 + HT-2 A0）：批量 case 不落数组，执行器/
  DUT 挂钩按 index 的 sample_map 用包内 gen_data 的 expand_sampled_rows 流式构造
  槽位区间现场喂入；verify 判定时自行重造同一内容并算 golden 与逐内容 ratio
  （同环境逐位一致），先余槽 bit-wise 一致性、后代表槽三层判定。
- 包内 canonical 切片按 --scope 二选一（2026-09-27 全量精度用例决策）：full（缺省）
  = 该算子 dedup 全量（A0 抽样零数据，不物化全量数组，n×batch 大 case 也只算
  k=min(5,batch) 个代表内容）；s1 = 二维代表子集六例（v3 前行为，spec §5 README
  行自洽）。切片顶层 package_scope 字段是 gen_data --select 消费的唯一事实源；
  dedup 对账（刷新后 154→151）与选中六例的对账都在冻结产物
  reports/solver-s3/canonical_batched.json，包内切片以 slice_note 指回。
- batched README 用独立模板 assets/package-readme-template-batched.md（缺失即停，
  fail-closed——通用模板讲逐字节复用流程，对纯脚本包是误导）。

S4 六算子 v2（Mr.0 2026-09-24 裁定：六算子补发纯脚本 v2、与 batched 统一形态；
v1 从未流通无需兼容）——单矩阵六算子的装包自此走 **build_purescript**（纯脚本装包，
与 build_batched 同形态）：

- 包不携带任何数据数组；包内容 = gen_data.py 副本 + canonical 切片（--scope：
  full=该算子全量精度用例，s1=代表子集，2026-09-27 全量精度用例决策）+
  verify 双件（纯脚本形态渲染）+ sim_dut.py + README（纯脚本模板
  assets/package-readme-template-purescript.md，缺失即停）+ perf_baseline.json
  （逐字节复用）+ cases/index.json + manifest.json，KB 级。
- index 全部条目记 ``materialize:"gen"``；条目与 ratio 参考值来自装包自检运行：
  现场生成包内切片全量（--select all 恰为切片，验证 README 第 0 步自洽）→ 校验字段 +
  index/实物对账 + golden 首/中/尾抽验（d/z 链路独立重算，只比对）→ 低精度准备链
  回填逐 case ratio（残差唯一实现仍在 criteria/verdict，经 fill_ratio_cpu.run_chain
  同一入口）。自检数据随后整目录删除，包内零数据由 rglob 断言兜底；ratio 仅作参考，
  判定侧（verify 副本与验收侧）现场同法重算，不消费包内数值。
- 旧 build()（S1/S2 逐字节复用装包，产出已交付六 v1 包）保留在本文件作 provenance，
  不再被路由——非批量装包一律纯脚本（S3 spec §5 通用数据策）。

CLI（spec §2.5 逐字；方括号内为本卡补充的可选项，缺省行为不需要它们）：

    build_package.py --staging <目录...> --out reports/solver-packages/<op>/
                     [--op <op>] [--scope {s1,full}] [--container-id <id>]
                     [--selfcheck <json>]

--scope 选切片口径（2026-09-27 全量精度用例决策）：full（缺省）= 该算子全部精度
用例入包（切片顶层 package_scope:"full"，gen_data --select all 物化全量）；s1 =
二维代表子集（v3 前行为）。--out 末段目录名即算子名（spec §2.5 的 <op>），--op 可
显式覆盖；--container-id
记入 manifest.env.容器标识（缺省取 $OPRUNWAY_CONTAINER_ID，再缺省 "unknown"）；
--selfcheck 把自检清单落成 JSON（演练记录消费）。退出码：0=成功；2=输入/契约错误
（来源缺失、算子无 case）；3=自检不过（此时包目录内容不可信，不写 manifest）。
"""

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

import numpy as np

TOOL = "build_package.py"
TOOL_VER = "s3-F10"  # 复核修复：s1 slice_note 去硬编码旧对账数、docstring 同步 scope 口径；承 s3-F9
OPS = ("spotrf", "spotrs", "spotri", "cpotrf", "cpotrs", "cpotri")
BATCHED_OPS = ("spotrfBatched", "spotrsBatched", "cpotrfBatched", "cpotrsBatched")
ALL_OPS = OPS + BATCHED_OPS
PACKAGE_VER = "s1"
PACKAGE_VER_BATCHED = "s3-batched"   # 纯脚本形态（S3 spec §5），与逐字节复用形态区分
PACKAGE_VER_PURESCRIPT = "s4-v2-purescript"  # 单矩阵六算子 v2（纯脚本形态，S4）
STANDARD_REFS = {
    "三层判定": "repos/solver_tasks-main/cholesky_precision/README.md",
    "阈值表": "repos/opbase-precision-standard/mixed_tolerance_standard.md",
}


class ContractError(RuntimeError):
    """输入不满足契约或来源缺失——停下报错（退出码 2），不猜测、不静默降级。"""


class SelfCheckError(RuntimeError):
    """自检清单有项不过——包不可信（退出码 3），不写 manifest、不覆盖任何源。"""


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 来源发现（模块文档「装配来源」；按 --staging 传入顺序取第一个命中）
# ---------------------------------------------------------------------------

def _index_ratio_filled(index_path):
    try:
        with open(index_path, encoding="utf-8") as fh:
            cases = json.load(fh).get("cases") or []
        return bool(cases) and all(
            c.get("ratio_cpu") is not None or c.get("ratio_cpu_status") == "prep_failed"
            for c in cases)
    except (OSError, ValueError):
        return False


def find_cases_dir(staging_dirs):
    """优先选 ratio_cpu 已回填的 index（B2 产物）；避免命中 B1 骨架造成包内 ratio 全空。"""
    cands = [d / "cases" for d in staging_dirs if (d / "cases" / "index.json").is_file()]
    if not cands:
        raise ContractError(f"staging 目录中找不到 cases/index.json：{[str(d) for d in staging_dirs]}")
    for c in cands:
        if _index_ratio_filled(c / "index.json"):
            return c
    return cands[0]


def find_npz(staging_dirs, index_dir, rel_name):
    """npz 与 index 允许来自不同 staging（B2 只回填 index 不复制 npz）。"""
    for base in [index_dir] + [d / "cases" for d in staging_dirs]:
        cand = base / rel_name
        if cand.is_file():
            return cand
    return None


def find_baseline(staging_dirs, op):
    for d in staging_dirs:
        for cand in (d / op / "perf_baseline.json", d / "perf_baseline.json"):
            if cand.is_file():
                return cand
    raise ContractError(f"staging 目录中找不到 {op} 的 perf_baseline.json")


def find_criteria_dir(staging_dirs):
    sib = Path(__file__).resolve().parent.parent.parent / "repo-task-solver-accept" / "criteria"
    cands = [d / "repo-task-solver-accept" / "criteria" for d in staging_dirs] + [sib]
    for cand in cands:
        if (cand / "render_verify.py").is_file():
            return cand
    raise ContractError(
        "找不到 repo-task-solver-accept/criteria/render_verify.py（staging 或兄弟 skill 目录）")


# ---------------------------------------------------------------------------
# golden 独立重算（spec §2.2 链路的重实现；只用于抽验比对，绝不落盘）
# ---------------------------------------------------------------------------

def _lapack():
    try:
        from scipy.linalg import lapack
    except ImportError as exc:
        raise ContractError(
            "缺 scipy：golden 抽验走 scipy.linalg.lapack 的 d/z 前缀例程（spec §1：容器内补装并记录版本）"
        ) from exc
    return lapack


def _is_complex_op(op):
    """算子 dtype 通路：c 前缀 = 复数（z 链路重算），s 前缀 = 实数（d 链路，S2 spec §0/§4）。"""
    return op.startswith("c")


def _dtype32_of(name64, arr64, case_id):
    """降型目标 dtype（spec §2.2 + S2 spec §4，字段名不变）：float64→float32、
    complex128→complex64；其余 dtype 属 schema 违约，fail-closed。"""
    dt = arr64.dtype
    if dt == np.float64:
        return np.float32
    if dt == np.complex128:
        return np.complex64
    raise SelfCheckError(
        f"{case_id}: {name64} dtype={dt} 不在 float64/complex128（spec §2.2 + S2 spec §4）")


def recompute_golden(op, arrays, uplo, case_id):
    """按 spec §2.2（实数 d 链路）/ S2 spec §4（复数 z 链路）独立重算 golden64
    （C 序）。info!=0 属数据问题，直接抛。"""
    lapack = _lapack()
    lower = uplo == "L"
    a64 = arrays["A64"]
    if _is_complex_op(op):
        if a64.dtype != np.complex128:
            raise SelfCheckError(
                f"{case_id}: {op} 要求 A64=complex128（S2 spec §4），得到 {a64.dtype}")
        potrf, potrs, potri, pfx, spd = (
            lapack.zpotrf, lapack.zpotrs, lapack.zpotri, "z", "HPD")
    else:
        if a64.dtype != np.float64:
            raise SelfCheckError(
                f"{case_id}: {op} 要求 A64=float64（spec §2.2），得到 {a64.dtype}")
        potrf, potrs, potri, pfx, spd = (
            lapack.dpotrf, lapack.dpotrs, lapack.dpotri, "d", "SPD")
    F, info = potrf(a64, lower=lower, clean=1)
    if info != 0:
        raise SelfCheckError(f"{case_id}: 抽验 {pfx}potrf info={info}（{spd} 冻结输入下应为 0）")
    if op.endswith("potrf"):
        g = F
    elif op.endswith("potrs"):
        g, info = potrs(F, arrays["B64"], lower=lower)
        if info != 0:
            raise SelfCheckError(f"{case_id}: 抽验 {pfx}potrs info={info}")
    else:  # *potri
        C, info = potri(F, lower=lower)
        if info != 0:
            raise SelfCheckError(f"{case_id}: 抽验 {pfx}potri info={info}")
        g = np.tril(C) if lower else np.triu(C)
    return np.ascontiguousarray(g)


def spot_check_indices(n_cases):
    """确定性抽样：首/中/尾（case 数 <3 时取全部）。返回有序去重下标。"""
    return sorted({0, n_cases // 2, n_cases - 1})


# ---------------------------------------------------------------------------
# 自检项
# ---------------------------------------------------------------------------

def check_cast_fields(arrays, case_id, case_purpose=None):
    """spec §2.2 校验字段：32 位数组 == 64 位数组降型，逐字节。

    降型目标由 64 位数组的 dtype 决定（float64→float32、complex128→complex64，
    S2 spec §4 字段名不变），实数路径与 S1 版逐位同判。
    HT-8：info 契约用例（case_purpose=="info"）不携带 golden——出现 golden 对
    反而属 schema 违约；其余用例 golden 对仍为必需（fail-closed 不放松）。
    """
    pairs = [("A32", "A64")]
    if case_purpose == "info":
        if "golden64" in arrays or "golden32" in arrays:
            raise SelfCheckError(
                f"{case_id}: info 契约用例不应携带 golden（HT-8：只比 info，无残差）")
    else:
        pairs.append(("golden32", "golden64"))
    if "B64" in arrays:
        pairs.append(("B32", "B64"))
    for a32, a64 in pairs:
        if a32 not in arrays or a64 not in arrays:
            raise SelfCheckError(f"{case_id}: npz 缺数组 {a32}/{a64}（spec §2.2 schema）")
        got = arrays[a32]
        dt32 = _dtype32_of(a64, arrays[a64], case_id)
        want = arrays[a64].astype(dt32)
        if got.dtype != dt32 or got.tobytes() != want.tobytes():
            raise SelfCheckError(
                f"{case_id}: {a32} != {a64}.astype({np.dtype(dt32).name})（spec §2.2 校验字段）")


def check_entry_vs_npz(entry, arrays):
    cid = entry["case_id"]
    declared = entry.get("arrays")
    actual = sorted(arrays.keys())
    if sorted(declared or []) != actual:
        raise SelfCheckError(f"{cid}: index.arrays={declared} 与 npz 实物 {actual} 不符")
    n = entry.get("n")
    if arrays["A64"].shape != (n, n):
        raise SelfCheckError(f"{cid}: A64 形状 {arrays['A64'].shape} != (n,n)=({n},{n})")


# ---------------------------------------------------------------------------
# 主装配
# ---------------------------------------------------------------------------

def _copy_bytes(src, dst):
    """字节复制 + 复读校验；返回 sha256。源永不改写。"""
    data = src.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    dst.write_bytes(data)
    back = _sha256_file(dst)
    if back != digest:
        raise SelfCheckError(f"复制校验失败: {dst}（写后 {back[:12]}… != 源 {digest[:12]}…）")
    return digest


def _blas_info():
    try:
        cfg = np.show_config(mode="dicts")
        blas = cfg.get("Build Dependencies", {}).get("blas", {})
        name, ver = blas.get("name"), blas.get("version")
        return f"{name} {ver}" if name else "unknown"
    except Exception:
        return "unknown"


def build(staging_dirs, out_dir, op, container_id, canonical_path):
    """S1/S2 逐字节复用装包（产出已交付六 v1 包）。S4 起不再被路由（provenance 保留）：
    非批量装包一律走 build_purescript（docstring「S4 六算子 v2」段）。"""
    cases_src = find_cases_dir(staging_dirs)
    baseline_src = find_baseline(staging_dirs, op)
    criteria_dir = find_criteria_dir(staging_dirs)

    with open(cases_src / "index.json", encoding="utf-8") as fh:
        index = json.load(fh)
    all_cases = index.get("cases")
    if not isinstance(all_cases, list):
        raise ContractError(f"{cases_src / 'index.json'} 无 cases 列表")
    op_cases = [c for c in all_cases if c.get("op") == op]
    if not op_cases:
        raise ContractError(f"源 index 中没有 {op} 的 case")

    checklist = {"operator": op, "tool": {"name": TOOL, "ver": TOOL_VER},
                 "sources": {"cases": str(cases_src), "baseline": str(baseline_src),
                             "criteria": str(criteria_dir),
                             "index_sha256": _sha256_file(cases_src / "index.json")},
                 "items": []}

    def tick(name, detail):
        checklist["items"].append({"check": name, "pass": True, "detail": detail})
        print(f"[build_package] ✓ {name}: {detail}")

    (out_dir / "cases").mkdir(parents=True, exist_ok=True)

    # 1) npz 逐字节复用 + 2) 校验字段 + 4) index/实物对账
    npz_sha = {}
    for entry in op_cases:
        cid, npz_rel = entry["case_id"], entry.get("npz")
        if npz_rel != f"cases/{cid}.npz":
            raise ContractError(f"{cid}: index.npz={npz_rel!r} 不是规范包内路径 cases/{cid}.npz")
        src = find_npz(staging_dirs, cases_src, f"{cid}.npz")
        if src is None:
            raise ContractError(f"{cid}: B 冻结 npz 缺失（不重生成，fail-closed）")
        if entry.get("ratio_cpu") is None and entry.get("ratio_cpu_status") != "prep_failed":
            raise ContractError(
                f"{cid}: index 的 ratio_cpu 为空且非 prep_failed——"
                "staging 命中了未回填的骨架 index，请把 B2 回填件加入 --staging")
        npz_sha[npz_rel] = _copy_bytes(src, out_dir / "cases" / f"{cid}.npz")
        with np.load(out_dir / npz_rel) as z:
            arrays = {k: z[k] for k in z.files}
        check_entry_vs_npz(entry, arrays)
        check_cast_fields(arrays, cid)
    tick("逐字节复用/npz", f"{len(op_cases)} 个 npz 复制后 sha256 与源相等")
    tick("校验字段", f"{len(op_cases)} 个 case 的 A32/golden32（含 B32）降型逐字节成立")
    tick("index 对账", f"{len(op_cases)} 条 index.arrays 与 npz 实物一致、npz 路径规范")
    n_pf = sum(1 for c in op_cases if c.get("ratio_cpu_status") == "prep_failed")
    tick("ratio_cpu 非空", f"{len(op_cases) - n_pf} 条已回填，prep_failed {n_pf} 条按状态入包")

    # 3) 抽验 3 case：golden 独立重算，只比对不覆盖
    picked = spot_check_indices(len(op_cases))
    spot_detail = []
    for i in picked:
        entry = op_cases[i]
        cid, uplo = entry["case_id"], entry["uplo"]
        with np.load(out_dir / entry["npz"]) as z:
            arrays = {k: z[k] for k in z.files}
        g64 = recompute_golden(op, arrays, uplo, cid)
        if g64.tobytes() != arrays["golden64"].tobytes():
            diff = float(np.max(np.abs(g64 - arrays["golden64"])))
            raise SelfCheckError(f"{cid}: golden64 独立重算与冻结值不一致（max|Δ|={diff:g}）")
        g32 = g64.astype(_dtype32_of("golden64", g64, cid))
        if g32.tobytes() != arrays["golden32"].tobytes():
            raise SelfCheckError(f"{cid}: golden32 独立重算降型与冻结值不一致")
        spot_detail.append(cid)
    chain = "z" if _is_complex_op(op) else "d"
    tick("抽验 golden", f"{len(picked)} case（首/中/尾：{'、'.join(spot_detail)}）"
                        f"{chain} 链路独立重算 golden64/golden32 逐字节一致；只比对未写回")
    checklist["spot_check_cases"] = spot_detail

    # 包内 index：顶层元数据保留、条目原样，只做算子子集选取
    op_index = {k: v for k, v in index.items() if k != "cases"}
    op_index["cases"] = op_cases
    with open(out_dir / "cases" / "index.json", "w", encoding="utf-8") as fh:
        json.dump(op_index, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tick("包内 index", f"{op} 条目 {len(op_cases)} 条逐字段原样入包（源 index 未改写）")

    # perf_baseline 逐字节复用
    base_sha = _copy_bytes(baseline_src, out_dir / "perf_baseline.json")
    tick("逐字节复用/perf_baseline", f"sha256 与源相等（{base_sha[:12]}…）")

    # verify 双件：D 卡确定性渲染入口
    sys.path.insert(0, str(criteria_dir))
    try:
        import render_verify
        import thresholds
        rendered = render_verify.render(op)
        for fname, text in sorted(rendered.items()):
            (out_dir / fname).write_bytes(text.encode("utf-8"))
        renderer_ver = render_verify.RENDERER_VER
        criteria_ver = thresholds.CRITERIA_VER
    finally:
        sys.path.remove(str(criteria_dir))
    tick("verify 渲染", f"verify_accuracy.py / verify_perf.py（criteria {criteria_ver} / "
                        f"renderer {renderer_ver}）经确定性入口渲染入包")

    # 自测配套件：gen_data 副本、canonical 算子切片、sim 副本、README（S2 spec §1）
    here = Path(__file__).resolve().parent
    gen_sha = _copy_bytes(here / "gen_data_cholesky.py", out_dir / "gen_data.py")
    sim_src = criteria_dir.parent / "scripts" / "sim_dut.py"
    if not sim_src.is_file():
        raise ContractError(f"sim_dut.py 未找到: {sim_src}")
    sim_sha = _copy_bytes(sim_src, out_dir / "sim_dut.py")
    with open(canonical_path, encoding="utf-8") as fh:
        canon = json.load(fh)
    slice_doc = {k: v for k, v in canon.items() if k != "cases"}
    slice_doc["cases"] = [c for c in canon["cases"] if c.get("op") == op]
    if not slice_doc["cases"]:
        raise ContractError(f"canonical 中没有 {op} 的 case")
    with open(out_dir / "canonical_cases.json", "w", encoding="utf-8") as fh:
        json.dump(slice_doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    tpl = here.parent / "assets" / "package-readme-template.md"
    (out_dir / "README.md").write_text(
        tpl.read_text(encoding="utf-8").replace("{op}", op), encoding="utf-8")
    tick("自测配套件", f"gen_data.py（{gen_sha[:12]}…）、sim_dut.py（{sim_sha[:12]}…）、"
                       f"canonical 切片 {len(slice_doc['cases'])} 条、README.md 渲染入包")

    # 5) 指纹 + manifest（spec §2.2）
    fingerprint = {}
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and path.name != "manifest.json" and "__pycache__" not in path.parts:
            fingerprint[path.relative_to(out_dir).as_posix()] = _sha256_file(path)
    try:
        import scipy
        scipy_ver = scipy.__version__
    except ImportError:
        scipy_ver = None
    manifest = {
        "package_ver": PACKAGE_VER,
        "operator": op,
        "interfaces": [op],
        "criteria_ver": criteria_ver,
        "renderer_ver": renderer_ver,
        "standard_refs": STANDARD_REFS,
        "env": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy_ver,
            "blas": _blas_info(),
            "容器标识": container_id,
        },
        "fingerprint": fingerprint,
        "build": {
            "tool": TOOL, "ver": TOOL_VER,
            "spec": "dev-doc/solver/solver-s1-cholesky-spec.md#2.2",
            "reuse": "npz 与 perf_baseline.json 为 B/C 冻结产物逐字节复用；"
                     "index 为 B2 回填件按算子过滤；verify 双件为 D 渲染器确定性输出",
            "source_index_sha256": checklist["sources"]["index_sha256"],
        },
    }
    with open(out_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tick("指纹", f"manifest.fingerprint 覆盖包内 {len(fingerprint)} 个文件（除 manifest.json）")

    checklist["fingerprint_count"] = len(fingerprint)
    checklist["all_passed"] = True
    return checklist


# ---------------------------------------------------------------------------
# ratio_cpu_mean 固化（HT-4，A2：发布前算好写入 index，判定时只读、零重算）
# ---------------------------------------------------------------------------

def purescript_ratio_cpu_mean(gen_cases):
    """HT-4 非批量口径：单算子切片内全部正定精度用例 ratio_cpu 的算术平均
    （info 契约用例不计入；prep_failed 在装包自检回填循环已 fail-closed 拒绝，
    到这里的精度条目必全 ok）。单算子切片内取均值，勿跨算子。"""
    vals = [float(e["ratio_cpu"]) for e in gen_cases
            if e.get("case_purpose") != "info"]
    if not vals:
        raise SelfCheckError("ratio_cpu_mean 不可算：切片内没有精度用例")
    return sum(vals) / len(vals)


def batched_case_ratio_cpu_mean(entry):
    """HT-4 批量口径：case 条目的加权均值 Σ(count_j·ratio_j)/batchSize——A0 抽样
    只对代表内容算 CPU ratio，按各内容槽位数加权后与任务书「batch 个矩阵均值」
    数学等价（sample_map 全覆盖保证 Σcount_j == batchSize）。info 契约条目不适用
    （调用方跳过）；含 prep_failed 内容时 mean 不可算，fail-closed。"""
    sm = entry.get("sample_map") or []
    ratios = entry.get("ratio_cpu") or []
    batch = int(entry["batch"])
    counts = [len(m["slots"]) for m in sm]
    if sum(counts) != batch or len(ratios) != len(counts):
        raise SelfCheckError(
            f"{entry.get('case_id')}: 槽位数 Σ{counts} != batch {batch} 或 ratio "
            f"{len(ratios)} 值 != 内容数 {len(counts)}——mean 加权口径对不上")
    if any(v is None for v in ratios):
        raise SelfCheckError(
            f"{entry.get('case_id')}: 含 prep_failed 内容，case 级 mean 不可算"
            "（fail-closed；cu 构造性正定输入下不应发生）")
    return sum(c * float(r) for c, r in zip(counts, ratios)) / batch


# ---------------------------------------------------------------------------
# S3 批量四包：纯脚本装包（S3 spec §5；docstring「S3 批量四包」段）
# ---------------------------------------------------------------------------

def build_batched(staging_dirs, out_dir, op, container_id, canonical_path,
                  scope="full"):
    """batched 纯脚本装包（A0 抽样，HT-2）：零数据数组，index 全条目
    materialize:"gen"，条目（含 sample_map 槽位映射）与逐内容 ratio 参考来自装包
    自检的现场生成运行（自检数据随后删除）。scope：full=该算子 dedup 全量切片
    （2026-09-27 全量精度用例决策），s1=二维代表子集六例（v3 前行为）。"""
    import shutil

    criteria_dir = find_criteria_dir(staging_dirs)
    baseline_src = find_baseline(staging_dirs, op)
    here = Path(__file__).resolve().parent

    checklist = {"operator": op, "tool": {"name": TOOL, "ver": TOOL_VER},
                 "package_form": "pure-script（S3 spec §5：零数据数组）",
                 "sources": {"canonical": str(canonical_path),
                             "baseline": str(baseline_src),
                             "criteria": str(criteria_dir)},
                 "items": []}

    def tick(name, detail):
        checklist["items"].append({"check": name, "pass": True, "detail": detail})
        print(f"[build_package] ✓ {name}: {detail}")

    out_dir.mkdir(parents=True, exist_ok=True)

    # canonical 算子切片按 scope 二选一（--scope：full=dedup 全量 / s1=二维代表子集）
    with open(canonical_path, encoding="utf-8") as fh:
        canon = json.load(fh)
    op_cases_all = [c for c in canon["cases"] if c.get("op") == op]
    if not op_cases_all:
        raise ContractError(f"canonical 中没有 {op} 的 case")
    if scope == "full":
        # 全量精度用例决策（2026-09-27）：A0 抽样零数据，全量入包不物化数组，
        # n×batch 大 case 也只算 k=min(5,batch) 个代表内容
        subset_cases = list(op_cases_all)
        n_subset = len(subset_cases)
    else:
        subset_cases = [c for c in op_cases_all if c.get("s1_subset") is True]
        n_subset = len(subset_cases)
        if n_subset != 6:
            raise ContractError(
                f"{op}: canonical 的二维代表子集应为 6 例（S3 spec §1），实为 {n_subset}")
    slice_doc = {k: v for k, v in canon.items() if k != "cases"}
    slice_doc["package_scope"] = scope  # gen_data --select 消费的唯一事实源
    if scope == "full":
        slice_doc["slice_note"] = (
            f"本切片含 {op} 的 dedup 全量 {n_subset} 例（2026-09-27 全量精度用例决策："
            f"发包精度用例=冻结册全量；A0 抽样零数据，判定侧现场重造）；对账"
            "（刷新后 154→151）见冻结产物 reports/solver-s3/canonical_batched.json")
    else:
        slice_doc["slice_note"] = (
            f"本切片只含 {op} 的二维代表子集 {n_subset} 例（S3 spec §5：README 第 0 步 "
            f"--select all 即物化这{n_subset}例）；该算子 dedup 全量 {len(op_cases_all)} 条与 "
            "冻结对账见冻结产物 reports/solver-s3/canonical_batched.json")
    slice_doc["cases"] = subset_cases
    with open(out_dir / "canonical_cases.json", "w", encoding="utf-8") as fh:
        json.dump(slice_doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    tick("canonical 切片", f"scope={scope}：{n_subset} 例入包"
                           f"（dedup 全量 {len(op_cases_all)} 条，"
                           "留冻结产物对账）")

    # 装包自检：现场生成子集（gen_data 分块/并行通路，golden+逐矩阵 ratio 一并算出）
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import gen_data_cholesky as gd
    tmp = out_dir / ".selfcheck-gen"
    if tmp.exists():
        shutil.rmtree(tmp)
    gen_argv = ["--canonical", str(out_dir / "canonical_cases.json"),
                "--out", str(tmp), "--select", "all", "--ops", op,
                "--criteria", str(criteria_dir)]
    try:
        rc = gd.main(gen_argv)
    except gd.ContractError as exc:
        raise ContractError(f"装包自检的现场生成失败：{exc}") from exc
    if rc != 0:
        raise SelfCheckError(f"装包自检的现场生成退出码 {rc}（应 0）")
    with open(tmp / "cases" / "index.json", encoding="utf-8") as fh:
        gen_index = json.load(fh)
    gen_cases = gen_index.get("cases") or []
    # HT-9：gen 侧对批量算子另派生 1 个 info 契约混合 case（与精度用例一并生成；
    # 派生基座与 gen 侧同口径——full=全量精度 case，s1=子集）
    n_info = len([e for e in gd.derive_batched_info_cases(subset_cases,
                                                          require_s1=(scope != "full"))
                  if e["op"] == op])
    if len(gen_cases) != n_subset + n_info:
        raise SelfCheckError(
            f"现场生成 {len(gen_cases)} case != 子集 {n_subset} + info 契约 {n_info}")
    for entry in gen_cases:
        if entry.get("case_purpose") == "info":
            continue
        ratio = entry.get("ratio_cpu")
        if (entry.get("ratio_cpu_status") != "ok" or not ratio
                or any(v is None for v in ratio)):
            raise SelfCheckError(
                f"{entry.get('case_id')}: 现场生成 ratio 状态 {entry.get('ratio_cpu_status')!r}"
                f"（cu 对角占优构造下应全 ok 且无 prep_failed——环境或数据有问题）")
    a0 = {e["case_id"]: {"contents": e.get("sampled_contents"),
                         "rep_slots": [m["rep_slot"] for m in e.get("sample_map") or []]}
          for e in gen_cases if e.get("case_purpose") != "info"}
    info_ids = [e["case_id"] for e in gen_cases if e.get("case_purpose") == "info"]
    # HT-4：精度条目固化 case 级 ratio_cpu_mean（Σ(count_j·ratio_j)/batchSize 槽位
    # 加权；info 契约条目不适用不计入）——判定侧只读零重算（batched_a0 经
    # case_arrays 消费，阈值第二支），mean 只随用例集版本重算
    n_mean = 0
    for entry in gen_cases:
        if entry.get("case_purpose") == "info":
            continue
        entry["ratio_cpu_mean"] = batched_case_ratio_cpu_mean(entry)
        n_mean += 1
    tick("装包自检/现场生成",
         f"{len(gen_cases)} case A0 抽样生成（不落数组）+golden+逐内容 ratio 完成"
         f"（精度 {n_subset} + info 契约 {n_info}: {'、'.join(info_ids)}，HT-9）；"
         + "；".join(f"{cid}:k={m['contents']}/rep={m['rep_slots']}"
                     for cid, m in a0.items()))
    tick("ratio 参考", "逐内容 ratio k 值列表写入包内 index 仅作参考"
                       "（S3 spec §3：判定侧现场重算，不消费包内参考；info 契约"
                       "条目不计入，HT-8/9）；精度条目另固化 case 级 "
                       f"ratio_cpu_mean（槽位加权，{n_mean} 例，HT-4）")

    # 包内 index：gen 产 index 原样 + 逐条 materialize:"gen"（零数据，S3 spec §5 + HT-2 A0）
    for entry in gen_cases:
        entry["materialize"] = "gen"
    gen_index["materialize_note"] = (
        "纯脚本包（S3 spec §5 + HT-2 A0 抽样）：包不携带数据数组；批量 case 固化 "
        "sample_map 槽位映射与逐内容 ratio_cpu k 值列表（仅作参考，判定侧现场重算），"
        "数组由执行器/DUT 挂钩按 gen_data 的 expand_sampled_rows 现场构造（流式喂入）。"
        "精度条目另固化 case 级 ratio_cpu_mean（HT-4：Σ(count_j·ratio_j)/batchSize "
        "槽位加权，判定时只读、零重算，potrf/potrs 阈值第二支消费）。"
        "case_purpose==\"info\" 的条目为批量 info 契约混合用例（HT-9）：只比 info，"
        "无 golden/ratio，不进精度均值——*potrfBatched 族 k_expected 为逐内容 k 值列表"
        "（LAPACK 约定：非正定内容记首个非正定主子式阶 k，正定记 0），"
        "经 sample_map 展开即全批期望 infoArray；"
        "*potrsBatched 族 k_expected=-1（标量 info 仅报参数错）")
    (out_dir / "cases").mkdir(exist_ok=True)
    with open(out_dir / "cases" / "index.json", "w", encoding="utf-8") as fh:
        json.dump(gen_index, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    shutil.rmtree(tmp)
    tick("包内 index", f"{len(gen_cases)} 条全部 materialize:\"gen\"；自检生成数据已删除")

    # perf_baseline 逐字节复用（C 卡产物）
    base_sha = _copy_bytes(baseline_src, out_dir / "perf_baseline.json")
    tick("逐字节复用/perf_baseline", f"sha256 与源相等（{base_sha[:12]}…）")

    # verify 双件：确定性渲染入口（batched 卡的 _OP_SPECS 由 accept 侧提供）
    sys.path.insert(0, str(criteria_dir))
    try:
        import render_verify
        import thresholds
        rendered = render_verify.render(op)
        for fname, text in sorted(rendered.items()):
            (out_dir / fname).write_bytes(text.encode("utf-8"))
        renderer_ver = render_verify.renderer_ver_for(op)  # 批量渲染走 BATCHED_RENDERER_VER，与副本内戳记一致
        criteria_ver = thresholds.CRITERIA_VER
    finally:
        sys.path.remove(str(criteria_dir))
    tick("verify 渲染", f"verify_accuracy.py / verify_perf.py（criteria {criteria_ver} / "
                        f"renderer {renderer_ver}）经确定性入口渲染入包")

    # 自测配套件：gen_data 副本、sim 副本、batched README（缺模板即停，docstring S3 段）
    gen_sha = _copy_bytes(here / "gen_data_cholesky.py", out_dir / "gen_data.py")
    sim_src = criteria_dir.parent / "scripts" / "sim_dut.py"
    if not sim_src.is_file():
        raise ContractError(f"sim_dut.py 未找到: {sim_src}")
    sim_sha = _copy_bytes(sim_src, out_dir / "sim_dut.py")
    tpl = here.parent / "assets" / "package-readme-template-batched.md"
    if not tpl.is_file():
        raise ContractError(
            f"batched README 模板缺失: {tpl}（纯脚本包不得用通用模板，fail-closed）")
    (out_dir / "README.md").write_text(
        tpl.read_text(encoding="utf-8")
        .replace("{op}", op).replace("{n_info}", str(n_info))
        .replace("{n_cases}", str(n_subset))
        .replace("{gram_form}", "B·Bᴴ" if op.lower().startswith("c") else "B·Bᵀ"),
        encoding="utf-8")
    tick("自测配套件", f"gen_data.py（{gen_sha[:12]}…）、sim_dut.py（{sim_sha[:12]}…）、"
                       f"README.md（batched 模板，info 契约 {n_info} 例）入包")

    # 零数据断言（S3 spec §5：无 npz、无 golden、无 ratio 数组——npz 是唯一数组载体）
    stray = [str(x.relative_to(out_dir)) for x in out_dir.rglob("*.npz")]
    if stray:
        raise SelfCheckError(f"纯脚本包内出现数据文件：{stray}")
    tick("零数据", "包内无 *.npz（golden/ratio 数组随 npz 一并为零，rglob 断言）")

    # 指纹 + manifest
    fingerprint = {}
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and path.name != "manifest.json" and "__pycache__" not in path.parts:
            fingerprint[path.relative_to(out_dir).as_posix()] = _sha256_file(path)
    try:
        import scipy
        scipy_ver = scipy.__version__
    except ImportError:
        scipy_ver = None
    manifest = {
        "package_ver": PACKAGE_VER_BATCHED,
        "operator": op,
        "interfaces": [op],
        "criteria_ver": criteria_ver,
        "renderer_ver": renderer_ver,
        "standard_refs": STANDARD_REFS,
        "env": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy_ver,
            "blas": _blas_info(),
            "容器标识": container_id,
        },
        "fingerprint": fingerprint,
        "build": {
            "tool": TOOL, "ver": TOOL_VER,
            "spec": "dev-doc/solver/solver-s3-batched-spec.md#5",
            "materialize": "gen（A0 抽样，HT-2）",
            "reuse": "纯脚本包（A0 抽样）：不携带数据数组；sample_map 与逐内容 ratio "
                     "参考固化在 index（仅作参考）；perf_baseline.json 为 C 冻结产物"
                     "逐字节复用；verify 双件为 D 渲染器确定性输出",
        },
    }
    with open(out_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tick("指纹", f"manifest.fingerprint 覆盖包内 {len(fingerprint)} 个文件（除 manifest.json）")

    checklist["fingerprint_count"] = len(fingerprint)
    checklist["gen_selfcheck"] = a0
    checklist["all_passed"] = True
    return checklist


# ---------------------------------------------------------------------------
# S4 六算子 v2：单矩阵算子的纯脚本装包（docstring「S4 六算子 v2」段）
# ---------------------------------------------------------------------------

def build_purescript(staging_dirs, out_dir, op, container_id, canonical_path,
                     scope="full"):
    """单矩阵算子纯脚本装包（S4 v2）：零数据数组，index 全条目 materialize:"gen"，
    条目与 ratio 参考值来自装包自检的现场生成+回填运行（生成数据随后删除）。
    scope：full=该算子全部精度用例切片（2026-09-27 全量精度用例决策），
    s1=代表子集（v3 前行为）。"""
    import shutil

    criteria_dir = find_criteria_dir(staging_dirs)
    baseline_src = find_baseline(staging_dirs, op)
    here = Path(__file__).resolve().parent
    canonical_path = Path(canonical_path)

    checklist = {"operator": op, "tool": {"name": TOOL, "ver": TOOL_VER},
                 "package_form": "pure-script（S4 v2：零数据数组，"
                                 "Mr.0 2026-09-24 裁定与 batched 统一形态）",
                 "sources": {"canonical": str(canonical_path),
                             "baseline": str(baseline_src),
                             "criteria": str(criteria_dir)},
                 "items": []}

    def tick(name, detail):
        checklist["items"].append({"check": name, "pass": True, "detail": detail})
        print(f"[build_package] ✓ {name}: {detail}")

    out_dir.mkdir(parents=True, exist_ok=True)

    # canonical 算子切片按 scope 二选一（--scope：full=全部精度用例 / s1=代表子集，
    # spec §2.1 的子集口径为 cu 主数据 ≥6 + std 补充 ≥2）
    with open(canonical_path, encoding="utf-8") as fh:
        canon = json.load(fh)
    op_cases_all = [c for c in canon["cases"] if c.get("op") == op]
    if not op_cases_all:
        raise ContractError(f"canonical 中没有 {op} 的 case")
    if scope == "full":
        # 全量精度用例决策（2026-09-27）：全部 cu 精度用例 + std 补充（9001/9002）
        subset_cases = list(op_cases_all)
        n_subset = len(subset_cases)
    else:
        subset_cases = [c for c in op_cases_all if c.get("s1_subset") is True]
        n_subset = len(subset_cases)
        if n_subset < 8:
            raise ContractError(
                f"{op}: canonical 的 s1 子集应 ≥8 例（spec §2.1：cu ≥6 + std ≥2），"
                f"实为 {n_subset}")
    slice_doc = {k: v for k, v in canon.items() if k != "cases"}
    slice_doc["package_scope"] = scope  # gen_data --select 消费的唯一事实源
    if scope == "full":
        slice_doc["slice_note"] = (
            f"本切片含 {op} 的全部精度用例 {n_subset} 例（2026-09-27 全量精度用例决策："
            f"发包精度用例=冻结册全量，含 nrhs 多右端组合）；README 第 0 步 --select all "
            f"即物化这{n_subset}例；源册见冻结件 {canonical_path.name}")
    else:
        slice_doc["slice_note"] = (
            f"本切片只含 {op} 的 s1 子集 {n_subset} 例（纯脚本包 README 第 0 步 "
            f"--select all 即物化这{n_subset}例）；该算子 canonical 全量 "
            f"{len(op_cases_all)} 条见冻结件 {canonical_path.name}，不入包")
    slice_doc["cases"] = subset_cases
    with open(out_dir / "canonical_cases.json", "w", encoding="utf-8") as fh:
        json.dump(slice_doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    tick("canonical 切片", f"scope={scope}：{n_subset} 例入包"
                           f"（canonical 全量 {len(op_cases_all)} 条，留冻结件对账）")

    # 装包自检①：现场生成切片全量（--select all 恰为切片，验证 README 第 0 步自洽）
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import gen_data_cholesky as gd
    tmp = out_dir / ".selfcheck-gen"
    if tmp.exists():
        shutil.rmtree(tmp)
    gen_argv = ["--canonical", str(out_dir / "canonical_cases.json"),
                "--out", str(tmp), "--select", "all", "--ops", op]
    try:
        rc = gd.main(gen_argv)
    except gd.ContractError as exc:
        raise ContractError(f"装包自检的现场生成失败：{exc}") from exc
    if rc != 0:
        raise SelfCheckError(f"装包自检的现场生成退出码 {rc}（应 0）")
    with open(tmp / "cases" / "index.json", encoding="utf-8") as fh:
        gen_index = json.load(fh)
    gen_cases = gen_index.get("cases") or []
    # HT-8：gen 侧另派生 info 契约用例（与精度用例一并生成；派生基座与 gen 侧
    # 同口径——full=切片全量精度 case，s1=子集）
    n_info = len([e for e in gd.derive_info_cases(subset_cases,
                                                  require_s1=(scope != "full"))
                  if e["op"] == op])
    if len(gen_cases) != n_subset + n_info:
        raise SelfCheckError(
            f"现场生成 {len(gen_cases)} case != 切片 {n_subset} + info 变体 {n_info}"
            "（--select all 应恰物化包内切片及其 info 派生）")
    tick("装包自检/现场生成",
         f"{len(gen_cases)} case 生成完成（精度 {n_subset} + info 契约 {n_info}；"
         "--select all == 包内切片）")

    # 装包自检②：校验字段 + index/实物对账 + golden 首/中/尾抽验（复用 v1 自检件；
    # info 契约用例无 golden——cast 校验按 case_purpose 分流，golden 抽验只在精度用例上做）
    for entry in gen_cases:
        with np.load(tmp / entry["npz"]) as z:
            arrays = {k: z[k] for k in z.files}
        check_entry_vs_npz(entry, arrays)
        check_cast_fields(arrays, entry["case_id"], entry.get("case_purpose"))
    tick("校验字段", f"{len(gen_cases)} 个 case 的 A32/golden32（含 B32）降型逐字节成立"
                     f"（info 契约 {n_info} 例无 golden，豁免该对）；index.arrays 与实物一致")
    acc_indices = [i for i, e in enumerate(gen_cases) if e.get("case_purpose") != "info"]
    picked = [acc_indices[i] for i in spot_check_indices(len(acc_indices))]
    spot_detail = []
    for i in picked:
        entry = gen_cases[i]
        cid = entry["case_id"]
        with np.load(tmp / entry["npz"]) as z:
            arrays = {k: z[k] for k in z.files}
        g64 = recompute_golden(op, arrays, entry["uplo"], cid)
        if g64.tobytes() != arrays["golden64"].tobytes():
            diff = float(np.max(np.abs(g64 - arrays["golden64"])))
            raise SelfCheckError(f"{cid}: golden64 独立重算与现场生成不一致（max|Δ|={diff:g}）")
        g32 = g64.astype(_dtype32_of("golden64", g64, cid))
        if g32.tobytes() != arrays["golden32"].tobytes():
            raise SelfCheckError(f"{cid}: golden32 独立重算降型与现场生成不一致")
        spot_detail.append(cid)
    chain = "z" if _is_complex_op(op) else "d"
    tick("抽验 golden", f"{len(picked)} case（首/中/尾：{'、'.join(spot_detail)}）"
                        f"{chain} 链路独立重算 golden64/golden32 逐字节一致；只比对未写回")
    checklist["spot_check_cases"] = spot_detail

    # 装包自检③：ratio 参考值回填（残差唯一实现在 criteria/verdict，
    # 经 fill_ratio_cpu.run_chain 同一入口；构造性 SPD/HPD 下应全 ok。
    # HT-8：info 契约用例无残差可算——跳过（info==k_expected 的构造性自检已在
    # 生成时当场验证，不成立即 ContractError）
    import fill_ratio_cpu as frc
    verdict_mod = frc.load_verdict(criteria_dir)
    lapack = frc._lapack()
    ratio_ref = {}
    for entry in gen_cases:
        if entry.get("case_purpose") == "info":
            continue
        cid = entry["case_id"]
        with np.load(tmp / entry["npz"]) as z:
            arrays = {k: z[k] for k in z.files}
        try:
            r = frc.run_chain(verdict_mod, lapack, entry, arrays)
        except frc.PrepFailed as exc:
            raise SelfCheckError(
                f"{cid}: 装包自检 ratio 准备链失败（{exc}）——"
                "构造性 SPD/HPD 输入下应全 ok，环境或数据有问题")
        entry["ratio_cpu"] = float(r)
        entry["ratio_cpu_status"] = "ok"
        ratio_ref[cid] = float(r)
    tick("ratio 参考值", f"{len(ratio_ref)} 精度 case 低精度准备链回填完成，全 ok"
                         f"（info 契约 {n_info} 例不计入，HT-8；仅作参考：判定侧现场"
                         "同法重算，不消费包内数值）")
    # HT-4：算子级 ratio_cpu_mean 固化（该算子全部正定精度用例的算术平均，info
    # 契约条目不计入）——index 顶层 {op: mean} map，accept_run/verify 副本按算子
    # 注入 case_arrays 消费（potrf/potrs 阈值第二支）；只读零重算，随用例集版本重算
    op_mean = purescript_ratio_cpu_mean(gen_cases)
    gen_index["ratio_cpu_mean"] = {op: op_mean}
    tick("ratio_cpu_mean", f"算子级均值 {op_mean:.6g} 固化入 index 顶层"
                           f"（{len(ratio_ref)} 精度 case 算术平均，HT-4）")

    # 包内 index：gen 产 index 原样 + 逐条 materialize:"gen"（零数据）
    for entry in gen_cases:
        entry["materialize"] = "gen"
    gen_index["materialize_note"] = (
        "纯脚本包（v2）：包不携带数据数组；npz/arrays 字段描述 gen_data 现场生成后"
        "的产物（README 第 0 步，--out data 时落 data/cases/）；ratio_cpu 为装包自检"
        "运行的参考值，仅作参考（判定侧现场同法重算）。index 顶层 ratio_cpu_mean 为"
        "算子级固化均值（HT-4：全部正定精度用例的算术平均，判定时只读、零重算）。"
        "case_purpose==\"info\" 的条目"
        "为 info 契约用例（HT-8）：只比被测 info==k_expected，无 golden/ratio，"
        "不进精度均值")
    (out_dir / "cases").mkdir(exist_ok=True)
    with open(out_dir / "cases" / "index.json", "w", encoding="utf-8") as fh:
        json.dump(gen_index, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    shutil.rmtree(tmp)
    tick("包内 index", f"{len(gen_cases)} 条全部 materialize:\"gen\"；自检生成数据已删除")

    # perf_baseline 逐字节复用（C 卡产物）
    base_sha = _copy_bytes(baseline_src, out_dir / "perf_baseline.json")
    tick("逐字节复用/perf_baseline", f"sha256 与源相等（{base_sha[:12]}…）")

    # verify 双件：确定性渲染入口（单矩阵算子的纯脚本形态渲染，s4-D5）
    sys.path.insert(0, str(criteria_dir))
    try:
        import render_verify
        import thresholds
        rendered = render_verify.render(op)
        for fname, text in sorted(rendered.items()):
            (out_dir / fname).write_bytes(text.encode("utf-8"))
        renderer_ver = render_verify.renderer_ver_for(op)
        criteria_ver = thresholds.CRITERIA_VER
    finally:
        sys.path.remove(str(criteria_dir))
    tick("verify 渲染", f"verify_accuracy.py / verify_perf.py（criteria {criteria_ver} / "
                        f"renderer {renderer_ver}）经确定性入口渲染入包")

    # 自测配套件：gen_data 副本、sim 副本、纯脚本 README（缺模板即停）
    gen_sha = _copy_bytes(here / "gen_data_cholesky.py", out_dir / "gen_data.py")
    sim_src = criteria_dir.parent / "scripts" / "sim_dut.py"
    if not sim_src.is_file():
        raise ContractError(f"sim_dut.py 未找到: {sim_src}")
    sim_sha = _copy_bytes(sim_src, out_dir / "sim_dut.py")
    tpl = here.parent / "assets" / "package-readme-template-purescript.md"
    if not tpl.is_file():
        raise ContractError(
            f"纯脚本 README 模板缺失: {tpl}（纯脚本包不得用逐字节复用模板，fail-closed）")
    (out_dir / "README.md").write_text(
        tpl.read_text(encoding="utf-8").replace("{op}", op)
                                       .replace("{n_cases}", str(n_subset))
                                       .replace("{n_info}", str(n_info)),
        encoding="utf-8")
    tick("自测配套件", f"gen_data.py（{gen_sha[:12]}…）、sim_dut.py（{sim_sha[:12]}…）、"
                       f"README.md（纯脚本模板）入包")

    # 零数据断言（无 npz、无 golden、无 ratio 数组——npz 是唯一数组载体）
    stray = [str(x.relative_to(out_dir)) for x in out_dir.rglob("*.npz")]
    if stray:
        raise SelfCheckError(f"纯脚本包内出现数据文件：{stray}")
    tick("零数据", "包内无 *.npz（golden/ratio 数组随 npz 一并为零，rglob 断言）")

    # 指纹 + manifest
    fingerprint = {}
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and path.name != "manifest.json" and "__pycache__" not in path.parts:
            fingerprint[path.relative_to(out_dir).as_posix()] = _sha256_file(path)
    try:
        import scipy
        scipy_ver = scipy.__version__
    except ImportError:
        scipy_ver = None
    manifest = {
        "package_ver": PACKAGE_VER_PURESCRIPT,
        "operator": op,
        "interfaces": [op],
        "criteria_ver": criteria_ver,
        "renderer_ver": renderer_ver,
        "standard_refs": STANDARD_REFS,
        "env": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy_ver,
            "blas": _blas_info(),
            "容器标识": container_id,
        },
        "fingerprint": fingerprint,
        "build": {
            "tool": TOOL, "ver": TOOL_VER,
            "spec": "dev-doc/solver/solver-s3-batched-spec.md#5",
            "materialize": "gen",
            "reuse": "纯脚本包（v2）：不携带数据数组；perf_baseline.json 为 C 冻结产物"
                     "逐字节复用；verify 双件为 D 渲染器确定性输出；index 与 ratio "
                     "参考值来自装包自检的现场生成+回填运行（仅作参考）；含 info 契约"
                     "用例（case_purpose==\"info\"，HT-8：只比被测 info==k_expected）",
        },
    }
    with open(out_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tick("指纹", f"manifest.fingerprint 覆盖包内 {len(fingerprint)} 个文件（除 manifest.json）")

    checklist["fingerprint_count"] = len(fingerprint)
    checklist["ratio_reference"] = ratio_ref
    checklist["all_passed"] = True
    return checklist


def main(argv=None):
    ap = argparse.ArgumentParser(description="S1 正式包装配（spec §2.5 CLI；F 卡）")
    ap.add_argument("--canonical", required=True,
                    help="canonical_cases.json 路径（算子切片写入包内）")
    ap.add_argument("--staging", required=True, nargs="+",
                    help="装配来源目录（可多个，按序发现；见模块文档）")
    ap.add_argument("--out", required=True,
                    help="包输出目录 reports/solver-packages/<op>/（末段即算子名）")
    ap.add_argument("--op", choices=ALL_OPS, help="算子名；缺省从 --out 末段推断")
    ap.add_argument("--scope", choices=("s1", "full"), default="full",
                    help="包内切片口径（2026-09-27 全量精度用例决策）："
                         "full=该算子全部精度用例（缺省）；s1=代表子集（v3 前行为）")
    ap.add_argument("--container-id",
                    default=os.environ.get("OPRUNWAY_CONTAINER_ID", "unknown"),
                    help="manifest.env.容器标识")
    ap.add_argument("--selfcheck", help="自检清单 JSON 输出路径（可选）")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    op = args.op or out_dir.resolve().name
    try:
        if op not in ALL_OPS:
            raise ContractError(f"--out 末段 {op!r} 不是算子名（{ALL_OPS}），请显式传 --op")
        staging_dirs = [Path(d) for d in args.staging]
        for d in staging_dirs:
            if not d.is_dir():
                raise ContractError(f"staging 目录不存在: {d}")
        if op in BATCHED_OPS:               # S3 纯脚本装包（spec §5 + HT-2 A0）
            checklist = build_batched(staging_dirs, out_dir, op, args.container_id,
                                      args.canonical, scope=args.scope)
        else:                               # S4 v2：单矩阵算子一律纯脚本装包
            checklist = build_purescript(staging_dirs, out_dir, op, args.container_id,
                                         args.canonical, scope=args.scope)
    except ContractError as exc:
        print(f"[build_package] 契约/输入错误: {exc}", file=sys.stderr)
        return 2
    except SelfCheckError as exc:
        print(f"[build_package] 自检不过（包不可信，未写 manifest）: {exc}", file=sys.stderr)
        return 3

    if args.selfcheck:
        sc = Path(args.selfcheck)
        sc.parent.mkdir(parents=True, exist_ok=True)
        with open(sc, "w", encoding="utf-8") as fh:
            json.dump(checklist, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    print(f"[build_package] {op}: 装包完成 → {out_dir}（自检清单全部打钩）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
