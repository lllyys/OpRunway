# OpRunway 会话交接 · 2026-07-26

> 本文是 2026-07-26 收尾时的最新入口。旧 `oprunway-session-handoff-2026-07-25.md` 是历史暂停点，包含已经被后续真机结果推翻的状态，不得作为当前事实源。

## 1 · 当前结论

- GitHub PR #10「解决了 e2e 耗时问题」已合并，合并提交为 `f8dd1f8`。
- Median + PR6429 最新精度结果为 **60/60 PASS**。
- 固定快照完整 E2E 为 **2101 秒（35 分 01 秒）**：
  - CP-A：157 秒
  - CP-B：615 秒
  - CP-C：173 秒
  - CP-D：1156 秒
- 非真机准备优化没有减少真机 case，没有修改精度/性能阈值、warmup/repeat、timing scope 或裁决链。
- 性能维仍为 **BLOCKED**，但已经产出有效性能数据，不再是“零数据”：
  - custom：50/50 有效；
  - `torch_npu` baseline：48/50 有效；
  - 48 对均为同机同口径 kernel-only 数据；
  - 35 对达到 `ratio >= 1.0`；
  - 2 个 BF16、`dim=1` baseline case 报 161002，custom 成功，归为 baseline limitation，不归因 DUT。

## 2 · 本轮完成的体系优化

- CP-A/CP-B 增加内容寻址的 `source_facts.json`、`case_plan.json` 与 fail-closed `preparation_receipt.json`，输入或依赖漂移时自动失效。
- `gen_cases --dry-run` 增加 durable ledger；正式 caseset、golden、evidence、verdict 和性能结果不缓存。
- `aclnn_py` 增加 CP-C0 静态 preflight，对账 header、symbol、arity、slot 顺序、role 与 ctype。
- `aclnn_py` harness 真机信任门落成代码硬门；收据绑定输入、golden、输出、PR/build/toolkit/SoC/符号及执行环境，不产生算子验收 PASS。
- 性能采集对齐 `msprof CLI + MSTX + task_time CSV`；custom 与 baseline 使用相同 kernel-only scope。
- 彻底移除 `numel < 4096 → trivial-met` 自动免测规则。全部性能 case 都必须真实采集或明确 blocked。
- GPU baseline 回归改为复用类级只读 caseset，减少非真机重复生成。

详细流水见 `doc/oprunway-changes-brief.md` 的 2026-07-26 小节。

## 3 · 最高优先级开放项：性能 case 重生成与重跑

任务书要求：

> 相比于 aclnnMedian、aclnnMedianDim 的小算子拼接版本性能不劣化。

2026-07-26 用户进一步明确：这里的“小算子拼接版本”等价于 Torch 对应接口。因此 Median 性能 baseline
恢复为同机 `torch_npu` 执行 `torch.median`；无需再证明等价，也不应改为直接测单个 ACLNN 接口。

- 性能 case 从精度 caseset 选择，且只测精度已通过的 case；
- A3 按输入物理载荷分类：`<= 256 KiB` 为小 shape，`> 256 KiB` 为大 shape；
- 大小分类只用于分组统计，不恢复任何小 case 免测；
- 已有 48 对 `torch_npu` ratio 是真实性能数据，但新标签进入 caseset 后仍须重新生成并按确定性链复核。

该问题已同时记录在：

- `plugin/samples/specs/median.spec.json` 的 `perf.torch_baseline` / `case_source` / `shape_classification`；
- `doc/oprunway-todo.md` 的“Median 性能口径确认与大小 shape 分类”。

## 4 · 下一 session 建议顺序

1. 先读仓根 `AGENTS.md`、本文、`doc/oprunway-changes-brief.md` 顶部和 `doc/oprunway-todo.md` 的新 baseline TODO。
2. 读 `doc/oprunway-real-machine-environment.md`，从被 `.gitignore` 忽略的 `.oprunway/real-machine.env` 取得实际连接/容器/路径，并先做只读环境探测。
3. 运行 `git status --short --branch`，确认本轮文档更新是否已经 commit。
4. 在远程 NPU 容器跑新增的性能 case 分类回归并重新生成 Median caseset。
5. 检查所有性能 case 同时带精度维，且 256 KiB 边界按物理 dtype 字节数正确分类。
6. 使用同一份精度 caseset 和 `torch_npu:torch.median` 重跑性能。
7. 以 `perf_compare.py`、`validate_acceptance_state.py` 和 `acceptance.json` 的确定性裁决为准更新报告。

## 5 · Git 与工作区注意事项

- GitHub `main` 在本轮合并后为 `f8dd1f8`；GitCode 镜像在收尾检查时仍落后，是否同步须用户明确授权。
- 收尾时存在若干 2026-07-25 的未跟踪机械 logbook stub，部分含本机 transcript 绝对路径；不要原样提交。
- 旧 2026-07-25 handoff 含环境专属主机名/路径且状态过时；不要把它当成当前交接，也不要未经脱敏直接提交。
- 本地只编辑源码、维护 Git 与知识记录；build、pytest、生成用例和验收 compute 仍全部在远程 NPU 容器执行。
- 私有主机名、远端路径和凭据继续只通过 `OPRUNWAY_*` 环境变量传入，不写进仓。

## 6 · 不要回退的决定

- 不从真机 case、trivial/numel 免测或放宽阈值下手优化时间。
- 不把 CPU torch 的精度 oracle 与性能 baseline 混为一谈。
- 不把 baseline limitation 归因成 DUT 失败。
- 不把 `covered`、collector 有数据或部分 case 达标写成整体验收通过。
- 不按算子名在通用工具代码中增加特判。
