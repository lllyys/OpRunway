---
name: repo-task-blas-case-gen
description: >-
  按 ops-blas 社区算子任务书生成固定为六件的 CSV 驱动 GTest 任务包。当用户给出
  ops-blas 或 aclblas 算子任务书、要产出题库 CSV 或自测脚本、或要为 BLAS
  接口设计用例时使用；
  已有任务包要在 NPU 上验收时改用 repo-task-blas-accept。
---

# BLAS 任务包生成

任务书 → 事实表 FACTS → 六件 → 静态校验。
这条链只生成任务包，不声称 NPU 验收通过。

## 入口参数

| 参数         | 含义       | 取值约束                    | 初值推断          |
| ---------- | -------- | ----------------------- | ------------- |
| `任务书`      | 社区算子任务书  | 含接口签名与参数表所在的节           | 用户给出          |
| `op`       | 算子短名     | 小写标识符，如 `cherk`         | 从接口名推断        |
| `family`   | 工程族目录名   | 同时用于 test 与 blas 目录     | 见下方推断规则       |
| `工作目录`     | 绝对路径     | 工作区的 `test_script/<op>` | S0 建立后进入      |
| `<python>` | 执行量具的解释器 | Python 3                | 优先用 `python3` |

每条命令都必须带 `cd <工作目录> &&`。shell 调用之间不继承当前目录。

## 前置检查

`family` 优先取头文件同族目录名；没有头文件时，取算子名去掉 `s/d/c/z` 类型前缀后的
名称。生成侧只需要 Python 3，不需要 NPU、CANN 或 ATK。S0 先运行：

```bash
mkdir -p <工作目录> && cd <工作目录> && <python> --version
```

输出以 `Python 3.` 开头且退出码为 0 才进入 S1。否则停止，报告
`阻塞·未生成 @S0`，并附解释器路径与完整输出。

## 主流程

各阶段按顺序执行：

| 阶段         | 输入与处理                    | 产物           | 出口与去向  |
| ---------- | ------------------------ | ------------ | ------ |
| S1 填 FACTS | 复制模板，按任务书接口签名与参数表填 FACTS | `gen_csv.py` | 0 进 S2 |
| S2 渲染六件    | 校验 FACTS，独立运行生成器并投影契约    | 固定六件         | 0 进 S3 |
| S3 校验      | 对事实表 FACTS 与六件运行包级 check | 覆盖与六件摘要      | 0 完成   |

```text
进度：
- [ ] S1 填 FACTS（package.py check --facts gen_csv.py --print-header 退出码 0）
- [ ] S2 渲染六件（package.py render）
- [ ] S3 校验（package.py check 退出码 0）
```

### S1 填 FACTS

填 enum 值、状态码与 nullable 前，先读
[aclblas-conventions.md](references/aclblas-conventions.md)。再复制模板：

```bash
mkdir -p <工作目录> && cd <工作目录> && \
cp <skill>/assets/template/gen_csv.py ./gen_csv.py
```

只编辑 `gen_csv.py` 顶部的 `FACTS` 区。字段、角色和引用规则见
[facts-schema.md](references/facts-schema.md)。填完运行：

```bash
cd <工作目录> && <python> <skill>/scripts/package.py check \
  --facts gen_csv.py --print-header
```

退出码 0 才能进入 S2。退出码 2 时逐条修 stderr 指向的字段；退出码 3 时修路径、
Python 语法或 FACTS 字面量。

### S2 渲染六件

```bash
cd <工作目录> && <python> <skill>/scripts/package.py render --facts gen_csv.py
```

render 先重做事实表和通用代码区校验，再在工作目录独立运行
`python3 gen_csv.py`。成功时打印 `<op>_test.csv: N rows` 与六件路径，退出码为 0。
不得手写六件绕过渲染器。

### S3 校验

render 实现并成功后运行：

```bash
cd <工作目录> && <python> <skill>/scripts/package.py check --facts gen_csv.py
```

check 逐行校验 CSV、其余模板投影，并与重生成结果逐字节比较。成功时打印
`pairs 覆盖 X/Y，infeasible Z 对` 和六件摘要。退出码 3 时修输入路径或语法；不得把这次
静态校验描述成 NPU 验收通过。退出码 2 时回 S1 修 FACTS，改完从 S1 的
校验重来。

## 停止条件

出现以下任一情形就停止，并报告阶段、缺失事实与解除阻塞所需材料：

- 任务书没有 C 原型，且头文件里没有同族或同前缀接口可供推断，无法确认参数顺序与类型。
- 事实表 FACTS 出现 role 和属性词表无法表达的“未分类参数”。报告能力边界，不把它
  硬塞进相近 role。

## 参考资料

- [facts-schema.md](references/facts-schema.md) — 事实表 FACTS 字段、引用、表达式与投影规则
- [aclblas-conventions.md](references/aclblas-conventions.md) — enum、状态码与参数推断约定
- [case-strategy.md](references/case-strategy.md) — 覆盖轴、2^n±1 边界、规模与必构造场景
- [csv-and-blocks.md](references/csv-and-blocks.md) — 轴、行物化、四块、pairwise 与包级校验
- [readme-contract.md](references/readme-contract.md) — README 章节来源与开发者落地约束
- [perf-protocol.md](references/perf-protocol.md) — msprof kernel 采集、比对与证据协议
- `assets/example/cherk/` — 矩阵乘任务书提取的完整六件示例
- `assets/example/sasum/` — ops-blas README 提取的示例，不代表存在 sasum 任务书
- `assets/example/srotm/` — 公开头文件提取的示例

