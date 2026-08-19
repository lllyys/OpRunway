---
name: repo-task-doc-write
description: 当需要为社区算子开发任务撰写任务书、检查已有任务书是否合规、或把需求方的口头要求整理成可验收的开发约束时使用。
---

# 社区算子任务书撰写

## 工作边界

任务书是两份契约合成的一份文档：

- 对**外部开发者**，它划定开发范围、实现约束、交付件、自验方法与提交位置
- 对**验收 agent**，它是全部验收约束的唯一来源

一条信息可能只服务其中一个受众，比如 §6 参考资料只给开发者看。
但两个受众都不满足的内容不该出现在任务书里。

**开发约束由需求方提供，不由 agent 决定。** agent 可以基于对标接口给出候选
（这个算子大概支持哪些 dtype、attr 该长什么样、失败场景怎么定义），
但每一条都要人判断是准了、缺了还是多了，答复原话记进 `decisions.json`。

任务书撰写发生在算子开发之前，**不依赖真机或 torch**。agent 给出的候选只能
来自模型知识或公开文档，没有一条能在本机实证——所以推断项的证据强度天然弱，
必须人拍板，这不是流程负担而是唯一可靠的把关点。

## 五个阶段

| 阶段 | 做什么 | 出口 |
| --- | --- | --- |
| T1 受理 | 收算子名与已有材料，判定工程模式、对标基线、是否随机算子 | 前提三项已拍板 |
| T2 推断 | 基于对标接口给出候选：签名、dtype 面、值域、错误场景、实现约束 | 候选成文，标注 provenance |
| T3 拍板 | 按批次追问，答复原话入 `decisions.json` | `next_questions.py` 报 done |
| T4 落稿 | 渲染脚手架，按 decisions 填内容 | md 全部章节非空 |
| T5 质量门 | `check_taskdoc.py` 跑到退出码 0 | 封条 sha256 匹配交付的 md |

## 命令

```bash
# T3：看下一批该问什么
python3 scripts/next_questions.py --decisions evidence/decisions.json --doc <md>

# T4：一次性渲染脚手架
python3 scripts/make_taskdoc.py --op <算子名> --out <工作目录>/<Op>_task_doc.md

# T5：质量门，退出码 0 才算过
python3 scripts/check_taskdoc.py --doc <md> \
    --decisions evidence/decisions.json --evidence-dir evidence/
```

## 交付硬要求

交付前必须贴出 `check_taskdoc.py` 的完整输出，含退出码。

`evidence/gate_pass.json` 的 `sha256` 必须等于交付的 md 的 sha256。
跑完门禁又改了稿，这两个值就对不上——**没有匹配的封条就是没过门**。

## 写作参考

`references/golden-task-doc.md` 是一份过了全部五层门禁的任务书。
写不确定某一节该写到什么程度时看它，不要看别处的任务书——
别处的没有经过本门禁。

`references/task-doc-template.md` 是章节骨架与每一列的填写判据。

## 判据在哪

模糊词表与替代写法在 `references/vague-words.json`，
枚举列的取值域在 `references/dtype-vocab.json`。

被门禁拦下时先读报错——每条报错都带「不写会怎样」和替代写法，
不要去改判据让它通过。

`references/manual-checklist.md` 是十四条人工检查项里仍需人读的那五条，
交付前逐条核对。
