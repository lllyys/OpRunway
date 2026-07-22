---
name: op-acceptance
description: OpRunway NPU 算子验收编排。输入=算子任务书(md 本地路径或链接)+PR 链接 → 派 subagent 产 spec/runner/跑测，primary 逐字引用 acceptance.json 等确定性产物裁决、不自行判定、不产 NL durable 工件；出中文验收报告。当用户要验收一个 NPU 算子、或给「任务书+PR」要验收结论时用。
mode: primary
skills:
  - acc-casegen
  - acc-perf
  - acc-precision
  - acc-rootcause
  - acc-runner
  - acc-spec
  - acceptance-workflow
agents:
  - op-acceptance
  - acc-spec-extractor
  - acc-runner-dev
  - acc-verify-rootcause
---

# OpRunway 算子验收 — 跨 CLI 编排清单（AGENTS.md）

> 本文件是 OpRunway 验收体系的 **plugin 级注册清单**，并**拟**作为跨 CLI 单一事实源（后者属 proposed·未 settle，
> 见文末「跨 CLI 单一源」）：Claude Code 按约定目录自动发现 `agents/*.md`（**不读本文件**），**Codex 等读本文件**
> （`AGENTS.md` 是 Codex 原生约定，plugin 根搭车）。编排 / 依赖 / 硬门以此为准。
> **脚本是内部实现——用户全程只对话、不碰脚本、不被要求手敲命令**（proposed·未 settle，载重前需核）。

**输入**：算子任务书（md 本地路径 **或** `http(s)` 链接）+ PR 链接。
**产出**（**真机验收通路**）：`reports/<op>/` 下 `correspondence.json` / `caseset.json` / `evidence.json` / `verdict.json` / `baseline.json`（有基线时）/ `perf_report.json` / `acceptance.json` + 中文验收报告。
⚠ **非验收通路（mock / catlass_mock）产的是** `dev_run_summary.json` + `dev_precision_check.json`（带 `evidence_grade=development` + NON-ACCEPTANCE 戳），**物理上不产 `acceptance.json` / `verdict.json`**（C5，2026-07-22）。

## 硬门（最高规则）

出**任何 pass 裁决前**，**必须**先过机器可校验验收门 `acc-common/validate_acceptance_state.py`
（三级 `--stage task1|task2|task3`，读**落盘** `evidence.json` 独立复核：**防跑子集报 100%、防放宽阈值、防混 e2e 墙钟**）。
验收门 `validate_acceptance_state.py` `STATUS: FAILED` → **不出 pass 裁决；仍由 run_workflow 写 `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）**（验收门未过=证据不可信/不完整）。`run_workflow.py` 已内嵌此门（Task1→2→3 **全跑完后统一校门** →
门未过总体 `BLOCKED`；**注：批量驱动、非阶段间实时阻断**）；**「不推进下一 Task」是 agent 编排纪律**。
判定脑子在 `acc-common/validator.py`（ADR 0007）、**不在编排层**；门只管「证据可信完整」，精度/性能 pass-fail 由 `validator`/`perf_compare` 判。

## 编排（CP-A..E · 薄 orchestrator + 3 subagent 状态机）

胖 agent 已改薄为 `mode:primary` 编排器：只做**调度 + 检查点(CP)状态机 + 工件门禁 + 对应校验前置**；
NL 生成 durable 工件（spec / runner）与真机跑测 / 归因**下沉 3 个 `mode:subagent`**。CP 状态机文本承载在
`skills/acceptance-workflow/SKILL.md`（primary 首响应先加载此 skill、禁裸调 subagent）。

### 注册面 vs 调度面（先厘清语义，别混）

- **plugin_agents** = 本 `AGENTS.md` frontmatter `agents:[4]` = `op-acceptance` + 3 subagent，**含 primary 自身** →
  本项目**声明**的 agent 注册清单，**预期**与 Claude Code 自动发现的 `agents/*.md`（按 stem）集合一致。
- **plugin_skills** = 本 frontmatter `skills:[7]` → 本项目声明的 skill 注册清单，**预期**与 `skills/*/SKILL.md` 集合一致。
  ⚠ 这两处 frontmatter **不负责让 Claude Code 暴露组件**（暴露靠约定目录自动发现）；它们是同步门的一侧、供 Codex 等读。
- **child_agents** = `agents/op-acceptance.md` frontmatter `agents:[3]` = `acc-spec-extractor` / `acc-runner-dev` /
  `acc-verify-rootcause`，**不含自己** → primary **可 dispatch 的子 agent**。
- **primary_skills** = `agents/op-acceptance.md` frontmatter `skills:[1]` = `acceptance-workflow` → primary **实际加载**的
  skill（原子 skill 已下沉 subagent，`check_agent_frontmatter.py` 强制恰为此一个）。
- 分**两层**：本清单是 plugin 级**注册面**（含 primary 自身、含全部 skill）；`agents/op-acceptance.md` 里的是 primary 的
  **调度面 / 实际加载**。两层数目本就不等，别互相「对齐」。

> ⚠ **`.claude-plugin/plugin.json` 不要声明 `agents`**。在实测的 Claude Code `2.1.206` 上，写成 `["./agents/x.md"]`
> 会被**静默忽略**——插件照常加载、`claude plugin validate` 照常 ✔、8 个 skill 照常在，但 `Agents (0)`、4 个 agent 全不
> 生效，`/op-acceptance` 调不起 primary。写成 `["agents/x.md"]`（去 `./`）或 `"./agents/"`（字符串）则**整个插件加载失败**。
> 已测的四种写法里**只有省略该字段**能得到 `Agents (4)`（靠约定目录 `agents/` 自动发现）——当前唯一实测可用，不等于
> schema 上唯一合法；其它版本未验证。别「好心」把它加回来（`check_manifest_sync.py` 设了反向门）。

### 检查点（CP，对话暂停点 + 工件门；缺 NPU/VPN 到 mock 为止）

- **CP-A 前置**（primary 亲自）：取材 `fetch_source.py` → **任务书↔PR 对应校验**（落 `correspondence.json`；proposed·未 settle，载重前需核）→
  环境/模式确认（mock vs new_example、NPU/VPN），`AskUserQuestion` 由 primary 做。
  校验靠 **改动落点目录 `pr_facts.target_dir`（机器可比）** + **issue/追踪号（NL 读 `task_doc`/PR title，非算子名字面匹配）** + **用户确认**。
  `correspondence.json` 的 `status ∈ {confirmed, mismatch, empty_task, needs_user_confirmation}`：
  `mismatch` / `empty_task` → 出**程序结论（非 pass/fail）**并停跑；`needs_user_confirmation` → primary **摆证据、由用户拍板**（不自动 judge 空任务）。
- **CP-B Task1 用例**：dispatch `acc-spec-extractor:extract_spec` → `spec` + `task_pr_gaps`；primary inline
  `gen_cases.py <spec> --dry-run`（plan-only 契约自检，**不产任何裁决**）——**CP-B 只关注 task1 用例计划自洽**；
  dry-run 报错或覆盖账本异常 → dispatch `refine_spec`。⚠ C5（2026-07-22）起 **mock 通路物理上不产 `acceptance.json`**，
  改产 `dev_run_summary.json`；本文件别处提到的「门控后写 acceptance.json」**只适用真机通路**。
- **CP-C runner**（真机路径、需 NPU）：dispatch `acc-runner-dev:gen_runner`（**先过 scope gate**）→ `verify_runner`；
  这是 acc-runner-dev 的 **runner 自检证据满足/不满足** 纪律（当前**非代码强制 sidecar 硬门、待补**），未满足则停在 CP-C、不上真机；acceptance 裁决只逐字引用 `validator.py` / `perf_compare.py` / `validate_acceptance_state.py` 产物（ADR 0007）。
- **CP-D 真机跑测（一次原子）**：dispatch `acc-verify-rootcause:run_npu` → `run_workflow.py --mode new_example`
  （**Task2 精度 + Task3 性能 + 三级门 task1/2/3 一次成**）→ `evidence.json`/`verdict.json`/`baseline.json`（有基线时）/`perf_report.json`/`acceptance.json`；
  FAIL → dispatch `rootcause`（先解耦「被测算子 vs harness」再归因）。
- **CP-E 报告**（primary）：**逐字引用** `acceptance.json`/`verdict.json`/`perf_report.json` 裁决 + `task_pr_gaps` + 各维度；
  `needs_review` 不当 pass；门 `FAILED` → `BLOCKED`。

### subagent 与 dispatch_mode 表

| subagent | mode | skill | dispatch_mode | 职责（单轮、禁内部循环、不自行判定、只回结构化摘要） |
|---|---|---|---|---|
| `acc-spec-extractor` | subagent | `acc-spec` | `extract_spec` / `refine_spec` | `extract_spec`：`task_doc`+`pr_facts` → `<op>.spec.json` + `task_pr_gaps`（多算子多 spec）；`refine_spec`：mock 门失败据 gate error 修 spec |
| `acc-runner-dev` | subagent | `acc-runner` | `gen_runner` / `verify_runner` | `gen_runner`：据 spec + 算子自带 example 生成 `oprunway_<op>_runner.cpp` + 选构建路径（**锚定 example 不猜**，含 **scope gate**：仅 `experimental/math/<op>` aclnn 闭环，catlass/legacy/非 math 族/未支持 dtype → `BLOCKED`/转 P3、不硬塞）；`verify_runner`：验证-才-信，手算 golden 小用例逐元素比，未过不上真机 |
| `acc-verify-rootcause` | subagent | （无 atomic skill） | `run_npu` / `rootcause` | `run_npu`：真机 `run_workflow.py --mode new_example`，一次原子跑 Task2+3+三级门；`rootcause`：任何 FAIL 先「被测物自 build + 声明 dtype + 手算 golden」独立复现，解耦 op vs harness 再归因（不外发、不替 PR 作者修到底） |

### 编排硬约束（措辞与 3 subagent / SKILL 一致）

- **判定唯一归确定性脚本链**：`validator.py`（精度）+ `perf_compare.py`（性能）+ `validate_acceptance_state.py`
  （三级完整性门）→ 门控后写 `acceptance.json`。**编排层与 subagent 不自行判 pass/fail，只逐字引用确定性产物的裁决并标来源**
  （ADR 0007）——不是「绝不提 pass/fail」。
- **subagent**：**单轮、禁内部循环、禁跨阶段、只回结构化摘要给 orchestrator、不自行判定**。
- **primary**：**可直接跑「无 NL 生成、无判定」的确定性脚本**（`fetch_source` / `run_workflow --mode mock` /
  `validate_acceptance_state` / `check_manifest_sync`）；**不做 NL 生成 durable 工件**（spec/runner 派 subagent）；
  **不自行判 pass/fail**；**首响应先加载 `acceptance-workflow` skill、禁裸调 subagent**。
- **三级门是 `run_workflow.py` 内部**（一次性串 Task1→2→3、末尾统一校门，是**批量驱动、非阶段间实时阻断**），
  **不是** orchestrator 分阶段单独调度；门 `FAILED` → 总体 `BLOCKED`、不出 pass 裁决。「不推进下一 Task/停在当前阶段」是 **agent 编排纪律**。
- **Task3 blocked 路由**：`BLOCKED_WAIT_GPU_BENCHMARK`（缺外部 GPU 标杆）/ `BLOCKED_INCOMPARABLE_TIMING_SCOPE`（口径不可比）；
  基线来源按任务书参考源（`spec.perf.baseline` 驱动，当前 aclnn 重写类 isclose/sign/equal/neg = `tbe`，catlass matmul 属对标类·synthetic·未定基线；proposed·未 settle，载重前需核）；GPU external 对比层 **consumer 侧已接入 pipeline**（`run_workflow --gpu-baseline` → `gpu_baseline` 校验 → `perf_compare` 对比），但**真实 GPU 标杆数据待外部提供**，缺数据即走 `BLOCKED_WAIT_GPU_BENCHMARK`。

## 约束

- **验收权威 = 任务书**；「PR 有测试」≠「验收过了」。
- 缺 NPU/VPN → 到 mock 为止，明确告知「真机跑测待开 VPN」、不假装真机。
- 私有主机名 / 远端路径走 `OPRUNWAY_*` 环境变量、**不入仓**；产物只落 `reports/`；**副作用先确认**（对外单一对话入口、脚本幕后）。
- **跨 CLI 单一源**（proposed·未 settle，载重前需核）：本 `AGENTS.md` 为事实源，`CLAUDE.md` 与之**手工同步**，由
  `acc-common/check_manifest_sync.py` 做**机器校验漂移门**——**与文件系统两方集合比对**：本 frontmatter `agents` ↔
  `agents/*.md`、本 frontmatter `skills` ↔ `skills/*/SKILL.md`（多登记 / 漏登记都报 DRIFT）；外加**硬拒**
  `.claude-plugin/plugin.json` 声明 `agents` 字段。`plugin.json` **不参与 agents 同步**（见上文 ⚠：它一声明反而全不加载）。
  **真正的「单一源生成器」是 P2 的 `init.sh` 扇出**，非「派生」。
  换运行时只换注册薄壳，`acc-common/` 脚本 + skills `references/` 不动。
