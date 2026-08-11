# OpRunway 当前 TODO

本页只保留尚未完成且有当前证据的工作。仓规以根 `AGENTS.md` 为准，已完成历史查 changes brief 与 Git。

## P0 · 已证明的 DUT 修复后复验

- [ ] RemainderTensorTensor 补齐 `ascend910_93` 的 kernel source、op_def config 与安装交付后，用当前
  ATK workflow 重跑 A3。完成条件：fresh build 生成非空 A3 ops-info/binary/kernel delivery，并形成完整
  accuracy/performance 证据；不能只增加 host ACLNN 符号。当前 fresh A3 formal 已正式产生
  `DUT_FAIL / TARGET_DELIVERY_MISSING`，acceptance SHA-256 为
  `63f6e2e8aed322f4762cfb1443bebbe507d728a71038b5f160bc064551bd89de`。

## P1 · 已证明的 workflow / witness 输入缺口

- [ ] GaussianBlur 保持官方 169-case denominator 不变，解决官方 `cv2.GaussianBlur` golden
  在末维超过 `CV_CN_MAX=512` 时无法产生 CPU reference 的问题后 fresh 重跑 A5。完成条件：
  `Test_001..Test_169` 及任务书 S1 全部有可比 CPU/DUT 证据；不删除 5 个大通道 case，
  不把 CPU oracle 失败归因为 DUT。当前轮为 165/170 精度通过、S1 61.4277 us，确定性
  `PLUGIN_ERROR / FAILURE_NOT_ATTRIBUTED_TO_DUT`。
- [ ] Staging 应在任意层级排除 `.git` transport metadata，并保留对真实 source/build input
  的严格变更检测。完成条件：嵌套 `.git/index` 不进入 staged tree 和 build-input anchor，
  嵌套源文件变更仍触发 `SOURCE_MUTATED`；Roll A3 在 fresh root 完成 build 与 execution。

## 维护规则

- 只新增有真实失败证据与完成判据的条目；不为假想未来预建架构。
- 完成项从本页删除，在 `dev-doc/oprunway-changes-brief.md` 顶部记录结果。
- bureau/canon 记录不进入普通实施 TODO；只有用户明确发起相应记录任务时才处理。
