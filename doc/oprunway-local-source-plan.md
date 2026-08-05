# 实施方案 · 本地来源通路 + `runner_form` 收敛到 `cpp_extension`

**日期**：2026-08-05
**优先级**：P0，优先实施

**两个目标合并成一份方案，因为它们在实现上强耦合**：

1. **本地来源**：让「任务书给本地路径 + 被测代码仓给本地目录」代替「在线 gitcode PR 链接」，
   成为一等公民通路
2. **通路收敛**：`runner_form` 正式验收只允许 `cpp_extension`，`cpp` / `aclnn_py` 暂时堵死

**为什么必须一起做**：`cpp_extension` 通路有一处 `aclnn_py` 通路**没有**的 PR head 硬校
（`vendor_build_receipt`，见 §1.3 表格第 3-5 行）。只做目标 1 而不看目标 2，
会漏掉三个消费者，其中一个在**三级验收门**里——改完照样跑不通。

**背景**：`doc/oprunway-roll-complex64-trial-findings.md` §1.3 / §2 B1 / §2 B2 / §6 目标 2 / §6 目标 8
**范围**：只做来源接入 + 通路收敛。**不含** spec 来源门（D5）、complex64 能力扩展（A3）、
执行方向确认单（目标 5/6/7）。

---

> **📍 交接入口：`doc/oprunway-session-handoff-2026-08-05.md`**（实施顺序、已知坑、待办都在那）

---

## 1 · 问题定位

### 1.1 任务书已支持，代码仓不支持

| 项 | 状态 | 依据 |
|---|---|---|
| 任务书本地路径 | ✅ 已支持 | `fetch_source.py:552`：`--taskdoc` 明写「本地路径 或 http(s) 链接」 |
| 代码仓本地路径 | ❌ 不支持 | `fetch_source.py:553`：只有 `--pr`（gitcode PR 链接） |

### 1.2 阻塞链（只给 `--taskdoc`、不给 `--pr`）

```
fetch_source.py
  ├─ 产 task_doc.md + task_doc.snapshot.md          ✅
  ├─ 不产 pr_facts.json                              ❌ :13 docstring「给了 --pr 才有」
  └─ 不产 source_facts.json                          ❌ :15 同上；写入在 :589 的 --pr 分支内
        ↓ 后面每一道门都以它为前置，全部 BLOCKED
```

`completeness.status` 只在 `reasons` 为空时才是 `complete`（`fetch_source.py:495-497`），
而 `:442-459` 累加的 reasons **全部是 PR 专属字段**：
`missing_or_invalid_head_sha` / `missing_pr_url` / `missing_source_repo` /
`missing_head_repo` / `unknown_fork_status` / `missing_pr_state` /
`missing_changed_files` / `missing_key_files`。

**手工补也补不齐**——这就是 Roll 那次第 722/739/745 行三轮尝试全挂的原因。

### 1.3 ⚠ `pr_head_sha` 的硬校点比想象中多：**9 处，横跨 6 个模块**

**实施前必须全部盘到**，漏一处就白改：

| # | 位置 | 作用 | 属哪条通路 |
|---|---|---|---|
| 1 | `preflight_aclnn.py:118` | 写 `bindings.pr_head_sha` | `aclnn_py` |
| 2 | `preflight_aclnn.py:134-136` | 校 `pr_facts.head_sha == source_facts.pr.head_sha` | `aclnn_py` |
| 3 | `verify_aclnn_harness.py:401 / 452 / 575` | 从 preflight bindings 读取并交叉核 | `aclnn_py` |
| 4 | `cpp_extension_adapter.py:284-289` | 校 `vendor_build_receipt.source.pr_head_sha` 为 40 位 hex | **`cpp_extension`** |
| 5 | `cpp_extension_driver.py:119-124` | 同上，driver 侧独立再校一遍 | **`cpp_extension`** |
| 6 | `validate_acceptance_state.py:788` + `:794-796` | **三级验收门**里再校一次 vendor build receipt（`head` 在 788 取值，长度/hex 校验在 794-796） | **`cpp_extension`** |
| 7 | `render_acceptance_markdown.py:206` | 「被测物与运行环境」表里渲染 `\| PR head \| ... \|`；`source` 取自 **vendor build receipt**（同表还有 vendor ELF / Extension ELF SHA256） | **`cpp_extension`**（订正：初稿写「两条都用」是错的） |
| 8 | `precision_retest_contract.py:358 / 416 / 432` | CP-F 精度复测：`base_pr_head` ← `source.pr_head_sha`；再组 `base_provenance.head_sha` 与 `expected_provenance.head_sha` 做漂移比对 | **`cpp_extension`**（依据：`:265-277` 的 `_cpp_extension_base_binding` 只认 `oprunway.cpp_extension_receipt`） |
| 9 | `precision_retest_runner.py:247` | CP-F 运行期 `actual.pr_head` ← `source.pr_head_sha` | **`cpp_extension`** |

⚠ 第 8/9 是 **CP-F 验收后精度复测**通路。它不在主验收链上，但一旦本地来源验收跑通、
后续要复测，这里会二次 BLOCKED。**不修的话，本地来源做完只是「能验收一次」，不能复测。**
（若本批决定先不做，也要在 §4 显式挂账，别让人以为已覆盖。）

第 4/5/6 校的是 **`vendor_build_receipt`**（schema `oprunway.vendor_build_receipt` v1，
`status` 须 `VERIFIED`），由**外部构建驱动产出**、不是本仓脚本生成——
所以本地来源的支持必须延伸到这份收据的契约（§2.8）。

⚠ 现在的错误信息是「vendor build receipt 缺完整 **PR head**/source repo」，
本地 checkout 天然给不出 PR head。

**不受影响、不要动的**（已逐个核实）：

| 模块 | 为什么不受影响 |
|---|---|
| `validate_taskdoc_input.py` | 只用 `source_facts.taskdoc.bytes_sha256` 与 envelope digest，**与来源无关** |
| `aclnn_adapter.py` | 只在注释里提 `pr_facts`；`op_subdir` 等值实际经 `OPRUNWAY_ACLNN_*` env 传入 |
| `gen_cases.py:3186-3190` | 只校 `correspondence.status == confirmed` 且 `source_facts_digest == 当前 digest`，**不读 pr 字段** |
| `repo_adapter.py:1078` | 注释提及，无实际读取 |

### 1.4 目标 2（通路收敛）的现状

`run_workflow.py:43-50`：

```python
_REAL_MACHINE_MODES = frozenset({"new_example", "aclnn_py", "cpp_extension"})
_RUNNER_FORM_TO_MODE = {
    "cpp": "new_example",
    "aclnn_py": "aclnn_py",
    "cpp_extension": "cpp_extension",
}
```

三条都是 `AGENTS.md` §4 认定的真机验收通路，且都能产 `acceptance.json`。

**支持收敛的证据**（真机成熟度确实不齐，`AGENTS.md` §9）：

| 通路 | 坐实情况 |
|---|---|
| `cpp_extension` | Median PR6429 **1152 例**完整 torch-parity 矩阵，`gate.passed=true` ← **唯一跑通完整验收的** |
| `aclnn_py` | 仓规原话：「历史 Median 60/60 来自 aclnn_py，**只证明旧 caseset**；迁移到 torch_parity + cpp_extension 后必须重跑，不得沿用旧 PASS」 |
| `cpp`（`new_example`） | IsClose / Sign 坐实，但 dtype 闭环只到 fp32/fp16/bf16，int 落 `DEFERRED_NP_BY_FORM` |

### 1.5 实证：这道坎决定成败

| 会话 | 用户给的 | 结果 |
|---|---|---|
| Median `189d72da`（08-03） | 本地目录 | 手工用 `content_address` 构造完整 `pr_facts.json` → 打通，跑完 56 例 |
| Roll `53dc004f`（08-05） | 本地目录 | 手工补三轮补不齐 `completeness` → 放弃，Task2 未启动 |

**能不能过取决于 agent 当场的耐心**——这本身就是缺陷。

---

## 2 · 设计

### 2.1 核心决策：判别式 + 分支必填集，**不让本地伪装成 PR**

`source_facts.payload` 增加判别式，**新开平级键**存本地事实，
而不是把本地数据塞进 `payload.pr`：

```jsonc
{
  "digest": "...",
  "domain": "oprunway/source-facts/v1",     // 不变
  "schema_version": 1,                       // 不变（见 §2.2）
  "payload": {
    "dut_source": "local_checkout",          // 新增判别式；缺省 = "pull_request"
    "taskdoc": { ... },                      // 不变
    "pr": { ... },                           // PR 通路才有；本地通路整键缺席
    "local_checkout": {                      // 本地通路才有；PR 通路整键缺席
      "root_digest": "<被测子树 Merkle sha256>",
      "op_subdir": "experimental/math/roll",
      "digest_excludes": [".git", "__pycache__", "*.pyc", "build", "build_out"],
      "git": {                               // 可选：是 git 仓时才有
        "head_sha": "…40 位…",
        "remote_url": "https://gitcode.com/xxx/ops-math.git",   // 可为 null
        "base_ref": "master",
        "dirty": false,
        "dirty_files": []
      }
    },
    "changed_files": [...] | "unavailable",  // 见 §2.4
    "key_files": [...],                      // 不变，两条通路共用
    "derived": { ... },                      // 不变
    "completeness": { "status": ..., "reasons": [...] },
    "producer": { ... }
  }
}
```

**为什么不复用 `payload.pr`**：一个叫 `pr` 的字段里装本地 checkout 数据，
就是「本地 provenance 伪装成 PR provenance」——下游、报告、人读收据时都会误判。
新键 + 判别式让「这份 DUT 事实从哪来」永远显式。

### 2.2 兼容性：字段不加，但 **digest 一定会变**（初稿在此论证错误，已推翻）

⚠ **不要 bump `schema_version`，不要改 `domain`。**

- PR 通路 payload 的**业务字段**一个都不加（`dut_source` 缺席，
  读侧 `payload.get("dut_source", "pull_request")` 兜底）
- 只有本地通路的新收据才带 `dut_source` + `local_checkout`

**⚠ 但「digest 不变」不成立。** `fetch_source.py:461`：

```python
logic_sha = hashlib.sha256(src.read()).hexdigest()      # ← fetch_source.py 自身源码的 sha256
...
"producer": {"tool": "fetch_source.py", "logic_sha256": logic_sha},   # :499，在 payload 内
```

`producer.logic_sha256` 是**工具自身源码的哈希**，且它在 payload 里 → 在 digest 里。
**只要改了 `fetch_source.py` 一个字节，PR 通路的 `source_facts.json` digest 就会变。**
这是**有意设计**（provenance：这份事实是哪版工具产的），不是 bug。

**因此安全绳要换个写法**：

| 初稿（错） | 订正 |
|---|---|
| Step 7.14 断言 PR 通路 digest 与改动前**完全相同** | 断言 **payload 去掉 `producer` 之后**与改动前逐字节相同；`producer.logic_sha256` 允许且应当变化 |

**连带后果（必须提前告知，否则会被当成 bug）**：

- 改完之后，**所有既有的 preparation 收据会从 `REUSABLE` 变成 `MISS`**
  （`validate_preparation_state` 靠 digest 判复用）→ 下一轮要重新准备一次
- 这是正确行为（工具逻辑变了，旧收据不该继续复用），但要在
  `doc/oprunway-changes-brief.md` 里写明，别让人以为复用坏了

**实施时不要图省事把 `dut_source` 设成无条件必填**——那样 PR 通路的业务字段也会变，
就分不清「digest 变是因为工具改了」还是「因为业务语义改了」。

### 2.3 `root_digest`：本地通路的 provenance 锚

**必填**，替代 `head_sha` 的锚定作用。即使目录不是 git 仓也一定有。

算法（实现时逐字照此，否则跨机不可复现）：

```
遍历 <repo_root>/<op_subdir>，收集三类条目（不越出 op_subdir）：
  · 常规文件  → kind = b"f", payload = 文件字节
  · 符号链接  → kind = b"l", payload = os.readlink() 的目标（不跟随）
  · 空目录    → kind = b"d", payload = b""        ← 必须计入，否则「删掉目录里最后一个文件」digest 不变

排序：按 os.fsencode(rel_path) 的**字节序**升序（不是 str 序，避免 Unicode 规范化与非 UTF-8 文件名问题）

逐条按**长度分帧**拼接（不用分隔符，避免路径里含 \0 或 \n 时的歧义）：
  frame = kind
        + len(path_bytes).to_bytes(8,"big")   + path_bytes
        + len(payload_digest).to_bytes(8,"big") + sha256(payload).digest()

root_digest = sha256(所有 frame 顺序拼接).hexdigest()

排除（按相对路径的**首段或后缀**匹配）：.git/  __pycache__/  *.pyc  build/  build_out/
```

⚠ 三个坑，实现时逐条对照：

| 坑 | 后果 | 本算法的处理 |
|---|---|---|
| 空目录被忽略 | 删掉目录里最后一个文件 → digest 不变 | 空目录以 `kind=b"d"` 计入 |
| 符号链接与同内容常规文件碰撞 | 把文件换成指向别处的软链 → digest 不变 | `kind` 前缀区分，`b"f"` vs `b"l"` |
| 非 UTF-8 / 不同 Unicode 规范化的文件名 | macOS(NFD) 与 Linux(NFC) 上算出不同值 | 用 `os.fsencode` 的原始字节，不做 str 编码 |

排除清单**必须写进收据**（`local_checkout.digest_excludes`）——
否则换个排除规则算出的 digest 不可比，而外表看不出来。

### 2.4 `changed_files` 缺 base 时标 `"unavailable"`，**不用空数组**

- 给了 `--base-ref` 且是 git 仓 → `git diff --name-only <base>...HEAD`
- 否则 → 字符串 `"unavailable"`

**绝不能填 `[]`**——空数组语义是「没有改动」，会让下游以为 PR 什么都没改。

### 2.5 dirty worktree：默认 fail-closed，逃生阀显式记账

| 情形 | 行为 |
|---|---|
| 非 git 仓 | 允许，`git` 键缺席，只靠 `root_digest` |
| git 仓 + clean | 允许，`git.dirty = false` |
| git 仓 + dirty，无 `--allow-dirty` | **BLOCKED**，错误列出 dirty 文件 |
| git 仓 + dirty，有 `--allow-dirty` | 允许，`git.dirty = true` + `dirty_files` 全量清单进收据 |

理由：dirty 时 `head_sha` 与实际被测字节不符，provenance 就是假的。
但开发期完全禁止工具没法用，所以给显式逃生阀 + 强制记账。
`dirty = true` 时报告**必须**标注（§3 Step 5）。

### 2.6 `completeness` 按 `dut_source` 分支 —— 且必须拆出 `warnings`

**⚠ 初稿这里自相矛盾，已订正。** 现有实现（`fetch_source.py:496`）：

```python
"status": "complete" if not reasons else "blocked",
```

**`reasons` 非空 = blocked**，没有第三态。所以初稿写的
「`changed_files_unavailable` 放 reasons 但不阻塞」在现有结构下**不可能成立**。

**订正：`completeness` 增加一个平级的 `warnings` 数组**，只有 `reasons` 参与 status 判定：

```jsonc
"completeness": {
  "status": "complete" | "blocked",       // 仍只看 reasons
  "reasons":  [...],                      // 阻塞项
  "warnings": [...]                       // 非阻塞项，新增；PR 通路恒为 []
}
```

⚠ 这会改 PR 通路的 payload 结构（多一个恒为 `[]` 的键）。两个选择：

- **(A) 推荐**：`warnings` 只在**非空时**才写入 → PR 通路 payload 业务字段不变
- (B) 无条件写 `"warnings": []` → 结构更整齐，但 PR 通路业务字段也变了，
  与 §2.2 想保持的「业务字段不变」冲突

**分支表**：

| 项 | pull_request | local_checkout | 落 reasons 还是 warnings |
|---|---|---|---|
| `facts["blocked"]`（动态，取自取材失败）| 阻塞 | 阻塞 | **reasons**（两条通路都要保留，初稿漏了这条） |
| `missing_or_invalid_head_sha` | 阻塞 | 不适用 | reasons |
| `missing_pr_url` / `missing_source_repo` / `missing_head_repo` / `unknown_fork_status` / `missing_pr_state` | 阻塞 | 不适用 | reasons |
| `missing_changed_files` | 阻塞 | 不适用 | reasons |
| `changed_files_unavailable` | 不适用 | **不阻塞** | **warnings** |
| `missing_key_files` | 阻塞 | 阻塞 | reasons（两条通路都靠它做 slot 对账） |
| `missing_root_digest` | 不适用 | **阻塞** | reasons（新增） |
| `dirty_worktree_not_allowed` | 不适用 | **阻塞** | reasons（新增，无 `--allow-dirty` 时） |

⚠ 下游消费者若有「reasons 为空即万事大吉」的假设，要同步改成也看 `warnings`
（至少在报告里展示），否则 `changed_files_unavailable` 会静默消失。

### 2.7 `pr_facts.json` 的本地对应物

`preflight_aclnn.py` 同时读 `pr_facts.json`（`:118` / `:138`）。
本地通路也产同名文件（**文件名不变**，减少消费者改动），内部同样用 `dut_source` 判别：

```jsonc
{
  "dut_source": "local_checkout",
  "op": "Roll",
  "target_dir": "experimental/math/roll",
  "local_checkout": { "root_digest": "...", "git": {...} },
  "changed_files": [...] | "unavailable",
  "key_files": { "<path>": "<文件正文>" },   // 与 PR 通路同形，供 header 签名解析
  "aclnn_headers": [...],
  "interface_kind": "...", "aclnn_entry": "...",
  "notes": [...]
}
```

`key_files` / `aclnn_headers` / `interface_kind` / `aclnn_entry` 的探测逻辑
**完全复用现有实现**（`_detect_interface_kind` 等），只是内容从本地目录读而非 API 取。

### 2.8 ⚠ `vendor_build_receipt` 的本地来源（**cpp_extension 专属，最容易漏**）

`cpp_extension` 通路额外依赖 `vendor_build_receipt`（`schema: oprunway.vendor_build_receipt` v1），
三处硬校 `source.pr_head_sha` 为 40 位 hex（§1.3 的 #4/#5/#6）。

**这份收据由外部构建驱动产出，不是本仓脚本生成**，所以要同步扩它的契约：

```jsonc
"source": {
  "dut_source": "local_checkout",       // 新增判别式，缺省 "pull_request"
  "repo": "<必填，两条通路都要>",
  "pr_head_sha": "…40 位…",             // pull_request 时必填
  "local_root_digest": "…64 位…"        // local_checkout 时必填（= source_facts 里的同名值）
}
```

三处校验改为按 `dut_source` 分支，**但必须先做一致性前置校验**：

```
第 0 步（前置，缺了整套设计被绕过）：
  receipt.source.dut_source  ==  source_facts.payload.dut_source
  不等 → BLOCKED

第 1 步（按 dut_source 分支）：
  pull_request  → 现有逻辑一行不改
  local_checkout → 校 local_root_digest 为 64 位 hex
                 且 == source_facts.payload.local_checkout.root_digest
```

⚠ **为什么第 0 步不能省**（codex 审出的绕过路径）：
若不强制两边 `dut_source` 一致，攻击/误用路径是——
`source_facts` 声明 `local_checkout`，而 `vendor_build_receipt` 声明 `pull_request`
并填一个**任意 40 位 hex** 当 `pr_head_sha` → 走进 PR 分支 → `local_root_digest`
那条等值校验**根本不会执行** → vendor `.so` 与被测源码的绑定完全失效。

⚠ **等值校验是本地通路的信任基石**——它替代了「build 产物对应哪个 PR head」的绑定。
少了它，vendor `.so` 与被测源码就失去了机器可核的对应关系。

### 2.9 通路收敛：白名单门（目标 2）

**改哪里**：`run_workflow.py`，`_RUNNER_FORM_TO_MODE` 附近新增：

```python
# 正式验收当前只走 cpp_extension：它是唯一跑通完整 torch_parity 矩阵的通路
# （AGENTS.md §9：Median PR6429 1152 例、gate.passed=true）。
# cpp / aclnn_py 的真机成熟度未达同等水平，暂不用于出验收裁决。
_ACCEPTANCE_RUNNER_FORMS = frozenset({"cpp_extension"})
```

**堵法（选 b，不选 a/c）**：

| 方案 | 评价 |
|---|---|
| (a) 直接删 `_RUNNER_FORM_TO_MODE` 两项 | ❌ 报错变成 KeyError，不说人话 |
| **(b) 保留映射 + 显式白名单门** | ✅ **采用**——错误信息能讲清「为什么堵、怎么绕」 |
| (c) 只在 skill / agent 层写规则 | ❌ 这次的教训就是纪律拦不住（findings §2 C1） |

**错误信息要求**（逐字建议）：

```
runner_form='aclnn_py' 当前不用于正式验收。
原因：只有 cpp_extension 跑通过完整 torch_parity 矩阵（Median PR6429 1152 例），
      cpp / aclnn_py 的真机成熟度未达同等水平（见 AGENTS.md §9）。
如需局部开发验证：加 --allow-experimental-form，该模式下不产 acceptance.json / verdict.json，
只产带 evidence_grade="development" 的非验收产物。
```

**逃生阀**：`--allow-experimental-form`，比照 mock 通路——
该通路**不产 `acceptance.json` / `verdict.json`**，只产带
`evidence_grade="development"` + NON-ACCEPTANCE 标记的产物。
完全删掉会让 `aclnn_py` 的现有能力无法回归验证，将来想恢复要从头考证。

⚠ **门必须落在两处，只拦入口会被绕过**（codex 审出）：

| 位置 | 拦什么 |
|---|---|
| ① `run_workflow` 入口 | 正常调用路径 |
| ② **写 `acceptance.json` 之前** | 绕过入口的路径——直接 `--mode aclnn_py`、或绕开 `run_workflow` 直接调 `repo_adapter` 子脚本 |

②不是多余的：仓里已有先例——`repo_adapter.py` 的注释明写
`MODES` 含 `catlass_mock` 时，「`repo_adapter.py cs wd acceptance.json catlass_mock`
是绕开 catlass CLI 那两道守卫的现成后门」，所以那边**也是在出口再校一次**。
本门照抄这个口径：**最终产物写门统一校验 `runner_form ∈ _ACCEPTANCE_RUNNER_FORMS`**。

⚠ **`repo_adapter.SUPPORTED_NP_BY_FORM` / `DEFERRED_NP_BY_FORM` 的 `aclnn_py` 条目保留不动**——
它们是**能力表**，不是准入表。删了将来恢复要重新考证 dtype 支持面。

### 2.10 ⚠ 仓规同步（不可省）

`AGENTS.md` §4 现在写着「三条都是真机验收通路、都能产验收裁决」。
**堵死两条 = 改仓规**，只改代码不改文档，下一个 session 读 `AGENTS.md`
仍会以为 `aclnn_py` 可用，撞上门再来问为什么。

改法：§4 的表**加一列「当前是否用于正式验收」**，并写清理由（真机成熟度），
而不是只写「禁用」。§9「当前能力边界」同步。

---

## 3 · 实施步骤

每步独立可提交、可回滚。**按序做，不要并行。**

### Step 0 · 冻结基线（先做，别跳）

```bash
python3 -m pytest plugin/acc-common/test_gen_cases_dtype_attr.py -k ExistingOpsByteIdentical -q
python3 -m pytest plugin/acc-common/test_validate_preparation_state.py -q
python3 -m pytest plugin/acc-common/test_spec_isolation.py -q
python3 -m pytest plugin/acc-common/test_cpp_extension_adapter.py \
                  plugin/acc-common/test_cpp_extension_driver.py \
                  plugin/acc-common/test_validate_cpp_extension_receipt.py -q
```

记录通过数。**后续每步都要保持这批全绿**——这是「没碰坏现有通路」的证据。

另存一份 PR 通路的 `source_facts.json`（拿现成报告目录里的即可）作为 Step 6.10 的字节对照基准。

### Step 1 · schema 与判别式（纯读侧，行为不变）

- `fetch_source.py`：`build_source_facts` 增加 `dut_source` 参数（默认 `"pull_request"`），
  PR 分支下**不写入该键**（保字节不变）
- `validate_preparation_state.py:68-78`：`_validate_source_payload` 先读
  `payload.get("dut_source", "pull_request")` 再分支
  - `pull_request` → 现有 `pr` 必填集**一行不改**
  - `local_checkout` → 校 `local_checkout.root_digest` 为 64 位 sha、`op_subdir` 非空 str

**验**：Step 0 全绿（PR 通路零变化）。

### Step 2 · `fetch_source.py` 加 `--local-repo`

**新增参数**：

```
--local-repo <path>       与 --pr 互斥；本地被测代码仓根
--op-subdir <rel>         被测算子子目录（相对 --local-repo）
--base-ref <ref>          可选；给了才算 changed_files
--allow-dirty             可选；允许 dirty worktree（记账，§2.5）
```

**新增函数**（与 `build_pr_facts` 平级）：

- `compute_root_digest(repo_root, op_subdir, excludes)` → §2.3 算法
- `probe_local_git(repo_root)` → `{head_sha, remote_url, base_ref, dirty, dirty_files}` 或 `None`
- `build_local_facts(repo_root, op_subdir, base_ref, allow_dirty)` → 写 `pr_facts.json`（§2.7 形态）
- `build_source_facts` 的 reasons 分支（§2.6）

**互斥校验**：`--pr` 与 `--local-repo` 同给 → fail-loud，**在任何文件读之前**中止
（对齐现有 `_parse_pr_url` 的 fail-loud 口径）。

**打印补齐**（同时解掉 findings §2 B1）：

```
无 --pr 且无 --local-repo：
[fetch] ⚠ 未给 --pr / --local-repo → 未产 source_facts.json 与 pr_facts.json；
        CP-C 三道门（preflight / harness trust / run_workflow）将 BLOCKED
退出码非 0
```

### Step 3 · `aclnn_py` 侧消费者（§1.3 的 #1/#2/#3）

即使 `aclnn_py` 要被堵（Step 6），这三处**仍要改**——
`--allow-experimental-form` 通路还要能跑，且 `preflight_aclnn` 是通用静态对账。

| 位置 | 改法 |
|---|---|
| `preflight_aclnn.py:118` | `bindings` 加 `dut_source`；local 时写 `bindings["local_root_digest"]`，**不要**把 root_digest 塞进 `pr_head_sha` |
| `preflight_aclnn.py:134-136` | 交叉校验按 `dut_source` 分支：PR 比 `head_sha`，local 比 `root_digest` |
| `verify_aclnn_harness.py:401 / 452 / 575` | 同样分支；从 preflight bindings 取对应字段 |

⚠ **`preflight_aclnn.py:142-160` 的 `key_files` 对账逻辑不要动**——两条通路同形，原样复用。

### Step 4 · `cpp_extension` 侧消费者（§1.3 的 #4/#5/#6，**主战场**）

这是目标 2 收敛后**唯一还活着**的通路，必须打通。

| 位置 | 改法 |
|---|---|
| `cpp_extension_adapter.py:284-289` | `_validate_vendor_build_receipt` 按 `source.dut_source` 分支；local 校 `local_root_digest` |
| `cpp_extension_driver.py:119-124` | 同上（driver 侧独立校验，两处都要改，别只改一处） |
| `validate_acceptance_state.py:788` + `:794-796` | **三级门**里同样分支——**这处最关键，漏了会在最后一刻 BLOCKED** |
| **产 `vendor_build_receipt` 的外部驱动** | ⚠ **本仓不产这份收据**——全仓只有消费方（`cpp_extension_adapter.py` / `cpp_extension_driver.py` / `validate_acceptance_state.py` 三处读，加两个测试文件里的构造夹具）。要改的是**真机侧那个执行 build 并写收据的脚本**，它不在本仓。<br>**⚠ 具体文件名本方案给不出**——全仓（含 `doc/`、`SKILL.md`、`agents/`）grep
`vendor_build_receipt` / `build_receipt` **零命中**于任何产出方描述，只有消费方。
这是一处**真实的信息缺口，不要靠猜**。<br>
**实施动作**：① **先定位**——向用户确认，或在真机上按
`grep -rl 'oprunway.vendor_build_receipt' <真机工作区>` 找写入方；找不到就停下来问，
不要自己新写一个产出方（会和真机现有的那份冲突）；② 让它按 §2.8 写 `dut_source` + `local_root_digest`；③ 在本仓的两个测试夹具（`test_cpp_extension_driver.py` / `test_validate_cpp_extension_receipt.py`）里同步加 local 形态的样例，作为契约的机器化定义 |

**新增交叉校验**（§2.8 的信任基石）：
`vendor_build_receipt.source.local_root_digest == source_facts.payload.local_checkout.root_digest`，
不等即 BLOCKED。建议落在 `validate_acceptance_state`（三级门），
与现有 PR head 的绑定校验同层。

### Step 4b · CP-F 精度复测通路（§1.3 的 #8/#9）

主验收链之外，但**不修就没法复测**。

| 位置 | 改法 |
|---|---|
| `precision_retest_contract.py:358` | `base_pr_head` 改为按 `dut_source` 取：PR 取 `pr_head_sha`，local 取 `local_root_digest`；建议字段改名 `base_source_identity` 并带 `dut_source`，避免再出现「叫 pr_head 装 digest」 |
| `precision_retest_contract.py:416 / 432` | `base_provenance` / `expected_provenance` 的 `head_sha` 同样分支；漂移比对逻辑本身不变 |
| `precision_retest_runner.py:247` | `actual.pr_head` 同样分支 |

⚠ **若本批决定先不做 Step 4b**，必须在方案 §4「不做什么」显式挂账，
并在 `AGENTS.md` §9 记一条「本地来源暂不支持 CP-F 复测」——不能让人以为已覆盖。

---

### Step 5 · provenance 降级在报告里如实标注

`render_acceptance_markdown.py:206` 现在硬渲染 `| PR head | ... |`。
改为按 `dut_source` 渲染整节（**确定性渲染，禁止手写**）：

```markdown
### 来源与 provenance

| 项 | 值 |
|---|---|
| DUT 来源 | 本地 checkout（**非在线 PR**） |
| 子树摘要 | root_digest = `abc123…` |
| git head | `d93dc7d…`（worktree clean） |
| base ref | `master` |
| changed_files | 12 个文件 |
| vendor 绑定 | build receipt 的 local_root_digest 与源码子树摘要**一致** |
| ⚠ provenance 强度 | 本地 checkout **无法证明**其对应任何具体 PR；任务书快照证明的是「验收依据了这份字节」，**不证明**它等于 gitcode 上的原文 |
```

dirty 时额外一行：`⚠ worktree dirty，被测字节与 git head 不符；dirty 文件 N 个（清单见收据）`。

### Step 6 · 通路收敛到 `cpp_extension`

1. `run_workflow.py` 加 `_ACCEPTANCE_RUNNER_FORMS = frozenset({"cpp_extension"})` 与白名单门（§2.9）
2. 加 `--allow-experimental-form`，该路径**不产** `acceptance.json` / `verdict.json`。
   **产物契约照抄仓内 mock / catlass_mock 的现成口径**（不要另发明一套）：
   - 文件名：`dev_run_summary.json` + `dev_precision_check.json`（同 `AGENTS.md` §9 对 mock 通路的描述）
   - `evidence.evidence_grade = "development"`
   - `evidence.acceptance_note = "NON-ACCEPTANCE (experimental runner_form) …"`
   - 判定条件：见 `catlass_adapter.py:488`（「`NON_ACCEPTANCE_MODES` 里的通路恒 `development`」）
     与 `perf_compare.py:19 / :116 / :765` 的既有实现
   - **把 `aclnn_py` / `cpp` 加进 `NON_ACCEPTANCE_MODES` 同类集合**，让出口守卫自动接管，
     而不是在 `run_workflow` 里另写一段 if
3. **影响面盘点**（做之前先跑一遍，别边改边发现）：
   ```bash
   # 现存 spec 里有几份不是 cpp_extension？
   grep -l '"runner_form"' plugin/samples/specs/*.json | xargs grep -h '"runner_form"'
   # 哪些测试按 aclnn_py / new_example 写？
   grep -rln 'aclnn_py\|new_example' plugin/acc-common/test_*.py
   ```
   受影响的测试标 `skip` 或改走 `--allow-experimental-form`，**不要直接删测试**
4. **同步 `AGENTS.md`**（§2.10）：§4 的表加「当前是否用于正式验收」列 + 理由；§9 同步

⚠ **实施前先知道代价**：本次 Roll 的 `aclnnRoll.spec.json` 写的是 `runner_form: "aclnn_py"`。
收敛后它必须改成 `cpp_extension`，而 `cpp_extension` 需要 `torch.ops` 桥 + vendor ELF 收据，
**接入成本高于 `aclnn_py`**。别做完门才发现 Roll 反而跑不了了。

⚠ **与 §4「不动 `plugin/samples/`」不冲突**（已核实）：`aclnnRoll.spec.json`
**不在** `plugin/samples/specs/` 下（那里只有 catlass_basic_matmul / equal / im2col /
isclose / median / neg / sign / upsample_nearest_3d / upsample_nearest_exact2d 九份），
它是远端报告目录里的 per-run 产物。**本批只动那份 per-run 副本，不碰 `plugin/samples/`。**

### Step 7 · 测试

**正路**：
1. `--local-repo` + git 仓 + clean + 给 `base-ref`，逐条断言（对应验收标准 1/2/3）：
   - 产物**恰好四件**：`task_doc.md` / `task_doc.snapshot.md` / `pr_facts.json` / `source_facts.json`
   - `payload.completeness.status == "complete"` **且** `payload.completeness.reasons == []`
   - `payload.dut_source == "local_checkout"`
   - `payload` 中**无 `pr` 键**
   - `payload.local_checkout.root_digest` 为 64 位小写 hex
   - `payload.local_checkout.digest_excludes` 非空且与实际使用的排除清单一致
2. `--local-repo` + 非 git 目录 → 仍 `complete`，`local_checkout.git` 键缺席
3. 全链路：`fetch_source --local-repo` → `preflight_aclnn` 不 BLOCKED
4. **cpp_extension 全链路**：local 来源 + `vendor_build_receipt` 带 `local_root_digest`
   → `cpp_extension_adapter` / `cpp_extension_driver` / `validate_acceptance_state` 三处都过

**负路（同等重要）**：
5. dirty 且无 `--allow-dirty` → BLOCKED，reasons 含 `dirty_worktree_not_allowed`
6. dirty 且有 `--allow-dirty` → 通过，`git.dirty == true`、`dirty_files` 非空
7. 无 base-ref → `changed_files == "unavailable"`（**断言不是 `[]`**）
8. `--pr` 与 `--local-repo` 同给 → fail-loud，且**不产任何文件**
9. 都不给 → 打印告警 + 非 0 退出，不产 `source_facts.json`
10. **`local_root_digest` 与 `source_facts.root_digest` 不等** → BLOCKED（§2.8 的信任基石）
11. `runner_form: "aclnn_py"` 且无 `--allow-experimental-form` → 明确报错，**不产 `acceptance.json`**
12. `runner_form: "aclnn_py"` + `--allow-experimental-form` → 产物带
    `evidence_grade="development"`，且**无 `acceptance.json` / `verdict.json`**

12b. **CP-F 复测**（若做了 Step 4b）：local 来源下 `precision_retest_contract` 能建立
    base provenance 且漂移比对不误报

**回归**：
13. Step 0 的四组全绿
14. **PR 通路的 `source_facts.json` 的 payload「去掉 `producer` 后」与 Step 0 基准逐字节相同**
    ← 本方案的安全绳。⚠ **不要断言整个 digest 相同**——`producer.logic_sha256`
    是 `fetch_source.py` 自身源码的哈希，改了工具它必然变（§2.2）

**确定性**：
15. 同一目录连跑两次 `compute_root_digest` 得同一值
16. 改动 op_subdir 下任一文件 → `root_digest` 变；改动 `.git/` 或 `__pycache__/` → **不变**

---

## 4 · 不做什么（本批边界）

- ❌ 不改 PR 通路的任何**业务字段**（Step 7.14 是硬约束）。
  ⚠ 措辞订正：**不是**「不改任何输出字节」——`producer.logic_sha256` 是
  `fetch_source.py` 自身源码的哈希，改了工具它必然变（§2.2），digest 也随之变。
  硬约束的准确表述是：**payload 去掉 `producer` 之后逐字节相同**
- ❌ 不让本地事实伪装成 PR 事实（不复用 `payload.pr`、不把 `root_digest` 塞进 `pr_head_sha`）
- ❌ 不删 `repo_adapter` 的 `aclnn_py` / `cpp` 能力表条目
- ❌ 不解决 D5「spec 无来源门」——另一批
- ❌ 不解决 A3 complex64 能力扩展——另一批
- ❌ 不动 `plugin/samples/`——另一批（且需配 D5）
- ❌ 不引入执行方向确认单（目标 5/6/7）——但 Step 5 的 provenance 节可被那份确认单直接复用

---

## 5 · 风险与取舍

| 风险 | 处理 |
|---|---|
| 本地 provenance 弱于 PR | 判别式 + 报告显式降级（Step 5）；**不消除风险，只让它可见** |
| 改动波及 7 个消费者，可能碰坏现有通路 | Step 0 冻结 + Step 7.14 逐字节回归；每步单独提交可回滚 |
| **漏改 `validate_acceptance_state.py:788`** | 它在三级门里，漏了会在**最后一刻**才 BLOCKED——Step 4 单列，Step 7.4 专测 |
| `root_digest` 排除规则将来变更 → 旧收据不可比 | 排除清单写进收据（§2.3）；变更时 digest 自然不同，不会静默 |
| `--local-repo` 目录中途被改 | `root_digest` 在 fetch 时算一次；harness 信任门与 run_workflow 已有「收据漂移即停」，会接住 |
| `--allow-dirty` 被滥用成常态 | dirty 收据进报告顶部；建议后续给正式验收路径加「dirty 一律拒」 |
| **收敛后 Roll 反而跑不了** | Step 6 的 ⚠：`cpp_extension` 接入成本高于 `aclnn_py`；**实施前先评估 Roll 迁过去要多少工作量** |
| `--allow-experimental-form` 变成常态绕过 | 该路径物理上不产 `acceptance.json`（比照 mock 通路），绕不出验收结论 |
| **只做主链、漏掉 CP-F** | 本地来源能验收一次但不能复测；Step 4b 单列，不做就在 §4 挂账 + 同步 `AGENTS.md` §9 |
| **改完所有旧 preparation 收据变 MISS** | `producer.logic_sha256` 必变（§2.2）→ 复用失效、要重新准备一次。**这是正确行为**，但必须提前写进 changes-brief，否则会被当成 bug 报上来 |
| **收据 `dut_source` 伪装绕过 root_digest 绑定** | §2.8 第 0 步：两边 `dut_source` 必须先一致再分支 |
| **白名单门只拦入口被绕过** | §2.9：入口 + 最终产物写门，两处都校（照抄 catlass_mock 后门的处理口径） |

---

## 6 · 验收标准（做完怎么算过）

以 aclnnRoll 这次的场景做见证（**不为 Roll 写任何特判**）：

```bash
python3 plugin/acc-common/fetch_source.py \
  --taskdoc    <本地任务书 md> \
  --local-repo <本地 ops 仓> \
  --op-subdir  <算子子目录> \
  --base-ref   <base> \
  --out        <报告根>
```

断言：

1. 产出 `task_doc.md` / `task_doc.snapshot.md` / `pr_facts.json` / `source_facts.json` 四件
2. `source_facts.payload.completeness.status == "complete"`、`reasons == []`
3. `source_facts.payload.dut_source == "local_checkout"`，且 payload 中**无 `pr` 键**
4. `preflight_aclnn.py --root <报告根> --spec spec.json` **不再** BLOCKED
5. `runner_form: "cpp_extension"` + 带 `local_root_digest` 的 `vendor_build_receipt`
   → `cpp_extension` 全链路（adapter / driver / 三级门）**均不因来源问题被拒**
6. `runner_form: "aclnn_py"` → 明确报错、不产 `acceptance.json`
7. **PR 通路回归**：拿现成 PR 链接跑一遍，`source_facts.json` 的
   **payload 去掉 `producer` 后**与 Step 0 基准逐字节相同
   （digest 会因 `producer.logic_sha256` 变化而不同，属预期，见 §2.2）

---

**本方案未实施。** 按 §3 的 Step 顺序推进，每步跑完对应测试再进下一步；
全部落地后在 `doc/oprunway-changes-brief.md` 顶部追加一行倒序摘要，
并同步 `AGENTS.md` §4 / §9（§2.10）。
