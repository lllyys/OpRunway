# OpRunway

OpRunway 用 ATK 验收调用方配对的“任务书 + 昇腾算子源码”。任务书定义语义、硬件与验收要求；源码、
header、example 和 op_def 定义 ABI 与被测事实。Workflow 不运行或消费 GPU 数据。

当前实现只有一条正式路径：

```text
caller-trusted inputs
  → source/taskdoc content anchors
  → ATK case generation + required-case coverage gate
  → fresh CANN package build + vendor ELF/symbol binding
  → ATK CPU/NPU accuracy and optional NPU profiler
  → deterministic acceptance.json
```

本仓没有命令行入口。唯一入口是 skill `/oprunway:acceptance-workflow`：把任务书与配对源码交给它，
它在 NPU 目标环境的全新 session 内完成来源绑定、ATK 用例生成、fresh build、精度与性能取证，
并按其中的判据产出终态与可离线核验的交付包。

`session-dir` 必须不存在。复杂 ABI 才额外使用哈希绑定的 `--generator` 或 `--execution-plugin`；它们是
ATK 的薄适配输入，不是第二套 runner。最终只认 `<session>/reports/acceptance.json`，状态为 `PASS`、
`DUT_FAIL` 或 `UNSUPPORTED`。若入口未能形成正式裁决，`workflow.json` 与可写入时的 `attempt.json` 会记录
`PLUGIN_ERROR`、`NEEDS_INPUT` 或 `BLOCKED`；这些不是 `acceptance.json` verdict。完整说明见
[`plugin/README.md`](plugin/README.md) 与 [`AGENTS.md`](AGENTS.md)。想先在一次会话里试用而不做任何持久安装，
用 `claude --plugin-dir "$(git rev-parse --show-toplevel)/plugin"` 临时加载，插件入口与安装方式见
[`plugin/README.md`](plugin/README.md#作为-claude-code-plugin-加载)。
