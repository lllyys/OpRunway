# sparse R1 实施计划（v3·精简版）

> **状态：已立项实施中**（分支 `feature/sparse-r1`；M0/M0.5 已完成）。
> 编号与状态唯一源：[sparse-gap-learning-map.md](sparse-gap-learning-map.md) §0；
> 接口唯一源：[sparse-r1-registry-freeze.md](sparse-r1-registry-freeze.md)（9 字段版）。
> 目标：case-gen 能为 frame 惯例的 sparse 算子造六件，开发者 harness 原样消费，
> accept 出裁决（R1）。本版按用户精简令（2026-09-01「不要加太多的门，只做最精简的
> 改动」）与 Codex 复审（TRIM_NEEDED，thread `01a05fde`）自 v2 裁剪而来；
> 历史演进见 changes brief 与 Git。

**阅读约定**（自造术语，冷读先过）：

| 术语 | 含义 |
| --- | --- |
| 六件 | 任务包六个文件：gen_csv.py、`<op>_test.csv`、两个 verify 脚本、README、gpu_baseline.csv |
| S1–S3 / A1–A5 | 阶段号：S=case-gen 生成链（校验→渲染→check）；A=accept 验收链（A1 环境、A2 部署与运行时包、A3 精度、A4 性能、A5 结论） |
| NO_REF | 性能汇总态：有性能用例但全配不到基线——证据不足，不判 PASS/FAIL |
| FACTS | 单算子事实字面量（AST 白名单、从不执行），见 facts-schema.md |
| registry | skill 内版本化的有限 profile/词表数据集，装域级惯例（9 字段，见冻结文档） |
| frame 惯例 | ops 仓 `test/frame/` 公共件之上的 CSV 驱动 gtest 写法（param.h 逐列读 CSV） |
| V1–V5/E1/G1–G4 | 核对与治理编号，定义见 learning-map §0 状态表 |
| 三道回归门 | 10 项派生物摘要（baseline-digests 文件）、双示例 check、fixture `--check`（fixture README） |

## 0. 范围、终态与纪律

**范围**：ops-sparse 仓 26/33 个 frame 惯例算子（成员清单唯一出处
[sparse-r1-census.md](sparse-r1-census.md) §1）。首通算子 **coo2csr**。
辅助负例 CSV 归 harness 自有（冻结 §2.6），不在验收范围；spgemm 950 ATK/torch
形态是特例不做；E1（arch35 真机）只阻塞 M7，不阻塞本地实施。

**两个 coo2csr 包，别混**（本文凡出现「新包/存量包」按此）：

| 包 | 来源与内容 | 用途 | A5 预期 |
| --- | --- | --- | --- |
| **存量包** | 仓内 `test/coo2csr/arch35/` 的 41 行，`L0_/L1_` 命名，无 PF 行 | 验 accept 吃旧包 | 性能**通过（无要求）** |
| **新包** | M3′ 由 case-gen 造的六件，含 200 条 `TC_PF_` 全待填 | 验造包与吃新包 | 性能 **NO_REF**、证据不足 |

**R1 预期终态（写死防误报）**：新包这条链无 GPU 基线，A5 预期就是上表右列的
NO_REF 与证据不足，退出码非 0 属预期；存量包那条链没有性能要求，判「通过」不是
漏判。「正式通过」前置是 G4 基线回填，M7 之后另立。静态自洽不得说成正式支持。

**精简纪律（用户裁定，本版执行基调）**：唯一门集合是**三道既有回归门 + 一次
coo2csr 纵向首通**——10 项派生物摘要、cherk/sasum 双示例 check、fixture
`record_fixture.py --check`。不再新增任何门/探针；每里程碑取最小 diff；一切用
普通函数与字典，不引入类层次与新校验层。Codex 评审只在两处：**接口真变更**
（registry 冻结面）与 **push 前仓规一轮**；不设逐里程碑 checkpoint。

**重钉协议与它的适用边界**（代替已砍的 allowlist/语义快照）：协议本身是——跑
`--check`（模板改过则先 `--refresh-common`），核对差异清单**恰好**是本次变更预期
影响的条目，然后在同一 commit 里重录基线与受影响摘要，靠 diff 审查收口。

边界必须写死，否则「行为不变」的证明力会被重钉稀释：

- **M1′ 步骤 1 至 3 不许重钉**，完成线是**三门零差异**。这三步的全部意义就是证明
  重构没改行为，一旦允许重录，证明就自我循环了。
- **只有 M1′ 步骤 4**（示例公共区迁移）用重钉，且差异面**预先限定为两行**：
  `baseline-digests.txt` 里两个 `gen_csv.py` 的参考摘要。这两行本就标注「不进
  逐字节门」，10 项派生物与 fixture 的 `results` 仍须零差异。
- M3′ 起引入新行为后，重钉按各里程碑写明的预期差异面执行。

**不变量**：裁决路径唯一；`params` 逐字对应 C 原型（造数控制走 case_controls）；
版本策略 v2 承载新键（v1 隐式 blas、行为逐字节不变；**v2 必填 `harness_profile`**，
不留第二套隐含接口）；`perf` 节存在时 rows 恰 200；产物三类划分（人读/中间产物/
最小可复现）；**NPU 现场空闲门**（用户裁定）——A3/A4 起跑前在两个 verify 模板内
现场核查目标卡空闲（fail-closed：查询失败/忙均阻塞、退出码 4、结果记 payload），
**实现与三分支实测归 M7**（本地阶段无 NPU 链可跑）。

## 1. 里程碑（最小序）

| 里程碑 | 内容 | 完成线 |
| --- | --- | --- |
| M0/M0.5（已完成） | 普查、投影矩阵 fixture、registry 冻结（9 字段） | 已过 checkpoint |
| M1′ | 最小生成基础：registry 落地 + blas 硬编码参数化 + 表头/行写入统一 | 三门**零差异**（仅步骤 4 允许两行参考摘要变） |
| M3′ | v2 首包能力：profile/overrides/case_controls/golden=harness + **新包**六件 | 新包 check=0 + 三门 |
| M2·5·6′ | accept 纵向闭合 + 三类产物 + 文档随改 | **存量包**本地 smoke + 三门 |
| M7（阻塞 E1） | 真机：空闲门实现、A3–A5、V4、calls_per_case 链、repro 补全、G4 复验 | 真机证据 |

## 2. M1′ · 最小生成基础

按七维审的裁定，本里程碑**终态由复杂度定、顺序由爆炸半径定**：追加式迁移，
每步单独提交可回滚，不可逆动作排最后。

1. **追加** registry 进模板公共代码区（普通字典）；本里程碑只实例化 **blas profile
   的值**（sparse_frame 值归 M3′）；package.py 经 `_load_generator()` 取用。
2. blas 硬编码三处**逐个**切为查 registry，值与现状逐字节一致、一处一验：
   S1 首参断言（`package.py:1169` 一带的 `raw_params[0]` handle 检查 →
   `first_param_ctype`，none 则跳过）、生成侧默认 expect token（模板 `:560`
   `_make_body` 的 `expect=` 默认参数）、状态词表（**`package.py:75` 的
   `STATUS_VALUES` 元组**，不在模板里）。**v1 词表策略**：v1 继续用 blas 完整
   词表（registry 上界即现词表）；「FACTS 精确子集」是 v2 的事，不倒置。
3. 表头/行写入统一，**迁移序写死**：
   a. 追加共享列描述函数（普通 dict），先不接任何消费者；
   b. 旧 package.py 副本与漂移自检**临时留作影子 oracle**——`_check_generation_report`
      （`package.py:1773` 一带）本就在比「模板产的 header」与「package 副本产的
      header」，模板侧换成新函数后它自动变成新旧对照。**注意它只在 `check` 路径
      跑，`render` 路径没有这层对照**，所以每步都要跑 check 而不能只 render；
   c. 逐消费者切换：行写入 → README 契约表 → `--print-header`，每切一处跑三门；
   d. 全部一致后**最后单独一步**删副本（`:1221`）与漂移自检（`:1773`），可独立回滚。
   轴/edge/perf 校验本里程碑不动。
4. 示例公共区确定性迁移（只保 FACTS 区、换公共区、GENERATOR_VERSION=2）——
   独立的最后一个机械提交；10 项派生物摘要不变，两个 gen_csv.py 摘要变化为预期
   差异（重钉协议）。

## 3. M3′ · v2 首包能力

1. schema v2：新键 `harness_profile`（必填）、`harness_overrides`（恰四键，冻结
   §2.5）、`case_controls`（`{name, kind: enum|tier, values}`；值为字符串、去重、
   **原始字符串端到端传递，判等即文本相等**——不建规范形机制，原 M4 取消）、
   `status_vocab`（必填，⊆ profile 上界）、`golden: {"kind": "harness"}`（与
   symbol/formula 互斥）。校验按版本分派（v1/v2 各自路径），**v1 路径逐字节保持**，
   v2 可整段回滚不牵动 blas；registry 的 sparse_frame 值在此实例化。
2. 投影：case_controls 产直接列并进轴，接入顺序**列 → 轴 → perf.key** 逐步可撤；
   control 逻辑只在 v2 且字段存在时进入，v1 不走任何新路径。新 control 的列名/
   轴名唯一性与命名空间冲突检查随实现自带（sparse 首包的正确性需要，非新增门；
   顺带覆盖存量陷阱 trap4/6 的 sparse 面）。
3. `footprint_policy` 生效，**最窄分支**：仅 `no_static_check` 跳过 `_footprint`，
   `dense_formula` 走完全未改的旧逻辑。
4. 产出**新包**：coo2csr FACTS（schema v2、sparse_frame、case_controls 按仓内列
   契约、200 PF 全待填）→ 六件 render → `check` 退出 0；warnings 空、列命中仓内
   源码作为 check 的成功判读。**命名口径**（消七维审指出的歧义）：新包用
   `TC_<块>_` 命名（用户既有裁定，V1 已证 gtest 按 case_name 全名过滤可跑任意
   名字）；存量包的 `L0_/L1_` 命名由 M2·5·6′ 的映射修正保证可验，两者并存不矛盾。

## 4. M2·5·6′ · accept 纵向闭合

实施序按爆炸半径排：语义改动在前逐步可撤，**目录重排排最后**；每个改写型动作
先追加影子、核对后再切换。

1. A1：**先追加** profile 探测函数（按各 profile `entry_headers`，0/多命中硬失败
   列候选）影子记录，确认 blas 仍唯一选中后再替换硬编码判断；恰一命中把 profile
   键名记入 runtime manifest；CANN 缺失退出 3 属预期照实写。
2. A2：两个 verify 模板的二进制寻址改「候选收集 + 唯一裁决」resolver
   （`build/test/**/<op>_test` glob，0/>1 fail-closed 报候选——注意现状遇多路径
   取首个，失败语义会变，blas 目录形态回放里核对三态）；README 模板与 CLI help
   的 ops-blas 措辞随手改 profile 渲染。
3. 精度期望集修正（P0）：**accept.py 与 verify 模板两侧同改**。现状两道硬筛都在：
   `accept.py:131` 的 `name.startswith("TC_")`，与 verify_accuracy 模板 `:237` 的
   `if "/TC_" not in full_name: continue`。存量包 41 行是 `L0_/L1_`，两处都会滤光。
   改为：期望集 = 主 CSV 除 `TC_PF_` 外全部有效数据行；gtest 映射删 `/TC_` 硬筛，
   按期望集中的 case_name 精确匹配。**只改一侧无效**——只改 accept，41 行进了
   期望集也会在映射层被滤掉。
4. A5 空基线终态：**证据先行**——先在性能证据里加 `total_pf/comparable_pf` 并确认
   区分正确，**最后一步**才切 verdict。现状的精确形态（`accept.py:857` 一带）是
   `expected_count == 0 and not path.is_file()` 判「通过」，理由写「部署 CSV 没有
   `TC_PF_` 用例」——**根因是拿「可比集大小」当「有没有 PF 行」的代理**：新包有
   200 条 PF 但基线全空时可比集也是 0，于是被误判成「没有性能要求」。改法是把
   判据换成 CSV 里的 `TC_PF_` 行数：无 PF 行 → 通过（无性能要求）；有 PF 行但
   可比集空 → 性能 NO_REF、总体证据不足、退出码 2。本项动 accept 核心裁决，
   按仓规**无条件过一次 Codex checkpoint**（仓规既有触发条件，非新增门）。
5. **一次性 blas 兼容回放**（七维审 P0，一次性验证、不设常设门）：复用 cherk/
   sasum 既有资产走 A1 → A2 → 期望集 → A5，覆盖两种 A5 状态（有可比 PF /
   PF 全无基线）；注释行口径的验证并入本回放，不单设探针。
   **预期的行为变更，不是回归破坏**：sasum 的 `gpu_baseline.csv` 200 行全空，
   按第 4 项改完后它的 A5 会从现状的「通过」变为「NO_REF、证据不足」。回放里
   看到这条结论翻转是**正确**的，判据是「翻转仅发生在有 PF 行且基线全空的包上」，
   cherk（4 行有基线）不得翻转。
6. 三类产物最小布局（用户裁定），**排本里程碑最后**：先向新布局追加/复制、验证
   完整后切默认读取，旧路径删除单独提交可回滚。内容：`report/report.md`（模板是
   skill 资产 `assets/template/report.md`，三节：精度、性能、备注说明；前两节
   verdict 数据填模板，备注归 agent）、`intermediate/`（全部执行期 JSON/日志/
   runtime）、`repro/`（六件副本 + 用例清单含失败标注；`rerun.sh` 与环境指纹归
   M7）。
7. 两个小改：CSV 注释行统一 strip 口径（accept.py:179）；`calls_per_case` 以
   manifest 为单一来源——CLI 参数兼容期保留但必须与 manifest 相等，删除另议。
8. 单一纵向 smoke，跑的是**存量包**（本地 ops-sparse 克隆里的
   `test/coo2csr/arch35/`）：A1 探测 → A2 布局推断 → runtime 渲染 → 期望集构建
   （41 行全进，无一条被 `TC_` 硬筛滤掉）→ A5 判「性能通过（无性能要求）」，
   一次跑完当完成线。M3′ 造的新包在本地只到 `check`，它的 A2–A5 需要真机，归 M7。
   真机 A3/A4 一律归 M7。
9. 文档随改：只更新实际变更的契约文档（facts-schema、run-chain、README 模板、
   SKILL description 范围、`no_static_check` 的 authoring 提示进 facts-schema）
   + changes brief；无独立文档里程碑、无冷读 agent。

## 5. M7 · 真机（阻塞 E1，单列）

E1 探明 950 环境；NPU 空闲门在两个 verify 模板实现 + 三分支实测（本地可用命令桩
自检一次，不设回归门）；calls_per_case 性能链实跑；A3–A5 真机证据；V4（Task
Type）核对；repro 补 `rerun.sh` 与环境指纹；G4 基线回填后复验「正式通过」。

## 6. 明确不做（本期）

- 原 M4 tier 规范形/canonicalizer/三侧同一性探针——control 值原始字符串端到端。
- 批量实证（3–5 算子）与对抗验证 fan-out——首通后按需另立。
- 语义快照、变更 allowlist、report/repro 可移植探针、冷读 agent、prose 完成门、
  逐里程碑 Codex checkpoint。
- 存量陷阱 trap1/2/3/5 的修复——blas 现网同样带着跑，挂 todo 存量项；
  fixture 负例照旧钉现状（trap4/6 的 sparse 面在 M3′ 顺带覆盖，届时按重钉协议
  重录对应负例）。
- descriptor/结构对象本体；ATK/torch 通路；sparse footprint 静态估算。

## 7. 交接清单

- 文档：本文、registry-freeze（9 字段版）、census、projection-matrix、fixture
  （含 `--check` 门与 README 处置表）、baseline-digests、learning-map §0 状态表。
- 本地只读克隆（绝对路径，不在 worktree 内）：
  `/Users/ll/Desktop/workspace-ascend/OpRunway/repos/ops-sparse`（HEAD `5b2a5ba`）。
- 关键坐标（已逐条对源码核过，行号以 `7d5c443` 为准）：两份 `_header_columns`
  （模板 `:470` / package `:1221`）、`_load_generator`（package `:1299`）、
  漂移自检 `_check_generation_report`（package `:1773`）、blas 专属三处
  （package `:1169` 首参 handle 检查 / 模板 `:560` `_make_body` 的 expect 默认值 /
  **package `:75` 的 `STATUS_VALUES`**）、量具双 resolver（verify_accuracy `:75` /
  verify_performance `:98`）、归一三处（package `_normalized_perf_key` / 量具 `:299` /
  accept `:211`）、edge/perf 校验（package `:927` / `:994`）、
  **TC_ 两道硬筛（accept `:131` / verify_accuracy `:237`）**、A5 性能结论
  （accept `:857` 一带）、`_row_is_valid` / `_footprint`（模板 `:462` / `:466`）。
- 已消核对项：V1/V2/V3/V5（见 learning-map §0）；待真机：V4、E1。
- 纪律：skill-edit-gate（先读 skill-best-practices + 挂 /skill-creator）；
  commit 不带 AI 署名；不 push 除非明示；接口变更过 Codex，push 前仓规一轮。
