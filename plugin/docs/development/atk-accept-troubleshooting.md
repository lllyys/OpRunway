# 跑测验收：排障事实

跑测侧 `CLAUDE.md` 只留「不知道就会把脚本写错」的契约性事实；**这里放另一半——
某次真机跑测观察到的现象与量级**。改脚本时不需要读，排查一次具体失败时按算子查。

出处一律标 `实测` 加日期；被后来的测量推翻的写成撤回，原文保留一行以免重走。
未定位的开放问题在 [executor-divergence.md](executor-divergence.md)。

## 通用：aicore 异常与连带

| 事实 | 出处 |
| --- | --- |
| aicore 异常会把同批次后面的用例全连累，`accuracy.json` 的执行失败数严重虚高 | IndexFillTensor 实测 47 条重跑后只有 1 条是真的；换算子后 210 条里只有 9 条 |
| 同一个算子**两次跑出的失败集合不完全相同**，单次验收会漏报 | 实测两轮 12 条 vs 14 条，并集 19 条 |
| 「ub address out of bounds」型缺陷**会偶发**，与批次组成、并发、切片都无关 | IndexFillTensor 实测 2026-08-31，case 170 单条重复跑 17 次失败 2 次。**成因未测出，不要写成结论** |
| **id 67 单条跑 17 次一次不复现，却在批次里挂过两次**——这类缺陷依赖批次上下文，单条复现包可能复现不出来 | 同上。**成因未测出** |
| ~~偶发率：id 77 3/5、id 61 1/5、id 204 1/5、id 170 2/17~~ **撤回**：那批数据取自有其他进程占卡的那轮，偶发性本身可能是争用假象 | 2026-08-31 在干净卡 + 独立执行器下 0/5 复现 |
| **同一配置的失败率跨时间窗能从 100% 翻到 0%**：case 128 在一个窗口 C++ 侧 5/5 挂，一小时后同卡同二进制 63/63 全过 | 实测 2026-09-01。**单窗口 5 连判定也不足以给一条用例定性** |
| 同物理卡另一 chip 有没有负载**不影响**判定 | 实测 2026-09-01：邻 chip 分别空转 runner 与循环跑 ATK，独占轮与并发轮交错，30/30 与 12/12 全过 |
| **卡本身可能是坏的**：某张卡上任何任务都挂起（含已知能通过的对照组），同一份二进制在别的卡上正常。**空闲不等于可用，卡挂起时先换卡再查算子** | 实测 2026-08-31，2026-09-01 在同一张卡上复现 |
| ATK 的 `--single_process`（`-sp`）是真串行、快 3 倍，但**同样连带**：每条外面有 `try/except`，清不掉 device 的错误态 | 实测 252 条：celery 91s / `-sp` 31s，两者跑到执行的都是 84 条、连带 168 条 |
| `-sp` 死在 aicore 风暴里时**不写报告，退出码仍是 0**；干净批上它出报告正常 | 实测 |

## IndexFillTensor

| 事实 | 出处 |
| --- | --- |
| 16 条失败用例过独立执行器，单轮内 5 次结果一致、0 条偶发 | 实测 2026-08-31，单卡、卡上无其他进程。**跨时间窗不成立**，见上表 |
| 照 ATK 口径修正 `storage_shape` 后，失败集合是 41/72/77/167/213（全是 `sync 507015`），与 ATK 判定基本吻合 | 2026-08-31 改对后重跑 |
| ~~「11 条 5/5 通过、其中 3 条仅 ATK 失败」~~ **撤回**：`storage_shape` 压成一维描述的内存布局与 ATK 不同，让 72/77/213 三条本该崩的用例跑通了 | 2026-08-31 查实 |
| **共卡会把通过的用例判成失败**：138、161、170 三条在被占的卡上判失败，换空闲卡单卡重跑 5/5 全过。占卡的进程叫 `python engine/main.py`，不叫 atk | 实测 2026-08-31 |
| **两个算子目录导出同一套 aclnn 符号**：`index/index_fill_d` 与 `experimental/index/index_fill` 都导出 `aclnnIndexFillTensor{,GetWorkspaceSize}` | `nm -D libcust_opapi.so`，实测 2026-08-31 |
| 装错算子目录时 A2 符号核查照样过、退 0，症状要到 A5 才显形，且伪装成「精度不达标 60.3%」 | 同上，252 条挂 100 条，100 条全是 `EZ1001` 在 `GetWorkspaceSize` 被拒 |
| case 214 精度不符时 `max_abs_error` 逐轮不同（128.0 / 132.0），该用例输出本身不确定 | 实测 2026-08-31 |
| runner 进程退出时打 `corrupted size vs. prev_size in fastbins`，**出现在结果写完之后**，不影响结论；成因未定位 | 实测 2026-08-31 |

## UpsampleNearestExact1d

| 事实 | 出处 |
| --- | --- |
| 修完 tiling 加载后 8 条（含 5 条 uint8）**8/8 通过**，case 80 与 golden 逐位相同；ATK 对照 uint8 79/80、fp32 26/27 | 实测 2026-08-31，空闲卡 |
| 每轮固定 **3 次进程重启**（对应 3 条把 device 打废的用例），16 条一遍跑完，没有「连带」这个概念 | 同上 |
| ~~独立执行器 fp32 三条 3/3 通过~~ **撤回**：当时 `ASCEND_CUSTOM_OPP_PATH` 填的是 opp 根，kernel 静默回落到 `opp/built-in/`，测的是内置实现 | 2026-08-31 查实 |
| **uint8 冲突已查清，ATK 是对的**：`ASCEND_CUSTOM_OPP_PATH` 填成 opp 根时其下没有 `vendors/config.ini`，vendor 不被加载、kernel 回落内置，而内置没有 uint8 kernel | 实测 2026-08-31。把 ATK 的该变量改成 opp 根，uint8 从 80/80 执行成功变 0/80，反向印证 |
| ~~「正确的 `ASCEND_CUSTOM_OPP_PATH` 随算子而变，只能探不能写死」~~ **撤回**：真因是 tiling 库没装，那张候选表是误诊，已连同探针一起删掉 | 2026-08-31 |
| 一次 atk 启动约 30 秒，几乎与用例条数无关（5 条 29.5 秒，187 条一两分钟） | 实测 2026-08-27 |
| 同一条冒烟命令、同一张卡，一次 29.5 秒另一次 10 分钟没跑完 2 条 | 实测 2026-08-27，**差异原因未测出** |

## Median / ForeachMulList / bernoulli

| 事实 | 出处 |
| --- | --- |
| 内置 `aclnnMedian` 在 `keepdim=True` 时不返回，与 dtype、shape 无关 | 实测，3 条对照组直接验出 |
| 母仓 `agent/cann-ops/ops-nn` 与算子工程版本不配套时，Median 与 ForeachMulList 都编不过（`op_api_def.h` 已改名 `op_api_def_nn.h`），换 `ops-test/ops-nn` 当母仓即通过 | 实测 |
| Median 构建日志尾部 12 行全是母仓 ONNX 插件的 deprecation 告警，真正的首个错误在两千行之前 | 实测 |
| 张量列表算子的 `output_info.json` 嵌套是对的，`_check_output_info` 曾误报，卡在 A2.5 | ForeachMulList 实测 |
| bernoulli 是「改母仓已有算子」这一类，构建落点的四条锚假设一条不中，真机上连撞四次，**四次里三次的报错方向是错的** | 实测，详见 [architecture-log.md](architecture-log.md) |

## 非连续切片

| 事实 | 出处 |
| --- | --- |
| ATK 在 `--slice_input non_contiguous` 时，**出参的 storage 声明是它实际分配的 2 倍** | 插桩 `create_acl_tensor`：入参 `alloc=23040 declared=23040`，出参 `alloc=11520 declared=23040`。成因是 `convert_output_data` 算的 `cur_index` 落回了入参下标 |
| 但**那不是 aicore 异常的触发点**，两个方向都证伪了 | 实测：ATK 侧把超额 storage 置空，8 条仍全部 10 次 aicore；C++ 侧照抄 2 倍 storage，照样通过 |
| ATK 的非连续切片发生在 **NPU 上**，不是 CPU 上——「`.npu()` 把视图压实导致越界」这条推断不成立 | 探针实测：入参到 `create_acl_tensor` 时 `device=npu stride=(90,30,1) contiguous=False` |
| `_build_non_contiguous_slice_view` 翻倍的是**随机选中的 axis**，而 `get_storage_shape` 写死翻倍 dim 0；总元素数相同，形状不同 | `atk/configs/dataset_config.py`、`atk/tasks/api_execute/aclnn_base_api.py`。**本轮没测出它的后果** |
| 非连续入参会让 aclnn 插入 CANN 内置辅助算子；实测见过内置 `StridedSlice`（`opp/built-in/.../ops_legacy/strided_slice/`）越界写 24 字节 | 实测，`mssanitizer --tool=memcheck` |

## 独立执行器（C++ runner）

| 事实 | 出处 |
| --- | --- |
| **裸 aclnn C++ 程序不会加载算子包的 tiling 库**，于是 kernel 用待验收包的、tiling 用内置的：aclnn 不报错、`sync` 成功、退出码 0，**输出却是错的**（upsample 全批只写几十个元素）。装它的是 GE 的 `OpTilingManager`，GE 由 **torch_npu** 拉起 | 实测 2026-08-31：裸 runner 日志 `op_tiling_manager` 0 次、ATK 20 次 |
| 这**不是环境变量问题**：用 ATK 一字不改的 `evidence/env.sh` 跑，不 dlopen 照样错（34/666332）；同一套环境加一次 dlopen 就逐位相同 | 同上 |
| 后备手段已验证：`LD_PRELOAD=libpython3.13.so:libtorch_npu.so` 同样能让结果逐位正确，一行 Python 都不执行 | 实测 2026-08-31。不做默认是因为它把复现包依赖变成整个 conda 环境 |
| 走不通的路：直接调 `ge::GEInitialize`。要额外链 `libge_runner`、`libgraph`、驱动目录的 `libascend_hal`；`std::string` 重载是旧 ABI；换 `AscendString` 重载编过后返回 `0xffffffff` 且 `GEGetErrorMsgV2()` 为空串 | 实测 2026-08-31，记下来省得重走 |
| 同一条用例上，C++ 执行器与 ATK 给出的 CANN 诊断**逐字相同**（`561000` + `Cannot find bin of op UpsampleNearest`）；此前记的「执行器分歧」在这个算子上不成立 | 实测 |
| **launch 失败后，CANN 在进程 teardown 里会写坏 host 堆**，进程以 SIGABRT(134) 退出 | `MALLOC_CHECK_=3` 逐点验过：`OP_EXECUTE` 返回、`aclGetRecentErrMsg`、`aclrtFree`、`release()` 四处堆都还是干净的 |
| 母仓 `examples/` 下的裸 aclnn 示例同样绕不开 tiling 那个坑。风险只落在「改造已有算子」这类任务上——往 `experimental/` 新增的算子没有内置可顶 | **推断自上面几条，未在母仓示例上实测** |
| 母仓 ops-nn 全部算子的 `GetWorkspaceSize` 签名只用 11 种参数类型，且 `const` 标输入、非 `const` 标输出 | 扫 35 个签名，实测 2026-08-31 |

## 两个执行器的对齐（IndexFillTensor，2026-09-02/03）

全量 252 条、连续口径、**两侧同用 CANN 内置 tiling**、三轮交错。

| 事实 | 出处 |
| --- | --- |
| **执行器是对齐的**：稳定失败集合两侧逐条相同——执行失败 `226`，精度不符 `27/66/89`，批内污染 `203/233/151` | 实测三轮 |
| `22`、`240`、`16` 是抖动位，**两侧都出现过也都缺席过**，单轮读数会把它们误判成「某一侧独有」 | `22` 在 C++ 轮 3 翻面，`240` 在两侧各缺席一轮 |
| 单跑复现不了批次相关的失败：`22` 在两侧各单跑 3 次全部通过，但它在全量批次里失败 | 实测。**查批内效应不能用单跑当证据** |
| `151` 在两侧每轮都判失败，分歧只在归类；C++ 自己在轮 3 也翻成 `batch_only` | 实测 |
| C++ 的单独复跑**复用了刚崩过的那张卡**，于是把通过的用例判成「单独跑也挂」。换现查空闲卡后同一条 3/3 通过 | 实测。已在 `run_cxx.py` 改成复跑前 `_pick_device("auto")` |

**批内污染与 device 资源作用域无关。** 把 `aclrtCreateStream`/`DestroyStream` 改成每条用例建销（**与 ATK pyaclnn 的作用域等价**，见 `pyaclnn_backend.py:144`/`:252`），再加上每条 `SetDevice`/`ResetDevice`，三档跑全量 252：失败 id 逐条相同，批内污染那几条一条没变。实验开关跑完已从 `runner.cpp` 撤掉。

## ATK 换不掉 tiling（IndexFillTensor，2026-09-03）

| 事实 | 出处 |
| --- | --- |
| ATK 认的三个环境变量 `ATK_CUSTOM_OPP_PATH` / `ASCEND_CUSTOM_OPP_PATH` / `ASCEND_OPP_PATH` **只用来找 `libcust_opapi.so`**，代码里一次都没碰 tiling 库 | `pyaclnn_backend.py:410`、`acl_wrapper.py:528-531`；全仓 grep `liboptiling`/`op_tiling` 无命中 |
| `LD_PRELOAD` vendor tiling **装得进去但抢不到注册**：`/proc/self/maps` 里确有那份 so，ATK 用的仍是内置那份 | 实测，日志模块标签 `[OP_PROTO]` 不变 |
| C++ runner 反过来会**默认拿到 vendor tiling**——`ASCEND_CUSTOM_OPP_PATH` 指着 vendor 目录就够了，不需要显式 dlopen | 实测：日志模块标签 `[OPS_NN]`。**只挡 `RUNNER_OPTILING_SO` 并不能换成内置**，此前据此得出的「tiling 不影响结果」是错的 |
| 要让 C++ 用内置 tiling，得 `LD_PRELOAD` CANN 的 `opp/built-in/.../op_tiling/lib/linux/aarch64/liboptiling.so`，抢在 vendor 之前注册 | 实测，标签变 `[OP_PROTO]` |

**后果:ATK 测不到待验收 tiling 引入的缺陷。** 同一执行器只换 tiling，vendor 稳定多出 6 条失败（执行失败 2、精度不符 2、批内污染 2），一条都没少。其中两条已定位到源码：tiling 输入逐字相同（`axis 0 / N 1 / P 1 / Q 63 / coreNum 48`），vendor 算出 `tilingKey=2`、内置算出 `0`，key 2 那条 kernel 报 `VEC supports illegal configurations in commands`。分支在 `op_host/arch22/index_fill_tiling.cpp:129`：`Q=63` 够不到 `N_LIMIT=64`，掉进 `FRONT_P_KEY`。

**日志模块标签是分辨 tiling 来源的判据**：`[OPS_NN]` = 算子包自带，`[OP_PROTO]` = CANN 内置。开 `ASCEND_GLOBAL_LOG_LEVEL=0 ASCEND_SLOG_PRINT_TO_STDOUT=1` 后 grep `<op>_tiling.cpp:` 即可。

## 自带件路（SpGemm A2A3 实测，2026-09-08）

| 事实 | 出处 |
| --- | --- |
| torch 扩展是 `setup.py` 就地编的，链接期 RPATH 指向工程的 `build/`，**RPATH 优先级高于 `LD_LIBRARY_PATH`**，进程里加载的是工程那份而不是装包目录那份 | 实测 `/proc/self/maps`。待验收实现的判据因此放宽到「装包目录或算子工程」两处，说明里带上命中的是哪一处 |
| torch_npu 2.10.0 的 Profiler 只产 `kernel_details.csv`，没有 `op_statistic.csv` | 实测。自带件按后者取数，整轮汇总不出数 |
| 逐 kernel 的 `Duration(us)` 求和除以 `Step Id` 去重个数，与自带件按 active steps 相除等价 | 与打过补丁那轮逐条比：50 条相对差中位 0.08%、最大 2.86% |
| **同一张卡上两个 Profiler 进程会互相踩**，parse 报 `CANN Profiling data does not exist` 或 `no such table: TASK` | 实测。一卡一进程 |
| 自带件的性能脚本默认把产物写进**它自己所在的目录**，trace 目录用 `mkdir` 不带 `exist_ok`，残留会让它开跑即崩 | 实测。必须显式给 `--output-dir` 与 `--trace-root`，每轮先清 |
| 50 条性能采集：单卡串行约 12 分钟，10 张卡分片 2 分 41 秒 | 实测 |
| 200 条精度（自带件路，标杆当场算）约 3 分钟 | 实测 |
| 任务书 P 表 8 个场景 10 分 33 秒，P-03 单次 Kernel 耗时 40 秒 | 实测。规模 1048576³、nnz(C) 67108864 |
| `work estimation failed, aclsparse status=3` 是**间歇性**的：同一条用例 Event 轮失败、Profiler 轮通过 | 实测 `spgemm-float32-014` 与 `p-03-float16` |

### 冷启动整链（全新现场，只用装在 `~/.claude/skills` 下的量具）

| 阶段 | 耗时 | 结果 |
| --- | --- | --- |
| A1 探环境 + 写 `facts.json` | 约 40 秒 | `env.json` |
| A2 编译安装（`standalone-so`） | 84 秒 | `install.json` |
| A3 精度（自带件路，标杆当场算） | 166 秒 | 200/200 |
| A4 性能（任务书 P 表 8 个场景，10 卡分片） | 638 秒 | 8/8 跑出数 |
| A5 裁决、报告、复现包 | 4 秒 | 不通过（性能不达标） |
| 合计 | 约 15 分钟 | 与热跑逐条一致 |

冷启动翻出的两处，都是热跑时被现场残留掩盖的：

| 现象 | 真因 |
| --- | --- |
| A2 退 4，报「多个 spgemm 目录，定不了落点」 | 仓里有 `sparse/spgemm` 与 `test/spgemm`。**量具拒绝猜**，要 `--target` |
| 待验收实现探针把正常情况判成错配 | `build_install.py` 探到母仓时记 `repo`，显式给 `--parent-repo` 时记 `parent_repo`。探针只认前者 |

### 自产路整链（SpGemm A2A3 实测，2026-09-08）

生成侧原版 S1 到 S4 出用例包，跑测侧原版 A1 到 A5，性能仍用自带件脚本。

| 阶段 | 耗时 | 结果 |
| --- | --- | --- |
| 生成侧 S3 生成 | 数十秒 | 120 条（fp32 60、complex64 60），性能子集 50 条 |
| 生成侧 S4 冻 golden | 32 秒 | 120/120 |
| A2.5 冒烟 | 约 40 秒 | 两种 dtype 都跑起来 |
| A3 精度 | 200 秒 | 120 条里 119 通过，1 条精度不符 |
| A3 隔离复验 | 47 秒，15 卡并发 | 那条重判成**批内污染**，单独跑通过 |
| A4 性能（任务书 P 表） | 约 10 分钟 | 8/8 |
| 整链 | 995 秒 | 结论：批内污染 1 条，污染源未定位 |

npu 侧对 complex64 的三处限制，都是**逐步试出来的，不是从文档推的**：

| 调用 | complex64 | 报错 |
| --- | --- | --- |
| 稠密张量的掩码赋值 | **不支持** | `Tensor self not implemented for DT_COMPLEX64` |
| CSR 搬到 NPU、`torch.sparse.mm` | 支持 | —— |
| 结果的 `to_dense()` | **不支持** | `aclnnIndexAdd` 161002 |
| 结果搬回 CPU 再 `to_dense()` | 支持 | —— |

所以执行器里**稀疏化与转 CSR 一律在 CPU 做完再搬，结果搬回 CPU 再转稠密**。
另一条实测：这个算子在 NPU 上的输出布局是 COO 不是 CSR，`crow_indices()` 取不到。

`inputs/` 用不用的判据从剖面改成**产物在不在**：自带件那种纯 attr 用例没有
`inputs/`，而自产的 npu 用例包声明的是真张量、S4 照样冻了，按剖面一刀切会把
冻好的那份跳过，两个节点各自按 seed 现造，而这一条从来没有量具核过。
