# sparse 支持候选方案（A 案：原 skill 内部分层 + 投影 IR）

> **状态：候选，未立项。** 2026-09-01 记录。三案对比经 Codex 架构评审
> （review-plan，gpt-5.6-sol/xhigh，thread `01a05afc-5d9c-7bb1-9374-df472164f970`），
> 裁决 A 案；本文即该裁决与七条硬边界的完整落笔。
> 差距全景与心智模型见 [sparse-gap-learning-map.md](sparse-gap-learning-map.md)。

## 1. 问题与三案

让 repo-task-blas-case-gen / repo-task-blas-accept 支持 `cann/ops-sparse`（35 算子，
descriptor 风格 API，`test/<op>/arch35/` 布局，全局结构参数列契约）。三个候选结构：

| 案 | 结构 | Codex 加权分（复杂度 ×2，满分 35） |
| --- | --- | --- |
| **A** | 原 skill 内部分层：域差异塌进声明数据 + 统一投影 IR | **26** |
| B | 新建平行 sparse skill | 18（重复买隔离：引擎/六件机制一式两份，双修复与漂移是永久成本） |
| C | 拆共享核心库 + 按域 skill | 22（两个高度同构域撑不起发布与版本耦合） |

**裁决：A。** accept 原地参数化（环境门探测化 + 路径 profile，保持唯一
`_overall_verdict`）；case-gen 在原 skill 内把 FACTS 规范化为统一投影 IR，由唯一的
组合/渲染/逐字节校验引擎消费。不拆发布单元、不复制引擎、不增加裁决路径。
改名（`repo-task-blas-*` 名不副实）是独立上游议题，不与本方案捆绑。

评审同时确认：accept 的 CSV 路径枚举已兼容无 family 布局（accept.py:104），
A1/A2 比预估更小；case-gen 的差距横跨角色、轴、表头、行物化、性能键五处，
不是「多几列」。

## 2. 核心修正（评审推翻了原始倾向中的一个设计）

**角色投影不能围绕 `params` 设计。** `sparsity/empty_row_prob/pattern/seed/idx_base`
是 harness 的造数控制，不是 C 原型参数——把它们伪装进 `params` 会打破
「params 逐字对应 C 原型」的现有契约。FACTS 内部分四块，规范化为一份 IR：

```text
FACTS
  ├─ API params            —— 逐字对应 C 原型（现契约不动）
  ├─ 逻辑结构 / descriptor  —— 签名参数引用的复合对象
  ├─ case_controls         —— 造数控制（sparsity 等），独立成节
  └─ harness_profile       —— 仓布局 / 基座列 / 词表选择
          ↓ 规范化与校验
  ColumnSpec + AxisSpec + KeySpec + MaterializationSpec（统一投影 IR）
          ↓
  唯一的 block/pairwise/render/check 引擎
```

## 3. 七条硬边界（A 案成立的前提；violat 任何一条，复杂度评分作废）

1. **API 参数与造数控制分离**：`params` 只放 C 原型；不造伪参数、伪 family 迁就旧模型。
2. **角色只映射有限投影原语**：不投影 / 直接列 / 复数拆列 / 定长展开 / 固定控制列 /
   离散轴。禁止角色携带回调、模板片段、自由表达式；新原语必须走引擎能力评审。
3. **所有消费者读同一份 IR**：表头、行物化、pairwise 轴、edge 合法键、`perf.key`、
   README 列表全部派生自同一 ColumnSpec/AxisSpec。现状 package.py 与模板各维护一份
   `_header_columns`（package.py:1221 / gen_csv.py:470），是本轮必须收敛的重复点；
   只统一表头、留下五套半统一规则即失败。
4. **数据化限定为 skill 内闭合 registry**：角色 schema、列名、顺序、默认值、词表、
   浮点档位、仓布局 profile 都放 skill 内版本化 registry；单算子 FACTS 只能选 profile
   填事实，不得自行发明投影。
5. **引擎零领域知识**：核心不出现 sparse/CSR/sparsity/if-domain；唯一合法动作是按
   role/profile 键查表。
6. **浮点档位用规范十进制文本做身份**：FACTS、CSV、基线、accept 四处同一 token；
   范围检查可解析 Decimal，但 pairwise/perf 身份禁止二进制 float 或容差比较。
7. **回归门先升级再动手**：现有 check 只证明「新版自洽」，不证明「与旧行为一致」。
   重构前钉住 cherk/sasum 的 CSV/表头/块数/基线字节摘要；另加三件——覆盖每个既有
   role 的投影矩阵测试、一个 sparse 六件包逐字节复核样例、浮点性能键在
   package/generator/accept 三侧的同一性验证。

## 4. 迁移形态与实施序

降险原则：先证明重构无害，再引入新域。

1. E1 探明 arch35 真机 + 剩余事实核对。V2/V3/V5 已用本地 clone（HEAD 5b2a5ba）核完：
   sparse wrapper 无 warm-up，`--calls-per-case` 填 1（blas 是 2，勿抄错）；`expect_result`
   词表各算子不同（SUCCESS/success/singular），accept 不解析、无碍；build.sh 与 ops-blas
   同款 `--ops=`。待核仅剩 V1（gtest 套件名生成器能否按 case_name 过滤）与 V4
   （sparse kernel 的 Task Type）。
2. 钉回归基线（边界 7 的字节摘要）。
3. IR 重构：现有 blas FACTS 完整编译到 IR，**全部派生产物逐字节不变**——这是
   第一个可验收里程碑。
4. accept A1/A2 参数化（环境门探测 + 路径 profile）。
5. 接 sparse profile：C2 列投影 → C3 浮点档位轴 → C1 本体逐面
   （结构对象、descriptor 指称、golden/verify 词表）。
6. D1 文档随实现走（facts-schema / case-strategy / csv-and-blocks 的 sparse 内容，
   SKILL description 范围）。

每步动核心裁决/校验逻辑均无条件过 Codex checkpoint（仓规现行触发条件）。

## 5. 三个最易做错的点（评审点名）

1. 把 sparse 全局造数控制伪装成 C `params`，污染 FACTS 本体与签名。
2. 只统一表头，遗漏轴、行物化、edge、perf、README，形成五套「半统一」规则。
3. 把浮点档位转成数值比较，破坏 CSV、基线与性能键的文本同一性。

## 6. 关联决策与待决

已裁定（对本方案是输入）：发放物按六件；块命名统一 `TC_<块>_`；性能用例固定
200 条 + 空 `gpu_ms` 待填基线；sparse v1 不做 footprint。

待决（立项时处理）：G2 上游改名；G3 sparse 任务书完整性门槛（完整 C 原型含
descriptor、结构参数语义、性能标杆口径——单位与是否已除 0.8）；G4 GPU 基线
回填流程归属。重开 C 案的唯一条件：V1 核实发现 sparse 有不同的用例块生命周期、
执行协议或终判语义——当前证据不支持。
