# Layer 0 坐实（一）：`spec.json` schema + 3 真实例

`spec.json` = 任务书解析产物、整条流水线的入口契约。这里把字段坐实，并用 3 个有代表性的 merged 任务实例化验证它兜得住语料多样性。配套 `oprunway-workflow-design.md`。

## 1. `spec.json` schema（坐实版）

```jsonc
{
  "op": "string",                         // 算子名
  "repo": "string",
  "hardware": ["string"],                 // 适配硬件（A2/A3/950PR/300V Pro…）
  "language": "AscendC",
  "reference": {                          // 参考实现（三类）
    "type": "tbe | gpu_lib | existing_op",
    "ref": "string",                      // 如 内置TBE Sign / cuSPARSE cusparseSpMV
    "formula": "string?",                 // 有则记（SPMV 有）
    "path": "string?"                     // TBE 参考路径 / GPU 头文件
  },
  "change": {
    "kind": "new_op | add_dtype | semantic | bugfix",
    "dtypes_added": ["string"],           // add_dtype 时
    "note": "string?"                     // semantic/bugfix 说明
  },
  "pr_impact": { "def": "", "kernel": "", "tiling": "", "tests": "" },  // 加dtype 4处影响面（辅助）
  "params": [ {
    "name": "", "io": "in|out|inout|attr",
    "dtype": ["string"], "format?": ["string"],
    "shape?": "string", "noncontiguous?": "bool|na",
    "constraints?": "string"
  } ],
  "dtype_combinations?": [ ["A/X","compute","Y"] ],      // ★ 有组合约束时（SPMV 5 组），防非法并集
  "params_source": "task_doc | derived_from_reference",  // ★ 任务书有表 vs 需从参考推
  "generalize": true,
  "verify_mode": "numerical | behavioral | exact",       // 数值 / 行为(Sleep) / 精确(bool·整数二进制)
  "precision": {
    "oracle": "ascendoptest | mere_mare | torch | np_isclose | scipy | std_exact | atk_double | none",
    "threshold_source": "string",
    "dtype_thresholds": { },              // 如 MERE/MARE 按 dtype
    "fallback": { "oracle": "", "threshold": "" }   // ★ 单标杆不满足时（SPMV→ATK双标杆）
  },
  "perf": {
    "baseline": "tbe | gpu_a100 | same_op_dtype | none",
    "target": "string",                   // 如 "无劣化" / "≥95%" / "0.5×A100"
    "reference_cases": [ { "desc": "", "dtype": "", "us": 0 } ],  // ★ 有具体用例基线时（SPMV 有）
    "small_shape_exception": "object?"    // ★ T6(待散文门)：{text, when_us_below, abs_gap_us_within, requires}；
                                          //   机读阈值独立字段(when_us_below/abs_gap_us_within)供 perf_compare；
                                          //   legacy 纯字符串向后兼容(perf_compare 正则兜底解析)
  },
  "deliverables": ["string"],
  "task_pr_gaps": ["string"]              // 任务书↔PR 落差（待确认）
}
```

★ = 坐实过程中新加的字段（见 §3）。

## 2. 三个真实例（验证多样性）

### 2.1 Sign（ops-math，加 dtype 重写）
```jsonc
{ "op":"Sign", "repo":"ops-math", "hardware":["Atlas A2","Atlas A3"],
  "reference":{"type":"tbe","ref":"内置 TBE Sign","path":"opp/built-in/... kernel/proto/config 三处"},
  "change":{"kind":"add_dtype","dtypes_added":["int16"],"note":"与 TBE 全对齐所有 dtype/format + 加 int16"},
  "pr_impact":{"def":"sign_def.cpp dtype 白名单+A3 config","kernel":"sign.h int16 分支","tiling":"schMode 2 档","tests":"—"},
  "params_source":"derived_from_reference",   // 任务书无参数表 → 从 TBE Sign 推
  "generalize":true, "verify_mode":"numerical",
  "precision":{"oracle":"ascendoptest","threshold_source":"AscendOpTest 默认阈值"},
  "perf":{"baseline":"tbe","target":"无劣化",
          "small_shape_exception":{"text":"<10us 差 3us→仿真图证明","when_us_below":10,"abs_gap_us_within":3,"requires":"simulation_plot+analysis"}},  // T6(待散文门)：string→object
  "task_pr_gaps":["PR 额外加了 ascend910_93(A3) config，任务书只写 A2/A3——一致"] }
```

### 2.2 IsClose（ops-math，语义改造）
```jsonc
{ "op":"IsClose", "repo":"ops-math", "hardware":["Atlas A2","Atlas A3"],
  "reference":{"type":"tbe","ref":"内置 aclnnIsClose TBE"},
  "change":{"kind":"semantic","note":"二进制比较→逻辑值比较（对齐 CPU）"},
  "params_source":"derived_from_reference",   // ★ 任务书无表 → 以下 params 均从 TBE/参考推（非任务书原文）
  "params":[{"name":"self","io":"in","dtype":["FLOAT","FLOAT16","BFLOAT16","INT32"],"shape":"广播≤8维","noncontiguous":true},
            {"name":"other","io":"in","dtype":["FLOAT","FLOAT16","BFLOAT16","INT32"],"shape":"广播≤8维","noncontiguous":true},
            {"name":"rtol","io":"attr","dtype":["double"]},{"name":"atol","io":"attr","dtype":["double"]},
            {"name":"equal_nan","io":"attr","dtype":["bool"]},
            {"name":"out","io":"out","dtype":["BOOL"]}],
  "generalize":true, "verify_mode":"exact",    // 输出 BOOL → 精确逐位（agent 实证 int8 精确比对）
  "precision":{"oracle":"ascendoptest","threshold_source":"AscendOpTest 默认阈值（bool 实为精确）"},
  "perf":{"baseline":"tbe","target":"所有核参与场景 ≥95%",
          "small_shape_exception":{"text":"<10us 差 3us→仿真图","when_us_below":10,"abs_gap_us_within":3,"requires":"simulation_plot+analysis"}},  // T6(待散文门)：string→object
  "task_pr_gaps":[] }
```

### 2.3 SPMV（ops-sparse，GPU 移植 / 库式）
```jsonc
{ "op":"SPMV", "repo":"ops-sparse", "hardware":["Ascend 950PR"],
  "reference":{"type":"gpu_lib","ref":"cuSPARSE cusparseSpMV","formula":"Y=α·op(A)·X+β·Y (CSR)"},
  "change":{"kind":"new_op","note":"现样例是 A2 版，要做 950PR；C 接口对齐 cusparse.h 三阶段"},
  "params_source":"task_doc",                 // 任务书有完整参数表
  "params":[{"name":"csrRowPtr","io":"in","dtype":["int32"],"shape":"[M+1]"},
            {"name":"csrColInd","io":"in","dtype":["int32"],"shape":"[NNZ]"},
            {"name":"csrVal","io":"in","dtype":["float16","bfloat16","float32","int8"],"shape":"[NNZ]"},
            {"name":"x_vec","io":"in","dtype":["float16","bfloat16","float32","int8"],"shape":"[K]"},
            {"name":"y_vec","io":"inout","dtype":["float16","bfloat16","float32","int32"],"shape":"[M]"},
            {"name":"trans","io":"attr","dtype":["bool"]},{"name":"alpha","io":"attr","dtype":["float32"]},
            {"name":"beta","io":"attr","dtype":["float32"]},{"name":"compute_type","io":"attr","dtype":["int32","float32"]}],
  "dtype_combinations":[["float32","float32","float32"],["int8","int32","int32"],["int8|float16|bfloat16","float32","float32"],["float16","float32","float16"],["bfloat16","float32","bfloat16"]],  // ★ 5 组(A/X,compute,Y)禁非法并集；各 tensor 参数 format=一维、非连续=支持
  "generalize":true, "verify_mode":"numerical",
  "precision":{"oracle":"mere_mare","threshold_source":"生态算子开源精度标准 experimental_standard",
    "fallback":{"oracle":"atk_double","threshold":"MERE/MARE/RMSE 比例 ≤ 2/1.2/1.2（cv_fused_double_benchmark）"}},
  "perf":{"baseline":"gpu_a100","target":"0.5×A100",
    "reference_cases":[{"desc":"128×128@95%","dtype":"float32","us":43.9},{"desc":"1024×1024@99%","dtype":"float32","us":46.3},
                       {"desc":"2048×4096@97.5%","dtype":"float32","us":45.4},{"desc":"160220×68750@99.9%","dtype":"float32","us":193.2}]},
  "deliverables":["fork 仓(工程+README+全规格 acl 调用测试+AscendOpTest/ATK 工程结果)","自验证报告","评审设计文档"],
  "task_pr_gaps":[] }
```

## 3. 坐实过程暴露的（schema 精化 + 设计含义）

1. **`params_source` 字段**：任务书**只有约 17/41 带参数表**（202604+202605 共 41 份、按「参数名」表头计；SPMV 有，Sign/IsClose 没有）。无表时参数须**从参考实现（TBE/GPU）推**——parse 步要标注来源，且「从参考推」可能不准 → 进 `task_pr_gaps` 或人工确认。
2. **`precision.fallback`**：精度口径可**分层**（SPMV：先 experimental_standard 单标杆，不满足才 ATK 双标杆 2/1.2/1.2）。gen_cases/validator 要支持「主口径 + 兜底口径」。
3. **`perf.reference_cases`**：性能目标可能是**具体用例的基线时延**（SPMV 给了 4 个 GPU-A100 用例 us），不只一个系数 → gen_cases 应**据 reference_cases 生成对应性能用例**（同规模/稀疏度），Task 3 对拍这些点。
4. **`verify_mode` 靠推导、非照抄**：本文三例覆盖 `numerical`(Sign) 与 `exact`(IsClose，输出 BOOL→精确比对，虽任务书写 AscendOpTest 阈值)；`behavioral` 由 Sleep 另证（不在本三例）。**parse 要能从「输出 dtype + 算子性质」推 verify_mode**，不能只抄任务书的精度句。
5. **三类参考 → 三种 golden 路子**：tbe（对拍内置算子/CPU）、gpu_lib（对拍 GPU 库语义，实操常用 torch/scipy CPU 复现，如 SPMV 的 CPU 参考）、existing_op。→ 决定 `caseset.json` 的 `golden_source`（**属 caseset 字段、非 spec**，坐实 caseset 时定）。

## 4. 结论

`spec.json` 用 3 个跨类实例（TBE 重写 / 语义改造 / GPU 移植）验证——**已覆盖代表性子集**：参考 tbe·gpu_lib（缺 existing_op）、改动 new_op·add_dtype·semantic（缺 bugfix）、精度 ascendoptest·mere_mare+atk 分层、性能 tbe·gpu_a100（缺 same_op_dtype）、验证 numerical·exact（behavioral 由 Sleep 另证）。**未覆盖项待补样例。** 精化出 5 个字段（`params_source` / `dtype_combinations` / `precision.fallback` / `perf.reference_cases` / `verify_mode` 推导规则）。

**下一步可选**：① 同法坐实 `caseset.json`（拿 IsClose 或 SPMV 生成一份真用例集，验证 gen_cases 的规则）；② 坐实 `repo_adapter` 接口（拿已 clone 的 ops-sparse SPMV 走 build/run/golden/perf，验证 new_example 模式）。
