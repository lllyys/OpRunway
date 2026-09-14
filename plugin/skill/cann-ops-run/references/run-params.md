# 跑测参数与命令模板

P0 收参数、P2 解析命令模板时读这份。跑起来之后用不上。

## P0 的四项参数

先零成本自动发现，再把仍缺失的项**合并成一次 `AskUserQuestion`**（最多 4 问）问完。
不要拆成多轮——每多一轮往返都要重放整个上下文，而这四项彼此无依赖。

**自动发现（不问用户）**：扫 `CWD/cann-ops-report/*/scan/_intermediate.json`，
存在的子目录名即候选仓名；用户请求里已明示的字段直接采用。

| # | 问题 | 约束 |
| --- | --- | --- |
| 1 | 目标仓 + 本地源码路径 `{repo: path}` | 仓名是自由字符串，无硬编码列表；`vendor_name` 由仓名派生（`ops-X` → `custom_X`）。路径须绝对或相对 CWD 可解析，且目录里有 `build.sh`。有候选仓时把发现结果列给用户确认并补路径 |
| 2 | 目标算子清单 | 列举算子名，或给清单文件路径。**必须是具体清单，不接受「整仓无差别全跑」**——跑测成本按小时计，不在本 skill 的设计范围。要更大范围就提醒用户用 #3 筛选缩小，或自己给更小的清单 |
| 3 | 这份清单要不要按 A5/950 特性筛一遍 | 筛 / **不筛（默认、常态）**。这是叠加在清单之上的**独立开关，不是另一个互斥的算子来源**。**不要**先去看 CWD 下有没有历史 scan 产物再顺着它默认——扫过不代表这次就该用 |
| 4 | 目标 SOC | `ascend910b` / `ascend950` / `ascend310p` / 自动探测 / 其它。**不臆测默认值** |

四项都已明示 → 跳过询问直接执行；只缺个别项 → 只问缺的，仍是一次调用。

## 算子清单到 CLI 参数的映射

| 情况 | 传参 |
| --- | --- |
| 用户列出算子（CSV 或自然语言「跑 op1、op2」） | `--ops op1,op2,op3` |
| 用户给文件路径 | `--ops-file <path>`（`.json` 含 `unique_targets` / 顶层 list / 一行一算子纯文本） |
| 选「筛选」且用户**没有**候选清单（就想跑全仓 A5 特性算子） | 不传 `--ops` / `--ops-file`，runner fallback 读 `_intermediate.json`。**仅此一种情况触发 fallback** |
| 选「筛选」且用户**有**候选清单 | 取「用户清单 ∩ `unique_targets`」用 `--ops` 显式传，不走 fallback |

**选「筛选」的前置**：`cann-950-feature-scan` **只能扫全仓** `op_list.md`，没有「只筛用户子清单」
这条能力，不要凭空假设有。CWD 下已有该仓 `scan/_intermediate.json` → 直接读；没有 →
现场跑一次 `cann-950-feature-scan` 扫全仓。交集为空要如实告诉用户「清单里没有算子命中
A5/950 特性」，**不要静默改跑别的算子**。

**选「不筛选」**：清单原样跑，跳过 `cann-950-feature-scan`，**不查、不提**该仓有没有 scan 产物。

## SOC 自动探测

用户选「自动探测」或不传 `--soc` 时，runner 读真机芯片名（如 `Ascend910_9382`）并映射到
build.sh 的短串（`ascend910_93`），打印 `SOC 自动探测：<raw> → <build_soc>`。

探不到（acl 不可用 / 无 NPU 权限 / 无 CANN）→ runner 报错退出。回到 P0 让用户显式给 SOC，
**不要猜**：芯片精确名到 build.sh 短串之间不是字面截断，猜错编出来的包装不上。

## P2 — 命令模板发现

build / install / run_example 三步用什么命令，**不是全局写死的**，是每次跑测从目标仓自己的
`docs/QUICKSTART.md` 解析出来、当真值信任的：

- install 本来就是通用 glob（`build_out/cann-ops-*linux*.run`），不需解析，天然仓无关
- build / run_example 从该仓 QUICKSTART.md「编译运行」章节的 fenced 代码块抽取，
  示例算子名换成 `{op}` 占位符，其余原样信任
- **每次跑测重新解析，不做跨会话缓存**：文档可能被仓维护者更新，缓存住的旧命令比重新解析一次更危险
- 结果落 `<repo>/test/quickstart_derived_cmds.json`，纯审计用途，不作下次输入

**解析不到时不会自动帮你用默认模板跑。** runner 以退出码 4 报错退出，列出缺 build 还是 run、
来源文档路径。见此信号**必须停下**问用户：

```
{repo} 没能从 docs/QUICKSTART.md 解析出 {build/run} 命令模板。要不要改用默认模板继续
（--pkg --soc=<soc> --ops=<op> / --run_example <op> eager cust --vendor_name=custom）？
  A. 用默认模板继续跑
  B. 先不跑，我去看一下这个仓的文档
```

选 A → 带 `--force-default-template` 重跑；选 B → 停止，**不得自作主张用默认模板**。
