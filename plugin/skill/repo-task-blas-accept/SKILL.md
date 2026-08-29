---
name: repo-task-blas-accept
description: >-
  使用 BLAS 任务包（一个 <op>_test.csv 加一张 gpu_baseline.csv）和开发者 ops-blas 工程，
  在 NPU 上完成精度与性能验收并给出证据化结论。当用户提到 ops-blas、aclblas、任务包、
  验收、GTest 或 NPU 跑测时使用；只需生成任务包时改用 repo-task-blas-case-gen。
---

# BLAS 任务包验收

任务包 + 开发者工程 → 环境门 → 有无门 → 精度 → 性能 → 结论。
每道门保留机器证据，局部成功不替代总结论。

**任务包**就是一个目录，里面恰好一个 `<op>_test.csv`（契约用例集；文件名去掉 `_test.csv`
就是 `<op>`）加一张 `gpu_baseline.csv`（GPU 基线表）。目录里别的文件 `scripts/accept.py`
（下称 accept）一概不读。**量具**指 accept 在 A2 渲染到 `<工作目录>/runtime/` 的
`verify_accuracy.py` 与 `verify_performance.py` 两个脚本，A3、A4 就是运行它们。

开发者工程侧有两样东西会反复提到。**部署 CSV** 是开发者放在
`<工程目录>/test/…/<arch>/<op>_test.csv` 的那份用例表，harness 实际消费它，查找规则见
[run-chain.md](references/run-chain.md) 的「工程查找规则」。**harness** 是工程里驱动 GTest
的 C++ 测试代码，即 `<op>_param.h`、`<op>_test.cpp`、`<op>_npu_wrapper.h` 三文件，位于
部署 CSV 所在 arch 目录的上一级。

用例按 `case_name` 前缀分两类：`TC_PF_` 开头的是性能用例，其余 `TC_` 开头的是精度用例。

## 入口参数

| 参数 | 含义 | 要求 |
| --- | --- | --- |
| `任务包目录` | 含上述两件的目录 | 绝对路径；`*_test.csv` 恰好一个 |
| `工程目录` | 开发者 ops-blas 检出 | 含 `build.sh`、`include/`、`test/` |
| `soc` | 目标 SoC | 与 `build.sh --soc` 一致，如 `ascend910b3` |
| `device` | NPU 设备号 | 非负整数，默认 0；编译期固定 |
| `python` | 执行 accept 与量具的解释器 | Python 3.8 或更高版本 |
| `工作目录` | 检查结果、运行时包和结论目录 | 绝对路径，先创建再进入 |
| `calls_per_case` | harness 一条 GTest 用例调用被测接口的次数 | 正整数，初值 1；A2′ 核对，A2 与 A4 填同一个值 |

命令里的 `<skill>` 是本 SKILL.md 所在目录的绝对路径，`<skill>/scripts/accept.py` 由它定位。
每条命令都必须带 `cd <工作目录> &&`：shell 调用之间不继承当前目录，也不继承环境变量，
需要 CANN 环境时把 `source <set_env.sh> &&` 放在同一条命令的最前面。

## 主流程

| 阶段 | 输入与处理 | 产物 | 出口与去向 |
| --- | --- | --- | --- |
| A1 环境 | 检查工具链、工程、CANN 与设备 | `env.json` | 0 进 A2；3 停止 |
| A2 有无门 | 任务包两件、部署 CSV、构建清单、量具模板；生成运行时包 | `check.json`、`runtime/` | 0 进 A2′；2/3 停止 |
| A2′ 三文件 | 读 `check.json` 里 harness 三文件的有无，核对 `calls_per_case` | 同 `check.json` | 记录后进 A3 |
| A3 精度 | 构建并运行全部精度用例 | `runtime/results/accuracy_<id>.json` | 0 进 A4；1 复跑后进 A5；3 修复后换 id 重跑 |
| A4 性能 | 逐例用 msprof 采集有基线的性能用例 | `runtime/results/performance_<id>.json` | 0/1/2 进 A5；3 修复后重跑一次 |
| A5 结论 | 校验集合、计数与哈希，机械裁决 | `verdict/verdict.json`、`verdict/report.md` | 0/1/2 |

**运行时包**是 A2 在 `<工作目录>/runtime/` 生成的目录，装着任务包 CSV 副本、规范化的
基线、渲染出的两个量具和 `manifest.json`，A3、A4 都在这个目录里执行。**有无门**只裁
文件有无，不看内容。**期望集**是本轮必须出结果的用例名集合：精度期望集是全部精度用例，
性能期望集是能配到非空 `gpu_ms` 基线的性能用例，精确定义见 run-chain.md 的 A3 与 A4。

完整参数、退出码、JSON 字段和路径规则见 [run-chain.md](references/run-chain.md)，
性能采集与状态见 [perf-protocol.md](references/perf-protocol.md)；两份 reference 是
运行态权威，不从开发文档补规则。

### A1 环境

```bash
mkdir -p <工作目录> && cd <工作目录> && \
<python> <skill>/scripts/accept.py env \
  --repo <工程目录> --soc <soc> --device <device>
```

退出码只有 0 和 3，其它值视同停止。0 进 A2；3 报 `阻塞·未验收 @A1`，列出 `env.json`
里 `hard_failures` 的项和解除阻塞所需材料。

### A2 有无门

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py check \
  --package <任务包目录> --repo <工程目录> --soc <soc> --device <device> \
  --calls-per-case <calls_per_case>
```

退出码只有 0、2、3，其它值视同停止。0 进 A2′，此时 `<工作目录>/runtime/` 已生成；
2 报 `不通过·契约 @A2`；3 表示同插件的 case-gen 模板缺失，报 `阻塞·未验收 @A2`，
先恢复完整插件。重跑 check 会覆盖 `runtime/` 与 `check.json`，`runtime/results/` 不受影响。
标准输出里的 `列名未读取:` 与 `性能期望集:`（有基线可比的 `TC_PF_` 条数）两行只是记录，
不影响退出码。

### A2′ 三文件

A2 已把 harness 三文件的有无写进 `check.json`：

```bash
cd <工作目录> && <python> -c \
  "import json; print(json.load(open('check.json'))['checks']['harness'])"
```

`missing` 非空只记警告，不停止。接着核对 `calls_per_case`，数法固定三步：

1. 打开 `<op>_npu_wrapper.h`，路径在 `checks.harness.files` 的 `npu_wrapper.h` 项。
2. 数 `aclblas<Op>(` 被调用的次数，`<Op>` 是首字母大写的 `<op>`，如 `cherk` 对应
   `aclblasCherk(`；一条用例的总次数 = warm-up 调用次数 + 正式调用次数，只看这一个文件。
3. 与 A2 所填不同时用正确值重跑 A2；文件缺失时按 1 计。

把读过的文件路径与调用次数（或「wrapper 缺失，按 1 计」）写进 `report.md` 的 `审阅备注`。

### A3 精度

```bash
cd <工作目录>/runtime && <python> verify_accuracy.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <id>
```

**run-id** 是一轮运行的标识，串起精度、复跑、性能与结论；只含字母、数字、点、下划线、
连字符，建议 `<op>-<YYYYMMDD-HHMM>`。首轮出现 `FAIL/SKIP/TIMEOUT/CRASH/MISSING` 时，
每个失败 case 精确复跑一次：

```bash
cd <工作目录>/runtime && <python> verify_accuracy.py \
  --repo <工程目录> --soc <soc> --device <device> \
  --run-id <id>-rerun --case <case_name> --skip-build
```

复跑 id 必须恰为 `<id>-rerun`，A5 只认这个名字；多例复跑时重复 `--case`，仍只产生一份
`<id>-rerun` JSON。首轮失败不因复跑 PASS 被抹除，归因四值见 run-chain.md 的「复跑与归因」。
退出码 3 是环境失败：按 troubleshooting.md 修复后换一个新 run-id 重跑一次（同 id 会
`RUN_ID_EXISTS`，A2 产物不动），仍 3 就停止并报 `证据不足 @A3`。

### A4 性能

A3 退出码 0 且 `check.json` 的 `checks.perf.comparable_pf` 大于 0 才运行。msprof 是
CANN 自带的性能采集命令，采集协议与通过判据见 [perf-protocol.md](references/perf-protocol.md)：

```bash
cd <工作目录>/runtime && <python> verify_performance.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <id> \
  --skip-build --calls-per-case <calls_per_case>
```

A3 有失败时不运行，A5 会把性能记为 `未执行(精度未通过)`；`comparable_pf` 为 0 时也不运行，
A5 记 `通过`（无性能用例）。GTest 自报 ms 不作性能依据。退出码 3 是环境失败：按
troubleshooting.md 修复后删掉 `runtime/results/<id>/performance/` 与
`runtime/results/performance_<id>.json`，用同一个 run-id 重跑一次（A5 只认与精度 JSON 同
run-id 的性能 JSON）；仍 3 才进 A5，A5 记 `证据不足`。

### A5 结论

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py verdict \
  --package <任务包目录> --repo <工程目录> --soc <soc> --device <device> \
  --run-id <id> --out <工作目录>/verdict
```

退出码 0 是 `通过`，1 是 `不通过`，2 是 `证据不足`。只把 A2′ 的记录填进 `report.md` 的
`审阅备注`，不改 `verdict.json` 的机械结论。

## 结论报告

A5 跑完后回给用户的内容固定四项：

- `verdict.json` 的 `verdict` 字段值（`通过`/`不通过`/`证据不足`）。
- `<工作目录>/verdict/report.md` 与 `verdict.json` 的绝对路径。
- A1–A5 各阶段退出码，A3 有复跑时一并列出。
- `审阅备注` 摘要：读过的 wrapper 路径、`calls_per_case`、harness 缺件与列名未读取。

## 停止条件

遇到以下任一条件立即停止，不用局部成功替代完整验收：

- A1 退出码 3：报告 `阻塞·未验收 @A1`。
- A2 退出码 2：报告 `不通过·契约 @A2`；退出码 3：报告 `阻塞·未验收 @A2`。
- A1、A2 出现上面没列的退出码：视同停止，报告 `阻塞·未验收 @<阶段>`。
- A3 修复后重跑仍退出 3：报告 `证据不足 @A3`，原因取 JSON 的 `summary.reason`。
- A5 退出码 2：报告 `证据不足 @A5`，原因取 `verdict.json` 各段的 `problems`。

停止报告固定五项：`阶段`、`状态`、`原因`、`已有证据`、`解除所需材料`。模板见
[run-chain.md](references/run-chain.md)。常见处置见
[troubleshooting.md](references/troubleshooting.md)。

## 参考资料

- [run-chain.md](references/run-chain.md) — A1–A5 命令、参数、产物、退出码、JSON 字段与复跑链
- [troubleshooting.md](references/troubleshooting.md) — 常见失败的现象、原因与处置
- [perf-protocol.md](references/perf-protocol.md) — msprof kernel 耗时协议与待实测边界
