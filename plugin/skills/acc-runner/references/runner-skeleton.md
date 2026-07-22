# acc-runner 骨架：per-op NPU runner 的固定框架 + 据算子 example 填的槽

> `acc-runner` skill 的 reference。据 spec + `pr_facts`（算子自带 example + op_def）**生成一个锚定算子实测路径的 runner** `oprunway_<op>_runner.cpp`，供 `repo_adapter.run_new_example` 在真 NPU 上跑正确性 + msprof 测性能。
>
> **⚠ 当前闭环范围（诚实说明，勿超范围声称）**：只有 **ops-math 风格、`experimental/math/<op>` 目录、aclnn 两段式接口**的算子是**代码闭环**的（`run_on_npu.sh` 目前硬编码 `experimental/math/$OP` + `--experimental` + `${VEN}_math`）。legacy / 非 math 族 / 双实现 / catlass **尚未支持**（需先扩 `run_on_npu.sh`/`repo_adapter` 加 `OPRUNWAY_TARGET_DIR` 等配置，见 §3）。
> **验证-才-信目前是「纪律」不是「代码强制门」**：`repo_adapter` 只检查 runner 文件是否存在，**不识别 unverified 标记**。真正的硬门要加 sidecar 契约（§4），未加前 agent/人**必须自觉执行验证**。
>
> 已跑通的三个 runner（`${OPRUNWAY_PLUGIN_ROOT}/samples/runners/oprunway_{isclose,sign,equal}_runner.cpp`，分别 unary/数值、binary/bool、binary/bool+attr）是**只读参考样例 / 生成器骨架种子**（非引擎组件、非运行时回退靶）。`samples/` 随插件分发（在插件内，2026-07-22 由仓根迁入）；`${OPRUNWAY_PLUGIN_ROOT}` = 本插件根中立变量，Claude 下等价 `${CLAUDE_PLUGIN_ROOT}`。
> ⚠ **落点**：你为用户算子生成的 runner 落 **`<ops_root>/<op>/`**（`ops_root` = `$OPRUNWAY_OPS_DIR` 或 `${OPRUNWAY_WORK_DIR:-$CWD}/.oprunway/ops`），**不要写进插件的 `new_example/`**（插件安装目录升版即冲；工程约定要求产物落用户 CWD）。`repo_adapter.find_runner()` **只查用户目录、无 fallback**（fallback 已退役 2026-07-20）：**缺 runner 直接 fail-closed BLOCKED，引擎绝不回退插件样例**——runner 是引擎的**输出**、非组件。**核心纪律（Equal 教训）**：aclnn 入口/dtype/参数顺序**一律从算子自带 `test_aclnn_*.cpp` 抠、不猜**。

## 0. 契约（固定，与 `repo_adapter`/`run_on_npu.sh` 对齐，勿改）

- **runner 文件名**：`oprunway_<op.lower()>_runner.cpp`（`repo_adapter` 用 `caseset['op'].lower()` → IsClose=`oprunway_isclose_runner.cpp`；注意 build `--ops` 用 snake `is_close`，与文件名的 lower 不同）。
- 读环境变量 `OPRUNWAY_CASES` → `$OPRUNWAY_CASES/manifest.txt`，逐行一个 case。
- **manifest 行**（两种，**唯一真相源是 `repo_adapter.run_new_example` 里写 manifest 那段代码**，生成 runner 前实读一次）：
  - 传统行：`case_id dtype [attr…] ndim d0 d1 …` —— 这组 dims **既是输入形状也是输出形状**（elementwise）。
  - 扩展行（C1，caseset 带显式输出形状时）：`case_id dtype [attr…] out_ndim o0 o1 … in_ndim i0 i1 …`
    —— **第一组仍是输出形状**（与传统行同位同义），再补一组**输入**形状（host 已把各输入广播到它、逐个写成 `x{j}.bin`）。
    格式**按整份 caseset 定**（引擎侧变量 `extended_manifest`），不逐 case 摇摆。
    ⚠ **闸门是 `out_shape_source == "golden.out_shape"`（该算子真导出了 `out_shape()`），不是「caseset 里有没有 `out_shape` 字段」**——
    新版 `gen_cases` 每条 case 都写 `expected.out_shape`，但对 elementwise 算子它的来源是 `golden_fn_actual`、不触发扩展行。
    **今天 4 份样例 golden 全不导出 `out_shape` → 传统行才是常态，扩展行至今一次都没产生过。**
    ⚠ 且**别指望老 runner 自动兼容**：三份样例里 `oprunway_isclose_runner.cpp` 明写 `"extra fields in manifest line"` **显式拒**多余 token
    （只有另外两份靠 `istringstream` 忽略）。而 isclose 恰恰是三份里**唯一带 attr** 的模板、最可能被非 elementwise 算子拷去当骨架——
    拷它就必须同步改 ParseLine，别假设「多写几个 token 没事」。
  - **attr 段**按 spec `params` 里 `io=="attr"` 的出现顺序（`caseset.attr_order`），**一个 attr 恒占一个 token**：
    `bool`→`1`/`0`；`int`/`float`→`str(v)`；**`list[int]`→逗号连接的单 token**（`[3,4]` → `3,4`，C2；绝不用带空格的 `str([3,4])`）。
    空数组 / 嵌套 / dict / None / 含空白的串 → 引擎侧 **fail-closed 报错**，不产坏 manifest。无 attr 则无该段。
- 每 case 目录 `$OPRUNWAY_CASES/<case_id>/`：输入 `x1.bin`、`x2.bin`…（按输入序）；写 `out.bin`。
  ⚠ 「host 端已把各输入广播成同一个 shape」**只对输出形状 = 各输入广播结果的算子（elementwise）成立**——
  这是 `repo_adapter.run_new_example` 里 `out_shape = np.broadcast_shapes(...)` + `np.broadcast_to(arr, out_shape)` 那段的行为（现址 ~`:495/:506`，**行号会漂、认代码别认行号**）。输出形状另有来源的算子见 **§6**。
- **out.bin**：bool 输出→`uint8` 每元素 0/1；数值输出→与输入同 dtype 原始字节。
  字节数 = **输出** numel × sizeof(输出元素)。⚠ 三个样例 runner 里它恰等于输入 numel × sizeof，**那是 elementwise 的巧合、不是契约**（§6.2）。
- **双哨兵 + returncode**（`repo_adapter` 三者都查，缺一即失败）：runner 打印 `OPRUNWAY_DONE total=%lld ok=%lld fail=%lld\n`（结束）；远端脚本 `run_on_npu.sh` 完成后打印 `OPRUNWAY_NPU_DONE`；runner 退出码 `failed==0 ? 0 : 1`。
- dtype 支持（**实读 `repo_adapter._NP` + 样例 runner 的 dtype 分派**）：`float32`(ACL_FLOAT/`float`)、`float16`(ACL_FLOAT16/`uint16_t`)、
  `bfloat16`(ACL_BF16/`uint16_t` 位模式，逻辑值走 fp32-on-grid)。⚠ **bf16 有分支 ≠ 该算子真机能跑**——真机 kernel 支持须**逐算子确认**，未证实前走 deferred、不进 spec 的 `params.dtype`。
  int 系仍 **Track C**（runner 无分支）。不支持的 dtype 报错、不静默；超出的入 gap，别硬塞。

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

> ⚠ 上面这套「一个 `shape`、一个 `numel` 打通输入与输出」的写法**只装得下 elementwise**。
> 目标算子的输出形状 ≠ 输入形状时（归约 / 形状由属性推），**先读 §6 再动手**，别照旧骨架硬套。

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
- **输出形状（§6）**：该算子输出形状 = 各输入广播结果吗？
  - 是（elementwise）→ `golden.py` **不导出** `out_shape`，骨架照旧。
  - 否 → `golden.py` 必须导出 `out_shape`（§6.1）；runner 的输入 buffer 与输出 buffer **分开算**（§6.2）；
    manifest 行格式**去实读 `repo_adapter` 当前写法**再写 `ParseLine`，读不出明确编码 → 记 gap、返回 BLOCKED，**不猜**。
- **attr 含数组（`list[int]`）**：按逗号连接的**单 token** 解析（`3,4`），别沿用「一个 attr 只可能是一个标量」的假设；引擎明确拒的形态（空数组/嵌套/dict/None）→ 记 gap、BLOCKED（§0 + §6.2 末）。

## 6. 输出形状不再恒等于输入形状（C1 · 用户 2026-07-22 拍板）

> **缘起**：C1 之前，引擎把「输出形状 = 各输入广播的结果」当铁律——`repo_adapter.run_new_example` 里
> `out_shape = np.broadcast_shapes(...)` + `golden.shape != out_shape → raise`。
> 于是 elementwise 之外的算子（Pdist 这类归约、Upsample/im2col 这类形状由属性推）一律撞死在这道闸上
> （算子形态分类学清点：44 行里 17 行卡在它）。**校验没被删掉、换的是期望值从哪来**：
> 现在优先读 caseset 里**显式声明**的输出形状，没声明才退回同形假设。
> **用户 2026-07-22 定**：输出形状交给 per-op `golden.py`，**不在 spec 里搞表达式语言**
> （否掉表达式语言的理由：im2col 的实际公式带 floor / 连乘 / 多维归约，小表达式语言表达不下）。

### 6.1 `golden.py` 的**可选**导出 `out_shape(in_shapes, attrs)`

`<ops_root>/<op>/golden.py` 在**必需**三件套（`golden_fn` + `GOLDEN_SOURCE` + `GOLDEN_PROVENANCE`，由
`gen_cases.load_golden` fail-closed 校验）之外，**可选**再导出一个 `out_shape`：

```python
def out_shape(in_shapes, attrs):
    """输入形状列表 + 属性 → 输出形状 tuple。

    in_shapes: list[tuple[int, ...]]，按输入序（= spec params 里 io=="in" 的出现序），
               取自该 case 真实构造出来的输入数组。
    attrs:     dict，键 = spec 里 io=="attr" 的参数名；值可以是标量或 list[int]（C2）。
    返回:      int 序列（tuple/list 均可；允许 numpy 整数、允许 `()` 标量输出、允许含 0 的空 Tensor 形状）。
               非序列 / 负数 / bool / 非整数 → 引擎 fail-closed 报错（不猜、不修正）。

    ⚠ 诚实边界（**必须原样保留在 docstring 里**）：本函数是**代码、不是数据**——
       门没法「不执行就校验」它。它写得对不对，只有真跑起来才知道
       （与 golden_fn 实际产出的数组比形状、与真机 out.bin 的字节数比）。
       用户 2026-07-22 明确接受了这个代价（对照方案「spec 表达式语言」被否）。
       因此：只据任务书原文 / 算子的 *_infershape.cpp 公式写，**不猜**；
       写不准就**别导出**，把「输出形状规则未知」记进 task_pr_gaps 并停下。
    """
```

- **不导出 = 输出同输入形状**（elementwise 缺省语义，等同现行广播行为）。
  **现有 4 份样例 golden（IsClose / Equal / Sign / Neg）一律不加此函数**，行为零变更。
- **导出了就以它为准**：`gen_cases.load_golden` 现返回 **4 元组** `(golden_fn, GOLDEN_SOURCE, GOLDEN_PROVENANCE, out_shape_fn)`，
  第 4 项未导出即 `None`；导出了但不可调用 → fail-closed。
  `gen_cases` 每条 case 都把最终输出形状写进 caseset 的 **`expected.out_shape`**，
  并用 **`expected.out_shape_source`** 如实记来源：`"golden.out_shape"`（声明并已与实测对账）/ `"golden_fn_actual"`（未声明、取自 golden 实测）。
  下游（`repo_adapter` 造 manifest、runner 开输出 buffer、`validator` 比对）**据 caseset 走**，不各自重算。
- **它只回形状、不算数值**。数值恒由 `golden_fn` 出。别写成第二份实现；
  两者对不上时引擎**已 fail-closed**：`gen_cases` 逐 case 拿 `golden_fn` 实际输出形状与 `out_shape()` 声明对账，
  不一致直接报错（不许「以其中一个为准」静默糊过去——本仓最高纪律：fail-closed 优于静默降级）。

**非 elementwise 具体例子① · Pdist（归约）**：`(N, M)` 两两求距离 → 1D、长 `N*(N-1)/2`；
attr `p` 只改距离范数、**不改形状**（依据 `doc/oprunway-op-shape-taxonomy.md` Pdist 行，该行标 `verified`，
出处 `.../pdist/op_host/pdist_infershape.cpp`）：

```python
def out_shape(in_shapes, attrs):
    """Pdist: (N, M) → (N*(N-1)/2,)。attr p 只改范数、不改形状。
    ⚠ 代码不是数据，门不执行它就校验不了——见 runner-skeleton §6.1 诚实边界。"""
    n, _m = in_shapes[0]        # rank 恒为 2：由 spec 的 rank 约束保证（acc-spec 侧 C3）
    return ((n * (n - 1)) // 2,)
```

**非 elementwise 具体例子② · UpsampleNearestExact1d（形状由属性公式推 + attr 是 `list[int]`）**：
`(N, C, L) → (N, C, output_size[0])`（依据同上分类学 Upsample 行，标 `verified`）：

```python
def out_shape(in_shapes, attrs):
    """UpsampleNearestExact1d: (N, C, L) → (N, C, output_size[0])。
    output_size 是 list[int]（C2 放开后的 attr 值类型）；scales_* / exact_mode 只改语义、不改形状。
    ⚠ 代码不是数据，门不执行它就校验不了——见 runner-skeleton §6.1 诚实边界。"""
    n, c, _l = in_shapes[0]     # rank 恒为 3
    return (n, c, int(attrs["output_size"][0]))
```

⚠ **引擎侧消费状态**：`out_shape` 的读取与消费在 `gen_cases.py`（加载 + 逐 case 对账 + 写 caseset）与
`repo_adapter.py`（据 caseset 的 `expected.out_shape` 造 manifest）——**非本页所属文件**，本轮同批落地、
**本机无 torch 验不到 golden 通路**（真结论以真机为准）。**以引擎实际行为为准**：环境里的引擎若还是旧版
（`load_golden` 返 3 元组、caseset 无 `expected.out_shape`），导出了也不生效、按缺省同形走，
**不得据本页断言「非 elementwise 已通」**。

### 6.2 runner 骨架该怎么变（**本轮不要求你真写出新 runner**，但别照旧骨架硬套）

现骨架（`${OPRUNWAY_PLUGIN_ROOT}/samples/runners/oprunway_{sign,equal,isclose}_runner.cpp`）把
「输入 numel == 输出 numel」焊死在三处：

- `struct Case { std::string id, dtype; std::vector<int64_t> shape; };` —— **全 case 只有一个 shape**；
- `RunTyped<T>(dir, c, numel, ...)` —— 同一个 `numel` 既开输入 buffer 又开输出 buffer
  （`std::vector<T> x(numel), yh(numel)`），两次 `MkTensor` 都传同一个 `c.shape`；
- `WriteExact(out.bin, yh.data(), numel * sizeof(T))` —— 回写字节数也是那个 `numel`。

这条链在 elementwise 上成立，**只因 host 侧已把所有输入广播成 out_shape 再落 `x{j}.bin`**。
输出形状一旦不等于输入形状，这个前提就没了。要拆成两套量：

| 现在 | 要变成 |
|---|---|
| `Case.shape` 一份 | `Case.in_shapes`（按输入序逐输入一份）+ `Case.out_shape` 一份 |
| 一个 `numel` | `in_numel[j]`（读 `x{j}.bin` 用）+ `out_numel`（开输出 tensor、算 `out.bin` 字节数用）|
| `MkTensor(x, c.shape, …)` / `MkTensor(yh, c.shape, …)` | 输入用 `in_shapes[j]`、输出用 `out_shape`（strides 各自按自己的 shape 算）|
| `WriteExact(..., numel * sizeof(T))` | `out_numel * sizeof(输出元素)` |
| numel 溢出检查 / `numel==0` 兜底 各一处 | 逐输入 + 输出**各自**查（「空输入非空输出」「非空输入空输出」两种都要挡）|

**manifest 怎么把两组形状传进来**（§0 已列格式，此处只讲解析）：扩展行是
`case_id dtype [attr…] out_ndim o0 o1 … in_ndim i0 i1 …`。`ParseLine` 建议**自检测**、不必预先知道是哪种行：
读完第一组 dims（**输出**形状）后再试读一个整数——读到 → 那是 `in_ndim`、继续读输入维度；
读不到（行已尽）→ 传统行，**输入形状 = 输出形状**。多输入时 host 已把每个输入广播到同一个 `in_shape` 再落 `x{j}.bin`，
故一组输入维度对全部 `x{j}.bin` 通用。

⚠ 仍然**先实读一次** `repo_adapter.run_new_example` 写 manifest 那段再动手：格式的唯一真相源在引擎侧，
本页是**转述**（转述可能滞后）。对不上就是逐 case 解析错位，而且**不一定当场报错**——可能安静地测错东西。

**attr 为 `list[int]` 时（C2）**：引擎把它编成**逗号连接的单 token**（`[3,4]` → `3,4`），
一个 attr 仍恒占一个位置；`bool`→`1/0`、`int/float`→`str(v)` 不变。
runner 侧对应地按逗号拆这个 token 成 `std::vector<int64_t>`，再按 example 里的实参形式传给 aclnn
（`aclIntArray` 通常要 `aclCreateIntArray(data, size)`——**具体以算子自带 `test_aclnn_*.cpp` 那两行为准，别猜**）。
⚠ 引擎对空数组 / 嵌套 / dict / None / 含空白的串是 **fail-closed 报错**（会撑破空格分隔的行格式）——
遇到这类 attr → 记 gap、返回 BLOCKED，**别自造编码**。

### 6.3 形状通了 ≠ 能验收（别越界声称）

`out_shape` 只解「输出形状」这一道闸。同一批算子还卡在别处
（依据 `doc/oprunway-op-shape-taxonomy.md` §3.5 及其 dtype 表，相关行标 `verified`）：

- **dtype**：两个 Upsample 的**任务增量本身就是 `uint8`**、UpsampleNearest3d 还要 `double`，
  im2col 的 op_def 谱含 complex32/64 等 15 类；而 runner 侧只有 `float32/float16/bfloat16`（§0 dtype 行）
  → 仍入 gap、仍 BLOCKED。**也就是说：形状问题解决了，这两个任务的核心 dtype 仍然造不出来。**
- **输入 rank 被锁死**（Pdist 2、im2col 3/4、Exact1d 3、Nearest3d 5）→ 靠 spec 的可选 `rank` 约束收窄（acc-spec 侧 C3）。
- **强制特殊场景条目照产**：`gen_cases._special_entries` 的空 Tensor / 标量 / 边界 / inf-nan 是**必覆盖**项，
  rank 约束下只是被 `_fit_rank` **保 numel 调维**（不会被过滤掉）；而**两个 Upsample 的任务书明写「不支持空 Tensor」**。
  **当前 spec 没有「关掉空 Tensor 用例」的字段** → 撞上就记 `task_pr_gaps` 并停下问用户，别硬造非法用例充数。
