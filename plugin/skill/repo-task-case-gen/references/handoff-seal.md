# 交接包封印

这页只管交接的生成侧一半：S2 出口如何封印交接包，以及封印清单需要满足哪些门。生成侧在
S2 所有产物和门禁完成后、运行 `seal_bundle.py` 前读取本页。共用的交接包目录树、字段来源与
排除名单在 `handoff.md`。

## 目录

- 封印清单
- 封印门
- 出口门字段表

## 封印清单

`evidence/bundle.json` 是交接包的封印清单。它记录生成条件、任务书指纹、接口选择、
分面入口和文件摘要，不记录 agent 的通过声明。

目录树与清单样例由 `render_views.py` 从 `handoff-contract.json` 渲染，改交接面先改契约。

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
    "baseline_kind": "torch",
    "execution_backend": "npu",
    "baseline_backend": "cpu",
    "mode_source": "任务书 §2.3"
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
