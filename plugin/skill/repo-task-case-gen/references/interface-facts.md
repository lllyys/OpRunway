# 算子事实表

`facts.json` 是 S1 的唯一产物，也是 S2 写 YAML 的唯一依据。它回答四个问题：
测什么接口、参数长什么样、取值边界在哪、通过标准是什么。

## 三跳来源规则

每个字段按优先级取，取到就停，并在 `source` 里记下取自哪一跳。

| 跳 | 来源 | `source` 取值 | 什么时候用得上 |
| --- | --- | --- | --- |
| 1 | 任务书任意位置 | `taskdoc` | 任务书带参数说明表，如 roll.md 的「参数说明」 |
| 2 | 工程 `docs/aclnn<Op>.md` | `opdoc` | 任务书只写目标不写接口，如 median 的「所有走入 aicore 的数据类型」 |
| 3 | 基线接口泛化推断 | `inferred` | 前两跳都没有，从 `torch.<op>` 的支持面推断 |

**社区任务书没有固定结构。** 不要按章节号找内容，按语义找：参数表可能叫「参数说明」，
可能叫「接口定义」，也可能根本不存在。dtype 可能在表格的「数据类型」列，也可能是正文里
一句「支持 int16, int8, uint8，double」。

### 第 2 跳怎么找

```bash
ls <工程目录>/docs/aclnn*.md
```

社区算子工程的 `docs/aclnn<Op>.md` 是**公开接口文档**，含接口原型、逐参数 dtype 表、
约束说明、返回码。它比任务书完整，是第 2 跳的正解。

一个工程可能有多份，对应多个接口（如 `aclnnIndexFill` 与 `aclnnIndexFillTensor`）。
按任务书点名的接口挑，任务书没点名就问用户，不要两个都测。

### 第 3 跳的纪律

推断出来的值必须同时满足两条才能用：

1. 写进 `facts.json` 时 `source` 标 `inferred`
2. 在给用户的进度里单列一行「推断项：<字段> = <值>，依据 <基线接口>」

推断 dtype 时取**基线接口在 NPU 上有意义的交集**，不是 torch 的全集。举例：
`torch.roll` 支持 fp64，但 Atlas A2 的 aicore 不跑 fp64，推断结果里不要放 fp64。

## 字段清单

```json
{
  "op": "Roll",
  "aclnn_name": "Roll",
  "baseline": "torch.roll",
  "backend": "aclnn",
  "signature": {
    "source": "opdoc",
    "text": "aclnnRollGetWorkspaceSize(const aclTensor *x, const aclIntArray *shifts, const aclIntArray *dims, aclTensor *out, uint64_t *workspaceSize, aclOpExecutor **executor)"
  },
  "params": [
    {
      "name": "x",
      "role": "input",
      "atk_type": "tensor",
      "dtypes": ["bf16", "fp16", "fp32", "int8", "uint8", "int32", "uint32", "complex64"],
      "source": "taskdoc"
    },
    {
      "name": "shifts",
      "role": "input",
      "atk_type": "attr",
      "dtypes": ["int"],
      "source": "taskdoc"
    }
  ],
  "shape": {
    "rank": [0, 8],
    "source": "taskdoc"
  },
  "constraints": [
    {"text": "dims 非空时 shifts 与 dims 长度必须一致", "source": "opdoc"},
    {"text": "dims 取值范围为 [-rank, rank)", "source": "opdoc"}
  ],
  "output": {
    "kind": "single",
    "position": "out",
    "source": "opdoc"
  },
  "accuracy": {"acc": "default", "source": "taskdoc"},
  "performance": {"kind": "none", "source": "taskdoc"}
}
```

### 逐字段说明

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `op` | 驼峰算子名 | 决定工作目录名与文件名前缀 |
| `aclnn_name` | 去掉 `aclnn` 前缀的接口名 | 填进 YAML 的 `aclnn_name` |
| `baseline` | `torch.xxx` | 填进 YAML 的 `name`，CPU 标杆靠它算 golden |
| `backend` | `aclnn` | 社区算子固定 `aclnn`，走 pyaclnn |
| `signature.text` | GetWorkspaceSize 原型原文 | 参数顺序的唯一依据 |
| `params[]` | 按签名顺序，**只列业务参数** | `workspaceSize` 与 `executor` 不列，pyaclnn 自己补 |
| `params[].role` | `input` / `output` | 输出参数也要列，位置要对 |
| `params[].atk_type` | `tensor` / `attr` / `scalar` | 映射规则见下 |
| `params[].dtypes` | ATK dtype 词表里的名字 | 词表见下 |
| `shape.rank` | `[最小, 最大]` | 决定 YAML 的 `dim_numbers` |
| `constraints[]` | 自然语言约束 | 逐条要在 YAML 或约束器里有落点 |
| `output.kind` | `single` / `multi` / `inplace` | 决定要不要写执行器 |
| `output.outputs[]` | **`kind=multi` 时必填** | 按签名顺序逐个列，见下 |
| `accuracy.acc` | `default` | 任务书说「AscendOpTest 默认阈值」时就是 `default` |

### 多输出算子的 outputs

`kind=multi` 时按签名顺序逐个声明，`freeze_golden.py` 靠它查输出有没有摊反：

```json
"output": {
  "kind": "multi",
  "position": "valuesOut,indicesOut",
  "outputs": [
    {"name": "valuesOut", "dtype": "same_as_input"},
    {"name": "indicesOut", "dtype": "int64"}
  ],
  "source": "opdoc"
}
```

`dtype` 填 ATK 词表里的值，或 `same_as_input`（与第一个输入张量同 dtype）。

**为什么要这个字段。** 执行器把两个输出摊反时 golden 照样全绿——它跑得出来，
只是 `output_0` 装的是本该在 `output_1` 的那个张量。到 NPU 侧才全部比对失败，
而报告会把它归成算子缺陷，结论完全反向。dtype 一比就抓到了。

**它的盲区**：两个输出 dtype 恰好相同时(比如都是 `int64`)摊反查不出来，
值算错更查不出来——这个字段只挡结构错，不挡值错。
| `performance.kind` | `none` / `builtin` / `cross_dtype` | 三种形态见下 |

### C 类型到 ATK 类型的映射

| C 声明 | `atk_type` | ATK dtype |
| --- | --- | --- |
| `const aclTensor *` | `tensor` | 按文档的 dtype 列 |
| `aclTensor *`（输出位） | `tensor` | 同上，`role` 填 `output` |
| `const aclIntArray *` | `attr` | `int` |
| `const aclScalar *` | `scalar` | 按文档的 dtype 列 |
| `int64_t` | `attr` | `int` |
| `bool` | `attr` | `attr_bool` |
| `char *` / 枚举 | `attr` | `string` |
| `uint64_t *workspaceSize` | 不列 | pyaclnn 自动补 |
| `aclOpExecutor **executor` | 不列 | pyaclnn 自动补 |

### ATK dtype 词表

取自 `atk/case_generator/utils/enums.py` 的 `TorchDtype`：

```text
fp64 fp32 fp16 bf16 int64 int32 int16 int8 uint64 uint32 uint16 uint8 bool
complex64 complex128 fp8e4m3 fp8e5m2
```

attr 类型取自同文件的 `StandardDtype`：

```text
int int8_t int32_t int64_t uint8_t uint32_t uint64_t double float attr_bool string non_param
```

**文档里的名字要翻译过来**：`FLOAT` → `fp32`，`FLOAT16` → `fp16`，`BFLOAT16` → `bf16`，
`DOUBLE` → `fp64`，`COMPLEX64` → `complex64`。`check_facts.py` 会拦住没翻译的。

### 性能基线三形态

| `performance.kind` | 何时 | 跑测侧怎么处理 |
| --- | --- | --- |
| `none` | 任务书写「性能要求：无」 | 只采集绝对耗时，不给达标结论 |
| `builtin` | 任务书写「不劣于 aclnnXxx 小算子拼接版本」 | 卸载自定义包再跑一轮内置实现作基线 |
| `cross_dtype` | 任务书写「int8 不劣于 int32」这类同算子内比较 | 一轮跑测内按 dtype 分组对比，额外填 `pairs` |

`cross_dtype` 时补一个 `pairs` 字段：

```json
"performance": {
  "kind": "cross_dtype",
  "pairs": [["int16", "int32"], ["int8", "int32"], ["uint8", "int32"], ["fp64", "int64"]],
  "source": "taskdoc"
}
```

## 常见误填

| 误填 | 后果 | 正确做法 |
| --- | --- | --- |
| `params` 里带上 `workspaceSize` / `executor` | pyaclnn 参数个数对不上，全部用例执行失败 | 只列业务参数 |
| dtype 写 `FLOAT16` 没翻译 | `atk case` 抛 KeyError | 翻成 `fp16` |
| `baseline` 写成 `torch.Tensor.roll` | CPU 标杆 eval 不出来，golden 全失败 | 用模块级函数名 |
| 输出参数不列进 `params` | 输出位置错，比对拿到的是输入 | 按签名顺序列全 |
| dtype 抄了工程 kernel 里的支持列表 | 拿被测实现当判据 | 只从 `docs/` 与任务书取 |
