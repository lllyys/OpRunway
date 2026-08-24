---
name: repo-task-case-gen
description: >-
  仅在只有任务书时，为社区算子任务书生成 ATK 测试用例、准备验收用例、把用例冻结封印、
  为多个 PR 复用同一套用例；已有交接包、工程目录和任务书时改用 repo-task-atk-accept。
---

# ATK 用例生成

## 工作边界

只验收任务书要求的公开契约。

**验收范围由任务书的精度与性能目标划定。**

任务书的功能描述、工程文档里的语义形态（比如同一个接口在某个出参传空时走
另一套语义），是背景说明，不自动变成必测数据。默认只按任务书 §2.3 声明的那一份接口签名构造用例：
一份签名，一份用例集。

要为某个语义形态单独构造数据，必须用户明确说了要，并把这句话记进
`evidence/constraints.md`。

没有这句话就不构造，也不写进必测集。

多测一种形态要多一整套 YAML、必测集、用例 JSON、冻结、部署冒烟和结论，
而任务书的精度性能目标一条也没多覆盖。

生成侧不读任何工程目录。上文的工程背景只限任务书已写明的内容；签名只来自
任务书 §2.3，dtype 只来自 §2.4。任务书写不清就停在待确认，请用户先用
`repo-task-doc-write` 补齐，不要去工程里找答案。

`align_signatures.py --task-doc <任务书>` 会核对签名来源，详见
[plugin-authoring.md](references/plugin-authoring.md)。

写 S2 产物之前读 [atk-pitfalls.md](references/atk-pitfalls.md)，它是 ATK 会咬人的地方，
读了就能一次写对，读不到就必踩。

会拦下你的检查点 S2 有 20 道，别等着被逐个拦下来才知道有这道门。S2 的卡上列着
全部门名；某道门的检查和前提用门名查，例如
`scripts/gate_lookup.py make_yaml.header_keys`。全集在
[gate-inventory.md](references/gate-inventory.md)，按需查，不通读。

判据大多有前提，换一类算子时先跑 `scripts/gate_lookup.py --conditional` 看那几道。
**跳过不是默认选项**——判别标准是「这道门要拦的缺陷还可能不可能发生」。

ATK 框架源码是兜底证据，不是首选。先用 reference、`assets/` 模板和脚本 `--help`；
「这个键能填吗、它到底怎么算」用 `scripts/atk_lookup.py` 查，不要 grep 源码。这些都解释不了
ATK 的实际行为时再读 ATK 源码，不要靠猜。读过就在证据里记下位置和结论，
下一轮把它补进 reference。精度阈值不适用兜底：阈值只从 `experimental_standard.md` 取。

整张只有一个取值的输入测不出错，由 `freeze_inputs.py` 冻结时核，不由声明认定。

## 环境前提

生成侧只需 Python、已安装的 ATK 与 torch（CPU），不需要 NPU 和 CANN。跑
`scripts/probe_env.py`，报告能力是「仅 Phase A」即可继续；没有 Phase A 就停在 S1。

## 阶段流程

生成侧按 S1 → S2 → 封印完成。一个阶段只有出口门禁全过才算完成。

进入 S1 前建立工作目录，此后所有相对路径都相对它：

```text
<工作区>/atk-case-<op>/
├── evidence/        env.json env.sh interface.json constraints.md timeline.jsonl
├── frozen_<接口分面>/   冻结输入，每个分面一个目录
└── <op>_decl.json <op>.yaml <op>_constraint.py <op>_materialize.py
```

冻结目录按分面分开不是整洁问题：`freeze_inputs.py` 会清空目标目录重建，
两个分面写同一个目录时，后冻的那次把先冻的输入删掉，而用例号两边都从 0 起、
号段重叠，跑出来的精度是拿别人的输入算的，报告照出、数字全错。
写重了脚本会退出码 2 拦下来。

工作目录不建在任何工程目录或 skill 目录里。

**每条命令自己带上 `cd <工作目录> &&`。** shell 工具每次调用都从会话启动目录
重新开始，上一条的 `cd` 不留到下一条。少写这一句，`-o evidence/xxx.json` 就落到
启动目录去了，下一条命令报「文件不存在」，然后去 `find` 满盘找——真机上第一条
摩擦记录就是这个。

阶段时间线固定写 `evidence/timeline.jsonl`，两个阶段用同一个路径，换了就接不上耗时。

| 阶段 | 出口门禁 | 冻结产物 | 量具 | 入口 reference |
| --- | --- | --- | --- | --- |
| S1 任务书解读 | 待确认项清零 / 环境指纹可用 / 接口事实已派生 | 约束表 / `env.json` / `interface.json` | `probe_env.py` | `intake.md` / `experimental_standard.md` / `environment.md` |
| S2 用例生成 | 签名 / 结构 / 覆盖 / 适配器 / 封印 | 必测集 / YAML / 插件 / 用例 JSON / 冻结输入 / `bundle.json` | `seal_bundle.py` | `case-design.md` / `handoff.md` / `handoff-seal.md` / `workdir-freeze.md` |

S1 入口读 [intake.md](references/intake.md)、
[experimental_standard.md](references/experimental_standard.md) 与
[environment.md](references/environment.md)。S2 入口读以下 reference：

- [case-design.md](references/case-design.md)
- [yaml-schema.md](references/yaml-schema.md)
- [atk-parameter-capabilities.md](references/atk-parameter-capabilities.md)
- [plugin-authoring.md](references/plugin-authoring.md)
- [atk-case.md](references/atk-case.md)
- [handoff.md](references/handoff.md)
- [handoff-seal.md](references/handoff-seal.md)
- [workdir-freeze.md](references/workdir-freeze.md)

`interface.json` 的 `baseline_kind` 是 `cann_builtin` 时，S2 额外读
[builtin-baseline-design.md](references/builtin-baseline-design.md)：真值来自先跑一轮 CANN 内置实现存盘再读回来，
跑测形态与上表默认路径不同。

S1 的两道门性质不同：任务书解读没有脚本，环境探测有脚本。
`env.json` 生成不等于 S1 通过，待确认项清零才算。

S2 结束即冻结 YAML、约束和用例 JSON。一次 `atk case` 生成后冻结，
部署或跑测失败不得重新生成；无效用例只剔除并留痕。

## 封印

S2 的所有量具与出口门禁通过后，最后跑 `scripts/seal_bundle.py`。它核对产物与门禁证据，
逐文件计算 SHA256，并写 `evidence/bundle.json`。

退出码 0 表示封印成功；退出码 2 表示产物不齐或门禁未过；退出码 3 表示工作目录
结构不对。

**封印成功后不得再改动或重跑任何 S2 产物。**

## 阶段作战卡

进入某一阶段时跑
`scripts/mark_step.py <阶段号> <阶段名> -o evidence/timeline.jsonl`。它记一次时间线，
并打印当阶段的卡。

卡列全该阶段的产物：写错了会怎样、规范在哪份 reference、由哪个量具校验。
卡由 `references/artifact-contracts.json` 渲染，是当阶段唯一要照着做的清单。
卡只给地图和雷区，规范正文在 reference 里。

忘了打卡也不会漏掉卡：跑本阶段任何一个量具时，它会补记一笔并把卡打到 stderr。
补记之后同阶段不再重复打，所以按时打卡与忘记打卡的上下文开销是一样的。

## 上下文被压缩后

一轮生成要几十次工具调用，中途上下文会被压缩，压缩后这份 SKILL.md 与
已读过的 reference 都不在上下文里了。

不要凭摘要往下写，也不要把读过的 reference 重读一遍。跑
`scripts/probe_progress.py -C <工作目录>`：它会在报告首行写明 `case-gen`，
再从已落盘的产物反推当前阶段、列出缺件，并把当阶段的卡再打一遍。

然后只读三样：`evidence/constraints.md`、`evidence/interface.json`，
以及卡里为当前那件产物点名的那份 reference。产物齐了的阶段就是过了，不重做、
不重新生成。

## 进度呈现

固定两条阶段任务，名称与上表一致，不随算子变化。阶段内的门禁不新开任务，
只更新当前阶段的一行进度。
进度行由阶段号、阶段名和当前门禁状态组成：

```text
S1 任务书解读 · 待确认 2 项（性能基线接口、非连续声明）
S2 用例生成 · 4/5 门禁通过 · 分面 1/2 · 待封印
```

接口分面不拆成并列任务，写成当前阶段的分面计数。阻塞写成 `阻塞·未生成 @S1`
或 `阻塞·未生成 @S2`，并附失败门禁名。进度行不输出脚本命令、绝对路径或日志片段。

## 关键决策

### 接口与基线

执行后端由接口模式唯一推导，不是选择题，S1 跑 `derive_interface.py` 一次定死。

`aclnn → pyaclnn`、`pytorch → npu`、`kernel → kernel`，基线节点恒为 `cpu`；
`triton` 和 `atb` 暂停验收。

`atk node -b aclnn` 要为每个算子写 C++ 绑定并编译 aclnnTest，不在本 skill 范围，不要用它。

任务书写“对标 `aclnnXxx`、等价 `torch.xxx`”时，待验收算子填 `aclnnXxx`。
基线直接填 `torch.xxx`。待验收算子和基线不可填同一个接口。

### 精度标准

默认声明 `mixed_tolerance_bm`：ATK 按每个输出张量的 dtype 分别选路，浮点走混合容差，
整型自动落到逐元素相等。int8 是例外：ATK 有意把它当量化输出，容忍 ±1；
输出取自输入的算子（median、topk）要先判它是不是量化值。dtype 不同不构成拆分面的理由，
判定表见 `references/experimental_standard.md#选哪个比较器`。

### 分面与生成

判据只有一条：这批用例能不能写进同一份 YAML，能就不分。
一份 YAML 只能写一个接口名、一套参数结构、一个输出位置、一种精度标准、一个执行器、
一个生成器。分开时把「哪个字段装不下」写进 `evidence/constraints.md`；
说不出是哪个字段就是不该分。

每个分面使用独立 YAML、必测集、用例 JSON、冻结目录。
每个接口分面仍只运行一次 `atk case`。

覆盖只声明语义轴、交互组和预算；combos 由 `make_must_cover.py` 生成，具体 shape 和参数
由物化脚本填写，YAML 由 `make_yaml.py` 推导。

### 适配器

签名对齐在生成前完成，YAML 能否表达由 `make_yaml.py` 推导时自行判定。
运行时契约表决定 ATK 对象、同类型参数顺序、workspace、executor 和 optional pointer 的构造。
待验收算子工程 C/C++ 测试只能提供 ABI 证据。
不能替代 ATK 运行时契约表。

签名不匹配时只允许基于公开证据修正一次适配器，改动写进证据目录。

第二次仍失败就停下出阻塞报告，不得枚举常量、空指针或参数顺序来试错。

## 停止即交付阻塞报告

出现以下任一情形时停止：

- S1 任务书、接口模式、精度或必要性能基线缺失，或 §2.3 / §2.4 写不清
- S2 任一出口门禁不过
- `seal_bundle.py` 退出码非 0

执行后端由接口模式推导，不单独构成缺失项。不要绕过门禁、重写判据或在封印后补件。

阻塞报告写明失败阶段号、失败门禁名和解除阻塞所需信息。
