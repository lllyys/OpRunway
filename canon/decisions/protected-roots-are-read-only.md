---
id: pg-protected-roots-are-read-only
title: Protected roots are read-only and never a working directory
updated: 2026-08-11
status: verified
rests_on:
  - { page: "[[Protected roots live only in the ignored machine env file]]", span: "^claim", because: "the read-only discipline applies to the roots declared in that env file" }
---

# Protected roots are read-only and never a working directory

保护根及其所有子目录永远只读，不得作为新的验收工作目录；任何变更必须由用户针对具体目录重新授权。 ^claim

**Verified.** AGENTS.md §6 · 2026-08-11
**Sources.** [[session unknown · 2026-07-28]]
