---
name: acc-casegen
description: 把算子任务书拆成原语组合、按 rule-catalog 拉每原语的必测 case 模式、实例化去重后出「功能/精度/性能」覆盖矩阵——OpRunway 验收 Task1 用例生成的展开规则。参考草案：尚未接入 op-acceptance live 流、勿自动触发；仅在需理解或扩展「任务书→用例集」的展开规则时阅读。live 用例生成由确定性 gen_cases.py 负责。
---

# acc-casegen — 任务书算子 → 覆盖用例集（展开规则）

**定位（诚实）**：本 skill 是落地设计 **P2 规划的原子能力 skill**（原子切分 = casegen / precision / perf / rootcause 四个能力薄壳），承载 canon `Primitive-to-case rule library`（canonical）里「展开逻辑 = SKILL.md、目录 = `references/rule-catalog.md`」划分中的**展开逻辑**部分。**当前尚未接入 op-acceptance live 编排流**：真实运行时的用例生成走确定性脚本 `${OPRUNWAY_PLUGIN_ROOT}/acc-common/gen_cases.py`（`${OPRUNWAY_PLUGIN_ROOT}` = 本插件根中立变量，Claude 下等价 `${CLAUDE_PLUGIN_ROOT}`）——**本 skill 不落盘 `caseset.json`、不调用也不替代 `gen_cases.py`**，只描述「一个算子该测哪些用例、为什么」的推理规则；**pass/fail 判定唯一归确定性 validator、不在本 skill**（ADR 0007）。

- **未登记进 AGENTS.md（诚实先例，P2 边界）**：本 skill **不列入** `plugin/AGENTS.md` 的 `skills:` 清单——登记 = 声称已接入 live 流，而本 skill 未接入（`op-acceptance` 的 live 用例生成仍走 `gen_cases.py`）。分发 / 发现由 `init.sh` 扇出保证（它 symlink `plugin/skills/` 下**全部** skill 目录、**不依赖 AGENTS.md 登记**），故不登记不影响可移植分发。待 P1 / 后续真正接进 runtime 调用链时再登记；本 skill 勿被自动触发。
- **通用 generator 是路线图、非当下能力**：`gen_cases.py` 现**按算子从用户侧 `<ops_root>/<op>/golden.py` 加载 golden**（引擎零内置、缺则 fail-closed；ADR 0011 golden 去引擎化——不再限于内置 4 算子），但**仍不是** rule-catalog 驱动的通用 generator。「rule-catalog → 通用 generator」是 canon 目标态 / 独立后续 todo，本 skill **不声称当下可对任意算子自动落 caseset**（且 golden.py 须先由 acc-runner-dev 从任务书产出）。

**输入**：算子任务书（公式 / 功能 / 规格 / dtype / shape / 属性 / 精度目标 / 性能目标）+（可选）PR 事实。
**输出（说明性）**：一份**覆盖矩阵**推理——原语 × tag × 三轴（dtype / 特殊值 / layout·对齐·tiling·workspace），每条用例标清「测什么形状 / 什么数据 / 为什么 / 走哪个 compare 分支 / 对应任务书条款」；缺口 / 不可达显式列出（no silent 漏项）。

## 展开算法（5 步；明细见 `references/rule-catalog.md`）

1. **拆原语**：读任务书公式 / 功能，把算子每个功能段映射到 rule-catalog §2 的原语（按 `identify_by`）。
2. **兜底 guard（硬规则）**：任一功能段匹配不到原语 → 产 `UNCOVERED_PRIMITIVE` 缺口项并中止 / 降级，**禁止静默归并进 matmul**。
3. **查库 + 叠三轴**：每原语拉 `mandatory` case 模式，叠加跨切面（dtype / 特殊值 / layout / 对齐 / tiling / workspace），按三轴叠加策略、非全组合。
4. **实例化**：在命名基准 base 上只改「被测轴」；「大 / 小」一律引用 rule-catalog 的 dtype 阈值符号，不瞎填。
5. **去重 + 元规则 + 覆盖报告**：多原语命中同一 case 合一条（`kind` 取并集、tolerance / timing 取最严来源、`case_origin` 记全部来源）；打全标签、标 golden 来源、升级 precision、选 performance；输出覆盖矩阵，缺口 / 不可达显式列出。

## 边界（跨运行时可移植 + 诚实）

- **不落盘、不判定**：本 skill 只产「该测什么」的说明性覆盖推理；不写 `caseset.json`、不算 pass/fail（判定归确定性 validator，ADR 0007）。
- **不替代 `gen_cases.py`**：live 用例生成的确定性活在 `gen_cases.py`；本 skill 是其**规则依据的人读版**，尚未接入编排。
- **阈值 / 口径不在此**：精度阈值、timing_scope 等在 policy / spec，不在本库。
- 全程中文。

**详规见** `references/rule-catalog.md`（11 原语 + 元规则 + 跨切面 dtype/layout/tiling + 组合规则）。
