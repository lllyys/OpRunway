# 产物根契约（算子仓闭环链路）

**改 `cann-env-setup` / `cann-950-feature-scan` / `cann-ops-run` / `cann-issue-report` / `cann-issue-track` /
`cann-doc-quickstart-check` / `cann-doc-tutorial-review` 中任意一个，且动到下表任一文件时读这份。**
只改单侧内部逻辑、或在开发别的链路，用不到。

本文只管算子仓闭环这条链路。ATK 链路走用例包、ops-blas 链路走六件包，契约各有一份：
[case-package-contract.md](case-package-contract.md) 与
[blas-case-package-contract.md](blas-case-package-contract.md)。

这份不放进任何一个 skill 目录，因为契约不属于任何一侧：放进生产方，改消费方的人看不到；
各放一份，两份必然漂。

## 与另外两条链路的差别：契约载体是产物根，不是包

ATK 与 ops-blas 的两侧靠一个**用户指定位置**的包握手，包路径当参数传。
这条链路不一样：**七个 skill 共用同一个产物根 `CWD/cann-ops-report/`**，
谁先跑谁在里面留下文件，后跑的按固定相对路径去读。

所以这条链路的「解耦」不是靠不互相提及，而是靠：

1. **消费方读文件，不假定执行顺序。** 找不到文件时打印**缺哪个文件、它由什么生成**，
   而不是「你没按顺序跑」。用户完全可以把别处跑出来的 `cann-ops-report/` 拷过来直接用。
2. **CWD 就是产物根的父目录。** 所有脚本用 `Path.cwd() / "cann-ops-report"`，
   命令一律在项目根跑。**不许有脚本 `cd` 进自己的 skill 目录**——那会把产物写进
   skill 安装目录，用户下次装新版就丢了。
3. **弱依赖不许升级成硬前置。** 下表标「弱」的文件缺失时该段留空，流程继续。

## 目录结构

```text
CWD/cann-ops-report/
├── setup/
│   ├── env_report.md                      cann-env-setup 的人读汇报
│   └── status.json                        cann-env-setup 的机读结论
├── <repo>/scan/
│   ├── _intermediate.json                 cann-950-feature-scan 的机读产物
│   ├── summary.md  detail.md              人读清单
├── <repo>/test/
│   ├── run_state.json                     cann-ops-run 的权威状态台账
│   ├── logs/<op>.phase1.<step>.log        每算子每步日志
│   ├── failures/<op>.md                   失败诊断
│   ├── explorations/<op>.md               P6 探索结论，首行含 SOLVED / UNSOLVED
│   └── quickstart_derived_cmds.json       本轮解析出的命令模板（审计用）
├── issues/
│   ├── state.json                         去重与提交状态
│   ├── repos.json                         repo → (platform, owner, repo) 缓存
│   ├── drafts/<repo>/per_op/<op>__<ft>.md  草稿，一算子一篇
│   ├── submitted/<repo>/<id>.json         已提交记录
│   ├── comments/<repo>/<id>.json          拉回的评论
│   ├── plans/<issue_id>.json              选定的修复方案
│   ├── patches/                           patch 类方案的 diff
│   └── replies/<repo>/<id>.json           回评内容
├── faq/FAQ.md                             cann-issue-track 沉淀的已知方案
├── doccheck/<repo>/
│   ├── quickstart/{faithful,explored}/ cann-doc-quickstart-check 的台账与报告
│   └── tutorial/                       cann-doc-tutorial-review 的 findings.json + REPORT.md/html
├── postrun_actions.json                   cann-ops-run 收尾闸门的三队列
└── SUMMARY.md                             跨仓摘要
```

## 跨 skill 的七项契约

| # | 文件 | 生产方 | 消费方 | 强弱 | 破坏时的表现 |
| --- | --- | --- | --- | --- | --- |
| 1 | `<repo>/test/run_state.json` | cann-ops-run | cann-issue-report、cann-issue-track | **强** | cann-issue-report 的 `scope` fatal 并说明去哪生成 |
| 2 | `<repo>/test/logs/` | cann-ops-run | cann-issue-report | **强** | 草稿里错误日志摘录段为空——**静默**，草稿照出 |
| 3 | `issues/state.json` | cann-issue-report | cann-issue-track | **强** | cann-issue-track 走手动注册流，不 fatal |
| 4 | `<repo>/scan/_intermediate.json` | cann-950-feature-scan | cann-ops-run（筛选开关）、cann-issue-report | 弱 | cann-ops-run 的 fallback 取不到清单；草稿里 950 段留空 |
| 5 | `<repo>/test/failures/<op>.md` | cann-ops-run | cann-issue-report | 弱 | 草稿缺「已尝试的诊断」段——**静默** |
| 6 | `<repo>/test/explorations/<op>.md` | cann-ops-run P6 | cann-issue-report、SUMMARY | 弱 | **首行必须含 `SOLVED` 或 `UNSOLVED`**，写别的词会被 SUMMARY 算成未解——**静默错算** |
| 7 | `postrun_actions.json` | cann-ops-run 收尾闸门 | cann-ops-run 自己的 agent 节点、faq_lookup | **强** | 退出码 3 无法消费，跑测结论不完整 |

**第 2、4、5、6 项破坏时静默**——不报错、退出码照样 0，只是产出悄悄少一块或算错。
改这几项必须两侧同时动，并在真机上冷启动复跑一遍真实算子，光看退出码证明不了对。

## `run_state.json` 的字段约定

```json
{"created_at": "...", "updated_at": "...",
 "ops": {"<op>": {"phase1": {"status": "...", "attempts": 1,
                             "duration_s": 920.5, "log_path": "..."}}}}
```

`status` 的合法取值由 cann-ops-run 的 `VALID_STATUSES` 定，写别的值会被拒。
**探索结论不写回这里**——`EXPLORED_*` 不是合法状态，它只落 `explorations/<op>.md`。

`phase1` 这个键是历史命名（示例跑测的旧名），保持兼容，不要改。

## 改动纪律

- 改任何一项前后，在仓根各跑一次 `python3 -m pytest`
- 改强契约（1、2、3、7）要两侧同时动
- 改静默项（2、4、5、6）**必须冷启动跑一次真实算子**：新开一个空 CWD，
  按链路顺序跑一遍，看下游产出是否真的对——退出码 0 不构成证据
- 新增产物落点先加进上面的目录树，再写代码
