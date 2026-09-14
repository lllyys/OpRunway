---
name: cann-doc-tutorial-review
description: 评估 CANN 算子仓的「进阶教程」（算子开发指南类）文档质量——通读教程 + 对照代码**静态**查证（默认不跑），逐条定位 漏讲/讲不清/过时/对不上代码/概念讲错，每条带证据、严重度与确定度，出体检报告。涉及「评进阶教程 / 开发指南文档质量 / 文档对不对得上代码 / 教程审稿 / tutorial 体检 / 文档信不信得过」等意图时激活本 skill。（只评进阶教程；quickstart 由 cann-doc-quickstart-check 负责。）
---

# 进阶教程文档体检

模拟一个挑剔但讲证据的审稿人：通读进阶教程，对照代码查证它「看得懂 / 跑得通 / 信得过」，
把「漏讲 / 讲不清 / 过时 / 对不上代码 / 讲错」逐条带证据写成体检报告。进阶教程的目的是「**教得会**」。

**默认不跑**——读懂教程 + 拿代码当裁判做静态评估，不真机执行。**只评、不改、不跑、不探索**；
要真跑 quickstart → `cann-doc-quickstart-check`，跑算子/找 workaround → `cann-ops-run`，
准备环境 → `cann-env-setup`。

## 三条灵魂（不可违背）

1. **不打玄学分**。主观判断（讲不清/教不会/讲错）先 **steelman**——替这段文字辩护到最强
   （「有没有一种读法这里其实没问题？」），辩不过去才算缺陷；报则必带**判例**（读者会卡在哪 + 原文 +
   改进建议），标「教学判断（非代码事实）」。
2. **grep 不到 ≠ 编造**。对照代码只证明「字符串出现过/没出现」，不能证明它是当前有效入口。
   结论**三态**：确认对不上（强证据反证）/ 疑似过时（静态没找到，但可能生成/外部/分支）/
   未找到静态证据（存疑，不指控）。只有强证据才写「确认」。
3. **按「实际开发者影响」判，不按机械字面对照判**。报（尤其升 blocker）前自问：正常开发者照做
   真会被卡住吗，还是他会自然适配？文档大量是示意/演示，不是逐字粘贴。只有明说「粘贴/替换进某文件」
   的**规定式**内容、照做真出错才升 blocker；示意式内容对不上 → 至多 misleading、通常不报。
   别把「逐字符照抄能否编译」当唯一标准——那会制造一堆实际影响不大的误报。

## 入口参数

| 参数 | 取值约束 | 初值推断 |
| --- | --- | --- |
| 目标仓 + 代码根 | 当前目录含 `docs/` 的候选仓；多个/拿不准就 `AskUserQuestion`，或用户给绝对路径 | P0 确认 |
| 待评文档 | **不写死路径**，每次扫描发现（见 P0），可多份 | P0 确认 |
| 仓名/路径/教程位置/SOC/版本 | **一律不写死**，每次发现 + 询问 | 会话内发现 |

**不假定** SOC/版本：教程声明的前提（CANN 版本/SOC）以教程为准核「版本适配」。命令在**项目根**跑，
产物落 `CWD/cann-ops-report/doccheck/<repo>/tutorial/`。

## 前置检查

纯静态（读教程 + grep 代码）→ 本地即可，不需 NPU，无 build/install 副作用。依赖 `python3` + 系统 `grep`,
纯 stdlib；代码在远程就在远程 grep，或把代码根同步回本地。

## 评估框架（五轴）

| 层 | 轴 | 看什么 |
| --- | --- | --- |
| 前置 | **找得到** | 教程是否存在、入口能否导航到（断链/标题误导/目录不可达） |
| **主评** | **信得过** | 命令/flag/路径/默认值/签名/讲错 → 对照代码，分级证据 + 三态 |
| 辅评 | **学得会** | 可迁移性/心智模型/讲「为什么」/audience-fit（按 P1 类型，不必太严） |
| 限定 | 可操作 | 只报静态阻断项，**不下「能跑通」结论** |
| 表达 | **读得懂** | 只报影响理解的术语/结构/模糊决策点 |

**缺陷 = 形态 × 来源**：形态 `缺/糊/错/冗`；「错」的来源 `对不上代码/过时/概念讲错/自相矛盾`。每条先分
**可量化**还是**教学判断**——决定怎么判、结论能下多硬、报告排第几段，分法见
[static-checks.md](references/static-checks.md)。

## 主流程

阶段：P0 发现 → P1 判型 → P2 可量化 → P3 教学判断 → P4 出报告。P2/P3 细节见
[static-checks.md](references/static-checks.md),P4 见 [report-spec.md](references/report-spec.md)。

### P0 — 发现并确认「目标仓 + 代码根 + 待评教程」（中文交互）

1. 发现候选仓（含 `docs/` 的子目录）+ 仓源码根。
2. **范围默认只取根 `docs/` 技术文档**（开发指南/安装/context/invocation 等）,**不逐个算子目录扫**
   （全仓上千篇、问题过多）；要扩到某算子目录由用户**显式指定**。发现命令：

   ```bash
   python3 <skill>/scripts/find_tutorials.py <repo_root> --under docs --json   # 列 <repo>/docs/ 下全部技术 .md
   ```

   带 `--under` 只枚举该子树下全部技术 `.md`;**不要用无 `--under` 的裸调用**（全仓启发式，既越出
   `docs/` 又漏掉 `docs/` 里的非教程文档）。
3. `AskUserQuestion` 确认评哪些（可多份）；一篇都没扫到 → 如实记「该仓无进阶教程」
   （找得到轴覆盖度缺陷），出报告。

### P1 — 判教程类型 + 受众

读标题/开头判类型与 audience-fit，落 `doc_meta`（教程路径 + 类型 + 受众 + 代码根）:

| 类型 | 「讲为什么」 | API 签名比对 |
| --- | --- | --- |
| 教学型（目标即教原理，如《算子开发指南》） | 必要但 graded，不必太严 | 常规 |
| 流程型（纯操作） | 加分项 | grep 只作线索，标「（推断）」 |
| API 用法型（教怎么调接口） | 常规 | 升为必做（须 AST/ctags） |

audience-fit：目标读者/前置能力/是否衔接 quickstart 写明了吗？未写明 → 降低「学得会」置信度，
不直接判教学失败。

### P2 / P3 / P4

- **P2（可量化，对照代码）**：先拿 [problem-taxonomy.md](references/problem-taxonomy.md) 当 checklist
  逐条排查（**3 大类 × 7 错误类型** S1–S7；大类由 `sub` 决定，从而决定结论能到多硬）。
  S6（内容描述不清晰）/S7（关键信息要素缺失）必须二选一，别再写旧 S6 合义。P2.0 脚本铺底、
  逐物 token 分诊、impact 定档见 [static-checks.md](references/static-checks.md)。
  **P2.1** 每条 finding 的字段契约见 [finding-fields.md](references/finding-fields.md)（写 `findings.json` 前读一次）。
- **P3（不可量化，学得会/读得懂）**：steelman → 判例闸 → 报，概念讲错需外部反证——规则见
  [static-checks.md](references/static-checks.md)。**报前先过
  [problem-taxonomy.md](references/problem-taxonomy.md)「报前自问」**：纯字面/
  示意式/风格不统一 → 不报，别把瑕疵当误导。
- **P4（出报告）**：渲染命令、必备模块、自校验闸与跨轴去重、产物结构见 [report-spec.md](references/report-spec.md)。
  渲染后可跑 **P5** 导出仓库同名 Excel（明细 7 列 + 统计柱状图）：`python3 <skill>/scripts/export_excel.py --repo <repo>`
  （见 report-spec;openpyxl/matplotlib 可选，缺则自动降级 csv/svg）。

## 铁律与禁忌

开头「三条灵魂」即前三条铁律，此处只列补充：

- ✓ **默认不跑（静态）**：结论只能写「未发现/存在静态阻断项」，**禁止**写「能跑通」
- ✗ **代码事实与教学判断分开标注**，报告分两段，不混
- ✗ **不凭空捏造**：每条缺陷必带代码位置或判例
- ✓ 评分**只定性**（档 + 缺陷计数）,**不打数字分**；仅可量化条可计数

## 失败去向

| 触发 | 行为 |
| --- | --- |
| 仓内无进阶教程 | 记「无进阶教程」（找得到轴覆盖度缺陷），出报告 |
| 抽出的物是占位符/外部命令/生成产物 | 不按字面判「对不上」，按 `static-checks.md` 分诊 |
| 教学缺陷过不了 steelman | **不报**（它其实站得住） |
| 想判「概念讲错」但只有直觉、无外部反证 | 降级「疑似概念风险 / 需人工确认」，不写「确认」 |
| 链接目标缺失，但可能是本地克隆不全 | 先过 linkcheck 护栏：越出仓根→降级、submodule/LFS→跳过（见 `problem-taxonomy.md`），别写「确认死链」 |

## 参考资料

- [problem-taxonomy.md](references/problem-taxonomy.md) — 3 大类 × 7 错误类型（S1–S7）清单 + 落类判据 +
  「报前自问」+ 已实测判定陷阱 + 本地克隆护栏。**P2 开始时当 checklist**
- [static-checks.md](references/static-checks.md) — 指标第一分类、P2.0 脚本、逐物分诊、impact、P3 steelman 闸、
  批量模式。**P2 起一直用**
- [finding-fields.md](references/finding-fields.md) — finding 字段契约。**写 findings.json 前读**
- [report-spec.md](references/report-spec.md) — 渲染命令、必备模块、自校验闸与跨轴去重、产物。**P4 出报告时读**
- `templates/report-engine.html` — HTML 报告引擎（自包含，**勿改**）
