"""ACL 枚举 / 常量——**单一真源**（提自参考仓 aclnn_runner.py:27-36）。

原参考仓把这些散在 runner 顶部；本文件把它们抽成一处 SSOT，逐条带 provenance，供
``aclnn_runner`` / ``aclnn_driver`` 共用，避免同一枚举在多处各写一份漂移。

✅ ABI 核验（WI-B4，2026-07-24 a3 容器只读核实）：下列枚举**已逐条对 CANN 9.0.1 核过**——
``aclDataType`` 在 ``acl/acl_base_rt.h``（不在 acl_base.h，注意）：BF16=27（:156）、FLOAT=0、
FLOAT16=1、INT8=2、INT32=3、UINT8=4、INT16=6、INT64=9、DOUBLE=11、BOOL=12；``aclFormat`` 同文件
ND=2；``aclrtMemcpyKind`` 在 ``acl/acl_rt.h`` H2D=1/D2H=2；``aclrtMemMallocPolicy`` HUGE_FIRST=0。
其它 CANN 版本上仍应重核（ABI 通常稳定但不假定）。详见 doc/oprunway-torch-baseline-design.md §9.1。
"""

from __future__ import annotations

from .base import AclnnRunnerError

# ── aclDataType 枚举 ──────────────────────────────────────────────────────────
#   provenance: acl/acl_base.h（enum aclDataType）。key = numpy dtype 名，value = ACL 枚举值。
#   int64/int32/int8/uint8 齐；median 的 indices 输出用 int64=9。
ACL_DTYPES = {
    "float32": 0,      # ACL_FLOAT
    "float16": 1,      # ACL_FLOAT16
    "int8": 2,         # ACL_INT8
    "int32": 3,        # ACL_INT32
    "uint8": 4,        # ACL_UINT8
    "int16": 6,        # ACL_INT16
    "uint16": 7,       # ACL_UINT16
    "uint32": 8,       # ACL_UINT32
    "int64": 9,        # ACL_INT64
    "uint64": 10,      # ACL_UINT64
    "float64": 11,     # ACL_DOUBLE
    "bool": 12,        # ACL_BOOL
    "complex64": 16,   # ACL_COMPLEX64
    "complex128": 17,  # ACL_COMPLEX128
    # bfloat16 = 27 —— 2026-07-24 a3 容器核实：CANN 9.0.1 acl/acl_base_rt.h:156 ACL_BF16=27 ✓。
    "bfloat16": 27,    # ACL_BF16（9.0.1 已核）
}

# ── aclFormat ────────────────────────────────────────────────────────────────
#   provenance: acl/acl_base.h（enum aclFormat）。本 runner 只用 ND（域内假设：无特殊 layout）。
ACL_FORMAT_ND = 2  # ACL_FORMAT_ND

# ── aclrtMemcpyKind ──────────────────────────────────────────────────────────
#   provenance: acl/acl_rt.h（enum aclrtMemcpyKind）。
MEMCPY_H2D = 1  # ACL_MEMCPY_HOST_TO_DEVICE
MEMCPY_D2H = 2  # ACL_MEMCPY_DEVICE_TO_HOST

# ── aclrtMemMallocPolicy ─────────────────────────────────────────────────────
#   provenance: acl/acl_rt.h（enum aclrtMemMallocPolicy）。
MALLOC_HUGE_FIRST = 0  # ACL_MEM_MALLOC_HUGE_FIRST

# ── ACL 错误码：重复 aclInit（无害，进程内幂等 init 时容忍）───────────────────────
#   provenance: acl/acl_base.h（ACL_ERROR_REPEAT_INITIALIZE）。
REPEAT_INITIALIZE = 100002


def acl_dtype(name: str) -> int:
    """numpy dtype 名 -> ACL 枚举；不识别的 dtype fail-closed（绝不静默塞默认）。"""
    try:
        return ACL_DTYPES[name]
    except KeyError as exc:
        raise AclnnRunnerError(f"unsupported aclnn dtype: {name!r}") from exc
