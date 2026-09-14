# 独立 C++ 执行器

## 目录

- 与 ATK 的能力差异
- 输入从哪来
- 非连续张量的三个字段一个都不能错
- 唯一随算子变化的代码
- 自定义算子包分四块接进来，谁没接谁就是空的
- tiling 库必须自己装，这是裸 aclnn 程序独有的坑
- 三档结论
- 为什么本执行器会漏报

`run_cxx.py` 走的这条路。**只换「谁把算子跑起来」**，用例、输入张量、golden、
精度标准、报告字段与 ATK 那条完全相同，所以两条路径的结论可比。

## 与 ATK 的能力差异

| | ATK | 独立执行器 |
| --- | --- | --- |
| aicore 异常之后 | worker 不复位不重启，同批后面全连带，要隔离复验 | **崩了重启进程接着跑**，没有「连带」这个概念 |
| 每条用例的结论 | 可能是推断出来的 | 都是自己跑出来的 |
| 新算子适配 | 开箱 | 签名类型不在词表里就跑不了 |
| 覆盖 | 精度 + 性能 | **只有精度**，性能判定是相对基线的比值，另一套方法学 |
| 交付算子作者 | 不能 | 描述符 + 二进制可直接复现 |

## 输入从哪来

**读生成侧冻结的 `inputs/<用例 id>/input.bin`，不自己造数。**
用例包没有 `inputs/` 时退 3，让人回生成侧重跑 `freeze_golden.py`。

早先 ATK 是跑测时按 `torch.manual_seed(case_id)` 现生成的。那要求两侧 atk 版本
行为完全一致，上游一改生成逻辑，golden 还是老的、输入变成新的，**比对全错且无痕**。
冻下来之后两个执行器读同一份字节，这个问题不存在。

## 非连续张量的三个字段一个都不能错

切片开着时描述符里这三样必须自洽，写错任何一个都不是崩，而是**换了一个被测对象**：

| 字段 | 取值 | 写错的症状 |
| --- | --- | --- |
| `strides` | 视图自己的 stride | 算子读到的是别的元素 |
| `nbytes` | stride 能触到的最远元素数 × 元素宽 | **不能按 numel 算**，少拷的部分算子照读，读到的是别的数据 |
| `storage_shape` | **照抄 ATK**：没切片就是 view 形状，切了是 `[shape[0]*2] + shape[1:]`（`atk/tasks/api_execute/aclnn_base_api.py:136`、`backends/lib_interface/acl_wrapper.py:312`）。出参永远用 view 形状 | 自己另定一套（比如压成一维 span）时，aclnn 不报错、CANN 内置 kernel 也照跑对，**但自定义 kernel 会拿它算 tiling**，算出退化结果、输出全零或只写一小块。实测这样会让本该崩的用例假装通过 |
| `nbytes` 与 storage 的关系 | 显存按 **storage 形状**给够，不是按 view 的 span | 声明 68 个元素只申请 67 个，kernel 照 storage 访问就越界 |

切不切、切哪条由 `random.Random(case_id)` 定，**必须与 ATK 那轮同口径**：先按
`rng.random() < 0.3` 抽一次，再拿同一个 rng 交给 ATK 自己的 `apply_slice_input`。
抽的次数或顺序差一次，选中的张量就换了一个，两边都不报错。

## 唯一随算子变化的代码

`gen_op_call` 从 `op_api/aclnn_<op>.h` 解析签名，生成十来行的 `op_call.inc`：

```c
#define OP_GET_WORKSPACE_SIZE(ws, ex) \
    aclnnUpsampleNearestExact1dGetWorkspaceSize(A_TENSOR(0), A_INTARRAY(1), A_DOUBLE(2), A_TENSOR(3), ws, ex)
```

脚本会把这一行打出来，**肉眼核一遍再往下看结论**。参数顺序的规则只有两条：

- aclnn 参数 = 用例的输入参数按序 + 输出参数按序（`atk/tasks/api_execute/aclnn_base_api.py`）
- 签名里 `const` 标输入、非 `const` 标输出

支持的参数类型就是母仓 ops-nn 全部算子用到的那 11 种。**不在表里就退 4**，
打印是哪个类型、原文是什么，不猜一个最接近的——猜错的后果是测了另一个调用。

## 自定义算子包分四块接进来，谁没接谁就是空的

**这是本执行器最大的失真来源。** 待验收算子在 CANN 里往往有一份同名内置实现
（社区任务多是「改造已有算子」），于是每一块都有内置对应件：接进来的那块用待验收的，
没接进来的那块就是内置的——**不报错，因为内置那份本来就在，不算缺失**。

| 块 | 谁负责接 | 没接上的后果 | 断言 |
| --- | --- | --- | --- |
| aclnn 接口 so | 链接顺序（`-lcust_opapi` 在 `-lopapi` 前） | 调的是内置接口 | `符号落点`（dladdr） |
| kernel 二进制 | `ASCEND_CUSTOM_OPP_PATH` | 跑的是内置 kernel，**结果还对**，只是测错了对象 | `kernel 落点` |
| **tiling** | **本执行器自己 dlopen**，见下节 | kernel 与 tiling 不配套，结果错且不报错 | `tiling 库` |
| opp 路径本身 | `install.json` 的 `vendor_dir` | 见下 | `算子包` |

实测代价，两条都踩过：

- `ASCEND_CUSTOM_OPP_PATH` 填 opp 根时，那下面没有 `vendors/config.ini`（CANN 装机目录里
  那份有 `load_priority=...`），vendor 不被加载，kernel 静默回落到 `opp/built-in/`。
  表现是 fp32 逐位正确（内置算得对）、uint8 报
  `Cannot find bin ... integral key 0/1/|uint8/ND/uint8/ND/`（内置没有 uint8 kernel）——
  看着像「待验收实现不支持 uint8」，其实整批都没测到待验收实现。
  **正确值就是 vendor 目录本身**，算子包自带的 `bin/set_env.bash` 写的也是它，不需要探。
- `-lopapi` 排在 `-lcust_opapi` 前面时，5 条 uint8 全报
  `EZ1001 ... should be in dtype support list [DT_FLOAT,...]`，同样是根本没调到待验收实现。

`kernel 落点` 断言取自 CANN 自己的日志行 `Available bin for op <X> is <path>`，
只在启动时的一次探针调用里开 `ASCEND_GLOBAL_LOG_LEVEL=0`（**必须是 0，级别 1 带不出这行**），
正式跑测不开——一次约五万行。**只断言本包提供的 op type**（取自包里 `.o` 文件名前缀）：
算子内部会调 `StridedSlice`、`Transpose` 这类辅助算子，它们的 kernel 本来就该来自内置，
一并要求在包里的话断言永远不过。

## tiling 库必须自己装，这是裸 aclnn 程序独有的坑

**`<vendor>/op_impl/ai_core/tbe/op_tiling/liboptiling.so` 没人装，算子就用 CANN 内置 tiling。**
kernel 是待验收的、tiling 是内置的，两者不配套：aclnn 不报错、`sync` 成功、退出码 0，
**输出却是错的**——实测见过全批只写几十个元素，其余全零。

装它的是 GE 的 `OpTilingManager`，而 GE 由 **torch_npu 初始化**拉起。所以：

| 跑法 | 装不装 |
| --- | --- |
| torch / torch_npu / ATK / 母仓走 torch 的自测 | 装。框架初始化顺带做了 |
| **裸 aclnn C++ 程序**（本执行器、母仓 `examples/` 下的 aclnn 示例） | **不装**，没人碰 GE |

实测证据四条：裸 runner 日志里 `op_tiling_manager` 出现 0 次、ATK 出现 20 次；
一个只 `import torch_npu` 并碰一次 NPU 的最小脚本（完全不涉及 ATK）同样是 20 次
并 dlopen 本包的 `liboptiling.so`；同一份 `env.sh` 下只差一次 dlopen，结果从
34/666332 变成 666332/666332 逐位相同；只在 ATK 侧出现的组件全是 GE
（`ge_executor.cc`、`graph_mem_allocator.cc`、`op_tiling_manager.cc`）。

**这不是环境变量问题**，没有任何变量能开关它——用 ATK 一字不改的 `env.sh` 跑，不 dlopen 照样错。

所以 runner 启动时自己 `dlopen(路径, RTLD_NOW | RTLD_GLOBAL)`，在第一次算子调用之前，
装不上退 3。`RTLD_GLOBAL` 不能省：tiling 靠静态构造往 CANN 的注册表注册，
`RTLD_LOCAL` 装进来别的库看不见，等于白装。

### 后备手段：预加载 torch_npu（有触发条件才用）

`LD_PRELOAD=libpython3.13.so:libtorch_npu.so` 也能让结果逐位正确——**一行 Python 都不执行**，
静态构造就把 GE 拉起来了，实测有效。但它把复现包的依赖从「算子包自带的一个 so」
变成「整个 conda 环境」，最小复现包就交付不出去了，所以不做默认。

**什么时候用它**：某个算子两条执行器结论对不上，且已排除算子本身的问题。
一试就对，说明还漏了 GE 做的别的事，那时再决定改不改默认；
一试还错，说明不是这条路上的问题，往别处查。

### 走不通的路：直接调 `ge::GEInitialize`

试过，不通，记下来省得重走：要额外链 `libge_runner`、`libgraph` 与驱动目录的
`libascend_hal`；`std::string` 重载是旧 ABI（`_GLIBCXX_USE_CXX11_ABI=0`）编的，
整个 runner 得跟着换 ABI；换 `AscendString` 重载编过之后，
`GEInitialize` 返回 `0xffffffff` 而 `GEGetErrorMsgV2()` 是空串，查不出缺哪个选项。

## 三档结论

判失败的用例过一遍这条路径之后分三档报，不要合并：

| 两侧 | 报什么 |
| --- | --- |
| 都失败 | 缺陷，附独立复现程序 |
| 仅 ATK 失败 | **仍判缺陷**，注明「独立执行器未复现」并附下面那段触发条件，交算子作者 |
| 都通过 | 撤回该条失败判定，报告里说明原因 |

**不要因为独立执行器通过就撤回 ATK 的判定。** 这一条换过代价：实测有几条在
ATK 下必挂、在本执行器下偶发，一度按「待定」处理，实际是本执行器漏报。

## 为什么本执行器会漏报

两侧搬输入上卡的方式不同，而这正是某类缺陷的触发条件：

| | 搬输入的方式 | 被测算子之前跑过 AI Core kernel 吗 |
| --- | --- | --- |
| ATK / 真实 PyTorch 用户 | torch 的拷贝 → `aclnnInplaceCopy` → `TensorMove` kernel | **跑过** |
| 本执行器 | `aclrtMemcpy`（DMA） | **没有** |

实测：给本执行器加一次前置 `aclnnInplaceCopy`（只读输入张量，内容不动），
178 立刻从 5/5 过变成 5/5 挂，硬件错误寄存器与 ATK 逐位相同；
换成 DMA 读同一块内存不触发，换成读无关缓冲也不触发。
完整实验与排除项记在本 skill 源仓的 `docs/development/executor-divergence.md`
（开发态文档，不随 skill 安装）。

**所以本执行器的定位是「交付算子作者的最小复现器」，不是 ATK 判定的仲裁者。**
它的价值在于：崩了能重启接着跑（没有连带）、每条结论都是自己跑出来的、
描述符加二进制可直接交出去。

退出码与每条失败的原因由脚本自己打印，不在这里重复。两个错误码要认得：
`launch 561000` 是 kernel 发射失败（多半这个 dtype/SoC 没有 kernel），
`sync 507015` 是 aicore 异常、进程已废，脚本会重启进程跑剩下的。

## A3.5 独立执行器（`backend=aclnn` 时必做，范围由 A2.6 决定）

`backend=npu` 时整段跳过：独立执行器按 aclnn 两段式接口拼调用
（`GetWorkspaceSize` 拿 workspace，再调主函数），npu 剖面没有那套接口，
拼不出来。报告里这一条写「不适用（非 aclnn 接口）」，`repro/` 也只有 ATK
一条路线，`make_repro.py` 会在 README 里写明原因。

用不依赖 ATK 执行的路径再跑一遍：用例、输入、golden、精度标准都不变，
**换的是谁把算子跑起来，以及 ATK 换不掉的那份 tiling**。

| A2.6 的 `shadowed` | 跑多少 | 结论以谁为准 |
| --- | --- | --- |
| 空 | 只跑 ATK 判失败的（加 `--ids-from stage/accuracy.json`） | ATK |
| **非空** | **全量**（不加 `--ids-from`） | **独立执行器** |

```bash
cd <现场>/work && source evidence/env.sh && <python> <skill>/scripts/run_cxx.py \
    --op <op> -c ../input/cases.json --golden ../input/golden \
    --facts ../input/facts.json --install stage/install.json \
    [--ids-from stage/accuracy.json] -o stage/accuracy_cxx.json
```

**`shadowed` 非空时必须跑全量。** ATK 那一轮测的是 CANN 内置实现，它判通过的用例里
可能藏着待验收 tiling 的缺陷——只跑 ATK 的失败集合，这些永远进不了复核。
两条路线的结果并列进报告，注明各自用的哪份 tiling。

跑几轮由 `facts.json` 的 `non_contiguous.required` 决定，脚本自己判：

| `non_contiguous.required` | 跑几轮 | 落在哪 |
| --- | --- | --- |
| 假 | 一轮（连续） | `stage/accuracy_cxx.json` |
| 真 | **两轮** | 连续 `stage/accuracy_cxx.json`、非连续 `stage/accuracy_cxx_noncontig.json`，**两个通过率各自独立报，不合并** |

**这一步与 A3 的口径已经不同。** A3 把两种布局按比例混在同一轮（run-accuracy.md
「精度的连续与非连续在同一轮里混跑」），这里仍是两轮：独立执行器不经过 ATK，
没有那边整轮兜底超时的约束，两轮的代价只是多跑一遍。两侧的通过率不要横向相减。

**不要自己加 `--slice-input`。** 手动指定就只跑那一个布局；`shadowed` 非空时独立执行器
是唯一有效的验收路径，少跑的那个布局**没有任何一轮测过待验收实现**，而报告上看不出来。

结论分三档写进报告，**不要合并**：

| 两侧结果 | 写什么 |
| --- | --- |
| 都失败 | 缺陷 |
| 仅 ATK 失败 | **仍判缺陷**，注明「独立执行器未复现」——理由见 [standalone-executor.md](standalone-executor.md) |
| 都通过 | 撤回该条失败判定并说明原因 |

| 退出码 | 含义 |
| --- | --- |
| 3 | 用例包没有 `input/inputs/`——老包，回生成侧重跑 `freeze_golden.py` |
| 4 | 签名含不支持的参数类型，该算子只能走 ATK，照实写进报告 |
