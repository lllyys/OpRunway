# CLAUDE.md — 社区算子验收 Skill

改这个 skill 时读。运行时规则在 `SKILL.md`，这里是开发规则。

行文、脚本命名与退出码约定、TDD 流程、开发态启动，全部沿用仓根 `CLAUDE.md`，
不在这里复制。复用的是**怎么写**，不是**写什么**——后者每个 skill 自己推导。

唯一目标：**驱动零上下文 agent 独立跑完端到端验收。**

## 验收流程

本目录承接生成侧 S1–S2。阶段归属的真相在
`references/artifact-contracts.json` 的 `stages[*].skill`，量具从那里读取：

- S1 把任务书变成约束、环境与接口证据
- S2 生成、校验并冻结用例，最后由 `seal_bundle.py` 封印交接包

生成侧不读算子工程；签名只来自任务书 §2.3，dtype 只来自 §2.4。任务书写不清就停在
待确认项，由用户先补任务书。脚本归属由骨架 `scripts` 表与
`test_script_ownership.py` 锁住。

交接包是与 `repo-task-atk-accept` 的唯一接口，规范在 `references/handoff.md`。
运行时读本目录 `SKILL.md`，使用者说明在
`docs/skills/repo-task-case-gen/`，改阶段先改骨架。

## 共用件双份纪律

本目录与另一平级 skill 共用一批文件。共用清单以
`references/artifact-contracts.json` 的 `scripts` 与 `references` 表中
`skill: "shared"` 的标注为准，不另维护一份手写清单。

修改共用件必须在 `repo-task-case-gen` 与 `repo-task-atk-accept` 两侧同时改。
仓级 `plugin/tests/test_shared_sync.py` 会核对共同路径文件的字节内容，并检查骨架登记的
共用件两侧都存在；失败信息会给出可直接使用的 `cp` 命令。

`SKILL.md` 与 `CLAUDE.md` 是设计内的同名差异：两侧都必须存在，但内容按阶段分别维护。

## 四条红线（不可退让）

### 红线 1：不拿算子实现当验收依据

算子实现是待验收对象，用它生成测试配置或给精度失败下归因结论是循环论证。

**禁的是「实现」，不是「工程」。** 待验收算子工程要读，公开接口面是签名对齐的
唯一合法来源。

- **允许读：** 工程的头文件声明 / README / 设计文档 / 构建入口、任务书、ATK 源码、
  PyTorch/NumPy baseline 源码、skill 文档
- **禁止读：** kernel/host 计算逻辑、tiling 分支、内部断言、实现里的报错字符串常量

生成侧的签名只来自任务书 §2.3；验收侧的签名只来自工程源码树里的头文件。
指向 CANN 装机目录会拿到官方已发布的同名接口，同名不代表同签名。

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
skill/repo-task-case-gen/
├── CLAUDE.md             # 本文件：生成侧开发规则
├── SKILL.md              # 生成侧入口：S1–S2 与封印
├── assets/example/       # 可复制的最小用例生成素材
├── references/           # 生成知识、交接规范与共用事实
│   ├── artifact-contracts.json  # 唯一骨架
│   ├── case-design.md           # 用例设计与分面
│   ├── plugin-authoring.md      # 约束器与适配器
│   └── handoff.md               # 两侧交接面
├── scripts/              # 生成器、门禁、冻结与封印量具
└── tests/                # 生成侧、共用件与行文防漂移测试
```

下划线开头的脚本是内部模块；可调用量具的归属与文档入口以骨架为准。

## ATK 版本锁在哪

版本锁在提交对象的 gitlink 里（`git ls-tree HEAD third_party/`），不在 `.gitmodules`
——那里没有 `branch =`，所以上游 master 往前走不影响本仓。升级子模块必须显式提交
gitlink 变化，藏不住。

升级后能力矩阵可能失效：矩阵是从某一版 ATK 实测出来的。生成侧的
`test_atk_version_pin.py` 会把子模块自报的 `PACKAGE_VERSION` 与矩阵的
`atk_versions` 对起来，对不上就红，
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
| 平级 skill | 各自带量具与知识、通过交接包承接生成或验收阶段的入口 |
| 接线字段 | `api_type`, `aclnn_api_type` |
| L0-L3 | 知识分层：事实/推导/策略/模板 |

## 常见问题

**Q: 为什么不读算子实现？**
A: 算子是待验收对象，用它生成测试是循环论证。生成侧只读任务书里的接口声明；验收侧
只读工程头文件的声明，去装机 CANN 找同名接口会拿到另一份代码的签名。

**Q: Agent 生成失败，是 skill 还是 agent 问题？**
A: 检查知识层：`atk-parameter-capabilities.json` 是否覆盖该参数类型？错误是否在
`plugin-authoring.md` 有预警？都没有 → skill 知识缺失。

**Q: 如何判断门禁该删还是留？**
A: 它校验的是「脚本刚生成的」→ 删；「agent 写的 + 真机现实」→ 留。

**Q: 冻结后接线错了怎么办？**
A: 走 `rewire_adapter.py`，它会校验语义不变（只改接线）。不满足 → 退出码 2，回 S2。

## 行文结构化欠账

2026-08-18 定了行文规则（`.claude/rules/prose-style.md`）。按本目录
`test_document_style.py` 的 `PROSE_BASELINE` 对现存文件合计，生成侧有
**194 处存量违规**：入口 `SKILL.md` 2 处，references 共 192 处。

欠账最重的四份 reference 是 `plugin-authoring.md` 49 处、`case-design.md` 45 处、
`yaml-schema.md` 33 处、`atk-parameter-capabilities.md` 20 处。
棘轮只允许数字下降；实际计数低于基线时必须同步降低表中的值。

**存量不还，理由是收益没测过而风险有案底。** 本仓两次在精简中删掉过真机验证的修复，
现有字符串断言只能防已知句子消失，不能证明语义完整。要还债时一次只改一份文件，
先补能测出行为退化的判据；本文件作为新文件，基线为 0。

## 参考文档

- **使用者设计：** `docs/skills/repo-task-case-gen/design.md`
- **使用者上手：** `docs/skills/repo-task-case-gen/quickstart.md`
- **开发原则：** `docs/development/skill-development-principles.md`
- **架构演进：** `docs/development/architecture-log.md`
- **拆分历史 spec：** `docs/superpowers/specs/2026-08-20-atk-skill-split-design.md`
- **拆分历史 plan：** `docs/superpowers/plans/2026-08-20-atk-skill-split.md`

**最后更新：** 2026-08-24（S11：生成侧成为平级、自足的 skill 目录）
