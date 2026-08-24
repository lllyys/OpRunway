# 交接包规范

## 脚本归属

每个脚本的归属记在 `artifact-contracts.json` 顶层的 `scripts` 表中，含义如下：

- `case-gen` 只供生成侧使用。
- `acceptance` 只供验收侧使用。
- `shared` 供两侧共同使用。

`build_skills.py` 只供维护者把嵌套源展开成独立安装目录，不在任一验收阶段调用。
交接包是生成侧完成 S2 后交给验收侧的完整工作目录。封印是生成侧为目录内文件记录 SHA256
清单，接收是验收侧重算摘要并核对任务书、ATK 版本与接口声明。

验收侧必须复制交接包后工作。它可以在副本里追加 S0 及后续阶段的证据，但不得改名、
搬动或重新生成任何 S2 产物。

## 目录

- 交接包目录
- 字段来源
- 排除名单

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
`baseline_api`、`baseline_kind`、`execution_backend`、`baseline_backend` 与 `mode_source`，
清单不得自行推导第二套接口决定。

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
