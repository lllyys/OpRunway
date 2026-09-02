# 事实表 FACTS 规范

## 目录

- [校验入口](#校验入口)
- [顶层键](#顶层键)
- [参数角色与属性](#参数角色与属性)
- [layout 双向引用](#layout-双向引用)
- [batch 与 producer](#batch-与-producer)
- [dtype 与 profiles](#dtype-与-profiles)
- [表达式白名单](#表达式白名单)
- [verify 词表](#verify-词表)
- [edge_cases](#edge_cases)
- [用例生成配置](#用例生成配置)
- [能力边界](#能力边界)
- [schema v2：harness 域包](#schema-v2harness-域包)
- [CSV 表头投影](#csv-表头投影)
- [常见报错与改法](#常见报错与改法)
- [完整示例](#完整示例)

事实表 FACTS 是任务包的唯一结构化输入。执行者先把任务书中的接口事实
写进 `gen_csv.py` 顶部的 `FACTS = {...}`，再让 `package.py` 校验。脚本只解析顶层赋值，
不会执行 `gen_csv.py`。

## 校验入口

在工作目录运行以下命令：

```bash
python3 <skill>/scripts/package.py check --facts gen_csv.py --print-header
```

退出码含义如下：

| 退出码 | 含义 | 处理 |
| --- | --- | --- |
| 0 | 事实表 FACTS 合法 | 进入渲染阶段 |
| 2 | 字段、引用或表达式不合法 | 按 stderr 的字段路径逐条修正 |
| 3 | 文件或字面量读不出来 | 修文件路径或语法，不要改校验器 |

`--facts` 也接受 `.json`。Python 文件的 FACTS 区只允许可选 shebang、编码注释、
可选模块 docstring、注释和唯一的 `FACTS = {...}` 字面量赋值。其他 AST 语句按行拒绝；
`check/render` 不导入或执行任务包文件，只把解析结果传给 skill 自带模板。

## 顶层键

事实表 FACTS 的顶层只开放以下键：

| 键 | 必填 | 取值 |
| --- | --- | --- |
| `schema_version` | 是 | 整数，`1` 或 `2`（v2 见「schema v2：harness 域包」一节） |
| `generator_version` | 是 | 整数，按版本矩阵：schema 1 与 2 目前都要求 `1` |
| `op` | 是 | 小写标识符 |
| `family` | 是 | 标识符，同时是 `test/<family>/` 与 `blas/<family>/` 的目录名 |
| `symbol` | 是 | 公开 C 函数名 |
| `returns` | 是 | 非空返回类型字符串 |
| `params` | 是 | 按任务书 C 原型的顺序排列的非空参数列表 |
| `constraints` | 否 | 表达式字符串列表，默认 `[]` |
| `golden` | 是 | `{kind, symbol?, formula?}` |
| `verify` | 见注 | 不重复的非空校验策略列表；`golden.kind=harness` 时不接受此键 |
| `edge_cases` | 否 | 边界用例列表，默认 `[]` |
| `perf` | 否 | 性能基线字典 |
| `dtype_profiles` | 条件 | 有参数使用 `dtype_from` 时必填，否则禁止出现 |
| `cases` | 否 | 用例轴和单行 footprint 上限的可选覆盖 |
| `sources` | 是 | 自由的 str→str 字典；至少含 `params` |

`sources` 的键由事实类别命名，值必须是非空字符串。`params` 写任务书里参数表或签名
所在的位置，例如 `任务书 §2.3`；case-gen 只从任务书取事实，不写 header/inferred 来源。

`golden.kind` 只能是 `cblas`、`lapacke`、`loop` 或 `composed`。前三种必须给
`symbol`，`composed` 必须给 `formula`。

`perf` 的结构是
`{key: [参数名...], rows: [...], sweep: bool, meta: {...}, threshold: number}`。
`key` 必须非空，每项引用普通 `enum`、`dim`、`layout` 参数或虚拟键 `profile`。
dtype/compute 参数不能直接作 key；混合精度 PF 行用 profile 名选择完整类型组合。
`gpu_ms` 可选，缺省表示该行只采集不评判。`sweep` 默认是 `False`。
`threshold` 可选，必须大于 0，默认 `0.8`；性能通过条件是
`gpu_ms / npu_ms >= threshold`。

`perf` 节存在时 `rows` 固定 200 行——每算子 200 个性能点位，`check` 机械强制。任务书给出的
GPU 实测点照抄并填 `gpu_ms`；不足 200 的部分由写 FACTS 者补设计点位（沿任务书的参数轴铺开），
省略 `gpu_ms`。每行的性能键——`perf.key` 各列的值经归一（去首尾空白、整数字符串转整数）后
组成的元组——必须唯一，`check` 与渲染出的量具用同一条归一规则判重。

GPU 数据尚未就绪时省略 `gpu_ms`，渲染出的 `gpu_baseline.csv` 就是键列齐全、`gpu_ms` 空置的
待填表。回填走两条路之一：把实测值填回 FACTS 再重渲染，`check` 保持可过；或直接往包内
待填表填数、交 accept 验收——此后包已偏离渲染产物，不要再跑 `package.py check`（派生文件
逐字节比对会失败）。配上数的行在验收时自动进入性能期望集。

`perf.meta` 可省略；存在时必须是字符串到字符串的字典，只开放以下六个键：

```text
timing_scope, device, library, warmup, statistic, source
```

未填写的元数据在 `gpu_baseline.csv` 中写成 `unspecified`，不由渲染器猜测。
msprof kernel 采集、统计、scope caveat 与退出码见
[perf-protocol.md](perf-protocol.md)。

## 参数角色与属性

每个参数都必须有唯一标识符 `name`、非空字符串 `ctype` 和 `role`。基础键之外，
每个 role 只开放表中的属性：

| role | 开放属性 | 必要规则 |
| --- | --- | --- |
| `handle` | 无 | 必须是首参，ctype 必须是 `aclblasHandle_t` |
| `enum` | `values`、`enum_kind` | `enum_kind` 取 `op/dtype/compute/algo`，默认 `op` |
| `dim` | 无 | 用于维度表达式 |
| `layout` | `kind`、`of` | `kind` 取 `ld/inc/stride/batch`；`kind=batch` 可省 `of` |
| `scalar` | `dtype/dtype_from`、`mem`、`nullable`、`values` | 方向固定为 in |
| `inout_scalar` | 同 `scalar` | 方向固定为 inout |
| `out_scalar` | 同 `scalar` | 方向固定为 out，不写 `dir` |
| `vector` | dtype、`dir`、`len`、`inc`、nullable、batch、producer | dir 默认 in |
| `fixed_vector` | dtype、`dir`、`len`、`nullable`、`samples` | len 是正整数字面量 |
| `matrix` | dtype、dir、形状、布局、结构与控制属性 | 见下文 |
| `int_array` | `dtype`、`dir`、`len`、`producer`、`nullable` | dtype 默认 int32 |

表中的“dtype”表示 `dtype` 与 `dtype_from` 必须且只能出现一个。`mem` 只能是
`host` 或 `device`，默认 `device`；`nullable` 必须是 bool，默认 `False`。

`enum_kind` 的含义和事实出处如下；校验器实际开放四种值，不是三种：

| kind | 含义 | 值的权威来源 |
| --- | --- | --- |
| `op` | 转置、填充、左右侧等操作选择 | 任务书；已知 ctype 受 CSV 词表约束 |
| `dtype` | 决定 buffer 或标量存储 dtype | `csv_loader.h` 的 DataType 解析表 |
| `compute` | 决定计算或 execution 精度 | `csv_loader.h` 的 ComputeType/DataType 解析表 |
| `algo` | 选择公开算法枚举，不决定 dtype | 任务书 |

`scalar` 和 `inout_scalar` 的 `values` 是可选非空列表；实数 dtype 的元素是 int
或 float，复数 dtype 的元素是 `[re, im]`。
`dtype_from` 标量的每个值必须与
所有 profile 的 `scalar_dtype` 都匹配。

`fixed_vector` 在 `dir=in/inout` 时必须给 `samples`，每个 sample 都是长度等于
`len` 的 int/float 列表；`dir=out` 时禁止出现 `samples`。
这两条共同保证输入样本
可物化，而输出不携带伪输入。

`vector.inc` 必须引用 `kind=inc` 的 layout，或直接写整数字面量 `1`。`matrix.ld`
必须引用 `kind=ld` 的 layout；只有 `storage=packed` 禁止写 `ld`。
`dir` 只能是 `in`、`inout` 或 `out`，其他方向没有生成语义。

矩阵还受以下结构规则约束：

- `order` 只能是 `col_major` 或 `row_major`，默认 `col_major`。
- `storage` 默认 `full`。
- 其他 storage 是 `upper/lower/symmetric/hermitian/triangular/banded/packed/lu_factorized`。
- `symmetric`、`hermitian`、`triangular` 必须给引用 enum 参数的 `uplo`。
- `triangular` 还必须给引用 enum 参数的 `diag`。
- `banded` 必须给引用 dim 参数的 `kl` 与 `ku`。
- `conditioning` 是字符串列表；存在时会投影矩阵构造类型列。

`int_array.dir` 必填。方向是 `in` 或 `inout` 时，必须给 `producer`；方向为 `out`
时可省。
producer 负责说明输入整数数组由哪个前置调用产生。

事实表 FACTS 至少声明一个输出，可以是 `out_scalar`、`inout_scalar`，也可以是
`out` 或 `inout` 的 buffer。
没有可观察输出时，调用成功不能构成精度验证。

已知 aclblas enum 的 `ctype` 与 `values` 必须符合
[aclblas-conventions.md](aclblas-conventions.md) 的短记号表。
`enum_kind=dtype` 的 ctype 必须是 `aclDataType`；`enum_kind=compute` 通常使用
`aclblasComputeType_t`，任务书明确把
`executionType` 声明为 `aclDataType` 的接口保留该 ctype。

## layout 双向引用

`layout.of` 接受一个参数名或参数名列表。它只能引用 `vector`、`matrix`、
`fixed_vector` 或 `int_array`，并且两侧必须互相指向：

- `kind=inc` 对应目标参数的 `inc`。
- `kind=ld` 对应目标参数的 `ld`。
- `kind=stride` 对应目标参数的 `batch.stride`。
- `kind=batch` 对应目标参数的 `batch.count`；这类 layout 可省 `of`。

双向检查防止生成器只看一侧时把布局参数绑定到错误的 buffer。

## batch 与 producer

`batch` 的结构如下：

```python
{
    "model": "ptr_array",  # ptr_array / strided / contiguous_implicit
    "count": "batch_count",
    "table_mem": "host",
    "element_mem": "device",
}
```

`count` 引用 dim 或 `kind=batch` 的 layout。`model=strided` 时必须增加 `stride`，
并引用 `kind=stride` 的 layout；`table_mem` 只允许用于 `ptr_array`。内存字段都只能
取 `host` 或 `device`。

`producer` 是非空调用描述，例如
`lapacke_sgetrf(A)`；括号中的每个名字必须是已声明参数。

## dtype 与 profiles

dtype 只能从以下词表中选：

```text
float32, float64, float16, bfloat16, complex64, complex128,
int8, uint8, int16, uint16, int32, int64
```

buffer 角色的 `dtype_from` 必须引用 `role=enum` 且 `enum_kind=dtype` 的参数。
`scalar`、`inout_scalar` 和 `out_scalar` 也可引用 `enum_kind=compute`；此时具体
dtype 取每个 profile 的 `scalar_dtype`。只要使用了 `dtype_from`，就必须提供非空
`dtype_profiles`；否则禁止出现 profiles。

每个 profile 的结构如下：

| 键 | 必填 | 规则 |
| --- | --- | --- |
| `name` | 是 | 唯一标识符 |
| `assign` | 是 | enum 参数与其 values 中一个值的对应关系 |
| `scalar_dtype` | 是 | 动态标量的 dtype；引用 compute enum 时据此确定类型 |
| `golden_dtype` | 是 | dtype 词表值 |
| `precision_row` | 是 | `FLOAT32/FLOAT16/BFLOAT16` |

`assign` 的键只能引用 `enum_kind=dtype/compute` 的 enum。`algo` 是独立轴，禁止由 profile
覆盖。每个 profile 都必须覆盖所有被 `dtype_from` 引用的 enum，避免类型未确定。

混合精度 profile 的 `precision_row` 取输出 dtype 对应的生态阈值行，不取输入 dtype、
compute dtype 或 golden dtype。输出有多个 dtype 时，任务书必须先给出统一验收口径；
没有口径就报告能力边界。

## 表达式白名单

`rows`、`cols`、`len` 和 `constraints` 使用 Python 表达式字符串，只开放以下节点：

- 名字：dim、enum、layout 参数；buffer 只能直接作为 `rows/cols/len` 的实参。
- 常量：整数；字符串只能在 Compare 中与 enum 参数比较。
- 算术：`+`、`-`、`*`、`//`、`%` 和一元负号。
- 比较：`==`、`!=`、`<`、`<=`、`>`、`>=`。
- 逻辑：`and`、`or`、`not`。
- 条件表达式：`a if condition else b`。
- 调用：只允许 `max`、`min`、`rows`、`cols`、`len`，参数递归遵守同一规则。

属性访问、下标、lambda、推导式、函数定义、关键字参数和其他节点都拒绝。报错会列出
原表达式与 AST 节点类型，便于定位。

## verify 词表

`verify` 必须非空且不得重复，只能使用以下 token：

```text
full, uplo_triangle, non_uplo_exact, hermitian_diag, vector_inc, scalar,
inout_scalars, index_exact, solution_residual, lu_reconstruction,
inverse_residual, qr_reconstruction, orthogonality, pivot_validity,
info_exact, batch_each
```

## edge_cases

每个 edge case 都必须给唯一 `name`、字典 `set`、`expect` 和非空 `source`。
`expect` 必须属于状态码词表；仅有 `ACLBLAS_STATUS_` 前缀不能证明它可解析。

`set` 只允许以下键：

- role 是 `enum/dim/layout/scalar/inout_scalar` 的参数名；值类型由渲染阶段解释。
- nullable 参数的 `null<Name>`；`Name` 是参数名首字母大写后的结果。
- 带 batch 参数的 `<name>_batch_pattern`。
- `dir=in/inout` 且 `len` 为整数字面量的 fixed_vector 元素列 `<name><i>`。

fixed_vector 元素的下标范围是 `0 <= i < len`，值必须是 int 或 float。本轮不支持
`dtype=complex64/complex128` 的 fixed_vector 元素。
不支持时校验器直接报能力边界，不把复数拆成隐式列。

batch pattern 可取 `UNIFORM`、`NULL_TABLE`、`MIXED_SINGULAR`，也可取
`NULL_ELEMENT_i`，其中 `i` 是非负整数。
每个值都直接进入 CSV 控制列。

ED 的 `set` 对 layout 参数直接写最终整数，不写 `min/pad` tier；enum 只能写该参数
`values` 中的声明值。
dtype/compute enum 可借此表达不支持组合，且 ED 行不要求组合属于
某个 profile；其他块仍必须使用 profile 中完整声明的组合。

## 用例生成配置

`cases` 只开放以下可选键：

| 键 | 取值 |
| --- | --- |
| `dim_tiers` | 非空正整数列表，覆盖矩阵维度轴 |
| `vec_dim_tiers` | 非空正整数列表，覆盖纯向量长度轴 |
| `batch_tiers` | 非空正整数列表，覆盖 batch 轴 |
| `inc_tiers` | 非空、非零整数列表，覆盖 inc 轴 |
| `fill_tiers` | 非空 METHOD_PATTERN_VAL 字符串列表，不接受 NULLPTR |
| `max_footprint_bytes` | 正整数，覆盖单行 host 侧缓冲上限 |

`fill_tiers` 的语法来自 `test/frame/fill.h` 36–44 行：METHOD 只能是
`INDEX/RANDOM/VALUE`，后接可选 PATTERN 与值片段。
数值片段接受小数和指数写法，例如 `RANDOM_0.1_0.1`；解析器要求所有片段被完整消费，
未知 pattern 或多余值会被拒绝。NULLPTR 只能由空指针控制列表达。

合法负步长放进 `cases.inc_tiers`，让它参与普通轴和 pairwise。只有任务书同时给出失败
状态码的非法步长，才写进 `edge_cases[].set`。
轴表达合法值，edge 表达带状态码的负例。

轴派生、四块生成、pairwise 和包级校验见
[csv-and-blocks.md](csv-and-blocks.md)。

## 能力边界

下列接口形态不在当前 FACTS v1 的可生成范围内：

- GroupedBatched 需要按 group 变化的 enum、shape、ld 和 scalar 数组；固定长度角色无法表达。
- blasLt 的 descriptor、attribute buffer、heuristic 结果与 workspace 不符合首参 handle 模型。
- LAPACK producer 链目前只保存调用描述，尚不能机械验证跨调用 dtype、长度、batch 对齐和顺序。

strided vector 与 packed matrix 已支持 footprint 和最小 stride。遇到上述未支持形态时停止，
记录能力边界，不把变长结构压进 fixed_vector 或自由文本后继续声称可生成。

## CSV 表头投影

`--print-header` 先投影两个固定列，再按 params 顺序处理参数：

| role 与条件 | 投影列 |
| --- | --- |
| handle、out_scalar、int_array | 不投影 |
| fixed_vector/vector/matrix 且 `dir=out` | 不投影 |
| enum、dim、layout | `<name>` |
| scalar/inout_scalar，dtype 是复数 | `<name>_re, <name>_im` |
| scalar/inout_scalar，其他固定 dtype | `<name>` |
| scalar/inout_scalar，dtype_from 的任一 `scalar_dtype` 为复数 | `<name>_re, <name>_im` |
| scalar/inout_scalar，dtype_from 的 `scalar_dtype` 全为实数 | `<name>` |
| vector/matrix，方向为 in/inout 且没有 producer | `<name 小写>_fill`，如 `A` 投影 `a_fill` |
| 上一行的 matrix 有 conditioning | 再加 `<name>_matrix_type` |
| vector/matrix 有 producer | 不投影 |
| fixed_vector，方向为 in/inout | `<name>0` 到 `<name>{len-1}` |

参数列后依次加入 `expect_result`、控制列和 `random_seed`。控制列按 params 顺序生成：
每个 nullable 参数加 `null<Name>`。
每个带 batch 的参数加
`<name>_batch_pattern`。

## 常见报错与改法

### `params[0] 必须是 role=handle`

按任务书 C 原型把 `aclblasHandle_t handle` 放到 params 首位。
不要省略 handle，也不要把它
写成 enum 或普通指针参数。

### `未开放的顶层键`

删除该键，或把事实移到已有字段。
顶层键是脚本与模板之间的版本契约；近似键若静默
通过，会让事实看似存在却永远不参与生成。

### `role=X 未开放属性 Y`

按 role 表删除或重新分类属性。
每种参数只有一套生成语义；跨 role 携带属性会让字段
出现两种解释。

### `dtype 与 dtype_from 必须且只能出现一个`

固定类型写 `dtype`，由 enum 决定类型时只写 `dtype_from`。
同时给两者无法确定权威，
两者都不给则无法选择数据生成器。

### `dtype_from 必须引用 enum_kind=dtype`

把 buffer 的 `dtype_from` 指向 dtype enum。
标量角色也可指向 compute enum，但每个
profile 必须用 `scalar_dtype` 给出具体类型，且 `assign` 必须覆盖该 compute enum。

### `layout.of 未反向包含` 或 `该参数未反向引用`

同时修 layout 的 `of` 和 buffer 的 `inc/ld/batch` 字段。
双向引用可独立确认绑定，
避免列顺序变化后布局参数误配。

### `表达式 ... 含不允许的 Subscript`

把下标逻辑改写为白名单表达式，或先抽成 dim/layout 参数。
表达式会进入生成代码，收窄 AST
节点可以阻止属性读取、任意求值和隐式状态依赖。

### `dtype_profiles 必填` 或 `assign 未覆盖`

为每种合法类型组合补 profile，并覆盖所有被 `dtype_from` 引用的 enum。
profile
记录动态 dtype、scalar、golden 和精度标准的完整对应关系，缺一项就无法生成
可比较结果。

### `edge_cases[*].set 含未知键`

改成开放参数名、派生控制列或合法的 fixed_vector 元素列。元素列只允许
`dir=in/inout`、整数字面量 `len`、实数 dtype 和 `0 <= i < len`；越界时修正下标。
set 会直接投影到 CSV；保留其他未知列只会产生没有生效的伪覆盖。

### `params 至少要有一个输出或 inout 参数`

按任务书 C 原型把结果参数标成输出角色或 `dir=out/inout`。
无可观察结果就无法做精度验证，
即使调用返回成功也不构成有效测试。

### `values 含 ...` 或 `enum_kind=... 时 ctype 必须是 ...`

先按约定表把全名或单字母缩写改成 CSV 短记号，再核对 enum 的 C 类型。
比如
`ACL_FLOAT` 写成 `FP32`，`ACLBLAS_UPPER` 或 `U` 写成 `UPPER`。

## schema v2：harness 域包

v2 面向「golden 与校验由开发者 harness 自带」的算子域（当前是 ops-sparse 的 frame
惯例）。v1 的一切规则照旧且行为逐字节不变；下面只写 v2 新增或不同的部分。

新增顶层键：

| 键 | 必填 | 取值 |
| --- | --- | --- |
| `harness_profile` | 是 | registry 里的 profile 键名（如 `sparse_frame`）；域级列名与词表默认都从它取 |
| `status_vocab` | 是 | 本算子 expect 列的精确词表，必须是 profile `status_vocab_bound` 的子集 |
| `harness_overrides` | 否 | 可覆盖键恰四个，整键替换；键名见下方「覆盖上界」 |
| `case_controls` | 否 | 造数控制列表，见下 |

**registry** 是模板通用代码区里的 `HARNESS_REGISTRY` 数据表：每个域一档（profile），
九个字段装仓级默认与闭合上界。域差异只进数据，引擎不按域开分枝。

v2 的其它规则：

- `golden` 开放 `{"kind": "harness"}`：golden 与校验由仓内 harness 自带（golden.h），
  与 `symbol`/`formula` 互斥，且整个 `verify` 键不适用（写了就报错）。
- **不投影参数**：`params` 仍逐字对应 C 原型；由 harness 自行取值、派生或造数的参数加
  `"projection": "none"`（只对 `enum`/`dim`/`int_array` 开放）。引擎对它列、轴、state、
  null/batch 控制列全静默；`int_array` 不投影时免 `producer`；这类参数不能作
  `perf.key`，也不能出现在 `edge_cases` 的 `set` 里。
- **case_controls**：`{name, kind: enum|tier, values}`。值是原始字符串端到端传递，判等即
  文本相等；每个 control 产一个直接列并进轴，可作 `perf.key`。控制名不得与任何参数名
  （含不投影参数）、`profile`、基座列或阈值列冲突，引擎在列与轴两侧都会机械拒绝。
- **覆盖上界**：可覆盖键是 `seed_columns`、`description_column`、`expect_column`、
  `threshold_columns`。`threshold_columns` 只能取三档（空表 / profile 默认前两件 / 三件套）；
  `expect_default_token` 不可覆盖（改它就是改 profile，走 registry 评审）。本版阈值列
  值源未建，要求覆盖为空表，非空即 fail-closed 拒绝。把 `expect_column` 覆盖为 `None`
  时 profile 的 `expect_default_token` 闲置不消费，不视为违反「无 expect 列时 token 为
  none」的 profile 级不变量。
- **expect 默认 token**：resolved 后仍有 expect 列时，profile 的 `expect_default_token`
  必须落在本算子 `status_vocab` 内；`edge_cases` 的 `expect` 也按 `status_vocab` 校验。
- **版本矩阵**：`generator_version` 按 `GENERATOR_VERSION_BY_SCHEMA` 校验（schema 1→1、
  schema 2→1）。某一行升版意味着公共代码区对该版包不再逐字节兼容，届时对应包的两个
  verify 脚本与 README 摘要按重钉协议重录。
- **`footprint_policy: no_static_check` 的作者责任**：引擎不做静态显存估算，用例规模
  上界由作者在轴与 `perf.rows` 网格里自行封顶。写 FACTS 时先算 harness 派生量的最坏值
  （如 coo2csr 的 `targetNnz = int(m*n*(1-sparsity))` 是 C 的 int），确保不溢出、不超设备
  内存，并把封顶依据写进 FACTS 注释。

## 完整示例

enum、状态码与参数记号以 [aclblas-conventions.md](aclblas-conventions.md) 为准。
示例只是完整字段组合，不覆盖约定表的事实优先级。

```python
FACTS = {
    "schema_version": 1,
    "generator_version": 1,
    "op": "cherk",
    "family": "herk",
    "symbol": "aclblasCherk",
    "returns": "aclblasStatus_t",
    "params": [
        {"name": "handle", "ctype": "aclblasHandle_t", "role": "handle"},
        {
            "name": "uplo",
            "ctype": "aclblasFillMode_t",
            "role": "enum",
            "values": ["UPPER", "LOWER"],
        },
        {
            "name": "trans",
            "ctype": "aclblasOperation_t",
            "role": "enum",
            "values": ["N", "C"],
        },
        {"name": "n", "ctype": "int", "role": "dim"},
        {"name": "k", "ctype": "int", "role": "dim"},
        {
            "name": "alpha",
            "ctype": "const float*",
            "role": "scalar",
            "dtype": "float32",
            "mem": "device",
            "nullable": True,
        },
        {
            "name": "A",
            "ctype": "const aclblasComplex*",
            "role": "matrix",
            "dtype": "complex64",
            "dir": "in",
            "rows": "n if trans == 'N' else k",
            "cols": "k if trans == 'N' else n",
            "ld": "lda",
            "nullable": True,
        },
        {
            "name": "lda",
            "ctype": "int",
            "role": "layout",
            "kind": "ld",
            "of": "A",
        },
        {
            "name": "beta",
            "ctype": "const float*",
            "role": "scalar",
            "dtype": "float32",
            "mem": "device",
            "nullable": True,
        },
        {
            "name": "C",
            "ctype": "aclblasComplex*",
            "role": "matrix",
            "dtype": "complex64",
            "dir": "inout",
            "rows": "n",
            "cols": "n",
            "ld": "ldc",
            "storage": "hermitian",
            "uplo": "uplo",
            "nullable": True,
        },
        {
            "name": "ldc",
            "ctype": "int",
            "role": "layout",
            "kind": "ld",
            "of": "C",
        },
    ],
    "constraints": [
        "n >= 0",
        "k >= 0",
        "lda >= max(1, rows(A))",
        "ldc >= max(1, rows(C))",
    ],
    "golden": {"kind": "cblas", "symbol": "cblas_cherk"},
    "verify": ["uplo_triangle", "non_uplo_exact", "hermitian_diag"],
    "edge_cases": [
        {
            "name": "zero_n",
            "set": {"n": 0},
            "expect": "ACLBLAS_STATUS_SUCCESS",
            "source": "任务书里 n 参数边界所在的节",
        },
        {
            "name": "null_a",
            "set": {"nullA": True},
            "expect": "ACLBLAS_STATUS_INVALID_VALUE",
            "source": "任务书里空指针规则所在的节",
        },
    ],
    "perf": {
        "key": ["n", "k", "uplo", "trans"],
        "rows": [
            {"n": 1024, "k": 1024, "uplo": "UPPER", "trans": "N", "gpu_ms": 0.314},
            {"n": 2048, "k": 2048, "uplo": "UPPER", "trans": "N", "gpu_ms": 1.929},
            {"n": 1024, "k": 1024, "uplo": "LOWER", "trans": "C", "gpu_ms": 0.250},
            {"n": 2048, "k": 2048, "uplo": "LOWER", "trans": "C", "gpu_ms": 2.024},
        ],
        "sweep": False,
    },
    "sources": {
        "params": "任务书里接口签名与参数表所在的节",
        "golden": "任务书里 golden 定义所在的节",
        "perf": "任务书里性能基线所在的节",
    },
}
```
