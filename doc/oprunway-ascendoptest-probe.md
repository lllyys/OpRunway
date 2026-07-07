# AscendOpTest 深挖（面向 OpRunway Task 2）

> 来源：workflow `ascendoptest-probe`（4 维并行 + 综合 + 对抗验证，2026-07-01），仓 `repos/AscendOpTest`（gitcode `HIT1920/AscendOpTest`）。
> 触发：任务书精度=AscendOpTest 默认阈值 + 用户「性能也能用」。**复用判定 = hybrid（对抗验证通过）。**

## 1. 精度 —— 高度复用

- **FP16 默认阈值 = `tolerance=1e-3` + `error_rate=1e-3`**（逐元素容差 1e-3，允许 **0.1% 坏点**）。第三位 0.1 遗留不读。其余：fp32/int=`1e-4`、bf16=`4e-3`、hfloat32=`1e-3`。（`compare/compare/accuracy_config.py:20`）
- **判据**（`compare.py:108-143`）：`|golden|≥1` 相对误差 / `<1` 绝对误差（共用同一阈值 `err_threshold[0]`）；坏点数 > 总数×`error_rate` 才 fail。inf→`finfo.max`、NaN==NaN 通过。case 不写阈值即回退 `default_acc`（`compare.py:150-152`）。
- **只出 pass/fail 布尔**，无 rtol/atol/mare/cos 等数值指标 → 报告要误差分布须 validator 自算。
- **golden = `expect_func`**（`golden/golden_gen/compute.py`）：我们写 numpy 融合参考（LN→分组 mm→bias→SiLU），输出 dtype 须=算子输出（float16）。仓内无现成 layernorm/silu/grouped golden。
- **复用** = `compare.py` + `accuracy_config.py` + 自供 expect_func。

## 2. 性能 —— 部分复用

- **唯一可消费口径 = kernel-only**：`msprof op --warm-up=5` → `OpBasicInfo.csv` 的 `Task Duration(us)` + Block Dim（`run_test.py:335`、`scripts/get_prof.py:60/65-66`）。exe 内部无计时/循环，时间全靠外部 msprof。→ 确认 ADR 0006「默认 kernel-only」。
- **不算加速比**：`get_prof.py` 只把 custom + built-in 两行并排写 `all_prof.csv`，无 speedup/ratio、无 1.2× 判定，且是手动脚本非 run_test 自动调用（`get_prof.py:88-92`）。
- **无 torch 通路**：参考实现只能是另一个 aclnn 内置算子（`-b`），跑不了 `torch.layer_norm+npu_grouped_matmul+add+silu` 未融合链（`cmake_generator.py:69-96`）。
- **device_e2e/host_e2e —— 能采、解析归我们（非「不能测」）**：采集层内建 `msprof --application`（`run_test.py:341`，含 H2D/D2H）能采整程 timeline，但自带 `get_prof.py` 只解析 kernel-only 的 OpBasicInfo.csv（`:60/:65`）、不解析 application 产物。→ **device_e2e 可行**，由我们驱动 --application + 自解析 op_summary + 裁到算子窗口（exe 是「aclInit→H2D→单次调用→D2H→WriteFile→aclFinalize」，整程时间含 init/文件IO污染，须裁窗）。融合类算子倾向 device_e2e。
- **结论**：1.2× 基线（torch 链）由 generated_harness 自测、比值由 validator 算；⚠ **必须同 timing_scope 对同 timing_scope**（torch 链是 host/e2e，别拿它 e2e 比融合 kernel-only）。

## 3. catlass 接入 —— 必须补桥（aclnn 导向）

框架硬绑 aclnn 两段式 API（`cpp_generator.py:543/548`）+ `cust_opapi` 链接（`cmake_generator.py:91`），只接受「已打包成 aclnn 自定义算子」的产物。catlass 裸 kernel 不能直测。两条桥（都落 generated_harness）：

- **路线 A（顺框架，终交付）**：封成 Ascend C 自定义算子（op_host 注册 + op_kernel 转调 catlass），部署 `opp/vendors/customize/`，导出 `aclnnLayerNormGroupedMatmulBiasSilu{GetWorkspaceSize,}`（符号名须与框架 CamelCase 推导一致）→ 原生跑。
- **路线 B（绕开，快验证）**：自写 exe 冒充（exe 名让 `run_test.py get_exe_name()` 抠到），接 `--case_name --timestamp`，读 data_gen bin、launch catlass kernel、写 output.bin → data_gen/compare 全复用。须避开 `--build`，性能测时 `msprof op --kernel-name` 用 `-k` 对上 catlass 符号。

## 4. 对 OpRunway 的结论（hybrid）

**Task 2 = 包一层 AscendOpTest 做精度（复用 compare + 我们供 golden）+ 自建 perf-ratio validator（算 1.2×、握最终判定）+ generated_harness 产出 aclnn 桥。** 精度不重造；性能的比值/基线/e2e 我们补；catlass 靠桥接入。

## 5. 待实测 / 待确认（开放）

1. **FP16 融合能否稳过 1e-3/0.1% 坏点**？Matmul 累加误差偏大，可能需 fp32 中间 golden 或按 bf16 放宽——M3 实测。
2. **1.2× 分母口径**（kernel-only 之和 vs device-e2e）——用户确认中。
3. **路线 A 动态 shape**：group(G)+可变 M 的 aclnn tiling / group_list 原型对齐。
4. **msprof op --warm-up=5 单次**是否够稳（无多迭代取中位）——抖动大则 harness 层多采取 min/median。
5. **路线 B 符号命中**：`--kernel-name` 能否精确匹配 catlass 生成 kernel（可能内联/改名）——实测用 `-k` 指定。
