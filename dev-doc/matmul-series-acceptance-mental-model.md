# 矩阵乘系列验收 · 心智模型与目标

> 2026-08-25 交接记录。只记"是什么/为什么/目标"，不记实现方案。供回归 main 后的会话接续。

## 三方角色模型

- **验收方（本仓 skill 的目标定位）**：出任务书、发验收工装、收交付做验收。
- **开发者**：按任务书在 ops-blas 仓交付算子实现与测试桥接件，用同一套工装自测后提交。
- **ops-blas 仓**：约定优于配置的流水线（机床），按目录名与文件名自动发现新算子。

## skill 的三块职责（用户定义的目标态）

1. 生成任务书（接口定义、公式、硬件目标、GPU 基准）。
2. 生成脚本与测试用例（CSV 题库、gen_csv、verify_accuracy/verify_performance、gpu_baseline、README 契约说明）。
3. 验收：拿开发者的算子工程 + 开发者编写的测试组件（test/ 中缺少但我们脚本需要的 C++ 六件套桥接件），用我们的测试用例跑验收。

## 公私边界

- **我们的**：任务书、CSV 题库、判据脚本、GPU 基线。自测与验收同一套，无信息差。
- **开发者的**：算子实现（blas/<family>/arch*/ 的 host+kernel）、公共头声明、C++ 六件套（CMakeLists / param.h / golden.h / test.cpp / CSV 部署 / wrapper.h）。
- **契约面仅三样**：CSV 列格式 ↔ param.h 解析；二进制路径约定 build/test/<family>/<op>/<op>_test；GTest 输出格式。契约以脚本为准而非文档。

## ops-blas 仓机制要点（已查实）

- build.sh --ops=<名> → expand_family_ops 家族展开 → CMake TEST_NAMES；--soc 映射架构目录（ascend910b→arch22，ascend950→arch35）。
- blas/ 与 test/ 均目录 GLOB 自动发现；新算子唯一必须编辑的既有文件是 include/cann_ops_blas.h 的接口声明。
- 精度判分发生在开发者二进制内部（golden.h 调 OpenBLAS + test.cpp 阈值比对）；性能判分在我们脚本侧（对 gpu_baseline，NPU ≤ GPU/0.8）。

## 5 个任务算子现状（repos/ops-blas master）

- **cherk**：已上游落地（test/herk/cherk 六件套，仅 arch35；blas/herk 存在）——唯一活样板，可作端到端演练对象。
- **chemm、cher2k**：家族目录不存在（test/hemm、test/her2k、blas/hemm、blas/her2k 均无），全新建。
- **csymm、csyrk**：家族在（仅 ssymm/ssyrk 即 float 版），补 c（complex64）变体。
- test/hemm/chemm/arch* 一类路径是交付目标，不是现状；任务包脚本按交付后布局预先写好。

## 验收完整性张力（设计判断点，未决）

- 精度 PASS/FAIL 寄生在被验收方代码里（golden 与阈值都是开发者交付物）→ 验收不能只跑脚本收结果，还需六件套符合性检查（golden 真调 OpenBLAS、阈值合规、CSV 未被篡改——gen_csv 固定种子可字节级复核）。
- 性能门独立性天然成立（耗时由我们脚本提取并裁决）。

## 材料位置（交接要点：spec/ 与 repos/ 均 gitignored，不随分支入库）

- `spec/matmul-series-task-doc/`：任务包实例（任务书 + 5 算子 test_script），职责 1+2 的参照原型。仅存在于本工作树本地。
- `spec/ops-blas-ref/`：sasum 测试件切片，PROVENANCE.txt 记来源（ops-blas @ 621aafd 的 test/asum/）。只读参照。
- 主检出 `repos/ops-blas/`：权威 clone（master）。
- 本工作树：feature/ops-blas-native-acceptance，本文提交时树干净。

## 目标与下一步

1. **下一步**：先在真机跑 ops-blas 仓已有算子的测试（锚点候选 sasum 与 cherk），取得已有流水线端到端的事实基线，再作判断。
2. 之后再决定 skill 如何承接三职责（与 todo 既有"拆成用例生成 / 测试验收两个 skill"的计划合流）——不预建架构。
