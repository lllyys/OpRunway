"""离线单测 aclnn_runtime 子包（ctypes 打桩/mock，**不需真机**）。

覆盖：
  · parse_aclnn_signature —— 完整有序形参表（tensor-in/out + **穿插的标量属性** int64/bool/float32/float64/
    aclScalar）；aclTensorList 域外 fail-closed；末两框架参显式校验（截断头 fail-closed）；单输出向后兼容；
  · contiguous_strides / acl_dtype 覆盖 int64/int32/int8/uint8/bf16；bf16 位窄化字节数 + round-trip；
  · _find_custom_opapi_libs **可选**（无 ASCEND_OPP_PATH / 无 lib → [] 不 raise，Bug#1）；
  · _resolve_symbol **custom vendor 优先**（Bug#A 假 PASS）+ handle 顺序即优先级 + 严格档
    require_custom_vendor fail-closed + 宽松档退全局 + 哪都没有 raise；provenance 逐符号记
    source/lib/address/global_conflict，runtime_provenance() 结构齐全（symbols 按名排序）；
  · stream 生命周期（Bug#C）：外部注入不销毁、自建才销毁 + reset、close 幂等、context-manager、
    aclFinalize 仅 finalize=True 才调、_as_stream 类型闸；
  · AclnnRunner.run(op, slots, signature=...) —— 签名**必传**、arity/名字/ctype 全对账才进 native；
    有序 slots 拼 argtypes（median 1in+2attr+2out 穿插顺序正确）、out_null→NULL 不产出、bf16 输入窄化 +
    输出展宽、attr marshal（C float vs double 分开）、0-d 输入保 []、物理 dtype≠声明 dtype 拒、
    **每个 native 失败点都释放资源**；
  · aclnn_driver.run_driver 执行**逐 case 已解析的 aclnn_call** + 落 out_k.bin（out_null、缺 aclnn_call /
    缺属性值 / 下标错 一律 fail-closed；签名取不到 fail-closed）；
  · aclnn_driver CLI 的 **DUT 声明透传**（改动⑪）：严格档没给 → 报错且提示 `--dut-lib` /
    `--dut-vendor-root`、`--allow-builtin-symbols`；`--dut-vendor-root` 推出 so 路径；manifest 的
    `runtime` 落**整份** provenance（`is_dut` / `dut_lib` / `ignored_custom_opapi_libs` /
    `device_owned` / `teardown`），close 之后回写 `teardown`。

只跑本文件：``pytest plugin/acc-common/test_aclnn_runtime.py -q``。
"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from aclnn_runtime import acl_consts
from aclnn_runtime import aclnn_driver
from aclnn_runtime import aclnn_runner as R
from aclnn_runtime.base import AclnnRunnerError


# ── header 解析（完整有序形参表，含穿插标量属性）───────────────────────────────────

_MEDIAN_HEADER = """
#ifndef ACLNN_MEDIAN_H_
#define ACLNN_MEDIAN_H_
#include "aclnn/acl_meta.h"

__attribute__((visibility("default")))
aclnnStatus aclnnMedianGetWorkspaceSize(const aclTensor *self, int64_t dim, bool keepdim,
                                        aclTensor *valuesOut, aclTensor *indicesOut,
                                        uint64_t *workspaceSize, aclOpExecutor **executor);

__attribute__((visibility("default")))
aclnnStatus aclnnMedian(void *workspace, uint64_t workspaceSize, aclOpExecutor *executor,
                        aclrtStream stream);
#endif
"""


def test_parse_signature_median_arity():
    sig = R.parse_aclnn_signature(_MEDIAN_HEADER)
    assert sig.op_name == "Median"
    assert sig.num_inputs == 1
    assert sig.num_outputs == 2
    assert sig.tensor_count == 3
    assert sig.input_names == ["self"]
    assert sig.output_names == ["valuesOut", "indicesOut"]
    # 顺序保真（向后兼容视图）：签名里 self 在前、两 out 在后。
    assert [p["io"] for p in sig.tensor_params] == ["in", "out", "out"]


def test_parse_signature_full_ordered_params_with_scalars():
    """完整有序形参表：dim(int64)/keepdim(bool) **穿插**在 self 与 valuesOut 之间（旧版丢了它们→段错误）。"""
    sig = R.parse_aclnn_signature(_MEDIAN_HEADER)
    assert [(p["role"], p["ctype"]) for p in sig.params] == [
        ("in", "tensor"), ("attr", "int64"), ("attr", "bool"),
        ("out", "tensor"), ("out", "tensor"),
    ]
    assert [p["name"] for p in sig.params] == ["self", "dim", "keepdim", "valuesOut", "indicesOut"]
    # workspaceSize / executor 两框架参**不计入**。
    assert all(p["name"] not in ("workspaceSize", "executor") for p in sig.params)
    assert [p["ctype"] for p in sig.attr_params] == ["int64", "bool"]


def test_parse_signature_float_and_scalar_attrs():
    """audit#5：C `double` → float64、C `float` → float32（**分开**，位宽不同不能合并）。"""
    header = ("aclnnStatus aclnnFooGetWorkspaceSize(const aclTensor *self, double alpha, "
              "float beta, const aclScalar *gamma, aclTensor *out, "
              "uint64_t *workspaceSize, aclOpExecutor **executor);")
    sig = R.parse_aclnn_signature(header)
    assert [(p["role"], p["ctype"]) for p in sig.params] == [
        ("in", "tensor"), ("attr", "float64"), ("attr", "float32"),
        ("attr", "scalar"), ("out", "tensor")]


def test_parse_signature_truncated_header_fail_closed():
    """audit#7：形参表没闭合右括号（头被截断）→ 立即 raise，不拿文件末尾硬凑签名。"""
    with pytest.raises(AclnnRunnerError):
        R.parse_aclnn_signature(
            "aclnnStatus aclnnFooGetWorkspaceSize(const aclTensor *self, aclTensor *out,")


def test_parse_signature_missing_framework_tail_fail_closed():
    """audit#7：末两形参不是 `uint64_t*` + `aclOpExecutor**` → fail-closed。"""
    with pytest.raises(AclnnRunnerError):
        R.parse_aclnn_signature(
            "aclnnStatus aclnnFooGetWorkspaceSize(const aclTensor *self, aclTensor *out);")
    with pytest.raises(AclnnRunnerError):   # 顺序颠倒
        R.parse_aclnn_signature(
            "aclnnStatus aclnnFooGetWorkspaceSize(const aclTensor *self, aclTensor *out, "
            "aclOpExecutor **executor, uint64_t *workspaceSize);")


def test_parse_signature_duplicate_framework_param_fail_closed():
    """audit#7：框架参在算子实参位置又出现一次（不唯一）→ fail-closed。"""
    with pytest.raises(AclnnRunnerError):
        R.parse_aclnn_signature(
            "aclnnStatus aclnnFooGetWorkspaceSize(const aclTensor *self, uint64_t *extra, "
            "aclTensor *out, uint64_t *workspaceSize, aclOpExecutor **executor);")


def test_parse_signature_raw_pointer_param_fail_closed():
    """裸指针形参（非 aclTensor/aclScalar）属域外接口形态 → fail-closed，不硬塞成标量属性。"""
    with pytest.raises(AclnnRunnerError):
        R.parse_aclnn_signature(
            "aclnnStatus aclnnFooGetWorkspaceSize(const aclTensor *self, const float *w, "
            "aclTensor *out, uint64_t *workspaceSize, aclOpExecutor **executor);")


def test_parse_signature_tensorlist_fail_closed():
    header = ("aclnnStatus aclnnBarGetWorkspaceSize(const aclTensorList *tensors, aclTensor *out, "
              "uint64_t *workspaceSize, aclOpExecutor **executor);")
    with pytest.raises(AclnnRunnerError):
        R.parse_aclnn_signature(header)


def test_parse_aclnn_op_from_header_dir(tmp_path):
    op_dir = tmp_path / "Median"
    (op_dir / "op_api").mkdir(parents=True)
    (op_dir / "op_api" / "aclnn_median.h").write_text(_MEDIAN_HEADER, encoding="utf-8")
    # _impl.h 应被忽略（只解析对外头）。
    (op_dir / "op_api" / "aclnn_median_impl.h").write_text("garbage", encoding="utf-8")
    sig = R.parse_aclnn_op(op_dir)
    assert sig.op_name == "Median"
    assert (sig.num_inputs, sig.num_outputs) == (1, 2)
    # 按 symbol 选定同样命中；symbol 对不上 → fail-closed（不静默拿别的头）。
    assert R.parse_aclnn_op(op_dir, symbol="Median").op_name == "Median"
    with pytest.raises(AclnnRunnerError):
        R.parse_aclnn_op(op_dir, symbol="Abs")


def test_parse_aclnn_op_multi_header_needs_symbol(tmp_path):
    """一个目录多份对外头 → 不给 symbol 就 fail-closed（旧版静默取第一份 = 拿错签名）。"""
    op_dir = tmp_path / "Ops"
    (op_dir / "op_api").mkdir(parents=True)
    (op_dir / "op_api" / "aclnn_median.h").write_text(_MEDIAN_HEADER, encoding="utf-8")
    (op_dir / "op_api" / "aclnn_abs.h").write_text(
        "aclnnStatus aclnnAbsGetWorkspaceSize(const aclTensor *self, aclTensor *out, "
        "uint64_t *workspaceSize, aclOpExecutor **executor);", encoding="utf-8")
    with pytest.raises(AclnnRunnerError):
        R.parse_aclnn_op(op_dir)
    assert R.parse_aclnn_op(op_dir, symbol="Abs").op_name == "Abs"


def test_parse_single_output_op():
    header = ("aclnnStatus aclnnAbsGetWorkspaceSize(const aclTensor *self, aclTensor *out, "
              "uint64_t *workspaceSize, aclOpExecutor **executor);")
    sig = R.parse_aclnn_signature(header)
    assert sig.op_name == "Abs"
    assert (sig.num_inputs, sig.num_outputs) == (1, 1)
    assert [p["role"] for p in sig.params] == ["in", "out"]


def test_parse_missing_signature_raises():
    with pytest.raises(AclnnRunnerError):
        R.parse_aclnn_signature("no aclnn signature here")


# ── custom vendor lib 发现（Bug#1 可选 + Bug#B 三源都吃）──────────────────────────

_LIB_ENVS = ("ASCEND_CUSTOM_OPP_PATH", "ASCEND_OPP_PATH", "LD_LIBRARY_PATH")


def _clear_lib_envs(monkeypatch):
    for var in _LIB_ENVS:
        monkeypatch.delenv(var, raising=False)


def _make_vendor(root: Path, name: str) -> Path:
    """造一个 install 后的 vendor 内容根：``<root>/vendors/<name>/op_api/lib/libcust_opapi.so``。"""
    vendor = root / "vendors" / name
    lib_dir = vendor / "op_api" / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "libcust_opapi.so").write_bytes(b"\x7fELF-fake")
    return vendor


def test_find_custom_opapi_libs_optional(monkeypatch):
    # 三个来源都没 set → 返回 [] 不 raise（内置算子照跑）。
    _clear_lib_envs(monkeypatch)
    assert R._find_custom_opapi_libs() == []
    # set 了但目录无 custom lib → 仍 [] 不 raise。
    monkeypatch.setenv("ASCEND_OPP_PATH", "/nonexistent/opp/path")
    monkeypatch.setenv("ASCEND_CUSTOM_OPP_PATH", "/nonexistent/vendor/root")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/nonexistent/lib:/also/nonexistent")
    assert R._find_custom_opapi_libs() == []


def test_find_custom_opapi_libs_reads_ascend_custom_opp_path(tmp_path, monkeypatch):
    """Bug#B：install 生成的权威 set_env.bash 只导 ASCEND_CUSTOM_OPP_PATH（vendor **内容根**、冒号分隔）。

    旧版只 glob $ASCEND_OPP_PATH → 按官方 env 跑时一个 custom lib 都找不到（DUT 全落空、静默用内置）。
    """
    _clear_lib_envs(monkeypatch)
    vendor = _make_vendor(tmp_path, "oprunway_nn")
    monkeypatch.setenv("ASCEND_CUSTOM_OPP_PATH", f"{vendor}:/nonexistent/root")
    libs = R._find_custom_opapi_libs()
    assert [Path(p).resolve() for p in libs] == [
        (vendor / "op_api" / "lib" / "libcust_opapi.so").resolve()]


def test_find_custom_opapi_libs_reads_ascend_opp_path_glob(tmp_path, monkeypatch):
    """老路子（手工 set ASCEND_OPP_PATH）不能退化：vendors/*/op_api/lib/ 仍要 glob 得到。"""
    _clear_lib_envs(monkeypatch)
    _make_vendor(tmp_path, "a_nn")
    _make_vendor(tmp_path, "b_nn")
    monkeypatch.setenv("ASCEND_OPP_PATH", str(tmp_path))
    libs = R._find_custom_opapi_libs()
    assert len(libs) == 2 and all(p.endswith("op_api/lib/libcust_opapi.so") for p in libs)


def test_find_custom_opapi_libs_ld_library_path_fallback(tmp_path, monkeypatch):
    """set_env.bash 同时导出的 LD_LIBRARY_PATH 兜底：逐目录找 libcust_opapi.so。"""
    _clear_lib_envs(monkeypatch)
    vendor = _make_vendor(tmp_path, "c_nn")
    monkeypatch.setenv("LD_LIBRARY_PATH", f"/nonexistent/lib:{vendor / 'op_api' / 'lib'}")
    libs = R._find_custom_opapi_libs()
    assert [Path(p).resolve() for p in libs] == [
        (vendor / "op_api" / "lib" / "libcust_opapi.so").resolve()]


def test_find_custom_opapi_libs_dedups_across_env_vars(tmp_path, monkeypatch):
    """同一个 so 被三个变量同时指到 → 只出现一次（按 realpath 去重），且权威源排最前。"""
    _clear_lib_envs(monkeypatch)
    vendor = _make_vendor(tmp_path, "d_nn")
    lib_dir = vendor / "op_api" / "lib"
    monkeypatch.setenv("ASCEND_CUSTOM_OPP_PATH", str(vendor))
    monkeypatch.setenv("ASCEND_OPP_PATH", str(tmp_path))
    monkeypatch.setenv("LD_LIBRARY_PATH", str(lib_dir))
    libs = R._find_custom_opapi_libs()
    assert len(libs) == 1
    assert Path(libs[0]).resolve() == (lib_dir / "libcust_opapi.so").resolve()


# ── contiguous_strides ───────────────────────────────────────────────────────

def test_contiguous_strides():
    assert R.contiguous_strides([4, 6]) == [6, 1]
    assert R.contiguous_strides([2, 3, 5]) == [15, 5, 1]
    assert R.contiguous_strides([7]) == [1]
    assert R.contiguous_strides([]) == []


# ── dtype 映射 ───────────────────────────────────────────────────────────────

def test_acl_dtype_covers_int_and_bf16():
    assert acl_consts.acl_dtype("int64") == 9
    assert acl_consts.acl_dtype("int32") == 3
    assert acl_consts.acl_dtype("int8") == 2
    assert acl_consts.acl_dtype("uint8") == 4
    assert acl_consts.acl_dtype("bfloat16") == 27
    assert acl_consts.acl_dtype("float32") == 0
    assert acl_consts.acl_dtype("float16") == 1
    # runner 本地薄封装同源。
    assert R._acl_dtype("int64") == 9
    assert R._acl_dtype("bfloat16") == 27


def test_acl_dtype_unknown_raises():
    with pytest.raises(AclnnRunnerError):
        acl_consts.acl_dtype("float8_e4m3")


# ── bf16 位窄化 ──────────────────────────────────────────────────────────────

def test_bf16_narrow_bytecount_and_dtype():
    x = np.array([1.0, 2.0, -3.5, 0.0], dtype=np.float32)
    bf = R.f32_to_bf16_bytes(x)
    assert bf.dtype == np.uint16
    assert bf.nbytes == x.size * 2          # bf16 = 2 字节/元素（非 fp32 的 4 字节）
    assert bf.size == x.size


def test_bf16_roundtrip_on_grid():
    # 这些值在 bf16 网格上可精确表示 → decode(encode(v)) == v。
    x = np.array([1.0, 2.0, -3.5, 0.5, 0.0, -0.0, 256.0], dtype=np.float32)
    back = R.bf16_bytes_to_f32(R.f32_to_bf16_bytes(x))
    assert back.dtype == np.float32
    assert np.array_equal(back, x)


def test_bf16_narrow_preserves_sign_zero():
    x = np.array([-0.0], dtype=np.float32)
    bf = R.f32_to_bf16_bytes(x)
    assert bf[0] == 0x8000                   # 负零符号位保留


def test_prep_input_bf16_from_fp32():
    runner = R.AclnnRunner()
    arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    storage, acl_name = runner._prep_input(arr, "bfloat16")
    assert acl_name == "bfloat16"
    assert storage.dtype == np.uint16
    assert storage.nbytes == 3 * 2


def test_prep_input_bf16_from_uint16_passthrough():
    runner = R.AclnnRunner()
    bits = np.array([0x3F80, 0x4000], dtype=np.uint16)   # 已是 bf16 位模式（1.0, 2.0）
    storage, acl_name = runner._prep_input(bits, "bfloat16")
    assert acl_name == "bfloat16"
    assert np.array_equal(storage, bits)                 # 不二次窄化


def test_prep_input_bf16_wrong_physical_dtype_rejected():
    """audit#2：bf16 的物理载体只能是 uint16(位模式)/float32(待窄化)，别的一律拒。"""
    runner = R.AclnnRunner()
    with pytest.raises(AclnnRunnerError):
        runner._prep_input(np.array([1, 2], dtype=np.int8), "bfloat16")


def test_prep_input_physical_dtype_must_match_declared():
    """audit#2 最小复现：2 元素 uint8 声明成 float32 —— 旧版只分配 2 字节而 tensor 要 8 字节 → 越界。"""
    runner = R.AclnnRunner()
    with pytest.raises(AclnnRunnerError):
        runner._prep_input(np.array([1, 2], dtype=np.uint8), "float32")


def test_checked_nbytes_and_overflow():
    assert R._checked_nbytes([], 4) == 4          # 0 维 = 1 个元素
    assert R._checked_nbytes([2, 3], 8) == 48
    with pytest.raises(AclnnRunnerError):
        R._checked_nbytes([-1], 4)
    with pytest.raises(AclnnRunnerError):         # numel × itemsize 溢出 64bit
        R._checked_nbytes([1 << 40, 1 << 40], 8)



# ── run() argtypes 拼装（mock ctypes，多输出 arity）─────────────────────────────

class _FakeFunc:
    """记录 argtypes/restype 的假 ctypes 函数，调用恒返 0。"""

    def __init__(self):
        self.argtypes = None
        self.restype = None
        self.calls = 0

    def __call__(self, *args):
        self.calls += 1
        return 0


class _FakeAcl:
    """按名惰性造 _FakeFunc 的假 ACL 句柄（模拟 CDLL(None)）。"""

    def __init__(self):
        self._funcs = {}

    def __getattr__(self, name):
        # 注意：__getattr__ 只在常规查找失败时触发；_funcs 存在 __dict__ 里不会递归。
        funcs = self.__dict__.setdefault("_funcs", {})
        if name not in funcs:
            funcs[name] = _FakeFunc()
        return funcs[name]


def _sig(op_name, *params):
    """手搓一份 AclnnSignature（无 header 的调用方就该这么显式构造——仍受 run() 全量校验）。"""
    return R.AclnnSignature(op_name=op_name, params=[
        {"name": n, "role": r, "ctype": c} for n, r, c in params])


_MEDIAN_SIG = R.parse_aclnn_signature(_MEDIAN_HEADER)
_FOO_1IN_1OUT = _sig("Foo", ("self", "in", "tensor"), ("out", "out", "tensor"))


def _mock_runner(monkeypatch, **kw):
    """造一个绕开 ctypes/NPU 的 AclnnRunner：假 acl 句柄 + 桩 _make_tensor/_malloc/_ck。

    ``kw`` 透传构造参数（如 ``require_custom_vendor=True`` 的严格档）。
    """
    runner = R.AclnnRunner(**kw)
    fake = _FakeAcl()
    runner._acl = fake
    runner._stream = ctypes.c_void_p()
    monkeypatch.setattr(runner, "_ensure_init", lambda: None)
    monkeypatch.setattr(runner, "_ck", lambda name, ret, ok=(0,): None)
    made = []

    def fake_make_tensor(shape, acl_dtype_name, *, host, nbytes):
        made.append({"shape": list(shape), "dtype": acl_dtype_name, "nbytes": nbytes})
        return object(), ctypes.c_void_p()

    monkeypatch.setattr(runner, "_make_tensor", fake_make_tensor)
    monkeypatch.setattr(runner, "_malloc", lambda n: ctypes.c_void_p())
    return runner, fake, made


def _in_slot(arr, dtype=None, name="self"):
    return {"kind": "in", "name": name, "array": arr, "dtype": dtype or arr.dtype.name}


def _out_slot(shape, dtype, index, role="value", name="out"):
    return {"kind": "out", "name": name, "shape": list(shape), "dtype": dtype,
            "role": role, "index": index}


def _median_slots(values_shape=(2,), with_indices=True):
    slots = [
        _in_slot(np.arange(6, dtype=np.float32).reshape(2, 3)),
        {"kind": "attr", "name": "dim", "ctype": "int64", "value": 1},
        {"kind": "attr", "name": "keepdim", "ctype": "bool", "value": False},
        _out_slot(list(values_shape), "float32", 0, "value", name="values"),
    ]
    slots.append(_out_slot(list(values_shape), "int64", 1, "index", name="indices")
                 if with_indices else {"kind": "out_null", "name": "indices"})
    return slots


def test_run_median_slots_interleaved_argtypes(monkeypatch):
    """median 有序 slots：in, attr int64(dim), attr bool(keepdim), out, out —— argtypes 按真实顺序拼。"""
    runner, fake, made = _mock_runner(monkeypatch)
    outs = runner.run("Median", _median_slots(), signature=_MEDIAN_SIG)
    gws = fake._funcs["aclnnMedianGetWorkspaceSize"]
    run_fn = fake._funcs["aclnnMedian"]
    # argtypes 精确保序：[vp(self), c_int64(dim), c_bool(keepdim), vp(values), vp(indices)] + [vp,vp]。
    assert gws.argtypes == [ctypes.c_void_p, ctypes.c_int64, ctypes.c_bool,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    assert run_fn.argtypes == [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_void_p]
    # 只为张量 slot 建 tensor（1 in + 2 out = 3），标量不建 tensor。
    assert len(made) == 3
    assert len(outs) == 2
    assert outs[0].shape == (2,) and outs[0].dtype == np.float32
    assert outs[1].shape == (2,) and outs[1].dtype == np.int64


def test_run_out_null_passes_null_and_no_output(monkeypatch):
    """全局 median：只有 values、无 indices → 末 out-slot 为 out_null（传 NULL、不 D2H、不产出）。"""
    runner, fake, made = _mock_runner(monkeypatch)
    slots = _median_slots(values_shape=(), with_indices=False)
    slots[0] = _in_slot(np.arange(4, dtype=np.float32))
    outs = runner.run("Median", slots, signature=_MEDIAN_SIG)
    gws = fake._funcs["aclnnMedianGetWorkspaceSize"]
    # out_null 仍占一个 vp 形参位（签名里 indicesOut 形参存在，只是传 NULL）。
    assert gws.argtypes == [ctypes.c_void_p, ctypes.c_int64, ctypes.c_bool,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    # 只建 1 in + 1 out（out_null 不建 tensor）。
    assert len(made) == 2
    # out_null 不产出 → 只返回 1 个输出。
    assert len(outs) == 1
    assert outs[0].dtype == np.float32


def test_run_bf16_output_alloc_and_widen(monkeypatch):
    """bf16 输出 slot：以 2 字节 alloc、D2H 后展宽成 fp32 返回。"""
    runner, fake, made = _mock_runner(monkeypatch)
    slots = [_in_slot(np.array([1.0, 2.0], dtype=np.float32)), _out_slot([2], "bfloat16", 0)]
    outs = runner.run("Foo", slots, signature=_FOO_1IN_1OUT)
    out_alloc = made[-1]
    assert out_alloc["dtype"] == "bfloat16"
    assert out_alloc["nbytes"] == 2 * 2               # 2 元素 × 2 字节
    assert outs[0].dtype == np.float32                # 展宽后 fp32
    assert outs[0].shape == (2,)


def test_run_bf16_input_narrowed(monkeypatch):
    """bf16 输入 slot（逻辑 dtype=bfloat16）→ 建 tensor 用 2 字节位模式。"""
    runner, fake, made = _mock_runner(monkeypatch)
    slots = [_in_slot(np.array([1.0, 2.0, 3.0], dtype=np.float32), dtype="bfloat16"),
             _out_slot([1], "float32", 0)]
    runner.run("Foo", slots, signature=_FOO_1IN_1OUT)
    in_alloc = made[0]
    assert in_alloc["dtype"] == "bfloat16"
    assert in_alloc["nbytes"] == 3 * 2                # fp32 被窄化成 3×2 字节


def test_run_float32_attr_marshals_c_float(monkeypatch):
    """audit#5：C `float` 形参 → c_float（**不是** c_double，位宽不同会传错值）。"""
    runner, fake, made = _mock_runner(monkeypatch)
    sig = _sig("Foo", ("self", "in", "tensor"), ("alpha", "attr", "float32"), ("out", "out", "tensor"))
    slots = [_in_slot(np.zeros(2, np.float32)),
             {"kind": "attr", "name": "alpha", "ctype": "float32", "value": 0.5},
             _out_slot([2], "float32", 0)]
    runner.run("Foo", slots, signature=sig)
    gws = fake._funcs["aclnnFooGetWorkspaceSize"]
    assert gws.argtypes == [ctypes.c_void_p, ctypes.c_float, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_void_p]


def test_run_float64_attr_marshals_c_double(monkeypatch):
    runner, fake, made = _mock_runner(monkeypatch)
    sig = _sig("Foo", ("self", "in", "tensor"), ("alpha", "attr", "float64"), ("out", "out", "tensor"))
    slots = [_in_slot(np.zeros(2, np.float32)),
             {"kind": "attr", "name": "alpha", "ctype": "float64", "value": 0.5},
             _out_slot([2], "float32", 0)]
    runner.run("Foo", slots, signature=sig)
    gws = fake._funcs["aclnnFooGetWorkspaceSize"]
    assert gws.argtypes == [ctypes.c_void_p, ctypes.c_double, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_void_p]


def test_run_legacy_float_ctype_rejected(monkeypatch):
    """旧的合并 ctype `"float"` 已废（位宽歧义）→ fail-closed，别猜是 float 还是 double。"""
    runner, _, _ = _mock_runner(monkeypatch)
    sig = _sig("Foo", ("self", "in", "tensor"), ("alpha", "attr", "float"), ("out", "out", "tensor"))
    slots = [_in_slot(np.zeros(2, np.float32)),
             {"kind": "attr", "name": "alpha", "ctype": "float", "value": 0.5},
             _out_slot([2], "float32", 0)]
    with pytest.raises(AclnnRunnerError):
        runner.run("Foo", slots, signature=sig)


def test_run_scalar_attr_creates_and_destroys(monkeypatch):
    """aclScalar 分支：aclCreateScalar 建、末尾 aclDestroyScalar 销（median 用不到、通用机制在）。"""
    runner, fake, made = _mock_runner(monkeypatch)
    # 让假 aclCreateScalar 返回非 NULL（否则 run 会 fail-closed）。
    created = []

    def fake_create_scalar(ptr, dt):
        created.append(dt)
        return 0xABCD
    monkeypatch.setattr(fake, "aclCreateScalar", fake_create_scalar)
    destroyed = []
    monkeypatch.setattr(fake, "aclDestroyScalar", lambda sc: destroyed.append(sc) or 0)

    sig = _sig("Foo", ("self", "in", "tensor"), ("beta", "attr", "scalar"), ("out", "out", "tensor"))
    slots = [_in_slot(np.zeros(2, np.float32)),
             {"kind": "attr", "name": "beta", "ctype": "scalar", "value": 1.5, "dtype": "float32"},
             _out_slot([2], "float32", 0)]
    runner.run("Foo", slots, signature=sig)
    gws = fake._funcs["aclnnFooGetWorkspaceSize"]
    assert gws.argtypes == [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_void_p]
    assert created == [acl_consts.acl_dtype("float32")]
    assert destroyed == [0xABCD]


def test_run_unknown_attr_ctype_raises(monkeypatch):
    runner, _, _ = _mock_runner(monkeypatch)
    sig = _sig("Foo", ("self", "in", "tensor"), ("k", "attr", "int128"), ("out", "out", "tensor"))
    with pytest.raises(AclnnRunnerError):
        runner.run("Foo", [_in_slot(np.zeros(2, np.float32)),
                           {"kind": "attr", "name": "k", "ctype": "int128", "value": 1},
                           _out_slot([2], "float32", 0)], signature=sig)


def test_run_unknown_slot_kind_raises(monkeypatch):
    runner, _, _ = _mock_runner(monkeypatch)
    sig = _sig("Foo", ("x", "bogus", "tensor"))
    with pytest.raises(AclnnRunnerError):
        runner.run("Foo", [{"kind": "bogus", "name": "x", "ctype": "tensor"}], signature=sig)


# ── 签名强制（audit#1/#4）─────────────────────────────────────────────────────

def test_run_requires_signature_kwarg(monkeypatch):
    """audit#1：signature 不再有 None 兜底——不传是 TypeError，显式 None 是 fail-closed。"""
    runner, _, _ = _mock_runner(monkeypatch)
    with pytest.raises(TypeError):
        runner.run("Median", _median_slots())
    with pytest.raises(AclnnRunnerError):
        runner.run("Median", _median_slots(), signature=None)


def test_run_arity_mismatch_rejected(monkeypatch):
    """slots 数 ≠ 签名形参数 → 绝不进 native 调用（旧版无签名时直接就调了）。"""
    runner, fake, _ = _mock_runner(monkeypatch)
    slots = _median_slots()[:-1]                 # 少一个 out
    with pytest.raises(AclnnRunnerError):
        runner.run("Median", slots, signature=_MEDIAN_SIG)
    assert fake._funcs.get("aclnnMedianGetWorkspaceSize") is None   # 一次 native 都没调


def test_run_op_name_mismatch_rejected(monkeypatch):
    """签名的算子名 ≠ 调用符号 → 签名与调用不同源，fail-closed。"""
    runner, _, _ = _mock_runner(monkeypatch)
    with pytest.raises(AclnnRunnerError):
        runner.run("MedianDim", _median_slots(), signature=_MEDIAN_SIG)


def test_run_signature_crossvalidation_pass(monkeypatch):
    """slots 的 name/role/ctype 与 header 签名一致 → 校验通过、正常跑（values ↔ valuesOut 按约定归一）。"""
    runner, fake, made = _mock_runner(monkeypatch)
    outs = runner.run("Median", _median_slots(), signature=_MEDIAN_SIG)
    assert len(outs) == 2


def test_run_signature_crossvalidation_role_mismatch_raises(monkeypatch):
    """slots 少一个 attr（role 序列与签名不符）→ fail-closed。"""
    runner, _, _ = _mock_runner(monkeypatch)
    slots = [s for s in _median_slots() if s.get("name") != "keepdim"]
    with pytest.raises(AclnnRunnerError):
        runner.run("Median", slots, signature=_MEDIAN_SIG)


def test_run_signature_crossvalidation_ctype_mismatch_raises(monkeypatch):
    """attr ctype 与签名不符（bool 处传成 float64）→ fail-closed。"""
    runner, _, _ = _mock_runner(monkeypatch)
    slots = _median_slots()
    slots[2] = {"kind": "attr", "name": "keepdim", "ctype": "float64", "value": 0.0}
    with pytest.raises(AclnnRunnerError):
        runner.run("Median", slots, signature=_MEDIAN_SIG)


def test_run_swapped_same_type_tensors_rejected(monkeypatch):
    """audit#4：两个相邻输入都是 tensor 时，self/other 对调只有比**名字**才拦得住。"""
    runner, _, _ = _mock_runner(monkeypatch)
    sig = _sig("Bar", ("self", "in", "tensor"), ("other", "in", "tensor"), ("out", "out", "tensor"))
    ok = [_in_slot(np.zeros(2, np.float32), name="self"),
          _in_slot(np.ones(2, np.float32), name="other"),
          _out_slot([2], "float32", 0)]
    assert len(runner.run("Bar", ok, signature=sig)) == 1
    swapped = [ok[1], ok[0], ok[2]]
    with pytest.raises(AclnnRunnerError):
        runner.run("Bar", swapped, signature=sig)


def test_run_slot_without_name_rejected(monkeypatch):
    """slots 必须全程带 name，缺 name 无从对账 → fail-closed。"""
    runner, _, _ = _mock_runner(monkeypatch)
    slots = [{"kind": "in", "array": np.zeros(2, np.float32), "dtype": "float32"},
             _out_slot([2], "float32", 0)]
    with pytest.raises(AclnnRunnerError):
        runner.run("Foo", slots, signature=_FOO_1IN_1OUT)


# ── dtype / shape 规范化（audit#2/#6）──────────────────────────────────────────

def test_run_input_physical_dtype_mismatch_rejected(monkeypatch):
    """audit#2 最小复现：2 元素 uint8 声明 float32 → 只会分配 2 字节而 tensor 要 8 字节 → 必须拒。"""
    runner, fake, made = _mock_runner(monkeypatch)
    slots = [_in_slot(np.array([1, 2], dtype=np.uint8), dtype="float32"),
             _out_slot([2], "float32", 0)]
    with pytest.raises(AclnnRunnerError):
        runner.run("Foo", slots, signature=_FOO_1IN_1OUT)
    assert made == []                                  # 连 tensor 都没建，更没进 native


def test_run_zero_dim_input_keeps_scalar_shape(monkeypatch):
    """audit#6：0 维输入保 shape=[]（旧版 `or [storage.size]` 把标量改成 [1]，与输出侧语义不一致）。"""
    runner, fake, made = _mock_runner(monkeypatch)
    slots = [_in_slot(np.float32(3.5).reshape(()) if hasattr(np.float32(3.5), "reshape")
                      else np.array(3.5, dtype=np.float32)),
             _out_slot([], "float32", 0)]
    outs = runner.run("Foo", slots, signature=_FOO_1IN_1OUT)
    assert made[0]["shape"] == []                      # 输入 0 维保真
    assert made[0]["nbytes"] == 4                      # 标量仍占 1 个元素
    assert made[1]["shape"] == []                      # 输出侧本来就保 []
    assert outs[0].shape == ()


# ── 资源回收：每个 native 失败点都得释放（audit#3）───────────────────────────────

def _alloc_runner(monkeypatch, fail_at=None, ws_bytes=0):
    """走**真** _malloc/_make_tensor 的 runner：记录建/销 tensor、malloc/free 次数，可在指定 _ck 点注入失败。"""
    runner = R.AclnnRunner()
    fake = _FakeAcl()
    runner._acl = fake
    runner._stream = ctypes.c_void_p()
    monkeypatch.setattr(runner, "_ensure_init", lambda: None)
    st = {"tensors": [], "destroyed": [], "mallocs": 0, "freed": 0, "scalars_destroyed": []}

    def ck(name, ret, ok=(0,)):
        if fail_at is not None and name == fail_at:
            raise AclnnRunnerError(f"injected failure at {name}")

    monkeypatch.setattr(runner, "_ck", ck)

    def create_tensor(*args):
        st["tensors"].append(len(st["tensors"]) + 1)
        return st["tensors"][-1]

    def malloc(*args):
        st["mallocs"] += 1
        return 0

    def free(dev):
        st["freed"] += 1
        return 0

    monkeypatch.setattr(fake, "aclCreateTensor", create_tensor)
    monkeypatch.setattr(fake, "aclrtMalloc", malloc)
    monkeypatch.setattr(fake, "aclrtFree", free)
    monkeypatch.setattr(fake, "aclDestroyTensor", lambda t: st["destroyed"].append(t) or 0)

    def gws(*args):
        if ws_bytes:
            args[-2]._obj.value = ws_bytes      # byref(ws)._obj 即那个 c_uint64
        return 0

    monkeypatch.setattr(fake, "aclnnFooGetWorkspaceSize", gws)
    return runner, fake, st


@pytest.mark.parametrize("fail_at", [
    "aclrtMemcpy(H2D)", "aclnnFooGetWorkspaceSize", "aclnnFoo",
    "aclrtSynchronizeStream", "aclrtMemcpy(D2H)",
])
def test_run_releases_resources_on_every_native_failure(monkeypatch, fail_at):
    """任一 native 失败点：已建的 tensor 全销、已 malloc 的 device 缓冲（含 workspace）全 free。"""
    runner, fake, st = _alloc_runner(monkeypatch, fail_at=fail_at, ws_bytes=4096)
    slots = [_in_slot(np.zeros(2, np.float32)), _out_slot([2], "float32", 0)]
    with pytest.raises(AclnnRunnerError):
        runner.run("Foo", slots, signature=_FOO_1IN_1OUT)
    assert st["freed"] == st["mallocs"], f"{fail_at}: device 缓冲泄漏"
    assert sorted(st["destroyed"]) == sorted(st["tensors"]), f"{fail_at}: tensor 泄漏"


def test_run_make_tensor_failure_frees_local_dev(monkeypatch):
    """_make_tensor 在建 tensor 失败时**就地**释放刚 malloc 的 dev（外层还没登记到它）。"""
    runner, fake, st = _alloc_runner(monkeypatch)
    monkeypatch.setattr(fake, "aclCreateTensor", lambda *a: 0)     # 返 NULL
    slots = [_in_slot(np.zeros(2, np.float32)), _out_slot([2], "float32", 0)]
    with pytest.raises(AclnnRunnerError):
        runner.run("Foo", slots, signature=_FOO_1IN_1OUT)
    assert st["mallocs"] == 1 and st["freed"] == 1


def test_run_success_releases_everything_including_workspace(monkeypatch):
    """成功路径同样全回收：2 个 tensor + 2 块 device 缓冲 + 1 块 workspace。"""
    runner, fake, st = _alloc_runner(monkeypatch, ws_bytes=4096)
    slots = [_in_slot(np.zeros(2, np.float32)), _out_slot([2], "float32", 0)]
    outs = runner.run("Foo", slots, signature=_FOO_1IN_1OUT)
    assert len(outs) == 1
    assert st["mallocs"] == 3 and st["freed"] == 3     # in + out + workspace
    assert sorted(st["destroyed"]) == [1, 2]


def test_run_symbol_missing_before_any_allocation(monkeypatch):
    """符号解析前移到分配之前：找不到 aclnn 符号时不该已经占着 device 内存。"""
    runner, fake, st = _alloc_runner(monkeypatch)

    class _NoSymAcl(_FakeAcl):
        def __getattr__(self, name):
            if name.startswith("aclnnFoo"):
                raise AttributeError(name)
            return super().__getattr__(name)

    nosym = _NoSymAcl()
    monkeypatch.setattr(nosym, "aclrtMalloc", lambda *a: st.__setitem__("mallocs", st["mallocs"] + 1) or 0)
    runner._acl = nosym
    with pytest.raises(AclnnRunnerError):
        runner.run("Foo", [_in_slot(np.zeros(2, np.float32)), _out_slot([2], "float32", 0)],
                   signature=_FOO_1IN_1OUT)
    assert st["mallocs"] == 0


# ── driver（执行**逐 case 已解析的 aclnn_call**，注入 fake runner，只产 out.bin 不判定）──────────

def _median_call(*, dim=1, keepdim=False, with_indices=True):
    """gen_cases 逐 case 解析后写进 case 的 aclnn_call（本文件按共享契约手造，等价于 spec.call_variants 的产物）。"""
    slots = [
        {"role": "in", "name": "self", "input_idx": 0},
        {"role": "attr", "name": "dim", "ctype": "int64", "value": dim},
        {"role": "attr", "name": "keepdim", "ctype": "bool", "value": keepdim},
        {"role": "out", "name": "values", "output_idx": 0},
    ]
    slots.append({"role": "out", "name": "indices", "output_idx": 1} if with_indices
                 else {"role": "out_null", "name": "indices"})
    return {"symbol": "Median", "slots": slots}


_SIGS = {"Median": _MEDIAN_SIG}


class _FakeRunner:
    """据 out-slots 返回确定性数组的假 runner，并记录每次 run 收到的 slots / signature。"""

    def __init__(self):
        self.calls = []

    def run(self, op_name, slots, *, signature):
        self.calls.append({"op": op_name, "slots": slots, "signature": signature})
        outs, i = [], 0
        for s in slots:
            if s["kind"] == "out":
                shp, dt = s["shape"], s["dtype"]
                n = int(np.prod(shp)) if shp else 1
                npdt = np.float32 if dt == "bfloat16" else np.dtype(dt)
                outs.append((np.arange(n, dtype=npdt) + i * 100).reshape(shp))
                i += 1
        return outs


def _write_case_inputs(work_dir: Path, cid: str, arrays: list) -> list:
    (work_dir / cid).mkdir(parents=True, exist_ok=True)
    recs = []
    for j, arr in enumerate(arrays):
        rel = f"{cid}/x{j + 1}.npy"
        np.save(work_dir / rel, arr)
        recs.append({"name": f"in{j}", "shape": list(arr.shape), "dtype": arr.dtype.name, "path": rel})
    return recs


def _median_caseset(recs, *, call, outputs):
    return {"op": "Median", "cases": [{
        "id": "c01", "inputs": recs, "attrs": {}, "aclnn_call": call,
        "expected": {"outputs": outputs},
    }]}


def test_driver_bydim_writes_ordered_bins(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(6, dtype=np.float32).reshape(2, 3)])
    caseset = _median_caseset(recs, call=_median_call(dim=1), outputs=[
        {"role": "value", "out_shape": [2], "compare_dtype": "float32"},
        {"role": "index", "out_shape": [2], "compare_dtype": "int64"},
    ])
    runner = _FakeRunner()
    out_dir = tmp_path / "out"
    manifest = aclnn_driver.run_driver(caseset, work, out_dir, runner, signatures=_SIGS)

    # slots 顺序 = 该 case aclnn_call 的顺序：in, attr(dim=1), attr(keepdim=False), out(value), out(index)。
    slots = runner.calls[0]["slots"]
    assert [s["kind"] for s in slots] == ["in", "attr", "attr", "out", "out"]
    assert [s["name"] for s in slots] == ["self", "dim", "keepdim", "values", "indices"]
    assert slots[1]["ctype"] == "int64" and slots[1]["value"] == 1
    assert slots[2]["ctype"] == "bool" and slots[2]["value"] is False
    assert runner.calls[0]["op"] == "Median"
    assert runner.calls[0]["signature"] is _MEDIAN_SIG        # 签名一路传到 runner
    # 落盘 out_0.bin(value/fp32) + out_1.bin(index/int64)，顺序正确。
    v = np.fromfile(out_dir / "c01" / "out_0.bin", dtype=np.float32)
    idx = np.fromfile(out_dir / "c01" / "out_1.bin", dtype=np.int64)
    assert np.array_equal(v, np.arange(2, dtype=np.float32))
    assert np.array_equal(idx, np.arange(2, dtype=np.int64) + 100)
    prod = manifest["produced"][0]
    assert [o["role"] for o in prod["outputs"]] == ["value", "index"]
    assert [o["path"] for o in prod["outputs"]] == ["c01/out_0.bin", "c01/out_1.bin"]
    assert manifest["symbol"] == "Median" and manifest["symbols"] == ["Median"]


def test_driver_global_variant_out_null(tmp_path):
    """全局 median case：变体由 gen_cases 解析好（dim=0/keepdim=False + 只 values）→ 第二 out-slot 是 out_null。"""
    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(5, dtype=np.float32)])
    caseset = _median_caseset(recs, call=_median_call(dim=0, with_indices=False),
                              outputs=[{"role": "value", "out_shape": [], "compare_dtype": "float32"}])
    runner = _FakeRunner()
    out_dir = tmp_path / "out"
    manifest = aclnn_driver.run_driver(caseset, work, out_dir, runner, signatures=_SIGS)
    slots = runner.calls[0]["slots"]
    assert [s["kind"] for s in slots] == ["in", "attr", "attr", "out", "out_null"]
    assert slots[1]["value"] == 0 and slots[2]["value"] is False   # 值来自 aclnn_call，不是 driver 兜的
    # 只落一个输出文件。
    assert (out_dir / "c01" / "out_0.bin").exists()
    assert not (out_dir / "c01" / "out_1.bin").exists()
    assert len(manifest["produced"][0]["outputs"]) == 1


def test_driver_slots_pass_runner_signature_check(tmp_path, monkeypatch):
    """端到端对账：driver 从 aclnn_call 派生的 slots，能过**真 runner** 对 median header 签名的逐项校验。

    （契约里 out slot 名叫 values/indices，header 里叫 valuesOut/indicesOut —— 归一后必须对得上。）
    """
    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(6, dtype=np.float32).reshape(2, 3)])
    caseset = _median_caseset(recs, call=_median_call(dim=1), outputs=[
        {"role": "value", "out_shape": [2], "compare_dtype": "float32"},
        {"role": "index", "out_shape": [2], "compare_dtype": "int64"},
    ])
    runner, fake, made = _mock_runner(monkeypatch)
    manifest = aclnn_driver.run_driver(caseset, work, tmp_path / "out", runner, signatures=_SIGS)
    assert len(manifest["produced"][0]["outputs"]) == 2
    assert len(made) == 3                                   # 1 in + 2 out


def test_driver_bf16_input_logical_dtype_forwarded(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "c01").mkdir(parents=True)
    bits = R.f32_to_bf16_bytes(np.array([1.0, 2.0, 3.0], dtype=np.float32))
    np.save(work / "c01" / "x1.npy", bits)
    recs = [{"name": "self", "shape": [3], "dtype": "bfloat16", "path": "c01/x1.npy",
             "storage_dtype": "uint16"}]
    caseset = _median_caseset(recs, call=_median_call(dim=0), outputs=[
        {"role": "value", "out_shape": [], "compare_dtype": "bfloat16"},
        {"role": "index", "out_shape": [], "compare_dtype": "int64"},
    ])
    runner = _FakeRunner()
    aclnn_driver.run_driver(caseset, work, tmp_path / "out", runner, signatures=_SIGS)
    in_slot = runner.calls[0]["slots"][0]
    assert in_slot["kind"] == "in" and in_slot["dtype"] == "bfloat16"


def test_driver_missing_aclnn_call_fail_closed(tmp_path):
    """没有逐 case 解析好的 aclnn_call → fail-closed（driver 不再合成模板、不推变体）。"""
    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(4, dtype=np.float32)])
    caseset = {"op": "Neg", "cases": [{
        "id": "c01", "inputs": recs, "attrs": {},
        "expected": {"out_shape": [4], "compare_dtype": "float32", "compare": "rel_err"},
    }]}
    with pytest.raises(AclnnRunnerError):
        aclnn_driver.run_driver(caseset, work, tmp_path / "out", _FakeRunner(), signatures=_SIGS)


def test_driver_attr_null_value_fail_closed(tmp_path):
    """属性值没解析（null）→ fail-closed；driver 绝不按 ctype 塞默认（dim=None→0 等于换了个算子）。"""
    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(4, dtype=np.float32)])
    call = _median_call(dim=1, with_indices=False)
    call["slots"][1]["value"] = None
    caseset = _median_caseset(recs, call=call,
                              outputs=[{"role": "value", "out_shape": [], "compare_dtype": "float32"}])
    with pytest.raises(AclnnRunnerError):
        aclnn_driver.run_driver(caseset, work, tmp_path / "out", _FakeRunner(), signatures=_SIGS)


def test_driver_attr_value_key_missing_fail_closed(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(4, dtype=np.float32)])
    call = _median_call(dim=1, with_indices=False)
    del call["slots"][1]["value"]
    caseset = _median_caseset(recs, call=call,
                              outputs=[{"role": "value", "out_shape": [], "compare_dtype": "float32"}])
    with pytest.raises(AclnnRunnerError):
        aclnn_driver.run_driver(caseset, work, tmp_path / "out", _FakeRunner(), signatures=_SIGS)


def test_driver_slot_index_out_of_range_fail_closed(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(4, dtype=np.float32)])
    call = _median_call(dim=1, with_indices=False)
    call["slots"][0]["input_idx"] = 3                 # 本 case 只有 1 个输入
    caseset = _median_caseset(recs, call=call,
                              outputs=[{"role": "value", "out_shape": [], "compare_dtype": "float32"}])
    with pytest.raises(AclnnRunnerError):
        aclnn_driver.run_driver(caseset, work, tmp_path / "out", _FakeRunner(), signatures=_SIGS)


def test_driver_output_plan_not_fully_consumed_fail_closed(tmp_path):
    """case 声明 2 个期望输出，但 aclnn_call 只取 1 个（另一个写成 out_null）→ 账目不平，fail-closed。"""
    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(6, dtype=np.float32).reshape(2, 3)])
    caseset = _median_caseset(recs, call=_median_call(dim=1, with_indices=False), outputs=[
        {"role": "value", "out_shape": [2], "compare_dtype": "float32"},
        {"role": "index", "out_shape": [2], "compare_dtype": "int64"},
    ])
    with pytest.raises(AclnnRunnerError):
        aclnn_driver.run_driver(caseset, work, tmp_path / "out", _FakeRunner(), signatures=_SIGS)


def test_driver_missing_slot_name_fail_closed(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(4, dtype=np.float32)])
    call = _median_call(dim=1, with_indices=False)
    del call["slots"][0]["name"]
    caseset = _median_caseset(recs, call=call,
                              outputs=[{"role": "value", "out_shape": [], "compare_dtype": "float32"}])
    with pytest.raises(AclnnRunnerError):
        aclnn_driver.run_driver(caseset, work, tmp_path / "out", _FakeRunner(), signatures=_SIGS)


def test_driver_runner_failure_carries_case_id_and_slots(tmp_path):
    """真机 bug#11：runner 抛错（如 `ACL 561103`）时，异常必须带 case_id + slots 摘要。

    否则 60 条用例里只看到一行 ACL 错误码，定位不到是哪条 case / 什么符号 / 什么形状 dtype 触发的
    ——只能人肉逐条重放。摘要只带元数据（符号 + 各 slot 的 role/name/shape/dtype），不带张量数据。
    """
    class _BoomRunner:
        def run(self, op_name, slots, *, signature):
            raise RuntimeError("aclnnMedianGetWorkspaceSize failed with ACL status 561103")

    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(6, dtype=np.float32).reshape(2, 3)])
    caseset = _median_caseset(recs, call=_median_call(dim=1, with_indices=False), outputs=[
        {"role": "value", "out_shape": [2], "compare_dtype": "float32"}])
    with pytest.raises(AclnnRunnerError) as ei:
        aclnn_driver.run_driver(caseset, work, tmp_path / "out", _BoomRunner(), signatures=_SIGS)
    msg = str(ei.value)
    assert "c01" in msg                                   # 哪条 case
    assert "aclnnMedian" in msg and "561103" in msg       # 原始故障不被吞
    assert "in:self[2,3]:float32" in msg                  # 输入 slot：形状 + dtype
    assert "attr:dim:int64=1" in msg                      # 属性 slot：ctype + 已解析的值
    assert "out#0:values[2]:float32(value)" in msg        # 输出 slot：计划下标 + 形状 + role
    assert "out_null:indices" in msg                      # 不产的输出也如实列出


def test_driver_path_escape_rejected(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    caseset = {"op": "Foo", "cases": [{
        "id": "c01",
        "inputs": [{"name": "x", "shape": [2], "dtype": "float32", "path": "../evil.npy"}],
        "attrs": {},
        "aclnn_call": {"symbol": "Foo", "slots": [
            {"role": "in", "name": "x", "input_idx": 0},
            {"role": "out", "name": "y", "output_idx": 0}]},
        "expected": {"out_shape": [2], "compare_dtype": "float32"},
    }]}
    sigs = {"Foo": _sig("Foo", ("x", "in", "tensor"), ("y", "out", "tensor"))}
    with pytest.raises(AclnnRunnerError):
        aclnn_driver.run_driver(caseset, work, tmp_path / "out", _FakeRunner(), signatures=sigs)


# ── driver 的签名解析（强制、fail-closed）────────────────────────────────────────

def test_driver_resolves_signature_from_installed_header(tmp_path, monkeypatch):
    """不注入签名时，driver 从 op 工程 / 已安装 vendor 的 aclnn 头解析（--op-dir / env 两路都走通）。"""
    monkeypatch.delenv("OPRUNWAY_ACLNN_OP_DIR", raising=False)
    monkeypatch.delenv("ASCEND_CUSTOM_OPP_PATH", raising=False)
    op_dir = tmp_path / "median_op"
    (op_dir / "op_api").mkdir(parents=True)
    (op_dir / "op_api" / "aclnn_median.h").write_text(_MEDIAN_HEADER, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(5, dtype=np.float32)])
    caseset = _median_caseset(recs, call=_median_call(dim=0, with_indices=False),
                              outputs=[{"role": "value", "out_shape": [], "compare_dtype": "float32"}])
    runner = _FakeRunner()
    aclnn_driver.run_driver(caseset, work, tmp_path / "out1", runner, op_dir=op_dir)
    sig = runner.calls[0]["signature"]
    assert sig.op_name == "Median" and [p["name"] for p in sig.params][:2] == ["self", "dim"]

    # 已安装 vendor 布局（op_api/include/）+ env 传入，同样解析得到。
    vendor = tmp_path / "vendors" / "x_nn"
    (vendor / "op_api" / "include").mkdir(parents=True)
    (vendor / "op_api" / "include" / "aclnn_median.h").write_text(_MEDIAN_HEADER, encoding="utf-8")
    monkeypatch.setenv("ASCEND_CUSTOM_OPP_PATH", f"{vendor}:/nonexistent")
    runner2 = _FakeRunner()
    aclnn_driver.run_driver(caseset, work, tmp_path / "out2", runner2)
    assert runner2.calls[0]["signature"].op_name == "Median"


def test_driver_signature_unavailable_fail_closed(tmp_path, monkeypatch):
    """取不到头签名 → fail-closed（绝不无签名调 native）。"""
    monkeypatch.delenv("OPRUNWAY_ACLNN_OP_DIR", raising=False)
    monkeypatch.delenv("ASCEND_CUSTOM_OPP_PATH", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    recs = _write_case_inputs(work, "c01", [np.arange(4, dtype=np.float32)])
    caseset = _median_caseset(recs, call=_median_call(dim=0, with_indices=False),
                              outputs=[{"role": "value", "out_shape": [], "compare_dtype": "float32"}])
    with pytest.raises(AclnnRunnerError):
        aclnn_driver.run_driver(caseset, work, tmp_path / "out", _FakeRunner())


# ── driver CLI 的 DUT 声明透传 + manifest 证据链（改动⑪ 的调用方侧）────────────────────

_PROV = {
    "device": 0, "strict_custom_vendor": True,
    "dut_lib": {"path": "/vend/op_api/lib/libcust_opapi.so", "size": 8, "mtime": 1.0, "sha256": None},
    "stream_owned": True, "device_owned": True,
    "custom_opapi_libs": [{"path": "/vend/op_api/lib/libcust_opapi.so"}],
    "ignored_custom_opapi_libs": [{"path": "/stale/op_api/lib/libcust_opapi.so"}],
    "teardown": {"closed": False, "errors": []},
    "symbols": [{"symbol": "aclnnMedian", "source": "custom_vendor",
                 "defining_lib": "/vend/op_api/lib/libcust_opapi.so",
                 "defining_lib_verified": True, "is_dut": True}],
}


class _ProvRunner(_FakeRunner):
    """带 provenance 的假 runner（真机 runner 的最小形状）：``prov`` 可改，模拟 close 前后的变化。"""

    def __init__(self, prov):
        super().__init__()
        self.prov = prov

    def runtime_provenance(self):
        return self.prov


def _one_case_median(tmp_path):
    """一条最小 median case（by-dim 变体）+ 其 work_dir，供 manifest / CLI 用例复用。"""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    recs = _write_case_inputs(work, "c01", [np.arange(6, dtype=np.float32).reshape(2, 3)])
    caseset = _median_caseset(recs, call=_median_call(dim=1), outputs=[
        {"role": "value", "out_shape": [2], "compare_dtype": "float32"},
        {"role": "index", "out_shape": [2], "compare_dtype": "int64"},
    ])
    return caseset, work


def test_driver_manifest_carries_full_provenance(tmp_path):
    """manifest 的 ``runtime`` = provenance **整份**（is_dut / dut_lib / ignored / device_owned / teardown）。

    这几栏就是「验的是不是本次被测物」的证据链，driver 一个都不许裁——载重的是 ``is_dut``，
    ``defining_lib_verified`` 只说明「属于某个 custom so」（陈旧安装产物同样满足）。
    """
    caseset, work = _one_case_median(tmp_path)
    runner = _ProvRunner(copy.deepcopy(_PROV))
    out_dir = tmp_path / "out"
    manifest = aclnn_driver.run_driver(caseset, work, out_dir, runner, signatures=_SIGS)
    on_disk = json.loads((out_dir / "out_manifest.json").read_text(encoding="utf-8"))
    assert manifest["runtime"] == runner.prov == on_disk["runtime"]
    rt = on_disk["runtime"]
    assert rt["symbols"][0]["is_dut"] is True
    assert rt["dut_lib"]["path"].endswith("libcust_opapi.so")
    assert rt["ignored_custom_opapi_libs"][0]["path"].startswith("/stale")
    assert rt["device_owned"] is True and rt["teardown"] == {"closed": False, "errors": []}


def test_refresh_manifest_runtime_records_teardown_after_close(tmp_path):
    """close 之后回写 provenance：``teardown`` 这栏只有关掉 runner 才是真的（否则永远「没关」）。"""
    caseset, work = _one_case_median(tmp_path)
    runner = _ProvRunner(copy.deepcopy(_PROV))
    out_dir = tmp_path / "out"
    manifest = aclnn_driver.run_driver(caseset, work, out_dir, runner, signatures=_SIGS)
    runner.prov = copy.deepcopy(_PROV)
    runner.prov["teardown"] = {"closed": True, "errors": [{"api": "aclrtResetDevice", "status": 7}]}
    aclnn_driver.refresh_manifest_runtime(manifest, runner)
    on_disk = json.loads((out_dir / "out_manifest.json").read_text(encoding="utf-8"))
    assert on_disk["runtime"]["teardown"]["closed"] is True
    assert on_disk["runtime"]["teardown"]["errors"][0]["api"] == "aclrtResetDevice"
    # 没实现 provenance 的 mock/旧 runner：原样返回、不写坏盘上文件。
    kept = aclnn_driver.refresh_manifest_runtime(manifest, _FakeRunner())
    assert kept["runtime"]["teardown"]["closed"] is True


def _cli_caseset(tmp_path):
    """把一条 median case 落成盘上 caseset.json，返回 (caseset 路径, work_dir)。"""
    caseset, work = _one_case_median(tmp_path)
    path = tmp_path / "caseset.json"
    path.write_text(json.dumps(caseset, ensure_ascii=False), encoding="utf-8")
    return path, work


def test_driver_cli_strict_requires_dut_declaration(tmp_path):
    """严格档（默认）没给 DUT → **CLI 就报错**，且提示的是命令行该加什么参数。

    不补这层，调用方只会看到 runner 构造异常里的 ``dut_lib=``，不知道该往命令行加哪个 flag。
    """
    cs, work = _cli_caseset(tmp_path)
    with pytest.raises(AclnnRunnerError) as ei:
        aclnn_driver.main([str(cs), str(tmp_path / "out"), "--work-dir", str(work)])
    msg = str(ei.value)
    assert "--dut-lib" in msg and "--dut-vendor-root" in msg
    assert "--allow-builtin-symbols" in msg and "假 PASS" in msg


def test_driver_cli_dut_vendor_root_derives_lib_and_lands_in_manifest(tmp_path, monkeypatch):
    """``--dut-vendor-root`` → DUT so = ``<root>/op_api/lib/libcust_opapi.so``，且落进 manifest。

    用**真** AclnnRunner（构造 / close / provenance 全是纯 Python，无 CANN）跑通 CLI 这段，
    只把真正要 device 的 run_driver 换成桩——推导口径由 runner 唯一解释，CLI 不复制一份。
    """
    cs, work = _cli_caseset(tmp_path)
    vendor_root = tmp_path / "vendors" / "customize_nn"
    (vendor_root / "op_api" / "lib").mkdir(parents=True)
    dut_so = vendor_root / "op_api" / "lib" / "libcust_opapi.so"
    dut_so.write_bytes(b"\x7fELF-dut")
    out_dir = tmp_path / "out"
    made = {}

    def fake_run_driver(caseset, work_dir, out, runner, **kw):
        out_root = Path(out).resolve()
        out_root.mkdir(parents=True, exist_ok=True)
        made["runner"] = runner
        manifest = {"op": caseset["op"], "symbol": "Median", "symbols": ["Median"],
                    "out_dir": str(out_root), "produced": [],
                    "runtime": runner.runtime_provenance()}
        aclnn_driver._write_manifest(manifest)
        return manifest

    monkeypatch.setattr(aclnn_driver, "run_driver", fake_run_driver)
    rc = aclnn_driver.main([str(cs), str(out_dir), "--work-dir", str(work),
                            "--dut-vendor-root", str(vendor_root)])
    assert rc == 0
    runner = made["runner"]
    assert runner._require_custom_vendor is True                 # 默认严格档
    assert runner._dut_lib == str(dut_so)                        # 内容根 → so 路径推对了
    rt = json.loads((out_dir / "out_manifest.json").read_text(encoding="utf-8"))["runtime"]
    assert rt["dut_lib"]["path"] == str(dut_so) and rt["dut_lib"]["size"] == len(b"\x7fELF-dut")
    assert rt["strict_custom_vendor"] is True
    assert rt["teardown"]["closed"] is True                      # close 之后回写过（证据落全）


def test_driver_cli_dut_lib_flag_and_conflict_is_runner_business(tmp_path, monkeypatch):
    """``--dut-lib`` 原样透传；两个 flag 都给且不一致 → 由 runner 统一判 fail-closed。"""
    cs, work = _cli_caseset(tmp_path)
    so = tmp_path / "v1" / "op_api" / "lib" / "libcust_opapi.so"
    so.parent.mkdir(parents=True)
    so.write_bytes(b"\x7fELF")
    seen = {}

    def fake_run_driver(caseset, work_dir, out, runner, **kw):
        Path(out).mkdir(parents=True, exist_ok=True)
        seen["dut"] = runner._dut_lib
        return {"op": caseset["op"], "symbol": None, "symbols": [], "out_dir": str(out),
                "produced": [], "runtime": runner.runtime_provenance()}

    monkeypatch.setattr(aclnn_driver, "run_driver", fake_run_driver)
    assert aclnn_driver.main([str(cs), str(tmp_path / "o1"), "--work-dir", str(work),
                              "--dut-lib", str(so)]) == 0
    assert seen["dut"] == str(so)
    with pytest.raises(AclnnRunnerError) as ei:
        aclnn_driver.main([str(cs), str(tmp_path / "o2"), "--work-dir", str(work),
                           "--dut-lib", str(so), "--dut-vendor-root", str(tmp_path / "v2")])
    assert "DUT 必须唯一" in str(ei.value)


def test_driver_cli_builtin_baseline_needs_no_dut(tmp_path, monkeypatch):
    """宽松档（``--allow-builtin-symbols``，跑 CANN 内置算子的基线场景）不要求 DUT。"""
    cs, work = _cli_caseset(tmp_path)
    captured = {}

    def fake_run_driver(caseset, work_dir, out, runner, **kw):
        Path(out).mkdir(parents=True, exist_ok=True)
        captured["strict"] = runner._require_custom_vendor
        captured["dut"] = runner._dut_lib
        return {"op": caseset["op"], "symbol": None, "symbols": [], "out_dir": str(out),
                "produced": [], "runtime": runner.runtime_provenance()}

    monkeypatch.setattr(aclnn_driver, "run_driver", fake_run_driver)
    assert aclnn_driver.main([str(cs), str(tmp_path / "out"), "--work-dir", str(work),
                              "--allow-builtin-symbols"]) == 0
    assert captured["strict"] is False and captured["dut"] is None


# ── 符号解析优先级 + provenance + 严格档（改动⑧ / 真机 Bug#A：假 PASS 的那条）──────────────

class _FakeLib:
    """假 CDLL handle（模拟 ``libcust_opapi.so``）：**只**暴露给定的符号，别的 AttributeError。

    对齐 ``_resolve_symbol`` 的取符号姿态（``getattr(handle, sym, None)`` —— 真 CDLL 缺符号
    正是抛 AttributeError）。
    """

    def __init__(self, symbols):
        self._symbols = {s: _FakeFunc() for s in symbols}

    def __getattr__(self, name):
        syms = self.__dict__.setdefault("_symbols", {})
        if name not in syms:
            raise AttributeError(name)
        return syms[name]


class _EmptyAcl:
    """全局命名空间里**什么符号都没有**的假 CDLL(None)。"""

    def __getattr__(self, name):
        raise AttributeError(name)


def _fake_so(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x7fELF-fake-" + name.encode())
    return p


def _fake_dl(monkeypatch, owners: dict) -> None:
    """给 mock 函数装上「地址 → **定义方 so**」的假 dladdr（mock 拿不到真地址，必须显式模拟）。

    ``owners`` = ``{fn 对象: 定义方 so 路径}``；**没登记**的函数 → 地址 None = 「dladdr 反查不出」，
    正是真机上无法证实/证伪来源的那种情形。
    """
    addrs, libs = {}, {}
    for i, (fn, lib) in enumerate(owners.items(), start=1):
        addr = 0x1000 * i
        addrs[id(fn)], libs[addr] = addr, lib
    monkeypatch.setattr(R, "_func_address", lambda fn: addrs.get(id(fn)))
    monkeypatch.setattr(R, "_dladdr_lib", lambda addr: libs.get(addr))


def _sym_runner(tmp_path, custom: list, *, acl=None, monkeypatch=None, defining=None,
                dut=None, **kw):
    """造一个只用来验符号解析的 runner：注入 [(so 路径, 假 handle)] + 假全局句柄，不碰 ctypes 真加载。

    ``custom`` = [(so 文件名, [该 so 暴露的符号名...])]，顺序即解析优先级。

    ``dut`` = 本次 DUT 的 so **文件名**（audit#1：严格档必须显式声明绑谁）。不给时缺省取 ``custom``
    的**第一个**（= 原来「权威 DUT 在首位」的口径）；``custom`` 为空则退化成 tmp_path 下一个未加载的
    DUT 路径——正是「声明了 DUT，但一个 custom lib 都没加载上」那种场景。

    给了 ``monkeypatch`` 就顺带装上假 dladdr：**默认**「每个 so 里的符号就由它自己定义」（= 自足的
    vendor lib，真机上 custom 算子的正常样子）；``defining`` = ``{符号名: 定义方 so 路径}`` 可覆盖某些符号
    ——用来模拟改动⑩那个洞：dlsym 经 ``libcust_opapi.so`` 命中，可实际定义在它 DT_NEEDED 的
    CANN 内置 ``libopapi_math.so`` 里。
    """
    handles = []
    for so_name, syms in custom:
        handles.append((str(_fake_so(tmp_path, so_name)), _FakeLib(syms)))
    if dut is not None:
        kw.setdefault("dut_lib", str(tmp_path / dut))
    elif kw.get("require_custom_vendor"):
        kw.setdefault("dut_lib",
                      handles[0][0] if handles else str(tmp_path / "libcust_opapi.so"))
    runner = R.AclnnRunner(**kw)
    runner._acl = _FakeAcl() if acl is None else acl
    runner._custom_handles = handles
    if monkeypatch is not None:
        override = defining or {}
        owners = {}
        for path, lib in handles:
            for sym, fn in lib._symbols.items():
                owner = override.get(sym, path)
                if owner is not None:                # None = 该符号故意反查不出
                    owners[fn] = owner
        _fake_dl(monkeypatch, owners)
    return runner


def test_resolve_symbol_prefers_custom_vendor_over_global(tmp_path, monkeypatch):
    """Bug#A 核心：custom 与全局**都有**同名符号时，必须取 custom 那个。

    真机上 CANN 自带的 libopapi.so 本身就导出 aclnnMedian* —— 从全局取 = 验的是内置实现、
    不是被测 PR 产物（同名同签名时会静默报**假 PASS**）。
    """
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnFoo", "aclnnFooGetWorkspaceSize"])],
                         monkeypatch=monkeypatch)
    path, handle = runner._custom_handles[0]
    fn = runner._resolve_symbol("aclnnFoo")
    assert fn is handle._symbols["aclnnFoo"]
    assert fn is not runner._acl.aclnnFoo            # **不是**全局那份
    rec = runner._sym_provenance["aclnnFoo"]
    assert rec["source"] == "custom_vendor"
    assert rec["defining_lib"] == str(path)          # 载重字段：dladdr 反查出的**定义方** so
    assert rec["defining_lib_verified"] is True
    assert rec["resolved_via"] == str(path)          # dlsym 走的 handle（此例二者同一）
    assert rec["lib"] == str(path)                   # provenance 指到具体哪个 .so
    assert rec["lib_size"] == Path(path).stat().st_size


def test_resolve_symbol_handle_order_is_priority(tmp_path, monkeypatch):
    """两个 custom handle：第一个没有该符号 → 取第二个，provenance 的 lib 指向第二个。"""
    runner = _sym_runner(tmp_path, [
        ("libcust_opapi_a.so", ["aclnnBarGetWorkspaceSize"]),   # 缺 aclnnBar
        ("libcust_opapi_b.so", ["aclnnBar"]),
    ], monkeypatch=monkeypatch)
    (path_a, _), (path_b, handle_b) = runner._custom_handles
    fn = runner._resolve_symbol("aclnnBar")
    assert fn is handle_b._symbols["aclnnBar"]
    rec = runner._sym_provenance["aclnnBar"]
    assert rec["lib"] == str(path_b) and rec["lib"] != str(path_a)
    assert rec["resolved_via"] == str(path_b)


def test_resolve_symbol_verified_across_vendor_libs_and_symlink(tmp_path, monkeypatch):
    """定义方 so 只要属于**已加载的 custom vendor 集合**即算证实——按 ``realpath`` 比，软链也认。

    真机 vendor 目录常经软链暴露（``$ASCEND_CUSTOM_OPP_PATH`` 指的可能是 symlink），dladdr 返回的是
    真身路径；按字面串比会把自家 vendor 误判成外来 so → 严格档误杀。
    """
    real = _fake_so(tmp_path, "libcust_opapi.so")
    link = tmp_path / "vendor_link.so"
    link.symlink_to(real)
    runner = R.AclnnRunner(require_custom_vendor=True, dut_lib=str(link))
    runner._acl = _FakeAcl()
    handle = _FakeLib(["aclnnFoo"])
    runner._custom_handles = [(str(link), handle)]           # 加载走软链
    _fake_dl(monkeypatch, {handle._symbols["aclnnFoo"]: str(real)})   # dladdr 返真身
    fn = runner._resolve_symbol("aclnnFoo")
    assert fn is handle._symbols["aclnnFoo"]
    rec = runner._sym_provenance["aclnnFoo"]
    assert rec["source"] == "custom_vendor" and rec["defining_lib_verified"] is True
    assert rec["is_dut"] is True                             # 软链别名 ≠ 换了个库
    assert rec["defining_lib"] == str(real) and rec["resolved_via"] == str(link)


def test_resolve_symbol_strict_mode_fails_closed_on_global_only(tmp_path):
    """严格档：符号只在全局/CANN 内置找得到 → raise，绝不拿它冒充 DUT（假 PASS）。"""
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnOther"])],
                         require_custom_vendor=True)
    with pytest.raises(AclnnRunnerError) as ei:
        runner._resolve_symbol("aclnnFoo")
    msg = str(ei.value)
    assert "require_custom_vendor" in msg          # 是调用方声明的严格档拦下的
    assert "假 PASS" in msg                         # 且点明后果
    assert "libcust_opapi.so" in msg
    assert runner._sym_provenance == {}             # fail-closed：不留「调过」的痕迹


#: a3 实测：libcust_opapi.so 的 DT_NEEDED 里有 CANN 内置的 libopapi_math.so，
#: 后者定义了 aclnnAbs / aclnnIsClose / aclnnSign / aclnnSort… 整个 elementwise/math 家族。
_BUILTIN_MATH_SO = "/usr/local/Ascend/cann-9.0.1/lib64/libopapi_math.so"


def test_resolve_symbol_strict_rejects_symbol_defined_in_dependency(tmp_path, monkeypatch):
    """洞 1 核心（真机 a3）：dlsym 经 vendor handle **命中了**，但定义方是它依赖的 CANN 内置 so。

    ``getattr(CDLL(libcust_opapi.so), "aclnnAbs")`` 底下的 POSIX dlsym 会沿 DT_NEEDED 依赖树继续找，
    a3 上就打到了 libopapi_math.so —— 旧实现据此记 ``source="custom_vendor"`` 且**不 raise**：
    严格档形同虚设、provenance 还把出处记错。现在必须 fail-closed。
    """
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnAbs"])],
                         monkeypatch=monkeypatch, defining={"aclnnAbs": _BUILTIN_MATH_SO},
                         require_custom_vendor=True)
    with pytest.raises(AclnnRunnerError) as ei:
        runner._resolve_symbol("aclnnAbs")
    msg = str(ei.value)
    assert "require_custom_vendor" in msg
    assert _BUILTIN_MATH_SO in msg                   # 点名真正的定义方
    assert "DT_NEEDED" in msg and "假 PASS" in msg    # 说清机理与后果
    assert runner._sym_provenance == {}               # fail-closed：不留「调过」的痕迹


def test_resolve_symbol_strict_rejects_when_defining_lib_unresolvable(tmp_path, monkeypatch):
    """定义方**反查不出** = 既证不实也证不伪 → 严格档同样 raise（宁可停，也不放行没证据的被测物）。"""
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnFoo"])],
                         monkeypatch=monkeypatch, defining={"aclnnFoo": None},
                         require_custom_vendor=True)
    with pytest.raises(AclnnRunnerError) as ei:
        runner._resolve_symbol("aclnnFoo")
    assert "反查不出" in str(ei.value)


def test_resolve_symbol_lenient_records_real_defining_lib_of_dependency(tmp_path, monkeypatch):
    """宽松档：可用，但 provenance 必须记**真实来源**（定义方 so），绝不冒充 custom vendor。"""
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnAbs"])],
                         monkeypatch=monkeypatch, defining={"aclnnAbs": _BUILTIN_MATH_SO})
    path, handle = runner._custom_handles[0]
    fn = runner._resolve_symbol("aclnnAbs")
    assert fn is handle._symbols["aclnnAbs"]          # 宽松档照跑
    rec = runner._sym_provenance["aclnnAbs"]
    assert rec["source"] == "dependency_of_custom_vendor"
    assert rec["defining_lib"] == _BUILTIN_MATH_SO    # 真实来源
    assert rec["lib"] == _BUILTIN_MATH_SO             # lib 与 defining_lib 同源
    assert rec["defining_lib_verified"] is False
    assert rec["resolved_via"] == str(path)           # dlsym 走的 handle 另记，**不是**来源
    assert runner.runtime_provenance()["symbols"][0]["defining_lib"] == _BUILTIN_MATH_SO


def test_resolve_symbol_lenient_marks_unverified_when_dladdr_blind(tmp_path, monkeypatch):
    """反查不出定义方时，宽松档记 ``custom_vendor_unverified``——不谎称已核实。"""
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnFoo"])],
                         monkeypatch=monkeypatch, defining={"aclnnFoo": None})
    runner._resolve_symbol("aclnnFoo")
    rec = runner._sym_provenance["aclnnFoo"]
    assert rec["source"] == "custom_vendor_unverified"
    assert rec["defining_lib"] is None and rec["defining_lib_verified"] is None


def test_global_conflict_alone_is_not_dut_evidence(tmp_path, monkeypatch):
    """「顺带」那条：``global_conflict=True`` **不能**单独当 DUT 证据——两边可能都是 CANN 内置。

    a3 实测 aclnnAbs 就是这样：handle 那份是沿依赖树找到的 libopapi_math.so，全局那份是另一个内置
    地址 → conflict=True，可**没有一份**出自被测 vendor。载重的是 defining_lib / defining_lib_verified。
    """
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnAbs"])])
    handle_fn = runner._custom_handles[0][1]._symbols["aclnnAbs"]
    monkeypatch.setattr(R, "_func_address", lambda fn: 0x1000 if fn is handle_fn else 0x2000)
    monkeypatch.setattr(R, "_dladdr_lib",
                        lambda addr: _BUILTIN_MATH_SO if addr == 0x1000
                        else "/usr/local/Ascend/cann-9.0.1/lib64/libopapi.so")
    runner._resolve_symbol("aclnnAbs")
    rec = runner._sym_provenance["aclnnAbs"]
    assert rec["global_conflict"] is True             # 冲突为真……
    assert rec["source"] == "dependency_of_custom_vendor"   # ……但**两边都不是** DUT
    assert rec["defining_lib_verified"] is False


def test_resolve_symbol_strict_mode_fails_closed_without_any_custom_lib(tmp_path):
    """严格档 + 一个 custom vendor lib 都没加载 → 同样 raise（不静默退全局）。"""
    runner = _sym_runner(tmp_path, [], require_custom_vendor=True)
    with pytest.raises(AclnnRunnerError) as ei:
        runner._resolve_symbol("aclnnFoo")
    assert "require_custom_vendor" in str(ei.value)


# ── 严格档必须绑定「本次 DUT」（audit#1：来自**某个** custom so ≠ 来自**本次** DUT so）────────

def test_strict_mode_without_dut_declaration_fails_closed():
    """严格档却没声明 DUT → **构造即 fail-closed**（不许「严格档却不知道该绑谁」）。

    没有 DUT 标识时，「符号来自某个 custom vendor so」只证明得了它来自环境里某个 custom so——
    ASCEND_CUSTOM_OPP_PATH / ASCEND_OPP_PATH / LD_LIBRARY_PATH 里继承来的**上次安装产物**同样算。
    """
    with pytest.raises(AclnnRunnerError) as ei:
        R.AclnnRunner(require_custom_vendor=True)
    msg = str(ei.value)
    assert "dut_lib" in msg and "dut_vendor_root" in msg
    assert "假 PASS" in msg
    assert R.AclnnRunner()._dut_lib is None          # 宽松档不受影响（跑内置算子的基线场景）


def test_dut_vendor_root_derives_lib_path(tmp_path):
    """DUT 也可按 vendor **内容根**声明：DUT so = ``<root>/op_api/lib/libcust_opapi.so``。"""
    root = tmp_path / "vendors" / "x_nn"
    runner = R.AclnnRunner(require_custom_vendor=True, dut_vendor_root=str(root))
    assert runner._dut_lib == str(root / "op_api" / "lib" / "libcust_opapi.so")


def test_dut_lib_and_vendor_root_must_agree(tmp_path):
    """两种声明都给且指向不同文件 → fail-closed（DUT 必须唯一，绝不替调用方挑一个）。"""
    root = tmp_path / "v1"
    with pytest.raises(AclnnRunnerError) as ei:
        R.AclnnRunner(require_custom_vendor=True, dut_vendor_root=str(root),
                      dut_lib=str(tmp_path / "v2" / "op_api" / "lib" / "libcust_opapi.so"))
    assert "DUT 必须唯一" in str(ei.value)
    same = str(root / "op_api" / "lib" / "libcust_opapi.so")     # 冗余但一致 → 接受
    assert R.AclnnRunner(require_custom_vendor=True, dut_vendor_root=str(root),
                         dut_lib=same)._dut_lib == same


def test_strict_rejects_stale_custom_so_when_dut_lacks_symbol(tmp_path, monkeypatch):
    """audit#1 复现：DUT 漏导该符号、环境里**继承来的陈旧 custom so** 有 → 旧版接受它 = 假 PASS。

    两个 custom handle：[0] = 本次 DUT（只有 ``aclnnBarGetWorkspaceSize``），[1] = 上次安装遗留的
    stale vendor so（自己定义了 ``aclnnBar``）。旧判据是「定义方属于**已加载的 custom vendor 集合**」
    → stale 那份照样满足 → 记 ``source=custom_vendor`` + ``defining_lib_verified=true``，
    **旧产物替本次 PR 跑完并报 PASS**，而 verified=true 又让人过度相信。现严格档只认 DUT。
    """
    runner = _sym_runner(tmp_path, [
        ("libcust_opapi.so", ["aclnnBarGetWorkspaceSize"]),       # 本次 DUT（漏了 aclnnBar）
        ("libcust_opapi_stale.so", ["aclnnBar"]),                 # 环境里继承来的陈旧产物
    ], monkeypatch=monkeypatch, require_custom_vendor=True)
    dut, stale = runner._custom_handles[0][0], runner._custom_handles[1][0]
    assert runner._dut_lib == dut
    with pytest.raises(AclnnRunnerError) as ei:
        runner._resolve_symbol("aclnnBar")
    msg = str(ei.value)
    assert dut in msg and stale in msg               # 点名期望 DUT + 实际有该符号的那个陈旧 so
    assert "别的已加载 custom vendor so" in msg       # 说清现场：旧产物冒充被测物
    assert "require_custom_vendor" in msg and "假 PASS" in msg
    assert runner._sym_provenance == {}              # fail-closed：不留「调过」的痕迹
    # 严格档不是把 custom 全拒了——DUT 自己有的符号照常解析，且标 is_dut。
    fn = runner._resolve_symbol("aclnnBarGetWorkspaceSize")
    assert fn is runner._custom_handles[0][1]._symbols["aclnnBarGetWorkspaceSize"]
    assert runner._sym_provenance["aclnnBarGetWorkspaceSize"]["is_dut"] is True


def test_lenient_marks_stale_custom_so_is_dut_false(tmp_path, monkeypatch):
    """同一场景在**宽松档**下仍可跑，但两个字段必须把话分开说清（audit#1 的误读源头）：

    ``defining_lib_verified=True``（确属**某个** custom so）而 ``is_dut=False``（**不是**本次被测物）。
    """
    runner = _sym_runner(tmp_path, [
        ("libcust_opapi.so", ["aclnnBarGetWorkspaceSize"]),
        ("libcust_opapi_stale.so", ["aclnnBar"]),
    ], monkeypatch=monkeypatch, dut="libcust_opapi.so")
    fn = runner._resolve_symbol("aclnnBar")
    assert fn is runner._custom_handles[1][1]._symbols["aclnnBar"]    # 宽松档照跑
    rec = runner._sym_provenance["aclnnBar"]
    assert rec["defining_lib_verified"] is True      # 「是某个 custom so」——不足以当 DUT 证据
    assert rec["is_dut"] is False                    # 「不是本次 DUT」——载重的是这条


def test_strict_rejects_two_stage_symbols_from_different_libs(tmp_path, monkeypatch):
    """两段式的两个符号**分属不同库** → 拒（一个出自 DUT、另一个出自内置/陈旧库都不行）。"""
    runner = _sym_runner(tmp_path, [("libcust_opapi.so",
                                     ["aclnnBarGetWorkspaceSize", "aclnnBar"])],
                         monkeypatch=monkeypatch, defining={"aclnnBar": _BUILTIN_MATH_SO},
                         require_custom_vendor=True)
    runner._resolve_symbol("aclnnBarGetWorkspaceSize")               # 这个确出自 DUT
    with pytest.raises(AclnnRunnerError) as ei:
        runner._resolve_symbol("aclnnBar")                           # 定义方是别的库 → 当场拒
    assert _BUILTIN_MATH_SO in str(ei.value)


def test_two_stage_guard_rejects_split_defining_libs(tmp_path):
    """配对约束单测（防御纵深）：即便逐符号都放行，两者不同出一个 DUT 也必须拒。"""
    dut = str(_fake_so(tmp_path, "libcust_opapi.so"))
    other = str(_fake_so(tmp_path, "libcust_opapi_stale.so"))
    runner = R.AclnnRunner(require_custom_vendor=True, dut_lib=dut)
    runner._sym_provenance = {"aclnnBarGetWorkspaceSize": {"defining_lib": dut},
                              "aclnnBar": {"defining_lib": other}}
    with pytest.raises(AclnnRunnerError) as ei:
        runner._assert_two_stage_same_dut("aclnnBarGetWorkspaceSize", "aclnnBar")
    msg = str(ei.value)
    assert dut in msg and other in msg and "假 PASS" in msg
    runner._sym_provenance["aclnnBar"] = {"defining_lib": dut}       # 同出 DUT → 放行
    runner._assert_two_stage_same_dut("aclnnBarGetWorkspaceSize", "aclnnBar")


def test_hardlink_alias_recognized_as_same_lib(tmp_path, monkeypatch):
    """audit#9：硬链接 / 同 inode 的不同可见路径是**同一个文件**，``realpath`` 合并不了。

    加载走 ``libcust_opapi.so``、``dladdr`` 却返回它的硬链接别名（加载器此前经该别名加载过同一文件）
    —— 只比 realpath 会把自家 DUT 判成外部库 → 严格档**假失败**（方向安全，但白白拦掉合法验收）。
    """
    real = _fake_so(tmp_path, "libcust_opapi.so")
    alias = tmp_path / "alias_libcust_opapi.so"
    os.link(real, alias)                                   # 硬链接：同 inode、不同 realpath
    assert os.path.realpath(alias) != os.path.realpath(real)
    assert R._same_file(str(alias), str(real)) is True     # 设备号+inode 认出同一份
    runner = R.AclnnRunner(require_custom_vendor=True, dut_lib=str(real))
    runner._acl = _FakeAcl()
    handle = _FakeLib(["aclnnFoo"])
    runner._custom_handles = [(str(real), handle)]
    _fake_dl(monkeypatch, {handle._symbols["aclnnFoo"]: str(alias)})   # dladdr 返别名
    assert runner._resolve_symbol("aclnnFoo") is handle._symbols["aclnnFoo"]
    assert runner._sym_provenance["aclnnFoo"]["is_dut"] is True


def test_same_file_stays_conservative_without_identity(tmp_path):
    """身份取不到（文件不存在 / 无权限）→ 保守判「不同」；安全方向不变，绝不「查不出就放行」。"""
    assert R._same_file(str(tmp_path / "gone_a.so"), str(tmp_path / "gone_b.so")) is False
    assert R._same_file(None, str(tmp_path)) is False
    assert R._same_file(str(tmp_path / "gone.so"), "") is False
    # realpath 相等这一支不依赖文件存在
    assert R._same_file(str(tmp_path / "gone.so"), str(tmp_path / "gone.so")) is True


def _patch_cdll(monkeypatch, tmp_path):
    """把 ``ctypes.CDLL`` 换成假加载器（记录加载顺序），让 ``_ensure_init`` 能离线跑完整流程。"""
    monkeypatch.setenv("ASCEND_TOOLKIT_HOME", str(tmp_path / "cann"))
    monkeypatch.delenv("ASCEND_OPP_PATH", raising=False)
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    loaded: list = []

    def fake_cdll(path, mode=0):
        loaded.append(path)
        return _FakeAcl() if path is None else _FakeLib([])

    monkeypatch.setattr(R.ctypes, "CDLL", fake_cdll)
    return loaded


def _stale_vendor(tmp_path):
    """造一个「环境里继承来的陈旧 vendor」内容根（上次 / 别的 PR 的安装产物）。"""
    root = tmp_path / "stale_vendor"
    (root / "op_api" / "lib").mkdir(parents=True)
    so = root / "op_api" / "lib" / "libcust_opapi.so"
    so.write_bytes(b"\x7fELF-stale")
    return root, so


def test_ensure_init_strict_loads_only_declared_dut(tmp_path, monkeypatch):
    """audit#1 的根上一刀：严格档**只加载**声明的 DUT so，环境探到的 custom so 一个都不加载。

    只要陈旧产物进了 ``_custom_handles``，本次 PR 漏导符号时就有东西能代跑。现在它们只作诊断
    记进 ``ignored_custom_opapi_libs``（既留证据，又不参与解析）。
    """
    dut = _fake_so(tmp_path, "libcust_opapi.so")
    stale_root, stale = _stale_vendor(tmp_path)
    loaded = _patch_cdll(monkeypatch, tmp_path)
    monkeypatch.setenv("ASCEND_CUSTOM_OPP_PATH", str(stale_root))
    runner = R.AclnnRunner(require_custom_vendor=True, dut_lib=str(dut))
    runner._ensure_init()
    assert str(dut) in loaded and str(stale) not in loaded
    assert runner._custom_paths() == [str(dut)]
    prov = runner.runtime_provenance()
    assert prov["dut_lib"]["path"] == str(dut)
    assert [f["path"] for f in prov["ignored_custom_opapi_libs"]] == [str(stale)]


def test_ensure_init_lenient_still_loads_env_custom_libs(tmp_path, monkeypatch):
    """宽松档不变：三源探测到的 custom so 照常加载（内置 / 无 PR 基线的老路子不受影响）。"""
    stale_root, stale = _stale_vendor(tmp_path)
    _patch_cdll(monkeypatch, tmp_path)
    monkeypatch.setenv("ASCEND_CUSTOM_OPP_PATH", str(stale_root))
    runner = R.AclnnRunner()
    runner._ensure_init()
    assert runner._custom_paths() == [str(stale)]
    assert runner.runtime_provenance()["ignored_custom_opapi_libs"] == []


def test_ensure_init_strict_missing_dut_so_fails_closed(tmp_path, monkeypatch):
    """严格档声明的 DUT so 根本不存在（没 build/install 出来）→ fail-closed，绝不拿环境里的顶替。"""
    stale_root, stale = _stale_vendor(tmp_path)
    _patch_cdll(monkeypatch, tmp_path)
    monkeypatch.setenv("ASCEND_CUSTOM_OPP_PATH", str(stale_root))
    missing = str(tmp_path / "never_built" / "libcust_opapi.so")
    runner = R.AclnnRunner(require_custom_vendor=True, dut_lib=missing)
    with pytest.raises(AclnnRunnerError) as ei:
        runner._ensure_init()
    msg = str(ei.value)
    assert missing in msg and str(stale) in msg and "fail-closed" in msg


def test_resolve_symbol_lenient_mode_falls_back_to_global(tmp_path):
    """宽松档（默认）：custom 里没有 → 退全局，正常返回并记 source="global"（跑 CANN 内置算子的场景）。"""
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnOther"])])
    fn = runner._resolve_symbol("aclnnFoo")
    assert fn is runner._acl.aclnnFoo
    rec = runner._sym_provenance["aclnnFoo"]
    assert rec["source"] == "global"
    assert runner.runtime_provenance()["strict_custom_vendor"] is False


def test_resolve_symbol_missing_everywhere_raises(tmp_path):
    """custom 没有、全局也没有 → raise（宽松档也不放过）。"""
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnOther"])], acl=_EmptyAcl())
    with pytest.raises(AclnnRunnerError) as ei:
        runner._resolve_symbol("aclnnFoo")
    assert "not found in loaded ACL libs" in str(ei.value)


def test_provenance_global_conflict_true_when_addresses_differ(tmp_path, monkeypatch):
    """``global_conflict=True`` = 全局同名符号是**另一个**实现（旧版正会打到它）。

    mock 句柄拿不到真地址（``_func_address`` 返 None）→ 必须 monkeypatch 模块级 ``_func_address``
    才测得到这条标记。
    """
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnFoo"])])
    custom_fn = runner._custom_handles[0][1]._symbols["aclnnFoo"]
    monkeypatch.setattr(R, "_func_address", lambda fn: 0x1000 if fn is custom_fn else 0x2000)
    monkeypatch.setattr(R, "_dladdr_lib",
                        lambda addr: "/usr/local/Ascend/lib64/libopapi.so" if addr else None)
    runner._resolve_symbol("aclnnFoo")
    rec = runner._sym_provenance["aclnnFoo"]
    assert rec["global_conflict"] is True
    assert rec["address"] == "0x1000" and rec["global_address"] == "0x2000"
    assert rec["global_lib"] == "/usr/local/Ascend/lib64/libopapi.so"


def test_provenance_global_conflict_false_when_same_address(tmp_path, monkeypatch):
    """custom handle 里的符号与全局解析到的是**同一个**实现 → 不算冲突。"""
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnFoo"])])
    monkeypatch.setattr(R, "_func_address", lambda fn: 0x1000)
    monkeypatch.setattr(R, "_dladdr_lib", lambda addr: None)
    runner._resolve_symbol("aclnnFoo")
    rec = runner._sym_provenance["aclnnFoo"]
    assert rec["global_conflict"] is False
    assert rec["address"] == rec["global_address"] == "0x1000"


def test_runtime_provenance_structure_and_sorted_symbols(tmp_path, monkeypatch):
    """证据链结构：字段齐全、symbols 按符号名排序、custom_opapi_libs 带指纹。"""
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnFoo", "aclnnFooGetWorkspaceSize"])],
                         monkeypatch=monkeypatch, device=5)
    runner._resolve_symbol("aclnnFooGetWorkspaceSize")     # 故意后解析的排前面
    runner._resolve_symbol("aclnnFoo")
    prov = runner.runtime_provenance()
    assert set(prov) == {"device", "strict_custom_vendor", "dut_lib", "stream_owned",
                         "device_owned", "custom_opapi_libs", "ignored_custom_opapi_libs",
                         "teardown", "symbols"}
    assert prov["device"] == 5
    assert prov["strict_custom_vendor"] is False
    assert prov["dut_lib"] is None                         # 宽松档没声明 DUT
    assert prov["stream_owned"] is False                   # 还没建过 stream
    assert prov["device_owned"] is False                   # 也没建过 device 上下文
    assert prov["ignored_custom_opapi_libs"] == []
    assert prov["teardown"] == {"closed": False, "errors": []}
    assert [s["symbol"] for s in prov["symbols"]] == ["aclnnFoo", "aclnnFooGetWorkspaceSize"]
    assert all(s["source"] == "custom_vendor" for s in prov["symbols"])
    assert all(s["is_dut"] is None for s in prov["symbols"])   # 没声明 DUT = 无从判断，别读成 True
    assert set(prov["symbols"][0]) == {
        "symbol", "source", "resolved_via", "defining_lib", "defining_lib_verified", "is_dut",
        "lib", "lib_size", "lib_mtime", "lib_sha256",
        "address", "global_address", "global_lib", "global_conflict"}
    so_path = runner._custom_handles[0][0]
    assert [f["path"] for f in prov["custom_opapi_libs"]] == [so_path]
    assert prov["custom_opapi_libs"][0]["sha256"] is None   # 默认不算 sha256（大 so 太贵）


def test_runtime_provenance_strict_flag_and_optional_sha256(tmp_path, monkeypatch):
    """严格档标记落进证据链；``hash_symbol_libs=True`` 时附 so 的 sha256。"""
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnFoo"])],
                         monkeypatch=monkeypatch,
                         require_custom_vendor=True, hash_symbol_libs=True)
    so_path = runner._custom_handles[0][0]
    runner._resolve_symbol("aclnnFoo")
    prov = runner.runtime_provenance()
    assert prov["strict_custom_vendor"] is True
    want = hashlib.sha256(Path(so_path).read_bytes()).hexdigest()
    assert prov["symbols"][0]["lib_sha256"] == want
    assert prov["custom_opapi_libs"][0]["sha256"] == want


def test_runtime_provenance_survives_close(tmp_path, monkeypatch):
    """洞 3：``close()`` 丢 handle，但 ``custom_opapi_libs`` 必须还在（否则证据链静默丢半条）。"""
    runner = _sym_runner(tmp_path, [("libcust_opapi.so", ["aclnnFoo"])], monkeypatch=monkeypatch)
    so_path = runner._custom_handles[0][0]
    runner._resolve_symbol("aclnnFoo")
    before = runner.runtime_provenance()
    runner.close()
    after = runner.runtime_provenance()
    assert [f["path"] for f in after["custom_opapi_libs"]] == [so_path]
    assert after["custom_opapi_libs"] == before["custom_opapi_libs"]
    assert after["symbols"] == before["symbols"]           # symbols 本来就在，别回归


def test_run_resolves_both_symbols_from_custom_vendor(tmp_path, monkeypatch):
    """端到端：run() 两个符号（GetWorkspaceSize + 执行）都走 custom handle，provenance 双双记上。"""
    runner, fake, made = _mock_runner(monkeypatch)
    handle = _FakeLib(["aclnnFooGetWorkspaceSize", "aclnnFoo"])
    so = str(_fake_so(tmp_path, "libcust_opapi.so"))
    runner._custom_handles = [(so, handle)]
    _fake_dl(monkeypatch, {fn: so for fn in handle._symbols.values()})
    slots = [_in_slot(np.zeros(2, np.float32)), _out_slot([2], "float32", 0)]
    runner.run("Foo", slots, signature=_FOO_1IN_1OUT)
    assert handle._symbols["aclnnFooGetWorkspaceSize"].calls == 1
    assert handle._symbols["aclnnFoo"].calls == 1
    # 全局同名符号只在记 provenance 时被**查看**（做 global_conflict 对照），一次都没被**调用**。
    assert fake._funcs["aclnnFoo"].calls == 0
    assert fake._funcs["aclnnFooGetWorkspaceSize"].calls == 0
    assert [s["symbol"] for s in runner.runtime_provenance()["symbols"]] == [
        "aclnnFoo", "aclnnFooGetWorkspaceSize"]


# ── stream 生命周期 / close（改动⑨ / 真机 Bug#C：stream 泄漏 + 无外部注入）─────────────────

def _stream_runner(monkeypatch, *, stream=None, device=0, **kw):
    """造一个已「init 完」的 runner（假 acl 句柄 + 已 setup stream），并记录 teardown 三件套的调用。"""
    runner = R.AclnnRunner(device=device, stream=stream, **kw)
    fake = _FakeAcl()
    log = {"created": 0, "destroyed": [], "reset": [], "finalized": 0}

    def create_stream(ptr):
        log["created"] += 1
        ptr._obj.value = 0x7000 + log["created"]
        return 0

    def finalize():
        log["finalized"] += 1
        return 0

    monkeypatch.setattr(fake, "aclrtCreateStream", create_stream)
    monkeypatch.setattr(fake, "aclrtDestroyStream", lambda s: log["destroyed"].append(s) or 0)
    monkeypatch.setattr(fake, "aclrtResetDevice", lambda d: log["reset"].append(d) or 0)
    monkeypatch.setattr(fake, "aclFinalize", finalize)
    runner._acl = fake
    # 走真正的 device bring-up（SetDevice → 记 device ownership → 定 stream），
    # 而不是只调 _setup_stream —— audit#6 要的正是「device ownership 独立记账」这一段。
    runner._bring_up_device(fake)
    return runner, fake, log


def test_stream_external_injected_is_used_and_never_destroyed(monkeypatch):
    """外部注入的 stream：不自建、close 时**绝不销毁别人的**，也不 reset 别人的 device 上下文。"""
    ext = ctypes.c_void_p(0x1234)
    runner, _, log = _stream_runner(monkeypatch, stream=ext, device=3)
    assert runner.owns_stream is False
    assert runner.owns_device is False               # 上下文归调用方，本 runner 不接管
    assert runner.stream is ext
    assert log["created"] == 0                       # 传了就不自建
    runner.close()
    assert log["destroyed"] == []                    # 一次都没销
    assert log["reset"] == []                        # 上下文是调用方的，不替它 reset


def test_stream_self_created_is_destroyed_and_device_reset(monkeypatch):
    """不传 stream：自建一条，close 时销毁它 + reset 自己建起来的 device 上下文（旧版一条都不销 → 泄漏）。"""
    runner, _, log = _stream_runner(monkeypatch, device=3)
    assert runner.owns_stream is True
    assert runner.owns_device is True                # device ownership **独立**记（audit#6）
    assert log["created"] == 1
    assert runner.stream.value == 0x7001
    runner.close()
    assert len(log["destroyed"]) == 1
    assert log["reset"] == [3]                       # reset 的是本 runner 的 device


def test_close_is_idempotent(monkeypatch):
    """close 幂等：连调两次不炸，销毁/reset 只发生一次。"""
    runner, _, log = _stream_runner(monkeypatch, device=1)
    runner.close()
    runner.close()
    runner.close()
    assert len(log["destroyed"]) == 1 and log["reset"] == [1]
    assert runner.stream is None and runner.owns_stream is False


def test_close_before_init_is_noop(monkeypatch):
    """从未 init 过（_acl is None）→ close 不炸、也没什么可回收。"""
    runner = R.AclnnRunner()
    runner.close()                                   # 不应抛
    assert runner.stream is None


def test_context_manager_closes_on_exit(monkeypatch):
    """with 语句退出时自动 close（销毁自建 stream）。"""
    runner, _, log = _stream_runner(monkeypatch, device=2)
    with runner as r:
        assert r is runner
        assert log["destroyed"] == []                # 还在上下文里，没销
    assert len(log["destroyed"]) == 1 and log["reset"] == [2]


def test_context_manager_does_not_swallow_exception(monkeypatch):
    """__exit__ 返回 False：清理归清理，绝不吞调用方的异常。"""
    runner, _, log = _stream_runner(monkeypatch)
    with pytest.raises(ValueError):
        with runner:
            raise ValueError("boom")
    assert len(log["destroyed"]) == 1                # 异常路径同样回收


def test_close_finalize_flag(monkeypatch):
    """aclFinalize 是进程级的（会拆同进程别的 runner / torch_npu）→ 默认不调，显式 finalize=True 才调。"""
    runner, _, log = _stream_runner(monkeypatch)
    runner.close()
    assert log["finalized"] == 0

    runner2, _, log2 = _stream_runner(monkeypatch)
    runner2.close(finalize=True)
    assert log2["finalized"] == 1


# ── device/context ownership 独立 + init 回滚（audit#6）───────────────────────────────

def test_create_stream_failure_resets_device(monkeypatch):
    """audit#6 复现：``aclrtSetDevice`` 成功、``aclrtCreateStream`` 失败 → device 上下文必须回滚。

    旧版 ``close()`` 只看 stream ownership，而此刻 ``_owns_stream`` 还是 ``False`` →
    **本 runner 建起来的 device 上下文没人 reset**（共享机上尤其要命）。
    """
    runner = R.AclnnRunner(device=4)
    fake = _FakeAcl()
    log = {"reset": [], "destroyed": []}
    monkeypatch.setattr(fake, "aclrtSetDevice", lambda d: 0)
    monkeypatch.setattr(fake, "aclrtCreateStream", lambda p: 507033)   # 建 stream 失败
    monkeypatch.setattr(fake, "aclrtResetDevice", lambda d: log["reset"].append(d) or 0)
    monkeypatch.setattr(fake, "aclrtDestroyStream", lambda s: log["destroyed"].append(s) or 0)
    runner._acl = fake
    with pytest.raises(AclnnRunnerError) as ei:
        runner._bring_up_device(fake)
    assert "aclrtCreateStream" in str(ei.value)       # 原始异常照常抛出（回滚不掩盖它）
    assert log["reset"] == [4]                        # 回滚了自己 set 的那条 device 上下文
    assert log["destroyed"] == []                     # stream 压根没建成，没得销
    assert runner.owns_device is False and runner.owns_stream is False
    assert runner.stream is None
    runner.close()
    assert log["reset"] == [4]                        # 已回滚过 → close 不重复 reset


def test_ensure_init_failure_does_not_leave_runner_half_initialized(tmp_path, monkeypatch):
    """``_bring_up_device`` 炸了就**不认** ``self._acl``——否则下次 _ensure_init 会当「已初始化」跳过。"""
    _patch_cdll(monkeypatch, tmp_path)
    monkeypatch.delenv("ASCEND_CUSTOM_OPP_PATH", raising=False)
    runner = R.AclnnRunner(device=2)
    monkeypatch.setattr(runner, "_bring_up_device",
                        lambda acl: (_ for _ in ()).throw(AclnnRunnerError("boom")))
    with pytest.raises(AclnnRunnerError):
        runner._ensure_init()
    assert runner._acl is None                        # 没有半初始化的 runner 混过去


# ── 清理 API 返回码（audit#7：ACL 用返回码报错、不抛异常）──────────────────────────────

def test_close_reports_nonzero_teardown_status(monkeypatch):
    """audit#7 复现：``aclrtDestroyStream`` 返 999——旧版整个忽略、对象照样标 closed、无任何记录。"""
    runner, fake, log = _stream_runner(monkeypatch, device=1)
    monkeypatch.setattr(fake, "aclrtDestroyStream", lambda s: 999)
    with pytest.raises(AclnnRunnerError) as ei:
        runner.close()
    msg = str(ei.value)
    assert "aclrtDestroyStream" in msg and "999" in msg
    status = runner.teardown_status()
    assert status["closed"] is True
    assert status["errors"] == [{"api": "aclrtDestroyStream", "status": 999, "error": None}]
    assert runner.runtime_provenance()["teardown"] == status          # 进证据链，可审计
    assert log["reset"] == [1]                        # 一处失败不阻断后续清理
    runner.close()                                    # 幂等：不重试、也不再抛
    assert runner.teardown_status()["errors"] == status["errors"]     # 但记录不丢


def test_close_teardown_error_raised_on_clean_exit(monkeypatch):
    """正常 with 退出（无原始异常）→ 清理失败必须报出来，别让半回收的上下文装成成功。"""
    runner, fake, _ = _stream_runner(monkeypatch)
    monkeypatch.setattr(fake, "aclrtDestroyStream", lambda s: 999)
    with pytest.raises(AclnnRunnerError, match="aclrtDestroyStream"):
        with runner:
            pass


def test_close_teardown_error_does_not_mask_original_exception(monkeypatch):
    """有原始异常在飞时，清理失败**只记账**、绝不覆盖它（否则真正的 root cause 就没了）。"""
    runner, fake, _ = _stream_runner(monkeypatch, device=1)
    monkeypatch.setattr(fake, "aclrtResetDevice", lambda d: 507899)
    with pytest.raises(ValueError, match="boom"):
        with runner:
            raise ValueError("boom")
    errs = runner.teardown_status()["errors"]
    assert [(e["api"], e["status"]) for e in errs] == [("aclrtResetDevice", 507899)]


def test_teardown_call_records_exception_and_non_int_status(monkeypatch):
    """清理 API 抛异常 / 返回值根本不是状态码——都记账，且 ``_teardown_call`` 自身绝不往外抛。"""
    runner, _, _ = _stream_runner(monkeypatch)

    def boom(_s):
        raise OSError("dlsym gone")

    runner._teardown_call("aclrtDestroyStream", boom, None)
    runner._teardown_call("aclFinalize", lambda: None)
    errs = runner.teardown_status()["errors"]
    assert [(e["api"], e["status"]) for e in errs] == [("aclrtDestroyStream", None),
                                                      ("aclFinalize", None)]
    assert "dlsym gone" in errs[0]["error"]
    assert "不是整数状态码" in errs[1]["error"]


def test_close_finalize_failure_reported(monkeypatch):
    """``aclFinalize`` 的返回码同样查（旧版这三个清理 API 的码全丢）。"""
    runner, fake, _ = _stream_runner(monkeypatch)
    monkeypatch.setattr(fake, "aclFinalize", lambda: 507899)
    with pytest.raises(AclnnRunnerError, match="aclFinalize"):
        runner.close(finalize=True)
    assert [e["api"] for e in runner.teardown_status()["errors"]] == ["aclFinalize"]


def test_closed_runner_refuses_to_run(monkeypatch):
    """close 后本 runner 不可再用（fail-closed，别在已回收的上下文上跑 kernel）。"""
    runner, _, _ = _stream_runner(monkeypatch)
    runner.close()
    with pytest.raises(AclnnRunnerError):
        runner._ensure_init()


def test_as_stream_accepts_c_void_p_and_int():
    """外部 stream 归一：c_void_p 原样、整数句柄包成 c_void_p。"""
    vp = ctypes.c_void_p(0xDEAD)
    assert R.AclnnRunner._as_stream(vp) is vp
    got = R.AclnnRunner._as_stream(0xBEEF)
    assert isinstance(got, ctypes.c_void_p) and got.value == 0xBEEF
    # 走构造参数同样成立（stream=None = 不注入、自建）。
    assert R.AclnnRunner(stream=0xBEEF)._external_stream.value == 0xBEEF
    assert R.AclnnRunner(stream=None)._external_stream is None


@pytest.mark.parametrize("bad", [True, False, "0x1234", 1.5, object(), [1], ctypes.c_int(7)])
def test_as_stream_rejects_wrong_types(bad):
    """类型闸 fail-closed：bool / 字符串 / 浮点 / 别的 ctypes 类型一律拒（别把野指针塞进 ACL）。"""
    with pytest.raises(AclnnRunnerError):
        R.AclnnRunner._as_stream(bad)
    with pytest.raises(AclnnRunnerError):            # 构造时就该炸，而不是跑到 native 才炸
        R.AclnnRunner(stream=bad)
