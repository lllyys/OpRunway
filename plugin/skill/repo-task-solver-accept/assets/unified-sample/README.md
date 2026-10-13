# Solver 开发者接入样例

同一套适配协议和测试入口覆盖 Cholesky 十接口、LU/QR 十六接口。所有测试代码位于
`test/`，不需要修改算子源码目录。任务包另行提供，包含用例、冻结CPU参考值和GPU性能基线。

## 文件与开发者职责

- `test/solver_adapter.h`：固定协议头，保持不变。新模块统一实现其中扩展生命周期接口（名称带 `_v2`），无需按算子族选版本。
- `test/sgetrf/sgetrf_adapter.cpp`：可读的真实调用示例，使用 `aclsolverSgetrf` 的 Host、紧凑行主序接口，输出 LU、1基 `ipiv` 和实际 `info`。这不是其他算子的实现。
- `test/run_tests.py`：所有算子共用的构建、精度、性能与重判入口。
- `test/common/`：与验收工具同源的输入生成、执行器和判定代码；无需开发者修改或补写判据。

为自己的算子提供 `test/<op>/<op>_adapter.cpp`，与自测共享。必须完成：

1. descriptor：填写算子名、dtype、实际 Host/Device 内存归属、布局、info形态和公开函数地址。
2. create_v2：检查容量，分配资源、workspace，处理部分分配失败；handle/stream由调用方管理。
3. reset_v2：恢复本轮输入、输出和info，转换布局及padding，复制ipiv/tau；不得调用目标算子。
4. execute：调用实际公开接口，分别返回适配状态和未经改写的算子返回码。
5. readback_v2：取回实际矩阵、info、ipiv/tau；不得填写预期结果。
6. destroy：释放资源，接受空指针及部分初始化状态。

接口签名、参数顺序、内存归属、步长、workspace单位和批量指针数组必须核对公开头与实现。
示例sgetrf仅支持batch=1、lda=n、m≤8192且m×n≤INT32_MAX；其他实现的约束由其适配模块核对。
info契约用例必须真实调用：奇异输入直接传入；QR的bad_param需执行约定的非法参数调用。
示例原样回传DUT的info，若实现不报告奇异位置，会被任务包判失败。

## 统一数据契约

传输缓冲均为Host紧凑行主序；complex64为交错float32实部/虚部。实际DUT可使用Device、
列主序或padding，由适配转换。字节数及附加数组count包含整个batch。ipiv为int32、1基；
tau与矩阵同dtype。未使用的附加数组长度为0。m/n/k、side/trans和各leading dimension
已经在固定结构中，不因新增同类算子改变协议。

| 算子族（含实/复） | 输入 | 矩阵输出 | 附加输出 |
| --- | --- | --- | --- |
| potrf / potrfBatched | A | 因子 | info；批量为数组 |
| potrs / potrsBatched | 因子A、B | X，n×nrhs | 标量info |
| potri | 因子A | 逆矩阵 | info |
| getrf | A，m×n | LU，m×n | ipiv[min(m,n)]、info |
| getri / getribatched | LU、ipiv | 逆矩阵 | info；批量为数组 |
| matinvbatched | A | 逆矩阵 | 批量info数组 |
| geqrf | A，m×n | QR，m×n | tau[min(m,n)]、info |
| sorgqr / cungqr | 反射子A、tau[k] | Q，m×n | info |
| sormqr / cunmqr | 反射子A、tau[k]、C | op(Q)C或Cop(Q) | info |
| gels | A，m×n、B，m×nrhs | X，n×nrhs | info |

mqr的反射子矩阵为nq×k，左乘nq=m，右乘nq=n。前置分解由公共输入准备完成，不计入目标性能。
本表是26接口的数据覆盖，不代表已交付26份真实适配或已完成26接口真机验收。

## 构建与精度测试

先加载CANN环境。需要C++17编译器、Python 3、NumPy、SciPy、threadpoolctl。
从本样例目录运行，下列路径替换为实际路径。构建输出必须为工程的build/libops_solver.so：

```bash
python3 test/run_tests.py build --repo /path/to/repo --ops sgetrf --soc ascend950 --out /path/to/run/build.json
python3 test/run_tests.py precision --package /path/to/sgetrf-package \
  --repo /path/to/repo --adapter /path/to/sample/test/sgetrf/sgetrf_adapter.cpp \
  --dut-abi host --layout row_major --provenance /path/to/run/build.json \
  --device 0 --rerun 5 --work-dir /path/to/run/work \
  --evidence-dir /path/to/run/evidence --report /path/to/run/precision.json
```

其他算子沿用命令，只替换任务包、适配模块及真实ABI/布局。正式提交将模块放在被测工程test/<op>/下。
精度使用任务包冻结参考值，实际32位输入/输出升64位计算残差；确定性独立比较输出、info及ipiv/tau。
返回0仅表示所选检查通过，1表示失败，2表示契约/输入错误。`--limit`仅用于调试，不表示全量通过。
失败现场可用 `python3 test/run_tests.py rejudge --rejudge /path/to/evidence-case --report /path/to/rejudge.json` 复核。

## 性能测试

性能与精度使用同一个适配模块。准备kernel-map.json：

```json
{"operators":{"sgetrf":[{"symbol":"实际编译kernel符号","op_name":"实际CSV的Op Name","launches_per_call":1,"target_only":true}]},"cases":{}}
```

逐项核对一次调用的全部目标kernel及launch次数；不同规格可在cases中按case_id覆盖。
排除输入准备与前置分解，不能省略目标kernel，也不能把准备kernel计入。

```bash
python3 test/run_tests.py performance --package /path/to/sgetrf-package \
  --repo /path/to/repo --adapter /path/to/sample/test/sgetrf/sgetrf_adapter.cpp \
  --dut-abi host --layout row_major --provenance /path/to/run/build.json \
  --kernel-map /path/to/kernel-map.json --target unverified --device 0 --out /path/to/new-perf-run
```

采集使用msprof op，预热5次，每kernel采集30×launch次数个样本，按单次调用总kernel耗时的算术平均值判定。
基线读取任务包perf_baseline.json；按NPU平均耗时≤GPU平均耗时/0.35判断，缺基线或采集证据不足不判通过。
报告与原始CSV均保留。性能退出码0=PASS，1=FAIL，2=INSUFFICIENT或输入错误。
任务要求950PR、Device和列主序时，Host示例即使测得时间也不满足正式性能验收条件。
只有现场核实后才填写真实target；不通过改标签掩盖不匹配的硬件、ABI或布局。
