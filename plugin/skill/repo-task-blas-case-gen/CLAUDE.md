# repo-task-blas-case-gen 开发约束

## 专属红线

- 不代写六件套中的 C++。`param.h`、`test.cpp` 与 `npu_wrapper.h` 由开发者实现。
- 不手写 CSV 绕过渲染器；CSV 必须由任务包内 `gen_csv.py` 可重复生成。
- 模板和脚本不得出现具体算子名；具体事实只存在于 FACTS 与 `assets/example/`。

## 真机验证过的事实

| 事实 | 出处 |
| --- | --- |
| 待第一次真机验收回填 | 本轮只有静态 check、render 与零上下文 eval，不冒充真机证据 |
