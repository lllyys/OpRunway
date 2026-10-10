#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence.py —— harness 的取证段：三键回传、失败留证、通过即弃。

三条规则（Q3 裁定「通过即弃大数组、异常自动留证、人工 dump 补充」）：

1. **三键回传**：`{out32, info, status}` 组成 npz 交给编排层——协议与包通路的被测
   输出三键逐字相同（SKILL.md 入口参数表），判定侧不需要知道数组是怎么来的。
2. **失败自动留证**：执行失败、崩溃、超时、数值 FAIL、复跑失配，五类任一出现就把
   输入、输出、spec、日志与构建取证打成一个自包含目录。seed 重跑找不回偶发错误
   输出，所以留证必须在当场、必须带实际数组。
3. **通过即弃**：数值 PASS 且确定性无失配的 case，大数组立即丢弃，只留判定记录与
   逐轮指纹。人工 `--keep` 指定的 case 例外。

留证件的自包含判据是**能独立重判**：导出目录加上 criteria 即可复算结论，不需要
真机、不需要原 session、不需要重新生成输入。目录里的 `REJUDGE.md` 写着那条命令。
"""

import json
import shutil
from pathlib import Path

import numpy as np

# 自动留证的触发归类（执行侧），与 exec_case.EXIT_KIND 的取值域对齐。
FAILED_KINDS = ("spec_error", "io_error", "unsupported", "acl_error",
                "op_error", "crash", "timeout", "unknown")

BUNDLE_FILES = ("case.json", "case.spec", "inputs.npz", "exec.log", "REJUDGE.md")

REJUDGE_TEMPLATE = """# 独立重判

本目录是 harness 自动留证件，自包含：不需要真机、不需要原 session、不需要重新
生成输入。重判只消费 `inputs.npz`、`dut.npz` 与 `case.json`，判定走 criteria 的
同一实现。

```bash
python3 {harness}/run_harness.py --rejudge {bundle} \\
  --gen-dir <repo-task-solver-case-gen 的 scripts 目录>
```

## 目录内容

| 文件 | 内容 |
| --- | --- |
| `case.json` | case 字段、算子 ABI、执行归类、当场判定结论、构建取证引用 |
| `case.spec` | 喂给 `harness_exec` 的逐字 spec，可直接在真机上复跑同一 case |
| `inputs.npz` | 实际喂进去的输入数组（不是 seed，是落盘的那一份） |
| `dut.npz` | 三键 `{{out32, info, status}}`；执行未产出输出时此文件缺席 |
| `out32.diff.bin` | 复跑失配轮的 out32（仅复跑失配时存在） |
| `exec.log` | 执行子进程的 stdout 与 stderr 全文 |
| `provenance.json` | S1 构建取证快照（源码 commit、命令、产物哈希、环境版本） |

## 留证原因

{reason}
"""


def three_key(out32, info, status):
    """组装三键。info 标量统一成 int64 标量数组，与包通路落盘口径一致。"""
    key = {"status": status}
    if out32 is not None:
        key["out32"] = np.asarray(out32)
    if info is not None:
        key["info"] = info if isinstance(info, np.ndarray) else np.int64(info)
    return key


def save_dut_npz(path, dut):
    """三键落 npz；status 作为 0 维字符串数组存，读回即原字符串。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": np.array(dut.get("status", "ok"))}
    for k in ("out32", "info"):
        if dut.get(k) is not None:
            payload[k] = np.asarray(dut[k])
    np.savez(path, **payload)
    return path


def load_dut_npz(path):
    """读回三键（重判入口）。"""
    with np.load(path, allow_pickle=False) as z:
        dut = {"status": str(z["status"].item()) if "status" in z else "ok"}
        for k in ("out32", "info"):
            if k in z:
                arr = z[k]
                dut[k] = np.int64(arr.item()) if arr.ndim == 0 else arr
    return dut


def should_keep(kind, numeric, determinism, keep_ids=(), case_id=None):
    """要不要留证。返回 (bool, 原因字符串)；不留证时原因为 None。"""
    if case_id is not None and case_id in keep_ids:
        return True, "人工 --keep 指定"
    if kind in FAILED_KINDS:
        return True, f"执行失败：归类 {kind}"
    if kind == "rerun_mismatch":
        return True, "复跑 bit-wise 失配"
    if determinism is not None and determinism.get("consistent") is False:
        return True, "确定性复跑失配"
    if numeric is not None and numeric != "PASS":
        return True, f"数值结论 {numeric}"
    return False, None


def export_failure(bundle_dir, case, abi, exec_rec, inputs, dut, reason,
                   provenance=None, verdict=None, harness_dir=None):
    """把一个 case 的全部现场打成可独立重判的目录，返回目录路径。"""
    bundle = Path(bundle_dir)
    bundle.mkdir(parents=True, exist_ok=True)

    np.savez(bundle / "inputs.npz",
             **{k: np.asarray(v) for k, v in inputs.items() if v is not None})
    if dut and (dut.get("out32") is not None or dut.get("info") is not None):
        save_dut_npz(bundle / "dut.npz", dut)
    (bundle / "case.spec").write_text(exec_rec.get("spec_text") or "",
                                      encoding="utf-8")
    (bundle / "exec.log").write_text(exec_rec.get("log") or "", encoding="utf-8")

    # 复跑失配轮的输出：子进程写在 case 目录里，搬进留证件才算自包含
    case_dir = Path(exec_rec.get("case_dir") or ".")
    for extra in ("out/out32.bin.diff.bin", "out/info.bin.diff.bin"):
        src = case_dir / extra
        if src.is_file():
            shutil.copyfile(src, bundle / Path(extra).name)

    if provenance is not None:
        with open(bundle / "provenance.json", "w", encoding="utf-8") as fh:
            json.dump(provenance, fh, ensure_ascii=False, indent=1, default=str)
            fh.write("\n")

    meta = {
        "case": {k: v for k, v in case.items()
                 if not isinstance(v, np.ndarray)},
        "abi": abi.to_dict() if hasattr(abi, "to_dict") else abi,
        "reason": reason,
        "exec": {k: v for k, v in exec_rec.items()
                 if k not in ("out32", "info", "log", "spec_text")},
        "verdict": verdict,
        "inputs": {k: {"shape": list(np.asarray(v).shape),
                       "dtype": str(np.asarray(v).dtype)}
                   for k, v in inputs.items() if v is not None},
    }
    with open(bundle / "case.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1, default=str)
        fh.write("\n")

    (bundle / "REJUDGE.md").write_text(
        REJUDGE_TEMPLATE.format(
            harness=harness_dir or Path(__file__).resolve().parent,
            bundle=bundle, reason=reason),
        encoding="utf-8")
    return bundle


def discard(*containers):
    """通过即弃：显式清空大数组容器，不等 GC。返回丢弃的数组个数。"""
    dropped = 0
    for c in containers:
        if isinstance(c, dict):
            for k in list(c):
                if isinstance(c[k], np.ndarray) and c[k].size > 0:
                    dropped += 1
                c.pop(k)
    return dropped


def bundle_is_self_contained(bundle_dir, require_dut=True):
    """留证件完整性自检：缺哪个文件就报出来，不静默。"""
    bundle = Path(bundle_dir)
    want = list(BUNDLE_FILES) + (["dut.npz"] if require_dut else [])
    missing = [f for f in want if not (bundle / f).is_file()]
    return (not missing), missing
