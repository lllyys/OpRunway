"""重放模板：把「比对不通过」变成「差在哪一个元素上」。

ATK 的精度报告只给一行 `torch.equal failed` 或一个 matched_ratio，
归因需要的是逐元素差异——哪些位置错、错成了什么、错法有没有规律。

本模板复用 ATK 运行时契约的组件，直接用待验收算子库调 aclnn 两段式，
输入取自 `frozen_inputs/`，与精度轮同源同字节，因此差异只可能来自实现。

它是本轮的一次性诊断产物，随证据交付；不属于待验收对象，也不进 `scripts/`。
量具在验收执行期不许改，诊断脚本不受这条限制。

用法：

    source <cann>/set_env.sh
    export ATK_CUSTOM_OPP_PATH=<本轮 vendor 的 libcust_opapi.so>
    <python> replay_case.py --symbol Roll --baseline torch.roll \
        --device 5 -d frozen_inputs 5 7 11

先跑一条 ATK 判**通过**的用例做自检：它必须 equal=True。
自检不过说明重放的绑定或环境不对，这时任何失败用例的结论都不成立。

比对的另一侧只能是任务书声明的基线接口。不要拿 CANN 内置实现当参照——
实现之间谁对谁错不在验收职责内，验收结论由基线和 ATK 判定决定。

归因不能跨层外推：按 dtype、rank、参与轴条数、参数是否重复分组，
每组至少重放一条，没重放过的组写 unknown。
"""

import argparse
import ctypes
import sys

import torch

# torch 没有 uint16/uint32/uint64。ATK 落盘时把这类张量换成
# `{"__type__": "uint32_tensor", "data": ndarray}`，必须用它自己的还原函数读，
# 裸 torch.load 拿到的是 dict，当张量用会抛 AttributeError。
from atk.common.utils import torch_load_safe
from atk.tasks.backends.lib_interface.acl_wrapper import (
    ACLRuntimeError,
    AclFormat,
    AclnnManager,
    AclnnStatus,
    CPP_TO_PYTHON_TYPE,
    OpExecutor,
    ascendcl,
    get_opp_lib_path,
    nnopbase,
)

CTYPE_BY_PY = {int: ctypes.c_int64, float: ctypes.c_float, bool: ctypes.c_bool}


def load_case(frozen_dir, case_id):
    """冻结输入还原成 Python 实参，顺序与 YAML 的 inputs 一致。"""
    payload = torch_load_safe(f"{frozen_dir}/{case_id}/input.bin")
    return list(payload) if isinstance(payload, (list, tuple)) else [payload]


def to_acl(value):
    """按运行时契约把一个实参转成 acl 对象，规则同 pyaclnn 后端。

    列表 → `create_x_list`，产出哪种 Array 由**首个元素的运行时类型**决定，
    所以组内元素必须齐一（`acl_wrapper.py:441-452`）。
    """
    if torch.is_tensor(value):
        return nnopbase.create_acl_tensor(value.to("npu"), AclFormat.ACL_FORMAT_ND)
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("空组拿不到 typed null，ATK 表示不了；见能力域 reference")
        ctype = CTYPE_BY_PY[type(value[0])]
        return nnopbase.create_x_list([ctype(item) for item in value])
    if value is None:
        # 无类型空指针会被签名校验拦下。要重放可空参数就显式给 C 类型：
        # ctypes.POINTER(CPP_TO_PYTHON_TYPE["aclTensor"])()
        raise ValueError("None 实参没有 C 类型可依据，改用 typed null 并写明依据")
    return CTYPE_BY_PY[type(value)](value)


def resolve(path):
    """把 `torch.roll` 这样的点号路径解析成可调用对象。"""
    module, _, name = path.rpartition(".")
    obj = __import__(module, fromlist=[name])
    return getattr(obj, name)


def replay(args, case_id):
    py_args = load_case(args.frozen_dir, case_id)
    cpu_args = [item.cpu() if torch.is_tensor(item) else item for item in py_args]

    # 期望值同时定义了输出的 shape 与 dtype，出参照它分配，不猜。
    expect = resolve(args.baseline)(*cpu_args)
    expects = list(expect) if isinstance(expect, (list, tuple)) else [expect]

    acl_inputs = [to_acl(item) for item in py_args]
    outs = [torch.empty_like(item).to("npu") for item in expects]
    acl_outs = [nnopbase.create_acl_tensor(item, AclFormat.ACL_FORMAT_ND) for item in outs]

    def unwrap(obj):
        return obj.tensor if hasattr(obj, "tensor") else obj

    call_args = [unwrap(item) for item in acl_inputs] + [unwrap(item) for item in acl_outs]

    lib = AclnnManager(get_opp_lib_path(f"{args.symbol}GetWorkspaceSize"), "aclnn")
    # workspaceSize 与 executor 由两段式约定固定收尾，不是可选参数。
    workspace_func = lib.bind_function(
        f"aclnn{args.symbol}GetWorkspaceSize",
        [type(item) for item in call_args]
        + [ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.POINTER(OpExecutor))],
        AclnnStatus)
    run_func = lib.bind_function(
        f"aclnn{args.symbol}",
        [ctypes.c_void_p, ctypes.c_uint64, ctypes.POINTER(OpExecutor), ctypes.c_void_p],
        AclnnStatus)

    stream = ctypes.c_void_p()
    ascendcl.aclrtCreateStream(ctypes.byref(stream))
    workspace_size = ctypes.c_uint64()
    executor = ctypes.POINTER(OpExecutor)()
    status = workspace_func(*call_args, ctypes.byref(workspace_size), ctypes.byref(executor))
    if status.value != AclnnStatus.ACLNN_SUCCESS:
        raise ACLRuntimeError(f"aclnn{args.symbol}GetWorkspaceSize 返回 {status.value}")

    workspace = ctypes.c_void_p()
    if workspace_size.value > 0:
        ascendcl.aclrtMalloc(ctypes.byref(workspace), workspace_size, 0)
    status = run_func(workspace, workspace_size, executor, stream)
    if status.value != AclnnStatus.ACLNN_SUCCESS:
        raise ACLRuntimeError(f"aclnn{args.symbol} 返回 {status.value}")
    torch.npu.synchronize()

    print(f"case {case_id}: " + " ".join(
        f"{list(item.shape)}/{item.dtype}" if torch.is_tensor(item) else repr(item)
        for item in cpu_args))
    for index, (got_npu, want) in enumerate(zip(outs, expects)):
        got = got_npu.cpu()
        if torch.equal(got, want):
            print(f"  输出{index}: equal=True")
            continue
        if got.shape != want.shape or got.dtype != want.dtype:
            print(f"  输出{index}: 形态不一致 实际={list(got.shape)}/{got.dtype} "
                  f"基线={list(want.shape)}/{want.dtype}")
            continue
        diff = got != want
        print(f"  输出{index}: equal=False 不一致 {int(diff.sum())}/{got.numel()}")
        for position in diff.nonzero()[:8]:
            key = tuple(int(v) for v in position)
            print(f"    {key}: 实际={got[key]} 基线={want[key]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", required=True,
                        help="aclnn 符号名去掉 aclnn 前缀，例如 Roll")
    parser.add_argument("--baseline", required=True, help="基线调用，例如 torch.roll")
    parser.add_argument("--device", type=int, required=True, help="与精度轮同一张卡")
    parser.add_argument("-d", "--frozen-dir", default="frozen_inputs")
    parser.add_argument("cases", nargs="+", help="用例号，先放一条判通过的做对照")
    args = parser.parse_args()

    ascendcl.aclrtSetDevice(args.device)
    for case_id in args.cases:
        try:
            replay(args, case_id)
        except Exception as exc:  # noqa: BLE001 一条炸掉不影响其余用例
            print(f"case {case_id}: 异常 {exc!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
