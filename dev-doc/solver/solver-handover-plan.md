# solver 线交接执行 plan（给交接 agent）

分工：`solver-handover-todo.md` 是看板（状态与背景），本文档是执行序。
**每完成一步：本文档打勾 + 看板对应 HT 条改 ✅ 填完成落点**。看板 20 条中
HT-1/6/11/12/13/20 已 ✅，无需执行；本 plan 覆盖其余全部。

总目标：完成全部 todo → 两 skill 满足 0924 任务书与 issue 要求 → 用改造后的
skill 产出两任务书全部 10 包 → 按任务书分文件夹交付。

口径基准：精度章按 0924 版任务书（repos/new_task_doc-0924，与 PR #44 精度章逐字同）；
性能门 0.35 与批量范围 n≤4096/batch≤30000 按 PR #44（repos/community_task 分支
pr-44）——**产包前核对上游 main 是否已合入，未合入与任务侧确认**。裁定原话在
`solver-skill-supplement.md` 24a-24i，与 plan 冲突时以裁定为准。

执行纪律（背景规则会自动注入，此处只列会踩的门）：
- 首次改 plugin/skill 前按 skill-edit-gate 提示读 skill-best-practices 并调用
  skill-creator，md 改动过 doc_style_lint 退 0；
- 测试用带 scipy 的 venv（无则自建 numpy/scipy/pytest）；真机与大规格在远程容器；
  **无昇腾环境时**：除真机执行与性能实采外全部工作可本地完成，真机项记「待真机」；
- 悬置项不替 原负责人 拍板；不 push/merge/对外发布，除非明示；
- 「绿」的判据是行为覆盖+全通过，不是测试数量不降（拆双轨可合法删旧断言）。

## 阶段 1：criteria 快批（HT-5→14→7→3，串行，每步全量 pytest 绿再进）

文件都在 plugin/skill/repo-task-solver-accept/：criteria/{thresholds,verdict,
cards_cholesky}.py、criteria/tests/、scripts/{accept_run,sim_dut,expectations}.py。

1. [ ] **HT-5** potri fallback_threshold → `max(5·ratio_cpu, 0.1)`。完成判据除 pytest
   外，按 issue A3 验证式：合法参考实现逐 case 残差对阈值扫描，最薄余量 ≥3×。
2. [ ] **HT-14** 混合容差收单套 rtol=atol=2⁻¹³；拆 verdict 的 standard 子字典、
   T3 注入、双套聚合与报告字段（手法参照 HT-1 拆双解释，全消费点追踪）。
3. [ ] **HT-7** cards：potrf 主判/诊断对调（factor vs golden 转正、recon 降诊断）；
   potri 收单目标（A·A⁻¹ vs I 挂诊断）；三算子主判基准 golden32→golden64。
   顺带：sim_dut NaN 扰动预期复核（新口径单点 NaN 只计 1 失配点，实测决定改扰动
   强度还是改预期，最小侵入）；清理 verdict 两处「拆实/虚待确认」残句。
4. [ ] **HT-3** potrf/potrs fallback_threshold → `max(5·ratio_cpu, 3·ratio_cpu_mean)`，
   tighten/floatup 退役。mean 消费：verdict 从 case_arrays 读 `ratio_cpu_mean`，
   accept_run 从 index 顶层注入；消费面同时核 stream_check 与渲染源（阶段 4 重渲收口）。
   缺 mean 语义（**本 plan 定义的兼容策略，fail-closed 方向，非既定裁定**）：
   单支 5·ratio_cpu，过即 PASS；超出记证据不足不判 FAIL，注明待含 mean 的 v3 包；
   新产包一律必须带 mean。
5. [ ] 批末 CRITERIA_VER 递增一次，注明四项。

## 阶段 2：树归并（批量与纯脚本通路入 plugin）

现状：plugin 无批量判定通路、无纯脚本装包通路——在 reports/solver-s3/tree
（批量+gen 并行修复）与 reports/solver-s4/tree（非批量纯脚本 build_purescript、
renderer s4-D5）；**无工作区时以交接包 trees/ 下两份为准**。

1. [ ] 三方逐文件 diff（s3 树、s4 树、plugin），列差异清单再动手；取舍原则：
   保留 plugin 的 0924 裁定成果（阶段 1 产物），移植树上的独立功能段。
2. [ ] 归并 s4 非批量纯脚本通路（build_purescript 路由、模板、渲染扩展段），
   验收：零数据数组、index 全 `materialize:"gen"`、复渲逐字节一致。
3. [ ] 归并 s3 批量通路（batched 卡、逐矩阵三层、批量渲染、gen 边界态预扫描修复
   ——修复即原 gen 并行断链问题，changes-brief 24g，无 HT 卡）。归并时批量卡
   **显式套用阶段 1 新口径**（golden64、2⁻¹³ 单套、动态锚点、新阈值式）并执行
   **HT-16**：T8 注入点摘除、solver-s3-batched-spec.md「T8 暂定」标题改写、tests 同步。
4. [ ] 全量测试重跑 + Codex checkpoint（核心裁决逻辑，无条件触发）。

## 阶段 3：case-gen 改造（HT-2、4、8、9、17、18）

文件在 plugin/skill/repo-task-solver-case-gen/：scripts/{gen_data_cholesky,
build_package,freeze_canonical,fill_ratio_cpu}.py、assets/、references/。

1. [ ] **HT-2** A0 抽样通路：min(5,batchSize) 代表；内容/摆放/填充三条流全部从
   case seed 派生（禁独立播种）；sample_map={content_idx:{rep_slot,slots[]}} 固化；
   **不落 npz，流式现场构造现场使用**（stream_check 型 DUT 挂钩）；accept 侧先余槽
   bit-wise 一致性（out32+info，potrs 的同内容=A、B 都同）后 rep_slot 三层判定；
   无 sample_map 旧包走全遍历兼容分支。三条硬约束原话见看板「原负责人补充描述」。
2. [ ] **HT-8** 非批量 info 用例与支路：每族 3 个中等规模非正定变体（第 k 阶对角
   减大数，k_min 构造即知）+ potri 奇异因子 + potrs 非法参数路径（info=-i）；
   k_expected 与 case_purpose 入 index；judge 按 case_purpose 分流（info 用例只比
   info，不进残差不进 mean）；装包自检按用途分流放行；info 与确定性各出**独立结论**
   （HT-12 口径）；sim_dut 配套正/负例。
3. [ ] **HT-9** batched info：1 个混合 case，1~2 个**代表内容**非正定（发包前与
   验收方确认此解释）；infoArray[i]==k_expected 逐矩阵、infoArray[0]=-i、
   potrsBatched 标量仅参数错。
4. [ ] **HT-17** 随机 SPD/HPD 补齐「均匀与正态各半」（std 类已有底子，对齐分布
   与 canonical 登记）。**HT-18** INF/NAN 用例类（判定门已备，补用例生成与期望
   升级，期望按 opbase 2.3 收窄规则）。
5. [ ] **HT-4** mean 固化（**排最终用例集定稿后**，即 HT-8/9/17/18 全落再算）：
   非批量 index 顶层按算子（只算本算子正定精度用例）；批量 case 级加权
   Σ(count_j·ratio_j)/batchSize；info 用例/非正定矩阵不计入；批量 README 模板
   创建时带 B1 构造性正定同句（HT-13 遗留）。
6. [ ] SKILL.md 与 references 同步：两 skill 的算子清单（十算子）、纯脚本/A0 形态、
   新字段（sample_map/case_purpose/k_expected/ratio_cpu_mean）、已裁口径——
   目前文本仍是旧状态，不同步则 skill 不可用。

## 阶段 4：收口（HT-10、HT-19）

1. [ ] **HT-10** 确定性复跑：≥5 次、第 2..n 与首次比对（含 info），独立结论。
2. [ ] **HT-19** v3 重渲：render_verify 全口径同步（动态锚点、2⁻¹³、摘 T7/T8、
   新阈值式与 mean、eps 披露、README 判定段、申诉句），RENDERER_VER 递增。
   前置门：最终用例集与 mean 已固化、批量/纯脚本模板齐。验收：复渲逐字节一致
   +**渲染副本与权威 criteria 判定行为一致**（同输入同结论抽验）。
3. [ ] 批次 Codex checkpoint（八维；含 B1 模板欠账）。

## 阶段 5：产包与交付

1. [ ] 用改造后两 skill 正式流程产 10 包（远程容器；批量 4 包 A0 抽样形态）。
2. [ ] 按任务书分文件夹：`cholesky-real-950/`（spotrf/spotrs/spotri/spotrfbatched/
   spotrsbatched）、`cholesky-complex-950/`（c 族同构），各含交付说明+对账表+
   性能采集说明；结构先例见交接包 delivered-packages/ 两个 tar。
3. [ ] 验收私集：与公开集同规格网格、异种子；独立固化 ratio_cpu/mean/sample_map；
   只存验收侧（ignored 区或远端保护区），消费方式与公开集同接口，防误用公开集
   统计量。
4. [ ] 冒烟矩阵覆盖四轴：实/复 × 非批量/批量 × 精度正例负例 × info/确定性支路，
   至少各轴命中一次；逐包自检+指纹入对账表。

## 阶段 6：对外项（须任务侧确认）

1. [ ] 任务书反馈清单路由：ε 2⁻²⁴（6 处）、容差表按新版、失效 URL、batchSize
   3000/30000 对齐、potrf 节补残差基口径。
2. [ ] **HT-15** 复核：任务侧补字若定「实际输入」，连带改 DPOT01 kwargs、CPU 参考
   链同基、重算 ratio/mean、重渲并重产受影响包——不是只改一行。

## 完成定义

看板 20 条全 ✅（或 已明示搁置）；十包按任务书分文件夹、指纹对账齐；
两 skill tests 全绿、渲染确定性与行为一致性成立；checkpoint 无阻断级发现。
