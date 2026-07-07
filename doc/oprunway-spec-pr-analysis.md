# 任务书规格 + 交付 PR 内容 · 规律总结

设计 workflow 前的地基。基于 `cann-ops-competitions` 7 月前 41 份任务书（模板统一）+ 深读 18 个代表性交付 PR（5 个 agent、file-level 核实、跨 6 仓、新算子/加dtype/移植 三类都覆盖）。

---

## 一、任务书规格（41/41 统一模板）

固定小节：`基础信息 / 任务概述 / 核心开发要求及验收标准（功能实现要求·测试标准·性能要求·精度要求·文档规范）/ 验收规则与流程 / TBE 参考路径 / 参考资料 / 环境获取 / 特别注意事项`。

关键字段（Task 1 输入契约照此定）：

| 字段 | 取值范围（实测） |
|---|---|
| **参考实现** | ① 内置 **TBE** 算子（重写类，给了 kernel/proto/config 路径）② **GPU 库**（移植类：cuSPARSE / cuBLAS / cuCollections / torch）③ 已有 Ascend C 算子（加 dtype 类） |
| **改动类型** | 新算子 / 加 dtype / 行为语义改造（如二进制比较→逻辑值比较）/ bug 修复（如 p=inf、负移位） |
| **精度 oracle** | 标准 GE 类多为 **AscendOpTest 默认阈值**；部分另给 **torch 对拍** / **experimental_standard MERE·MARE**（按 dtype 分级：FP32 2⁻¹³、FP16 2⁻¹⁰，MARE=10×）/ **ATK 双标杆**（SPMV，高精度 CPU 为真值，≤ 2/1.2/1.2）/ **scipy·std 精确**（Trsm/dynamicMap）/ **无**（Sleep 行为型） |
| **性能基线** | **由任务书按参考源定**：① **TBE 95%/无劣化**（重写类，及多数加 dtype 类如 Sign/Relu/Sigmoid）② **GPU A100 的 0.5–0.8×**（移植类，对标 cuSPARSE/cuBLAS/cuCollections）③ **相对同 op 其他 dtype 不劣化**（部分加 dtype 类如 IndexFill/upsample）④ 部分未列明。**任务书常带「小 shape 例外条款」**（如 <10us 差 3us 需仿真图证明） |
| **泛化** | 强制（控制类如 Sleep 例外）；**验收用泛化数据**跑功能/精度/性能全维度 |
| **交付件** | fork 仓（工程 + README + **多组 aclnn 调用测试**）+ **自验证报告** + **评审通过的设计文档**；后两者 **off-repo**（入 cann-competitions / 交昇腾小助手），**不在算子仓 PR 里** |

---

## 二、交付 PR 内容（结构规律）

### 工程范式三分天下（repo-adapter 必须都吃）
1. **标准 GE/aclnn 算子**（ops-math / ops-nn / ops-cv 主流）：`op_host(def/infershape/tiling) + op_kernel(.cpp/.h + tiling_key/data) + op_api(aclnn 两段式 + L0) + op_graph(proto) + config/<soc>/binary.json + tests(ST + UT) + docs + CMake`。ST 常走 **ATK**（JSON 用例 + torch/AscendOpTest 标杆）。
2. **experimental 库式**（ops-sparse SPMV / ops-blas Trsm）：自定义 `aclsparse*`/`aclblas*` **C API** + op_host/op_kernel + **自包含测试主程序**（自带 CPU/scipy golden）。**无 GE 注册、无 op_graph**。ops-blas 每算子目录**自带 `run.sh`**（去中心化）。
3. **纯头文件模板库**（ops-collections dynamicMap）：STL 风格，**完全无 op_host/op_kernel**，Catch2 单测。

### 「加 dtype」影响面常见 4 处（标准 GE 类）
① op_host `*_def.cpp` dtype 白名单 → ② op_kernel dtype 分支（窄/整型 `Cast` 升 fp16/fp32 算完转回、8B 用 `ReinterpretCast` 视图翻倍，靠 `if constexpr(sizeof(T))` 编译期路由）→ ③ op_host tiling 按 dtype 的内存/workspace/bufferCoefficient 倍增 → ④ 该 dtype 的测试用例。
⚠ **diff 大小 ≠ 影响面**：「改已有算子」原地改（Relu/Sigmoid/upsample）vs「新建目录」整包首次开源（IndexFill/SyncBatchNorm），后者表面全新增。

### 测试 / golden / 性能证据完整度差异极大
- **测试完整度**：零测试（Sign/Equal，只有 aclnn example + printf）→ UT + gen/compare golden（IsClose/foreach）→ 全套 ST + UT + **自验证报告进 PR**（Pdist）。
- **golden 来源多样**：numpy `np.isclose` / torch `interpolate` / scipy `solve_triangular` / host `std::unordered_map` / 内嵌 C++ double / 内建 host 参考对拍。
- **性能证据基本缺席**：仓内多**无 msprof/perf 脚本**；少数（Trsm 有 `Duration μs` 报告、dynamicMap 有 `std::chrono` 墙钟）。精度也多外包 AscendOpTest。
- **PR 自带 UT ≠ 精度验收**：部分 UT 只覆盖 workspace/shape/error 分支（如 Fmod 仅 `TestGetWorkspaceSize`、Mod kernel UT 仅 `SUCCEED()`）；许多 PR 的 golden / 验收证据在 PR 外（AscendOpTest / 外链自验证报告）。

---

## 三、给 workflow 设计的关键信号

1. **任务书是权威，PR 不逐项对齐** → Task 1 生成用例**以任务书为准**，把「任务书↔PR 落差」显式标为**待确认项**。实证落差：Fmod 任务书 INT16／PR 交付 INT32；im2col 任务书 A2/A3／PR 主攻 950；RightShift 10× 性能目标／PR 零证据。
2. **证据得 OpRunway 自己产** → 性能证据基本缺席、精度证据强弱不一。**这正是 OpRunway 的价值**：自产精度 + 性能证据，别指望 PR 里有、别把「PR 有测试」当「验收过了」。
3. **契约要覆盖的多样性维度**：
   - **验证模式**：数值型（golden + 阈值）／ 行为型（返回码 + 计时，如 Sleep）／ 精确型（整数/bool **二进制一致**）。
   - **精度口径**：AscendOpTest 默认 / MERE·MARE（dtype 分级）/ torch 对拍 / np.isclose（dtype 容差 fp16 1e-2·fp32 1e-4）/ 整数精确相等。
   - **性能基线**：TBE 95% / GPU-A100(0.5–0.8×) / 相对同 op 其他 dtype；**都按任务书**，+ 小 shape 例外。→ 印证 `perf_baseline_source` 枚举，且**基线随任务书/参考源/改动目标变**（移植类=GPU、重写与多数加 dtype 类=TBE、部分加 dtype=同 op 其他 dtype），不是单一 gpu_external。
   - **整型语义逐算子定**：AddList 饱和截断 vs MulList wraparound → **casegen 不能假设统一整型语义**，golden/oracle 必须逐算子对齐参考实现。
4. **repo-adapter 三模式要吃三范式 + 各仓不同的 build/run/golden/perf**：build（仓级 `build.sh` / 算子级 `run.sh` / `scripts/build.sh`）、golden（CPU内联/scipy/torch/STL/AscendOpTest）、perf（μs 报告/chrono墙钟/无）各仓不一 → 统一接口、按仓换实现（`existing_example`/`new_example`/`generated_harness` 映射到这三范式）。

---

## 附：18 个样本速查

| 算子 | 仓 | 改动 | 参考 | 精度 oracle | 性能基线 | 工程范式 | PR 内测试/golden |
|---|---|---|---|---|---|---|---|
| Sign | ops-math | 加 int16 | TBE | AscendOpTest | TBE 无劣化 | 标准GE | 无（仅 example） |
| Equal | ops-math | 语义改造 | TBE | AscendOpTest | TBE 95% | 标准GE | 无（5 example） |
| IsClose | ops-math | 语义改造 | TBE | AscendOpTest | TBE 95% | 标准GE | ut + numpy golden |
| Pdist | ops-math | 新算子+修复 | TBE/torch | AscendOpTest | TBE 95% | 标准GE+op_graph | st+ut+**自验证报告** |
| Fmod | ops-math | 新算子（任务 INT16/PR INT32） | TBE | AscendOpTest | TBE 95% | 标准GE | ut（仅 WS）·golden 外 |
| im2col | ops-math | 存量+950+bool（任务 A2/A3） | TBE | AscendOpTest/二进制一致 | ≥TBE | 标准GE·GE IR | 30 ut·golden 外 |
| RightShift | ops-math | 新算子 | torch | AscendOpTest | 10×原（PR 无 perf 证据） | 标准GE | ut 内建 host 对拍 |
| Sleep | ops-nn | 新算子(控制) | torch.cuda._sleep | **无（行为型）** | 无 | 标准GE(无 tensor) | 无（wall-clock 观察） |
| ForeachAddListV2 | ops-nn | 加 int16/8/uint8 | 存量 aclnn | AscendOpTest | — | 标准GE(共享模板) | ut + numpy(饱和) |
| ForeachMulList | ops-nn | 加 int16/8/uint8 | 存量 aclnn | AscendOpTest | — | 标准GE(独立+SIMT) | ut + numpy(wraparound) |
| Relu | ops-nn | 加 int16 | TBE | AscendOpTest | TBE 95% | 标准GE(改已有) | st(JSON/ATK)+ut |
| InplaceSigmoid | ops-nn | 加 int16/8/uint8 | TBE | AscendOpTest | TBE 95% | 标准GE(改已有) | ut·golden 外 |
| IndexFillTensor | ops-nn | 加 int16/8/uint8/double | 已有 AscendC | AscendOpTest | 相对同宽 dtype | 标准GE(新建目录) | ut 齐全·golden 外 |
| SyncBatchNormGatherStats | ops-nn | 新算子 | aclnn 文档 | AscendOpTest+**模型验证** | TBE 95%+模型 | 标准GE(双平台) | ut·外链报告 |
| SPMV | ops-sparse | 新算子(950) | cuSPARSE | experimental_standard/**ATK 双标杆** | **0.5×A100** | experimental 库式 | 测试主程序(内联 CPU·无 perf) |
| dynamicMap | ops-collections | 新容器 | cuCollections | 对齐 static_map(精确) | **0.7/0.5×A100** | 头文件库(Catch2) | Catch2 + perf(chrono) |
| UpsampleExact1d&2d | ops-cv | 加 uint8 | torch | AscendOpTest | uint8 vs fp16 ≤5% | 标准GE(SOC 分门) | st(ATK)+ut(torch golden) |
| TrsmBatched | ops-blas | 新算子(2 dtype) | cuBLAS | python 一致(PR 用 scipy) | **0.8×A100** | experimental 库式(算子级 run.sh) | test+perf(μs)+报告 |

> **核验说明**：`ops-sparse` / `ops-cv` 已 clone、本地核过（SPMV=库式、upsample=标准 GE 属实）。**`ops-math` / `ops-nn` / `ops-collections` 未 clone，`ops-blas` 的 `experimental` 本地为空（#243 open）→ 这些行的 PR 细节来自 agent + gitcode API，非本地可核。** 另：upsample uint8 在本地 `ops-cv` master 未见（`image/upsample_nearest` 仅 fp32/fp16/bf16）→ **交付状态存疑**（主 PR #1012 已 closed，见 `oprunway-task-pr-map.md`）。
