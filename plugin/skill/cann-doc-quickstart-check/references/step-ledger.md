# 逐步台账：把文档解析成步骤、执行、判定

P1 解析文档、P3/P4 逐步执行时读这份。

## P1 — 把文档解析成字面步骤序列

通读选定文档，抽出**有序**步骤。下表是解析期给每步打的标签，其中只有 `command`
（及 `cwd` / `expected`）会进 `steps.json`，`kind` 是心里的分诊，不持久化：

| 字段 | 含义 | 落盘 |
| --- | --- | --- |
| `kind` | `prerequisite`（假设满足）/ `command`（要执行）/ `expected` / `note` | 否 |
| `doc_quote` | 文档**原文**逐字，含命令、路径、期望 → `--doc-quote` | 是 |
| `command` | `command` 类：**逐字**抄，含笔误，不改、不规整化 → `--cmd` | 是 |
| `cwd` | 文档指明的工作目录 → `--cwd` | 是 |
| `expected` | 文档声称这步应得到的输出 / 产物 / 现象 → `--expected` | 是 |

**cwd 缺失怎么办**：`run_step` 必须给 `--cwd`。文档**没指明**该命令在哪跑、且前一步也没
隐含 cwd → 这步判 `DOC_AMBIGUOUS` 卡住即停（开发者就是不知道在哪敲）。
**不要**替文档猜一个目录硬跑。仅当文档或上一步明确隐含了 cwd（如「进入仓根后」）才沿用。

## P2 — 记一次 meta

两趟子目录隔离（`<repo>/faithful` 与 `<repo>/explored`），跑探索趟时再为它记一次：

```bash
<python> <skill>/scripts/run_step.py --repo <repo>/quickstart/faithful --meta \
  --doc <文档相对仓根路径> --prereq 'CANN 已装' --prereq '<文档声明的其它前提>'
```

## P3 — 忠实趟：执行 + 判定

**判上一步和跑下一步合成一次调用**（`--prev-verdict`），别拆成两次——逐步两次往返要
重放整个上下文，而判定和执行本来就在同一个决策点上。

```bash
# 第 1 步：没有上一步可判，直接执行
<python> <skill>/scripts/run_step.py --repo <repo>/quickstart/faithful --idx 1 \
  --cwd <doc 指明的目录> --cmd '<逐字命令>' \
  --doc-quote '<文档原文>' --expected '<文档声称的期望>'

# 第 n 步（n≥2）：先给第 n-1 步落判定，再执行本步——一次调用
<python> <skill>/scripts/run_step.py --repo <repo>/quickstart/faithful --idx <n> --cwd <dir> \
  --prev-verdict <上一步 verdict> [--prev-defect '<缺什么/错在哪>'] \
  [--prev-fix '<文档应补/改成什么>'] \
  --cmd '<逐字命令>' --doc-quote '<文档原文>' --expected '<期望>'

# 最后一步没有"下一步"可搭车，单独判定收尾
<python> <skill>/scripts/run_step.py --repo <repo>/quickstart/faithful --idx <n> --judge \
  --verdict <verdict> [--defect '...'] [--fix '...']
```

`--prev-verdict` 默认作用于 `idx-1`，要指定别的步用 `--prev-idx`。
**脚本内置卡住即停闸门**：上一步被判成 `FAIL` / `DOC_AMBIGUOUS` / `DOC_MISSING` 时，
判定照常落盘但本步不会被执行，直接提示收尾——合并调用不会漏掉纪律。
指向不存在的步 → 退出码 1 且不执行（台账不能缺条）。

| 判定 | 条件 |
| --- | --- |
| `OK` | 退出码符合文档隐含期望，且（若文档给了期望输出）输出命中期望 |
| `FAIL` | 退出码 ≠ 0，或输出与文档声称的期望矛盾 |
| `DOC_AMBIGUOUS` | 文档没说清这步怎么做 / cwd / 参数，无法确定地执行 |
| `DOC_MISSING` | 执行到这需要一个文档没写的前置动作才能继续 |

遇到第一个 blocker → **立即停**（开发者就是会在这卡住），记下卡点，出忠实报告。

## P4 — 探索趟：带累积修复重跑

用 `--repo <repo>/quickstart/explored`，先为它记一次 meta，再从头带累积修复重跑。
对忠实趟暴露的每个缺陷，注入它自己写的「修订建议」，用 `--prev-injected-fix` 如实记进该步：

```bash
<python> <skill>/scripts/run_step.py --repo <repo>/quickstart/explored --idx <n> --cwd <dir> \
  --prev-verdict <上一步 verdict> \
  --prev-injected-fix '<上一步注入了什么：source / soc / proxy…>' \
  [--prev-defect ...] [--prev-fix ...] \
  --doc-quote '[假设文档补了 X] <文档原命令>' --cmd '<注入修复 && 文档原命令>'
```

某步失败 → 注入修复后**重跑同一步**：`--idx <n> --prev-idx <n> --prev-verdict FAIL …`。
`--prev-idx` 与 `--idx` 相同时闸门放行——这是正当续跑，不是跳过卡点。

**终止条件（任一）**：① 全流程跑通；② 碰到**非文档原因**（机器 / 运行时，如 NPU
`aclrtSetDevice` 错）→ 判 `FAIL`、`--defect` 注明「非文档缺陷」、收尾；
③ 某文档缺陷找不到有据的修复 → 止于该步。**最终跑不通也没关系，可提前结束。**

## 报告的必备模块

| 模块 | 忠实趟（默认必出） | 探索趟（跑了才出） |
| --- | --- | --- |
| 总评 | 能否纯按文档跑通 / 卡在第 N 步 | 补齐 K 个坑后能否跑通 / 止于第 N 步 + 注入修复数 |
| 逐步台账 | 每步原文 / 命令 / 退出码 / 判定 + 链 `steps/<idx>.*.log` | 同上 **+「注入修复」列** |
| 缺陷清单 | 每条 现象 / 原文 / 缺什么 / **修订建议** / 真实摘录 | 同上 **+ 本趟实际注入了什么、应用后是否通过** |
| 卡点 / 终止详情 | 停在第 N 步 + 真实报错 | 止于第 N 步 + 是否文档之过（非文档原因要点明） |

失败与输出**必须是真实执行日志**；推断项标 `(推断)`，与真实摘录严格区分。
