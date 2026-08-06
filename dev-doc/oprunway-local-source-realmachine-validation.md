# 本地来源通路 · 真机验证记录（2026-08-05，a3）

> 🔴 **历史文档：本文记录的那套实现已于 2026-08-06 合并时被取代、代码已删。**
> 本文验的是 `dut_source.py` 判别式 + `local_checkout` / `root_digest` /
> `oprunway.local_subtree_merkle` v1 那一套。现行实现是 `source_provenance.py` 的
> `declared_source_form × provenance_kind` 两轴词表 + 快照 merkle（`AGENTS.md` §9.3）。
> **本文里的字段名、CLI 参数（`--local-repo` / `--op-subdir` / `--base-ref` / `--allow-dirty`）
> 和锚值都已不是现行口径**，⚠ 那个 `root_digest=c8867ce09f6e…` 用现行算法**复算不出来**
> （帧格式、排除集合、路径基准三处都不同），不得拿去和任何一份现行收据对账。
>
> 仍然有效的是**结论层面的事实**：那一轮确实在真机上从本地路径跑到了确定性裁决、
> 得到 1344 例 / 58 fail 与 `FAIL(精度)`（`AGENTS.md` §4.5 的 caseset ②）。
> 读本文只读这一层，别照着里面的参数去跑。

> 验的是 `dev-doc/oprunway-local-source-plan.md` 的目标 1（本地来源一等通路）。
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
| 执行环境 | a3 专用容器，Python 3.12.13、CANN 9.0.1、SoC `ascend910_93`、torch 2.10.0+cpu / torch_npu 2.10.0 |
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

### 6.6 ⚠ 和 1152 那组数**不是**一回事（口径已定案：并列记，见 `AGENTS.md` §4.5）

`cpp_extension` 通路的 Median 真机精度结果有**两组不可比的数**，本次是其中一组：

| caseset | spec 出处 | 矩阵（仓内可证部分） | 例数 | PASS | FAIL | 来源锚 |
|---|---|---|---|---|---|---|
| ①「仅按维」 | 真机当轮 per-run spec（**未入仓**） | `8 dtype × 8 rank × 3 规模 × 6 属性` | 1152 | 1101 | 51 | PR 通路（本仓记为 PR6429） |
| ②「按维 + global」← **本次** | 仓内 `plugin/samples/specs/median.spec.json` | `8 dtype × 8 rank × 3 shape × 7 attr`（1 global + 6 by-dim） | 1344 | 1286 | 58 | **本次 = `local_checkout`**，`root_digest=c8867ce09f6e…`（**不是 PR 锚**，见 §4） |

**数字不同是因为用的 spec 不同**——按设计记录（`dev-doc/oprunway-changes-brief.md`），② 相对 ① 多出来的是
`global` attr profile 共 192 条，正是任务书要求的无 `dim` 接口。

2026-08-05 定案：**两组并列记录，不挑一个当「正统」**（完整理由与引用纪律在 `AGENTS.md` §4.5）。
落到本记录上就四条：

- 不许把本次写成「复现了基线」——两者例数、失败数都不同；
- 引本次数字时**必须点名** `plugin/samples/specs/median.spec.json` **和本次的 `local_checkout` 来源身份**，
  不得简写成「Median 精度基线」，更不得写成「PR6429 的结果」（§4 已记：本地收据里的 git head 只是信息字段）；
- `1344 − 1152 = 192`、`58 − 51 = 7` 只是**算术相符**，本仓没有逐 case 对照证据，
  不得据此说「① 的 51 条在本次原样重现」；
- ⚠ 「两者**只**差 `global` 这一档、其余轴逐字段相同」**不可证**——① 的 per-run spec 未入仓，
  仓内只留了它的计数结构，没有 dtype 清单 / shape 数值 / attr 逐档内容可比。

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
| 7 | ~~产出方**没接进编排**~~ —— **本次跑测时**确实是手工调用（`SKILL.md` / `plugin/AGENTS.md` 当时没写要产这份收据）。**此后已补接**：`skills/acceptance-workflow/SKILL.md` 的 CP-C 与工件表、`plugin/AGENTS.md` 现在都要求产 `vendor_build_receipt`，产不出来即停在 CP-C。⚠ 所以这一条对**本次记录**成立、对**当前编排**已不成立，别照抄进新报告 |

---

## 8 · 追加见证：「就地跑」执行形态端到端（2026-08-06，a3）

> 本节是**另一件事**的见证，不是 §1–7 的续写：§1–7 验的是「被测**来源**」（本地 checkout vs 在线 PR），
> 本节验的是「**执行形态**」（就地跑 vs 远程连）。两者正交，本节顺手把两者叠在一起跑了一遍。
>
> ⚠ 本节裁决同样是 `FAIL(精度)`。**见证的是「就地跑能把链走通并产出裁决」，不是「算子精度达标」。**

### 8.1 见证的是哪条口径

`AGENTS.md` §5.3 与 `skills/acceptance-workflow/SKILL.md` CP-A 把执行形态写成两种平级一等：

| 形态 | 说明 | 要 `.oprunway/real-machine.env` 吗 |
|---|---|---|
| 远程连 | 开发机 → 目标机，得先 SSH 进去才够得着环境 | 要 |
| **就地跑** | **会话/进程本身就在目标机（或其容器）内** | **不要** |

「就地跑」那份环境变量清单此前是**按代码实读**写出来的，**一次都没真机跑过**。本节把它跑了。

### 8.2 怎么算「就地跑」（可观察判据）

判据不是「谁发起的」，而是**整条流水线的 argv 里出没出现 `ssh` / `docker exec`**。
做法：把一个脚本送进容器，**在容器内**跑完整条链，脚本内部不再 ssh 出去、不再 docker exec。

实测顺带坐实了一条更硬的事实：**该容器内 `ssh` / `scp` 根本不在 `PATH` 上**
（脚本里 `command -v ssh scp` 输出为空）——就是说这条链即使想走远程传输层也走不了。

| 项 | 值 |
|---|---|
| 仓 HEAD（导出用） | `739a6915c3422c9a3c72201c36ccddc44804c1a8`（`git archive HEAD plugin`，不含任何在途工作树改动）|
| plugin 包 sha256 | `f7f39b48bed9b1f41c54a34b7eea15a3c5666fcf6bd6714ee1be3e92a337d5e8`（本地算与容器内 `sha256sum` 一致）|
| 容器 / 工作根 | a3 目标容器 / `/work/run/oprw19_inplace`（新建；未碰任何已有目录）|
| 环境 | Python 3.12.13、CANN 9.0.1、SoC `ascend910_93`、torch 2.10.0+cpu / torch_npu 2.10.0 |
| 被测 | Median · ops-nn MR 6429 head `0290d61ac066f9f4e620a3714f5941e82dc4e72a`，`experimental/index/median` |
| 来源通路 | **本地 checkout**（`fetch_source.py --local-repo`，全程不带任何 PR id）|
| 本轮无 `.oprunway/real-machine.env` | 本 worktree 里就没有这个文件；按新口径不构成阻塞——**实测确实没挡住** |

被测 checkout 是从 §6 那份（`/work/run/oprw19/localsrc/ops-nn`）**整树复制**到本轮自有目录的，
只读取、不改动原目录；复制后 `git rev-parse HEAD` 与 `git status --porcelain`（0 行）都核过。

### 8.3 A/B 两跑：把三个「传输层变量」当变量做对照

| | A 跑 | B 跑 |
|---|---|---|
| `OPRUNWAY_TARGET` | `local` | **unset** |
| `OPRUNWAY_SSH_HOST` | unset | unset |
| `OPRUNWAY_REMOTE_DIR` | unset | unset |
| 其余环境 | —— | 与 A 逐字相同（复用 A 产出的 vendor `.so` 与收据）|
| 时间 | 03:31:04 → 03:35:14 | 03:39:16 → 03:40:59 |
| Task1 | 1344 例 | 1344 例 |
| Task2 | `裁决=fail` `{total:1344, fail:58, uncertain:0, risk:0, gaps:0, scaled:0, golden_blocked:0, contract_problems:0}` | **同上，逐字相同** |
| 验收门 task1/task2 | STATUS: PASSED，`gate={"passed":true,"errors":{}}` | STATUS: PASSED |
| Task3 性能 | 跳过，`perf_status=skipped_precision_gate` | 同 |
| `acceptance.json` 裁决 | `op=Median` · `repo_mode=cpp_extension` · `state=FAILED_PRECISION` · **`overall=FAIL(精度)`** | **字节级相同**（两份 `acceptance.json` sha256 都是 `01d06e0ddafceab8…`）|
| `verdict.json`（逐 case）| 1344 条 | **字节级相同**（sha256 都是 `2aa3c4685b5b97ab…`；另用脚本核过 case id 集合与逐 case 裁决 map 全等）|
| 三级门（带 `--source-facts`）| task1 / task2 → **STATUS: PASSED** | task1 / task2 → **STATUS: PASSED** |

⚠ **A/B 能证到哪、证不到哪，分清楚**（这条是审修门逼出来的，别再写含糊）：

| 命题 | 证据类型 |
|---|---|
| 不给 `OPRUNWAY_SSH_HOST` / `OPRUNWAY_REMOTE_DIR`，`cpp_extension` 照样一路跑到裁决 | **实测**（A、B 两跑这两个变量都 unset，都出了 `acceptance.json`）→ SKILL.md 原文「`REMOTE_DIR` 仍要给」被**证伪** |
| `OPRUNWAY_TARGET` 取 `local` 与不设，对裁决**无可观察影响** | **实测**（A/B 唯一变量就是它，两边 `acceptance.json`、`verdict.json` 字节级相同）|
| `cpp_extension` 主链**根本不读**这三个变量 | **读码**：三条 runner form 的 Python 主链里读它们的只有 `repo_adapter._ne_cfg` 与 `aclnn_adapter._aclnn_cfg`；`run_workflow` 只在 `mode == "new_example"` 时调 `_ne_cfg`，`cpp_extension` 分支两个都不碰。A/B **不能单独证明这一条**（“读了但不影响结果”与“没读”观测上同形）|

⚠ 上面那句「只有 `_ne_cfg` / `_aclnn_cfg`」限定在**三条 runner form 的 Python 主链**内。
仓里还有别的读者——`acc-common/catlass/run_on_catlass_npu.sh` 就把 `OPRUNWAY_REMOTE_DIR` 当必填并做强校验——
但 catlass 不是从 `runner_form` 派生的通路（`AGENTS.md` §4），与本节结论不冲突。别把这句读成「全仓唯一读者」。

⚠ 这三个变量对 `cpp_extension` 的无关性**与执行形态无关**：就地跑、远程连都一样不读。
`cpp_extension` 真要跨机/跨容器执行，唯一入口是把前缀写进
`OPRUNWAY_CPP_EXTENSION_DRIVER_JSON` 的 argv，**设了 `OPRUNWAY_SSH_HOST` 也不会让 driver 连出去**。

同一进程内的对照探针（同样 env 下直接调那两个配置函数）：

```
repo_adapter._ne_cfg (new_example):    RAISES -> ValueError: 缺 OPRUNWAY_REMOTE_DIR（远端（或本机）工作根目录）……
repo_adapter._ne_cfg with TARGET=local: RAISES -> 缺 OPRUNWAY_REMOTE_DIR（同一条）
aclnn_adapter._aclnn_cfg (aclnn_py):   RAISES -> ValueError: 缺 OPRUNWAY_ACLNN_OP_SUBDIR……
```

⚠ 第三行是在 `OPRUNWAY_ACLNN_OP_SUBDIR` 上先失败的，**没走到** `OPRUNWAY_REMOTE_DIR` 那一项——
所以「`aclnn_py` 也要 `OPRUNWAY_REMOTE_DIR`」这句是**读码**得出的（`aclnn_adapter.py:338` 的
`_req("OPRUNWAY_REMOTE_DIR", …)`），不是本轮实测。别把它写成实测。

### 8.4 构建与收据（就地跑，本地来源）

`make_vendor_build_receipt.py` 真跑 build+install 后落收据，`status=VERIFIED`：

```json
"source": {
  "repo": "https://gitcode.com/cann/ops-nn.git",
  "repo_source": "local_checkout.git.remote_url",
  "local_root_digest": "c8867ce09f6e527244c3f8a809101eaf40eb0ed50ec1b58b554a51f919fdd15e",
  "dut_source": "local_checkout"
},
"build": { "cwd": "/work/run/oprw19_inplace/localsrc/ops-nn", "returncode": 0 },
"artifact": { "library_sha256": "da847fa13c29c1da7be215064dc2705bc52fe932dad4298b9bfcff72432dd630",
              "existed_before_build": false }
```

`local_root_digest` 与本轮取材侧的 `root_digest` 同值（`c8867ce09f6e…`，也与 §4 那次相同），三级门的等值校验对上了。

⚠ **`.so` 的 sha256 与 §6.1 那次（`35ba85e0d719…`）不同，但 `root_digest` 完全相同。**
这**不是**异常，恰恰是 §7 第 3 条那句「`root_digest` 相同不等于 vendor `.so` 相同」在现场的实例
（`验收报告.md` 的 provenance 节也逐字写着这句）。原因**推断**为构建绝对路径不同、构建非位级可复现
（本轮 cwd 是 `/work/run/oprw19_inplace/localsrc/ops-nn`，§6 那次是 `/work/run/oprw19/localsrc/ops-nn`）——
**未做进一步取证**。

⚠ 与此同时，**本轮 A/B 两跑的精度结果字节级相同**（`verdict.json` sha256 同为 `2aa3c468…`）——
但那两跑用的是**同一份 `.so`**，说明不了 ELF 变化。至于与 **§6.3 那次**（不同 `.so`）的关系：
**只核到汇总计数相同（1344 例 / 58 fail），没做逐 case 对照**——§6 那轮的 `verdict.json` 未取来比。
所以只能说「汇总计数一致」，**不能**据此声称「ELF 字节不同、DUT 行为一致」。
另外**不许把这写成「复现了 1152 那组数」**——那仍是另一个 caseset（§6.6 的并列记录纪律原样成立）。

### 8.5 交付物：文档口径 vs 实际所需，逐项对账

对每个变量单独 unset 一次、其余不变、跑完整 `run_workflow`（8 次探针，03:41:51 → 03:47:04）：

⚠ 表里「证据」一栏区分 **实测** 与 **读码**，别混着引。

| 变量 | SKILL.md CP-A 就地跑段原先怎么写 | 结论 | 证据 |
|---|---|---|---|
| `OPRUNWAY_TARGET` | 「设 `=local`」 | 对 `cpp_extension` **无效配置**（设不设都一样）| **实测**：A/B 唯一变量，两边产物字节级相同。「不读」本身是**读码**（唯一读者是 `_ne_cfg`/`_aclnn_cfg`）|
| `OPRUNWAY_SSH_HOST` | 「免填」 | 对 `cpp_extension` **无效配置**（结论对，但理由不是「local 模式免填」而是压根没这条通路）| **实测**：两跑都 unset 且都出裁决。「不读」是**读码** |
| `OPRUNWAY_REMOTE_DIR` | 「**仍要给**」 | ❌ **对 `cpp_extension` 是错的** | **实测证伪**：两跑都 unset，都一路跑到 `acceptance.json` |
| `OPRUNWAY_CPP_EXTENSION_DRIVER_JSON` | 有写（「argv 不带 ssh/docker exec 前缀」）| ✅ 必需 | unset → `缺 OPRUNWAY_CPP_EXTENSION_DRIVER_JSON；cpp_extension 不猜 SSH/container 入口`，无 `acceptance.json` |
| `OPRUNWAY_CPP_EXTENSION_REAL=1` | **该段没写** | ✅ 必需 | unset → `真机路径未启用；须显式设 OPRUNWAY_CPP_EXTENSION_REAL=1` |
| `OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY` | **该段没写** | ✅ 必需 | unset → driver rc=1，无 `acceptance.json` |
| `OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT` | **该段没写** | ✅ 必需 | unset → driver rc=1，无 `acceptance.json` |
| `OPRUNWAY_SOC` | **该段没写**（只在「其余变量」里一笔带过）| ✅ 必需 | unset → 跑完 1344 例后 driver rc=1（driver 的 runtime provenance fail-closed），无 `acceptance.json` |
| `ASCEND_TOOLKIT_VERSION`（或 `CANN_VERSION`）| **该段没写** | ✅ 必需 | 同上 |
| golden 根（默认 `<CWD>/.oprunway/ops`，或 `OPRUNWAY_OPS_DIR`）| **该段没写** | ✅ 必需 | unset `OPRUNWAY_OPS_DIR` → `缺 golden: …/.oprunway/ops/Median/golden.py（引擎不回退内置 golden，fail-closed）` |
| `OPRUNWAY_CPP_EXTENSION_DEVICE` | **该段没写** | ⚠ **本轮没见证到它必需** | unset 后**照样**产出 `acceptance.json`（`overall=FAIL(精度)`）。读码：`cpp_extension_adapter.py:213` 在精度未全过时**提前返回**，根本没读到这一行；精度全过且有性能 case 时它没有默认值、缺了 fail-closed。**精度过了的场景本轮未覆盖** |

**结论（就是本 task 的交付物）**：那份「就地跑清单」不是写错了，是**写串了作用域**——
`OPRUNWAY_TARGET` / `OPRUNWAY_SSH_HOST` / `OPRUNWAY_REMOTE_DIR` 只被
`repo_adapter._ne_cfg`（`new_example`）与 `aclnn_adapter._aclnn_cfg`（`aclnn_py`）读，
而这两条通路**当前都产不出验收裁决**（`AGENTS.md` §4）。
按原文照做去跑一场**正式验收**，会同时踩两脚：给了一个不起作用的 `OPRUNWAY_REMOTE_DIR`，
又漏掉 6 个真正 fail-closed 的必需项。

已据此改 `plugin/skills/acceptance-workflow/SKILL.md` 的 CP-A 就地跑段（**只改那一段**）：
按 runner form 把清单拆开，并把上表 6 项必需变量补进 `cpp_extension` 那条。

### 8.6 这次证明了什么、没证明什么

**证明了**：

| # | 结论 |
|---|---|
| 1 | 「就地跑」是能真跑通的一等形态：整条链在容器内执行、argv 无 `ssh` / `docker exec`，一路到 `acceptance.json` |
| 2 | 缺 `.oprunway/real-machine.env` **确实不构成阻塞**（本 worktree 根本没有这个文件，链照跑）|
| 3 | **实测**：不给 `OPRUNWAY_SSH_HOST` / `OPRUNWAY_REMOTE_DIR`，`cpp_extension` 照样出裁决；`OPRUNWAY_TARGET` 设与不设，产物字节级相同。（「主链不读」这句更强的断言是**读码**结论，见 §8.3）|
| 4 | 就地跑 + 本地来源可以叠着走：`dut_source=local_checkout`、`root_digest` 三处（取材 / 收据 / 三级门）对得上 |
| 5 | 6 个真正必需的环境变量逐个 unset 都**fail-closed**（无一例静默降级、无一例产出假 `acceptance.json`）|

**没证明**：

| # | 仍未覆盖 |
|---|---|
| 1 | **性能维仍然没跑到**（精度 fail → Task3 `skipped_precision_gate`）。§7 第 1 条原样成立 |
| 2 | **精度没达标**：`FAIL(精度)`，1344 例 58 fail。走通 ≠ 通过 |
| 3 | `OPRUNWAY_CPP_EXTENSION_DEVICE` 的必需性只到**读码**（精度全过的场景本轮没出现）|
| 4 | 「`aclnn_py` 需要 `OPRUNWAY_REMOTE_DIR`」也只到**读码**（探针先在 `OPRUNWAY_ACLNN_OP_SUBDIR` 上失败）|
| 5 | `OPRUNWAY_PLUGIN_ROOT` 没进探针：本轮 `run_workflow.py` 是按**绝对路径**调的，该变量服务的是 Layer 2 薄壳 |
| 6 | 「远程连」形态本轮**没跑**，只跑了就地跑；两者的对照结论仅限于上表列出的变量 |
| 7 | 本轮**没走完整 CP-A..E 编排**（没派 NL subagent、没做对应校验/任务书校验门），只跑确定性脚本链。见证的是执行形态与环境变量，不是编排层 |
| 8 | **证据本身没进仓**：产物留在 a3 目标容器的 `/work/run/oprw19_inplace/` 下（`reports/` 是 ignored、日志几十 MB）。下面的 §8.7 只是**索引 + 摘要**；现场一旦清掉，这些数字就只剩本文的叙述、无法再复核 |

### 8.7 证据索引（可复核锚点）

全部落在 a3 目标容器 `/work/run/oprw19_inplace/`（新建目录，未碰任何已有现场）。
⚠ **这些文件不在 Git 里**（`reports/` 按 §5.4 是 ignored），下表的 sha256 是**当时实测**、供事后比对；
现场被清后无法复核，别把本表当成永久证据。

| 文件（相对 `/work/run/oprw19_inplace/`）| sha256 | 是什么 |
|---|---|---|
| `reports/Median-inplace/acceptance.json` | `01d06e0ddafceab8ee24d4fd550b5d733876eee75a4db74b5c781634e6f09833` | A 跑最终裁决 |
| `reports/Median-inplace-notarget/acceptance.json` | `01d06e0ddafceab8ee24d4fd550b5d733876eee75a4db74b5c781634e6f09833` | B 跑最终裁决（**与 A 同值**）|
| `reports/Median-inplace/verdict.json` | `2aa3c4685b5b97abf6fede57b4e8034b93e3507647127e34828d17b93f89a1e0` | A 跑逐 case 精度裁决（1344 条）|
| `reports/Median-inplace-notarget/verdict.json` | `2aa3c4685b5b97abf6fede57b4e8034b93e3507647127e34828d17b93f89a1e0` | B 跑逐 case（**与 A 同值**）|
| `out_inplace/source_facts.json` | `3dbdb1a8256ad566a73bef18a0d1986e7705a9bd8c7240837ff56e87a0c59704` | CP-A 取材事实包（`dut_source=local_checkout`）|
| `reports/Median-inplace/source_facts.json` | `3dbdb1a8256ad566a73bef18a0d1986e7705a9bd8c7240837ff56e87a0c59704` | 三级门用的那份对照物（**与取材侧同值**，就是拷进去的）|
| `vendor_real/vendor-build-receipt.json` | `0e9fdccd30500d6dc903b1daa3fad8202862258cd8689a04b043e263ba8f16fc` | vendor build 收据（`status=VERIFIED`）|
| `inplace.log` | `9a2e712a1bcef2a2228a712420fdcce4811f747626a15deafe9c6e9e7b663294` | A 跑全量日志（含 build、env dump、门输出）|
| `probe.log` | `8dd4cc73afdd90d3f5f9d214fc512ce9a5203bba995a4bb530f03ea8a6219ff9` | §8.5 那 8 次逐变量 unset 探针的全量日志 |
| `run_inplace.sh` / `run_inplace2.sh` / `probe_env.sh` | —— | A 跑 / B 跑 / 探针的脚本原件（可复跑）|

导出用的 plugin 包：`git archive HEAD plugin` → sha256 `f7f39b48bed9b1f41c54a34b7eea15a3c5666fcf6bd6714ee1be3e92a337d5e8`
（HEAD = `739a6915c3422c9a3c72201c36ccddc44804c1a8`；本地算与容器内 `sha256sum` 一致，
所以真机跑的确实是这个 commit 的 plugin、不含任何在途工作树改动）。
