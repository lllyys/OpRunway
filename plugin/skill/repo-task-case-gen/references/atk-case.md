# ATK 用例生成命令

## 边界和版本

只使用环境指纹中的绝对 CLI。

命令以 `--help` 为准，不要猜。
`--help` 解释不了实际行为时可以读 ATK 源码确认，并在证据里留痕。

版本不是 `26.8.8` 时，只查看实际使用的 `case --help`、`node --help` 和 `task --help`。

帮助仍不足时报告“ATK 使用规则缺失”。

## 生成

有约束插件：

```bash
<atk-cli> case -f <yaml> -p <constraint>
```

无插件时省略 `-p`。

产物落在**当前工作目录**下：`result/<yaml 文件名>/json/all_<yaml 文件名>.json`。

目录名取自 YAML 的文件名，不是 `name`、`api` 或 `aclnn_name`。

日志里那一行叫 `save case json file:`，以它为准，不要自己拼路径。

`not get env ATK_TASK_OUTPUT_PATH` 是无害警告，产物路径与它无关。

本轮每个接口分面只运行一次 `atk case`。
