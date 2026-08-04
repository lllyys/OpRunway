# GaussianBlur 执行方向确认单

> 对应 `doc/oprunway-execution-direction-review-checklist.md` §3.1 的人读确认单。
> **本文不产生验收结论**，只讲清「这一轮到底怎么验的、每步产出了什么、还有什么没定」。
> 数据全部来自 2026-08-03 真机实跑的产物，非推断。
>
> ⚠ 该确认单在 workflow 里**还没有对应节点**（checklist 建议的 CP-B2 尚未实现），
> 所以这份是**事后手工补的**——也就是说，本轮是「先跑完才看清方向」，
> 而不是 checklist 设想的「先确认方向再跑」。这本身就是最大的一条待改进项。

---

## 1 · 流程全景：这一轮实际走了什么

每一格都是真实产物，落在报告根 `reports/gb_run/`。

| #  | 阶段               | 脚本                             | 吃什么                            | 吐什么                                                              | 实际结果                                                                           |
| -- | ---------------- | ------------------------------ | ------------------------------ | ---------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| 1  | **CP-A 取源**      | `fetch_source.py`              | 任务书 URL + PR 快照目录              | `pr_facts.json`<br>`source_facts.json`<br>`task_doc.snapshot.md` | `completeness=snapshot_only`<br>29 个文件（全在 `gaussian_blur/` 下）                  |
| 2  | **CP-B0 任务书输入门** | `validate_taskdoc_input.py`    | `task_doc.snapshot.md`         | `taskdoc_validation.json`<br>`taskdoc_validation_receipt.json`   | **`NEEDS_USER`（exit 2）**<br>18 项里 14 satisfied / 2 missing / 2 ambiguous       |
| 3  | **CP-B1 抽 spec** | *(本轮手写)*                       | 任务书 + PR 事实                    | `gaussian_blur.spec.json`                                        | 手写，未经 CP-B2 确认                                                                 |
| 4  | **CP-B2 方向确认**   | **不存在**                        | —                              | —                                                                | ⚠ **这个节点还没实现**，就是本文档                                                           |
| 5  | **真值**           | `golden.py`                    | —                              | `<ops_root>/GaussianBlur/golden.py`                              | OpenCV CPU `cv2.GaussianBlur` 4.11.0                                           |
| 6  | **CP-C0 静态接口门**  | `preflight_aclnn.py`           | `source_facts` + spec + header | `work/aclnn_preflight.json`                                      | `READY_WAIT_NPU_TRUST_GATE`<br>**stage2 判为 `extended`（10 参）**                  |
| 7  | **用例生成**         | `gen_cases.py`                 | spec + golden                  | `caseset.json`                                                   | **24 例**                                                                       |
| 8  | **CP-C 真机信任门**   | `verify_aclnn_harness.py`      | caseset + spec + preflight     | `work/aclnn_harness_trust.json`                                  | `TRUSTED_FOR_CP_D`<br>见证 1/24 真机跑通并与 golden 对拍 pass                            |
| 9  | **Task2 真机执行**   | `run_workflow --mode aclnn_py` | caseset                        | `evidence.json` + 逐 case `work/<case_id>/`                       | 24 例全部真机执行                                                                     |
| 10 | **精度裁决**         | `validator.py`                 | caseset + evidence             | `verdict.json`                                                   | **pass，24/24，fail 0**                                                          |
| 11 | **Task3 性能**     | `perf_compare.py`              | perf 计划                        | `perf_report.json`                                               | **无基线 → 挂起**                                                                   |
| 12 | **证据完整性门**       | `validate_acceptance_state.py` | 全部产物                           | `acceptance.json`                                                | task1 ✅ task2 ✅ **task3 ❌**<br>`BLOCKED(验收门未过)` / `BLOCKED_WAIT_REAL_BASELINE` |

## 2 · 绑定链：凭什么说「跑的就是这份东西」

这是整条流水线最要紧、也最容易被忽略的一层。每份产物都是**内容寻址信封**
（`{digest, domain, payload, schema_version}`），后一道门逐字核前一道门的摘要，任一处漂移即停。

```
任务书字节 ─sha256─► task_doc.snapshot.md
                          │
PR 快照 ─merkle─► source_facts (digest 5b04c0c9…)
                    │  provenance_kind = local_snapshot
                    │  pr_head_sha     = null          ← 没有上游 commit 可绑
                    │  snapshot_merkle = f536077e…     ← 算子子树，29 文件
                    │  snapshot_scope  = gaussian_blur
                    ▼
             CP-C0 preflight  bindings{ source_facts_digest, spec_sha256=c955f3…,
                                        runner_form=aclnn_py, 上面四项原样带下 }
                    ▼
             CP-C  trust      bindings{ caseset_sha256=9c56f3ed…,
                                        execution.config{ build_args, device=7,
                                          snapshot_sha256=203d4b77…（整仓）},
                                        vendor ELF sha256 }
                    ▼
             Task2 真机执行 → evidence → validator → acceptance
```

**两个 merkle 的区别要分清**（这轮踩过坑）：

|           | 值           | 范围                     | 谁在用                    |
| --------- | ----------- | ---------------------- | ---------------------- |
| 子树 merkle | `f536077e…` | `gaussian_blur/`，29 文件 | CP-A / CP-C0 / CP-C 对账 |
| 整仓 merkle | `203d4b77…` | 整个仓，2565 文件            | 真机取源段校验快照未被改动          |

scope 不同的两个摘要**不可比**，直接比必然对不上——所以 scope 本身也进了收据。

**红线**：`pr_head_sha = null` 一路透传，`acceptance.json` 全文无任何 40 位 hex。
merkle 只证「跑的就是这份字节」，**不证**它等于任何上游 commit。
任何结论都不得声称已绑定 PR head。

---

## 3 · 逐项确认（对照 checklist §4）

### 4.1 验收范围与缺口处理 ⚠ **待确认**

- 验收 dtype：**只有 CV\_32F**（任务书 §2.3 分级表只定义 L1 一档）。
- **任务书自身不一致**：§2 参数表列了 `CV_8U/CV_16U/CV_16S/CV_32F/CV_64F` 五种 depth，
  §2.3 只定义 L1，而 §8 又要求描述「CV\_64F **L2** 策略」——**L2 被引用两次却从未定义**。
- **交付件缺口**：§任务概述要求「基于 Ascend C Kernel + **OpenCV C\++ 适配层**」实现，
  §1 要求 OpenCV C\++ 层接口「必选，逐字对齐」，§7 分层把它列为必选——**PR 未交付该层**。
  本轮验的是 aclnn 层对 OpenCV CPU **库**的数值一致性；
  「适配层接口逐字对齐」这一项**未验**。
- 其它未覆盖：TC-03..TC-11 定向用例、in-place（PR 用 `CheckInplaceUnsupported` 显式拒绝）。

**要你定**：这些缺口是立即阻断，还是「先跑已实现部分但最终不得 PASS」？
本轮实际按后者执行，但这是我替你选的，未经确认。

### 4.2 DUT 与交付形态 ✅ 已明确

```
交付工程：ops-cv 仓根一级 gaussian_blur/（29 个文件）
DUT     ：本轮构建的 vendors/oprwgb_cv/op_api/lib/libcust_opapi.so
测试桥  ：通用 Python ctypes harness（aclnn_py），不是交付物
```

### 4.3 接入执行路线 ✅ 已明确

`runner_form = aclnn_py`。选它的理由：它已经拥有 caseset / golden / 输出采集 / workflow 接口，
只缺几个明确的 ABI slot；而 `cpp` 路的核心产物是一份 per-op `runner.cpp`，撞 AGENTS.md 5.1。

本轮为它补齐的 ABI 能力：`aclIntArray*` 参数、**stage2 真解析**（本算子是非标准 10 参）、
输出方向以 stage2 的 const 限定符为准。

### 4.4 Golden 真值 ⚠ **CP-B0 判 `ambiguous`，待确认**

当前用：OpenCV **CPU** `cv2.GaussianBlur`（容器内 4.11.0），单 API 直接真值，不需人核。

CP-B0 机读结论逐字如下：

> 任务书对真值来源给出三处互相冲突的说法：§4 功能比对写「OpenCV CPU（同版本）」；
> §6 真值生成方式写「以 OpenCV **GPU**（同版本、`cv::GaussianBlur`）为标杆」；
> §6 精度策略表 CV\_32F（L1）又写「对标 OpenCV CPU」。CPU 与 GPU 实现不保证逐位一致，
> 究竟以哪一个为精度真值直接决定裁决结果。

**要你定**：CPU 还是 GPU。本轮按你「不考虑 GPU」的口径走了 CPU。

### 4.5 特殊语义 ⚠ **CP-B0 判 `missing`**

任务书对 CV\_32F 下的 NaN/Inf 行为**没有规定**。
而 `operator_class=floating_compute`（如实填写）会**强制**铺出 inf/-inf/nan 三条 case。
这三条本轮**都 pass**——但任务书既然没规定期望行为，这个 pass **不构成功能结论**。

高斯是 5×5 **可分离**两趟加权求和，一个非有限值会沿行、列两趟污染整个邻域，
NPU 与 OpenCV 的累加顺序/中间截断不同，结果在语义上本就不具可比性。

### 4.6 精度标准 ✅ 已确认（你口头拍板「默认即可」）

| 项             | 值                                                    |
| ------------- | ---------------------------------------------------- |
| standard      | `ascendoptest_default`                               |
| fp32 阈值 / 错误率 | `1e-4` / `1e-4`                                      |
| 来源            | `precision_policy._AOT_TABLE` 的**工具缺省值**，**不是任务书给的** |

任务书未给任何数值阈值——这一项按「工具默认」记，属**推断口径**。

### 4.7 用例范围与预算 ⚠ 部分为方案选择

- **24 例**，由通用正交网格铺出：shape 阶梯 × attr 笛卡尔 × 强制特殊场景。
- attr 两轴：`ksize ∈ {[5,5],[3,3]}` × `border_type ∈ {1, 4}`，sigma 钉死 1.2。
- ⚠ `attr_matrix` 是「每 attr 取值并集再全笛卡尔」，**不是按行展开**——写 2 行会展开成 4 组。
- 输入值域 `uniform[-5,5]`，**不是非负图像域**——报告不得声称覆盖真实图像分布。
- ⚠ **任务书点名的 TC-03..TC-11 一条都没对应上**。spec 里也没有字段能声明「点名用例」。

### 4.8 性能 baseline ⚠ **CP-B0 判 `ambiguous`；本轮无数据**

任务书写「OpenCV CUDA（A100）**或** OCL GPU」二选一、无准确 API、未说明异机对比如何成立。
按你的全局口径（AGENTS.md 5.10）本轮**不做 GPU 对比**。

### 4.9 性能方法与达标标准 ⚠ **CP-B0 判 `missing`；本轮无数据**

任务书只给了一个 `0.45×`，kernel-only / 端到端、统计量、warmup / repeat **全缺**。

spec 整块省略了 `perf` 字段，但 `gen_cases` 仍把 24 例都打上性能维，
Task3 走 `_real_baseline_or_blocked` → 挂起。**这是 task3 红的唯一原因。**

> `perf_required = true`（CP-B0 核实任务书确有性能条款），所以「无性能证据 → 不判 PASS」
> 是正确行为，不是 bug。但 AGENTS.md 5.10 定的「只测 msprof 实测」目前**只有文字、没有代码**
> ——`perf.mode="measure_only"` 尚未实现。

### 4.10 目标硬件与环境 ✅ 已核（双源一致）

| 项      | 值                                                                 |
| ------ | ----------------------------------------------------------------- |
| 硬件     | Ascend 950PR（任务书「适配硬件」↔ `op_def` 的 `AddConfig("ascend950")` 双源一致） |
| SoC 串  | `ascend950`（**不是** `ascend950pr`）                                 |
| 环境     | CANN 9.0.0 · Ubuntu 22.04.5 · Python 3.11.15 · x86\_64            |
| 关键版本   | numpy 1.26.4 · cv2 4.11.0 · torch 2.10.0+cpu · torch\_npu 2.10.0  |
| device | 7（8 张卡跑测时全 idle）                                                  |

### 4.11 构建、安装与来源策略 ⚠ 降级

```
取源    ：local_snapshot（跳过 git fetch，只校 merkle）
构建    ：--pkg --soc=ascend950 --ops=gaussian_blur --vendor_name=oprwgb
          （不带 --experimental，不带 --no_force）
安装    ：用户态隔离 vendor，落 vendors/oprwgb_cv/，不写共享 OPP
绑定    ：vendor ELF sha256 + 整仓 merkle + 子树 merkle + build_args 全进收据
复用    ：reuse_build = false（本轮强制重建）
```

**降级点**：`pr_head_unbound` —— 上游 `cann/ops-cv` 客观不存在该 PR
（实测扫了 600 个 merge request 无命中；任务书自己写明代码在**私仓**）。
合成一个 40 位 hex 是 AGENTS.md 5.8 明令禁止的造假，故 `head_sha` 落 null。

### 4.12 失败、异常与挂起策略 ✅ 按缺省规则执行

```
精度 24/24 pass          → 继续走性能
性能无证据               → BLOCKED，不判 PASS      ✅ 本轮如此
证据/provenance 不完整   → BLOCKED
needs_review ≠ PASS
任务书功能 gap 未解决    → 不得 PASS
```

---

## 4 · 现在卡在哪：一句话

**流程通了，裁决没通。** 精度维实打实全绿（24/24 真机跑、对 OpenCV CPU 库），
但 `overall = BLOCKED(验收门未过)`，卡在性能维一条数据都没有。

## 5 · 需要你拍板的清单

| # | 事项                                         | 为什么必须是你                                                    |
| - | ------------------------------------------ | ---------------------------------------------------------- |
| 1 | **真值到底是 OpenCV CPU 还是 GPU**                | 任务书三处自相矛盾，CP-B0 判 `ambiguous`。按契约只能由你 `supplied` 补事实       |
| 2 | **性能 baseline 用哪个、异机对比怎么成立**               | 任务书「CUDA(A100) 或 OCL GPU」二选一、无准确 API                       |
| 3 | **性能口径**（kernel-only? warmup/repeat? 统计量?） | 任务书只给了 `0.45×`，方法全缺                                        |
| 4 | **交付件缺口怎么处理**                              | PR 未交付 OpenCV C\++ 适配层（§任务概述/§1/§7 均标必选）。停跑，还是部分执行但不得 PASS |
| 5 | **NaN/Inf 期望行为**                           | 任务书未规定，而工具会强制铺这三条 case                                     |

前三条 CP-B0 已机读判出并阻断（`NEEDS_USER`），后两条目前**只在 spec 的 `task_pr_gaps` 里、
靠人手写**——这正是下一节要说的问题。

## 6 · 这一轮暴露的流程本身的问题

| # | 问题                               | 状态                                                                                                                  |
| - | -------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| 1 | **CP-B2 方向确认节点不存在**              | 本轮是「先跑完才看清方向」。本文档是事后手工补的                                                                                            |
| 2 | **CP-B0 的 `satisfied` 判据太松**     | 机器只校「覆盖到该项 + 引文是真原文 + 没跟别项复用同一句」，**不要求把必选/可选交付件抽全**。本例中 `delivery_scope` 只摘到一句就判 satisfied，§任务概述/§1/§7 里三个「必选」一句没进来 |
| 3 | **「任务书必选交付件 ↔ PR 实际交付物」无任何机器对账** | CP-B0 只读任务书（刻意设计），`fetch_source` 只读 PR，两者从不碰面。这个判断全靠 agent 手写 `task_pr_gaps`——手写就会错                                 |
| 4 | **AGENTS.md 5.10 只有文字没有代码**      | `perf.mode="measure_only"` 未实现，所以性能维只能挂起而不是「有实测数据的终态」                                                               |

第 2、3 条正在修（独立 worktree）。第 1、4 条待你定。
