---
title: catlass to aclnn bridge for AscendOpTest
updated: 2026-07-06
status: canonical
reviewed: 2026-07-06
---

# catlass to aclnn bridge for AscendOpTest

**白话：测试框架 AscendOpTest 只认「打包成标准 aclnn 的算子」，catlass 给的却是没打包的裸 kernel——要用它测 catlass kernel，得搭「桥」。两种搭法：A = 把 kernel 正式封成标准 aclnn 算子（正规、工作量大）；B = 写个「冒牌」exe 骗过框架去直调裸 kernel（取巧、快）。**

技术上：AscendOpTest 生成的执行工程**只调 aclnn 两段式 C API**（`aclnn<Op>GetWorkspaceSize` / `aclnn<Op>`；custom 链走 `cust_opapi`、built-in 走 `opapi`）——**catlass 裸模板 kernel 不能进这条生成路径直测**。两条桥都落到 [[Repo adapter interface and modes]] 的 `generated_harness` 交付物：

- **路线 A（正规封装，终交付）**——**把 catlass 裸 kernel 正式做成一个标准 aclnn 自定义算子、装进系统，让框架像测正规算子一样原生测它**。具体：op_host（tiling/infershape/InferDataType/OpDef 原型注册）+ op_kernel（内部转调 catlass 模板），导出 `aclnn<Op>{GetWorkspaceSize,}`（符号名 = IR `op` 原样、头文件 = `aclnn_<snake>.h`，须与 `cpp_generator` 推导一致；如 `CatlassBasicMatmul`→`aclnnCatlassBasicMatmul`）。**装法**：CANN 里自定义算子以「custom vendor 包」形式安装（`opp/vendors/<名>/`——这是 CANN 装自定义算子的**标准机制、非改动他人**）；**⚠ 共享机上须装到用户态 OPP**（`ASCEND_CUSTOM_OPP_PATH` 或 `--op-path <user_opp>/op_api` + 用户 `set_env`/`LD_LIBRARY_PATH`），**不写系统默认的共享 `opp/vendors/`**（`run_test.py` 默认 `--op-path` 指系统目录、共享机上不可用）。代价：整套 host 注册 + aclnn 封装工作量大（目标融合算子 = LN/grouped-mm/bias/SiLU 全注册一致）→ 宜「先最小 CatlassBasicMatmul 验证、再融合」。catlass 已自带 `examples/advanced/basic_matmul_aclnn`（op_host + op_kernel + json，op_kernel 用 `extern "C" __global__ __aicore__` 包装转调模板）作现成范式。
- **路线 B（走捷径，快验证）**——**不封装，自写一个「冒牌」exe 假装成被测算子：接住框架输入、直接调 catlass 裸 kernel、把结果写回框架要的位置；框架的造数据/golden/对比全照常复用。取巧、快、不写共享目录，但非正规交付**。具体：自写可执行冒充被测 exe，接受 `--case_name --timestamp [--output_shapes]`，按框架路径协议读 data_gen 产物（默认 `op_test/<op小写>_<case小写>_<ts>/input/<input_desc.data_path>`，未填 `data_path` 时才是 `<name>.bin`）、直接 launch catlass kernel、写默认 `.../output/<output_desc.data_path>`（未填时为 `<name>.bin`，**非固定 `output.bin`**）。data_gen/golden/compare 全复用。三个要点：① **exe 名**——`get_exe_name()` 取 CMakeLists 首条含 `add_executable` 行的 `split("(")[1].strip()`，故 exe 名须**单独成行**（复刻生成器换行格式），且注释里不得出现该命令串；② **框架不负责编自造 exe**——harness 须自带 CMakeLists、自编、把产物放进 `<aclnn_dir>/build/<exe>`，且**全程禁 `--build`**（否则触发 aclnngen 重生成覆盖工程）；③ **性能 `-k`**——catlass 裸模板符号是 `KernelAdapter<…>` 模板实例、**不可预设**，须先 `msprof` 实测符号或用 `extern "C"` 包装钉死，再喂 `msprof op --kernel-name`。

依据 [[catlass acceptance mechanics]] 与 [[ADR 0008 — Reuse AscendOpTest for Task 2]]。

> ⚠ 开放：路线 A 封 aclnn 时 group(G) + 可变 M 是动态 shape，GetWorkspaceSize/tiling 能否覆盖、group_list 作输入 tensor 还是 attr，需在原型设计时定。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]，[[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（Codex 审计修订：用户态 OPP / 自编 exe 闭环 / exe 名解析 / -k 模板符号 / 输出路径 / basic_matmul_aclnn 范式；+ A/B 白话 + 澄清 vendor 是自定义算子标准包机制）
