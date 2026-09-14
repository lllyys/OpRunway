# BLAS 六件任务包契约

**修改 `repo-task-blas-case-gen` 或 `repo-task-blas-accept` 且触及本页任一项时读本文件。**

本文件描述两个 skill 之间的开发契约。
运行态 agent 不读取 `docs/`，所以每项同时列出
必须同步的 `SKILL.md` 或 reference；只改本页不会改变实际行为。

## Contents

- [使用态边界](#使用态边界)
- [六件清单](#六件清单)
- [FACTS 属性模型](#facts-属性模型)
- [CSV 投影与四块](#csv-投影与四块)
- [精度结果 JSON](#精度结果-json)
- [性能结果 JSON](#性能结果-json)
- [部署与查找规则](#部署与查找规则)
- [运行态归属](#运行态归属)
- [改契约的纪律](#改契约的纪律)

## 使用态边界

两个 skill 可独立使用。生成侧接受原样任务书，验收侧接受任何满足本契约的六件目录；
验收侧通过同插件的 case-gen 校验器读取 FACTS，但不要求本次任务包由同一会话生成。

开发态以六件目录为唯一交界，不再建设第二套 acceptance 实现。

## 六件清单

任务包固定包含以下文件，文件名和职责属于契约：

| 文件 | 生成侧责任 | 验收侧用途 |
| --- | --- | --- |
| `gen_csv.py` | 保存 FACTS 与冻结的通用生成代码 | 读取 FACTS，核生成器版本与通用区哈希 |
| `<op>_test.csv` | 生成四块用例并保持可重生成 | 部署、构造精度集和性能集 |
| `verify_accuracy.py` | 渲染身份常量与精度运行协议 | 构建、运行非 PF 用例并写 JSON |
| `verify_performance.py` | 渲染性能键、阈值与 msprof 协议 | 逐个 PF 用例采集 kernel 时间并写 JSON |
| `README.md` | 投影接口、列、golden 与 verify 契约 | A2′ 人工审阅 C++ 消费侧 |
| `gpu_baseline.csv` | 投影性能键、GPU 值与口径元数据 | 性能脚本匹配基线并标 scope caveat |

六件均在时任务包才完整；没有性能基线也保留只有元数据和表头的
`gpu_baseline.csv`。

## FACTS 属性模型

FACTS 是六件的唯一结构化事实源，按公开 C 签名顺序保存参数。角色、开放属性、表达式、
profiles、edge、perf、sources 与词表不在本页复制，运行态权威是
`repo-task-blas-case-gen/references/facts-schema.md`。

`package.py check` 是 CLI 机械门；内部 `validate()` 只校 FACTS schema。新增或改变 FACTS
字段时，至少同步以下消费面：

- `validate`、表头投影和生成模板。
- README 渲染、六件包级 check 与示例。
- accept 的 FACTS 加载、声明比对或 verdict；没有消费则写明不受影响。
- `facts-schema.md` 与相关运行态 reference。

## CSV 投影与四块

CSV 表头由 params 顺序确定，参数角色投影、控制列、轴、footprint 与 pairwise 的权威规则
在 `repo-task-blas-case-gen/references/csv-and-blocks.md`。四块按固定顺序合并：

| 块 | 前缀 | 责任 |
| --- | --- | --- |
| L0 | `TC_L0_` | 小尺寸、op enum 全组合与 profile 地板 |
| PW | `TC_PW_` | 合法轴值的确定性 pairwise 覆盖 |
| ED | `TC_ED_` | 有来源和期望状态码的边界或负例 |
| PF | `TC_PF_` | 性能基线行和可选尺寸 sweep |

精度期望集是部署 CSV 中所有 `TC_` 且不以 `TC_PF_` 开头的行。性能期望集只含
`TC_PF_` 行；相同键同时出现在 PW 与 PF 时仍各自保留，因为两块承载不同证据。

## 精度结果 JSON

`verify_accuracy.py` 原子写 `results/accuracy_<run-id>.json`。顶层字段如下：

| 字段 | 含义 |
| --- | --- |
| `run_id/op/family/soc/arch/device/repo` | 本轮身份与工程环境 |
| `binary/binary_sha256` | 被执行二进制及内容身份 |
| `csv_path/csv_sha256/package_csv_sha256` | 部署 CSV 与任务包绑定 |
| `gtest_filter` | 本轮传给 GTest 的完整过滤串 |
| `cases` | 期望集每例的实际记录 |
| `summary` | expected 与六种状态计数；环境失败另带 reason/message |
| `exit_code` | 0 全 PASS，1 测试失败，3 环境问题 |
| `started/finished` | 带时区时间戳 |

每个 `cases[]` 含 `name`、`gtest_name`、`status`、`ms` 与 `message`。status 只取
`PASS/FAIL/SKIP/TIMEOUT/CRASH/MISSING`；其中 GTest 的 `ms` 只作诊断，不是性能证据。

## 性能结果 JSON

`verify_performance.py` 原子写 `results/performance_<run-id>.json`。它复用精度 JSON 的
身份和哈希字段，并增加 `gpu_baseline_path` 与 `msprof`；每例字段如下：

| 字段 | 含义 |
| --- | --- |
| `name/gtest_name/status/message` | 用例身份与运行状态 |
| `kernel_us` | 多次 kernel duration 总和的中位数 |
| `samples` | 每次采样的 kernel duration 总和 |
| `launches` | 每次采样命中的 kernel 行数 |
| `gpu_ms` | 同键 GPU 基线；无值时为 null |
| `ratio` | `gpu_ms/(kernel_us/1000)` |
| `spread` | `(max-min)/median` |
| `verdict` | 单例状态及可选 `(scope caveat)` 后缀 |

summary 含状态计数、`status`、`timing_scope`、`threshold` 与 `scope_caveat`。
status 只取 `通过/不通过/NO_REF/证据不足`。验收侧不重算 kernel 数据，但会重算状态计数，
并闭合期望集、身份、CSV/binary SHA、summary 和退出码。

性能协议与退出码的运行态权威是两侧 `references/perf-protocol.md`。msprof 目标机 spike
尚未完成，目录模式、列名、task type 与命令组合仍标为“待实测”；本地解析成功不能消掉标记。

## 部署与查找规则

CSV 必须逐字节部署到：

```text
test/<family>/<op>/<arch>/<op>_test.csv
```

测试通过 `ReplaceFileExtension2Csv(__FILE__)` 从测试源码同目录取同名 CSV，因此只把文件
放在包外目录不生效。accept 按以下三条规则依次构造 CSV 与二进制候选：

1. 直接目录：`test/<op>/...` 或 `build/test/<op>/...`。
2. 族目录：`test/*/<op>/...` 或 `build/test/*/<op>/...`。
3. 去首个类型字符的平铺目录：`test/<op[1:]>/...` 或对应 build 目录。

CSV 三条规则同时命中多份时是 `CSV_AMBIGUOUS`，没有命中是 `CSV_NOT_DEPLOYED`；命中
文件的 SHA-256 必须与任务包 CSV 一致。二进制名固定为 `<op>_test`。

## 运行态归属

A5 必须读取同一工作目录的 `check.json`，并校验其 `evidence_id`、package、repo、op、family、
soc、device、任务包哈希和部署 CSV 哈希。缺失、畸形或身份不一致都判为证据不足，不能由
精度与性能 PASS 覆盖。

开发契约的每个部分在运行态有唯一讲解位置：

| 契约项 | 运行态权威位置 |
| --- | --- |
| 六件与生成阶段 | case-gen `SKILL.md`、`references/readme-contract.md` |
| FACTS 模型与词表 | case-gen `references/facts-schema.md`、`aclblas-conventions.md` |
| CSV 投影与四块 | case-gen `references/csv-and-blocks.md` |
| 精度脚本与 JSON | case-gen 渲染 README、accept `references/run-chain.md` |
| 性能协议与 JSON | 两侧 `references/perf-protocol.md` |
| 部署、二进制与复跑 | accept `SKILL.md`、`references/run-chain.md` |
| 常见运行错误 | accept `references/troubleshooting.md` |

## 改契约的纪律

改动六件文件名、FACTS 字段、CSV 投影、结果 JSON、部署路径或状态语义时，必须执行以下步骤：

1. 同时修改生成侧与验收侧的消费者，不留兼容性猜测。
2. 更新上表指向的运行态文档；开发文档不能替代 skill 自身契约。
3. 重渲染三个示例，并对全部锚点执行 `check → render → check`。
4. 在目标 SoC 真机重跑受影响的精度与性能链，再回填 skill 级真机事实表。

第 4 条尚未完成时，只能报告静态门通过和协议待实测，不能写“正式验收通过”。
