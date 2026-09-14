# 失败算子跟进链（P5.5 → P5.7 → P6）

> **何时读本文件**：runner 退出码 3 / `ACTION_REQUIRED`，即 `postrun_actions.json` 的
> `failed_ops` 或 `uncertain_reviews` 非空时。按 P5.5 → P5.7 → P6 顺序执行，全部处理完才算收尾。

失败集合 = `status ∈ {BUILD_FAIL, INSTALL_FAIL, RUN_EXIT_FAIL, RUN_PATTERN_FAIL, TIMEOUT}`。

## P5.5 — FAQ Lookup（先查已知方案，最省成本）

先写失败诊断（见 SKILL.md「失败诊断」），再对全部失败算子查一次 FAQ：

```bash
<python> <skill>/scripts/faq_lookup.py     # 默认读 CWD/cann-ops-report/postrun_actions.json
```

它吐一个命中列表（JSON），命中数打在 stderr。

- 命中为空 → **静默**，不打印任何内容，直接进 P5.7。
- 命中非空 → 用 `AskUserQuestion` 提示：

  ```
  X 个失败算子在 FAQ 命中已知修复方案，要应用并重试吗？
    1. ops-transformer / grouped_matmul  → [env] ASCEND_GLOBAL_LOG_LEVEL=1（来源：<issue_url>）
    2. ops-cv / resize_bilinear_v2       → [build_flag] -DCMAKE_BUILD_TYPE=Debug
    A. 全部应用并重试
    B. 选择部分（请告诉我编号）
    C. 跳过
  ```

  用户选 A / B → 对每个命中算子调 `retest_orchestrator.retest()`（cann-issue-track skill 的共享脚本）。

**约束**：
- `patch` 类 fix **不在此处自动应用**（避免意外改工作区），只在汇总里提示「FAQ 中有源码修复方案，可用 `cann-issue-track` 处理」。
- `faq_lookup` 内部 NEVER raise（任何异常静默 return None），不影响主跑测流程。

## P5.7 — 自动续跑（FAQ 未命中时自动触发，不等用户指令）

对全部 FAIL 算子续跑一轮（`--ops` 只传失败项，PASS 不重跑）：

- 复测仍 FAIL → 确定性失败已确认，进 P6
- 复测翻盘 PASS → 标记偶发，记入汇总，**不进 P6**
- **只续跑 1 轮**，不无限循环

## P6 — 失败算子自主探索修复

**前置条件（全部满足才进入）**：
1. 同一失败已被复测确认 ≥2 次（P5.7 自动完成）
2. P5.5 FAQ 未命中（已知方案优先）
3. 用户同意进入探索（`AskUserQuestion` 列出失败算子问哪些要探索；同仓同批失败一次确认即可，不必每轮重问）

**目标**：自主定位根因并尝试修复；**无论成败，过程与结论都沉淀为 issue 材料**——成功 → issue 附「已验证修复方案」（高价值）；失败 → issue 附「已排除路径」（同样有价值）。

**探索流程**（每算子独立，按成本从低到高，每层验证后再升级）：

1. **读证据**：失败日志 + 算子源码（op_kernel / op_host / op_api）+ examples + 对照同类 PASS 算子（如失败的 resize_bicubic 对照 PASS 的 resize_bilinear）
2. **形成假设**：缺符号 → 查 vendor lib 导出与示例链接名；运行错 → 查 errcode 含义 / 输入构造；缺示例 → 对照同类算子的示例改写
3. **低成本验证**：环境变量 / build 参数 / 命令行变体（不动文件）
4. **源码级验证**：`git -C <repo> switch -c explore-<op>` 临时分支改示例或源码 → 复测 → **无论结果切回原分支**，diff 存档
5. **预算**：单算子最多 5 次验证，超出即收档止损

**产物**：`CWD/cann-ops-report/<repo>/test/explorations/<op>.md`

- **首行必须含 `SOLVED` 或 `UNSOLVED`**——SUMMARY 的「探索（解/总）」列靠扫这一行汇总，写别的词会被算成未解。其后写 根因 / 尝试 1..N / 结论（SOLVED + diff·方案，或 UNSOLVED + 已排除清单）
- 探索结论**只落这份 `.md`，不写回 run_state**——run_state 的 status 保持原始失败状态（`EXPLORED_*` 不是合法状态，写入会被 `VALID_STATUSES` 拒绝）。SUMMARY 的「探索（解/总）」列靠扫本目录 `.md` 的首行汇总
- `cann-issue-report` 起草时自动引用本目录

**红线**：临时分支不合并、跑完必恢复现场；不动 PASS 算子；NPU 串行。

## 收尾

`failed_ops` 走完上述链条、`uncertain_reviews` 逐个复核落成终态（见 SKILL.md「UNCERTAIN 复核」）后，
再向用户汇报本轮结果；仍有未解失败 → 提示可用 `cann-issue-report` 上报社区。
