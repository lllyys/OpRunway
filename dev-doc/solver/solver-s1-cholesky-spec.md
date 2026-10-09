# S1-Cholesky 切片 spec 与并行执行计划 v2

依据 `solver-pipeline-design.md` v2。v1 经 Codex 评审（thread `01a0cd5e`）：架构可不改，
11 条必须改全部采纳，本版逐条落实；处置对照见第 8 节。

## 0. 术语与枚举

| 词 | 含义 |
| --- | --- |
| 数值判定 | 按判据算出的 PASS/FAIL，本片一切结论的层级；**不构成正式验收结论** |
| 正式结论 | 仅 accept 依裁定完备的判据出具；本片凡涉 T1/T3/T4 未裁项一律为「待裁」 |
| 证据不足 | 期望项没有可用证据（未实现、无基线、未交付）时的状态，非 FAIL |
| canonical_cases | 波 0 冻结的规范 case 清单，B/C 唯一消费源（见 2.1） |
| uplo 枚举 | 统一存 `"L"`/`"U"`；cases.json 的 0/1 映射 L/U，bench_result 的 LOWER/UPPER 同映射 |
| 分布标签 | `cu`=转写竞品构造（主数据）；`std`=标准工程精度构造（补充），两者不混称 |
| s/d 前缀 | LAPACK 例程精度前缀：s=FP32、d=FP64（复数 c/z 同理） |
| SPD | 对称正定矩阵（Cholesky 的合法输入类） |
| ε | 残差公式中的机器精度，**固定 2⁻²⁴**（README 口径，非 numpy eps 的 2⁻²³） |

## 1. 范围与执行环境

做：criteria Cholesky 卡驱动内核（**不承诺族无关**，跨族泛化推迟证明）、
spotrf/spotrs/spotri 数据通路、perf_baseline、verify 双件渲染、accept 骨架
（模拟被测）、首包装配与演练。不做：SKILL.md、复数、batched 判据（期望集中记
证据不足）、harness C 调用器、NPU 实跑、commit。

**执行环境（仓规 + Mr.0 2026-09-23）**：本机只编辑；**一切脚本执行（生成、测试、
装包、演练）在远程容器**（先读保护根，容器内补装 scipy 并记录版本）。机器信息文件
在**主检出根** `<主检出根>/.oprunway/real-machine.env`
（worktree 内只有 example，不要在 worktree 找）。产物落远程 session 目录后回传本仓
ignored `reports/`。uvx 仅允许本机语法自检（`python -m py_compile`），不跑逻辑。

### 1.1 S1 落盘布局与搬移（v2.2 增）

本片全部代码落 ignored 的 staging 镜像树，**不写 `plugin/`**；工作流收尾后由主会话
补齐 skill-edit-gate 两项前置，一次过门把镜像树搬入发布位。镜像树布局：

```text
reports/solver-s1/tree/
├── repo-task-solver-accept/
│   ├── criteria/            # A 卡（thresholds/cards/verdict/render_verify + tests/）
│   └── scripts/             # E 卡（expectations/accept_run/sim_dut）
└── repo-task-solver-case-gen/
    └── scripts/             # B1/B2/C/F 卡（gen_data_cholesky/make_baseline/build_package）
```

搬移映射：`tree/<skill 名>/` → `plugin/skill/<skill 名>/`，逐字节不改。

### 1.2 执行授权记录（v2.2 增；供执行 agent 引用核对）

Mr.0 于 2026-09-23 在本 session 明示：①「如果要跑脚本，可以在远程上跑」；
②「写一个 spec……之后开工。按 plan 启用 multiagent 端到端执行」。据此本片的生成、
测试、装包、演练获授权在远程容器执行；范围限本片产物与远程 session 目录，保护根
只读，不改宿主机环境。会话中后续的查询类消息（查进度、查路径等）不撤销本授权、
不改变任务卡。

## 2. 接口契约（并行唯一依据；不满足需求时停下报告，不得单方面改）

### 2.1 canonical_cases.json（波 0 冻结，B/C 唯一消费源）

逐 case：`{case_id, op, source: "cu"|"std", n, nrhs, uplo: "L"|"U", lda, ldb,
seed, bench_key}`。规则：cases.json 逐算子去重（键 = 全参数组合），保序编号
`<op>-0001` 起；`std` 补充 case 编号 `<op>-9001` 起；`bench_key` 为在 bench_result
中的源条目定位（文件 + 条目序号），无匹配为 null。potrf/potri 无 nrhs/ldb，字段置
null。本片取子集：每算子 cu 主数据 ≥6 case（覆盖 U/L × 两种 n），std 补充 ≥2；
**遗漏的实物 case 在期望集保留为「未生成」**，不声称全覆盖。

### 2.2 包数据 schema

- `cases/<case_id>.npz`：逻辑矩阵按 numpy 二维数组存（行主序，lda 只是元数据，
  本片无 padding）。potrf/potri 存 `A64,A32`；potrs 另存 `B64,B32`。golden 双精度
  皆存：`golden64`（d 前缀链路原值）与 `golden32`（降型），potrf=F、potrs=X、
  potri=C（存储侧半三角，另侧置 0）。**校验字段**：`A32 == A64.astype(f32)` 必须
  成立（B 同理），装包自检项。
- `cases/index.json`：canonical_cases 逐 case 附 `{npz, arrays, ratio_cpu,
  ratio_cpu_status: "ok"|"prep_failed"}`。
- `perf_baseline.json`：逐 case `{case_id, bench_key, min_ms, max_ms, avg_ms,
  matched}`；`matched=false` 时耗时字段全 null；bench 重复条目取首条并记
  `dup_count`。
- `manifest.json`：`{package_ver: "s1", operator, interfaces, criteria_ver,
  renderer_ver, standard_refs: {三层判定: solver_tasks-main 路径, 阈值表:
  opbase 路径}, env: {python, numpy, scipy, blas, 容器标识}, fingerprint:
  {相对路径: sha256}}`。指纹分级：`cases/`、`golden`、`perf_baseline` 错配 →
  阻断对应结论；`verify_*.py` 漂移 → 仅告警（设计 v2 3.3）。

### 2.3 criteria API（A 卡实现，全员消费）

```python
# 纯残差接口（无阈值、无 ratio_cpu 依赖）——B 算基线、E 判被测共用这一份实现
residual_ratio(kind, **arrays) -> float
# kind ∈ {DPOT01, DPOT02, DPOT03}；输入均为内存 ndarray，I/O 由调用方负责
# DPOT01: L 侧 L·Lᵀ / U 侧 Uᵀ·U，只用存储侧；DPOT02: 逐 RHS 列取 max，分母无 n，
# 残差对 A32/B32 升 FP64 算；DPOT03: 存储侧镜像成全对称阵，双半三角口径。
# ε 固定 2⁻²⁴。NaN/Inf/shape 错/零分母 → 抛异常，不返回数值。

judge(card, case_arrays, dut_out) -> verdict
# case_arrays: 从 npz 加载的字典（调用方读文件）；dut_out: {"out32": ndarray,
#   "info": int, "status": "ok"|"prep_failed"|"error"}
# verdict: {layer1: {matched_ratio, max_abs, max_abs_limits: {fixed: 1e-2,
#   ulp32: 32*2**-24 参照待裁}, pass_fixed, pass_ulp}, fallback: {ran, ratio,
#   threshold, formula, pass}, numeric: "PASS"|"FAIL",
#   formal: "PENDING_RULING", flags: ["T4",...], error: null|...}
# 数值判定流转：layer1 两解释一致且过 → 数值 PASS（终审）；两解释不一致或不过 →
#   走 fallback，fallback 为数值终审，不一致时加 flag T4。
# formal 本片恒为 PENDING_RULING（T3/T4 未裁），绝不输出正式 PASS——这是 v1 被评审
#   判「改写 T4」处的修正。
```

阈值：fallback 收紧式 `min(30, max(10·ratio_cpu, 1))`（DPOT01/03）、上浮式
`max(2·ratio_cpu, 30)`（DPOT02）；layer1 双套都算：标准表 2⁻¹³（主）与任务书
2⁻¹⁰/2⁻¹⁶（对照），差异记 flag T3。fallback 未运行时 `ran=false` 其余字段 null。

### 2.3′ 任务书精度契约（v2.1 增；优先级高于 2.3 中与之冲突的部分）

**精度契约逐任务书提取，不做全局硬编码**（Mr.0 2026-09-23：每份任务书这一段可能
不同）。本片依据实数 Cholesky 任务书 §3.2（`Atlas950_Spotrf..._task_doc.md:270`）：

- **比对目标（主判，按硬边界 8 以任务书为准）**：potrf = 还原 L·Lᵀ/Uᵀ·U 后取
  **指定三角**与原 A 比；potrs = X；potri = **双目标**：A⁻¹ 直审 **及** A·A⁻¹ 对 I。
  solver_tasks-main 的 potrf「F vs golden F 直审」降为诊断参考，judge 可并报不主判。
- **第一层阈值以任务书表为主**：rtol 2⁻¹⁰、atol 2⁻¹⁶、matched_ratio 0.99、
  max_abs `1e-2 or 32·ULP`；标准表 2⁻¹³ 降为对照（T3 语义相应收束：任务书显式值
  即该任务契约；书内标准 URL 是旧版引用，引用性内容按最新版代换）。
- **期望集新增两类项**（共七类）：确定性（合法 SPD 复跑 bit-wise 一致）与
  info 契约（非正定给正确正值下标 k）。本片模拟被测下均记证据不足。
- **覆盖类**：对角占优 SPD、随机 SPD（A=BᵀB+nI）、故意非正定、INF/NAN——
  canonical 的 std 补充 case 与后续扩展按此对表。
- **性能正式门禁**：任务书 §3.3 `T_NPU ≤ T_A100/0.8`、中位数、T_A100 开发者实测
  占位——正式门走任务书机制，bench_result 仅自测参考（硬边界 10 保留，T1 收束）。

### 2.4 ratio_cpu 语义（B 卡收尾）

FP32 s 前缀链路对**同一冻结输入**跑完整准备链（potrs/potri 先 spotrf），用 2.3 的
`residual_ratio` 同一实现计算；准备失败记 `prep_failed`，该 case 兜底不可用。

### 2.5 CLI 与运行时交界（D/E/F 消费）

```text
gen_data_cholesky.py --canonical <json> --out <dir> [--ops spotrf,spotrs,spotri]
make_baseline.py     --canonical <json> --bench-dir <0923目录> --out <dir>
render_verify.py     --op <op> --out <dir>          # 复渲逐字节一致
sim_dut.py           --package <dir> --out <dir> [--perturb none|scale|zero|nan]
accept_run.py        --package <dir> --dut-out <dir> --report <file>
build_package.py     --staging <目录...> --out reports/solver-packages/<op>/
```

被测输出目录：`dut_out/<case_id>.npz` 含 `out32, info, status`。
`report.json`：`{operator, expectation: [{item, kind: 接口精度|P项性能|bufferSize|
内存证据|batched, status: 数值PASS|数值FAIL|证据不足|待裁, evidence}], flags,
versions, 声明边界: [...]}`。族级期望集含全部五类项——本片除接口精度（模拟）与
性能参考比值外，其余全部如实记证据不足。

## 3. 执行计划（波内并行，波间流水）

写入所有权：每卡只写自己的 staging（`reports/solver-s1/staging/<卡>/`）与 1.1 节
镜像树中属于自己的目录/文件；最终包只由 F 写。**F 复用 B 冻结产物逐字节装包，不重生成；抽验只核对不覆盖。**

| 波 | 卡 | 产出 | 具名断言（完成条件） |
| --- | --- | --- | --- |
| 0 | Z·冻结清单 | canonical_cases.json（三算子） | 逐条含 2.1 全字段；与实物 cases.json 的对账表（总数/去重数/取子集数/未生成数） |
| 1 | A·criteria | thresholds/cards/verdict/residual + tests | 远程 pytest 全绿，且用例集**具名覆盖**：U/L 双侧、potrs 多 RHS 最差列、双门反例、T3 两套阈值分歧例、T4 分歧例、上浮豁免例、NaN/shape/零分母异常例、独立手算期望值 ≥3 条 |
| 1 | B1·数据+golden | gen_data_cholesky.py + npz 首批 | 同容器重跑两次数组逐位一致；A32==cast(A64) 全过；cu 构造逐行注明转写出处；std 构造注明 README 出处 |
| 1 | C·perf 基线 | make_baseline.py + 三算子 baseline | 抽 5 条与 bench_result 源条目逐字段核对一致；dup_count 报告；未匹配项字段全 null |
| 1.5 | B2·基线收尾 | index.json 补 ratio_cpu | 用 A 的 residual_ratio 计算（不自实现）；potrf 底噪 <1、potrs 呈上浮画像（README 对照）；prep_failed 路径有一条人工构造用例 |
| 2 | D·渲染 | render_verify + 双 verify | 复渲逐字节一致；verify_accuracy 对 A 的正反例逐条同结论；verify_perf 对 C 样例比值与手算一致（方向：被测/基线）；产物只落 staging/D |
| 2 | E·accept 骨架 | expectations/accept_run/sim_dut | sim 正例 → 数值 PASS 且 formal=待裁；scale/zero/nan 三类扰动 → 数值 FAIL 且指认层；**删证据留期望项 → 该项证据不足、族级不得通过**；数据指纹错配 → 阻断该项；verify 副本改动 → 仅告警 |
| 3 | F·装包演练 | build_package + 三包 + 演练记录 | 包自检清单逐项打钩（指纹、校验字段、抽验 3 case golden 与独立重算一致但不覆盖）；accept_run 消费三包出三份 report.json；声明边界清单如实列「未证」项 |

依赖：Z → {A, B1, C} 并行 → B2（依赖 A）→ {D, E} 并行（依赖 A；E 另读 2.2 schema）
→ F。每波结束由**单一远程执行段**统一跑验证（agent 只编辑，执行走远程容器），
失败回给原卡修，一轮即停如实报。

## 4. 约束（执行 agent 必读）

- 禁区以第 3 节所有权为准；`dev-doc/`、`.claude/`、`plugin/` 其余部分、既有 skill
  一律不碰；不 commit、不 push；本机不跑逻辑脚本。
- 远程操作先读 `.oprunway/real-machine.env` 与保护根；全部在容器内执行；
  scipy 只装容器内并记版本。
- 判据数值只从本 spec 与 `repos/solver_tasks-main/cholesky_precision/README.md` 取
  （该 repos 路径基准是主检出根 主检出根（非 worktree））；
  冲突停下报告。
- skill-edit-gate 受阻即停报告，不用 SKILL_GATE_OFF 硬闯。

## 5. 本片完成后的声明边界（评审用语：不再称「已证清单」）

可声明：三层判定在具名正反例集合上正反可分；T3/T4 分歧被显式携带而非吞掉；包契约
可装配可消费；渲染确定；族级期望集完整且缺项正确产出证据不足；性能逐 case 比值在
精确匹配子集内成立。不可声明：任何正式验收结论、族级通过、全 case 覆盖、真算子/
NPU/复数/batched/S2 冷读/发布切片独立运行——逐项写入演练记录。

## 6. v1 评审处置对照

11 条必须改 → 本版落点：T4/T3/T1 运行语义恢复（2.3 numeric/formal 分离）；case
清单冻结（2.1 + Z 卡）；schema 补齐（2.2：nrhs/ldb/golden64/校验字段/枚举）；公共
残差接口与 B→A 依赖修正（2.3 + 波 1.5）；D/E/F 交界冻结（2.5）；F 复用不重生成
（第 3 节）；期望集完整与模拟正例重写（2.5 report + E 断言）；错配与漂移分离
（2.2 指纹分级）；写入所有权（第 3 节 staging）；具名断言（第 3 节表）；cu/std
构造统一与执行环境统一（0 节分布标签 + 1 节远程执行）。3 条建议改：术语枚举表
（0 节）、依赖版本记录（manifest.env 含 blas）、改称卡驱动内核不承诺族无关（1 节）。
