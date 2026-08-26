# repo-task-blas-case-gen 开发约束

## 专属红线

- 不代写六件套中的 C++。`param.h`、`test.cpp` 与 `npu_wrapper.h` 由开发者实现。
- 不手写 CSV 绕过渲染器；CSV 必须由任务包内 `gen_csv.py` 可重复生成。
- 模板和脚本不得出现具体算子名；具体事实只存在于 FACTS 与 `assets/example/`。

## 真机验证过的事实

| 事实 | 出处 |
| --- | --- |
| `build.sh --soc=ascend910_93 --ops=<op>` 编出 `build/test/<family>/<op>/<op>_test` | 实测（A3，sger） |
| `--gtest_list_tests` 名字形如 `<Suite>/<Suite>.CsvDriven/TC_*` | 实测（sger） |
| msprof 采集与导出分两步：先 `--application`，再 `--export=on` 才出 op_summary | 实测（A3，CANN 9.0.1） |
| op_summary 列 `Task Type`(8)、`Task Duration(us)`(10)；向量算子出 `AI_VECTOR_CORE` | 实测（sger） |
| verify_performance 整条流水线可复现:5 次中位数 spread 1.3–2.4% | 实测（sger 1024²=613μs 等） |
| 精度轮阈值、Cube 类 kernel 类型、A5 完整验收尚未真机回填 | 待第一次算子真机验收 |
