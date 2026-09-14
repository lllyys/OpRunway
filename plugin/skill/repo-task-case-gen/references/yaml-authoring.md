# ATK YAML 写法

用例设计文件定义输入空间与验收标准，`atk case` 按它生成用例 JSON。
字段真源是 ATK 的《用例设计文件说明》，本文只写生成社区算子用例时实际要填的部分。


## S2 用例设计

读 [yaml-authoring.md](yaml-authoring.md) 与
[case-strategy.md](case-strategy.md) 写 `<op>.yaml`；精度阈值只从
[precision-standard.md](precision-standard.md) 取，不凭记忆写 rtol/atol；
要不要写插件按 [plugin-authoring.md](plugin-authoring.md) 的判据表决定，
一条都不命中就什么都不写，`generate` 留 `default`。

**进了 S2 才想换基线接口的，回 S1 重走拍板流程。**

`accuracy.kind=builtin` 时这一步就要写 CPU 执行器，返回形状与 dtype 正确的张量即可，
写法见 [builtin-baseline.md](builtin-baseline.md)——**S4 才发现要写就晚了一轮**。

```bash
cd <工作目录> && <python> <skill>/scripts/gen_cases.py --dry-run
```

dry-run 用 `dtype_numbers=1` 跑，60 秒超时，验证 YAML 与约束器能组合出合法用例。
跑 `atk case` 之前它先静态查三件事，都在这一步就退 2：

| 拦什么 | 不拦会怎样 |
| --- | --- |
| YAML 的 `api_type` / `aclnn_api_type` / `generate` 与插件文件 `@register("…")` 的名字对不上 | 执行器要到 S4 才报「标杆一条都没跑出来」，白跑一轮；约束器更糟——ATK 退回默认生成器，约束**静默失效**，条数与 dtype 分布都正常 |
| 目录里有 YAML 没引用的残留插件 | 跟着用例包交到跑测侧被当本轮设计加载 |
| 执行器把 aclnn 入参解包又扔掉（口径与豁免见 plugin-authoring.md「执行器的边界」） | 那个参数从没被测到 |

**它不执行 `function_<op>.py`**——名字对得上不等于签名对，执行器第一次真正被调用是 S4。
第三条退 2 时二选一：

| 情形 | 怎么办 |
| --- | --- |
| 这个参数本来就该传 | 改执行器传上 |
| torch 侧确实接不住 | 在 `facts.json` 的 `baseline_params` 里写一行理由，跟着用例包交给跑测侧 |

```json
"baseline_params": {"scales": "torch 侧 size 与 scale_factor 二选一，给了 size 就传不了它"}
```
## 骨架

**在 `assets/skeleton.yaml`，不在这里。** 那是一个可执行文件，数值是唯一真源：

```bash
cp <skill>/assets/skeleton.yaml <op>.yaml
```

本文只讲每个字段填什么，**不重复具体数值**——数值的规则在
[case-strategy.md](case-strategy.md)，数值本身在 `skeleton.yaml`。

## 顶层字段

| 字段 | 填什么 | 依据 |
| --- | --- | --- |
| `api` | 固定 `pytorch` | CPU 标杆走 torch |
| `api_type` | `function`，写了执行器就填注册名 | CPU 侧执行方式 |
| `aclnn_api_type` | `aclnn_function`，写了执行器就填注册名 | NPU 侧执行方式 |
| `name` | `facts.json` 的 `baseline` | CPU 标杆靠它 eval |
| `aclnn_name` | `facts.json` 的 `aclnn_name` | pyaclnn 拼 `aclnn<Name>` 找符号 |
| `generate` | `default`，写了约束器就填注册名 | 见 plugin-authoring.md |
| `dtype_numbers` | 一开始就按回填表填 | 见 case-strategy.md。dry-run 时 `gen_cases.py` 自己拼 `-dt 1`，YAML 里这个值被忽略，不用为 dry-run 改它 |
| `extra_numbers` | 见下 | 边界用例开关的总闸 |
| `standard.acc` | `default` | 见 precision-standard.md |
| `standard.perf` | `not_key` | 性能任务另跑，这里不设阈值 |
| `outputs` | `null` | 用算子默认返回值 |

### 动态输入：`aclTensorList *`

**真机跑通过**（2026-08-27，一个 `aclTensorList*` 双输入算子，217 条用例含 golden）。
下面标「实测」的是那次量出来的，其余来自 `parameter_tensors.py:29-82`。

个数可变的张量输入写成 `type: tensors`，个数由 `tuple_numbers` 控：

```yaml
  - name: null
    type: tensors            # tensor_tuple 只差一点：生成的数据是 tuple 不是 list
    required: true
    dtypes:
      values: [fp16, fp32]
    tuple_numbers:
      values: [2, 3, 4]      # 每条用例随机抽一个当列表长度
    shapes:
      dim_numbers: {values: [1, 2, 3, 4]}
      max_length: 4194304
```

三处与单张量不一样，都会影响用例设计：

| 差别 | 单 `tensor` | `tensors` |
| --- | --- | --- |
| `max_length` 比的是什么 | 这一个张量的元素数 | **整个列表求和**，且用 `cases[0].dtype` 一个 dtype 算宽度 |
| 超限怎么处理 | `while` 循环整条重抽 | **预算减半后递归重来**（`parameter_tensors.py:78-80`） |
| 约束器里拿到什么 | 一个对象 | **一个 list**，改长度要切片赋值 |

第二条有个不在源码注释里、只有跑起来才看得到的后果（**实测**）：
`self.gen` 的元素预算被减半之后**不会恢复**（`generate_with_index` 只把外层字段
还原成 `max_length`），于是预算一路走低，抽形状的接受率跟着塌。秩越高塌得越快，
rank 8 会直接打满 ATK 的 300 秒守卫。

**所以 `tensors` 算子的 `dim_numbers` 更不能写 ≥5 的秩**，规则与判据同
[case-strategy.md](case-strategy.md)「高秩不靠抽样」，那节的接受率表对两种形态都成立。

### 张量列表**输出**：结果要包一层

输出是一个 `aclTensorList*` 时，CPU 执行器**不能直接返回 list**。ATK 的
`get_output_data_infos` 对返回的列表逐项递归再整体 append：

| 执行器返回 | `output_info.json` 变成 | NPU 侧 |
| --- | --- | --- |
| `[t0, t1, t2]` | `[[t0], [t1], [t2]]` | 每项各建一个 aclTensorList，第一段接口参数个数对不上，**全部用例执行失败** |
| `[(t0, t1, t2)]` | `[[t0, t1, t2]]` | 建**一个** out 张量列表，对 |

照抄 `assets/function_foreach_mul_list.py`。判断依据是**输出侧参数是不是
`aclTensorList*`**，与输入是不是列表无关——输入是列表输出是单张量的算子，
执行器照常写。

约束器里把 list 改成单个对象会被 ATK 拦下：

```
该输入应当是list, 请检查你的after_case_config是否把tensors类型的输入改成了tensor
```

**这个错会报出来**，不像钩子签名写错那样静默。

### extra_numbers 与 boundary 的关系

`extra_numbers` 是边界用例的总闸，`boundary.*` 是分项开关。总闸关了分项开也没用。

| 取值 | 什么时候 |
| --- | --- |
| `0` | 接口文档没写明支持空张量/inf/nan。**社区算子的默认选择** |
| `all` | 文档逐条写明了边界行为，要按它验收 |
| 正整数 | 只要一小批边界用例控制耗时 |

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

取值该覆盖哪些边界见 case-strategy.md「shape：2^n 与 2^n−1 边界对」。

### boundary 开关取值

| 开关 | 什么时候开 | 什么时候关 |
| --- | --- | --- |
| `has_empty` | 接口文档说支持空张量 | 文档写「不支持空张量」时**必须关**，否则整批假失败 |
| `has_scalar` | 秩下界是 0 或 1 | 秩下界大于 1 时关 |
| `has_infnan` | 浮点算子且文档没禁 inf/nan | 整型算子、索引类算子关 |
| `has_upper_border` | 全量跑测时开 | 它把某一轴硬编码成 `2^31+1`，不受 `max_length` 约束 |
| `has_lower_border` | 同 upper | 同上 |

**五个分项在 ATK 里默认全是 `True`**（`atk/configs/design_config.py:321-325`），
要关就得逐项显式写 `false`；总闸 `extra_numbers: 0` 一关，分项开着也不生效。

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

`aclIntArray *` 这类整数数组属性，**YAML 里写 `attrs`**（`facts.json` 的 `atk_type` 写 `attr`，两处名字不同是对的），并配 `tuple_numbers` 控制长度：

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

### ATK 表达不了的两种取值

| 想要什么 | 为什么造不出来 | 怎么办 |
| --- | --- | --- |
| `attrs` 列表为**空** | ATK 排序时取 `input_case[0]`（`atk/case_generator/utils/reports.py`），空列表直接 `IndexError: list index out of range`，整轮 `atk case` 崩掉 | `tuple_numbers` 的下界至少 1 |
| 某个 dtype 的 `scalar` 取值超出**另一个**参数 dtype 的表示范围 | 每个参数各自独立抽 dtype 与取值 | 在约束器里把它的 `dtype` 赋成被跟随的那个参数的 `dtype` |

第一条会连带影响秩的取法：某个参数「必须是空数组」的场景（如 0 维张量上的
`dims`）在 ATK 里表达不了，那一档只能不测，在交付简表的「遗留」里如实写明。

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
