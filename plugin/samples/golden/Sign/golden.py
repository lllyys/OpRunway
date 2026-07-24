"""OpRunway 精度 golden 样例 · Sign —— 只读参考 / 生成器骨架种子（非引擎组件、非运行时回退靶）。

引擎按算子从用户侧 `<ops_root>/<op>/golden.py` 加载；本样例迁自引擎内置 `GOLDEN` 表（ADR 0011）。
须导出 `golden_fn(inputs, attrs) -> ndarray` + `GOLDEN_SOURCE` + `GOLDEN_PROVENANCE`。

⚠ **本文件的措辞会被后续 agent 照抄**去产新算子的 golden.py —— `GOLDEN_PROVENANCE` 必须逐字属实、不许含糊。
四份样例（IsClose/Equal/Sign/Neg）共用同一套判据，出自用户 2026-07-22 裁定：**两档链（R3）**① 任务书指定的
真值口径 → ② CPU 上的 torch/numpy 现成 API；**PR 里的参考实现一律禁止作 golden 源（R2）**；内置 TBE / 仓自带
参考只是 **impl_reference**（「照着谁重写」≠「真值该怎么算」）、**不构成授权** → 只能落第二档。统一句式：
  第一档 → "第一档（tier 1）·任务书指定真值口径（<原句摘要>）→ <backend>.<api>(CPU)"
  第二档 → "第二档（tier 2）·任务书未指定真值口径（仅 <impl_reference 内容>）→ 回落 CPU 现成 API <backend>.<api>"
档位判定的**唯一**实现是 `precision_policy.derive_golden_tier`；此处只抄录判定结果，不复述其逻辑。

⚠ **本文件曾是错措辞的错源**：旧 `GOLDEN_PROVENANCE` 写「任务书指定纯重写」，而 Sign 任务书**一字未提**
torch / numpy / 公式，只说「参考内置 TBE 实现」——那是 impl_reference、不构成授权。**golden 函数本身没错**
（第二档回落 torch.sign 正当、不需人核，R5 一级：现成 API 单调），错的只是把回落说成了「任务书指定」。

后端（ADR 0011 决策 4 · 本轮裁定 R6「torch 优先、numpy 兜底，**生成期**选型并写死」）：golden 恒 CPU，本样例
生成期已选定 torch；**运行时**不兜底——torch 缺失即 fail-closed（确定性红线）。
"""
import numpy as np

GOLDEN_SOURCE = "torch torch.sign"
# 判档依据（Sign 任务书原文，通篇只有这两句涉及「参考谁」，均无真值口径指定）：
#   正文    「参考昇腾版本内置Sign算子的 TBE 实现，……实现功能一致的算子」
#   功能要求「与原 TBE 算子核心功能完全对齐……并增加对int16的支持」
# 二者皆 authorization.kind = impl_reference（照着谁重写），**不构成 golden 授权** → 第二档回落，非第一档。
GOLDEN_PROVENANCE = (
    "第二档（tier 2）·任务书未指定真值口径"
    "（Sign 任务书仅给参考实现出处：「参考昇腾版本内置Sign算子的 TBE 实现」+「增加对int16的支持」，"
    "属 impl_reference、不构成 golden 授权）"
    "→ 回落 CPU 现成 API torch.sign；单 API 单调、按 R5 不需人核。"
    "边界：torch.sign(NaN)=0，与 np.sign(NaN)=NaN 不同——后端于生成期选定 torch 并写死，运行时不兜底"
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
    return np.ascontiguousarray(t.sign(t.from_numpy(np.ascontiguousarray(inputs[0]))).numpy())
