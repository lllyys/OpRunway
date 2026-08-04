# cannbot catlass-op-generator / ops-direct-invoke —— 可复用资产（面向 OpRunway Task 2）

> 来源：深挖 `repos/cannbot-skills/plugins-official/{catlass-op-generator, ops-direct-invoke}`（2026-07-01）。
> 定性：两者都是**「开发/生成」导向**（Architect 选型 → Developer 生成全新算子工程 → Reviewer 打 100 分），**不是验收导向**。复用时**剥掉「创作 + 评分」那层**，只取「怎么在 catlass 上 build/调用/跑/比对」的执行骨架。
> `catlass-op-generator` 是 `ops-direct-invoke` 的 catlass 特化派生（目录/op_host/测试同构，仅 CMake 追加 catlass 选项、op_kernel 改 catlass 模板拼装）。

---

## 1. 它本质是个 generated_harness 生成器（对应我们最难那个模式）

catlass 是模板库、kernel 跑不起来 → catlass-op-generator 生成一套自包含 `operators/{op}/` 调用壳工程。这正是我们 [[Repo adapter interface and modes]] 的 `generated_harness`。生成的工程：

```
operators/{op}/
├── op_host/{op}_tiling.h    # TilingData POD（host/kernel 共用）
├── op_host/{op}.asc         # main + ACL 初始化 + Tiling 计算 + <<<>>> 启动 + 结果搬回 + verify
├── op_kernel/{op}.asc       # catlass using 链 + Kernel{}(params)（★验收时这里直接指向 PR 的 kernel）
├── scripts/{gen_data,golden,verify_result}.py
├── CMakeLists.txt           # 标准 Ascend C + 注入 catlass 选项
└── run.sh                   # 编译 → gen_data → 跑可执行 → verify
```

**差异（务必记住）**：cannbot 的 Developer 是「从 DESIGN 现写 op_kernel」；**OpRunway 验收是「host 调用壳照搬，op_kernel 直接调 PR 交付的 kernel」**——少一层「创作 kernel」。

---

## 2. 三样直接可复用（generated_harness 执行后端）

| 资产 | 位置 | 复用 |
|---|---|---|
| **调用壳拆分法** | `catlass-op-generator/workflows/templates/design-template.md §1.4`；`workflows/development-guide.md §3.2` | 直接借鉴。example `main()` 拆成：host 侧(ACL 初始化/显存/`<<<usedNumBlocks,...>>>` 启动/结果搬回) + op_kernel 侧(`typename Kernel::Params params{...}` → `Kernel{}(params)`)；**去掉 example 自带的 DeviceGemm 适配器** |
| **CMake catlass 注入** | `development-guide.md §二`；`workflows/task-prompts.md` Step 3 | 直接复用。标准 Ascend C CMake **只追加** `-I${...}/catlass/include` + `-DCATLASS_ARCH`（arch：910b/910_93=2201、950=3510）；**禁止用 catlass 仓自家的 CMake 函数**（那是 example 构建辅助） |
| **确定性脚本 + 流水** | `workflows/scripts/verify_cmake_config.py`、`run.sh`、`verify_catlass_ready.sh` | `verify_cmake_config.py` 正则强校验 `-I…catlass/include` + `CATLASS_ARCH` 存在（返回 0/1）——**直接复用为构建门禁**；`run.sh`（编译→gen_data→跑→verify）复用骨架；`verify_catlass_ready.sh`（克隆 catlass + 防呆不进算子目录）借鉴 |

---

## 3. 精度 / 性能做法（部分借鉴，口径按 OpRunway 重写）

- **精度**：golden = CPU **NumPy**（`gen_data.py`/`golden.py` 共用），`verify_result.py` 做 atol/rtol；单层阈值 FP32 `1e-5`、FP16 `1e-3`、BF16 `1e-2`。→ 我们用[[ADR 0005 — Precision acceptance is a three-layer policy]]三层，比它细；可借 gen_data/golden 共用以保证对齐。
- **性能**：用 **`msprof op`** 采集，口径 **Task Duration（kernel-only）** + PipeUtilization + BlockDim。基线是「catlass 自家 example / 理论耗时」（<30%/<20%）——**基线源不适用**（我们要 GPU 标杆 + 任务书目标）。
- **精度问题诊断分类法**（`development-guide` / reviewer）：按输出特征区分「代码 bug」（全 0/NaN、仅特定核错、padding 参与）vs「精度问题」（FP32 好 FP16 差、误差随规模线性增长、所有 dtype 均匀不足）。→ **吸收进 validator 的不达标归因输出**。

---

## 4. 编排（ops-direct-invoke）—— 印证我们的结构，但我们的判定更强

- 范式：`AGENTS.md`(primary 剧本) + `workflows/task-prompts.md`(唯一执行手册，禁凭记忆自构造 prompt) + 4 agents（architect / design-reviewer / developer / reviewer）。Step 1 环境门禁→2 设计→2.5 设计串讲→3 开发→4 审查→5 修复循环(≤3)→**6 精度+性能验收**→7 汇报。与我们「阶段 + CP」同构，可作骨架镜像。
- 唯一机读 JSON：`environment.json`（`verify_environment.sh` 生成，`validation.all_passed` 作门禁布尔）——**环境门禁是好范式**。
- **它的三大缺口 = 我们已定的强项，别退回**：① perf/precision 证据是 `summary.txt` **文本**（我们要 JSON 机读）；② 达标判定靠 Reviewer(**LLM 主观**)读 summary（我们要**确定性 validator**，见 [[ADR 0007 — Verdicts come from a deterministic validator]]）；③ **无真正人工 CP**（我们用 AskUserQuestion 卡人）。

---

## 5. 三个坑（不能照搬）

1. **「生成/开发」≠「验收」**：别把 Architect 选型 / 设计串讲 / 100 分代码评分那套创作-评分机器搬进来——那是「评开发质量」，不是「评算子是否达标」。
2. **判定别退回「文本 summary + LLM」**：它没有 perf/precision validator（唯一脚本只校验 CMake）。坚持 JSON 证据 + 确定性 validator。
3. **catlass 内部视角口径不能套 ST 用例**：性能基线（自家 example/理论耗时）、测试 shape 偏好（L1 分块整数倍、避免过小 M/N）都是 catlass kernel 的脾气；OpRunway 的用例来自**任务书**，口径要按 OpRunway 重写。
