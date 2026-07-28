---
title: session unknown · 2026-07-28
updated: 2026-07-28
status: logbook
session: unknown
transcript: ""
---

## 2026-07-28T06:58:21-04:00 session unknown — 登记远端验收失败现场保护规则

**Intent.** 防止后续新 session 误修改或清理用户指定保留的远端验收失败现场。

**Decisions.**
- 真实保护路径只保存在 ignored 的机器环境文件中；仓规只记录通用读取和禁止操作规则。
- 保护根及其子目录只读，不得作为新验收工作目录；变更必须由用户针对具体目录重新授权。

**Changes.**
- `.oprunway/real-machine.env` (updated) — 登记机器本地真实保护根。
- `.oprunway/real-machine.env.example` (updated) — 增加脱敏保护根变量模板。
- `AGENTS.md` (updated) — 新 session 远端操作前强制检查保护根。
- `doc/oprunway-real-machine-environment.md` (updated) — 记录保护根操作纪律。

**Open threads.**
- 当前仅形成仓规和机器本地保护清单，尚未增加脚本级路径拒绝门。

**Source.** transcript ``
