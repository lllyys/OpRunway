# 方案模板附录（P2 对号入座用，非穷举）

> **何时读本文件**：P2 阶段拿到外部评论、要判定 `kind` 时。
> **遇到新形态按「形态本质」归到最接近的 kind，不要因为没见过就放弃可执行的方案。**

## 六种 kind 一览

| kind | 形态 | 例 |
| --- | --- | --- |
| `env` | 设环境变量 | `ASCEND_GLOBAL_LOG_LEVEL=1` |
| `build_flag` | cmake / 构建参数 | `-DENABLE_HIF8=ON` |
| `cmd_arg` | build.sh 多加参数 | `build.sh --pkg --extra-flag` |
| `clean` | 跑测前的清理命令 | `pkill bisheng; rm -rf kernel_meta_*` |
| `patch` | 提供代码 diff | 评论里的 ```diff 块 |
| `upgrade` | 切版本 / 拉新代码 | 「升级到 v2.1 / git pull」 |

## 候选清单怎么展示

kind 用中文说：设环境变量 / 调构建参数 / 调命令行参数 / 清理遗留文件 / 修改源码 / 升级版本。

```
【建议顺序】无副作用方案优先：设环境变量 / 构建参数 → 清理遗留文件 → 源码修改。
先试低成本方案，失败再升级，避免工作区被意外改动。

issue #N（ops-nn / quant_batch_matmul_v3）共 M 条评论，识别出 K 条候选方案：
  1. [设环境变量]   「ASCEND_GLOBAL_LOG_LEVEL=1」  来自 @xxx  可信度：中
  2. [清理遗留文件] 「pkill -f bisheng; rm -rf kernel_meta_*」  来自 @xxx  可信度：中
  3. [等待 PR 合并] 「会删除冗余依赖 → PR #5065」 来自 @yyy  （无法自动执行，仅供参考）
```

只有一个 actionable 方案 → 省略优先级提示，直接问「是否应用此方案？」。
`pr_pending` / `discuss` 不标序号、放列表末尾标「仅供参考」、不可选。

用 `AskUserQuestion` 让用户选，选定后落盘 `cann-ops-report/issues/plans/<issue_id>.json`：

```json
{"kind": "clean",
 "suggested_fix": "pkill -f ${ASCEND_HOME_PATH}/bin/bisheng;rm -rf kernel_meta_*",
 "confidence": "med",
 "source": {"author": "xxx", "created_at": "2026-05-19T14:10:19+08:00"}}
```

## 逐类判例

## env

```
请设置 export ASCEND_GLOBAL_LOG_LEVEL=1 再重试
```
→ `{"kind": "env", "suggested_fix": "ASCEND_GLOBAL_LOG_LEVEL=1"}`

## build_flag

```
cmake 加 -DENABLE_HIF8=ON
```
→ `{"kind": "build_flag", "suggested_fix": "-DENABLE_HIF8=ON"}`

## cmd_arg

```
试试 build.sh --pkg --soc=ascend950 --ops=foo --no_aicpu
```
→ `{"kind": "cmd_arg", "suggested_fix": "--pkg --soc=ascend950 --ops=foo --no_aicpu"}`

## clean

```
这个是上次编译遗留文件导致，请执行：
pkill -f ${ASCEND_HOME_PATH}/bin/bisheng;
rm -rf scripts/kernel/binary_script/kernel_meta_*
后重新构建
```
→ `{"kind": "clean", "suggested_fix": "pkill -f ${ASCEND_HOME_PATH}/bin/bisheng;rm -rf scripts/kernel/binary_script/kernel_meta_*"}`

## patch

````
试试这个 diff：
```diff
--- a/op_kernel/foo.cpp
+++ b/op_kernel/foo.cpp
@@ -42 +42 @@
-old line
+new line
```
````
→ `{"kind": "patch", "suggested_fix": "--- a/op_kernel/foo.cpp\n..."}`

## upgrade

```
请升级到 v9.0.1，这个问题在 commit abc123 已修复
```
→ `{"kind": "upgrade", "suggested_fix": "升级到 v9.0.1，commit abc123"}`

## pr_pending（不可自动执行，仅展示）

```
会删除 qbmmv3 的冗余依赖 → https://gitcode.com/cann/ops-nn/pull/5065
```
→ 归 `pr_pending`，展示 PR 链接，不进 P3。

## discuss（不可自动执行）

```
能否提供完整的构建日志和环境变量？
```
→ 归 `discuss`，展示给用户，不进 P3。

## 归类兜底

| 新形态本质 | 归到 |
|---|---|
| shell 命令序列 | `clean` |
| 给 build.sh 加参数 | `cmd_arg` |
| 改源码 | `patch` |
