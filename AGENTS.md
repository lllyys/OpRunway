# AGENTS.md — OpRunway 仓根唯一指令入口

**全程中文。** 本文件是 Codex、Claude Code 及其它运行时共同使用的**唯一仓规源**。
仓根 `CLAUDE.md` 只保留 `@AGENTS.md` 路由，不再维护第二份规则；规则、状态和环境入口只在这里更新。

---

## 1 · 这个仓是什么

**OpRunway = NPU（昇腾）算子验收工作区**：输入是“算子任务书 + 被测来源”，输出是机器可校验的验收裁决和中文验收报告。
被测来源有两条平级通路：在线 PR 链接，或本地已 clone 的 checkout（见 §9.3）。

```
任务书 + 被测来源 ──① 用例生成（ST）──▶ 测试用例集 ──② NPU 跑测──▶ NPU 精度 + 性能
                                            │
                                            └──③ 同一份用例喂外部 GPU 标杆──▶ NPU↔GPU 性能报告
```

用例集是整条流水线的脊柱：

- Task 1：从任务书与被测来源（PR 或本地 checkout）生成覆盖功能、精度、性能的用例集；
- Task 2：同一份用例在 NPU 上生成精度证据和性能数据；
- Task 3：消费外部 GPU 数据，按同一 case 身份生成跨设备性能报告。
  ⚠ **Task 3 是按需能力，不是每轮必做**：按 5.10，任务书即使写了「与 GPU 比对」也默认只做
  NPU msprof 实测；**只有用户明确要求做 GPU 对比时**才走 Task 3。

任务书是验收权威；被测来源（PR 或本地 checkout）和 op_def 是被测事实与能力证据，不能反过来覆盖任务书。

---

## 2 · 先接插件根变量

制品里的脚本路径统一使用中立变量。Claude harness 通常只提供 `CLAUDE_PLUGIN_ROOT`；Codex 等运行时须显式设置主变量：

```bash
export OPRUNWAY_PLUGIN_ROOT="$(git rev-parse --show-toplevel)/plugin"
```

- 主变量：`OPRUNWAY_PLUGIN_ROOT`；
- Claude 兼容别名：`CLAUDE_PLUGIN_ROOT`；
- 可执行命令统一写 `${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}`；
- 私有主机名、容器名、真实远端路径不得进入 Git tracked 文件；
- 机器本地值只允许写入被 `.gitignore` 忽略的 `.oprunway/real-machine.env`；
- token、密码、私钥连该本地文件也不得写。

---

## 3 · 三层架构与确定性裁决

`plugin/acc-common/` 的 JSON 契约（Layer 0）和确定性 Python 脚本（Layer 1）不依赖任何 agent/CLI 框架；`plugin/agents/`、`plugin/skills/`、`plugin/commands/` 是 Layer 2 薄壳。

**判定的脑子在脚本里，不在 agent：**

- `validator.py`：精度裁决；
- `perf_compare.py`：性能裁决；
- `validate_acceptance_state.py`：三级证据完整性门；
- `run_workflow.py`：门控后写 `acceptance.json`；并对 `spec.runner_form` 做验收准入（§4，入口 + 出口两道门）。

任何 agent 或编排层都不得自行重判 pass/fail，只能逐字引用确定性产物并标明来源。

主入口：

```bash
python3 "${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}/acc-common/run_workflow.py" \
  <spec.json> --mode <mode> --out <报告目录> \
  --source-facts <CP-A 取材目录>/source_facts.json
```

⚠ **`--source-facts` 在验收通路上必给，缺席直接拒跑**（不是可选参数）：三级门要拿它与 vendor build receipt
的来源锚逐字对账，缺对照物时「收据自称 `gitcode_pr`、事实其实是 `local_snapshot`」这类伪装查不出来。
它拒在 `os.makedirs` / staging / Task1 **之前**，不留半个产物目录。
路径就是 **CP-A 取材那一步 `fetch_source.py --out <取材目录>` 产的那份**（`completeness.status` 须为 `complete`），
**与 `--out <报告目录>` 不是同一个目录**——报告目录里那份是本轮 staging 出来的副本，是产物不是输入。
非验收通路（`mock`、加了 `--allow-experimental-form` 的 `cpp` / `aclnn_py`）**不受此强制**：那条路
物理上不产验收裁决，也没有来源锚要对账。

常用脚本：

- `fetch_source.py`：任务书 + 被测来源（`--pr <PR 链接>` 或 `--pr-snapshot <本地目录> [--target-dir <仓内相对目录>]`）→ 中立事实包；`completeness=blocked` 时非 0 退出（3）；
- `source_provenance.py`：来源形态判别式（声明 `declared_source_form ∈ {git_pr, local_source}` × 实得 `provenance_kind ∈ {gitcode_pr, local_snapshot}`）与 provenance 锚绑定的**读侧唯一入口**；档位路由表、降级授权与「未声明按最严的 `git_pr` 对待」只留这一份实现；
- `vendor_build_receipt.py`：`vendor_build_receipt` 的 schema 与**唯一校验处**（`validate()` / `summarize()`）；adapter、driver、三级门都调它，不许各抄一份；
- `validate_taskdoc_input.py`：任务书输入校验门（18 项 + 交付件清单，抽 spec 之前）；
- `reconcile_deliverables.py`：任务书必选交付件 ↔ PR 实际交付物对账（不做模糊名字匹配，认不出即落缺口）；
- `perf_mode.py`：`spec.perf.mode` 口径（`ratio_gated` / `measure_only`）与 `measure_only_authorization` 的唯一真源（§5.10）；
- `taskdoc_links.py` / `taskdoc_caseset.py`：任务书自带用例集与 golden 的取材、识别与接口映射；
- `gen_cases.py <spec> --dry-run`：plan-only 用例计划自检，不产裁决；
- `check_golden.py`：golden 来源契约；
- `preflight_aclnn.py`：`aclnn_py` 静态接口预检；
- `verify_aclnn_harness.py`：真机 harness 信任门；
- `make_vendor_build_receipt.py`：`cpp_extension` 的 vendor `.so` 出身证明（`vendor_build_receipt`）**产出方**；**真跑 build**、`build.returncode` 是 `subprocess.run` 实测值，**没有「只记录不执行」模式**；`--library` 须被这次 build 改写过，本地来源另核构建前后两次「构建树 ↔ 指纹树」。
  ⚠ 另一条生产路径 `vendor_build_receipt.py` 的 CLI（`snapshot-digest` + `emit`）**自己不执行 build**，
  `emit --returncode` 是**调用方自报值、不构成机器可核证据**；两条路径产的收据在 schema 上目前分不出来，
  引用时必须点名是哪条产的（记账见 §9.4）；
- `validate_preparation_state.py`：非真机复用收据；
- `validate_acceptance_state.py`：验收证据复核门；含 build receipt ↔ source_facts 的来源锚对账（`--source-facts` 可显式指路）。

跑测信号按“退出码 → 强失败信号 → 强成功信号 → 待复核”分层读取；`UNCERTAIN`、`needs_review` 和证据不完整都不能静默升级为 pass。

---

## 4 · `--mode` 只由 `spec.runner_form` 派生

⚠ **当前只有 `cpp_extension` 能产验收裁决。** `cpp` / `aclnn_py` 的 spec 会在入口和出口两处被拦：
不加逃生阀直接报错，加了也只出开发级证据。这是按真机成熟度有意收敛，不是 bug。

`spec.runner_form` 是唯一真源，受控词表为 `{cpp, aclnn_py, cpp_extension}`：

| `runner_form` | `--mode` | 当前能否产验收裁决 | 执行形态 | 默认性能对照物 |
|---|---|---|---|---|
| `cpp` | `new_example` | ❌ 不能。要跑须加 `--allow-experimental-form`，且只产开发级产物 | 编译 per-op C++ runner | 同法测的内置 TBE |
| `aclnn_py` | `aclnn_py` | ❌ 不能。同上 | 通用 ctypes 调标准 aclnn 两段式 `.so` | 逐字按任务书配置；可为同机 `torch_npu`，也可直接调用 CANN 内置 ACLNN |
| `cpp_extension`（**= 缺省，整字段省略即此**） | `cpp_extension` | ✅ 能，当前**唯一**准入形态 | 按官方 `NpuExtension` / `EXEC_NPU_CMD_EXT` 生成独立 `torch.ops` 调用桥；DUT 仍是被测来源构建的 vendor `.so` | 逐字按任务书配置；runner form 不决定 baseline |

准入白名单在代码里就一行：`run_workflow._ACCEPTANCE_RUNNER_FORMS = frozenset({"cpp_extension"})`。

**缺省 = `cpp_extension`，`cpp` 必须显式写。** 缺省值的唯一真源是
`repo_adapter.DEFAULT_RUNNER_FORM`，读侧统一走 `repo_adapter.spec_runner_form(spec)` /
`resolve_runner_form(form_or_None)`——`run_workflow`、`gen_cases`、`cpp_extension_codegen`、
`cpp_extension_adapter.prepare` 等全部同源，**不许再各写一份 `spec.get("runner_form", "cpp")`**。
为什么缺省跟着准入走：缺省若是 `cpp`，「spec 漏写这个字段」派生出的就是 `new_example`，一步撞上准入门；
而漏写时想要的恰恰是当前唯一准入的那条通路。这条不变式在 `run_workflow` 里有断言守着。

⚠ **只有键缺席才吃缺省。** 显式写成 `null` / `""` / `0` 不是「没写」，那是一份写坏的 spec，
照旧在受控词表处 fail-closed 报「不受支持」——读侧一律 `.get(k, DEFAULT)`，**不许用 `or` 兜**，
`or` 会把「写坏的 form」和「没写 form」混为一谈。

⚠ **缺省能兜住，不等于可以省着不写。** 正式验收的 spec **一律显式写 `"runner_form": "cpp_extension"`**：
执行身份要在 spec 里一眼可读、可审，别让下一个人去翻代码才知道这份 spec 按哪种形态跑。
同理，`cpp` / `aclnn_py` 现在**只能显式声明**，省略已不再表达它们。

### 4.1 为什么只准入 `cpp_extension`

理由是**真机成熟度**，不是形态优劣：

| 通路 | 真机走到哪一步 | 结论 |
|---|---|---|
| `cpp_extension` | Median 已跑通完整 torch_parity 矩阵，`gate.passed=true`。⚠ 这里有**两个不可比、来源身份也不同**的 caseset（PR 通路 1152 / 本地通路 1344），并列记在 **§4.5**，引用必须点名 spec 与来源 | 当前唯一有完整矩阵背书的通路 |
| `aclnn_py` | 只有历史 Median 60/60 | 那是**旧 caseset** 的结果；迁到 torch_parity + `cpp_extension` 后必须重跑，旧 PASS 不得沿用 |
| `cpp`（`new_example`） | IsClose、Sign 已坐实 | dtype 闭环只到 fp32/fp16/bf16，覆盖不够 |

**能力表不是准入表，别互相反推。** `repo_adapter.SUPPORTED_NP_BY_FORM` / `DEFERRED_NP_BY_FORM` 里
`aclnn_py`、`cpp` 的条目**照旧保留、本轮没动**：那张表回答“这条通路支持哪些 dtype”，准入白名单回答
“这条通路能不能出裁决”，两个问题不同。`cpp` 的 int 等未支持项仍落 `DEFERRED_NP_BY_FORM`，仍须显式挂账并 fail-closed。

### 4.2 门落在哪两处

| 位置 | 函数 | 说明 |
|---|---|---|
| ① 入口门 | `_resolve_mode` | 正常调用路径在这里被拦。只对真机通路生效；显式 `mock` / `catlass_mock` 逃生口不受影响（它们本来就不产验收产物） |
| ② 出口门 | `_assert_acceptance_form_allowed` | 写 `acceptance.json` / `verdict.json` **之前**再校一次 |

为什么要两道：口径照抄 `repo_adapter` 对 `catlass_mock` 后门的处置——**只拦入口拦不住**。
下一个人看到出口门别当冗余删掉。

### 4.3 逃生阀 `--allow-experimental-form` 怎么用

| 它放行什么 | 它不放行什么 |
|---|---|
| 让 `cpp` / `aclnn_py` 在真机上**跑起来**：修通路、复现问题、局部开发验证 | **产验收裁决**。该路径物理上只产 `dev_run_summary.json` / `dev_precision_check.json`（`evidence_grade="development"` + NON-ACCEPTANCE 标记），不写 `acceptance.json` / `verdict.json` |

所以“加了 `--allow-experimental-form` 跑绿了”不能写成验收通过、不能进验收报告的裁决栏——
这和 §5.8“covered 不等于验收通过”是同一条纪律。

**收敛的代价要认账**：Roll 的 spec 写的是 `aclnn_py`，要继续做正式验收就得迁到 `cpp_extension`，
而后者需要 torch.ops 调用桥 + vendor ELF 构建收据，接入成本明显更高。这是已知账单，不是加个逃生阀就算解决。

### 4.4 其余仍然成立的约定

- `cpp_extension` 不重编 op-plugin，也不把 op-plugin 当 DUT；它只复用官方 C++ Extension 接入机制，
  并须以独立构建收据机校绑定完整 PR head、构建命令和实际加载的 vendor ELF；
- Median + PR6429 的 `cpp_extension` torch-parity 真机精度结果**并列记两个 caseset**（1152 与 1344），
  **不存在单一的「Median 精度基线数字」**；两组数各自的 spec 出处、矩阵构成与引用纪律见 **§4.5**；
- `mock`、`catlass`、`catlass_mock` 不能从 `runner_form` 派生，只能显式用于局部开发或对应通路；
- mock 通路物理上不产 `acceptance.json` 或 `verdict.json`；
- `run_workflow --mode` 的 argparse 默认值是 `None` = **不指定，按 spec 派生**；
  编排层**不得**自己显式传 `new_example`（spec 是 `cpp_extension` 时会当场撞 mode 不匹配门）；
- runner form 只决定执行形态，**不能反推任务书指定的实际性能标杆**；每份任务书的 baseline 仍须单独核实。

### 4.5 Median 精度基线：两个 caseset 并列记，不挑「正统」（2026-08-05 口径定案）

`cpp_extension` 通路的 Median 真机精度结果长期有**两组数**在混引。它们**不是同一个 caseset、彼此不可比**。

| caseset | spec 出处 | 矩阵构成（仓内可证部分） | 例数 | PASS | FAIL | `gate.passed` | 确定性裁决 | 本仓留档的来源锚 |
|---|---|---|---|---|---|---|---|---|
| **①「仅按维」** | 真机当轮 per-run spec，**未入仓** | `8 dtype × 8 rank × 3 规模 × 6 属性`（cannbot 仅按维 overload） | **1152** | 1101 | **51** | `true` | `FAIL(精度)` | PR 通路，本仓一贯记为 `cann/ops-nn` PR6429 |
| **②「按维 + global」** | 仓内 tracked 样例 `plugin/samples/specs/median.spec.json` | `8 dtype × 8 rank × 3 shape × 7 attr`（1 global + 6 by-dim） | **1344** | 1286 | **58** | `true` | `FAIL(精度)` | ⚠ **本仓留档的那次走的是本地来源**，锚是当时那套 `local_checkout.root_digest=c8867ce09f6e…`；**不是 PR 锚**（见 §9.3） |

⚠ **两条 caseset 的来源身份不同，别合并成一句「PR6429 的结果」**：② 本仓留档的那一轮走的是本地来源通路，
它记的 git head **恰好**等于 MR 6429 的 head，但那是**信息字段、不是 provenance 锚**
（`dev-doc/oprunway-local-source-realmachine-validation.md` §4 已明确记这一点）。
更早一轮出现过**同规模**的 1344 例 / 58 fail，但**本仓没有留下那一轮的来源形态与来源锚**——
所以它只能作「同规模的另一次记录」并存，**不得**被当作 ② 的 PR 侧背书。

⚠ **② 那个 `c8867ce09f6e…` 是历史值，现在复算不出来**：它由已被取代的
`oprunway.local_subtree_merkle` v1 算出（§9.3）。当前实现是快照 merkle
（`fetch_source._snapshot_merkle`），帧格式、排除集合、路径基准三处都不同，
**对同一份源码必然算出另一个值**。
引用 ② 时只能把它当作「那一轮留下的历史锚」，**不得**拿它去和今天任何一份收据对账。

**证据分层，别把推断说成已证**：

| 说法 | 证据强度 |
|---|---|
| ② 是 `8×8×3×7 = 1344 = case_target`，dtype 8 种、rank 1..8、shape `31/2047/262144` 加尾随 1 | ✅ **机器可复算**（直接读 `plugin/samples/specs/median.spec.json`） |
| ① 是 `8 dtype × 8 rank × 3 规模 × 6 属性 = 1152` | ✅ 有留档（`dev-doc/oprunway-execution-direction-review-checklist.md`），但只留了**计数结构** |
| ② 相对 ① 多出来的正是 `global`（无 `dim`）那一档、共 192 条 | ⚠ **设计记录级**：`dev-doc/oprunway-changes-brief.md`「1152 是 cannbot 仅按维 overload 的数量，任务书 global 接口另补 192 条，不能漏测」 |
| 「两者**只**差这一档，其余轴逐字段完全相同」 | ❌ **不可证**。① 的 per-run spec **未入仓**，仓内没有它的 dtype 清单、shape 数值、attr 逐档内容，**无法机校**。不得写成已证事实 |

- ① 的 51 条失败**全部**含越界 `indicesOut=2147483647`，技术归因已闭环到 DUT 长轴浮点路径
  （补证记录见 `dev-doc/oprunway-changes-brief.md`）；⚠ ② 的 58 条**逐 case 分布本仓无记录**，别照抄这条归因；
- ② 在本仓有**多次留档、全部走本地 checkout 通路**：`dev-doc/oprunway-local-source-realmachine-validation.md`
  §6（2026-08-05 端到端验收）与 §8（2026-08-06「就地跑」形态的 A/B 两跑）。
  ⚠ 强度分层：§8 的 A/B **两份 `verdict.json` 字节级相同**（实测）；§6 那次与 §8 之间**只核到汇总计数相同
  （1344 例 / 58 fail），没做逐 case 对照**——别把它写成「三次逐 case 一致」。

**为什么并列，不挑一个当正统**：

| 走向 | 代价 |
|---|---|
| 只留 1152 | 下一个人跑仓内样例 spec 必然撞见 1344，每次都要重新困惑一遍 |
| 只留 1344 | 抹掉真机那次 per-run spec 的历史证据 |
| **并列（本仓采用）** | 不丢任何事实，也不必在两份不可比的数字之间硬挑一个 |

⚠ **引用纪律（违反即失真）**：

- 引任何一组数**必须同时点名它对应的 spec**。只写「Median 精度基线是 XXXX 例」一律视为失真表述；
- ② **不是** ① 的「复现」。例数、失败数都不同，**任何「复现了基线」一类措辞都是错的**；
- 算术上 `1344 − 1152 = 192 = 8×8×3`（恰是新增的 `global` attr profile 那一档）、`58 − 51 = 7`。
  ⚠ **这只是算术相符，不是逐 case 对照结论**——本仓**没有**把两份逐 case 比对过的证据，
  **不得**据此宣称「① 的 51 条在 ② 里原样重现」或「② 只是多挂了 7 条」；
- 任务书要求的无 `dim`（global）接口**只在 ② 里被覆盖**：`dev-doc/oprunway-changes-brief.md` 原话是
  「1152 是 cannbot 仅按维 overload 的数量，任务书 global 接口另补 192 条，不能漏测」。
  所以论**任务书覆盖面** ② 更全，论**真机 per-run provenance** ① 是当时实跑的那份——两件事分开说，别互相顶替；
- 两组数的**确定性裁决同为 `FAIL(精度)`**，`gate.passed=true` 只说明证据完整、判定链自洽，
  **不是算子通过**。所以「换个 caseset 就能翻案」不成立，不要拿 caseset 之争当结论之争。

---

## 5 · 最高纪律

### 5.1 泛化优先，绝不按算子身份特判

这是最高原则：

- 接口、目标目录、shape、dtype、硬件从“任务书 × op_def × header/example”按字段分源探测；
- 代码里不得出现 `if op == "<具体算子>"` 一类身份分派；
- 允许按稳定的接口能力、仓形态或框架扩通用 adapter；
- per-op spec、golden、IR、gap、目标机是通用 schema 消费的数据，合法；
- 手写 per-op runner 或为某算子修改通用判定逻辑，违规；
- 具体算子只能作为见证/测试输入，不能成为通用机制的隐藏特例；
- 建通用能力时优先用能压满结构轴的见证，最小见证只用于冒烟、隔离故障或 baseline；
- 域内定义以 `plugin/acc-common/contract_ir/` 为准；无状态、标准 aclnn 两段式、无 opaque descriptor 的形态应工具零改可跑；
- 域外或未知接口能力一律 fail-closed 标“不支持的接口能力”，不硬塞、不自动归类；
- ABI 以 header/example 为事实源；语义、dtype、硬件以任务书和 op_def 交叉；
- 三源缺失或冲突仍无法确定时，停下询问用户，绝不静默猜测。

### 5.2 方案、权限与副作用

- 构建新 skill/agent/workflow 或做超出既有方案的架构改动前，先给方案、取舍和边界，经用户同意再实施；
- clone、checkout、build、真机跑测、删除/覆盖、改远端环境、对外发布前先确认；
- 支持的脚本优先提供 `*_DRY_RUN=1` 或等价 dry-run；
- 用户授权某项动作不自动扩张到其它仓、其它远端或其它副作用；
- 没有 NPU 的开发机上只做编辑、Git、只读探测和知识记录（「就地跑」形态下本机就是目标机，compute 本来就在本机做，不受此限；见 §5.3）。

### 5.3 一切 compute 在 NPU 目标环境（远程连 / 就地跑都是一等形态）

- build、pytest、用例生成、golden 生成、验收、profiler 全在 NPU 容器/目标环境执行；
- **没有 NPU 的开发机上**不建 venv、不跑 pytest、不 import torch/numpy 做验收 compute；
- 真机环境统一入口：`dev-doc/oprunway-real-machine-environment.md`；
- 「远程连」形态的实际连接元数据：本地忽略文件 `.oprunway/real-machine.env`。

**先认清自己在哪种执行形态**，两种都是一等通路，不是「主 + 降级」：

| 形态 | 说明 | 要 `.oprunway/real-machine.env` 吗 |
|---|---|---|
| **远程连**：开发机 → 目标机 | 得先 SSH 进目标机/容器才够得着环境 | **要**——SSH alias、容器名、远端工作目录都在里面 |
| **就地跑**：会话本身就在目标机（或其容器）里 | 环境就在本机，没有「连过去」这一步 | **不要**——压根没有连接元数据可言 |

- ⚠ **`.oprunway/real-machine.env` 是「远程连」这一种形态的连接元数据，不是跑验收的通用硬前置。**
  **文件不存在不构成阻塞**：就地跑时本来就用不到它，编排层**不得**以「缺 `.oprunway/real-machine.env` /
  拿不到 SSH alias、容器名、远端工作目录」为由拒绝启动验收。两种形态各自要哪些环境变量，
  见 `dev-doc/oprunway-real-machine-environment.md` §1；
- **保护根语义不随形态放松**：该文件**存在时**，每次新 session 做任何 clone/build/跑测/清理前**必须读取**其
  `OPRUNWAY_MACHINE_PROTECTED_ROOTS`。其中每个根及其全部子目录均为
  **只读保留现场**：禁止写入、覆盖、移动、删除或作为新执行目录；只允许经用户明确要求的只读核验。
  **文件不存在、或存在但未设该变量**，都只表示**当前未登记保护根**——不构成阻塞，但同样**不得**据此
  推断任何目录可以随意写入或清理（未登记 ≠ 已授权；删除/覆盖仍按 §5.2 逐次征得用户确认）；
- 机器 profile 只负责找到执行环境，不能替代任务书硬件核定和本轮 PR provenance。

### 5.4 零硬编码与本地配置

- 仓名、路径、SoC、目标算子、阈值、PR head 不写死在通用代码；
- 运行时探测、从 spec/pr_facts 派生或询问用户；
- 不碰 `~/.config`、不改 shell rc；
- 验收产物只落用户 CWD 的 `reports/`；
- `.oprunway/real-machine.env` 是机器连接元数据的唯一仓内本地例外，必须保持 ignored；
  它只服务「远程连」形态，就地跑时不需要它存在（§5.3）。

### 5.5 Git、发布与署名

- 不 push、不 merge，除非用户明示；
- 对非本用户仓的 issue、PR、comment 必须先获同意；
- commit 可以按开发检查点进行，不要求每个 commit 单独审；
- 人类署名使用 `lys` / `lllyys`；
- commit、PR body、报告和其它对外产出不得带任何 AI 署名、trailer 或生成标识；
- 历史遗留 trailer 不重写，只约束新产出。

### 5.6 文档落点与改动简表

- 项目 Markdown、图、SVG 等**开发过程产物**统一放仓根 `dev-doc/`；
  这里放的是设计稿、TODO、handoff、实测记录、环境说明——是**开发者写给自己和后来者看的**，
  不是面向使用者的产品文档（那类若将来要有，另立目录，别混进来）；
- 不写到工作区上层的 `markdown/`；
- 每次落地后在 `dev-doc/oprunway-changes-brief.md` 顶部追加一两句倒序摘要；
- 当前交接**两份并列，都要读**，⚠ **合并后尚无单一权威 handoff**：
  `dev-doc/oprunway-session-handoff-2026-08-05.md`（主线：GaussianBlur / ops-cv、`measure_only`、
  任务书自带用例集）与 `dev-doc/oprunway-session-handoff-2026-08-05-evening.md`
  （本地来源 + `runner_form` 准入收敛那条线）。两份各自成文于合并之前，**都不描述合并后的状态**——
  下一轮开工第一件事是产一份合并后的新 handoff，届时这两份降为历史材料；
  更早的 handoff（2026-08-04 / 2026-07-26 / 2026-07-13）只作历史材料。

### 5.7 push 前审修门

push 前，对自上次 push 以来将要发布的全部改动统一做一轮审修；不逐 commit 审：

- 代码/脚本：走 `cc-suite:audit-fix` 的 audit → fix → verify，一轮即停；
- 散文/设计/仓规：走独立 Codex 散文审；
- 散文审默认使用 `gpt-5.6-sol`、reasoning `low`；
- 当前 CLI 形式为 `codex exec -m gpt-5.6-sol -c model_reasoning_effort=low`；
- verify 剩余 finding 如实交用户，不自动无限迭代；
- `nlpm` 是 NL 制品质量 lint，不替代本门；
- 旧 `mcp__plugin_nlpm_codex-cli__codex` 已退役，散文审走 `codex exec` CLI；
- ADR 0010 当前存在历史触发点张力；在 `bureau:review` settle 前，以本节“push 前统一审修”为现行规则。

### 5.8 不捏造、不越权判定

- 报告数字、错误和耗时必须来自真实日志/产物；
- 推断项显式标“推断”；
- `needs_review` 不当 pass；
- “PR 有测试”“代码接通”“covered”“collector 有数据”都不等于验收通过；
- FAIL 归因前先核任务书↔PR 对应，再解耦 DUT 与 harness；
- 验收权威只认任务书，最终裁决只认确定性脚本链。

### 5.9 canon 写门与开工 grounding

- durable 知识遵循 capture → compile → review；
- 不手改 cabinet 页，不自行把状态升为 canonical；
- 只有 canonical 可当已定事实，proposed/verified/stale/contested 均须按 trust tier 对待；
- 开始 durable 设计、组件建设、bureau 写入或 FAIL 归因前，先读 `canon/architecture/`、`canon/decisions/` 和 `canon/lint/findings.md`；
- canon 过大时至少读 overview，并用 bureau query 按需查证；
- 通读与 query 并用；未读或未 settle 页面不得冒充门禁依据；
- 当前运行规则与未 review canon 冲突时，显式记录张力，不静默覆盖。

### 5.10 性能口径：只测 msprof 实测，不比 GPU

用户 2026-08-03 明示的**全局原则，适用于所有类型的任务书**：

- 任务书**对性能没有要求** → 只用 msprof 采 NPU 实测性能即可；
- 任务书要求的是**与 GPU 比对**（如“以 OpenCV CUDA A100 为参考，ratio ≥ 0.45×”）→ **同样只用 msprof 测实测性能**，
  不必真的去对比 GPU、不必获取 GPU 标杆数据；
- 本轮改动属**对原算子新增 dtype 支持 / 扩展 shape·rank / 开发新算子**三类之一（用户 2026-08-05 明示）
  → **同样只用 msprof 测实测性能**，不做任何同机比值对比。

理由：GPU 标杆（A100 / OpenCV CUDA / ATK 双标杆）要么拿不到环境，要么获取成本远高于它带来的验收价值；
卡在等 GPU 数据上会把整条流水线阻塞住。而 NPU 侧 msprof kernel-only 数据在**真机实跑、
与 case 及本轮 provenance 绑定、采样与有效性检查都做过**的前提下，是可信、可复现的性能证据
（这三个前提缺一条就不成立——「kernel-only」只说明计时范围，本身不构成可信性）。

落地约束：

- 不因缺 GPU 数据把结论落到 `BLOCKED_WAIT_GPU_BENCHMARK`；该终态只在用户明确要求做 GPU 对比时才用；
- 性能维产出 = msprof 实测 kernel 耗时 + 分档说明，不是比值裁决；
- 三种情形（`no_perf_requirement` / `gpu_comparison` / `change_class_no_perf_comparison`）**授权强度相同**：
  都须在 spec 的 `perf.measure_only_authorization` 里给出 ground + cite + quote + `taskdoc_snapshot_sha256`，
  缺一 fail-closed（受控词表见 `plugin/acc-common/perf_mode.py`）；
- 走**改动类别**这一条时，任务书**若另写了比值 / 绝对门限 / 吞吐条款，该条款照旧强制进 `task_pr_gaps`
  标「未验收」**，本轮仍只产 msprof 绝对耗时，禁止取 baseline、禁止算 ratio、禁止任何达标宣称
  ——与上面 GPU 条款的处置逐字同形；改的是取证方式，不是条款可以不算数；
- 报告须如实写“按用户口径只做 NPU msprof 实测，未做 GPU 标杆对比”，
  **不得把它包装成“已达标 0.45×”**——没测的比值不能编（5.8）；
- msprof 数据仍须真机真跑，不接受推算（5.3）；
- **任务书的 GPU 比值条款按「未验收」记账**，不是「已通过」也不是「不适用」：
  该条进 `task_pr_gaps`，最终裁决**不得**因为 NPU 侧有实测数就宣称整体通过。
  任务书仍是验收权威（5.8）——本节改的是「怎么取证」，不是「条款可以不算数」。

### 5.11 精度真值口径：任务书写 GPU 一律解析为同族 CPU

用户 2026-08-05 明示的**全局解析规则，适用于所有任务书**：

> 任务书里指定 GPU 真值口径的（如「以 OpenCV GPU / CUDA 为标杆」），
> **就应该被解析成同族的 CPU 实现**。这是一类问题的统一读法，不是逐份的例外处理。

⚠ **它与 5.10 是两件不同性质的事，别照搬**：

| | 5.10（性能） | 5.11（精度真值） |
|---|---|---|
| 做什么 | **取消比较**——不取标杆、不算比值、不产达标结论 | **解析口径**——把 GPU 写法读成同族 CPU |
| 任务书条款 | 仍然成立，按「未验收」进 `task_pr_gaps` | **已被满足**（CPU 就是它的正确读法） |
| 报告怎么写 | 「未做 GPU 标杆对比」 | 「任务书写 GPU，按 §5.11 解析为同族 CPU」 |

因此 5.11 下**不产生**「GPU 口径未验收」这类 gap——那是把解析规则误当成降级取证。
反过来也不许含糊：报告须留**解析记录**（原文怎么写的、解析成了什么、依据本节），
不得直接写成「任务书要求 CPU」把解析这一步抹掉（5.8：事实与推断分开）。

落地约束：

- 映射是**数据**（受控表，如 `opencv_cuda` → `opencv_cpu`），按**具体库**判，
  绝不按算子身份分支（5.1）；**该库无 CPU 对应 → 仍 fail-closed**，不硬凑；
- ⚠ **粗粒度声明解析不了，也不许猜**：`gpu_lib` 这类兜底值底下同时装着 OpenCV-CUDA、cuSPARSE、
  cuDNN……把整族映射到某一个 CPU 库，等于把「任务书点名 cuSPARSE」悄悄换成 OpenCV CPU，
  那不是「同族」而是换了个不相干的实现。任务书写得太泛时如实落 `gpu_lib` 并 **fail-closed**，
  要人把真值口径细化到具体库后重判；
- 解析后的 `method_kind` 必须落在 `precision_policy.RUNNABLE_METHOD_KINDS` 内，否则照旧 fail-closed；
- ⚠ **阈值不随口径自动搬家**：任务书阈值若是按 NPU↔GPU 误差预算给的，套到 CPU↔NPU 上未必成立
  （同一库的 CPU 与 GPU 实现并非逐位一致）。阈值来源与真值口径**不同源**时，
  报告须标明该维「阈值来源与真值口径不同源」，由人确认；本轮 GaussianBlur 的阈值走
  workflow 默认口径、与该风险无关。

---

## 6 · 仓目录

```text
OpRunway/
├── AGENTS.md                         # 唯一仓规源
├── CLAUDE.md                         # 仅 @AGENTS.md 路由
├── BUREAU.md                         # bureau 入口
├── .oprunway/
│   ├── real-machine.env.example      # tracked 脱敏模板
│   └── real-machine.env              # ignored 本地真实值
├── dev-doc/                          # 开发过程产物：设计、TODO、handoff、实测记录、环境说明
├── plugin/
│   ├── acc-common/                   # Layer 0/1 契约与确定性脚本
│   ├── agents/                       # Layer 2 agent 薄壳
│   ├── skills/                       # Layer 2 skills
│   ├── commands/                     # Layer 2 入口
│   └── samples/                      # spec/golden/runner 样例数据
├── canon/                            # bureau durable knowledge
├── reports/                          # ignored 验收产物
└── repos/                            # ignored 外部被测/参考仓
```

---

## 7 · 外部仓与复用边界

涉及仓会随任务变化，已知范围包括但不限于：

- `cann/catlass`
- `cann/ops-nn`
- `cann/ops-math`
- `cann/ops-sparse`
- `cann/ops-blas`
- `cann/ops-cv`
- `cann/asc-devkit`
- `cann/catccos`
- `cann/shmem`
- `cann/oam-tools`
- `cann/amct`
- `cann/hixl`
- `cann/cann-recipes-infer`（重点子目录 `ops/tilelang`）

复用边界：

- `repos/` 下的外部仓是被测对象或方法论参考，不进入本仓 Git；
- 浅克隆不能冒充指定 tag/commit 已核实，需要特定版本时单独 fetch 并记录 provenance；
- `cannbot-ops-input`/cannbot 只作 case、精度、性能方法参考，不成为运行时依赖；
- catlass、ops-*、稀疏/通信等不同仓形态通过通用能力或 per-repo adapter 接入，不互相硬套；
- 姊妹项目的环境搭建经验可复用，“跑没跑崩”式判定不能替代本仓精度/性能验收；
- 具体任务始终以正确的任务书、对应 PR 和本轮事实包为准。

---

## 8 · 深挖入口

| 目标 | 入口 |
|---|---|
| CP-A..E 状态机、硬门、subagent 契约 | `plugin/AGENTS.md` + `plugin/skills/acceptance-workflow/SKILL.md` |
| 设计与数据契约 | `dev-doc/oprunway-design.md` |
| 最新交接 | ⚠ **两份并列**：`dev-doc/oprunway-session-handoff-2026-08-05.md` + `dev-doc/oprunway-session-handoff-2026-08-05-evening.md`（都成文于本次合并之前，见 §5.6）|
| 当前 TODO | `dev-doc/oprunway-todo.md` |
| 改动流水 | `dev-doc/oprunway-changes-brief.md` |
| 真机环境 | `dev-doc/oprunway-real-machine-environment.md`（「远程连」另需 `.oprunway/real-machine.env`；「就地跑」不需要，§5.3）|
| 本地来源真机验证（⚠ 记录的是已被取代的 `local_checkout` 实现，见 §9.3）| `dev-doc/oprunway-local-source-realmachine-validation.md` |
| 已定决策 | `canon/decisions/`，先看 status/trust tier |
| 人读蓝图/历史案例 | `plugin/workflows/`，冲突时以 acceptance-workflow skill 为准 |

---

## 9 · 当前能力边界

先看三条最容易踩的：正式验收只走 `cpp_extension`（§4）；本地代码是一等输入形态、
**已跑通端到端全链，但「出性能比值裁决」仍未见证**（§9.3）；
`fetch_source.py` 一改，既有 preparation 收据全部变 `MISS`（§9.4）。

### 9.1 真机已坐实的

- 真 NPU 已坐实：IsClose、Sign；Median 的 `cpp_extension` 精度结果是**两个不可比的 caseset 并列**——
  PR 通路 per-run spec 的 1152 例（1101 PASS / 51 FAIL）与本地通路仓内样例 spec 的 1344 例（1286 PASS / 58 FAIL），
  两者均 `gate.passed=true`、确定性裁决同为 `FAIL(精度)`；**引用必须点名 spec 与来源身份**，完整口径与纪律见 §4.5；
  Elu/Silu 在 A5-950 有 18/18 非空例证据；
- **GaussianBlur（ops-cv，2026-08-05，干净现场端到端）**：`runner_form=cpp_extension`、
  `declared_source_form=local_source`（本地代码，非 PR）、`precision.case_source=taskdoc`
  （用任务书自带的 169 条自测用例与 OpenCV CPU golden）。终态
  `BLOCKED_GOLDEN_UNAVAILABLE`、`gate.passed=true`、`gate.errors={}`；
  169 例中 164 例可判且**数值失败 0**，5 例因通道数超 OpenCV `CV_CN_MAX` 算不出真值 →
  记为**结论空白**（非算子失败）；性能 16 条真实 kernel-only `npu_us`（`measure_only`，无标杆对比）。
  ⚠ 这条**不是** PASS，是「能判的部分没查出问题、有一部分判不了」；
- **本地代码是一等输入形态**（5.11 之外的另一条 2026-08-05 口径）：`--pr-snapshot` 即声明
  `local_source`，`completeness=complete`、**无需**任何降级授权环境变量；
  「声称测 PR 却只拿到快照」仍是降级、仍要显式授权，**未声明按最严的 `git_pr` 对待**；
- **任务书自带用例集/golden 通路已闭环**：`taskdoc_links.py`（链接取材）→ `taskdoc_caseset.py`
  （识别 + 接口映射 IR + golden wrapper）→ `gen_cases --taskdoc-caseset`。
  任务书给了 case 就用它的，**识别不到即 BLOCKED，绝不回退自生成**；
- Median 性能数据不是零数据：custom 50/50、`torch_npu` baseline 48/50 有效，48 对评分、35 对达到 `ratio >= 1.0`；
- 2 个 BF16、`dim=1` baseline case 报 161002、custom 成功，按 baseline limitation 挂起，不归因 DUT；
- 用户已确认 Median 任务书所称 `aclnnMedian` / `aclnnMedianDim` 小算子拼接版本等价于 Torch 对应接口，故性能 baseline 为同机 `torch_npu` 的 `torch.median`，无需另证等价、也不改为直调单个 ACLNN 接口；
- **通用性能 case 规则**：性能 case 必须从同一份精度 caseset 选择；A3 按全部输入物理载荷之和 `<= 256 KiB` 为小 shape、`> 256 KiB` 为大 shape。硬件边界写入 spec，不按算子身份分支；大小分类只用于分组，所有性能 case 仍须真实采集。

### 9.2 验收通路已收敛到 `cpp_extension`

规则和理由见 §4，这里只记边界后果：

- `cpp`、`aclnn_py` 仍能在真机上跑（须 `--allow-experimental-form`），但**不产验收裁决**；
- `aclnn_py` perf collector 已真机产出同口径 kernel-only 数据——“通路有数据”既不等于“任务书条款通过”，现在也不等于“能出裁决”；
- `aclnn_py` 的历史 Median 60/60 属于旧 caseset，不得沿用为 torch_parity 下的 PASS；
- mock/catlass_mock 只产带 `evidence_grade="development"` 和 NON-ACCEPTANCE 标记的
  `dev_run_summary.json` / `dev_precision_check.json`，不产 `acceptance.json` / `verdict.json`；
- **CP-F 后验精度复测只接受 `base spec.runner_form == "cpp_extension"`**（2026-08-05 定论，
  不再是“待确认的副作用”）。`cpp` / `aclnn_py` 的历史验收产物**仍保持原裁决与历史效力**，
  但不支持创建或执行 CP-F 复测 attempt。被拒表示“当前复测能力不覆盖该通路”，
  **不表示基础验收失效、失败或被重新裁决**。

  ⚠ **CP-F 没有逃生阀，`--allow-experimental-form` 不适用于它、也不得用于绕过。**
  理由是那个逃生阀的全部安全性建立在“该路径**物理上不产** `acceptance.json` / `verdict.json`”上，
  而 **CP-F 就是要写 `verdict.json`**（`precision_retest_runner` 落 attempt 产物），
  报告还直接展示“validator 精度裁决”——放非准入通路进来，产出的东西长得就是一份验收裁决，
  等于换个门绕过准入。

  出路（真需要复测非准入通路时）：用 `cpp_extension` 重做一次完整 CP-A..E 验收当新基线，
  再对它做 CP-F。⚠ 那是**新验收**，不能称作旧通路的漂移复测。
  编排层在 F0/F1 就不该起草这类 directive，别拖到 F3 才失败、白做冻结。

### 9.3 被测来源：本地代码是一等输入形态

被测代码有两条**平级**输入形态，不是“主 + 降级”。判别式**不是一个字段，是两条正交词表**——
一条说“你声明要测什么”，一条说“工具真拿到了什么”，两者比对才是档位判据：

| 声明 `declared_source_form` | 实得 `provenance_kind` | 怎么取 | 档位 | provenance 锚 |
|---|---|---|---|---|
| `git_pr` | `gitcode_pr` | `fetch_source.py --pr <PR 链接>` | `complete`，不需授权 | `pr.head_sha`（恰 40 hex） |
| `local_source` | `local_snapshot` | `fetch_source.py --pr-snapshot <本地目录> [--target-dir <仓内相对目录>]` | `complete`，**不需任何授权环境变量** | `snapshot_merkle_sha256`（恰 64 hex）+ `snapshot_scope` |
| `git_pr` | `local_snapshot` | 本来要测 PR、只拿到快照 | `snapshot_only` = **降级** | 须 `OPRUNWAY_ALLOW_DEGRADED_PROVENANCE` 授权，并挂 `pr_head_unbound` |
| `local_source` | `gitcode_pr` | —— | **一律拒**（声明本地却带着上游 commit） | —— |

- 读侧唯一入口是 `plugin/acc-common/source_provenance.py`；路由 allowlist `_ROUTES` 只装上表**前三行**
  这三条可达路由（第四行不在表里 = 不可达 = 拒），**表外一律拒**。
  adapter 侧另有 `source_mode ∈ {git_fetch, local_snapshot}`，换算表只在 `ADAPTER_KIND_TO_INTAKE` 一处；
- `--pr` 与 `--pr-snapshot` 走 argparse 互斥组，同给在**解析期**退 2；
- `--target-dir` **两条通路共用**，逐字采用、末段即 op 名，绕过 `_guess_op`。ops-cv 那种仓根一级算子目录
  （`gaussian_blur/`）能跑通靠的就是它；
- **未声明形态按最严的 `git_pr` 对待**：老事实包没有 `declared_source_form`，两侧都没有时仍落降级档、
  仍要授权，与改动前逐字同规矩；一侧有一侧没有则 fail-closed（那不是同一次取材产的）；
- `pr_head_unbound` 的语义**已分家**：`local_source` 形态的 `head_sha=null` 是**中性事实**，
  走 `bindings["source_form_facts"]`（`local_source_has_no_upstream_commit` /
  `..._file_set_is_subtree_not_pr_diff`），**不进降级台账**；只有「本该绑却没绑」才是降级。
  ⚠ 报告须原样带着这些中性事实（不得声称已绑 PR head），但**不得**把它们渲染成「降级 / 未绑定」。

⚠ **`--pr-snapshot` 收的是「一份没有 git 的目录快照」，工具完全不探 git。** 所以这条通路里
不存在 dirty worktree、base-ref、git head 之类的概念，也没有对应的门——别去找。

**摘要与锚的覆盖面：**

- intake 侧产**一个** merkle（`snapshot_merkle_sha256`），范围由 `--target-dir` 决定的 `snapshot_scope`；
- vendor build receipt 侧绑**六项**：仓根 `repo` + `snapshot_subtree_scope` + `snapshot_sha256`（**整树** merkle）
  + `snapshot_subtree_sha256`（**算子子树** merkle）+ 构建 argv + vendor ELF `sha256`；
  `source_provenance.check_build_identity` 做「build 实得 ↔ 执行配置 ↔ CP-C0 bindings」三方对账，
  且要求两侧 scope 相等才比 merkle；
- ⚠ **跨端对账比的是子树那一个**：intake 的 `snapshot_merkle_sha256` ↔ 收据的 `snapshot_subtree_sha256`。
  两侧**不同名**，别按同名比；
- ⚠ **`gitcode_pr` 档必须反向排他**：`snapshot_sha256` / `snapshot_subtree_sha256` / `snapshot_subtree_scope`
  须为缺席或 `None`，堵「本地 provenance 伪装成 PR provenance」。注意 payload 里 PR 通路**恒带**这些键、值为 `None`，
  判据只能写「值是 `None`」，写成「键不存在」会把所有 PR 通路当场打死；
- 排除目录名与算法版本以结构化的 `snapshot_digest_policy` 写进事实包（**本轮新增**），校验端逐字对账，
  不接受任意排除策略。⚠ 在它落地之前，跳过目录名只落在 `pr_facts.snapshot_skipped_dir_names`、
  **下游任何一道门都拿不到、也没人对账**——「摘要覆盖了什么」是暗知识。

⚠ **摘要算法明确不覆盖三件事**（要修就得升 `algorithm_version` 并同步改三处实现，且**作废现存全部
现场 merkle 与收据**——本轮没做）：

1. **软链**：文件与目录软链一律跳过、不进摘要。`median.cpp -> /tmp/x.cpp` 这类替换后，
   编译器跟随软链读到的字节完全不在覆盖内；
2. **空目录**：不计入，空目录本身的增删不可见；
3. **可执行位**：不入帧，`build.sh` 从 644 改到 755 会改变构建行为却不改摘要。

⚠ **两条硬限制仍在，别含糊过去：**

1. **子树 merkle 只覆盖 `snapshot_scope`。** 整树 `snapshot_sha256` 把仓级构建脚本、公共头文件
   也纳进来了，比只有子树锚时严得多；但**跨端对账用的仍是子树那一个**，整树摘要目前只作记录。
   ⚠ 落在 scope 之外的 `aclnn_headers` 是否会被静默漏掉（`_key_file_candidates` 只从 scope 内挑），
   **本轮没有核到**，留作待查（见 §9.4）；
2. **本地来源的性能维尚未见证到「出比值裁决」。** GaussianBlur 那轮性能走的是 `measure_only`
   （16 条真实 kernel-only `npu_us`，无标杆对比，§5.10）；更早 Median 那轮精度判 `fail`、
   Task3 按 fail-fast 跳过采集。所以“本地来源能出**性能比值**裁决”这件事仍未见证。

**真机见证：**

- **GaussianBlur（ops-cv，2026-08-05，当前唯一的本地来源全链见证）**：
  `runner_form=cpp_extension` + `declared_source_form=local_source` + `--pr-snapshot`，端到端跑通。
  终态与逐项数字见 §9.1；
- ⚠ **Median 那轮本地来源验收（2026-08-05，专用容器）是历史记录，其实现已被取代。**
  那次走的是本轮合并中**已删除**的另一套实现（`dut_source.py` 判别式 + `local_checkout.root_digest`
  + `oprunway.local_subtree_merkle` v1）。它留下的 `root_digest=c8867ce09f6e…` 由那套算法算出，
  **在当前实现下复算不出来**（帧格式、排除集合、路径基准三处都不同）。
  完整记录见 `dev-doc/oprunway-local-source-realmachine-validation.md`——读它时**只把结论当历史**，
  里面的字段名、CLI 参数（`--local-repo` / `--op-subdir` / `--allow-dirty`）与锚值**都已不是现行口径**。

**接入状态（权威表在 `plugin/acc-common/source_provenance.py` 模块头，以代码为准）：**

| 消费者 | 状态 |
|---|---|
| `fetch_source`（产出方） | ✅ 已接 |
| `validate_preparation_state` | ✅ 已接 |
| `preflight_aclnn` | ✅ 已接（`source_provenance.bind` 一处出判定，不再手写分支） |
| `cpp_extension_adapter` / `cpp_extension_driver` / `validate_acceptance_state` | ✅ 已接（vendor build receipt 绑定，**主验收链**） |
| `render_acceptance_markdown` | ✅ 已接（按 `provenance_kind` + `declared_source_form` 渲染「来源与 provenance」节） |
| `precision_retest_contract` / `precision_retest_runner` | ✅ 已接（CP-F 验收后复测） |
| `verify_aclnn_harness` | ✅ 已接（`source_provenance.check_config_against_preflight` / `check_build_identity`） |

⚠ **最后一行是本轮翻案的：** 上一版仓规写着「`aclnn_adapter` 构建端根本不存在可与本地锚对账的锚，
所以这道门对本地来源结构性 fail-closed、不是待办」。那个判断**已被证伪**——`aclnn_adapter._source_block`
现在在容器内内联**同一份**摘要算法，算出 `SNAPSHOT_SHA256` / `SUBTREE_SHA256` 与 intake 侧对账
（真机实测两端对同一目录得同值、2565 个文件）。原来的 `_require_pull_request_path` 是在拦一条已经跑通的通路，
本轮已删。⚠ 连带纪律：**这份算法现在有三处实现**（`fetch_source._snapshot_merkle`、
`vendor_build_receipt.take_snapshot_digest`、`aclnn_adapter` 里那段内联 python3），
**改一处必须同步改三处**，否则症状是永远的 `SNAPSHOT_MISMATCH`。

**三级门里的来源锚对账，两步且顺序固定：** 先核两边 `provenance_kind`（及两侧都在场时的
`declared_source_form`）一致，再按形态核锚值相等。⚠ 第 0 步不等时的错误信息要说
「来源身份被伪装」而不是「锚漂移」——两件事的排查方向完全不同。

`source_facts.json` 缺席时**本门自己**的处置按通路分，这条是实测逼出来的：历史真机验收报告目录
（`reports/<Op>-spec-<x>/`）里**本来就没有** `source_facts.json`，取材的 `--out` 与验收产物目录不是同一个。
所以 `local_snapshot` 找不到就 BLOCKED，`gitcode_pr` 沿用旧行为；`validate_acceptance_state` 的
`--source-facts` 可显式指路。

✅ **那条伪装面已由编排层封死（2026-08-05）**，不再是待办：`run_workflow` 在**验收通路**上把
`--source-facts` 定为**必填**（缺席即拒跑，且拒在 `os.makedirs` / staging / Task1 **之前**，不留半个产物目录），
把它按字节 staging 进 `--out`，并**每次都显式**把这份 staging 副本指给 task1/task2/task3 三级门，不走自动发现。
于是正常验收链上「没有对照物」不再是一个可达状态——**缺席本身成了非法**。CP-F 同理
（`precision_retest_runner` 显式传冻结副本）。⚠ 连带后果：报告目录里现在**会**有一份 staging 的
`source_facts.json`，上一段那句「报告目录里本来就没有」只对**封死之前**的老产物成立。

⚠ **剩余面，如实记账**：**手工单独跑 `validate_acceptance_state` CLI 且不给 `--source-facts`** 时，
`gitcode_pr` 照旧不阻断（就是上一段那条按通路分的处置）。要判断一份产物是不是走了封死后的编排链，
看它 `--out` 里有没有那份 staging 的 `source_facts.json`；没有就说明它是老产物或手工拼的，
**别当成「门放行了」**。显式给了 `--source-facts` 却指不到文件**不属于**这条剩余面——那按
`SOURCE_FACTS_UNTRUSTED` 阻断（typo 不得退化成「没找到」）。

### 9.4 本轮的连带账单

⚠ **既有 preparation 收据会从 `REUSABLE` 变 `MISS`。** 原因是 `producer.logic_sha256` 就是
`fetch_source.py` 自身源码的哈希、且它在 payload 里——改了工具必然改 digest。
**这是正确行为，不是复用坏了**：看到 MISS 别去“修”复用逻辑，重跑取材即可。

⚠ **真机上留存的 aclnn harness 信任门收据会 revalidate 失败。** `verify_aclnn_harness._LOGIC_FILES`
新增了 `source_provenance.py`：这道门直接 import 它并用它做 provenance 判定
（`check_config_against_preflight` / `check_build_identity`），不纳入摘要就等于**判定逻辑有一半
脱离逐字节哈希覆盖**——放松路由表或把严校改成 `.get()`，旧收据照样能 revalidate 通过。
`bindings.logic_files` 因此整体变化。同样**是正确行为**：门的判定逻辑变了，旧收据不该继续算数。
下一轮要走 `aclnn_py` 真机通路的话，先重跑这道门。

⚠ **CP-F directive schema 又一次 breaking change，在途 attempt 全废。** 这是**第二次**：
上一轮把 `pr_head` 拆成 `pr_head_sha`（恰 40 位）/ 本地摘要（恰 64 位），本轮随判别式换词表
再改一次——本地侧现在是 `snapshot_subtree_sha256`（恰 64 位）+ `snapshot_subtree_scope`，
判别键改用 `provenance_kind`，`repo` 两条通路都必填。**旧 directive 一律不能继续执行**，
要重新起草 directive、重新跑 F2。
（历史成因值得记一笔：最早那版 `pr_head` 只有一条 `^[0-9a-f]{40,64}$`，那个 40..64 的区间
就是物理入口——填 64 位摘要能原样通过。所以字段宽度校验要**恰好**，不要给区间。）

CP-F 的 `directive.source_identity.repo` ↔ 首轮 build receipt `runner_binding.base_source_repo`
逐字对账保留（`repo` 原本“宣称有门其实没门”）。⚠ **只有 `cpp_extension` 通路有这个对照物**；
`cpp` / `aclnn_py` 的首轮 `execution_provenance` 里根本没有仓名字段，那两条通路的 `repo` 目前只作人工记账。
写法不一致（同一个仓写成 `ops-nn` 与 `cann/ops-nn`）会直接 BLOCK。

⚠ **`vendor_build_receipt.py` CLI 的 `emit --returncode` 是自报值，不是实测值。** 该脚本
全文没有 `import subprocess`、从不执行 build，而 schema 目前**记不下**「这个 0 是实跑还是自报」，
消费者分辨不出。真跑那条路径是 `make_vendor_build_receipt.py`（§3）。
在两者可机读区分之前（拟加 `build.evidence ∈ {executed, self_reported}` 并由验收门要求 `executed`），
**引用任何一份收据都必须点名它由哪条路径产出**，不许含糊过去。
⚠ 落地这条会让现存那些用 `emit` 产的现场收据在验收门上变红、需要重产——这是**待用户拍板的成本项**，
本轮只记账、未落地。

⚠ **`--target-dir` 收窄 scope 后，落在 scope 之外的 `aclnn_headers` 会不会被静默漏掉，本轮没有核到。**
`_key_file_candidates(paths, tdir)` 只从 scope 内的 `paths` 里挑；scope 外的头文件是「消失」还是
「报错」，需要到 `_apply_key_file_facts` / `_key_file_candidates` 实测一次再定。
**在核清之前不得假定它已 fail-closed。**

⚠ **§5.10 目前没有进 spec 的抽取路径。** `plugin/skills/acc-spec/references/taskdoc-to-spec.md`
仍只教 extractor 写 `baseline` + `target_ratio`，对「任务书性能要求：无」没有任何指引。
两条出路二选一：补这份 reference（教它写 `mode=measure_only` + 授权四件套），
或靠性能口径解析处的 fail-closed 把这类 spec 拦成 BLOCKED、逼人手工补授权。**别两个都不做。**

⚠ **张力，显式挂着不静默压平**：§4 把验收准入收敛到 `cpp_extension`（`aclnn_py` 不产裁决），
而同期 `aclnn_py` 的 ops-cv / 仓根一级算子 / 非标准 stage2 通路刚被打通。
样例上不冲突（GaussianBlur 的 spec 写的是 `cpp_extension`），但**若有 `aclnn_py` 的 spec 期望出正式裁决，
准入门会当场拒它**。这需要用户拍板，本轮不自行改任何一侧。
连带的 Roll 迁移成本照旧：其 spec 现写 `aclnn_py`，要做正式验收就得迁到 `cpp_extension`，
而后者要 torch.ops 桥 + vendor ELF 构建收据，接入成本更高（详见 §4.3）。

### 9.5 仓形态与外部依赖

- ops-<族>、标准 aclnn 两段式、用户态 opp 安装型是当前主要闭环；域外形态 fail-closed；
- 外部 GPU consumer 已接入，真实 GPU 数据仍待提供；**仅当用户明确要求做 GPU 对比**时，
  缺数据才走 `BLOCKED_WAIT_GPU_BENCHMARK`——默认口径见 5.10，不因缺 GPU 数据挂起。

---

## 10 · 发布形态

- OpRunway 继续作为本仓 `plugin/` 子目录维护，不拆独立 repo；
- scripts、JSON 契约和 skill references 保持工具中立；
- 各运行时只维护注册/入口薄壳；
- skills 外部同步属于后续发布事项，不是当前验收阻塞项；
- `CLAUDE.md` 不再复制规则，只路由到本文件。

<!-- bureau:start -->
@BUREAU.md
<!-- bureau:end -->
