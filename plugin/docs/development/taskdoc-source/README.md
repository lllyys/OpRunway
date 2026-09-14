# 任务书素材与修复对照

## 这里放什么

- `origin/` —— 上游素材的只读拷贝，来源
  `task-docs/community_task_docs/`，拷贝日期 2026-08-18。源不在本仓，不做自动同步。
- `defect-map.md` —— 逐条缺陷与对应判据，判据的追溯依据。
- `repair-log.md` —— 修复决策与理由。

## 修复版在哪

修复后的模板与黄金样例只有一份，在 skill 的 references 下：

- `../../../skill/repo-task-doc-write/references/task-doc-template.md`
- `../../../skill/repo-task-doc-write/references/golden-task-doc.md`

这里不放副本。两份内容相同的文件迟早会漂。

## 只读纪律

`origin/` 下发现缺陷不要就地修。在 `defect-map.md` 记一条，修复产物另存到
references。这样「上游原样是什么」和「我们认为该是什么」永远分得开，
上游改版时能逐条重放。
