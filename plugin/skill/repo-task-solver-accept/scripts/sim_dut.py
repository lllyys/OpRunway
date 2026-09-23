#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sim_dut.py —— S1-Cholesky E 卡：模拟被测（accept 骨架的正例/扰动数据源）。

契约：dev-doc/solver/solver-s1-cholesky-spec.md §2.5（CLI 与被测输出目录格式）、
§1（本片 accept 骨架用模拟被测，不跑真算子/NPU）。模拟量产语义：

- none（正例）：out32 = 包内 golden32 逐字节复制——理想被测，用于验证判定通路的
  正向可分（E 行断言：数值 PASS 且 formal=待裁）。
- scale：out32 = golden32 × 2（整体相对偏移 δ=1）。倍数这样定标：DPOT03 分母带
  ‖A‖₁‖C‖₁ ≈ κ₁，均匀相对偏移 δ 的兜底残差 ≈ δ/(n·κ₁·ε)，冻结 S1 集最差
  n·κ₁·ε ≈ 0.28（spotri-9002，实测 δ=2⁻⁶ 时 ratio 仅 0.056，判 PASS 属判据
  语义内），δ=1 使全部 case 兜底超阈（最差余量 ≥3.5×）；layer1 的 rtol 2⁻¹⁰
  与 max_abs 门更早失守 → 数值 FAIL。
- zero：out32 = 全零。potrf 还原残差打满；potrs/potri 触发残差零分母
  （x/‖C‖ 为 0），fallback 记残差不可计算 → 数值 FAIL。
- nan：out32 = golden32 复制后首元素置 NaN。layer1 统计把 NaN 计为不符且
  max_abs=inf（双门必不过），fallback 对 NaN 拒算 → 数值 FAIL。

扰动施加于包内全部 case；info 恒 0、status 恒 "ok"（扰动只动数值，不模拟接口层
失败——info/确定性契约在本片记证据不足，spec §2.3′）。输出无随机性，可复跑比对。

CLI（spec §2.5，逐字）：
    sim_dut.py --package <dir> --out <dir> [--perturb none|scale|zero|nan]
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
TOOL_VER = "s1-E1"
PERTURBS = ("none", "scale", "zero", "nan")
SCALE_FACTOR = np.float32(2.0)  # 定标依据见模块文档 scale 条


def perturb_out32(golden32, mode):
    """对 golden32 施加扰动，返回 float32 输出阵（语义见模块文档）。"""
    g = np.asarray(golden32, dtype=np.float32)
    if mode == "none":
        return g.copy()
    if mode == "scale":
        return (g * SCALE_FACTOR).astype(np.float32)
    if mode == "zero":
        return np.zeros_like(g)
    if mode == "nan":
        out = g.copy()
        out.flat[0] = np.float32("nan")
        return out
    raise ValueError(f"未知扰动 {mode!r}，只支持 {PERTURBS}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="S1 模拟被测（spec §2.5 CLI）")
    ap.add_argument("--package", required=True, help="算子包目录（spec §2.2）")
    ap.add_argument("--out", required=True, help="被测输出目录（<case_id>.npz 落此）")
    ap.add_argument("--perturb", choices=PERTURBS, default="none")
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
            skipped.append({"case_id": case_id, "reason": f"包内 npz 缺失: {npz_rel}"})
            continue
        try:
            with np.load(path) as z:
                golden32 = z["golden32"]
        except Exception as exc:
            skipped.append({"case_id": case_id,
                            "reason": f"npz 不可读: {type(exc).__name__}: {exc}"})
            continue
        out32 = perturb_out32(golden32, args.perturb)
        np.savez(out_dir / f"{case_id}.npz",
                 out32=out32, info=np.int64(0), status=np.str_("ok"))
        written.append(case_id)

    manifest = {
        "tool": {"name": TOOL, "ver": TOOL_VER},
        "spec": "dev-doc/solver/solver-s1-cholesky-spec.md#2.5",
        "package": str(package),
        "perturb": args.perturb,
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
