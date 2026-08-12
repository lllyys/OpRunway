---
id: pg-protected-roots-live-in-ignored-machine-env
title: Protected roots live only in the ignored machine env file
updated: 2026-08-11
status: verified
---

# Protected roots live only in the ignored machine env file

真实的远端保护路径只保存在 ignored 的机器环境文件 `.oprunway/real-machine.env` 中；仓库规则本身只记录通用的读取方式和禁止操作，不写入任何具体主机、容器或路径。 ^claim

仓内只跟踪脱敏模板 `.oprunway/real-machine.env.example`。远端操作前先从该环境文件读取 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`。

**Verified.** AGENTS.md §6 · 2026-08-11
**Sources.** [[session unknown · 2026-07-28]]
