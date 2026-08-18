# CLAUDE.md — 社区算子验收 Skill 仓

每次会话自动加载。只写经过源码验证的事实。

---

## 0. 一句话

用 ATK 测社区算子，判定精度与性能是否满足任务书要求。

验收 Skill：`skill/repo-task-atk-test/`

唯一目标：**驱动零上下文 agent 独立跑完端到端验收。**

---

## 1. 验收流程

阶段定义的真相在 `references/artifact-contracts.json` 的 `stages`，此处只作索引。

```
S1: 任务书解读     → 约束表 + evidence/env.json + evidence/interface.json
S2: 用例生成       → must_cover.json + YAML + 插件 + 用例 JSON + 冻结输入
S3: 编译安装部署   → 绑定报告 + 冒烟日志
S4: 精度性能测试   → accuracy/performance results + verdict
S5: 输出测试结果   → 报告 + 结论 + 复现包
```

---

## 2. 四条红线（不可退让）

### 红线 1：不拿算子实现当验收依据
算子实现是待验收对象，用它生成测试配置或给精度失败下归因结论是循环论证。

**禁的是「实现」，不是「工程」。** 待验收算子工程要读，公开接口面是签名对齐的唯一合法来源。

**允许读：** 待验收算子工程的头文件声明 / README / 设计文档 / 构建入口、任务书、ATK 源码、PyTorch/NumPy baseline 源码、skill 文档

**禁止读：** kernel/host 计算逻辑、tiling 分支、内部断言、实现里的报错字符串常量

签名对齐只能用工程源码树里的头文件；指向 CANN 装机目录会拿到官方已发布的同名接口，同名不代表同签名。

### 红线 2：ATK 是黑盒
不修改 `third_party/ATK/`。它是指向上游 `gitcode.com/Ascend/ATK.git` 的 git submodule，
改一行就会脏掉 submodule 指针，把本仓与上游的偏离一起带进提交。
行为不符预期 → 记录为已知问题，不打补丁。

### 红线 3：冻结纪律
S2 结束即冻结，此后输入 SHA256 不可变。
接线改写走 `rewire_adapter.py`（唯一出口）。
S3 或 S4 失败都不得回到 S2 重新生成。

### 红线 4：Agent 声明不可信
判据从数据推导，不允许 agent 声称"我检查过了"就放行。

---

## 3. 核心架构理念

### 3.0 知识层前置构建（最高优先级）

**Why：** Agent 生成效率取决于前置知识完备性。知识缺失 → 执行期返工（成本 5-10x）。

**What：** 前置知识必须覆盖 5 大领域
1. 目标产物规范（`artifact-contracts.json`）
2. ATK 框架知识（`atk-cli.md`, `case-design.md`）
3. 约束器生成规则（`atk-parameter-capabilities.json`）
4. 跑测适配规则（`plugin-authoring.md`）
5. 环境依赖链（`probe_env.py`）

**验收标准：**
- Agent 一次生成成功率 >90%
- 手工自证次数 =0（所有判据机械化）

**How：** 详见 `docs/development/skill-development-principles.md` §1

---

### 3.1 产物契约骨架（唯一真相来源）

**Why：** 历史问题是"按事故索引"，完备性不可判定。

**What：** `artifact-contracts.json` 回答四个问题
- 谁产出（`owner: "agent" | "script" | "atk"`）
- 依据什么（`spec` 指向规范文档）
- 谁消费（`consumed_by` 依赖链）
- 写错了怎么炸（`failure` 失败模式）

**派生视图：**
- 作战卡：由 `mark_step.py` 在阶段入口从骨架渲染，不签入任何文件
- 决策点清单（decision-points.md）：从 `owner: "agent"` 提取
- 四条不变量（test_contracts.py）：守护骨架合法性

**原则：** 视图从骨架派生，骨架是唯一可编辑对象。

**How：** 详见 `docs/development/skill-development-principles.md` §2

---

### 3.2 判据机械化

**Why：** Agent 可能误判、可能理解错规则、可能为了通过门禁而撒谎。

**What：** 四类判据全部从数据推导
- 适配器：从 `range_values` 读 null token
- 基线绑定：从函数签名反射参数名
- 可表达性：从 `semantic` 检查白名单
- 环境：从 npu-smi 解析健康状态

**禁止：** Agent 声称"我已检查"就放行。

**How：** 详见 `docs/development/skill-development-principles.md` §3

---

### 3.3 Fail-fast 策略

**Why：** 不等到后期才暴露前期问题，减少返工成本。

**What：** 三处关键前移
- 可表达性：S4 → S1（提前 3 阶段）
- 基线绑定：S3 → S2（提前 1 阶段）
- 环境检查：S1 → S0（提前 1 阶段）

**成本收益：** 平均节省 2 个阶段的返工

**How：** 详见 `docs/development/skill-development-principles.md` §4

---

### 3.4 受控修改通道

**Why：** 接线字段改写风险极高，必须单一卡点校验语义不变性。

**What：** `rewire_adapter.py` 是接线改写唯一出口
- 白名单：`WIRING_KEYS = {"api_type", "aclnn_api_type"}`
- 语义 diff：strip wiring → deep-equal 其余
- 不满足 → 退出码 2，回 S2

**How：** 详见 `docs/development/skill-development-principles.md` §5

---

### 3.5 知识分层（L0/L1/L2/L3）

**Why：** 事实与推导分层，模板通过显式引用锁定事实，防止漂移。

**What：**
- L0（事实层）：ATK 源码、任务书、真机现实
- L1（推导层）：从 L0 推导（如基线签名）
- L2（策略层）：跨算子通用规则
- L3（模板层）：引用 L0-L2 的实现

**锁定机制：** L3 显式引用 L0-L2 行号 → 事实变更时测试红灯

**How：** 详见 `docs/development/skill-development-principles.md` §6

---

## 4. 文件结构与开发态启动

```
repo-task-atk-test/           # 本仓（远程 gitcode.com/Justbin/repo-task-atk-test）
├── CLAUDE.md                 # 本文件
├── skill/repo-task-atk-test/ # skill 本体，唯一发布物
├── docs/                     # 开发侧文档，不随 skill 发布
│   ├── development/          # 开发规则 + 架构演进
│   └── superpowers/          # specs/ 与 plans/
└── third_party/ATK/          # submodule → gitcode.com/Ascend/ATK（红线 2：只读）
```

```
skill/repo-task-atk-test/
├── scripts/              # 量具层
│   ├── _*.py             # 内部模块（无CLI）
│   ├── check_*.py        # 门禁（退出码0/2/3）
│   ├── make_*.py         # 生成器
│   ├── probe_env.py      # S0环境探测
│   ├── probe_progress.py # 压缩后从盘上产物反推进度并重打卡
│   ├── gate_lookup.py    # 按量具名查检查点（替代通读清单）
│   ├── _axis_binding.py  # 轴取值词表与dtype出处
│   └── rewire_adapter.py # 接线改写唯一出口
├── references/           # 运行时知识
│   ├── artifact-contracts.json       # 唯一骨架（量具读，验收时不打开）
│   ├── decision-points.md            # Agent决策清单（从骨架派生）
│   ├── atk-parameter-capabilities.json
│   ├── atk-cli.md
│   ├── case-design.md
│   ├── plugin-authoring.md
│   ├── build-deploy.md
│   └── ...
├── tests/                # 测试层
│   ├── test_contracts.py # 四条不变量
│   └── test_*.py         # 62+回归测试
└── SKILL.md              # 入口
```

### 开发态启动（先跑通这一步，否则回归会假绿）

克隆漏了 `--recursive` 会拿到空的 `third_party/ATK/`，补救：

```bash
git submodule update --init
```

**ATK 没有被 pip 安装，跑回归必须显式给 `PYTHONPATH`：**

```bash
PYTHONPATH=third_party/ATK python3 -m pytest skill/repo-task-atk-test/tests/ -q
```

迁出前 skill 寄生在 ATK 仓根里，`import atk` 只是 CWD 巧合命中了 `./atk/`。
本仓 ATK 在 `third_party/ATK/atk`，巧合不再成立。

量具本身不会因此假通过——它们都响亮失败：`validate_cases.py` 退出码 3、
`capture_reference.py` 抛 `CaptureError`、`align_signatures.py` 让 ImportError 冒出来。
**会假绿的是回归测试**：`test_override_effective` 的 C2b 在 import 不到 ATK 时自行 skip，
于是不给 PYTHONPATH 跑出来的"全绿"比真实覆盖少了一块，而计数看不出来。

不要图省事跑 `pip install -e third_party/ATK`：本机是 homebrew python3.14 且无 venv，
装下去会污染全局 site-packages。要装先建 venv。

### ATK 版本锁在哪

版本锁在提交对象的 gitlink 里（`git ls-tree HEAD third_party/`），
不在 `.gitmodules`——那里没有 `branch =`，所以上游 master 往前走不影响本仓。

升级子模块必须显式提交 gitlink 变化，藏不住。

升级后能力矩阵可能失效：矩阵是从某一版 ATK 实测出来的。
`test_atk_version_pin.py` 会把子模块自报的 `PACKAGE_VERSION` 与矩阵的
`atk_versions` 对起来，对不上就红，并指向 `probe_atk_capabilities.py` 重探。

---

## 5. 开发流程

修改 skill 的标准流程：

1. **骨架先行：** 新产物进 `artifact-contracts.json`
2. **知识前置：** 在 `skill/references/` 补充规范（不是在脚本里写注释）
3. **TDD 实施：** 写失败测试 → 实现 → 测试通过
4. **派生视图：** `render_views.py --write`（`--cards` 只给维护者看渲染结果）
5. **验收：** 四条不变量 + 防漂移测试

**遇到问题时的查找顺序：**
1. 先读 `docs/development/skill-development-principles.md`（开发规则）
2. 再读 `skill/repo-task-atk-test/references/`（运行时知识）
3. 最后读源码（量具实现）

---

## 6. 术语表

| 术语 | 含义 |
|---|---|
| 任务书 | 算子开发任务书（精度/性能要求） |
| 基线 | PyTorch/NumPy 参考实现 |
| 约束器 | `constraint.py`（参数取值规则） |
| 适配器 | `plugin.py`（处理None等类型） |
| 冻结 | S2 结束时锁定输入 SHA256 |
| 接线字段 | `api_type`, `aclnn_api_type` |
| 骨架 | `artifact-contracts.json` |
| L0-L3 | 知识分层：事实/推导/策略/模板 |

---

## 7. 常见问题

**Q: 为什么不读算子实现？**
A: 算子是待验收对象，用它生成测试是循环论证。但工程目录本身要读——头文件里的接口声明是签名对齐的唯一来源，去装机 CANN 找同名接口会拿到另一份代码的签名。

**Q: Agent生成失败，是skill还是agent问题？**
A: 检查知识层：`atk-parameter-capabilities.json`是否覆盖该参数类型？错误是否在`plugin-authoring.md`有预警？都没有→skill知识缺失。

**Q: 如何判断门禁该删还是留？**
A: 它校验的是「脚本刚生成的」→删；「agent写的+真机现实」→留。

**Q: 冻结后接线错了怎么办？**
A: 走 `rewire_adapter.py`，它会校验语义不变（只改接线）。不满足→退出码2，回S2。

---

## 8. 架构演进

完整演进记录与各轮复盘的逐条修复清单：`docs/development/architecture-log.md`。

流水账不进 CLAUDE.md：它每次会话都进上下文，而读它的人正在改代码，
不是在查历史。

**当前状态：** 本仓实测 22 failed, 756 passed, 13 skipped（跑法见 §4 开发态启动）。

22 条既存失败 = 20 条依赖 torch（本机未安装）+ 2 条行文门禁红
（`experimental_standard.md` 168/178 行超长、176 行用了「符号」）。

该数字与迁出前旧仓基线一致，搬运无损。

**最后更新：** 2026-08-17（skill 从 ATK 上游克隆迁出为独立仓）

---

## 9. 参考文档

**开发规则详解：** `docs/development/skill-development-principles.md`

**设计文档：** `docs/superpowers/specs/2026-08-15-atk-knowledge-architecture-design.md`

**实施计划：**
- Plan A: `docs/superpowers/plans/2026-08-16-atk-gate-simplification.md`
- Plan B: `docs/superpowers/plans/2026-08-15-atk-contract-spine.md`
- Plan C: `docs/superpowers/plans/2026-08-16-atk-defect-closure.md`
