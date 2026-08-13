---
name: acceptance-workflow
description: 对一对任务书与昇腾算子源码执行正式验收——在全新 session 内做 ATK 用例生成、NPU 上 fresh build、精度与性能取证，并按本文判据产出裁决。当调用方给出任务书加算子源码要求做 NPU 算子验收，或提到 ATK、acceptance.json、DUT_FAIL、TARGET_DELIVERY_MISSING 时使用。本 skill 不安装 ATK、CANN 或任何依赖，环境未就绪时停在 BLOCKED。
---

# Acceptance workflow

## 何时使用

调用方给出任务书和对应源码，要求做 NPU 算子验收时使用。安装依赖、建立代理隧道或准备 CANN/NPU 不属于
本 skill；这些前置不就绪时停止并标 `BLOCKED`。

## 价值顺序

只编排一次干净验收，不自行裁决。取舍时按以下顺序决定：

1. 任务书权威高于源码实现便利；调用方给定的任务书/源码关联无需再次鉴权。
2. 完整、可重放的证据高于尽快出结果；证据缺失时 fail-closed。
3. 声明式 spec/design 高于自定义代码。
4. 单一算子缺口留在被哈希绑定的 witness plugin，高于污染通用 core；本轮不做跨算子抽象。
5. 正式终态逐字引用，不自行归因或改写。

## 验收流程

复制这份清单到回复里，逐项勾掉再往下走：

```
- [ ] 步骤 1  确认适用
- [ ] 步骤 2  生成 op.spec.json
- [ ] 步骤 3  生成 ATK design
- [ ] 步骤 4  绑定官方 self-test bundle（调用方提供时必做）
- [ ] 步骤 5  选择能力落点
- [ ] 步骤 6  环境前置检查
- [ ] 步骤 7  选定并锁定物理卡
- [ ] 步骤 8  在锁内执行验收（只走一遍）
- [ ] 步骤 9  核对终态判据
- [ ] 步骤 10 产出收据与终态，逐字汇报
```

步骤 7 产出的物理卡编号是步骤 8 的必需入参，两步不可颠倒。

## 步骤 1　确认适用

调用方同时给出任务书与对应算子源码即可开始；二者的关联由调用方断言，不再按 PR、issue、ref 或 head
鉴权。输入是 URL 时先完整物化为本轮只读输入。

ATK、CANN 或 NPU 未准备好时停在 `BLOCKED`，ABI 或任务书事实不足时停在 `NEEDS_INPUT`。不安装依赖，
不现场生成 core 补丁。

## 步骤 2　生成 op.spec.json

从任务书抽取语义、硬件和验收维度，从 header/example 抽 ABI，从 op_def 交叉 dtype 与 SoC。只放稳定字段，
不写机器路径。

```json
{
  "schema": "oprunway.acceptance_spec",
  "schema_version": 1,
  "operator": {
    "name": "Example",
    "aclnn_name": "Example",
    "op_type": "Example",
    "build_token": "example",
    "source_subdir": "domain/example"
  },
  "task": {
    "taskdoc_sha256": "<64 hex>",
    "hardware": ["ascend910_93"],
    "dimensions": {"precision": true, "performance": "none"},
    "precision": {"atk_accuracy": "single_bm"},
    "unvalidated_requirements": ["无法由本 NPU workflow 取证的任务书原文条款"],
    "required_cases": [
      {"inputs": [{"index": 0, "dtype": "fp32", "shape": [2, 3]}]}
    ],
    "performance_required_cases": []
  },
  "runner": {
    "form": "atk_aclnn",
    "atk_version": "<preflight 探测到的 ATK 版本>",
    "device": 0,
    "case_timeout_seconds": 300,
    "stage_timeout_seconds": 3600,
    "workflow_timeout_seconds": 7200,
    "seed": 17
  },
  "build": {
    "profile": "cann_ops_package_v1",
    "vendor_name": "oprunway",
    "jobs": 8
  }
}
```

字段约束：

| 字段 | 含义 | 约束 | 违反时 |
|---|---|---|---|
| `task.hardware` | CANN 规范 SoC token，如 A2 `ascend910b`、A3 `ascend910_93`、Ascend 950 `ascend950` | 新硬件无需改 plugin 白名单 | 目标 SoC 不在集合内判 `UNSUPPORTED` |
| `dimensions.performance` | 只有 `none` 与 `measure` | `measure` 保存 ATK device 时间与代表 case 的原始 CANN profiler CSV | — |
| `unvalidated_requirements` | 本 workflow 无法取证的任务书条款 | GPU/原算子比值不得用 NPU 绝对时间替代，须逐项写入并由报告原样保留；无条款时用空列表 | 报告中不得宣称达标 |
| `required_cases` | 任务书最低覆盖契约，按 ACLNN 参数顺序列出必须生成的 dtype/shape/属性子集 | 不同条目必须匹配不同 case | 缺任一条在 fresh build 前停止；非空 caseset 不构成放行理由 |
| `performance_required_cases` | 按 `required_cases` 下标选代表场景 | `measure` 时必须非空，`none` 时必须为空 | 流程错误 |
| `precision.atk_accuracy` | ATK 精度比较器 | 与 ATK design 的 `standard.acc` 逐字一致 | 生成后逐 case 对账，不一致即拦截 |
| `runner.device` | 运行环境内的逻辑 device | 恒为 0；物理卡不是 spec 事实，不得写入 tracked 输入 | — |

全量 caseset 跑 accuracy，只有 `performance_required_cases` 选中的子集另跑 `performance_device` 与
profiler。空 Tensor 等无 kernel 用例因此仍纳入功能/精度，但不会伪造 profiler。

ATK 侧字段的权威说明见 [reference/atk/](reference/atk/)（上游逐字副本）。动手前先读那里，不要读 ATK 源码
反推；副本答不上或版本不匹配时才逆向，并把结论连同实测 ATK 版本写进本轮记录。

## 步骤 3　生成 ATK design

YAML 或 CSV，覆盖任务书要求的 dtype、shape、边界、属性组合与精度策略。

- 普通确定性算子用 ATK `single_bm` 或任务书阈值。
- 随机算子必须用任务书授权的固定种子或统计策略。ATK 不能表达该策略时，提供一个通用能力型
  execution/accuracy plugin 并作为 session 输入哈希绑定；不得退回旧 golden/runner 引擎，也不得用逐元素
  比较冒充统计验收。
- 任务书引用生态算子混合容差标准时用 ATK `mixed_tolerance_bm`，并把任务书要求的 dtype 阈值显式写入该
  对象；不得用 ATK 的无版本隐式默认值替代任务书。

**动手写 design 之前**读 [reference/atk/用例设计文件说明.md](reference/atk/用例设计文件说明.md)，
不要先写再对照——字段名与取值形态靠猜会反复返工。参数之间的耦合约束（例如某个属性必须落在输入 rank
范围内）有官方机制，见 [reference/atk/自定义参数约束.md](reference/atk/自定义参数约束.md)，不必自行发明。

design 的 `standard.acc` 必须与步骤 2 的 `task.precision.atk_accuracy` 逐字一致。不一致就回到步骤 2 改齐，
不要靠执行期对账去发现。

## 步骤 4　绑定官方 self-test bundle

**调用方未提供 bundle 时跳过本步。**

调用方给出任务书官方 `self_test_case/<op>/` 目录或 URL 时，该 bundle 高于现场推导、DUT README/example 和
手写 witness 子集，必须成为功能/精度的完整分母。

在 spec 增加 `task.case_bundle`，记录来源 locator、原始 case 数、预期生成总数、规范化投影 SHA-256，以及
cases/prototype/golden 每个文件的相对路径和 SHA-256。步骤 8 的 casegen 与 execution 必须都用这份已校验的包。

目录缺失、多文件、少文件、软链、摘要漂移或投影不一致，一律停在 `NEEDS_INPUT` 或流程错误，不得回退到
较小用例集。

Bundle 未包含但任务书单独要求的性能场景作为 supplemental case 追加，并只由 `performance_required_cases`
选择；不得用它替换官方 accuracy 分母。

## 步骤 5　选择能力落点

先选择最小的已声明能力：

| 已确认事实 | 落点 |
|---|---|
| ATK design 原生可表达 | 只写 spec 与 design，本步无产出 |
| 仅 case 组合或生成顺序无法表达 | generator plugin，作为 session 输入哈希绑定 |
| CPU 真值、统计比较或 ACLNN ABI 与 ATK 通用桥不兼容 | execution/accuracy plugin，同上 |
| 新仓构建形态不属于 `cann_ops_package_v1` | 停在 `BLOCKED`；不自行新增 build profile |
| ABI、任务书事实缺失或输入内部冲突 | `NEEDS_INPUT`，不猜测、不补特判 |

缺口一律留在被哈希绑定的 witness 边界，作为本轮 session 的输入，不进仓库。**本轮绝不修改 `plugin/` 下的
通用代码。**

本步选出的 plugin 在步骤 8 作为本轮 session 输入使用。

**选定落点之后、动手写插件之前**读对应那篇。generator 见
[reference/atk/自定义参数约束.md](reference/atk/自定义参数约束.md)。

execution plugin 以 [reference/atk/atk_user_guide.md](reference/atk/atk_user_guide.md) 第 338 行起的
「pyaclnn 最小接口」为准——它继承 `AclnnBaseApi`，与本仓固定的 `atk_aclnn` runner 匹配。
[reference/atk/自定义执行方式.md](reference/atk/自定义执行方式.md) 里的 ACLNN 示例继承 `BaseApi`，在
ATK 26.5.14 上会 `TypeError`，只可作为接口概念的参考，不要照抄；其执行器模板里的 GPU 分支也必须删掉。

照抄模板前先读 [reference/atk/README.md](reference/atk/README.md) 的「读之前必看」与「本仓适用性」两节。

## 步骤 6　环境前置检查

- 目标环境有可用的公开 `atk` 命令。它不在 `PATH` 上（例如装在某个虚拟环境目录里）或存在多个版本时，
  记下要用的那个可执行的绝对路径，在步骤 8 用该绝对路径调用。
- CANN 环境与目标 NPU 就绪。不要求 venv，也不关心 ATK 由系统、镜像、用户目录还是虚拟环境提供。
- 准备一个**不存在**的全新 ASCII session 路径。
- **调用方给的任务书与源码一律只读。** 由本轮 session 自己复制一份进来使用，不在原位 checkout、build、
  安装或写入任何产物。部分目标环境还会另行指定若干只读根，给了就一并遵守；**没有给不等于可以就地写入**。

任一项不满足即停在 `BLOCKED`，不要继续到步骤 7。

## 步骤 7　选定并锁定物理卡

设备发现和资源调度是 agent/目标环境的操作边界，不是 acceptance core。按序执行：

1. 读取当前目标完整 `npu-smi info`，逐卡检查健康项与进程事实；不得把利用率 0% 当成空闲证明。
2. 选择实际健康且空闲的物理卡，在 plugin 外对预置机器共享路径中的对应锁文件尝试非阻塞 `flock`。锁冲突
   只说明该卡已分配，记录事实后尝试其它卡，不得等待后抢占、删除锁文件或覆盖持有者。
3. 取得锁后，在锁内紧邻步骤 8 启动前再次读取 `npu-smi`。健康或占用状态有变则释放锁、拒绝该候选、回到
   本步第 1 项；不得 kill、reset 或向现有 NPU 进程发信号。
4. 把已锁定的编号带入步骤 8，并保持同一 `flock` 覆盖整个步骤 8。

同一目标环境的多个算子可以在不同空闲卡上并行；同一卡由外部共享锁互斥。每个算子仍使用独立 fresh
session，不得共享 staging、build、ATK cache、输出或报告等可变产物。

没有候选同时通过健康、空闲和锁检查时，向 Mr.0 报告 `DEVICE_UNAVAILABLE`，逐项列出每张候选卡的健康、
占用或锁冲突事实，然后停止等待指定物理 device。此时不启动步骤 8，也不生成或假称 workflow/DUT 结论。
Mr.0 指定只缩小候选范围，不授权强占；收到指定后仍须从本步第 1 项重来，并使用不存在的新 session。

## 步骤 8　在锁内执行验收

在锁内只走一遍，依次完成：只读输入锚定 → clean staging → ATK casegen → fresh package build/install →
ELF 双符号验证 → ATK accuracy/performance execution → 完整分母、实际加载 ELF、CPU/DUT 输出和 profiler
校验 → 终态。不得跳过任一环节，也不得事后单独重跑某一环节再把证据拼进来。

调用方给的每份输入先复制进本轮 session，此后一切校验、哈希与执行只用副本。每个 plugin 文件都要复制到
session 并记录 SHA-256；声明了 `task.case_bundle` 时，bundle 先完整复制到 fresh session，再把同一只读
副本用于 casegen 与 execution plugin，两处取值必须相同。

步骤 6 若记下了 `atk` 可执行的绝对路径，本步一律用该绝对路径调用，不依赖 `PATH` 解析。

ATK 执行是两次独立调用，不是一次。全量 caseset 跑 accuracy，并保存 DUT 与 CPU 两侧输出；
`performance_required_cases` 选中的子集另跑 `performance_device`，并保存原始 CANN profiler 数据。
`dimensions.performance` 为 `none` 时不跑第二次，也不得产出任何性能证据。两次各自留下完整的命令回执与
返回码，一次失败不跳过另一次（判据见步骤 9）。任务类型名以随包
[reference/atk/README.md](reference/atk/README.md) 的勘误为准，不要照抄副本正文里的清单。

把步骤 7 锁定的物理卡显式传给 ATK child 并记录同一个 `ASCEND_RT_VISIBLE_DEVICES=<N>`；本步不枚举候选、
不解析 `npu-smi`、不管理 machine lease-domain，也不产生设备分配 receipt——那些是步骤 7 的事。

全程最多 7200 秒主动墙钟，超时即终止本轮并清理整个进程组；不得延长预算后把重试结果当作本轮结果，也不得
靠缩减任务书覆盖、复用旧 build 或放宽判据提速。任一环节失败停在那里，按步骤 9 归因，不得就地修正后继续。

### 怎么等长命令跑完

fresh build 与 ATK execution 可能各自跑很久，远长于驱动方单次命令通常允许的时长；而驱动方在你停止动作时
可能判定你已做完。等待方式由这两条决定，与具体在什么环境里驱动无关：

- **不要指望一次调用把它等完。** 时间一长就会被单次命令的时长上限截断。
- **不要交给后台再干等。** 停止动作可能被判定为已完成；驱动进程退出时，它启动的进程会被一并终止。
- **让长命令脱离驱动进程运行**，把 PID 与返回码写进日志，这样驱动方即使中断，这一次执行仍能自己跑完。
- **然后反复做有界的检查。** 每次检查都是一次动作，等待期间因此始终有进展可见；每次都在时长上限内返回，
  因此不会被截断。检查到进程退出或产物出现就停止，再进入下一环节。

轮询期间不要重复启动同一条命令，也不要因为等得久就改判。锁在整个等待期间必须继续持有。

## 步骤 9　核对终态判据

精度与性能独立取证：只要性能维度已声明、目标仍可运行且总预算未耗尽，即使精度执行不完整也继续执行
performance/profile。

任一阶段的执行失败均保留为非 DUT 流程状态，不能据此直接生成 `DUT_FAIL`。

唯一在 build 阶段允许成立的 DUT 终态是强证据闭合的 `TARGET_DELIVERY_MISSING`：任务书准入目标 SoC、
fresh build/install 成功、请求 cache 与 host ACLNN 双符号已绑定，但安装树没有该 SoC 的算子
ops-info/binary/kernel delivery。此时停止 execution 并直接判 `DUT_FAIL`；普通 build 失败或
证据不完整仍是流程错误。

终态是 `PLUGIN_ERROR`、`NEEDS_INPUT` 或 `BLOCKED` 时，**修复流程后回到步骤 6 并使用全新 session 重跑**。
旧 session 只读保留，不覆盖、不拼接证据。

`UNSUPPORTED` 不在此列。它与 `PASS`、`DUT_FAIL` 一样是正式终态：目标 SoC 不在任务书硬件集合时没有流程
可修，重跑也不会改变结果。按步骤 10 逐字汇报后停止。更换目标 SoC 只能由调用方决定；不得为绕开这个终态
去改 spec 的 `task.hardware`，它来自任务书。

## 步骤 10　产出收据与终态，逐字汇报

本轮必须产出下列收据与终态文件，缺其一即本轮终态不成立：

- `receipts/source_facts.json`：任务书、caller-trusted 关联、算子源码锚、完整 build 输入锚和 SoC 准入；
- `receipts/cases.json`：ATK 版本与可执行文件 SHA-256、design/generator 摘要、实际生成 caseset；
- `receipts/build.json`：完整 build 输入锚、fresh build/package、install、ELF、符号和 target delivery；
- `receipts/execution.json`：显式物理卡到逻辑 device 0 的映射、ATK child environment、ATK 工作簿、完整
  分母、加载 ELF、CPU/DUT 输出和 profiler；
- `receipts/workflow.json`：主动总耗时和各阶段耗时；
- `reports/acceptance.json` / `.md`：唯一正式终态。

只逐字引用终态，不自行归因或改写。向用户汇报时输出 `reports/acceptance.json` 与
`receipts/workflow.json` 的状态、分阶段耗时和结构化未验证限制；所选物理卡作为环境调度事实单独报告，
不冒充 formal verdict。
