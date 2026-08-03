# OpRunway 分阶段 dispatch prompt 模板库（task-prompts）

> **这是材料仓，不是 skill**（无 SKILL.md）。装「primary 每次派 subagent 该给什么」的**可复用 prompt 模板**，供人 / orchestrator 参考拼装。
>
> **⚠ 前向声明 / 诚实边界**：
> - 这些模板**面向 P1 的 3 个 subagent**（`acc-spec-extractor` / `acc-runner-dev` / `acc-verify-rootcause`，`plugin/agents/*.md`）。**P1 已建这 3 个 subagent**；但**本 task-prompts.md 只是材料仓参考、不是 live dispatch 路径**——真正的 live 编排契约在 `skills/acceptance-workflow/SKILL.md`（CP-A..E 状态机 + §2 dispatch 六段契约），primary 首响应加载它、按它派 subagent。
> - 本文件**不被自动加载、不改变运行路径**；它是「人读/新 CLI 移植时抄的模板」，若与 `acceptance-workflow` skill 冲突，**以 skill 为权威**。
> - **判定唯一归确定性脚本链**（ADR 0007）：所有模板都强调 subagent「单轮 / 禁内部循环 / 不自行判 pass/fail、只回结构化摘要」。

## 0. dispatch 六段契约（每次派都给全，缺一段 subagent 就做不了）

| 段 | 内容 |
|---|---|
| **工作区** | `reports/<op>/`（及 `work/` 子目录）路径；`${OPRUNWAY_PLUGIN_ROOT}` = 本插件根中立变量——**Claude 下等价 `${CLAUDE_PLUGIN_ROOT}`**（harness 自动设），**Codex 等非 Claude 运行时须先显式 `export OPRUNWAY_PLUGIN_ROOT=<仓根>/plugin`**；写进要执行的命令时用自兜底写法 `${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}`，两边都不用谁记得先 export |
| **dispatch_mode** | 本次模式取值（下表；与 frontmatter 的 `mode:subagent` 不同名） |
| **输入工件** | 该 mode 需读的已落盘工件 |
| **已确认约束** | 用户已明确的范围/选择；没有则写 `[]`。不得让后续 subagent 对同一项重复研究或提问 |
| **验收标准** | 本轮「算干完」的判据 |
| **本次产出** | 要落盘的工件名 + 回给 orchestrator 的结构化摘要字段 |

## 1. `acc-spec-extractor`（skill: acc-spec）

### 1a. `dispatch_mode = extract_spec`
```
工作区: reports/<op>/  · ${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}   # 插件根：Claude 走别名兜底；Codex 等先 export OPRUNWAY_PLUGIN_ROOT
dispatch_mode: extract_spec
输入工件: work/task_doc.md + work/task_doc.snapshot.md + work/pr_facts.json + work/source_facts.json + work/correspondence.json（①fetch_source/CP-A 产）
已确认约束: correspondence.json.confirmed_constraints（没有则 []）；逐项原样采用，不重复研究
验收标准: 按 acc-spec references/taskdoc-to-spec.md 字段映射逐字段抽；verify_mode 合法；
          numerical 有 threshold；dtype 只填 pipeline 支持子集、余入 task_pr_gaps；缺项落 gaps 不臆造。
          一份任务书含 N 算子 → N 份 spec。
本次产出: reports/<op>/<op>.spec.json（+ 多算子多份）；摘要回：产了几份 spec、关键字段、gaps 列表。
纪律: 单轮 / 禁内部循环 / 禁跨阶段 / 不自行判 pass/fail / 只回结构化摘要；
      source_facts 完整时禁重新联网/遍历无关目录；执行预算 300 秒，预算将尽则把未知项结构化落 gap/needs_user 后交还 primary。
```

### 1b. `dispatch_mode = refine_spec`
```
dispatch_mode: refine_spec
输入工件: 上轮 spec + 契约自检失败的报错文本（dry-run stderr / case_plan 账本 / preparation receipt 的 MISS 原因）
验收标准: 据 gate error 定向修 spec（如 dtype 子集/threshold/policy 口径），不扩范围。
本次产出: 修订后 <op>.spec.json；摘要回：改了哪几处、为何、是否仍有 gap。
```

## 2. `acc-runner-dev`（skill: acc-runner）

### 2a. `dispatch_mode = gen_runner`
```
dispatch_mode: gen_runner
输入工件: <op>.spec.json + pr_facts.json（含 test_aclnn_*.cpp + *_def.cpp）
验收标准: 先过 scope gate——ops-<族> 仓·aclnn 两段式·opp 安装型（含非 experimental 子树）；catlass/非 aclnn/双实现/未支持 dtype
          → 返回 BLOCKED / 转 P3，不硬塞。过 gate 后据 spec + 自带 example 锚定 aclnn 入口/dtype/参数顺序
          （抠 example 不猜——Equal 曾猜错入口翻车），生成 oprunway_<op>_runner.cpp + 选构建路径。
本次产出: <ops_root>/<op>/oprunway_<op>_runner.cpp（用户 CWD，非插件目录）+ 构建路径配置；摘要回：锚了哪个 example、四槽填法、gap。
```

### 2b. `dispatch_mode = verify_runner`
```
dispatch_mode: verify_runner
输入工件: 生成的 runner + spec
验收标准: 造手算 golden 小用例 → 喂 custom exe 跑 → 逐元素比；不一致 → custom↔builtin 同 case 对照解耦
          root-cause（runner 错 vs 算子错），别产假裁决。自检证据不满足 → 停在 CP-C、不上真机。
本次产出: runner 自检证据（满足/不满足）；摘要回：自检结论 + 若不满足的解耦线索。
纪律: 当前非代码强制 sidecar 硬门、待补——靠 agent/人自觉守纪律。
```

## 3. `acc-verify-rootcause`（无 atomic skill；方法论指针 acc-precision/acc-perf/acc-rootcause）

### 3a. `dispatch_mode = run_npu`
```
dispatch_mode: run_npu
输入工件: <op>.spec.json + 自检满足的 runner
验收标准: 真机 run_workflow.py --mode <mode>（OPRUNWAY_* 指真实机器/路径，不写进仓）——
          <mode> 据 spec.runner_form 派生：cpp（缺省）→ new_example；aclnn_py → aclnn_py（且须 OPRUNWAY_ACLNN_REAL=1）；
          mock / catlass / catlass_mock 派生不出、只能显式指定（非验收通路 / catlass 逃生口）。
          ⚠ 三条真机通路 new_example/aclnn_py/cpp_extension 都可产验收裁决，mode 只从 spec.runner_form 派生；
            走错的代价：cpp 路真机 dtype 白名单只有 fp32/fp16/bf16（int32 等落 DEFERRED_NP_BY_FORM["cpp"]、真机 fail-closed）
            → 覆盖缺一块；且性能基线对照物不同（new_example=内置 TBE、aclnn_py=同机 torch_npu），
            「任务书对标 torch」场景走 new_example 比出来的不是任务书要的那个比较。
          Task2 真 NPU 精度 vs numpy golden + Task3 msprof kernel-only 性能 vs 基线 + 末尾统一校三级门，一次原子跑完。
本次产出: evidence.json / verdict.json / baseline.json（有基线时）/ perf_report.json / acceptance.json；
          摘要回：acceptance.json.overall + 各维度通过数（逐字引用，不自判）。
```

### 3b. `dispatch_mode = rootcause`
```
dispatch_mode: rootcause
输入工件: FAIL 的 verdict.json / perf_report.json / acceptance.json + evidence
验收标准: 先验「任务书↔PR 对应」本身（acc-rootcause §0），再「被测物自 build + 声明 dtype + 手算 golden」
          独立复现、custom↔builtin 对照，解耦「被测算子 vs harness」再归因（acc-rootcause §1）。
          技术判定与官方口径分开、不外发、不臆断、不来回改口（Equal 血教训）。
本次产出: 解耦结论 + 缺陷定性（非 pass/fail 裁决——裁决归脚本）；摘要回：根因归属 + 证据 + 残留不确定。
```

## 4. 移植到新 CLI 时

把上面模板按目标 CLI 的 subagent 机制翻译即可；**Layer 0/1（数据契约 + 确定性脚本）不动**，只换 Layer 2 薄壳（`workflow-three-layer-architecture`·proposed·未 settle，载重前需核）。
