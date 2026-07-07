---
title: catlass acceptance mechanics
updated: 2026-07-01
status: canonical
reviewed: 2026-07-01
---

# catlass acceptance mechanics

catlass（CUTLASS 风格的昇腾 C++ 模板库，matmul/GEMM 系）的验收三要素：

- **构建/跑单算子**：`scripts/build.sh <example> [-DCATLASS_ARCH=3510]`（Ascend950 必带 arch，默认 `2201` 为 Atlas A2/A3）→ `./output/bin/<example> m n k deviceId`，打印 `Compare success./failed.`。
- **精度 golden = CPU host float32**：库在 `examples/common/golden/`（`matmul.hpp` 算 golden、`compare_data.hpp` 判定，按 dtype + 计算量分层阈值），可源码级复用。
- **性能采集**：**验收默认用 `msprof op`**（profile 交付的 kernel，输出 Task Duration = **kernel-only**，不含 H2D/D2H）。catlass 自带的 **msTuner（`tools/tuner/`）是「搜最优 tiling」的调优工具，不是验收工具**——验收测的是 PR 交付的那个 kernel、不替它搜配置。两者同为 kernel-only Task Duration，timing_scope 一致。

这是 [[OpRunway acceptance pipeline]] Task 2 的事实依据。

**Verified.** 2026-06-29，对照 `repos/catlass`：`scripts/build.sh`、`examples/common/golden/compare_data.hpp`、`examples/common/golden/matmul.hpp`、`tools/tuner/README.md` 均存在并按内容指纹记录（见 `_verify.json`）。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]
