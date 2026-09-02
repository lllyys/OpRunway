# M0.5 registry 接口冻结（v2，经 checkpoint 上半场审修订）

本文冻结 harness_profile 与 registry 的**字段面**——M1（ProjectionIR）、M2（accept 参数化）、
M3（FACTS v2）共同依赖的接口。值在 M3 实例化时按普查数据填，字段面自本文冻结后只经
Codex checkpoint 变更。依据：[census](sparse-r1-census.md) §2/§5、
[projection-matrix](sparse-r1-projection-matrix.md)、checkpoint 上半场审
（thread `01a05fb3-d0df`，裁 FREEZE_WITH_FIXES，本版即按其清单修订）。
术语（A1/S1/COLUMN_NOT_READ 等）见
[implementation-plan](sparse-r1-implementation-plan.md) 的「阅读约定」表。

## 1. 总体结构

- registry 是 skill 内**闭合、版本化**的数据表（候选方案硬边界 4：域级惯例只进数据，
  禁代码分枝），随 M1 落进模板公共代码区，任务包因嵌同一公共区而自含。
- 本期恰好两个 profile：`blas`、`sparse_frame`。Lt 家族（ltmatmul、prune、sparseLt）与
  self_contained 7 算子不在本期范围，registry 不留它们的半成品字段。
- **粒度裁定（普查驱动）**：profile 只装**仓级默认与闭合上界**；逐算子偏差由 FACTS
  按 §2.5 的覆盖契约显式声明。禁止为单个算子在 registry 里开洞。

## 2. harness_profile 字段面（9 项，冻结）

| # | 字段 | 类型 | 语义 |
| --- | --- | --- | --- |
| 1 | `seed_columns` | list[str] | 种子列名；blas=`["random_seed"]`，sparse 默认 `["seed"]`；空表合法（无种子列） |
| 2 | `description_column` | str 或 none | blas=`description`；sparse 默认 none |
| 3 | `expect_column` | str 或 none | 主 CSV 的期望列名；none = 无此列（合法形态） |
| 4 | `expect_default_token` | str 或 none | 生成侧 expect 列默认写值（原 #6/#10 合并）；无 expect 列时必须 none |
| 5 | `status_vocab_bound` | list[str] | 域级闭合**上界**；FACTS 声明本算子精确子集，edge token 由子集派生 |
| 6 | `threshold_columns` | list[str] | 三档合法取值：三件套 / mere+mare / 空表 |
| 7 | `first_param_ctype` | str 或 none | 可选断言：非 none 时 S1 断言首参 ctype 等于它；blas 保现值，sparse=none |
| 8 | `entry_headers` | list[str] | A1 入口头；blas=`cann_ops_blas.h`，sparse=`cann_ops_sparse.h` |
| 9 | `footprint_policy` | str | `dense_formula`（blas）或 `no_static_check`（sparse：只跳过静态判定，不承诺运行时护栏） |

对上半场审的字段裁定采纳情况：`domain` 删（与 profile 键名重复，manifest 记选中的键）；
`id_column` 删（升为 §2.6 全局不变量）；`success_token`+`default_expect_token` 合并为 #4；
`edge_expect_tokens` 删（由 FACTS 子集派生）；`excluded_headers` 删（精确白名单下无消费
语义，Lt 排除写在 §1 范围里）；`handle_ctype` 改为可选断言 #7；`runtime_only` 改名
`no_static_check`（原名暗示未经证明的运行时兜底）。

A1 判定规则（写死）：在 `<repo>/include/` 下按全部 profile 的 `entry_headers` 探测，
恰一命中 → 选定 profile 并把**键名**记入 runtime manifest；0 命中 → 硬失败并列出
探测过的候选；多命中 → 硬失败列出全部命中。探测结果只记录不裁决（§4）。

## 2.5 FACTS 覆盖契约（冻结）

FACTS v2 以 `harness_profile: "<profile键名>"` 选 profile——**v2 必填**（v1 隐式
blas、行为不变；不留第二套隐含接口）。覆盖规则：

- **允许覆盖的键恰四个**：`seed_columns`、`description_column`、`expect_column`、
  `threshold_columns`。写在 FACTS 顶层 `harness_overrides` 字典里。
- **合并规则 = 整键替换**：覆盖键的值整体取代 profile 默认，不做深合并。
- **上界约束**：`threshold_columns` 必须落在 #6 的三档之一；FACTS 的
  `status_vocab`（算子精确词表，v2 新增必填项，见 M3）必须 ⊆ profile 的
  `status_vocab_bound`；`expect_default_token` 不可覆盖（改它 = 改 profile，走 registry）。
- **冲突检查**：覆盖出的列名与 `params` 投影列、`case_controls` 列两两不冲突，S1 拒。
- 未覆盖的键用 profile 默认；空值语义同字段定义（如 `seed_columns: []` = 无种子列）。

## 2.6 全局不变量（不属任何 profile）

- **id 列名 = `case_name`**：本期两域一致（`case_id` 仅 Lt 家族，已排除；Lt 进场时再议）。
- **CSV `#` 注释行是全局词法规则**：行首（含前导空白后）为 `#` 的行不是数据行。
  生成侧不产注释行；accept/量具读包侧统一按此规则跳过（M2·5·6′ 落地，验证并入
  一次性 blas 兼容回放，不单设探针）。
- **辅助 CSV 所有权**：任务包只拥有主 `<op>_test.csv`；仓内个别算子的辅助负例 CSV
  （普查见 densetosparse 与 sparse2dense 的 L2 套件）属 harness 自有测试资产——
  case-gen 不生成、accept 不部署、不进本期期望集。若未来要求验收辅助套件，
  须新增通用 FACTS `csv_artifacts` 机制，不得为个别算子开洞。

## 3. 基座列的两种所有权（拆开，逐列标主）

按**生成责任**与 **COLUMN_NOT_READ 排除**两个正交属性标注（普查证实「frame 读取、
无需 param.h 命中」的旧定义不成立——阈值列 param.h 也解析）：

| 列 | 生成者 | COLUMN_NOT_READ 排除？ |
| --- | --- | --- |
| id 列（case_name） | 生成器固定写 | 排除（frame 用作 gtest 名） |
| expect 列（若有） | 生成器按 #4 默认 token 写 | 不排除 |
| 阈值列（若有） | 生成器按 FACTS 写 | 不排除 |
| 种子列 | 生成器顺序写 | 不排除 |
| description 列（若有） | 生成器写 | 排除（人读，harness 可不读） |
| 语义列（params 投影 + case_controls） | 生成器按投影写 | 不排除 |

本表与 §2 字段的关系：§2 定「列叫什么、默认写什么」，本表定「谁生成、缺读算不算事」。
M1 的 ColumnSpec 是两者的统一物化载体，落地后本表塌进 ColumnSpec 的描述符元数据。

## 4. 同场裁定：accept 保持 profile-agnostic

**accept 不消费 profile 字段做裁决**。裁决输入全部来自包与 gtest 结果——expect 词表
accept 不解析、阈值在 CSV 里、family 由路径推断。A1 只把选定的 profile 键名写进
runtime manifest。M2 与 M3 在本文冻结后即可并行。

上半场审对此的三个先决修正（**归 M2 实施**，已入 plan M2 任务清单）：
精度期望集规则改为「主 CSV 除 `TC_PF_` 外全部有效数据行」（现状按 `TC_` 前缀收，
会把 coo2csr 的 L0_/L1_ 行全漏掉）；`#` 注释行读取口径统一（`_csv_header` 与其它
读取器的 strip 差异）；`calls_per_case` 去静默默认（显式必填或从 manifest 渲染，
A5 核对证据值与 manifest 相等）。

## 5. 冻结状态

- 冻结面：§2 九字段 + §2.5 覆盖契约 + §2.6 全局不变量 + §3 所有权表 + §4 裁定。
  变更须过 Codex checkpoint。
- 字段值分两步实例化（七维审对齐）：blas profile 的值在 M1′ 随硬编码参数化落地
  （与现状逐字节一致）；sparse_frame 的值在 M3′ 按 census-data.json 实例化。
- 上半场审遗留到实施的项：三个 accept 修正（→M2）、`no_static_check` 下 sparse FACTS
  的 constraints 约束纪律（→M3 的 authoring 指南）。
