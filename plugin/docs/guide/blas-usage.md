# ops-blas 验收 skill 使用指南

两个平级 skill 把「社区算子任务书」变成「NPU 上的验收结论」：`repo-task-blas-case-gen`
出测试用例与脚本，`repo-task-blas-accept` 拿它们在 NPU 上验收。

```text
算子任务书 ─▶ repo-task-blas-case-gen ─▶ 六件包 ─▶ repo-task-blas-accept ─▶ verdict
              （纯 Python，不碰 NPU）              （+ 开发者工程，NPU 机上）
```

两个 skill 使用态解耦，单独都能用，谁也不预设「先去跑另一个」。唯一的硬耦合是那个六件包
——case-gen 的产出正好是 accept 的必需输入。

## 1. repo-task-blas-case-gen — 出用例与脚本

输入一份算子任务书，生成 CSV 测试用例和可直接运行的配套脚本。只生成文件，不在 NPU 上执行，
也不据此宣告验收通过。

- **何时用**：拿到 ops-blas 或 aclblas 社区算子任务书，需要出题库 CSV、自测脚本，或设计
  BLAS 接口用例时。
- **怎么调**：建议用 slash 命令 `/repo-task-blas-case-gen`；提到「出用例 / 任务包 / 自测
  脚本」时它也会按 description 自动触发。

### 输入

**只有任务书。** 含接口签名与参数表的算子任务书；运行时不读头文件或任何代码，接口事实全部
取自任务书。任务书缺 C 原型或参数表、无法唯一确定必需事实时，skill 停下报缺口，不猜。

环境只需 Python 3.8 或更高，不需要 NPU、CANN 或 ATK。

### 输出（六件，都不许手工编写）

| 文件 | 是什么 |
| --- | --- |
| `gen_csv.py` | 事实表 FACTS 与独立 CSV 生成器 |
| `<op>_test.csv` | CSV 驱动的 GTest 测试用例 |
| `verify_accuracy.py` | 精度验收入口 |
| `verify_performance.py` | msprof kernel 性能验收入口 |
| `README.md` | 交给开发者的契约 |
| `gpu_baseline.csv` | GPU 性能基线与元数据 |

### 流程与退出码

S0 前置检查 → S1 填 FACTS → S2 render → S3 check。`package.py check` 退出码 0 才算过；
2 表示逐条修 FACTS，3 表示修路径或 Python 语法。

### 提示词模板

```text
/repo-task-blas-case-gen
为算子 <op> 生成用例与脚本。
- 任务书：<路径，或直接粘贴任务书正文>
- 工作目录：<绝对路径>/test_script/<op>
- python 用 python3
只依据任务书填 FACTS。做完贴 S3 的 check 输出。
```

## 2. repo-task-blas-accept — 在 NPU 上验收

拿六件包和开发者的 ops-blas 工程，在 NPU 上过环境、契约、精度、性能几道门，给出证据化结论。
每道门保留机器证据，局部成功不替代总结论。

- **何时用**：已有 case-gen 的六件包，且开发者已实现该算子的工程，要在 NPU 上判精度与性能。
- **怎么调**：`/repo-task-blas-accept`。必须在有 NPU 与 CANN 的环境执行。

### 输入

- **六件包**：case-gen 的产出目录。`gpu_baseline.csv` 里 `gpu_ms` 有值的 `TC_PF_` 行才进
  性能期望集；同键重复行不报错，首行生效并记 warning。
- **开发者工程**：ops-blas 检出，含 `build.sh`、`include/`、`test/`，以及该算子的实现和
  三份 C++ harness（见下方「开发者交付物」）。
- `soc`（如 `ascend910_93`）、`device`（默认 0）、`python`（3.8+）。
- `产物目录`（可选）：A5 三类产物的写入位置，缺省 `<工作目录>/verdict`，只辖 A5 产物。
- 环境：CANN（`set_env.sh`、`msprof`）、cblas、一张空闲卡。

### 输出

结论取 **通过 / 不通过 / 证据不足**，退出码 0/1/2。A5 产物落产物目录下三类布局：
`report/report.md`（人读报告）、`intermediate/verdict.json` 等机械证据、`repro/` 复跑脚本。
`runtime/` 与 profiling 原始数据仍在工作目录，不随产物目录走。

### 流程

A1 环境 → A2 契约 → A2′ 人工审阅（对照 README 读三份 C++）→ A3 精度 → A4 性能 →
A5 结论（→ 按需 A4″ 复测，见下节）。

A4 每个有基线的用例起一次 msprof 采样（单次采集、默认无预热，op_summary 自动导出），
逐例进度行实时打印，200 例约 25 分钟。用例执行成功与否以每次采样的 gtest JSON 证据为准：
证据缺失或不合格记 `CRASH`，msprof 失败或无 kernel 行记 `NO_KERNEL`，都归证据不足，
不会误判 FAIL。预热可用 `--warmup N` 显式开启：采样前另起一个不计分的裸 GTest 预热进程；
默认 0——实测预热不改变 kernel 测量（见 perf-protocol 的「已实测与待实测边界」），
开与不开都不影响判据。

### 验收后复测与豁免

对个别用例的结论有疑问时不必重跑全量：验收支持按 case 复测与豁免，结果由 A5 折叠进
最终报告，首轮证据原样保留、每轮参数入档，被认可而不属篡改。

- **何时用**：首轮个别用例 FAIL 或证据不足，怀疑偶发波动、采样偏冷或属已知范围外问题。
- **判据三句话**：复测按 pass-once 裁——一个 case 在任一有效轮 PASS 即 PASS，永久有效；
  豁免把 case 移出裁决分母（逐例理由必填），之后再测同 case 即撤销豁免；每个复测轮跑完
  必须重跑 A5，否则不构成最终报告。
- **最小输入**：首轮工作目录 + 点名的 case；其余参数（轮号、设备、是否需要设备映射）由
  skill 的复测路由自动恢复。预热次数是复测旋钮（如 `--warmup 10`），默认 0。
- **报告样貌**：合并报告逐例展示首轮值、各轮摘要与有效状态；豁免例单列理由；未生效的
  轮（无效/中断）醒目列出，不会静默消失。
- 流程细节与完整契约（轮次、有效性、折叠规则、恢复）见
  [SKILL.md](../../skill/repo-task-blas-accept/SKILL.md) 的「A4″ 性能复测」与
  [retest-protocol.md](../../skill/repo-task-blas-accept/references/retest-protocol.md)。

### 提示词模板

```text
/repo-task-blas-accept
验收算子 <op>。
- 任务包目录：<case-gen 的六件包目录>
- 工程目录：<开发者 ops-blas 检出>
- soc=<如 ascend910_93>，device=0，python=python3，run-id 自取（如 t1）
- 产物目录：<可省；缺省 <工作目录>/verdict>
先 source <CANN 路径>/set_env.sh。A2′ 逐项核 param.h / test.cpp / npu_wrapper.h 对照 README。
做完贴 A5 结论、<产物目录>/report/report.md 与 <产物目录>/intermediate/verdict.json 的
绝对路径，以及 A1–A5 各阶段退出码。
```

复测时：

```text
/repo-task-blas-accept
对 run-id <id> 的验收结果复测。
- 工作目录：<首轮工作目录>
- 复测 case：<名，可多个>；预热 <N 次，可省>
（或：豁免 <名>，理由：<一句话>）
做完贴合并后的 A5 结论与 report.md 路径。
```

## 开发者交付物（accept 的前提）

accept 不代写 C++。开发者要在 `test/<family>/<op>/arch*/` 附近提供：

- `<op>_param.h` —— 按 README 列契约读 CSV 每列
- `<op>_test.cpp` —— CSV 驱动的参数化 GTest，可被 `--gtest_list_tests` 列成 `TC_*` 用例
- `<op>_npu_wrapper.h` —— 设备侧调用适配
- `<op>_golden.h` —— CPU 参考（cblas / lapacke）
- `CMakeLists.txt`，以及算子本身的实现

其中唯一和六件包硬对齐的，是 `param.h` 读的列名要与 CSV 的列一致。

## 串起来用

同一个算子：先 `/repo-task-blas-case-gen` 出六件包，再把包交给 `/repo-task-blas-accept`
在 NPU 上验收。case-gen 可在任意装了 Python 的机器跑，accept 必须在 NPU 机上跑。
