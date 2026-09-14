# Repo Task ATK 验收 Skill 精简与鲁棒性改造设计

## 1. 目标

- 精简 `skill/repo-task-atk-test/SKILL.md`。
- 精简并重组 `references/`。
- 清理脚本中的长篇说明。
- 消除重复的数据处理逻辑。
- 集中脚本共同消费的验收政策。
- 修复会改变验收结论的脚本缺陷。
- 保持精度和性能跑测的最小闭环。
- 让零上下文 Agent 可以独立完成验收。

## 2. 非目标

- 不重写 ATK。
- 不修改 `atk/` 源码。
- 不建设统一的大型 CLI 框架。
- 不读取待测算子源码设计覆盖。
- 不读取待测算子源码补充归因。
- 不保留现有 reference 文件名兼容性。
- 不保留现有脚本内部 API 兼容性。
- 不为历史事故保留说明性文字。

## 3. 当前问题

### 3.1 文档问题

- `SKILL.md` 有 768 行。
- 主 Skill 超过 skill-creator 建议的 500 行上限。
- 12 个 reference 共 3553 行。
- 主 Skill 重复了 reference 中的命令、模板和原理。
- 多个 reference 同时承担规则、示例、故障说明和历史复盘。
- reference 之间存在循环引用。
- 第 3 步要求一次读取约 1520 行 reference。
- 主 Skill 存在多观点长句。
- reference 存在多观点长句。

### 3.2 脚本问题

- 9 个脚本共 2917 行。
- docstring 占 530 行。
- 模块级说明包含历史事故和实现复盘。
- 公共用例解析逻辑被多次复制。
- 接口模式规则被多次复制。
- 错误归因规则同时存在于代码和文档。
- 当前脚本没有回归测试。

### 3.3 已确认的功能缺陷

- 无效标杆可能导致通过率超过 100%。
- 无效标杆可能导致失败数为负数。
- 指定输出不存在时可能被空集合判为通过。
- 精度标准检查只读取首个匹配用例。
- 精度裁决未强制验证报告任务类型。
- 接口后端证据为空时可能通过门禁。
- 性能抽样数可能超过目标数量。
- 复合输入类型未被所有脚本一致处理。
- 不同批次的产物可能被错误组合。
- 非有限输入会被启发式移出通过率分母。

## 4. 设计原则

### 4.1 主 Skill 原则

以下原则必须保留在 `SKILL.md`：

1. 不修改待测工程和编译产物。
2. 不在验收执行期间修改验收脚本。
3. 约束只来自任务书、基线接口和生态精度标准。
4. 推断项必须交给用户确认。
5. 不根据待测实现设计覆盖范围。
6. 不根据待测实现修正输入域。
7. 不阅读待测源码补充精度归因。
8. 数字、通过率和结论由确定性脚本生成。
9. 部署阻塞时停止执行并输出阻塞结论。
10. 有效跑测结果只根据报告字段和报错信息归因。

这些原则从流程开始时生效。

这些原则不能依赖 reference 的条件加载。

### 4.2 信息放置原则

- 全流程行为约束放在 `SKILL.md`。
- 阶段性知识放在对应 reference。
- 多脚本共享的声明式政策放在 JSON。
- 算法和格式适配留在 Python。
- 历史事故和过程叙述直接删除。
- 同一事实只保留一个权威位置。
- Python 不解析 Markdown 规则。

### 4.3 行文原则

- 一行表达一个观点。
- 一个句子表达一个判断。
- 不用分号串联独立规则。
- 不用括号承载第二层论述。
- 先写动作。
- 再写必要条件。
- 必要理由紧跟对应规则。
- 删除不影响执行的解释。
- 示例只保留最小可执行内容。
- 表格单元格不写复合段落。

## 5. 主 Skill 结构

主 Skill 参考 `atk-operator-onboarding/references/workflow.md` 的节奏。

主 Skill 使用线性流程：

1. 受理任务书和待测工程。
2. 建立验收约束表。
3. 探测本地或远端环境。
4. 生成 YAML、插件和必测组合。
5. 生成并校验用例集。
6. 完成资源预检。
7. 固化运行清单。
8. 执行精度测试。
9. 在精度通过后执行性能测试。
10. 解析结果并生成裁决。
11. 输出报告和复现包。

每一步固定包含以下内容：

- 输入。
- 动作。
- 产物。
- 停止条件。
- 需要读取的 reference。

主 Skill 不包含以下内容：

- 完整 schema。
- 完整插件模板。
- 完整报告模板。
- 历史事故。
- ATK 源码分析过程。
- 重复的命令变体。
- 重复的失败归因表。

`SKILL.md` 目标为 300 至 400 行。

`SKILL.md` 不得超过 500 行。

## 6. Reference 结构

### 6.1 目标文件

```text
references/
├── intake.md
├── case-design.md
├── yaml-schema.md
├── plugin-authoring.md
├── execution.md
├── performance.md
├── reporting.md
├── experimental-standard.md
└── acceptance-policy.json
```

### 6.2 文件职责

`intake.md` 保存约束来源和确认表。

`intake.md` 保存接口模式和基线接口的确认方法。

`case-design.md` 保存覆盖维度设计方法。

`case-design.md` 保存不可行组合的声明规则。

`yaml-schema.md` 保存 YAML 字段契约。

`plugin-authoring.md` 保存生成器和执行插件契约。

`plugin-authoring.md` 保存最小插件模板。

`execution.md` 保存环境、构建、部署和跑测命令。

`execution.md` 保存常见运行错误的最小处置。

`performance.md` 保存性能样本和比较规则。

`reporting.md` 保存结果结构和报告格式。

`reporting.md` 保存客观归因字段的解释。

`experimental-standard.md` 保留生态精度标准镜像。

`acceptance-policy.json` 保存脚本共同消费的验收政策。

### 6.3 迁移关系

- 合并 `acceptance-constraints.md` 和 `interface-mode.md` 到 `intake.md`。
- 精简 `coverage-design.md` 为 `case-design.md`。
- 保留并精简 `yaml-schema.md`。
- 合并 `api-binding-contract.md` 到 `plugin-authoring.md`。
- 合并 `atk_user_guide.md`、`build-and-deploy.md` 和 `troubleshooting.md` 到 `execution.md`。
- 保留并精简 `performance-acceptance.md` 为 `performance.md`。
- 精简 `verdict-and-report-schema.md` 为 `reporting.md`。
- 保留 `experimental-standard.md` 的标准正文。

### 6.4 Reference 约束

- 每个 reference 只承担一个阶段职责。
- 每个 reference 由 `SKILL.md` 直接链接。
- reference 之间不形成阅读依赖链。
- reference 原则上不超过 300 行。
- 超过 100 行的 reference 提供目录。
- reference 不重复主 Skill 的全局原则。
- reference 不重复 `acceptance-policy.json` 的政策表。

## 7. 验收政策文件

新增 `references/acceptance-policy.json`。

该文件只保存声明式政策。

建议结构如下：

```json
{
  "schema_version": 1,
  "interface_modes": {},
  "accuracy": {
    "allowed_comparators": [],
    "forbidden_override_prefixes": []
  },
  "failure_attribution": [],
  "conclusion_causes": []
}
```

`interface_modes` 定义模式、后端和 YAML 必填字段。

`accuracy` 定义允许的比较器。

`accuracy` 定义禁止私自覆盖的阈值字段。

`failure_attribution` 定义有序错误特征。

`failure_attribution` 定义归因类别和提示。

`conclusion_causes` 定义影响最终结论的类别。

脚本启动时校验政策文件。

政策缺失时拒绝执行相关裁决。

政策字段错误时拒绝执行相关裁决。

manifest 记录政策版本。

manifest 记录政策文件摘要。

## 8. 脚本结构

### 8.1 保留的阶段 CLI

- `probe_env.py`
- `check_coverage.py`
- `validate_cases.py`
- `preflight_cases.py`
- `make_manifest.py`
- `select_perf_cases.py`
- `parse_atk_report.py`
- `verdict.py`
- `make_repro.py`

不为减少文件数量而强行合并 CLI。

每个 CLI 保持单一阶段职责。

### 8.2 新增的内部模块

- `_case_utils.py`
- `_policy.py`
- `_report_reader.py`

`_case_utils.py` 统一复合输入解析。

`_case_utils.py` 统一属性提取。

`_case_utils.py` 统一 shape 和 numel 计算。

`_case_utils.py` 统一用例签名生成。

`_policy.py` 负责加载政策文件。

`_policy.py` 负责校验政策文件。

`_report_reader.py` 隔离 Excel 列名和节点解析。

内部模块不提供用户命令。

### 8.3 脚本文字约束

模块 docstring 只保留以下内容：

- 用途。
- 输入。
- 输出。
- 退出码。

函数 docstring 只解释非直观契约。

局部注释只解释不可见约束。

删除历史事故。

删除耗时复盘。

删除长篇处置建议。

删除易漂移的 ATK 源码行号。

### 8.4 不外置的实现内容

- Excel 列名和别名解析。
- ATK 报告格式适配。
- dtype 字节数计算。
- shape 和广播计算。
- 用例投影算法。
- 笛卡尔积核对算法。
- 性能均匀抽样算法。
- 文件读取和参数解析。
- 退出码处理。

这些内容属于实现。

这些内容不属于验收政策。

## 9. 数据流

```text
约束表
→ must_cover.json
→ cases.json
→ validation.json
→ preflight.json
→ manifest.json
→ accuracy_results.json
→ performance_results.json
→ verdict.json
→ report.md
→ repro 包
```

每个阶段只消费声明的上游产物。

manifest 记录 YAML 摘要。

manifest 记录插件摘要。

manifest 记录必测集摘要。

manifest 记录用例集摘要。

manifest 记录政策摘要。

解析结果记录原始报告摘要。

解析结果记录用例集摘要。

裁决前核对所有摘要。

摘要不一致时拒绝裁决。

## 10. 精度和归因边界

- 所有实际执行用例都进入通过率。
- 非有限输入只记录事实标签。
- 解析器不得擅自改变通过率分母。
- 解析器不得擅自改变通过率分子。
- 精度报告必须声明任务类型为 `accuracy`。
- 指定输出必须在实际输出集合中存在。
- 所有用例必须使用一致的精度标准。
- 精度标准缺失时拒绝裁决。
- 后端证据缺失时拒绝裁决。
- 失败归因只使用结果字段和报错文本。
- 证据不足时使用 `unknown`。
- 不通过阅读待测源码补充归因。
- 报告客观呈现精度通过率。
- 报告分开展示归因统计。

## 11. 错误处理

统一退出码：

- `0` 表示完成且检查通过。
- `2` 表示发现确定性问题。
- `3` 表示环境或信息不足。

统一错误格式：

```text
[RULE_ID] 问题
证据：实际值
动作：下一步操作
```

每条错误只说明一个问题。

错误信息不包含历史案例。

错误信息不包含长篇原理。

## 12. 验证设计

使用 Python 标准库 `unittest`。

测试不依赖第三方测试框架。

新增以下测试目录：

```text
tests/
├── test_case_utils.py
├── test_policy.py
├── test_report_parser.py
├── test_perf_selection.py
├── test_verdict.py
└── test_document_style.py
```

### 12.1 功能回归

- 验证通过率不超过 100%。
- 验证失败数不为负数。
- 验证指定输出必须存在。
- 验证所有用例使用一致精度标准。
- 验证精度裁决拒绝性能报告。
- 验证接口后端证据不能为空。
- 验证性能抽样数不超过目标值。
- 验证复合输入类型被一致解析。
- 验证非有限输入保留在通过率分母。
- 验证跨产物摘要不一致时拒绝裁决。
- 验证政策缺失时拒绝裁决。
- 验证政策格式错误时拒绝裁决。

### 12.2 文本门禁

- 检查 `SKILL.md` 不超过 500 行。
- 检查异常长的正文行。
- 检查正文行是否堆叠多个完整句。
- 排除代码块。
- 排除表格。
- 排除纯链接行。
- 对合理例外进行显式登记。

文本门禁只负责提示结构退化。

人工复核负责判断语义是否精简。

### 12.3 验证顺序

1. 运行单元测试。
2. 运行文本门禁。
3. 运行 skill-creator 的 `quick_validate.py`。
4. 运行最小端到端样例。
5. 核对 reference 路由。
6. 核对文档命令与 argparse。

## 13. CLAUDE.md 一致性

只更新本次改造造成的失效信息。

更新已重命名的 reference 路径。

更新脚本和 reference 数量。

修正 `make_repro.py` 未实现的陈旧状态。

不在本次重写整个 `CLAUDE.md`。

## 14. 完成标准

- 主 Skill 保持 300 至 400 行。
- 主 Skill 不超过 500 行。
- 主 Skill 保留全部全局原则。
- 主 Skill 不包含长篇事故叙述。
- reference 不存在循环阅读依赖。
- reference 不重复全局原则。
- 脚本不包含长篇历史说明。
- 共同政策只存在于 `acceptance-policy.json`。
- 共同数据处理只存在于内部模块。
- 已确认的功能缺陷全部有回归测试。
- 全部单元测试通过。
- 文本门禁通过。
- `quick_validate.py` 通过。
- 最小端到端样例通过。
- 精度报告只呈现客观通过率和报错归因。
- 验收流程不要求阅读待测算子源码。
