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

- **六件包**：case-gen 的产出目录。
- **开发者工程**：ops-blas 检出，含 `build.sh`、`include/`、`test/`，以及该算子的实现和
  三份 C++ harness（见下方「开发者交付物」）。
- `soc`（如 `ascend910_93`）、`device`（默认 0）、`python`（3.8+）。
- 环境：CANN（`set_env.sh`、`msprof`）、cblas、一张空闲卡。

### 输出

`verdict.json` 与 `report.md`，结论取 **通过 / 不通过 / 证据不足**。退出码 0 通过、
1 不通过、2 证据不足。

### 流程

A1 环境 → A2 契约 → A2′ 人工审阅（对照 README 读三份 C++）→ A3 精度 → A4 性能 → A5 结论。

### 提示词模板

```text
/repo-task-blas-accept
验收算子 <op>。
- 任务包目录：<case-gen 的六件包目录>
- 工程目录：<开发者 ops-blas 检出>
- soc=<如 ascend910_93>，device=0，python=python3，run-id 自取（如 t1）
先 source <CANN 路径>/set_env.sh。A2′ 逐项核 param.h / test.cpp / npu_wrapper.h 对照 README。
做完贴 A5 的 verdict.json 与 report.md。
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
