# OpRunway 验收开发指南（development-guide）

> **这是材料仓，不是 skill。** `plugin/workflows/` 无 `SKILL.md`（判据 = 有无 SKILL.md）——它装「怎么把一个 NPU 算子从『任务书+PR』走到验收裁决」的人读蓝图、分阶段 dispatch 模板（`task-prompts.md`）、已验证算子案例（`archive_ops/`），供人 / subagent 组合参考。**承载状态机的单数 workflow 是 skill `skills/acceptance-workflow/SKILL.md`**（CP-A..E 状态机脑子），本蓝图是它的人读伴侣、不重复其权威。
>
> **判定唯一归确定性脚本链**（`validator.py` 精度 + `perf_compare.py` 性能 + `validate_acceptance_state.py` 三级完整性门 → 门控后写 `acceptance.json`，ADR 0007）。本蓝图与所有原子 skill 都**不自行判 pass/fail**，只描述怎么做。

## 0. 输入 / 输出

- **输入**：算子任务书（md 本地路径或链接）+ PR 链接。
- **输出**：`reports/<op>/` 下 `correspondence.json` / `<op>.spec.json` / `caseset.json` / `evidence.json` / `verdict.json` / `baseline.json`（**仅有基线时**——缺 GPU 标杆挂起时不产）/ `perf_report.json` / `acceptance.json` + 中文验收报告。
- **产物只落用户 CWD 的 `reports/`**；私有主机名/远端路径走 `OPRUNWAY_*` 环境变量、**不入仓**；副作用（clone/build/真机跑测/对外动作）先列计划、点头再做。

## 1. 六步验收流水线（对齐 AGENTS.md 硬门 + design §2）

```
①取材+对应校验 → ②任务书→spec → ③spec→用例集(ST) → ④runner 锚定+自检 → ⑤真机跑测(精度+性能) → ⑥三级门+裁决+报告
```

| 步 | 干什么 | 确定性脚本 / 原子 skill（方法论） | 关键纪律 |
|---|---|---|---|
| ① 取材 + 对应校验 | 任务书/PR → 中立 JSON；验证「任务书↔PR 对应」本身 | `fetch_source.py`；方法论 `acc-rootcause`§0 | **配错/空任务 → 下游作废**（Equal 血教训）；对应靠 issue 号+落点目录、非名字面匹配 |
| ② 任务书 → spec | 抽 `<op>.spec.json` + `task_pr_gaps` | `acc-spec` skill（NL）+ `fetch_source.py`（取材） | 缺项落 gaps 不臆造；dtype 只填支持子集、余入 gaps |
| ③ spec → 用例集 | 产覆盖「功能/精度/性能」的 caseset | `acc-casegen`（展开规则）+ `gen_cases.py`（确定性落盘，仅注册算子） | 无原语匹配 → `UNCOVERED_PRIMITIVE`，禁静默归并 |
| ④ runner 锚定 + 自检 | 生成 per-op runner，验证-才-信 | `acc-runner`（NL 锚定 example）+ `run_on_npu.sh` | aclnn 入口/dtype/顺序**抠 example 不猜**；自检不满足停在此、不上真机 |
| ⑤ 真机跑测 | Task2 精度 vs golden + Task3 性能 vs 基线 | `repo_adapter` / `run_workflow.py --mode new_example`；方法论 `acc-precision` / `acc-perf` | 精度=真 NPU vs numpy golden；性能=msprof kernel-only vs 基线；`OPRUNWAY_*` 指真机 |
| ⑥ 门 + 裁决 + 报告 | 三级完整性门 → 裁决 → 中文报告 | `validate_acceptance_state.py` + `validator.py` + `perf_compare.py`；FAIL→`acc-rootcause` | 门 FAILED → `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）；报告逐字引用产物、`needs_review` 不当 pass |

## 2. CP-A..E 检查点（对话暂停点 + 工件门）

蓝图层面的 CP 语义（权威状态机在 `skills/acceptance-workflow/SKILL.md`，此处只作导航）：

- **CP-A 前置**（primary 亲自）：取材 + 对应校验（落 `correspondence.json`）+ 环境/模式确认（mock vs new_example、NPU/VPN）。`status=confirmed` 才进 CP-B；`mismatch`/`empty_task` → 出程序结论、停跑。
- **CP-B Task1 用例**：dispatch `acc-spec-extractor` 产 spec；primary inline `run_workflow.py --mode mock` 自检用例链自洽。
- **CP-C runner**（需 NPU）：dispatch `acc-runner-dev`（先过 scope gate）→ runner 自检证据满足才允许上真机。
- **CP-D 真机跑测**（一次原子）：dispatch `acc-verify-rootcause:run_npu` → `run_workflow.py --mode new_example`，Task2+3+三级门一次成；FAIL → `rootcause`。
- **CP-E 报告**（primary）：逐字引用 `acceptance.json`/`verdict.json`/`perf_report.json` 裁决 + `task_pr_gaps` + 各维度通过数。

## 3. 铁律（每步都受约束）

1. **判定唯一归确定性脚本链**，编排层/skill 只引用不自判（ADR 0007）。
2. **验收权威 = 任务书**；「PR 有测试」≠「验收过了」。
3. **缺 NPU/VPN → 到 mock 为止**，明确告知「真机待开 VPN」、不假装真机（mock 全过、真机才暴露——Sign 慢就是真机才现的）。
4. **零硬编码**：仓名/路径/SOC/阈值不写死，运行时探测或问用户；`OPRUNWAY_*` 不入仓。
5. **FAIL 先解耦再归因**：先验对应（①）、再解耦「被测物 vs harness」（`acc-rootcause`），别凭 signature 猜、别来回改口。

## 4. 加一个新算子要几步

`spec + golden(gen_cases 注册) + runner` 三件套 → mock 端到端自洽 → 真机跑测。案例见 `archive_ops/`（已验证算子，如实标 verdict）。
