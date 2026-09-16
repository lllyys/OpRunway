---
name: repo-task-blas-harness-gen
description: >-
  开发者的 ops-blas 算子工程缺少 C++ 测试 harness 时，据任务包（repo-task-blas-case-gen
  形制的交付目录）确定性生成一套 harness overlay，经 fail-closed 安装 preflight 装入
  目标工程，交 repo-task-blas-accept 验收。为缺 harness 的工程补齐测试树时使用本 skill，
  属边缘工具，开发者自带 harness 的主流场景不用；只需生成任务包时改用
  repo-task-blas-case-gen，验收已就绪的工程时改用 repo-task-blas-accept。
---

# BLAS C++ harness 生成与安装

**harness** 指驱动一次算子验收的整套 C++ 测试代码：CSV 参数解析（`<op>_param.h`）、
golden 参考（`<op>_golden.h`）、device 调用适配（`<op>_npu_wrapper.h`）、GTest 驱动
（`<op>_test.cpp`）与 CMake 注册（`CMakeLists.txt`）。**任务包**是
repo-task-blas-case-gen 形制的交付目录，本 skill 只消费其中两件：`gen_csv.py`——顶部含
结构化事实字典 **FACTS**（签名、params、golden、verify、cases 等接口事实）——与
`<op>_test.csv`（契约用例集）。本 skill 把 FACTS 确定性编译成 harness，产物是
**overlay**：一棵以开发者工程根为基准的相对路径文件树（`test/<家族>/<op>/…`），
安装即拷入工程。

本 skill 只生成与安装文件，不构建、不跑测、不出验收结论。构建与验收由
repo-task-blas-accept 按其自身流程执行，对本 skill 零感知；本 skill 也不要求
repo-task-blas-case-gen 安装在场，运行期不读它的任何文件（自带测试以其示例包为
只读数据 fixture，属开发态）。

## 入口参数

| 参数 | 含义 | 取值约束 | 确定方式 |
| --- | --- | --- | --- |
| `任务包目录` | 含 `gen_csv.py` 与恰好一个 `*_test.csv` 的目录 | 绝对路径 | 用户给出 |
| `工作目录` | staging 树、IR（定义见 H2 节）与 manifest 的落点 | 绝对路径 | 用户给出，H0 创建 |
| `工程目录` | 开发者 ops-blas 工程检出，H4 起才需要 | 含 `build.sh`、`include/`、`test/` | 用户给出 |
| `soc` | 目标 SoC，H4 起才需要 | 在工程兼容配置（见 H4 节）的映射表内，实值随 Step 3 交付 | 用户给出 |
| `<python>` | 执行脚本的解释器 | Python 3.8+ | 优先用 `python3` |

`<op>` 是算子短名，取 `*_test.csv` 文件名去掉 `_test.csv`；`<家族>` 取 FACTS 的
`family` 字段。`<skill>` 是本 SKILL.md 所在目录的绝对路径。每条命令都必须带
`cd <工作目录> &&`：shell 调用之间不继承当前目录。

## 前置检查

生成阶段（H1–H3）只需要 Python，不需要 NPU、CANN、ATK 或目标工程；安装阶段
（H4–H5）需要目标工程检出与 soc。H0 先运行：

```bash
mkdir -p <工作目录> && cd <工作目录> && <python> --version
```

仅当退出码为 0、且版本输出以 `Python 3.` 开头时，才进入 H1。否则停止，报告
`阻塞·未生成 @H0`，附解释器路径与完整输出。

**当前交付状态**：H0–H5 与 rollback 全部可执行（sasum 建设样本真机走通 A1–A5，
2026-09-10）。支持的算子组合以建设样本实际覆盖为准（当前 L1 scalar-verify；矩阵类
随 Step 4 sgemm 进入）。

## MVP 支持矩阵

超出「支持」列的输入一律按停机码表停机，不做近似生成或静默降级：

| 轴 | 支持 | 拒绝（停机码） |
| --- | --- | --- |
| FACTS schema | v1 | 其它版本 → `UNSUPPORTED_PACKAGE_VERSION` |
| 包生成器 ABI | 允许表内的版本组合 | 未知组合 → `UNSUPPORTED_PACKAGE_VERSION` |
| golden.kind | `cblas` 且实参可由角色机械映射 | 其余 kind → `UNSUPPORTED_CONTRACT` |
| 参数角色组合 | 封闭角色表（Step 2 冻结，见 contract-ir.md）内、样本覆盖的组合 | 其余 → `UNSUPPORTED_CONTRACT` |
| 复数标量 / nullable | 不支持 | → `UNSUPPORTED_CONTRACT` |
| 输入形态 | A：任务包 + 目标工程 | B：无 FACTS 的工程 → 本 skill 不适用 |
| 目标工程 | 工程兼容配置绑定的 revision | 其它 revision → `TARGET_INCOMPATIBLE` |

**包生成器 ABI** 指任务包内 `gen_csv.py` 暴露给本 skill 消费的接口面：FACTS 字面量、
`_column_specs(facts)`（每列 name/kind/source/index）与 `_header_columns(facts)`
（表头投影）。允许表随本 skill 分发，见 [package-abi.md](references/package-abi.md)。
**建设样本**指按落地顺序在真机走通全链的样本算子（当前 sasum；sgemm 随 Step 4 进入）；
角色组合的支持范围以它们实际覆盖为准，不据此宣告全 BLAS 通用。

两条拒绝的理由：FACTS schema v2 开放 `golden.kind=harness`，golden 与校验本就由开发者
harness 自带，对这种包反向生成 harness 是悖论；复数与 nullable 的上游规范存在分叉
（三种复数列名、两种 null 表达），等 cherk 类样本进入时再显式选择，本期不预选默认。

## 主流程

| 阶段 | 输入与处理 | 产物 | 出口与去向 |
| --- | --- | --- | --- |
| H1 装包 | 读包内两件，过三道门 | `package.json` 快照 | 0 进 H2；否则停止 |
| H2 契约编译 | H1 快照（含 FACTS 与列投影）→ IR | `ir.json`（内部件） | 0 进 H3；否则停止 |
| H3 渲染 | IR → staging 树，双渲对比 | `staging/test/…` | 0 进 H4；否则停止 |
| H4 preflight | overlay + 工程 + soc，逐项硬门 | `preflight.json` | 0 进 H5；否则停止 |
| H5 安装 | dry-run 后拷入工程，写 manifest | 工程内 overlay、manifest | 0 交接验收 |

### H1 装包（三道门）

```bash
cd <工作目录> && <python> <skill>/scripts/harness.py load --package <任务包目录>
```

装载纪律固定：单次读取 `gen_csv.py` 字节；AST 校验并以 `ast.literal_eval` 提取 FACTS
（非字面量即停）；ABI 版本门通过后才对同一份字节 exec——不执行不被认识的代码。
装载共三道门，全 fail-closed：

1. **ABI 版本门**——（FACTS schema 版本，生成器版本，通用代码区 SHA-256）组合必须在
   允许表内，接口函数缺失或版本未知报 `UNSUPPORTED_PACKAGE_VERSION`。
2. **装载纪律门**——语法、AST 校验或 FACTS 字面量提取失败报 `PACKAGE_MALFORMED`。
3. **同源断言**——`_column_specs(FACTS)` 的列名序列、`_header_columns(FACTS)`、CSV
   实际表头三者必须逐列相等（含列序），重复列名拒绝；不等报 `HEADER_MISMATCH`。

列语义不在本 skill 重建：生成器直接消费包内 `_column_specs(facts)`，它与产出该份
CSV 的是同一份代码。装载末尾另做支持矩阵的机械可判预筛（golden.kind、复数标量、
nullable），命中报 `UNSUPPORTED_CONTRACT`；参数角色组合的完整判定在 H2。

### H2 契约编译

```bash
cd <工作目录> && <python> <skill>/scripts/harness.py compile
```

把 H1 快照（`package.json`，含 FACTS 与列投影——H2 只消费快照，不重读任务包）编译成
**Contract IR**（下称 IR）：机器可读的可执行契约，生成的 C++ 按它读列、按它调 golden、
按它做 verify 与状态码绑定。IR 是内部产物，不进交付件。
编译前完成支持矩阵的完整判定（角色组合封闭表；机械可判子集已在 H1 预筛），超界报
`UNSUPPORTED_CONTRACT`，报文列出超界的轴与值，不做近似降级。字段与正负实例见
[contract-ir.md](references/contract-ir.md)。

### H3 渲染 overlay

```bash
cd <工作目录> && <python> <skill>/scripts/harness.py render
```

按 IR 渲出 `staging/test/<家族>/<op>/`，布局见
[overlay-layout.md](references/overlay-layout.md)（占位，Step 3 交付）。渲染执行两次并逐字节比较（树级
哈希），不一致报 `RENDER_MISMATCH`——确定性是硬契约：同一输入两次生成必须逐字节相同。
staging 树带 provenance（生成器版本、IR 版本、兼容配置版本、输入内容哈希）。

### H4 安装 preflight

```bash
cd <工作目录> && <python> <skill>/scripts/harness.py preflight \
  --repo <工程目录> --soc <soc>
```

**工程兼容配置**指随本 skill 分发、绑定目标工程具体 revision 的 frame ABI 与 CMake
helper 描述（带版本号与文件指纹）；生成只对它负责，实况核验就在本步。检查项全部
fail-closed，逐项定义见 [install-preflight.md](references/install-preflight.md)
（占位，Step 3 交付）：

- 工程 revision 与 frame 文件 SHA-256 指纹逐一相符，不符报 `TARGET_INCOMPATIBLE`。
  语义漂移不必然编译失败，判定可能无声变味，所以这是硬门，不赌编译错兜底。
- 入口签名与工程公开头（`include/cann_ops_blas.h`）交叉校验；FACTS 里的签名只够渲染，
  不能替代对实况的核对。
- `test/` 下 `<op>` 目录同名歧义报 `TARGET_AMBIGUOUS`：工程按名搜索取第一匹配，
  歧义必须拒绝而非静默选择。
- 当前 soc 下算子实现在位、CMake 注册 helper 在位，缺失报 `TARGET_INCOMPATIBLE`。

### H5 安装

```bash
cd <工作目录> && <python> <skill>/scripts/harness.py install --repo <工程目录> --dry-run
cd <工作目录> && <python> <skill>/scripts/harness.py install --repo <工程目录>
```

先 dry-run 打印将写入的文件清单，向用户确认后实装。目标文件已存在且与将写入内容不
逐字节相同即拒绝，报 `TARGET_EXISTS`，不覆盖不合并。安装清单写进 `manifest.json`
（每文件 src/dst/SHA-256）；`harness.py rollback --repo <工程目录>` 按 manifest
整体回滚，盘上文件与记录的 SHA 不符时拒绝回滚，报 `ROLLBACK_REJECTED` 交人工处置。

### 交接验收

安装成功后回给用户四样：工程内落位路径、`manifest.json` 绝对路径、`<op>` 与 soc、
「后续验收用 repo-task-blas-accept」一句话。本 skill 任何阶段的成功都只表示
「harness 已生成/已安装」，不得表述为算子验收通过。

## 能力边界

以下边界来自 sasum 真机 spike 实证（2026-09-10），是契约不是缺省：

- golden 参考的累加精度由生成器显式选择（当前 double），不复用工程 frame（目标工程
  `test/frame/` 公共测试库：fixture、CSV 解析、比对）的 float 顺序累加——后者在
  n≥2^24 时整批丢弃落入半 ULP 的加数，golden 本身是错的。
- 精度判据（ULP 钳位阈值）与用例规模阶梯来自任务包，本 skill 原样落位，不修正、
  不放宽、不补偿；判据在大 n 下进入噪声区属任务包侧问题，如实报告给用户。
- overlay 布局冻结为实证形态：根级放共享 `<op>_param.h`、`<op>_golden.h`，arch 目录
  放 `<op>_test.cpp`、`<op>_npu_wrapper.h` 与 CSV 逐字节副本；CSV 与测试源同名同目录
  （工程编译期按测试源路径定位 CSV）；CMake 注册用一行式 helper 调用。
- 读列契约是 require-throw：`<op>_param.h` 对缺列或空值在用例注册期抛错，不给缺省值。
  这把**空跑**——列没真正喂进算子而测试「通过」的假通过——挡在注册期。
- wrapper 对被测接口每用例恰调用一次，不做 warm-up 额外调用，避免验收侧调用计数歧义。
- 不生成、不替换工程根 `build.sh`；不自带 gtest main（工程 CMake helper 自动挂
  frame 的 test_main）。
- overlay 只声明对工程兼容配置绑定的那个 revision 兼容，不声称任意 revision 通用。

## 停机码表

停机即终态，不重试、不降级；`UNSUPPORTED_` 开头的两个码是能力边界而非缺陷，如实报告：

| 停机码 | 阶段 | 触发 | 报告措辞 |
| --- | --- | --- | --- |
| `UNSUPPORTED_PACKAGE_VERSION` | H1 | ABI 版本组合未知或接口函数缺失 | `不支持·未生成 @H1` |
| `PACKAGE_MALFORMED` | H1 | 装载纪律失败：语法/AST/FACTS 非字面量 | `阻塞·未生成 @H1` |
| `HEADER_MISMATCH` | H1 | 同源断言失败：三方表头不等或重复列名 | `阻塞·未生成 @H1` |
| `UNSUPPORTED_CONTRACT` | H1 预筛 / H2 | FACTS 语义超出支持矩阵 | `不支持·未生成 @触发阶段` |
| `RENDER_MISMATCH` | H3 | 双渲逐字节对比不一致 | `阻塞·未生成 @H3` |
| `TARGET_INCOMPATIBLE` | H4 | revision/指纹/签名/实现/helper 任一不符 | `阻塞·未安装 @H4` |
| `TARGET_AMBIGUOUS` | H4 | `<op>` 目录同名歧义 | `阻塞·未安装 @H4` |
| `TARGET_EXISTS` | H5 | 目标文件已存在且不逐字节相同 | `阻塞·未安装 @H5` |
| `ROLLBACK_REJECTED` | H5 | manifest SHA 与盘上文件不符 | `阻塞·需人工 @H5` |

退出码约定：0 成功；2 停机（stderr 末行固定 `STOP <停机码>`）；3 输入路径或环境错误
（不带停机码，修正后重跑同一阶段）。其它退出码视同停止，报告 `阻塞 @<阶段>`。

## 停止条件

出现停机码或退出码 3 即停止，报告固定五项：`阶段`、`停机码`（无则写退出码）、`原因`、
`已有证据`、`解除所需材料`。特别地：

- H4、H5 的任何拒绝都不修改工程的任何文件即退出。
- `ROLLBACK_REJECTED` 后不再自动重试，列出 manifest 记录与盘上实际的逐文件 SHA 差异。
- 任务包缺 `gen_csv.py`，或 `*_test.csv` 不恰好一个：H1 前即停，报 `阻塞·未生成 @H1`。

## 参考资料

- [package-abi.md](references/package-abi.md) — 包生成器 ABI 允许表、装载纪律与同源断言机械规则
- [contract-ir.md](references/contract-ir.md) — Contract IR schema、gate 对照与正负实例
- [template-contract.md](references/template-contract.md) — 发射器模板契约与禁止推断清单
- [overlay-layout.md](references/overlay-layout.md) — overlay 目录形态与命名（占位，Step 3 交付）
- [golden-policy.md](references/golden-policy.md) — golden 映射与累加精度策略（占位，Step 3 交付）
- [compat-config.md](references/compat-config.md) — 工程兼容配置（占位，Step 3 交付）
- [install-preflight.md](references/install-preflight.md) — 安装检查与回滚（占位，Step 3 交付）
- ABI 允许表现居 `scripts/package_loader.py` 的 `KNOWN_PACKAGE_ABIS`；渲染模板
  `assets/template/` 与机读兼容配置随 Step 3 交付，当前缺席。
