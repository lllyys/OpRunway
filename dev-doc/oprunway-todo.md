# OpRunway 当前 TODO

本文件只记录**尚未完成、当前仍有价值**的工作。规则以仓根 `AGENTS.md` 为唯一真源；已完成事项、
历史实测和迁移过程查 `dev-doc/oprunway-changes-brief.md` 与 Git 历史。

## P2 · 当前回归阻塞

- [ ] **迁移剩余 legacy receipt 测试夹具**：当前无排除全量测试共 2642 项，其中 21 failure、3 error、
  18 skipped。24 个失败/错误集中在旧测试仍构造 extension receipt v1 / vendor receipt v2，被 fresh
  anti-downgrade 门提前拒绝。完成判据：只升级测试夹具与断言到 current outer receipt + vendor receipt
  v3，不放宽产品门；A3 无排除全量测试零 failure/error，skip 逐项有明确原因。

## P3 · 通用验收能力缺口

| 能力 | 当前边界 | 完成判据 |
|---|---|---|
| `aclTensorList` 正式链 | 尚未贯通 casegen → cpp_extension → driver → evidence/gate | 用字段驱动的通用 fixture 跑通生成、真机执行与三级门；不得按算子名分支 |
| 多输入 + 多输出组合契约 | `multi_input_contract` 当前主要覆盖单输出，多输出能力尚未与其组合验证 | 同一 spec 同时包含多输入、多输出，slot/manifest/receipt/evidence 全链对账，并有缺失/漂移 mutation |
| Contract IR 硬化 | scalar/array ABI、复杂 C/C++ 签名解析、error-path RAII、关系约束与语义 probe 仍可加强 | 每项先有可复现失败；实现后由 schema、codegen 和 mutation 测试共同闭合 |
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

- [ ] 用户明确要求 GPU 对比时，才采集并接入 Task3 GPU baseline；默认只做 NPU msprof。
- [ ] Catlass 若重新进入验收范围，先确定正式 runner/receipt 路线并取得对应真机环境。
- [ ] 新仓形态出现真实任务时，再按通用能力扩 adapter；不以“未来可能需要”为由预建全部仓适配。
- [ ] MERE/MARE、ATK 等额外度量只有任务书明确要求且现有标准不足时才立项。

## P6 · Canon / Bureau 人审债

- [ ] 复核并 settle 与 caller-trusted 输入模型冲突的旧 correspondence / PR-head claim；在 review 前，
  现行执行规则以 `AGENTS.md` 为准。
- [ ] 处理仍为 proposed/contested 的 golden、审修触发点和发布形态页面；不得由 agent 自行升 canonical。
- [ ] 清理 canon 校验中指向已删除历史样例或文档的路径。

## 维护规则

- 用户后续明确要求新增或加入本页的 TODO，默认列为当时的最高优先级；只有用户明确指定时才降级。
- 只新增有明确失败证据、完成判据或用户需求的条目。
- 完成后从本页删除，并在 `dev-doc/oprunway-changes-brief.md` 记录结果。
- 不在本页保存历史测试数字、commit/PR 流水、过期外部状态或已关闭决策。
- 不新建并列 TODO；专项实施细节只有在真正开工时才进入对应计划。
