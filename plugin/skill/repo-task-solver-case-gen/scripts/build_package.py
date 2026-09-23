#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_package.py —— S1-Cholesky F 卡：正式包装配（复用冻结产物逐字节装包）。

契约：dev-doc/solver/solver-s1-cholesky-spec.md §2.2（包 schema 与 manifest）、
§2.5（CLI）、第 3 节 F 行与写入所有权（**F 复用 B 冻结产物逐字节装包，不重生成；
抽验只核对不覆盖**）。本脚本不含任何数据构造与判据实现：npz 与 perf_baseline
一律字节复制，golden 抽验用 spec §2.2 声明的 d 前缀链路独立重算后**只比对**。

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
2. 校验字段（spec §2.2）——全部 case ``A32 == A64.astype(f32)``（B、golden 同理）；
3. 抽验 3 case——确定性取包内首/中/尾三个 case，按 spec §2.2 的 d 前缀链路
   （spotrf=dpotrf；spotrs=dpotrf→dpotrs；spotri=dpotrf→dpotri 存储侧半三角另侧置 0）
   独立重算 golden64/golden32，与冻结值逐字节一致；**只比对，绝不写回**；
4. index 一致——包内 index 条目与源 index 该算子条目逐字段相等，npz/arrays 与实物对得上；
5. 指纹——manifest.fingerprint 覆盖包内除 manifest.json 外全部文件（含 verify 双件；
   分级消费见 accept_run：cases/ 与 perf_baseline 错配阻断，verify 漂移仅告警）。

CLI（spec §2.5 逐字；方括号内为本卡补充的可选项，缺省行为不需要它们）：

    build_package.py --staging <目录...> --out reports/solver-packages/<op>/
                     [--op <op>] [--container-id <id>] [--selfcheck <json>]

--out 末段目录名即算子名（spec §2.5 的 <op>），--op 可显式覆盖；--container-id
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
TOOL_VER = "s1-F1"
OPS = ("spotrf", "spotrs", "spotri")
PACKAGE_VER = "s1"
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

def find_cases_dir(staging_dirs):
    for d in staging_dirs:
        if (d / "cases" / "index.json").is_file():
            return d / "cases"
    raise ContractError(f"staging 目录中找不到 cases/index.json：{[str(d) for d in staging_dirs]}")


def find_baseline(staging_dirs, op):
    for d in staging_dirs:
        for cand in (d / op / "perf_baseline.json", d / "perf_baseline.json"):
            if cand.is_file():
                return cand
    raise ContractError(f"staging 目录中找不到 {op} 的 perf_baseline.json")


def find_criteria_dir(staging_dirs):
    for d in staging_dirs:
        cand = d / "repo-task-solver-accept" / "criteria"
        if (cand / "render_verify.py").is_file():
            return cand
    raise ContractError("staging 目录中找不到镜像树 repo-task-solver-accept/criteria/render_verify.py")


# ---------------------------------------------------------------------------
# golden 独立重算（spec §2.2 链路的重实现；只用于抽验比对，绝不落盘）
# ---------------------------------------------------------------------------

def _lapack():
    try:
        from scipy.linalg import lapack
    except ImportError as exc:
        raise ContractError(
            "缺 scipy：golden 抽验走 scipy.linalg.lapack 的 d 前缀例程（spec §1：容器内补装并记录版本）"
        ) from exc
    return lapack


def recompute_golden(op, arrays, uplo, case_id):
    """按 spec §2.2 链路独立重算 golden64（C 序）。info!=0 属数据问题，直接抛。"""
    lapack = _lapack()
    lower = uplo == "L"
    F, info = lapack.dpotrf(arrays["A64"], lower=lower, clean=1)
    if info != 0:
        raise SelfCheckError(f"{case_id}: 抽验 dpotrf info={info}（SPD 冻结输入下应为 0）")
    if op == "spotrf":
        g = F
    elif op == "spotrs":
        g, info = lapack.dpotrs(F, arrays["B64"], lower=lower)
        if info != 0:
            raise SelfCheckError(f"{case_id}: 抽验 dpotrs info={info}")
    else:  # spotri
        C, info = lapack.dpotri(F, lower=lower)
        if info != 0:
            raise SelfCheckError(f"{case_id}: 抽验 dpotri info={info}")
        g = np.tril(C) if lower else np.triu(C)
    return np.ascontiguousarray(g)


def spot_check_indices(n_cases):
    """确定性抽样：首/中/尾（case 数 <3 时取全部）。返回有序去重下标。"""
    return sorted({0, n_cases // 2, n_cases - 1})


# ---------------------------------------------------------------------------
# 自检项
# ---------------------------------------------------------------------------

def check_cast_fields(arrays, case_id):
    """spec §2.2 校验字段：32 位数组 == 64 位数组降型，逐字节。"""
    pairs = [("A32", "A64"), ("golden32", "golden64")]
    if "B64" in arrays:
        pairs.append(("B32", "B64"))
    for a32, a64 in pairs:
        if a32 not in arrays or a64 not in arrays:
            raise SelfCheckError(f"{case_id}: npz 缺数组 {a32}/{a64}（spec §2.2 schema）")
        got = arrays[a32]
        want = arrays[a64].astype(np.float32)
        if got.dtype != np.float32 or got.tobytes() != want.tobytes():
            raise SelfCheckError(f"{case_id}: {a32} != {a64}.astype(float32)（spec §2.2 校验字段）")


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


def build(staging_dirs, out_dir, op, container_id):
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
        src = cases_src / f"{cid}.npz"
        if not src.is_file():
            raise ContractError(f"{cid}: B 冻结 npz 缺失: {src}（不重生成，fail-closed）")
        npz_sha[npz_rel] = _copy_bytes(src, out_dir / "cases" / f"{cid}.npz")
        with np.load(out_dir / npz_rel) as z:
            arrays = {k: z[k] for k in z.files}
        check_entry_vs_npz(entry, arrays)
        check_cast_fields(arrays, cid)
    tick("逐字节复用/npz", f"{len(op_cases)} 个 npz 复制后 sha256 与源相等")
    tick("校验字段", f"{len(op_cases)} 个 case 的 A32/golden32（含 B32）降型逐字节成立")
    tick("index 对账", f"{len(op_cases)} 条 index.arrays 与 npz 实物一致、npz 路径规范")

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
        g32 = g64.astype(np.float32)
        if g32.tobytes() != arrays["golden32"].tobytes():
            raise SelfCheckError(f"{cid}: golden32 独立重算降型与冻结值不一致")
        spot_detail.append(cid)
    tick("抽验 golden", f"{len(picked)} case（首/中/尾：{'、'.join(spot_detail)}）"
                        "d 链路独立重算 golden64/golden32 逐字节一致；只比对未写回")
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="S1 正式包装配（spec §2.5 CLI；F 卡）")
    ap.add_argument("--staging", required=True, nargs="+",
                    help="装配来源目录（可多个，按序发现；见模块文档）")
    ap.add_argument("--out", required=True,
                    help="包输出目录 reports/solver-packages/<op>/（末段即算子名）")
    ap.add_argument("--op", choices=OPS, help="算子名；缺省从 --out 末段推断")
    ap.add_argument("--container-id",
                    default=os.environ.get("OPRUNWAY_CONTAINER_ID", "unknown"),
                    help="manifest.env.容器标识")
    ap.add_argument("--selfcheck", help="自检清单 JSON 输出路径（可选）")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    op = args.op or out_dir.resolve().name
    try:
        if op not in OPS:
            raise ContractError(f"--out 末段 {op!r} 不是算子名（{OPS}），请显式传 --op")
        staging_dirs = [Path(d) for d in args.staging]
        for d in staging_dirs:
            if not d.is_dir():
                raise ContractError(f"staging 目录不存在: {d}")
        checklist = build(staging_dirs, out_dir, op, args.container_id)
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
