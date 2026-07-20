---
title: Real-NPU runner supports only float32 and float16
updated: 2026-07-20
status: verified
---

# Real-NPU runner supports only float32 and float16

> ⚠ **标题已被 supersede（2026-07-16）**：真机 runner 现支持 **float32 / float16 / bfloat16** 三种。
> bf16 于 2026-07-16 在 a3 真 NPU 经 **provenance-clean 重建**后验收通过（详见下「Update」段）。标题保留原文
> 仅为不打断既有 `[[…]]` 反链；claim 以本体现行真相为准，改名待 `bureau:review`。

`plugin/acc-common/repo_adapter.py` 的 `new_example`（真机跑测）路径用 `_NP` 白名单限定真机可跑的 dtype。
**当前 `_NP = {float32, float16, bfloat16}`**——bf16 逻辑走「fp32-on-grid」、物理落盘 uint16 位模式（`runner.cpp`
加 `ACL_BF16` dispatch + `gen_cases` 的 bf16 位级 codec）。`int32` / `int16` 仍属 **Track C**：`gen_cases`
造得出用例，但 `runner.cpp` 无对应 dtype 分支、真机跑不了。

`repo_adapter` 对「spec 声明了但 runner 不支持」的 dtype 是 **fail-closed 的**（`dtn not in _NP` 直接
`raise ValueError`），这道防线仍在。真正的洞在上一层：spec **该声明而未声明**时，dtype 集由 `spec` 驱动、无人
拿算子 `op_def` 去核完整性——收窄发生在门的视野之外（同 [[A gate must validate the object that actually takes effect]]）。

三层 dtype 能力对比：`precision_policy` 阈值表 15 种 → `gen_cases` 可造 5 种（fp32/fp16/int32/int16/bf16）
→ **真机 runner 3 种（fp32/fp16/bf16）**。例：IsClose 的 op_def 声明 4 种输入 dtype（fp32/fp16/bf16/int32），
真机跑得了前 3 种（int32 仍 Track C），见 [[Target hardware and dtype set are determined per operator from taskdoc and op_def]]。

**Update 2026-07-16（supersedes 标题）.** bf16 从「runner 代码已加 dispatch、但验收级未证」转为**验收级已测**：在
a3（CANN 9.0.1 容器、真 A3 NPU）从任务书正源 `experimental/math/is_close`(Atlas A2/A3) **provenance-clean
从源重建 opp** 后重验——opp stamp `op_src=experimental/math/is_close`、ophash 与真源逐字节 sha256 一致、Task2
裁决=pass（27 用例含 9 个 bf16、0 fail）、三门 PASSED；provenance 门 fail-closed 三情形实测通过。该跑**整体**判
NEEDS_REVIEW·requires_human_cp（因用**插件样例 runner** 走 route B、按当时既定纪律挂人核，exit 2；该「插件样例 runner→人核」路径已于 2026-07-20 退役、改 fail-closed，见 [[Runner is an output of the engine not a component]]）——这与 bf16 的
**dtype 覆盖正交**、不动摇「bf16 精度全过」的结论：故 `samples/specs/isclose.spec.json` 把 bf16 从 `dtype_deferred`
转 `dtype_tested`（int32 仍 Track C）。绑源机制见
[[opp is provenance-bound to the op source with a fail-closed gate]]；「实测跑过≠验收了正确的东西」的教训亦在其中。

**Verified.** 2026-07-16 核、2026-07-20 重录 runner 路径（runner 移出引擎），均存在：`plugin/acc-common/repo_adapter.py`（`_NP` 含 `bfloat16`）、
`samples/runners/oprunway_isclose_runner.cpp`（`ACL_BF16` dispatch；2026-07-20 由 `new_example/` 迁此、内容/hash 不变）、
`samples/specs/isclose.spec.json`（`dtype_tested` 含 `bfloat16`、`int32` 挂 `dtype_deferred`）。

**Sources.** [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]，[[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：Q9 重核 `_NP` 未变；2026-07-16：bf16 扩 runner + provenance-clean 验收 → `_NP` 加 `bfloat16`、claim 更新；2026-07-20：runner 移出引擎 → Verified 路径 `new_example/` → `samples/runners/` 重录、claim 不变）
