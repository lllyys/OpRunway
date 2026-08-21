---
name: repo-task-atk-accept
description: >-
  仅在有封印交接包、待验收工程目录和任务书时，在 NPU 上构建跑测、
  核对精度与性能是否满足任务书、判断提交能否放行；只有任务书时改用 repo-task-case-gen。
---

# ATK 算子测试验收

## 工作边界

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

装机目录下的同名接口是官方已发布的另一份代码，同名不代表同签名。别队 vendor 目录、
上一轮构建产物同理。工程里找不到就停下来问用户，不要换个目录接着找。

**社区算子与 CANN 官方接口重名是常态，不是异常。** 不用去 `nm -D` 装机的
`libopapi.so`、也不用 grep 装机目录来确认这件事——确认了也不改变任何决定。
重名带来的真实风险只有一个（ATK 的签名自检会搜到官方那份头文件），它由 S3 的
`probe_env.py --custom-opp` 锁定、`check_opapi_binding.py` 裁决，机制写在
[build-deploy.md](../references/build-deploy.md#签名自检搜的是磁盘上的同名头文件)。

`check_bundle.py` 会用 `--env` 里的工程根核对 PR 头文件的来源，规则见
[handoff.md](../references/handoff.md)。

精度分母由 `verdict.py` 算，包含全部有效执行用例；证据不足时使用 `unknown`。

一条失败的归因只覆盖它自己。跨用例的结论先按用例规格特征分组，
只对有证据的组下结论，其余写 `unknown`。

部署、绑定或最小用例失败时停止全量执行。输出“阻塞·未验收”报告。
所有 ATK 任务通过 `run_atk_task.py` 拉起。凡是要 CANN 环境的命令都以
`source evidence/env.sh` 开头，构建与安装也算。

## S0 接收与环境

从原交接包复制，不在原目录里验收。按以下顺序建立入口：

1. 把 `atk-case-<op>/` 复制为本轮的 `atk-verify-<op>/`。
2. 在副本里跑 `scripts/probe_env.py`，必须报「Phase A + B」。
3. 带同一份任务书和本轮 `env.json` 跑 `scripts/check_bundle.py`。
4. 给 `check_bundle.py --header <PR 头文件>`，核对 PR 与任务书派生接口。

`check_bundle.py` 核对交接包完整性、任务书 SHA256、ATK 版本与接口一致性，并写
`evidence/bundle_intake.json`。它或 `probe_env.py` 任一退出码非 0，立即输出
`阻塞·未验收 @S0`，不进入 S3。
会拦下你的检查点 S0 有 4 道，具体检查与前提用门名查 `scripts/gate_lookup.py`。

**接口不一致要单列为「PR 接口偏离任务书 §2.3」，不得归因为交接包损坏。**

## 阶段流程

验收侧按 S0 → S3 → S4 → S5 完成。一个阶段只有出口门禁全过才算完成。

所有相对路径都相对复制出的验收副本：

```text
<工作区>/atk-verify-<op>/
├── evidence/        env.json env.sh interface.json constraints.md repro.sh timeline.jsonl
├── conclusion/      accuracy_results.json performance_results.json verdict.json
├── frozen_<接口分面>/   交接包内的冻结输入
└── <op>_decl.json <op>.yaml <op>_constraint.py <op>_materialize.py
```

工作目录建在待验收算子工程的同级，不建在工程里面，也不建在 skill 目录里。

**每条命令自己带上 `cd <工作目录> &&`。** shell 工具每次调用都从会话启动目录
重新开始，上一条的 `cd` 不留到下一条。少写这一句，`-o evidence/xxx.json` 就落到
启动目录去了，下一条命令报「文件不存在」，然后去 `find` 满盘找——真机上第一条
摩擦记录就是这个。

阶段时间线固定写 `evidence/timeline.jsonl`，验收侧四个阶段用同一个路径，换了就接不上耗时。

| 阶段 | 出口门禁 | 冻结产物 | 量具 | 入口 reference |
| --- | --- | --- | --- | --- |
| S0 接收与环境 | 交接包完整 / 任务书一致 / ATK 版本一致 / 接口一致 / 环境指纹可用 | `bundle_intake.json` / `env.json` | `check_bundle.py` / `probe_env.py` | `handoff.md` / `execution.md` |
| S3 编译安装部署 | 构建 / 安装 / SoC / op_api / ABI 绑定 / 冒烟 | 绑定报告 / 冒烟日志 | 见作战卡 | `build-deploy.md` / `execution.md` |
| S4 精度性能测试 | 精度已裁决 / 性能状态非空 | accuracy / performance results / verdict | 见作战卡 | `reporting.md` / `performance.md` |
| S5 输出测试结果 | 摘要 / 政策摘要 / 证据链 | 报告 / 结论 / 复现包 | 见作战卡 | 同 S4 |

S0 入口读 [handoff.md](../references/handoff.md) 与
[execution.md](../references/execution.md)。S3 读 [build-deploy.md](../references/build-deploy.md)、
[execution.md](../references/execution.md) 与 [atk-cli.md](../references/atk-cli.md)；S4、S5 读
[reporting.md](../references/reporting.md) 与 [performance.md](../references/performance.md)。

`interface.json` 的 `baseline_kind` 是 `cann_builtin` 时，S3 到 S4 额外读
[builtin-baseline.md](../references/builtin-baseline.md)：真值来自先跑一轮 CANN 内置实现存盘再读回来，
跑测形态与上表默认路径不同。

S3 或 S4 失败都不得回到 S2 重新生成。

S4 的性能状态只能取三者之一：通过、未执行(精度未通过)、未执行(无基线)。
状态由 `verdict.py` 从性能产物推导，不由报告作者自己写。无对比基线也要跑一轮
`performance_device`，把待验收算子端绝对耗时落盘供取用。

## 冻结与接线改写

一次 `atk case` 生成后冻结，部署或跑测失败不得重新生成；无效用例只剔除并留痕。

**`rewire_adapter.py` 是封印后接线改写的唯一出口。**

接线字段的受控改写走 `rewire_adapter.py`，它只在用例语义未变时放行。改写成功后它会
同步 `evidence/bundle.json` 的文件摘要与改写记录，不得绕过它直接 patch 产物。

## 阶段作战卡

进入某一阶段时跑
`scripts/mark_step.py <阶段号> <阶段名> -o evidence/timeline.jsonl`。它记一次时间线，
并打印当阶段的卡。

卡列全该阶段的产物：写错了会怎样、规范在哪份 reference、由哪个量具校验。
卡由 `../references/artifact-contracts.json` 渲染，是当阶段唯一要照着做的清单。
卡只给地图和雷区，规范正文在 reference 里。

忘了打卡也不会漏掉卡：跑本阶段任何一个量具时，它会补记一笔并把卡打到 stderr。
补记之后同阶段不再重复打，所以按时打卡与忘记打卡的上下文开销是一样的。

## 上下文被压缩后

一轮验收要几十次工具调用，中途上下文会被压缩，压缩后这份 SKILL.md 与
已读过的 reference 都不在上下文里了。

不要凭摘要往下写，也不要把读过的 reference 重读一遍。跑
`scripts/probe_progress.py -C <工作目录>`：它会在报告首行写明 `acceptance`，
再从已落盘的产物反推当前阶段、列出缺件，并把当阶段的卡再打一遍。

然后只读三样：`evidence/constraints.md`、`evidence/interface.json`，
以及卡里为当前那件产物点名的那份 reference。产物齐了的阶段就是过了，不重做、
不重新生成。

## 进度呈现

固定四条阶段任务，名称与上表一致，不随算子变化。阶段内的门禁不新开任务，
只更新当前阶段的一行进度。
进度行由阶段号、阶段名和当前门禁状态组成：

```text
S0 接收与环境 · 4/5 门禁通过 · 接口一致性待核
S3 编译安装部署 · 4/5 门禁通过
S4 精度性能测试 · 精度 98.2% 通过 · 性能 未执行(无基线)
S5 输出测试结果 · 证据链 3/3
```

阻塞写成 `阻塞·未验收 @S3`，并附失败门禁名。进度行不输出脚本命令、绝对路径或日志片段。

## 执行与归因

只从本次日志取得报告路径。确认成功数、失败数、实际后端和待验收算子加载路径。
退出码为 0 不等于验收通过。

每个分面使用独立 YAML、必测集、用例 JSON、冻结目录和 `verdict.json`。
`verdict.py` 按 sha256 把 coverage 与 results 配成一对，一次只裁一个分面；
分面各出一份 `conclusion/verdict_<分面>.json`，报告再把它们汇总。
最终裁决必须汇总全部任务书要求的接口分面。

只因原始错误明确表明参数或 attr 无法形成调用而剔除用例。环境、绑定和原因不明的失败不得剔除。
失败、超时或中止的已执行用例运行 `save_failed_cases.py`。

性能全轮默认 50 条，按执行拓扑分组，不按精度分面重复取样。
精度未通过时不执行性能；基线缺失只是不做通过判定，绝对耗时照样采集。

## 停止即交付阻塞报告

出现以下任一情形时停止：

- S0 交接包完整性、任务书一致性、ATK 版本一致性或接口一致性不过
- S0 环境指纹不可用，或不是「Phase A + B」
- S3 构建、安装、SoC/op_api/ABI 绑定或第二条冒烟失败

不要强跑全量、修改待验收对象或用人工结论绕过门禁。

阻塞报告写明失败阶段号、失败门禁名和解除阻塞所需信息。
