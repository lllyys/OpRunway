# decisions.json 记录格式

任务书拍板记录的唯一载体，调度器 `next_questions.py` 按骨架 key 读取。本文定义
记录字段、两个保留 key、有效拍板判据、失效规则与总审文件契约。

## 记录字段

顶层结构 `{"<骨架key>": {...}}`。字段全部增量可选，旧记录只含 `human_reply` 仍合法：

| 字段 | 含义 |
| --- | --- |
| `human_reply` | 构成有效拍板的答复原话，逐字保存。非空即视为该项已答 |
| `note` | 不构成拍板的答复原话（「暂不决定」类），逐字保存，不使该项完成 |
| `source` | 答复来源：`intake`、`review` 或 `batch` |
| `confirmed_content` | 被确认的内容文本。`2.3.signature` 与 `2.1.baseline` 必须存代码形态的签名文本，散文不入 |

## 有效拍板

明确的准、改或给值才写 `human_reply`；「还不知道」「暂不决定」类答复写 `note`，
该项保持待问。

## 保留 key

带下划线前缀，调度器按骨架 key 取记录，读不到它们：

- `_premises.random_operator`：随机算子前提，
  `{"value": "random" 或 "nonrandom", "human_reply": "<原话>"}`。
- `_superseded`：失效记录列表，元素为 `{"key", "record", "reason", "ts"}`。

## 失效规则

已确认的上游内容发生变化——`human_reply` 或 `confirmed_content` 任一变，含原话
相同而内容变——按下表沿闭包传播（间接依赖自动覆盖）：受影响 key 的整条记录移入
`_superseded` 并从原位删除，回到待问，下轮总审重新生成、重新确认。

| 上游 | 失效的下游 |
| --- | --- |
| `2.2.project_mode` | `2.3.signature`、`3.5.tooling` |
| `2.1.baseline` | `2.3.signature`、`2.4.*` 全部、`3.5.param_mapping` |
| `2.3.signature` | `2.4.param_name`、`2.4.direction`、`2.4.description`、`3.5.param_mapping` |
| `2.4.dtype` | `3.2.threshold_table` |
| `2.4.value_range` | `3.5.generation_rules` |
| `_premises.random_operator` | `3.2.random_strategy` |

## 总审文件

每轮总审落 `evidence/review-round-<N>.md`，节结构固定——`## <骨架key>` 标题、
一行 detail（名称 · §章节 · 不写会怎样），然后恰一个 `candidate` 围栏块装实际
候选内容：

    ## 2.3.signature
    接口定义 · §2.3 · 开发者按错误签名实现
    ```candidate
    aclnnStatus aclnnFoo(const aclTensor *x, aclTensor *out)
    ```

机器只认围栏块：信号扫描与 `confirmed_content` 的复制只取块内内容，detail 不进
解析。整体同意仅覆盖本轮文件的围栏块内容；文件按轮归档，不复用。

**呈现前对账（顺序固定）**：候选成文 → 以该文件跑
`next_questions.py --candidates <文件> --json` → 核对 pending 集合与文件节集合
一致（含被候选文本新触发的条件项）→ 不一致则补节重跑 → 才呈现给人。
跳过对账会让条件项漏出本轮、多出一轮追问。

## 签名解析接口

`_review_signature.parse_signature(text)` 返回
`{"status": "complete" | "incomplete" | "multiple", "params": [参数名...]}`。
白名单：恰一个 Python `def` 或恰一个 C 原型。判 `complete` 须括号配平、每个
参数位解析为合法标识符、无省略号与占位符；重名参数判 `incomplete`；两个及以上
签名判 `multiple`。两侧 `complete` 且参数名序列逐位相等判一致，存在差异判
不一致；任一侧非 `complete` 或缺 `confirmed_content` 判无法判断。
