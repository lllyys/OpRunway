#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_harness.py —— 验收共享 harness 的编排层：现场造输入 → 执行 → 判定 → 报告。

四段式 harness（2026-10-09d 裁定）的第四段。前三段是 `build_dut.py`（S1 构建）、
`harness_exec.cpp` 加 `exec_case.py`（S2 执行）、`evidence.py`（取证）。

一套工具两方用（开发者自测与验收者验收走同一条执行通路），但**判定只有一份实现**：
本层直接调 `criteria/` 的 `verdict` / `batched_a0` / `batched_parallel`，不复制判定
逻辑；输入也直接调 case-gen 的 `gen_data_cholesky`，不另建生成器。验收证据只认
验收者自己这次运行的产物。

## 输入来源

| 来源 | 参数 | 构造实现 |
| --- | --- | --- |
| Cholesky 任务包 | `--canonical` 加 `--gen-dir` | `gen_data_cholesky.build_case_arrays`、批量走 `build_batched_contents` 加 `expand_sampled_rows` |
| 通路演练 | `--probe-op` 加 `--n/--batch/--seed` | 同仓的 `fill_spd_cu` / `fill_hpd_cu`（现工程算子无 case-gen 用例，复用矩阵构造件） |

## 判定分流

`--judge auto` 按算子查 criteria 的判定卡：

- Cholesky 十算子有卡 → 走 criteria 一段式残差判定，出数值 PASS/FAIL。
- 现工程算子（cmatinv_batched 等）无卡 → 只做**结构性验证**：三键落地、形状与
  dtype 合法、数值有限、info 形态合规，并记一个诊断用的求逆残差（两种布局口径
  各算一遍，报哪一种对上）。**结构性验证不是数值判定**，报告里 `numeric` 记
  `NOT_JUDGED`，真判定等 Cholesky 交付。

## 复跑

`--rerun N`（N≥2）由执行子进程在同一进程同一 stream 内完成：每轮恢复输入、
逐键 bit-wise 比对（Q4 裁定）。确定性结论独立于数值结论（HT-12 口径）。

## 独立重判

`--rejudge <留证目录>` 读 `inputs.npz` 加 `dut.npz` 加 `case.json`，走同一判定函数
复算结论，不连真机。

退出码：0 全部通过（含确定性无失配）；1 存在执行失败、数值 FAIL、结构性不合格或
确定性失配；2 入口参数或契约错误。`formal` 恒 `PENDING_RULING`，本工具不出具正式结论。
"""

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np

import evidence
import exec_case
import op_abi

TOOL = "run_harness.py"
TOOL_VER = "h1-r1"
HERE = Path(__file__).resolve().parent
CRITERIA = HERE.parent.parent / "criteria"
RATIO_BASIS = "A32-f64"


class ContractError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 同源实现装入（与 stream_check 同一套路径注入，避免平行副本）
# ---------------------------------------------------------------------------

def load_modules(gen_dir):
    gen_dir = Path(gen_dir)
    if not (gen_dir / "gen_data_cholesky.py").is_file():
        raise ContractError(f"--gen-dir 里找不到 gen_data_cholesky.py: {gen_dir}")
    for p in (str(CRITERIA), str(gen_dir)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import batched_a0 as a0_mod                 # noqa: E402
    import batched_parallel as bp_mod           # noqa: E402
    import cards_cholesky as cards_mod          # noqa: E402
    import fill_ratio_cpu as ratio_mod          # noqa: E402
    import gen_data_cholesky as gen_mod         # noqa: E402
    import verdict as verdict_mod               # noqa: E402
    return {"a0": a0_mod, "bp": bp_mod, "cards": cards_mod, "ratio": ratio_mod,
            "gen": gen_mod, "verdict": verdict_mod}


def has_criteria_card(mods, op):
    try:
        mods["cards"].get_card(op)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 输入构造：全部复用既有实现
# ---------------------------------------------------------------------------

def probe_case(mods, op, n, batch, seed, nrhs=0, uplo="L"):
    """通路演练输入：复用 case-gen 的矩阵构造件造一批良态矩阵。

    现工程算子在 case-gen 里没有用例，但矩阵构造件可以直接复用——`fill_hpd_cu`
    产 Hermitian 正定矩阵、`fill_spd_cu` 产对称正定矩阵，两者都非奇异，正是求逆族
    算子的合法输入。本函数只拼批维，不新写分布。
    """
    abi = op_abi.get(op)
    gen = mods["gen"]
    rng = np.random.default_rng(seed)
    cx = abi.dtype == "complex64"
    mats = [(gen.fill_hpd_cu(rng, n) if cx else gen.fill_spd_cu(rng, n))
            for _ in range(batch)]
    A64 = np.ascontiguousarray(np.stack(mats)) if abi.batched \
        else np.ascontiguousarray(mats[0])
    A32 = A64.astype(np.complex64 if cx else np.float32)
    return {"case_id": f"probe-{op}-n{n}-b{batch}-s{seed}", "op": op, "n": n,
            "batch": batch if abi.batched else 1, "nrhs": nrhs, "uplo": uplo,
            "seed": seed, "source": "cu", "case_purpose": "probe",
            "_arrays": {"A64": A64, "A32": A32}}


def out32_shape(abi, case):
    n = int(case["n"])
    batch = int(case.get("batch") or 1)
    nrhs = int(case.get("nrhs") or 0)
    cols = nrhs if abi.call_shape == "potrs_b_inplace" else n
    return (batch, n, cols) if abi.batched else (n, cols)


def build_spec(abi, case, runs, device_id, layout="row_major"):
    """执行子进程的 spec。`layout` 是双口径分流点：现工程行主序，任务书列主序。"""
    shape = out32_shape(abi, case)
    return {
        "op": abi.op, "abi": abi.abi, "call_shape": abi.call_shape,
        "dtype": abi.dtype, "info_kind": abi.info_kind,
        "device_id": device_id, "n": int(case["n"]),
        "batch": int(case.get("batch") or 1), "nrhs": int(case.get("nrhs") or 0),
        "lda": int(case["n"]), "uplo": case.get("uplo") or "L",
        "runs": int(runs), "layout": layout,
        "out32_shape": ",".join(str(x) for x in shape),
    }


# ---------------------------------------------------------------------------
# 判定：criteria 唯一实现 / 结构性验证
# ---------------------------------------------------------------------------

def _inv_residual(A, C):
    """诊断用求逆残差 ‖I − A·C‖₁ / (n·‖A‖₁·‖C‖₁)。只做证据，不做判据。"""
    A64 = np.asarray(A, dtype=np.complex128 if np.iscomplexobj(A) else np.float64)
    C64 = np.asarray(C, dtype=A64.dtype)
    n = A64.shape[-1]
    eye = np.eye(n, dtype=A64.dtype)
    num = np.abs(eye - A64 @ C64).sum(axis=-2).max(axis=-1)
    den = n * np.abs(A64).sum(axis=-2).max(axis=-1) * np.abs(C64).sum(axis=-2).max(axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(den > 0, num / den, np.inf)
    return float(np.max(ratio))


def structural_check(abi, case, inputs, dut):
    """结构性验证：三键落地、形状 dtype 合法、数值有限、info 形态合规。

    **不是数值判定**：返回的 `numeric` 恒为 `NOT_JUDGED`。criteria 的判定卡只覆盖
    Cholesky 十算子；现工程算子的真判定等 Cholesky 交付后按交付头接入。
    """
    checks, failures = [], []

    def ck(name, ok, detail=""):
        checks.append({"name": name, "pass": bool(ok), "detail": detail})
        if not ok:
            failures.append(f"{name}: {detail}")

    ck("status_ok", dut.get("status") == "ok", f"status={dut.get('status')!r}")
    out32 = dut.get("out32")
    info = dut.get("info")
    ck("out32_present", out32 is not None)
    ck("info_present", info is not None)
    if out32 is None or info is None:
        return {"numeric": "NOT_JUDGED", "structural": "FAIL",
                "checks": checks, "failures": failures, "sanity": None}

    want_shape = out32_shape(abi, case)
    want_dtype = exec_case.NP_DTYPE[abi.dtype]
    ck("out32_shape", tuple(out32.shape) == want_shape,
       f"{tuple(out32.shape)} vs 期望 {want_shape}")
    ck("out32_dtype", out32.dtype == want_dtype,
       f"{out32.dtype} vs 期望 {np.dtype(want_dtype).name}")
    ck("out32_finite", bool(np.isfinite(out32).all()),
       "存在 NaN 或 Inf" if not bool(np.isfinite(out32).all()) else "")
    if abi.info_kind == "array":
        ok = isinstance(info, np.ndarray) and info.dtype == np.int32 \
            and info.shape == (int(case.get("batch") or 1),)
        ck("info_shape_array", ok,
           f"期望 (batch,) int32，得到 {getattr(info, 'shape', type(info))} "
           f"{getattr(info, 'dtype', '')}")
    else:
        ck("info_shape_scalar", np.ndim(info) == 0,
           f"期望标量，得到 ndim={np.ndim(info)}")

    # 诊断证据：求逆残差。片上布局口径两算（现工程文档自述行主序，任务书要列主序），
    # 报哪一种对上——这是证据，不是判据。
    # 口径判别的边界：输入是 Hermitian 或对称矩阵时 conj(Aᵀ) == A，两个残差必然相等，
    # `layout_match` 对这类输入判别不出布局。要判布局得喂非对称输入。
    sanity = None
    if abi.call_shape in ("outofplace_a_ainv", "inplace_a") and "A64" in inputs:
        A = np.asarray(inputs["A64"])
        C = np.asarray(out32)
        try:
            direct = _inv_residual(A, C)
            swapped = _inv_residual(np.conjugate(np.swapaxes(A, -1, -2)), C)
            indistinguishable = np.allclose(A, np.conjugate(np.swapaxes(A, -1, -2)))
            sanity = {"inv_residual_direct": direct,
                      "inv_residual_transpose_conj": swapped,
                      "layout_match": ("indistinguishable" if indistinguishable
                                       else "direct" if direct <= swapped
                                       else "transpose_conj"),
                      "note": "诊断证据，不是判据；判定卡覆盖 Cholesky 十算子。"
                              "对称/Hermitian 输入下两个残差必然相等，布局判别不出"}
        except (ValueError, TypeError) as exc:
            sanity = {"error": f"{type(exc).__name__}: {exc}"}

    return {"numeric": "NOT_JUDGED",
            "structural": "PASS" if not failures else "FAIL",
            "checks": checks, "failures": failures, "sanity": sanity}


def criteria_judge(mods, card, case, arrays, dut, ratio_cpu, ratio_status,
                   ratio_mean=None, jobs=1, sample_map=None):
    """criteria 判定入口。单矩阵直通 verdict.judge，批量走 A0 两层。"""
    case_arrays = dict(arrays)
    case_arrays.update(case)
    case_arrays["ratio_cpu"] = ratio_cpu
    case_arrays["ratio_cpu_status"] = ratio_status
    if sample_map is not None:
        case_arrays["sample_map"] = sample_map
    if ratio_mean is not None:
        case_arrays["ratio_cpu_mean"] = ratio_mean
    if getattr(card, "batched", False):
        return mods["a0"].judge_a0(card, case_arrays, dut, jobs=jobs)
    return mods["bp"].judge_parallel(card, case_arrays, dut, jobs=jobs)


# ---------------------------------------------------------------------------
# case 级编排
# ---------------------------------------------------------------------------

def prepare_case(mods, case, abi):
    """现场造输入，返回 (arrays, exec_inputs, sample_map)。

    `arrays` 给判定用（含 A64/golden 等），`exec_inputs` 只含要落 bin 的 f32/c64
    输入——大数组不重复一份。
    """
    gen = mods["gen"]
    sample_map = None
    if case.get("case_purpose") == "probe":
        arrays = case.pop("_arrays")
        return arrays, {"in_a": arrays["A32"]}, None
    gen.validate_case(case)          # 进场校验走 case-gen 同一实现
    if abi.batched:
        contents = gen.build_batched_contents(case)
        sample_map = gen.derive_sample_map(case["seed"], case["batch"])
        arrays = gen.expand_sampled_rows(contents, sample_map, 0, int(case["batch"]))
        arrays["_contents"] = contents
    else:
        arrays = gen.build_case_arrays(case)
    exec_inputs = {"in_a": arrays["A32"]}
    if abi.call_shape == "potrs_b_inplace":
        if "B32" not in arrays:
            raise ContractError(f"{case['case_id']}: potrs 族缺 B32 输入")
        exec_inputs["in_b"] = arrays["B32"]
    return arrays, exec_inputs, sample_map


def run_one(mods, case, abi, ctx):
    """跑一个 case 的全程：造输入 → 执行 → 判定 → 取证。返回报告记录。"""
    cid = case["case_id"]
    t0 = time.time()
    arrays, exec_inputs, sample_map = prepare_case(mods, case, abi)
    contents = arrays.pop("_contents", None)

    spec = build_spec(abi, case, ctx["rerun"], ctx["device_id"], ctx["layout"])
    exec_rec = exec_case.run_case(
        ctx["executor"], Path(ctx["work_dir"]) / cid, spec, exec_inputs,
        ctx["repo"], ctx["ascend_home"], ctx["build_dir"], ctx["timeout"],
        ctx["cwd"])
    dut = evidence.three_key(exec_rec["out32"], exec_rec["info"], exec_rec["status"])
    det = exec_case.rerun_record(exec_rec["result"])

    verdict_rec, numeric = None, None
    if exec_rec["kind"] in exec_case.EXECUTED_KINDS:
        if ctx["judge"] == "criteria":
            card = mods["cards"].get_card(case["op"])
            ratio_cpu, ratio_status = compute_ratio(mods, case, arrays, contents,
                                                    sample_map)
            judge_arrays = contents if contents is not None else arrays
            verdict_rec = criteria_judge(
                mods, card, case, judge_arrays, dut, ratio_cpu, ratio_status,
                ctx["ratio_mean"].get(case["op"]) if ctx["ratio_mean"] else None,
                ctx["jobs"], sample_map)
            numeric = verdict_rec.get("numeric")
        else:
            verdict_rec = structural_check(abi, case, arrays, dut)
            numeric = verdict_rec["structural"]   # 结构性结论，不是数值结论

    rec = {
        "case_id": cid, "op": case["op"], "n": case.get("n"),
        "batch": case.get("batch"), "uplo": case.get("uplo"),
        "abi": abi.abi, "call_shape": abi.call_shape,
        "exec": {"kind": exec_rec["kind"], "status": exec_rec["status"],
                 "detail": exec_rec["detail"], "returncode": exec_rec["returncode"],
                 "wall_seconds": exec_rec["wall_seconds"],
                 "lib_path": exec_rec["result"].get("lib_path"),
                 "run1_ms": exec_rec["result"].get("run1_ms"),
                 "op_ret": exec_rec["result"].get("run1_ret")},
        "judge_mode": ctx["judge"],
        "verdict": verdict_rec,
        "determinism": det,
        "seconds": round(time.time() - t0, 3),
    }

    # numeric 在 criteria 模式是数值结论，在 structural 模式是结构性结论；
    # 两种模式下「不是 PASS 就留证」的规则相同，不分支。
    keep, reason = evidence.should_keep(exec_rec["kind"], numeric, det,
                                        ctx["keep_ids"], cid)
    if keep:
        bundle = evidence.export_failure(
            Path(ctx["evidence_dir"]) / cid, case, abi, exec_rec,
            {"A64": arrays.get("A64"), "A32": arrays.get("A32"),
             "B32": arrays.get("B32"), "golden32": arrays.get("golden32")},
            dut, reason, ctx["provenance"], verdict_rec, HERE)
        ok, missing = evidence.bundle_is_self_contained(
            bundle, require_dut=exec_rec["kind"] in exec_case.EXECUTED_KINDS)
        rec["evidence"] = {"bundle": str(bundle), "reason": reason,
                           "self_contained": ok, "missing": missing}
    else:
        rec["evidence"] = None
        rec["discarded_arrays"] = evidence.discard(arrays, dut, exec_inputs,
                                                   contents or {})
    return rec


def compute_ratio(mods, case, arrays, contents, sample_map):
    """CPU 同精度链的 ratio_cpu（批量逐内容）。实现在 fill_ratio_cpu，本层只编排。"""
    ratio_mod, verdict_mod = mods["ratio"], mods["verdict"]
    lapack = ratio_mod._lapack()
    if contents is None:
        try:
            return float(ratio_mod.run_chain(verdict_mod, lapack, case, arrays)), "ok"
        except ratio_mod.PrepFailed:
            return None, "prep_failed"
    base_op = mods["cards"].get_card(case["op"]).base_op
    vals, failed = [], 0
    for i in range(len(sample_map)):
        sub_case = {"case_id": f"{case['case_id']}#c{i}", "op": base_op,
                    "uplo": case["uplo"]}
        sub_arrays = {k: contents[k][i]
                      for k in ("A64", "A32", "B64", "B32") if k in contents}
        try:
            vals.append(float(ratio_mod.run_chain(verdict_mod, lapack, sub_case,
                                                  sub_arrays)))
        except ratio_mod.PrepFailed:
            vals.append(None)
            failed += 1
    return vals, ("ok" if failed < len(sample_map) else "prep_failed")


# ---------------------------------------------------------------------------
# 独立重判
# ---------------------------------------------------------------------------

def rejudge(bundle_dir, gen_dir, jobs=1):
    """读留证件复算结论，不连真机。返回 (记录, 退出码)。"""
    bundle = Path(bundle_dir)
    meta_path = bundle / "case.json"
    if not meta_path.is_file():
        raise ContractError(f"{bundle} 不是留证件（缺 case.json）")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    case, abi_d = meta["case"], meta["abi"]
    dut_path = bundle / "dut.npz"
    if not dut_path.is_file():
        return {"case_id": case.get("case_id"), "rejudge": "no_dut",
                "reason": meta.get("reason"),
                "note": "执行未产出三键，没有可重判的被测输出"}, 1
    mods = load_modules(gen_dir)
    abi = op_abi.get(abi_d["op"])
    dut = evidence.load_dut_npz(dut_path)
    if dut.get("status") != "ok":
        # status 非 ok 即执行失败，不判精度（SKILL.md 检查条件）。三键仍在留证件里，
        # 供人看现场；重判只复述执行失败这件事，不把它说成数值 FAIL。
        return {"case_id": case.get("case_id"), "op": case.get("op"),
                "rejudge": "exec_failed", "status": dut["status"],
                "reason": meta.get("reason"),
                "note": "被测输出 status 非 ok：记执行失败，不参与数值统计；"
                        "先解决执行问题再重新运行"}, 1
    with np.load(bundle / "inputs.npz", allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    if has_criteria_card(mods, case["op"]):
        card = mods["cards"].get_card(case["op"])
        sample_map = (mods["gen"].derive_sample_map(case["seed"], case["batch"])
                      if getattr(card, "batched", False) else None)
        ratio_cpu, ratio_status = compute_ratio(mods, case, arrays, None, sample_map)
        v = criteria_judge(mods, card, case, arrays, dut, ratio_cpu, ratio_status,
                           None, jobs, sample_map)
        numeric = v.get("numeric")
    else:
        v = structural_check(abi, case, arrays, dut)
        numeric = v["structural"]
    rec = {"case_id": case.get("case_id"), "op": case.get("op"),
           "rejudge": "done", "numeric": numeric, "verdict": v,
           "original": {"reason": meta.get("reason"),
                        "exec_kind": (meta.get("exec") or {}).get("kind"),
                        "verdict": meta.get("verdict")}}
    return rec, (0 if numeric in ("PASS",) else 1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _pick_cases(mods, canonical_path, ops, max_n, limit):
    doc = json.loads(Path(canonical_path).read_text(encoding="utf-8"))
    cases = doc.get("cases")
    if not isinstance(cases, list):
        raise ContractError(f"{canonical_path}: 顶层缺 cases 数组")
    ratio_mean = None
    idx = Path(canonical_path).parent / "cases" / "index.json"
    if idx.is_file():
        index_doc = json.loads(idx.read_text(encoding="utf-8")) or {}
        basis = index_doc.get("ratio_basis")
        if basis != RATIO_BASIS:
            raise ContractError(
                f"旧基包不受理：参考值基准非 {RATIO_BASIS}（以新为准裁定 2026-10-09），"
                f"请使用更新版任务包（index.ratio_basis={basis!r}）")
        ratio_mean = index_doc.get("ratio_cpu_mean")
    want = set(o.strip() for o in ops.split(",")) if ops else None
    picked = [c for c in cases
              if (want is None or c.get("op") in want)
              and (max_n is None or c.get("n", 0) <= max_n)]
    if limit:
        picked = picked[:limit]
    if not picked:
        raise ContractError("筛选后没有 case 可跑（检查 --ops/--max-n）")
    return picked, ratio_mean


def main(argv=None):
    ap = argparse.ArgumentParser(prog=TOOL, description=__doc__.splitlines()[0])
    ap.add_argument("--gen-dir", required=True,
                    help="repo-task-solver-case-gen 的 scripts 目录（提供输入构造与 ratio 链）")
    ap.add_argument("--report", help="判定记录 JSON 输出路径")
    ap.add_argument("--rejudge", help="留证目录；只重判不连真机，其余参数忽略")

    src = ap.add_argument_group("输入来源")
    src.add_argument("--canonical", help="canonical_cases.json 路径（Cholesky 任务包）")
    src.add_argument("--probe-op", help="通路演练：现工程算子名（如 cmatinv_batched）")
    src.add_argument("--n", type=int, default=8, help="演练规格 n")
    src.add_argument("--batch", type=int, default=2, help="演练规格 batch")
    src.add_argument("--seed", type=int, default=923000001, help="演练输入 seed")
    src.add_argument("--ops", help="canonical 的算子子集，逗号分隔")
    src.add_argument("--max-n", type=int, default=None)
    src.add_argument("--limit", type=int, default=None)

    mach = ap.add_argument_group("真机参数")
    mach.add_argument("--repo", help="ops-solver 仓根（S1 已构建）")
    mach.add_argument("--ascend-home", help="缺省取 $ASCEND_HOME_PATH")
    mach.add_argument("--build-dir", default=None, help="缺省 <repo>/build")
    mach.add_argument("--executor", default=None,
                      help="已编译的执行件；缺省就地编译到 <work>/harness_exec")
    mach.add_argument("--work-dir", default="harness-work", help="per-case 现场目录")
    mach.add_argument("--evidence-dir", default="harness-evidence", help="留证件根目录")
    mach.add_argument("--provenance", default=None, help="S1 构建取证 JSON，进留证件")
    mach.add_argument("--device", type=int, default=0)
    mach.add_argument("--timeout", type=int, default=1800)
    mach.add_argument("--layout", default="row_major",
                      choices=("row_major", "column_major"),
                      help="片上布局口径（双口径分流点：现工程行主序，任务书列主序）")

    pol = ap.add_argument_group("判定与取证")
    pol.add_argument("--judge", default="auto",
                     choices=("auto", "criteria", "structural"))
    pol.add_argument("--rerun", type=int, default=1,
                     help="确定性复跑次数（≥2 启用；同子进程同 stream，每轮恢复输入）")
    pol.add_argument("--jobs", type=int, default=1, help="批量逐矩阵判定的分块数")
    pol.add_argument("--keep", action="append", default=[],
                     help="人工指定留证的 case_id（可重复）")
    args = ap.parse_args(argv)

    if args.rejudge:
        try:
            rec, code = rejudge(args.rejudge, args.gen_dir, args.jobs)
        except ContractError as exc:
            print(f"[{TOOL}] 重判输入错误: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(rec, ensure_ascii=False, indent=1, default=str))
        if args.report:
            exec_case.dump_json(args.report, {"tool": {"name": TOOL, "ver": TOOL_VER},
                                              "mode": "rejudge", "record": rec})
        return code

    try:
        if not args.report:
            raise ContractError("--report 必填（重判模式除外）")
        if bool(args.canonical) == bool(args.probe_op):
            raise ContractError("--canonical 与 --probe-op 二选一")
        if not args.repo:
            raise ContractError("--repo 必填：执行段要对该仓的公开头编译")
        ascend_home = args.ascend_home or os.environ.get("ASCEND_HOME_PATH")
        if not ascend_home:
            raise ContractError("--ascend-home 缺省取 $ASCEND_HOME_PATH，两者都没有")
        mods = load_modules(args.gen_dir)

        ratio_mean = None
        if args.probe_op:
            op_abi.require_exec(args.probe_op)
            cases = [probe_case(mods, args.probe_op, args.n, args.batch, args.seed)]
        else:
            cases, ratio_mean = _pick_cases(mods, args.canonical, args.ops,
                                            args.max_n, args.limit)
            for c in cases:
                op_abi.require_exec(c["op"])

        work = Path(args.work_dir)
        executor = args.executor
        compile_rec = None
        if not executor:
            wanted = sorted({c["op"] for c in cases})
            compile_rec = exec_case.compile_executor(
                args.repo, ascend_home, work / "harness_exec",
                ops=wanted, build_dir=args.build_dir)
            executor = compile_rec["bin"]
        provenance = (json.loads(Path(args.provenance).read_text(encoding="utf-8"))
                      if args.provenance else None)
    except (ContractError, op_abi.AbiError, exec_case.ExecError) as exc:
        print(f"[{TOOL}] 输入/契约错误: {exc}", file=sys.stderr)
        return 2

    ctx = {"executor": executor, "work_dir": work,
           "evidence_dir": args.evidence_dir, "repo": args.repo,
           "ascend_home": ascend_home, "build_dir": args.build_dir,
           "device_id": args.device, "timeout": args.timeout,
           "cwd": args.repo, "rerun": args.rerun, "jobs": args.jobs,
           "layout": args.layout, "keep_ids": set(args.keep),
           "provenance": provenance, "ratio_mean": ratio_mean, "judge": None}

    records, t0 = [], time.time()
    n_ok = n_bad = n_err = n_det_fail = 0
    for case in cases:
        abi = op_abi.get(case["op"])
        ctx["judge"] = (args.judge if args.judge != "auto"
                        else ("criteria" if has_criteria_card(mods, case["op"])
                              else "structural"))
        try:
            rec = run_one(mods, case, abi, ctx)
        except Exception as exc:                       # 单 case 失败不吞整轮
            n_err += 1
            records.append({"case_id": case.get("case_id"), "op": case.get("op"),
                            "error": f"{type(exc).__name__}: {exc}"})
            continue
        records.append(rec)
        v = rec.get("verdict") or {}
        status = v.get("numeric") if ctx["judge"] == "criteria" else v.get("structural")
        if rec["exec"]["kind"] not in exec_case.EXECUTED_KINDS or v.get("error"):
            n_err += 1
        elif status in ("PASS",):
            n_ok += 1
        else:
            n_bad += 1
        det = rec.get("determinism")
        if det is not None and not det.get("consistent"):
            n_det_fail += 1
        print(f"[{TOOL}] {rec['case_id']} exec={rec['exec']['kind']} "
              f"{ctx['judge']}={status} det="
              f"{'-' if det is None else ('ok' if det['consistent'] else 'FAIL')}",
              file=sys.stderr)

    report = {
        "tool": {"name": TOOL, "ver": TOOL_VER},
        "params": {k: v for k, v in vars(args).items() if k != "keep"},
        "keep": args.keep,
        "env": {"python": sys.version.split()[0], "numpy": np.__version__,
                "machine": platform.machine(), "platform": platform.platform()},
        "compile": compile_rec,
        "provenance": provenance,
        "summary": {"total": len(cases), "pass": n_ok, "fail": n_bad,
                    "error": n_err, "det_fail": n_det_fail,
                    "wall_seconds": round(time.time() - t0, 1)},
        "formal": "PENDING_RULING",
        "judge_scope": "criteria 判定卡只覆盖 Cholesky 十算子；其余算子记结构性结论，"
                       "numeric=NOT_JUDGED，真判定等 Cholesky 交付后按交付头接入",
        "cases": records,
    }
    exec_case.dump_json(args.report, report)
    print(f"[{TOOL}] 完成 {len(cases)} case: pass={n_ok} fail={n_bad} err={n_err} "
          f"det_fail={n_det_fail} → {args.report}")
    return 0 if (n_bad == 0 and n_err == 0 and n_det_fail == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
