"""aclnn_runtime — ctypes-ACL 单算子 runner form（torch-对标验收场景的核心可搬件）。

本子包提供一条 **op-中立** 的 aclnn 两段式（``aclnn<Op>GetWorkspaceSize`` / ``aclnn<Op>``）
单算子执行通路：不需 per-op runner 源、不需 in-tree torch_npu build，直接经 ACL 单算子 C API
（ctypes）调「已 build install 的 custom 算子」或「CANN 内置 aclnn 算子」。

模块划分
--------
- ``base``          : 异常基类 ``AclnnRunnerError`` + ``InvocationResult``（原样 adapt 参考仓）。
- ``acl_consts``    : ACL 枚举 / 常量**单一真源**（dtype / format / memcpy / malloc / repeat_init），
                      逐条带 provenance，bf16 枚举标 9.0.1 核验 TODO。
- ``aclnn_runner``  : 纯 helper（header 解析 / stride / dtype 映射 / bf16 位窄化）+ ``AclnnRunner``
                      （多输出、任意 dtype 的 ctypes 执行体）。**从 header 正则推 arity，绝不按算子名分派。**
- ``aclnn_driver``  : 容器内执行的驱动脚本——读 caseset + 各 case storage .bin → 逐 case 调
                      ``AclnnRunner.run`` → 落 ``out_k.bin``。**只产原始输出、绝不判定**
                      （判定唯一归 OpRunway 确定性脚本链，ADR 0007）。

泛化边界（律令#0）：本子包的一切分派据 **spec / caseset / header 字段**（scenario / runner_form /
out_role / 输出个数 / dtype），代码里**绝不出现按算子名的分支**。median 只是首程见证，换任意「域内」
aclnn 算子（无状态、标准两段式、无 opaque descriptor）工具零改即跑。

纯 helper（header 解析 / dtype 映射 / stride / bf16 窄化）**无 CANN 依赖、可离线单测**；
``AclnnRunner`` 的 ctypes 执行路径与 ``aclnn_driver`` 的真机跑测须 NPU 容器。
"""

from .base import AclnnRunnerError, InvocationResult

__all__ = ["AclnnRunnerError", "InvocationResult"]
