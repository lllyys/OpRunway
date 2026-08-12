---
name: acceptance-workflow
description: 对一对任务书与昇腾算子源码执行正式验收——在全新 session 内做 ATK 用例生成、NPU 上 fresh build、精度与性能取证，并由确定性终结器产出裁决。当调用方给出任务书加算子源码要求做 NPU 算子验收，或提到 ATK、acceptance.json、DUT_FAIL、TARGET_DELIVERY_MISSING 时使用。本 skill 不安装 ATK、CANN 或任何依赖，环境未就绪时停在 BLOCKED。
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
4. 单一算子缺口留在被哈希绑定的 witness plugin，高于污染通用 core。
5. 第二个独立实例出现前不提前抽象；具体落点遵循步骤 5 的矩阵。
6. 正式终态逐字引用，不自行归因或改写。

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
- [ ] 步骤 8  调用 accept（在锁内，只调一次）
- [ ] 步骤 9  核对终态判据
- [ ] 步骤 10 读取收据并逐字汇报
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
| `runner.device` | 隔离容器内的逻辑 device | 恒为 0；物理卡不是 spec 事实，不得写入 tracked 输入 | — |

全量 caseset 跑 accuracy，只有 `performance_required_cases` 选中的子集另跑 `performance_device` 与
profiler。空 Tensor 等无 kernel 用例因此仍纳入功能/精度，但不会伪造 profiler。

## 步骤 3　生成 ATK design

YAML 或 CSV，覆盖任务书要求的 dtype、shape、边界、属性组合与精度策略。

- 普通确定性算子用 ATK `single_bm` 或任务书阈值。
- 随机算子必须用任务书授权的固定种子或统计策略。ATK 不能表达该策略时，提供一个通用能力型
  execution/accuracy plugin 并作为 session 输入哈希绑定；不得退回旧 golden/runner 引擎，也不得用逐元素
  比较冒充统计验收。
- 任务书引用生态算子混合容差标准时用 ATK `mixed_tolerance_bm`，并把任务书要求的 dtype 阈值显式写入该
  对象；不得用 ATK 的无版本隐式默认值替代任务书。

design 的 `standard.acc` 必须与步骤 2 的 `task.precision.atk_accuracy` 逐字一致。不一致就回到步骤 2 改齐，
不要靠执行期对账去发现。

## 步骤 4　绑定官方 self-test bundle

**调用方未提供 bundle 时跳过本步。**

调用方给出任务书官方 `self_test_case/<op>/` 目录或 URL 时，该 bundle 高于现场推导、DUT README/example 和
手写 witness 子集，必须成为功能/精度的完整分母。

在 spec 增加 `task.case_bundle`，记录来源 locator、原始 case 数、预期生成总数、规范化投影 SHA-256，以及
cases/prototype/golden 每个文件的相对路径和 SHA-256。步骤 8 必须相应追加 `--task-cases-root`。

目录缺失、多文件、少文件、软链、摘要漂移或投影不一致，一律停在 `NEEDS_INPUT` 或流程错误，不得回退到
较小用例集。

Bundle 未包含但任务书单独要求的性能场景作为 supplemental case 追加，并只由 `performance_required_cases`
选择；不得用它替换官方 accuracy 分母。

已绑定的具体算子 bundle 见 [reference/witnesses.md](reference/witnesses.md)。

## 步骤 5　选择能力落点

先选择最小的已声明能力，不因一个见证算子的缺口改通用 core：

| 已确认事实 | 落点 |
|---|---|
| ATK design 原生可表达 | 只写 spec 与 design，本步无产出 |
| 仅 case 组合或生成顺序无法表达 | generator plugin |
| CPU 真值、统计比较或 ACLNN ABI 与 ATK 通用桥不兼容 | execution/accuracy plugin |
| 第二个独立算子再次出现同一稳定缺口 | 才评估提升为按能力建模的 core adapter |
| ABI、任务书事实缺失或输入内部冲突 | `NEEDS_INPUT`，不猜测、不补特判 |
| 新仓构建形态不属于 `cann_ops_package_v1` | 新 build profile adapter，不在旧 profile 加分支 |

首个实例始终留在被哈希绑定的 witness 边界。只有第二个独立实例证明接口族稳定后，才讨论把共同机制提升为
capability adapter；提升也不得携带算子名、仓名、shape、dtype、SoC 或阈值白名单。正式范围只覆盖
`atk_aclnn + cann_ops_package_v1`，是算子泛化，不是任意 repository build profile 泛化。

本步选出的 plugin 在步骤 8 用 `--generator` 或 `--execution-plugin` 传入。

## 步骤 6　环境前置检查

- 目标环境有可用的公开 `atk` 命令。它不在 `PATH` 上（例如装在某个虚拟环境目录里）或存在多个版本时，
  记下要用的那个可执行的绝对路径，在步骤 8 用 `--atk-bin` 显式指定。
- CANN 环境与目标 NPU 就绪。不要求 venv，也不关心 ATK 由系统、镜像、用户目录还是虚拟环境提供。
- 准备一个**不存在**的全新 ASCII session 路径。
- `real-machine.env` 把某路径列为 protected root 时，该路径只作只读输入源：允许复制 caller source 到 fresh
  session，禁止在原位 checkout、build、安装或写入产物。不得把这一约束外推到未配置保护的其它路径。

任一项不满足即停在 `BLOCKED`，不要继续到步骤 7。

## 步骤 7　选定并锁定物理卡

设备发现和资源调度是 agent/目标环境的操作边界，不是 acceptance core。按序执行：

1. 读取当前目标完整 `npu-smi info`，逐卡检查健康项与进程事实；不得把利用率 0% 当成空闲证明。
2. 选择实际健康且空闲的物理卡，在 plugin 外对预置机器共享路径中的对应锁文件尝试非阻塞 `flock`。锁冲突
   只说明该卡已分配，记录事实后尝试其它卡，不得等待后抢占、删除锁文件或覆盖持有者。
3. 取得锁后，在锁内紧邻步骤 8 启动前再次读取 `npu-smi`。健康或占用状态有变则释放锁、拒绝该候选、回到
   本步第 1 项；不得 kill、reset 或向现有 NPU 进程发信号。
4. 把已锁定的编号带入步骤 8，并保持同一 `flock` 覆盖整个 CLI 生命周期。

同一目标环境的多个算子可以在不同空闲卡上并行；同一卡由外部共享锁互斥。每个算子仍使用独立 fresh
session，不得共享 staging、build、ATK cache、输出或报告等可变产物。

没有候选同时通过健康、空闲和锁检查时，向 Mr.0 报告 `DEVICE_UNAVAILABLE`，逐项列出每张候选卡的健康、
占用或锁冲突事实，然后停止等待指定物理 device。此时不启动步骤 8，也不生成或假称 workflow/DUT 结论。
Mr.0 指定只缩小候选范围，不授权强占；收到指定后仍须从本步第 1 项重来，并使用不存在的新 session。

## 步骤 8　调用 accept

在锁内只运行一次。基础形态：

```bash
python3 "$OPRUNWAY_PLUGIN_ROOT/oprunway_cli.py" accept \
  --spec "$SPEC" \
  --taskdoc "$TASKDOC" \
  --source-root "$SOURCE" \
  --design "$ATK_DESIGN" \
  --target-soc "$SOC" \
  --physical-device "$PHYSICAL_DEVICE" \
  --session-dir "$SESSION"
```

按前面步骤的结论追加可选参数，**没有对应结论就不要加**：

| 追加参数 | 何时加 |
|---|---|
| `--task-cases-root` | 步骤 4 声明了 `task.case_bundle` |
| `--generator` / `--execution-plugin` | 步骤 5 选出了对应 plugin |
| `--atk-bin` | 步骤 6 发现 `atk` 不在 `PATH` 上，或需要指定某个具体可执行 |

每个 plugin 文件都会复制到 session 并写入 SHA-256 收据；bundle 会先完整复制到 fresh session，再把同一只读
副本传给 casegen 与 execution plugin。不要手工拼子命令绕过正式入口。

CLI 把物理卡映射为逻辑 device 0，accuracy/performance 的 ATK child 使用并记录同一个
`ASCEND_RT_VISIBLE_DEVICES=<N>`；plugin 不枚举候选、不解析 `npu-smi`、不管理 machine lease-domain，也不
产生设备分配 receipt。

入口内部依次完成：只读输入锚定 → clean staging → ATK casegen → fresh package build/install → ELF 双符号
验证 → ATK accuracy/performance execution → 完整分母、实际加载 ELF、CPU/DUT 输出和 profiler 校验 →
确定性终态。全程最多 7200 秒，超时杀整个进程组。

### 怎么等它跑完

这一次调用可能跑到 7200 秒，远长于驱动方单次命令通常允许的时长；而驱动方在你停止动作时可能判定你已
做完。等待方式由这两条决定，与具体在什么环境里驱动无关：

- **不要指望一次调用把它等完。** 时间一长就会被单次命令的时长上限截断。
- **不要交给后台再干等。** 停止动作可能被判定为已完成；驱动进程退出时，它启动的进程会被一并终止。
- **让 accept 脱离驱动进程运行**，把 PID 与返回码写进日志，这样驱动方即使中断，这一次执行仍能自己跑完。
- **然后反复做有界的检查。** 每次检查都是一次动作，等待期间因此始终有进展可见；每次都在时长上限内返回，
  因此不会被截断。检查到进程退出或终态文件出现就停止，再进入步骤 9。

轮询期间不要重复调用 accept，也不要因为等得久就改判。锁在整个等待期间必须继续持有。

## 步骤 9　核对终态判据

精度与性能独立取证：只要性能维度已声明、目标仍可运行且总预算未耗尽，即使精度执行不完整也继续执行
performance/profile。

任一阶段的执行失败均保留为非 DUT 流程状态，不能据此直接生成 `DUT_FAIL`。

唯一在 build 阶段允许成立的 DUT 终态是强证据闭合的 `TARGET_DELIVERY_MISSING`：任务书准入目标 SoC、
fresh build/install 成功、请求 cache 与 host ACLNN 双符号已绑定，但安装树没有该 SoC 的算子
ops-info/binary/kernel delivery。此时停止 execution 并由唯一 finalizer 输出 `DUT_FAIL`；普通 build 失败或
证据不完整仍是流程错误。

终态是 `PLUGIN_ERROR`、`UNSUPPORTED`、`NEEDS_INPUT` 或 `BLOCKED` 时，**修复流程后回到步骤 6 并使用全新
session 重跑**。旧 session 只读保留，不覆盖、不拼接证据。

## 步骤 10　读取收据并逐字汇报

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
