# OpRunway 当前 TODO

本页只保留尚未完成且有当前证据的工作。仓规以根 `AGENTS.md` 为准，已完成历史查 changes brief 与 Git。

## P0 · 已证明的 DUT 修复后复验

- [ ] RemainderTensorTensor 补齐 `ascend910_93` 的 kernel source、op_def config 与安装交付后，用当前
  ATK workflow 重跑 A3。完成条件：fresh build 生成非空 A3 ops-info/binary/kernel delivery，并形成完整
  accuracy/performance 证据；不能只增加 host ACLNN 符号。
- [ ] GaussianBlur 让任务书规定的 FP32 自动核尺寸 K13 进入 ACLNN/tiling 支持集合，并定位 A5 性能 case 13
  的设备超时后重跑。完成条件：14/14 accuracy 完整执行，K13 case 通过任务书 `mixed_tolerance_bm`，性能
  case 形成有限正 kernel time 与成对 profiler CSV。GPU/A100 与资源条款仍保持结构化未验证。

## 维护规则

- 只新增有真实失败证据与完成判据的条目；不为假想未来预建架构。
- 完成项从本页删除，在 `dev-doc/oprunway-changes-brief.md` 顶部记录结果。
- bureau/canon 记录不进入普通实施 TODO；只有用户明确发起相应记录任务时才处理。
