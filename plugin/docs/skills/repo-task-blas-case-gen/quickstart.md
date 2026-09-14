# repo-task-blas-case-gen 五分钟上手

输入是一份 ops-blas/`aclblas` 任务书，以及可读取的公开头文件或同族 README。输出是开发者
可独立执行的六件任务包；生成侧只需 Python 3.8 以上标准库，不需要 NPU。

运行规则在 `skill/repo-task-blas-case-gen/SKILL.md`，专属红线在同目录 `CLAUDE.md`。

## 准备 FACTS

先建绝对工作目录并复制模板，然后只编辑顶部 FACTS 区：

```bash
mkdir -p <工作目录> && cd <工作目录> && \
cp <skill>/assets/template/gen_csv.py ./gen_csv.py
```

填 enum、状态码与 nullable 前，读 `references/aclblas-conventions.md`；字段和角色查
`references/facts-schema.md`。任务书没有 C 原型时，只能按同族或同前缀公开声明推断。

## 三条命令

第一条检查事实表并打印预期表头：

```bash
cd <工作目录> && python3 <skill>/scripts/package.py check \
  --facts gen_csv.py --print-header
```

第二条渲染固定六件：

```bash
cd <工作目录> && python3 <skill>/scripts/package.py render --facts gen_csv.py
```

第三条做包级校验和逐字节重生成比对：

```bash
cd <工作目录> && python3 <skill>/scripts/package.py check --facts gen_csv.py
```

三条命令都退出 0 才算生成完成。静态门通过不等于 NPU 验收通过。

## 产物

六件都在 `<工作目录>`：

```text
gen_csv.py
<op>_test.csv
verify_accuracy.py
verify_performance.py
README.md
gpu_baseline.csv
```

开发者拿到目录后可直接运行 `python3 gen_csv.py` 重生 CSV，不依赖本 skill。

## 失败去哪看

| 现象 | 先看哪里 |
| --- | --- |
| FACTS 字段、引用或表达式报错 | stderr 的字段路径与 `facts-schema.md` 常见报错 |
| enum、状态码或 fill 不确定 | `aclblas-conventions.md` |
| pairwise、四块或 footprint 异常 | `csv-and-blocks.md` 与 check 的 report |
| 通用代码区哈希不一致 | 从模板重复制，重新填 FACTS，不手改通用区 |
| 角色无法表达参数 | 停止并报告能力边界，不硬塞进相近角色 |
