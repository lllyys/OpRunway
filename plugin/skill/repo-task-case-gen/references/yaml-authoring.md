# ATK YAML 写法

用例设计文件定义输入空间与验收标准，`atk case` 按它生成用例 JSON。
字段真源是 ATK 的《用例设计文件说明》，本文只写生成社区算子用例时实际要填的部分。

## 骨架

```yaml
api: pytorch
api_type: function
aclnn_api_type: aclnn_function
version: v2.1
name: torch.roll
aclnn_name: Roll
generate: default
dtype_numbers: 1
extra_numbers: 0
standard:
  acc: default
  perf: not_key
inputs:
  - name: null
    type: tensor
    required: true
    dtypes:
      values: [bf16, fp16, fp32, int8, uint8, int32, complex64]
    ranges:
      valid:
        values: [[-5, 5]]
    shapes:
      dim_numbers:
        values: [1, 2, 3, 4]
      dim_values:
        values: [1, 2, 3, 7, 8, 15, 16, 31, 32, 63, 64, 127, 128, 255, 256, 511, 512]
      max_length: 1048576
    boundary:
      has_empty: false
      has_scalar: true
      has_upper_border: false
outputs: null
```

## 顶层字段

| 字段 | 填什么 | 依据 |
| --- | --- | --- |
| `api` | 固定 `pytorch` | CPU 标杆走 torch |
| `api_type` | `function`，写了执行器就填注册名 | CPU 侧执行方式 |
| `aclnn_api_type` | `aclnn_function`，写了执行器就填注册名 | NPU 侧执行方式 |
| `name` | `facts.json` 的 `baseline` | CPU 标杆靠它 eval |
| `aclnn_name` | `facts.json` 的 `aclnn_name` | pyaclnn 拼 `aclnn<Name>` 找符号 |
| `generate` | `default`，写了约束器就填注册名 | 见 plugin-authoring.md |
| `dtype_numbers` | S2 先填 `1`，S3 前回填 | 见 case-strategy.md |
| `extra_numbers` | 见下 | 边界用例开关的总闸 |
| `standard.acc` | `default` | 见 precision-standard.md |
| `standard.perf` | `not_key` | 性能任务另跑，这里不设阈值 |
| `outputs` | `null` | 用算子默认返回值 |

### extra_numbers 与 boundary 的关系

`extra_numbers` 是边界用例的总闸，`boundary.*` 是分项开关。总闸关了分项开也没用。

| 取值 | 什么时候 |
| --- | --- |
| `0` | 接口文档没写明支持空张量/inf/nan。**社区算子的默认选择** |
| `all` | 文档逐条写明了边界行为，要按它验收 |
| 正整数 | 只要一小批边界用例控制耗时 |

roll 与 median 的文档都没说支持空张量，三个目标算子实测都用 `0`。
开成 `all` 会生成一批空张量用例然后整批假失败——那测的是参数校验不是计算。

**`name` 与 `aclnn_name` 一定不能是同一个接口。** `name` 是标杆语义（`torch.roll`），
`aclnn_name` 是被测接口（`Roll`）。填成一样等于拿被测算子比它自己。

## inputs 只列输入，不列 out

**`inputs` 只写 `facts.json` 里 `role: "input"` 的参数，顺序与签名一致。
输出参数不写进去。**

ATK 从 CPU 标杆的返回值推出输出张量的 shape 与 dtype，再自己把
`out`、`workspaceSize`、`executor` 三个位置补到 aclnn 调用尾部
（`atk/tasks/api_execute/aclnn_base_api.py:85` 起）。把 `out` 写进 `inputs`
会多出一个参数，pyaclnn 报「参数数量不匹配：传入 N 个，预期 N-1 个」。

`facts.json` 里仍然要列 `out`——那是签名事实，写下来才知道参数总数对不对。
两处的差别就在这：事实表记全签名，YAML 只记输入空间。

| 算子 | `facts.json` 的 params | YAML 的 inputs |
| --- | --- | --- |
| roll | x、shifts、dims、out | x、shifts、dims |
| median | self、dim、keepDim、valuesOut、indicesOut | self、dim、keepDim |

多输出算子（median 的 values + indices）靠 CPU 标杆返回元组表达，
ATK 按元组长度生成对应个数的输出张量，不需要在 YAML 里声明。

## name 决定传参方式

`name` 字段决定传参方式：

| `name` | 效果 |
| --- | --- |
| `null` | 位置参数，进 `input_data.args` |
| 字符串 | 关键字参数，进 `input_data.kwargs[name]` |

CPU 标杆是 `eval(name)(*args, **kwargs)`，所以 **`name` 要么全填 `null` 走位置参数，
要么填 torch 认识的关键字名**。填了 torch 不认识的名字（如 C 侧的 `valuesOut`）会
`TypeError: unexpected keyword argument`。拿不准就填 `null`。

## tensor 输入

```yaml
- name: null
  type: tensor
  required: true
  dtypes:
    values: [fp16, fp32, bf16]
  ranges:
    valid:
      values: [[-5, 5]]
  shapes:
    dim_numbers:
      values: [1, 2, 3, 4]
    dim_values:
      values: [1, 2, 7, 8, 15, 16, 31, 32, 63, 64, 127, 128, 255, 256, 511, 512]
    max_length: 1048576
  boundary:
    has_empty: false
    has_scalar: true
    has_infnan: false
    has_upper_border: false
    has_lower_border: false
```

| 字段 | 说明 |
| --- | --- |
| `dtypes.values` | ATK dtype 词表里的名字，来自 `facts.json` |
| `ranges.valid.values` | 值域，列表的列表，ATK 在里面均匀随机挑一项。**含整型 dtype 时不能只写 `[[-5, 5]]`**，见 case-strategy.md「张量取值」 |
| `shapes.dim_numbers.values` | 秩的取值，来自 `facts.json` 的 `shape.rank` |
| `shapes.dim_values.values` | 每一维的候选值，**必须是离散列表** |
| `shapes.max_length` | 单个输入的最大元素数，控制用例体积 |
| `boundary.*` | 边界用例开关，见下 |

### dim_values 绝对不要写成 range

```yaml
# ✗ 会按笛卡尔积展开，生成卡死
dim_values:
  range: [1, 1024]

# ✓ 离散列表
dim_values:
  values: [1, 2, 7, 8, 15, 16, 31, 32, 63, 64, 127, 128, 255, 256, 511, 512]
```

取值该覆盖哪些边界见 case-strategy.md「shape 边界对」。

### boundary 开关怎么定

| 开关 | 什么时候开 | 什么时候关 |
| --- | --- | --- |
| `has_empty` | 接口文档说支持空张量 | 文档写「不支持空张量」时**必须关**，否则整批假失败 |
| `has_scalar` | 秩下界是 0 或 1 | 秩下界大于 1 时关 |
| `has_infnan` | 浮点算子且文档没禁 inf/nan | 整型算子、索引类算子关 |
| `has_upper_border` | 全量跑测时开 | 默认关，它生成极大张量，冒烟阶段拖垮耗时 |
| `has_lower_border` | 同 upper | 默认关 |

`aclnnMedian.md` 写「不支持空张量（self 元素个数为 0 时返回 ACLNN_ERR_PARAM_INVALID）」，
对应 `has_empty: false`。这类约束在 `facts.json` 的 `constraints` 里，逐条要有落点。

## attr 输入

```yaml
# 整数属性
- name: null
  type: attr
  dtypes:
    values: [int]
  ranges:
    valid:
      values: [[-4, 4]]

# 布尔属性
- name: null
  type: attr
  dtypes:
    values: [attr_bool]
  ranges:
    valid:
      values: [true, false]

# 枚举属性
- name: null
  type: attr
  dtypes:
    values: [string]
  ranges:
    valid:
      values: ["sum", "mean", "none"]
```

`aclIntArray *` 这类整数数组属性用 `attrs` 并配 `tuple_numbers` 控制长度：

```yaml
- name: null
  type: attrs
  dtypes:
    values: [int]
  ranges:
    valid:
      values: [[-8, 8]]
  tuple_numbers:
    values: [1, 2, 3, 4]
```

数组长度与其它参数联动（如 `shifts` 与 `dims` 等长）时，YAML 表达不了，
要写约束器，见 plugin-authoring.md。

## 特殊值

`ranges` 里可以写这些字符串：

| 值 | 语义 |
| --- | --- |
| `"inf"` / `"-inf"` | 正负无穷 |
| `"nan"` | NaN |
| `"null"` | 空 Tensor |
| `"default"` | `None`，用于可选参数 |

可选指针参数（文档写「为 nullptr 时表示……」）就靠 `"default"` 表达：

```yaml
- name: null
  type: tensor
  dtypes:
    values: [int32, int64]
  ranges:
    valid:
      values: [[0, 0], "default"]
```

## 写完自检

- [ ] `inputs` 只含 `facts.json` 里 `role: "input"` 的参数，顺序与签名一致
- [ ] 输出参数（`out` / `valuesOut` / `indicesOut`）**没有**写进 `inputs`
- [ ] dtype 全在 ATK 词表里，没有 `FLOAT16` 这种没翻译的
- [ ] `dim_values` 是离散列表不是 range
- [ ] `facts.json` 的每条 `constraints` 都能指出落在 YAML 哪个字段或约束器哪一行
- [ ] `name` 与 `aclnn_name` 不是同一个接口
- [ ] 浮点阈值写 `1.0e-5` 不写 `1e-5`（YAML 会把后者解析成字符串）
