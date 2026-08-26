---
name: repo-task-blas-accept
description: >-
  使用 BLAS 六件任务包和开发者 ops-blas 工程，在 NPU 上完成精度与性能验收并给出
  证据化结论。当用户提到 ops-blas、aclblas、任务包、验收、GTest 或 NPU 跑测时使用；
  只需生成任务包时改用 repo-task-blas-case-gen。
---

# BLAS 任务包验收

任务包 + 开发者工程 → 环境门 → 契约门 → 人工审阅 → 精度 → 性能 → 结论。
每道门保留机器证据，局部成功不替代总结论。

## 入口参数

| 参数 | 含义 | 要求 |
| --- | --- | --- |
| `任务包目录` | case-gen 生成的目录 | 含固定六件 |
| `工程目录` | 开发者 ops-blas 检出 | 含 `build.sh`、`include/`、`test/` |
| `soc` | 目标 SoC | 与 `build.sh --soc` 一致 |
| `device` | NPU 设备号 | 非负整数，默认 0；编译期固定 |
| `python` | 执行量具的解释器 | Python 3.8 或更高版本 |
| `工作目录` | 日志、检查结果和结论目录 | 绝对路径，先创建再进入 |

每条命令都必须带 `cd <工作目录> &&`。shell 调用之间不继承当前目录。

## 主流程

| 阶段 | 输入与处理 | 产物 | 出口与去向 |
| --- | --- | --- | --- |
| A1 环境 | 检查工具链、工程、CANN 与设备 | `env.json` | 0 进 A2；非 0 停止 |
| A2 契约 | 检查六件、声明、部署 CSV 与构建清单 | `check.json` | 0 进 A2′；2 停止 |
| A2′ 审阅 | 对照 README 审阅三份 C++ 文件 | 审阅记录 | 记录进入 A5 报告 |
| A3 精度 | 构建并运行非 `TC_PF_` 用例 | `accuracy_<id>.json` | 通过进 A4 |
| A4 性能 | 逐例用 msprof 采集 `TC_PF_` | `performance_<id>.json` | 证据进入 A5 |
| A5 结论 | 校验证据集合与哈希，机械裁决 | `verdict.json`、`report.md` | 0/1/2 |

完整参数、退出码和路径规则见
[run-chain.md](references/run-chain.md)。性能字段与状态见
[perf-protocol.md](references/perf-protocol.md)。
两份 reference 是运行态权威，不从开发文档补规则。

### A1 环境

```bash
mkdir -p <工作目录> && cd <工作目录> && \
<python> <skill>/scripts/accept.py env \
  --repo <工程目录> --soc <soc> --device <device>
```

退出码 0 才进入 A2。
非 0 报 `阻塞·未验收 @A1`，列出缺失项和解除阻塞所需材料。

### A2 契约

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py check \
  --package <任务包目录> --repo <工程目录> --soc <soc> --device <device>
```

退出码 0 才进入 A2′。退出码 2 报 `不通过·契约 @A2`；退出码 3 表示同插件的
case-gen 量具缺失，先恢复完整插件。

### A2′ 审阅

只读开发者工程的 tracked 源文件。`build.sh` 可在工程内写入 `build/`、`build_out/`、
`out/` 构建副产物，但不得改 tracked 文件。审阅 `param.h`、`test.cpp`、
`npu_wrapper.h` 时逐项对照任务包 README：

1. 每个 CSV 投影列是否显式读取，缺列或空值是否抛错。
2. golden 来源、dtype、producer 与 in-place 快照是否落实。
3. 每个 verify token 的对象、算法和阈值是否落实。

记录必须写明读过的实际路径、已证实条款和未能证明的条款。未能证明的条款不能写成通过。

### A3 精度

```bash
cd <工作目录> && cd <任务包目录> && <python> verify_accuracy.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <id>
```

若首轮出现 `FAIL/TIMEOUT/CRASH/MISSING`，每个失败 case 精确复跑一次：

```bash
cd <工作目录> && cd <任务包目录> && <python> verify_accuracy.py \
  --repo <工程目录> --soc <soc> --device <device> \
  --run-id <id>-rerun --case <case_name> --skip-build
```

多例复跑时重复 `--case`，仍只产生一份 `<id>-rerun` JSON。首轮失败不因复跑 PASS 被抹除；
归因使用 `reproduced/flaky/not_rerun/not_reproduced`。

### A4 性能

A3 全部 PASS 才运行：

```bash
cd <工作目录> && cd <任务包目录> && <python> verify_performance.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <id> --skip-build
```

A3 有失败时不运行，状态记 `未执行(精度未通过)`。GTest 自报 ms 不作性能依据。

### A5 结论

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py verdict \
  --package <任务包目录> --repo <工程目录> --soc <soc> --device <device> \
  --run-id <id> --out <工作目录>/verdict
```

退出码 0 是 `通过`，1 是 `不通过`，2 是 `证据不足`。把 A2′ 审阅记录替换进
`report.md` 的 `<A2′ 记录由 agent 填>` 占位，不改 `verdict.json` 的机械结论。

## 停止条件

遇到以下任一条件立即停止，不用局部成功替代完整验收：

- A1 非 0：报告 `阻塞·未验收 @A1`。
- A2 契约不一致：报告 `不通过·契约 @A2`。
- `results/` 缺当前 run-id 的精度 JSON：报告 `证据不足 @A5`。
- A3 期望集或执行集为零：报告 `证据不足 @A3`。

停止报告固定包含：`阶段`、`原因`、`已有证据`、`解除所需材料`。模板见
[run-chain.md](references/run-chain.md)。常见处置见
[troubleshooting.md](references/troubleshooting.md)。

## 参考资料

- [run-chain.md](references/run-chain.md) — A1–A5 命令、产物、退出码与复跑链
- [troubleshooting.md](references/troubleshooting.md) — 常见失败的现象、原因与处置
- [perf-protocol.md](references/perf-protocol.md) — msprof kernel 耗时协议与待实测边界
