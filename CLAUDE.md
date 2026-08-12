@AGENTS.md

## 写入通路

Claude 对本仓工作树内文件的所有写入，一律通过 `/cc-suite:implement` 交由 Codex 落盘；
Claude 不直接使用 Write/Edit 修改仓内文件。仓外目标不受此约束：scratchpad 临时文件，
以及远端 NPU session 目录及其 build、安装、日志、报告产物（由正式 CLI 产生）。
