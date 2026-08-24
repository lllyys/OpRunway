# ATK 会咬人的地方

写 S2 产物之前先读完这一页。

这里的每一条都不考验判断力，纯粹是知识：读了就能一次写对，读不到就必踩。

它们在量具里各有一道检查点拦着（门的名字附在每条标题后），但被拦下来
才知道有这道门，就已经多花了一轮。

判据来源是 ATK 源码与真机实测，每条都在正文里写清依据在哪份文件的哪一行。

各条都写了「不知道会怎样」——那是这份知识的价值所在。

没写后果的规则读起来像可选项，写了才知道它不是。

## 目录

- 插件注册
- 用例集会被 ATK 悄悄改动
- 取值与 dtype
- 接线字段
- 复合组

## 插件注册

### `atk node` 入口不会自动加载 `function_*.py`

门 `freeze_inputs.baseline_plugin`

「文件名以 `function_` 开头并与 case 文件同目录就自动加载」，只在
`atk aclnn` / `atk pytorch` 这两个别名入口成立（`atk/bin/op_alias.py:164`）。

本 skill 一条命令都不走那两个入口，走的是 `atk node … task -c`。

`task -p/--plugin_path` 接受一个 py 文件或一个目录，**只能给一次**（给两次是后一个覆盖前一个）。

所以生成器和 CPU golden 要放进同一个目录，`-p` 指那个目录：

```text
<工作目录>/plugins/
├── <op>_constraint.py     生成器
└── function_<op>.py       CPU golden
```

组合表在工作目录根上，插件里用 `Path(__file__).resolve().parents[1] / "..."` 读。

`freeze_inputs.py` 的 `-p` 也照这个写法给目录。

**不知道会怎样：** 不会有人说「插件没加载」。冻结那次跑测的 CPU 节点会拿默认的
`function` 执行器去 `eval(name)`，然后整批基线执行失败。真机实测（bernoulli，
2026-08-17）155 条全挂，而报错指向的是 YAML 输入名，真因是插件根本没进来。

### 加载到不等于用上

门 `validate_cases.registration`

ATK 按 YAML 的 `api_type` 取执行器，默认取内置的 `function`。

注册名要同时写进声明文件的 `yaml` 块：`@register("x")` 对 `"api_type": "x"`。

三个注册键各管一侧，不要混：

| 键 | 管谁 |
| --- | --- |
| `generate` | 生成器 |
| `api_type` | 基线侧执行器 |
| `aclnn_api_type` | pyaclnn 侧执行器 |

另外三条注册约束：文件内 `CaseGenerator` 子类数必须等于装饰器数；
mixin 必须在基类列表最前面；基类必须以 `CaseGenerator` 这个名字导入。

**不知道会怎样：** 两边对不上时 ATK 报「没有对应标杆 API 文件」，插件静静地不生效。
子类数与装饰器数对不上时，注册名会挂到基类上，你的覆写一条都不会被调用。

### `api_type` 的取名不自由

门 `validate_cases.registration`

ATK 拿 `api_type` 做**子串**匹配，决定还要不要额外装载输入
（`atk/tasks/executors/dataset_executor.py:80-83,172-188`）。

名字里出现 `tensor` 就去找 `tensor_input.bin`，出现 `method` 就去找 `method_input.bin`。

所以注册名里避开这两个词，除非确实声明了 `tensor_input` 或 `method_inputs`。

**不知道会怎样：** 没有声明对应输入组时该文件不存在，装载阶段直接 `FileNotFoundError`。

## 用例集会被 ATK 悄悄改动

### 三个展开因子必须退化成恒等

门 `validate_cases.case_set`

枚举模式下这三个键必须写成恒等值，`make_yaml.py` 已经固定这么写：

| 键 | 恒等值 | 不写会怎样 |
| --- | --- | --- |
| `dtype_numbers` | `1` | ATK 自行扩展 dtype，用例数与 must_cover 对不上 |
| `extra_numbers` | `0` | ATK 把某一轴硬编码成 `2^31+1`，撑爆单卡 |
| `shape_distributions` | `[[0, 1.0]]` | 与 combos 的实际 shape 冲突 |

`extra_numbers` 那条不受 `max_length` 约束。

**不知道会怎样：** 手改 YAML 打开规模配额，`super().generate()` 的 shape 泛化会挂住
——ATK 源码自己注明它可能死循环。

用例数对不上时，覆盖率的分母也就没有意义了。

### `CaseConfig` 没有 `coverage_tags` 字段

门 `validate_cases.case_set`

`CaseConfig` 只有 `id`，`extra` 未放开（`atk/configs/case_config.py`）。

覆盖标签留在物化组合表里，由 `check_coverage.py` 按 `extract` 规则回读，不进用例对象。

生成器能覆写的字段就这两张表：

| 对象 | 字段 |
| --- | --- |
| `CaseConfig` | `id`、`name`、`aclnn_name`、`version`、`expected_error_msg`、`api`、`api_type`、`aclnn_api_type`、`standard`、`outputs`、`inputs`、`method_inputs`、`tensor_input`、`compute_times`、`is_boundary`、`default_seed` |
| `InputCaseConfig` | `name`、`type`、`required`、`dtype`、`shape`、`range_values`、`backward`、`align_32B`、`outlier_values` |

**不知道会怎样：** 赋值直接 `ValueError: "CaseConfig" object has no field`。

### `self.index` 在返回前已经自增

门 `validate_cases.registration`

取本条组合要用 `self.index - 1`。

**不知道会怎样：** 用 `self.index` 会整体错位一条，最后一条越界。

## 取值与 dtype

### 显式 `range` 无条件压过按 dtype 的推导

门 `expressibility.value_ranges`

写了 `range` 就以它为准，没有任何东西核对它与 dtype 相不相容。

判据是可表示性，不是宽窄：区间越界的那一半会被 ATK 按 dtype 截断。

**拿哪个 dtype 去量，按参数分**：`dims` 的 dtype 轴是**张量**的 dtype，
attr 与 scalar 的 dtype 写在它们自己的契约里。

**不知道会怎样：** 真机事故（roll，2026-08-16）input 写 `range: [-5, 5]`，
dtype 轴里有 uint8/uint32——ATK 正态采样负均值后按 dtype 截断，整张清零，
5 条用例退化成常量张量，测不出任何错。

### attr 的 dtype 用 C++ 名

门 `expressibility.contracts`

`int64_t`、`bool` 是 attr 的写法，和 tensor 的 `int64` 不是一套。

attr / scalar 的 dtype 来自契约，不从 combos 猜。

**不知道会怎样：** 写混了推导不出正确的 ctype，绑定阶段才炸。

### 种子必须钉成一个常量

门 `validate_cases.seed_pinned`

接口声明里出现 `seed`、`offset`、`generator` 之类时，用例数据里必须把它钉成
同一个常量，由 constraint 逐条覆写，不留取值区间。

种子不是覆盖轴，不进 `dims`，也不参与组合展开。

ATK 的 `default_seed`（`atk/tasks/backends/backend.py:150`）只管**输入数据生成**，
管不到算子内部的随机数流，不能拿它代替这件事。

**不知道会怎样：** 一次性废掉三件事——冒烟与全量拿到的随机数流不同，冒烟通过
说明不了全量；`repro.sh` 复现不出报告里那条失败；与 CANN 内置实现逐位比对不成立。

## 接线字段

### 改接线必然要重跑 `atk case`

门 `check_adapter_binding.wiring`

ATK 从**用例 JSON** 的 `api_type` / `aclnn_api_type` 取执行器
（`atk/tasks/backends/backend.py:61`、`atk/tasks/backends/pyaclnn_backend.py:150`），
不是从 YAML 取。

用例里没写这个键不等于「没绑」，等于绑了 ATK 内置的默认执行器
（`atk/configs/case_config.py:90-91`，默认 `function` / `aclnn_function`）。

所以适配器判定必须在冻结之前做完。

已经冻结了就走受控通道 `rewire_adapter.py`，它只在用例语义未变时放行。

**不知道会怎样：** 真机实测（roll，2026-08-15）判断「S3 冒烟再说」，
于是 S3 才发现要改 YAML，而改 YAML 要重跑 `atk case`，正撞上「S2 后冻结」的纪律。

### 不要自己补 workspace 和 executor

门 `check_adapter_binding.wiring`

后端绑定函数时固定追加 `uint64_t* workspaceSize` 和 `aclOpExecutor** executor`。

optional pointer 的构造方式必须来自运行时契约表，不得用 `ctypes.c_void_p(0)`
伪造 typed optional pointer。

需要某个类型的构造方式时查这两张表，不要枚举常量、不要猜：

| 表 | 位置 |
| --- | --- |
| `CPP_TO_PYTHON_TYPE` | `atk.tasks.backends.lib_interface.acl_wrapper` |
| `PYTYPE_TO_CTYPE` | `atk.tasks.backends.pyaclnn_backend` |

**不知道会怎样：** 适配器再补一次 workspace 就多出两个参数，绑定必错。

### `outputs` 只对 in-place 算子写

门 `validate_cases.case_set`

值是输出张量在 args 里的下标。

输出参数由 ATK 按执行器契约追加，不要在输入列表里重复声明。

**不知道会怎样：** 非 in-place 函数写了它，ATK 丢弃返回值改从 `args[outputs]` 取，
越界后被吞成 `RuntimeError: run opp task failed!`，整个分面用例全灭。

## 复合组

### 组长度是 ATK 随机抽的

门 `expressibility.axis_values`

组长度来自 YAML 的 `tuple_numbers`，ATK 每条用例随机抽一个，抽完还洗牌
（L0 `group_length.per_case_selection` = `random_choice_then_shuffled`，
`group_length.follows_combo_order` = `false`）。

所以组长度与 combo 的顺序**没有对应关系**。

复合组在运行期是 `list[InputCaseConfig]`，不是单个对象。

per-combo 长度会变的复合组必须**整组重建**，写法见 `assets/example/constraint.py`
的 `rebuild_group`；逐个改元素只在长度恰好相等时才对。

**不知道会怎样：** 按 combo 顺序假设组长度，长度不等时改到别人的元素上，
跑得出数但比对必错。

这类算子重跑必然得到不同用例，`rewire_adapter.py` 会如实拒绝。

### 缺省参数不是空的复合组

门 `expressibility.axis_values`

`type="attr"`、`range_values=None` 表示**无效输入**，不表示缺省。

可选 attr 用显式缺省 token（L0 `default_token` = `default`），不创建空的复合 group。

bool attr 只使用 `True`、`False` 或 `default` 三种形态。

组的最小长度是 1（L0 `group_min_length`）。

**不知道会怎样：** 空复合组在可表达性检查就被拒；用 `None` 当缺省会被当成无效输入，
测的是错误路径而不是缺省语义。

### 能力矩阵随 skill 发布，别每轮重探

门 `validate_cases.runtime_structure`

运行期参数结构按版本化的能力矩阵检查，矩阵就是
`references/atk-parameter-capabilities.json`，验收期直接用。

查具体取值用 `scripts/atk_lookup.py <主题>`，不要 grep ATK 源码。

**只有装机 ATK 版本与矩阵记录的 `capability_id` 对不上时**，才跑
`probe_atk_capabilities.py` 重探，拿实测结果替换矩阵。

**不知道会怎样：** 每轮重探要真机跑一遍探针，白花时间；
反过来，版本真的不一致却照用旧矩阵，C6 会按过期的结构判定，放过真实的结构错误。

### 同一个输入不能既是平面 attr 又是复合 attr

门 `expressibility.axis_values`

序列语义必须在 `extract` 规则里声明 `runtime_container=list` 或 `tuple`。

**不知道会怎样：** 混合形态在同一份设计里表达不出来，要拆接口分面才装得下。
