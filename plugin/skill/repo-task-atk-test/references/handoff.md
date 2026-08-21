# 交接包规范

## 脚本归属

每个脚本的归属记在 `artifact-contracts.json` 顶层的 `scripts` 表中，含义如下：

- `case-gen` 只供生成侧使用。
- `acceptance` 只供验收侧使用。
- `shared` 供两侧共同使用。

交接包是生成侧完成 S2 后交给验收侧的完整工作目录。封印是生成侧为目录内文件记录
SHA256 清单，接收是验收侧重算摘要并核对任务书、ATK 版本与接口声明。

验收侧必须复制交接包后工作。它可以在副本里追加 S0 及后续阶段的证据，但不得改名、
搬动或重新生成任何 S2 产物。

## 目录

- 交接包目录
- 封印清单
- 字段来源
- 排除名单
- 封印门
- 出口门字段表
- 接收门
- 任务书一致性
- ATK 版本一致性
- 接口一致性
- 接收结果
- 接线改写后的清单

## 交接包目录

交接包沿用 S2 结束时的工作目录，不另建一套发布布局。`verdict.py`、
`make_repro.py`、`select_perf_cases.py` 与 `save_failed_cases.py` 因此继续读取原路径。

目录至少保留以下结构，方括号表示条件产物：

```text
atk-case-<op>/
├── evidence/
│   ├── constraints.md
│   ├── interface.json
│   ├── env.json
│   ├── env.sh
│   ├── signature_alignment.json
│   ├── signature_contract.json
│   ├── <各量具报告>.json
│   ├── timeline.jsonl
│   └── bundle.json
├── <op>_decl.json
├── <op>_materialize.py
├── must_cover.json
├── <op>_materialized.json
├── <op>.yaml
├── <op>_constraint.py
├── [function_<op>.py]
├── result/<yaml>/json/all_<yaml>.json
└── frozen_<分面>/
```

验收侧把整个目录复制为自己的工作副本，再追加 `evidence/pr_signature.json`、
`evidence/bundle_intake.json`、构建证据、跑测结果与结论。S2 的相对路径一个不改，
跨阶段消费者无需猜新位置或接受路径参数改写。

目录名可以随复制动作变化，清单内的文件键不带顶层目录名。所有文件键都以交接包根为
起点，并使用正斜杠分隔的相对路径。

覆盖、校验、冻结与适配器报告的文件名由各量具的 `-o` 决定，多分面时由调用方选名。
封印不从文件名猜分面，而是按报告内的用例集 SHA256 与实际用例 JSON 配对。

## 封印清单

`evidence/bundle.json` 是交接包的封印清单。它记录生成条件、任务书指纹、接口选择、
分面入口和文件摘要，不记录 agent 的通过声明。

清单的顶层形态如下：

```json
{
  "schema_version": 1,
  "operator": "<op>",
  "sealed_at": "<ISO 8601>",
  "task_doc": {"name": "<文件名>", "sha256": "<全文摘要>"},
  "generator": {"skill": "repo-task-case-gen", "phase_supported": "仅 Phase A"},
  "atk": {"version": "<ATK 版本>", "python": "<解释器>"},
  "interface": {
    "interface_mode": "aclnn",
    "candidate_symbol": "aclnnXxx",
    "baseline_api": "torch.xxx",
    "baseline_kind": "torch"
  },
  "facets": [],
  "files": {},
  "excluded": [
    "evidence/timeline.jsonl",
    "evidence/repro.sh",
    "evidence/bundle.json",
    "evidence/env.json",
    "evidence/env.sh"
  ],
  "ignored_dirs": ["__pycache__"]
}
```

只有 `seal_bundle.py` 可以创建初始清单。脚本成功退出后，任何 S2 量具再次写入都会改变
摘要，验收侧的 `check_bundle.py` 必须把这种变化判为篡改。

## 字段来源

每个字段都有唯一的机械来源，不允许凭记忆补值：

- `schema_version` 由 `seal_bundle.py` 按当前支持的清单版本写入，初始值为 `1`。
- `operator` 取自 `evidence/interface.json` 已确认的算子名。
- `sealed_at` 是封印成功时的 ISO 8601 时间，必须带时区。
- `task_doc.name` 取本次任务书的文件名，不含父目录。
- `task_doc.sha256` 对任务书原始字节全文计算，不做换行或编码归一化。
- `generator.skill` 固定写 `repo-task-case-gen`，表示产物来自生成侧。
- `generator.phase_supported` 取自生成机 `evidence/env.json` 的阶段能力结论。
- `atk.version` 取自 `evidence/env.json` 的 ATK 版本指纹。
- `atk.python` 取自 `evidence/env.json` 的 `selected_python`。
- `interface` 从 `evidence/interface.json` 复制接口模式、待验收接口与基线字段。
- `facets` 从各分面的用例集摘要、报告与已生成文件反推，不由 agent 手列。
- `files` 对排除文件与忽略目录之外的每个普通文件记录相对路径与 SHA256。
- `excluded` 固定记录五个排除路径，不接受调用方追加临时例外。
- `ignored_dirs` 固定记录 `__pycache__`，任何层级同名目录都不进摘要清单。

每个 `facets` 项给出 `name`、`yaml`、`case_json`、`must_cover`、`frozen_dir`、
`coverage_report`、`freeze_report`、`validate_report` 与 `adapter_report`。路径必须在封印
时存在；普通文件路径还必须同时出现在 `files` 中。

`interface` 逐项复制 `evidence/interface.json` 的 `interface_mode`、`candidate_symbol`、
`baseline_api` 与 `baseline_kind`，清单不得自行推导第二套接口决定。

## 排除名单

`files` 覆盖交接包内除五个排除文件与忽略目录外的全部普通文件。目录本身不记摘要，
软链接不是普通文件，不能静默跟随到交接包之外。

五个排除文件及理由如下：

- `evidence/timeline.jsonl` 会在验收侧继续追加阶段记录，不能作为冻结输入。
- `evidence/repro.sh` 会随验收命令继续更新，不能作为生成侧固定产物。
- `evidence/bundle.json` 不能包含自己的摘要，否则清单内容无法收敛。
- `evidence/env.json` 是生成机指纹，验收侧 S0 会重新探测并覆盖。
- `evidence/env.sh` 由现场环境派生，验收侧 S0 会重新生成。

任何层级名为 `__pycache__` 的目录整体忽略。ATK 导入 `<op>_constraint.py` 会生成
含时间戳的 `.pyc`，验收侧再导入一次就会改变它，因此不是冻结输入。

排除名单与忽略目录写进清单，验收侧核对其恰好是上述固定值。两侧不得临时加入
新例外来换取通过。清单已复制 ATK 版本、解释器与 `phase_supported`，排除机器指纹不会丢掉交接前提。

## 封印门

`seal_bundle.py` 是生成侧出口，只在所有 S1、S2 工作完成后运行。它依次执行四步，前一
步失败时不写一份看似完整的清单。

1. 按骨架与进度探测规则核对 S1、S2 产物存在。
2. 按 `interface.json` 与 `signature_alignment.json` 判断条件产物是否应当存在。
3. 核对签名契约与各分面的覆盖、冻结、校验、适配器报告存在且结论通过。
4. 枚举文件、计算 SHA256，并以完整结果写入 `evidence/bundle.json`。

完整性检查先复用进度探测规则核对 S1、S2 登记产物，再按内容配对所有分面报告。
骨架要求的文件缺失要逐项报缺件，条件判据未落盘要说明是哪份判据未定。

退出码约定如下：

- `0`：全部判据通过，清单已经写完。
- `2`：产物不齐、条件产物不符，或任一道 S2 出口门未通过。
- `3`：工作目录结构错误，无法可靠判断或写入清单。

退出码为 `2` 时必须列出缺失、未通过或多出的项目。退出码为 `3` 时不得留下半份新清单；
已有清单也不得被不完整结果覆盖。

## 出口门字段表

分面报告是包含用例集摘要、可按摘要归属到某个 YAML 分面的量具报告。
配对键是报告与用例 JSON 共用的 SHA256 字段，不是文件名或目录名。

| 报告 | 识别键 | 配对与通过条件 |
| --- | --- | --- |
| 覆盖 | `case_file_sha256`, `must_cover_sha256` | 按前者；`missing == []` |
|  |  | `must_cover_hit == must_cover_total` |
| 冻结 | `case_json_sha256`, `frozen_dir`, `inputs` | 按摘要；`unmaterialized_ids == []` |
|  |  | `baseline_failed_cases == []` |
| 冻结常量输入 | 同冻结报告 | `constant_input_cases == {}` |
|  |  | 或 `constant_input_check == "not_applicable"` |
| 用例校验 | `case_file_sha256`, `failures` | 按摘要；`failures == []` |
| 适配器 | `case_file_sha256`, `problems` | 按摘要；`problems == []` |
| 签名契约 | `evidence/signature_contract.json` | 全局；`verdict == "pass"` |
|  |  | `problems == []` |

配对还必须同时满足以下条件：

- 覆盖报告的 `must_cover_sha256` 在交接包根目录的 JSON 文件中恰好命中一份。
- 每个分面恰好配到一份覆盖、冻结、用例校验与适配器报告。
- `frozen_dir` 即使是绝对路径，也能化为交接包根下已存在的相对目录。

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
