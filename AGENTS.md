# AGENTS.md — OpRunway 仓根入口（非 Claude 运行时先读这份）

**全程中文。** 本文件是 **Codex 等以 `AGENTS.md` 为约定入口的运行时**进本仓的第一站：说清「这仓是什么、怎么把路径接上、哪一层可以直接用、纪律是什么」。
Claude Code 读的是仓根 `CLAUDE.md`（**用户的仓规文件，勿改**）——本文件是**新增补充、不替代它**；两者冲突时以 `CLAUDE.md` 为准。

---

## 1 · 这个仓是什么

**OpRunway = NPU（昇腾）算子「验收（acceptance）」工作区**：输入 =「**算子任务书 + PR 链接**」，输出 = **机器可校验的验收裁决 + 中文验收报告**。三段式流水线：

```
任务书 + PR ──①用例生成(ST)──▶ 测试用例集 ──②NPU 跑测──▶ NPU 精度对比 + 性能数据
                                  │                                   │
                                  └──③ 同一份用例喂 GPU 标杆 ──────────┘ ──▶ NPU↔GPU 性能对比报告
```

用例集是脊柱：Task 2（NPU 精度 + 性能）和 Task 3（性能对比）都消费同一份。

---

## 2 · 🔴 先把插件根变量接上（否则所有路径都断）

制品里的脚本路径统一写成插件根变量。**Claude 下** harness 自动提供 `CLAUDE_PLUGIN_ROOT`；**非 Claude 运行时（Codex 等）两个变量都没有**，必须先显式 export：

```bash
export OPRUNWAY_PLUGIN_ROOT="$(git rev-parse --show-toplevel)/plugin"   # 或 <仓根>/plugin 绝对路径
```

- **中立主变量 = `OPRUNWAY_PLUGIN_ROOT`**；`CLAUDE_PLUGIN_ROOT` 只是 Claude 分支的**兼容别名**（`plugin/init.sh:55` 就是这个口径）。
- 制品里出现在**可执行命令**中的写法是自兜底的 **`${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}`** —— Claude 下走别名照常活，Codex 下只要 export 了主变量就活，**不依赖谁记得先 export**。散文里则直接写 `${OPRUNWAY_PLUGIN_ROOT}`。
- 私有主机名 / 真实远端路径一律走 `OPRUNWAY_*` 环境变量传入，**不得进入 Git tracked 文件**；用户要求的机器本地值只允许写入被 `.gitignore` 忽略的 `.oprunway/real-machine.env`。token / 密码 / 私钥连该本地文件也不得写。仓里的 tracked 默认值一律是占位。

---

## 3 · 哪一层是工具中立的、可以直接用

`plugin/acc-common/` 的**确定性 Python 脚本**（Layer 1）与 JSON 契约（Layer 0）**不依赖任何 CLI / agent 框架**，换运行时零改；只有 Layer 2 的薄壳（`plugin/agents/`、`plugin/skills/`、`plugin/commands/`）是各家运行时各自注册的。

**判定的脑子在脚本里，不在 agent**（ADR 0007 `canon/decisions/0007-deterministic-validator.md`，canonical）：
`validator.py`（精度）+ `perf_compare.py`（性能）+ `validate_acceptance_state.py`（三级完整性门）→ 门控后由 `run_workflow.py` 写 `acceptance.json`。
**任何 agent / 编排层都不自行判 pass/fail，只逐字引用这些产物的裁决并标来源。**

主入口（真实可跑）：

```bash
python3 "${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}/acc-common/run_workflow.py" \
        <spec.json> --mode <mode> --out <报告目录>
```

其余常用确定性脚本：`fetch_source.py`（任务书/PR → 中立 JSON）、`gen_cases.py <spec> --dry-run`（用例计划契约自检，plan-only、不产裁决）、`check_golden.py`（golden 来源契约）、`validate_acceptance_state.py`（复核门）。

---

## 4 · `--mode` 怎么定：据 `spec.runner_form` 派生，不由人挑

`spec.runner_form` 是**唯一真源**，受控词表 `{cpp（缺省）, aclnn_py}`：

| `spec.runner_form` | `--mode` | 形态 | 性能基线对照物 |
|---|---|---|---|
| `cpp`（或未声明） | `new_example` | 编译 per-op C++ runner 跑 | **同法测的内置 TBE** |
| `aclnn_py` | `aclnn_py` | 无 per-op runner 源，op 工程即 DUT，通用 ctypes 两段式调 `.so` | **同机 `torch_npu` 跑同一份 torch reference** |

- **两条都是真机验收通路、都产验收裁决**（`run_workflow.py:37` `_REAL_MACHINE_MODES = {"new_example", "aclnn_py"}`）。别写成「`new_example` 是唯一产裁决的路」——median+PR6429 的最新真机 60/60 精度 PASS 正是 `aclnn_py` 跑出来的。
- `mock` / `catlass` / `catlass_mock` **派生不出来**，只能显式指定（局部自检 / catlass 通路的正当逃生口）。
- ⚠ `run_workflow.py` 的 argparse 默认值是 `new_example`，**派生是编排层的职责**。走错通路的代价是实打实的：① `cpp` 路真机 dtype 白名单只有 fp32/fp16/bf16（`repo_adapter.py:19` 的 `_NP`；int32 等落 `DEFERRED_NP_BY_FORM["cpp"]`，生成期造得出用例、**真机跑到 fail-closed**）→ 覆盖缺一块；② 两条路的性能基线**不是同一个对照物**，「任务书对标 torch」场景走 `new_example` 拿到的不是任务书要的那个比较。

---

## 5 · 最高纪律（摘自仓根 `CLAUDE.md`，逐条都咬人）

1. **🔴 泛化优先（律令 #0）**：本项目是**通用**算子验收工具。代码里**绝不出现按算子名的分支**（`if op == "<名>"`）、绝不为某个算子裁专属逻辑。per-op 的 spec / golden / gap 是**通用 schema 消费的数据**（合规）；手写 per-op runner、为某算子改工具代码 = 违规。域外形态（非标准 aclnn 两段式 / 有状态 / opaque descriptor …）一律 **fail-closed 标「不支持的接口能力」**，不硬塞。
2. **🔴 一切 compute 在远程 NPU 容器里跑、本地零 compute**：build / 跑测 / 验收 / pytest 全在远程昇腾机的容器内做；本地只做「编辑源码 + git + 知识捕获」。远程机器名与路径经 `OPRUNWAY_*` 传入，不写进仓。
3. **零硬编码**：仓名 / 路径 / SOC / 目标算子 / 精度阈值统统不写死，运行时探测或直接问用户；产物只落用户 CWD 的 `reports/`，不碰 `~/.config`、不改 shell rc。机器专属连接元数据的唯一例外是用户要求的仓内忽略文件 `.oprunway/real-machine.env`，不得被 Git 跟踪。
4. **副作用先确认**：clone / checkout / build / 真机跑测 / 删除覆盖 / 对外发布，先列计划、点头再做。
5. **不 push、不 merge，除非用户明示**；对非本用户仓的 issue/PR/comment 也须先经同意。
6. **对外产出绝不带任何 AI 署名**：commit / PR body **不得**追加 `Co-Authored-By: Claude …`、`Claude-Session: …`、`🤖 Generated with …` 之类——**不因工具或提交模板的默认值而追加**；人类署名一律 `lys` / `lllyys`。
7. **不凭空捏造**：报告里的数字与错误必须来自真实日志 / 真实采集，推断项显式标 `(推断)`；`needs_review` 不当 pass，「PR 有测试」≠「验收过了」，**验收权威只认任务书**。
8. **durable 知识走 `canon/` 的写门**：capture → compile → review，**绝不手改 cabinet 页、绝不自行设 `canonical`**；读时按 trust tier（只有 `canonical` 当事实）。见 `BUREAU.md`。

---

## 6 · 往哪儿深挖

| 要什么 | 看哪 |
|---|---|
| **详细编排**（CP-A..E 状态机、硬门、subagent 契约） | `plugin/AGENTS.md`（插件级注册清单）+ `plugin/skills/acceptance-workflow/SKILL.md`（权威状态机） |
| **设计与数据契约** | `doc/oprunway-design.md` |
| **当前状态与 TODO / 交接** | `doc/oprunway-session-handoff-2026-07-26.md`、`doc/oprunway-todo.md`、`doc/oprunway-changes-brief.md`（改动流水） |
| **真机环境入口** | `doc/oprunway-real-machine-environment.md`（能力/版本/探测纪律）+ 本地忽略文件 `.oprunway/real-machine.env`（实际 alias/container/path） |
| **已定决策（ADR）** | `canon/decisions/`（读前看 `status:` trust tier） |
| **人读蓝图 / dispatch 模板 / 历史算子案例** | `plugin/workflows/`（材料仓，非权威；冲突以 `acceptance-workflow` skill 为准） |

---

## 7 · 诚实的能力边界（别把「代码接通」说成「验证过」）

- **经真 NPU 验收裁决坐实的算子**：IsClose / Sign（A3）· **Median（A3，PR6429，精度 60/60 PASS）** · Elu / Silu（A5-950，18/18 非空例）。
- ⚠ **Median 的性能维仍 BLOCKED，但已不是“零数据”**：custom 50/50、`torch_npu` baseline 48/50 产出同机同口径 kernel-only 耗时；48 对进入评分，35 对达到 `ratio >= 1.0`，另 2 个 BF16、`dim=1` case 的 baseline 报 161002、custom 成功，按 baseline limitation 挂起。不得把 blocked case 归因 DUT，也不得把 35/48 写成整体验收通过。
- **任务书性能标杆仍有未消解出入**：任务书点名 `aclnnMedian` / `aclnnMedianDim` 的小算子拼接版本，当前 spec 使用 `torch_npu torch.median`。二者等价性尚无可复核证据；解决前，现有 ratio 不得用于宣称满足任务书性能条款。见 `median.spec.json.task_pr_gaps` 与 `doc/oprunway-todo.md`。
- **`aclnn_py` 的 perf 通路已真机产出有效耗时**：collector 已对齐 `msprof CLI + MSTX + task_time CSV`，custom 与 baseline 使用同一 kernel-only scope；仍须逐任务核对 baseline 来源，**“通路有数据”不等于“任务书性能条款已通过”**。
- **mock（含 `catlass_mock`）通路不产验收裁决**：C5（2026-07-22）起它**物理上不产** `acceptance.json` / `verdict.json`，只产带 `evidence_grade="development"` + NON-ACCEPTANCE 戳的 `dev_run_summary.json` / `dev_precision_check.json`。**不是「产了但不算数」，是压根不产。**
- **runner 生成的闭环范围**：ops-<族> 仓 · aclnn 两段式 · opp 安装型；catlass / 非 aclnn 接口 / 双实现属待扩，命中即 `BLOCKED`、不硬塞。
- **外部 GPU 标杆**：consumer 侧已接入 pipeline，**真实数据仍待外部方提供**；缺数据时移植类算子一律 `BLOCKED_WAIT_GPU_BENCHMARK`（正规挂起、非 fail、绝不显 PASS）。
