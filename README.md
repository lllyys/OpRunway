# OpRunway

社区算子任务 skills 的开发、分析与验收工作区。项目维护上游
[repo-task-atk-test](https://gitcode.com/Justbin/repo-task-atk-test) 镜像，
并独立维护 OpRunway 自有 skills。

## 目录与维护范围

| 目录 | 用途 |
| --- | --- |
| `plugin/skill/` | 上游 skills 镜像及本项目开发改动 |
| `plugin/.claude-plugin/` | 上游兼容 manifest 与镜像来源记录 |
| `.agents/skills/` | Codex 项目入口；上游 skills 使用链接，自有 skills 使用实体目录 |
| `repos/repo-task-atk-test/` | 上游完整参考 clone，由 Git 忽略，不直接开发 |
| `reports/` | 本地分析报告、交付产物及证据，由 Git 忽略 |
| `dev-doc/` | 开发摘要与待办 |
| `archive/` | 历史材料 |

上游镜像来源见 [upstream.json](plugin/.claude-plugin/upstream.json)。
参考 clone 的更新与 `plugin/` 镜像同步分别进行，分析报告需注明实际读取的版本。

## 社区任务 skill 分析

使用项目自有的
[community-task-skill-analysis](.agents/skills/community-task-skill-analysis/SKILL.md)
分析任务 skills 的机制，输出带 Mermaid 流程图的 Markdown。
分析按实际职责、输入输出约定和调用关系分组，适用于已有及新增 skills。

分析覆盖八个方面：职责边界、逐 skill 输入输出、任务包文件职责、用例覆盖、
精度判断、性能采集与判断、开发者工程调用链，以及
casegen（用例或任务包生成）—开发者—accept（验收）闭环。
详细要求见[分析标准](.agents/skills/community-task-skill-analysis/references/analysis-standard.md)。

在 Codex 中提供分析对象与输出位置，例如：

```text
$community-task-skill-analysis
分析 repos/repo-task-atk-test/ 中社区任务相关的 casegen 和 accept skills，
按八项标准整理，输出到 reports/community-task-analysis/analysis.md。
```

默认静态分析，区分现行机制、已验证能力、待验证边界及维护建议。
该 skill 的实体文件由 OpRunway 维护，与 Justbin 镜像和参考仓隔离，不随上游切片导出。

## 用例生成与验收

按任务接口和交付形式选择对应的用例生成及验收 skill，入口见 `.agents/skills/`。
任务包生成、构建、测试与验收在远程 NPU 环境执行；本机用于编辑、Git 和只读探测。
ATK、CANN 与 Python 依赖属于环境前置。

正式验收使用[隔离验收入口](.agents/skills/isolated-acceptance/SKILL.md)，
在仓外加载指定发布副本。分析报告、自测成功和局部验证分别说明范围，
正式验收结论依据验收 skill 的检查结果与完整证据。

## Codex 项目入口

实施、审核与验收使用 Codex，工作规则见 [AGENTS.md](AGENTS.md)。
文档检查和审核规则位于 `.codex/rules/`，配置按项目维护。
开发摘要见 [oprunway-changes-brief.md](dev-doc/oprunway-changes-brief.md)，
待办见 [oprunway-todo.md](dev-doc/oprunway-todo.md)。
