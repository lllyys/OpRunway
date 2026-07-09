# OpRunway 已验证案例台账（validated ops ledger）

> 「用哪些真实『任务书 + PR』做过验收、拿到什么裁决、证据是什么」的**可查证台账**。
> PR 均在 gitcode `cann/ops-math`、经 API + 本地 `pr_facts.json` **双重核对为真且 merged**。
> ⚠ **Equal 例外（2026-07-09 更正）**：其社区任务**未验收通过、无已验收对应 PR**；原先列的 #2890 系**误配**（正式确认非本任务交付 PR），先前「A3 未达标」结论**已作废**——详见下方。
> 对应 GitHub PR：[lllyys/OpRunway#2](https://github.com/lllyys/OpRunway/pull/2)（本 doc 是它的镜像说明，供 GitCode 侧查阅）。

## 四个算子（任务书 + PR 双输入）

| 算子 | PR（gitcode，已查证 merged） | 测试方式 | 裁决（真机实测数据） |
|---|---|---|---|
| **IsClose** | *最早的 demo 算子，pipeline 首建于此，无对应社区任务 PR* | 真 a3 NPU 端到端 | ✅ **PASS**（精度=真 NPU out vs numpy golden；性能=msprof kernel-only vs 真 TBE 基线达标）|
| **Sign** | [#2702](https://gitcode.com/cann/ops-math/merge_requests/2702) 【社区任务】AscendC实现Sign | 真 a3 NPU + `fetch_source` ① | ⚠ **性能未达成**：`sign_004` kernel **9.68us** vs TBE 基线 **6.32us**、**ratio 0.653**（达标 0/1）|
| **Equal** | **无有效被测 PR**：社区任务 [`Equal_task_doc.md`](https://gitcode.com/cann/cann-ops-competitions/blob/master/04_tasks/01_community-task-2026/docs/202604/Equal_task_doc.md)**未验收通过、无已验收对应 PR**；~~#2890~~ 系**误配**（正式确认非本任务交付 PR）| —（无有效被测 PR）| ⚠ **无结论 · 此前「A3 未达标·真阳性」已作废**（系拿误配的 #2890 去对不相干任务书所得）。属「任务书要了但未落地/未验收」情形——正是 OpRunway 该识别并跳过的样本。详见下方归因段 |
| **Neg** | [#2680](https://gitcode.com/cann/ops-math/merge_requests/2680) 【社区任务】AscendC实现Neg | `acc-spec` 产 spec → mock 端到端 | ✅ **PASS**（新算子接入 demo）|

## 为什么 Sign「没成功」/ Equal 为何作废——诚实归因

- **mock 模式这几个全 PASS**（numpy golden 直接当 NPU 输出、自己跟自己比、trivially 匹配）；**只有真机 `new_example` 才暴露** Sign 慢（Equal 那条已因「任务书↔PR 配错」作废，见下）。所以验收**必须上真机**；mock 只验流水线自洽。
- **Sign = 真·性能未达成**：runner 正常（精度 5/5 过、`sign_000` NPU 输出 `[-1,0,1,-1,-1]`=golden、kernel 真跑），`sign_004`(1024×1024) 真机 **9.68us vs TBE 6.32us、ratio 0.653**。caveat：需确认自定义 kernel 构建优化口径与 TBE 对等、取稳态/warmup、多 shape 复测，才能 100% 归因到 PR 实现而非构建配置。
- **⚠ Equal = 结论作废（任务书↔PR 配错 + 空任务）**（2026-07-09 正式确认）：经确认——(1) **PR #2890 不是本社区 Equal 任务的交付 PR**（我们误配）；(2) **该 Equal 社区任务至今未验收通过、无已验收对应 PR**。故先前「Equal A3 未达标·真阳性」**整体作废**——它是拿误配的 #2890 去对不相干的 `Equal_task_doc.md`（要 A2/A3）判出来的。`#2890 在 A3 上 fp32/fp16 全 0` 只是一条**与本社区任务无关**的原始观测，是不是缺陷取决于 #2890 自己的任务书，本台账不再据此下任何验收结论。
  - **教训（比「解耦」更上游）**：验收前**任务书↔PR 的对应关系本身必须先验证**——一旦配错，或对应的其实是「未验收的空任务」，下游一切裁决（哪怕 root-cause 解耦做得再干净）全部作废。Equal 这一轮把「归因」refine 了三遍（op-bug→harness→op），却始终没质疑最上游的「这个 PR 是不是这个任务的」——这才是真正的根。
  - 原缺陷报告 `doc/equal-a3-defect-report.md` 已**删除**（前提不成立、避免误导）。

## 与机器门（P0）的关系

本轮 P0 机器可校验门 `acc-common/validate_acceptance_state.py` 把上面这套「真机机读证据、防假通过」固化成**代码硬门**：
- 性能须 **msprof op kernel-only**（防混 e2e 墙钟）；精度须**真 NPU out vs golden**、阈值三处一致（防放宽）；evidence 须覆盖 caseset 全部用例（**防跑子集报 100%**）。
- 门只管「证据可信+完整」，pass/fail 判定留 `validator`/`perf_compare`（如合法的 Sign 性能 fail 由 verdict 如实表达、不被门盖成 BLOCKED；~~Equal 精度 fail~~ 那例已因 #2890 误配作废、不再作本台账裁决）。

> 相关：`oprunway-agent-system-design.md`（落地设计）、`oprunway-todo.md`（P1–P3）、`oprunway-changes-brief.md`（改动流水）。
