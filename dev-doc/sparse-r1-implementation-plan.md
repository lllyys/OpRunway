# sparse R1 实施计划

> **状态：已立项实施中**（2026-09-01 用户裁定开工，分支 `feature/sparse-r1`；
> M0 为立项前预研）。编号与状态唯一源：
> [sparse-gap-learning-map.md](sparse-gap-learning-map.md) §0。
> 目标：case-gen 能为 frame 惯例的 sparse 算子造六件，开发者 harness 原样消费，
> accept 出裁决（R1）。承接 [sparse-support-candidate-plan.md](sparse-support-candidate-plan.md)
> 的 A 案与七条硬边界。2026-09-01 起草；同日经 Codex 计划评审（MAJOR GAPS，
> thread `01a05bf6-e8f7-7692-aaab-001b3366f73e`）修订为本版——主要修正：普查前移
> 冻结 registry、IR 补全七消费者、版本策略改为 v2 + 确定性迁移、blas 专属校验
> profile 化、NO_REF 终态显式化、tier 规范形收紧。

**阅读约定**（本文与关联文档的自造术语，首次冷读先过这张表）：

| 术语 | 含义 |
| --- | --- |
| 六件 | 任务包的六个文件：gen_csv.py、`<op>_test.csv`、两个 verify 脚本、README、gpu_baseline.csv |
| S1–S3 / A1–A5 | 阶段号：S=case-gen 生成链（校验→渲染→check）；A=accept 验收链（A1 环境、A2 部署与运行时包、A3 精度、A4 性能、A5 结论） |
| NO_REF | 性能汇总态：有性能用例但全部配不到基线，证据不足，不判 PASS/FAIL |
| FACTS | 单算子事实字面量（AST 白名单、从不执行），见 facts-schema.md |
| registry | skill 内版本化的有限 profile/词表数据集，装域级惯例 |
| 投影 IR | FACTS+registry 编译出的中间表示（列/轴/键/物化四份 spec），列消费者的唯一输入 |
| frame 惯例 | ops 仓 `test/frame/` 公共件之上的 CSV 驱动 gtest 写法（param.h 逐列读 CSV） |
| npu-smi 命令桩 | 用可替换的假 `npu-smi` 输出喂判定逻辑的本地测试手段（无真机时用） |
| ultracode | 多 agent 并行执行方式；可降级为串行，不构成对任何工具的硬依赖 |

## 0. 范围、终态与不变量

**范围**：ops-sparse 仓 26/33 个 frame 惯例算子（CSV-gtest；成员清单与统计口径的
唯一出处是 [sparse-r1-census.md](sparse-r1-census.md) §1）。E1（arch35 真机）不阻塞
M0.5–M6 的本地实施，只阻塞 M7 真机验证与「正式支持」声明。spgemm 950 任务书的
ATK/torch 形态按用户裁定为特例，不在本计划内。首个打通算子 **coo2csr**。

**R1 的预期终态（显式写死，避免误报）**：本期 sparse 无 GPU 基线，200 个性能点
全待填，故 accept A5 的预期结论是**「精度通过、性能 NO_REF、总体证据不足」**，
退出码非 0 属预期。追求「正式通过」的前置是 G4（GPU 基线回填），归 M7 之后另立。
任何阶段不得把静态自洽描述成正式支持。

**执行方式**：实施在主 session 进行，本文即交接件；并行阶段用 multi-agent
ultracode（Workflow fan-out），形态见 §1.5。测试用 ops-sparse 已有算子实证。

**不变量（每个里程碑都要守）**：

- **M1 逐字节门**：cherk/sasum 五件派生物（CSV、README、gpu_baseline、两个 verify）
  与 [sparse-r1-baseline-digests.txt](sparse-r1-baseline-digests.txt) 逐项相等；
  gen_csv.py 因公共区更新而变，走 §3.4 确定性迁移。**M2 起**逐字节门只保 CSV/README/
  gpu_baseline 三件；verify 模板属预期变更，改为「变更文件 allowlist + blas 语义
  快照不变」（同一本地 ops-blas 树上，改前后 env/check/runtime manifest 规范化对照）。
- **NPU 现场空闲门**（用户裁定）：skill 做实际 NPU 测试起跑前，必须**现场**检查目标卡
  空闲。忙卡对 A4 是直接污染（kernel 耗时混入他人负载），对 A3 是抢占风险。落点是
  **两个 verify 模板**（A3/A4 是独立运行的运行时脚本，只改 accept.py 保证不了起跑时刻）：
  verify_accuracy 在首次 gtest 前、verify_performance 在首次 warm-up/msprof 前各查一次，
  **不复用** A1 环境快照或对方的结果。判定表写死，fail-closed：

  | 现场查询结果 | 判定 | 动作 |
  | --- | --- | --- |
  | npu-smi 查询失败或输出无法解析 | 阻塞 | 退出码 4，报「空闲门查询失败」 |
  | 目标 device 有占用进程，或利用率非 0 | 忙 | 退出码 4，列出占用进程 |
  | 明确空闲（无进程且利用率 0） | 放行 | 起跑 |

  核查记录写入结果 payload 的 `npu_gate` 字段（device、时间戳、判定、原始输出摘要）。
  M2 实现，本地用 npu-smi 命令桩验证三分支（含代表性原始输出样本），真机行为归 M7。
- 裁决路径唯一；引擎零领域词（领域内容只进 registry 数据）。
- `params` 逐字对应 C 原型；造数控制走 `case_controls`，不伪装成参数。
- 消费者棘轮：任何列/轴/键的消费者不得绕开 IR 重新遍历 params 猜投影。

**版本策略（修订）**：新增 FACTS 键对旧校验器不是加法兼容（旧 checker 严格拒绝
未知顶层键），所以：`schema_version=2` 承载新键（v1 FACTS 不含新键、行为不变；
新 checker 同时读 v1/v2，旧 checker 拒 v2 属预期）；模板 `GENERATOR_VERSION=2`，
现有示例经 §3.4 迁移到新公共区并同步改版本号。兼容方向单向：新读旧，旧不读新。

## 1. 里程碑总览

| 里程碑 | 内容 | 验收线 | Codex checkpoint |
| --- | --- | --- | --- |
| M0（已完成） | 钉五件摘要；V1/V2/V3/V5 核对 | 摘要文件在案 | 不需要 |
| M0.5 | 26 算子契约普查 + 现有 role 投影矩阵钉板 + registry 接口冻结 | 普查报告 + 投影矩阵 fixture | **是**（冻结接口） |
| M1 | ProjectionIR：`compile_facts` 唯一源，七消费者收编 | 逐字节门 + 投影矩阵不变 | **是** |
| M2 | accept A1/A2 参数化（依赖 M0.5 接口） | blas 语义快照 + 探针 | **是** |
| M3 | FACTS v2：case_controls + harness_profile 实例化 | 负例齐全；v1 FACTS 不变 | **是** |
| M4 | tier 规范形与文本同一性 | 三侧探针 | 并入 M3 |
| M5 | 本地静态链闭合：coo2csr 首通 + 批量实证 | 见 §6 断言清单 | **是** |
| M6 | 文档与收尾 | 冷读过 + 行长/棘轮 | 随 M5 |
| M7（阻塞 E1） | 950 真机 A2–A5 + V4 + G4 基线回填后复验 | 真机证据 | 另排 |

依赖序：M0.5 → M1 → M3 → M4 → M5；**M2 在 M0.5 冻结 registry 接口后**才与 M3
并行（accept 是否消费 profile 在 M0.5 一并定：倾向 accept 保持 profile-agnostic，
A1 探测到的 domain 只记入 runtime manifest，不参与裁决）。

## 1.5 ultracode fan-out 形态

主干改动（M1 IR、M3 契约）串行精工；并行用在普查、批量实证、验证、文档四类。
**写入纪律（修订）**：fan-out agent 一律只读仓与克隆，产出 schema 化结构（FACTS
字面量、证据 JSON）返回主 loop；仓内与 dev-doc 落盘唯一由主 loop 执行；agent 可用
scratchpad 临时目录做渲染试验，临时产物不作为最终证据。

| 阶段 | fan-out | 每 agent 任务 | 汇合物 |
| --- | --- | --- | --- |
| M0.5 普查 | 26 算子并行 | 读 `test/<op>/` 三件套，产出契约摘要（见 §2.1 字段清单） | registry 冻结依据；离群清单 |
| M5 批量实证 | 另选 3–5 算子 | 按普查摘要写 FACTS（返回字面量）→ 主 loop 渲染与 check | 「可造包」抽样证据 |
| M5 验证 | 每包 2–3 个对抗 agent | 从「列契约命中」「TC_ 块结构」「tier 三侧同一性」证伪 | 幸存才算过 |
| M6 文档 | 4 文档并行 + 1 零上下文冷读 | 各写一份；冷读只挑「用了没定义/先用后释」 | 冷读过才收 |

## 2. M0.5 · 普查、投影矩阵、registry 接口冻结

1. **26 算子契约普查**（fan-out，见 §1.5）：以本地只读克隆固定 SHA 为准。每算子
   读 CSV 表头 + param.h + wrapper，摘要字段：列名序、种子列名、阈值列、
   expect_result 词表、success token、handle ctype、有无 description/warm-up、
   候选算子（gtsv2、gather、densetosparse、prune）的批量实证适配度。
2. **现有 role 投影矩阵钉板**：构造覆盖每个现有 role×条件（复数标量、dtype_from、
   conditioning、nullable、batch、fixed_vector、producer…）的合成 FACTS 集，机械
   记录其 header/body/axes/edge 合法键/perf.key 的现状输出为 fixture——这是 M1
   「无行为变化」的完备证明面，cherk/sasum 两例覆盖不了所有 role。
3. **registry 接口冻结**：按普查定稿 harness_profile 的字段面——
   `generated_base_columns`（生成器固定列及其物化 writer）、
   `framework_owned_columns`（frame 读取、无需 param.h 命中，如阈值列）、
   种子列名、是否投影 description、`handle_ctype`、`success_token`、
   `status_vocab`（闭合词表，禁自由文本）、edge 允许 token、A1 的 domain 入口头
   清单（显式列出，如 `cann_ops_blas.h`/`cann_ops_sparse.h`，排除 `*_common.h`；
   0/1/多命中的判定都写死）。基座列的两种含义就此拆开，逐列标所有者。

## 3. M1 · ProjectionIR（唯一源进模板公共区）

1. **IR 定义**：模板公共代码区新增纯函数 `compile_facts(facts) -> ProjectionIR`，
   含四份 spec：`ColumnSpec`（有序列描述符）、`AxisSpec`（离散轴：值集合、来源）、
   `KeySpec`（perf/判重键及其类型——int 或 text）、`MaterializationSpec`
   （case_name、可选 description、顺序种子写入哪列、expect 默认 token 的 writer）。
   投影原语收敛为硬边界 2 的白名单：`omit`、`direct`、`complex_pair`、
   `fixed_expand`、`fixed_control`；轴类别住 AxisSpec，角色/来源/README 文案是
   描述符元数据，不扩充原语。registry 数据同住公共区——任务包因嵌同一公共区
   而自含；package.py 先加载可信模板取 IR，再做 validate/render/check。
2. **七个消费者逐项收编**（完成门，缺一不算完成）：模板 `_header_columns`、
   `_body_mapping`/`_assemble_rows` 物化、`build_axes`、package.py edge 合法键
   校验（:927）、perf.key 校验（:994）、`_column_contract_rows` README 契约表、
   `--print-header`。package.py 删除自身 `_header_columns`（:1221）与漂移自检
   （:1773）。棘轮：消费者不得再自行遍历 params。
3. **blas 专属校验同步 profile 化**（否则首个 sparse FACTS 过不了 S1）：
   首参 ctype 断言（package.py:1169 的 `aclblasHandle_t`）、默认成功 token
   （模板 :560 的 `ACLBLAS_STATUS_SUCCESS`）、状态/edge 词表（:75）全部改查
   registry；M1 内 blas profile 的值与现状逐字节一致。
4. **示例确定性迁移**：现有 cherk/sasum 的 gen_csv.py 只保留 FACTS 区，公共区
   从新模板整体替换，校验 FACTS AST 不变（版本号字段按 §0 版本策略同步），
   再重渲染对照逐字节门。旧包（新 checker 拒）按版本策略属预期，README 不改口径。
5. 验收：逐字节门 10 项派生物（两个 gen_csv.py 是迁移前参考，见 digests 文件头）
   + M0.5 投影矩阵 fixture 全项不变 + 负例回归
   （撞键/行数/未知键 v1 语义不变）。

## 4. M2 · accept A1/A2（依赖 M0.5 接口）

1. A1：入口头探测按 M0.5 registry 的 domain 清单；`CANN set_env.sh` 仍是硬门
   ——本地无 CANN 机器上 **A1 退出 3 属预期**，验收只断言 header/domain 检查项
   的输出正确，不把 CANN 缺失说成软失败。
2. A2/量具路径：**两个** verify 模板（accuracy resolver :75 与 performance
   resolver :98）同步把二进制寻址改为 `build/test/**/<op>_test` glob 唯一命中，
   0 或 >1 命中报错并列候选；README 模板与 CLI help 里硬编码的 ops-blas 措辞
   一并按 profile 渲染。探测出的 domain/profile 写入 runtime manifest（记录，
   不参与裁决）。accept 的 `BASE_COLUMNS` 列所有权改按 M0.5 的两个集合。
3. NPU 现场空闲门（判定表见 §0）：落在**两个 verify 模板**——verify_accuracy 首次
   gtest 前、verify_performance 首次 warm-up/msprof 前各自现场核查，不复用早先结果；
   三分支（空闲/忙/查询失败）按 §0 表执行，退出码 4，`npu_gate` 记录进结果 payload。
   本地验收：npu-smi 命令桩喂三分支各一份代表性原始输出，断言退出码与 payload 字段。
4. A5 空基线终态（评审抓到的现状缺口）：现行 verdict 在可比性能集为空时直接记
   「性能通过（无性能用例）」，把「无 `TC_PF_` 行」与「有 `TC_PF_` 但基线全空」混为
   一谈。改为：无 `TC_PF_` 行 → 通过（无性能要求）；有 `TC_PF_` 但可比集为空 →
   性能 `NO_REF`、总体「证据不足」、退出码 2。回归：两种包各一探针。
5. 验收（可操作版）：同一本地 ops-blas 树改前/改后各跑一次，规范化对比
   `env.json`/`check.json`/runtime manifest；量具寻址探针覆盖 0/1/>1 三种命中；
   对本地 ops-sparse 克隆跑 A2 布局推断探针。真机 A3/A4 归 M7。

## 5. M3 · FACTS v2：case_controls 与 harness_profile 实例化

M0.5 已冻结接口，本里程碑做实例化与校验：

```python
"schema_version": 2,
"harness_profile": "sparse_frame",   # 缺省 "blas"；键值来自 registry
"case_controls": [
    {"name": "sparsity", "kind": "tier", "values": ["0.0", "0.5", "0.9", "0.99"]},
    {"name": "pattern", "kind": "enum", "values": ["random", "diag", "banded"]},
],
```

1. 校验：v2 才接受新键；name 合法且与 params/列名不冲突；kind ∈ {enum, tier}；
   values 非空、字符串、去重；tier 过 §5.5 规范形。sparse 正例负例齐全。
   `footprint_policy` 一并实例化（registry-freeze §2 第 14 项）：sparse_frame 取
   `runtime_only`，模板 `_row_is_valid` 按它跳过 `_footprint`；回归例一条——按稠密
   公式会误拒、按 `runtime_only` 必须生成。
2. 投影：control 经 ColumnSpec 产 `fixed_control` 列，进 AxisSpec 与 README。
3. perf.key 可引用 control name，KeySpec 标 text 型。
4. golden 新词表值 `{"kind": "harness"}`：validator `_golden_requirements`、
   README 投影（package.py:1510）、readme-contract 同步；禁止 symbol/formula
   字段共存；并加回归探针固化不变式「量具与 accept 均不读 golden」。
5. 红线：不新增角色；descriptor/结构对象本体（C1-full）不在本期。

## 5.5 M4 · tier 规范形与文本同一性

1. 唯一 canonicalizer：tier 文本必须匹配 `^-?\d+\.\d+$`（**必须含小数点**，整数档
   写 `"1.0"`——确保永不落入现行 strip+int 归一的整数分支）；禁止 `-0.x` 之外的
   负零形、前导零冗余（`"00.5"`）与尾零冗余（`"0.50"`）；给出规范形提示。
2. KeySpec 带类型：tier 键永远按字符串比较；归一函数三处不改语义。
3. 三侧探针（M5 断言）：同一 tier 在 package 判重、量具 `_normalize_key_value`、
   accept `_load_baseline` 的键表示逐字符相同。

## 6. M5 · 本地静态链闭合（coo2csr 首通 + 批量实证）

改名自「端到端」：无真机时 A2 是静态兼容探针，真端到端归 M7。

1. coo2csr FACTS（schema v2、sparse_frame profile、case_controls 按仓内列契约、
   perf 固定 200 全待填、golden harness）→ 渲染六件 → `check` 退出 0。
2. **断言清单（全部显式，warning 不许静默过）**：契约摘要 warnings 集为空；
   列读取报告全命中仓内 `test/coo2csr/` 源码（含 `seed`/`idx_base`/阈值列）；
   TC_ 块结构成立；tier 三侧同一性探针过；A2 推断链（op/family/runtime 渲染）成立。
3. 批量实证与对抗验证按 §1.5 fan-out（3–5 个已有算子重复 1–2 步）。
4. blas 回归全项重跑（§0 不变量）。

## 7. M6 · 文档与收尾

- `facts-schema.md`：schema v2、case_controls、harness_profile、tier 规范形、
  golden "harness"；`case-strategy.md`：sparse 轴设计；`csv-and-blocks.md`：
  基座列两集合与所有者表；accept 文档：calls-per-case 两族取值（blas 2 /
  sparse frame 1）、A1 domain 探测语义、glob 寻址规则、R1 预期终态（NO_REF）。
- `dev-doc/oprunway-changes-brief.md` 逐里程碑追加；todo 候选节改进行中。
- 全部过 prose-style；M6 冷读 agent 零上下文验收。

## 8. 风险对照（评审 Top 3 → 防线）

| 风险 | 防线 |
| --- | --- |
| 假统一 IR（表头统一、轴/edge/键/物化各猜） | M1 完成门列七消费者 + 「不得重遍历 params」棘轮（§3.2） |
| 首包穿不过现有契约（handle/状态词表/默认 expect 写死 blas） | M1.3 专项 profile 化 + M3 sparse 正负例（§3.3/§5.1） |
| 假回归安全 | 版本策略显式化（§0）+ M1 投影矩阵完备面（§2.2）+ M2 语义快照（§4.3）+ NO_REF 终态写死（§0） |

## 9. 主 session 交接清单

- 文档四件：本文、candidate-plan、learning-map、baseline-digests。
- 本地只读克隆（**绝对路径，不在 worktree 内**）：
  `/Users/ll/Desktop/workspace-ascend/OpRunway/repos/ops-sparse`（HEAD `5b2a5ba`）；
  开工门先确认可读；若需重 clone 按仓规另取用户授权。
- 关键坐标：模板/package 两份 `_header_columns`（gen_csv.py:470 / package.py:1221）、
  `_load_generator`（package.py:1299）、漂移自检（:1773）、blas 专属校验三处
  （package.py:1169 handle、:75 状态词表、模板 :560 默认 expect）、量具双 resolver
  （verify_accuracy.py:75 / verify_performance.py:98）、归一三处（package
  `_normalized_perf_key`、量具 :299、accept :211）、edge/perf 校验（package.py:927/:994）。
- 已消核对项：V1（gtest 按 case_name 过滤 ✓）、V2（sparse 无 warm-up，
  calls-per-case=1）、V3（expect_result 词表按算子异→闭合 status_vocab 方案）、
  V5（build.sh 同款 --ops）。待真机：V4、E1。
- 纪律：skill-edit-gate（先读 skill-best-practices + 挂 /skill-creator）；
  动核心无条件 Codex checkpoint；commit 不带 AI 署名；不 push 除非明示。

## 10. 明确不做（本期）

- descriptor/结构对象本体（C1-full）——等首个 descriptor 风格算子。
- sparse footprint 估算——运行时护栏兜底（已裁定）。
- per-dtype 性能阈值——本期无基线；出现带基线任务书再议。
- ATK/torch 形态通路——特例，不建。
- 真机验收与 G4 基线回填——M7，阻塞 E1，单列立项。
