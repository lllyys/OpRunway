# 性能脚本渲染约定

生成侧只负责把性能采集脚本渲染进任务包。**正式验收怎么采、怎么判、怎么消费，权威在
`repo-task-blas-accept` 的 `references/perf-protocol.md`**，本文件不重复其中任何一条。

## Contents

- [渲染产物](#渲染产物)
- [渲染入参](#渲染入参)
- [模板替换点](#模板替换点)
- [阈值语义](#阈值语义)
- [性能期望集](#性能期望集)
- [运行参数来源](#运行参数来源)
- [范围之外](#范围之外)

## 渲染产物

`package.py` 从 `assets/template/verify_performance.py` 渲染出包里的
`verify_performance.py`，同批写出 `gpu_baseline.csv`：

| 产物 | 来源 | 内容 |
| --- | --- | --- |
| `verify_performance.py` | 模板加 FACTS 替换 | 性能采集入口，真机上直接运行 |
| `gpu_baseline.csv` | `perf.meta`、`perf.key`、`perf.rows` | 六行元数据注释、表头 `id,<性能键…>,gpu_ms`、逐行基线 |

两者都是派生文件：手改包内副本会让 `package.py check` 的逐字节比对失败。要改就改 FACTS
或模板，再重新渲染。

accept 另有一条入口走同一份模板：`render_runtime()` 只用 op、family、性能键列、阈值与
harness profile 五个最小事实渲染，不需要 FACTS——社区旧包因此也能变成运行时包。模板改动
同时作用于这两条入口，两侧都要回归。

## 渲染入参

FACTS 字段的定义、取值约束与校验在 [facts-schema.md](facts-schema.md)，本节只列出进入
性能脚本的那些项与去处：

| FACTS 项 | 去处 |
| --- | --- |
| `op`、`family` | 脚本常量，决定构建目标与产物路径 |
| `harness_profile` | 从 registry 取该 profile 的构建与绑卡惯例三字段，注入 `BUILD_CONVENTION` |
| `perf.key` | 基线键列顺序，脚本按它给每行算基线键 |
| `perf.rows` | `gpu_baseline.csv` 的数据行，`gpu_ms` 可留空 |
| `perf.threshold` | 脚本常量 `PERF_THRESHOLD` |
| `perf.meta` | `gpu_baseline.csv` 顶部的六行注释，未填写的写 `unspecified` |
| `dtype_profiles` | `PROFILE_ASSIGNS`，供虚拟键 `profile` 投影出 dtype 列 |
| 任务包 CSV 的文件名与哈希 | `CSV_NAME`、`PACKAGE_CSV_SHA256`，运行期据此定位并校验 CSV |

`perf.meta` 的 `warmup` 键描述 GPU 对照组自己怎么测，与 NPU 侧采集无关，不要按名字联想
成采集脚本的参数。该键当前在已有任务包里全是 `unspecified`。

## 模板替换点

模板顶部有一段渲染常量区，占位符形如 `@@NAME@@`，渲染时一次性替换。脚本正文不含任何
算子相关的字面量——这是「模板和脚本不得出现具体算子名」这条红线的落点。

| 占位符 | 取值来源 |
| --- | --- |
| `@@OP@@`、`@@FAMILY@@` | FACTS 的 `op` 与 `family` |
| `@@CSV_NAME@@` | `<op>_test.csv` |
| `@@PACKAGE_CSV_SHA256@@` | 渲染时实算的任务包 CSV 哈希 |
| `@@GENERATOR_VERSION@@` | FACTS 的 `generator_version` |
| `@@PERF_KEY@@` | `perf.key` 列名，按列表顺序展开 |
| `@@PERF_THRESHOLD@@` | `perf.threshold`，缺省 `0.8` |
| `@@PROFILE_ASSIGNS@@` | `dtype_profiles` 的 name 到 assign 映射，JSON 文本 |
| `@@HARNESS_PROFILE@@` | FACTS 的 `harness_profile`，缺省 `blas` |
| `@@BUILD_CONVENTION@@` | registry 里该 profile 的三字段，JSON 文本 |

渲染后的脚本带可执行位。校验渲染是否一致用 `package.py check`，它重渲一遍再逐字节比对。

## 阈值语义

**生成参数与正式验收政策是两件事，不要抹平。**

- 生成侧：`perf.threshold` 默认 `0.8` 是项目策略，不是外部事实。任务书另有要求时 FACTS
  可以覆盖，覆盖值渲染进脚本常量，并出现在包内 README 的性能说明里。
- 验收侧：正式结论用的阈值固定 `0.8`，由 accept 渲染运行时脚本时传入，并写进
  `manifest.json` 的 `threshold`，任务包不能覆盖。

所以同一份模板在两种场合可能带不同阈值：包内自测按 FACTS 的值，正式验收按 accept 传入
的值。两者不一致不是缺陷，是分工。

## 性能期望集

渲染出的脚本只跑**能在 `gpu_baseline.csv` 配到非空 `gpu_ms` 的 `TC_PF_` 行**。配不到
基线的 `TC_PF_` 行不采集、不评判，用例名计入结果 JSON 的 `ignored_no_ref`。

`TC_PF_` 是性能用例块的前缀；四块命名见 [csv-and-blocks.md](csv-and-blocks.md) 的「四块」。
基线按基线键匹配，基线键就是 `perf.key` 各列的值经归一后拼成的元组，归一规则是去首尾
空白、整数字符串转整数。

生成期填不上 `gpu_ms` 的行照常渲染进 CSV 与基线文件。**基线表补上数之后不必重新渲染**，
那些行在验收时自动进入期望集；但补数属于手工编辑，此后该包不再通过 `package.py check`
的逐字节比对。

## 运行参数来源

脚本的运行期参数由启动方在真机上传入，生成侧不定义它们的取值：验收链 A4 传
`--repo`、`--soc`、`--device`、`--run-id`、`--skip-build` 与 `--calls-per-case`，复测轮
另传点名与设备映射参数。这些参数的含义、取值约束与退出码在 accept 侧的
`references/run-chain.md` 与 `references/perf-protocol.md`。

包内自测按 README 给出的命令行跑，用的是同一个脚本、同一批参数名。

## 范围之外

下列内容一律以 accept 的 `references/perf-protocol.md` 为准，生成侧不做第二处定义：

- 采集命令的形态、产物布局与解析方式。
- 逐次失败判定、状态词表与截断检查。
- 统计、比对与 scope caveat。
- 结果 JSON 的字段、summary 与退出码。
- 证据保护、复测与豁免、验收消费规则。

改模板的采集行为时，同步改的是那一份协议，不是本文件。本文件只在渲染入参、替换点或
产物清单变化时才动。
