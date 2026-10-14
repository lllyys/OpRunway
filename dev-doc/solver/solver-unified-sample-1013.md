# 统一 Solver 开发者样例

用户确认：新交付统一接入方式，开发者不按算子族切换样例。旧协议保持兼容，精度和性能判据不复制。

## 落地

- 新导出入口 export_solver_sample.py；样例只含 README 与 test，测试代码全部位于 test。
- 固定扩展生命周期协议覆盖26接口；新增sgetrf真实Host行主序调用示范，ipiv和info从实际接口回传。
- 共用 test/run_tests.py 路由构建、精度、性能、重判，分发同源运行代码，不分发 SKILL.md、内部报告或测试夹具。
- 修复harness对LU/QR固定Device的假设，支持明确声明dut-abi；Cholesky的v2矩形RHS输出维度补齐。
- 独立审查提出旧CMake说明问题，已改为统一入口并复核。未改精度算法、阈值或GPU基线。

## 验证

远端126项协议/编排/性能回归通过；后续48项统一样例与重判回归、最终27项独立导出与26接口尺寸检查通过（存在重叠，不累计为独立项数）。
真实CANN头编译检查通过，交付源码在950环境构建成功。sgetrf小规格真实调用30秒超时、无输出；msprof op未取得有效采样，性能报告为INSUFFICIENT。这不构成算子精度或性能通过，超时原因未定位。

交付与证据：ignored reports/solver-unified-sample-1013/；最终包 final/solver-developer-sample.tar.gz。
完整真机运行仍需排查环境/初始化/调用超时；未恢复或重置设备，未改开发者算子实现。
本轮与 casegen 说明修正一并提交，发布目标为 GitCode fork 的 solver-accept-1010 分支；不推上游。
