# 最终报告规格（PHASE{N}_FINAL_REPORT.md）

> **何时读本文件**：示例跑测全部完成 **且** 用户明确要求生成报告。不在跑测中途生成。

**产出路径**：`CWD/cann-ops-report/<repo>/test/PHASE{N}_FINAL_REPORT.md`

## 数据来源（按优先级）

1. `CWD/cann-ops-report/<repo>/test/run_state.json` — 算子 × phase × status × duration_s（**权威**）
2. `CWD/cann-ops-report/<repo>/test/logs/<op>.phase{N}.{step}.log` — 真实错误信息摘录
3. `CWD/cann-ops-report/<repo>/scan/_intermediate.json` — 950 特性命中（**用户没扫描就跳过此数据源**）
4. `CWD/cann-ops-report/<repo>/test/phase{N}_*_report.json` — 过程性 JSON

## 必备 6 大模块（顺序固定）

| # | 模块 | 关键内容 |
|---|------|---------|
| I | 执行摘要 | 一句话结论 + 关键数字表 + 三大发现 + 950 特性覆盖矩阵（若有 scan）+ P1/P2/P3 计划 |
| II | 按仓成绩单 | 每仓 × 6 列汇总表（PASS / 各类失败 / 通过率）+ 状态注脚 |
| III | 失败算子分类诊断 | BUILD_FAIL / RUN_EXIT_FAIL / RUN_PATTERN_FAIL 三张表，每行含：仓 / 算子 / 耗时 / 950 特性（若有 scan）/ **真实错误日志摘录** / 根因诊断 / **复现命令** / **诊断步骤 ①②③** |
| IV | PASS 算子汇总 | 按仓列举（含耗时 / 特性 / 特点）+ 多规则命中基准表（若有 scan） |
| V | 950 特性覆盖矩阵 | 按规则统计（覆盖率 / 信号强度）+ 仓级热力图 + 关键发现（**仅当用户跑过 scan 时输出，否则跳过本节**） |
| VI | 构建优化建议 | 超长编译（≥900s）处理 + BUILD_FAIL 代码瘦身 + 并发策略改进 |
| 附录 | 完整算子清单 | 每仓所有算子 × 状态 × 耗时 × 特性（若有）× 日志路径 |

## 关键约束（必须满足）

1. **失败算子三要素**：① 真实错误（grep 日志，不可凭空推断）② 完整复现命令（`cd <abs-path> && bash build.sh ...`）③ 带序号诊断步骤 ①②③
2. **超长编译标记**：耗时 ≥900s 标 `⚠️ TIMEOUT`
3. **耗时落表**：PASS 算子也带耗时
4. **950 特性关联**：scan 产物存在则标注 hif8 / simt / regbase；**不存在则该列写「—」，不要捏造**
5. **不凭空推断**：日志没有的标 `(推断)`，与真实摘录严格区分
6. **复现命令完整**：不留省略号

## 生成流程

1. 读 `run_state.json` → 按 repo × status 分组
2. 抽样读 5–10 个失败日志，grep `ERROR\|undefined\|failed\|exit=` 提取真实报错
3. scan 产物存在 → 读 `_intermediate.json` 关联 950 特性；不存在则该维度空缺
4. `AskUserQuestion` 确认报告用途 + 格式 + 核心模块
5. **先输出大纲预案，用户审视后再落盘**
6. Write `CWD/cann-ops-report/<repo>/test/PHASE{N}_FINAL_REPORT.md`
