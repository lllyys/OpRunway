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
| `--application` 带 `--ai-core=on --task-time=on` 时自动导出 op_summary 恰一份，无需第二步 export | 实测（A3，CANN 9.0.1，sger/ctpmv） |
| msprof 不透传 application 退出码：exit 7 与 SIGSEGV 后仍退 0，仅 stderr WARNING | 实测（A3，CANN 9.0.1） |
| op_summary 列 `Task Type`(8)、`Task Duration(us)`(10)；向量算子出 `AI_VECTOR_CORE` | 实测（sger） |
| 单次采样跨进程可复现：sger 同例两轮偏差 ≤2%（306/571/99µs）；单次不偏冷：ctpmv 三尺寸各 5 次独立单采样中位偏差 ≤1.7%，首例无台阶 | 实测（A3，Release 口径） |
| 精度轮阈值、Cube 类 kernel 类型待回填；A5 完整验收链已真机通（sger 小包 A1→A5） | 实测（A3，2026-09-14） |
