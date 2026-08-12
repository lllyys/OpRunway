# AGENTS.md — OpRunway 唯一仓级规则

**全程中文。** 仓根 `CLAUDE.md` 路由到本文件，并只承载仅 Claude 需要读取的操作规则；面向所有 agent 的共享仓规一律在本文件维护，不设第二套。

## 1. 目标与权威

OpRunway 验收调用方配对的“任务书 + 被测源码”。调用方给出二者即断言关联成立，不再按 PR、issue、
fork、ref 或 head 鉴权。任务书是语义、硬件和验收要求权威；源码、header、example 与 op_def 是 ABI、
能力和被测事实。

正式路径只有两段：

1. 从任务书和源码形成 spec 与 ATK 设计，由 ATK 实际生成 caseset；
2. 在 NPU 上 fresh build，使用 ATK 执行同一 caseset，采集精度、NPU profiler、加载 ELF 和输出证据，
   再由确定性代码生成终态。

Workflow 不连接、不运行、不采集、不消费 GPU 数据。GPU 精度表述只可解析为同库族 CPU 真值；GPU
性能或资源对比必须记为未验证限制，不能伪造，也不能据 NPU 绝对时间宣称达标。

## 2. 唯一实现与入口

设置：

```bash
export OPRUNWAY_PLUGIN_ROOT="$(git rev-parse --show-toplevel)/plugin"
```

- `plugin/oprunway/`：唯一确定性实现；
- `plugin/oprunway_cli.py accept`：唯一正式入口；
- `plugin/skills/acceptance-workflow/SKILL.md`：唯一 skill，也是唯一编排层。

不得恢复另一套 case generator、golden engine、runner、状态机、裁决器或兼容通路。ATK 缺失能力只能放在
调用方提供且被收据哈希绑定的最薄 execution/generator plugin；通用生产代码不得按具体算子名分支。

正式 runner form 固定为 `atk_aclnn`。安装 ATK、CANN、Python 依赖和建立网络隧道属于环境前置准备，
不是 plugin 能力。Plugin 只做版本/路径 preflight，绝不安装依赖、修改系统 Python、shell rc 或共享 CANN。
当前 build 能力固定为 `cann_ops_package_v1`：只在该 profile 内对算子泛化，不承诺任意仓形态。第二种真实
仓形态出现后新增独立 build profile adapter，不在旧 profile 中堆仓名或路径分支。单一算子暴露的 ATK
缺口留在被哈希绑定的 witness；第二个独立算子复现同一稳定缺口后，才评估提升为 capability adapter。

## 3. 确定性事实链

`oprunway.verdict.finalize` 是唯一终态生产者；skill 和报告不得重判。正式 PASS 至少绑定：

- 任务书 SHA-256 与 caller-trusted 关联声明；
- 目标源码子树内容锚，且原始输入、clean staging、build 前后逐字一致；另以同一 staging 忽略规则绑定包含
  `build.sh` 和共享构建文件的完整 build 输入锚；
- fresh build 命令、目标 SoC、fresh package、安装树与 CMake target 证据；
- fresh vendor ELF SHA-256、`GetWorkspaceSize`/执行双符号和 `nm` 证据；
- ATK 公开版本探测、可执行文件摘要、设计文件、生成 caseset、case 完整分母；
- ATK 实际加载的 vendor ELF、每个正常 case 的 DUT/CPU 输出及预期报错 case 的工作簿结果；
- 需要性能时，每个 case 的 ATK NPU device 时间以及原始 CANN profiler `op_statistic`/`op_summary` CSV；
- 最终 receipts 的互相哈希绑定。

任何显式 null、坏类型、漂移、软链目标、缺失 case、缺失输出、缺失 profiler、ATK 返回码异常或超时都
fail-closed。ATK 的进程返回码和“task success”文字不能单独作为成功依据。

## 4. 终态与归因

对外状态词表只有：`PASS`、`DUT_FAIL`、`PLUGIN_ERROR`、`UNSUPPORTED`、`NEEDS_INPUT`、`BLOCKED`。
其中正式 `acceptance.json` verdict 只有 `PASS`、`DUT_FAIL`、`UNSUPPORTED`；其余三项只描述未形成正式
裁决的 workflow/attempt，不是 DUT 结论。

- `DUT_FAIL` 用于同轮完整执行且证据明确的数值不匹配，或确定性门已独立证明的 DUT 能力缺失；
- 唯一允许在 execution 前形成的 DUT 能力缺失是 `TARGET_DELIVERY_MISSING`：任务书准入目标 SoC、fresh
  build/install 成功、请求 cache 与 host ACLNN 双符号已绑定，但安装树没有该 SoC 的算子
  ops-info/binary/kernel delivery；
- 普通 build 失败以及 ATK、adapter、harness、环境、超时或证据缺失不得归为 DUT 失败；
- 目标 SoC 不在任务书硬件集合为 `UNSUPPORTED`，不得执行 DUT；
- 未知 ABI/精度能力或输入内部事实不足为 `NEEDS_INPUT`；
- 依赖/NPU/外部服务不可用可为 `BLOCKED`；
- 其余流程实现问题为 `PLUGIN_ERROR`。

证据不完整绝不 PASS。`acceptance.json` 与中文 Markdown 只由确定性终结器生成。

## 5. 验收口径

- 精度是必选维度；默认由 ATK 在 NPU DUT 与任务书授权的 CPU 真值之间裁决。
- 随机算子必须在 spec/ATK 设计中声明任务书要求的统计或固定种子策略；不能用普通逐元素比较替代。
- 性能模式只有 `none` 与 `measure`。`measure` 使用 ATK `performance_device` 及其保存的 CANN profiler
  kernel 数据，明确 timing scope；GPU/原算子比值未同法实测时列为 `UNVALIDATED`。
- 内存、显存、workspace、带宽等资源不是第三验收维度；报告说明未评估，不宣称资源条款达标。
- 单 session 主动墙钟预算不得超过 7200 秒；不得靠缩减任务书覆盖、复用旧 build 或放宽判据提速。

## 6. 隔离、环境与权限

- Build、用例生成、测试、golden、profiler 和正式裁决都在 NPU 目标环境执行；本机只编辑、Git、只读探测。
- 物理 NPU 的发现、健康/空闲判断和互斥调度属于 agent 与目标环境的操作边界，不属于 plugin 的验收核心。
  Agent 必须读取当前目标的完整 `npu-smi info`，依据健康项和进程事实选择实际空闲卡；不得只看利用率。
- 启动正式 CLI 前，agent 必须在 plugin 外对所选物理卡取得预置机器共享路径上的非阻塞 `flock`，持锁覆盖
  整个正式 CLI，并在锁内紧邻启动前再次读取 `npu-smi` 复核。已有进程、异常卡或已持锁卡只能拒绝；禁止
  kill、reset、抢占或覆盖锁。同一 A3 上不同空闲卡可以并行，同一卡由外部锁互斥。
- Formal CLI 只接收显式 `--physical-device N`，把它隔离映射为逻辑 device 0，并在 execution receipt 中记录
  实际 child environment；CLI 不自动选卡、不解析 `npu-smi`、不创建或裁决机器 lease-domain receipt。
- 没有可用卡时，agent 必须向 Mr.0 列出每张候选卡的健康、占用或锁冲突事实并等待指定物理卡；指定不等于
  强占，后续仍须使用全新 session，重新检查并取得同一外部锁。
- 每次 `accept` 必须使用不存在的新 ASCII session 目录。源码 staging、build、安装、ATK 缓存、输出、日志和
  报告全部位于该目录；不同算子不得复用可变产物。
- 可共享只读的 ATK 安装和内容寻址依赖缓存。正式 session 自己复制 caller source，外部源码与任务书只读。
- 当前 ignored `real-machine.env` 中，A3/A5 的 input-cache 配置值均逐字列入各自 protected roots；这些 cache
  只允许读取并复制到 fresh session，不得在原位 checkout、build、安装、写日志或修改内容。配置关系不成立
  时不得自行假定其它 cache 也是只读权威输入。
- 私有主机、容器和路径只放 ignored `.oprunway/real-machine.env`；秘密不得写入仓库。
- 该文件只在主 checkout 维护一份，worktree 不各自复制、也不各自新建。读取路径一律解析为
  `"$(dirname "$(git rev-parse --git-common-dir)")/.oprunway/real-machine.env"`，在主 checkout 与任意 worktree 中都指向同一份。
- 该文件同时登记每个已在真机落地的算子输入：任务书与被测源码分别标注，并把其所在的根逐字列入 protected roots。
- 该文件存在时，远端操作先读取 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`；保护根及子目录永远只读。
- Clone、checkout、build、真机执行、删除/覆盖、远端环境修改和发布须有用户授权。授权不扩张到其它目标。
- 不 push、不 merge，除非用户明示；commit 不加 AI 署名或 trailer。

## 7. 输入、泛化与文档

- 仓名、算子名、路径、SoC、shape、dtype、阈值、URL/ref/head 不得硬编码在通用代码。
- 字段来源：语义/硬件/阈值来自任务书；ABI 来自 header/example；能力与 dtype 用 op_def 交叉验证。
- 新接口族只能通过稳定能力扩展；未知能力 fail-closed，不自动归类。
- 外部仓、任务书和样例保持 ignored，不成为 tracked 运行时依赖。
- 开发记录只写 `dev-doc/`；每次落地在 `dev-doc/oprunway-changes-brief.md` 顶部追加倒序摘要。
- 当前待办唯一入口为 `dev-doc/oprunway-todo.md`。

## 8. 记录系统边界

`canon/` 与 bureau 内容只作为保留的历史记录。普通架构、代码、测试、验收、FAIL 归因和文档实施不得读取、
查询、依赖或等待它们，也不得把它们当事实源、审批门、阻塞条件或裁决依据。唯一例外是用户明确发起
bureau/canon 的记录、整理、查询、审阅或维护任务；此例外不改变其 trust tier 或历史内容。

## 9. 发布前检查

Push 前对自上次 push 以来的代码做一轮 audit → fix → verify；散文规则单独审阅。一轮即停，剩余问题如实
报告。测试、代码接通、ATK 可导入或局部 evidence 都不得描述成算子正式通过。
