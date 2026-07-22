"""OpRunway 精度 golden · UpsampleNearestExact2d（ops-cv `image/upsample_nearest`）——**shape_transform 通路**。

引擎按算子从用户侧 `<ops_root>/<op>/golden.py` 加载。句式与诚实边界写法照
`plugin/samples/golden/Sign/golden.py` 抄（那 4 份是 elementwise 样例、都不导出 `out_shape`）；
本份是**第一份导出 `out_shape` 的样例**：输出 H/W 由属性 `output_size`（`list[int]`，契约 C2）推导，
不是输入广播形状 —— 故按契约 C1 **必须**导出 `out_shape(in_shapes, attrs)`，否则 gen_cases 会因
「实测输出形状 ≠ 各输入广播形状、却没声明 out_shape」当场 fail-closed。

—— 算子真实语义（**实读**，2026-07-22）——
· 任务书 `repos/cann-ops-competitions/04_tasks/01_community-task-2026/docs/202605/
  UpsampleNearestExact1d&UpsampleNearestExact2d_task_doc.md`：一份任务书**同时**覆盖两个 aclnn 接口
  （`aclnnUpsampleNearestExact1d` rank 3 / `aclnnUpsampleNearestExact2d` rank 4）。**本文件只建模 2d**。
· `:35` 2d 参数表：self = FLOAT32/FLOAT16/BFLOAT16/UINT8、格式 NCHW/NHWC/ND、**维度 4**、不支持空 Tensor；
  `:36` outputSize「size为2，取值大于零」；`:37-38` scalesH/scalesW（double，不能传负值）。
· `repos/ops-cv/image/upsample_nearest/docs/aclnnUpsampleNearestExact2d.md:18` 明写
  「如果输入shape为(N, C, H, W)，则输出shape为(N, C, outputSize[0], outputSize[1])」；`:22-30` 计算公式
  h_src = min(floor((h_dst + 0.5) / scalesH), H - 1)，scalesH = outputSize[0] / H（w 同理）。
· `repos/ops-cv/image/upsample_nearest/op_host/upsample_nearest_def.cpp:34-37`：op_def 侧算子名是
  **`UpsampleNearest`**（非 `UpsampleNearestExact2d`），attr 为 `output_size`(ListInt, REQUIRED) +
  `scales_h`/`scales_w`(Float, 0.0) + `exact_mode`(Bool, false)；1d/2d/exact 与否共用同一个 kernel op。
  `op_host/op_api/aclnn_upsample_nearest_exact2d.cpp:257,270` 调 l0op 时 `exactMode` **恒传 true**
  → 走 aclnnUpsampleNearestExact2d 这个入口时 exact_mode 不是可变参数，故本 golden 不建模该 attr。

后端（ADR 0011 决策 4 · R6「torch 优先、numpy 兜底，**生成期**选型并写死」）：golden 恒 CPU，本文件
生成期已选定 torch；**运行时**不兜底——torch 缺失即 fail-closed（确定性红线）。选 torch 而非 numpy 的
硬理由：numpy **没有**最近邻上采样的现成 API（`np.repeat` 只能整数倍、且不是 nearest-exact 的半像素规则），
而 torch 的 `nn.functional.interpolate(mode="nearest-exact")` 正是**单调一次调用**的现成 API。
"""
import numpy as np

GOLDEN_SOURCE = "torch torch.nn.functional.interpolate(mode='nearest-exact')"
# 判档依据（任务书原文，通篇涉及「真值/精度怎么定」的只有这一句）：
#   精度要求「算子计算精度需满足 [AscendOpTest](…) 工具默认阈值」——那是**容差**，不是**真值口径**；
#   任务概述/功能实现要求只说「修改 …/upsample_nearest 目录下的文件，使其支持uint8数据类型」，
#   属 impl_reference（照着谁改），**不构成 golden 授权** → 第二档回落，非第一档。
GOLDEN_PROVENANCE = (
    "第二档（tier 2）·任务书未指定真值口径"
    "（UpsampleNearestExact1d&2d 任务书只写精度要求「算子计算精度需满足 AscendOpTest 工具默认阈值」"
    "＝容差不是真值口径，另只给改造出处「修改 …/image/upsample_nearest 目录下的文件，使其支持uint8数据类型」，"
    "属 impl_reference、不构成 golden 授权）"
    "→ 回落 CPU 现成 API torch.nn.functional.interpolate(mode='nearest-exact')；单 API 单调、按 R5 不需人核。"
    "⚠ 但**模式选型这一刀是 agent 判的、属自报，建议人核**："
    "选 'nearest-exact'（而非 'nearest'）的依据是 ① 算子名 aclnnUpsampleNearestExact2d 对应 PyTorch ATen 的 "
    "upsample_nearest_exact2d；② 仓内**接口文档**（非实现代码）"
    "docs/aclnnUpsampleNearestExact2d.md:22-30 的公式 h_src=min(floor((h_dst+0.5)/scalesH), H-1) "
    "与 PyTorch nearest-exact 的半像素规则 src=floor((dst+0.5)*in/out) 逐字相符。"
    "golden 的**数值**全部由 torch 算，仓内/PR 的参考实现代码未被读作真值（R2）。"
    "边界：本 golden 只建模 output_size 驱动的通路（scales_h/scales_w 非 0 → fail-closed）；"
    "空 Tensor 输入 → fail-closed（任务书 :35 明写「不支持空Tensor」，无真值可言）"
)

_RANK = 4                       # 任务书 :35「维度(shape) 4」；aclnn 文档报错表「self的shape不是4维」
_OUTPUT_SIZE_LEN = 2            # 任务书 :36「size为2，取值大于零」
_SCALE_ATTRS = ("scales_h", "scales_w")   # op_def 名（upsample_nearest_def.cpp:35-36）


def _require_torch():
    try:
        import torch
        return torch
    except Exception as e:                 # noqa: BLE001
        raise RuntimeError(
            "golden 需 torch(CPU) 作 CPU 标杆参考、但未安装/不可用。请安装 CPU 版："
            "pip install torch --index-url https://download.pytorch.org/whl/cpu。"
            "不静默回退——确定性红线（ADR 0011 决策 4）。") from e


def _output_size(attrs):
    """取并校 `output_size`（C2 的 list[int] attr）。非法即 fail-closed，绝不替 spec 猜默认值。"""
    v = attrs.get("output_size")
    if not isinstance(v, list) or len(v) != _OUTPUT_SIZE_LEN:
        raise ValueError(
            f"UpsampleNearestExact2d: attr output_size={v!r} 非法——须为长度 {_OUTPUT_SIZE_LEN} 的 int 列表"
            f"（任务书 UpsampleNearestExact1d&2d_task_doc.md:36「size为2，取值大于零」）")
    for d in v:
        if isinstance(d, bool) or not isinstance(d, int) or d <= 0:
            raise ValueError(
                f"UpsampleNearestExact2d: attr output_size={v!r} 含非正整数元素"
                f"（任务书 :36「各元素取值大于零」）")
    return [int(d) for d in v]


def _check_scales(attrs):
    """本 golden 只建模 output_size 驱动的通路；scales 非 0 的那条通路**未建模** → fail-closed。

    理由（诚实边界，不是能力不足的遮羞布）：aclnn 文档 `约束说明` 只给出
    `outputSize_H = floor(self_H * scalesH)` 这条**一致性约束**，并未定义「scales 非 0 时 src 索引怎么算」
    与 output_size 冲突时谁优先；替它猜一个 = 造一个没有出处的真值。宁可停下。"""
    for k in _SCALE_ATTRS:
        v = attrs.get(k, 0.0)
        if v is None:
            continue
        if float(v) != 0.0:
            raise ValueError(
                f"UpsampleNearestExact2d: attr {k}={v!r} 非 0——本 golden 只建模 output_size 驱动的通路，"
                f"scales 非 0 的语义（与 output_size 冲突时谁优先）任务书与 aclnn 文档均未定义，"
                f"不猜、fail-closed")


def _check_in_shape(shape):
    shape = tuple(int(d) for d in shape)
    if len(shape) != _RANK:
        raise ValueError(
            f"UpsampleNearestExact2d: 输入 rank={len(shape)}（shape={shape}），但该算子只支持 rank {_RANK} "
            f"(N,C,H,W)——任务书 UpsampleNearestExact1d&2d_task_doc.md:35「维度(shape) 4」、"
            f"aclnn 文档报错表「self的shape不是4维」。spec 须声明 rank={_RANK}（契约 C3）")
    if any(d == 0 for d in shape):
        raise ValueError(
            f"UpsampleNearestExact2d: 输入含 0 维（shape={shape}）——任务书 :35 明写「不支持空Tensor」，"
            f"最近邻采样在空轴上无源像素可取、**无真值可言**，fail-closed。"
            f"⚠ 这条用例是 gen_cases `_special_entries` **无条件**强制注入的（`empty`，opbase §1.4）："
            f"引擎目前没有「本算子不支持空 Tensor」的表达位，spec 关不掉它 → 这是引擎侧缺口，"
            f"不是 golden 该靠造一个假 golden 绕过去的东西")
    return shape


def out_shape(in_shapes, attrs):
    """C1：输出形状由属性推导 —— (N, C, output_size[0], output_size[1])。

    出处：`repos/ops-cv/image/upsample_nearest/docs/aclnnUpsampleNearestExact2d.md:18`
    「如果输入shape为(N, C, H, W)，则输出shape为(N, C, outputSize[0], outputSize[1])」，
    与任务书 :35「out 的 shape 的 N 轴、C 轴与 self 保持一致」一致。
    ⚠ ND 格式按 NCHW 处理（任务书 :35「当数据格式为ND时，默认按照 NCHW格式处理」）——
    本 spec 的 format 轴只有 ND（gen_cases elementwise 网格不含 format 轴），故恒按 NCHW 解。
    """
    s = _check_in_shape(in_shapes[0])
    _check_scales(attrs)
    osz = _output_size(attrs)
    return (s[0], s[1], osz[0], osz[1])


def golden_fn(inputs, attrs):
    t = _require_torch()
    x = np.ascontiguousarray(inputs[0])
    _check_in_shape(x.shape)
    _check_scales(attrs)
    osz = _output_size(attrs)
    y = t.nn.functional.interpolate(t.from_numpy(x), size=(osz[0], osz[1]), mode="nearest-exact")
    return np.ascontiguousarray(y.numpy())
