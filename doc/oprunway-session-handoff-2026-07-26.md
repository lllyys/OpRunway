# OpRunway 会话交接 · 2026-07-26

> 本文是 2026-07-26 建立、2026-07-27 续写的最新入口。旧 `oprunway-session-handoff-2026-07-25.md` 是历史暂停点，包含已经被后续真机结果推翻的状态，不得作为当前事实源。

## 1 · 当前结论

- GitHub PR #10「解决了 e2e 耗时问题」已合并，合并提交为 `f8dd1f8`。
- Median + PR6429 最新精度结果为 **60/60 PASS**。
- 固定快照完整 E2E 为 **2101 秒（35 分 01 秒）**：
  - CP-A：157 秒
  - CP-B：615 秒
  - CP-C：173 秒
  - CP-D：1156 秒
- 非真机准备优化没有减少真机 case，没有修改精度/性能阈值、warmup/repeat、timing scope 或裁决链。
- 2026-07-27 已以带 256 KiB 分类的新 caseset 正式复跑；性能维仍为 **BLOCKED**：
  - custom：50/50 有效；
  - `torch_npu` baseline：48/50 有效；
  - 48 对均为同机同口径 kernel-only 数据；
  - 35 对达到 `ratio >= 1.0`；
  - 2 个 BF16、`dim=1` baseline case 报 161002，custom 成功，归为 baseline limitation，不归因 DUT。
  - small：24 planned / 22 scored / 19 达标 / 2 blocked，聚合 speedup 7.5006；
  - large：26 planned / 26 scored / 16 达标 / 0 blocked，聚合 speedup 0.3668；
  - overall：50 planned / 48 scored / 35 达标 / 2 blocked，聚合 speedup 3.4268。

## 2 · 本轮完成的体系优化

- CP-A/CP-B 增加内容寻址的 `source_facts.json`、`case_plan.json` 与 fail-closed `preparation_receipt.json`，输入或依赖漂移时自动失效。
- `gen_cases --dry-run` 增加 durable ledger；正式 caseset、golden、evidence、verdict 和性能结果不缓存。
- `aclnn_py` 增加 CP-C0 静态 preflight，对账 header、symbol、arity、slot 顺序、role 与 ctype。
- `aclnn_py` harness 真机信任门落成代码硬门；收据绑定输入、golden、输出、PR/build/toolkit/SoC/符号及执行环境，不产生算子验收 PASS。
- 性能采集对齐 `msprof CLI + MSTX + task_time CSV`；custom 与 baseline 使用相同 kernel-only scope。
- 彻底移除 `numel < 4096 → trivial-met` 自动免测规则。全部性能 case 都必须真实采集或明确 blocked。
- GPU baseline 回归改为复用类级只读 caseset，减少非真机重复生成。
- 性能整轮硬超时改为 `max(1200, 60 × 实际选中 case 数)`；50 case 真机使用 3000 秒并完成，逐 case 进度会 flush，避免再次只剩 `PERF_FAIL`。

详细流水见 `doc/oprunway-changes-brief.md` 的 2026-07-26 小节。

## 3 · 最高优先级开放项：真实性能未达标与 cannbot 造例差距

任务书要求：

> 相比于 aclnnMedian、aclnnMedianDim 的小算子拼接版本性能不劣化。

2026-07-26 用户进一步明确：这里的“小算子拼接版本”等价于 Torch 对应接口。因此 Median 性能 baseline
恢复为同机 `torch_npu` 执行 `torch.median`；无需再证明等价，也不应改为直接测单个 ACLNN 接口。

- 性能 case 从精度 caseset 选择，且只测精度已通过的 case；
- A3 按输入物理载荷分类：`<= 256 KiB` 为小 shape，`> 256 KiB` 为大 shape；
- 大小分类只用于分组统计，不恢复任何小 case 免测；
- 新标签 caseset 已正式复跑；13 个可评分 case 低于 1.0，另有 2 个 baseline limitation，不能靠聚合 speedup、删 case 或改阈值写成通过。
- 当前 `torch_parity` 只是受控档位与账本护栏，生成器源码明确说明对齐造例逻辑的“批 B”尚未实施；所以当前可以证明性能 case 来自精度 caseset、选择账本和大小分类完整，不能证明 shape/attr 网格与 cannbot 数据集逐例一致。

该问题已同时记录在：

- `plugin/samples/specs/median.spec.json` 的 `perf.torch_baseline` / `case_source` / `shape_classification`；
- `doc/oprunway-todo.md` 的“Median 性能口径确认与大小 shape 分类”。

## 4 · 下一 session 建议顺序

1. 先读仓根 `AGENTS.md`、本文、`doc/oprunway-changes-brief.md` 顶部和 `doc/oprunway-todo.md` 的新 baseline TODO。
2. 读 `doc/oprunway-real-machine-environment.md`，从被 `.gitignore` 忽略的 `.oprunway/real-machine.env` 取得实际连接/容器/路径，并先做只读环境探测。
3. 运行 `git status --short --branch`，确认本轮文档更新是否已经 commit。
4. 先按最新 `perf_collect.json` 把 13 个未达标 case 分成 fp 全局大 shape、int64 全局大 shape、fp 长度 3 的 global 小 shape三组；验收侧不替 DUT 优化，也不改阈值。
5. 若用户决定继续完善 cannbot 造例对标，实现字段驱动的 `torch_parity` 批 B，并重新走 CP-B/C/D；不得把 `repos/cannbot-ops-input` 变成运行时依赖。
6. 以 `perf_compare.py`、`validate_acceptance_state.py` 和 `acceptance.json` 的确定性裁决为准更新报告。

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
