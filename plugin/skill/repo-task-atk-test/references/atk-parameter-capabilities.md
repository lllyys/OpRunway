# ATK 参数能力域

## 目录

- 能填哪些键
- 参数结构
- C 签名反查
- 取值
- dtype 和可选参数
- 证据层级

## 能填哪些键

YAML 的 `backend`、`api_type`、`generate`、`standard.acc`、dtype 令牌都是**注册表的键**，填错即不生效或报错。

七张表的键都能查，不要去 ATK 源码里 grep：

```bash
<python> scripts/atk_lookup.py              # 列出可查的主题
<python> scripts/atk_lookup.py comparator   # 某个主题的全部取值
```


| 表 | 决定什么 |
| --- | --- |
| `backend` | 执行后端 |
| `api_type` | 调用形态，对应 YAML 的 `<前缀>api_type` |
| `comparator` | `standard.acc` 能填什么；`default` 就是 `mixed_tolerance_bm` |
| `run_mode` | 执行模式开关 |
| `generator` | 内置生成器；自写插件的注册名与它们同一命名空间 |
| `parameter_type` | YAML 的输入 `type` |
| `dtype` | dtype 令牌全集 |

同 JSON 的 `semantics` 记的是**语义**而不是键：查得到的行为不必再读源码，也不要凭直觉假设。

例如 `equal_passes_when_both_sides_are_all_nan` 为真——两侧全 NaN 时 `equal` 无条件判过。

这两块是**维护期**产物：`scripts/probe_atk_capabilities.py` 从装机 ATK 反射与实测，结果固化进 JSON 随 skill 发布。

验收期不要跑探针，查就够了。

查不到再读 ATK 源码，读完把结论补进 JSON。

只有 ATK 换版本时才重跑探针，届时键或语义变了要同步改本文件与受影响的门禁。

## 参数结构

能力矩阵记录 ATK 版本、后端、参数类型、容器和 optional 表示。

参数类型分为 tensor、scalar、attr、list 和 tuple。

复合参数必须同时满足 YAML 类型、runtime container 和参数契约。

普通 Python 对象不是 typed `aclTensor*`。

optional tensor 使用 ATK 暴露的 typed pointer alias 或 factory。

## C 签名反查

手里是 aclnn 的 C 签名，要倒推 YAML 怎么声明，查这张表，不要去读 ATK 源码。

pyaclnn 把复合组交给 `create_x_list`，产出哪种 acl 对象由**组内元素的运行时类型**决定：

| C 签名里的类型 | YAML 类型 | 契约 dtype | 元素落成 |
| --- | --- | --- | --- |
| `aclTensor*` | `tensor` | 来自 combo | `aclTensor` |
| `aclTensorList*` | `tensors` / `tensor_tuple` | 来自 combo | `aclCreateTensorList` |
| `aclScalar*` | `scalar` | 契约 | `aclScalar` |
| `aclScalarList*` | `scalars` / `scalar_tuple` | 契约 | `aclCreateScalarList` |
| `aclIntArray*` | `attrs` / `attr_tuple` | `int` | `aclCreateIntArray` |
| `aclFloatArray*` | `attrs` / `attr_tuple` | `float` 或 `float32` | `aclCreateFloatArray` |
| `aclBoolArray*` | `attrs` / `attr_tuple` | `bool` 或 `attr_bool` | `aclCreateBoolArray` |
| `int64_t` / `float` / `bool` / `char*` 等标量 | `attr` | 契约 | 直接 ctypes 标量，不是 acl 对象 |

同一个 `attrs` 组换个 dtype 就换一种 C 类型，所以 dtype 不是风格问题，是签名问题。

数组与标量的 dtype 白名单不是同一份：`int64_t` 只对第 8 行的标量 `attr` 成立。

`aclIntArray*` 写 `int64_t` 会被 C6 判 `unsupported_attr_array_dtype`，两份白名单见本 reference 的 JSON。

`attr` 与 `scalar` 的分岔只看类型名里有没有 `attr`：`attr` 走 ctypes 标量，`scalar` 走 `aclScalar*`。

三条会当场炸的边界：

- 组内元素类型必须齐一，混 dtype 报 `TypeError: All elements must be of type ...`，因为产出类型只按**第一个元素**判定。
- 空组拿不到 typed null，`create_x_list` 对空列表返回 `None`，所以缺省不能用空组表示。
- `attr` 的 dtype 不在 `PYTYPE_TO_CTYPE` 里直接 `ValueError`，dtype 白名单以本 reference 的 JSON 为准。

转换依据 `atk/tasks/backends/pyaclnn_backend.py:287-325`。

分派依据 `atk/tasks/backends/lib_interface/acl_wrapper.py:422-457`。

YAML 里写 `None` 由 ATK 自己转成 `ctypes.c_void_p(0)`，那是它的内部实现。

自己写执行器时仍然只能用 typed null，不要照抄这一处。

## 取值

`range_values` 表示生成范围或合法占位值。

`default` 是独立的 ATK token，不等于 JSON null。

list/tuple 取值必须匹配声明的容器。

复合参数不得用空组表示缺省。

## dtype 和可选参数

tensor dtype 来自 combo。

attr/scalar dtype 来自参数契约。

同一复合组的 dtype 必须满足后端能力矩阵。

可选参数使用显式 omitted 或 default 语义。

不要用 `ctypes.c_void_p(0)` 推断 optional pointer。

数组参数（`aclIntArray*` 等）允许 `nullptr` 时，三条路只有一条通：

| 想表达 | 写法 | 结果 |
| --- | --- | --- |
| 整组为空 | `attrs` 组给空列表 | 不通。`create_x_list` 对空列表返回 `None`，拿不到 typed null |
| 整组为 None | `attrs` 组整体置空 | 不通。组是逐成员转换的，没有「整组为 None」这条路径 |
| 该参数恒为 `nullptr` | 声明成 flat `attr` + `nullable`，占位写 `default` | 通。运行期取到 `None`，pyaclnn 转成 `c_void_p(0)` |

`default` → `None` 的落点是 `atk/case_generator/generator/data_types/data_standard.py:76-79`。

`None` → NULL 指针的落点是 `atk/tasks/backends/pyaclnn_backend.py:322-323`。

代价是 `type` 一个 YAML 只能写一次：同一个参数没法一部分用例传数组、另一部分传 `nullptr`。

任务书确实要求两种取值都验时才拆接口分面——拆一次要多一整套 YAML、必测集与跑测，不为「覆盖好看」而拆。

拆不动时（例如空数组触发的展平语义）写进覆盖留痕，记为 ATK 能力边界，不记为实现缺陷。

## 证据层级

能力门禁只使用本 reference 的版本化 JSON、脚本探测结果和公开运行契约。

待验收算子工程测试可以证明 C ABI，但不能证明 ATK 对象容器或运行时语义。

无法确认参数类型、同类型顺序、workspace、executor 或 optional 构造时停止。
