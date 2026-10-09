# S3-batched 切片 spec v2.1：四个批量算子入线（纯脚本包）

Mr.0 2026-09-24 裁定：两任务书全部 10 算子都做——batched 四个以**后补包**交付，
已交付六包一个字节不动。v1 经 Codex 评审（thread `01a0d22c`）九条必须改全采纳，
处置对照见第 8 节。

## 0. 继承与术语

继承优先级：本文 > `solver-s2-spec.md`（2.3′ 任务书契约、复数增量表）>
`solver-s1-cholesky-spec.md`（1.1 布局、1.2 授权、schema/CLI）> 设计 v2。
「不落盘」裁定（supplement 23i）优先于 S1 的冻结落盘条款，本文第 5 节给出
batched 包的落地形态。s1 子集/cu/ratio_cpu/T7/T8 → 设计 v2 第 0/4 节与 S1 spec；
「评审包问题 6」= `reports/solver-review-bundle-cholesky/README_review.md` 第 6 问
（batched 判定标准计划）。「既有六算子」= spotrf/spotrs/spotri/cpotrf/cpotrs/cpotri。

## 1. canonical 增量

- 字段加 `batch`；去重键与 bench 匹配键都含 batch；`nrhs` **显式记 1**
  （potrsBatched 官方仅支持 nrhs=1；cases.json 无该字段，冻结时写入）。
- 冻结断言：lda==n、（potrsBatched）ldb==n；192 原始 → 去重后条数入对账
  （实查为 188，四算子逐一核对）。
- s1 子集（确定性算法，冻结时**直接列出选中六例**入对账表）：dedup 后按
  (n, batch, lda, ldb) 全序排序；逐 uplo 取 ① n 最小者中 batch 最大的首条、
  ② n 最大者中 batch 最小的首条、③ 排除①②后排序中位（下中位）一条。
  称「二维代表子集」，不称覆盖。
- **无 std 蓝本**照实声明；任务书要求的随机 SPD/HPD、非正定、INF/NAN、确定性
  等覆盖类别在期望集保留证据不足，不因六例而缩减。

## 2. gen 增量

- 构造：逐矩阵调用既有 fill，**同一 rand 流跨批续抽**；potrsBatched 的 A/B
  次序以其 cu 实测为准（评审核实：先全部 A 再全部 B）逐行注明出处。
- npz：批维堆叠 `A64/A32=(batch,n,n)`、`B64/B32=(batch,n,1)`、golden 同形；
  字段名与 dtype 映射不变；降型校验同前。
- **批内分块公共边界**：gen/golden/ratio/判定共用一个工作集上限常量
  `CHUNK_BYTES`（初值 512MB，装包机实测后可调），逐矩阵处理天然保持抽取
  次序，分块前后数组逐位一致须有测试；性能 case 的原规格 batch 不受分块
  影响（分块只在 CPU 侧生成与判定，不改任务书性能调用形状）。

## 3. ratio_cpu 增量（逐矩阵配对，不做 max 聚合）

- 逐矩阵计算 c_i；**判定用逐矩阵配对**：r_i ≤ f(c_i) 对每个 i（f 为既有
  单矩阵阈值公式），不做 `max(r) ≤ f(max(c))`（评审给出反例：差矩阵放宽
  他家阈值）。
- 存储（v2.1，Mr.0 裁定「全都现场造」）：包内**不携带** ratio_cpu 数组；
  验收与自测侧在判定时现场逐矩阵同法重算（同环境确定性）。装包自检运行
  产出的 min/median/max 摘要写入 index 仅作参考。

## 4. 判定卡增量（T8 暂定方案；语义映射见下）

- **逐矩阵完整三层**：layer1 按任务书目标逐矩阵（potrfBatched 还原取指定
  三角对 A_i；potrsBatched X_i 对 golden_i）过混合容差双门（复数拆实虚）；
  不满足逐矩阵走 fallback（DPOT01/DPOT02，配对 c_i）。**case 数值结论 =
  全部矩阵通过**；整批统计只入 diagnostics。
- 报告增：`fail_count`、`first_fail_index`、`worst_index`（不产百万行明细）。
- **info 按接口角色分型**（任务书接口说明第 69 行）：potrfBatched 被测输出
  `info` 为 int32 shape=(batch,)（infoArray）；**potrsBatched 仍为标量**
  （仅报参数错；正定性由前置分解的 infoArray 反映，准备失败归 prep 不归
  目标接口——硬边界 5）。batch=1 不得被标量化（防误掩测试）。
- **T8 语义映射**：数值结果可展示；**正式精度项仍按 T8 记证据不足**直至
  标准侧确认（评审包问题 6 的答复即收束点）；verdict 加 flag `T8` 标注
  暂定聚合方案。
- 负例承诺的准确表述：「单矩阵扰动**超出完整判据允许范围**时整 case 数值
  FAIL 且报告指认矩阵序号」（不否定 fallback 的正当豁免）。

## 5. 包交付形态（v2.1：纯脚本包，Mr.0 2026-09-24 裁定「全都现场造，
无论大小」）

- batched 四包**不携带任何数据数组**：无 cases/*.npz、无 golden、无 ratio_cpu
  数组。index 全部条目记 `materialize:"gen"`；包内容 = gen_data.py + canonical
  切片 + verify 双件 + sim_dut + README + perf_baseline + manifest（KB 级）。
- 开发者流程改为「先造数后测」：README 写明第 0 步
  `python3 gen_data.py --canonical canonical_cases.json --out data --select all`
  （执行器从 data/cases/*.npz 读输入）；verify 判定时**自行现场重生成**同一
  输入并算 golden 与逐矩阵 ratio（同环境逐位一致，不依赖开发者的 data 目录）。
- 可行性依据：batched 六例全为 cu 配方构造（纯整数流 + 简单算术），转写抽查
  证明跨环境逐位稳定；无 std 类 BLAS 末位差风险。
- 已交付六包维持原形不动；此后新包一律纯脚本形态（本条为通用数据策）。
- 装包与演练覆盖**四个新包全部**（实/复 × 分解/求解四象限），不以单包代表。

## 6. 改动面（评审补齐后共十三处）

freeze_canonical、gen_data、fill_ratio_cpu、cards、verdict（批维+info 分型）、
render_verify（副本批维+按需生成）、sim_dut（批维+info 分型）、build_package
（纯脚本装包：零数组、全 materialize）、stream_check（批维）、**accept_run（info 数组读取，
现 `.item()` 会炸）**、**expectations（batched 声明与状态、覆盖缺口）**、
**make_baseline（匹配键加 batch，现未核对）**、**两份 SKILL.md 与包 README
模板（支持范围与 batched 说明）**。

## 7. 验证门

- **既有六算子零漂移**：criteria 全量 pytest 与六包一致性抽验（不只实数四件）；
  新 accept 消费旧六包行为不变（独立回归）。
- batched 新测试：逐矩阵配对反例（评审的 c=[0.01,3] 型用例）、单矩阵超限
  扰动指认序号、potrsBatched 标量 info 与 prep 失败隔离、batch=1 形状、
  分块一致性、verify 现场重生成与 gen_data 落盘产物逐位一致（同环境）。
- 装四包（纯脚本形态）+ 每包按「先造数后测」全流程正/负例演练；代表档
  （GB 级 case 与百万小矩阵档）实测耗时与峰值 RSS 入记录。
- 交付：`reports/solver-delivery-0923-batched/` + 交付说明补页（与首批六包
  关系、materialize 用法、T8 状态）。

## 8. v1 评审处置对照

九条必须改 → info 按角色分型（§4）；逐矩阵配对与负例表述（§3/§4）；T8 映射
（§4）；子集算法确定化与 192/188 对账（§1）；容量预算、CHUNK_BYTES 与交付
形态（§2/§5）；改动面补四处（§6）；零漂移与四包全验（§7）；覆盖类别与 P 项
保留（§1/§7）；继承优先级与术语指向（§0）。两条建议改采纳：nrhs 显式记 1
（§1）、失败三元组报告（§4）。
