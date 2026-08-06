# OpRunway：任务书自带用例/golden 吸收 + GaussianBlur 走通

> **v4 —— 目标导向重写。唯一目标：尽快让新 workflow 端到端跑通一次（需求 9/13）。**
>
> v3 是按 43 条加固型审计改出来的，**已经加固到偏离目标**。本轮 3 路并行重审
> （goal / blocker / coverage，gpt-5.6-sol·high）出 **65 条**（22 Critical / 37 High / 5 Medium / 1 Low），
> findings：`.cc-suite/audits/audit-fix-20260805-062000-findings.md`。
> 三路结论一致：**照 v3 执行会在 W-1/W2/W4/W8 提前 BLOCKED，既跑不完 169-case，也产不出需求 2 要的 msprof 数据。**
>
> v4 按「垂直切片 + 先实测再加固」重排，并把 v3 里 5 个「待拍板」中的 4 个**用证据消掉**。

---

## 0 · 用证据消掉的伪问题（v3 曾要你拍板）

| v3 待拍板 | 证据 | 结论 |
|---|---|---|
| 6.76 GiB 用例数据放哪；要不要先设计事务式分批协议 | `dev-doc/oprunway-real-machine-environment.md` §3.2：**Docker 数据卷 1.7 T / 剩 1.3 T**；`/tmp` tmpfs 376 G | **假两难**。直接在 Docker 卷下建干净执行目录，**W9 整项删除** |
| W8 provenance 死路（只有「取不存在的 PR head」或「BLOCKED」） | `source_provenance.py` 已有通用 `local_snapshot` 契约：`snapshot_only` 档 + `OPRUNWAY_ALLOW_DEGRADED_PROVENANCE` + 两侧 head **必须都是 null** + merkle 逐字相等 + `pr_head_unbound` 机读挂账 | **有第三条路**：vendor build receipt 按 `source.kind` 分流，复用这套，**不伪造 commit** |
| 任务书 case 缺 `border_type`，默认值从哪来 | 任务书 golden 调 `cv2.GaussianBlur` **省略** `borderType` → 其可执行语义就是 OpenCV `BORDER_DEFAULT` | **不用问**。落成有来源的常量绑定（绑原 golden 摘要 + OpenCV API 语义），wrapper 拒非默认值 |
| W5 要不要加第三个 measure_only ground | 需求 2 逐字：四类场景**统一**只输出 msprof 绝对耗时。ground 的非冗余适用面正是「任务书写了比值条款但改动属这四类」——v3 加的「互斥即 fail-closed」护栏把它掐死了 | **加 ground、删护栏**，改为**强制把原条款记进 gap**（与 §5.10 同一形态） |

剩一个真待办：工作区里 `perf_mode.py` 的 3 处未提交改动（上一轮 finding #27 的修法）——**保留、不回滚**，
从明确基线另开干净实现 worktree，不为取测试基线回滚它。

---

## 1 · 本轮真正的输入与已核事实

| 项 | 值 |
|---|---|
| 任务书 | `https://gitcode.com/cann/cann-ops-competitions/blob/master/04_tasks/01_community-task-2026/docs/202607/GaussianBlur_task_doc.md` |
| PR | `repos/ops-cv-TreamTik-feat-experimental-gaussian-blur`（**本地目录、无 `.git`**）→ `local_snapshot` / `head_sha=null` |
| 任务书自带件 | `self_test_case/gaussian_blur/`：`*_cases.json`（**169 条**）+ `*_golden.py`（OpenCV **CPU**）+ `*_prototype.json` |
| 现 spec | `runner_form="aclnn_py"`，params `src/ksize/sigma_x/sigma_y/border_type/dst`，`dtype float32` |
| 任务书 prototype | `self/out`，`type "float"`，attr `ksize_x/ksize_y/sigmaX/sigmaY`（**无 border_type**） |

**会真正卡住的四处硬门（已逐条读源码核实）**：

1. **PR head 强制有三处**，不是一处：`cpp_extension_driver.py:119`、`cpp_extension_adapter.py:283`、
   `validate_acceptance_state.py:824`。只改一处没用。
2. **cpp_extension 性能前置**：`cpp_extension_adapter.py:197-203` 要求**全部应裁精度 case 通过**才采性能。
   已知 5 条通道数 > `CV_CN_MAX=512` 的 case 很可能 golden 跑不通 → **msprof 零数据 → 需求 2 落空**。
3. **IO 名跨层不一致**：spec `src/dst/float32` vs prototype `self/out/float`。
   若按「名称逐字一致」对账，**169 条全部 identity_mismatch**。
4. **性能 case 选取**：`gen_cases.py:337-470` 只挑带「性能」维或匹配 include tag 的 case。
   169 条若只标精度维 → **零性能 case**，复现上一轮的性能挂起。

---

## 2 · 执行策略：垂直切片，先实测再加固

**原则**：先用最短路径产出一次真实的端到端产物，再拿实测阻断日志决定加固什么（需求 8/13）。
v3 那些「先建中央 registry / 全局仓规 / 沙箱工程 / 共享 ABI IR / 全矩阵测试」一律**推迟到首次跑通之后**。

**停止 TDD（需求 12）**：不写测试先行、不设全量回归前置门。测试只保留
「实现后针对性 smoke + 阻断性负例」，全套 pytest 放到走通之后或准备 push 时。

---

## 3 · 三路并行（需求 10 · ultracode fan-out）

三条 lane 互不写同一文件，可并行；在 L4 汇合。

```
L-A  取材与用例          L-B  provenance 与 ABI        L-C  性能口径
 A1 gitcode 链接取材      B1 vendor receipt 按          C1 四类场景 → measure_only
 A2 caseset 识别+映射IR      source.kind 分流（三处）      （加 ground、删互斥护栏）
 A3 golden wrapper        B2 cpp_extension extended     C2 性能 case 从 taskdoc
 A4 materializer             stage2 + int_array            精度 case 选取
                                                        C3 解「精度全过才采性能」门
        └──────────────┬──────────────┴──────────────┘
                       ▼
       L4 汇合：数据通道 → 状态机 → checkpoint commit → 真机跑 → 实测反馈
```

真机连接、共享报告目录、最终裁决**仍串行**，避免并行写冲突。

---

## L-A · 取材与用例

### A1 · gitcode 链接取材（需求 1）

新增 `plugin/acc-common/taskdoc_links.py`（stdlib-only）。**最小可用面**：

- 抽取：markdown `[..](..)` + **裸 URL** 两种；
- 分类受控词表：`gitcode_blob | gitcode_tree | gitcode_relative | gitcode_repo_root |
  gitcode_merge_request | gitcode_discussion | external | unknown`；
- 取材：blob → contents API；tree / relative-dir → 列目录（**深度 2 有界**）；
  复用 `fetch_source._get` / `_repo_file` / `_GITCODE_HOSTS`；
- 简单资源上限：文件数 / 单文件字节 / 总字节 / 超时；超限落 `resource_limited`；
- 每条记内容 hash；产物绑 `taskdoc_snapshot_sha256` + `source_facts_sha256` + **解析出的 commit SHA**
  （防任务书变更后热恢复复用旧结果）；
- **三态覆盖（需求 1 字面要求）**：每条链接必须落
  `fetched` / `explicitly_excluded` / `unsupported_recorded`，**没有 ok 兜底**。
  - `explicitly_excluded` —— 用户 2026-08-05 逐字排除的（ATK / AscendOpTest / design_template ×2 /
    tasklist），须记下**是谁、何时排除的**，不是脚本自己认定的；
  - `unsupported_recorded` —— discussions（v5 REST 无端点、HTML 为 SPA 空壳）。
    ⚠ **按用户口径 4：如实登记但不阻断**。v4 一度写成 `blocking_unsupported`，那会让 CP-A 当场停死，
    与用户已定口径矛盾——已更正。「不阻断」不等于「不记」：它必须出现在链接清单与 CP-E 报告里。

**推迟**：header 认证迁移、通用重定向策略、逐 blob 全量元数据、本地任务书的相对资源解析、producer-logic hash。

### A2 · caseset 识别 + **接口映射 IR**（需求 4）

新增 `plugin/acc-common/taskdoc_caseset.py`，产**版本化、内容寻址**的 `taskdoc_caseset.json`。

**识别**：目录来自 A1 的 `tree`/`relative` 结果；须有顶层数组 JSON（每项含 `case_name`/`op_name`/
`input_desc`/`output_desc`）+ 同目录 AST 可证的 `expect_func`（只允许同目录 basename，拒 `..`/绝对路径）。

**接口映射 IR（v3 最致命的漏，必须有）**：不能要求跨 API 层符号名逐字相同。
建一份摘要绑定的映射 IR，**同时**描述 input / output / attr：

- 按 **role + 顺序 + 数量 + canonical dtype + shape 约束** 对账，产显式映射
  `self→src`、`out→dst`、`float→float32`、`ksize_x/ksize_y→ksize[0]/ksize[1]`、`sigmaX→sigma_x`…；
- 任务书 case 缺的 attr（`border_type`）→ 从**指定 golden 的可执行语义**派生默认值
  （`cv2.GaussianBlur` 省略 `borderType` = `BORDER_DEFAULT`），绑原 golden 摘要记录来源；
- 三源（prototype × 任务书 × header）仍无法确定时才停下问用户；
- 映射规则是**结构驱动的通用机制**，不写算子分支（需求 6）。

**字段严校只管语义字段**（v3 的「所有嵌套键全覆盖」会被 `case_path`/`golden_path` 这类元数据误拦）：

- 会改变执行语义的字段（`data_path`、dtype、shape、attr、`err_threshold`）严校；
- 未知元数据**保留 + 告警**，不阻断；
- **`data_path` 非空 → BLOCKED**（钉死输入我们暂不支持，不许静默换成现造随机值）。

**需求 4 的硬边界（v3 违反了）**：

> **只有「完整扫描证明任务书没有提供 case」才允许走 `generated`。**
> 明确提供了、但识别失败 / 字段不支持 / 身份冲突 / 资源受限 → **BLOCKED 并保留候选摘要与原因**，
> **绝不回退自生成**。回退等于再造一次上一轮「流程看似通过、实际绕过任务书用例」的错误产物。

### A3 · golden wrapper（需求 3）

任务书 `*_golden.py` **冻结为只读内容寻址输入**，不改名、不覆盖 `<ops_root>/<op>/golden.py`
（目标已存在且摘要不同 → 停，覆盖须单独授权）。

另**生成**通用 wrapper 提供引擎要求的 `golden_fn(inputs, attrs)` + `GOLDEN_SOURCE` +
`GOLDEN_PROVENANCE` + `GOLDEN_CONTRACT`（`gen_cases.py:567-575` 强制这三个，任务书那份一个都没有）。
wrapper 靠 A2 的映射 IR 做参数变换，**泛化部分在 IR、不写算子分支**。

- 本轮只支持**全 caseset 单一 `expect_func`**（169 条同指一函数），多函数形态显式拒；
- ⚠ **`[H,W,1]` 通道轴必须补回**（交接坑 4，`oprunway-session-handoff-2026-08-04.md:177`）：
  `cv2.GaussianBlur` 对 `[H,W,1]` 会 squeeze 掉最后一维，而 NPU 输出恒与输入同 shape。
  任务书自带的 golden **已经自己处理了**（`if out.shape != img.shape: out = out.reshape(img.shape)`），
  wrapper **不得重复处理或抵消它**——须核对冻结件确已覆盖，并对 rank-3 且 C=1 的 case 做一次形状对拍；
- 执行隔离**降级为最小可用**：独立 worker 进程 + 超时 + 退出码 + 输出大小限额。
  完整沙箱（无网络/只读输入/容器级隔离）**推迟**——它是安全加固工程，不是走通的必要条件。
  ⚠ `gen_cases.py:502-587` 与 `:2807-2842` 都在主进程 import/调用 golden，真要隔离得用**长驻 worker**
  （最大单条约 777 MiB，逐 case 起进程会 OOM/极慢）。

**删除 v3 的 GPU→CPU 全局降级层与其五类测试** —— 与「不考虑任何 GPU 和 ATK」无关，
且需求 3 要的是「用任务书**指明**的接口」，不是「建立全局 CPU 覆盖规则」。
本轮直接消费任务书自带的 CPU golden；正文 §6 的 CPU/GPU 矛盾**写进报告**即可。
（全局 §5.11 仓规**推迟**到走通之后。）

### A4 · materializer（v3 完全没有，Critical）

169 条只有 `shape` / `data_type` / `value_range`，**没有输入字节**（无 `data_path`）。
v3 说「在 `_plan()` 前分叉」，但**没定义怎么把描述变成确定性的输入数据** —— 实现到这一步会立刻返工。

定义 taskdoc 专用确定性 materializer：

- `float → float32`；`value_range` 端点语义与分布明确；
- **seed 由「任务书摘要 + 单条 case 内容摘要」派生**（可复现）；
- shape / rank 校验；
- 把 seed、分布策略、生成器版本、逐 case 内容摘要**写进 caseset**——否则不能声称「用了任务书的 case」。

**阈值**：`err_threshold` 两值的权威顺序必须查明并写进规范化产物的 `source_schema/version`
（本例两值都是 `1e-3`，恰好看不出顺序错；换成不等值就会误判）。校长度/类型/有限性/非负，
显式拒 NaN/Inf（Python `json` 默认接受非标准 `NaN`/`Infinity`）。

⚠ **本轮裁决按需求 7 用 workflow 默认精度 policy**；任务书阈值先规范化落盘、**不参与本轮裁决**，
报告写明「任务书给了 1e-3，本轮按需求 7 用默认口径」。

---

## L-B · provenance 与 ABI

### B1 · vendor build receipt 按 `source.kind` 分流（解 W8 死路）

把 receipt 契约升级为分流，**三处校验同步改**（缺一仍会被挡）：
`cpp_extension_driver.py:119` / `cpp_extension_adapter.py:283` / `validate_acceptance_state.py:824`。

| `source.kind` | 绑定内容 |
|---|---|
| `git_pr` | 40 位 head + repo（**行为不变**） |
| `local_snapshot` | `head_sha=null`（**不伪造 commit**）+ snapshot scope + 整树/子树 Merkle + 构建 argv + vendor ELF 摘要 + 显式 `pr_head_unbound` 挂账 |

复用 `source_provenance.bind()` 的既有核验模式（它已经把「两侧 head 必须都是 null、
谁合成 40 位 hex 就当场报错」写死了，正是我们要的）。

⚠ **先统一三处命名，否则接不上**（verify 抓到）：仓内同一概念现在有三套词——
`provenance_kind = gitcode_pr | local_snapshot`（事实包侧）、
`source_mode = git_fetch | local_snapshot`（执行配置侧）、
本项拟用的 `source.kind`（receipt 侧）。**必须先定 receipt schema 版本与三者的映射表**，
再动三处校验；否则「local_snapshot 已支持」会在词表边界上再断一次。

### B2 · cpp_extension extended stage2 + 数组 attr

- **不新增 `int64[]` dtype**：现 spec 用 `dtype:["int64"]` + list 值表达数组，数组性由 `_attr_ctype`
  从**值结构**派生。复用 canonical `int_array`。
- **10 参 stage2 不是靠调参数顺序解决的**：`dev-doc/oprunway-gaussianblur-support-plan.md:78` 已记
  `EXEC_NPU_CMD_EXT` 假设**标准四参** stage2，GaussianBlur 是 **extended 10 参**。
  codegen 按 preflight 的 `stage2_form` 选**受验证的 extended dispatcher**；standard 宏仅限 standard；
  未知形态 fail-closed。
- **不做全仓共享 ABI IR 迁移**：让 codegen **直接消费现有 `preflight_aclnn` / `aclnn_runtime` 的签名解析结果**，
  不同步重写 `gen_cases.aclnn_call` / adapter / manifest 等已经在工作的调用槽契约。

---

## L-C · 性能口径（需求 2）

### C0 · `runner_form` 的确定性派生与 spec 落地（verify 抓到 v4 只写了目标、没写实现）

需求 2「这种场景明确使用 torch 封装接入」必须有**实现落点**，不能只在 D5 声明目标：

- 受控 `change.kind` 四类命中 → **spec 生成时显式写入 `runner_form: "cpp_extension"`**；
- 接口预检（`preflight_aclnn`）只决定「支持 or BLOCKED」，**不得静默改选 `aclnn_py`**；
- `run_workflow` 仍只从 `spec.runner_form` 派生 mode（单一真源不变）；
- 本轮须有明确工作项：**把 `plugin/samples/specs/gaussian_blur.spec.json` 的
  `runner_form` 从 `aclnn_py` 改为 `cpp_extension`**，并补齐该形态所需的 `call_variants` 等字段。
  ⚠ 这会改变该 spec 的产物，**v3 那句「gaussian_blur.spec.json 字节不变」作废**。

⚠ **词表口径要写准**（verify #9/#47）：需求 2 的四类里，前三类
（新增 dtype / 扩 shape·rank / 新算子）是 **`change.kind` 的值**，第四类
（任务书标明性能对标 GPU）是**任务书条款**，两者不同源。`derive_mode` 必须分两支判、
再取并集，不能笼统写成「四类 change.kind」。`change.kind` 需新增 `extend_shape`（现词表无格子）。

### C1 · 四类场景 → `measure_only`（按需求 2 直接实现，不再拍板）

- `AGENTS.md` §5.10 增第三个授权情形 `change_class_no_perf_comparison`，同步扩
  `perf_mode.MEASURE_ONLY_GROUNDS`；仍要求 `cite`/`quote`/`taskdoc_snapshot_sha256` 锚与 gap 记账。
- **删除 v3 加的「与比值条款互斥即 fail-closed」护栏** —— 它恰好把该 ground 唯一非冗余的适用面掐死了。
  改为：任务书若写了比值/绝对门限/吞吐条款，**该条款强制记进 `task_pr_gaps` 标「未验收」**，
  仍只产 msprof 绝对耗时，**禁止 baseline、ratio、达标宣称**（与 §5.10 同一形态）。
- `derive_mode` 从**受控 `change.kind` 四类 + 任务书 GPU 条款**派生；其余条款保持现行为或报 unsupported。
- **不新建版本化性能条款 contract、不做迁移与状态机接口**（推迟）；现有 `perf_mode` 已够用，
  GaussianBlur 已经在用 `measure_only` + `gpu_comparison` authorization，`run_workflow.py:84-109` 会校它。

### C2 · 性能 case 从 taskdoc 精度 case 选取（否则必然零数据）

现 `gen_cases.py:337-470` 只挑带「性能」维的 case。169 条规范化后若只标精度维 → 零性能 case。
→ 新增 `perf.case_source=precision_cases`：把**成功物化且过硬件/资源门**的 taskdoc 精度 case 放进候选池，
再套现有 `max_cases` / 大小 shape 分档 / 采样规则；**性能 case ID 必须引用原精度 case ID**。

### C3 · 解开 cpp_extension 的「精度全过才采性能」硬门（Critical）

**性能总门有两层，只解一层没用**（verify 抓到 v4 只解了 adapter 那层）：

| 层 | 位置 | 现行为 |
|---|---|---|
| ① adapter | `cpp_extension_adapter.py:197-203` | 全部应裁精度 case 通过才采性能 |
| ② workflow | `run_workflow.py:416` 附近 | 任何精度 FAIL/BLOCKED 即产**零数据** `perf_report` |

只要一条 taskdoc golden 跑不通或 DUT 精度失败，需求 2 的 msprof 数据就**完全没有**。
→ **两层都要为 `measure_only` 加分流**：从已成功执行且精度可判的 case 中选性能子集继续采集，
**完整保留失败/未验证的分母**；性能报告**不得**据此宣称精度或任务书整体通过。
（`measure_only` 本就不产比值裁决，性能与精度解耦是这一档的应有之义；`ratio_gated` 行为不变。）

---

## L4 · 汇合

### D1 · `golden_unavailable` 做成一等状态（Critical）

v3 说「保留 case 并 BLOCKED」，但**没有对应的数据模型**：`gen_cases.py:2807-2811` 任一 golden 异常
会直接中断全量生成；`validate_acceptance_state.py:687-700` 要求每 case 有 golden，
`:719-726` / `:864-891` 要求证据一一对应，`:1283-1311` 还会实际读 golden/output。

→ 新增一等状态：case 身份仍写进完整 manifest、**允许无 golden 文件**；
执行证据写 `not_run/golden_unavailable`；Task1/Task2 门判其为 **BLOCKED 而非结构损坏**；
**其余可执行 case 继续跑**，最终仍产出可核验的 acceptance 产物。
（v3 的「任一 unavailable 就整体 BLOCKED」会让流程在性能采集前停死。）

### D2 · caseset 的数据通道（v3 没命名，会立刻返工）

`run_workflow.py:335-337` 只把 spec + 工作目录传给 `gen_cases`；`gen_cases.py:2735-2788` 也只从 spec 取数据。
→ spec 只引用 `taskdoc_caseset.json` 的摘要/逻辑身份；给 `run_workflow` / CLI / dry-run ledger / `gen_cases`
**加显式输入**，并绑 `taskdoc_links.json` + prototype + 原 golden + 映射 IR 的摘要。

### D3 · CP-A..E 状态机（v3 漏了 CP-C / CP-D —— 正是会卡住的两处）

`plugin/skills/acceptance-workflow/SKILL.md`：

- CP-A 落 `taskdoc_links.json`；
- CP-B / dry-run 绑定规范化 caseset；
- **CP-C**（`SKILL.md:160`）要求生成完整 caseset 与信任收据 → 须认新 taskdoc case 身份、映射与
  `golden_unavailable`；连带 `verify_aclnn_harness.py:278-350` 的 caseset 绑定逻辑；
- **CP-D**（`SKILL.md:169-177`）仍要求精确 40 位 head → 须按最终执行路径认 `local_snapshot`；
- CP-E 报告如实标注：case 来源、golden 来源、`golden_unavailable` 明细、未按原口径验收的条款、`pr_head_unbound`。

### D4 · checkpoint commit（需求 11）

L4 集成通过后、真机跑之前，打一个**只含本任务改动**的本地 checkpoint commit（**不 push**）。
先隔离工作区已有的 `perf_mode.py` 改动（保留、不卷入）。这也让下一步能真正从干净 worktree 起。

### D5 · 真机跑（需求 7）

- **subagent + 干净 session + 干净工作目录**：checkpoint commit 后开新 session；
  经 acceptance workflow dispatch subagent；本地用**新报告根**；
  远端在 **Docker 卷下、且不在 `OPRUNWAY_MACHINE_PROTECTED_ROOTS` 之内**的新目录；
  **禁止携带上一轮 caseset/golden/receipt**，只复用只读源码快照。
- 真机 build/run 前须有独立确认点（§5.2/§5.3），并先读 protected roots。
- 磁盘：运行前用**真实物化图**在目标挂载点做一次 `df` + 内存门；1.3 T 可用，不需要分批删除。
- 不考虑任何 GPU / ATK；精度按 workflow 默认口径。
- ⛔ **只能用 `cpp_extension`，不许降级**（用户 2026-08-05 明示）：
  **不存在** `aclnn_py` 退路。B1（receipt 按 `source.kind` 分流）与 B2（extended 10 参 dispatcher +
  `int_array`）因此**从「并行 lane」升级为关键路径硬前置**——它们不通，本轮就跑不出验收产物，
  正确处置是**如实报阻断点**，不是换通路。
  连带：`runner_form` 必须有**确定性派生 + spec 落地**（见 C0），不能只在文字上声明目标。

### D6 · 实测反馈闭环（需求 8 —— v3 完全没有）

读本次真实产物与**首个失败点**，区分「workflow/harness 缺陷」与「DUT 真实失败」；
**只修前者**，用同一份冻结输入重跑。**一轮有界优化即可**，DUT 的真实失败不强修。

---

## 4 · 测试（需求 12：停止 TDD）

不写测试先行，不设全量回归前置门。本轮只留：

- **A1**：真实成功 fixture 各一 + HTTP 失败一个负例 +
  **「任务书明确提供 caseset 但不可解析 → 必须 BLOCKED、不得 fallback」一个负例**（需求 4 的机器护栏，必须有）；
- **A2/A4**：`taskdoc_caseset.json` 摘要漂移一个负例；
- **C1**：四类派生的代表项 + GaussianBlur `measure_only` 实际产出；
- **B2**：GaussianBlur codegen 编译 + 一次 extended 真调用 + 一个 standard 小见证（防 Median 回归）；
- 实现后跑**相关模块**测试 + 语法/import smoke（⚠ 容器 Python 3.11.15，**本地 `py_compile` 不算数**）。

**推迟到走通之后**：17 个 sibling 覆盖表、四类留出集、每个 gap kind 四类拒绝测试、
全性能分类矩阵、11 份 spec 全量回归、完整对抗 fixture 矩阵、全套 1900+ pytest、push 前审修门。

---

## 5 · 明确推迟（不是取消）

首次跑通并拿到实测反馈之后再做：

- **中央 gap registry**：本轮只加真正进裁决的少量 kind，且**不反转 unknown→BLOCKED**
  ——`median.spec.json:109-115` 等现存条目无 `kind`，`validator.py:613-618` 与
  `validate_acceptance_state.py:253-267` 现在容忍自由文本；反转会先炸自己；
- 全局 §5.11 仓规、完整 golden 沙箱、共享 ABI IR 全仓迁移、本地任务书相对资源、
  性能条款完整词表（绝对门限/吞吐）、17 sibling 研究、push 前审修门。

---

## 6 · 已授权 / 已封死（用户 2026-08-05）

| # | 事项 | 决定 |
|---|---|---|
| 1 | 真机 build/run（§5.2） | ✅ **已授权**。仍须先读 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`、在 Docker 卷下新建执行目录 |
| 2 | 需求 2 的降级出口 | ⛔ **封死**。只能 `cpp_extension`，**不许退回 `aclnn_py`**。B1/B2 因此是关键路径硬前置；打不通就如实报阻断点 |

**无待拍板项。**

---

## 7 · 跑通的最短阻断点（verify 给的排序，按当前决定重排）

1. **B1 · provenance 契约**：先统一 `provenance_kind` / `source_mode` / `source.kind` 三套词并定 receipt schema 版本，
   再改 driver / adapter / acceptance gate 三处。**降级出口已封死 → 这是第一硬门。**
2. **B2 · extended 10 参 dispatcher + `int_array` codegen**：必须真编译 + 真调用一次。
3. **C0 · `runner_form` 派生 + `gaussian_blur.spec.json` 改 `cpp_extension`** 并补 `call_variants`。
4. **C3 · 两层性能总门**（adapter + `run_workflow`）都要为 `measure_only` 分流，否则最终仍是零数据 perf。
5. **A2/A4 · 映射 IR + materializer**：没有它们 169 条进不了引擎。
6. **D1 · `golden_unavailable` 一等状态**：否则 5 条 C>512 会让全量生成中断。

---

## 8 · 落地后须补（仓规 §5.6）

`dev-doc/oprunway-changes-brief.md` 顶部追加倒序摘要 —— v4 一度漏排，已补记于此。
