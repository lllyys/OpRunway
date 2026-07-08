---
name: acc-spec
description: 把算子任务书（md 本地路径或链接）+ PR 链接，抽成中立的 <op>.spec.json（OpRunway 验收流水线 Layer 0 契约）。当你拿到「算子任务书 + 对应 PR」要开始 NPU 算子验收、或需要从任务书生成 spec 时用。一份任务书含多个算子时产出多份 spec。规则由真实社区任务书语料归纳、经三个已建 spec 验证。
---

# acc-spec — 任务书 + PR → spec.json

**输入**：算子任务书（`md` 本地路径 **或** `http(s)` 链接）+ PR 链接。
**输出**：一份或多份 `<op>.spec.json`（Layer 0 中立契约）+ 每份显式 `task_pr_gaps`。
**边界**：这步只把「任务书/PR 里有什么、缺什么」**确定性**地落成 spec，**不做验收判定**（判定在 `validator.py`）。缺项落 gaps，**不臆造**。

## 步骤

1. **取材**（确定性活，下放给脚本）：
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/acc-common/fetch_source.py --taskdoc <路径|链接> --pr <PR链接> --out <workdir>
   ```
   产出 `<workdir>/task_doc.md`（任务书原文）+ `<workdir>/pr_facts.json`（op / 目标仓·目录 / merged / 改动文件 / **`key_files`：算子自带 example(`test_aclnn_*.cpp`) + `*_def.cpp`**）。无 `--pr` 时只有任务书。

2. **抽 spec**（本 skill 的 NL 判断核心）：读 `task_doc.md` + `pr_facts.json`，按 `references/taskdoc-to-spec.md` 的**字段映射表**逐字段抽。四个要点（都在 ref 里，最易错）：
   - **dtype 全集**：任务书『支持所有类型』模糊 → 读 `pr_facts.key_files` 的 `*_def.cpp` `DataType({...})` 得**任务全集**；但 **`params.dtype` 只填当前 pipeline 支持的子集**（fp32/fp16），**不支持的 dtype 不进 `params.dtype`（否则 gen_cases/runner 崩），全集与不支持项入 `task_pr_gaps`**（详见 ref §4）。任务书显式 dtype 表 > PR op_def。
   - **verify_mode**：三值决策树 behavioral/exact/numerical（ref §2）——任务书从不直写，靠输出 dtype+运算性质推断。
   - **precision.threshold**：必落数字（exact→0；numerical→主 dtype 默认值 fp16≈1e-3 等，标『(推断/待工具核实)』）——23/23 任务书不给数值。
   - **runner 锚定线索**：从 `pr_facts.key_files` 的 `test_aclnn_*.cpp` 读**算子实测用的 aclnn 入口 + 输入 dtype**，记进 spec 供 ③ 生成 runner——**别凭 header 猜**（Equal 曾因猜错入口/dtype 翻车）。

3. **多算子**：一份任务书含 N 个算子 → N 份 spec（共享字段复用 + 逐算子独立，ref §5）。

4. **自检**：按 ref §7 校验（verify_mode 合法、numerical 有 threshold、params 有 out、exact⇒threshold=0、add_dtype⇒dtypes_added⊆params.dtype…）。

5. **落盘**：写 `${CLAUDE_PLUGIN_ROOT}/acc-common/specs/<op>.spec.json`（或用户指定目录）。所有缺口/矛盾/推断落 `task_pr_gaps`，推断项标 `(推断)`。向用户复述：产了几份 spec、关键字段、gaps。

## 约束（跨运行时可移植）

- **全程中文**；只据任务书/PR 原文，不臆造；缺项落 `task_pr_gaps` 不静默。
- **确定性活（取材/fetch）在 `fetch_source.py`，本 skill 只做 NL 判断**——换运行时(Codex/Antigravity)只换调用壳，`fetch_source.py` + `references/` 不动。
- **任务书是验收权威**；PR 用于补 dtype/example/目标目录，**不代表『验收过了』**。
- 抽完的 spec 交下游：`gen_cases.py`(Task1) / `run_workflow.py`(Task2/3)；或由 `op-acceptance` agent 继续编排 ③-⑥。

**详规见** `references/taskdoc-to-spec.md`（目标 schema · 字段映射 · verify_mode 决策树 · threshold 兜底 · 多算子拆分 · GPU 移植特例 · 自检清单）。
