"""OpRunway 精度 golden 样例 · IsClose —— 只读参考 / 生成器骨架种子（非引擎组件、非运行时回退靶）。

引擎（`gen_cases.load_golden`）按算子从用户侧 `<ops_root>/<op>/golden.py` 加载 golden；本样例迁自引擎内置
`GOLDEN` 表（ADR 0011：golden 去引擎化）。golden.py 须导出 `golden_fn(inputs, attrs) -> ndarray` +
`GOLDEN_SOURCE`（供 oracle_source 映射的来源串）+ `GOLDEN_PROVENANCE`（来源出处）。

后端（ADR 0011 决策 4）：golden 恒 CPU、torch 优先——IsClose 用 `torch.isclose`；torch 缺失 → fail-closed（不静默回退，确定性红线）。
"""
import math

import numpy as np

GOLDEN_SOURCE = "torch torch.isclose"      # 供 oracle_source 映射（首 token torch → torch_ref）
GOLDEN_PROVENANCE = "任务书指定「二进制→对齐 CPU 逻辑比较」→ torch.isclose(CPU)；语义改造非自撰，见 IsClose 任务书 reference"


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
