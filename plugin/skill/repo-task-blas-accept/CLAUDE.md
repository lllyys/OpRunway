# repo-task-blas-accept 开发约束

## 专属红线

- 开发者工程的 tracked 源文件只读；验收不得 patch C++、CSV、构建脚本或公开头文件。
- `build.sh` 在工程内产生的 `build/`、`build_out/`、`out/` 是允许的构建副产物。
- 不修改 host 环境、设备状态或其他进程；缺前置时报告阻塞，不代装、不 kill、不 reset。
- 结论只认部署 CSV 和本 run-id 的 JSON；旧结果、包内未部署 CSV 与终端摘要不能替代。

## 真机验证过的事实

| 事实 | 出处 |
| --- | --- |
| `--device` 写入编译期 `TEST_DEVICE_ID` | `build.sh:17,80-81,276` |
| CSV 从 `__FILE__` 同目录的同名文件加载 | `test/frame/csv_loader.h:115-122` |
| A1 env 正确检出真机工具链（cmake/g++/build.sh/frame/header/CANN/msprof/cblas） | 实测（A3，sger） |
| npu-smi 在容器内解析不出设备（记"未知"，非门禁）——探针脆弱，待改 | 实测（A3） |
| 精度 harness 真机通:build-skip→list→名字映射→gtest json→结果 JSON | 实测（A3，sger TC_L0 全 PASS） |
| 性能 msprof 流水线真机通:warmup + 5 次 collect+export + 解析 + 中位数 | 实测（A3，sger） |
| kernel_us 可复现:5 次独立采样中位数 spread 1.3–2.4% | 实测（sger 1024²=613μs、512×2048=1139μs、256²=197μs） |
| accept 运行链用现成算子只验机械,不验真算子正确性（需契约 C++ test） | 设计边界 |
