# OpRunway plugin

一个 ATK 驱动的昇腾 NPU 算子验收入口。Plugin 不安装 ATK/CANN，也不提供 GPU workflow；它只校验已准备
环境，然后在全新 session 中完成来源绑定、ATK 用例生成、fresh build、ATK 执行和确定性裁决。默认从
目标环境的 `PATH` 查找公开 `atk` 命令，不要求或探测 venv；多版本并存时才显式传 `--atk-bin`。

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

复杂 ABI 可额外传 `--generator` 或 `--execution-plugin`；它们是 session 输入，会被复制和哈希绑定，不是
平行 runner。正式产物位于 `<session>/receipts/` 与 `<session>/reports/`。

正式 `acceptance.json` verdict 只有 `PASS`、`DUT_FAIL`、`UNSUPPORTED`。未形成正式裁决时，
`workflow.json` 与可写入时的 `attempt.json` 使用 `PLUGIN_ERROR`、`NEEDS_INPUT` 或 `BLOCKED` 描述本轮尝试；
这些状态不是 DUT 结论。ATK 控制台文字、返回码、单测或局部证据都不是验收结论。
Spec 必须显式绑定 ATK 精度比较器，caseset 会逐 case 对账。精度与性能独立取证：声明了性能维度时，精度
执行不完整也不自动跳过性能；两者的执行错误均先保持 `PLUGIN_ERROR`，不会直接归因到 DUT。

开发测试必须在 NPU 目标环境执行：

```bash
cd "$OPRUNWAY_PLUGIN_ROOT"
OPRUNWAY_ATK_BIN="$(command -v atk)" \
OPRUNWAY_TASKDOC_ROOT=/path/to/taskdocs \
python3 -m unittest discover -s tests -v
```
