# 上游样例与模板的缺陷对照表

对照对象是 `origin/` 下的两份只读归档。发现缺陷不就地修，修复产物在
`skill/repo-task-doc-write/references/` 下另存。

每条判据都必须能指回这张表里的一条缺陷。指不回去的判据是凭空设计的，
要么删掉，要么在这里补上它防的是什么。

## 样例缺陷

| 编号 | 位置 | 原文 | 为什么是缺陷 | 判据 |
| --- | --- | --- | --- | --- |
| D01 | 样例 §2.3 / §2.4 | §2.3 有 `windowSizeLen`，§2.4 表格无此行 | 漏参，开发者不知道要实现它，验收侧构造不出调用 | `signature_matches_param_table` |
| D02 | 样例 §2.3 / §2.4 | §2.3 `windowSize`，§2.4 `window_size` | 违反 checklist 第 7 条「要和接口定义章节中的参数一致」 | `signature_matches_param_table` |
| D03 | 样例 §2.4 q/k/v | 值域 `(-∞,∞)` | fp16 表示不了，且生成不出数据；与 §3.5 的 `(0,1)正态分布` 矛盾 | `no_unbounded_range` + `range_matches_distribution` |
| D04 | 样例 §2.4 / §2.1 | §2.4 `text_length ∈ (0,∞)`，§2.1 说 `has_text=false 时按 0 处理` | 0 不在声明值域内，边界未定义 | `range_matches_algorithm` |
| D05 | 样例 §2.4 window_size | 描述说奇数窗口，值域写 `(0,∞)`，§3.5 写 `[1,100] 平均分布` | 奇数约束三处都没落进可执行表达 | `range_matches_distribution` |
| D06 | 样例 §3.3 | 「与 0.8 倍【FastVideo + GPU(A100)】持平」 | 「持平」无判据，是 `≥` 还是 `±容差` 没定义 | `perf_criterion_has_comparator` |
| D07 | 样例 §3.1 / §3.3 | §3.1 写「A2系列产品」，§3.3 只给 910B3 | 模板 §3.3 与 checklist 第 10 条都要求 A2 明确 910B3、910B4 | `models_covered_by_perf_table` |
| D08 | 样例 §2.4 异常行为列 | 8 行全是「触发参数校验报错」 | 给不出任何一个具体非法输入，验收侧构造不出错误用例 | `error_column_not_uniform` |
| D09 | 样例 §3.1 | 只有 CANN 版本 | 违反 checklist 第 9 条「其他依赖三方软件版本」；GPU 标杆无驱动与 CUDA 版本，性能对标不可复现 | `env_lists_third_party_versions` |
| D10 | 样例 §3.5 | `[自测用例和测试指导](测试用例文件夹路径)` | 未替换的占位符，同时是隐藏跳转链接，违反 checklist 顶部「链接必须裸露」 | `no_placeholder` + `no_inline_link` |
| D11 | 样例全文 | 无「非连续 Tensor」声明 | 验收侧 `intake.md` 明写无此列记为未声明，每份任务书都会挂待确认项 | `section_present:2.5` |
| D12 | 样例 §3.5 | 写了「(0,1)正态分布」，命中随机算子信号词，但无对比策略 | 验收侧 `derive_interface.py` 会退出码 2 拦住 | `random_strategy_when_signaled` |
| D13 | 样例 §3.5 | seqShape 的 a、b、c 与 textLength 各自独立均匀采样，与 §2.1/§2.4 钉死的 `S = textLength + T×H×W` 矛盾 | 「内部一致」与「可执行」是两回事：`range_matches_distribution` 只查 §2.4 与 §3.5 两处数字是否互相对得上，不问任何一边照字面执行是否构造得出算子会接受的输入；这一行是从上游样例逐字带过来的，与已修的 D05 同一缺陷类，是评审找到的唯一一处「黄金样例被塑造得刚好通过判据」 | 无判据覆盖，靠改写黄金样例 §3.5 的采样顺序与依赖规避（见 repair-log.md） |

## 模板缺陷

| 编号 | 位置 | 原文 | 为什么是缺陷 | 修复方向 |
| --- | --- | --- | --- | --- |
| T01 | 模板 §3.2 | 「**建议**参考生态算子开源精度标准」 | 把硬标准写成软建议，样例里的模糊写法是它纵容的 | 改成「必须满足」，并要求逐 dtype 抄出阈值表 |
| T02 | 模板 §2.4 表头 | 每列给的是疑问句（「参数的shape是多少？」） | 疑问句不是判据，填的人只能凭感觉 | 每列改成可判定的填写要求 |
| T03 | 模板全文 | 无 §2.5 算子实现约束 | 非连续、broadcast、dynamic shape、原地语义、确定性、空 Tensor 无处声明 | 新增 §2.5，六个子项 |
| T04 | 模板 §3.2 | 无随机算子分支 | 随机类算子的精度对比策略无处写 | §3.2 增条件子项 |
| T05 | 上游 `README.md` | 声称模板是 v2.3，目录里是 v3.0 | 文档维护脱节 | 归档时记下，不修上游 |

## 无对应缺陷编号的判据

以下五条判据在样例与模板对照表里找不到逐条对应的编号，直接列在这里
补上「防的是什么」，让「每条判据都能指回一条缺陷」这条纪律站得住。

| 判据 | 防的是什么 |
| --- | --- |
| `dtypes_covered_by_threshold_table` | §2.4 出现的张量 dtype 在 §3.2 阈值表里漏了一列，精度要求实际上没覆盖到那个 dtype |
| `thresholds_match_standard` | §3.2 只写「满足生态标准」带过，不落地成 checklist 第 9 条要求的可执行阈值 |
| `params_covered_by_generation_table` | §3.5 的用例生成规则漏了 §2.4 里的某个参数，开发者不知道那个参数该怎么生成测试输入 |
| `deliverables_complete` | §4 列的交付件少了 checklist 第 13 条要求的某一项（设计文档/自测用例/自测报告/代码仓说明之一），验收侧收不齐材料 |
| `pr_target_is_a_path` | §5 只写了仓名没写目录，或写成一句话描述，不是 checklist 第 14 条要求的「具体到算子仓的目录」 |

`nonempty`、`enum_value`、`table_header_matches` 不在此列——它们是别的判据
垒在上面的地基（判非空、判取值在枚举域内、判表头列数与文字对不对），不针对
某一类缺陷，本身就不该有编号。
