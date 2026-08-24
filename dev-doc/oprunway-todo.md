# OpRunway 当前 TODO

本页只保留尚未完成且有当前证据的工作。仓规以根 `AGENTS.md` 为准，已完成历史查 changes brief 与 Git。

## P0 · 已证明的 DUT 修复后复验

- [ ] RemainderTensorTensor：补齐 `ascend910_93` 的 kernel source、op_def config 与安装交付后，
  用镜像 skill 重跑 A3。完成条件：fresh build 产出非空 A3 ops-info/binary/kernel delivery，并形成
  完整 accuracy/performance 证据；不能只增加 host ACLNN 符号。此前一轮已产生
  `DUT_FAIL / TARGET_DELIVERY_MISSING` 终态（本地 reports/ 已清空，结论以 changes brief 与 Git 历史为准）。

## P1 · 迁移收尾

- [ ] `repo-task-doc-write` 的 `test_document_style.py` 与 `test_shared_facts_sync.py` 仍写旧验收路径；
  保持该目录零接触，待任务书 skill 自身维护时迁到两个平级目录。
- [ ] `.claude/skills/isolated-acceptance/` 的无头流程按两个平级 skill 重排，并验证无 NPU
  生成、真机接收及 S3–S5；本轮只收口 plugin 镜像，不改私有运行通路。
- [ ] 演练一次上游同步（fetch → `git diff --binary | git apply --3way --directory=plugin` → 更新
  `plugin/.claude-plugin/upstream.json` 基线）。

## 维护规则

- 只新增有真实失败证据与完成判据的条目；不为假想未来预建架构。
- 完成项从本页删除，在 `dev-doc/oprunway-changes-brief.md` 顶部记录结果。
