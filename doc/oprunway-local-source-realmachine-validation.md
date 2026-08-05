# 本地来源通路 · 真机验证记录（2026-08-05，a3）

> 验的是 `doc/oprunway-local-source-plan.md` 的目标 1（本地来源一等通路）。
> 本文分两段：**①§1–5 取材段**（本地 vs 在线两条通路的事实包比对）；
> **②§6 端到端验收段**（同日续跑：构建 → 收据 → NPU 跑测 → 三级门 → 裁决 → 报告）。
>
> ⚠ **本次裁决是 `FAIL(精度)`。** 本文证明的是**通路走通**，不是精度达标——两件事别混。
> ⚠ **性能维没跑到**（精度 fail → Task3 fail-fast 跳过），见 §7。

---

## 1 · 见证选的是什么

| 项 | 值 |
|---|---|
| 任务书 | `cann/cann-ops-competitions` → `04_tasks/01_community-task-2026/docs/202604/median_task_doc.md` |
| 被测 PR | `cann/ops-nn` MR 6429，head `0290d61ac066f9f4e620a3714f5941e82dc4e72a` |
| 算子子目录 | `experimental/index/median` |
| 执行环境 | a3 容器 `oprunway_prov`，Python 3.12.13、CANN 9.0.1、SoC `ascend910_93`、torch 2.10.0+cpu / torch_npu 2.10.0 |
| 工作目录 | `/work/run/oprw19`（新建，未碰任何保护根） |

选 Median 是因为它是**当前唯一跑通完整 torch_parity 矩阵**的算子（`AGENTS.md` §9），
拿它当见证能压满结构轴。**没有为 Median 写任何特判**——所有判断都走通用字段探测。

## 2 · 怎么克的（可复现）

```bash
# 任务书仓：浅克隆够用（只取一个 md 文件）
git clone --depth 1 https://gitcode.com/cann/cann-ops-competitions.git

# 被测仓：先取 PR head，再补 base
git init ops-nn && cd ops-nn
git remote add origin https://gitcode.com/cann/ops-nn.git
git fetch --filter=blob:none --no-tags origin refs/merge-requests/6429/head:pr6429
git checkout pr6429
git fetch --filter=blob:none --no-tags --unshallow origin master:refs/remotes/origin/master
```

⚠ **`--depth` 会把整个仓变成 shallow，`merge-base` 随即失败**，`changed_files` 就算不出来。
第一次跑正是被这条卡住，工具给的错误信息一字未改地说中了病因：

```
--base-ref 'refs/remotes/origin/master'（8d67308d4a93）与 HEAD **没有共同祖先**，算不出改动清单。
  这通常说明给错了 base（或仓是浅克隆、历史被截断）。
  不给 --base-ref 可以继续，changed_files 记 'unavailable'。
```

`--unshallow` 之后 `merge-base` 解到 `548fa95a0e4d…`，一次通过。
（这条与 `AGENTS.md` §7「浅克隆不能冒充指定 tag/commit 已核实」同源，
只是这里暴露的是另一个后果：**浅克隆还会让改动清单算不出来**。）

## 3 · 两条通路各跑一次

```bash
# 本地：只给文件路径和目录，**不带任何 PR id**
python3 plugin/acc-common/fetch_source.py \
  --taskdoc    .../median_task_doc.md \
  --local-repo .../ops-nn \
  --op-subdir  experimental/index/median \
  --base-ref   refs/remotes/origin/master \
  --out        out_median_local

# 在线：任务书链接 + PR 链接
python3 plugin/acc-common/fetch_source.py \
  --taskdoc https://gitcode.com/.../median_task_doc.md \
  --pr      https://gitcode.com/cann/ops-nn/pull/6429 \
  --out     out_median_pr
```

两次都 `completeness=complete`、`reasons=[]`、退出码 0。

## 4 · 结果：被测事实逐字相同，只有 provenance 锚不同

**应当一致的**（实测一致）：

| 字段 | 结果 |
|---|---|
| 任务书字节 | `5d24e7337d79fb5e…`，4379 字节，两边相同 |
| `derived` | `op=median`、`target_dir=experimental/index/median`、`interface_kind=aclnn_2stage`、`aclnn_entry=aclnnMedian`、`aclnn_headers=[experimental/index/median/op_host/op_api/aclnn_median.h]` |
| `changed_files` | 23 个文件，一字不差 |
| `key_files` | 6 份，路径集合与逐份内容摘要都相同 |

唯一有意的差异是 `taskdoc.source_locator`：本地记受控标签 `<local-file>`，在线记 URL。
本地绝对路径既不可移植、又会让同内容跨工作区无法命中，所以**内容身份只认 `bytes_sha256`**。

**应当不同的**（实测不同，且互不伪装）：

| | `dut_source` | 锚 | 有 `pr` 键 | 有 `local_checkout` 键 |
|---|---|---|---|---|
| 本地 | `local_checkout` | `root_digest = c8867ce09f6e5272…` | ❌ | ✅ |
| 在线 | 缺席（→ `pull_request`） | `head_sha = 0290d61ac066f9f4…` | ✅ | ❌ |

⚠ 本地收据里的 `local_checkout.git.head_sha` 恰好也等于 `0290d61ac066…`，
但它是**信息字段不是锚**——worktree 可能 dirty，它与被测字节没有绑定关系。
读锚一律走 `dut_source.identity()`，不许按字段名去翻 `head_sha`。

`aclnn_entry=aclnnMedian` 与 `AGENTS.md` §9 记载的 PR6429 实测一致
（单一统一符号，DUT `.so` 不导出 `aclnnMedianDim`）——说明结构探测在真 PR 上判对了。

## 5 · 下游门（取材段）

`validate_preparation_state._validate_source_payload` 接受这份本地事实包（不抛）。

---

## 6 · 端到端验收：本地 checkout 一路跑到裁决

同日续跑。输入**仍然只有本地路径**（本地任务书文件 + 本地 checkout 目录 + 算子子目录），
**全程不带任何 PR id**。走的是 `cpp_extension` 主链（§4 收敛后唯一准入形态）。

### 6.1 构建

从本地 checkout 全量构建，成功：

```bash
./build.sh --experimental --ops=median --soc=ascend910_93 --vendor_name=customize --pkg
# 再跑生成的 .run --install-path <安装目录>
```

| 项 | 值 |
|---|---|
| 产物 | `libcust_opapi.so` |
| `sha256` | `35ba85e0d719e86e73c291ac5c6b8c2988501eafbe8a7e9f9cb27778801cf14f` |

⚠ **构建前后 `op_subdir` 摘要都是 `c8867ce09f6e…`（没变）**——因为 ops-nn 的产物落在**仓根
`build_out/`**，不写进被测子树。所以 `make_vendor_build_receipt` 那两道「构建树 ↔ 指纹树」门
不会误伤。**这不是通用保证**：换个把中间产物直接吐在算子目录里的仓形态，构建后摘要就会变，
那两道门会（正确地）拦下来——到时候要面对的是「指纹该在构建前取还是构建后取」，不是去关门。

### 6.2 收据

`make_vendor_build_receipt.py`（本轮新落地的产出方，此前真机上根本没有产出方，
Median 那份老收据是人手写的）产出 `vendor_build_receipt`，**四道校验全绿**。
`source` 段按 `dut_source` 分支，实测落成：

```json
"source": {
  "repo": "https://gitcode.com/cann/ops-nn.git",
  "repo_source": "local_checkout.git.remote_url",
  "local_root_digest": "c8867ce09f6e…",
  "dut_source": "local_checkout"
}
```

`local_root_digest` 与 §4 那份 `source_facts` 的 `root_digest` **是同一个值**——
三级门就是拿这两个值做等值校验的，这次在真机上真的对上了。

### 6.3 NPU 跑测与裁决

`acceptance.json`：

| 项 | 值 |
|---|---|
| `op` / `repo_mode` | `Median` / `cpp_extension` |
| `state` | `FAILED_PRECISION` |
| `overall` | **`FAIL(精度)`** |
| Task1 生成 | 1344 例 |
| Task2 裁决 | `fail` — `{total:1344, fail:58, uncertain:0, risk:0, gaps:0, scaled:0, golden_blocked:0, contract_problems:0}` |
| 验收门 task1/task2 | **STATUS: PASSED**，`gate.errors = {}` |
| Task3 性能 | **跳过**，`perf_status=skipped_precision_gate`（精度 fail → 既有 fail-fast） |

⚠ **「验收门 PASSED」说的是证据完整、判定链自洽，不是算子过了。** 裁决本身是 `FAIL(精度)`。

### 6.4 三级门

`validate_acceptance_state` 带 `--source-facts` 显式指路复核：**STATUS: PASSED**。

⚠ 本地通路**必须显式传 `--source-facts`**：真机验收产物目录（`reports/<Op>-spec-<x>/`）里
本来就没有 `source_facts.json`，取材的 `--out` 与验收产物目录不是同一个；找不到即 BLOCKED。

### 6.5 报告渲染

`验收报告.md` 的「来源与 provenance」节按 `local_checkout` 形态如实渲染出来了，包含：

- 强度声明：**本地 checkout——只能证明「验的就是这份字节」**；
- `root_digest`；
- worktree 干净度 `clean`；
- git head，并明确标注为**信息字段、非 provenance 锚**；
- 两条 ⚠：无法证明对应任何具体 PR、摘要只覆盖 `op_subdir`。

### 6.6 ⚠ 和 §4.4 的 1152 基线**不是**一回事

`AGENTS.md` §4.4 记的 `cpp_extension` 精度基线是「1152 例中 1101 PASS、51 FAIL」，
本次是「1344 例 / 58 fail」。**数字不同是因为用的 spec 不同**（本次用
`plugin/samples/specs/median.spec.json`，其 torch_parity 矩阵规模与真机那次的 per-run spec 不同）。

两者**不是同一个 caseset**：

- 不许把本次写成「复现了基线」；
- 不许拿本次数字去改 §4.4 的基线记录。

本次的价值是**通路走通**，不是刷新精度基线。

---

## 7 · 这次证明了什么、没证明什么

**证明了**：

| # | 结论 |
|---|---|
| 1 | 「只给本地任务书文件 + 本地代码目录、不带任何 PR id」这条路能一次跑通到 `complete` |
| 2 | 两条通路对同一份代码派生出**逐字相同**的被测事实，本地 provenance 不会伪装成 PR provenance |
| 3 | 本地来源能走完 **CP-A 取材 → 构建 → 收据 → NPU 跑测 → 三级门 → 裁决 → 报告**，一路到确定性裁决 |
| 4 | `vendor_build_receipt` 的本地形态**有产出方了**，四道校验全绿，且 `local_root_digest` 与取材侧 `root_digest` 在真机上对得上 |
| 5 | 报告按 `dut_source` kind 渲染「来源与 provenance」，强度如实标注（含两条不能证明什么的 ⚠） |

**没证明**（别当已覆盖）：

| # | 仍未覆盖 |
|---|---|
| 1 | **性能维没跑到。** 本轮精度 fail → Task3 按既有 fail-fast 跳过（`skipped_precision_gate`）。「本地来源能出**性能**裁决」这件事仍未见证 |
| 2 | **精度没达标。** 本次裁决是 `FAIL(精度)`，1344 例中 58 fail。通路走通 ≠ 算子通过 |
| 3 | `root_digest` **只覆盖 `op_subdir`**，不含仓级构建脚本、公共头文件。它证明「被测算子子树的字节是这一份」，**不证明**「整个构建输入闭包是这一份」 |
| 4 | 收据对 `--library` 的绑定只到「该文件在构建窗口内被改写」（构建前后 `(mtime_ns, size, sha256)` 三项全同即 fail-closed），**不证明它由那条 argv 产出**——一次 `touch` 就能骗过 |
| 5 | `aclnn_py` + `local_checkout` 仍是**结构性 fail-closed**（`verify_aclnn_harness` 显式拒）。`aclnn_adapter` 只能按 PR ref 在容器内重新取源 build，构建端不存在可与 `local_root_digest` 对账的锚。不是排期问题 |
| 6 | 三级门的**残留伪装面**没封：`source_facts` 缺席 + 收据自称 `pull_request` 时，「`source_facts` 其实说的是 local」查不出来（没有对照物）。要封死得让编排层每次都传 `--source-facts`，让缺席本身成为非法 |
| 7 | 产出方**没接进编排**：`SKILL.md` / `plugin/AGENTS.md` 里没有一句说要产这份收据。本次是**手工调用**跑出来的，下一轮上真机的人照样可能漏 |
