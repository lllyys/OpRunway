# 报告规格（P4）

P4 渲染报告时读这份。

## 渲染命令

```bash
python3 <skill>/scripts/render_report.py --repo <repo> [--format both|html|md]   # 默认 both，同目录出 .md + .html
python3 <skill>/scripts/render_report.py --repo <repo> --with-minor              # 保留瑕疵（默认舍弃）

# P5（可选但推荐）：仓库同名 Excel + 统计柱状图（与 REPORT 同口径：自校验闸/去重/默认丢 minor）
python3 <skill>/scripts/export_excel.py --repo <repo>                            # <repo>/<repo>.xlsx + statistics.png
python3 <skill>/scripts/export_excel.py --all                                    # 导出 doccheck 下全部已评仓
```

**报告默认出 HTML**：`render_html` 吐自包含引擎 `templates/report-engine.html`（CSS+JS 全内联，**勿改**）+
注入从 findings 映射的 `DATA` 数组——「报告怎么读」说明、结论条、分类概览（3 大类 × 7 错误类型）、阻断直达、
筛选、分组卡片、修改清单全由引擎 JS 现算。

**HTML 生成是纯脚本、完全确定**：同一份 `findings.json` 两次生成除时间戳外字节一致，模型不参与任何 HTML 编写。
卡片按 P2.1 四件套（问题/后果/图示/修复）+ 严重度·确定度呈现。保留 `REPORT.md` 备 diff/PR 引用。

## 必备模块

0. **覆盖标注（TE-1）**：`doc_meta.axes_evaluated` 记本轮真评过的轴；**未评过的轴在总评标「本轮未评」
   而非「合格」**，不把覆盖缺口伪装成通过。某轴 finder 失败/未产出务必如实（别让空轴假装合格）。
1. **总评**：五轴各给**定性档**（合格 / 有缺陷 / 不合格 / 本轮未评）+ 缺陷计数（**仅可量化条计数**）+
   **开发者影响分布**（🔴 阻断 / 🟠 误导 / ⚪ 瑕疵，阻断优先修、瑕疵可缓）；一句话结论；教程类型/受众。
2. **事实问题（可量化）**——先列：每条 `原文 / 轴 / 形态·来源 / 证据等级 / 代码位置 / 三态 / 改进建议`。
3. **教学判断（不可量化）**——后列，带「教学判断」标签：每条 `原文 / 判例（读者会卡在…） / 改进建议`；
   概念讲错附外部反证或标「疑似」。
4. 每条统一「干净交接」格式：`原文 + 类别（可量化/不可量化） + 证据或判例 + 三态结论 + 开放问题（没能确证的）
   + 下一责任人（文档作者）`。

## 两道自动闸门

**自校验闸**（render 强制）：可量化条必带 代码位置/匹配串；不可量化条必带 判例 + steelman 痕迹。缺则
该条**不准进报告**（标记待补），防止玄学批评和无证据指控混进去。

**跨轴去重（TE-2）**：多个 finder 在同一教程行命中同一处（如「信得过」+「读得懂」各报一遍）时，render 按
`(类别, 教程行号)` 折叠，保留证据最强一条、其余记 `also_hit`，五轴计数不虚高。`findings.json` 保留全部
（非破坏式），只在报告层去重。

## 产物

```text
cann-ops-report/doccheck/<repo>/tutorial/
├── doc_meta.json   ← 教程路径 + 类型（教学型/流程型/API用法型） + 受众 + 代码根 + axes_evaluated（本轮评过的轴）
├── findings.json   ← 机读：每条 = 类别/轴/形态·来源/证据等级或判例/三态/原文/改进建议/下一责任人（全部，不去重）
├── REPORT.md       ← 体检报告 Markdown（定性档 + 事实问题段 + 教学判断段）
├── REPORT.html     ← 体检报告 HTML（默认产出；卡片式/三态色标/折叠）
├── <repo>.xlsx     ← P5 导出（openpyxl 在时）：Sheet「问题明细」第一行 7 列 + Sheet「统计」含 Excel 原生柱状图
├── statistics.png  ← P5 统计柱状图（matplotlib + 系统 CJK 字体在时；否则 statistics.svg，纯 stdlib）
└──（缺 openpyxl 时）<repo>.csv + <repo>-stats.csv —— 与 .xlsx 同口径的降级表格
```

**依赖**：P5 的 `.xlsx` 与 `.png` 需要 `openpyxl` / `matplotlib`（**可选**，见 `requirements.txt`）。
两者缺省时自动降级 `<repo>.csv` + `<repo>-stats.csv` + `statistics.svg`（纯 stdlib），不中断流程。
