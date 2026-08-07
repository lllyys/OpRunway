# Roll、Bernoulli、Remainder 三算子统一实测记录（2026-08-07）

## 1 · 结论先行

本轮以三份任务书和三个本地源码 checkout 为输入，在 Atlas A3 的三张独立卡上完成了三次正式
`cpp_extension` 单卡验收；随后又按每算子三卡分组，以相同正式 caseset 做了 precision-only 分片与合并复验。
裁决只能逐算子读取，不能合并成“三算子通过”：

| 算子 | 正式精度 | 性能取证 | 三级门 | 确定性 acceptance |
|---|---:|---:|---:|---|
| Bernoulli | 98 总例，88 执行并通过，10 个 rank0 执行失败 | 97 个性能例，87 实测、10 阻断 | Task1/Task2 `PASSED`；Task3 未完成 | `FAIL(精度)` / `FAILED_PRECISION` / exit 1 |
| RemainderTensorTensor | 18/18 通过 | 18/18 msprof 实测 | Task1/Task2/Task3 `PASSED` | `PASSED_WITH_GAPS` / exit 2 / human CP |
| Roll | 252 总例，243 执行并通过，9 个 rank0 执行失败 | 18 个性能例，9 实测、9 阻断 | Task1/Task2 `PASSED`；Task3 未完成 | `FAIL(精度)` / `FAILED_PRECISION` / exit 1 |

这里的 `gate_passed=true` 只证明证据链完整、自洽，不能把两个 `FAIL(精度)` 升级为通过；
Remainder 的 `PASSED_WITH_GAPS` 也不是无条件 `PASS`。

多卡复验的合并 verdict 与相应单卡 verdict 逐字同 SHA：Bernoulli 仍为 88/10、Remainder 仍为
18/0、Roll 仍为 243/9。它只证明精度分片执行与确定性合并等价，未采集性能，也不改写上表的正式 acceptance。

## 2 · 证据边界与机器总账

### 2.1 本轮实际测了什么

- **任务书输入形态已实测两种**：Bernoulli、Remainder 使用用户提供的本地 Markdown；Roll 从用户提供的
  [在线任务书](https://gitcode.com/cann/cann-ops-competitions/blob/master/04_tasks/01_community-task-2026/docs/202607/aclnnRoll_task_doc.md)
  取材并落快照。
- **三份正式 acceptance 的 DUT 来源都是本地来源**：三个源码均以
  `local_source → local_snapshot` 取材、构建和验收。
- **在线任务书不等于在线 PR 来源**。补充在线取材已得到 Roll 的 exact `gitcode_pr` 完整事实包，
  但 Bernoulli 缺 exact PR/fork/ref/head，Remainder 的两个候选都因目标目录下无 changed files 而
  blocked，不能任选其一。故在线 PR 来源支持是 `PARTIALLY_EXERCISED`，而不是三算子全部完成。
- Roll 的在线事实包只证明 exact PR 身份和取材闭环；上表正式 Roll acceptance 仍绑定本地 snapshot，
  不能把补充 intake 冒充为一次 PR 来源的构建、执行或验收。
- 三份正式 acceptance 都是**单卡**结果；另以原正式 caseset 做了 precision-only 多卡分片复验：
  Bernoulli 使用 device 1/4/6，Remainder 与 Roll 使用 device 4/5/6。该复验不重采 msprof，
  不产生第二份完整 acceptance。

### 2.2 R/G/E 总账

含真实机器绝对路径的完整账本只保存在 gitignored
`reports/oprunway-roll-bernoulli-remainder-rge-2026-08-07.json`，并在 A3 本批证据目录留有同字节副本；
其最终 SHA-256 为 `efeb056b5010e729c9d61aa3f60055728a781de7964ae871bcdd882bb531b5ba`，
它不进入 Git。tracked 的通用校验器是
[`validate_rge_ledger.py`](../plugin/acc-common/validate_rge_ledger.py)，变异测试是
[`test_validate_rge_ledger.py`](../plugin/acc-common/test_validate_rge_ledger.py)。

校验器机械检查：

1. `structure_denominator_total = case_target + structured_excluded`；
2. `generated.case_count = case_target`，缺失 dtype 与 `dtype_unsupported` gap 精确同集；
3. `planned = produced + failed + invocation_excluded`；
4. 精度各分区恰好覆盖总分母，执行错误不能伪装为数值通过；
5. `perf_cases = measured + blocked`，measure-only 下未比较的性能条款必须有结构化 gap；
6. 任务书硬件集合减去实测硬件集合，必须与硬件 gap 精确同集；
7. `gate_passed` 不能升级失败裁决；
8. N0–N10 必须完整、有序，每个 evidence ref 必须指向登记产物或 A3 测试；
9. 在线 PR 能力、各算子实际 intake 状态与正式 acceptance 的来源形态严格分栏；
10. 多卡 v3 inventory 对顶层固定工件、顶层 `work`、每片 formal/pre-smoke、正式 single work
    做普通文件精确递归闭包；拒绝 symlink、越界、缺失、额外文件，并现场复算 bytes/SHA；
11. 每片逐字加载 caseset、cpp snapshot、plan、receipt、device identity、shard result、manifest、
    vendor/CANN ELF 与输入/golden/out，再只用这些磁盘 result 重放 merge；
12. 直接复用当前 Task2 multi gate，重放单卡 adapter receipt、single/multi equivalence 与 Bernoulli
    stochastic precondition/formal/collection/evaluation；旧 gate log 只作 trace，不作信任根；
13. `--verify-artifacts` 在证据所在环境逐文件复算所有登记 SHA-256。

A3 定向执行结果为 30/30 tests `OK`，包含产物 SHA、正式产物重投影、R/G/E
分母、终态顺序、在线 intake、N0–N10 证据映射、仓内测试日志身份变异、整片 coherent replacement，
以及真实 stochastic formal 的正例与 coherent replacement 负例；
CLI 输出：

```text
RGE_LEDGER_VALID operators=3 artifacts_verified=yes
```

最终 CLI 日志 SHA-256 为 `5dec1961db9ad6cf76215fac68baf5f6673082b0643a68aba4d33311b86429f0`，
rc 文件为 0、SHA-256 `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa`。

最终完整隔离回归以完整 `plugin + AGENTS.md` fresh snapshot、清空全部 `OPRUNWAY_*`、不排除任何
`test_*.py` 并启用 verbose 身份日志执行：2637 tests `OK (skipped=7)`，退出码 0；日志 SHA-256 为
`5b82768b797fbc0d54fbd8957e940afa304b16adb1363153315c580782e87e97`。`compileall` 返回 0，空日志
SHA-256 为 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。另有 source 相关定向
325/325 `OK (skipped=1)`；fetch/source 取材专项日志明确写出
`Ran 119 tests` 与 `OK (skipped=1)`；这是 119 个总测试中含 1 个 skip，不是“119 通过再加 1 skip”。
它证明 GitCode PR URL 解析、head SHA 固定、网络失败分层与本地 snapshot 两条通路的通用能力；
只有 Roll 的补充 intake 实际走到了 exact `gitcode_pr` complete。

### 2.3 在线 PR 来源补充取材

| 算子 | 状态 | 机器事实 | 结论边界 |
|---|---|---|---|
| Roll | `COMPLETE` | [MR 4250](https://gitcode.com/cann/ops-math/merge_requests/4250)；fork `Twilight-Fanyi/ops-math`；ref `aclnn-roll-complex64`；exact head `ddfbc6630d43944d315aa111dccd96611e0f9b71`；目标 `experimental/math/roll`；25 文件 manifest `9f59aecfb07d09198bacfe68149f542caeaf237593f3c6d2a461712cd49e1fa3` | exact `gitcode_pr` intake 已完成；未据此重跑正式 acceptance |
| Bernoulli | `BLOCKED` | `missing_exact_online_identity` | 缺 exact PR/fork/ref/head，禁止猜测 |
| Remainder | `BLOCKED` | MR 4269、4296 均 `rc=3/no_changed_files_under_target_dir` | 候选歧义，禁止任选一个冒充 exact 来源 |

补充在线身份审计 SHA-256 为
`573f4d77b95a8814658254ab6414bcd50f0b7120771596380f3569608145fba4`。Roll complete intake 的
`source_facts.json`、`pr_facts.json`、taskdoc snapshot、取材日志、实测 rc 文件 SHA-256 依次为
`ee863a1968fc2c5d110442952155b719f67a49baa8a92d9165006f65b1907a50`、
`97d1f2b36e6ba8debd1d1d7f6a8aab3dab6418d5398abbfbeab3bfb9b18b630a`、
`1088ffd3af097d450de6e25667c34afb4fe5f8e539ab7ca4418a4d79d5bd3f06`、
`b169056dd2e0d01736210529417dc5d0bf69252f033a73c2313fe9b7a812720e`、
`9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa`。逐 manifest blob 复核记录
SHA-256 为 `7ad7f05c837d06310c5494e864cfe3315dfca79f7d37d5092db293c3c667d1c0`。
该次取材现场留存的 `fetch_source.py` SHA-256 为
`a015339676cb53ceaa7fbed7b76887a4ad8b41b5d25219c00dc988dad645ed46`，与本记录收口时 tracked producer
逐字相同；日志和 facts 均在该脚本副本之后生成。本轮执行记录确认它是禁用可能返回 base view 的
tree fallback 后重新在线取材的结果；但脚本 SHA 与文件时序本身只支持、不能单独证明进程实际 argv，
因此总账不把这两项当作独立执行身份收据。source envelope 与逐 blob 审计仍负责数据内容闭包；
source envelope 把 repository/ref/target/manifest 全部绑在同一份 facts 中。更早的 `roll-online-final2`
和收紧前 facts 都只作历史诊断，不再作总账的 Roll online 身份根。

### 2.4 多卡 precision-only 等价复验

fresh v3 为每个算子分别生成 `oprunway.multi_card_artifact_inventory/v3`。inventory 不只登记
manifest/result：它还精确闭包顶层 work、每片 formal、每片 pre-smoke、正式 single work、生成源码、
extension manifest/ELF、vendor/CANN 定义 ELF 与全部 input/golden/out，并由总账现场调用当前 Task2 gate。
三个 inventory 的稳定 ID 与 SHA-256 为：

- `multicard_v3_bernoulli`：`50badd31c7320c9e666b31816e3657c48da61a202965d4e5bdefb98f25203b22`；
- `multicard_v3_remainder`：`ab89c371f21af882499a12d547f74b9e8500b7f41eb694698cb0cf2bb4900b83`；
- `multicard_v3_roll`：`2104b24cd2e2f557d5fd23bc04a5c87bd0b81aa9ff563547afa13d4fd9280c86`。

所有 shard 使用独立工作目录、没有共享执行写目录。这批复验没有采集或重放 msprof。

| 算子 | partition / shard sizes | 各 shard produced/failed | merged verdict | 等价记录 |
|---|---|---|---|---|
| Bernoulli | devices `[1,4,6]`；`stochastic_formal_witness_affinity_v1` / `[38,30,30]` | `[36/2,30/0,22/8]`；7 个 formal witness 全在 shard 0/device 1，不跨卡投票 | 88/10；SHA `fc84140520d7b5dd4e8d6066ad57bdca38d3012ce89307a37eec44b08af2b8e2` | `passed=true`；SHA `ab0236c715c47a934278a1deade0b2270e146d8839295e37127905ca0cd7bc95` |
| Remainder | devices `[4,5,6]`；`case_order_round_robin_v1` / `[6,6,6]` | `[6/0,6/0,6/0]` | 18/0；SHA `df4617204c4a396d8e21be346f4d9efdea8f302db0a794e08389ce4f7b095d3b` | `passed=true`；SHA `4b886bdac4cdb40d5939a0a62ec16381fd534d201e655ce1660384364d4ca529` |
| Roll | devices `[4,5,6]`；`atomic_profile_affinity_round_robin_v1` / `[84,84,84]` | `[81/3,81/3,81/3]` | 243/9；SHA `3998130be206ee629b7575b81e594ad48c9975a1aa31579ace11916df0fbbc24` | `passed=true`；SHA `e3c6eea26ca117aff9f982824cafc47540943fb6830c4169b5ed938e2145888c` |

三份单卡/多卡 verdict SHA 逐算子相同，三份 fresh v3 Task2 gate 均现场重放 rc=0。总账并不信任
旧 gate log 的文字；它从登记的磁盘闭包重新验证 loader、CANN、source/build receipt、ELF、输出、
golden、shard result、merge 和 equivalence。Bernoulli 的 stochastic formal 内容也由正式 single work
重算后与登记 artifact/evidence 对账，而不是只核一个自报 SHA。

## 3 · N0–N10 计划证据状态

| 阶段 | 机读状态 | 本轮证据 | 未闭合项 |
|---|---|---|---|
| N0 基线与差量 | `VERIFIED` | 三份正式 spec/caseset、Roll exact PR 补充取材、spec-change 测试 | 无阶段级缺口 |
| N1 输出写入门 | `VERIFIED` | 三份 precision evidence、driver sentinel 测试 | 无阶段级缺口 |
| N2 显式 ND format | `VERIFIED` | 三份 extension receipt、codegen 测试 | 无阶段级缺口 |
| N3 CANN 版本门 | `VERIFIED` | 三份 receipt 均由 API 实测 CANN 9.0.1；版本 mutation | 无阶段级缺口 |
| N4 DUT 身份/构建 | `VERIFIED` | 三份 VERIFIED build receipt、三枚 ELF、双符号定义者 | 无阶段级缺口 |
| N5 权威覆盖/RGE/dtype | `VERIFIED_WITH_STRUCTURED_GAPS` | 三份自产 caseset、dtype requirement ledger | Roll 的 int16/int64 未生成，已结构化挂账 |
| N6 多输入/广播/host scalar | `VERIFIED` | Remainder 三类多输入 profile；Bernoulli host scalar/RNG receipt | 无阶段级缺口 |
| N7 rank0/空数组/layout | `VERIFIED` | Roll 空 dims、empty、rank0、layout；Bernoulli rank0/非连续；mutation | exact DUT 的 rank0 失败是被门检出的正式结果，不是门缺失 |
| N8 Bernoulli 随机前提 | `VERIFIED` | formal stochastic evidence `SATISFIED`，整体置信度 0.999 | 无阶段级缺口 |
| N9 性能证据 | `VERIFIED_WITH_STRUCTURED_GAPS` | 三份 measure-only msprof 报告 | 三份“不低于/不影响原算子性能”均未取 baseline |
| N10 正式见证/双来源 | `PARTIALLY_VERIFIED` | 三算子单卡正式裁决、多卡 precision-only 等价、Roll exact PR intake 已完成 | Bernoulli exact online identity 缺失；Remainder online identity 歧义；A2/A5 产品覆盖见各算子 gap |

因此，三算子**单卡正式验收和多卡精度等价复验都已实跑**，但双来源只闭合 Roll，统一计划 N10
仍不能标成完全完成。

## 4 · 共用运行身份

三轮都在 Atlas A3 环境使用 `cpp_extension`；CANN 版本不是从路径猜测，而是
`aclsysGetCANNVersion` 实测为 `9.0.1`。定义该 API 的 `libascendcl.so` SHA-256 为：

```text
94edd165d95210c2a46c52f77f0215cb64e78c42176a451f8e118daccfa912fe
```

运行栈共同记录 `torch 2.10.0+cpu`、`torch_npu 2.10.0`、逻辑 SoC `ascend910_93`。每个 DUT
都先以 `snapshot-digest → measured build → emit` 生成 `VERIFIED` build receipt，再由运行 receipt
逐字绑定 workspace/stage2 双符号定义者与实际加载 ELF。

## 5 · Bernoulli

### 5.1 输入、来源锚与执行身份

| 项 | 实测值 |
|---|---|
| 任务书 | 本地 `aclnnBernoulli_task_doc.md`；snapshot `e9c1b1722f49c8cbc800a99cf936caee3662707781abd4e64399e81eba29cde4` |
| 被测源码 | 本地 checkout `ops-math-feat-aclnn-bernoulli-memory-optimization` |
| provenance | `local_source → local_snapshot`；scope `experimental/random/bernoulli` |
| subtree merkle | `346ae94fda8bedb580d87bf24839b3c343c5d119dddcfbf68971c71393ea2c98` |
| full snapshot | `e4f33a83a060694a079b99dd0e5448f5602b01ecaf746628bcdde574d5c5ed63` |
| source envelope | `47d5e605c9c48d181ee40ee8b4ed2e9e0e49c476f1fb5881435474b95bdd49d2` |
| 正式 device | Atlas A3 device 1；实际 SoC `Ascend910_9382`；fingerprint `d8fad0861b98413405af4eef17451fc5adb58eea96092b6de919c49edb903b99` |
| DUT ELF | SHA-256 `d490d9dd6a085526262512699d7a0436cd2d9d6a493da74ec48d11f1d1d8b94a` |
| 双符号 | `aclnnBernoulliGetWorkspaceSize` / `aclnnBernoulli`，定义者与加载 ELF 一致 |

### 5.2 R/G/E、精度与性能

- R/G/E：10 个任务书 dtype 全进入 generated；`R=98`、`G=98`、`E planned=98`，结构排除 0；
  execution 为 88 produced、10 failed、0 invocation excluded。
- 10 个失败恰好覆盖 10 个 dtype 的 rank0 case，唯一错误为
  `aclnnBernoulliGetWorkspaceSize failed, ret=161002`。
- 88 个真正执行的 case 全部通过；数值 mismatch 0、uncertain 0、contract problem 0。
- Bernoulli formal stochastic evidence 已满足边界、均值、同 seed/offset 重复性、seed/offset 独立性，
  family-wise confidence 为 0.999。这个前提通过不覆盖 rank0 的 exact DUT 执行失败。
- 性能是 `measure_only`：97 个性能例中 87 measured、10 blocked，实测 case 范围 0.900–55.421 μs。
  dtype 聚合值（μs）为 bf16 34.221、bool 31.151、fp16 32.201、fp32 34.371、fp64 2.860、
  int16 2.985、int32 32.186、int64 30.376、int8 33.431、uint8 35.626。
- 任务书“不低于原算子性能”没有 baseline，只能记
  `performance_requirement_unvalidated`；另有 Atlas A2 未测的覆盖限制。
- 最终机器裁决：`FAIL(精度)`。Bernoulli 没有做独立 stock A/B，不能把 Roll 的 A/B 结论外推到它；
  唯一可写的是 receipt-bound exact DUT 的 10 个 rank0 case 实测返回 161002。

### 5.3 正式产物登记

完整机器路径只在 ignored R/G/E ledger；下表用稳定 artifact ID 与相对语义登记。

| Artifact ID | 相对语义 | SHA-256 |
|---|---|---|
| `bern.taskdoc` | intake taskdoc snapshot | `e9c1b1722f49c8cbc800a99cf936caee3662707781abd4e64399e81eba29cde4` |
| `bern.source` | formal `source_facts.json` | `0505fb769dccd0d75f91d20ffd3562de5de036a036a588e71c3cdf8557d54e10` |
| `bern.spec` | formal `spec.json` | `4f504074f5423cc5b366e8e4394e80fc3cfe87fb1c9dfffe37da61f5c70b9962` |
| `bern.cases` | formal `caseset.json` | `498c4629bdd29dcf9555ccea8ede34455326ad0e06633224df286bab3423834b` |
| `bern.evidence` | formal `evidence.json` | `0003450380e0b15521392ec11885989507be6456e480679aab6a4424f7c6cad0` |
| `bern.stochastic` | work stochastic formal evidence | `0b11a08c79cba8cd40ff7bfc6ff2402521d42f1ee6c762ec1a9d75291d9eebf7` |
| `bern.verdict` | formal `verdict.json` | `fc84140520d7b5dd4e8d6066ad57bdca38d3012ce89307a37eec44b08af2b8e2` |
| `bern.perf` | formal `perf_report.json` | `f02d59ff86381988a9b8d875bd4352868255c2568608952f112b064ceb1b877a` |
| `bern.acceptance` | formal `acceptance.json` | `8e599254a179527d129181b93a992c1f2131611b40c5a575a7a3a710ba87e1d2` |
| `bern.extension_receipt` | formal work receipt | `66957a79d8abe270026ba1d6652a04b7b1719f07b963e04b92ca65c108b9d6df` |
| `bern.build_receipt` | N9 vendor build receipt | `08e0c83020a5c2c139dc7447b99064a15b42c08a00fe1337ecabc59c81ba4c4b` |
| `bern.vendor_elf` | receipt-bound `libcust_opapi.so` | `d490d9dd6a085526262512699d7a0436cd2d9d6a493da74ec48d11f1d1d8b94a` |
| `bern.gate1` | final Task1 gate log | `f30d19476b7dfd8ed45512ff630b70f95dd43d49d3e8d08ea3341c5a6bab7e54` |
| `bern.gate2` | final Task2 gate log | `648fc81af7bb62545bad3aba037f7b8ae08cf373ce9b25bd4eebbffc72f5d04c` |
| `bern.report` | formal Chinese report | `40310a8c752e834a1eaf977673ad917fd698e8c094f4ff7ab42984b6583c3af9` |

## 6 · RemainderTensorTensor

### 6.1 输入、来源锚与执行身份

| 项 | 实测值 |
|---|---|
| 任务书 | 本地 `aclnnRemainderTensorTensor_task_doc.md`；snapshot `b8487ce4160d33712c4cf148ccde8e9b542415967f5e417b5cec3559dfdf3a71` |
| 被测源码 | 本地 checkout `ops-math-feat-aclnn-remainder-tensor-tensor-memory-optimization` |
| provenance | `local_source → local_snapshot`；scope `math/floor_mod` |
| subtree merkle | `0eee37732409f549b8ce709bc3f55eac39ba00d72397b533f2328384c1c9ccf6` |
| full snapshot | `8a8dfad1ebfb55d3a288c74208f6f3d31718ae96871f36f251ab304e16d4c3c8` |
| source envelope | `b86615fd751eaeee406de43680bf01090afb73177d9890d7d7c6dd498a9d4c39` |
| 正式 device | Atlas A3 device 2；receipt 记录逻辑 SoC `ascend910_93`，没有单列 runtime fingerprint |
| DUT ELF | SHA-256 `0f85b0afe453c736ad9f924a592ff8b9fdaba982e621a4ff2fb595477ab41a4d` |
| 双符号 | `aclnnRemainderTensorTensorGetWorkspaceSize` / `aclnnRemainderTensorTensor`，定义者与加载 ELF 一致 |

### 6.2 R/G/E、精度与性能

- R/G/E：6 dtype × 3 profile，共 `R=G=E=18`；0 structured exclusion、0 execution failure。
- 三个 profile 是 broadcast（`self[2,1,4]` / `other[1,3,1]`）、rank mismatch
  （`self[2,3,4]` / `other[4]`）、rank0 tensor（`self[]` / `other[2,3]`，other 非零）。
- 精度 18/18 通过，数值失败、执行失败、uncertain 均为 0。
- 性能 `measure_only` 18/18 measured，逐 case 4.81–90.74 μs；dtype 聚合值（μs）为
  bf16 5.66、fp16 5.41、fp32 5.48、fp64 69.47、int32 5.03、int64 5.52。
- post-finalize Task1/Task2/Task3 都 `PASSED`；Task3 只证明性能采集完整，不证明“不低于原算子性能”。
- 仍有两个 gap：未取原算子 baseline；任务书覆盖 Atlas A2/A3，但本轮只测 A3 device 2。
- 最终机器裁决是 `PASSED_WITH_GAPS`，exit 2，要求 human CP。旧的错误 work-dir 和旧 BF16
  diagnostic 已被正式实测否定，不能引用旧 report 或临时结果翻案。

### 6.3 正式产物登记

| Artifact ID | 相对语义 | SHA-256 |
|---|---|---|
| `rem.taskdoc` | intake taskdoc snapshot | `b8487ce4160d33712c4cf148ccde8e9b542415967f5e417b5cec3559dfdf3a71` |
| `rem.source` | final2 `source_facts.json` | `bcb066ca262357b0eb7a1539df0aea547d093229a71bfbbe3fcb7054ae777999` |
| `rem.spec` | final2 `spec.json` | `310ccc80f577e1f98a3898a228d35b71723740ab7080cab16e19f9b1038fba54` |
| `rem.cases` | final2 `caseset.json` | `723c3a6268050994a81c4de4a89df86843553d3b77f7b489d94b1d6ccc5f833b` |
| `rem.evidence` | final2 `evidence.json` | `a372156b6839ed8545f33a840d9610ac0c50b52bc472c6ffe6ef7cec7c6a87b0` |
| `rem.verdict` | final2 `verdict.json` | `df4617204c4a396d8e21be346f4d9efdea8f302db0a794e08389ce4f7b095d3b` |
| `rem.perf` | final2 `perf_report.json` | `e7256fdf6864127a71bf2e9dcf40764de148eb3c905334bf12f62b5668bdb89d` |
| `rem.acceptance` | final2 `acceptance.json` | `43bf992ae1c80b2f5fda10ed54156ab36369f51771caf6a1bb6ad0d06bd35d02` |
| `rem.extension_receipt` | final2 work receipt | `2b422664c8e89c8febe72c653fabb1e841a9d66e33dffefe04a7123bd59a2f51` |
| `rem.build_receipt` | N9 vendor build receipt | `aa6da23914f4f7bc3065ef009ba3e673f8eaef996191ab1cadf05248999560d1` |
| `rem.vendor_elf` | receipt-bound `libcust_opapi.so` | `0f85b0afe453c736ad9f924a592ff8b9fdaba982e621a4ff2fb595477ab41a4d` |
| `rem.gate1` | post-finalize Task1 log | `de024446b22daf1e254a01f157fe249f81e6f0da9eb50611f7e300b46c452d26` |
| `rem.gate2` | post-finalize Task2 log | `dc57700eb20179dcaea727266047c5ebb49bcf122b25704a566b3038ad4a5de8` |
| `rem.gate3` | post-finalize Task3 log | `8de57549991cff1ce7bab9ef251b22ad2d78ea1b81352b2e1a1417023305684d` |

formal final2 根没有独立 `验收报告.md`，本记录不虚构该文件。

## 7 · Roll

### 7.1 输入、来源锚与执行身份

| 项 | 实测值 |
|---|---|
| 任务书 | GitCode 在线 URL；snapshot `1088ffd3af097d450de6e25667c34afb4fe5f8e539ab7ca4418a4d79d5bd3f06` |
| 被测源码 | 本地 checkout `ops-math-roll` |
| provenance | `local_source → local_snapshot`；scope `experimental/math/roll` |
| subtree merkle | `6fbe788d88c35d1186446614e0f597d11152a940f7cdc0aead7ce50b177fa2be` |
| full snapshot | `d4e898f7f8103135269f375f4c75d318bec1edc3d50187f27535488a0400e281` |
| source envelope | `3d2e9dcc051c94da96db37cacb166ac9f013883aa0c1526edad7e9dd91f2e316` |
| 正式 device | Atlas A3 device 3；receipt 记录逻辑 SoC `ascend910_93`，没有单列 runtime fingerprint |
| DUT ELF | SHA-256 `2c3aeb472cf900b53ae2b0dec6f9f9d0167958e43dd423e9277aef73a5304ef3` |
| 双符号 | `aclnnRollGetWorkspaceSize` / `aclnnRoll`，定义者与加载 ELF 一致 |

### 7.2 R/G/E、精度与性能

- 任务书与保持集共要求 11 dtype；生成/可执行 9 dtype，int16、int64 没有被静默删除，而是进入
  `dtype_unsupported` gap。
- 完整结构分母为 360：252 executable case + 108 有 predicate/source 绑定的 structured exclusions。
  execution 为 243 produced、9 failed、0 invocation excluded。
- caseset 覆盖 flatten、axis 0、多负轴、重复轴循环归一、rank0、rank8、empty tensor、非连续布局。
  27 个 `[0,3]` empty tensor case 全部通过。
- 9 个失败恰好覆盖 9 个生成 dtype 的 rank0/empty-dims case，唯一错误为
  `aclnnRollGetWorkspaceSize failed, ret=161002`；243 个已执行 case 数值失败为 0。
- 性能 `measure_only`：18 个性能例，9 个 large case measured、9 个 rank0 case blocked；实测
  8.67–9.79 μs。逐 dtype值（μs）为 bf16 8.74、bool 8.70、complex64 9.79、fp16 8.72、
  fp32 9.05、int32 8.96、int8 8.69、uint32 9.03、uint8 8.67。
- 未验证“不影响原 dtype 性能”，未测 Atlas A2/A5；最终机器裁决为 `FAIL(精度)`。

### 7.3 rank0 root-cause A/B

以下 baseline/A/B 绑定同一 spec、同一 vendor ELF、同一 252 case 分母，三次 invocation 都是
243 produced / 9 failed，失败 case ID 与唯一 161002 错误完全一致：

| 路径 | 角色 | 实测结果 |
|---|---|---|
| baseline | 正式裁决 | 通用显式 ND converter；243/9 |
| A | diagnostic only | rank0 改用 null storage-shape / storage-rank zero；仍 243/9 |
| B | diagnostic only | rank0 输入输出改用 op-plugin 官方 `ConvertType(at::Tensor)`；仍 243/9 |
| stock control | 差分对照，不是 DUT | 同卡 `torch_npu` stock Roll 对 scalar + shift + empty dims 通过 |

**实测结论**：两种“通用 rank0 descriptor 生成错误”假设均被排除；receipt-bound exact DUT
仍在 workspace-size 阶段拒绝全部 9 个 rank0 dtype case，而 stock 路径接受同语义。

**推断边界**：剩余归因是“exact DUT vendor rank0 regression **或** build/source inconsistency”；
现有证据不能在二者之间二选一。A/B 的实验代码已撤销，没有把为 Roll 诊断写的临时 converter 留进通用 workflow。

### 7.4 正式产物登记

| Artifact ID | 相对语义 | SHA-256 |
|---|---|---|
| `roll.taskdoc` | online intake snapshot | `1088ffd3af097d450de6e25667c34afb4fe5f8e539ab7ca4418a4d79d5bd3f06` |
| `roll.source` | formal-d `source_facts.json` | `c14d2ec2591ceddb90ac607808bf97a4e5c83a1f9cfe911088f7b2ad26fdfe1e` |
| `roll.spec` | formal-d `spec.json` | `f5d9eb9e1fdb7d34aee30515236420f97a01e6ccf152bf8362cc0c020962e882` |
| `roll.cases` | formal-d `caseset.json` | `75eeed27a4eced136d785a8d5ed52a56d88e89b33d55aa516e0264c22c9e18ea` |
| `roll.evidence` | formal-d `evidence.json` | `84ee961061f0f202091d2059a1c7b8920d012948f630ed2852383b8f848bb08a` |
| `roll.verdict` | formal-d `verdict.json` | `3998130be206ee629b7575b81e594ad48c9975a1aa31579ace11916df0fbbc24` |
| `roll.perf` | formal-d `perf_report.json` | `fe8bfab3a432dd47916387b5431214de3195bc23d7eda5128a1fc55af5f5b4c8` |
| `roll.acceptance` | formal-d `acceptance.json` | `3afce119b2bf68324d143df6351e2745d899e6d602c6129f65a4fbdaac0bb532` |
| `roll.extension_receipt` | formal-d work receipt | `224665017919ec9d7d419dc35ef4b973ca932107bcfda4077612dc91b2a9f209` |
| `roll.build_receipt` | N9 vendor build receipt | `c1ffeb906ae6b719530e12badf34faa7d7e76be595681b295e04324422b00b3f` |
| `roll.vendor_elf` | receipt-bound `libcust_opapi.so` | `2c3aeb472cf900b53ae2b0dec6f9f9d0167958e43dd423e9277aef73a5304ef3` |
| `roll.gate1` | final audit Task1 log | `16dddc2900801c5c5d97eb09a8c769846339c22e2bcbf2b290525178692d983a` |
| `roll.gate2` | final audit Task2 log | `eb8aac5352637b6fd3283dd398c0ff668cf5f7048ae8fac66d895f598469da66` |
| `roll.rootcause_ab` | rank0 A/B machine record | `00513179c4744b8863d22772f79f57a9b3afae215c359670501a491aa8f47806` |
| `roll.report` | formal Chinese report | `23d6ad7c1100ee3697a581dc36401413c5f834dd50ae97ddfb9092147b7c507d` |

## 8 · task_pr_gaps 与不能写成完成的事项

| 算子/范围 | gap | 当前状态 |
|---|---|---|
| Bernoulli | “不低于原算子性能”未取 baseline | `UNVALIDATED`；只有 msprof 绝对耗时 |
| Bernoulli | Atlas A2 未测 | `UNVALIDATED`；仅 A3 device 1 |
| Remainder | “不低于原算子性能”未取 baseline | `UNVALIDATED`；只有 msprof 绝对耗时 |
| Remainder | Atlas A2 未测 | `UNVALIDATED`；仅 A3 device 2 |
| Roll | int16/int64 要求未进入 generated/executable | `UNVALIDATED`；显式 dtype gap |
| Roll | “不影响原 dtype 性能”未取 baseline | `UNVALIDATED`；只有 msprof 绝对耗时 |
| Roll | Atlas A2/A5 未测 | `UNVALIDATED`；仅 A3 device 3 |
| N10 | 三算子在线 PR 实际 provenance | `PARTIALLY_EXERCISED`；Roll exact `gitcode_pr` complete；Bernoulli 缺 exact identity；Remainder 两候选 blocked 且不可擅选 |
| N10 | 三算子单卡/多卡固定 caseset 等价复验 | `VERIFIED` for precision-only；三算子的 merged verdict 与各自单卡 verdict SHA 一致，未重采 msprof |

## 9 · 实测与推断的最终分界

- **实测**：taskdoc/source/spec/cases/receipt/ELF 的 SHA；三卡 device/CANN；precision/perf 计数；
  gate 与 acceptance；Roll baseline/A/B/stock control 的逐路径结果。
- **机器裁决**：Bernoulli 与 Roll 为 `FAIL(精度)`；Remainder 为 `PASSED_WITH_GAPS`。
- **推断**：Roll rank0 问题已隔离到 exact DUT vendor regression 或 build/source inconsistency，但未完成二选一。
- **已实测但只完成部分范围**：Roll exact 在线 PR intake；三算子 precision-only 多卡分片/合并等价。
- **未实测**：Bernoulli exact 在线 PR provenance、Remainder 唯一 exact 在线 PR provenance、多卡 msprof/完整 acceptance、
  Atlas A2/A5 产品覆盖、三个性能保持条款的 baseline。
- **禁止外推**：不能把 Roll A/B 归因套到 Bernoulli；不能把在线任务书 fetch 说成在线 PR DUT；
  不能把 gate passed 或有 msprof 数字写成算子整体通过。
