# OpRunway 非真机流程性能优化方案

> 目标：不改变真机精度/性能测试策略，把“任务书 + PR → 验收报告”的端到端墙钟压到
> 1.7 小时（102 分钟）以内。本文只优化 CP-A/CP-B/CP-C 的取材、事实抽取、计划生成、
> 静态预检、断点续跑和编排往返；CP-D 的用例范围、阈值、warmup/repeat、timing scope、
> validator、perf_compare 与三级门均不改变。

## 1. 基线与硬预算

Median 历史运行的 CP-A..D 总墙钟为 11,354 秒（3:09:14），其中：

| 段 | 历史墙钟 |
|---|---:|
| CP-A/B/C 与阶段切换 | 5,891 秒（98:11） |
| CP-D | 5,463 秒（91:03） |

若 CP-D 原样保留，102 分钟总预算只剩 657 秒（10:57）给 CP-A/B/C。故目标不是把
确定性脚本快几秒，而是让已验证工件可按内容安全复用，并让冷启动只做一次事实抽取。

目标预算：

| 段 | 热续跑目标 | 冷启动目标 |
|---|---:|---:|
| CP-A | ≤ 1 分钟 | ≤ 1 分钟 |
| CP-B | ≤ 2 分钟 | 4–6 分钟 |
| CP-C | ≤ 3 分钟 | 3.5–5 分钟 |
| 阶段切换 | ≤ 30 秒 | ≤ 1 分钟 |
| CP-D | 不改变 | 不改变 |

热续跑目标总墙钟为 94–97 分钟；冷启动目标为 99–102 分钟。

## 2. 不可触碰的边界

- 不减少或跳过任何精度/性能 case。
- 不改变 dtype、shape、特殊值和属性覆盖。
- 不改变 warmup、repeat、性能基线、target ratio 或 kernel-only 口径。
- 不缓存或复用正式 caseset、`.npy`、evidence、verdict、baseline、perf_report、acceptance。
- 不让 dry-run、静态 preflight 或 cache hit 产生/暗示验收 PASS。
- 不跳过真实 build、DUT 来源核验、harness trust gate 或 CP-D。
- 不增加按算子名分支；Median 只作为通用能力的结构性见证。

## 3. 内容寻址工件

### 3.1 `source_facts.json`

一次取材后形成只读事实包：

- 任务书 locator、字节摘要与快照摘要；
- PR canonical URL、source repo、PR number、head SHA、head repo；
- changed files 与 key files 的路径、来源 ref、字节摘要；
- 接口形态、aclnn 入口、目标目录等确定性派生事实；
- dtype/hardware/reference source 的来源、引用和冲突；
- `complete | blocked` 完整性状态及原因；
- producer contract version 与逻辑摘要。

PR head 至少做一次轻量刷新。head 变化即新内容身份；离线不得把旧 head 宣称为最新。

### 3.2 `case_plan.json`

`gen_cases --dry-run` 的 stdout 改为同时可落结构化账本：

- spec 摘要与规划器逻辑摘要；
- case ID、数量、dtype/shape/attr 分布；
- 特殊场景、覆盖强度、丢失组合与零配对；
- golden/out-shape 依赖状态和摘要；
- source/correspondence/spec/snapshot 绑定；
- deterministic plan digest。

默认 stdout 保持人读兼容；正式 `gen_cases()` 路径不读该账本。

### 3.3 `aclnn_preflight.json`

`runner_form=aclnn_py` 的非真机 CP-C0 工件：

- interface kind 与候选 header；
- header 签名和 spec slots 对账；
- call variants、固定 ABI attrs、reference kwargs 的分离映射；
- 候选符号与待真机核验项；
- source/spec/PR head 摘要。

它只能得出 `READY_WAIT_NPU_TRUST_GATE` 或静态 BLOCKED 原因，不能得出验收结论。DUT
实际导出符号、定义方 `.so` 和行为仍由真机 build/trust gate 核验。

## 4. 指纹与失效传播

所有摘要使用 canonical JSON 和带 domain 的 SHA256；不用 mtime、绝对路径或创建时间作身份。

```text
任务书字节变化
  → source facts
  → correspondence
  → spec / golden 引文
  → case plan / aclnn preflight

PR head 变化
  → source facts
  → correspondence / task_pr_gaps
  → spec / runner anchor
  → case plan / aclnn preflight

spec 变化
  → case plan / aclnn preflight

golden.py、任务书快照或规划规则变化
  → case plan
```

命中必须同时满足 schema、producer logic digest、payload digest、dependency digests、
`completeness=complete` 和 `correspondence.status=confirmed`。任一不满足均为带原因的 MISS；
摘要冲突或同 SHA 内容变化为 BLOCKED corruption，不允许“尽量复用”。

## 5. CP 状态机调整

### CP-A

1. 轻量刷新 PR 元信息并钉 head SHA；
2. 内容命中则物化 `task_doc.md`、`pr_facts.json`、`source_facts.json`；
3. snapshot 在 CP-A 一次生成，SHA 在 spec 抽取前已知，消除后续回填；
4. correspondence 仍需原有机器证据和用户确认，不由 source cache 自动确认。

### CP-B

1. spec agent 只消费 taskdoc、source facts、correspondence 与 schema；
2. golden agent 复用同一 evidence bundle，不重复通读 PR；
3. primary 运行 dry-run 并落 `case_plan.json`；
4. case target 等用户选择由 primary 一次询问；
5. 仅在确定性账本报错时进入一次 `finalize_spec`，不再用宽泛 `refine_spec` 反复研究来源。

### CP-C

- `cpp` 保持既有 runner 流程；
- `aclnn_py` 增加 CP-C0 静态 preflight 的明确 owner；
- CP-C1 仍执行真实 build 与完整 harness trust gate；
- immutable PR head 的 build 可在用户批准后与 CP-B 并行，但最终 runner form/硬件不匹配时必须弃用。

## 6. “任务书对标 torch”契约收敛

- `runner_form` 只由接口能力派生；`torch_ref_aclnn` 是 oracle/baseline 场景标签，不替代 form。
- extractor 回执必须包含 scenario、runner form、form-aware dtype、call variants、多输出角色、
  operator class、torch baseline mapping 与 gaps。
- 固定 DUT ABI attrs 与 torch reference kwargs 分字段表达，不能复用一个 `active_attrs` 同时承担两种语义。
- 未证明“小算子拼接版本”等同于 `torch_npu` 时，必须进入结构化 `task_pr_gaps`。
- 无 NPU 时最多到 `READY_WAIT_NPU_TRUST_GATE`，不跑 mock 冒充验收。

## 7. 验证

1. canonical JSON、摘要、原子写、坏记录、路径逃逸和并发单测；
2. source head/taskdoc/key file 任一变化触发正确失效；
3. dry-run stdout 与结构化账本一致，正式 caseset/`.npy` 字节不变；
4. cache 开/关下 adapter、validator、perf_compare 和 gate 调用次数完全一致；
5. agent frontmatter、manifest sync 和文案一致性检查；
6. 所有 pytest 在远端 NPU 容器执行；
7. 用全新 subagent session，只给任务书 URL、PR URL、输出目录和已验证 receipts，完成 Median 真机验收；
8. 以真实 timing receipt 证明总墙钟 ≤ 102 分钟，否则据瓶颈继续迭代 skill。
