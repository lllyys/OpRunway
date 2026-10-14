# gels 参考口径改动回退（2026-10-13）

用户指出双精度残差计算本身正确。此前将计算精度与参考矩阵来源混淆，未经确认
就改变参考输入并强制重冻，依据不足。本轮仅回退这些改动，保留独立成立的修复。

- fill_ratio_cpu.py、build_package.py恢复18e3566字节内容。
- render_verify的历史CPU辅助链恢复A64/B64；删除新增逐条ratio_input_basis要求。
- accept_run删除缺该标记即拒绝的分支，保留QR元数据透传及冻结最优性参考有效性检查。
- 保留输入重建不现场计算阈值参考、超定读取冻结ratio_opt_cpu的修复。
- 同步两包三个函数文本、README及manifest；原index/canonical/GPU基线未变。
- 撤回强制服务器重冻待办；既有参考矩阵口径一致性仍待确认，不宣称已证实正确。

产物位于reports/solver-luqr-1013，管理INDEX及gels-frozen-fix说明已更正。
独立Codex静态audit核对源码无回退遗漏，后续同轮verify核对产物；未运行测试。
本地提交保存修正，不push。旧结论保留历史但已显式标为撤回。
