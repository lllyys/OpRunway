---
name: repo-task-atk-test
description: 当需要用 ATK 验收社区算子工程、核对任务书的精度与性能要求、生成验收证据或判断提交能否放行时使用。
---

# ATK 社区算子验收

## 工作边界

只验收任务书要求的公开契约。

**验收范围由任务书的精度与性能目标划定。**

任务书的功能描述、工程文档里的语义形态（比如同一个接口在某个出参传空时走
另一套语义），是背景说明，不自动变成必测数据。默认只按工程声明的那一份接口
签名构造用例：一份签名，一份用例集。

要为某个语义形态单独构造数据，必须用户明确说了要，并把这句话记进
`evidence/constraints.md`。

没有这句话就不构造，也不写进必测集。

多测一种形态要多一整套 YAML、必测集、用例 JSON、冻结、部署冒烟和结论，
而任务书的精度性能目标一条也没多覆盖。

待验收算子工程本身可以读，禁止的是**拿实现当验收依据**：不要用 kernel
计算逻辑、tiling 分支、内部断言去补充验收知识，也不要用它设计取值、构造
预期报错文本或给精度失败下归因结论——那是让待验收算子给自己出考卷。

工程目录里的**公开接口面**照读不误，它是签名对齐的唯一合法来源：

| 可读 | 不可读 |
| --- | --- |
| 头文件里的函数声明（`aclnnXxx` 的参数名、顺序、类型） | kernel / host 侧计算实现 |
| 任务书、README、工程自带设计文档 | 内部断言、私有分支、tiling 阈值 |
| 构建入口脚本、工程 git 版本 | 报错字符串常量（预期文本只能来自任务书或基线生态） |

**签名只能从待验收算子工程目录里读**（`env.json` 的
`operator_project.path`），头文件构建前就在源码树里。

**任何情况下都不要去 CANN 内置算子工程找签名。**

装机目录下的同名接口是官方已发布的另一份代码，同名不代表同签名。

别队 vendor 目录、上一轮构建产物同理。

工程里找不到就停下来问用户，不要换个目录接着找。

**社区算子与 CANN 官方接口重名是常态，不是异常。** 不用去 `nm -D` 装机的
`libopapi.so`、也不用 grep 装机目录来确认这件事——确认了也不改变任何决定。
重名带来的真实风险只有一个（ATK 的签名自检会搜到官方那份头文件），它由 S3 的
`probe_env.py --custom-opp` 锁定、`check_opapi_binding.py` 裁决，
机制写在 [references/build-deploy.md](references/build-deploy.md#签名自检搜的是磁盘上的同名头文件)。

`align_signatures.py` 的 `--env` 会强制核对签名是从哪个文件读的，详见
[references/plugin-authoring.md](references/plugin-authoring.md)。

分面、物化、接线字段、能力域这些词在各 reference 里反复出现，动手前先读
[references/glossary.md](references/glossary.md)，一页读完。

写 S2 产物之前读 [references/atk-pitfalls.md](references/atk-pitfalls.md)，
它是 ATK 会咬人的地方，读了就能一次写对，读不到就必踩。

会拦下你的检查点有 19 道，全在 S2，别等着被逐个拦下来才知道有这道门。
有哪些门不用你去找：S2 的卡上就列着全部门名，跟着卡走就不会漏。
某道门具体检查什么、前提是什么，用门名查：`scripts/gate_lookup.py make_yaml.header_keys`。
全集在 [references/gate-inventory.md](references/gate-inventory.md)，按需查，不通读。

判据大多有前提，换一类算子时先跑 `scripts/gate_lookup.py --conditional` 看那几道。
**跳过不是默认选项**——判别标准是「这道门要拦的缺陷还可能不可能发生」。

ATK 框架源码是兜底证据，不是首选。
先用 reference、`assets/` 模板和脚本 `--help`。
「这个键能填吗、它到底怎么算」用 `scripts/atk_lookup.py` 查，不要 grep 源码。
这些都解释不了 ATK 的实际行为时再读 ATK 源码，不要靠猜。
读过就在证据里记下位置和结论，下一轮把它补进 reference。
精度阈值不适用兜底：阈值只从 `experimental_standard.md` 取。

不要修改待验收算子源码、构建脚本或编译产物。
验收 YAML、插件、证据和报告不属于待验收对象。

其他算子的验收目录（如 `atk-verify-<op>/`）不是证据：它们的接线、取值和
结论没有经过本次门禁，"先例通过了"不能替代读懂一条判据或规则。确有共性
经验要复用，先把它写成 reference 或量具规则，再照规则走，不要直接抄产物。

`scripts/` 是量具。
发现脚本缺陷时停止当前轮次：改量具要经得起回归测试——补一条复现缺陷的
用例，并在至少两个不触发该缺陷的既有基线/算子上验证没有引入新的误判，
全部过了才算修复，然后重跑受影响阶段，把改动和验证记入证据。
说不清「为什么没引入新误判」就不要动 `scripts/`：记录为已知问题，
剔除受影响用例并留痕，不要为了让当前用例通过而悄悄改判据。

覆盖、精度和性能判据必须能追溯到任务书、已确认基线或生态标准。
推断项先标记并请求确认。

所有数字由脚本产生。
模型只组织证据和文字，不人工重算通过率、覆盖率或性能选样。

一次 `atk case` 生成后冻结，部署或跑测失败不得重新生成；无效用例只剔除并留痕。
接线字段的受控改写走 `rewire_adapter.py`，它只在用例语义未变时放行。

精度分母由 `verdict.py` 算，包含全部有效执行用例；证据不足时使用 `unknown`。

整张只有一个取值的输入测不出错，由 `freeze_inputs.py` 冻结时核，不由声明认定。

一条失败的归因只覆盖它自己。
跨用例的结论先按用例规格特征分组，只对有证据的组下结论，其余写 `unknown`。

部署、绑定或最小用例失败时停止全量执行。
输出“阻塞·未验收”报告。
所有 ATK 任务通过 `run_atk_task.py` 拉起。
凡是要 CANN 环境的命令都以 `source evidence/env.sh` 开头，构建与安装也算。

## 阶段流程

验收分五个阶段，按顺序完成。
一个阶段只有出口门禁全过才算完成。

进入 S2 前建立工作目录，此后所有相对路径都相对它：

```text
<工作区>/atk-verify-<op>/
├── evidence/        env.json env.sh interface.json constraints.md repro.sh timeline.jsonl
├── conclusion/      accuracy_results.json performance_results.json verdict.json
├── frozen_<接口分面>/   冻结输入，每个分面一个目录
└── <op>_decl.json <op>.yaml <op>_constraint.py <op>_materialize.py
```

冻结目录按分面分开不是整洁问题：`freeze_inputs.py` 会清空目标目录重建，
两个分面写同一个目录时，后冻的那次把先冻的输入删掉，而用例号两边都从 0 起、
号段重叠，跑出来的精度是拿别人的输入算的，报告照出、数字全错。
写重了脚本会退出码 2 拦下来。

工作目录建在待验收算子工程的同级，不建在工程里面，也不建在 skill 目录里。

**每条命令自己带上 `cd <工作目录> &&`。** shell 工具每次调用都从会话启动目录
重新开始，上一条的 `cd` 不留到下一条。少写这一句，`-o evidence/xxx.json` 就落到
启动目录去了，下一条命令报「文件不存在」，然后去 `find` 满盘找——真机上第一条
摩擦记录就是这个。

阶段时间线固定写 `evidence/timeline.jsonl`，五个阶段用同一个路径，换了就接不上耗时。

| 阶段 | 出口门禁 | 冻结产物 | 入口 reference |
| --- | --- | --- | --- |
| S1 任务书解读 | 待确认项清零，环境指纹可用，接口事实已派生 | 约束表、`evidence/env.json`、`evidence/interface.json` | [intake.md](references/intake.md)、[experimental_standard.md](references/experimental_standard.md) |
| S2 用例生成 | 签名、结构、覆盖三道校验全过 | `must_cover.json`、YAML、插件、用例 JSON、冻结输入 | [case-design.md](references/case-design.md)、[yaml-schema.md](references/yaml-schema.md)、[atk-parameter-capabilities.md](references/atk-parameter-capabilities.md)、[plugin-authoring.md](references/plugin-authoring.md)、[atk-cli.md](references/atk-cli.md) |
| S3 编译安装部署 | 构建、安装、SoC/op_api/ABI 绑定和冒烟全过 | 绑定报告、冒烟日志 | [build-deploy.md](references/build-deploy.md)、[execution.md](references/execution.md) |
| S4 精度性能测试 | 精度已裁决，且性能状态非空 | accuracy 与 performance results、verdict | [reporting.md](references/reporting.md)、[performance.md](references/performance.md) |
| S5 输出测试结果 | 摘要、政策摘要和证据链三项核对通过 | 报告、结论、复现包 | 同 S4 |

`interface.json` 的 `baseline_kind` 是 `cann_builtin` 时，
S2 到 S4 额外读 [builtin-baseline.md](references/builtin-baseline.md)：
真值来自先跑一轮 CANN 内置实现存盘再读回来，跑测形态与上表默认路径不同。

S1 的两道门性质不同：任务书解读没有脚本，环境探测有脚本。
`env.json` 生成不等于 S1 通过，待确认项清零才算。

S2 结束即冻结 YAML、约束和用例 JSON。
S3 或 S4 失败都不得回到 S2 重新生成。

S4 的性能状态只能取三者之一：通过、未执行(精度未通过)、未执行(无基线)。
状态由 `verdict.py` 从性能产物推导，不由报告作者自己写。
无对比基线也要跑一轮 `performance_device`，把待验收算子端绝对耗时落盘供取用。

## 阶段作战卡

进入某一阶段时跑 `scripts/mark_step.py <阶段号> <阶段名> -o evidence/timeline.jsonl`。
它记一次时间线，并打印当阶段的卡。

卡列全该阶段的产物：写错了会怎样、规范在哪份 reference、由哪个量具校验。

卡由 `references/artifact-contracts.json` 渲染，是当阶段唯一要照着做的清单。
卡只给地图和雷区，规范正文在 reference 里。

忘了打卡也不会漏掉卡：跑本阶段任何一个量具时，它会补记一笔并把卡打到 stderr。
补记之后同阶段不再重复打，所以按时打卡与忘记打卡的上下文开销是一样的。

## 上下文被压缩后

一轮验收要几十次工具调用，中途上下文会被压缩，压缩后这份 SKILL.md 与
已读过的 reference 都不在上下文里了。

不要凭摘要往下写，也不要把读过的 reference 重读一遍。

跑 `scripts/probe_progress.py -C <工作目录>`：它从工作目录里已经落盘的产物
反推当前阶段、列出缺哪几件，并把当阶段的卡再打一遍。

然后只读三样：`evidence/constraints.md`、`evidence/interface.json`，
以及卡里为当前那件产物点名的那份 reference。

产物齐了的阶段就是过了，不重做、不重新生成。

## 进度呈现

固定五条阶段任务，名称与上表一致，不随算子变化。
阶段内的门禁不新开任务，只更新当前阶段的一行进度。

进度行由阶段号、阶段名和当前门禁状态组成：

```text
S1 任务书解读 · 待确认 2 项（性能基线接口、非连续声明）
S2 用例生成 · 2/3 门禁通过 · 分面 1/2
S3 编译安装部署 · 4/5 门禁通过
S4 精度性能测试 · 精度 98.2% 通过 · 性能 未执行(无基线)
S5 输出测试结果 · 证据链 3/3
```

接口分面不拆成并列任务，写成当前阶段的分面计数。

阻塞写成 `阻塞·未验收 @S3`，并附失败门禁名。
进度行不输出脚本命令、绝对路径或日志片段。

## 关键决策

### 接口与基线

执行后端由接口模式唯一推导，不是选择题，S1 跑 `derive_interface.py` 一次定死。

`aclnn → pyaclnn`、`pytorch → npu`、`kernel → kernel`，基线节点恒为 `cpu`；`triton` 和 `atb` 暂停验收。

`atk node -b aclnn` 要为每个算子写 C++ 绑定并编译 aclnnTest，不在本 skill 范围，不要用它。

任务书写“对标 `aclnnXxx`、等价 `torch.xxx`”时，待验收算子填 `aclnnXxx`。
基线直接填 `torch.xxx`。
待验收算子和基线不可填同一个接口。

### 精度标准

默认声明 `mixed_tolerance_bm`：ATK 按每个输出张量的 dtype 分别选路，浮点走混合容差，整型自动落到逐元素相等。
int8 是例外：ATK 有意把它当量化输出，容忍 ±1；输出取自输入的算子（median、topk）要先判它是不是量化值。
dtype 不同不构成拆分面的理由，判定表见 `references/experimental_standard.md#选哪个比较器`。

### 分面与生成

判据只有一条：这批用例能不能写进同一份 YAML，能就不分。
一份 YAML 只能写一个接口名、一套参数结构、一个输出位置、一种精度标准、一个执行器、一个生成器。
分开时把「哪个字段装不下」写进 `evidence/constraints.md`；说不出是哪个字段就是不该分。
每个分面使用独立 YAML、必测集、用例 JSON、冻结目录和 `verdict.json`。
每个接口分面仍只运行一次 `atk case`。
`verdict.py` 按 sha256 把 coverage 与 results 配成一对，一次只裁一个分面；
分面各出一份 `conclusion/verdict_<分面>.json`，报告再把它们汇总。
最终裁决必须汇总全部任务书要求的接口分面。

覆盖只声明语义轴、交互组和预算；combos 由 `make_must_cover.py` 生成，具体 shape 和参数由物化脚本填写，YAML 由 `make_yaml.py` 推导。

### 适配器

签名对齐在生成前完成，YAML 能否表达由 `make_yaml.py` 推导时自行判定。
运行时契约表决定 ATK 对象、同类型参数顺序、workspace、executor 和 optional pointer 的构造。
待验收算子工程 C/C++ 测试只能提供 ABI 证据。
不能替代 ATK 运行时契约表。

签名不匹配时只允许基于公开证据修正一次适配器，改动写进证据目录。

第二次仍失败就停下出阻塞报告，不得枚举常量、空指针或参数顺序来试错。

### 执行与归因

只从本次日志取得报告路径。
确认成功数、失败数、实际后端和待验收算子加载路径。
退出码为 0 不等于验收通过。

只因原始错误明确表明参数或 attr 无法形成调用而剔除用例。
环境、绑定和原因不明的失败不得剔除。
失败、超时或中止的已执行用例运行 `save_failed_cases.py`。

性能全轮默认 50 条，按执行拓扑分组，不按精度分面重复取样。
精度未通过时不执行性能；基线缺失只是不做通过判定，绝对耗时照样采集。

## 参考和脚本规则

只读取当前阶段的 reference。
reference 只提供该决策域的规范。
不通过链接追读其他 reference。

`references/artifact-contracts.json` 是量具读的骨架，卡和检查点清单都从它渲染。
不必打开它：它比整份 SKILL.md 还长，而卡和 `gate_lookup.py` 已经把里面的话
按需送到眼前。唯一没有别处可查的是产物依赖链（`consumed_by`，谁消费这件产物），
确实要查那个再打开。

脚本参数以脚本自身 `--help` 为准。
版本不兼容时只核对实际子命令。
不要使用裸 `atk`。
始终使用环境指纹中的绝对 CLI 和同一 Python。

常用入口见各阶段 reference。
不要在主技能中复制脚本参数。

### 停止即交付阻塞报告

S1 缺少任务书、工程、接口模式、精度或必要性能基线时停止；执行后端由接口模式推导，不单独构成缺失项。
S3 构建、安装、SoC/op_api/ABI 绑定或第二条冒烟失败时停止。
不要强跑全量、修改待验收对象或用人工结论绕过门禁。

阻塞报告写明失败阶段号、失败门禁名和解除阻塞所需信息。
