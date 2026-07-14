# 干净用户测试 · 发现与改法设计

> 来源：2026-07-13 隔离环境测 IsClose 插件（`OpRunway-usertest/work/`，真 A3 端到端跑通、裁决 `PASSED_WITH_RISK`）+ 用户 11 问 + transcript 精读。
> **状态：设计/提案，未实施、未 commit。** 按 CLAUDE.md #1，先抛方案、点头才落地。
> 测试本身的正面结论：隔离基本干净（未读 doc/canon/repos 源码），agent 诚实（裁决逐字引用脚本、主动点破仿真图不足）。以下是**要改的**。

---

## 焦点：spec 样例——"教结构"和"给答案"混在一起（本轮重点，用户点名）

### 问题（有实证）

`taskdoc-to-spec.md:5`（acc-spec 的 reference）原话：

> 目标 schema 见 `plugin/acc-common/specs/{equal,isclose,sign}.spec.json`

这是**文档主动把三份填满答案的真 spec 指给 agent 当"目标长这样"**。后果在 transcript 里坐实：agent 产 IsClose 的 spec 前，读了 `specs/isclose.spec.json`——它验收的算子恰好是样例里那个，读到的是**同一道题的标准答案**（`threshold=0`、`target 0.95`、硬件 `Atlas A2/A3`、语义改造 note），随后"推导"出与样例逐项一致的 spec。虽自产 spec 挂了出处、derivation 真做了，但**无法排除锚定**。

**根因**：两种"参考"被混在同一份文件：
- **参考 schema 结构**（字段名 / 类型 / 格式）——**合理**，agent 需要知道产出长什么样；
- **参考具体数值答案**（这个算子的 threshold 是多少、硬件是啥）——**有害**，是"先看答案再推导"。

`specs/` 在运行时 reference 会翻到的地方，且被文档明确指过去，所以两种参考必然混淆。

### 改法

1. **拆分：空模板 vs 真样例**
   - 新建 `plugin/acc-common/spec_schema_template.json`：**只有字段名 + 类型 + 注释占位符，零真实数值**（`"threshold": "<number: exact→0; numerical→AscendOpTest主dtype默认>"` 这种）。taskdoc-to-spec 的"目标 schema"改指向它。
   - 真 spec 样例移出 `acc-common/specs/` → 顶层 `samples/specs/`，标注"参考案例；**产 spec 时不得查阅同名算子的样例**"。
2. **acc-spec 加硬纪律**：产 spec 阶段**只准读 `task_doc.md` + `pr_facts.json`（+ 空模板）**，**禁止读任何已有 `.spec.json`**。要看格式看空模板，不看答案。
3. **test fixture 改合成假算子**：`test_gen_cases`/`test_catlass` 等对 `specs/isclose.spec.json` 的依赖，换成合成的 `FakeElementwise` fixture（放 `test_fixtures/`），使单测不依赖"仓里有真算子答案"。
4. **catlass_basic_matmul.spec.json**：它是 synthetic demo（无真实 task_doc↔PR），随 catlass adapter 走，本条不动，但一并从 `specs/` 迁到 `samples/`。

> 与用户上一轮"可以带样例"不冲突：样例可以带，但**不躺在运行时会翻到的路径**、且**产 spec 时禁读同名算子样例**。
> 与 runner 侧同源：runner 已用 `runner_source`+`NEEDS_REVIEW` 堵"样例冒充验收"；**spec 侧现在敞开、文档还主动指过去**——这次一并堵。

---

## 其余 10 问的定性与改法（按性质分组）

### A · 真缺陷：私有环境写死默认值（别人拿到插件跑不起来）

- **Q2 机器连接**：`repo_adapter._ne_cfg()` 把 `host="ascend-a3"` 写死默认，无 `AskUserQuestion`、**无"目标机=本机、直接跑"分支**（现在强制 ssh+scp）。
  改：CP-D 前 `AskUserQuestion`「目标机=① 本机直连 ② ssh 远端」；默认值改"必须显式提供，否则报错"，不用私有主机名兜底。
- **Q3 被测仓路径**：`ops: "/home/lys/ops-math"` 写死；**全仓无 git clone**。别人机器没这路径即失败。
  改：被测仓缺失时从 PR 的 gitcode URL 现场浅 clone 到工作目录；路径运行时探测/询问，不给私有默认。

### B · Q4 代码来源 = PR 那版，做硬门（**已讨论定稿 2026-07-13，待新 ADR**）

**用户拍板**：① 被测代码**必须是 PR 那版** → **fail-closed 硬门**，不满足 BLOCKED（非告警放行）；
② 真实验收场景 **PR 恒未合并**（→"合并后验哪个"问题不存在；主干永远不含本 PR 改动，master 兜底恒错）；
③ 取**发起验收那一刻 head 的最新 commit** → 钉死一个 sha → 报告记录该 sha。

**调查坐实的关键（`w60pkf2jx`）**：被测代码是**两条独立来源**，硬门必须卡在第二条：
- **Source 1 · 取材锚**：`fetch_source` 抓 op_def/example → 喂 spec/runner。现状分支名兜底 `head→base→master→main`，head 删了静默落 master（IsClose 踩的坑）。
- **Source 2 · 真正被测的二进制**：真机 `cd $OPRUNWAY_OPS_REPO; build.sh` 编**当前工作树**。**流水线从不 checkout 这个仓**（grep git 操作零命中）、无 sha 记录、无校验——"被测=PR版"完全外包给人，没人管。**硬门必须落在这里**（gate-must-check-the-effective-object）。

**硬门完整判据（定稿）**：
1. CP-A 解析 PR head → 钉 `head_sha`（**解析一次、全程复用**，不每步各自取"最新"，否则作者中途 push 会自造不一致）；记进 `pr_facts` + 报告。
2. 取材（Source 1）从 `head_sha` 取，**删 master 兜底**；立即落盘固化该版全文（防 squash-GC 后不可达）。
3. 真机仓（Source 2）checkout 到 `head_sha`；build 前 `git rev-parse HEAD` 记**实际编的 sha**（catlass `run_on_catlass_npu.sh:92` 已有此范式可抄）。
4. **门校** `取材 sha == build sha == head_sha`，三者一致才放行；不一致 / 缺任一 → **BLOCKED，不启动 build**。
5. 报告记 `head_sha` + `built_commit` 作代码来源 provenance（新增 evidence 字段）。

**D4（谁 checkout Source 2）= clone-to-scratch（用户批，合并 Q3）**：不碰用户现有仓，**永远浅 clone 到独立 scratch 目录 + checkout `head_sha` + 记 sha**。既躲开"动用户仓工作树"的副作用，又天然保证版本正确。Q3 的"按需 clone"与 D4 一并解决。

**API 可行性**：`pulls/{num}/files` 的 `raw_url` 实测跨 fork、合并后可拉、无需 token；`contents?ref=<sha>` 实测可用 → 硬门可自动化。
⚠ **落地前必验**：未合并 PR 的 head 常在贡献者 **fork**（`head.repo.full_name`），open+fork 的 `?ref=<sha>` 可解析性**尚未实测**——实现前拿一个真实未合并社区 PR 试一次 API。

**canon 状态**：canon **无**"被测代码=PR版"决策（`verify-spec-pr-correspondence` 只管"任务书↔PR 配对身份"，不管代码版本），但有 canonical 底色（`acceptance-contract-evidence-chain` 要"证明验收覆盖了 PR"）→ **需新立 ADR**，作 `verify-spec-pr-correspondence` 的姊妹（同属"别拿错被测基准"家族）。

### C · 最该修：精度真值（golden）由插件自写、无交叉核对（Q5+Q9 合并为一个架构议题）

> Q5 已 5 路深挖定稿（workflow `wcc1cqov2`，2026-07-13）；Q9 待深挖。二者同一病根，合并处理。

**Q5 定稿 · runner 没问题，该治的是共有病根**：
- **runner 本身无 bug**：泛型壳 `RunTypedCase<T>` 只按字节宽搬字节，dtype 分支是白名单 if 链，只实例化 fp32/fp16，其余**具名 fail-closed 报错**。2 类上限是**成文的 Track C 边界**（`runner-skeleton §0`），非缺陷；对 bf16/int32 显式拒绝、不静默误转。
- **"扩 runner 到 4 dtype"证实徒劳**：`_NP` Track C 拦截在 `repo_adapter.py:415`（每条 case 都查），**严格早于** runner 部署（`:473`）和 NPU 执行（`:484`）。补完美的 4-dtype C++，bf16/int32 一行都执行不到 = 死代码（对抗核验无旁路可推翻）。
- **纠正 Q5 表述**：transcript 复盘证明 agent **从没真去扩**——它识破多层耦合（line 265）、自陈 bf16 codec 风险（line 270）、把"扩/不扩"抛给用户并**推荐不扩**，用户选不扩。处置是范本。
- **stale 行号更正**：Track C 拦截现在 `:415`（非旧记的 `:351`，我改传输层时 `:351` 变成 `_copy_to`）。

**Q9 · golden 由插件自写（比 runner 更深的洞）**：
- `gen_cases.GOLDEN` 里 golden = 插件写死的 numpy（`np.isclose` 等），不来自算子/AscendOpTest。numpy 语义与昇腾算子若有差（边界/NaN/dtype 提升），golden 本身就错，"精度通过"失去意义。evidence 的 `ascendoptest_bool` **10/10 全 null**——交叉核没接上。
- **比 runner 更深**：runner 至少有一条纪律级独立交叉核（手算小 case + custom-exe vs 内置 TBE-exe 对照）；**golden 是终端 oracle，没有"给 golden 的 golden"**，公式理解错无任何独立信号能抓。且 `oracle_source` 写死假常量 `cpu_ref`（canon verified tier，fail-open）。

**共有病根**：runner（agent 生成 C++）与 golden（agent 写死 numpy）都是"把验证被测算子的东西放进 agent 自撰、未独立验证的代码里"。两者都**非机器强制**（runner sidecar 硬门 `.verified.json` 未实现；golden 来源字段假常量）。

**改法（Q5 workflow 建议，分档，待用户拍板深浅）**：
1. **成文禁止** agent 在验收跑测中临时扩 dtype / 改 runner 分支或 `_NP` —— 不支持的 dtype 走干净 gap。（本次 agent 已这么做，固化为规则。）
2. **bf16/int32 作深思的 Track C 扩展**：整条链一起改 + 真机验证；int32 先行（无阈值难题），bf16 押后（阈值 provenance 未 settle）。前置 = op_def AddConfig + 任务书逐算子交叉核验。
3. **runner/golden 一起治**：短期落 runner sidecar 代码硬门 + dtype 能力单源化；中期改造算子自带 `test_aclnn_*.cpp` 接框架替代手填四槽；**golden 那侧更深，`oracle_source` 假常量要改成真来源，别只治 runner**。
4. **tier 注意**：`generated_harness` 模式本身 canonical（改定义走 ADR）；但"agent 手填 C++ 四槽"这个具体实现在无 tier 的 skill 文件里，换模板库/复用 test_aclnn 不用动 ADR。

### D · 半成品：全 dtype 解析在，但静默收窄未堵

- **Q7 "支持所有 dtype"**：`taskdoc-to-spec.md:126` **已有**规则——任务书只写"所有类型"时读 PR `*_def.cpp` 的 `REG_OP DataType({...})` 拿全集（解析逻辑在）。但全集解析出后，`params.dtype` 只填 pipeline 支持子集、其余记 `task_pr_gaps`；**gap 为空时静默收窄、无人知情**（上一轮 F1）。
  改：即上一轮定的 **fail-closed 三落点**（`select_standard` 白名单 / spec 记 `dtype_required` vs `dtype_tested` / 门校 caseset dtype 覆盖），代码未落地。

### E · 澄清（非 bug）

- **Q1 specs/ 该不该在**：见上方焦点——部分该迁（样例）+ 部分该换（fixture 用假算子）。
- **Q6 bf16/int32 扩展（用户 2026-07-13 确认：暂不支持、后续做）** —— 明确的 backlog，非 bug。现状 `repo_adapter._NP` 只 fp32/fp16，bf16/int32 走 Track C 干净 gap。Q5 workflow 已摸清每层代价，分两档做：

  **档一 · int32（近，无阈值难题，可先行）**：
  1. spec 声明 int32（前置 = op_def `AddConfig` + 任务书**逐算子交叉核验**该算子真收 int32，不外推）；
  2. `repo_adapter._NP += int32`（**真正的解锁点**——:415 那道 Track C 门是先拦者）；
  3. runner.cpp 加 `RunTypedCase<int32_t>(...ACL_INT32...)`（约 2 行分派）。
  - precision_policy **无需改**（int→EXACT，阈值恒 0）；numerical readback 对 int32 天然通过（storage==logical）。

  **档二 · bf16（远，押后）**：除 int32 三步外还要——
  4. `_NP` 改 storage-aware（bf16 无对应 numpy dtype，`_NP[dtn]` 直接坏）+ 补 storage-aware 读回分支（uint16→fp32，调已存在但没接进来的 `readback_output`）；
  5. runner 用 **`ACL_BF16`（不是 `ACL_FLOAT16`——bf16≠fp16，位布局不同，且都是 2 字节，选错能过字节数校验、只靠精度门兜）**。
  - ⚠ **难点**：lossy bf16 数值算子的**误差阈值 provenance 未 settle**（AscendOpTest 表值 vs 生态 MERE/MARE proposed）+ numpy 无法承载 bf16。IsClose 因输出恒 bool→exact 绕开了这难题；genuinely-lossy 的 bf16 算子本轮无。

  **共同前置（硬约束）**：加 dtype = 改权威 spec，触"dtype 从任务书推不猜"。谁做/何时做：spec 加 dtype = Track B（gated，经用户 gate）；runner 分支 = Track C（挂真机 + pr_facts）。**都不是一次验收跑测里能顺手做的**——见上方 Q5 改法第 1 条（成文禁止 agent 临时扩）。
- **Q8 `baseline_mock: True`**：只在 mock 分支设。测试时看到它是 **CP-B `--mode mock` 自检阶段**；**真机 CP-D 用 `_real_baseline.json`（真 TBE msprof，env=`builtin-TBE msprof`）、不 mock**。逻辑对，但字段易误会——建议报告把"mock 自检 / 真机验收"两阶段 baseline 来源标醒目。
- **Q11 性能仿真图分析能力**：**没有**。`perf_sim_plot` "只画不判"，`perf_compare._lin_slope` 拟合注明"模型/推断非实测"。测试里"斜率为负、物理讲不通"是那次 agent 人肉看出的，不是插件能力。门只查"图在不在"（又一 `gate-must-check-the-effective-object`）。当前靠 `requires_human_cp` 挂人工看图。要自动分析须另立能力，暂无。

---

## 贯穿性病根（两条，值得单独立 ADR）

1. **私有环境写死默认值**（Q2/Q3，牵连 Q4）——插件把"我们的环境"当默认，别人跑不起来。工程约定第一条"零硬编码"被默认值架空。
2. **agent 自写参考实现来验精度**（Q9/Q10，牵连 Q5）——精度真值可能本身就错，且无独立交叉核对。

---

## 优先级建议（待用户拍板）

| 档 | 条目 |
|---|---|
| P0（正确性，影响裁决可信） | Q9 golden 来源 + AscendOpTest 交叉核对；Q7 fail-closed 三落点 |
| P1（可用性，影响别人能否用） | Q2 机器连接询问 + 本机分支；Q3 按需 clone；Q4 取 PR 原始改动 |
| P2（防污染，影响"干净推导"成色） | **spec 样例拆分（本轮焦点）** + acc-spec 禁读同名 spec |
| P3（体验/边界） | Q6 bf16/int32 扩展；Q8 报告口径；Q11 仿真图分析（暂搁） |

---

## 本方案没做的事

- 没动一行代码，没 commit。
- 上述改法均为提案，逐条待用户确认后才进实施。
