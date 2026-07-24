"""OpRunway 精度 golden · UpsampleNearest3d（ops-cv `image/upsample_nearest3d`）——**shape_transform 通路**。

引擎按算子从用户侧 `<ops_root>/<op>/golden.py` 加载。句式与诚实边界写法照
`plugin/samples/golden/Sign/golden.py` 抄。输出 D/H/W 由属性 `output_size`（`list[int]`，契约 C2）推导 →
按契约 C1 **必须**导出 `out_shape(in_shapes, attrs)`。

—— 算子真实语义（**实读**，2026-07-22）——
· 任务书 `repos/cann-ops-competitions/04_tasks/01_community-task-2026/docs/202605/
  UpsampleNearest3d_task_doc.md:6` 适配硬件「Atlas A2 训练系列产品/Atlas A3 系列产品」；
  `:26` self = FLOAT32/FLOAT16/BFLOAT16/**DOUBLE/UINT8**、格式 NCDHW/NDHWC/ND、**维度 5**、
  「不支持空Tensor」「shape的C、D、H、W维的size大于0」；
  `:27` outputSize「size为3，各元素取值均大于零」；`:28-30` scalesD/scalesH/scalesW（double）；
  `:31` out「输入和输出shape的N、C轴必须相同」。
· `repos/ops-cv/image/upsample_nearest3d/docs/aclnnUpsampleNearest3d.md:18` 明写
  「如果输入shape为(N, C, D, H, W)，则输出shape为(N, C, outputSize[0], outputSize[1], outputSize[2])」；
  `:26-34` 计算公式 d_src = min(floor(d_dst / scalesD), self_D - 1)，scalesD = outputSize[0] / self_D
  （h/w 同理）—— 注意**没有** +0.5 的半像素项，与 Exact 系不同。
· `op_host/upsample_nearest3d_infershape.cpp:54-56` 用 output_size 逐维盖 D/H/W、N/C 不变，与文档一致。
· `op_host/upsample_nearest3d_def.cpp:41-44`：op_def attr 为 `output_size`(ListInt, **OPTIONAL**) +
  `scale_d`/`scale_h`/`scale_w`(Float, 0.0)。⚠ 属性名是 `scale_*`（单数），不是 3d 文档/aclnn 签名里的
  `scalesD/scalesH/scalesW`，也不是 2d op 的 `scales_h/scales_w` —— 本文件按 **op_def 名**建模。

后端（ADR 0011 决策 4 · R6）：golden 恒 CPU，本文件生成期已选定 torch；运行时不兜底——torch 缺失即
fail-closed（确定性红线）。选 torch 而非 numpy 的硬理由同 UpsampleNearestExact2d：numpy 无最近邻上采样
现成 API，torch 的 `nn.functional.interpolate(mode="nearest")` 是**单调一次调用**的现成 API。
"""
import numpy as np

GOLDEN_SOURCE = "torch torch.nn.functional.interpolate(mode='nearest')"
# 判档依据（任务书原文，通篇涉及「真值/精度怎么定」的只有这一句）：
#   精度要求「算子计算精度需满足 [AscendOpTest](…) 工具默认阈值」——那是**容差**，不是**真值口径**；
#   任务概述「当前aclnnUpsampleNearest3d算子不支持unit8数据类型，需要在原来的代码上进行再开发」
#   属 impl_reference（照着谁改），**不构成 golden 授权** → 第二档回落，非第一档。
#   （另：任务书 :13 与 :19 把 uint8 拼成 "unit8"，两处皆误拼；不影响判档，已记进 spec 的 task_pr_gaps。）
GOLDEN_PROVENANCE = (
    "第二档（tier 2）·任务书未指定真值口径"
    "（UpsampleNearest3d 任务书只写精度要求「算子计算精度需满足 AscendOpTest 工具默认阈值」"
    "＝容差不是真值口径，另只给改造出处「在原来的代码上进行再开发，使其支持unit8数据类型」"
    "＋「修改 …/image/upsample_nearest3d 目录下的文件」，属 impl_reference、不构成 golden 授权）"
    "→ 回落 CPU 现成 API torch.nn.functional.interpolate(mode='nearest')；单 API 单调、按 R5 不需人核。"
    "⚠ 但**模式选型这一刀是 agent 判的、属自报，建议人核**："
    "选 'nearest'（而非 'nearest-exact'）的依据是 ① 算子名 aclnnUpsampleNearest3d 对应 PyTorch ATen 的 "
    "upsample_nearest3d；② 仓内**接口文档**（非实现代码）docs/aclnnUpsampleNearest3d.md:26-34 的公式 "
    "d_src=min(floor(d_dst/scalesD), self_D-1)（**无 +0.5 半像素项**）与 PyTorch nearest 的 "
    "src=floor(dst*in/out) 逐字相符，恰与同仓 Exact 系（有 +0.5）相反。"
    "golden 的**数值**全部由 torch 算，仓内/PR 的参考实现代码未被读作真值（R2）。"
    "边界：本 golden 只建模 output_size 驱动的通路（scale_d/scale_h/scale_w 非 0 → fail-closed）；"
    "空 Tensor 输入 → fail-closed（任务书 :26 明写「不支持空Tensor」，无真值可言）"
)

_RANK = 5                       # 任务书 :26「维度(shape) 5」；aclnn 文档报错表「self的shape不是5维」
_OUTPUT_SIZE_LEN = 3            # 任务书 :27「size为3，各元素取值均大于零」
_SCALE_ATTRS = ("scale_d", "scale_h", "scale_w")   # op_def 名（upsample_nearest3d_def.cpp:42-44）


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
            f"UpsampleNearest3d: attr output_size={v!r} 非法——须为长度 {_OUTPUT_SIZE_LEN} 的 int 列表"
            f"（任务书 UpsampleNearest3d_task_doc.md:27「size为3，各元素取值均大于零」）")
    for d in v:
        if isinstance(d, bool) or not isinstance(d, int) or d <= 0:
            raise ValueError(
                f"UpsampleNearest3d: attr output_size={v!r} 含非正整数元素（任务书 :27「各元素取值均大于零」）")
    return [int(d) for d in v]


def _check_scales(attrs):
    """本 golden 只建模 output_size 驱动的通路；scale_* 非 0 的那条通路**未建模** → fail-closed。

    理由：`upsample_nearest3d_infershape.cpp:48,58,70` 明确 output_size 与 scales **二选一**
    （"only one of attr::output_size or attr::scales should be defined as a non-empty value"），
    两者同时非空是非法输入；替它猜一个 = 造一个没有出处的真值。宁可停下。"""
    for k in _SCALE_ATTRS:
        v = attrs.get(k, 0.0)
        if v is None:
            continue
        if float(v) != 0.0:
            raise ValueError(
                f"UpsampleNearest3d: attr {k}={v!r} 非 0——本 golden 只建模 output_size 驱动的通路；"
                f"infershape 明确 output_size 与 scales 二选一，两者同时非空非法，不猜、fail-closed")


def _check_in_shape(shape):
    shape = tuple(int(d) for d in shape)
    if len(shape) != _RANK:
        raise ValueError(
            f"UpsampleNearest3d: 输入 rank={len(shape)}（shape={shape}），但该算子只支持 rank {_RANK} "
            f"(N,C,D,H,W)——任务书 UpsampleNearest3d_task_doc.md:26「维度(shape) 5」、"
            f"aclnn 文档报错表「self的shape不是5维」。spec 须声明 rank={_RANK}（契约 C3）")
    if any(d == 0 for d in shape):
        raise ValueError(
            f"UpsampleNearest3d: 输入含 0 维（shape={shape}）——任务书 :26 明写「不支持空Tensor」"
            f"「shape的C、D、H、W维的size大于0」，最近邻采样在空轴上无源像素可取、**无真值可言**，fail-closed。"
            f"⚠ 这条用例是 gen_cases `_special_entries` **无条件**强制注入的（`empty`，opbase §1.4）："
            f"引擎目前没有「本算子不支持空 Tensor」的表达位，spec 关不掉它 → 这是引擎侧缺口，"
            f"不是 golden 该靠造一个假 golden 绕过去的东西")
    return shape


def out_shape(in_shapes, attrs):
    """C1：输出形状由属性推导 —— (N, C, output_size[0], output_size[1], output_size[2])。

    出处：`repos/ops-cv/image/upsample_nearest3d/docs/aclnnUpsampleNearest3d.md:18` +
    `op_host/upsample_nearest3d_infershape.cpp:54-56`（output_size 逐维盖 D/H/W、N/C 不变）。
    ⚠ ND 格式按 NCDHW 处理（任务书 :26「当数据格式为ND时，默认按照NCDHW格式处理」）。
    """
    s = _check_in_shape(in_shapes[0])
    _check_scales(attrs)
    osz = _output_size(attrs)
    return (s[0], s[1], osz[0], osz[1], osz[2])


def golden_fn(inputs, attrs):
    t = _require_torch()
    x = np.ascontiguousarray(inputs[0])
    _check_in_shape(x.shape)
    _check_scales(attrs)
    osz = _output_size(attrs)
    y = t.nn.functional.interpolate(t.from_numpy(x), size=(osz[0], osz[1], osz[2]), mode="nearest")
    return np.ascontiguousarray(y.numpy())
