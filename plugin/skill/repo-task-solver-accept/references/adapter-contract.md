# 开发者适配模块协议

开发者在 `test/<op>/<op>_adapter.cpp` 实现 `assets/adapter/solver_adapter.h` 的固定C入口。
与自测main分开，二者共用同一模块，不增加acceptance目录。验收方使用skill原版协议头和
固定执行器重新编译该源码，链接从交付源码构建的libops_solver.so；不运行开发者判定代码。
统一开发者样例由 `scripts/harness/export_solver_sample.py --out <新目录>` 导出，
新模块统一使用扩展生命周期接口；Cholesky与LU/QR共用同一入口，无需开发者选择协议版本。
样例仅含README与test目录，不含SKILL.md；精度/性能代码直接分发权威实现。
`assets/unified-sample/test/sgetrf` 提供真实Host接口示例，其他算子按协议补实际调用。
旧cmatinv导出入口仅用于已有交付兼容，不作为新交付通用样例。

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

一次适配编译只服务一个算子。普通自测通过统一样例test/run_tests.py编译并链接同一源文件；验收编译入口
不消费开发者main，不允许以开发者自测报告替代输出。记录适配源码、协议头、公开头、编译命令、
执行件与实际DUT库指纹；后续运行沿用同版适配，不由agent临时改写适配源码。

--dut-abi声明已核对的实际Host/Device内存归属；--layout声明实际DUT布局；C++适配执行器单独记录transport_layout=host_compact_row_major。
未知适配、版本不符、容量不符、执行失败、缺info都停止或保留证据不足，不宣称接入成功。
历史兼容样例已验证Host cmatinv，Device协议能力不能代替具体开发者适配模块的真机验证。

## 性能

使用同一适配模块的execute调用，经msprof op采集全部目标kernel，统计单次调用总耗时的算术平均。
create/reset/前置分解不能发射被筛选的目标kernel；仅分阶段并不能保证profiler排除它们，须核对
kernel清单与每调用launch次数。预热、输入恢复、同步搬运不作为API墙钟计入性能。
新交付统一通过test/run_tests.py performance运行；缺GPU基线保留证据不足。
历史cmatinv入口profile_cmatinv_sample.py仅供旧交付兼容。

## LU/QR v2 数据契约

LU/QR descriptor 的 version 为 `SOLVER_ADAPTER_VERSION_V2`，调用 create_v2、
reset_v2、readback_v2；execute、destroy 与 descriptor 沿用公共入口。
Cholesky 与已有样例可继续使用 v1。v2 的 base.struct_size 为 sizeof(SolverAdapterCaseV2)。

| 字段或缓冲区 | 含义 |
| --- | --- |
| m/n/k、side/trans | 矩形维度、反射子数、左右乘及转置；side 为 L/R，trans 为 N/T/C |
| base.lda/base.ldb、ldc/ldx | DUT 实际步长；gels 的 ldda/lddb/lddx 分别映射 lda/ldb/ldx |
| a/b/out | Host 紧凑行主序；传入字节数为整个 batch 总量，适配负责 padding 与布局转换 |
| in_ipiv/out_ipiv | int32、1 基主元；count 为整个 batch 元素数 |
| in_tau/out_tau | 与矩阵同 dtype 的反射子系数；Case 中 count 为元素数，reset/readback 参数为字节数 |

| 算子族 | 输入 a、b、附加输入 | 输出 out、附加输出 |
| --- | --- | --- |
| getrf | A，m×n | 打包 LU，m×n；ipiv 长 min(m,n) |
| getri/getribatched | LU32，n×n；ipiv 长 n | 逆矩阵，n×n |
| matinvbatched | A，n×n | 逆矩阵，n×n |
| geqrf | A，m×n | 打包 QR，m×n；tau 长 min(m,n) |
| gqr | 打包反射子 A，m×n；tau 长 k | Q，m×n |
| mqr | 打包反射子 A，nq×k；b 为 C，m×n；tau 长 k | op(Q)·C 或 C·op(Q)，m×n |
| gels | A，m×n；b 为 B，m×nrhs | X，n×nrhs |

mqr 的 nq 在左乘时为 m、右乘时为 n。批量数组依次拼接各矩阵，附加数组同序。
传输 dtype、大小与公开接口须逐项核对；reset 每轮恢复 ipiv/tau，readback 取真实附加输出。
这些输出与 out/info 一起做字节一致性检查，失败证据保存附加数组供独立重判。

QR 的 info_probe=bad_param 要求适配对公开接口第一个可校验参数执行非法调用，
原样回传实际 info，与任务用例 k_expected 核对；若该公开接口不能产生约定状态，
停止并报告契约不兼容，不在适配中填写预期 info。正常调用的维度不得因此修改。
本协议的局部验证不代替各开发者 Device 适配的真机验证。
