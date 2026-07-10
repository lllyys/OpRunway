---
name: acc-runner
description: 为 ops-math 风格、experimental/math 目录、aclnn 两段式接口的算子，据 spec + PR 事实（算子自带 example + op_def）生成一个「锚定算子实测路径」的 per-op NPU runner（oprunway_<op.lower()>_runner.cpp），并按 runner 自检证据满足/不满足 纪律（未满足则停在 CP-C、不上真机）后才交真机跑测。OpRunway 验收 ③：spec 已就绪、要在真 NPU 上跑一个此类算子的正确性/性能时用。legacy/非math族/双实现/catlass 当前不支持（需先扩 adapter）。
---

# acc-runner — 生成并验证 per-op NPU runner（③）

**输入**：`<op>.spec.json`（②acc-spec 产）+ `pr_facts.json`（①fetch_source 产，含算子自带 `test_aclnn_*.cpp` + `*_def.cpp`）。
**输出**：**`<ops_root>/<op>/oprunway_<op.lower()>_runner.cpp`** + 构建路径配置
（`ops_root` = `$OPRUNWAY_OPS_DIR`(绝对) 或 `${OPRUNWAY_WORK_DIR:-$CWD}/.oprunway/ops`）。
⚠ **落用户工作目录、不写插件安装目录**（升版即冲；工程约定要求产物落用户 CWD；`ops_root` 落插件目录内会被拒）。
插件里的 `acc-common/new_example/oprunway_*_runner.cpp` 是**随插件发行的样例，按只读用、不得改写**。
`repo_adapter.find_runner()` 查找顺序 = **用户目录优先 → 插件样例 fallback**；命中样例（=用户没为本任务提供 runner）时
会在 stderr 告警「跑的不是为你的任务生成的 runner」，真机 `new_example` 的 evidence 记 `runner_source=builtin_sample`，
**裁决被强制降为 `NEEDS_REVIEW`、进人工 CP、绝不出干净 PASS**。
**当前范围（诚实）**：仅 **`experimental/math/<op>` aclnn 算子**代码闭环；余待扩（见 ref §3）。**runner 自检证据满足/不满足 纪律当前非代码强制 sidecar 硬门、待补**（`repo_adapter` 只查文件在不在，不识别 unverified；ref §4）。
**核心纪律（Equal 教训固化）**：aclnn 入口/dtype/参数顺序**从算子自带 example 抠、不猜**；**runner 自检证据不满足则停在 CP-C、不上真机**（靠 agent/人自觉，直到 sidecar 门落地）；acceptance 裁决只逐字引用 validator.py / perf_compare.py / validate_acceptance_state.py 产物（ADR 0007）。
**调用者**：本 skill 由 acc-runner-dev subagent 以 `dispatch_mode=gen_runner`/`verify_runner` 调用；单轮 / 禁内部循环 / 不自行判定等纪律以该 agent 为准（指针，不在此复制）。

## 步骤

1. **选构建路径**（确定性）：据 `pr_facts.target_dir` 判 experimental / 正式 / 双实现 / catlass，定 build.sh flags（见 `references/runner-skeleton.md` §3）。**未扩 adapter 前，双实现一律记 gap / 返回 BLOCKED（转 P3），不在本 skill 选择**（与 description「双实现当前不支持」一致）。

2. **生成 runner**（NL，锚定 example）：拷固定 I/O 骨架（从 `oprunway_sign_runner.cpp` 一元 或 `oprunway_equal_runner.cpp` 二元 起），**只填四个槽**（skeleton §2）：
   - **A** aclnn 头：抄 `pr_facts.key_files` 里 `test_aclnn_*.cpp` 的 `#include`（别按 op 名猜——Equal 用的是 `aclnn_eq_tensor.h`）。
   - **B** 输入数 + attr：spec `params`(io=in 计数、attr 按 `attr_order`)。
   - **C** 输出 dtype：verify_mode=exact 且 out=bool→bool(uint8)；numerical→同输入 dtype。
   - **D** aclnn 调用：**照抄 example 里那两行**（`aclnn<Op>GetWorkspaceSize(...)` + `aclnn<Op>(...)`）的参数个数/顺序/attr。
   dtype 只支持 example/spec 里 pipeline 支持的子集（float32/float16）；不支持的入 gap。

3. **runner 自检证据满足/不满足**（真机·**当前非代码强制 sidecar 硬门、待补**，skeleton §4）：编出 runner → 造**手算 golden 的小用例** → 喂 **custom exe** 跑 → 检查 rc/`OPRUNWAY_DONE`/out.bin 字节 + 值**逐元素等于手算 golden** 即自检证据满足。不一致 → **custom vs builtin exe 同 case 对照**解耦 root-cause（runner 错 vs 算子错），**别产假裁决**、显式暴露。自检证据不满足 → 停在 CP-C、不上真机、不接 `run_new_example`（靠自觉，直到 sidecar 门落地）；acceptance 裁决只逐字引用 validator.py / perf_compare.py / validate_acceptance_state.py 产物（ADR 0007）。

4. **交付**：自检证据满足 → runner 落 **`<用户 CWD>/.oprunway/ops/<op>/`**（不是插件目录），把构建路径配置（`OPRUNWAY_OPS_REPO/SOC/VENDOR/OP` 等）交 `repo_adapter.run_new_example`（④）跑全量用例 + msprof。

## 约束（跨运行时可移植）
- 全程中文；**runner 一律锚定算子自带 example，不猜**；**runner 自检证据满足/不满足 是必守纪律**（当前非代码强制 sidecar 硬门、待补，见本页开头「当前范围」与 skeleton §4），不可跳过。
- runner 是 C++、真机专属；本 skill 只做「据 example 生成 + 定义验证」，编译/跑测的确定性活在 `run_on_npu.sh` / `repo_adapter`。
- 新算子 dtype/arity 超出当前 runner 支持（如 bf16/int8）→ 扩 gap，别硬塞让下游崩。

**详规见** `references/runner-skeleton.md`（契约 · 固定框架 · 四槽填法 · 构建路径 · 验证门 · 自检）。
