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

环境依赖由执行环境提前准备，plugin 不安装 ATK、CANN 或 Python 包。目标环境有可用的公开 `atk` 命令即可，
不要求 venv。它不在 `PATH` 上（例如装在某个虚拟环境目录里）或存在多个版本时，用可选的 `--atk-bin` 显式
指定要用的那个可执行的绝对路径。正式调用：

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
[`plugin/README.md`](plugin/README.md) 与 [`AGENTS.md`](AGENTS.md)。想先在一次会话里试用而不做任何持久安装，
用 `claude --plugin-dir "$(git rev-parse --show-toplevel)/plugin"` 临时加载，插件入口与安装方式见
[`plugin/README.md`](plugin/README.md#作为-claude-code-plugin-加载)。

## 开发测试

测试必须在 NPU 目标环境执行，从仓根运行：

```bash
OPRUNWAY_ATK_BIN="$(command -v atk)" \
OPRUNWAY_TASKDOC_ROOT=/path/to/taskdocs \
OPRUNWAY_GAUSSIAN_BLUR_CASES_ROOT=/path/to/gaussian_blur/self_test_case \
python3 -m unittest discover -s tests -v
```

`tests/` 在仓根，不在 `plugin/` 下——它是本仓的开发资产，不随 plugin 分发。其中
`tests/witnesses/<算子>/` 是单元测试夹具，**不得作为验收输入使用**：直接复用会跳过「从任务书推导 spec 与
design」这一环，而那是全链上唯一无工具、无校验的环节。

在非 NPU 环境（例如 macOS）跑会有一批与代码无关的既有失败，来自 `/tmp` 到 `/private/tmp` 的符号链接解析
差异；判断改动是否引入回归应以 NPU 目标环境的结果为准。
