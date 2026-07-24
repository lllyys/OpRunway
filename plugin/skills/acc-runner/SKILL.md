---
name: acc-runner
description: OpRunway 验收的代码产出 skill，两件事：(a) gen_golden——据任务书为任意算子产 <ops_root>/<op>/golden.py（真值口径走两档链、PR/仓内参考实现禁作 golden 源、后端生成期定死、check_golden.py 自检），纯 CPU、不需 NPU、不受 runner scope gate 约束；(b) gen_runner/verify_runner——为 ops-<族> 仓、aclnn 两段式接口、opp 安装型的算子（含非 experimental 子树，引擎目录/后缀已生成化），据 spec + PR 事实（算子自带 example + op_def）生成一个「锚定算子实测路径」的 per-op NPU runner（oprunway_<op.lower()>_runner.cpp），并按 runner 自检证据满足/不满足 纪律（未满足则停在 CP-C、不上真机）后才交真机跑测。OpRunway 验收 ③：spec 已就绪、要在真 NPU 上跑一个此类算子的正确性/性能时用。catlass（换构建体系）/非 aclnn 接口/双实现 当前不支持（需先扩 adapter，见 doc/oprunway-batch6b-design.md）。
---

# acc-runner — 产 golden.py 与 per-op NPU runner

> **两条独立通路，别混**：`gen_golden`（CP-B，纯 CPU，手册 `references/golden-authoring.md`）与 `gen_runner`/`verify_runner`（CP-C，真机）。
> 下面「输入/输出/当前范围/步骤」讲的是 **runner 通路**；**golden 通路整套在手册里**，且**不过 runner 的 scope gate**
> （golden 是纯 CPU Python、与算子仓布局无关；把 runner 的 gate 套上去会把一堆本可先产 golden 的算子挡在 CP-B 外）。

**输入**：`<op>.spec.json`（②acc-spec 产）+ `pr_facts.json`（①fetch_source 产，含算子自带 `test_aclnn_*.cpp` + `*_def.cpp`）。
**输出**：**`<ops_root>/<op>/oprunway_<op.lower()>_runner.cpp`** + 构建路径配置
（`ops_root` = `$OPRUNWAY_OPS_DIR`(绝对) 或 `${OPRUNWAY_WORK_DIR:-$CWD}/.oprunway/ops`）。
⚠ **落用户工作目录、不写插件安装目录**（升版即冲；工程约定要求产物落用户 CWD；`ops_root` 落插件目录内会被拒）。
`${OPRUNWAY_PLUGIN_ROOT}/samples/runners/oprunway_*_runner.cpp` 是**只读参考样例 / 生成器骨架种子**（非引擎组件、非运行时回退靶）。`samples/` 随插件分发（在插件内，2026-07-22 由仓根迁入）；`${OPRUNWAY_PLUGIN_ROOT}` = 本插件根中立变量，Claude 下等价 `${CLAUDE_PLUGIN_ROOT}`。
`repo_adapter.find_runner()` **只查用户目录**（`<ops_root>/<op>/`）——**引擎不回退插件样例，fallback 已退役 2026-07-20**：
缺 runner 直接 **fail-closed** 报错，真机 `new_example` 模式 `runner_source` 恒 `user`、非 user 一律 `BLOCKED`。
runner 是引擎的**输出**、非组件；样例只供参照生成（照 §2 四槽拷），绝不作运行时兜底。
**当前范围（诚实）**：代码闭环 = **ops-<族> 仓 · opp 安装型产物 · aclnn 两段式接口**（引擎目录/后缀已生成化、不再硬编码 experimental/math，2026-07-23 批 6b 调研更正；真闸=build.sh 家族命令+opp 布局+aclnn 链接）；catlass/双实现待扩（`doc/oprunway-batch6b-design.md`）。**runner 自检证据满足/不满足 纪律当前非代码强制 sidecar 硬门、待补**（`repo_adapter` 只查文件在不在，不识别 unverified；ref §4）。
**核心纪律（Equal 教训固化）**：aclnn 入口/dtype/参数顺序**从算子自带 example 抠、不猜**；**runner 自检证据不满足则停在 CP-C、不上真机**（靠 agent/人自觉，直到 sidecar 门落地）；acceptance 裁决只逐字引用 validator.py / perf_compare.py / validate_acceptance_state.py 产物（ADR 0007）。
**调用者**：本 skill 由 acc-runner-dev subagent 以 `dispatch_mode=gen_golden`/`gen_runner`/`verify_runner` 调用；单轮 / 禁内部循环 / 不自行判定等纪律以该 agent 为准（指针，不在此复制）。

## 步骤

0. **先判输出形状来源**（C1 · 决定后面所有步骤的形状口径）：该算子的输出形状 = 各输入广播的结果吗？
   - **是（elementwise）** → `golden.py` **不导出** `out_shape`（缺省语义就是「输出同输入形状」），runner 照旧骨架。**现有 4 份样例 golden（IsClose/Equal/Sign/Neg）一律不加此函数**。
   - **否（归约 / 形状由属性公式推）** → `<ops_root>/<op>/golden.py` **必须**导出 `out_shape(in_shapes, attrs)`（**由 `gen_golden` 写**——runner 侧发现形状不对就回 `gen_golden` 改 golden.py，**别在 runner 里另写一份形状推导**，两份必然漂），导出了就以它为准；runner 的输入 buffer 与输出 buffer 要**分开算**。写法、Pdist / Upsample 两个具体例子、**诚实边界**（它是**代码不是数据**，门没法「不执行就校验」它——用户 2026-07-22 明确接受此代价）与骨架改法，全在 `references/runner-skeleton.md` **§6**。
   - **输出形状依赖输入的「值」**（`bincount` 那类：输出长度 = `max(self)+1`，运行期才知道 buffer 多大）
     → ⛔ **BLOCKED、记 gap，不在 C1 覆盖范围内**。理由：`out_shape(in_shapes, attrs)` 只拿得到**形状与属性**、
     **拿不到输入的值**，表达不了这类算子。（与 `agents/acc-runner-dev.md` 的判据一致，别两处打架。）
   ⚠ 引擎侧消费落在 `gen_cases.py`（具名元组 `Golden(fn, source, provenance, out_shape, contract)` 加载 + 逐 case 与 `golden_fn` 实测形状对账 + 写 caseset `expected.out_shape` / `out_shape_source`）与 `repo_adapter.py`（据它造 manifest）——**非本 skill 所属文件、本轮同批落地**，**以引擎实际行为为准**（旧版引擎里导出也不生效），别据此宣称「非 elementwise 已通」。

1. **选构建路径**（确定性）：据 `pr_facts.target_dir` 判 experimental / 正式 / 双实现 / catlass，定 build.sh flags（见 `references/runner-skeleton.md` §3）。**未扩 adapter 前，双实现一律记 gap / 返回 BLOCKED（转 P3），不在本 skill 选择**（与 description「双实现当前不支持」一致）。

2. **生成 runner**（NL，锚定 example）：拷固定 I/O 骨架（从 `oprunway_sign_runner.cpp` 一元 或 `oprunway_equal_runner.cpp` 二元 起），**只填四个槽**（skeleton §2）：
   - **A** aclnn 头：抄 `pr_facts.key_files` 里 `test_aclnn_*.cpp` 的 `#include`（别按 op 名猜——Equal 用的是 `aclnn_eq_tensor.h`）。
   - **B** 输入数 + attr：spec `params`(io=in 计数、attr 按 `attr_order`)。
   - **C** 输出 dtype：verify_mode=exact 且 out=bool→bool(uint8)；numerical→同输入 dtype。
   - **D** aclnn 调用：**照抄 example 里那两行**（`aclnn<Op>GetWorkspaceSize(...)` + `aclnn<Op>(...)`）的参数个数/顺序/attr。
   dtype 只支持 example/spec 里 pipeline 支持的子集（runner 侧 `float32/float16/bfloat16`，**bf16 真机 kernel 须逐算子确认**；int 系仍 Track C）；不支持的入 gap。
   ⚠ **manifest 行格式别照旧样例硬套**：输出形状 ≠ 输入形状时走**扩展行**（`… out_ndim o… in_ndim i…`，第一组仍是输出形状）；
   attr 是 `list[int]` 时编成**逗号连接的单 token**（`[3,4]`→`3,4`）。格式的唯一真相源在 `repo_adapter.run_new_example`——
   写 `ParseLine` 前**实读一次**再动手（skeleton §0 + §6.2）；遇到引擎明确 fail-closed 的形态（空数组/嵌套/dict attr）→ 记 gap、返回 BLOCKED，**不自造编码**。

3. **runner 自检证据满足/不满足**（真机·**当前非代码强制 sidecar 硬门、待补**，skeleton §4）：编出 runner → 造**手算 golden 的小用例** → 喂 **custom exe** 跑 → 检查 rc/`OPRUNWAY_DONE`/out.bin 字节 + 值**逐元素等于手算 golden** 即自检证据满足。不一致 → **custom vs builtin exe 同 case 对照**解耦 root-cause（runner 错 vs 算子错），**别产假裁决**、显式暴露。自检证据不满足 → 停在 CP-C、不上真机、不接 `run_new_example`（靠自觉，直到 sidecar 门落地）；acceptance 裁决只逐字引用 validator.py / perf_compare.py / validate_acceptance_state.py 产物（ADR 0007）。

4. **交付**：自检证据满足 → runner 落 **`<用户 CWD>/.oprunway/ops/<op>/`**（不是插件目录），把构建路径配置（`OPRUNWAY_OPS_REPO/SOC/VENDOR/OP` 等）交 `repo_adapter.run_new_example`（④）跑全量用例 + msprof。

## 约束（跨运行时可移植）
- 全程中文；**runner 一律锚定算子自带 example，不猜**；**runner 自检证据满足/不满足 是必守纪律**（当前非代码强制 sidecar 硬门、待补，见本页开头「当前范围」与 skeleton §4），不可跳过。
- runner 是 C++、真机专属；本 skill 只做「据 example 生成 + 定义验证」，编译/跑测的确定性活在 `run_on_npu.sh` / `repo_adapter`。
- 新算子 dtype/arity 超出当前 runner 支持（如 int8/uint8/double/complex；bf16 属「有分支但真机未逐算子证实」）→ 扩 gap，别硬塞让下游崩。
- **形状通了 ≠ 能验收**：`out_shape` 只解「输出形状」这一道闸；dtype 谱（uint8/double/complex）、输入 rank 锁死、`gen_cases` 无条件产的空 `(0,)`/标量 `(1,)`/`(1,1,1)` 特殊场景条目，仍可能各自把该算子挡住 → 逐条记 gap、该 BLOCKED 就 BLOCKED（skeleton §6.3）。

**详规见** `references/golden-authoring.md`（**`gen_golden` 手册**：两档链决策树 · 文件骨架 · `GOLDEN_CONTRACT` 字段 · `check_golden.py` 退出码判读 · 何时 BLOCKED）与 `references/runner-skeleton.md`（契约 · 固定框架 · 四槽填法 · 构建路径 · 验证门 · 自检 · **§6 非 elementwise 输出形状 / golden.py `out_shape`**）。
