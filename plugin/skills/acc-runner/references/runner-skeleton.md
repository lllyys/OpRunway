# acc-runner 骨架：per-op NPU runner 的固定框架 + 据算子 example 填的槽

> `acc-runner` skill 的 reference。据 spec + `pr_facts`（算子自带 example + op_def）**生成一个锚定算子实测路径的 runner** `oprunway_<op>_runner.cpp`，供 `repo_adapter.run_new_example` 在真 NPU 上跑正确性 + msprof 测性能。
>
> **⚠ 当前闭环范围（诚实说明，勿超范围声称）**：只有 **ops-math 风格、`experimental/math/<op>` 目录、aclnn 两段式接口**的算子是**代码闭环**的（`run_on_npu.sh` 目前硬编码 `experimental/math/$OP` + `--experimental` + `${VEN}_math`）。legacy / 非 math 族 / 双实现 / catlass **尚未支持**（需先扩 `run_on_npu.sh`/`repo_adapter` 加 `OPRUNWAY_TARGET_DIR` 等配置，见 §3）。
> **验证-才-信目前是「纪律」不是「代码强制门」**：`repo_adapter` 只检查 runner 文件是否存在，**不识别 unverified 标记**。真正的硬门要加 sidecar 契约（§4），未加前 agent/人**必须自觉执行验证**。
>
> 已跑通的三个 runner（`samples/runners/oprunway_{isclose,sign,equal}_runner.cpp`，分别 unary/数值、binary/bool、binary/bool+attr）是**只读参考样例 / 生成器骨架种子**（非引擎组件、非运行时回退靶）。
> ⚠ **落点**：你为用户算子生成的 runner 落 **`<ops_root>/<op>/`**（`ops_root` = `$OPRUNWAY_OPS_DIR` 或 `${OPRUNWAY_WORK_DIR:-$CWD}/.oprunway/ops`），**不要写进插件的 `new_example/`**（插件安装目录升版即冲；工程约定要求产物落用户 CWD）。`repo_adapter.find_runner()` **只查用户目录、无 fallback**（fallback 已退役 2026-07-20）：**缺 runner 直接 fail-closed BLOCKED，引擎绝不回退插件样例**——runner 是引擎的**输出**、非组件。**核心纪律（Equal 教训）**：aclnn 入口/dtype/参数顺序**一律从算子自带 `test_aclnn_*.cpp` 抠、不猜**。

## 0. 契约（固定，与 `repo_adapter`/`run_on_npu.sh` 对齐，勿改）

- **runner 文件名**：`oprunway_<op.lower()>_runner.cpp`（`repo_adapter` 用 `caseset['op'].lower()` → IsClose=`oprunway_isclose_runner.cpp`；注意 build `--ops` 用 snake `is_close`，与文件名的 lower 不同）。
- 读环境变量 `OPRUNWAY_CASES` → `$OPRUNWAY_CASES/manifest.txt`，逐行一个 case。
- **manifest 行**：`case_id dtype [attr…] ndim dim0 dim1 …`（attr 段按 spec `params` 里 `io=="attr"` 的出现顺序，`gen_cases`/`repo_adapter` 据此派生；bool→0/1；无 attr 则无该段）。
- 每 case 目录 `$OPRUNWAY_CASES/<case_id>/`：输入 `x1.bin`、`x2.bin`…（按输入序，host 端已广播成同 shape）；写 `out.bin`。
- **out.bin**：bool 输出→`uint8` 每元素 0/1；数值输出→与输入同 dtype 原始字节。字节数 = numel × sizeof(输出元素)。
- **双哨兵 + returncode**（`repo_adapter` 三者都查，缺一即失败）：runner 打印 `OPRUNWAY_DONE total=%lld ok=%lld fail=%lld\n`（结束）；远端脚本 `run_on_npu.sh` 完成后打印 `OPRUNWAY_NPU_DONE`；runner 退出码 `failed==0 ? 0 : 1`。
- dtype 支持：`float32`(ACL_FLOAT/`float`)、`float16`(ACL_FLOAT16/`uint16_t`)。不支持的 dtype 报错、不静默；超出（bf16/int8…）入 gap，别硬塞。

## 1. 共享框架（三个 runner 语义相同的部分——**伪代码摘要**，从真 runner 拷、按需微调）

> ⚠ 非逐字相同：IsClose runner 另有 header fallback、更严文件校验等；Sign/Equal 是更小骨架。**从 `oprunway_sign_runner.cpp`（一元）或 `oprunway_equal_runner.cpp`（二元）整体拷贝，只改 §2 四槽** 最稳。

```
includes: acl/acl.h + aclnnop/aclnn_<op>.h(槽A) + <fstream/sstream/vector/cstdint/...>
宏: SUCCESS=0 FAILED=1 LOG_PRINT(printf 包装)
工具: JoinPath / ShapeSize / ReadExact / WriteExact
Init(dev,&stream): aclInit + aclrtSetDevice + aclrtCreateStream
MkTensor<T>(host,shape,&dev,dt,&t): aclrtMalloc + H2D + aclCreateTensor(ND strides)
struct Case{ id,dtype; vector<int64_t> shape; }
ParseLine: 读 case_id, dtype, [槽B 的 attr…], ndim, dims
RunTyped<T>: 读 x?.bin(槽B) → MkTensor 输入+输出(槽C) → 槽D 的 aclnn 两段调用 → sync → D2H → WriteExact(out.bin)
RunCase: numel 溢出检查 + numel==0 兜底 + dtype 分派(float32→float / float16→uint16_t)
main: 读 OPRUNWAY_CASES/manifest → 逐行 ParseLine+RunCase → 打印 OPRUNWAY_DONE → 退出码
```

## 2. 四个槽（从算子 example + spec 填，别猜）

| 槽 | 填什么 | 从哪读 |
|---|---|---|
| **A · aclnn 头** | `#include "aclnnop/aclnn_<x>.h"` | 算子 `test_aclnn_*.cpp` 的 `#include`（Equal 用 `aclnn_eq_tensor.h`，非猜的 `aclnn_equal.h`）|
| **B · 输入 + attr（含类型/编码/传参）** | ① 输入 `x?.bin` 个数；② attr 顺序（spec `params` io=attr 出现序）；③ **每个 attr 的 C++ 类型 + manifest 解析类型 + 传给 aclnn 的形式**（IsClose：`double rtol/atol` + `bool equalNan`，manifest 里 rtol/atol 读 double、equal_nan 读 0/1）| spec `params` + **优先 example 里 `aclnn…GetWorkspaceSize` 的实参类型/顺序** |
| **C · 输出 dtype** | bool→`vector<uint8_t>`+`ACL_BOOL`+写 numel 字节；数值→`vector<T>`+同输入 dtype | **example 创建 out tensor 的 dtype** 为准；spec `verify_mode` 佐证（exact 且 out=bool→bool；numerical→同输入 dtype）|
| **D · aclnn 调用** | `aclnn<Op>GetWorkspaceSize(<按签名入参>, out, &ws,&exec)` + `aclnn<Op>(ws,wsSize,exec,stream)` | **example 里实际那两行**——参数个数/顺序/attr 全照抄（最易猜错、Equal 翻车处）|

**三档填法**：一元数值(Sign)：A=`aclnn_sign.h`/B=1输入0attr/C=同dtype/D=`aclnnSignGetWorkspaceSize(self,out,…)`；二元bool(Equal)：A=`aclnn_eq_tensor.h`/B=2输入0attr/C=bool/D=`aclnnEqTensorGetWorkspaceSize(self,other,out,…)`；二元bool+attr(IsClose)：A=`aclnn_is_close.h`/B=2输入+3attr(`double rtol,double atol,bool equalNan`)/C=bool/D=`aclnnIsCloseGetWorkspaceSize(self,other,rtol,atol,equalNan,out,…)`。

## 3. 构建路径选择（**当前仅 experimental/math 闭环，余待扩展**）

| pr_facts.target_dir | 状态 | 做法 |
|---|---|---|
| `experimental/math/<op>`（is_close/sign/equal）| ✅ **已闭环** | `run_on_npu.sh` 现走 `--experimental --pkg --ops=<op> --soc=<soc> --vendor_name=<v>` |
| `<族>/<op>` 非 experimental（`activation/relu`、`math/equal`…）/ 双实现 / catlass | ⛔ **未支持** | **先扩** `run_on_npu.sh`/`repo_adapter`（加 `OPRUNWAY_TARGET_DIR`/`OPRUNWAY_EXPERIMENTAL` 等配置并消费）再用；当前遇到 → 记 gap、不假装能跑 |
> ⚠ Equal 那次 = experimental 实现本身坏、非路径选错；但 legacy/双实现确要选对——**现在代码还没做，别声称能选**。

## 4. 验证-才-信（**当前是纪律；代码硬门待补 sidecar**）

生成的 runner 未验证不得用于出裁决。**可执行验证规程**（真机）：
1. 编 runner（standalone `g++ … -lascendcl -lnnopbase -lcust_opapi`，见 run_on_npu.sh）。
2. 造 **1–2 个手算 golden 的小 case**：建 `$OPRUNWAY_CASES/<cid>/x?.bin` + 一行 manifest（如 Equal self={0,1,2,3}/other={0,1,9,9} → golden {1,1,0,0}）。
3. 用 **custom exe** 跑，检查：`rc==0`、输出含 `OPRUNWAY_DONE total=n ok=n fail=0`、`out.bin` 字节数 = numel×sizeof、bool 值∈{0,1} / 数值误差在阈内、且**逐元素等于手算 golden**。
4. **全过 → 才信**；任一不过 → 见下 root-cause，**禁止交 run_new_example**。
5. **（待补的代码硬门）**：写 sidecar `oprunway_<op>_runner.verified.json`（记 runner hash + op 源码 hash + 手算 case + 结果）；`repo_adapter` 运行前校验 sidecar——**此门未实现前，验证靠 agent/人自觉**。

**root-cause 解耦**（ops-math aclnn 路径）：同一手算 case，**custom exe 与 builtin exe（不设 ASCEND_CUSTOM_OPP_PATH，链系统 opapi）对照**——custom 错 / builtin 对 → 偏被测算子实现有问题；两者都错 → 优先查 runner（aclnn 入口/参数/manifest）。**别臆断、别来回改口**（Equal 血教训）。

## 5. 自检（生成后）
- 四槽都从 example/spec 填、无 TODO/占位；aclnn 头 = example `#include`；输入数 = spec io=in 个数；attr 顺序+类型 = params/example。
- out.bin 写法与 verify_mode/out tensor dtype 一致；保留 `OPRUNWAY_DONE` 原格式 + 退出码语义。
- 文件名 = `oprunway_<op.lower()>_runner.cpp`。
- **未过 §4 验证前，runner 不接 run_new_example**（当前靠自觉，直到 sidecar 门落地）。
- target_dir 非 `experimental/math` → 记 gap、不硬跑（§3）。
