# contract_ir —— U7 通用算子接口契约 IR

**这是什么**：一份 **op 无关**的接口描述契约。探测器（只读 `header × op_def × example × glue × doc × infershape`）
对**任意**域内算子产出一份符合 `contract_ir.schema.v1.json` 的 IR 实例；唯一 codegen 模板机械消费它，
emit 出每个算子的**类型化薄 binding**。**契约里没有任何按算子名的分支**——加新算子 = 探测出一份 IR（纯数据），
引擎/模板一行不改。设计与来龙去脉见 `doc/oprunway-u7-generalization-design.md` §3.5 / §3.6。

## 文件

- `contract_ir.schema.v1.json` —— 版本化 JSON Schema（draft 2020-12）。9 个正交 IR 元素 + G1–G5 缺口补齐。
- `examples/*.ir.json` —— 跨轴见证算子的 IR 实例（= 探测器应产出的样子 + Schema 的 round-trip 正例）。
- `codegen.py` —— **唯一通用 codegen**：吃一份 IR JSON → 机械 emit 类型化 `binding.cpp`。**零 op 名分支**（元测试钉住）；域外/fail_closed/ data-dependent-出-尺寸-算不出 → **拒绝生成、退非零**，绝不硬凑。用法：`python codegen.py <ir.json> [-o binding.cpp]`。
- `test_codegen.py` —— codegen 回归测试（foreach emit 正确 + 三条 fail-closed + 无 op 名分支）。

## 9 个正交元素（每个都取代了旧的「按类专名」）

| 元素 | 作用 | 取代 |
|---|---|---|
| `parameter_descriptor[]` | 递归、单一真相源；kind 含 tensor_list、direction 含 inout | role 词表、input/output_count |
| `constraint_graph` | 参数关系图；`index_zip`/`intra_list_homogeneous` 边（G2） | `list_parallel_to` |
| `value_domain` + `dtype_selector` | per-输出 dtype；selector 三键源含 `platform_predicate`（G3） | `dtype_paired_to`、`role:index` |
| `shape_materialization` | extent 三来源、mode 可复合（G1，解 data-dependent） | `shape_transform_formula` 等按形状类词表 |
| `output_mapping` | 声明序→槽序二部图 + 输入侧反向（G5） | `op_def_output_order_warning` |
| `acceptance_predicate` | 等价关系判据 + 跨输出引用 + 阈值 tier（G4） | `tie_break` 枚举 |
| `storage_alias` | inout/别名；`readback_binding` 锚 D2H 源、`const` 不可信 | IN/OUT 二分 |
| `abi_signature` | 两段式都探测、不假定恒 4 参 | `stage2_fixed_template` |
| `provenance` | 每字段来源 + 冲突裁决状态机、按字段分源 | 「复合标签」的不可实施性 |

## 铁律（探测器/模板都必须守）

1. **顺序/方向/映射不可外推**：aclIntArray 顺序、输出反转映射、inout 方向，一律从 example/glue 抠——
   `const` 不可信（foreach `x1` 是 `const aclTensorList*` 却被写）；唯一回读 ground truth = example 的 D2H 源 buffer。
2. **缺源/冲突/域外一律 fail-closed**：`provenance.state ∈ {needs_source, conflict, out_of_domain}` → 模板停手、不生成、交人裁，**绝不静默猜**（尤其 data-dependent 输出的 out 尺寸算不出时，绝不猜个 size 分配了就比对）。
3. **目标机双源核**：目标 a3/a5 两台，逐算子由「任务书 适配硬件 × op_def AddConfig」定；`contested`（如 op_def 仅 ascend950 vs 任务书 A2/A3）→ 停下人核。

## 适用域

只做**无状态 + 标准 aclnn 两段式 + 无 opaque descriptor 生命周期**的算子。稀疏库 API（SPMV 三阶段 handle）、
有状态容器（dynamicMap）、无张量/时序（Sleep）**命中即 fail-closed、标「不支持的接口能力」**，绝不硬塞、绝不自动归某类 adapter。

## 状态（2026-07-24）

- **Schema v1 + prober v1 + codegen v1 全建成、13 测全绿**（含元测试证 prober/codegen 源码零 op 名分支）。
- **F3 全链本地验证过**：`prober 探目录 → IR 骨架 → codegen`，且 codegen **对四根硬结构轴全对**：
  | 轴 | 见证 IR | codegen |
  |---|---|---|
  | tensor_list | `examples/foreach_add_list.ir.json` | ✓ emit（aclCreateTensorList 打包三列表） |
  | inout/alias | `examples/inplace_sigmoid.ir.json` | ✓ emit（单 selfRef 槽、自身 buffer 回读、const 不可信） |
  | 多输出反转 | `examples/argmax.ir.json` | ✓ emit（`aclnnMaxDim(...)` + output_mapping `{src0→slot1,src1→slot0}` 反转带出） |
  | data-dependent | `examples/bincount.ir.json` | ⊘ **正确 fail-closed**（out 尺寸算不出，exit 4） |
- 4 见证 IR 的语义字段从**真 example/glue/infershape** grounding（provenance 带 file:line）。
- **尚未做**：prober **v2**（自动抠 direction/output_mapping/value_domain/shape，measured against 这 4 份 oracle）；**真机 build+run**（`covered ≠ 真机绿`，task#5）。⚠ codegen emit 的 C++ 真机编译前未验证；反转映射带注释形式、aclScalar/aclIntArray 经 helper stub。
- ⚠ **`examples/*.ir.json` 是「结构测试 fixture」、不是「验收目标」**：argmax(=MaxDim)/bincount 用来测通用机制的多输出反转/data-dependent 轴（用真 aclnn 接口），但作**验收目标**它们经对应核已**作废挂起**（选错 PR/未落地，见 doc §6.A + task#7）。foreach/inplace_sigmoid 同理只作结构 fixture。**fixture 证的是「工具对该结构轴能不能 emit」，不证「该算子该被验收」。**
- ⚠ 对应核（2026-07-24）坐实 **5 个算子选错 PR/未落地 → 作废挂起**（im2col/logspace/MinDim/MaxDim/bincount），承任务书权威 + 硬约束#1。
