# OpRunway 会话交接 · 2026-08-05 晚

> **本文是当前交接入口**，接替同日的 `doc/oprunway-session-handoff-2026-08-05.md`（那份写于开工前）。
> 只写「接下来做什么、从哪开始、有什么坑」，不堆历史。流水看 `doc/oprunway-changes-brief.md`。

---

## 1 · 一句话

`doc/oprunway-local-source-plan.md` 的 Step 0–6 **全部落地**，本地 checkout 已是一等被测来源通路，
`runner_form` 准入收敛到 `cpp_extension`。
⚠ **但本地来源一次 NPU 都没跑过**——接通的是代码和门，不是验收。

---

## 2 · 已完成（对照 local-source-plan 的 Step）

| Step | 内容 | 状态 |
|---|---|---|
| 0 | 冻结基线 | ✅ 全程保持全绿 |
| 1–2 | `dut_source` 判别式 + `fetch_source --local-repo/--op-subdir/--base-ref/--allow-dirty` | ✅ 真机跑过（只到 CP-A 取材） |
| 3 | `aclnn_py` 侧：`preflight_aclnn` 分支 | ✅（`verify_aclnn_harness` 见 §3） |
| 4 | `cpp_extension` 主链：adapter / driver / 三级门 | ✅ 代码接通 + 单测；⚠ **外部构建驱动未改**，见 §3 |
| 4b | CP-F 精度复测：`precision_retest_contract` / `_runner` | ✅ 但 schema breaking，见 §4 |
| 5 | 报告「来源与 provenance」节按 kind 渲染 | ✅ |
| 6 | 通路收敛到 `cpp_extension`（入口 + 出口两道门 + 逃生阀） | ✅ |

外加三件顺手的：

- `precision_policy.derive_output_dtype` 漏解析 `<from_input>` 哨兵（把哨兵字面量当 dtype 返回）；
- `aclnn_driver` f-string 替换字段跨行（PEP 701，真机 3.11 SyntaxError；本地 `ast.parse(feature_version=(3,11))` 测不出来）；
- main 基线上原本就红的 5 个测试全部修好。

**当前基线：a3 容器 Python 3.12.13，1774 passed / 11 skipped / 0 failed（515 subtests）。**

---

## 3 · 没做的 / 挂账的 ⚠ 这节最重要

| 项 | 说明 |
|---|---|
| **本地来源没跑过真机验收** | 只到 CP-A 取材 + 单测。**没跑 NPU、没出精度裁决、没出性能裁决。** 任何地方不许写成「本地来源已完成验收」 |
| **`vendor_build_receipt` 压根没有产出方** | 本仓只有消费方（三处读 + 两个测试夹具）。⚠ **2026-08-05 在 a3 上实跑 `grep -rl 'oprunway.vendor_build_receipt'` 核过：真机上也没有产出方**——`/work/run` 与保护根里全部命中都是 plugin 自己的消费代码、收据本身和日志。Median PR6429 那份 `/tmp/oprunway-cppext-49e898f/vendor-build-receipt.json`（2026-07-27 11:03，743 字节）是**人手写的**，从来没有生成脚本。所以下一步不是「去找产出方」而是「得写一个」。**这是本地来源真机跑通的唯一硬卡点** |
| **`aclnn_py` + `local_checkout` 结构性 fail-closed** | `verify_aclnn_harness` 判别式已接但显式拒 `local_checkout`：`aclnn_adapter` 只能按 PR ref 在容器内重新取源 build，**构建端根本不存在可与 `local_root_digest` 对账的锚**。放它过去，收据看着齐全、绑定其实是空的。只要 `aclnn_adapter` 的取源方式不变，这道门就一直关着——**不是排期问题，别当成「下一批补上」** |
| **`root_digest` 只覆盖 `op_subdir`** | 不含仓级构建脚本、公共头文件。它证明「被测算子子树的字节是这一份」，**不证明**「整个构建输入闭包是这一份」 |
| **三级门的残留伪装面** | `source_facts` 缺席 + 收据自称 `pull_request` 时，「`source_facts` 其实说的是 local」查不出来（没有对照物）。PR 通路沿用旧行为是**实测逼出来的**：真机报告目录里本来就没有 `source_facts.json`。要彻底封死，得让**编排层每次都传 `--source-facts`**，让缺席本身成为非法 |
| **CP-F 对 `cpp` / `aclnn_py` base spec 现在一律拒跑** | `precision_retest_runner` 调 `run_workflow._resolve_mode(spec, None)`，**不传** `allow_experimental_form` → 非准入通路的旧验收无法复测。**尚未确认是有意还是收敛的副作用**，需要定口径 |
| **CP-F `repo` 对账只覆盖 `cpp_extension`** | `cpp` / `aclnn_py` 的首轮 `execution_provenance` 里没有仓名字段，没有对照物，`repo` 只作人工记账 |

---

## 4 · 下一步从哪开始（按序）

1. **写一个 `vendor_build_receipt` 产出方（构建后自动落收据）。**
   这是本地来源真机跑通的唯一硬卡点，其余都排在它后面。

   ⚠ **别再照 local-source-plan Step 4 去「找写入方」了**——那条已在 2026-08-05 实跑核过：
   真机上不存在任何产出方，Median 那份收据是人手写的。所以这一步是**新建**，不是修改。
   要写的东西很薄：build 完之后把 `source` / `build` / `artifact` 三段落成 JSON，
   其中 `artifact.library_sha256` 必须**现算**安装后那个 ELF 的摘要（不能抄构建目录里的中间产物）。

   `source` 段按 `dut_source` 分支——这正是本地来源接不上的那一处：

   ```jsonc
   "source": {
     "dut_source": "local_checkout",   // 缺省 "pull_request"，PR 通路不写这个键
     "repo": "<必填，两条通路都要>",
     "local_root_digest": "…64 位…"    // local 时必填；PR 时改填 pr_head_sha（40 位）
   }
   ```

   `local_root_digest` 的值直接取 `fetch_source` 产的
   `source_facts.payload.local_checkout.root_digest`——三级门就是拿这两个值做等值校验的，
   对不上即 BLOCKED。⚠ 别自己重算一遍：重算意味着两处各有一份摘要实现，迟早分叉。

   改完在本仓两个测试夹具（`test_cpp_extension_driver.py` / `test_validate_cpp_extension_receipt.py`）
   里同步加 local 形态样例，作为契约的机器化定义。
2. **重新起草 CP-F directive、重跑 F2。** schema 是 breaking（`pr_head` → `pr_head_sha` / `local_root_digest`，
   `repo` 必填），在途 attempt 全废。起草时注意 `repo` 写法要和首轮 build receipt 的
   `runner_binding.base_source_repo` **逐字**一致。
3. **定 CP-F 对非准入通路的口径**（§3 最后两行）：`cpp` / `aclnn_py` 的旧验收要不要能复测。
   要能，就给 `precision_retest_runner` 一条显式的实验通路参数；不要，就把这条写成规则。
4. **本地来源跑一次真机验收**（第 1 步做完才有意义）：`cpp_extension` + `local_checkout` 端到端，
   出精度裁决。这一步跑完之前，AGENTS.md §9.3 那条「没跑过 NPU」不许改。
5. workflow 治理批：`doc/oprunway-workflow-governance-plan.md`，按其 §6 分批。
   Roll 要继续做正式验收得先迁到 `cpp_extension`（torch.ops 桥 + vendor ELF 收据，成本更高）。

---

## 5 · 坑

| 坑 | 表现 / 绕法 |
|---|---|
| **浅克隆** | 给了 `--base-ref` 但历史被截断 → `merge-base` 找不到共同祖先 → `fetch_source` **直接报错中止**（不是静默记 `unavailable`）。绕法：`git fetch --unshallow`，或干脆不给 `--base-ref`（那样 `changed_files` 记 `"unavailable"`） |
| **真机报告目录里没有 `source_facts.json`** | 取材的 `--out` 和验收产物目录不是同一个。本地通路找不到就 BLOCKED；用 `validate_acceptance_state --source-facts <路径>` 显式指路 |
| **既有 preparation 收据会变 `MISS`** | `producer.logic_sha256` 是 `fetch_source.py` 自身源码的哈希、且在 payload 里，改工具必然改 digest。**这是正确行为**，别去「修」复用逻辑，重跑取材即可 |
| **真机上留存的 aclnn 信任门收据会 revalidate 失败** | `_LOGIC_FILES` 加了 `dut_source.py`（判别式已成这道门的判定依赖）。同理是正确行为；要走 `aclnn_py` 真机通路得先重跑这道门 |
| **`changed_files` 的 `"unavailable"` 不是 `[]`** | 后者语义是「确实没改动」。下游若有「`completeness.reasons` 空即万事大吉」的假设，要同步改成也看新增的 `completeness.warnings` |
| **出口门别当冗余删掉** | 收敛门落在 `_resolve_mode`（入口）和写 `acceptance.json` / `verdict.json` 前（出口）两处。只拦入口拦不住 |
| **本地 `ast.parse(feature_version=(3,11))` 测不出 PEP 701** | 语法门要用真 3.11 解释器跑（a5 上有） |
