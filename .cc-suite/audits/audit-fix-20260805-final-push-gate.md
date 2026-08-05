# Audit Findings

**Run**: audit-fix 20260805 final（push 前统一审修门，AGENTS.md §5.7）
**Scope**: `main`(`4d1544d`) → `worktree-oprunway19` HEAD(`7aa5690`) **+ 工作树未提交改动**（37 tracked 文件 + 1 untracked handoff）
**Audit type**: full，按关注点拆 4 块代码 + 3 块散文，各一次 codex 调用（整份大 diff 一次审必超时）
**Model**: 代码 `gpt-5.6-sol` / effort high；散文 `gpt-5.6-sol` / effort low（仓规指定）
**Verify thread**: `019fd2b4-9a2d-7bb3-b8f6-af067de1ad5c`（复核修复本身，6 条 finding，见 §三补）
**Audit threads**: A `019fd299-344f-7dc0-98de-e54e21608323`（fetch_source/dut_source）·
B `019fd299-3a1c-74d2-9902-4c7dbbb76ca4`（precision 链）·
C `019fd299-4138-7851-a0c0-eeeabfddaaa2`（五道门）·
D `019fd299-4ac4-73d2-8c83-5f68dd947b3f`（渲染/adapter/driver）·
P1 `019fd29a-345a-7712-80ba-7194a5a55838`（AGENTS/SKILL/交接）·
P2 `019fd29a-3944-78d2-82e8-4d305fcc264b`（两份方案）·
P3 `019fd29a-42f6-7c63-ba2d-f9c6cc617312`（Roll findings / 过程审计包）
**Status values**: open | fixed | not-fixed | partial | rejected | skipped

---

## 一 · 代码 finding

| # | File | Line | Severity | Dimension | Finding | Status |
|---|------|------|----------|-----------|---------|--------|
| C1a | plugin/acc-common/validate_acceptance_state.py | 894 | Critical | 2 · 判据可绕过 | 三级门只对 `local_checkout` 比锚值；**PR 通路只比了 kind 就 return**。`preflight_aclnn` 那条 head 校验比的是 `pr_facts ↔ source_facts`，不是 `build receipt ↔ source_facts`——「build 出来的 `.so` 对应哪个 commit」在此之前没人核过，两条通路待遇不对称 | fixed |
| C2 | plugin/acc-common/validate_acceptance_state.py | 831 | Critical | 2 · 判据可绕过 | `_find_source_facts` 只 `json.load` 取 `payload`，**不复算内容寻址 digest**。手写一份只含「与恶意收据同值的 `local_root_digest`」的最小 JSON 就能当本地来源的信任锚——而本地锚的全部可信度就来自这条对账 | fixed |
| C1b | plugin/acc-common/validate_acceptance_state.py | 822 | High | 1 · fail-open | 显式 `--source-facts` 指向不存在的文件时静默退成「没找到」→ PR 通路直接放行。一个 typo 就把整条对账关掉；「自动发现落空」（常态）与「显式指路指空」（用户认定有对照物）被抹平 | fixed |
| A1 | plugin/acc-common/dut_source.py | 122 | High | 4 · 宣称有门其实没门 | docstring 写「`expected_kind` 这道前置校验不能省（否则整套设计被绕过）」，签名却是 `expected_kind=None`。「忘了传」与「确认过没有对照物」在调用点长得一模一样 | fixed |
| A6 | plugin/acc-common/dut_source.py | 149 | High | 2 · 判据可绕过 | build receipt 的 `source` 里两条通路的锚字段**没有互斥校验**：`dut_source=pull_request` 可以同时带 `local_root_digest`。任何按字段名直取（不用返回三元组）的下游都能自选来源身份 | fixed |
| A3 | plugin/acc-common/fetch_source.py | 782 | High | 1 · fail-open | `_local_key_files` 注释写「逃逸软链必须拒」，实现是 `continue`；读文件 `OSError` 同样 `continue`。跳过后 `completeness` 仍是 complete，事实包却少了 header/example/op_def——`aclnn_*.h` 缺席直接动摇 aclnn 路由第一依据 | fixed |
| A7a | plugin/acc-common/fetch_source.py | 966 | High | 1 · fail-open | `changed_files` 只判「非空」。`changed_files="abc"` 既非空又不是哨兵 → 通过 → payload 生成式**按字符迭代**成 `["a","b","c"]`：一份凭空捏出、形态完全合法的改动清单 | fixed |
| A7b/C5 | plugin/acc-common/fetch_source.py<br>plugin/acc-common/validate_preparation_state.py | 971 / 102 | High | 1 · fail-open | `warnings` 是按 `git.dirty` 派生的：把 `dirty` 改成 `false`（清单照旧非空）就能让 `dirty_worktree_allowed` 降级留痕整条消失。另 `git: null` 被 `is not None` 判成「非 git 仓」，免掉全部一致性校验 | fixed |
| C4 | plugin/acc-common/run_workflow.py | 265 | High | 4 · 宣称有门其实没门 | `_assert_acceptance_form_allowed(spec, mode)` docstring 说「同时看 spec 与 mode，交叉校验比只看一个更难伪造」，实现里 `mode` **只用在报错文案**里 | fixed |
| D1 | plugin/acc-common/render_acceptance_markdown.py | 190 | High | 1 · fail-open | 渲染器只校来源锚形态，**不校 build receipt 自身是否 `VERIFIED v1`**，却输出「可证明验的就是这个 PR 的这个 commit」这类强度断言——等于替一份未核验的收据背书 | fixed |
| A2 | plugin/acc-common/fetch_source.py | 239 | High | 8 · 越权断言 | `root_digest` 按「任一路径段等名」剔除 `build` / `build_out`，但工具没有也无法证明这些目录不含构建输入。子树内 `**/build/**` 的改动不改摘要，而「明确不覆盖的东西」那张表没写这条 | partial（如实挂账，行为不改） |
| D3 | plugin/acc-common/render_acceptance_markdown.py | 145 | Medium | 8 · 越权断言 | `git` 键缺席就断言「不适用——不是 git 仓」，且**不看 `completeness`**。一份被裁剪 / blocked 的 facts 缺 git 键，含义是「不知道」，被读成了结论 | fixed |
| D4 | plugin/acc-common/render_acceptance_markdown.py | 149 | Medium | 3 · 正确性 | `dirty=true` 配空清单渲染成「worktree 有 **0** 项未提交改动」——一句自相矛盾、却读起来像「其实没什么事」的话 | fixed |
| C7 | plugin/acc-common/validate_acceptance_state.py | 900 | Medium | 3 · 正确性 | 畸形 `root_digest`（int/dict）已被前一句判非法，报错拼装里 `(facts_digest or '（缺失）')[:12]` 会再抛 `TypeError`，把确定性错误变成裸 traceback | fixed |
| A8 | plugin/acc-common/fetch_source.py | 652 | Medium | 1 · fail-open | porcelain `R`/`C` 记录只记新名、跳过原名。把文件从被测子树挪出去时 `dirty_files_in_op_subdir` 变 0——收据宣称「被测子树内没有未提交改动」，而子树里实际少了一个文件。另原名字段缺席（输出截断）被当成正常 | fixed |
| C3 | plugin/acc-common/run_workflow.py | 271 | Medium | 4 · 宣称有门其实没门 | 出口门 docstring 声称能逮住「绕开 run_workflow 直调子脚本」。实际只覆盖一切经 `run()` 的路径；手写/外部工具伪造的 `acceptance.json` 不在其内 | fixed（改为如实陈述覆盖范围） |
| A4 | plugin/acc-common/fetch_source.py | 875 | Low | 8 · 越权断言 | 取材期 TOCTOU 复算只能逮「改了没改回去」，逮不住「改完再原样改回来」。原注释没说清边界 | fixed（补边界说明） |

### rejected（核过，不成立）

| # | 来源 | 主张 | 为什么不成立 |
|---|---|---|---|
| R1 | B#2 | F3 的 fresh build receipt「`repo` 完全未与 directive 或基础收据对账」 | **实现里有**：`precision_retest_runner.py:305` 的 `source.get("repo") != binding.get("base_source_repo")` 就是这条对账，与 `fresh_build.argv` 同一个判断块。codex 只看 diff、漏了这段未改动的上下文 |
| R2 | B#8 | manifest 记 `_canonical_sha(dict)`、执行侧用 `sha256_file(path)` 比，两个口径可能不一致 | 不一致不存在：`atomic_write_json` 写的就是 `canonical_json_bytes(value)` 的原样字节，`_canonical_sha` = `sha256(canonical_json_bytes(value))`。两者恒等 |
| R3 | B#3 | `precision_policy` 对**所有单输出** spec 放开「in_dt ∈ 允许集 → 取 in_dt」是把歧义猜成确定映射 | 这是**改动前就有**的历来口径，且上一轮（iter1 #1/#2）已专门就它做过决策：`sign`/`neg` 的 spec 把 `out.dtype` 写成 `["float32","float16"]` 就是靠这条挑 in_dt，收紧会让这些 spec 全部派生失败。多输出侧（`allow_input_membership=False`）保持严格。本轮不改，张力已在函数 docstring 里写明 |
| R4 | A#10 / B#5 / B#6 / C#6 | 「diff 未增加任何测试，新增判定分支无覆盖」（共 4 条） | **拆块 prompt 的产物，不是事实**：为控制 token，4 块代码 prompt 都只放了实现文件、没放测试文件。本轮实际新增测试 `test_fetch_source.py` +462、`test_precision_retest_contract.py` +299、`test_render_acceptance_markdown.py` +159、`test_precision_policy.py` +159、`test_preflight_aclnn.py` +139、`test_verify_aclnn_harness.py` +125、`test_validate_cpp_extension_receipt.py` +92 等 |
| R5 | A#5 | key_files 用 `decode("utf-8","replace")`，`bytes_sha256` 与磁盘字节不对应 | 半真但不构成缺陷：`bytes_sha256` 的语义自始就是「所记录内容的 utf-8 编码摘要」，PR 通路（`_key_files` 走 API，同样 `replace`）与本地通路口径一致，preflight 的对账两侧同源。真实 C/C++ header 为 ASCII/UTF-8；改成严格解码属通路语义变更，不在本轮范围。**记为已知残留** |
| R6 | A#9 | 本地 `source_facts` 没有可信 repo 身份，收据 `repo` 只要非空即可 | 与 A2 是同一件事的两面（锚只覆盖 `op_subdir`），已在 A2 里如实挂账。本地通路的 `repo` 本就是描述性字段，绑定被测字节的是 `root_digest`，不另开一条 |
| R7 | D#2 | 渲染器按 kind+anchor 匹配 source_facts，两个 root_digest 相同但 dirty 状态不同的 worktree 可以互相顶替 | 需要「同一 `op_subdir` 字节全等」+「有人把另一份 facts 放进报告目录」同时成立。真正的阻断在三级门（现已加 digest 复核），渲染器本就只如实标强度、不重判。成本/收益不划算，**记为已知残留** |
| R8 | D#5 / D#6 | 渲染 provenance 节无测试；`_cell` 转义可能不足 | 前半同 R4（测试文件没进 prompt，`ProvenanceSectionTest` 本就存在，本轮又加 7 条）。后半：`_cell` 已做 `|` → `\|` 与换行 → 空格，Markdown 表格破表面已封；反引号/HTML 属渲染观感，不影响裁决 |

---

## 二 · 散文 finding

| # | File | Severity | Finding | Status |
|---|------|----------|---------|--------|
| P2-1 | doc/oprunway-local-source-plan.md:108 | Critical | 「`cpp_extension` ← **唯一跑通完整验收的**」把 `gate.passed=true`（证据完整性门过）写成了验收通过。那一轮的确定性裁决是 `FAIL(精度)`（1101 PASS / 51 FAIL）——本仓头号语义事故 | fixed |
| P1-1 | plugin/skills/acceptance-workflow/SKILL.md:256 | Critical | 「本地来源要复测 `aclnn_py`，只能先回到 PR 通路取材」——CP-F 复测的前提是同一份 DUT；换来源 = 换 DUT 身份，那是另一次完整验收，写成复测办法等于给来源替换开口子 | fixed |
| P3-1 | doc/oprunway-process-audit-package.md:12 | Critical | 声称过程审计包「可用于检查 provenance」，但默认 allowlist 收不到 `source_facts.json`（取材 `--out` 与报告目录不同）——`local_checkout` 时 `local_root_digest` 根本没有对照物 | fixed |
| P1-3 | plugin/skills/acceptance-workflow/SKILL.md:92 | Critical | 本地通路做不了对应校验的第 2 条（要读 PR `title` 拿 issue 号，而本地来源没有 PR），规程却没说这条腿断了。剩下的 `--op-subdir` 是用户自己给的，拿它当对应证据就是自证 | fixed |
| P3-9 / P2-7 | doc/oprunway-roll-...-findings.md:720<br>doc/oprunway-workflow-governance-plan.md:607 | High | 「Median 1152 基线的 **58** 条失败」——58 是上一轮 1344-case checkpoint 的数字；1152 基线是 **51** FAIL（`AGENTS.md` §9） | fixed（两处） |
| P3-4/5/6/7/8/11 | doc/oprunway-roll-complex64-trial-findings.md | High | 整份文档写于实施之前，**同一次 push 里已落地其中两项**（本地来源通路 B2、runner_form 准入白名单），而文中仍写「没有一等公民通路」「❌ 不支持」「建议加一道白名单门」「仓规现在写着三条都能产验收裁决」「本文档不包含任何已实施的改动」——照着旧建议会重复开发 | fixed（加「实施状态」表 + 逐处标注） |
| P3-10 | doc/oprunway-roll-...-findings.md:885 | High | 「`aclnn_py` … 与**本次** 56 例」——56 例是 08-03 手工构造 `pr_facts` 那次的数字；本次 Roll 只生成 50 例、Task2 一例未跑。读者会读成「aclnn_py 本轮已完成真机跑测」 | fixed |
| P2-2 | doc/oprunway-local-source-plan.md:542 | Critical | 「vendor 绑定：`local_root_digest` 与**源码**子树摘要一致」——摘要只覆盖 `op_subdir`，称作与「源码」的绑定夸大了 provenance 强度 | fixed |
| P1-4 | doc/oprunway-session-handoff-2026-08-05.md:11 | High | 「本轮只产文档，未改任何 `plugin/` 代码」——同一次 push 里 plugin 改了 27 个文件。该文描述的是 08-05 白天那一段，但它自称「当前交接入口」，读者会把「本轮」读成这次 push | fixed（降为历史材料 + 限定「这一段」） |
| P1-5 / P1-2 | AGENTS.md:207 / :397 | High | 交接入口指向旧 handoff；`source_facts` 缺席时的残留伪装面未记账 | **已在工作树未提交改动里修好**（codex 看的是 `main...HEAD`，看不到）。⚠ 见下方「交付前必做」 |
| P2-3/4/5/8/9/10/11/12/13/14/15 | doc/oprunway-workflow-governance-plan.md | Critical×3 / High×8 | 对**尚未实施**的治理方案的设计批评：`run_id` 未纳入 `source_facts_digest`/逻辑摘要、D0 出口门未校 `source_facts_digest`、「手写 spec 必被拒」不成立（内容寻址证明不了收据由 extractor 所产）、`cp_states` 只要求「递增不重复」不足以保证 CP 链无缺口、`gate_attempts` 缺连续性与输入绑定、`self_digest` 覆盖不全、`source_facts.json（可选）`与失效规则冲突、case-data 摘要「直接复用 root_digest 定义」与集合边界冲突等 | not-fixed（见下） |

**P2 那一组为什么不改**：`doc/oprunway-workflow-governance-plan.md` 是**未实施的设计草案**，
这些是对草案的设计意见，不是仓里正在跑的代码缺陷。现在照单改草案，等于让审计意见替代设计决策；
正确处置是**在实施该方案前**把这 11 条当作输入逐条过一遍。已把清单原样留在本文件，实施时按 thread
`019fd29a-3944-78d2-82e8-4d305fcc264b` 取全文。

---

## 三 · 处置要点

### 判别式（`dut_source.py`）
- `validate_build_receipt_source` 的 `expected_kind` 改成**必填关键字**，新增哨兵 `NO_EXPECTED_KIND`。
  照 iter1 处理 `allow_input_membership` 的同一口径：**差异抬到参数上、由调用方显式声明**，
  让「忘了传」和「确认过没有对照物」在调用点长得不一样。7 个调用点全部显式化。
- 新增 `ANCHOR_FIELD` 表与**两条通路锚字段互斥**校验；两套锚齐备即拒。

### 三级门（`validate_acceptance_state.py`）
- `_find_source_facts` 现在复核内容寻址 envelope（domain + 复算 digest），并把
  「显式路径指不到文件」与「自动发现落空」分成两态：前者 `__BAD__`（阻断），后者 `None`（按通路分）。
- 锚对账改为**两条通路同口径**：`dut_source.identity(facts)` 取锚三元组，与收据三元组整块比。
  顺带消掉 `(facts_digest or '（缺失）')[:12]` 那个 `TypeError`（identity 保证锚是合法 hex 串）。
- PR 通路的兼容性没有被打破：真机报告目录里本来就没有 `source_facts.json`，仍走「找不到 → 不阻断」。

### 取材（`fetch_source.py`）
- 新增 `_is_str_list`（允许空表）/ `_is_path_list`（要求非空）两个谓词，替掉「非空即可」的判据。
  ⚠ 两者必须分开：`dirty_files == []` 是**干净 worktree 的正确表示**，用「非空」去要求它会把
  每一次干净取材都判成畸形收据（第一版就是这么写的，回归当场逮住 4 条红）。
- `git` 事实新增 `dirty ↔ dirty_files` 互相蕴含校验；`validate_preparation_state` 侧同步收紧
  （`git: null` 不再等同缺席、`dirty_files` 元素类型、`dirty_files_in_op_subdir ⊆ dirty_files`）。
- 关键文件的逃逸软链 / 读失败从 `continue` 改成 `raise`。
- porcelain `R`/`C` 的原名一并记进 `dirty_files`；原名字段缺席（截断）抛 `GitProbeError`。

### 渲染器（`render_acceptance_markdown.py`）
- provenance 节新增分支 ②：收据非 `oprunway.vendor_build_receipt` v1 / 非 `VERIFIED` → 不作任何断言。
- 采信 `source_facts` 前先要求 `completeness=complete`；「git 键缺席 → 不是 git 仓」这条**由缺席
  推结论**的断言，只有在事实包完整时才允许。
- `dirty` 与清单必须互相印证才给结论，否则一律「未知」。三条新措辞全部落常量，测试直接引用常量断言。

### 编排（`run_workflow.py`）
- 出口门真的用上 `mode`：要求 `runner_form` 准入 ∧ `mode == _RUNNER_FORM_TO_MODE[runner_form]`。
- 出口门 docstring 改为如实陈述覆盖范围（一切经 `run()` 的路径；不含手写 `acceptance.json`）。

### 新增测试（共 +23 条净增，全部指向本轮堵掉的分支）
`test_fetch_source.py`：`changed_files` 字符串迭代、`dirty` 与清单矛盾、畸形 `dirty_files`、
干净空表正例、逃逸软链中止、R/C 改名双记账、`expected_kind` 必填、两锚齐备被拒。
`test_validate_cpp_extension_receipt.py`：envelope 被篡改、显式路径指空、PR 锚必须与 facts 相等（正反各一）。
`test_render_acceptance_markdown.py`：envelope 篡改不采信、残缺 facts ≠ 非 git 仓、完整 facts 无 git 键正例、
`dirty=true` 配空表、`dirty=false` 配非空表、干净 worktree 正例、三种未 VERIFIED 收据。

---

## 三补 · verify 轮（codex 复核修复本身）

verify 的结论是「不能放行」，5 条成立、1 条驳回，全部当轮处理完。

| # | File | Line | Severity | Finding | Status |
|---|------|------|----------|---------|--------|
| V2 | plugin/acc-common/validate_acceptance_state.py | 851 | Critical | **digest 自洽证明不了对照物合格**。`make_artifact` 谁都能调，包一个只含「与收据同值的 root_digest」的最小 payload 照样自洽；更要命的是 `completeness.status="blocked"` 的**真实**取材产物——digest 完全正确，而仓规写死了「blocked 只供诊断」。拿它当本地锚的对照物就是把不完整证据升级为可裁决 | fixed |
| V1 | plugin/acc-common/validate_acceptance_state.py | 839 | Critical | `explicit = bool(source_facts_path)` → `--source-facts ""`（空环境变量展开的常见形态）被当成「没显式指定」，悄悄退回自动发现，用户明确要求的对账就此关掉 | fixed |
| V3 | plugin/acc-common/fetch_source.py | 1023 | Critical | 我自己修的那条只改了 `validate_preparation_state`：`build_source_facts` 里仍是 `local.get("git") is not None`，`git: null` 照样跳过全部一致性校验。**同一条规则在两道门里含义不同**比原来的洞更糟 | fixed |
| V5 | plugin/acc-common/fetch_source.py | 684 | High | 我把 R/C 的原名一律记成脏——但 `C`（copy）的原文件一个字节都没动，脏的只有新拷出来那份。原文件在子树内、拷贝在子树外时，等于**虚构**一条子树内脏文件，凭空把 provenance 说弱 | fixed（R 记两名，C 只记新名，原名字段仍消费以推进索引） |
| V6 | plugin/acc-common/render_acceptance_markdown.py | 163 | Medium | 渲染层 `dirty_files` 只判「是非空 list」：`[null]` 会被 `len()` 数成 1，渲染出「有 1 项未提交改动」——数字是编的。`dirty_files_in_op_subdir` 也没校子集，「子树内 N 项」可以大于总数 | fixed |
| V4 | plugin/acc-common/fetch_source.py | 1018 | High | 主张本地通路的 `changed_files=[]`（给了 base_ref 且确无差异）应放行，现在被 `_is_path_list` 判为缺失 | **rejected** |

**V4 为什么驳回**：`[]` 在**本轮改动之前**就已经被 `elif not changed_files:` 判成
`missing_changed_files` 了——PR 与本地两条通路一直同口径。我的改动只是把「非空即可」换成
「形态 + 非空」，**没有**新增这条阻断。按 V4 改属于**放宽一条既有的 fail-closed 判定**，
本轮审修门明令不许为了让审计通过而放宽。若确要放行「确实没改」这一情形，那是一次独立的
语义决策（需要回答「一个什么都没改的 PR 该不该进验收」），不该夹在审修门里做。

**V2 的处置值得单说**：改成**复用** `validate_preparation_state._validate_source_payload`
而不是另写一套判据——它已经在校 taskdoc / key_files 锚 / 两条通路必填集 /
`completeness=complete 且 reasons=[]` / warnings 与载重事实一致 / `producer.tool`。
副作用是渲染层那三条「dirty 形态矛盾」的分支**走不到渲染器了**（对照物在更早一层就被判 `__BAD__`）。
早一层拦住是好事，但渲染层那几条**不能因此删掉**——它的职责是「拿到什么都不许说成 clean」，
不该依赖上游一定筛干净。所以把这几条改成直调 `_local_rows` 的
`LocalRowsSecondLineOfDefenceTest`，让第二道防线有独立见证、不被第一道遮住。

---

## 四 · 回归证据

**Python 3.12.13 全量（a3 `oprunway_prov` 容器，`plugin/acc-common/`）**

```
main 基线（本轮开工前）：      1774 passed, 0 failed
audit 修复后：                1797 passed, 11 skipped, 527 subtests passed
verify 修复后（终态）：       1800 passed, 11 skipped, 14 warnings, 527 subtests passed in 83.24s
```

净增 26 条、**0 failed**，无 skip 增加。中途四次红全部当场修掉并复跑：
① `_is_path_list` 误用导致干净 worktree 被判畸形（4 条红）；
② `test_precision_retest_contract` 的期望正则被更精确的互斥报错取代（1 条 subtest 红，已按新归因更新断言）；
③ verify 修复后夹具 payload 不再满足完整契约（4 条红，改用共享的 `source_facts_payload`，
   并让它像真 producer 一样**从载重事实派生** `completeness.warnings`）；
④ 新测试类插错位置把两条既有用例吞进去（4 条红，已把类移到文件末尾）。

**真 Python 3.11.15 只读语法门（a5 `oprunway_gb` 镜像，覆盖全 `plugin/`）**

```
[syntax-gate] python 3.11.15  已编译 107 个 .py
[syntax-gate] PASS 失败 0 个
```

真机是 3.11、开发机是 3.12，PEP 701（f-string 替换字段跨行 / 嵌套同类引号）只有真 3.11 解释器
编译才暴露，`ast.parse(feature_version=(3,11))` 查不出来——所以这道门必须在目标版本上跑。

---

## 五 · 交付前必做 / 已知残留

1. ⚠ **`doc/oprunway-session-handoff-2026-08-05-evening.md` 目前是 untracked**，
   而 `AGENTS.md` 的「§5.6 当前交接」和「§8 最新交接」两处都已指向它。
   **提交时必须把这个文件一并加进去**，否则仓规源指向一个不存在的文件。
2. `source_facts` **自动发现**落空 + 收据自称 `pull_request` 时，「其实说的是 local」这种伪装仍查不出来
   （没有对照物）。要封死得让编排层每次都传 `--source-facts`。已在 `AGENTS.md` §9.3 与门的 docstring 里记账。
   注：显式指路指空**不属于**这条残留面——本轮已改成阻断。
3. `root_digest` 排除 `build` / `build_out`（任一路径段）→ 这两类目录里的改动不改摘要。
   行为不改（in-tree build 会让摘要随每次构建漂移，取材↔构建间的复算会永远失败），
   已写进 `compute_root_digest` 的「明确不覆盖」表，并机器可核地落在 `digest_policy.excluded_segment_names`。
4. key_files 的 `decode("utf-8","replace")`（R5）与渲染器 source_facts 的「同摘要跨 worktree 顶替」（R7）
   两条已知残留，理由见上表。
5. `doc/oprunway-workflow-governance-plan.md` 的 11 条设计意见（P2 组）**未落地**，
   实施该治理方案前须逐条过一遍。
6. ⚠ 散文层还有一条**未处理**：本轮新增 `⚠` 共约 105 个，其中
   `doc/oprunway-local-source-plan.md` 27 个、`doc/oprunway-workflow-governance-plan.md` 34 个、
   `doc/oprunway-roll-complex64-trial-findings.md` 31 个。密度过高会让 `⚠` 失去警示作用。
   本轮只清理了 `AGENTS.md` 的两个（「能力表不是准入表」「收敛的代价要认账」降为普通加粗）。
   两份方案文档的批量降噪改动面大、纯风格、且属人主观取舍，留给用户定夺；
   建议保留口径：`⚠` 只用于**可能导致错误裁决、来源失真或证据静默降级**的事项。
