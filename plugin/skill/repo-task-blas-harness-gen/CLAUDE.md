# repo-task-blas-harness-gen 开发规则

只记本 skill 独有的取舍与真机事实；共用约束见仓根。

## 与仓根规则的两处已裁定张力

- 本 skill 建 `tests/`：外层工作区仓规（AGENTS §2）规定判据确定性由 skill 自带
  scripts 与 tests 承担，与仓根「不建 tests/」冲突时以外层为准。测试全部本机可跑
  （stdlib unittest、零 CANN 依赖），真机验证仍是行为改动的最终标准。
- README.md 首表未加行：README 属上游镜像逐字区，行随上游 PR 一并补；
  在此之前本 skill 仅登记于 plugin.json（已核实可加载）。

## 真机验证过的事实（2026-09-10 sasum spike，ascend910_93/arch22）

| 事实 | 出处 |
| --- | --- |
| overlay 布局：根级共享 param/golden，arch 目录放 test.cpp/wrapper/CSV | 实测构建+A1–A5（spike 报告）|
| CSV 由测试源编译期路径定位，须同名同目录 | test/frame/csv_loader.h:115-123 |
| 一行式 ops_blas_add_gtest_tests 注册整目录替换后的 gtest 目标 | 实测，build_sasum.log |
| frame float 顺序累加 golden 在 n≥2^24 停摆，参考值可低 8 倍 | 实测 TC_PW_070/073/075，DIAG 数值 |
| require-throw 读列在注册期抛错，277 行好数据零触发 | 实测 gtest_list=278 |
| 通用代码区 SHA（模板与 sasum/cherk 示例逐字节同值） | 实算 8c90c6a8… |
