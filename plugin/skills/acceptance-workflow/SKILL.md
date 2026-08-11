---
name: acceptance-workflow
description: 用一个干净 session 执行 OpRunway 的 ATK 用例生成、fresh build、NPU 测试与确定性验收。
---

# Acceptance workflow

## 何时使用

调用方给出任务书和对应源码，要求做 NPU 算子验收时使用。安装依赖、建立代理隧道或准备 CANN/NPU 不属于
本 skill；这些前置不就绪时停止并标 `BLOCKED`。

## 准备两个声明式输入

从任务书抽取语义、硬件和验收维度，从 header/example 抽 ABI，从 op_def 交叉 dtype 与 SoC。生成：

1. `op.spec.json`：只放稳定字段，不写机器路径；
2. ATK YAML/CSV 设计：覆盖任务书要求的 dtype、shape、边界、属性组合与精度策略。

最小 spec：

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
    "atk_version": "26.5.14",
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

`runner.device` 固定表示隔离容器内的逻辑 device 0；物理卡不是 spec 事实，不得写入 tracked 输入。物理卡由
agent 在目标环境外部分配，formal CLI 只通过显式 `--physical-device N` 接收本轮选择，把它映射为逻辑 0，
并在 execution receipt 中记录实际 child environment。

`hardware` 使用 CANN 的规范 SoC token，例如 A2 `ascend910b`、A3 `ascend910_93`、Ascend 950
`ascend950`；未来硬件不需修改 plugin 白名单。性能只有 `none` 与 `measure`；`measure` 保存 ATK device
时间和代表 case 的原始 CANN profiler CSV。GPU/原算子比值不能由 NPU
绝对时间替代，须逐项写入 `unvalidated_requirements` 并由最终报告原样保留；没有限制时使用空列表。

`required_cases` 是任务书最低覆盖契约。每项按 ACLNN 参数顺序列出必须由 ATK 生成的 dtype、shape 或属性
值子集；不同条目必须匹配不同 case。ATK 输出缺任一条时在 fresh build 前停止，不能靠非空 caseset 放行。
`performance_required_cases` 按前者的下标选代表场景：`performance=measure` 时必须非空，`none` 时必须为空。
全量 caseset 跑 accuracy，只有该子集另跑 `performance_device` 和 profiler；空 Tensor 等无 kernel 用例因此
仍纳入功能/精度，但不会伪造 profiler。

### 任务书配套 self-test bundle

调用方若同时给出任务书官方 `self_test_case/<op>/` 目录或 URL，该 bundle 高于现场推导、DUT README/example
和手写 witness 子集，必须成为 Task 1 的功能/精度完整分母。Spec 用 `task.case_bundle` 记录来源 locator、原始
case 数、预期生成总数、规范化投影 SHA-256，以及 cases/prototype/golden 每个文件的相对路径和 SHA-256；
正式 CLI 必须同时传 `--task-cases-root`。目录缺失、多文件、少文件、软链、摘要漂移或投影不一致均停在
`NEEDS_INPUT`/流程错误，不得回退到较小用例集。

Bundle 未包含但任务书单独要求的性能场景仍作为 supplemental case 追加，并只由
`performance_required_cases` 选择；不得用它替换官方 accuracy 分母。GaussianBlur 当前绑定调用方指定的
`https://gitcode.com/cann/cann-ops-competitions/tree/master/04_tasks/01_community-task-2026/docs/202607/self_test_case/gaussian_blur/`
三文件 bundle：169 个官方 case 全量跑 accuracy，
任务书 S1 作为第 170 个 performance case。其官方用例不含 K13，所以不得自行把 K13 加入本轮分母。

普通确定性算子使用 ATK `single_bm`/任务书阈值。随机算子必须用任务书授权的固定种子或统计策略；若 ATK
当前不能表达该策略，先提供一个通用能力型 execution/accuracy plugin 并将其作为 session 输入哈希绑定，
不得退回旧 golden/runner 引擎，也不得用逐元素比较冒充统计验收。

`task.precision.atk_accuracy` 必须与 ATK design 的 `standard.acc` 逐字一致；生成后每个 case 再次对账，防止
design 与正式执行口径漂移。任务书引用生态算子混合容差标准时使用 ATK `mixed_tolerance_bm`，并把任务书
要求的 dtype 阈值显式写入该对象；不得用 ATK 的无版本隐式默认值替代任务书。

## 选择能力落点

先选择最小的已声明能力，不因一个见证算子的缺口改通用 core：

| 已确认事实 | 落点 |
|---|---|
| ATK design 原生可表达 | 只写 spec 与 design |
| 仅 case 组合或生成顺序无法表达 | generator plugin |
| CPU 真值、统计比较或 ACLNN ABI 与 ATK 通用桥不兼容 | execution/accuracy plugin |
| 第二个独立算子再次出现同一稳定缺口 | 才评估提升为按能力建模的 core adapter |
| ABI、任务书事实缺失或输入内部冲突 | `NEEDS_INPUT`，不猜测、不补特判 |
| 新仓构建形态不属于 `cann_ops_package_v1` | 新 build profile adapter，不在旧 profile 加分支 |

首个实例始终留在被哈希绑定的 witness 边界。只有第二个独立实例证明接口族稳定后，才讨论把共同机制提升
为 capability adapter；提升也不得携带算子名、仓名、shape、dtype、SoC 或阈值白名单。当前正式范围只覆盖
`atk_aclnn + cann_ops_package_v1`，是算子泛化，不是任意 repository build profile 泛化。

## 执行

先确认目标环境的 `PATH` 可解析公开的 `atk` 命令、CANN 环境和目标 NPU，并按下节协议在 plugin 外选定、
锁定和复核一个物理 device；同时准备一个不存在的全新 ASCII session 路径。
当前 ignored `real-machine.env` 已把 A3/A5 input cache 各自列入对应 protected roots，因此它们只作只读输入源：
允许复制 caller source 到 fresh session，禁止在 cache 原位 checkout、build、安装或写入产物；不得把这一事实
外推到配置未保护的其它路径。
插件不要求 venv，也不关心 ATK 是由系统、镜像、用户目录还是虚拟环境提供。然后只运行：

```bash
python3 "$OPRUNWAY_PLUGIN_ROOT/oprunway_cli.py" accept \
  --spec "$SPEC" \
  --taskdoc "$TASKDOC" \
  --source-root "$SOURCE" \
  --design "$ATK_DESIGN" \
  --task-cases-root "$TASK_CASES_ROOT" \
  --target-soc "$SOC" \
  --physical-device "$PHYSICAL_DEVICE" \
  --session-dir "$SESSION"
```

多版本并存时可用 `--atk-bin` 显式覆盖命令路径。只按上表选择 `--generator` 或
`--execution-plugin`；每个文件会复制到 session 并写入 SHA-256 收据。
`--task-cases-root` 仅在 spec 声明 `task.case_bundle` 时传入；bundle 会先完整复制到 fresh session，再把同一
只读副本传给 casegen 与 execution plugin。

### 环境侧非抢占设备分配与并行验收

设备发现和资源调度是 agent/目标环境的操作边界，不是 acceptance core。按以下顺序执行：

1. 在当前目标读取完整 `npu-smi info`，逐卡检查健康项与进程事实；不得把利用率 0% 当成空闲证明。
2. 选择实际健康且空闲的物理卡，在 plugin 外对预置机器共享路径中的对应锁文件尝试非阻塞 `flock`。锁冲突
   只说明该卡已分配，记录事实后尝试其它卡，不得等待后抢占、删除锁文件或覆盖持有者。
3. 取得锁后，在锁内紧邻正式 CLI 启动前再次读取 `npu-smi`。若健康或占用状态变化，释放锁并拒绝该候选；
   不得 kill、reset 或向现有 NPU 进程发信号。
4. 只把已锁定的编号作为 `--physical-device N` 传给 formal CLI，并保持同一 `flock` 覆盖整个 CLI 生命周期。
   CLI 将物理卡映射为逻辑 device 0，accuracy/performance 的 ATK child 使用并记录同一个
   `ASCEND_RT_VISIBLE_DEVICES=<N>`；plugin 不枚举候选、不解析 `npu-smi`、不管理 machine lease-domain，也不
   产生设备分配 receipt。

同一目标环境的多个算子可以在不同 A3 空闲卡上并行；同一卡由外部共享锁互斥。每个算子仍使用独立 fresh
session，不得共享 staging、build、ATK cache、输出或报告等可变产物。

若没有候选同时通过健康、空闲和锁检查，向 Mr.0 报告 `DEVICE_UNAVAILABLE`，逐项列出每张候选卡的健康、
占用或锁冲突事实，然后停止等待指定物理 device；此时不启动 formal CLI，也不生成或假称 workflow/DUT
结论。Mr.0 指定只缩小候选范围，不授权强占；收到指定后仍须重新检查、取得外部锁、锁内复核，并使用不存
在的新 session。指定卡仍不可用时继续等待，不得 kill、reset、preempt 或覆盖锁。

入口依次完成：只读输入锚定 → clean staging → ATK casegen → fresh package build/install → ELF 双符号验证 →
ATK accuracy/performance execution → 完整分母、实际加载 ELF、CPU/DUT 输出和 profiler 校验 → 确定性终态。
全程最多 7200 秒，超时杀整个进程组。

精度与性能独立取证：只要性能维度已声明、目标仍可运行且总预算未耗尽，即使精度执行不完整也继续执行
performance/profile。任一阶段的执行失败均保留为非 DUT 流程状态，不能据此直接生成 `DUT_FAIL`。
唯一 build 阶段 DUT 终态是强证据闭合的 `TARGET_DELIVERY_MISSING`：任务书准入目标 SoC、fresh build/install
成功、请求 cache 与 host ACLNN 双符号已绑定，但安装树没有该 SoC 的算子 ops-info/binary/kernel delivery。
此时停止 execution 并由唯一 finalizer 输出 `DUT_FAIL`；普通 build 失败或证据不完整仍是流程错误。

## 读取结果

- `receipts/source_facts.json`：任务书、caller-trusted 关联、算子源码锚、完整 build 输入锚和 SoC 准入；
- `receipts/cases.json`：ATK 版本与可执行文件 SHA-256、design/generator 摘要、实际生成 caseset；
- `receipts/build.json`：完整 build 输入锚、fresh build/package、install、ELF、符号和 target delivery；
- `receipts/execution.json`：显式物理卡到逻辑 device 0 的映射、ATK child environment、ATK 工作簿、完整
  分母、加载 ELF、CPU/DUT 输出和 profiler；
- `receipts/workflow.json`：主动总耗时和各阶段耗时；
- `reports/acceptance.json` / `.md`：唯一正式终态。

只逐字引用终态。`PLUGIN_ERROR`、`UNSUPPORTED`、`NEEDS_INPUT` 或 `BLOCKED` 都不是 DUT 失败；修复流程后必须
换全新 session 重跑。旧 session 只读保留，不覆盖、不拼接证据。
