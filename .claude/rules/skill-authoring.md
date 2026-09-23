---
paths:
  - "plugin/skill/*/SKILL.md"
  - "plugin/skill/*/references/*.md"
---

# skill 编写规则

移植自上游 `repo-task-atk-test`（基线 13b3f88）。上游仓根即本仓 `plugin/`：文中 `skill/`、
`docs/` 对应本仓 `plugin/skill/`、`plugin/docs/`。

适用于 `skill/<name>/SKILL.md` 与 `references/*.md`。中文句子怎么写见 `.claude/rules/zh-writing.md`。
规则出处与标定数据见 `docs/development/skill-authoring-basis.md`，只在修改规则时读。

完成标准：

```bash
python3 .claude/hooks/doc_style_lint.py <改过的文件>   # 退 0
python3 .claude/hooks/skill_budget_lint.py             # 退 0
```

## 规则

「机检」列：是 = 命中退 1；报数 = 只打印计数；否 = 靠评测与复审。

| 编号 | 规则 | 正例 | 机检 |
| --- | --- | --- | --- |
| SA-01 | `name` 与目录同名；小写字母、数字、单个连字符；不超过 64 字符 | `repo-task-case-gen` | 是 |
| SA-02 | `description` 用第三人称写做什么与何时用，不超过 1024 字符。与别的 skill 有混淆风险时才写「……时改用 X」 | `按任务书生成 ATK 用例……当用户给出任务书时使用` | 长度是 |
| SA-03 | `SKILL.md` 按骨架写，没有内容的章节不写 | 见下方「骨架」 | 否 |
| SA-04 | `SKILL.md` 不超过 12 KB；`SKILL.md` 加某一阶段链出的 references 不超过 32 KB | — | 是 |
| SA-05 | references 全部从 `SKILL.md` 直接链出，只有一级；没有未链出的文件；引用的章节与锚点存在 | `[plugin-authoring.md](references/plugin-authoring.md)` | 是 |
| SA-06 | reference 超过 100 行时，开头列出各节 | — | 报数 |
| SA-07 | 链接放在用得上的那一步。执行到这一行时不读就会失败，才在这一行链出 | S2 才用的 reference 在 S2 那一行链出 | 否 |
| SA-08 | 只写 Claude 不知道的内容：本仓约定、退出码含义、ATK 某版本的实测行为。torch、aclnn、精度比对、NPU 编译部署不解释 | — | 否 |
| SA-09 | 每个阶段有可检查的完成条件：命令退出码、文件存在、字段取值 | `check_facts.py` 退 0 | 否 |
| SA-10 | 失败情况写成检查表，三列：条件 / 表现 / 处理方式 | 见下方「内容放置」 | 否 |
| SA-11 | 每类内容放在固定位置 | 见下方「内容放置」 | 否 |
| SA-12 | 不引用「脚本名:行号」。写清脚本是执行还是阅读，默认执行 | 运行 `probe_env.py -o env.gen.json` | 是 |
| SA-13 | 给一个默认做法，再写例外条件；不并列多个选项 | 默认走 S2/S3；拿不到接口原型时走 S2′ | 否 |
| SA-14 | 需要说明原因的指令，原因写成一个分句，放进表格的「原因」列 | — | 否 |
| SA-15 | 技术事实只来自 ATK 源码、CANN 官方文档、任务书、工程公开接口、用户提供五处；未验证的标「未验证」 | — | 否 |
| SA-16 | 不写机器地址、账号、绝对路径、CANN 版本；运行时探测或询问 | — | 否 |
| SA-17 | 命令、路径、参数、退出码、输出模板写成固定值 | `probe_env.py` 退 2 时输出 `阻塞·未生成 @S0` | 否 |

## 骨架

````markdown
---
name: <与目录同名>
description: <做什么 + 何时用>
---

# <skill 名>

## 入口参数      表格：参数名 | 含义 | 取值约束 | 初值推断
## 前置检查      有强制步骤才写，标明不可跳过
## 主流程        阶段表；长流程拆进 references/
## 参考资料      链接清单
````

## 内容放置

| 内容 | 位置 | 反例 |
| --- | --- | --- |
| 脚本做了什么 | 脚本自己的输出与报错 | 命令后跟一段「它先做 X，再把结果写进 Y」 |
| 为什么这么定 | 表格的「原因」列，或 references | 指令后追一句因果 |
| 并列的约束或条件 | 列表 | 用顿号串成一句 |
| 失败条件与处理方式 | 检查表：条件 / 表现 / 处理方式 | 散落各处的「……时回 S2」 |
| 阶段的输入输出 | 阶段表 | 段落叙述 |
| 某个算子运行得到的事实、开发过程 | skill 的 `CLAUDE.md` 或 `docs/development/` | 写在 references 里的「实测见过一份……」 |
| 可照抄的模板与样例 | `assets/` | 贴在 `SKILL.md` 里的整份 YAML |

一条命令后面只跟检查表或下一步。

## 目录

| 目录 | 放什么 |
| --- | --- |
| `references/` | 按需读取的通用知识。遮住算子名后句子仍成立才放这里 |
| `scripts/` | 可执行的检查与生成脚本 |
| `assets/` | 模板与样例 |
