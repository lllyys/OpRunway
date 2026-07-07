# OpRunway 施工 TODO（离「通用算子验收工具」还差的）

> 现状：**主干施工完毕 + 真机端到端验证通过**（mock + new_example 两模式；三算子 IsClose/Sign/Equal 真 A3 跑通、裁决全对；精度=真 NPU vs numpy golden，性能=msprof 真 kernel-only vs 真 TBE 基线；过 codex 假通过审修）。
> 但**不是整体完工**——下面是剩余的洞，按优先级排。来源：2026-07-07 三算子验证 + Equal 归因反复那一轮的教训。

## 🔒 已用教训钉住的硬约束（别再违反，先写这，因为最值钱）

1. **FAIL 必先解耦 root-cause 再归因**：用「被测物自己的 build + 声明支持的 dtype + 手算 golden」独立复现，确认是「被测物 vs 我的 harness」，才能下结论。曾有一次跳过这步、在质疑下来回改口，绕了远路才靠解耦测试定论。
2. **平台 / spec / 构建路径从任务书推，不猜**：equal 的 hardware/oracle/阈值我一度瞎猜（碰巧对）；`Equal_task_doc.md` 明写「适配硬件 Atlas A2/A3」→ a3 才是对的平台、a5(950) 无关。
3. **合入状态用 gitcode 查证，别沿用假设**：「7月前=已合入」是设定、不是事实；`api.gitcode.com/api/v5/.../commits?path=` 一查即知（本机直连）。

## P0 · 堵了才能「加新算子不踩坑」（先做这俩）

1. **任务书(md) → spec 自动化（Task 0）**：解析算子任务书，抽出 dtypes、适配硬件/平台、精度口径（AscendOpTest 阈值 / MERE·MARE）、性能口径（≥TBE 95% + 小shape例外）、verify_mode、泛化要求 → 生成 `spec.json`。现状：spec 手写、我还猜过。这是「每个算子人肉且易错」的根。
2. **per-op runner 锚定 + 构建路径选择 + root-cause 步骤入 harness**：
   - runner 必须锚定算子自带 example（dtype、aclnn 入口、语义），不能凭 header 猜（Equal 教训）。
   - 构建路径按算子类型选（experimental / legacy / 双实现 math+experimental 并存），不能一律 `--experimental --pkg`。
   - 把「FAIL → 解耦复现」做成 harness 的一步，而不是靠人临场手动。

## P1 · 验收口径完整性

3. **接真 AscendOpTest oracle / MERE·MARE**：现在是简化的 exact-mismatch / max_rel_err，任务书要的是 AscendOpTest 工具默认阈值（数值算子还要 MERE·MARE 按 dtype）。
4. **性能小shape例外**：任务书要求「<10us 场景相差 3us 时，出性能仿真图 + 分析证明与 TBE 一致/更优」——现在没实现。
5. **dtype / attr 覆盖扩面**：runner 只支持 float32/16；attr 只测默认值（如 IsClose 的 `equal_nan=True`、不同 rtol/atol 分支没覆盖）。补 int/bf16 + attr 值矩阵。

## P2 · 广度 + 收尾

6. **GPU 标杆接入（Task 3 原设计）**：现在只做了 NPU↔内置TBE；NPU↔GPU 对比等外部给 GPU 基线 schema 才能对齐。
7. **泛化到 catlass + 其余仓**：现在只打通 ops-math C-API 一条路；catlass（原 Task 2 重点）与其它 11 仓未做。
8. **发布形态定稿**：现在还是 `plugin/acc-common/` 脚本；「自维护插件仓 + skills external-sync 进 awesome-ascend-skills」的形态待定稿。

## 备注

- 「完工」的标准还没定（够 demo / 够内部用 / 够对外发布）——定了标准可倒推「到可用 v2 还差哪几步」。当前 P0 两项是任何标准下都得先堵的。
- 详细设计/契约见 `doc/oprunway-design.md`；改动流水见 `doc/oprunway-changes-brief.md`。
