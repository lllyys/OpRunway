# 生成器与执行插件

## 目录

- 生成器
- 运行时对象
- 注册约束
- 执行器
- c_api 执行器
- ACLNN 适配
- 适配器判定在 S2 完成
- CPU golden

## 职责

生成器把已冻结 combo 物化为 ATK 用例。

执行器只负责将用例对象映射到已确认的待验收算子接口。

插件不得新增任务书未声明的约束。

不确定的参数语义先停止并补充契约证据。

可抄的样例：生成器 `assets/example/constraint.py`，CPU golden `assets/example/function_example.py`。

## 生成器

只有当通用生成规则不能表达参数关系、边界值或输出结构时才写生成器。

生成器读取 combo 的语义轴，填充实际 shape、dtype、attr 和错误标签。

不要把具体算子约束写进通用工具。

生成器必须返回固定数量的 case。

`CaseConfig` 只有 `id`，**没有 `coverage_tags` 字段**，赋值会直接 `ValueError: "CaseConfig" object has no field`。

覆盖标签留在物化组合表里，由 `check_coverage.py` 按 `extract` 规则回读，不进用例对象。

覆写字段前确认对象层级：`case.inputs[i]` 可能是 tensor、scalar、attr 或复合组。

复合组在运行期是 `list[InputCaseConfig]`，不是单个对象。

组长度来自 YAML 的 `tuple_numbers`，ATK 每条用例随机抽一个，抽完还洗牌。

所以组长度与 combo 的顺序没有对应关系，查 `atk_lookup.py group_length` 看全部事实。

per-combo 长度会变的复合组必须**整组重建**，逐个改元素只在长度恰好相等时才对。

整组重建的写法见 `assets/example/constraint.py` 的 `rebuild_group`。

list/tuple 参数使用对应的运行时容器。

不要把缺省参数表示为空的复合输入组。

`type="attr"`、`range_values=None` 表示无效输入，不表示缺省。

bool attr 只使用 ATK 契约允许的 `True`、`False` 或 `default` 形态。

可选 attr 使用显式缺省 token，不创建空的复合 group。

## 注册约束

文件内 `CaseGenerator` 子类数量必须等于装饰器数量。

mixin 必须位于基类列表最前面。

基类必须以 `CaseGenerator` 名称导入。

每个生成器使用唯一注册名。

公共 helper 不注册、不继承 `CaseGenerator`。

多个接口分面可共用 helper，但每个分面单独注册一个生成器。

## 运行时对象

参数契约至少记录：元素种类、容器、nullable、dtype、语义顺序和是否 omitted。

生成器能覆写的字段（`atk/configs/case_config.py`，`extra` 未放开，写未声明字段直接报错）：

| 对象 | 字段 |
| --- | --- |
| `CaseConfig` | `id`、`name`、`aclnn_name`、`version`、`expected_error_msg`、`api`、`api_type`、`aclnn_api_type`、`standard`、`outputs`、`inputs`、`method_inputs`、`tensor_input`、`compute_times`、`is_boundary`、`default_seed` |
| `InputCaseConfig` | `name`、`type`、`required`、`dtype`、`shape`、`range_values`、`backward`、`align_32B`、`outlier_values` |

`generate()` 里 `self.index` 在返回前已自增，所以取本条组合用 `self.index - 1`。

`super().generate()` 会跑 shape 泛化，ATK 源码注明它可能死循环。

`make_yaml.py` 产出的 YAML 固定写 `shape_distributions: [[0, 1.0]]` 关掉规模配额，这条路径才安全。

手改 YAML 打开配额就会挂住。

运行时契约表是 ATK 对象的真源，具体就是这两张字典：

| 表 | 位置 | 内容 |
| --- | --- | --- |
| `CPP_TO_PYTHON_TYPE` | `atk.tasks.backends.lib_interface.acl_wrapper` | C 类型名 → ATK 运行时对象 |
| `PYTYPE_TO_CTYPE` | `atk.tasks.backends.pyaclnn_backend` | YAML 的 dtype 令牌 → `ctypes` 标量类型 |

`align_signatures.py` 读的就是它们，报告里的 ctype 结论由此而来。

需要某个类型的构造方式时查这两张表，不要枚举常量、不要猜。

同类型参数的语义顺序必须从该表确认。

不要把 C/C++ 函数签名直接当成 ATK Python 对象签名。

输出参数由 ATK 按执行器契约追加，不要在输入列表中重复声明。

## c_api 执行器

```bash
<python> scripts/align_signatures.py ... --layout-order row_major --layout-source '<依据>'
```

可抄的双节点样例是 `assets/example/c_api_executor.py`。它只读取两类数据：

- `ATK_C_API_LIBRARY` 指向的绝对 `.so` 路径；
- `ATK_C_API_CALL_SEQUENCE` 指向的 `<op>_call_sequence.json`。
- mangled 形态还要设置 `ATK_C_API_EXPORTED_NAME`，值由 S3 绑定门禁给出。

执行器不得导入生成侧模块。调用序列表跨过技能拆分边界后，验收侧必须自行核对
版本、步骤、参数类别和布局；`layout.order` 仍为空就停止。

同一个 `@register("example_c_api")` 服务两个节点。两侧都由
`case_config.api_type` 选择注册类，依据是 `atk/tasks/backends/backend.py:60`；类内按
`self.device` 分流，`npu` 直调动态库，其余后端执行 torch 基线翻译。

NPU 分支不得含 torch 计算算子。性能窗口会汇总其中每个算子的 Task Duration，
所以这里只允许设备选择、当前 stream、`data_ptr()`、动态库调用和同步。

C 返回状态不等于调用序列表的 `status_ok` 时抛
`RuntimeError(f"c_api status {code}")`。错误用例的 `expected_error_msg` 按这一格式写。

## ACLNN 执行器

**不要自己补 workspace 和 executor**。

后端绑定函数时固定追加 `uint64_t* workspaceSize` 和 `aclOpExecutor** executor`，适配器再补一次就错了。

待验收算子的接口名来自 `aclnn_api_type`；基线由 ATK 的基线接口执行。

适配器只处理公开签名已确认的参数映射。

optional pointer 的构造方式必须来自运行时契约表。

优先使用 typed pointer alias 或 factory。

不得使用 `ctypes.c_void_p(0)` 伪造 typed optional pointer。

输出包使用 `type(output_package)()` 等 ATK 规定的工厂构造。

不得枚举常量、空指针或参数顺序。

## ABI 与语义校验

`align_signatures.py` 对齐基线 API 与 ACLNN C 声明的参数名、顺序、ctype 和出参位置。

### 签名只能从工程目录里读

**待验收算子工程目录，别处都不行。** 具体说是 `evidence/env.json` 的
`operator_project.path` 那棵树，头文件构建前就在源码树里，不用等 S3 构建安装。

**任何情况下都不要去 CANN 内置算子工程找签名。** 装机目录下常有同名的官方已
发布实现，它和待验收算子是两份独立代码，**同名不代表同签名**——median 就是这么
翻的车：装机版 4 参无 `dim`，待验收算子 7 参带 `dim`，S2 冻结了错的签名，直到
S3 构建安装完才发现，白跑一整轮构建。别队的 vendor 目录、上一轮的构建产物、
`find` 出来的同名头文件，同理都不能拿来读签名。

工程里找不到这个接口的声明时，停下来向用户确认工程路径或接口名，**不要换一个
目录去找**。

脚本强制核对，两条路都堵死：

- `--env evidence/env.json` 现在是必填，脚本据此拿 `operator_project.path` 做白名单
- `--header` 必须落在这棵树下；落在 CANN 装机根下另有一条更具体的报错
- `--signature`（手抄声明）必须同时给 `--signature-source`，指向这棵树里真实存在的文件
- `env.json` 里没有 `operator_project` 就不放行：重跑 `probe_env.py --op-repo <工程目录>`

```bash
<python> scripts/align_signatures.py \
  --baseline <yaml-name> \
  --header <工程目录>/op_host/op_api --aclnn-name <yaml-aclnn-name> \
  --env evidence/env.json \
  -o evidence/signature_alignment.json
```

读的是哪个文件会记进 `signature_alignment.json` 的 `signature_source`，报告引用它说明签名从哪来。

任务书点名的接口数量不是判据。

待验收算子可能用一个接口名服务任务书里点名的多种用法（比如靠某个可空
输出区分语义），这是它的实现自由。

只要签名对齐拿到的是它自己真实的签名，就按真实签名把各分面的用例配到
对应入口，不构成阻断理由。

真正要处理的只有一件事：这个接口能不能被正确调用。

签名撞了已发布的官方同名接口但不一致时，记入证据作为风险项，不必然是
阻断条件。

它产出 `signature_alignment.json`，先读这四个键再决定要不要写适配器：

| 键 | 怎么用 |
| --- | --- |
| `aclnn_adapter.required` | `false` 就不要写执行器，默认 `aclnn_function` 已经够用 |
| `aclnn_adapter.reasons` | `true` 时这里说明差在哪，照着补那一处，不要顺手改别的 |
| `aclnn_adapter.determinable` | `false` 表示基线形参名取不到，位置配对不可信，停下来补契约证据 |
| `semantic_review` | 需要人判断的疑点清单，非空时逐条看过再动手 |

三个覆写点，按差异类型选一个：

| 差异 | 覆写 |
| --- | --- |
| 入参装配（缺可空出参、顺序不同） | `init_by_input_data` |
| 出参回收（输出包结构或顺序不符） | `after_call` |
| 签名类型自检 | `get_cpp_func_signature_type` |

默认拼装是 `input_args = [入参展开...] + output_packages`，出参永远在尾部。

一个 YAML 输入可能展开成多个 arg，所以定位插入点要按 `len(output_packages)` 切分，不要用魔数比长度。

可抄的样例：`assets/example/aclnn_executor.py`。

C ABI 证据不能替代 ATK 运行时契约表。

生成前运行能力门禁和静态适配检查。

适配后运行 `--cpp_func_signature_type_path` 的最小真机语义冒烟。

最小真机语义冒烟必须验证参数装载、workspace、executor 和输出结构。

默认绑定不匹配时，只有公开证据支持的 `aclnn_api_type` 修正才可使用。

最多修正一次适配器，把改动、依据和失败类别记进证据目录。

第二次失败或语义证据不足时停止。

## 适配器判定在 S2 完成

`semantic_review` 给的是**条件性**预警：条件成不成立，签名里看不出来。

条件成不成立不用猜，用例集就在手上，跑这道门禁逐条核：

```bash
<python> scripts/check_adapter_binding.py -j <case-json> \
  -a evidence/signature_alignment.json -o evidence/adapter_binding.json
```

退出码 2 表示该写的适配器没写，或对齐报告 `determinable=false` 无法判定。

预警类型与判定方式：

| 预警类型 | 怎么在用例集里验证触发 | 触发了怎么办 |
| --- | --- | --- |
| 可空指针（`aclXxx*` 且非 `aclTensor`） | 该输入名有用例的 `range_values` 是 `null` 或 `default` | 写 typed null 执行器，把 `aclnn_api_type` 改成它的注册名 |
| aclnn 独有参数 | `baseline_adapter.required` 为 `true` | 写 `function_<op>.py` 把它 pop 掉，`api_type` 改成它的注册名 |
| 基线独有参数 | `aclnn_adapter.required` 为 `true` | 在 `init_by_input_data` 里丢掉它 |
| 多个 aten 重载 | 脚本判不了，报告里标 `requires_manual_review` | 拆接口分面 |
| 变长基线签名 | 脚本判不了，报告里标 `requires_manual_review` | 人工核对位置配对 |

执行器绑定以**用例 JSON** 为准，不是 YAML。

ATK 从 `case_config.api_type` / `aclnn_api_type` 取执行器，用例里没写就是内置默认。

所以改接线必然要重跑 `atk case`，这就是判定必须在冻结之前做完的原因。

### S3 才暴露时的唯一出口

运行期类型校验这类问题确实可能只在 S3 冒烟才暴露。

那时用例已冻结，改接线要重跑 `atk case`，与冻结纪律冲突。

唯一的受控通道是：

```bash
<python> scripts/rewire_adapter.py -f <yaml> -j <case-json> \
  --atk-cli "$ATK_CLI" --set aclnn_api_type=<注册名> \
  -p <生成器插件> --frozen evidence/frozen_inputs.json -o evidence/rewire.json
```

它 patch 接线字段、重跑生成、逐条用例逐字段比对，只有接线变了才放行。

退出码 2 表示用例语义也变了，那不属于接线改写，回 S2 重做整轮。

复合组长度是 ATK 随机抽的，这类算子重跑必然得到不同用例，脚本会如实拒绝。

## CPU golden

CPU golden 插件只负责生成基线输出。

它是条件产物：`signature_alignment.json` 的 `baseline_adapter.required` 为 false 时不要写。

基线返回多输出（tuple/namedtuple）不构成写它的理由：ATK 在
`atk/tasks/executors/opp_executor.py` 的 `save_output_data` 对 list/tuple
输出递归拆存为独立文件，`atk/tasks/post_process/base_compare.py` 逐文件
比对，各输出 dtype 不同不受影响——多输出本身不是判据，
`baseline_adapter.required` 才是。

ATK 对 `name` 能解析到的基线函数名自带内置执行器，多写一个空壳插件只会多一处漂移源。

需不需要它由 S2 的签名对齐当场判定，不靠记忆，也不靠「产物清单里有这一行」。

**本 skill 走的 `atk node … task -c` 这条路不会自动加载它。**

「`function_*.py` 与 case 文件同目录就自动加载」只在 `atk aclnn` /
`atk pytorch` 这两个别名入口成立（`atk/bin/op_alias.py:164`），
本 skill 一条命令都不走那两个入口。

`task -p/--plugin_path` 接受一个 py 文件**或一个目录**，而且只能给一次
（给两次是后一个覆盖前一个）。所以生成器和 CPU golden 要放进同一个目录，
`-p` 指那个目录：

```text
<工作目录>/plugins/
├── <op>_constraint.py     生成器
└── function_<op>.py       CPU golden
```

组合表在工作目录根上，插件里用 `Path(__file__).resolve().parents[1] / "..."` 读。

`freeze_inputs.py` 的 `-p` 也照这个写法给目录。

漏了这一步不会说「插件没加载」：冻结那次跑测的 CPU 节点会拿默认的
`function` 执行器去 `eval(name)`，然后整批基线执行失败。真机实测
（bernoulli，2026-08-17）：155 条全挂，报错指向的是 YAML 输入名，
而真因是插件根本没进来。

**加载到不等于用上。**

ATK 按 YAML 的 `api_type` 取执行器，默认取内置的 `function`。

所以注册名要同时写进声明文件的 `yaml` 块：`@register("x")` 对 `"api_type": "x"`。

两边对不上时 ATK 报「没有对应标杆 API 文件」，插件静静地不生效。

三个注册键各管一侧，不要混：`generate` 管生成器，`api_type` 管基线侧执行器，`aclnn_api_type` 管 pyaclnn 侧。

执行期复用冻结输入；不要在复跑阶段重新生成参数。

CPU golden 不得改变待验收算子执行后端、比较器或通过率分母。

## 自查

- 注册名唯一。
- 生成器与装饰器一一对应。
- 复合参数容器正确。
- optional pointer 使用 typed factory。
- workspace 和 executor 顺序正确。
- 输出参数未重复加入输入。
- 运行时契约表、签名对齐表和最小真机语义冒烟证据齐全。
