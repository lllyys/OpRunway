---
title: Real-NPU runner supports only float32 and float16
updated: 2026-07-15
status: verified
---

# Real-NPU runner supports only float32 and float16

`plugin/acc-common/repo_adapter.py` 的 `new_example`（真机跑测）路径用 `_NP = {"float32", "float16"}`
限定真机可跑的 dtype——**真机 runner 当前只支持这两种**。`int32` / `int16` / `bfloat16` 属 Track C：
`gen_cases` 造得出用例，但 `runner.cpp` 无对应 dtype 分支，真机跑不了。

`repo_adapter` 对「spec 声明了但 runner 不支持」的 dtype 是 **fail-closed 的**（`dtn not in _NP` 直接
`raise ValueError`），这道防线是好的。真正的洞在上一层：spec **该声明而未声明**时，`gen_cases` 的
dtype 集由 `spec.params[].dtype` 驱动，没人拿算子 `op_def` 去核对 spec 的完整性——收窄发生在门的视野之外
（同 [[A gate must validate the object that actually takes effect]]）。

三层 dtype 能力对比：`precision_policy` 阈值表 15 种 → `gen_cases` 可造 5 种（fp32/fp16/int32/int16/bf16）
→ **真机 runner 仅 2 种**。例：IsClose 的 op_def 声明 4 种输入 dtype（fp32/fp16/bf16/int32），真机只跑得了前 2 种
（此 4 种统计源在 `repos/` 内、gitignore 不入库，属 proposed，见 [[Target hardware and dtype set are determined per operator from taskdoc and op_def]]）。

**Verified.** `plugin/acc-common/repo_adapter.py`（2026-07-15 重核，指纹匹配）：`_NP = {"float32"…"float16"…}`、
未支持 dtype `raise` 均存在。本会话 Q9 改的是同文件的 `oracle_source` 映射（`cpu_ref` 常量 → 据 `golden_source` 据实映射）与
`_deploy.tgz` 打包位置，**`_NP` Track C 门未动**、claim 不变；指纹重录 `_verify.json`。

**Sources.** [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]，[[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：repo_adapter 因 Q9 改动重核、`_NP` 未变）
