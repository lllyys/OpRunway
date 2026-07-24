"""异常基类 + 调用结果容器（adapt 参考仓 cannbot-ops-input adapters/base.py）。

搬法：**原样搬、异常类改名** ``AdapterError -> AclnnRunnerError``（OpRunway 命名空间自洽，
避免与 repo_adapter 既有语义撞名）。纯 stdlib，零 CANN 依赖，可离线 import。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class AclnnRunnerError(RuntimeError):
    """aclnn 单算子的 build 或 ctypes 调用失败。"""


@dataclass
class InvocationResult:
    outputs: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
