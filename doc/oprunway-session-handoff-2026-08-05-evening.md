# OpRunway 会话交接 · 2026-08-05 晚

> **本文是当前交接入口**，接替同日的 `doc/oprunway-session-handoff-2026-08-05.md`（那份写于开工前）。
> 只写「接下来做什么、从哪开始、有什么坑」，不堆历史。流水看 `doc/oprunway-changes-brief.md`。

---

## 1 · 一句话

`doc/oprunway-local-source-plan.md` 的 Step 0–6 **全部落地**，本地 checkout 已是一等被测来源通路，
`runner_form` 准入收敛到 `cpp_extension`；当日晚**已在 a3 上跑通一次端到端真机验收并出裁决**。
⚠ 那次裁决是 **`FAIL(精度)`**，且**性能维没跑到**（精度 fail → Task3 fail-fast）——
证明的是通路走通，不是算子过了、更不是性能维已覆盖。

---

## 2 · 已完成（对照 local-source-plan 的 Step）

| Step | 内容 | 状态 |
|---|---|---|
| 0 | 冻结基线 | ✅ 全程保持全绿 |
| 1–2 | `dut_source` 判别式 + `fetch_source --local-repo/--op-subdir/--base-ref/--allow-dirty` | ✅ 真机跑过（取材段，与在线通路逐字比对） |
| 3 | `aclnn_py` 侧：`preflight_aclnn` 分支 | ✅（`verify_aclnn_harness` 见 §3） |
| 4 | `cpp_extension` 主链：adapter / driver / 三级门 | ✅ 代码接通 + 单测 + 真机端到端跑通（见下面倒数第二行） |
| 4b | CP-F 精度复测：`precision_retest_contract` / `_runner` | ✅ 但 schema breaking，见 §4 |
| 5 | 报告「来源与 provenance」节按 kind 渲染 | ✅ |
| 6 | 通路收敛到 `cpp_extension`（入口 + 出口两道门 + 逃生阀） | ✅ |
| — | `vendor_build_receipt` **产出方** `make_vendor_build_receipt.py`（此前真机上根本没有产出方） | ✅ 新建 + 一轮对抗审修落地；⚠ 未接进编排，见 §3 |
| — | **本地来源跑通真机验收**：`cpp_extension` + `local_checkout` 端到端，构建 → 收据 → NPU → 三级门 → 裁决 → 报告 | ✅ 出裁决 `FAIL(精度)`（1344 例 / 58 fail）、验收门 `PASSED`、三级门 `PASSED`；⚠ 性能维跳过，见 §3 |

⚠ 本地来源那次的 1344 例 / 58 fail 与 `AGENTS.md` §4.4 的「1152 例、51 FAIL」**不是同一个 caseset**
（本次用 `plugin/samples/specs/median.spec.json`）。别写成「复现了基线」，也别拿它改 §4.4。
细节见 `doc/oprunway-local-source-realmachine-validation.md`。

外加三件顺手的：

- `precision_policy.derive_output_dtype` 漏解析 `<from_input>` 哨兵（把哨兵字面量当 dtype 返回）；
- `aclnn_driver` f-string 替换字段跨行（PEP 701，真机 3.11 SyntaxError；本地 `ast.parse(feature_version=(3,11))` 测不出来）；
- main 基线上原本就红的 5 个测试全部修好。

**当前基线：a3 容器 Python 3.12.13，push 前审修门末轮实测 1800 passed / 0 failed**
（更早那次是 1774 passed / 11 skipped / 0 failed，515 subtests）。
⚠ 之后又新增了 `test_precision_retest_runner.test_cpf_only_supports_cpp_extension`，**没重跑全量**。

---

## 3 · 没做的 / 挂账的 ⚠ 这节最重要

| 项 | 说明 |
|---|---|
| **本地来源的性能维仍未见证** | 端到端验收已跑通并出裁决，但那轮精度判 `fail` → Task3 按既有 fail-fast 跳过（`perf_status=skipped_precision_gate`）。**「本地来源能出性能裁决」这件事没被证明过。** 精度维已证，性能维别一起写成已覆盖 |
| **产出方没接进编排** | `make_vendor_build_receipt.py` 已落地并在真机实战用过一次，但 `SKILL.md` / `plugin/AGENTS.md` 里**没有一句**说要产这份收据——本次是手工调用跑出来的，下一轮上真机的人照样会漏。代码层面的洞补上了，流程层面还没有 |
| **收据对 `--library` 只是弱绑定** | 构建前后各取一次 `(mtime_ns, size, sha256)`，三项全同即 fail-closed。这只证明「该文件在构建窗口内被改写」，**不证明它由那条 argv 产出**——一次 `touch` 就能骗过 |
| **`aclnn_py` + `local_checkout` 结构性 fail-closed** | `verify_aclnn_harness` 判别式已接但显式拒 `local_checkout`：`aclnn_adapter` 只能按 PR ref 在容器内重新取源 build，**构建端根本不存在可与 `local_root_digest` 对账的锚**。放它过去，收据看着齐全、绑定其实是空的。只要 `aclnn_adapter` 的取源方式不变，这道门就一直关着——**不是排期问题，别当成「下一批补上」** |
| **`root_digest` 只覆盖 `op_subdir`** | 不含仓级构建脚本、公共头文件。它证明「被测算子子树的字节是这一份」，**不证明**「整个构建输入闭包是这一份」 |
| **三级门的残留伪装面** | `source_facts` 缺席 + 收据自称 `pull_request` 时，「`source_facts` 其实说的是 local」查不出来（没有对照物）。PR 通路沿用旧行为是**实测逼出来的**：真机报告目录里本来就没有 `source_facts.json`。要彻底封死，得让**编排层每次都传 `--source-facts`**，让缺席本身成为非法 |
| **CP-F 对 `cpp` / `aclnn_py` base spec 一律拒跑** | **已定口径（2026-08-05）：不允许复测**，不再是待确认的副作用。理由是产物形态——`--allow-experimental-form` 的安全性全靠「物理上不产 `verdict.json`」，而 CP-F 就是要写 `verdict.json`。被拒 = 复测能力不覆盖该通路，**不表示基础验收失效**。已落 `AGENTS.md` §9.2 + runner 文案/注释 + 单测 |
| **CP-F `repo` 对账只覆盖 `cpp_extension`** | `cpp` / `aclnn_py` 的首轮 `execution_provenance` 里没有仓名字段，没有对照物，`repo` 只作人工记账 |

---

## 4 · 下一步从哪开始（按序）

先记三条**已经做完、别再重复排期**的（原第 1、3、4 条）：

| 原编号 | 事项 | 结果 |
|---|---|---|
| 1 | 写 `vendor_build_receipt` 产出方 | ✅ `make_vendor_build_receipt.py` 已新建 + 过一轮对抗审修。⚠ `local-source-plan` Step 4 那句「先 grep 找写入方、找不到就停下来问、别自己新写」**已作废**：真机上实跑核过就是没有，产出方是新建的。⚠ 未接进编排 → 变成下面第 2 条 |
| 3 | 定 CP-F 对非准入通路的口径 | ✅ **不允许复测**，已成明文规则（§3 那行 + `AGENTS.md` §9.2） |
| 4 | 本地来源跑一次真机验收 | ✅ 已跑通并出裁决 `FAIL(精度)`。⚠ **性能维仍未见证** → 变成下面第 3 条 |

还没做的：

1. **重新起草 CP-F directive、重跑 F2。** schema 是 breaking（`pr_head` → `pr_head_sha` / `local_root_digest`，
   `repo` 必填），在途 attempt 全废。起草时注意 `repo` 写法要和首轮 build receipt 的
   `runner_binding.base_source_repo` **逐字**一致。
2. **把 `make_vendor_build_receipt.py` 接进编排。** 现在 `SKILL.md` / `plugin/AGENTS.md` 里没有一句
   说构建后要产这份收据，真机上全靠人记得手工调一次。代码层面的洞已补，流程层面还没有。
3. **见证本地来源的性能维。** 上一轮精度 fail → Task3 fail-fast 跳过，性能通路一次没走到。
   要么换一个精度能过的见证，要么先把精度那 58 例的归因做掉。
   ⚠ 归因前按 §5.8 先核任务书↔被测对应、再解耦 DUT 与 harness，别直接下结论。
4. workflow 治理批：`doc/oprunway-workflow-governance-plan.md`，按其 §6 分批。
   Roll 要继续做正式验收得先迁到 `cpp_extension`（torch.ops 桥 + vendor ELF 收据，成本更高）。

---

## 5 · 坑

| 坑 | 表现 / 绕法 |
|---|---|
| **浅克隆** | 给了 `--base-ref` 但历史被截断 → `merge-base` 找不到共同祖先 → `fetch_source` **直接报错中止**（不是静默记 `unavailable`）。绕法：`git fetch --unshallow`，或干脆不给 `--base-ref`（那样 `changed_files` 记 `"unavailable"`） |
| **真机报告目录里没有 `source_facts.json`** | 取材的 `--out` 和验收产物目录不是同一个。本地通路找不到就 BLOCKED；用 `validate_acceptance_state --source-facts <路径>` 显式指路 |
| **构建把产物吐进 `op_subdir` 会顶掉指纹** | ops-nn 的产物落**仓根 `build_out/`**，所以那次构建前后 `root_digest` 都是 `c8867ce09f6e…`，收据那两道「构建树 ↔ 指纹树」门没误伤。**这不是通用保证**：换个把中间产物写进算子目录的仓形态，构建后摘要就会变、门会（正确地）拦下来。到时候要定的是「指纹在构建前取还是构建后取」，**不是去关门** |
| **既有 preparation 收据会变 `MISS`** | `producer.logic_sha256` 是 `fetch_source.py` 自身源码的哈希、且在 payload 里，改工具必然改 digest。**这是正确行为**，别去「修」复用逻辑，重跑取材即可 |
| **真机上留存的 aclnn 信任门收据会 revalidate 失败** | `_LOGIC_FILES` 加了 `dut_source.py`（判别式已成这道门的判定依赖）。同理是正确行为；要走 `aclnn_py` 真机通路得先重跑这道门 |
| **`changed_files` 的 `"unavailable"` 不是 `[]`** | 后者语义是「确实没改动」。下游若有「`completeness.reasons` 空即万事大吉」的假设，要同步改成也看新增的 `completeness.warnings` |
| **出口门别当冗余删掉** | 收敛门落在 `_resolve_mode`（入口）和写 `acceptance.json` / `verdict.json` 前（出口）两处。只拦入口拦不住 |
| **本地 `ast.parse(feature_version=(3,11))` 测不出 PEP 701** | 语法门要用真 3.11 解释器跑（a5 上有） |
