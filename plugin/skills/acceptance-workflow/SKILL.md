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

`hardware` 使用 CANN 的规范 SoC token，例如 A2 `ascend910b`、A3 `ascend910_93`、Ascend 950
`ascend950`；未来硬件不需修改 plugin 白名单。性能只有 `none` 与 `measure`；`measure` 保存 ATK device
时间和代表 case 的原始 CANN profiler CSV。GPU/原算子比值不能由 NPU
绝对时间替代，须逐项写入 `unvalidated_requirements` 并由最终报告原样保留；没有限制时使用空列表。

`required_cases` 是任务书最低覆盖契约。每项按 ACLNN 参数顺序列出必须由 ATK 生成的 dtype、shape 或属性
值子集；不同条目必须匹配不同 case。ATK 输出缺任一条时在 fresh build 前停止，不能靠非空 caseset 放行。
`performance_required_cases` 按前者的下标选代表场景：`performance=measure` 时必须非空，`none` 时必须为空。
全量 caseset 跑 accuracy，只有该子集另跑 `performance_device` 和 profiler；空 Tensor 等无 kernel 用例因此
仍纳入功能/精度，但不会伪造 profiler。

普通确定性算子使用 ATK `single_bm`/任务书阈值。随机算子必须用任务书授权的固定种子或统计策略；若 ATK
当前不能表达该策略，先提供一个通用能力型 execution/accuracy plugin 并将其作为 session 输入哈希绑定，
不得退回旧 golden/runner 引擎，也不得用逐元素比较冒充统计验收。

`task.precision.atk_accuracy` 必须与 ATK design 的 `standard.acc` 逐字一致；生成后每个 case 再次对账，防止
design 与正式执行口径漂移。任务书引用生态算子混合容差标准时使用 ATK `mixed_tolerance_bm`，并把任务书
要求的 dtype 阈值显式写入该对象；不得用 ATK 的无版本隐式默认值替代任务书。

## 执行

先确认目标环境的 `PATH` 可解析公开的 `atk` 命令、CANN 环境、目标 NPU 和全新 ASCII session 路径。
插件不要求 venv，也不关心 ATK 是由系统、镜像、用户目录还是虚拟环境提供。然后只运行：

```bash
python3 "$OPRUNWAY_PLUGIN_ROOT/oprunway_cli.py" accept \
  --spec "$SPEC" \
  --taskdoc "$TASKDOC" \
  --source-root "$SOURCE" \
  --design "$ATK_DESIGN" \
  --target-soc "$SOC" \
  --session-dir "$SESSION"
```

多版本并存时可用 `--atk-bin` 显式覆盖命令路径。复杂 ABI 才传 `--generator` 或
`--execution-plugin`。每个文件会复制到 session 并写入 SHA-256 收据。

入口依次完成：只读输入锚定 → clean staging → ATK casegen → fresh package build/install → ELF 双符号验证 →
ATK accuracy/performance execution → 完整分母、实际加载 ELF、CPU/DUT 输出和 profiler 校验 → 确定性终态。
全程最多 7200 秒，超时杀整个进程组。

精度与性能独立取证：只要性能维度已声明、目标仍可运行且总预算未耗尽，即使精度执行不完整也继续执行
performance/profile。任一阶段的执行失败均保留为非 DUT 流程状态，不能据此直接生成 `DUT_FAIL`。

## 读取结果

- `receipts/source_facts.json`：任务书、caller-trusted 关联、算子源码锚、完整 build 输入锚和 SoC 准入；
- `receipts/cases.json`：ATK 版本与可执行文件 SHA-256、design/generator 摘要、实际生成 caseset；
- `receipts/build.json`：完整 build 输入锚、fresh build/package、install、ELF、符号和 target delivery；
- `receipts/execution.json`：ATK 工作簿、完整分母、加载 ELF、CPU/DUT 输出和 profiler；
- `receipts/workflow.json`：主动总耗时和各阶段耗时；
- `reports/acceptance.json` / `.md`：唯一正式终态。

只逐字引用终态。`PLUGIN_ERROR`、`UNSUPPORTED`、`NEEDS_INPUT` 或 `BLOCKED` 都不是 DUT 失败；修复流程后必须
换全新 session 重跑。旧 session 只读保留，不覆盖、不拼接证据。
