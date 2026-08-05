# OpRunway 交接 · 2026-08-04

> 取代 `dev-doc/oprunway-session-handoff-2026-07-26.md` 作为当前交接。旧的只作历史材料。
> 本文写给**下一个 session 的自己**：先读这份，再读 `AGENTS.md`，然后照 §3 开工。

---

## 0 · 一句话

GaussianBlur 的验收流水线**已经真机跑通一遍**（精度 24/24 pass、性能维挂起、`overall=BLOCKED`），
沿途修掉的工具链缺口已 push。**下一轮的目标是五项泛化能力**（见 §3），做完后再用新 workflow
重跑一次 GaussianBlur 验收。

---

## 1 · 当前状态（事实，非计划）

| 项 | 值 |
|---|---|
| 分支 | `worktree-oprunway18`，已 push 到 `origin`（GitHub），**未 merge** |
| HEAD | `4238e64`，自 `4d1544d`(origin/main) 起 **12 个 commit** |
| 工作区 | 干净，只有 4 个未跟踪的 bureau logbook 条目 |
| 单测 | 容器内 **1903 passed / 10 failed**；那 10 条在未改动 HEAD 上同样失败（root 身份下的 setenv 软链守卫 5 条 + bf16/dtype 4 条 + 1 条 `SUBFAILED(torch_parity)`），**与本轮改动无关** |
| 审修门 | 代码审（58 条 → 修 34）+ 散文审（36 条 → 修完 fact 与要害 contradiction）**都已过**，见 `.cc-suite/audits/` |

### 1.1 真机环境（已就绪，别重建）

细节见 `dev-doc/oprunway-real-machine-environment.md` §3（本轮刚整节重写过）。要点：

- **950 真机走容器**（旧记录说「无 Docker 权限、host 执行」，已失效）；
- 容器里 CANN 9.0.0 / Ubuntu 22.04.5 / **Python 3.11.15** / x86_64，8× Ascend950PR；
- 已装 numpy 1.26.4 · cv2 4.11.0 · torch 2.10.0+cpu · torch_npu 2.10.0 · pytest 9.1.1；
- 实际 ssh alias / 容器名 / 工作区路径**只在** `.oprunway/real-machine.env`（被 ignore）。

### 1.2 GaussianBlur 上一轮验收产物

在容器的报告根 `<工作区>/gb/reports/gb_run/`，含 `acceptance.json` / `verdict.json` /
`caseset.json` / `evidence.json` / `pr_facts.json` / `taskdoc_validation.json` 等。

```
precision_verdict = pass（24/24，fail 0）
perf_status       = blocked_wait_real_baseline
overall           = BLOCKED(验收门未过)
```

⚠ **这份产物已经过时**：`measure_only` 与交付件对账都是在它跑完**之后**才落地的。
要按新口径给结论，必须重跑。

---

## 2 · 已经做完的，不要重做

按「被真实报错逼出来」而非「设计出来」排序：

| 能力 | 一句话 |
|---|---|
| `aclIntArray*` 参数 | 工具链此前对**任何数组型参数**都 fail-closed |
| **stage2 真解析** | 旧实现把 argtypes 写死 4 参且不看 stage2——非标准形态是**静默错调**，不是报错 |
| 输出方向以 stage2 为准 | `const aclTensor* dst` 按 const 判会当成输入 → 零输出 |
| attr `float64` | aclnn_py 上**任何浮点 attr** 过去都走不通 |
| `opencv_cpu` 真值方法族 | 此前任何 CPU 第三方库真值都必然 tier4 blocked |
| `local_snapshot` 取源形态 | 无 `.git` 的快照，`head_sha` 落 **null**，绝不合成 hex |
| vendor 后缀 / build flag | 仓形态字段驱动（ops-cv 装 `_cv`；`--no_force` 会让 build.sh 退 1） |
| `--target-dir` / `--pr-snapshot` | 仓根一级算子目录探不到；无 git 快照产不出事实包 |
| `.npy` 魔数判形态 | **「legacy 单输出 + aclnn_py」这个组合在验收门上曾恒 FAILED** |
| `perf.mode="measure_only"` | 只测不比；口径只从 caseset 读、不信 perf_report 自报 |
| 交付件清单结构化 + 对账 | `delivery_scope` 产机器可读清单；`reconcile_deliverables.py` 逐条核归宿 |

---

## 3 · 下一轮目标（用户 2026-08-04 定）

**五项泛化能力全部落地 + 用新 workflow 把 GaussianBlur 验收跑通，即可停。**
是否 PASS 无所谓，重要的是流程走通。

### 3.1 gitcode 链接内容读取

任务书里散着一堆 gitcode 链接，现在**读不到内容**。本份任务书实测有这些：

```
cann/opbase/blob/.../ops_precision_standard/experimental_standard.md   ← 精度标准
HIT1920/AscendOpTest                                                   ← 精度测试工具
AscendTest/ATK                                                         ← 双标杆工具
cann/cann-competitions/.../resources/design_template.md                ← 设计文档模板
cann/cann-ops-competitions/.../resources/design_template.md
cann/cann-competitions/tree/.../tasklist                               ← 目录（需列目录）
cann/ops-cv/tree/master/gaussian_blur                                  ← 目录
org/cann/discussions/39
./self_test_case/gaussian_blur/                                        ← **相对链接**，见 3.4
```

**已有的地基**：`plugin/acc-common/fetch_source.py` 里已经有
`API = "https://api.gitcode.com/api/v5"`、`_token()`（读 `GITCODE_TOKEN`）、`_get()`、
以及按 contents API 取单文件的能力（`_GITCODE_HOSTS` 白名单也在）。

**要补的**：把「任务书正文里出现的 gitcode 链接」变成可解析、可取内容的一等能力——
至少要支持 `blob`（单文件）、`tree`（列目录）、**相对链接**（相对任务书自身路径解析）。
取到的内容须落成内容寻址产物并绑本轮任务书字节，否则下游无从核验。

⚠ 别把它做成「按算子名去猜某个目录」——判据只能是链接本身的结构。

### 3.2 「无性能对比要求」场景的统一归并

用户明确把**四类场景**归为同一类，一律**只输出 msprof 绝对耗时数据**、不做性能对比：

1. 对原算子新增数据类型支持；
2. 扩展 shape / rank；
3. 开发新算子；
4. **任务书中明确标明性能对标 GPU**。

**并且：这类场景明确使用 torch 封装接入。**

> ⚠ **这条改变了方向，务必想清楚再动手。**
> 上一轮 GaussianBlur 选的是 `runner_form = aclnn_py`（ctypes 直调），
> 而「torch 封装接入」在本仓对应的是 **`cpp_extension`**（独立 `torch.ops` C++ Extension 调用桥，
> DUT 仍是 PR 构建的 vendor `.so`，extension 只是测试桥——见 `AGENTS.md` §4）。
> 需要先判断：是把这四类场景的 `runner_form` 定为 `cpp_extension`，还是别的含义。
> **拿不准就先问用户**，别自己决定——这会推翻上一轮的选型理由。
>
> 已知障碍（上一轮探针记录）：`cpp_extension_codegen._ATTR_CPP_TYPES` 只有标量、**没有
> `IntArrayRef`**；它 emit 的 `EXEC_NPU_CMD_EXT` 宏假设标准两段式派发，对 GaussianBlur
> 那种 10 参 stage2 对不上。这两条要先解决。

**已有的地基**：`perf.mode="measure_only"` 已落地（只测不比、口径只从 caseset 读、
每条性能 case 仍强制要真实 `npu_us`）。第 2 项要做的是**让这四类场景自动落到该模式**，
而不是靠人在 spec 里手写——判据须来自 `change.kind` / 任务书性能条款形态等**结构字段**。

### 3.3 用任务书指明的 golden 接口做验证

任务书通常会点名真值口径（本份点的是 OpenCV，且 CPU/GPU 三处自相矛盾）。
现在 `golden.method_kind` 是人在 spec 里填的，**没有机制保证它就是任务书点名的那个**。

要做的是：从任务书抽出「指明的 golden 接口」→ 成为结构化事实 → 与 spec 的
`golden.method_kind` / `golden.py` 的 `GOLDEN_SOURCE` 逐字对账，不一致 fail-closed。

**已有的地基**：`precision_policy.verify_authorization` 已经能核「授权引文出自任务书快照」，
但它只证引文来源，**不证「这句该算 oracle_method 还是 impl_reference」**（模块自己写明的诚实边界）。

### 3.4 任务书提供精度 case 时，用它的

**任务书给了 case → 用任务书的，不自行生成。只有不给时才自行生成 + 用默认精度标准。**

本份任务书第 158 行：`精度自测用例参考[自测用例目录](./self_test_case/gaussian_blur/)`
——**相对链接**，要靠 3.1 的能力解析并拉取。

要做的：
- caseset 来源增加「任务书提供」这一档，与「自行生成」互斥且可机读区分；
- 任务书 case 须落成内容寻址产物、绑任务书字节，报告里如实标明 case 来源；
- 两档在 `coverage_strength` 上的表述必须不同——**用了任务书的 case 就不能再声称
  「1-wise + 白名单」那套覆盖强度**。

### 3.5 基于 3.1–3.4 改造 workflow

**泛化性是硬要求**（用户第 6 条 = `AGENTS.md` 5.1）：不得有针对具体算子的设计。
判据只能来自链接结构、任务书文本结构、spec 字段、`change.kind`、仓形态。

### 3.6 然后重跑验收

用新 workflow 对同一份任务书 + 同一个 PR 快照重跑。
**用 subagent、干净 session、干净工作目录**，避免被之前的工作影响。
不考虑 GPU、不考虑 ATK。精度用 workflow 默认标准。

---

## 4 · 坑（都是实测踩过的，别再踩一遍）

1. **本地 Python 3.14、容器 3.11.15**。PEP 701 语法（f-string 表达式跨越隐式拼接字面量）
   在 3.11 上是语法错误。**本地 `py_compile` 过了不算数**，权威检查必须用容器的 python3。
   历史上 `aclnn_driver.py:266` 就因此在 HEAD 上无法 import，而没人发现——因为以前只在
   Python 3.12 的机器上跑。
2. **SSH 有连接速率限制**。短时间开多条新连接会被 sshd 拒，一旦被拒要等十几分钟。
   每条命令都必须复用 ControlMaster：
   `-o ControlMaster=auto -o ControlPath="$HOME/.ssh/cm/%r@%h:%p" -o ControlPersist=120m`
3. **zsh 不做默认词分割**。`for f in $FILES` 会把整串当成一个参数——本轮因此白跑过两次并行任务。
   用数组 `"${arr[@]}"`。
4. **`cv2.GaussianBlur` 对 `[H,W,1]` 会 squeeze 掉最后一维**，而 NPU 输出恒与输入同 shape。
   golden 必须补回，**只在 C=1 时触发**。根因是 OpenCV 把通道折进 type、`Mat` 眼中
   「单通道」与「无通道轴」是同一件事。
5. **ops-cv 的 `cmake/func.cmake` 对绝对路径做 `EXCLUDE REGEX "aclnn_"`**。
   checkout 目录名含 `aclnn_` 会让 `op_api/gaussian_blur.cpp` 被静默滤掉——
   **编译成功、安装成功、`nm` 里符号也在，直到 dlopen 才炸**。已把 checkout 名改成 `dut_src`。
6. **磁盘**：950 机器的 `/home` 已 100% 满、根分区仅剩个位数 G，只有 Docker 数据卷有空间。
7. **agent 写的测试可能与它自己的实现打架**（因为按纪律它不跑测试）。合入前必须在容器里跑。
8. **subagent 的 worktree 默认基于 `origin/main`**，不是当前分支。要它先
   `git reset --hard <当前分支>`，否则它撞上 HEAD 上的 Python 3.11 阻塞、一条测试都跑不了。

---

## 5 · 悬而未决

| # | 事项 | 说明 |
|---|---|---|
| 1 | **3.2 的「torch 封装」到底指什么** | 若指 `cpp_extension`，会推翻上一轮 `aclnn_py` 的选型。**建议先问用户** |
| 2 | `reconcile_deliverables` 没接进硬门 | 现在是「文档要求跑、没有机器门逼你跑」。升硬门要改状态机，按 5.2 需先给方案 |
| 3 | 4 条审计 finding 只做到 partial | 共同原因：报告目录里**没有内容寻址的 spec 工件**，做不到「与经摘要绑定的 spec 原值交叉核验」。要补得先让 spec 进产物链 |
| 4 | `PASS(无性能要求)` 终态不可达 | 「性能」dim 写死在 `gen_cases` 用例模板里（`:915` / `:1976`），与 spec 是否声明 `perf` 无关 → `perf_cases` 恒 > 0。修它要动模板 + 一批断言 `dims` 的测试 |
| 5 | 审计 Medium/Low 24 条未修 | 按 5.7 一轮即停，留在 `.cc-suite/audits/audit-fix-20260804-002313-findings.md` |
| 6 | gitcode 镜像落后 | `gitcode/main` 停在 `a400878`，落后 `origin/main`。本轮只 push 了 origin |
| 7 | CP-B0 对本份任务书判 `NEEDS_USER` | 3 项阻断（真值口径三处矛盾、性能基线二选一无准确 API、性能口径缺 kernel-only/warmup/repeat）按契约**只能由人 `supplied` 补事实** |

---

## 6 · 任务书 ↔ PR 的实质冲突（工具侧不消解，原样进报告）

1. 任务书 §任务概述 / §1 / §7 都把 **OpenCV C++ 适配层**标为必选交付件，**PR 未交付**；
2. 任务书 §3.3 要求 in-place，PR 的 `op_api/aclnn_gaussian_blur.cpp:205` 用
   `CheckInplaceUnsupported` **明确拒绝**；
3. DUT 自身 ABI 不自洽：`dst` 在 stage1 是 `const aclTensor*`、stage2 是 `aclTensor*`；
4. 任务书 §6 主口径写 OpenCV **GPU** 真值，§4 与 §6 表格又写 CPU；
5. 任务书定义了 L1（CV_32F）一档，却在 §8 两次引用**从未定义的 L2**（CV_64F）。

⚠ 「以 OpenCV C++ 层为唯一验收基准」那句是在指定**参照物**，不是要求 PR 携带——
这一点上一轮我读错过一次，措辞已按「交付件缺口」而非「基准搞错」修正。
