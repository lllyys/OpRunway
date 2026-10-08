#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sim_dut.py —— S1-Cholesky E 卡：模拟被测（accept 骨架的正例/扰动数据源）。

契约：dev-doc/solver/solver-s1-cholesky-spec.md §2.5（CLI 与被测输出目录格式）、
§1（本片 accept 骨架用模拟被测，不跑真算子/NPU）；S2c 复数增量按
dev-doc/solver/solver-s2-spec.md §4（dtype 映射：out32 与包内 golden32 同 dtype，
实数 case float32、复数 case complex64，不再强转 float32）。模拟量产语义：

- none（正例）：out32 = 包内 golden32 逐字节复制——理想被测，用于验证判定通路的
  正向可分（E 行断言：数值 PASS 且 formal=待裁）。s2-A1 一段式下其残差在底噪
  水平（golden32 即合法 FP32 实现），健康阈值必过。
- scale：out32 = golden32 × 2（整体相对偏移 δ=1）。倍数按一段式残差语义定标：
  DPOT03 分母带 ‖A‖₁‖C‖₁ ≈ κ₁，均匀相对偏移 δ 的残差 ≈ δ/(n·κ₁·ε)，冻结 S1 集
  最差 n·κ₁·ε ≈ 0.28（spotri-9002，实测 δ=2⁻⁶ 时 ratio 仅 0.056，判 PASS 属判据
  语义内），δ=1 使全部 case 残差超阈（最薄处为 potri 绝对线 0.1，余量 ≥3.5×；
  potrf/potrs 残差 ≈ 3/(nε)、δ/ε 量级，远超 5·ratio_cpu/3·mean）→ 数值 FAIL。
- zero：out32 = 全零。potrf 还原残差打满（ratio = 1/(n·ε)）→ 数值 FAIL；
  potrs/potri 触发残差零分母（‖x‖/‖C‖ 为 0），残差不可计算 → 数值 FAIL、
  error 指认零分母（fail-closed，不崩溃不放行）。
- nan：out32 = 全块 NaN。残差接口的有限性校验拒算 → 数值 FAIL、error 指认
  NaN/Inf（s2-A1 一段式下任意单点 NaN 即被拒算；保持全块只为与历史产物字节
  稳定，不回调强度）。

复数专属两类（S2 spec §4 验证增量：纯虚部错误、漏共轭；施加于实数 case 属用法
错误，逐 case 记 skipped 并以退出码如实表达，不静默降级）：

S3 批量增量（S3 spec §4/§6，D3 卡）：

- 批量 case（op 带 Batched 后缀，golden32 为 (batch, n, cols) 三维）逐字节复制或
  整批逐元素扰动语义与单矩阵相同（nan 为全块 NaN，批内每矩阵同语义）。
- `--perturb-index i` 做**逐矩阵扰动**：只扰动第 i 个矩阵（0 起），其余矩阵逐字节
  复制——负例演练「单矩阵超限扰动时整 case 数值 FAIL 且报告指认矩阵序号」的数据源。
  施加于非批量 case 属用法错误，逐 case 记 skipped（同复数专属扰动的处理）。
- info 按接口角色分型（任务书接口说明第 69 行）：potrfBatched 族写 int32
  shape=(batch,) 全 0 infoArray（batch=1 也保 (1,) 不标量化）；potrsBatched 族与
  单矩阵算子写标量 0。

HT-8 info 契约用例（index 条目 case_purpose=="info"，npz 无 golden32——构造性
用例只带输入数组）：扰动 none（正例）产 info=k_expected（理想被测，契约项数值
PASS）；任一扰动模式非 none（负例演练）产错误 info——k_expected>0 报 k+1（下标
漂移一类实现错），k_expected<0（非法参数路径，期望 -1）报 0（未拒绝非法参数）——
契约项数值 FAIL。out32 取 A32 同形全零占位，判定不消费（_judge_info_inner 只比
info）；status 恒 "ok"。

S4 六算子 v2 增量（Mr.0 2026-09-24 裁定：六算子补发纯脚本 v2、与 batched 统一形态）：

- 纯脚本形态自此覆盖全部新包（批量四算子与单矩阵六算子 v2）。纯脚本包内
  cases/index.json 条目记 `materialize:"gen"`、不携带 npz——本工具从**现场生成的
  golden 取材**：先按包 README 第 0 步用包内 gen_data.py 造数，再把 --package 指向
  其 --out 目录（与批量包同机制）。误把 --package 指向纯脚本包根时，逐 case 的
  skip 理由会指回「先造数后测」流程，不再报笼统的「npz 缺失」。

- imag（纯虚部）：out32 = golden32 + i·|golden32|——逐元素加纯虚偏移，幅度取该
  元素复模（δ=1，与 scale 同定标，残差同量级超阈）。实部逐位不动、误差全在
  虚部：只比实部的实现会放过它，DPOT 复数版的复模残差必须抓住 → 数值 FAIL。
- conj（漏共轭）：out32 = conj(golden32)——模拟漏共轭实现（L·Lᵀ 顶替 L·Lᴴ 一类）。
  偏差 = 2·|Im golden32|，经共轭转置 recon 在残差分子如实放大 → 数值 FAIL；
  虚部恒零的数据数学上区分不了漏共轭，故本扰动只对复数 case 有意义。

扰动施加于包内全部 case；info 恒 0、status 恒 "ok"（扰动只动数值，不模拟接口层
失败——info/确定性契约在本片记证据不足，spec §2.3′）。输出无随机性，可复跑比对；
none/scale/zero/nan 对实数 case 的输出与 S1 版逐位一致（S2c 实数字节稳定红线）。

CLI（spec §2.5 基础上 S2c 增两类复数扰动、S3 增逐矩阵序号）：
    sim_dut.py --package <dir> --out <dir> [--perturb none|scale|zero|nan|imag|conj]
               [--perturb-index i]
--package 也可指向 gen_data 的产物目录（纯脚本包「先造数后测」流程的 data 目录，
同为 cases/index.json + cases/*.npz 布局；纯脚本包只有这一条取材通路）。
输出：<out>/<case_id>.npz 含 out32/info/status（spec §2.5 被测输出目录格式），另落
<out>/sim_manifest.json 记录来源包、扰动模式与环境版本（溯源件，accept_run 不消费）。
"""

import argparse
import json
import platform
import sys
from pathlib import Path

import numpy as np

TOOL = "sim_dut.py"
TOOL_VER = "s2a1-E6"  # s2-A1：负例预期按一段式残差语义重标（输出字节不变）；承 s4-E5
PERTURBS = ("none", "scale", "zero", "nan", "imag", "conj")
COMPLEX_ONLY_PERTURBS = ("imag", "conj")
SCALE_FACTOR = np.float32(2.0)  # 定标依据见模块文档 scale 条


def info_dut_value(k_expected, mode):
    """HT-8：info 契约用例的 sim 被测 info 值（sim_dut 与 stream_check 共用，无平行副本）。
    正例（mode=="none"）= k_expected；负例（其余扰动模式）= 错误 info——
    k_expected>0 报 k+1（下标漂移一类实现错），k_expected<0（非法参数路径）报 0
    （未拒绝非法参数）。语义见模块文档 HT-8 段。"""
    if mode == "none":
        return k_expected
    return 0 if k_expected < 0 else k_expected + 1


def perturb_out32(golden32, mode, matrix_index=None):
    """对 golden32 施加扰动，返回与其同 dtype 的输出阵（语义见模块文档）。

    dtype 跟随包内 golden32（index/包契约的 dtype：实数 float32、复数 complex64，
    S2 spec §4 字段名不变）；复数专属扰动喂实数 case 抛 ValueError（fail-closed）。
    matrix_index 非空时做逐矩阵扰动（S3 spec §4 负例承诺）：只扰动批量 case 的
    第 matrix_index 个矩阵，其余矩阵逐字节复制；非批量 case（golden32 非三维）或
    序号越界抛 ValueError。nan 模式递归到子矩阵仍取全块（见模块文档 nan 条）。
    """
    g = np.asarray(golden32)
    if mode in COMPLEX_ONLY_PERTURBS and not np.issubdtype(g.dtype, np.complexfloating):
        raise ValueError(
            f"扰动 {mode!r} 是复数专属（S2 spec §4），对 dtype={g.dtype} 的 case 不适用")
    if matrix_index is not None:
        if g.ndim != 3:
            raise ValueError(
                f"--perturb-index 仅对批量 case（golden32 三维）有效，"
                f"得到 shape={g.shape}")
        if not 0 <= matrix_index < g.shape[0]:
            raise ValueError(
                f"--perturb-index {matrix_index} 越界（batch={g.shape[0]}）")
        out = g.copy()
        out[matrix_index] = perturb_out32(g[matrix_index], mode)
        return out
    if mode == "none":
        return g.copy()
    if mode == "scale":
        return (g * SCALE_FACTOR).astype(g.dtype)
    if mode == "zero":
        return np.zeros_like(g)
    if mode == "nan":
        return np.full_like(g, np.nan, dtype=g.dtype)   # 全块（强度保持，见模块文档 nan 条）
    if mode == "imag":
        return (g + 1j * np.abs(g)).astype(g.dtype)
    if mode == "conj":
        return np.conj(g).astype(g.dtype)
    raise ValueError(f"未知扰动 {mode!r}，只支持 {PERTURBS}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="S1 模拟被测（spec §2.5 CLI）")
    ap.add_argument("--package", required=True, help="算子包目录（spec §2.2）")
    ap.add_argument("--out", required=True, help="被测输出目录（<case_id>.npz 落此）")
    ap.add_argument("--perturb", choices=PERTURBS, default="none")
    ap.add_argument("--perturb-index", type=int, default=None,
                    help="批量 case 的逐矩阵扰动：只扰动该矩阵序号（0 起），"
                         "其余矩阵逐字节复制（S3 spec §4 负例演练）")
    args = ap.parse_args(argv)

    package, out_dir = Path(args.package), Path(args.out)
    index_path = package / "cases" / "index.json"
    try:
        with open(index_path, "r", encoding="utf-8") as fh:
            index = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[sim_dut] 错误: cases/index.json 不可用: {exc}", file=sys.stderr)
        return 2
    cases = index.get("cases")
    if not isinstance(cases, list) or not cases:
        print("[sim_dut] 错误: index.json 无 cases 列表", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []
    for entry in cases:
        case_id, npz_rel = entry.get("case_id"), entry.get("npz")
        path = package / npz_rel if npz_rel else None
        if path is None or not path.is_file():
            if entry.get("materialize") == "gen":
                reason = ('纯脚本包不携带数组（materialize:"gen"）：先按包 README '
                          "第 0 步用包内 gen_data.py 造数，再把 --package 指向其 "
                          "--out 目录")
            else:
                reason = f"包内 npz 缺失: {npz_rel}"
            skipped.append({"case_id": case_id, "reason": reason})
            continue
        if entry.get("case_purpose") == "info":
            # HT-8：info 契约用例（npz 无 golden32）——正/负例语义见模块文档
            try:
                with np.load(path) as z:
                    a32 = z["A32"]
                k_expected = int(entry["k_expected"])
            except Exception as exc:
                skipped.append({"case_id": case_id,
                                "reason": f"info 用例输入不可用: "
                                          f"{type(exc).__name__}: {exc}"})
                continue
            info_val = np.int64(info_dut_value(k_expected, args.perturb))
            np.savez(out_dir / f"{case_id}.npz",
                     out32=np.zeros_like(a32), info=np.int64(info_val),
                     status=np.str_("ok"))
            written.append(case_id)
            continue
        try:
            with np.load(path) as z:
                golden32 = z["golden32"]
        except Exception as exc:
            skipped.append({"case_id": case_id,
                            "reason": f"npz 不可读: {type(exc).__name__}: {exc}"})
            continue
        try:
            out32 = perturb_out32(golden32, args.perturb, args.perturb_index)
        except ValueError as exc:
            skipped.append({"case_id": case_id, "reason": str(exc)})
            continue
        # info 按接口角色分型（S3 spec §4）：potrfBatched 族产 (batch,) int32
        # infoArray（batch=1 不标量化）；potrsBatched 族与单矩阵算子产标量。
        op = str(entry.get("op") or "")
        if op.endswith("Batched") and "potrf" in op:
            if out32.ndim != 3:
                skipped.append({"case_id": case_id,
                                "reason": f"op={op} 但 golden32 非批量三维: "
                                          f"shape={out32.shape}"})
                continue
            info_val = np.zeros(out32.shape[0], dtype=np.int32)
        else:
            info_val = np.int64(0)
        np.savez(out_dir / f"{case_id}.npz",
                 out32=out32, info=info_val, status=np.str_("ok"))
        written.append(case_id)

    manifest = {
        "tool": {"name": TOOL, "ver": TOOL_VER},
        "spec": "dev-doc/solver/solver-s1-cholesky-spec.md#2.5",
        "package": str(package),
        "perturb": args.perturb,
        "perturb_index": args.perturb_index,
        "written": written,
        "skipped": skipped,
        "env": {"python": platform.python_version(), "numpy": np.__version__},
    }
    with open(out_dir / "sim_manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[sim_dut] perturb={args.perturb}: 写出 {len(written)} case，"
          f"跳过 {len(skipped)}（{out_dir}）")
    # 包内一个 case 都模拟不出来属输入问题，如实以退出码表达（fail-closed）。
    return 0 if written and not skipped else (2 if not written else 3)


if __name__ == "__main__":
    sys.exit(main())
