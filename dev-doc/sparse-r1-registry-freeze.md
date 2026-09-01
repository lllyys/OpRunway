# M0.5 registry 接口冻结（待 checkpoint 确认）

本文冻结 harness_profile 与 registry 的**字段面**——M1（ProjectionIR）、M2（accept 参数化）、
M3（FACTS v2）共同依赖的接口。值可以在 M3 实例化时按普查数据填，字段面自本文冻结后只经
Codex checkpoint 变更。依据：[census](sparse-r1-census.md) §2/§5、
[projection-matrix](sparse-r1-projection-matrix.md)。

## 1. 总体结构

- registry 是 skill 内**闭合、版本化**的数据表（候选方案硬边界 4），随 M1 落进模板公共
  代码区，任务包因嵌同一公共区而自含。
- 本期恰好两个 domain：`blas`、`sparse_frame`。Lt 家族（ltmatmul、prune、sparseLt）与
  self_contained 7 算子不在本期范围（census §1/§3），registry 里不留它们的半成品字段。
- **粒度裁定（普查驱动）**：profile 只装**仓级默认与闭合词表**；逐算子的语义列与偏差由
  FACTS 显式声明（case_controls / 覆盖字段）。禁止为单个算子在 registry 里开洞。

## 2. harness_profile 字段面（冻结项）

| # | 字段 | 类型 | 语义与依据 |
| --- | --- | --- | --- |
| 1 | `domain` | str | profile 名，同时是 A1 探测结果的记录值 |
| 2 | `id_column` | str | blas=`case_name`；sparse_frame=`case_name`（`case_id` 仅 Lt，已排除） |
| 3 | `seed_columns` | list[str] | blas=`["random_seed"]`，sparse 默认 `["seed"]`；成对种子由 FACTS 覆盖 |
| 4 | `description_column` | str 或 none | blas=`description`；sparse_frame 默认 none（26 个里仅 4 个有） |
| 5 | `expect_column` | str 或 none | **none 是合法形态**（8 个算子无此列，census §2） |
| 6 | `success_token` | str 或 none | 仓级默认（blas 全名，sparse=`SUCCESS`）；可覆盖；无 expect 列则 none |
| 7 | `status_vocab` | list[str] | 闭合词表，禁自由文本；覆盖普查全部变体（全名/小写/多词） |
| 8 | `threshold_columns` | list[str] | 三档合法取值：三件套 / mere+mare 两件套 / 空表 |
| 9 | `handle_ctype` | str | blas=`aclblasHandle_t`；sparse=`aclsparseHandle_t`；S1 首参断言数据源 |
| 10 | `default_expect_token` | str 或 none | 生成侧 expect 列的默认写值（替换模板 :560 写死） |
| 11 | `edge_expect_tokens` | list[str] | edge_cases.expect 的合法 token（替换 :75 词表） |
| 12 | `entry_headers` | list[str] | A1 入口头；blas=`cann_ops_blas.h`，sparse=`cann_ops_sparse.h` |
| 13 | `excluded_headers` | list[str] | 显式排除清单：`*_common.h`、`cann_ops_sparseLt.h` |
| 14 | `footprint_policy` | str | `dense_formula`（blas 现状）或 `runtime_only`（sparse_frame） |

第 14 项补自六项 dev-doc 的 Codex 审（High：「不做 sparse footprint」无机械落地路径）：
`runtime_only` 表示生成侧不做 footprint 判定——模板 `_row_is_valid`（gen_csv.py:466 一带）按
本字段机械跳过 `_footprint`，靠 harness 运行时护栏兜底。M3 实例化时配回归例：一条按稠密
公式会误拒、按 `runtime_only` 必须生成的行。

A1 判定规则（写死）：在 `<repo>/include/` 下按全部 profile 的 `entry_headers` 探测，
恰一命中 → 定 domain 并记入 runtime manifest；0 命中 → 硬失败并列出探测过的候选；
多命中 → 硬失败列出全部命中。探测结果**只记录不裁决**（见 §4）。

## 3. 基座列的两种所有权（拆开，逐列标主）

普查修正了 plan 里 `framework_owned_columns` 的原定义——「frame 读取、无需 param.h 命中」
在本仓不成立：阈值列 param.h 也解析（census §5.3）。改为按**生成责任**与
**COLUMN_NOT_READ 排除**两个正交属性标注：

| 列 | 生成者 | COLUMN_NOT_READ 排除？ |
| --- | --- | --- |
| id 列 | 生成器固定写 | 排除（frame 用作 gtest 名） |
| expect 列（若有） | 生成器按 profile 默认 token 写 | 不排除 |
| 阈值列（若有） | 生成器按 FACTS 写 | 不排除 |
| 种子列 | 生成器顺序写 | 不排除 |
| description 列（若有） | 生成器写 | 排除（人读，harness 可不读） |
| 语义列（FACTS 声明） | 生成器按投影写 | 不排除 |

## 4. 同场裁定：accept 保持 profile-agnostic

plan §1 留给 M0.5 的悬案就此定：**accept 不消费 profile 字段做裁决**。依据是普查证实
accept 的裁决输入全部来自包与 gtest 结果——expect 词表 accept 本来不解析、阈值在 CSV 里、
family 由路径推断。A1 只把探测到的 domain 写进 runtime manifest。因此 M2 与 M3 在本文
冻结后即可并行。

## 5. 生成侧适配的两个仓级事实（进 registry 注记，不是字段）

- sparse_frame 的 CSV 可含 `#` 注释行（census §5.5）：case-gen 不生成注释行；accept 读包
  侧行处理需容忍（M2 验收探针覆盖）。
- sparse_frame 无 warm-up：文档与 README 模板给 `--calls-per-case` 的默认建议按 domain
  给（blas=2、sparse_frame=1），不改 accept 的参数语义。

## 6. 冻结状态

- 字段面（§2 十三项 + §3 所有权表 + §4 裁定）：冻结，变更须过 Codex checkpoint。
- 字段值：M3 按 census-data.json 实例化，正负例齐全后生效。
- 待 checkpoint 一并裁的开放项：无（普查未留悬案；若 fixture 录制暴露新分叉，回填 §2）。
