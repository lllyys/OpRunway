# 本地来源通路 · 真机验证记录（2026-08-05，a3）

> 验的是 `doc/oprunway-local-source-plan.md` 的目标 1（本地来源一等通路）。
> **不是**验收裁决记录——这里只证明「取材通路打通了」，没跑 NPU、没出精度/性能结论。

---

## 1 · 见证选的是什么

| 项 | 值 |
|---|---|
| 任务书 | `cann/cann-ops-competitions` → `04_tasks/01_community-task-2026/docs/202604/median_task_doc.md` |
| 被测 PR | `cann/ops-nn` MR 6429，head `0290d61ac066f9f4e620a3714f5941e82dc4e72a` |
| 算子子目录 | `experimental/index/median` |
| 执行环境 | a3 容器 `oprunway_prov`，Python 3.12.13 |
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

## 5 · 下游门

`validate_preparation_state._validate_source_payload` 接受这份本地事实包（不抛）。

## 6 · 这次证明了什么、没证明什么

**证明了**：

- 「只给本地任务书文件 + 本地代码目录、不带任何 PR id」这条路能一次跑通到 `complete`；
- 两条通路对同一份代码派生出**逐字相同**的被测事实；
- 本地 provenance 不会伪装成 PR provenance。

**没证明**（别当已覆盖）：

- 没跑 NPU、没出精度/性能裁决——本文只到 CP-A 取材；
- `root_digest` 只覆盖 `op_subdir`，**不含仓级构建脚本/公共头文件**，
  它证明的是「被测算子子树的字节是这一份」，不是「整个构建输入闭包是这一份」；
- vendor build receipt 的本地形态由**外部构建驱动**产出，本仓只有消费方，
  真机上那份产出方还没改（见 local-source-plan Step 4）。
