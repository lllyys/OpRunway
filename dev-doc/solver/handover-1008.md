# 会话收尾交接（2026-10-08）

本文件记录 2026-10-07～10-08 会话完成的工作、裁定与结论，接续
`solver-handover-plan.md`（执行序）与 `solver-handover-todo.md`（看板）。
验证细节的单一事实源是 `frozen/private/VERIFICATION.md`，本文件只做收口索引。

## 一、任务书精度标准修复（HT-6 落地，✅ 已完成）

两册任务书 §3.2 精度章从旧版标准对齐到 opbase 新版
`docs/zh/ops_precision_standard/mixed_tolerance_standard.md`：

| 旧版 | 新版 |
| --- | --- |
| rtol = 2⁻¹⁰ (9.77e-4) | **rtol = 2⁻¹³ (1.2e-4)** |
| atol = 2⁻¹⁶ (1.53e-5) | **atol = 2⁻¹³ (1.2e-4)** |
| max_abs_error_limit = 1e-2 or 32·ULP | **max( 1e-2, 32·ULP(g_low) ) 动态锚点** |
| ε = 2⁻²³ ≈ 1.19e-7（3 处/册） | **ε = 2⁻²⁴ ≈ 5.96e-8（unit roundoff，SLAMCH('E') 口径）** |
| experimental_standard.md（2 处/册） | **mixed_tolerance_standard.md** |

另在逐元素判定条目下补新标准收窄规则：max_abs 只取有限值比对点、g_low 按
输出 dtype RNE 收窄、±inf 与 golden 收窄后同号即通过。

**ε 为什么是 2⁻²⁴ 不是 2⁻²³**（易混点，写死在此）：2⁻²³ 是 float32 相邻
浮点数的间距（machine epsilon），RNE 舍入的最大相对误差是其一半 2⁻²⁴
（unit roundoff）。残差公式衡量舍入误差，且 LAPACK 生态 `SLAMCH('E')`
即 2⁻²⁴——旧文「单位舍入，ε=2⁻²³」是名值打架，已改正。

**判定脚本校验结论**：`repo-task-atk-test` 仓 criteria（thresholds.py/
verdict.py，HT-14/A4 产物）实测逐条符合新版（rtol=atol=2⁻¹³、
matched_ratio 0.99、max_abs 动态锚点、RNE 收窄、ε=2⁻²⁴）——**无需改动**；
任务书与验收脚本的双套矛盾自此收拢为单套。

**落点与状态**：`community_task/9月/昇腾社区线上发放0923/` 下两册
`Atlas950_{S,C}potrf_..._task_doc.md`（实数册 L281-286/L296/L526、复数册
L287-292/L302/L533）。**在 community_task 仓 `solver_1010` 分支，未提交**。

## 二、裁定记录（2026-10-08，用户拍板）

| 项 | 裁定 |
| --- | --- |
| HT-18 INF/NAN 用例 | **暂不新增**，维持悬置 |
| HT-15 potrf 残差基 | **挂起**（详解：potrf 节留白 A 取构造侧 A64 还是实际输入；裁定先例 24i=维持 A64；若补字选甲=按裁定落字零代码、乙=对齐 potrs 条款需重算 ratio/mean 重产四包；我建议甲，待拍板） |
| HT-23 交接包大文件 | **挂起**（甲摘要化/乙 filter-repo/丙 LFS） |
| HT-24 potri 直审宽容 | **挂起**（问题说明已产出待转研发：`issue-spotri-direct-review-loose-1007.md`） |
| 批量验证问题记录缺失 | 已补：VERIFICATION §3.1（首跑实录+误读更正）、§3.2（三根因）、§3.4（r2 结果） |
| 「1~2 个矩阵按代表内容解释」（B6） | 已解释：A0 抽样下非正定按代表内容构造、其映射全槽随之非正定；记录在看板 HT-2/HT-9，落字时机随任务书定稿 |
| 簿记/commit | 上轮暂不处理；本文件提交为例外（用户明示授权） |

## 三、批量四包端到端验证（✅ 已闭环，十包验证全部完成）

- **首跑（10-07）判定未生效**，4 个根因（全是调用侧脚本问题，
  **包内交付件零改动**，git 工作区干净可证）：①dut-out 相对路径被 verify
  以包目录为 cwd 解析 → 全轴证据不足（对话曾误读 neg rc=1 为期望 FAIL，
  已更正）；②info 用例传 index 固化 id，verify 走 canonical 现场派生 id
  → rc=2（**HT-21 双链口径差异实锤**，全量跑不受影响，仅 --case-id 命中）；
  ③cpotrfBatched 造 npz 疑 OOM 中断；④r2 首试暴露：rs 族精度轮 info 须
  标量（仅报参数错），写成数组被 verify 正确拒收——结构预检按设计工作。
- **r2（10-08）四包五轴全过**：pos（golden32 铺槽）rc=0；negA（rep 单槽×2）
  FAIL 归因槽位；negB（内容0 全槽×2）FAIL 归因内容0；info 正（铺展开
  k_expected）rc=0；info 负（全 0）FAIL 逐槽指认。A0 两层判定链与 info
  契约**正负双向可分**。
- **未覆盖项（如实声明，见台账 §3.4）**：批量 f32 独立实现轴未测；info 正轴
  为构造值非逐槽真实现；抽 3 case 非全量（判定侧全量自检已跑过）。
- 脚本归档：`frozen/private/batched-verify-r2.py`（原 /tmp 临时件）。

## 四、台账与状态收口

- `frozen/private/VERIFICATION.md`：状态速读改「十包全闭环」；§1 批量四行
  端到端列 ✓；新增 §3.4 r2 记录与未覆盖项声明；§3.5 原方案轴存档。
- `frozen/private/SEEDS.md`：状态段（2026-10-08）改「十包已产制、验证全部
  闭环」，含批量端到端一行结论。

## 五、接手人下一步（按优先级）

1. **community_task 任务书修改提交**：分支 `solver_1010`（此前交付线在
   `solver_0923_delivery`），目标分支待任务侧指定；注意两册精度章已改未提。
2. **HT-15 补字拍板**（建议甲：potrf 节补「残差对构造侧 FLOAT64 输入 A64
   计算」一句，与已交付十包及全部验证证据零冲突）。
3. 批量 f32 独立实现轴补验（可选，方法照单矩阵 f32test，DUT 换 scipy
   逐矩阵计算铺槽）。
4. HT-23 处置后交接包仓 push；skill 仓 push（本地领先 f5322b4）。
5. HT-24 研发反馈跟进（文档已备）。

## 红线重申

悬置项不自行拍板；不 push/merge 除非明示；commit 无 AI 署名；
`review-bundle-issues-0924.md` 原文永不改（状态只文末追注）；
磁盘 284G 红线——批量验证须逐轮清盘（batched-verify-r2.py 已内置）。
