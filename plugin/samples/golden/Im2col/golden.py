"""OpRunway 精度 golden 样例 · Im2col —— **shape_transform 形态**（输出形状由属性推导）。

引擎（`gen_cases.load_golden`）按算子从用户侧 `<ops_root>/<op>/golden.py` 加载 golden。
须导出 `golden_fn(inputs, attrs) -> ndarray` + `GOLDEN_SOURCE` + `GOLDEN_PROVENANCE`；
本算子**输出形状不等于输入形状**（由 kernel_size/dilation/padding/stride 推），故按契约 C1
**必须**另导出 `out_shape(in_shapes, attrs) -> tuple[int,...]`——不导出的话 gen_cases 会按缺省
「输出同各输入广播形状」当场校出不符并 fail-closed。

⚠ **本文件的措辞会被后续 agent 照抄** —— `GOLDEN_PROVENANCE` 必须逐字属实、不许含糊。
判据与句式照 `plugin/samples/golden/Sign/golden.py`（同一套，出自用户 2026-07-22 裁定）：
  · **两档链（R3）**：① 任务书指定的真值口径 → ② CPU 上的 torch/numpy 现成 API。
  · **PR / 仓里的参考实现一律禁止作 golden 源（R2）**——拿被测物当正确答案 = 自证。
    ⚠ 本算子仓里确实躺着一份 `repos/ops-math/conversion/im2col/tests/assets/golden.py`
      （被测仓自带、torch.unfold + np.pad 拼装、按 kernel 级 4 维输出写的）。**本文件没有引用它、
      也不得引用**：它落在 R2 禁区里。本文件只用了仓内的**算子文档**（README / docs/aclnnIm2col.md）
      与 **aclnn 入参校验源码** 去核对形状公式，那是「算子该怎么算」的规格、不是「真值由谁算」的来源。
  · 内置 TBE 只是 **impl_reference**（「照着谁重写」≠「真值该怎么算」）、**不构成 golden 授权** → 只能落第二档。
统一句式（照抄）：
  第一档 → "第一档（tier 1）·任务书指定真值口径（<原句摘要>）→ <backend>.<api>(CPU)"
  第二档 → "第二档（tier 2）·任务书未指定真值口径（仅 <impl_reference 内容>）→ 回落 CPU 现成 API <backend>.<api>"
档位判定的**唯一**实现是 `precision_policy.derive_golden_tier`；此处只抄录判定结果，不复述其逻辑。

后端（ADR 0011 决策 4 · R6「torch 优先、numpy 兜底，**生成期**选型并写死」）：生成期选定 **torch**，
且此处 torch **不是可换的偏好、是硬要求**——`torch.nn.functional.unfold` 就是 im2col 本身
（**单 API 单调** → R5 一级、不需人核）；换 numpy 就只能 `pad + sliding_window_view + transpose + reshape`
自拼多步，按 `derive_golden_tier` 规则 ⑧（impl_reference + multistep = 无授权却自拼）会直接判
**tier 4 · blocked**。运行时不兜底：torch 缺失即 fail-closed（确定性红线）。
"""
import numpy as np

GOLDEN_SOURCE = "torch torch.nn.functional.unfold"   # 首 token torch → oracle_source = torch_ref
# 判档依据（Im2col 任务书 `docs/202605/im2col_task_doc.md` 原文，通篇涉及「参考谁 / 怎么判」只有这三处）：
#   任务概述    「参考昇腾版本内置aclnnIm2col算子的 TBE 实现，在昇腾 NPU 上基于 Ascend C 编程语言实现
#                功能一致的算子，并且使其支持bool数据类型」          → impl_reference（照着谁重写）
#   功能实现要求 1「与原 TBE 算子核心功能完全对齐，……精度标准为二进制一致。」 → 说的是**容差**（逐位），
#                非真值口径；「与原 TBE 对齐」同样只是 impl_reference
#   精度要求    「算子计算精度需满足 AscendOpTest 工具默认阈值。」    → 说的是**阈值工具**，非真值口径
# 三处**都没有**就「真值该由谁算」作出指定（authorization.kind = impl_reference）→ **第二档回落**，非第一档。
GOLDEN_PROVENANCE = (
    "第二档（tier 2）·任务书未指定真值口径"
    "（Im2col 任务书只给参考实现出处与容差口径：「参考昇腾版本内置aclnnIm2col算子的 TBE 实现」"
    "+「与原 TBE 算子核心功能完全对齐……精度标准为二进制一致」+「需满足 AscendOpTest 工具默认阈值」，"
    "前者属 impl_reference、后两者是容差/阈值而非真值口径，均不构成 golden 授权）"
    "→ 回落 CPU 现成 API torch.nn.functional.unfold；单 API 单调、按 R5 不需人核。"
    "边界一：该 API 即 aten::im2col，入参顺序 (kernel_size, dilation, padding, stride) 与 "
    "aclnnIm2colGetWorkspaceSize 逐项同序同义，padding 为**两侧对称**填充（对齐 aclnn 的 size-2 padding，"
    "非 kernel 级 op_def 那个 size-4 的 pads）。"
    "边界二：空 Tensor 只在「4 维且 N=0」时合法，3 维含 0 维 / 4 维 C·H·W 含 0 一律被算子与 torch 双双拒绝；"
    "本文件**不为 numel=0 编造输出**——那等于替算子发明它并不支持的语义。"
    "边界三：生成期本机无 torch，故 fp16 在 CPU 上的 unfold 支持性未实测 (推断可用)"
)

# 形状公式的出处（三处独立核对一致，逐项列明，勿含糊）：
#   ① torch.nn.Unfold 官方文档的 L 公式 —— golden 就是这个 API，`out_shape` 的职责是声明**它**的输出形状；
#   ② 算子 README `repos/ops-math/conversion/im2col/README.md:18` 计算公式
#      L = ∏_d floor((spatial[d] + 2*padding[d] - dilation[d]*(kernel_size[d]-1) - 1)/stride[d] + 1)
#      与 `docs/aclnnIm2col.md:25` 同句；
#   ③ aclnn 入参校验源码 `op_api/aclnn_im2col.cpp:104-118` CheckOutputDims：
#      outputH = div_rtn(H + 2*padding[0] - (dilation[0]*(kernelSize[0]-1) + 1), stride[0]) + 1（div_rtn = 向下取整），
#      并硬性要求 outputHeight ≥ 1 且 outputWidth ≥ 1。
#   三者等价（python `//` 对整数即向下取整，与 div_rtn 同）。输出布局：3 维输入 (C,H,W) → (C*kH*kW, L)；
#   4 维输入 (N,C,H,W) → (N, C*kH*kW, L)——README:16 与 docs/aclnnIm2col.md:22 原句「展平为
#   （N, C × ∏（kernel_size）, L）的 3-D 或（C × ∏（kernel_size）, L）的 2-D 的 output 张量的列」。
_ATTR_KEYS = ("kernel_size", "dilation", "padding", "stride")


def _pair(attrs, name):
    """取一个 size-2 的 int 属性并按**任务书『算子约束限制』原文**校验；不合法即 fail-closed，不猜、不补默认。

    任务书原文：「kernelSize、dilation、padding、stride的size必须为2」「kernelSize、dilation、stride的值
    必须大于0」「padding的值不能小于0」。
    """
    if name not in attrs:
        raise ValueError(f"Im2col golden: 缺属性 {name!r}（须由 spec 的 params[io=attr].default / attr_matrix 给出，"
                         f"golden 不擅自补默认值）")
    v = attrs[name]
    if isinstance(v, (list, tuple)):
        vals = list(v)
    else:
        raise ValueError(f"Im2col golden: 属性 {name}={v!r} 须为长度 2 的 int 数组（任务书：size必须为2）")
    if len(vals) != 2 or any(isinstance(x, bool) or not isinstance(x, (int, np.integer)) for x in vals):
        raise ValueError(f"Im2col golden: 属性 {name}={v!r} 须为长度 2 的 int 数组（任务书：size必须为2）")
    vals = [int(x) for x in vals]
    lo = 0 if name == "padding" else 1
    if any(x < lo for x in vals):
        raise ValueError(f"Im2col golden: 属性 {name}={vals} 越界"
                         f"（任务书：kernelSize/dilation/stride 的值必须大于0，padding 的值不能小于0）")
    return vals


def _dims(shape):
    """把输入 shape 拆成 (batched, N, C, H, W)。任务书『算子约束限制』：输入张量的维度必须是3维或4维。"""
    shp = tuple(int(d) for d in shape)
    if len(shp) == 4:
        # 空 Tensor 的**唯一**合法形态：4 维且只有 N==0（`op_api/aclnn_im2col.cpp` CheckInputDims
        # 只放过 dim0；aten 的 im2col_shape_check 同款）。C/H/W 任一为 0 → 算子与 torch 双双拒。
        if any(d == 0 for d in shp[1:]):
            raise ValueError(
                f"Im2col golden: 4 维输入 shape={shp} 的 C/H/W 含 0 维——算子只允许 N==0 这一种空形态"
                f"（aclnn_im2col.cpp CheckInputDims 只放过 dim0）。**不为它编造输出**："
                f"那等于替算子发明它并不支持的语义。fail-closed。")
        return True, shp[0], shp[1], shp[2], shp[3]
    if len(shp) == 3:
        # 3 维要求三维全 >0（同上 CheckInputDims）。⚠ 这道闸原来漏了 —— 而本文件的 GOLDEN_PROVENANCE
        # 「边界二」白纸黑字声称「不为 numel=0 编造输出」，实测 out_shape((1,1,0)) 却返回 (4,2)。
        # **声明与实现打架，且 fail-closed 被委托给了 torch**（换个替身结论就变）。
        # 本仓明写 GOLDEN_PROVENANCE 会被后续 agent 逐字照抄——含糊一份、抄错一片，故必须由文件自己断言。
        if any(d == 0 for d in shp):
            raise ValueError(
                f"Im2col golden: 3 维输入 shape={shp} 含 0 维——算子要求三维全 >0"
                f"（空 Tensor 只在「4 维且 N==0」时合法）。**不为它编造输出**。fail-closed。")
        return False, 1, shp[0], shp[1], shp[2]
    raise ValueError(f"Im2col golden: 输入 shape={shp} 的维度是 {len(shp)}，"
                     f"任务书要求必须是 3 维 (C,H,W) 或 4 维 (N,C,H,W)")


def out_shape(in_shapes, attrs):
    """契约 C1：声明输出形状。`in_shapes` = 按 spec 顺序的输入形状列表，`attrs` = 该 case 的属性字典。

    gen_cases 每条 case 都会拿本函数的返回值与 `golden_fn` 的实测输出形状对账，不一致即 fail-closed；
    故这里**独立**按公式算（不去调 golden_fn 抄答案），两条路互相印证才有意义。
    """
    if not in_shapes:
        raise ValueError("Im2col golden: out_shape 收到空的 in_shapes")
    batched, n, c, h, w = _dims(in_shapes[0])
    kh, kw = _pair(attrs, "kernel_size")
    dh, dw = _pair(attrs, "dilation")
    ph, pw = _pair(attrs, "padding")
    sh, sw = _pair(attrs, "stride")
    out_h = (h + 2 * ph - dh * (kh - 1) - 1) // sh + 1     # `//` 向下取整 = aclnn 的 div_rtn
    out_w = (w + 2 * pw - dw * (kw - 1) - 1) // sw + 1
    if out_h < 1 or out_w < 1:
        raise ValueError(
            f"Im2col golden: 由属性推出的滑动块阵列形状 ({out_h}, {out_w}) 必须为正，"
            f"该组 (shape={tuple(in_shapes[0])}, kernel_size=[{kh},{kw}], dilation=[{dh},{dw}], "
            f"padding=[{ph},{pw}], stride=[{sh},{sw}]) 对算子非法 —— "
            f"aclnn 侧同样拒绝（op_api/aclnn_im2col.cpp CheckOutputDims: outputHeight/Width < 1 → "
            f"ACLNN_ERR_PARAM_INVALID）。fail-closed，不为非法输入编造输出形状")
    cols, blocks = c * kh * kw, out_h * out_w
    return (n, cols, blocks) if batched else (cols, blocks)


def _require_torch():
    try:
        import torch
        return torch
    except Exception as e:                 # noqa: BLE001 —— 缺失/损坏一律要求安装、不静默兜底
        raise RuntimeError(
            "golden 需 torch(CPU) 作 CPU 标杆参考、但未安装/不可用。请安装 CPU 版："
            "pip install torch --index-url https://download.pytorch.org/whl/cpu。"
            "不静默回退——确定性红线（ADR 0011 决策 4）；本算子尤其不可换 numpy 自拼"
            "（无授权 + 多步自拼 = derive_golden_tier 规则 ⑧ 判 tier 4 blocked）。") from e


def golden_fn(inputs, attrs):
    t = _require_torch()
    x = np.ascontiguousarray(inputs[0])
    # 关键字传参：torch.nn.functional.unfold(input, kernel_size, dilation, padding, stride)——
    # 位置序与 aclnn 一致，但显式写 key 免得日后哪一版改了默认值顺序而静默错位。
    y = t.nn.functional.unfold(
        t.from_numpy(x),
        kernel_size=_pair(attrs, "kernel_size"),
        dilation=_pair(attrs, "dilation"),
        padding=_pair(attrs, "padding"),
        stride=_pair(attrs, "stride"),
    )
    return np.ascontiguousarray(y.numpy())
