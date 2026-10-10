# cmatinv_batched 精度与性能测试样例

通过公开接口`aclsolverCmatinvBatched`调用实际算子库。精度和性能测试共用
`test/cmatinv_batched/cmatinv_batched_adapter.cpp`中的适配模块。

## 运行

需要CANN、C++17编译器、CMake、Python 3、NumPy、SciPy；性能测试还需要`msprof op`。
先加载CANN环境，并通过工程构建入口生成`build/libops_solver.so`。然后在本样例目录运行：

```bash
# 精度：默认n=8、batch=4，执行5轮
bash run_sample.sh /path/to/ops-solver ./precision-run --mode precision

# 性能：msprof op预热5次，采集30次目标kernel
bash run_sample.sh /path/to/ops-solver ./performance-run --mode performance

# 同时测试精度、性能，自定义用例和设备
bash run_sample.sh /path/to/ops-solver ./test-run --mode all --n 16 --batch 8 --seed 42 --device 0
```

输出目录必须是新目录。`PYTHON`可指定Python解释器。
测试结果在`<输出目录>/results/report.json`，输入、实际输出、info、执行日志及性能原始CSV
保存在同一结果目录。退出码0表示检查通过或性能采集完成，1表示检查失败，2表示运行错误。

## 精度检查

生成行对角占优的非对称复矩阵，以complex64输入算子。逐矩阵计算求逆残差：

`ratio = ||I - A × Ainv||₁ / (n × ||A||₁ × ||Ainv||₁ × 2⁻²⁴)`

范数为最大列绝对和，残差用complex128计算。CPU参考逆由SciPy的complex64
`cgetrf + cgetri`从同一实际输入计算。每个矩阵要求：

`ratio <= max(5 × cpu_ratio, 3 × mean(cpu_ratio))`

同时检查返回状态、info=0、NaN/Inf及5轮输出逐字节一致性。报告列出每个矩阵的残差和阈值。
本样例覆盖可逆矩阵；奇异矩阵和非法参数用例应按所交付接口的约定补充。

## 性能检查

使用`msprof op`的kernel模式，报告30次kernel耗时的算术平均值（ms）；中位数仅供参考。
输入准备及Host/Device搬运不计入该kernel耗时。性能调用也检查输出精度和30轮一致性。
默认kernel符号在`run_tests.py`中；实现更换符号时通过`--kernel-name`指定实际符号，
并用`--op-name`指定性能CSV的`Op Name`列所用名称。
此样例按每次调用一个目标kernel采集；多kernel实现需补齐采集清单和每次调用耗时求和。

可通过`--baseline /path/to/perf_baseline.json`传入同用例的GPU基线。GPU基线应使用
本次生成的`input.bin`，按相同kernel范围和均值口径采集。文件字段：

- `case`：与报告中的case完全相同（op、n、batch、seed、dtype、layout）。
- `reference`：GPU型号和基线来源。
- `timing_scope`：`kernel_only`；`statistic`：`mean`。
- `gpu_ms`：该用例的GPU平均耗时，单位ms。
- `required_speedup`：任务要求的最低`GPU耗时/NPU耗时`，例如任务要求0.35时填写0.35。

有基线时按`gpu_ms / mean_ms >= required_speedup`判定；无基线时报告`MEASURED`，只表示采集完成。

## 接入自己的实现

- `cmatinv_batched_adapter.cpp`：补全公开接口调用、Host/Device指针、布局转换、workspace及资源释放。
  当前样例使用Host指针、行主序complex64、标量info，必须与实际公开接口逐项核对。
- `include/solver_adapter.h`：固定适配接口；保持不变。适配模块不包含main、输入生成或判定逻辑。
- `run_tests.py`：精度与性能测试入口；更换算子时补充对应输入、数学判据和性能采集方式。
- `test/common/`：公共C++调用程序和进程管理；通过适配模块读取实际输出。
- `CMakeLists.txt`：按工程实际路径调整公开头文件与算子库位置。

提交时将适配模块保留在工程的`test/<op>/`目录，与自测共用。
