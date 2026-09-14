# npu 剖面：`backend=npu` 这条路

用例包 `facts.json` 的 `backend` 是 `npu` 时读这份。缺省的 `aclnn` 剖面用不到。

分流判据在生成侧定，跑测侧只读——判据本身见生成侧的 `interface-facts.md`「backend」。

## 工作原理

算子没有 aclnn 两段式 C 接口，而是由算子工程把实现注册进 torch 的 dispatch。
ATK 起一个 torch 节点调它，被测节点在报表里叫 `npu_0`，标杆仍是 `cpu_0`。

注册形态有两种，**对 ATK 是同一件事**（都是「调一个 callable」），但对探针不是：

| 注册形态 | 用例里调什么 | 待验收 so 没装进来时 |
| --- | --- | --- |
| 新建命名空间的自定义算子 | `torch.ops.<ns>.<name>` | 调用直接抛异常，看得见 |
| 注册进 ATen 已有算子的 NPU dispatch 键 | 该算子的公开 torch API | **不报错**，dispatcher 落到别的实现，通过率照样好看 |

第二种是「待验收实现的核对」那道判据非做不可的原因。实测形态：ops-sparse 的
`TORCH_LIBRARY_IMPL(aten, PrivateUse1)` 与 `(aten, SparseCsrPrivateUse1)` 各注册
一次 `_sparse_addmm`，调用面是 `torch.sparse.addmm`（2026-09-09）。

**报表形态与 aclnn 那条路同构**，这是复用整个报表解析层的依据。实测对照
（stub 跑任务自带的 200 条精度件，2026-09-07）：

| 项 | aclnn 剖面 | npu 剖面 |
| --- | --- | --- |
| sheet | `statistic` `summary` `failed cases` `accuracy false cases` | 逐字相同 |
| summary 列 | 名称／总用例数／执行成功／执行失败／通过／通过率／精度是否达标 | 逐字相同 |
| 被测节点名 | `pyaclnn_0` | `npu_0` |
| 精度判定挂哪列 | `<标杆节点>_精度通过`，被测节点那列整列 `None` | 相同 |

所以差异收敛成 `run_atk.py` 的 `PROFILES` 那张表，七个字段。

## 跳过的两个阶段

| 阶段 | 为什么不适用 | 报告里写什么 |
| --- | --- | --- |
| A2.6 tiling 归属 | 查的是「CANN 内置的同名 aclnn tiling 有没有抢先注册」。npu 剖面没有 aclnn 接口，不存在这种竞争 | `不适用（非 aclnn 接口，无 tiling 竞争）` |
| A3.5 独立执行器 | C++ 执行器按两段式拼调用：先 `GetWorkspaceSize` 拿 workspace，再调主函数。没有这套接口就拼不出来 | `不适用（非 aclnn 接口）` |

**两条都要显式写进报告，不要留空。** 留空的表现是读报告的人分不清「没做」
与「做了没结论」，而这两者的处置完全不同。

跳过 A3.5 的代价是**少了一路交叉验证**：aclnn 剖面下 ATK 判失败的用例还会被
C++ 执行器复核一遍，npu 剖面下只有 ATK 一条路线。所以 npu 剖面的失败判定
更依赖 A3 的隔离复验——那一步不能省。

## 待验收实现的核对

这是本链路最贵的一条判据：核不住时报告一切正常，通过率甚至好看，
但测的是别处的同名实现。

| 剖面 | 判据 | 在哪一步 |
| --- | --- | --- |
| aclnn | 日志里 `import aclnnXxxGetWorkspaceSize from <路径> success!` 的路径落在本轮 vendor 目录下 | 每轮跑完 |
| npu | import 注册模块后读 `/proc/self/maps`，待验收 so 落在本轮装包目录下 | 每轮开跑前 |

npu 剖面用探针而不是查日志，因为实现由 `torch.ops.load_library` 或工程自己的
import 装进进程，**ATK 一个字都不打**。ATen 键那一档还要更进一步：没装进来时
调用**不抛异常**，dispatcher 静默落到别的实现——这一档没有探针就没有任何证据。

探针要两样东西，都从 `stage/install.json` 读，由 A2 写进去：

| 字段 | 谁给 | 缺了会怎样 |
| --- | --- | --- |
| `install_root` | `build_install.py` 自动 | `run_atk.py` 退 3 |
| `register_module` | A2 的 `--register-module` 参数 | 同上 |

`register_module` 是**import 一下就完成注册的那个 python 模块名**。算子工程的
`README`、`examples/` 或 torch 适配层的说明里能找到，特征是模块顶层调
`torch.ops.load_library`。找不到时问用户，不要猜一个——猜错时探针 import 失败，
报错指向模块名而不指向 A2 的参数。它在 A2 由 `--register-module` 传进去，
A2 顺带把 torch 扩展编出来并写进 `PYTHONPATH`，见
[build-deploy.md](build-deploy.md)。

自带件路上还多一处：ATK 的 worker 各自加载插件，**注册模块要在 `kit_fixes` 的
插件入口里也 import 一次**。主进程 import 过不算——那个进程不跑用例。

## 性能

与 aclnn 剖面同一条命令，ATK 的 `performance_device` 在 torch 节点上照样可用。

`performance.kind` 不能填 `builtin`：那一档靠摘掉 opp vendor 层回落到 CANN
内置同名实现，npu 剖面没有那一层，`run_atk.py` 会退 3。

**任务书要求的口径 ATK 表达不了时，主线是外部量测件，ATK 那轮只作旁证。**
判据是逐条比对任务书的性能章节与 ATK 的能力，**不是「有没有自带脚本」**——
自产用例包也会落到这一档，那时量测件用 `assets/perf_harness.py`。整条支路
（判据表、采样口径、交换格式、裁决）在 [external-perf.md](external-perf.md)。
实测过的一例（`aclsparseDenseToSparse` 任务书 3.3）：

| 任务书要求 | ATK `performance_device` |
| --- | --- |
| 预热 10、采样 30 | 能，`--performance_data 10,30,n` |
| 分阶段报 Analysis／Convert／三阶段／端到端 | **不能**，一个节点一个数，不拆算子内部阶段 |
| 中位数与 p90 | **不能**，`perf_ntimes` 取的是后 n 次的平均值 |
| 复用描述符／workspace／输出再采样 | **不能**，每条用例独立走 dataset 到执行 |
| 两个独立计时范围各自对应采集 | **不能**，只有一个范围 |
| workspace 峰值、各 Kernel 耗时 | **不能**，要 Profiler |

六条里五条不能，所以那份任务的性能结论只能来自外部量测件。
`performance.kind` 填 `threshold`，`criterion` 抄任务书原话，
**实测数据来自量测件，ATK 那轮的绝对耗时并列进报告但不参与算倍率**——
两个口径的数相除没有意义。

## 常见问题

| 现象 | 原因 | 怎么办 |
| --- | --- | --- |
| 通过率 0，但日志里看不到报错 | 报表解析不到被测节点：`facts.json` 的 `backend` 与算子实际形态不符 | 核 `backend`，回生成侧改 |
| `import <register_module>` 失败 | 待验收实现没装进来，或模块名给错 | 先 `source evidence/env.sh`，再核 A2 的 `--register-module` |
| A2 退 3，`lib64` 下没有 `.so` | 装包漏了 `--install` | 脚本自己会带，退 3 时看 `evidence/install.log` |
| A4 退 3 提到 `builtin` | `facts.json` 的 `performance.kind` 填了 `builtin` | 回生成侧改成 `threshold` 或 `none` |
| Device 耗时全是 0.00 | **先看「Device 耗时 N 条」那个 N**。N 非 0 说明列解析对了，值真的是 0——算子没发 NPU kernel（纯 CPU 实现或桩）。N 为 0 才是列名对不上，核 `backend` | 报表里 npu 节点的列名是 `npu_0_Device性能（us）` |
