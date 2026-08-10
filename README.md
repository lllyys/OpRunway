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

环境依赖由执行环境提前准备，plugin 不安装 ATK、CANN 或 Python 包。目标环境只需在 `PATH` 暴露公开
`atk` 命令，不要求 venv；多版本并存时才显式使用可选的 `--atk-bin`。正式调用：

```bash
export OPRUNWAY_PLUGIN_ROOT="$(git rev-parse --show-toplevel)/plugin"
python3 "$OPRUNWAY_PLUGIN_ROOT/oprunway_cli.py" accept \
  --spec /path/op.spec.json \
  --taskdoc /path/task.md \
  --source-root /path/read-only-source \
  --design /path/atk-design.yaml \
  --target-soc ascend910_93 \
  --session-dir /new/ascii/session
```

`session-dir` 必须不存在。复杂 ABI 才额外使用哈希绑定的 `--generator` 或 `--execution-plugin`；它们是
ATK 的薄适配输入，不是第二套 runner。最终只认 `<session>/reports/acceptance.json`，状态为 `PASS`、
`DUT_FAIL` 或 `UNSUPPORTED`。若入口未能形成正式裁决，`workflow.json` 与可写入时的 `attempt.json` 会记录
`PLUGIN_ERROR`、`NEEDS_INPUT` 或 `BLOCKED`；这些不是 `acceptance.json` verdict。完整说明见
[`plugin/README.md`](plugin/README.md) 与 [`AGENTS.md`](AGENTS.md)。
