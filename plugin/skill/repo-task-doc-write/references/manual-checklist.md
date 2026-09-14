# 任务书人工检查清单

本文件由 `scripts/render_views.py` 从 `references/taskdoc-elements.json` 渲染，不要手改。
改内容请改骨架再重新渲染。

源清单是 `任务书人工checklist.xlsx` Sheet1 的十四条，归档在 `docs/development/taskdoc-source/origin/checklist-14.md`。

## 已被机械判据覆盖

这几条不需要人读，`check_taskdoc.py` 会裁决。

| 序号 | 检查项 | 由哪些判据覆盖 |
| --- | --- | --- |
| 1 | 检查章节完整性 | human_reply_recorded、no_inline_link、no_vague_word、nonempty、section_present |
| 3 | 任务概述 | human_reply_recorded、no_inline_link、no_placeholder、no_vague_word、nonempty |
| 5 | 算子工程模式 | enum_value、human_reply_recorded、no_vague_word、nonempty |
| 6 | 接口定义 | human_reply_recorded、nonempty、signature_matches_param_table |
| 7 | 参数说明 | dtypes_covered_by_threshold_table、enum_value、error_column_not_uniform、human_reply_recorded、no_unbounded_range、no_vague_word、nonempty、range_matches_algorithm、range_matches_distribution、signature_matches_param_table、table_header_matches |
| 8 | 软硬件环境要求 | env_lists_third_party_versions、human_reply_recorded、models_covered_by_perf_table、no_vague_word、nonempty |
| 10 | 性能要求 | env_lists_third_party_versions、human_reply_recorded、models_covered_by_perf_table、no_vague_word、nonempty、perf_criterion_has_comparator |
| 11 | 内存要求 | human_reply_recorded、no_vague_word、nonempty |
| 14 | PR申请合入 | human_reply_recorded、no_inline_link、no_vague_word、nonempty、pr_target_is_a_path |

## 仍需人读

这几条判的是「对不对」而不是「有没有」，脚本判不了。交付前逐条核对。

| 序号 | 检查项 | 人要判断什么 |
| --- | --- | --- |
| 2 | 任务书标题 | 标题与任务发放纪要的任务名是否一致 |
| 4 | 功能要求 | 算法逻辑或计算公式是否正确 |
| 9 | 精度要求 | 精度阈值是否覆盖了所有输入场景 |
| 12 | 自验要求 | 自验策略是否真的可行 |
| 13 | 验收交付件 | 交付件要求是否恰当 |
