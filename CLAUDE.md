@AGENTS.md

## 写入通路

cc-suite 可用时，Claude 对本仓工作树内文件的所有写入一律通过 `/cc-suite:implement` 交由 Codex 落盘，
Claude 不直接使用 Write/Edit 修改仓内文件。判据是仓根存在 `.cc-suite.md` 且 `codex` 在 `PATH` 上；
不确定时先跑 `/cc-suite:codex-preflight`。

cc-suite 不可用时（未安装、Codex 未认证或 preflight 失败）不阻塞工作：Claude 直接编辑，但必须在回复中
说明这一轮没有走 Codex 通路，不得默默降级。

两种情况下仓外目标都不受此约束：scratchpad 临时文件，以及远端 NPU session 目录及其 build、安装、
日志、报告产物（由正式 CLI 产生）。
