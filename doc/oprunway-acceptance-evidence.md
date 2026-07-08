# OpRunway 已验证案例台账（validated ops ledger）

> 「用哪些真实『任务书 + PR』做过验收、拿到什么裁决、证据是什么」的**可查证台账**。
> PR 均在 gitcode `cann/ops-math`、经 API + 本地 `pr_facts.json` **双重核对为真且 merged**。
> 对应 GitHub PR：[lllyys/OpRunway#2](https://github.com/lllyys/OpRunway/pull/2)（本 doc 是它的镜像说明，供 GitCode 侧查阅）。

## 四个算子（任务书 + PR 双输入）

| 算子 | PR（gitcode，已查证 merged） | 测试方式 | 裁决（真机实测数据） |
|---|---|---|---|
| **IsClose** | *最早的 demo 算子，pipeline 首建于此，无对应社区任务 PR* | 真 a3 NPU 端到端 | ✅ **PASS**（精度=真 NPU out vs numpy golden；性能=msprof kernel-only vs 真 TBE 基线达标）|
| **Sign** | [#2702](https://gitcode.com/cann/ops-math/merge_requests/2702) 【社区任务】AscendC实现Sign | 真 a3 NPU + `fetch_source` ① | ⚠ **性能未达成**：`sign_004` kernel **9.68us** vs TBE 基线 **6.32us**、**ratio 0.653**（达标 0/1）|
| **Equal** | [#2890](https://gitcode.com/cann/ops-math/merge_requests/2890) 贡献Ascend C实现的Equal | 真 a3 NPU + `fetch_source` ①（8 key files）| ❌ **精度 fail，归因指向我们的 runner**：真机 6 挂 5，逐字节看 `out.bin` **全 0**（kernel 未执行/未回写的特征，非算子比较逻辑错）——**不是干净的算子 bug 证据**，需修 Equal runner（二元输入 / bool 输出通路）后重跑解耦再归因 |
| **Neg** | [#2680](https://gitcode.com/cann/ops-math/merge_requests/2680) 【社区任务】AscendC实现Neg | `acc-spec` 产 spec → mock 端到端 | ✅ **PASS**（新算子接入 demo）|

## 为什么 Sign/Equal「没成功」——逐字节解耦后的诚实归因

- **mock 模式这几个全 PASS**（numpy golden 直接当 NPU 输出、自己跟自己比、trivially 匹配）；**只有真机 `new_example` 才暴露** Sign 慢、Equal 挂。所以验收**必须上真机**；mock 只验流水线自洽。
- **Sign = 真·性能未达成**：runner 正常（精度 5/5 过、`sign_000` NPU 输出 `[-1,0,1,-1,-1]`=golden、kernel 真跑），`sign_004`(1024×1024) 真机 **9.68us vs TBE 6.32us、ratio 0.653**。caveat：需确认自定义 kernel 构建优化口径与 TBE 对等、取稳态/warmup、多 shape 复测，才能 100% 归因到 PR 实现而非构建配置。
- **⚠ Equal = 归因已修正为「我们的 runner 问题」，不是算子 bug**：逐字节看 `out.bin` **全 0**（16 字节全 `0x00`），是 **kernel 未执行/未回写输出 buffer** 的特征；而算子自己 build 得 `[0,1,0,1]` 混合值（非全 0）——**全 0 signature 指向 harness、不指向算子比较逻辑**。之前把 eq_ne 的 fail 直接安到算子头上是**过度归因**，已改。要归因到算子 bug，须先修 Equal runner（二元输入 / bool 输出通路）、重跑解耦测试。**这是「FAIL 先解耦 root-cause 再归因」纪律的一次实战。**

## 与机器门（P0）的关系

本轮 P0 机器可校验门 `acc-common/validate_acceptance_state.py` 把上面这套「真机机读证据、防假通过」固化成**代码硬门**：
- 性能须 **msprof op kernel-only**（防混 e2e 墙钟）；精度须**真 NPU out vs golden**、阈值三处一致（防放宽）；evidence 须覆盖 caseset 全部用例（**防跑子集报 100%**）。
- 门只管「证据可信+完整」，pass/fail 判定留 `validator`/`perf_compare`（合法的 Sign 性能 fail、Equal 精度 fail 由 verdict 如实表达，不被门盖成 BLOCKED）。

> 相关：`oprunway-agent-system-design.md`（落地设计）、`oprunway-todo.md`（P1–P3）、`oprunway-changes-brief.md`（改动流水）。
