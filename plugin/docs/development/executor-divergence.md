# 两个执行器在同一条用例上判定不一致

2026-09-01 结案。**根因已定位并在 C++ 执行器里单变量 100% 复现。**
分歧有两类，第一类是 tiling 归属，第二类是本文主题；两类都已查清。

一句话：**两侧测的是同一个真实缺陷，差别只在触发条件。ATK 每次跑测都满足触发条件，
独立执行器默认不满足，于是漏报。**

## 结论

| 问题 | 答案 |
| --- | --- |
| 151/178/189 在 ATK 下必挂，在独立执行器下偶发，谁对？ | **ATK 对。** 缺陷真实存在，独立执行器漏报 |
| 触发条件是什么 | **紧邻的前一个 AI Core kernel 访问过被测算子的输入张量那块 GM** |
| ATK 为什么每次都满足 | 它用 torch 搬输入上卡，torch 的拷贝走 `aclnnInplaceCopy`，在 NPU 上由 `TensorMove` kernel 实现 |
| 独立执行器为什么不满足 | 它用 `aclrtMemcpy` 搬输入，那是 DMA，不发射 kernel |
| 哪个更接近真实用户 | **就本节的搬运路径而言是 ATK**：真实 PyTorch 用户的输入同样由 torch 搬上卡，同样先跑 TensorMove。**但不要推广成「ATK 是权威」**——存在同名内置算子时 ATK 绑的是 CANN 旧 tiling，测的不是交付的代码，那一维要以独立执行器为准，见下面「第一类分歧」 |

## 复刻实验（决定性）

给独立执行器加一个开关，在调用被测算子之前先发一次 `aclnnInplaceCopy`，
其余一律不动。热卡、同一张现查空卡、同一条 178：

| 前置动作 | self 内容 | 走 AI Core kernel | 结果 |
| --- | --- | --- | --- |
| 无 | 原始 | — | 过 5/5 |
| **kernel 读 self**（dst=scratch, src=self） | 原始 | **是** | **挂 5/5** |
| DMA 读 self（`aclrtMemcpy` D2D） | 原始 | 否 | 过 5/5 |
| **kernel 写 self**（先 DMA 备份再写回，内容不变） | 原始 | **是** | **挂 5/5** |
| kernel 写 self（写入全 0，内容被破坏） | 全 0 | 是 | 过 5/5 |
| kernel 只碰 out，不碰 self | 原始 | 是 | 过 5/5 |
| kernel 只碰无关 scratch（16 / 4096 / 2093056 元素都试过） | 原始 | 是 | 过 5/5 |

两条边界很干净：**换成 DMA 就不触发**（同样访问同一块内存），
**换成别的内存就不触发**（同样是 AI Core kernel）。

## 再往下一层：触发要同时满足两个条件

上表里「kernel 写 self 写成全 0 → 过」这一格暴露出第二个变量。拆开之后：

| self 的数据 | 前置 kernel 碰过 self | 结果 |
| --- | --- | --- |
| 原始 | 无 | 过 5/5 |
| 原始 | **有** | **挂 5/5** |
| DMA 清零 | 无 | 过 5/5 |
| DMA 清零 | 有 | 过 5/5 |

**两个条件缺一不可。** 而且已核实两档发射的 TensorMove 完全相同
（`data_size 2093056`、`needCoreNum 48`），dispatch 没有因数据不同而改路。

进一步固定「有前置 kernel 读 self」，只改 self 的数据（DMA 填常量）：

| self 填什么（bf16 语义） | 结果 |
| --- | --- |
| `0x00` → 0.0 | 过 5/5 |
| `0x01` → 约 9e-38，极小 | 过 5/5 |
| `0x3f` → 约 0.75 | **挂 5/5** |
| `0xff` → NaN | **挂 5/5** |
| 原始随机数据 | **挂 5/5** |

**按数值量级分界，不按位模式分界**——`0x0101` 与 `0x3f3f` 同样是全同字节、
同样可压缩，结果却相反。

已排除的邻近假设：**不是越界读 GM**。在 self 缓冲区之后填 `0x00`/`0xFF`/`0xAA`
（4 KB 与 64 B 两种长度），无前置 kernel 时一律 5/5 过——读到的界外数据不影响结果。

**能观测到的到此为止。** 「前一个 kernel 碰过同一块 GM」+「数据量级非极小」两个条件
同时满足才发作，且发作时是 100% 而非偶发，这是**核内缺少流水同步（VEC 与 MTE 之间）
时典型的数据相关时序敏感**形态——前一个 kernel 把这块数据带进片上缓存改变了访存时延，
数据量级改变了 VEC 的处理时延，两者共同决定竞态的胜负。**这一句是推断，不是结论**：
再往里就要读 kernel 源码，那越过本仓红线，交算子作者。

复刻出来的失败与 ATK 的失败**逐位相同**，连硬件错误寄存器都一样：

```
C++ 加前置 : errorStr: VEC instruction error: the ub address out of bounds.
             fixp_error0 0x800db, fixp_error1 0x5e, fsmId:1, tslot:4, blk:0, subErrType:4
ATK 原始   : errorStr: VEC instruction error: the ub address out of bounds.
             fixp_error0 0x800db, fixp_error1 0x5e, fsmId:1, tslot:4, blk:0, subErrType:4
```

fault kernel 同为 `IndexFill_c575a009…_high_performance_1`，
AIC_INFO 的 28 个入参里除地址外逐项相同（tiling 11 字段、索引 `0x2db`=731、
fill 值 `0x3f80`=bf16 1.0、blockDim 48、argsSize 224）。

复现材料：`work/cxxCONT/probe_mix2.cpp`（`RUNNER_PREWARM_MIX=r|rd|ww|wwd`）。

## 排除过的假设

每条都有实验，不要重做：

| 假设 | 怎么排除的 |
| --- | --- |
| 部署方式不对 | 按官方默认装进 CANN `opp/vendors`、`config.ini` 自动排第一，ATK 结果不变 |
| `ASCEND_CUSTOM_OPP_PATH` 值形态 | 指 vendor 目录、指 opp 根两种都试，结果不变 |
| 缺 `vendors/config.ini` | 补上，结果不变 |
| vendor 在 GE 搜索序里排太后 | 实测本包**已经排第一**（`GetOppPluginPathNew` 原文），内置排最后 |
| 显式 dlopen tiling | 带/不带在 2×2 里各自一致，对判定零影响 |
| 输入数据两侧不同 | ATK 的 `input.bin` 是 zip，张量从偏移 768 起，与 C++ 的 `.bin` **逐字节相等** |
| torch_npu 缓存分配器 | `PYTORCH_NO_NPU_MEMORY_CACHING=1` 关掉，3/3 仍挂 |
| self/out 显存相邻（间隔 512B） | 在 C++ 侧复刻到地址低位逐位相同，3/3 过 |
| workspace 残留内容 | 填 0 / 0xFF / 0xAA 各 6 次全过 |
| 前一条 IndexFill 用例污染 | 同进程内先跑 83/93/128/151/189/203，178 照样过 |
| 输出缓冲初值 | 0 / 0xFF / 0xAA / 完全不初始化，各 6 次全过 |
| 「冷卡首跑必挂」 | **不成立**。现查空卡 6 张里 2 张首跑挂、4 张过。早先「6 张卡全挂」的统计里混进了被占用的卡 |

## 第一类分歧：两侧绑的不是同一份 tiling（机制已查清）

现象：C++ 侧 tiling 打印是 `[OPS_NN]` 行号 72/84/115/251/272（待验收
`experimental/index/index_fill/op_host/arch22/`），ATK 侧是 `[OP_PROTO]` 行号
75/87/116/233/255（CANN 内置 `index/index_fill/op_host/`）。

**机制是 GE 的 `op_impl_space_registry_v2_impl.cc::MergeFunctions` 先到先得。**
日志措辞可判别：

- `op type IndexFill tiling func **registered**.` —— 槽位空，真的注册进去了
- `op type IndexFill tiling func **has been** registered.` —— 槽位已占，本次跳过

两侧的注册时序：

```
C++  : 待验收 count 27  → registered      内置 count 256 → has been registered（跳过）
ATK  : 内置   count 561 → registered      待验收 count 946 → has been registered（跳过）
```

差别来源是 **torch_npu 链着整套 GE**（`ldd libtorch_npu.so` 见 `libge_runner`、
`libgert`、`libregister`），GE 把 CANN 内置 op host 更早拉进来。

**没有任何环境变量控制这一层。** `ASCEND_CUSTOM_OPP_PATH` 与 `config.ini` 只决定
扫描顺序（实测本包已排第一），tiling 函数的槽位归属由库加载时序决定。
`LD_PRELOAD` 也不行——它只保证库被映射，反而使其错过 GE 的注册窗口（实测：
preload 后待验收那次注册在干活的 worker 里完全消失）。

**但这一类不是 151/178/189 失败的原因**：C++ 用内置 tiling（`LD_PRELOAD` torch_npu）
跑 178 照样 5/5 过，而 C++ 用待验收 tiling 加上前置 kernel 就 5/5 挂。

## 给算子作者的证据

归因到此为止，成因交作者。要交出去的是：

- 用例：178（bf16，`[4096, 511]`，dim=-2，index=[731]，fill=1.0）。151、189 同族
- 触发条件（两个必须同时满足）：① 紧邻的前一个 AI Core kernel 访问过输入张量的 GM，
  DMA 访问不触发；② 输入数据的数值量级非极小（全 0 或 ~1e-38 量级不触发，
  0.75 与 NaN 触发）。满足时 100% 复现，不是偶发
- 已排除：越界读 GM（self 之后填任何模式都不影响）、显存布局、workspace 残留、
  输出缓冲初值、tiling 归属
- 硬件报文：`VEC instruction error: the ub address out of bounds`，`subErrType:4`
- fault kernel：待验收包的 `IndexFill_…_high_performance_1`（tilingKey 1，blockDim 48）
- tiling 参数：coreNum 48 / N 4096 / indicesNum 1 / indicesProcessMode 1 /
  frontCoreNumTaskIndices 16 / tailCoreNumTaskIndices 32 /
  frontCoreDataTaskIndices 86 / tailCoreDataTaskIndices 85 /
  ubSize 196352 / P 1 / Q 511 / tilingKey 1
- 出错 block 不固定（实测 blk=0 与 blk=40 都出现过），说明不是某个固定核

## 抖动：仍然存在，但不影响本结论

同一条 128、同一张卡、同一份二进制：09:37–09:50 五次全挂，10:20–11:00 六十三次全过。
**任何单次判定都不足以定性一条用例**，比对多个变体必须在同一时间窗内交错跑。
本文所有对照实验都是同卡同窗口交错做的。

## 量测协议：踩出来的四条

1. **不 source `evidence/env.sh` 就开跑，ATK 静默去测 CANN 内置实现**（fp64 的 151 报
   `EZ1001 Tensor self not implemented for DT_DOUBLE`）。对比脚本开头必须断言
   `ASCEND_CUSTOM_OPP_PATH` 与 `ATK_CUSTOM_OPP_PATH` 非空
2. **卡号不许写死，开跑那一刻现查，且跑前复查。** 本次就因为写死卡号，把两张被别人
   占用的卡当成空卡，据此得出过「冷卡首跑必挂」的错误结论
3. **跨时间窗的数不可比**，比对变体必须每轮各跑一次、交错进行
4. **判定读数优先用确定性量**（tiling 身份、注册措辞、errorStr 寄存器值），
   通过率是概率量，单档少于 20 次说明不了问题
