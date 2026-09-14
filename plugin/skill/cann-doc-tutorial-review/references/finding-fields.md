# finding 字段契约（P2.1）

> **何时读本文件**：P2/P3 产出 findings、准备写 `findings.json` 时。
> 字段对齐报告引擎 `templates/report-engine.html`，**面向文档维护者、不写黑话**。
> `render_html` 直接用这些字段；缺了才从 `improvement` 降级派生，质量打折。

## 必填分类字段

| 字段 | 含义 |
|---|---|
| `sub` | 细类 `S1`–`S7`（**必填**，即错误类型）——大类与「结论能到多硬」由它推出，见 `problem-taxonomy.md`。**S6 内容描述不清晰 / S7 关键信息要素缺失 必须二选一**，别再写旧版 S6 的「讲不清+缺必要信息」合义 |
| `impact` | `blocker`（阻断）/ `misleading`（误导）。瑕疵 `minor` 本轮不报 |
| `verdict` | 三态结论（**必填**）——`CONFIRMED_*` = 确认，其余 = 疑似 |

> 「错误类型」（Excel 列 / 统计柱状图轴标签）由 `sub` 推出全称（`_state.err_type(sub)`），finder 不用另填：
> S1 文档接口声明与代码不符 · S2 文档示例代码不符合预期行为 · S3 引用或路径失效 ·
> S4 同源文档内容冲突 · S5 概念/术语讲错 · S6 文档内容描述不清晰 · S7 关键信息要素缺失。

> **不要再填 `conf`**：确定度就是 `verdict` 本身，单独一个布尔只会跟它打架（曾出现 verdict 写「疑似」、conf 写 true，报告两处自相矛盾）。render 一律按 `verdict` 推，老产物里的 `conf` 被忽略。
>
> 旧的 `type`（missing/untrust/readable）与 `check`（code/doc/rule）**已被大类吸收，finder 不必填**；老 `findings.json` 里若有，render 会忽略。

其余基础字段：`category` / `axis` / `quote`（原文） / `code_location` / `root_cause` / `doc`（批量模式下指明哪一篇）。

## 展示字段

| 字段 | 必要性 | 内容 |
|---|---|---|
| `prob` | 必填 | [1] 问题一句话 ≤40 字，格式「\<要素\>\<错在哪\>」 |
| `fix` | 必填 | [2] 修复方向一句话（`improvement` 的一句话浓缩） |
| `conseq` | 可省 | 对开发者的后果；省略时按 `impact` 自动填默认句 |
| `fig` | 可省 | 图示，**只在真能画清楚时才给**（见下）；拿不准就别写 |

`improvement` 仍保留（完整解释，供 MD 报告与卡片折叠区）。

> `conseq` / `fig` 都有降级路径，为凑字段硬编一个后果或图示，比省略更糟。

## `fig` 三形态

| 问题性质 | 结构 |
|---|---|
| 不可信（文档说的和实际不符） | `{kind:'conflict', a:{tag, val 文档错}, b:{tag, val 实际对}}` |
| 缺失（该讲没讲） | `{kind:'gap', have:{tag, items[]}, doc:{tag, items[]（缺项加 "缺:" 前缀）}}` |
| 不一致（多处自相矛盾） | `{kind:'incons', items:[{loc, val}], note}` |

拿不准就**省略** `fig`。`figNote` 可选注解，可用 `<b>对</b>` / `<span class="bad">错</span>` 标色。
