# 交接包接收

这页只管交接的验收侧一半：S0 入口如何接收已封印的交接包，并判定能否进入 S3。验收侧在
复制交接包并完成 S0 环境探测后、运行 `check_bundle.py` 前读取本页。共用的交接包目录树、
字段来源与排除名单在 `handoff.md`。

## 目录

- 接收门
- 任务书一致性
- ATK 版本一致性
- 接口一致性
- 接收结果
- 接线改写后的清单

## 接收门

`check_bundle.py` 是验收侧入口。它在交接包副本中运行，接收任务书与验收机环境指纹；
aclnn 模式另接收 PR 的 GetWorkspaceSize 头文件和接口名。

调用形态如下。`-C` 与 `-o` 可省略，分别默认当前目录与
`evidence/bundle_intake.json`：

```text
check_bundle.py [-C <交接包副本>] --task-doc <md> --env <env.json>
  [--header <PR 头文件或目录> --aclnn-name <名>]
  [-o evidence/bundle_intake.json]
```

清单的 `interface.baseline_api` 仍记进接口证据，供后续复现，但不参与 PR 头文件与
任务书声明的机械比对。

脚本一次执行完七步，把能判断的项目全部写入同一份接收结果：

1. 确认 `bundle.json` 存在，并且 `schema_version` 是当前量具认识的版本。
2. 重算 `files` 中每个文件的 SHA256，同时检查清单文件没有缺失。
3. 比较本次任务书全文 SHA256 与 `task_doc.sha256`。
4. 比较验收机 `env.json` 的 ATK 版本与清单里的 `atk.version`。
5. 在 aclnn 前提成立时写出 `evidence/pr_signature.json`，并核对含出参的完整参数声明。
6. 写出 `evidence/bundle_intake.json`，逐项保留结论与证据。
7. 按全部判据汇总退出码，不因第一项失败而跳过其余可执行检查。

退出码约定如下：

- `0`：所有适用判据通过，可以进入 S3。
- `2`：至少一道可判断的判据不满足，结论是阻塞·未验收 @S0。
- `3`：清单缺失、版本不认识，或必需输入无法解析。

完整性、任务书、ATK 版本和接口四项要独立给结论。一个文件被改动不能掩盖任务书也
拿错的事实，验收侧应在一次接收中看到全部可修问题。

新增文件只在以下三类位置判为篡改：

- `frozen_*/` 之下的任何新文件。
- `result/` 之下的任何新文件。
- 根目录新增的 `*.yaml`、`*_decl.json`、`*_constraint.py`、`*_materialize.py`、
  `must_cover*.json`、`*_materialized.json` 或 `function_*.py`。

验收侧可以合法追加 `evidence/*.json`、`conclusion/`、ATK `output/` 与日志，这些位置的新文件
不算篡改。已经记在 `files` 中的文件仍要逐个核对摘要，合法追加规则不会放行对旧文件的修改。

## 任务书一致性

接收侧对用户本次给出的任务书原始字节计算 SHA256，与 `bundle.json` 的
`task_doc.sha256` 比较。只比较文件名、标题或算子名都不构成通过。

摘要不一致时把任务书项判为失败，不重新生成用例，也不在副本里替换清单摘要。解除
条件是找到与交接包绑定的原任务书，或回到生成侧重新产出一份新交接包。

## ATK 版本一致性

验收机先用环境探测量具生成自己的 `env.json`。接收门读取其中的 ATK 版本，与清单的
`atk.version` 做精确比较；不得只比较主版本或凭兼容性猜测放行。

版本不一致时，可表达性能力矩阵的前提已经变化，因此停在 S0。换解释器路径但 ATK
版本相同不触发这道门，解释器信息仍保留在清单里供复现。

## 接口一致性

- PR 头文件只能来自 `--env` 所指 `env.json` 的 `operator_project.path` 工程树，这是来源白名单。
- 路径落在 `/usr/local/Ascend` 或 `env.json` 记录的 `ASCEND_TOOLKIT_HOME` 之下一律拒绝，
  因为那里是官方已发布的同名接口，同名不代表同签名。
- 工程树里找不到声明就停下来问用户，不要换目录找。

接口模式为 aclnn 且工程里有 GetWorkspaceSize 头文件时，接收门必须核对全部业务参数，
包括入参与出参：

- PR 头文件的参数名集合等于任务书签名的参数名集合。
- 两边共有参数的相对顺序一致。
- 每个参数的 C 类型、指针层数与 `const` 一致。

三项任一失败都单列为“PR 接口与任务书不一致”，并在证据中列出缺失、多出、乱序或
类型不同的参数。它属于 PR 偏离任务书，不得与交接包摘要损坏混写，也不得回 S2 改考卷。

接口差异使用四个固定字段名：

- `missing`：任务书签名有、PR 头文件没有的参数名。
- `extra`：PR 头文件有、任务书签名没有的参数名。
- `reordered`：两边共有参数的预期顺序与实际顺序。
- `type_mismatch`：参数名及任务书预期完整 C 类型、PR 实际完整 C 类型；完整类型包含
  指针层数与 `const`。

头文件解析成功后写出 `evidence/pr_signature.json`，形态固定如下：

```json
{
  "source": {"kind": "header", "path": "<实际命中的工程头文件>"},
  "signature": "<PR 声明原文>",
  "parameters": []
}
```

`parameters` 保存同一解析器得到的全部业务参数，不含 ATK 自动补齐的 `workspaceSize` 与
`executor`。旧产物 `evidence/signature_alignment_pr.json` 不再生成。

pytorch 或 kernel 模式没有可比较的 C 头文件。此时 `interface.applicable` 为 `false`，
`interface.passed` 为 `null`，`interface.evidence.reason` 说明接口模式；不适用不计失败。

aclnn 模式下，调用方必须给 `--header` 与 `--aclnn-name`。头文件参数少给任一项时，
接口项写为不适用，`reason` 写明“未给头文件”及所缺内容，整体退出码只由其余适用项决定。
stdout 必须提示 S0 接口一致性门没有执行，进入 S3 前必须补跑；这个临时不适用不能当作
已经通过接口门。

## 接收结果

`evidence/bundle_intake.json` 的顶层字段如下：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `verdict` | 字符串 | 所有适用项通过为 `pass`，否则为 `blocked` |
| `bundle_sha256` | 字符串 | 本次读取的 `evidence/bundle.json` 自身摘要 |
| `checked_at` | 字符串 | 带时区的 ISO 8601 检查时间 |
| `integrity` | 对象 | 封印文件与受保护新增路径的完整性结论 |
| `task_doc` | 对象 | 本次任务书与清单所绑任务书的摘要结论 |
| `atk_version` | 对象 | 验收机与生成侧 ATK 版本结论 |
| `interface` | 对象 | PR 头文件与任务书签名的接口结论，或不适用原因 |
| `evidence.rewires` | 数组 | 清单已有的合法接线改写记录，原样复制 |

四个检查对象都有相同的三个外层字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `passed` | 布尔值或 `null` | 通过、失败；不适用时为 `null` |
| `applicable` | 布尔值 | 本次是否实际执行该判据 |
| `evidence` | 对象 | 本次从文件、输入或子量具得到的证据 |

各检查对象的 `evidence` 字段完整形态如下：

| 检查 | 证据字段 |
| --- | --- |
| `integrity` | `missing`、`mismatched`、`unexpected`、`manifest_problems`、`registered_files` |
| `task_doc` | `path`、`expected_sha256`、`actual_sha256`、`missing` |
| `atk_version` | `expected`、`actual`、`missing` |
| `interface` 适用 | `baseline_api`、`missing`、`extra`、`reordered`、`type_mismatch`、`expected_report`、`actual_report` |
| `interface` 不适用 | `reason` |

头文件来源不合规、声明解析失败或任务书对齐报告缺少声明原文时，`interface` 仍是适用项且
判失败。此时 `evidence` 用 `reason` 保存错误，并保留四类差异字段为空数组。

任一适用项失败，整体结论就是阻塞·未验收 @S0，不进入 S3。接口不一致必须保留独立
分类，使最终报告能区分 PR 偏离任务书、交接包损坏、任务书拿错和环境版本不符。

接收结果是验收侧新证据，不回写生成侧原包。最终 S5 证据链消费副本中的这份文件。

## 接线改写后的清单

`rewire_adapter.py` 是封印后唯一允许修改 S2 文件的通道。它只有在语义不变性检查通过后，
才能改接线字段并同步清单。

改写成功后必须完成以下清单更新：

- 重算被改 YAML、`result/<yaml名>/` 下全部普通文件以及可选冻结报告的 SHA256，更新或
  新增 `files` 中对应的正斜杠相对路径。
- 在 `rewires` 追加一项，字段固定为 `at`、`yaml`、`fields`、`record`、`backup` 与
  `changes`。

`at` 是带时区的 ISO 8601 时间，`fields` 是本次 patch 的键列表。`yaml`、`record` 与
`backup` 都是相对交接包根的路径；`record` 指向 `-o` 留痕，`backup` 指向
`<yaml>.pre_rewire` 原文备份。

`changes` 对每个摘要实际变化或新增的文件记录 `file`、`before` 与 `after`。旧摘要不存在时
`before` 为 `null`；摘要未变的 touched 文件不进 `changes`。留痕与备份不进 `files`，但能从
`rewires` 找回证据链。

清单更新与文件改写必须作为一次成功操作对外呈现。只改文件不改清单会被接收门判为
篡改；只改清单不改文件会留下虚假的合法化记录，两种情况都不得返回 `0`。
