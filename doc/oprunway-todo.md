# OpRunway 施工 TODO（离「通用算子验收工具」还差的）

> 现状：**主干施工完毕 + 真机端到端验证通过**（mock + new_example 两模式；三算子 IsClose/Sign/Equal 真 A3 跑通、裁决全对；精度=真 NPU vs numpy golden，性能=msprof 真 kernel-only vs 真 TBE 基线；过 codex 假通过审修）。
> 但**不是整体完工**——下面是剩余的洞，按优先级排。来源：2026-07-07 三算子验证 + Equal 归因反复那一轮的教训。

## 🔒 已用教训钉住的硬约束（别再违反，先写这，因为最值钱）

1. **FAIL 必先解耦 root-cause 再归因**：用「被测物自己的 build + 声明支持的 dtype + 手算 golden」独立复现，确认是「被测物 vs 我的 harness」，才能下结论。曾有一次跳过这步、在质疑下来回改口，绕了远路才靠解耦测试定论。
   - **2026-07-08 又一实证（Equal）**：我一度把 Equal 真机 fail 当「算子 bug 真阳性」写进 PR/doc。逐字节解耦发现 `out.bin` **全 0**（16B 全 `0x00`）——是 **kernel 没执行/没回写 buffer** 的特征、**我们 Equal runner 的问题**（算子自己 build 出 `[0,1,0,1]` 混合值、非全 0）。**教训：全 0 输出 = harness 嫌疑（buffer 零初始化没填），别安到算子比较逻辑头上。** 对比 Sign：runner 正常（精度 5/5 过），性能 fail 是真的。
   - **待办**：修 Equal runner（二元输入 / bool 输出通路，疑输出 tensor 未绑定/未回写）→ 重跑解耦才能对 Equal 算子归因。
2. **平台 / spec / 构建路径从任务书推，不猜**：equal 的 hardware/oracle/阈值我一度瞎猜（碰巧对）；`Equal_task_doc.md` 明写「适配硬件 Atlas A2/A3」→ a3 才是对的平台、a5(950) 无关。
3. **合入状态用 gitcode 查证，别沿用假设**：「7月前=已合入」是设定、不是事实；`api.gitcode.com/api/v5/.../commits?path=` 一查即知（本机直连）。

## 🚀 落地设计（对齐 cannbot）· P1–P3 —— 当前前沿 TODO（session 清空后从这接）

> 背景：本 session 深研 `repos/cannbot-skills`，产出落地设计 **`doc/oprunway-agent-system-design.md`**（三层 Plugin→Agent→Skill + 机器门 + 跨CLI AGENTS.md，分期 P0–P3）。
> **⚠ 这套 P0–P3 是「体系结构」轴，跟下面旧的 P0/P1/P2「验收口径」轴是两码事、别混。**
> **落地设计 P0 已落地并 merge**（GitHub PR #2 → main `b23fd83`，GitCode 同步）：机器可校验门 `acc-common/validate_acceptance_state.py`（三级**完整性门**：防跑子集报100%/防放宽阈值/防混e2e、抗坏输入、只管证据可信完整不重判精度）+ 接 `run_workflow` 硬 blocker（FAILED→BLOCKED+非零退出+`acceptance.json`）+ `AGENTS.md` 跨CLI单一源 + `check_manifest_sync.py` + 28 单测 + codex 双门。
> **开任一阶段前先按 CLAUDE.md #1 抛方案经用户同意。详规见设计 §5。**

- **P1 · 编排升级**：胖 `op-acceptance` agent → **薄 primary orchestrator**（只调度+状态机+工件门）+ **3 个 mode 驱动 subagent**（单轮、禁内部循环）：`acc-spec-extractor` / `acc-runner-dev` / `acc-verify-rootcause`；+ **`acceptance-workflow` skill**（承载 CP 状态机、调 P0 的机器门）。参考 cannbot `tilelang-op-orchestrator`（AGENTS.md `mode:primary` + subagent `mode` 字段 + 3阶段状态机 + 工件门 + 断点续跑/失败恢复）。
- **P2 · 原子化 + 分发**：`acc-spec`/`acc-runner` → 拆成**原子 skill**（`acc-casegen`/`acc-precision`/`acc-perf`/`acc-rootcause` 各一、由 subagent 组合，参考 cannbot `ops/ascendc-*` ~20 个原子 skill 库）；建 **`workflows/` 材料仓**（`development-guide.md` 蓝图 + `task-prompts.md` subagent 分阶段 dispatch prompt + `archive_ops/` 已验证算子案例，**非 skill、无 SKILL.md**）；写 **`init.sh`** 安装期扇出到各 CLI（Claude→CLAUDE.md、其余→AGENTS.md、symlink skills/agents）。
- **P3 · catlass 验收路线 adapter**（子任务清单）：① arch 运行时探测（`environment.json`/AskUser，**禁硬编码 3510/2201**）② example/harness 选择 + CMake arch 注入(`-DCATLASS_ARCH`) ③ `gen_data/golden/verify_result` 三件套数据流 ④ **raw log → `evidence.json` parser** ⑤ msprof kernel-only 解析 ⑥ GPU/baseline schema 对齐。参考 canon `catlass-to-aclnn-bridge`，**不整包引** `catlass-op-generator`（它是开发/生成链，我们是验收）。

---

## P0 ·〔验收口径轴〕堵了才能「加新算子不踩坑」— ✅ 本 session 已做完

1. ✅ **任务书(md) → spec 自动化（Task 0）**：已建 **`acc-spec` skill**（23 份真实任务书语料 grounding + codex 审：dtype 全集/verify_mode 决策树/threshold 兜底/多算子拆分）。spec 不再人肉搓。
2. ✅ **per-op runner 锚定 + root-cause 步骤入 harness**：已建 **`acc-runner` skill**（锚定算子自带 example、诚实 scope）+ **`op-acceptance` agent** 把「FAIL→被测物自己build+声明dtype+手算golden 解耦」写成第⑤步纪律。（构建路径自动选仍部分待扩，见旧 P2-7 catlass。）

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
