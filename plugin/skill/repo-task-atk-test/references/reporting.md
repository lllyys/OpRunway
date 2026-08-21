# 结果、裁决与报告

## 目录

- 解析
- 统计
- 归因
- 裁决
- 交付

解析、统计、归因和裁决属于 S4；报告结构与复现包属于 S5。

## 解析

精度：

```bash
<python> scripts/parse_atk_report.py \
  -i <accuracy.xlsx> -c <case-json> [-x evidence/excluded_cases.json] \
  -o conclusion/accuracy_results.json
```

性能省略 `-c`，输出 `performance_results.json`。

脚本保存源文件摘要、用例摘要、任务、节点、后端、总数、通过数、失败数、通过率和逐用例字段。

## 统计

通过率为 `passed / valid_cases`。

有效用例是产生有效精度判定的用例。

无效参数和资源预算用例不进入分母，必须单列 id、原因和原始日志。

无效必测用例同时列为覆盖缺口。

`non_finite_input` 只是事实标签，不改变分母。

不得把未知失败标记为无效。

## 归因

归因只使用报告失败原因、运行结果和精度详情。

接口模式规则由 `references/interface-policy.json` 定义；精度、用例排除、失败归因和结论原因由
`references/verdict-policy.json` 定义。

证据不足时使用 `unknown`。

报告区分客观统计、初步归因和待复测项。

### 不得跨类外推

一条失败用例的归因只覆盖它自己。

写「这批失败的根因是 X」之前，先按能从用例规格算出来的特征分组：dtype、rank、参与轴的条数、参数是否有重复值、规模档。

只对有证据的那组下结论，其余组写 `unknown`，并把各组条数写进报告。

真机实测（roll，2026-08-15）：67 条失败的证据全来自多轴用例，结论却写成「多轴语义不一致」，而其中 20 条是单轴用例——交付给开发者的诊断因此是错的。

### 需要逐元素差异时

ATK 报告只给「比对不通过」，不给差在哪。

要看差异可以自己重放，模板见 `assets/example/replay_case.py`，它复用 ATK 运行时契约的组件读同一份冻结输入。

重放是可选的深挖手段，不是归因的必经步骤：证据不足就写 `unknown`，不要为了凑一个根因去补跑。

重放脚本与日志不属于待验收对象，也不进量具，随证据一起交付。

## 裁决

执行：

```bash
<python> scripts/verdict.py \
  --interface evidence/interface.json \
  -c evidence/coverage.json \
  -r conclusion/accuracy_results.json \
  --perf-results conclusion/performance_results.json \
  [--perf-baseline-source '<任务书原文>'] \
  --op <算子名> --env evidence/env.json \
  -o conclusion/verdict.json
```

精度通过时 `--perf-results` 必给：性能状态由它推导，缺了直接拒绝裁决。

`--perf-baseline-source` 不给即判定为无基线，不要为了让状态变「通过」去编一份依据。

裁决核对任务类型、实际后端与 S1 派生后端是否一致、用例摘要、必测集摘要、精度标准、比较器、禁止覆盖字段、输出存在性和排除集合。

写报告时直接取 `verdict.json` 的这几个键，不要再自己从结果文件重算：

| 键 | 内容 |
| --- | --- |
| `conclusion` / `reason` | 结论与依据，报告的结论句照抄这两项 |
| `partitions` | 分区级总数、通过数、失败数、通过率 |
| `performance` | 性能状态原文、依据、基线从哪来、已采集绝对耗时的用例数 |
| `pending_recheck` / `must_cases_pending_recheck` | 待复核用例，以及其中落在必测集里的 |
| `excluded_cases` / `must_coverage_gap_ids` | 剔除集合，以及由此形成的必测覆盖缺口 |
| `interface` | 模式、执行后端、基线、性能基线及其依据 |

任一门禁失败都拒绝裁决。

## 报告结构

报告按以下顺序写：结论、验收范围、覆盖、精度、性能、失败归因、待复测、无效用例、证据链、复现。

数字全部从结构化产物读取。

不要人工重算通过率。

不要把推定根因写成确认事实。

阻塞时写“阻塞·未验收 @<阶段号>”，列出失败门禁、命令、退出码、错误、已完成项和重入条件。

S5 完成条件是摘要、政策摘要和证据链三项核对通过。

报告必须写出 S4 的性能状态原文，照抄 `verdict.json` 的 `performance.status`。

状态为「未执行(无基线)」时，性能一节仍要写清绝对耗时是从哪份报告读的，那是本轮真实采集的数据。

失败、超时或中止的已执行用例运行：

```bash
<python> scripts/save_failed_cases.py \
  -j <case-json> --ids '[0]' --stage smoke \
  --log evidence/smoke.log -o evidence/failed_cases.json
```

该文件保存冻结用例的完整参数、阶段和原始日志路径。

## 复现包

```bash
<python> scripts/make_repro.py \
  --interface evidence/interface.json --verdict conclusion/verdict.json \
  -j <case-json> --cmd-log evidence/repro.sh \
  --env evidence/env.json -o delivery/repro.tar.gz
```

只传入实际使用的可选文件。

复现包必须包含实际用例、政策和命令日志，不得生成未执行命令。

## 真值来自 CANN 内置实现时

`verdict.json` 的 `conclusion_kind` 是 `regression_vs_builtin` 时，结论只能写
「待验收算子的输出与 CANN 内置实现逐位一致」或「第 N 条不一致」。

不能写「精度达标」：内置实现本身没有被这轮验收检验过，它只是对照物。

报告必须引用三样东西，缺一项结论不成立：

| 引什么 | 从哪取 |
| --- | --- |
| 两侧算子库的完整路径与 SHA256 | `evidence/opp_library_builtin.json`、`evidence/opp_library_candidate.json` |
| 两轮的用例 JSON 与冻结输入 | `evidence/golden_provenance.json` 的 `case_json_sha256`、`input_data` |
| 反证实验的结论 | 同上的 `counter_experiment` |

这三样都是落盘产物，照抄路径与摘要值，不要另写一遍描述。

反证实验那一条尤其要写进报告正文：全绿本身说明不了任何事，它是唯一能区分
「真的逐位一致」与「比对压根没接上」的证据。

裁决前的核对分两处，都不由报告作者自己声明：

`check_golden_source.py` 核两侧库不是同一份、两轮吃同一批用例、反证实验通过，
过了才写出 `evidence/golden_source.json`。

`verdict.py` 再核这份结论存在且为 ok、取证齐备、实际跑的比较器是 `equal`，
缺任何一样直接拒绝出结论。
