---
title: A gate must validate the object that actually takes effect
updated: 2026-07-15
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

第三例（2026-07-10，acceptance 门）：[[Machine-verifiable acceptance gate]] 校 `evidence ⊂ caseset`
（evidence 覆盖 caseset 全部用例、id 一一对应），但看不见 `caseset ⊂ 任务书要求`。任务书要「所有数据类型」，
spec 只填 runner 支持的子集、`task_pr_gaps` 留空，caseset 因而只造那个子集——门校 caseset↔evidence 全绿、
裁决 PASSED，而任务书要求的 dtype 根本没测。门校的是 caseset↔evidence，实际该生效的对象是「任务书要求↔evidence」。
同源还有 [[oracle_source is a hardcoded constant not a recorded fact]]：门永远读到合法的 `cpu_ref` 常量，
无论 golden 实际从哪来。

**第三例部分已治（2026-07-15，Q7/Q9）**：[[Machine-verifiable acceptance gate]] 续加两门照本条纪律修——
dtype 覆盖门改**用真实用例的 `inputs[0].dtype`** 算覆盖（不信 caseset 自报 `dtype_tested`，防「跑子集报全」）；
oracle_source 一致门校 `evidence.oracle_source == 映射(golden_source)`、**彻底**堵住假常量。**但 dtype 侧只半闭合**：
「任务书要求」这一侧仍由**可缺省的** caseset `dtype_required` 代传，`needs_user`/legacy/字段缺失时门不 BLOCK，
且 `dtype_required` 的任务书权威来源（原 TBE 信息库）未接通——即「任务书要求↔实际测量」尚未真正锚到任务书，
仅在 `dtype_required` 为已知列表时成立。oracle_source 侧则从「校 caseset↔evidence」彻底改为「校实际生效对象」。

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

**Sources.** [[session 64604f71-dd13-4256-9a74-072fec018b48 · 2026-07-09]], [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]], [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：Q7/Q9 两门照本纪律落地）
