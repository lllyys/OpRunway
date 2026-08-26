# 用例包契约

**改 `repo-task-case-gen` 或 `repo-task-atk-accept` 且动到下表任一项时读这份。**
只改单侧内部逻辑、或在开发别的 skill，用不到。

这份不放在任何一个 skill 目录里，因为契约不属于任何一侧：放进生成侧，改
`run_atk.py` 的人看不到；两边各放一份，两份必然漂。

## 使用态不强制串联

契约约束的是开发，不是使用。两个 skill 单独都能用：

- 生成侧吃社区**原样**的任务书，不要求由 `repo-task-doc-write` 产出
- 跑测侧吃任何符合下面结构的目录，不要求由生成侧产出
- 三个 skill 的 `SKILL.md` 里都不许写「先去跑另一个 skill」这种前置

`repo-task-doc-write` 与这两个没有契约，改它不影响它们。

## 用例包结构

`atk-case-<op>/` 是个普通目录，不做 SHA256 封印，不做只读锁：

```text
atk-case-<op>/
├── facts.json           算子事实表，每项带 source
├── <op>.yaml            用例设计
├── <op>_constraint.py   有才放
├── function_<op>.py     有才放
├── cases.json           用例
└── golden/              CPU 标杆输出 + manifest.json
```

## 四项契约

由生成侧定、跑测侧读。**改任一项两侧都会动**：

| 契约项 | 跑测侧谁在读 | 破坏后的表现 |
| --- | --- | --- |
| `cases.json` 这个文件名 | `sample_smoke.py`、`run_atk.py` 隔离复验 | ATK 拿用例文件基名当 golden 子目录名（`atk/tasks/result_process.py:67`），改名后报「标杆输出为空」 |
| `golden/` 目录布局 `<根>/<backend>/<用例文件基名>/<id>/` | `run_atk.py` 的 `accuracy_load` | 同上。**不报错，只是一条都匹配不上** |
| `facts.json` 的 `performance.kind` | `verdict.py:57` 选性能裁决分支 | 取值不在 `none`/`builtin`/`cross_dtype` 里就退回 `none`，性能永远不评级 |
| `function_<op>.py` 这个命名 | `run_atk.py:266` 的 `glob("function_*.py")` | 自动挂不上，CPU 标杆执行器找不到 |

## 改契约的纪律

1. 两份 skill 的 `CLAUDE.md` 都读一遍，确认改动没和某一侧的红线冲突
2. 两侧都在真机上复跑，**不能只跑改动那一侧**
3. 改完更新上表，**并同步运行态那一侧的表述**——四项在运行态各有归属，
   跑 skill 的 agent 读的是它们，不是本文件：

| 契约项 | 运行态在哪讲 |
| --- | --- |
| `cases.json` 文件名 | 跑测侧 `references/run-accuracy.md`、`troubleshooting.md` |
| `golden/` 布局 | 跑测侧 `references/troubleshooting.md` 的自查三步 |
| `performance.kind` | 生成侧 `references/interface-facts.md` 词表 + 跑测侧 `SKILL.md` 裁决表 |
| `function_<op>.py` | 生成侧 `references/plugin-authoring.md` |

**`docs/` 下的文件 agent 跑 skill 时读不到。** 只改本文件不改上面那些位置，
开发者看得懂，执行的 agent 照旧按老规矩来。

第 2 条是硬的：前两项被破坏时跑测侧不报错，只是 golden 一条都匹配不上，
而报错文本指向用例包，不指向你改的那行。只跑单侧看不出任何异常。

## 归因边界

跑测侧报错时先分清归哪一侧，改错侧比不改更糟：

| 现象 | 归哪一侧 |
| --- | --- |
| 参数数量不匹配、dtype 不在 ATK 词表 | 生成侧，改 `<op>.yaml` 与 `facts.json` |
| 「标杆输出为空」 | 契约被破坏，两侧对一遍上表 |
| 精度不符、aicore 异常 | 哪一侧都不是，是被测算子的缺陷，照实报 |

另有两条反向红线，各自写在自己那侧的 `CLAUDE.md` 里，不在这里复制：
生成侧「不为让跑测通过而改窄用例」，跑测侧「不就地 patch 用例包」。
