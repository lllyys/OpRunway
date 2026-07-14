# 交接文档 · 2026-07-13（session 0513d745 → 新 session）

> 起点：干净用户测 IsClose 插件 + 用户 11 问深挖，挖出多条设计/正确性问题。
> 本文是给**新 session** 的接力棒——对话上下文已丢，只有本文 + 已 commit + 设计 md 留得住。
> **先读**：`doc/oprunway-usertest-findings-design.md`（所有问题的定稿），再读本文的「在飞/待决/未 commit」。

---

## 一、git 状态（换 session 前）

- **当前分支** `fix/q2q3-transport`（HEAD `0c182ea`）。
- **PR #6 OPEN**（https://github.com/lllyys/OpRunway/pull/6）：含 marketplace `57eedbb` + Q2/Q3 `0c182ea` 两 commit。**用户自己 merge**，不要替他合。merge 后同步 GitCode 镜像（`git push gitcode main`）。
- **main 本地 = `57eedbb`**（marketplace，已 commit）；**origin/main = `4cef15f`**（marketplace 未推，随 PR #6 进）。
- **未 commit（跟着分支走、没丢）**：
  - `doc/oprunway-usertest-findings-design.md`（★ 所有问题定稿，未 commit）
  - `canon/logbook/2026/07/0513d745-*.md`（本会话 minute，多 checkpoint，含「绝不信 PR」capture）
  - 3 个 stub `26ae30a1 / 30c62b6e / ee2d28fe`（**用户明示不带**，永远别 commit）
- **规则**：commit **绝不带** `Co-Authored-By: Claude` / `Claude-Session:` trailer（用户「never」，已入 CLAUDE.md + memory）。main 是保护分支→走 PR。

## 二、已完成（本会话）

1. **产物落点改造**（已入 main `a7c8417`/PR #5）：runner/spec 落用户 CWD `.oprunway/ops/<op>/`，不写插件目录；样例 fallback 出声 + `runner_source` provenance。
2. **marketplace.json**（`57eedbb`，PR #6）：补齐 `/plugin install` 分发。
3. **Q2/Q3 传输层**（`0c182ea`，PR #6）：机器连接零硬编码 + local/remote 传输抽象 + 安全守卫。449 测绿、过 codex 门。
4. **干净用户测试**：`OpRunway-usertest/`（隔离环境，README 在内）。IsClose 真 A3 端到端跑通、裁决 PASSED_WITH_RISK、隔离干净、agent 诚实。
5. **bureau capture**：本会话 minute 记了「绝不信 PR」原则等，**未 compile→review**（低权威）。

## 三、★ 待做（按优先级，都在设计 md 里有详规）

### P0 · 正确性

- **「验收标准永远来自任务书，PR 是被测物，绝不信 PR」**（用户 2026-07-13 明示，最高原则）。
  - 审计（workflow `wpbfnc2rp`）已定位**唯一硬违反 V1**：dtype 全集来源 = PR op_def（`taskdoc-to-spec.md:126` + `SKILL.md:21` + `:122` 兜底次序把 PR 排在独立源之上）。危害：会遮住 canon ADR 要抓的 Fmod 式「PR 缩 dtype」缺口。
  - **纠正**：dtype 全集改 `任务书显式 > 原 TBE 算子信息库（opp/built-in/.../tbe/config/*.ini，spec reference.path 已指）> 问用户`；op_def **降级为仅对照**（PR 声明<全集→记 gap）。
  - 其余 5 类（精度/性能/shape/golden/硬件）审计判**合法**（PR 建被测物 or 仅对照），别误伤。
  - **落地形态**：给 canon `task-spec-authoritative-over-pr`（proposed）补一节「标准来源路由」+ CLAUDE.md 加一行最高优先级红线。**未做。**
- **golden 来源（Q8/Q9）** — 用户「不建议 agent 自己写算子代码验证精度」。现状：`gen_cases.GOLDEN` 是插件写死的 numpy；runner 自检 golden 也 agent 手算。**golden 比 runner 更深的洞（没有"给 golden 的 golden"）**。
  - **待用户定来源**（问了没发出去）：① AscendOpTest expect_func 为主（canon 指的路）② torch 等价为主 ③ 分级 + 强制交叉核。**⚠ 坑**：内置 TBE 当 golden 只在「PR 忠实重写、非修 bug」时成立。
- **Q7 dtype fail-closed 三落点**：`select_standard` 白名单 / spec 记 `dtype_required`(任务书源) vs `dtype_tested` / 门校 caseset dtype 覆盖。**代码未落地。** 与 V1 互补（V1 管来源、Q7 管跑不到就 fail）。

### P1 · 可用性 / 扩展

- **Q4 被测代码 = PR head 硬门**（已讨论**定稿**，见设计 md §B）：PR 恒未合并→取当前 head 最新 commit→钉 sha→报告记录。三处一致门（取材 sha == build sha == head_sha，不一致 BLOCKED）。D4 = clone-to-scratch（合并 Q3 按需 clone）。**⚠ 落地前必验**：open+fork 的 `?ref=<sha>` API 可解析性（拿真实未合并社区 PR 试）。需新立 ADR。**代码未落地。**
- **Q3 按需 clone**：被测仓不存在→从 PR gitcode URL 浅 clone 到 scratch。与 Q4 D4 合并做。
- **int32 扩展（Q6，用户确认后续做）**：上游 gen_cases/precision_policy 已就绪，只差 `repo_adapter._NP += int32`（:13，先拦者）+ runner.cpp 加 `RunTypedCase<int32_t>(...ACL_INT32...)`。int→EXACT 无阈值难题。**Q2/Q3 锁已解，可直接做。** bf16 押后（阈值 provenance 未 settle、numpy 无 bf16）。

### P2 · 防污染

- **Q1 spec 样例隔离**：`taskdoc-to-spec.md:5` 把真 spec 指给 agent 当"目标 schema"→ agent 产 spec 前读了标准答案（软污染，transcript 坐实）。改：空模板 vs 真样例拆开、样例移出 `acc-common/specs/`、acc-spec 禁读同名 spec、test fixture 用假算子。

### 澄清（非 bug，已答）

- **Q5 runner**：没问题。2 类 dtype 是成文边界；"扩 runner" 徒劳（`_NP`:415 Python 先拦，早于 runner 部署:473）；agent 其实没真扩（识破耦合、荐不扩、交用户）。
- **Q8 baseline_mock**：真机不 mock；那是 CP-B mock 自检阶段。
- **Q11 仿真图分析**：无自动能力，靠人看图。

## 四、bureau 待办

- `bureau:compile` 本会话 minute → cabinet（proposed）；`bureau:review` 人门 promote。
- 积压未 promote：之前几轮的 dossier + 「绝不信 PR」原则（建议并入 `task-spec-authoritative-over-pr`）。

## 五、方法论教训（钉给新 session 的自己）

- **别 grep "OK" 判测试绿**（unittest 输出带 ANSI 色码），用**进程退出码**。本会话反复栽这个。
- **worktree fan-out 收拢后必须亲验合并回归**（各自 worktree 绿 ≠ 合到一起绿——本会话真抓到一个跨-worktree 冲突）。
- **代码门只跑一轮**（audit→fix→verify），剩余 finding 如实列给用户。散文门同理。codex 长任务放后台跑（默认 timeout 2min 太短）。
- **工具调用前别写任何前导字**（本会话反复因一个前缀词毁掉工具调用）。
