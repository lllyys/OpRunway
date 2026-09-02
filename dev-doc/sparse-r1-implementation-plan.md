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

## 0. 范围、终态与纪律

**范围**：ops-sparse 仓 26/33 个 frame 惯例算子（成员清单唯一出处
[sparse-r1-census.md](sparse-r1-census.md) §1）。首通算子 **coo2csr**。
辅助负例 CSV 归 harness 自有（冻结 §2.6），不在验收范围；spgemm 950 ATK/torch
形态是特例不做；E1（arch35 真机）只阻塞 M7，不阻塞本地实施。

**R1 预期终态（写死防误报）**：本期无 GPU 基线，200 性能点全待填，A5 预期结论
**「精度通过、性能 NO_REF、总体证据不足」**，退出码非 0 属预期。「正式通过」
前置是 G4 基线回填，M7 之后另立。静态自洽不得说成正式支持。

**精简纪律（用户裁定，本版执行基调）**：唯一门集合是**三道既有回归门 + 一次
coo2csr 纵向首通**——10 项派生物摘要、cherk/sasum 双示例 check、fixture
`record_fixture.py --check`。不再新增任何门/探针；每里程碑取最小 diff；一切用
普通函数与字典，不引入类层次与新校验层。Codex 评审只在两处：**接口真变更**
（registry 冻结面）与 **push 前仓规一轮**；不设逐里程碑 checkpoint。

**模板变更的重钉协议**（代替已砍的 allowlist/语义快照）：改模板必然改渲染物。
协议是：跑 `--check`（先 `--refresh-common`），核对差异清单**恰好**是本次变更
预期影响的条目，然后在同一 commit 里重录基线与受影响摘要——靠 diff 审查收口，
不建新机制。

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
| M1′ | 最小生成基础：registry 落地 + blas 硬编码参数化 + 表头/行写入统一 | 三门全绿（重钉协议） |
| M3′ | v2 首包能力：profile/overrides/case_controls/golden=harness + coo2csr 六件 | coo2csr check=0 + 三门 |
| M2·5·6′ | accept 纵向闭合 + 三类产物 + 文档随改 | coo2csr 本地 smoke + 三门 |
| M7（阻塞 E1） | 真机：空闲门实现、A3–A5、V4、calls_per_case 链、repro 补全、G4 复验 | 真机证据 |

## 2. M1′ · 最小生成基础

1. registry（9 字段 × 2 profile，值按冻结文档与普查数据）作为普通字典进模板公共
   代码区；package.py 经 `_load_generator()` 取用。
2. blas 硬编码三处改查 registry，值与现状逐字节一致：S1 首参断言
   （package.py:1169，改按 `first_param_ctype`，none 则跳过）、生成侧默认 expect
   token（模板 :560）、状态/edge 词表（:75，改由 FACTS 精确子集派生，blas 示例的
   子集即现词表）。
3. 表头/行写入统一：一个普通函数产有序列描述（普通 dict），
   模板 `_header_columns`、行物化、README 契约表、`--print-header` 都从它取；
   删 package.py 的 `_header_columns` 副本（:1221）与漂移自检（:1773）。
   轴/edge/perf 校验暂不动——case_controls 接入时（M3′）只做必要适配。
4. 示例公共区确定性迁移（只保 FACTS 区、换公共区、GENERATOR_VERSION=2），
   派生物 10 项逐字节不变；fixture 走重钉协议。

## 3. M3′ · v2 首包能力

1. schema v2：新键 `harness_profile`（必填）、`harness_overrides`（恰四键，冻结
   §2.5）、`case_controls`（`{name, kind: enum|tier, values}`；值为字符串、去重、
   **原始字符串端到端传递，判等即文本相等**——不建规范形机制，原 M4 取消）、
   `status_vocab`（必填，⊆ profile 上界）、`golden: {"kind": "harness"}`（与
   symbol/formula 互斥）。v1 FACTS 行为不变（旧 checker 拒 v2 属预期）。
2. 投影：case_controls 产直接列并进轴；`perf.key` 可引用 control 名（text 型）。
   新 control 的列名/轴名唯一性与命名空间冲突检查随实现自带（这是 sparse 首包的
   正确性需要，非新增门；顺带覆盖存量陷阱 trap4/6 的 sparse 面）。
3. `footprint_policy` 生效：sparse_frame=`no_static_check`，模板 `_row_is_valid`
   按它跳过 `_footprint`。
4. 产出 coo2csr FACTS（schema v2、sparse_frame、case_controls 按仓内列契约、
   200 PF 全待填）→ 六件 render → `check` 退出 0；warnings 空、列命中仓内源码、
   TC_ 命名成立作为 check 的成功判读，不另立探针。

## 4. M2·5·6′ · accept 纵向闭合

1. A1：按各 profile `entry_headers` 探测（0/多命中硬失败列候选，恰一命中把
   profile 键名记入 runtime manifest）；CANN 缺失退出 3 属预期照实写。
2. A2：两个 verify 模板的二进制寻址改 `build/test/**/<op>_test` glob 唯一命中
   resolver（0/>1 fail-closed 报候选）；README 模板与 CLI help 的 ops-blas 措辞
   随手改为 profile 渲染。
3. 精度期望集修正：主 CSV 除 `TC_PF_` 外**全部有效数据行**进期望集（现状按 TC_
   前缀收会漏光 coo2csr 41 行）；gtest 映射按 case_name 精确匹配。
4. A5 空基线终态：无 `TC_PF_` → 通过（无性能要求）；有 `TC_PF_` 但可比集空 →
   性能 NO_REF、总体证据不足、退出码 2。
5. 三类产物最小布局（用户裁定）：`report/report.md`（模板是 skill 资产
   `assets/template/report.md`，三节：精度、性能、备注说明；前两节 verdict 数据
   填模板，备注归 agent）、`intermediate/`（全部执行期 JSON/日志/runtime）、
   `repro/`（六件副本 + 用例清单含失败标注；`rerun.sh` 与环境指纹归 M7——本地
   阶段没有真实执行可复现）。
6. 两个小改随手带上（无探针）：CSV 注释行统一 strip 口径（accept.py:179）；
   `calls_per_case` 以 manifest 为单一来源、量具只读它。
7. coo2csr 单一纵向 smoke：对本地 ops-sparse 克隆走 A1 探测 + A2 布局推断 +
   runtime 渲染 + 期望集构建（41 行全进），一次跑完当完成线；真机 A3/A4 归 M7。
8. 文档随改：只更新实际变更的契约文档（facts-schema、run-chain、README 模板、
   SKILL description 范围）+ changes brief；无独立文档里程碑、无冷读 agent。

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
- 关键坐标：模板/package 两份 `_header_columns`（gen_csv.py:470 / package.py:1221）、
  `_load_generator`（package.py:1299）、漂移自检（:1773）、blas 专属校验三处
  （package.py:1169 / 模板 :560 / :75）、量具双 resolver（verify_accuracy.py:75 /
  verify_performance.py:98）、归一三处（package `_normalized_perf_key`、量具 :299、
  accept :211）、edge/perf 校验（package.py:927/:994）、A5 性能结论
  （accept.py:857 一带）、`_row_is_valid`/`_footprint`（模板 :462/:466）。
- 已消核对项：V1/V2/V3/V5（见 learning-map §0）；待真机：V4、E1。
- 纪律：skill-edit-gate（先读 skill-best-practices + 挂 /skill-creator）；
  commit 不带 AI 署名；不 push 除非明示；接口变更过 Codex，push 前仓规一轮。
