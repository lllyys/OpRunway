# 任务书 intake 表

逐段填写：答案写进各小节的 `answer` 围栏块（可多行，贴原文即可），**留空 = 跳过**。
「例：」一行只是示范，不会被读取；只有块内内容才会逐字映射进 decisions.json——
构成有效拍板的记入 `human_reply`，其余记入 `note`，判定见
[decisions-format.md](../references/decisions-format.md) 的「记录字段」「有效拍板」两节。
小节标题即骨架 key，与 `references/taskdoc-elements.json` 的 `elements` 同名。

## 必填·事实类

贴原始材料：版本号、型号、标杆数据、链接原文。判据摘自
`references/task-doc-template.md` 原文，括注章节号。

### 1.background

任务背景。判据（§1）：「基于 xxx 背景」
例：社区任务「hypot 算子开发」，补齐 aclnn 数学算子缺口

```answer
```

### 1.language

开发语言与工程。判据（§1）：「使用 xxx 语言/模板库开发 xxx 算子/接口」
例：使用 Ascend C 与 aclnn 算子工程模板库开发 hypot 算子

```answer
```

### 1.repo

算子相关代码仓。判据（整体要求）：「所有链接需要显式贴出来，不能使用隐藏跳转链接」
例：https://gitcode.com/cann/ops-math

```answer
```

### 3.1.hardware

适配硬件与具体型号。判据（§3.1）：「硬件型号要具体到子型号。写「A2系列产品」不够，
要写明 910B3、910B4 等」
例：910B3、910B4

```answer
```

### 3.1.cann_version

CANN 版本。判据（§3.1）：「包括硬件型号（A2/A3/950）、CANN版本以及其他依赖的三方软件版本」
例：CANN 8.2.RC1

```answer
```

### 3.1.third_party

三方软件版本。判据（§3.1）：「三方软件版本包括 torch、torch_npu，性能对标 GPU 时
还要写标杆环境的驱动与 CUDA 版本」
例：torch 2.4.0、torch_npu 2.4.0；GPU 标杆环境 CUDA 12.4、驱动 550.54

```answer
```

### 3.3.baseline_env

性能标杆接口与环境。判据（§3.3）：「对标算子/接口，如果没有现成的对标接口，
使用小算子拼接的性能采集策略要明确出来」
例：A100 + torch 2.4.0 跑 torch.hypot 采集标杆

```answer
```

### 3.3.criterion

性能判据。判据（§3.3）：「判据必须含比较符与数字，如「不低于标杆的 0.8 倍」」
例：不低于标杆的 0.8 倍，910B3、910B4 逐型号达标

```answer
```

### 3.4.memory

内存要求。判据（§3.4）：「描述算子调用的内存占用要求，例如对标torch.xxx接口，
内存占用比值不能超过xxx」「若不涉及请填“不涉及”」
例：对标 torch.hypot，内存占用比值不超过 1.2

```answer
```

### 5.pr_target

PR 合入目录。判据（§5）：「需要清晰指出算子开发完成后的提交代码仓和目录」
例：https://gitcode.com/cann/ops-math 的 examples/hypot 目录

```answer
```

## 必填·决定类

### 2.2.project_mode

算子工程模式，三选一。判据（§2.2）：「kernel直调/aclnn算子工程化开发/torch接口」
例：aclnn算子工程化开发

```answer
```

### 2.1.baseline

对标基线接口。判据（§2.1）：「明确对标的基线接口，如torch.xxx」
例：torch.hypot

```answer
```

## 前提

### 是否随机算子

第一行 `random` 或 `nonrandom`，第二行一句依据。答案落保留 key
`_premises.random_operator`，不是骨架要素。
例：nonrandom ／ 逐元素确定性计算，无随机数生成

```answer
```

## 可选·已有结论可直接写

下面这些项默认由助手基于对标接口先给候选、再请你确认。**这一节可以整个跳过**，
流程照走；某项已有现成结论就填进对应块，能省一轮确认。空块一律跳过。

### 2.1.operator_name

算子名与接口名。例：hypot ／ aclnnHypot

```answer
```

### 2.1.formula

数学公式。例：out_i = sqrt(a_i^2 + b_i^2)

```answer
```

### 2.1.algorithm

算法说明。例：逐元素计算，平方求和再开方，中间量用 fp32 累加防溢出

```answer
```

### 2.3.signature

接口定义。例：hypot(Tensor a, Tensor b, Tensor out)

```answer
```

### 2.4.param_name

参数名。例：a、b、out（与接口定义逐字一致）

```answer
```

### 2.4.direction

输入输出属性。例：a 输入、b 输入、out 输出(独立输出)

```answer
```

### 2.4.description

参数描述。例：a 是第一条直角边张量

```answer
```

### 2.4.data_type

数据类型。例：a/b/out 均为 tensor

```answer
```

### 2.4.dtype

dtype 面。例：FLOAT16、FLOAT32

```answer
```

### 2.4.format

排布格式。例：ND

```answer
```

### 2.4.shape

维度。例：a 与 b 同 shape，秩 1–8；out 同 a

```answer
```

### 2.4.value_range

值域。例：[-1e4, 1e4]

```answer
```

### 2.4.error_behavior

异常行为。例：dtype 不支持时返回 ACLNN_ERR_PARAM_INVALID

```answer
```

### 2.5.non_contiguous

非连续张量支持。例：支持，a/b/out 均支持

```answer
```

### 2.5.broadcast

广播规则。例：不支持

```answer
```

### 2.5.dynamic_shape

动态 shape。例：要求支持编译期未知 shape

```answer
```

### 2.5.inplace_view

原地与视图语义。例：out 不复用输入内存，不返回视图

```answer
```

### 2.5.deterministic

确定性计算。例：要求，同输入两次执行逐位一致

```answer
```

### 2.5.empty_tensor

空张量与 0 维。例：合法输入，输出同 shape 空张量

```answer
```

### 3.2.reference_api

精度对标接口。例：torch.hypot

```answer
```

### 3.2.threshold_table

逐 dtype 阈值表。例：FLOAT16 rtol 2^-8，FLOAT32 rtol 2^-11，逐 dtype 成表

```answer
```

### 3.2.verdict_formula

精度判定公式。例：|actual−golden| ≤ atol + rtol×|golden| 的比例 ≥ 99.9%

```answer
```

### 3.2.random_strategy

随机算子精度对比策略。例：不涉及（非随机算子）

```answer
```

### 3.3.case_table

性能 case 与标杆数据。判据（§3.3）：「给出性能要求测试的case和标杆性能」，模板
允许「也可以在测试用例文件中提供」——立项时没有就留空，后续追问。
例：TC_PF_ 用例 8 条随测试用例文件提供，含各型号标杆值

```answer
```

### 3.5.tooling

自验工具与策略。例：verify_accuracy.py 自验脚本 + 门禁全量跑

```answer
```

### 3.5.param_mapping

参数序列对应关系。例：与 torch.hypot 逐位一致，无需映射

```answer
```

### 3.5.generation_rules

入参生成规则。例：a/b 均匀分布 [-1e4, 1e4] 占 80%，边界值 20%

```answer
```

### 4.deliverables

验收交付件。例：kernel 源码、host 适配、测试用例、自验报告

```answer
```

### 7.notes

特别注意事项。例：大值输入先除后乘防溢出

```answer
```
