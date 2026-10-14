# sgetrf 源码与接入样例

本工程演示真实算子实现、公开接口、适配模块及构建入口的关系。

- src/sgetrf/：真实LU分解实现；src/utils/：必要依赖。
- include/：原工程公开头。样例只构建sgetrf及handle/stream管理接口，其他声明不代表提供对应实现。
- test/sgetrf/：适配源码、固定协议头和接入说明。test下只有算子目录。
- CMakeLists.txt、build.sh：最小构建入口，无额外Python测试工具。
- LICENSE、SOURCE.md：原代码许可证及来源。

## 构建

原实现支持A2/A3，不支持950PR/950DT；默认构建目标为A3（ascend910_93）。需要已安装的CANN、CMake及C++17编译器。加载CANN的set_env.sh后执行：

```bash
bash build.sh --ops=sgetrf --soc=ascend910_93
```

构建目标可通过`--soc`更改。面向A5（950）开发时，在替换或适配算子实现后使用：

```bash
bash build.sh --ops=sgetrf --soc=ascend950
```

仅修改`--soc`不会使所附A2/A3旧实现支持A5；该实现在A5实测中调用`aclrtGetHardwareSyncAddr`返回不支持。
A5开发者可沿用`test/sgetrf/`的固定适配协议，并按实际公开接口调整适配调用、内存归属及布局；算子实现须满足A5任务书要求。

产物为build/libops_solver.so及build/libsgetrf_adapter.so。构建同时检查适配模块与真实公开接口的链接，安装库与公开头至build_out供验收构建入口读取。
开发自己的算子时，替换src中的实现和对应公开接口，按test/sgetrf/README.md补齐适配模块。
验收方通过适配模块独立完成精度、确定性和msprof op性能检查；本工程不包含验收工具。

源码保持原实现，Host行主序输入输出。构建成功不等于算子精度或性能通过，
本样例已在A3验证普通4×4用例精度及5轮确定性，并完成msprof op采集。
所附旧实现尚未支持奇异矩阵的info回报：对应用例预期info=1，实际为0，未满足新任务书要求。
本工程用于演示接入，开发者仍需按任务书补齐算子能力；不代表完整验收通过。
