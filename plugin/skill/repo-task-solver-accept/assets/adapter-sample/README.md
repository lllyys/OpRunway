# cmatinv 固定适配模块样例

开发者把test/cmatinv_batched目录中的适配源与自测源放进自己的同名test目录即可，
无需acceptance目录。adapter没有main、没有标准答案或误差判定；自测main与验收harness
编译同一份adapter源，并调用真实公开接口aclsolverCmatinvBatched。

文件：include/solver_adapter.h为固定协议；test/cmatinv_batched含adapter、独立自测main和CMake。
协议头由验收工具维护，开发者不可自行更改；验收编译强制使用skill原版头文件。
本包不包含skill、算子源码、预编译库或环境依赖。需要已配置的CANN、g++、CMake、numpy/scipy。

## 验收调用样例

另行提供repo-task-solver-accept及相邻repo-task-solver-case-gen，并设置：

```bash
export SOLVER_ACCEPT_DIR=/path/to/repo-task-solver-accept
export SOLVER_CASE_GEN_DIR=/path/to/repo-task-solver-case-gen
bash run_sample.sh /path/to/ops-solver ascend910_93 0 ./run-001
```

先source现场CANN set_env.sh。脚本从源码构建libops_solver.so，在源码外编译固定harness与
本包adapter，执行n=8、batch=4、seed=923000001五轮，留存实际输出与构建/编译/库身份。
将来验收正式交付时，--adapter指向交付仓test/cmatinv_batched/cmatinv_batched_adapter.cpp。
结构和复跑通过不等于正式数值通过：共享harness仍报告numeric=NOT_JUDGED；
本样例展示固定调用协议，自测main的简单对角矩阵检查不是正式精度卡。

## 开发者自测使用同一模块

实际算子库先构建完成，再执行（示例设备0）：

```bash
cmake -S test/cmatinv_batched -B ./selftest-build \
  -DOPS_SOLVER_REPO=/path/to/ops-solver -DSOLVER_ADAPTER_INCLUDE="$PWD/include"
cmake --build ./selftest-build
./selftest-build/cmatinv_adapter_test
```

该main独立检查2I的逆为0.5I，以及协议版本/缓冲容量错误会拒绝；报告不能作为验收证据。

## 性能调用样例

```bash
python3 "$SOLVER_ACCEPT_DIR/scripts/harness/profile_cmatinv_sample.py" \
  --repo /path/to/ops-solver --provenance ./run-001/build.json \
  --gen-dir "$SOLVER_CASE_GEN_DIR/scripts" \
  --adapter "$PWD/test/cmatinv_batched/cmatinv_batched_adapter.cpp" \
  --device 0 --out ./perf-001
```

msprof op采30次目标kernel，报告算术平均值；中位数只作诊断。没有cmatinv匹配GPU基线，
不判性能达标。适配create/reset中不得额外调用目标kernel，否则采样会混入准备工作。

## 换算子时开发者需要改什么

替换descriptor真实接口信息，以及create/reset/execute/readback/destroy中的接口专用部分。
Host传输固定紧凑行主序；真实算子可为Device/列主序，适配负责转换、workspace及指针数组。
execute只调用目标接口，返回原始状态；不生成测试输入、不改任务参数、不伪造info、不判PASS。
handle/stream归harness所有。部分创建失败仍需能destroy。新数学运算需验收侧独立判据，
当前样例不宣称Device接口或十个Cholesky算子已经实跑通过。详细契约见外部skill的
references/adapter-contract.md。
