---
title: Repo adapter interface and modes
updated: 2026-07-02
status: canonical
reviewed: 2026-07-06
---

# Repo adapter interface and modes

被验收算子有**三种接入模式**——按 **OpRunway 要自造多少调用壳**、由低到高排风险（仓里现成 example → PR 自带 example → 我们生成调用壳）——仓适配器必须都覆盖（光有 example + msTuner 覆盖不了「只有 kernel/模板、无调用壳」的 PR，解决 Q2）：

- `existing_example` — 映射到仓里已有 example（低风险）；
- `new_example` — PR 自带 example，直接构建运行（中风险）；
- `generated_harness` — OpRunway 按用例生成调用壳/配置（**高风险，必须模板边界 + 人工确认**）。

**统一接口**（泛化到 ops-blas/ops-cv/tilelang 时换实现、不换接口）：`discover` · `build` · `materialize_case` · `run_correctness` · `run_perf` · `parse_results` · `collect_artifacts`。catlass 的实现依据 [[catlass acceptance mechanics]]。

**catlass 的 `generated_harness` 有现成配方**（借自 cannbot `catlass-op-generator`，详见 `doc/oprunway-cannbot-catlass-reuse.md`）：host 调用壳（ACL 初始化 + Tiling + `<<<>>>` 启动 + 结果搬回 + verify）+ op_kernel（catlass `using` 链 → **直接调 PR 交付的 kernel**）+ CMake 注入（`-I catlass/include` + `-DCATLASS_ARCH`，禁用 catlass 自家 CMake 函数）+ 确定性 `verify_cmake_config.py` 构建门禁 + `run.sh` 流水。与 cannbot 的关键差异：我们**包住 PR 现成 kernel**，不像它从 DESIGN 现写 kernel。接入 AscendOpTest 时，generated_harness 还要产出 [[catlass to aclnn bridge for AscendOpTest]] 的桥接层（封 aclnn 自定义算子，或自造遵守框架协议的 exe）。generated_harness 的 4 项通用职责见 [[generated_harness responsibilities]]。

是 [[OpRunway component breakdown]] 中 acc-npu-run「仓适配器」一侧的设计，吃 [[Acceptance contract and evidence chain]] 的用例。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]，[[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（三模式加「按需自造调用壳排风险」总纲）
