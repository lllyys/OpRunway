# repo-task-blas-accept 五分钟上手

输入是固定六件任务包、开发者 ops-blas 工程、目标 SoC 与设备号。输出是精度和性能 JSON、
`verdict.json` 与 `report.md`；A3/A4 必须在有健康 NPU 的目标环境运行。

运行规则在 `skill/repo-task-blas-accept/SKILL.md`，完整阶段与错误码在
`references/run-chain.md` 和 `references/troubleshooting.md`。

## 三条命令

第一条建立工作目录并检查环境：

```bash
mkdir -p <工作目录> && cd <工作目录> && \
python3 <skill>/scripts/accept.py env \
  --repo <工程目录> --soc <soc> --device <device>
```

第二条核六件、公开声明、部署 CSV 与已有构建清单：

```bash
cd <工作目录> && python3 <skill>/scripts/accept.py check \
  --package <任务包目录> --repo <工程目录> --soc <soc> --device <device>
```

第二条退出 0 后，先按 README 人工审阅 `param.h`、`test.cpp`、`npu_wrapper.h`。第三条是
首轮全通过的 happy path，依次运行精度、性能和机械结论：

```bash
cd <工作目录> && cd <任务包目录> && \
python3 verify_accuracy.py --repo <工程目录> --soc <soc> \
  --device <device> --run-id <id> && \
python3 verify_performance.py --repo <工程目录> --soc <soc> \
  --device <device> --run-id <id> --skip-build && \
cd <工作目录> && python3 <skill>/scripts/accept.py verdict \
  --package <任务包目录> --repo <工程目录> --soc <soc> \
  --device <device> --run-id <id> --out <工作目录>/verdict
```

精度出现非 PASS 时不要继续性能；按 `SKILL.md` 用 `--case` 精确复跑，再单独执行 verdict。

## 产物

任务包内 `results/` 保存构建日志、GTest JSON、accuracy JSON、performance JSON 与 msprof
原始目录。工作目录保存 `env.json`、`check.json`，最终目录保存以下两件：

```text
<工作目录>/verdict/verdict.json
<工作目录>/verdict/report.md
```

把 A2′ 审阅记录填进报告占位，不手改机器结论。

## 失败去哪看

| 现象 | 先看哪里 |
| --- | --- |
| A1 退出 3 | `env.json` 的 hard_failures 与工程/CANN 路径 |
| A2 退出 2 | `check.json` 的声明差异、CSV 哈希和构建清单 |
| A3 为 FAIL/MISSING/CRASH/TIMEOUT | accuracy JSON、构建日志与 GTest JSON |
| 性能为 NO_KERNEL 或证据不足 | `references/perf-protocol.md` 与 msprof 原始目录 |
| CSV_MISMATCH 或 DUPLICATE_CASE | `references/troubleshooting.md` 的对应条目 |
| verdict 为证据不足 | expected 集合、run-id、二进制和 CSV SHA-256 |
