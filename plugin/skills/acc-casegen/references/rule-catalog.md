# acc-casegen 规则库（rule-catalog）v2

> acc-casegen 的知识库：**「算子含某原语 → 必须生成哪些测试用例」**。跨算子复用。
> canon 设计页：`Primitive-to-case rule library`。种子：夹具 LayerNormGroupedMatmulBiasSilu 评审 + rule-catalog v1 对 catlass 74 example 的对抗评审（`doc/oprunway-task1-cases-critique.md` / `doc/oprunway-rule-catalog-critique.md`）。
> 本文件是**清单**；「拆原语→查库→实例化→去重→元规则」的展开逻辑在 acc-casegen 的 SKILL.md。**阈值/口径不在此（在 policy）；本库只管「测什么形状/什么数据/为什么」。**

## 怎么用（展开算法）

1. **拆原语**：读任务书公式/功能，把算子每个功能段映射到 §2 的原语（`identify_by`）。
2. **兜底 guard（硬规则）**：**任一功能段匹配不到原语 → 产出 `UNCOVERED_PRIMITIVE` 缺口项并中止/降级，禁止静默归并进 matmul**。§2 的 `identify_by` 带排他条款。
3. **查库 + 叠三轴**：每原语拉 `mandatory`；叠加 §1 跨切面（dtype / 特殊值 / layout / 对齐 / tiling / workspace）——按 §0 三轴叠加策略，非全组合。
4. **实例化**：在**命名基准 base**（§1.0）上只改「被测轴」；`shape_rule/data_rule` 里的「大/小」一律引用 §1.1 dtype 阈值符号（禁止瞎填）。
5. **去重合并**：多原语命中同一 case → 合一条，`kind` 取并集（有 prec 即含 prec）、tolerance/timing 取最严来源、`case_origin` 记全部来源。
6. **元规则 + 覆盖报告**：打全标签、生成 golden、升级 precision、选 performance；输出覆盖矩阵（原语×tag×三轴），缺口/不可达显式列出（no silent 漏项）。

---

## 0. 元规则（对所有 case 生效）

```yaml
golden:
  compose: "按任务书公式把各原语 numpy 参考按数据流串起来"
  intermediate_dtype_from_spec: "中间精度逐段对齐算子 spec 声明的中间 dtype，不得默认一路 fp32；含中途 cast/requant 的融合（swiglu→bf16→requant、finalize_routing）必须有一条专测中间 cast 截断"
  normalization_rsqrt: "归一化的 1/sqrt 若算子用硬件 Rsqrt(牛顿迭代近似)，golden 要么建模该近似、要么 policy 按 rsqrt ULP 放宽——否则 var 两端 false-fail"
  layout_agnostic: "golden 用逻辑索引；物理字节摆放交 harness（generated_harness responsibilities 职责#2）"
dynamic_semantics_first: "分组/动态 shape（group_list vs 规则张量、变长 M、空 group）先从算子 host 接口锁定；错则 golden 全错"
per_case_tags: [id, kind, case_origin, spec_clause_ref, pr_change_ref, dtype, shape, exec_unit, arch, data_source, oracle_source, policy_id, compare_branch, workspace_size]
compare_branch_awareness: "预估输出落 compare 相对(|golden|>=1)还是绝对(<1)分支；关键路径确保有 case 落相对分支（否则 abs 阈值『假容易』）"
data_source: "普通分布用框架 data_gen（显式设 value_range、勿默认全正[0,1]）；特殊分布（近常量/饱和/大幅值/带 seed/tie）必须 harness 预置 bin（职责#3），同源喂 kernel 与 golden"
three_axis_overlay:            # 防组合爆炸 + 保证三轴进覆盖矩阵
  primary_functional_dtype: "选一个主 dtype 做全 mandatory 覆盖；其它 dtype 只在 overflow/dtype_specific/round 相关 tag 补测"
  special_value: "标 mandatory（仅对声明 inf/nan 传播语义的算子）vs optional"
  layout: "只对声明 zN/nZ/非连续的输入展开 layout_combo；ND 默认不展"
dedup_semantics: "合并 case：kind 取并集、policy 取最严、case_origin 记全部来源与各自 tag"
coverage_report: "矩阵维度 = 原语 × mandatory tag × 三轴；缺口列 UNCOVERED；物理不可达列 INFEASIBLE 附理由（见 §3 冲突消解）"
```

---

## 1. 跨切面规则

### 1.0 命名基准与扰动（保证可复现）
```yaml
base:        { M: 256, N: 256, K: 256, G: 8 }   # 均按主 dtype 对齐；规则表述为「在 base 上只改被测轴」
small: 16
big_accum: 4096          # 大累加维统一符号
xl_accum: 7168           # 极硬精度点
micro_pert: "1e-3 * |mean|"   # near_constant 行内 std≈此值
```

### 1.1 dtype 规则（阈值供 data_rule 引用）
```yaml
fp16:  { align_elems: 16, overflow_at: 65504, mantissa: 10b, accum: "cube 输入 fp16→L0C fp32 累加", notes: "误差主导=输入表示误差+乘积舍入+输出cast；累加域降精度只在 splitK/Vector 归约" }
bf16:  { align_elems: 16, overflow_at: 3.4e38, mantissa: 7b, notes: "尾数 7 位→相对精度约 fp16 的 8x，overflow 更难但 accumulation/相对误差更大；large_K/large_reduce 的 prec case 同样要覆盖 bf16" }
fp8_e4m3: { align_elems: 32, max: 448,  mantissa: 3b, notes: "饱和/量化边界主导" }
fp8_e5m2: { align_elems: 32, max: 57344, mantissa: 2b }
e8m0:  { role: "MX 共享指数(仅幂次)", clamp: "±fpX_max", notes: "block=32 的 scale" }
int8:  { align_elems: 32, range: [-128,127], compare: exact_equal }
int4:  { range: [-8,7], compare: exact_equal, notes: "饱和/解包边界" }
fp4_e2m1/e3m0: { mantissa: <=1b, notes: "量化步长边界主导" }
int32: { compare: exact_equal }
hif8:  { arch: ascend950, notes: "950 特性" }
```

### 1.2 对齐 / DMA
```yaml
align_rules:
  elem_align: "由 dtype.align_elems 推导（fp16/bf16=16、int8/fp8=32）；nonaligned 值按 dtype 取（fp16 用 17/257、int8 用 33/513）"
  dma_burst_512B: "GM↔L1 有 512B 边界；补 dma_512B_tail（元素对齐但字节非 512B 整数倍）"
```

### 1.3 特殊值
```yaml
special_value_rules:
  - { tag: zeros, why: "除零/log0/激活零点" }
  - { tag: inf_nan, mandatory_if: "算子声明 inf/nan 传播语义", why: "inf->finfo.max、NaN==NaN pass、±inf 都要" }
  - { tag: extremes, why: "上/下溢边界（引用 dtype.overflow_at/max）" }
  - { tag: cast_boundary, why: "输出 cast：fp16/bf16 round-half-even 中点 tie、刚过 overflow_at 溢为 inf" }
  - { tag: denormal, optional: true, why: "flush-to-zero" }
```

### 1.4 layout（含 Ascend 分形）
```yaml
layout_rules:
  - { tag: rowmajor_vs_colmajor, why: "非 RowMajor 须区分 X_logical(golden)/X_bin(kernel)，禁同一 reshape（职责#2）" }
  - { tag: nd_vs_nz_fractal, why: "Ascend cube 分形 zN/nZ：内块 16 对齐（fp16 16x16、int8/fp8 16x32）；golden 逻辑索引、kernel 分形物理字节，harness 写死 fractal 重排公式，否则静默全错" }
  - { tag: strided_noncontiguous, mandatory_if: "算子声明支持非连续 Tensor" }
```

### 1.5 tiling / BlockDim / workspace（Ascend 一等公民，tile/core 数作算子元数据传入）
```yaml
tiling_rules:
  - { tag: tile_exact, shape_rule: "某轴 = L1/L0 tile 尺寸整数倍" }
  - { tag: tile_plus1, shape_rule: "tile+1（尾块=1）" }
  - { tag: sub_tile, shape_rule: "< 1 个 tile（尾块短）" }
  - { tag: blockdim_vs_core, shape_rule: "CeilDiv(M,m1)*CeilDiv(N,n1) 相对核数取 <<（空转）/ =（铺满）/ >>（多波 wave）" }
workspace_awareness:
  applicability: "splitK/streamK/grouped/EVG/attention 等用 workspace 的原语"
  mandatory: "≥1 条命中 GetWorkspaceSize>0 最大分支、≥1 条 =0 退化；case 元数据记 workspace_size（harness 须按其分配，否则读越界）"
exec_unit_arch:
  exec_unit: "cube / vector / mixed（如 flash 混用；epilogue 在 vector 还是 cube fixpipe 影响 knee/sat 落点）"
  arch: "A2 / ascend950；950-only 特性(mx/fp4/hif8/evg/simt/regbase)打 arch 门控，不在 A2 实例化"
```

---

## 2. 原语规则（`identify_by` 带排他条款）

### 2.1 reduction（LayerNorm/RMSNorm/Softmax/Reduce*/L2Norm/Mean/Var）
```yaml
identify_by: "沿某维求 mean/var/sum/max/min/norm。排他：含前缀顺序依赖→cumsum；含序关系/topk→sort_topk"
mandatory:
  - { tag: reduce_dim_1, kind: [func,prec], shape_rule: "归约维=1", why: "退化：var=0→ε分支 / softmax 单元素 / mean=自身" }
  - { tag: large_reduce_dim_overflow, kind: prec, shape_rule: "归约维=big_accum", why: "fp16 平方和逼近/超 overflow_at" }
  - { tag: large_reduce_capacity, kind: func, shape_rule: "归约维超 UB k-tile", why: "多趟分块归约正确性，全 dtype，与精度正交" }
  - { tag: mixedsign_large_reduce, kind: prec, data_rule: "归约维=big_accum 且含大幅值异号", why: "分块部分和顺序≠单遍 + catastrophic cancellation（和≈0→相对误差爆炸），强制落相对分支" }
  - { tag: underflow_sum, kind: prec, data_rule: "fp16 小值累加", why: "下溢到 0" }
  - { tag: constant_rows, kind: [func,prec], data_rule: "沿归约维每行常量", why: "var=0，eps 主导" }
  - { tag: near_constant_rows, kind: prec, data_rule: "行内 std≈micro_pert", why: "var≈0，rsqrt 值极大放大误差" }
  - { tag: rsqrt_approx, kind: prec, applicability: normalization, data_rule: "var 跨 1e-6~1e6", why: "暴露硬件 Rsqrt 近似在大输出处偏离" }
  - { tag: softmax_stability, kind: [func,prec], applicability: softmax, data_rule: "全大正/全大负/uniform_row(输出≈1/N)/masked_neg_inf(exp=0)", why: "max 减法稳定性、exp 溢出、归一" }
  - { tag: strided_or_multiaxis_reduce, kind: func, shape_rule: "非末轴/多轴归约", why: "非连续归约、空归约维(size0 vs size1)" }
  - { tag: nonaligned_reduce, kind: func, shape_rule: "归约维按 dtype 非对齐", why: "尾块 padding 是否参与归约" }
```

### 2.2 matmul（GEMM/batched/grouped/带 epilogue）
```yaml
identify_by: "C=A·B（可带 α/β、epilogue）。排他：含 softmax/attention-mask/滑窗→attention；含滑窗卷积→conv；含结构化稀疏→sparse_matmul；含跨 K 原子加归约→splitk"
mandatory:
  - { tag: K_1, kind: func, case_origin: [reduction,matmul], shape_rule: "K=1（rank-1 外积）", why: "累加深度=1 退化；与 reduction.reduce_dim_1 去重" }
  - { tag: nonaligned_M/K/N, kind: func, shape_rule: "在 base 上仅一轴按 dtype 非对齐（单轴可定位）", why: "M/K/N pad 各自定位" }
  - { tag: dma_512B_tail, kind: func, why: "元素对齐但字节非512B" }
  - { tag: large_K_precision, kind: prec, shape_rule: "K=xl_accum", why: "cube fp16→fp32 累加下，大 K 放大输入表示误差与输出 cast（非累加域溢出）" }
  - { tag: large_K_capacity, kind: func, shape_rule: "K 刚超 L1 k-tile", why: "多趟 partial-combine，全 dtype，功能正确性" }
  - { tag: M_1, kind: func, why: "GEMV 行退化" }
  - { tag: N_1, kind: func, why: "列退化" }
  - { tag: beta_accumulate, kind: func, applicability: "C=αAB+βC", data_rule: "β≠0 且 C 预置 / β=0 覆盖初值", why: "读改写累加 vs 忽略初值" }
  - { tag: tile_boundary, kind: func, ref: tiling_rules, why: "落 tile 边界/尾块/多波" }
  - { tag: layout_combo, kind: func, ref: layout_rules, applicability: "A/B 有 Row/Col/zN/nZ 之分" }
grouped_extra:                 # 仅 grouped matmul
  - { tag: group_uniform_M, applicability: "规则(G,M,K)张量" }
  - { tag: group_variable_M, kind: [func,prec], applicability: group_list, why: "不等 M，golden 变长表示" }
  - { tag: group_empty_M0, applicability: group_list, why: "空组 M=0，最易崩" }
  - { tag: group_1, why: "单组退化" }
  - { tag: group_many, shape_rule: "G>=64", why: "多组调度" }
```

### 2.2b splitk_streamk（独立原语；跨核原子加归约）
```yaml
identify_by: "沿 K 切分、多核 fp32 部分和原子加/reduce 到 GM/workspace"
meta_note: "golden 单遍 fp32 归约与设备多核归约顺序不同 → tolerance 按『归约顺序无关』放宽，非收紧"
mandatory:
  - { tag: split_factor_1, kind: func, why: "退化=单遍 matmul 冒烟" }
  - { tag: vary_split_factor, kind: prec, shape_rule: "splitK=1/4/16", why: "不同 split 舍入路径，结果须 tol 内一致" }
  - { tag: split_tail, kind: func, shape_rule: "K 不整除 split 因子", why: "尾片短" }
  - { tag: atomic_determinism, kind: prec, data_rule: "大幅值异号分片", why: "原子加顺序随核调度→run-to-run 非比特一致；重复跑 N 次，差异纳入 tolerance" }
  - { tag: single_vs_multi_core, kind: prec, why: "单核 baseline 与多核一致（容差因累加序放宽）" }
  - { tag: streamk_tail_round, kind: func, applicability: streamk, shape_rule: "B%C∈(0,0.8C]", why: "尾轮/uneven tile across core" }
```

### 2.3 activation（三类互斥，先归类再取 mandatory）
```yaml
classify:
  fully_bounded: [Sigmoid, Tanh, HardSigmoid]        # 上下都有界
  semi_bounded:  [SiLU, GELU, ELU, Softplus]         # 一端有界一端线性（SiLU 下界≈-0.278、GELU 下界≈-0.17）
  unbounded:     [ReLU, LeakyReLU, Exp]
mandatory:
  fully_bounded: [sat_both_ends, knee_region, zero_point]
  semi_bounded:  # SiLU/GELU 都走这，解决 v1 GELU 归类冲突
    - { tag: sat_bounded_end, data_rule: "输入大负→饱和端(→0/→下界)" }
    - { tag: linear_end_relative, kind: [func,prec], data_rule: "输入大正→线性区，强制 |out|>=1 走相对误差分支", why: "否则全落 abs『假容易』" }
    - { tag: knee_region, kind: prec, data_rule: "输入集中拐点(x≈-1.278/0)" }
  unbounded:     [zero_crossing, large_positive_overflow, negative_region]
```

### 2.4 gated_activation（SwiGLU/GeGLU/GLU）
```yaml
identify_by: "输入沿某维切两半：一半门控激活后与另一半逐元素相乘"
mandatory:
  - { tag: split_axis_correctness, kind: func, why: "切半维/顺序锁定，错则 golden 全错" }
  - { tag: gate_saturation_both_ends, kind: [func,prec] }
  - { tag: cross_term_extremes, kind: prec, data_rule: "另一半大幅值×门控", why: "溢出" }
  - { tag: zero_gate, kind: func }
  # 与 MX 量化融合(如 65)时叠加 2.6
```

### 2.5 elementwise_binary（Add/Mul/Sub/Bias/Residual）
```yaml
identify_by: "逐元素二元；bias/residual 加法"
mandatory:
  - { tag: same_shape, kind: func }
  - { tag: broadcast, kind: func, applicability: "声明支持广播", why: "含标量/0维广播" }
  - { tag: extremes_signs, kind: [func,prec], data_rule: "大正/大负/异号", why: "抵消误差、mul 大*大溢出" }
  - { tag: inf_nan_propagate, kind: func, applicability: "声明传播", ref: special_value_rules }
  - { tag: zeros, kind: func }
```

### 2.6 quantization（Quant/Dequant/低比特 matmul）
```yaml
identify_by: "含 scale/zero_point 的量化/反量化，或低比特 matmul。排他：解包后仍是 matmul 主体→挂 2.2 + 本节叠加"
granularity_enum: [per_tensor, per_token, per_channel, per_group, per_block, dual_level, mx_block32]
mandatory:
  - { tag: granularity_each, kind: func, why: "每种粒度测 scale 广播轴与应用位置" }
  - { tag: scale_edge, kind: prec, data_rule: "scale 极大/极小(引用 dtype)" }
  - { tag: quant_saturation, kind: [func,prec], data_rule: "输入>dtype.max（int8 127/fp8 448）", why: "饱和" }
  - { tag: round_mode, kind: prec, data_rule: "量化 bin 中点 x.5 tie", why: "round-half-even(native) vs toward-zero(baseline) 差 1 ULP，须对齐算子 spec" }
  - { tag: mx_block_boundary, kind: [func,prec], applicability: mx, shape_rule: "K 非 block_size(32) 整除尾块", why: "per-block scale 边界" }
  - { tag: mx_shared_exponent, kind: prec, applicability: mx, data_rule: "e8m0 上/下溢 clamp±fpX_max" }
  - { tag: mixed_aw_dtype, kind: func, applicability: "A/W 异 dtype(A8W4/W8A16)", why: "解包对齐" }
  - { tag: dual_level_scale, kind: prec, applicability: dual_level }
  - { tag: dequant_chain_largeN, kind: prec, shape_rule: "N=big_accum，scale 跨量级", why: "dequant×scale→(bias)→requant/cast 链式舍入累积，强制相对分支" }
```

### 2.7 attention / flash-attention / MLA（catlass 最大非矩阵族）
```yaml
identify_by: "Q·Kᵀ→(scale)→(mask)→softmax→·V（含 causal/mask/paged/GQA/MLA 低秩KV）"
mandatory:
  - { tag: causal_mask, kind: func }
  - { tag: varlen_batch, kind: func, data_rule: "每 batch q/kv 不等长", why: "变长" }
  - { tag: gqa_mqa, kind: func, shape_rule: "num_heads≠kv_heads", why: "KV 广播" }
  - { tag: long_kv_context, kind: prec, shape_rule: "kv>=big_accum", why: "online-softmax 累加 + exp 溢出" }
  - { tag: q_seqlen_1_decode, kind: func, why: "decode 退化" }
  - { tag: paged_kv_block_tables, kind: func, applicability: paged }
  - { tag: head_dim_nonaligned, kind: func }
  - { tag: mask_all_masked, kind: func, data_rule: "整行 mask", why: "softmax 全0/NaN 边界" }
  - { tag: softmax_scale, kind: func }
  - { tag: mla_lowrank_kv, kind: [func,prec], applicability: MLA, why: "低秩 KV 压缩/解压" }
```

### 2.8 conv（conv1d/2d/3d）
```yaml
identify_by: "滑窗卷积"
mandatory:
  - { tag: padding_halo, kind: func, data_rule: "pad>0 补零是否入卷积" }
  - { tag: stride_gt1, kind: func }
  - { tag: dilation_gt1, kind: func }
  - { tag: kernel_1x1, kind: func, why: "退化 pointwise，与 matmul 交叉验证" }
  - { tag: channel_nonaligned, kind: func, shape_rule: "Cin/Cout 非16对齐→pad" }
  - { tag: large_cin_reduce, kind: prec, ref: "reduction.large_reduce", why: "Cin·kh·kw 归约累加" }
  - { tag: output_size_edge, kind: func, shape_rule: "(d+2p-dil*(k-1)-1)/s+1 下取整临界，某维 out=1" }
  - { tag: asymmetric_kernel, kind: func, shape_rule: "kh≠kw" }
  - { tag: layout_nchw_nhwc_5hd, kind: func, ref: layout_rules }
```

### 2.9 gather_scatter / MoE finalize_routing
```yaml
identify_by: "按索引 gather/scatter/散射累加/路由；输出清零→加权→Scatter Add。排他：非索引的规则分组→grouped matmul"
mandatory:
  - { tag: index_in_range, kind: func }
  - { tag: duplicate_index, kind: prec, why: "重复目标→atomic add 累加正确性" }
  - { tag: empty_target_row, kind: func, why: "无命中→保持清零初值，最易崩" }
  - { tag: out_of_range_index, kind: func }
  - { tag: routing_weight_sum, kind: prec, applicability: routing }
  - { tag: shared_expert_branch, kind: func, applicability: routing }
```

### 2.10 sparse_matmul（结构化 2:4）
```yaml
identify_by: "结构化稀疏 matmul（压缩 A/B + metadata/index，catlass 仅 int8）"
mandatory:
  - { tag: structured_2to4_pattern, kind: func, why: "每4元恰2非零合法性" }
  - { tag: metadata_index_correctness, kind: func, why: "索引选对元素" }
  - { tag: compressed_layout_bytes, kind: func, ref: layout_rules, why: "压缩字节契约（职责#2）" }
  - { tag: int8_exact, kind: func }
  - { tag: sparsity_boundary, kind: func, shape_rule: "K 非4整除" }
```

### 2.11 strided_batched
```yaml
identify_by: "独立 strideA/B/C 的 batch（非默认连续 batch），lda/ldb/ldc"
mandatory:
  - { tag: batch_stride_noncontig, kind: func, why: "stride≠默认" }
  - { tag: vs_plain_batch, kind: func, why: "与普通 batch_many 区分" }
```

### 2.12 占位原语（identify_by + 2-3 mandatory，防被 reduction 误吸；后续算子按需补全）
```yaml
cumsum_scan:   { identify_by: "前缀顺序依赖", mandatory: [sequential_prefix, large_len_accum_precision, mixedsign_cancel] }
sort_topk:     { identify_by: "序关系/topk", mandatory: [ties_stability, k_1, k_N, all_equal] }
transpose_permute: { identify_by: "维序重排(dtype无关字节搬运)", mandatory: [noncontig_stride, dim1_squeeze] }
pool:          { identify_by: "窗口池化", mandatory: [stride_pad, window_all_equal, window_1_degenerate] }
rope_embedding:{ identify_by: "旋转位置/查表", mandatory: [pair_rotation_values, position_0, index_out_of_range] }
```

---

## 3. 组合与产出规则

```yaml
composition:
  fusion: "融合算子 = 各原语 mandatory 并集，按数据流顺序拼 golden；上游输出分布约束下游 data_rule"
  conflict_resolution:            # 下游 mandatory 从上游输出不可达时
    - "优先经该原语自身可控输入注入（bias/weight/scale）达到目标分布"
    - "物理不可达 → coverage_report 标 INFEASIBLE 附理由，禁止静默丢"
    - "并保留一条『单原语独立』case 兜底该边界"
  dedup: "同 shape/data 命中多原语→合一条（kind 并集、policy 最严、case_origin 记全部）"
  precision_promotion: "从 functional 挑数值最硬(large_K/near_constant/mixedsign/低比特/round_mode)升级 precision，绑 tolerance_policy"
  performance_selection: "另选真实负载目标 shape 作 performance，绑 timing_policy+perf_baseline；补一条 launch-bound(多组/小K)专测融合省 launch"
  minimal_degenerate: "始终保留全最小(各维=1)冒烟"
coverage_report:
  dims: "原语 × mandatory tag × 三轴(dtype/特殊值/layout)"
  emit: "UNCOVERED_PRIMITIVE(功能段无原语) / INFEASIBLE(不可达) / gaps(tag 无 case) 全部显式列出——no silent 漏项"
```

## 4. 生长与维护

- 每次评审/真机跑测暴露的新失败模式 → 加成新 `mandatory`（注来源）。
- 新原语 → 新增 2.x 小节并更新 `identify_by` 排他条款。
- **v2 覆盖**：reduction / matmul / splitk-streamk / activation(三类) / gated_activation / elementwise / quantization(粒度谱系) / attention-MLA / conv / gather_scatter-routing / sparse / strided_batched + 5 占位。
- **仍待真机校准**：所有 kind=prec 的阈值是否稳过（尤其大 K / near_constant / MX round），M3 定；tiling/BlockDim 具体值随算子元数据。
