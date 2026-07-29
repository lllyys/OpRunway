---
title: Contract IR v1 supports generated ACLNN bindings
updated: 2026-07-26
status: verified
---

# Contract IR v1 supports generated ACLNN bindings

`plugin/acc-common/contract_ir/` 已包含版本化 JSON Schema、README、四个结构见证 IR，以及通用 prober /
codegen。Schema 把输入输出、属性、shape materialization、约束、dtype selector、acceptance predicate 与
反向映射表达为数据，为“探测器 → 规范化 IR → 唯一模板 → 机械生成 binding”的通用路径提供 Layer 0 契约。

本页只确认制品存在及其声明的契约结构，不声称所有接口形态均已真机验证。

**Verified.** 2026-07-26 核：`plugin/acc-common/contract_ir/contract_ir.schema.v1.json` 与
`plugin/acc-common/contract_ir/README.md` 存在；examples 下含 foreach_add_list、inplace_sigmoid、
bincount、argmax 四份 IR。

**Sources.** [[session 9f5c778e-cdf9-4c84-bb4f-f0ab8c49a99d · 2026-07-24]]
