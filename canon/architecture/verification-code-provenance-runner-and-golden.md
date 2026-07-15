---
title: Verification code provenance for runner and golden
updated: 2026-07-15
status: proposed
---

# Verification code provenance for runner and golden

> ⚠ **时态提示**：下文「runner/golden 两处同病」「`oracle_source` 写死 `cpu_ref`」等段落描述的是 **Q9 修复前**状态，留作病理记录。golden 后端已定 torch、`oracle_source` 假常量已止血（见文末「golden 侧已拍板 + 止血」）；**但 golden 的独立交叉核仍未治**——本页核心洞未闭合，故仍 proposed。

验收里「验证被测算子对不对」的东西，本身是 **agent 自撰、未独立验证的代码**——runner 与 golden 两处同病：

- **runner**（agent 生成的 C++ 泛型壳）——至少有一条纪律级独立交叉核（手算小 case + custom-exe vs 内置 TBE-exe 对照，见 [[Root-cause decoupling before attribution]]）。
- **golden**（agent 写死的 numpy，`plugin/acc-common/gen_cases.py` 的 `GOLDEN`，见 [[gen_cases GOLDEN hardcodes four elementwise operators]]）——**更深的洞**：golden 是终端 oracle，**没有「给 golden 的 golden」**。公式理解错，无任何独立信号能抓；numpy 语义与昇腾算子若有差（边界/NaN/dtype 提升），golden 本身就错，「精度通过」失去意义。

**两处都非机器强制**：runner 的 sidecar 硬门（`.verified.json`）未实现；golden 的来源字段 `oracle_source` 写死假常量 `cpu_ref`（[[oracle_source is a hardcoded constant not a recorded fact]]，fail-open）。evidence 的 `ascendoptest_bool` 交叉核 10/10 全 null。

**厘清（勿误伤 runner）**：runner 本身**无 bug**——2 类 dtype（fp32/fp16）是成文 Track C 边界（[[Real-NPU runner supports only float32 and float16]]），对 bf16/int32 具名 fail-closed 拒绝、不静默误转；「扩 runner 到 4 dtype」徒劳（`_NP` Track C 门先拦，补的 C++ 是死代码）。agent 当次也没真去扩——识破耦合、荐不扩、交用户，处置是范本。真正该治的是**共有病根**：把验证被测物的代码放进 agent 自撰、未独立交叉核的实现里。

**方向（未拍板深浅）**：成文禁止 agent 在验收跑测中临时扩 dtype / 改 runner 分支 / 改 `_NP`；短期落 runner sidecar 代码硬门 + dtype 能力单源化；golden 那侧更深——`oracle_source` 假常量改真来源、接上独立交叉核。`generated_harness` 用 agent 生成 harness 这个模式本身 canonical（[[generated_harness responsibilities]]，改定义走 ADR）；但「agent 手填 C++ 四槽」这个具体实现在无 tier 的 skill 文件、换模板库不动 ADR。

**golden 侧已拍板 + 止血（2026-07-15，Q9）**：① **golden 来源已定**——不再是「agent 照理解自撰 numpy」，而是**任务书指定的测试方法测出来的值**为准（[[Golden and precision standard come only from the task-doc-specified method]]），内置四算子后端**固定 torch(CPU)**（[[Golden is fixed to torch on CPU for determinism]]）；② `oracle_source` **假常量已治**——删两处写死 `cpu_ref`、据 `golden_source` 据实映射、加一致门（[[oracle_source is a hardcoded constant not a recorded fact]]、[[Machine-verifiable acceptance gate]]）。**仍未治**：golden 的「独立交叉核」（给 golden 的 golden）与 runner sidecar 硬门 —— 本页核心洞（终端 oracle 无独立信号）**未闭合**，故留 proposed。

**Sources.** [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]（2026-07-13：Q5+Q9 合并为「验证代码 provenance」架构议题），[[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：Q9 golden 来源拍板 torch + oracle_source 止血）
