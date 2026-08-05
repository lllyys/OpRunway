# OpRunway 交接 · 2026-08-05

> 取代 `dev-doc/oprunway-session-handoff-2026-08-04.md` 作为当前交接。旧的只作历史材料。
> 本文写给**下一个 session 的自己**：先读这份，再读 `AGENTS.md`，然后照 §3 开工。

---

## 0 · 一句话

上一轮定的**五项泛化能力已经全部落地**，并用新 workflow 在干净现场把 GaussianBlur
从头跑到尾——**没有 source 任何 `set_env.bash`、没有设任何降级授权环境变量**，
终态 `BLOCKED_GOLDEN_UNAVAILABLE`（169 条里 5 条 OpenCV 算不出真值，其余 164 条 DUT 数值失败为 0）。
这一轮**没有 push**，8 个 commit 还压在本地。

---

## 1 · 当前状态（事实，非计划）

| 项 | 值 |
| --- | --- |
| 分支 | `worktree-oprunway18` |
| HEAD | `cdd98b1`，自 `4d1544d`(origin/main) 起 **21 个 commit** |
| **未 push** | `origin/worktree-oprunway18` 停在 `6f77d30`——**最新 8 个 commit 只在本地**（`f9567f0`..`cdd98b1`）|
| 工作区 | 干净 |
| 单测基线 | 容器内 **2014 passed / 29 failed**；失败集与未改动 HEAD 逐条相同 → 零回归。**29 不是 10**——上一份交接记的 10 条是更早的基线，别拿旧数字对账 |
| 审修门 | ⚠ **本轮尚未过 5.7 的 push 前审修门**（代码审 + 散文审）。push 前必须补 |

### 1.1 真机环境

细节见 `dev-doc/oprunway-real-machine-environment.md` §3。与上一份交接相同，未重建：
950 真机走容器，CANN 9.0.0 / Ubuntu 22.04.5 / **Python 3.11.15** / x86_64，8× Ascend950PR；
实际 ssh alias / 容器名 / 工作区路径只在被 ignore 的 `.oprunway/real-machine.env`。

### 1.2 GaussianBlur 干净现场最终跑测结果

新执行目录，全程未设 `OPRUNWAY_ALLOW_DEGRADED_PROVENANCE`、未 source vendor 的 `set_env.bash`、
receipt 由 `snapshot-digest` / `emit` 两段式生成：

```
overall  = BLOCKED_GOLDEN_UNAVAILABLE      state = BLOCKED_GOLDEN_UNAVAILABLE
gate.passed = true                         gate.errors = {}
counts   : 169 total / fail 0 / golden_unavailable 5 / contract_problems 0
perf     : 16 条真实 kernel-only npu_us，measure_only、无标杆对比
source   : declared_source_form = local_source，degradations = []
```

**怎么读这个结论**：164 条可判用例里 DUT 数值失败为 0；5 条是通道数超 OpenCV `CV_CN_MAX`
（C=937/978/533/708/742）的**参考实现能力边界**，结论是**空白**——既不是算子错了，也不是通过。
⚠ **别把它写成「164/169 通过」**，要写「169 条里 5 条无从判定」。

---

## 2 · 已经做完的，不要重做

上一轮 §3 的五项泛化目标**全部落地**，外加干净现场逼出来的一批收尾修复：

| # | 能力 | 一句话 |
| --- | --- | --- |
| 3.1 | **gitcode 链接取材** | 新脚本 `taskdoc_links.py`：链接按**只看结构**的受控词表分类（blob / tree / **相对链接** / MR / …），`STATUSES` **无 `ok` 兜底**，可变 ref **先解析成 commit sha 再 pin** |
| 3.2 | 无性能对比场景归并 | `perf.mode="measure_only"` 上一轮已落地，本轮真机跑出 16 条 kernel-only 实测 |
| 3.3 | 任务书指明的 golden | `taskdoc_caseset.py` 生成 golden 包装层，并把任务书快照落到 `<ops_root>/<op>/task_doc.snapshot.md` 作授权锚 |
| 3.4 | **任务书自带用例集** | `spec.precision.case_source ∈ {generated, taskdoc}`（省略 = generated = 现行为）；`taskdoc` 档必须显式喂 `--taskdoc-caseset`，**识别不到就 BLOCKED、绝不回退自生成** |
| 3.5 | workflow 改造 | 见本轮 W7 文档同步：`acceptance-workflow/SKILL.md` + `acc-spec/references/taskdoc-to-spec.md` + `acc-precision/SKILL.md` |
| — | **本地代码升为一等输入形态** | `declared_source_form ∈ {git_pr, local_source}`，入口就定（`--pr` / `--pr-snapshot`）。档位判据换轴：从「有没有拿到 PR head」改成「**实得是否与声明一致**」。`local_source` 如愿实得 = `complete`、**不需要任何授权**、`degradations=[]` |
| — | `pr_head_unbound` 语义分家 | 中性形态事实走 `bindings["source_form_facts"]`，降级台账只装「本该绑却没绑」 |
| — | **`golden_unavailable` 一等状态** | case 身份仍进 caseset、允许无 golden 文件、其余 case 继续跑；validator 移出 `fails` 单列名单；新终态 `BLOCKED_GOLDEN_UNAVAILABLE`（与 `BLOCKED_GOLDEN_UNAUTHORIZED` 分开）。**真实失败优先**，两者并存仍报 fail |
| — | **收据不再手拼** | `vendor_build_receipt.py snapshot-digest`（build **之前**）→ `emit`（build 之后）；按 `source.provenance_kind` 分流校验；三处消费方共用一份校验 |
| — | **符号来源由收据反推自设** | driver 从 receipt 绑定的 vendor ELF 按 CANN 布局反推 `ASCEND_CUSTOM_OPP_PATH` 并在任何算子调用前设入；**不再依赖谁 source 过 `set_env.bash`**；环境已有冲突值 → fail-closed |
| — | stage2 形态分派 | `standard` 走官方 `EXEC_NPU_CMD_EXT`；**`extended`（非四参）走手写两段式**；未知形态 fail-closed；manifest 无 `degradations` 台账 = 「没人核过」→ 门拒 |
| — | 单条 case 失败不再中断全量 | driver 逐 case try/except，失败进 `out_manifest.failed[]`（case_id + 逐字错误 + `error_kind`），跑完仍写 `complete` |
| — | `aclnn_tensor_format` | 缺省 `torch_npu_rank_default` 与历史逐字节相同；声明 `nd` 时生成 ND 转换（解决 op-plugin 按 rank 贴格式导致 rank-3 被 L2 拒成 161002）。⚠ `nd` 只在 `extended` 下实现 |
| — | **§5.11 精度真值口径** | 任务书写 GPU 一律解析为同族 CPU（`precision_policy.resolve_gpu_oracle_to_cpu`）。⚠ 与 §5.10 性质不同：5.10 是取消比较、条款按未验收挂账；5.11 是解析口径、条款**已被满足**、**不产生** gap |

---

## 3 · 下一步

按优先级，不是并列清单：

1. **过 5.7 审修门然后 push**。自上次 push（`6f77d30`）以来的 8 个 commit 一轮统一审：
   代码走 `cc-suite:audit-fix`、散文走独立 Codex 散文审（`codex exec -m gpt-5.6-sol -c model_reasoning_effort=low`）。
   ⚠ **不 merge**，push 完停下报状态。
2. **gitcode 镜像仍落后**（见 §5）。
3. 悬而未决那几条（§5）——都不是本轮阻塞项，但攒着会变贵。

---

## 4 · 坑（都是实测踩过的，别再踩一遍）

### 4.1 远端与并发

1. **SSH 有连接速率限制**。短时间开多条新连接会被 sshd 拒，一旦被拒**要等十几分钟**。
   因此：**绝不前台跑长命令**，一律 `nohup … &` 起在后台，然后每 60s 轮询一次结果文件。
   ⚠ **轮询用 `grep -q`，别用 `grep -c … || echo 0`**——后者会产出两行（`grep` 的 0 和 `echo` 的 0），
   把「还没跑完」误判成完成。同时复用 ControlMaster：
   `-o ControlMaster=auto -o ControlPath="$HOME/.ssh/cm/%r@%h:%p" -o ControlPersist=120m`。
2. **提交前必须在容器跑全量回归并与基线 failed nodeid 集合逐条比对**。
   本轮有一次 fail-open 就是 commit 时没跑全量带进去的。
   「本地 `py_compile` 过了」不算数——**容器 Python 3.11.15、本地 3.14**，PEP 701 语法在 3.11 上是语法错误。
3. **macOS 打包必须 `COPYFILE_DISABLE=1` 且排除 `._*`**，否则 AppleDouble 文件会污染容器里的测试
   （归档成员集合须严格等于「manifest 覆盖文件 + manifest 自身」的 allowlist）。

### 4.2 收据与工件

4. **整树 merkle 必须在 build 之前算**。build 会往源码树里写产物，事后再摘就摘到「源码 + 产物」，
   与 CP-A 记的那份字节永远对不上。`vendor_build_receipt.py` 的两段式（`snapshot-digest` → `emit`）
   就是结构性地杜绝这个错法——`emit` 不会自己去摘树。
5. **`run_workflow` 的 work 口径是 `<--out>/work`**（写死在代码里）。CP-A/B 的产物
   （`aclnn_preflight.json` / `source_facts.json` / `pr_facts.json` / spec / `taskdoc_caseset.json` /
   golden / 各 case 目录）**必须放这里**。放别处的后果是**静默走空**，不是报错——
   预检不在 `<work>/aclnn_preflight.json` 时 codegen 会退回 standard 形态并挂
   `stage2_form_unverified`，跑完了门才说「没核过」。已写进 `acceptance-workflow/SKILL.md` §1.1。
6. **预检工件必须用 `--out` 写**。`preflight_aclnn.py --out` 产的是内容寻址 envelope
   （`schema_version`/`domain`/`digest`/`payload`）；**stdout 重定向得到的是裸 payload**，
   下游 `read_artifact` 校 domain + digest 时当场拒。
7. **同源码同 build 命令，vendor `.so` 产物哈希不比特可复现**。receipt 的 ELF 摘要只证
   「**这次**装的是这个」，别把它写成可复现性证明。
8. **`vendor_build_receipt.py emit --build-argv` 必须写等号形式**（`--build-argv=--pkg`）。
   真实构建实参几乎全以 `-` 开头，分开写会被 argparse 当成另一个选项、当场 `expected one argument`。

### 4.3 仓形态

9. **checkout 目录名不能含 `aclnn_`**。ops-cv 的 `cmake/func.cmake` 对**绝对路径**做
   `EXCLUDE REGEX "aclnn_"`，目录名撞上就把 `op_api/<op>.cpp` 静默滤掉——
   **编译成功、安装成功、`nm` 里符号也在，直到 dlopen 才炸**。已固定用 `dut_src`。
10. **`cv2.GaussianBlur` 对 `[H,W,1]` 会 squeeze 掉最后一维**，而 NPU 输出恒与输入同 shape。
    golden 必须补回，且**只在 C=1 时触发**。
11. **磁盘**：950 机器的 `/home` 已满、根分区仅剩个位数 G，只有 Docker 数据卷有空间。
12. **subagent 的 worktree 默认基于 `origin/main`**，不是当前分支。要它先
    `git reset --hard <当前分支>`，否则一条测试都跑不了。
13. **agent 写的测试可能与它自己的实现打架**（因为按纪律它不跑测试）。合入前必须在容器里跑。
14. **zsh 不做默认词分割**。`for f in $FILES` 会把整串当成一个参数，用数组 `"${arr[@]}"`。

---

## 5 · 悬而未决

| # | 事项 | 说明 |
| - | --- | --- |
| 1 | **本轮 8 个 commit 未 push、未过审修门** | 见 §3.1。这是当前最该先做的一件事 |
| 2 | `reconcile_deliverables` 没接进硬门 | 现在是「文档要求跑、没有机器门逼你跑」。升硬门要改状态机，按 5.2 需先给方案 |
| 3 | 审计 Medium/Low 未修项 | 按 5.7 一轮即停，留在 `.cc-suite/audits/`（本轮两份：`audit-fix-20260805-051500-findings.md`、`audit-fix-20260805-062000-findings.md`）|
| 4 | `PASS(无性能要求)` 终态不可达 | 「性能」dim 写死在 `gen_cases` 用例模板里，与 spec 是否声明 `perf` 无关 → `perf_cases` 恒 > 0。修它要动模板 + 一批断言 `dims` 的测试 |
| 5 | gitcode 镜像落后 | `gitcode/main` 停在 `a400878`，落后 `origin/main`（`4d1544d`）|
| 6 | GaussianBlur 那 5 条 `golden_unavailable` 怎么处置 | 工具侧已如实记为空白结论。**要么换参考实现，要么由人裁定这批 case 不在验收范围**——两条都得人来定 |
| 7 | `taskdoc-to-spec.md` 仍未覆盖 `cpp_extension` 的完整抽取规则 | 本轮只把词表、`call_variants` 触发条件、`aclnn_tensor_format` 补齐；§1.3 整节的行文仍以 `aclnn_py` 为叙述主体 |
| 8 | CP-B0 对 GaussianBlur 任务书判 `NEEDS_USER` | 3 项阻断按契约**只能由人 `supplied` 补事实**，不能豁免 |

---

## 6 · 任务书 ↔ PR 的实质冲突（工具侧不消解，原样进报告）

沿用上一份交接，**除第 4 条已按 §5.11 改判**：

1. 任务书 §任务概述 / §1 / §7 都把 **OpenCV C++ 适配层**标为必选交付件，**PR 未交付**；
2. 任务书 §3.3 要求 in-place，PR 的 `op_api/aclnn_gaussian_blur.cpp` 用
   `CheckInplaceUnsupported` **明确拒绝**；
3. DUT 自身 ABI 不自洽：`dst` 在 stage1 是 `const aclTensor*`、stage2 是 `aclTensor*`；
4. ~~任务书 §6 主口径写 OpenCV GPU 真值、§4 与 §6 表格又写 CPU~~
   → **已按 AGENTS.md §5.11 改判**：GPU 写法统一解析为同族 CPU，该条款**视为已满足**、
   不再作为 gap 挂账；spec 里那条改写成**解析记录**（原文怎么写的 / 解析成了什么 / 依据本节）；
5. 任务书定义了 L1（CV_32F）一档，却在 §8 两次引用**从未定义的 L2**（CV_64F）。

⚠ 「以 OpenCV C++ 层为唯一验收基准」那句是在指定**参照物**，不是要求 PR 携带——
措辞按「交付件缺口」而非「基准搞错」。
