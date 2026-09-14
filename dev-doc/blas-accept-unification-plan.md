# BLAS 验收链统一方案：case-gen 产出对标旧包，accept 单一路径

状态：Codex 评审（MAJOR GAPS，thread `01a04cad-b71f-7051-becc-8503d0ecd766`）→ 按下文 §0 收敛 → 实施中。
日期：2026-08-29。证据：`reports/pr-accept-20260828/`（四个 PR 真机验收）与本文件 §9。

## 0. 实施版最终口径（与下文冲突处以此为准）

Codex 评审采纳的：包内 CSV 是契约（期望集来源），部署 CSV 只是实际输入；运行时落点统一到
`<工作目录>/runtime/`；`render_runtime` 带 `csv_sha256`；A2 保持「未编译只提示」；fill 小写要改
生成器模板本身。用户裁定简化的：不比对部署 CSV 与包内 CSV 的 SHA（连警告都不留）；列名未被读取只
记录不裁决；不做「有 PF 无基线判证据不足」的特例；默认无异常、无违规，不为异常分支加逻辑；仓规不建
单测，验证靠真机。

| 项 | 最终 |
| --- | --- |
| accept 读的包内文件 | 只有 `<op>_test.csv`（恰好一个）与 `gpu_baseline.csv` |
| A2（`accept.py check`） | 有无门：包两件在；部署 CSV 三条规则恰好命中一份；`built_tests.list` 有则核；case-gen 模板在场。记录：`COLUMN_NOT_READ` 列表、无基线 PF 数、各 SHA |
| A2′ | `<op>_param.h`、`<op>_test.cpp`、`<op>_npu_wrapper.h` 三个文件有无，缺件只记录 |
| 推断 | op = CSV 文件名；family = 部署 CSV 相对 `test/` 的首段；perf.key = 基线表头去 id/gpu_ms，再去 CSV 没有或整列为空的列；阈值 0.8 |
| 运行时包 | `<工作目录>/runtime/`：CSV 副本、规范化基线（表头 `id,<key…>,gpu_ms`）、渲染的两个 verify 脚本、`manifest.json`；结果落 `runtime/results/` |
| 期望集 | 精度 = 包内 CSV 非 `TC_PF_` 行；性能 = 包内 CSV 中能配到非空 `gpu_ms` 的 `TC_PF_` 行；部署里缺的记 MISSING |
| `--calls-per-case N` | `check` 写进 manifest，`verify_performance.py` 接收并把 kernel 总时长除以 N；默认 1，旧 harness（固定 warm-up）填 2 |
| A5 | 从 `runtime/manifest.json` + `runtime/results` 出结论；集合闭合、计数一致、哈希、NO_REF→证据不足 不变；不再核 FACTS/声明/CSV SHA |
| case-gen | fill 列 `<name 小写>_fill`；`package.py` 新增 `render_runtime()`；模板期望集与 `_summarize` 同上 |
| 删除 | `_load_package_facts`、声明比对、五件逐字节、`gen_csv.py` 哈希、CSV SHA 比对、ReadMap 计数 |

## 1. 问题

社区 8 月 BLAS 任务发出去的任务包有两代手写格式（任务书随附 5 包、0826 线上发放 31 包），
开发者的 C++ harness 全部按旧包写；skill 当前的六件是 FACTS 渲染的第三代格式。三者互不兼容：

- `repo-task-blas-accept` 对 36 个旧包在 A2 第一步全部退出 2（`gen_csv.py` 没有 FACTS）；
- case-gen 生成的 CSV 拿去跑旧 harness，`ReadMap` 找不到列时静默取默认值，会产出假 PASS；
- 四个 PR（!347 cherk、!348 csyrk、!349 cher2k、!350 csymm）只能脱离 skill 手工按旧包验收。

旧包自身的缺陷在真机上全部复现：`[ SKIPPED ]` 藏在「ALL PASS」里（各 4 条 n=8000 未执行）、
删掉的 100 条 PF 行无人发现、超时静默退 0、性能取 gtest 毫秒（含 CPU golden）按设计不可能达标、
没有一个旧包真正调用 msprof。

## 2. 目标与约束

| 需求 | 含义 |
| --- | --- |
| R1 | case-gen 生成的六件能让**按旧包写的 harness**通过 accept 完成验收 |
| R2 | accept 能**原样**使用旧包（不补清单、不改脚本、不改 CSV）完成验收 |

约束：不分新旧两条路径；`experimental/` 不在范围；假定 ops-blas 的 CSV 驱动 gtest 框架
（`test/frame/`）；A2 与 A2′ 只以「文件有无」裁决；性能采集保留 msprof。

## 3. 方案

### 3.1 一条路径

```
任务书 ──case-gen──▶ 六件（旧包形态）──┐
                                       ├──accept──▶ 有无门 → 渲染量具 → A3 → A4 → A5
旧包（原样）────────────────────────────┘
```

accept 对任何包只看两件：`<op>_test.csv` 与 `gpu_baseline.csv`。从它们和工程推断
op / family / 基线键，用 case-gen 模板把两个 verify 脚本渲染到工作目录后执行。
包内的 `gen_csv.py`、README、verify 脚本 accept 一概不读。

### 3.2 accept 的门

| 阶段 | 裁决（有无） | 只记录，不裁决 |
| --- | --- | --- |
| A1 | `build.sh`、`test/frame/csv_loader.h`、`include/cann_ops_blas.h`、CANN | cmake/g++/msprof/npu-smi/cblas 等 |
| A2 | 包：`<op>_test.csv`、`gpu_baseline.csv`；工程：部署 CSV 按三条规则找到且唯一、`built_tests.list` 含算子且不在 `skipped_tests.list`；量具：case-gen 模板在场 | 包内 CSV 与部署 CSV 的 SHA 是否一致；CSV 每个列名是否在 `param.h`（或同目录头文件）中以字面量出现（`COLUMN_NOT_READ` 列表）；`ReadMap` 带默认值的调用数 |
| A2′ | `param.h`、`test.cpp`、`npu_wrapper.h` 存在 | — |
| A3–A5 | 不变：run-id、JSON、二进制/CSV 哈希、集合闭合、计数一致、NO_REF 与证据不足的判定 | — |

从 accept 删除：读 FACTS、通用代码区 SHA、五件逐字节比对、声明比对、`gen_csv.py` 哈希。
证据身份（`evidence_id`）改为 package/repo/op/family/soc/device + 包内 CSV 与基线的 SHA。

### 3.3 推断规则

| 事实 | 来源 |
| --- | --- |
| `op` | `<op>_test.csv` 文件名 |
| `family` | 现有三条查找规则命中的目录（`test/<op>/`、`test/*/<op>/`、`test/<op[1:]>/`） |
| `perf.key` | `gpu_baseline.csv` 表头去掉 `id`、`gpu_ms`，再去掉 CSV 表头里不存在或整列为空的列 |
| 基线副本 | 按上述键规范化后写入工作目录（模板要求表头严格等于 `id,<key…>,gpu_ms`） |
| 阈值 | 0.8 |

### 3.4 性能协议的两条规则

1. **A4 期望集 = 部署 CSV 中有 GPU 基线的 `TC_PF_` 行。** 无基线的 PF 行不进入期望集、不跑，
   报告里只计数。对 case-gen 包退化为现状（PF 行由 `perf.rows` 物化，行行有基线）；对随附旧包是
   4 条，对 0826 旧包是 200 条。
2. **`--calls-per-case N`（默认 1）。** msprof 采到的是一条 gtest 用例里全部 kernel；kernel 总时长
   除以 N 得单次调用。README 契约明写「一条用例只调用被测接口一次，预热与重复由量具负责」；
   旧 harness（含四个 PR，固定 warm-up 一次）由验收人按 wrapper 事实填 2，并写进 JSON 与报告。

采集方式不变：每条用例 1 次预热 + 5 次 `msprof --application` 采集，`--export=on` 读
op_summary 的 `Task Duration(us)`，取中位数，`ratio = gpu_ms / npu_ms ≥ 0.8`。

### 3.5 case-gen 对标旧包

| 项 | 现状 | 改动 |
| --- | --- | --- |
| fill 列名 | `<Name>_fill`，Name 为头文件里的参数名（`A_fill`） | 改为小写 `a_fill`；`_header_columns`、README 列契约同步 |
| 复数标量、null 标志、枚举、ld | `alpha_re/_im`、`nullA`、明文枚举、显式 ld | 已与随附代一致，不动 |
| 列序、块名、序号位数 | 与旧包不同 | 不动：harness 按列名取值 |
| verify 脚本命令行 | 已是旧脚本参数的超集（`--run-id` 默认时间戳） | 不动；可选补旧式 `汇总:`/`结论:` 两行输出 |
| README | FACTS 投影 | 加「一条用例只调一次接口」条款 |
| 对标的旧代 | — | 任务书随附代。0826 代的 `alpha_real/imag`、`a_null` 命名与之互斥，不对标 |

## 4. 明确放弃的

- 接口保真（声明返回类型/参数类型与名字比对）——旧包无可靠来源，统一不做。
- 列契约强制（`param.h` 缺列抛错）——改为记录 `COLUMN_NOT_READ`，不裁决。
- CSV 来源可信（渲染器保证覆盖）——accept 不再核，只记 provenance。
- 0826 代命名、chemm（无 PR）、四 PR 合并态、`-O0`（仓库 `CMakeLists.txt:66` 强制 Debug，
  issue #363，仓库口径）。

## 5. 改动清单

**`plugin/skill/repo-task-blas-case-gen`**

| 文件 | 改动 |
| --- | --- |
| `scripts/package.py` | fill 列小写（生成器投影、`_header_columns`、README 列契约）；暴露 `render_runtime(op, family, perf_key, threshold, out_dir)` 供 accept 渲染两个 verify 脚本 |
| `assets/template/verify_performance.py` | `--calls-per-case`；期望集按基线键过滤；NO_REF 不再决定汇总状态 |
| `assets/template/verify_accuracy.py` | 可选：旧式两行摘要 |
| `assets/template/README.md`、`references/readme-contract.md`、`perf-protocol.md`、`csv-and-blocks.md` | 列名规则、单次调用契约、期望集规则 |
| `assets/example/cherk/`、`sasum/` | 重渲染 |

**`plugin/skill/repo-task-blas-accept`**

| 文件 | 改动 |
| --- | --- |
| `scripts/accept.py` `check` | 有无门；推断 op/family/perf.key；基线规范化副本；SHA 与列名读取记录；新 `evidence_id` 字段 |
| `scripts/accept.py` A3/A4 前 | 调 `render_runtime` 渲染到工作目录；透传 `--calls-per-case` |
| `scripts/accept.py` `verdict` | 期望 PF 集按基线过滤；`_contract_summary` 认新字段与 warnings；`report.md` 首页列 provenance、警告、`calls_per_case` |
| `SKILL.md`、`references/run-chain.md`、`troubleshooting.md` | 入口参数、门的语义、A2′ 新形态、`--calls-per-case` |

估计 300–450 行含文档。

## 6. 验证矩阵

| 路径 | 输入 | 预期 |
| --- | --- | --- |
| R1 | cherk 现有 FACTS + 新填 csyrk/cher2k/csymm → 渲染新包 → 新 CSV 部署到 scratch checkout → accept 全链，`--calls-per-case 2` | A2 通过、`COLUMN_NOT_READ` 为空；A3 与 `reports/pr-accept-20260828` 一致（各 4 条 n=8000 非 PASS，其余 PASS）；A4 默认构建不通过，kernel 数与该报告 §5.2 一致 |
| R2 | 旧包原样 + PR 原 checkout → accept | A2 有无通过，警告 CSV 不一致；A4 期望集 4 条全 MISSING → 证据不足；把旧包完整 CSV 部署回去后与 R1 结果一致 |
| 存量 | 36 个旧包静态过 A2（`--repo` 指本地只读克隆） | 全部退出 0 |
| 回归 | `assets/example` 重渲染后 `package.py check`；`tests/test_document_style.py` | 逐字节一致；风格测试不新增违规 |

## 7. 实施顺序与检查点

1. 前置：读 `.claude/hooks/skill-best-practices.md`，经 `/skill-creator` 改动 skill。
2. case-gen：fill 小写 → `render_runtime` → 性能模板两条规则 → 文档 → 示例重渲染。
3. accept：`check` → 渲染与透传 → `verdict`/报告 → 文档。
4. 三份 FACTS（csyrk/cher2k/csymm）。
5. 真机：R1、R2 各四个 PR；存量 36 包静态。
6. Codex 六维评审（两个 skill 都动了对外契约，无条件触发）；一轮 audit → fix → verify。

## 8. 风险与待定口径

| 风险 | 处理 |
| --- | --- |
| A2′ 弱化后，列名对不上的假 PASS 只靠 `COLUMN_NOT_READ` 记录 | 报告首页列出；是否升级为裁决留待后续 |
| 开发者 harness 的 host 内存护栏跳过大形状 → A3 MISSING、A4 NO_KERNEL | 如实报告，不绕 |
| 0826 包 200 条 PF 全量 msprof 约 3 小时/算子 | `--repeats` 可调；先跑随附版 |
| `calls-per-case` 是人工填的 harness 事实 | 写进 JSON 与报告；新 README 契约要求 1 |
| 契约 CSV 由谁部署 | 四个 PR 删了 PF 行，按门就是 CSV 不一致 + A4 证据不足；验收人可在 scratch checkout 部署包内 CSV 复验 |

## 9. 已有证据

- 四个 PR 真机验收：`reports/pr-accept-20260828/report.md`（精度 0 失败、各 4 条未执行；
  性能默认构建 16/16 不通过 ratio 0.05–0.10，Release 对照 16/16 通过 2.06–4.40×；
  msprof 原始 op_summary 160+ 份）。
- A2 探针：36 个旧包 + 10 种畸形包，全部退出 2、无 traceback（scratchpad `acc/`）。
- 旧包横向比对：36/36 共有 `case_name/description/expect_result/random_seed`、`TC_` 命名、
  gtest 二进制约定；脚本 31 版（同骨架逐算子手改）；列名约定 13 种；0/36 调用 msprof。
- 模板耦合点：verify 脚本只烘 OP/FAMILY/CSV_NAME/GENERATOR_VERSION/PERF_KEY/PERF_THRESHOLD/
  PROFILE_ASSIGNS；只读 `case_name`、`TC_PF_` 与基线键列。
