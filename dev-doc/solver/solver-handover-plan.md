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

1. [x] **HT-5** potri fallback_threshold → `max(5·ratio_cpu, 0.1)`。完成判据除 pytest
   外，按 issue A3 验证式：合法参考实现逐 case 残差对阈值扫描，最薄余量 ≥3×。
2. [x] **HT-14** 混合容差收单套 rtol=atol=2⁻¹³；拆 verdict 的 standard 子字典、
   T3 注入、双套聚合与报告字段（手法参照 HT-1 拆双解释，全消费点追踪）。
3. [x] **HT-7** cards：potrf 主判/诊断对调（factor vs golden 转正、recon 降诊断）；
   potri 收单目标（A·A⁻¹ vs I 挂诊断）；三算子主判基准 golden32→golden64。
   顺带：sim_dut NaN 扰动预期复核（新口径单点 NaN 只计 1 失配点，实测决定改扰动
   强度还是改预期，最小侵入）；清理 verdict 两处「拆实/虚待确认」残句。
4. [x] **HT-3** potrf/potrs fallback_threshold → `max(5·ratio_cpu, 3·ratio_cpu_mean)`，
   tighten/floatup 退役。mean 消费：verdict 从 case_arrays 读 `ratio_cpu_mean`，
   accept_run 从 index 顶层注入；消费面同时核 stream_check 与渲染源（阶段 4 重渲收口）。
   缺 mean 语义（**本 plan 定义的兼容策略，fail-closed 方向，非既定裁定**）：
   单支 5·ratio_cpu，过即 PASS；超出记证据不足不判 FAIL，注明待含 mean 的 v3 包；
   新产包一律必须带 mean。
5. [x] 批末 CRITERIA_VER 递增一次，注明四项。（已完成：s1-A5→s1-A6，2026-09-26）

## 阶段 2：树归并（批量与纯脚本通路入 plugin）

现状：plugin 无批量判定通路、无纯脚本装包通路——在 reports/solver-s3/tree
（批量+gen 并行修复）与 reports/solver-s4/tree（非批量纯脚本 build_purescript、
renderer s4-D5）；**无工作区时以交接包 trees/ 下两份为准**。

1. [x] 三方逐文件 diff（s3 树、s4 树、plugin），列差异清单再动手；取舍原则：
   保留 plugin 的 0924 裁定成果（阶段 1 产物），移植树上的独立功能段。
2. [x] 归并 s4 非批量纯脚本通路（build_purescript 路由、模板、渲染扩展段），
   验收：零数据数组、index 全 `materialize:"gen"`、复渲逐字节一致。
3. [x] 归并 s3 批量通路（batched 卡、逐矩阵三层、批量渲染、gen 边界态预扫描修复
   ——修复即原 gen 并行断链问题，详见该树 gen_data_cholesky.py docstring 的
   边界态预扫描段，无 HT 卡）。归并时批量卡
   **显式套用阶段 1 新口径**（golden64、2⁻¹³ 单套、动态锚点、新阈值式）并执行
   **HT-16**：T8 注入点摘除、solver-s3-batched-spec.md「T8 暂定」标题改写、tests 同步。
   （执行记录 2026-09-26：阶段 1 新口径同步**扩大到 render_verify 全机体**——
   判定机体、六算子块、批量/纯脚本模板、_OP_SPECS 全量重基 A6，RENDERER_VER=s4-D6、
   BATCHED_RENDERER_VER=s3-D4；原阶段 4 HT-19 的渲染器核心**提前至本步执行**，
   经用户拍板「提前同步」——build_package 已直接消费本入口且 fail-closed 断言
   不允许接线对 A6、机体留旧式的中间态。验证：十算子渲染冒烟 + 复渲逐字节
   一致 + 全量 pytest 119/119 绿。）
4. [ ] 全量测试重跑（已过：119/119，2026-09-26）+ Codex checkpoint（核心裁决
   逻辑，无条件触发；环境无 Codex，替代方式待与任务方确认——候选 TRAE-code-review）。

## 阶段 3：case-gen 改造（HT-2、4、8、9、17、18）

文件在 plugin/skill/repo-task-solver-case-gen/：scripts/{gen_data_cholesky,
build_package,freeze_canonical,fill_ratio_cpu}.py、assets/、references/。

1. [x] **HT-2** A0 抽样通路：min(5,batchSize) 代表；内容/摆放/填充三条流全部从
   case seed 派生（禁独立播种）；sample_map={content_idx:{rep_slot,slots[]}} 固化；
   **不落 npz，流式现场构造现场使用**（stream_check 型 DUT 挂钩）；accept 侧先余槽
   bit-wise 一致性（out32+info，potrs 的同内容=A、B 都同）后 rep_slot 三层判定；
   无 sample_map 旧包走全遍历兼容分支。三条硬约束原话见看板「原负责人补充描述」。
2. [x] **HT-8** 非批量 info 用例与支路：每族 3 个中等规模非正定变体（第 k 阶对角
   减大数，k_min 构造即知）+ potri 奇异因子 + potrs 非法参数路径（info=-i）；
   k_expected 与 case_purpose 入 index；judge 按 case_purpose 分流（info 用例只比
   info，不进残差不进 mean）；装包自检按用途分流放行；info 与确定性各出**独立结论**
   （HT-12 口径）；sim_dut 配套正/负例。
3. [x] **HT-9** batched info：1 个混合 case，1~2 个**代表内容**非正定（发包前与
   验收方确认此解释）；infoArray[i]==k_expected 逐矩阵、infoArray[0]=-i、
   potrsBatched 标量仅参数错。
4. [x] **HT-17** 随机 SPD/HPD 补齐「均匀与正态各半」（std 类已有底子，对齐分布
   与 canonical 登记）。**HT-18** INF/NAN 用例类（判定门已备，补用例生成与期望
   升级，期望按 opbase 2.3 收窄规则）——HT-18 记悬置不施工（2026-09-26 拍板：
   期望口径待任务侧裁定，意向十算子小集）。
5. [x] **HT-4** mean 固化（**排最终用例集定稿后**，即 HT-8/9/17/18 全落再算）：
   非批量 index 顶层按算子（只算本算子正定精度用例）；批量 case 级加权
   Σ(count_j·ratio_j)/batchSize；info 用例/非正定矩阵不计入；批量 README 模板
   创建时带 B1 构造性正定同句（HT-13 遗留）。
6. [x] SKILL.md 与 references 同步：两 skill 的算子清单（十算子）、纯脚本/A0 形态、
   新字段（sample_map/case_purpose/k_expected/ratio_cpu_mean）、已裁口径——
   目前文本仍是旧状态，不同步则 skill 不可用。

（执行记录 2026-09-26：六步全收官，pytest 158/158 绿。① HT-2 Step A-E 全链——
gen 三流派生/derive_sample_map/expand_sampled_rows、accept 侧 batched_a0.py 新建
（余槽 bit-wise 一致性 + 代表槽三层）、stream_check 批量分支、render_verify 批量
模板、build_batched 纯脚本装包、批量 README 模板新建（HT-13 B1 同句欠账清）；
test_a0_sampling.py 9 例，145/145 绿。② HT-8 全链——derive_info_cases/build_info_arrays
（构造性自检 fail-closed）、render_verify _judge_info_inner、sim_dut s4-E5、
build_purescript 分流、purescript README；端到端冒烟全过。③ HT-9 全链——
derive_batched_info_cases/build_batched_info_arrays、batched_a0._judge_a0_info、
stream_check 批量 info；修复 2 bug（INFO_PROBE_OF KeyError、标量非 0 预检拦截）；
7 步冒烟全过，154/154 绿。④ HT-17 核对五项全齐 + test_std_distribution.py 2 例
（156/156）；HT-18 记悬置。⑤ HT-4——build_package 两助手+两路接线（s3-F8）、
render_verify 模板 _judge_a0 补 mean 透传（权威-副本同构分歧补齐）+ 两 CLI index
mean 读取注入（s4-D8/s3-D7）、两 README 模板 mean 句（assets 目录 md 写入异常坐实，
Shell 原子写入处置，此前 Edit 落盘内容会回退/混层）、test_mean_freeze.py 2 例；
冒烟七步全过，含消费铁证（同扰动 dut：原包 FAIL → index mean=1e30 后 PASS，
3m 第二支真被消费）与十算子复渲确定性（158/158）。⑥ SKILL.md/package-contract.md
同步 A0/info/mean 口径与字段面；min/median/max 摘要（HT-2 后已退役）与 purescript
README 结论含义节 T3/T4/T7（HT-14/HT-16 后 flags 恒空）过时措辞清零；prep_failed
装包 fail-closed 口径入检查条件表。）

## 阶段 4：收口（HT-10、HT-19）

1. [x] **HT-10** 确定性复跑：≥5 次、第 2..n 与首次比对（含 info），独立结论。
   落点：stream_check `--rerun N`（s3-d3-r5）+ `_bitwise_same`/`_rerun_compare`
   字节域三键比对（out32/info bit-wise、status 相等；NaN 按位等同、-0.0≠0.0）、
   逐 case `determinism` 子记录、summary det_pass/det_fail、确定性失配令退出码 1；
   info 契约用例不参与（构造性探针）；expectations KIND_DETERMINISM 占位联动更新；
   新增 test_determinism_rerun.py 4 测试（字节域语义/三键比对/端到端正例/
   monkeypatch 负例）；真实册 --rerun 5 冒烟单矩阵 8/8、批量 6/6 全 det PASS。
2. [x] **HT-19** v3 重渲：渲染器核心已于阶段 2/3 提前落地（RENDERER_VER
   s4-D6→s4-D8、BATCHED_RENDERER_VER s3-D4→s3-D7），本卡收窄为验收
   （verify/ht19_v3_render_accept.py）。验收抓漏修复：spotri/cpotri 专属块漏定义
   FALLBACK_USES_MEAN（机体无条件引用，potri 副本一进 fallback 即 NameError、
   FAIL 证据被内部异常污染）→ 补 `FALLBACK_USES_MEAN = False` 对齐权威卡语义，
   RENDERER_VER s4-D9。两道门全过：①复渲确定性十算子 × 双件逐字节一致；
   ②同输入同结论 16 项抽验（单矩阵 spotrf/cpotrs/spotri 各三态——含 potrs mean
   双支与 potri 单支式；批量 spotrfBatched/spotrsBatched 各 ideal/余槽篡改/info
   契约正例，verdict 归一逐项相等）+ 文面断言（2^-24、PENDING_RULING、
   max(5*ratio_cpu, 3*ratio_cpu_mean)）。报告 verify/ht19_v3_render_accept.json。
3. [x] 批次 checkpoint——经用户拍板以 TRAE-code-review 替代（环境无 Codex，阶段 2
   同先例）。范围=阶段 4 未提交变更（stream_check HT-10 + render_verify s4-D9 +
   expectations 占位 + 新增 test_determinism_rerun.py，+80/-8）。双乙交叉验证结论：
   **无阻断级发现**。FALLBACK_USES_MEAN 修复实证确认正确完备（机体引用点唯一、
   六块定义与权威卡逐一对齐、_PERF_TEMPLATE 不涉该名、批量渲染零漂移保持、potri
   副本 fallback 冒烟走单支 0.1 地板无 NameError）。3 条 minor 经用户拍板**只记录
   不修**：①`_check_op_spec` 缺块内 FALLBACK_USES_MEAN ↔ card.fallback_uses_mean
   一致性断言（防静默漂移，未来同类施工补）；②stream_check det 计数先于记录落定，
   判定异常落 error 桩时 summary 计数与 determinism 条数可失配（低概率，退出码不
   受影响）；③determinism.runs 记计划次数非实际执行次数（first_diff.run 可推断
   止点，字段语义待注释）。主审初筛另两条（expectations KIND_INFO 措辞、测试裸
   open）经证伪判误报/不值得报。162/162 绿。

## 阶段 5：产包与交付

1. [x] 十包本地产齐（2026-09-26，用户拍板本地执行 + 锁 numpy 2.3.3/scipy 1.16.2，
   venv-solver；远程容器留待真机验证）。S1–S5 全链：三册冻结（real/complex 各
   532 例 s1 子集 24；batched 752 例）→ 造数 → ratio 回填 → 基线 → 装包，十包
   装包自检全部打钩（ratio 回填 + mean 固化 + renderer s4-D9 + 零数据 + 指纹），
   产物 `build-v3/packages/` + 逐包 selfcheck.json。
2. [x] 分文件夹组装齐（`build-v3/delivery/cholesky-{real,complex}-950/`）：各 5 包
   + 三件套（DELIVERY_NOTE/DELIVERY_LEDGER/PERF_COLLECTION_SUPPLEMENT，由 case-gen
   assets 模板经 verify/assemble_delivery_v3.py 渲染，指纹/固化字段现场读包不手抄；
   批量包含 A0 sample_map 槽位映射最大 ~59 MB，交付口径已如实声明）。
3. [x] 验收私集机制就绪（2026-09-26）：freeze_canonical 种子域参数化
   （--seed-base/--std-seed-base，公域默认 923000000/20250912 产物逐字节不变，
   conventions.seed 随实际基值记录）；私域基值 920284139/51523761 落
   frozen/private/SEEDS.md（机密，本地 git 留痕经用户拍板）；机械断言
   test_private_seed_base.py 2 测试（公域零漂移 + 私域平移不相交）；164/164 绿。
   私集三册冻结与产包待执行（随第 1 步产包一并，私集包整体落 frozen/private/
   不进交付树）。
4. [x] 冒烟矩阵四轴全过（报告归档 `build-v3/smoke/`）：实/复 × 非批量/批量正例
   +info+确定性（--rerun 5）——spotrf 11/11 det 8/8、cpotrs 11/11 det 8/8、
   spotrfBatched 7/7 det 6/6、cpotrsBatched 7/7 det 6/6；负例轴 spotrf scale 扰动
   11/11 全 FAIL 且 det 8/8 独立过。冒烟抓漏两处已修（stream_check s3-d3-r6）：
   ① det 比对去 tobytes 改 uint8 视图（批量大 case 免整块复制）；② 包内 index
   顶层 ratio_cpu_mean 注入 case_arrays（此前流式通路缺 mean，负例兜底双支不可算
   误记 error——修复后负例 11/11 全 FAIL err=0）。
   **批量确定性复跑内存口径**：批量包 --rerun 5 单进程峰值 43–61.5 GB（-0090
   n=1040×batch=1739 全批展开×复跑副本），验收侧须 ≥64GB 内存独占执行，禁止
   批量冒烟并发；skill 侧测试 164/164 绿。
5. [x] **全量精度用例决策 v4 重产**（2026-09-27，用户决策+拍板执行）：精度用例
   =刷新后 cases.json 全量（消掉「8 条 vs 全部 case」的 ratio_cpu_mean 口径歧义，
   任务书「所有 case 平均」自此无歧义）。前置：任务目录刷新基线坐实（spotrf 143
   不变；spotrs 删大 n 39 + 增 nrhs 组合 23 → 174；spotri 删 >4096 54 条 → 143；
   批量各删 37 增 batch=1e6 3 条 → dedup 151）；spotrs 多右端判定口径经拍板=
   逐列判取最差（DPOT02 原生实现，零改动贯通）。冻结改造（skill 仓 commit
   7741535）：freeze_canonical 对账 154→151；gen_data info 派生 require_s1 参数化
   + 切片顶层 package_scope 事实源；build_package（s3-F9）--scope {s1,full} 缺省
   full，两处切片段/自检对账同口径。全链 v4（build-v4/，交接包 commit 6d7dc7b）：
   real/complex 各 466 例、batched 604 例；回填 932+604 ok 全 0 prep_failed、画像
   硬门全过（potrf max 0.524/potri max 0.649 <1）；十包 --scope full 全生效
   （spotrs 包 176 精度条目含 nrhs>1 共 101 条，nrhs 贯通实证）；交付组组装
   （assemble_delivery_v4.py）；冒烟四轴正例全同构 v3 + 全量抽验轴（spotrs 全册
   n≤512 85/85 含 nrhs 用例；spotrfBatched 抽 20 条 21/21）。
   **过程事件**：磁盘满（100%）致 complex 首跑中断——改逐算子流式管线（gen→fill→
   删 npz，单算子峰值 ~15G）后通过；批量 --rerun 5 峰值实测 25–47GB（新目录删大 n
   后较 v3 的 43–61.5GB 显著下降，但仍须独占禁并发）。
   **耗时实测（全量形态）**：冻结秒级、单册造数 ~8min、单册回填 ~10min、装包
   1–12min/包、全链 ~90min——串行足够，单矩阵 fill 并行改造不立项（批量侧已有
   workers；三处重复计算为契约性独立证据，保留）。

## 阶段 6：对外项（须任务侧确认）

1. [ ] 任务书反馈清单路由：ε 2⁻²⁴（6 处）、容差表按新版、失效 URL、batchSize
   3000/30000 对齐、potrf 节补残差基口径。
2. [ ] **HT-15** 复核：任务侧补字若定「实际输入」，连带改 DPOT01 kwargs、CPU 参考
   链同基、重算 ratio/mean、重渲并重产受影响包——不是只改一行。

## 完成定义

看板 20 条全 ✅（或 已明示搁置）；十包按任务书分文件夹、指纹对账齐；
两 skill tests 全绿、渲染确定性与行为一致性成立；checkpoint 无阻断级发现。
