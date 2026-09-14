---
name: cann-ops-run
description: 在真机上跑 CANN 算子示例（build → install → 跑 examples），覆盖单算子、单仓全量、多仓并发，并给出跑测报告与失败诊断。涉及 build.sh / 跑测脚本命令、SOC 名称（ascend910b / ascend950 等）、跑测 / 续跑 / 重测 / 诊断某个算子为什么失败、950 特性（hif8 / simt / regbase）算子验证等意图时都用本 skill，不要直接敲 build.sh。只想把已知失败上报社区时改用 cann-issue-report。
---

# CANN 算子示例跑测

任意 ops 仓的目标算子 → build → install → 真机跑示例 → 状态台账与报告。

**不要直接调 `bash build.sh`、`build_out/*.run` 或自写并发脚本。** 这些命令不是全局固定的，
要从目标仓自己的文档解析；而且绕过本流程会跳过收尾闸门，导致「退出码 0 但结论错」。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| `<skill>` | 本 skill 的安装路径 | 命令都在**项目根**跑，产物落 `CWD/cann-ops-report/` | 从本文件位置得出 |
| `<python>` | 解释器 | 能 `import acl`（自动探测 SOC 时需要） | 真机上的 CANN 环境 python |
| `repo=path` | 目标仓与本地源码路径 | 目录里有 `build.sh` | P0 问用户 |
| `算子清单` | 要跑哪些算子 | **必须是具体清单**，不接受整仓无差别全跑 | P0 问用户 |
| `SOC` | 目标芯片 | `ascend910b` / `ascend950` / `ascend310p` / 自动探测 | P0 问用户，**不臆测** |

## 前置检查

`ASCEND_HOME_PATH` 由 CANN toolkit 安装时自动设置，runner 从中推导 `set_env.sh` 并自动 source。
若未设置，说明 CANN toolkit 未安装，提示用户先装——本 skill 不装、不换 CANN。

## 主流程

| 步 | 做什么 | 判据 / 去向 |
| --- | --- | --- |
| P0 | 一次性收齐四项参数 | 见 [references/run-params.md](references/run-params.md) |
| P1 | 读 `<repo>/test/run_state.json`，识别已 PASS 与 SKIPPED | 续跑时跳过它们 |
| P2 | 从目标仓 `docs/QUICKSTART.md` 解析命令模板 | 解析不到 → runner 退 4，回 P0 那份 reference |
| P3 | 起后台任务跑测 | 拓扑与命令见 [references/run-topology.md](references/run-topology.md) |
| P4 | 消费退出码 | `0` 收尾汇报；`3` 见下；`4` 回 P2 |
| P5 | 按需生成最终报告 | 见 [references/final-report.md](references/final-report.md) |

起跑前先声明范围（哪些仓、哪些算子）与拓扑（A/B/C），让用户有机会在花掉几小时机时之前纠正。

### 退出码 3 必须消费

runner 末尾有收尾闸门：跑完扫 `run_state`，把没结算的算子分三队列写进
`CWD/cann-ops-report/postrun_actions.json`。

| 队列 | 装什么 |
| --- | --- |
| `failed_ops` | `BUILD_FAIL` / `INSTALL_FAIL` / `RUN_EXIT_FAIL` / `RUN_PATTERN_FAIL` / `TIMEOUT` |
| `uncertain_reviews` | `UNCERTAIN`，待复核落成 `PASS` 或 `RUN_PATTERN_FAIL` |
| `incomplete_ops` | `PENDING` / `RUNNING` / 未知——**没真正跑完**（worker 崩了、或被中断） |

三队列全空 → 退出码 0；任一非空 → 退出码 3 + SUMMARY 顶部水印。

**看到退出码 3 就不能对用户说「本轮跑测完成」。** 闸门存在的理由就是这个：FAQ 查询、
自动续跑、探索、`UNCERTAIN` 复核都是 agent 的活，直接跑 runner 只做 build/install/run，
会把这些全绕过却仍然退 0。读 `postrun_actions.json`，然后读
[references/failure-followup.md](references/failure-followup.md) 并按其执行，
把 `uncertain_reviews` 逐个复核完，才算这一轮收尾。

**`UNCERTAIN` 复核**：它是四层判定的 L3 兜底（退出码 0 但既无强成功也无强失败信号），
跑测中不阻塞。复核时读该算子的 run 日志 + examples 期望输出，人工判定后落成
`PASS` 或 `RUN_PATTERN_FAIL`——`UNCERTAIN` 不是终态。

### 失败诊断（用户说「诊断 {op}」时）

1. 读 `<repo>/test/run_state.json` 找该算子的 `status` 与 `log_path`
2. 读 build / install / run 日志
3. 读算子源码 `<repo>/<category>/<op>/`
4. 写 `<repo>/test/failures/{op}.md`：根因 + 排查建议

只出结论，不动手改源码。

## 输出路径

产物按仓分开写，多仓互不干扰：

```text
cann-ops-report/
├── <repo>/test/run_state.json                算子跑测状态（权威）
├── <repo>/test/logs/                         每算子的 build / install / run 日志
├── <repo>/test/quickstart_derived_cmds.json  本轮解析出的命令模板（审计用）
├── <repo>/test/failures/<op>.md              失败诊断
├── <repo>/test/explorations/<op>.md          P6 探索结论
├── <repo>/test/PHASE{N}_FINAL_REPORT.md      最终报告（仅用户要求时生成）
├── postrun_actions.json                      收尾闸门的三队列
└── SUMMARY.md                                每轮自动生成的跨仓摘要（给人看）
```

## 边界与禁忌

- ✗ 不直接调 `bash build.sh`；不起多进程跑同仓多算子（`CMakeCache.txt` 冲突）
- ✗ 不修改算子源码 / examples / 测试用例。**唯一例外**是 P6 探索的临时分支内，跑完恢复现场
- ✗ 不假设 SOC 与算子清单，不硬编码仓名，不持久化仓路径 / 清单 / SOC
- ✗ 不重装 CANN，只 source `set_env.sh`
- ✗ 不凭空捏造错误信息，每条都要 grep 得到日志出处
- ✗ 跑测中途不生成报告；跑测期间不轮询日志
- ✗ 退出码 3 时不声称「跑测完成」

## 参考资料

- [references/run-params.md](references/run-params.md) — P0 四项参数、算子清单转 CLI 参数、
  SOC 探测、P2 命令模板发现与退出码 4 的处置。**P0/P2 阶段读**
- [references/run-topology.md](references/run-topology.md) — 三种并发拓扑的入口命令、
  合并 build 的连坐兜底、退出码表、为什么不轮询日志。**起跑前读**
- [references/failure-followup.md](references/failure-followup.md) — 失败算子的
  FAQ 查询 → 自动续跑 → 自主探索三段链条。**退出码 3 时读**
- [references/final-report.md](references/final-report.md) — 最终报告的数据来源、
  6 大模块、生成流程。**用户明确要求生成报告时读**
