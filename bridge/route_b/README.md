# AscendOpTest 桥 · 路线 B（自造 exe，真机去风险）

把 canon 里还是 `proposed` 的「catlass→aclnn 桥」第一次在真机打通，给整条 Task 2 去风险。
**本轮只证机制**：AscendOpTest 能否驱动一个已知可跑通的 catlass kernel，走完
`data_gen → 自造 exe → compare` 的精度闭环，并用 `msprof op` 采到 kernel-only 性能。
载体故意选**最简、已验证 `Compare success.` 的 basic matmul**（默认 43，arch 3510），
不碰目标融合算子 `LayerNormGroupedMatmulBiasSilu`（其 catlass kernel 仓里还不存在，需算子 PR 提供）。

## 为什么是路线 B

AscendOpTest 生成的执行工程**只调 aclnn 两段式 API**（`cpp_generator.py:59-60,542-548`），
catlass 裸模板 kernel 不能进这条生成路径。两条桥：
- **A**：封成 aclnn 自定义算子走 `opp/vendors/` —— 默认写**共享 CANN**（`run_test.py:64`），共享机违规；且工作量大。
- **B**（本目录）：自写 exe 冒充被测 exe，**data_gen/golden/compare 全复用**，只替换中间那个 exe。不写共享目录、快。

## 桥怎么搭上去的（回源码钉死的契约）

1. **exe 名**：`get_exe_name()` 读 `aclnn_op/CMakeLists.txt` 第一条 `add_executable(` 行、`split("(")[1].strip()`
   （`run_test.py:294-303`）→ 名字**必须单独成行**。见 `aclnn_op/CMakeLists.txt`。
2. **不触发 aclnngen 覆盖**：`run_test.py:484` 三条件（`aclnn_dir` 不存在 / `--build` / `build/<exe>` 不存在）
   任一为真就重生成并 `rm -rf build`。所以：预置 `aclnn_op/` + 把编好的假 exe 放 `aclnn_op/build/execute_matmul_op` + **全程不带 `--build`**。
3. **exe 参数**：`--case_name --timestamp [--output_shapes]`（`run_test.py:335/343`）。
4. **路径协议**（均相对 `case_path`，缺省 = run_test.py 的 CWD）：
   - 输入 `op_test/<op小写>_<case小写>_<ts>/input/<name>.bin`（`compute.py`、`run_test.py:390`）
   - 实际输出 `.../output/<name>.bin`（`output_parse.py`）← 假 exe 写这里
   - golden `.../output/golden_<name>.bin`（`save_data.py`）← golden_gen 写
   假 exe 用环境变量 `OPRUNWAY_CASE_PATH` 作 base（缺省 `.`），须与 case_path 一致。
5. **golden**：`expect_func(*inputs)`，入参是逐个 `np.fromfile(dtype).reshape(shape)` 的数组，返回 numpy 数组 list（`compute.py`）。
6. **判定**：pass/fail 落 `result.csv` 的 `compare_result` 列（`verify_result.py`）；fp32 阈值 `1e-4`。
7. **符号命中**（perf）：catlass 裸模板符号是 `KernelAdapter<...>` 模板实例、不可预设 →
   先跑一次 `msprof op` 读 `OpBasicInfo.csv` 拿真实 demangled 符号，再用 `-k` 精确命中。

## 制品

| 文件 | 作用 |
|---|---|
| `fake_exe/oprunway_bridge_matmul.cpp` | 假 exe：复刻 43 的 catlass 启动，两端 IO 换成读/写框架约定 bin |
| `optest_cases/matmul_ir.json` | 算子原型（`op=CatlassBasicMatmul`，in x1/x2、out y，fp32） |
| `optest_cases/matmul_cases.json` | 单 case `Test_001`（512³，fp32，`err_threshold=1e-4`；`expect_func` 用 `__GOLDEN_PY__` 占位，run 脚本替换） |
| `golden/matmul_golden.py` | expect_func：`matmul_golden(x1,x2)`，fp32 累加，返回 `[y]` |
| `aclnn_op/CMakeLists.txt` | dummy，仅供 `get_exe_name()` 抠出 `execute_matmul_op` |
| `run_derisk.sh` | 编排：build（换源编→放 exe→还原）/ stage / precision / perf / perf_e2e，支持 `DRY_RUN=1` |

## 真机怎么跑（a5，arch 3510）

前置（用户目录内、不碰共享 CANN）：conda 环境 `oprunway`（numpy/ml_dtypes/build）；
AscendOpTest 部署到 `/home/lys/AscendOpTest`；把本目录 rsync 到 `/home/lys/optest`。

```bash
# 从 /home/lys/optest（= 本目录）运行
DRY_RUN=1 bash run_derisk.sh all      # 先干跑看命令
bash run_derisk.sh build              # 换 43 源→build.sh 编→放 aclnn_op/build/execute_matmul_op→还原源
bash run_derisk.sh stage              # 生成 matmul_cases.run.json，注入 golden 绝对路径并校验 exe
bash run_derisk.sh precision          # data_gen→假exe→compare；看 result.csv 的 compare_result
bash run_derisk.sh perf               # 先无 -k 跑一次读符号，再 OPRUNWAY_KERNEL=<符号> 重跑精确命中
bash run_derisk.sh perf_e2e           # msprof --application 端到端 timeline，含 H2D/D2H
```

## Go / No-Go

- **精度 Go** = `result.csv` 该 case `compare_result == pass`（fp32 1e-4）。关掉路线 B 管道类 unknown（exe 名解析 / Stage-B 跳过 / 路径对齐 / 写盘 dtype / CWD 对齐）。
- **perf Go** = `msprof op` 出稳定 kernel-only `Task Duration(us)` 且 `-k` 命中单符号。关掉开放项 ④（warmup 单次稳不稳）⑤（`-k` 命中）。
- 关不掉（需目标算子 PR / 后续轮）：①FP16 融合坏点率、②1.2× 分母口径、③路线 A 动态 shape。

## 诚实 caveat

- catlass 在真机是无 `.git` 纯拷贝，本轮不涉版本对账（目标算子验收时是硬伤）。
- 43 的真机 demangled 符号未实测，perf 的 `-k` 首轮靠读 `OpBasicInfo.csv` 得到。
- 假 exe 只在真机用 ASC 工具链编译验证；本地无法编。
- shape 改了要同步改 `matmul_cases.json` 的 shape 与假 exe 的 `-DOPRUNWAY_M/N/K`。
