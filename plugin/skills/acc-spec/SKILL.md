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

2. **抽 spec**（本 skill 的 NL 判断核心）：读 `task_doc.md` + `pr_facts.json`，按 `references/taskdoc-to-spec.md` 的**字段映射表**逐字段抽。
   ⚠ **产 spec 阶段只读 `task_doc.md` + `pr_facts.json`（+ 空模板 `acc-common/spec_schema_template.jsonc`），禁读任何 `.spec.json`（含 `samples/specs/` 的真样例）**——真样例已迁 `samples/specs/`、只作人读参考，产 spec 时**不得查阅同名算子样例**（被测算子恰好在样例里时=先看到同一道题的标准答案，软污染）。看结构看空模板、不看真样例数值。
   四个要点（都在 ref 里，最易错）：
   - **dtype 全集**（⚠ 绝不来自被测 PR）：来源 = **任务书显式表/规格 > 原 TBE 算子信息库（独立于被测 PR）> 问用户**；**PR 的 `*_def.cpp` op_def 只作对照**——PR 声明 < 任务书全集 → 记 `task_pr_gaps`（Fmod 式缩 dtype），**绝不当全集权威**。全新算子无 built-in 条目 / 独立源暂未接通 → **问用户**、**不回退读 PR**。`params.dtype` 只填**当前 pipeline 支持且该算子真机可验收**的子集（fp32/fp16 稳定；**bf16 的 runner dispatch/codec 已接入、mock 已验，但须逐算子确认真机 kernel 支持 + policy 可判 + 输出语义**——bf16 numerical 且非精确白名单算子 gen_cases 会 fail-fast、真机 kernel 未证实/被阻塞时一律走 deferred、不入 `params.dtype`；int 仍 Track C），**不支持的 dtype 不进 `params.dtype`（否则 gen_cases/runner 崩），全集与不支持项入 `task_pr_gaps`**（详见 ref §4）。
   - **dtype 覆盖门字段**（Q7，ref §1 dtype_required/dtype_tested 行）：`dtype_required`=任务书**权威全集**（来源同上：任务书表 > 信息库 > 问用户；全集未知/信息库未接通→字符串 `"needs_user"`；legacy 未迁→省略）；`dtype_tested`=实测子集（gen_cases 据**真实生成的 cases** 归并；门用真实 cases 对账，自报不符即 BLOCKED）。**required 有、tested 无的 dtype 必须逐个记进 `task_pr_gaps` 的 `{kind:"dtype_deferred",dtypes:[…],reason:…}`**——门据此放行（显式挂账≠静默收窄），否则 BLOCKED。IsClose 已核全集={float32,float16,bfloat16,int32}；runner/codec 已接入 bf16（mock 验），但 **bf16 真机验收阻塞在 op-build 环境**，故 IsClose 现 **tested={fp32,fp16}、bf16+int32 均 deferred**（bf16 待真机 op-build 恢复+kernel 验、int32 待 runner int 分支）。
   - **verify_mode**：三值决策树 behavioral/exact/numerical（ref §2）——任务书从不直写，靠输出 dtype+运算性质推断。
   - **precision.threshold**：必落数字（exact→0；numerical→主 dtype 默认值 fp16≈1e-3 等，标『(推断/待工具核实)』）——23/23 任务书不给数值。
   - **precision.standard + tolerance_policy_id**（T5，待散文门）：显式声明平台层标准（`ascendoptest_default / ecosystem_mere_mare / exact / behavioral`，据 oracle+verify_mode 映射，见 ref §1.1 决策树）+ tolerance_policy_id。⚠ 两层 id 别混：**spec 级** `tolerance_policy_id` 是摘要/向后兼容（无 dtype 后缀），**caseset 级** `expected.tolerance_policy_id` 才是门控口径（格式 `standard:dtype`，由 `gen_cases` 派生）。`threshold` 现仅是向后兼容 digest（真门控走结构化 policy，见 ref §3）；per-case 结构化 policy 由 `gen_cases` 按 golden dtype 自动派生，不用手写。`ecosystem_mere_mare` 为 proposed/NOT_SETTLED——单标杆不过记 needs_review、不自动 fail。任务书验收目标明确宽于平台底线时才加可选 `acceptance_policy`。
   - **precision.case_target**（精度用例数，**用户口径优先**）：缺省 50。**运行时 `AskUserQuestion` 问用户「本算子精度用例造多少条？建议 50」**——先 `python gen_cases.py --dry-run <spec>`（plan-only、无 torch）拿该算子 **[强制下限 S, pool_max]** 区间呈现给用户（覆盖 opbase §1.1「不设下限」，用户 2026-07-15 定：数量以用户为准）。写入 `precision.case_target`（**须 ≥1**，0/负→gen_cases fail-fast）。gen_cases 按 opbase §1 覆盖-预算铺到此数。详见 ref『case_target 交互』。
   - **runner 锚定线索**：从 `pr_facts.key_files` 的 `test_aclnn_*.cpp` 读**算子实测用的 aclnn 入口 + 输入 dtype**，记进 spec 供 ③ 生成 runner——**别凭 header 猜**（Equal 曾因猜错入口/dtype 翻车）。

3. **多算子**：一份任务书含 N 个算子 → N 份 spec（共享字段复用 + 逐算子独立，ref §5）。

4. **自检**：按 ref §7 校验（verify_mode 合法、numerical 有 threshold、params 有 out、exact⇒threshold=0、add_dtype⇒dtypes_added 非空且其中 pipeline 支持项已并入 params.dtype、不支持项只记 gap…）。

5. **落盘**：写 **`<ops_root>/<op>/<op>.spec.json`**，其中 `ops_root` = 绝对路径 `$OPRUNWAY_OPS_DIR`（若设），否则 `${OPRUNWAY_WORK_DIR:-$CWD}/.oprunway/ops`。
   ⚠ **落用户工作目录、不写插件安装目录**——真实 `/plugin install` 后插件在 `~/.claude/plugins/cache/…`，升版即整目录换掉、用户产物被冲；
   工程约定要求「零持久化配置；所有产物落用户 CWD」。`repo_adapter.ops_root()` 会拒绝把 `ops_root` 设到插件目录内。
   真 spec 样例已迁出运行时路径到 **`samples/specs/*.spec.json`**（纯人读参考）；**产 spec 阶段禁读任何 `.spec.json`（含 `samples/`）、不得查阅同名算子样例**（软污染，是纪律非文件系统强制）——结构只看空模板 `acc-common/spec_schema_template.jsonc`。
   所有缺口/矛盾/推断落 `task_pr_gaps`，推断项标 `(推断)`。向用户复述：产了几份 spec、落在哪、关键字段、gaps。

## 约束（跨运行时可移植）

- **全程中文**；只据任务书/PR 原文，不臆造；缺项落 `task_pr_gaps` 不静默。
- **确定性活（取材/fetch）在 `fetch_source.py`，本 skill 只做 NL 判断**——换运行时(Codex/Antigravity)只换调用壳，`fetch_source.py` + `references/` 不动。
- **任务书是验收权威**；PR 仅用于补 example/目标目录（被测物锚点）——**dtype 全集只对照、不作来源**（PR 声明 < 任务书全集 → 记 gap），**不代表『验收过了』**。
- 抽完的 spec 交下游：`gen_cases.py`(Task1) / `run_workflow.py`(Task2/3)；或由 `op-acceptance` agent 继续编排 ③-⑥。

**详规见** `references/taskdoc-to-spec.md`（目标 schema · 字段映射 · verify_mode 决策树 · threshold 兜底 · 多算子拆分 · GPU 移植特例 · 自检清单）。
