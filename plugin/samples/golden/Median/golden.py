"""OpRunway 精度 golden 样例 · Median —— **多输出 + torch 对标**形态（values + indices 双输出，torch 为真值口径）。

引擎（`gen_cases.load_golden`）按算子从用户侧 `<ops_root>/<op>/golden.py` 加载 golden。本算子有两个与
elementwise 样例不同的形态，都**据字段驱动、非按算子名特判**：
  · **双输出**：`torch.median(x, dim, keepdim)` 返回 `(values, indices)`——`golden_fn` 返回 **tuple**，
    gen_cases 逐输出落 `golden_{k}.npy`；index 输出**不逐位比下标**（tie 上 NPU 与 torch 可合法给不同下标），
    判据是 `gather(self, idx) 一致`（`index_value_consistency`，据 spec 的 `out_role=="index"` 字段派生）。
  · **同算子两种 arity**：`torch.median(x)`（无 dim）是**全局归约·单输出**（只出 values，无 indices）；
    `torch.median(x, dim)` 是**按维归约·双输出**。据 `attrs.get("dim") is None` 分派（据字段，非算子名）。
    对应 caseset 的 `expected.outputs[]` 长度随 case 变（全局=1、按维=2），validator 向后兼容处理。
  · **输出形状 ≠ 输入形状**（归约掉 dim / 全局→标量），故按契约 C1 **必须**导出 `out_shape(in_shapes, attrs)`；
    value/index 归约后同形，故 `out_shape` 返回**一个**共用形状，gen_cases 拿它与每个输出的实测形状对账。

⚠ **本文件措辞会被后续 agent 照抄** —— `GOLDEN_PROVENANCE` 必须逐字属实。判据与句式照
`plugin/samples/golden/IsClose/golden.py`（同一套，出自用户 2026-07-22 裁定）：
  · **两档链（R3）**：① 任务书指定的真值口径 → ② CPU 上的 torch/numpy 现成 API。
  · **PR / 仓里的参考实现一律禁止作 golden 源（R2）**——本算子仓里躺着 aclnn 参考实现
    `repos/ops-nn/index/gather_v2/op_api/aclnn_median.cpp`（任务书「参考实现路径」指的那份）。**本文件不引用它**：
    那是「算子该怎么实现」，不是「真值该由谁算」。真值口径由任务书**另行**指定为 torch.median。
档位判定的**唯一**实现是 `precision_policy.derive_golden_tier`；此处只抄录判定结果，不复述其逻辑。

后端（ADR 0011 决策 4 · R6「torch 优先，**生成期**选型并写死」）：生成期选定 **torch**，且此处 torch
**不是可换的偏好、是硬要求**——任务书把真值口径本身指定为 torch.median（「与torch.median功能完全对齐」），
换 numpy 就不再是「任务书指定的那个口径」了。运行时不兜底：torch 缺失即 fail-closed（确定性红线）。
"""
import numpy as np

GOLDEN_SOURCE = "torch torch.median"     # 首 token torch → oracle_source = torch_ref

# ── 声明式来源块（批 2）：任务书全文快照与本文件同处算子目录（task_doc.snapshot.md），引文按
#    `task_doc.snapshot.md:<行号>` 锚定、可被 `precision_policy.verify_authorization` 逐字复核
#    （校快照 sha256 → 校 cite 行区间 → 校 quote 是该区间逐字子串）。引文必须**逐字**摘自快照那一行。
GOLDEN_CONTRACT = {
    "source": "single_api",              # 一个现成 API（torch.median）直出，非多步自拼
    "method_kind": "torch_cpu",          # R3 第二档的可跑方法族之一（CPU 上的 torch）
    "method": "torch.median",            # 人读：到底调的哪个 API
    "authorization": {
        # 任务书**就真值口径本身**作出的指定（「参考torch.median功能…实现功能一致的算子」、
        # 「与torch.median功能完全对齐」——torch.median 即正确输出的定义）→ oracle_method → 第一档。
        # 对照 Im2col 的「参考内置 TBE 实现」属 impl_reference（照着谁重写）→ 第二档。
        "kind": "oracle_method",
        "cite": "task_doc.snapshot.md:13",
        "quote": "参考torch.median功能，在昇腾 NPU 上基于 Ascend C 编程语言实现功能一致的算子",
    },
    "taskdoc_snapshot": {"sha256": "5d24e7337d79fb5e0835df51e8fdb7d337c57e9b8a53fd6aba1296c466ef11cf"},
}
# 判档依据（Median 任务书 `docs/202604/median_task_doc.md` 原文，涉及「真值口径」两处同款）：
#   任务概述     「参考torch.median功能，在昇腾 NPU 上基于 Ascend C 编程语言实现功能一致的算子」
#   功能实现要求 1「与torch.median功能完全对齐，支持aclnnMedian、aclnnMedianDim所有走入aicore的数据类型」
# 两处都把**真值口径本身**指定为 torch.median（authorization.kind = oracle_method）→ 第一档，非回落。
# 快照已入库：同目录 `task_doc.snapshot.md`（真任务书原样拷贝），sha256 即上面契约块那串。
#   ⚠ 但 `verify_authorization` 只证「这句引文确实出自快照那几行」，**不证**「这句话算 oracle_method
#   还是 impl_reference」——那一刀仍是自报（Im2col/Median 的分野正在这一刀上，机器拦不住，靠人核）。
GOLDEN_PROVENANCE = (
    "第一档（tier 1）·任务书指定真值口径"
    "（Median 任务书任务概述：「参考torch.median功能，在昇腾 NPU 上基于 Ascend C 编程语言实现功能一致的算子」；"
    "功能实现要求同款「与torch.median功能完全对齐」）"
    "→ torch.median(CPU)。"
    "边界一：双输出——torch.median(x, dim) 返回 (values, indices)；全局 torch.median(x) 单输出（无 indices）。"
    "index 输出在并列(tie)上 NPU 与 torch 可合法给不同下标，故判据为 gather(self, idx) 值一致，非逐位比下标。"
    "边界二：偶数长度 median torch 取 lower-middle（下中位），NPU 实现是否一致须 tie 用例真机验（本文件只产 torch 口径）。"
    "边界三：空 Tensor 不支持（numel==0 → aclnn 侧 ACLNN_ERR_PARAM_INVALID）；本文件不为 numel=0 编造输出，"
    "spec 应设 allow_empty_tensor:false。"
    "边界四：生成期本机无 torch，故 fp16/bf16 在 CPU 上的 median 支持性未实测 (推断可用)"
)


def _require_torch():
    try:
        import torch
        return torch
    except Exception as e:                 # noqa: BLE001 —— 缺失/损坏一律要求安装、不静默兜底
        raise RuntimeError(
            "golden 需 torch(CPU) 作 CPU 标杆参考、但未安装/不可用。请安装 CPU 版："
            "pip install torch --index-url https://download.pytorch.org/whl/cpu。"
            "不静默回退——确定性红线（ADR 0011 决策 4）；任务书把真值口径指定为 torch.median，不可换 numpy。") from e


def _resolve_dim(dim, ndim, where):
    """把 dim 规范化为 [0, ndim) 的正轴；越界 → fail-closed（不猜、不 clamp）。"""
    d = int(dim)
    d = d if d >= 0 else d + ndim
    if not (0 <= d < ndim):
        raise ValueError(f"Median golden: {where} dim={dim} 越界（输入 ndim={ndim}）")
    return d


def out_shape(in_shapes, attrs):
    """契约 C1：声明输出形状（value/index 归约后同形，返回**一个**共用形状）。
    `attrs.get("dim") is None` → 全局归约 = 标量 ()；否则归约掉 dim（keepdim=True 时该轴保留为长度 1）。

    gen_cases 每条 case 都拿本函数返回值与 `golden_fn` 实测的**每个**输出形状对账，不一致即 fail-closed。"""
    if not in_shapes:
        raise ValueError("Median golden: out_shape 收到空的 in_shapes")
    shp = tuple(int(d) for d in in_shapes[0])
    dim = attrs.get("dim")
    if dim is None:
        return ()                                        # 全局归约 → 标量
    d = _resolve_dim(dim, len(shp), "out_shape")
    if attrs.get("keepdim"):
        return shp[:d] + (1,) + shp[d + 1:]
    return shp[:d] + shp[d + 1:]


def golden_fn(inputs, attrs):
    """`attrs.get("dim") is None` → 全局 torch.median(x)（**单输出**，返回单数组）；
    否则 torch.median(x, dim, keepdim)（**双输出**，返回 `(values, indices)` tuple）。
    据 `dim` 是否 present 分派（据字段，非算子名）。"""
    t = _require_torch()
    x = t.from_numpy(np.ascontiguousarray(inputs[0]))
    dim = attrs.get("dim")
    if dim is None:                                      # 全局归约：单输出（无 indices）
        return t.median(x).numpy()
    d = _resolve_dim(dim, x.dim(), "golden_fn")
    r = t.median(x, dim=d, keepdim=bool(attrs.get("keepdim", False)))
    return (r.values.numpy(), r.indices.numpy())         # 双输出：values + indices（下标 int64）
