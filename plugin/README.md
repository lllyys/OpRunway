# OpRunway — NPU 算子验收 agent+skill 体系

输入 = **算子任务书（md 或链接）+ 被测来源（PR 或本地 checkout）** → agent 自动产 spec、跑测、出**裁决 + 中文报告**。主链为：用例生成(ST) → NPU 精度 + msprof 性能跑测；只有用户明确要求 GPU 对比时才追加跨设备性能报告。

## 怎么用：在会话里对话，不碰脚本

装上本插件后，**在会话里对 `op-acceptance` agent 用自然语言说要验收什么**即可，例如：

> 帮我验收这个算子：任务书 `<md 路径或链接>`，PR `<链接>`。

agent 内部完成六步（取材 → 任务书→spec → 生成并验证 runner → NPU 跑测 → 失败解耦 → 报告）。**你全程只对话、看进展与最终报告——不需要、也不会被要求去跑任何脚本或命令。** 缺东西（任务书 / PR / 是否已开 NPU-VPN）它会问你；验收通路由 spec 派生，不让人选择 mock 还是真机。

- NPU 暂不可达 → 先完成 CP-A/CP-B 与 `cpp_extension` 的静态接口 preflight，再停在真机构建/信任门之前，如实报告待办；不切 mock、不改抽退役 form。远程连时才检查 VPN，就地跑不需要连接元数据。
- 一份任务书含多个算子 → 自动拆成多份、逐个验收。

## 形态（统一 · 可移植）

**一个对话式 agent 为唯一入口**，内部用 skills（NL 判断）+ 确定性脚本（工具中立）：

| 层 | 内容 | 用户可见？ |
|---|---|---|
| **agent** `op-acceptance` | 对话入口 + 六步编排 | ✅ 唯一入口，只对话 |
| **skills** `acc-spec`(任务书→spec)、`acc-runner`(生成 runner) | NL 判断规则（`references/`）| 内部 |
| **scripts** `acc-common/*.py` | 取材 / 造用例 / 裁决 / 性能（确定性核心）| 内部（agent 幕后跑，**不暴露给用户**）|

**跨 CLI 统一**：脚本 + JSON 契约 + skill `references/` 工具中立、一份到处用；换运行时（Codex / Antigravity）只换 agent/skill 的注册薄壳，核心不动。判定脑子在 `acc-common/validator.py`（ADR 0007），不在 agent。

## 现状（诚实）

- **mock 仅供开发自检**（无需真机，物理上不产验收裁决）；正式验收当前只准入 `cpp_extension`，spec 必须显式写出该值。
- 当前闭环 = ops-<族> 仓·标准 aclnn 两段式·opp 安装型（含非 experimental 子树）+ 官方 `NpuExtension` 调用桥 + vendor ELF 构建收据；catlass/非 aclnn/双实现待扩（见 dev-doc/oprunway-batch6b-design.md）。
- **加算子不改工具代码**：用例生成已去引擎化（PR #7 runner / PR #8 golden），加算子 = 在 `<ops_root>/<op>/golden.py`
  落一份 golden。⚠ 仅指 **elementwise 通路**；`catlass_adapter` 的内置 matmul golden 与 `gen_cases._BF16_EXACT_OPS`
  仍是引擎里的算子知识（两处已知例外，如实记账）。样例 golden 现 8 份
  （Equal / Im2col / IsClose / Median / Neg / Sign / UpsampleNearest3d / UpsampleNearestExact2d）。
- **经真 NPU 验收裁决的算子**：IsClose / Sign（A3）· Median（A3；`cpp_extension` torch-parity
  有两份来源身份不同、不可互相替代的历史 caseset：PR 通路 1152 例/51 FAIL，本地通路 1344 例/58 FAIL；
  两者确定性裁决均为 `FAIL(精度)`，引用须点名对应 spec 与来源，详见仓根 `AGENTS.md` §4.5）·
  Elu / Silu（A5-950，18/18 非空例）。
  ⚠ **Median 的性能维仍 BLOCKED，但已有真实性能数据**：custom 50/50、`torch_npu` baseline 48/50 有效，48 对实际评分、35 对达到 `ratio >= 1.0`；2 个 BF16、`dim=1` baseline case 报 161002，归为 baseline limitation。任务书指定的小算子拼接标杆是否等同当前 `torch_npu torch.median` 尚未核实，解决前不得宣称满足任务书性能条款。
  mock 通路自 C5（2026-07-22）起**不产验收裁决**（只产标 NON-ACCEPTANCE 的 `dev_run_summary.json`）。
新一轮不得从下面的历史能力表选择执行形态；能力表不是准入表。

<!-- oprunway:retired-begin -->
### 退役 runner form 的历史能力表

⛔ 历史留档 · 不得 dispatch · 不要照做

- `cpp`（当年使用 `--mode new_example`，编译 per-op C++ runner 跑）支持 **float32 / float16 / bfloat16** 三种
  （bf16 逻辑 = fp32-on-grid，2026-07-16 在真 a3 验收通过）；int16/int32 属 Track C **挂账集**
  （`DEFERRED_NP_BY_FORM`）——生成期能造用例、**真机跑到仍 fail-closed**，spec 须以 `task_pr_gaps.dtype_deferred` 显式挂账
  （该条目须带 `capability_source: "runner"` + `runner_form`，门会拿活表交叉核验；⚠ 挂账只表示缺口被如实记下，
  **不表示该 dtype 免检**，见 `skills/acc-spec/references/taskdoc-to-spec.md` §1.2a）。
- `aclnn_py`（当年使用同名 mode，通用 ctypes 两段式调 `.so`）支持 fp32 / fp16 / bf16 / int64 / int32 / int16 / int8 / uint8 / bool，**无挂账项**；
  Median 的声明 dtype（fp32/fp16/bf16/int32）已全部真机跑过。

这两种 form 自 2026-08-06 起都没有真机入口；旧结果保持历史效力，但不得据此生成或 dispatch 新一轮。
<!-- oprunway:retired-end -->

> 设计/契约见 `../doc/oprunway-design.md`；改动流水见 `../doc/oprunway-changes-brief.md`；TODO 见 `../doc/oprunway-todo.md`。
