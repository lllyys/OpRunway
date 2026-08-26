# BLAS 验收故障排查

## Contents

- [构建失败](#构建失败)
- [SoC 被跳过](#soc-被跳过)
- [找不到 GTest](#找不到-gtest)
- [CSV 不一致](#csv-不一致)
- [GTest 名重复](#gtest-名重复)
- [运行超时](#运行超时)
- [设备被占用](#设备被占用)

## 构建失败

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `BUILD_FAILED`，日志缺 CANN 头或库 | 当前 shell 未加载 CANN 环境 | source 探测到的 `set_env.sh` 后重跑 A3 |
| `build.sh` 不可读 | `--repo` 指错目录或检出不完整 | 指向含可读 `build.sh` 的工程根 |
| 编译器或 CMake 不在 PATH | 工具链环境未加载 | 按工程要求加载编译环境，再跑 A1 |

不要仅看终端尾部。
保留并引用任务包 `results/build_<run-id>.log` 的首个编译错误。

## SoC 被跳过

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `OP_SKIPPED` | `skipped_tests.list` 记录实现或测试源码不支持该 SoC | 读 `op|reason` 原文，补源码或改用任务书支持的 SoC |
| `NOT_BUILT` | 已有构建清单来自别的 `--ops` | 不用 `--skip-build`，让 A3 按目标算子重编译 |

`--device` 由 `build.sh` 写入 `-DTEST_DEVICE_ID`。改设备号后必须重编译，不能只改运行参数。

## 找不到 GTest

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `BINARY_NOT_FOUND` | 三条构建目录规则都无二进制 | 检查构建日志和 `built_tests.list` |
| `LIST_FAILED` | 二进制依赖缺失、不可执行或列举超时 | 手动运行 `<bin> --gtest_list_tests` 并修动态库环境 |
| `MISSING` | 部署 CSV 的 case_name 未注册为参数化 GTest | 对照 `PrintCaseInfoString` 与 `case_name` 列 |

开发者自加的 `TEST_F` 不属于 CSV 验收集。
它不能补偿参数化用例缺失。

## CSV 不一致

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `CSV_NOT_DEPLOYED` | arch 目录未放任务包 CSV | 逐字节复制到 README 规定位置 |
| `CSV_MISMATCH` | 源码树仍是旧 CSV 或手改版 | 用任务包 CSV 覆盖，核对 SHA-256 后重跑 A2 |
| `CSV_AMBIGUOUS` | 三条规则同时命中多份 CSV | 删除错误部署，只保留工程实际消费的一份 |

不要通过修改任务包 CSV 去迎合已部署旧文件；任务包由 case-gen 机械重生成。

## GTest 名重复

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `DUPLICATE_CASE` | 两个完整 GTest 名映射到同一 case_name | 修测试实例化或 `PrintCaseInfoString`，确保全局唯一 |

先保存 `--gtest_list_tests` 全文，再定位重复的完整名。
不要在验收脚本里静默选第一个。

## 运行超时

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `TIMEOUT` | 用例、构建或列举超过默认时限 | 确认没有死锁后，按阶段增大 `--timeout` 或 `--build-timeout` |
| 性能采样超时 | 单例 shape 太大或 msprof 卡住 | 用 `--case` 单独复现，保留 profiler 原始目录 |

增大超时不能替代死锁诊断。复跑仍须使用同一 CSV 与二进制证据链。

## 设备被占用

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| A1 显示设备状态未知或疑似有进程 | `npu-smi` 格式无法解析或设备正被其他任务使用 | 手工核对 `npu-smi info`，协调空闲设备后重编译 |
| 运行期设备忙、初始化失败 | 编译期 device 与实际空闲设备不一致 | 用新的 `--device` 重新执行构建，不复用旧二进制 |

环境探测解析失败只报未知，不据此断言设备空闲。
真机执行前必须由操作者确认。
