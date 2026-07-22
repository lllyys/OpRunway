"""OpRunway 精度 golden 样例 · IsClose —— 只读参考 / 生成器骨架种子（非引擎组件、非运行时回退靶）。

引擎（`gen_cases.load_golden`）按算子从用户侧 `<ops_root>/<op>/golden.py` 加载 golden；本样例迁自引擎内置
`GOLDEN` 表（ADR 0011：golden 去引擎化）。golden.py 须导出 `golden_fn(inputs, attrs) -> ndarray` +
`GOLDEN_SOURCE`（供 oracle_source 映射的来源串）+ `GOLDEN_PROVENANCE`（来源出处）。

⚠ **本文件的措辞会被后续 agent 照抄**去产新算子的 golden.py —— `GOLDEN_PROVENANCE` 必须逐字属实、不许含糊
（含糊一份，抄错一片）。四份样例（IsClose/Equal/Sign/Neg）共用同一套判据，出自用户 2026-07-22 裁定：
  · **两档链（R3）**：① 任务书指定的真值口径 → ② CPU 上的 torch/numpy 现成 API。
  · **PR 里的参考实现一律禁止作 golden 源（R2）**——落地方式是受控词表里根本没有那个格子。
  · 内置 TBE / 仓自带参考之类的「参考实现」只是 **impl_reference**：说的是「照着谁重写」、不是「真值该怎么算」，
    **不构成 golden 授权** → 只能落第二档。
统一句式（四份必须同款）：
  第一档 → "第一档（tier 1）·任务书指定真值口径（<原句摘要>）→ <backend>.<api>(CPU)"
  第二档 → "第二档（tier 2）·任务书未指定真值口径（仅 <impl_reference 内容>）→ 回落 CPU 现成 API <backend>.<api>"
档位判定的**唯一**实现是 `precision_policy.derive_golden_tier`；此处只抄录本样例的判定结果，不复述其逻辑。

后端（ADR 0011 决策 4 · 本轮裁定 R6「torch 优先、numpy 兜底，**生成期**选型并写死进文件」）：golden 恒 CPU，
本样例生成期已选定 torch（`torch.isclose`）；**运行时**不兜底——torch 缺失即 fail-closed（确定性红线）。
"""
import math

import numpy as np

GOLDEN_SOURCE = "torch torch.isclose"      # 供 oracle_source 映射（首 token torch → torch_ref）
# 判档依据（IsClose 任务书原文，两处同款语义改造要求）：
#   正文    「实现方式从原来比较二进制的实现方式，更改成和cpu一致的比较逻辑值的实现方式」
#   功能要求「比较方式从二进制比较改为逻辑值比较」
# 这是任务书**就真值口径本身**作出的指定（authorization.kind = oracle_method）→ 第一档，非回落。
# ⚠ 诚实边界（2026-07-22）：这里的 tier 1 是**快照就位后的目标档位**，今天机器上核不出来——
#   R12「任务书全文快照入库」属批 4、**尚未做**（全仓 find task_doc.snapshot* = 0 个）。在快照落地前
#   `precision_policy.verify_authorization` 必返 False，`derive_golden_tier` 会按规则 ② 判
#   **tier 4 · unverifiable_authorization**（假授权不降级、直接 blocked）。对照 Sign/Neg 走
#   impl_reference → 规则 ⑦ → tier 2，今天就机器自洽。引文本身已逐字核对属实。
GOLDEN_PROVENANCE = (
    "第一档（tier 1）·任务书指定真值口径"
    "（IsClose 任务书：「实现方式从原来比较二进制的实现方式，更改成和cpu一致的比较逻辑值的实现方式」；"
    "功能实现要求同款「比较方式从二进制比较改为逻辑值比较」）"
    "→ torch.isclose(CPU)"
)


def _require_torch():
    try:
        import torch
        return torch
    except Exception as e:                 # noqa: BLE001 —— 缺失/损坏一律要求安装、不静默兜底
        raise RuntimeError(
            "golden 需 torch(CPU) 作 CPU 标杆参考、但未安装/不可用。请安装 CPU 版："
            "pip install torch --index-url https://download.pytorch.org/whl/cpu。"
            "不静默回退——确定性红线（ADR 0011 决策 4）。") from e


def golden_fn(inputs, attrs):
    t = _require_torch()
    rtol, atol = float(attrs["rtol"]), float(attrs["atol"])
    if not (math.isfinite(rtol) and math.isfinite(atol) and rtol >= 0 and atol >= 0):
        raise ValueError(f"IsClose golden: rtol/atol 须有限非负，得 rtol={rtol} atol={atol}")
    a = t.from_numpy(np.ascontiguousarray(inputs[0]))
    b = t.from_numpy(np.ascontiguousarray(inputs[1]))
    r = t.isclose(a, b, rtol=rtol, atol=atol, equal_nan=bool(attrs["equal_nan"]))
    return np.ascontiguousarray(r.numpy())              # bool 输出
