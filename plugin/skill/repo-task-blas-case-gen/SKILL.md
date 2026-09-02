---
name: repo-task-blas-case-gen
description: >-
  根据 ops-blas / ops-sparse（aclblas / aclsparse）社区算子任务书，生成由 CSV 驱动的
  GTest 用例和配套脚本。需要产出题库 CSV、自测脚本或设计 BLAS/稀疏接口用例时使用
  本 skill；需要在 NPU 上验收已有用例和脚本时，改用 repo-task-blas-accept。
---

# BLAS 测试用例与测试脚本生成

输入一份算子任务书后，本 skill 生成 CSV 测试用例和可直接运行的配套脚本。本 skill 只
负责生成文件，不在 NPU 上执行测试，也不据此宣告验收通过。

流程分两步。第一步，把任务书里的**接口事实**——函数签名、每个参数的角色与 dtype、golden
参考、校验方式——填进 `gen_csv.py` 顶部的 Python 字典 `FACTS`（本文称它「事实表」）。第二步，
生成器根据 `FACTS` 生成下面六个文件；这些文件都不得手工编写：

- `gen_csv.py`——`FACTS` 与生成器本身
- `<op>_test.csv`——测试用例
- `verify_accuracy.py`、`verify_performance.py`——精度与性能验收脚本
- `README.md`——交给开发者的契约
- `gpu_baseline.csv`——性能基线

`<op>` 是算子短名，`<skill>` 是本 skill 目录的绝对路径。

## 入口参数

| 参数         | 含义       | 取值约束                    | 确定方式            |
| ---------- | -------- | ----------------------- | --------------- |
| `任务书`      | 社区算子任务书  | 含接口签名与参数表所在的节           | 用户给出            |
| `op`       | 算子短名     | 小写标识符，如 `cherk`         | 从接口名推断          |
| `family`   | 工程族目录名   | 同时用于 test 与 blas 目录     | 见下方推断规则         |
| `工作目录`     | 绝对路径     | 工作区的 `test_script/<op>` | 由 S0 创建，各阶段在此执行 |
| `<python>` | 运行脚本的解释器 | Python 3.8+             | 优先用 `python3`   |

每条命令都必须带 `cd <工作目录> &&`。shell 调用之间不继承当前目录。

## 前置检查

`family` 优先取任务书写明的工程族名；任务书没写时，对符合 typed-BLAS 命名（`s/d/c/z`
类型前缀）的算子去前缀，不符合该模式或有歧义时停止。case-gen 的输入只有任务书，
接口事实全部取自任务书。生成侧只需要 Python 3.8 或更高，
不需要 NPU、CANN 或 ATK。S0 先运行：

```bash
mkdir -p <工作目录> && cd <工作目录> && <python> --version
```

仅当退出码为 0、且版本输出以 `Python 3.` 开头时，才进入 S1。否则停止，报告
`阻塞·未生成 @S0`，并附解释器路径与完整输出。

## 主流程

各阶段按顺序执行：

| 阶段         | 输入与处理                    | 产物           | 出口与去向      |
| ---------- | ------------------------ | ------------ | ---------- |
| S1 填 FACTS | 复制模板，按任务书接口签名与参数表填 FACTS | `gen_csv.py` | 退出码 0 进 S2 |
| S2 渲染文件    | 校验 FACTS，独立运行生成器并生成各契约文件 | 六个文件         | 退出码 0 进 S3 |
| S3 校验      | 对 FACTS 与全部文件运行包级 check  | 覆盖与文件摘要      | 退出码 0 完成   |

```text
进度：
- [ ] S1 填 FACTS（package.py check --facts gen_csv.py --print-header 退出码 0）
- [ ] S2 渲染文件（package.py render）
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

### S2 渲染文件

```bash
cd <工作目录> && <python> <skill>/scripts/package.py render --facts gen_csv.py
```

render 先重做事实表和通用代码区校验，再在工作目录独立运行
`python3 gen_csv.py`。成功时打印 `<op>_test.csv: N rows` 与各文件路径，退出码为 0。
不得手写这些文件绕过渲染器。渲染出的 CSV 里，输入 buffer 的填充列 `*_fill` 用小写参数名
（`A` 对应 `a_fill`），列名规则见 [csv-and-blocks.md](references/csv-and-blocks.md)。

### S3 校验

`render` 执行成功后，运行：

```bash
cd <工作目录> && <python> <skill>/scripts/package.py check --facts gen_csv.py
```

check 逐行校验 CSV 与其余模板投影，并与重新生成的结果逐字节比较。成功时打印
`pairs 覆盖 X/Y，infeasible Z 对` 和文件摘要。若退出码为 2，返回 S1 修改 FACTS，
并从 S1 的校验重新开始；若退出码为 3，修正输入路径或语法。该步骤只完成静态校验，
不得表述为已通过 NPU 验收。

## 停止条件

出现以下任一情形就停止，并报告阶段、缺失事实与解除阻塞所需材料：

- 任务书不足以唯一确定完成本任务所需的任一 FACTS——签名（symbol/returns/参数顺序/ctype）、
  family、golden、verify，以及任务书要求的 cases、constraints、perf——无法填。
- 事实表 FACTS 出现 role 和属性词表无法表达的“未分类参数”。报告能力边界，不把它
  硬塞进相近 role。

## 参考资料

- [facts-schema.md](references/facts-schema.md) — 事实表 FACTS 字段、引用、表达式与投影规则
- [aclblas-conventions.md](references/aclblas-conventions.md) — enum、状态码与参数约定
- [case-strategy.md](references/case-strategy.md) — 覆盖轴、2^n±1 边界、规模与必构造场景
- [csv-and-blocks.md](references/csv-and-blocks.md) — 轴、行物化、四块、pairwise 与包级校验
- [readme-contract.md](references/readme-contract.md) — README 章节来源与开发者落地约束
- [perf-protocol.md](references/perf-protocol.md) — msprof kernel 采集、比对与证据协议
- `assets/example/cherk/` — 矩阵乘任务书提取的完整示例（六个文件）
- `assets/example/sasum/` — 从 sasum 任务书提取的完整示例（六个文件）

