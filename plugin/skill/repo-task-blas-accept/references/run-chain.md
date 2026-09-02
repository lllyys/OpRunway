# 验收运行链

## Contents

- [输入与目录](#输入与目录)
- [A1 环境](#a1-环境)
- [A2 有无门](#a2-有无门)
- [A2′ 三文件](#a2-三文件)
- [A3 精度](#a3-精度)
- [A4 性能](#a4-性能)
- [A5 结论](#a5-结论)
- [工程查找规则](#工程查找规则)
- [复跑与归因](#复跑与归因)
- [停止报告模板](#停止报告模板)

## 输入与目录

**任务包**是一个目录，里面恰好一个 `<op>_test.csv`（契约用例集；文件名去掉 `_test.csv`
就是 `<op>`）加一张 `gpu_baseline.csv`（GPU 基线表）。`scripts/accept.py`（下称 accept）
对任何任务包只读这两件；`gen_csv.py`、README、包内 verify 脚本一概不读。**量具**指 accept
渲染出的 `verify_accuracy.py` 与 `verify_performance.py`，A3、A4 运行它们。命令里的
`<skill>` 是 SKILL.md 所在目录的绝对路径，`<skill>/scripts/accept.py` 由它定位。

**部署 CSV** 是开发者放在 `<工程目录>/test/…/<arch>/<op>_test.csv` 的那份用例表，harness
实际消费它，查找规则见「工程查找规则」。**harness** 是工程里驱动 GTest 的 C++ 测试代码，
即 `<op>_param.h`、`<op>_test.cpp`、`<op>_npu_wrapper.h` 三文件，位于部署 CSV 所在 arch
目录的上一级。

| 输入 | 责任 |
| --- | --- |
| 任务包 | 给出期望用例集与 GPU 基线 |
| 开发者工程 | 提供 harness、构建脚本、部署 CSV 与被测实现 |
| SoC | 决定部署 CSV 的 arch 目录 |
| device | 传给 `build.sh --device`，由 `-DTEST_DEVICE_ID` 编译期固定 |
| calls_per_case | harness 一条 GTest 用例调用被测接口的次数，数法见 A2′，A2 与 A4 填同一个值 |
| run-id | 一轮运行的标识，串起精度、复跑、性能与结论；建议 `<op>-<YYYYMMDD-HHMM>` |

所有输入路径使用绝对路径，所有命令先进入工作目录。工作目录的布局固定如下：

```text
<工作目录>/
├── env.json                    # A1
├── check.json                  # A2（含 A2′）
├── runtime/                    # A2 生成的运行时包
│   ├── <op>_test.csv           # 任务包 CSV 副本
│   ├── gpu_baseline.csv        # 规范化基线，表头 id,<基线键…>,gpu_ms
│   ├── verify_accuracy.py      # 渲染出的精度量具
│   ├── verify_performance.py   # 渲染出的性能量具
│   ├── manifest.json           # 运行时包清单
│   └── results/                # A3/A4 写入
│       ├── accuracy_<id>.json
│       ├── accuracy_<id>-rerun.json
│       ├── performance_<id>.json
│       └── <id>/{accuracy,performance}/   # build.log、gtest.json、prof/
└── verdict/                    # A5 三类产物布局
    ├── report/report.md        # 人读报告（三节：精度、性能、备注说明）
    ├── intermediate/           # 执行期 JSON：verdict/accuracy/performance/check/manifest
    └── repro/                  # 最小可复现：任务包六件副本 + cases.csv 用例清单
```

**运行时包**就是 `runtime/` 这个目录：accept 把任意任务包变成量具能直接消费的形态。
**基线键**是 `gpu_baseline.csv` 里用来把一行基线配到一条 CSV 用例的列组合，推断规则见 A2。

## A1 环境

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py env \
  --repo <工程目录> --soc <soc> --device <device>
```

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | 硬前置齐全 | A2 |
| 3 | 硬前置至少一项缺失 | 停止 |

A1 只返回这两个值，其它值视同停止。硬前置五项：Python ≥ 3.8、`<工程目录>/build.sh` 可读、
`test/frame/csv_loader.h`、harness_profile 探测恰一命中、CANN `set_env.sh`（按 `ASCEND_HOME`、
`ASCEND_TOOLKIT_HOME`、`/usr/local/Ascend/ascend-toolkit/latest` 顺序找）。
**harness_profile 探测**按 registry 各 profile 的入口头在 `<工程目录>/include/` 下探测
（blas=`cann_ops_blas.h`、sparse_frame=`cann_ops_sparse.h`）；0 命中或多命中都是硬失败并
列出候选，恰一命中的键名会写进 A2 的 runtime manifest。`cmake`、`g++`、
msprof、`npu-smi`、`cblas.h`、`lapacke.h` 是警告项，后续阶段会在实际使用点给出确定错误。

`env.json` 的 `checks[]` 记录每项的 `name/status/detail/hard`，`hard_failures` 列出未通过的
硬前置，`exit_code` 是退出码。`name` 为 `CANN set_env.sh` 那项的 `detail` 就是 `set_env.sh`
的绝对路径，后面命令里的 `<set_env.sh>` 都取它。

## A2 有无门

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py check \
  --package <任务包目录> --repo <工程目录> --soc <soc> --device <device> \
  --calls-per-case <calls_per_case>
```

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | 该在的文件都在，运行时包已生成 | A2′ |
| 2 | 任务包、部署 CSV、构建清单或基线不合格 | 停止并修包或开发者工程 |
| 3 | 同插件 `repo-task-blas-case-gen/scripts/package.py` 缺失 | 恢复完整插件后重跑 |

A2 只返回这三个值，其它值视同停止。重跑 check 会覆盖 `runtime/` 与 `check.json`，
`runtime/results/` 不受影响。

有无门只裁文件有无，不看内容；按下表顺序逐项检查，任一不满足就写进 `errors` 并退出 2
（量具模板缺失退 3）：

| 对象 | 判据 | 不满足时的错误码 |
| --- | --- | --- |
| 任务包 | `*_test.csv` 恰好一个，`gpu_baseline.csv` 存在 | `PACKAGE_INVALID` |
| 量具模板 | 同插件 case-gen 的 `package.py` 能加载 | `CASE_GEN_NOT_FOUND`（退出 3） |
| 部署 CSV | SoC 有 arch 映射，三条查找规则恰好命中一份 | `CSV_NOT_DEPLOYED` / `CSV_AMBIGUOUS` |
| 构建清单 | 有清单才核：算子在 `built_tests.list` 且不在 `skipped_tests.list` | `NOT_BUILT` / `OP_SKIPPED` |
| 基线 | `gpu_baseline.csv` 有表头 | `BASELINE_INVALID` |

构建清单是 `<工程目录>/build/test/built_tests.list` 与同目录的 `skipped_tests.list`，由
`build.sh` 生成。还没编译时清单不存在，只记警告 `未编译`，A3 会调用 `build.sh` 生成。

以下几项只记录进 `check.json` 与 `warnings`，不影响退出码：

- `HARNESS_FILE_MISSING`：harness 三文件缺哪个，见 A2′。
- `COLUMN_NOT_READ`：CSV 里哪些非基座列名没有在 harness 源码中以 `"列名"` 字面量出现。
  基座列是 `case_name`、`description`、`expect_result`、`random_seed`、`mere_threshold`、
  `mare_multiplier`，由 `test/frame` 读取，不要求 harness 显式读。
- `NO_REF`：任务包里有多少条 `TC_PF_` 用例配不到非空 `gpu_ms` 基线。

`check.json` 的顶层字段：`command`、`package`、`repo`、`soc`、`device`、`calls_per_case`、
`op`、`family`、`package_csv_sha256`、`baseline_sha256`、`deployed_csv_sha256`、`checks`、
`errors`、`warnings`、`exit_code`、`evidence_id`、`runtime`（即 manifest）、`generated_at`。
`checks` 下分 `package`、`csv`、`build`、`harness`、`columns`、`perf` 六段；
`checks.perf.comparable_pf` 是性能期望集的大小，`ignored_pf` 是无基线被忽略的条数。
`evidence_id` 是 `command/package/repo/op/family/soc/device/package_csv_sha256/`
`baseline_sha256/calls_per_case` 十个字段按键排序后 JSON 序列化的 SHA-256，A5 会重算比对。

推断规则如下，只用任务包与工程里的事实：

| 事实 | 来源 |
| --- | --- |
| `op` | `<op>_test.csv` 的文件名 |
| `family` | 部署 CSV 路径 `test/<family>/…` 的首段；`test/<op>/<arch>/` 时首段就是 `op` |
| 基线键 | 基线表头去掉 `id`、`gpu_ms`，再去掉 CSV 表头没有的列和基线里整列为空的列 |
| 阈值 | 固定 `0.8`，判据见 A4 |

「整列为空」指 `gpu_baseline.csv` 里该列所有行都为空。生成运行时包时，基线副本按基线键
投影，表头严格为 `id,<基线键…>,gpu_ms`，`#key=value` 形式的元数据行原样保留；`id` 为空的行
补 `<op>-base-NNN`。`manifest.json` 记录 `op`、`family`、`perf_key`、`threshold`、
`calls_per_case`、`package`、`package_csv`、`package_csv_sha256`、`baseline_sha256`、
`normalized_baseline_sha256`、两个脚本的 SHA-256（`scripts`）与 `generated_at`。

## A2′ 三文件

A2′ 是 A2 的附属检查：只看 harness 三文件在 harness 目录里有没有，结果在 `check.json` 的
`checks.harness`（`dir`、`files`、`missing`）。

```bash
cd <工作目录> && <python> -c \
  "import json; print(json.load(open('check.json'))['checks']['harness'])"
```

`missing` 非空只是警告，A5 的报告会列出。这一步同时核对 `calls_per_case`：

1. 打开 `<op>_npu_wrapper.h`，路径在 `checks.harness.files` 的 `npu_wrapper.h` 项。
2. 数 `aclblas<Op>(` 被调用的次数，`<Op>` 是首字母大写的 `<op>`，如 `cherk` 对应
   `aclblasCherk(`；一条用例的总次数 = warm-up 调用次数 + 正式调用次数，只看这一个文件。
3. 与 A2 所填不同时用正确值重跑 A2；文件缺失时按 1 计。

把读过的路径与次数（或「wrapper 缺失，按 1 计」）写进 `report/report.md` 的 `备注说明`。

## A3 精度

```bash
cd <工作目录>/runtime && <python> verify_accuracy.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <id>
```

开发者工程的 tracked 源文件保持只读。`build.sh` 允许在工程内产生 `build/`、
`build_out/` 与 `out/`；这些目录是构建副产物，不是源文件修改授权，测试二进制只在
`<工程目录>/build/test/` 下找。脚本在 `results/<id>/accuracy/` 独占阶段目录，目录已存在时
退出 3（`RUN_ID_EXISTS`），避免旧 GTest JSON 污染新轮次。

**期望集**是本轮必须出结果的用例名集合。精度期望集 = 运行时包 CSV 里除 `TC_PF_` 前缀外的
全部有效数据行（不筛命名前缀，社区旧包的 `L0_/L1_` 命名一样进集合）；`--case` 与
`--filter` 只收窄，不新增。

`--repo/--soc/--device` 与入口参数同义，其余参数如下：

| 参数 | 含义 | 默认值 |
| --- | --- | --- |
| `--case <case_name>` | 精确复跑 case_name，可重复 | 无 |
| `--filter <子串>` | 按 case_name 子串收窄精度集 | 无 |
| `--skip-build` | 复用上次编译产物，不调用 `build.sh` | 不带则先编译 |
| `--build-timeout <秒>` | 编译超时秒数 | 1800 |
| `--timeout <秒>` | 测试与列举超时秒数 | 3600 |
| `--run-id <id>` | 结果运行标识 | 当前时间 `YYYYMMDDTHHMMSS`；验收必须显式给 |
| `--out <路径>` | 结果 JSON 路径 | `runtime/results/accuracy_<id>.json` |

| 退出码 | 含义 |
| --- | --- |
| 0 | 期望集非空且全部 PASS |
| 1 | 至少一例非 PASS |
| 3 | 环境失败，`summary.reason` 给出错误码 |

退出 3 的错误码有八个：`RUN_ID_INVALID`、`RUN_ID_EXISTS`、`CSV_NOT_DEPLOYED`、`BUILD_FAILED`、
`OP_SKIPPED`、`BINARY_NOT_FOUND`、`LIST_FAILED`、`DUPLICATE_CASE`。修复后换一个新 run-id
重跑一次，A2 产物不动，旧 id 的 JSON 留着不碍事；仍 3 就停止。

输出 `results/accuracy_<id>.json`：身份字段 `run_id/op/family/soc/arch/device/repo`，
证据字段 `binary/binary_sha256/csv_path/csv_sha256/package_csv_sha256/gtest_filter`，
`cases[]` 每例 `name/gtest_name/status/ms/message`，`summary` 记 `expected` 与六种状态计数，
`exit_code`、`started`、`finished`。逐例 `status` 只取 `PASS/FAIL/SKIP/TIMEOUT/CRASH/MISSING`；
`MISSING` 表示期望用例没出现在 `--gtest_list_tests` 或结果 JSON 里。

## A4 性能

只有 A3 退出码 0 且 `check.json` 的 `checks.perf.comparable_pf` 大于 0 才运行：

```bash
cd <工作目录>/runtime && <python> verify_performance.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <id> \
  --skip-build --calls-per-case <calls_per_case>
```

性能期望集 = 运行时包 CSV 里 `TC_PF_` 用例中能按基线键配到非空 `gpu_ms` 的行。
配不到的行不跑，只写进 JSON 的 `ignored_no_ref`。脚本同样独占 `results/<id>/performance/`，
目录已存在时退出 3（`RUN_ID_EXISTS`）。

`--repo/--soc/--device` 与入口参数同义，其余参数如下：

| 参数 | 含义 | 默认值 |
| --- | --- | --- |
| `--case <case_name>` | 精确复跑 case_name，可重复 | 无 |
| `--filter <子串>` | 按 case_name 子串收窄性能集 | 无 |
| `--skip-build` | 复用上次编译产物，不调用 `build.sh` | 不带则先编译；验收固定带 |
| `--build-timeout <秒>` | 编译超时秒数 | 1800 |
| `--timeout <秒>` | 每个进程的超时秒数 | 3600 |
| `--msprof <路径>` | 覆盖 msprof 可执行文件路径 | 按 perf-protocol.md 的查找顺序 |
| `--repeats <N>` | msprof 采样次数 | 5 |
| `--calls-per-case <N>` | 一条 gtest 用例调用被测接口的次数，kernel 总时长除以它 | 1 |
| `--run-id <id>` | 结果运行标识 | 当前时间；验收必须与 A3 相同 |
| `--out <路径>` | 结果 JSON 路径 | `runtime/results/performance_<id>.json` |

通过判据：`npu_ms` 是 msprof 采到的 kernel 单次调用耗时的中位数（毫秒），
`ratio = gpu_ms / npu_ms`，`ratio ≥ 0.8` 为 PASS。阈值 `0.8` 由 accept 固定写进量具与
`manifest.json` 的 `threshold`，任务包不能覆盖；`--calls-per-case` 填的值原样写进 JSON 的
`calls_per_case`。

| 退出码 | 含义 |
| --- | --- |
| 0 | `summary.status` 为 `通过`；期望集为空时为 `NO_REF` |
| 1 | `不通过`：至少一例 FAIL，且没有证据缺口 |
| 2 | `证据不足`：任一 `NO_KERNEL/CRASH/TIMEOUT/MISSING` |
| 3 | 环境失败，比 A3 多 `BASELINE_INVALID/CSV_INVALID/MSPROF_NOT_FOUND` |

`CSV_INVALID` 指运行时包 CSV 某行列数与表头不一致，或 `TC_PF_` 行缺基线键列。退出 3 修复后
删掉 `results/<id>/performance/` 与 `results/performance_<id>.json`，用同一个 run-id 重跑
一次——A5 只认与精度 JSON 同 run-id 的性能 JSON，换 id 会让 A5 找不到精度证据。仍 3 就
进 A5，A5 记 `证据不足`。

输出 `results/performance_<id>.json`。A5 直接取 `summary.status`，不重算 kernel 数据；
采集序列、字段与状态见 [perf-protocol.md](perf-protocol.md)。

## A5 结论

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py verdict \
  --package <任务包目录> --repo <工程目录> --soc <soc> --device <device> \
  --run-id <id> --out <工作目录>/verdict
```

产物按三类落在 `--out` 下：`report/report.md`、`intermediate/verdict.json`（及各证据 JSON
副本）、`repro/`。`repro/` 含 `rerun.sh`（同参复跑命令清单；verify/verdict 的非零退出
是协议语义不中断脚本）与 `environment.json`（平台与身份链指纹）。

verdict 从 `<工作目录>/runtime/manifest.json`、`runtime/<op>_test.csv`、
`runtime/gpu_baseline.csv` 重新算出两个期望集，再核 `runtime/results/` 下本 run-id 的 JSON
和 `<工作目录>/check.json`。核的是下面这些，任一不满足都是 `证据不足`：

- 身份一致：精度与性能 JSON 的 `run_id/op/family/soc/device/repo` 与本次参数相同。
- **集合闭合**：JSON `cases` 的用例名集合与期望集完全相等，不多不少；精度期望集不能为空。
- 计数一致：`summary` 的各状态计数、`expected`、`status` 与 `exit_code` 都能由 `cases` 推出。
- 哈希：精度 JSON 的 `binary_sha256`、`csv_sha256` 非空，性能 JSON 的两者与精度 JSON 相同。
- 契约绑定：`check.json` 恰好一份，其 `package/repo/op/family/soc/device/package_csv_sha256/`
  `baseline_sha256/calls_per_case` 与 manifest 及本次参数一致，`evidence_id` 按 A2 的算法
  重算后与文件里的值相同。
- 精度 JSON 的 `exit_code` 不是 3。
- 性能 JSON 的 `calls_per_case` 与 manifest 相等（manifest 是唯一来源）。

| 退出码 | `verdict` | 条件 |
| --- | --- | --- |
| 0 | 通过 | check 退出 0，精度全 PASS，性能 `通过` 或 CSV 无 `TC_PF_` 行 |
| 1 | 不通过 | check 退出非 0、精度首轮有非 PASS，或性能 `不通过` |
| 2 | 证据不足 | 上面任一核不过、结果 JSON 缺失、性能 `证据不足` 或 `NO_REF` |

「有没有性能要求」的判据是 CSV 的 `TC_PF_` 行数（证据字段 `total_pf`），不是可比集大小
（`comparable_pf`）：无 `TC_PF_` 行才是「通过（无性能要求）」；有 `TC_PF_` 行而全配不到
基线时是 `NO_REF`。**NO_REF** 不能证明性能达标，所以总结论是 `证据不足`。A3 有非 PASS 时性能记 `未执行(精度未通过)`，总结论 `不通过`。

`timing_scope` 来自基线文件的元数据行 `# timing_scope=<值>`，说明 `gpu_ms` 的计时口径：
`kernel` 与 msprof 的 kernel 口径同类，缺省记 `unspecified`。不是 `kernel` 时性能状态追加
`(scope caveat)`，不改结论；`performance.base_status` 是未追加该后缀的原始状态。
协议见 [perf-protocol.md](perf-protocol.md)。

输出落 `verdict/` 下的三类布局。`intermediate/verdict.json` 顶层是 `run_id/op/soc/device`、
`runtime`（manifest 原样）、`accuracy`（`status/expected/executed/pass/fail/counts/attribution/`
`problems/case_names`）、`performance`（`status/base_status/expected/executed/total_pf/`
`comparable_pf/case_sets/timing_scope/scope_caveat/threshold/reason/problems`）、
`contract`、`evidence`、`layout` 与 `verdict`。`report/report.md` 三节：`精度`、`性能`、
`备注说明`；标题下的摘要块自动带总结论原因、契约状态、警告、完整身份链
（包 CSV/基线/二进制 SHA-256、性能键）与证据指引，性能节含可比集逐用例表
（kernel_us/gpu_ms/ratio/spread/verdict，超 30 条截断指向 JSON），只有
`备注说明` 由 agent 填。`repro/cases.csv` 覆盖精度、可比 PF、无参考 PF 三类用例，
标状态与是否执行。

## 工程查找规则

SoC 到 arch 的映射与 `build.sh` 一致：`ascend910b*`、`ascend910_93` → `arch22`，
`ascend950*` → `arch35`，`ascend310p*` → `arch20`。

部署 CSV 与测试二进制都按三条规则查找：

1. 直接目录：`test/<op>/<arch>/<op>_test.csv`。
2. 族目录：`test/*/<op>/<arch>/<op>_test.csv`。
3. 去掉首个类型字符的平铺目录：`test/<op[1:]>/<arch>/<op>_test.csv`。类型字符是 BLAS
   名首字母 s/d/c/z（单精度/双精度/单复/双复），如 `sgemm` → `test/gemm/<arch>/sgemm_test.csv`。

部署 CSV 多路径同时命中记 `CSV_AMBIGUOUS`。二进制 `<op>_test` 在 `<工程目录>/build/test/`
下**递归收集候选后唯一裁决**：0 命中记 `BINARY_NOT_FOUND`，多命中记 `BINARY_AMBIGUOUS`
并列出全部候选；`build_out/` 与 `out/` 与二进制查找无关（但可作运行库路径，见构建惯例）。

GTest 映射先运行 `--gtest_list_tests`：不缩进且以 `.` 结尾的行是 suite，后续缩进行是
测试名，两者拼接为完整名；最后一个 `/` 后的片段是 `case_name`，**按任务包期望集精确
匹配保留，不筛命名前缀**（结果解析同一口径）。重复映射记 `DUPLICATE_CASE`，退出 3。

## 复跑与归因

首轮每个非 PASS case 精确复跑一次，多个 `--case` 可以合并为一个进程：

```bash
cd <工作目录>/runtime && <python> verify_accuracy.py \
  --repo <工程目录> --soc <soc> --device <device> \
  --run-id <id>-rerun --case <case_name> --skip-build
```

| 首轮 | 复跑 | 归因 |
| --- | --- | --- |
| 非 PASS | PASS | `flaky` |
| 非 PASS | FAIL | `reproduced` |
| 非 PASS | 无复跑文件 | `not_rerun` |
| 非 PASS | 未出现或为其他状态 | `not_reproduced` |

复跑只用于归因，不把首轮不通过改写为通过。复跑 id 必须恰为 `<id>-rerun`：A5 只读
`accuracy_<id>-rerun.json`，别的名字一律按 `not_rerun` 归因。

## 停止报告模板

```text
阶段：<A1/A2/A3/A5>
状态：<阻塞·未验收/不通过·契约/证据不足>
原因：<稳定错误码与具体差异>
已有证据：<文件路径、退出码、哈希或用例名>
解除所需材料：<缺失工具、部署 CSV、构建产物、运行 JSON 或 C++ 文件>
```
