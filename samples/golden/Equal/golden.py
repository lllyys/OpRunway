"""OpRunway 精度 golden 样例 · Equal —— 只读参考 / 生成器骨架种子（非引擎组件、非运行时回退靶）。

引擎按算子从用户侧 `<ops_root>/<op>/golden.py` 加载；本样例迁自引擎内置 `GOLDEN` 表（ADR 0011）。
须导出 `golden_fn(inputs, attrs) -> ndarray` + `GOLDEN_SOURCE` + `GOLDEN_PROVENANCE`。后端恒 CPU torch（决策 4）。
"""
import numpy as np

GOLDEN_SOURCE = "torch torch.eq"
GOLDEN_PROVENANCE = "任务书指定「二进制→对齐 CPU 逻辑比较」→ torch.eq(CPU)；语义改造非自撰，见 Equal 任务书 reference"


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
    a = t.from_numpy(np.ascontiguousarray(inputs[0]))
    b = t.from_numpy(np.ascontiguousarray(inputs[1]))
    return np.ascontiguousarray(t.eq(a, b).numpy())
