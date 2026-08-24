# 任务受理与约束确认

本文件覆盖 S1 的全部内容。

S1 有两道性质不同的门：任务书解读靠人读，环境探测靠脚本。

`env.json` 生成不等于 S1 通过，待确认项清零才算。

## 目录

- 输入与来源
- 约束表
- 运行信息
- 接口和基线
- 阻塞条件

## 输入与来源

受理任务书、待验收算子工程路径、跑测环境和用户已知的接口、后端、精度基线、性能基线及 device。

PR 先归约为本地工程。

不要直接修改待验收算子工程。

约束只来自任务书、已确认基线接口和生态精度标准。

不要从实现、kernel 分支或历史测试补充覆盖和输入域。

未写清的内容标记为“待确认”，确认后才进入正式约束。

## 验收约束表

| 字段 | 记录内容 |
| --- | --- |
| 算子 | 任务书里的算子名、要提交验收的接口名（如 `aclnnMedian`） |
| 输入 | 名称、类型、dtype、rank、shape、attr |
| 非连续 Tensor | 任务书参数表「非连续Tensor」列；无此列记为未声明 |
| 输出 | 数量、dtype、语义 |
| 内存布局 | `order` 与依据（谁要求的、写在哪）；不得按接口家族惯例猜 |
| c_api 指针参数归属 | `host_scalar` 与原地输出的逐项决定，以及各自的公开文档依据 |
| 功能范围 | 必须支持的场景 |
| 语义形态 | 同一接口的另一套语义（某个出参传空时换算法之类）。逐条写「不构造」或「用户已要求构造 + 原话 + 日期」；默认是不构造 |
| 错误语义 | 必须拒绝的输入和报错要求 |
| 精度 | 标准名称和依据（哪份文档、哪一节） |
| 性能 | 绝对门槛或对比规则 |
| 环境 | 任务书写的适配硬件原文、`env.json` 的 `devices.selected_name` 和 `build_soc`，三项并列 |
| 提交 | pytorch、aclnn、c_api、kernel、triton 或 atb |
| 构建 | 公开命令 |
| 推断项 | 内容、依据、确认状态 |

任务书写的适配硬件是**产品市场名**（「Atlas A2 训练系列产品」），`env.json` 的
`build_soc` 是**构建族名**（`ascend910b`、`ascend910_93`）。

两者之间没有一张能机械核对的对照表：CANN 装机目录里没有，工程里也没有。

所以不要为这件事去 grep 装机目录、翻编译脚本的常量表或按型号数字推——真机上
为它花过五六条命令，最后仍然只能写「确认不了」。

**它不是待确认项。** 约束表的「环境」一行把三项并列写下就够了：

| 记什么 | 从哪来 |
| --- | --- |
| 任务书适配硬件原文 | 任务书，照抄 |
| `devices.selected_name` | `env.json` |
| `build_soc` | `env.json` |

判据在 S3，不在这里：拿真机的 `build_soc` 去构建，包里有没有这一族的 kernel
由 `check_soc_binding.py` 客观裁决。构建不出来或包里没有，那时才是结构性阻塞，
报告写「工程未声明真机 SoC」，不换 SoC 重建。

用产品名推构建族推错了，代价是 S2 用例设计、S3 整轮构建安装全部白做；照上表
写下来再让 S3 裁决，代价是零。

参数表写“支持非连续”时，精度阶段补一轮非连续执行。

验收范围由任务书的精度与性能目标划定。任务书的功能描述和工程设计文档里的
语义形态是背景说明，默认不构造数据、不进必测集——多测一种形态要多一整套
YAML、必测集、用例 JSON、冻结、部署冒烟和结论，而精度性能目标一条也没多覆盖。

要测就问用户，答复原话记进约束表的「语义形态」一行。不问就当不测，不要
自己按“任务书提到了”把它加进来。

任务书指定其他精度标准时记录原文；实际判定仍按生态标准。

## 运行信息

记录工程和任务书绝对路径、实际 Python、ATK 导入状态、ATK CLI、torch_npu、CANN、device、构建入口和环境初始化命令。

环境字段由 `probe_env.py` 生成。

用户指定 device 时直接使用，不做张量预检。

裸 `torch_npu` 张量探测在没先 `set_device` 时会挂住，实测挂满 120 秒被移到后台，再花几次调用去收尸。

device 可用与否由 S3 的正式 ATK 冒烟裁决，那是唯一有判别力的验证。

用户未指定时选择一张健康候选卡，再用正式 ATK 冒烟验证。

## 接口和基线

开跑前确认：接口模式、待验收算子的接口名、精度基线和性能基线。

待验收算子与基线是两个东西。

任务书写“对标 `aclnnXxx`，等价于 `torch.xxx`”时，`aclnnXxx` 是待验收算子的接口名，`torch.xxx` 是基线。

不要把待验收算子的接口名当基线。

基线是三件独立的事，用户的约束落在哪一维就声明哪一维：

| 维度 | 参数 | 取值 | 默认 |
| --- | --- | --- | --- |
| 基线函数名 | `--baseline` | 接口名 | 必填 |
| 基线是什么 | `--baseline-kind` | `torch` / `cann_builtin` | `torch` |
| 判定那轮谁当基线节点 | `--baseline-node-backend` | `cpu` / `npu` | `cpu`；`cann_builtin` 只能是 `cpu` |
| 选型依据 | `--baseline-source` | 文本 | `cann_builtin` 时必填 |

`--baseline-node-backend` 问的不是「基线跑在哪个设备」。

`cann_builtin` 只能填 `cpu`，而内置实现本身跑在 npu 上——两轮的待测实现都在 npu，
判定那轮的基线节点只是从磁盘读真值，两轮拓扑见 builtin-baseline.md#谁跑在哪。

`--baseline-kind torch` 时基线根名必须属于 `torch`、`torch_npu`、`math` 或 `dist`。

`c_api` 模式的待验收节点使用 `npu` 后端，基线规则不变：函数根名仍来自上述
torch 白名单，`baseline_kind` 固定为 `torch`。

选择 `c_api` 时先按 `c-api-mode.md` 判断适用范围，再生成 `interface.json`。

基线跑 `npu` 与跑 `cpu` 同样合法，ATK 的基线节点只是复制主节点再改后端字段。

基线默认不得指向 CANN 内置实现。

只有任务书或用户明确要求与内置实现对比时才用 `--baseline-kind cann_builtin`，并把依据（谁要求的、写在哪）写进 `--baseline-source`。

拿内置实现当标杆，等于用任务书正要修的那份代码做真值，证据强度弱于框架基线，报告要写明这一点。

选了 `cann_builtin` 就要在 S2 之前读一遍 builtin-baseline.md：这条路的跑测形态、
目录规则和必做的反证实验都在那里，跟常规验收不是一回事。它还要求先回答一个问题——
待验收算子的接口声明里种子是不是显式入参；答不了这条路不成立。

执行后端由接口模式**唯一推导**，不是选择题：

| 模式 | 执行后端 | 定位字段 | 状态 |
| --- | --- | --- | --- |
| pytorch | npu | name | 可执行 |
| aclnn | pyaclnn | aclnn_name | 可执行 |
| c_api | npu | name | 可执行 |
| kernel | kernel | kernel_name | 可执行 |
| triton | triton | triton_name | 未开放 |
| atb | atb | 无 | 未开放 |

`atk node -b aclnn` 走的是 `AclnnBackend`，要求每个算子单独写 C++ 绑定并编译 `aclnnTest`，不在本 skill 范围，不要用它。

接口模式与基线确认后立即派生并落盘，后面所有阶段只读这份文件，不再重新声明：

```bash
<python> scripts/derive_interface.py --mode <aclnn|pytorch|c_api|kernel> \
  --candidate <待验收算子的接口名> --baseline <torch.xxx> \
  --mode-source '<依据：任务书哪一节这么写的>' --task-doc <任务书路径> \
  [--baseline-node-backend <cpu|npu>] \
  [--baseline-kind cann_builtin --baseline-source '<依据：谁要求的、写在哪>'] \
  [--random-strategy <四个取值之一> --random-strategy-source '<依据：谁确认的、何时>'] \
  [--seed-parameters seed,offset] \
  -o evidence/interface.json
```

派生结果里的 `baseline_backend` 就是后面跑测命令里基线节点的 `-b` 取值，不要另填。

对比性能还要确认标杆后端和 device；绝对值模式的门槛记入本文件的约束表，报告直接引用，不进 `interface.json`。

容差标准只从 `experimental_standard.md` 取。
ATK 代码里的阈值常量与真源不符，读到也不采信。

## 随机算子的精度对比策略

任务书出现随机数信号词（`references/random-operator-signals.json` 的
`signal_keywords`，如"随机采样""bernoulli""dropout""seed"）时，
`derive_interface.py` 会自动扫描 `--task-doc` 命中，此时不给
`--random-strategy`/`--random-strategy-source` 直接退出码 2。

命中之后不要自己写探针脚本去实证"CPU baseline 与 NPU candidate 同 seed
是否逐元素一致"——不同硬件/后端的随机数生成算法通常不同，同 seed 不保证
跨后端一致，这是行业通识（写在 `random-operator-signals.json` 的
`known_fact` 里），不需要每个随机算子的验收都现场重新证明一遍。

命中之后直接问用户，问题模板见 `random-operator-signals.json` 的 `ask_template`。

判据不是自由作文，从这四个取值里选一个（`random-operator-signals.json`
的 `strategies`）：

| 取值 | 前提 |
| --- | --- |
| `equal_vs_builtin_pinned_seed` | 种子是显式入参且在用例数据里钉死；跑法见 builtin-baseline.md |
| `deterministic_boundary_only` | 任务书认「只测概率取 0 或 1」这样的覆盖够用 |
| `distribution_test` | 判定公式与阈值已经写明 |
| `self_consistency` | 种子是显式入参 |

填不进这张表的直接被拒，报错会把菜单列出来。

「合理的统计/固定种子策略」这类任务书原句读起来像判据，但没有任何一步能照它执行。

选 `equal_vs_builtin_pinned_seed` 或 `self_consistency` 时，还要用
`--seed-parameters` 列出接口声明里的种子参数名，逗号分隔。

那两档要两次跑测拿到同一条随机数流，接口里没有种子入参就做不到，脚本会拦。

参数名只能读待验收算子工程目录里的头文件得到（红线 1 允许），不看 kernel 与 host 实现。

拿到的判据连同依据（谁确认的、何时）传给 `--random-strategy` /
`--random-strategy-source`，种子参数名传给 `--seed-parameters`。

三者都写进 `interface.json`：判据进 `random_operator`，种子名进 `seed_parameters`。

S2 的用例设计、`validate_cases.py` 的 C7 与 S4 的精度判据直接读它们，不再重新问一遍。

## 阻塞条件

缺少任务书、工程、接口模式、开放模式、精度基线或必要性能基线时停止；执行后端由接口模式推导，不单独构成缺失项。

停止报告写“阻塞·未验收 @S1”，列出缺失项和解除阻塞所需信息。

待确认项未清零时不得进入 S2。
