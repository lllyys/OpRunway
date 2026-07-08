# OpRunway agent+skill 体系落地设计（对齐 cannbot，抛方案·未实现）

> 目的：把当前 OpRunway 体系（对话式 op-acceptance agent + acc-* skills + acc-common 脚本）**对齐 cannbot 验证过的成熟范式**——三层 `Plugin→Agent→Skill` + 机器门状态机 + 跨 CLI 中立 `AGENTS.md`。
> **这是设计，先抛方案经同意才落地**（CLAUDE.md #1）。只借 cannbot 的**架构骨架 + 门控机制 + 方法论 skill 口径**，不引它的「算子开发/生成」链路（我们是**验收**、开发≠评测）。

## 0. 原则（OpRunway 专属，凌驾于照搬之上）

- **验收 ≠ 开发**：只借 cannbot 方法论（`ops-precision-standard` 精度、`ops-profiling` 性能、`ascendc-st-design` ST 用例分类），**绝不把 developer/上库 agent 当验收依据**。
- **门以机读证据为准，md 只是展示物**：pass/fail 只来自**结构化机读证据** `evidence.json`（精度=真 NPU out vs golden 的 metric；性能=**`msprof op` kernel-only `Task Duration(us)`**，统一 `timing_scope`）；raw log 只存 hash/path、不作判定。⚠ catlass `Compare success.` 只是仓内 smoke 信号、**非**任务书验收；`msTuner` 是**调优**工具、**非**默认验收性能源（默认走 `msprof op`）。推断项标 `(推断)`。
- **单一真值源**：Task1 用例集是脊柱，Task2/3 都消费它；阈值从任务书 + 方法论 skill 派生，**零硬编码、不烤进 validator**。
- **私有基建走 `OPRUNWAY_*` 环境变量**、不写进仓；产物只落 `reports/`；副作用先确认。
- **Codex audit-fix 双门自设**（cannbot 用同 runtime reviewer，无 `codex exec` 双门先例——我们照 CLAUDE.md #5 走 `codex exec`）。
- **对话式**：用户只对话，脚本是 agent 内部实现、不暴露（已定）。

## 1. 三层结构（Plugin→Agent→Skill，映射 OpRunway）

| 层 | cannbot | OpRunway 落法 |
|---|---|---|
| **Plugin** | 编排入口包 + `AGENTS.md` 声明编排 | `plugin/`（oprunway）：`.claude-plugin/plugin.json`（Claude 原生）+ `AGENTS.md`（跨 CLI 中立事实源） |
| **Agent** | primary orchestrator + subagents（architect/developer/reviewer/tester）| **primary `op-acceptance`（只调度+状态机+门禁）** + subagents：`acc-spec-extractor` / `acc-runner-dev` / `acc-verify-rootcause`（mode 驱动、单轮、禁内部循环）|
| **Skill** | 原子能力（`ops/ascendc-*`）+ workflow=skill | 原子 skill：`acc-spec` / `acc-runner` / `acc-casegen` / `acc-precision` / `acc-perf` / `acc-rootcause`；**workflow skill `acceptance-workflow`（承载 CP 状态机 + 机器门）** |

**workflow 落成一个 skill**：`skills/acceptance-workflow/SKILL.md` 承载三段式 CP 门状态机；primary agent「首响应先加载它、禁裸调 subagent」。（注：cannbot 的 ops-registry-invoke 是插件根 `workflow/SKILL.md` 再 symlink 到 skills——**这不是 cannbot 全局规则**，别的插件用复数 `workflows/` 做材料仓；OpRunway 直接放 `skills/acceptance-workflow/`。）**材料（蓝图/prompt/案例）放复数 `workflows/` 资料仓、不套 SKILL.md**。

## 2. 目录约定（对齐 cannbot；★=新增/改造）

```
plugin/
├── .claude-plugin/plugin.json    ★    Claude 原生清单（现只 name/version/desc；补 agents/dependencies）
├── AGENTS.md                     ★    中立编排清单（跨CLI单一事实源；编排+依赖+一句硬门禁；生成 CLAUDE.md/plugin.json）
├── agents/
│   ├── op-acceptance.md          ★    已有胖 agent → P1 改薄为 primary orchestrator（状态机+门+调度）
│   ├── acc-spec-extractor.md     ★    subagent：任务书+PR→spec（单轮/mode）
│   ├── acc-runner-dev.md         ★    subagent：据 example 生成+验证 runner（单轮/mode）
│   └── acc-verify-rootcause.md   ★    subagent：跑测+FAIL 解耦 root-cause（单轮/mode）
├── skills/
│   ├── acceptance-workflow/      ★    workflow=skill：SKILL.md(CP 状态机) + resources/validate_acceptance_state.py(机器门)
│   ├── acc-spec/  acc-runner/         已有（拆细后）
│   ├── acc-casegen/ acc-precision/ acc-perf/ acc-rootcause/  ★  原子 skill（从现有拆）
├── workflows/                    ★    材料仓（非skill）：
│   ├── development-guide.md            六步验收蓝图（各阶段+CP 门）
│   ├── task-prompts.md                 subagent 分阶段 dispatch prompt 模板库
│   └── archive_ops/                    已验证算子案例（spec+runner 参照，生成新算子时锚定）
├── acc-common/                        Layer 1 确定性脚本（工具中立核心，agent 内部调、不暴露）
│   └── (gen_cases/repo_adapter/validator/perf_compare/fetch_source/run_workflow).py
└── init.sh                       ★    安装期扇出到各 CLI（Claude→CLAUDE.md，其余→AGENTS.md；规划）
```

制品状态机对齐 `reports/<repo>/<op>/<pr>/{cases,npu,perf-compare}/`——上游产物即下游输入。

## 3. 关键改造 · 机器可校验门（补 codex 揪的「验证-才-信是纪律非门」）

**位置（P0 不依赖 P1 的 workflow skill）**：`acc-common/validate_acceptance_state.py`（stdlib）；P1 再由 `acceptance-workflow` skill 调用。

- `--stage task1|task2|task3` 三级门；只读 **`evidence.json` 等结构化机读证据**（不认 md/LOG 文字）：
  - **task1**：用例集自洽——`Task1 用例id == NPU 实跑id == GPU 标杆输入id`，每 `(dtype,shape)` 计数对齐任务书目标（**专防「跑子集报 100%」**）。
  - **task2**：精度门——真 NPU out vs golden，metric/阈值取自 spec（spec/caseset/evidence 三处一致）。
  - **task3**：性能门——`msprof op` kernel-only us、双边同 `timing_scope`、ratio ≥ target。
- 打印 `STATUS: PASSED/FAILED`(exit 0/1) + 累积 error（非 fail-fast）。
- **调用点是硬门的关键**（只打印 STATUS 不算门）：`op-acceptance` / `AGENTS.md` 在**出报告前强制跑对应 stage gate**，`FAILED` → **不推进下一 Task、不生成裁决**、显式暴露。配 **fixtures + 单测**证明门能挡住「跑子集报 100%」。
- 证据契约按被测类型原生（ops-math aclnn / catlass）、路径 probe 或 AskUser、零硬编码。

> 这**拟**把「runner 验证-才-信」从纪律**变成代码硬门**（sidecar + 调用点落地后），是 cannbot `validate_workflow_state.py` 的 OpRunway 版（借架构、不借 ACLNN 专属 schema）。

## 4. 跨 CLI 统一形态

**单一 canonical 源 = `AGENTS.md` frontmatter（编排+依赖+门禁）；`CLAUDE.md` 与 `plugin.json` 从它生成**（配一个 check 脚本验同步，杜绝双写漂移）。发布走 `init.sh` 式安装器：`tool` 参数决定落地——Claude→`CLAUDE.md` + `.claude/{skills,agents}/`，其余(opencode/trae/cursor/copilot)→`AGENTS.md` + 各自目录，skills/agents symlink 发现。**Codex 不设专用 init 分支，但项目根 `AGENTS.md` 正是 Codex 原生读取的文件——免费搭车**（要 Codex 的 skills/agents discovery 再另列 OpRunway 扩展）。
⚠ 避开 cannbot 的坑：① 单一源生成、别两处手抄；② 不照搬 `external_directory: allow`；③ **不 `sed` 私有路径进制品**——用 **symlink 保持相对目录拓扑** 或 `OPRUNWAY_PLUGIN_ROOT`/manifest 定位（机器门也读该变量），私有主机/远端路径走 `OPRUNWAY_*` 不入仓。

## 5. 分期落地（建议）

- **P0（补最大短板：机器门 + 跨 CLI 定型；非「一键」，拆 5 子任务）**：① **证据契约最小版**（`evidence.json` 精度/性能字段 + 用例 id + `timing_scope`）；② `acc-common/validate_acceptance_state.py`（三级门）；③ **调用集成**（op-acceptance 出报告前强制跑门、`FAILED` 不出裁决）；④ **fixtures + 单测**（证明门挡住「跑子集报 100%」）；⑤ 加 `AGENTS.md`（单一源 + 生成 `CLAUDE.md`/`plugin.json` + sync check）。
- **P1**：primary orchestrator + 3 subagent（mode 驱动）+ `acceptance-workflow` skill（CP 状态机、调用 P0 的门）；op-acceptance 从「胖 agent」变「薄编排 + 专职 subagent」。
- **P2**：acc-* 拆成原子 skill；建 `workflows/` 材料仓（蓝图 + task-prompts + archive_ops）；`init.sh` 扇出。
- **P3**：catlass 验收路线 **adapter 子任务清单**——① arch 运行时探测（`environment.json`/AskUser，禁硬编码 3510/2201）；② example/harness 选择 + CMake arch 注入(`-DCATLASS_ARCH`)；③ `gen_data/golden/verify_result` 三件套数据流；④ **raw log → `evidence.json` parser**；⑤ msprof kernel-only 解析；⑥ GPU/baseline schema 对齐。参考 canon `catlass-to-aclnn-bridge`，不整包引 catlass-op-generator。

## 6. 现状映射（什么已有/改/新增）

| 现状 | 处置 |
|---|---|
| `op-acceptance` 胖 agent | 改薄 → primary orchestrator（P1）|
| `acc-spec` / `acc-runner` skill | 保留、拆细为原子（P2）|
| `acc-common/*.py` | 保留（工具中立核心，位置不变）|
| 「验证-才-信」纪律 | → `validate_acceptance_state.py` 硬门（P0）|
| 跨 CLI（悬案）| → `AGENTS.md` 同源（P0）|
| bridge | 已删（catlass 路线 P3 从 canon 重造）|

> 相关：`oprunway-design.md`（总）、`oprunway-workflow-design.md`（三层契约）、`oprunway-todo.md`（缺口）、canon `oprunway-component-breakdown` / `catlass-to-aclnn-bridge`。
