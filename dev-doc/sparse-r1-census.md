# M0.5 契约普查报告（ops-sparse @ 5b2a5ba）

数据源：本地只读克隆 `/Users/ll/Desktop/workspace-ascend/OpRunway/repos/ops-sparse`，
33 个 `test/<op>/` 目录逐一提取（fan-out 33 agent，33/33 收齐）。逐算子原始摘要在
[sparse-r1-census-data.json](sparse-r1-census-data.json)，本文只留结论与离群清单。

## 1. 形态分布

| 形态 | 数量 |
| --- | --- |
| csv_gtest（frame 惯例） | 26 |
| self_contained（硬编码 gtest） | 7 |

csv_gtest 26 个：coo2csr、coo_get、csr2coo、csr2csc_ex2、csr2gebsr、csrgeam2、
densetosparse、gather、gebsr2gebsc、gpsv_interleaved_batch、gtsv2、gtsv2_nopivot、
gtsv2_strided_batch、gtsv_interleaved_batch、ltmatmul、nnz、prune、scatter、sddmm、
sparse2dense、sparseLt、spgemm、spmm_op、spmv_op、spsm、spsv。
self_contained 7 个：coosort、cscsort、csrsort、cube_spmm、spmm、spmv、spvv。

## 2. 核心发现：列契约按算子分化，不是一仓一契约

候选方案假定「一个 sparse_frame profile」就能覆盖仓内惯例。普查证伪了这个粒度——
仓级一致的只有骨架，字段名与词表在算子间自由分化：

| 维度 | 分布（26 个 csv_gtest 内） |
| --- | --- |
| id 列名 | `case_name`（24）；`case_id`（ltmatmul、prune） |
| 种子列名 | `seed`（19）；`random_seed`（3）；`seed_a`+`seed_b`（csrgeam2）；无（Lt 家族 3 个） |
| 阈值列 | 三件套 mere/mare/abs（6）；两件套 mere/mare（6）；零阈值列（14） |
| expect_result | `SUCCESS` 多数；全名 5；小写 1（gtsv2）；多词表 3；**无此列** 8 |
| description 列 | 只有 gtsv2、sddmm、spmm_op、spsv 有 |
| handle 类型 | `aclsparseHandle_t` 主流；descriptor 直传（coo_get）；Lt 家族两种 Lt handle |

仓级真正一致的骨架（可进 registry 当仓级默认）：

- 读列机制全仓统一：**没有一个算子用字面 `ReadMap()`**，全部走 csv_loader.h 的
  `fillCustom(csv_map)` + `parseString/parseInt/parseDouble(row, "列名")`。列名仍以字符串
  字面量出现在源码里，accept 的 COLUMN_NOT_READ 字面量搜索依旧有效。
- **无 warm-up**：26 个算子每用例只调 API 一次，`--calls-per-case=1` 全仓成立（V2 定论）。
- 命名全用 `L0_/L1_/L2_/WB_` 等分层前缀，无一处 `TC_`；对 accept 的语义无碍
  （非 `TC_PF_` 行全进精度期望集，且仓内 CSV 本来就零性能行）。
- 异常/负例用例**普遍不进 CSV**：硬编码在 `TEST_F(<Op>ExceptionTest)` 里（coo2csr、
  csr2coo、csrgeam2、gpsv、gtsv2_strided_batch 等均如此）。CSV 只装正例是仓内常态。
- 布局统一 `test/<op>/{param.h,golden.h} + arch35/{test.cpp,wrapper,csv}`（param/golden
  跨 arch 共享，CSV 随 arch）；main 由 `test/frame/test_main.cpp` 共享。

## 3. 离群清单（registry 冻结时必须显式处理或排除）

| 算子 | 离群点 |
| --- | --- |
| densetosparse | 双 CSV：主表正例 + 独立 `l2_cases.csv` 负例（六列 mutation 契约）；无 expect_result |
| coo_get | 无 handle（descriptor accessor）；CSV 表头后有 `#` 注释行；value_lo/hi 列解析但未使用 |
| csr2coo | CSV 含 13 行 `#` 注释行；阈值列全 unused（整数位级比对） |
| gtsv2 | expect 词表小写且判定不读该列（由 matrixType 与 n==0 在 cpp 内分支）；阈值内置按 dtype |
| ltmatmul、prune、sparseLt | Lt 家族：`case_id` 列、无种子列、gtest 名由 caseId() 生成非 CSV 名直用 |
| csrgeam2 | 双矩阵成对列（sparsity_a/b、seed_a/b、empty_row_prob_a/b、index_base_a/b/c） |
| 整数变换类（coo2csr 等） | 阈值列存在但注释明言不用，golden 为 bit-exact 比对 |

## 4. 试点适配度（M5 批量实证候选）

| 候选 | 适配度 | 一句话 |
| --- | --- | --- |
| gather | **高** | 6 个语义列、典型正交轴（dtype×idx_type×idx_base）、seed 驱动，可直接轴展开造行 |
| gtsv2 | 中上 | 形状参简单同构，但阈值内置、verdict 不读 expect 列，适合当「无阈值列」类试点 |
| prune | 中 | 13 列全标量/枚举、dtype 四档，但无 expect/阈值/种子列，契约极简不同构 |
| densetosparse | 低 | 四格式三段调用 + 双 CSV 负例结构，不做首批 |

建议 M5 试点序：coo2csr（首通，plan 已定）→ gather → gtsv2；prune 视 registry
对「无 expect 列」的处理再定；densetosparse 排除出本期。

## 5. 对 registry 冻结（§2.3）的直接输入

1. **profile 粒度修正**：单一 `sparse_frame` profile 不够——种子列名、阈值列集、expect
   词表、id 列名都按算子漂移。冻结方向：registry 只装**仓级默认**（id=case_name、
   seed=seed、expect success token、阈值三件套可选），逐算子偏差由 FACTS 的
   `case_controls`/profile 覆盖字段显式声明，词表保持闭合（禁自由文本）。
2. **A1 domain 入口头**：`include/cann_ops_sparse.h`（另有 `cann_ops_sparseLt.h`，Lt
   家族本期不做，排除项写死）。
3. **framework_owned_columns 实证**：阈值列由 frame/golden 读取、param.h 也解析——
   「无需 param.h 命中」的假设对本仓不成立，该字段定义要按普查改为「所有者=frame，
   但字面量仍在 harness 源码出现」。
4. `success_token` 仓级默认 `SUCCESS`，但按算子可覆盖（大写全名/小写/无此列三种变体
   都真实存在）；「无 expect 列」是合法形态，registry 要有显式的 none 取值。
5. CSV 里可出现 `#` 注释行——case-gen 生成侧不产注释行，但 accept 读包侧的行计数、
   期望集构建要容忍它（blas csv_loader 同源，已处理引号；注释行为 sparse 新发现）。

## 6. 与既有结论的对账

- V2（无 warm-up，calls-per-case=1）：26/26 证实，无一例外。
- V3（expect 词表按算子异）：证实且比预想更散——五种变体含「无此列」。
- 「26/33 frame 形态」：证实，清单与早前抽查一致。
- 「异常用例不进 CSV」：新固化的仓级惯例，写进 registry 的 edge 语义参考。
