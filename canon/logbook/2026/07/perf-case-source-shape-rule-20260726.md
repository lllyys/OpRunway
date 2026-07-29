---
title: session perf-case-source-shape-rule-20260726 · 2026-07-26
updated: 2026-07-26
status: logbook
session: perf-case-source-shape-rule-20260726
transcript: ""
---

## [2026-07-26T23:00:00-04:00] session perf-cas — 性能基线语义与通用 shape 分类纠正

**Intent.** 继续解决 Median 性能标杆与任务书解释不一致，并把性能用例来源、大小 shape 分界从单算子口径提升为通用规则。

**Decisions.**
- 用户明确确认：Median 任务书所称 `aclnnMedian` / `aclnnMedianDim` 小算子拼接版本等价于 Torch 对应接口。因此 Median 性能 baseline 应为同机 `torch_npu` 执行 `torch.median`；不需要再做等价性证明，也不应改为直接测单个 ACLNN 接口。这纠正了上一份 minute 中“Median 切到 aclnn_builtin”的判断，并要求后续 compile 复核 [[Direct ACLNN performance baseline]] 中的 Median 专属表述。
- 性能 case 必须取自同一份精度 caseset，是通用规则，不是 Median 特例。带性能维的 case 必须同时带精度维；精度未通过的 case 不进入性能比较。该决定要求纠正 [[Performance reuses precision inputs with a trivial-met exemption]]：复用精度输入仍成立，但 `trivial-met` 已废除。
- 性能 case 按目标硬件的 UB 单次承载能力分小 shape 和大 shape，也是通用规则。A3 上按全部输入的物理载荷字节数求和，`<= 256 KiB` 归小 shape，`> 256 KiB` 归大 shape；边界计入小 shape，因为可一次搬入 UB。
- 大小 shape 标签只用于分组统计，不产生免测、不自动放宽 `target_ratio`，也不恢复已经删除的 `numel < 4096 → trivial-met`。
- 通用 `aclnn_builtin` 能力保留，供任务书实际要求直接 ACLNN baseline 的其它任务使用；不能因为该能力存在就把 Median 强行路由过去。

**Changes.**
- `plugin/samples/specs/median.spec.json` (updated) — 恢复 `torch_npu:torch.median`，增加精度 caseset 来源与 A3 256 KiB 分类契约。
- `plugin/acc-common/gen_cases.py` (updated) — 通用校验性能 case 必须复用精度 case，并按输入物理字节打小 shape 或大 shape 标签；缺规则 fail-closed。
- `plugin/acc-common/perf_compare.py` (updated) — 从 caseset 的既有分类元数据生成 `by_shape_class` 汇总，只读展示、不参与裁决。
- `plugin/acc-common/catlass_adapter.py` (updated) — 独立 builder 同步执行性能 case 属于精度 case 的规则与字节分类。
- `plugin/samples/specs/*.spec.json`、测试 fixtures 与 archive specs (updated) — 当前 A3 性能 spec 统一声明通用规则。
- `AGENTS.md`、plugin agents/skills、TODO、handoff 与改动简表 (updated) — 同步新口径并保留历史纠正链。
- `plugin/acc-common/test_gen_cases_perf_shape_classification.py` 及相关测试 (new or updated) — 钉住 262144 bytes 边界、bf16 物理字节、fail-closed 与 Median baseline。

**Open threads.**
- 依仓规，pytest、用例重生成和验收 compute 必须在远程 NPU 环境执行；本轮尚未获得远程执行授权，因此新增规则与迁移尚未跑测试。
- 本 minute 尚未 compile；旧 verified dossier 与 `_verify.json` 会因 Median spec 改回 Torch baseline 而需要重新蒸馏、处理 stale/contested 状态。
- Median 仍需用重新生成的 caseset 在 A3 真机重跑；两个 BF16 baseline 161002 case 继续按 baseline limitation 挂起，不归因 DUT。

**Source.** transcript ``
