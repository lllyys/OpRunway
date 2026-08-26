# BLAS 验收运行链

## Contents

- [输入与目录](#输入与目录)
- [A1 环境](#a1-环境)
- [A2 契约](#a2-契约)
- [A2′ 人工审阅](#a2-人工审阅)
- [A3 精度](#a3-精度)
- [A4 性能](#a4-性能)
- [A5 结论](#a5-结论)
- [工程查找规则](#工程查找规则)
- [复跑与归因](#复跑与归因)
- [停止报告模板](#停止报告模板)

## 输入与目录

工作目录只存 `env.json`、`check.json` 和最终结论。运行 JSON 与构建日志由任务包内的
verify 脚本写入 `<任务包>/results/`。
所有输入路径使用绝对路径，所有命令先进入工作目录。

| 输入 | 责任 |
| --- | --- |
| 六件任务包 | 固定 FACTS、CSV、README、基线与两个 verify 脚本 |
| 开发者工程 | 提供公开声明、C++ 测试、构建脚本与被测实现 |
| SoC | 决定部署 CSV 的 arch 目录 |
| device | 传给 `build.sh --device`，由 `-DTEST_DEVICE_ID` 编译期固定 |
| run-id | 连接精度、复跑、性能与最终结论 |

## A1 环境

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py env \
  --repo <工程目录> --soc <soc> --device <device>
```

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | 工程与 CANN 硬前置齐全 | A2 |
| 3 | build/frame/头文件/CANN 至少一项缺失 | 停止 |

`cmake`、`g++`、msprof、NPU 状态和 CPU golden 头文件是警告项。后续阶段仍会在实际使用点
给出确定错误，避免环境探测器误判非标准安装。

## A2 契约

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py check \
  --package <任务包目录> --repo <工程目录> --soc <soc> --device <device>
```

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | 六件、声明、部署 CSV 与已有构建清单一致 | A2′ |
| 2 | 契约不一致 | 停止并修包或开发者工程 |
| 3 | 同插件 case-gen 量具缺失 | 恢复完整插件后重跑 |

`check.json` 保存每个门的状态和完整差异。头文件尚未出现目标符号时明确记
`DECL_NOT_FOUND`。
不能把任务包里的事实表当作开发者声明的替代品。

## A2′ 人工审阅

在工程内定位并读完目标算子的 `param.h`、`test.cpp`、`npu_wrapper.h`。审阅记录至少包含：

- 三个实际文件路径；不存在时写缺失。
- README 每个投影列对应的读取语句与缺值失败路径。
- golden 调用、dtype、producer 链和 in-place 快照证据。
- 每个 verify token 对应的校验代码与阈值。
- 无法从源码证明的条款及所需材料。

## A3 精度

```bash
cd <工作目录> && cd <任务包目录> && <python> verify_accuracy.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <id>
```

输出 `<任务包>/results/accuracy_<id>.json`。退出码 0 表示期望精度集全部 PASS，1 表示
存在测试失败，3 表示部署、构建、二进制或 GTest 列举环境失败。

## A4 性能

只有 A3 首轮全部 PASS 才运行：

```bash
cd <工作目录> && cd <任务包目录> && <python> verify_performance.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <id> --skip-build
```

输出 `<任务包>/results/performance_<id>.json`。summary 状态直接取性能量具产生的
`通过/不通过/NO_REF/证据不足`，验收器不重算 kernel 数据。

## A5 结论

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py verdict \
  --package <任务包目录> --repo <工程目录> --soc <soc> --device <device> \
  --run-id <id> --out <工作目录>/verdict
```

| 退出码 | `verdict` | 条件 |
| --- | --- | --- |
| 0 | 通过 | 精度通过，性能通过或没有性能用例，契约证据无失败 |
| 1 | 不通过 | 首轮精度失败、性能失败或已有 check 失败 |
| 2 | 证据不足 | 结果缺失、集合不闭合、哈希缺失、NO_REF 或性能证据不足 |

输出 `verdict.json` 与 `report.md`。报告中的 A2′ 占位由 agent 填写，机器结论不手改。

## 工程查找规则

SoC 到 arch 的映射与 `build.sh` 一致。
具体映射是 910B/910_93 → arch22，950 → arch35，310P → arch20。

部署 CSV 与测试二进制都按三条规则查找：

1. 直接目录：`test/<op>/...`。
2. 族目录：`test/*/<op>/...`。
3. 去掉首个类型字符的平铺目录：`test/<op[1:]>/...`。

CSV 位于源码 arch 目录；二进制位于 `build/test` 的对应目录。多路径同时命中视为歧义。

GTest 映射先运行 `--gtest_list_tests`：不缩进且以 `.` 结尾的行是 suite，后续缩进行是
测试名；两者拼接为完整名，最后一个 `/` 后的片段是 `case_name`。重复映射必须失败。

## 复跑与归因

首轮每个非 PASS case 精确复跑一次，多个 `--case` 可以合并为一个进程：

```bash
cd <工作目录> && cd <任务包目录> && <python> verify_accuracy.py \
  --repo <工程目录> --soc <soc> --device <device> \
  --run-id <id>-rerun --case <case_name> --skip-build
```

| 首轮 | 复跑 | 归因 |
| --- | --- | --- |
| 非 PASS | PASS | `flaky` |
| 非 PASS | FAIL | `reproduced` |
| 非 PASS | 无复跑文件 | `not_rerun` |
| 非 PASS | 未出现或为其他状态 | `not_reproduced` |

复跑只用于归因，不把首轮不通过改写为通过。

## 停止报告模板

```text
阶段：<A1/A2/A3/A5>
状态：<阻塞·未验收/不通过·契约/证据不足>
原因：<稳定错误码与具体差异>
已有证据：<文件路径、退出码、哈希或用例名>
解除所需材料：<缺失工具、正确 CSV、声明、运行 JSON 或 C++ 证据>
```
