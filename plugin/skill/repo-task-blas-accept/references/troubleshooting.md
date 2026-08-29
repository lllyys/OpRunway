# BLAS 验收故障排查

## Contents

- [通用规则](#通用规则)
- [找不到部署 CSV](#找不到部署-csv)
- [未编译或构建失败](#未编译或构建失败)
- [用例缺失 MISSING](#用例缺失-missing)
- [无基线 NO_REF](#无基线-no_ref)
- [msprof 缺失](#msprof-缺失)
- [列名未被读取](#列名未被读取)

## 通用规则

三条规则各节都用到，不再重复：

- shell 调用之间不继承环境变量。要 CANN 环境时把 `source <set_env.sh> &&` 写在同一条命令
  最前面；`<set_env.sh>` 取 `env.json` 里 `name` 为 `CANN set_env.sh` 那项的 `detail`。
- A3 退出 3 修复后换新 run-id 重跑；A4 退出 3 修复后删掉 `runtime/results/<id>/performance/`
  与 `runtime/results/performance_<id>.json`，用同一个 run-id 重跑。细则见 run-chain.md。
- 验收不改工程 tracked 文件；部署 CSV、harness 与源码改动都由开发者做。

## 找不到部署 CSV

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `CSV_NOT_DEPLOYED`，说 SoC 无 arch 映射 | `--soc` 不在 910B/910_93、950、310P 三族 | 改用 `build.sh` 认的 SoC 名 |
| `CSV_NOT_DEPLOYED`，命中 0 份 | `test/` 下没有 `<op>_test.csv`，或放错 arch 目录 | 让开发者按三条规则之一部署 |
| `CSV_AMBIGUOUS` | 三条规则同时命中多份 | 只保留工程实际消费的一份 |

三条查找规则见 [run-chain.md](run-chain.md) 的「工程查找规则」。

## 未编译或构建失败

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| A2 警告 `未编译` | 构建清单还不存在 | 正常，A3 不带 `--skip-build` 会调用 `build.sh` 生成 |
| A2 `NOT_BUILT`，退出 2 | 已有清单来自别的 `--ops` 构建 | 按下面命令手动编译后重跑 A2 |
| A3 `BUILD_FAILED`，说清单不含目标算子 | `build.sh` 跑完但没登记 `<op>` | 读 `build.log` 与 `skipped_tests.list` |
| `OP_SKIPPED` | `skipped_tests.list` 记录源码不支持该 SoC | 读该文件里 `op\|reason` 原文，补源码或换 SoC |
| `BUILD_FAILED`，`build.log` 缺 CANN 头或库 | 当前 shell 未加载 CANN 环境 | 按下面命令带 `source` 重跑 A3 |

构建清单是 `<工程目录>/build/test/built_tests.list` 与同目录的 `skipped_tests.list`，由
`build.sh` 生成；`--ops <算子名>` 是 `build.sh` 选算子的参数，A3 以 `--ops=<op>` 调用它。
手动编译与带 CANN 环境重跑 A3 的完整命令：

```bash
source <set_env.sh> && cd <工程目录> && \
  bash build.sh --soc=<soc> --ops=<op> --device=<device>
source <set_env.sh> && cd <工作目录>/runtime && <python> verify_accuracy.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <新 id>
```

不要仅看终端尾部，保留并引用 `runtime/results/<id>/accuracy/build.log` 的首个编译错误。
`--device` 由 `build.sh` 写入 `-DTEST_DEVICE_ID`，改设备号后必须重编译，不能只改运行参数。

## 用例缺失 MISSING

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `MISSING`，说不在 `--gtest_list_tests` | 部署 CSV 少了这条用例，或 harness 没注册它 | 对照两份 CSV 的 `case_name`，让开发者补 |
| `MISSING`，说结果 JSON 无该用例 | 进程正常退出但没跑到它 | 看 `results/<id>/accuracy/gtest.json` 后 `--case` 复跑 |
| `BINARY_NOT_FOUND` | `build/test/` 下三条规则都无 `<op>_test` | 检查 `build.log` 与 `built_tests.list` |
| `LIST_FAILED` | 二进制依赖缺失、不可执行或列举超时 | 手动跑 `<bin> --gtest_list_tests`，修动态库环境 |

`<bin>` 是精度 JSON 的 `binary` 字段。期望集来自任务包 CSV，不是部署 CSV：部署 CSV 删掉的行
照样计入期望集并记 `MISSING`，A5 判 `证据不足`。开发者自加的 `TEST_F` 不属于期望集，
不能补偿参数化用例缺失。

## 无基线 NO_REF

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| A2 警告 `NO_REF: N 条 TC_PF_ 无可比基线` | 按基线键配不到行，或该行 `gpu_ms` 为空 | 只记录；这些用例不进期望集，不跑 |
| `checks.perf.comparable_pf` 为 0 | 所有 `TC_PF_` 都无基线，或本就没有 `TC_PF_` | 不跑 A4，A5 记性能 `通过`（无性能用例） |
| `comparable_pf` 为 0 却跑了 A4，A4 状态 `NO_REF` | 期望集本来就空 | 删掉性能 JSON，A5 即记 `通过` |
| `comparable_pf` 大于 0，A4 状态 `NO_REF` | `--case/--filter` 收窄成空 | 删掉 A4 两处产物，不带收窄参数同 id 重跑 A4 |

「A4 状态」指性能 JSON `runtime/results/performance_<id>.json` 的 `summary.status`；
「A4 两处产物」指这份 JSON 加 `runtime/results/<id>/performance/`。
基线键的推断规则见 [run-chain.md](run-chain.md) 的 A2。要让某条 `TC_PF_` 参与评判，
只能由任务包提供者补 `gpu_baseline.csv`，验收不代填 `gpu_ms`。

## msprof 缺失

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| A1 `msprof` 警告 | PATH 与 CANN 默认位置都没有 | 先不阻塞；A4 命令前加 `source <set_env.sh> &&` |
| A4 `MSPROF_NOT_FOUND`，退出 3 | 期望集非空但找不到可执行的 msprof | 按下面命令带 `source` 或 `--msprof <路径>` 重跑 A4 |
| 逐例 `NO_KERNEL` | msprof 导出失败或 `op_summary_*.csv` 无 kernel 行 | 看 `prof/<case>/` 下的日志 |

「导出」指 `msprof --export=on`，它从采集目录生成 `op_summary_*.csv`；`prof/<case>/` 在
`runtime/results/<id>/performance/` 下，`r<N>.log` 与 `r<N>.export.log` 是第 N 次采集与
导出的输出。msprof 的查找顺序与 op_summary 解析见 [perf-protocol.md](perf-protocol.md)。
带 CANN 环境重跑 A4 的完整命令（`--msprof` 只在 `source` 后仍找不到时加）：

```bash
source <set_env.sh> && cd <工作目录>/runtime && \
  rm -rf results/<id>/performance results/performance_<id>.json && \
  <python> verify_performance.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <id> \
  --skip-build --calls-per-case <calls_per_case> [--msprof <路径>]
```

GTest 自报的 ms 不能替代 msprof，`NO_KERNEL` 也不能以 0 代替。

## 列名未被读取

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| A2 警告 `COLUMN_NOT_READ: <列名…>` | 列名没在 harness 源码里以字面量出现 | 只记录，原样进 `report.md` |

harness 通过 `test/frame` 的 `ReadMap("列名")` 取列，列名不出现就静默取默认值。这是提示
不是门：列名被静默忽略意味着该列的取值没有真正驱动测试，读报告的人要据此判断精度 PASS
的覆盖面；验收不改 harness，也不改任务包 CSV 去迎合它。
