"""离线单测 aclnn_runtime 子包（ctypes 打桩/mock，**不需真机**）。

覆盖：
  · parse_aclnn_signature —— 完整有序形参表（tensor-in/out + **穿插的标量属性** int64/bool/float32/float64/
    aclScalar）；aclTensorList 域外 fail-closed；末两框架参显式校验（截断头 fail-closed）；单输出向后兼容；
  · contiguous_strides / acl_dtype 覆盖 int64/int32/int8/uint8/bf16；bf16 位窄化字节数 + round-trip；
  · _find_custom_opapi_libs **可选**（无 ASCEND_OPP_PATH / 无 lib → [] 不 raise，Bug#1）；
  · AclnnRunner.run(op, slots, signature=...) —— 签名**必传**、arity/名字/ctype 全对账才进 native；
    有序 slots 拼 argtypes（median 1in+2attr+2out 穿插顺序正确）、out_null→NULL 不产出、bf16 输入窄化 +
    输出展宽、attr marshal（C float vs double 分开）、0-d 输入保 []、物理 dtype≠声明 dtype 拒、
    **每个 native 失败点都释放资源**；
  · aclnn_driver.run_driver 执行**逐 case 已解析的 aclnn_call** + 落 out_k.bin（out_null、缺 aclnn_call /
    缺属性值 / 下标错 一律 fail-closed；签名取不到 fail-closed）。

只跑本文件：``pytest plugin/acc-common/test_aclnn_runtime.py -q``。
"""

from __future__ import annotations

import ctypes
import json
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


# ── custom vendor lib 可选（Bug#1）───────────────────────────────────────────

def test_find_custom_opapi_libs_optional(monkeypatch):
    # 未 set ASCEND_OPP_PATH → 返回 [] 不 raise（内置算子照跑）。
    monkeypatch.delenv("ASCEND_OPP_PATH", raising=False)
    assert R._find_custom_opapi_libs() == []
    # set 了但目录无 custom lib → 仍 [] 不 raise。
    monkeypatch.setenv("ASCEND_OPP_PATH", "/nonexistent/opp/path")
    assert R._find_custom_opapi_libs() == []


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


def _mock_runner(monkeypatch):
    """造一个绕开 ctypes/NPU 的 AclnnRunner：假 acl 句柄 + 桩 _make_tensor/_malloc/_ck。"""
    runner = R.AclnnRunner()
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
