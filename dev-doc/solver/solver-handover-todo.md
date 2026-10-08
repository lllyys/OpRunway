# solver 线交接待办簿

> 2026-10-08 接管注：交接方不再 push，其本地 git 历史（f5322b4 等）不可得；回交快照（已导入 repos/repo-task-atk-test-evolve 的 evolve-1008）即正史起点，后续演进全部在本方。HT-23 所指的对方交接包仓随之失效：回交内容已存reports/solver-handover-1008-returned/（本仓 ignored 区，无 git 大文件问题）。

**怎么用**：先看总表挑活——🟢 直接做，🔴 先去拿裁定。做完一条：状态改 ✅，
在该条「完成落点」填产物或 commit，**不删条目**。只收录已明示要记的条目。

## 总表

| 编号 | 一句话 | 状态 | 卡点 / 等谁 |
| --- | --- | --- | --- |
| HT-1 | max_abs 硬上限口径：采 opbase 最新标准动态锚点 | ✅ 完成 | 渲染口径随 v3 重渲；核心裁决逻辑复核经拍板以 TRAE-code-review 替代 Codex checkpoint（阶段 4，无阻断级发现，3 minor 记录不修） |
| HT-2 | A0：批量抽样验证通路（构造 + 判定） | ✅ 完成 | 全链 A0 化 + batched README 模板（HT-13 同句入）；145/145 绿（2026-09-26，未 commit）；「代表内容解释」待验收方对一句（随阶段 5） |
| HT-3 | A1：potrf/potrs 残差阈值换式 | ✅ 完成 | 91/91 绿（2026-09-26，未 commit）；缺 mean 走单支兼容 |
| HT-4 | A2：ratio_cpu_mean 预计算固化 | ✅ 完成 | 发包侧固化+渲染侧消费全链；冒烟含 3m>5r 消费铁证；私集固化机制随阶段 5 就绪（--seed-base 参数化 + frozen/private/SEEDS.md）；v4 全量决策（2026-09-27）消掉 mean 统计范围歧义——发包精度用例=刷新目录全量，「所有 case 平均」无歧义 |
| HT-5 | A3：potri 残差阈值换式 | ✅ 完成 | 87/87 绿（2026-09-26，未 commit）；A3 扫描最薄余量 5.00× |
| HT-6 | A4：ε 口径——我方侧标注与披露 | ✅ 完成（我方侧） | 任务书侧 6 处数值修订待路由 |
| HT-7 | A5：判定次序改「先生态标准、不过再 LAPACK」 | ✅ 完成 | 88/88 绿（2026-09-26，未 commit）；sim_dut NaN 扰动改全块 |
| HT-8 | B2：非 batch 算子 info 契约判定支路 | ✅ 完成 | 全链 info 支路 + 端到端冒烟全过（2026-09-26，未 commit） |
| HT-9 | B3：batch 算子 info 契约判定支路 | ✅ 完成 | 批量 info 混合 case 全链 + 2 bug 修复 + 7 步冒烟；154/154 绿（2026-09-26，未 commit）；「1~2 个矩阵按代表内容解释」记入交付记录，发包前与验收方对一句 |
| HT-10 | B4：确定性复跑检查 | ✅ 完成 | 2026-09-26：--rerun N + bit-wise 三键比对 + 独立结论；162/162 绿 |
| HT-11 | C3：复数拆实/虚口径——T7 已摘 | ✅ 完成 | 已裁定 issue 即书面确认 |
| HT-12 | C4：info/确定性独立检查项 | ✅ 确认维持 | issue 已认可，零改动 |
| HT-13 | B1：正定性由构造保证的契约声明 | ✅ 完成 | 同句已随 HT-2 批量 README 模板落地（2026-09-26），欠账清 |
| HT-14 | 〔issue C1〕混合容差换 2⁻¹³ + T3 双轨拆除 | ✅ 完成 | 阶段 1 criteria 快批随 HT-5→7→3 同批落地（commit ccad108，CRITERIA_VER s1-A6，91 测试绿；现随 164/164 回归） |
| HT-15 | 〔新书比对〕potrf 残差计算基——暂维持 A64 | 🟡 等任务侧补字复核 | 反馈请求待路由；补字若定实际输入则需改 |
| HT-16 | 〔新书比对〕T8 摘除，batched 数值结论升正式 | ✅ 完成 | 随阶段 2 树归并落地（2026-09-26）；spec 标题改写；flags 恒空 |
| HT-17 | 〔新书比对〕随机 SPD 均匀与正态各半 | ✅ 完成 | 核对五项全齐 + 2 测试固化，156/156 绿（2026-09-26，未 commit） |
| HT-18 | 〔新书比对〕INF/NAN 用例类 | 🔴 悬置 | 期望口径（opbase 2.3 收窄的期望集形态）待任务侧裁定后施工 |
| HT-19 | 〔派生收口〕v3 重渲主卡 | ✅ 完成 | 2026-09-26：验收两道门全过；抓漏修 FALLBACK_USES_MEAN（s4-D9）；162/162 绿 |
| HT-20 | 〔新书比对〕申诉通道句入验收报告 | ✅ 完成 | 落 expectations 逐项 evidence，仅残差超阈 FAIL 附句 |
| HT-21 | 〔v4 复核〕info 契约派生基座双链口径差异——记录 | ✅ 记录（不改码） | v4 交叉复核（2026-09-27）第 4 项：设计使然非缺陷，判定链用包内切片重造自洽不受损 |
| HT-22 | 〔对外内容〕任务目录整理优化——产物入位 + 算子目录只留对外件 | ✅ 完成 | 2026-09-27 用户指令：两任务目录各算子只留 cases.json/bench_result.json 参考 + 交付包；cu 脚本/编译脚本/日志/改动报告移 `_internal_archive/` 存档不删除 |
| HT-23 | 〔git 大文件〕批量包 index 254MB 处置 | ✅ 随对方线关闭（2026-10-08） | 摘要化方案已实作后**整体回退**（revert 8e1d65e，用户拍板暂停）；快装模式（--ratio-source reuse）半成品丢弃；详见逐条说明 |
| HT-24 | 〔私集验证〕potri 负例定标失效 + 直审双门小幅宽容语义 | 🔴 挂起（2026-10-07 更正） | 初版「分母塌缩」根因更正：判定链无缺陷（_dpot03 与任务书公式一致）；真实机制=spotri 逆元素幅度 ~1/n，scale/zero 绝对差落 max_abs=1e-2 门内、直审按次序放行；sim δ=1 定标声明失效为文档错误；候选甲乙丙重列待拍板，详见逐条说明 |
| HT-25 | 〔对外对齐〕任务书定稿版回灌——taskbooks/ 两册同步发布版 | ✅ 完成 | 2026-10-08：两册以发布版逐字节替换（md5 一致，实数/复数册），README 产物地图标注契约真源迁移；包本体核实与发布包一致未重产 |
| HT-26 | 〔对外对齐〕accept skill 性能门禁机制同步（0.35×GPU数据） | ✅ 完成 | 2026-10-08：expectations.py 性能门禁改 0.35+msprof+bench_result 口径、内存证据改「不设门仅存证」；references/perf-collection.md 对齐发布版 PERF_COLLECTION_SUPPLEMENT；两树 SKILL.md、verdict.py/render_verify.py/thresholds.py ε 注释更新；criteria 测试基线无回归（24 failed 为本地环境缺 golden 夹具，stash 前后一致） |
| HT-27 | 〔对外对齐〕case-gen README 模板对齐发布版 + 十包 README 重渲验证 | ✅ 完成 | 2026-10-08：purescript/batched 模板由发布版 README 反向生成（保留 {op}/{n_cases}/{gram_form}），十包重渲逐字节对齐验证 ALL MATCH；README 回灌 build-v4 packages+delivery 并重算 manifest README 指纹；批量 delivery index 改 gzip 交付形态（36MB，roundtrip 校验过）；两册 DELIVERY_LEDGER 指纹表更新并注明发布版 manifest 差异；注意 assemble_delivery_v4.py 重跑会回退 gzip 形态，需补 gzip 步骤 |
| HT-28 | 〔对外反馈〕任务书与发布包残留问题反馈任务侧 | 🔴 等任务侧 | ①空问题（n=0/nrhs=0/batchSize=0 成功返回）与维度表下界 1 并存，且包内用例无 n=0/n=1（min n=2）；②§3.3 引用 `gpu_baseline.csv` 但两个 Cholesky 任务目录均无此文件（仅 bench_result.json）；③复数册 CRLF 与实数册 LF 不一致；④发布十包 manifest 的 README.md 指纹过期（README 装包后手改未重算，10/10 全部对不上），需任务侧重算或以本仓重算版为准 |
| HT-29 | 〔仓务〕solver-handover-0924_copy 工作记录删除 | ✅ 关闭（不属本项目） | 2026-10-08 确认：solver-handover-0924_copy 为本地单独处理线（其 40 份 README+3 模板的批量替换是本地试验内容），不属本项目交付物；本项目不留该副本的同步/回退义务，后续以主仓 HT-27 产物为唯一基线 |
| HT-30 | 〔仓务〕skill 权威仓澄清 + accept 增量前向移植 | ✅ 完成 | 2026-10-08 澄清：repo-task-atk-test 为 skill 唯一权威活仓（PR #15 后持续演进：86+137 测试、A0/ratio_cpu_mean/申诉指引、模板对外化+gzip 说明），trees/solver-s4-tree 仅为归并源快照、只承载本仓增量；已把 HT-26 增量前向移植到 atk-test（SKILL.md/verdict/render_verify/thresholds 逐字节拷贝、perf-collection.md 发布版重写、expectations.py 在 s3-D4 版上重放 0.35 门禁+内存不设门），criteria 137/137 全绿；case-gen 侧经核验 atk-test 模板本就正确（{n_info} 参数化，十包渲染逐字节命中发布版 README），无需改动；atk-test 6 文件改动未提交，待用户提交 |

## 逐条说明

### HT-25 任务书定稿版回灌（对外对齐）—— ✅ 完成（2026-10-08）

- **背景**：2026-10-08 比对 solver-handover-0924/taskbooks 两册与
  community_task `solver_1010` 分支发布版（单精度实数/复数 Cholesky 任务），
  发布版已定稿多轮：精度章按 opbase 新标准（HT-6 落字）、维度范围放宽
  （batched n∈[1,4096]、batchSize∈[1,1000000]，与发布包内 batch=1e6 用例一致）、
  性能门禁改 0.35×GPU数据（GPU 参考值取 bench_result.json 预填，msprof 采集
  NPU kernel 耗时）、§3.4 内存不做要求、新增 task_submission 交付件模板。
- **做什么**：taskbooks/ 两册以发布版为准替换（复数册 CRLF 保持发布版原样）；
  README 产物地图标注契约真源迁移。
- **不做**：包本体不重产——发布 10 包与 build-v4/packages 逐字节一致已核实
  （verify_accuracy.py / gen_data.py / cases/index.json md5 相同）。
- 完成落点：taskbooks/ 两册（发布版逐字节，md5 复核一致）；README.md 产物地图行。

### HT-26 accept skill 性能门禁机制同步（对外对齐）—— ✅ 完成（2026-10-08）

- **背景**：任务书性能章从「0.8×A100 + 开发者实测 T_A100 填表」改为
  「0.35×GPU数据 + msprof 采集 + bench_result.json 参考值预填」；accept skill
  仍按旧机制：expectations.py 性能项恒待裁、perf-collection.md 参考文 A100 旧文。
- **做什么**：trees/{solver-s3-tree,solver-s4-tree}/repo-task-solver-accept 下
  expectations.py 性能正式门禁项改 0.35 机制、references/perf-collection.md
  对齐发布版 PERF_COLLECTION_SUPPLEMENT、SKILL.md 表述更新；verdict.py 注释
  「任务书现行文本 2⁻²³ 有误」顺手更正（任务书已改 2⁻²⁴）。
- 完成落点：trees/{solver-s3-tree,solver-s4-tree}/repo-task-solver-accept/{scripts/expectations.py,
  references/perf-collection.md,SKILL.md,criteria/verdict.py,criteria/render_verify.py,
  criteria/thresholds.py}；criteria 测试 stash 前后同为 24F/26P（本地缺 golden 夹具，非回归）。

### HT-27 case-gen README 模板对齐发布版（对外对齐）—— ✅ 完成（2026-10-08）

- **背景**：发布包 README 与 build-v4 产包 README 不同（装包后手工修订未回灌
  模板）：①「本包供开发者自测」口径（去验收侧同包消费表述）；②ratio_cpu
  语义改「发布参考，判定以现场重算值为准」；③canonical 行改「全量清单 N 例」；
  ④verify_perf 描述改 CUDA cuSolver 实测、正式判据 T_NPU ≤ T_GPU数据/0.35；
  ⑤PENDING_RULING 措辞「正式结论由验收方出具；flags 恒空」；⑥批量包 README
  为 A0 抽样/info 契约/gzip index 新流程全文。另 trees 模板存「T要求执行。」
  截断残留（56/75/79 行）。
- **做什么**：重写 purescript/batched 两模板至与发布版逐字一致（保留
  {op}/{n_cases}/{gram_form} 占位符），通用模板修性能句；以模板重渲十包
  README 并与发布包逐字节对齐验证；同步 build-v4/packages 与
  build-v4/delivery 副本。
- 完成落点：assets/package-readme-template-{purescript,batched}.md（发布版反向生成）+
  package-readme-template.md（截断句修复）；十包重渲 ALL MATCH；build-v4 十包 README
  与 manifest README 指纹重算；批量 delivery index 改 .gz 交付（36MB，roundtrip 校验）；
  两册 DELIVERY_LEDGER 指纹表更新。
- **遗留**：assemble_delivery_v4.py 无 gzip 步骤，重跑组装会回退为明文 index——
  需在组装器补「批量包 index.json.gz 化」步骤（随下次改码批次）。

### HT-28 任务书残留矛盾反馈任务侧（对外反馈）—— 🔴 等任务侧（2026-10-08）

- **逐项**：
  1. 空问题契约（n=0/nrhs=0/batchSize=0 成功返回，API 节 L178 + 边界轴表）
     与维度表下界 1（L251-254）并存；且发布包用例 min n=2，无 n=0/n=1 用例——
     空问题验收口径需任务侧明确（是否入用例、抑或仅作开发者自测项）。
  2. §3.3 验收流程引用 `gpu_baseline.csv`，两个 Cholesky 任务目录均无此文件
     （只有 bench_result.json；gpu_baseline.csv 存在于同批 BLAS 任务）——
     引用是否为模板残留待确认。
  3. 复数册任务书为 CRLF 行尾，实数册为 LF——建议统一。
- **已消解项（记录）**：batchSize 三处口径矛盾已由任务侧改 [1,1000000] 消掉
  （2026-10-08 复核，原维度表 [1,30000] vs P-09~P-12 的 46k~103k vs 包内 1e6）。
- 完成落点：待路由。

### HT-24 potri 负例演练定标失效 + 直审双门小幅宽容语义 —— 🔴 挂起（2026-10-07 更正）

- **发现经过**：私集十包验证（冒烟 + f32 真实实现）。spotri/cpotri 的 scale
  （golden×2）与 zero 负例均 121 PASS / 24 FAIL（两种扰动分布完全一致）。
- **⚠️ 2026-10-07 更正**：初版「分母塌缩」根因有误（残差公式记错）——
  `_dpot03` 实现与任务书一致（`ratio = ‖I−A·C‖₁/(n·‖A‖₁·‖C‖₁·ε)`），
  **判定链无缺陷，逐层按设计工作**（报告实证：FAIL case 直审+fallback 双层
  正确挂；PASS case `fallback.ran=false`——直审过则不跑残差，HT-7 次序使然）。
- **真实机制**：spotri golden 是逆矩阵 C，对角占优构造下元素幅度 ~O(1/n)
  （n=4096 → max|C|≈2.4e-4）。scale/zero 扰动的绝对差 = |golden| ≈ 1/n：
  - 直审 max_abs 门（固定兜底 1e-2）→ 2.4e-4 << 1e-2 放行；
  - 小元素绝对差落 atol=2⁻¹³ 内 → matched_ratio 0.99+ 放行；
  - 判定次序（先生态标准）→ fallback 不跑 → PASS。
  小 n case（0001~0024）|C|~O(0.1) 超 1e-2 门 → 直审挂 → fallback 跑 →
  ratio 百万级 FAIL。FAIL/PASS 按 n 单调分界，与「随机漏抓」不符的旁证一致。
- **定性**：
  1. sim_dut 文档「scale δ=1 使全部 case 兜底超阈」定标声明对 potri **失效**
     （未计入 C 幅度 ~1/n 随 n 缩小）——文档级错误，需修声明或改定标；
  2. 直审 max_abs=1e-2 固定兜底门对输出幅度 ~1e-4 的 case 有 40 倍宽容度
     （「输出全零但直审放行」）——**生态标准表固有语义**，是否需对 potri
     收紧属口径裁定，非实现缺陷。
- **包内 sim 另一缺口**（不变）：info 契约用例一律跳过——正例 verify 恒
  rc=1（no_evidence 3），info 端到端在包内演练不通。
- **候选方案**（待拍板）：甲=potri 判定增设相对幅度门（动 criteria+重标定+
  重渲重装）；乙=验收侧对 potri 强制跑残差层（判定次序例外）；丙=sim 文档
  修正 + 负例演练改用强扰动（README 声明）。
- 完成落点：待拍板。

### HT-1 max_abs 硬上限口径 —— ✅ 完成

- **决什么**：混合容差第二门（max_abs_error_limit）二选一：
  - A：issue 清单 C2 方案——删 or，定常数 1e-2；
  - B：opbase 最新标准（a5e8e71，2026-09-24）——`max(1e-2, 32·ULP(g_low))` 动态锚点
    + ±inf 收窄（大幅值元素下 B 比 A 宽）。
- **裁定**：原负责人 2026-09-24——「用新的生态标准 opbase 最新标准，这个决定了」，即 B 方案：
  `max_abs_error_limit = max(1e-2, 32·ULP(g_low))` 动态锚点 + ±inf 收窄。
- **做什么**：criteria 混合容差层实现动态锚点（g_low=最大误差点 golden 按输出 dtype
  RNE 收窄；MAX_ABS_ULP32 固定常数退役）与 ±inf 收窄比对；包内 verify 口径随 v3 重渲。
  注意：此处 ULP 用间距语义（随 g_low 值域缩放），与残差 ε=2⁻²⁴ 是两个常数，勿混。
- **背景**：`review-bundle-issues-0924.md` C2 与追注 2；`repos/opbase`
  mixed_tolerance_standard.md 第 93/136/146 行。
- 完成落点：criteria/ 的 thresholds.py（max_abs_limit 单点函数、MAX_ABS_ULP32 退役）、
  verdict.py（_layer1_stats 收窄+±inf/NaN 分类+锚点、_gate 单门、T3/T4 双轨拆除）、
  tests/ 四文件重标定+新增 9 例；CRITERIA_VER s1-A4；pytest 86/86（2026-09-24，未 commit）。
  遗留：render_verify 渲染口径随 v3 重渲；sim_dut 的 NaN 扰动已随 HT-7 复核落定
  （改全块 NaN，见 HT-7 完成落点）；核心裁决逻辑复核经拍板以 TRAE-code-review 替代
  Codex checkpoint（阶段 4 收官项，无阻断级发现，3 minor 只记录不修）。

### HT-2 A0 批量抽样验证通路 —— ✅ 完成（2026-09-26）

- **做什么**：
  - case-gen 侧：每 batch 用例只造 min(5, batchSize) 个代表矩阵（内容流沿用 cu 转写
    配方）；代表槽位与余槽填充由 case seed 派生的两条独立子流决定；
    `sample_map`（{content_idx: {rep_slot, slots[]}}，0 基、全覆盖、无重复）固化随包；
  - accept 侧：先一致性核对（每内容全部槽位 out32+info 与 rep_slot 逐位比对，失配
    FAIL 报槽位号），后精度判定（仅 rep_slot，复用现有三层原样，不加开关）；
    无 sample_map 的旧包走全遍历兼容分支。
- **硬约束**（本文件「原负责人补充描述」三条）：不落 npz、流式现场构造现场使用；
  子流一律从 case seed 派生、禁独立播种；验收私集种子不公开。
- **待确认一句**：混合 info 用例「1~2 个矩阵非正定」在抽样下按「代表内容」解释。
- **背景**：`review-bundle-issues-0924.md` A0/B3。
- 完成落点（2026-09-26，未 commit）：gen_data_cholesky 三流派生 + derive_sample_map +
  expand_sampled_rows + gen_batched_case（A0 不落数组）；accept 侧 criteria/batched_a0.py
  新建（余槽 bit-wise 一致性第一层 + 代表槽逐内容三层判定）+ stream_check 批量流式
  分支；render_verify 批量模板（BATCHED_RENDERER_VER 现 s3-D7，承 HT-4）；build_package
  build_batched 纯脚本装包（TOOL_VER s3-F8）；批量 README 模板新建（含 HT-13 B1
  构造性正定同句，欠账清）；tests/test_a0_sampling.py 9 例，全链 145/145 绿。
  遗留一句：混合 info「1~2 个矩阵」按代表内容解释——发包前与验收方确认（随阶段 5）。

### HT-3 A1 potrf/potrs 残差阈值换式 —— ✅ 完成（2026-09-26）

- **做什么**：criteria/thresholds.py——potrf/potrs 换 `max(5·ratio_cpu, 3·ratio_cpu_mean)`；
  收紧式/上浮式及 30 地板/上限退役；batched 逐矩阵判，mean 取 case 级值。
- **背景**：`review-bundle-issues-0924.md` A1；0924 任务书 §3.2.2。
- 完成落点（2026-09-26，未 commit）：
  - `thresholds.py`：`POTRF_POTRS_FORMULA = "max(5*ratio_cpu, 3*ratio_cpu_mean)"` +
    `potrf_potrs_threshold(ratio_cpu, ratio_cpu_mean)`（mean=None 显式拒绝，不静默
    降级）+ `potrf_potrs_single_line`（缺 mean 兼容单支 5·ratio_cpu）；
    TIGHTEN/FLOATUP 公式与 tighten/floatup 函数、30 地板/上限删除。
  - `cards_cholesky.py`：CriteriaCard 增 `fallback_uses_mean` 声明（potrf/potrs/
    cpotrf/cpotrs True、potri/cpotri False——mean 线结构性不适用），四卡接线新式。
  - `verdict.py` `_run_fallback`：uses_mean 卡读 `case_arrays["ratio_cpu_mean"]`
    两支裁决；缺 mean 兼容口径（plan 定义，fail-closed）：残差 ≤ 单支 5·ratio_cpu
    → PASS（formula 注明单支）；超出 → 证据不足不判 FAIL（error 指认缺 mean、
    待 v3 包），layer1 证据保留。
  - `accept_run.py`：accuracy_items 收 index，从顶层 `ratio_cpu_mean` 按算子注入
    case_arrays（index 无该键注入 None → 兼容路径；mean 本体 HT-4 固化）。
  - `stream_check.py`：消费面核对注释（流式不产 mean，负例演练主要落 layer1）。
  - tests：test_thresholds 换式两例、test_judge_flow 相对线/均值线/缺 mean 四例
    （原上浮豁免例重写）、test_judge_cards/test_complex_criteria 接线与兜底断言
    重标定（helper 加 mean=0.0 默认）；净增 3 例。
  - `references/verdict-mechanism.md` 残差复核行同步。
  - pytest 91/91 绿。遗留：render_verify 随 HT-19 重渲（换式后模板 import 即
    fail-closed 报错）；ratio_cpu_mean 本体固化在 HT-4（发包侧）。

### HT-4 A2 ratio_cpu_mean 预计算固化 —— ✅ 完成（2026-09-26）

- **做什么**：发布前用 CPU 参考链路算好写入 index，判定时只读、零重算——
  - 非批量：index 按算子存该算子全部正定精度用例的算术平均（单算子切片内，勿跨算子）；
  - 批量：case 条目存加权均值 Σ(count_j·ratio_j)/batchSize（与任务书「batch 个矩阵
    均值」数学等价，CPU 只算代表）；
  - info 用例与非正定矩阵不计入（原负责人 2026-09-24 引 issue B2 口径确认）；两类用例
    在 index.json 用 `case_purpose` 分列标记：精度判定 / info 契约检查；
    mean 只随用例集版本重算；
  - 验收私集另行固化一份（种子不公开，见补充描述 3）。
- **背景**：`review-bundle-issues-0924.md` A2；本文件补充描述 2、3。
- 完成落点（2026-09-26，未 commit）：build_package.py 两助手（purescript_ratio_cpu_mean
  算术平均 / batched_case_ratio_cpu_mean 槽位加权；info 契约条目不计入，Σcount≠batch
  或 ratio 含 None 即 SelfCheckError fail-closed）+ 两路装包接线（TOOL_VER s3-F8）；
  render_verify.py 批量模板 _judge_a0 补 mean 透传（对齐权威 batched_a0，模板同构
  分歧补齐）+ 批量/纯脚本两 CLI 从包内 index 读固化 mean 注入 case_arrays（缺键
  注入 None → 单支兼容口径；RENDERER_VER s4-D8、BATCHED_RENDERER_VER s3-D7）；
  两 README 模板补 mean 消费句（assets 目录 md 写入异常坐实——Edit 落盘内容会回退/
  混层，改 Shell 原子写入 + 立即验证处置）；tests/test_mean_freeze.py 2 例。冒烟七步
  全过：两路装包 mean 逐条对拍、verify 副本全跑退 0、消费铁证（同扰动 dut 原包
  FAIL → index mean=1e30 后 PASS，3m 第二支真被消费）、十算子复渲确定性；158/158 绿。
  验收私集机制随阶段 5 就绪（2026-09-26）：freeze_canonical --seed-base/--std-seed-base
  参数化（公域默认产物逐字节不变）；私域基值 920284139/51523761 落
  frozen/private/SEEDS.md；test_private_seed_base.py 2 断言（公域零漂移 + 私域
  平移不相交）；公开十包已产齐（2026-09-26，S1–S5 全链装包自检全过），私集包
  同链 --seed-base 换域即可产制，仍待执行。
  **v4 全量决策落点（2026-09-27）**：用户决策「精度用例=刷新后 cases.json 全量」——
  实施机制为包内 canonical 切片顶层 `package_scope:"full"` 事实源（build_package
  --scope 缺省 full，commit 7741535；单矩阵算子级 mean 与批量条目级 mean 的统计
  范围随之=包内全量精度用例），任务书「所有 case 平均」的口径歧义自此消除，
  2026-09-27 会话曾提的「mean 统计范围悬置项」就此关闭；v4 十包已按此重产
  （spotrs 包 176 条精度用例含 nrhs>1 101 条，build-v4/ commit 6d7dc7b）。

### HT-5 A3 potri 残差阈值换式 —— ✅ 完成

- **做了什么**：thresholds.py 落地 `potri_threshold(ratio_cpu) = max(5·ratio_cpu, 0.1)`
  （docstring 记 issue A3 三条修订依据：相对线 5× 统一、绝对线 0.1 因 DPOT03 双归一、
  mean 线结构性不适用）并修正悬空注释；cards_cholesky.py spotri/cpotri 两卡接线
  POTRI_FORMULA/potri_threshold；tests 重标定（test_thresholds 新增 formula 测试、
  test_card_wiring/test_c_card_wiring 换 0.1/10.0 断言、test_judge_cards 与
  test_complex_criteria 的 potri 兜底 threshold 断言 1.0→0.1）。
- **A3 验证式（扫描）**：verify/ht5_potri_a3_scan.py——80 case（real/complex ×
  cu/std/谱条件数/幅值缩放 × L/U），合法参考实现（与 ratio_cpu 同源的 LAPACK
  s/c 前缀参考链）逐 case DPOT03 残差对阈值扫描，**全 case 通过、最薄余量 5.00×**
  （≥3× 要求）。绝对线 0.1 主导 62 case、相对线主导 18 case，ratio_cpu 实测
  0.0003~0.243 ulp。
- **证据性发现（9 条，报告 observations 字段）**：显式逆路径观察实现
  （potrf→DTRTRI→FP32 gemm）在 n≥256 残差逼近 0.1 线（余量 1.45~2.76×），
  n=1024 复数越过 0.1 线（ratio 0.1368，余量 0.73×）；LU 通路最薄 8.30× 健康。
  即：真实实现若走「显式三角逆+矩阵乘」形态，会被 0.1 绝对线判 FAIL——阈值数值
  是任务书定稿不在施工范围，已写入扫描报告供任务侧知悉。
- **完成落点**：criteria/thresholds.py、criteria/cards_cholesky.py、
  criteria/tests/{test_thresholds,test_judge_cards,test_complex_criteria}.py、
  交接包 verify/ht5_potri_a3_scan.py（+ht5_potri_a3_scan.json）；
  pytest 87/87 绿（2026-09-26，未 commit 随批次走）；render_verify 按 plan 推迟
  阶段 4 重渲（现调卡后 render 会 fail-closed 报错，属计划内中间态）。
- **背景**：`review-bundle-issues-0924.md` A3。

### HT-6 A4 ε 口径：我方侧标注与披露 —— ✅ 完成（我方侧）

- **做了什么**：EPS32=2⁻²⁴ 保持不变（任务书 2⁻²³ 与其「单位舍入」名词自相矛盾，修书
  不修码）；thresholds.py ε 注释扩写防误改；fallback 报告加 `"eps": "2^-24"` 披露字段
  （_null_fallback 与 _run_fallback 三个产出点）；CRITERIA_VER s1-A2→s1-A3；
  新增防漂移测试（eps 字符串求值 == EPS32）。
- **完成落点**：plugin/skill/repo-task-solver-accept/criteria/ 的 thresholds.py、
  verdict.py、tests/test_judge_flow.py、tests/test_thresholds.py；pytest 77/77 绿
  （2026-09-24，未 commit 随批次走）。
- **遗留两条**：任务书侧 6 处 ε 数值修订（2⁻²³→2⁻²⁴）属对外动作由交接方向任务侧反馈；
  render_verify.py 渲染模板里的 null-fallback 副本未加 eps 键，随 v3 重渲（HT-1 裁定后）
  一并补。
- **背景**：`review-bundle-issues-0924.md` A4。

### HT-7 A5 判定次序：先生态标准、不过再 LAPACK —— ✅ 完成（2026-09-26）

- **裁定**：原负责人 2026-09-24——「我们的设计有问题，得先走生态精度标准，不通过的话再走
  lapack」「就是 issue 里说的那样」，即 issue A5 选项①按新任务书两步结构。
  归档见 solver-skill-supplement.md 24f。
- **做什么**（criteria/cards_cholesky.py 为主）：
  - potrf：主判改 F vs golden（现诊断项转正）；recon 撤出第一步（其数学形式留在
    DPOT01 兜底层不丢）；
  - potri：第一步收成 C vs golden 单目标；A·A⁻¹ vs I 只留 DPOT03 复核层；
  - potrs 不变；批量卡同步对调，逐矩阵语义不变；
  - **主判基准统一换 golden64**（原负责人 2026-09-24 裁「用任务书的」，supplement 24h）：
    potrf/potrs/potri 三算子第一层比对基准从 golden32 换 golden64，判定结果不变；
  - spec §2.3′ 注记作废说明、CRITERIA_VER 递增、tests 重标定。
- **背景**：`review-bundle-issues-0924.md` A5；两步结构对病态 case 的兜底语义
  （直审挂 → 残差层复核，终审不误伤）。
- 完成落点（2026-09-26，未 commit）：
  - `cards_cholesky.py`：`_potrf_targets` 转正 F vs golden 直审（复数 `_reim` 拆
    实/虚），原 recon 代码移入 `_potrf_diagnostics`；`_potri_targets` 收单
    ainv_vs_golden（A·A⁻¹ 语义留 DPOT03 复核层）；三算子 golden 键全换 `golden64`；
    模块 docstring 记对调依据（24f/24h）。
  - `verdict.py`：docstring 同步（两步结构、golden64 基准、诊断降级语义），删两处
    「拆实/虚待确认」残句；golden 统计前按 FP32 RNE 收窄，golden64 与 golden32
    判定逐位等价（24h）。
  - `sim_dut.py`：NaN 扰动复核裁定**改强度**（单点 NaN 直审下 n≥100 恰 0.99 过线，
    plan 授权二选一取最小侵入）——`_perturb_nan` 改全块 NaN 保证负例可靠 FAIL；
    顺手修 scale 条目过时 rtol 数值（2⁻¹⁰→2⁻¹³）。
  - tests 四文件重标定：test_judge_cards（potrf 兜底语义正名测试、A64 缺键降级
    分化、potri 收单四例）、test_complex_criteria（names 对调/收单、漏共轭直审
    差值 4→2）、test_lapack_integration（golden64 不降型 + names 断言）、
    test_judge_flow（键名）；净增 test_missing_golden64_yields_error_verdict。
  - `references/verdict-mechanism.md` 比对目标行、tests/conftest.py 索引注释同步。
  - pytest 88/88 绿（HT-3 后 91/91）。遗留：render_verify 随 v3 重渲（HT-19）；
    批量卡对调随树归并（阶段 2）。

### HT-8 B2 非 batch 算子 info 契约判定支路 —— ✅ 完成（2026-09-26）

- **现状缺口**：judge 入口第一道门 info!=0 → error FAIL，被测 info=k>0 走「不可裁」，
  无「核对 k 是否正确」的判定路径；sample report 自述该项证据不足。
- **用例侧**：选 3 个中等规模用例各配 1 个非正定变体——对构造的 SPD 将第 k_min 阶
  顺序主子式变为不正定（对角减大数），k_min 构造时即已知；S/C 两精度、实/复两族
  均覆盖；k_expected 连同矩阵一起入包。
- **判定方式**：非正定用例只做 info 输出对比（info==k_expected 判 PASS 否则 FAIL），
  不做残差精度判定（非正定下分解中途失败，残差无意义）。
- **判定内核支路**（与精度判定相互独立，按 C4 口径作独立检查项）：
  - potrf：info==k_expected 判 PASS；
  - potri：构造奇异因子（某阶顺序主子式为零），info==k_expected 判 PASS；
  - potrs：无正值分支，仅验非法参数路径（info=-i），不涉及 k。
- **mean 口径**：非正定用例不计入 ratio_cpu_mean，`case_purpose` 分列标记（见 HT-4）。
- **背景**：`review-bundle-issues-0924.md` B2（原负责人 2026-09-24 贴文确认）。
- 完成落点（2026-09-26，未 commit）：gen_data_cholesky derive_info_cases /
  build_info_arrays（每算子 3 变体：非正定对角减大数 / 奇异因子第 k 阶置零 /
  potrs 非法 uplo；构造性自检 info==k_expected fail-closed，不产 golden）；render_verify
  _judge_info_inner（case_purpose 分流，只比 info、不进残差不进 mean）；sim_dut info
  支路（s4-E5：正例 info=k_expected / 负例错误 info）；build_purescript 装包自检按
  用途分流（info 无 golden 豁免、ratio 跳过）；purescript README info 契约段。
  端到端冒烟全过（造数→sim→verify 退 0、--case-id 单跑 [info契约] 行、负例退 1）。

### HT-9 B3 batch 算子 info 契约判定支路 —— ✅ 完成（2026-09-26）

- **现状缺口**：Batched 判定此前整体证据不足、族级不判通过；新任务书已给完整口径
  （逐矩阵同式 ratio 逐矩阵判，任一矩阵超阈该用例不过；抽样方法见 A0/HT-2）。
- **用例侧**：选 1 个中等规模 case，其中 1~2 个矩阵构造为非正定（其余保持正定），
  各矩阵 k_expected（正定为 0）入包，验证 infoArray 逐矩阵独立写入。
  A0 抽样下「1~2 个矩阵」按「1~2 个代表内容」解释（其映射槽位同非正定、
  k_expected 按映射展开）——发包前与验收方确认一句。
- **判定方式**：非正定矩阵按 B2 同款只比 info 不进残差；batched 逐矩阵核对
  infoArray[i]==k_expected；参数错写 infoArray[0]=-i 与 potrsBatched 标量 info
  仅报参数错一并覆盖。
- **mean 口径**：同 B2，非正定矩阵不计入 case 内均值（见 HT-4）。
- **背景**：`review-bundle-issues-0924.md` B3（原负责人 2026-09-24 贴文确认）。
- 完成落点（2026-09-26，未 commit）：gen_data_cholesky derive_batched_info_cases /
  build_batched_info_arrays（每算子 1 混合 case：potrfBatched 代表内容 0/1 非正定、
  k_expected 逐内容 k 值列表经 sample_map 展开即全批期望；potrsBatched
  bad_param_uplo k_expected=-1 标量）；batched_a0._judge_a0_info（infoArray 逐槽
  核对，fail_count/first_fail_index/diagnostics.info_mismatches 逐槽证据）；stream_check
  批量 info 分支；build_batched 接线（info 条目不产 golden/ratio、n_info 计数）；批量
  README info 段。修复 2 bug：INFO_PROBE_OF KeyError、标量非 0 预检拦截 info 契约
  条目。7 步冒烟全过（stream_check 正/负例、装包对账、verify 副本全跑、--case-id
  单跑、篡改单槽负例）；154/154 绿。「1~2 个矩阵按代表内容解释」已记入交付记录，
  发包前与验收方确认一句。

### HT-10 B4 确定性复跑检查 —— ✅ 完成

- **现状缺口**：「合法用例重复执行 bit-wise 一致」只有证据不足占位，无复跑驱动
  与比对实现。
- **做什么**：同一输入重复执行 ≥5 次，输出（含 info）之间 bit-wise 一致即通过。
  施工细节：第 2..n 次全部与第 1 次比对即等价于两两一致，比较次数线性。
- **背景**：`review-bundle-issues-0924.md` B4（原负责人 2026-09-24 贴文确认）。
- 完成落点：stream_check `--rerun N`（s3-d3-r5；N≥2 启用）——`_bitwise_same`
  字节域比较（NaN 按位等同不走 ==、-0.0≠0.0、shape/dtype 先行）+ `_rerun_compare`
  三键比对（out32/info bit-wise、status 相等）；逐 case `determinism` 子记录
  （runs/consistent/first_diff 带 run 序号与失配键）、summary det_pass/det_fail、
  确定性失配令退出码 1；info 契约用例不参与（构造性非正定探针）；结论独立于
  数值判定（HT-12 口径）。expectations KIND_DETERMINISM 占位联动更新（复跑证据
  由验收侧 --rerun ≥5 出具）。新增 criteria/tests/test_determinism_rerun.py
  4 测试（字节域语义/三键比对/端到端 --rerun 5 正例/monkeypatch 非确定 DUT 负例）。
  真实册冒烟：pkg-spotrf canonical 8/8、pkg-spotrfBatched 6/6 全 det PASS
  （runs=5 consistent=True）。pytest 162/162 绿（2026-09-26，未 commit）。

### HT-11 C3 复数拆实/虚口径 —— ✅ 完成

- **现状**：bundle 实现为实部、虚部各自算 matched_ratio 与 max_abs、双侧同时达标
  （不合并成 2N 元素互相稀释），与复数任务书 §3.2 第 3 条本意一致；实现无需改动。
- **裁定**：原负责人 2026-09-24——issue 即书面确认，摘 T7。
- **背景**：`review-bundle-issues-0924.md` C3。
- 完成落点：verdict.py T7 注入点摘除+docstring 三处同步；test_complex_criteria 8 处断言
  改「无 T7」；pytest 86/86（2026-09-24，未 commit）。

### HT-12 C4 info/确定性独立于精度判定 —— ✅ 确认维持

- **结论**：bundle 按「独立检查项」处理、不并入精度判定流转，issue 评审认可
  （「合理，确认即可」）。设计维持，零改动；B2/B3/B4 新支路按此口径实现。
- **背景**：`review-bundle-issues-0924.md` C4（原负责人 2026-09-24 贴文）。
- 完成落点：设计维持即完成，无产物。

### HT-13 B1 正定性由构造保证 —— ✅ 完成

- **做了什么**：纯文档三处，零代码——package-contract.md「关键约定」新增构造性正定
  条目（对角占优 Gershgorin 下界 (n+1)/2；随机底阵 B·Bᴴ+n·I 下界 n；无运行时检查、
  非正定 info 用例豁免）；README 模板开发者视角加一句；fill_spd_cu/fill_hpd_cu
  docstring 各加构造性论证与「勿补运行时检查」提醒（复数侧数学核对：非对角模
  ≤ √2/4，界取严格不等号）。doc_style/budget lint 全退 0。
- **完成落点**：plugin/skill/repo-task-solver-case-gen/ 的 references/package-contract.md、
  assets/package-readme-template.md、scripts/gen_data_cholesky.py（2026-09-24，未 commit）。
- **遗留**：batched README 模板尚不存在（随 HT-2 的 v3 创建时带上同句）；模板属对外
  契约，Codex checkpoint 与 criteria 批次合并评审。
- **背景**：`review-bundle-issues-0924.md` B1。

### HT-14 混合容差换 2⁻¹³ + T3 双轨拆除 —— 🟢 可开工〔issue C1〕

- **来源**：issue C1（任务书表 2⁻¹⁰/2⁻¹⁶ vs opbase 最新 2⁻¹³ 双套矛盾）；
  原负责人 已裁「用新版」（supplement 24d）。
- **做什么**：thresholds 的 TASKBOOK/STANDARD 双套收成 2⁻¹³ 单套；verdict 的
  standard 子字典与 T3 flag 注入机制拆除；tests 重标定。与 HT-7 同文件宜同批。
- 完成落点：随阶段 1 criteria 快批（HT-5→14→7→3 同批，commit ccad108）落地；
  thresholds 单套 2⁻¹³、T3 双轨拆除、tests 重标定（s1-A6，91 测试绿 2026-09-26）。

### HT-15 potrf 残差计算基 —— 🟡 等任务侧补字复核（暂维持 A64）〔新书比对〕

- **来源**：与新任务书比对发现，issue 未提。书 potrs 节明文「残差对实现实际输入
  计算（升双精度），不得对构造侧 B64/A64」（我们 DPOT02 已合规）；potrf 节留白，
  我们 DPOT01 现用 A64（构造侧）。
- **裁定**：原负责人 2026-09-24 选②——**维持 A64**，不随 potrs 条款类推；待任务侧对
  potrf 节补字后再复核。NPU 与 CPU 参考同基（README 1.3 口径），配对阈值下判定
  格局稳定。
- **遗留**：请任务侧在 potrf 节补明残差 A 的取数口径（并入任务书反馈清单，与
  ε 修订、容差表、batchSize 对齐同路由）。
- **当前动作**：零代码改动（暂维持裁定已记 supplement 24i）；**未闭环**——
  ① 补字请求随任务书反馈清单路由（待向任务侧发出）；② 任务侧补字后按其口径复核，
  若定「实际输入升精」则改 DPOT01 kwargs 与 CPU 参考链路同基（几行）。
- 完成落点：

### HT-16 T8 摘除，batched 数值结论升正式 —— ✅ 完成〔新书比对〕

- **来源**：新任务书给出批量完整口径（逐矩阵判、case 内均值），T8「暂定方案」
  依据消失；issue B3 间接确认。
- **2026-09-24 核对结果**：plugin criteria 零 T8 注入点、零批量通路——批量实现
  （逐矩阵三层 + T8）在 reports/solver-s3、s4 两树，未归并 plugin。已完成：
  expectations 批量措辞更新（判定口径已官方定案，不再待标准确认）+ CRITERIA_VER
  s1-A5。**摘除动作转挂**：批量通路归并/重建入 plugin 时（与 HT-9 同批）执行，
  spec 文件 solver-s3-batched-spec.md 的「T8 暂定」标题一并改写。
- 完成落点（部分）：expectations.py 措辞 + thresholds.py 版本注（2026-09-24）。
- 完成落点（收束，2026-09-26 随阶段 2 树归并）：verdict/render_verify 批量通路
  flags 恒空、`layer1_pass_count`、数值结论升正式；render_verify 批量/纯脚本模板
  头部与 CLI 措辞同步（A6 全口径重基一并完成）；solver-s3-batched-spec.md §4
  标题与 T8 条目改写为「HT-16 收束」；全量 pytest 119/119 绿。

### HT-17 随机 SPD 均匀与正态各半 —— ✅ 完成〔新书比对〕

- **来源**：新书 3.2.1 条 4 新要求「随机 SPD（A = BᵀB + n I，均匀与正态各半）」；
  issue 未列。
- **做什么**：std 构造类已实现（每算子 2 条）；核对/补齐 B 元素分布为均匀与正态
  各半，canonical 登记对齐；复数同理（实/虚均匀与正态各半）。
- **核对结论（2026-09-26）**：五项全齐，无补齐项——
  1. 每算子恰 2 条 std（freeze_canonical.STD_CASES_BASE，实/复两册同构）；
  2. 各半成立：序数 9001→均匀组 U、9002→正态组 N（STD_DIST_BY_ORDINAL），
     每算子 1+1，单册 6 条 3+3、全集 12 条 6+6 恰 50/50；
  3. 分布参数与新书 experimental_standard.md「用例生成」表逐项一致：
     U(-5,5) / N(μ∈[-5,5], σ∈[0.1,2])（gen_spd_std/gen_rhs_std 转写
     ch_potrf/ch_potrs_verdict.py，右端 X_true 同表，正态组 μ/σ 按 verdict 序独立续抽）；
  4. 复数同理：实部底阵在前、虚部续抽同分布（U 组虚部 U(-5,5)、N 组虚部复用
     同一 μ/σ），实/虚各半；
  5. canonical 登记对齐：登记落点 = STD_DIST_BY_ORDINAL（代码常量，注释含
     README §1.4 表 #1/#10 与新书表出处）+ freeze_canonical.STD_CASES_BASE——
     冻结产物**不另加** dist 字段（real 册与 S1 冻结件逐字节一致的回归保护不动）；
     std_dist_of 表外序数 fail-closed（不猜测）。
  另记：新书式 A=BᵀB+nI 与蓝本 B·Bᴴ 的差异 gen_spd_std docstring 已有裁定
  （实现随蓝本，方阵满秩下两者同为 SPD，判定只看残差分布族），不另立。
- 完成落点：gen_data_cholesky.py STD_DIST_BY_ORDINAL 注记 HT-17 核对结论；
  case-gen tests/test_std_distribution.py 新增 2 测试（登记各半断言 +
  构造函数按蓝本抽取序逐位重演对拍 U/N×实/复×spd/rhs 全组合 + min eig ≥ n）；
  pytest 156/156 绿（2026-09-26，未 commit）。

### HT-18 INF/NAN 用例类 —— 🔴 悬置〔新书比对〕

- **来源**：新书 3.2.1 条 4「INF/NAN 按精度标准文档对应规则验收」；issue 未列。
  判定门的 ±inf 收窄规则已随 HT-1 落地，缺的是用例侧。
- **做什么**：生成含 INF/NAN 输入的用例类（用途标记独立于精度用例）、期望集从
  「证据不足」升级为按 opbase 2.3 收窄规则的明确期望；sim_dut 配套。
- **悬置（2026-09-26 拍板）**：期望口径——opbase 2.3 收窄规则下期望集的形态
  （哪些输入组合收窄到什么期望、与「证据不足」声明的边界）——待任务侧裁定后再
  施工，本轮只记悬置不落码；裁定回来后意向「十算子小集」（每算子少量代表用例
  先行验证口径）。
- 完成落点：

### HT-19 v3 重渲主卡 —— ✅ 完成〔派生收口〕

- **来源**：各改造项的派生收口（HT-1/HT-6/HT-11/HT-13 的「遗留」行 + HT-14/16
  完成后），issue 与新书共同派生。
- **做什么**：render_verify 按新口径一次重渲（动态锚点、2⁻¹³ 单套、摘 T7/T8、
  新阈值式、eps 披露、README 判定段），RENDERER_VER 递增；十包 v3 补丁是否发放
  由 原负责人 届时裁定。
- **2026-09-26 变更**：渲染器核心（判定机体/六算子块/批量与纯脚本模板 A6 全口径
  重基，RENDERER_VER=s4-D6、BATCHED_RENDERER_VER=s3-D4）经用户拍板提前至阶段 2
  树归并执行完毕；本卡收窄为**验收**——复渲逐字节一致 + 渲染副本与权威 criteria
  判定行为一致抽验 + README 判定段与申诉句核对。
- **前置**：criteria 批（HT-7/8/14/16）落完。
- 完成落点：验收脚本 verify/ht19_v3_render_accept.py 两道门全过（2026-09-26，
  报告 ht19_v3_render_accept.json）——①复渲确定性：十算子 render()×2，
  verify_accuracy/verify_perf 双件逐字节一致；②同输入同结论 16 项抽验（verdict
  归一逐项相等）：单矩阵 spotrf/cpotrs/spotri 各三态（ideal PASS/scale·conj 扰动
  FAIL/info=5 error——覆盖 potrs mean 双支式与 potri 单支式）、批量
  spotrfBatched/spotrsBatched 各 ideal/余槽 bit-wise 篡改 FAIL（槽位证据一致）/
  info 契约正例（infoArray 逐槽展开与标量两分型）；文面断言（2^-24、
  PENDING_RULING、max(5*ratio_cpu, 3*ratio_cpu_mean)）。验收抓漏修复：spotri/
  cpotri 专属块漏定义 FALLBACK_USES_MEAN（机体无条件引用，potri 副本一进 fallback
  即 NameError、FAIL 证据被内部异常污染）→ 补 `FALLBACK_USES_MEAN = False`，
  RENDERER_VER s4-D9。pytest 162/162 绿（未 commit）。

### HT-23 批量包 index 大文件处置 —— 🔴 挂起（2026-09-28 用户拍板暂停）

- **问题**：批量四包的 `cases/index.json` 内 sample_map 按槽位全展开（batch=1e6
  时单文件 254MB/包）；B2 回填件（ratio-batched/cases/index.json，四算子合集）
  达 1GB。两仓本地历史已含此形态大 blob（交接包 `08249aa`/`6d7dc7b`、
  community_task `d2da697`），**push 会挂**（平台普通 git 单文件 ~100MB）。
- **已实作后回退的方案**（commit `df3f185` → revert `8e1d65e`，2026-09-28）：
  - sample_map 摘要化——index 只落 `{k, rep_slots, slot_counts, note}`（KB 级），
    全量映射由 (seed, batch) 确定性纯函数 `derive_sample_map` 现场重算；A0 字面
    「固化随包发布」滚翻为「固化生成器+种子」（映射解释不一致的杜绝目的以更强
    形式达成，三方调同一函数逐位一致）；
  - 快装模式半成品（HT-23a，未提交已丢弃）——`--ratio-source reuse`：ratio 复用
    B2 回填件 + 首/中/尾 3 条现场重算对账（全量对账降级为抽验，用户曾拍板）。
- **回退理由**：用户 2026-09-28 指令「暂停，回退因大文件而做的修改，记录待办
  暂不处理」。回退后代码形态（gen s3-b6 / build s3-F10）与 build-v4 产物一致，
  164/164 绿。
- **候选方案（待拍板后实施）**：
  - 甲：重新落摘要化（+快装可选）——包 index KB 级，git 直接推；A0 固化语义
    滚翻需记录决策依据；
  - 乙：保留全量形态，两仓 filter-repo 剔 >50M blob（历史重写），交付走包外
    介质（对外包仍是全量 index，满足 A0 字面）；git 只留小文件与小包；
  - 丙：Git LFS——支持但配额紧（~1GB 贴上限）、消费方需装 git-lfs、每版全量
    index 多一份 LFS 存储；不推荐。
- **关联现状**：A-1 私集包产制、十包重装、两仓 push 全部挂在本条后面；冒烟/
  交付树不受影响（build-v4 十包与交付组完整可用）。

### HT-22 任务目录整理优化（对外内容）—— 🟢 可开工

- **决什么**（2026-09-27 用户指令）：两个 950 任务目录（实数/复数）原每算子混放
  cu 基准脚本（*_bench.cu）、编译脚本（build_run.sh）、日志（compile.log/run.log）、
  用例（cases.json）、性能结果（bench_result.json）。对外视角只需「用例 + 运行
  结果参考 + 我方交付包」。
- **做什么**：① build-v4 交付组十包按册归位到对应算子目录 `package/` 子目录；
  ② 三件套（DELIVERY_NOTE/LEDGER/PERF_COLLECTION_SUPPLEMENT）落任务目录根；
  ③ cu 脚本/编译脚本/日志/改动报告移 0923 根 `_internal_archive/<册>/<op>/`
  存档（非破坏性，可随时还原）；④ cases.json/bench_result.json 原位保留。
- 完成落点：2026-09-27 执行完毕——十包归位（每算子目录 = cases.json +
  bench_result.json + package/），三件套落两任务目录根，开发侧 42 件
  （每算子 4 件 + 两册 spotrsbatched 改动报告各 1 件）移
  community_task/9月/昇腾社区线上发放0923/_internal_archive/{实数,复数}/<op>/
  （504K，未删除可还原）。目录不在 git 管辖，本条目即唯一留痕。

### HT-21 info 契约派生基座双链口径差异（v4 复核记录）—— ✅ 记录不改码

- **来源**：v4 冻结改造交叉复核（TRAE-code-review 7741535，2026-09-27）第 4 项，
  双校验员 2/2 确认 minor。
- **现象**：S2 造数 staging 对冻结册直接跑 gen_data——册顶层无 package_scope →
  require_s1=True → info 契约用例从 s1 子集基派生；装包自检对包内切片（顶层
  package_scope:"full"）→ require_s1=False → 从全量切片基派生。两链 info 条目
  条数恒定（单矩阵 3/算子、批量 1/算子）但派生样本（n/case_id/seed）可不同，
  S2 证据与包内 index 的 info 条目无法逐条对账。
- **定性**：设计使然非缺陷——gen_data docstring 明文「基座扩池换 n/nrhs 样本」，
  条数恒定；info 判定只比 info==k_expected，与样本无关；判定链消费包内切片
  现场重造，自洽不受损。v4 冒烟（11/11、7/7）实证两链 info 均判过。
- **处置**：记录不改码。若后续要求 staging 证据与包内容逐条对账，需在 S2 造数
  入口按包口径注入 package_scope（属施工级变更，另行立项）。

### HT-20 申诉通道句入验收报告 —— 🟢 可开工〔新书比对〕

- **来源**：新书 3.2.2 各节「注：若残差超阈值判定不通过，也可举证算子实现无 bug
  并分析误差产生的原因」；issue 未提。
- **做什么**：验收报告残差 FAIL 项带一句申诉指引（流程性，一处模板句）。
- 完成落点：expectations.py 的 accuracy_item_from_verdict——APPEAL_NOTE 常量，仅
  「残差已算出且超阈的数值 FAIL」项 evidence 附句（残差不可计算/证据不足不附，
  贴任务书注文适用范围）；四路径 smoke 验证；pytest 86/86（2026-09-24，未 commit）。

## 原负责人补充描述（2026-09-24，约束后续施工）

1. **A0 不采用 npz**：「不采用 npz 了吧，就用我们之前的方案，现场构造，现场使用」——
   批量抽样通路沿用流式方案（生成→喂被测→判定→即弃），不经 npz 落盘中转。
2. **种子与 ratio_cpu_mean**：「ratio_cpu_mean 需要用所有的 case 来算 mean，而种子不同
   会导致 case 不同……所以我们希望对每个 case 都使用不同但对于本 case 固定的种子，
   这样 ratio_cpu_mean 可以被提前算出来，在每次 case 里复用，而不需要反复计算。」——
   即：种子唯一来源是 canonical 冻结件（逐 case 显式、互不相同）；A0 的摆放/填充子流
   也从 case seed 派生，禁止独立播种；mean 发布前算一次固化随包，只随用例集版本重算。
3. **验收种子不公开**：「这个逐 case 固定的种子不能提供给开发者，以防他们绕过校验」——
   种子分两域：开发者包内的自测种子公开（先造数后测照旧）；正式验收用**私有种子集**，
   只存验收侧，随其固化私集的 ratio_cpu/mean/sample_map。私集与公开集同规格网格、
   不同种子。注：此裁定在种子面上引入防绕过考量，与信任成本维「无恶意场景」先例
   （2026-09-17）构成例外，以本条为准。
