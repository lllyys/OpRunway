# 算子事实表

`facts.json` 是 S1 的唯一产物，也是 S2 写 YAML 的唯一依据。它回答四个问题：
测什么接口、参数长什么样、取值边界在哪、通过标准是什么。


## S1 事实表

读 [interface-facts.md](interface-facts.md)，按三跳来源规则填 `facts.json`：
**任务书任意位置 → 工程 `docs/aclnn<Op>.md` → 基线接口泛化推断**。每个字段带 `source`，
取值只能是 `taskdoc` / `opdoc` / `inferred`。

```bash
cd <工作目录> && <python> <skill>/scripts/check_facts.py facts.json
```

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | 事实表合规 | 进 S2；末尾那行「CPU 执行器 …」连同下一步一起打印，照它走 |
| 2 | 字段缺失，或 dtype 不在 ATK 词表里 | 照打印的字段名补 |
| 3 | JSON 语法错 | 修语法 |
| 4 | `baseline_source` 还是 `inferred` | 安全网：说明先写了再问，补问一次改成 `user` 重跑 |

**基线接口要自己挑时，在动笔写 `facts.json` 之前问用户。** 基线是精度比对的真值来源，
用户换一个接口，签名解读、参数表、要不要写执行器全跟着变，先写就得整份重来。

| 基线来自 | `baseline_source` 填 | 动作 |
| --- | --- | --- |
| 任务书点名 | `taskdoc` | 直接写 `facts.json` |
| 工程 `docs/aclnn*.md` 点名 | `opdoc` | 直接写 `facts.json` |
| 自己挑的候选 | 拍板后填 `user` | **先问，拿到答复再写** |

问的时候摆三项，签名读一遍就有，不用先跑脚本：候选接口是谁、它的参数与 aclnn
**是不是逐位对应**、依据是什么。

| 挑候选时 | 为什么 |
| --- | --- |
| 优先挑逐位对应的接口 | 不用写执行器，ATK 按序透传，没有丢参数的余地 |
| 语义存疑就换两三组入参跑一条对照 | 同名参数是不是同义、边界怎么取整、某个 dtype 收不收，推不出来；跑 torch 前设 `TORCH_DEVICE_BACKEND_AUTOLOAD=0` |
| 推出来的结论不写进 `facts.json` | `source` 没有「我觉得」这一档 |
| 测出语义差异先在用例设计里规避，换基线是最后手段 | 换一次连带签名解读、参数表、执行器全部重来 |

#### backend：这条用例包走哪个执行剖面

**这是分流的唯一判据，判一次，跑测侧只读不重判。** 判的是算子暴露的**接口形态**，
不是它属于哪个仓：

```bash
grep -rl "GetWorkspaceSize" <工程目录>/include <工程目录> --include="*.h" | head
```

| 命中 | `backend` 填 | ATK 怎么调 | 被测节点 |
| --- | --- | --- | --- |
| 有 `aclnn<Op>GetWorkspaceSize` 两段式声明 | `aclnn`（缺省） | ctypes dlopen 直调 C 接口 | `pyaclnn_0` |
| 0 命中，算子注册进 torch 的 dispatch | `npu` | 起 torch 调那个 callable | `npu_0` |

**0 命中不等于没有 C 接口。** 三段式的 aclsparse 那套用的是 `GetBufferSize`，
ATK 的 pyaclnn 后端按 `GetWorkspaceSize` 这个符号名解析，绑不上，所以照样走 `npu`。

`npu` 这一档的注册形态有两种，**调法都是「起 torch 调一个 callable」，填的都是 `npu`**：

| 注册形态 | 调用面 | 实测出处 |
| --- | --- | --- |
| 新建命名空间的自定义算子 | `torch.ops.<ns>.<name>` | — |
| 注册进 ATen 已有算子的 NPU dispatch 键 | 该算子的公开 torch API | ops-sparse 的 `TORCH_LIBRARY_IMPL(aten, PrivateUse1)` 与 `(aten, SparseCsrPrivateUse1)` 各注册一次 `_sparse_addmm`，调用面是 `torch.sparse.addmm`（2026-09-09） |

第二种**调用面就是公开 torch API**，所以待测侧与标杆侧可以是同一个调用，
只差设备与算值 dtype——这一档的精度基线怎么填见下面「精度基线」。

**填错的表现是跑测侧报表里解析不到被测节点，通过率算成 0。**

#### npu 剖面下受限的两个字段

填 `backend: npu` 之后这两个字段的取值受限，`check_facts.py` 会拦：

| 字段 | 取值 | 为什么 |
| --- | --- | --- |
| `accuracy.kind` | **不能 `builtin`** | `builtin` 是「拿 CANN 装机目录里的内置同名 aclnn 接口当标杆」，这条路上没有 aclnn 接口 |
| `performance.kind` | **不能 `builtin`** | 同上，它靠摘掉 opp vendor 层回落到内置实现，这条路上没有那一层。跑测侧退 3 |

**受限的只有这两个。** `non_contiguous.required` 与输入字节冻不冻**不由 `backend` 定**，
由下面这条轴定。

#### 用例形态：真张量声明，还是 attr 编码

这是与 `backend` 正交的第二条轴，决定**非连续轮跑不跑、输入字节冻不冻、执行器怎么写**。
两条轴的取值恰好对应过一次（自带件的 npu 用例全是 attr 编码），此后被当成一件事，
按 `backend` 一刀切——aclnn 剖面必然是真张量，npu 剖面两种都有。

| 形态 | 判据 | `non_contiguous.required` | `inputs/` |
| --- | --- | --- | --- |
| 真张量声明 | `params[]` 里有 `role: input` 且 `atk_type` 是 `tensor`／`tensors` | 按文档的「非连续Tensor」列填 | S4 照常冻，跑测侧 `--input_data` 直读 |
| attr 编码 | 入参全是 attr，张量由执行器按其中的 seed 现造 | 恒 `false`——`--slice_input` 切的是 ATK 造的那份，切不到执行器现造的 | 冻不出来，跑测侧按产物不在自动跳过 |

attr 编码只在采纳任务方自带件时出现（他们把整组输入压成几个整数）。
**自产用例一律走真张量声明**，覆盖面才由本仓的用例设计负责。
`check_facts.py` 按 `params[]` 判：入参里有张量却填 `non_contiguous.required: false`
时不拦（文档没打勾就是不要求），入参全是 attr 却填 `true` 时退 4。

#### 精度基线：torch 有没有语义等价的公开接口

```bash
python3 -c "import torch; print(torch.<候选接口>.__doc__)"   # 先设 TORCH_DEVICE_BACKEND_AUTOLOAD=0
```

| 情形 | `accuracy.kind` | `baseline` 填 |
| --- | --- | --- |
| 有参数逐位对应的接口，ATK 按序透传就能调 | `torch`（缺省） | `torch.<接口名>` |
| 任务书说「与内置实现对齐」 | `builtin` | 仍填 torch 接口，只给形状 |
| 有等价的公开接口，但 ATK 直调不出来 | `plugin` | 执行器注册名 `function_<op>_cpu` |
| **没有语义等价接口** | `plugin` | 同上 |

第三行是 `plugin` 的一半用量，判据是**调得出来吗**，不是**有没有这个接口**。
三种情形会落到这里，都要写执行器：

| 拦在哪 | 例子 |
| --- | --- |
| 入参不是 ATK 造得出的张量，要先拼成一个结构 | CSR 稀疏张量：ATK 造 `crow`／`col`／`values` 三个平张量，`torch.sparse.addmm` 要的是 `torch.sparse_csr_tensor(...)` 拼出来的那一个 |
| 标杆要换算值 dtype 才算得准 | 任务书规定 fp16／bf16 用 fp32 算 golden、fp32 用 fp64、complex64 用 complex128 |
| 待测侧与标杆侧是同一个调用，只差设备 | ATen 键注册的算子，两侧都是那个公开 API |

**这三种情形下标杆仍是公开接口，不是自写实现**，验收结论不降级——降不降级
按标杆是谁判，不按用没用执行器判，判据表在
[precision-standard.md](precision-standard.md)「标杆是谁决定结论说到哪一步」。

任务书里出现这两类话时多填一个字段：

| 任务书说 | `facts.json` 填 |
| --- | --- |
| 点名要新增或重点支持某个 dtype | `focus_dtypes: ["uint8"]`；只改 S3 的生成方式，别的字段不动 |
| 「和原算子对齐」「不劣于内置 aclnnXxx」 | `accuracy.kind: builtin`，边界见 interface-facts.md「精度基线两形态」；缺省是 `torch` |
| 给了性能要求，但要比的不是另一个实现（roofline 百分比、绝对耗时上限、带宽利用率） | `performance.kind: threshold` 并把原话抄进 `criterion`。**填 `none` 会把这条要求从验收报告里抹掉**，四种形态见 interface-facts.md |

两条红线：

| 红线 | 可取 | 不可取 |
| --- | --- | --- |
| 约束只从公开接口面取 | 任务书、`docs/aclnn*.md`、`README.md`、`op_api/*.h` 的函数声明 | `op_kernel/`、`op_host/*_tiling.cpp`、内部断言——用被测算子自己的断言生成用例，写错的断言永远测不出来 |
| 事实只从本地取 | 三跳都在本机 | WebFetch / WebSearch——网上取来的事实填 `opdoc` 是谎报本地有这份文档，填 `inferred` 是把别人写死的事实伪装成推断，跑测侧再也追不回来源 |

本地三跳都拿不到接口原型时按「停止条件」停在 S1，把缺的文件名列给用户。
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

### 第 2 跳的查找

```bash
ls <工程目录>/docs/aclnn*.md
```

社区算子工程的 `docs/aclnn<Op>.md` 是**公开接口文档**，含接口原型、逐参数 dtype 表、
约束说明、返回码。它比任务书完整，是第 2 跳的正解。

一个工程可能有多份，对应多个接口（如 `aclnnIndexFill` 与 `aclnnIndexFillTensor`）。
按任务书点名的接口挑，任务书没点名就问用户，不要两个都测。

**工程目录不存在时这一跳直接跳过。** 用户没给工程目录、或给了但没有 `docs/aclnn*.md`，
就是没有第 2 跳，**不要去线上仓找那份文件**（理由见 SKILL.md「事实只从本地取」）。
社区任务书的参数表通常已经够填满 `facts.json`；它也不够时走第 3 跳，按下面的纪律标注。

### 第 3 跳的纪律

推断出来的值必须同时满足两条才能用：

1. 写进 `facts.json` 时 `source` 标 `inferred`
2. 在给用户的进度里单列一行「推断项：<字段> = <值>，依据 <基线接口>」

**基线接口本身是例外，它不止要标注，还要用户点头。** 别的字段推错了顶多是覆盖面
偏了，基线推错了是整份 golden 的值都不对。所以它单独用顶层 `baseline_source`
记来源，取 `inferred` 时 `check_facts.py` 退 4 把你拦在 S1，见 SKILL.md「S1 事实表」。

推断 dtype 时取**基线接口在 NPU 上有意义的交集**，不是 torch 的全集。举例：
`torch.roll` 支持 fp64，但 Atlas A2 的 aicore 不跑 fp64，推断结果里不要放 fp64。

## 字段清单

```json
{
  "op": "Roll",
  "aclnn_name": "Roll",
  "baseline": "torch.roll",
  "baseline_source": "user",
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
| `baseline_source` | `taskdoc` / `opdoc` / `inferred` / `user` | 基线是谁定的。填 `inferred` 时 `check_facts.py` 退 4，要用户拍板 |
| `baseline_params` | 可选，`{参数名: 理由}` | 执行器丢掉了哪个 aclnn 入参、为什么。没丢就不写这个字段 |
| `focus_dtypes` | 可选，`["uint8"]` | 任务书点名要新增/重点测的 dtype。填了 `gen_cases.py` 拆两轮生成，让它们占全量的一半；不填就一轮跑完 |
| `backend` | `aclnn`（缺省）/ `npu` | 算子怎么被调起来，判据见上面「backend」那一节。**不要按算子属于哪个仓填** |
| `signature.text` | GetWorkspaceSize 原型原文 | 参数顺序的唯一依据 |
| `params[]` | 按签名顺序，**只列业务参数** | `workspaceSize` 与 `executor` 不列，pyaclnn 自己补 |
| `params[].role` | `input` / `output` | 输出参数也要列，位置要对 |
| `params[].atk_type` | 常用四种见下方映射表；**完整 9 种查 [atk-surface.md](atk-surface.md)** | 映射规则见下 |
| `params[].dtypes` | ATK dtype 词表里的名字 | 词表见下 |
| `shape.rank` | `[最小, 最大]` | 决定 YAML 的 `dim_numbers` |
| `constraints[]` | 自然语言约束 | 逐条要在 YAML 或约束器里有落点 |
| `non_contiguous` | `{"required": true/false, "source": …}` | 任务书参数表「非连续Tensor」列打勾就 `true`，跑测侧据此加 `--slice_input`。**和别的字段一样要 `source`** |
| `output.kind` | `single` / `multi` / `inplace` | 决定要不要写执行器 |
| `output.outputs[]` | **`kind=multi` 时必填** | 按签名顺序逐个列，见下 |
| `accuracy.acc` | `default` | 任务书说「AscendOpTest 默认阈值」时就是 `default` |
| `accuracy.kind` | `torch`（缺省）/ `builtin` / `plugin` | 精度基线是谁，见上 |

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

**它的盲区**：两个输出 dtype 恰好相同时（比如都是 `int64`）摊反查不出来，
值算错更查不出来——这个字段只挡结构错，不挡值错。
| `performance.kind` | `none` / `builtin` / `cross_dtype` / `threshold` | 四种形态见下 |
| `performance.sampling` | 可选，`{warmup, samples, source}` | 任务书规定了预热与采样次数时**必填**，见下「采样口径」。跑测侧的外部量测件缺它就停下来问，不替你猜一个 |

### C 类型到 ATK 类型的映射

| C 声明 | `atk_type` | ATK dtype |
| --- | --- | --- |
| `const aclTensor *` | `tensor` | 按文档的 dtype 列 |
| `aclTensor *`（输出位） | `tensor` | 同上，`role` 填 `output` |
| `const aclTensorList *` | `tensors` | 按文档的 dtype 列，个数用 `tuple_numbers` 控 |
| `const aclIntArray *` | `attr` | `int` |
| `const aclScalar *` | `scalar` | 按文档的 dtype 列 |
| `int64_t` | `attr` | `int` |
| `double` | `attr` | `double`（ATK 的 `DOUBLE` 就是 Python `float`，C `float` 才填 `float`）|
| `bool` | `attr` | `attr_bool` |
| `char *` / 枚举 | `attr` | `string` |
| `uint64_t *workspaceSize` | 不列 | pyaclnn 自动补 |
| `aclOpExecutor **executor` | 不列 | pyaclnn 自动补 |

**表里没有的 C 类型不等于不支持。** 先查 [atk-surface.md](atk-surface.md) 的参数类型清单，
找得到对应项就照它写，找不到再报「ATK 不支持」。

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

### 精度基线两形态

| `accuracy.kind` | 任务书原话长什么样 | S4 拿谁当 golden |
| --- | --- | --- |
| `torch`（缺省） | 「精度满足 AscendOpTest 默认阈值」 | YAML `name` 指的 torch 接口，只起 cpu 节点 |
| `builtin` | 「和原算子对齐」「不影响原有功能」「与现有实现逐位一致」 | CANN 装机目录里的内置同名 aclnn 接口 |

**`builtin` 只在基线与被测同名同签名时成立**——也就是优化、重构、inplace 融合
这类「不改语义只改实现」的任务。pyaclnn 是按 `aclnn_name` 拼符号、按位置传参的，
调用约定来自 `aclnn_api_type` 而它是 per-case 字段，同一次跑测里给不出两套。
新增算子或改了签名的任务用不了这条，填 `torch`。

什么时候**必须**用 `builtin`：随机数算子。CPU 与 NPU 的随机数流不同（前者 MT19937，
后者 Philox），同一个 seed 也不可能逐位一致，torch 基线在这类算子上直接失效。
前提是种子和偏移是显式入参且落进了用例数据——bernoulli 的 `seed`/`offset` 正是。

### 性能基线四形态

| `performance.kind` | 何时 | 跑测侧怎么处理 |
| --- | --- | --- |
| `none` | 任务书写「性能要求：无」 | 只采集绝对耗时，不给达标结论 |
| `builtin` | 「不劣于 aclnnXxx 小算子拼接版本」「**不低于原算子性能**」「不低于现有实现」 | 跑测脚本读到它自己接着跑第二轮：卸载自定义包，测内置实现作基线 |
| `cross_dtype` | 任务书写「int8 不劣于 int32」这类同算子内比较 | 一轮跑测内按 dtype 分组对比，额外填 `pairs` |
| `threshold` | 要求成立与否不取决于另一个实现：「达到 compute/memory bound 的 80%」「单次调用不超过 x us」「带宽利用率 ≥ y%」 | 一轮采绝对耗时，把 `criterion` 原话与实测数据一起写进报告，标 `待人工判定`，额外填 `criterion` |

**`threshold` 不是兜底档。** 任务书要求与之比较的是**另一个跑得起来的实现**时用
`builtin` 或 `cross_dtype`，跑测侧自己就能起第二轮。`threshold` 是「比较对象不在这台
机器上」那一档，裁决分两种：

| `criterion` 原话里 | 跑测侧 |
| --- | --- |
| 抠得出倍数门槛（`0.25 倍`／`0.25 ×`／`不超过基线的 4 倍`），且拿得到逐条基线数值 | **真判**，逐条算倍率比门槛。基线数值由跑测侧的外部量测件收编 |
| 抠不出数值门槛（`达到 memory bound 的 80%`、`不劣于内置实现`） | 算不出（要 FLOPs／字节数与该机型的理论峰值，跑测侧两样都没有），原样递给读报告的人，标 `待人工判定` |

**第一种要把门槛原话一字不改地抄进 `criterion`。** 跑测侧认的是写法不是某个字，
漏抄一句「算术平均值须不低于 0.35 倍」就少判一道门。任务书给的基线数据表
（每个场景一个耗时数）**留在任务书那边不抄进 `facts.json`**——它是逐条数据，
不是判据，由跑测侧的外部量测件按场景对齐后收编。

### 采样口径

任务书规定了预热与采样次数时，抄进 `performance.sampling`：

```json
"performance": {
  "kind": "threshold",
  "criterion": "所有场景的性能倍率均须大于 0.25 倍，全部量化场景性能倍率的算术平均值须不低于 0.35 倍",
  "sampling": {"warmup": 10, "samples": 30, "source": "任务书 性能要求 第 3 条"},
  "source": "taskdoc"
}
```

`{warmup, samples, source}` 三项都要，`source` 填任务书里那句话的出处。
**这是验收口径不是量测件的调参**，低于它采出来的数不能当验收依据；
跑测侧的外部量测件取不到这个字段就停下来问，不替谁猜一个数。
任务书没规定就不写这个字段。

**两张表里的原话都是例句，不是判定式。** 任务书措辞五花八门：bernoulli 写的是
「不低于原算子性能」，与 `builtin` 那行的例句一个字都不重合，说的却是同一件事
（「原算子」就是任务概述里那套小算子拼接实现）。按字面找例句会填错，
而**填错的后果是静默的**——`performance.kind` 填成 `none` 时性能整节不评级，
报告里只是少一节，没人看得出来。

判据只有一条：**任务书要求与之比较的对象是谁。**

| 比较对象 | 填 |
| --- | --- |
| 没有比较对象，任务书写明无性能要求 | `none` |
| CANN 现有的同名实现——无论叫「原算子」「现有实现」还是「aclnnXxx 小算子拼接版本」 | `builtin` |
| 同一个算子的另一个 dtype | `cross_dtype` |
| 硬件理论上限、或一个写死的数值 | `threshold` |

精度那张表同理。**任务书写了要求却填成 `none`，那条要求就此消失**，跑测侧不会问，
报告里只是少一节。四档里挑不出来时问用户，不要默认 `none`。

`threshold` 时补一个 `criterion` 字段，照抄任务书原话：

```json
"performance": {
  "kind": "threshold",
  "criterion": "达到 compute bound 或 memory bound 的 80%",
  "source": "taskdoc"
}
```

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
