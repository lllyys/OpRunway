# repo-task-doc-write 要素四分类

状态：分析产物（2026-09-16），未实施。已过一轮 Codex 评审
（gpt-6-astra · high · thread `01a0a938-d2ba-7502-a035-3b5b2bf16bea`，裁 NEEDS REVISION），
本版已吸收其修正。基于骨架 `plugin/skill/repo-task-doc-write/references/taskdoc-elements.json`
的 43 条要素与任务书模板逐条核对得出。

**分类只描述「内容从哪来」，不等于拍板权限。** 拍板责任由骨架现有字段表达：
`decision_owner` 是三值（human 41 条 / agent 1 条 / template 1 条），
`agent_may_propose` 标 agent 可否给候选。本分类是分析标签，改任何一条的
拍板权限都是对红线 A 执行契约的变更，须单独立项评审，不随分类自动生效。

用途：作为交互模型改造（intake 一次输入 + 合并拍板）的地基——
一类 = intake 必填面，二类 = 候选总审面，三类 = 免拍板候选（现役 0 条，见下），
四类 = 不碰。

## 一、provided · 人必须提供（13 条 + 1 项前提）

推不出来的外部事实与决定：

| 子类     | 条目                                                                                              |
| ------ | ----------------------------------------------------------------------------------------------- |
| 任务语境   | `1.background` 背景、`1.language` 开发语言与工程、`1.repo` 代码仓、`5.pr_target` PR 合入目录                       |
| 验收环境事实 | `3.1.hardware` 硬件具体型号、`3.1.cann_version`、`3.1.third_party` 三方软件版本                               |
| 要求标尺   | `3.3.baseline_env` 性能标杆环境、`3.3.criterion` 性能判据、`3.3.case_table` 性能 case 与标杆数据、`3.4.memory` 内存要求 |
| 方向性决定  | `2.2.project_mode` 工程模式、`2.1.baseline` 对标基线（agent 能给候选，但这是决定）                                   |
| 前提判定   | 是否随机算子（信号词预判 + 人确认，不是骨架要素）                                                                      |

「事实」（版本、型号、标杆数据——可核对）与「决定」（0.8 倍、内存上限——只有拍板）
形态不同：intake 模板里前者贴材料、后者给选项。

## 二、inferred · 模型推断（28 条候选来源项，其中 27 条需人拍板）

从算子名 + 基线 + 公开文档推出候选，本机不可实证。27 条 `decision_owner=human`
归红线 A 辖区；`6.references` 是唯一 `agent` 自决的推断项，不需拍板：

- **§2.1**：`operator_name`、`formula` 公式、`algorithm` 算法
- **§2.3**：`signature` 接口定义
- **§2.4 九列**：`param_name`、`direction`、`description`、`data_type`、`dtype`、
  `format`、`shape`、`value_range`、`error_behavior`
- **§2.5 六项**：`non_contiguous`、`broadcast`、`dynamic_shape`、`inplace_view`、
  `deterministic`、`empty_tensor`
- **§3.2**：`reference_api`、`verdict_formula`、`random_strategy`（条件：随机信号命中）
- **§3.5**：`tooling`、`param_mapping`（条件：签名与基线不一致）、`generation_rules`
- **§4/§7**：`deliverables`、`notes`
- **§6**：`references`（唯一 agent 自决的推断项，不需拍板）

## 三、mechanical · 机械推导，免拍板（现役 0 条，候选 1 条）

**Codex 评审推翻了初版「阈值表已被 3 条 check 钉死」的断言，实读 `_checks.py` 属实**：
`dtypes_covered_by_threshold_table` 只在 §3.2 全文搜 dtype 字符串；`per_dtype_threshold`
只要求存在至少两列的表格；`thresholds_match_standard` 只核对特定布局里 `2^-N` 格式的
rtol/atol 单元格——缺行、十进制数值、未知 dtype 都会被跳过，`required_matched_ratio`
与 `max_abs_error_limit` 完全不查。反例：rtol/atol 写十进制 `999`，三条 check 全绿。

所以 `3.2.threshold_table` 只是**候选**：内容确实由已拍板上游 + 生态标准唯一确定
（推导性成立），但校验不完整（免拍板的前提不成立）。转正条件是把内容约束补完整并
fail-closed（缺失、未知、解析不了一律判红而不是跳过），在此之前保留人工拍板。

**准入判据（防膨胀闸门）**，两条同时满足才准进：①已拍板输入足以**唯一确定**结果
（不是「大体能推」）；②`check_registry` 里的机械 check **完整覆盖**结果且 fail-closed。
`param_mapping` 连①都不满足——签名能暴露顺序差异，但改名参数的语义对应、默认值
补齐、拆分合并都是选择，不是推导——不入此类，保留人工确认。

## 四、fixed · 模板固定件（1 条）

`8.environment` 环境获取——模板明写「无需修改，使用模板原始内容」，
骨架里 `decision_owner=template`（不是 agent）。

## 两根正交的轴

不进四类，盘点时要带着：

- **条件适用**：`random_strategy`、`param_mapping` 只在特定条件下需要。
  条件求值有两侧：门禁侧按文档属性求（`check_taskdoc.py` 已实现，消除了「读自身答复
  记录」的自指涉循环）——但**不是完整兜底**：`_signature` 只比较两侧共有参数名的相对
  顺序，基线段无可解析代码块、参数整体改名等情形静默返回 False，T5 同样不报；
  调度侧（`next_questions.py`）现只实现 `random_signal_hit`，
  `signature_differs_from_baseline` 静默失效——两侧叠加，参数映射可能在追问与门禁
  两处同时漏掉。
- **推导拓扑序**：§2.4 依赖基线 + 签名，阈值表依赖 §2.4 dtype 面，
  生成规则依赖值域——候选生成必须先定基线再展开。

## 同批实证的调度缺陷

- **D1 批次覆盖缺口**：`question_batches` 只收 24/43 条，17 条人拍板项在批次外
  （含条件项 `param_mapping`，受 D2 影响实际最多列出 16 条），`next_questions.py`
  只裸打 key 列表，无名称、无「不写会怎样」。
- **D2 条件求值缺口**：见上「条件适用」轴。
- **D3 收尾崩溃（Codex 发现，已动态复现）**：批内 24 项全部答完、批外仍有欠答时，
  `next_batch` 为 None，默认文本模式在 `next_questions.py:86` 抛
  `TypeError: 'NoneType' object is not subscriptable`——批外项没有可执行的收尾路径。
  `--json` 模式不崩，返回 `done=false, next_batch=null`。

## 落地顺序（Codex 评审裁定，采纳）

界面改造、调度正确性、免拍板权限三件事不捆绑，按序独立落地，各自可回滚：

1. 修正本分析的现状断言（本版已完成）。
2. 独立修 D1/D2/D3，并明确条件检测的覆盖边界（已覆盖 / 未覆盖 / 无法判断三态，
   无法判断不得当成不适用）。
3. 保留现有 `decision_owner` 与答复记录要求，只做 intake 与合并总审的交互优化；
   须明确：前提（基线、工程模式）确认先于依赖候选总审，上游答复变更后受影响的
   下游候选一律重新生成、重新确认，不复用旧确认。
4. 阈值表机械 check 补完整并 fail-closed 后，单独立项评审免拍板转正。

