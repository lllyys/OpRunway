# CLAUDE.md — 用例生成 skill

改这个 skill 时读。运行时规则在 `SKILL.md`，行文规范与开发流程沿用仓根 `CLAUDE.md`。

唯一目标：**让零上下文 agent 拿一份任意结构的社区任务书，独立产出可跑测的用例包。**

| 要什么 | 去哪 |
| --- | --- |
| 某个算子的用例分布调不出来、基线冻不出来 | [排障事实](../../docs/development/case-gen-troubleshooting.md) |
| 某个决定是怎么演进来的 | [架构演进记录](../../docs/development/architecture-log.md) |

## 下游是跑测侧

产物 `<输出目录>/<op>/` 被 `repo-task-atk-accept` 直接吃掉，输出目录由用户指定，一个
算子一个子目录。使用态上两侧不强制串联，**开发态上有十四项硬契约**：`cases.json`
文件名、`golden/` 布局、`facts.json` 的 `performance.kind` 与
`non_contiguous.required`、`golden/manifest.json` 的 `baseline_dir`、
`function_<op>.py` 与 `env.gen.json` 的命名、`kind=cross_dtype` 时性能子集按 shape
成对、`inputs/<id>/input.bin` 的布局与目录名、`gen/` 这一层、`facts.json` 的
`backend`、`kit/` 与 `function_<op>.py` 的依赖。

**这里只是索引，逐项的破坏表现在[契约文档](../../docs/development/case-package-contract.md)里。**
别照着这些名字自己推后果——其中六项被破坏时跑测侧不报错。改完两侧都要真机复跑。

**不为让跑测通过而改窄用例。** 跑测侧报某组用例挂了，先判是不是算子真的错了。把
YAML 取值范围收窄、把失败用例从 `cases.json` 里剔掉，都能让跑测变绿——那正是本 skill
存在意义的反面。真要改窄，前提是**任务书或工程文档明确写了该取值非法**，且在
`facts.json` 的对应项里留下 `source`。凭跑测结果反推约束一律不行。

## 用例从哪来：两条路

**分叉在 S2，合流在 S4。** S1 与 S4 两条路共用，中间二选一：

| 路 | 何时 | 做什么 | 载体 |
| --- | --- | --- | --- |
| 自产 | 自带件缺执行器插件或缺用例清单 | S2 写 YAML 与插件、S3 跑 `atk case` | `gen_cases.py` |
| 采纳 | 两样都有 | S2′ 归一，S2/S3 整段跳过 | `adopt_kit.py` |

**采纳路上本 skill 的增量只剩契约那几项**，用例设计与语义一个字都不动。
这条边界要守住：改自带件的语义等于替任务方做主，而 `adopted_files` 记的指纹
与任务仓那份不同之后，验收结论追不回来源。

### backend 是分流的唯一判据

判的是算子暴露的**接口形态**，不是它属于哪个仓、也不是它算什么。
判据、字段约束与自带件不全时怎么补，全在
[kit-adoption.md](references/kit-adoption.md)。

| 约束 | 破了会怎样 |
| --- | --- |
| **判一次，写进 `facts.json` 的 `backend`**，跑测侧只读不重判 | 两处各判一次必然漂，而漂了不报错 |
| `backend=npu` 时 `accuracy.kind` 与 `performance.kind` 都不能 `builtin` | 两者都靠 CANN 装机目录里的内置同名 aclnn 接口，npu 剖面没有 aclnn 接口。**`accuracy.kind` 可以是 `plugin`**——稀疏这类要先拼输入结构才调得动公开接口的算子只能走它 |
| `non_contiguous.required` 能不能填 `true`，判据是**用例形态**不是 `backend` | `--slice_input` 在 ATK 后端基类里对已载入的输入张量做（`atk/tasks/backends/backend.py:160`），两个剖面都生效。只有入参全是 attr、张量由执行器现造时才切不到。曾按 `backend` 一刀切，起因是自带件的 npu 用例恰好全是 attr 编码 |
| 采纳时自带件整棵树搬进 `kit/`，**不拆平** | 原件里有 `parents[1] / "common"` 这类相对引用，拆平就断，而断了之后 ATK 静默走内置路径 |
| 四个被采纳文件的 sha256 进 `facts.json` | golden 是自带件的手写实现算的，那份换一个字节标杆就换一批，而自带件在任务仓里、本仓管不到它的版本 |

## 三条红线

| 红线 | 具体是什么 |
| --- | --- |
| **不拿算子实现当判据** | 被测算子的 kernel、tiling、内部断言不能用来生成用例——**用它自己的断言反推取值范围，写错的断言永远测不出来**。可读：任务书、工程 `docs/aclnn*.md` 与 `README.md`、`op_api/*.h` 的函数声明；不可读：`op_kernel/`、`op_host/*_tiling.cpp`、内部断言与报错字符串常量 |
| **ATK 是黑盒** | 不改 `third_party/ATK/`，它是指向上游的 submodule，改一行就脏掉 gitlink。行为不符预期就记为已知问题并写清楚，不打补丁 |
| **判据从数据推导** | `check_facts.py` 校验的是 `facts.json` 的内容而不是 agent 的自述；用例数下限、golden 覆盖率都是硬判据。**不允许 agent 声称「我检查过了」就放行** |

红线 1 与 ATK 自带 `atk-quality-guard` 的 NEVER #4 正好相反——那个 skill 给已验收算子
做质量加固，目标是不产生假失败；本 skill 验收社区提交的算子，目标是能发现它错。
所以 `op-engineering.md` 那套「扫 C++ assert」的做法不采纳。

## 不要改回去

| 约束 | 改回去会怎样 |
| --- | --- |
| **任务书由 agent 解析，脚本只校验结果** | 社区任务书没有固定结构：三份真实任务书里 roll 与 indexfill 有参数表但列名不同，median 连参数表都没有。上一版硬要 `§2.3` 的代码块和 `§2.4` 的固定表头，三份一份都过不了 |
| **用例包不做 SHA256 封印** | 封印挡的是「有人偷偷改了用例包」，而真实场景里生成与跑测由同一个人在同一台机器上连着做。代价是每改一行 YAML 都要重走封印流程。可靠性由 S1–S4 的出口判据保证 |
| **`kind=builtin` 冻 golden 时那个 cpu 节点去不掉** | aclnn 的 `out` 是第一段接口的**入参**，调用方要先申请好，而 pyaclnn 推不出它多大（`pyaclnn_backend.py:278` 直接抛「标杆输出为空」）。ATK 拿输出 shape/dtype 只有两个来源：同一轮里标杆节点的返回值，或 `accuracy_load` 读冻好的 `output_info.json`——第一份 golden 只能走前者。那个 cpu 节点**只给形状**，值无意义（随机数算子上更是如此） |
| **基线的 `output_info.json` 用 cpu 那份覆盖，不要改回「摊平一层」** | cpu 那份不是第二来源，**它就是决定了 `out` 尺寸的那一份**。摊平是在猜规则，且对 tensorlist 算子摊不干净。现在覆盖前按叶子逐项比对，不一致就退 2 不覆盖——那说明内置实现的实际输出与申请时的尺寸不符，是该被看见的问题 |
| **`builtin` 只支持同名同签名**，不要试图加适配层 | pyaclnn 按 `aclnn_name` 拼符号、按位置传参，调用约定来自 per-case 的 `aclnn_api_type`（`case_config.py:91`）。同一次跑测里两个 pyaclnn 节点共用一份 case config，**给不出两套调用约定**，也没有 per-node 的 `aclnn_name` 覆盖。所以它只适用于「不改语义只改实现」的优化 / 重构任务 |
| **找不到内置符号时不要替用户回落到 torch 基线** | 回落会让验收报告悄悄换掉判据。`freeze_golden.py` 退 2，打印找的符号名和搜过的 glob，去留交给用户 |
| **golden 要冻结，不要现场算标杆** | 现场算也能跑，但同一套用例反复验收多个 PR 时基准会漂。冻结后 `accuracy_load` 读盘比对，结论跨轮次可比。这是 ATK 原生路径，不是我们造的轮子 |
| **四个脚本都要在 import 段之后立刻 `os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")`**，`check_facts.py` 那层宽 `except Exception` 也不要收窄 | 真机 conda 环境里装了 `torch_npu`，没 source CANN 时 `import torch` 抛 **`RuntimeError`** 而不是 `ImportError`：`probe_env.py` 退 2 让 **S0 直接阻塞**，`gen_cases.py` / `freeze_golden.py` 的子进程起不来，`check_facts.py` 只 catch `ImportError` 时直接崩。这一条打穿了「生成侧不需要 NPU 与 CANN」那句话 |
| **`skeleton.yaml` 的任何数值改动，必须在真机上用它跑一遍 `--dry-run`** | 它是可执行文件不是文档代码块，就是为了这条能落地。上一版骨架写在 `yaml-authoring.md` 的代码块里，改它零成本、跑它没门路，三代之内烂成了错的（`max_length` 停在 1048576、`dim_numbers` 停在 4、`dim_values` 停在 512） |
| **可调数值只许有一份** | `case-strategy.md` 写规则不写值，`skeleton.yaml` 写值不写理由。之前同一组数字有三份副本，三份全不一致 |
| `references/atk-surface.md` **只记「有什么」不记「怎么用」** | 它是从钉死的 ATK 版本查出来的清单。升版本时重跑那份文件里的两条命令替换输出；往里加语义会漂，名字不会 |

## 契约性事实

真机上验出来的 ATK 行为，**不知道就会把 YAML 或脚本写错**。环境是 Atlas A3 + ATK
26.8.8。某个算子的分布与耗时在[排障事实](../../docs/development/case-gen-troubleshooting.md)。

### 用例与 YAML

| 事实 | 出处 |
| --- | --- |
| `aclnn_api_type` 默认值是 `aclnn_function`，`pyaclnn` 不是注册名 | `atk/configs/case_config.py:91` |
| `attrs` 列表**不能为空**：ATK 排序时取 `input_case[0]`，空列表直接 `IndexError`，整轮 `atk case` 崩掉。因此「某参数必须是空数组」的场景造不出来 | 实测 Roll 秩 0（`case_generator/utils/reports.py` 的 `get_numel_by_input_type`） |
| `scalar` 参数的 dtype 与张量参数**各自独立抽**。文档写「可转化为 self 的数据类型」时必须在约束器里 `value.dtype = self_t.dtype` | 实测 IndexFillTensor：uint8 的 value 抽到负值配 int8 的 self，3 条 golden 冻不出来 |
| `type: scalar` 的取值不落进 cases.json，存的是区间、运行时再采 | 实测：写 `[[0, 1]]` 只生成 0 与 1，写 `[[0.5, 0.5]]` 才拿得到 0.5 |
| `type: attr` 的整数取值多数会落成标量，少数保留成区间二元组 | 实测，140 条里 8 条是 `[42, 42]` 这种形态 |
| `type: tensors` **逐元素独立抽 dtype**，列表内部会混 dtype；预算减半重抽还会把列表长度系统性挤向 1 | `parameter_tensors.py:73-81` + 实测 |
| 约束器里对列表参数做 `x1[:] = x1[:1]` 会把「列表长度」这根轴整根压塌，而 dry-run、条数、golden 三关都不响 | 实测 |
| `max_length` 是**字节预算**不是元素数：`get_max_number_ele` 先按 dtype 宽度除一次 | `parameter_tensor.py:41-51` + 真机实测 |
| 那张宽度表漏了 **bf16 / uint16 / uint32 / uint64 / complex128**，五个全掉进 `else` 被当 1 字节，分别拿到 2× / 2× / 4× / 8× / 16× 的字节预算 | 同上；A3 上逐个调 `get_max_number_ele` 实测，另 200 条抽样里 complex128 抽到 33 MB = 7.9 × `max_length` |
| `aclIntArray*` 参数在 cases.json 里落成 `[{"type": "attrs", ...}]`——dict 列表但不是张量 | 实测，`case_shape.classify` 已补 `ATTR_TYPES` 分支 |
| `atk case` 的 `-df/--dtype_filter` 是**滤除**列表，`-dt` 对滤剩的每个 dtype 各生效——两者配合才能给某个 dtype 加权，YAML 里没有这个入口 | 实测：`-df bf16,fp16,int16,int32 -dt 8` 在 7 dtype 的 YAML 上出 24 条 |
| `atk case` 的产物写在 `result/<yaml stem>/json/all_<stem>.json` | `case_generator/utils/reports.py:369` |
| 输入数据由 `default_seed` 决定，同一份 cases.json 两次跑输入相同 | `atk/tasks/dataset/base_dataset.py:85` |
| **`case_config.id` 在 `after_case_config` 里恒为 0**（id 在钩子跑完之后才赋），用它取模会让全部用例落进同一分支 | 实测，钩子里 print 一次即可复核 |
| `after_input_config` 的签名是 `(self, index, input_case)` | `case_generator/generator/base_generator.py:64` |
| 约束器钩子漏写 `return case_config` 时，报错在出报表那步：`AttributeError: 'NoneType' object has no attribute 'method_inputs'` | 实测，`case_generator/utils/reports.py:238` |
| 非连续张量是**跑测期**的事：`--slice_input non_contiguous` 开 2 倍存储取步长 2 的视图再 `copy_`，**逻辑值不变，golden 不用重冻** | `atk/configs/dataset_config.py:296-313`，CPU 侧 5 条实测 exit 0 |

### 执行器与 golden

| 事实 | 出处 |
| --- | --- |
| YAML 的 `inputs` 不含 `out`，ATK 从 CPU 标杆返回值推输出 | `atk/tasks/api_execute/aclnn_base_api.py:85` |
| aclnn 侧有完整自动类型转换，cpu 侧一行都没有——**要写的执行器几乎全在基线侧** | `pyaclnn_backend.py:287` 有 `convert_input_data`，`cpu_backend.py` 里没有 |
| `aclnn_function` 是空壳，三个方法全 `super()` 直通基类 | `atk/tasks/api_execute/function_api.py:61` |
| 出参是 `aclTensorList*` 时 CPU 执行器必写，而**基线 eval 探针查不出来**——调用本身成功，错的是返回值的嵌套层数 | 实测 ForeachMulList；判据已补进 `check_facts.py` 的 `_probe_baseline` |
| 单 cpu 节点跑 accuracy + `--save_data output` 能产出 golden | 实测，180/180 |
| golden 路径是 `<根>/<backend>/<用例文件基名>/<id>/` | `atk/common/utils.py:259` + `atk/tasks/result_process.py:67` |
| golden 的 `output_info.json` 是 `[{"dtype": ..., "shape": [], "stride": []}, ...]`，每输出一项；**pyaclnn 节点存的比 cpu 节点多套一层 list** | 实测 + `atk/common/utils.py:246` |
| 嵌套层数由**返回值的容器类型**决定，与后端无关：裸 tensor 走 `else` 存 `[{...}]`；tuple 不递归；**list 递归后整个 append**，每层多一层。tensorlist 输出会多两层 | `atk/common/utils.py:241`、`aclnn_base_api.py:113` |
| 张量列表算子的 `output_info.json` 就是 `[[{t0},…,{tn}]]`：外层「一个输出」内层「列表里 n 个张量」，不是多套了一层 | 实测 ForeachMulList 用例 0 |
| **cpu 节点不给 `-n` 时继承主节点的名字**，所以 `kind=builtin` 冻出来的形状目录叫 `cpu_builtin/` 而不是 `cpu_0/`；`kind=torch` 时主节点就是 cpu 自己且名字为空，才落成 `cpu_0/` | `atk/configs/nodes_config.py:141`（`node.name or main_node.name`）+ `:147` 的兜底编号 |
| `manifest.json` 的 `baseline_dir` 记的是 `pyaclnn_builtin/`（`freeze_golden.py:285` 写死 `-n builtin`），**不能取 `pyaclnn_0`** | 理由见跑测侧 `CLAUDE.md`「标杆节点的 backend 不能写死成 cpu」 |
| 同一份 cases.json 两轮独立冻结，golden **逐位相同**——这是「输入按 `case_id` 确定性重生成」的直接证据，也是拿内置当基线的前提 | 实测 Bernoulli 140/140、UpsampleNearestExact1d uint8 三条逐位验过 |
| **`--save_data input` 能把输入张量全量落盘**，与 `--save_data output` 并列给；ATK 存在 `atk_output/<任务>/input/<save_name>/<id>/input.bin`，比 `--input_data` 要的布局多一层 save_name | 实测 2026-08-31 |
| `SAVE_DATA_LIST = ["profile", "input", "input_final", "output", "db"]`，`input` 是切片前的，`input_final` 是 `--slice_input` 生效后的 | `atk/configs/base_config.py:27`、`atk/tasks/executors/opp_executor.py:84` |
| 装了 torch_npu 的机器上没 source CANN 时 `import torch` 抛 **RuntimeError** 不是 ImportError | 实测 |

## 目录结构

```
skill/repo-task-case-gen/
├── SKILL.md              入口：S0–S4
├── references/           10 份按需加载的知识，每个阶段的展开各归一份
├── scripts/              8 个量具
└── assets/               skeleton.yaml（YAML 数值的唯一真源）+ 6 份钩子 API 示例
```

脚本之间不互相 import，各自独立可跑。没有共用模块，也没有双份同步纪律——两侧的
`probe_env.py` 是两份不同的脚本，跑测侧要查 NPU 与 CANN，生成侧不查。

## 加东西之前

- 新增 reference 或脚本前先答：**没有它，零上下文 agent 会在哪一步卡住？** 答不上来
  就不加。上一版 35 份 reference、69 个脚本的教训是，为「可能有用」加的东西会挤占
  agent 读真正有用那几份的预算
- 真机跑出来的事实按上面那把尺子分流：影响 YAML 或脚本怎么写的进「契约性事实」，
  某算子的分布与耗时进[排障事实](../../docs/development/case-gen-troubleshooting.md)
- 变更记录写进[架构演进记录](../../docs/development/architecture-log.md)，不在本文件堆栈
- 体积欠账 2026-09-09 结清：S1–S4 的展开下沉到各自 reference，规模档从
  `case-strategy.md` 独立成 `size-bands.md`。**S2 的四份 reference 拆成四行**——
  一行一个去处，覆盖轴、规模档、精度阈值、插件各按需载入，不再一次全载。
  复测：`python3 .claude/hooks/skill_budget_lint.py`
