# sparse R1 交接（给零上下文的新 session）

本文的读者是一个**刚接手、没有任何前文**的 session。读完这一份就能开工，不需要翻
聊天记录。术语在首次出现处给定义或指向定义。

## 1. 一句话背景

本仓有两个算子验收 skill：`repo-task-blas-case-gen`（按任务书造测试用例包）与
`repo-task-blas-accept`（拿包去真机跑测并出验收结论）。它们原本只服务
稠密 BLAS 算子（`cann/ops-blas`）。**sparse R1 这个任务，是让同一条流水线也能服务
稀疏算子仓 `cann/ops-sparse`**，第一个打通的算子是 `coo2csr`。

架构结论早已定案，不要重开：域差异（列名、词表、惯例）全部塞进一份叫 **registry**
的数据表，引擎代码不长 `if sparse` 这类分枝。定案与三案对比见
[sparse-support-candidate-plan.md](sparse-support-candidate-plan.md)。

## 2. 现在在哪

| 阶段 | 状态 |
| --- | --- |
| M0 / M0.5 | **已完成**：仓普查、投影矩阵 fixture、registry 接口冻结，均已过评审 |
| M1′ | **下一步，未动工**。第一个动作见 §5 |
| M3′ / M2·5·6′ / M7 | 未开始 |

代码侧**一行都还没改**。至今全部产出是 `dev-doc/` 下的文档与 fixture 资产。

## 3. 开工前必读（按顺序，约 20 分钟）

1. [sparse-r1-implementation-plan.md](sparse-r1-implementation-plan.md) —— 主文档。
   顶部有「阅读约定」术语表，§0 是纪律与不变量，§2 是你要做的 M1′。
2. [sparse-r1-registry-freeze.md](sparse-r1-registry-freeze.md) —— registry 的
   12 个字段面，**已冻结**，改它要过 Codex 评审。
3. [sparse-r1-projection-fixture/README.md](sparse-r1-projection-fixture/README.md)
   —— 三道回归门里最重要的那道怎么用。

按需再读：[census](sparse-r1-census.md)（26 个算子的列契约实况）、
[projection-matrix](sparse-r1-projection-matrix.md)（模板里每个参数角色影响哪些
产物，M1′ 改代码时的地图）、[learning-map](sparse-gap-learning-map.md) §0
（V1–V5、E1、G1–G4 这些编号的定义与状态，唯一出处）。

## 4. 环境事实

两个绝对路径（本文其余路径都相对工作树根）：

```text
工作树      /Users/ll/Desktop/workspace-ascend/OpRunway/.claude/worktrees/oprunway-sparse-r1
只读克隆    /Users/ll/Desktop/workspace-ascend/OpRunway/repos/ops-sparse
```

| 项 | 值 |
| --- | --- |
| 分支 | `feature/sparse-r1`，HEAD `d6fb75e` |
| 待改代码 | `plugin/skill/repo-task-blas-case-gen/`（M1′、M3′）、`repo-task-blas-accept/`（M2·5·6′） |
| 只读克隆 | ops-sparse 仓，HEAD `5b2a5ba`，**在工作树之外**，只读不改 |
| 真机 | 未探明（编号 E1）。本地做不了 A3/A4，全部归 M7 |

## 5. 立刻可做的第一步

M1′ 步骤 1：把 registry 作为一个普通字典**追加**进
`plugin/skill/repo-task-blas-case-gen/assets/template/gen_csv.py` 的通用代码区，
本阶段只填 blas 那一档的值，且**先不接任何消费者**。

这一步是纯追加，不改任何既有行为，做完三道门必须零差异。plan §2 写死了后续
步骤的顺序，照着走，不要跳步或合并提交。

## 6. 怎么验证（三道门，一条命令）

```bash
cd /Users/ll/Desktop/workspace-ascend/OpRunway/.claude/worktrees/oprunway-sparse-r1
bash dev-doc/sparse-r1-regression-gate.sh
```

打印 `GATE: GREEN` 才算过。三道门分别是：

1. **10 项派生物摘要** —— cherk/sasum 两个示例包各 5 个渲染产物的 SHA-256，比对
   [sparse-r1-baseline-digests.txt](sparse-r1-baseline-digests.txt)。
2. **双示例 check** —— 两个示例包跑 `package.py check`，退出码须为 0。
3. **fixture `--check`** —— 12 个合成用例（5 正例覆盖各种参数角色、7 负例钉住已知
   缺陷的现状行为）重跑并逐字节比对。

M1′ 全程要求**零差异**。只有 M1′ 步骤 4 允许两行差异，且限定为 digests 文件里
两个 `gen_csv.py` 的参考摘要（它们本就标注「不进逐字节门」）。

## 7. 红线与纪律

- **改 `plugin/skill/**` 前要先解 skill-edit-gate**：本会话内先读一遍
  `skill-best-practices`，并挂 `/skill-creator`，否则写入会被 hook 挡下。
- **Codex 评审只在两处**：registry 冻结面真变更；push 前仓规一轮。另有一处仓规
  硬性触发——改 `accept.py` 的 verdict（在 M2·5·6′），无条件过一次。
  不设逐里程碑 checkpoint。
- **不新增门/探针**。用户明确裁定过：门就这三道加一次 coo2csr 首通。
- commit 不带 AI 署名；**不 push**，除非用户明说。
- 写 markdown 守 [prose-style](../.claude/rules/prose-style.md)：一行 ≤100 字符，
  并列 ≥2 条要列表化。

## 8. 五个容易踩的坑

1. **两个包都叫 coo2csr**。仓内**存量包**是 41 行、命名 `L0_/L1_`、没有性能用例，
   它的验收结论「性能通过（无性能要求）」是**正确**的；M3′ 由 case-gen 造的**新包**
   有 200 条性能用例且基线全空，结论该是 NO_REF 与证据不足。别拿一个的预期去核
   另一个。
2. **重钉协议不是万能出口**。「重钉」指模板改动后重录基线摘要。M1′ 步骤 1 至 3
   禁止重钉——那三步的全部意义就是证明重构没改行为，允许重录等于自证。
3. **`TC_` 硬筛有两道，改一道等于没改**：`accept.py:131` 与 verify_accuracy 模板
   `:237`。存量包的 `L0_/L1_` 用例两处都会被滤光。
4. **A5 的 bug 根因是「拿可比集大小当有没有性能用例的代理」**，不是「可比集为空
   就判通过」。改的时候要把判据换成 CSV 里的 `TC_PF_` 行数，别只动分支条件。
5. **编辑器自动格式化会破坏 markdown**。实测：它把表格填充对齐后 19 行超出 100
   字符上限，还把行首的 `+`（连接词「加上」）normalize 成 `-`（列表项），语义变了。
   提交前务必跑一次行长检查，看到非自己所做的格式化 diff 就丢弃。

## 9. 未决项（不阻塞 M1′）

- **E1**：arch35 真机未探明，阻塞 M7 与「正式支持」声明，不阻塞本地实施。
- **G2 / G3 / G4**：上游 skill 改名、sparse 任务书完整性门槛、GPU 基线回填归属，
  三项待用户裁定。
- **存量陷阱 trap1/2/3/5**：case-gen 里四个已知缺陷，blas 现网同样带着跑，本期
  不修，挂在 [oprunway-todo.md](oprunway-todo.md)。fixture 负例照旧钉住它们的
  现状行为——**看到这些负例「失败」是预期的，不要去修**。
