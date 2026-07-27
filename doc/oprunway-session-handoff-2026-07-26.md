# OpRunway 会话交接 · 2026-07-26

> 本文是 2026-07-26 建立、2026-07-27 续写的最新入口。旧
> `oprunway-session-handoff-2026-07-25.md` 仅作历史材料，不得覆盖本文的最终真机事实。

## 1 · Median 最终验收结论

- Median + PR6429 在 A3 的最终确定性裁决为 **PASS**。
- 精度：60/60 PASS，8 类任务书 dtype 全覆盖；无 fail、uncertain、risk、gap 或契约问题。
- 性能：从同一份精度 caseset 选择 40 例，40/40 获得同机、同输入、同为
  `kernel_only` 的 DUT/baseline 有效数据，40/40 达到任务书 `ratio >= 1.0`，无 blocked。
- A3 大小 shape 分类：
  - small（全部输入物理载荷 `<= 256 KiB`）：14/14 达标，聚合 speedup 5.3846；
  - large（全部输入物理载荷 `> 256 KiB`）：26/26 达标，聚合 speedup 8.4507；
  - shape overall：40/40 达标，聚合 speedup 3.4817；
  - 最慢一例的逐 case speedup 仍为 1.7459。
- `perf_report.json` 顶层 `overall_speedup=44.8822` 是 cannbot 兼容的总耗时加权口径；
  `shape_overall.speedup=3.4817` 是 shape 视图的中位数聚合，两者含义不同，不混写。
- 根产物 `evidence.json`、`baseline.json`、`perf_report.json`、`verdict.json`、
  `acceptance.json` 均已刷新；Task 1、Task 2、Task 3 三道证据门全部通过。

## 2 · 任务书与性能标杆的最终理解

任务书要求：

> 相比于 aclnnMedian、aclnnMedianDim 的小算子拼接版本性能不劣化。

用户已确认该“小算子拼接版本”等价于 Torch 对应接口。因此本任务直接按任务书比较：

- baseline：同机 stock `torch_npu` 执行 `torch.median`；
- DUT：独立调用 PR 构建出的 ACLNN 两段式接口；
- 两侧使用同一 case 输入、同一设备和相同 `kernel_only` profiler 口径；
- baseline 子进程精确移除 DUT custom OPP 路径，避免同名实现污染；
- DUT 继续用符号定义方 provenance 证明命中 PR 产物。

无需重复证明 Torch 包装与小算子拼接等价，也无需为了“入口形式相同”额外建设 C++ Extension。
性能 baseline 是任务书指定的语义参考；DUT 是被测实现，两者本来就不要求处在同一 API 层。

## 3 · 通用性能 case 规则

- 性能输入必须从同一份精度 caseset 选择，不能另造一套性能输入。
- 只有本轮精度裁决已通过的 case 才可进入性能比较。
- A3 以全部输入的物理字节之和分类：`<= 256 KiB` 为 small，`> 256 KiB` 为 large。
- 分类只用于分组统计；small 也必须真实采集，不恢复 `trivial-met` 或按 numel 免测。
- 单元素输入继续完整参加精度；本任务以通用
  `perf.case_selection.min_total_input_elements=2` 排除无法形成同口径 device-kernel 比值的退化性能点。
- cannbot 冻结 Median 性能集可作规则参考，不能逐例照抄：它只有按维接口，任务书还要求全局接口。
  当前 40 例是在任务书轴上从 60 例精度集选出的 performance-pass 子集。

## 4 · 本轮源码与工具检查点

A3 隔离 PR 源码保留以下三个提交，**不要回退**：

- `4fbaa74f7`：global value-only 路径；
- `e215fa176`：global int64 reduction 并行化；
- `36e5211f8`：按每核实际行数缩小 small-row 工作区。

第三项是按 dtype、对齐和每核行数派生 K 值的通用策略，不含 case 或算子身份特判；它把
`float32[4,6], dim=-1` 从约 18 μs 降到约 2.5 μs，并在最终 60 例精度复跑后保持全绿。

OpRunway 通用侧本轮补齐：

- torch baseline 的字段驱动 keyword group；
- baseline 环境隔离、单侧超时和进程组回收、逐 case 原子 checkpoint；
- strict retry merge；
- 性能选例/大小分类/失败明细的三级证据对账；
- `finalize_clean_acceptance.py`：只在精度、性能和三级证据均为 clean pass 时原子写
  `acceptance.json`，任一条件不满足即 fail-closed 且不覆盖旧裁决。

## 5 · 真机验证与空间

- 最终源码精度：60/60 PASS。
- 最终性能：40/40 有效且达标。
- OpRunway 全量远程回归：
  `1505 passed, 10 skipped, 14 warnings, 474 subtests passed`。
- finalizer 相关回归：
  `319 passed, 14 warnings, 29 subtests passed`。
- 只清理了本用户容器 `/tmp` 中可确认属于旧 pytest/perf/msprof 的临时目录；
  `/tmp` 从约 18G 降至首次清理后 322M；完成测试并做最终清理后为 23M。
- 最近复核：`/home/liangyuansheng` 7.2G（其中本轮 `/work/run` 6.5G）、根盘可用约 19G。
  不得清理其他用户目录。

## 6 · Git 与下一步

- 本轮只做本地检查点 commit，不 push、不 merge。
- 工作树另有 bureau compile、图稿、旧 C++ Extension 实验等跨轮改动；选择性提交，禁止整树回退。
- `cpp_extension_codegen.py`、witness 及相关 fixture 是被最终路线淘汰的实验，不纳入本轮验收提交。
- reports 是 ignored 真机产物；最终事实以远端隔离报告目录中的根 JSON 和本文数字为准。
- 后续若要发布，按仓规对本次待发布代码走一次 audit→fix→verify、散文走独立 Codex 审，再由用户决定
  是否 push。

## 7 · 不要再绕回去的决定

- 不为每份新任务书重复证明其明确指定 baseline 与某个框架包装的等价性。
- 不把精度 oracle、性能 baseline 和 DUT 调用入口混成同一概念。
- 不按算子名写通用工具特判。
- 不用小 shape 免测、减 case 或放宽阈值换取性能 PASS。
- 不把 collector 有数据、部分 case 达标或 `needs_review` 写成验收通过。
- 不回退三个已经通过全量精度与性能复验的源码优化提交。
