# 插件与具体算子解耦 —— 设计方案

> 起因：想在隔离环境里「模拟一个已安装插件的用户」验收 IsClose，发现插件肚子里带着 IsClose 的现成答案。
> 追问「为什么会有答案」，挖出的不是残留，是**能力边界**：通用 elementwise 路径只认识 4 个算子。
>
> **状态：方案。runner 侧（S5-runner）已实施**（2026-07-20，分支 `refactor/runner-out-of-engine`：3 份样例 runner 迁 `samples/runners/`、fallback 退役改 fail-closed、runner 只作输出）；**golden 侧（D1/S1）+ 来源分级（S2）+ oracle_source 门（S3）仍未实施**，须先走 ADR（golden 归属，见 §五 #5）。

---

## 一句话结论

`gen_cases.py` 把每个算子的 golden 写死成 Python 函数（`GOLDEN` 字典，4 个算子），
于是「插件里有具体任务内容」不是清理问题，是**通用 elementwise 路径只认这 4 个算子**。

⚠ **注意 scope**：catlass matmul 另有独立 builder（`catlass_adapter.py:186` 注释：「matmul caseset **有意**由本
builder 产、不进 gen_cases 的 GOLDEN 字典」），所以「插件只能验收 4 个算子」是**过强的全称断言**。
但那条路径只产 **development-grade evidence**、**不出验收裁决**（不产 `verdict.json` / `acceptance.json`、不跑门
——见 canon `Synthetic catlass demo cannot forge a PASS acceptance`，tier `proposed`）。
**准确表述**：插件的**验收裁决**能力当前仅覆盖 `GOLDEN` 里硬注册的 4 个 elementwise 算子；catlass 路径是 demo·synthetic，不出裁决。

而 canon 早已（`canonical` tier）定好了 golden 该怎么来——代码与之不符。

---

## 一、canon 怎么说（按 trust tier 标注）

| 出处 | tier | 说了什么 |
|---|---|---|
| `acceptance-contract-evidence-chain.md` | **canonical** | 每条用例至少含 **13** 字段，其中 **`oracle_source`** ∈ {`analytical_ref`, `cpu_ref`, `torch_ref`, `catlass_existing_ref`, `task_spec_expected`, `external_ref`}；另有 `spec_clause_ref` / `pr_change_ref`，「Task 1 须含 PR 影响面分析，把改动映射到用例，**否则证明不了验收覆盖了 PR**」 |
| ADR 0002 | **canonical** | 原文：「泛化到 ops-blas/ops-cv/tilelang 时 **golden/构建/性能入口都变**，见 Repo adapter」。⚠ 只说跨仓时 golden 会变，**未明文规定 golden 函数归 repo_adapter 持有**——「golden 属 repo_adapter 层」是本方案的**推导**，非 canon 既有结论 |
| `ascendoptest-precision-thresholds.md` | **canonical** | 「**golden 由 `expect_func` 提供**：我们写 numpy 融合参考，输出 dtype 须与算子输出一致」 |
| `primitive-to-case-rule-library.md` | **canonical** | 「golden = 各原语 numpy 参考按公式拼……**动态/分组语义先从 host 接口锁定（错则所有精度用例废）**」 |
| `catlass-acceptance-mechanics.md` | **canonical** | catlass 的 golden = CPU host float32，在 `examples/common/golden/`，**可源码级复用** |
| `repo-adapter.md` | **canonical** | 统一接口 7 方法：`discover`/`build`/`materialize_case`/`run_correctness`/`run_perf`/`parse_results`/`collect_artifacts`（换实现、不换接口） |
| ADR 0008 | proposed | 「我们只提供 `expect_func`（numpy 融合 golden）」 |
| `machine-verifiable-acceptance-gate.md` | verified | 门只读落盘证据：防跑子集 / 防放宽阈值 / 防混 e2e / 抗坏输入 |
| `gate-checks-evidence-integrity-not-verdict.md` | proposed | 门只管「证据可信 + 完整」，不重判 pass/fail |
| `gate-must-check-the-effective-object.md` | proposed | **门校验的对象与实际生效的对象若不是同一个，绿色就没有意义**；门必须 fail-closed |
| `verify-spec-pr-correspondence-before-acceptance.md` | proposed | 验收前先验「任务书 ↔ PR」对应；Equal 案例：误配 PR → **下游一切裁决作废** |

**关键**（区分「canon 已定」与「本方案推导」）：
- **canon 已定**：golden 的来源是 canonical 契约里的 **per-case 字段 `oracle_source`**，六个枚举值早已定死；
  golden 本身是 **per-op 的 `expect_func`**（`ascendoptest-precision-thresholds.md`，canonical）；
  catlass 自带的 golden **可源码级复用**（`catlass-acceptance-mechanics.md`，canonical）。
- **本方案推导（非 canon 结论）**：golden 的源码**归属**（是否属 repo_adapter 层）、以及来源的**优先级排序**——
  ADR 0002 只说跨仓时 golden 会变，未规定归属。见 §三 S1/S2，须经 ADR 确认。

---

## 二、代码偏离了什么

按严重度排序。

### D1 · 通用 elementwise 路径只认识 4 个算子（阻断级）

```python
# gen_cases.py:113
GOLDEN = {"IsClose": ("numpy np.isclose", golden_isclose),
          "Sign":    ("numpy np.sign",    golden_sign),
          "Equal":   ("numpy np.equal",   golden_equal),
          "Neg":     ("numpy np.negative", golden_neg)}

# gen_cases.py:311
if op not in GOLDEN:
    raise ValueError(f"unsupported op {op!r}, supported={list(GOLDEN)}")
```

用户拿这条路径验收第 5 个算子 → 第一步 `gen_cases` 直接抛异常。加算子 = 改插件源码。
（catlass 走的是另一条 builder，不受此限；但它不出验收裁决，见上方 scope 注。）

- **与 ADR 0002（canonical）的跨仓泛化方向不符（推断）**。ADR 0002 原文只说「泛化到 ops-blas/ops-cv/tilelang 时
  **golden/构建/性能入口都变**，见 Repo adapter」，**并未明文规定 golden 函数必须归 repo_adapter 持有**；
  `repo-adapter.md` 的七方法里也没有 golden 一项。能坐实的是「硬注册阻碍泛化」，
  **golden 的源码归属仍需 ADR/设计确认**——本方案 S1 提的归属是提案，不是既定 canon。
- 与 `CLAUDE.md` 自述矛盾：那里写「加算子 = spec + golden + runner **三文件**」，但 golden 不是文件，是函数。
- **这就是「为什么插件里有 IsClose 的答案」的机制性原因**：答案不是残留，是内置。

### D2 · `oracle_source` 是写死的常量 —— 「门校错对象」第三例

canonical 契约要求 per-case 记录 `oracle_source`（6 枚举）。实际：

- `gen_cases.py` **完全不产**这个字段；
- 只在 evidence 层出现，两处都是常量：
  - `repo_adapter.py:131` → `"oracle_source": "cpu_ref"`
  - `catlass_adapter.py:379` → `"oracle_source": "cpu_ref"`

门（`validate_acceptance_state.py`）读 evidence 校完整性，于是**永远看到一个合法的 `cpu_ref`**，
无论 golden 实际从哪来。

**诚实边界**：当前四个 golden 都是 **NumPy host 计算**——但「跑在 host Python 上」**不自动等于** canonical 枚举里的
`cpu_ref`。`golden_isclose` 这类「按算子公式现写的 numpy 参考」在语义上更接近 `analytical_ref`。
**这四个函数的 `oracle_source` 尚未逐项核定**，所以写死 `cpu_ref` 可能对部分算子恰好成立，
**不能整体声称「恰好正确」**（先前版本如此断言，此处更正）。

无论逐项核定结果如何，这都是 fail-open 的设计——字段记录的是假设，不是事实。
一旦 golden 来源按 canonical 变成 `catlass_existing_ref`，字段不会跟着变，门也校不出来。

同构于 `gate-must-check-the-effective-object.md` 记的两例（`plugin.json` 的 `agents` 字段 /
`check_agent_frontmatter.py` 校项目自定约定）。本条是第三例。

### D3 · 契约列了 13 个 per-case 字段，顶层字面命中只有 5 个

canonical 契约 `acceptance-contract-evidence-chain.md` 列的是 **13 个**（先前版本误记为 12）：
`id` `kind` `case_origin` `spec_clause_ref` `pr_change_ref` `applicability` `dtype` `shape`
`oracle_source` `tolerance_policy_id` `timing_policy_id` `perf_baseline_source` `expect`

| 字段 | gen_cases 顶层字面命中？ |
|---|---|
| `id` · `case_origin` · `dtype` · `shape` · `tolerance_policy_id` | ✓ |
| `kind` · `spec_clause_ref` · `pr_change_ref` · `applicability` · `oracle_source` · `timing_policy_id` · `perf_baseline_source` | ✗ |

⚠ **口径**：上表是「字段名在 `gen_cases.py` 里字面出现」的粗查。代码另产 `dims` / `tags` / `inputs` / `attrs` /
`expected`，且**部分契约语义嵌套在 `expected` 之内**（契约写 `expect`，代码用 `expected`——命名/层级 drift）。
所以「只覆盖 5 个」是**顶层字面口径，不是完整的契约合规审计**。真要下结论须逐字段比对语义与嵌套层级。

可确证的是：`spec_clause_ref` / `pr_change_ref` **在代码里任何层级都不存在** ⇒ canonical 契约里
「把 PR 改动映射到用例，否则**证明不了验收覆盖了 PR**」这条**没有实现**。
`gen_cases` 有 `rule_ref`（规则来源），但那是「哪条造例规则」，不是「任务书哪一条 / PR 哪个改动」。

### D4 · 算子名当白名单，驱动比较语义

```python
# gen_cases.py:34
_BF16_EXACT_OPS = frozenset({"Sign", "Neg"})
# gen_cases.py:373 —— 不在名单里的 bf16 数值算子，一律拒
if dtn == _BF16 and not out_is_bool and op not in _BF16_EXACT_OPS:
    raise ValueError(...)
```

「bf16 输出精确可表示」是算子的**数学性质**（sign 只出 {-1,0,1}、neg 精确取负），不是算子的**名字**。
`Abs` / `Ceil` / `Floor` / `Round` 性质相同却会被拒；反之若注册一个同名不同义的 `Sign`，白名单会错误放行。
spec 里现在没有能表达这个性质的字段。

### D5 · 用户产物落在插件安装目录

| 位置 | 说了什么 |
|---|---|
| `skills/acceptance-workflow/SKILL.md:10` | 产物落**用户 CWD** 的 `reports/<op>/` |
| `skills/acc-spec/SKILL.md:31` | spec 落 `${CLAUDE_PLUGIN_ROOT}/acc-common/specs/<op>.spec.json` |
| `acc-common/repo_adapter.py:230` | runner 只从 `os.path.join(here, "new_example", …)` 找，`here` = 插件安装目录 |

**必须分开两类东西**（先前版本混为一谈）：

**(a) 运行时新生成的用户产物** —— `acc-spec` 为**当前任务**产的 spec、`acc-runner` 为**当前算子**产的 runner。
把它们默认写进插件安装目录，与工程约定「零持久化配置；所有产物落用户 CWD 下的 `reports/`」**冲突**。
真实用户 `/plugin install` 后插件在 `~/.claude/plugins/cache/` 下，插件一更新就冲掉。**这才是落点违规。**

**(b) 随插件发布的样例 / fixture** —— 仓里现存的这些文件：
`samples/specs/*.spec.json` × 5（Q1 已迁）· `samples/runners/oprunway_{isclose,sign,equal}_runner.cpp` × 3（2026-07-20 已迁）·
`acc-common/catlass/oprunway_catlass_basic_matmul_{950,a2}_runner.cpp` × 2 · `workflows/archive_ops/{isclose,sign}/`

它们随插件分发，本身**不构成落点违规**（发行物带样例是正常的）。它们的问题是**另一个**：
与 D1 的 `GOLDEN` 硬注册叠加后，**demo 期的脚手架被当成了插件的内置能力**——用户看到 `specs/isclose.spec.json`
就以为插件"支持 IsClose"，而真相是「这条 elementwise 路径恰好硬编码了它」。
是否把它们移出插件（如迁到顶层 `samples/`）**是一项新的设计选择，不是修 bug**。

### D6 · 默认值指向具体算子

`repo_adapter.py:205` → `g("OPRUNWAY_OP", "is_close")`。
工程约定说「默认值只作**常见值**呈现给用户确认」——但算子名没有「常见值」可言。

### 不是问题的（撤回 / 澄清）

- `validate_acceptance_state.py:287` 的 `math.isclose(...)` 是 **Python 标准库函数**，与算子 IsClose 无关。假阳性。
- `precision_policy.py` 的 5 处、`gen_cases.py` 的大半、`repo_adapter.py:44,221` —— **注释里举例**，不驱动行为，有教学价值，建议保留。

---

## 三、设计

### S1 · golden 契约（插件不含任何算子）

```python
# 契约：per-op，用户侧文件
def golden_fn(inputs: list[np.ndarray], attrs: dict) -> np.ndarray: ...

# 伴随元数据（决定 oracle_source，不能省）
GOLDEN_SOURCE = "cpu_ref"          # ∈ canonical 六枚举
GOLDEN_PROVENANCE = "抠自 ops-math PR #2943 的 CPU 参考实现 src/.../is_close_cpu.cc"
```

`gen_cases.GOLDEN` 由**内置字典**改为**加载器**：从用户目录按 op 名加载 `golden.py`，
读出 `golden_fn` + `GOLDEN_SOURCE` + `GOLDEN_PROVENANCE`。插件自身**不含任何算子的 golden**。

改动其实不大 —— 现有 `GOLDEN[op]` 返回的就是 `(src_name, golden_fn)` 二元组，形态已经对了，
只是那张表被硬编码在插件里。把「表」换成「加载」即可。

**fail-closed**：加载不到 / 缺 `GOLDEN_SOURCE` / source 不在六枚举内 → 直接失败，不猜、不兜底。

### S2 · golden 来源优先级（新提案，需确认）

canon 定了六个枚举值，但**没定优先级**。这是本方案新增的部分：

| 优先级 | `oracle_source` | 来源 | 可信度 |
|---|---|---|---|
| 1 | `catlass_existing_ref` | 仓自带参考实现（catlass `examples/common/golden/` canonical 标"可源码级复用"） | 最高 |
| 2 | `cpu_ref` | PR 里的 CPU 参考实现 | 高 |
| 3 | `torch_ref` | torch 等价算子 | 高 |
| 4 | `task_spec_expected` | 任务书给的期望值表 | 中 |
| 5 | `analytical_ref` | **agent 按任务书公式现写的 numpy 参考** | **最低** |
| 6 | `external_ref` | 外部给定 | 视来源 |

**为什么要分级**：`primitive-to-case-rule-library.md`（canonical）已经警告
「动态/分组语义先从 host 接口锁定，**错则所有精度用例废**」。
golden 若由 agent 自己按公式写而写错了，整条精度链失真——**而且会"验收通过"**。
`verify-spec-pr-correspondence-before-acceptance.md` 的 Equal 案例是同一类事故的实证：
最上游配错，下游门再严都是空的。

所以 `analytical_ref`（agent 自写）必须：① 排在最后；② 在报告里显式标注可信度；
③ 触发人工 CP（`AskUserQuestion`），不静默通过。

### S3 · `oracle_source` 成为真实字段，门校它

1. `gen_cases` 把 golden 加载时读到的 `GOLDEN_SOURCE` 写进**每条 case**（Task 1 产物）。
2. `repo_adapter` / `catlass_adapter` **透传**，不再自己填常量。
3. 门 `validate_acceptance_state.py` 增校：`evidence[i].oracle_source == caseset[i].oracle_source`（同 id 一一对应），
   且值 ∈ 六枚举。任一不符 → `FAILED`。

这样门校的就是**实际生效的那个对象**，堵上 D2。

### S4 · `_BF16_EXACT_OPS` → spec 声明

spec 加字段（**名字待定**）：

```jsonc
"precision": {
  "output_exact_on_grid": true,     // 输出值域 ⊆ 目标 dtype 网格 → bf16/fp16 走 exact_equal
  "_source": "(推断) 由 acc-spec 从任务书算子语义推导"
}
```

- 由 `acc-spec` 从任务书 / 算子语义填，推断项标 `(推断)`。
- **fail-closed**：spec 未声明 → bf16 数值算子**仍然拒跑**（保持现有严格度，不借机放松）。
- 删掉 `_BF16_EXACT_OPS` 白名单。

### S5 · 落点：三文件进用户 CWD

```
<用户 CWD>/
├── .oprunway/ops/<op>/
│   ├── spec.json          ← acc-spec 产
│   ├── golden.py          ← acc-runner-dev / 从仓抠
│   └── runner.cpp         ← acc-runner 产
└── reports/<op>/          ← 跑测产物（已有）
```

- 为什么不放 `reports/`：spec/golden/runner 是**输入**，reports 是**产物**，且 `reports/` 在 gitignore。
- `repo_adapter` 查找顺序：**用户目录**（唯一）。插件内置 fallback **不保留**——保留就等于插件还含算子。
- 现有 4 个算子的 spec/runner 挪出插件 → 顶层 `samples/`，明确标「示例，非插件能力」。

### S6 · 杂项

- `repo_adapter.py:205` 的 `OPRUNWAY_OP` 去掉 `is_close` 默认值，缺失即报错。
- `expect` vs `expected` 命名 drift，确认后统一。

---

## 四、影响面

| 项 | 影响 |
|---|---|
| `gen_cases.py` | 核心改动：`GOLDEN` 表 → 加载器；补 `oracle_source` 等契约字段；删 `_BF16_EXACT_OPS` |
| `repo_adapter.py` | golden 归属上移；`oracle_source` 透传；runner 查找路径；去 op 默认值 |
| `catlass_adapter.py` | `oracle_source` 透传 |
| `validate_acceptance_state.py` | 增 `oracle_source` 一致性校验（S3） |
| spec schema | 增 `precision.output_exact_on_grid`；`doc/oprunway-spec-schema.md` 同步 |
| `acc-spec` / `acc-runner` / `acc-runner-dev` | 落点改用户 CWD；acc-spec 需产 `spec_clause_ref` 锚点 |
| 单测 | 现有测试大量依赖 `GOLDEN["Sign"]` 等 → 改用 fixture golden，不依赖「内置算子」 |
| canon | 本轮发现应走 `bureau:capture` → `compile`；D2 值得追加进 `gate-must-check-the-effective-object.md` 作第三例 |

---

## 五、开放问题（需要拍板）

1. **`golden.py` 是用户侧 Python，动态 import = 执行代码。** 安全边界怎么定？
   （对照：`runner.cpp` 本来就要编译并在 NPU 上执行，性质相同。倾向：可接受，但要在文档里显式说明，不装作没有。）
2. **D3（补齐 7 个契约字段）体量最大**，是否与 D1/D2 拆成两轮？
   `spec_clause_ref` 依赖 acc-spec 在 spec 里保留任务书条款锚点，`pr_change_ref` 依赖 `pr_facts.json`（`fetch_source` 已产）。
3. **`.oprunway/` 这个目录名**是否合适？会不会与「零持久化配置」冲突？
   （倾向：不冲突——它在用户 CWD 下、随项目走，不是 `~/.config`。）
4. **`analytical_ref` 触发人工 CP** 是否会让 agent 流程卡太多次？
5. 本轮是否需要一条 **ADR**（golden 归属与来源分级）走 bureau capture → compile → review？

---

## 六、这份方案没做的事

- **没动一行代码。**
- **没 commit。** 要 commit 需先过 CLAUDE.md #5 的 codex 散文门，并在 `doc/oprunway-changes-brief.md` 追一笔。
- **没做隔离环境测试。** 原计划（隔离 config dir + 隔离 CWD + 藏三份答案 + 真机跑 IsClose）
  **仍然可跑**，不必等 D1——IsClose 恰是内置 4 算子之一，其 golden 在 `GOLDEN` 字典里，
  `gen_cases` 不会崩；藏掉 spec / runner 后能真实测到 `acc-spec` 与 `acc-runner` 两环。

  但要认清它测到了什么、没测到什么：

  | | 测得到 | 测不到 |
  |---|---|---|
  | `acc-spec`（任务书 → spec） | ✓ 藏掉 `specs/isclose.spec.json` 即可 | |
  | `acc-runner`（→ runner.cpp） | ✓ 藏掉 runner 后 `repo_adapter.py:231` 硬失败，逼 agent 生成 | |
  | golden | | ✗ **IsClose 的 golden 内置、白送** |
  | 落点（D5） | ✓ agent 会暴露"生成的 runner 该放哪" | |
  | 泛化能力 | | ✗ **结果不可外推**：第 5 个算子会在 `gen_cases:311` 直接崩 |

  换句话说：**这次测试能验证编排链路 + 两个生成环节，但不能用来声称「插件能验收任意算子」。**
  要验证后者，必须先落 D1。
