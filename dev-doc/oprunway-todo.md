# OpRunway 当前 TODO

本文件只记录**尚未完成、当前仍有价值**的工作。规则以仓根 `AGENTS.md` 为唯一真源；已完成事项、
历史实测和迁移过程查 `dev-doc/oprunway-changes-brief.md` 与 Git 历史。

## P0 · 当前最高优先级

- [ ] **prepare 异常统一提交失败 attempt**：Remainder v14 实测中，prepare/adapter 在 Task2 前抛出
  typed 异常时进程直接退出，未生成 committed `attempt_record.json` 和中文失败明细。完成判据：CP-B 后的
  prepare/codegen/adapter/driver 前置异常统一进入现有 pre-execution finalizer，清理同根旧正式产物并绑定
  source/spec/build/stage/error；不得生成 PASS、不得伪造用例执行，也不得为补报告而重跑 DUT。

- [ ] **修复任务书要求与能力声明缺项的验收状态映射**：任务书明确要求的 dtype、shape、rank、attr
  或接口能力若未在 `op_def`/正式能力声明中出现，应判为交付契约 `FAIL`，不能降为 `UNVALIDATED`。
  Roll 是当前见证：任务书要求的 `int16`、`int64` 同时缺于 `op_def`、ACLNN 实现的
  `DTYPE_SUPPORT_LIST` 和接口文档，应判“任务书要求未实现”，不是证据不足。
  `UNVALIDATED` 只用于缺外部环境或证据、当前确实无法判断的事项。完成判据：spec/caseset/validator/
  acceptance/report 对该分类逐字一致；补“任务书要求但 op_def 漏声明 → FAIL”“外部证据不可得 →
  UNVALIDATED”及两者不可互换的行为与 mutation 测试，并让报告分别展示契约失败与执行失败。
- [ ] **修复 measure-only 不生成性能失败明细**：`perf_report.json` 已在 `per_case[]` 记录
  `blocked=true`、`npu_us=null` 与失败原因，但 `render_acceptance_markdown.py` 只读取
  `non_passing_cases`，导致主报告显示 blocked 并引用 `性能失败明细.md`，实际文件却不存在。完成判据：
  renderer 从确定性性能产物投影所有 blocked/exception 等未通过行；存在未通过性能 case 时必须生成明细，
  不存在时不得留下链接或旧文件；补 measure-only 行为测试及 stale-file 清理测试。

## P3 · 通用验收能力缺口

| 能力 | 当前边界 | 完成判据 |
|---|---|---|
| `aclTensorList` 正式链 | 尚未贯通 casegen → cpp_extension → driver → evidence/gate | 用字段驱动的通用 fixture 跑通生成、真机执行与三级门；不得按算子名分支 |
| 多输入 + 多输出组合契约 | `multi_input_contract` 当前主要覆盖单输出，多输出能力尚未与其组合验证 | 同一 spec 同时包含多输入、多输出，slot/manifest/receipt/evidence 全链对账，并有缺失/漂移 mutation |
| Contract IR 硬化 | scalar/array ABI、复杂 C/C++ 签名解析、error-path RAII、关系约束与语义 probe 仍可加强 | 每项先有可复现失败；实现后由 schema、codegen 和 mutation 测试共同闭合 |
| 多注册 target 的 kernel identity 选择 | current 只接受目标 scope 内恰好一个 `OP_ADD`；零/多候选会在 CP-A fail-closed，尚无受控的多注册选择契约 | 出现真实多注册任务后，用 source-bound selector 明示选中候选并证明其属于扫描集合；spec/closure/gate/RGE 全链拒绝缺选、错选和摘要漂移，不按算子名分支 |
| 精度边界 | NaN、±0、Inf、`equal_nan` 交集尚缺统一的端到端覆盖盘点 | 按 dtype/compare mode 建行为矩阵；判据来自 spec，caseset/evidence 同改也不能绕门 |
| Pdist 属性轴 | 通用 attr/rank/cost 能力已存在，但正式 `p=inf` 属性覆盖尚未以当前 workflow 复核 | 仅在有真实任务书+源码输入时生成正式 spec/caseset并真机验证，不沿用旧 runner 结论 |
| TBE 信息库输入源 | 任务书 dtype 独立源尚未形成稳定 adapter | 从运行环境探测，不写死 SSH/路径；缺源时 fail-closed 询问用户，不回退信任被测源码自证 |

## P4 · 扩域决策

以下接口目前保持显式域外，不预建 adapter。只有出现真实任务书需求并经用户确认扩域后，才转为实施项：

- 无张量输入；
- 稀疏张量；
- 状态容器或 opaque descriptor；
- 分布式/通信接口；
- 需要跨设备协同完成一次算子调用的接口。

## P5 · 条件性工作（不阻塞默认验收）

- [ ] Catlass 若重新进入验收范围，先确定正式 runner/receipt 路线并取得对应真机环境。
- [ ] 新仓形态出现真实任务时，再按通用能力扩 adapter；不以“未来可能需要”为由预建全部仓适配。
- [ ] MERE/MARE、ATK 等额外度量只有任务书明确要求且现有标准不足时才立项。

## P6 · Canon / Bureau 人审债

- [ ] 复核并 settle 与 caller-trusted 输入模型冲突的旧 correspondence / PR-head claim；在 review 前，
  现行执行规则以 `AGENTS.md` 为准。
- [ ] 处理仍为 proposed/contested 的 golden、审修触发点和发布形态页面；不得由 agent 自行升 canonical。
- [ ] 清理 canon 校验中指向已删除历史样例或文档的路径。

## 维护规则

- 只新增有明确失败证据、完成判据或用户需求的条目。
- 完成后从本页删除，并在 `dev-doc/oprunway-changes-brief.md` 记录结果。
- 不在本页保存历史测试数字、commit/PR 流水、过期外部状态或已关闭决策。
- 不新建并列 TODO；专项实施细节只有在真正开工时才进入对应计划。
