# repo-task-blas-case-gen 开发约束

## 专属红线

- 不代写六件套中的 C++。`param.h`、`test.cpp` 与 `npu_wrapper.h` 由开发者实现。
- 不手写 CSV 绕过渲染器；CSV 必须由任务包内 `gen_csv.py` 可重复生成。
- 模板和脚本不得出现具体算子名；具体事实只存在于 FACTS 与 `assets/example/`。

## 真机验证过的事实

| 事实 | 出处 |
| --- | --- |
| `build.sh --soc=ascend910_93 --ops=<op>` 编出 `build/test/<family>/<op>/<op>_test` | 实测（A3，sger） |
| `--gtest_list_tests` 名字形如 `<Suite>/<Suite>.CsvDriven/TC_*` | 实测（sger） |
| `msopprof` 是独立可执行文件且在 PATH 上，`msprof op` 子命令同样可用 | 实测（A3，CANN 9.0.1） |
| 采集选项必须排在被测二进制之前，其后一律当作被测程序的参数 | 实测（A3，CANN 9.0.1） |
| `--application=` 已废弃，改用位置参数；`--ai-core`、`--task-time` 退 255 | 实测（A3，CANN 9.0.1） |
| `--aic-metrics=BasicInfo` 档单 launch 用例 4.6s、1.8 MB；默认档 6.2s、2.1 MB | 实测（A3，ctpmv） |
| msopprof 不透传被测程序失败：不存在的 gtest 用例照样退 0 且不产 CSV；工具自身参数错误退非零 | 实测（A3，CANN 9.0.1） |
| `OpBasicInfo.csv` 九列，**没有 `Task Type` 列**，旧的 kernel task 类型过滤整体作废 | 实测（A3，ctpmv/sasum） |
| `Device Id` 记物理卡号：`ASCEND_RT_VISIBLE_DEVICES=3` 时报 3，不是进程内逻辑 0 | 实测（A3，ctpmv） |
| 采到 1 个 launch 时产物扁平：`OPPROF_*/OpBasicInfo.csv` | 实测（A3，ctpmv） |
| 采到多个时产物嵌套：`OPPROF_*/<kernel 符号名>/<序号>/OpBasicInfo_<时间戳>.csv` | 实测（A3，probe4 14 launch） |
| `--launch-count` 超额指定安全：单 launch 用例上取 100 与取 1 的读数、耗时、体积都相同 | 实测（A3，ctpmv） |
| 产物体积随实际采到的 launch 数线性增长，约 2.2 MB 每 launch | 实测（A3，probe4 1/2/5/14 launch） |
| BLAS 不都是单 launch：cherk 一次调用 6 个 kernel（反交织 + 4 GEMM + 合并） | 源码 `blas/herk/arch35/cherk_host.cpp` |
| ssymm LEFT 在 m=1280 n=128 下 105 个 launch：row 与 k 按 256 分块 | 源码 `blas/symm/arch22/ssymm_host.cpp` |
| `gemm_strided_batched` 每批一次 launch，batchCount 只校验非负，无上界 | 源码 `blas/gemm_strided_batched/arch35/` |
| 预热无效：`--warm-up` 取 0/5/50 得 26.70/27.34/27.16 us，噪声级；重放模式本身绕开首次调用惩罚 | 实测（A3，ctpmv） |
| 换后端读数系统性偏低：TC_PF_1001–1003 三例 0.64/0.79/0.84 倍，绝对差 8.5–11 us | 实测（A3，msprof 与 msopprof 双跑） |
| 磁盘写满时 msopprof 仍退 0，只在日志刷 `Copy failed` 且不产 CSV | 实测（A3，容器 `/dev/shm` 仅 64 M） |
| 单次采样跨进程可复现：sger 同例两轮偏差 ≤2%（306/571/99µs） | 实测（A3，旧 msprof 口径，数值不可比） |
| 单次不偏冷：ctpmv 三尺寸各 5 次独立单采样中位偏差 ≤1.7%，首例无台阶 | 实测（A3，旧 msprof 口径，数值不可比） |
| 精度轮阈值待回填；A5 完整验收链已真机通（sger 小包 A1→A5） | 实测（A3，2026-09-14） |
| 多 launch 算子的端到端与截断路径尚未真机验证，cherk 与大 batchCount 用例是门控项 | 待验证（2026-09-23） |

源码类出处的基线是 ops-blas@621aafdb。`OpBasicInfo.csv` 的九列依次是：

```text
Op Name, Op Type, Task Duration(us), Block Dim, Mix Block Dim,
Device Id, Pid, Current Freq, Rated Freq
```
