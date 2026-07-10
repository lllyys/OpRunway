---
title: A gate must validate the object that actually takes effect
updated: 2026-07-10
status: proposed
---

# A gate must validate the object that actually takes effect

**门校验的对象与实际生效的对象若不是同一个东西，绿色就没有意义。**

实证（2026-07-10）：`check_manifest_sync.py` 曾拿 `.claude-plugin/plugin.json` 的 `agents` 数组
与 `AGENTS.md` frontmatter 比对，判「双写漂移」。但 Claude Code 并不据该字段注册 agent
（见 [[Claude Code plugin agents load by directory discovery]]）——于是三道自查
（`check_manifest_sync.py` / `check_agent_frontmatter.py` / `claude plugin validate`）全绿，
而 4 个 agent 一个都没注册、`/op-acceptance` 调不起 primary。门校的是一个不生效的字段。

同源的第二例：`check_agent_frontmatter.py` 校的是项目自定约定（`mode` / `dispatch_mode` / 单轮纪律），
与「Claude Code 认不认这个 agent」无关。两个 checker 都 PASS，也证明不了 agent 能被加载。

**推论**：

- 比对的一侧应取**实际生效的事实源**。故 `check_manifest_sync.py` 改为
  `AGENTS.md` 注册清单 ↔ **文件系统**（`agents/*.md`、`skills/*/SKILL.md`）两方集合比对。
- 磁盘集合只是自动发现的**候选集**，仍不证明每个文件都被成功解析注册。门只校
  「声明 ↔ 磁盘候选集 ↔ 禁用字段」；**组件确实加载**须另证——`plugin details` 的 `Agents (N)`
  与真会话调度实测。**别把门的绿色当加载成功的证据。**
- 门必须 **fail-closed**：读不了 / 解析不了 / 语法不认识，一律判失败。截断的 frontmatter、
  畸形行被静默忽略、流列表空项被 `if x.strip()` 吞掉——每一处 fail-open 都能让门假绿。
- 门自身要有测试。`check_manifest_sync.py` 此前没有专属测试（5 个 parser 用例寄居在无关模块），
  这正是它带病多时无人察觉的原因。

与 [[Machine-verifiable acceptance gate]] 同宗：那条讲「防跑子集报 100%」，本条讲「防校错对象」。

**Sources.** [[session 64604f71-dd13-4256-9a74-072fec018b48 · 2026-07-09]]
