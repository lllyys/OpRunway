---
title: 原算子非回归验收设计（性能 + 功能）
created: 2026-08-12
status: 设计已定，代码未动。用户裁定：做 op_def + 性能双非回归；有同口径基线时才判；先写设计。
witness: Roll（aclnnRoll complex64 扩展）
---

# 原算子非回归验收设计

## 1. 问题：`measure` 回答不了任何一份任务书的性能要求

四个 witness 里三个声明了 `performance: measure`，三个都在 `unvalidated_requirements` 里写着真正的要求没验：

| 算子 | 任务书要求 | 未验证条款原文 |
|---|---|---|
| bernoulli | 性能不低于**原算子** | 仅采集本轮 NPU kernel 时间，未执行同口径原算子基线。 |
| remainder | 性能不低于**原算子** | 逐字相同 |
| gaussian_blur | OpenCV CUDA（A100）基线的 0.45× 比值 | workflow 不运行或消费 GPU 数据。 |

`measure` 产出的是**绝对** NPU kernel 时间，而任务书的性能要求全是**相对**的。一个算子跑 50 μs，不知道它是从 30 μs 退化来的还是从 80 μs 优化来的——同一个数字，两种相反结论。

所以这个维度目前的状态是：跑了，但没答上任何被问到的问题。

## 2. 缺失的概念：原算子

「原算子」= 本次改动之前就存在的实现，导出同一套公开接口。

两类相对要求都依赖它：

- **性能非回归**：新版不得比原算子慢。
- **功能非回归**：新版不得丢掉原算子已支持的能力。

工作流目前没有这个概念。第四轮 Roll 做的交叉验证是「任务书 ↔ 新算子 op_def」，从头到尾没有看过原算子。

## 3. 实证：Roll 的 INT64

Roll 的原算子与目标在**同一棵源码树**里，导出同一套 `aclnnRoll` / `aclnnRollGetWorkspaceSize`：

```
conversion/roll（原算子）        BF16 FLOAT FLOAT16 INT32 INT64 INT8 UINT32 UINT8
任务书参数表 x                    BF16 FLOAT FLOAT16 INT32       INT8 UINT32 UINT8 + COMPLEX64（新增）
experimental/math/roll（目标）    BF16 FLOAT FLOAT16 INT32       INT8 UINT32 UINT8 + COMPLEX64 + BOOL
```

两处偏差：INT64 消失（原算子有，任务书表没列，新算子没有）；BOOL 出现（原算子没有，任务书表也没列，新算子有且本轮测了它）。

任务书第 22 行写「保持原有支持的数据类型（如 float16/bfloat16/float32/int 等）功能不变」，第 86 行写「扩展 complex64 不得影响原有 dtype 功能与性能」。而参数表没有 INT64。**任务书自身存在张力**，本设计不替它裁定，只要求把差异如实暴露。

这次差异没有被任何一道门发现，第四轮照常 PASS。这是本设计存在的直接理由。

## 4. 前人做过一次，能力在重写中丢了

`dev-doc/oprunway-torch-baseline-design.md`（2026-07-24，pre-ATK 架构）的「组件⑤ perf msprof 基线」实现过双边耗时对比，真机跑过 50 例。该架构已于 2026-08-10 被 ATK 工作流替换，能力随之丢失。其教训直接沿用：

1. **有有效基线且双边 scope 同口径，才出性能裁决；否则不出，绝不冒充达标。**（原文如此，与本次裁定一致）
2. **双边 scope 必须显式校验。** 两侧测的必须是同一个东西，不同就 fail-closed。
3. **基线自己会失败。** 50 例中有 2 例因基线侧报错而无法比较，这类失败不得记在 DUT 头上。
4. **精度先筛。** 只对精度已通过的 case 测性能；给错误结果计时没有意义。
5. **不要自动免测 trivial case。** 该分类曾被移除并重算。

本次与旧设计的关键差别：旧设计的基线是 **torch_npu**（另一个框架的实现），跨框架，scope 对齐困难；本设计的基线是**同仓同接口的原算子**，走同一套 ATK harness、同一份 caseset，scope 天然一致。

## 5. spec 契约扩展

新增可选对象 `task.baseline`。缺省即无基线。

```json
"task": {
  "baseline": {
    "source_subdir": "conversion/roll",
    "build_token": "roll",
    "performance_tolerance": {"max_slowdown_ratio": 1.05}
  }
}
```

| 字段 | 含义 | 约束 |
|---|---|---|
| `source_subdir` | 原算子在**同一棵源码树**内的子目录 | 必须与 `operator.source_subdir` 不同；两者必须导出同一套公开 ACLNN 符号，否则流程错误 |
| `build_token` | 原算子的 build target | 与目标算子同一个 build profile |
| `performance_tolerance.max_slowdown_ratio` | 允许的最大放慢比 | **必须来自任务书**。任务书没有给出容差时不得自拟——省略该字段，性能只测不判 |

不引入 `--baseline-source-root`。原算子在同一棵源码树内是当前唯一见证的形态；出现第二种形态（原算子在另一个仓）之前不提前抽象。

## 6. 性能维度的表达模型

裁定为「永远测、有同口径基线时才判」，因此 `dimensions.performance` 的语义变化：

- 取值 `none` 的原义是「不测」，与「永远测」冲突，应废止。
- 判定权不再由任务书措辞决定，而由**基线是否存在**决定。

新语义：

| 情形 | 采数据 | 出裁决 |
|---|---|---|
| 无 `task.baseline` | 是 | 否——比值记 UNVALIDATED |
| 有 `task.baseline`，无 `performance_tolerance` | 是（双边） | 否——两侧数字都列出，不写达标与否 |
| 有 `task.baseline` + `performance_tolerance` | 是（双边） | 是 |
| 任务书以 GPU 或外部实现为标杆 | 是（仅本机） | **永远否**——仓规 §1 禁止连接、运行、采集、消费 GPU 数据 |

「永远测」不等于全量测。全量 caseset 仍只跑 accuracy；性能只跑 `performance_required_cases` 选中的代表子集。因此原先声明 `none` 的算子需要补选代表 case，而不是把 225 个 case 全部加测。

## 7. 执行形态与证据

一次 accept 内，同一张卡、同一份 caseset、同一个 ATK 可执行：

```
staging（一棵源码树）
  ├─ build target = operator.build_token   → install_A → ELF_A
  └─ build target = baseline.build_token   → install_B → ELF_B
同一 cases.json
  ├─ ELF_A 上跑 performance_device + profiler  → 时间 A
  └─ ELF_B 上跑 performance_device + profiler  → 时间 B
```

必须落盘并绑定的证据：

- 两次 build 的返回码、install 树、CMake target 事实；
- 两个 vendor ELF 的 SHA-256 与双符号 `nm` 证据；
- 两侧 ATK 实际加载的 ELF（从 ATK 自己的日志解析，不采信声明）；
- 两侧的原始 CANN profiler `op_statistic` / `op_summary` CSV；
- 两侧的 timing scope 标识，且**必须相同**；
- 逐 case 的 A/B 时间与比值。

## 8. 判定规则

**性能非回归。** 同时满足下列条件才出裁决：两侧 build 与执行都在本轮同一 session 内完成；两侧 scope 相同；该 case 的精度已通过；`performance_tolerance` 存在。判据为 `B_new ≤ B_old × max_slowdown_ratio`。

不满足时的归属，逐项 fail-closed：

| 情形 | 归属 |
|---|---|
| 基线侧 build 或执行失败 | **不是** DUT 失败。记为该 case 的性能证据缺失，比值 UNVALIDATED |
| 两侧 scope 不同 | 流程错误，不出性能裁决 |
| 精度未通过的 case | 不参与性能判定 |
| 无容差声明 | 只列数字，UNVALIDATED |
| 满足全部条件且超出容差 | 数值不匹配，按仓规 §4 属 `DUT_FAIL` |

最后一条要谨慎使用：性能测量有噪声，把它升格为 `DUT_FAIL` 的前提是容差由任务书给出、双边同口径、且证据完整。任何一项不成立都退回 UNVALIDATED，不得凭经验自拟阈值。

**功能非回归。** 这一项不需要第二次执行，build 前读两边 op_def 即可：

- 计算原算子与目标算子在 dtype 集合、SoC 集合上的差集；
- 目标算子**少掉**原算子已有的项，是回归候选；
- 目标算子**多出**的项，如实记录，不作判定。

差集非空时的处理：**不自行裁定，如实暴露三方对比（原算子 op_def / 任务书条款 / 目标算子 op_def）**。任务书条款与原算子能力冲突时（Roll 的 INT64 即此形），记 `NEEDS_INPUT` 或写入 `unvalidated_requirements`，由调用方澄清。仓规 §1 规定任务书是权威，但权威内部自相矛盾时，工作流的职责是暴露而不是选边。

## 9. 明确不做

- 不连接、不运行、不采集、不消费 GPU 数据。GPU 比值永远 UNVALIDATED。
- 不做内存、显存、workspace、带宽的验收。仓规 §5 规定资源不是第三验收维度。
- 不用 NPU 绝对时间宣称任何比值达标。
- 不自拟性能容差。
- 不在原算子缺席时伪造基线（例如拿上一轮的旧数据、拿另一张卡的数据）。

## 10. 已知代价与未决问题

**代价。** 每轮多一次 build 与一次执行。以 Roll 为参照，build 约 105 秒、performance 执行取决于代表 case 数量，总预算 7200 秒仍宽裕（第四轮实际用了 589 秒）。原先声明 `none` 的算子从「零性能开销」变为「有开销」。

**未决问题，落地前需要答：**

1. 现有三个 measure 用户（bernoulli / gaussian_blur / remainder）的 spec 需要迁移。它们的任务书是否给出了可用的性能容差？没有的话，它们改造后仍然只测不判，收益仅限于双边数字。
2. `conversion/roll` 是否确为任务书所指的「已有 aclnnRoll」，需要调用方确认。同名同接口是强信号但不是断言。
3. 两个 build target 在同一 install 前缀下共存时，vendor 名与 opp 路径是否冲突，需要在真机验证后才能确定 install 布局。
4. 性能超出容差判 `DUT_FAIL` 之前，是否需要重复测量取统计量。单次测量升格为终态裁决，噪声风险未评估。
5. 功能非回归目前只比 op_def 声明的集合。声明支持但实际不工作的 dtype 不会被这一项发现——那需要用例覆盖，属于 `required_cases` 的职责。

**Sources.** 第四轮 Roll 真机验收（session `sessions/s01`，PASS 225/225）；四个 witness 的 spec.json；`dev-doc/oprunway-torch-baseline-design.md` 组件⑤；用户 2026-08-12 的三项裁定。
