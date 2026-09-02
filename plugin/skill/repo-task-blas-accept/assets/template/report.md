<!-- 本模板由 accept.py verdict 渲染（_write_layout）。整行 HTML 注释在渲染时剥除， -->
<!-- 只用于向维护者说明各 token 的展开形态；三节结构是用户裁定，不加第四节。 -->
# @@OP@@ 验收报告

**结论：@@VERDICT@@**（run_id=@@RUN_ID@@，soc=@@SOC@@）

<!-- @@SUMMARY@@ 展开为逐行列表： -->
<!--   - 结论原因：…（仅非「通过」时，列精度/性能/契约中失守的维度） -->
<!--   - 契约（check.json）：<状态>，随后逐条「警告：…」「契约错误：…」 -->
<!--   - 运行时身份：op、profile、calls_per_case、性能键 -->
<!--   - 包 CSV / 基线 / 测试二进制 SHA-256（全量）； -->
<!--     部署 CSV SHA-256 仅在与包 CSV 不一致时另列（一致即省略，出现即警示） -->
<!--   - 证据指引：intermediate/（机器可读全量）与 repro/（rerun.sh 等复现材料） -->
@@SUMMARY@@

## 精度

<!-- @@ACCURACY_SECTION@@ 展开为：状态（pass/expected）、执行条数；有首轮失败时附 -->
<!-- 归因表（case_name/首轮/复跑/归因），否则一行「无首轮失败」；证据问题逐条列出。 -->
@@ACCURACY_SECTION@@

## 性能

<!-- @@PERFORMANCE_SECTION@@ 展开为：状态、total_pf（有无性能要求的判据）、 -->
<!-- comparable_pf、timing_scope、NO_REF 计数、threshold、scope caveat 说明、reason； -->
<!-- 可比集非空时附逐用例表（case_name/status/kernel_us/gpu_ms/ratio/spread/verdict， -->
<!-- 超 30 条截断并指向 intermediate/ 的 performance JSON）；证据问题逐条列出。 -->
@@PERFORMANCE_SECTION@@

## 备注说明

<由验收 agent 填写：环境备注、与任务书的偏差、复跑与归因说明>
