# OpRunway 执行方向确认单设计（评审稿）

## 1. 目的

当前 CP-B 会把任务书与 PR 事实抽取为机器可消费的 `<Op>.spec.json`，但 spec 不适合直接交给人快速审阅。若在生成 golden、展开用例或上真机之前没有把关键选择讲清楚，后续可能选错验收范围、接入形态、精度 golden、性能 baseline、测试口径或异常处理路线。

建议在 CP-B 增加一个“执行方向人工确认门”，集中展示真正影响后续路线的决策。

本文只提出确认单内容和节点设计，尚未修改 workflow。

## 2. 建议节点

```text
CP-B1：分析任务书 + PR，生成 spec
                    ↓
CP-B2：生成《执行方向确认单》
                    ↓
人工确认 / 要求修改 / 阻塞
                    ↓
确认后生成 golden + case_plan
                    ↓
CP-C 接入与信任门
                    ↓
CP-D 真机精度和性能验收
```

确认单应绑定当前 spec 和事实包。任务书、PR head 或 spec 发生变化后，原确认自动失效。

## 3. 建议产物

### 3.1 人读确认单

```text
ops/<Op>/<Op>.execution-plan.md
```

它负责用中文解释“接下来准备怎么验”，不代替机器契约，不产生验收结论。

### 3.2 机器确认收据

```text
work/execution_plan_confirmation.json
```

最小结构建议如下：

```json
{
  "schema": "oprunway.execution_plan_confirmation",
  "schema_version": 1,
  "op": "Median",
  "spec_sha256": "<当前 spec 摘要>",
  "source_facts_digest": "<当前事实包摘要>",
  "status": "confirmed",
  "decisions": {
    "scope": "partial_run_no_pass",
    "runner_form": "cpp_extension",
    "golden": "torch_cpu",
    "precision": "ascendoptest_default_plus_exact",
    "case_plan": "confirmed",
    "perf_baseline": "torch_npu",
    "perf_scope": "kernel_only",
    "hardware": ["Atlas A3"]
  },
  "requested_changes": [],
  "acceptance_verdict": null
}
```

状态建议限定为：

| 状态 | 含义 | 后续动作 |
|---|---|---|
| `confirmed` | 认可当前执行方向 | 进入 golden、case plan 和后续阶段 |
| `changes_requested` | 方向需要调整 | 修改 spec，重新生成确认单 |
| `blocked` | 当前条件不允许继续 | 停在 CP-B |

这份收据只证明执行方案已获确认，`acceptance_verdict` 必须为 `null`，不能被当作验收 PASS。

## 4. 需要人工确认的关键点

### 4.1 验收范围与缺口处理

确认：

- 验收哪些 API、overload 和 dtype；
- PR 是否完整覆盖任务书要求；
- 缺口是立即阻断，还是允许先跑已实现部分但最终不得 PASS；
- 是否存在经过权威确认的任务书范围变更。

这是最高优先级决策，不能由 agent 静默选择。



### 4.2 DUT 与交付形态

DUT 是 `Device Under Test`，即本轮真正接受功能、精度和性能验收的被测对象。对当前 Median，
DUT 是指定 PR 构建出的 vendor `libcust_opapi.so` 及其所调度的算子实现；独立 `torch.ops`
Extension 只是把测试用例接到 DUT 的调用桥，不是 DUT。

分别写清：

- 交付的算子工程是什么；
- 真正的 DUT 是哪个构建物；
- 哪些组件只是测试桥。

示例：

```text
交付工程：ops-nn 自定义 ACLNN 算子工程
DUT：指定 PR 构建的 libcust_opapi.so
测试桥：独立 torch.ops C++ Extension
```

不得把“通过 Torch extension 测试”表述为“交付物本身是 Torch extension”。

### 4.3 接入执行路线

确认 `runner_form` 及选择原因：

| `runner_form` | 执行形态 |
|---|---|
| `cpp` | per-op C++ runner |
| `aclnn_py` | 通用 ctypes 直调标准 ACLNN |
| `cpp_extension` | 独立 `torch.ops` C++ Extension 调用桥 |

具体而言：`cpp` 编译并运行 per-op C++ runner；`aclnn_py` 不生成 per-op runner，而由通用 Python
ctypes harness 调用 ACLNN 两段式接口；`cpp_extension` 生成独立 `torch.ops` 桥，再调用指定 PR
构建的 vendor `.so`。当前 Median 采用 `cpp_extension`，但 DUT 仍是 vendor `.so`，extension
只是测试桥。

runner form 决定 CP-C 如何建桥、验证加载来源以及 CP-D 如何执行，但不能反推性能 baseline。

### 4.4 Golden 真值

确认：

- 谁是真值；
- 准确调用哪个 API 和 overload；
- 在 CPU、GPU 还是其他环境生成；
- 多输出分别如何解释；
- 是单 API 直接真值，还是组合/参考实现；
- 是否需要人工复核。

### 4.5 特殊语义处理

只列会改变裁决的语义：

- 重复值或 tie 时，索引必须完全相同还是只需指向合法值；
- Torch 与 DUT 的索引 dtype 不同是否允许；
- NaN、Inf、正负零如何处理；
- 空 tensor、标量、负 `dim` 是否属于范围；
- 默认属性、nullable 属性和 optional output 如何解释。

其中，重复值或 tie 是指输入中存在多个相同的合法中位数位置。例如
`[3, 1, 3, 5, 7]` 的中位数值为 `3`，但索引可指向位置 `0` 或 `2`。确认单必须明确：
索引要与 Torch 完全一致，还是允许索引不同、但要求它合法且回取值与 `valuesOut` 一致。

这些问题不能依赖 runner 临场猜测。

### 4.6 精度标准

确认：

- 使用 AscendOpTest、exact、MERE/MARE 或其他标准；
- 各 dtype 的 tolerance 和 error rate；
- 整数是否收紧为 exact；
- index 输出采用什么标准；
- 多输出如何折叠；
- shape 不一致是否直接失败。

MERE 是所有元素相对误差的平均值，用于反映整体偏差；MARE 是最大相对误差，用于反映最差单点。
当前该口径仍为 `proposed / NOT_SETTLED`，Median 实际使用的是浮点 AscendOpTest default、整数
exact 和索引一致性标准。

确认单应展示实际生效的逐 dtype policy，不能只展示 spec 顶层兼容字段 `threshold`。

### 4.7 用例范围与预算

确认：

- dtype、rank、shape 和属性覆盖；
- 输入值域、分布和特殊场景；
- contiguous/noncontiguous；
- 完整笛卡尔积还是有界采样；
- case 总数和预计执行成本。

方案性选择必须明确标出。例如“rank 1～8”若不是任务书规定，就应标记为用例设计选择，而非任务书事实。

### 4.8 性能 baseline

确认：

- baseline 类型和准确 API；
- baseline 是否与 DUT 语义等价；
- 是否同机、同卡、同输入；
- baseline 不支持某些 dtype/case 时如何处理；
- baseline 来源是任务书、用户确认还是方案推断。

runner form 和 baseline 必须分别确认。

### 4.9 性能方法与达标标准

确认：

- kernel-only 还是端到端；
- profiler；
- warmup、repeat 和统计量；
- custom 与 baseline 是否同口径；
- 性能 case 数和选择规则；
- ratio 的定义和达标阈值；
- timeout、抖动和重试规则。

### 4.10 目标硬件与环境范围

确认：

- 实际验收硬件和 SoC；
- 是否需要多种硬件分别验证；
- 性能条款绑定哪个硬件；
- CANN、torch、torch_npu 是否有版本约束；
- 是否要求同机 baseline。

任务书支持多个硬件时，不能默认“在其中一台测试”就代表完整覆盖。

### 4.11 构建、安装与来源策略

只确认方向：

- 指定 PR fresh build，还是允许按内容寻址复用；
- 是否只安装到用户态隔离 vendor；
- 是否禁止写共享 OPP；
- vendor ELF 是否绑定完整 PR head、构建命令和文件摘要；
- 构建物或环境漂移后是否强制重验。

其中，fresh build 是从指定 PR head 重新构建；内容寻址复用则只在 PR、源码、构建参数、SoC、
工具链和 ELF 摘要完全一致时复用旧产物。用户态隔离 vendor 是把算子安装到本轮独立目录和
vendor name 下；禁止写共享 OPP 是为了避免覆盖系统或其他人的算子。绑定完整 PR head、构建命令
和 ELF SHA-256，是为了证明实际加载的 `.so` 确实来自指定 PR。只要 PR、spec、caseset、ELF、
CANN、torch_npu 或 SoC 等关键身份发生漂移，相关旧收据和验收结果就应失效并按影响范围重验。

具体路径、SHA 值和日志位置由机器产物记录，无需逐项人工确认。

### 4.12 失败、异常与挂起策略

确认：

- 精度失败后是否跳过性能；
- DUT 执行失败如何处理；
- baseline 自身失败是 DUT FAIL，还是 baseline limitation；
- 缺数据、计时口径不一致或来源不可信时是否 BLOCKED；
- 任务书功能 gap 是否阻止最终 PASS；
- timeout 或部分 case 缺失是否允许继续。

功能 gap 是指任务书要求某项 API、overload、dtype、属性、输出或硬件能力，但当前 PR 没有实现或
无法执行；它不是“功能能运行但结果算错”的精度失败。例如 Median 任务书要求无 `dim` 的
`torch.median(input)`，而当前 PR 只有带 `dim/keepDim` 的双输出接口，这就是 API surface 功能
gap。功能 gap 未解决时，应选择停跑，或只测试已实现部分但禁止最终 PASS。

建议默认规则：

```text
精度失败 → 跳过性能
DUT 错误 → FAIL
baseline 能力不足 → BLOCKED/limitation，不归因 DUT
证据或 provenance 不完整 → BLOCKED
needs_review ≠ PASS
任务书功能 gap 未解决 → 不得 PASS
```


## 5. 不需要人工逐项确认的内容

以下内容可自动生成、展示并由机器门校验：

- 文件具体落点；
- JSON schema 版本；
- SHA-256 的具体值；
- extension namespace 的具体字符串；
- build log 路径；
- receipt 内部字段；
- case ID 命名；
- repro 脚本名称；
- runner 内部参数组装细节。

## 6. Median 当前示例

以下内容基于当前 Median spec，仅用于展示确认单形式。

| 事项 | 当前方案 | 待确认点 |
|---|---|---|
| 验收范围 | 任务书要求无 `dim` 和带 `dim` 两类 `torch.median` | PR 缺无 `dim` 路线：停跑，还是部分执行但不得 PASS |
| 交付形态 | `ops-nn` 自定义 ACLNN 算子工程 | 确认 |
| DUT | 指定 PR 构建的 `libcust_opapi.so` | 确认 |
| 测试接入 | 独立 `torch.ops` C++ Extension | 确认它仅为测试桥 |
| runner form | `cpp_extension` | 确认 |
| Golden | CPU `torch.median(input, dim, keepdim)` | 确认 |
| index 语义 | DUT `int32`，Torch 通常为 `int64`，按 index-value consistency | 确认 tie/index 处理 |
| 精度标准 | 浮点 AscendOpTest default；整数 exact；索引零 mismatch | 确认整数收紧策略 |
| 用例矩阵 | 8 dtype × 8 rank × 3 规模 × 6 属性，共 1152 | 确认数量与覆盖结构 |
| shape | 主要为 `[N,1,1,...]` | 是否补真实多维 shape |
| 输入值域 | uniform `[-5,5]` | 是否补 tie、极值、NaN/Inf |
| 性能 baseline | 同机 `torch_npu:torch.median` | 已有用户确认，应固化 |
| 性能方法 | msprof kernel-only，warmup 5，repeat 20，取中位数 | 确认 |
| 性能阈值 | `baseline_us/custom_us >= 1.0` | 确认“不劣化”的定义 |
| 性能用例 | 从精度通过 case 中选择，最多 50 个 | 确认预算和覆盖轴 |
| 硬件 | 当前目标 Atlas A3 / `ascend910_93` | 是否还必须覆盖 A2 |
| 来源策略 | 指定 PR 构建、用户态隔离 vendor、ELF 内容寻址绑定 | 确认 |
| 失败策略 | 精度失败跳过性能；证据不完整 BLOCKED；gap 未解不得 PASS | 确认 |

## 7. 建议硬门

进入 golden、case plan 或 CP-C 前，应满足：

```text
execution_plan_confirmation.json 存在
status == confirmed
spec_sha256 与当前 spec 一致
source_facts_digest 与当前事实包一致
不存在未处理的 requested_changes
```

若 spec、任务书字节或 PR head 变化：

```text
旧确认失效
→ 重新生成执行方向确认单
→ 重新人工确认
```

这样可以保证后续执行沿着人工确认过的方案进行，同时不把实现细节堆进人工审阅节点。
