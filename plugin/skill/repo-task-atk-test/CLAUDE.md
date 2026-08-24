# CLAUDE.md — 社区算子验收 Skill

改这个 skill 时读。运行时规则在 `SKILL.md`，这里是开发规则。

行文、脚本命名与退出码约定、TDD 流程、开发态启动，全部沿用仓根 `CLAUDE.md`，
不在这里复制。复用的是**怎么写**，不是**写什么**——后者每个 skill 自己推导。

唯一目标：**驱动零上下文 agent 独立跑完端到端验收。**

## 验收流程

阶段归属的真相在 `references/artifact-contracts.json` 的 `stages[*].skill`，量具从那里读：

- 生成侧 `repo-task-case-gen` 负责 S1–S2
- 验收侧 `repo-task-atk-accept` 负责 S0 与 S3–S5

每个脚本的归属在骨架 `scripts` 表中，由 `test_script_ownership.py` 锁住。

`skill/` 下另有两个只含指针的顶层别名目录，测试锁住其 frontmatter 与对应子页一致。

两侧共用一份 `scripts/` 与 `references/`。交接包是两侧唯一接口，规范在
`references/handoff.md`；agent 运行时看对应子 `SKILL.md`，使用者看
`docs/skills/repo-task-atk-test/design.md`，改阶段只改骨架。

## 四条红线（不可退让）

### 红线 1：不拿算子实现当验收依据

算子实现是待验收对象，用它生成测试配置或给精度失败下归因结论是循环论证。

**禁的是「实现」，不是「工程」。** 待验收算子工程要读，公开接口面是签名对齐的
唯一合法来源。

- **允许读：** 工程的头文件声明 / README / 设计文档 / 构建入口、任务书、ATK 源码、
  PyTorch/NumPy baseline 源码、skill 文档
- **禁止读：** kernel/host 计算逻辑、tiling 分支、内部断言、实现里的报错字符串常量

签名对齐只能用工程源码树里的头文件；指向 CANN 装机目录会拿到官方已发布的同名接口，
同名不代表同签名。

### 红线 2：ATK 是黑盒

不修改 `third_party/ATK/`。它是指向上游 `gitcode.com/Ascend/ATK.git` 的 git
submodule，改一行就会脏掉 submodule 指针，把本仓与上游的偏离一起带进提交。

行为不符预期 → 记录为已知问题，不打补丁。

### 红线 3：冻结纪律

S2 结束即冻结，此后输入 SHA256 不可变。接线改写走 `rewire_adapter.py`（唯一出口）。

S3 或 S4 失败都不得回到 S2 重新生成。

### 红线 4：Agent 声明不可信

判据从数据推导，不允许 agent 声称"我检查过了"就放行。

四条红线在两侧的具体形态见拆分设计 §2.6；这里不复制那张两侧对照表。

## 核心架构理念

### 知识层前置构建（最高优先级）

**Why：** Agent 生成效率取决于前置知识完备性。知识缺失 → 执行期返工（成本 5-10x）。

**What：** 前置知识必须覆盖五大领域——目标产物规范（`artifact-contracts.json`）、
ATK 框架知识（`atk-cli.md`、`case-design.md`）、约束器生成规则
（`atk-parameter-capabilities.json`）、跑测适配规则（`plugin-authoring.md`）、
环境依赖链（`probe_env.py`）。

**验收标准：** Agent 一次生成成功率 >90%，手工自证次数 =0（所有判据机械化）。

详见 `docs/development/skill-development-principles.md` §1。

### 产物契约骨架（唯一真相来源）

**Why：** 历史问题是"按事故索引"，完备性不可判定。

**What：** `artifact-contracts.json` 回答四个问题——谁产出（`owner`）、依据什么
（`spec`）、谁消费（`consumed_by`）、写错了怎么炸（`failure`）。

派生视图有三类：作战卡由 `mark_step.py` 在阶段入口从骨架渲染、不签入任何文件；
决策点清单（`decision-points.md`）从 `owner: "agent"` 提取；四条不变量
（`test_contracts.py`）守护骨架合法性。

**视图从骨架派生，骨架是唯一可编辑对象。** 详见 principles §2。

### 判据机械化

**Why：** Agent 可能误判、可能理解错规则、可能为了通过门禁而撒谎。

四类判据全部从数据推导：适配器从 `range_values` 读 null token，基线绑定从函数签名
反射参数名，可表达性从 `semantic` 检查白名单，环境从 npu-smi 解析健康状态。

**禁止 Agent 声称"我已检查"就放行。** 详见 principles §3。

### Fail-fast 策略

**Why：** 不等到后期才暴露前期问题，减少返工成本。

三处关键前移：可表达性 S4 → S1（提前 3 阶段），基线绑定 S3 → S2（提前 1 阶段），
环境检查 S1 → S0（提前 1 阶段）。平均节省 2 个阶段的返工，详见 principles §4。

### 受控修改通道

**Why：** 接线字段改写风险极高，必须单一卡点校验语义不变性。

`rewire_adapter.py` 是接线改写唯一出口：白名单 `WIRING_KEYS = {"api_type",
"aclnn_api_type"}`，语义 diff 做 strip wiring 后 deep-equal 其余，不满足则退出码 2
回 S2。详见 principles §5。

### 知识分层（L0/L1/L2/L3）

**Why：** 事实与推导分层，模板通过显式引用锁定事实，防止漂移。

- **L0（事实层）：** ATK 源码、任务书、真机现实
- **L1（推导层）：** 从 L0 推导，如基线签名
- **L2（策略层）：** 跨算子通用规则
- **L3（模板层）：** 引用 L0-L2 的实现

锁定机制是 L3 显式引用 L0-L2 行号，事实变更时测试红灯。详见 principles §6。

## 目录结构

```
skill/repo-task-atk-test/
├── case-gen/
│   └── SKILL.md          # 生成侧入口：S1–S2
├── acceptance/
│   └── SKILL.md          # 验收侧入口：S0、S3–S5
├── scripts/              # 量具层
│   ├── _*.py             # 内部模块（无CLI）
│   ├── _taskdoc.py       # 任务书解析薄封装
│   ├── check_*.py        # 门禁（退出码0/2/3）
│   ├── check_bundle.py   # 交接包接收门
│   ├── make_*.py         # 生成器
│   ├── probe_env.py      # S0环境探测
│   ├── probe_progress.py # 压缩后从盘上产物反推进度并重打卡
│   ├── gate_lookup.py    # 按量具名查检查点（替代通读清单）
│   ├── _axis_binding.py  # 轴取值词表与dtype出处
│   ├── seal_bundle.py    # 交接包封印门
│   ├── freeze_golden.py  # S3真实拓扑golden冻结
│   └── rewire_adapter.py # 接线改写唯一出口
├── references/           # 运行时知识
│   ├── artifact-contracts.json       # 唯一骨架（量具读，验收时不打开）
│   ├── decision-points.md            # Agent决策清单（从骨架派生）
│   ├── handoff.md                    # 交接包规范
│   ├── atk-parameter-capabilities.json
│   ├── atk-cli.md
│   ├── case-design.md
│   ├── plugin-authoring.md
│   ├── build-deploy.md
│   └── ...
├── tests/                # 测试层
│   ├── _paths.py         # 嵌套源与展开产物的唯一路径解析
│   ├── _decl_fixture.py  # 两侧共用夹具
│   ├── case_gen/         # 只测生成侧量具与知识
│   ├── acceptance/       # 只测验收侧量具与知识
│   └── shared/           # 共用件、骨架、行文、路由与跨侧边界
└── SKILL.md              # 入口
```

## ATK 版本锁在哪

版本锁在提交对象的 gitlink 里（`git ls-tree HEAD third_party/`），不在 `.gitmodules`
——那里没有 `branch =`，所以上游 master 往前走不影响本仓。升级子模块必须显式提交
gitlink 变化，藏不住。

升级后能力矩阵可能失效：矩阵是从某一版 ATK 实测出来的。`test_atk_version_pin.py`
会把子模块自报的 `PACKAGE_VERSION` 与矩阵的 `atk_versions` 对起来，对不上就红，
并指向 `probe_atk_capabilities.py` 重探。

## 术语表

| 术语 | 含义 |
|---|---|
| 任务书 | 算子开发任务书（精度/性能要求） |
| 基线 | PyTorch/NumPy 参考实现 |
| 约束器 | `constraint.py`（参数取值规则） |
| 适配器 | `plugin.py`（处理None等类型） |
| 冻结 | S2 结束时锁定输入 SHA256 |
| 交接包 | 生成侧封印后交给验收侧的完整工作目录 |
| 封印 | `seal_bundle.py` 核齐产物并写入文件摘要清单 |
| 接收门 | `check_bundle.py` 重算摘要并核对任务书、ATK 版本与接口 |
| 子 skill | 共用父目录量具与知识、分别承接生成或验收阶段的入口 |
| 接线字段 | `api_type`, `aclnn_api_type` |
| L0-L3 | 知识分层：事实/推导/策略/模板 |

## 常见问题

**Q: 为什么不读算子实现？**
A: 算子是待验收对象，用它生成测试是循环论证。但工程目录本身要读——头文件里的接口
声明是签名对齐的唯一来源，去装机 CANN 找同名接口会拿到另一份代码的签名。

**Q: Agent 生成失败，是 skill 还是 agent 问题？**
A: 检查知识层：`atk-parameter-capabilities.json` 是否覆盖该参数类型？错误是否在
`plugin-authoring.md` 有预警？都没有 → skill 知识缺失。

**Q: 如何判断门禁该删还是留？**
A: 它校验的是「脚本刚生成的」→ 删；「agent 写的 + 真机现实」→ 留。

**Q: 冻结后接线错了怎么办？**
A: 走 `rewire_adapter.py`，它会校验语义不变（只改接线）。不满足 → 退出码 2，回 S2。

## 行文结构化欠账

2026-08-18 定了行文规则（`.claude/rules/prose-style.md`），本 skill 有 **276 处
存量违规**未改，由 `test_document_style.py` 的 `PROSE_BASELINE` 棘轮兜住，
不阻塞新工作。

按规则分：273 处连续单句自然段、3 处段内并列句未列表化。三份入口的基线分别是父
`SKILL.md` 0 处、`case-gen/SKILL.md` 2 处、`acceptance/SKILL.md` 1 处；欠账最重的
四个 reference 是 `plugin-authoring.md` 49 处、`case-design.md` 45 处、
`yaml-schema.md` 33 处、`build-deploy.md` 23 处。

**存量不还，理由是收益没测过而风险有案底。** 合并段落就是「精简」这个动作本身，
而本仓两次在精简中删掉过真机验证过的修复；兜底的字符串断言只认得「原来写的还在」，
发现不了漏掉一句。收益一侧则全是人类可读性论证，从没在 agent 行为上实测过。
下一个人想动手之前，先拿出一个能测出退化的判据。

棘轮的价值在拦新债，不在还旧债，这部分零成本且已生效。真要还也是一次一个文件：
改完把 `PROSE_BASELINE` 里对应的数字降下去，降到 0 就删掉那一行。基线只减不增，
`test_prose_baseline_has_no_stale_entries` 会拦住「改好了却不降基线」。

2026-08-19 计数单位从「一串」换成「一段」，合计因此从 88 变成 276——**数字变大不是
欠债变多**。原先一串连续单句段不管 3 个还是 17 个都只记 1 处，往里面继续加单句段
计数不变、棘轮不红，88 处存量于是全是能免费长大的口子。实测过：往 `case-design.md`
末尾追加三个单句段，旧记法 14 → 14 绿，新记法 45 → 48 红。

本文件不在棘轮里，基线是 0。

## 参考文档

- **架构演进与各轮复盘：** `docs/development/architecture-log.md`
- **设计文档：** `docs/superpowers/specs/2026-08-15-atk-knowledge-architecture-design.md`
- **Plan A：** `docs/superpowers/plans/2026-08-16-atk-gate-simplification.md`
- **Plan B：** `docs/superpowers/plans/2026-08-15-atk-contract-spine.md`
- **Plan C：** `docs/superpowers/plans/2026-08-16-atk-defect-closure.md`
- **拆分 spec：** `docs/superpowers/specs/2026-08-20-atk-skill-split-design.md`
- **拆分 plan：** `docs/superpowers/plans/2026-08-20-atk-skill-split.md`

**最后更新：** 2026-08-20（拆成生成用例与测试验收两个子 skill，父入口只负责路由）
