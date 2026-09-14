# Task 1 用例集对抗评审 → 通用规则提炼

> 来源：workflow `task1-cases-critique`（4 lens 对抗评审 LayerNormGroupedMatmulBiasSilu 草稿，2026-07-01）。
> **定位**：这些不是「修这一个算子」的清单，而是 **acc-casegen（跨算子）+ repo-adapter/harness（跨仓/跨框架）的通用规则**。
> 评审结论：golden 数学**公式正确**、shape/边界设计合理，但用例**当前一条跑不起来**——卡点全是通用的执行接线。

---

## A. acc-casegen「算子原语 → case 模式」规则库（跨算子复用）

算子 = 原语组合；每类原语挂一套 case 规则。评审揭示的规则（种子）：

| 原语 | 通用 case 规则 |
|---|---|
| **归约**（LayerNorm/Softmax/Reduce） | 归约维=1（var=0 分支）；**大归约维 fp16 平方和逼近/超 65504**（专测方差归约是否用 fp32）；行常量/近常量（var≈0，1/√(var+ε) 放大误差）；ε 鲁棒 |
| **matmul / 分组 / batch** | 大 K 累加（fp16 精度硬点）；**单轴非对齐**（M-pad / K-pad / N-pad 各一条，可定位）而非三轴同时非对齐（失败无法定位）；单/多 batch；M/N=1；**变长分组 / 空 group(M=0)** —— grouped-matmul 最典型失败，若语义为 group_list 必测 |
| **有界激活**（SiLU/GELU/…） | **两端都测** + 至少一条**强制输出走「相对误差分支」**（|D|≥1）——否则多数点 |D|<1 落绝对误差分支，`1e-3 atol 假容易`；拐点/线性区可选 |
| **elementwise / bias** | 广播、极值、大负（饱和→0）、大正 |

**通用元规则**：
1. 每条 case 显式标注：**走 compare 的哪个分支**（rel/abs）、**数据来源**（框架 data_gen / 预置 bin）、覆盖的**任务书条款**、对应的 **PR 改动**。
2. **golden 组合器**：按公式拼各原语 numpy 参考、**全程 fp32 中间、最后降输出 dtype**（本例正确，是大 K 精度的正确兜底）。落盘字节契约：按输出 layout（RowMajor (G,M,N)）、目标 dtype 小端 `tofile`，与 kernel 输出逐元素对齐。
3. **动态/分组语义必须先从 host 接口锁定**（uniform_M vs group_list）——它决定 golden 的张量表示与循环，错了所有精度用例废。

---

## B. repo-adapter / harness 通用职责（跨仓/跨框架，「跑起来」的真活）

评审证明：**难点不在生成用例，在让用例在「某仓的 kernel × 某框架(AscendOpTest)」上真跑**。这些是 generated_harness 的通用产出：

1. **bin-IO shim（critical）**：被测 kernel 若是「进程内自造数据+自校验」（catlass example 就是，`grouped_matmul.cpp` 自 fill/自 CompareData），必须改造成**按框架路径协议**读 `op_test/{op}_{case}_{ts}/input/*.bin`、接 `--case_name/--timestamp/--output_shapes`、把输出写到 `output_desc.data_path`。这是 generated_harness 的核心。
2. **layout 字节契约（critical）**：非 RowMajor 张量（本例 B ColumnMajor）**必须区分 `X_logical`（喂 golden，逻辑索引）与 `X_bin`（喂 kernel，物理字节）**，禁止复用同一 reshape——否则 golden 对、设备读到转置、**静默全错**（compare 只报大面积坏点不崩）。契约里写死每个输入的 layout→字节摆放。
3. **数据注入（critical）**：框架 data_gen 常只有 uniform 且无种子；特殊分布（负饱和/近常量/大幅值/带 seed）**由 harness 预置 bin**（走 `os.path.exists` 逃生口跳过框架生成），**同一份 bin 同时喂 kernel 与 golden**（同源）。
4. **性能测量栈（major）**：框架多半只给 kernel-only 单次采集、无多迭代取中位。1.2× 要 harness 自建：**两侧同 timing_scope**（都 `msprof --application` 抓 op_summary 自解析累加，或都 device event 计时）、**基线参考实现按目标 CANN 的真实签名适配**（如 `npu_grouped_matmul` 需 (G·M,K)+group_list、bias 独立 add、layer_norm normalized_shape=[K]/无 γβ）、warmup/iters/median 自实现。

---

## C. 性能口径（OQ2）现在更紧

评审明确：**kernel-only 会系统性低估融合「省 launch」的收益**（尤其 PF03 多专家 G=64），与任务书「整体性能」有张力 → **融合类算子倾向 device_e2e**。1.2× 分母口径必须先解冻（用户确认中），且两侧同 scope 在同一测量栈保证。

---

## D. 本夹具（这一个算子）待修（次要，等 acc-casegen 成形后作为其金标准输出）

- `ratio_dir` 注释（L68「融合/基线<1」）与权威字段（L69「基线/融合≥1.2」）方向矛盾 → 统一，防算反假通过。
- 补 `P07_silu_poscut`（bias 大正、强制 |D|≥1 走相对误差分支），与 F13 配成 SiLU 双端。
- K=1 三条（F01/F04/P06）本质同路径 → 删 F04 或 P06 之一。
- F07/P05 三轴同非对齐近重复 → 一条综合、一条改单轴非对齐。
- method 串补真：相对误差分母是 `max(|golden|,|output|)+1e-9`、`inf→±finfo.max`、`default_acc[2]=0.1` 未被消费。
- 普通用例显式设 `value_range`（默认 [0,1] 全正）。
