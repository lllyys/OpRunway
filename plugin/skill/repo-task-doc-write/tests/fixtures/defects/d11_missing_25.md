<!-- 黄金样例：以 origin/task_doc_example_v3.0.md 为底稿逐条修 D01-D12 得到，
     不是另起炉灶重写。缺陷清单见 docs/development/taskdoc-source/defect-map.md，
     逐条修复记录见 docs/development/taskdoc-source/repair-log.md「样例修复」一节。 -->

# SlidingTileAttention算子开发任务书

## 1. 任务概述

参考 FastVideo 中的 sliding_tile_attention 实现（https://github.com/hao-ai-lab/FastVideo/blob/4ddcdf541f32b63b5c684016c903658e2e2b6f67/fastvideo-kernel/python/fastvideo_kernel/ops.py#L14 ），在昇腾 NPU 上基于 Ascend C 编程语言实现功能一致的算子，完成算子设计、开发、测试全流程工作，验收通过后将算子提交至昇腾算子开源仓。

## 2. 核心开发要求

### 2.1 功能实现要求

与 FastVideo 的 sliding_tile_attention 核心功能完全对齐，支持算子对应的数据类型 bfloat16 和 float16、数据格式 BNSD。对标基线接口为 FastVideo.sliding_tile_attention。

FastVideo.sliding_tile_attention 的接口定义如下：

```python
def sliding_tile_attention(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    window_size: list,
    text_length: int,
    has_text: bool = True,
    seq_shape: str = "30x48x80",
) -> torch.Tensor:
```

**数学公式**：

对于每个 head n，给定 Q, K, V ∈ R^{B×N×S×D}，scale = 1/√D：

1. 注意力分数：score_{i,j} = (Q_i · K_j) × scale
2. 滑窗掩码 Mask_{i,j}：对于 image 段中 3D 坐标为 (i_t, i_h, i_w) 的 query 位置 i，key 位置 j（3D 坐标 (j_t, j_h, j_w)）可见当且仅当：
   - j 属于 text 段（j < text_length），或
   - j 属于 image 段且 |i_t - j_t| ≤ ⌊wt/2⌋ 且 |i_h - j_h| ≤ ⌊wh/2⌋ 且 |i_w - j_w| ≤ ⌊ww/2⌋
3. 输出：O_i = Σ_{j: Mask_{i,j}=1} softmax(score_{i,j}) · V_j

其中 (wt, wh, ww) 为 head n 的滑窗尺寸，由 window_size 指定；image 段 3D 网格尺寸 (T, H, W) 由 seq_shape 指定。

**算法说明**：

1. **序列结构**：输入序列 S 由 text 段和 image 段拼接而成。当 has_text=true 时，S = text_length + T×H×W；当 has_text=false 时，S = T×H×W（text_length 按 0 处理）。image 段 token 按 3D 网格 (T, H, W) 排列，由 seq_shape 指定（如 "30x48x80" 表示 T=30, H=48, W=80），image_seq_len = T × H × W。

2. **滑窗机制**：每个 head 拥有独立的 3D 滑窗尺寸 (wt, wh, ww)。对于 image 段中的每个 query 位置，仅与 3D 窗口范围内的 key/value 计算 attention。text 段 token 对所有 image 位置全局可见。

3. **逐 head 处理**：每个 head 独立执行滑窗 attention。window_size 长度为 1 时广播到所有 head，长度为 N（head 数）时逐 head 生效。

4. **Padding**：当 has_text=true 时，序列长度会被 padding 到 384 的倍数（通过重复末尾 token），计算完成后截断回原始长度。

5. **Text 全局注意力**：当 has_text=true 时，在逐 head 滑窗 attention 之后，额外执行一次全局 attention 覆盖 text 段。

**确定性计算要求**：算子需支持确定性计算，相同输入多次执行结果一致。判定方式见 §2.5「确定性计算要求」。

### 2.2 算子工程模式

使用 Ascend C 的aclnn算子工程化开发方式进行算子开发。

### 2.3 接口定义

```
aclnnStatus aclnnSlidingTileAttentionGetWorkspaceSize(
    const aclTensor          *q,
    const aclTensor          *k,
    const aclTensor          *v,
    aclTensor                *output,
    const aclIntArray *const *windowSize,
    uint64_t                  windowSizeLen,
    int64_t                   textLength,
    bool                      hasText,
    const char               *seqShape,
    uint64_t                 *workspaceSize,
    aclOpExecutor           **executor)

aclnnStatus aclnnSlidingTileAttention(
    void          *workspace,
    uint64_t       workspaceSize,
    aclOpExecutor *executor,
    aclrtStream    stream)
```

### 2.4 参数说明

| 参数名 | 输入／输出/属性 | 描述 | 数据类型 | dtype类型 | 数据排布格式 | 维度(shape) | 值域范围 | 异常行为 |
|---|---|----|------------|----------|----------|-----------|-----|-----|
| q | 输入 | query tensor | tensor | FLOAT16、BFLOAT16 | BNSD | [B,N,S,D] <br> q、k、v、output 必须均为 4 维 BNSD 布局，shape 完全一致。B、N、S、D 均必须大于 0。S 必须不小于 image_seq_len + textLength，其中 image_seq_len = t * h * w。输入 tensor 不支持 broadcast，不支持 q/k/v 使用不同 shape 或不同 dtype。 | [-4, 4] | dtype 非 FLOAT16/BFLOAT16 时返回 ACLNN_ERR_PARAM_INVALID；q/k/v 三者 shape 不完全一致时同样报错；S < imageSeqLen + textLength 时报错 |
| k | 输入 | key tensor | tensor | FLOAT16、BFLOAT16 | BNSD  | [B,N,S,D] <br>约束和输入q一致| [-4, 4] | dtype 非 FLOAT16/BFLOAT16 时返回 ACLNN_ERR_PARAM_INVALID；q/k/v 三者 shape 不完全一致时同样报错；S < imageSeqLen + textLength 时报错 |
| v | 输入 | value tensor | tensor | FLOAT16、BFLOAT16 | BNSD | [B,N,S,D] <br>约束和输入q一致| [-4, 4] | dtype 非 FLOAT16/BFLOAT16 时返回 ACLNN_ERR_PARAM_INVALID；q/k/v 三者 shape 不完全一致时同样报错；S < imageSeqLen + textLength 时报错 |
| output | 输出(独立输出) | attention 结果 | tensor | FLOAT16、BFLOAT16 | BNSD | [B,N,S,D] <br>约束和输入q一致| [-4, 4]（softmax 加权 v 的凸组合，不超出 v 的值域） | - |
| windowSize | 属性 | 每个 head 的 (t,h,w) 奇数窗口；长度为 1 时广播到所有 head，长度为 windowSizeLen（等于 head 数）时逐 head 生效 | list_list_int | int | - | 长度为 windowSizeLen 的 list，每项为 3 元组 (wt,wh,ww) | 每维 (wt,wh,ww) 均取 [1, 15] 内的奇数 | 元素不是奇数、或某维超出 [1,15]、或 windowSizeLen 与实际长度不匹配时报错 |
| windowSizeLen | 属性 | windowSize 数组长度，等于 windowSize 的长度 | scalar | int | - | - | [1, N]（N 为 head 数） | 长度与 head 数不匹配时报错 |
| textLength | 属性 | text token 数；hasText=false 时按 0 处理 | scalar | int | - | - | [0, S)（S 为 q/k/v 的 S 维长度）；hasText=false 时取 0 | 为负数、或 hasText=true 时 textLength ≥ S 时报错 |
| hasText | 属性 | 是否存在 text token | scalar | bool | - | - | True/False | 取值非 True/False（非法 bool 类型）时报错 |
| seqShape | 属性 | image token 三维尺寸，如 30x48x80 | string | - | - | - | 格式为 "axbxc"，a、b、c 均为 [1, 200] 内正整数 | 格式不是 axbxc、或 a×b×c 与 S 对不上时报错 |


## 3. 验收标准

### 3.1 软硬件环境要求

- **适配硬件**：A2 系列，具体型号 910B3、910B4
- **CANN 版本**：CANN 9.0.0 及以上
- **三方软件版本**：torch 2.1.0 及以上、torch_npu 2.1.0.post3 及以上；性能标杆 GPU（A100）环境使用 NVIDIA 驱动 535.104.05、CUDA 12.2

### 3.2 精度要求
1. 算子输出结果需要和FastVideo.sliding_tile_attention接口的输出一致；
2. 算子计算精度需满足生态算子开源精度标准（https://gitcode.com/cann/opbase/blob/master/docs/zh/ops_precision_standard/experimental_standard.md ）. 具体如下：

    | 数据类型 | FLOAT16 | BFLOAT16 |
    |----------|---------|----------|
    | rtol | 2^-9 (1.95e-3) | 2^-6 (1.56e-2) |
    | atol | 2^-9 (1.95e-3) | 2^-6 (1.56e-2) |
    | required_matched_ratio | 0.99 | 0.99 |
    | max_abs_error_limit | 1e-1 or 32 * ULP | 1e-0 or 32 * ULP |

    当用例同时满足 matched_ratio ≥ required_matched_ratio 且 max_abs_error ≤ max_abs_error_limit 时，判定该用例精度通过。

**随机类算子的精度对比判定策略**：不涉及——本算子不含随机数生成，§3.5 的正态分布是测试数据的生成规则，不是算子行为。

### 3.3 性能要求

1. 要求在 FLOAT16 和 BFLOAT16 输入精度场景下，910B3 与 910B4 上的平均单次耗时（Avg time，单位 us）均不高于 GPU A100 标杆平均耗时的 1.25 倍（即达到标杆 0.8 倍性能）；

2. 性能自测case如下：

| B | H | D | Img_seq    | Text_len | Window_size                           | GPU A100 标杆（Avg time，us） | 910B3 判据（≤，us） | 910B4 判据（≤，us） |
|---|---|----|------------|----------|---------------------------------------|------------------------|------------------------|------------------------|
| 1 | 8 | 64 | 30\*48\*80 | 128      | [(1,1,1), (3,3,3), (3,5,5), (5,5,5), (1,3,3), (3,3,5), (5,5,7), (7,7,7)] | 524.79 | 655.99 | 655.99 |
| 1 | 8 | 64 | 36\*48\*48 | None     | [(3,3,3)] * H                         | 4618 | 5772.50 | 5772.50 |

### 3.4 内存要求
不涉及

### 3.5 自验要求

本任务使用 AscendOpTest 工具（https://gitcode.com/HIT1920/AscendOpTest ）进行验收，请根据本任务给出的自测用例与测试指导完成自测，并输出自测报告。

自测用例与测试指导：https://gitcode.com/<org>/<repo>/tree/master/tests/sliding_tile_attention

本任务的自测用例的输入数据生成遵循如下分布规则，如果需要进行更完善的自测，请参考如下规则生成自测用例：
| 参数名 | Tensor值域分布 | Attr 覆盖规则 |
|---|---|----|
| q | (0, 1)正态分布，4 sigma 截断至 [-4, 4]，100% | - |
| k | (0, 1)正态分布，4 sigma 截断至 [-4, 4]，100% | - |
| v | (0, 1)正态分布，4 sigma 截断至 [-4, 4]，100% | - |
| output | 不生成，由算子计算得出 | - |
| windowSize |- | [1,15] 内奇数均匀取值 |
| windowSizeLen |- | 等于 windowSize 的实际长度，随 windowSize 联动生成，不单独取值 |
| textLength | - | hasText=true 时在 [0, S) 内均匀取值，hasText=false 时固定为 0 |
| hasText | - | True、False |
| seqShape | - | "axbxc", a、b、c都在[1, 200] 平均分布 |

## 4. 验收交付件

在社区任务IT系统中提交验收时， 需要提交以下交付件：

| 序号 | 交付件名称 | 交付件要求 |
|------|-----------|------------|
| 1 | 算子设计文档 | 1. 设计文档模板：https://gitcode.com/cann/cann-competitions/blob/master/04_tasks/01_community-task-2026/resources/design_template.md ；<br> 2. 在cann-competitions 仓库（https://gitcode.com/cann/cann-competitions/tree/master/04_tasks/01_community-task-2026/tasklist ）以PR形式提交设计文档，通过评审后合入仓库，详细说明见：https://gitcode.com/cann/cann-competitions/blob/master/04_tasks/01_community-task-2026/README.md|
| 2 | 自测用例及测试代码 |1. 需要清晰列出精度测试case和性能测试case；<br> 2. 测试代码中的readme文件需要说明测试步骤，保证验收人可以复现测试结果|
| 3 | 自测报告 | 1. 自测报告模板：https://docs.qq.com/sheet/DUmVWWndaUE12WGFB?tab=BB08J2 ；<br> 2. 需要包含用例参数、精度对比结果及截图、性能数据及截图 |
| 4 | 待验收代码地址 | 1. 个人代码仓链接、分支、算子目录；需要在个人仓邀请账号Ascend-CANN作为开发者，如下图所示； <br> 2. 需要根据每个仓库的规范提供算子readme文档 <br> |

![邀请示意](./pics/invite.jpeg)

## 5. PR 申请合入

测试通过后，在昇腾算子开源仓提交 PR 申请，申请将开发完成的算子合入该目录： https://gitcode.com/cann/ops-transformer/tree/master/experimental/attention。

## 6. 参考资料

1. Ascend C算子开发文档：https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/850/opdevg/Ascendcopdevg/atlas_ascendc_map_10_0002.html ；
2. 算子开发接口文档：https://www.hiascend.com/document/detail/zh/canncommercial/850/API/ascendcopapi/atlasascendc_api_07_0003.html ；
3. Ascend C在线课程：https://www.hiascend.com/developer/courses/detail/1691696509765107713 ；
4. 代码样例：https://gitcode.com/cann/ops-transformer/tree/master/experimental 。

## 7. 特别注意事项

1. 所有交付件需提前完成自验证，确认符合验收标准后再提交验收申请；
2. 开发前请务必阅读【社区任务】流程及注意事项：https://gitcode.com/org/cann/discussions/39 。

## 8. 环境获取（无需修改，使用模板原始内容）

 1. 使用 hidevlab webIDE 算力：https://hidevlab.huawei.com/online-develop-intro?from=hiascend 。
 - **【补充说明】填写示例：本人gitcode账号是 yolo，现在参与社区任务"7月社区任务-aclnnRoll算子开发"，需要申请A2/A3算力进行任务开发。**
 	 
 	![环境截图](./pics/zaixiankaifa1.png)  
 	![环境截图](./pics/apply.png)  
 	 
2. 开源仓提供100小时免费时长，请不使用时及时关闭，用时耗尽前请务必保存相关资料，建议及时提交备份。
 	 
 	![环境截图](./pics/yunkaifa.png)
 	 
3. 如需额外环境资源，请联系昇腾CANN小助手。
