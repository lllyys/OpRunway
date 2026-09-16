# 包生成器 ABI —— 允许表、装载纪律与同源断言

**包生成器 ABI** 是任务包内 `gen_csv.py` 暴露给本 skill 的接口面。本文是 H1 装包
三道门的机械规则权威；实现在 `scripts/package_loader.py`，两者不一致以先修文档为病。

## 接口面

- `FACTS`：模块顶层唯一的字典字面量赋值，位于 FACTS 区标记行之间。
- `_column_specs(facts)`：每列一个 dict，至少含 `name`/`kind`/`source`
  （fixed_vector 元素列另带 `index`），顺序即 CSV 列序。
- `_header_columns(facts)`：列名投影，恒等于 `_column_specs` 的 name 序列。
- `GENERATOR_VERSION`：通用代码区常量，与 `FACTS["generator_version"]` 必须一致。
- CSV 命名：`{op}_test.csv`。编码与格式（UTF-8/LF/QUOTE_MINIMAL）是生成器输出
  惯例，H1 只机械验证表头同源与 UTF-8 可解码，不验证整文件格式风格。

## 区域标记（逐字，整行严格相等）

```text
# ===== FACTS 区开始 =====
# ===== FACTS 区结束 =====
# ===== 通用代码区（由 repo-task-blas-case-gen 渲染，禁止修改）=====
```

三行各恰出现一次且按上述顺序；缺失、重复或乱序报 `PACKAGE_MALFORMED`。

## 通用代码区摘要算法

对 `gen_csv.py` 的原始字节，取通用区标记行的行首字节偏移起至文件末尾的全部字节，
计算 SHA-256。CRLF 文件天然摘要不符，被版本门拒绝，属预期 fail-closed。

## ABI 允许表

允许表是（schema_version, generator_version, 通用区 SHA-256）三元组集合，
随本 skill 分发于 `scripts/package_loader.py` 的 `KNOWN_PACKAGE_ABIS`：

| schema | generator | 通用区 SHA-256 | 登记依据 |
| --- | --- | --- | --- |
| 1 | 1 | `8c90c6a8…de6088a1`（全值见 KNOWN_PACKAGE_ABIS） | 2026-09-10 模板实算，sasum/cherk 同值 |

三元组不在表内报 `UNSUPPORTED_PACKAGE_VERSION`。加行规则：新条目必须给出实算过程与
来源包，先按新通用区重推列投影期望表核对（测试债见 dev-doc todo），再过一轮 Codex
评审（核心契约，无条件触发）。

## 三道门与停机码归属

1. **ABI 版本门**：三元组查表；exec 只发生在过表之后——不执行不被认识的代码。
   过表后补充断言接口函数在场、`GENERATOR_VERSION` 与 FACTS 交叉一致（抓允许表
   登记错误）。未知或缺失报 `UNSUPPORTED_PACKAGE_VERSION`。
2. **装载纪律门**：单次读字节；AST 顶层形态校验（标记前只许 docstring 与唯一
   FACTS 字面量赋值）；`ast.literal_eval` 提取 FACTS。任何违例报 `PACKAGE_MALFORMED`。
3. **同源断言**：`_column_specs` 的 name 序列、`_header_columns`、CSV 实际表头
   三方逐列相等（含列序）；任何一方重复列名拒绝。违例报 `HEADER_MISMATCH`——
   语义是「表头契约不成立：重复或三方不一致」，不狭义指两份内容不等。

三码的报告分型：`UNSUPPORTED_PACKAGE_VERSION` 是能力边界（包可能是合法的新版
产物），`PACKAGE_MALFORMED` 与 `HEADER_MISMATCH` 是包缺陷。支持矩阵（SKILL.md『MVP 支持矩阵』节）的机械可判
子集（golden.kind、复数标量、nullable）在装载末尾预筛，命中报 `UNSUPPORTED_CONTRACT`；
参数角色组合的封闭表判定在 H2 契约编译（Step 2 冻结后实现）。
