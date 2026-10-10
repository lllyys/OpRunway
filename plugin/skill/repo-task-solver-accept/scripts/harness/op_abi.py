# -*- coding: utf-8 -*-
"""算子 ABI 表与交付头解析（harness 的分流数据面）。

harness 对被测的全部依赖收在这一张表里：算子名 → 入口符号、调用形状、数值域、
info 形态、内存归属口径。**新增算子是加一行数据，不是加一处特判**——执行子进程
按调用形状分支，一个形状服务多个算子（现工程六算子同构，PROBE §5.1）。

两套内存归属口径正交（2026-10-09d 裁定「双口径分流」）：

- `host`——矩阵与 info 都是 Host 指针，算子内部自己 `aclrtMalloc` 与 H2D/D2H。
  现工程七个算子全是这个口径（PROBE §3.2，新基线 05e6b09 复核仍然成立）。
- `device`——调用方在 Device 上备矩阵、`devInfo` 与 `Workspace`，算子只收指针。
  任务书 §2.1–2.4 要求这一套；Cholesky 十算子尚未交付，表里记
  `status="awaiting_delivery"`，签名以交付头为准、不以任务书钉死。

交付头是唯一真相：`available_ops()` 只认头文件里真的声明了入口符号的算子，
`compile_defines()` 按此生成执行子进程的编译宏。头里没有的算子一行代码都不编进去，
所以不存在「链接期才发现算子不存在」。
"""

import re

# 调用形状：执行子进程按此分支，与算子名无关。
# - inplace_a            (handle, n, A, lda, info)                      → out32 = A
# - outofplace_a_ainv    (handle, n, A, lda, Ainv, lda_inv, info, batch) → out32 = Ainv
# - potrf_inplace        (handle, uplo, n, A, lda, info)                → out32 = A
# - potrs_b_inplace      (handle, uplo, n, nrhs, A, lda, B, ldb, info)  → out32 = B
# - inplace_a_ipiv       (handle, m, n, A, lda, ipiv, info)             → out32 = A，另有 ipiv
# - eig_a_w              (handle, jobz, uplo, n, A, lda, W, info)       → out32 = W
CALL_SHAPES = (
    "inplace_a",
    "outofplace_a_ainv",
    "potrf_inplace",
    "potrs_b_inplace",
    "inplace_a_ipiv",
    "eig_a_w",
)

# 执行子进程已实现的调用形状；其余形状的算子表里照记，调用时明确拒绝。
EXEC_SHAPES = ("inplace_a", "outofplace_a_ainv")

DTYPES = {"float32": 4, "complex64": 8}


class AbiError(RuntimeError):
    """交付头与表不符，或请求了未实现的口径——报错停下，不猜签名。"""


class OpAbi:
    """一个算子的 ABI 描述。字段全部是数据，没有行为分支。"""

    __slots__ = ("op", "entry", "call_shape", "dtype", "info_kind", "batched",
                 "abi", "status", "note")

    def __init__(self, op, entry, call_shape, dtype, info_kind, batched,
                 abi, status, note=""):
        if call_shape not in CALL_SHAPES:
            raise AbiError(f"{op}: 未登记的调用形状 {call_shape!r}")
        if dtype not in DTYPES:
            raise AbiError(f"{op}: 未登记的数值域 {dtype!r}")
        if info_kind not in ("scalar", "array"):
            raise AbiError(f"{op}: info 形态只能是 scalar/array，得到 {info_kind!r}")
        if abi not in ("host", "device"):
            raise AbiError(f"{op}: 内存归属口径只能是 host/device，得到 {abi!r}")
        if status not in ("present", "awaiting_delivery"):
            raise AbiError(f"{op}: status 只能是 present/awaiting_delivery，得到 {status!r}")
        self.op, self.entry, self.call_shape = op, entry, call_shape
        self.dtype, self.info_kind, self.batched = dtype, info_kind, batched
        self.abi, self.status, self.note = abi, status, note

    @property
    def exec_supported(self):
        """执行子进程能否真的调它：形状已实现且算子已交付。"""
        return self.call_shape in EXEC_SHAPES and self.status == "present"

    @property
    def macro(self):
        return "HARNESS_OP_" + self.op.upper()

    def itemsize(self):
        return DTYPES[self.dtype]

    def to_dict(self):
        return {"op": self.op, "entry": self.entry, "call_shape": self.call_shape,
                "dtype": self.dtype, "info_kind": self.info_kind,
                "batched": self.batched, "abi": self.abi, "status": self.status,
                "exec_supported": self.exec_supported, "note": self.note}


def _a(*args, **kw):
    a = OpAbi(*args, **kw)
    return a.op, a


# 现工程算子（基线 05e6b09 逐行复核 include/cann_ops_solver.h）。
# 七个算子全部声明在 `extern "C"` 块外且带 `std::complex<float>` 参数 → ctypes 路线
# 关闭，执行子进程必须对公开头编译（PROBE §3.1，新基线仍然成立）。
_PRESENT = dict([
    _a("cmatinv_batched", "aclsolverCmatinvBatched", "outofplace_a_ainv",
       "complex64", "scalar", True, "host", "present",
       "n≥32 转调 CgetriBatched 并仍返回成功（host:113-116 实测行为）"),
    _a("cgetri_batched", "aclsolverCgetriBatched", "outofplace_a_ainv",
       "complex64", "scalar", True, "host", "present"),
    _a("cgetri", "aclsolverCgetri", "inplace_a", "complex64", "scalar", False,
       "host", "present", "入参按注释是 Cgetrf 的 LU 因子，不是原矩阵"),
    _a("sgetri", "aclsolverSgetri", "inplace_a", "float32", "scalar", False,
       "host", "present", "入参按注释是 Sgetrf 的 LU 因子，不是原矩阵"),
    _a("cgetrf", "aclsolverCgetrf", "inplace_a_ipiv", "complex64", "scalar",
       False, "host", "present", "第三键 ipiv 未进三键契约，执行段未实现该形状"),
    _a("sgetrf", "aclsolverSgetrf", "inplace_a_ipiv", "float32", "scalar",
       False, "host", "present", "第三键 ipiv 未进三键契约，执行段未实现该形状"),
    _a("cheevj", "aclsolverCheevj", "eig_a_w", "complex64", "scalar", False,
       "host", "present", "新基线 05e6b09 新增；out32 是特征值 W，执行段未实现该形状"),
])

# Cholesky 十算子（任务书 §2.1–2.4 口径）。签名、入口符号与内存归属以**交付头**为准，
# 本表的 entry 只是按任务书命名规则的预期值，交付后按实际头核对并更正。
_AWAITING = dict([
    _a("spotrf", "aclsolverSpotrf", "potrf_inplace", "float32", "scalar", False,
       "device", "awaiting_delivery"),
    _a("spotrs", "aclsolverSpotrs", "potrs_b_inplace", "float32", "scalar", False,
       "device", "awaiting_delivery"),
    _a("spotri", "aclsolverSpotri", "potrf_inplace", "float32", "scalar", False,
       "device", "awaiting_delivery"),
    _a("cpotrf", "aclsolverCpotrf", "potrf_inplace", "complex64", "scalar", False,
       "device", "awaiting_delivery"),
    _a("cpotrs", "aclsolverCpotrs", "potrs_b_inplace", "complex64", "scalar", False,
       "device", "awaiting_delivery"),
    _a("cpotri", "aclsolverCpotri", "potrf_inplace", "complex64", "scalar", False,
       "device", "awaiting_delivery"),
    _a("spotrfBatched", "aclsolverSpotrfBatched", "potrf_inplace", "float32",
       "array", True, "device", "awaiting_delivery"),
    _a("spotrsBatched", "aclsolverSpotrsBatched", "potrs_b_inplace", "float32",
       "scalar", True, "device", "awaiting_delivery"),
    _a("cpotrfBatched", "aclsolverCpotrfBatched", "potrf_inplace", "complex64",
       "array", True, "device", "awaiting_delivery"),
    _a("cpotrsBatched", "aclsolverCpotrsBatched", "potrs_b_inplace", "complex64",
       "scalar", True, "device", "awaiting_delivery"),
])

TABLE = dict(_PRESENT)
TABLE.update(_AWAITING)

# 三键契约的 out32 来源（SKILL.md 入口参数表「out32、info、status 三键」）：
# potrf/potri 族取原地覆盖后的 A，potrs 族取 B，求逆族取 Ainv。
OUT32_SOURCE = {
    "inplace_a": "A",
    "outofplace_a_ainv": "Ainv",
    "potrf_inplace": "A",
    "potrs_b_inplace": "B",
    "inplace_a_ipiv": "A",
    "eig_a_w": "W",
}


def get(op):
    try:
        return TABLE[op]
    except KeyError:
        raise AbiError(
            f"算子 {op!r} 不在 ABI 表内；已登记：{', '.join(sorted(TABLE))}") from None


def header_entries(header_text):
    """从公开头文本里取出所有 `aclsolver*` 入口符号名。

    只认函数声明形态（符号名后紧跟左括号），注释与文档块里的同名提及不计入——
    头文件的 doxygen 注释大量提到入口名（新基线 05e6b09 的 `@return` 段即是）。
    """
    stripped = re.sub(r"/\*.*?\*/", " ", header_text, flags=re.S)
    stripped = re.sub(r"//[^\n]*", " ", stripped)
    return set(re.findall(r"\b(aclsolver[A-Za-z0-9_]*)\s*\(", stripped))


def available_ops(header_text, table=None):
    """交付头里真的声明了入口的算子子集：{op: OpAbi}。"""
    entries = header_entries(header_text)
    tbl = TABLE if table is None else table
    return {op: a for op, a in tbl.items() if a.entry in entries}


def compile_defines(ops):
    """执行子进程的编译宏：每个可用算子一个 `-DHARNESS_OP_<OP>=1`。"""
    return [f"-D{get(op).macro}=1" for op in sorted(ops)]


def require_exec(op):
    """执行段真要调它之前的准入；不满足就报清楚缺什么，不静默降级。"""
    a = get(op)
    if a.status != "present":
        raise AbiError(
            f"{op}: 尚未交付（status={a.status}）。交付后按交付头核对三件事再开用——"
            f"入口符号是否为 {a.entry}、声明是否落在 extern \"C\" 块内、"
            f"矩阵/info/workspace 的内存归属是 {a.abi} 还是 host")
    if a.call_shape not in EXEC_SHAPES:
        raise AbiError(
            f"{op}: 调用形状 {a.call_shape} 未进执行段（已实现 {', '.join(EXEC_SHAPES)}）。"
            f"{a.note or '补该形状需在 harness_exec.cpp 加一个 call_shape 分支'}")
    return a
