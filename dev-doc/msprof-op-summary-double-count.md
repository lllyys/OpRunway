# msprof op_summary 双份计数 bug（accept 性能通路）

状态：**已定位，待修。** 记录人：_robin（2026-09-11 离线日志排查）。
影响 skill：`repo-task-blas-accept` 的性能采集通路（A4），以及它从
`repo-task-blas-case-gen` 渲染出的 `verify_performance.py`。

## 一句话

accept 的 msprof 通路把**同一次采集导出了两遍**，两份 op_summary 落进同一个
`mindstudio_profiler_output/`，而 `parse_op_summary` glob 后**不去重地把两份都累加**，
导致同一个 kernel 被数两遍——`launches` 和 `kernel_us` 都翻倍，性能比值腰斩，好算子被判假 FAIL。

## 触发机制（两个缺陷叠加）

1. **导出两遍。** perf-protocol 规定的采集序列是先 `msprof --application=... --output=<dir>`、
   再 `msprof --export=on --output=<dir>`，并假设第一步只产 `task_time + sqlite`、不产
   op_summary（协议标注「依据：实测 A3，CANN 9.0.1，ascend910_93」）。这个假设换平台就不成立：
   在 910B3 上 `--application` 那步**自己就分析并导出了** op_summary，随后显式 `--export=on`
   **又导一遍**，两次都写进同一个 `mindstudio_profiler_output/`。
2. **解析不去重。** `parse_op_summary` 的 glob 是
   `PROF_*/mindstudio_profiler_output/op_summary_*.csv`，匹配到几份就累加几份，
   没有按内容哈希或文件去重，`launches` 只记录保留行数、不参与任何校验（协议明写
   「不得再除以 launches」）。于是两份 op_summary 的 kernel 行被全部加进去。

两者叠加 = 双份计数。哪一步是主责取决于平台的 msprof 行为，但**解析层缺去重守卫**是
无论如何都该补的那道门。

## 证据（Ctpmv / 910B3，2026-09-11 那次验收）

来源：任务方回传的 `aclblasCtpmv/performance_ctpmv-20260911-t1.json` 与
`aclblasCtpmv/prof/TC_PF_*/r*.{log,export.log}`。

- **launches = [2,2,2,2,2] 全 200 例一致**，无一例外，与尺寸无关（n=1 到 n=2048 都是 2）。
- **导出确实两遍**：`r1.log`（`--application` 阶段，02:57:43）与 `r1.export.log`
  （`--export=on`，02:57:46）各有一次完整 "exporting summary output_file ... stored in
  .../mindstudio_profiler_output"，写的是同一个 PROF 目录。
- **kernel_us ÷ 2 逐条吻合同族 910_93 的单份值**（a3 实测，Release）：

  | n | 910B3 kernel_us | ÷2 | a3(910_93) 单 launch |
  | --- | ---: | ---: | ---: |
  | 512 | 46.44 | 23.22 | 22.88 |
  | 1024 | 88.52 | 44.26 | 45.38 |
  | 2048 | 136.72 | 68.36 | 68.24 |

  同一算子在 910_93 上 launches=1、910B3 上 launches=2 且减半正好对上——「2」是同一个
  kernel 被数两遍，不是真两个 kernel。

- **去重后重算裁决**（kernel÷2、ratio×2）：**PASS 61 → 192，FAIL 139 → 8**。
  剩 8 例全是最小尺寸（TC_PF_1005–1010 等，n≤~32），去重后 ratio 也在 0.45–0.80，
  是小算子启动开销占比高的边界情况，待 aclrtEvent 复核，不影响「大面积 FAIL 是假象」的结论。

同型现象此前在 **cgeru / 950** 已见：`launches=[2,2,2,2,2]`，去重后回 `[1,1,1,1,1]`，
同卡 aclrtEvent 200/200 PASS。即这是**跨平台**（950 与 910B3 都中）的通路缺陷，不是偶发。

## 机制已逐字节坐实（a3 穿刺，2026-09-11）

在 a3（910_93）容器内实跑 ctpmv arch22：collect + export 后 `mindstudio_profiler_output/`
里出现**两份 op_summary，md5 完全相同**（`d700c354…`，各 12 行），批量跑与单发跑都如此。
即"`--application` 自动导 + `--export=on` 再导 → 两份相同文件 → glob 全累加"这条机制**已直接见到文件、
逐字节坐实**，不再是推断。

仅剩的边界：Ctpmv/910B3 **那一次**的 op_summary 原文仍未回传（`r*/` 目录已空），所以 910B3 那次
是"同工具同流水线的强推断"（launches=2 + ÷2 对齐 910_93），而非该次文件的逐字节直证——但机制本身
已由 a3 钉死。

## 修复方向（实施时按 codex-review 七维评估，勿照单全收）

1. **解析层去重（优先，平台无关）。** `parse_op_summary` 对匹配到的 op_summary 去重——
   按文件内容哈希，或只认单一 op_summary；发现多份且内容相同时取一份，内容不同时
   fail-closed 报异常而非静默累加。
2. **采集层不重复导出。** 别假设 `--application` 不导出：要么去掉冗余的 `--export=on`，
   要么先探测 `--application` 是否已产 op_summary，已产则跳过第二次。
3. **补机械门。** `launches` 不能只记不校验——加一条「同一 case 的 launch 数应稳定、
   且与已知的每调用真实 launch 数一致」的 sanity，异常时裁 NO_KERNEL/环境错误而非 FAIL，
   把静默的双份计数升级为显式失败。

改这些属对外性能裁决口径 + 核心解析逻辑，按 `codex-review.md` **无条件过一轮 checkpoint 评审**；
且改 skill 须走 skill-best-practices + skill-creator 那道门。本条只做记录，未动 skill。

## 关联

- 这只是 accept msprof 通路诸问题里最易量化的一个；同通路的其它缺口还有：采集前崩
  （基线重复键、枚举错配、默认采集参数不足产 0 op_summary）、冷进程逐次采样与
  「warmup + 热循环 >50 次取平均」口径不一致、极慢、msprof 绝对准确性从未对 aclrtEvent
  验证过。这些各自另记/另立项，不在本条范围。
- 另一个已确认的 skill 缺陷（默认 msprof 调用缺 `--ai-core=on --task-time=on`，在
  a3/CANN 9.0.1 上产 0 op_summary → NO_KERNEL）见 todo 同批条目。
