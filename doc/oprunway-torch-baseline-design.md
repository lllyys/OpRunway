---
title: OpRunway torch-对标验收场景 · Vendoring 设计与实现蓝图
created: 2026-07-24
status: 架构已获用户批准（Option A）。**分维状态（2026-07-26 性能复核后更新，以本行为准）**——
        **median(PR6429) 端到端验收已跑，最终裁决 = `FAIL(精度)`**（a3 真机 60 例，`fail=6` 全部且仅有
        NaN 输入场景；`gate.passed=true` → 是**成立的精度 FAIL、不是 BLOCKED**；产物 `reports/Median/`，
        报告 `reports/Median/验收报告-median-pr6429.md`）。accuracy 主链 + 编排接线：已在真机跑通并出裁决。
        perf：custom/torch baseline 已按 cannbot 同口径完成 50-case 真机采集；48 例有有效双边 kernel-only
        耗时，2 例因 torch_npu BF16 dim=1 基线报 CANN 161002 而 BLOCKED。移除 trivial 自动免测后重算：
        达标35/50、真实评分48/50、blocked2、status=blocked。正文旧状态与此冲突时以 §9.9 为准。
witness: median（PR6429）
sources: 用户 2026-07-24 指定参考仓 gitcode Justbin/cannbot-ops-input；3 份调研 + 1 轮 Workflow 综合
---

# OpRunway torch-对标模式 · Vendoring 蓝图（可执行）

> 🔴 **全文级 SUPERSEDE 横幅（2026-07-26 更新，覆盖下文所有「当前状态」类表述）**
> **median(PR6429) 的最终验收裁决是 `FAIL(精度)`**——a3 真机 60 例、`fail=6`（全部且仅有 NaN 输入场景）、
> `gate.passed=true`（证据可信完整，是**成立的精度 FAIL**、不是 BLOCKED）。性能维后续已按 cannbot 同口径
> 采到 48/50 个有效双边耗时，2 个 torch BF16 dim=1 基线 BLOCKED；详见 §9.9。
> 逐字产物在 `reports/Median/`（`acceptance.json` / `verdict.json` / `perf_report.json` / `harness_trust_gate.json`），
> 人读报告 `reports/Median/验收报告-median-pr6429.md`。
> 本文 §1–§8 大多写于首跑**之前**，其中「未跑 / 未实现 / 均须真机验证 / 一条 case 都还没跑过」等状态描述
> **已过时**；**凡与本横幅、§9.8、§9.9 或 `reports/Median/` 落盘产物冲突，一律以后者为准**。
> ⚠ 同时守住反向的诚实：**跑过 ≠ 跑通**——精度历史首跑是 FAIL；最新性能有真实数据，但整体仍 blocked，
> 不能把「已采到耗时」读成「性能验收通过」。

> **由来**：用户 2026-07-24 定新规则——「任务书对标 torch」场景下参考 `cannbot-ops-input` 仓的精度/性能 case 生成规则、
> 测试方式方法、torch 封装算子方式，改造 OpRunway 并对 median 任务书 + PR6429 做端到端验收。架构经用户拍板走
> **Option A（adapt/vendor 参考仓成熟代码进 OpRunway，对齐我们的契约/证据链/门）**。本文是该决策的可执行蓝图。
> canon 张力见 §6，须走 `bureau:note→compile→review`；promote 前以 CLAUDE.md + 本蓝图为现行权威。

见证算子：median（PR6429，aclnn 两段式、双输出 values+indices）。落地环境：a3 目标容器（torch_npu 2.10 / CANN 9.0.1 / 单卡 davinci0 / 根盘 8.6G）。

核心判断（贯穿全篇）：**torch-对标不是新建一套并行系统**。判定/证据链/门仍唯一归 OpRunway 确定性脚本链。新增面只有四块真能力——(A) ctypes-aclnn Python runner form（全新，参考仓最有价值的可搬件）、(B) `torch_allclose` 精度 standard、(C) torch golden 冻结、(D) **多输出契约扩展**（median 的 values+indices，是唯一契约级缺口，最重）。参考仓的六轴 case-gen / 三态计数 / pass_rate 分母语义 **不整搬**——映射进 OpRunway 既有 gen_cases/validator 语义。

---

## 1. 新文件布局

放置根：`plugin/acc-common/`。原则：新能力独立子包；判定/契约变更就地 EDIT 既有 SSOT 文件（不另起并行模块）。

```
plugin/acc-common/
├── aclnn_runtime/                    ★新子包：ctypes-aclnn runner form（本蓝图核心）
│   ├── __init__.py                   新写（docstring）
│   ├── base.py                       ← adapt 参考仓 adapters/base.py（原样搬，异常类改名 AclnnRunnerError）
│   ├── aclnn_runner.py               ← adapt 参考仓 adapters/aclnn_runner.py（改：bf16/多输出/ACL常量核9.0.1/soc；见 §4）
│   ├── aclnn_driver.py               新写：在容器内跑的驱动脚本——读 caseset+.bin→逐case调 AclnnRunner→落 out_k.bin（纯 ctypes+numpy，判定不在此）
│   └── acl_consts.py                 新写：ACL 枚举/常量单一真源（dtype/format/memcpy/malloc），带 CANN 9.0.1 核验 provenance
├── aclnn_adapter.py                  新写：run_aclnn_py(caseset, work) + find_aclnn_project(op) + ACLNN_MODES 注册；deploy/build/exec/collect 编排（比照 catlass_adapter.py 作为新 mode 并入 repo_adapter.MODES）
├── torch_ref_freeze.py               ← adapt 参考仓 common/reference_resolver.py（可选·中优先：签名冻结+binding_sha256+版本漂移 fail-closed）
├── dataset_verify.py                 ← adapt 参考仓 dataset_loader.py（可选·低优先：sha256/size/路径逃逸/schema 物理完整性自检，纯 stdlib）
│
│  ── 以下就地 EDIT（不新增文件）──
├── precision_policy.py               EDIT：+TORCH_ALLCLOSE standard（容差表 adapt 自参考仓 accuracy.py:47-54）
├── validator.py                      EDIT：+judge_torch_allclose、+多输出逐输出判定
├── gen_cases.py                      EDIT：+compare=torch_allclose、+value_profile(nan/tie/special)、+多输出 golden 落盘
├── repo_adapter.py                   EDIT：+MODES["aclnn_py"]、+多输出 readback、+torch_npu baseline 解析
├── run_workflow.py                   EDIT：登记 aclnn_py 为 acceptance-capable
└── new_example/run_on_npu.sh         EDIT（perf 里程碑）：+torch_npu 基线支路
```

| 新文件 | 来源 | 搬法 | 说明 |
|---|---|---|---|
| `aclnn_runtime/base.py` | 参考仓 `adapters/base.py` | **原样搬** | ~15 行异常基类；改名 `AdapterError→AclnnRunnerError` |
| `aclnn_runtime/aclnn_runner.py` | 参考仓 `adapters/aclnn_runner.py` | **改** | 五处改：bf16、多输出、ACL常量核 9.0.1、soc 探测、opp 隔离（§4 详） |
| `aclnn_runtime/acl_consts.py` | 参考仓 `aclnn_runner.py:27-36` 抽出 | **改+新写** | 把 `_ACL_DTYPES/_ACL_FORMAT_ND/_MEMCPY_*/_MALLOC_*/_REPEAT_INITIALIZE` 提成单一真源，逐条对 9.0.1 `acl/acl_base.h` 核 |
| `aclnn_runtime/aclnn_driver.py` | 参考仓 `case_call.materialize_call`（设计）+ `adapters/registry.py:65-75`（invoke） | **新写（借设计）** | 容器内执行；只产 out.bin，**不判定** |
| `aclnn_adapter.py` | 参考仓 `registry.py` + OpRunway `catlass_adapter.py`（并入范式） | **新写** | 新 adapter mode；比照 `CATLASS_MODES` 并入 |
| `torch_ref_freeze.py` | 参考仓 `common/reference_resolver.py` | **改（改包名/异常）** | 可选；torch 惰性 import，纯逻辑 |
| `dataset_verify.py` | 参考仓 `dataset_loader.py` | **改（改包名）** | 可选；纯 stdlib，零耦合 |

**不搬**：参考仓 `design_contract.py` / `case_generator.py` / `performance_selection.py`（六轴校验+数值生成+perf选例）——其能力由 OpRunway `gen_cases.py` 承载，只把其中 `generate_array` 的 special_values（nan/inf 别名）机制**借设计**到 gen_cases 的 value_profile（§3.2）；`accuracy.py` 三态计数/pass_rate 分母**不搬**（用 OpRunway validator 语义）；`arg_binding.py` 遗留路径**不搬**；`adapters/{model_new,torch_custom_op}.py` 和 perf 全家（torch.ops.npu 通路）**不搬**（median 走 registry/ctypes，非 torch.ops）。

---

## 2. 数据契约变更（给出具体 JSON）

### 2.1 spec（`spec_schema_template.jsonc`）

新增/改动字段（**按场景裁靠数据、不靠 agent 特判**）：

```jsonc
{
  "op": "Median",
  "repo": "...",
  "hardware": "...",                        // a3=ascend910b/ascend910_93 双源交叉
  "scenario": "torch_ref_aclnn",            // ★新增·场景标识（枚举）：编排层据此不做if算子名分派，只据字段
  "runner_form": "aclnn_py",                // ★新增·runner 形态：cpp | aclnn_py（默认 cpp；缺省=cpp）
  "params": [
    {"name":"self","io":"in","dtype":["float32","float16","bfloat16","int32"],"rank":[1,4]},
    {"name":"dim","io":"attr","dtype":["int64"]},
    {"name":"keepdim","io":"attr","dtype":["bool"],"default":false},
    {"name":"values","io":"out","dtype":["<from_input>"],"out_role":"value"},     // ★out_role
    {"name":"indices","io":"out","dtype":["int64"],"out_role":"index","index_of":"values"} // ★out_role+index_of
  ],
  "verify_mode": "numerical",
  "precision": {
    "oracle": "torch",                       // ★现被 select_standard raise，需放行→torch_allclose
    "standard": "torch_allclose",            // ★新 standard（显式优先，推荐走这条）
    "tolerance_source": "dtype_table",       // ★rtol/atol 权威来源：dtype_table | taskdoc | torch_default
    "case_target": 16
  },
  "perf": {
    "baseline": "torch_npu",                 // ★perf.baseline 枚举 +torch_npu（perf_compare 源无关，纯标签）
    "target_ratio": 0.6
  },
  "golden": {
    "source": "torch",
    "method_kind": "oracle_method",          // torch 被任务书指定为真值口径→tier1（非 impl_reference）
    "authorization": {"kind": "oracle_method"},
    "taskdoc_snapshot": {"sha256": "..."}
  }
}
```

⚠ **上面这块是首程草案，有三处后来被推翻，别照抄**（权威 schema 看 `plugin/acc-common/spec_schema_template.jsonc`
+ `skills/acc-spec/references/taskdoc-to-spec.md`）：
① `indices` 的 `dtype:["int64"]` **是错的口径**——index 输出的 dtype **必须照算子 header/op_def 实际声明填**
（PR6429 的 op_def 固定 `DT_INT32`；写 int64 会把 `indices_dtype_mismatch` 这条落差掩掉）；且 index 输出还**必填 `gather_from`**（指某 in 参数），这里漏了。
② `perf.target_ratio: 0.6` **是错的**——0.6 是参考仓 cannbot-ops-input 的自家默认，median 任务书写的是「不劣化」→ **1.0**；照 0.6 会把「比基线慢 40%」判成达标。
③ `runner_form=="aclnn_py"` **必须**同时给 `call_variants`（这里没写），否则 `gen_cases` 当场 fail-closed。

`scenario` 触发判据（编排层零 if-算子名）：`scenario=="torch_ref_aclnn"` ⟺ 任务书声明对标 torch + PR 是 aclnn 两段式
（**仓根** `build.sh` + 算子目录有 `op_host/` + 在 `<op_subdir>` 下**有界递归**能找到 `aclnn_*.h`；⚠ 原文的
「op工程有 build.sh+op_api+op_graph」**已被 §9.4 实测推翻**——无 per-op build.sh、无 `op_graph/`、接口头层级不预设）。
由 `acc-spec-extractor` 从任务书×op_def 抽出。

### 2.2 caseset.json（gen_cases 产出）

`expected` 由单输出扩为多输出（**契约级扩展**）：

```jsonc
{
  "id": "c07",
  "dims": ["功能","dim分派","tie"],
  "inputs": [{"name":"self","shape":[4,6],"dtype":"float32","path":"...","storage_dtype":"float32"}],
  "attrs": {"dim":1,"keepdim":false},
  "expected": {
    "outputs": [                              // ★新增·多输出数组（单输出算子=长度1，向后兼容）
      {"role":"value","golden_path":".../golden_0.npy","golden_tier":1,
       "out_shape":[4],"out_shape_source":"reduce","compare":"torch_allclose","compare_dtype":"float32",
       "standard":"torch_allclose","policy":{"kind":"torch_allclose","rtol":1.22e-4,"atol":1e-3},
       "tolerance_policy_id":"torch_allclose:float32","threshold":null},
      {"role":"index","index_of":0,"golden_path":".../golden_1.npy","golden_tier":1,
       "out_shape":[4],"out_shape_source":"reduce","compare":"index_value_consistency","compare_dtype":"int64",
       "standard":"torch_allclose","policy":{"kind":"index_value_consistency","gather_from":"self","value_rtol":1.22e-4,"value_atol":1e-3}}
    ],
    "verify_mode":"numerical","golden_source":"torch ...","case_origin":"...","rule_ref":"..."
  }
}
```

关键：**index 输出不逐位比下标**（NPU 与 golden 在 tie 上可合法给不同 index），判据是 `gather(self, idx_npu) allclose gather(self, idx_golden)`（`compare:"index_value_consistency"`, `gather_from` 指向输入名）。单输出算子 `outputs` 长度=1、无 index role → 完全向后兼容旧 caseset（旧 `expected` 单值结构由 gen_cases 在 `len(out_params)==1` 时保留或统一升级为长度1数组，二选一，建议统一升级+validator 双读兼容）。

### 2.3 evidence.json（repo_adapter 产出）

```jsonc
{
  "op":"Median","repo_mode":"aclnn_py","evidence_grade":"acceptance_candidate",
  "runner_source":"user","runner_path":".../<op工程>/build.sh","runner_form":"aclnn_py",
  "evidence":[{
    "case_id":"c07","status":"ok",
    "precision":{                             // ★per-output metrics 数组
      "outputs":[
        {"role":"value","standard":"torch_allclose","tolerance_policy_id":"torch_allclose:float32",
         "policy":{...},"metrics":{"mismatch":0,"numel":4,"max_abs_err":1e-6,"max_rel_err":2e-7},
         "golden_path":"...","out_path":".../out_0.bin",
         "provenance":{"golden_sha256":"...","out_sha256":"...","numel":4}},
        {"role":"index","index_of":0,"policy":{"kind":"index_value_consistency",...},
         "metrics":{"mismatch":0,"numel":4,"gathered_max_abs_err":0.0},
         "out_path":".../out_1.bin","provenance":{...}}
      ],
      "oracle_source":"torch_ref","not_settled":false
    },
    "perf":{"scope":"kernel_only","us":12.3,"note":""}
  }]
}
```

### 2.4 verdict.json（validator 产出）

**结构不改**——per-output judge 结果 AND 折叠进现有三层字段：`per_case[].精度` = 全 output 皆 pass 才 pass；任一 output fail→fail、uncertain→needs_review。`判据` 字段追加 per-output 摘要串。`overall.verdict` 词表不变。

### 2.5 perf_report.json

`per_case[].baseline.source` = `"torch_npu"`（纯标签）；`baseline_source` 顶层 = `"torch_npu"`。perf_compare **判定逻辑零改**（源无关，只读 us+scope）。

### 2.6 acceptance.json

**不改**（除非新增 canonical state，不建议）。新增 mode 经 `_STATE_MAP` 现有映射即可。

---

## 3. 六组件落地设计

### 组件① golden 多输出（gen_cases.py）

- **golden.py 模板**（新写 `samples/golden/Median/golden.py`，照抄 `Im2col/golden.py` 结构）：`golden_fn(inputs, attrs)` 里 `import torch`（延迟），`r = torch.median(x, dim=attrs["dim"], keepdim=attrs["keepdim"])` 返回 `(values.numpy(), indices.numpy())`；全局分派 `torch.median(x)` 返回单元素。`GOLDEN_CONTRACT.authorization.kind="oracle_method"`（→tier1）。
- **gen_cases.py:1187/1264/1293**：`golden = golden_fn(...)` 若返回 tuple → 逐输出 `np.save(golden_{k}.npy)`，`expected.outputs[k].golden_path` 逐一填。out_shape 逐输出推（value/index 同形，reduce 掉 dim；keepdim 保留）。
- **gen_cases.py:1281-1291**：`compare` 决策链——value 输出 fp→`torch_allclose`；index 输出→`index_value_consistency`（据 `out_role=="index"`）。随后 `effective_standard`/`threshold_for` 自动带出 policy。

### 组件② case-gen 六轴 + 见证数据（gen_cases.py）

- **属性等价类**：median 的 `dim` first/middle/last 用 OpRunway 现有 `attr_matrix`（gen_cases.py:676）表达——按 rank 解析 dim（借参考仓 `values_by_rank` 设计：first=0；middle=rank//2；last=rank-1）。`keepdim` bool 两值。全笛卡尔 3×2 由 attr_matrix 表达。
- **value_profile（新增·借参考仓 case_generator.generate_array 设计）**：gen_cases 当前随机生成，需新增受控数值——`special_values`（nan/±inf，别名映射 `{"nan":np.nan,...}` + `np.resize` 循环填充）、`tie`（构造重复值使 median 命中并列，如 `[3,1,3,1,2,3]`）。加一个 `value_profile` 维（semantic/nan/tie/uniform），由 spec case 计划驱动。**这是搬进 gen_cases 的唯一数值生成机制**（不搬整个 case_generator，只借 generate_array 的 special_values/tie 分支逻辑，op-中立）。
- **奇偶长度 / dim=1 退化**：shape 轴现有机制表达（reduced 轴长度取 3/4 覆盖奇偶；rank=1 覆盖退化）。

### 组件③ ctypes-aclnn runner（`aclnn_runtime/`，见 §4 详）

### 组件④ torch_allclose 判据 + 多输出比对（precision_policy.py + validator.py）

改动点（报告4 附表锚点，逐一）：
1. `precision_policy.py:71-75`：`TORCH_ALLCLOSE = "torch_allclose"` 加入 `STANDARDS`。
2. `select_standard`（:135-147）：`precision.standard=="torch_allclose"` 走显式优先放行（:125-128 已通用）；`oracle:"torch"` 加一行 `if oracle=="torch": return TORCH_ALLCLOSE`（否则命中 :142 raise）。
3. `threshold_for`（:237-258）：`if standard==TORCH_ALLCLOSE:` 返回 `{"kind":TORCH_ALLCLOSE,"rtol":..,"atol":..,"equal_nan":True}`。**rtol/atol 来源按 `tolerance_source`**：`dtype_table`→adapt 参考仓 `accuracy.py:47-54` 六行表（fp16 atol9e-2/rtol2^-10、bf16 1e-1/2^-7、fp32 1e-3/2^-13、fp64 1e-6/2^-30、complex 同 fp32/fp64）；`taskdoc`→从 spec 派生；`torch_default`→1e-5/1e-8。**带 provenance**（抄自参考仓 accuracy.py→一手 tilelang2ascend verification_ascendc.py）。
4. `threshold_digest`（:549-560）：加 `if kind==TORCH_ALLCLOSE: return (policy["rtol"],policy["atol"])`（否则 :560 raise，三处一致校验失效）。
5. `compute_metrics`（:576/669-676）：加 `if kind==TORCH_ALLCLOSE:` 分支——`mismatch = count(|o-g| > atol+rtol*|g|)`（`equal_nan=True`），返回 `{"mismatch","numel","max_abs_err","max_rel_err"}`（numpy 惰性 import）。加 `if kind=="index_value_consistency":` 分支——需 `self`（从 case input 重读）+ `gather(self, idx_actual)` vs `gather(self, idx_golden)` 做 allclose，`mismatch=count(gathered超差)`。**受 :623-627 双侧 dtype 严校约束**：bf16 不在 SUPPORTED_COMPUTE_DTYPES → 走 storage_dtype=fp32 比对（golden 也 fp32 存储），与现状一致。
6. `validator.py:114-116 _JUDGES`：`TORCH_ALLCLOSE: judge_torch_allclose`。
7. `validator.py` 新增 `judge_torch_allclose(policy, metrics)`（仿 :78 judge_mere_mare，**纯 stdlib**）：读 `mismatch`（`_is_nonneg_int`）+`numel`（`_is_pos_int`），判 `mismatch==0`（torch.allclose 容错率=0，一元素超差即 fail）。返回 `("pass"/"fail", why)`。index 路径同一 judge（mismatch 语义统一）。
8. **多输出 judge 折叠**：`validator.py:150-196 _precision_contract` + per_case 组装——遍历 `evidence.precision.outputs[]` 逐输出 judge，AND 折叠。三处 digest（spec-canonical/caseset/evidence）逐输出对齐。

### 组件⑤ perf msprof 基线（run_on_npu.sh + repo_adapter + perf_compare）

> ⚠ **状态已变（2026-07-24，覆盖本小节末尾的「perf 列为第二里程碑」）**：perf 采集端**代码已接通**
> （`aclnn_runtime/perf_msprof.py` + `repo_adapter.parse_torch_npu_baseline` 真消费口 + 基线行为五分类
> + 精度先筛 + 双边 scope 校验，实测修正见 §9.7）。**但真机仍 BLOCKED、一个耗时数都没产出过**
> （首跑：精度 fail-fast；且即便不 fail-fast，profiler db 的 `TASK.taskType` 是数值枚举 id、未解回名字 → 双边 fail-closed）。
> **口径：有有效基线且双边 scope 同为 `kernel_only` 才出性能裁决；否则 BLOCKED，绝不冒充达标。**

- **首程 perf 走 torch_npu 基线**（median 的 torch-对标语义）：基线 = `torch_npu` 上 `torch.median` 的 kernel-only 耗时；custom = ctypes-aclnn 的 median kernel 耗时。
- perf_compare **零改**（源无关，只读 us+scope+ratio）。
- 采集端改造（§5 详）：aclnn runner 的 perf 需 python-ctypes 的 kernel-only msprof 采集。**首程可复用参考仓 torch-based msprof wrapper**（容器有 torch_npu 2.10）跑基线；custom 侧 ctypes 用 `msprof --task-time` 圈 range。**perf 列为第二里程碑**，accuracy 通路优先绿。

### 组件⑥ 编排路由（SKILL.md / op-acceptance.md）

**几乎零 if-场景分支**（律令#0 想要的效果）：
- CP-A/B：场景全编码进 spec（`scenario`/`runner_form`/`precision.standard`/`perf.baseline`），由 acc-spec-extractor 从任务书抽，非编排层判算子名。
- CP-C runner dispatch：`acc-runner-dev` scope gate 放行 `runner_form=="aclnn_py"`——此形态**无 per-op runner 源**（op工程即 DUT，runner 通用），gate 只需校 op工程有 build.sh+op_api+op_graph。
- CP-D dispatch：`run_workflow.py --mode aclnn_py`（新 mode），须被 `_acceptance_capable` 认可。
- CP-D §6 基线路由文本补一条：`scenario==torch_ref_aclnn → torch_npu 基线`（真机内基线，非 GPU 外部数据，无 blocked 路由）。

---

## 4. ctypes-aclnn runner 落地细节（核心）

> ⚠ **本节 §4.1 的调起链路、§4.2 的「`<ops_root>/<op>/` 含 build.sh+op_api+op_graph」定位与 vendor 路径写法均已被 §9.4/§9.6 实测推翻、不可照抄执行**：
> 真实形态是 **ops 仓根 `build.sh --ops=<op>`** + `<op_subdir>/op_host/` + `<op_subdir>/op_host/op_api/aclnn_*.h`（**无** per-op build.sh、**无** op_graph；⚠ 接口头在 `op_host/op_api/`，`<op_subdir>/` 下**没有** `op_api/`——故 gate 一律**在 `<op_subdir>` 下有界递归**找、不钉死层级）；
> custom vendor 实际落 `<install-path>/vendors/<vendor_name>**_nn**/op_api/lib/libcust_opapi.so`（`_nn` 后缀由 install 自动追加），
> 运行时须同时设 `ASCEND_CUSTOM_OPP_PATH` 与 `LD_LIBRARY_PATH`（权威见 install 生成的 `vendors/<v>_nn/bin/set_env.bash`）。**执行一律以 §9.6 为准。**

### 4.1 调起链路（作为新 runner form）

```
run_workflow.py --mode aclnn_py
  └─ repo_adapter.MODES["aclnn_py"] = aclnn_adapter.run_aclnn_py(caseset, work)
       ├─ find_aclnn_project(op) → <ops_root>/<op>/（含 build.sh + op_api/aclnn_*.h + op_graph/）
       │     · 复用 repo_adapter.py:434 _reject_symlink_segments 软链守卫 + _check_id 防注入
       │     · 缺 build.sh/op_api/op_graph → fail-closed（不回退）
       ├─ build 阶段（可选/幂等）：容器内 build.sh --soc=<soc> → install 到 ASCEND_CUSTOM_OPP_PATH（用户目录，不污染共享 opp）
       ├─ deploy：caseset + input .bin + aclnn_runtime/{aclnn_runner,acl_consts,base,aclnn_driver}.py → scp/cp 进容器工作目录
       ├─ exec：容器内 `python aclnn_driver.py <caseset> <out_dir>`
       │     · driver 逐 case：读 storage_dtype .bin → AclnnRunner.run(op_name, inputs, out_shapes[], out_dtypes[]) → 落 out_0.bin/out_1.bin
       │     · driver 只产原始输出，**不判定**
       └─ collect：拉回 out_k.bin → repo_adapter 组装 evidence（compute_metrics 在 OpRunway 侧判，判定唯一归确定性脚本链）
```

**关键泛化点**：aclnn runner form **无 per-op runner 代码**（与 C++ 的 `oprunway_<op>_runner.cpp` 不同）。op工程（PR checkout）是数据/DUT，`aclnn_runner.py` 完全 op-中立（从 header 正则拿 op 名，从 op_def/header 推 arity）。这比 C++ 模型更泛化——换任意域内 aclnn 算子零改 runner。

### 4.2 定位 build 好的 median .so / opp / aclnn 头

- **op 名**：`parse_aclnn_op(<op工程>/op_api)` → glob `aclnn_*.h` 剔 `*_impl.h` → 正则 `aclnn(\w+)GetWorkspaceSize\(` → `Median`。符号 `aclnnMedianGetWorkspaceSize` / `aclnnMedian`。
- **.so 加载**（`_ensure_init`）：`$ASCEND_TOOLKIT_HOME/lib64/{libascendcl,libnnopbase,libopapi}.so` + `$ASCEND_OPP_PATH/vendors/*/op_api/lib/libcust_opapi.so`（build install 产物），全 `RTLD_GLOBAL|RTLD_NOW` → `CDLL(None)` 取全局符号。
- **env**：`ASCEND_TOOLKIT_HOME`/`ASCEND_OPP_PATH` 由容器内 `set_env.sh` 设；install 时 `ASCEND_CUSTOM_OPP_PATH` 指用户目录，且 `_find_custom_opapi_libs` 的 `$ASCEND_OPP_PATH` 要含该目录（install 目标=查找目标）。

### 4.3 多输出（median values+indices）——必须扩 runner

参考仓 `run()` 硬假设单输出（`t_out` 单个，`gws.argtypes` = `len(inputs)+1`）。改造：

```python
def run(self, op_name, inputs, *, output_shapes: list, output_dtypes: list) -> list[np.ndarray]:
    # output_shapes/output_dtypes 从 op_def/header 推的真实输出 arity（非按算子名）
    in_tensors = [make_tensor(host=arr) for arr in inputs]
    out_tensors, out_hosts, out_devs = [], [], []
    for shp, dt in zip(output_shapes, output_dtypes):
        h = alloc_host(shp, dt); out_tensors.append(make_tensor(host=None, shape=shp, dtype=dt))
        out_hosts.append(h); out_devs.append(...)
    tensors = in_tensors + out_tensors            # 真实 arity
    gws.argtypes = [vp]*len(tensors) + [vp, vp]   # N tensor + &wsSize + &executor
    gws(*tensors, byref(ws), byref(exe))
    run_fn(ws_ptr, ws.value, exe, stream)
    sync
    for h, dev in zip(out_hosts, out_devs): D2H(h, dev)   # 逐输出拷回
    return out_hosts
```

arity 来源：header 签名 `aclnnMedian(self, dim, keepdim, valuesOut, indicesOut, ...)` 数 `aclTensor*` 输入/输出，或 op_def 的 output 声明（**通用推 arity，绝不按算子名**）。median tensor 参数序须与 aclnn 签名精确对齐——输入/输出 aclTensor 的顺序从 header 解析（`parse_aclnn_op` 扩为返回 in/out 各自个数+顺序）。

### 4.4 任意 dtype（bf16 + int）

- **int**（int32/int64/int8/uint8）：`_ACL_DTYPES` 已覆盖（int64=9/int32=3/int8=2/uint8=4）。median 的 indices 输出 int64=9 ✓。
- **bf16（半缺口，必改）**：`_ACL_DTYPES` 加 `"bfloat16":27`（ACL 枚举，**须对 9.0.1 `acl_base.h` 核实**）。numpy 无 bf16→输入侧 storage_dtype=fp32 读盘、device tensor 用真正窄化（不能 memcpy fp32 字节当 bf16——需 fp32→bf16 位截断，或用 ml_dtypes/torch 做窄化后再 memcpy）。首程见证集**至少 1 个 bf16 case** 压这条轴。输出 bf16 host buffer 按 2 字节开、D2H 后按 bf16 解释再转 fp32 落盘。
- `acl_consts.py` 集中所有枚举，逐条核 9.0.1：`_ACL_FORMAT_ND=2`、`_MEMCPY_H2D=1/D2H=2`、`_MALLOC_HUGE_FIRST=0`、`_REPEAT_INITIALIZE=100002`（稳定 ABI，通常不变，但核）。

### 4.5 在 a3 目标容器跑

- **accuracy 通路（ctypes）不依赖 torch**：只要 CANN 9.0.1 提供四个 .so + set_env 设两 env + median 已 build install 出 libcust_opapi.so → 纯 ctypes+numpy 可跑。容器有 numpy ✓。
- **device**：单卡 davinci0 → `AclnnRunner(device=0)`（`aclrtSetDevice(0)`）。
- **ctypes 可行性**：容器内 python 能 `CDLL` CANN .so（torch_npu 2.10 本身就 dlopen 这些库）→ 低风险。唯一硬风险=ACL 枚举漂移（§4.4 核）。
- **argtypes 必声明**：每个指针型参数声明 argtypes（否则 ctypes 默认 c_int 截断 64-bit 指针）——照搬参考仓 :112-130。

---

## 5. DUT 构建契约（PR6429 Median）

> ⚠ **本节 §5.1/§5.2 的「三签名 registry + per-op build.sh」与 §5.3 的 D1 方案均已作废、不可照抄执行**——
> 实测坐实：PR6429 **无** per-op `build.sh`、**无** `op_graph`，build 走**仓根** `build.sh --pkg --experimental --ops=median`（§9.6 有完整可复现配方）；
> **D1 stand-in 必须绑内置 `aclnnMedianDim`、绝不能绑内置 `aclnnMedian`**（后者极可能是全局单输出变体，按 dim 签名调会崩/UB，§9.3）。**执行一律以 §9.3/§9.4/§9.6 为准。**

### 5.1 build（registry archetype）

- op工程签名：`build.sh` + `op_api/` + `op_graph/`（三签名全命中 → registry/aclnn）。
- **build 入口**：容器内 `bash <op工程>/build.sh --soc=<soc>`。**soc 双源探测**：任务书 `适配硬件` × op_def `AddConfig`——a3=`ascend910b`（+`ascend910_93`），**不吃默认 ascend910b**（registry.py:24 默认须被 spec.hardware 覆盖）。
- **产物**：build.sh compile→install，把 `custom_opp_*.run` 装进 `$ASCEND_CUSTOM_OPP_PATH/vendors/<vendor>/`，暴露 `op_api/lib/libcust_opapi.so`；`aclnn_median.h` 留在 op工程 `op_api/`。
- **副作用隔离**：`ASCEND_CUSTOM_OPP_PATH` 指 `<远端工作根>/<work>/opp`（用户目录），**绝不写共享 CANN 的 opp/vendors/**（CLAUDE.md a3 共享机告警）。`is_built()` 只判 `build/` 存在（改源须显式重建）。

### 5.2 runner 找 DUT

~~`find_aclnn_project(op)` → `<ops_root>/<op>/`（含 build.sh+op_api+op_graph）；`AclnnRunner` 从 `$ASCEND_OPP_PATH/vendors/*/op_api/lib/libcust_opapi.so` glob 到 median 符号；op 名从 `op_api/aclnn_median.h` 正则拿。~~
⚠ **SUPERSEDED（§9.4/§9.6 实测）**：实际是 **仓根 `build.sh --ops=<op>`**（无 per-op build.sh、无 `op_graph/`），
接口头**在 `<op_subdir>` 下有界递归**找（PR6429 真实落点 `<op_subdir>/op_host/op_api/aclnn_median.h`，`<op_subdir>/` 下**没有** `op_api/`），
**绝不钉死某一层**；custom lib 的发现走 **`ASCEND_CUSTOM_OPP_PATH`**（**别覆盖 `ASCEND_OPP_PATH`**，见 §9.7 末的 env 订正）。

### 5.3 De-risk 顺序（先 stand-in 验通路，再上 PR 真算子）

1. **D0·纯 ctypes 冒烟（无 median）**：容器内跑一个已知内置 aclnn 算子（如 CANN 自带 `aclnnAdd`，在 libopapi.so 全局符号里）→ 验 `_ensure_init`/`_make_tensor`/两段调用/H2D-D2H/ACL 枚举 9.0.1 全对。**单输出、fp32**。这一步 0 依赖 PR、0 build。
2. ~~**D1·内置 aclnnMedian 作 stand-in**~~ ⚠ **已作废（§9.3）**：内置 `aclnnMedian` 极可能是**全局单输出**变体、签名不同，按 dim 签名调会崩/UB。**改绑内置 `aclnnMedianDim`**（1-in/2-out dim arity，与 PR6429 一致）→ ctypes 调它验**多输出通路**（values+indices）+ index_value_consistency 判据 + bf16 窄化。仍 0 依赖 PR checkout。
3. **D1'·torch_npu median 作 golden/基线 stand-in**：`torch.median` on NPU 产参考（走 torch.ops，非 ctypes）→ 只验 golden 冻结 + torch_allclose 判据 + perf 基线，不验 ctypes runner。
4. **D2·PR6429 build**：checkout PR → `build.sh --soc=ascend910b` install → ctypes 调 custom `aclnnMedian` → 端到端。
5. **D3·bounded 见证集全绿**（§8 case 表）→ 诚实覆盖账本 → 再 scale 全 6 轴。

**FAIL 先解耦 root-cause 再归因**：build 失败 / opp install / ctypes 符号缺失 / kernel 崩 / 精度 mismatch / index tie 分歧——分层判，不混归。

---

## 6. Generalization 边界 + canon 张力清单

**律令#0 合规确认**（逐制品）：
- `torch_allclose` standard = precision_policy 受控词表加一个**能力**，通用 `threshold_for/compute_metrics/judge` 处理，任何声明该 standard 的算子零改即用 ✓。
- `torch_npu` 基线 = `perf.baseline` 一个**源枚举**，perf_compare 源无关 ✓。
- ctypes-aclnn runner = 一个**新 harness form / adapter mode**（按能力/仓/框架扩，同 catlass `generated_harness` 先例），注册进 MODES，**无 per-op runner 代码**、无 `if op=="Median"` ✓。
- 多输出 = **通用多输出契约扩展**（据 `out_role` 字段分派），非给某算子塞第二 golden ✓。
- **红线**：绝不出现按算子名分支；index 判据据 `out_role=="index"` 字段（数据），非 median 特判。

**canon 页张力（走 bureau:note→compile→review，promote 前以 CLAUDE.md/本蓝图为现行权威）**：

| canon 页 | 张力 | 处置 |
|---|---|---|
| `precision-standard-from-taskdoc` | 加第 4 个 standard `torch_allclose` + 放行 `oracle:"torch"`（现刻意 raise 堵 class C 静默降级） | 记账：**必须说明 rtol/atol 权威来源**（dtype_table 抄自 tilelang2ascend / taskdoc / torch_default），否则重蹈"值被同步放宽"洞。带 provenance 打 proposed |
| `runner-is-output` | ctypes-aclnn 是**新 runner form**，且**无 per-op 源**（op工程即 DUT） | 记为**能力扩展**（find_runner 从 `.cpp` 硬编码扩到支持 aclnn_py form）；非回退兜底（fallback 已退役，不复活） |
| `perf-baseline-by-reference-source`（proposed·未 settle） | 加 `torch_npu` 基线源 | 补一类"torch-对标类→torch_npu"，与 tbe/gpu/同op 并列，记 provenance |
| `real-npu-runner-fp32-fp16`（现 `_NP` 仅 fp32/fp16/bf16→fp32；int 仍 Track C） | ctypes runner 原生支持 int（int64 indices 必需）+ bf16 真窄化 | 记为 runner v2 能力扩展；`_NP` 白名单据 runner_form 分派（aclnn_py form 放开 int/bf16） |
| **新页 `multi-output-io-contract`**（须新立） | 现 caseset/evidence/runner 单输出契约是**未成文隐含约定**（散在 gen_cases:1264/repo_adapter:867/validator:183/precision_policy:183） | **扩多输出前先立一页**，否则改一层漏一层。定义 `expected.outputs[]` / `out_role` / `index_value_consistency` 语义 |
| **新页 `aclnn-runner-form`**（须新立） | ctypes-aclnn form 的 ACL ABI 假设（format=ND、枚举核版本、opp 隔离、soc 探测、无 per-op 源） | 立页记"域内假设"（无 opaque descriptor、标准两段式、format ND） |
| ADR 0005/0007（三层口径/裁决只从 validator） | torch_allclose judge 落进现有三层字段，不新增裁决出口 | **无张力·保持** |
| ADR 0011（golden 冻结） | torch golden.py `authorization.kind=oracle_method`→tier1 | 现有机制承载·无张力 |

~~**未上真机提醒**（承 golden-branch-handoff）：全部为静态集成点+设计，torch_allclose NPU 实测、ctypes-aclnn 在 9.0.1 运行时能否跑通、多输出 arity 解析、bf16 窄化、torch_npu perf kernel-only 口径——**均须 a3 目标容器真机验证，covered≠真机绿**。~~
⚠ **SUPERSEDED（2026-07-24 真机首跑）**：上面这条**已过时**。实际结果——torch_allclose 判据 / ctypes-aclnn 在 9.0.1 / 多输出 arity / bf16 窄化**都已在 a3 真机跑过**，端到端出了裁决 **`FAIL(精度)`**（60 例 fail=6，见文首横幅与 §9.8）；**唯独 `torch_npu` perf kernel-only 口径仍未验证**——性能维一个耗时数都没产出（BLOCKED）。**别再据本条说「一切都还没上真机」，也别反过来说「都验过了」。**

---

## 7. 实现任务分解（fanout 用）

> ⚠ **本节是 2026-07-24 的施工清单、已执行完毕，属历史记录**。其中涉及 DUT 定位/构建的描述（尤其 WI-C1）沿用旧「三签名」口径，**已被 §9.4/§9.6 推翻**；勿从本节摘取可执行判据。

依赖标注：`→` 依赖谁。可并行组同字母前缀。验收判据=单测/契约自检（无真机的项）。

### A 组·判据内核（纯确定性脚本、可离线单测、无真机）—— 全部可并行

- **WI-A1 · torch_allclose standard 接入**
  改：precision_policy.py:71-75/135-147/237-258/549-560/669-676。
  验收：单测 `select_standard("torch","torch_allclose")→TORCH_ALLCLOSE`；`threshold_for` fp16/fp32/bf16 返回 §4 表值；`threshold_digest` 出确定性 digest；`compute_metrics` allclose 语义（造 numpy 对照）。依赖：无。

- **WI-A2 · judge_torch_allclose**
  改：validator.py:78-93 新增 + :114-116 注册。
  验收：单测 mismatch=0→pass、mismatch>0→fail、numel 非法→raise。→A1（读 policy 结构）。

- **WI-A3 · 多输出契约（gen_cases + caseset schema）**
  改：gen_cases.py:1187/1264/1281-1293（`expected.outputs[]`、逐输出 golden 落盘、compare 决策）。
  验收：单输出算子 caseset 向后兼容（`outputs` 长度1）；median golden.py 产 tuple→两 golden.npy；契约自检 spec↔caseset digest 三处一致。依赖：无（可与 A1 并行，schema 独立）。

- **WI-A4 · index_value_consistency 判据**
  改：precision_policy.py compute_metrics 加 gather 分支；validator judge 复用 A2。
  验收：单测——造 tie 输入+两组不同 index（值一致）→pass；值不一致→fail。→A1,A2,A3。

- **WI-A5 · 多输出 judge 折叠**
  改：validator.py:150-196 遍历 outputs AND 折叠 + per_case 组装。
  验收：单测 value pass+index pass→精度 pass；任一 fail→fail。→A2,A3,A4。

- **WI-A6 · value_profile（nan/tie/special 数值生成）**
  改：gen_cases 加 value_profile 维 + 借 case_generator.generate_array special_values/tie 逻辑。
  验收：单测 nan profile 产含 nan 数组、tie profile 产并列值。依赖：无。

### B 组·ctypes runner（新子包、部分可离线单测、执行须真机）

- **WI-B1 · aclnn_runtime 骨架搬运**
  新：base.py（原样）、acl_consts.py（抽常量）、aclnn_runner.py 纯 helper（parse_aclnn_op/contiguous_strides/_acl_dtype/_find_custom_opapi_libs）。
  验收：**离线单测**——parse_aclnn_op 对造的 aclnn_median.h 拿出 `Median`+arity；contiguous_strides 正确；_acl_dtype 覆盖 int64/int32/int8/uint8/bf16。依赖：无。

- **WI-B2 · run() 多输出 + bf16 改造**
  改：aclnn_runner.py run()（多输出 tensor、arity 从 header 推、bf16 窄化）。
  验收：离线 mock（ctypes 打桩）验 argtypes 拼装 arity 正确；bf16 窄化字节数对。→B1。

- **WI-B3 · aclnn_driver.py**（容器内执行脚本，只产 out.bin 不判定）
  新写；借 materialize_call 设计读 storage_dtype .bin → run → 落 out_k.bin。
  验收：离线——喂假 AclnnRunner（返回固定 array）验 out_k.bin 落盘+顺序正确。→B1,B2。

- **WI-B4 · ACL 枚举 9.0.1 核验**（真机/容器）
  对 `acl/acl_base.h` 核 `_ACL_DTYPES`（尤其 bf16=27）、format/memcpy/malloc/repeat_init。
  验收：容器内 D0 冒烟（内置 aclnnAdd）跑通、Compare 对。→B1。**须真机**。

### C 组·adapter/编排接线（须 B 组）

- **WI-C1 · aclnn_adapter.run_aclnn_py + find_aclnn_project**
  新写；deploy/build/exec/collect；比照 catlass_adapter 并入 repo_adapter.MODES。
  验收：mock 模式（不上真机）验 find_aclnn_project 软链守卫+签名校验；MODES["aclnn_py"] 可 dispatch。→B3。

- **WI-C2 · repo_adapter 多输出 readback + evidence**
  改：repo_adapter.py:867/889（多 out.bin 读回）、_precision_evidence（per-output metrics 调 compute_metrics）。
  验收：契约自检——evidence.precision.outputs[] 结构对、digest 与 caseset 一致。→A3,A4,C1。

- **WI-C3 · run_workflow 登记 aclnn_py acceptance-capable**
  改：run_workflow.py:34/37-41 `_REAL_MACHINE_MODE`/`_acceptance_capable` + :404 mode choices。
  验收：单测 aclnn_py 产的 evidence 不被降级；`--mode aclnn_py` 可选。→C1。

- **WI-C4 · 编排文本（SKILL.md/op-acceptance.md）**
  改：CP-C scope gate 放行 aclnn_py form、CP-D §6 基线补 torch_npu。
  验收：文本 review（nlpm 可选）。→C1,C3。

### D 组·DUT build + 真机端到端（须 A/B/C）

- **WI-D1 · Median golden.py + spec**
  新：samples/golden/Median/golden.py（torch）+ median spec.json（§2.1）。
  验收：golden.py 离线产 (values,indices)；spec 过 schema 校验。→A3。

- **WI-D2 · De-risk D0-D1（容器 stand-in）**
  容器内内置算子冒烟 + 内置/torch median stand-in 验多输出+bf16+index 判据。
  验收：D0 Compare 对；D1 多输出通路绿。→B4,C2,D1。**须真机**。

- **WI-D3 · PR6429 build + 端到端**
  checkout PR→build.sh install→ctypes 调 custom median→bounded 见证集全绿+覆盖账本。
  验收：§8 case 表逐 case pass；verdict=pass/passed_with_gaps。→D2。**须真机**。

### E 组·perf（第二里程碑，须 D 组 accuracy 绿）

- **WI-E1 · torch_npu 基线采集**（run_on_npu.sh 支路 或 参考仓 torch msprof wrapper 复用）
- **WI-E2 · ctypes custom kernel-only msprof 采集**
- **WI-E3 · repo_adapter perf 解析 source=torch_npu + perf_compare 冒烟**（源无关，逻辑零改）
  验收：kernel-only scope 双边一致、ratio 算对。→D3。**须真机**。

### F 组·可选加固（低优先、随时并行）

- **WI-F1 · torch_ref_freeze.py**（版本漂移 fail-closed）——adapt reference_resolver。
- **WI-F2 · dataset_verify.py**（sha256/size/路径逃逸自检）——adapt dataset_loader，纯 stdlib。

**关键路径**：A3→A4→A5 与 B1→B2→B3→B4→C2 两条主链并行，汇于 C2/D2→D3。A 组全离线可先行、最快出可测件。

---

## 8. 风险与未知

### 磁盘（根盘 8.6G）
- build.sh 编译产物 + custom_opp_*.run + msprof 采集（.csv/.json 可上百 MB/次）易撑爆。
- **对策**：`ASCEND_CUSTOM_OPP_PATH`/work_dir 指大盘（a3 远端工作根 340G free，非根盘）；msprof output 及时清；bounded 见证集限 case 数控采集量；build `build/` 复用不重编。

### build 可行性
- PR6429 build.sh 是否在 9.0.1 直接编成未知（beta vs release CANN 差异）；op_graph/op_api 结构须实见。
- **未知**：是否需特定 CANN 分支；install 是否要额外权限。De-risk D2 先验。

### 单卡 davinci0
- 共享容器多进程抢卡风险；`aclrtSetDevice(0)` 固定 0 卡。perf 采集须独占防干扰。
- **对策**：runner 串行；perf warmup+repeat 同进程隔离。

### ctypes 在容器可行性
- **核心未知**：9.0.1 的 ACL 枚举（尤其 **bf16=27** 需核）、format/memcpy/malloc 常量是否与参考仓一致；`libcust_opapi.so` 符号能否经 `CDLL(None)` 全局取到；`RTLD_GLOBAL` 在容器内行为。
- **对策**：WI-B4/D0 冒烟先行、0 依赖 PR，最先跑通排除 ABI 风险。

### 多输出
- **arity 解析**：median aclnn 签名的输入/输出 aclTensor 顺序须从 header 精确解析（`aclnnMedian(self, dim, keepdim, valuesOut, indicesOut, ...)`）——若 header 参数顺序与假设不符会崩。**须实见 PR 的 aclnn_median.h**。
- **index tie 判据**：`index_value_consistency` 依赖能从 case 重读 `self` + gather——须确认 gather 语义（多维 dim 的 index 是沿 reduced 轴，gather 要按 dim 展开）。torch.median 偶数长度取 lower-middle，NPU 实现是否一致须 tie case 验。
- **全局 median 无 indices**：`torch.median(input)` 单输出，`torch.median(input,dim)` 双输出——同算子两种 arity，须 spec 用两组 case 区分（据 `dim` 是否 present），driver 按 out arity 走，非按算子名。

### bf16
- numpy 无 bf16→窄化拷贝须真位截断（ml_dtypes 或 torch.to(bf16)）；容器有 torch_npu 可借 torch 做 host 侧窄化。输出 bf16 D2H 后解释须对。**风险中**。

### bounded 见证集（首程 case 清单，覆盖结构轴）

| # | 分派 | dtype | 长度 | rank/dim | keepdim | value_profile | 压的轴 |
|---|---|---|---|---|---|---|---|
| c01 | 全局 | fp32 | 奇(5) | rank1 | — | uniform | 全局分派·单输出·奇 |
| c02 | 全局 | fp32 | 偶(6) | rank1 | — | uniform | 偶(lower-middle) |
| c03 | 全局 | fp16 | 奇(7) | rank2 | — | uniform | 全局·fp16 |
| c04 | 全局 | int32 | 奇(5) | rank1 | — | uniform | 全局·int·exact |
| c05 | 按维 | fp32 | 奇(5) | rank2 dim=0(first) | false | uniform | 按维·双输出·first·奇 |
| c06 | 按维 | fp32 | 偶(4) | rank2 dim=1(last) | true | uniform | last·keepdim·偶 |
| c07 | 按维 | fp32 | 奇(5) | rank3 dim=1(middle) | false | uniform | middle·rank3 |
| c08 | 按维 | fp16 | 偶(6) | rank2 dim=-1 | false | uniform | fp16·负 dim |
| c09 | 按维 | bf16 | 奇(5) | rank2 dim=0 | false | uniform | **bf16 窄化** |
| c10 | 按维 | int32 | 奇(5) | rank2 dim=1 | false | uniform | int·双输出·index int64 |
| c11 | 按维 | fp32 | 奇(3) | rank1 dim=0 | false | uniform | **dim=1 退化(1D 按维)** |
| c12 | 按维 | fp32 | 奇(5) | rank2 dim=1 | false | **nan** | nan 传播 |
| c13 | 按维 | fp32 | 偶(6) | rank2 dim=1 | false | **tie** | **index tie·value_consistency** |
| c14 | 按维 | fp16 | 奇(7) | rank3 dim=2(last) | true | tie | fp16·tie·keepdim |
| c15 | 按维 | fp32 | 奇(5) | rank4 dim=2(middle) | false | uniform | rank4·middle |
| c16 | 按维 | bf16 | 偶(4) | rank2 dim=1 | true | tie | bf16·tie·偶·keepdim |

**计划覆盖账本**（⚠ **这 16 条是计划清单、整套见证集尚未执行**——下列一律读作「计划覆盖」，**不是已达成的实测覆盖**；承 covered≠真机绿）：全局/按维两分派、双输出(c05+)、fp32/fp16/bf16/int32、奇偶、dim=1退化(c11)、nan(c12)、tie(c13/c14/c16)、first/middle/last(c05/c07/c06)、keepdim T/F。
~~**已实测覆盖（截至 2026-07-24，仅 D0/D1，且用的是内置算子非 PR DUT）**：D0 `aclnnAbs` fp32 + bf16（单输出）；D1 `aclnnMedianDim` fp32-distinct / fp32-tie / bf16（多输出 + index 值一致性）。**PR6429 自定义 median 一条 case 都还没跑过。**~~
⚠ **SUPERSEDED（2026-07-24 当日稍晚）**：「PR6429 一条 case 都还没跑过」**已过时**——当日已用 PR6429 自定义 `aclnnMedian`
跑完 **60 条**正式用例，裁决 **`FAIL(精度)`**（`fail=6`，全部且仅有 NaN 输入场景；见文首横幅 / §9.8 / `reports/Median/`）。
⚠ 但**这 60 条不是上表那 16 条见证集**，且**覆盖仍不完整**——诚实的未覆盖清单：
输入 dtype 只测到 float32/float16/int32/int16（**bfloat16 / int64 / int8 / uint8 未测**）；
**op_def 声明的 int32 indices 路径未测**（首跑被工具逼着按 int64 走）；
**任务书点名的「dim 所指轴维度 = 1」零覆盖**；**性能维全 BLOCKED**；
**任务书要求的 `aclnnMedianDim` 入口 PR 根本没提供**（`api_surface_mismatch`），自然一条没测。
**未覆盖（scale 阶段补）**：rank5-8、int8/int64/uint8 输入、±inf、complex（median 无）、empty tensor、6 轴全笛卡尔。首程 16 case 通 + 账本诚实 → 再 scale。

### 其它未知
- torch_npu perf 是否输出 kernel-only 口径（否则 perf_compare blocked_incomparable_timing_scope）——须 profiler 配置验。
- `torch_adapter.py` escape hatch（registry.py:45-63 工程私带逻辑）：**建议默认关闭**（否则等于按算子特判后门），aclnn_adapter 只走通用 ctypes 默认分支。
- op工程若含 `op_graph` 但 aclnn 签名非标准两段式（有 opaque descriptor）→ 域外，fail-closed 标"不支持的接口能力"，不硬塞。

---

## 9. a3 de-risk 实测修正（2026-07-24 只读探测坐实，覆盖上文假设）

在 a3 目标容器（CANN 9.0.1 / torch_npu 2.10）只读探测，结论如下——**凡与上文冲突以本节为准**。

### 9.1 ACL 枚举全部对表命中（`acl_consts.py` 按此落值、零猜测）
`aclDataType`（`/usr/local/Ascend/cann-9.0.1/include/acl/acl_base_rt.h`）：FLOAT=0 / FLOAT16=1 / INT8=2 / INT32=3 / UINT8=4 / INT16=6 / INT64=9 / DOUBLE=11 / BOOL=12 / **BF16=27**（:156，✓ 蓝图假设正确）。
`aclFormat`（同文件）：**ND=2**。`aclrtMemcpyKind`（`acl_rt.h`）：**H2D=1 / D2H=2**。`aclrtMemMallocPolicy`：**HUGE_FIRST=0**。
注：`ACL_ERROR_REPEAT_INITIALIZE=100002` 是 `aclInit` 重复初始化的**错误码**（无害），非 workspace 常量。

### 9.2 ctypes 通路 = 高可行
容器内 `CDLL(RTLD_GLOBAL)` 加载 `libascendcl.so`(devlib)/`libnnopbase.so`(devlib)/`libopapi.so`(lib64) 全成功；`aclCreateTensor`/`aclrtMalloc`/`aclrtMemcpy`/`aclnnAdd*` 等符号全可见。**D0 冒烟具备执行条件**（此为只读探测后的**预判**，非实测；⚠ 真正跑通的 D0 用的是**内置 `aclnnAbs`**、不是 `aclnnAdd`——`aclnnAdd` 带 `aclScalar alpha` 参数、当时 runner 未支持，**Add 冒烟从未执行**，实测见 §9.6）。bf16 host 窄化：ml_dtypes 无 → **用 torch `.to(torch.bfloat16)`**（容器 torch 2.10 可用）。

### 9.3 ⚠ 修正 §5.3 的 D1 stand-in：改绑内置 `aclnnMedianDim`（非 `aclnnMedian`）
libopapi.so 同时导出内置 `aclnnMedian` 与 `aclnnMedianDim`（均无头文件发布）。按 ATen 惯例，内置 `aclnnMedian` 极可能是**全局·单输出**变体（签名不同），而 PR6429 的 `aclnnMedian` 是**dim 统一版·2 输出**。故 **D1 stand-in 绑内置 `aclnnMedianDim`**（其 1-in/2-out dim arity 与 PR6429 一致、走同一条 ctypes runner 机制），**绝不绑内置 `aclnnMedian`**（按 dim 签名调全局变体会崩/UB）。首跑用小张量对拍 CPU `torch.median` 当场坐实。（此推断标（推断），D1 实测验。）

### 9.4 ⚠ 修正 §5.1 的 DUT build：PR6429 无 per-op build.sh / 无 op_graph
PR6429（fork `LiJianhao2/ops-nn` @ `0290d61ac066f9f4e620a3714f5941e82dc4e72a`，base `cann/ops-nn` master，state=open）是 **ops-nn CMake 框架内**的实验算子（`experimental/index/median/`，23 文件），**没有** per-op `build.sh`、**没有** `op_graph`；op_host/CMakeLists 用 ops-nn 宏 `add_modules_sources(... OPTYPE median ACLNNTYPE aclnn_exclude)`（aclnn 接口手写、非自动生成）。→ **蓝图 §5.1「build.sh+op_api+op_graph 三签名 registry」对 ops-nn 不成立**。真实 build 配方（D2 用）：
1. 容器用户目录 clone fork、checkout `0290d61a`（或对 base master 打 PR patch）。
2. `source .../ascend-toolkit/set_env.sh` 后跑**仓根** `bash build.sh --pkg --experimental --soc=ascend910_93 --ops=median --vendor_name=customize --no_force`（op 名 `median` 取自 op_host CMakeLists 的 `OPTYPE median`；`--no_force` 规避 install_deps 联网）。
3. 产**自定义 vendor 包**（`libcust_opapi.so` 导出 `aclnnMedian`）；**install 前 `ASCEND_CUSTOM_OPP_PATH` 指用户目录**（不污染共享 opp）；runner 靠 `LD_LIBRARY_PATH` 指到该 vendor 加载符号。
4. 预估：数分钟~十几分钟、几百 MB~1G、风险=install_deps 联网(用 --no_force)+ 自定义 vendor 与内置 `aclnnMedian` 符号加载优先级。
→ `aclnn_adapter.find_aclnn_project` / CP-C scope gate 的「三签名」判据要改成「**ops-<族> 仓 + 仓根 build.sh --ops=<op> + `<op_subdir>` 下能找到手写 `aclnn_*.h` 接口头**」。
⚠ **接口头的落点是 `<op_subdir>/op_host/op_api/aclnn_median.h`**（本节早先写成 `<op_subdir>/op_api/…` 属笔误，2026-07-24 dogfood 实测订正——`experimental/index/median/` 下只有 `CMakeLists.txt README.md docs examples op_host op_kernel tests`，**没有** `op_api/`；参见 §9.6 D1 用的真 header 路径）。
故 gate 的实现是**在 `<op_subdir>` 下有界递归**（深度 ≤3、目录数上限，逐段拒软链）找 `aclnn_*.h`（剔 `*_impl.h`）——`op_api/`、`op_host/op_api/`、`op_api/include/` 各种落点都认，**绝不钉死某一层**（钉死即拿一种布局冒充「通用」，换个仓就把真 PR 判成「非域内」硬阻塞）；一份都找不到才 fail-closed，并在报错里列出**实际扫过的目录 / 扫到的 .h / 跳过的软链**。`aclnn_median.h` 两段式签名（self→dim→keepDim→valuesOut→indicesOut→ws→exec，1-in/2-out）**已坐实**、与蓝图一致，runner arity 按此。

### 9.5 aclnn 调用流坐实
example `test_aclnn_median.cpp` 的流程 = aclInit(nullptr)→aclrtSetDevice→CreateStream→aclrtMalloc(HUGE_FIRST)→Memcpy(H2D)→aclCreateTensor(+strides)→GetWorkspaceSize→aclrtMalloc(ws)→aclnnMedian→同步→Memcpy(D2H)，与 §9.1 枚举一一对应，正是 ctypes runner 要复刻的流程。空张量不支持（numel==0→`ACLNN_ERR_PARAM_INVALID`）。

### 9.6 D0/D1/D2 实测（2026-07-24 a3 目标容器坐实）
**D0（内置 `aclnnAbs`，走真实 `AclnnRunner.run` 1in/1out）✅**：fp32 max_abs_err=0.0、bf16 max_abs_err=0.0 → ctypes 底座（CDLL/CreateTensor/H2D/两段/D2H/枚举）+ **bf16=27** 真机全对。
**D1（内置 `aclnnMedianDim`，手写正确签名）✅**：fp32 distinct/ties + bf16 三例全绿——多输出逐 D2H、int64 indices、index 值一致性、bf16 窄化机制真机成立。**但暴露 runner 两处必修**（已修复中）：① `_ensure_init` 无条件要 custom vendor lib → 内置算子被拦（改可选）；② `run()` 不能传 median 的 `dim`/`keepDim` 标量 attr（须按签名真实顺序交织 tensor/scalar）。
**D2（PR6429 自定义 Median build）✅**：9.0.1 一次 build 通过（~2min），`libcust_opapi.so` 导出 `aclnnMedian`(+GetWorkspaceSize)，ctypes 可加载。

### 9.8 修后 runner 打真 DUT + ctypes MSTX 实测（2026-07-24，a3 容器）

**① 修后 runner × PR6429 自定义 `aclnnMedian`：局部 runner 冒烟 7/7 通过（⚠ 非验收结论）。**
⚠ **别摘引成「验收已绿」**：这 7 条是 runner 打通用的**局部冒烟**、**不含**后来失败的 NaN 场景，
与验收裁决无关。**同日的正式验收是 60 例、最终裁决 `FAIL(精度)`**（`fail=6`，全部且仅有 NaN 输入场景：
NaN 未按 `torch.median` 语义传播，失败只在 `dim=None`（拍平）与 `dim=0`（非末轴 transpose 路径），
`dim=-1` 末轴 pass）——逐字见 `reports/Median/acceptance.json` / `verdict.json`。
下面这 7 条里「values 与 torch 逐位一致」的说法**只对这 7 条无 NaN 输入成立**，不可外推。
签名从真 header（`experimental/index/median/op_host/op_api/aclnn_median.h`）用 `parse_aclnn_op(op_dir, symbol="Median")` 解析，**零改动零特判**即得正确有序形参表 `self(in) / dim(int64) / keepDim(bool) / valuesOut(out) / indicesOut(out)`。
覆盖：fp32 奇/偶长度、tie、bf16、int32、**全局（`indicesOut=NULL` → `shape=[]` 0-d 标量）**、keepdim。
- **下中位数语义坐实**：`[6.94,-7.40,-2.81,-0.90]` → `-2.81`（**非**均值 -1.855）。
- **bf16**：runner 的 round-half-even 窄化位模式 **== torch bf16 位模式**（断言过）。
- **📌 给验收报告的副产**：PR 文档 `docs/aclnnMedian.md` 称 indices 取「沿规约维**首个出现**」，但输入 `[3,1,3,1]`（中位值 1、首现下标 1）DUT 返回 **3**；**torch 也返回 3** → DUT 与 torch 契约一致，属**文档措辞不准、非功能 gap**。

**② 🔴 逮到会造成「假 PASS」的符号解析缺陷（分「当时发现」/「当前状态」两段读，别混）。**

**【当时发现·2026-07-24 de-risk 现场】**
`_ensure_init` 用 `RTLD_GLOBAL` 加载后从 `CDLL(None)` 全局取符号，而 **ELF 全局符号先加载者赢**、**CANN 9.0.1 的 `libopapi.so` 本身就导出 `aclnnMedian`/`aclnnMedianDim`**：
```
aclnnMedianGetWorkspaceSize  global=0xffff80dfeef0
                  libopapi.so(内置)=0xffff80dfeef0   ← 全局解析到内置
            libcust_opapi.so(DUT)=0xffff7f3b6ae0
```
本次因内置 arity 不同当场报 `ACL 161001` 才暴露。**危险场景是 PR 改一个同名同签名的既有算子——runner 会静默验证内置版本、报假 PASS**（验收工具最坏失效）。
**当时定的修法**：`aclnn*` 符号**优先从 custom vendor handle `dlsym`**、找不到再退全局；**记录每个符号来自哪个 `.so`** 作 provenance 进证据链；提供**严格档 fail-closed**（声明「必须来自 custom vendor」时只在全局找到即 raise）。
⚠ **LD_PRELOAD 不是可选方案**：实测每次进程退出必 `double free`/SIGABRT，且已定位非我方代码所致（裸 `aclInit+aclrtSetDevice` 在有 LD_PRELOAD 时同样 abort）。
当时另记两条：**B** `_find_custom_opapi_libs` 只读 `ASCEND_OPP_PATH`，而官方 install 的 `set_env.bash` 导出的是 **`ASCEND_CUSTOM_OPP_PATH`** → 按官方 env 跑**一个 custom lib 都找不到**（driver 却用对了，两模块口径不一致）；**C** 每 runner 建一条 stream 从不销毁、无 teardown、无法注入外部 stream（影响 perf 按 stream 归并）。

**【当前状态·2026-07-24 收工时】**（"已进修复队列" 这句已过时，别再照它办事）
- **已实现**（代码落地 + 离线单测）：① custom vendor handle 优先 `dlsym`；② **`dladdr` 反查定义方 so** ——
  只 dlsym 不够，POSIX dlsym 会沿 `DT_NEEDED` 依赖树继续找（实测 `libcust_opapi.so` 依赖内置 `libopapi_math.so`，
  后者定义了 `aclnnAbs/aclnnIsClose/aclnnSign/aclnnSort…` 整个 math 家族 → 严格档对这类算子**原本根本没 fail-closed**、
  provenance 还记成 custom vendor）；③ provenance 载重字段改为 `defining_lib`(+`defining_lib_verified`)，
  dlsym 走的 handle 另记 `resolved_via`，并写明 **`global_conflict` 不能单独当 DUT 证据**；
  ④ **严格档 fail-closed** 默认开；⑤ perf wrapper 同样改默认严格档 + `with` 销毁自建 stream（补上了 C 那条），
  并修 `close()` 后指纹丢失；⑥ B 那条（`ASCEND_CUSTOM_OPP_PATH` 口径）已统一。
- **已在真机复验的部分**：CP-C **harness 信任门**在 a3 复跑并留证——`reports/Median/harness_trust_gate.json`
  记 `strict_custom_vendor: true`、`symbol_sources: ["custom_vendor"]`、DUT 指向
  `.../vendors/customize_nn/op_api/lib/libcust_opapi.so`（PR head `0290d61a`）→ **该次自检验的确实是 PR 的算子、非 CANN 内置**。
- **⚠ 不确定（如实记，别编）**：**那 60 例正式验收跑用的是修前还是修后的符号解析，当前从落盘产物判定不了**——
  `reports/Median/evidence.json` 里**没有任何符号 provenance 字段**（`defining_lib` / `resolved_via` / `strict_*` 一个都没有，
  只有 golden/out 的 sha256），且信任门 JSON 的落盘时间（10:15:58）**晚于** evidence/acceptance（10:14:06）。
  故只能说：**信任门那次是修后 + 严格档**；60 例那次**不确定**。
- **下一步（未做）**：把符号 provenance（`defining_lib` / `defining_lib_verified` / `resolved_via` / 严格档开关）
  **落进 evidence.json**，让「验的是不是 DUT」成为每次验收的机读证据，而不是靠旁证推断。

**③ ctypes 侧能打 MSTX（perf 最后一个未知已解）。**
`torch_npu.profiler.profile(_ExperimentalConfig(mstx=True))` 包住 **ctypes** 调用 → **rid=1..5 非 0**、`MSTX_EVENTS` 5 行、`COMPUTE_TASK_INFO` 含 `aclnnMedian_Median_Median`×4（**确认是 DUT**）、`taskType=KERNEL_AIVEC`（**正落 db 白名单**）；按 MSTX 窗裁剪每窗恰好 2 kernel、三次散布 <3%（21.94/21.40/21.52us）。host-only range（不传 stream）同样能正确裁到 device kernel（时间戳同 ns 域、不要求同流）。
msprof CLI 包住**纯 ctypes**（完全不 import torch）也采得到（19.78/20.02/19.48us，与上者互印证；`Task Type=AI_VECTOR_CORE`）但**无 MSTX**。
- **对 fail-closed 的意味**：ctypes 通路**不必放宽**，可与 torch 通路共用 db 路线 + 同一 `KERNEL_*` 白名单。**代价**：MSTX 让 `torch_npu` 成为 ctypes 性能通路的硬依赖（虽然被测算子完全不经 torch）。建议门写成「**MSTX 证据** 或 **msprof CLI op_summary + 每 case 独立进程隔离**」二选一，且**产物显式标注走了哪条**，别让弱证据冒充强证据。
- **⚠ 一次 aclnn 调用 ≠ 一个 kernel**：indices 要 int64 时 aclnn 层会插一个 `CastAiCore_Cast`（~1.8us、占 8%）→ 性能口径必须是**窗内累加**，不能只取首个 kernel。
- iter0 的 MSTX 窗达 110ms（ACL 惰性初始化），但**kernel 本身时间第一次就稳**（20.20 vs 19.64/19.90）——warmup 是削 host 毛刺、不是削 kernel。

### 9.7 perf 采集通路 de-risk 实测（2026-07-24，a3 容器）——**推翻设计 3 条，补防御 3 条**
见证：`torch.median(x,dim=1)` fp32 1024×1024。**凡与 §3 组件⑤ 冲突以本节为准。**

**❌ 必须改**
- **A · MSTX 只能走 `torch_npu.profiler`，msprof CLI 下 Python 打不出**：`torch_npu.npu.mstx.range_start()` 在 msprof CLI 下 **rid 恒为 0、静默失败**（`mstx` 类有 `@_no_exception_func()` 吞异常）；CANN 原生 `import mstx` 进程挂死。唯一成立：`torch_npu.profiler.profile(experimental_config=_ExperimentalConfig(mstx=True))`（根因：`torch_npu/profiler/experimental_config.py:73` 的 `mstx` 开关只认 torch_npu profiler 自己的配置）。产物 `ascend_pytorch_profiler.db` 有 `MSTX_EVENTS` 表（startNs/endNs/rangeId/message），配 `TASK`+`COMPUTE_TASK_INFO` 可严格按窗口裁剪。
- **B · kernel 类型白名单要两套，且 db 路线那套我们原来全落空**：CSV 路线（`op_summary.Task Type` / `task_time.kernel_type`）= `AI_CORE/AI_VECTOR_CORE/MIX_AIC/MIX_AIV/AI_CPU`（原设计对）；**db 路线（`TASK.taskType`）= `KERNEL_AIVEC`/`KERNEL_MIX_AIV`/…**，用原白名单**一个都匹配不上 → 静默得 0 us**。**命中数为 0 必须 fail-closed**，绝不让空结果冒充"没有 kernel"。
- **C · msprof 默认 `--ai-core=on` 把数字抬高数倍，必须显式关**：Sort(MIX_AIV) 192.46 → 51.29（**3.75×**）；每次调用 kernel 总和 308.9 → 153.2 us（**2.0×**）。关掉后 msprof/torch_npu profiler 三路吻合（150~159 us/call）。**基线与被测必须同一采集配置。**

**⚠ 补防御**
- **D · MIX 类 kernel 在 `TASK` 表出现两次**（实测 TASK 373 行 vs COMPUTE_TASK_INFO 312 行，多出 52 个无 name 的 `KERNEL_MIX_AIV`）→ 必须 `join COMPUTE_TASK_INFO on globalTaskId` 且**丢弃 name 为 NULL 的行**，否则翻倍。
- **E · MSTX range 的 wall duration 绝不能当性能数字**：实测某窗 wall=141ms 而窗内 kernel 累加仅 1.5ms（差 90 倍，全是 profiler 启动+首次 kernel 加载）。range **只作裁剪边界**，数字必须来自窗内 kernel duration 累加。
- **F · CSV 时间戳两个坑**：`Task Start Time(us)` 值带**尾随 tab**、19 位十进制**用 float 解析丢精度 ~0.25us** → **优先 db（`startNs` 整数纳秒）、次选 csv**。

**✅ 成立可照写**：warmup=5/repeat=20/每 kernel 中位数×每次调用启动数（158.95 us/call vs wall 162.46；warmup 窗 157.60 与 measure 窗 158.95 仅差 0.9%）；**缺 MSTX 即 fail-closed 必须保留**（失败是静默的，不 fail-closed 会拿整进程 kernel 当测量窗）；`no_device_kernel_observed` 判得住（CPU-only 窗 0 个计算 kernel vs device 窗 120 个）；一次性 setup kernel 可区分（MSTX 三窗 或 `count % n_calls != 0`，实测揪出 `preload_stack_16KB` count=1）。

**📌 未验证**：`MEMCPY_ASYNC` 不计入这条**目前是空转**——造了 WITH_MEMCPY 窗口但 taskType 分布与纯 device 窗完全一样、没有 memcpy 类 task（CANN 9.0.1 + torch_npu Level0 下 H2D/D2H 不产生 TASK 行）。kernel-only 累加天然排除了它，但规则本身**没被真正测过**，别当已验证。

**环境更正**：容器内 `torch.npu.device_count()=**16**`（**非单卡**，设计勿假定单卡）；根盘 `/` 已用 99%、**仅剩 41G**（共享机，别堆 profiling 产物）。D2 产物完好（`libcust_opapi.so` 71520B、`ops-nn-pr6429` 980M）。
**下一个待 de-risk（dogfood 主通路）**：**ctypes runner 侧**能否打出 MSTX 并被采到——本次只坐实 Python/torch 一侧；CANN mstx C API 在 `tools/mstx/include/mstx/ms_tools_ext.h` + `aarch64-linux/lib64/mstx.so`，未验证。

**实测可复现 build 配方（覆盖 §9.4 预估、供 aclnn_adapter bake）**：
1. **取源**（fork 私有不可匿名 clone → 从 base 仓取 PR head，通用可复现）：
   ```
   cd <host 挂载目录>; git init -q ops-nn-pr6429 && cd ops-nn-pr6429
   git remote add origin https://gitcode.com/cann/ops-nn.git
   git fetch --depth 1 origin 0290d61ac066f9f4e620a3714f5941e82dc4e72a   # 或 refs/merge-requests/6429/head
   git checkout -q FETCH_HEAD
   ```
2. **依赖门**（`--pkg` 硬门，`--no_force` 挡不住；离线容器用零联网 shim、有网 `apt-get install -y dos2unix pigz`）：PATH 前置 `pigz`(≥2.4，可 shim 转 gzip、剥 `-p N`)+`dos2unix`(可 shim 转 `sed -i 's/\r$//'`)。
3. **build**（字段来自 op_def/任务书、非硬编码）：`source set_env.sh`（CANN 9.0.1）后仓根跑
   `bash build.sh --pkg --experimental --soc=<soc> --ops=<snake_op> --vendor_name=<v> --no_force`
   （experimental 算子必带 `--experimental`；soc 由适配硬件推：A2/A3→`ascend910_93`；op 名取 op_host CMakeLists 的 `OPTYPE`=`median`）。
4. **install**（绝不写共享 opp）：`build_out/*.run --quiet --install-path=<用户目录>/median_vendor`。
   ⚠ **`--vendor_name=customize` 落地目录是 `customize_nn`**（自动补 `_nn` 后缀）——adapter 定位 vendor 路径用 `customize_nn`。
5. **运行时 env**（install 生成的 `vendors/customize_nn/bin/set_env.bash` 为权威）：
   `ASCEND_CUSTOM_OPP_PATH=<...>/median_vendor/vendors/customize_nn:$ASCEND_CUSTOM_OPP_PATH`；
   `LD_LIBRARY_PATH=<...>/customize_nn/op_api/lib:<CANN>/lib64:$LD_LIBRARY_PATH`；ctypes `RTLD_GLOBAL` 加载 `libcust_opapi.so` 即解析 custom `aclnnMedian`。
   ⚠ **绝不要把 `<CANN>/devlib` 放进 `LD_LIBRARY_PATH`**（2026-07-24 dogfood 真机实测订正，旧文写了是错的）：`devlib` 是**编译期 stub 库**，运行期加载会让 CANN 报
   `stub library cannot be used for execution` → **`aclInit failed with ACL status 500000`，exec/perf 100% 必死**。二分证据：只前置 `lib64` → `aclInit=0` 正常；只前置 `devlib` → stub 错。
   （§9.2 那句「`libascendcl.so`(devlib) 加载成功」是 D0 **只读探测期**的观察——能 `CDLL` 打开 ≠ 能用它跑 kernel，勿据此配 env。）
   ⚠ **同理绝不要覆盖 `ASCEND_OPP_PATH`**：曾 `export ASCEND_OPP_PATH="<install-path>"` 盖掉 CANN 自己的 opp 根 → 60/60 用例 `ACL 561103`（找不到 kernel）。
   对照证据：`ASCEND_OPP_PATH=CANN 真根 → 60/60 pass`；`=install-path → 0/60`。custom lib 的发现由 **`ASCEND_CUSTOM_OPP_PATH`** 负责，不需要动 `ASCEND_OPP_PATH`。
   median 顶层 CMakeLists 无 add_sources → `--experimental` 虽置 torch-ext 但对 median 空转、**不依赖 torch**；install_deps 的实际安装函数在 main 流程未被调用（只有前置检查门）。

### 9.9 cannbot 同口径性能链真机闭环（2026-07-26，a3 容器）

§9.7 A 的结论只适用于 `torch_npu._C._mstx` / `torch_npu.npu.mstx`：该 API 在 msprof CLI 下
`rid=0`。按 cannbot 的真实实现直接 ctypes 加载 `libms_tools_ext.so`，调用
`mstxRangeStartA(message, stream.npu_stream)` / `mstxRangeEnd`，真机得到 `range_id=1`；
msprof 的 `msprof_tx_*.csv` 与 `task_time_*.csv` 能裁出有效 kernel-only 窗。因此 collector 已改为：

- custom 与 torch baseline 均为 `msprof CLI + ctypes MSTX + CSV`；
- 同为 warmup=5/repeat=20、`median_x_launches`、多 kernel 求和；
- `PROFILER_TRACE_EX` 仅作为 msprof 控制 task 忽略，其他未知 task type 仍 fail-closed；
- DB parser 只保留给历史/离线产物，live 路径显式钉 `route=csv`；
- `numel<4096 → trivial-met` 从比较器、GPU baseline consumer 和验收门整体废止。

真机 50-case 结果（复用同一冻结 caseset，custom/baseline 共 100 次 msprof side）：

- custom：50/50 为 NPU、CSV 可解析；
- torch baseline：48/50 为 NPU、CSV 可解析；
- 2 个 blocked：`median_bfloat16_1_scalar_a0`、
  `median_bfloat16_1x1x1_bndlo_a0`。两者均为 torch_npu/CANN 内置基线报 161002
  （BF16 的 dim axis value 1 不支持），custom 侧成功；不是 DUT 缺陷，也不是 parser 失败；
- 移除免测后重算：`perf_cases=50`、`cases_scored=48`、`达标=35`、`blocked=2`、
  `status=blocked`、`trivial_rows=0`；
- 原 `numel<4096` 的 24 个 case 中，22 个有真实 ratio，2 个就是上述明确 blocked；不再有自动通过。

这证明 cannbot 的采集方法可以对标并已接入；但当前性能整体裁决仍是 `blocked`，不能表述为性能 PASS。
