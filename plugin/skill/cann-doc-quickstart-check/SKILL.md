---
name: cann-doc-quickstart-check
description: 忠实模拟开发者「照着快速入门文档一步步操作」来体检文档质量——只按文档做，禁止任何文档以外的探索、绕过和 workaround，看能否纯按文档跑通；跑不通就出结论报告，暴露文档缺陷。涉及「按快速入门跑一遍 / quickstart 体检 / 快速入门验证 / 快速入门能不能跑通 / 开发者照文档能不能上手 / 快速入门文档质量」等意图时使用本 skill。只针对 quickstart 与快速入门；评进阶教程与开发指南改用 cann-doc-tutorial-review。
---

# 快速入门文档体检

把自己当成一个**只会照文档做、不会自己想办法**的新开发者，严格按文档一步步执行，
看能不能跑起来。跑不起来的地方，就是文档的缺陷。

**默认只跑忠实趟**——「开发者纯按文档卡在哪」本身就是主结论，先出这份报告。
探索趟要真机再跑一整遍，**出完忠实报告后问用户要不要跑**，别默认烧掉两倍机器时间。

## 本 skill 的灵魂：忠实趟禁止探索

其它 skill 鼓励主动找 workaround，本 skill 的忠实趟恰恰相反。

**因为探索也许能把它跑起来，但那是你的本事，不是文档的本事。** 一旦你 source 了文档没让你
source 的环境、加了文档没写的 flag、改了文档里的错命令、清了缓存重试，你就掩盖了文档的缺陷。
开发者照着这份文档**跑不起来**这一事实，必须先被忠实趟如实暴露。

两趟各自能做什么、探索趟只准注入什么，见
[references/two-passes.md](references/two-passes.md)——**P3 执行前读一次。**

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| `<skill>` | 本 skill 的安装路径 | 命令在**项目根**跑，产物落 `CWD/cann-ops-report/doccheck/<repo>/quickstart/` | 从本文件位置得出 |
| `<python>` | 解释器 | 只用 stdlib | 真机上的 python3 |
| `目标仓` | 被体检的仓 | 从 CWD 发现候选（有 `docs/` 或 README 的子目录），或用户给绝对路径 | P0 确认 |
| `待评文档` | 评哪一份快速入门 | 一仓一份，可多仓 | P0 让用户确认，**不替它选** |
| `SOC / 执行环境` | 文档若要求传 SOC 等 | **以文档为准**，不替文档假定 | 从文档读 |

## 前置检查

文档的 build / run 步骤要 NPU + CANN，**执行落到昇腾环境**（远程服务器或容器，
零硬编码，每次发现或询问）。范式：`ssh <host> 'docker exec <ctr> bash -lc "<命令>"'`，或直连。

**整套 P2–P4 必须在同一处跑**：`run_step` 与 `render_report` 在哪跑，
`cann-ops-report/doccheck/` 就落在那台机器的 CWD。别让台账和报告分散两地——
要么都在远程，要么把 `steps.json` 同步回本地后再本地渲染。

## 主流程

| 步 | 做什么 | 命令 / 去向 |
| --- | --- | --- |
| P0 | 发现并确认目标仓 + 待评文档 | `<python> <skill>/scripts/find_docs.py <repo_root> --json` |
| P1 | 把文档解析成字面步骤序列 | 见 [references/step-ledger.md](references/step-ledger.md) |
| P2 | 记一次 meta，确认起始状态 | 同上 |
| P3 | 忠实趟：逐字执行 + 卡住即停 | 同上 |
| P4 | 探索趟（按需）：补坑续跑 | 同上 |

### P0 — 发现并确认

`find_docs.py` 扫候选：`docs/QUICKSTART*.md`、`QUICKSTART*.md`、`docs/zh/快速入门*.md`、
`**/快速入门*.md`、`docs/**/getting[-_]started*`，兜底 `README*.md` 与 `docs/zh/*.md`
里标题含「快速入门 / 快速开始 / Quick Start」的。

候选用中文列给用户，`AskUserQuestion` 确认**每个仓评测哪一份**。
一仓没有任何候选 → 提示用户给文档路径，或如实记「该仓无快速入门文档」——这本身就是文档缺陷。

### P3 / P4 — 执行

逐步执行、判定四态、卡住即停闸门、两趟怎么合并调用，全在
[references/step-ledger.md](references/step-ledger.md)。

忠实趟遇到第一个 blocker 就停，出报告：

```bash
<python> <skill>/scripts/render_report.py --repo <repo>/quickstart/faithful --kind faithful \
  --out <CWD>/cann-ops-report/doccheck/<repo>/quickstart/faithful/REPORT.md
```

然后问用户要不要跑探索趟：

```
忠实报告已出：纯按文档卡在第 N 步（<卡点>）。
要不要再跑一趟探索趟——逐个补坑续跑，看这份文档一共几个坑、补齐后能不能真跑通？
  A. 跑（会再占一次真机 build/run 时间）    B. 不用，忠实报告就够了
```

选 B → 到此结束。选 A → 用 `--repo <repo>/quickstart/explored` 重跑一遍，末了同样渲染：

```bash
<python> <skill>/scripts/render_report.py --repo <repo>/quickstart/explored --kind explored \
  --out <CWD>/cann-ops-report/doccheck/<repo>/quickstart/explored/REPORT.md
```

## 产物

```text
cann-ops-report/doccheck/<repo>/quickstart/
├── faithful/                 忠实趟（纯按文档，卡住即停）
│   ├── doc_meta.json         文档相对路径 + 假设已满足的前提
│   ├── steps.json            逐步台账
│   ├── steps/<idx>.<slug>.log
│   └── REPORT.md             【报告①】开发者纯按文档卡在哪（主结论）
└── explored/                 探索趟（按需才有），同构
    └── REPORT.md             【报告②】补齐 K 个坑后能否跑通 + 完整修订清单
```

`steps.json` 每步的 `idx` / `doc_quote` / `command` / `cwd` / `expected` / `exit_code` /
`duration_s` / `timed_out` / `stdout_excerpt` / `stderr_excerpt` / `log_path` 由
`run_step` 自动写。**只需用 `--judge` 填** `verdict`、`defect`、`fix_suggestion`，
探索趟另填 `injected_fix`（忠实趟恒空）。

## 失败去向

| 触发 | 行为 |
| --- | --- |
| 选定仓无任何快速入门文档 | 不执行任何步骤；`run_step --meta --doc '(无)' --prereq '该仓未提供快速入门文档'` 后直接渲染，总评判「文档缺位」缺陷 |
| 文档某步 `FAIL` / 歧义 / 缺步 | 卡住即停，记卡点，出忠实报告，再问用户要不要跑探索趟 |
| 文档命令含明显笔误 | **照笔误执行**，把笔误当缺陷记下，不修 |
| 想到了 workaround | 忠实趟**禁止使用**，只记「文档缺此步」+ 修订建议；探索趟只准注入该修订建议本身 |

## 参考资料

- [references/two-passes.md](references/two-passes.md) — 两趟的能与不能、起始状态约定、
  与兄弟 skill 的边界。**P3 执行前读**
- [references/step-ledger.md](references/step-ledger.md) — 文档怎么解析成步骤、
  `run_step` 的三种模式与合并调用、四态判定、报告必备模块。**P1 起一直用**
