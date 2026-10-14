# -*- coding: utf-8 -*-
"""ABI 表与交付头解析的机械断言。

覆盖清单 → 测试对照：

- doxygen 注释里的同名提及不算声明 → test_header_entries_ignores_comments
- 交付头里没有的算子不进可用集与编译宏 → test_available_ops_follows_header、
  test_compile_defines_shape
- 未交付算子的准入拒绝要指名三件待核事项 → test_require_exec_awaiting_names_checks
- 已交付但形状未实现的算子照样拒绝 → test_require_exec_unimplemented_shape
- 表字段自校验（形状/数值域/info 形态/口径/状态） → test_table_field_validation
- 每个调用形状都登记了 out32 来源 → test_out32_source_covers_every_shape
- 新基线 05e6b09 的七个现工程算子齐备且全为 host 口径 → test_present_ops_are_host_abi
"""
import pathlib
import sys

import pytest

HARNESS = pathlib.Path(__file__).resolve().parent.parent
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import op_abi  # noqa: E402

# 逐字摘自 include/cann_ops_solver.h（基线 05e6b09）的形态：注释里反复提到入口名，
# 真正的声明只有块外那两行。
HEADER_SNIPPET = '''
#ifdef __cplusplus
extern "C"
{
#endif
    /**
     * @brief Create an aclsolver handle
     * Allocates a handle. Used with aclsolverDestroy and aclsolverSetStream.
     * @return ACLSOLVER_STATUS_SUCCESS success
     */
    aclsolverStatus_t aclsolverCreate(aclsolverHandle_t *handle);
#ifdef __cplusplus
}
#endif

// aclsolverCgetriBatched 在注释里被提到，但本头没有声明它
/**
 * @brief Batched complex matrix inversion for small matrices (n < 32)
 * When n >= 32, the call is forwarded to aclsolverCgetriBatched semantics.
 */
aclError aclsolverCmatinvBatched(aclsolverHandle_t handle, const int64_t n,
                                 std::complex<float> *A, const int64_t lda,
                                 std::complex<float> *Ainv, const int64_t lda_inv,
                                 int32_t *info, int64_t batchSize);
aclError aclsolverSgetri(aclsolverHandle_t handle, const int64_t n, float *A,
                         const int64_t lda, int32_t *info);
'''


def test_header_entries_ignores_comments():
    entries = op_abi.header_entries(HEADER_SNIPPET)
    assert {"aclsolverCreate", "aclsolverCmatinvBatched", "aclsolverSgetri"} == entries
    # 注释里提到的 Destroy/SetStream/CgetriBatched 一个都不许进来
    assert "aclsolverCgetriBatched" not in entries
    assert "aclsolverDestroy" not in entries


def test_available_ops_follows_header():
    avail = op_abi.available_ops(HEADER_SNIPPET)
    assert sorted(avail) == ["cmatinv_batched", "sgetri"]
    assert avail["cmatinv_batched"].call_shape == "outofplace_a_ainv"
    assert avail["sgetri"].dtype == "float32"


def test_compile_defines_shape():
    assert op_abi.compile_defines(["cmatinv_batched", "sgetri"]) == [
        "-DHARNESS_OP_CMATINV_BATCHED=1", "-DHARNESS_OP_SGETRI=1"]


def test_require_exec_awaiting_names_checks():
    with pytest.raises(op_abi.AbiError) as exc:
        op_abi.require_exec("spotrf")
    msg = str(exc.value)
    # 拒绝时必须指名待核的三件事，否则交付到手还要再翻一遍设计文档
    assert "aclsolverSpotrf" in msg
    assert 'extern "C"' in msg
    assert "device" in msg


def test_require_exec_unimplemented_shape():
    # cgetrf 已交付（present），但 inplace_a_ipiv 形状没进执行段
    with pytest.raises(op_abi.AbiError) as exc:
        op_abi.require_exec("cgetrf")
    assert "inplace_a_ipiv" in str(exc.value)
    assert op_abi.get("cgetrf").status == "present"
    assert op_abi.get("cgetrf").exec_supported is False


def test_require_exec_passes_for_implemented():
    a = op_abi.require_exec("cmatinv_batched")
    assert a.exec_supported is True
    assert a.macro == "HARNESS_OP_CMATINV_BATCHED"
    assert a.itemsize() == 8


def test_unknown_op_raises():
    with pytest.raises(op_abi.AbiError):
        op_abi.get("nosuchop")


@pytest.mark.parametrize("bad", [
    {"call_shape": "nope"}, {"dtype": "float64"}, {"info_kind": "vector"},
    {"abi": "managed"}, {"status": "maybe"},
])
def test_table_field_validation(bad):
    kw = {"op": "x", "entry": "aclsolverX", "call_shape": "inplace_a",
          "dtype": "float32", "info_kind": "scalar", "batched": False,
          "abi": "host", "status": "present"}
    kw.update(bad)
    with pytest.raises(op_abi.AbiError):
        op_abi.OpAbi(**kw)


def test_out32_source_covers_every_shape():
    assert set(op_abi.OUT32_SOURCE) == set(op_abi.CALL_SHAPES)


def test_present_ops_are_host_abi():
    present = {op: a for op, a in op_abi.TABLE.items() if a.status == "present"}
    # 基线 05e6b09 的七个算子（含新增 cheevj）
    assert sorted(present) == ["cgetrf", "cgetri", "cgetri_batched", "cheevj",
                               "cmatinv_batched", "sgetrf", "sgetri"]
    assert all(a.abi == "host" for a in present.values())
    # Cholesky 十算子全部按任务书记 device 口径且待交付
    awaiting = {op: a for op, a in op_abi.TABLE.items()
                if a.status == "awaiting_delivery"}
    assert len(awaiting) == 10
    assert all(a.abi == "device" for a in awaiting.values())
