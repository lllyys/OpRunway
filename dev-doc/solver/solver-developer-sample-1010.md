# 开发者精度与性能样例（2026-10-10）

用户要求样例仅包含开发者需要的信息，实际用于精度和性能自测；后续明确要求commit/push本版。

## 改动

- 样例README只保留运行、精度公式、性能口径和适配必补项；不附skill、内部验证记录、旧性能数值。
- run_sample.sh用CMake构建自测程序，调用同一份test/cmatinv_batched适配源码。输入生成和判定均在Python入口，适配模块仍无main及判定。
- 精度：逐complex64矩阵用SciPy cgetrf/cgetri取CPU参考残差，实际输入/输出升complex128计算求逆残差；按max(5*cpu_ratio,3*cpu_ratio_mean)检查，另查info、NaN/Inf及五轮一致性。
- 性能：msprof op、5次预热/30条目标kernel、算术平均；同case与口径的GPU基线可判speedup阈值，没有基线仅MEASURED。性能调用也查精度和30轮一致性。
- export_cmatinv_sample.py复制原版协议、公共执行器和进程管理，生成独立小包；公共运行源码无另写分叉。

## 验证与限制

独立Codex audit→fix→verify通过：修正--kernel-name改变后CSV名称仍写死，新增--op-name并覆盖回归；构建错误码统一2。

远端默认n8/batch4完整包CMake构建、精度与性能运行退出0：根据入口收敛条件，精度和性能调用输出检查均通过；30次mean0.3284219014ms、median0.3303566135ms。原始结果留在本轮自有容器/work/developer-sample-final/results，日志/work/developer-sample-final.log。

后续SSH连接被关闭，故未取得新回归套件/work/developer-sample-suite.log最终结果，也未确认n16补充检查的执行结果；前一版469项不代表本版新增5项回归已验证。连接恢复后优先取回日志和报告，并停止保留自有容器。最后纯元数据对齐call_shape到outofplace_a_ainv以及README基线输入说明，不改变已实跑数值/性能逻辑。

本地归档：reports/cmatinv-developer-sample-1010/final/cmatinv-sample.tar.gz（开发者包，不含本记录）。十包和核心验收判据不变。样例为可逆复矩阵自测，不声称已覆盖完整任务的奇异/非法参数用例。
