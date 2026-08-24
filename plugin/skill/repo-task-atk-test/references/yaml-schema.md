# ATK 用例设计 YAML

## 目录

- 顶层字段
- 输入契约
- 输出不在 YAML 里声明
- 取值和 shape
- 标准与模板
- 校验

## 顶层字段

YAML 由 `make_yaml.py` 从物化后的 `must_cover.json` 推导。

声明文件的 `yaml` 块只保存头部字段：定位字段 `name`、`aclnn_name`、`kernel_name`。

通用字段 `version` 和 `api`。

路由字段 `api_type`、`aclnn_api_type`、`generate` 和 `standard`。

`api_type` 绑基线执行器，`aclnn_api_type` 绑待验收算子执行器，`generate` 绑生成器。

三者各管一侧，缺哪一个就走哪一侧的 ATK 默认路径，不会报「没配」。

以下字段由组合推导，不手写：输入 `type`、dtype、rank、维度值、tuple 长度和 shape 分布。

`int`、`float`、`bool` 和 `string` 等 attr/scalar 的 dtype 必须来自参数契约。

接口模式与字段映射：

| 模式 | 定位字段 | 后端 |
| --- | --- | --- |
| pytorch | name | npu |
| aclnn | aclnn_name | pyaclnn |
| c_api | name | npu |
| kernel | kernel_name | kernel |

## 输入契约

每个 YAML 输入在 `must_cover.parameters` 中有同名契约：

```json
{
  "element_kind": "tensor",
  "runtime_container": "single",
  "nullable": false
}
```

`element_kind` 只能是 `tensor`、`scalar` 或 `attr`。

`runtime_container` 只能是 `single`、`list` 或 `tuple`。

复合 attr 使用 `attr_tuple`，YAML 使用 `attr_tuple`；列表使用 `attrs`。

真正不进入调用签名的占位参数写 `omitted: true`。

同名参数跨通道时写 `inputs.<name>` 或 `method_inputs.<name>`。

可选参数不能用空的复合输入组表示缺省。

## 输出不在 YAML 里声明

YAML 的 `inputs` 只写输入，**输出一个都不写**，多输出算子也不写。

这一条不写清楚，多输出算子（median 的 `valuesOut` + `indicesOut`）会让人以为
少了个 `outputs: [...]` 之类的块，然后去 ATK 源码里找——真机上为这一个问题
翻了十几次源码。

输出是这么来的：

1. 基线节点先跑一遍，返回值就是输出。返回 tuple 的按元素顺序展开成多个输出，
   记进 `task_result.output_info_list`。
2. aclnn 侧按这份清单逐个 `torch.empty` 出等形状、等 dtype 的 NPU 张量，
   **追加在全部入参之后**，再由 ATK 补 workspace 与 executor。

所以 C 声明的形状必须是「入参…出参…workspaceSize, executor」，而且

> **基线返回值的顺序 = C 声明里出参的顺序。**

`torch.median(input, dim, keepdim)` 返回 `(values, indices)`，
对上 `aclnnMedianGetWorkspaceSize(self, dim, keepDim, valuesOut, indicesOut, ...)`
的 `valuesOut, indicesOut`，一一对应。

两边顺序反了 ATK 不报错：它照位置填地址，精度对比一路跑完，结果全错。
这道核对由 `check_signature_contract.py` 在 S2 做，不要靠眼睛看。

基线一条输出都没产出时，ATK 抛 `标杆输出为空，请检查标杆是否运行失败或者没有输出`
——那是基线插件挂了，不是输出声明的问题。

依据 `atk/tasks/api_execute/aclnn_base_api.py:85-90`（出参追加在入参之后）、
`atk/tasks/backends/pyaclnn_backend.py:278,327-343`（按基线输出建 NPU 张量）。

## 推导不出来、必须自己判断的字段

以下字段 `make_yaml.py` 不会替你决定，写错的代价都在很靠后才暴露：

| 字段 | 语义 | 写错的后果 |
| --- | --- | --- |
| `outputs` | 只对 **in-place** 算子写，值是输出张量在 args 里的下标 | 非 in-place 函数写了它，ATK 丢弃返回值改从 `args[outputs]` 取，越界后被吞成 `RuntimeError: run opp task failed!`，整个分面用例全灭 |
| `api` | 纯元数据，默认 `pytorch`；只跟着用例进报告，不参与执行、绑定或产物命名 | 没有后果，也不要为它读源码——填什么都不改变行为 |
| `api_type` | **非 aclnn 侧**执行器注册名，默认 `function`；CPU/基线节点用它取执行器 | 写了但没有同名 `@register`，ATK 报「没有对应标杆 API 文件」 |
| `aclnn_api_type` | aclnn 执行器注册名，**只被 pyaclnn 后端消费** | 写在 aclnn 后端上不生效，绑定仍走默认路径 |
| `expected_error_msg` | 该用例的预期报错文本，由生成器逐条挂 | 文本来源不对就是误判，见下 |
| `boundary` | tensor 的边界场景开关，五项是 `has_empty`、`has_infnan`、`has_scalar`、`has_upper_border`、`has_lower_border`，省略时全开 | 非 tensor 输入写它直接报错 |
| `dtype_numbers` / `extra_numbers` / `shape_distributions` | ATK 自身的展开因子 | 枚举模式下必须退化成恒等（`1` / `0` / `[[0, 1.0]]`），否则 ATK 在必测集之外补随机用例，覆盖分母对不上 |

`api_type` 的取名不自由：ATK 拿它做**子串**匹配来决定还要不要额外装载输入。

名字里出现 `tensor` 就去找 `tensor_input.bin`，出现 `method` 就去找 `method_input.bin`。

没有声明对应输入组时该文件不存在，装载阶段直接 `FileNotFoundError`。

所以注册名里避开这两个词，除非确实声明了 `tensor_input` 或 `method_inputs`。

依据 `atk/tasks/executors/dataset_executor.py:80-83,172-188`。

`expected_error_msg` 的文本只能来自任务书或基线生态语义。

不要从待验收算子实现的源码里抄报错串——那等于让实现自证，而且同一接口的两个分面文本不一致时会被误判成阻塞。

任何一个分面拿不到有依据的预期文本，就不建该用例。

## 取值和 shape

tensor 默认从 combo 读取 dtype 和 shape；多输入用 `dtype_key`、`shape_key` 指定。

attr 的 `dtype` 写在契约中，不能从 combo 推断。

`range_values` 只描述合法占位值；生成器负责逐条覆写实际值。

shape 使用 `dim_numbers` 和 `dim_values` 表达 rank 与维度候选。

`max_length` 是单张量的**字节**预算，不是元素数；ATK 按 dtype 宽度换算元素上限。

预算给小了 ATK 只重掷不裁剪 shape，300 秒后抛超时，报错里会点名 `max_length`。

shape 关系由 combo 的语义轴和生成器决定，不在通用 YAML 中写算子专属逻辑。

支持的 `extract.from`：`input_dtype`、`input_shape`、`input_rank`、`input_numel`。

还支持 `input_bytes`、`scalar_dtype` 和 `attr`。

list/tuple attr 必须声明 `runtime_container`，并使用对应 YAML 类型。

## 标准与校验

`standard.acc` 由验收政策和比较器共同核对，阈值真源始终是精度标准那份 reference。

`standard.perf` 只是 ATK 性能标准表里的一个名字，取值 `key` / `not_key` / `DSA`。

26.8.8 里这三个名字的阈值完全相同，且只在**对比模式**下参与判定，绝对值模式不看它。

默认值就是 `not_key`，没有对比标杆时照抄默认即可，不必为它读 ATK 源码。

精度标准由验收政策和 `standard` 字段共同核对。

一份用例集只能使用一个比较器。

禁止在用例中携带阈值覆盖字段。

完整可跑的样例见 `assets/example/`，它由 `tests/test_assets_example.py` 守着，不会和脚本漂移。

不要照抄取值，只照抄结构。

校验顺序：能力门禁、生成前静态校验、一次 `atk case`、生成后结构校验、覆盖校验。

任何校验失败都停止，不修改已生成的 YAML 或用例 JSON。
