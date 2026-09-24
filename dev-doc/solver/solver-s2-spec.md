# S2 切片 spec v2：SKILL.md 登记 → 冷读演练 → 复数三算子

顺序由 Mr.0 钉定（2026-09-23）。v1 经 Codex 评审（thread `01a0d0e6`）：顺序与架构
可不改，九条必须改本版逐条落实（处置对照见第 7 节）。继承 `solver-s1-cholesky-spec.md`
的 1.1 布局、1.2 授权记录与未裁定运行语义；**继承例外**：S1「禁写 plugin」条款本片
失效（SKILL.md 与登记就是要写 plugin，主会话门前置已补齐）；判据数值来源在 S1 基础上
扩入复数任务书 §3.2 与本 spec 第 4 节契约表。

依赖链（评审修正后）：**补齐包交付 → a 初评 → 修订并复验 → b 冷读 → c 复数扩展 →
最终文档与包回归**。

## 0. 术语与指向

判据卡/族级期望集/formal/T7 → `solver-pipeline-design.md` 第 0/4 节；bench_key/
canonical/cards/双跑逐位一致（比较对象=数组内容非容器字节）→ S1 spec 第 2 节；
DPOT01/02/03 → `repos/solver_tasks-main/cholesky_precision/README.md`；c/z 前缀 =
LAPACK 复数单/双精度例程前缀；ᴴ = 共轭转置；sim = `sim_dut.py` 产的模拟被测输出；
with/without = 带/不带待测 skill 的对照评测组。

## 1. S1 交接缺口（本片必修，a 之前）

对照环节设计 §2 的**十类**包契约逐件盘点（评审已盘，结论照录）：

| 缺口 | 处置 |
| --- | --- |
| `gen_data.py` 未随包交付（第三缺口） | 装包器把生成脚本 + 所需 canonical 切片装入包；manifest 随之更新 |
| README 缺失（第九类） | a 补齐；模板放 case-gen 的 `assets/`（两套规则一致要求），渲染入口读取 |
| **b 的模拟输出来源**：包内无 sim、README 无法凭空给 `--dut-out` | 裁定：`sim_dut.py` 随包交付（自测辅助件定位，与 verify 双件同级），README 写明「开发者以真实实现输出替换 sim 输出」的边界 |
| harness 未交付 | S1 已显式延期，非漏装；README 必须写明模拟边界 |

index/golden 位置经评审核实**无缺口**（golden 嵌 npz、index 在 cases/，S1 记录支撑）。

## 2. 子片 a：SKILL.md + 登记（skill-creator 评测闭环）

产出：两份 SKILL.md、plugin.json skills 数组两行、README 首表两行、包 README
模板（assets/）与三包回填、上述交接缺口修复。

写作约束：双规则集同时过，**五处冲突预裁定**（评审给定，照录为准）：

| 冲突 | 裁定 |
| --- | --- |
| 上游「判据/判据表」vs 移植 zh-writing 禁「判据」 | 结构语义随上游，字面用「检查条件/检查表」；映射记入 SKILL 术语节 |
| 「判据卡」为本仓自造词 | SKILL 文面改用「检查条件卡」，首次使用处就地定义并链设计文档 |
| zh-writing「用例包」定义绑定 ATK 线 | solver 任务包就地定义，不套用该条 |
| 「标杆」术语表定义为精度参考 | `perf_baseline` 字段名不动，正文写「性能参考耗时」，不机械替换 |
| README 模板位置 | 放 `assets/`（v1 写「渲染入口同级」作废） |

lint 命令与目标集显式列出（退 0 ≠ 全部规则过，人工规则另过自检清单）：

```bash
cd plugin && python3 .claude/hooks/doc_style_lint.py skill/repo-task-solver-*/SKILL.md
python3 .claude/hooks/doc_style_lint.py   # 仓根移植版，工作目录=worktree 根
python3 .claude/hooks/skill_budget_lint.py
```

skill-creator 闭环（plugin/CLAUDE.md 开发流程第 1 步及评测段）：每 skill 3 条真实
prompt；with/without 同输入、同工具权限、同远程环境、独立输出目录，without 组不得
能读到已登记的待测 skill；评测证据分三档报告——文本检查证明路由与边界、远程 sim
证明文件契约与正反例走通、未评部分（真实调用器/NPU）如实列出。3 条 prompt 只作
最小回归集，不据以声称 description 触发质量。**修订后必须复验**（一轮修订上限指
修改次数，不免除修订版评测）。

完成条件：两 lint 按上列命令退 0 + 自检清单过；`claude plugin validate` 过（根
CLAUDE.md 告警属预期）；登记按**集合核对**（plugin.json 条目集 == 目录集，非数数）；
初评与复验报告归档 `reports/solver-s2/`。

## 3. 子片 b：S2 冷读自测演练

隔离契约（零上下文的可执行定义）：仓外新建工作目录，只放包副本；全新 headless
会话（不 resume）；不加载 solver skill、不注入本仓记忆；远程执行方式与日志位置
写进演练说明；停止条件 = 断言四项判完或演练者声明卡死。参考 `isolated-acceptance`
的隔离手法但**不套用其完整验收流程**（b 测的是包，加载 accept skill 会补上包欠缺
的知识，测不准）。

断言四项（机械判定；精度脚本已区分退出码 0/1/2）：

1. 正例：`verify_accuracy` 退 0，目标 case 全部数值 PASS，无证据不足项；
2. 负例：指定 case + 有效比较区域 + 确定扰动（幅度须超出残差兜底的既定豁免语义），
   退 1 且报告指认该 case 数值 FAIL；
3. 无效运行区分：缺依赖/缺输入/参数错的退 2 **不算**「抓住错误输出」；
4. 记录：初始提示、完整交互、命令、报告全存档；spotrs 复验用**新的独立会话**。

演练者报出的「要猜的地方」是人工观察清单（不包装成机械断言），逐条按**责任位置**
修复——README 措辞、脚本报错、包 schema 各归其位，不一律补文档。

## 4. 子片 c：复数三算子（cpotrf/cpotrs/cpotri）

**现有实现含实数硬编码（评审实查）：cards 与 residual 拒绝复数、sim 强转 float32
丢虚部、render_verify 内嵌实数判定、装包校验硬编码 float32——c 的改动面必须覆盖
criteria、renderer、sim、accept 消费、装包五处，不是只加卡。**

契约增量表（冻结，实施不得临场发明）：

| 项 | 契约 |
| --- | --- |
| dtype 映射 | 字段名不变：`A64/B64/golden64`=complex128、`A32/B32/golden32/out32`=complex64；降型校验 `A32==A64.astype(complex64)` |
| LAPACK 链路 | golden 用 z 前缀；ratio_cpu 用 c 前缀完整准备链（potrs/potri 的前置 cpotrf 同精度） |
| 第一层统计 | 实部、虚部**各自**算 matched_ratio 与 max_abs，双侧同时达标才过；不合并成 2N 元素互相稀释（复数任务书 §3.2 第 3 条） |
| Hermitian 语义 | 构造 A=BᴴB+nI；L·Lᴴ/Uᴴ·U 还原；存储侧共轭镜像恢复全阵；对角虚部按 0 处理并校验；无效半三角不参与比较 |
| 复数残差 | 范数与绝对值用复模；升精度到 complex128 再算（先升后取模，防 astype 丢虚部坑）；公式仍 DPOT01/02/03 形状，ε=2⁻²⁴，出处 cholesky README + 交接文档踩坑 9 |
| cpotri 双目标 | A⁻¹ 直审与 A·A⁻¹ 对 I 两目标保留，各自拆实虚 |
| 状态 | T7 按任务书收束为拆实虚（歧义记 flag 保留至任务方确认）；batch 聚合未实现记证据不足；formal 恒待裁 |

验证增量：高区分度断言——纯虚部错误、漏共轭、U/L 双侧、无效半三角污染、c/z 链路
精度；**三个实数算子全量回归**（c 改了共享件，实数结论不得漂移）；c 完成后回改两份
SKILL.md 的支持范围并复验相关评测用例（a 阶段 SKILL 只如实写实数+sim）。

## 5. 执行形态与约束

子片 a 主会话驱动（skill-creator 闭环含 Mr.0 评审环节）；b 单 agent 冷读（隔离契约
见第 3 节）；c 用 S1 工作流缩小版（扩清单 → 编码卡并行 → 远程验证 → 装包 → 实数
回归）。写 skill 文档前先 Read 两份规则文件（skill-md-gate 要求）。其余沿 S1 spec
第 4 节（远程执行、写入所有权、不 push）。

## 6. 声明边界

本片完成后新增可声明：skill 以插件形态可加载；**包内模拟自测冷读通过**（不泛称
完整开发者自测）；复数通路与实数同构可运行且实数回归不漂移。仍不可声明：正式验收
结论、真算子/NPU、batched、gels、QR/LU/geev 族、description 触发质量。

## 7. v1 评审处置对照

九条必须改 → 落点：包清单校正与生成器交付（第 1 节，引用改指环节设计 §2 十类）；
b 模拟输出来源（第 1 节裁定 sim 随包）；冷读断言与隔离冻结（第 3 节）；a 修订复验
与 c 后回归（第 2 节末、第 4 节末）；复数契约表（第 4 节）；renderer/sim/实数回归
入 c 范围（第 4 节）；写作冲突五裁定与模板位置（第 2 节）；继承例外与来源扩展
（导言）；声明收窄与术语指向（第 6、0 节）。三条建议改：登记集合核对（第 2 节）、
README 缺陷按责任位置修（第 3 节）、双跑比较对象=数组内容（第 0 节）。
