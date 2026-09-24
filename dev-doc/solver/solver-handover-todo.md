# solver 线交接待办簿

**怎么用**：先看总表挑活——🟢 直接做，🔴 先去拿裁定。做完一条：状态改 ✅，
在该条「完成落点」填产物或 commit，**不删条目**。仓级待办入口
`dev-doc/oprunway-todo.md` 指到本文件。只收录 已明示要记的条目。

## 总表

| 编号 | 一句话 | 状态 | 卡点 / 等谁 |
| --- | --- | --- | --- |
| HT-1 | max_abs 硬上限口径：采 opbase 最新标准动态锚点 | ✅ 完成 | 渲染口径随 v3 重渲；Codex checkpoint 待排 |
| HT-2 | A0：批量抽样验证通路（构造 + 判定） | 🟢 可开工 | 受补充描述三条约束 |
| HT-3 | A1：potrf/potrs 残差阈值换式 | 🟢 可开工 | 无 |
| HT-4 | A2：ratio_cpu_mean 预计算固化 | 🟢 可开工 | 无 |
| HT-5 | A3：potri 残差阈值换式 | 🟢 可开工 | 无 |
| HT-6 | A4：ε 口径——我方侧标注与披露 | ✅ 完成（我方侧） | 任务书侧 6 处数值修订待路由 |
| HT-7 | A5：判定次序改「先生态标准、不过再 LAPACK」 | 🟢 可开工 | 已裁（issue 选项①），待施工 |
| HT-8 | B2：非 batch 算子 info 契约判定支路 | 🟢 可开工 | 无 |
| HT-9 | B3：batch 算子 info 契约判定支路 | 🟢 可开工 | 「1~2 个矩阵」抽样下按代表内容解释，发包前与验收方对一句 |
| HT-10 | B4：确定性复跑检查 | 🟢 可开工 | 无 |
| HT-11 | C3：复数拆实/虚口径——T7 已摘 | ✅ 完成 | 已裁定 issue 即书面确认 |
| HT-12 | C4：info/确定性独立检查项 | ✅ 确认维持 | issue 已认可，零改动 |
| HT-13 | B1：正定性由构造保证的契约声明 | ✅ 完成 | batched README 模板创建时补同句（随 HT-2） |
| HT-14 | 〔issue C1〕混合容差换 2⁻¹³ + T3 双轨拆除 | 🟢 可开工 | 已裁「用新版」（24d），无卡欠账补立 |
| HT-15 | 〔新书比对〕potrf 残差计算基——暂维持 A64 | 🟡 等任务侧补字复核 | 反馈请求待路由；补字若定实际输入则需改 |
| HT-16 | 〔新书比对〕T8 摘除，batched 数值结论升正式 | 🟡 转挂树归并 | plugin 无批量通路（在 s3/s4 树），摘除随批量入 plugin 执行 |
| HT-17 | 〔新书比对〕随机 SPD 均匀与正态各半 | 🟢 可开工 | std 类已有底子，对齐分布比例 |
| HT-18 | 〔新书比对〕INF/NAN 用例类 | 🟢 可开工 | 判定门 ±inf 收窄已落（HT-1），缺用例侧 |
| HT-19 | 〔派生收口〕v3 重渲主卡 | 🟡 等前置 | 集齐 HT-1/6/11 遗留与各改造项后一次重渲 |
| HT-20 | 〔新书比对〕申诉通道句入验收报告 | ✅ 完成 | 落 expectations 逐项 evidence，仅残差超阈 FAIL 附句 |

## 逐条说明

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
  遗留：render_verify 渲染口径随 v3 重渲；sim_dut 的 NaN 扰动预期需随 HT-7 轮复核
  （单点 NaN 新口径下只计 1 失配点，可能过 layer1）；核心裁决逻辑 Codex checkpoint 待排。

### HT-2 A0 批量抽样验证通路 —— 🟢 可开工

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
- 完成落点：

### HT-3 A1 potrf/potrs 残差阈值换式 —— 🟢 可开工

- **做什么**：criteria/thresholds.py——potrf/potrs 换 `max(5·ratio_cpu, 3·ratio_cpu_mean)`；
  收紧式/上浮式及 30 地板/上限退役；batched 逐矩阵判，mean 取 case 级值。
- **背景**：`review-bundle-issues-0924.md` A1；0924 任务书 §3.2.2。
- 完成落点：

### HT-4 A2 ratio_cpu_mean 预计算固化 —— 🟢 可开工

- **做什么**：发布前用 CPU 参考链路算好写入 index，判定时只读、零重算——
  - 非批量：index 按算子存该算子全部正定精度用例的算术平均（单算子切片内，勿跨算子）；
  - 批量：case 条目存加权均值 Σ(count_j·ratio_j)/batchSize（与任务书「batch 个矩阵
    均值」数学等价，CPU 只算代表）；
  - info 用例与非正定矩阵不计入（原负责人 2026-09-24 引 issue B2 口径确认）；两类用例
    在 index.json 用 `case_purpose` 分列标记：精度判定 / info 契约检查；
    mean 只随用例集版本重算；
  - 验收私集另行固化一份（种子不公开，见补充描述 3）。
- **背景**：`review-bundle-issues-0924.md` A2；本文件补充描述 2、3。
- 完成落点：

### HT-5 A3 potri 残差阈值换式 —— 🟢 可开工

- **做什么**：criteria/thresholds.py——potri 换 `max(5·ratio_cpu, 0.1)`；
  mean 线不适用（CPU 残差均值被双归一压低一个量级，issue A3 论证）；0.1 为
  「合法实现散布上界 × 健康余量」标定，验证方式=合法参考实现逐 case 扫描、
  最薄余量 ≥3×。
- **背景**：`review-bundle-issues-0924.md` A3。
- 完成落点：

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

### HT-7 A5 判定次序：先生态标准、不过再 LAPACK —— 🟢 可开工（已裁）

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
- 完成落点：

### HT-8 B2 非 batch 算子 info 契约判定支路 —— 🟢 可开工

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
- 完成落点：

### HT-9 B3 batch 算子 info 契约判定支路 —— 🟢 可开工

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
- 完成落点：

### HT-10 B4 确定性复跑检查 —— 🟢 可开工

- **现状缺口**：「合法用例重复执行 bit-wise 一致」只有证据不足占位，无复跑驱动
  与比对实现。
- **做什么**：同一输入重复执行 ≥5 次，输出（含 info）之间 bit-wise 一致即通过。
  施工细节：第 2..n 次全部与第 1 次比对即等价于两两一致，比较次数线性。
- **背景**：`review-bundle-issues-0924.md` B4（原负责人 2026-09-24 贴文确认）。
- 完成落点：

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
- 完成落点：

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

### HT-16 T8 摘除，batched 数值结论升正式 —— 🟡 转挂树归并〔新书比对〕

- **来源**：新任务书给出批量完整口径（逐矩阵判、case 内均值），T8「暂定方案」
  依据消失；issue B3 间接确认。
- **2026-09-24 核对结果**：plugin criteria 零 T8 注入点、零批量通路——批量实现
  （逐矩阵三层 + T8）在 reports/solver-s3、s4 两树，未归并 plugin。已完成：
  expectations 批量措辞更新（判定口径已官方定案，不再待标准确认）+ CRITERIA_VER
  s1-A5。**摘除动作转挂**：批量通路归并/重建入 plugin 时（与 HT-9 同批）执行，
  spec 文件 solver-s3-batched-spec.md 的「T8 暂定」标题一并改写。
- 完成落点（部分）：expectations.py 措辞 + thresholds.py 版本注（2026-09-24）。

### HT-17 随机 SPD 均匀与正态各半 —— 🟢 可开工〔新书比对〕

- **来源**：新书 3.2.1 条 4 新要求「随机 SPD（A = BᵀB + n I，均匀与正态各半）」；
  issue 未列。
- **做什么**：std 构造类已实现（每算子 2 条）；核对/补齐 B 元素分布为均匀与正态
  各半，canonical 登记对齐；复数同理（实/虚均匀与正态各半）。
- 完成落点：

### HT-18 INF/NAN 用例类 —— 🟢 可开工〔新书比对〕

- **来源**：新书 3.2.1 条 4「INF/NAN 按精度标准文档对应规则验收」；issue 未列。
  判定门的 ±inf 收窄规则已随 HT-1 落地，缺的是用例侧。
- **做什么**：生成含 INF/NAN 输入的用例类（用途标记独立于精度用例）、期望集从
  「证据不足」升级为按 opbase 2.3 收窄规则的明确期望；sim_dut 配套。
- 完成落点：

### HT-19 v3 重渲主卡 —— 🟡 等前置〔派生收口〕

- **来源**：各改造项的派生收口（HT-1/HT-6/HT-11/HT-13 的「遗留」行 + HT-14/16
  完成后），issue 与新书共同派生。
- **做什么**：render_verify 按新口径一次重渲（动态锚点、2⁻¹³ 单套、摘 T7/T8、
  新阈值式、eps 披露、README 判定段），RENDERER_VER 递增；十包 v3 补丁是否发放
  由 原负责人 届时裁定。
- **前置**：criteria 批（HT-7/8/14/16）落完。
- 完成落点：

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
