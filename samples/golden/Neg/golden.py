"""OpRunway 精度 golden 样例 · Neg —— 只读参考 / 生成器骨架种子（非引擎组件、非运行时回退靶）。

引擎按算子从用户侧 `<ops_root>/<op>/golden.py` 加载；本样例迁自引擎内置 `GOLDEN` 表（ADR 0011）。
须导出 `golden_fn(inputs, attrs) -> ndarray` + `GOLDEN_SOURCE` + `GOLDEN_PROVENANCE`。

⚠ **本文件的措辞会被后续 agent 照抄**去产新算子的 golden.py —— `GOLDEN_PROVENANCE` 必须逐字属实、不许含糊。
四份样例（IsClose/Equal/Sign/Neg）共用同一套判据，出自用户 2026-07-22 裁定：**两档链（R3）**① 任务书指定的
真值口径 → ② CPU 上的 torch/numpy 现成 API；**PR 里的参考实现一律禁止作 golden 源（R2）**；内置 TBE / 仓自带
参考只是 **impl_reference**（「照着谁重写」≠「真值该怎么算」）、**不构成授权** → 只能落第二档。统一句式：
  第一档 → "第一档（tier 1）·任务书指定真值口径（<原句摘要>）→ <backend>.<api>(CPU)"
  第二档 → "第二档（tier 2）·任务书未指定真值口径（仅 <impl_reference 内容>）→ 回落 CPU 现成 API <backend>.<api>"
档位判定的**唯一**实现是 `precision_policy.derive_golden_tier`；此处只抄录判定结果，不复述其逻辑。

⚠ **Neg 是「授权与实测 dtype 错位」的样板，别照着含糊成「任务书指定」**：Neg 任务书确实点名了 `torch.neg`，
但那句**只覆盖 uint8**；而本样例实测 dtype 是 {float32, float16}、**不在点名范围内** → 该指定对本样例不生效。
按本轮**保守默认**（档位 per-op、同算子多 dtype 档位不一时取最保守档；**可推翻**，per-dtype 扩展留待需要）
→ 整体落第二档。uint8 回绕 / int64 溢出 / int-min 取负本轮 out-of-scope（见 neg.spec 的 task_pr_gaps）。

后端（ADR 0011 决策 4 · 本轮裁定 R6「torch 优先、numpy 兜底，**生成期**选型并写死」）：golden 恒 CPU，本样例
生成期已选定 torch；**运行时**不兜底——torch 缺失即 fail-closed（确定性红线）。
"""
import numpy as np

GOLDEN_SOURCE = "torch torch.neg"
# 判档依据（Neg 任务书原文）：
#   正文    「参考昇腾版本内置neg算子的 TBE 实现，……实现功能一致的算子」  → impl_reference，不构成授权
#   功能要求「特别地需要支持int16、uint8、int64类型输入。说明：当输入类型为 uint8时，其行为和`torch.neg`一致，
#            `torch.neg(uint8)` ……其值等于 `256 - x`……即发生“回绕”」   → 唯一点名 torch.neg 处，**限定 uint8**
# 本样例实测 dtype = {float32, float16}（samples/specs/neg.spec.json 的 params[].dtype），不落在 uint8 上，
# 故该点名对本样例不生效 → 按 per-op 取最保守档，整体归第二档回落（single_api、按 R5 不需人核）。
GOLDEN_PROVENANCE = (
    "第二档（tier 2）·任务书未就本样例实测 dtype 指定真值口径"
    "（Neg 任务书正文仅「参考昇腾版本内置neg算子的 TBE 实现」= impl_reference、不构成授权；"
    "其唯一点名 torch.neg 之处限定 uint8——「当输入类型为 uint8时，其行为和 torch.neg 一致」回绕 256-x，"
    "而本样例实测 dtype 为 float32/float16、不在该点名范围内，指定对本样例不生效）"
    "→ 回落 CPU 现成 API torch.neg；单 API 单调、按 R5 不需人核。"
    "档位按本轮保守默认（per-op 取最保守档，可推翻）；uint8 回绕本轮 out-of-scope"
)


def _require_torch():
    try:
        import torch
        return torch
    except Exception as e:                 # noqa: BLE001
        raise RuntimeError(
            "golden 需 torch(CPU) 作 CPU 标杆参考、但未安装/不可用。请安装 CPU 版："
            "pip install torch --index-url https://download.pytorch.org/whl/cpu。"
            "不静默回退——确定性红线（ADR 0011 决策 4）。") from e


def golden_fn(inputs, attrs):
    t = _require_torch()
    return np.ascontiguousarray(t.neg(t.from_numpy(np.ascontiguousarray(inputs[0]))).numpy())
