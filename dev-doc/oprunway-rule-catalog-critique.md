# rule-catalog v1 对抗评审 → v2（提炼）

> workflow `rule-catalog-critique`（4 lens：数值/分类覆盖/Ascend适配/可用性，对照 catlass 74 example，2026-07-02）。
> 40 条 finding：4 critical + 15 major + 7 缺失原语 + 7 minor。**已全部折进 v2**（`plugin/skills/acc-casegen/references/rule-catalog.md`）。

## 三类核心问题（v1 的病）

**① 覆盖漏洞（~40% example 家族零骨架或被 matmul 过宽 identify_by 静默吞并）**
- 缺原语：attention/flash/MLA（catlass 最大非矩阵族，19/23/40/49/70/72）、conv（24/33/56）、gated_activation SwiGLU（65）、gather_scatter/finalize_routing（71）、sparse 2:4（41）、strided_batched（45）、cumsum/sort-topk/transpose/pool/rope 占位。
- 修：v2 §2 补 2.4/2.7–2.12 全部原语；**加 `UNCOVERED_PRIMITIVE` 硬 guard**（功能段匹配不到原语→报缺口/中止，禁静默归并）；matmul identify_by 加排他条款。

**② Ascend 硬件契约缺失**
- NZ/zN/nZ 分形 layout（cube 最核心，16x16/16x32 字节重排，不写死静默全错）；splitK/streamK 跨核**原子加归约**（run-to-run 非比特一致、split 因子舍入不同）；tiling/BlockDim（tile 边界/多波）；workspace（>0 vs =0）；dtype 相关对齐（int8=32 元素、512B DMA）。
- 修：v2 §1.2/1.4/1.5 补对齐/分形/tiling/workspace；§2.2b 独立 splitk_streamk 原语。

**③ 数值机理错误（会 false-fail/false-pass）**
- `large_K_accum` why 错（cube fp16→**fp32 累加**，误差是输入表示+输出 cast，非累加域溢出；大 K 还触发容量多趟 func 全 dtype）；`large_reduce` 漏 catastrophic cancellation；golden 一路 fp32 对含中途 cast/requant 融合系统性偏差；rsqrt 硬件近似未建模；bf16 归因错（尾数 7bit 相对误差 8x，非 range）；缺 MX/E8M0/round-mode/低比特 dtype。
- 修：v2 §0 golden intermediate-from-spec + rsqrt；§1.1 dtype 修正+补低比特；§2.1/2.2 why 重写 + mixedsign/underflow/rsqrt_approx；§2.6 量化粒度谱系 + round_mode + MX。

## 结构性修（可用性）
- **命名基准 base**（M=N=K=256/G=8）+ 命名扰动（micro_pert）→ 可复现（v1「其余小值/微扰」无定值）。
- **三轴叠加策略**（primary dtype 全覆盖、其余仅相关 tag）→ 防组合爆炸 + 三轴进覆盖矩阵。
- **data_rule 阈值绑 dtype**（「大」= dtype.overflow_at/max）→ LLM 可无歧义实例化。
- **composition 冲突消解**（下游不可达→注入/标 INFEASIBLE/独立兜底）+ dedup kind/policy 语义。
- **exec_unit(cube/vector)/arch(A2/950)** 标签，950 特性门控。

## 结论
v1 骨架方向对，但覆盖与数值两块不合格。v2 已补齐。**仍待真机（M3）校准**：prec 类阈值是否稳过、tiling/BlockDim 具体值。
