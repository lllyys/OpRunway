# 开发者适配模块协议 v1

开发者在 `test/<op>/<op>_adapter.cpp` 实现 `assets/adapter/solver_adapter.h` 的固定C入口。
与自测main分开，二者共用同一模块，不增加acceptance目录。验收方使用skill原版协议头和
固定执行器重新编译该源码，链接从交付源码构建的libops_solver.so；不运行开发者判定代码。
完整样例在assets/adapter-sample/test/cmatinv_batched。模块不提供golden或PASS/FAIL。
使用scripts/harness/export_cmatinv_sample.py --out <新目录>分发独立开发者样例，
自动附带固定协议、公共执行器及精度/性能测试入口；不含skill及内部验证记录。

## 生命周期与数据

1. descriptor声明协议版本、算子、dtype、真实DUT的Host/Device与布局、info形态和公开函数地址。
   验收者逐项对照公开头、实际实现和adapter调用行；声明和函数地址不是自动正确性证明。
2. create创建每case资源，handle/stream由harness提供，不得自行销毁；资源分配失败也必须
   回填部分state，destroy接受null和部分初始化state。workspace单位按公开接口核对。
3. reset每轮恢复传入的原始输入、输出和info。不得生成替代输入、返回缓存结果或调用目标算子。
4. execute只调用被测目标接口，适配返回码和DUT原始返回码分开；不得计算误差或伪造info。
5. harness同步同一stream后调用readback，取实际输出和info，最后destroy释放适配资源。

传输缓冲区固定为Host紧凑行主序，complex64为交错的两个float32（实、虚），不把
std::complex类型放进C协议。它不等于DUT布局：Device指针、列主序、lda/ldb padding、
批量指针数组由适配转换。传输容量显式传递，适配必须检查容量，不修改输入参数以“修复”非法调用。
调用参数n/nrhs/batch/lda/ldb/uplo与info_probe原样交给适配；v1仅支持已登记的正尺寸矩阵输出，
零尺寸、负维度、向量/多输出需要明确扩展协议与测试，不能宣称已覆盖全部边界。

普通potrs/potri因子由skill从实际A32独立准备，原始A32仍留给判据；singular_factor探针
输入本身已是因子，不再次分解。新数学运算仍需独立判据，不能由适配模块定义通过条件。
非零DUT返回码目前记录为执行错误；预期非零返回码的接口边界需专项协议扩展，不冒充精度FAIL。

## 构建与验收

沿用harness-run的构建命令。在run_harness.py中增加：

```bash
--adapter /delivered/repo/test/cmatinv_batched/cmatinv_batched_adapter.cpp
```

一次适配编译只服务一个算子。普通自测可用样例CMake目标链接同一源文件；验收编译入口
不消费开发者main，不允许以开发者自测报告替代输出。记录适配源码、协议头、公开头、编译命令、
执行件与实际DUT库指纹；后续运行沿用同版适配，不由agent临时改写适配源码。

--layout声明实际DUT布局；C++适配执行器单独记录transport_layout=host_compact_row_major。
未知适配、版本不符、容量不符、执行失败、缺info都停止或保留证据不足，不宣称接入成功。
当前真实验证样例为Host cmatinv，Device协议能力不能代替具体开发者适配模块的真机验证。

## 性能

使用同一适配模块的execute调用，经msprof op采集全部目标kernel，统计单次调用总耗时的算术平均。
create/reset/前置分解不能发射被筛选的目标kernel；仅分阶段并不能保证profiler排除它们，须核对
kernel清单与每调用launch次数。预热、输入恢复、同步搬运不作为API墙钟计入性能。
cmatinv样例入口profile_cmatinv_sample.py支持--adapter；没有GPU基线时只出实测，不判性能达标。
