---
name: acceptance-workflow
description: 对一对任务书与昇腾算子源码执行正式验收——冻结 spec 与 ATK design，在 NPU 上 fresh build，用 ATK 生成用例并取证精度与性能，按本文判据产出裁决。当调用方给出任务书加算子源码要求做 NPU 算子验收，或提到 ATK、acceptance.json、DUT_FAIL、TARGET_DELIVERY_MISSING 时使用。本 skill 不安装 ATK、CANN 或任何依赖，环境未就绪时停在 BLOCKED。
---

# Acceptance workflow

## 何时使用

调用方给出任务书和对应源码，要求做 NPU 算子验收时使用。安装依赖、建立代理隧道或准备 CANN/NPU 不属于
本 skill；这些前置不就绪时停止并标 `BLOCKED`。

## 参考资料

写 spec、写 design、调 ATK 之前先读对应那篇，不要靠猜，也不要读 ATK 源码反推：

- [reference/atk-authoring.md](reference/atk-authoring.md)　`op.spec.json` 与 design 的形状、字段约束、
  能力落点、实测通过的 ATK 命令。**步骤 2 与步骤 4 之前必读。**
- [reference/atk-design-template.yaml](reference/atk-design-template.yaml)　可直接照填的 design 模版，
  阈值已按标准写死。**写 design 从它开始，不要从零起稿。**
- [reference/atk-internals.md](reference/atk-internals.md)　ATK 内部注册表事实：阈值通路与键名白名单、
  内建默认值与标准不符的三种 dtype、两个比较器相反的 nan/inf 行为、边界生成的硬编码、执行桥的三个坑。
  **写 design 或插件前读它，别再去逆向源码。**
- [reference/experimental_standard.md](reference/experimental_standard.md)　《生态算子开源精度标准》，
  精度判据的唯一来源。**任务书里凡引用 AscendOpTest 之处都读作这份。** 步骤 2 必读。
- [reference/atk-source-facts.md](reference/atk-source-facts.md)　ATK 26.5.14 源码里几个会造成**假通过**
  的行为。写 design 之前必读。
- [reference/atk/](reference/atk/)　上游文档逐字副本，字段含义与完整取值以它为准：
  [用例设计文件说明.md](reference/atk/用例设计文件说明.md) 讲 design 字段，
  [自定义参数约束.md](reference/atk/自定义参数约束.md) 讲 generator plugin，
  [atk_user_guide.md](reference/atk/atk_user_guide.md) 的「pyaclnn 最小接口」讲 execution plugin。
  照抄任何模板前先看 [README.md](reference/atk/README.md) 的「读之前必看」与「本仓适用性」——
  [自定义执行方式.md](reference/atk/自定义执行方式.md) 的 ACLNN 示例继承 `BaseApi`，26.5.14 上会
  `TypeError`。

## 验收流程

复制这份清单到回复里，逐项勾掉再往下走：

```
- [ ] 步骤 1  锚定输入与环境准入
- [ ] 步骤 2  冻结 spec 与 ATK design
- [ ] 步骤 3  编译、安装、验证装载身份
- [ ] 步骤 4  生成 ATK case
- [ ] 步骤 5  精度测试
- [ ] 步骤 6  性能测试
- [ ] 步骤 7  证据闭合与报告
```

另有三节贯穿性规则在步骤 7 之后——「贯穿全程」「失败与修复的处置」「怎么等长命令跑完」，动手前先读。

**步骤 2 必须在步骤 3 之前完成并冻结。** 先看见实现再设计 case，就会照着实现裁剪 case：任务书要求的
边界跑不通就从 design 里省掉，剩下的全过，于是对一个缩了水的任务宣布 PASS。

## 步骤 1　锚定输入与环境准入

- **调用方给的任务书与源码一律只读。** 复制一份进本轮 session，此后一切校验、哈希与执行只用副本；不在
  原位 checkout、build、安装或写入任何产物。部分目标环境还会另行指定若干只读根，给了就一并遵守；没有给
  不等于可以就地写入。
- 记录任务书 SHA-256 与算子源码子树的内容锚。这两个锚是「测的到底是哪一份」的唯一证据，缺了就不成其为
  验收。
- session 目录必须是一个**不存在**的全新 ASCII 路径。
- 找到 `atk` 可执行文件，记下**绝对路径**、公开版本与 SHA-256。它常不在 `PATH` 上（例如装在某个虚拟
  环境目录里）。此后生成 case 与执行一律用这一个绝对路径：两处用了不同版本，生成语义可能不同，而报告
  只会写一句「ATK 就绪」。
- CANN 环境与目标 NPU 就绪。不关心 ATK 由系统、镜像、用户目录还是虚拟环境提供。

目标 SoC 不在任务书的硬件集合内即 `UNSUPPORTED`，不执行 DUT。**这个判断先于环境检查**：SoC 不匹配是确定
的结论，不该被「ATK 没装」这类可修复的问题遮住。其余任一项不满足停在 `BLOCKED`。

## 步骤 2　冻结 spec 与 ATK design

从任务书抽语义、硬件、验收维度与阈值；从 header/example 抽 ABI；用 op\_def 交叉验证 dtype 与 SoC 能力。
本步产出 `op.spec.json`、ATK design，以及 design 表达不了时的 plugin，离开本步时全部冻结。

形状、字段约束与能力落点见 [reference/atk-authoring.md](reference/atk-authoring.md)。写 design 之前另读
[reference/atk-source-facts.md](reference/atk-source-facts.md)：`boundary` 有两个默认值会造成假通过，
必须显式覆盖。

任务书事实、ABI 或精度口径不足时停在 `NEEDS_INPUT`，不猜测、不补特判。仓的构建形态不属于
`cann_ops_package_v1` 时停在 `BLOCKED`，不自行新增 build profile。

## 步骤 3　编译、安装、验证装载身份

1. 从 session 内的源码副本建 clean staging，记录完整 build 输入锚（含 `build.sh` 与共享构建文件）。
2. fresh build（长命令）。
3. 安装到本轮 session 自己的安装树。
4. **验证装载身份。** 记录 vendor ELF 的 SHA-256，用 `nm` 证明它同时导出 `aclnnXxxGetWorkspaceSize` 与
   `aclnnXxx`，并把 `ATK_CUSTOM_OPP_PATH` 钉到本轮安装树。CANN 自带的 `libopapi.so` 可能本身就导出同名
   `aclnnXxx`——不钉路径、不验来源，测的可能是系统实现而不是被测代码，而且完全无声。这是**最容易产生假
   PASS 的一处**。
5. 检查该 SoC 的算子交付（ops-info / binary / kernel）。任务书准入的 SoC、build 与 install 均成功、双符号
   已绑定，但安装树里没有该 SoC 的交付 → 直接判 `DUT_FAIL / TARGET_DELIVERY_MISSING` 并停止后续。**这是
   执行前唯一允许成立的 DUT 结论**；普通 build 失败是流程错误，不是 DUT 失败。

## 步骤 4　生成 ATK case

用步骤 2 冻结的 design（和 generator plugin）调用 ATK 生成 caseset，命令见
[reference/atk-authoring.md](reference/atk-authoring.md)。

生成后与 `required_cases` 逐条对账，**对的是 case ID 集合，不是数量**：任务书每一条必测覆盖都要映射到一个
不同的实际 case ID。数量对得上不代表覆盖到了——漏掉一个边界 case、另一个重复生成，计数完全一样。

这份 caseset 就是本轮的完整分母，此后不再增删。

## 步骤 5　精度测试

全量 caseset 跑 ATK accuracy，保存 DUT 与 CPU 两侧输出、完整命令回执与返回码。

执行前 `source` 安装树里的 `set_env.bash`——CANN 的脚本会读未定义变量，`set -u` 要临时关掉。ATK 的 `-o`
目录只校验存在、不会创建，先 `mkdir -p`。

ATK 的进程返回码和「task success」文字不能单独作为成功依据：要逐 case 看工作簿结果，核对每个正常 case
都有 DUT 与 CPU 输出，预期报错的 case 有对应的报错匹配结果。

## 步骤 6　性能测试

**测量恒做**，与任务书是否提出性能要求无关。跑 ATK `performance_device`，保存 device 时间与原始 CANN
profiler 数据（`op_statistic` / `op_summary`）。

**是否构成判据由任务书决定**，即 `task.performance_is_verdict`：

- 任务书有性能要求：数值参与终态，采集失败是真缺口。
- 任务书没有要求：数值照进报告，明确标「任务书未提出性能要求，此处仅为实测值，不构成达标声明」；采集
  失败只记 `UNVALIDATED`，**不改变精度结论**。把测量变成无条件的，不等于给每一轮多加一个失败面。

跑哪些 case：任务书指定了代表场景就用它的；没指定时按固定规则选——**每个 dtype 取最小与最大各一个**。
只测最大 shape 会漏掉小 shape 上的启动开销回退，两端都要。不产生 kernel 的 case（空 Tensor 等）排除在
profiler 分母之外并记下排除原因；没有 kernel 是事实不是故障，不得因此伪造数据。

两条措辞纪律：ATK 的 `avg_time` 是 profiler 窗口内所有行之和，不等于被测 kernel 的单独耗时，报告必须写清
timing scope；没有同法实测的 GPU 或原算子基线时，任何比值一律记 `UNVALIDATED`，NPU 绝对时间不能顶替。

预算不够跑完选中的子集时：任务书要求性能就停在 `NEEDS_INPUT`，请调用方指定代表场景；只是观测性采集就
停止采集并记 `UNVALIDATED`，不得偷偷缩减后宣称达标。

## 步骤 7　证据闭合与报告

先闭合证据，再产出终态。本步不做开放式「分析」——同一次 ATK 超时，一个人分析成 `DUT_FAIL`、另一个分析成
`BLOCKED`，那就等于没有判据。

**闭合检查**，任一不成立即不得 `PASS`：

- caseset 的 case ID 集合 = ATK 工作簿实际执行到的 ID 集合 = 有输出证据的 ID 集合；
- `required_cases` 每条都映射到一个不同的实际 case ID；
- ATK 实际加载的 vendor ELF 就是步骤 3 那一份；
- 每个正常 case 都有 DUT 与 CPU 输出，每个预期报错 case 有工作簿结果；
- 显式 null、坏类型、缺失 case、缺失输出、ATK 返回码异常或超时，一律 fail-closed。

**终态词表。** 正式 `acceptance.json` 的 verdict 只有 `PASS`、`DUT_FAIL`、`UNSUPPORTED`。
`PLUGIN_ERROR`、`NEEDS_INPUT`、`BLOCKED` 描述的是本轮没有形成 DUT 结论，它们不是 DUT 结论。

**归因。**

- `DUT_FAIL` 只用于同轮完整执行、证据明确的数值不匹配，或步骤 3 已证据闭合的 `TARGET_DELIVERY_MISSING`；
- harness、ATK、adapter、环境、超时或证据缺失一律不得归为 DUT 失败；
- 未知 ABI/精度口径或输入内部事实不足 → `NEEDS_INPUT`；依赖或 NPU 不可用 → `BLOCKED`；其余流程实现问题
  → `PLUGIN_ERROR`。

精度与性能独立取证：性能采集失败不影响精度结论，反之亦然。`UNSUPPORTED` 与 `PASS`、`DUT_FAIL` 一样是
正式终态，重跑不会改变结果；不得为绕开它去改 spec 的 `task.hardware`，那来自任务书。

终态是 `PLUGIN_ERROR`、`NEEDS_INPUT` 或 `BLOCKED` 时，修复流程后回到步骤 1 重跑，同一 session 内即可；
重跑前先清空会被重新产出的目录（staging、安装树、ATK 输出），再把对应收据整份重写。

**必须产出的文件**，缺其一即本轮终态不成立：

- `receipts/source_facts.json`：任务书、caller-trusted 关联、算子源码锚、完整 build 输入锚和 SoC 准入；
- `receipts/cases.json`：ATK 绝对路径、版本与可执行文件 SHA-256、design/generator 摘要、生成的 caseset；
- `receipts/build.json`：build 输入锚、fresh build/package、install、vendor ELF、双符号和 target delivery；
- `receipts/execution.json`：物理卡到逻辑 device 0 的映射、ATK child environment、工作簿、完整分母、实际
  加载的 ELF、CPU/DUT 输出和 profiler；
- `receipts/workflow.json`：主动总耗时和各阶段耗时；
- `reports/acceptance.json` / `.md`：唯一正式终态。

只逐字引用终态，不自行归因或改写。向用户汇报时输出 `reports/acceptance.json` 与
`receipts/workflow.json` 的状态、分阶段耗时和结构化未验证限制；所选物理卡作为环境调度事实单独报告，不
冒充 formal verdict。

## 贯穿全程

- ATK 一律用步骤 1 记下的绝对路径调用，不依赖 `PATH` 解析。
- 把调用方选定的物理卡显式传给每个 ATK child 并记录同一个 `ASCEND_RT_VISIBLE_DEVICES=<N>`；ATK 侧恒为
  逻辑 `--devices 0`。本 skill 不枚举候选、不解析 `npu-smi`，也不产生设备分配 receipt——选卡在本 skill
  之外完成。**调用方没有给出物理卡号就停在 `NEEDS_INPUT`，不要自己挑一张。** 利用率 0% 不构成空闲
  证据：卡上可能正跑着别人的进程，与人共卡会让性能数据失去意义，也可能干扰对方。
- 最多 7200 秒主动墙钟，超时即终止本轮并清理整个进程组；不得延长预算后把重试结果当作本轮结果，也不得靠
  缩减任务书覆盖、复用旧 build 或放宽判据提速。
- 缺口一律留在被哈希绑定的 witness plugin，作为本轮 session 的输入，不进仓库。**本轮绝不修改 `plugin/`
  下的通用代码。**

## 失败与修复的处置

任一步失败先停下判断修复的性质，再决定从哪里继续。要守的只有一条：**没有过期的证据活下来**——交出去的
那套收据必须描述同一次执行，而不是几次尝试里各取一段拼成的。按这条推：

- 修复没有改变任何已写收据所绑定的对象：就地修完**从失败的那一步继续**，前面已完成的步骤不动。
- 修复改变了被哈希绑定的 session 输入（spec、design、generator、execution plugin）：从第一个消费该输入的
  步骤起，其后每一步全部重做，对应收据整份重写，旧值不得留下任何一个字段。同一个 session 里重做即可。
- 被锚定的源码变了：那不是修复，是换了被测对象，本轮到此为止。

每次就地修复都要记进当步的回执：时刻、改了哪个文件、为什么改。一次执行里发生过几次就地修复，必须能从
收据上数出来；藏起来的修复等同于伪造过程。重做下游同理，要能看出哪些收据是重写过的。

判据不在可修之列。阈值、case 分母、必测覆盖、通过条件，以及任何会让原本不通过的结果变成通过的改动，本轮
内都不能动——中途放宽判据再往下走，产出的已经不是这一轮的验收结论。若发现判据本身就是错的，那是重新
发起一次验收，不是修复。

## 怎么等长命令跑完

步骤 3 的 build 与步骤 5、6 的 ATK 执行可能各自跑很久，远长于驱动方单次命令允许的时长；而驱动方在你停止
动作时可能判定你已做完。两头都要避开：

- **让长命令脱离驱动进程运行**，把 PID 与返回码写进日志，驱动方即使中断这一次执行仍能自己跑完。指望一次
  调用等完会被时长上限截断；交给后台再干等，则停止动作可能被判定为已完成，且驱动进程退出时它启动的进程
  会被一并终止。
- **然后反复做有界的检查。** 每次检查都是一次动作，等待期间因此始终有进展可见，且每次都在时长上限内
  返回。检查到进程退出或产物出现就停止。轮询期间不要重复启动同一条命令，也不要因为等得久就改判。

