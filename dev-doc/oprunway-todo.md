# OpRunway 当前 TODO

本页只保留尚未完成且有当前证据的工作。仓规以根 `AGENTS.md` 为准，已完成历史查 changes brief 与 Git。

## P0 · 已证明的 DUT 修复后复验

- [ ] RemainderTensorTensor 补齐 `ascend910_93` 的 kernel source、op_def config 与安装交付后，用当前
  ATK workflow 重跑 A3。完成条件：fresh build 生成非空 A3 ops-info/binary/kernel delivery，并形成完整
  accuracy/performance 证据；不能只增加 host ACLNN 符号。当前 fresh A3 formal 已正式产生
  `DUT_FAIL / TARGET_DELIVERY_MISSING`，acceptance SHA-256 为
  `ca9aadb9da5dfe2588e7427f23ffae9855ee41a293ae7c69b7127f7ffb0de63a`。

## 维护规则

- 只新增有真实失败证据与完成判据的条目；不为假想未来预建架构。
- 完成项从本页删除，在 `dev-doc/oprunway-changes-brief.md` 顶部记录结果。
- bureau/canon 记录不进入普通实施 TODO；只有用户明确发起相应记录任务时才处理。
