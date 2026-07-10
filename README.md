# OpRunway

**NPU 算子「验收（acceptance）」workspace。** 把下面这条三段式验收流水线，做成可复用的 workflow / agents / skills：

```
任务书 + PR ──①用例生成(ST)──▶ 测试用例集 ──②NPU跑测──▶ NPU 精度对比 + 性能数据
                                  │                                   │
                                  └──③ 同一份用例喂 GPU 标杆 ──────────┘
                                          ───▶ NPU↔GPU 性能对比报告
```

- **Task 1 · 用例生成(ST)**：PR + 算子任务书 → 覆盖功能/精度/性能的机读+人读用例集（整条流水线的脊柱）。
- **Task 2 · NPU 跑测**：用同一套用例，在算子工程上跑出 NPU 精度对比 + 性能数据。
- **Task 3 · 性能对比**：同一套用例喂 GPU 标杆 → NPU↔GPU 性能对比报告。

## 现状

**主干施工完毕 + 真机端到端验证通过**，但还不是「能对任意算子一键验收」的成品——见 [`doc/oprunway-todo.md`](doc/oprunway-todo.md)。

- **架构**：三层可移植设计。Layer 0 六份 JSON 契约 · Layer 1 确定性脚本（工具中立的「脑子」）· Layer 2 per-tool 薄壳（编排）。Stage 间只传 JSON。
- **已真机验证**：两个结构不同的算子（IsClose 二元/bool、Sign 一元/数值）在真昇腾 NPU 上跑通且**裁决经核对正确**——精度 = 真 NPU 输出 vs numpy golden，性能 = msprof 真 kernel-only vs 真内置 TBE 基线，总体门同时卡精度+性能。
  ⚠ **Equal 不计入有效结论**：它虽也在真机跑过，但事后确认**任务书↔PR 配错、且该 Equal 社区任务本身从未验收通过**，故其验收裁决已整体作废（见 [`doc/oprunway-changes-brief.md`](doc/oprunway-changes-brief.md) 顶部横幅）。Neg 仅接入 mock 级流水线；catlass（GEMM 系）当前实现为「注入其自带 example 树」的 repo-native harness（对应 canon 的「路线 C」更正仍待 compile→review，非既定 canonical），真机待 950（ascend-a5）+ VPN 验证。
- **裁决可信（确定性 + 对抗加固）**：pass/fail 只出自确定性脚本——`validator.py` 判精度、`perf_compare.py` 判性能，编排层与 subagent **只引用不自判**（ADR 0007）；**三级完整性门不重判 pass/fail**，只校验证据可信完整，门失败映射 `BLOCKED`。并对 evidence↔落盘产物做 sha256 绑定 + 门内重算比对，堵「伪造 metrics / 跑子集报 100% / 放宽阈值 / 混 e2e 墙钟」等假通过；`validator` 保持 stdlib-only。`acc-common` 由 **368 个 unittest 用例**覆盖——含判定链、三级门、适配器与脚本，以及对抗负例（谎报 dtype、伪造 summary、跑性能子集、越界产物路径等）。
- **加一个算子**：对 `experimental/math/<op>` 的 aclnn 两段式算子，agent 可自动产 `spec`（acc-spec）+ `runner`（acc-runner）；**catlass / legacy / 非 math 族 / dtype 超范围会返回 `BLOCKED` 或转 P3，不硬塞**。`gen_cases` 的 golden 仍是一处手工注册（待自动化）；runner 自检目前是**纪律、非代码强制门**。用户侧无感——只需在会话里给任务书 + PR。

## 安装

**前置**：Claude Code `2.1.206`（当前唯一实测版本，其它版本未验证）· `python3` + `numpy`（确定性脚本依赖；仓内暂无依赖声明文件）。

**加载插件**（当前唯一支持的方式）：

```bash
cd /path/to/OpRunway
claude --plugin-dir ./plugin
```

**确认装好了**——组件数必须是 `Skills (8)` + `Agents (4)`：

```bash
claude --plugin-dir ./plugin plugin details oprunway
```

若显示 `Agents (0)`，则 agent 没加载、`/op-acceptance` 调不起 primary（见下方陷阱）。

**开发迭代**：改完插件文件可先试 `/reload-plugins` 热加载（各类组件是否都能可靠热更新，本次未逐类实测；若 `plugin details` 的组件数或定义没更新，重启会话再查）。改动 `AGENTS.md` / `agents/` / `skills/` 后跑一次漂移门：

```bash
python3 plugin/acc-common/check_manifest_sync.py   # 期望 STATUS: SYNCED
```

> ⚠ **陷阱：`.claude-plugin/plugin.json` 不要声明 `agents` 字段。** 在实测的 `2.1.206` 上，写成 `["./agents/x.md"]` 会被
> **静默忽略**——插件照常加载、`claude plugin validate` 照常 ✔、skills 照常在，但 `Agents (0)`、4 个 agent 全不生效；
> 写成 `["agents/x.md"]`（去 `./`）或 `"./agents/"`（字符串）则**整个插件加载失败**。已测的四种写法里，**只有省略该字段**
> 能得到 `Agents (4)`（靠约定目录 `agents/` 自动发现）——这是当前唯一实测可用的写法，不等于 schema 上唯一合法。
> `check_manifest_sync.py` 已设反向门拦这条。

> marketplace 分发（`claude plugin marketplace add` + `plugin install`）尚未提供——仓内还没有 `marketplace.json`。

## 怎么用：在会话里对话（不跑脚本）

装上插件后，**在支持的 agent CLI 会话里，对 `op-acceptance` agent 用自然语言说要验收什么**：

> 帮我验收这个算子：任务书 `<md 路径或链接>`，PR `<链接>`。

agent 内部完成全部六步（取材 → 任务书→spec → 生成并验证 runner → NPU 跑测 → 失败解耦 → 报告）。**你只对话、看进展与最终报告——不需要、也不会被要求跑任何脚本或命令。** 缺东西（任务书 / PR / 是否已开 NPU-VPN / mock 还是真机）它会**用对话问你**。

- 缺 NPU/VPN → 到 mock 自检为止，如实告诉你「真机跑测待开 VPN」，不假装。
- 真机跑测的机器/路径经 `OPRUNWAY_*` 环境变量注入（agent 内部用、不写进仓、不需你手敲）。
- 平台/精度/性能口径由 agent **从算子任务书推**（不猜）。

> 内部实现（确定性脚本 `acc-common/*.py`、spec/runner 生成、判定 `validator.py`）是 agent 幕后的事，**不作为用法暴露给用户**。开发/契约细节见 `doc/oprunway-design.md`。

## 目录

```
plugin/     agent+skill 体系（acc-common 脚本 + skills + agents + commands + .claude-plugin/manifest）
doc/        设计与流水线（oprunway-design.md）、改动简表、TODO
canon/      bureau 决策/ADR（durable 知识，capture→compile→review 三态）
spec/       算子 spec 笔记
repos/      被测/参考算子仓（外部克隆，.gitignore 不入库）
```

## 约定（继承 cann-ops-test）

零硬编码（仓名/路径/SOC/阈值运行时探测或询问）· 零持久化配置（产物落 CWD 下 `reports/`）· 全程中文 · 副作用先确认 · 跑测多层判定 · 不凭空捏造（推断项显式标注）。

> 详细设计见 `doc/oprunway-design.md`；改动流水见 `doc/oprunway-changes-brief.md`；待办见 `doc/oprunway-todo.md`。

---

**仓库**：双镜像 —— GitHub [`lllyys/OpRunway`](https://github.com/lllyys/OpRunway) · GitCode [`brian66237/OpRunway`](https://gitcode.com/brian66237/OpRunway)。插件在 `plugin/`（`.claude-plugin/plugin.json`，名 `oprunway`）；改动经 PR 入库。
