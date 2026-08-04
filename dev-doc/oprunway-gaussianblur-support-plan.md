# GaussianBlur 验收支持计划（2026-08-03）

> 状态：**v2（Codex 审后精简），待批**，尚未动任何代码。
> 目标：**用最短路径拿到第一个端到端结果**。结果是 PASS、FAIL 还是明确的运行错误都算数；
> 精度判错、覆盖不全、代码丑一律接受。唯一不可接受的是「改了一大堆、结果还是跑不起来」。
> 硬约束只保留两条：AGENTS.md 5.1（不得按算子身份特判）与 5.8（不得捏造/谎报标杆）。
> §2 的完整 gap 清单保留作台账，**但 §3 只实施其中一小部分**；被砍的见 §4。

---

## 0 · 本轮输入

| 项 | 值 |
|---|---|
| 任务书 | `https://gitcode.com/cann/cann-ops-competitions/blob/master/04_tasks/01_community-task-2026/docs/202607/GaussianBlur_task_doc.md` |
| PR（DUT） | `repos/ops-cv-TreamTik-feat-experimental-gaussian-blur`（ops-cv，分支名 `feat/experimental-gaussian-blur`） |
| 硬件 | Ascend 950PR（spec 里 soc 须写 `ascend950`，不是 `ascend950pr`） |
| 精度范围 | 仅 CV_32F（任务书 L1 一档），即 `DT_FLOAT` / fp32 |
| 本轮边界 | v1 原定纯本地；**v2 改为「先真机冒烟 → 再改框架 → 立刻真机跑」**（Codex 审后调整，见 §3、§7）。需按 5.2 放行远端操作 |

### 0.1 已核定的 PR 事实

`gaussian_blur/op_host/gaussian_blur_def.cpp`：

```
Input  "src" DT_FLOAT FORMAT_ND AutoContiguous
Output "dst" DT_FLOAT FORMAT_ND
Attr   "ksize" REQUIRED ListInt / "sigma_x" Float / "sigma_y" Float / "border_type" Int
AICore AddConfig("ascend950")
```

`gaussian_blur/op_api/aclnn_gaussian_blur.h`：

```c
aclnnStatus aclnnGaussianBlurGetWorkspaceSize(
    const aclTensor* src, const aclIntArray* ksize, double sigmaX, double sigmaY,
    int64_t borderType, const aclTensor* dst, uint64_t* workspaceSize, aclOpExecutor** executor);

aclnnStatus aclnnGaussianBlur(
    void* workspace, uint64_t workspaceSize, aclOpExecutor* executor,
    const aclTensor* src, const aclIntArray* ksize, double sigmaX, double sigmaY,
    int64_t borderType, aclTensor* dst, const aclrtStream stream);
```

三处偏离标准形态，是本轮全部 ABI 工作的根因：

1. `aclIntArray*` 作为形参（工具链从未支持过任何数组型参数）；
2. **stage2 是 10 参**，重复接收全部实参 —— 而不是标准的 `(workspace, size, executor, stream)` 4 参；
3. `dst` 在 stage1 写成 `const aclTensor*`（stage2 又写成 `aclTensor*`）—— DUT 自身接口不自洽。

`gaussian_blur/examples/test_aclnn_gaussian_blur.cpp` 是一份可逐字对照的 ABI 锚
（`aclCreateIntArray({7,7}, 2)` → 10 参 stage2 → 从 `dstAddress` D2H）。

### 0.2 用户口径（2026-08-03，覆盖任务书原文）

这三条是**全局原则**，不只对本算子生效：

| # | 口径 | 影响 |
|---|---|---|
| P1 | 性能无要求、或要求「与 GPU 比对」时，**一律只用 msprof 测 NPU 实测性能**，不做 GPU 对比、不等 GPU 数据 | 砍掉外部 GPU 标杆整条；不再走 `BLOCKED_WAIT_GPU_BENCHMARK`；任务书 S1 的 `0.45×` 比值门不作为裁决 |
| P2 | 精度**只对 OpenCV CPU**，不看 GPU | 任务书 §6 主口径写的是 OpenCV GPU，按 P2 改锚到 §4「功能比对以 **OpenCV CPU**（同版本）为标杆」与 §6 表格「CV_32F（L1）→ 对标 OpenCV CPU」两句 |
| P3 | **不必考虑 ATK** | ATK 双标杆（max rel ≤2 / mean rel ≤1.2 / RMSE ≤1.2）整条不实现，不挂待办 |

P1 须同步写进 `AGENTS.md`（仓规源），本文件只作引用。

---

## 1 · runner form 决策：`aclnn_py`

两路探针给出相反结论，采信 repo-build 侧，选 **`aclnn_py`**。

| 候选 | 判定 | 理由 |
|---|---|---|
| **`aclnn_py`** | **采纳** | 它**已经拥有 caseset、golden、输出采集和 workflow 接口，只缺几个明确的 ABI slot**（Codex 审后换的理由，比 v1 那句「未来 ops-cv 都受益」更站得住）；全程无 per-op 源码；本地唯一可机校的门 `preflight_aclnn.py` 只存在于这条路 |
| `cpp` / `new_example` | 否决 | 核心产物是一份 per-op `runner.cpp`，撞 AGENTS.md 5.1「手写 per-op runner 违规」；本地零可验证物；自带两条 blocking（`repo_adapter.py:962` + `run_on_npu.sh:22` 的「`op_src` 须 ≥2 段」深度代理，ops-cv 是仓根一级单段路径必被拒；`run_on_npu.sh:95-101` 在 `set -e` 下无条件链接 builtin-TBE 基线 exe，全新算子在 `libopapi.so` 里没有 `aclnnGaussianBlur` 符号，g++ 未定义引用会把整跑打断在正确性跑之前）；修完这两条也换不来任何 ABI 能力 |
| `cpp_extension` | 否决 | 严格更差：`cpp_extension_codegen._ATTR_CPP_TYPES` 只有标量没有 `IntArrayRef`，且它 emit 的 `EXEC_NPU_CMD_EXT` 宏本身假设标准两段式派发，对 10 参 stage2 同样对不上 |
| `mock` / `catlass*` | 不适用 | 物理上不产 `acceptance.json` / `verdict.json`，不是验收通路 |

**回归风险控制**：stage2 **只支持已观察到的两种结构** —— 标准 4 参走与今天逐字节一致的旧路径，
「框架三参 + 重复 stage1 实参 + stream」走新路径。不建通用 C ABI 解析器，Median 等既有热路径回归面收敛到零。

---

## 2 · Gap 清单

### 2.1 Blocking —— 不改就出不了裁决

| ID | Gap | 证据 | 量 |
|---|---|---|---|
| **B1** | `aclIntArray*` 形参被判「域外接口能力」，解析期就 raise；运行期连 `aclCreateIntArray`/`aclDestroyIntArray` 的 argtypes 都没声明 | `aclnn_runner.py:257-259 / 403-409 / 850-859 / 1341-1375` | M |
| **B2** | stage2 **静默错调**：`run()` 写死 `argtypes=[vp,c_uint64,vp,vp]` 并按 4 参调用，`parse_aclnn_signature` 根本不解析 stage2。aarch64 上 stream 会从垃圾寄存器取，段错误或错值都可能，全链无静态门能拦 —— **5.8 意义上最危险的一条** | `aclnn_runner.py:1403 / 1414`；`PR:aclnn_gaussian_blur.h:51-53` | M |
| **B3** | `_classify_param` 按 `\bconst\b` 判方向 → `const aclTensor* dst` 落 `role=in`，`num_outputs=0`，一个 D2H 都不做 | `aclnn_runner.py:253-256`；`contract_ir/prober.py:138 / 152-157` | S |
| **B4** | attr ctype 表既缺 `float64` 又产死 token：`_ATTR_CTYPE_MAP = {int64, bool, float32→"float", float→"float"}`，而 runner 的 `_ATTR_CTYPES` 只认 `{int64,bool,float32,float64}` 且对 `"float"` 有专门拒绝分支和锁死单测 → **aclnn_py 通路上今天任何浮点 attr 都是死的**（Median 只用 int64/bool 所以一直没暴露） | `gen_cases.py:2408-2431`；`aclnn_runner.py:404-408`；`test_aclnn_runtime.py:505-525` | S |
| **B5** | `spec` 能装 `list[int]` 值，却没有「数组属性」这个 slot 种类，`_build_aclnn_call` 造不出 ksize 槽 | `gen_cases.py:1651-1680 / 2458-2495` | S |
| **B6** | OpenCV 真值必然 tier4 blocked：`RUNNABLE_METHOD_KINDS` 硬编码 `{torch_cpu, numpy_cpu}`，填 `other_external`/`external_method` 都落 tier4 → `overall=blocked_golden_unauthorized`，整轮不产精度结论。唯一「能跑」的写法是谎称 torch/numpy，属禁止的静默换标杆 | `precision_policy.py:793-796 / 899-910` | S |
| **B7** | `_guess_op` 正则要求 `<族>/<op>/(op_host\|op_kernel\|op_api\|examples)/`，算子目录前**必须**还有一层；ops-cv 是仓根一级 `gaussian_blur/op_host/…`。实测 `_guess_op` 返回 `(None,None)` → `key_files={}` → `_detect_interface_kind({})` 判成 `library_header` 并 BLOCKED。手工把 `target_dir` 设成 `gaussian_blur` 后同样三份文件判出 `('aclnn_2stage','aclnnGaussianBlur')` | `fetch_source.py:157-163 / 361-374` | S |
| **B8** | `_aclnn_paths` 把 vendor 内容根拼成 `<vendor>_nn`，ops-cv 实际装到 `<vendor>_cv` → `OPRUNWAY_ACLNN_NOLIB`/`NO_VENDOR` | `aclnn_adapter.py:404 / 407 / 620 / 775` | S |
| **B9** | `_build_args` 固定发 `--experimental --no_force`：`--no_force` 不在 ops-cv 的 `SUPPORTED_LONG_OPTS` 里，`build.sh` 直接 `[ERROR] Invalid long option` 退 1；`--experimental` 会走只挂 `experimental/image\|objdetect` 的 CMake 分支，而 `add_subdirectory(gaussian_blur)` 在 `else()` 分支 → `check_compiled_ops` FATAL_ERROR。**分支名叫 `feat/experimental-gaussian-blur`，但算子目录在仓根，不在 `experimental/` 下** | `aclnn_adapter.py:567 / 569`；`PR:build.sh:22 / 97` | S |
| **B10** | `perf.shape_classification.hardware` 是只含 `"Atlas A3"` 的封闭表，`Ascend 950PR` 直接 ValueError，连 `--dry-run` 计划都产不出；同一张表在两个模块里各抄一份 | `gen_cases.py:205-208`；`validate_acceptance_state.py:468-491` | S |
| **B11** | `baseline=gpu_external` 时 NPU 侧 kernel 计时**根本不采集**，挂起态会被门判成证据破损 | `run_workflow.py:64-70 / 89-99`；`aclnn_adapter.py:1327-1334` | M |

> **B10 / B11 在 v2 里不修**：spec 整块省略 `perf` 即可绕过，见 §4.1。

### 2.2 Degraded —— 会跑，但结论会歪或不完整

| ID | Gap | 处理 |
|---|---|---|
| D1 | 任务书 §6 主口径是 OpenCV **GPU**，手册把 OpenCV-GPU 逐字列为 tier4 blocked、不自动回落 | 按 P2 锚到 CPU 两句走 `opencv_cpu` → tier1；报告须显式声明「**未验证 OpenCV GPU 标杆**」（cv::GaussianBlur 的 CPU 与 CUDA 实现并非逐位一致） |
| D2 | MERE/MARE 常量整批 `_MM_NOT_SETTLED=True`/`status=proposed`，未达只出 `uncertain` 而非 `fail` → `overall` 永远停在 `needs_review` | standard 用 `ascendoptest_default`（`\|expect\|<1` 走绝对容差 1e-4，图像域稳、可判 pass/fail），`acceptance_policy` 挂 `ecosystem_mere_mare` 作任务书口径放行层。**仍不等于跑过 AscendOpTest 工具本身** |
| D3 | 默认输入是 `rng.uniform(-5,5)`、不是非负图像域；通用路径没有值域旋钮 | 不改；报告不得声称覆盖了真实图像分布 |
| D4 | `operator_class` 只能诚实填 `floating_compute` → `_special_entries` 强制铺 inf/-inf/nan 三条。任务书 CV_32F 口径没要求这些语义，5×5 核会把整行污染，OpenCV CPU 与 NPU 两趟 separable 的 `inf−inf` 顺序差异足以产生不可比结果 —— **与 Median PR6429 那次误判同形** | **不改代码、不谎报类别**，写进 `task_pr_gaps` 并要求报告单列这三条 |
| D5 | `attr_matrix` 的行会被拆成每键取值集再重新笛卡尔，写不出「就这几组 attr 元组」；且 `gen_cases.py:18` 的 docstring 写着「显式列表语义，非笛卡尔」是**过时描述、与实现和单测相反** | 本轮把 `attr_matrix` 写到个位数行规避；顺手改掉那句 docstring |
| D6 | `contract_ir/prober.py` 不解析 stage2，却把 `full_signature` 拼成标准 4 参串并标 `"probe":"matched"` —— 凭空断言，属 5.8 | 3 行改成标 `needs_source`，顺手修 |
| D7 | `oracle_source` 六枚举里没有「第三方 CPU 库参考」这格，`cpu_ref` 被 `PRODUCIBLE_ORACLE_SOURCES` 按 R2 禁产 | 借 `external_ref` + `GOLDEN_SOURCE="external_ref cv2.GaussianBlur (OpenCV <ver> CPU)"`。账本读起来像「外部给了一份数据」、实际是本机 cv2 现算，真实出处只落自由文本 —— 如实记，**不新增第七个枚举**（那是 canonical 契约，牵动 gate 与多处对账） |
| D8 | golden 只能由 `golden_fn` 在 gen_cases 进程内现算，没有「预生成产物 + 内容哈希」入口；cv2 缺失要等真跑到才炸（无 preflight） | 接受；远端容器须装得上 `opencv-python-headless` |
| D9 | `GOLDEN_CONTRACT` 没有第三方库版本槽位，任务书要求的「同版本 OpenCV」无处机校 | 只把 `cv2.__version__` 写进 `GOLDEN_SOURCE` 自由文本 + `golden.py` 里做最低版本断言，不建机校收据 |
| D10 | direction override 是**声明**不是**证明**：工具不会自动去 example 里核 `dst` 确实是被写的那块 buffer | 由人核 `PR:examples/test_aclnn_gaussian_blur.cpp:95-97` 的 D2H 源后在报告里标为「人核事实」 |

### 2.3 已经支持 —— 不必动

- 运行形态识别：`aclnn_adapter.find_aclnn_project` 按**结构签名**判仓（仓根真实 `build.sh` + `<op_subdir>/op_host/` + 深度 ≤3 的 `aclnn_*.h`），ops-cv + 仓根一级 `gaussian_blur` 三条全中；`_safe_op_subdir` 不带 cpp 侧的「≥2 段」规则。库名 `libcust_opapi.so` 与 ops-nn 一致。
- dtype：`float32` 在 `SUPPORTED_NP_BY_FORM` 的 cpp / aclnn_py / cpp_extension 三种形态里全支持，`DEFERRED_NP_BY_FORM[aclnn_py]` 为空。
- SoC：确定性层无 SoC 白名单，`_SOC_RE=^ascend[0-9a-z_]+$` 放行 `ascend950`，一路透传到 `build.sh --soc=`；op 侧 CMake 已 pin `COMPUTE_UNIT ascend950`。
- 顶层 `hardware` / `repo` / `runner_form` 三字段可直接写（`hardware` 是自由文本数组）。
- attr 作为 per-case 取值已完整支持：`params[].io=attr` + `default` + 顶层 `attr_matrix`，`_check_attr_value` 明确放行 `list[int]`（`im2col.spec.json:87-142` 已实证）。
- `preflight_aclnn` 这道 CP-C0 静态门本身可用（核 completeness、核 head_sha、逐 header 核 `bytes_sha256`、按 `call_variants` 展开 slots 对账、成功只给 `READY_WAIT_NPU_TRUST_GATE` 不给裁决）；它今天挂掉纯粹是被下游 parse/`_classify` 拦住。
- fp32 精度链除 golden 来源外无缺口：`threshold_for` 对 fp32 三种标准都有条目、`compute_metrics` 浮点分支齐备、输出形状 == 输入形状故 `golden.py` 不必导出 `out_shape`。
- borderType 编码 PR 与 OpenCV **完全同值**（0/1/2/4，`PR:gaussian_blur_utils.h:21` + `PR:README.md:67`），golden 可把 `border_type` 直接透传 cv2，**无需映射表**。
- 1024×1024 fp32 这条 shape 物理上可达（`_LARGE_SHAPES[0]`），性能轮不必新造 shape 阶梯。

---

## 3 · 实施计划（v2，Codex 审后精简）

**总原则**：先证明 DUT 在目标机上能 build / load / call，再改框架。改完立刻上真机跑，按第一个失败点修。
下面每一步都刻意做小；凡是「为将来通用性」的部分一律推到第二轮。

> ⚠ Step 0 与 Step 5 涉及远端 clone / build / 跑测，按 AGENTS.md 5.2 **须先取得用户确认**；
> 开工前必须读 `.oprunway/real-machine.env` 的 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`，另选全新工作目录。

### Step 0 —— 解输入门 + 零改动真机冒烟（**不能放到最后**）

零代码。做两件事：

1. **拿到真 PR URL + head sha**（见 §6 Q1）。这是全局最大阻塞项：代码全改完，
   `validate_preparation_state` 与 `preflight_aclnn` 仍硬要求 `source_facts.completeness == "complete"`，
   照样跑不起来 —— 正是「改了一大堆还是跑不起来」那个失败模式。
2. **按 PR 自己的方式 build，跑它自带的 `gaussian_blur/examples/test_aclnn_gaussian_blur.cpp`**（固定输入）。

这一步只回答一个问题：**DUT 在目标机上到底能不能 build、load、call。**
不能的话立刻停手，不要动 OpRunway 任何代码。
能的话也**只是 DUT 冒烟，不是验收裁决**，不得冒充流水线结论（5.8）。

### Step 1 · 2 · 3 —— 三个并行的最小代码改动

| # | 文件 | 只做这些 |
|---|---|---|
| **S1** | `plugin/acc-common/aclnn_runtime/aclnn_runner.py` | ① `aclIntArray*` 的建/传/销毁（**只这一种数组类型**）；② stage2 **只支持已观察到的两种结构** —— 标准 4 参、以及「框架三参 + 重复 stage1 实参 + stream」，不建通用 C ABI IR；③ 输出方向从 **stage2 的非 const `dst`** 直接得出（stage2 写的就是 `aclTensor* dst`，白拿）。<br>**不做**：通用参数种类表、`aclFloatArray`/`aclBoolArray`/`const char*`、可复用的 direction_overrides 机制 |
| **S2** | `plugin/acc-common/gen_cases.py` + `precision_policy.py` | ① `double`/`float64` 映射对；② `list[int] → int_array` slot；③ `opencv_cpu` 进 `RUNNABLE_METHOD_KINDS`（**故意不放行 `gpu_lib`**）。<br>**不做**：perf shape profile、docstring 修复、`spec_schema_template.jsonc` 与 `golden-authoring.md` 的全量文档同步、`test_precision_policy.py` 的组合计数矩阵同步 |
| **S3** | `plugin/acc-common/aclnn_adapter.py` + `fetch_source.py` | ① vendor 后缀改成**必填读 `OPRUNWAY_ACLNN_VENDOR_SUFFIX`**（不做 basename 正则推导、不折进缓存前缀）；② `--experimental` 仅对 `experimental/` 前缀的 op 加；③ 默认 `--no_force` 删掉；④ `fetch_source` 加一个 `--target-dir` **覆盖参数**（约 3 行，零回归面）—— 已核实 `target_dir` 今天只由 `_guess_op` 自动猜、**没有任何 CLI 覆盖口**，所以「显式指定绕过探测器」这条路今天并不存在；加覆盖参数比改正则安全得多。<br>**不做**：`_guess_op` 正则改写（会动到既有 median / im2col 的多层目录语义） |

### Step 4 —— 一个 golden + 一个最小 spec

依赖 S1/S2 的字段定型。

- `plugin/samples/golden/GaussianBlur/golden.py`：`cv2.GaussianBlur` 延迟 import，
  border 值**直接透传不映射**（PR 与 OpenCV 编码同值，已核）。
- `plugin/samples/specs/gaussian_blur.spec.json`，**只留**：fp32 / 一个小 shape /
  一组 `ksize·sigma·border` / OpenCV CPU golden / `runner_form=aclnn_py` / `target_dir=gaussian_blur` /
  **`perf` 整块省略**（模板明确允许）。
  **不做**：attr 矩阵、特殊值覆盖、大 shape、版本收据、任务书 case 全覆盖。

### Step 5 —— 真机跑最短流水线，按第一个失败点修

```text
gen_cases → golden → preflight → build/install → 单 case runtime → validator/workflow
```

第一目标是拿到真实的 `PASS` / `FAIL` / 明确运行错误，**三者都算「跑起来」**。
在这一步出结果之前，不补任何被砍项、不扩测试矩阵。

### 并行关系

```
Step 0  ──────────────────────────►  (门：不过就停)
          ├── S1 ┐
          ├── S2 ├── Step 4 ── Step 5
          └── S3 ┘
```


## 4 · 明确不做（挂账，不静默跳过）

### 4.1 Codex 审后新增砍掉的（v1 里有、v2 里没有）

| 项 | 砍掉的理由 |
|---|---|
| **`perf.mode="measure_only"`（原 A6）** | 它不是一个性能开关，而是**新增一种跨模块状态语义**，横跨 `perf_compare` / `run_workflow` / `validate_acceptance_state` 三个文件。很可能出现三方对 `measured` 理解不一致，最后**精度已经能跑却被性能状态机卡死**。本轮改为 spec 整块省略 `perf`。<br>⚠ **遗留张力**：`AGENTS.md` 5.10（P1）已经写成仓规，但本轮不实现它 —— 任何声明了 `perf` 的 spec 仍走老的比值门。这条规则目前**只有文字、没有代码**，第二轮补 |
| **`_guess_op` 正则改写（原 A4）** | `(g2, g1+g2)` 这种捕获组拼接容易改变既有多层目录语义，动到 median / im2col。已核实 `target_dir` **今天没有任何 CLI 覆盖口**，所以改为加一个 `--target-dir` 覆盖参数（约 3 行、零回归面），探测器不动 |
| **`prober.py` 诚实性修复（原 A8）** | 与当前运行链无关，不阻塞任何调用。值得以后修，今天不花这个时间 |
| **`gen_cases.py:18` docstring 修复（原 D5 顺手项）** | 文档错误不阻塞任何调用 |
| **`spec_schema_template.jsonc` / `golden-authoring.md` / `test_precision_policy.py` 全量同步（原 A3 的一半）** | `precision_policy` 有组合矩阵、授权等级、schema 和测试计数，全面同步很容易扩散。首跑只改实际运行门所需的枚举集合 |
| **`vendor_suffix` 正则推导 + fail-closed + 折进缓存前缀（原 A5 的一半）** | 同时影响配置、路径、shell 模板、库加载和缓存 provenance，任一处漏改都表现为 **build 成功但 load 失败**。缩成一个必填环境变量 |
| **可复用的 `direction_overrides` 机制（原 A1③ + A7 整步）** | stage2 写的就是非 const 的 `aclTensor* dst`，解析 stage2 时**白拿方向**，不必再造一套 spec→runtime 的方向覆盖通道 |
| **通用 C ABI 参数种类表 / stage2 通用解析器（原 A1 的野心）** | C 声明解析比看起来危险：换行、宏、注释、const 差异、参数名差异都会扩大范围。首版**只支持已观察到的两种 stage2 结构** |

### 4.2 原本就不做的

| 项 | 原因 |
|---|---|
| **定向用例 TC-03/04/05/06/08/09/10/11** | 需要 `explicit_cases` 档 + `expect_raises` case kind + ROI/非连续 step/in-place 语义，要同时改 caseset schema、runner 返回码语义、validator 三处，成本远超本轮。全部按「未覆盖」挂账，**不是「通过」** |
| **`aclFloatArray` / `aclBoolArray` / `const char*`** | 本轮只放开 `aclIntArray` 一种；ops-cv 的 `aclnnResize`（aclFloatArray）、`aclnnIou`（const char*）仍 fail-closed，作为通用化欠账 |
| **ATK 双标杆** | 按 P3 不做。`compute_metrics` 结构上只吃两个数组，全仓无 RMSE。spec 的 oracle 写 `mere_mare` 而不是 `atk_double`，避免 `select_standard` 把 `atk_double` 静默映射成单标杆后账本自称走了双标杆 |
| **GPU 真值 / GPU 性能标杆 / `gpu_baseline_request.json`** | 按 P1、P2 不做 |
| **`contract_ir` 与 `cpp_extension` 两条旁路** | `contract_ir` 没接进任何验收流水线（`dev-doc/oprunway-todo.md:101` 仍是 F3 待办）且 `CreateAclIntArrayFromCase` 只有声明无实现；`cpp_extension_codegen` 既无数组 attr 类型又用 `EXEC_NPU_CMD_EXT` 假设标准 stage2。两条都不投入 |
| **OpenCV 版本/构建指纹的独立收据** | 只做 `golden.py` 内的最低版本断言 + `GOLDEN_SOURCE` 自由文本，不建机校收据 |
| **`_PERF_SHAPE_PROFILES` 抽公共文件** | 两处重复表本轮不重构，只让 spec 能直供边界值绕过它 |

---

## 5 · 任务书 ↔ PR 实质冲突（工具侧不消解，原样进报告）

| # | 冲突 | 证据 |
|---|---|---|
| **C1** | 任务书「验收口径」把 **OpenCV C++ 层 `cv::GaussianBlur()` 定为唯一验收基准**，但本 PR 只交付 aclnn + Ascend C kernel，`cv_hal` 与 OpenCV 适配层**根本不在树里** —— 被测对象与验收基准不对齐 | 任务书 §「验收口径」/ §7 接口分层；PR 树 |
| **C2** | 任务书 §3.3 要求 in-place **须支持**，PR 明确拒绝 | `PR:gaussian_blur/op_api/aclnn_gaussian_blur.cpp:205` `CheckInplaceUnsupported` |
| **C3** | DUT 自身接口不自洽：stage1 `const aclTensor* dst`、stage2 `aclTensor* dst`；同仓其它算子（`objdetect/iou_v2`、`image/resize_bilinear_v2`）都是标准 4 参 stage2，只有 GaussianBlur 偏离 | `PR:aclnn_gaussian_blur.h:33 / 51-53` |
| **C4** | 任务书 §6 主口径写 OpenCV **GPU** 真值，本轮按 P2 只对 CPU | 任务书 §6；用户口径 P2 |

**C1/C2 需要与出题方或开发方对齐**，工具侧不能替它们做决定。

---

## 6 · 待用户决策

| # | 事项 | 选项 |
|---|---|---|
| **Q1** | **PR provenance**：DUT 目录没有 `.git`（只有 `.gitcode`/`.gitignore`），拿不到 head sha。`fetch_source` 只认活的 gitcode PR 链接，硬造 `pr_facts` 会因至少八条 reason 判 `completeness=blocked`，而 `validate_preparation_state` 与 `preflight_aclnn` 都硬要求 `complete` | (a) 提供真 PR URL + head sha（**推荐**）<br>(b) 建降级的 `--pr-snapshot`：产 `provenance_kind="local_snapshot"`、`head_sha=null`（**绝不合成 40 位 hex**）、`snapshot_merkle_sha256`。但 merkle 只证「本地这份字节是什么」，**不证**它等于任何 PR head，最终裁决不得声称已绑定 PR head |
| **Q2** | **C1/C2 两条实质冲突** | (a) 按「验收 aclnn 层、冲突挂账」推进（**推荐**，不阻塞）<br>(b) 先去和出题/开发方对齐再动手 |
| **Q3** | ~~性能维本轮做不做~~ | **已定：不做**。Codex 审后 spec 整块省略 `perf`，950PR 的 UB 边界这个未知硬件事实随之不再阻塞。见 §4.1 的遗留张力 |
| **Q4** | **本地不跑 pytest**（AGENTS.md 5.3 + 用户多次明示）| 这批改动会**写测试但不执行**，只做纯 stdlib 的 `python3 -c` 静态自检。交付时单测处于未验证状态，要等远端那轮才绿 |
| **Q5** | **Step 0 / Step 5 要上真机**（clone / build / 跑测），按 5.2 须先确认 | v1 是纯本地轮；v2 把「零改动真机冒烟」提到最前面，因为「先证明 DUT 能 build/load/call」比改框架优先级高。**需要你放行远端操作** |

另有一条已知 CP-B0 输入缺口：任务书本身有至少四项 must 会走 `stop` 且只能用
`decisions[].action="supplied"` 解除 —— `performance_metric_scope`（全文无 kernel-only vs 端到端、无 warmup/repeat）、
`dependencies_and_prerequisites`（A100 + 同版本 OpenCV「请开发者自行准备」）、`deliverable_and_dut` / `integration_form`（即 C1）、
`acceptance_completion_criteria`（「性能验收取最优实现」不可机械判定）。这是**输入缺口不是工具缺陷**。
其中 `performance_metric_scope` 与 `acceptance_completion_criteria` 两条按 P1 已自动消解。

---

## 7 · Codex 审记录（2026-08-03，`gpt-5.6-sol` / reasoning low，只读）

评审标准：委托人要求「plan 足够简单、能快速实施、有瑕疵没关系、一定要能先跑起来、结果错误无所谓、拒绝过度设计」。

### 采纳的意见

1. **最大的漏项：Q1 的 PR provenance 根本没进实施步骤。** 代码全改完，CP 门仍会把整轮拦下 ——
   正是委托人最怕的「改了一大堆、结果还是跑不起来」。已提为 **Step 0 第一项**。
2. **新增「零改动真机冒烟」为 Step 0**：先编译并跑 PR 自带的 `test_aclnn_gaussian_blur.cpp`，
   它已经包含 `aclCreateIntArray` + 正确的 10 参 stage2 + 输出 D2H。
   若 DUT 本身都 build/load 不起来，应立即停手，不要动框架。
3. **风险倒置**：v1 判为 S 的 A5（vendor suffix）实际横跨配置/路径/shell 模板/库加载/缓存 provenance，
   漏改一处就是「build 成功但 load 失败」；A3 也不是「加个枚举」那么简单。两者都已缩水。
4. **可以三行糊过去的大项**：B3 从 stage2 的非 const `dst` 白拿方向；B8 一个必填环境变量；
   B10/B11 删掉 spec 的 `perf` 整块。
5. **CUT**：A4（改为加覆盖参数）、A6、A8、docstring 修复、文档/测试全量同步。

### 未采纳 / 需要修正的

- Codex 建议「显式提供 `target_dir=gaussian_blur`，不改探测器」。**前提不成立** ——
  已核实 `fetch_source` 的 `target_dir` 只由 `_guess_op` 自动猜，
  CLI 只有 `--taskdoc / --pr / --out / --snapshot-into` 四个参数，**没有覆盖口**。
  故改为加 `--target-dir` 覆盖参数（3 行），仍比改正则安全。

### 确认无误的

- **`aclnn_py` 是对的**，理由要换一个：不是「未来 ops-cv 都受益」，
  而是它**已经拥有 caseset、golden、输出采集和 workflow 接口，只缺几个明确的 ABI slot**。
- **`cpp/new_example` 没有被错误挡掉**：即使放宽一级 `op_src` 并跳过 builtin 链接，
  仍得写或生成 GaussianBlur 专用的数组属性和 10 参调用 —— 工作量不比 S1/S2 少，
  只是把改动从 Python runtime 转移到一份算子专用 C++ 文件。
  唯一合规例外是造「按 header 自动生成 runner 的通用生成器」，
  而为一个首跑造生成器**是更严重的过度设计**。

---

## 8 · 实测记录（2026-08-03，950 容器）

> 本节只记**真实跑出来的结果**。计划里的预判凡被实测推翻的，以本节为准。

### 8.1 Step 0 零改动真机冒烟：**全通**

按 Codex 审后提到最前面的这道门，四步逐个过：

| 步 | 结果 |
|---|---|
| build | `build.sh --pkg --soc=ascend950 --ops=gaussian_blur --vendor_name=oprwgb` 编过，产**唯一**一个 `.run`（1.2 M） |
| install | 落地 `vendors/oprwgb_cv/`，`libcust_opapi.so` 603 K |
| 符号 | `nm -D` 见 `aclnnGaussianBlur` 与 `aclnnGaussianBlurGetWorkspaceSize`，均 `T`（已定义） |
| 真机执行 | PR 自带 example 输出 `GaussianBlur succeeded, output[0]=0.484254`，`exit=0` |

结论：**DUT 本身在 950 上是可 build / 可加载 / 可执行的**，后续框架改造有意义。

### 8.2 计划里两条预判被真机坐实

- **B9**：`no_force` 在 ops-cv `build.sh` 的 `SUPPORTED_LONG_OPTS` 里出现 **0 次** → `--no_force` 必然
  `Invalid long option` 退 1。`CMakeLists.txt:138-149` 证实 `ENABLE_EXPERIMENTAL=ON` 只挂
  `experimental/image|objdetect`，而 `add_subdirectory(gaussian_blur)` 在 **else 分支**——
  带 `--experimental` 反而把被测算子排除掉。
- **B8**：install 实际落 `vendors/oprwgb_**cv**/`，写死 `_nn` 必然 `NOLIB`。

### 8.3 探针漏掉、实跑才暴露的两个硬阻塞

| # | 问题 | 处理 |
|---|---|---|
| **S4** | `_aclnn_cfg` 对取源是**强制 git**：`PR_REF` 必须 40 位 SHA 或 `refs/merge-requests/<N>/head`，`BASE_REPO` 必须 http(s) git 远端，install 脚本里是 `git fetch --depth 1` + `rev-parse` 比对。而本轮 DUT 是无 `.git` 的快照，且**实测确认 `cann/ops-cv` 上游不存在该 PR**（扫 600 个 MR 无命中；任务书自己写明代码在**私仓**）。这不是"链接还没拿到"，是客观不存在 | 新增 `OPRUNWAY_ACLNN_SOURCE_MODE ∈ {git_fetch, local_snapshot}`，缺省 `git_fetch` 且逐字节不变。`local_snapshot` 跳过 fetch、改校确定性 merkle，`head_sha` 一路透传为 **null**——合成 40 位 hex 是 5.8 禁止的造假 |
| **Py3.11** | `aclnn_driver.py:266` 的 f-string 表达式跨越了隐式拼接的字面量，那是 **PEP 701（Python 3.12+）语法**。容器是 3.11.15，该文件**在未改动的 HEAD 上就无法 import**。此前没暴露，是因为既往真机工作都在 Python 3.12.13 的 A2/A3 上 | 改成先算普通变量再单点插值。**工具链有一条未声明的 Python ≥3.12 依赖**，需要在 CI/门里补一道目标版本语法检查 |

**一条流程教训**：本地 Python 3.14 上 `py_compile` 通过**不能代表目标环境通过**。
凡要在容器里跑的代码，权威语法检查必须用容器的 python3 做。

### 8.4 merkle 两端一致性（自己踩了自己设计的坑）

`local_snapshot` 的取源段最初用 shell 的 `sha256sum | sha256sum`，而 intake 侧
`fetch_source._snapshot_merkle` 是「逐条 `sha256(相对路径)` + `sha256(内容)` 再喂总摘要」——
**两种算法，两端永远对不上**，症状会表现成「快照没改却 `SNAPSHOT_MISMATCH`」，极难归因。
已改为在取源段内联同一段 Python，跳过目录名从 `fetch_source` **惰性导入**（不复制第二份）。
真机实测两条代码路径对同一目录得同一摘要
`203d4b77f3016f0513832cb87946dcddb48c43658ee6b2d48a247a92af25d049`（2565 个文件）。

### 8.5 单测：零回归

在同一容器内跑六个相关测试文件：

| | passed | failed |
|---|---|---|
| 未改动的 HEAD（仅打上 Py3.11 语法修复） | 545 | 9 |
| 本批改动后 | **599** | **9** |

失败的是同一批 9 个，全部与容器内 root 身份下的 setenv 软链/权限守卫等环境因素有关，
在未改动的 HEAD 上同样失败 → **本批改动零回归**，净增 54 项通过。

> 期间发现两处 agent 写的测试与其自身实现打架（测试写了但按指令未执行）：
> `test_build_sh_six_flags_at_repo_root` 仍断言已被移除的 `--no_force`；
> `NormTargetDirTest` 期望绝对路径被接受、而实现对其 fail-loud。两处均以**实现为准**改测试。
